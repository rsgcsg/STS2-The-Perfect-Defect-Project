"""Current recording maintenance maps into its existing authenticated Application API."""
from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.developer_server import Application, create_server


def test_control_token_maps_cooling_and_restore_to_original_recording_owner(tmp_path, monkeypatch):
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    app = Application(config)
    calls = []
    monkeypatch.setattr(app.p1_maintenance, "configure", lambda **body:
                        calls.append(("configure", body)) or {"status": "configured"})
    monkeypatch.setattr(app.local_recording_import, "start_p1_storage", lambda **body:
                        calls.append(("storage", body)) or {"status": "pending"})
    monkeypatch.setattr(app.local_recording_import, "reconcile_p1_storage", lambda:
                        calls.append(("reconcile", None)) or {"status": "completed"})
    monkeypatch.setattr(app.local_recording_import, "status", lambda:
                        {"schema": "stpd/local-import-v1", "status": "idle"})
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(route, body=None, token=None):
        headers = {"Authorization": "Bearer " + (token or app.control_token)}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(base + route, headers=headers,
                          data=None if body is None else json.dumps(body).encode())
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    try:
        policy = {"enabled": True, "peer": {"kind": "local"}, "projection_id": None,
                  "scope": {"origin": "declared_human"}, "cool_recent": True}
        assert call("/api/local-recordings/maintenance", policy)["status"] == "configured"
        for action in ("cool", "restore-expanded"):
            assert call("/api/local-recordings/storage", {
                "action": action, "artifact_id": "a" * 64, "peer": {"kind": "local"}})[
                    "status"] == "pending"
        assert call("/api/local-recordings/storage/reconcile", {})["status"] == "completed"
        assert "csrf_token" not in call("/api/local-recordings/import/status")
        assert [kind for kind, _ in calls] == ["configure", "storage", "storage", "reconcile"]
        with pytest.raises(HTTPError) as denied:
            call("/api/local-recordings/storage", {}, token="wrong")
        assert denied.value.code == 403
        with pytest.raises(HTTPError) as malformed:
            call("/api/local-recordings/maintenance", {**policy, "raw_path": "/no"})
        assert malformed.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=3)


def test_maintenance_read_and_summary_are_bounded_while_owner_lock_is_busy(
    tmp_path, monkeypatch, capsys,
):
    from spireagent.workbench import developer_server
    from spireagent.workbench.developer import atomic_json
    from spireagent.workbench.p1_recordings_cli import main

    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    app = Application(config)
    path = tmp_path / "project.json"
    atomic_json(path, config.to_dict())
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(developer_server, "running", lambda _: {
        "port": server.server_port, "control_token": app.control_token})
    owner_started, release = threading.Event(), threading.Event()

    def hold_owner_lock():
        with app.local_recording_import.lock:
            owner_started.set()
            release.wait(10)

    owner = threading.Thread(target=hold_owner_lock)
    owner.start()
    assert owner_started.wait(3)
    try:
        start = time.monotonic()
        assert main(["maintenance", "status", "--config", str(path)]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "busy"
        with urlopen(base + "/recording-summary", timeout=1) as response:
            html = response.read().decode()
        assert "在忙，稍后再查" in html
        assert "自动入库已停用" not in html
        assert time.monotonic() - start < 1
    finally:
        release.set()
        owner.join(timeout=3)
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=3)


@pytest.mark.parametrize("wrapped", [False, True])
def test_status_transport_timeout_is_busy_and_never_repeats_a_mutation(
    tmp_path, monkeypatch, capsys, wrapped,
):
    from urllib.error import URLError

    from spireagent.workbench import developer_server
    from spireagent.workbench.developer import atomic_json
    from spireagent.workbench.p1_recordings_cli import main

    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    path = tmp_path / "project.json"
    atomic_json(path, config.to_dict())
    monkeypatch.setattr(developer_server, "running", lambda _: {"port": 1234,
                                                               "control_token": "synthetic"})
    calls = []

    def request(url, **kwargs):
        calls.append(kwargs)
        raise URLError(TimeoutError()) if wrapped else TimeoutError()

    monkeypatch.setattr(developer_server, "_local_request", request)
    assert main(["maintenance", "status", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "busy"
    assert main(["maintenance", "disable", "--config", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "busy_try_later"
    assert len(calls) == 2 and all(call["method"] == "GET" for call in calls)


def test_summary_names_the_live_cache_step_even_after_next_tick(tmp_path):
    from spireagent.workbench.p1_cohort_intake import render_summary

    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    app = Application(config)
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(10))
    app.local_recording_import.thread = worker
    app.local_recording_import.operation.update(action="store-cache-trim", status="pending")
    app.local_recording_import._maintenance.update(
        status="operation_in_progress", policy={"enabled": True})
    worker.start()
    try:
        html = render_summary(config, app.local_recording_import.maintenance_status())
        assert "正在整理缓存" in html
        assert "游戏运行时不再开始下一步" in html
    finally:
        release.set()
        worker.join(timeout=3)
        app.close()


def test_summary_http_failure_and_explicit_refresh_of_failed_generation(tmp_path):
    from spireagent.workbench.p1_cohort_intake import DEFAULT_POLICY

    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    app = Application(config)
    service = app.p1_maintenance
    service.configure(True, None, cohort_policy=str(DEFAULT_POLICY))
    service._failed(ValueError("synthetic inspection failure"))
    app.local_recording_import._maintenance["cohort_failed_signature"] = "a" * 64
    app.local_recording_import._save()
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/recording-summary", timeout=5) as response:
            html = response.read().decode()
            assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        assert "class='status danger' role='alert'" in html
        assert "自动维护失败" in html
        assert "状态最后核对" in html
        assert "<script src='/assets/recording-summary.js' defer></script>" in html
        assert "<script>" not in html
        with urlopen(base + "/assets/recording-summary.js", timeout=5) as response:
            assert response.headers.get_content_type() == "text/javascript"
            assert response.headers.get_content_charset() == "utf-8"
            assert b"maintenance-stale" in response.read()
        request = Request(base + "/api/local-recordings/cohort/refresh", data=b"{}",
                          headers={"Authorization": "Bearer " + app.control_token,
                                   "Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            assert json.load(response)["status"] == "refresh_requested"
        assert "cohort_failed_signature" not in app.local_recording_import._maintenance
        assert app.local_recording_import._maintenance["policy"]["enabled"] is False
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=3)
