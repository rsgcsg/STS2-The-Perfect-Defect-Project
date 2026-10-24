"""Serial Reference episodes through the existing Workbench owners.

This dispatcher has no gameplay operation, controller, bootstrap or retry loop.
The Runtime runs the Agent; Environment owns exact Actor/Host finish.
"""
from __future__ import annotations

import hashlib
import math
import os
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, Protocol

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes
from spireagent.source import source_identity
from spireagent.workbench import agent_run_request, local_model_cli
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json
from spireagent.workbench.developer_server import running
from spireagent.workbench.native_episode_binding import checked_context
from stpd.evaluation.suites import (
    FOUR_AGENT_SUITE_ID,
    checkpoint_reference_manifest,
    diagnostic_reference_manifest,
    four_agent_reference_manifest,
)
from stpd.game_seed import derive_game_seed, require_canonical_game_seed

REPORT_SCHEMA = "spireagent/reference-suite-report-v1"
MAX_JSON = 1024 * 1024
MAX_REPORT_BYTES = 64 * MAX_JSON


def _reject(code: str) -> NoReturn:
    raise BoundaryError("reference_suite", code)


def load_manifest(path: Path, sha256: str) -> tuple[bytes, dict[str, Any]]:
    digest(sha256, "reference_suite.manifest")
    raw = _read(path)
    if hashlib.sha256(raw).hexdigest() != sha256:
        _reject("manifest_hash_mismatch")
    value = decode_json(raw)
    if isinstance(value, dict) and value.get("purpose") == "checkpoint_reference":
        version, character, ascension = (value.get("game_version"), value.get("character"),
                                         value.get("ascension"))
        if (not isinstance(version, str) or not isinstance(character, str)
                or type(ascension) is not int):
            _reject("manifest_configuration_changed")
        expected_checkpoint = checkpoint_reference_manifest(
            version, character, ascension)
        if json_bytes(value) != json_bytes(expected_checkpoint):
            _reject("manifest_configuration_changed")
        return raw, value
    if isinstance(value, dict) and value.get("suite_id") == FOUR_AGENT_SUITE_ID:
        game_version = value.get("game_version")
        if not isinstance(game_version, str):
            _reject("manifest_configuration_changed")
        expected = four_agent_reference_manifest(game_version)
        if json_bytes(value) != json_bytes(expected):
            _reject("manifest_configuration_changed")
        return raw, value
    if isinstance(value, dict) and value.get("purpose") == "diagnostic_reference":
        version, group, count = (value.get("game_version"), value.get("group_id"),
                                 value.get("episode_count"))
        if not isinstance(version, str) or not isinstance(group, str) or type(count) is not int:
            _reject("manifest_configuration_changed")
        expected_diagnostic = diagnostic_reference_manifest(version, group, count)
        if json_bytes(value) != json_bytes(expected_diagnostic):
            _reject("manifest_configuration_changed")
        return raw, value
    expected = {"schema": "stpd/evaluation-suite-v1", "suite_id": "ref-20", "version": "1.0.0",
                "game_version": "v0.111.0", "character": "Defect", "ascension": 0,
                "host": "Reference", "purpose": "paired_reference", "episode_count": 20,
                "seed_derivation": "derive_game_seed(suite_id, ordinal_1_based)"}
    seeds = [derive_game_seed("ref-20", ordinal) for ordinal in range(1, 21)]
    if (not isinstance(value, dict) or set(value) != {*expected, "seeds"}
            or any(json_bytes(value.get(key)) != json_bytes(item) for key, item in expected.items())
            or value["seeds"] != seeds or len(set(seeds)) != 20):
        _reject("manifest_configuration_changed")
    for seed in seeds:
        if require_canonical_game_seed(seed) != seed:
            _reject("manifest_seed_not_canonical")
    return raw, value


def _read(path: Path, *, max_bytes: int = MAX_JSON) -> bytes:
    if any(parent.is_symlink() for parent in (path, *path.parents)) or not path.is_file():
        _reject("unsafe_or_missing_original")
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        _reject("original_over_limit")
    return raw


@dataclass(frozen=True)
class SuiteLimits:
    preparation_seconds: float
    episode_seconds: float
    cleanup_seconds: float
    suite_seconds: float
    poll_seconds: float = 0.25
    http_seconds: float = 25

    def __post_init__(self) -> None:
        if any(type(value) not in {int, float} or not math.isfinite(value) or value <= 0
               for value in asdict(self).values()):
            _reject("finite_positive_limits_required")
        if (self.http_seconds > 180 or self.cleanup_seconds > 180
                or self.suite_seconds > 86400 or self.poll_seconds > self.suite_seconds):
            _reject("limits_out_of_bounds")


class ReferenceClient(Protocol):
    identity: dict[str, str]

    def readiness(self, selection: str, timeout: float) -> dict[str, Any]: ...
    def prepare(self, body: dict[str, Any], timeout: float) -> dict[str, Any]: ...
    def native_status(self, timeout: float) -> dict[str, Any]: ...
    def request(self, selection: str, ref: str, profile: str, timeout: float) -> dict[str, Any]: ...
    def submit(self, request: dict[str, Any], ref: str, timeout: float) -> dict[str, Any]: ...
    def status(self, timeout: float) -> dict[str, Any]: ...
    def finish(self, ref: str, timeout: float) -> dict[str, Any]: ...
    def originals(self, evaluation: dict[str, Any], request: dict[str, Any],
                  startup: dict[str, Any]) -> tuple[bytes, bytes]: ...
    def preserve(self, run_id: str, peer: dict[str, Any], timeout: float) -> dict[str, Any]: ...
    def preservation_status(self, timeout: float) -> dict[str, Any]: ...
    def metrics(self, evaluation: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]: ...


class WorkbenchReferenceClient:
    """One already-running Application, fenced before every HTTP request."""

    def __init__(self, config: ProjectConfig, *, _execution_scope: str | None = None) -> None:
        current = running(config)
        if current is None:
            _reject("open_workbench_first")
        self.config = config
        self._execution_scope = _execution_scope
        self.identity = {"application_instance_id": current["instance_id"],
                         "configuration_id": current["configuration_id"]}

    def _native(self, action: str, timeout: float, **kwargs: Any) -> dict[str, Any]:
        return local_model_cli.native_episode_command(
            self.config, action, expected_instance_id=self.identity["application_instance_id"],
            timeout_seconds=timeout, _execution_scope=self._execution_scope, **kwargs)

    def _model(self, action: str, timeout: float, **kwargs: Any) -> dict[str, Any]:
        return local_model_cli.model_command(
            self.config, action, expected_instance_id=self.identity["application_instance_id"],
            timeout_seconds=timeout, _execution_scope=self._execution_scope, **kwargs)

    def readiness(self, selection: str, timeout: float) -> dict[str, Any]:
        return self._model("readiness", timeout, selection=selection)

    def prepare(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        return self._native("prepare-owned", timeout, **{
            key: body[key] for key in (
                "intent_id", "seed", "display_mode", "speed", "character", "ascension")
            if key in body})

    def native_status(self, timeout: float) -> dict[str, Any]:
        return self._native("native-status", timeout)

    def request(self, selection: str, ref: str, profile: str, timeout: float) -> dict[str, Any]:
        return self._native("agent-request", timeout, selection=selection, environment_ref=ref,
                            operation="prepare-and-takeover", run_profile=profile)

    def submit(self, request: dict[str, Any], ref: str, timeout: float) -> dict[str, Any]:
        return self._native("run-agent", timeout, environment_ref=ref, original_request=request)

    def status(self, timeout: float) -> dict[str, Any]:
        return self._model("status", timeout)

    def finish(self, ref: str, timeout: float) -> dict[str, Any]:
        return self._native("native-finish", timeout, environment_ref=ref)

    def preserve(self, run_id: str, peer: dict[str, Any], timeout: float) -> dict[str, Any]:
        return self._native("evidence-publish", timeout, run_id=run_id, peer=peer)

    def preservation_status(self, timeout: float) -> dict[str, Any]:
        return self._native("evidence-status", timeout)

    def originals(self, evaluation: dict[str, Any], request: dict[str, Any],
                  startup: dict[str, Any]) -> tuple[bytes, bytes]:
        from spireagent.policy_files import _inside

        owner = self.config.state_dir / "models"
        # Reuse the original producer's startup/composition/request verifier.
        verified = agent_run_request.original(owner, request, startup)
        request_raw = _read(_inside(owner, request["path"]))
        if (hashlib.sha256(request_raw).hexdigest() != request["sha256"]
                or decode_json(request_raw) != verified):
            _reject("original_request_changed")
        identity = digest(evaluation.get("evaluation_id"), "reference_suite.evaluation")
        raw = _read(owner / "evaluations" / (identity + ".json"))
        if raw != canonical_json(evaluation).encode():
            _reject("original_evaluation_changed")
        return request_raw, raw

    def metrics(self, evaluation: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        from stpd.evaluation.reference_metrics import read_reference_metrics

        return read_reference_metrics(
            self.config.state_dir / "models" / "agent-runs" / evaluation["run_id"],
            evaluation, context)


class _Artifacts:
    def __init__(self, root: Path) -> None:
        if any(parent.is_symlink() for parent in (root, *root.parents)):
            _reject("unsafe_run_directory")
        root.mkdir(parents=True, exist_ok=False)
        self.root = root
        self.sequence = 0

    def put(self, label: str, raw: bytes) -> dict[str, Any]:
        self.sequence += 1
        name = f"{self.sequence:04d}-{label}"
        with (self.root / name).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "recorded_at": datetime.now(UTC).isoformat()}

    def save(self, report: dict[str, Any]) -> None:
        atomic_json(self.root / "report.json", report)


def _status(value: dict[str, Any]) -> dict[str, Any]:
    # Persist API facts used here, not arbitrary private Application state.
    keys = {"schema", "status", "loaded", "selection_id", "operation", "startup", "runtime",
            "startup_native_episode", "startup_agent_run_request", "agent_run_request",
            "evaluation", "error_code", "observation_error"}
    return {key: item for key, item in value.items() if key in keys}


def _same_resource(value: dict[str, Any], row: dict[str, Any], identity: dict[str, str]) -> None:
    if (value.get("environment_ref") != row["environment_ref"]
            or value.get("generation_id") != row["generation_id"]
            or value.get("intent_id") != row["intent_id"]
            or any(value.get(key) != item for key, item in identity.items())):
        _reject("resource_or_application_changed")


def _error(error: Exception) -> str:
    return error.code if isinstance(error, BoundaryError) else type(error).__name__



def _completed_rows(paths: tuple[Path, ...], manifest_sha: str, *, profile: str,
                    display: str, speed: str, peer: dict[str, Any] | None,
                    selected: dict[str, str], artifacts: _Artifacts,
                    ) -> dict[tuple[str, str], dict[str, Any]]:
    """Reuse finished originals only. An uncertain offer must be reconciled by its owner."""
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        raw = _read(path, max_bytes=MAX_REPORT_BYTES)
        previous = decode_json(raw)
        if (not isinstance(previous, dict) or previous.get("schema") != REPORT_SCHEMA
                or previous.get("manifest", {}).get("sha256") != manifest_sha
                or previous.get("run_profile") != profile or previous.get("speed") != speed
                or previous.get("display_mode") != display
                or previous.get("evidence_peer") != peer):
            _reject("previous_attempt_configuration_changed")
        frozen = artifacts.put("previous-report.json", raw)
        for row in previous.get("episodes", []):
            if row.get("status") == "not_started" and not row.get("world_mutation_offered"):
                continue
            if (row.get("status") != "finished" or not row.get("stored_evidence_id")
                    or row.get("preservation_error") or row.get("cleanup_accepted") is not True):
                _reject("previous_offered_attempt_requires_reconciliation")
            key = (row["seed"], row["selection_id"])
            if row["selection_id"] not in selected.values():
                _reject("previous_selection_not_in_plan")
            if key in result:
                _reject("duplicate_previous_episode")
            finish_ref = row["finish"]
            finish_raw = _read(path.parent / finish_ref["path"])
            if (hashlib.sha256(finish_raw).hexdigest() != finish_ref["sha256"]
                    or len(finish_raw) != finish_ref["bytes"]
                    or decode_json(finish_raw).get("phase") != "closed"):
                _reject("previous_finish_unproved")
            # The durable preservation receipt is checked against the original report.
            ref = row["preservation"]
            receipt_raw = _read(path.parent / ref["path"])
            if (len(receipt_raw) != ref["bytes"]
                    or hashlib.sha256(receipt_raw).hexdigest() != ref["sha256"]):
                _reject("previous_receipt_changed")
            receipt = decode_json(receipt_raw)
            if (receipt.get("status") != "completed" or receipt.get("run_id") != row["run_id"]
                    or receipt.get("artifact_id") != row["stored_evidence_id"]
                    or not receipt.get("peer_readback")):
                _reject("previous_preservation_unproved")
            result[key] = {"previous_report": frozen, "original_report_path": str(path.resolve()),
                           "original_row": row}
    return result


def run_suite(
    manifest_path: Path, manifest_sha256: str, *, client: ReferenceClient, run_dir: Path,
    selection: str, run_profile: str, limits: SuiteLimits, max_episodes: int = 1,
    selections: dict[str, str] | None = None, evidence_peer: dict[str, Any] | None = None,
    display_mode: str = "headless", speed: str = "instant",
    completed_reports: tuple[Path, ...] = (),
    before_episode: Callable[[dict[str, Any]], bool] = lambda _report: True,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep, should_stop: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """New attempt only. Existing run directories are read-only recovery artifacts."""
    if (type(max_episodes) is not int or not 1 <= max_episodes <= 200
            or run_profile not in {"short", "extended", "continuous"}
            or not isinstance(selection, str)
            or not (selection or selections) or display_mode not in {"headless", "native-ui"}
            or speed not in {"normal", "fast", "instant"}):
        _reject("invalid_attempt_configuration")
    raw_manifest, manifest = load_manifest(manifest_path, manifest_sha256)
    if manifest.get("purpose") not in {"diagnostic_reference", "checkpoint_reference"} and speed != "instant":
        _reject("fixed_suite_requires_instant_speed")
    four = manifest["suite_id"] == FOUR_AGENT_SUITE_ID
    checkpoint = manifest.get("purpose") == "checkpoint_reference"
    agents = ("random", "frequency", "linear", "mstd")
    if four:
        if (not isinstance(selections, dict) or set(selections) != set(agents)
                or any(not isinstance(v, str) or not v for v in selections.values())
                or len(set(selections.values())) != 4 or not isinstance(evidence_peer, dict)):
            _reject("four_distinct_selections_and_evidence_peer_required")
        selected = {a: selections[a] for a in agents}
    elif checkpoint and selections is not None:
        if (not selections or len(selections) > 12
                or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                       for k, v in selections.items())
                or len(set(selections.values())) != len(selections)):
            _reject("distinct_checkpoint_selections_required")
        selected = dict(selections)
    else:
        if selections is not None or max_episodes > manifest["episode_count"]:
            _reject("legacy_suite_requires_single_selection")
        selected = {"agent": selection}
    plan = [(index + 1, seed, agent, identity)
            for index, seed in enumerate(manifest["seeds"])
            for agent, identity in selected.items()]
    if max_episodes > len(plan):
        _reject("invalid_attempt_configuration")
    artifacts = _Artifacts(run_dir)
    started = clock()
    report: dict[str, Any] = {"schema": REPORT_SCHEMA, "attempt_id": uuid.uuid4().hex,
        "manifest": artifacts.put("manifest.json", raw_manifest),
        "selection_id": selection, "run_profile": run_profile,
        "application": dict(client.identity), "limits": asdict(limits),
        "source": source_identity(ROOT, require_clean=False).to_dict(),
        "suite_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "client_source_sha256": hashlib.sha256(
            Path(local_model_cli.__file__).read_bytes()).hexdigest(),
        "max_episodes": max_episodes, "workers": 1, "display_mode": display_mode,
        "speed": speed, "planned": len(plan), "status": "running", "stop_reason": None,
        "world_offers": 0, "actor_started": 0,
        "selections": selected, "evidence_peer": evidence_peer,
        "episodes": [{"ordinal": index + 1, "seed_ordinal": ordinal, "seed": seed,
                      "agent": agent, "selection_id": identity, "intent_id": uuid.uuid4().hex,
                      "status": "not_started", "reason": "not_dispatched", "artifacts": [],
                      "world_mutation_offered": False, "actor_started": False}
                     for index, (ordinal, seed, agent, identity) in enumerate(plan)],
        "non_claims": ["not_Human_origin", "not_strategy_quality_qualification",
                       "not_Host_equivalence", "not_new_P1_profile_support"]}
    completed = _completed_rows(completed_reports, manifest_sha256, profile=run_profile,
        display=display_mode, speed=speed, peer=evidence_peer, selected=selected,
        artifacts=artifacts)
    for row in report["episodes"]:
        prior = completed.get((row["seed"], row["selection_id"]))
        if prior is not None:
            row.update(status="reused", reason="previous_finished_original", **prior)
    artifacts.save(report)  # The entire denominator exists before even readiness.

    def timeout(deadline: float, cap: float = limits.http_seconds) -> float:
        remaining = min(deadline, started + limits.suite_seconds) - clock()
        if remaining <= 0:
            _reject("suite_or_phase_wall_limit")
        return min(cap, remaining)

    try:
        compositions = {}
        for agent, identity in selected.items():
            ready = client.readiness(identity, timeout(started + limits.suite_seconds))
            report["readiness_" + agent] = artifacts.put("readiness.json", json_bytes(ready))
            if ready.get("status") != "ready_to_load" or ready.get("selection_id") != identity:
                _reject("selection_not_ready")
            composition = ready["agent_composition"]
            if four and (
                composition.get("semantic_definition", {}).get("agent", {}).get("id")
                != "p1-" + agent
                or composition.get("semantic_definition", {}).get("input", {}).get("profile")
                != "p1-global-r1"
                or composition.get("implementation", {}).get("adapter", {}).get("id")
                != ("stpd-p1-mstd-agent" if agent == "mstd" else "stpd-p1-baseline-agent")
            ):
                _reject("four_agent_selection_identity_mismatch")
            compositions[identity] = composition
        report["composition_ids"] = {key: value["composition_id"]
                                     for key, value in compositions.items()}
        if selection in compositions:
            report["composition_id"] = compositions[selection]["composition_id"]
        artifacts.save(report)
    except (OSError, ValueError, KeyError, TypeError) as error:
        report.update(status="blocked", stop_reason=_error(error))
        for row in report["episodes"]:
            row["reason"] = report["stop_reason"]
        with suppress(OSError, ValueError):
            artifacts.save(report)
        return report

    pending_rows = [row for row in report["episodes"] if row["status"] != "reused"]
    for row in pending_rows[:max_episodes]:
        selection = row["selection_id"]
        composition = compositions[selection]
        if not before_episode(report):
            report["stop_reason"] = "stop_requested"
            break
        if should_stop() or clock() >= started + limits.suite_seconds:
            report["stop_reason"] = "stop_requested" if should_stop() else "suite_wall_limit"
            break
        episode_started = clock()
        phase_deadline = episode_started + limits.preparation_seconds
        ref = None
        request = None
        state: dict[str, Any] = {}
        reason = "episode_unknown"
        cleanup: dict[str, Any] | None = None
        evaluation: dict[str, Any] | None = None
        try:
            row.update(status="preparing", reason=None)
            body = {"ownership": "owned", "intent_id": row["intent_id"], "seed": row["seed"],
                    "display_mode": display_mode, "speed": speed}
            if checkpoint:
                body.update(character=manifest["character"], ascension=manifest["ascension"])
            row["artifacts"].append(artifacts.put("prepare-request.json", json_bytes(body)))
            artifacts.save(report)
            resource = client.prepare(body, timeout(phase_deadline))
            ref = digest(resource.get("environment_ref"),
                         "reference_suite.environment_ref", length=32)
            row.update(environment_ref=ref, generation_id=resource.get("generation_id"))
            while True:
                _same_resource(resource, row, client.identity)
                row["world_mutation_offered"] = resource.get("world_mutation_offered") is True
                row["preparation_outcome"] = resource.get("preparation_outcome")
                if resource.get("phase") == "ready":
                    break
                if resource.get("phase") != "preparing":
                    _reject("preparation_not_ready")
                if should_stop():
                    _reject("stop_requested")
                sleep(min(limits.poll_seconds, timeout(phase_deadline)))
                resource = client.native_status(timeout(phase_deadline))
            context = checked_context(resource.get("admission_context"))
            binding = context["binding"]
            if (context["environment_ref"] != ref or binding["ownership"] != "owned"
                    or binding["generation_id"] != row["generation_id"]
                    or any(binding[key] != value for key, value in client.identity.items())
                    or resource["preparation"] != body
                    or binding["installed_tuple"]["game_version"] != manifest["game_version"]
                    or binding["speed"]["requested"] != speed
                    or binding["speed"]["effective"] != speed
                    or binding["presentation"]["requested"] != display_mode):
                _reject("prepared_binding_changed")
            if checkpoint and binding.get("scenario") != {
                "character": manifest["character"], "ascension": manifest["ascension"]
            }:
                _reject("prepared_scenario_changed")
            row["admission_context"] = context
            row["artifacts"].append(artifacts.put("ready.json", json_bytes(resource)))
            request = client.request(selection, ref, run_profile, timeout(phase_deadline))
            if (request.get("schema") != agent_run_request.SCHEMA
                    or request.get("target") != "native"
                    or request.get("operation") != "prepare-and-takeover"
                    or request.get("run_profile") != run_profile
                    or not _same_model_binding(composition, request)
                    or request.get("admission_context", {}).get("native_episode") != context):
                _reject("generated_request_changed")
            row.update(execution_selection_id=request["selection_id"],
                       execution_composition_id=request["composition_id"])
            # The existing HTTP helper offers canonical JSON without a newline.
            row["request"] = artifacts.put(
                "generated-request.json", canonical_json(request).encode())
            row.update(status="submitting", runtime_budget=request.get("budget"))
            artifacts.save(report)  # Failure here cannot offer run-agent.
            if should_stop():
                _reject("stop_requested")
            phase_deadline = clock() + limits.episode_seconds
            offered = client.submit(request, ref, timeout(phase_deadline))  # Exactly one offer.
            operation_id = offered.get("operation", {}).get("id")
            if not isinstance(operation_id, str):
                _reject("operation_identity_missing")
            row.update(operation_id=operation_id, status="running")
            row["artifacts"].append(
                artifacts.put("run-agent-response.json", json_bytes(_status(offered))))
            artifacts.save(report)
            run_id = None
            while True:
                if should_stop():
                    _reject("stop_requested")
                try:
                    state = client.status(timeout(phase_deadline))
                except (OSError, BoundaryError) as error:
                    if (isinstance(error, BoundaryError) and error.code
                            != "workbench_request_failed_check_status_before_retry"):
                        raise
                    # Read transport can disappear before the original owner's
                    # observer publishes the already finalized immutable run.
                    # This consumes the same episode allowance and issues no command.
                    row["status_read_error"] = _error(error)
                    sleep(min(limits.poll_seconds, timeout(phase_deadline)))
                    continue
                # LocalModelService publishes the native resource association
                # before exposing startup_native_episode. Read the resource
                # after that status, so an older detached resource response
                # cannot be mistaken for an Actor replacement during startup.
                # These remain separate observations, with all identity checks.
                resource = client.native_status(timeout(phase_deadline))
                _same_resource(resource, row, client.identity)
                operation = state.get("operation", {})
                if operation.get("id") != operation_id:
                    _reject("operation_replaced")
                association = state.get("startup_native_episode") or {}
                startup = state.get("startup") or {}
                if association.get("admission_context") == context:
                    current_run = startup.get("run_id")
                    if (not isinstance(current_run, str) or association.get("run_id") != current_run
                            or run_id is not None and current_run != run_id):
                        _reject("actor_run_changed")
                    if run_id is None:
                        run_id = current_run
                        row.update(run_id=run_id, actor_started=True)
                        row["artifacts"].append(
                            artifacts.put("actor-started.json", json_bytes(_status(state))))
                        artifacts.save(report)
                    if (not isinstance(resource.get("actor"), dict)
                            or resource["actor"].get("run_id") != run_id):
                        try:
                            row["actor_binding_observations"] = artifacts.put(
                                "actor-binding-observations.json", json_bytes({
                                    "read_order": "application_status_then_native_resource",
                                    "application_status": _status(state),
                                    "native_resource": resource,
                                }))
                        except (OSError, ValueError) as error:
                            row["actor_binding_diagnostic_error"] = _error(error)
                        _reject("resource_actor_changed")
                elif operation.get("status") != "pending":
                    _reject("actor_association_missing")
                observed_evaluation = state.get("evaluation")
                if (run_id is not None and state.get("status") == "stopped"
                        and isinstance(observed_evaluation, dict)
                        and observed_evaluation.get("run_id") == run_id):
                    # Only original-owner publication ends this wait. Full
                    # request/evaluation bytes and Stop evidence are checked
                    # after exact Environment finish, before any next dispatch.
                    reason = "owner_finalized"
                    break
                if state.get("status") in {
                    "command_unknown", "recovery_required", "failed",
                } or operation.get("status") not in {"pending", "completed"}:
                    _reject("actor_operation_unknown_or_failed")
                if (state.get("status") in {"runtime_exited", "stopped"}
                        or state.get("observation_error")):
                    sleep(min(limits.poll_seconds, timeout(phase_deadline)))
                    continue
                runtime = state.get("runtime") or {}
                if run_id is not None:
                    if runtime.get("run_id") != run_id:
                        _reject("runtime_run_changed")
                    if runtime.get("tainted") is True or runtime.get("pending_request") is not None:
                        _reject("delivery_or_runtime_unknown")
                    if runtime.get("autonomy_budget", {}).get("state") == "exhausted":
                        reason = "runtime_budget_exhausted"
                        break
                    if (runtime.get("lifecycle") == "stopped"
                            or runtime.get("autonomy_budget", {}).get("ended_reason") == "stopped"
                            or (runtime.get("last_directive") or {}).get("type") == "close"):
                        # Close first changes mode/budget, then flushes Stop and
                        # seals. Wait for its owner instead of racing it with Stop.
                        sleep(min(limits.poll_seconds, timeout(phase_deadline)))
                        continue
                    if operation.get("status") == "completed" and runtime.get("mode") == "human":
                        reason = "actor_returned_human"
                        break
                sleep(min(limits.poll_seconds, timeout(phase_deadline)))
        except KeyboardInterrupt:
            reason = "interrupted"
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = _error(error)
        finally:
            row["reason"] = reason
            cleanup_deadline = clock() + limits.cleanup_seconds

            def cleanup_timeout(cap: float, deadline: float = cleanup_deadline) -> float:
                remaining = deadline - clock()
                if remaining <= 0:
                    _reject("cleanup_wall_limit")
                return min(cap, remaining)

            if state:
                try:
                    row["stop_observation"] = artifacts.put(
                        "stop-observation.json", json_bytes(_status(state)))
                except (OSError, ValueError) as error:
                    row["persistence_error"] = _error(error)
            # Never infer a lost preparation's ref from latest. Only the same
            # original intent and Application may supply it through a read.
            if ref is None:
                with suppress(OSError, ValueError, KeyError, TypeError):
                    resource = client.native_status(
                        cleanup_timeout(limits.http_seconds))
                    if (resource.get("intent_id") == row["intent_id"]
                            and all(resource.get(key) == value
                                    for key, value in client.identity.items())):
                        ref = resource["environment_ref"]
                        row.update(environment_ref=ref, generation_id=resource.get("generation_id"),
                                   world_mutation_offered=resource.get(
                                       "world_mutation_offered") is True)
            if ref is not None:
                try:
                    row["status"] = "finishing"
                    try:
                        artifacts.save(report)
                    except (OSError, ValueError) as error:
                        row["persistence_error"] = _error(error)
                    # Cleanup has its own finite allowance even when suite/Actor
                    # wall expires; it does not reopen the Actor's Runtime budget.
                    cleanup = client.finish(ref, cleanup_timeout(180))
                    row["finish"] = artifacts.put("finish.json", json_bytes(cleanup))
                    if (cleanup.get("environment_ref") != ref
                            or cleanup.get("generation_id") != row["generation_id"]):
                        _reject("finish_resource_changed")
                    if cleanup.get("finish_complete") is not True:
                        _reject("finish_not_confirmed")
                    state = client.status(cleanup_timeout(limits.http_seconds))
                    evaluation = _seal(client, artifacts, row, state, cleanup, request)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    row["cleanup_error"] = _error(error)
            row["last_status"] = _status(state)
            clean = _clean(cleanup, evaluation) and "persistence_error" not in row
            row["cleanup_accepted"] = clean
            row["status"] = "finished" if clean else "unknown"
            if evaluation is not None:
                row["agent_task_outcome"] = evaluation.get("agent_task_outcome")
                row["delivery"] = {key: evaluation.get(key) for key in (
                    "tainted", "pending_request", "request_outcome_counts", "delivery_counts")}
                if checkpoint:
                    try:
                        metrics = client.metrics(evaluation, row["admission_context"])
                        row["game_metrics"] = {k: v for k, v in metrics.items() if k != "observations"}
                        row["metrics"] = artifacts.put(
                            "game-metrics.json", json_bytes(metrics))
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        row["metrics_error"] = _error(error)
            row["elapsed_seconds"] = clock() - episode_started
            try:
                artifacts.save(report)
            except (OSError, ValueError) as error:
                row.update(status="unknown", persistence_error=_error(error))
                clean = False
                reason = "report_persistence_failed"
        if evaluation is not None and evidence_peer is not None:
            try:
                preservation = client.preserve(row["run_id"], evidence_peer,
                                               timeout(started + limits.suite_seconds))
                row["preservation_offer"] = artifacts.put(
                    "preservation-offer.json", json_bytes(preservation))
                operation_id = preservation.get("operation_id")
                if not isinstance(operation_id, str):
                    _reject("preservation_operation_missing")
                while preservation.get("status") == "pending":
                    sleep(min(limits.poll_seconds, timeout(started + limits.suite_seconds)))
                    preservation = client.preservation_status(
                        timeout(started + limits.suite_seconds))
                    if preservation.get("operation_id") != operation_id:
                        _reject("preservation_operation_changed")
                row["preservation"] = artifacts.put("preservation.json", json_bytes(preservation))
                if (preservation.get("status") != "completed"
                        or preservation.get("run_id") != row["run_id"]
                        or preservation.get("peer") != evidence_peer
                        or not preservation.get("artifact_id")
                        or not preservation.get("peer_readback")):
                    _reject("original_evidence_not_preserved")
                row["stored_evidence_id"] = preservation["artifact_id"]
                artifacts.save(report)
            except (OSError, ValueError, KeyError, TypeError) as error:
                row["preservation_error"] = _error(error)
                reason = "preservation_unknown_or_failed"
                artifacts.save(report)
        if not clean or reason not in {
            "runtime_stopped", "actor_returned_human", "owner_finalized",
        }:
            report["stop_reason"] = reason
            break
    for row in report["episodes"]:
        if row["status"] == "not_started":
            row["reason"] = report["stop_reason"] or "canary_limit"
    report["summary"] = {
        "not_started": sum(row["status"] == "not_started" for row in report["episodes"]),
        "reused_finished": sum(row["status"] == "reused" for row in report["episodes"]),
        "cleanup_unknown": sum(row["status"] == "unknown" for row in report["episodes"]),
        "preserved_evidence": sum("stored_evidence_id" in row for row in report["episodes"]),
        "preservation_failed_or_unknown": sum(
            "preservation_error" in row for row in report["episodes"]),
        "natural_results": {result: sum(
            row.get("agent_task_outcome", {}).get("terminal_observation", {}).get("status")
            == "observed" and row["agent_task_outcome"]["terminal_observation"].get("result")
            == result for row in report["episodes"]) for result in ("win", "loss")},
        "task_completion": {status: sum(row.get("agent_task_outcome", {})
            .get("task_completion", {}).get("status") == status for row in report["episodes"])
            for status in ("completed", "unproved", "unknown")},
        "mechanism_coverage": "retained_in_original_evaluations_not_qualified_by_dispatcher",
    }
    report.update(status="stopped" if report["stop_reason"] else "completed",
                  elapsed_seconds=clock() - started,
                  world_offers=sum(row["world_mutation_offered"] for row in report["episodes"]),
                  actor_started=sum(row["actor_started"] for row in report["episodes"]))
    try:
        artifacts.save(report)
    except (OSError, ValueError) as error:
        report.update(status="stopped", stop_reason="report_persistence_failed",
                      persistence_error=_error(error))
    return report


def _same_model_binding(selected: dict[str, Any], request: dict[str, Any]) -> bool:
    """An exact registration or an environment-only P1 execution registration."""
    actual = request.get("composition")
    if (not isinstance(actual, dict) or request.get("selection_id") != actual.get("selection_id")
            or request.get("composition_id") != actual.get("composition_id")):
        return False
    if request.get("selection_id") == selected.get("selection_id"):
        return json_bytes(actual) == json_bytes(selected)
    original, effective = selected.get("semantic_definition"), actual.get("semantic_definition")
    implementation = actual.get("implementation", {})
    return not (not isinstance(original, dict) or not isinstance(effective, dict)
            or selected.get("implementation", {}).get("adapter", {}).get("id")
            not in {"stpd-p1-baseline-agent", "stpd-p1-mstd-agent"}
            or request.get("selection_id") != actual.get("selection_id")
            or request.get("composition_id") != actual.get("composition_id")
            or hashlib.sha256(canonical_json({key: value for key, value in actual.items()
                if key != "composition_id"}).encode()).hexdigest() != actual.get("composition_id")
            or json_bytes({key: value for key, value in original.items() if key != "environment"})
            != json_bytes({key: value for key, value in effective.items() if key != "environment"})
            or any(json_bytes(implementation.get(key))
                   != json_bytes(selected.get("implementation", {}).get(key))
                   for key in ("adapter", "runtime_artifact"))
            or effective.get("environment", {}).get("profile") != "p1-global-r1"
            or effective.get("environment", {}).get("requirements", {})
            .get("environment", {}).get("host_kind") != "reference")


def _seal(client: ReferenceClient, artifacts: _Artifacts, row: dict[str, Any],
          state: dict[str, Any], finish: dict[str, Any],
          generated: dict[str, Any] | None) -> dict[str, Any]:
    actor = finish.get("actor_finish") or {}
    reference = actor.get("evaluation_reference")
    evaluation = state.get("evaluation")
    startup = state.get("startup") or {}
    association = state.get("startup_agent_run_request") or {}
    request_ref = association.get("request")
    run_id = row.get("run_id")
    if (run_id is None and generated is not None
            and (state.get("startup_native_episode") or {}).get("admission_context")
            == row.get("admission_context") and isinstance(request_ref, dict)):
        # A lost POST reply is never retried. Original owner records may still
        # identify that exact submitted request after its Actor has been stopped.
        run_id = startup.get("run_id")
        row.update(run_id=run_id, operation_id=request_ref.get("operation_id"), actor_started=True)
    if (run_id is None or startup.get("run_id") != run_id or actor.get("run_id") != run_id
            or association.get("run_id") != run_id or not isinstance(request_ref, dict)
            or not isinstance(reference, dict) or reference.get("run_id") != run_id
            or not isinstance(evaluation, dict) or evaluation.get("run_id") != run_id
            or hashlib.sha256(json_bytes(evaluation)).hexdigest() != reference.get("sha256")):
        _reject("exact_evaluation_or_request_missing")
    body = {key: item for key, item in evaluation.items() if key != "evaluation_id"}
    if hashlib.sha256(canonical_json(body).encode()).hexdigest() != evaluation.get("evaluation_id"):
        _reject("evaluation_content_id_changed")
    execution = evaluation.get("agent_execution", {})
    episode = execution.get("native_episode", {})
    if (execution.get("run_id") != run_id or execution.get("request") != request_ref
            or episode.get("status") != "bound"
            or episode.get("admission_context") != row.get("admission_context")):
        _reject("evaluation_execution_binding_changed")
    original_request, original_evaluation = client.originals(evaluation, request_ref, startup)
    admitted = decode_json(original_request)
    if (hashlib.sha256(original_request).hexdigest() != request_ref.get("sha256")
            or not isinstance(admitted, dict) or admitted.get("operation_id") != row["operation_id"]
            or json_bytes({key: value for key, value in admitted.items() if key != "operation_id"})
            != json_bytes(generated)
            or original_evaluation != canonical_json(evaluation).encode()):
        _reject("original_bytes_or_binding_changed")
    row["original_request"] = artifacts.put("original-agent-request.json", original_request)
    row["evaluation"] = artifacts.put("original-evaluation.json", original_evaluation)
    row["evaluation_reference"] = reference
    row["evaluation_id"] = evaluation["evaluation_id"]
    row["evidence_content_id"] = evaluation.get("evidence_content_id")
    return evaluation


def _clean(finish: dict[str, Any] | None, evaluation: dict[str, Any] | None) -> bool:
    if finish is None or evaluation is None:
        return False
    actor, cleanup = finish.get("actor_finish", {}), finish.get("cleanup", {})
    end = evaluation.get("agent_task_outcome", {}).get("operation_end", {})
    controller = evaluation.get("controller_at_stop", end.get("controller"))
    tainted = evaluation.get("tainted", end.get("tainted"))
    return (finish.get("finish_complete") is True and finish.get("phase") == "closed"
            and (actor.get("status") == "stopped"
                 or actor.get("status") == "owned_process_contained"
                 and type(actor.get("process_exit_code")) is int
                 and actor["process_exit_code"] == 0)
            and actor.get("seal") == "available"
            and cleanup.get("native_exit_disposition") == "graceful"
            and cleanup.get("native_shutdown_ack") == "runtime_bound_acknowledged"
            and cleanup.get("driver_exit_code") == 0 and cleanup.get("forced_fallback") is False
            and cleanup.get("cleanup_error") is None
            and evaluation.get("evidence_verification") == "pass"
            and evaluation.get("agent_execution", {}).get("status") == "bound"
            and controller == "released"
            and tainted is False
            and end.get("status") in {"task_ended", "censored"}
            and evaluation.get("pending_request") is None
            and not any(evaluation.get("delivery_counts", {}).get(key, 0) for key in ("unknown",))
            and not any(evaluation.get("request_outcome_counts", {}).get(key, 0)
                        for key in ("unknown", "partially_delivered"))
            )
