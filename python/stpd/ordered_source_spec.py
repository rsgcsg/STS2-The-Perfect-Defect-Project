"""Closed Source3 recorded-capture recipe contracts; discovery imports no tensors.

These are research representations, not Source/Connector authority or permission.
Old synthetic native formats keep their original schemas and source restrictions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields

from .canonical import semantic_hash
from .fullrun.native_structured_inputs import INPUT_SPEC
from .native_sampled_carry_spec import (
    INPUT_SPEC as SAMPLED_INPUT_SPEC,
)
from .native_sampled_carry_spec import (
    RECIPE as SAMPLED_RECIPE,
)
from .native_sampled_carry_spec import (
    RESET_REASONS,
)
from .native_sampled_carry_spec import (
    VIEW as SAMPLED_VIEW,
)
from .policy.native_task import ready_summary_task_spec

if TYPE_CHECKING:
    from .native_graph_spec import NativeGraphControl

RAW_SCHEMA = "stpd/source3-original-bundle-v1"
ADMISSION_SCHEMA = "stpd/source3-ordered-admission-v1"
PARTITION_SCHEMA = "stpd/source3-ordered-partition-v1"
SOURCE_SCHEMA = "stpd/source3-ordered-native-training-source-v1"
INPUT_SCHEMA = "stpd/source3-ordered-native-training-input-v1"
RUN_SCHEMA = "stpd/source3-ordered-native-m2-run-v1"
CHECKPOINT_SCHEMA = "stpd/source3-ordered-native-m2-checkpoint-v1"
MODEL_SCHEMA = "stpd/source3-ordered-native-m2-model-v1"
REPORT_SCHEMA = "stpd/source3-ordered-native-m2-report-v1"
EVALUATION_INPUT_SCHEMA = "stpd/source3-fixed-native-evaluation-input-v1"
EVALUATION_REPORT_SCHEMA = "stpd/source3-fixed-native-evaluation-report-v1"
PACKAGE_SCHEMA = "stpd/source3-ordered-native-m2-package-v1"
VALIDATION_SCHEMA = "stpd/source3-ordered-source-validation-v1"
SCOPE = "source3-ordered-native-training-code-closure-v1"
QUALIFICATION = "source3_publication_memory_exact_N_recorded_only"
PRETRAIN_QUALIFICATION = "ordered_recorded_capture_N_pretraining_only"
SAMPLED_QUALIFICATION = "source3_decision_sample_carry_N_reexpression_only"
DEFAULT_VIEW = "publication_memory"
PRETRAIN_VIEW = "recorded_capture_pretraining"
COHORTS = frozenset({"declared_human", "agent_protocol", "agent_native_ui"})
MAX_STEPS_PER_RUN = 131072

PRETRAIN_PROJECTION_BODY = {
    "schema": "stpd/source3-ordered-projection-spec-v1",
    "id": "source3-ordered-recorded-public-capture",
    "version": "1.0.0",
    "source_type": "source-session-bundle-v3",
    "source_profile": "native-logical-source-v3",
    "mode": "recorded_capture_reexpression_pretraining",
    "input_spec": INPUT_SPEC,
    "order": "original_epoch_then_publication_cut_then_input_prefix_ordinal",
    "input_basis": "independent_original_pre_capture_after_publication_at_its_cut",
    "boundary": "original_publication_cut_and_after_input_ordinal",
    "reset": "first_admitted_frame_of_each_original_attachment_epoch",
    "admission": "complete_epoch_prefix_until_first_gap_pause_or_unproven_basis",
    "actor_change": "no_native_reset_proof",
    "missing": "exclude_gap_and_remaining_epoch_without_current_backfill",
    "unlabelled": "consume_complete_ordered_publication_and_input_basis",
    "advance": "existing_qualified_occurrence_and_revision_coherence",
    "features": "native_full_reference_InputSpec_only",
    "I": False,
    "F": False,
    "original_refs": "raw_bundle_capture_catalog_stream_row_and_original_position",
    "history_claim": "recorded_public_capture_replay_not_attention_or_agent_consumption",
    "deployment_relation": "same_frame_features_and_advance_rule_different_acquisition_history",
    "deployment_acquisition": "AgentSpec_required_publications_only",
    "transfer": "separate_deployed_Agent_history_and_natural_game_evaluation_required",
    "cache": "immutable_bytes_and_structural_projection_only_no_trainable_activation_reuse",
}
PRETRAIN_PROJECTION_SPEC = {
    "id": PRETRAIN_PROJECTION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(PRETRAIN_PROJECTION_BODY),
}
PROJECTION_BODY = {
    **PRETRAIN_PROJECTION_BODY,
    "id": "source3-publication-memory-matched-input",
    "mode": "deployment_publication_memory_exact_basis_N",
    "input_basis": ("score_only_when_coherent_with_preceding_required_publication_"
                    "and_same_complete_C"),
    "unlabelled": "consume_all_complete_required_publications",
    "advance": "required_publications_only_input_basis_never_adds_W_advance",
    "deployment_relation": "same_required_publication_history_and_native_advance_rule",
    "transfer": "native_runtime_and_natural_game_evaluation_remain_separate",
}
PROJECTION_SPEC = {
    "id": PROJECTION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(PROJECTION_BODY),
}
PRETRAIN_TARGET_BODY = {
    "schema": "stpd/source3-N-target-spec-v1",
    "id": "source3-exact-delivered-choice-N",
    "version": "1.0.0",
    "N": "one_unique_full_original_catalog_action_exactly_mapped_and_delivered",
    "label_identity": "original_bundle_input_id_not_terminal_row_order",
    "cohort": "N_from_selected_original_SourceDeclaration_context_keeps_original_origins",
    "loss": "cross_entropy_over_complete_C_at_original_input_basis",
    "mask": "all_publications_and_noneligible_input_choices_have_no_N_loss",
    "supervision": "targets_and_delivery_evidence_never_enter_input_features",
    "gradient": "shared_parameters_may_receive_gradient_without_local_target",
    "memory": "carry_values_TBPTT4_gradients_not_proof_of_learned_memory",
    "Z": "unsupported_requires_separate_causal_successor_target_evidence",
    "O": "unsupported_requires_separate_endpoint_metric_censoring_evidence",
    "evaluation": "eligible_unique_N_choices_and_original_run_related_holdout",
    "task_spec": ready_summary_task_spec(),
    "task_mask": "qualified_ready_terminal_summary_has_no_N_target",
    "non_claims": [
        "human_origin_proof",
        "attention",
        "agent_consume_ack",
        "Commit",
        "causal_successor",
        "whole_game_qualification",
        "data_use_permission",
        "independent_unseen_test",
        "policy_quality",
        "victory",
    ],
}
PRETRAIN_TARGET_SPEC = {
    "id": PRETRAIN_TARGET_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(PRETRAIN_TARGET_BODY),
}
TARGET_BODY = {
    **PRETRAIN_TARGET_BODY,
    "id": "source3-publication-matched-exact-delivered-N",
    "exposure_match": "original_basis_NativeUnit_and_complete_C_equal_preceding_publication",
}
TARGET_SPEC = {"id": TARGET_BODY["id"], "version": "1.0.0", "sha256": semantic_hash(TARGET_BODY)}
SAMPLED_PROJECTION_BODY = {
    **PRETRAIN_PROJECTION_BODY,
    "id": "source3-decision-sample-carry-input",
    "mode": "original_input_basis_sampled_carry_reexpression",
    "input_spec": SAMPLED_INPUT_SPEC,
    "input_basis": "independent_complete_original_pre_capture_and_full_C",
    "reset": "explicit_derived_sample_segments_within_original_run",
    "reset_reasons": list(RESET_REASONS),
    "admission": "raw_verified_original_input_order_then_independently_valid_spans",
    "actor_change": "explicit_actor_handoff_cut",
    "missing": "explicit_cut_then_next_independent_basis_starts_W0",
    "unlabelled": "retain_every_eligible_changed_input_basis_without_N",
    "advance": "shared_native_unit_once_per_occurrence_for_eligible_inputs_only",
    "features": "shared_native_feature_projection_with_sampled_InputSpec",
    "history_claim": "declared_input_basis_reexpression_not_actual_Agent_consumption",
    "deployment_relation": "same_features_complete_C_sample_rule_carry_and_segment_resets",
    "deployment_acquisition": "eligible_changed_Current_samples_exact_ACK_evidence_separate",
}
SAMPLED_PROJECTION_SPEC = {
    "id": SAMPLED_PROJECTION_BODY["id"], "version": "1.0.0",
    "sha256": semantic_hash(SAMPLED_PROJECTION_BODY),
}
SAMPLED_TARGET_BODY = {
    **PRETRAIN_TARGET_BODY,
    "id": "source3-sampled-basis-exact-delivered-N",
    "exposure_match": "eligible_changed_original_basis_and_complete_original_C",
    "mask": "unlabelled_context_readiness_duplicate_and_inexact_delivery_have_no_N_loss",
}
SAMPLED_TARGET_SPEC = {
    "id": SAMPLED_TARGET_BODY["id"], "version": "1.0.0",
    "sha256": semantic_hash(SAMPLED_TARGET_BODY),
}
VIEW_SPECS = {
    DEFAULT_VIEW: (PROJECTION_SPEC, TARGET_SPEC),
    PRETRAIN_VIEW: (PRETRAIN_PROJECTION_SPEC, PRETRAIN_TARGET_SPEC),
    SAMPLED_VIEW: (SAMPLED_PROJECTION_SPEC, SAMPLED_TARGET_SPEC),
}

# The graph/reset is the existing closed numerical preset. The recipe also fixes
# the new source/target contract; manifests cannot choose modules or trainers.
RECIPES = {
    f"source3-native-m2-k{slots}d96-{reset}-N{suffix}-v1": (slots, reset, view)
    for slots in (1, 8)
    for reset in ("carry", "reset")
    for suffix, view in (("", DEFAULT_VIEW), ("-pretrain", PRETRAIN_VIEW))
}
RECIPES[SAMPLED_RECIPE] = (1, "carry", SAMPLED_VIEW)
DEFAULT_RECIPE = "source3-native-m2-k1d96-carry-N-v1"


def recipe_control(recipe: str) -> NativeGraphControl:
    from .native_graph_spec import GraphSpec, NativeGraphControl, ResetSpec

    if recipe not in RECIPES:
        raise BoundaryError("source3_recipe", "unsupported_recipe")
    slots, reset, _ = RECIPES[recipe]
    return NativeGraphControl(
        GraphSpec(slots),
        ResetSpec("carry" if reset == "carry" else "reset_before_each_actual_advance"),
    )


def view_specs(view: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if view not in VIEW_SPECS:
        raise BoundaryError("source3_spec", "unsupported_view_preset")
    projection, target = VIEW_SPECS[view]
    return dict(projection), dict(target)


def checked_view(projection_spec: object, target_spec: object) -> str:
    for view, (projection, target) in VIEW_SPECS.items():
        if json_bytes(projection_spec) == json_bytes(projection) and json_bytes(
            target_spec
        ) == json_bytes(target):
            return view
    raise BoundaryError("source3_spec", "projection_target_preset_mismatch")


def recipe_view(recipe: str) -> str:
    if recipe not in RECIPES:
        raise BoundaryError("source3_recipe", "unsupported_recipe")
    return RECIPES[recipe][2]


def view_qualification(view: str) -> str:
    view_specs(view)
    return (QUALIFICATION if view == DEFAULT_VIEW else SAMPLED_QUALIFICATION
            if view == SAMPLED_VIEW else PRETRAIN_QUALIFICATION)


def view_input_spec(view: str) -> dict[str, Any]:
    view_specs(view)
    return dict(SAMPLED_INPUT_SPEC if view == SAMPLED_VIEW else INPUT_SPEC)


def validation_identity(
    source_sha256: str,
    cohort: str,
    raw_refs: object,
    *,
    projection_spec: object = PROJECTION_SPEC,
    target_spec: object = TARGET_SPEC,
) -> dict[str, Any]:
    """Portable exact contract/ancestry IDs, not a substitute for raw replay."""
    digest(source_sha256, "source3.source_sha256")
    view = checked_view(projection_spec, target_spec)
    if cohort not in COHORTS or not isinstance(raw_refs, list) or not raw_refs:
        raise BoundaryError("source3_spec", "source_or_raw_reference_scope")
    refs = []
    for value in raw_refs:
        ref = object_fields(value, {"raw_id", "admission_id"}, "source3.original_reference")
        for role in ("raw_id", "admission_id"):
            digest(ref[role], "source3." + role)
        refs.append(ref)
    if len({ref["raw_id"] for ref in refs}) != len(refs) or refs != sorted(
        refs, key=lambda ref: ref["raw_id"]
    ):
        raise BoundaryError("source3_spec", "raw_reference_order_or_duplicates")
    return {
        "schema": VALIDATION_SCHEMA,
        "source_sha256": source_sha256,
        "input_spec_sha256": view_input_spec(view)["sha256"],
        "cohort": cohort,
        "projection_spec": view_specs(view)[0],
        "target_spec": view_specs(view)[1],
        "raw_refs": refs,
        "validation": "Source3_originals_reverified_then_ordered_N_admitted",
    }
