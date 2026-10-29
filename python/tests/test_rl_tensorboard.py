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
        "version": 2, "updates": 20, "wins": 1, "mean_max_floor": 14.5, "unknown": 2,
        "planned": 20})
    publish_evaluation(exchange, tmp_path / "confirmation", {
        "evaluation_kind": "confirmation", "version": 2, "updates": 20,
        "wins": 2, "mean_max_floor": 15.0, "unknown": 1, "planned": 40})
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
    assert len(events.Scalars("evaluation/wins")) == 1
    assert events.Scalars("evaluation/planned")[0].value == 20
    assert events.Scalars("confirmation/wins")[0].value == 2
    assert events.Scalars("confirmation/planned")[0].value == 40
    with pytest.raises(BoundaryError, match="invalid_evaluation_metrics"):
        publish_evaluation(exchange, tmp_path / "bad", {
            "version": 2, "updates": 20, "wins": 1, "mean_max_floor": float("nan"), "unknown": 2})


def test_remote_stop_and_learner_events_are_bound_sealed_and_append_only(tmp_path):
    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    supervisor, learner = Exchange(peer, "stop"), Exchange(peer, "stop")
    assert learner.stop_request() is None
    with pytest.raises(BoundaryError, match="invalid_stop_reason"):
        supervisor.request_stop("")
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


def test_histograms_and_not_applicable_text_share_closed_event_publication(tmp_path):
    exchange = Exchange({'kind': 'local', 'directory': str(tmp_path / 'peer')}, 'histograms')
    metrics = TensorBoardMetrics(exchange, tmp_path / 'metrics')
    metrics.histograms({'train/advantage': [-0.2, 0, 0.5], 'train/empty': []}, 10)
    metrics.text('algorithm/reward', 'whole-episode return', 10)
    metrics.scalars({'train/policy_entropy': 0.5}, 10)
    metrics.publish()
    events = EventAccumulator(str(metrics.directory)).Reload()
    assert events.Histograms('train/advantage')[0].step == 10
    assert 'train/empty' not in events.Tags()['histograms']
    assert events.Tensors('algorithm/value_loss/text_summary')[0].tensor_proto.string_val == [
        b'not applicable: no critic']
    assert 'algorithm/value_loss' not in events.Tags()['scalars']
    with pytest.raises(BoundaryError, match='nonfinite_histogram'):
        metrics.histograms({'bad': [float('nan')]}, 11)
    publish_evaluation(exchange, tmp_path / 'quick', {
        'evaluation_kind': 'quick', 'version': 2, 'updates': 20, 'wins': 0,
        'mean_max_floor': 14.5, 'unknown': 0, 'planned': 20})
    publish_evaluation(exchange, tmp_path / 'milestone', {
        'evaluation_kind': 'milestone', 'version': 2, 'updates': 20, 'wins': 0,
        'mean_max_floor': 14.5, 'unknown': 0, 'planned': 100})
