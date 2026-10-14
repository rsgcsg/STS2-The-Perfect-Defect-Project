"""Actual protobuf shapes from the locked SDK; no live client/provider calls."""

import asyncio
from types import SimpleNamespace

import pytest
from modal_proto import api_pb2 as api
from training_executor_fixtures import prepared_fixture

from spireagent.json_boundary import BoundaryError
from spireagent.modal_training_executor import SdkModalTrainingBackend
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
    from modal._utils.function_utils import _process_result

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

    async def get(timeout):
        assert timeout == 0
        # Locked SDK decoder, actual protobuf status. No fake RemoteError invention.
        return await _process_result(result, api.DATA_FORMAT_PICKLE, client.stub, client)

    call = SimpleNamespace(get=SimpleNamespace(aio=get))
    sdk = SimpleNamespace(
        exception=modal.exception,
        Client=SimpleNamespace(from_env=SimpleNamespace(aio=from_env)),
        FunctionCall=SimpleNamespace(
            from_id=lambda selected: call if selected == handle.call_id else None
        ),
    )
    backend = SdkModalTrainingBackend(sdk=sdk)
    if known:
        outcome = backend.poll(attempt, handle, budget=TrainingProviderBudget(lambda: 30))
        assert isinstance(outcome, KnownFailure)
        assert outcome.code == "provider_call_terminated" and outcome.handle == handle
    else:
        with pytest.raises(modal.exception.RemoteError):
            backend.poll(attempt, handle, budget=TrainingProviderBudget(lambda: 30))
    assert len(reads) == 1
