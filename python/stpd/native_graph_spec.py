"""Four trusted native memory controls; data never chooses executable behavior.

The historical model uses None. These explicit controls have separate identities,
including K1 carry, so old packages and numerical checkpoints keep their meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes, object_fields

TRAINING_SCOPE = "native-structured-graph-training-code-closure-v1"
RUN_SCHEMA = "stpd/native-structured-m2-run-v2"
CHECKPOINT_SCHEMA = "stpd/native-structured-m2-training-checkpoint-v2"
MODEL_SCHEMA = "stpd/native-structured-m2-model-v2"
REPORT_SCHEMA = "stpd/native-structured-m2-training-report-v2"


@dataclass(frozen=True)
class GraphSpec:
    slots: int = 1
    width: int = 96

    def validate(self) -> None:
        if type(self.slots) is not int or self.slots not in (1, 8) or self.width != 96 or type(
            self.width
        ) is not int:
            raise BoundaryError("native_graph", "unsupported_graph")


@dataclass(frozen=True)
class ResetSpec:
    mode: str = "carry"

    def validate(self) -> None:
        if self.mode not in ("carry", "reset_before_each_actual_advance"):
            raise BoundaryError("native_graph", "unsupported_reset")


@dataclass(frozen=True)
class NativeGraphControl:
    graph: GraphSpec = GraphSpec()
    reset: ResetSpec = ResetSpec()

    def validate(self) -> None:
        if type(self.graph) is not GraphSpec or type(self.reset) is not ResetSpec:
            raise BoundaryError("native_graph", "typed_control_required")
        self.graph.validate()
        self.reset.validate()

    @property
    def id(self) -> str:
        self.validate()
        reset = "carry" if self.reset.mode == "carry" else "reset-each-advance"
        return f"stpd.native-m2.k{self.graph.slots}d96.{reset}.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "graph": {"slots": self.graph.slots, "width": self.graph.width},
            "reset": {"mode": self.reset.mode},
        }


PRESETS = tuple(
    NativeGraphControl(GraphSpec(slots), ResetSpec(mode))
    for slots in (1, 8)
    for mode in ("carry", "reset_before_each_actual_advance")
)


def checked_control(value: object) -> NativeGraphControl:
    value = object_fields(value, {"id", "graph", "reset"}, "native_graph.control")
    for preset in PRESETS:
        # Canonical comparison keeps bools/floats distinct from integer controls.
        if json_bytes(value) == json_bytes(preset.to_dict()):
            return preset
    raise BoundaryError("native_graph", "unsupported_control")


def optional_control(value: object) -> NativeGraphControl | None:
    if value is None:
        return None
    if type(value) is not NativeGraphControl:
        raise BoundaryError("native_graph", "typed_control_required")
    value.validate()
    return value


def control_from_identity(identity: dict[str, Any]) -> NativeGraphControl | None:
    """A closed preset only; complete identity equality is checked by the engine."""
    if not isinstance(identity, dict):
        raise BoundaryError("native_graph", "execution_identity_required")
    return checked_control(identity["model_control"]) if "model_control" in identity else None
