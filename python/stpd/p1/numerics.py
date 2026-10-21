"""Scoped Torch process settings and RNG owned by one sequential trainer.

The worker owns process isolation. These scopes also prevent a library caller's
next consumer from inheriting training settings or dropout RNG state.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from contextlib import contextmanager
from functools import wraps
from typing import TypedDict

import torch


class TrainingRNG(TypedDict):
    torch_cpu: torch.Tensor
    python: tuple[object, ...]
    torch_cuda: torch.Tensor | None


class TrainingNumerics:
    def __init__(self, seed: int, threads: int, device: torch.device) -> None:
        self.threads, self.device = threads, device
        self.rng: TrainingRNG = {
            "torch_cpu": torch.Generator().manual_seed(seed).get_state(),
            "python": random.Random(seed).getstate(),
            "torch_cuda": torch.Generator(device=device).manual_seed(seed).get_state()
            if device.type == "cuda"
            else None,
        }

    @contextmanager
    def scope(self) -> Iterator[None]:
        threads = torch.get_num_threads()
        deterministic = torch.are_deterministic_algorithms_enabled()
        warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
        settings = (
            torch.backends.cuda.matmul.allow_tf32,
            torch.backends.cudnn.allow_tf32,
            torch.backends.cudnn.benchmark,
        )
        outer_python = random.getstate()
        devices = [self.device.index or 0] if self.device.type == "cuda" else []
        with torch.random.fork_rng(devices=devices):
            try:
                torch.set_num_threads(self.threads)
                torch.use_deterministic_algorithms(True)
                if devices:
                    torch.backends.cuda.matmul.allow_tf32 = False
                    torch.backends.cudnn.allow_tf32 = False
                    torch.backends.cudnn.benchmark = False
                    cuda_rng = self.rng["torch_cuda"]
                    assert cuda_rng is not None  # CUDA trainers own a CUDA RNG state.
                    torch.cuda.set_rng_state(cuda_rng, self.device)
                torch.set_rng_state(self.rng["torch_cpu"])
                random.setstate(self.rng["python"])
                yield
            finally:
                self.rng = {
                    "torch_cpu": torch.get_rng_state(),
                    "python": random.getstate(),
                    "torch_cuda": torch.cuda.get_rng_state(self.device) if devices else None,
                }
                random.setstate(outer_python)
                torch.set_num_threads(threads)
                torch.use_deterministic_algorithms(deterministic, warn_only=warn_only)
                (
                    torch.backends.cuda.matmul.allow_tf32,
                    torch.backends.cudnn.allow_tf32,
                    torch.backends.cudnn.benchmark,
                ) = settings


def training_scope(method):
    @wraps(method)
    def scoped(self, *args, **kwargs):
        with self._numerics.scope():
            return method(self, *args, **kwargs)

    return scoped
