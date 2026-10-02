"""Authenticated browser command admits only exact explicit local import DTOs."""

from __future__ import annotations

import json
import threading
from email.message import Message
from http.cookiejar import CookieJar
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.developer_server import Application, configuration_id, create_server


def test_local_import_http_requires_browser_proof_and_explicit_attestation(
    tmp_path: Path, monkeypatch,
) -> None:
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config.state_dir.mkdir()
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    assert app.local_recording_import.catalog is not app.local_recordings
    atomic_json(config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
    })
    calls = []
    monkeypatch.setattr(app.local_recording_import, "start", lambda candidate, attested:
                        calls.append((candidate, attested)) or {"status": "pending"})
    monkeypatch.setattr(app.account, "status", lambda: pytest.fail("cloud login not required"))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def post(body: dict, *, origin: str | None = root, csrf: str | None = None,
             host: str | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json",
                   "X-CSRF-Token": app.account.csrf if csrf is None else csrf}
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        request = Request(root + "/api/local-recordings/import",
                          data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with client.open(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        valid = {"candidate_id": "a" * 64, "human_origin_attested": True}
        assert post(valid)[0] == 403
        client.open(root + "/").close()
        with client.open(root + "/api/local-recordings/import/status") as response:
            status = json.load(response)
        assert status["status"] == "idle"
        assert status["csrf_token"] == app.account.csrf
        assert post(valid, origin="http://localhost:1")[0] == 403
        assert post(valid, csrf="wrong")[0] == 403
        assert post(valid, host=f"localhost:{server.server_port}")[0] == 403
        assert post({"candidate_id": "a" * 64})[0] == 400
        assert post({**valid, "human_origin_attested": False})[0] == 409
        assert post({**valid, "path": "/private/raw"})[0] == 400
        assert calls == []
        assert post(valid) == (200, {"status": "pending"})
        assert calls == [("a" * 64, True)]
        assert app.hub is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_member_archive_import_http_requires_exact_authenticated_id_request(
    tmp_path: Path, monkeypatch,
) -> None:
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config.state_dir.mkdir()
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    atomic_json(config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
    })
    calls = []
    monkeypatch.setattr(
        app.local_recording_import, "start_member_archive",
        lambda export, file, attested: calls.append((export, file, attested))
        or {"status": "pending"},
    )
    monkeypatch.setattr(app.account, "status", lambda: pytest.fail("cloud login not required"))
    class ServerProbe:
        def __init__(self, _address, handler_class):
            self.handler_class = handler_class
            self.server_port = 8765

    monkeypatch.setattr("spireagent.workbench.developer_server.ThreadingHTTPServer", ServerProbe)
    server = create_server(app)

    class ConnectionProbe:
        def __init__(self) -> None:
            self.timeout = None

        def gettimeout(self):
            return self.timeout

        def settimeout(self, timeout):
            self.timeout = timeout

    def post(body: dict, *, headers: dict | None = None) -> tuple[int, dict]:
        payload = json.dumps(body).encode("utf-8")
        handler = object.__new__(server.handler_class)
        handler.path = "/api/local-recordings/import-member-archive"
        handler.server = server
        handler.connection = ConnectionProbe()
        handler.rfile = BytesIO(payload)
        handler.close_connection = False
        handler.headers = Message()
        authorization = {
            "Host": "127.0.0.1:8765",
            "Cookie": f"{app.account.cookie_name}={app.account.cookie}",
            "Origin": "http://127.0.0.1:8765",
            "X-CSRF-Token": app.account.csrf,
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        }
        authorization.update(headers or {})
        for name, value in authorization.items():
            handler.headers[name] = value
        response = []
        handler.respond = lambda status, payload: response.append((status, payload))
        handler.do_POST()
        assert len(response) == 1
        return response[0][0], json.loads(response[0][1])

    try:
        valid = {"export_id": "a" * 64, "file_id": "b" * 64,
                 "human_origin_attested": True}
        assert post(valid, headers={"Host": "localhost:8765"})[0] == 403
        assert post(valid, headers={"Origin": "http://localhost:8765"})[0] == 403
        assert post(valid, headers={"X-CSRF-Token": "wrong"})[0] == 403
        assert post(valid, headers={"Cookie": ""})[0] == 403
        assert post({**valid, "path": "/private/raw"})[0] == 400
        assert post({**valid, "human_origin_attested": False})[0] == 409
        assert calls == []
        assert post(valid) == (200, {"status": "pending"})
        assert calls == [("a" * 64, "b" * 64, True)]
    finally:
        app.close()


def test_member_archive_import_http_routes_over_loopback_with_browser_proof(
    tmp_path: Path, monkeypatch,
) -> None:
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config.state_dir.mkdir()
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    atomic_json(config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
    })
    calls = []
    monkeypatch.setattr(
        app.local_recording_import, "start_member_archive",
        lambda export, file, attested: calls.append((export, file, attested))
        or {"status": "pending"},
    )
    monkeypatch.setattr(app.account, "status", lambda: pytest.fail("cloud login not required"))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def post(body: dict, *, origin: str | None = root, csrf: str | None = None,
             opener=None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json",
                   "X-CSRF-Token": app.account.csrf if csrf is None else csrf}
        if origin is not None:
            headers["Origin"] = origin
        request = Request(
            root + "/api/local-recordings/import-member-archive",
            data=json.dumps(body).encode(), headers=headers, method="POST",
        )
        try:
            with (opener or client).open(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        valid = {"export_id": "a" * 64, "file_id": "b" * 64,
                 "human_origin_attested": True}
        assert post(valid)[0] == 403
        client.open(root + "/").close()
        empty_client = build_opener(HTTPCookieProcessor(CookieJar()))
        assert post(valid, opener=empty_client)[0] == 403
        assert post(valid, origin="http://localhost:1")[0] == 403
        assert post(valid, csrf="wrong")[0] == 403
        assert post({**valid, "extra": "rejected"})[0] == 400
        assert post({"export_id": "a" * 64, "file_id": "b" * 64})[0] == 400
        assert calls == []
        assert post(valid) == (200, {"status": "pending"})
        assert calls == [("a" * 64, "b" * 64, True)]
        assert app.hub is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_local_preview_http_requires_explicit_authenticated_post(
    tmp_path: Path, monkeypatch,
) -> None:
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config.state_dir.mkdir()
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    atomic_json(config.state_dir / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
    })
    calls: list[object] = []
    monkeypatch.setattr(app.local_recording_preview, "start", lambda artifact:
                        calls.append(artifact) or {"status": "pending"})
    monkeypatch.setattr(app.account, "status", lambda: pytest.fail("cloud login not required"))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def post(body: dict, *, origin: str | None = root, csrf: str | None = None) -> int:
        headers = {"Content-Type": "application/json",
                   "X-CSRF-Token": app.account.csrf if csrf is None else csrf}
        if origin is not None:
            headers["Origin"] = origin
        request = Request(root + "/api/local-recordings/preview",
                          data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with client.open(request) as response:
                return response.status
        except HTTPError as error:
            return error.code

    try:
        valid = {"artifact_id": "a" * 64}
        assert post(valid) == 403
        client.open(root + "/").close()
        with client.open(root + "/api/local-recordings/preview/status") as response:
            assert json.load(response)["status"] == "idle"
        assert calls == []
        assert post(valid, origin="http://localhost:1") == 403
        assert post(valid, csrf="wrong") == 403
        assert post({"artifact_id": "a" * 64, "path": "/private/raw"}) == 400
        assert calls == []
        assert post(valid) == 200
        assert calls == ["a" * 64]
        assert app.hub is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
