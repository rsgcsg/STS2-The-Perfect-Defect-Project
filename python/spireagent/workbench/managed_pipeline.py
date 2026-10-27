"""Preservation extension at the existing executor's sealed-original seam.

The Host/Runtime execution, typed seal, outcome and close stay in their owner.
This hook persists the save inputs; the ledger starts saving only after close.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from sts2_platform_evidence import verify_agent_session_run_evidence

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.package_identity import validate_installed_package
from spireagent.storage.store import copy_artifact
from stpd.p1.trajectory import MAX_EPISODE_BYTES, extract_trajectory

from .global_evidence_store import (
    publish_global_evidence,
    publish_global_runtime,
    verify_global_artifact,
)
from .managed_evaluation import ManagedEpisodeExecutor, _phase
from .managed_failure_store import publish_failure, verify_failure
from .managed_retention import preservation_route, retention_configuration
from .managed_trajectory_store import MAX_EPISODES, MAX_SHARD_BYTES, publish_shard, read_shard
from .reference_suite import MAX_REPORT_BYTES, _Artifacts, _read, verify_completed_row


def _referenced(root: Path, ref: dict[str, Any], *,
                max_bytes: int = MAX_REPORT_BYTES) -> bytes:
    if Path(ref["path"]).name != ref["path"]:
        raise BoundaryError("managed_pipeline", "save_reference_path")
    raw = _read(root / ref["path"], max_bytes=max_bytes)
    if len(raw) != ref["bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise BoundaryError("managed_pipeline", "save_reference_changed")
    return raw


def verify_closed_save(row: dict[str, Any], path: Path) -> None:
    finish = json.loads(_referenced(path.parent, row["finish"]))
    if row.get("cleanup_accepted") is not True or finish.get("phase") != "closed":
        raise BoundaryError("managed_pipeline", "closed_host_receipt_required")
    job = json.loads(_referenced(path.parent, row["save_job"]))
    if (job.get("schema") != "spireagent/managed-save-job-1"
            or job.get("selection_id") != row.get("selection_id")
            or job.get("retention") != row.get("retention")):
        raise BoundaryError("managed_pipeline", "save_job_identity_changed")


def verify_unscored_save(row: dict[str, Any], path: Path) -> None:
    verify_closed_save(row, path)
    if row.get("retention", {}).get("score_eligible") is not False:
        raise BoundaryError("managed_pipeline", "unscored_retention_required")
    if row.get("trajectory_shard_id"):
        receipt = json.loads(_referenced(path.parent, row["trajectory_preservation"]))
        if (receipt.get("status") != "completed" or receipt.get("full_readback") is not True
                or receipt.get("artifact_id") != row["trajectory_shard_id"]
                or receipt.get("run_id") != row["run_id"]
                or receipt.get("trajectory_verification") != "pass"):
            raise BoundaryError("managed_pipeline", "trajectory_readback_required")
    elif row.get("preservation_route") == "full_local":
        receipt = json.loads(_referenced(path.parent, row["local_preservation"]))
        if (receipt.get("status") != "completed"
                or receipt.get("artifact_id") != row.get("local_evidence_id")
                or receipt.get("readback", {}).get("full_readback") is not True
                or receipt.get("readback", {}).get("typed_verification") != "pass"):
            raise BoundaryError("managed_pipeline", "local_readback_required")
    elif row.get("stored_evidence_id"):
        verify_completed_row(path, {**row, "status": "finished"})
        receipt = json.loads(_referenced(path.parent, row["preservation"]))
        if (receipt["peer_readback"].get("full_readback") is not True
                or receipt["peer_readback"].get("typed_verification") != "pass"):
            raise BoundaryError("managed_pipeline", "full_typed_peer_readback_required")
    else:
        raise BoundaryError("managed_pipeline", "unscored_save_receipt_required")


class PipelinedManagedExecutor(ManagedEpisodeExecutor):
    def __init__(self, configuration: dict[str, Any], *, episode_seconds: float,
                 cleanup_seconds: float) -> None:
        if (retention_configuration(configuration)["tier"] == "diagnostic"
                and configuration.get("store") == "s3"):
            raise BoundaryError("managed_pipeline", "diagnostic_requires_node_store")
        super().__init__(configuration, episode_seconds=episode_seconds,
                         cleanup_seconds=cleanup_seconds)

    def __call__(self, row: dict[str, Any], manifest: dict[str, Any], artifacts: Any,
                 checkpoint: Any) -> None:
        row["pipeline_started_at"] = time.time()
        row["retention"] = retention_configuration(self.configuration)
        row["scenario"] = {"character": manifest["character"], "ascension": manifest["ascension"]}
        super().__call__(row, manifest, artifacts, checkpoint)
        row["execution_status"], row["execution_reason"] = row["status"], row.get("reason")
        row["execution_wall_seconds"] = row["elapsed_seconds"]
        row["save_enqueued_at"] = time.time()
        if row.get("world_mutation_offered") and not row.get("save_job"):
            expected = {"global_runtime_directory": str(self.runtime),
                        "run_id": row.get("run_id")}
            self._write_job(row, artifacts, checkpoint, expected, mode="failure")
        if row.get("save_job") and row.get("cleanup_accepted"):
            row.update(status="save_pending", reason="closed_host_waiting_for_preservation")
        checkpoint()

    def _preserve(self, row: dict[str, Any], artifacts: Any, checkpoint: Any, run: Path,
                  expected: dict[str, Any], model: dict[str, Any], private: Path) -> None:
        # No new execution/close implementation: the base calls this only after
        # its typed seal. It then classifies the original and closes the Host.
        self._write_job(row, artifacts, checkpoint, expected, mode="sealed")

    def _write_job(self, row: dict[str, Any], artifacts: Any, checkpoint: Any,
                   expected: dict[str, Any], *, mode: str) -> None:
        model = self.configuration["models"][row["selection_id"]]
        row["save_job"] = artifacts.put("save-job.json", json_bytes({
            "schema": "spireagent/managed-save-job-1", "expected": expected,
            "run": f"runs/{row['run_id']}" if row.get("run_id") else None,
            "mode": mode, "selection_id": row["selection_id"],
            "episode": {"seed": row["seed"], "manifest_sha256": row["manifest_sha256"],
                        "scenario": row["scenario"]},
            "model_artifact_id": model["artifact_id"], "producer": self.producer.to_dict(),
            "retention": row["retention"], "peer": self.configuration["peer"],
            "store": self.preparation["store"]}))
        checkpoint()

    def preserve_report(self, path: Path) -> dict[str, Any]:
        report: dict[str, Any] = json.loads(_read(path, max_bytes=MAX_REPORT_BYTES))
        row = report["episodes"][0]
        artifacts = _Artifacts.reopen(path.parent)

        def checkpoint():
            artifacts.save(report)

        started = time.monotonic()
        row.setdefault("phase_seconds", {})["save_wait"] = max(
            0, time.time() - row.get("save_enqueued_at", time.time()))
        try:
            if (row.get("status") not in {"save_pending", "save_failed"}
                    or not row.get("cleanup_accepted")):
                raise BoundaryError("managed_pipeline", "closed_pending_original_required")
            job = json.loads(_referenced(path.parent, row["save_job"]))
            model = self.configuration["models"][row["selection_id"]]
            expected = job["expected"]
            if (job.get("schema") != "spireagent/managed-save-job-1"
                    or job["producer"] != self.producer.to_dict()
                    or job["peer"] != self.configuration["peer"]
                    or job["store"] != self.preparation["store"]
                    or job["retention"] != row["retention"]
                    or row["retention"] != retention_configuration(self.configuration)
                    or job["model_artifact_id"] != model["artifact_id"]
                    or job["selection_id"] != row["selection_id"]
                    or job["episode"] != {"seed": row["seed"],
                        "manifest_sha256": row["manifest_sha256"], "scenario": row["scenario"]}
                    or expected["run_id"] != row.get("run_id")
                    or expected["global_runtime_directory"] != str(self.runtime)
                    or job["run"] != (f"runs/{row['run_id']}" if row.get("run_id") else None)):
                raise BoundaryError("managed_pipeline", "save_job_identity_changed")
            private = path.parent / "private"
            if job.get("mode") == "failure":
                self._preserve_failure(row, artifacts, job, private)
                row["elapsed_seconds"] = time.time() - row.get("pipeline_started_at", time.time())
                row.setdefault("detail_seconds", {})["save_worker"] = time.monotonic() - started
                report.update(status="stopped", stop_reason=row["execution_reason"])
                checkpoint()
                return report
            if job.get("mode") != "sealed":
                raise BoundaryError("managed_pipeline", "unknown_save_mode")
            run = path.parent / job["run"]
            # Re-open the sealed original, not a cached qualification bit.
            with _phase(row, "save_verify"):
                verified = verify_agent_session_run_evidence(run, expected)
            if not verified.passed or verified.value is None:
                raise BoundaryError("managed_pipeline", "typed_original_verification_failed")
            route = preservation_route(row["retention"], row)
            row["preservation_route"] = route
            if route in {"full_peer", "trajectory_and_full_peer"}:
                super()._preserve(row, artifacts, checkpoint, run, expected, model, private)
            elif route == "full_local":
                with _phase(row, "preserve"):
                    runtime = publish_global_runtime(self.store, self.producer, expected)
                    stored = publish_global_evidence(self.store, run, self.producer,
                        expected=expected, model_artifact_id=model["artifact_id"],
                        runtime_artifact_id=runtime.artifact_id, scratch=private)
                    readback = verify_global_artifact(self.store, stored.artifact_id,
                        expected=expected, scratch=private, read_concurrency=self.read_concurrency)
                    row["local_evidence_id"] = stored.artifact_id
                    row["local_preservation"] = artifacts.put(
                        "local-preservation.json", json_bytes({
                        "status": "completed", "artifact_id": stored.artifact_id,
                        "readback": readback, "retention": "existing_7_day_closeout_rule"}))
            if route in {"trajectory", "trajectory_and_full_peer"}:
                with _phase(row, "trajectory_extract"):
                    identity = {"producer": self.producer.to_dict(),
                        "host_pin": self.configuration["host_pin"],
                        "runtime_pin": self.configuration["runtime_pin"],
                        "model_artifact_id": model["artifact_id"],
                        "model_sha256": expected["agent_artifact_sha256"],
                        "input_spec": verified.value.agent_manifest["input"]["input_spec"],
                        "seed": row["seed"], "character": row["scenario"]["character"],
                        "ascension": row["scenario"]["ascension"], "retention": row["retention"],
                        "behavior_policy": {"mode": "argmax", "temperature": None,
                                            "uniform_mix": 0},
                        "initial_progress_sha256": self.configuration["initial_progress"][
                            "progress_sha256"]}
                    trajectory = extract_trajectory(verified.value, identity=identity,
                        outcome=row["agent_task_outcome"], status=row["execution_status"],
                        reason=row["execution_reason"],
                        max_floor=(row.get("game_metrics") or {}).get("max_observed_floor"))
                    row["trajectory"] = artifacts.put("trajectory.json", json_bytes(trajectory))
                row.update(status="trajectory_pending", reason="waiting_for_shard_readback")
                row["trajectory_enqueued_at"] = time.time()
            else:
                row.update(status=(row["execution_status"] if row["retention"]["score_eligible"]
                                   or row["execution_status"] != "finished" else "saved"),
                           reason=row["execution_reason"])
            row.pop("save_error", None)
        except Exception as error:
            row.update(status="save_failed", reason="preservation_failed",
                       save_error=error.code if isinstance(error, BoundaryError)
                       else type(error).__name__)
        row.setdefault("detail_seconds", {})["save_worker"] = time.monotonic() - started
        row["elapsed_seconds"] = time.time() - row.get("pipeline_started_at", time.time())
        report.update(status="completed" if row["status"] in {"saved", "finished"}
                      else "stopped", stop_reason=row["reason"])
        checkpoint()
        return report

    def _preserve_failure(self, row: dict[str, Any], artifacts: Any, job: dict[str, Any],
                          private: Path) -> None:
        validate_installed_package(self.runtime.parent, self.configuration["runtime_pin"],
            required_paths=("dist/cli.js", "dist/global-replay.js", "dist/managed.js"))
        digest = hashlib.sha256()
        for module in sorted(self.runtime.glob("*.js")):
            digest.update(module.name.encode())
            digest.update(b"\0")
            digest.update(module.read_bytes())
            digest.update(b"\0")
        runtime = publish_global_runtime(self.store, self.producer, {
            "global_runtime_directory": str(self.runtime),
            "runtime_version": json.loads((self.runtime.parent / "package.json").read_bytes())[
                "version"], "runtime_code_sha256": digest.hexdigest()})
        facts = {"run_id": row.get("run_id"), "seed": row["seed"],
            "model_artifact_id": job["model_artifact_id"], "producer": job["producer"],
            "runtime_artifact_id": runtime.artifact_id,
            "host_pin": self.configuration["host_pin"],
            "runtime_pin": self.configuration["runtime_pin"], "retention": row["retention"],
            "initial_progress_sha256": self.configuration["initial_progress"]["progress_sha256"],
            "status": row["execution_status"], "reason": row["execution_reason"],
            "finish": json.loads(_referenced(artifacts.root, row["finish"]))}
        source = artifacts.root / (job["run"] or "runs")
        with _phase(row, "preserve"):
            stored = publish_failure(self.store, source, self.producer, facts, scratch=private)
            row["failure_pending"] = artifacts.put("failure-pending.json", json_bytes({
                "artifact_id": stored.artifact_id, "facts": facts}))
            # Same immutable artifact is safe to copy again after a lost receipt.
            copy_artifact(self.store, self.peer, stored.artifact_id, reuse_published_parents=True)
            readback = verify_failure(self.peer, stored.artifact_id, facts=facts, scratch=private)
            row["stored_failure_id"] = stored.artifact_id
            row["failure_preservation"] = artifacts.put("failure-preservation.json",
                json_bytes(readback))
        row.update(status=row["execution_status"], reason=row["execution_reason"],
                   preservation_route="full_peer_unqualified_failure")

    def finalize_trajectories(self, paths: list[Path]) -> list[tuple[Path, dict[str, Any]]]:
        """Pack up to eight episodes/256MiB; final partial shard is part of batch time."""
        completed: list[tuple[Path, dict[str, Any]]] = []
        group: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
        size = 0

        def flush():
            if not group:
                return
            began = time.time()
            manifest = publish_shard(self.store, [v for _, _, v in group], self.producer,
                                     scratch=group[0][0].parent / "private")
            copy_artifact(self.store, self.peer, manifest.artifact_id, reuse_published_parents=True)
            # Each invocation reads the complete new shard from the frozen peer.
            readback = list(read_shard(self.peer, manifest.artifact_id,
                                      scratch=group[0][0].parent / "private"))
            if readback != [v for _, _, v in group]:
                raise BoundaryError("managed_pipeline", "shard_readback_changed")
            for path, report, _ in group:
                row = report["episodes"][0]
                row.setdefault("phase_seconds", {})["shard_wait"] = max(
                    0, began - row.get("trajectory_enqueued_at", began))
                row["phase_seconds"]["shard_preserve_shared"] = time.time() - began
                row["elapsed_seconds"] = time.time() - row.get("pipeline_started_at", began)
                artifacts = _Artifacts.reopen(path.parent)
                row["trajectory_shard_id"] = manifest.artifact_id
                row["trajectory_preservation"] = artifacts.put("trajectory-preservation.json",
                    json_bytes({"status": "completed", "artifact_id": manifest.artifact_id,
                        "run_id": row["run_id"], "peer": self.configuration["peer"],
                        "full_readback": True, "trajectory_verification": "pass",
                        "compressed_bytes": manifest.payload("trajectory").size}))
                row.update(status="saved" if row["execution_status"] == "finished"
                           else row["execution_status"], reason=row["execution_reason"])
                report.update(status="completed" if row["status"] == "saved" else "stopped",
                              stop_reason=None if row["status"] == "saved" else row["reason"])
                artifacts.save(report)
                completed.append((path, report))
            group.clear()

        for path in paths:
            report = json.loads(_read(path, max_bytes=MAX_REPORT_BYTES))
            row = report["episodes"][0]
            if row["status"] != "trajectory_pending" or not row.get("cleanup_accepted"):
                raise BoundaryError("managed_pipeline", "closed_trajectory_required")
            raw = _referenced(path.parent, row["trajectory"], max_bytes=MAX_EPISODE_BYTES)
            if group and (len(group) == MAX_EPISODES or size + len(raw) > MAX_SHARD_BYTES):
                flush()
                size = 0
            group.append((path, report, json.loads(raw)))
            size += len(raw)
        flush()
        return completed
