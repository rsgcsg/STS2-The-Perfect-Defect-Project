"""Explicit v2 port binding over the real synthetic train/export fixture."""
from __future__ import annotations

import copy
import io
import json
from unittest.mock import patch

import pytest
from test_memory_v2_run_export import package as package
from test_memory_v2_run_export import two_cpu_threads as two_cpu_threads

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun.memory_token_inputs import project_memory_v2_snapshot
from stpd.memory_policy_installation import bind_memory_export, input_profile_for_config, validate
from stpd.policy.memory_port import PORT_SCHEMA, MemoryPolicyAdapter, serve


def _bind(package, tmp_path, monkeypatch):
    directory, exported, _, _, _, view = package
    root = tmp_path / "binding"
    root.mkdir()
    config_path, manifest_path = root / "config.json", root / "manifest.json"
    monkeypatch.setattr("stpd.memory_policy_installation.code_digest", lambda _: "b" * 64)
    snapshots = [item.snapshot for item in view.inputs]
    assert all(isinstance(item, dict) for item in snapshots)
    support = {"game_versions": ["synthetic"], "game_commits": ["synthetic"],
               "interaction_kinds": sorted({s["interaction"]["kind"] for s in snapshots}),
               "action_verbs": sorted({a["verb"] for s in snapshots
                                       for a in s["menu_actions"]["actions"]})}
    config, manifest = bind_memory_export(
        root, directory, config_path, manifest_path, input_profile="text-menu-v2",
        manifest_id="synthetic-v2-m2",
        policy={"id": "synthetic-m2", "version": "1", "provider": "test",
                "architecture": "stage1a.dsimple.m2.k1.experimental.v1"},
        requirements={"connector_protocol_version": "1.0.0",
            "environment": {"host_kind": "test", "connector_version": "test",
                "connector_source_revision": "source", "connector_artifact_sha256": "a" * 64,
                "connector_module_version_id": "mvid", "modset_status": "exact",
                "modset_fingerprint": "synthetic", "loaded_mod_ids": []},
            "reads": [], "candidate_order_digest": "sha256-json-menu-action-id-order",
            "whole_decision_admission": True, "score_count_matches_candidate_count": True,
            "selected_index": True, "successor_required": True}, support=support)
    assert validate(root, config_path, manifest_path) == (config, manifest)
    assert config["model_id"] == exported["ids"]["model"]
    assert config["schema"] == "stpd/m2-policy-config-v2"
    assert config["input_profile"] == "text-menu-v2"
    assert manifest["adapter"]["version"] == "2.0.0"
    assert manifest["representation"]["input_schema"] == (
        "sts2.player-environment/text-menu-snapshot-2")
    monkeypatch.setattr("stpd.policy.memory_port.ROOT", root)
    return config, manifest, config_path, manifest_path, view


def test_explicit_v2_port_reads_selection_once_and_binds_all_candidates(
    package, tmp_path, monkeypatch,
):
    _, manifest, config_path, manifest_path, view = _bind(package, tmp_path, monkeypatch)
    adapter = MemoryPolicyAdapter(config_path, manifest_path)
    with patch.object(adapter.scorer._model, "step", wraps=adapter.scorer._model.step) as step:
        for index, item in enumerate(view.inputs):
            snapshot = item.snapshot
            public = project_memory_v2_snapshot(snapshot)
            request = {"run_id": "run", "manifest": manifest,
                       "bundle": {"observation": snapshot, "reads": []},
                       "candidate_digest": public.candidate_digest,
                       "candidate_count": len(public.action_ids), "continuity_token": "episode-A"}
            bad = copy.deepcopy(request)
            bad["candidate_digest"] = "0" * 64
            with pytest.raises(BoundaryError, match="candidate_binding_mismatch"):
                adapter.decide(bad)
            assert step.call_count == index
            output, completion = adapter.decide(request)
            assert step.call_count == index + 1
            assert len(output["scores"]) == len(public.action_ids)
            assert output["candidate_digest"] == public.candidate_digest
            assert completion == {"continuity_token": "episode-A",
                                  "snapshot_id": snapshot["snapshot_id"],
                                  "sequence": snapshot["sequence"]}
            assert adapter.decide(request) == (output, completion)
            assert step.call_count == index + 1
    fresh = MemoryPolicyAdapter(config_path, manifest_path)
    destination = io.StringIO()
    first = view.inputs[0].snapshot
    public = project_memory_v2_snapshot(first)
    request["bundle"]["observation"] = first
    request.update(candidate_digest=public.candidate_digest, candidate_count=len(public.action_ids))
    line = {"schema": PORT_SCHEMA, "message_type": "decide", "request_id": "r",
            "input": request}
    assert serve(fresh, io.StringIO(json.dumps(line)+"\n"), destination) == 0
    ready, decision = map(json.loads, destination.getvalue().splitlines())
    assert ready["adapter"] == manifest["adapter"]
    assert decision["message_type"] == "decision" and decision["request_id"] == "r"
    assert decision["completion"]["snapshot_id"] == first["snapshot_id"]
    assert fresh.closed


def test_explicit_v2_binding_and_port_reject_cross_profile_before_memory_write(
    package, tmp_path, monkeypatch,
):
    config, manifest, config_path, manifest_path, view = _bind(package, tmp_path, monkeypatch)
    adapter = MemoryPolicyAdapter(config_path, manifest_path)
    sample = copy.deepcopy(view.inputs[0].snapshot)
    public = project_memory_v2_snapshot(sample)
    request = {"run_id": "run", "manifest": manifest,
               "bundle": {"observation": sample, "reads": []},
               "candidate_digest": public.candidate_digest,
               "candidate_count": len(public.action_ids), "continuity_token": "episode-A"}
    with patch.object(adapter.scorer._model, "step", wraps=adapter.scorer._model.step) as step:
        sample["input_profile"] = "text-menu-v1"
        with pytest.raises(BoundaryError):
            adapter.decide(request)
        assert step.call_count == 0
    changed = copy.deepcopy(manifest)
    changed["representation"]["input_schema"] = "sts2.player-environment/text-menu-snapshot-1"
    manifest_path.write_bytes(json_bytes(changed))
    with pytest.raises(BoundaryError, match="trusted_policy_identity_drift"):
        validate(config_path.parent, config_path, manifest_path)
    manifest_path.write_bytes(json_bytes(manifest))
    changed_config = {**config, "input_profile": "text-menu-v1"}
    config_path.write_bytes(json_bytes(changed_config))
    with pytest.raises(BoundaryError):
        validate(config_path.parent, config_path, manifest_path)


@pytest.mark.parametrize("change", [
    {"schema": "stpd/m2-policy-config-v3"},
    {"input_profile": "text-menu-v1"},
    {"input_profile": None},
    {"untrusted_profile_hint": "text-menu-v2"},
])
def test_binding_configuration_is_closed_before_loading_weights(change):
    config = {"schema": "stpd/m2-policy-config-v2", "input_profile": "text-menu-v2",
              "export_path": "/unused", "export_manifest_sha256": "a" * 64,
              "model_id": "b" * 64}
    assert input_profile_for_config(config) == "text-menu-v2"
    with pytest.raises(BoundaryError):
        input_profile_for_config({**config, **change})
