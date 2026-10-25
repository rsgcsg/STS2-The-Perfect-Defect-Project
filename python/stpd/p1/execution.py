"""Explicit M-std execution choices; no learning, storage or scheduling authority."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, object_fields


@dataclass(frozen=True)
class MStdExecution:
    implementation: str = "reference"
    microbatch_size: int = 8
    scene_attention_area: int = 262144
    text_attention_area: int = 262144
    cpu_threads: int = 2
    defer_score_sync: bool = False

    def __post_init__(self) -> None:
        if self.implementation not in {"reference", "row-fused-v1", "experimental-packed-v1"}:
            raise BoundaryError("p1_execution", "unsupported_implementation")
        limits = ((self.microbatch_size, 1, 8),
                  (self.scene_attention_area, 1, 2**28),
                  (self.text_attention_area, 1, 2**28),
                  (self.cpu_threads, 1, 16))
        if any(type(value) is not int or not low <= value <= high
               for value, low, high in limits) or type(self.defer_score_sync) is not bool:
            raise BoundaryError("p1_execution", "invalid_execution_configuration")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def for_plan(cls, name: str) -> MStdExecution:
        """Versioned new-Run plans; the legacy constructor stays reference.

        A plan name is resolved by the admitted source and frozen in its Run.
        Existing packages/checkpoints without a plan retain their original path.
        """
        if name == "reference-v1":
            return cls()
        if name == "mstd-training-v1":
            return cls(implementation="experimental-packed-v1", defer_score_sync=True)
        raise BoundaryError("p1_execution", "unsupported_execution_plan")

    @classmethod
    def decode(cls, value: object) -> MStdExecution:
        return cls(**object_fields(value, set(cls.__dataclass_fields__), "p1_execution"))


def microbatches(frames: Any, indices: list[int], model: Any = None) -> Iterator[list[int]]:
    """Preserve ordered optimizer membership and complete oversized single frames."""
    execution = model.execution if model else MStdExecution()
    group: list[int] = []
    maximum = text_count = text_width = 0
    for index in indices:
        size = len(frames[index].entities)
        texts = {text for node in frames[index].entities for _, text in node.text}
        width = max((len(model.tokenize(text)[0]) for text in texts), default=0) if model else 0
        padded = max(maximum, size)
        text_area = (text_count + len(texts)) * max(text_width, width) ** 2
        if group and (
            len(group) == execution.microbatch_size
            or padded * padded * (len(group) + 1) > execution.scene_attention_area
            or text_area > execution.text_attention_area
        ):
            yield group
            group, maximum, text_count, text_width = [], 0, 0, 0
        group.append(index)
        maximum = max(maximum, size)
        text_count += len(texts)
        text_width = max(text_width, width)
    if group:
        yield group
