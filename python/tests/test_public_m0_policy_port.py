"""Synthetic generic-Snapshot public M0 port contracts; no game or Human evidence."""

from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path

import pytest
from test_public_inputs import snapshot

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError
from spireagent.policies import policy_support
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_model_registration import (
    LocalModelRegistration,
    _requirements,
)
from spireagent.workbench.local_models import LocalModelService
from stpd.fullrun.public_inputs import PUBLIC_VERBS, project_public_snapshot
from stpd.policy.public_m0_port import PublicM0PolicyAdapter
from stpd.policy.token_port import PORT_SCHEMA, serve
from stpd.public_m0_policy_installation import (
    ADAPTER_ID,
    GENERIC_ACTION_VERBS,
    GENERIC_INTERACTION_KINDS,
    PROFILE,
    _validate_qwen_snapshot,
)


def capabilities() -> dict:
    return {
        "protocol_version": "1.0.0",
        "snapshot_schema": "sts2.player-environment/snapshot-1",
        "action_schema": "sts2.player-environment/action-1",
        "receipt_schema": "sts2.player-environment/receipt-1",
        "control_schema": "sts2.player-environment/control-1",
        "status": "implemented",
        "host": {
            "id": "host-id", "name": "test", "version": "connector-1",
            "runtime_instance_id": "runtime-instance",
            "host_kind": "test",
            "implementation": {"source_revision": "source-1",
                                "artifact_sha256": "a" * 64,
                                "module_version_id": "mvid-1"},
        },
        "game": {
            "version": "game-1", "commit": "game-commit", "branch": None,
            "main_assembly_hash": None,
            "compatibility": {"status": "supported", "observation_allowed": True,
                              "detail": "exact fixture"},
            "modset": {"status": "exact", "fingerprint": "modset-1", "scope": "exact",
                       "loaded_mod_ids": [], "detail": "fixture"},
        },
        "environment_fingerprint": "environment-1",
        "verbs": sorted(GENERIC_ACTION_VERBS),
        "snapshot_bound": True,
        "single_controller": True,
        "execution_available": True,
        "control": {"recommended_renewal_ms": 1000},
        "evidence_profiles": [{
            "id": "native", "enabled": False, "supported_kinds": [],
            "snapshot_bound": True, "runtime_bound": True,
            "default_in_consumer_flow": False, "creates_mutation_authority": False,
            "enters_action_ledger": False,
        }],
        "non_claims": ["fixture only"],
    }


def test_generic_capabilities_bind_exact_snapshot_identity_and_closed_support():
    requirements, support = _requirements(capabilities(), input_profile=PROFILE)
    assert requirements == {
        "connector_protocol_version": "1.0.0",
        "environment": {
            "host_kind": "test", "connector_version": "connector-1",
            "connector_source_revision": "source-1",
            "connector_artifact_sha256": "a" * 64,
            "connector_module_version_id": "mvid-1", "modset_status": "exact",
            "modset_fingerprint": "modset-1", "loaded_mod_ids": [],
        },
        "reads": [], "whole_decision_admission": True,
        "candidate_order_digest": "sha256-json-bound-action-id-order",
        "score_count_matches_candidate_count": True, "selected_index": True,
        "successor_required": True,
    }
    assert support == {
        "game_versions": ["game-1"], "game_commits": ["game-commit"],
        "interaction_kinds": sorted(GENERIC_INTERACTION_KINDS),
        "action_verbs": sorted(GENERIC_ACTION_VERBS & PUBLIC_VERBS),
    }


@pytest.mark.parametrize("change", [
    lambda value: value.update(snapshot_schema="sts2.player-environment/text-menu-snapshot-1"),
    lambda value: value.update(receipt_schema="sts2.player-environment/text-menu-action-result-1"),
    lambda value: value.update(execution_available=False),
    lambda value: value.update(single_controller=False),
    lambda value: value["host"]["implementation"].update(artifact_sha256=None),
    lambda value: value.update(verbs=["begin_card_play"]),
    lambda value: value["game"]["compatibility"].update(observation_allowed=False),
])
def test_generic_capabilities_reject_mismatched_identity_or_scope(change):
    value = capabilities()
    change(value)
    with pytest.raises(BoundaryError, match="generic_capabilities_incompatible"):
        _requirements(value, input_profile=PROFILE)


class FakeScorer:
    def score_snapshot(self, value):
        public = project_public_snapshot(value)
        return {action.key: float(index) for index, action in enumerate(public.actions)}


def pair():
    manifest = {
        "manifest_id": "selection-m0",
        "adapter": {"id": ADAPTER_ID},
        "representation": {"id": "public_lite", "version": "public-v1",
                           "input_schema": "sts2.player-environment/snapshot-1"},
        "support": {"interaction_kinds": ["combat_turn"],
                    "action_verbs": sorted(GENERIC_ACTION_VERBS)},
    }
    adapter = object.__new__(PublicM0PolicyAdapter)
    adapter.manifest, adapter.scorer, adapter.closed = manifest, FakeScorer(), False
    observed = snapshot()
    for action in observed["bound_actions"]["actions"]:
        action["label"] = "same visible label"
    keys = [action["bound_action_id"] for action in observed["bound_actions"]["actions"]]
    request = {
        "run_id": "run-1", "manifest": copy.deepcopy(manifest),
        "bundle": {"observation": observed, "reads": []},
        "candidate_count": len(keys),
        "candidate_digest": hashlib.sha256(canonical_json(keys).encode()).hexdigest(),
    }
    return adapter, request


def test_full_ordered_catalog_scores_protocol_and_close():
    adapter, request = pair()
    result = adapter.decide(request)
    keys = [action["bound_action_id"] for action in
            request["bundle"]["observation"]["bound_actions"]["actions"]]
    assert result == {"candidate_digest": request["candidate_digest"],
                      "scores": [0.0, 1.0], "selected_index": 1}
    assert len(keys) == len(result["scores"]) == request["candidate_count"]
    line = {"schema": PORT_SCHEMA, "message_type": "decide", "request_id": "r1",
            "input": request}
    output = io.StringIO()
    assert serve(adapter, io.StringIO(json.dumps(line) + "\n"), output) == 0
    ready, decision = map(json.loads, output.getvalue().splitlines())
    assert ready["message_type"] == "ready"
    assert decision["request_id"] == "r1" and decision["output"] == result
    with pytest.raises(BoundaryError, match="adapter_closed"):
        adapter.decide(request)


@pytest.mark.parametrize("change", [
    lambda request: request.update(candidate_count=True),
    lambda request: request.update(candidate_digest="0" * 64),
    lambda request: request["bundle"].update(reads=[{}]),
    lambda request: request["bundle"]["observation"]["bound_actions"]["actions"].reverse(),
    lambda request: request["bundle"]["observation"]["bound_actions"].update(total_count=3),
    lambda request: request["bundle"]["observation"]["interaction"].update(kind="future_kind"),
    lambda request: request["bundle"]["observation"]["bound_actions"]["actions"][0].update(
        verb="purchase"),
    lambda request: request["bundle"]["observation"]["information_policy"].update(
        includes_hidden_information=True),
])
def test_rejects_whole_catalog_or_identity_drift_without_filtering(change):
    adapter, request = pair()
    change(request)
    with pytest.raises(BoundaryError):
        adapter.decide(request)


@pytest.mark.parametrize("scores", [{"other": 1.0}, {"card-runtime": float("nan")}])
def test_rejects_partial_or_nonfinite_score_maps(scores, monkeypatch):
    adapter, request = pair()
    monkeypatch.setattr(adapter.scorer, "score_snapshot", lambda _: scores)
    with pytest.raises(BoundaryError, match="score_binding_mismatch"):
        adapter.decide(request)


def test_public_adapter_is_an_explicit_trusted_pair():
    module = policy_support(ADAPTER_ID)
    assert module.PROFILE == PROFILE
    assert module.ADAPTER_ID == ADAPTER_ID


def test_public_m0_install_runtime_device_is_independent_of_training_device(
    tmp_path, monkeypatch,
):
    from types import SimpleNamespace

    import stpd.public_m0_policy_installation as installation

    identity = "a" * 64
    renderer = {"version": 1, "profile": "public_lite", "status": "ready"}
    manifest = {"fixture": "manifest"}
    policy_config = {
        "model_id": identity, "export_path": str(tmp_path / "export"),
        "renderer": renderer, "runtime_device": "cpu",
    }
    training_config = SimpleNamespace(device="cuda")
    artifact = SimpleNamespace(artifact_id=identity)
    info = {"renderer": renderer}
    monkeypatch.setattr(installation, "validate", lambda *args, **kwargs: (
        policy_config, manifest,
    ))
    monkeypatch.setattr(installation, "_model_details", lambda *args: (
        artifact, training_config, info,
    ))
    monkeypatch.setattr(
        installation.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b'{"available":true}'),
    )

    checks = installation.inspect(
        tmp_path, {"config": "registered/config.json", "manifest": "registered/manifest.json"},
        manifest, policy_config,
    )
    assert training_config.device == "cuda"
    assert checks["backend"] == {"status": "pass", "code": "cpu"}


def test_public_m0_install_rejects_cuda_runtime_device():
    import stpd.public_m0_policy_installation as installation

    with pytest.raises(BoundaryError, match="unsupported_runtime_device"):
        installation._public_m0_runtime_device("cuda")


def test_local_registry_routes_public_m0_only_to_its_trusted_port(tmp_path):
    service = LocalModelService(ProjectConfig(tmp_path, "", "", None, combination()))
    service.private_root.mkdir(parents=True)
    entry = {"id": "public-m0", "label": "fixture", "adapter": ADAPTER_ID,
             "runtime_profile": PROFILE, "manifest": "registered/manifest.json",
             "config": "registered/config.json"}
    (service.private_root / "token-policies-v1.json").write_text(json.dumps({
        "schema": "stpd/local-token-policies-v1", "policies": [entry],
    }), encoding="utf-8")
    registered = next(item for item in service.registry()["policies"]
                      if item["id"] == "public-m0")
    assert service.adapter_arguments(registered) == [
        "-m", "stpd.policy.public_m0_port",
        "--config", str(service.private_root / "registered/config.json"),
        "--manifest", str(service.private_root / "registered/manifest.json"),
        "--binding-root", str(service.private_root),
    ]

    entry["adapter"] = "token-v1"
    (service.private_root / "token-policies-v1.json").write_text(json.dumps({
        "schema": "stpd/local-token-policies-v1", "policies": [entry],
    }), encoding="utf-8")
    with pytest.raises(BoundaryError, match="unsupported_runtime_profile"):
        service.registry()


def test_public_m0_status_shape_alone_cannot_authorize_registration(
    tmp_path, monkeypatch,
):
    identity = "a" * 64
    export = type("Export", (), {"status": lambda self: {
        "schema": "stpd/local-model-export-operation-v3",
        "operation": {"model_id": identity, "model_type": "public_m0",
                      "profile": PROFILE},
    }})()
    service = LocalModelRegistration(
        object(), export, type("Models", (), {})(),
    )
    monkeypatch.setattr("spireagent.workbench.local_model_registration.require_local_models",
                        lambda _stage: None)
    with pytest.raises(BoundaryError, match="public_m0_export_verification_required"):
        service.register(identity)


@pytest.mark.parametrize(("backbone", "identity_key"), [("pf", "qwen"), ("pl", "qwen_base")])
def test_public_pf_pl_require_explicit_exact_qwen_snapshot(
    tmp_path, monkeypatch, backbone, identity_key,
):
    from types import SimpleNamespace

    from stpd.qwen import l2

    observed = SimpleNamespace(
        model_id="Qwen/Qwen3-0.6B-Base", repo_revision="1" * 40,
        weights_sha256="2" * 64, config_sha256="3" * 64,
        tokenizer_bundle_sha256="4" * 64,
    )
    identity = {
        "model_id": observed.model_id, "model_revision": observed.repo_revision,
        "tokenizer_revision": observed.repo_revision,
        "weights_sha256": observed.weights_sha256,
        "config_sha256": observed.config_sha256,
        "tokenizer_sha256": observed.tokenizer_bundle_sha256,
    }
    calls = []

    def inspect(snapshot):
        calls.append(snapshot)
        return observed

    monkeypatch.setattr(l2, "inspect_l2_snapshot", inspect)
    config = type("M0Config", (), {"recipe": f"stage1a.dsimple.light-action.m0.{backbone}.v1"})()
    info = {"backbone": {identity_key: identity}}
    with pytest.raises(BoundaryError, match="pinned_snapshot_required"):
        _validate_qwen_snapshot(config, info, None)
    assert calls == []

    snapshot = tmp_path / "pinned-qwen"
    _validate_qwen_snapshot(config, info, str(snapshot))
    assert calls == [snapshot]

    wrong = {"backbone": {identity_key: {**identity, "weights_sha256": "5" * 64}}}
    with pytest.raises(BoundaryError, match="qwen_snapshot_identity_mismatch"):
        _validate_qwen_snapshot(config, wrong, str(snapshot))


def test_public_scratch_rejects_an_unneeded_qwen_snapshot(tmp_path):
    config = type("M0Config", (), {
        "recipe": "stage1a.dsimple.light-action.m0.s.v1",
    })()
    _validate_qwen_snapshot(config, {"backbone": {"kind": "scratch"}}, None)
    with pytest.raises(BoundaryError, match="scratch_has_no_qwen_dependency"):
        _validate_qwen_snapshot(config, {"backbone": {"kind": "scratch"}},
                                str(tmp_path / "unrelated"))


def test_verified_public_export_binds_and_scores_real_current_snapshot(
    tmp_path, monkeypatch,
):
    pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    import test_decision_store
    from platform_bundle3_fixture import bundle3
    from test_light_action_m0_canonical_cli import _cli, _synthetic_workspace

    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore
    from stpd.policy.token_decision import PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA
    from stpd.public_m0_policy_installation import bind_public_m0_export, validate

    monkeypatch.setattr(
        test_decision_store, "bundle3", lambda path: bundle3(path, public_bindings=True)
    )
    config_source, store_dir, dataset_id, _owner = _synthetic_workspace(tmp_path)
    operation = "d" * 32
    common = ("--store", str(store_dir))
    prepared = _cli(
        monkeypatch, *common, "prepare-light-action-m0",
        "--project-config", str(config_source), "--dataset", dataset_id,
        "--operation", operation, "--backbone", "s", "--input-profile", "public_lite",
        "--train-limit", "8", "--dev-limit", "4",
    )
    trained = _cli(
        monkeypatch, *common, "train-light-action-m0",
        "--project-config", str(config_source), "--inputs", prepared["training_input_id"],
        "--operation", operation, "--recipe", "stage1a.dsimple.light-action.m0.s.v1",
        "--steps", "1",
    )
    assert trained["state"] == "completed"
    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    result = store.get_manifest(trained["result_id"])
    model_id = result.parent("model")
    export_path = tmp_path / "verified-public-m0"
    exported = _cli(
        monkeypatch, *common, "export-light-action-m0",
        "--project-config", str(config_source), "--operation", operation,
        "--model", model_id, "--destination", str(export_path),
    )
    assert exported["schema"] == PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA
    requirements, support = _requirements(capabilities(), input_profile=PROFILE)
    private = tmp_path / "private"
    config_path, manifest_path = private / "registrations/m0/config.json", (
        private / "registrations/m0/manifest.json"
    )
    bind_public_m0_export(
        Path(__file__).resolve().parents[1], export_path, config_path, manifest_path,
        manifest_id="public-m0-test", policy={"id": "public-m0-test", "version": "1.0.0",
        "provider": "stpd", "architecture": "stage1a.dsimple.light-action.m0.s.v1"},
        requirements=requirements, support=support, binding_root=private,
    )
    config, manifest = validate(
        Path(__file__).resolve().parents[1], config_path, manifest_path,
        binding_root=private,
    )
    assert config["model_id"] == model_id
    assert config["runtime_device"] == "cpu"
    assert manifest["adapter"]["id"] == ADAPTER_ID
    adapter = PublicM0PolicyAdapter(config_path, manifest_path, binding_root=private)
    assert adapter.scorer.runtime_device == "cpu"
    observation = snapshot()
    for action in observation["bound_actions"]["actions"]:
        action["label"] = "same visible label"
    keys = [action["bound_action_id"] for action in observation["bound_actions"]["actions"]]
    request = {
        "run_id": "synthetic-run", "manifest": manifest,
        "bundle": {"observation": observation, "reads": []},
        "candidate_count": len(keys),
        "candidate_digest": hashlib.sha256(canonical_json(keys).encode()).hexdigest(),
    }
    decision = adapter.decide(request)
    assert len(decision["scores"]) == len(keys) == request["candidate_count"]
    assert decision["candidate_digest"] == request["candidate_digest"]
    assert decision["selected_index"] in range(len(keys))
    adapter.close()

    envelope_path = export_path / "model.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["schema"] = "stpd/stage1a-export-v1"
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(BoundaryError):
        validate(Path(__file__).resolve().parents[1], config_path, manifest_path,
                 binding_root=private)
