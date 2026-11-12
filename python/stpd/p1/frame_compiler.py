"""Weight-free public-input compilation and a bound, pickle-free TensorFrame wire.

The existing projector and compile_frame remain the only encoding implementation.
Decoding checks identities, input hashes and tensor structure without projecting or
compiling the public observation again. This is derived input, not a legality or
trajectory-verification authority.
"""
from __future__ import annotations

import base64
import binascii
import copy
import functools
import gzip
import io
import json
import zlib
from pathlib import Path
from typing import Any, NoReturn

import safetensors
import tokenizers
import torch
from torch import Tensor

from spireagent.json_boundary import BoundaryError, object_fields
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

from .data import digest, json_bytes
from .input import is_v2_encoding, project, validate_input_spec
from .input_encoding import InputEncoding
from .tensor_input import TensorFrame, compile_frame
from .vocabulary import validate_vocabulary

SCHEMA = "stpd/p1-compiled-frame-1"
ENCODING = "stpd-tensor-tree-gzip-base64-1"
MAX_FRAME_BYTES = 64 * 1024 * 1024
CODE_FILES = ("frame_compiler.py", "input_encoding.py", "tensor_input.py", "input.py",
              "input_order.py", "input_identity.py", "vocabulary.py", "data.py",
              "public_vocabulary_v1.json")
_INTEGER_TENSORS = {
    "text_ids", "text_lengths", "identities", "identity_fields", "kinds", "positions",
    "number_fields", "missing_fields", "text_fields", "text_indices", "actions",
}
_REDUCTIONS = {"ids", "numbers", "text", "refs", "source", "target"}


def _reject(code: str) -> NoReturn:
    raise BoundaryError("p1_frame_compiler", code)


@functools.cache
def _compiler_source_digests() -> tuple[tuple[str, str], ...]:
    # Read once per process: the source files of the running process. s1g/h100b: a
    # per-mu-load reread of these files on NFS sat > 120 s in an open (D state) and the
    # follower watchdog stopped the run.
    root = Path(__file__).parent
    sources = {name: digest((root / name).read_bytes()) for name in CODE_FILES}
    sources["checkpoint_codec.py"] = digest(
        (root.parent / "workers/checkpoint_codec.py").read_bytes())
    platform = root.parent.parent / "spireagent"
    for name in ("json_boundary.py", "encoding.py"):
        sources["spireagent/" + name] = digest((platform / name).read_bytes())
    sources["uv.lock"] = digest((root.parents[1] / "uv.lock").read_bytes())
    return tuple(sources.items())


def compiler_digest() -> str:
    """Bind actual encoding/codec sources and dependency versions, never theta.

    The source files are read once per process; the dependency versions are read
    on every call (no file access)."""
    return digest(json_bytes({"sources": dict(_compiler_source_digests()),
                              "tokenizers": tokenizers.__version__,
                              "torch": torch.__version__, "safetensors": safetensors.__version__}))


def observation_hashes(observation: dict[str, Any]) -> dict[str, str]:
    """Bind the complete supplied observation and ordered original catalog."""
    if not isinstance(observation, dict) or not isinstance(observation.get("catalog"), list):
        _reject("public_observation_and_catalog_required")
    if any(not isinstance(choice, dict) or not isinstance(choice.get("choice_id"), str)
           for choice in observation["catalog"]):
        _reject("invalid_public_catalog")
    try:
        return {"observation_sha256": digest(json_bytes(observation)),
                "catalog_sha256": digest(json_bytes(observation["catalog"]))}
    except (TypeError, ValueError) as error:
        raise BoundaryError("p1_frame_compiler", "invalid_public_json") from error


class FrameCompiler:
    """An independent CPU encoder; owns no MStd, parameters or shared cache."""

    def __init__(self, vocabulary: dict[str, Any]) -> None:
        if not isinstance(vocabulary, dict):
            _reject("invalid_vocabulary_or_input_spec")
        vocabulary = copy.deepcopy(vocabulary)
        try:
            projection = validate_input_spec(vocabulary.get("input_spec"))
            if not is_v2_encoding(projection):
                _reject("v2_input_required")
            validate_vocabulary(vocabulary)
            self._encoding = InputEncoding(vocabulary)
        except (TypeError, KeyError, ValueError) as error:
            if isinstance(error, BoundaryError):
                raise
            raise BoundaryError("p1_frame_compiler", "invalid_vocabulary_or_input_spec") from error
        self._projection = copy.deepcopy(projection)
        self._identity = json_bytes({"input_spec": self._projection,
            "vocabulary_sha256": digest(json_bytes(vocabulary)),
            "compiler_sha256": compiler_digest()})

    @property
    def identity(self) -> dict[str, Any]:
        return dict(json.loads(self._identity))

    def compile(self, observation: dict[str, Any]) -> TensorFrame:
        return compile_frame(project(observation, input_projection=self._projection),
                             self._encoding)

    def encode(self, observation: dict[str, Any]) -> dict[str, Any]:
        """Compile once; carry only typed TensorFrame state and its bindings."""
        bindings = observation_hashes(observation)
        frame = self.compile(observation)
        raw = encode_checkpoint(frame.state())
        if len(raw) > MAX_FRAME_BYTES:
            _reject("frame_size_limit")
        compressed = gzip.compress(raw, compresslevel=1, mtime=0)
        body = {"schema": SCHEMA, "compiler": self.identity, **bindings,
                "encoding": ENCODING, "frame_sha256": digest(raw),
                "data": base64.b64encode(compressed).decode("ascii")}
        return {**body, "record_sha256": digest(json_bytes(body))}

    def decode(self, value: object, *, observation: dict[str, Any] | None = None,
               observation_sha256: str | None = None,
               catalog_sha256: str | None = None,
               expected_identity: dict[str, Any] | None = None) -> TensorFrame:
        """Hash-check raw input when present; never project/tokenize/recompile it.

        Callers with an already verified source can pass its two expected hashes
        instead. Omitting them verifies the self-contained wire bindings only.
        An explicitly frozen producer identity may differ from this consumer's
        runtime build, but must use this exact InputSpec and vocabulary. Encoding
        always records the actual local compiler identity.
        """
        wire = object_fields(value, {"schema", "compiler", "observation_sha256",
            "catalog_sha256", "encoding", "frame_sha256", "data", "record_sha256"},
            "p1_frame_compiler")
        if wire["schema"] != SCHEMA or wire["encoding"] != ENCODING:
            _reject("unsupported_frame_encoding")
        expected_compiler = self.identity if expected_identity is None else object_fields(
            expected_identity, {"input_spec", "vocabulary_sha256", "compiler_sha256"},
            "p1_frame_compiler")
        if any(expected_compiler[key] != self.identity[key]
               for key in ("input_spec", "vocabulary_sha256")):
            _reject("compiler_vocabulary_mismatch")
        if wire["compiler"] != expected_compiler:
            _reject("compiler_identity_mismatch")
        body = {key: entry for key, entry in wire.items() if key != "record_sha256"}
        try:
            if digest(json_bytes(body)) != wire["record_sha256"]:
                _reject("wire_hash_mismatch")
        except (TypeError, ValueError) as error:
            if isinstance(error, BoundaryError):
                raise
            raise BoundaryError("p1_frame_compiler", "invalid_wire_json") from error
        bindings = observation_hashes(observation) if observation is not None else {}
        for key, expected in (("observation_sha256", observation_sha256),
                              ("catalog_sha256", catalog_sha256)):
            actual = wire[key]
            if (not isinstance(actual, str) or len(actual) != 64
                    or any(character not in "0123456789abcdef" for character in actual)):
                _reject("invalid_input_digest")
            if (expected is not None and expected != actual
                    or key in bindings and bindings[key] != actual):
                _reject("public_input_hash_mismatch")
        encoded = wire["data"]
        if not isinstance(encoded, str) or len(encoded) > MAX_FRAME_BYTES * 2:
            _reject("frame_size_limit")
        try:
            compressed = base64.b64decode(encoded, validate=True)
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                raw = stream.read(MAX_FRAME_BYTES + 1)
        except (ValueError, binascii.Error, OSError, EOFError, zlib.error) as error:
            raise BoundaryError("p1_frame_compiler", "invalid_frame_payload") from error
        if len(raw) > MAX_FRAME_BYTES:
            _reject("frame_size_limit")
        if digest(raw) != wire["frame_sha256"]:
            _reject("frame_hash_mismatch")
        state = decode_checkpoint(raw)
        self._validate_state(state)
        frame = TensorFrame.restore(state)
        if observation is not None and frame.choices != tuple(
                choice.get("choice_id") for choice in observation["catalog"]):
            _reject("catalog_choice_binding_mismatch")
        return frame

    def _validate_state(self, state: dict[str, Any]) -> None:
        """Validate the real indexing/shape boundary before MStd consumes tensors."""
        state = object_fields(state, {"tensors", "reductions", "choices", "entity_count"},
                              "p1_frame_compiler")
        tensors = object_fields(state["tensors"], _INTEGER_TENSORS | {"numbers", "text_numbers"},
                                "p1_frame_compiler")
        reductions = object_fields(state["reductions"], _REDUCTIONS, "p1_frame_compiler")
        choices, count = state["choices"], state["entity_count"]
        if (type(count) is not int or count < 1 or not isinstance(choices, tuple)
                or any(not isinstance(choice, str) for choice in choices)
                or len(set(choices)) != len(choices)):
            _reject("invalid_frame_structure")
        for key, tensor in tensors.items():
            dtype = torch.long if key in _INTEGER_TENSORS else torch.float32
            rank = 2 if key == "text_ids" else 3 if key == "text_numbers" else 1
            if (not isinstance(tensor, Tensor) or tensor.dtype != dtype
                    or tensor.device.type != "cpu" or tensor.ndim != rank):
                _reject("invalid_frame_tensor")
        text_count, width = tensors["text_ids"].shape
        lengths = tensors["text_lengths"]
        if (tensors["text_numbers"].shape != (text_count, width, 1)
                or lengths.numel() != text_count
                or bool(((lengths < 1) | (lengths > width)).any())):
            _reject("invalid_text_lengths")
        for key, size in {"kinds": count, "positions": count, "actions": len(choices),
                          "identity_fields": tensors["identities"].numel(),
                          "number_fields": tensors["numbers"].numel(),
                          "text_indices": tensors["text_fields"].numel()}.items():
            if tensors[key].numel() != size:
                _reject("frame_length_mismatch")
        vocabulary = self._encoding.vocabulary
        bounds = {"identities": len(vocabulary["ids"]), "kinds": len(vocabulary["kinds"]),
                  "actions": len(vocabulary["actions"]), "text_ids": 4096,
                  "text_indices": text_count}
        bounds.update(dict.fromkeys(("identity_fields", "number_fields", "missing_fields",
                                     "text_fields"), len(vocabulary["fields"])))
        for key, bound in bounds.items():
            self._indices(tensors[key], bound)
        if bool((tensors["positions"] < 0).any()):
            _reject("invalid_frame_index")
        reduction_bounds = {"ids": tensors["identities"].numel(),
            "numbers": tensors["numbers"].numel() + tensors["missing_fields"].numel(),
            "text": tensors["text_fields"].numel(), "refs": count,
            "source": count, "target": count}
        for key, value in reductions.items():
            value = object_fields(value, {"buckets", "inverse"}, "p1_frame_compiler")
            buckets, inverse = value["buckets"], value["inverse"]
            owners = len(choices) if key in {"source", "target"} else count
            if (not isinstance(buckets, tuple) or not isinstance(inverse, Tensor)
                    or inverse.dtype != torch.long or inverse.ndim != 1
                    or not torch.equal(inverse.sort().values, torch.arange(owners))):
                _reject("invalid_reduction_structure")
            total, widths = 0, set()
            for bucket in buckets:
                if not isinstance(bucket, tuple) or len(bucket) != 3:
                    _reject("invalid_reduction_structure")
                width, rows, indices = bucket
                if (type(width) is not int or width < 0 or width in widths
                        or type(rows) is not int or rows < 1 or not isinstance(indices, Tensor)
                        or indices.dtype != torch.long or indices.ndim != 1
                        or indices.numel() != width * rows):
                    _reject("invalid_reduction_structure")
                self._indices(indices, reduction_bounds[key])
                widths.add(width)
                total += rows
            if total != owners:
                _reject("reduction_length_mismatch")

    @staticmethod
    def _indices(value: Tensor, bound: int) -> None:
        if bool(((value < 0) | (value >= bound)).any()):
            _reject("invalid_frame_index")
