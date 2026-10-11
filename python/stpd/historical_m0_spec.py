"""Closed historical-public M0 relation/recipe; discovery has no numerical imports."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, digest, object_fields

RECIPE = "historical-public-light-action-m0-warm-start-v1"
RELATION_SCHEMA = "stpd/historical-public-m0-relation-v1"
INPUT_SCHEMA = "stpd/stage1a-light-action-m0-historical-input-v1"
INPUT_FORMAT = "stpd-token-light-action-m0-historical-v1"
RUN_SCHEMA = "stpd/historical-public-m0-run-v1"
MODEL_SCHEMA = "stpd/stage1a-light-action-m0-public-model-v1"
INITIALIZATION_SCHEMA = "stpd/model-weight-warm-start-v1"
ORDER_PROFILE = "public-hbc-v2-dual-c8d654-v1"
ORDER_CONTRACT = {
    "profile": ORDER_PROFILE,
    "view_producer_revision": "71309adfcdfd808a6b99d2bc43abda9079ef1d7a",
    "view_source_sha256": "48f082948b440a52321d66a660a8fcb1ec3ee84a7824baf75d04ee7a99d0369a",
    "input_producer_revision": "c8d654b7aa87ad71da7c5f12e0a20815f2c16889",
    "input_source_sha256": "3b1f30b0b1892cee022ba5cd643728fe0e8995c0882e51ac2899c18d15fa3951",
    "ordering": "included_dispositions_then_samples_then_encoded_rows_without_reordering",
    "source_admission": "separate_owner_held_selected_H_and_frozen_encoding_receipt_required",
}
HISTORICAL_PUBLIC_RECEIPT_SCHEMA = "stpd/local-historical-public-input-receipt-v1"
HISTORICAL_PUBLIC_PROOF_KIND = "selected_original_H_choice_and_frozen_encoding"
MAX_SELECTED_ARCHIVE_BYTES = 512 * 1024**2
MAX_MEMBERS_BYTES = 64 * 1024**2
MAX_RELATION_BYTES = 8 * 1024**2
MAX_SELECTED_ROWS = 4096
MAX_SELECTED_BYTES = 128 * 1024**2
MAX_ROW_BYTES = 8 * 1024**2
MAX_ORIGINAL_ROWS = 100000
MEMBER_FIELDS = frozenset(
    {
        "occurrence",
        "run_id",
        "source_archive_sha256",
        "split",
        "transition_id",
    }
)
DIAGNOSTIC_SCOPE = {
    "evaluation_scope": "within_training_purpose_historical_allocation",
    "historical_external_exposure": "inherited_not_clean",
    "physical_game_independence": "unresolved",
    "clean_held_out_claim": False,
    "current_game_ascension_claim": False,
}


@dataclass(frozen=True)
class HistoricalM0Config:
    warm_start_model_id: str
    seed: int = 1701
    steps: int = 10
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    gradient_clip: float = 1.0
    device: str = "cpu"
    max_state_tokens: int = 8192
    max_action_bytes: int = 8192
    checkpoint_every_boundaries: int = 100

    def __post_init__(self) -> None:
        digest(self.warm_start_model_id, "historical_m0.warm_start_model_id")
        if (
            type(self.seed) is not int
            or not 0 <= self.seed < 2**63
            or type(self.steps) is not int
            or not 1 <= self.steps <= 10000
            or self.device not in {"cpu", "mps"}
            or type(self.checkpoint_every_boundaries) is not int
            or not 1 <= self.checkpoint_every_boundaries <= 100
        ):
            raise BoundaryError("historical_m0", "invalid_config")
        for name in ("learning_rate", "weight_decay", "gradient_clip"):
            value = getattr(self, name)
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or value < 0
                or name != "weight_decay"
                and value == 0
            ):
                raise BoundaryError("historical_m0", "invalid_optimizer")
        for name in ("max_state_tokens", "max_action_bytes"):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= 8192:
                raise BoundaryError("historical_m0", "invalid_input_limit")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def decode(cls, value: object) -> HistoricalM0Config:
        return cls(**object_fields(value, set(cls.__dataclass_fields__), "historical_m0.config"))

    def numerical_fields(self) -> dict[str, Any]:
        return {
            "recipe": "stage1a.dsimple.light-action.m0.s.v1",
            **{
                name: value
                for name, value in asdict(self).items()
                if name not in {"warm_start_model_id", "checkpoint_every_boundaries"}
            },
        }
