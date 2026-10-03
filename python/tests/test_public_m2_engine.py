"""Synthetic engineering checks for public M2 window continuation."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
import torch

import stpd.workers.public_m2_engine as engine_module
from spireagent.json_boundary import BoundaryError
from stpd.models.light_action_m2_training_data import LightActionM2TrainingStep
from stpd.models.token_core import ScratchShape
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.public_m2_engine import (
    PublicM2Engine,
    PublicM2EngineChain,
    PublicM2EngineConfig,
    load_public_m2_weights,
)
from stpd.workers.public_m2_preflight import (
    preflight_public_m2_engine,
    validate_public_m2_checkpoint,
    validate_public_m2_export,
)


def _step(position: int, *, target: str = "b") -> LightActionM2TrainingStep:
    return LightActionM2TrainingStep(
        position=position, page=(1, 2, 3), action_ids=("a", "b"),
        byte_actions=((256, 65, 257), (256, 66, 257)),
        target_action_id=target, previous_actual_action=None,
        reset_before=position == 0,
    )


def _config(*, epochs: int = 1, source_digest: str = "a" * 64,
            slots: int = 8, reset_each_step: bool = False) -> PublicM2EngineConfig:
    return PublicM2EngineConfig(
        source_digest=source_digest, state_tokenizer_sha256="b" * 64,
        shape=ScratchShape(vocab_size=258, width=12, layers=1, heads=2,
                           feedforward=24, dropout=0.1, max_tokens=16),
        max_action_bytes=8, max_actions_per_step=2, max_chain_steps=16,
        max_total_steps=32, max_total_input_tokens=512, epochs=epochs,
        slots=slots, reset_each_step=reset_each_step,
    )


def _engine(*, epochs: int = 1, train_steps: int = 9,
            source_digest: str = "a" * 64, slots: int = 8,
            reset_each_step: bool = False) -> PublicM2Engine:
    return PublicM2Engine(
        (PublicM2EngineChain("train", tuple(_step(i) for i in range(train_steps))),),
        (PublicM2EngineChain("dev", (_step(0), _step(1, target="a"))),),
        _config(epochs=epochs, source_digest=source_digest, slots=slots,
                reset_each_step=reset_each_step),
    )


@pytest.mark.parametrize(("slots", "reset_each_step"), (
    (8, False), (1, False), (8, True), (1, True),
))
def test_mid_chain_resume_matches_uninterrupted_and_dev_is_read_only(
    slots: int, reset_each_step: bool,
) -> None:
    torch.set_num_threads(1)
    original = _engine(slots=slots, reset_each_step=reset_each_step)
    first = original.advance_window()
    assert (first.label_count, first.optimizer_updates, first.window_cursor) == (8, 1, 8)
    raw = original.checkpoint()
    metric = original.evaluate_dev()
    assert metric.label_count == 2 and 0 <= metric.top1_accuracy <= 1
    assert original.checkpoint() == raw
    resumed = _engine(slots=slots, reset_each_step=reset_each_step)
    resumed.restore(raw)
    assert resumed.window_cursor == 8
    original.advance_window()
    resumed.advance_window()
    assert original.finished and resumed.finished
    assert original.label_count == resumed.label_count == 9
    assert original.optimizer_updates == resumed.optimizer_updates == 2
    assert original.checkpoint() == resumed.checkpoint()
    exported = decode_checkpoint(original.export_weights())
    assert exported["completed_epochs"] == 1
    assert exported["run_complete"] is True
    assert exported["checkpoint_digest"] == hashlib.sha256(original.checkpoint()).hexdigest()
    loaded = load_public_m2_weights(
        original.export_weights(), original.config,
        input_digest=original.input_digest, completed_epochs=1,
    )
    assert not loaded.training


def test_public_m2_learning_rate_default_matches_six_arm_plan() -> None:
    assert _config().learning_rate == 3e-4


def test_four_decision_windows_resume_byte_exact_and_extended_limit() -> None:
    torch.set_num_threads(1)
    config = replace(_config(), window_steps=4, max_window_tokens=98_304)
    config.validate()
    with pytest.raises(BoundaryError, match="invalid_config"):
        replace(config, max_window_tokens=98_305).validate()
    train = (PublicM2EngineChain("train", tuple(_step(i) for i in range(9))),)
    dev = (PublicM2EngineChain("dev", (_step(0),)),)
    original = PublicM2Engine(train, dev, config)
    first = original.advance_window()
    assert (first.label_count, first.window_cursor) == (4, 4)
    checkpoint = original.checkpoint()
    resumed = PublicM2Engine(train, dev, config)
    resumed.restore(checkpoint)
    while not original.finished:
        original.advance_window()
        resumed.advance_window()
    assert original.optimizer_updates == resumed.optimizer_updates == 3
    assert original.checkpoint() == resumed.checkpoint()


def test_cpu_preflight_validates_checkpoint_and_export_structure() -> None:
    torch.set_num_threads(1)
    engine = _engine()
    engine.advance_window()
    profile = preflight_public_m2_engine(
        engine.train_chains, engine.dev_chains, engine.config,
    )
    raw = engine.checkpoint()
    assert validate_public_m2_checkpoint(
        raw, profile, expected_runtime=engine.runtime,
    )["window_cursor"] == 8
    for key, changed in (
        ("input_digest", "f" * 64),
        ("runtime", {**engine.runtime, "implementation_sha256": "f" * 64}),
        ("memory", torch.zeros(1, 12)),
        ("cpu_rng", torch.zeros(1, dtype=torch.uint8)),
    ):
        value = decode_checkpoint(raw)
        value[key] = changed
        with pytest.raises(BoundaryError):
            validate_public_m2_checkpoint(
                encode_checkpoint(value), profile, expected_runtime=engine.runtime,
            )
    value = decode_checkpoint(raw)
    first = next(iter(value["optimizer"]["state"].values()))
    first["step"] = first["step"].to(torch.int64)
    from stpd.workers.public_m2_engine import _optimizer_digest
    value["optimizer_digest"] = _optimizer_digest(
        value["optimizer"], profile.parameter_names,
    )
    with pytest.raises(BoundaryError, match="optimizer_state_mismatch"):
        validate_public_m2_checkpoint(
            encode_checkpoint(value), profile, expected_runtime=engine.runtime,
        )
    value = decode_checkpoint(raw)
    first_name = next(iter(value["model"]))
    value["model"][first_name] = value["model"][first_name].flatten()[:1]
    value["model_digest"] = engine_module._tensor_digest(value["model"])
    with pytest.raises(BoundaryError, match="model_mismatch"):
        validate_public_m2_checkpoint(
            encode_checkpoint(value), profile, expected_runtime=engine.runtime,
        )
    value = decode_checkpoint(raw)
    first_name = next(iter(value["model"]))
    value["model"][first_name].flatten()[0] = float("nan")
    value["model_digest"] = engine_module._tensor_digest(value["model"])
    with pytest.raises(BoundaryError, match="non_finite_tensor"):
        validate_public_m2_checkpoint(
            encode_checkpoint(value), profile, expected_runtime=engine.runtime,
        )
    engine.advance_window()
    checkpoint_raw = engine.checkpoint()
    export = engine.export_weights()
    assert validate_public_m2_export(
        export, profile, completed_epochs=1, checkpoint_raw=checkpoint_raw,
        expected_runtime=engine.runtime,
    )["optimizer_updates"] == 2
    with pytest.raises(BoundaryError, match="checkpoint_mismatch"):
        validate_public_m2_export(
            export, profile, completed_epochs=1,
            checkpoint_raw=raw, expected_runtime=engine.runtime,
        )


def test_gpu_tagged_checkpoint_cpu_validation_is_structural_only() -> None:
    """This fabricated header is not evidence that a CUDA RNG can be restored."""
    torch.set_num_threads(1)
    engine = _engine()
    engine.advance_window()
    config = replace(engine.config, device="cuda:0")
    profile = preflight_public_m2_engine(engine.train_chains, engine.dev_chains, config)
    value = decode_checkpoint(engine.checkpoint())
    value["config"] = engine_module.asdict(config)
    value["cuda_rng"] = torch.tensor([1, 2, 3], dtype=torch.uint8)
    value["rng_digest"] = engine_module._tensor_digest({
        "cpu_rng": value["cpu_rng"], "cuda_rng": value["cuda_rng"],
    })
    assert validate_public_m2_checkpoint(
        encode_checkpoint(value), profile, expected_runtime=engine.runtime,
    )["config"]["device"] == "cuda:0"
    value["cuda_rng"] = torch.tensor([1, 2, 3], dtype=torch.int64)
    value["rng_digest"] = engine_module._tensor_digest({
        "cpu_rng": value["cpu_rng"], "cuda_rng": value["cuda_rng"],
    })
    with pytest.raises(BoundaryError, match="rng_mismatch"):
        validate_public_m2_checkpoint(
            encode_checkpoint(value), profile, expected_runtime=engine.runtime,
        )


def test_five_epoch_exports_are_bound_to_same_incomplete_run() -> None:
    torch.set_num_threads(1)
    engine = _engine(epochs=5, train_steps=2)
    exports = []
    for epoch in range(1, 6):
        engine.advance_window()
        if epoch in {1, 3, 5}:
            exports.append(decode_checkpoint(engine.export_weights()))
    assert [item["completed_epochs"] for item in exports] == [1, 3, 5]
    assert [item["optimizer_updates"] for item in exports] == [1, 3, 5]
    assert [item["run_complete"] for item in exports] == [False, False, True]
    assert len({item["input_digest"] for item in exports}) == 1


def test_preflight_rejects_partial_input_and_restore_poison() -> None:
    torch.set_num_threads(1)
    train = PublicM2EngineChain("same", (_step(0),))
    with pytest.raises(BoundaryError):
        PublicM2Engine((train,), (train,), _config())
    with pytest.raises(BoundaryError):
        PublicM2Engine(
            (PublicM2EngineChain("train", (_step(0), _step(1, target="missing"))),),
            (PublicM2EngineChain("dev", (_step(0),)),), _config(),
        )
    engine = _engine()
    engine.advance_window()
    bad = decode_checkpoint(engine.checkpoint())
    bad["optimizer_updates"] = 77
    victim = _engine()
    with pytest.raises(BoundaryError):
        victim.restore(encode_checkpoint(bad))
    with pytest.raises(BoundaryError):
        victim.advance_window()
    mismatch = _engine(source_digest="c" * 64)
    with pytest.raises(BoundaryError):
        mismatch.restore(engine.checkpoint())


def test_restore_rejects_typed_but_invalid_adam_step_and_poison() -> None:
    torch.set_num_threads(1)
    producer = _engine()
    producer.advance_window()
    value = decode_checkpoint(producer.checkpoint())
    first = next(iter(value["optimizer"]["state"]))
    value["optimizer"]["state"][first]["step"] = torch.tensor(1, dtype=torch.int64)
    value["optimizer_digest"] = engine_module._optimizer_digest(
        value["optimizer"], producer.parameter_names,
    )
    victim = _engine()
    with pytest.raises(BoundaryError):
        victim.restore(encode_checkpoint(value))
    with pytest.raises(BoundaryError):
        victim.advance_window()


def test_gpu_tagged_training_export_loads_for_cpu_inference() -> None:
    """Contract-only fixture: a CUDA header here is not a CUDA training claim."""
    torch.set_num_threads(1)
    engine = _engine()
    while not engine.finished:
        engine.advance_window()
    value = decode_checkpoint(engine.export_weights())
    value["config"]["device"] = "cuda:0"
    config = replace(engine.config, device="cuda:0")
    loaded = load_public_m2_weights(
        encode_checkpoint(value), config, input_digest=engine.input_digest,
        completed_epochs=1,
    )
    assert loaded.write_queries.device.type == "cpu"
    with pytest.raises(BoundaryError):
        load_public_m2_weights(
            encode_checkpoint(value), config, input_digest=engine.input_digest,
            completed_epochs=1, inference_device="unavailable",
        )


def test_long_chain_has_no_legacy_64_step_reset_or_truncation() -> None:
    torch.set_num_threads(1)
    config = _config()
    config = PublicM2EngineConfig(**{
        **config.__dict__, "max_chain_steps": 72, "max_total_steps": 80,
        "max_total_input_tokens": 1024,
    })
    engine = PublicM2Engine(
        (PublicM2EngineChain("train", tuple(_step(i) for i in range(65))),),
        (PublicM2EngineChain("dev", (_step(0),)),), config,
    )
    assert len(engine.train_chains[0].steps) == 65
    while not engine.finished:
        engine.advance_window()
    assert engine.label_count == 65
    assert engine.optimizer_updates == 9


def test_hot_window_path_does_not_reserialize_whole_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    torch.set_num_threads(1)
    config = _config()
    config = PublicM2EngineConfig(**{**config.__dict__, "window_steps": 2})
    engine = PublicM2Engine(
        (PublicM2EngineChain("train", tuple(_step(i) for i in range(4))),),
        (PublicM2EngineChain("dev", (_step(0),)),), config,
    )

    def no_full_scan(*_args: object) -> None:
        raise AssertionError("whole-input/file hashing on hot window path")

    monkeypatch.setattr(engine_module, "_chain_values", no_full_scan)
    monkeypatch.setattr(engine_module, "_implementation_digest", no_full_scan)
    engine.advance_window()
    engine.advance_window()
    assert engine.finished and engine.optimizer_updates == 2
