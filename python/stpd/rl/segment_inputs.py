"""Compiled input row bytes, with historical RL4 artifact/input-pack readers.

New segments carry their actor-produced frames. A frozen input pack can supply
the same derived representation for an older checkpoint's original segments.
Neither representation selects training rows, labels or uses. RL4.1 reads hot
segments in batch_preparation and retains verified auxiliary row bytes in the
existing AuxPool. Its live path never uses the legacy artifact cache/input pack.
"""
from __future__ import annotations

import gzip
import json
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.store import ManifestArtifactStore

from stpd.p1.data import digest, json_bytes
from stpd.p1.export import input_spec, runtime_projection
from stpd.p1.frame_compiler import SCHEMA as FRAME_SCHEMA
from stpd.p1.frame_compiler import FrameCompiler, observation_hashes
from stpd.p1.tensor_input import TensorFrame

SCHEMA = "stpd/rl-segment-input-pack-1"
SEGMENT_SCHEMA = "stpd/rl-segment-compiled-inputs-1"
TRAJECTORY_SCHEMA = "stpd/p1-trajectory-segment-2"
MAX_BYTES = 64 * 1024**2


def frame_key(value: dict[str, Any]) -> str:
    return digest(json_bytes(value))


def validate_wire(wire: Any, value: dict[str, Any], identity: dict[str, Any]) -> None:
    """Bind the original public bytes without projecting them a second time."""
    if (not isinstance(wire, dict) or wire.get("schema") != FRAME_SCHEMA
            or wire.get("compiler") != identity
            or any(wire.get(key) != expected
                   for key, expected in observation_hashes(value["observation"]).items())
            or wire.get("record_sha256") != digest(json_bytes(
                {key: item for key, item in wire.items() if key != "record_sha256"}))):
        raise BoundaryError("rl_inputs", "compiled_public_input_binding_changed")


def segment_body(segment: dict[str, Any]) -> dict[str, Any]:
    return {"schema": SEGMENT_SCHEMA, "compiler_identity": segment["compiler_identity"],
            "current": [step["behavior"]["compiled_input"] for step in segment["steps"]],
            "boundary": segment["compiled_boundary"]}


def validate_body(body: dict[str, Any], segment: dict[str, Any],
                  identity: dict[str, Any]) -> None:
    if (body.get("schema") != SEGMENT_SCHEMA or body.get("compiler_identity") != identity
            or not isinstance(body.get("current"), list)
            or len(body["current"]) != segment["rows"]):
        raise BoundaryError("rl_inputs", "compiled_segment_membership_changed")
    for step, wire in zip(segment["steps"], body["current"], strict=True):
        validate_wire(wire, step["input"], identity)
    if segment["terminal"] is None:
        validate_wire(body.get("boundary"), segment["steps"][-1]["successor"], identity)
    elif body.get("boundary") is not None:
        raise BoundaryError("rl_inputs", "terminal_has_no_compiled_bootstrap")


def _read(store: ManifestArtifactStore, payload: Any) -> dict[str, Any]:
    import io

    if payload.size > MAX_BYTES:
        raise BoundaryError("rl_inputs", "compiled_segment_size_limit")
    with gzip.GzipFile(fileobj=io.BytesIO(store.bytes(payload, maximum=MAX_BYTES))) as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise BoundaryError("rl_inputs", "compiled_segment_size_limit")
    value: dict[str, Any] = json.loads(raw)
    return value


def publish_input_pack(store: ManifestArtifactStore, segment_ids: Iterable[str],
                       vocabulary: dict[str, Any], producer: Producer, *,
                       read_segment: Callable[..., dict[str, Any]], scratch: Path,
                       checkpoint: str) -> Manifest:
    """Explicit CPU migration; preserve every original segment and its role."""
    compiler = FrameCompiler(vocabulary)
    expected_spec = input_spec(vocabulary, runtime_projection(vocabulary))
    entries = {}
    for artifact_id in sorted(set(segment_ids)):
        segment = read_segment(store, artifact_id, scratch=scratch)
        values = [step["input"] for step in segment["steps"]]
        if segment["terminal"] is None:
            values.append(segment["steps"][-1]["successor"])
        if any(value["input_spec"] != expected_spec for value in values):
            raise BoundaryError("rl_inputs", "original_segment_input_spec_changed")
        body = {"schema": SEGMENT_SCHEMA, "compiler_identity": compiler.identity,
                "current": [compiler.encode(step["input"]["observation"])
                            for step in segment["steps"]],
                "boundary": (compiler.encode(values[-1]["observation"])
                             if segment["terminal"] is None else None)}
        validate_body(body, segment, compiler.identity)
        raw = json_bytes(body)
        if len(raw) > MAX_BYTES:
            raise BoundaryError("rl_inputs", "compiled_segment_size_limit")
        source = store.get_manifest(artifact_id)
        manifest = Manifest("feature_set", producer,
            parents=(Parent("original_segment", artifact_id),),
            payloads=(store.put_bytes("compiled_inputs", gzip.compress(
                raw, compresslevel=1, mtime=0), "application/gzip"),),
            parameters=FrozenObject.of({"schema": SEGMENT_SCHEMA,
                "original_trajectory_sha256": source.payload("trajectory").sha256,
                "compiler_identity": compiler.identity, "rows": segment["rows"]}))
        store.publish(manifest)
        entries[artifact_id] = manifest.artifact_id
    index = {"schema": SCHEMA, "compiler_identity": compiler.identity, "segments": entries}
    manifest = Manifest("feature_set", producer,
        parents=(Parent("checkpoint", checkpoint), *tuple(
            Parent(f"segment_{i:06d}", artifact) for i, artifact in enumerate(entries.values()))),
        payloads=(store.put_bytes("index", json_bytes(index)),),
        parameters=FrozenObject.of({"schema": SCHEMA,
            "compiler_identity": compiler.identity, "segment_count": len(entries)}))
    store.publish(manifest)
    return manifest


class SegmentInputs:
    """Read one frozen representation; bounded verified bytes live with this reader.

    Artifact IDs, compiler identity and pack membership are immutable in this
    owner. Retain only compact verified input bytes, never trajectories, decoded
    tensors, auxiliary labels or training state. A replacement reader is cold.
    The bound covers LRU residency, excluding cold-fill/decode temporaries.
    """

    def __init__(self, store: ManifestArtifactStore, vocabulary: dict[str, Any],
                 identity: dict[str, Any], pack_id: str | None, *,
                 cache_bytes: int = 512 * 1024**2) -> None:
        if type(cache_bytes) is not int or cache_bytes < 0:
            raise ValueError("nonnegative_input_cache_bytes_required")
        self.store, self.identity = store, identity
        self._limit = cache_bytes
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, _VerifiedInputs] = OrderedDict()
        self._empty_cache_bytes = sys.getsizeof(self._cache)
        self._bytes = self._hits = self._misses = self._evictions = 0
        self.compiler = FrameCompiler(vocabulary)
        local = self.compiler.identity
        if (set(identity) != {"input_spec", "vocabulary_sha256", "compiler_sha256"}
                or any(identity.get(key) != local[key]
                       for key in ("input_spec", "vocabulary_sha256"))
                or not isinstance(identity.get("compiler_sha256"), str)
                or len(identity["compiler_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in identity["compiler_sha256"])):
            raise BoundaryError("rl_inputs", "frozen_compiler_identity_changed")
        self.entries: dict[str, str] = {}
        if pack_id:
            manifest = store.get_manifest(pack_id)
            index = json.loads(store.bytes(manifest.payload("index")))
            if (manifest.kind != "feature_set" or index.get("schema") != SCHEMA
                    or index.get("compiler_identity") != identity
                    or manifest.parameters.value().get("schema") != SCHEMA):
                raise BoundaryError("rl_inputs", "frozen_input_pack_binding_changed")
            self.entries = index["segments"]
            parents = {parent.artifact_id for parent in manifest.parents
                       if parent.role.startswith("segment_")}
            if (not isinstance(self.entries, dict)
                    or set(self.entries.values()) != parents
                    or len(self.entries) != manifest.parameters.value().get("segment_count")):
                raise BoundaryError("rl_inputs", "frozen_input_pack_parents_changed")

    def body(self, artifact_id: str, segment: dict[str, Any]) -> dict[str, Any]:
        if segment.get("schema") == TRAJECTORY_SCHEMA:
            body = segment_body(segment)
        else:
            if artifact_id not in self.entries:
                raise BoundaryError("rl_inputs", "original_segment_missing_from_frozen_input_pack")
            manifest = self.store.get_manifest(self.entries[artifact_id])
            info = manifest.parameters.value()
            source = self.store.get_manifest(artifact_id)
            if (manifest.kind != "feature_set"
                    or manifest.parents != (Parent("original_segment", artifact_id),)
                    or info.get("schema") != SEGMENT_SCHEMA
                    or info.get("original_trajectory_sha256") != source.payload("trajectory").sha256
                    or info.get("compiler_identity") != self.identity
                    or info.get("rows") != segment["rows"]):
                raise BoundaryError("rl_inputs", "derived_original_segment_binding_changed")
            body = _read(self.store, manifest.payload("compiled_inputs"))
        validate_body(body, segment, self.identity)
        return body

    def frame(self, value: dict[str, Any], wire: dict[str, Any]) -> TensorFrame:
        return self.compiler.decode(wire, observation=value["observation"],
                                    expected_identity=self.identity)

    def _verified(self, artifact_id: str,
                  load: Callable[[], dict[str, Any]]) -> _VerifiedInputs:
        # Main auxiliary reads and the single prepare-ahead reader share this
        # lifecycle. Serialize cold fills so the same immutable artifact is not
        # simultaneously read/validated twice; decode happens outside the lock.
        with self._lock:
            found = self._cache.get(artifact_id)
            if found is not None:
                self._cache.move_to_end(artifact_id)
                self._hits += 1
                return found
            self._misses += 1
            segment = load()  # The normal reader validates the complete source.
            body = self.body(artifact_id, segment)
            current = tuple(_verified_row(step["input"], wire, step["behavior"]["floor"])
                for step, wire in zip(segment["steps"], body["current"], strict=True))
            boundary = (_verified_row(segment["steps"][-1]["successor"], body["boundary"], None)
                        if segment["terminal"] is None else None)
            # Count actual retained bytes and tuples, plus a conservative bound
            # for the key and entry object, as in the existing text cache. The
            # OrderedDict's actual allocated table is counted separately below.
            # No views can retain an uncounted large owner.
            size = (sum(sys.getsizeof(raw) for raw in current) + sys.getsizeof(current)
                    + (sys.getsizeof(boundary) if boundary is not None else 0)
                    + sys.getsizeof(artifact_id) + 512)
            found = _VerifiedInputs(current, boundary, size)
            if size <= self._limit:
                self._cache[artifact_id] = found
                self._bytes += size
                while self._cache and self._accounted_bytes() > self._limit:
                    _, removed = self._cache.popitem(last=False)
                    self._bytes -= removed.size
                    self._evictions += 1
                if not self._cache:
                    self._cache.clear()  # Release any retained high-water table.
            return found

    def _accounted_bytes(self) -> int:
        # The reader's fixed empty container exists even when caching is off.
        # Count all variable table capacity, including capacity after eviction.
        return self._bytes + sys.getsizeof(self._cache) - self._empty_cache_bytes

    def _decode(self, row: dict[str, Any]) -> TensorFrame:
        frame = self.compiler.decode(row["wire"], expected_identity=self.identity,
                                     **row["hashes"])
        if frame.choices != tuple(row["choices"]):
            raise BoundaryError("p1_frame_compiler", "catalog_choice_binding_mismatch")
        return frame

    def auxiliary(self, artifact_id: str, row: int, *,
                  read_segment: Callable[..., dict[str, Any]], scratch: Path
                  ) -> tuple[TensorFrame, int]:
        inputs = self._verified(artifact_id, lambda: read_segment(
            self.store, artifact_id, scratch=scratch))
        value = json.loads(inputs.current[row])
        return self._decode(value), value["floor"]

    def frames(self, artifact_id: str, segment: dict[str, Any]) -> dict[str, TensorFrame]:
        inputs = self._verified(artifact_id, lambda: segment)
        rows = inputs.current + ((inputs.boundary,) if inputs.boundary is not None else ())
        result = {}
        for raw in rows:
            row = json.loads(raw)
            result[row["input_key"]] = self._decode(row)
        return result

    def cache_report(self) -> dict[str, int]:
        with self._lock:
            return {"limit_bytes": self._limit, "accounted_bytes": self._accounted_bytes(),
                    "segments": len(self._cache), "hits": self._hits, "misses": self._misses,
                    "evictions": self._evictions}


@dataclass(frozen=True, slots=True)
class _VerifiedInputs:
    current: tuple[bytes, ...]
    boundary: bytes | None
    size: int


def _verified_row(value: dict[str, Any], wire: dict[str, Any], floor: int | None) -> bytes:
    return json_bytes({"wire": wire, "hashes": observation_hashes(value["observation"]),
        "choices": [choice.get("choice_id") for choice in value["observation"]["catalog"]],
        "input_key": frame_key(value), "floor": floor})


def verified_auxiliary_bytes(step: dict[str, Any]) -> bytes:
    """Snapshot only an already verified source row; no repeated full-body hash.

    The normal HotSegment reader has checked wire hashes against the public
    observation/catalog. Retain those exact bindings; the decoder still verifies
    wire/tensor integrity and compiler/vocabulary before numerical consumption.
    """
    wire = step["behavior"]["compiled_input"]
    return json_bytes({"wire": wire,
        "hashes": {key: wire[key] for key in ("observation_sha256", "catalog_sha256")},
        "choices": [choice["choice_id"] for choice in step["input"]["catalog"]],
        "floor": step["behavior"]["floor"]})


def decode_auxiliary_bytes(compiler: FrameCompiler, body: bytes,
                           identity: dict[str, Any]) -> tuple[TensorFrame, int]:
    row = json.loads(body)
    frame = compiler.decode(row["wire"], expected_identity=identity, **row["hashes"])
    if frame.choices != tuple(row["choices"]):
        raise BoundaryError("rl_inputs", "auxiliary_catalog_binding_changed")
    return frame, row["floor"]
