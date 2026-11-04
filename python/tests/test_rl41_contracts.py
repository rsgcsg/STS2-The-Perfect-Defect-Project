"""F0 interface smoke only; no claim of migrated learner/actor/data behavior."""
from __future__ import annotations

import dataclasses
import inspect
import random
import subprocess
import sys
from pathlib import Path
from typing import get_type_hints

import pytest
import torch
from test_rl41_w1 import model as model

import spireagent.rl_contracts as messages
import stpd.rl.checkpoint_state as checkpoint
import stpd.rl.prepared_batch as prepared
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


@pytest.mark.parametrize("module", [messages, prepared, checkpoint])
def test_all_contract_annotations_resolve(module):
    for value in vars(module).values():
        if inspect.isclass(value) and value.__module__ == module.__name__:
            get_type_hints(value)
    # Detect accidental imports from the reused environment's older editable checkout.
    assert Path(module.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1])


def test_import_in_fresh_cpu_worker():
    result = subprocess.run([sys.executable, "-c", """
from spireagent.rl_contracts import HotSegment, LocalMuScorer, MuPublication
from stpd.rl.prepared_batch import PreparedBatch, PrepareRequest
from stpd.rl.checkpoint_state import SelfContainedCheckpoint, convert_cp16
"""], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_hot_contract_has_values_bootstrap_and_distinct_ack_stage():
    assert {"outcome_probabilities", "mu", "compiled_input", "floor"} <= (
        messages.Behavior.__required_keys__)
    assert {"bootstrap", "steps", "original_commit", "training_generation"} <= (
        messages.HotSegment.__required_keys__)
    assert {"mu_snapshot_sha256", "successor_input_sha256", "outcome_probabilities",
            "floor"} == messages.Bootstrap.__required_keys__
    identity = messages.RunSequence("unit-run", "generation-a", 0)
    ack = messages.InputReceipt("request", identity, "a" * 64, 1.0,
                                "commit", messages.SegmentCommitted("actor/0"))
    discarded = messages.InputDisposition("request", identity, "discarded_on_restore", "CP")
    assert ack.stage == "recorded"
    assert discarded.request_key == ack.request_key
    assert ack.stage == "recorded"  # later disposition did not replace original receipt
    assert messages.RunSequence("unit-run", "generation-b", 0) != identity


def test_lost_begin_response_is_recoverable_from_original_receipt():
    # No new grant or World action is needed when the first response is lost.
    identity = messages.RunSequence("unit-run", "generation-a", 7)
    grant = messages.SegmentGrant(identity, "actor/7", {"version": 57}, 1.0)
    receipt = messages.InputReceipt("begin-key", identity, None, 1.0, "begin", grant)
    assert receipt.response is grant
    assert receipt.response.lease == "actor/7"
    assert receipt.response.mu["version"] == 57


@pytest.mark.parametrize("control_input", [
    {"operation": "episode_open", "actor": "a", "episode": "e"},
    {"operation": "begin", "actor": "a", "episode": "e", "previous_lease": None},
    {"operation": "commit", "segment_id": "a/0"},
    {"operation": "episode_end", "labels": {"actor": "a", "episode": "e"}},
])
def test_control_keys_ignore_transport_but_separate_restore_generations(control_input):
    value = {"run": "unit-run", "training_generation": "generation-a", **control_input}
    key = messages.control_request_key(value)
    assert key == messages.control_request_key({**value, "request_key": key,
                                               "pool_id": "new-pool", "clock": 10})
    assert key != messages.control_request_key({**value, "training_generation": "generation-b"})


def test_prepared_batch_exposes_complete_cpu_handoff():
    fields = {field.name for field in dataclasses.fields(prepared.PreparedBatch)}
    assert {"request", "row_refs", "frames", "advantages", "normalization", "minibatches",
            "behavior_outcomes", "outcome_targets", "hp_targets", "policy_mask", "td_mask",
            "terminal_mask", "hp_mask", "catalog_offsets", "behavior_probabilities"} <= fields
    assert {"members", "auxiliary", "minibatch_rows", "auxiliary_minibatches",
            "selection_rng_state", "max_output_bytes"} <= {
        field.name for field in dataclasses.fields(prepared.PrepareRequest)}


def test_cp_contract_roundtrips_native_adam_ema_and_rng_without_parents():
    # Tiny synthetic model tests only compatibility with the existing tensor-tree codec.
    model = torch.nn.Linear(2, 1)
    optimizer = torch.optim.Adam(model.parameters(), lr=4e-5)
    model(torch.ones(1, 2)).sum().backward()
    optimizer.step()
    construction = checkpoint.ModelConstruction(
        {"unit": "linear"}, {"unit": True}, {}, {}, {}, {"precision": "fp32"}, "b" * 64)
    rng = checkpoint.RngState(None, random.Random(37).getstate(), torch.get_rng_state(),
                              (torch.tensor([1, 2, 3], dtype=torch.uint8),))
    cp = checkpoint.SelfContainedCheckpoint(
        messages.RunSequence("unit-run", "generation-b", 16), construction,
        {"epochs": 1}, {"unit": True}, model.state_dict(), optimizer.state_dict(),
        (tuple(name for name, _ in model.named_parameters()),),
        {key: value.clone() for key, value in model.state_dict().items()},
        checkpoint.LearningCounters(362, 10, 48951, 42787, 57, 362), rng, None)
    state = dataclasses.asdict(cp)
    restored = decode_checkpoint(encode_checkpoint(state))
    assert restored["identity"] == state["identity"]
    assert restored["counters"] == state["counters"]
    assert restored["optimizer"]["param_groups"] == state["optimizer"]["param_groups"]
    for index, adam in state["optimizer"]["state"].items():
        for name, value in adam.items():
            assert torch.equal(restored["optimizer"]["state"][index][name], value)
    for name in cp.weights:
        assert torch.equal(restored["weights"][name], cp.weights[name])
        assert torch.equal(restored["ema"][name], cp.ema[name])
    assert restored["rng"]["learner_random"] == rng.learner_random
    assert torch.equal(restored["rng"]["cuda_rng"][0], rng.cuda_rng[0])
    assert not {"queue", "active", "leases", "aux", "parents"} & state.keys()


def test_cp16_conversion_preserves_learning_state_and_rejects_reused_generation(model):
    from test_rl41_w1 import assert_tree, construction, legacy_cp16
    state, names = legacy_cp16(model)
    source = checkpoint.CP16ConversionInput(
        state, construction(model), names, "a" * 64, "g-old", "g-new",
        {"run": "w1-unit"}, {"unit": True})
    converted = checkpoint.convert_cp16(source)
    assert converted.identity.sequence == 16
    assert converted.counters.updates == 362
    assert converted.counters.mu_version == 57
    assert_tree(converted.weights, state["weights"])
    assert_tree(converted.optimizer, state["optimizer"])
    assert_tree(converted.ema, state["ema"])
    assert converted.rng.learner_random == state["random"]
    assert converted.conversion.discarded_hot_state
    with pytest.raises(ValueError, match="explicit_CP16_and_new_generation_required"):
        checkpoint.convert_cp16(dataclasses.replace(source, target_generation="g-old"))
