"""Small application contracts for trusted local training, independent of model runtimes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from spireagent.json_boundary import BoundaryError, digest

REQUEST_SCHEMA = "spireagent/training-request-v1"
REMOTE_REQUEST_SCHEMA = "spireagent/training-request-v2"
REMOTE_PLACEMENTS = frozenset({"modal-cpu", "modal-cuda"})
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
    # These are application cumulative caps. Remote slice/resource/cost caps are
    # separately frozen in a qualified target and may not exceed this envelope.
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
    batch_policy: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.batch_policy is not None:
            from stpd.structured_workload_contracts import StructuredBatchPolicy

            object.__setattr__(self, "batch_policy", StructuredBatchPolicy.from_dict(
                self.batch_policy).to_dict())
        if self.execution_policy is not None:
            from spireagent.workbench.trusted_recipes import checked_recipe_execution_policy

            object.__setattr__(self, "execution_policy", checked_recipe_execution_policy(
                self.recipe_id, self.execution_policy))

    def validate(self) -> None:
        digest(self.intent_id, "local_training.intent_id", length=32)
        digest(self.source_id, "local_training.source_id")
        if not isinstance(self.recipe_id, str) or not isinstance(self.config, dict):
            raise BoundaryError("local_training", "invalid_training_request")
        if (not isinstance(self.placement_id, str)
                or self.placement_id not in {LOCAL_PLACEMENT, *REMOTE_PLACEMENTS}):
            raise BoundaryError("local_training", "unsupported_placement")
        validate_limits(self.limits)
        if self.batch_policy is not None:
            from spireagent.workbench.trusted_recipes import STRUCTURED_RECIPES
            from stpd.structured_workload_contracts import StructuredBatchPolicy

            StructuredBatchPolicy.from_dict(self.batch_policy)
            if (self.placement_id not in REMOTE_PLACEMENTS
                    or self.recipe_id not in STRUCTURED_RECIPES):
                raise BoundaryError("local_training", "batch_remote_structured_placement_required")
        if self.execution_policy is not None:
            from spireagent.workbench.trusted_recipes import checked_recipe_execution_policy

            checked_recipe_execution_policy(self.recipe_id, self.execution_policy)
        if self.after_completed_operation_id is not None:
            digest(self.after_completed_operation_id,
                   "local_training.after_completed_operation_id", length=32)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema": (REQUEST_SCHEMA if self.placement_id == LOCAL_PLACEMENT
                           else REMOTE_REQUEST_SCHEMA), "intent_id": self.intent_id,
                "recipe_id": self.recipe_id, "source_id": self.source_id,
                "config": dict(self.config), "placement_id": self.placement_id,
                "limits": dict(self.limits),
                "after_completed_operation_id": self.after_completed_operation_id,
                **({"execution_policy": dict(self.execution_policy)}
                   if self.execution_policy is not None else {}),
                **({"batch_policy": dict(self.batch_policy)}
                   if self.batch_policy is not None else {})}

    @classmethod
    def from_dict(cls, value: object) -> TrainingRequest:
        required = {
            "schema", "intent_id", "recipe_id", "source_id", "config", "placement_id",
            "limits", "after_completed_operation_id",
        }
        if (not isinstance(value, dict)
                or not required <= set(value) <= required | {"execution_policy", "batch_policy"}
                or value["schema"] not in {REQUEST_SCHEMA, REMOTE_REQUEST_SCHEMA}
                or (value["schema"] == REQUEST_SCHEMA) !=
                (value.get("placement_id") == LOCAL_PLACEMENT)):
            raise BoundaryError("local_training", "invalid_training_request")
        if "execution_policy" in value:
            from spireagent.workbench.trusted_recipes import checked_recipe_execution_policy

            checked_recipe_execution_policy(value["recipe_id"], value["execution_policy"])
        result = cls(**{key: item for key, item in value.items() if key != "schema"})
        result.validate()
        return result


def checked_training_plan(value: object) -> dict[str, Any]:
    """Validate the saved domain plan's shape/digest without reading data or tensors."""
    import hashlib

    from spireagent.json_boundary import json_bytes, object_fields

    fields = {"schema", "updates_per_epoch", "epochs", "update_cap", "planned_optimizer_updates",
              "data_counts", "whole_cohort_target", "source_sha256", "plan_sha256",
              "chunk_count_per_epoch", "planned_boundaries", "distinct_N_basis", "progress_basis",
              "tbptt_advances"}
    if isinstance(value, dict) and "batch_policy" in value:
        from stpd.structured_workload_contracts import StructuredBatchPolicy

        StructuredBatchPolicy.from_dict(value["batch_policy"])
        fields.add("batch_policy")
    row = dict(object_fields(value, fields, "local_training.plan"))
    if (row["schema"] != "stpd/structured-training-plan-v1"
            or row["distinct_N_basis"] not in {
                "qualified_original_train_run_and_capture_identity",
                "qualified_original_train_run_and_source_occurrence_identity",
                "admitted_train_run_step_identity"}
            or row["progress_basis"] !=
            "actual_cumulative_optimizer_updates_label_uses_observations_boundaries"):
        raise BoundaryError("local_training", "invalid_training_plan")
    if type(row["tbptt_advances"]) is not int or row["tbptt_advances"] != 4:
        raise BoundaryError("local_training", "invalid_training_plan_credit_window")
    for name in ("updates_per_epoch", "epochs", "update_cap", "planned_optimizer_updates",
                 "chunk_count_per_epoch", "planned_boundaries"):
        if type(row[name]) is not int or not 1 <= row[name] <= 10**12:
            raise BoundaryError("local_training", "invalid_training_plan")
    counts = object_fields(row["data_counts"], {"distinct_supervised_N", "contexts",
        "unlabelled_contexts", "excluded_N", "captured_readiness_dispositions"},
        "local_training.plan")
    for name, count in counts.items():
        if name == "excluded_N" and count is None:
            continue
        if type(count) is not int or not 0 <= count <= 10**12:
            raise BoundaryError("local_training", "invalid_training_plan_data_counts")
    cohort = object_fields(row["whole_cohort_target"], {"scope", "planned"}, "local_training.plan")
    if (cohort["scope"] != "one_full_frozen_selected_TRAIN_pass"
            or type(cohort["planned"]) is not bool):
        raise BoundaryError("local_training", "invalid_training_plan_coverage")
    digest(row["source_sha256"], "local_training.plan_source")
    claimed = digest(row.pop("plan_sha256"), "local_training.plan_digest")
    if hashlib.sha256(json_bytes(row)).hexdigest() != claimed:
        raise BoundaryError("local_training", "training_plan_digest_mismatch")
    row["plan_sha256"] = claimed
    return row


def checked_training_counters(value: object) -> dict[str, int]:
    """Accepted numerical counters; unavailable current-slice work stays absent."""
    from spireagent.json_boundary import object_fields

    row = dict(object_fields(value, {"optimizer_updates", "label_uses", "observations",
        "boundaries", "epoch", "whole_cohort_passes"}, "local_training.counters"))
    if any(type(count) is not int or not 0 <= count <= 10**12 for count in row.values()):
        raise BoundaryError("local_training", "invalid_training_counters")
    return row
