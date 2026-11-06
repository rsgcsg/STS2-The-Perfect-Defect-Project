"""Hot-segment validation and read-only historical artifact readers.

Runtime supplies verified transitions and the actor supplies same-mu values.
New rows use Exchange hot slots; the old archive-bundle writer is retired.
Historical v1/v2 artifacts still undergo their original integrity/parent checks.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Parent
from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ManifestArtifactStore
from stpd.rl.segment_inputs import TRAJECTORY_SCHEMA as COMPILED_SCHEMA
from stpd.rl.segment_validation import SCHEMA as SCHEMA
from stpd.rl.segment_validation import validate_hot_segment as validate_hot_segment
from stpd.rl.segment_validation import validate_segment as validate_segment

MAX_BYTES = 64 * 1024**2


def _parents(store: ManifestArtifactStore, segment: dict[str, Any]) -> tuple[Parent, ...]:
    mu, battles = segment["mu"], segment.get("hp_battles", [])
    model = store.get_manifest(mu["model_artifact_id"])
    head = store.get_manifest(mu["heads_artifact_id"])
    if (
        model.kind != "model"
        or model.payload("weights").sha256 != mu["model_sha256"]
        or head.kind != "checkpoint"
        or head.payload("checkpoint").sha256 != mu["heads_sha256"]
        or head.parents != (Parent("policy", model.artifact_id),)
    ):
        raise BoundaryError("trajectory_segment", "segment_model_binding_changed")
    references = sorted(
        {
            state["segment_artifact_id"]
            for battle in battles
            for state in battle["states"]
            if "segment_artifact_id" in state
        }
    )
    previous = segment.get("previous_segment_artifact_id")
    original_parents = []
    if previous is not None:
        parent = store.get_manifest(previous)
        info = parent.parameters.value()
        if (
            parent.kind != "analysis"
            or info.get("schema") not in {SCHEMA, COMPILED_SCHEMA}
            or info.get("run_id") != segment["run_id"]
            or info.get("end_step") != segment["steps"][0]["step_index"] - 1
        ):
            raise BoundaryError("trajectory_segment", "original_prefix_parent_changed")
        original_parents.append(Parent("previous", previous))
    elif segment["steps"][0]["step_index"] != 0:
        raise BoundaryError("trajectory_segment", "original_prefix_parent_missing")
    return tuple(
        sorted(
            (
                Parent("model", model.artifact_id),
                Parent("heads", head.artifact_id),
                *original_parents,
                *(
                    Parent(f"hp-segment-{index:04d}", artifact)
                    for index, artifact in enumerate(references)
                ),
            )
        )
    )


def read_segment(
    store: ManifestArtifactStore, artifact_id: str, *, scratch: Path
) -> dict[str, Any]:
    import tempfile

    if artifact_id.startswith("hot:"):
        from spireagent.rl_exchange import Exchange
        from spireagent.storage.local import LocalBlobStore

        if not isinstance(store.blobs, LocalBlobStore):
            raise BoundaryError("trajectory_segment", "hot_segment_requires_local_exchange")
        slot = artifact_id.removeprefix("hot:")
        exchange = Exchange(
            {"kind": "local", "directory": str(store.blobs.root)}, slot.split("/")[0]
        )
        return exchange.read_hot_segment(exchange.hot_payload_reference(slot))

    manifest = store.get_manifest(artifact_id)
    info = manifest.parameters.value()
    if (
        manifest.kind != "analysis"
        or info.get("schema") not in {SCHEMA, COMPILED_SCHEMA}
        or type(info.get("decoded_bytes")) is not int
        or not 0 < info["decoded_bytes"] <= MAX_BYTES
        or len(manifest.payloads) != (3 if info.get("schema") == COMPILED_SCHEMA else 2)
    ):
        raise BoundaryError("trajectory_segment", "invalid_segment_manifest")
    with tempfile.TemporaryDirectory(prefix="segment-read-", dir=scratch) as directory:
        path = store.materialize(manifest.payload("trajectory"), Path(directory) / "segment.gz")
        with gzip.open(path, "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    if len(raw) != info["decoded_bytes"] or hashlib.sha256(raw).hexdigest() != info["sha256"]:
        raise BoundaryError("trajectory_segment", "segment_integrity_failure")
    value: dict[str, Any] = json.loads(raw)
    if info["schema"] == COMPILED_SCHEMA:

        compressed = store.bytes(manifest.payload("compiled_inputs"), maximum=MAX_BYTES)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            compiled = stream.read(MAX_BYTES + 1)
        if len(compiled) > MAX_BYTES:
            raise BoundaryError("trajectory_segment", "compiled_segment_byte_limit")
        body = json.loads(compiled)
        if (body.get("compiler_identity") != value.get("compiler_identity")
                or len(body.get("current", [])) != len(value["steps"])):
            raise BoundaryError("trajectory_segment", "compiled_segment_binding_changed")
        for step, wire in zip(value["steps"], body["current"], strict=True):
            step["behavior"]["compiled_input"] = wire
        value["compiled_boundary"] = body["boundary"]
    if value.get("schema") != info["schema"]:
        raise BoundaryError("trajectory_segment", "segment_encoding_changed")
    validate_segment(value)
    if manifest.parents != _parents(store, value):
        raise BoundaryError("trajectory_segment", "segment_parent_binding_changed")
    original = value["original_commit"]
    if (
        manifest.payload("originals").sha256 != original["archive_sha256"]
        or manifest.payload("originals").size != original["archive_bytes"]
    ):
        raise BoundaryError("trajectory_segment", "original_block_manifest_binding_changed")
    if any(value[key] != info[key] for key in ("run_id", "segment_id", "rows", "mu")):
        raise BoundaryError("trajectory_segment", "segment_manifest_binding_changed")
    if (info["start_step"] != value["steps"][0]["step_index"]
            or info["end_step"] != value["steps"][-1]["step_index"]):
        raise BoundaryError("trajectory_segment", "segment_step_range_binding_changed")
    return value
