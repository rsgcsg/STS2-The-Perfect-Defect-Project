"""Synthetic native-v1 text projection checks; no Human store or source admission."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_memory_sequence_bridge import observed, view
from test_text_menu_data import snapshot
from tokenizers import Tokenizer, models, pre_tokenizers

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.confirmed_interaction import HISTORY_INPUT_PROFILE, confirmed_action_text
from stpd.fullrun.light_action_m2_text_projection import (
    LightActionM2TextProjectionConfig,
    project_light_action_m2_human_text,
    text_projection_bytes,
)
from stpd.fullrun.memory_token_inputs import project_memory_profile_snapshot
from stpd.light_action_codec import SPEC_SHA256, decode_action, encode_action


def tokenizer() -> Tokenizer:
    result = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    result.pre_tokenizer = pre_tokenizers.Whitespace()
    return result


def config(**changes) -> LightActionM2TextProjectionConfig:
    value = LightActionM2TextProjectionConfig(
        input_profile=HISTORY_INPUT_PROFILE,
        slots=1,
        reset_each_step=False,
        max_action_bytes=512,
        max_actions_per_step=8,
        max_page_tokens=256,
        max_episode_steps=64,
        max_chunk_steps=32,
        max_episode_tokens=65_536,
        max_chunk_tokens=65_536,
    )
    return replace(value, **changes)


def human_items():
    specifications = (
        ("a", "opaque-play", 1, 8, 0),
        ("b", "opaque-nav", 3, 4, 0),
        ("c", None, 5, 10, 5),
        ("d", None, 8, 11, 9),
        ("e", None, 9, 12, 12),
    )
    result = []
    for event, action_id, capture, physical, watermark in specifications:
        item = observed(event, capture, reset=capture == 1, action=action_id)
        result.append(
            replace(
                item,
                snapshot=snapshot(event),
                capture_ordinal=capture,
                physical_sequence=physical,
                completed_append_watermark=watermark,
                confirmed_at_sequence=physical if action_id is not None else None,
                confirmed_effect_domain=(
                    "native_input"
                    if action_id == "opaque-play"
                    else "text_menu"
                    if action_id == "opaque-nav"
                    else None
                ),
            )
        )
    return tuple(result)


def test_native_text_projection_uses_byte_catalog_and_exact_append_watermark():
    items = human_items()
    result = project_light_action_m2_human_text(view(*items), tokenizer(), config())
    assert result.input_profile == "text-menu-v1-confirmed-interaction"
    assert result.action_codec_sha256 == SPEC_SHA256
    assert len(result.episodes) == 1
    steps = result.episodes[0].steps
    assert [step.target_action_id for step in steps] == [
        "opaque-play",
        "opaque-nav",
        None,
        None,
        None,
    ]
    assert [step.previous_actual_action is not None for step in steps] == [
        False,
        False,
        True,
        True,
        False,
    ]
    # W=5 sees physical append 4; W=9 sees append 8. Capture ordinal gaps
    # and nonmonotonic physical append order do not manufacture resets.
    assert [step.reset_before for step in steps] == [True, False, False, False, False]
    assert decode_action(steps[2].previous_actual_action) == confirmed_action_text(
        items[1].snapshot,
        "opaque-nav",
        effect_domain="text_menu",
        basis="last_known_human_input_witness",
        profile=HISTORY_INPUT_PROFILE,
    )
    assert decode_action(steps[3].previous_actual_action) == confirmed_action_text(
        items[0].snapshot,
        "opaque-play",
        effect_domain="native_input",
        basis="last_known_human_input_witness",
        profile=HISTORY_INPUT_PROFILE,
    )
    assert "opaque-play" not in decode_action(steps[2].byte_actions[1])
    assert steps[0].action_ids == ("opaque-nav", "opaque-play")
    expected_public = project_memory_profile_snapshot(items[0].snapshot, HISTORY_INPUT_PROFILE)
    assert steps[0].byte_actions[1] == encode_action(expected_public.action_texts[1], max_bytes=512)
    assert all(
        "action_id" not in decode_action(action) for step in steps for action in step.byte_actions
    )
    projected_map = {item.event_id: item for item in result.dispositions}
    assert projected_map["c"].episode_id == result.episodes[0].episode_id
    assert projected_map["c"].position == 2


def test_numeric_capture_gap_is_not_a_missing_input_and_unlabeled_page_remains():
    items = human_items()[:3]
    gapped = (
        items[0],
        replace(items[1], capture_ordinal=7, source_sequence=7),
        replace(items[2], capture_ordinal=8, source_sequence=8),
    )
    result = project_light_action_m2_human_text(view(*gapped), tokenizer(), config())
    steps = result.episodes[0].steps
    assert len(steps) == 3
    assert steps[1].position == 1 and not steps[1].reset_before
    assert steps[2].target_action_id is None
    assert steps[2].previous_actual_action is not None


def test_matched_reset_uses_identical_rows_and_carry_eligibility():
    items = human_items()
    memory = project_light_action_m2_human_text(view(*items), tokenizer(), config())
    reset = project_light_action_m2_human_text(
        view(*items), tokenizer(), config(reset_each_step=True)
    )
    assert memory.episodes[0].steps == reset.episodes[0].steps
    assert memory.episodes[0].reset_each_step is False
    assert reset.episodes[0].reset_each_step is True
    assert memory.identity != reset.identity
    assert memory.graph == "dsimple.light-action.m2.v1"
    assert memory.source_id == "verified-fixture-source"
    assert len(memory.state_tokenizer_sha256) == 64
    assert b'"input_profile":"text-menu-v1-confirmed-interaction"' in text_projection_bytes(memory)
    with pytest.raises(BoundaryError, match="projection_identity_mismatch"):
        text_projection_bytes(replace(memory, identity="0" * 64))


def test_current_or_future_choice_is_not_current_history_and_watermark_retires_once():
    items = human_items()
    # A's W=0 excludes itself; B's W=0 excludes A even though A is already read.
    # C's W=5 consumes B. D's W=9 consumes A. E cannot reuse either one.
    result = project_light_action_m2_human_text(view(*items), tokenizer(), config())
    steps = result.episodes[0].steps
    assert steps[0].previous_actual_action is None
    assert steps[1].previous_actual_action is None
    assert decode_action(steps[2].previous_actual_action) == confirmed_action_text(
        items[1].snapshot,
        "opaque-nav",
        effect_domain="text_menu",
        basis="last_known_human_input_witness",
        profile=HISTORY_INPUT_PROFILE,
    )
    assert decode_action(steps[3].previous_actual_action) == confirmed_action_text(
        items[0].snapshot,
        "opaque-play",
        effect_domain="native_input",
        basis="last_known_human_input_witness",
        profile=HISTORY_INPUT_PROFILE,
    )
    assert steps[4].previous_actual_action is None


def test_future_capture_is_not_history_even_when_its_append_is_below_current_watermark():
    current = replace(
        observed("current", 1, reset=True, action=None),
        snapshot=snapshot("current"),
        capture_ordinal=1,
        physical_sequence=11,
        completed_append_watermark=50,
    )
    later_capture = replace(
        observed("later", 2, action="opaque-play"),
        snapshot=snapshot("later"),
        capture_ordinal=4,
        physical_sequence=4,
        completed_append_watermark=50,
        confirmed_at_sequence=4,
        confirmed_effect_domain="native_input",
    )
    result = project_light_action_m2_human_text(view(current, later_capture), tokenizer(), config())
    assert result.episodes[0].steps[0].target_action_id is None
    assert result.episodes[0].steps[0].previous_actual_action is None
    assert result.episodes[0].steps[1].target_action_id == "opaque-play"
    assert result.episodes[0].steps[1].previous_actual_action is None


def test_missing_observation_resets_and_isolated_unlocated_row_has_no_history():
    first, second, *_ = human_items()
    missing = replace(
        observed("missing", 2, action=None, missing=True), snapshot=None, reset_before=False
    )
    after = replace(observed("after-gap", 3, action=None), reset_before=False)
    isolated = replace(
        human_items()[0],
        event_id="unlocated",
        stream_id="isolated",
        source_sequence=1,
        reset_before=True,
        capture_ordinal=None,
        completed_append_watermark=None,
    )
    result = project_light_action_m2_human_text(
        view(first, missing, after, isolated), tokenizer(), config()
    )
    by_event = {item.event_id: item for item in result.dispositions}
    assert by_event["missing"].reason == "missing_observation"
    assert by_event["after-gap"].reason == "reset_required"
    isolated_episode = next(
        episode
        for episode in result.episodes
        if episode.steps[0].target_action_id == "opaque-play"
        and episode.episode_id != result.episodes[0].episode_id
    )
    assert isolated_episode.steps[0].previous_actual_action is None


def test_settling_page_is_explicitly_dispositioned_without_training_write():
    before = replace(human_items()[0], snapshot=snapshot("before"))
    before_snapshot = before.snapshot
    before_snapshot["session"] = {
        "runtime_instance_id": "runtime-1",
        "environment_fingerprint": "environment-1",
    }
    before = replace(before, snapshot=before_snapshot)
    settling_snapshot = snapshot("settle")
    settling_snapshot.update(status="settling")
    settling_snapshot["session"] = {
        "runtime_instance_id": "runtime-1",
        "environment_fingerprint": "environment-1",
    }
    settling_snapshot["interaction"]["capabilities"] = []
    settling_snapshot["menu_actions"].update(
        status="unavailable", materialized_count=0, total_count=2, actions=[]
    )
    settling = replace(observed("settle", 2, action=None), snapshot=settling_snapshot)
    after = replace(observed("after", 3, action="opaque-nav"), snapshot=snapshot("after"))
    after_snapshot = after.snapshot
    after_snapshot["session"] = {
        "runtime_instance_id": "runtime-1",
        "environment_fingerprint": "environment-1",
    }
    after = replace(after, snapshot=after_snapshot)
    after = replace(
        after,
        capture_ordinal=3,
        physical_sequence=3,
        completed_append_watermark=2,
        confirmed_at_sequence=3,
        confirmed_effect_domain="text_menu",
    )
    result = project_light_action_m2_human_text(
        view(before, settling, after), tokenizer(), config(max_settling_events=1)
    )
    assert len(result.episodes[0].steps) == 2
    disposition = next(item for item in result.dispositions if item.event_id == "settle")
    assert (disposition.disposition, disposition.reason) == (
        "no_memory_write",
        "verified_settling_observation",
    )


def test_profile_suffix_or_nonhuman_scope_is_not_accepted():
    with pytest.raises(BoundaryError, match="invalid_projection_config"):
        project_light_action_m2_human_text(
            view(*human_items()), tokenizer(), config(input_profile="text-menu-v1")
        )
    with pytest.raises(BoundaryError, match="verified_human_view_required"):
        project_light_action_m2_human_text(
            replace(view(*human_items()), stream_scope="verified_agent_observed_inputs"),
            tokenizer(),
            config(),
        )


def test_page_and_action_byte_limits_reject_whole_input():
    with pytest.raises(BoundaryError, match="action_byte_limit_exceeded_no_truncation"):
        project_light_action_m2_human_text(
            view(*human_items()), tokenizer(), config(max_action_bytes=4)
        )
    with pytest.raises(BoundaryError, match="page_token_limit"):
        project_light_action_m2_human_text(
            view(*human_items()), tokenizer(), config(max_page_tokens=1)
        )
