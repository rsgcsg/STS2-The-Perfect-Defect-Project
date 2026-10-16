"""Read-only common Task outcome projection from bound raw Managed evaluation.

The accepted local file index, source snapshot and exact original observation
joins establish this scoped grammar. It is never sealed Policy Runtime evidence.
The existing agent_task_outcome.project verifier and archival semantics are unchanged.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from spireagent.json_boundary import BoundaryError

from .task_outcome import AgentTaskOutcome, _empty

INDEX_SCHEMA = "spireagent/managed-evaluation-file-index-v1"
SOURCE_FILES = (
    "stpd/evaluation/gates.py",
    "stpd/evaluation/managed_suite.py",
    "stpd/evaluation/suites.py",
    "tools/evaluate_managed_suite.py",
    "spireagent/managed_task_outcome.py",
    "spireagent/task_outcome.py",
    "spireagent/workbench/agent_task_outcome.py",
    "uv.lock",
)
EVALUATION_FILES = SOURCE_FILES[:4]
TASK_SPEC = {
    "schema": "stpd/managed-natural-terminal-task-spec-v1",
    "id": "managed-standard-run-natural-terminal",
    "version": "1.0.0",
    "input_schema": "sts2.player-environment/snapshot-1",
    "completion": "complete_public_game_over_surface_victory_boolean",
    "navigation": "uniform_complete_bound_catalog",
    "return_to_menu_required": False,
}


def managed_terminal(snapshot: dict[str, Any]) -> Literal["win", "loss"] | None:
    interaction = snapshot.get("interaction", {})
    if interaction.get("kind") != "game_over":
        return None
    content = interaction.get("content", {})
    surface, context = content.get("surface", {}), content.get("context", {})
    catalog = snapshot.get("bound_actions", {})
    if (
        snapshot.get("schema") != TASK_SPEC["input_schema"]
        or snapshot.get("status") != "observed"
        or snapshot.get("completeness", {}).get("status") != "complete"
        or snapshot.get("completeness", {}).get("missing") != []
        or interaction.get("stage") != "complete"
        or interaction.get("content_schema") != "sts2.player-environment/surface/game_over-1"
        or surface.get("kind") != "game_over"
        or surface.get("stage") != "complete"
        or context.get("kind") != "terminal"
        or type(surface.get("victory")) is not bool
        or catalog.get("status") != "complete"
        or catalog.get("actions") != []
        or catalog.get("total_count") != 0
        or catalog.get("materialized_count") != 0
    ):
        raise BoundaryError("managed_task_outcome", "managed_terminal_incomplete")
    return "win" if surface["victory"] else "loss"


class ManagedEvidenceIndex:
    """Hash-bound original raw files; caller supplies the externally retained index SHA."""

    def __init__(self, directory: Path, expected_index_sha256: str):
        self.directory = directory.resolve()
        path = self.directory / "evidence-index.json"
        if path.is_symlink():
            raise BoundaryError("managed_task_outcome", "managed_index_symlink")
        raw = path.read_bytes()
        if len(raw) > 4 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_index_sha256:
            raise BoundaryError("managed_task_outcome", "managed_index_changed")
        value = json.loads(raw)
        if value.get("schema") != INDEX_SCHEMA:
            raise BoundaryError("managed_task_outcome", "managed_index_schema")
        self.files: dict[str, dict[str, Any]] = {}
        for item in value["files"]:
            relative = item["path"]
            if (
                not isinstance(relative, str)
                or Path(relative).is_absolute()
                or ".." in Path(relative).parts
                or relative in self.files
            ):
                raise BoundaryError("managed_task_outcome", "managed_file_path")
            self.files[relative] = item
        self.index_sha256 = expected_index_sha256

    def read(self, relative: str, maximum: int = 64 * 1024 * 1024) -> bytes:
        item = self.files.get(relative)
        if item is None or type(item.get("bytes")) is not int or not 0 <= item["bytes"] <= maximum:
            raise BoundaryError("managed_task_outcome", "managed_file_unavailable")
        path = self.directory / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(self.directory)
        ):
            raise BoundaryError("managed_task_outcome", "managed_file_changed")
        raw = path.read_bytes()
        if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise BoundaryError("managed_task_outcome", "managed_file_changed")
        return raw

    def source_binding(self, meta: dict[str, Any]) -> None:
        for relative in SOURCE_FILES:
            raw = self.read("evaluation-source/" + relative)
            if hashlib.sha256(raw).hexdigest() != meta["source_file_sha256"][relative]:
                raise BoundaryError("managed_task_outcome", "managed_source_changed")
        digest = hashlib.sha256()
        for relative in sorted(EVALUATION_FILES):
            digest.update(
                ("python/" + relative).encode() + b"\0" + self.read("evaluation-source/" + relative)
            )
        if digest.hexdigest() != meta["evaluation_code_sha256"]:
            raise BoundaryError("managed_task_outcome", "managed_evaluation_source_changed")


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise BoundaryError("managed_task_outcome", reason)


def _ready(ready: dict[str, Any], game_version: str) -> tuple[str, str]:
    exact, manifest = ready["exact_game"], ready["candidate_manifest"]
    build, loaded = ready["candidate_build"], ready["runtime_identity"]
    expected = manifest["expected_build"]
    _require(
        exact == manifest["exact_game"] and exact["version"] == game_version,
        "managed_exact_game_mismatch",
    )
    _require(
        build["artifact_sha256"] == expected["artifact_sha256"] == loaded["host_assembly_sha256"]
        and build["artifact_mvid"] == expected["artifact_mvid"] == loaded["host_module_mvid"]
        and build["source_patch_sha256"] == expected["source_patch_sha256"]
        and build["upstream_revision"] == manifest["upstream"]["revision"]
        and build["original_sts2_sha256"]
        == build["runtime_sts2_sha256"]
        == loaded["sts2_assembly_sha256"]
        == exact["sts2_dll_sha256"],
        "managed_loaded_tuple_mismatch",
    )
    headless = ready["headless"]
    _require(
        bool(headless["source_digest_sha256"]) and bool(headless["source_revision"]),
        "managed_host_source_missing",
    )
    runtime, fingerprint = ready["adapter_runtime_instance_id"], ready["environment_fingerprint"]
    _require(
        isinstance(runtime, str)
        and bool(runtime)
        and isinstance(fingerprint, str)
        and bool(fingerprint),
        "managed_runtime_identity_missing",
    )
    return runtime, fingerprint


def project_managed_episode(index: ManagedEvidenceIndex, ordinal: int) -> AgentTaskOutcome:
    """Project only verified original current episode facts, never report-row assertions."""
    outcome = _empty("managed_raw_unverified")
    try:
        config = json.loads(index.read("config.json"))
        meta = json.loads(index.read("meta.json"))
        index.source_binding(meta)
        _require(config["task_spec"] == TASK_SPEC, "managed_task_spec_changed")
        _require(
            config["agent_input_scope"]["selection_input"] == "complete_bound_action_ids_only"
            and config["agent_input_scope"]["read_calls"] == [],
            "managed_input_scope_changed",
        )
        manifest = config["manifest"]
        _require(
            type(ordinal) is int and 1 <= ordinal <= len(manifest["seeds"]),
            "managed_ordinal_invalid",
        )
        seed = manifest["seeds"][ordinal - 1]
        relative = f"episode-{ordinal:04d}/events.jsonl"
        raw = index.read(relative)
        events = [json.loads(line) for line in raw.splitlines()]
        _require(
            bool(events) and [e["sequence"] for e in events] == list(range(1, len(events) + 1)),
            "managed_event_sequence_changed",
        )
        _require(
            events[0]["kind"] == "ready" and events[-1]["kind"] == "episode_end",
            "managed_episode_grammar_incomplete",
        )
        runtime, fingerprint = _ready(events[0]["value"], manifest["game_version"])
        ready = events[0]["value"]
        provenance = None
        reset = None
        latest_snapshot = None
        terminal = None
        witness = None
        offer = None
        unknown = False
        close = None
        cleanup_error = False
        for event in events[1:-1]:
            kind, value = event["kind"], event["value"]
            if kind == "episode_identity":
                _require(provenance is None, "managed_duplicate_provenance")
                provenance = value["episode_provenance"]
                _require(
                    provenance["verdict"] == "provenance_pass"
                    and provenance["requested_seed"] == provenance["actual_seed"] == seed
                    and provenance["runtime_instance_id"] == runtime,
                    "managed_seed_runtime_mismatch",
                )
            elif kind in {"reset_snapshot", "snapshot"}:
                _require(
                    not unknown and close is None and terminal is None,
                    "managed_observation_after_end",
                )
                _require(
                    value["session"]
                    == {"runtime_instance_id": runtime, "environment_fingerprint": fingerprint},
                    "managed_observation_runtime_mismatch",
                )
                run = value["persistent"]["content"]["run"]
                player = value["persistent"]["content"]["player"]
                _require(
                    run["ascension"] == manifest["ascension"] == 0
                    and player["character_definition_id"] == manifest["character"].upper(),
                    "managed_configuration_mismatch",
                )
                if kind == "reset_snapshot":
                    _require(reset is None, "managed_duplicate_reset")
                    reset = value
                else:
                    _require(
                        reset is not None and provenance is not None,
                        "managed_missing_reset_provenance",
                    )
                    latest_snapshot = value
                    terminal = managed_terminal(value)
                    if terminal:
                        witness = {
                            "events": index.files[relative],
                            "event_sequence": event["sequence"],
                            "snapshot_id": value["snapshot_id"],
                            "profile": TASK_SPEC["input_schema"],
                        }
            elif kind == "step_offer":
                _require(
                    not unknown
                    and close is None
                    and terminal is None
                    and offer is None
                    and latest_snapshot is not None,
                    "managed_mutation_after_end_or_without_basis",
                )
                assert latest_snapshot is not None
                catalog = latest_snapshot["bound_actions"]
                actions = catalog["actions"]
                _require(
                    catalog["status"] == "complete"
                    and catalog["materialized_count"] == catalog["total_count"] == len(actions)
                    and value["snapshot_id"] == latest_snapshot["snapshot_id"]
                    and value["action_id"] in [a["bound_action_id"] for a in actions],
                    "managed_offer_not_in_complete_catalog",
                )
                offer = value
            elif kind == "step":
                _require(
                    not unknown and close is None and offer is not None,
                    "managed_step_without_offer",
                )
                assert offer is not None
                receipt = value["receipt"]
                if not isinstance(receipt, dict) or receipt.get("delivery") == "unknown":
                    unknown = True
                else:
                    _require(
                        value["request_id"] == offer["request_id"] == receipt["request_id"]
                        and value["action_id"]
                        == offer["action_id"]
                        == receipt["action"]["bound_action_id"]
                        and receipt["delivery"] in {"delivered", "not_delivered"},
                        "managed_receipt_binding_changed",
                    )
                offer = None
            elif kind == "error":
                unknown = unknown or value.get("pending") == "step"
            elif kind == "close_receipt":
                _require(close is None, "managed_duplicate_close")
                close = value
            elif kind in {"reset_offer", "cleanup_error"}:
                cleanup_error = cleanup_error or kind == "cleanup_error"
                _require(not unknown or kind == "cleanup_error", "managed_reset_after_unknown")
            else:
                raise BoundaryError("managed_task_outcome", "managed_unknown_event_kind")
        _require(
            reset is not None and provenance is not None and close is not None,
            "managed_required_episode_facts_missing",
        )
        end = events[-1]["value"]
        unknown = unknown or offer is not None
        _require(end["unknown_delivery"] == unknown, "managed_unknown_summary_mismatch")
        _require(end["terminal_outcome"] == terminal, "managed_terminal_summary_mismatch")
        assert close is not None
        cleanup = (
            close.get("driver_exit_code") == 0
            and close.get("forced_fallback") is False
            and close.get("cleanup_error") is None
            and not cleanup_error
        )
        reason = end["censor_reason"]
        _require(
            terminal is None or reason in {None, "cleanup_unknown"},
            "managed_terminal_censor_mismatch",
        )
        outcome["evidence"].update(
            {
                "verification": "managed_raw_files_verified",
                "scope": "catalog_only_engineering_reliability",
                "sealed_policy_runtime_evidence": False,
                "content_id": index.index_sha256,
                "events": index.files[relative],
                "config": index.files["config.json"],
                "meta": index.files["meta.json"],
            }
        )
        outcome["agent_composition"] = {
            "agent": {"id": meta["agent_id"]},
            "models": [],
            "task_spec": dict(TASK_SPEC),
            "binding": "managed_raw_source_bound",
            "runtime_identity": ready,
        }
        outcome["task_completion"].update(
            {
                "status": "completed" if terminal and not unknown else "unproved",
                "task_spec": dict(TASK_SPEC),
                "reason_codes": []
                if terminal and not unknown
                else [reason or "terminal_not_observed"],
                "witness": witness if terminal and not unknown else None,
            }
        )
        outcome["terminal_observation"].update(
            {
                "status": "observed" if terminal else "not_observed",
                "result": terminal,
                "ready_summary": None,
                "witnesses": [witness] if witness else [],
            }
        )
        outcome["operation_end"].update(
            {
                "status": "unknown"
                if unknown or not cleanup
                else "task_ended"
                if terminal
                else "censored",
                "reason_codes": [reason] if reason else [],
                "event_sequence": events[-1]["sequence"],
                "controller": None,
                "tainted": unknown,
                "recorded_reason": reason,
                "budget": config["limits"],
            }
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError, BoundaryError) as error:
        reason = (
            error.code
            if isinstance(error, BoundaryError)
            else "managed_original_projection_unavailable"
        )
        # Failed projection must never retain a partially published completion.
        outcome = _empty("managed_raw_unverified")
        outcome["task_completion"]["reason_codes"] = [reason]
        outcome["operation_end"]["reason_codes"] = [reason]
    return outcome
