"""The two RL exchange objects: model versions and trajectory-shard records.

Actors and the learner only meet here. Both records are immutable, hashed and
append-only blobs in the same frozen peer that already holds the model and
trajectory artifacts, so a local directory peer and an R2 peer behave the same:
transport is only the peer binding. Model bytes stay the existing ``model``
artifact; a shard record only names an existing, read-back trajectory shard.
There is no mutable "latest" pointer: the latest version is the largest key.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, NoReturn

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.config import open_peer
from spireagent.storage.store import ManifestArtifactStore

MODEL_SCHEMA = "spireagent/rl-model-version-1"
SHARD_SCHEMA = "spireagent/rl-trajectory-shard-record-1"
CHECKPOINT_SCHEMA = "spireagent/rl-learner-checkpoint-record-1"
STOP_SCHEMA = "spireagent/rl-stop-request-1"
EVENT_SCHEMA = "spireagent/rl-learner-event-1"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def reject(reason: str) -> NoReturn:
    raise BoundaryError("rl_exchange", reason)


def _sealed(value: dict[str, Any]) -> bytes:
    body = {k: v for k, v in value.items() if k != "record_sha256"}
    return json_bytes({**body, "record_sha256": hashlib.sha256(json_bytes(body)).hexdigest()})


def _open(raw: bytes, schema: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(raw)
    if (not isinstance(value, dict) or value.get("schema") != schema
            or json_bytes(value) != _sealed(value)):
        reject("record_integrity_failure")
    return value


class Exchange:
    def __init__(self, peer: dict[str, Any], run: str) -> None:
        if not _NAME.fullmatch(run):
            reject("invalid_run_name")
        self.binding, self.run = peer, run
        self.store: ManifestArtifactStore = open_peer(peer)
        self.blobs = self.store.blobs
        self.prefix = f"exchange/rl/{run}/"

    def request_stop(self, reason: str) -> dict[str, Any]:
        """First request wins; later callers observe that original request."""
        existing = self.stop_request()
        if existing is not None:
            return existing
        raw = _sealed({"schema": STOP_SCHEMA, "run": self.run, "reason": reason,
                       "requested_at": time.time()})
        try:
            self.blobs.put_if_absent(f"{self.prefix}control/stop.json", raw)
        except BoundaryError as error:
            if error.code != "immutable_key_collision":
                raise
        result = self.stop_request()
        if result is None:
            reject("stop_request_missing_after_publication")
        return result

    def stop_request(self) -> dict[str, Any] | None:
        key = f"{self.prefix}control/stop.json"
        if key not in self.blobs.keys(f"{self.prefix}control/"):
            return None
        value = _open(self.blobs.get(key), STOP_SCHEMA)
        if (value.get("run") != self.run or not isinstance(value.get("reason"), str)
                or not value["reason"] or type(value.get("requested_at")) not in (int, float)):
            reject("stop_request_identity_mismatch")
        return value

    def publish_event(self, kind: str, **fields: Any) -> dict[str, Any]:
        raw = _sealed({**fields, "schema": EVENT_SCHEMA, "run": self.run,
                       "at": time.time(), "kind": kind})
        key = f"{self.prefix}learner-events/{time.time_ns():020d}-{uuid.uuid4().hex}.json"
        self.blobs.put_if_absent(key, raw)
        value: dict[str, Any] = json.loads(raw)
        return value

    def event_records(self) -> list[dict[str, Any]]:
        return [self._event_record(key) for key in sorted(
            self.blobs.keys(f"{self.prefix}learner-events/")) if key.endswith(".json")]

    def latest_event(self) -> dict[str, Any] | None:
        keys = sorted(k for k in self.blobs.keys(f"{self.prefix}learner-events/")
                      if k.endswith(".json"))
        return self._event_record(keys[-1]) if keys else None

    def _event_record(self, key: str) -> dict[str, Any]:
        value = _open(self.blobs.get(key), EVENT_SCHEMA)
        if value.get("run") != self.run:
            reject("event_identity_mismatch")
        return value

    # Learner checkpoints (resume across machines/allocations) ----------
    def publish_checkpoint(self, version: int, artifact_id: str) -> dict[str, Any]:
        """Name the full learner checkpoint taken at ``version``; the artifact is verified."""
        if type(version) is not int or not 0 < version < 10**6:
            reject("invalid_checkpoint_record")
        if self.store.get_manifest(artifact_id).kind != "checkpoint":
            reject("checkpoint_record_binding_mismatch")
        raw = _sealed({"schema": CHECKPOINT_SCHEMA, "run": self.run, "version": version,
                       "checkpoint_artifact_id": artifact_id, "published_at": time.time()})
        if not self.blobs.put_if_absent(f"{self.prefix}checkpoints/{version:06d}.json", raw):
            reject("checkpoint_record_already_published")
        value: dict[str, Any] = json.loads(raw)
        return value

    def latest_checkpoint(self) -> dict[str, Any] | None:
        keys = sorted(k for k in self.blobs.keys(f"{self.prefix}checkpoints/")
                      if k.endswith(".json"))
        if not keys:
            return None
        value = _open(self.blobs.get(keys[-1]), CHECKPOINT_SCHEMA)
        if value["run"] != self.run:
            reject("checkpoint_record_identity_mismatch")
        return value

    # Model versions -----------------------------------------------------
    def publish_model(self, record: dict[str, Any]) -> dict[str, Any]:
        version = record.get("version")
        if (type(version) is not int or not 0 <= version < 10**6
                or record.get("run") != self.run
                or not isinstance(record.get("model_artifact_id"), str)):
            reject("invalid_model_version")
        manifest = self.store.get_manifest(record["model_artifact_id"])
        if manifest.kind != "model" or manifest.payload("weights").sha256 != record["model_sha256"]:
            reject("model_version_binding_mismatch")
        raw = _sealed({**record, "schema": MODEL_SCHEMA})
        # An identical republish is a no-op; different bytes raise immutable_key_collision.
        self.blobs.put_if_absent(f"{self.prefix}models/{version:06d}.json", raw)
        value: dict[str, Any] = json.loads(raw)
        return value

    def model_versions(self) -> list[int]:
        keys = self.blobs.keys(f"{self.prefix}models/")
        return sorted(int(Path(key).stem) for key in keys if key.endswith(".json"))

    def model(self, version: int) -> dict[str, Any]:
        value = _open(self.blobs.get(f"{self.prefix}models/{version:06d}.json"), MODEL_SCHEMA)
        if value["version"] != version or value["run"] != self.run:
            reject("model_version_identity_mismatch")
        return value

    def latest_model(self) -> dict[str, Any] | None:
        versions = self.model_versions()
        return self.model(versions[-1]) if versions else None

    def materialize_model(self, record: dict[str, Any], cache: Path) -> Path:
        """Fetch the package (model.json + model.tensor-tree) once and verify both hashes."""
        destination: Path = cache / str(record["model_artifact_id"])
        if (destination / "COMPLETE").exists():
            return destination
        manifest = self.store.get_manifest(record["model_artifact_id"])
        staging = cache / f".{record['model_artifact_id']}.{time.time_ns()}"
        staging.mkdir(parents=True)
        self.store.materialize(manifest.payload("weights"), staging / "model.tensor-tree")
        self.store.materialize(manifest.payload("package_manifest"), staging / "model.json")
        descriptor = json.loads((staging / "model.json").read_bytes())
        with (staging / "model.tensor-tree").open("rb") as stream:
            weights = hashlib.file_digest(stream, "sha256").hexdigest()
        if weights != record["model_sha256"] or descriptor.get("package_sha256") != weights:
            reject("model_package_hash_mismatch")
        (staging / "COMPLETE").write_text(weights + "\n")
        try:
            staging.rename(destination)
        except OSError:
            if not (destination / "COMPLETE").exists():
                raise
        return destination

    # Trajectory-shard records ------------------------------------------
    def publish_shard(self, actor: str, sequence: int, record: dict[str, Any]) -> dict[str, Any]:
        if not _NAME.fullmatch(actor) or type(sequence) is not int or not 0 <= sequence < 10**8:
            reject("invalid_shard_record_key")
        if record.get("shard_artifact_id") is not None:
            info = self.store.get_manifest(record["shard_artifact_id"]).parameters.value()
            if [e["run_id"] for e in info["episodes"]] != record["run_ids"]:
                reject("shard_record_binding_mismatch")
        raw = _sealed({**record, "schema": SHARD_SCHEMA, "run": self.run, "actor": actor,
                       "sequence": sequence})
        if not self.blobs.put_if_absent(f"{self.prefix}shards/{actor}/{sequence:08d}.json", raw):
            reject("shard_record_already_published")
        value: dict[str, Any] = json.loads(raw)
        return value

    def shard_keys(self) -> list[str]:
        return sorted(k for k in self.blobs.keys(f"{self.prefix}shards/") if k.endswith(".json"))

    def shard_record(self, key: str) -> dict[str, Any]:
        if not key.startswith(f"{self.prefix}shards/"):
            reject("foreign_shard_record")
        return _open(self.blobs.get(key), SHARD_SCHEMA)
