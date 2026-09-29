"""Closed v2 M2 projection uses the published ordered text-menu catalog."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_memory_sequence_bridge import model as v1_model
from test_memory_sequence_bridge import observed as v1_observed
from test_memory_sequence_bridge import tokenizer as v1_tokenizer
from test_memory_sequence_bridge import view as v1_view
from tokenizers import Tokenizer

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.memory_sequence_bridge import (
    MemoryEpisodeProjectionConfig,
    MemoryEpisodeProjectionConfigV2,
    project_memory_episodes,
    v2_episode_projection_config,
)
from stpd.fullrun.memory_token_inputs import (
    encode_memory_texts,
    project_memory_profile_snapshot,
)
from stpd.fullrun.memory_training_prepare import fit_observed_memory_tokenizer
from stpd.fullrun.observed_input_sequence import ObservedInput, ObservedInputView, SourceEventRef
from stpd.models.dsimple_memory import ExperimentalDSimpleM2
from stpd.models.token_core import ScratchShape, ScratchTokenCore

FIXTURES = Path(__file__).parent / "fixtures" / "text_menu_v2"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _snapshot_sequence() -> tuple[dict, ...]:
    root = _fixture("targeted-root")
    selected = _fixture("targeted-select")["successor"]
    cancelled = json.loads(json.dumps(root))
    cancelled["menu"]["revision"] = 2
    reselected = json.loads(json.dumps(selected))
    reselected["menu"]["revision"] = 3
    target = json.loads(json.dumps(selected))
    target["menu"]["cursor"] = "card_confirmation"
    target["menu"]["revision"] = 4
    target["menu"]["selection"] = [
        {"role": "card", "referent_id": "card-C"},
        {"role": "target", "referent_id": "enemy-E"},
    ]
    target["menu_actions"]["actions"] = [
        {"action_id": "opaque-confirm", "kind": "native_input", "verb": "play",
         "label": "play", "subject_referent_id": "card-C",
         "arguments": [{"role": "target", "referent_id": "enemy-E"}],
         "effect_domain": "native_input"},
        {"action_id": "opaque-cancel", "kind": "system_selection",
         "verb": "cancel_selection", "label": "cancel_selection",
         "subject_referent_id": None, "arguments": [], "effect_domain": "text_menu"},
    ]
    target["menu_actions"]["total_count"] = 2
    target["menu_actions"]["materialized_count"] = 2
    pages = (root, selected, cancelled, reselected, target)
    for index, page in enumerate(pages, 1):
        page["sequence"] = index
        page["snapshot_id"] = f"v2-step-{index}"
    return pages


def _view(pages: tuple[dict, ...]) -> ObservedInputView:
    chosen_actions = (
        "v2-select-card-C", "v2-cancel-card-C", "v2-select-card-C",
        "v2-select-target-E", "opaque-confirm",
    )
    assert len(pages) == len(chosen_actions)
    items = tuple(ObservedInput(
        stream_id="managed:fixture", source_kind="managed_control_input_stream",
        event_id=f"event-{index}", source_sequence=index, snapshot=page,
        observation_mask=True, selected_action_id=chosen_actions[index - 1],
        choice_mask=True, delivery_status="not_applicable", delivery_mask=False,
        successor_snapshot=None, successor_relation="none", successor_observation_mask=False,
        causal_successor_mask=False, reset_before=index == 1,
        reset_reason="stream_start" if index == 1 else None,
        source_events=(SourceEventRef(index, "managed_run_event", f"event-{index}"),),
    ) for index, page in enumerate(pages, 1))
    return ObservedInputView("managed-fixture", "managed_engineering_control_inputs",
                             False, items)


def test_explicit_v2_tokenizer_and_episode_use_identical_ordered_complete_pages():
    pages = _snapshot_sequence()
    view = _view(pages)
    config = v2_episode_projection_config()
    tokens, count = fit_observed_memory_tokenizer(
        view, max_settling_events=0, input_profile="text-menu-v2")
    tokenizer = Tokenizer.from_str(tokens.decode())
    model = ExperimentalDSimpleM2(ScratchTokenCore(ScratchShape(
        tokenizer.get_vocab_size(), 8, 1, 2, 16, 0.0, 16384)))
    result = project_memory_episodes(
        view, tokenizer, model, max_observations=8, max_input_tokens=100000,
        projection_config=config)
    assert count == 1 and not result.diagnostics
    assert len(result.episodes) == 1
    steps = result.episodes[0].steps
    assert len(steps) == len(pages)
    assert tuple(step.label_key for step in steps) == (
        "v2-select-card-C", "v2-cancel-card-C", "v2-select-card-C",
        "v2-select-target-E", "opaque-confirm")
    for page, step in zip(pages, steps, strict=True):
        public = project_memory_profile_snapshot(page, "text-menu-v2")
        row = encode_memory_texts(tokenizer, public.state_text, public.action_texts,
                                  max_tokens=model.core.max_tokens, slots=model.slots)
        assert step.action_keys == public.action_ids
        assert step.page.tolist() == list(row.state)
        assert tuple(action.tolist() for action in step.actions) == tuple(
            list(action) for action in row.actions)
        assert public.candidate_digest and len(public.action_ids) == len(row.actions)
        assert all("card-C" not in text and "enemy-E" not in text and "opaque-" not in text
                   for text in (public.state_text, *public.action_texts))


def test_v1_config_bytes_and_default_projection_contract_stay_unchanged():
    config = MemoryEpisodeProjectionConfig("stpd/memory-episode-projection-config-v1", 64)
    assert asdict(config) == {"schema": "stpd/memory-episode-projection-config-v1",
                              "max_settling_events": 64}
    source = v1_view(v1_observed("original", 1, reset=True))
    model = v1_model()
    tokenizer = v1_tokenizer()
    defaults = project_memory_episodes(
        source, tokenizer, model, max_observations=2, max_input_tokens=100000)
    explicit = project_memory_episodes(
        source, tokenizer, model, max_observations=2, max_input_tokens=100000,
        projection_config=MemoryEpisodeProjectionConfig(
            "stpd/memory-episode-projection-config-v1", 0))
    assert defaults.event_mapping == explicit.event_mapping
    assert defaults.sources == explicit.sources
    assert defaults.episodes[0].steps[0].action_keys == explicit.episodes[0].steps[0].action_keys
    assert defaults.episodes[0].steps[0].page.tolist() == (
        explicit.episodes[0].steps[0].page.tolist())


def test_v2_rejects_unknown_mixed_profile_and_mutated_renderer():
    pages = _snapshot_sequence()
    view = _view(pages)
    config = v2_episode_projection_config()
    with pytest.raises(BoundaryError, match="unknown_text_menu_profile"):
        project_memory_profile_snapshot(pages[0], "text-menu-v3")
    with pytest.raises(ValueError, match="invalid memory episode projection config"):
        replace(config, renderer_id="stpd/m2-canonical-current-page-v1")
    with pytest.raises(ValueError, match="invalid memory episode projection config"):
        replace(config, renderer_wrapper="unknown-wrapper")
    with pytest.raises(ValueError, match="invalid memory episode projection config"):
        replace(config, input_profile="text-menu-v1")
    with pytest.raises(ValueError, match="invalid memory episode projection config"):
        replace(config, max_settling_events=1)
    class ForgedConfig(MemoryEpisodeProjectionConfigV2):
        def __post_init__(self) -> None:
            pass

    forged = ForgedConfig("wrong-schema", 0, "text-menu-v1", "wrong-renderer",
                          config.renderer_text_menu_version, config.renderer_wrapper)
    wrong = json.loads(json.dumps(pages[2]))
    wrong["input_profile"] = "text-menu-v1"
    mixed = _view((*pages[:2], wrong, *pages[3:]))
    with pytest.raises(BoundaryError):
        fit_observed_memory_tokenizer(mixed, max_settling_events=0,
                                      input_profile="text-menu-v2")
    tokens, _ = fit_observed_memory_tokenizer(
        view, max_settling_events=0, input_profile="text-menu-v2")
    tokenizer = Tokenizer.from_str(tokens.decode())
    model = ExperimentalDSimpleM2(ScratchTokenCore(ScratchShape(
        tokenizer.get_vocab_size(), 8, 1, 2, 16, 0.0, 16384)))
    with pytest.raises(BoundaryError, match="projection_config_mismatch"):
        project_memory_episodes(
            view, tokenizer, model, max_observations=8, max_input_tokens=100000,
            projection_config=forged)
    tampered = v2_episode_projection_config()
    object.__setattr__(tampered, "renderer_id", "wrong-renderer")
    with pytest.raises(ValueError, match="invalid memory episode projection config"):
        project_memory_episodes(
            view, tokenizer, model, max_observations=8, max_input_tokens=100000,
            projection_config=tampered)
    result = project_memory_episodes(
        mixed, tokenizer, model, max_observations=8, max_input_tokens=100000,
        projection_config=config)
    assert not result.episodes and result.diagnostics
    with pytest.raises(BoundaryError, match="v2_source_or_settling_mismatch"):
        project_memory_episodes(
            view, tokenizer, model, max_observations=8, max_input_tokens=100000,
            max_settling_events=1, projection_config=config)
    wrong_kind = replace(view, inputs=(replace(view.inputs[0],
                                             source_kind="human_input_stream"),
                                       *view.inputs[1:]))
    with pytest.raises(BoundaryError, match="observed_source_profile_mismatch"):
        fit_observed_memory_tokenizer(wrong_kind, max_settling_events=0,
                                      input_profile="text-menu-v2")
    with pytest.raises(BoundaryError, match="v2_source_or_settling_mismatch"):
        project_memory_episodes(
            wrong_kind, tokenizer, model, max_observations=8, max_input_tokens=100000,
            projection_config=config)
