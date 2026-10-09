"""Current adapter against the locked public SDK; only client/wire I/O is replaced."""

from __future__ import annotations

import importlib.metadata
import socket
from unittest.mock import AsyncMock

import modal
import pytest
from google.protobuf.empty_pb2 import Empty
from modal._serialization import deserialize_data_format, serialize_data_format
from modal._utils.async_utils import synchronizer
from modal.client import _Client
from modal_proto import api_pb2 as api
from test_artifact_store_v1 import PRODUCER

from spireagent.json_boundary import BoundaryError
from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest
from stpd.cloud_jobs.modal import MODAL_SDK_VERSION, ModalProvider, ModalTarget


class Wire:
    """Typed transport replies, with no substitute SDK Function or FunctionCall."""

    call_id = "fc-conformance"
    function_id = "fu-conformance"

    def __init__(self, selected):
        self.selected = selected
        self.lookups = []
        self.submissions = []
        self.polls = []
        self.cancellations = []
        self.submit_error = None
        self.result = None
        self.execution_timeout = False
        self.live_app_unavailable = False

    async def FunctionGet(self, request):
        assert isinstance(request, api.FunctionGetRequest)
        self.lookups.append(request)
        if self.live_app_unavailable:
            pytest.fail("saved call recovery looked up the live App")
        assert request.app_name == self.selected.app_name
        assert request.object_tag == "compute" and request.app_version == 0
        return api.FunctionGetResponse(
            function_id=self.function_id,
            handle_metadata=api.FunctionHandleMetadata(
                function_name="compute", function_type=api.Function.FUNCTION_TYPE_FUNCTION,
                app_id="ap-conformance", supported_input_formats=[api.DATA_FORMAT_PICKLE],
            ),
        )

    async def FunctionMap(self, request):
        assert isinstance(request, api.FunctionMapRequest)
        self.submissions.append(request)
        assert request.function_id == self.function_id
        assert request.function_call_invocation_type == api.FUNCTION_CALL_INVOCATION_TYPE_ASYNC
        assert len(request.pipelined_inputs) == 1
        if self.submit_error is not None:
            raise self.submit_error
        return api.FunctionMapResponse(
            function_call_id=self.call_id,
            pipelined_inputs=[api.FunctionPutInputsResponseItem(input_id="in-conformance")],
        )

    async def FunctionGetOutputs(self, request, *, retry):
        assert isinstance(request, api.FunctionGetOutputsRequest)
        assert retry is not None  # Retain the actual SDK polling transport contract.
        self.polls.append(request)
        assert request.function_call_id == self.call_id
        assert request.timeout == 0 and request.start_idx == request.end_idx == 0
        assert request.clear_on_success is False
        if self.execution_timeout:
            result = api.GenericResult(
                status=api.GenericResult.GENERIC_STATUS_TIMEOUT, exception="remote deadline",
            )
        elif self.result is None:
            return api.FunctionGetOutputsResponse(num_unfinished_inputs=1)
        else:
            result = api.GenericResult(
                status=api.GenericResult.GENERIC_STATUS_SUCCESS,
                data=serialize_data_format(self.result, api.DATA_FORMAT_PICKLE),
            )
        return api.FunctionGetOutputsResponse(outputs=[api.FunctionGetOutputsItem(
            result=result, data_format=api.DATA_FORMAT_PICKLE,
        )])

    async def FunctionCallCancel(self, request):
        assert isinstance(request, api.FunctionCallCancelRequest)
        assert request.function_call_id == self.call_id and request.terminate_containers
        self.cancellations.append(request)
        return Empty()


@pytest.fixture
def sdk_wire(monkeypatch):
    assert importlib.metadata.version("modal") == MODAL_SDK_VERSION == "1.5.5"
    selected = ModalTarget(PRODUCER, "registry.example/stpd@sha256:" + "d" * 64)
    wire = Wire(selected)
    client = modal.Client("http://127.0.0.1:1", api.CLIENT_TYPE_CLIENT, None)
    internal = synchronizer._translate_in(client)
    internal._stub = wire

    def forbidden(*_args, **_kwargs):
        pytest.fail("conformance test attempted network I/O")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    opened = AsyncMock(side_effect=AssertionError("SDK client open forbidden"))
    monkeypatch.setattr(_Client, "_open", opened)
    monkeypatch.setattr(_Client, "from_env", AsyncMock(return_value=internal))
    yield ModalProvider(selected, sdk=modal), wire
    opened.assert_not_awaited()


def request():
    return ComputeRequest("features", "a" * 64, PRODUCER, "sdk-conformance-attempt")


def completed(wire, value):
    receipt = ComputeReceipt(
        value.request_id, value.attempt_id, value.kind, value.input_id,
        PRODUCER, "candidate_prepared", "b" * 64,
    )
    wire.result = {"target_id": wire.selected.target_id, "receipt": receipt.to_dict()}
    return receipt


def test_real_public_spawn_serializes_and_restores_original_call_without_live_app(sdk_wire):
    provider, wire = sdk_wire
    value = request()
    handle = provider.submit(value)
    assert handle.call_id == wire.call_id
    assert len(wire.lookups) == len(wire.submissions) == 1
    input_value = wire.submissions[0].pipelined_inputs[0].input
    assert not input_value.args_blob_id
    args, kwargs = deserialize_data_format(input_value.args, input_value.data_format, None)
    assert args == (value.to_dict(), wire.selected.target_id) and kwargs == {}
    saved = handle.to_dict()
    wire.live_app_unavailable = True
    # A fresh adapter uses actual public FunctionCall.from_id and lazy hydration.
    replacement = ModalProvider(wire.selected, sdk=modal)
    restored = replacement.restore_handle(saved)
    expected = completed(wire, value)
    assert replacement.poll(restored) == expected
    assert replacement.poll(restored) == expected
    assert restored.to_dict() == saved and restored.call_id == wire.call_id
    assert len(wire.lookups) == len(wire.submissions) == 1
    assert len(wire.polls) == 2 and not wire.cancellations


def test_real_sdk_submit_transport_unknown_invokes_once_without_retry(sdk_wire):
    provider, wire = sdk_wire
    wire.submit_error = TimeoutError("remote may have accepted the one input")
    with pytest.raises(BoundaryError) as error:
        provider.submit(request())
    assert error.value.code == "submission_unknown"
    assert len(wire.lookups) == len(wire.submissions) == 1
    assert not wire.polls and not wire.cancellations


def test_locked_sdk_pending_poll_raises_builtin_timeout_at_the_wire_boundary(sdk_wire):
    provider, wire = sdk_wire
    handle = provider.submit(request())
    call = modal.FunctionCall.from_id(handle.call_id)
    with pytest.raises(TimeoutError) as error:
        call.get(timeout=0)
    assert type(error.value) is TimeoutError
    assert not isinstance(error.value, modal.exception.TimeoutError)
    assert len(wire.polls) == len(wire.submissions) == 1


@pytest.mark.parametrize("execution_timeout", [False, True])
def test_real_sdk_empty_poll_and_remote_execution_timeout_remain_distinct(
    sdk_wire, execution_timeout,
):
    provider, wire = sdk_wire
    handle = provider.submit(request())
    saved = handle.to_dict()
    wire.execution_timeout = execution_timeout
    for _ in range(2):
        if execution_timeout:
            with pytest.raises(BoundaryError) as error:
                provider.poll(handle)
            assert error.value.code == "execution_timeout"
        else:
            assert provider.poll(handle) is None
        assert handle.to_dict() == saved
    assert len(wire.lookups) == len(wire.submissions) == 1
    assert len(wire.polls) == 2 and not wire.cancellations


def test_real_sdk_cancel_ack_does_not_create_a_terminal_receipt_or_new_call(sdk_wire):
    provider, wire = sdk_wire
    value = request()
    handle = provider.submit(value)
    saved = handle.to_dict()
    wire.live_app_unavailable = True
    restored = ModalProvider(wire.selected, sdk=modal).restore_handle(saved)
    assert provider.cancel(restored) is None
    assert len(wire.cancellations) == 1
    # The real SDK's cancellation response contains no result or terminal receipt.
    assert wire.result is None and not wire.polls
    assert restored.to_dict() == saved
    expected = completed(wire, value)
    assert provider.poll(restored) == expected
    assert len(wire.lookups) == len(wire.submissions) == 1
    assert all(item.function_call_id == saved["call_id"]
               for item in [*wire.polls, *wire.cancellations])
