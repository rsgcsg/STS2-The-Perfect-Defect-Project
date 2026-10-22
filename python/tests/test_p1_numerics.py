"""The two numerical libraries must not configure their next process consumer."""

from __future__ import annotations

import random

import pytest
import torch
from test_p1_formal_training import engine
from test_p1_t0 import observation

from stpd.p1.input import fit_vocabulary, project
from stpd.p1.training import Trainer, maximum_difference
from stpd.workers.checkpoint_codec import decode_checkpoint


def settings():
    return (
        torch.get_num_threads(),
        torch.are_deterministic_algorithms_enabled(),
        torch.is_deterministic_algorithms_warn_only_enabled(),
        torch.backends.cuda.matmul.allow_tf32,
        torch.backends.cudnn.allow_tf32,
        torch.backends.cudnn.benchmark,
        random.getstate(),
        torch.get_rng_state().clone(),
    )


def unchanged(before):
    after = settings()
    assert before[:-1] == after[:-1]
    assert torch.equal(before[-1], after[-1])


@pytest.mark.parametrize("formal", [False, True])
def test_construct_update_checkpoint_restore_leave_caller_unchanged(formal):
    before = settings()
    frame = project(observation())
    vocabulary = fit_vocabulary([frame])
    trainer = engine() if formal else Trainer([frame], [1], vocabulary, "source", 2)
    unchanged(before)
    trainer.advance()
    unchanged(before)
    midpoint = trainer.checkpoint()
    unchanged(before)
    trainer.advance()
    expected = decode_checkpoint(trainer.checkpoint())
    # Another consumer can use the ambient RNG between trainer operations.
    torch.rand(3)
    random.random()
    other = settings()
    trainer.restore(midpoint)
    unchanged(other)
    trainer.advance()
    unchanged(other)
    assert maximum_difference(expected, decode_checkpoint(trainer.checkpoint())) == 0


def test_failed_update_restores_process_settings(monkeypatch):
    trainer = engine()
    before = settings()

    def fail(frame):
        raise RuntimeError("fixture forward failure")

    monkeypatch.setattr(trainer.model, "forward_batch", fail)
    with pytest.raises(RuntimeError, match="fixture forward failure"):
        trainer.advance()
    unchanged(before)
