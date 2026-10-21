"""Current recording maintenance maps into its existing authenticated Application API."""
from __future__ import annotations

import json
import threading
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
