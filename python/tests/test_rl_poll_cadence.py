"""Exercise the real learner loop: cadence skips network polling, local STOP stays immediate."""
from dataclasses import replace

import pytest

from spireagent.json_boundary import BoundaryError
from stpd.rl.learner import Learner, LearnerConfig


@pytest.mark.parametrize("interval,expected", [(0, 4), (1000, 1)])
def test_exchange_cadence_keeps_local_stop_and_final_checkpoint(tmp_path, monkeypatch,
                                                               interval, expected):
    from unittest.mock import Mock

    from spireagent.artifact_contracts import Producer
    from spireagent.workbench.rl_learner import IO
    from stpd.p1.export import export_model
    from stpd.p1.model import MStd
    from stpd.p1.vocabulary import public_vocabulary
    from stpd.rl import learner as module

    monkeypatch.setattr(module, "source_identity", lambda _: Producer("test", "a"*40, "b"*64))
    checkpoint = tmp_path / "checkpoint"
    checkpoint.write_bytes(b"diagnostic")
    package = tmp_path / "package"
    export_model(MStd(public_vocabulary()), checkpoint, package, {"scope": "test"})
    learner = Learner(LearnerConfig(run="cadence", peer={"kind": "local",
        "directory": str(tmp_path / "peer")}, start_package=str(package),
        tensorboard_steps=0, exchange_poll_seconds=interval), tmp_path / "work", IO)
    stop = Mock(return_value=None)
    monkeypatch.setattr(learner.exchange, "stop_request", stop)
    poll = Mock()
    monkeypatch.setattr(learner, "poll", poll)
    calls = []

    def update():
        calls.append(None)
        learner.updates += 1
        if len(calls) == 4:
            (learner.work / "STOP").touch()
        return {"diagnostic": True}

    monkeypatch.setattr(learner, "train_once", update)
    summary = learner.run()
    assert summary["updates"] == len(calls) == 4
    assert poll.call_count == stop.call_count == expected
    assert summary["timing"]["phases"]["stop_poll"] >= 0
    assert learner.exchange.latest_checkpoint() is not None


@pytest.mark.parametrize("interval", [-1, float("inf"), float("nan"), True])
def test_invalid_cadence_rejected_before_io(tmp_path, interval):
    config = replace(LearnerConfig(run="invalid", peer={}), exchange_poll_seconds=interval)
    with pytest.raises(BoundaryError, match="invalid_exchange_poll_seconds"):
        Learner(config, tmp_path, None)
