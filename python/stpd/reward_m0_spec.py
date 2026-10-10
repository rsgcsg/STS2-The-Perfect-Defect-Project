"""Closed offline paired reward-M0 contract; no execution or numerical imports."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, object_fields

from .canonical import semantic_hash
from .fullrun.public_inputs import COMPACT_IDENTITY
from .stage1a_recipes import LIGHT_ACTION_M0_GRAPH

RECIPE = "reward-m0-legacycompact-deck-pair-v1"
SOURCE_SCHEMA = "stpd/reward-m0-paired-training-source-v1"
INPUT_SCHEMA = "stpd/reward-m0-paired-training-input-v1"
RUN_SCHEMA = "stpd/reward-m0-paired-run-v1"
CHECKPOINT_SCHEMA = "stpd/reward-m0-paired-checkpoint-v1"
MODEL_SCHEMA = "stpd/reward-m0-paired-model-v1"
RESULT_SCHEMA = "stpd/reward-m0-paired-result-v1"
COHORT = "original_legacy_public_TRAIN"
MAX_CHOICES = 300
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_CACHED_BYTES = 512 * 1024 * 1024
DECK_SCHEMA = "stpd/public-run-deck-unordered-multiset-v1"
DECK_BODY = {
    "schema": DECK_SCHEMA,
    "version": "1.0.0",
    "read": "original_H_frame_materialized_run_deck_bound_to_exact_snapshot",
    "complete": "complete_for_player_run_deck_contents_without_semantic_order",
    "ordering": "canonical_unordered_multiset_preserve_duplicate_card_instances",
    "fields": "all_VisibleCard_public_fields_except_opaque_entity_id",
    "upgrades": "original_is_upgraded",
    "enchantments": "original_public_existing_enchantment_including_amount",
    "none": "missing_Read_excluded_from_both_arms_never_empty_deck",
}
INPUT_BODIES = {
    "A": {
        "schema": "stpd/reward-m0-input-spec-v1",
        "id": "reward-m0-legacycompact-A-v1",
        "version": "1.0.0",
        "state": "exact_cached_original_legacycompact_state_text",
        "renderer": COMPACT_IDENTITY,
        "C": "exact_original_complete_ordered_action_texts",
        "I": False,
        "F": False,
        "memory": "M0_stateless",
        "targets": "original_N_only",
    },
    "B": {
        "schema": "stpd/reward-m0-input-spec-v1",
        "id": "reward-m0-legacycompact-deck-B-v1",
        "version": "1.0.0",
        "state": "A_state_plus_canonical_complete_unordered_run_deck_block",
        "renderer": COMPACT_IDENTITY,
        "deck": DECK_BODY,
        "C": "exact_original_complete_ordered_action_texts",
        "I": False,
        "F": False,
        "memory": "M0_stateless",
        "targets": "original_N_only",
    },
}
INPUT_SPECS = {
    arm: {"id": body["id"], "version": "1.0.0", "sha256": semantic_hash(body)}
    for arm, body in INPUT_BODIES.items()
}
SOURCE_SPEC_BODY = {
    "schema": SOURCE_SCHEMA,
    "version": "1.0.0",
    "cohort": COHORT,
    "input_specs": INPUT_SPECS,
    "selection": "explicit_unique_original_TRAIN_occurrence_ids_at_most_300",
    "paired": "same_occurrence_ids_original_targets_and_complete_ordered_C",
    "missing_B": "exclude_both_with_original_reason",
    "tokenizer": "one_frozen_train_only_union_A_B_state_BPE_no_dev_or_Gold",
    "comparison": "same_M0_graph_initial_parameters_seed_schedule_optimizer_and_limits",
    "gameplay_cause": "hypothesis_not_established_by_pairing_or_fit",
    "evaluation": "training_rows_only_no_heldout_or_quality_claim",
    "graph": LIGHT_ACTION_M0_GRAPH,
    "online": "separate_Read_capable_adapter_export_registration_and_runtime_followup",
}
SOURCE_SPEC = {
    "id": "reward-m0-legacycompact-deck-pair-source-v1",
    "version": "1.0.0",
    "sha256": semantic_hash(SOURCE_SPEC_BODY),
}


@dataclass(frozen=True)
class RewardM0Config:
    arm: str = "A"
    seed: int = 1701
    steps: int = 40
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    gradient_clip: float = 1.0
    device: str = "cpu"
    max_state_tokens: int = 8192
    max_action_bytes: int = 8192
    state_vocab_size: int = 8192
    checkpoint_every_boundaries: int = 10

    def __post_init__(self) -> None:
        if self.arm not in INPUT_SPECS or self.device not in {"cpu", "mps"}:
            raise BoundaryError("reward_m0", "explicit_arm_and_local_device_required")
        for name, minimum, maximum in (
            ("seed", 0, 2**63 - 1),
            ("steps", 1, 3000),
            ("max_state_tokens", 1, 8192),
            ("max_action_bytes", 1, 8192),
            ("state_vocab_size", 256, 8192),
            ("checkpoint_every_boundaries", 1, 100),
        ):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise BoundaryError("reward_m0", "invalid_" + name)
        for name in ("learning_rate", "weight_decay", "gradient_clip"):
            value = getattr(self, name)
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or value < 0
                or name != "weight_decay"
                and value == 0
            ):
                raise BoundaryError("reward_m0", "invalid_optimizer")

    @classmethod
    def decode(cls, value: object) -> RewardM0Config:
        fields = object_fields(value, set(cls.__dataclass_fields__), "reward_m0.config")
        return cls(**fields)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def paired_schedule(self) -> dict[str, Any]:
        return {key: value for key, value in self.to_dict().items() if key != "arm"}


def checked_pair_limits(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {"wall_seconds", "scratch_bytes"}:
        raise BoundaryError("reward_m0", "explicit_paired_limits_required")
    if (
        type(value["wall_seconds"]) is not int
        or not 1 <= value["wall_seconds"] <= 3600
        or type(value["scratch_bytes"]) is not int
        or not 16 * 1024**2 <= value["scratch_bytes"] <= 1024**3
    ):
        raise BoundaryError("reward_m0", "invalid_paired_limits")
    return dict(value)
