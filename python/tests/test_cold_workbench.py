"""Collection and recorded-report reads do not require the optional ML backend."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_local_evaluation import _token


def test_cold_collection_and_existing_report_without_ml_imports(tmp_path: Path) -> None:
    store, evaluation_id = _token(tmp_path / "sample")
    root = Path(__file__).resolve().parents[1]
    script = r'''
import builtins
import json
import sys
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, build_opener

real_import = builtins.__import__
def no_ml(name, *args, **kwargs):
    if name.split(".", 1)[0] in {"torch", "tokenizers", "safetensors", "transformers"}:
        raise AssertionError("ML import during read-only Workbench request: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = no_ml

from spireagent.storage.registry import SQLiteRegistry
from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.developer_server import Application, create_server

store_root, registry_path, state_root, evaluation_id = sys.argv[1:]
SQLiteRegistry(Path(registry_path))
config = ProjectConfig(Path(state_root), "", "", None, combination(),
    LocalResearchWorkspaceConfig(Path(store_root), Path(registry_path)))
app = Application(config)
server = create_server(app)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
client = build_opener(HTTPCookieProcessor(CookieJar()))
base = "http://127.0.0.1:" + str(server.server_port)
try:
    for route in ("/", "/api/status", "/api/local-models/status",
                  "/api/local-training/status", "/api/local-memory-evaluations/status",
                  "/api/member/collection-flow"):
        with client.open(base + route) as response:
            assert response.status == 200, route
    with client.open(base + "/api/local-workspace?kind=offline_evaluation") as response:
        assert json.load(response)["total"] == 1
    with client.open(base + "/api/local-workspace/evaluations/" + evaluation_id) as response:
        report = json.load(response)
    assert report["evaluation_id"] == evaluation_id
    assert report["validation_scope"] == "recorded_report_and_parent_identities"
    import spireagent.workbench.local_model_dependencies as dependencies
    dependencies.find_spec = lambda _name: None
    for operation in (lambda: app.local_model_registration.register("0" * 64),):
        try:
            operation()
        except BoundaryError as error:
            assert error.code == "local_models_extra_required"
        else:
            raise AssertionError("missing ML dependencies did not block mutation")
    assert not {"torch", "tokenizers", "safetensors", "transformers"} & sys.modules.keys()
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    app.close()
'''
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    interpreter = os.environ.get("STPD_TEST_COLD_INTERPRETER", sys.executable)
    child = subprocess.run(
        [interpreter, "-B", "-c", script, str(store.blobs.root),
         str(tmp_path / "registry.sqlite"), str(tmp_path / "state"), evaluation_id],
        env=env, cwd=root, text=True, capture_output=True, timeout=30,
    )
    assert child.returncode == 0, child.stderr


@pytest.mark.parametrize("profile", ["text-menu-v1", "text-menu-m2-v1", "text-menu-m2-v2"])
def test_completed_export_status_preserves_profile_without_importing_ml(tmp_path, profile):
    # Only the completed export's metadata is needed for this read. Weight and
    # binding verification must remain unavailable until the backend is present.
    script = r'''
import builtins
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

real_import = builtins.__import__
def no_ml(name, *args, **kwargs):
    if name.split(".", 1)[0] in {"torch", "tokenizers", "safetensors", "transformers"}:
        raise AssertionError("ML import while reading export status: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = no_ml
from spireagent.workbench import local_model_dependencies as dependencies
from spireagent.workbench.local_model_registration import LocalModelRegistration
from stpd.fullrun.memory_projection_config import (
    MemoryEpisodeProjectionConfig, v2_episode_projection_config,
)
dependencies.find_spec = lambda _: None
state, profile = Path(sys.argv[1]), sys.argv[2]
model_id = "a" * 64
operation = {"status": "completed", "model_id": model_id}
if profile != "text-menu-v1":
    operation["model_type"] = "memory"
    export = state / "model-exports" / model_id
    export.mkdir(parents=True)
    projection = (v2_episode_projection_config() if profile.endswith("v2") else
                  MemoryEpisodeProjectionConfig("stpd/memory-episode-projection-config-v1", 0))
    (export / "model.json").write_text(json.dumps({"projection_config": asdict(projection)}))
before = {str(path): path.read_bytes() for path in state.rglob("*") if path.is_file()}
service = LocalModelRegistration(SimpleNamespace(state_dir=state),
    SimpleNamespace(status=lambda: {"availability": "ready", "operation": operation}),
    SimpleNamespace())
result = service.status(model_id)
assert result["status"] == "unavailable", result
assert result["reason_code"] == "local_models_extra_required", result
assert result["runtime_profile"] == profile, result
assert operation["status"] == "completed"
assert {str(path): path.read_bytes() for path in state.rglob("*") if path.is_file()} == before
assert not {"torch", "tokenizers", "safetensors", "transformers"} & sys.modules.keys()
'''
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    child = subprocess.run(
        [os.environ.get("STPD_TEST_COLD_INTERPRETER", sys.executable), "-B", "-c", script,
         str(tmp_path), profile], env=env, cwd=root, text=True, capture_output=True, timeout=20,
    )
    assert child.returncode == 0, child.stderr
