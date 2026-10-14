"""Actual protobuf shapes from the locked SDK; no live client/provider calls."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from modal_proto import api_pb2 as api
from training_executor_fixtures import prepared_fixture

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.modal_training_executor import SdkModalTrainingBackend, modal_gpu_matches
from spireagent.training_execution_contracts import TrainingProviderBudget


def replies(attempt):
    result = api.FunctionGetResponse(function_id="fu-wire")
    result.handle_metadata.app_id = "ap-wire"
    result.handle_metadata.function_name = "training_attempt"
    result.handle_metadata.function_type = api.Function.FUNCTION_TYPE_FUNCTION
    ranked = result.function.ranked_functions.add(rank=0)
    definition = ranked.function
    definition.image_id = attempt.placement.image_object_id
    definition.function_name = "training_attempt"
    definition.resources.milli_cpu = attempt.limits.cpu_millicores
    definition.resources.milli_cpu_max = attempt.limits.cpu_millicores
    definition.resources.memory_mb = attempt.limits.memory_mib
    definition.resources.memory_mb_max = attempt.limits.memory_mib
    definition.startup_timeout_secs = attempt.limits.startup_seconds
    result.function.timeout_secs = attempt.limits.wall_seconds
    # Locked SDK's legacy FunctionData startup field remains zero.
    assert result.function.startup_timeout_secs == 0
    result.function.autoscaler_settings.max_containers = 1
    layout = api.AppGetLayoutResponse()
    layout.app_layout.function_ids["training_attempt"] = result.function_id
    obj = layout.app_layout.objects.add(object_id=result.function_id)
    obj.function_handle_metadata.CopyFrom(result.handle_metadata)
    return layout, result


class Stub:
    def __init__(self, attempt, layout, result):
        self.attempt, self.layout, self.result = attempt, layout, result

    async def AppGetLayout(self, request, **kwargs):
        assert isinstance(request, api.AppGetLayoutRequest) and request.app_id == "ap-wire"
        return self.layout

    async def FunctionGet(self, request, **kwargs):
        assert isinstance(request, api.FunctionGetRequest)
        assert request.app_name == self.attempt.app_name
        assert request.environment_name == self.attempt.placement.environment
        assert request.object_tag == "training_attempt" and request.app_version == 0
        return self.result


def backend(attempt, layout, result):
    client = SimpleNamespace(stub=Stub(attempt, layout, result))

    async def from_env():
        return client

    sdk = SimpleNamespace(Client=SimpleNamespace(from_env=SimpleNamespace(aio=from_env)))
    return SdkModalTrainingBackend(sdk=sdk)


def test_rank_zero_definition_is_actual_startup_resource_owner():
    attempt, _, _ = prepared_fixture()
    layout, result = replies(attempt)
    assert (
        backend(attempt, layout, result)._function_identity(
            attempt, "ap-wire", budget=TrainingProviderBudget(lambda: 30)
        )
        == "fu-wire"
    )


@pytest.mark.parametrize(
    "change", ["image", "startup", "cpu", "cpu_max", "memory_max", "app", "rank"]
)
def test_provider_protobuf_identity_and_resource_drift_reject(change):
    attempt, _, _ = prepared_fixture()
    layout, result = replies(attempt)
    definition = result.function.ranked_functions[0].function
    if change == "image":
        definition.image_id = "im-drift"
    elif change == "startup":
        definition.startup_timeout_secs += 1
    elif change == "cpu":
        definition.resources.milli_cpu += 1
    elif change == "cpu_max":
        definition.resources.milli_cpu_max = 0
    elif change == "memory_max":
        definition.resources.memory_mb_max = 0
    elif change == "app":
        result.handle_metadata.app_id = "ap-drift"
    else:
        result.function.ranked_functions[0].rank = 1
    with pytest.raises(BoundaryError):
        backend(attempt, layout, result)._function_identity(
            attempt, "ap-wire", budget=TrainingProviderBudget(lambda: 30)
        )


def a10_attempt():
    attempt, _, _ = prepared_fixture()
    runtime = attempt.runtime.value()
    runtime["device"] = "cuda:0"
    return replace(
        attempt,
        placement=replace(attempt.placement, gpu="A10", device="cuda:0"),
        runtime=FrozenObject.of(runtime),
        limits=replace(attempt.limits, cpu_millicores=2000, memory_mib=8192),
    )


@pytest.mark.parametrize("wire_type", ["A10", "A10G"])
def test_a10_original_function_consumer_accepts_observed_modal_wire_alias(wire_type):
    # q05's original FunctionCreateResponse records A10G/count1, CPU2000/RAM8192.
    # Reconstruct those resource fields in locked SDK FunctionGetResponse, which
    # is the ordinary adapter's actual consumer; this fixture makes no RPC claim.
    attempt = a10_attempt()
    layout, result = replies(attempt)
    gpu = result.function.ranked_functions[0].function.resources.gpu_config
    gpu.gpu_type, gpu.count = wire_type, 1
    assert (
        backend(attempt, layout, result)._function_identity(
            attempt, "ap-wire", budget=TrainingProviderBudget(lambda: 30)
        )
        == "fu-wire"
    )
    assert gpu.gpu_type == wire_type  # Preserve provider evidence, no normalization.


@pytest.mark.parametrize(
    "defect",
    [
        "wrong_gpu",
        "empty_gpu",
        "zero_count",
        "two_count",
        "image",
        "cpu",
        "cpu_max",
        "memory",
        "memory_max",
        "startup",
        "timeout",
        "rank",
        "retries",
    ],
)
def test_a10_wire_alias_does_not_hide_other_original_function_drift(defect):
    attempt = a10_attempt()
    layout, result = replies(attempt)
    definition = result.function.ranked_functions[0].function
    gpu = definition.resources.gpu_config
    gpu.gpu_type, gpu.count = "A10G", 1
    if defect == "wrong_gpu":
        gpu.gpu_type = "H100!"
    elif defect == "empty_gpu":
        gpu.gpu_type = ""
    elif defect == "zero_count":
        gpu.count = 0
    elif defect == "two_count":
        gpu.count = 2
    elif defect == "image":
        definition.image_id = "im-drift"
    elif defect == "cpu":
        definition.resources.milli_cpu += 1
    elif defect == "cpu_max":
        definition.resources.milli_cpu_max = 0
    elif defect == "memory":
        definition.resources.memory_mb += 1
    elif defect == "memory_max":
        definition.resources.memory_mb_max = 0
    elif defect == "startup":
        definition.startup_timeout_secs += 1
    elif defect == "timeout":
        result.function.timeout_secs += 1
    elif defect == "rank":
        result.function.ranked_functions[0].rank = 1
    else:
        definition.retry_policy.retries = 1
    with pytest.raises(BoundaryError):
        backend(attempt, layout, result)._function_identity(
            attempt, "ap-wire", budget=TrainingProviderBudget(lambda: 30)
        )


@pytest.mark.parametrize("selector", ["L4", "L40S", "A100-40GB", "A100-80GB", "H100!"])
def test_other_modal_selectors_remain_exact(selector):
    assert modal_gpu_matches(selector, selector, 1)
    assert not modal_gpu_matches(selector, "A10G", 1)
    assert not modal_gpu_matches(selector, selector, 2)


def test_wire_alias_does_not_expand_public_selector_or_accept_invalid_count():
    attempt = a10_attempt()
    with pytest.raises(BoundaryError, match="unsupported_gpu"):
        replace(attempt.placement, gpu="A10G")
    assert not modal_gpu_matches("A10G", "A10G", 1)
    assert not modal_gpu_matches("A10", "A10G", True)
    assert modal_gpu_matches("none", "", 0)
    assert not modal_gpu_matches("none", "A10G", 0)
    assert not modal_gpu_matches("none", "", 1)


def test_blocking_sdk_coroutine_uses_original_remaining_and_cancels():
    stopped = []

    async def blocked():
        try:
            await asyncio.sleep(3600)
        finally:
            stopped.append(True)

    with pytest.raises(TimeoutError):
        SdkModalTrainingBackend._await(blocked, TrainingProviderBudget(lambda: 0.03))
    assert stopped == [True]


@pytest.mark.parametrize(
    "status,unfinished,known",
    [
        (api.GenericResult.GENERIC_STATUS_TERMINATED, 0, True),
        (api.GenericResult.GENERIC_STATUS_TERMINATED, 1, False),
        (api.GenericResult.GENERIC_STATUS_FAILURE, 0, False),
    ],
)
def test_actual_sdk_empty_terminated_result_requires_original_terminal_protobuf(
    status, unfinished, known
):
    import modal

    from spireagent.training_execution_contracts import (
        KnownFailure,
        TrainingAppRef,
        TrainingCallHandle,
    )

    attempt, _, _ = prepared_fixture()
    ref = TrainingAppRef(
        attempt.identity,
        "ap-wire",
        "fu-wire",
        attempt.app_name,
        attempt.placement.account_id,
        attempt.placement.environment,
        attempt.placement.image_object_id,
    )
    handle = TrainingCallHandle(ref, "fc-wire")
    result = api.GenericResult(status=status)
    response = api.FunctionGetOutputsResponse(num_unfinished_inputs=unfinished)
    response.outputs.add(idx=0).result.CopyFrom(result)
    reads = []

    class OutputStub:
        async def FunctionGetOutputs(self, request, **kwargs):
            assert request.function_call_id == handle.call_id
            assert request.start_idx == request.end_idx == 0
            assert request.clear_on_success is False and request.timeout == 0
            assert kwargs["retry"] is None
            reads.append(request)
            return response

    client = SimpleNamespace(stub=OutputStub())

    async def from_env():
        return client

    sdk = SimpleNamespace(
        exception=modal.exception,
        Client=SimpleNamespace(from_env=SimpleNamespace(aio=from_env)),
    )
    backend = SdkModalTrainingBackend(sdk=sdk)
    if known:
        outcome = backend.poll(attempt, handle, budget=TrainingProviderBudget(lambda: 30))
        assert isinstance(outcome, KnownFailure)
        assert outcome.code == "provider_call_terminated" and outcome.handle == handle
    elif unfinished:
        with pytest.raises(BoundaryError, match="original_output_binding_mismatch"):
            backend.poll(attempt, handle, budget=TrainingProviderBudget(lambda: 30))
    else:
        with pytest.raises(modal.exception.RemoteError):
            backend.poll(attempt, handle, budget=TrainingProviderBudget(lambda: 30))
    assert len(reads) == 1


def test_successful_original_call_with_nonbyte_return_is_known_invalid_not_a_candidate():
    import modal

    from spireagent.training_execution_contracts import (
        KnownFailure,
        TrainingAppRef,
        TrainingCallHandle,
    )

    attempt, _, _ = prepared_fixture()
    ref = TrainingAppRef(
        attempt.identity,
        "ap-wire",
        "fu-wire",
        attempt.app_name,
        attempt.placement.account_id,
        attempt.placement.environment,
        attempt.placement.image_object_id,
    )
    handle = TrainingCallHandle(ref, "fc-wire")

    from modal._serialization import serialize_data_format

    async def original_output(request, *, retry, timeout):
        assert request.function_call_id == handle.call_id and request.timeout == 0
        assert request.start_idx == request.end_idx == 0 and not request.clear_on_success
        assert retry is None and timeout <= 30
        response = api.FunctionGetOutputsResponse()
        response.outputs.add(idx=0, data_format=api.DATA_FORMAT_PICKLE).result.CopyFrom(
            api.GenericResult(
                status=api.GenericResult.GENERIC_STATUS_SUCCESS,
                data=serialize_data_format({"unexpected": "worker_return"}, api.DATA_FORMAT_PICKLE),
            )
        )
        return response

    client = SimpleNamespace(stub=SimpleNamespace(FunctionGetOutputs=original_output))

    async def from_env():
        return client

    sdk = SimpleNamespace(
        exception=modal.exception,
        Client=SimpleNamespace(from_env=SimpleNamespace(aio=from_env)),
    )
    outcome = SdkModalTrainingBackend(sdk=sdk).poll(
        attempt, handle, budget=TrainingProviderBudget(lambda: 30)
    )
    assert isinstance(outcome, KnownFailure) and outcome.handle == handle
    assert outcome.code == "provider_worker_response_invalid"
