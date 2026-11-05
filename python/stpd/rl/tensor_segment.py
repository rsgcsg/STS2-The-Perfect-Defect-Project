"""Actor-compiled parameter-free sources. Transport and label admission stay outside.

Packets contain canonical JSON descriptors and safetensors dtype slabs, never
pickle, model state or a second copy of the original observation/trajectory.
The spool is a bounded, actor-local derived working set, not an original Store.
"""

from __future__ import annotations

import dataclasses as dc
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import torch
from safetensors.torch import save as save_tensors

from spireagent.rl_dataplane_ports import (
    MAX_HEADER_BYTES,
    MAX_PACKET_BYTES,
    MAX_SEGMENT_ROWS,
    TENSOR_SEGMENT_FORMAT,
    TENSOR_SEGMENT_MAGIC,
    EncodedSegment,
)
from stpd.p1.data import json_bytes
from stpd.p1.export import input_spec, runtime_projection
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.tensor_input import Reduction, TensorFrame

from . import batch_preparation as bp
from .prepared_batch import AuxiliaryFrame

# Closed descriptor vocabulary. No dynamic import/class construction from wire.
_CLASSES = {c.__name__: c for c in (bp.PreparedSource, bp._TensorView, TensorFrame, Reduction)}
_DTYPES = {
    "F32": np.dtype("<f4"),
    "F64": np.dtype("<f8"),
    "I64": np.dtype("<i8"),
    "I32": np.dtype("<i4"),
    "BOOL": np.dtype("?"),
}


def _sha(raw: Any) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: Any) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise ValueError("binary_duplicate_field")
            out[key] = value
        return out

    return json.loads(
        bytes(raw),
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("binary_nonfinite_json")),
    )


def _pack(value: Any) -> Any:
    if dc.is_dataclass(value):
        if type(value).__name__ not in _CLASSES:
            raise ValueError("binary_unsupported_descriptor")
        return {
            "$class": type(value).__name__,
            "fields": {
                f.name: _pack(getattr(value, f.name))
                for f in dc.fields(value)
                if not isinstance(value, TensorFrame) or f.name not in {"host", "metadata_cache"}
            },
        }
    if isinstance(value, tuple):
        return {"$tuple": [_pack(v) for v in value]}
    if isinstance(value, list):
        return [_pack(v) for v in value]
    if isinstance(value, dict):
        if any(not isinstance(k, str) or k.startswith("$") for k in value):
            raise ValueError("binary_invalid_descriptor_key")
        return {k: _pack(v) for k, v in value.items()}
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError("binary_unsupported_descriptor")


def _unpack(value: Any) -> Any:
    if isinstance(value, list):
        return [_unpack(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "$class" in value:
        if set(value) != {"$class", "fields"} or value["$class"] not in _CLASSES:
            raise ValueError("binary_unknown_descriptor")
        cls = _CLASSES[value["$class"]]
        expected = {f.name for f in dc.fields(cls)}
        if cls is TensorFrame:
            expected -= {"host", "metadata_cache"}
        if set(value["fields"]) != expected:
            raise ValueError("binary_descriptor_fields_changed")
        return cls(**{k: _unpack(v) for k, v in value["fields"].items()})
    if "$tuple" in value:
        if set(value) != {"$tuple"}:
            raise ValueError("binary_invalid_tuple")
        return tuple(_unpack(v) for v in value["$tuple"])
    if any(k.startswith("$") for k in value):
        raise ValueError("binary_unknown_descriptor")
    return {k: _unpack(v) for k, v in value.items()}


def _slabs(raw: memoryview) -> tuple[np.ndarray, ...]:
    """Read safetensors without copying the slabs; owner must retain raw storage."""
    if len(raw) < 8:
        raise ValueError("binary_invalid_slabs")
    n = int.from_bytes(raw[:8], "little")
    if not 0 < n <= MAX_HEADER_BYTES or n + 8 > len(raw):
        raise ValueError("binary_invalid_slab_header")
    header = _json(raw[8 : 8 + n])
    if not isinstance(header, dict) or set(header) != {str(i) for i in range(len(header))}:
        raise ValueError("binary_invalid_slab_names")
    slabs, intervals = [], []
    for i in range(len(header)):
        item = header[str(i)]
        if not isinstance(item, dict) or set(item) != {"dtype", "shape", "data_offsets"}:
            raise ValueError("binary_invalid_slab_descriptor")
        dtype, shape, offsets = item["dtype"], item["shape"], item["data_offsets"]
        if (
            dtype not in _DTYPES
            or not isinstance(shape, list)
            or len(shape) != 1
            or type(shape[0]) is not int
            or shape[0] < 0
            or not isinstance(offsets, list)
            or len(offsets) != 2
            or any(type(v) is not int for v in offsets)
        ):
            raise ValueError("binary_unsupported_dtype_or_shape")
        start, end = offsets
        if (
            not 0 <= start <= end <= len(raw) - 8 - n
            or end - start != shape[0] * _DTYPES[dtype].itemsize
        ):
            raise ValueError("binary_slab_offset_out_of_bounds")
        intervals.append((start, end))
        slabs.append(np.frombuffer(raw[8 + n + start : 8 + n + end], dtype=_DTYPES[dtype]))
    cursor = 0
    for start, end in sorted(intervals):
        if start != cursor:
            raise ValueError("binary_overlapping_or_missing_slab_bytes")
        cursor = end
    if cursor != len(raw) - 8 - n:
        raise ValueError("binary_trailing_slab_bytes")
    return tuple(slabs)


def _witness(segment: dict[str, Any]) -> dict[str, Any]:
    result = {
        k: segment[k]
        for k in (
            "training_split",
            "identity",
            "run",
            "training_generation",
            "segment_id",
            "run_id",
            "rows",
            "mu",
            "terminal",
            "compiler_identity",
            "original_commit",
            "hp_battles",
        )
    }
    result["steps"] = []
    for step in segment["steps"]:
        successor = step["successor"]
        observation = successor["observation"]
        result["steps"].append(
            {
                "step_index": step["step_index"],
                "input": {"input_spec": step["input"]["input_spec"]},
                "successor": {
                    "acquisition_id": successor["acquisition_id"],
                    "observation": {
                        k: observation.get(k) for k in ("status", "scene", "combat", "player")
                    },
                },
                "successor_sha256": _sha(json_bytes(successor)),
            }
        )
    return result


def _bindings(segment: dict[str, Any], source: bp.PreparedSource) -> list[dict[str, Any]]:
    witness_steps = _witness(segment)["steps"]
    return [
        {
            "source": ref,
            "choices": list(frame.choices),
            "input_sha256": step["behavior"]["input_sha256"],
            "observation_sha256": step["behavior"]["compiled_input"]["observation_sha256"],
            "catalog_sha256": step["behavior"]["compiled_input"]["catalog_sha256"],
            "record_sha256": step["behavior"]["compiled_input"]["record_sha256"],
            "successor_sha256": _sha(json_bytes(step["successor"])),
            "witness_sha256": _sha(json_bytes(witness_steps[i])),
            "mu": segment["mu"],
            "compiler_identity": segment["compiler_identity"],
            "original_commit": segment["original_commit"],
        }
        for i, (step, ref, frame) in enumerate(
            zip(segment["steps"], source.row_refs, source.frames, strict=True)
        )
    ]


def encode_packet(
    source: bp.PreparedSource,
    envelope: dict[str, Any],
    *,
    witness: dict[str, Any],
    bindings: list[dict[str, Any]],
) -> EncodedSegment:
    source = dc.replace(source, seconds={}, pid=0)
    tree, slabs = bp.pack_transport(bp.contiguous_slabs(source)[0])
    env = dict(
        envelope, format=TENSOR_SEGMENT_FORMAT, row_bindings_sha256=_sha(json_bytes(bindings))
    )
    header = json_bytes(
        {
            "schema": TENSOR_SEGMENT_FORMAT,
            "envelope": env,
            "source": _pack(tree),
            "witness": witness,
            "bindings": bindings,
        }
    )
    if len(header) > MAX_HEADER_BYTES:
        raise ValueError("binary_header_byte_limit")
    body = save_tensors({str(i): torch.from_numpy(a) for i, a in enumerate(slabs)})
    packet = TENSOR_SEGMENT_MAGIC + len(header).to_bytes(8, "little") + header + body
    if len(packet) > MAX_PACKET_BYTES:
        raise ValueError("binary_packet_byte_limit")
    return EncodedSegment(dict(env, packet_sha256=_sha(packet), packet_size=len(packet)), packet)


def decode_packet(
    raw: bytes | memoryview,
    *,
    envelope: dict[str, Any] | None = None,
    compiler: FrameCompiler | None = None,
) -> tuple[bp.PreparedSource, dict[str, Any]]:
    raw = memoryview(raw)
    if len(raw) < 24 or len(raw) > MAX_PACKET_BYTES or raw[:8] != TENSOR_SEGMENT_MAGIC:
        raise ValueError("binary_magic_or_size_changed")
    n = int.from_bytes(raw[8:16], "little")
    if not 0 < n <= MAX_HEADER_BYTES or 16 + n + 8 > len(raw):
        raise ValueError("binary_header_length_out_of_bounds")
    header = _json(raw[16 : 16 + n])
    if (
        not isinstance(header, dict)
        or set(header) != {"schema", "envelope", "source", "witness", "bindings"}
        or header["schema"] != TENSOR_SEGMENT_FORMAT
    ):
        raise ValueError("binary_unknown_schema")
    env = header["envelope"]
    common = {
        "format",
        "payload_kind",
        "run",
        "training_generation",
        "actor",
        "episode",
        "run_id",
        "rows",
        "producer",
        "recipe_digest",
        "use_and_seed_digest",
        "row_bindings_sha256",
    }
    fields = (
        common | {"segment_id", "mu", "compiler_identity", "input_spec", "original_commit"}
        if env.get("payload_kind") == "ppo"
        else common | {"bundle_id", "typed_original"}
    )
    if (
        set(env) != fields
        or not isinstance(env.get("producer"), dict)
        or not env["producer"]
        or any(
            not isinstance(env.get(k), str) or not env[k]
            for k in ("run", "training_generation", "actor", "episode", "run_id")
        )
        or any(
            not isinstance(env.get(k), str)
            or len(env[k]) != 64
            or any(c not in "0123456789abcdef" for c in env[k])
            for k in ("recipe_digest", "use_and_seed_digest", "row_bindings_sha256")
        )
        or type(env.get("rows")) is not int
        or not 1 <= env["rows"] <= MAX_SEGMENT_ROWS
    ):
        raise ValueError("binary_envelope_fields_changed")
    if envelope is not None and envelope != dict(
        env, packet_size=len(raw), packet_sha256=_sha(raw)
    ):
        raise ValueError("binary_envelope_binding_changed")
    if (
        env.get("format") != TENSOR_SEGMENT_FORMAT
        or env.get("payload_kind") not in {"ppo", "aux"}
        or env.get("row_bindings_sha256") != _sha(json_bytes(header["bindings"]))
    ):
        raise ValueError("binary_row_bindings_changed")
    slabs = _slabs(raw[16 + n :])
    tree = _unpack(header["source"])

    def validate(node: Any) -> Any:
        if isinstance(node, bp._TensorView) and (
            type(node.slab) is not int
            or not 0 <= node.slab < len(slabs)
            or type(node.offset) is not int
            or node.offset < 0
            or not isinstance(node.shape, tuple)
            or any(type(d) is not int or d < 0 for d in node.shape)
            or math.prod(node.shape) > len(slabs[node.slab]) - node.offset
        ):
            raise ValueError("binary_tensor_offset_out_of_bounds")
        return node

    bp._map_structure(tree, validate)
    source = bp.unpack_transport((tree, slabs))
    validate_source(source, header, compiler=compiler)
    return source, header


def validate_source(
    source: bp.PreparedSource, header: dict[str, Any], *, compiler: FrameCompiler | None = None
) -> None:
    env, witness, bindings = header["envelope"], header["witness"], header["bindings"]
    if not isinstance(source, bp.PreparedSource):
        raise ValueError("binary_source_type_changed")
    n = len(source.frames)
    if (
        not 1 <= n <= MAX_SEGMENT_ROWS
        or len(bindings) != n
        or any(
            len(getattr(source, k)) != n
            for k in ("floors", "row_refs", "actions", "policy_probabilities", "metadata")
        )
        or source.probabilities.shape != (n, 51)
        or source.targets.shape != (n, 51)
        or source.raw_advantages.shape != (n,)
        or witness["rows"] != n
        or len(witness["steps"]) != n
        or source.segment_id != witness["segment_id"]
        or source.run_id != env["run_id"]
        or source.generation != env["training_generation"]
        or source.compiler_identity != witness["compiler_identity"]
        or witness["run"] != env["run"]
        or witness["run_id"] != env["run_id"]
        or witness["training_generation"] != env["training_generation"]
        or witness["identity"]["intent_id"] != env["episode"]
        or (
            env["payload_kind"] == "ppo"
            and (
                env["rows"] != n
                or env["segment_id"] != source.segment_id
                or env["mu"] != witness["mu"]
                or env["compiler_identity"] != source.compiler_identity
                or env["original_commit"] != witness["original_commit"]
            )
        )
    ):
        raise ValueError("binary_source_membership_changed")
    if source.labels != {k: witness[k] for k in ("segment_id", "run_id", "terminal", "hp_battles")}:
        raise ValueError("binary_label_source_binding_changed")
    expected_spec = (
        input_spec(compiler._encoding.vocabulary, runtime_projection(compiler._encoding.vocabulary))
        if compiler is not None
        else None
    )
    if (
        any(
            step["input"]["input_spec"] != witness["steps"][0]["input"]["input_spec"]
            for step in witness["steps"]
        )
        or (
            env["payload_kind"] == "ppo"
            and env["input_spec"] != witness["steps"][0]["input"]["input_spec"]
        )
        or (
            compiler is not None
            and (
                witness["steps"][0]["input"]["input_spec"] != expected_spec
                or any(
                    source.compiler_identity.get(k) != compiler.identity[k]
                    for k in ("input_spec", "vocabulary_sha256")
                )
            )
        )
    ):
        raise ValueError("binary_input_spec_or_compiler_changed")
    for tensor in (source.probabilities, source.targets, source.raw_advantages):
        if tensor.dtype != torch.float32 or not bool(torch.isfinite(tensor).all()):
            raise ValueError("binary_nonfinite_or_dtype_changed")
    for mass in (source.probabilities, source.targets):
        if bool((mass < 0).any()) or not torch.allclose(mass.sum(-1), torch.ones(n), atol=1e-5):
            raise ValueError("binary_invalid_outcome_distribution")
    present = 0
    for i, (frame, binding) in enumerate(zip(source.frames, bindings, strict=True)):
        ref = {
            "run_id": source.run_id,
            "segment_id": source.segment_id,
            "training_generation": source.generation,
            "row": i,
            "key": f"{source.run_id}:{witness['steps'][i]['step_index']}",
        }
        if (
            source.row_refs[i] != ref
            or binding["source"] != ref
            or binding["mu"] != witness["mu"]
            or binding["compiler_identity"] != source.compiler_identity
            or binding["original_commit"] != witness["original_commit"]
            or binding["successor_sha256"] != witness["steps"][i]["successor_sha256"]
            or binding["witness_sha256"] != _sha(json_bytes(witness["steps"][i]))
            or type(source.floors[i]) is not int
            or source.floors[i] < 0
        ):
            raise ValueError("binary_row_binding_changed")
        if frame is None:
            if env["payload_kind"] != "aux":
                raise ValueError("binary_missing_PPO_frame")
            continue
        present += 1
        probabilities, action = source.policy_probabilities[i], source.actions[i]
        if (
            not frame.choices
            or len(set(frame.choices)) != len(frame.choices)
            or list(frame.choices) != binding["choices"]
            or len(probabilities) != len(frame.choices)
            or type(action) is not int
            or not 0 <= action < len(frame.choices)
            or any(not math.isfinite(p) or p < 0 for p in probabilities)
            or not math.isclose(sum(probabilities), 1, abs_tol=1e-5)
            or probabilities[action] <= 0
        ):
            raise ValueError("binary_catalog_or_probability_changed")
        if compiler is not None:
            compiler._validate_state(frame.state())
    if present != env["rows"]:
        raise ValueError("binary_ragged_row_count_changed")


def auxiliary_frame(source: bp.PreparedSource, ref: dict[str, Any]) -> AuxiliaryFrame:
    row = ref.get("row")
    if (
        type(row) is not int
        or not 0 <= row < len(source.frames)
        or source.row_refs[row] != ref
        or source.frames[row] is None
    ):
        raise ValueError("binary_auxiliary_row_binding_changed")
    return AuxiliaryFrame(ref, source.frames[row], source.floors[row])


class TensorSegmentEncoder:
    def __init__(
        self,
        *,
        vocabulary: dict[str, Any],
        compiler_identity: dict[str, Any],
        recipe: dict[str, Any],
        spool: Path | str,
    ) -> None:
        self.compiler = FrameCompiler(vocabulary)
        self.expected_spec = input_spec(vocabulary, runtime_projection(vocabulary))
        self.identity, self.recipe = compiler_identity, dict(recipe)
        if any(
            compiler_identity.get(k) != self.compiler.identity[k]
            for k in ("input_spec", "vocabulary_sha256")
        ):
            raise ValueError("binary_encoder_compiler_changed")
        self.spool = Path(spool)
        self.spool.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_spool_bytes = int(recipe.get("max_spool_bytes", 2 * 1024**3))
        if self.max_spool_bytes < 1:
            raise ValueError("positive_binary_spool_limit_required")

    def _path(self, segment_id: str) -> Path:
        return self.spool / (_sha(segment_id.encode()) + ".stseg")

    def encode(self, verified_segment: dict[str, Any]) -> EncodedSegment:
        # Original live verification/OriginalBarrier precede this call. This is
        # the same production typed verifier, not another legality engine.
        from spireagent.workbench.managed_segment_store import validate_hot_segment

        validate_hot_segment(verified_segment)
        s = verified_segment
        if s["compiler_identity"] != self.identity:
            raise ValueError("binary_encoder_compiler_changed")
        source = bp._prepare_source(
            s,
            self.compiler,
            self.expected_spec,
            excluded_seeds=frozenset(self.recipe.get("excluded_seeds", ())),
            gae_lambda=float(self.recipe.get("gae_lambda", 0.99)),
            public_bindings_verified=True,
        )
        env = {
            k: s[k]
            for k in (
                "run",
                "training_generation",
                "run_id",
                "segment_id",
                "rows",
                "mu",
                "compiler_identity",
                "original_commit",
            )
        }
        env.update(
            payload_kind="ppo",
            actor=s["identity"].get("actor", self.recipe.get("actor", s["identity"]["intent_id"])),
            episode=s["identity"]["intent_id"],
            producer=self.recipe["producer"],
            input_spec=self.expected_spec,
            recipe_digest=self.recipe["recipe_digest"],
            use_and_seed_digest=self.recipe["use_and_seed_digest"],
        )
        encoded = encode_packet(source, env, witness=_witness(s), bindings=_bindings(s, source))
        path = self._path(s["segment_id"])
        if path.exists():
            if path.read_bytes() != encoded.packet:
                raise ValueError("binary_spool_source_changed")
        else:
            occupied = sum(p.stat().st_size for p in self.spool.glob("*.stseg"))
            if occupied + len(encoded.packet) > self.max_spool_bytes:
                raise ValueError("binary_spool_byte_limit")
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                stream.write(encoded.packet)
        return encoded

    def auxiliary(self, labels: dict[str, Any]) -> tuple[EncodedSegment, ...]:
        # Selection comes from the existing completed_auxiliary/uniform depth
        # producer. No future dataset, random choice or availability tick here.
        selected: dict[str, set[int]] = {}
        refs = list(labels.get("outcome_states", ()))
        refs += [r for b in labels.get("hp_battles", ()) for r in b["states"]]
        if not refs:
            return ()
        for item in refs:
            ref = item["source"]
            selected.setdefault(ref["segment_id"], set()).add(ref["row"])
        # Final/boundary witnesses must travel even when not selected for loss.
        for item in labels.get("segments", ()):
            selected.setdefault(item["segment_id"], set())
        packets = []
        for segment_id, rows in selected.items():
            raw = self._path(segment_id).read_bytes()
            source, header = decode_packet(raw, compiler=self.compiler)
            env = header["envelope"]
            if (
                labels["run_id"] != env["run_id"]
                or labels["training_generation"] != env["training_generation"]
                or labels.get("episode", env["episode"]) != env["episode"]
            ):
                raise ValueError("binary_late_label_generation_changed")
            for ref in (r["source"] for r in refs if r["source"]["segment_id"] == segment_id):
                auxiliary_frame(source, ref)
            if not rows:
                # A witness-only source is represented by one real row; no
                # invented frame or target, and never admitted to the aux pool.
                rows = {len(source.frames) - 1}
            frames = tuple(f if i in rows else None for i, f in enumerate(source.frames))
            aux = dc.replace(source, frames=frames)
            aux_env = {
                k: env[k]
                for k in (
                    "run",
                    "training_generation",
                    "actor",
                    "episode",
                    "run_id",
                    "producer",
                    "recipe_digest",
                    "use_and_seed_digest",
                )
            }
            aux_env.update(
                payload_kind="aux",
                rows=len(rows),
                bundle_id=_sha(json_bytes({"segment": segment_id, "labels": labels})),
                typed_original=labels.get("typed_original"),
            )
            packets.append(
                encode_packet(aux, aux_env, witness=header["witness"], bindings=header["bindings"])
            )
        return tuple(packets)
