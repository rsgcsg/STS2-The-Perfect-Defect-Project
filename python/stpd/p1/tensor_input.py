"""Precomputed, nonlearned Input-v2 tensors. No padding or RNG is frozen here.

Each reduction retains the reference's bucket order and actual row length. Text
and scene padding still follows the original microbatch, preserving dropout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import torch
from torch import Tensor

from .input import Frame, Role, signed_log


class FrameEncoding(Protocol):
    """The parameter-free subset used by compilation, also supplied by MStd."""

    vocabulary: dict[str, Any]

    def index(self, table: str, value: str) -> int: ...

    def tokenize(self, text: str) -> tuple[list[int], list[float]]: ...


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
    metadata_cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def text_keys(self) -> tuple[bytes, ...]:
        source = self.host or self
        if "text_keys" not in source.metadata_cache:
            source.metadata_cache["text_keys"] = text_input_keys((
                source.tensors["text_ids"], source.tensors["text_numbers"],
                source.tensors["text_lengths"]))
        return cast(tuple[bytes, ...], source.metadata_cache["text_keys"])

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
    reductions = {key: _joint_reduction(key, sources, offsets, cumulative)
                  for key in ("ids", "numbers", "text", "refs")}
    return JointNodes(tensors, reductions)


def _joint_reduction(key: str, sources: list[TensorFrame], offsets: list[dict[str, int]],
                     cumulative: dict[str, int]) -> Reduction:
    """Merge one reduction across frames with a constant number of tensor ops.

    Buckets keep first-seen width order and frame order within a width. Each
    frame's inverse is a permutation of its own packed positions, so the joint
    inverse is `position[local + base]`, where `position` maps every frame-local
    packed slot to its joint slot. This equals scattering each frame's
    `inverse.argsort()` owners, without one argsort and add per bucket.
    """
    groups: dict[int, list[tuple[Tensor, int, int, int, int]]] = {}
    inverses: list[Tensor] = []
    segments: dict[int, list[tuple[int, int]]] = {}
    for frame, offset in zip(sources, offsets, strict=True):
        reduction = frame.reductions[key]
        if key == "numbers":
            # Present values first, then missing ones after every present value.
            present = frame.tensors["numbers"].numel()
            low, high = offset["numbers"], cumulative["numbers"] + offset["missing"] - present
        else:
            present, low, high = 0, offset[key], offset[key]
        cursor = 0
        for width, count, indices in reduction.buckets:
            groups.setdefault(width, []).append((indices, count, present, low, high))
            segments.setdefault(width, []).append((offset["refs"] + cursor, count))
            cursor += count
        if cursor != reduction.inverse.numel():
            raise ValueError("joint_reduction_inverse_length")
        inverses.append(reduction.inverse + offset["refs"])
    parts = [part for width_parts in groups.values() for part in width_parts]
    raw = torch.cat([part[0] for part in parts])
    sizes = torch.tensor([part[0].numel() for part in parts], dtype=torch.long)
    columns = torch.tensor([part[2:] for part in parts], dtype=raw.dtype)
    columns = columns.reshape(len(parts), 3).repeat_interleave(sizes, dim=0)
    if key == "numbers":
        joined = torch.where(raw < columns[:, 0], raw + columns[:, 1], raw + columns[:, 2])
    else:
        joined = raw + columns[:, 1]
    pieces = joined.split([sum(part[0].numel() for part in width_parts)
                           for width_parts in groups.values()])
    # Own storage per bucket (clone), as torch.cat gave each bucket before.
    combined = [(width, sum(part[1] for part in width_parts), piece.clone())
                for (width, width_parts), piece in zip(groups.items(), pieces, strict=True)]
    starts = [start for width in groups for start, _ in segments[width]]
    counts = torch.tensor([count for width in groups for _, count in segments[width]],
                          dtype=torch.long)
    total = int(counts.sum())
    if total != cumulative["refs"]:
        raise ValueError("joint_reduction_inverse_length")
    joint_starts = torch.cumsum(counts, 0) - counts
    source = (torch.arange(total) + (torch.tensor(starts, dtype=torch.long)
                                     - joint_starts).repeat_interleave(counts))
    position = torch.empty(total, dtype=torch.long)
    position[source] = torch.arange(total)
    return Reduction(tuple(combined), position[torch.cat(inverses)])


def compile_frame(frame: Frame, model: FrameEncoding) -> TensorFrame:
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


def text_input_keys(inputs: tuple[Tensor, Tensor, Tensor]) -> tuple[bytes, ...]:
    """Exact parameter-independent text identity; never read CUDA rows to Python."""
    if any(value.device.type != "cpu" for value in inputs):
        raise ValueError("text_reuse_requires_host_input_metadata")
    ids, numbers = inputs[0].numpy(), inputs[1].numpy()
    lengths = inputs[2].tolist()
    return tuple(length.to_bytes(4, "little") + ids[i, :length].tobytes()
                 + numbers[i, :length].tobytes() for i, length in enumerate(lengths))
