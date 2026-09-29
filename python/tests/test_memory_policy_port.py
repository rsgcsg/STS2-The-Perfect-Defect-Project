"""Portable M2 policy package and port-2 contract; synthetic, train-only."""

from __future__ import annotations

import copy
import io
import json
from unittest.mock import patch

import pytest
import torch
from test_memory_run import PRODUCER, _real_fixture_tokenizer, _verified_human_source
from test_memory_run import setup as old_run_setup
from test_text_menu_data import snapshot

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.memory_token_inputs import project_memory_snapshot
from stpd.memory_policy_installation import bind_memory_export, validate
from stpd.policy.memory_export import (
    MANIFEST_NAME,
    export_memory_package,
    validate_memory_package,
)
from stpd.policy.memory_port import PORT_SCHEMA, MemoryPolicyAdapter, serve
from stpd.workers.memory_ranking import MemoryConfig
from stpd.workers.memory_run import execute_memory_run, prepare_observed_memory_run


@pytest.fixture(autouse=True)
def two_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


@pytest.fixture
def package(tmp_path):
    store, source = _verified_human_source(tmp_path)
    tokenizer = _real_fixture_tokenizer()
    config = MemoryConfig(vocab_size=1, episode_count=1, max_tokens=256,
                          max_episode_input_tokens=1024, max_total_input_tokens=1024)
    run = prepare_observed_memory_run(store, source.artifact_id, config, PRODUCER, tokenizer)
    reporter = ObjectStoreRunReporter(store, store.blobs)
    outcome = execute_memory_run(store, reporter, run.artifact_id, PRODUCER)
    assert outcome.state == "completed"
    destination = tmp_path / "package"
    manifest = export_memory_package(store, reporter, run.artifact_id, destination)
    return destination, manifest, store, reporter, run.artifact_id


def test_portable_package_verified_lineage_and_no_source_bytes(package):
    directory, manifest, store, reporter, run_id = package
    checked, weights, tokenizer, config = validate_memory_package(directory)
    assert checked == manifest
    assert weights and tokenizer and config.episode_count == 1
    assert set(path.name for path in directory.iterdir()) == {
        "model.json", "weights.tensor-tree", "tokenizer.json"}
    assert all(key not in manifest for key in (
        "source_map", "events", "snapshot", "private_path"))
    with pytest.raises(BoundaryError, match="destination_exists"):
        export_memory_package(store, reporter, run_id, directory)


def test_package_rejects_drift_and_incomplete_or_old_input(package, tmp_path):
    directory, manifest, store, reporter, run_id = package
    weights = directory / "weights.tensor-tree"
    original = weights.read_bytes()
    weights.write_bytes(original + b"drift")
    with pytest.raises(BoundaryError, match="payload_digest_mismatch"):
        validate_memory_package(directory)
    weights.write_bytes(original)
    manifest_file = directory / MANIFEST_NAME
    changed = copy.deepcopy(manifest)
    changed["renderer"]["id"] = "unverified-renderer"
    manifest_file.write_bytes(json_bytes(changed))
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        validate_memory_package(directory)
    changed = copy.deepcopy(manifest)
    changed["evaluation_status"] = "completed"
    manifest_file.write_bytes(json_bytes(changed))
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        validate_memory_package(directory)
    manifest_file.write_bytes(json_bytes(manifest))
    with (patch.object(reporter, "completed", return_value=None),
          pytest.raises(BoundaryError, match="completed_result_required")):
        export_memory_package(store, reporter, run_id, tmp_path / "unfinished")
    old_store, old_reporter, _source, _config, old_run = old_run_setup(
        tmp_path / "old", count=1)
    with pytest.raises(BoundaryError, match="verified_observed_input_required"):
        export_memory_package(old_store, old_reporter, old_run.artifact_id,
                              tmp_path / "old-export")


def test_package_rejects_tokenizer_and_manifest_size_drift(package, monkeypatch):
    directory, manifest, *_ = package
    tokenizer = directory / "tokenizer.json"
    original = tokenizer.read_bytes()
    tokenizer.write_bytes(b"invalid")
    with pytest.raises(BoundaryError, match="payload_digest_mismatch"):
        validate_memory_package(directory)
    tokenizer.write_bytes(original)
    import stpd.policy.memory_export as export_module

    monkeypatch.setattr(export_module, "MAX_MANIFEST_BYTES", len(json_bytes(manifest)) - 1)
    with pytest.raises(BoundaryError, match="missing_or_oversize_file"):
        validate_memory_package(directory)


def _adapter(package, tmp_path, monkeypatch):
    directory, manifest, *_ = package
    root = tmp_path / "root"
    root.mkdir()
    config_path, manifest_path = root / "config.json", root / "manifest.json"
    import stpd.memory_policy_installation as installation

    monkeypatch.setattr(installation, "code_digest", lambda _: "b" * 64)
    support = {"game_versions": ["synthetic"], "game_commits": ["synthetic"],
               "interaction_kinds": ["choice"], "action_verbs": ["play", "back"]}
    sample = snapshot("first")
    support["interaction_kinds"] = [sample["interaction"]["kind"]]
    support["action_verbs"] = sorted({a["verb"] for a in sample["menu_actions"]["actions"]})
    config, policy_manifest = bind_memory_export(
        root, directory, config_path, manifest_path, manifest_id="synthetic-m2",
        policy={"id": "synthetic-m2", "version": "1", "provider": "test",
                "architecture": "stage1a.dsimple.m2.k1.experimental.v1"},
        requirements={"connector_protocol_version": "1.0.0",
                      "environment": {"host_kind": "test", "connector_version": "test",
                                      "connector_source_revision": "source",
                                      "connector_artifact_sha256": "a" * 64,
                                      "connector_module_version_id": "mvid",
                                      "modset_status": "exact", "modset_fingerprint": "test",
                                      "loaded_mod_ids": []},
                      "reads": [], "candidate_order_digest":
                      "sha256-json-menu-action-id-order", "whole_decision_admission": True,
                      "score_count_matches_candidate_count": True, "selected_index": True,
                      "successor_required": True},
        support=support)
    assert set(config) == {"schema", "export_path", "export_manifest_sha256", "model_id"}
    assert config["schema"] == "stpd/m2-policy-config-v1"
    assert policy_manifest["adapter"]["version"] == "1.0.0"
    assert policy_manifest["representation"]["input_schema"] == (
        "sts2.player-environment/text-menu-snapshot-1")
    assert validate(root, config_path, manifest_path) == (config, policy_manifest)
    import stpd.policy.memory_port as port

    monkeypatch.setattr(port, "ROOT", root)
    adapter = MemoryPolicyAdapter(config_path, manifest_path)
    return adapter, policy_manifest, config_path, manifest_path, sample


def test_port2_exact_sibling_completion_and_prewrite_rejection(package, tmp_path, monkeypatch):
    adapter, manifest, config_path, manifest_path, sample = _adapter(package, tmp_path,
                                                                     monkeypatch)
    sample["session"] = {"runtime_instance_id": "runtime-1",
                         "environment_fingerprint": "environment-1"}
    public = project_memory_snapshot(sample)
    request = {"run_id": "run-1", "manifest": manifest,
               "bundle": {"observation": sample, "reads": []},
               "candidate_digest": public.candidate_digest,
               "candidate_count": len(public.action_ids), "continuity_token": "opaque-A"}
    with patch.object(adapter.scorer._model, "step", wraps=adapter.scorer._model.step) as step:
        bad = copy.deepcopy(request)
        bad["candidate_digest"] = "0" * 64
        with pytest.raises(BoundaryError, match="candidate_binding_mismatch"):
            adapter.decide(bad)
        import stpd.policy.memory_port as port

        with (patch.object(port, "MAX_SNAPSHOT_BYTES", len(json_bytes(sample)) - 1),
              pytest.raises(BoundaryError, match="snapshot_size_limit")):
            adapter.decide(request)
        assert step.call_count == 0
        output, completion = adapter.decide(request)
        assert set(output) == {"candidate_digest", "scores", "selected_index"}
        assert completion == {"continuity_token": "opaque-A",
                              "snapshot_id": sample["snapshot_id"],
                              "sequence": sample["sequence"]}
        assert step.call_count == 1
        assert step.call_args.kwargs == {}
        assert adapter.decide(request) == (output, completion)
        assert step.call_count == 1
    fresh = MemoryPolicyAdapter(config_path, manifest_path)
    line = {"schema": PORT_SCHEMA, "message_type": "decide", "request_id": "r1",
            "input": request}
    response = io.StringIO()
    assert serve(fresh, io.StringIO(json.dumps(line) + "\n"), response) == 0
    ready, decision = map(json.loads, response.getvalue().splitlines())
    assert ready["schema"] == PORT_SCHEMA and ready["message_type"] == "ready"
    assert decision["output"] == output and decision["completion"] == completion
    assert "completion" not in decision["output"]


def test_port2_rejects_extra_keys_and_errors_have_no_completion(package, tmp_path, monkeypatch):
    adapter, manifest, _config_path, _manifest_path, sample = _adapter(package, tmp_path,
                                                                       monkeypatch)
    public = project_memory_snapshot(sample)
    request = {"run_id": "run", "manifest": manifest,
               "bundle": {"observation": sample, "reads": []},
               "candidate_digest": public.candidate_digest,
               "candidate_count": len(public.action_ids), "continuity_token": "A"}
    request["game_id"] = "forbidden"
    with pytest.raises(BoundaryError):
        adapter.decide(request)
    response = io.StringIO()
    serve(adapter, io.StringIO(json.dumps({"schema": PORT_SCHEMA, "message_type": "decide",
                                           "request_id": "r", "input": request}) + "\n"),
          response)
    error = json.loads(response.getvalue().splitlines()[1])
    assert error["message_type"] == "error" and "completion" not in error
    malformed = io.StringIO()
    duplicate = '{"schema":"' + PORT_SCHEMA + '","schema":"' + PORT_SCHEMA + '"}\n'
    serve(MemoryPolicyAdapter(_config_path, _manifest_path), io.StringIO(duplicate), malformed)
    duplicate_error = json.loads(malformed.getvalue().splitlines()[1])
    assert duplicate_error["message_type"] == "error"
    assert "duplicate_key" in duplicate_error["error"]["message"]


def test_installation_rejects_changed_code_pin_and_package(package, tmp_path, monkeypatch):
    _adapter_instance, manifest, config_path, manifest_path, _sample = _adapter(
        package, tmp_path, monkeypatch)
    root = config_path.parent
    changed = copy.deepcopy(manifest)
    changed["adapter"]["code_sha256"] = "0" * 64
    manifest_path.write_bytes(json_bytes(changed))
    with pytest.raises(BoundaryError, match="trusted_policy_identity_drift"):
        validate(root, config_path, manifest_path)
    manifest_path.write_bytes(json_bytes(manifest))
    directory = package[0]
    weights = directory / "weights.tensor-tree"
    weights.write_bytes(weights.read_bytes() + b"drift")
    with pytest.raises(BoundaryError, match="payload_digest_mismatch"):
        validate(root, config_path, manifest_path)


def test_port2_accepts_initial_zero_sequence_and_resets_on_new_token(
        package, tmp_path, monkeypatch):
    adapter, manifest, _config_path, _manifest_path, sample = _adapter(
        package, tmp_path, monkeypatch)
    sample["sequence"] = 0
    sample["session"] = {"runtime_instance_id": "runtime-1",
                         "environment_fingerprint": "environment-1"}
    public = project_memory_snapshot(sample)
    request = {"run_id": "run", "manifest": manifest,
               "bundle": {"observation": sample, "reads": []},
               "candidate_digest": public.candidate_digest,
               "candidate_count": len(public.action_ids), "continuity_token": "A"}
    first, completion = adapter.decide(request)
    assert completion["sequence"] == 0
    request["continuity_token"] = "B"
    assert adapter.decide(request)[0] == first
    request["continuity_token"] = "A"
    with pytest.raises(BoundaryError, match="retired_continuity"):
        adapter.decide(request)
