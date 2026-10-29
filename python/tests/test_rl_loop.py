"""RL1 actor/learner pieces on synthetic data; a Managed smoke is the game-bound evidence."""
from __future__ import annotations

import collections
import hashlib
import json
import math

import pytest
import torch
from test_p1_t0 import observation

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError
from spireagent.rl_exchange import Exchange
from spireagent.workbench.managed_evaluation import behavior_policy
from stpd.p1.input import fit_vocabulary, project
from stpd.p1.model import MStd
from stpd.p1.sampling import distribution, draw, validate_policy
from stpd.rl.loss import LossConfig, RlSample, rl_update
from stpd.rl.reward import episode_reward

PRODUCER = Producer("synthetic-rl", "a" * 40, "b" * 64)


def test_behavior_distribution_matches_declared_formula_and_draws_are_keyed():
    p, q = distribution([2.0, 0.0, -1.0], 1.2, 0.03)
    assert math.isclose(sum(p), 1) and math.isclose(sum(q), 1)
    tempered = [x ** (1 / 1.2) for x in p]
    assert q == pytest.approx([0.97 * t / sum(tempered) + 0.01 for t in tempered])
    assert min(q) >= 0.01
    assert draw(q, "seed", "unit-1") == draw(q, "seed", "unit-1")
    counts = collections.Counter(draw(q, "seed", f"unit-{i}") for i in range(4000))
    assert counts[0] / 4000 == pytest.approx(q[0], abs=0.03)
    assert draw([0.0, 1.0, 0.0], "s", "u") == 1
    for bad in ({"mode": "temperature_uniform", "temperature": 2.0, "uniform_mix": 0, "seed": "s"},
                {"mode": "argmax", "temperature": 1.0, "uniform_mix": 0, "seed": "s"},
                {"mode": "temperature_uniform", "temperature": 1.0, "uniform_mix": 0}):
        with pytest.raises(ValueError):
            validate_policy(bad)
    with pytest.raises(ValueError):
        distribution([1.0, math.inf], 1.0, 0.0)


def test_sampling_only_for_unscored_training_trajectories_with_per_episode_seed():
    declared = {"mode": "temperature_uniform", "temperature": 1.0, "uniform_mix": 0.03}
    training = {"intent_id": "run-1", "retention": {"tier": "trajectory", "purpose": "training"}}
    assert behavior_policy({"artifact_id": "m"}, training) is None
    assert behavior_policy({"behavior_policy": declared}, training) == {**declared,
                                                                         "seed": "run-1"}
    for row in ({**training, "retention": {"tier": "trajectory", "purpose": "evaluation"}},
                {**training, "retention": {"tier": "formal"}}):
        with pytest.raises(BoundaryError, match="sampling_requires_unscored_training"):
            behavior_policy({"behavior_policy": declared}, row)
    with pytest.raises(BoundaryError, match="sampling_requires_unscored_training"):
        behavior_policy({"behavior_policy": {**declared, "seed": "fixed"}}, training)


def _episode(result, floor=None, bosses=None, status="finished"):
    return {"outcome": {"status": status, "unknown": status == "unknown",
                        "terminal": {"status": "observed", "result": result} if result else None,
                        "max_observed_floor": floor, "boss_clearance": {"count": bosses}},
            "identity": {"ascension": 0}}


def test_reward_win_beats_every_loss_and_unknown_is_excluded():
    assert episode_reward(_episode("win")) == (1.0, "win")
    best_loss = episode_reward(_episode("loss", floor=48, bosses=3))[0]
    assert best_loss is not None and best_loss < 1.0
    early = episode_reward(_episode("loss", floor=12, bosses=0))[0]
    assert early == pytest.approx(0.5 * 12 / 48)
    assert episode_reward(_episode("loss", floor=20, bosses=None))[0] == pytest.approx(
        0.5 * 20 / 48)
    assert episode_reward(_episode(None, status="unknown")) == (None, "unknown")
    assert episode_reward(_episode("loss", floor=None))[0] is None


def _model_artifact(store, weights=b"weights-v0"):
    sha = hashlib.sha256(weights).hexdigest()
    manifest = Manifest("model", PRODUCER, payloads=(
        store.put_bytes("weights", weights),
        store.put_bytes("package_manifest", json.dumps({"package_sha256": sha}).encode())))
    store.publish(manifest)
    return manifest, sha


def test_exchange_records_are_sealed_append_only_and_hash_checked(tmp_path):
    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    exchange = Exchange(peer, "smoke")
    manifest, sha = _model_artifact(exchange.store)
    record = {"run": "smoke", "version": 0, "parent_version": None,
              "model_artifact_id": manifest.artifact_id, "model_sha256": sha}
    exchange.publish_model(record)
    assert exchange.publish_model(record)["version"] == 0  # identical republish is idempotent
    with pytest.raises(BoundaryError, match="immutable_key_collision"):
        exchange.publish_model({**record, "parent_version": 7})
    with pytest.raises(BoundaryError, match="model_version_binding_mismatch"):
        exchange.publish_model({**record, "version": 1, "model_sha256": "0" * 64})
    other = Exchange(peer, "smoke")  # a second process sees the same latest version
    assert other.latest_model()["model_artifact_id"] == manifest.artifact_id
    package = other.materialize_model(other.latest_model(), tmp_path / "cache")
    assert (package / "model.tensor-tree").read_bytes() == b"weights-v0"
    assert other.materialize_model(other.latest_model(), tmp_path / "cache") == package
    exchange.publish_shard("actor-a", 0, {"shard_artifact_id": None, "run_ids": [],
                                          "model_version": 0, "disposition": "without_shard"})
    with pytest.raises(BoundaryError, match="immutable_key_collision"):
        exchange.publish_shard("actor-a", 0, {"shard_artifact_id": None, "run_ids": []})
    key = other.shard_keys()[0]
    assert other.shard_record(key)["disposition"] == "without_shard"
    raw = tmp_path / "peer" / key
    raw.chmod(0o600)
    raw.write_bytes(raw.read_bytes().replace(b"without_shard", b"trajectory"))
    with pytest.raises(BoundaryError, match="record_integrity_failure"):
        other.shard_record(key)
    with pytest.raises(BoundaryError, match="invalid_run_name"):
        Exchange(peer, "../escape")


def test_rl_update_moves_toward_positive_advantage_and_respects_anchor():
    first = project(observation())
    torch.manual_seed(3)
    reference = MStd(fit_vocabulary([first])).eval()
    model = MStd(reference.vocabulary)
    model.load_state_dict(reference.state_dict())
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-2)
    with torch.no_grad():
        before = torch.log_softmax(model.forward_batch([first])[0], -1)
    action = int(before.argmin())
    probability = float(before[action].exp())
    samples = [RlSample(first, action, probability, probability, 1.0, 0, f"k{i}")
               for i in range(4)]
    for _ in range(5):
        result = rl_update(model, reference, optimizer, samples, [(first, action)], 1e-2,
                           LossConfig(clip=0.2, kl_coef=0.02, anchor_coef=0.1))
        assert math.isfinite(result["gradient_norm"]) and result["kl"] >= -1e-6
    with torch.no_grad():
        after = torch.log_softmax(model.forward_batch([first])[0], -1)
    assert after[action] > before[action]
    with pytest.raises(BoundaryError, match="empty_rl_batch"):
        rl_update(model, reference, optimizer, [], [], 1e-2, LossConfig())


def test_cached_reference_gives_the_same_update_as_recomputing():
    frame = project(observation())
    torch.manual_seed(5)
    reference = MStd(fit_vocabulary([frame])).eval()
    samples = [RlSample(frame, 0, 0.4, 0.5, 1.0, 0, "a"),
               RlSample(frame, 1, 0.3, 0.2, -1.0, 0, "b")]
    results = []
    for cache in (None, {}):
        model = MStd(reference.vocabulary)
        model.load_state_dict(reference.state_dict())
        optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
        torch.manual_seed(7)  # same dropout draws for both runs
        results.append(rl_update(model, reference, optimizer, samples, [], 0.1, LossConfig(),
                                 cache))
        if cache is not None:
            assert set(cache) == {"a", "b"}
    for name in ("policy_loss", "kl", "gradient_norm"):
        assert results[0][name] == pytest.approx(results[1][name])


def test_learner_resumes_a_run_from_its_published_full_checkpoint(tmp_path, monkeypatch):
    from spireagent.workbench.rl_learner import IO
    from stpd.p1.export import export_model
    from stpd.rl import learner as module
    from stpd.rl.learner import Learner, LearnerConfig

    monkeypatch.setattr(module, "source_identity", lambda _root: PRODUCER)

    torch.manual_seed(11)
    package = tmp_path / "start"
    checkpoint = tmp_path / "start.tensor-tree"
    checkpoint.write_bytes(b"synthetic start checkpoint")
    export_model(MStd(fit_vocabulary([project(observation())])), checkpoint, package,
                 {"scope": "synthetic"})
    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    config = LearnerConfig(run="resume", peer=peer, start_package=str(package))
    first = Learner(config, tmp_path / "a", IO)
    with pytest.raises(BoundaryError, match="run_already_has_versions_use_resume_checkpoint"):
        Learner(config, tmp_path / "b", IO)
    sum(p.sum() for p in first.model.parameters()).backward()
    first.optimizer.step()  # optimizer state that a successor must keep
    first.updates, first.counts["episodes_loss"] = 3, 5
    first.returns.extend([0.25, 0.5])
    first.maybe_version(force=True)
    first._collect_version(wait=True)
    first._take_checkpoint(wait=True)
    record = first.exchange.latest_checkpoint()
    assert record["version"] == 1 and first.exchange.latest_model()["version"] == 1
    second = Learner(LearnerConfig(**{**config.__dict__,
                                      "resume_checkpoint": record["checkpoint_artifact_id"]}),
                     tmp_path / "b", IO)
    assert (second.version, second.lineage_version, second.updates) == (1, 1, 3)
    assert list(second.returns) == [0.25, 0.5] and second.counts["episodes_loss"] == 5
    for name, value in first.model.state_dict().items():
        assert torch.equal(value, second.model.state_dict()[name])
    assert second.optimizer.state_dict()["state"].keys() == first.optimizer.state_dict()[
        "state"].keys()
    second.updates += 1
    second.maybe_version(force=True)
    second._collect_version(wait=True)
    assert second.exchange.latest_model()["parent_version"] == 1
    second.exchange.request_stop("test_wall_limit")
    summary = second.run()
    assert summary["updates"] == 4
    assert second.exchange.latest_checkpoint()["version"] == 2
    assert second.exchange.latest_event()["kind"] == "finished"
    first._take_checkpoint(wait=True)
    first.checkpoints.__exit__(None)


def test_learner_failure_is_last_remote_event_after_cleanup(tmp_path, monkeypatch):
    from spireagent.workbench.rl_learner import IO
    from stpd.p1.export import export_model
    from stpd.rl import learner as module
    from stpd.rl.learner import Learner, LearnerConfig

    monkeypatch.setattr(module, "source_identity", lambda _root: PRODUCER)
    package, checkpoint = tmp_path / "start", tmp_path / "start.tensor-tree"
    checkpoint.write_bytes(b"synthetic start checkpoint")
    export_model(MStd(fit_vocabulary([project(observation())])), checkpoint, package,
                 {"scope": "synthetic"})
    learner = Learner(LearnerConfig(run="failure", start_package=str(package),
        peer={"kind": "local", "directory": str(tmp_path / "peer")}), tmp_path / "work", IO)

    def fail():
        raise BoundaryError("rl_loss", "non_finite_update")

    monkeypatch.setattr(learner, "poll", fail)
    with pytest.raises(BoundaryError, match="non_finite_update"):
        learner.run()
    event = learner.exchange.latest_event()
    assert event["kind"] == "failed" and event["error_code"] == "non_finite_update"


def test_stop_captures_latest_version_skipped_while_checkpoint_publisher_was_busy(
        tmp_path, monkeypatch):
    import threading

    from spireagent.workbench.rl_learner import IO
    from stpd.p1.export import export_model
    from stpd.rl import learner as module
    from stpd.rl.learner import Learner, LearnerConfig

    monkeypatch.setattr(module, "source_identity", lambda _root: PRODUCER)
    package, checkpoint = tmp_path / "start", tmp_path / "start.tensor-tree"
    checkpoint.write_bytes(b"synthetic start checkpoint")
    export_model(MStd(fit_vocabulary([project(observation())])), checkpoint, package,
                 {"scope": "synthetic"})
    learner = Learner(LearnerConfig(run="busy-checkpoint", start_package=str(package),
        peer={"kind": "local", "directory": str(tmp_path / "peer")}), tmp_path / "work", IO)
    gate = threading.Event()
    publish = learner.checkpoints.publish

    def blocked_publish(snapshot):
        assert gate.wait(timeout=30)
        return publish(snapshot)

    monkeypatch.setattr(learner.checkpoints, "publish", blocked_publish)
    try:
        learner.updates = 1
        learner.maybe_version(force=True)
        learner._collect_version(wait=True)
        learner.updates = 2
        learner.maybe_version(force=True)
        learner._collect_version(wait=True)
        assert learner.exchange.latest_model()["version"] == 2
        assert learner.exchange.latest_checkpoint() is None
    finally:
        gate.set()
    learner.exchange.request_stop("test_wall_limit")
    learner.run()
    record = learner.exchange.latest_checkpoint()
    assert record["version"] == learner.exchange.latest_model()["version"] == 2
    manifest = learner.exchange.store.get_manifest(record["checkpoint_artifact_id"])
    assert manifest.parameters.value()["updates"] == learner.updates == 2
    assert learner.exchange.latest_event()["kind"] == "finished"


def test_model_publication_uses_counters_frozen_with_its_weights(tmp_path, monkeypatch):
    import threading

    from spireagent.workbench.rl_learner import IO
    from stpd.p1.export import export_model
    from stpd.rl import learner as module
    from stpd.rl.learner import Learner, LearnerConfig

    monkeypatch.setattr(module, "source_identity", lambda _root: PRODUCER)
    package, checkpoint = tmp_path / "start", tmp_path / "start.tensor-tree"
    checkpoint.write_bytes(b"synthetic start checkpoint")
    export_model(MStd(fit_vocabulary([project(observation())])), checkpoint, package,
                 {"scope": "synthetic"})
    learner = Learner(LearnerConfig(run="frozen-counters", start_package=str(package),
        peer={"kind": "local", "directory": str(tmp_path / "peer")}), tmp_path / "work", IO)
    gate = threading.Event()
    publish = learner._publish

    def blocked_publish(*args):
        assert gate.wait(timeout=30)
        return publish(*args)

    monkeypatch.setattr(learner, "_publish", blocked_publish)
    try:
        learner.updates, learner.counts["rl_samples"] = 1, 10
        learner.maybe_version(force=True)
        learner.updates, learner.counts["rl_samples"] = 2, 20
    finally:
        gate.set()
    learner._collect_version(wait=True)
    record = learner.exchange.latest_model()
    descriptor = json.loads((learner.work / "versions/000001/model.json").read_bytes())
    assert record["training"]["updates"] == descriptor["training_identity"]["updates"] == 1
    assert record["training"]["rl_samples"] == 10
    assert learner.updates == 2 and learner.counts["rl_samples"] == 20
    learner.exchange.request_stop("test_wall_limit")
    learner.run()


def test_actor_switches_to_the_newest_fetched_version_between_ledgers(tmp_path, monkeypatch):
    from spireagent.workbench import evaluation_ledger, managed_pipeline
    from spireagent.workbench.rl_actor import Actor

    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    exchange = Exchange(peer, "swap")
    for version, weights in enumerate((b"weights-v0", b"weights-v1")):
        manifest, sha = _model_artifact(exchange.store, weights)
        if version == 0:
            exchange.publish_model({"run": "swap", "version": 0, "parent_version": None,
                                    "model_artifact_id": manifest.artifact_id,
                                    "model_sha256": sha})
        else:
            newer = {"run": "swap", "version": 1, "parent_version": 0,
                     "model_artifact_id": manifest.artifact_id, "model_sha256": sha}
    execution = tmp_path / "execution.json"
    execution.write_text(json.dumps({"peer": peer, "retention": {
        "tier": "trajectory", "purpose": "training", "audit_every": 50}}))
    built = []

    class Executor:
        def __init__(self, value, **_):
            built.append(value["models"])

        def close(self):
            pass

    class Ledger:
        def __init__(self, selection):
            self.selection = selection

        def run_managed(self, **_):
            if len(built) == 1:  # the learner publishes v1 while v0 is being played
                exchange.publish_model(newer)
                actor.prefetch.refresh()
            return {"status": "completed"}

        def slots(self):
            return [{"seed": self.selection, "status": "saved", "run_id": None}]

    monkeypatch.setattr(managed_pipeline, "PipelinedManagedExecutor", Executor)
    monkeypatch.setattr(evaluation_ledger.EvaluationLedger, "create", classmethod(
        lambda cls, *_, selections, **__: Ledger(selections["model"])))
    actor = Actor({"schema": "spireagent/rl-actor-config-1", "run": "swap", "peer": peer,
                   "actor": "a", "execution": str(execution), "seed_namespace": "test",
                   "behavior": {"mode": "temperature_uniform", "temperature": 1.0,
                                "uniform_mix": 0.03},
                   "games_per_ledger": 1, "episode_seconds": 1, "wall_seconds": 600,
                   "max_games": 2, "model_poll_seconds": 3600}, tmp_path / "actor")
    summary = actor.run()
    assert [g["model_version"] for g in summary["games"]] == [0, 1]
    assert [list(models) for models in built] == [["rl-v000000"], ["rl-v000001"]]
    records = [exchange.shard_record(k) for k in exchange.shard_keys()]
    assert [(r["model_version"], r["disposition"]) for r in records] == [
        (0, "without_shard"), (1, "without_shard")]
    assert summary["counts"]["executor_builds"] == 2

    # Any ledger stop (here: unknown cleanup not accepted) ends the actor; no fresh seeds.
    Ledger.run_managed = lambda self, **_: {"status": "stopped",
                                            "stop_reason": "unknown_cleanup_requires_owner_read"}
    again = Actor({**actor.config, "actor": "b"}, tmp_path / "actor-b")
    stopped = again.run()
    assert stopped["status"] == "stopped_ledger:unknown_cleanup_requires_owner_read"
    assert len(stopped["games"]) == 1
