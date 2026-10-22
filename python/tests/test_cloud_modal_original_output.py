"""Locked SDK clients/decoder; only original typed output I/O is substituted."""
from __future__ import annotations

import modal
import pytest
from modal._serialization import serialize_data_format
from modal_proto import api_pb2 as api
from test_artifact_store_v1 import PRODUCER
from test_cloud_modal_sdk_conformance import sdk_wire as sdk_wire

from spireagent.json_boundary import BoundaryError
from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest
from stpd.cloud_jobs.modal import ModalCall


@pytest.fixture
def original_wire(sdk_wire):
    provider, wire = sdk_wire
    request = ComputeRequest("training", "a" * 64, PRODUCER, "original-attempt")
    handle = ModalCall(wire.selected.target_id, request, wire.call_id)
    wire.reply = api.FunctionGetOutputsResponse(num_unfinished_inputs=1)
    wire.reads = wire.polls
    return provider, handle, wire, wire.client


@pytest.mark.parametrize("unfinished", [1, 0])
def test_locked_sdk_timeout_classes_do_not_define_pending(original_wire, unfinished):
    _, handle, wire, client = original_wire
    wire.reply.num_unfinished_inputs = unfinished
    expected = TimeoutError if unfinished else modal.exception.OutputExpiredError
    with pytest.raises(expected) as caught:
        modal.FunctionCall.from_id(handle.call_id, client=client).get(timeout=0)
    assert type(caught.value) is expected
    assert isinstance(caught.value, modal.exception.TimeoutError) is (not unfinished)


def test_original_pending_then_no_ack_is_uncertain_not_pending(original_wire):
    provider, handle, wire, _ = original_wire
    saved = handle.to_dict()
    assert provider.poll(handle) is None
    wire.error = TimeoutError("RPC has no ACK")
    with pytest.raises(BoundaryError, match="result_unavailable"):
        provider.poll(handle)
    assert len(wire.reads) == 2 and handle.to_dict() == saved


def test_empty_finished_ack_is_expired_not_pending(original_wire):
    provider, handle, wire, _ = original_wire
    wire.reply.num_unfinished_inputs = 0
    with pytest.raises(BoundaryError, match="result_unavailable"):
        provider.poll(handle)
    assert len(wire.reads) == 1


@pytest.mark.parametrize("shape", ["multi_pending", "multi_output", "foreign_index",
                                  "unfinished_output", "missing_result", "invalid_status"])
def test_only_one_original_terminal_output_is_admitted(original_wire, shape):
    provider, handle, wire, _ = original_wire
    wire.reply.num_unfinished_inputs = 0
    if shape == "multi_pending":
        wire.reply.num_unfinished_inputs = 2
    else:
        output = wire.reply.outputs.add(idx=0, data_format=api.DATA_FORMAT_PICKLE)
        if shape != "missing_result":
            output.result.status = api.GenericResult.GENERIC_STATUS_SUCCESS
        if shape == "multi_output":
            wire.reply.outputs.add(idx=1)
        if shape == "foreign_index":
            output.idx = 1
        if shape == "unfinished_output":
            wire.reply.num_unfinished_inputs = 1
        if shape == "invalid_status":
            output.result.status = api.GenericResult.GENERIC_STATUS_UNSPECIFIED
    with pytest.raises(BoundaryError, match="original_output_binding_mismatch"):
        provider.poll(handle)
    assert len(wire.reads) == 1


def test_original_terminal_receipt_uses_locked_decoder_and_binding(original_wire):
    provider, handle, wire, _ = original_wire
    receipt = ComputeReceipt(handle.request.request_id, handle.request.attempt_id,
                             handle.request.kind, handle.request.input_id, PRODUCER,
                             "candidate_prepared", "b" * 64)
    value = {"target_id": handle.target_id, "receipt": receipt.to_dict()}
    wire.reply = api.FunctionGetOutputsResponse()
    wire.reply.outputs.add(idx=0, data_format=api.DATA_FORMAT_PICKLE).result.CopyFrom(
        api.GenericResult(status=api.GenericResult.GENERIC_STATUS_SUCCESS,
                          data=serialize_data_format(value, api.DATA_FORMAT_PICKLE))
    )
    assert provider.poll(handle) == receipt
    value["target_id"] = "c" * 64
    wire.reply.outputs[0].result.data = serialize_data_format(value, api.DATA_FORMAT_PICKLE)
    with pytest.raises(BoundaryError, match="deployed_target_mismatch"):
        provider.poll(handle)
