"""One mixed recording use case over the existing recorder, Runtime and Store.

The native lane retains origin=unknown. Neither this index nor a source
declaration grants Human demonstration, training or model-score eligibility.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tarfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, cast
from uuid import uuid4

from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.workbench.developer import ROOT, atomic_json

SCHEMA = "spireagent/mixed-run-1"
BLOCKED = "blocked_pending_user_policy"
DEFAULT_SETTINGS = {
    "delay_ms": 1000,
    "single_step": False,
    "shop": False,
    "card_reward": False,
    "relic_reward": False,
    "boss": False,
    "confidence_below": None,
}


def reject(reason: str) -> NoReturn:
    raise BoundaryError("mixed_run", reason)


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(DEFAULT_SETTINGS):
        reject("invalid_settings")
    if (
        type(value["delay_ms"]) is not int
        or not 0 <= value["delay_ms"] <= 3000
        or any(
            type(value[k]) is not bool
            for k in DEFAULT_SETTINGS
            if k not in {"delay_ms", "confidence_below"}
        )
        or value["confidence_below"] is not None
        and (
            type(value["confidence_below"]) not in {int, float}
            or not 0 <= value["confidence_below"] <= 1
        )
    ):
        reject("invalid_settings")
    return dict(value)


def preview(pending: dict[str, Any]) -> dict[str, Any]:
    """Consumer-owned presentation of recorded complete scores, never inference."""
    detail = pending["detail"]
    catalog = detail["observation"]["catalog"]
    directive = detail["directive"]
    chosen = [i for i, c in enumerate(catalog) if c["choice_id"] == detail["choice_id"]]
    scores = directive.get("scores", {}).get("values")
    probabilities = None
    if (
        isinstance(scores, list)
        and len(scores) == len(catalog)
        and scores
        and all(type(x) in {int, float} and math.isfinite(x) for x in scores)
    ):
        weights = [math.exp(x - max(scores)) for x in scores]
        probabilities = [x / sum(weights) for x in weights]
    if len(chosen) != 1:
        reject("preview_choice_binding")
    return {
        "review_id": pending["id"],
        "epoch": pending["epoch"],
        "acquisition_id": detail["acquisition_id"],
        "choice": catalog[chosen[0]],
        "scene": {
            key: detail["observation"]["scene"].get(key) for key in ("family", "native_kind")
        },
        "before_boss": any(
            node.get("entity_id") == catalog[chosen[0]].get("subject")
            and node.get("point_type") == "boss"
            for node in detail["observation"]["scene"]
            .get("content", {})
            .get("surface", {})
            .get("next_options", [])
        ),
        "model_probabilities": probabilities,
        "confidence": probabilities[chosen[0]] if probabilities else None,
        "probability_meaning": "softmax_of_recorded_scores_not_win_probability",
        "behavior": "original_agent_selection_unchanged",
    }


def verify_cut(raw: Path, cut: dict[str, Any]) -> None:
    watermark = cut["watermark"]
    for filename, length, digest in (
        ("p1-native-events.jsonl", "events_byte_count", "events_sha256"),
        ("run-journal.jsonl", "journal_byte_count", "journal_sha256"),
    ):
        count = watermark[length]
        if type(count) is not int or count < 0:
            reject("invalid_cut")
        with (raw / filename).open("rb") as source:
            h = hashlib.sha256()
            while count:
                part = source.read(min(count, 1024 * 1024))
                if not part:
                    reject("cut_exceeds_closed_original")
                h.update(part)
                count -= len(part)
        if h.hexdigest() != watermark[digest]:
            reject("cut_hash_mismatch")


def environment_index(
    raw: Path, runs: dict[str, Path], journal: list[dict[str, Any]]
) -> dict[str, Any]:
    """Preserve each original order and the cross-lane cuts; never order by UTC."""
    manifest = json.loads((raw / "recording-manifest.json").read_bytes())
    if manifest.get("p1_native_origin") != "unknown" or not manifest.get("mixed_run_id"):
        reject("mixed_original_must_not_claim_human")
    cuts = [row["cut"] for row in journal if "cut" in row]
    for cut in cuts:
        if (
            cut["session_id"] != manifest["session_id"]
            or cut["timeline_id"] != manifest["timeline_id"]
        ):
            reject("cut_session_mismatch")
        verify_cut(raw, cut)
    native = []
    native_rows = [
        json.loads(line) for line in (raw / "p1-native-events.jsonl").read_bytes().splitlines()
    ]
    terminals = {
        row["decision_id"]: row
        for row in native_rows
        if row["kind"] == "lifecycle"
        and row.get("phase") in {"finished", "cancelled", "aborted", "unknown"}
    }
    for row in native_rows:
        if row["kind"] == "decision_accepted":
            proved = (
                row.get("native_witness") is not None
                and row.get("basis") is not None
                and row.get("missing_reason") is None
            )
            native.append(
                {
                    "lane": "native",
                    "sequence": row["sequence"],
                    "decision_id": row["decision_id"],
                    "actor": "native_operator" if proved else "unknown",
                    "terminal": terminals.get(row["decision_id"]),
                    "outcome_unknown": row["decision_id"] not in terminals
                    or terminals[row["decision_id"]]["phase"] == "unknown",
                    "basis": row.get("basis"),
                    "witness": row.get("native_witness"),
                    "source_declaration_used": False,
                }
            )
    model = []
    for run_id, directory in runs.items():
        events = [json.loads(x) for x in (directory / "events.jsonl").read_bytes().splitlines()]
        receipts = {
            e["payload"]["request_id"]: e["payload"]["receipt"]
            for e in events
            if e["kind"] == "global_receipt"
        }
        not_sent = {
            e["payload"]["request_id"]
            for e in events
            if e["kind"] == "global_dispatch_error" and not e["payload"]["sent"]
        }
        for event in events:
            if event["kind"] != "global_dispatch":
                continue
            p = event["payload"]
            receipt = receipts.get(p["request_id"])
            model.append(
                {
                    "lane": "runtime",
                    "run_id": run_id,
                    "sequence": event["sequence"],
                    "epoch": p["recovery_epoch"],
                    "request_id": p["request_id"],
                    "actor": "model" if receipt is not None else "unknown",
                    "delivery": receipt["delivery"]
                    if receipt
                    else "not_started"
                    if p["request_id"] in not_sent
                    else "unknown",
                    "receipt": receipt,
                    "source_ordinal": p["source_ordinal"],
                    "outcome_unknown": receipt["delivery"] == "unknown"
                    if receipt
                    else p["request_id"] not in not_sent,
                    "choice_id": p["choice_id"],
                    "bound_action": p["bound_action"],
                    "expected_snapshot_id": p["expected_snapshot_id"],
                }
            )
    return {
        "schema": "spireagent/mixed-environment-index-1",
        "session_id": manifest["session_id"],
        "native_operations": native,
        "model_operations": model,
        "control_boundaries": journal,
        "ordering": "original_lane_order_and_durable_cuts_no_wall_clock_interpolation",
        "training_admission": BLOCKED,
        "score_eligible": False,
        "human_demonstration_admission": "undecided",
    }


def mixed_binding(state_dir: Path, session: str) -> dict[str, Any] | None:
    for path in (state_dir / "models/mixed-runs").glob("*/binding.json"):
        value = json.loads(path.read_bytes())
        if value.get("session_id") == session:
            state_path = path.parent / "state.json"
            state = json.loads(state_path.read_bytes())
            return {
                "mixed_run_id": value["mixed_run_id"],
                "recording_kind": "mixed_run",
                "training_admission": BLOCKED,
                "score_eligible": False,
                "mixed_sealed": state.get("status") == "sealed",
                "mixed_artifact_id": state.get("artifact_id"),
            }
    return None


class MixedRunApplication:
    def __init__(self, app: Any) -> None:
        self.app, self.models = app, app.models
        self.root = app.config.state_dir / "models/mixed-runs"
        self.lock = threading.RLock()
        self.current: dict[str, Any] | None = None
        self.review_seen: tuple[str, float] | None = None
        self.last_pause_key: str | None = None
        pointer = self.root / "active.json"
        if pointer.is_file():
            identity = json.loads(pointer.read_bytes())["mixed_run_id"]
            path = self.root / identity / "state.json"
            self.current = json.loads(path.read_bytes())
            # A restarted Application never silently adopts or resumes a model.
            if self.current["status"] not in {"sealed", "failed", "start_unknown"}:
                self.current.update(status="recovery_required", error="application_restarted")
                self._save()

    def _save(self) -> None:
        assert self.current is not None
        atomic_json(self.root / self.current["mixed_run_id"] / "state.json", self.current)

    def _event(self, kind: str, **facts: Any) -> None:
        assert self.current is not None
        path = self.root / self.current["mixed_run_id"] / "control.jsonl"
        with path.open("ab") as target:
            target.write(
                json_bytes({"kind": kind, "recorded_at": datetime.now(UTC).isoformat(), **facts})
            )
            target.flush()
            os.fsync(target.fileno())

    def status(self) -> dict[str, Any]:
        with self.lock:
            value = self.current or {"schema": SCHEMA, "status": "idle"}
            return cast(
                dict[str, Any],
                json.loads(
                    json.dumps(
                        {
                            k: v
                            for k, v in value.items()
                            if k not in {"startups", "review_approvals", "observed_control"}
                        }
                    )
                ),
            )

    def start(self) -> dict[str, Any]:
        from .native_tasks import P1_PROFILE

        with self.models.lock, self.lock:
            if self.current and self.current["status"] != "sealed":
                reject("previous_mixed_run_unfinished")
            runtime = (
                self.models.client.request("/status")["status"] if self.models.client else None
            )
            if (
                self.models.client is None
                or not self.models.state.get("loaded")
                or not runtime
                or runtime.get("mode") != "human"
                or runtime.get("controller") != "released"
                or runtime.get("last_observation") is not None
                or runtime.get("last_directive") is not None
                or runtime.get("autonomy_budget", {}).get("submissions_used", 0) != 0
                or self.models.state.get("managed_environment") is not None
                or runtime.get("session", {}).get("profile") != "p1-global-r1"
                or self.models.state.get("native_episode") is not None
            ):
                reject("load_reference_p1_model_in_human_first")
            owner = self.app.native_recording_status()["status"]
            if (runtime.get("environment") or {}).get("runtime_instance_id") != owner[
                "runtime_instance_id"
            ]:
                reject("model_game_identity_mismatch")
            if owner["recording_lifecycle"] not in {"ready", "closed"}:
                reject("start_before_recording_do_not_relabel_existing_original")
            identity = uuid4().hex
            self.root.joinpath(identity).mkdir(parents=True)
            self.current = {
                "schema": SCHEMA,
                "mixed_run_id": identity,
                "status": "starting",
                "runtime_instance_id": owner["runtime_instance_id"],
                "settings": dict(DEFAULT_SETTINGS),
                "model_runs": [runtime["run_id"]],
                "startups": {
                    runtime["run_id"]: self.models._agent_evidence_expected(
                        dict(self.models.client.startup)
                    )
                },
                "training_admission": BLOCKED,
                "score_eligible": False,
            }
            self._save()
            atomic_json(self.root / "active.json", {"mixed_run_id": identity})
            command = str(uuid4())
            self._event("start_requested", command_id=command)
            try:
                result = self.app.control_native_recording(
                    {
                        "kind": "start_new_session",
                        "runtime_instance_id": owner["runtime_instance_id"],
                        "recording_session_id": owner["recording_session_id"],
                        "source_segment_id": None,
                        "source_kind": "unknown",
                        "actor_id": None,
                        "command_id": command,
                        "capture_profile_id": P1_PROFILE,
                        "mixed_run_id": identity,
                    }
                )
                # The common application returns its view after the exact command.
                status = result["status"]
                last = result.get("last_result") or result
                if not last.get("accepted") or status["recording_lifecycle"] != "recording":
                    reject("recording_start_unconfirmed")
                session = status["recording_session_id"]
                self.current.update(status="recording", session_id=session)
                binding = {
                    "schema": SCHEMA,
                    "mixed_run_id": identity,
                    "session_id": session,
                    "runtime_instance_id": owner["runtime_instance_id"],
                    "declared_origin": "unknown",
                    "training_admission": BLOCKED,
                    "score_eligible": False,
                }
                with (self.root / identity / "binding.json").open("xb") as stream:
                    stream.write(json_bytes(binding))
                    stream.flush()
                    os.fsync(stream.fileno())
                self._event("started", session_id=session)
                self._save()
                return self.status()
            except BaseException:
                self.current.update(status="start_unknown")
                self._save()
                raise

    def configure(self, value: Any) -> dict[str, Any]:
        with self.lock:
            if not self.current or self.current["status"] != "recording":
                reject("recording_required")
            self.current["settings"] = settings(value)
            self.last_pause_key = None
            self._event("settings", settings=self.current["settings"])
            self._save()
            return self.status()

    def _owner(self) -> dict[str, Any]:
        assert self.current is not None
        owner = self.models.native_tasks.recording_status(self.app.config.platform_url)
        if (
            owner["runtime_instance_id"] != self.current["runtime_instance_id"]
            or owner["recording_session_id"] != self.current["session_id"]
        ):
            reject("game_or_recording_replaced")
        return cast(dict[str, Any], owner)

    def checkpoint(self) -> dict[str, Any]:
        owner = self._owner()
        result = self.models.native_tasks.recording_command(
            self.app.config.platform_url, owner, "checkpoint_p1_native"
        )
        if not result["accepted"] or result["pending"]:
            reject("checkpoint_unconfirmed")
        cut = result.get("p1_native_checkpoint")
        if (
            not isinstance(cut, dict)
            or cut.get("schema") != "sts2.human-annotator/p1-native-checkpoint-1"
            or cut.get("session_id") != owner["recording_session_id"]
        ):
            reject("checkpoint_identity")
        return cut

    def before_control(self, action: str, client: Any, binding: Any) -> bool:
        with self.lock:
            if not self.current or self.current["status"] == "sealed":
                return False
            if self.current["status"] != "recording":
                reject("mixed_recording_not_ready")
            if self.current.get("review_unknown"):
                reject("review_requires_explicit_human_recovery")
            run = client.startup["run_id"]
            if run not in self.current["model_runs"]:
                if action in {"human", "stop"}:
                    return False
                self.current["model_runs"].append(run)
                self.current.setdefault("startups", {})[run] = self.models._agent_evidence_expected(
                    dict(client.startup)
                )
                self._save()
            if action in {"auto", "one_step", "tick", "shadow"}:
                owner = self._owner()
                if owner["recording_lifecycle"] != "recording" or owner["health"] != {
                    "append_health": "healthy",
                    "disk_health": "healthy",
                    "error": None,
                }:
                    reject("recording_not_healthy")
                cut = self.checkpoint()
                if cut["watermark"]["pending_count"]:
                    reject("native_operation_pending")
                if not client.request("/review")["review"]["enabled"]:
                    client.request("/review", {"enabled": True}, binding=binding)
                self._event(
                    "control_requested",
                    action=action,
                    run_id=run,
                    cut=cut,
                    runtime_epoch=binding.recovery_epoch,
                )
            else:
                # Recovery must reach Runtime first; a disk flush cannot delay Pause.
                self._event("recovery_intent", action=action, run_id=run)
            return True

    def after_control(self, action: str, runtime: dict[str, Any]) -> None:
        with self.lock:
            if not self.current or runtime["run_id"] not in self.current.get("model_runs", []):
                return None
            facts: dict[str, Any] = {
                "action": action,
                "run_id": runtime["run_id"],
                "runtime_epoch": runtime["session"]["recovery_epoch"],
                "controller": runtime["controller"],
                "pending_request": runtime.get("pending_request"),
            }
            if self._owner()["recording_lifecycle"] in {"recording", "paused"}:
                facts["cut"] = self.checkpoint()
            self._event("control_completed", **facts)
            if action == "human" and self.current.get("review_unknown"):
                self.current.setdefault("unknown_reviews", []).append(
                    self.current.pop("review_unknown")
                )
            if (
                action == "stop"
                and self.current["status"] == "finishing"
                and runtime["lifecycle"] == "stopped"
            ):
                self._close_native()
            self._save()

    def _close_native(self) -> None:
        assert self.current is not None
        owner = self._owner()
        if owner["recording_lifecycle"] in {"recording", "paused"}:
            command = str(uuid4())
            self.current["status"] = "closing"
            self._save()
            self._event("close_requested", command_id=command)
            try:
                value = self.models.native_tasks.recording_command(
                    self.app.config.platform_url, owner, "close", command_id=command
                )
                if not value["accepted"]:
                    reject("native_close_rejected")
            except (BoundaryError, OSError, ValueError):
                self.current["status"] = "close_unknown"
                self._save()
                raise

    def finish(self) -> dict[str, Any]:
        with self.lock:
            if not self.current or self.current["status"] != "recording":
                reject("recording_required")
            self.current["status"] = "finishing"
            self._save()
        if self.models.client is None:
            with self.lock:
                self._close_native()
        else:
            self.models.command("stop")
        return self.status()

    def observe(self, client: Any | None, runtime: dict[str, Any] | None) -> None:
        with self.lock:
            if self.current and self.current.get("review_unknown"):
                if self.current["review_unknown"].get("recovery_requested"):
                    return
                self.current["review_unknown"]["recovery_requested"] = True
                self._save()
                recover = True
            else:
                recover = False
        if recover:
            self.models.command("human")
            return
        action = self._observe(client, runtime)
        if action:
            self.models.command(action)

    def _observe(self, client: Any | None, runtime: dict[str, Any] | None) -> str | None:
        with self.lock:
            if not self.current or self.current["status"] not in {"recording", "finishing"}:
                return None
            owner = self._owner()
            if self.current["status"] == "finishing" and (
                runtime is None or runtime["lifecycle"] == "stopped"
            ):
                self._close_native()
                return None
            if owner["recording_lifecycle"] == "closed":
                if runtime and runtime["lifecycle"] != "stopped":
                    self.current["status"] = "finishing"
                    self._save()
                    return "stop"
                return None
            if runtime is not None:
                observed = {
                    "run_id": runtime["run_id"],
                    "mode": runtime["mode"],
                    "epoch": runtime["session"]["recovery_epoch"],
                }
                if observed != self.current.get("observed_control"):
                    self.current["observed_control"] = observed
                    self._event("control_observed", **observed, cut=self.checkpoint())
                    self._save()
            if client is None or runtime is None or runtime["mode"] == "human":
                return None
            review = client.request("/review")["review"]
            pending = review.get("pending")
            if not pending:
                return None
            view = preview(pending)
            self.current["preview"] = view
            if not self.review_seen or self.review_seen[0] != pending["id"]:
                self.review_seen = pending["id"], time.monotonic()
                self._event("preview", run_id=runtime["run_id"], preview=view)
                self._save()
            options = self.current["settings"]
            kind = view["scene"]["native_kind"]
            pause_key = view["acquisition_id"]
            stop = (
                (options["shop"] and kind in {"shop_room", "shop_inventory", "shop"})
                or options["card_reward"]
                and kind in {"card_reward", "card_reward_selection"}
                or options["relic_reward"]
                and kind
                in {"relic_choice", "native_boss_relic_selection", "treasure_relic_selection"}
                or options["boss"]
                and view["before_boss"]
                or options["confidence_below"] is not None
                and view["confidence"] is not None
                and view["confidence"] < options["confidence_below"]
            )
            if stop and pause_key != self.last_pause_key:
                self.last_pause_key = pause_key
                return "human"
            if (time.monotonic() - self.review_seen[1]) * 1000 < options["delay_ms"]:
                return None
            attempts = self.current.setdefault("review_approvals", {})
            if pending["id"] in attempts:
                return None  # The original POST was already offered, including unknown.
            # The append-only control journal retains every prior offer. Only
            # the one current review needs mutable reconciliation state.
            attempts.clear()
            from .local_models import RuntimeControlBinding

            binding = RuntimeControlBinding.from_environment(
                client.request("/environment"), runtime["run_id"]
            )
            self._event(
                "review_approval_requested",
                run_id=runtime["run_id"],
                review_id=pending["id"],
                epoch=pending["epoch"],
            )
            attempts[pending["id"]] = "offered"
            self._save()
            try:
                client.request("/review", {"approve": pending["id"]}, binding=binding)
                attempts[pending["id"]] = "confirmed"
            except (BoundaryError, OSError, ValueError):
                attempts[pending["id"]] = "unknown"
                self.current["review_unknown"] = {
                    "review_id": pending["id"],
                    "run_id": runtime["run_id"],
                    "epoch": pending["epoch"],
                }
                self._save()
                raise
            self._save()
        return None

    def seal(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Called by Q after its ordinary game-exit, operation and space admission."""
        from sts2_platform_evidence import verify_agent_session_run_evidence
        from sts2_platform_evidence.collection_tool import CollectionTool

        from spireagent.artifact_contracts import Manifest
        from spireagent.source import source_identity

        from .collection_tool_registration import current_collection_tool
        from .local_recording_import import _labels, _selected_store, _sync
        from .p1_recording_storage import require_workspace_space

        with self.lock:
            state = self.current
            if not state or candidate["session_id"] != state.get("session_id"):
                reject("mixed_session_not_current")
            if state["status"] == "sealed":
                return self.status()
            if state["status"] in {"start_unknown", "recovery_required", "failed"}:
                reject("mixed_recovery_required")
            if self.models.process is not None and self.models.process.poll() is None:
                reject("original_runtime_still_running")
            if self.models.thread is not None and self.models.thread.is_alive():
                reject("original_model_operation_pending")
            directory = self.root / state["mixed_run_id"]
            originals = {
                run: self.models.directory / "agent-runs" / run for run in state["model_runs"]
            }
            startups = state.get("startups", {})
            for run, original in originals.items():
                if run not in startups:
                    reject("original_runtime_startup_missing")
                verified = verify_agent_session_run_evidence(original, startups[run])
                if verified.status != "pass":
                    reject("original_runtime_not_sealed")
            tool_path, release = current_collection_tool(self.app.config)
            tool = CollectionTool(tool_path, release)
            labels = _labels(self.app.config)
            native = Path(candidate["source_directory"])
            size = sum(
                p.stat().st_size
                for root in [native, *originals.values()]
                for p in root.rglob("*")
                if p.is_file()
            )
            require_workspace_space(self.app.config.state_dir, 3 * size, persistent_bytes=2 * size)
            staged = directory / "sealed-originals"
            if staged.exists():
                reject("prior_seal_attempt_inspect_original_result")
            staged.mkdir()
            try:
                bundle = tool.pack_p1_native(
                    native,
                    staged / "native",
                    labels["worker_id"],
                    labels["campaign_id"],
                    human_origin_attested=False,
                )
                verified_native = tool.verify_p1_native(bundle)
                if (
                    verified_native["recording"]["session_id"] != state["session_id"]
                    or verified_native["recording"]["p1_native_origin"] != "unknown"
                    or verified_native["recording"].get("mixed_run_id") != state["mixed_run_id"]
                ):
                    reject("native_binding_mismatch")
                for run, original in originals.items():
                    if any(p.is_symlink() for p in original.rglob("*")):
                        reject("runtime_original_symlink")
                    shutil.copytree(original, staged / "runtime" / run)
                    if (
                        verify_agent_session_run_evidence(
                            staged / "runtime" / run, startups[run]
                        ).status
                        != "pass"
                    ):
                        reject("runtime_copy_verification_failed")
                journal = [
                    json.loads(x) for x in (directory / "control.jsonl").read_bytes().splitlines()
                ]
                raw = bundle / "raw"
                index = environment_index(raw, originals, journal)
                index.update(
                    mixed_run_id=state["mixed_run_id"],
                    runtime_instance_id=state["runtime_instance_id"],
                    native_bundle=str(bundle.relative_to(staged)),
                    runtime_runs=state["model_runs"],
                )
                for name in ("binding.json", "control.jsonl"):
                    shutil.copyfile(directory / name, staged / name)
                (staged / "environment-index.json").write_bytes(json_bytes(index))
                files = {}
                for path in staged.rglob("*"):
                    if path.is_symlink():
                        reject("mixed_archive_symlink")
                    if path.is_file():
                        files[str(path.relative_to(staged))] = {
                            "bytes": path.stat().st_size,
                            "sha256": file_hash(path),
                        }
                close = {
                    "schema": "spireagent/mixed-run-close-1",
                    "recording_kind": "mixed_run",
                    "mixed_run_id": state["mixed_run_id"],
                    "session_id": state["session_id"],
                    "native_closed": True,
                    "all_runtime_originals_closed": True,
                    "files": files,
                    "training_admission": BLOCKED,
                    "score_eligible": False,
                }
                (staged / "mixed-close.json").write_bytes(json_bytes(close))
                archive = directory / "mixed-originals.tar.gz"
                with tarfile.open(archive, "x:gz") as output:
                    for path in sorted(staged.rglob("*")):
                        if path.is_file():
                            output.add(path, arcname=str(path.relative_to(staged)), recursive=False)
                # Verify archive bytes against the same bounded immutable inventory.
                with tarfile.open(archive, "r:gz") as source:
                    expected = files | {
                        "mixed-close.json": {
                            "bytes": (staged / "mixed-close.json").stat().st_size,
                            "sha256": hashlib.sha256(
                                (staged / "mixed-close.json").read_bytes()
                            ).hexdigest(),
                        }
                    }
                    seen = set()
                    for member in source:
                        if (
                            not member.isfile()
                            or member.name not in expected
                            or member.name in seen
                        ):
                            reject("archive_inventory_mismatch")
                        handle = source.extractfile(member)
                        assert handle is not None
                        digest = hashlib.sha256()
                        while part := handle.read(1024 * 1024):
                            digest.update(part)
                        if digest.hexdigest() != expected[member.name]["sha256"]:
                            reject("archive_hash_mismatch")
                        seen.add(member.name)
                    if seen != set(expected):
                        reject("archive_members_missing")
                store, registry = _selected_store(self.app.config, require_curation=False)
                with archive.open("rb") as source:
                    payload = store.put_payload("archive", source, "application/gzip")
                artifact = Manifest(
                    "evidence",
                    source_identity(ROOT),
                    payloads=(
                        payload,
                        store.put_bytes("index", json_bytes(index)),
                        store.put_bytes("close", json_bytes(close)),
                    ),
                    parameters=FrozenObject.of(
                        {
                            "schema": SCHEMA,
                            "recording_kind": "mixed_run",
                            "mixed_run_id": state["mixed_run_id"],
                            "session_id": state["session_id"],
                            "training_admission": BLOCKED,
                            "training_pool_eligible": False,
                            "score_eligible": False,
                            "human_demonstration_admission": "undecided",
                        }
                    ),
                )
                identity = store.publish(artifact)
                for p in artifact.payloads:
                    for _ in store.read_payload(p):
                        pass
                _sync(store, registry)
                state.update(
                    status="sealed",
                    artifact_id=identity,
                    native_operation_count=len(index["native_operations"]),
                    model_operation_count=len(index["model_operations"]),
                    unknown_operations=sum(
                        x["outcome_unknown"]
                        for x in index["native_operations"] + index["model_operations"]
                    ),
                    unknown_actor_operations=sum(
                        x["actor"] == "unknown"
                        for x in index["native_operations"] + index["model_operations"]
                    ),
                )
                self._save()
                return self.status()
            except BaseException:
                state.update(status="failed", error="seal_failed_originals_retained")
                self._save()
                raise
