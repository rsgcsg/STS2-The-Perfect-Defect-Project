"""Source-shaped natural-summary task conformance; no native runtime claim.

The new Source3 package writer owns its concrete loader/schema. Composition
tests inject that explicitly loaded descriptor; legacy packages use the actual
existing exporter/loader, proving their generic Next behavior stays unchanged.
"""

from __future__ import annotations

import copy
import io
import json

import pytest
import torch
from test_native_agent_epochs import ParentMessages, message
from test_native_structured_model import ack, offer, snapshot, state_metadata
from test_native_structured_model import agent_files as agent_files

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.policy import native_agent
from stpd.policy.native_agent import NativeStructuredAgent, serve
from stpd.policy.native_structured_export import load_native_package
from stpd.policy.native_task import observe_ready_summary, ready_summary_task_spec


def terminal_snapshot(*, stage="summary", outcome="loss", empty=False):
    observation, catalog = snapshot(empty=empty)
    observation["status"] = "settling" if "animating" in stage else "interactive"
    observation["interaction"].update(kind="game_over", stage=stage)
    observation["interaction"]["content"] = {
        "context": {
            "kind": "game_over", "result": outcome, "game_mode": "standard",
            "score": 15 if stage == "summary" else None,
            "floor_reached": 1 if stage == "summary" else None,
            "ascension": 0 if stage == "summary" else None,
        },
        "surface": {
            "kind": "game_over", "stage": stage,
            "return_destination": "main_menu" if stage == "summary" else None,
            "can_advance_summary": stage == "intro",
            "can_return": stage == "summary", "other_controls": [],
        },
    }
    return observation, catalog


def next_input(agent):
    return {
        "continuity_token": agent.scorer.continuity,
        "consumption_id": agent.scorer.consumption_id,
        "state_version": agent.scorer.state_version,
        "basis_acquisition_id": agent.scorer.acquisition_id,
        "received_cursor": "known-cursor",
    }


def composed_agent(files, monkeypatch, *, task=True, spec=None, version="1.2.0"):
    folder, path, _, manifest = files
    manifest = copy.deepcopy(manifest)
    manifest["support"]["interaction_kinds"] = ["*"]
    if task:
        loaded_metadata, model = load_native_package(folder)
        loaded_metadata = copy.deepcopy(loaded_metadata)
        loaded_metadata["agent_spec"].update(
            version=version, task_spec=ready_summary_task_spec() if spec is None else spec
        )
        manifest["agent"]["version"] = version
        monkeypatch.setattr(native_agent, "load_native_package",
                            lambda _, **kwargs: (loaded_metadata, model))
    path.write_bytes(json_bytes(manifest))
    return NativeStructuredAgent(folder, path)


@pytest.mark.parametrize("outcome", ["win", "loss"])
@pytest.mark.parametrize("destination", ["main_menu", "timeline"])
def test_ready_public_summary_is_task_complete_without_erasing_nonempty_catalog(
    outcome, destination
):
    observation, catalog = terminal_snapshot(outcome=outcome)
    observation["interaction"]["content"]["surface"]["return_destination"] = destination
    original = copy.deepcopy((observation, catalog))
    observed = observe_ready_summary(observation)
    assert observed.game_outcome_known and observed.outcome == outcome
    assert observed.agent_task_complete
    assert (observation, catalog) == original and len(catalog) == 2


@pytest.mark.parametrize("stage", ["intro", "intro_animating", "summary_animating"])
def test_known_natural_outcome_is_distinct_from_summary_task_completion(stage):
    observation, _ = terminal_snapshot(stage=stage)
    observation["status"] = "settling" if "animating" in stage else "interactive"
    observed = observe_ready_summary(observation)
    assert observed.game_outcome_known and observed.outcome == "loss"
    assert not observed.agent_task_complete


@pytest.mark.parametrize("mutation", [
    lambda o: o.update(status="settling"),
    lambda o: o["interaction"].update(stage="intro"),
    lambda o: o["interaction"]["content"]["surface"].update(can_return=False),
    lambda o: o["interaction"]["content"]["surface"].update(can_return=1),
    lambda o: o["interaction"]["content"]["surface"].update(can_advance_summary=True),
    lambda o: o["interaction"]["content"]["surface"].update(return_destination="unknown"),
    lambda o: o["interaction"]["content"]["context"].update(score=None),
    lambda o: o["interaction"]["content"]["context"].update(floor_reached=None),
    lambda o: o["interaction"]["content"]["context"].update(ascension=True),
    lambda o: o["interaction"]["content"]["context"].update(result="unknown"),
    lambda o: o["interaction"]["content"]["context"].update(game_mode="abandoned"),
    lambda o: o["completeness"].update(missing=["interaction"]),
    lambda o: o["completeness"].update(full_reference_complete=False),
])
def test_incomplete_or_ambiguous_summary_cannot_complete_task(mutation):
    observation, _ = terminal_snapshot()
    mutation(observation)
    assert not observe_ready_summary(observation).agent_task_complete


def test_status_hp_process_exit_or_future_labels_are_not_terminal_facts():
    observation, _ = snapshot(empty=True)
    observation.update(status="terminal", game_outcome="win", process_exit=0)
    observation["persistent"]["content"]["hp"] = 0
    observed = observe_ready_summary(observation)
    assert not observed.game_outcome_known and not observed.agent_task_complete


def test_task_descriptor_is_fresh_and_separates_menu_return_and_censoring():
    spec = ready_summary_task_spec()
    assert not spec["return_to_menu_required"] and not spec["timing_learned"]
    assert set(spec["censoring"]) >= {"budget", "disconnect", "unknown_delivery", "source_gap"}
    spec["administrative_actions"].append("reset")
    assert ready_summary_task_spec()["administrative_actions"] == []


def test_explicit_task_closes_only_exact_acked_basis_without_scoring_or_advancing_w(
    agent_files, monkeypatch
):
    agent = composed_agent(agent_files, monkeypatch)
    observation, catalog = terminal_snapshot()
    report = agent.consume(offer(observation, catalog))
    with pytest.raises(BoundaryError, match="acknowledged_next_basis_required"):
        agent.next(next_input(agent))
    agent.scorer.acknowledge(ack(report))
    before = agent.scorer.memory.clone()
    monkeypatch.setattr(agent.scorer, "scores", lambda: pytest.fail("ready task must not score"))
    output = agent.next(next_input(agent))
    assert output["directive"] == {
        "type": "close", "reason": "native_ready_summary_task_complete",
    }
    assert agent.scorer.input["catalog"] == catalog and len(agent.scorer.frame.action_ids) == 2
    assert torch.equal(before, agent.scorer.memory) and agent.scorer.state_version == 1
    stale = next_input(agent) | {"basis_acquisition_id": "different-basis"}
    with pytest.raises(BoundaryError, match="acknowledged_next_basis_required"):
        agent.next(stale)


@pytest.mark.parametrize("task", [False, True])
def test_legacy_generic_agent_scores_summary_and_new_task_still_scores_intro(
    agent_files, monkeypatch, task
):
    agent = composed_agent(agent_files, monkeypatch, task=task)
    observation, catalog = terminal_snapshot(stage="intro" if task else "summary")
    agent.scorer.acknowledge(ack(agent.consume(offer(observation, catalog))))
    output = agent.next(next_input(agent))
    assert output["directive"]["type"] == "act"
    assert len(output["directive"]["scores"]["values"]) == len(catalog)


def test_stateless_task_restores_exact_terminal_input_and_close_behavior(agent_files, monkeypatch):
    agent = composed_agent(agent_files, monkeypatch)
    observation, catalog = terminal_snapshot()
    agent.scorer.acknowledge(ack(agent.consume(offer(observation, catalog))))
    metadata = state_metadata(agent)
    exported = agent.export_state(metadata)
    restored = composed_agent(agent_files, monkeypatch)
    assert restored.restore_state(metadata, exported) == {"metadata": metadata}
    assert restored.next(next_input(restored)) == agent.next(next_input(agent))
    assert restored.scorer.input["observation"] == observation


def test_required_gap_ack_cannot_turn_a_terminal_offer_into_completion(agent_files, monkeypatch):
    agent = composed_agent(agent_files, monkeypatch)
    observation, catalog = terminal_snapshot()
    report = agent.consume(offer(observation, catalog))
    acknowledgement = ack(report)
    acknowledgement["prefix"]["omissions"]["gap"] = {"reason": "source_gap"}
    with pytest.raises(BoundaryError, match="qualified_ack_prefix_required"):
        agent.scorer.acknowledge(acknowledgement)
    with pytest.raises(BoundaryError, match="acknowledged_next_basis_required"):
        agent.next(next_input(agent))


@pytest.mark.parametrize("spec,version", [
    (ready_summary_task_spec() | {"return_to_menu_required": True}, "1.2.0"),
    (ready_summary_task_spec() | {"completion": "process_exit"}, "1.2.0"),
    (ready_summary_task_spec(), "1.1.0"),
])
def test_task_definition_and_agent_version_cannot_silently_change(
    agent_files, monkeypatch, spec, version
):
    with pytest.raises(BoundaryError, match="unsupported_task_spec"):
        composed_agent(agent_files, monkeypatch, spec=spec, version=version)


def test_stdio_consume_ack_next_exposes_explicit_close_and_original_catalog(
    agent_files, monkeypatch
):
    agent = composed_agent(agent_files, monkeypatch)
    output = io.StringIO()
    observation, catalog = terminal_snapshot()

    class TerminalParent(ParentMessages):
        def readline(self, size=-1):
            if self.position == 0:
                self.position += 1
                return message("consume", "consume-1", offer(observation, catalog))
            return super().readline(size)

    parent = TerminalParent(agent, output, [
        lambda p: message("next", "next-1", next_input(agent)),
    ])
    assert serve(agent, parent, output) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [reply["message_type"] for reply in replies] == ["ready", "consumed", "directive"]
    assert replies[-1]["output"]["directive"] == {
        "type": "close", "reason": "native_ready_summary_task_complete",
    }
    assert agent.scorer.input["catalog"] == catalog
