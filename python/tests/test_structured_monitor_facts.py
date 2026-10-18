"""Pure fixed-four plan facts and original occurrence counts; no neural runtime."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.recipes.structured import (
    StructuredRecipeAdapter,
    _preflight_result,
)
from stpd.structured_workload_contracts import (
    StructuredTrainingConfig,
    accepted_counters,
    chunk_plans,
    frozen_training_plan,
)


def dataset():
    # Score-only rows count as contexts/labels, not memory advances. The reset
    # at8 separates a span, and the final unlabelled span still belongs to a pass.
    advances = (True, False, True, True, False, True, True, False, True, True, True)
    steps = tuple(
        SimpleNamespace(
            position=position,
            advance=advance,
            reset_before=position in {0, 8, 10},
            chosen_action_id="choice" if position in {4, 8} else None,
            capture_id="shared-capture",
        )
        for position, advance in enumerate(advances)
    )
    return SimpleNamespace(
        source_sha256="f" * 64,
        runs=(SimpleNamespace(run_id="original/train", split="train", steps=steps),),
    )


def index():
    # Different original inputs can reuse a capture. Their original occurrences
    # remain distinct; the excluded original candidate supplies its denominator.
    return tuple(
        {
            "raw_id": "a" * 64,
            "run_id": "original/train",
            "occurrence_id": f"{position:064x}",
            "capture_id": "shared-capture",
            "N_eligible": position < 2,
            "original_N_candidate": position < 3,
            "target_eligibility": "empty_C_readiness_not_sample"
            if position == 3
            else "eligible_exact_delivered_N"
            if position < 2
            else "masked_original_N",
        }
        for position in range(4)
    )


def plan(value, config):
    verified = _preflight_result(value, index(), True)
    return frozen_training_plan(
        value,
        config,
        data_counts=verified.data_counts.value(),
        distinct_N_basis=verified.distinct_N_basis,
    )


def test_single_chunk_owner_counts_advances_and_preserves_reset_and_tail():
    plans = chunk_plans(dataset(), StructuredTrainingConfig())
    assert [(row.start, row.end, row.advances, row.labels) for row in plans] == [
        (0, 6, 4, 1),
        (6, 8, 1, 0),
        (8, 10, 2, 1),
        (10, 11, 1, 0),
    ]


def test_distinct_original_N_does_not_deduplicate_capture_or_multiply_epochs():
    value = plan(dataset(), StructuredTrainingConfig(epochs=3, max_updates=100))
    assert (
        value["distinct_N_basis"] == "qualified_original_train_run_and_source_occurrence_identity"
    )
    assert value["data_counts"] == {
        "distinct_supervised_N": 2,
        "contexts": 11,
        "unlabelled_contexts": 9,
        "excluded_N": 1,
        "captured_readiness_dispositions": 1,
    }
    assert (
        value["updates_per_epoch"],
        value["planned_optimizer_updates"],
        value["update_cap"],
    ) == (2, 6, 100)
    assert value["planned_boundaries"] == 12
    actual = accepted_counters(
        SimpleNamespace(updates=4, labels=4, observations=22, boundary=8, epoch=2)
    )
    assert actual == {
        "optimizer_updates": 4,
        "label_uses": 4,
        "observations": 22,
        "boundaries": 8,
        "epoch": 2,
        "whole_cohort_passes": 2,
    }
    assert value["data_counts"]["distinct_supervised_N"] != actual["label_uses"]


def test_update_cap_before_unlabelled_tail_is_not_a_whole_pass():
    capped = plan(dataset(), StructuredTrainingConfig(epochs=1, max_updates=2))
    complete = plan(dataset(), StructuredTrainingConfig(epochs=1, max_updates=100))
    assert capped["planned_optimizer_updates"] == complete["planned_optimizer_updates"] == 2
    assert capped["planned_boundaries"] == 3
    assert capped["whole_cohort_target"]["planned"] is False
    assert complete["planned_boundaries"] == 4
    assert complete["whole_cohort_target"]["planned"] is True


def test_recipe_plan_reuses_original_preflight_and_keeps_unknown_denominator(monkeypatch):
    adapter = StructuredRecipeAdapter("structured-m2-cpu-v3")
    value = dataset()
    prepared = _preflight_result(value, None, True)
    request = TrainingRequest(
        "1" * 32,
        "structured-m2-cpu-v3",
        "b" * 64,
        {**asdict(StructuredTrainingConfig()), "checkpoint_every_boundaries": 1},
    )
    monkeypatch.setattr(
        adapter, "preflight_request", lambda *args: pytest.fail("second source replay")
    )
    result = adapter.training_plan(None, None, request, prepared_dataset=prepared)
    assert result["data_counts"]["excluded_N"] is None
    assert result["distinct_N_basis"] == "admitted_train_run_step_identity"
    assert _preflight_result(value, index(), False) is value


@pytest.mark.parametrize("window", [16, 32])
def test_released_fixed_four_configuration_remains_closed(window):
    with pytest.raises(BoundaryError, match="unsupported_fixed_recipe"):
        StructuredTrainingConfig(tbptt_advances=window).validate()
