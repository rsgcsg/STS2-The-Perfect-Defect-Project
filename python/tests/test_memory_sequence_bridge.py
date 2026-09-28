"""Source-bound, computation-free M2 bridge regressions using public fixtures."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import snapshot
from test_text_menu_human_import import _declared_bundle
from tokenizers import Tokenizer, models, pre_tokenizers

from stpd.fullrun.memory_sequence_bridge import project_memory_windows
from stpd.fullrun.observed_input_sequence import (
    ObservedInput,
    ObservedInputView,
    SourceEventRef,
    load_observed_input_view,
)
from stpd.fullrun.text_menu_human_import import (
    publish_human_text_source,
    publish_verified_human_text_bundle,
)
from stpd.models.dsimple_memory import ExperimentalDSimpleM2
from stpd.models.dsimple_sequence_training import memory_sequence_loss
from stpd.models.token_core import ScratchShape, ScratchTokenCore


def tokenizer() -> Tokenizer:
    value = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    value.pre_tokenizer = pre_tokenizers.Whitespace()
    return value


def model() -> ExperimentalDSimpleM2:
    return ExperimentalDSimpleM2(ScratchTokenCore(ScratchShape(32, 8, 1, 2, 16, 0.0, 256)))


def observed(
    event: str, sequence: int, *, stream: str = "human:session:timeline:run",
    reset: bool = False, action: str | None = "opaque-play",
    missing: bool = False, unknown: bool = False,
) -> ObservedInput:
    return ObservedInput(
        stream_id=stream, source_kind="human_input_stream", event_id=event,
        source_sequence=sequence, snapshot=None if missing else snapshot(event),
        observation_mask=not missing, selected_action_id=action,
        choice_mask=action is not None,
        delivery_status="unknown" if unknown else "human_witness_only",
        delivery_mask=False, successor_snapshot=None, successor_relation="none",
        successor_observation_mask=False, causal_successor_mask=False,
        reset_before=reset, reset_reason="stream_start" if reset else None,
        source_events=(SourceEventRef(sequence, "human_text_input", event),),
    )


def view(*items: ObservedInput) -> ObservedInputView:
    return ObservedInputView("verified-fixture-source", "partial_human_input_stream", False,
                             tuple(items))


def agent_observed(event: str, sequence: int, *, reset: bool = False,
                   action: str | None = None) -> ObservedInput:
    """Shape of a verified Agent decision input, which may choose navigation."""
    return replace(
        observed(event, sequence, reset=reset, action=action),
        stream_id="agent:content:run", source_kind="agent_decision_inputs",
        delivery_status="not_applicable" if action == "opaque-nav" else "not_attempted",
        source_events=(SourceEventRef(sequence, "text_decision_input", event),),
    )


def test_full_prefix_retains_unlabelled_observation_and_exact_catalog_binding():
    subject = model()
    items = (agent_observed("cue", 1, reset=True),
             agent_observed("choice", 2, action="opaque-nav"))
    agent_view = ObservedInputView("verified-agent-fixture-source",
                                   "verified_agent_observed_inputs", False, items)
    result = project_memory_windows(agent_view, tokenizer(), subject)
    assert not result.diagnostics
    (window,) = result.windows
    assert window.valid_mask == (True, True)
    assert window.loss_mask == (False, True)
    assert window.burn_in_mask == (False, False)
    assert [step.position for step in window.steps if step is not None] == [0, 1]
    assert [step.action_keys for step in window.steps if step is not None] == [
        ("opaque-nav", "opaque-play"), ("opaque-nav", "opaque-play")]
    assert window.steps[1] is not None and window.steps[1].label_key == "opaque-nav"
    assert result.sources[0].event_ids == ("cue", "choice")
    assert result.sources[0].first_reset_reason == "stream_start"
    assert all(step.previous_actual_action is None and step.public_feedback is None
               for step in window.steps if step is not None)
    assert memory_sequence_loss(subject, window).isfinite()


def test_burn_in_masks_its_label_without_reconstructing_truncated_history():
    subject = model()
    result = project_memory_windows(
        view(observed("a", 1, reset=True), observed("b", 2)),
        tokenizer(), subject, burn_in_steps=1)
    assert not result.diagnostics
    assert result.windows[0].burn_in_mask == (True, False)
    assert result.windows[0].loss_mask == (False, True)


def test_missing_observation_splits_only_on_explicit_reset_and_keeps_cancelled_page():
    items = (
        observed("a", 1, reset=True),
        observed("cancelled", 2, action=None),
        observed("missing", 3, action=None, missing=True),
        observed("cannot_continue", 4),
        observed("after_reset", 5, reset=True),
    )
    result = project_memory_windows(view(*items), tokenizer(), model())
    assert [len(window.steps) for window in result.windows] == [2, 1]
    assert result.windows[0].loss_mask == (True, False)
    assert [item.reason for item in result.diagnostics] == [
        "missing_observation", "reset_required"]


def test_identity_change_requires_reset_and_never_carries_previous_action():
    other = observed("other", 1, stream="human:other:timeline:run")
    result = project_memory_windows(
        view(observed("a", 1, reset=True), other,
             replace(other, event_id="other-reset", source_sequence=2, reset_before=True)),
        tokenizer(), model())
    assert len(result.windows) == 2
    assert [item.reason for item in result.diagnostics] == ["reset_required"]
    assert all(window.steps[0] is not None and window.steps[0].reset_before
               for window in result.windows)


def test_wrong_label_duplicate_event_and_oversized_prefix_fail_closed():
    bad = observed("bad", 1, reset=True, action="absent")
    result = project_memory_windows(view(bad), tokenizer(), model())
    assert not result.windows
    assert [item.reason for item in result.diagnostics] == ["choice_binding_mismatch"]
    with pytest.raises(ValueError, match="duplicate observed event"):
        project_memory_windows(view(bad, bad), tokenizer(), model())
    long = (observed("start", 1, reset=True),) + tuple(
        observed(f"step-{index}", index) for index in range(2, 66))
    result = project_memory_windows(view(*long), tokenizer(), model())
    assert not result.windows
    assert result.diagnostics[0].reason == "window_limit_no_truncation"


def test_token_budget_and_all_burn_in_labels_return_diagnostics(monkeypatch):
    import stpd.fullrun.memory_sequence_bridge as bridge

    monkeypatch.setattr(bridge, "MAX_WINDOW_INPUT_TOKENS", 1)
    limited = project_memory_windows(
        view(observed("a", 1, reset=True)), tokenizer(), model())
    assert not limited.windows
    assert limited.diagnostics[0].reason == "window_input_token_limit"
    monkeypatch.undo()
    masked = project_memory_windows(
        view(observed("a", 1, reset=True)), tokenizer(), model(), burn_in_steps=1)
    assert not masked.windows
    assert masked.diagnostics[0].reason == "no_learn_span_label"


def test_unknown_delivery_does_not_become_previous_actual_action_or_feedback():
    first = observed("unknown", 1, reset=True, unknown=True)
    second = observed("after", 2, action=None)
    result = project_memory_windows(view(first, second), tokenizer(), model())
    assert not result.diagnostics
    (window,) = result.windows
    assert window.loss_mask == (True, False)
    assert window.steps[1] is not None
    assert window.steps[1].previous_actual_action is None
    assert window.steps[1].public_feedback is None


def test_verified_owner_human_source_flows_through_existing_loader(tmp_path):
    directory, row, _ = _declared_bundle(
        tmp_path / "fixture", "begin_card_play_exact_factory_return", "begin_card_play"
    )
    target = store(tmp_path / "artifacts")
    evidence = publish_verified_human_text_bundle(target, directory, PRODUCER)
    source = publish_human_text_source(target, (evidence.artifact_id,), PRODUCER)
    observed_view = load_observed_input_view(target, source.artifact_id)

    result = project_memory_windows(observed_view, tokenizer(), model())

    assert not result.diagnostics
    assert len(result.windows) == 1
    assert result.sources[0].source_id == source.artifact_id
    assert result.sources[0].stream_id == observed_view.inputs[0].stream_id
    (step,) = result.windows[0].steps
    assert step is not None and step.label_key == row["chosen_action"]["action_id"]
    assert step.previous_actual_action is None and step.public_feedback is None
