"""WP8 mu handoff: learner publication -> FastInferenceService hot swap."""
from __future__ import annotations

import copy
import os
import tempfile
from typing import Any

import pytest
import torch
from test_p1_fast_inference import (  # noqa: F401 - fixtures
    AGENT_SPEC,
    MU,
    SAMPLING,
    FakeAssembler,
    FakeEnv,
    _aggregator,
    _decision_line,
    _observations,
    _service,
    model,
    vocabulary,
)

from stpd.p1.fast_mu_follower import FastMuFollower
from stpd.rl.fast_assembly import MuHandoff

NEW = "c" * 64


def identity(snapshot: str, generation: str = "gen-1", version: int = 2,
             updates: int = 8) -> dict[str, Any]:
    return {"snapshot_sha256": snapshot, "training_generation": generation,
            "version": version, "updates": updates, "model_sha256": "m" * 64}


class Source:
    def __init__(self, latest: dict[str, Any], loaded: Any = "model") -> None:
        self.current, self.model = latest, loaded
        self.loads: list[str] = []
        self.fail_latest = 0
        self.fail_load = False

    def latest(self) -> dict[str, Any]:
        if self.fail_latest:
            self.fail_latest -= 1
            raise OSError("learner_unreachable")
        return dict(self.current)

    def load(self, value: dict[str, Any]) -> Any:
        if self.fail_load:
            raise ValueError("behavior_snapshot_checksum_changed")
        self.loads.append(value["snapshot_sha256"])
        return self.model


class Service:
    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.published: list[tuple[Any, MuHandoff]] = []

    def publish(self, scorer: Any, handoff: MuHandoff) -> bool:
        self.published.append((scorer, handoff))
        return self.accept


def test_each_identity_is_offered_once_and_a_new_generation_again():
    source, service = Source(identity(NEW)), Service()
    follower = FastMuFollower(source, service)
    assert follower.step() is True
    assert follower.step() is False and follower.counters["unchanged"] == 1
    assert service.published[0][1] == MuHandoff(NEW, "gen-1", 2, 8)
    assert service.published[0][1].weights is None
    # Same weights, CP restore's new generation: offered again (assembler rebinds).
    source.current = identity(NEW, generation="gen-2", version=0, updates=8)
    assert follower.step() is True
    assert [h.training_generation for _, h in service.published] == ["gen-1", "gen-2"]
    assert source.loads == [NEW, NEW]
    assert follower.status()["published"] == 2


def test_a_refused_offer_is_counted_and_not_forced_again():
    source, service = Source(identity(NEW)), Service(accept=False)
    follower = FastMuFollower(source, service)
    assert follower.step() is False and follower.step() is False
    assert len(service.published) == 1
    assert follower.counters["refused"] == 1 and follower.counters["unchanged"] == 1


def test_transient_read_errors_recover_and_repeated_failures_stop_loudly():
    source, service = Source(identity(NEW)), Service()
    source.fail_latest = 1
    follower = FastMuFollower(source, service, interval=0.01, max_failures=3).start()
    for _ in range(500):
        if service.published:
            break
        follower.stopping.wait(0.01)
    follower.check()  # one failure, then success: still running
    assert service.published and follower.counters["failures"] == 1
    assert follower.failures == 0 and not follower.stopped()
    follower.stop()

    broken = Source(identity(NEW))
    broken.fail_load = True
    follower = FastMuFollower(broken, Service(), interval=0.01, max_failures=2).start()
    follower.thread.join(10)
    assert follower.stopped() and follower.counters["failures"] == 2
    with pytest.raises(ValueError, match="behavior_snapshot_checksum_changed"):
        follower.check()
    assert follower.offered is None  # nothing was offered in its place


def test_invalid_settings_are_refused():
    for options in ({"interval": 0}, {"max_failures": 0}):
        with pytest.raises(ValueError, match="invalid_fast_mu_follower_settings"):
            FastMuFollower(Source(identity(NEW)), Service(), **options)


def test_rows_after_the_swap_carry_the_new_mu(vocabulary, model):  # noqa: F811
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler)
    newer = copy.deepcopy(model)
    with torch.no_grad():
        for parameter in newer.rl4_heads.parameters():
            parameter.add_(0.01)
    path = os.path.join(tempfile.mkdtemp(), "link.sock")
    aggregator = _aggregator(vocabulary, service, path)
    try:
        env = FakeEnv(path)
        env.send({"type": "hello", "env_process_id": "env-1", "slots": 1, "p1_revision": "x",
                  "input_spec": AGENT_SPEC})
        env.send({"type": "episode_open", "game_instance_id": "g1", "actor_id": "actor-1",
                  "sampling": SAMPLING, "single_choice": "score", "start": {"kind": "new_run"}})
        observations = _observations(2)
        env.socket.sendall(_decision_line(1, "g1", 0, observations[0]))
        assert env.reply()["mu_snapshot_sha256"] == MU
        follower = FastMuFollower(Source(identity(NEW), newer), service)
        assert follower.step() is True
        env.send({"type": "step_outcome", "row_id": 1, "game_instance_id": "g1",
                  "step_index": 0, "status": "completed", "receipt_sha256": "b" * 64,
                  "public_facts": {"act": 1, "floor": 3, "hp": 50, "max_hp": 70},
                  "terminal": None})
        env.socket.sendall(_decision_line(2, "g1", 1, observations[1]))
        assert env.reply()["mu_snapshot_sha256"] == NEW
        assert assembler.mu == MuHandoff(NEW, "gen-1", 2, 8)
    finally:
        aggregator.close()
        service.close()
