"""Precomputed, nonlearned Input-v2 tensors. No padding or RNG is frozen here.

Each reduction retains the reference's bucket order and actual row length. Text
and scene padding still follows the original microbatch, preserving dropout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import torch
from torch import Tensor

from .input import Frame, Role, signed_log

if TYPE_CHECKING:
    from .model import MStd


@dataclass(frozen=True)
class Reduction:
    buckets: tuple[tuple[int, int, Tensor], ...]
    inverse: Tensor

    def state(self) -> dict[str, Any]:
        return {"buckets": self.buckets, "inverse": self.inverse}

    @classmethod
    def restore(cls, value: dict[str, Any]) -> Reduction:
        return cls(value["buckets"], value["inverse"])


def reduction(rows: list[list[int]]) -> Reduction:
    buckets: dict[int, list[int]] = {}
    for row, terms in enumerate(rows):
        buckets.setdefault(len(terms), []).append(row)
    order = [row for owners in buckets.values() for row in owners]
    inverse = [0] * len(order)
    for index, row in enumerate(order):
        inverse[row] = index
    return Reduction(tuple((width, len(owners), torch.tensor(
        [index for row in owners for index in rows[row]], dtype=torch.long))
        for width, owners in buckets.items()), torch.tensor(inverse, dtype=torch.long))


def role_rows(roles: list[Role]) -> list[list[int]]:
    return [list(role) if isinstance(role, tuple) else [role] if role >= 0 else []
            for role in roles]


@dataclass(frozen=True)
class TensorFrame:
    tensors: dict[str, Tensor]
    reductions: dict[str, Reduction]
    choices: tuple[str, ...]
    entity_count: int
    # Transient CPU metadata retained by device caches; never serialized.
    host: TensorFrame | None = field(default=None, repr=False, compare=False)

    @property
    def text_count(self) -> int:
        return self.tensors["text_ids"].shape[0]

    @property
    def text_width(self) -> int:
        return self.tensors["text_ids"].shape[1]

    def state(self) -> dict[str, Any]:
        return {"tensors": self.tensors, "reductions": {
            key: value.state() for key, value in self.reductions.items()},
            "choices": self.choices, "entity_count": self.entity_count}

    @classmethod
    def restore(cls, value: dict[str, Any]) -> TensorFrame:
        return cls(value["tensors"], {key: Reduction.restore(row)
                   for key, row in value["reductions"].items()},
                   value["choices"], value["entity_count"])


ModelFrame = Frame | TensorFrame


@dataclass(frozen=True)
class JointNodes:
    """One disjoint node graph for a microbatch, not a scene or action catalog."""
    tensors: dict[str, Tensor]
    reductions: dict[str, Reduction]


def joint_nodes(frames: list[TensorFrame]) -> JointNodes:
    """Assemble on CPU, retaining each row's mean length and local position.

    Only the current forward owns this metadata; memberships are not cached.
    Device-cached frames must retain their original host metadata so this never
    reads CUDA indices back to Python. Text vectors remain independently encoded.
    """
    sources = [frame.host or frame for frame in frames]
    if any(value.device.type != "cpu" for frame in sources for value in frame.tensors.values()):
        raise ValueError("joint_nodes_requires_host_metadata")
    names = ("identities", "identity_fields", "kinds", "positions", "number_fields",
             "numbers", "missing_fields", "text_fields")
    tensors = {key: torch.cat([frame.tensors[key] for frame in sources]) for key in names}
    offsets = []
    cumulative = dict.fromkeys(("ids", "numbers", "missing", "text", "refs", "vectors"), 0)
    for frame in sources:
        offsets.append(dict(cumulative))
        for key, count in {
            "ids": frame.tensors["identities"].numel(),
            "numbers": frame.tensors["numbers"].numel(),
            "missing": frame.tensors["missing_fields"].numel(),
            "text": frame.tensors["text_fields"].numel(),
            "refs": frame.entity_count, "vectors": frame.text_count,
        }.items():
            cumulative[key] += count
    tensors["text_indices"] = torch.cat([
        frame.tensors["text_indices"] + offset["vectors"]
        for frame, offset in zip(sources, offsets, strict=True)])
    reductions = {}
    for key in ("ids", "numbers", "text", "refs"):
        buckets: dict[int, list[tuple[int, Tensor, Tensor]]] = {}
        for frame, offset in zip(sources, offsets, strict=True):
            reduction = frame.reductions[key]
            packed_owners = reduction.inverse.argsort()
            cursor = 0
            for width, count, indices in reduction.buckets:
                owners = packed_owners[cursor:cursor + count] + offset["refs"]
                cursor += count
                if key == "numbers":
                    present = frame.tensors["numbers"].numel()
                    indices = torch.where(indices < present, indices + offset["numbers"],
                                          indices - present + cumulative["numbers"]
                                          + offset["missing"])
                else:
                    indices = indices + offset[key]
                buckets.setdefault(width, []).append((count, indices, owners))
        combined = []
        order: list[Tensor] = []
        for width, parts in buckets.items():
            combined.append((width, sum(part[0] for part in parts),
                             torch.cat([part[1] for part in parts])))
            order.extend(part[2] for part in parts)
        inverse = torch.empty(cumulative["refs"], dtype=torch.long)
        inverse[torch.cat(order)] = torch.arange(cumulative["refs"])
        reductions[key] = Reduction(tuple(combined), inverse)
    return JointNodes(tensors, reductions)


def compile_frame(frame: Frame, model: MStd) -> TensorFrame:
    from .input import is_v2_encoding

    if not is_v2_encoding(model.vocabulary.get("input_spec")):
        raise ValueError("tensor_input_requires_v2")
    entities = frame.entities
    texts = sorted({text for node in entities for _, text in node.text})
    tokens = [model.tokenize(text) for text in texts]
    width = max((len(ids) for ids, _ in tokens), default=0)
    tensors = {
        "text_ids": torch.tensor([ids + [0] * (width - len(ids)) for ids, _ in tokens],
                                 dtype=torch.long).reshape(len(tokens), width),
        "text_numbers": torch.tensor([nums + [0.] * (width - len(nums)) for _, nums in tokens],
                                     dtype=torch.float32).reshape(len(tokens), width, 1),
        "text_lengths": torch.tensor([len(ids) for ids, _ in tokens], dtype=torch.long),
    }
    id_pairs = [(row, key, value) for row, e in enumerate(entities) for key, value in e.identities]
    id_rows: list[list[int]] = [[] for _ in entities]
    for i, (row, _, _) in enumerate(id_pairs):
        id_rows[row].append(i)
    present: list[tuple[int, float]] = []
    missing: list[int] = []
    number_rows: list[list[int]] = [[] for _ in entities]
    for row, entity in enumerate(entities):
        for key, number in entity.numeric:
            if number is None:
                number_rows[row].append(-len(missing) - 1)
                missing.append(model.index("fields", key))
            else:
                number_rows[row].append(len(present))
                present.append((model.index("fields", key), signed_log(number)))
    number_rows = [[i if i >= 0 else len(present) - i - 1 for i in row] for row in number_rows]
    text_rows: list[list[int]] = [[] for _ in entities]
    text_fields: list[int] = []
    text_indices: list[int] = []
    lookup = {text: i for i, text in enumerate(texts)}
    for row, entity in enumerate(entities):
        for key, text in entity.text:
            text_rows[row].append(len(text_fields))
            text_fields.append(model.index("fields", key))
            text_indices.append(lookup[text])
    for key, values in {
        "identities": [model.index("ids", value) for _, _, value in id_pairs],
        "identity_fields": [model.index("fields", key) for _, key, _ in id_pairs],
        "kinds": [model.index("kinds", e.kind) for e in entities],
        "positions": [e.position for e in entities],
        "number_fields": [key for key, _ in present],
        "missing_fields": missing, "text_fields": text_fields, "text_indices": text_indices,
        "actions": [model.index("actions", c["kind"]) for c in frame.choices],
    }.items():
        tensors[key] = torch.tensor(values, dtype=torch.long)
    tensors["numbers"] = torch.tensor([value for _, value in present], dtype=torch.float32)
    reductions = {key: reduction(rows) for key, rows in {
        "ids": id_rows, "numbers": number_rows, "text": text_rows,
        "refs": [e.refs for e in entities],
        "source": role_rows([s for s, _ in frame.roles]),
        "target": role_rows([t for _, t in frame.roles]),
    }.items()}
    return TensorFrame(tensors, reductions, tuple(c["choice_id"] for c in frame.choices),
                       len(entities))


def text_batch(frames: list[TensorFrame]) -> tuple[Tensor, Tensor, Tensor]:
    width = max(frame.text_width for frame in frames)
    return (torch.cat([torch.nn.functional.pad(f.tensors["text_ids"],
                      (0, width - f.text_width)) for f in frames]),
            torch.cat([torch.nn.functional.pad(f.tensors["text_numbers"],
                      (0, 0, 0, width - f.text_width)) for f in frames]),
            torch.cat([f.tensors["text_lengths"] for f in frames]))
