"""Closed Source3 recorded-capture recipe contracts; discovery imports no tensors.

These are research representations, not Source/Connector authority or permission.
Old synthetic native formats keep their original schemas and source restrictions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .canonical import semantic_hash
from .fullrun.native_structured_inputs import INPUT_SPEC

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
PACKAGE_SCHEMA = "stpd/source3-ordered-native-m2-package-v1"
SCOPE = "source3-ordered-native-training-code-closure-v1"
QUALIFICATION = "ordered_recorded_public_capture_N_only"
COHORTS = frozenset({"declared_human", "agent_protocol", "agent_native_ui"})
MAX_STEPS_PER_RUN = 131072

PROJECTION_BODY = {
    "schema": "stpd/source3-ordered-projection-spec-v1",
    "id": "source3-ordered-recorded-public-capture",
    "version": "1.0.0",
    "source_type": "source-session-bundle-v3",
    "source_profile": "native-logical-source-v3",
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
    "cache": "immutable_bytes_and_structural_projection_only_no_trainable_activation_reuse",
}
PROJECTION_SPEC = {
    "id": PROJECTION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(PROJECTION_BODY),
}
TARGET_BODY = {
    "schema": "stpd/source3-N-target-spec-v1",
    "id": "source3-exact-delivered-choice-N",
    "version": "1.0.0",
    "N": "one_unique_full_original_catalog_action_exactly_mapped_and_delivered",
    "label_identity": "original_bundle_input_id_not_terminal_row_order",
    "loss": "cross_entropy_over_complete_C_at_original_input_basis",
    "mask": "all_publications_and_noneligible_input_choices_have_no_N_loss",
    "supervision": "targets_and_delivery_evidence_never_enter_input_features",
    "gradient": "shared_parameters_may_receive_gradient_without_local_target",
    "memory": "carry_values_TBPTT4_gradients_not_proof_of_learned_memory",
    "Z": "unsupported_requires_separate_causal_successor_target_evidence",
    "O": "unsupported_requires_separate_endpoint_metric_censoring_evidence",
    "evaluation": "eligible_unique_N_choices_and_original_run_related_holdout",
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
TARGET_SPEC = {"id": TARGET_BODY["id"], "version": "1.0.0", "sha256": semantic_hash(TARGET_BODY)}

# The graph/reset is the existing closed numerical preset. The recipe also fixes
# the new source/target contract; manifests cannot choose modules or trainers.
RECIPES = {
    f"source3-native-m2-k{slots}d96-{reset}-N-v1": (slots, reset)
    for slots in (1, 8)
    for reset in ("carry", "reset")
}
DEFAULT_RECIPE = "source3-native-m2-k1d96-carry-N-v1"


def recipe_control(recipe: str) -> NativeGraphControl:
    from spireagent.json_boundary import BoundaryError

    from .native_graph_spec import GraphSpec, NativeGraphControl, ResetSpec

    if recipe not in RECIPES:
        raise BoundaryError("source3_recipe", "unsupported_recipe")
    slots, reset = RECIPES[recipe]
    return NativeGraphControl(
        GraphSpec(slots),
        ResetSpec("carry" if reset == "carry" else "reset_before_each_actual_advance"),
    )
