"""Faithful independent-lane numerical reference and checkpoint negative cases."""

from __future__ import annotations

import pytest
import torch
from test_structured_remote_domain import packet
from test_structured_resume import equal_tree
from test_structured_s0 import sample, source
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun.structured_sequences import parse_structured_dataset
from stpd.models.structured_batch import BatchStructuredTrainingEngine
from stpd.models.structured_device import structured_device_session
from stpd.models.structured_engine import StructuredTrainingEngine
from stpd.models.structured_m2 import StructuredM2
from stpd.structured_code_scope import TRAINING_SCOPE
from stpd.structured_remote_contracts import StructuredDevicePolicy
from stpd.structured_workload_contracts import StructuredBatchPolicy, batch_rounds
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


def lane_dataset():
    value = source([sample(20 + index, hp=12 - index) for index in range(10)], group="long")
    short = source([sample(40), sample(41, hp=9)], group="short")
    third = source([sample(50 + index, hp=7 - index) for index in range(5)], group="last")
    value["runs"].extend(short["runs"] + third["runs"])
    value["runs"][0]["steps"][2]["reset_before"] = True
    value["runs"][0]["steps"][2]["reset_reason"] = "synthetic_segment"
    # Unlabelled advancement still writes W, while score-only supervised rows read it.
    for step in value["runs"][0]["steps"][:1]:
        step["chosen_action_id"] = None
    return parse_structured_dataset(json_bytes(value))


def engine(data=None, lanes=2):
    data = lane_dataset() if data is None else data
    data, config, identity, *_ = packet(data_value=data, batch_policy=StructuredBatchPolicy(lanes))
    return BatchStructuredTrainingEngine(
        data, config, code_scope=TRAINING_SCOPE, identity=identity, policy=StructuredDevicePolicy()
    )


@pytest.fixture(autouse=True)
def numeric_session():
    with structured_device_session(StructuredDevicePolicy(), cpu_threads=2):
        yield


def test_single_lane_is_exact_cpu_sequence_and_default_identity_is_unchanged():
    batch = engine(lanes=1)
    serial = StructuredTrainingEngine(batch.dataset, batch.config)
    assert "batch_policy" not in serial.identity
    assert serial.identity["engine"] == "structured-tbptt-boundaries-v1"
    while not batch.training_complete:
        batch.advance_chunk()
        serial.advance_chunk()
        equal_tree(batch.model.state_dict(), serial.model.state_dict())
        equal_tree(batch.optimizer.state_dict(), serial.optimizer.state_dict())
        # A finished run is reset before the following run uses the lane.
        if batch._checkpoint_extension()["batch_lanes"][0]["next_step"] not in (0, None):
            equal_tree(batch.memory[0], serial.memory)
    assert batch.updates == serial.updates and batch.labels == serial.labels


def test_joint_loss_gradients_match_separate_independent_lane_backward_same_weights():
    batch = engine()
    reference = StructuredM2(seed=batch.config.seed)
    reference.load_state_dict(batch.model.state_dict())
    total_labels = batch.plans[0].labels
    expected_memories = {}
    # Compute each lane independently, normalize by total labels, accumulate gradients
    # without stepping or sharing W. This is a different backward decomposition.
    for lane, plan in batch.rounds[0]:
        memory = reference.initial_memory()
        losses = []
        for step in batch.train[plan.run_index].steps[plan.start : plan.end]:
            if step.reset_before:
                memory = reference.initial_memory()
            entities = reference.encode(step.frame)
            if step.advance:
                memory = reference.advance(entities, memory)
            if step.chosen_action_id is not None:
                scores = reference.score(step.frame, entities, memory)
                losses.append(
                    F.cross_entropy(
                        scores.unsqueeze(0),
                        torch.tensor([step.frame.action_ids.index(step.chosen_action_id)]),
                    )
                )
        expected_memories[lane] = memory.detach().clone()
        if losses:
            (torch.stack(losses).sum() / total_labels).backward()
    captured = {}
    original_step = batch.optimizer.step

    def capture():
        captured.update(
            {
                name: None if parameter.grad is None else parameter.grad.detach().clone()
                for name, parameter in batch.model.named_parameters()
            }
        )
        original_step()

    batch.optimizer.step = capture
    batch.advance_chunk()
    for name, parameter in reference.named_parameters():
        if parameter.grad is None:
            assert captured[name] is None
        else:
            torch.testing.assert_close(captured[name], parameter.grad, rtol=3e-5, atol=2e-7)
    # Exhausted short lane gets reset; long lane retains only its own forward W.
    for lane, cursor in enumerate(batch._checkpoint_extension()["batch_lanes"]):
        if cursor["next_step"] not in (0, None):
            equal_tree(batch.memory[lane], expected_memories[lane])
        else:
            assert not batch.memory[lane].any()


@pytest.mark.parametrize("boundary", [0, 1, 2, 3])
def test_pause_cancel_boundary_resume_restores_lanes_optimizer_and_cumulative_updates(boundary):
    original = engine()
    for _ in range(boundary):
        if not original.training_complete:
            original.advance_chunk()
    restored = engine()
    restored.restore(original.checkpoint())
    equal_tree(original.memory, restored.memory)
    while not original.training_complete:
        original.advance_chunk()
        restored.advance_chunk()
    equal_tree(original.model.state_dict(), restored.model.state_dict())
    equal_tree(original.optimizer.state_dict(), restored.optimizer.state_dict())
    assert original.metrics() == restored.metrics()
    assert original.updates == 3


def test_unlabelled_lanes_and_padding_never_add_labels_or_fake_advances():
    batch = engine()
    planned = batch_rounds(batch.dataset, batch.config, StructuredBatchPolicy(2))
    for group in planned[:3]:
        if batch.training_complete:
            break
        old = batch.labels, batch.observations, batch.advances
        batch.advance_chunk()
        assert batch.labels - old[0] == sum(plan.labels for _, plan in group)
        assert batch.observations - old[1] == sum(plan.end - plan.start for _, plan in group)
        assert batch.advances - old[2] == sum(plan.advances for _, plan in group)
    assert batch.memory.grad_fn is None
    assert all(parameter.grad is None for parameter in batch.model.parameters())


@pytest.mark.parametrize("mutation", ["cursor", "inactive_memory", "policy", "nan"])
def test_checkpoint_cannot_rebind_lane_or_restore_corrupt_memory(mutation):
    batch = engine()
    batch.advance_chunk()
    value = decode_checkpoint(batch.checkpoint())
    if mutation == "cursor":
        value["batch_lanes"][0]["run_index"] = 2
    elif mutation == "inactive_memory":
        value["memory"][1].fill_(1)
    elif mutation == "policy":
        value["identity"]["batch_policy"]["lanes"] = 3
    else:
        value["memory"][0].fill_(float("nan"))
    with pytest.raises(BoundaryError):
        engine().restore(encode_checkpoint(value))


def test_completed_run_wave_and_epoch_reset_have_no_memory_or_gradient_leakage():
    from spireagent.json_boundary import FrozenObject
    from stpd.models.structured_device import structured_device_runtime
    from stpd.models.structured_engine import execution_identity
    from stpd.structured_remote_contracts import freeze_remote_execution_identity
    from stpd.structured_workload_contracts import StructuredTrainingConfig

    data = lane_dataset()
    config = StructuredTrainingConfig(epochs=2)
    policy = StructuredDevicePolicy()
    identity = freeze_remote_execution_identity(
        execution_identity(data, config, code_scope=TRAINING_SCOPE),
        data,
        runtime=FrozenObject.of(structured_device_runtime(policy)),
        device_policy=policy,
        execution_binding_id="0" * 64,
        code_closure_sha256="0" * 64,
        batch_policy=StructuredBatchPolicy(2),
    )
    batch = BatchStructuredTrainingEngine(
        data, config, code_scope=TRAINING_SCOPE, identity=identity, policy=policy
    )
    epoch_totals = sum(len(run.steps) for run in data.runs)
    while not batch.training_complete:
        batch.advance_chunk()
        for index, cursor in enumerate(batch._checkpoint_extension()["batch_lanes"]):
            if cursor["next_step"] in {None, 0}:
                assert not batch.memory[index].any()
        if batch.chunk == 0:
            assert not batch.memory.any()
        restored = BatchStructuredTrainingEngine(
            data, config, code_scope=TRAINING_SCOPE, identity=identity, policy=policy
        )
        restored.restore(batch.checkpoint())
        equal_tree(batch.memory, restored.memory)
    assert batch.epoch == 2 and batch.observations == 2 * epoch_totals


@pytest.mark.parametrize("field", ["initial_train", "batch_lanes", "memory"])
def test_remote_parent_rejects_forged_initial_metrics_or_lane_state(field):
    from stpd.structured_remote_contracts import parse_structured_remote_request
    from stpd.workers.structured_remote_domain import _checked_state

    data, config, identity, _, request, _, _ = packet(
        data_value=lane_dataset(), batch_policy=StructuredBatchPolicy(2)
    )
    batch = BatchStructuredTrainingEngine(
        data, config, code_scope=TRAINING_SCOPE, identity=identity, policy=StructuredDevicePolicy()
    )
    batch.advance_chunk()
    state = decode_checkpoint(batch.checkpoint())
    if field == "initial_train":
        state[field]["mean_loss"] += 0.5
    elif field == "batch_lanes":
        state[field][0]["next_step"] += 1
    else:
        state[field][1].fill_(1)
    with pytest.raises(BoundaryError):
        _checked_state(
            encode_checkpoint(state), parse_structured_remote_request(request), data, config
        )
