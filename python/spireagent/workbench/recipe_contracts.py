"""Small application contracts for trusted local training, independent of model runtimes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from spireagent.json_boundary import BoundaryError, digest

REQUEST_SCHEMA = "spireagent/training-request-v1"
SNAPSHOT_SCHEMA = "spireagent/training-operation-snapshot-v1"
LOCAL_PLACEMENT = "local-cpu"
WALL_AMENDMENT_SCHEMA = "spireagent/training-wall-budget-amendment-v1"


def effective_training_limits(operation: dict[str, Any]) -> dict[str, int]:
    """Derive the cap from the original request and its validated amendment chain."""
    limits = validate_limits(operation["request"]["limits"])
    amendments = operation.get("budget_amendments", [])
    if not isinstance(amendments, list) or len(amendments) > 32:
        raise BoundaryError("local_training", "invalid_budget_amendments")
    attempts = {operation["attempt_id"], *(item["attempt_id"]
                                         for item in operation["attempts"])}
    intents: set[str] = set()
    elapsed, accepted = 0.0, 0.0
    fields = {"schema", "intent_id", "operation_id", "attempt_id", "prior_wall_seconds",
              "wall_seconds", "charged_elapsed_seconds", "accepted_at"}
    for record in amendments:
        if not isinstance(record, dict) or set(record) != fields or not limits:
            raise BoundaryError("local_training", "invalid_budget_amendments")
        intent = digest(record["intent_id"], "local_training.amendment_intent", length=32)
        checked = validate_limits({"wall_seconds": record["wall_seconds"],
                                   "scratch_bytes": limits["scratch_bytes"]})
        charged, stamp = record["charged_elapsed_seconds"], record["accepted_at"]
        if (record["schema"] != WALL_AMENDMENT_SCHEMA or intent in intents
                or record["operation_id"] != operation["operation_id"]
                or record["attempt_id"] not in attempts
                or type(record["prior_wall_seconds"]) is not int
                or record["prior_wall_seconds"] != limits["wall_seconds"]
                or checked["wall_seconds"] <= limits["wall_seconds"]
                or type(charged) not in {int, float} or not math.isfinite(charged)
                or not elapsed <= charged <= operation["elapsed_seconds"]
                or charged >= checked["wall_seconds"]
                or type(stamp) not in {int, float} or not math.isfinite(stamp)
                or stamp <= 0 or stamp < accepted):
            raise BoundaryError("local_training", "invalid_budget_amendments")
        limits = checked
        intents.add(intent)
        elapsed, accepted = charged, stamp
    return limits


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
    execution_policy: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.execution_policy is not None:
            from spireagent.workbench.trusted_recipes import checked_recipe_execution_policy

            object.__setattr__(self, "execution_policy", checked_recipe_execution_policy(
                self.recipe_id, self.execution_policy))

    def validate(self) -> None:
        digest(self.intent_id, "local_training.intent_id", length=32)
        digest(self.source_id, "local_training.source_id")
        if not isinstance(self.recipe_id, str) or not isinstance(self.config, dict):
            raise BoundaryError("local_training", "invalid_training_request")
        if self.placement_id != LOCAL_PLACEMENT:
            raise BoundaryError("local_training", "unsupported_placement")
        validate_limits(self.limits)
        if self.execution_policy is not None:
            from spireagent.workbench.trusted_recipes import checked_recipe_execution_policy

            checked_recipe_execution_policy(self.recipe_id, self.execution_policy)
        if self.after_completed_operation_id is not None:
            digest(self.after_completed_operation_id,
                   "local_training.after_completed_operation_id", length=32)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema": REQUEST_SCHEMA, "intent_id": self.intent_id,
                "recipe_id": self.recipe_id, "source_id": self.source_id,
                "config": dict(self.config), "placement_id": self.placement_id,
                "limits": dict(self.limits),
                "after_completed_operation_id": self.after_completed_operation_id,
                **({"execution_policy": dict(self.execution_policy)}
                   if self.execution_policy is not None else {})}

    @classmethod
    def from_dict(cls, value: object) -> TrainingRequest:
        required = {
            "schema", "intent_id", "recipe_id", "source_id", "config", "placement_id",
            "limits", "after_completed_operation_id",
        }
        if (not isinstance(value, dict)
                or set(value) not in (required, required | {"execution_policy"})
                or value["schema"] != REQUEST_SCHEMA):
            raise BoundaryError("local_training", "invalid_training_request")
        if "execution_policy" in value:
            from spireagent.workbench.trusted_recipes import checked_recipe_execution_policy

            checked_recipe_execution_policy(value["recipe_id"], value["execution_policy"])
        result = cls(**{key: item for key, item in value.items() if key != "schema"})
        result.validate()
        return result
