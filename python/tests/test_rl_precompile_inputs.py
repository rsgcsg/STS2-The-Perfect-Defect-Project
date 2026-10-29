"""The existing shard reader can prepare CPU-only complete inputs before admission."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
import torch
from test_p1_t0 import observation

from spireagent.json_boundary import BoundaryError
from stpd.p1.execution import MStdExecution
from stpd.p1.export import input_spec, runtime_projection
from stpd.p1.model import MStd
from stpd.p1.tensor_input import TensorFrame
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.learner import Learner, LearnerConfig
from stpd.rl.loss import LossConfig, RlSample, rl_update
from stpd.rl.performance import PerformanceConfig


def setup(precompile):
    torch.set_num_threads(2)
    torch.manual_seed(3)
    learner = Learner.__new__(Learner)
    learner.config = LearnerConfig(run="synthetic", peer={}, performance=PerformanceConfig(
        packed=True, tensor_cache=True, precompile_inputs=precompile, defer_sync=True))
    learner.reference = MStd(public_vocabulary(), MStdExecution.for_plan("mstd-training-v1")).eval()
    learner.model = copy.deepcopy(learner.reference)
    learner.projection = runtime_projection(learner.reference.vocabulary)
    learner.input_spec = input_spec(learner.reference.vocabulary, learner.projection)
    episode = {"identity": {"input_spec": learner.input_spec}, "run_id": "synthetic",
               "observations": [{"observation": observation()}],
               "steps": [{"observation_index": 0, "action_id": "c9", "action_index": 1,
                          "index": 0, "behavior": {"selected_probability": 0.5,
                                                   "model_probabilities": [0.5, 0.5]}}]}
    return learner, episode


def test_reader_precompile_is_cpu_only_rng_free_and_matches_real_update(monkeypatch):
    histories = []
    models = []
    for precompile in (False, True):
        learner, episode = setup(precompile)
        before_rng = torch.get_rng_state().clone()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-rl-reader") as reader:
            rows = reader.submit(learner._project, episode).result()
        assert torch.equal(before_rng, torch.get_rng_state())
        assert not learner.model._tokens
        frame, action, behavior, old, key = rows[0]
        assert action == 1 and key == "synthetic:0" and behavior == old == 0.5
        if precompile:
            assert isinstance(frame, TensorFrame)
            assert frame.choices == ("c7", "c9")
            assert all(value.device.type == "cpu" for value in frame.tensors.values())
            assert all(value.inverse.device.type == "cpu"
                       for value in frame.reductions.values())
            assert learner.reference._tokens
            monkeypatch.setattr(learner.reference, "tokenize", lambda _: pytest.fail(
                "owner update tokenized the reader reference"))
            monkeypatch.setattr(learner.model, "tokenize", lambda _: pytest.fail(
                "precompiled update repeated input compilation"))
        torch.manual_seed(17)
        result = rl_update(learner.model, learner.reference,
                           torch.optim.AdamW(learner.model.parameters(), lr=1e-3),
                           [RlSample(frame, action, behavior, old, .2, 0, key)], [],
                           1e-3, LossConfig(), {}, performance=learner.config.performance)
        result.pop("update_seconds")
        result.pop("phase_seconds", None)
        histories.append(result)
        models.append(learner.model)
    assert histories[0] == histories[1]
    for key, value in models[0].state_dict().items():
        assert torch.equal(value, models[1].state_dict()[key]), key


def test_precompile_preserves_original_projection_rejections_and_is_default_off():
    with pytest.raises(BoundaryError, match="precompile_inputs_requires_tensor_cache"):
        PerformanceConfig(precompile_inputs=True)
    learner, episode = setup(True)
    episode["identity"]["input_spec"] = {"different": "input"}
    with pytest.raises(BoundaryError, match="trajectory_input_spec_differs"):
        learner._project(episode)
    episode["identity"]["input_spec"] = learner.input_spec
    episode["steps"][0]["action_id"] = "not-in-complete-catalog"
    with pytest.raises(ValueError):
        learner._project(episode)
    assert not learner.reference._tokens
    assert not PerformanceConfig().precompile_inputs
