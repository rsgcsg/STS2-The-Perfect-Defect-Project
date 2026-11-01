"""Real Store/trajectory codec → spawn → owner admission and complete CP restore.

Observations and outcomes are synthetic; no gameplay qualification is claimed.
"""
import collections
import copy
import pickle
import time
from dataclasses import replace

import pytest
import torch
from test_p1_t0 import observation
from test_p1_trajectory import episode as trajectory
from test_rl_critic import seed
from test_rl_engineering import configured

from spireagent.json_boundary import BoundaryError
from spireagent.storage.blobs import StoreError
from spireagent.workbench.managed_trajectory_store import publish_shard
from spireagent.workbench.rl_learner import IO
from stpd.p1.tensor_input import TensorFrame
from stpd.rl.critic import CriticConfig
from stpd.rl.learner import Learner, LearnerConfig
from stpd.rl.loss import LossConfig
from stpd.rl.performance import PerformanceConfig
from stpd.rl.reader import decode_process_read


def publish(learner, index, *, age=20, unknown=False, wrong_model=False, validation=False):
    model = learner.exchange.latest_model()
    value = trajectory(model["model_artifact_id"], run_id=f"process-run-{index}")
    value["identity"].update(input_spec=learner.input_spec,
        model_sha256=model["model_sha256"],
        seed=seed(validation),
        behavior_policy={"mode": "temperature_uniform", "temperature": 1.0, "uniform_mix": 0})
    for row in value["observations"]:
        row["input_spec"] = learner.input_spec
    value["observations"][0]["observation"] = observation()
    step = value["steps"][0]
    step.update(action_id="c9", action_index=1, generated_at=time.time() - 10)
    step["behavior"].update(mode="temperature_uniform", temperature=1.0, uniform_mix=0,
        probabilities=[.5, .5], model_probabilities=[.5, .5], selected_probability=.5)
    value["outcome"].update(terminal={"status": "observed", "result": "loss"},
                            max_observed_floor=12)
    if unknown:
        value["outcome"].update(status="unknown", unknown=True, terminal=None,
                                reason="synthetic_unknown")
    shard = publish_shard(learner.exchange.store, [value], learner.producer, scratch=learner.work)
    learner.exchange.publish_shard("reader-test", index, {
        "shard_artifact_id": shard.artifact_id, "run_ids": [value["run_id"]],
        "model_artifact_id": "f" * 64 if wrong_model else model["model_artifact_id"],
        "model_version": model["version"],
        "games": [{"run_id": value["run_id"], "started_at": time.time() - age}]})
    return learner.exchange.shard_keys()[-1]


def comparable_rows(rows):
    normalized = copy.deepcopy(rows)
    for row in normalized:
        for name in ("reader_seconds", "projection_seconds", "input_prepare_seconds"):
            row.pop(name, None)
        samples = []
        for frame, *metadata in row["samples"]:
            assert isinstance(frame, TensorFrame)
            assert all(t.device.type == "cpu" for t in frame.tensors.values())
            tensors = {key: (str(t.dtype), list(t.shape), t.contiguous().numpy().tobytes())
                       for key, t in frame.tensors.items()}
            reductions = {key: (r.inverse.tolist(),
                [(width, count, indices.tolist()) for width, count, indices in r.buckets])
                for key, r in frame.reductions.items()}
            samples.append((frame.choices, frame.entity_count, tensors, reductions, metadata))
        row["samples"] = samples
    return normalized


@pytest.mark.parametrize("ppo", [False, True])
def test_spawn_reader_preserves_inputs_age_unknown_and_complete_CP(tmp_path, monkeypatch, ppo):
    options = {"critic": CriticConfig(normalize_targets=True, head_lr=1e-4,
        fixed_validation=True, min_validation_episodes=1, min_train_episodes=1,
        validation_every=1), "loss": LossConfig(mode="ppo", value_coef=.02),
        "prepared_targets": True} if ppo else {}
    learner, _ = configured(tmp_path, monkeypatch, reader_backend="process", reader_workers=2,
        background_io=True, read_ahead=3, max_reuse=2, max_episode_age_seconds=600,
        performance=PerformanceConfig(packed=True, tensor_cache=True, precompile_inputs=True),
        **options)
    if ppo:
        learner.critic.ready = True
        learner.critic.freeze_policy(False)
    keys = [publish(learner, 0), publish(learner, 1, age=610),
            publish(learner, 2, unknown=True)]
    if ppo:
        keys.append(publish(learner, 3, validation=True))
    expected = [learner._read_once(key) for key in keys]
    rng = torch.get_rng_state().clone()
    futures = [learner._submit_read(key) for key in keys]
    actual = [learner._read_result(f.result(timeout=60)) for f in futures]
    assert torch.equal(rng, torch.get_rng_state())
    assert ([comparable_rows(rows) for rows in actual]
            == [comparable_rows(rows) for rows in expected])
    assert len(actual[0][0]["samples"]) == 1
    assert actual[1][0]["samples"] == actual[2][0]["samples"] == []
    assert actual[2][0]["episode"]["outcome"]["unknown"] is True
    # Reads and preparation never mutate owner admission/seen/use/RNG state.
    assert not learner.seen and not learner.buffer and not learner.seen_episodes
    learner._admit(actual[0])
    learner.seen.add(keys[0])
    if ppo:
        learner._admit(actual[3])
        learner.seen.add(keys[3])
        learner.critic.freeze_validation()
    assert learner.train_once() is not None
    assert learner.buffer[0].uses == 1
    weights = {k: v.clone() for k, v in learner.model.state_dict().items()}
    optimizer = copy.deepcopy(learner.optimizer.state_dict())
    (learner.work / "STOP").touch()
    learner.run()
    cp = learner.exchange.latest_checkpoint()
    with pytest.raises(RuntimeError, match="shutdown"):
        learner._submit_read(keys[0])
    successor = Learner(replace(learner.config, resume_checkpoint=cp["checkpoint_artifact_id"]),
                        tmp_path / "resumed", IO)
    try:
        assert successor.updates == learner.updates == 1
        assert successor.buffer[0].uses == 1
        assert successor.buffer[0].episode_started_at == learner.buffer[0].episode_started_at
        assert successor.seen == learner.seen
        assert successor.ledger.counts == learner.ledger.counts
        if ppo:
            assert len(successor.optimizer.param_groups) == 2
            assert successor.critic.fixed_validation_ids == ["process-run-3"]
            assert successor.critic.state() == learner.critic.state()
        for key, value in weights.items():
            assert torch.equal(successor.model.state_dict()[key], value)
        restored = successor.optimizer.state_dict()
        assert restored["param_groups"] == optimizer["param_groups"]
        for key, state in optimizer["state"].items():
            for name, value in state.items():
                assert torch.equal(restored["state"][key][name], value)
    finally:
        (successor.work / "STOP").touch()
        successor.run()


def test_spawn_propagates_original_classified_binding_error(tmp_path, monkeypatch):
    learner, _ = configured(tmp_path, monkeypatch, reader_backend="process")
    key = publish(learner, 0, wrong_model=True)
    try:
        with pytest.raises(BoundaryError) as error:
            learner._read_result(learner._submit_read(key).result(timeout=60))
        assert error.value.stage == "rl_learner"
        assert error.value.code == "shard_record_model_mismatch"
        assert not learner.buffer and not learner.seen
    finally:
        (learner.work / "STOP").touch()
        learner.run()


def test_process_error_payload_preserves_safe_retry_diagnostics():
    timings = collections.defaultdict(float)
    raw = pickle.dumps({"rows": [], "operation": "read_shard", "seconds": 2.5,
        "error": {"store": True, "stage": "artifact_store", "code": "s3_get_failed",
                  "recovery": "inspect", "diagnostic": {"retryable": True, "attempt": 3}}})
    with pytest.raises(StoreError) as error:
        decode_process_read(raw, timings)
    assert error.value.code == "s3_get_failed"
    assert error.value.diagnostic == {"retryable": True, "attempt": 3}
    assert timings["read_read_shard"] == 2.5


@pytest.mark.parametrize("backend", [None, "fork", [], True])
def test_invalid_reader_backend_rejected_before_io(tmp_path, backend):
    with pytest.raises(BoundaryError, match="invalid_bounded_io_configuration"):
        Learner(LearnerConfig(run="invalid", peer={}, reader_backend=backend), tmp_path, None)
