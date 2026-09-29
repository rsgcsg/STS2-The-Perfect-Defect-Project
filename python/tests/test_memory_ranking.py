"""Tiny CPU-only durability checks for experimental M2; no source admission."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import torch

from spireagent.json_boundary import BoundaryError
from stpd.models.dsimple_sequence_training import MemorySequenceEpisode, MemorySequenceStep
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.memory_ranking import (
    MemoryConfig,
    MemoryRankingEngine,
    MemoryTrainingInput,
    load_memory_export,
)

TOKENIZER = "a" * 64


@pytest.fixture(autouse=True)
def two_cpu_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def source(*, count: int = 3, optional: bool = True) -> MemoryTrainingInput:
    episodes = []
    for number in range(count):
        name = f"episode-{number}"
        steps = tuple(
            MemorySequenceStep(
                name, index, torch.tensor([1, 2 + number + index], dtype=torch.long),
                ("A", "B"), (torch.tensor([6]), torch.tensor([7])),
                "A" if index % 2 == 0 else "B", reset_before=index == 0,
                previous_actual_action=(torch.tensor([8]) if optional and number > 0
                                        and index == 1 else None),
                public_feedback=(torch.tensor([9]) if optional and number > 0
                                 and index == 1 else None),
            )
            for index in range(3)
        )
        episodes.append(MemorySequenceEpisode(name, steps))
    return MemoryTrainingInput("synthetic-source", TOKENIZER, tuple(episodes))


def config(**changes) -> MemoryConfig:
    return replace(MemoryConfig(vocab_size=32, episode_count=3, dropout=0.2,
                                max_chunk_steps=2, max_episode_input_tokens=100,
                                max_total_input_tokens=300), **changes)


def finish(engine: MemoryRankingEngine) -> bytes:
    while engine.next_episode < engine.config.episode_count:
        assert torch.isfinite(torch.tensor(engine.advance()))
    return engine.checkpoint()


def test_snapshot_input_is_independent_of_live_engine():
    engine = MemoryRankingEngine(source(), config())
    before = engine.checkpoint()
    first = engine.snapshot_input()
    second = engine.snapshot_input()
    first.episodes[0].steps[0].page[0] = 11
    first.episodes[1].steps[1].previous_actual_action[0] = 12
    assert second.episodes[0].steps[0].page.tolist() == [1, 2]
    assert second.episodes[1].steps[1].previous_actual_action.tolist() == [8]
    assert engine.checkpoint() == before


def test_same_and_new_process_resume_match_uninterrupted_with_dropout(tmp_path: Path):
    inputs, settings = source(), config()
    baseline = MemoryRankingEngine(inputs, settings)
    baseline_raw = finish(baseline)
    paused = MemoryRankingEngine(inputs, settings)
    paused.advance()
    partial = paused.checkpoint()
    state = decode_checkpoint(partial)
    assert state["next_episode"] == 1
    assert all("previous_action" not in name and "feedback" not in name
               for name in state["active_parameter_names"])
    assert len(state["active_parameter_names"]) < len(state["parameter_names"])
    resumed = MemoryRankingEngine(inputs, settings)
    resumed.restore(partial)
    assert finish(resumed) == baseline_raw

    checkpoint_path = tmp_path / "checkpoint.bin"
    output_path = tmp_path / "resumed.bin"
    checkpoint_path.write_bytes(partial)
    script = """
import sys
import torch
from pathlib import Path
from stpd.models.dsimple_sequence_training import MemorySequenceEpisode, MemorySequenceStep
from stpd.workers.memory_ranking import MemoryConfig, MemoryRankingEngine, MemoryTrainingInput
torch.set_num_threads(2)
episodes = []
for number in range(3):
    name = f'episode-{number}'
    steps = tuple(MemorySequenceStep(
        name, index, torch.tensor([1, 2 + number + index]), ('A', 'B'),
        (torch.tensor([6]), torch.tensor([7])), 'A' if index % 2 == 0 else 'B',
        reset_before=index == 0,
        previous_actual_action=torch.tensor([8]) if number > 0 and index == 1 else None,
        public_feedback=torch.tensor([9]) if number > 0 and index == 1 else None,
    ) for index in range(3))
    episodes.append(MemorySequenceEpisode(name, steps))
source = MemoryTrainingInput('synthetic-source', 'a' * 64, tuple(episodes))
config = MemoryConfig(vocab_size=32, episode_count=3, dropout=0.2,
                      max_chunk_steps=2, max_episode_input_tokens=100,
                      max_total_input_tokens=300)
engine = MemoryRankingEngine(source, config)
engine.restore(Path(sys.argv[1]).read_bytes())
while engine.next_episode < config.episode_count:
    engine.advance()
Path(sys.argv[2]).write_bytes(engine.checkpoint())
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(checkpoint_path), str(output_path)],
        check=False, capture_output=True, text=True, timeout=30,
    )
    assert child.returncode == 0, child.stderr
    assert output_path.read_bytes() == baseline_raw


@pytest.mark.parametrize(
    "change", ["slots", "reset", "gate", "seed", "source", "tokenizer", "data"]
)
def test_restore_rejects_config_and_actual_input_identity_changes(change: str):
    inputs = source()
    settings = config()
    engine = MemoryRankingEngine(inputs, settings)
    engine.advance()
    raw = engine.checkpoint()
    if change == "slots":
        settings = replace(settings, slots=8)
    elif change == "reset":
        settings = replace(settings, reset_each_step=True)
    elif change == "gate":
        settings = replace(settings, gated=True)
    elif change == "seed":
        settings = replace(settings, seed=99)
    elif change == "source":
        inputs = replace(inputs, source_id="other-source")
    elif change == "tokenizer":
        inputs = replace(inputs, tokenizer_sha256="b" * 64)
    else:
        first = inputs.episodes[0]
        changed = replace(first.steps[0], page=torch.tensor([1, 11]))
        inputs = replace(inputs, episodes=(replace(first, steps=(changed, *first.steps[1:])),
                                           *inputs.episodes[1:]))
    with pytest.raises(BoundaryError, match="resume_identity_mismatch"):
        MemoryRankingEngine(inputs, settings).restore(raw)


@pytest.mark.parametrize("corruption", ["model", "optimizer_missing", "optimizer_binding",
                                       "optimizer_dtype", "model_shape"])
def test_corrupt_checkpoint_rejected(corruption: str):
    engine = MemoryRankingEngine(source(), config())
    engine.advance()
    value = decode_checkpoint(engine.checkpoint())
    if corruption == "model":
        name = next(iter(value["model"]))
        value["model"][name].flatten()[0] += 1
    elif corruption == "model_shape":
        name = next(iter(value["model"]))
        value["model"][name] = value["model"][name].flatten()
    elif corruption == "optimizer_missing":
        value["optimizer"]["state"].pop(next(iter(value["optimizer"]["state"])))
    elif corruption == "optimizer_binding":
        state = value["optimizer"]["state"]
        a, b = list(state)[:2]
        state[a], state[b] = state[b], state[a]
    else:
        state = value["optimizer"]["state"]
        index = next(iter(state))
        state[index]["exp_avg"] = state[index]["exp_avg"].double()
    with pytest.raises(BoundaryError):
        MemoryRankingEngine(source(), config()).restore(encode_checkpoint(value))


def test_negative_adam_second_moment_rejected_even_with_recomputed_digest():
    from stpd.workers.memory_ranking import _optimizer_digest

    engine = MemoryRankingEngine(source(), config())
    engine.advance()
    value = decode_checkpoint(engine.checkpoint())
    state = value["optimizer"]["state"]
    index = next(iter(state))
    state[index]["exp_avg_sq"].flatten()[0] = -1
    value["optimizer_digest"] = _optimizer_digest(
        value["optimizer"], value["parameter_names"],
    )
    with pytest.raises(BoundaryError, match="optimizer_tensor_mismatch"):
        MemoryRankingEngine(source(), config()).restore(encode_checkpoint(value))


@pytest.mark.parametrize("removed", ["write_queries", "previous_action_marker",
                                      "feedback_embedding.weight"])
def test_recorded_optional_adam_state_and_mandatory_state_inventory(removed: str):
    engine = MemoryRankingEngine(source(), config())
    engine.advance()
    engine.advance()
    raw = engine.checkpoint()
    state = decode_checkpoint(raw)
    active = state["active_parameter_names"]
    assert "previous_action_marker" in active
    assert "feedback_marker" in active
    assert "feedback_embedding.weight" in active
    MemoryRankingEngine(source(), config()).restore(raw)
    # Rewriting the recorded inventory and its digest cannot hide a missing
    # parameter that a completed labeled update must have used.
    missing = decode_checkpoint(raw)
    index = missing["parameter_names"].index(removed)
    missing["optimizer"]["state"].pop(index)
    missing["active_parameter_names"] = tuple(
        name for name in missing["active_parameter_names"] if name != removed
    )
    from stpd.workers.memory_ranking import _optimizer_digest
    missing["optimizer_digest"] = _optimizer_digest(
        missing["optimizer"], missing["parameter_names"],
    )
    with pytest.raises(BoundaryError, match="optimizer_inventory_mismatch"):
        MemoryRankingEngine(source(), config()).restore(encode_checkpoint(missing))


@pytest.mark.parametrize(
    ("optional_position", "chunk_steps", "reset", "expected"),
    [(0, 1, False, False), (2, 3, False, False), (0, 3, True, False),
     (0, 3, False, True), (1, 3, True, True)],
)
def test_optional_adam_state_follows_labeled_chunk_graph(
    optional_position: int, chunk_steps: int, reset: bool, expected: bool,
):
    original = source(count=1, optional=False)
    episode = original.episodes[0]
    steps = tuple(replace(step, label_key="A" if position == 1 else None,
                          previous_actual_action=(torch.tensor([8])
                                                  if position == optional_position else None),
                          public_feedback=(torch.tensor([9])
                                           if position == optional_position else None))
                  for position, step in enumerate(episode.steps))
    inputs = replace(original, episodes=(replace(episode, steps=steps),))
    engine = MemoryRankingEngine(
        inputs, config(episode_count=1, max_chunk_steps=chunk_steps, reset_each_step=reset),
    )
    engine.advance()
    state = decode_checkpoint(engine.checkpoint())
    assert ("previous_action_marker" in state["active_parameter_names"]) is expected
    assert ("feedback_embedding.weight" in state["active_parameter_names"]) is expected
    MemoryRankingEngine(inputs, engine.config).restore(engine.checkpoint())


def test_implementation_identity_is_checked_on_restore():
    inputs, settings = source(count=1), config(episode_count=1)
    engine = MemoryRankingEngine(inputs, settings)
    engine.advance()
    value = decode_checkpoint(engine.checkpoint())
    assert len(value["runtime"]["implementation_sha256"]) == 64
    value["runtime"]["implementation_sha256"] = "0" * 64
    with pytest.raises(BoundaryError, match="resume_identity_mismatch"):
        MemoryRankingEngine(inputs, settings).restore(encode_checkpoint(value))


@pytest.mark.parametrize(
    ("slots", "gated", "reset"), [(1, False, False), (8, True, False), (1, False, True)]
)
def test_export_reload_replays_scores_with_exact_candidate_order_and_config(
    slots: int, gated: bool, reset: bool,
):
    def assert_keyed_scores(actual, actual_keys, original, original_keys):
        assert len(actual_keys) == len(original_keys) == len(actual)
        assert len(set(original_keys)) == len(original_keys)
        assert set(actual_keys) == set(original_keys)
        assert bool(torch.isfinite(actual).all()), "nonfinite reordered scores"
        assert bool(torch.isfinite(original).all()), "nonfinite original scores"
        assert actual.dtype == original.dtype == torch.float32
        by_key = dict(zip(original_keys, original, strict=True))
        expected = torch.stack([by_key[key] for key in actual_keys])
        # Reordering a candidate batch can change the FP32 reduction path on
        # Windows while each score must still belong to its exact action key.
        torch.testing.assert_close(actual, expected, atol=1e-5, rtol=1.3e-6, equal_nan=False)

    settings = config(episode_count=1, slots=slots, gated=gated, reset_each_step=reset)
    inputs = source(count=1)
    engine = MemoryRankingEngine(inputs, settings)
    engine.advance()
    raw = engine.export()
    assert set(decode_checkpoint(raw)) == {
        "schema", "config", "tokenizer_sha256", "implementation_sha256",
        "weights", "weights_digest",
    }
    loaded = load_memory_export(raw, settings, TOKENIZER)
    engine.model.eval()
    item = inputs.episodes[0]
    for subject in (engine.model, loaded):
        memory = subject.initial_memory()
        with torch.no_grad():
            scores = []
            for step in item.steps:
                value, memory = subject.step(
                    step.page, step.actions, memory, reset_before=step.reset_before,
                )
                scores.append(value)
            final = item.steps[-1]
            original = subject.score(memory, final.actions)
            permuted = subject.score(memory, final.actions[::-1])
            assert_keyed_scores(permuted, final.action_keys[::-1], original, final.action_keys)
            # The fixture's two actions have distinct real model scores, so
            # attaching the original keys to the reordered scores must fail.
            assert abs(float(original[0] - original[1])) > 1e-3
            with pytest.raises(AssertionError):
                assert_keyed_scores(permuted, final.action_keys, original, final.action_keys)
            outside_tolerance = permuted.clone()
            outside_tolerance[0] += 1e-3
            with pytest.raises(AssertionError):
                assert_keyed_scores(outside_tolerance, final.action_keys[::-1],
                                    original, final.action_keys)
            for invalid in (float("nan"), float("inf"), -float("inf")):
                nonfinite = permuted.clone()
                nonfinite[0] = invalid
                with pytest.raises(AssertionError, match="nonfinite reordered scores"):
                    assert_keyed_scores(nonfinite, final.action_keys[::-1],
                                        original, final.action_keys)
        if subject is engine.model:
            reference = (memory, scores, permuted)
        else:
            torch.testing.assert_close(memory, reference[0], atol=0, rtol=0)
            for left, right in zip(scores, reference[1], strict=True):
                torch.testing.assert_close(left, right, atol=0, rtol=0)
            torch.testing.assert_close(permuted, reference[2], atol=0, rtol=0)
    for wrong in (replace(settings, slots=8 if slots == 1 else 1),
                  replace(settings, reset_each_step=not reset),
                  replace(settings, gated=not gated)):
        with pytest.raises(BoundaryError, match="export_identity_mismatch"):
            load_memory_export(raw, wrong, TOKENIZER)
    with pytest.raises(BoundaryError, match="export_identity_mismatch"):
        load_memory_export(raw, settings, "b" * 64)
    changed_implementation = decode_checkpoint(raw)
    changed_implementation["implementation_sha256"] = "0" * 64
    with pytest.raises(BoundaryError, match="export_identity_mismatch"):
        load_memory_export(encode_checkpoint(changed_implementation), settings, TOKENIZER)
    value = decode_checkpoint(raw)
    name = next(iter(value["weights"]))
    value["weights"][name].flatten()[0] += 1
    with pytest.raises(BoundaryError, match="weights_digest_mismatch"):
        load_memory_export(encode_checkpoint(value), settings, TOKENIZER)


def test_alias_copy_internal_tamper_late_invalid_and_episode_reset():
    inputs = source()
    settings = config()
    engine = MemoryRankingEngine(inputs, settings)
    original = engine.input_digest
    inputs.episodes[0].steps[0].page[0] = 10
    assert engine.input_digest == original
    with patch.object(engine.model, "initial_memory", wraps=engine.model.initial_memory) as reset:
        trained = finish(engine)
    assert reset.call_count == 3
    assert trained == finish(MemoryRankingEngine(source(), settings))
    tampered = MemoryRankingEngine(source(), settings)
    tampered._episodes[0].steps[0].page[0] = 10
    with pytest.raises(BoundaryError, match="runtime_or_input_changed"):
        tampered.advance()
    with pytest.raises(BoundaryError, match="failed_engine"):
        tampered.checkpoint()

    bad = source()
    final = bad.episodes[-1]
    last = replace(final.steps[-1], actions=(torch.tensor([6]), torch.tensor([32])))
    bad = replace(bad, episodes=(*bad.episodes[:-1],
                                 replace(final, steps=(*final.steps[:-1], last))))
    with patch.object(torch.optim.AdamW, "step", autospec=True) as update:
        with pytest.raises(BoundaryError, match="invalid_episode_step"):
            MemoryRankingEngine(bad, settings)
        update.assert_not_called()


def test_mid_episode_failure_cannot_checkpoint_or_continue():
    settings = config(episode_count=1, max_chunk_steps=1)
    engine = MemoryRankingEngine(source(count=1), settings)
    original = engine.optimizer.step
    calls = 0

    def fail_after_update(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 2:
            raise RuntimeError("synthetic optimizer interruption")
        return result

    with (patch.object(engine.optimizer, "step", side_effect=fail_after_update),
          pytest.raises(RuntimeError, match="interruption")):
        engine.advance()
    assert calls == 2 and engine.next_episode == 0
    for operation in (engine.advance, engine.checkpoint, engine.export):
        with pytest.raises(BoundaryError, match="failed_engine"):
            operation()
