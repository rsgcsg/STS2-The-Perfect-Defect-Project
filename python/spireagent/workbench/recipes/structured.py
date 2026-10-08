"""Trusted structured workload composition over existing Store/Reporter/curation.

STPD verifies numeric checkpoints/results. The application owns source admission,
use reservations, attempt publication fences, control and cumulative wall budget.
"""

from __future__ import annotations

import math
import os
import sys
import time
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json, digest
from spireagent.source import source_identity
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ROOT
from spireagent.workbench.research_process import private_child
from spireagent.workbench.training_scratch import retained_scratch_bytes

_private_child = private_child


def verify_resume_checkpoint(store: Any, operation: dict[str, Any], checkpoint_id: Any) -> None:
    checkpoint = store.get_manifest(checkpoint_id)
    run = store.get_manifest(operation["run_id"])
    info = checkpoint.parameters.value()
    if (checkpoint.kind != "checkpoint" or checkpoint.producer != run.producer
            or checkpoint.parent("run") != run.artifact_id
            or checkpoint.parent("training_input") != operation["input_id"]
            or info.get("schema") != "stpd/structured-m2-training-checkpoint-v2"):
        raise BoundaryError("local_training", "checkpoint_run_identity_mismatch")
    prior = {item["attempt_id"] for item in operation["attempts"]
             if item["writer_terminal"]}
    if operation["writer_terminal"]:
        prior.add(operation["attempt_id"])
    if info.get("attempt") not in prior:
        raise BoundaryError("local_training", "checkpoint_writer_terminal_required")
    # A published checkpoint absent its owning event is an uncertain publication,
    # not a selectable recovery point.
    events = ObjectStoreRunReporter(store, store.blobs).events(run.artifact_id)
    if not any(event.parameters.value().get("kind") == "checkpoint"
               and event.parameters.value().get("attempt") == info["attempt"]
               and event.parameters.value().get("details", {}).get("checkpoint_id")
               == checkpoint_id for event in events):
        raise BoundaryError("local_training", "checkpoint_event_required")


class StructuredRecipeAdapter:
    def preflight(self, store: Any, owner: Any, source_id: str) -> Any:
        from stpd.fullrun.structured_sequences import (
            MAX_SOURCE_BYTES,
            SOURCE_SCHEMA,
            parse_structured_dataset,
        )

        source = store.get_manifest(source_id)
        info = source.parameters.value()
        if "partition_schema" in info:
            from stpd.fullrun.protocol_source import verify_protocol_source_partition

            verified = verify_protocol_source_partition(store, source_id)
            if (verified.split != "train" or
                    any(run.split != "train" for run in verified.dataset.runs)):
                raise BoundaryError("local_training", "train_only_source_required")
            if owner.ledger.dataset(source_id) != ("training", set(verified.runs)):
                raise BoundaryError("local_training", "training_claim_mismatch")
            return verified.dataset
        payload = source.payload("source")
        if (source.kind != "dataset" or info.get("schema") != SOURCE_SCHEMA
                or payload.size > MAX_SOURCE_BYTES):
            raise BoundaryError("local_training", "structured_source_manifest_required")
        dataset = parse_structured_dataset(b"".join(store.read_payload(payload)))
        if (dataset.source_sha256 != payload.sha256
                or info.get("source_sha256") != dataset.source_sha256
                or info.get("source_kind") != dataset.source_kind):
            raise BoundaryError("local_training", "structured_source_identity_mismatch")
        # Synthetic fixtures exercise this actual application path without
        # granting Agent/Human provenance or runtime qualification. A production
        # Agent source needs its owning typed verifier and immutable report join.
        if dataset.source_kind != "synthetic" or info.get("qualification") != "synthetic_fixture":
            raise BoundaryError("local_training", "structured_source_verifier_required")
        if any(run.split != "train" for run in dataset.runs):
            raise BoundaryError("local_training", "train_only_source_required")
        runs = {run.run_id for run in dataset.runs}
        if owner.ledger.dataset(source_id) != ("training", runs):
            raise BoundaryError("local_training", "training_claim_mismatch")
        if (not owner.ledger.exact_source_ready(source_id)
                or owner.ledger.source_runs(source_id) != runs):
            raise BoundaryError("local_training", "source_index_incomplete")
        return dataset

    def execute(self, service: Any, path: Any, identity: str, owner: Any,
                store: Any, mark_started: Any) -> None:
        operation = service._read(path, owner.identity)
        dataset = self.preflight(store, owner, operation["dataset_id"])
        runs = {run.run_id for run in dataset.runs}
        source_id, attempt_id = operation["dataset_id"], operation["attempt_id"]
        protocol_source = "partition_schema" in store.get_manifest(source_id).parameters.value()
        if protocol_source:
            if operation["mode"] == "start":
                owner.record_verified_protocol_training_use(store, source_id, identity)
            owner.require_verified_protocol_training_use(store, source_id, identity)
        else:
            if operation["mode"] == "start":
                owner.ledger.use_source(source_id, "training", identity)
                owner.ledger.use(runs, "training", identity)
            owner.ledger.require_training_use(source_id, {source_id}, runs, identity)

        def current() -> dict[str, Any]:
            value = service._read(path, owner.identity)
            if (value.get("operation_id") != identity or value.get("attempt_id") != attempt_id
                    or value.get("status") != "pending" or value.get("writer_terminal")
                    or service._active_attempt != attempt_id):
                raise BoundaryError("local_training", "attempt_fence_lost")
            return value

        with service._lock:
            current()
            service._advance(path, identity, use_state="reserved", stage="preparing_run")
        sequence = 0
        handshake = False
        terminal: dict[str, Any] | None = None
        failure: dict[str, str] | None = None

        def message(raw: bytes) -> None:
            nonlocal sequence, handshake, terminal, failure
            value = decode_json(raw)
            if (not isinstance(value, dict) or set(value) != {"schema", "operation_id",
                    "attempt_id", "sequence", "kind", "details"}
                    or value["schema"] != "spireagent/structured-child-event-v1"
                    or value["operation_id"] != identity or value["attempt_id"] != attempt_id
                    or type(value["sequence"]) is not int or value["sequence"] != sequence+1
                    or not isinstance(value["details"], dict)):
                raise BoundaryError("local_training", "child_channel_identity_mismatch")
            sequence += 1
            details, kind = value["details"], value["kind"]
            with service._lock:
                recorded = current()
                if kind == "started":
                    if sequence != 1 or details or handshake:
                        raise BoundaryError("local_training", "child_handshake_invalid")
                    handshake = True
                    service._advance(path, identity, child_handshake=True)
                elif kind == "reserve_artifact":
                    if (not handshake or set(details) != {"number", "size", "role"}
                            or type(details["number"]) is not int
                            or type(details["size"]) is not int or details["size"] < 0
                            or details["role"] not in {"payload", "manifest", "slot"}):
                        raise BoundaryError("local_training", "child_reservation_invalid")
                    prior = recorded.get("artifact_reservation", {})
                    expected = (prior.get("number", 0)+1
                                if prior.get("attempt_id") == attempt_id else 1)
                    used = recorded.get("artifact_reserved_bytes", 0)+details["size"]
                    if details["number"] != expected:
                        raise BoundaryError("local_training", "child_reservation_invalid")
                    if used > recorded["request"]["limits"]["scratch_bytes"]:
                        raise BoundaryError("local_training", "artifact_budget_exhausted")
                    service._advance(path, identity, artifact_reserved_bytes=used,
                                     artifact_reservation={"attempt_id": attempt_id, **details})
                elif kind == "prepared":
                    if not handshake or set(details) != {"run_id", "input_id"}:
                        raise BoundaryError("local_training", "child_preparation_invalid")
                    run_id = digest(details["run_id"], "local_training.run_id")
                    input_id = digest(details["input_id"], "local_training.input_id")
                    run, training = store.get_manifest(run_id), store.get_manifest(input_id)
                    if (recorded["mode"] != "start" or recorded.get("run_id")
                            or run.kind != "run" or run.producer != source_identity(ROOT)
                            or run.parameters.value().get("schema") != "stpd/structured-m2-run-v2"
                            or run.parameters.value().get("operation_id") != identity
                            or run.parent("training_input") != input_id
                            or training.kind != "training_input"
                            or training.producer != run.producer
                            or training.parent("source") != source_id):
                        raise BoundaryError("local_training", "child_preparation_invalid")
                    service._advance(path, identity, run_id=run_id, input_id=input_id,
                                     stage="training")
                elif kind == "event":
                    if not handshake or set(details) != {"event_id"} or not recorded.get("run_id"):
                        raise BoundaryError("local_training", "child_event_invalid")
                    event = store.get_manifest(digest(
                        details["event_id"], "local_training.event_id"))
                    info = event.parameters.value()
                    run = store.get_manifest(recorded["run_id"])
                    if (event.kind != "run_event" or event.producer != run.producer
                            or event.parent("run") != run.artifact_id
                            or info.get("schema") != "stpd/run-event-v1"
                            or info.get("attempt") != attempt_id
                            or type(info.get("step")) is not int
                            or info["step"] <= recorded.get("_event_step", 0)):
                        raise BoundaryError("local_training", "child_event_invalid")
                    update: dict[str, Any] = {"_event_step": info["step"]}
                    event_details = info.get("details", {})
                    if info.get("kind") == "checkpoint":
                        cp_id = digest(event_details.get("checkpoint_id"),
                                       "local_training.checkpoint")
                        checkpoint = store.get_manifest(cp_id)
                        if (checkpoint.kind != "checkpoint" or checkpoint.producer != run.producer
                                or checkpoint.parent("run") != run.artifact_id
                                or checkpoint.parent("training_input") != recorded["input_id"]
                                or checkpoint.parameters.value().get("attempt") != attempt_id):
                            raise BoundaryError("local_training", "child_checkpoint_invalid")
                        update["checkpoint_id"] = cp_id
                    if "optimizer_updates" in event_details:
                        updates = event_details["optimizer_updates"]
                        if type(updates) is not int or not 0 <= updates <= 10000:
                            raise BoundaryError("local_training", "child_progress_invalid")
                        update.update(last_progress_at=time.time(), progress={
                            "unit": "optimizer_update", "completed": updates, "total": None})
                    service._advance(path, identity, **update)
                elif kind == "terminal":
                    keys = {"state", "run_id", "attempt_id", "checkpoint_id", "result_id",
                            "model_id", "event_step", "optimizer_updates", "attempt_seconds"}
                    if (not handshake or terminal is not None or set(details) != keys
                            or details["state"] not in {"completed", "paused", "cancelled"}
                            or details["run_id"] != recorded.get("run_id")
                            or details["attempt_id"] != attempt_id
                            or type(details["event_step"]) is not int
                            or details["event_step"] < recorded.get("_event_step", 0)
                            or type(details["optimizer_updates"]) is not int
                            or not 0 <= details["optimizer_updates"] <= 10000
                            or type(details["attempt_seconds"]) not in {float, int}
                            or not math.isfinite(details["attempt_seconds"])
                            or details["attempt_seconds"] < 0):
                        raise BoundaryError("local_training", "child_terminal_invalid")
                    digest(details["checkpoint_id"], "local_training.checkpoint_id")
                    for key in ("result_id", "model_id"):
                        if details["state"] == "completed":
                            digest(details[key], "local_training."+key)
                        elif details[key] is not None:
                            raise BoundaryError("local_training", "child_terminal_invalid")
                    terminal = details
                elif kind == "error":
                    if (set(details) != {"error_code", "exception_type"}
                            or any(not isinstance(item, str) or not 0 < len(item) <= 256
                                   for item in details.values())):
                        raise BoundaryError("local_training", "child_error_invalid")
                    failure = details
                    service._child_failure_diagnostic = dict(details)
                else:
                    raise BoundaryError("local_training", "child_channel_kind_invalid")

        def spawned() -> None:
            mark_started()
            with service._lock:
                current()
                service._advance(path, identity, child_spawned=True)

        def stop_requested() -> bool:
            with service._lock:
                value = current()
                if (retained_scratch_bytes(path, value) >
                        value["request"]["limits"]["scratch_bytes"]):
                    raise BoundaryError("local_training", "scratch_budget_exhausted")
                if value["requested_action"] != "cancel":
                    return False
                started_cancel = service._cancel_requested_monotonic
                if started_cancel is None:
                    service._cancel_requested_monotonic = time.monotonic()
                    return False
                return time.monotonic()-started_cancel >= 1.0

        def exited(exit_code: int, forced: bool, seconds: float) -> None:
            receipt = {"attempt_id": attempt_id, "exit_code": exit_code,
                       "forced": forced, "elapsed_seconds": seconds, "ended_at": time.time()}
            service._active_child_exit = receipt
            with service._lock:
                current()
                service._advance(path, identity, child_exit=receipt)

        started = service._attempt_started_monotonic
        if started is None:
            raise BoundaryError("local_training", "attempt_clock_required")
        remaining = (operation["request"]["limits"]["wall_seconds"]-
                     operation["elapsed_seconds"]-(time.monotonic()-started))
        if remaining <= 0:
            raise BoundaryError("local_training", "cumulative_budget_exhausted")
        command = [sys.executable, "-m", "spireagent.workbench.recipes.structured_child",
                   "--store", str(owner.store_dir), "--operation-file", str(path),
                   "--operation-id", identity, "--attempt-id", attempt_id,
                   "--child-lock", str(path.parent/operation["child_lock_name"]),
                   "--scratch-dir", str(path.parent/operation["scratch_name"]),
                   "--remaining-seconds", str(remaining)]
        environment = dict(os.environ)
        for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)
        log = path.parent/("local-training-"+identity+"-"+attempt_id+".log")
        exit_code, _ = _private_child(command, log, environment, on_started=spawned,
                                     timeout_seconds=remaining, on_stdout_line=message,
                                     on_exited=exited, stop_requested=stop_requested)
        if exit_code:
            raise BoundaryError("local_training", failure["error_code"] if failure else
                                "structured_training_process_failed")
        if terminal is None:
            raise BoundaryError("local_training", "child_terminal_missing")
        with service._lock:
            current()
            completion = {"checkpoint_id": terminal["checkpoint_id"],
                          "progress": {"unit": "optimizer_update",
                                       "completed": terminal["optimizer_updates"], "total": None}}
            if terminal["state"] == "completed":
                completion.update(result_id=terminal["result_id"], model_id=terminal["model_id"],
                                  evaluation_status="not_run", stage="completed")
            service._finish_attempt(path, owner, identity, expected_attempt_id=attempt_id,
                                    status=terminal["state"], **completion)
        if terminal["state"] == "completed":
            from spireagent.storage.registry import SQLiteRegistry, sync_registry

            _, _, registry_path = service._selected()
            sync_registry(store, SQLiteRegistry(registry_path))
