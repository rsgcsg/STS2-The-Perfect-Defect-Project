"""Actual numerical child protocol keeps model history across parent owner fences."""

from __future__ import annotations

import io
import json

import pytest
import torch
from test_native_structured_model import ack, offer, snapshot, state_metadata
from test_native_structured_model import agent_files as agent_files

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.policy.native_agent import SESSION_SCHEMA, NativeStructuredAgent, serve


def message(kind, request, value, *, epoch=0, session="physical-session"):
    return json_bytes(
        {
            "schema": SESSION_SCHEMA,
            "message_type": kind,
            "session_id": session,
            "recovery_epoch": epoch,
            "request_id": request,
            "completion" if kind == "consume_ack" else "input": value,
        }
    ).decode()


def next_input(report):
    return {
        "continuity_token": "segment",
        "consumption_id": report["consumption_id"],
        "state_version": report["state_version"],
        "basis_acquisition_id": "acquisition-1",
        "received_cursor": "cursor",
    }


class ParentMessages(io.StringIO):
    """Parent sends only the child's actual returned identifiers, not invented ACKs."""

    def __init__(self, child, output, tail, *, acknowledge=True):
        super().__init__()
        self.child, self.output, self.tail = child, output, tail
        self.acknowledge = acknowledge
        self.position = 0
        self.report = None
        self.before = None

    def readline(self, size=-1):
        position = self.position
        self.position += 1
        if position == 0:
            observation, catalog = snapshot()
            return message("consume", "consume-1", offer(observation, catalog))
        if position == 1:
            self.report = json.loads(self.output.getvalue().splitlines()[-1])["completion"]
            if self.acknowledge:
                return message("consume_ack", "consume-1", ack(self.report))
        self.before = self.child.scorer.memory.clone() if self.before is None else self.before
        index = position - (2 if self.acknowledge else 1)
        if index < len(self.tail):
            return self.tail[index](self)
        return ""


def test_idle_same_session_epoch_advance_exports_actual_state_without_changing_w(agent_files):
    folder, path, _, _ = agent_files
    child, output = NativeStructuredAgent(folder, path), io.StringIO()
    parent = ParentMessages(
        child,
        output,
        [
            lambda p: message("next", "next-0", next_input(p.report)),
            lambda p: message(
                "export_state", "export-1", {"expected_metadata": state_metadata(child)}, epoch=1
            ),
            lambda p: message("next", "next-2", next_input(p.report), epoch=2),
        ],
    )
    assert serve(child, parent, output) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [reply["message_type"] for reply in replies] == [
        "ready",
        "consumed",
        "directive",
        "state_exported",
        "directive",
    ]
    assert [reply["recovery_epoch"] for reply in replies[1:]] == [0, 0, 1, 2]
    assert replies[2]["output"] == replies[4]["output"]
    assert replies[3]["output"]["metadata"]["state_version"] == 1
    assert child.scorer.state_version == 1 and torch.equal(parent.before, child.scorer.memory)


@pytest.mark.parametrize(
    "epoch,session,request_id,error",
    [
        (-1, "physical-session", "fresh", "message_session_or_size"),
        (2**53, "physical-session", "fresh", "message_session_or_size"),
        (True, "physical-session", "fresh", "message_session_or_size"),
        (1.0, "physical-session", "fresh", "message_session_or_size"),
        (1, "different-session", "fresh", "message_session_or_size"),
        (1, "physical-session", "consume-1", "request_reuse_or_capacity"),
    ],
)
def test_parent_fence_requires_same_session_safe_integer_and_fresh_request(
    agent_files,
    epoch,
    session,
    request_id,
    error,
):
    folder, path, _, _ = agent_files
    child, output = NativeStructuredAgent(folder, path), io.StringIO()
    parent = ParentMessages(
        child,
        output,
        [
            lambda p: message(
                "next", request_id, next_input(p.report), epoch=epoch, session=session
            ),
        ],
    )
    with pytest.raises(BoundaryError, match=error):
        serve(child, parent, output)
    assert child.scorer.state_version == 1 and torch.equal(parent.before, child.scorer.memory)


def test_epoch_regression_rejects_after_real_idle_epoch_advance(agent_files):
    folder, path, _, _ = agent_files
    child, output = NativeStructuredAgent(folder, path), io.StringIO()
    parent = ParentMessages(
        child,
        output,
        [
            lambda p: message("next", "next-2", next_input(p.report), epoch=2),
            lambda p: message("next", "stale-1", next_input(p.report), epoch=1),
        ],
    )
    with pytest.raises(BoundaryError, match="message_session_or_size"):
        serve(child, parent, output)
    assert child.scorer.state_version == 1 and torch.equal(parent.before, child.scorer.memory)


def test_maximum_safe_epoch_is_a_parent_fence_without_a_model_advance(agent_files):
    folder, path, _, _ = agent_files
    child, output = NativeStructuredAgent(folder, path), io.StringIO()
    parent = ParentMessages(
        child,
        output,
        [
            lambda p: message("next", "maximum-safe", next_input(p.report), epoch=2**53 - 1),
        ],
    )
    assert serve(child, parent, output) == 0
    assert json.loads(output.getvalue().splitlines()[-1])["recovery_epoch"] == 2**53 - 1
    assert child.scorer.state_version == 1 and torch.equal(parent.before, child.scorer.memory)


@pytest.mark.parametrize(
    "kind,error",
    [
        ("consume_ack", "consume_ack_request_binding"),
        ("next", "consume_ack_before_next_required"),
    ],
)
def test_new_epoch_cannot_ack_old_proposal_or_skip_pending_ack(agent_files, kind, error):
    folder, path, _, _ = agent_files
    child, output = NativeStructuredAgent(folder, path), io.StringIO()
    before = child.scorer.memory.clone()
    parent = ParentMessages(
        child,
        output,
        [
            lambda p: message(
                kind,
                "consume-1" if kind == "consume_ack" else "next-1",
                ack(p.report) if kind == "consume_ack" else next_input(p.report),
                epoch=1,
            ),
        ],
        acknowledge=False,
    )
    with pytest.raises(BoundaryError, match=error):
        serve(child, parent, output)
    assert child.scorer.state_version == 0 and child.scorer.pending is not None
    assert torch.equal(before, child.scorer.memory)
