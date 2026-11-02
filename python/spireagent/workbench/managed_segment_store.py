"""Short verified trajectory segments on the existing immutable artifact Store.

Runtime supplies the verified transition prefix; the actor supplies its actual
behavior journal and durable original references. This is a new trajectory
encoding on the same Store/Exchange, without a game loop or legality engine.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.store import ManifestArtifactStore
from stpd.p1.data import digest
from stpd.p1.data import json_bytes as p1_json_bytes
from stpd.p1.trajectory import validate_behavior

SCHEMA = "stpd/p1-trajectory-segment-1"
MAX_BYTES = 64 * 1024**2
MAX_ORIGINAL_BYTES = 256 * 1024**2


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
            or info.get("schema") != SCHEMA
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


def validate_segment(value: dict[str, Any]) -> None:
    steps = value.get("steps")
    mu = value.get("mu") or {}
    if (
        value.get("schema") != SCHEMA
        or not isinstance(steps, list)
        or not 1 <= len(steps) <= 64
        or value.get("rows") != len(steps)
        or value.get("training_split") != "TRAIN"
        or not isinstance(value.get("run_id"), str)
        or not isinstance(value.get("segment_id"), str)
        or len(mu.get("snapshot_sha256", "")) != 64
        or type(mu.get("updates")) is not int or mu["updates"] < 0
        or not isinstance(value.get("original_commit"), dict)
    ):
        raise BoundaryError("trajectory_segment", "invalid_segment")
    original = value["original_commit"]
    if (
        original.get("run_id") != value["run_id"]
        or original.get("segment_id") != value["segment_id"]
        or not isinstance(original.get("archive_sha256"), str)
        or len(original.get("archive_sha256", "")) != 64
        or type(original.get("archive_bytes")) is not int
        or not 0 < original.get("archive_bytes", 0) <= MAX_ORIGINAL_BYTES
    ):
        raise BoundaryError("trajectory_segment", "invalid_original_block_binding")
    previous = None
    for step in steps:
        current, behavior = step["input"], step["behavior"]
        catalog = current["catalog"]
        directive = step["directive"]["directive"]
        selected = [
            i
            for i, item in enumerate(catalog)
            if item["choice_id"] == directive["selection"]["action_id"]
        ]
        if (
            step.get("schema") != "sts2.policy-runtime/verified-global-transition-1"
            or step["run_id"] != value["run_id"]
            or step["result"]["status"] != "completed"
            or behavior["mu"] != mu
            or behavior["lease"] != value["segment_id"]
            or behavior["acquisition_id"] != current["acquisition_id"]
            or behavior["input_sha256"] != digest(p1_json_bytes(current))
            or behavior["catalog_sha256"] != digest(p1_json_bytes(catalog))
            or catalog != current["observation"]["catalog"]
            or directive["scores"]["values"] != behavior["scores"]
            or step["sampling"].get("model_logits") != behavior["scores"]
            or step["sampling"].get("mode") != "temperature_uniform"
            or step["sampling"].get("temperature") != 1
            or step["sampling"].get("uniform_mix") != 0
            or len(selected) != 1
            or (previous is not None and step["step_index"] != previous + 1)
        ):
            raise BoundaryError("trajectory_segment", "transition_behavior_binding_changed")
        validate_behavior(step["sampling"], len(catalog), selected[0])
        previous = step["step_index"]
    if any(a["successor"] != b["input"] for a, b in zip(steps, steps[1:], strict=False)):
        raise BoundaryError("trajectory_segment", "segment_successor_changed")
    if (
        value.get("terminal") is not None
        and steps[-1]["successor"]["observation"]["status"] != "terminal"
    ):
        raise BoundaryError("trajectory_segment", "terminal_without_verified_successor")
    for battle in value.get("hp_battles", []):
        boundary = next(
            (step for step in steps if step["step_index"] == battle["boundary_step_index"]), None
        )
        if boundary is None:
            raise BoundaryError("trajectory_segment", "battle_hp_boundary_changed")
        successor = boundary["successor"]
        observation = successor["observation"]
        hp = ((observation.get("player") or {}).get("content") or {}).get("hp")
        if (
            successor["acquisition_id"] != battle["boundary_acquisition_id"]
            or not str((observation.get("scene") or {}).get("native_kind", "")).startswith("reward")
            or (observation.get("combat") or {}).get("availability") == "available"
            or not isinstance(hp, (int, float))
            or isinstance(hp, bool)
            or hp < 0
            or battle["hp"] != hp / 100
            or battle["end_step"] != boundary["step_index"] + 1
            or battle["start_step"] >= battle["end_step"]
            or battle["battle_id"] != f"{value['run_id']}:{battle['start_step']}"
        ):
            raise BoundaryError("trajectory_segment", "battle_hp_boundary_changed")


def publish_segment(
    store: ManifestArtifactStore, segment: dict[str, Any], producer: Producer, *, originals: bytes
) -> Manifest:
    validate_segment(segment)
    raw = json_bytes(segment)
    if len(raw) > MAX_BYTES:
        raise BoundaryError("trajectory_segment", "segment_byte_limit")
    parents = _parents(store, segment)
    expected = segment["original_commit"]
    if (
        len(originals) != expected["archive_bytes"]
        or hashlib.sha256(originals).hexdigest() != expected["archive_sha256"]
    ):
        raise BoundaryError("trajectory_segment", "original_block_integrity_failure")
    original_payload = store.put_payload("originals", io.BytesIO(originals), "application/gzip")
    # The existing observer may continue to trail the game while this bounded
    # upload/readback completes. Its lease stays charged, providing backpressure.
    observed = hashlib.sha256()
    for chunk in store.read_payload(original_payload):
        observed.update(chunk)
    if observed.hexdigest() != expected["archive_sha256"]:
        raise BoundaryError("trajectory_segment", "original_block_peer_readback_failure")
    payload = store.put_payload(
        "trajectory", io.BytesIO(gzip.compress(raw, compresslevel=1, mtime=0)), "application/gzip"
    )
    manifest = Manifest(
        "analysis",
        producer,
        parents=parents,
        payloads=tuple(sorted((payload, original_payload))),
        parameters=FrozenObject.of(
            {
                "schema": SCHEMA,
                "run_id": segment["run_id"],
                "segment_id": segment["segment_id"],
                "rows": len(segment["steps"]),
                "start_step": segment["steps"][0]["step_index"],
                "end_step": segment["steps"][-1]["step_index"],
                "decoded_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "mu": segment["mu"],
            }
        ),
    )
    store.publish(manifest)
    return manifest


def read_segment(
    store: ManifestArtifactStore, artifact_id: str, *, scratch: Path
) -> dict[str, Any]:
    import tempfile

    manifest = store.get_manifest(artifact_id)
    info = manifest.parameters.value()
    if (
        manifest.kind != "analysis"
        or info.get("schema") != SCHEMA
        or type(info.get("decoded_bytes")) is not int
        or not 0 < info["decoded_bytes"] <= MAX_BYTES
        or len(manifest.payloads) != 2
    ):
        raise BoundaryError("trajectory_segment", "invalid_segment_manifest")
    with tempfile.TemporaryDirectory(prefix="segment-read-", dir=scratch) as directory:
        path = store.materialize(manifest.payload("trajectory"), Path(directory) / "segment.gz")
        with gzip.open(path, "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    if len(raw) != info["decoded_bytes"] or hashlib.sha256(raw).hexdigest() != info["sha256"]:
        raise BoundaryError("trajectory_segment", "segment_integrity_failure")
    value: dict[str, Any] = json.loads(raw)
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
