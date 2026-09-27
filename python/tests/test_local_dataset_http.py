"""Local Dataset browser API admits exact explicit commands without team login."""

from __future__ import annotations

import json
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.developer_server import Application, configuration_id, create_server


def test_dataset_routes_require_local_cookie_csrf_and_exact_body(
    tmp_path: Path, monkeypatch,
) -> None:
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config.state_dir.mkdir()
    path = tmp_path / "project.json"
    atomic_json(path, config.to_dict())
    app = Application(config, config_path=path)
    atomic_json(config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
    })
    calls: list[tuple] = []
    monkeypatch.setattr(app.local_datasets, "start_preview", lambda artifact, purpose, paired:
                        calls.append(("preview", artifact, purpose, paired)) or
                        {"schema": "test", "operation": {"status": "pending"}})
    monkeypatch.setattr(app.local_datasets, "start_publish", lambda token:
                        calls.append(("publish", token)) or
                        {"schema": "test", "operation": {"status": "pending"}})
    monkeypatch.setattr(app.account, "status", lambda: pytest.fail("team login not required"))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def post(route: str, body: dict, *, csrf: str | None = None,
             origin: str | None = root) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json",
                   "X-CSRF-Token": app.account.csrf if csrf is None else csrf}
        if origin is not None:
            headers["Origin"] = origin
        request = Request(root + route, data=json.dumps(body).encode(),
                          headers=headers, method="POST")
        try:
            with client.open(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.loads(error.read())

    valid = {"artifact_id": "a" * 64, "purpose": "training", "paired_training": None}
    try:
        assert post("/api/local-datasets/preview", valid)[0] == 403
        with pytest.raises(HTTPError) as denied:
            client.open(root + "/api/local-datasets/status")
        assert denied.value.code == 401
        client.open(root + "/").close()
        with client.open(root + "/api/local-datasets/status") as response:
            status = json.load(response)
        assert status["availability"] == "workspace_required"
        assert status["operation"]["status"] == "idle"
        assert status["csrf_token"] == app.account.csrf
        assert not (config.state_dir / "local-dataset-operation.json").exists()
        assert post("/api/local-datasets/preview", valid, csrf="wrong")[0] == 403
        assert post("/api/local-datasets/preview", valid,
                    origin="http://localhost:1")[0] == 403
        assert post("/api/local-datasets/preview", {**valid, "raw_path": "/tmp"})[0] == 400
        assert post("/api/local-datasets/preview", {"artifact_id": "a" * 64})[0] == 400
        assert post("/api/local-datasets/publish", {"preview_id": "b" * 32,
                                                      "purpose": "gold"})[0] == 400
        assert calls == []
        assert post("/api/local-datasets/preview", valid)[0] == 200
        assert post("/api/local-datasets/publish", {"preview_id": "b" * 32})[0] == 200
        assert calls == [("preview", "a" * 64, "training", None), ("publish", "b" * 32)]
        assert app.hub is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
