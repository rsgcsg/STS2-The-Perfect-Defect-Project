"""Small application contracts for trusted local training, independent of model runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from spireagent.json_boundary import BoundaryError, digest

REQUEST_SCHEMA = "spireagent/training-request-v1"
SNAPSHOT_SCHEMA = "spireagent/training-operation-snapshot-v1"
LOCAL_PLACEMENT = "local-cpu"


def validate_limits(value: object) -> dict[str, int]:
    # This service has no paid or remote execution adapter. Do not advertise
    # unenforced memory, scratch or provider limits as executable capabilities.
    if value == {}:
        return {}
    if (not isinstance(value, dict) or "wall_seconds" not in value
            or set(value) - {"wall_seconds", "scratch_bytes"}):
        raise BoundaryError("local_training", "unsupported_resource_limits")
    seconds = value["wall_seconds"]
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise BoundaryError("local_training", "invalid_wall_seconds")
    scratch = value.get("scratch_bytes", 512 * 1024 * 1024)
    if type(scratch) is not int or not 16 * 1024 * 1024 <= scratch <= 1024 * 1024 * 1024:
        raise BoundaryError("local_training", "invalid_scratch_bytes")
    return {"wall_seconds": seconds, "scratch_bytes": scratch}


@dataclass(frozen=True)
class TrainingRequest:
    intent_id: str
    recipe_id: str
    source_id: str
    config: dict[str, Any]
    placement_id: str = LOCAL_PLACEMENT
    limits: dict[str, int] = field(default_factory=lambda: {"wall_seconds": 600})
    after_completed_operation_id: str | None = None

    def validate(self) -> None:
        digest(self.intent_id, "local_training.intent_id", length=32)
        digest(self.source_id, "local_training.source_id")
        if not isinstance(self.recipe_id, str) or not isinstance(self.config, dict):
            raise BoundaryError("local_training", "invalid_training_request")
        if self.placement_id != LOCAL_PLACEMENT:
            raise BoundaryError("local_training", "unsupported_placement")
        validate_limits(self.limits)
        if self.after_completed_operation_id is not None:
            digest(self.after_completed_operation_id,
                   "local_training.after_completed_operation_id", length=32)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema": REQUEST_SCHEMA, "intent_id": self.intent_id,
                "recipe_id": self.recipe_id, "source_id": self.source_id,
                "config": dict(self.config), "placement_id": self.placement_id,
                "limits": dict(self.limits),
                "after_completed_operation_id": self.after_completed_operation_id}

    @classmethod
    def from_dict(cls, value: object) -> TrainingRequest:
        if not isinstance(value, dict) or set(value) != {
            "schema", "intent_id", "recipe_id", "source_id", "config", "placement_id",
            "limits", "after_completed_operation_id",
        } or value["schema"] != REQUEST_SCHEMA:
            raise BoundaryError("local_training", "invalid_training_request")
        result = cls(**{key: item for key, item in value.items() if key != "schema"})
        result.validate()
        return result
