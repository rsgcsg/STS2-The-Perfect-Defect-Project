"""Synthetic offline export from a completed text-menu model; no registration."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest
import torch
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import row

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import copy_artifact
from spireagent.workbench import local_model_export as export_module
from spireagent.workbench.developer import (
    LocalResearchWorkspaceConfig,
    ProjectConfig,
    atomic_json,
    combination,
)
from spireagent.workbench.developer_server import (
    Application,
    configuration_id,
    create_server,
    instance_lock,
)
from spireagent.workbench.local_model_export import (
    EXPORT_ROOT,
    OPERATION_FILE,
    SCHEMA,
    LocalModelExport,
)
from stpd.fullrun.text_menu_data import publish_text_menu_bc_view, publish_text_menu_source
from stpd.fullrun.token_inputs import load_token_inputs, publish_token_inputs
from stpd.policy.token_decision import TokenDecisionScorer
from stpd.workers.token_ranking import TokenConfig
from stpd.workers.token_worker import execute_tokens, prepare_token_run


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    folder = tmp_path_factory.mktemp("completed-text-menu-model")
    archive = store(folder / "store")
    original_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        source = publish_text_menu_source(archive, (row("one"), row("two", native=True)),
                                          PRODUCER)
        view = publish_text_menu_bc_view(archive, source.artifact_id, PRODUCER)
        tokens = publish_token_inputs(archive, view.artifact_id, "s", PRODUCER,
                                      max_tokens=4096)
        config = TokenConfig.text_menu_small_b(
            steps=1, width=16, heads=2, layers=1, feedforward=32, max_tokens=4096,
        )
        run = prepare_token_run(archive, load_token_inputs(archive, tokens.artifact_id),
                                config, PRODUCER)
        result = execute_tokens(archive, ObjectStoreRunReporter(archive, archive.blobs),
                                run.artifact_id, PRODUCER)
        model_id = archive.get_manifest(result.result_id).parent("model")
    finally:
        torch.set_num_threads(original_threads)
    registry_path = folder / "registry.sqlite"
    sync_registry(archive, SQLiteRegistry(registry_path))
    return folder / "store", registry_path, archive, model_id


def _config(tmp_path: Path, completed):
    store_dir, registry_path, _, _ = completed
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return ProjectConfig(state_dir, "", "", None, combination(),
                         LocalResearchWorkspaceConfig(store_dir, registry_path))


def _settle(service: LocalModelExport) -> dict:
    assert service.thread is not None
    service.thread.join(timeout=30)
    assert not service.thread.is_alive()
    return service.status()["operation"]


def test_exact_completed_model_exports_once_and_rechecks_standalone_bytes(
    tmp_path: Path, completed,
) -> None:
    config = _config(tmp_path, completed)
    _, registry_path, archive, model_id = completed
    before_manifests = archive.manifest_ids()
    before_registry = registry_path.read_bytes()
    service = LocalModelExport(config)
    assert service.status() == {"schema": SCHEMA, "operation": {"status": "idle"},
                                "availability": "ready"}
    assert not (config.state_dir / OPERATION_FILE).exists()
    assert not (config.state_dir / EXPORT_ROOT).exists()
    started = service.start(model_id)["operation"]
    assert started["status"] == "pending"
    assert service.start(model_id)["operation"]["operation_id"] == started["operation_id"]
    done = _settle(service)
    assert done["status"] == "completed", done
    assert done["model_id"] == model_id
    model = archive.get_manifest(model_id)
    assert done["payload_bytes"] == sum(item.size for item in model.payloads)
    destination = config.state_dir / EXPORT_ROOT / model_id
    scorer = TokenDecisionScorer(destination)
    assert scorer.artifact == model
    assert set(scorer.score_snapshot(row("one")["snapshot"])) == {
        item["action_id"] for item in row("one")["snapshot"]["menu_actions"]["actions"]
    }
    assert archive.manifest_ids() == before_manifests
    assert registry_path.read_bytes() == before_registry

    # A repeated click does not trust the old journal or overwrite the directory.
    existing = (destination / "weights.safetensors").read_bytes()
    repeated = service.start(model_id)["operation"]
    assert repeated["status"] == "pending"
    assert _settle(service)["status"] == "completed"
    assert (destination / "weights.safetensors").read_bytes() == existing
    (destination / "weights.safetensors").write_bytes(b"tampered")
    service.start(model_id)
    invalid = _settle(service)
    assert invalid["status"] == "failed"
    assert invalid["error_code"] in {"payload_size_mismatch", "payload_digest_mismatch"}
    assert (destination / "weights.safetensors").read_bytes() == b"tampered"


def test_restart_pending_is_read_only_and_requires_explicit_same_model(
    tmp_path: Path, completed,
) -> None:
    config = _config(tmp_path, completed)
    model_id = completed[3]
    operation = {"schema": SCHEMA, "status": "pending", "operation_id": "a" * 32,
                 "model_id": model_id, "store_root": str(completed[0])}
    atomic_json(config.state_dir / OPERATION_FILE, operation)
    before = (config.state_dir / OPERATION_FILE).read_bytes()
    service = LocalModelExport(config)
    assert service.status()["operation"] == {
        "status": "interrupted", "operation_id": "a" * 32, "model_id": model_id,
        "error_code": "previous_export_outcome_unknown",
    }
    assert (config.state_dir / OPERATION_FILE).read_bytes() == before
    assert not (config.state_dir / EXPORT_ROOT).exists()
    with instance_lock(config.state_dir / ".local-model-export.lock"):
        assert service.status()["operation"]["status"] == "pending"
    assert service.status()["operation"]["status"] == "interrupted"
    with pytest.raises(BoundaryError, match="previous_export_outcome_unknown"):
        service.start("b" * 64)
    service.start(model_id)
    assert _settle(service)["status"] == "completed"


def test_unsupported_metadata_and_unsafe_paths_never_export(
    tmp_path: Path, completed,
) -> None:
    config = _config(tmp_path, completed)
    _, _, archive, model_id = completed
    model = archive.get_manifest(model_id)
    wrong = replace(model, parameters=FrozenObject.of({
        **model.parameters.value(), "serializer": {"profile": "other"},
    }))
    archive.publish(wrong)
    service = LocalModelExport(config)
    with pytest.raises(BoundaryError, match="unsupported_model_for_offline_export"):
        service.start(wrong.artifact_id)
    pf_config = {**model.parameters.value()["config"], "recipe": "stage1a.b.pf.v2"}
    pf = replace(model, parameters=FrozenObject.of({
        **model.parameters.value(), "config": pf_config,
        "backbone": {"kind": "pf"},
    }))
    archive.publish(pf)
    with pytest.raises(BoundaryError, match="unsupported_model_for_offline_export"):
        service.start(pf.artifact_id)
    assert not (config.state_dir / EXPORT_ROOT).exists()
    (config.state_dir / ".local-model-export.lock").symlink_to(tmp_path / "outside")
    with pytest.raises(BoundaryError, match="operation_recovery_required"):
        service.start(model_id)
    assert not (tmp_path / "outside").exists()
    assert not (config.state_dir / OPERATION_FILE).exists()
    (config.state_dir / ".local-model-export.lock").unlink()
    (config.state_dir / EXPORT_ROOT).symlink_to(tmp_path / "outside")
    with pytest.raises(BoundaryError, match="unsafe_export_root"):
        service.start(model_id)
    assert not (tmp_path / "outside").exists()


def test_selected_store_change_cannot_relabel_old_completion(tmp_path: Path, completed) -> None:
    config = _config(tmp_path, completed)
    service = LocalModelExport(config)
    service.start(completed[3])
    assert _settle(service)["status"] == "completed"
    other_store = store(tmp_path / "other-store")
    copy_artifact(completed[2], other_store, completed[3])
    other_registry = tmp_path / "other-registry.sqlite"
    sync_registry(other_store, SQLiteRegistry(other_registry))
    switched = replace(config, research_workspace=LocalResearchWorkspaceConfig(
        tmp_path / "other-store", other_registry,
    ))
    observer = LocalModelExport(switched)
    status = observer.status()
    assert status["availability"] == "workspace_changed"
    assert status["operation"]["model_id"] == completed[3]
    observer.start(completed[3])
    assert _settle(observer)["status"] == "completed"
    assert observer.status()["availability"] == "ready"


def test_verified_export_with_lost_completion_journal_stays_unknown_until_explicit_recheck(
    tmp_path: Path, completed, monkeypatch,
) -> None:
    config = _config(tmp_path, completed)
    model_id = completed[3]
    original = export_module.atomic_json

    def fail_completion(path, value):
        if value.get("status") == "completed":
            raise OSError("synthetic journal failure")
        return original(path, value)

    monkeypatch.setattr(export_module, "atomic_json", fail_completion)
    service = LocalModelExport(config)
    service.start(model_id)
    assert _settle(service)["status"] == "interrupted"
    assert (config.state_dir / EXPORT_ROOT / model_id / "model.json").is_file()
    assert json.loads((config.state_dir / OPERATION_FILE).read_bytes())["status"] == "pending"
    monkeypatch.setattr(export_module, "atomic_json", original)
    restarted = LocalModelExport(config)
    restarted.start(model_id)
    assert _settle(restarted)["status"] == "completed"


def test_http_requires_cookie_origin_csrf_and_live_configuration(
    tmp_path: Path, completed,
) -> None:
    config = _config(tmp_path, completed)
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    atomic_json(config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
    })
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    model_id = completed[3]

    def post(*, origin: bool = True, csrf: str = ""):
        request = Request(root + "/api/local-model-exports/start",
                          data=json.dumps({"model_id": model_id}).encode(),
                          headers={"Content-Type": "application/json", "Host":
                                   f"127.0.0.1:{server.server_port}",
                                   **({"Origin": root} if origin else {}),
                                   "X-CSRF-Token": csrf})
        return client.open(request)

    try:
        with pytest.raises(HTTPError) as unauthenticated:
            client.open(root + "/api/local-model-exports/status")
        assert unauthenticated.value.code == 401
        client.open(root + "/").close()
        with client.open(root + "/api/local-model-exports/status") as response:
            status = json.load(response)
        assert status["operation"] == {"status": "idle"}
        assert "csrf_token" in status
        assert not (config.state_dir / OPERATION_FILE).exists()
        for origin, csrf in ((False, status["csrf_token"]), (True, "wrong")):
            with pytest.raises(HTTPError) as denied:
                post(origin=origin, csrf=csrf)
            assert denied.value.code == 403
        assert not (config.state_dir / OPERATION_FILE).exists()
        invalid_body = Request(root + "/api/local-model-exports/start",
                               data=json.dumps({"model_id": model_id,
                                                "destination": "/tmp/other"}).encode(),
                               headers={"Content-Type": "application/json",
                                        "Origin": root,
                                        "X-CSRF-Token": status["csrf_token"]})
        with pytest.raises(HTTPError) as invalid:
            client.open(invalid_body)
        assert invalid.value.code == 400
        assert not (config.state_dir / OPERATION_FILE).exists()
        atomic_json(config.state_dir / "runtime.json", {
            "instance_id": "other", "configuration_id": configuration_id(config),
        })
        with pytest.raises(HTTPError) as stale:
            post(csrf=status["csrf_token"])
        assert stale.value.code == 409
        assert not (config.state_dir / OPERATION_FILE).exists()
        atomic_json(config.state_dir / "runtime.json", {
            "instance_id": app.instance_id, "configuration_id": configuration_id(config),
        })
        with post(csrf=status["csrf_token"]) as response:
            started = json.load(response)
        assert started["operation"]["status"] == "pending"
        assert _settle(app.local_model_export)["status"] == "completed"
        with client.open(root + "/api/local-model-exports/status") as response:
            done = json.load(response)
        assert done["operation"]["model_id"] == model_id
        assert "store_root" not in json.dumps(done)
        assert "model-exports" not in json.dumps(done)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
