"""Actual locked public SDK clients/decoder with guarded typed output I/O."""

from __future__ import annotations

import asyncio
import socket
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import modal
import pytest
from modal._serialization import serialize_data_format
from modal._utils.async_utils import synchronizer
from modal.client import _Client
from modal_proto import api_pb2 as api
from training_executor_fixtures import app_fixture, prepared_fixture, response_fixture

from spireagent.modal_training_executor import ModalTrainingExecutor, SdkModalTrainingBackend
from spireagent.training_execution_contracts import (
    CandidateResult,
    KnownFailure,
    TrainingCallHandle,
    TrainingProviderBudget,
    Unknown,
)


class OutputWire:
    def __init__(self, handle):
        self.handle = handle
        self.reads = []
        self.reply = api.FunctionGetOutputsResponse(num_unfinished_inputs=1)
        self.error = None
        self.blocked = False
        self.cancelled = 0
        self.available = 30.0
        self.remaining = lambda: self.available
        self.late = False
        self.blob_reads = 0
        self.blob_cancelled = 0

    async def FunctionGetOutputs(self, request, *, retry, timeout=None):
        assert isinstance(request, api.FunctionGetOutputsRequest)
        assert request.function_call_id == self.handle.call_id
        assert request.timeout == 0 and request.start_idx == request.end_idx == 0
        assert request.clear_on_success is False and request.last_entry_id == "0-0"
        self.reads.append((request, retry, timeout))
        if timeout is not None:
            assert retry is None and 0 < timeout <= 30
        else:
            assert retry is not None  # The real public SDK's legacy get(timeout=0).
        if self.error is not None:
            raise self.error
        if self.blocked:
            try:
                await asyncio.sleep(3600)
            finally:
                self.cancelled += 1
        if self.late:
            self.available = 0
        return self.reply

    async def BlobGet(self, request):
        assert isinstance(request, api.BlobGetRequest)
        assert request.blob_id == "bl-original-stalled-output"
        self.blob_reads += 1
        try:
            await asyncio.sleep(3600)
        finally:
            self.blob_cancelled += 1

    def output(self, value=None, *, status=api.GenericResult.GENERIC_STATUS_SUCCESS):
        self.reply = api.FunctionGetOutputsResponse()
        self.reply.outputs.add(idx=0, data_format=api.DATA_FORMAT_PICKLE).result.CopyFrom(
            api.GenericResult(
                status=status, data=serialize_data_format(value, api.DATA_FORMAT_PICKLE)
            )
        )


@pytest.fixture
def wire_system(monkeypatch):
    attempt, _, _ = prepared_fixture()
    handle = TrainingCallHandle(app_fixture(attempt), "fc-original-output-fixture")
    wire = OutputWire(handle)
    public_client = modal.Client("http://127.0.0.1:1", api.CLIENT_TYPE_CLIENT, None)
    internal = synchronizer._translate_in(public_client)
    internal._stub = wire
    opened = AsyncMock(side_effect=AssertionError("SDK client open forbidden"))
    monkeypatch.setattr(_Client, "_open", opened)
    monkeypatch.setattr(_Client, "_client_from_env", internal)
    monkeypatch.setattr(_Client, "_client_from_env_lock", None)
    synchronizer._get_loop(start=True)

    def forbidden(*_args, **_kwargs):
        pytest.fail("original-output test attempted network I/O")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    backend = SdkModalTrainingBackend(sdk=modal)
    owner = SimpleNamespace(
        load_app_ref=lambda _: handle.app_ref,
        load_call_handle=lambda _: handle,
        remaining_seconds=lambda _: wire.remaining(),
    )
    executor = ModalTrainingExecutor(owner, backend=backend)
    yield attempt, handle, wire, public_client, backend, executor
    opened.assert_not_awaited()


def poll(system, boundary):
    attempt, handle, wire, _, backend, executor = system
    if boundary == "backend":
        return backend.poll(attempt, handle, budget=TrainingProviderBudget(wire.remaining))
    return executor.poll(attempt, handle)


@pytest.mark.parametrize("unfinished", [1, 0])
def test_actual_public_sdk_pending_and_expired_exception_types(wire_system, unfinished):
    _, handle, wire, public_client, _, _ = wire_system
    wire.reply.num_unfinished_inputs = unfinished
    call = modal.FunctionCall.from_id(handle.call_id, client=public_client)
    expected = TimeoutError if unfinished else modal.exception.OutputExpiredError
    with pytest.raises(expected) as caught:
        call.get(timeout=0)
    assert type(caught.value) is expected
    assert isinstance(caught.value, modal.exception.TimeoutError) is (not unfinished)
    assert wire.reads


@pytest.mark.parametrize("boundary", ["backend", "executor"])
def test_fresh_original_pending_ack_is_pending_with_one_nonblocking_read(wire_system, boundary):
    assert poll(wire_system, boundary) is None
    wire = wire_system[2]
    assert len(wire.reads) == 1 and wire.reads[0][1] is None


@pytest.mark.parametrize("boundary", ["backend", "executor"])
def test_empty_finished_expired_ack_remains_unknown(wire_system, boundary):
    wire_system[2].reply.num_unfinished_inputs = 0
    if boundary == "backend":
        with pytest.raises(modal.exception.OutputExpiredError):
            poll(wire_system, boundary)
    else:
        assert isinstance(poll(wire_system, boundary), Unknown)
    assert len(wire_system[2].reads) == 1


@pytest.mark.parametrize("boundary", ["backend", "executor"])
@pytest.mark.parametrize(
    "error", [TimeoutError("local RPC timeout"), modal.exception.ServiceError("Deadline Exceeded")]
)
def test_no_ack_transport_errors_never_mean_pending(wire_system, boundary, error):
    wire_system[2].error = error
    if boundary == "backend":
        with pytest.raises(type(error)):
            poll(wire_system, boundary)
    else:
        assert isinstance(poll(wire_system, boundary), Unknown)
    assert len(wire_system[2].reads) == 1


def test_previous_pending_ack_cannot_cover_a_later_read_timeout(wire_system):
    assert poll(wire_system, "executor") is None
    wire_system[2].error = TimeoutError("next read has no ACK")
    assert isinstance(poll(wire_system, "executor"), Unknown)
    assert len(wire_system[2].reads) == 2


@pytest.mark.parametrize("boundary", ["backend", "executor"])
@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("remote user timeout"),
        modal.exception.FunctionTimeoutError("remote user class"),
    ],
)
def test_serialized_remote_builtin_timeout_is_not_empty_poll(wire_system, boundary, error):
    wire_system[2].output(error, status=api.GenericResult.GENERIC_STATUS_FAILURE)
    if boundary == "backend":
        with pytest.raises(type(error)):
            poll(wire_system, boundary)
    else:
        assert isinstance(poll(wire_system, boundary), Unknown)
    assert len(wire_system[2].reads) == 1


@pytest.mark.parametrize("boundary", ["backend", "executor"])
@pytest.mark.parametrize("value", [None, {"invalid": "bytes required"}])
def test_retained_nonbyte_success_is_known_invalid_not_pending(wire_system, boundary, value):
    wire_system[2].output(value)
    result = poll(wire_system, boundary)
    assert isinstance(result, KnownFailure)
    assert result.code == "provider_worker_response_invalid" and result.handle == wire_system[1]
    assert len(wire_system[2].reads) == 1


@pytest.mark.parametrize("boundary", ["backend", "executor"])
@pytest.mark.parametrize(
    "status,code",
    [
        (api.GenericResult.GENERIC_STATUS_TIMEOUT, "provider_execution_timeout"),
        (api.GenericResult.GENERIC_STATUS_TERMINATED, "provider_call_terminated"),
    ],
)
def test_original_single_terminal_failure_ack_retains_known_failure(
    wire_system, boundary, status, code
):
    wire_system[2].output(status=status)
    result = poll(wire_system, boundary)
    assert isinstance(result, KnownFailure) and result.code == code
    assert result.handle == wire_system[1] and len(wire_system[2].reads) == 1


@pytest.mark.parametrize(
    "defect",
    [
        "type",
        "index",
        "multiple",
        "unfinished",
        "pending-count",
        "missing-result",
        "unspecified-status",
        "unknown-status",
    ],
)
def test_invalid_terminal_ack_shape_is_unknown_before_decoding(wire_system, defect):
    wire = wire_system[2]
    wire.output(response_fixture(wire_system[0]))
    if defect == "type":
        wire.reply = SimpleNamespace(outputs=[], num_unfinished_inputs=1)
    elif defect == "pending-count":
        wire.reply = api.FunctionGetOutputsResponse(num_unfinished_inputs=2)
    elif defect == "missing-result":
        wire.reply.outputs[0].ClearField("result")
    elif defect == "unspecified-status":
        wire.reply.outputs[0].result.status = api.GenericResult.GENERIC_STATUS_UNSPECIFIED
    elif defect == "unknown-status":
        wire.reply.outputs[0].result.status = 99
    elif defect == "index":
        wire.reply.outputs[0].idx = 1
    elif defect == "multiple":
        wire.reply.outputs.add(idx=1)
    else:
        wire.reply.num_unfinished_inputs = 1
    assert isinstance(poll(wire_system, "executor"), Unknown)
    assert len(wire.reads) == 1


def test_successful_retained_original_bytes_reach_parent_transport_validation(wire_system):
    wire_system[2].output(response_fixture(wire_system[0]))
    result = poll(wire_system, "executor")
    assert isinstance(result, CandidateResult) and result.handle == wire_system[1]
    assert len(wire_system[2].reads) == 1


def test_expired_wallet_starts_no_original_output_rpc(wire_system):
    wire_system[2].available = 0
    assert isinstance(poll(wire_system, "executor"), Unknown)
    assert wire_system[2].reads == []


def test_ack_after_wallet_expiry_does_not_publish_pending(wire_system):
    wire_system[2].late = True
    assert isinstance(poll(wire_system, "executor"), Unknown)
    assert len(wire_system[2].reads) == 1


def test_real_async_wait_for_timeout_cancels_rpc_and_remains_unknown(wire_system):
    wire = wire_system[2]
    deadline = time.monotonic() + 0.03
    wire.remaining = lambda: max(0, deadline - time.monotonic())
    wire.blocked = True
    assert isinstance(poll(wire_system, "executor"), Unknown)
    assert len(wire.reads) == wire.cancelled == 1


def test_actual_sdk_blob_decode_is_canceled_by_same_original_wallet(wire_system):
    wire = wire_system[2]
    wire.output()
    wire.reply.outputs[0].result.data_blob_id = "bl-original-stalled-output"
    deadline = time.monotonic() + 0.03
    wire.remaining = lambda: max(0, deadline - time.monotonic())
    assert isinstance(poll(wire_system, "executor"), Unknown)
    assert len(wire.reads) == wire.blob_reads == wire.blob_cancelled == 1
