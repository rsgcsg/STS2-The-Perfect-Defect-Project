"""Browser proof and GET purity for explicit in-place preparation."""

from __future__ import annotations

import json
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import (
    LocalResearchWorkspaceConfig,
    ProjectConfig,
    atomic_json,
    combination,
)
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.inplace_curation import LEDGER_NAME, PLAN_NAME


def test_explicit_prepare_http_requires_browser_and_does_not_write_on_get(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    ManifestArtifactStore(LocalBlobStore(store_dir))
    registry_path = tmp_path / "registry.sqlite"
    SQLiteRegistry(registry_path)
    state = tmp_path / "state"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination(),
                           LocalResearchWorkspaceConfig(store_dir, registry_path))
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    atomic_json(state / "runtime.json", {"instance_id": app.instance_id,
                                         "configuration_id": configuration_id(config)})
    server = create_server(app)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    url = root + "/api/local-workspace/curation"

    def post(*, csrf: str, body: bytes = b"{}") -> int:
        request = Request(url + "/prepare", data=body, method="POST",
                          headers={"Content-Type": "application/json", "Origin": root,
                                   "X-CSRF-Token": csrf})
        try:
            with client.open(request) as response:
                return response.status
        except HTTPError as error:
            return error.code

    try:
        try:
            client.open(url).close()
            raise AssertionError("unauthenticated GET was accepted")
        except HTTPError as error:
            assert error.code == 401
        client.open(root + "/").close()
        with client.open(url) as response:
            status = json.load(response)
        assert status["status"] == "preparation_required"
        assert status["csrf_token"] == app.account.csrf
        assert not (store_dir / PLAN_NAME).exists()
        assert not (store_dir / LEDGER_NAME).exists()
        assert post(csrf="invalid") == 403
        assert post(csrf=app.account.csrf, body=b'{"path":"/tmp/other"}') == 400
        assert post(csrf=app.account.csrf) == 200
        assert app.local_curation_preparation.thread is not None
        app.local_curation_preparation.thread.join(timeout=10)
        assert app.local_curation_preparation.status()["status"] == "ready"
        with client.open(url) as response:
            ready = json.load(response)
        assert ready["status"] == "ready"
        assert ready["unknown_dataset_count"] == 0
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        app.close()
