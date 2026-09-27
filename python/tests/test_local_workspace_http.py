from __future__ import annotations

import json
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, build_opener

import pytest

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import (
    LocalResearchWorkspaceConfig,
    ProjectConfig,
    combination,
)
from spireagent.workbench.developer_server import Application, create_server


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
        assert "research_workspace" in value["entry"]
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
