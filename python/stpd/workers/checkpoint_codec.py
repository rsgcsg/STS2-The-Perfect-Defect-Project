"""Bounded typed checkpoint trees over safetensors; never deserialize Python pickle."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import torch
from safetensors.torch import load, save
from torch import Tensor

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes, object_fields

MAGIC = b"STPDCKP1"
MAX_BYTES = 512 * 1024 * 1024
MAX_HEADER = 8 * 1024 * 1024
MAX_NODES = 100_000


def encode_checkpoint(state: Mapping[str, Any]) -> bytes:
    tensors: dict[str, Tensor] = {}
    nodes = 0

    def encode(value: Any, depth: int = 0) -> dict[str, Any]:
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > MAX_NODES:
            raise BoundaryError("checkpoint", "structure_limit")
        if isinstance(value, Tensor):
            if value.layout != torch.strided or not bool(torch.isfinite(value).all()):
                raise BoundaryError("checkpoint", "unsupported_or_non_finite_tensor")
            name = f"t{len(tensors):08d}"
            tensors[name] = value.detach().cpu().contiguous()
            return {"kind": "tensor", "value": name}
        if isinstance(value, Mapping):
            entries = []
            for key, item in value.items():
                if type(key) not in {str, int}:
                    raise BoundaryError("checkpoint", "unsupported_mapping_key")
                entries.append([key, encode(item, depth + 1)])
            return {"kind": "mapping", "value": entries}
        if isinstance(value, (list, tuple)):
            return {
                "kind": "tuple" if isinstance(value, tuple) else "list",
                "value": [encode(item, depth + 1) for item in value],
            }
        if value is None or type(value) in {bool, int, float, str}:
            return {"kind": "scalar", "value": value}
        raise BoundaryError("checkpoint", "unsupported_value_type")

    header = json_bytes({"schema": "stpd/tensor-tree-v1", "tree": encode(state)})
    if len(header) > MAX_HEADER:
        raise BoundaryError("checkpoint", "header_limit")
    encoded = MAGIC + len(header).to_bytes(8, "little") + header + save(tensors)
    if len(encoded) > MAX_BYTES:
        raise BoundaryError("checkpoint", "payload_limit")
    return encoded


def decode_checkpoint(raw: bytes) -> dict[str, Any]:
    if len(raw) < 16 or len(raw) > MAX_BYTES or raw[:8] != MAGIC:
        raise BoundaryError("checkpoint", "unsupported_format_or_size")
    size = int.from_bytes(raw[8:16], "little")
    if size > MAX_HEADER or size < 1 or size + 16 >= len(raw):
        raise BoundaryError("checkpoint", "header_limit")
    header_raw = raw[16 : 16 + size]
    header = object_fields(decode_json(header_raw), {"schema", "tree"}, "checkpoint")
    if header["schema"] != "stpd/tensor-tree-v1" or header_raw != json_bytes(header):
        raise BoundaryError("checkpoint", "header_mismatch")
    try:
        tensors = load(raw[16 + size :])
    except Exception as error:
        raise BoundaryError("checkpoint", "invalid_tensor_payload") from error
    used: set[str] = set()
    nodes = 0

    def decode(value: object, depth: int = 0) -> Any:
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > MAX_NODES:
            raise BoundaryError("checkpoint", "structure_limit")
        node = object_fields(value, {"kind", "value"}, "checkpoint.node")
        kind, item = node["kind"], node["value"]
        if kind == "tensor":
            if not isinstance(item, str) or item not in tensors or item in used:
                raise BoundaryError("checkpoint", "tensor_reference_mismatch")
            tensor = tensors[item]
            if not bool(torch.isfinite(tensor).all()):
                raise BoundaryError("checkpoint", "non_finite_tensor")
            used.add(item)
            return tensor
        if kind == "scalar":
            if item is not None and type(item) not in {bool, int, float, str}:
                raise BoundaryError("checkpoint", "invalid_scalar")
            return item
        if kind in {"list", "tuple"} and isinstance(item, list):
            values = [decode(entry, depth + 1) for entry in item]
            return tuple(values) if kind == "tuple" else values
        if kind == "mapping" and isinstance(item, list):
            mapping: dict[Any, Any] = {}
            for entry in item:
                if (
                    not isinstance(entry, list)
                    or len(entry) != 2
                    or type(entry[0]) not in {str, int}
                    or entry[0] in mapping
                ):
                    raise BoundaryError("checkpoint", "invalid_mapping_entry")
                mapping[entry[0]] = decode(entry[1], depth + 1)
            return mapping
        raise BoundaryError("checkpoint", "unknown_node_kind")

    result = decode(header["tree"])
    if not isinstance(result, dict) or set(tensors) != used:
        raise BoundaryError("checkpoint", "tensor_inventory_mismatch")
    return result
