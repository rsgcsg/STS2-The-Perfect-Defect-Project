"""Source-bound, computation-free M2 bridge regressions using public fixtures."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import snapshot
from test_text_menu_human_import import _declared_bundle
from tokenizers import Tokenizer, models, pre_tokenizers

from stpd.fullrun.memory_sequence_bridge import (
    project_memory_episodes,
    project_memory_windows,
)
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


def test_window_diagnostics_remain_in_source_order_after_shared_segmentation():
    result = project_memory_windows(
        view(observed("invalid", 1, reset=True, action="absent"),
             observed("missing", 2, action=None, missing=True)),
        tokenizer(), model(),
    )
    assert [item.reason for item in result.diagnostics] == [
        "choice_binding_mismatch", "missing_observation",
    ]


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


def test_long_episode_projects_all_observations_and_source_event_ids():
    subject = model()
    items = tuple(observed(f"event-{index}", index + 1, reset=index == 0,
                           action="opaque-play" if index == 64 else None)
                  for index in range(65))
    with patch.object(subject.core, "contextualize") as compute:
        result = project_memory_episodes(
            view(*items), tokenizer(), subject,
            max_observations=65, max_input_tokens=100_000,
        )
        compute.assert_not_called()
    assert not result.diagnostics
    (episode,) = result.episodes
    assert len(episode.steps) == 65
    assert tuple(step.position for step in episode.steps) == tuple(range(65))
    assert episode.steps[0].reset_before
    assert all(not step.reset_before for step in episode.steps[1:])
    assert all(step.label_key is None for step in episode.steps[:-1])
    assert episode.steps[-1].label_key == "opaque-play"
    assert result.sources[0].event_ids == tuple(item.event_id for item in items)
    assert result.sources[0].source_id == "verified-fixture-source"


def test_episode_binding_permutation_and_unknown_delivery_keep_source_boundary():
    subject = model()
    first = observed("first", 1, reset=True, action=None)
    final = observed("unknown", 2, unknown=True)
    changed = deepcopy(final.snapshot)
    assert changed is not None
    changed["menu_actions"]["actions"].reverse()
    final = replace(final, snapshot=changed)
    result = project_memory_episodes(
        view(first, final), tokenizer(), subject,
        max_observations=2, max_input_tokens=10_000,
    )
    assert not result.diagnostics
    steps = result.episodes[0].steps
    assert steps[-1].action_keys == ("opaque-play", "opaque-nav")
    assert steps[-1].label_key == "opaque-play"
    assert all(step.previous_actual_action is None and step.public_feedback is None
               for step in steps)
    assert result.sources[0].event_ids == ("first", "unknown")
    bad = replace(final, selected_action_id="missing")
    rejected = project_memory_episodes(
        view(first, bad), tokenizer(), subject,
        max_observations=2, max_input_tokens=10_000,
    )
    assert not rejected.episodes
    assert [item.reason for item in rejected.diagnostics] == ["choice_binding_mismatch"]


def test_episode_missing_observation_breaks_until_explicit_reset():
    items = (
        observed("first", 1, reset=True),
        observed("missing", 2, action=None, missing=True),
        observed("blocked", 3),
        observed("new", 4, reset=True),
    )
    result = project_memory_episodes(
        view(*items), tokenizer(), model(),
        max_observations=4, max_input_tokens=10_000,
    )
    assert [source.event_ids for source in result.sources] == [("first",), ("new",)]
    assert [item.reason for item in result.diagnostics] == [
        "missing_observation", "reset_required",
    ]
    assert all(episode.steps[0].position == 0 and episode.steps[0].reset_before
               for episode in result.episodes)
    assert result.episodes[0].episode_id != result.episodes[1].episode_id


def test_episode_limits_reject_whole_segment_without_truncating_or_scoring():
    subject = model()
    items = tuple(observed(f"event-{index}", index + 1, reset=index == 0)
                  for index in range(65))
    accepted = project_memory_episodes(
        view(*items), tokenizer(), subject,
        max_observations=65, max_input_tokens=100_000,
    )
    steps = accepted.episodes[0].steps
    total_tokens = sum(step.page.numel() + sum(action.numel() for action in step.actions)
                       for step in steps)
    with patch.object(subject.core, "contextualize") as compute:
        too_many = project_memory_episodes(
            view(*items), tokenizer(), subject,
            max_observations=64, max_input_tokens=total_tokens,
        )
        too_large = project_memory_episodes(
            view(*items), tokenizer(), subject,
            max_observations=65, max_input_tokens=total_tokens - 1,
        )
        compute.assert_not_called()
    assert not too_many.episodes and not too_large.episodes
    assert too_many.diagnostics[0].reason == "episode_observation_limit"
    assert too_large.diagnostics[0].reason == "episode_input_token_limit"
    assert too_many.diagnostics[0].first_event_id == "event-0"


def test_episode_requires_label_and_restarts_at_each_explicit_reset():
    first = observed("first", 1, reset=True, action=None)
    second = observed("second", 2, reset=True)
    third = observed("third", 3)
    result = project_memory_episodes(
        view(first, second, third), tokenizer(), model(),
        max_observations=3, max_input_tokens=10_000,
    )
    assert [item.reason for item in result.diagnostics] == ["no_learn_span_label"]
    assert [source.event_ids for source in result.sources] == [("second", "third")]
    assert result.episodes[0].steps[0].position == 0
    assert result.episodes[0].steps[0].reset_before
    assert not result.episodes[0].steps[1].reset_before


def session_observed(
    event: str, sequence: int, *, status: str = "interactive",
    reset: bool = False, action: str | None = "opaque-play",
) -> ObservedInput:
    page = snapshot(event)
    page["session"] = {
        "runtime_instance_id": "verified-runtime",
        "environment_fingerprint": "verified-environment",
    }
    if status == "settling":
        page["status"] = "settling"
        page["menu_actions"].update(
            status="unavailable", actions=[], total_count=0, materialized_count=0,
        )
        action = None
    return replace(observed(event, sequence, reset=reset, action=action), snapshot=page)


def test_opted_in_verified_settling_keeps_event_provenance_without_model_step():
    subject = model()
    items = (
        session_observed("ready-1", 1, reset=True),
        session_observed("settling-1", 2, status="settling"),
        session_observed("settling-2", 3, status="settling"),
        session_observed("ready-2", 4),
    )
    with patch.object(subject.core, "contextualize") as compute:
        result = project_memory_episodes(
            view(*items), tokenizer(), subject,
            max_observations=2, max_input_tokens=10_000, max_settling_events=2,
        )
        compute.assert_not_called()
    assert not result.diagnostics
    (episode,) = result.episodes
    assert len(episode.steps) == 2
    assert [step.position for step in episode.steps] == [0, 1]
    assert [step.reset_before for step in episode.steps] == [True, False]
    assert result.sources[0].event_ids == tuple(item.event_id for item in items)
    assert result.sources[0].step_event_ids == ("ready-1", "ready-2")
    assert result.sources[0].settling_event_ids == ("settling-1", "settling-2")
    assert all(step.previous_actual_action is None and step.public_feedback is None
               for step in episode.steps)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("default", "settling_requires_opt_in"),
        ("too_many", "episode_settling_limit"),
        ("schema", "invalid_settling_observation"),
        ("profile", "invalid_settling_observation"),
        ("identity_missing", "settling_identity_unverified"),
        ("identity", "settling_identity_drift"),
        ("choice", "invalid_settling_observation"),
        ("catalog", "invalid_settling_observation"),
        ("catalog_status", "invalid_settling_observation"),
        ("incomplete", "invalid_settling_observation"),
        ("status", "complete_current_menu_required"),
        ("other_invalid", "complete_current_menu_required"),
        ("leading", "settling_needs_interactive_neighbors"),
        ("unbounded", "settling_needs_interactive_neighbors"),
    ],
)
def test_settling_opt_in_rejects_unproved_skips(mutation: str, reason: str):
    first = session_observed("ready-1", 1, reset=True)
    middle = session_observed("settling", 2, status="settling")
    last = session_observed("ready-2", 3)
    limit = 1
    if mutation == "default":
        limit = 0
    elif mutation == "too_many":
        middle = session_observed("settling", 2, status="settling")
        last = session_observed("settling-2", 3, status="settling")
        # Keep a trustworthy interactive suffix so only the cap is challenged.
        items = (first, middle, last, session_observed("ready-3", 4))
    elif mutation == "identity":
        page = deepcopy(middle.snapshot)
        assert page is not None
        page["session"]["environment_fingerprint"] = "foreign"
        middle = replace(middle, snapshot=page)
    elif mutation in {"schema", "profile", "identity_missing"}:
        page = deepcopy(middle.snapshot)
        assert page is not None
        if mutation == "schema":
            page["schema"] = "wrong-schema"
        elif mutation == "profile":
            page["input_profile"] = "wrong-profile"
        else:
            page["session"].pop("runtime_instance_id")
        middle = replace(middle, snapshot=page)
    elif mutation == "choice":
        middle = replace(middle, selected_action_id="opaque-play", choice_mask=True)
    elif mutation == "catalog":
        page = deepcopy(middle.snapshot)
        assert page is not None
        assert first.snapshot is not None
        page["menu_actions"]["actions"] = deepcopy(first.snapshot["menu_actions"]["actions"])
        middle = replace(middle, snapshot=page)
    elif mutation == "catalog_status":
        page = deepcopy(middle.snapshot)
        assert page is not None
        page["menu_actions"]["status"] = "complete"
        middle = replace(middle, snapshot=page)
    elif mutation == "incomplete":
        page = deepcopy(middle.snapshot)
        assert page is not None
        page["completeness"]["status"] = "partial"
        middle = replace(middle, snapshot=page)
    elif mutation == "status":
        page = deepcopy(middle.snapshot)
        assert page is not None
        page["status"] = "visible_unsupported"
        middle = replace(middle, snapshot=page)
    elif mutation == "other_invalid":
        page = deepcopy(middle.snapshot)
        assert page is not None
        page["status"] = "visible_unsupported"
        middle = replace(middle, snapshot=page)
        items = (first, session_observed("settling-0", 2, status="settling"),
                 replace(middle, source_sequence=3), replace(last, source_sequence=4))
    elif mutation == "leading":
        items = (replace(middle, reset_before=True, reset_reason="stream_start",
                         source_sequence=1), replace(last, source_sequence=2))
    elif mutation == "unbounded":
        items = (first, middle)
    if mutation not in {"too_many", "other_invalid", "leading", "unbounded"}:
        items = (first, middle, last)
    result = project_memory_episodes(
        view(*items), tokenizer(), model(),
        max_observations=4, max_input_tokens=10_000, max_settling_events=limit,
    )
    assert not result.episodes
    assert [item.reason for item in result.diagnostics] == [reason]
