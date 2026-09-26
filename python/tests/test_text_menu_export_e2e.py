"""Synthetic text-menu export/install seam; no Human or game qualification."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest
import torch
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import row

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.text_menu_data import publish_text_menu_bc_view, publish_text_menu_source
from stpd.fullrun.token_inputs import load_token_inputs, publish_token_inputs
from stpd.policy.token_decision import TokenDecisionScorer, export_token_model
from stpd.policy.token_port import ROOT, TokenPolicyAdapter
from stpd.token_policy_installation import bind_text_menu_export
from stpd.workers.token_ranking import TokenConfig
from stpd.workers.token_worker import execute_tokens, prepare_token_run


def test_synthetic_source_to_real_export_and_installed_adapter(tmp_path):
    torch.set_num_threads(2)
    archive = store(tmp_path / "artifacts")
    first, second = row("one"), row("two", native=True)
    source = publish_text_menu_source(archive, (first, second), PRODUCER)
    view = publish_text_menu_bc_view(archive, source.artifact_id, PRODUCER)
    tokens = publish_token_inputs(archive, view.artifact_id, "s", PRODUCER, max_tokens=4096)
    inputs = load_token_inputs(archive, tokens.artifact_id)
    assert len(inputs.samples) == 2 and {item.split for item in inputs.samples} == {"train", "dev"}
    config = TokenConfig(recipe="stage1a.b.s.v2", steps=2, width=16, heads=2,
                         layers=1, feedforward=32, dropout=0.0, max_tokens=4096)
    run = prepare_token_run(archive, inputs, config, PRODUCER)
    reporter = ObjectStoreRunReporter(archive, archive.blobs)
    paused = execute_tokens(archive, reporter, run.artifact_id, PRODUCER, stop_after=1)
    assert paused.state == "paused" and paused.checkpoint_id
    completed = execute_tokens(archive, reporter, run.artifact_id, PRODUCER,
                               resume=paused.checkpoint_id)
    assert completed.state == "completed" and completed.result_id
    model_id = archive.get_manifest(completed.result_id).parent("model")
    destination = tmp_path / "export"
    export_token_model(archive, model_id, destination)
    scorer = TokenDecisionScorer(destination)
    observation = first["snapshot"]
    score_map = scorer.score_snapshot(observation)
    assert tuple(score_map) == tuple(action["action_id"] for action in
                                    observation["menu_actions"]["actions"])

    # Caller supplies test-Host identity and exact support. The production
    # metadata builder derives only code/model/serializer pins, never these facts.
    requirements = {
        "connector_protocol_version": "1.0.0",
        "environment": {"host_kind": "test", "connector_version": "test-connector",
                        "connector_source_revision": "test-source",
                        "connector_artifact_sha256": "1" * 64,
                        "connector_module_version_id": "test-module",
                        "modset_status": "known", "modset_fingerprint": "test-modset",
                        "loaded_mod_ids": []},
        "reads": [], "whole_decision_admission": True,
        "candidate_order_digest": "sha256-json-menu-action-id-order",
        "score_count_matches_candidate_count": True, "selected_index": True,
        "successor_required": True,
    }
    support = {"game_versions": ["test-game"], "game_commits": ["test-commit"],
               "interaction_kinds": ["combat_turn"],
               "action_verbs": ["open_information", "play"]}
    policy = {"id": "synthetic-text-menu", "version": "1.0.0",
              "provider": "stpd", "architecture": "stage1a.b.s.v2"}
    local_root = ROOT / ".local"
    local_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="text-menu-adapter-", dir=local_root) as name:
        folder = Path(name)
        config_path, manifest_path = folder / "config.json", folder / "manifest.json"
        for wrong in ({**requirements, "candidate_order_digest":
                       "sha256-json-bound-action-id-order"},
                      {**requirements, "successor_required": False}):
            with pytest.raises(BoundaryError, match="text_menu_runtime_requirements_mismatch"):
                bind_text_menu_export(
                    ROOT, destination, config_path, manifest_path,
                    manifest_id="synthetic-text-menu-1", policy=policy,
                    requirements=wrong, support=support,
                )
            assert not config_path.exists() and not manifest_path.exists()
        _, manifest = bind_text_menu_export(
            ROOT, destination, config_path, manifest_path, manifest_id="synthetic-text-menu-1",
            policy=policy, requirements=requirements, support=support,
        )
        adapter = TokenPolicyAdapter(config_path, manifest_path)
        keys = list(score_map)
        digest = hashlib.sha256(canonical_json(keys).encode()).hexdigest()
        request = {"run_id": "synthetic-run", "manifest": manifest,
                   "bundle": {"observation": observation, "reads": []},
                   "candidate_count": len(keys), "candidate_digest": digest}
        decision = adapter.decide(request)
        assert decision["candidate_digest"] == digest
        assert decision["scores"] == list(score_map.values())
        assert decision["selected_index"] == max(
            range(len(keys)), key=decision["scores"].__getitem__)
        request["candidate_digest"] = "0" * 64
        with pytest.raises(BoundaryError, match="candidate_binding_mismatch"):
            adapter.decide(request)
        adapter.close()
