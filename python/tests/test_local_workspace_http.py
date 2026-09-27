from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import (
    LocalResearchWorkspaceConfig,
    ProjectConfig,
    atomic_json,
    combination,
)
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.managed_local_workspace import REGISTRATION_NAME, ROOT_NAME


def test_local_workspace_page_api_uses_local_cookie_without_cloud_session(
    tmp_path: Path, monkeypatch
) -> None:
    store_dir = tmp_path / "research-store"
    artifact_store = ManifestArtifactStore(LocalBlobStore(store_dir))
    payload = artifact_store.put_bytes("records", b"private-record-payload")
    manifest = Manifest(
        "dataset",
        Producer("local/fixture", "a" * 40, "b" * 64),
        payloads=(payload,),
        parameters=FrozenObject.of({"name": "offline fixture"}),
    )
    artifact_store.publish(manifest)
    registry_path = tmp_path / "registry.sqlite"
    registry = SQLiteRegistry(registry_path)
    sync_registry(artifact_store, registry, frozenset({manifest.artifact_id}))
    config = ProjectConfig(
        tmp_path / "state",
        "",
        "",
        None,
        combination(),
        LocalResearchWorkspaceConfig(store_dir, registry_path),
    )
    app = Application(config)
    monkeypatch.setattr(
        app.account,
        "status",
        lambda: pytest.fail("local workspace browsing must not request cloud identity"),
    )
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        assert app.hub is None
        with pytest.raises(HTTPError) as denied:
            client.open(root + "/api/local-workspace")
        assert denied.value.code == 401
        client.open(root + "/").close()
        before_db = registry_path.read_bytes()
        with client.open(root + "/api/local-workspace?kind=dataset&limit=10&offset=0") as response:
            page = json.load(response)
        assert page["source"] == "configured_local_artifact_store"
        assert page["total"] == 1
        assert page["items"][0]["artifact_id"] == manifest.artifact_id
        with client.open(
            root + "/api/local-workspace/artifacts/" + manifest.artifact_id
        ) as response:
            detail = json.load(response)
            status = response.status
        assert status == 200
        assert detail["payloads"][0]["sha256"] == payload.sha256
        assert registry_path.read_bytes() == before_db
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_unconfigured_local_workspace_endpoint_is_authenticated_and_side_effect_free(
    tmp_path: Path,
) -> None:
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    app = Application(config)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        client.open(root + "/").close()
        with client.open(root + "/api/local-workspace") as response:
            value = json.load(response)
        assert value["status"] == "not_configured"
        assert value["requires_cloud_account"] is False
        assert "local workspace" in value["entry"]
        assert not (config.state_dir / ROOT_NAME).exists()
        assert not (tmp_path / "research-store").exists()
        assert not (tmp_path / "registry.sqlite").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_invalid_registered_registry_is_reported_without_initializing_it(tmp_path: Path) -> None:
    store_dir = tmp_path / "existing-store"
    store_dir.mkdir()
    registry_path = tmp_path / "uninitialized.sqlite"
    registry_path.touch()
    config = ProjectConfig(
        tmp_path / "state",
        "",
        "",
        None,
        combination(),
        LocalResearchWorkspaceConfig(store_dir, registry_path),
    )
    app = Application(config)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        client.open(root + "/").close()
        with client.open(root + "/api/local-workspace") as response:
            value = json.load(response)
        assert value["status"] == "unavailable"
        assert value["error_code"] == "unsupported_or_uninitialized_cache"
        assert registry_path.read_bytes() == b""
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_managed_workspace_post_is_local_only_idempotent_and_persists_across_restart(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    config = ProjectConfig(
        state_dir,
        "",
        "",
        None,
        combination(),
        None,
    )
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    runtime_path = state_dir / "runtime.json"
    atomic_json(runtime_path, {
        "instance_id": app.instance_id,
        "configuration_id": configuration_id(config),
    })
    original_config = config_path.read_bytes()
    original_runtime = runtime_path.read_bytes()
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        assert app.hub is None
        with pytest.raises(HTTPError) as denied:
            client.open(root + "/api/local-workspace/managed")
        assert denied.value.code == 401
        client.open(root + "/").close()
        with client.open(root + "/api/local-workspace/managed") as response:
            initial = json.load(response)
        assert initial["status"] == "not_created"
        assert not (state_dir / ROOT_NAME).exists()

        def post(body: bytes = b"{}", *, origin: str | None = None, csrf: str | None = None,
                 host: str | None = None):
            headers = {
                "Content-Type": "application/json",
                "Cookie": f"{app.account.cookie_name}={app.account.cookie}",
                "X-CSRF-Token": csrf if csrf is not None else app.account.csrf,
            }
            if origin is not None:
                headers["Origin"] = origin
            if host is not None:
                headers["Host"] = host
            request = Request(
                root + "/api/local-workspace/managed/create",
                data=body,
                headers=headers,
                method="POST",
            )
            try:
                with client.open(request) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.loads(error.read())

        expected_origin = root
        assert post(origin="http://localhost:1")[0] == 403
        assert post(origin=expected_origin, csrf="wrong")[0] == 403
        assert post(b'{"path":"/tmp/forbidden"}', origin=expected_origin)[0] == 400
        wrong_host = f"localhost:{server.server_port}"
        assert post(body=b"{}", origin=expected_origin, host=wrong_host)[0] == 403
        assert not (state_dir / REGISTRATION_NAME).exists()

        runtime_path.write_text("[]", encoding="utf-8")
        malformed_runtime = post(origin=expected_origin)
        assert malformed_runtime[0] == 409
        assert malformed_runtime[1]["error"] == "running_instance_unavailable"
        assert not (state_dir / ROOT_NAME).exists()
        atomic_json(runtime_path, {
            "instance_id": "f" * 32,
            "configuration_id": configuration_id(config),
        })
        stale_instance = post(origin=expected_origin)
        assert stale_instance[0] == 409
        assert stale_instance[1]["error"] == "running_configuration_mismatch"
        assert not (state_dir / ROOT_NAME).exists()
        atomic_json(runtime_path, {
            "instance_id": app.instance_id,
            "configuration_id": configuration_id(config),
        })

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: post(origin=expected_origin), range(2)))
        assert all(status == 200 for status, _ in results)
        identity = results[0][1]["workspace_id"]
        assert results[1][1]["workspace_id"] == identity
        assert (state_dir / ROOT_NAME / identity / "workspace.json").is_file()
        assert config_path.read_bytes() == original_config
        assert runtime_path.read_bytes() == original_runtime

        with client.open(root + "/api/local-workspace/managed") as response:
            value = json.load(response)
        assert value["status"] == "ready"
        assert value["workspace_id"] == identity
        with client.open(root + "/api/local-workspace?limit=10&offset=0") as response:
            inventory = json.load(response)
        assert inventory["total"] == 0
        assert inventory["source"] == "configured_local_artifact_store"
        managed_root = state_dir / ROOT_NAME / identity
        managed_store = ManifestArtifactStore(LocalBlobStore(managed_root / "store"))
        payload = managed_store.put_bytes("records", b"synthetic managed fixture")
        manifest = Manifest(
            "dataset",
            Producer("local/fixture", "a" * 40, "b" * 64),
            payloads=(payload,),
            parameters=FrozenObject.of({"name": "managed fixture"}),
        )
        managed_store.publish(manifest)
        sync_registry(managed_store, SQLiteRegistry(managed_root / "registry.sqlite"))
        with client.open(root + "/api/local-workspace?limit=10&offset=0") as response:
            inventory = json.load(response)
        assert inventory["total"] == 1
        assert inventory["items"][0]["artifact_id"] == manifest.artifact_id
        artifact_url = root + "/api/local-workspace/artifacts/" + manifest.artifact_id
        with client.open(artifact_url) as response:
            artifact = json.load(response)
        assert artifact["artifact_id"] == manifest.artifact_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()

    # The real server creates a fresh per-process cookie and runtime binding on
    # restart; the state-directory registration retains the same empty store.
    restarted = Application(config, config_path=config_path)
    atomic_json(runtime_path, {
        "instance_id": restarted.instance_id,
        "configuration_id": configuration_id(config),
    })
    second_server = create_server(restarted)
    second_thread = threading.Thread(target=second_server.serve_forever, daemon=True)
    second_thread.start()
    second_root = f"http://127.0.0.1:{second_server.server_port}"
    second_client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        second_client.open(second_root + "/").close()
        status_url = second_root + "/api/local-workspace/managed"
        with second_client.open(status_url) as response:
            value = json.load(response)
        assert value["status"] == "ready"
        assert value["workspace_id"] == identity
        with second_client.open(second_root + "/api/local-workspace?limit=25&offset=0") as response:
            inventory = json.load(response)
        assert inventory["total"] == 1
        assert inventory["items"][0]["artifact_id"] == manifest.artifact_id
        artifact_url = second_root + "/api/local-workspace/artifacts/" + manifest.artifact_id
        with second_client.open(artifact_url) as response:
            artifact = json.load(response)
        assert artifact["artifact_id"] == manifest.artifact_id
    finally:
        second_server.shutdown()
        second_server.server_close()
        second_thread.join(timeout=3)
        restarted.close()


def test_legacy_workspace_has_priority_and_blocks_managed_creation(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    legacy_store_dir = tmp_path / "legacy-store"
    legacy_store = ManifestArtifactStore(LocalBlobStore(legacy_store_dir))
    legacy_registry_path = tmp_path / "legacy-registry.sqlite"
    sync_registry(legacy_store, SQLiteRegistry(legacy_registry_path))
    config = ProjectConfig(
        state_dir,
        "",
        "",
        None,
        combination(),
        LocalResearchWorkspaceConfig(legacy_store_dir, legacy_registry_path),
    )
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    runtime_path = state_dir / "runtime.json"
    atomic_json(runtime_path, {
        "instance_id": app.instance_id,
        "configuration_id": configuration_id(config),
    })
    config_before = config_path.read_bytes()
    runtime_before = runtime_path.read_bytes()
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        client.open(root + "/").close()
        with client.open(root + "/api/local-workspace/managed") as response:
            status = json.load(response)
        assert status["status"] == "legacy_workspace_configured"
        with client.open(root + "/api/local-workspace?limit=10&offset=0") as response:
            inventory = json.load(response)
        assert inventory["total"] == 0
        request = Request(
            root + "/api/local-workspace/managed/create",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "Cookie": f"{app.account.cookie_name}={app.account.cookie}",
                "Origin": root,
                "X-CSRF-Token": app.account.csrf,
            },
            method="POST",
        )
        with pytest.raises(HTTPError) as rejected:
            client.open(request)
        assert rejected.value.code == 409
        assert json.loads(rejected.value.read())["error"] == "legacy_workspace_configured"
        assert not (state_dir / REGISTRATION_NAME).exists()
        assert config_path.read_bytes() == config_before
        assert runtime_path.read_bytes() == runtime_before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_corrupt_managed_registration_is_visible_and_never_recreated(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registration = state_dir / REGISTRATION_NAME
    registration.write_text('{"schema":"corrupt"}', encoding="utf-8")
    original = registration.read_bytes()
    config = ProjectConfig(state_dir, "", "", None, combination(), None)
    app = Application(config)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        client.open(root + "/").close()
        with client.open(root + "/api/local-workspace/managed") as response:
            status = json.load(response)
        assert status["status"] == "unavailable"
        assert status["error_code"] == "registration_invalid"
        with client.open(root + "/api/local-workspace?limit=10&offset=0") as response:
            inventory = json.load(response)
        assert inventory["status"] == "unavailable"
        assert registration.read_bytes() == original
        assert not (state_dir / ROOT_NAME).exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
