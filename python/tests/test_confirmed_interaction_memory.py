"""Confirmed interaction history is source ordered and uses the frozen menu."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
from test_artifact_store_v1 import PRODUCER, store
from test_managed_text_menu_import import _archive
from test_memory_scorer import exported as exported
from test_memory_scorer import page, tokenizer
from test_memory_sequence_bridge import observed, view

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.confirmed_interaction import (
    HISTORY_INPUT_PROFILE,
    V2_HISTORY_INPUT_PROFILE,
    confirmed_action_text,
)
from stpd.fullrun.managed_text_menu_import import import_managed_text_menu_report
from stpd.fullrun.memory_projection_config import history_episode_projection_config
from stpd.fullrun.memory_sequence_bridge import project_memory_episodes
from stpd.fullrun.memory_token_inputs import encode_memory_texts, project_memory_profile_snapshot
from stpd.fullrun.memory_training_prepare import fit_observed_memory_tokenizer
from stpd.fullrun.observed_input_sequence import (
    ObservedInputView,
    _agent_view,
    load_observed_input_view,
)
from stpd.memory_policy_installation import bind_memory_export, validate
from stpd.models.dsimple_memory import ExperimentalDSimpleM2
from stpd.models.token_core import ScratchShape, ScratchTokenCore
from stpd.policy.memory_export import (
    export_memory_package,
    validate_memory_package,
    verify_memory_package,
)
from stpd.policy.memory_port import HISTORY_PORT_SCHEMA, MemoryPolicyAdapter, serve
from stpd.policy.memory_scorer import OnlineM2Scorer
from stpd.workers.memory_ranking import MemoryConfig, load_memory_export
from stpd.workers.memory_run import execute_memory_run, prepare_observed_memory_run


@pytest.fixture(autouse=True)
def one_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def _model() -> ExperimentalDSimpleM2:
    return ExperimentalDSimpleM2(ScratchTokenCore(ScratchShape(32, 8, 1, 2, 16, 0.0, 512)))


def _project(items):
    result = project_memory_episodes(
        view(*items), tokenizer(), _model(), max_observations=8,
        max_input_tokens=100000, max_settling_events=64,
        projection_config=history_episode_projection_config(HISTORY_INPUT_PROFILE))
    assert not result.diagnostics
    return result.episodes[0].steps


def test_nested_human_capture_append_watermark_and_single_slot_retirement():
    items = (
        replace(observed("a", 1, reset=True, action="opaque-play"),
                confirmed_at_sequence=8, confirmed_effect_domain="native_input",
                capture_ordinal=1, physical_sequence=8, completed_append_watermark=0),
        replace(observed("b", 2, action="opaque-nav"),
                confirmed_at_sequence=4, confirmed_effect_domain="text_menu",
                capture_ordinal=2, physical_sequence=4, completed_append_watermark=0),
        replace(observed("c", 3), capture_ordinal=3,
                physical_sequence=10, completed_append_watermark=5),
        replace(observed("d", 4), capture_ordinal=4,
                physical_sequence=11, completed_append_watermark=9),
        replace(observed("e", 5), capture_ordinal=5,
                physical_sequence=12, completed_append_watermark=12),
    )
    steps = _project(items)
    assert [step.previous_actual_action is None for step in steps] == [
        True, True, False, False, True]
    token = tokenizer()
    for position, source in ((2, items[1]), (3, items[0])):
        text = confirmed_action_text(
            source.snapshot, source.selected_action_id,
            effect_domain=source.confirmed_effect_domain,
            basis="last_known_human_input_witness", profile=HISTORY_INPUT_PROFILE)
        assert steps[position].previous_actual_action.tolist() == token.encode(text).ids
    # The current row's label never becomes its own previous interaction.
    assert steps[0].previous_actual_action is None


def test_row1_and_agent_late_result_never_use_future_or_unconfirmed_choice():
    assert all(step.previous_actual_action is None for step in _project((
        observed("legacy-a", 1, reset=True), observed("legacy-b", 2))))
    first = replace(observed("agent-a", 1, reset=True),
                    stream_id="agent:run", source_kind="agent_decision_inputs",
                    confirmed_at_sequence=7, confirmed_effect_domain="native_input")
    middle = replace(observed("agent-b", 5, action=None),
                     stream_id="agent:run", source_kind="agent_decision_inputs")
    last = replace(observed("agent-c", 9),
                   stream_id="agent:run", source_kind="agent_decision_inputs")
    agent = ObservedInputView("agent-source", "verified_agent_observed_inputs", False,
                              (first, middle, last))
    result = project_memory_episodes(
        agent, tokenizer(), _model(), max_observations=8, max_input_tokens=100000,
        max_settling_events=64,
        projection_config=history_episode_projection_config(HISTORY_INPUT_PROFILE))
    assert not result.diagnostics
    assert [step.previous_actual_action is None for step in result.episodes[0].steps] == [
        True, True, False]


def test_cancelled_dispatch_is_not_unknown_or_confirmed(monkeypatch):
    events = (
        {"sequence": 1, "kind": "text_decision_input",
         "payload": {"decision_id": "decision", "snapshot": page("cancel", 1)}},
        {"sequence": 2, "kind": "decision",
         "payload": {"decision": {"decision_id": "decision",
                                  "resolved_bound_action_id": "opaque-nav"},
                     "resolved_bound_action_id": "opaque-nav"}},
        {"sequence": 3, "kind": "text_menu_dispatch_attempt",
         "payload": {"decision_id": "decision"}},
        {"sequence": 4, "kind": "text_menu_dispatch_cancelled",
         "payload": {"decision_id": "decision", "reason": "recovery_before_submit"}},
    )
    monkeypatch.setattr("stpd.fullrun.observed_input_sequence.load_verified_agent_run_events",
                        lambda *_: SimpleNamespace(events=events, content_id="content",
                                                   run_id="run"))
    observed_view = _agent_view(None, "source")
    item = observed_view.inputs[0]
    assert item.selected_action_id == "opaque-nav"
    assert item.delivery_status == "not_attempted"
    assert item.confirmed_at_sequence is None
    assert [ref.kind for ref in item.source_events] == [
        "text_decision_input", "decision", "text_menu_dispatch_attempt",
        "text_menu_dispatch_cancelled"]


def test_online_offline_three_step_tokens_memory_scores_and_binding(exported):
    weights, config, token_bytes = exported
    offline = load_memory_export(weights, config, hashlib.sha256(token_bytes).hexdigest()).eval()
    online = OnlineM2Scorer.from_export(
        weights, config, token_bytes, input_profile=HISTORY_INPUT_PROFILE)
    token = tokenizer()
    memory = offline.initial_memory()
    pages = [page(f"history-{index}", index) for index in (1, 2, 3)]
    prior = None
    with torch.inference_mode():
        for index, snapshot in enumerate(pages):
            public = project_memory_profile_snapshot(snapshot, HISTORY_INPUT_PROFILE)
            interaction = None
            previous_tokens = None
            if prior is not None:
                prior_page, prior_public = prior
                interaction = {
                    "decision_id": f"decision-{index}",
                    "snapshot_id": prior_page["snapshot_id"],
                    "candidate_digest": prior_public.candidate_digest,
                    "action_id": prior_public.action_ids[0],
                    "request_id": f"request-{index}",
                    "effect_domain": "text_menu", "result_kind": "menu_applied",
                }
                text = confirmed_action_text(
                    prior_page, interaction["action_id"], effect_domain="text_menu",
                    basis="confirmed_connector_result", profile=HISTORY_INPUT_PROFILE)
                previous_tokens = torch.tensor(token.encode(text).ids)
            row = encode_memory_texts(
                token, public.state_text, public.action_texts,
                max_tokens=config.max_tokens, slots=config.slots,
                previous_actual_action=previous_tokens is not None)
            actual = online.observe_and_score(
                continuity_token="continuity", snapshot_bytes=json_bytes(snapshot),
                expected_candidate_digest=public.candidate_digest,
                expected_candidate_count=len(public.action_ids),
                previous_interaction=interaction)
            scores, memory = offline.step(
                torch.tensor(row.state),
                tuple(torch.tensor(action) for action in row.actions), memory,
                previous_actual_action=previous_tokens, reset_before=index == 0)
            assert actual.scores == tuple(float(score) for score in scores.tolist())
            torch.testing.assert_close(online._memory, memory)
            prior = snapshot, public
    with pytest.raises(BoundaryError, match="history_binding_mismatch"):
        online.observe_and_score(continuity_token="continuity",
                                 snapshot_bytes=json_bytes(pages[-1]),
                                 previous_interaction=interaction)
    changed = dict(interaction, request_id="new-request", candidate_digest="0" * 64)
    with pytest.raises(BoundaryError, match="history_binding_mismatch"):
        online.observe_and_score(continuity_token="continuity",
                                 snapshot_bytes=json_bytes(pages[-1]),
                                 previous_interaction=changed)


def test_same_snapshot_new_history_writes_once_and_reordered_menu_cannot_bind(exported):
    weights, config, token_bytes = exported
    online = OnlineM2Scorer.from_export(
        weights, config, token_bytes, input_profile=HISTORY_INPUT_PROFILE)
    snapshot = page("same", 1)
    public = project_memory_profile_snapshot(snapshot, HISTORY_INPUT_PROFILE)
    with patch.object(online._model, "step", wraps=online._model.step) as step:
        first = online.observe_and_score(
            continuity_token="same", snapshot_bytes=json_bytes(snapshot))
        interaction = {"decision_id": "decision", "snapshot_id": snapshot["snapshot_id"],
                       "candidate_digest": public.candidate_digest,
                       "action_id": "opaque-nav", "request_id": "request",
                       "effect_domain": "text_menu", "result_kind": "menu_applied"}
        second = online.observe_and_score(
            continuity_token="same", snapshot_bytes=json_bytes(snapshot),
            previous_interaction=interaction)
        assert step.call_count == 2
        assert second.action_ids == first.action_ids
        assert online.observe_and_score(
            continuity_token="same", snapshot_bytes=json_bytes(snapshot)) == second
        assert step.call_count == 2
    reordered = page("reordered", 2)
    reordered["menu_actions"]["actions"].reverse()
    wrong = dict(interaction, request_id="new",
                 candidate_digest=project_memory_profile_snapshot(
                     reordered, HISTORY_INPUT_PROFILE).candidate_digest,
                 action_id="opaque-play",
                 effect_domain="native_input", result_kind="native_input_delivered")
    with pytest.raises(BoundaryError, match="history_binding_mismatch"):
        online.observe_and_score(continuity_token="same",
                                 snapshot_bytes=json_bytes(reordered),
                                 previous_interaction=wrong)


def test_history_v2_checkpoint_package_and_old_profile_rejection(tmp_path: Path,
                                                                 monkeypatch):
    archive, report_id, expected = _archive(tmp_path)
    research = store(tmp_path / "research")
    source = import_managed_text_menu_report(
        archive, research, report_id, PRODUCER, expected=expected)
    view_ = load_observed_input_view(research, source.manifest.artifact_id)
    tokens, count = fit_observed_memory_tokenizer(
        view_, max_settling_events=0, input_profile=V2_HISTORY_INPUT_PROFILE)
    from tokenizers import Tokenizer

    config = MemoryConfig(
        vocab_size=Tokenizer.from_str(tokens.decode()).get_vocab_size(),
        episode_count=count, max_tokens=4096, max_episode_observations=8,
        max_episode_input_tokens=65536, max_total_input_tokens=65536,
        max_chunk_steps=2, max_chunk_input_tokens=65536, cpu_threads=1)
    run = prepare_observed_memory_run(
        research, source.manifest.artifact_id, config, PRODUCER, tokens,
        max_settling_events=0, input_profile=V2_HISTORY_INPUT_PROFILE,
        reject_diagnostics=True)
    reporter = ObjectStoreRunReporter(research, research.blobs)
    outcome = execute_memory_run(research, reporter, run.artifact_id, PRODUCER)
    assert outcome.state == "completed"
    directory = tmp_path / "history-package"
    package = export_memory_package(research, reporter, run.artifact_id, directory)
    assert package["renderer"]["id"] == "stpd/m2-confirmed-interaction-v2"
    assert validate_memory_package(directory, input_profile=V2_HISTORY_INPUT_PROFILE)[0] == package
    assert verify_memory_package(
        research, reporter, package["ids"]["model"], directory,
        input_profile=V2_HISTORY_INPUT_PROFILE) == package
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        validate_memory_package(directory, input_profile="text-menu-v2")
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        validate_memory_package(directory)

    monkeypatch.setattr("stpd.memory_policy_installation.code_digest", lambda _: "b" * 64)
    monkeypatch.setattr("stpd.policy.memory_port.ROOT", tmp_path)
    bindings = tmp_path / "bindings"
    bindings.mkdir()
    config_path, manifest_path = bindings / "config.json", bindings / "manifest.json"
    snapshots = [item.snapshot for item in view_.inputs]
    support = {"game_versions": ["synthetic"], "game_commits": ["synthetic"],
               "interaction_kinds": sorted({s["interaction"]["kind"] for s in snapshots}),
               "action_verbs": sorted({a["verb"] for s in snapshots
                                       for a in s["menu_actions"]["actions"]})}
    config_binding, manifest = bind_memory_export(
        tmp_path, directory, config_path, manifest_path, binding_root=bindings,
        input_profile=V2_HISTORY_INPUT_PROFILE, manifest_id="history-port3",
        policy={"id": "history-port3", "version": "1", "provider": "test",
                "architecture": "stage1a.dsimple.m2.k1.confirmed-interaction.v2"},
        requirements={"connector_protocol_version": "1.0.0",
            "environment": {"host_kind": "test", "connector_version": "test",
                "connector_source_revision": "source", "connector_artifact_sha256": "a" * 64,
                "connector_module_version_id": "mvid", "modset_status": "exact",
                "modset_fingerprint": "synthetic", "loaded_mod_ids": []},
            "reads": [], "candidate_order_digest": "sha256-json-menu-action-id-order",
            "whole_decision_admission": True, "score_count_matches_candidate_count": True,
            "selected_index": True, "successor_required": True}, support=support)
    assert validate(tmp_path, config_path, manifest_path, binding_root=bindings) == (
        config_binding, manifest)
    assert manifest["adapter"]["protocol"] == "sts2.policy-runtime/decision-only-ndjson-3"
    adapter = MemoryPolicyAdapter(config_path, manifest_path, binding_root=bindings)
    assert adapter.port_schema == HISTORY_PORT_SCHEMA
    first, second = snapshots[:2]
    first_public = project_memory_profile_snapshot(first, V2_HISTORY_INPUT_PROFILE)
    second_public = project_memory_profile_snapshot(second, V2_HISTORY_INPUT_PROFILE)
    request = {"run_id": "run", "manifest": manifest,
               "bundle": {"observation": first, "reads": []},
               "candidate_digest": first_public.candidate_digest,
               "candidate_count": len(first_public.action_ids),
               "continuity_token": "continuity", "previous_interaction": None}
    _, completion = adapter.decide(request)
    assert completion["previous_interaction_request_id"] is None
    chosen = view_.inputs[0].selected_action_id
    action = next(a for a in first["menu_actions"]["actions"] if a["action_id"] == chosen)
    interaction = {"decision_id": "decision-1", "snapshot_id": first["snapshot_id"],
                   "candidate_digest": first_public.candidate_digest, "action_id": chosen,
                   "request_id": "request-1", "effect_domain": action["effect_domain"],
                   "result_kind": ("menu_applied" if action["effect_domain"] == "text_menu"
                                   else "native_input_delivered")}
    request.update(bundle={"observation": second, "reads": []},
                   candidate_digest=second_public.candidate_digest,
                   candidate_count=len(second_public.action_ids),
                   previous_interaction=interaction)
    old_memory = adapter.scorer._memory.clone()
    with pytest.raises(BoundaryError, match="history_binding_mismatch"):
        adapter.decide({**request, "previous_interaction": dict(
            interaction, candidate_digest="0" * 64)})
    torch.testing.assert_close(adapter.scorer._memory, old_memory)
    _, completion = adapter.decide(request)
    assert completion["previous_interaction_request_id"] == "request-1"
    with pytest.raises(BoundaryError, match="history_binding_mismatch"):
        adapter.decide(request)
    stream = io.StringIO()
    fresh = MemoryPolicyAdapter(config_path, manifest_path, binding_root=bindings)
    first_request = {**request, "bundle": {"observation": first, "reads": []},
                     "candidate_digest": first_public.candidate_digest,
                     "candidate_count": len(first_public.action_ids),
                     "previous_interaction": None}
    line = {"schema": HISTORY_PORT_SCHEMA, "message_type": "decide",
            "request_id": "port-request", "input": first_request}
    assert serve(fresh, io.StringIO(json.dumps(line) + "\n"), stream) == 0
    ready, decision = map(json.loads, stream.getvalue().splitlines())
    assert ready["schema"] == decision["schema"] == HISTORY_PORT_SCHEMA
    assert decision["completion"]["previous_interaction_request_id"] is None
