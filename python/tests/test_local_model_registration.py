"""Synthetic exact-export registration; never touches a live Connector or game."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from dataclasses import replace
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest
from test_artifact_store_v1 import store
from test_local_model_export import _config, _settle

from spireagent.json_boundary import BoundaryError
from spireagent.package_identity import PackageIdentityError
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.store import copy_artifact
from spireagent.workbench import local_model_export as export_module
from spireagent.workbench import local_model_registration as registration_module
from spireagent.workbench.developer import ROOT, LocalResearchWorkspaceConfig, atomic_json
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import (
    REGISTRY,
    SCHEMA,
    VERBS,
    LocalModelRegistration,
    _managed_requirements,
    _requirements,
)
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE,
    RESET_K1_RECIPE,
    V2_M2_K1_RECIPE,
    V2_RESET_K1_RECIPE,
)
from stpd.token_policy_installation import validate

pytest_plugins = ["test_local_model_export"]


def _registration(tmp_path: Path, completed, monkeypatch):
    config = _config(tmp_path, completed)
    exported = LocalModelExport(config)
    exported.start(completed[3])
    assert _settle(exported)["status"] == "completed"
    models = LocalModelService(config)
    root = tmp_path / "python-root"
    (root / "configs/developer").mkdir(parents=True)
    (root / "configs/v0/qwen").mkdir(parents=True)
    (root / "stpd/policy").mkdir(parents=True)
    (root / "spireagent").mkdir()
    for name in ("configs/developer/local-policies-v1.json",
                 "configs/v0/qwen/qwen3-0.6b-base-l2.json", "stpd/policy/token_port.py",
                 "uv.lock"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    models.root = root
    models.private_root.mkdir(parents=True)
    atomic_json(models.private_root / "text-menu-runtime-v1.json", {
        "schema": "stpd/local-text-runtime-v1",
        "runtime_package": {"package": "@rsgcsg/sts2-policy-runtime",
                            "dependency_layout": "bundled_source_candidate"},
    })
    monkeypatch.setattr(registration_module, "validate_runtime_install",
                        lambda *_args: {"version": "synthetic-validated"})
    service = LocalModelRegistration(config, exported, models)
    monkeypatch.setattr(service, "_capabilities", lambda _sdk, **_kwargs: _caps())
    return service, config, completed[3], root, models


@pytest.fixture
def registration(tmp_path: Path, completed, monkeypatch):
    return _registration(tmp_path, completed, monkeypatch)


def _caps() -> dict:
    return {"input_profile": "text-menu-v1",
            "snapshot_schema": "sts2.player-environment/text-menu-snapshot-1",
            "receipt_schema": "sts2.player-environment/text-menu-action-result-1",
            "protocol_version": "1.0.0", "execution_available": True,
            "single_controller": True, "verbs": list(VERBS),
            "host": {"host_kind": "test", "version": "synthetic-connector",
                     "implementation": {"source_revision": "test-source",
                                        "artifact_sha256": "a" * 64,
                                        "module_version_id": "test-mvid"}},
            "game": {"version": "test-game", "commit": "test-commit",
                     "modset": {"status": "exact", "fingerprint": "test-modset",
                                "loaded_mod_ids": []}}}


def test_connector_sdk_path_follows_pinned_layout_when_both_paths_exist(
        tmp_path: Path) -> None:
    node_modules = tmp_path / "node_modules"
    flat_sdk = node_modules / registration_module.CONNECTOR_PACKAGE / "dist" / "index.js"
    nested_sdk = (node_modules / registration_module.RUNTIME_PACKAGE / "node_modules"
                  / registration_module.CONNECTOR_PACKAGE / "dist" / "index.js")
    flat_sdk.parent.mkdir(parents=True)
    nested_sdk.parent.mkdir(parents=True)
    flat_sdk.write_text("// flat connector SDK\n")
    nested_sdk.write_text("// nested connector SDK\n")

    assert registration_module._connector_sdk_path(
        node_modules, {"dependency_layout": None},
    ) == flat_sdk
    assert registration_module._connector_sdk_path(
        node_modules, {"dependency_layout": registration_module.BUNDLED_LAYOUT},
    ) == nested_sdk
    with pytest.raises(BoundaryError, match="runtime_package_not_pinned"):
        registration_module._connector_sdk_path(node_modules, {"dependency_layout": "unknown"})


def test_v2_capabilities_require_exact_profile_schemas_and_selection_verbs() -> None:
    caps = _caps()
    caps.update(input_profile="text-menu-v2",
                snapshot_schema="sts2.player-environment/text-menu-snapshot-2",
                receipt_schema="sts2.player-environment/text-menu-action-result-2",
                verbs=[*VERBS, "select_card", "select_target", "cancel_selection"])
    requirements, support = _requirements(caps, input_profile="text-menu-v2")
    assert requirements["whole_decision_admission"] is True
    assert support["action_verbs"][-3:] == [
        "select_card", "select_target", "cancel_selection"]
    for changed in ({**caps, "input_profile": "text-menu-v1"},
                    {**caps, "snapshot_schema": "sts2.player-environment/text-menu-snapshot-1"},
                    {**caps, "verbs": list(VERBS)}):
        with pytest.raises(BoundaryError, match="text_menu_capabilities_incompatible"):
            _requirements(changed, input_profile="text-menu-v2")


def test_exact_export_binds_existing_policy_contract_and_is_idempotent(registration):
    service, config, model_id, root, models = registration
    assert service.status(model_id) == {
        "schema": SCHEMA, "model_id": model_id, "status": "not_registered",
        "loaded": False, "runtime_profile": "text-menu-v1",
    }
    result = service.register(model_id)
    assert result["status"] == "registered" and result["loaded"] is False
    assert service.status(model_id) == result
    assert service.register(model_id) == result
    entry = models.selection(result["selection_id"])
    assert entry["runtime_profile"] == "text-menu-v1" and entry["adapter"] == "token-v1"
    config_file = models.private_root / entry["config"]
    manifest_file = models.private_root / entry["manifest"]
    bound_config, manifest = validate(root, config_file, manifest_file,
                                      binding_root=models.private_root)
    assert bound_config["model_id"] == model_id
    assert bound_config["export_path"] == str(config.state_dir / "model-exports" / model_id)
    assert manifest["requirements"]["environment"]["host_kind"] == "test"
    assert manifest["support"]["action_verbs"] == list(VERBS)
    assert manifest["claims"]["full_run"] is False
    assert manifest["policy"]["architecture"] == "stage1a.dsimple.s.v1"
    assert entry["label"] == "本机文字菜单 D-Simple " + model_id[:8]
    assert len(json.loads((models.private_root / REGISTRY).read_bytes())["policies"]) == 1


def test_existing_b_model_registration_preserves_its_architecture(
    tmp_path: Path, completed_b, monkeypatch,
) -> None:
    service, _, model_id, root, models = _registration(tmp_path, completed_b, monkeypatch)
    result = service.register(model_id)
    entry = models.selection(result["selection_id"])
    _, manifest = validate(root, models.private_root / entry["config"],
                           models.private_root / entry["manifest"],
                           binding_root=models.private_root)
    assert manifest["policy"]["architecture"] == "stage1a.b.s.v2"
    assert entry["label"] == "本机文字菜单 B " + model_id[:8]


@pytest.mark.parametrize(("recipe", "label", "profile"), [
    (M2_K1_RECIPE, "M2-K1 训练版", "text-menu-m2-v1"),
    (RESET_K1_RECIPE, "Reset-K1 独立训练对照版", "text-menu-m2-v1"),
    (V2_M2_K1_RECIPE, "M2-K1 v2 工程训练版", "text-menu-m2-v2"),
    (V2_RESET_K1_RECIPE, "Reset-K1 v2 工程对照版", "text-menu-m2-v2"),
])
def test_memory_registration_keeps_exact_architecture_and_label(
        tmp_path: Path, monkeypatch, recipe: str, label: str, profile: str) -> None:
    model_id = "a" * 64
    root = tmp_path / "models"
    root.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    exported = SimpleNamespace(
        status=lambda: {"schema": "stpd/local-model-export-operation-v2",
                        "operation": {"model_id": model_id, "model_type": "memory"}},
        verified_memory_for_registration=lambda *_args, **_kwargs: tmp_path / "export",
        verified_memory_recipe_for_registration=lambda *_args, **_kwargs: recipe,
    )
    models = SimpleNamespace(
        root=root, private_root=root,
        text_runtime_profile=lambda requested: (runtime, {"pin": requested}),
        _connector_pin=lambda: {"pin": "connector"},
    )
    service = LocalModelRegistration(SimpleNamespace(), exported, models)
    monkeypatch.setattr(registration_module, "validate_runtime_install", lambda *_args: {})
    monkeypatch.setattr(registration_module, "_v2_sdk_available", lambda _sdk: True)
    def capabilities(*_args, **kwargs):
        value = _caps()
        if kwargs.get("input_profile") == "text-menu-v2":
            value.update(input_profile="text-menu-v2",
                         snapshot_schema="sts2.player-environment/text-menu-snapshot-2",
                         receipt_schema="sts2.player-environment/text-menu-action-result-2",
                         verbs=[*VERBS, "select_card", "select_target", "cancel_selection"])
        return value
    monkeypatch.setattr(service, "_capabilities", capabilities)
    monkeypatch.setattr(service, "_context_available", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_m2_runtime_manifest_compatible",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_entries", lambda: [])
    monkeypatch.setattr(service, "_matching", lambda *_args, **_kwargs: (None, False))
    captured: dict[str, object] = {}

    def bind(_root, _export, config_path, manifest_path, **kwargs):
        captured.update(kwargs)
        config_path.write_text("{}")
        manifest_path.write_text("{}")

    monkeypatch.setattr(registration_module, "bind_memory_export", bind)
    result = service.register(model_id)
    assert result["runtime_profile"] == profile
    assert captured.get("input_profile") == ("text-menu-v2" if profile.endswith("v2") else None)
    registry = json.loads((service.models.private_root / REGISTRY).read_bytes())
    entry = registry["policies"][0]
    assert result["status"] == "registered"
    assert captured["policy"]["architecture"] == recipe
    assert entry["label"] == "本机文字菜单 " + label + " " + model_id[:8]
    assert entry["adapter"] == "stpd-m2-decision-adapter"


def test_registration_budget_prevents_append_after_expensive_binding(
        registration, monkeypatch) -> None:
    service, _, model_id, root, _ = registration
    clock = [0.0]
    monkeypatch.setattr(registration_module, "monotonic", lambda: clock[0])
    binder = registration_module.bind_text_menu_export

    def slow_capabilities(_sdk, *, deadline):
        assert deadline == registration_module.REGISTRATION_SECONDS
        clock[0] += registration_module.REGISTRATION_SECONDS - 10.0
        return _caps()

    def slow_binding(*args, **kwargs):
        result = binder(*args, **kwargs)
        clock[0] += 11.0
        return result

    monkeypatch.setattr(service, "_capabilities", slow_capabilities)
    monkeypatch.setattr(registration_module, "bind_text_menu_export", slow_binding)
    with pytest.raises(BoundaryError, match="registration_timeout"):
        service.register(model_id)
    assert not (service.models.private_root / REGISTRY).exists()
    assert list((service.models.private_root / "model-registrations").iterdir()) == []


@pytest.mark.parametrize(
    ("receipt_matches", "verification_seconds", "expected_stage", "expected_code"),
    [
        (True, 44.9, "ok", "ok"),
        (False, 0.0, "local_model_export", "verified_export_required"),
        (True, 45.0, "local_model_registration", "registration_timeout"),
    ],
)
def test_public_m0_export_verification_distinguishes_deadline_from_receipt_failure(
        tmp_path: Path, monkeypatch, receipt_matches: bool, verification_seconds: float,
        expected_stage: str, expected_code: str) -> None:
    model_id = "a" * 64
    store_root = tmp_path / "store"
    state_dir = tmp_path / "state"
    model = SimpleNamespace(artifact_id=model_id)
    store = SimpleNamespace(blobs=SimpleNamespace(root=store_root),
                            get_manifest=lambda _identity: model)
    workspace = SimpleNamespace(store=store)
    verified = {
        "export_schema": "synthetic-export-schema",
        "model_schema": "synthetic-model-schema",
        "package_sha256": "b" * 64,
        "package_size": 1,
        "payload_sha256": "c" * 64,
        "payload_sizes": {},
        "payload_bytes": 0,
    }
    lineage = {"result_id": "d" * 64}
    receipt = {
        "schema": export_module.PUBLIC_M0_RECEIPT_SCHEMA,
        "model_id": model_id,
        "export_schema": verified["export_schema"],
        "model_schema": verified["model_schema"],
        **lineage,
        "package_sha256": verified["package_sha256"],
        "package_size": verified["package_size"],
        "payload_sha256": verified["payload_sha256"],
        "payload_sizes": verified["payload_sizes"],
        "payload_bytes": verified["payload_bytes"],
    }
    if not receipt_matches:
        receipt["package_sha256"] = "e" * 64
    exporter = SimpleNamespace(
        lock=threading.RLock(),
        config=SimpleNamespace(state_dir=state_dir),
        _read=lambda: {
            "schema": export_module.SCHEMA_V3,
            "status": "completed",
            "model_id": model_id,
            "model_type": "public_m0",
            "profile": "public-snapshot-m0-v1",
            "verified_receipt": receipt,
            "store_root": str(store_root),
        },
        _workspace=lambda: workspace,
        _memory_owner=lambda _store: object(),
    )
    clock = [0.0]
    monkeypatch.setattr(export_module, "_public_m0_lineage", lambda *_args: lineage)
    def verify(*_args):
        clock[0] += verification_seconds
        return verified
    monkeypatch.setattr(export_module, "_verify_public_m0_export", verify)
    monkeypatch.setattr(export_module, "monotonic", lambda: clock[0])

    try:
        result = LocalModelExport.verified_public_m0_for_registration(
            exporter, model_id, deadline=45.0,
        )
    except BoundaryError as error:
        assert (error.stage, error.code) == (expected_stage, expected_code)
    else:
        assert (expected_stage, expected_code) == ("ok", "ok")
        assert result == state_dir / export_module.EXPORT_ROOT / model_id


def test_node_checks_consume_one_registration_deadline(registration, monkeypatch) -> None:
    service, _, _, root, _ = registration
    clock = [0.0]
    observed: list[float] = []
    monkeypatch.setattr(registration_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(registration_module.shutil, "which", lambda _name: "node")
    outputs = [json.dumps(_caps()).encode(), json.dumps({
        "schema": "sts2.player-environment/text-menu-observation-context-1",
        "continuity_available": True,
    }).encode(), b""]

    def run(*_args, timeout, **_kwargs):
        index = len(observed)
        observed.append(timeout)
        clock[0] += (9.0, 8.0, 4.0)[index]
        return subprocess.CompletedProcess([], 0, outputs[index])

    monkeypatch.setattr(registration_module.subprocess, "run", run)
    deadline = 22.0
    assert LocalModelRegistration._capabilities(service, root / "sdk.js",
                                                deadline=deadline) == _caps()
    service._context_available(root / "sdk.js", deadline=deadline)
    service._m2_runtime_manifest_compatible(root / "node_modules", root / "manifest.json",
                                            deadline=deadline)
    assert observed == [12.0, 12.0, 5.0]
    clock[0] = 22.0
    with pytest.raises(BoundaryError, match="registration_timeout"):
        LocalModelRegistration._capabilities(service, root / "sdk.js", deadline=deadline)


def test_changed_environment_and_source_append_without_rewriting_old(registration,
                                                                       monkeypatch):
    service, _, model_id, root, _ = registration
    first = service.register(model_id)
    old = (service.models.private_root / REGISTRY).read_bytes()
    changed = _caps()
    changed["game"]["modset"]["fingerprint"] = "new-modset"
    monkeypatch.setattr(service, "_capabilities", lambda _sdk, **_kwargs: changed)
    second = service.register(model_id)
    assert second["selection_id"] != first["selection_id"]
    assert len(json.loads((service.models.private_root / REGISTRY).read_bytes())["policies"]) == 2
    assert old != (service.models.private_root / REGISTRY).read_bytes()
    (root / "stpd/policy/token_port.py").write_text("# changed source\n")
    assert service.status(model_id)["status"] == "not_registered"
    assert service.status(model_id)["reason_code"] == "source_binding_changed"
    rebound = service.register(model_id)
    assert rebound["status"] == "registered"
    assert rebound["selection_id"] not in {first["selection_id"], second["selection_id"]}
    assert len(json.loads((service.models.private_root / REGISTRY).read_bytes())["policies"]) == 3


def test_private_registration_survives_checkout_change_but_code_drift_blocks(
        registration, tmp_path: Path) -> None:
    service, _, model_id, root, models = registration
    selected = service.register(model_id)["selection_id"]
    roster = (models.private_root / REGISTRY).read_bytes()
    other = tmp_path / "another-checkout"
    shutil.copytree(root, other)
    models.root = other
    assert service.status(model_id)["selection_id"] == selected
    assert models.selection(selected)["id"] == selected
    assert (models.private_root / REGISTRY).read_bytes() == roster
    assert not (other / REGISTRY).exists()
    (other / "stpd/policy/token_port.py").write_text("# different trusted source\n")
    assert service.status(model_id)["reason_code"] == "source_binding_changed"
    assert models.readiness(selected)["checks"]["token_policy"] == {
        "status": "blocked", "code": "trusted_policy_identity_drift",
    }


def test_cold_token_adapter_accepts_application_bound_private_config(registration):
    service, _, model_id, _, models = registration
    models.root = ROOT
    selected = service.register(model_id)["selection_id"]
    entry = models.selection(selected)
    arguments = models.adapter_arguments(entry)
    assert arguments[-2:] == ["--binding-root", str(models.private_root)]
    result = subprocess.run([sys.executable, *arguments], input=b"", capture_output=True,
                            timeout=30, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_registration_distinguishes_profile_absence_invalidity_and_install_drift(
        registration, monkeypatch):
    service, _, model_id, _, models = registration
    profile = models.private_root / "text-menu-runtime-v1.json"
    original = profile.read_bytes()
    profile.unlink()
    with pytest.raises(BoundaryError, match="text_runtime_profile_required"):
        service.register(model_id)
    profile.write_text("{}")
    with pytest.raises(BoundaryError, match="text_runtime_profile_invalid"):
        service.register(model_id)
    profile.write_bytes(original)
    monkeypatch.setattr(registration_module, "validate_runtime_install",
                        lambda *_: (_ for _ in ()).throw(PackageIdentityError("drift")))
    with pytest.raises(BoundaryError, match="text_runtime_local_install_required"):
        service.register(model_id)


def test_missing_export_bad_capabilities_and_failed_registry_write_are_closed(
    registration, monkeypatch,
):
    service, config, model_id, root, _ = registration
    assert service.status("b" * 64)["reason_code"] == "verified_export_required"
    with pytest.raises(BoundaryError, match="verified_export_required"):
        service.register("b" * 64)
    incomplete = _caps()
    incomplete["verbs"] = ["select"]
    with pytest.raises(BoundaryError, match="text_menu_capabilities_incompatible"):
        _requirements(incomplete)
    monkeypatch.setattr(service, "_capabilities", lambda _sdk, **_kwargs: incomplete)
    with pytest.raises(BoundaryError, match="text_menu_capabilities_incompatible"):
        service.register(model_id)
    assert not (service.models.private_root / REGISTRY).exists()
    monkeypatch.setattr(service, "_capabilities", lambda _sdk, **_kwargs: _caps())
    original = registration_module.atomic_json

    def failed_registry(path, value):
        if path == service.models.private_root / REGISTRY:
            raise OSError("synthetic atomic-write failure")
        return original(path, value)

    monkeypatch.setattr(registration_module, "atomic_json", failed_registry)
    with pytest.raises(BoundaryError, match="registration_write_failed"):
        service.register(model_id)
    assert not (service.models.private_root / REGISTRY).exists()
    monkeypatch.setattr(registration_module, "atomic_json", original)
    assert service.register(model_id)["status"] == "registered"
    assert config.state_dir.is_dir()


def _managed_contract_target() -> dict:
    return {
        "exact_game": {"version": "1.0.0", "commit": "game-commit"},
        "text_menu_contracts": [{
            "input_profile": "text-menu-v2",
            "snapshot_schema": "sts2.player-environment/text-menu-snapshot-2",
            "receipt_schema": "sts2.player-environment/text-menu-action-result-2",
            "protocol_version": "1.0.0",
            "interaction_kinds": ["combat_turn", "card_reward"],
            "action_verbs": ["play", "end_turn", "select_card"],
        }],
        # These private attachment details must never enter policy requirements.
        "service_instance_id": "service-private",
        "client_attachment": "/private/managed/client.json",
        "bearer": "managed-secret-token",
    }


def test_managed_requirements_accepts_only_one_exact_v2_host_contract():
    requirements, support = _managed_requirements(_managed_contract_target())
    assert requirements["environment"] == {
        "kind": "managed_text_v2", "text_protocol_version": "1.0.0",
        "input_profile": "text-menu-v2",
    }
    assert support["game_versions"] == ["1.0.0"]
    assert support["game_commits"] == ["game-commit"]
    assert support["interaction_kinds"] == ["combat_turn", "card_reward"]
    assert support["action_verbs"] == ["play", "end_turn", "select_card"]
    rendered = json.dumps({"requirements": requirements, "support": support})
    assert "managed-secret-token" not in rendered
    assert "/private/managed/client.json" not in rendered
    assert "service-private" not in rendered

    invalid = _managed_contract_target()
    invalid["text_menu_contracts"].append(dict(invalid["text_menu_contracts"][0]))
    with pytest.raises(BoundaryError, match="managed_contract_unavailable"):
        _managed_requirements(invalid)
    invalid = _managed_contract_target()
    invalid["text_menu_contracts"][0]["receipt_schema"] = (
        "sts2.player-environment/text-menu-action-result-1")
    with pytest.raises(BoundaryError, match="managed_contract_unavailable"):
        _managed_requirements(invalid)


def test_matching_keeps_native_and_managed_registrations_separate_for_same_export(
        tmp_path: Path, monkeypatch) -> None:
    from stpd import memory_policy_installation, token_policy_installation

    model_id = "a" * 64
    export = tmp_path / "export"
    root = tmp_path / "root"
    private = tmp_path / "private"
    export.mkdir()
    private.mkdir()
    config_paths = []
    manifest_paths = []
    entries = []
    for selection, kind in (("native-selection", "native"),
                            ("managed-selection", "managed")):
        config_path = private / f"{selection}.config.json"
        manifest_path = private / f"{selection}.manifest.json"
        config_path.write_text(json.dumps({"model_id": model_id,
                                           "export_path": str(export.resolve())}))
        manifest_path.write_text(json.dumps({
            "requirements": {"environment": {"kind": (
                "managed_text_v2" if kind == "managed" else "native")}},
            "adapter": {"code_sha256": "trusted-code"},
        }))
        config_paths.append(config_path)
        manifest_paths.append(manifest_path)
        entries.append({"id": selection, "runtime_profile": "text-menu-m2-v2",
                        "config": config_path.name, "manifest": manifest_path.name})

    models = SimpleNamespace(root=root, private_root=private,
                             registry=lambda: {},)
    registration = LocalModelRegistration(SimpleNamespace(), SimpleNamespace(), models)
    monkeypatch.setattr(registration, "_entries", lambda: entries)
    monkeypatch.setattr(memory_policy_installation, "code_digest", lambda _root: "trusted-code")
    monkeypatch.setattr(memory_policy_installation, "validate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(token_policy_installation, "validate", lambda *_args, **_kwargs: None)

    assert registration._matching(model_id, export, profile="text-menu-m2-v2",
                                  environment_kind="native") == ("native-selection", False)
    assert registration._matching(model_id, export, profile="text-menu-m2-v2",
                                  environment_kind="managed") == ("managed-selection", False)


def test_managed_registration_uses_host_contract_without_native_probes_or_private_publication(
        tmp_path: Path, monkeypatch) -> None:
    model_id = "b" * 64
    export_path = tmp_path / "verified-export"
    export_path.mkdir()
    root = tmp_path / "checkout"
    root.mkdir()
    private = tmp_path / "private-models"
    private.mkdir(mode=0o700)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    export = SimpleNamespace(
        status=lambda: {"schema": "stpd/local-model-export-operation-v2",
                        "operation": {"model_id": model_id, "model_type": "memory"}},
        verified_memory_for_registration=lambda *_args, **_kwargs: export_path,
        verified_memory_recipe_for_registration=lambda *_args, **_kwargs:
            "stage1a.dsimple.m2.k1.confirmed-interaction.v2",
    )
    models = SimpleNamespace(
        root=root, private_root=private,
        text_runtime_profile=lambda _profile: (runtime, {"pin": "runtime-pin"}),
        _connector_pin=lambda: {"pin": "connector-pin"},
        _managed_runtime_target=_managed_contract_target,
        registry=lambda: {},
    )
    registration = LocalModelRegistration(SimpleNamespace(), export, models)
    monkeypatch.setattr(registration_module, "require_local_models", lambda *_args: None)
    monkeypatch.setattr(registration_module, "validate_runtime_install", lambda *_args: {})
    monkeypatch.setattr(registration, "_matching", lambda *_args, **_kwargs: (None, False))
    monkeypatch.setattr(registration, "_m2_runtime_manifest_compatible",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(registration, "_capabilities",
                        lambda *_args, **_kwargs: pytest.fail("Native capabilities were queried"))
    monkeypatch.setattr(registration, "_context_available",
                        lambda *_args, **_kwargs: pytest.fail("Native context was queried"))

    captured: dict[str, object] = {}

    def bind(_root, _export, config_path, manifest_path, **kwargs):
        captured.update(kwargs)
        config_path.write_text(json.dumps({"model_id": model_id,
                                           "export_path": str(export_path.resolve())}))
        manifest_path.write_text(json.dumps({"requirements": kwargs["requirements"],
                                             "support": kwargs["support"]}))

    monkeypatch.setattr(registration_module, "bind_managed_memory_export", bind)
    result = registration.register(model_id, environment_kind="managed")
    assert result["status"] == "registered"
    assert result["environment_kind"] == "managed"
    assert result["runtime_profile"] == "text-menu-m2-v2"
    assert captured["requirements"]["environment"]["kind"] == "managed_text_v2"
    registry = json.loads((private / REGISTRY).read_bytes())
    manifest_path = private / registry["policies"][0]["manifest"]
    published = manifest_path.read_text()
    for secret in ("service-private", "managed-secret-token", "/private/managed/client.json"):
        assert secret not in published


def test_managed_registration_rejects_non_confirmed_interaction_model(
        tmp_path: Path, monkeypatch) -> None:
    model_id = "c" * 64
    export_path = tmp_path / "verified-export"
    export_path.mkdir()
    export = SimpleNamespace(
        status=lambda: {"schema": "stpd/local-model-export-operation-v2",
                        "operation": {"model_id": model_id, "model_type": "memory"}},
        verified_memory_for_registration=lambda *_args, **_kwargs: export_path,
        verified_memory_recipe_for_registration=lambda *_args, **_kwargs:
            "stage1a.dsimple.m2.k1.experimental.v2",
    )
    models = SimpleNamespace()
    registration = LocalModelRegistration(SimpleNamespace(), export, models)
    monkeypatch.setattr(registration_module, "require_local_models", lambda *_args: None)
    with pytest.raises(BoundaryError, match="managed_requires_confirmed_interaction_model"):
        registration.register(model_id, environment_kind="managed")


def test_registration_lock_serializes_same_model_and_malformed_binding_is_unavailable(
    registration, monkeypatch,
):
    service, _, model_id, root, _ = registration
    entered = threading.Event()
    release = threading.Event()
    original = registration_module.bind_text_menu_export
    outcomes: list[object] = []

    def held_binding(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(registration_module, "bind_text_menu_export", held_binding)

    def first() -> None:
        try:
            outcomes.append(service.register(model_id))
        except Exception as error:
            outcomes.append(error)

    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(BoundaryError, match="registration_in_progress"):
            service.register(model_id)
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(outcomes) == 1 and isinstance(outcomes[0], dict)
    assert service.register(model_id) == outcomes[0]
    entry = json.loads((service.models.private_root / REGISTRY).read_bytes())["policies"][0]
    manifest_path = service.models.private_root / entry["manifest"]
    manifest = json.loads(manifest_path.read_bytes())
    manifest["adapter"] = []
    atomic_json(manifest_path, manifest)
    assert service.status(model_id)["reason_code"] == "registration_metadata_invalid"


def test_workspace_switch_does_not_authorize_registration(
    registration, tmp_path: Path, completed,
):
    service, config, model_id, root, _ = registration
    other = store(tmp_path / "other-store")
    copy_artifact(completed[2], other, model_id)
    registry = tmp_path / "other-registry.sqlite"
    sync_registry(other, SQLiteRegistry(registry))
    switched = replace(config, research_workspace=LocalResearchWorkspaceConfig(
        tmp_path / "other-store", registry,
    ))
    service.config = switched
    service.export = LocalModelExport(switched)
    assert service.status(model_id)["reason_code"] == "workspace_changed"
    with pytest.raises(BoundaryError, match="workspace_changed"):
        service.register(model_id)


def test_unsafe_registry_does_not_authorize_registration(registration, tmp_path: Path):
    service, _, model_id, root, _ = registration
    try:
        (service.models.private_root / REGISTRY).symlink_to(tmp_path / "outside")
    except OSError as error:
        pytest.skip(f"symlink permission unavailable: {error}")
    assert service.status(model_id)["reason_code"] == "registration_metadata_invalid"
    with pytest.raises(BoundaryError):
        service.register(model_id)
    assert not (tmp_path / "outside").exists()


def test_http_exact_body_browser_guard_and_live_instance(
    registration, tmp_path: Path, monkeypatch,
):
    service, config, model_id, model_root, _ = registration
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    app.local_model_registration = service
    atomic_json(config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
    })
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def post(body: dict, *, origin: bool = True, csrf: str = ""):
        request = Request(root + "/api/local-model-registrations/register",
                          data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json",
                                   **({"Origin": root} if origin else {}),
                                   "X-CSRF-Token": csrf})
        return client.open(request)

    try:
        status_url = root + "/api/local-model-registrations/status?model_id=" + model_id
        with pytest.raises(HTTPError) as unauthenticated:
            client.open(status_url)
        assert unauthenticated.value.code == 401
        client.open(root + "/").close()
        with client.open(status_url) as response:
            before = json.load(response)
        assert before["status"] == "not_registered"
        for origin, csrf in ((False, before["csrf_token"]), (True, "wrong")):
            with pytest.raises(HTTPError) as denied:
                post({"model_id": model_id}, origin=origin, csrf=csrf)
            assert denied.value.code == 403
        with pytest.raises(HTTPError) as extra:
            post({"model_id": model_id, "manifest": "/tmp/fake"},
                 csrf=before["csrf_token"])
        assert extra.value.code == 400
        atomic_json(config.state_dir / "runtime.json", {
            "instance_id": "stale", "configuration_id": configuration_id(config),
        })
        with pytest.raises(HTTPError) as stale:
            post({"model_id": model_id}, csrf=before["csrf_token"])
        assert stale.value.code == 409
        atomic_json(config.state_dir / "runtime.json", {
            "instance_id": app.instance_id, "configuration_id": configuration_id(config),
        })
        original_validate = registration_module.validate_runtime_install

        def drifted_install(*_args):
            raise PackageIdentityError("synthetic missing Runtime package")

        monkeypatch.setattr(registration_module, "validate_runtime_install", drifted_install)
        with pytest.raises(HTTPError) as missing:
            post({"model_id": model_id}, csrf=before["csrf_token"])
        assert missing.value.code == 409
        assert json.load(missing.value)["error"] == "text_runtime_local_install_required"
        assert not (service.models.private_root / REGISTRY).exists()
        monkeypatch.setattr(registration_module, "validate_runtime_install", original_validate)
        with post({"model_id": model_id}, csrf=before["csrf_token"]) as response:
            registered = json.load(response)
        assert registered["status"] == "registered"
        with client.open(status_url) as response:
            after = json.load(response)
        assert after["selection_id"] == registered["selection_id"]
        assert "export_path" not in json.dumps(after)
        assert "modset" not in json.dumps(after)
        targets = []

        def managed_status(identity, *, environment_kind="native"):
            targets.append(("status", identity, environment_kind))
            return {"schema": SCHEMA, "model_id": identity,
                    "environment_kind": environment_kind, "status": "not_registered"}

        def managed_register(identity, *, environment_kind="native"):
            targets.append(("register", identity, environment_kind))
            return {"schema": SCHEMA, "model_id": identity,
                    "environment_kind": environment_kind, "status": "registered"}

        monkeypatch.setattr(service, "status", managed_status)
        monkeypatch.setattr(service, "register", managed_register)
        with client.open(status_url + "&environment_kind=managed") as response:
            managed = json.load(response)
        assert managed["environment_kind"] == "managed"
        assert targets == [("status", model_id, "managed")]
        with post({"model_id": model_id, "environment_kind": "managed"},
                  csrf=managed["csrf_token"]) as response:
            assert json.load(response)["environment_kind"] == "managed"
        assert targets[-1] == ("register", model_id, "managed")
        with pytest.raises(HTTPError) as duplicate:
            client.open(status_url + "&environment_kind=managed&environment_kind=native")
        assert duplicate.value.code == 400
        with pytest.raises(HTTPError) as empty:
            client.open(status_url + "&environment_kind=")
        assert empty.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


@pytest.mark.parametrize("target", [None, [], {}, "other"])
def test_registration_rejects_invalid_environment_target(target):
    with pytest.raises(BoundaryError, match="unsupported_environment_target"):
        registration_module._target_kind(target)
