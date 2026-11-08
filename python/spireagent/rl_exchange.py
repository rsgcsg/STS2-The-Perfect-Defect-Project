"""The existing actor/learner Exchange owns facts and reference-counted hot bytes.

Model, CP and control facts stay immutable on the original Store/archive. New
training rows live in shared-local slots, outside CAS and archive closure; the
last PPO/label/aux/prepare reference releases them. Callers select generations
from the learner rendezvous, never by sorting random generation names. Legacy
namespaces and artifact encodings remain readable as historical evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from spireagent.rl_socket_transport import NodeMuCache

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.instance_lock import instance_lock
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.rl_contracts import RL_NAME as _NAME
from spireagent.rl_contracts import HotPayloadRef, control_request_key, exchange_prefix
from spireagent.rl_dataplane_ports import CONTROL_SCHEMA, BinaryRef
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
        "commit": ("segment_id", "segment_artifact_id", "run_id", "rows", "mu", "payload"),
        "episode_end": ("actor", "episode", "run_id", "terminal", "outcome_states",
                        "hp_battles", "typed_original", "segments", "unqualified"),
    }
    if operation not in fields:
        reject("unsupported_input_operation")
    if operation == "episode_end" and "labels" in value:
        if any(value[key] != value["labels"][key] for key in ("run", "training_generation")):
            reject("episode_labels_generation_changed")
        value = {**value, **value["labels"]}
    result = {key: value[key] for key in fields[operation] if key in value}
    binary = value.get("control_schema") == CONTROL_SCHEMA
    if "control_schema" in value and not binary:
        reject("unsupported_rl_control_schema")
    if binary:
        result["control_schema"] = CONTROL_SCHEMA
        if operation == "begin":
            for key in ("node_id", "node_cached_mu_snapshot"):
                if key in value:
                    result[key] = value[key]
        for key in (("envelope", "binary_aux") if operation == "commit" else
                    ("binary_aux",) if operation == "episode_end" else ()):
            if key in value:
                result[key] = value[key]
    if "training_generation" in value:
        generation = value["training_generation"]
        if not isinstance(generation, str) or not _NAME.fullmatch(generation):
            reject("invalid_training_generation")
        result.update(run=value["run"], training_generation=generation)
        if operation == "commit" and "payload" in result:
            ref: HotPayloadRef | BinaryRef
            if binary:
                from spireagent.rl_socket_transport import binary_ref

                ref = binary_ref(result["payload"])
                prefix = "tensor:"
            else:
                ref = _hot_ref(result["payload"])
                prefix = "hot:"
            if (ref.slot.split("/")[:2] != [result["run"], generation]
                    or result.get("segment_artifact_id", prefix + ref.slot) != prefix + ref.slot):
                reject("hot_commit_reference_changed")
            result["payload"] = asdict(ref)
            result.setdefault("segment_artifact_id", prefix + ref.slot)
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
    if "training_generation" in value:
        return control_request_key(
            {**value, **({"labels": value} if value["operation"] == "episode_end" else {})}
        )
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
              and isinstance(item, str) and not item.startswith("hot:")}
    for item in value.values():
        result.update(_input_artifacts(item))
    return result


def _hot_ref(value: HotPayloadRef | dict[str, Any]) -> HotPayloadRef:
    ref = value if isinstance(value, HotPayloadRef) else HotPayloadRef(**value)
    parts = ref.slot.split("/")
    if (len(parts) != 3 or not all(_NAME.fullmatch(part) for part in parts)
            or not re.fullmatch(r"[a-f0-9]{64}", ref.sha256)
            or type(ref.size) is not int or not 0 < ref.size <= 64 * 1024**2):
        reject("invalid_hot_payload_reference")
    return ref


class Exchange:
    def __init__(self, peer: dict[str, Any], run: str) -> None:
        prefix = exchange_prefix(run)
        self.binding, self.run = peer, run
        self.store: ManifestArtifactStore = open_peer(peer)
        self.blobs = self.store.blobs
        self.prefix = prefix
        self.event_sink: Callable[[dict[str, Any]], Any] | None = None
        self.stop_source: Callable[[str | None], dict[str, Any] | None] | None = None
        self.stop_sink: Callable[[str, str | None], dict[str, Any]] | None = None
        self.generation_source: Callable[[str], bool] | None = None
        self.live_generation: str | None = None
        self.model_provider: NodeMuCache | None = None

    def _hot_store(self) -> LocalBlobStore:
        if not isinstance(self.blobs, LocalBlobStore):
            reject("hot_payload_requires_shared_local_exchange")
        # This is the existing Exchange's working set, never Store CAS/parents.
        return LocalBlobStore(self.blobs.root / ".rl-hot", maximum=64 * 1024**2)

    def _hot_state(self, ref: HotPayloadRef) -> tuple[LocalBlobStore, str, dict[str, Any]]:
        ref = _hot_ref(ref)
        if ref.slot.split("/")[0] != self.run:
            reject("foreign_hot_payload")
        store = self._hot_store()
        key = ref.slot + ".owners.json"
        state = json.loads(store.get(key))
        if state["payload"] != asdict(ref) or not state["owners"]:
            reject("hot_payload_binding_changed")
        return store, key, state

    @staticmethod
    def _write_hot_state(store: LocalBlobStore, key: str, state: dict[str, Any]) -> None:
        import os
        import tempfile

        path = store._path(key)
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(json_bytes(state))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name == "posix":
                descriptor = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @contextmanager
    def _hot_lock(self, ref: HotPayloadRef, *, generation: bool = False) -> Iterator[None]:
        # Unlike the process-owner guard, a reference mutation waits for another
        # short mutation. Large reads/validation never run under this OS lock.
        if ref.slot.split("/")[0] != self.run:
            reject("foreign_hot_payload")
        path = self._hot_store().root.joinpath(*ref.slot.split("/")[:2], "owners.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b"):
            pass
        # Normal mutations share a generation lease and serialize only a bounded
        # stripe of payloads. Restore excludes every stripe before revoking the
        # generation. A single NFS exclusive lock here made 8/12 workers slower
        # than 4, even after all decode work had completed.
        with instance_lock(path, create=False, shared=not generation, blocking=True):
            if generation:
                yield
            else:
                stripe = hashlib.sha256(ref.slot.encode()).digest()[0]
                with instance_lock(path.with_name(f"owners-{stripe:02x}.lock"), blocking=True):
                    yield

    def put_hot_payload(self, raw: bytes, *, training_generation: str, owner: str,
                        costs: dict[str, float] | None = None) -> HotPayloadRef:
        began = time.perf_counter()
        ref = _hot_ref(HotPayloadRef(
            f"{self.run}/{training_generation}/{uuid.uuid4().hex}",
            hashlib.sha256(raw).hexdigest(), len(raw)))
        if costs is not None:
            costs["payload_hash"] = time.perf_counter() - began
        if not isinstance(owner, str) or not owner:
            reject("hot_payload_owner_required")
        store = self._hot_store()
        began = time.perf_counter()
        store.put_if_absent(ref.slot + ".json", raw)
        with self._hot_lock(ref):
            if store._path(f"{self.run}/{training_generation}/discarded.json").exists():
                store._path(ref.slot + ".json").unlink(missing_ok=True)
                reject("hot_generation_discarded_on_restore")
            store.put_if_absent(ref.slot + ".owners.json", json_bytes(
                {"payload": asdict(ref), "owners": [owner]}))
        if costs is not None:
            costs["hot_files_and_fsync"] = time.perf_counter() - began
        return ref

    def retain_hot_payload(self, payload: HotPayloadRef | dict[str, Any], owner: str) -> None:
        ref = _hot_ref(payload)
        if not isinstance(owner, str) or not owner:
            reject("hot_payload_owner_required")
        with self._hot_lock(ref):
            store, key, state = self._hot_state(ref)
            state["owners"] = sorted(set(state["owners"]) | {owner})
            self._write_hot_state(store, key, state)

    def release_hot_payload(self, payload: HotPayloadRef | dict[str, Any], owner: str) -> None:
        ref = _hot_ref(payload)
        with self._hot_lock(ref):
            store = self._hot_store()
            if (
                not store._path(ref.slot + ".owners.json").exists()
                and store._path("/".join(ref.slot.split("/")[:2]) + "/discarded.json").exists()
            ):
                return  # Explicit restore revoked these refs, not an ordinary eviction.
            store, key, state = self._hot_state(ref)
            if owner not in state["owners"]:
                reject("hot_payload_owner_missing")
            state["owners"].remove(owner)
            if state["owners"]:
                self._write_hot_state(store, key, state)
            else:
                # Only a newly created Exchange hot slot is addressable here.
                # No Store object, archive or pre-existing original is removed.
                store._path(ref.slot + ".json").unlink()
                store._path(key).unlink()

    def read_hot_payload(self, payload: HotPayloadRef | dict[str, Any]) -> bytes:
        ref = _hot_ref(payload)
        reader = "read:" + uuid.uuid4().hex
        lock = self._hot_store()._path(
            "/".join(ref.slot.split("/")[:2]) + "/readers/" + reader[5:] + ".lock")
        try:
            # Death releases this OS lease even if it precedes the finally below.
            # A later CP restore can distinguish that stale ref from a live read.
            with instance_lock(lock):
                self.retain_hot_payload(ref, reader)
                try:
                    raw = self._hot_store().get(ref.slot + ".json")
                    if len(raw) != ref.size or hashlib.sha256(raw).hexdigest() != ref.sha256:
                        reject("hot_payload_integrity_failure")
                    return raw
                finally:
                    self.release_hot_payload(ref, reader)
        finally:
            lock.unlink(missing_ok=True)

    def put_hot_segment(self, segment: dict[str, Any], *, owner: str,
                        costs: dict[str, float] | None = None) -> HotPayloadRef:
        from .workbench.managed_segment_store import validate_hot_segment

        began = time.perf_counter()
        validate_hot_segment(segment)
        if costs is not None:
            costs["binding_validation"] = time.perf_counter() - began
        if segment["run"] != self.run:
            reject("hot_segment_run_changed")
        began = time.perf_counter()
        raw = json_bytes(segment)
        if costs is not None:
            costs["encoding"] = time.perf_counter() - began
        return self.put_hot_payload(raw,
            training_generation=segment["training_generation"], owner=owner, costs=costs)

    def read_hot_segment(self, payload: HotPayloadRef | dict[str, Any], *,
                         costs: dict[str, float] | None = None) -> dict[str, Any]:
        from .workbench.managed_segment_store import validate_hot_segment

        ref = _hot_ref(payload)
        began = time.perf_counter()
        raw = self.read_hot_payload(ref)
        read_at = time.perf_counter()
        segment: dict[str, Any] = json.loads(raw)
        decoded_at = time.perf_counter()
        validate_hot_segment(segment)
        if segment["run"] != self.run or segment["training_generation"] != ref.slot.split("/")[1]:
            reject("hot_segment_generation_changed")
        if costs is not None:
            costs.update(hot_read_wall_seconds=read_at - began,
                         hot_json_seconds=decoded_at - read_at,
                         hot_validate_seconds=time.perf_counter() - decoded_at)
        return segment

    def hot_payload_reference(self, slot: str) -> HotPayloadRef:
        store = self._hot_store()
        # Validate before using slot in a filesystem key.
        _hot_ref(HotPayloadRef(slot, "0" * 64, 1))
        if slot.split("/")[0] != self.run:
            reject("foreign_hot_payload")
        state = json.loads(store.get(slot + ".owners.json"))
        ref = _hot_ref(state["payload"])
        if ref.slot != slot:
            reject("hot_payload_slot_changed")
        return ref

    def hot_status(self, training_generation: str) -> dict[str, int]:
        _hot_ref(HotPayloadRef(f"{self.run}/{training_generation}/probe", "0" * 64, 1))
        store = self._hot_store()
        states = [
            json.loads(store.get(key))
            for key in store.keys(f"{self.run}/{training_generation}/")
            if key.endswith(".owners.json")
        ]
        paths = [store._path(key) for key in store.keys(f"{self.run}/{training_generation}/")
                 if re.fullmatch(r"[a-f0-9]{32}\.json", Path(key).name)]
        return {"payloads": len(paths), "bytes": sum(p.stat().st_size for p in paths),
                "references": sum(len(s["owners"]) for s in states)}

    def hot_generation_discarded(self, training_generation: str) -> bool:
        if self.generation_source is not None:
            return self.generation_source(training_generation)
        ref = _hot_ref(HotPayloadRef(f"{self.run}/{training_generation}/restore", "0" * 64, 1))
        return self._hot_store()._path(
            "/".join(ref.slot.split("/")[:2]) + "/discarded.json").exists()

    def discard_hot_generation(self, training_generation: str) -> dict[str, int]:
        """CP-only restore, after the old prepare/SGD owners have stopped.

        Revoke only this new transport's generation. Original ACKs, native
        originals, CAS and archived history are never traversed or edited.
        """
        ref = _hot_ref(HotPayloadRef(f"{self.run}/{training_generation}/restore", "0" * 64, 1))
        store = self._hot_store()
        with self._hot_lock(ref, generation=True), ExitStack() as readers:
            states = [(key, json.loads(store.get(key)))
                      for key in store.keys(f"{self.run}/{training_generation}/")
                      if key.endswith(".owners.json")]
            for _, state in states:
                for owner in state["owners"]:
                    if not owner.startswith("read:"):
                        continue
                    lock = store._path(f"{self.run}/{training_generation}/readers/{owner[5:]}.lock")
                    if not lock.is_file():
                        reject("hot_generation_has_active_readers")  # Historical unleased reader.
                    try:
                        readers.enter_context(instance_lock(lock, create=False))
                    except BoundaryError:
                        reject("hot_generation_has_active_readers")
            store.put_if_absent(f"{self.run}/{training_generation}/discarded.json",
                               json_bytes({"reason": "discarded_on_restore"}))
            paths = [store._path(key) for key in store.keys(f"{self.run}/{training_generation}/")
                     if re.fullmatch(r"[a-f0-9]{32}\.json", Path(key).name)]
            result = {"payloads": len(paths), "bytes": sum(p.stat().st_size for p in paths)}
            for path in paths:
                path.unlink()  # Includes a crashed put's new, not-yet-referenced slot.
            for key, _state in states:
                store._path(key).unlink()
        # CP-only recovery must not read an archived input log. This one
        # generation disposition revokes transient hot state; original per-input
        # ACK/used facts stay immutable and queryable under their original keys.
        self.publish_event("input_disposition", stage="discarded_on_restore",
            training_generation=training_generation, scope="hot_generation", **result)
        return result

    # One append-only input log; request keys are indexes into these same facts.
    def _optional(self, key: str) -> bytes | None:
        try:
            return self.blobs.get(key)
        except StoreError as error:
            if error.code != "object_not_found":
                raise
            return None

    def _input_prefix(self, generation: str | None) -> str:
        if generation is None:
            return self.prefix
        if not isinstance(generation, str) or not _NAME.fullmatch(generation):
            reject("invalid_training_generation")
        return f"{self.prefix}generations/{generation}/"

    def input_records(
        self, after: int, *, training_generation: str | None = None
    ) -> list[dict[str, Any]]:
        """List metadata once and read only the tail, never earlier input bodies."""
        prefix = self._input_prefix(training_generation)
        keys = sorted(key for key in self.blobs.keys(f"{prefix}inputs/")
                      if key.endswith(".json") and int(Path(key).stem) > after)
        records = [_open(self.blobs.get(key), INPUT_SCHEMA) for key in keys]
        if [record["sequence"] for record in records] != list(
                range(after + 1, after + len(records) + 1)):
            reject("input_log_gap")
        if any(record["run"] != self.run for record in records):
            reject("input_log_run_changed")
        return records

    def require_input_position(self, sequence: int, head: str, *,
                               training_generation: str | None = None) -> None:
        raw = self._optional(
            f"{self._input_prefix(training_generation)}inputs/{sequence:012d}.json"
        )
        if raw is None:
            reject("input_log_missing_seed_original_exchange_before_restore")
        record = _open(raw, INPUT_SCHEMA)
        if (record["run"] != self.run or record["sequence"] != sequence
                or record["input_artifact_id"] != head):
            reject("checkpoint_input_head_changed")

    def input_receipt(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request = control_input(request)
        if request.get("run", self.run) != self.run:
            reject("input_run_changed")
        prefix = self._input_prefix(request.get("training_generation"))
        raw = self._optional(f"{prefix}input-keys/{input_key(request)}.json")
        if raw is None:
            return None
        record = _open(raw, INPUT_SCHEMA)
        if self._optional(f"{prefix}inputs/{record['sequence']:012d}.json") != raw:
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
        prefix = self._input_prefix(record.get("training_generation"))
        self.blobs.put_if_absent(
            f"{prefix}input-keys/{record['request_key']}.json", _sealed(record))

    def append_input(self, request: dict[str, Any], response: dict[str, Any], *,
                     sequence: int, previous: str | None, producer: Producer,
                     anchor: str | None = None) -> dict[str, Any]:
        """Persist before apply/ACK. An uncertain write fences the learner owner."""
        if not isinstance(self.blobs, LocalBlobStore):
            reject("segment_input_owner_requires_local_durable_store")
        request = control_input(request)
        if request.get("run", self.run) != self.run:
            reject("input_run_changed")
        prefix = self._input_prefix(request.get("training_generation"))
        with instance_lock(self.blobs.root / ".rl-input-owners" / f"{self.run}.lock"):
            latest = sequence - 1
            if self._optional(f"{prefix}inputs/{sequence:012d}.json") is not None:
                reject("input_owner_position_changed")
            if type(sequence) is not int or sequence < 1:
                reject("invalid_input_sequence")
            if latest:
                prior = _open(self.blobs.get(
                    f"{prefix}inputs/{latest:012d}.json"), INPUT_SCHEMA)
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
                **({"training_generation": request["training_generation"]}
                   if "training_generation" in request else {}),
                "request_key": input_key(request),
                "input_sha256": hashlib.sha256(json_bytes(request)).hexdigest(),
                "input_artifact_id": artifact.artifact_id})
            self.blobs.put_if_absent(f"{prefix}inputs/{sequence:012d}.json", raw)
            record: dict[str, Any] = json.loads(raw)
            self.index_input(record)
            return record

    def persist_episode_end(self, request: dict[str, Any], producer: Producer) -> dict[str, Any]:
        """Producer's original close input exists even if its RPC never arrives."""
        request = control_input({**request, "operation": "episode_end"})
        if request.get("run", self.run) != self.run:
            reject("input_run_changed")
        prefix = self._input_prefix(request.get("training_generation"))
        artifact = Manifest("run_event", producer,
            parents=tuple(Parent(f"data_{i}", artifact)
                          for i, artifact in enumerate(sorted(_input_artifacts(request)))),
            payloads=(self.store.put_bytes("input", json_bytes(request)),),
            parameters=FrozenObject.of({"schema": END_SCHEMA, "run": self.run}))
        self.store.publish(artifact)
        self.blobs.put_if_absent(f"{prefix}episode-ends/{input_key(request)}.json",
            _sealed({"schema": END_SCHEMA, "run": self.run,
                     "input_artifact_id": artifact.artifact_id}))
        return request

    def episode_end_input(
        self, actor: str, episode: str, *, training_generation: str | None = None
    ) -> dict[str, Any] | None:
        key_request = {"operation": "episode_end", "actor": actor, "episode": episode}
        if training_generation is not None:
            key_request.update(run=self.run, training_generation=training_generation)
        key = input_key(key_request)
        prefix = self._input_prefix(training_generation)
        raw = self._optional(f"{prefix}episode-ends/{key}.json")
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

    def defer_archive(self) -> bool:
        """Record archive intents only; the shared publisher copies them (OPS-2)."""
        from spireagent.storage.async_archive import ArchivedStore

        if not isinstance(self.store, ArchivedStore):
            return False
        self.store.archive.defer_to_shared_publisher()
        return True

    def drain_archive(self, timeout: float = 120) -> dict[str, Any] | None:
        from spireagent.storage.async_archive import ArchivedStore

        return (self.store.archive.drain(timeout=timeout, owned=True)
                if isinstance(self.store, ArchivedStore) else None)

    def run_deadline(self, requested: float | None = None, *,
                     budget_id: str | None = None) -> float | None:
        """One immutable absolute deadline for this run, including all restorations."""
        if budget_id is not None and (not isinstance(budget_id, str)
                                      or not _NAME.fullmatch(budget_id)):
            reject("invalid_operating_budget_id")
        prefix = self.prefix + (f"control/budgets/{budget_id}/" if budget_id else "control/")
        key = prefix + "deadline.json"
        if requested is not None:
            if (type(requested) not in (int, float) or not math.isfinite(requested)
                    or requested <= 0):
                reject("invalid_run_deadline")
            try:
                self.blobs.put_if_absent(key, _sealed({"schema": EVENT_SCHEMA, "run": self.run,
                    "kind": "run_deadline", "deadline_at": float(requested)}))
            except BoundaryError as error:
                if error.code != "immutable_key_collision":
                    raise
        if key not in self.blobs.keys(prefix):
            return None
        record = _open(self.blobs.get(key), EVENT_SCHEMA)
        value = record.get("deadline_at")
        if (record.get("run") != self.run or record.get("kind") != "run_deadline"
                or not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value <= 0):
            reject("invalid_run_deadline")
        if requested is not None and value != requested:
            reject("run_deadline_cannot_be_reset")
        return float(value)

    def request_stop(self, reason: str, *,
                     training_generation: str | None = None) -> dict[str, Any]:
        """First request wins; later callers observe that original request."""
        if not isinstance(reason, str) or not reason:
            reject("invalid_stop_reason")
        if self.stop_sink is not None:
            return self.stop_sink(reason, training_generation)
        existing = self.stop_request(training_generation=training_generation)
        if existing is not None:
            return existing
        raw = _sealed({"schema": STOP_SCHEMA, "run": self.run, "reason": reason,
                       **({"training_generation": training_generation}
                          if training_generation is not None else {}),
                       "requested_at": time.time()})
        try:
            key = f"{self._input_prefix(training_generation)}control/stop.json"
            self.blobs.put_if_absent(key, raw)
        except BoundaryError as error:
            if error.code != "immutable_key_collision":
                raise
        result = self.stop_request(training_generation=training_generation)
        if result is None:
            reject("stop_request_missing_after_publication")
        return result

    def stop_request(self, *, training_generation: str | None = None) -> dict[str, Any] | None:
        if self.stop_source is not None:
            return self.stop_source(training_generation)
        # An explicit whole-run stop remains binding. Operational stops fence only
        # their generation; a fresh, authorized continuation preserves the old fact.
        prefix = self.prefix
        key = f"{prefix}control/stop.json"
        if key not in self.blobs.keys(f"{prefix}control/") and training_generation is not None:
            prefix = self._input_prefix(training_generation)
            key = f"{prefix}control/stop.json"
        if key not in self.blobs.keys(f"{prefix}control/"):
            return None
        value = _open(self.blobs.get(key), STOP_SCHEMA)
        if (value.get("run") != self.run or not isinstance(value.get("reason"), str)
                or not value["reason"] or type(value.get("requested_at")) not in (int, float)
                or value.get("training_generation") not in {None, training_generation}):
            reject("stop_request_identity_mismatch")
        return value

    def admission_pause(self, budget_id: str | None) -> dict[str, Any] | None:
        """A budget-scoped pause blocks new Worlds, never existing input or closure."""
        identity = hashlib.sha256(json_bytes(budget_id)).hexdigest()
        key = f"{self.prefix}control/admission-pause-{identity}.json"
        try:
            value = _open(self.blobs.get(key), EVENT_SCHEMA)
        except StoreError as error:
            if error.code != "object_not_found":
                raise
            return None
        if (value.get("run") != self.run or value.get("budget_id") != budget_id
                or value.get("kind") != "admission_pause"):
            reject("admission_pause_identity_mismatch")
        return value

    def pause_admission(self, budget_id: str | None, reason: str) -> None:
        if self.admission_pause(budget_id) is not None:
            return
        identity = hashlib.sha256(json_bytes(budget_id)).hexdigest()
        raw = _sealed({"schema": EVENT_SCHEMA, "run": self.run, "budget_id": budget_id,
                       "kind": "admission_pause", "reason": reason, "at": time.time()})
        try:
            self.blobs.put_if_absent(
                f"{self.prefix}control/admission-pause-{identity}.json", raw)
        except BoundaryError as error:
            if error.code != "immutable_key_collision":
                raise
        if self.admission_pause(budget_id) is None:
            reject("admission_pause_missing_after_publication")

    def publish_event(self, kind: str, **fields: Any) -> dict[str, Any]:
        if self.live_generation is not None:
            fields.setdefault("training_generation", self.live_generation)
        raw = _sealed({**fields, "schema": EVENT_SCHEMA, "run": self.run,
                       "at": time.time(), "kind": kind})
        if self.event_sink is not None:
            value: dict[str, Any] = json.loads(raw)
            self.event_sink(value)
            return value
        key = f"{self.prefix}learner-events/{time.time_ns():020d}-{uuid.uuid4().hex}.json"
        self.blobs.put_if_absent(key, raw)
        value = json.loads(raw)
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
                           sequence: int | None = None,
                           training_generation: str | None = None) -> dict[str, Any]:
        """Name the full learner checkpoint taken at ``version``; the artifact is verified."""
        if type(version) is not int or not (0 if sequence is not None else 1) <= version < 10**6:
            reject("invalid_checkpoint_record")
        if self.store.get_manifest(artifact_id).kind != "checkpoint":
            reject("checkpoint_record_binding_mismatch")
        if sequence is not None and (type(sequence) is not int or not 0 < sequence < 10**6):
            reject("invalid_checkpoint_sequence")
        raw = _sealed({"schema": (CHECKPOINT_SCHEMA if sequence is None
                                   else SEGMENT_CHECKPOINT_SCHEMA),
                       **({"training_generation": training_generation}
                          if training_generation is not None else {}),
                       **({"checkpoint_sequence": sequence} if sequence is not None else {}),
                       "run": self.run, "version": version,
                       "checkpoint_artifact_id": artifact_id, "published_at": time.time()})
        key = version if sequence is None else sequence
        prefix = self._input_prefix(training_generation)
        if not self.blobs.put_if_absent(f"{prefix}checkpoints/{key:06d}.json", raw):
            reject("checkpoint_record_already_published")
        value: dict[str, Any] = json.loads(raw)
        return value

    def latest_checkpoint(self, *, training_generation: str | None = None) -> dict[str, Any] | None:
        prefix = self._input_prefix(training_generation)
        keys = sorted(k for k in self.blobs.keys(f"{prefix}checkpoints/")
                      if k.endswith(".json"))
        if not keys:
            return None
        raw = self.blobs.get(keys[-1])
        schema = json.loads(raw).get("schema")
        if schema not in {CHECKPOINT_SCHEMA, SEGMENT_CHECKPOINT_SCHEMA}:
            reject("unsupported_checkpoint_record")
        value = _open(raw, schema)
        if value["run"] != self.run or value.get("training_generation") != training_generation:
            reject("checkpoint_record_identity_mismatch")
        return value

    def checkpoint_records(self) -> list[dict[str, Any]]:
        """All immutable CP records; sequence/counters order them, never generation names."""
        records = []
        prefixes = [f"{self.prefix}checkpoints/"]
        if isinstance(self.blobs, LocalBlobStore):
            generations = self.blobs.root / self.prefix / "generations"
            if generations.is_dir():
                prefixes.extend(f"{self.prefix}generations/{path.name}/checkpoints/"
                                for path in generations.iterdir() if path.is_dir())
        else:
            prefixes.append(f"{self.prefix}generations/")
        for key in (key for prefix in prefixes for key in self.blobs.keys(prefix)):
            if "/checkpoints/" not in key or not key.endswith(".json"):
                continue
            raw = self.blobs.get(key)
            schema = json.loads(raw).get("schema")
            if schema not in {CHECKPOINT_SCHEMA, SEGMENT_CHECKPOINT_SCHEMA}:
                reject("unsupported_checkpoint_record")
            record = _open(raw, schema)
            if record["run"] != self.run:
                reject("checkpoint_record_identity_mismatch")
            records.append(record)
        return sorted(records, key=lambda r: (r.get("checkpoint_sequence", 0), r["published_at"]))

    def retain_checkpoints(self, *, protected: tuple[str, ...] = (), recent: int = 3
                           ) -> dict[str, Any]:
        """R2 keeps every CP. Only remote-verified local CP payloads may cool."""
        from spireagent.storage.async_archive import ArchivedStore
        from spireagent.storage.cache import configure_cache, trim_cache

        if not isinstance(self.store, ArchivedStore) or type(recent) is not int or recent < 3:
            reject("archived_store_and_at_least_three_checkpoints_required")
        records = self.checkpoint_records()
        keep = {r["checkpoint_artifact_id"] for r in records[-recent:]} | set(protected)
        candidates = tuple(sorted({r["checkpoint_artifact_id"] for r in records} - keep))
        if self.store._cache_policy is None:
            configure_cache(self.store, self.store.archive.binding,
                            max_bytes=0, retain_recent=recent)
        result = trim_cache(self.store, protected=tuple(sorted(keep)), candidates=candidates)
        return {**result, "recent": recent, "protected_checkpoints": sorted(keep),
                "all_checkpoint_ids": [r["checkpoint_artifact_id"] for r in records],
                "remote_deletions": 0}

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
        prefix = self._input_prefix(record.get("training_generation"))
        self.blobs.put_if_absent(f"{prefix}models/{version:06d}.json", raw)
        value: dict[str, Any] = json.loads(raw)
        return value

    def model_versions(self, *, training_generation: str | None = None) -> list[int]:
        keys = self.blobs.keys(f"{self._input_prefix(training_generation)}models/")
        return sorted(int(Path(key).stem) for key in keys if key.endswith(".json"))

    def model(self, version: int, *, training_generation: str | None = None) -> dict[str, Any]:
        prefix = self._input_prefix(training_generation)
        value = _open(self.blobs.get(f"{prefix}models/{version:06d}.json"), MODEL_SCHEMA)
        if (value["version"] != version or value["run"] != self.run
                or value.get("training_generation") != training_generation):
            reject("model_version_identity_mismatch")
        return value

    def latest_model(self, *, training_generation: str | None = None) -> dict[str, Any] | None:
        if self.model_provider is not None:
            return self.model_provider.model_record(training_generation)
        versions = self.model_versions(training_generation=training_generation)
        return (
            self.model(versions[-1], training_generation=training_generation) if versions else None
        )

    def materialize_model(self, record: dict[str, Any], cache: Path) -> Path:
        """Fetch the package (model.json + model.tensor-tree) once and verify both hashes."""
        if self.model_provider is not None:
            return self.model_provider.bootstrap_model(record, cache, self.store)
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
        if "payload" in record:
            segment = self.read_hot_segment(record["payload"])
            if (any(segment[key] != record[key] for key in ("segment_id", "run_id", "rows", "mu"))
                    or record["segment_artifact_id"] != "hot:" + _hot_ref(record["payload"]).slot):
                reject("segment_record_binding_mismatch")
            generation = segment["training_generation"]
            raw = _sealed({**record, "schema": SEGMENT_SCHEMA, "run": self.run,
                           "training_generation": generation, "actor": actor, "sequence": sequence})
            self.blobs.put_if_absent(
                f"{self.prefix}generations/{generation}/shards/{actor}/{sequence:08d}.json", raw)
            published: dict[str, Any] = json.loads(raw)
            return published
        reject("legacy_segment_publication_retired")

    def segment_record(self, key: str) -> dict[str, Any]:
        scoped = key.removeprefix(self.prefix)
        if not key.startswith(self.prefix) or not (
            scoped.startswith("shards/")
            or re.fullmatch(
                r"generations/[a-z0-9][a-z0-9-]{0,62}/shards/[a-z0-9][a-z0-9-]{0,62}/[0-9]{8}\.json",
                scoped,
            )
        ):
            reject("foreign_segment_record")
        value = _open(self.blobs.get(key), SEGMENT_SCHEMA)
        if value.get("run") != self.run:
            reject("segment_record_identity_mismatch")
        if (
            scoped.startswith("generations/")
            and value.get("training_generation") != scoped.split("/")[1]
        ):
            reject("segment_record_generation_mismatch")
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

    def shard_keys(self, *, training_generation: str | None = None) -> list[str]:
        prefix = self._input_prefix(training_generation)
        return sorted(k for k in self.blobs.keys(f"{prefix}shards/") if k.endswith(".json"))

    def shard_record(self, key: str) -> dict[str, Any]:
        if not key.startswith(f"{self.prefix}shards/"):
            reject("foreign_shard_record")
        return _open(self.blobs.get(key), SHARD_SCHEMA)
