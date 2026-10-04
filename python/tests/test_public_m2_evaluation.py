"""Synthetic contract tests for independent, whole-chain public M2 dev evaluation."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
import torch
from tokenizers import Tokenizer, models

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes
from stpd.canonical import semantic_hash
from stpd.fullrun.public_m2_sequences import (
    EDGE_PROFILE,
    PublicM2Chain,
    PublicM2EvidenceRow,
    PublicM2Input,
)
from stpd.models.light_action_m2_training_data import LightActionM2TrainingStep
from stpd.models.token_core import ScratchShape
from stpd.workers.public_m2_engine import (
    PublicM2Engine,
    PublicM2EngineChain,
    PublicM2EngineConfig,
)
from stpd.workers.public_m2_evaluation import (
    PublicM2EvalLimits,
    build_public_m2_eval_selection,
    combine_public_m2_eval_shards,
    evaluate_public_m2_stage,
    open_public_m2_eval_session,
    partition_public_m2_eval_selection,
    public_m2_engine_input_digest,
    read_public_m2_eval_selection,
)


@pytest.fixture(autouse=True)
def one_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def _step(position: int, *, target: str = "a") -> LightActionM2TrainingStep:
    return LightActionM2TrainingStep(
        position, (1, 2, 3), ("b", "a"),
        ((256, 66, 257), (256, 65, 257)), target, None, position == 0,
    )


def _chain(name: str, split: str, count: int) -> PublicM2Chain:
    evidence = tuple(PublicM2EvidenceRow(
        semantic_hash(["transition", name, position]),
        semantic_hash(["archive", name]), "synthetic-session", name, position + 1,
        semantic_hash(["pre", name, position]),
        semantic_hash(["successor", name, position]),
        semantic_hash(["commit", name, position]),
        semantic_hash(["proof", name, position]), semantic_hash(["segment", name]),
        semantic_hash(["public-sample", name, position]),
    ) for position in range(count))
    chain_id = semantic_hash({
        "profile": EDGE_PROFILE, "split": split,
        "evidence": [row.__dict__ for row in evidence],
    })
    return PublicM2Chain(
        chain_id, split, evidence, tuple(_step(index) for index in range(count)),
    )


def _source(
    *, binding: str = "d", dev_counts: tuple[int, ...] = (15, 3),
) -> PublicM2Input:
    tokenizer = Tokenizer(models.WordLevel(
        {f"token{i}": i for i in range(258)}, unk_token="token0",
    )).to_str().encode()
    train = _chain("parent-train", "train", 2)
    dev = tuple(_chain(f"parent-dev-{count}", "dev", count) for count in dev_counts)
    return PublicM2Input(
        source_binding_digest=binding * 64,
        codec_fit_chain_ids=(train.chain_id,),
        codec_fit_transition_ids=tuple(row.transition_id for row in train.evidence),
        state_tokenizer=tokenizer, chains=(train, *dev), max_state_tokens=16,
        max_action_bytes=8,
    )


def _config(tokenizer_sha: str, source_digest: str) -> PublicM2EngineConfig:
    return PublicM2EngineConfig(
        source_digest=source_digest, state_tokenizer_sha256=tokenizer_sha,
        shape=ScratchShape(vocab_size=258, width=12, layers=1, heads=2,
                           feedforward=24, dropout=0.1, max_tokens=16),
        max_action_bytes=8, max_actions_per_step=2, max_chain_steps=13,
        max_total_steps=16, max_total_input_tokens=256, epochs=5,
        seed=1701,
    )


def _epoch_one_export():
    source = _source(binding="c", dev_counts=(13,))
    config = _config(hashlib.sha256(source.state_tokenizer).hexdigest(), source.identity)
    train = tuple(PublicM2EngineChain(chain.chain_id, chain.steps)
                  for chain in source.chains if chain.split == "train")
    dev = tuple(PublicM2EngineChain(chain.chain_id, chain.steps)
                for chain in source.chains if chain.split == "dev")
    engine = PublicM2Engine(train, dev, config)
    engine.advance_window()
    assert engine.completed_epochs == 1 and not engine.finished
    return config, engine, engine.export_weights()


def _selection(source: PublicM2Input, engine: PublicM2Engine):
    input_digest = public_m2_engine_input_digest(engine.train_chains, engine.dev_chains)
    limits = PublicM2EvalLimits(
        max_rows=100, max_chains=10, max_payload_bytes=100_000,
        max_chain_steps=20, max_state_tokens=16, max_actions_per_step=8,
        max_action_bytes=8, max_total_input_tokens=10_000,
    )
    return input_digest, build_public_m2_eval_selection(
        source, training_input_digest=input_digest, limits=limits,
    )


def test_stage1_export_evaluates_long_full_dev_on_cpu_without_engine_or_codec_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, engine, weights = _epoch_one_export()
    pilot_source = _source(binding="c", dev_counts=(13,))
    source = _source()
    input_digest, pilot_selection = _selection(pilot_source, engine)
    _, selection = _selection(source, engine)
    original_bytes = bytes(weights)
    import stpd.workers.public_m2_evaluation as module

    loads = 0
    original_loader = module.load_public_m2_weights

    def count_load(*args, **kwargs):
        nonlocal loads
        loads += 1
        return original_loader(*args, **kwargs)

    monkeypatch.setattr(module, "load_public_m2_weights", count_load)
    session = open_public_m2_eval_session(
        weights, config, training_input_digest=input_digest, inference_device="cpu",
    )
    old_dev = session.evaluate(pilot_source, pilot_selection)
    result = session.evaluate(source, selection)
    replayed_old_dev = session.evaluate(pilot_source, pilot_selection)
    summary = combine_public_m2_eval_shards(
        selection, (result,), expected_bindings=session.bindings,
        expected_runtime=session.runtime,
    )
    assert sum(item.label_count for item in old_dev.chains) == 13
    assert old_dev.chains == replayed_old_dev.chains
    assert loads == 1
    assert pilot_selection.source_input_identity == config.source_digest
    assert selection.source_input_identity != config.source_digest
    assert result.completed_epochs == 1 and result.run_complete is False
    assert result.inference_device == "cpu"
    assert summary.chain_count == 2 and summary.label_count == 18
    assert tuple(item.label_count for item in result.chains) == (15, 3)
    assert all(item.cross_entropy_sum >= 0 for item in result.chains)
    assert summary.loss_mean == pytest.approx(summary.cross_entropy_sum / 18)
    assert summary.top1_accuracy == pytest.approx(summary.correct_count / 18)
    assert summary.inference_device == "cpu"
    assert all(session.runtime[key] is None for key in (
        "gpu_name", "gpu_compute_capability", "gpu_total_memory_bytes",
        "cuda_version", "cudnn_version",
    ))
    assert len(selection.ordered_transition_ids) == 18
    assert selection.row_count == 18
    assert source.codec_fit_transition_ids == tuple(
        row.transition_id for row in source.chains[0].evidence
    )
    assert not set(selection.ordered_transition_ids) & set(source.codec_fit_transition_ids)
    assert weights == original_bytes
    assert result.peak_allocated_bytes == result.peak_reserved_bytes == 0
    assert set(result.phase_seconds) == {"model_load", "numeric_evaluation", "total"}


def test_frozen_epoch_exports_1_3_5_load_and_score_under_their_exact_headers() -> None:
    source = _source(binding="c", dev_counts=(13,))
    config = _config(hashlib.sha256(source.state_tokenizer).hexdigest(), source.identity)
    train = tuple(PublicM2EngineChain(chain.chain_id, chain.steps)
                  for chain in source.chains if chain.split == "train")
    dev = tuple(PublicM2EngineChain(chain.chain_id, chain.steps)
                for chain in source.chains if chain.split == "dev")
    engine = PublicM2Engine(train, dev, config)
    digest = public_m2_engine_input_digest(train, dev)
    _, selection = _selection(source, engine)
    observed = []
    for epoch in range(1, 6):
        engine.advance_window()
        if epoch in {1, 3, 5}:
            weights = engine.export_weights()
            session = open_public_m2_eval_session(
                weights, config, training_input_digest=digest, completed_epochs=epoch,
            )
            result = session.evaluate(source, selection)
            assert len(result.chains) == 1 and result.chains[0].label_count == 13
            observed.append((result.completed_epochs, result.run_complete))
    assert observed == [(1, False), (3, False), (5, True)]


def test_selection_wire_binds_ordered_membership_and_rejects_tampering() -> None:
    config, engine, _ = _epoch_one_export()
    source = _source()
    input_digest, selection = _selection(source, engine)
    raw = selection.payload_bytes()
    assert read_public_m2_eval_selection(raw, source) == selection
    decoded = decode_json(raw)
    decoded["chains"][0]["ordered_transition_ids"].reverse()
    with pytest.raises(BoundaryError):
        read_public_m2_eval_selection(json_bytes(decoded), source)
    decoded = decode_json(raw)
    decoded["identity"] = "f" * 64
    with pytest.raises(BoundaryError):
        read_public_m2_eval_selection(json_bytes(decoded), source)
    with pytest.raises(BoundaryError):
        build_public_m2_eval_selection(
            source, training_input_digest="a" * 64,
            limits=PublicM2EvalLimits(max_rows=1),
        )
    assert config.source_digest != source.identity
    assert input_digest != selection.identity
    assert selection.source_input_identity == source.identity


def test_whole_chain_partitions_recombine_once_and_reject_gap_overlap_and_catalog_tamper() -> None:
    config, engine, weights = _epoch_one_export()
    source = _source()
    input_digest, selection = _selection(source, engine)
    session = open_public_m2_eval_session(
        weights, config, training_input_digest=input_digest,
    )
    whole = session.evaluate(source, selection)
    first = session.evaluate(
        source, selection,
        shard=partition_public_m2_eval_selection(selection, 0, 1),
    )
    second = session.evaluate(
        source, selection,
        shard=partition_public_m2_eval_selection(selection, 1, 2),
    )
    all_at_once = combine_public_m2_eval_shards(
        selection, (whole,), expected_bindings=session.bindings,
        expected_runtime=session.runtime,
    )
    split = combine_public_m2_eval_shards(
        selection, (first, second), expected_bindings=session.bindings,
        expected_runtime=session.runtime,
    )
    assert (split.label_count, split.correct_count) == (
        all_at_once.label_count, all_at_once.correct_count,
    )
    assert split.cross_entropy_sum == pytest.approx(all_at_once.cross_entropy_sum, abs=1e-7)
    assert split.ordered_chain_ids == all_at_once.ordered_chain_ids
    with pytest.raises(BoundaryError, match="incomplete_eval_coverage"):
        combine_public_m2_eval_shards(
            selection, (first,), expected_bindings=session.bindings,
            expected_runtime=session.runtime,
        )
    with pytest.raises(BoundaryError, match="duplicate_or_tampered_chain_metric"):
        combine_public_m2_eval_shards(
            selection, (first, first, second), expected_bindings=session.bindings,
            expected_runtime=session.runtime,
        )
    bad_catalog = replace(first, ordered_chain_ids=("tampered",))
    with pytest.raises(BoundaryError, match="shard_chain_catalog_mismatch"):
        combine_public_m2_eval_shards(
            selection, (bad_catalog, second), expected_bindings=session.bindings,
            expected_runtime=session.runtime,
        )
    fake_hashes = replace(
        first, weights_sha256="f" * 64, weights_digest="e" * 64,
    )
    with pytest.raises(BoundaryError, match="shard_expected_model_binding_mismatch"):
        combine_public_m2_eval_shards(
            selection, (fake_hashes, second), expected_bindings=session.bindings,
            expected_runtime=session.runtime,
        )
    fake_runtime = replace(first, runtime={"torch": "same-looking"})
    with pytest.raises(BoundaryError):
        combine_public_m2_eval_shards(
            selection, (fake_runtime, second), expected_bindings=session.bindings,
            expected_runtime=session.runtime,
        )
    with pytest.raises(BoundaryError, match="invalid_shard_range"):
        partition_public_m2_eval_selection(selection, 1, 1)


def test_evaluator_rejects_header_input_epoch_and_selection_identity_drift() -> None:
    config, engine, weights = _epoch_one_export()
    source = _source()
    input_digest, selection = _selection(source, engine)
    with pytest.raises(BoundaryError, match="stage_weights_header_mismatch"):
        evaluate_public_m2_stage(
            weights, source, selection, config, training_input_digest="e" * 64,
        )
    changed_config = replace(config, source_digest="f" * 64)
    with pytest.raises(BoundaryError, match="stage_weights_header_mismatch"):
        evaluate_public_m2_stage(
            weights, source, selection, changed_config, training_input_digest=input_digest,
        )
    changed_selection = replace(selection, source_input_identity="b" * 64)
    with pytest.raises(BoundaryError):
        evaluate_public_m2_stage(
            weights, source, changed_selection, config, training_input_digest=input_digest,
        )


def test_limits_are_dev_owned_and_reset_is_only_at_chain_start() -> None:
    _, engine, _ = _epoch_one_export()
    source = _source()
    _, selection = _selection(source, engine)
    assert selection.chains[0].row_count == 15 > config_max_chain_steps(engine.config)
    assert source.chains[1].steps[0].reset_before is True
    assert all(not step.reset_before for step in source.chains[1].steps[1:])
    too_small = replace(selection, limits=replace(selection.limits, max_chain_steps=13))
    with pytest.raises(
        BoundaryError, match="selection_membership_mismatch|selection_chain_mismatch",
    ):
        too_small.validate_against(source)


def config_max_chain_steps(config: PublicM2EngineConfig) -> int:
    return config.max_chain_steps
