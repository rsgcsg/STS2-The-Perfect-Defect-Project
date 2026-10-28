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
from spireagent.workbench.managed_evaluation import behavior_policy
from spireagent.workbench.rl_exchange import Exchange
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
    samples = [RlSample(first, action, float(before[action].exp()), 1.0, 0, f"k{i}")
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
    samples = [RlSample(frame, 0, 0.4, 1.0, 0, "a"), RlSample(frame, 1, 0.3, -1.0, 0, "b")]
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
