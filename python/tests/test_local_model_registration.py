"""Synthetic exact-export registration; never touches a live Connector or game."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import replace
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest
from test_artifact_store_v1 import store
from test_local_model_export import _config, _settle

from spireagent.json_boundary import BoundaryError
from spireagent.package_identity import PackageIdentityError
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.store import copy_artifact
from spireagent.workbench import local_model_registration as registration_module
from spireagent.workbench.developer import ROOT, LocalResearchWorkspaceConfig, atomic_json
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import (
    REGISTRY,
    SCHEMA,
    VERBS,
    LocalModelRegistration,
    _requirements,
)
from spireagent.workbench.local_models import LocalModelService
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
    (root / ".local").mkdir()
    atomic_json(root / ".local/text-menu-runtime-v1.json", {
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
    config_file = root / entry["config"]
    manifest_file = root / entry["manifest"]
    bound_config, manifest = validate(root, config_file, manifest_file)
    assert bound_config["model_id"] == model_id
    assert bound_config["export_path"] == str(config.state_dir / "model-exports" / model_id)
    assert manifest["requirements"]["environment"]["host_kind"] == "test"
    assert manifest["support"]["action_verbs"] == list(VERBS)
    assert manifest["claims"]["full_run"] is False
    assert manifest["policy"]["architecture"] == "stage1a.dsimple.s.v1"
    assert entry["label"] == "本机文字菜单 D-Simple " + model_id[:8]
    assert len(json.loads((root / REGISTRY).read_bytes())["policies"]) == 1


def test_existing_b_model_registration_preserves_its_architecture(
    tmp_path: Path, completed_b, monkeypatch,
) -> None:
    service, _, model_id, root, models = _registration(tmp_path, completed_b, monkeypatch)
    result = service.register(model_id)
    entry = models.selection(result["selection_id"])
    _, manifest = validate(root, root / entry["config"], root / entry["manifest"])
    assert manifest["policy"]["architecture"] == "stage1a.b.s.v2"
    assert entry["label"] == "本机文字菜单 B " + model_id[:8]


def test_registration_budget_prevents_append_after_expensive_binding(
        registration, monkeypatch) -> None:
    service, _, model_id, root, _ = registration
    clock = [0.0]
    monkeypatch.setattr(registration_module, "monotonic", lambda: clock[0])
    binder = registration_module.bind_text_menu_export

    def slow_capabilities(_sdk, *, deadline):
        assert deadline == 22.0
        clock[0] += 11.0
        return _caps()

    def slow_binding(*args, **kwargs):
        result = binder(*args, **kwargs)
        clock[0] += 12.0
        return result

    monkeypatch.setattr(service, "_capabilities", slow_capabilities)
    monkeypatch.setattr(registration_module, "bind_text_menu_export", slow_binding)
    with pytest.raises(BoundaryError, match="registration_timeout"):
        service.register(model_id)
    assert not (root / REGISTRY).exists()
    assert list((root / ".local/model-registrations").iterdir()) == []


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
    old = (root / REGISTRY).read_bytes()
    changed = _caps()
    changed["game"]["modset"]["fingerprint"] = "new-modset"
    monkeypatch.setattr(service, "_capabilities", lambda _sdk, **_kwargs: changed)
    second = service.register(model_id)
    assert second["selection_id"] != first["selection_id"]
    assert len(json.loads((root / REGISTRY).read_bytes())["policies"]) == 2
    assert old != (root / REGISTRY).read_bytes()
    (root / "stpd/policy/token_port.py").write_text("# changed source\n")
    assert service.status(model_id)["status"] == "not_registered"
    assert service.status(model_id)["reason_code"] == "source_binding_changed"
    rebound = service.register(model_id)
    assert rebound["status"] == "registered"
    assert rebound["selection_id"] not in {first["selection_id"], second["selection_id"]}
    assert len(json.loads((root / REGISTRY).read_bytes())["policies"]) == 3


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
    assert not (root / REGISTRY).exists()
    monkeypatch.setattr(service, "_capabilities", lambda _sdk, **_kwargs: _caps())
    original = registration_module.atomic_json

    def failed_registry(path, value):
        if path == root / REGISTRY:
            raise OSError("synthetic atomic-write failure")
        return original(path, value)

    monkeypatch.setattr(registration_module, "atomic_json", failed_registry)
    with pytest.raises(BoundaryError, match="registration_write_failed"):
        service.register(model_id)
    assert not (root / REGISTRY).exists()
    monkeypatch.setattr(registration_module, "atomic_json", original)
    assert service.register(model_id)["status"] == "registered"
    assert config.state_dir.is_dir()


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
    entry = json.loads((root / REGISTRY).read_bytes())["policies"][0]
    manifest_path = root / entry["manifest"]
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
        (root / REGISTRY).symlink_to(tmp_path / "outside")
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
        assert not (model_root / REGISTRY).exists()
        monkeypatch.setattr(registration_module, "validate_runtime_install", original_validate)
        with post({"model_id": model_id}, csrf=before["csrf_token"]) as response:
            registered = json.load(response)
        assert registered["status"] == "registered"
        with client.open(status_url) as response:
            after = json.load(response)
        assert after["selection_id"] == registered["selection_id"]
        assert "export_path" not in json.dumps(after)
        assert "modset" not in json.dumps(after)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
