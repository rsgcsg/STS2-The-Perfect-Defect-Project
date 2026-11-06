"""Isolate tests that embed the normally process-owned RL Actor.

Interop thread initialization is irreversible in PyTorch. The full-mu test runs
in a fresh process and exercises the real initializer; other embedded Actor
tests leave that process-wide setting alone. Reversible inference settings are
restored so later training/export tests use their original runtime.
"""

from __future__ import annotations

import os

import pytest
import torch

from stpd.p1 import local_mu

ACTOR_PROCESS_TEST = "SPIREAGENT_TEST_ACTOR_PROCESS"


@pytest.fixture(autouse=True)
def isolated_actor_runtime(monkeypatch):
    threads = torch.get_num_threads()
    fastpath = torch.backends.mha.get_fastpath_enabled()
    if os.environ.get(ACTOR_PROCESS_TEST) != "1":
        monkeypatch.setattr(local_mu, "single_cpu_thread", lambda: torch.set_num_threads(1))
    try:
        yield
    finally:
        torch.set_num_threads(threads)
        torch.backends.mha.set_fastpath_enabled(fastpath)
