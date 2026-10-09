"""Actual M2 recurrence and duplex protocol over the shared synthetic contract.

These numerical tests need the lead's heavy slot. They exercise the scorer/Agent
call path directly; package admission/export is independently owned and tested
by the sampled Source3 exporter packet. No real data/train or game is used.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path

import pytest
import torch

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun.native_structured_sequences import qualify_native
from stpd.models.native_structured_scorer import NativeStructuredScorer
from stpd.models.structured_m2 import StructuredM2
from stpd.native_graph_spec import NativeGraphControl
from stpd.native_sampled_carry_spec import INPUT_SPEC, sampled_agent_spec
from stpd.policy.native_agent import NativeStructuredAgent, SESSION_SCHEMA, serve
from stpd.policy.native_structured_export import encode_native_weights

torch.set_num_threads(2)
torch.set_num_interop_threads(1)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "components/policy-runtime/contracts/fixtures/sampled-current-carry-v1.json").read_bytes())


@pytest.fixture
def agent() -> NativeStructuredAgent:
    torch.set_num_threads(2)
    model = StructuredM2(seed=7, model_control=NativeGraphControl())
    weights = hashlib.sha256(encode_native_weights(model)).hexdigest()
    scorer = NativeStructuredScorer(model, "test-model", weights, input_spec=INPUT_SPEC)
    # The package is deliberately outside this direct numerical protocol test.
    child = NativeStructuredAgent.__new__(NativeStructuredAgent)
    child.metadata = {"agent_spec": sampled_agent_spec(NativeGraphControl())}
    child.manifest = copy.deepcopy(FIXTURE["manifest"])
    child.scorer, child.sampled, child.ready_summary_task, child.closed = scorer, True, True, False
    child.issued_acquisition = None
    return child


def offer(name: str, previous: str | None) -> dict:
    frame = FIXTURE["frames"][name]
    return {"acquisition_id": "acq-" + name, "input_spec": INPUT_SPEC, "continuity_token": "segment-1",
            "previous_consumption_id": previous, "observation": copy.deepcopy(frame["observation"]),
            "catalog": copy.deepcopy(frame["catalog"])}


def ack(report: dict) -> dict:
    return {**{key: report[key] for key in ("consumption_id", "acquisition_id", "state_version", "advanced")},
            "prefix": {"continuity_token": "segment-1", "history_mode": "sampled_current",
                       "consumption_mode": "once_per_occurrence", "received_cursor": "cursor",
                       "consumed_publication_index": None,
                       "omissions": {"received_unconsumed_count": 0, "missing_scopes": [], "gap": None}}}


def next_input(child: NativeStructuredAgent) -> dict:
    return {"continuity_token": "segment-1", "consumption_id": child.scorer.consumption_id,
            "state_version": child.scorer.state_version, "basis_acquisition_id": child.scorer.acquisition_id,
            "received_cursor": "cursor"}


def test_three_advances_use_actual_carry_and_exact_ack_only(agent):
    scorer, model = agent.scorer, agent.scorer.model
    expected = model.initial_memory()
    states = []
    for version, name in enumerate(("map_a", "inspect_b", "map_c"), 1):
        value = offer(name, scorer.consumption_id)
        old = scorer.memory.clone()
        frame, _ = qualify_native(value["observation"], value["catalog"])
        with torch.inference_mode():
            expected = model.advance(model.encode(frame), expected)
        report = agent.consume(value)
        assert report["advanced"] is True and report["state_version"] == version
        assert torch.equal(scorer.memory, old) and scorer.state_version == version - 1
        with pytest.raises(BoundaryError, match="acknowledged_basis_required"):
            scorer.scores()
        wrong = ack(report); wrong["acquisition_id"] = "forged"
        with pytest.raises(BoundaryError, match="consume_ack_binding"):
            scorer.acknowledge(wrong)
        assert torch.equal(scorer.memory, old)
        scorer.acknowledge(ack(report))
        assert torch.equal(scorer.memory, expected)
        states.append(scorer.memory.clone())
        output = agent.next(next_input(agent))
        assert output["directive"]["basis_acquisition_id"] == value["acquisition_id"]
        assert output["directive"]["scores"]["catalog_digest"] == value["observation"]["catalog"]["digest"]
    assert not torch.equal(states[0], states[2])
    with pytest.raises(BoundaryError, match="sampled_readiness_check_is_not_consumption"):
        agent.consume(offer("map_c", scorer.consumption_id))
    assert scorer.state_version == 3 and torch.equal(scorer.memory, states[-1])
    with pytest.raises(BoundaryError, match="sampled_state_recovery_none"):
        scorer.state()


def wire(kind: str, request: str, value: dict) -> str:
    field = "completion" if kind == "consume_ack" else "result" if kind == "query_result" else "input"
    return json_bytes({"schema": SESSION_SCHEMA, "message_type": kind, "session_id": "physical-session",
                       "recovery_epoch": 0, "request_id": request, field: value}).decode("utf-8")


class Parent(io.StringIO):
    """Respond to actual child IDs and inspect staged W before the exact ACK."""
    def __init__(self, child, output, frames, *, cancel_after_proposal=False, forged_ack=False):
        super().__init__()
        self.child, self.output, self.frames = child, output, iter(frames)
        self.cancel, self.forged = cancel_after_proposal, forged_ack
        self.next_serial = 0
        self.last_read = 0
        self.next_pending = False
        self.before = child.scorer.memory.clone()
        self.advances = 0

    def readline(self, size=-1):
        replies = [json.loads(line) for line in self.output.getvalue().splitlines()]
        last = replies[-1]
        if last["message_type"] in ("ready", "directive"):
            try: self.frame = next(self.frames)
            except StopIteration: return ""
            self.before = self.child.scorer.memory.clone()
            self.next_serial += 1
            return wire("next", "N" + str(self.next_serial), next_input(self.child))
        if last["message_type"] == "query":
            assert last["request_id"].startswith("child-")
            assert last["input"] == {"method": "current", "arguments": {
                "eager_scope": ["persistent", "interaction", "referents", "catalog"], "expected_snapshot_id": None}}
            frame = copy.deepcopy(FIXTURE["frames"][self.frame])
            return wire("query_result", last["request_id"], {"method": "current", "acquisition_id": "acq-" + self.frame,
                "value": {key: frame[key] for key in ("capture", "observation", "catalog")} | {"catalog_materialized": True}})
        if last["message_type"] == "consumed":
            assert self.child.scorer.pending is not None
            assert torch.equal(self.before, self.child.scorer.memory)
            self.advances += 1
            if self.cancel: return ""
            acknowledgement = ack(last["completion"])
            if self.forged: acknowledgement["state_version"] += 1
            return wire("consume_ack", last["request_id"], acknowledgement)
        raise AssertionError(last)


def test_actual_python_duplex_revisits_and_unchanged_query_keep_one_sample_rule(agent):
    output = io.StringIO()
    parent = Parent(agent, output, ["map_a", "map_a", "inspect_b", "map_c", "map_c"])
    assert serve(agent, parent, output) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert parent.advances == 3 and agent.scorer.state_version == 3
    assert [r["output"]["directive"]["type"] for r in replies if r["message_type"] == "directive"] == [
        "act", "await", "act", "act", "await"]
    assert all(r["output"]["directive"].get("timeout_ms", 250) == 250
               for r in replies if r["message_type"] == "directive")
    for r in replies:
        if r["message_type"] == "directive": assert r["request_id"].startswith("N")
        elif r["message_type"] in ("query", "consumed"): assert r["request_id"].startswith("child-")


def test_actual_python_empty_wait_and_unlabelled_summary_ack_before_close(agent):
    output = io.StringIO(); parent = Parent(agent, output, ["empty_wait", "ready_summary"])
    assert serve(agent, parent, output) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert parent.advances == 1 and agent.scorer.state_version == 1
    assert [r["output"]["directive"]["type"] for r in replies if r["message_type"] == "directive"] == ["await", "close"]
    assert not agent.scorer.frame.candidates


@pytest.mark.parametrize("forged", [False, True])
def test_interrupted_or_wrong_ack_preserves_original_uncommitted_w(agent, forged):
    output = io.StringIO(); parent = Parent(agent, output, ["map_a"], cancel_after_proposal=not forged, forged_ack=forged)
    initial = agent.scorer.memory.clone()
    if forged:
        with pytest.raises(BoundaryError, match="consume_ack_binding"):
            serve(agent, parent, output)
    else:
        assert serve(agent, parent, output) == 0
    assert agent.scorer.state_version == 0 and torch.equal(agent.scorer.memory, initial)
    assert agent.scorer.pending is not None
    with pytest.raises(BoundaryError, match="sampled_next_prefix_binding"):
        agent.begin_sampled_next(next_input(agent))
