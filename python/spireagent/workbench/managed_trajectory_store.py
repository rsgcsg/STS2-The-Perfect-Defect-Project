"""Bounded trajectory shards over the existing immutable Store and R2 transport."""
from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.store import ManifestArtifactStore
from stpd.p1.trajectory import MAX_EPISODE_BYTES, validate_trajectory

SCHEMA = "spireagent/p1-trajectory-shard-1"
MAX_EPISODES = 8
MAX_SHARD_BYTES = 256 * 1024**2


def _model_bindings(store: ManifestArtifactStore, entries: Sequence[dict[str, Any]]) -> list[str]:
    models = sorted({item["identity"]["model_artifact_id"] for item in entries})
    for model_id in models:
        model = store.get_manifest(model_id)
        if model.kind != "model" or any(
                item["identity"]["model_sha256"] != model.payload("weights").sha256
                for item in entries if item["identity"]["model_artifact_id"] == model_id):
            raise BoundaryError("trajectory_store", "shard_model_weights_mismatch")
    return models


def publish_shard(store: ManifestArtifactStore, episodes: Sequence[dict[str, Any]],
                  producer: Producer, *, scratch: Path) -> Manifest:
    if not 1 <= len(episodes) <= MAX_EPISODES:
        raise BoundaryError("trajectory_store", "shard_episode_limit")
    _model_bindings(store, episodes)
    index = []
    total = 0
    with tempfile.TemporaryDirectory(prefix="trajectory-pack-", dir=scratch) as tmp:
        path = Path(tmp) / "shard.jsonl.gz"
        with path.open("wb") as destination, gzip.GzipFile(
                filename="", fileobj=destination, mode="wb", compresslevel=1, mtime=0) as stream:
            for episode in episodes:
                validate_trajectory(episode)
                raw = json_bytes(episode)
                total += len(raw)
                if total > MAX_SHARD_BYTES:
                    raise BoundaryError("trajectory_store", "shard_byte_limit")
                index.append({"run_id": episode["run_id"], "bytes": len(raw),
                              "sha256": hashlib.sha256(raw).hexdigest(),
                              "steps": len(episode["steps"]), "identity": episode["identity"]})
                stream.write(raw)
        if len({item["run_id"] for item in index}) != len(index):
            raise BoundaryError("trajectory_store", "duplicate_episode")
        with path.open("rb") as source:
            payload = store.put_payload("trajectory", source, "application/gzip")
    models = sorted({item["identity"]["model_artifact_id"] for item in index})
    manifest = Manifest("analysis", producer,
        parents=tuple(Parent(f"model_{i}", model) for i, model in enumerate(models)),
        payloads=(payload,), parameters=FrozenObject.of({"schema": SCHEMA,
            "episodes": index, "decoded_bytes": total, "score_eligible": False,
            "research_admission": "not_evaluated"}))
    store.publish(manifest)
    return manifest


def read_shard(store: ManifestArtifactStore, artifact_id: str, *,
               scratch: Path) -> Iterator[dict[str, Any]]:
    """Fresh physical payload/hash read before exposing verified episode rows."""
    manifest = store.get_manifest(artifact_id)
    info = manifest.parameters.value()
    if (manifest.kind != "analysis" or info.get("schema") != SCHEMA
            or info.get("score_eligible") is not False
            or len(manifest.payloads) != 1
            or not 1 <= len(info.get("episodes", [])) <= MAX_EPISODES
            or type(info.get("decoded_bytes")) is not int
            or not 0 < info["decoded_bytes"] <= MAX_SHARD_BYTES):
        raise BoundaryError("trajectory_store", "invalid_shard")
    payload = manifest.payload("trajectory")
    if payload.size > MAX_SHARD_BYTES:
        raise BoundaryError("trajectory_store", "compressed_shard_limit")
    models = _model_bindings(store, info["episodes"])
    if manifest.parents != tuple(Parent(f"model_{i}", model) for i, model in enumerate(models)):
        raise BoundaryError("trajectory_store", "shard_model_parent_mismatch")
    with tempfile.TemporaryDirectory(prefix="trajectory-read-", dir=scratch) as tmp:
        path = store.materialize(payload, Path(tmp) / "shard.jsonl.gz")
        total = 0
        seen = set()
        with gzip.open(path, "rb") as stream:
            for entry in info["episodes"]:
                raw = stream.readline(MAX_EPISODE_BYTES + 2)
                total += len(raw)
                if (not raw.endswith(b"\n") or len(raw) != entry["bytes"]
                        or total > MAX_SHARD_BYTES
                        or hashlib.sha256(raw).hexdigest() != entry["sha256"]):
                    raise BoundaryError("trajectory_store", "episode_integrity_failure")
                value = json.loads(raw)
                validate_trajectory(value)
                if (value["run_id"] != entry["run_id"] or value["run_id"] in seen
                        or value["identity"] != entry["identity"]
                        or len(value["steps"]) != entry["steps"]):
                    raise BoundaryError("trajectory_store", "episode_identity_failure")
                seen.add(value["run_id"])
                yield value
            if stream.read(1) or total != info["decoded_bytes"]:
                raise BoundaryError("trajectory_store", "shard_inventory_failure")
