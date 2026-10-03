from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from spireagent.console.page import CSP, asset, render_shell
from spireagent.json_boundary import BoundaryError
from spireagent.workbench.console import LocalConsole, ProjectionCache, pagination
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.developer_server import Application, create_server
from spireagent.workbench.hub_client import HubClient


def config(tmp_path, *, delivery=True, hub=True):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return ProjectConfig(
        state,
        "https://hub.example" if hub else "",
        "http://127.0.0.1:15526",
        tmp_path / "delivery.json" if delivery else None,
        combination(),
    )


def _raw_post_response(
    port: int, headers: bytes, body: bytes = b"", *, path: str = "/api/console/collections",
) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
        client.settimeout(2)
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
        ).encode("ascii") + headers + b"\r\n" + body
        client.sendall(request)
        response = bytearray()
        while chunk := client.recv(4096):
            response.extend(chunk)
        return bytes(response)


@contextmanager
def _test_server(tmp_path):
    app = Application(config(tmp_path, delivery=False, hub=False))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_shared_shell_has_no_embedded_runtime_data_or_external_dependencies():
    for mode, api in [("local", "/api/console"), ("cloud", "/app/api")]:
        page = render_shell(mode, api, "https://hub.example")
        assert 'lang="zh-CN"' in page and 'data-api="' + api in page
        assert "训练与模型" in page
        assert ('data-view="local-home"' in page) == (mode == "local")
        assert ('data-view="campaigns"' in page) == (mode == "local")
        assert ('data-view="local-workspace"' in page) == (mode == "local")
        assert "http-equiv" not in page
        assert "localStorage" not in asset("console.js")[1].decode()
        assert "innerHTML" not in asset("console.js")[1].decode()
        assert "unsafe-inline" not in CSP
        for view in (
            "collections",
            "datasets",
            "research",
            "evaluations",
            "system",
        ):
            assert 'data-view="' + view in page
    assert asset("../developer.py") is None
    no_hub_local = render_shell("local", "/api/console")
    assert 'data-view="datasets"' not in no_hub_local
    assert 'data-view="research"' not in no_hub_local
    for view in (
        "local-home",
        "campaigns",
        "local-models",
        "local-workspace",
        "collections",
        "evaluations",
    ):
        assert f'data-view="{view}"' in no_hub_local
    assert 'data-view="local-models"' in render_shell("local", "/api/console")
    assert 'href="?view=local-home"' in render_shell("local", "/api/console")
    assert 'href="?view=local-home"' not in render_shell("cloud", "/app/api")
    assert 'data-view="local-models"' not in render_shell("cloud", "/app/api")
    assert 'data-view="local-workspace"' not in render_shell("cloud", "/app/api")
    assert asset("missing.js") is None
    with pytest.raises(ValueError):
        render_shell("cloud", "/arbitrary")
    with pytest.raises(ValueError):
        render_shell("local", "/api/console", "javascript:alert(1)")


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "limit=2&limit=3",
        "offset=hello",
        "path=secrets",
        "limit=1&offset=0&extra=2",
    ],
)
def test_pagination_rejects_unbounded_or_arbitrary_queries(query):
    with pytest.raises(BoundaryError, match="invalid_pagination"):
        pagination(query)


def test_typed_hub_pagination_does_not_relax_path_or_redirect_boundary(monkeypatch):
    client = HubClient("https://hub.example")
    monkeypatch.setenv("STPD_HUB_TOKEN", "device-credential")
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return BytesIO(b'{"items":[],"offset":100}')

    client.opener = Opener()
    assert client.get("/v1/console/collections", limit=25, offset=100)["offset"] == 100
    assert requests[0].full_url == "https://hub.example/v1/console/collections?limit=25&offset=100"
    with pytest.raises(BoundaryError):
        client.get("/v1/console/collections?token=other")
    with pytest.raises(BoundaryError):
        client.get("/v1/console/collections", limit=True)
    assert len(requests) == 1


def test_cache_preserves_receipt_and_observation_time_when_cloud_fails():
    cache = ProjectionCache(ttl=0, maximum=2)
    receipt = {"receipt_id": "a" * 32, "status": "verified"}
    value = cache.read(
        "record", lambda: {"receipt": receipt, "observed_at": "2026-01-01T00:00:00Z"}
    )
    value["receipt"]["status"] = "caller_must_not_mutate_cache"

    def unavailable():
        raise BoundaryError("hub", "unavailable")

    stale = cache.read("record", unavailable)
    assert stale["status"] == "stale" and stale["receipt"] == receipt
    assert stale["observed_at"] == "2026-01-01T00:00:00Z"
    assert "checked_at" in stale
    missing = cache.read("never_observed", unavailable)
    assert missing["status"] == "unavailable" and "receipt" not in missing
    assert "observed_at" not in missing
    recovered = cache.read(
        "record", lambda: {"receipt": receipt, "observed_at": "2026-01-02T00:00:00Z"}
    )
    assert "status" not in recovered and "error_code" not in recovered
    cache.read("third", lambda: {"value": 1})
    assert len(cache.values) == 2


def test_local_console_uses_isolated_owner_projection_and_keeps_dispositions(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    item = {
        "id": "a" * 64,
        "session_id": "session-a",
        "source": "/private/human/source",
        "content_id": "c" * 64,
        "status": "verified",
        "attempts": 5,
        "receipt": {"status": "verified"},
        "summary": {
            "counts": {"canonical": 569, "real_failures": 0, "cancelled": 1, "diagnostics": 55}
        },
    }
    owner = {
        "schema": "sts2.evidence/delivery-status-2",
        "sessions": [item],
        "total": 125,
        "counts": {"pending": 0, "verified": 125},
        "next_offset": 125,
        "quality": {"canonical": 999, "real_failures": 3},
    }
    calls = []

    def read(command, **kwargs):
        calls.append(command)
        assert command[:5] == [
            sys.executable,
            "-I",
            "-m",
            "sts2_platform_evidence.delivery_cli",
            "status",
        ]
        assert kwargs["env"] == {"STPD_HUB_TOKEN": "device-only"}
        assert kwargs["cwd"] == cfg.state_dir
        return subprocess.CompletedProcess(command, 0, json.dumps(owner).encode())

    monkeypatch.setattr("spireagent.workbench.console.subprocess.run", read)
    console = LocalConsole(
        cfg, None, lambda: {"STPD_HUB_TOKEN": "device-only"}, lambda: "running", {}
    )
    result = console.collections(25, 100)
    row = result["items"][0]
    assert result["total"] == 125 and result["offset"] == 100
    assert "source" not in row and "private" not in json.dumps(row)
    assert row["attempts"] == 5 and row["summary"]["counts"]["real_failures"] == 0
    assert row["research"]["status"] == "not_assessed"
    assert row["local_delivery"] is True
    assert "collection_context" not in row
    assert "--summary" in calls[0] and calls[0][-1] == "100"
    overview = console.overview()
    # Global quality must come from the owner aggregate, not the current page.
    assert overview["quality"]["canonical"] == 999
    assert overview["quality"]["real_failures"] == 3


def test_local_detail_requires_exact_remote_content_and_preserves_offline_receipt(
    tmp_path, monkeypatch
):
    console = LocalConsole(config(tmp_path), None, lambda: {}, lambda: "running", {})
    item = {
        "id": "a" * 64,
        "content_id": "b" * 64,
        "upload_id": "c" * 32,
        "status": "verified",
        "receipt": {"status": "verified"},
    }
    monkeypatch.setattr(console, "local_status", lambda **kwargs: {"sessions": [item]})
    monkeypatch.setattr(console, "remote", lambda *a, **k: {"item": {"content_id": "d" * 64}})
    with pytest.raises(BoundaryError, match="remote_identity_mismatch"):
        console.collection_detail("a" * 64)
    monkeypatch.setattr(console, "remote", lambda *a, **k: {"status": "unavailable"})
    result = console.collection_detail("a" * 64)
    assert result["item"]["status"] == "verified"
    assert result["item"]["remote"]["status"] == "unavailable"
    assert "collection_context" not in result["item"]


def test_local_detail_keeps_local_and_remote_delivery_observations(tmp_path, monkeypatch):
    console = LocalConsole(config(tmp_path), None, lambda: {}, lambda: "running", {})
    row = {
        "id": "a" * 64,
        "upload_id": "b" * 32,
        "content_id": "c" * 64,
        "status": "pending",
        "stage": "verification_pending",
    }
    monkeypatch.setattr(console, "local_status", lambda **kw: {"sessions": [row]})
    remote = {
        "item": {
            "upload_id": "b" * 32,
            "content_id": "c" * 64,
            "status": "verified",
            "receipt": {"receipt_id": "b" * 32},
            "collection_context": {"kind": "default", "name": "Daily"},
        },
        "observed_at": "2026-09-13T00:00:00Z",
    }
    monkeypatch.setattr(console, "remote", lambda *a, **kw: remote)
    value = console.collection_detail("a" * 64)["item"]
    assert value["status"] == "pending"
    assert value["remote"]["delivery_status"] == "verified"
    assert value["remote"]["receipt"]["receipt_id"] == "b" * 32
    assert value["collection_context"] == {"kind": "default", "name": "Daily"}
    remote["status"] = "stale"
    cached = console.collection_detail("a" * 64)["item"]
    assert cached["collection_context"] == value["collection_context"]
    assert cached["remote"]["status"] == "stale"
    assert cached["remote"]["observed_at"] == remote["observed_at"]
    remote["item"]["upload_id"] = "d" * 32
    with pytest.raises(BoundaryError, match="remote_identity_mismatch"):
        console.collection_detail("a" * 64)


def test_local_catalog_detail_uses_authorized_route_and_rejects_query(tmp_path, monkeypatch):
    console = LocalConsole(config(tmp_path), None, lambda: {}, lambda: "running", {})
    paths = []

    def remote(path):
        paths.append(path)
        return {"item": {"artifact_id": "a" * 64}}

    monkeypatch.setattr(console, "remote", remote)
    item = console.route("models/" + "a" * 64, "")["item"]
    assert item["local_download"] is False
    assert paths == ["models/" + "a" * 64]
    with pytest.raises(BoundaryError, match="unexpected_query"):
        console.route("models/" + "a" * 64, "limit=25")


def test_http_shell_and_assets_do_not_query_owners_or_accept_browser_mutations(
    tmp_path, monkeypatch
):
    app = Application(config(tmp_path, delivery=False, hub=False))
    monkeypatch.setattr(
        app, "snapshot", lambda: pytest.fail("shell must not block on owner queries")
    )
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(root + "/?view=collections", timeout=2) as response:
            page = response.read().decode()
            assert 'data-view="collections"' in page and app.control_token not in page
            assert response.headers["Content-Security-Policy"] == CSP
        with urlopen(root + "/", timeout=2) as response:
            page = response.read().decode()
            assert 'data-view="local-home"' in page
            assert 'href="?view=local-home"' in page
            assert 'data-cloud-url=""' in page
            assert "打开云端" not in page
        for name, kind in (("console.js", "text/javascript"), ("console.css", "text/css")):
            with urlopen(root + "/assets/" + name, timeout=2) as response:
                assert response.headers["Content-Type"].startswith(kind)
        with urlopen(root + "/api/console/collections?limit=25&offset=100", timeout=2) as response:
            data = json.load(response)
            assert data["status"] == "not_configured" and data["items"] == []
        for path in ("/api/console/collections", "/api/console/jobs", "/stop"):
            with pytest.raises(HTTPError) as error:
                urlopen(Request(root + path, data=b"{}"), timeout=2)
            assert error.value.code == 403
        browser_headers = {
            "Cookie": f"{app.account.cookie_name}={app.account.cookie}",
            "Origin": root,
            "X-CSRF-Token": app.account.csrf,
            "Content-Type": "application/json",
        }
        with pytest.raises(HTTPError) as error:
            urlopen(
                Request(
                    root + "/api/local-datasets/not-a-route",
                    data=b"{}", headers=browser_headers,
                ),
                timeout=2,
            )
        assert error.value.code == 404
        with pytest.raises(HTTPError) as error:
            urlopen(
                Request(root + "/assets/console.js", headers={"Host": "attacker.example"}),
                timeout=2,
            )
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_rejected_chunked_post_closes_without_running_action(tmp_path, monkeypatch):
    app = Application(config(tmp_path, delivery=False, hub=False))
    calls = []
    monkeypatch.setattr(
        app, "start_local_training", lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = b'{"dataset_id":"' + b"a" * 64 + b'"}'
    chunked_body = f"{len(body):X}\r\n".encode("ascii") + body + b"\r\n0\r\n\r\n"
    try:
        response = _raw_post_response(
            server.server_port, b"Transfer-Encoding: chunked\r\n", chunked_body,
            path="/api/local-training/start",
        )
        assert response == b""
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_rejected_post_closes_ambiguous_and_out_of_bound_framing(tmp_path):
    invalid_framing = (
        (b"Content-Length: invalid\r\n", b""),
        (b"Content-Length: -1\r\n", b""),
        (b"Content-Length: 65537\r\n", b""),
        (b"Content-Length: 0\r\nContent-Length: 0\r\n", b""),
        (b"Content-Length: 0\r\nContent-Length: 1\r\n", b""),
        (b"Transfer-Encoding: gzip\r\n", b""),
        (b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n", b""),
        (b"Transfer-Encoding: chunked\r\nContent-Length: 0\r\n", b""),
    )
    with _test_server(tmp_path) as server:
        for headers, body in invalid_framing:
            assert _raw_post_response(server.server_port, headers, body) == b""


def test_rejected_post_incomplete_content_length_closes_after_deadline(tmp_path):
    with _test_server(tmp_path) as server:
        started = time.monotonic()
        response = _raw_post_response(server.server_port, b"Content-Length: 2\r\n", b"{")
        elapsed = time.monotonic() - started
        assert response == b""
        assert 0.75 <= elapsed < 2


def test_rejected_post_drain_deadline_is_absolute(monkeypatch):
    from types import SimpleNamespace

    from spireagent.workbench import developer_server

    now = [0.0]
    time_module = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(developer_server, "time", time_module)

    class SlowStream:
        def read1(self, size):
            assert size > 0
            now[0] += 0.6
            return b"x"

    class RecordedSocket:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

    connection = RecordedSocket()
    complete = developer_server._discard_rejected_post_bytes(
        SlowStream(), connection, 3, deadline=1.0,
    )
    assert complete is False
    assert connection.timeouts == pytest.approx([1.0, 0.4])
    assert now[0] > 1.0


def test_upgrade_can_observe_and_stop_predecessor_but_cannot_start_it(
    tmp_path, monkeypatch, capsys
):
    from spireagent.workbench import developer_cli

    cfg = config(tmp_path, delivery=False, hub=False)
    previous = cfg.to_dict()
    previous["combination"]["evidence_source_revision"] = "f" * 40
    path = tmp_path / "project.json"
    path.write_text(json.dumps(previous))
    calls = []
    monkeypatch.setattr(
        developer_cli,
        "status_project",
        lambda c: calls.append(c.state_dir) or {"status": "running"},
    )
    monkeypatch.setattr(
        developer_cli, "stop_project", lambda c: calls.append(c.state_dir) or {"status": "stopping"}
    )
    for command in ("status", "stop"):
        assert developer_cli.main([command, "--config", str(path)]) == 0
        capsys.readouterr()
    assert calls == [cfg.state_dir, cfg.state_dir]
    for command in ("open", "serve", "download", "doctor"):
        assert developer_cli.main([command, "--config", str(path)]) == 1
        assert json.loads(capsys.readouterr().out)["code"] == "combination_changed_rerun_setup"
    previous["schema"] = "future-untrusted"
    path.write_text(json.dumps(previous))
    with pytest.raises(BoundaryError, match="unsupported_project_config"):
        ProjectConfig.load(path, require_current_combination=False)


def test_bad_upload_preference_does_not_prevent_workbench_diagnostics(tmp_path, monkeypatch):
    from spireagent.workbench.collection_flow import PREFERENCE_FILE

    app = Application(config(tmp_path))
    path = app.config.state_dir / PREFERENCE_FILE
    path.write_text("invalid preference")
    path.chmod(0o600)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not start uploader")
    )
    try:
        app.start_delivery()
        assert app.delivery_status()["status"] == "blocked"
        assert app.collection_flow.status()["stage"] == "upload_blocked"
        assert path.read_text() == "invalid preference"
    finally:
        app.close()
