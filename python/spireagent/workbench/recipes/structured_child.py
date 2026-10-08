"""Fixed private structured worker. Its application journal access is read-only.

The parent owns operation/control/selection writes. This child owns a distinct
OS lifecycle mutex until process exit and uses only immutable Store/Reporter
publication through the STPD authority seam.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
import time
import traceback
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes
from spireagent.source import source_identity
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.replaceable_file import read_replaceable_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT
from spireagent.workbench.instance_lock import instance_lock
from spireagent.workbench.trusted_recipes import (
    STRUCTURED_SCOPED_RECIPE,
    structured_recipe_run_schema,
    structured_recipe_scope,
)

EVENT_SCHEMA = "spireagent/structured-child-event-v1"
MAX_JOURNAL_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 16384
MAX_EVENTS = 262144
_LIFECYCLE_LOCK: Any = None


class ChildChannel:
    def __init__(self, operation_id: str, attempt_id: str) -> None:
        self.operation_id, self.attempt_id = operation_id, attempt_id
        self.sequence = 0

    def emit(self, kind: str, **details: Any) -> None:
        self.sequence += 1
        value = {"schema": EVENT_SCHEMA, "operation_id": self.operation_id,
                 "attempt_id": self.attempt_id, "sequence": self.sequence,
                 "kind": kind, "details": details}
        raw = json_bytes(value)
        if len(raw) > MAX_EVENT_BYTES or self.sequence > MAX_EVENTS:
            raise BoundaryError("structured_child", "channel_budget_exhausted")
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()


class ReadOnlyAttemptFence:
    def __init__(self, path: Path, operation_id: str, attempt_id: str,
                 remaining_seconds: float, store: Any) -> None:
        self.path, self.operation_id, self.attempt_id = path, operation_id, attempt_id
        self.remaining_seconds, self.store = remaining_seconds, store
        self.started_clock = time.monotonic()
        self.reservation_number = 0
        self.channel: ChildChannel | None = None

    def operation(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise BoundaryError("structured_child", "operation_storage_invalid")
        raw = read_replaceable_bytes(self.path)
        if not 0 < len(raw) <= MAX_JOURNAL_BYTES:
            raise BoundaryError("structured_child", "operation_storage_budget")
        value = decode_json(raw)
        if (not isinstance(value, dict)
                or value.get("schema") != "spireagent/local-training-operation-v3"
                or value.get("operation_id") != self.operation_id
                or value.get("attempt_id") != self.attempt_id
                or value.get("status") != "pending" or value.get("writer_terminal") is not False
                or value.get("worker_isolation") != "private_child-v1"
                or value.get("use_state") != "reserved"
                or value.get("child_lock_name") != "local-training-"+self.attempt_id+".child.lock"):
            raise BoundaryError("structured_child", "attempt_fence_lost")
        if value.get("_owner", [None])[-1] != str(self.store.blobs.root.resolve()):
            raise BoundaryError("structured_child", "store_owner_binding_mismatch")
        return value

    def assert_current(self, run_id: str | None, operation_id: str, attempt_id: str) -> None:
        if operation_id != self.operation_id or attempt_id != self.attempt_id:
            raise BoundaryError("structured_child", "attempt_fence_lost")
        value = self.operation()
        if run_id is not None and value.get("run_id") != run_id:
            raise BoundaryError("structured_child", "attempt_run_binding_mismatch")

    def authorize_resume(self, run_id: str, attempt_id: str, checkpoint_id: str) -> None:
        self.assert_current(run_id, self.operation_id, attempt_id)
        value = self.operation()
        if (value.get("mode") != "resume" or value.get("resume_checkpoint_id") != checkpoint_id
                or not value.get("attempts") or not value["attempts"][-1]["writer_terminal"]):
            raise BoundaryError("structured_child", "prior_writer_terminal_required")
        from spireagent.workbench.recipes.structured import verify_resume_checkpoint

        verify_resume_checkpoint(self.store, value, checkpoint_id)

    def requested_action(self) -> Literal["continue", "pause", "cancel"]:
        value = self.operation()
        action = value.get("requested_action")
        if action not in {"continue", "pause", "cancel"}:
            raise BoundaryError("structured_child", "invalid_control_action")
        if time.monotonic()-self.started_clock >= self.remaining_seconds:
            return "pause"
        return cast(Literal["continue", "pause", "cancel"], action)

    def reserve_artifact(self, role: str, size: int) -> None:
        self.assert_current(None, self.operation_id, self.attempt_id)
        if self.channel is None:
            raise BoundaryError("structured_child", "parent_channel_required")
        self.reservation_number += 1
        expected = {"attempt_id": self.attempt_id, "number": self.reservation_number,
                    "role": role, "size": size}
        self.channel.emit("reserve_artifact", number=self.reservation_number, role=role, size=size)
        deadline = time.monotonic()+5.0
        while True:
            value = self.operation()
            if value.get("artifact_reservation") == expected:
                return
            if time.monotonic() >= deadline:
                raise BoundaryError("structured_child", "artifact_reservation_ack_timeout")
            time.sleep(0.005)

    def wait_for_parent_run(self, run_id: str, input_id: str) -> None:
        deadline = time.monotonic()+5.0
        while True:
            value = self.operation()
            if value.get("run_id") == run_id and value.get("input_id") == input_id:
                return
            if time.monotonic() >= deadline:
                raise BoundaryError("structured_child", "prepared_run_ack_timeout")
            time.sleep(0.02)


class FencedStore:
    def __init__(self, store: Any, fence: ReadOnlyAttemptFence) -> None:
        self.store, self.fence = store, fence

    def __getattr__(self, name: str) -> Any:
        return getattr(self.store, name)

    def put_payload(self, *args: Any, **kwargs: Any) -> Any:
        self.fence.assert_current(None, self.fence.operation_id, self.fence.attempt_id)
        stream = args[1] if len(args) > 1 else kwargs["stream"]
        if not stream.seekable():
            raise BoundaryError("structured_child", "bounded_seekable_payload_required")
        start = stream.tell()
        stream.seek(0, 2)
        size = stream.tell()-start
        stream.seek(start)
        self.fence.reserve_artifact("payload", size)
        return self.store.put_payload(*args, **kwargs)

    def publish(self, *args: Any, **kwargs: Any) -> Any:
        self.fence.assert_current(None, self.fence.operation_id, self.fence.attempt_id)
        manifest = args[0] if args else kwargs["manifest"]
        self.fence.reserve_artifact("manifest", len(manifest.to_bytes()))
        return self.store.publish(*args, **kwargs)


class FencedSlots:
    def __init__(self, slots: Any, fence: ReadOnlyAttemptFence) -> None:
        self.slots, self.fence = slots, fence

    def __getattr__(self, name: str) -> Any:
        return getattr(self.slots, name)

    def put_if_absent(self, key: str, value: bytes) -> Any:
        self.fence.reserve_artifact("slot", len(value))
        return self.slots.put_if_absent(key, value)


class ChildReporter:
    def __init__(self, reporter: ObjectStoreRunReporter,
                 fence: ReadOnlyAttemptFence, channel: ChildChannel) -> None:
        self.reporter, self.fence, self.channel = reporter, fence, channel

    def __getattr__(self, name: str) -> Any:
        return getattr(self.reporter, name)

    def emit(self, event: Any) -> str:
        self.fence.assert_current(event.parent("run"), self.fence.operation_id,
                                  self.fence.attempt_id)
        identity = self.reporter.emit(event)
        self.channel.emit("event", event_id=identity)
        return identity

    def complete(self, result: Any) -> str:
        self.fence.assert_current(result.parent("run"), self.fence.operation_id,
                                  self.fence.attempt_id)
        return self.reporter.complete(result)


def run_child(args: argparse.Namespace, channel: ChildChannel) -> None:
    store = ManifestArtifactStore(LocalBlobStore(args.store, create=False))
    fence = ReadOnlyAttemptFence(args.operation_file, args.operation_id, args.attempt_id,
                                args.remaining_seconds, store)
    operation = fence.operation()
    fence.channel = channel
    scope = structured_recipe_scope(operation["recipe"])
    scoped = operation["recipe"] == STRUCTURED_SCOPED_RECIPE
    current_producer = source_identity(ROOT)
    if scoped and current_producer != Producer.decode(operation["attempt_producer"]):
        raise BoundaryError("structured_child", "current_attempt_producer_mismatch")
    channel.emit("started", **({"attempt_producer": current_producer.to_dict()} if scoped else {}))
    for name in ("TMPDIR", "TMP", "TEMP"):
        os.environ[name] = str(args.scratch_dir)
    tempfile.tempdir = str(args.scratch_dir)
    # Numerical imports happen only after immutable application admission and
    # the independent child lifecycle mutex are established.
    from stpd.fullrun.structured_sequences import parse_structured_dataset
    from stpd.structured_workload_contracts import (
        StructuredTrainingConfig,
        StructuredWorkloadRequest,
    )
    from stpd.workers.structured_execution import (
        execute_structured_workload,
        prepare_structured_workload,
    )

    fenced_store = FencedStore(store, fence)
    if operation["mode"] == "start":
        source = store.get_manifest(operation["dataset_id"])
        if "partition_schema" in source.parameters.value():
            from stpd.fullrun.protocol_source import verify_protocol_source_partition

            verified = verify_protocol_source_partition(store, source.artifact_id)
            if verified.split != "train":
                raise BoundaryError("structured_child", "train_only_source_required")
            dataset = verified.dataset
        else:
            payload = source.payload("source")
            dataset = parse_structured_dataset(b"".join(store.read_payload(payload)))
        if any(run.split != "train" for run in dataset.runs):
            raise BoundaryError("structured_child", "train_only_source_required")
        run = prepare_structured_workload(
            fenced_store, dataset, current_producer,
            StructuredTrainingConfig(**{key: item for key, item in
                                        operation["request"]["config"].items()
                                        if key != "checkpoint_every_boundaries"}),
            operation_id=args.operation_id, source_id=source.artifact_id, code_scope=scope)
        input_id = run.parent("training_input")
        channel.emit("prepared", run_id=run.artifact_id, input_id=input_id)
        fence.wait_for_parent_run(run.artifact_id, input_id)
    else:
        run = store.get_manifest(operation["run_id"])
    if (run.parameters.value().get("schema")
            != structured_recipe_run_schema(operation["recipe"])):
        raise BoundaryError("structured_child", "recipe_run_scope_mismatch")
    request = StructuredWorkloadRequest(
        run.artifact_id, run.parent("training_input"), args.operation_id, args.attempt_id,
        mode=operation["mode"],
        resume_checkpoint_id=(operation.get("resume_checkpoint_id")
                              if operation["mode"] == "resume" else None),
        checkpoint_every_boundaries=operation["request"]["config"]["checkpoint_every_boundaries"])
    reporter = ChildReporter(ObjectStoreRunReporter(fenced_store, FencedSlots(store.blobs, fence)),
                             fence, channel)
    result = execute_structured_workload(fenced_store, reporter, request, run.producer,
                                        authority=fence, control=fence,
                                        **({"attempt_producer": current_producer}
                                           if scoped else {}))
    channel.emit("terminal", **asdict(result))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fixed trusted private structured worker")
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--operation-file", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--child-lock", type=Path, required=True)
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--remaining-seconds", type=float, required=True)
    args = parser.parse_args(argv)
    digest(args.operation_id, "structured_child.operation", length=32)
    digest(args.attempt_id, "structured_child.attempt", length=32)
    if (not math.isfinite(args.remaining_seconds) or args.remaining_seconds <= 0
            or args.child_lock.parent != args.operation_file.parent
            or args.child_lock.name != "local-training-"+args.attempt_id+".child.lock"
            or args.child_lock.is_symlink()
            or args.scratch_dir.parent != args.operation_file.parent
            or args.scratch_dir.name != "local-training-"+args.attempt_id+".scratch"
            or args.scratch_dir.is_symlink() or not args.scratch_dir.is_dir()):
        raise BoundaryError("structured_child", "invalid_ownership_or_budget")
    channel = ChildChannel(args.operation_id, args.attempt_id)
    global _LIFECYCLE_LOCK
    try:
        # Keep the owner handle reachable through interpreter/process shutdown.
        # No parent may adopt this writer while an exception/report is in flight.
        _LIFECYCLE_LOCK = instance_lock(args.child_lock, create=False)
        _LIFECYCLE_LOCK.__enter__()
        run_child(args, channel)
        return 0
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        code = error.code if isinstance(error, BoundaryError) else "structured_worker_runtime_error"
        with suppress(OSError, ValueError, BoundaryError):
            channel.emit("error", error_code=code,
                         exception_type=type(error).__module__+"."+type(error).__qualname__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
