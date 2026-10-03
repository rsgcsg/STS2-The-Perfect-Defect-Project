"""Provider-neutral, bounded typed bytes transport for one public M2 attempt.

Source manifests are declared for lineage only. Their payloads are never copied,
read in the projection, or made available to the numerical worker. The caller
owns the single-writer lease, independent runtime pin, and ambiguous delivery.
"""

from __future__ import annotations

import hashlib
import io
import math
import struct
import tempfile
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Payload, Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields
from spireagent.storage.blobs import BlobStore
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ArtifactStore, ManifestArtifactStore

from .checkpoint_codec import decode_checkpoint
from .public_m2_preflight import (
    PublicM2Preflight,
    validate_public_m2_checkpoint,
    validate_public_m2_export,
)
from .public_m2_run import (
    CHECKPOINT_SCHEMA,
    EVALUATION_SCHEMA,
    MODEL_SCHEMA,
    RESULT_SCHEMA,
    STAGE_SCHEMA,
    _load_run,
    execute_public_m2_run,
)
from .worker import WorkerResult

REQUEST_SCHEMA = "stpd/public-m2-remote-request-v1"
RESULT_SCHEMA_REMOTE = "stpd/public-m2-remote-result-v1"
MAX_REQUEST_BYTES = 256 * 1024 * 1024
MAX_RESULT_BYTES = 128 * 1024 * 1024
_MAX_HEADER_BYTES = 32 * 1024 * 1024
_REQUEST_MAGIC = b"STPD-M2-REQUEST\x00"
_RESULT_MAGIC = b"STPD-M2-RESULT\x00"
_SOURCE_ROLES = ("dataset", "allocation", "source_view")
_DELTA_KINDS = frozenset({
    "run_event", "checkpoint", "model", "offline_evaluation", "analysis", "run_result",
})
_STAGE = "public_m2_remote"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pack(magic: bytes, header: dict[str, Any], blobs: dict[str, bytes],
          maximum: int) -> bytes:
    if any(digest(key, _STAGE) != _sha(value) for key, value in blobs.items()):
        raise BoundaryError(_STAGE, "blob_integrity_mismatch")
    inventory = [{"sha256": key, "size": len(blobs[key])} for key in sorted(blobs)]
    framed = json_bytes({**header, "blobs": inventory})
    if len(framed) > _MAX_HEADER_BYTES:
        raise BoundaryError(_STAGE, "header_size_limit")
    if len(magic) + 4 + len(framed) + sum(map(len, blobs.values())) > maximum:
        raise BoundaryError(_STAGE, "frame_size_limit")
    raw = magic + struct.pack(">I", len(framed)) + framed + b"".join(
        blobs[key] for key in sorted(blobs)
    )
    return raw


def _unpack(raw: bytes, magic: bytes, maximum: int) -> tuple[dict[str, Any], dict[str, bytes]]:
    if (type(raw) is not bytes or len(raw) > maximum or not raw.startswith(magic)
            or len(raw) < len(magic) + 4):
        raise BoundaryError(_STAGE, "invalid_frame")
    header_size = struct.unpack(">I", raw[len(magic):len(magic) + 4])[0]
    offset = len(magic) + 4
    if header_size > _MAX_HEADER_BYTES or offset + header_size > len(raw):
        raise BoundaryError(_STAGE, "header_size_limit")
    header_raw = raw[offset:offset + header_size]
    header = decode_json(header_raw)
    if not isinstance(header, dict) or json_bytes(header) != header_raw:
        raise BoundaryError(_STAGE, "noncanonical_header")
    cursor = offset + header_size
    inventory = header.get("blobs")
    if not isinstance(inventory, list):
        raise BoundaryError(_STAGE, "invalid_blob_inventory")
    blobs: dict[str, bytes] = {}
    for item in inventory:
        entry = object_fields(item, {"sha256", "size"}, _STAGE)
        key = digest(entry["sha256"], _STAGE)
        size = entry["size"]
        if type(size) is not int or size < 0 or key in blobs or cursor + size > len(raw):
            raise BoundaryError(_STAGE, "invalid_blob_inventory")
        data = raw[cursor:cursor + size]
        if _sha(data) != key:
            raise BoundaryError(_STAGE, "blob_integrity_mismatch")
        blobs[key] = data
        cursor += size
    if cursor != len(raw) or list(blobs) != sorted(blobs):
        raise BoundaryError(_STAGE, "noncanonical_frame")
    return header, blobs


def _manifest_entry(manifest: Manifest) -> dict[str, str]:
    return {"id": manifest.artifact_id, "raw": manifest.to_bytes().decode("utf-8")}


def _manifests(value: object) -> dict[str, Manifest]:
    if not isinstance(value, list):
        raise BoundaryError(_STAGE, "invalid_manifest_inventory")
    found: dict[str, Manifest] = {}
    for item in value:
        entry = object_fields(item, {"id", "raw"}, _STAGE)
        identity = digest(entry["id"], _STAGE)
        if type(entry["raw"]) is not str or identity in found:
            raise BoundaryError(_STAGE, "invalid_manifest_inventory")
        manifest = Manifest.from_bytes(entry["raw"].encode("utf-8"), identity)
        if manifest.to_bytes().decode("utf-8") != entry["raw"]:
            raise BoundaryError(_STAGE, "noncanonical_manifest")
        found[identity] = manifest
    if list(found) != sorted(found):
        raise BoundaryError(_STAGE, "noncanonical_manifest_inventory")
    return found


def _payload_bytes(store: ArtifactStore, payload: Payload, maximum: int) -> bytes:
    if payload.size > maximum:
        raise BoundaryError(_STAGE, "payload_size_limit")
    raw = b"".join(store.read_payload(payload))
    if len(raw) != payload.size or _sha(raw) != payload.sha256:
        raise BoundaryError(_STAGE, "payload_integrity_mismatch")
    return raw


class _Projection:
    """Readonly three-source declaration over a real temporary artifact store."""

    def __init__(self, base: ManifestArtifactStore, sources: dict[str, Manifest]) -> None:
        self.base, self.sources = base, sources
        self.source_payloads = {p.sha256 for item in sources.values() for p in item.payloads}

    def get_manifest(self, artifact_id: str) -> Manifest:
        return self.sources.get(artifact_id) or self.base.get_manifest(artifact_id)

    def manifest_ids(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.sources) | set(self.base.manifest_ids())))

    def read_payload(self, payload: Payload) -> Iterator[bytes]:
        if payload.sha256 in self.source_payloads:
            raise BoundaryError(_STAGE, "source_payload_forbidden")
        return self.base.read_payload(payload)

    def put_payload(self, role: str, source: Any,
                    media_type: str = "application/octet-stream") -> Payload:
        return self.base.put_payload(role, source, media_type)

    def publish(self, manifest: Manifest) -> str:
        if manifest.artifact_id in self.sources or manifest.kind in {
            "dataset", "model_view", "protocol",
        }:
            raise BoundaryError(_STAGE, "source_projection_readonly")
        for parent in manifest.parents:
            self.get_manifest(parent.artifact_id)
        for payload in manifest.payloads:
            for _ in self.read_payload(payload):
                pass
        self.base.blobs.put_if_absent(
            f"manifests/{manifest.artifact_id}.json", manifest.to_bytes(),
        )
        return manifest.artifact_id


def _import_projection(projection: _Projection, manifests: dict[str, Manifest],
                       blobs: dict[str, bytes]) -> None:
    referenced = {p.sha256 for item in manifests.values() for p in item.payloads}
    if set(blobs) - referenced:
        raise BoundaryError(_STAGE, "orphan_blob")
    for item in manifests.values():
        for payload in item.payloads:
            if payload.sha256 in projection.source_payloads:
                raise BoundaryError(_STAGE, "source_payload_forbidden")
            if payload.sha256 in blobs:
                actual = projection.put_payload(
                    payload.role, io.BytesIO(blobs[payload.sha256]), payload.media_type,
                )
                if actual != payload:
                    raise BoundaryError(_STAGE, "payload_descriptor_mismatch")
    pending = dict(manifests)
    while pending:
        ready = [key for key, item in pending.items() if all(
            parent.artifact_id in projection.manifest_ids() for parent in item.parents
        )]
        if not ready:
            raise BoundaryError(_STAGE, "missing_or_cyclic_parent")
        for key in ready:
            projection.publish(pending.pop(key))


class _RecordingReporter(ObjectStoreRunReporter):
    def __init__(self, store: ArtifactStore, slots: BlobStore) -> None:
        super().__init__(store, slots)
        self.emitted: list[str] = []

    def emit(self, event: Manifest) -> str:
        identity = super().emit(event)
        if identity not in self.emitted:
            self.emitted.append(identity)
        return identity


def _source_manifests(store: ArtifactStore, run: Manifest) -> dict[str, Manifest]:
    info = run.parameters.value()
    view = store.get_manifest(info["source_view_id"])
    allocation = store.get_manifest(info["allocation_id"])
    dataset = store.get_manifest(view.parent("dataset"))
    if (view.kind != "model_view" or allocation.kind != "protocol"
            or dataset.kind != "dataset" or view.parent("allocation") != allocation.artifact_id
            or allocation.parent("dataset") != dataset.artifact_id):
        raise BoundaryError(_STAGE, "source_declaration_mismatch")
    return {item.artifact_id: item for item in (dataset, allocation, view)}


def _runtime_pin(value: object, profile: PublicM2Preflight) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {
            "torch", "python", "platform", "default_dtype", "cpu_threads",
            "implementation_sha256",
        } or value.get("implementation_sha256")
            != profile.runtime["implementation_sha256"]
            or value.get("default_dtype") != "torch.float32"
            or type(value.get("cpu_threads")) is not int or value["cpu_threads"] < 1
            or any(type(value.get(key)) is not str or not value[key]
                   for key in ("torch", "python", "platform"))):
        raise BoundaryError(_STAGE, "invalid_expected_runtime")
    return value


def _remaining_windows(profile: PublicM2Preflight, checkpoint: dict[str, Any] | None
                       ) -> int:
    if checkpoint is not None and checkpoint["completed_epochs"] == profile.config.epochs:
        return 0
    chain_index = 0 if checkpoint is None else checkpoint["chain_index"]
    cursor = 0 if checkpoint is None else checkpoint["window_cursor"]
    chains = profile.train_chains
    width = profile.config.window_steps
    return sum((len(chain.steps) - (cursor if index == chain_index else 0) + width - 1)
               // width for index, chain in enumerate(chains) if index >= chain_index)


def _request_parts(raw: bytes) -> tuple[dict[str, Any], dict[str, Manifest],
                                        dict[str, Manifest], dict[str, bytes]]:
    header, blobs = _unpack(raw, _REQUEST_MAGIC, MAX_REQUEST_BYTES)
    if set(header) != {
        "schema", "run_id", "operation_id", "attempt_id", "producer",
        "expected_runtime", "resume_id", "max_windows", "source_ids",
        "sources", "manifests", "history_event_ids", "accepted_artifact_ids", "blobs",
    } or header["schema"] != REQUEST_SCHEMA:
        raise BoundaryError(_STAGE, "request_fieldset")
    sources = _manifests(header["sources"])
    manifests = _manifests(header["manifests"])
    source_ids = header["source_ids"]
    if (not isinstance(source_ids, dict) or set(source_ids) != set(_SOURCE_ROLES)
            or set(sources) & set(manifests)
            or len(sources) != 3
            or any(not isinstance(source_ids[role], str)
                   or source_ids[role] not in sources
                   or sources[source_ids[role]].kind != kind
                   for role, kind in (
                       ("dataset", "dataset"), ("allocation", "protocol"),
                       ("source_view", "model_view"),
                   ))
            or set(source_ids.values()) != set(sources)):
        raise BoundaryError(_STAGE, "source_declaration_mismatch")
    if (sum(item.kind == "training_input" for item in manifests.values()) != 1
            or sum(item.kind == "experiment" for item in manifests.values()) != 1
            or sum(item.kind == "run" for item in manifests.values()) != 1
            or sum(item.kind == "checkpoint" for item in manifests.values())
            != (header["resume_id"] is not None)
            or any(item.kind not in {
                "training_input", "experiment", "run", "checkpoint", "run_event",
            } for item in manifests.values())):
        raise BoundaryError(_STAGE, "request_manifest_inventory")
    if (not isinstance(header["history_event_ids"], list)
            or any(type(item) is not str for item in header["history_event_ids"])
            or len(set(header["history_event_ids"])) != len(header["history_event_ids"])
            or header["history_event_ids"] != sorted(header["history_event_ids"])
            or set(header["history_event_ids"]) != {
                key for key, item in manifests.items() if item.kind == "run_event"
            }):
        raise BoundaryError(_STAGE, "event_history_mismatch")
    if (not isinstance(header["accepted_artifact_ids"], list)
            or any(type(key) is not str for key in header["accepted_artifact_ids"])
            or header["accepted_artifact_ids"] != sorted(set(header["accepted_artifact_ids"]))):
        raise BoundaryError(_STAGE, "accepted_history_mismatch")
    return header, sources, manifests, blobs


def _projection_from_request(raw: bytes, root: Path
                             ) -> tuple[_Projection, _RecordingReporter, dict[str, Any],
                                        dict[str, Manifest], dict[str, Manifest], dict[str, bytes]]:
    header, sources, manifests, blobs = _request_parts(raw)
    base = ManifestArtifactStore(LocalBlobStore(root))
    projection = _Projection(base, sources)
    _import_projection(projection, manifests, blobs)
    reporter = _RecordingReporter(projection, base.blobs)
    return projection, reporter, header, sources, manifests, blobs


def build_public_m2_remote_request(
    store: ArtifactStore, reporter: ObjectStoreRunReporter, run_id: str,
    producer: Producer, *, attempt_id: str, expected_runtime: dict[str, Any],
    resume: str | None, max_windows: int,
) -> bytes:
    """Package one already-prepared typed run; source payloads are forbidden."""
    digest(run_id, _STAGE)
    digest(attempt_id, _STAGE, length=32)
    if type(max_windows) is not int or not 0 <= max_windows <= 512:
        raise BoundaryError(_STAGE, "invalid_window_budget")
    run, training, _, _, profile = _load_run(
        store, run_id, producer, preflight_only=True,
    )
    assert isinstance(profile, PublicM2Preflight)
    _runtime_pin(expected_runtime, profile)
    if reporter.completed(run_id) is not None:
        raise BoundaryError(_STAGE, "already_completed")
    events = reporter.events(run_id)
    checkpoint = None
    if resume is None:
        if events:
            raise BoundaryError(_STAGE, "existing_history_requires_resume")
    else:
        digest(resume, _STAGE)
        checkpoint_events = [event for event in events
                             if event.parameters.value().get("kind") == "checkpoint"]
        matching = [event for event in checkpoint_events
                    if event.parameters.value().get("details", {}).get("checkpoint_id")
                    == resume]
        if (not matching or max(event.parameters.value()["optimizer_updates"]
                                for event in checkpoint_events)
                != matching[-1].parameters.value()["optimizer_updates"]):
            raise BoundaryError(_STAGE, "resume_not_latest")
        item = store.get_manifest(resume)
        if (item.kind != "checkpoint" or item.producer != producer
                or item.parent("run") != run_id
                or item.parent("training_input") != training.artifact_id
                or item.parameters.value().get("schema") != CHECKPOINT_SCHEMA):
            raise BoundaryError(_STAGE, "checkpoint_lineage_mismatch")
        checkpoint = validate_public_m2_checkpoint(
            _payload_bytes(store, item.payload("checkpoint"), MAX_REQUEST_BYTES),
            profile, expected_runtime=expected_runtime,
        )
        if any(item.parameters.value().get(key) != checkpoint[key] for key in (
            "completed_epochs", "chain_index", "window_cursor", "label_count",
            "optimizer_updates",
        )):
            raise BoundaryError(_STAGE, "checkpoint_progress_mismatch")
    remaining = _remaining_windows(profile, checkpoint)
    if max_windows > remaining or (max_windows == 0) is not (remaining == 0):
        raise BoundaryError(_STAGE, "epoch_window_budget_exceeded")
    sources = _source_manifests(store, run)
    items = {item.artifact_id: item for item in (
        training, store.get_manifest(run.parent("experiment")), run,
        *(() if resume is None else (store.get_manifest(resume),)), *events,
    )}
    blobs = {payload.sha256: _payload_bytes(store, payload, MAX_REQUEST_BYTES)
             for item in items.values() for payload in item.payloads}
    info = run.parameters.value()
    accepted_ids = sorted(identity for identity in store.manifest_ids()
                          if any(parent.artifact_id == run_id
                                 for parent in store.get_manifest(identity).parents))
    return _pack(_REQUEST_MAGIC, {
        "schema": REQUEST_SCHEMA, "run_id": run_id,
        "operation_id": info["operation_id"], "attempt_id": attempt_id,
        "producer": producer.to_dict(), "expected_runtime": expected_runtime,
        "resume_id": resume, "max_windows": max_windows,
        "source_ids": {
            "dataset": next(key for key, item in sources.items() if item.kind == "dataset"),
            "allocation": info["allocation_id"], "source_view": info["source_view_id"],
        },
        "sources": [_manifest_entry(sources[key]) for key in sorted(sources)],
        "manifests": [_manifest_entry(items[key]) for key in sorted(items)],
        "history_event_ids": sorted(event.artifact_id for event in events),
        "accepted_artifact_ids": accepted_ids,
    }, blobs, MAX_REQUEST_BYTES)


def execute_public_m2_remote_request(raw: bytes, *, request_sha256: str) -> bytes:
    """Execute one bounded attempt against a disposable local projection."""
    if digest(request_sha256, _STAGE) != _sha(raw):
        raise BoundaryError(_STAGE, "request_digest_mismatch")
    with tempfile.TemporaryDirectory(prefix="stpd-m2-projection-") as temporary:
        store, reporter, header, sources, baseline, request_blobs = _projection_from_request(
            raw, Path(temporary),
        )
        producer = Producer.decode(header["producer"])
        run_id = digest(header["run_id"], _STAGE)
        run, _, _, _, profile = _load_run(store, run_id, producer, preflight_only=True)
        assert isinstance(profile, PublicM2Preflight)
        if (run.parameters.value()["operation_id"] != header["operation_id"]
                or _runtime_pin(header["expected_runtime"], profile)
                != header["expected_runtime"]
                or header["resume_id"] is not None and header["resume_id"] not in baseline
                or type(header["max_windows"]) is not int
                or not 0 <= header["max_windows"] <= 512):
            raise BoundaryError(_STAGE, "request_binding_mismatch")
        prior_checkpoint = None
        if header["resume_id"] is not None:
            prior_item = store.get_manifest(header["resume_id"])
            prior_checkpoint = validate_public_m2_checkpoint(
                _payload_bytes(store, prior_item.payload("checkpoint"), MAX_REQUEST_BYTES),
                profile, expected_runtime=header["expected_runtime"],
            )
        remaining = _remaining_windows(profile, prior_checkpoint)
        if (header["max_windows"] > remaining
                or (header["max_windows"] == 0) is not (remaining == 0)):
            raise BoundaryError(_STAGE, "epoch_window_budget_exceeded")
        before = set(store.manifest_ids())
        result = execute_public_m2_run(
            store, reporter, run_id, producer, resume=header["resume_id"],
            stop_after_windows=(header["max_windows"] or None),
        )
        if (result.state not in {"paused", "completed"} or result.checkpoint_id is None
                or len([key for key in reporter.emitted if
                        store.get_manifest(key).parameters.value().get("kind")
                        == "window_completed"]) > header["max_windows"]
                or len({store.get_manifest(key).parameters.value().get("details", {}).get("epoch")
                        for key in reporter.emitted if
                        store.get_manifest(key).parameters.value().get("kind")
                        == "epoch_stage"}) > 1):
            raise BoundaryError(_STAGE, "attempt_bound_exceeded")
        checkpoint = store.get_manifest(result.checkpoint_id)
        runtime = decode_checkpoint(_payload_bytes(
            store, checkpoint.payload("checkpoint"), MAX_RESULT_BYTES,
        ))["runtime"]
        if runtime != header["expected_runtime"]:
            raise BoundaryError(_STAGE, "runtime_mismatch")
        delta = {key: store.get_manifest(key) for key in set(store.manifest_ids()) - before}
        if any(item.kind not in _DELTA_KINDS or item.producer != producer
               for item in delta.values()):
            raise BoundaryError(_STAGE, "invalid_delta_kind")
        blobs = {payload.sha256: _payload_bytes(store, payload, MAX_RESULT_BYTES)
                 for item in delta.values() for payload in item.payloads
                 if payload.sha256 not in request_blobs}
        return _pack(_RESULT_MAGIC, {
            "schema": RESULT_SCHEMA_REMOTE, "request_sha256": request_sha256,
            "run_id": run_id, "operation_id": header["operation_id"],
            "attempt_id": header["attempt_id"], "producer": producer.to_dict(),
            "runtime": runtime,
            "worker_result": asdict(result), "event_ids": reporter.emitted,
            "manifests": [_manifest_entry(delta[key]) for key in sorted(delta)],
        }, blobs, MAX_RESULT_BYTES)


def _checked_delta(
    store: _Projection, delta: dict[str, Manifest], profile: PublicM2Preflight,
    run: Manifest, training: Manifest, expected_runtime: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Check every new artifact against the typed M2 run, without training."""
    checkpoints: dict[str, dict[str, Any]] = {}
    run_id = run.artifact_id
    for identity, item in delta.items():
        if (item.kind not in _DELTA_KINDS or item.producer != run.producer
                or item.parent("run") != run_id):
            raise BoundaryError(_STAGE, "delta_lineage_mismatch")
        if item.kind == "checkpoint":
            info = item.parameters.value()
            if (set(info) != {
                    "schema", "operation_id", "input_identity", "engine_input_digest",
                    "implementation_sha256", "config", "engine_schema",
                    "completed_epochs", "chain_index", "window_cursor", "label_count",
                    "optimizer_updates",
                } or sorted(parent.role for parent in item.parents)
                    != ["run", "training_input"]
                    or item.parent("training_input") != training.artifact_id
                    or [p.role for p in item.payloads] != ["checkpoint"]
                    or info.get("schema") != CHECKPOINT_SCHEMA
                    or info.get("engine_schema")
                    != "stpd/public-m2-engine-checkpoint-v1"
                    or info.get("operation_id") != run.parameters.value()["operation_id"]
                    or info.get("input_identity") != run.parameters.value()["input_identity"]
                    or info.get("config") != asdict(profile.config)
                    or info.get("engine_input_digest") != profile.input_digest
                    or info.get("implementation_sha256")
                    != profile.runtime["implementation_sha256"]):
                raise BoundaryError(_STAGE, "checkpoint_lineage_mismatch")
            checked = validate_public_m2_checkpoint(
                _payload_bytes(store, item.payload("checkpoint"), MAX_RESULT_BYTES),
                profile, expected_runtime=expected_runtime,
            )
            if any(info.get(key) != checked[key] for key in (
                "completed_epochs", "chain_index", "window_cursor", "label_count",
                "optimizer_updates",
            )):
                raise BoundaryError(_STAGE, "checkpoint_progress_mismatch")
            checkpoints[identity] = checked
    for item in delta.values():
        if item.kind != "analysis":
            continue
        info = item.parameters.value()
        epoch = info.get("epoch")
        if (set(info) != {"schema", "epoch", "run_complete", "operation_id",
                         "input_identity"} or info["schema"] != STAGE_SCHEMA
                or type(epoch) is not int or epoch not in {1, 3, 5}
                or info["run_complete"] is not (epoch == 5)
                or info["operation_id"] != run.parameters.value()["operation_id"]
                or info["input_identity"] != run.parameters.value()["input_identity"]
                or sorted(parent.role for parent in item.parents) != [
                    "checkpoint", "model", "offline_evaluation", "run", "training_input",
                ] or item.parent("training_input") != training.artifact_id):
            raise BoundaryError(_STAGE, "stage_lineage_mismatch")
        checkpoint_id = item.parent("checkpoint")
        checkpoint = checkpoints.get(checkpoint_id)
        model = store.get_manifest(item.parent("model"))
        evaluation = store.get_manifest(item.parent("offline_evaluation"))
        if (checkpoint is None or checkpoint["completed_epochs"] != epoch
                or checkpoint["chain_index"] != 0 or checkpoint["window_cursor"] != 0
                or model.kind != "model" or model.producer != run.producer
                or model.parameters.value() != {
                    "schema": MODEL_SCHEMA, "epoch": epoch, "run_complete": epoch == 5,
                    "operation_id": info["operation_id"],
                    "config": asdict(profile.config),
                    "input_identity": info["input_identity"],
                    "engine_input_digest": profile.input_digest,
                    "implementation_sha256": profile.runtime["implementation_sha256"],
                    "qualification": "engineering_only",
                }
                or sorted(parent.role for parent in model.parents)
                != ["checkpoint", "run", "training_input"]
                or model.parent("checkpoint") != checkpoint_id
                or model.parent("run") != run_id
                or model.parent("training_input") != training.artifact_id
                or sorted(payload.role for payload in model.payloads)
                != ["state_tokenizer", "weights"]
                or model.payload("state_tokenizer") != training.payload("state_tokenizer")
                or evaluation.kind != "offline_evaluation"
                or evaluation.producer != run.producer
                or evaluation.parent("checkpoint") != checkpoint_id
                or evaluation.parent("model") != model.artifact_id
                or evaluation.parent("run") != run_id
                or evaluation.parent("training_input") != training.artifact_id):
            raise BoundaryError(_STAGE, "stage_children_mismatch")
        metrics = evaluation.parameters.value()
        labels = sum(len(chain.steps) for chain in profile.dev_chains)
        if (set(metrics) != {
                "schema", "epoch", "partition", "input_identity", "loss_mean",
                "top1_accuracy", "label_count", "correct_count", "qualification",
            } or metrics["schema"] != EVALUATION_SCHEMA
                or metrics["epoch"] != epoch or metrics["partition"] != "dev"
                or metrics["input_identity"] != info["input_identity"]
                or metrics["qualification"] != "engineering_only"
                or type(metrics["label_count"]) is not int
                or metrics["label_count"] != labels
                or type(metrics["correct_count"]) is not int
                or not 0 <= metrics["correct_count"] <= labels
                or type(metrics["loss_mean"]) not in {int, float}
                or not math.isfinite(metrics["loss_mean"]) or metrics["loss_mean"] < 0
                or type(metrics["top1_accuracy"]) not in {int, float}
                or metrics["top1_accuracy"] != metrics["correct_count"] / labels):
            raise BoundaryError(_STAGE, "evaluation_mismatch")
        validate_public_m2_export(
            _payload_bytes(store, model.payload("weights"), MAX_RESULT_BYTES), profile,
            completed_epochs=epoch,
            checkpoint_raw=_payload_bytes(
                store, store.get_manifest(checkpoint_id).payload("checkpoint"),
                MAX_RESULT_BYTES,
            ), expected_runtime=expected_runtime,
        )
    return checkpoints


def _checked_result(
    store: _Projection, result_header: dict[str, Any], delta: dict[str, Manifest],
    checkpoints: dict[str, dict[str, Any]], run: Manifest, training: Manifest,
    request_header: dict[str, Any],
) -> WorkerResult:
    raw = object_fields(result_header["worker_result"],
                        {"state", "run_id", "checkpoint_id", "result_id"}, _STAGE)
    state, checkpoint_id = raw["state"], raw["checkpoint_id"]
    if (state not in {"paused", "completed"} or raw["run_id"] != run.artifact_id
            or type(checkpoint_id) is not str or checkpoint_id not in checkpoints
            or (state == "paused") is not (raw["result_id"] is None)):
        raise BoundaryError(_STAGE, "worker_result_mismatch")
    event_ids = result_header["event_ids"]
    if (not isinstance(event_ids, list) or len(event_ids) != len(set(event_ids))
            or set(event_ids) != {key for key, item in delta.items()
                                  if item.kind == "run_event"}):
        raise BoundaryError(_STAGE, "event_delta_mismatch")
    events = [delta[key] for key in event_ids]
    values = [item.parameters.value() for item in events]
    kinds = [value.get("kind") for value in values]
    if (len(events) < 4 or kinds[:2] != ["loading", "resumed" if request_header["resume_id"]
                                  else "started"]
            or kinds[-1] != state
            or any(kind not in {"window_completed", "checkpoint", "epoch_stage"}
                   for kind in kinds[2:-1])
            or any(item.parent("run") != run.artifact_id for item in events)
            or len({value.get("attempt") for value in values}) != 1
            or any(value.get("schema") != "stpd/run-event-v1" for value in values)
            or (kinds.count("window_completed") < 1
                and request_header["max_windows"] != 0)
            or kinds.count("window_completed") > request_header["max_windows"]
            or len({value.get("details", {}).get("epoch") for value in values
                    if value.get("kind") == "epoch_stage"}) > 1
            or values[-1].get("details", {}).get(
                "checkpoint_id" if state == "paused" else "result_id"
            ) != (checkpoint_id if state == "paused" else raw["result_id"])):
        raise BoundaryError(_STAGE, "event_progress_mismatch")
    digest(values[0].get("attempt"), _STAGE, length=32)
    prior_updates = 0
    prior_labels = 0
    if request_header["resume_id"] is not None:
        prior = store.get_manifest(request_header["resume_id"])
        prior_updates = prior.parameters.value()["optimizer_updates"]
        prior_labels = prior.parameters.value()["label_count"]
    if (checkpoints[checkpoint_id]["optimizer_updates"] - prior_updates
            != kinds.count("window_completed")
            or checkpoints[checkpoint_id]["label_count"] - prior_labels
            != sum(value.get("details", {}).get("labels", 0) for value in values
                   if value.get("kind") == "window_completed")
            or values[-1].get("optimizer_updates")
            != checkpoints[checkpoint_id]["optimizer_updates"]):
        raise BoundaryError(_STAGE, "event_progress_mismatch")
    current_updates = 0
    for value in values:
        if value.get("kind") == "resumed":
            current_updates = prior_updates
        if value.get("kind") == "window_completed":
            current_updates += 1
            details = value.get("details", {})
            if (type(details.get("labels")) is not int
                    or not 1 <= details["labels"] <= 8
                    or type(details.get("loss_mean")) not in {int, float}
                    or not math.isfinite(details["loss_mean"])):
                raise BoundaryError(_STAGE, "window_event_mismatch")
        if (type(value.get("optimizer_updates")) is not int
                or value["optimizer_updates"] != current_updates):
            raise BoundaryError(_STAGE, "event_progress_mismatch")
    for value in values:
        if value.get("kind") == "checkpoint" and value.get("details", {}).get(
            "checkpoint_id") not in checkpoints:
            raise BoundaryError(_STAGE, "checkpoint_event_mismatch")
        if value.get("kind") == "epoch_stage":
            stage_id = value.get("details", {}).get("stage_id")
            if (stage_id not in delta or delta[stage_id].kind != "analysis"
                    or delta[stage_id].parameters.value().get("epoch")
                    != value.get("details", {}).get("epoch")):
                raise BoundaryError(_STAGE, "stage_event_mismatch")
    if (request_header["max_windows"] > 0 and checkpoint_id not in {
            value.get("details", {}).get("checkpoint_id") for value in values
            if value.get("kind") == "checkpoint"
    }):
        raise BoundaryError(_STAGE, "checkpoint_event_mismatch")
    if state == "completed":
        identity = raw["result_id"]
        if type(identity) is not str or identity not in delta:
            raise BoundaryError(_STAGE, "result_manifest_missing")
        result = delta[identity]
        if (result.kind != "run_result" or result.producer != run.producer
                or result.parameters.value() != {
                    "schema": RESULT_SCHEMA, "state": "completed", "epoch": 5,
                    "operation_id": run.parameters.value()["operation_id"],
                    "input_identity": run.parameters.value()["input_identity"],
                    "qualification": "engineering_only",
                } or result.parent("checkpoint") != checkpoint_id
                or result.parent("training_input") != training.artifact_id
                or checkpoints[checkpoint_id]["completed_epochs"] != 5
                or result.parent("stage") not in delta
                or result.parent("model") != delta[result.parent("stage")].parent("model")
                or result.parent("offline_evaluation")
                != delta[result.parent("stage")].parent("offline_evaluation")):
            raise BoundaryError(_STAGE, "terminal_result_mismatch")
    elif any(item.kind == "run_result" for item in delta.values()):
        raise BoundaryError(_STAGE, "unexpected_completion")
    referenced = set(event_ids)
    referenced.update(value.get("details", {}).get("checkpoint_id")
                      for value in values if value.get("kind") == "checkpoint")
    for item in delta.values():
        if item.kind == "analysis":
            referenced.update((item.artifact_id, item.parent("checkpoint"),
                               item.parent("model"), item.parent("offline_evaluation")))
    referenced.add(checkpoint_id)
    if state == "completed":
        referenced.add(raw["result_id"])
    if referenced - {request_header["resume_id"]} != set(delta):
        raise BoundaryError(_STAGE, "orphan_or_missing_delta")
    return WorkerResult(state, run.artifact_id, checkpoint_id, raw["result_id"])


def accept_public_m2_remote_result(
    store: ArtifactStore, reporter: ObjectStoreRunReporter, request_raw: bytes,
    result_raw: bytes, *, request_sha256: str, expected_runtime: dict[str, Any],
    select_completion: bool = False,
) -> WorkerResult:
    """Validate the entire result first, then idempotently publish its exact delta."""
    if digest(request_sha256, _STAGE) != _sha(request_raw):
        raise BoundaryError(_STAGE, "request_digest_mismatch")
    request, sources, baseline, request_blobs = _request_parts(request_raw)
    result, blobs = _unpack(result_raw, _RESULT_MAGIC, MAX_RESULT_BYTES)
    if (set(result) != {"schema", "request_sha256", "run_id", "operation_id",
                       "attempt_id", "producer", "runtime", "worker_result",
                       "event_ids", "manifests", "blobs"}
            or result["schema"] != RESULT_SCHEMA_REMOTE
            or result["request_sha256"] != request_sha256
            or any(result[key] != request[key] for key in (
                "run_id", "operation_id", "attempt_id", "producer",
            )) or result["runtime"] != expected_runtime
            or request["expected_runtime"] != expected_runtime
            or type(select_completion) is not bool):
        raise BoundaryError(_STAGE, "result_binding_mismatch")
    delta = _manifests(result["manifests"])
    if set(delta) & (set(baseline) | set(sources)):
        raise BoundaryError(_STAGE, "non_delta_manifest")
    run_id = request["run_id"]
    allowed = set(request["accepted_artifact_ids"]) | set(delta)
    current = {key: store.get_manifest(key) for key in store.manifest_ids()
               if any(parent.artifact_id == run_id
                      for parent in store.get_manifest(key).parents)}
    if set(current) - allowed:
        raise BoundaryError(_STAGE, "unexpected_local_history")
    for identity, manifest in {**sources, **baseline, **delta}.items():
        if identity in sources or identity in baseline or identity in current:
            try:
                local = store.get_manifest(identity)
            except Exception as error:
                if identity in sources or identity in baseline:
                    raise BoundaryError(_STAGE, "missing_request_baseline") from error
            else:
                if local != manifest:
                    raise BoundaryError(_STAGE, "local_manifest_collision")
    for item in baseline.values():
        for payload in item.payloads:
            if (_payload_bytes(store, payload, MAX_REQUEST_BYTES)
                    != request_blobs[payload.sha256]):
                raise BoundaryError(_STAGE, "local_payload_mismatch")
    existing_completion = reporter.completed(run_id)
    if (set(request["accepted_artifact_ids"]) - set(current)
            or set(request["history_event_ids"])
            != {key for key, item in current.items() if item.kind == "run_event"
                and key not in delta}
            or existing_completion is not None and
            existing_completion.artifact_id != result["worker_result"].get("result_id")):
        raise BoundaryError(_STAGE, "local_history_mismatch")
    with tempfile.TemporaryDirectory(prefix="stpd-m2-accept-") as temporary:
        shadow, _, _, _, _, _ = _projection_from_request(request_raw, Path(temporary))
        _import_projection(shadow, delta, blobs)
        run, training, _, _, profile = _load_run(
            shadow, run_id, Producer.decode(request["producer"]), preflight_only=True,
        )
        assert isinstance(profile, PublicM2Preflight)
        _runtime_pin(expected_runtime, profile)
        checked_items = dict(delta)
        if request["resume_id"] is not None:
            checked_items[request["resume_id"]] = shadow.get_manifest(
                request["resume_id"]
            )
        checkpoints = _checked_delta(
            shadow, checked_items, profile, run, training, expected_runtime,
        )
        outcome = _checked_result(
            shadow, result, delta, checkpoints, run, training, request,
        )
        if select_completion and outcome.state != "completed":
            raise BoundaryError(_STAGE, "completion_selection_requires_terminal_result")
    # Validation is complete. CAS publication may be resumed after a crash.
    for item in delta.values():
        for payload in item.payloads:
            if payload.sha256 in blobs:
                actual = store.put_payload(
                    payload.role, io.BytesIO(blobs[payload.sha256]), payload.media_type,
                )
                if actual != payload:
                    raise BoundaryError(_STAGE, "local_payload_collision")
    pending = {key: item for key, item in delta.items() if item.kind != "run_event"}
    while pending:
        known = set(store.manifest_ids())
        ready = [key for key, item in pending.items()
                 if all(parent.artifact_id in known for parent in item.parents)]
        if not ready:
            raise BoundaryError(_STAGE, "local_parent_missing")
        for identity in ready:
            store.publish(pending.pop(identity))
    for identity in result["event_ids"]:
        reporter.emit(delta[identity])
    if select_completion and outcome.state == "completed":
        assert outcome.result_id is not None
        reporter.complete(delta[outcome.result_id])
    return outcome
