"""CPU checks for exported M2 observation scoring, not policy quality."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest
import torch
from test_text_menu_data import snapshot
from tokenizers import Tokenizer, models, pre_tokenizers

from spireagent.json_boundary import BoundaryError, json_bytes
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
    vocabulary = {"[UNK]": 0}
    vocabulary.update({f"word{index}": index for index in range(1, 32)})
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
        public = project_text_menu_snapshot(value)
        row = encode_texts(token, public.state_text, public.action_texts,
                           max_tokens=config.max_tokens)
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


def test_real_export_multistep_matches_offline_same_weights_and_reads_once(exported):
    weights, config, token_bytes = exported
    online = scorer(exported)
    offline = load_memory_export(weights, config, hashlib.sha256(token_bytes).hexdigest())
    token = Tokenizer.from_str(token_bytes.decode())
    memory = offline.initial_memory()
    with patch.object(online._model.core, "contextualize",
                      wraps=online._model.core.contextualize) as page_calls:
        for position, value in enumerate((page("first", 1), page("second", 2))):
            actual = online.observe_and_score(continuity_token="run-A",
                                              snapshot_bytes=json_bytes(value))
            public = project_text_menu_snapshot(value)
            row = encode_texts(token, public.state_text, public.action_texts,
                               max_tokens=config.max_tokens)
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
            assert page_calls.call_count == position + 1
            # Whitespace and object key order change no parsed snapshot fact.
            assert online.observe_and_score(
                continuity_token="run-A",
                snapshot_bytes=json.dumps(value, indent=2).encode()) == actual
            assert page_calls.call_count == position + 1


def test_same_id_changed_full_snapshot_and_out_of_order_are_rejected(exported):
    online = scorer(exported)
    first = page("first", 1)
    online.observe_and_score(continuity_token="A", snapshot_bytes=json_bytes(first))
    changed = deepcopy(first)
    changed["observed_at"] = "2026-09-26T00:00:01Z"
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
    assert online._observations == 3


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
    assert online._observations == 0
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
