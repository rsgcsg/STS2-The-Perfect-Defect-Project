"""A persistent slot ledger over the existing serial Reference dispatcher.

No gameplay, retry, model registry, or evidence format is implemented here.
The immutable plan and append-only slot events outlive any dispatcher process.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from spireagent.instance_lock import instance_lock
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.local import LocalBlobStore

from .developer import ProjectConfig
from .local_model_cli import native_episode_command
from .managed_retention import pipeline_configuration, retention_configuration
from .p1_recording_storage import NEXT_EPISODE_PEAK_BYTES, workspace_reserve_bytes
from .reference_suite import (
    MAX_REPORT_BYTES,
    SuiteLimits,
    WorkbenchReferenceClient,
    _read,
    load_manifest,
    run_suite,
    verify_completed_row,
)

SCHEMA = "spireagent/reference-evaluation-ledger-v1"


def reject(code: str) -> NoReturn:
    raise BoundaryError("evaluation_ledger", code)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def reference(path: Path) -> dict[str, Any]:
    raw = _read(path, max_bytes=MAX_REPORT_BYTES)
    return {"path": str(path.resolve()), "sha256": digest(raw), "bytes": len(raw)}


def read_reference(ref: dict[str, Any]) -> bytes:
    raw = _read(Path(ref["path"]), max_bytes=MAX_REPORT_BYTES)
    if len(raw) != ref["bytes"] or digest(raw) != ref["sha256"]:
        reject("original_reference_changed")
    return raw


def result_row(row: dict[str, Any], report_path: Path) -> dict[str, Any]:
    verify_completed_row(report_path, row)
    preservation = json.loads(_read(report_path.parent / row["preservation"]["path"]))
    readback = preservation.get("peer_readback") or {}
    if (readback.get("full_readback") is not True
            or readback.get("typed_verification") != "pass"):
        reject("full_typed_R2_receipt_required")
    observation = (row.get("agent_task_outcome") or {}).get("terminal_observation") or {}
    if observation.get("status") != "observed" or observation.get("result") not in {"win", "loss"}:
        reject("natural_terminal_required")
    return {"status": "finished", "run_id": row["run_id"],
            "natural_result": observation["result"],
            "max_observed_floor": (row.get("game_metrics") or {}).get("max_observed_floor"),
            "stored_evidence_id": row["stored_evidence_id"],
            "report": reference(report_path),
            "world_mutation_offered": row.get("world_mutation_offered") is True}


class EvaluationLedger:
    def __init__(self, directory: Path) -> None:
        if directory.is_symlink() or directory.resolve() != directory:
            reject("unsafe_ledger_directory")
        self.directory = directory
        self.store = LocalBlobStore(directory, create=False)
        self.plan = json.loads(self.store.get("plan.json"))
        if self.plan.get("schema") != SCHEMA:
            reject("unsupported_ledger")
        for ref in self.plan["originals"]:
            read_reference(ref)

    @classmethod
    def import_matrix(cls, directory: Path, matrix: Path, episodes: Path,
                      config: ProjectConfig) -> EvaluationLedger:
        if directory.exists() or directory.is_symlink():
            reject("ledger_already_exists")
        frozen = json.loads(_read(matrix))
        declared_conditions = frozen.get("conditions")
        if declared_conditions is not None and (
            not isinstance(declared_conditions, dict)
            or set(declared_conditions) != {"run_profile", "display_mode", "speed", "evidence_peer"}
            or declared_conditions["run_profile"] not in {"short", "extended", "continuous"}
            or declared_conditions["display_mode"] not in {"headless", "native-ui"}
            or declared_conditions["speed"] not in {"normal", "fast", "instant"}
            or not isinstance(declared_conditions["evidence_peer"], dict)
            or not declared_conditions["evidence_peer"]
        ):
            reject("invalid_declared_conditions")
        rows = list(csv.DictReader(io.StringIO(_read(episodes).decode("utf-8"))))
        expected = {}
        for group in frozen["groups"]:
            manifest_path = Path(group["manifest_path"])
            _, manifest = load_manifest(manifest_path, group["manifest_sha256"])
            if (group["character"] != manifest["character"]
                    or group["ascension"] != manifest["ascension"]):
                reject("frozen_group_changed")
            for ordinal, seed in enumerate(manifest["seeds"], 1):
                for step, selection in group["selections"].items():
                    expected[(group["character"], str(group["ascension"]), seed, step)] = {
                        "character": group["character"], "ascension": group["ascension"],
                        "seed": seed, "seed_ordinal": ordinal, "step": step,
                        "selection_id": selection, "manifest": reference(manifest_path)}
        if len(rows) != frozen["planned"] or len(expected) != len(rows):
            reject("full_denominator_required")
        reports: dict[str, Any] = {}
        conditions = None
        slots = []
        for original in rows:
            key = (original["character"], original["ascension"], original["seed"], original["step"])
            if key not in expected:
                reject("duplicate_or_unplanned_slot")
            slot = expected.pop(key)
            if (original["selection_id"] != slot["selection_id"]
                    or int(original["seed_ordinal"]) != slot["seed_ordinal"]):
                reject("original_slot_binding_changed")
            slot["slot_id"] = digest(json_bytes({"matrix": digest(_read(matrix)), **slot}))
            slot["imported_csv_row"] = original
            if original["status"] == "not_started":
                if original.get("run_id") or original.get("stored_evidence_id"):
                    reject("not_started_slot_has_run")
                path = (Path(original["original_report"])
                        if original.get("original_report") else None)
                if path is not None and path.is_file():
                    if str(path) not in reports:
                        reports[str(path)] = json.loads(_read(path, max_bytes=MAX_REPORT_BYTES))
                    matches = [r for r in reports[str(path)]["episodes"]
                               if r["seed"] == slot["seed"]
                               and r["selection_id"] == slot["selection_id"]]
                    if (len(matches) != 1 or matches[0]["status"] != "not_started"
                            or matches[0].get("world_mutation_offered")
                            or matches[0].get("actor_started")):
                        reject("not_started_original_has_offer")
                slot["status"] = "not_started"
            else:
                path = Path(original["original_report"])
                if str(path) not in reports:
                    reports[str(path)] = json.loads(_read(path, max_bytes=MAX_REPORT_BYTES))
                report = reports[str(path)]
                current = {k: report[k] for k in (
                    "run_profile", "display_mode", "speed", "evidence_peer")}
                if conditions is not None and conditions != current:
                    reject("mixed_original_conditions")
                conditions = current
                if report["manifest"]["sha256"] != slot["manifest"]["sha256"]:
                    reject("original_manifest_changed")
                matches = [r for r in report["episodes"]
                           if r["seed"] == slot["seed"]
                           and r["selection_id"] == slot["selection_id"]]
                if len(matches) != 1 or matches[0].get("run_id") != original["run_id"]:
                    reject("original_episode_changed")
                old = matches[0]
                if original["status"] == "finished":
                    slot.update(result_row(old, path))
                    if slot["natural_result"] != original["natural_result"]:
                        reject("original_result_changed")
                elif original["status"] in {"unknown", "interrupted_unknown"}:
                    slot.update(status="unknown", run_id=original["run_id"],
                                environment_ref=old.get("environment_ref"),
                                world_mutation_offered=old.get("world_mutation_offered") is True,
                                reason=original["reason"], report=reference(path))
                else:
                    reject("unresolved_import_requires_explicit_unknown")
            slots.append(slot)
        if conditions is None:
            if declared_conditions is None:
                reject("original_conditions_required")
            conditions = declared_conditions
        elif declared_conditions is not None and conditions != declared_conditions:
            reject("declared_conditions_differ_from_originals")
        plan = {"schema": SCHEMA, "created_at": datetime.now(UTC).isoformat(),
                "state_dir": str(config.state_dir),
                "originals": [reference(matrix), reference(episodes)],
                "conditions": conditions, "slots": slots, "planned": len(slots),
                "retry_unknown": False, "replacements": "not_implemented"}
        LocalBlobStore(directory).put_if_absent("plan.json", json_bytes(plan))
        return cls(directory)

    @classmethod
    def create(
        cls,
        directory: Path,
        manifest_path: Path,
        manifest_sha256: str,
        *,
        selections: dict[str, str],
        execution: Path,
    ) -> EvaluationLedger:
        """Freeze a new Managed denominator; never clone or reset an old ledger."""
        if directory.exists() or not selections or len(set(selections.values())) != len(selections):
            reject("new_directory_and_distinct_selections_required")
        _, manifest = load_manifest(manifest_path, manifest_sha256)
        if manifest["host"] != "Managed":
            reject("managed_manifest_required")
        raw = _read(execution)
        configuration = json.loads(raw)
        if configuration.get("schema") != "spireagent/managed-evaluation-execution-v1" or set(
            configuration["models"]
        ) != set(selections.values()):
            reject("execution_selection_mismatch")
        from .managed_evaluation import storage_configuration

        storage = storage_configuration(configuration)
        retention = retention_configuration(configuration)
        pipeline = pipeline_configuration(configuration)
        if retention["tier"] == "diagnostic" and storage["store"] == "s3":
            reject("diagnostic_requires_node_store")
        slots = []
        for ordinal, seed in enumerate(manifest["seeds"], 1):
            for step, selection in selections.items():
                slot = {
                    "character": manifest["character"],
                    "ascension": manifest["ascension"],
                    "seed": seed,
                    "seed_ordinal": ordinal,
                    "step": step,
                    "selection_id": selection,
                    "manifest": reference(manifest_path),
                    "host_kind": "managed",
                    "retention": retention,
                    "status": "not_started",
                }
                slot["slot_id"] = digest(json_bytes(slot))
                slots.append(slot)
        plan = {
            "schema": SCHEMA,
            "created_at": datetime.now(UTC).isoformat(),
            "host_kind": "managed",
            "execution": reference(execution),
            "storage": storage,
            "retention": retention,
            "pipeline": pipeline,
            "originals": [reference(manifest_path), reference(execution)],
            "conditions": {
                "run_profile": "extended",
                "display_mode": "headless",
                "speed": "instant",
                "evidence_peer": configuration["peer"],
            },
            "slots": slots,
            "planned": len(slots),
            "retry_unknown": False,
            "replacements": "not_implemented",
        }
        LocalBlobStore(directory).put_if_absent("plan.json", json_bytes(plan))
        return cls(directory)

    def _managed_result(self, row: dict[str, Any], path: Path) -> dict[str, Any]:
        declared = self.plan.get("retention")
        if declared is not None and row.get("retention") != declared:
            reject("attempt_retention_differs_from_plan")
        eligible = (declared or {}).get("score_eligible", True)
        if row.get("status") == "saved":
            from .managed_pipeline import verify_unscored_save

            verify_unscored_save(row, path)
        if row.get("status") == "finished" and eligible:
            value = result_row(row, path)
        else:
            value = {
                "status": row.get("status", "unknown"),
                "reason": row.get("reason"),
                "run_id": row.get("run_id"),
                "report": reference(path),
                "stored_evidence_id": row.get("stored_evidence_id"),
                "stored_failure_id": row.get("stored_failure_id"),
                "world_mutation_offered": row.get("world_mutation_offered") is True,
                "observed_result": ((row.get("agent_task_outcome") or {})
                                    .get("terminal_observation") or {}).get("result"),
                "trajectory_shard_id": row.get("trajectory_shard_id"),
                "preservation_route": row.get("preservation_route"),
            }
        return {
            **value,
            "host_kind": "managed",
            "elapsed_seconds": row.get("elapsed_seconds"),
            "cleanup_accepted": row.get("cleanup_accepted") is True,
            "score_eligible": eligible,
            "retention": row.get("retention"),
        }

    def run_managed(
        self,
        *,
        max_episodes: int = 200,
        workers: int = 1,
        limits: SuiteLimits | None = None,
        executor: Any = None,
        slot_ids: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """The calling command holds the ledger lock; only this thread appends events.

        Workers receive one already-reserved slot each and never touch the queue.
        Unknown/censored results belong to their own slots; other isolated workers
        continue. Unconfirmed cleanup stops admission because a process may still
        consume the bounded worker capacity. No unknown slot is offered again.
        """
        if (
            self.plan.get("host_kind") != "managed"
            or type(workers) is not int
            or not 1 <= workers <= 16
            or type(max_episodes) is not int
            or not 1 <= max_episodes <= 200
        ):
            reject("invalid_managed_dispatch")
        selected = None
        if slot_ids is not None:
            if (not isinstance(slot_ids, tuple) or not slot_ids
                    or any(not isinstance(identity, str) for identity in slot_ids)
                    or len(set(slot_ids)) != len(slot_ids)
                    or not set(slot_ids) <= {row["slot_id"] for row in self.plan["slots"]}):
                reject("invalid_managed_slot_selection")
            selected = set(slot_ids)
        self.reconcile_reports()
        if any(
            row["status"] in {"unknown", "reserved"} and not row.get("cleanup_accepted")
            for row in self.slots()
        ):
            return {"status": "stopped", "stop_reason": "unknown_cleanup_requires_owner_read"}
        configuration = json.loads(read_reference(self.plan["execution"]))
        configuration.update(self.plan.get("storage", {}))
        recoveries = [r for r in self.slots() if r["status"] in {
            "save_pending", "save_failed", "trajectory_pending"}]
        reserve = max(configuration.get("minimum_free_bytes", 0), workspace_reserve_bytes())
        if shutil.disk_usage(self.directory).free < reserve + (
                0 if recoveries else NEXT_EPISODE_PEAK_BYTES):
            return {"status": "stopped", "stop_reason": "workspace_capacity_insufficient"}
        limits = limits or SuiteLimits(150, 1800, 60, 86400, 1, 30)
        owns_executor = executor is None
        if executor is None:
            from .managed_pipeline import PipelinedManagedExecutor

            executor = PipelinedManagedExecutor(
                configuration,
                episode_seconds=limits.episode_seconds,
                cleanup_seconds=limits.cleanup_seconds,
            )
        if owns_executor:
            number = len(self.store.keys("managed-preparations/")) + 1
            self.store.put_if_absent(f"managed-preparations/{number:06d}.json",
                                     json_bytes(executor.preparation))
        pending_slots = [
            row for row in self.slots() if row["status"] == "not_started"
            and (selected is None or row["slot_id"] in selected)
        ][:max_episodes] if not recoveries else []
        pending = iter(pending_slots)
        active: dict[Any, tuple[dict[str, Any], Path, str]] = {}
        pipeline = pipeline_configuration(configuration)
        trajectory_wait: list[tuple[dict[str, Any], Path]] = []
        stop_reason = None
        began = time.monotonic()
        dispatched = 0
        finished = 0
        conditions = self.plan["conditions"]

        def episode(slot: dict[str, Any], attempt: Path) -> dict[str, Any]:
            def execute(row, manifest, artifacts, checkpoint):
                row["manifest_sha256"] = slot["manifest"]["sha256"]
                row["retention"] = self.plan.get(
                    "retention", retention_configuration(configuration))
                executor(row, manifest, artifacts, checkpoint)

            return run_suite(
                Path(slot["manifest"]["path"]),
                slot["manifest"]["sha256"],
                client=None,
                host_kind="managed",
                episode_executor=execute,
                run_dir=attempt,
                selection=slot["selection_id"],
                episode_keys=((slot["seed"], slot["selection_id"]),),
                run_profile=conditions["run_profile"],
                display_mode=conditions["display_mode"],
                speed=conditions["speed"],
                evidence_peer=conditions["evidence_peer"],
                limits=limits,
                max_episodes=1,
                should_stop=lambda: (self.directory / "STOP").exists(),
            )

        def close_executor() -> None:
            summary = executor.close()
            if summary is not None:
                (self.directory / "inference-pool-summary.json").write_bytes(json_bytes(summary))

        with (ExitStack() as lifecycle, ThreadPoolExecutor(max_workers=workers) as pool,
              ThreadPoolExecutor(max_workers=pipeline["save_workers"]) as saves):
            if owns_executor:
                lifecycle.callback(close_executor)
            # The same durable slot remains offered while only its save is resumed.
            for slot in recoveries:
                if not slot.get("cleanup_accepted"):
                    reject("save_recovery_requires_closed_host")
                path = Path(slot["attempt_report"])
                if slot["status"] == "trajectory_pending":
                    trajectory_wait.append((slot, path))
                else:
                    active[saves.submit(executor.preserve_report, path)] = (slot, path, "save")
            exhausted = False
            while active or trajectory_wait or not exhausted and stop_reason is None:
                if trajectory_wait and (len(trajectory_wait) >= 8
                        or not active):
                    group, trajectory_wait = trajectory_wait[:8], trajectory_wait[8:]
                    active[saves.submit(executor.finalize_trajectories,
                        [path for _, path in group])] = (
                            {"shard_slots": group}, group[0][1], "shard")
                executing = sum(stage == "execute" for _, _, stage in active.values())
                def unsettled(waiting=trajectory_wait):
                    return len(waiting) + sum(len(s.get("shard_slots", [s]))
                                                     for s, _, _ in active.values())

                while (executing < workers and unsettled() < pipeline["pending_limit"]
                       and not exhausted and stop_reason is None):
                    if dispatched == len(pending_slots):
                        exhausted = True
                        break
                    if ((self.directory / "STOP").exists()
                            or time.monotonic() - began >= limits.suite_seconds):
                        stop_reason = "stop_requested_or_suite_wall_limit"
                        break
                    # Each unsettled original owns its complete peak allowance,
                    # including queued saves. It cannot be promised twice.
                    required = reserve + NEXT_EPISODE_PEAK_BYTES * (unsettled() + 1)
                    if shutil.disk_usage(self.directory).free < required:
                        if not active:
                            stop_reason = "workspace_capacity_insufficient"
                        break
                    next_slot = next(pending, None)
                    if next_slot is None:
                        exhausted = True
                        break
                    slot = next_slot
                    number = len(self.store.keys("events/")) + 1
                    attempt = self.directory / "attempts" / f"{number:06d}"
                    self.event(slot["slot_id"], "reserved",
                               report_path=str(attempt / "report.json"))
                    active[pool.submit(episode, slot, attempt)] = (
                        slot, attempt / "report.json", "execute")
                    dispatched += 1
                    executing += 1
                if not active:
                    break
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    slot, path, stage = active.pop(future)
                    if stage == "shard":
                        try:
                            future.result()
                        except Exception as error:
                            stop_reason = ("trajectory_shard_save_failed:" + (
                                error.code if isinstance(error, BoundaryError)
                                else type(error).__name__))
                        # Read each durable report even after partial shard failure.
                        for member, member_path in slot["shard_slots"]:
                            report = json.loads(_read(member_path, max_bytes=MAX_REPORT_BYTES))
                            value = self._managed_result(report["episodes"][0], member_path)
                            self.event(member["slot_id"], "result", result=value)
                        continue
                    try:
                        result = future.result()
                        value = self._managed_result(result["episodes"][0], path)
                    except Exception as error:
                        value = {"status": "save_failed" if stage == "save" else "unknown",
                            "reason": error.code if isinstance(error, BoundaryError)
                            else type(error).__name__, "report": reference(path) if path.exists()
                            else None, "cleanup_accepted": stage == "save"}
                    self.event(slot["slot_id"], "result", result=value)
                    if value["status"] == "save_pending" and value.get("cleanup_accepted"):
                        active[saves.submit(executor.preserve_report, path)] = (slot, path, "save")
                    if value["status"] == "trajectory_pending":
                        trajectory_wait.append((slot, path))
                    finished += value["status"] == "finished"
                    print(json.dumps({"slot_id": slot["slot_id"],
                                      "seed_ordinal": slot["seed_ordinal"], **value}), flush=True)
                    if not value.get("cleanup_accepted"):
                        stop_reason = "unknown_cleanup_requires_owner_read"
                    elif value["status"] == "save_failed":
                        stop_reason = "save_failed_resume_save_only"
        return {
            "status": "stopped" if stop_reason else "completed",
            "stop_reason": stop_reason,
            "host_kind": "managed",
            "workers": workers,
            "pipeline": pipeline,
            "dispatched": dispatched,
            "new_finished": finished,
            "elapsed_seconds": time.monotonic() - began,
        }

    def event(self, slot_id: str, kind: str, **value: Any) -> None:
        number = len(self.store.keys("events/")) + 1
        self.store.put_if_absent(
            f"events/{number:06d}.json",
            json_bytes(
                {"slot_id": slot_id, "kind": kind, "at": datetime.now(UTC).isoformat(), **value}
            ),
        )

    def slots(self) -> list[dict[str, Any]]:
        rows = {r["slot_id"]: dict(r) for r in self.plan["slots"]}
        for key in self.store.keys("events/"):
            event = json.loads(self.store.get(key))
            row = rows[event["slot_id"]]
            kind = event["kind"]
            if kind == "reserved":
                if row["status"] != "not_started":
                    reject("slot_already_offered")
                row.update(status="reserved", attempt_report=event["report_path"])
            elif kind == "result":
                if row["status"] not in {"reserved", "save_pending", "save_failed",
                                        "trajectory_pending"}:
                    reject("slot_result_without_reservation")
                row.update(event["result"])
            elif kind == "release":
                if row["status"] != "unknown" or event["receipt"]["result"] != "unknown":
                    reject("release_requires_unknown")
                row["unknown_release"] = event["receipt"]
            elif kind == "cache":
                row["cache"] = event["receipt"]
            else:
                reject("unsupported_ledger_event")
        return list(rows.values())

    def seal_unknown(self, config: ProjectConfig, expected: dict[str, Any]) -> dict[str, Any]:
        rows = [r for r in self.slots() if r["status"] == "unknown"
                and r.get("run_id") == expected.get("expected_run_id")
                and r.get("environment_ref") == expected.get("expected_environment_ref")]
        if len(rows) != 1:
            reject("unknown_slot_identity_mismatch")
        receipt = native_episode_command(config, "seal-unknown", expected_release=expected,
                                        timeout_seconds=120)
        if (receipt.get("released") is not True or receipt.get("result") != "unknown"
                or receipt.get("expected") != expected):
            reject("unknown_release_unconfirmed")
        self.event(rows[0]["slot_id"], "release", receipt=receipt)
        return receipt

    def reconcile_reports(self) -> None:
        """Read an abandoned original attempt, never reoffer or recover a command."""
        for slot in self.slots():
            if slot["status"] in {"reserved", "save_pending", "save_failed",
                                  "trajectory_pending"}:
                path = Path(slot["attempt_report"])
                if path.exists():
                    report = json.loads(_read(path, max_bytes=MAX_REPORT_BYTES))
                    row = report["episodes"][0]
                    result = {"status": "unknown",
                              "reason": "abandoned_attempt_requires_owner_read",
                              "run_id": row.get("run_id"),
                              "environment_ref": row.get("environment_ref"),
                              "world_mutation_offered": row.get("world_mutation_offered") is True,
                              "report": reference(path)}
                    if (self.plan.get("host_kind") == "managed" and row.get("save_job")
                            and row.get("cleanup_accepted")):
                        from .managed_pipeline import verify_closed_save

                        verify_closed_save(row, path)
                        if (row.get("seed") != slot["seed"]
                                or row.get("selection_id") != slot["selection_id"]
                                or report["manifest"]["sha256"] != slot["manifest"]["sha256"]):
                            reject("pending_attempt_identity_changed")
                        if row.get("execution_status") is None:
                            row["execution_status"] = row["status"]
                            row["execution_reason"] = row.get("reason")
                            row["status"] = "save_pending"
                            from .reference_suite import atomic_json

                            atomic_json(path, report)
                        result = self._managed_result(row, path)
                    elif (self.plan.get("host_kind") == "managed"
                            and report.get("status") != "running"):
                        result = self._managed_result(row, path)
                    elif row.get("status") == "finished" and not row.get("preservation_error"):
                        try:
                            result = result_row(row, path)
                        except (OSError, ValueError, KeyError, TypeError) as error:
                            result["reason"] = (error.code if isinstance(error, BoundaryError)
                                                else type(error).__name__)
                    elif row.get("status") == "not_started" and report.get("status") != "running":
                        result = {"status": "not_started", "reason": report.get("stop_reason"),
                                  "report": reference(path)}
                else:
                    result = {"status": "unknown", "reason": "reserved_attempt_report_unavailable"}
                self.event(slot["slot_id"], "result", result=result)

    def run(self, config: ProjectConfig, *, max_episodes: int = 200,
            limits: SuiteLimits | None = None, cool: bool = True) -> dict[str, Any]:
        if str(config.state_dir) != self.plan["state_dir"]:
            reject("original_runtime_state_required")
        self.reconcile_reports()
        if any(r["status"] == "unknown" and not r.get("unknown_release") for r in self.slots()):
            return {"status": "stopped", "stop_reason": "unknown_requires_explicit_seal"}
        client = WorkbenchReferenceClient(config)
        resource = client.native_status(30)
        if resource.get("environment_ref") is not None:
            preview = native_episode_command(
                config,
                "unknown-status",
                expected_instance_id=client.identity["application_instance_id"],
                timeout_seconds=30,
            )
            if (preview.get("publication") or {}).get("requires_confirmation"):
                return {
                    "status": "stopped",
                    "stop_reason": "publication_unknown_requires_explicit_seal",
                }
            old_unknown = any(
                r["status"] == "unknown" and r.get("environment_ref") == resource["environment_ref"]
                for r in self.slots()
            )
            if old_unknown and preview.get("release") is None:
                return {
                    "status": "stopped",
                    "stop_reason": "unknown_requires_current_Application_confirmation",
                }
            if (
                resource.get("phase") not in {"idle", "closed", "failed"}
                and preview.get("release") is None
            ):
                return {"status": "stopped", "stop_reason": "native_environment_busy_or_unknown"}
        limits = limits or SuiteLimits(150, 1800, 180, 86400, 2, 30)
        conditions = self.plan["conditions"]
        count = 0
        for slot in self.slots():
            if slot["status"] != "not_started":
                continue
            if count >= max_episodes or (self.directory / "STOP").exists():
                return {"status": "stopped", "stop_reason": "stop_requested"}
            # Reserve once before invoking even the existing dispatcher's first
            # write. Process loss cannot return an uncertain slot to the queue.
            number = len(self.store.keys("events/")) + 1
            attempt = self.directory / "attempts" / f"{number:06d}"
            report_path = attempt / "report.json"
            self.event(slot["slot_id"], "reserved", report_path=str(report_path))
            result = run_suite(
                Path(slot["manifest"]["path"]),
                slot["manifest"]["sha256"],
                client=client,
                run_dir=attempt,
                selection="",
                selections={slot["step"]: slot["selection_id"]},
                episode_keys=((slot["seed"], slot["selection_id"]),),
                run_profile=conditions["run_profile"],
                display_mode=conditions["display_mode"],
                speed=conditions["speed"],
                evidence_peer=conditions["evidence_peer"],
                limits=limits,
                max_episodes=1,
                should_stop=lambda: (self.directory / "STOP").exists(),
            )
            row = result["episodes"][0]
            if row["status"] == "not_started":
                value = {
                    "status": "not_started",
                    "reason": result["stop_reason"],
                    "report": reference(report_path),
                }
            elif row["status"] == "finished" and not row.get("preservation_error"):
                value = result_row(row, report_path)
            else:
                value = {
                    "status": "unknown",
                    "run_id": row.get("run_id"),
                    "environment_ref": row.get("environment_ref"),
                    "reason": result["stop_reason"],
                    "world_mutation_offered": row.get("world_mutation_offered") is True,
                    "report": reference(report_path),
                }
            self.event(slot["slot_id"], "result", result=value)
            progress = {
                k: v
                for k, v in value.items()
                if k in {"status", "run_id", "natural_result", "reason"}
            }
            print(
                json.dumps(
                    {
                        "slot_id": slot["slot_id"],
                        "step": slot["step"],
                        "seed_ordinal": slot["seed_ordinal"],
                        **progress,
                    }
                ),
                flush=True,
            )
            if value["status"] != "finished" or result["status"] != "completed":
                return {"status": "stopped", "stop_reason": result["stop_reason"] or "unknown"}
            if cool:
                # The service owns credentials and exact current producer context.
                # The CLI never reads a credential file or opens a second Store.
                receipt = native_episode_command(
                    config,
                    "cool-evidence",
                    run_id=row["run_id"],
                    expected_instance_id=client.identity["application_instance_id"],
                    timeout_seconds=180,
                )
                self.event(slot["slot_id"], "cache", receipt=receipt)
            count += 1
        return {"status": "completed", "new_finished": count}

    def report(self) -> dict[str, Any]:
        rows = self.slots()
        groups = []
        for character, ascension, step in sorted(
            {(r["character"], r["ascension"], r["step"]) for r in rows}
        ):
            selected = [
                r
                for r in rows
                if (r["character"], r["ascension"], r["step"]) == (character, ascension, step)
            ]
            finished = [r for r in selected if r["status"] == "finished"]
            floors = [
                r["max_observed_floor"] for r in finished if r.get("max_observed_floor") is not None
            ]
            groups.append(
                {
                    "character": character,
                    "ascension": ascension,
                    "step": step,
                    "planned": len(selected),
                    "scored": len(finished),
                    "win": sum(r.get("natural_result") == "win" for r in finished),
                    "loss": sum(r.get("natural_result") == "loss" for r in finished),
                    "mean_floor": sum(floors) / len(floors) if floors else None,
                }
            )
        return {
            "schema": SCHEMA,
            "host_kind": self.plan.get("host_kind", "reference"),
            "pooling": "separate_host_conditions",
            "planned": len(rows),
            "censored": sum(r["status"] == "censored" for r in rows),
            "started": sum(r.get("world_mutation_offered") is True for r in rows),
            "win": sum(r.get("natural_result") == "win" for r in rows),
            "loss": sum(r.get("natural_result") == "loss" for r in rows),
            "unknown": sum(r["status"] == "unknown" for r in rows),
            "unknown_sealed": sum(bool(r.get("unknown_release")) for r in rows),
            "not_started": sum(r["status"] == "not_started" for r in rows),
            "reserved": sum(r["status"] == "reserved" for r in rows),
            "save_pending": sum(r["status"] == "save_pending" for r in rows),
            "save_failed": sum(r["status"] == "save_failed" for r in rows),
            "saved_unscored": sum(r["status"] == "saved" for r in rows),
            "trajectory_pending": sum(r["status"] == "trajectory_pending" for r in rows),
            "retention": self.plan.get("retention"),
            "replaced": 0,
            "R2_original_receipts": sum(bool(r.get("stored_evidence_id")) for r in rows),
            "R2_failure_receipts": sum(bool(r.get("stored_failure_id")) for r in rows),
            "trajectory_shards": len({r["trajectory_shard_id"] for r in rows
                                      if r.get("trajectory_shard_id")}),
            "groups": groups,
            "slots": rows,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("plan", "create", "run", "status", "report", "seal-unknown")
    )
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--episodes", type=Path)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-episodes", type=int, default=200)
    parser.add_argument("--host", choices=("reference", "managed"), default="reference")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--slot-id", action="append")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--selections", type=Path)
    parser.add_argument("--execution", type=Path)
    parser.add_argument("--cloud-environment", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.slot_id and (args.action != "run" or args.host != "managed"):
            reject("slot_selection_requires_managed_run")
        if args.cloud_environment:
            import os

            from .service_process import cloud_environment

            os.environ.update(cloud_environment(args.cloud_environment.resolve()))
        directory = args.ledger.resolve()
        lock = (
            nullcontext()
            if args.action in {"status", "report"}
            else instance_lock(directory.parent / (directory.name + ".lock"))
        )
        with lock:
            config = ProjectConfig.load(args.config) if args.config else None
            if args.action == "create":
                if (
                    args.host != "managed"
                    or args.manifest is None
                    or args.manifest_sha256 is None
                    or args.selections is None
                    or args.execution is None
                ):
                    reject("managed_manifest_execution_and_selections_required")
                ledger = EvaluationLedger.create(
                    directory,
                    args.manifest.resolve(),
                    args.manifest_sha256,
                    selections=json.loads(_read(args.selections)),
                    execution=args.execution.resolve(),
                )
                result = ledger.report()
            elif args.action == "plan":
                if config is None or args.matrix is None or args.episodes is None:
                    reject("matrix_episodes_and_config_required")
                ledger = EvaluationLedger.import_matrix(
                    directory, args.matrix.resolve(), args.episodes.resolve(), config
                )
                result = ledger.report()
            else:
                ledger = EvaluationLedger(directory)
                if args.action == "seal-unknown":
                    if config is None or args.expected is None:
                        reject("expected_identity_and_config_required")
                    result = ledger.seal_unknown(config, json.loads(_read(args.expected)))
                elif args.action == "run":
                    if args.host == "managed":
                        result = ledger.run_managed(
                            max_episodes=args.max_episodes, workers=args.workers,
                            slot_ids=tuple(args.slot_id) if args.slot_id else None,
                        )
                    elif (
                        config is None
                        or args.workers != 1
                        or ledger.plan.get("host_kind", "reference") != "reference"
                    ):
                        reject("config_and_bounded_new_episodes_required")
                    else:
                        result = ledger.run(config, max_episodes=args.max_episodes)
                else:
                    result = ledger.report()
            if args.output:
                from .developer import atomic_json

                atomic_json(args.output, result)
            if args.action == "status" or args.output:
                result = {k: v for k, v in result.items() if k not in {"slots", "groups"}}
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("status") != "stopped" else 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(
            json.dumps(
                {
                    "status": "stopped",
                    "error": error.code
                    if isinstance(error, BoundaryError)
                    else type(error).__name__,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
