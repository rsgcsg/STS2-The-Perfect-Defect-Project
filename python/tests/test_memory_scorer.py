"""CPU checks for exported M2 observation scoring, not policy quality."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Event
from unittest.mock import patch

import pytest
import torch
from test_text_menu_data import snapshot
from tokenizers import Tokenizer, models, pre_tokenizers

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes
from stpd.fullrun.memory_token_inputs import encode_memory_texts
from stpd.fullrun.text_menu_inputs import project_text_menu_snapshot
from stpd.fullrun.token_inputs import encode_texts
from stpd.models.dsimple_sequence_training import MemorySequenceEpisode, MemorySequenceStep
from stpd.policy.memory_scorer import OnlineM2Scorer
from stpd.workers.memory_ranking import (
    MemoryConfig,
    MemoryRankingEngine,
    MemoryTrainingInput,
    load_memory_export,
)


@pytest.fixture(autouse=True)
def one_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def tokenizer() -> Tokenizer:
    words = ("Information", "Play", "Defend", "open_information", "play", "combat_turn",
             "player", "cards", "hand_card", "Block", "Gain", "phase", "name", "description",
             "CURRENT_PAGE", "CURRENT_MENU", "display_text", "native_input", "system_navigation",
             "subject", "arguments", "kind", "verb", "ordinal", "root", "run", "floor",
             "hp", "content", "state", "visible")
    vocabulary = {"[UNK]": 0, **{word: index for index, word in enumerate(words, 1)}}
    value = Tokenizer(models.WordLevel(vocabulary, unk_token="[UNK]"))
    value.pre_tokenizer = pre_tokenizers.Whitespace()
    return value


def page(name: str, sequence: int) -> dict:
    value = snapshot(name)
    value["sequence"] = sequence
    value["session"] = {"runtime_instance_id": "runtime-1",
                        "environment_fingerprint": "environment-1"}
    return value


@pytest.fixture
def exported():
    token = tokenizer()
    token_bytes = token.to_str().encode()
    config = MemoryConfig(vocab_size=32, episode_count=1, max_tokens=512,
                          max_chunk_input_tokens=4096, max_episode_input_tokens=8192,
                          max_total_input_tokens=8192, max_actions_per_step=4,
                          cpu_threads=1)
    steps = []
    for position, value in enumerate((page("first", 1), page("second", 2))):
        public = project_text_menu_snapshot(decode_json(json_bytes(value)))
        row = encode_memory_texts(token, public.state_text, public.action_texts,
                                  max_tokens=config.max_tokens, slots=config.slots)
        steps.append(MemorySequenceStep(
            "episode", position, torch.tensor(row.state, dtype=torch.long),
            public.action_ids,
            tuple(torch.tensor(action, dtype=torch.long) for action in row.actions),
            public.action_ids[0] if position == 1 else None,
            reset_before=position == 0,
        ))
    source = MemoryTrainingInput("synthetic", hashlib.sha256(token_bytes).hexdigest(),
                                 (MemorySequenceEpisode("episode", tuple(steps)),))
    engine = MemoryRankingEngine(source, config)
    assert torch.isfinite(torch.tensor(engine.advance()))
    return engine.export(), config, token_bytes


def scorer(exported) -> OnlineM2Scorer:
    weights, config, token_bytes = exported
    return OnlineM2Scorer.from_export(weights, config, token_bytes)


def test_m2_page_and_light_actions_have_independent_token_limits():
    token = tokenizer()
    state = "Defend " * 8
    actions = ("Play Defend",)
    page_tokens = len(token.encode("OBS\n" + state + "\n").ids)
    capacity = page_tokens + 2
    row = encode_memory_texts(token, state, actions, max_tokens=capacity, slots=1)
    assert len(row.state) + 2 == capacity
    assert len(row.actions[0]) <= capacity
    with pytest.raises(BoundaryError, match="joint_limit_exceeded_no_truncation"):
        encode_texts(token, state, actions, max_tokens=capacity)
    with pytest.raises(BoundaryError, match="m2_limit_exceeded_no_truncation"):
        encode_memory_texts(token, state, actions, max_tokens=capacity - 1, slots=1)
    with pytest.raises(BoundaryError, match="m2_limit_exceeded_no_truncation"):
        encode_memory_texts(token, "Defend", ("Play " * capacity,),
                            max_tokens=capacity, slots=1)


def test_real_export_multistep_matches_offline_same_weights_and_reads_once(exported):
    weights, config, token_bytes = exported
    online = scorer(exported)
    offline = load_memory_export(weights, config, hashlib.sha256(token_bytes).hexdigest()).eval()
    token = Tokenizer.from_str(token_bytes.decode())
    memory = offline.initial_memory()
    with patch.object(online._model.core, "contextualize",
                      wraps=online._model.core.contextualize) as page_calls:
        for position, value in enumerate((page("first", 1), page("second", 2))):
            actual = online.observe_and_score(continuity_token="run-A",
                                              snapshot_bytes=json_bytes(value))
            public = project_text_menu_snapshot(decode_json(json_bytes(value)))
            row = encode_memory_texts(token, public.state_text, public.action_texts,
                                      max_tokens=config.max_tokens, slots=config.slots)
            assert row == encode_memory_texts(
                online._tokenizer, public.state_text, public.action_texts,
                max_tokens=online._model.core.max_tokens, slots=online._model.slots)
            with torch.inference_mode():
                expected, memory = offline.step(
                    torch.tensor(row.state),
                    tuple(torch.tensor(action) for action in row.actions), memory,
                    previous_actual_action=None, feedback=None,
                    reset_before=position == 0,
                )
            assert actual.action_ids == public.action_ids
            assert actual.candidate_digest == public.candidate_digest
            assert actual.scores == tuple(float(v) for v in expected.tolist())
            assert len(actual.scores) == len(public.action_ids)
            assert actual.scores[0] != actual.scores[1]
            assert page_calls.call_count == position + 1
            # Whitespace and object key order change no parsed snapshot fact.
            assert online.observe_and_score(
                continuity_token="run-A",
                snapshot_bytes=json.dumps(value, indent=2).encode()) == actual
            assert page_calls.call_count == position + 1
    reordered = page("first", 1)
    reordered["snapshot_id"] = "opaque-snapshot-reordered"
    reordered["menu_actions"]["actions"].reverse()
    actual = scorer(exported).observe_and_score(
        continuity_token="run-B", snapshot_bytes=json_bytes(reordered))
    public = project_text_menu_snapshot(decode_json(json_bytes(reordered)))
    row = encode_memory_texts(token, public.state_text, public.action_texts,
                              max_tokens=config.max_tokens, slots=config.slots)
    with torch.inference_mode():
        expected, _ = offline.step(torch.tensor(row.state),
                                   tuple(torch.tensor(action) for action in row.actions),
                                   offline.initial_memory(), reset_before=True)
    assert actual.action_ids == public.action_ids == ("opaque-play", "opaque-nav")
    torch.testing.assert_close(torch.tensor(actual.scores), expected,
                               atol=1e-5, rtol=1.3e-6)


def test_same_id_changed_full_snapshot_and_out_of_order_are_rejected(exported):
    online = scorer(exported)
    first = page("first", 1)
    original = online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(first))
    changed = deepcopy(first)
    changed["observed_at"] = "2026-09-26T00:00:01Z"
    with patch.object(online._model, "step", wraps=online._model.step) as step:
        assert online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(changed)) \
            == original
        assert step.call_count == 0
    assert online._sequence == 1
    changed["menu_actions"]["actions"][1]["label"] = "Altered choice"
    with pytest.raises(BoundaryError, match="snapshot_identity_reused"):
        online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(changed))
    with pytest.raises(BoundaryError, match="observation_order_reversed"):
        online.observe_and_score(continuity_token="A",
                                 snapshot_bytes=json_bytes(page("earlier", 1)))
    # Returning to the same semantic page with a new source snapshot advances.
    online.observe_and_score(continuity_token="A",
                             snapshot_bytes=json_bytes(page("second", 2)))
    online.observe_and_score(continuity_token="A",
                             snapshot_bytes=json_bytes(page("first-again", 3)))
    assert online._sequence == 3


def test_preflight_and_failed_score_do_not_commit_memory(exported):
    online = scorer(exported)
    first = page("first", 1)
    malformed = deepcopy(first)
    malformed["menu_actions"]["total_count"] += 1
    with patch.object(online._model, "step", wraps=online._model.step) as step:
        with pytest.raises(BoundaryError, match="complete_catalog_required"):
            online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(malformed))
        assert step.call_count == 0
    unbound = deepcopy(first)
    unbound["menu_actions"]["actions"][1]["subject_referent_id"] = "missing"
    with pytest.raises(BoundaryError, match="subject_binding_mismatch"):
        online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(unbound))
    incomplete = deepcopy(first)
    del incomplete["observed_at"]
    with pytest.raises(BoundaryError, match="snapshot_fields_mismatch"):
        online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(incomplete))
    with pytest.raises(BoundaryError, match="duplicate_key"):
        online.observe_and_score(continuity_token="A",
                                 snapshot_bytes=b'{"snapshot_id":"a","snapshot_id":"b"}')
    with pytest.raises(BoundaryError, match="non_finite_number"):
        online.observe_and_score(continuity_token="A", snapshot_bytes=b'{"x":NaN}')
    with (patch.object(online._model, "score", side_effect=lambda memory, actions:
                       torch.full((len(actions),), float("nan"))),
          pytest.raises(BoundaryError, match="nonfinite_or_unbound_scores")):
        online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(first))
    assert online._snapshot_id is None
    assert online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(first)) \
        == scorer(exported).observe_and_score(continuity_token="A",
                                               snapshot_bytes=json_bytes(first))


def test_all_limits_precede_write_without_catalog_truncation(exported):
    _, config, _ = exported
    first = page("first", 1)
    limited = scorer(exported)
    limited._config = replace(config, max_actions_per_step=1)
    with patch.object(limited._model, "step", wraps=limited._model.step) as step:
        with pytest.raises(BoundaryError, match="catalog_limit_no_truncation"):
            limited.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(first))
        assert step.call_count == 0
    token_limited = scorer(exported)
    token_limited._config = replace(config, max_chunk_input_tokens=1)
    with patch.object(token_limited._model, "step", wraps=token_limited._model.step) as step:
        with pytest.raises(BoundaryError, match="observation_budget_no_truncation"):
            token_limited.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(first))
        assert step.call_count == 0


def test_online_continuity_outlives_training_episode_budgets(exported):
    _, config, _ = exported
    online = scorer(exported)
    online._config = replace(config, max_episode_observations=2,
                             max_episode_input_tokens=1)
    with patch.object(online._model, "step", wraps=online._model.step) as step:
        for sequence in range(1, 5):
            result = online.observe_and_score(
                continuity_token="A",
                snapshot_bytes=json_bytes(page(f"turn-{sequence}", sequence)))
            assert len(result.scores) == 2
        assert step.call_count == 4


def test_concurrent_observation_rejected_without_second_memory_write(exported):
    online = scorer(exported)
    first = json_bytes(page("first", 1))
    started, release = Event(), Event()
    original_step = online._model.step

    def held_step(*args, **kwargs):
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release held model step")
        return original_step(*args, **kwargs)

    with patch.object(online._model, "step", side_effect=held_step) as step:
        with ThreadPoolExecutor(max_workers=1) as workers:
            first_call = workers.submit(online.observe_and_score,
                                        continuity_token="A", snapshot_bytes=first)
            try:
                assert started.wait(timeout=5)
                with pytest.raises(BoundaryError, match="concurrent_observation"):
                    online.observe_and_score(continuity_token="A", snapshot_bytes=first)
                assert step.call_count == 1
            finally:
                release.set()
            result = first_call.result(timeout=5)
        assert online.observe_and_score(continuity_token="A", snapshot_bytes=first) == result
        assert step.call_count == 1


def test_continuity_reset_retirement_and_session_drift(exported, monkeypatch):
    online = scorer(exported)
    first = page("first", 1)
    online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(first))
    drift = deepcopy(page("second", 2))
    drift["session"]["environment_fingerprint"] = "environment-2"
    with pytest.raises(BoundaryError, match="session_identity_changed"):
        online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(drift))
    reset = online.observe_and_score(continuity_token="B", snapshot_bytes=json_bytes(first))
    assert reset == scorer(exported).observe_and_score(
        continuity_token="B", snapshot_bytes=json_bytes(first))
    with pytest.raises(BoundaryError, match="retired_continuity"):
        online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(first))
    monkeypatch.setattr("stpd.policy.memory_scorer.MAX_RETIRED_CONTINUITIES", 1)
    with pytest.raises(BoundaryError, match="continuity_limit"):
        online.observe_and_score(continuity_token="C", snapshot_bytes=json_bytes(first))


def test_export_identity_and_tokenizer_are_checked(exported):
    weights, config, token_bytes = exported
    with pytest.raises(BoundaryError, match="export_identity_mismatch"):
        OnlineM2Scorer.from_export(weights, replace(config, seed=config.seed + 1),
                                   token_bytes)
    changed = Tokenizer.from_str(token_bytes.decode())
    changed.pre_tokenizer = pre_tokenizers.ByteLevel()
    with pytest.raises(BoundaryError, match="export_identity_mismatch"):
        OnlineM2Scorer.from_export(weights, config, changed.to_str().encode())
    with pytest.raises(BoundaryError, match="tokenizer_invalid"):
        OnlineM2Scorer.from_export(weights, config, b'{"broken":')


def test_raw_input_limits_reject_before_parsing_or_writing(exported):
    import stpd.policy.memory_scorer as module

    weights, config, token_bytes = exported
    # Lower the threshold, not the input, to exercise the actual bytes entrypoints.
    with patch.object(module, "MAX_TOKENIZER_BYTES", len(token_bytes) - 1), \
            patch.object(module, "load_memory_export") as load:
        with pytest.raises(BoundaryError, match="tokenizer_size_limit"):
            OnlineM2Scorer.from_export(weights, config, token_bytes)
        load.assert_not_called()
    online = scorer(exported)
    raw = json_bytes(page("first", 1))
    original = online._memory.clone()
    with patch.object(module, "MAX_SNAPSHOT_BYTES", len(raw) - 1), \
            patch.object(module, "decode_json") as decode, \
            patch.object(online._model, "step") as compute:
        with pytest.raises(BoundaryError, match="snapshot_size_limit"):
            online.observe_and_score(continuity_token="run", snapshot_bytes=raw)
        decode.assert_not_called()
        compute.assert_not_called()
    assert online._continuity is None and torch.equal(online._memory, original)
    assert len(online.observe_and_score(continuity_token="run", snapshot_bytes=raw).scores) == 2


def test_observed_training_and_online_use_identical_tokens_for_object_key_orders(exported):
    from test_memory_sequence_bridge import observed, view

    from stpd.fullrun.memory_sequence_bridge import project_memory_episodes

    def reverse_objects(value):
        if isinstance(value, dict):
            return {key: reverse_objects(item) for key, item in reversed(value.items())}
        if isinstance(value, list):
            return [reverse_objects(item) for item in value]
        return value

    baseline = None
    for value in (page("first", 1), reverse_objects(page("first", 1))):
        online = scorer(exported)
        item = replace(observed("first", 1, reset=True), snapshot=value)
        projected = project_memory_episodes(
            view(item), online._tokenizer, online._model,
            max_observations=1, max_input_tokens=4096)
        assert not projected.diagnostics
        (step,) = projected.episodes[0].steps
        with patch.object(online._model, "step", wraps=online._model.step) as compute:
            result = online.observe_and_score(
                continuity_token="run", snapshot_bytes=json.dumps(value).encode())
        args = compute.call_args.args
        assert torch.equal(step.page, args[0])
        assert len(step.actions) == len(args[1])
        assert all(torch.equal(left, right)
                   for left, right in zip(step.actions, args[1], strict=True))
        assert step.action_keys == result.action_ids
        if baseline is not None:
            assert torch.equal(step.page, baseline.page)
            assert all(torch.equal(left, right)
                       for left, right in zip(step.actions, baseline.actions, strict=True))
        baseline = step
