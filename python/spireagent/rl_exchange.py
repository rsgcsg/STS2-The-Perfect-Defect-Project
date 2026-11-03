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

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.instance_lock import instance_lock
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.blobs import StoreError
from spireagent.storage.config import open_peer
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore

MODEL_SCHEMA = "spireagent/rl-model-version-1"
SHARD_SCHEMA = "spireagent/rl-trajectory-shard-record-1"
SEGMENT_SCHEMA = "spireagent/rl-trajectory-segment-record-1"
CHECKPOINT_SCHEMA = "spireagent/rl-learner-checkpoint-record-1"
SEGMENT_CHECKPOINT_SCHEMA = "spireagent/rl-learner-checkpoint-record-2"
STOP_SCHEMA = "spireagent/rl-stop-request-1"
EVENT_SCHEMA = "spireagent/rl-learner-event-1"
INPUT_SCHEMA = "spireagent/rl-segment-input-1"
END_SCHEMA = "spireagent/rl-episode-end-input-1"
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


def control_input(value: dict[str, Any]) -> dict[str, Any]:
    """Stable original input, excluding connection IDs, clocks and transport fields."""
    operation = value["operation"]
    fields = {
        "episode_open": ("actor", "episode"),
        "begin": ("actor", "episode", "previous_lease"),
        "commit": ("segment_id", "segment_artifact_id", "run_id", "rows", "mu"),
        "episode_end": ("actor", "episode", "run_id", "terminal", "outcome_states",
                        "hp_battles", "typed_original", "segments", "unqualified"),
    }
    if operation not in fields:
        reject("unsupported_input_operation")
    result = {key: value[key] for key in fields[operation] if key in value}
    if operation != "commit" and (not isinstance(result.get("actor"), str)
                or not _NAME.fullmatch(result["actor"])
                or not isinstance(result.get("episode"), str)
                or not 0 < len(result["episode"]) <= 256):
        reject("invalid_input_owner_identity")
    if operation == "begin":
        result.setdefault("previous_lease", None)
        previous = result["previous_lease"]
        if previous is not None and (not isinstance(previous, str)
                or not previous.startswith(result["actor"] + "/")
                or not previous.rpartition("/")[2].isdigit()):
            reject("invalid_begin_predecessor")
    if operation == "episode_end" and (not isinstance(result.get("outcome_states"), list)
                or not isinstance(result.get("hp_battles"), list)
                or (result.get("terminal") is not None
                    and not isinstance(result["terminal"], dict))):
        reject("invalid_episode_end_input")
    return {"operation": operation, **result}


def input_key(value: dict[str, Any]) -> str:
    operation = value["operation"]
    identity = ([value["segment_id"]] if operation == "commit" else
                [value["actor"], value["episode"]] +
                ([value.get("previous_lease")] if operation == "begin" else []))
    return hashlib.sha256(json_bytes([operation, *identity])).hexdigest()


def _input_artifacts(value: Any) -> set[str]:
    if isinstance(value, list):
        return set().union(*(_input_artifacts(item) for item in value))
    if not isinstance(value, dict):
        return set()
    result = {item for key, item in value.items()
              if key in {"segment_artifact_id", "heads_artifact_id", "model_artifact_id"}
              and isinstance(item, str)}
    for item in value.values():
        result.update(_input_artifacts(item))
    return result


class Exchange:
    def __init__(self, peer: dict[str, Any], run: str) -> None:
        if not _NAME.fullmatch(run):
            reject("invalid_run_name")
        self.binding, self.run = peer, run
        self.store: ManifestArtifactStore = open_peer(peer)
        self.blobs = self.store.blobs
        self.prefix = f"exchange/rl/{run}/"

    # One append-only input log; request keys are indexes into these same facts.
    def _optional(self, key: str) -> bytes | None:
        try:
            return self.blobs.get(key)
        except StoreError as error:
            if error.code != "object_not_found":
                raise
            return None

    def input_records(self, after: int) -> list[dict[str, Any]]:
        """List metadata once and read only the tail, never earlier input bodies."""
        keys = sorted(key for key in self.blobs.keys(f"{self.prefix}inputs/")
                      if key.endswith(".json") and int(Path(key).stem) > after)
        records = [_open(self.blobs.get(key), INPUT_SCHEMA) for key in keys]
        if [record["sequence"] for record in records] != list(
                range(after + 1, after + len(records) + 1)):
            reject("input_log_gap")
        if any(record["run"] != self.run for record in records):
            reject("input_log_run_changed")
        return records

    def require_input_position(self, sequence: int, head: str) -> None:
        raw = self._optional(f"{self.prefix}inputs/{sequence:012d}.json")
        if raw is None:
            reject("input_log_missing_seed_original_exchange_before_restore")
        record = _open(raw, INPUT_SCHEMA)
        if (record["run"] != self.run or record["sequence"] != sequence
                or record["input_artifact_id"] != head):
            reject("checkpoint_input_head_changed")

    def input_receipt(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request = control_input(request)
        raw = self._optional(f"{self.prefix}input-keys/{input_key(request)}.json")
        if raw is None:
            return None
        record = _open(raw, INPUT_SCHEMA)
        if self._optional(f"{self.prefix}inputs/{record['sequence']:012d}.json") != raw:
            reject("input_receipt_log_record_missing")
        fact = self.read_input(record)
        if fact["request"] != request:
            reject("input_key_reused_with_different_request")
        return {"record": record, **fact}

    def read_input(self, record: dict[str, Any]) -> dict[str, Any]:
        artifact = self.store.get_manifest(record["input_artifact_id"])
        fact: dict[str, Any] = json.loads(self.store.bytes(artifact.payload("input")))
        if (artifact.kind != "run_event" or fact.get("schema") != INPUT_SCHEMA
                or fact.get("run") != self.run or fact.get("sequence") != record["sequence"]
                or input_key(fact["request"]) != record["request_key"]
                or hashlib.sha256(json_bytes(fact["request"])).hexdigest()
                != record["input_sha256"]):
            reject("input_fact_binding_changed")
        return fact

    def index_input(self, record: dict[str, Any]) -> None:
        self.blobs.put_if_absent(
            f"{self.prefix}input-keys/{record['request_key']}.json", _sealed(record))

    def append_input(self, request: dict[str, Any], response: dict[str, Any], *,
                     sequence: int, previous: str | None, producer: Producer,
                     anchor: str | None = None) -> dict[str, Any]:
        """Persist before apply/ACK. An uncertain write fences the learner owner."""
        if not isinstance(self.blobs, LocalBlobStore):
            reject("segment_input_owner_requires_local_durable_store")
        request = control_input(request)
        with instance_lock(self.blobs.root / ".rl-input-owners" / f"{self.run}.lock"):
            latest = sequence - 1
            if self._optional(f"{self.prefix}inputs/{sequence:012d}.json") is not None:
                reject("input_owner_position_changed")
            if type(sequence) is not int or sequence < 1:
                reject("invalid_input_sequence")
            if latest:
                prior = _open(self.blobs.get(
                    f"{self.prefix}inputs/{latest:012d}.json"), INPUT_SCHEMA)
                if prior["input_artifact_id"] != previous:
                    reject("input_owner_head_changed")
            elif previous is not None:
                reject("input_owner_head_changed")
            fact = {"schema": INPUT_SCHEMA, "run": self.run, "sequence": sequence,
                    "previous": previous, "anchor_checkpoint": anchor,
                    "request": request, "response": response}
            artifacts = _input_artifacts([request, response])
            parents = ([Parent("previous_input", previous)] if previous else [])
            if not previous and anchor:
                parents.append(Parent("anchor_checkpoint", anchor))
            parents.extend(Parent(f"data_{i}", artifact)
                           for i, artifact in enumerate(sorted(artifacts)))
            artifact = Manifest("run_event", producer, parents=tuple(parents),
                payloads=(self.store.put_bytes("input", json_bytes(fact)),),
                parameters=FrozenObject.of({"schema": INPUT_SCHEMA, "run": self.run,
                                           "sequence": sequence}))
            self.store.publish(artifact)
            raw = _sealed({"schema": INPUT_SCHEMA, "run": self.run, "sequence": sequence,
                "request_key": input_key(request),
                "input_sha256": hashlib.sha256(json_bytes(request)).hexdigest(),
                "input_artifact_id": artifact.artifact_id})
            self.blobs.put_if_absent(f"{self.prefix}inputs/{sequence:012d}.json", raw)
            record: dict[str, Any] = json.loads(raw)
            self.index_input(record)
            return record

    def persist_episode_end(self, request: dict[str, Any], producer: Producer) -> dict[str, Any]:
        """Producer's original close input exists even if its RPC never arrives."""
        request = control_input({**request, "operation": "episode_end"})
        artifact = Manifest("run_event", producer,
            parents=tuple(Parent(f"data_{i}", artifact)
                          for i, artifact in enumerate(sorted(_input_artifacts(request)))),
            payloads=(self.store.put_bytes("input", json_bytes(request)),),
            parameters=FrozenObject.of({"schema": END_SCHEMA, "run": self.run}))
        self.store.publish(artifact)
        self.blobs.put_if_absent(f"{self.prefix}episode-ends/{input_key(request)}.json",
            _sealed({"schema": END_SCHEMA, "run": self.run,
                     "input_artifact_id": artifact.artifact_id}))
        return request

    def episode_end_input(self, actor: str, episode: str) -> dict[str, Any] | None:
        key = input_key({"operation": "episode_end", "actor": actor, "episode": episode})
        raw = self._optional(f"{self.prefix}episode-ends/{key}.json")
        if raw is None:
            return None
        record = _open(raw, END_SCHEMA)
        artifact = self.store.get_manifest(record["input_artifact_id"])
        request: dict[str, Any] = json.loads(self.store.bytes(artifact.payload("input")))
        if (record["run"] != self.run or input_key(request) != key
                or artifact.parameters.value().get("schema") != END_SCHEMA):
            reject("episode_end_input_changed")
        return request

    def archive_status(self) -> dict[str, Any] | None:
        from spireagent.storage.async_archive import ArchivedStore

        return (self.store.archive.status(owned=True)
                if isinstance(self.store, ArchivedStore) else None)

    def drain_archive(self, timeout: float = 120) -> dict[str, Any] | None:
        from spireagent.storage.async_archive import ArchivedStore

        return (self.store.archive.drain(timeout=timeout, owned=True)
                if isinstance(self.store, ArchivedStore) else None)

    def request_stop(self, reason: str) -> dict[str, Any]:
        """First request wins; later callers observe that original request."""
        if not isinstance(reason, str) or not reason:
            reject("invalid_stop_reason")
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
        return [self.event_record(key) for key in sorted(
            self.blobs.keys(f"{self.prefix}learner-events/")) if key.endswith(".json")]

    def latest_event(self) -> dict[str, Any] | None:
        keys = sorted(k for k in self.blobs.keys(f"{self.prefix}learner-events/")
                      if k.endswith(".json"))
        return self.event_record(keys[-1]) if keys else None

    def event_record(self, key: str) -> dict[str, Any]:
        value = _open(self.blobs.get(key), EVENT_SCHEMA)
        if value.get("run") != self.run:
            reject("event_identity_mismatch")
        return value

    # Learner checkpoints (resume across machines/allocations) ----------
    def publish_checkpoint(self, version: int, artifact_id: str, *,
                           sequence: int | None = None) -> dict[str, Any]:
        """Name the full learner checkpoint taken at ``version``; the artifact is verified."""
        if type(version) is not int or not (0 if sequence is not None else 1) <= version < 10**6:
            reject("invalid_checkpoint_record")
        if self.store.get_manifest(artifact_id).kind != "checkpoint":
            reject("checkpoint_record_binding_mismatch")
        if sequence is not None and (type(sequence) is not int or not 0 < sequence < 10**6):
            reject("invalid_checkpoint_sequence")
        raw = _sealed({"schema": (CHECKPOINT_SCHEMA if sequence is None
                                   else SEGMENT_CHECKPOINT_SCHEMA),
                       **({"checkpoint_sequence": sequence} if sequence is not None else {}),
                       "run": self.run, "version": version,
                       "checkpoint_artifact_id": artifact_id, "published_at": time.time()})
        key = version if sequence is None else sequence
        if not self.blobs.put_if_absent(f"{self.prefix}checkpoints/{key:06d}.json", raw):
            reject("checkpoint_record_already_published")
        value: dict[str, Any] = json.loads(raw)
        return value

    def latest_checkpoint(self) -> dict[str, Any] | None:
        keys = sorted(k for k in self.blobs.keys(f"{self.prefix}checkpoints/")
                      if k.endswith(".json"))
        if not keys:
            return None
        raw = self.blobs.get(keys[-1])
        schema = json.loads(raw).get("schema")
        if schema not in {CHECKPOINT_SCHEMA, SEGMENT_CHECKPOINT_SCHEMA}:
            reject("unsupported_checkpoint_record")
        value = _open(raw, schema)
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
    def publish_segment(self, actor: str, sequence: int, record: dict[str, Any]) -> dict[str, Any]:
        """Replace the live training edge; keep original shard records read-only."""
        if not _NAME.fullmatch(actor) or type(sequence) is not int or not 0 <= sequence < 10**8:
            reject("invalid_segment_record_key")
        info = self.store.get_manifest(record["segment_artifact_id"]).parameters.value()
        if (info.get("schema") != "stpd/p1-trajectory-segment-1"
                or any(info[key] != record[key] for key in ("segment_id", "run_id", "rows", "mu"))):
            reject("segment_record_binding_mismatch")
        raw = _sealed({**record, "schema": SEGMENT_SCHEMA, "run": self.run,
                       "actor": actor, "sequence": sequence})
        if not self.blobs.put_if_absent(f"{self.prefix}shards/{actor}/{sequence:08d}.json", raw):
            reject("segment_record_already_published")
        value: dict[str, Any] = json.loads(raw)
        return value

    def segment_record(self, key: str) -> dict[str, Any]:
        if not key.startswith(f"{self.prefix}shards/"):
            reject("foreign_segment_record")
        value = _open(self.blobs.get(key), SEGMENT_SCHEMA)
        if value.get("run") != self.run:
            reject("segment_record_identity_mismatch")
        return value

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
