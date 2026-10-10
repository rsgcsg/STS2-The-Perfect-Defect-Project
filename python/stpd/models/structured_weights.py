"""One safe structured-weight codec and exact shape verifier for trusted profiles."""

from __future__ import annotations

from typing import Any

import torch

from spireagent.json_boundary import BoundaryError, object_fields

from ..native_graph_spec import NativeGraphControl
from ..workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from .structured_m2 import StructuredM2


def encode_structured_weights(
    model: StructuredM2, *, schema: str, graph: dict[str, Any], projection: dict[str, Any]
) -> bytes:
    model.validate_parameters()
    return encode_checkpoint(
        {
            "schema": schema,
            "graph": graph,
            "projection": projection,
            "seed": model.seed,
            "state_dict": dict(model.state_dict()),
        }
    )


def load_structured_weights(
    raw: bytes, *, schema: str, graph: dict[str, Any], projection: dict[str, Any], seed: int,
    model_control: NativeGraphControl | None = None
) -> StructuredM2:
    """Schema/graph/projection expectations come from trusted code, never artifacts."""
    decoded = object_fields(
        decode_checkpoint(raw),
        {"schema", "graph", "projection", "seed", "state_dict"},
        "structured_package.payload",
    )
    if (
        decoded["schema"] != schema
        or decoded["graph"] != graph
        or decoded["projection"] != projection
        or decoded["seed"] != seed
        or not isinstance(decoded["state_dict"], dict)
    ):
        raise BoundaryError("structured_package", "weights_identity_mismatch")
    model = StructuredM2(seed=seed, model_control=model_control)
    expected = model.state_dict()
    actual = decoded["state_dict"]
    if set(expected) != set(actual) or any(
        not isinstance(actual[key], torch.Tensor)
        or actual[key].dtype != tensor.dtype
        or actual[key].shape != tensor.shape
        for key, tensor in expected.items()
    ):
        raise BoundaryError("structured_package", "state_dict_shape_or_type")
    model.load_state_dict(actual, strict=True)
    model.validate_parameters()
    return model.eval()
