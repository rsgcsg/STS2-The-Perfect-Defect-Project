"""Real fixed M-std updates: epoch tails, dev selection and full-state recovery."""

from __future__ import annotations

from dataclasses import replace

import pytest
import torch
from test_p1_t0 import observation

from spireagent.json_boundary import BoundaryError
from stpd.p1.formal_training import MStdTrainer, Population
from stpd.p1.input import fit_vocabulary, project
from stpd.p1.spec import MStdConfig
from stpd.p1.training import maximum_difference
from stpd.workers.checkpoint_codec import decode_checkpoint


def population(count: int, prefix: str, *, target: int = 0) -> Population:
    frame = project(observation())
    return Population(
        (frame,) * count, (target,) * count, tuple(f"{prefix}-{i}" for i in range(count))
    )


def engine(*, train_count=3, max_epochs=2, test_target=0,
           stopping_policy="dev-patience-v1"):
    populations = {
        "train": population(train_count, "train"),
        "dev": population(2, "dev", target=1),
        "test": population(1, "test", target=test_target),
    }
    vocab = fit_vocabulary(list(populations["train"].frames))
    return MStdTrainer(populations, vocab, "a" * 64,
                      MStdConfig(max_epochs=max_epochs, stopping_policy=stopping_policy))


def complete(trainer):
    while trainer.phase == "training":
        trainer.advance()
    return trainer


def test_nonfinite_loss_does_not_update_parameters(monkeypatch):
    trainer = engine()
    before = {key: value.clone() for key, value in trainer.model.state_dict().items()}
    # Finite extreme logits can still produce an infinite cross-entropy loss.
    # The batched host read must reject it before optimizer.step.
    parameter = next(trainer.model.parameters())
    monkeypatch.setattr(
        trainer.model,
        "forward_batch",
        lambda frames: [
            parameter.reshape(-1)[:2] * 0 + torch.tensor([-3e38, 3e38]) for _ in frames
        ],
    )
    with pytest.raises(BoundaryError, match="non_finite_loss"):
        trainer.advance()
    assert trainer.step == 0
    assert not trainer.optimizer.state
    assert all(torch.equal(value, before[key]) for key, value in trainer.model.state_dict().items())


@pytest.mark.parametrize("cuda_available", [False, True])
def test_cuda_preflight_rejects_missing_device_or_workspace(monkeypatch, cuda_available):
    populations = {
        "train": population(1, "train"),
        "dev": population(1, "dev"),
        "test": population(0, "test"),
    }
    vocabulary = fit_vocabulary(list(populations["train"].frames))
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda_available)
    monkeypatch.delenv("CUBLAS_WORKSPACE_CONFIG", raising=False)
    expected = (
        "deterministic_cublas_workspace_required"
        if cuda_available
        else "cuda_unavailable_no_fallback"
    )
    with pytest.raises(BoundaryError, match=expected):
        MStdTrainer(populations, vocabulary, "a" * 64, MStdConfig(device="cuda:0"))


@pytest.mark.skipif(not torch.cuda.is_available(), reason="requires real CUDA hardware")
def test_real_cuda_full_state_resume(monkeypatch):
    monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    populations = {
        "train": population(3, "train"),
        "dev": population(2, "dev", target=1),
        "test": population(1, "test"),
    }
    vocabulary = fit_vocabulary(list(populations["train"].frames))
    config = MStdConfig(device="cuda:0", max_epochs=2)
    trainer = MStdTrainer(populations, vocabulary, "a" * 64, config)
    trainer.advance()
    checkpoint = trainer.checkpoint()
    complete(trainer)
    expected = decode_checkpoint(trainer.checkpoint())
    restored = MStdTrainer(populations, vocabulary, "a" * 64, config)
    restored.restore(checkpoint)
    complete(restored)
    assert maximum_difference(expected, decode_checkpoint(restored.checkpoint())) == 0.0
    assert restored.finish() == trainer.finish()


def test_real_epoch_tail_and_boundary_resume_are_exact():
    uninterrupted = engine(train_count=65)
    with pytest.raises(BoundaryError, match="test_before_model_selection"):
        uninterrupted.finish()
    uninterrupted.advance()
    result = uninterrupted.advance()
    assert result["examples"] == 1 and uninterrupted.epoch == 1
    midpoint = uninterrupted.checkpoint()
    complete(uninterrupted)
    final = decode_checkpoint(uninterrupted.checkpoint())
    assert [row["examples"] for row in final["history"]] == [64, 1, 64, 1]
    assert sum(row["examples"] for row in final["history"]) == 130
    restored = engine(train_count=65)
    restored.restore(midpoint)
    complete(restored)
    assert maximum_difference(final, decode_checkpoint(restored.checkpoint())) == 0.0
    assert restored.finish() == uninterrupted.finish()
    assert (
        maximum_difference(
            decode_checkpoint(restored.checkpoint()), decode_checkpoint(uninterrupted.checkpoint())
        )
        == 0.0
    )


def test_real_dev_patience_best_last_and_patience_resume():
    trainer = engine(max_epochs=12)
    for _ in range(4):
        trainer.advance()
    checkpoint = trainer.checkpoint()
    complete(trainer)
    assert trainer.stop_reason == "dev_patience_exhausted"
    best = min(trainer.dev_history, key=lambda row: row["mean_N_loss"])
    assert trainer.best_epoch == best["epoch"]
    assert trainer.best_step == best["step"]
    assert trainer.epoch == trainer.best_epoch + 5
    assert trainer.best_step < trainer.step
    assert any(
        not torch.equal(value.cpu(), trainer.best_weights[key])
        for key, value in trainer.model.state_dict().items()
    )
    restored = engine(max_epochs=12)
    restored.restore(checkpoint)
    complete(restored)
    assert (
        maximum_difference(
            decode_checkpoint(trainer.checkpoint()), decode_checkpoint(restored.checkpoint())
        )
        == 0.0
    )
    before = trainer.best_epoch, trainer.best_loss, trainer.bad_epochs
    result = trainer.finish()
    assert result["N"] == 1
    assert before == (trainer.best_epoch, trainer.best_loss, trainer.bad_epochs)


def test_test_labels_do_not_choose_the_model_or_change_updates():
    first = complete(engine(test_target=0))
    first_last = {k: v.clone() for k, v in first.model.state_dict().items()}
    second = complete(engine(test_target=1))
    assert first.dev_history == second.dev_history
    assert (first.best_epoch, first.best_step, first.best_loss) == (
        second.best_epoch,
        second.best_step,
        second.best_loss,
    )
    assert all(torch.equal(v, second.model.state_dict()[k]) for k, v in first_last.items())
    assert first.finish()["mean_N_loss"] != second.finish()["mean_N_loss"]


def test_epoch_budget_keeps_training_after_dev_patience_and_resumes_exactly():
    trainer = engine(max_epochs=9, stopping_policy="epoch-budget-v1")
    for _ in range(4):
        trainer.advance()
    checkpoint = trainer.checkpoint()
    complete(trainer)
    assert trainer.epoch == 9 and trainer.bad_epochs >= 5
    assert trainer.stop_reason == "epoch_budget_exhausted"
    restored = engine(max_epochs=9, stopping_policy="epoch-budget-v1")
    restored.restore(checkpoint)
    complete(restored)
    assert maximum_difference(decode_checkpoint(trainer.checkpoint()),
                              decode_checkpoint(restored.checkpoint())) == 0.0
    assert trainer.best_step == min(trainer.dev_history, key=lambda r: r["mean_N_loss"])["step"]


def test_long_run_config_is_explicit_and_legacy_config_bytes_stay_unchanged():
    legacy = MStdConfig().to_dict()
    assert "stopping_policy" not in legacy
    assert MStdConfig.decode(legacy).to_dict() == legacy
    long_run = MStdConfig(max_epochs=40, stopping_policy="epoch-budget-v1")
    assert MStdConfig.decode(long_run.to_dict()) == long_run
    for invalid in [None, [], 1, "none"]:
        with pytest.raises(BoundaryError, match="unsupported_stopping_policy"):
            replace(long_run, stopping_policy=invalid)
    with pytest.raises(BoundaryError, match="invalid_epoch_budget"):
        replace(long_run, max_epochs=41)


def test_invalid_partitions_target_and_unavailable_device_fail_explicitly():
    trainer = engine()
    from stpd.p1.execution import MStdExecution

    with pytest.raises(BoundaryError, match="formal_recipe_cpu_threads_mismatch"):
        MStdTrainer(trainer.populations, trainer.vocabulary, "a" * 64, trainer.config,
                    MStdExecution(cpu_threads=4))
    bad = {**trainer.populations, "dev": trainer.populations["train"]}
    with pytest.raises(BoundaryError, match="point_leakage"):
        MStdTrainer(bad, trainer.vocabulary, "a" * 64, trainer.config)
    bad["dev"] = Population((), (), ())
    with pytest.raises(BoundaryError, match="nonempty_train_and_dev"):
        MStdTrainer(bad, trainer.vocabulary, "a" * 64, trainer.config)
    with pytest.raises(BoundaryError, match="target_outside"):
        Population(trainer.populations["dev"].frames, (8, 0), ("one", "two"))
    with pytest.raises(BoundaryError, match="invalid_epoch_budget"):
        replace(trainer.config, max_epochs=True)
    with pytest.raises(BoundaryError, match="explicit_device"):
        replace(trainer.config, device="auto")
    if not torch.cuda.is_available():
        with pytest.raises(BoundaryError, match="cuda_unavailable_no_fallback"):
            MStdTrainer(
                trainer.populations,
                trainer.vocabulary,
                "a" * 64,
                replace(trainer.config, device="cuda:0"),
            )
