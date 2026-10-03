"""Synthetic matched M0 controls over the exact public chain token input."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
import torch
from test_public_m2_sequences import fixture
from tokenizers import Tokenizer

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.public_m2_sequences import (
    compile_public_m2_input,
    project_public_m2_chains,
)
from stpd.models.token_core import ScratchShape
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.public_m0_matched_engine import (
    PublicM0MatchedConfig,
    PublicM0MatchedEngine,
    _optimizer_digest,
    load_public_m0_matched_weights,
)


def _source(train_count: int = 9, dev_count: int = 2):
    train_samples, train_evidence = fixture(train_count)
    dev_samples, dev_evidence = fixture(dev_count, run="dev", split="dev")
    chains = project_public_m2_chains(
        train_samples + dev_samples, train_evidence + dev_evidence,
    )
    fit = tuple(chain for chain in chains if chain.split == "train")
    return compile_public_m2_input(
        chains, fit, source_binding_digest="a" * 64,
        max_state_tokens=1000, max_action_bytes=1000,
    )


def _config(source, *, epochs: int = 1, window_steps: int = 8,
            tiny: bool = True) -> PublicM0MatchedConfig:
    vocab = Tokenizer.from_str(source.state_tokenizer.decode()).get_vocab_size()
    shape = (ScratchShape(vocab, 12, 1, 2, 24, 0.1, source.max_state_tokens)
             if tiny else None)
    return PublicM0MatchedConfig(
        source_digest=source.identity, max_chain_steps=16, max_total_steps=32,
        max_total_input_tokens=100_000, max_actions_per_step=2,
        epochs=epochs, window_steps=window_steps, shape_override=shape,
    )


def test_mid_chain_resume_matches_uninterrupted_and_dev_is_read_only() -> None:
    torch.set_num_threads(1)
    source = _source()
    config = _config(source)
    assert config.learning_rate == 3e-4
    original = PublicM0MatchedEngine(source, config)
    first = original.advance_window()
    assert (first.label_count, first.optimizer_updates, first.window_cursor) == (8, 1, 8)
    raw = original.checkpoint()
    metric = original.evaluate_dev()
    assert metric.label_count == 2 and 0 <= metric.top1_accuracy <= 1
    assert original.checkpoint() == raw
    resumed = PublicM0MatchedEngine(source, config)
    resumed.restore(raw)
    original.advance_window()
    resumed.advance_window()
    assert original.finished and resumed.finished
    assert original.label_count == resumed.label_count == 9
    assert original.optimizer_updates == resumed.optimizer_updates == 2
    assert original.checkpoint() == resumed.checkpoint()
    exported = decode_checkpoint(original.export_weights())
    assert exported["completed_epochs"] == 1 and exported["run_complete"] is True
    assert exported["checkpoint_digest"] == hashlib.sha256(original.checkpoint()).hexdigest()
    loaded = load_public_m0_matched_weights(
        original.export_weights(), source, config, completed_epochs=1,
    )
    assert not loaded.training and loaded.state_projection.weight.device.type == "cpu"


def test_same_five_epoch_run_exports_incomplete_intermediates() -> None:
    torch.set_num_threads(1)
    source = _source(1, 1)
    engine = PublicM0MatchedEngine(source, _config(source, epochs=5))
    exports = []
    for epoch in range(1, 6):
        engine.advance_window()
        if epoch in {1, 3, 5}:
            exports.append(decode_checkpoint(engine.export_weights()))
    assert [item["completed_epochs"] for item in exports] == [1, 3, 5]
    assert [item["optimizer_updates"] for item in exports] == [1, 3, 5]
    assert [item["run_complete"] for item in exports] == [False, False, True]
    assert all(item["source_digest"] == source.identity for item in exports)


def test_invalid_optimizer_step_rejected_after_recomputed_digest_and_poison() -> None:
    torch.set_num_threads(1)
    source = _source(2, 1)
    config = _config(source)
    producer = PublicM0MatchedEngine(source, config)
    producer.advance_window()
    value = decode_checkpoint(producer.checkpoint())
    first = next(iter(value["optimizer"]["state"]))
    value["optimizer"]["state"][first]["step"] = torch.tensor(1, dtype=torch.int64)
    value["optimizer_digest"] = _optimizer_digest(
        value["optimizer"], producer.parameter_names,
    )
    victim = PublicM0MatchedEngine(source, config)
    with pytest.raises(BoundaryError):
        victim.restore(encode_checkpoint(value))
    with pytest.raises(BoundaryError):
        victim.advance_window()


def test_source_and_window_limits_fail_before_training() -> None:
    torch.set_num_threads(1)
    source = _source(2, 1)
    with pytest.raises(BoundaryError, match="source_identity_mismatch"):
        PublicM0MatchedEngine(source, replace(_config(source), source_digest="b" * 64))
    with pytest.raises(BoundaryError, match="window_limit_exceeded"):
        PublicM0MatchedEngine(source, replace(_config(source), max_window_tokens=1))


def test_production_shape_reuses_existing_m0_constructor() -> None:
    torch.set_num_threads(1)
    source = _source(1, 1)
    engine = PublicM0MatchedEngine(source, _config(source, tiny=False))
    assert isinstance(engine.model.core.shape, ScratchShape)
    assert (engine.model.core.shape.width, engine.model.core.shape.layers,
            engine.model.core.shape.heads, engine.model.core.shape.feedforward,
            engine.model.core.shape.dropout) == (384, 2, 6, 1536, 0.1)


def test_cuda_tagged_export_metadata_loads_on_cpu_contract_only() -> None:
    """This header fixture is not evidence of actual CUDA training."""
    torch.set_num_threads(1)
    source = _source(1, 1)
    config = _config(source)
    producer = PublicM0MatchedEngine(source, config)
    producer.advance_window()
    value = decode_checkpoint(producer.export_weights())
    value["config"]["device"] = "cuda:0"
    loaded = load_public_m0_matched_weights(
        encode_checkpoint(value), source, replace(config, device="cuda:0"),
        completed_epochs=1,
    )
    assert loaded.state_projection.weight.device.type == "cpu"
