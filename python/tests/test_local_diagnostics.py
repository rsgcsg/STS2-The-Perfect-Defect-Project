"""Private diagnostics through the real HTTP error boundary; no live App or compute."""
from __future__ import annotations

import json
import os
import stat
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.local_diagnostics import LocalDiagnostics, phase
from spireagent.workbench.developer_server import create_server

SENTINEL = "REQUEST_SECRET_9d2f"
ROUTES = [
    ("GET", "/api/local-recordings/store", "invalid_p1_query"),
    ("GET", "/api/remote-training/progress?job_id=" + "a" * 32,
     "invalid_remote_training_request"),
    ("GET", "/api/remote-training/status", "invalid_remote_training_request"),
    ("POST", "/api/local-workspace/curation/prepare", "invalid_local_curation_request"),
    ("POST", "/api/native-recording/command", "invalid_native_recording_command"),
    ("POST", "/api/local-recordings/maintenance", "invalid_p1_maintenance_policy"),
    ("POST", "/api/local-recordings/storage", "invalid_p1_storage_request"),
    ("POST", "/api/local-recordings/derive", "invalid_p1_derivation_request"),
    ("POST", "/api/local-recordings/preview", "invalid_local_preview_request"),
    ("POST", "/api/local-recordings/import", "invalid_local_import_request"),
    ("POST", "/api/local-datasets/preview", "invalid_local_dataset_request"),
    ("POST", "/api/p1-data/prepare", "invalid_p1_data_request"),
    ("POST", "/api/remote-training/start", "invalid_remote_training_request"),
    ("POST", "/api/local-training/start", "invalid_local_training_request"),
    ("POST", "/api/local-memory-evaluations/start", "invalid_local_memory_evaluation_request"),
    ("POST", "/api/local-model-exports/start", "invalid_local_model_export_request"),
    ("POST", "/api/local-model-registrations/register", "invalid_local_model_registration_request"),
    ("POST", "/api/local-workspace/managed/create", "invalid_managed_workspace_request"),
]


@pytest.mark.parametrize("method,path,code", ROUTES, ids=[r[1] for r in ROUTES])
@pytest.mark.parametrize("error_type", [OSError, ValueError, TypeError, BoundaryError])
def test_all_18_fallbacks_preserve_response_and_correlate_private_error(
    tmp_path, monkeypatch, method, path, code, error_type,
):
    def fail(*args, **kwargs):
        if error_type is BoundaryError:
            raise BoundaryError("test", "existing_error")
        raise error_type(SENTINEL)

    app = SimpleNamespace(config=SimpleNamespace(state_dir=tmp_path),
                          _require_training_instance=lambda: None,
                          local_research_workspace=fail,
                          local_datasets=SimpleNamespace(_selected=fail),
                          remote_training=SimpleNamespace(progress=fail, status=fail, command=fail))
    server = create_server(app)
    handler = server.RequestHandlerClass
    monkeypatch.setattr(handler, "authenticated_browser", lambda _: True)
    monkeypatch.setattr(handler, "control_client", lambda _: True)
    monkeypatch.setattr(handler, "browser_write", lambda _: True)
    original = handler.json_body

    def failed_body(self, *args, **kwargs):
        # Consume an actual secret-bearing request through the original parser.
        assert original(self, *args, **kwargs)["sentinel"] == SENTINEL
        fail()

    monkeypatch.setattr(handler, "json_body", failed_body)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}" + path,
                          data=json.dumps({"sentinel": SENTINEL}).encode(), method=method,
                          headers={"Content-Type": "application/json"})
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=5)
        response = json.loads(caught.value.read())
        if error_type is BoundaryError:
            assert caught.value.code == 409
            assert response == {"error": "existing_error"}
            assert not (tmp_path / "logs/errors.jsonl").exists()
        else:
            assert caught.value.code == 400
            assert response["error"] == code
            assert set(response) == {"error", "error_id"}
            records = [json.loads(line) for line in
                       (tmp_path / "logs/errors.jsonl").read_text().splitlines()]
            assert len(records) == 1
            record = records[0]
            assert record["error_id"] == response["error_id"]
            assert record["exception_type"] == error_type.__name__
            assert record["stack"] and record["stack"][-1]["function"] == "fail"
            assert record["path"].startswith("/api/") and record["stage"]
            safe_path = path.partition("?")[0]
            assert record["path"] == safe_path
            assert record["action"] == safe_path.rsplit("/", 1)[-1]
            assert record["elapsed_ms"] >= 0
            assert record["time_utc"] and record["operation_id"]
        assert SENTINEL not in "".join(p.read_text() for p in (tmp_path / "logs").glob("*"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_private_logs_rotate_and_keep_only_safe_frames(tmp_path, monkeypatch):
    from spireagent import local_diagnostics as owner

    monkeypatch.setattr(owner, "MAX_BYTES", 2048)
    log = LocalDiagnostics(tmp_path)
    for _ in range(40):
        try:
            raise ValueError(SENTINEL)
        except ValueError as error:
            log.error(error, path="/api/example", action="test", stage="validate")
    files = list((tmp_path / "logs").iterdir())
    assert len(files) == 3
    for file in files:
        assert file.stat().st_size <= 2048
        if os.name == "posix":
            assert stat.S_IMODE(file.stat().st_mode) == 0o600
        assert SENTINEL not in file.read_text()
        for line in file.read_text().splitlines():
            assert set(json.loads(line)["stack"][-1]) == {"file", "line", "function"}


def test_failed_phase_correlates_once_and_logging_failure_preserves_original(tmp_path):
    log = LocalDiagnostics(tmp_path)
    with log.operation("/api/remote-training/start", "start"):
        try:
            with phase("input.transfer"):
                raise TypeError(SENTINEL)
        except TypeError as error:
            identity = log.error(error)
    errors = [json.loads(line) for line in
              (tmp_path / "logs/errors.jsonl").read_text().splitlines()]
    phases = [json.loads(line) for line in
              (tmp_path / "logs/phases.jsonl").read_text().splitlines()]
    assert len(errors) == 1 and errors[0]["error_id"] == identity
    assert errors[0]["stage"] == "input.transfer"
    assert [p["event"] for p in phases] == ["phase_started", "phase_finished"]
    assert phases[-1]["error_id"] == identity and phases[-1]["duration_ms"] >= 0
    blocked = tmp_path / "not_a_directory"
    blocked.write_bytes(b"preserved")
    with (pytest.raises(ValueError, match=SENTINEL),
          LocalDiagnostics(blocked).operation("test", "test"), phase("failed")):
        raise ValueError(SENTINEL)
    assert blocked.read_bytes() == b"preserved"
