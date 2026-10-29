import pytest
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

from spireagent.json_boundary import BoundaryError
from spireagent.rl_exchange import Exchange
from spireagent.rl_tensorboard import TensorBoardMetrics, publish_evaluation


def test_closed_event_files_roundtrip_and_append_without_changing_old_bytes(tmp_path):
    exchange = Exchange({"kind": "local", "directory": str(tmp_path / "peer")}, "curves")
    metrics = TensorBoardMetrics(exchange, tmp_path / "local")
    metrics.scalars({"train/policy_loss": 1.25, "train/anchor_loss": None,
                     "train/mean_staleness": 2}, 10)
    first = metrics.publish()
    original = {key: exchange.blobs.get(key) for key in first}
    metrics.scalars({"train/policy_loss": 0.75}, 20)
    metrics.publish()
    publish_evaluation(exchange, tmp_path / "eval", {
        "version": 2, "updates": 20, "wins": 1, "mean_max_floor": 14.5, "unknown": 2})
    for key, raw in original.items():
        assert exchange.blobs.get(key) == raw
    downloaded = tmp_path / "downloaded"
    downloaded.mkdir()
    for key in exchange.blobs.keys(f"{exchange.prefix}tb/"):
        (downloaded / key.rsplit("/", 1)[1]).write_bytes(exchange.blobs.get(key))
    events = EventAccumulator(str(downloaded)).Reload()
    assert sorted((row.step, row.value) for row in events.Scalars("train/policy_loss")) == [
        (10, 1.25), (20, 0.75)]
    assert events.Scalars("evaluation/unknown")[0].value == 2
    assert events.Scalars("evaluation/mean_max_floor")[0].value == 14.5
    assert events.Scalars("evaluation/wins")[0].step == 20
    with pytest.raises(BoundaryError, match="invalid_evaluation_metrics"):
        publish_evaluation(exchange, tmp_path / "bad", {
            "version": 2, "updates": 20, "wins": 1, "mean_max_floor": float("nan"), "unknown": 2})


def test_remote_stop_and_learner_events_are_bound_sealed_and_append_only(tmp_path):
    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    supervisor, learner = Exchange(peer, "stop"), Exchange(peer, "stop")
    assert learner.stop_request() is None
    request = supervisor.request_stop("wall_limit")
    assert learner.stop_request() == request
    assert supervisor.request_stop("unknown_rate") == request
    learner.publish_event("progress", version=2, updates=16, counts={"episodes_loss": 20})
    latest = learner.publish_event("finished", version=3, updates=17)
    assert supervisor.latest_event() == latest
    assert [event["kind"] for event in supervisor.event_records()] == ["progress", "finished"]
    path = tmp_path / "peer" / "exchange/rl/stop/control/stop.json"
    path.chmod(0o600)
    path.write_bytes(path.read_bytes().replace(b"wall_limit", b"wall_other"))
    with pytest.raises(BoundaryError, match="record_integrity_failure"):
        learner.stop_request()
