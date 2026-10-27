"""Codec/probability counterexamples; synthetic data does not qualify a game run."""
from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.config import open_store
from spireagent.workbench.managed_trajectory_store import publish_shard, read_shard
from stpd.p1.trajectory import SCHEMA, validate_behavior, validate_trajectory

PRODUCER = Producer("synthetic-trajectory", "a" * 40, "b" * 64)


def episode(model_id, run_id="one"):
    spec = {"id": "synthetic", "version": "1"}
    observation = {"catalog": [{"choice_id": "a"}, {"choice_id": "b"}]}
    return {"schema": SCHEMA, "run_id": run_id,
        "identity": {"producer": PRODUCER.to_dict(), "host_pin": {}, "runtime_pin": {},
            "model_artifact_id": model_id, "model_sha256": "c" * 64, "input_spec": spec,
            "seed": "fresh", "ascension": 0, "character": "Defect",
            "initial_progress_sha256": "d" * 64, "retention": {"tier": "trajectory"},
            "behavior_policy": {"mode": "argmax", "temperature": None, "uniform_mix": 0}},
        "observations": [{"index": 0, "input_spec": spec, "observation": observation},
                         {"index": 1, "input_spec": spec, "observation": {"catalog": []}}],
        "steps": [{"index": 0, "observation_index": 0, "next_observation_index": 1,
            "next_step_index": None, "action_index": 0, "action_id": "a",
            "state_value": None, "action_values": None,
            "behavior": {"mode": "argmax", "probabilities": [1.0, 0.0],
                "model_probabilities": [0.75, 0.25], "selected_probability": 1.0,
                "temperature": None, "uniform_mix": 0}}],
        "outcome": {"status": "finished", "unknown": False, "terminal": {"result": "loss"}},
        "score_eligible": False, "rl_ready": False}


def test_sampling_probability_includes_temperature_and_uniform_floor():
    raw = [0.8, 0.2]
    t, epsilon = 1.2, 0.03
    weights = [p ** (1 / t) for p in raw]
    probabilities = [(1-epsilon)*p/sum(weights)+epsilon/2 for p in weights]
    behavior = {"mode": "temperature_uniform", "temperature": t, "uniform_mix": epsilon,
        "model_probabilities": raw, "probabilities": probabilities,
        "selected_probability": probabilities[1]}
    validate_behavior(behavior, 2, 1)
    behavior["selected_probability"] = raw[1]
    with pytest.raises(BoundaryError, match="behavior_probability_mismatch"):
        validate_behavior(behavior, 2, 1)
    behavior["selected_probability"] = probabilities[1]
    behavior["probabilities"] = raw
    with pytest.raises(BoundaryError, match="behavior_probability_mismatch"):
        validate_behavior(behavior, 2, 1)


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(score_eligible=True),
    lambda v: v.update(rl_ready=True),
    lambda v: v["identity"].pop("seed"),
    lambda v: v["steps"][0].update(action_id="foreign"),
    lambda v: v["steps"][0].update(next_observation_index=0),
    lambda v: v["steps"][0].update(action_values=[1.0]),
    lambda v: v["steps"][0].update(state_value=math.nan),
    lambda v: v["steps"][0]["behavior"].update(probabilities=[0.75, 0.25]),
    lambda v: v["outcome"].update(status="unknown", unknown=False),
])
def test_invalid_trajectory_is_not_silently_usable(mutate):
    value = episode("e" * 64)
    mutate(value)
    with pytest.raises((BoundaryError, ValueError)):
        validate_trajectory(value)


def test_eight_episode_shard_fresh_store_roundtrip_and_corruption(tmp_path: Path):
    local, peer = open_store(str(tmp_path / "local")), open_store(str(tmp_path / "peer"))
    weights = local.put_bytes("weights", b"synthetic-model")
    model = Manifest("model", PRODUCER, payloads=(weights,))
    local.publish(model)
    episodes = [episode(model.artifact_id, str(i)) for i in range(8)]
    for value in episodes:
        value["identity"]["model_sha256"] = weights.sha256
    manifest = publish_shard(local, episodes, PRODUCER, scratch=tmp_path)
    from spireagent.storage.store import copy_artifact

    copy_artifact(local, peer, manifest.artifact_id)
    assert list(read_shard(peer, manifest.artifact_id, scratch=tmp_path)) == episodes
    assert manifest == publish_shard(local, episodes, PRODUCER, scratch=tmp_path)
    with pytest.raises(BoundaryError, match="shard_episode_limit"):
        publish_shard(local, episodes + [episodes[0]], PRODUCER, scratch=tmp_path)
    duplicate = copy.deepcopy(episodes)
    duplicate[-1]["run_id"] = duplicate[0]["run_id"]
    with pytest.raises(BoundaryError, match="duplicate_episode"):
        publish_shard(local, duplicate, PRODUCER, scratch=tmp_path)
    index = peer.blobs.get(f"payload-indexes/v1/{manifest.payload('trajectory').sha256}.json")
    import json

    chunk = json.loads(index)["chunks"][0]["sha256"]
    path = tmp_path / "peer" / "objects" / "sha256" / chunk
    path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        list(read_shard(peer, manifest.artifact_id, scratch=tmp_path))


def test_shard_model_weights_identity_is_not_just_a_label(tmp_path):
    store = open_store(str(tmp_path / "store"))
    model = Manifest("model", PRODUCER, payloads=(store.put_bytes("weights", b"model-A"),))
    store.publish(model)
    value = episode(model.artifact_id)
    with pytest.raises(BoundaryError, match="shard_model_weights_mismatch"):
        publish_shard(store, [value], PRODUCER, scratch=tmp_path)
