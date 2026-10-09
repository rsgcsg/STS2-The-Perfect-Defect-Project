"""Synthetic loopback browser auth/instance guards for the existing data owners."""

from __future__ import annotations

import json
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest
from metadata_import_guard import no_torch_imports as no_torch_imports
from test_native_agent_product_data import settled
from test_native_agent_sampled_source import original as original
from test_native_workbench_access import paired_app

from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.developer_server import create_server
from spireagent.workbench.managed_local_workspace import ROOT_NAME, create_managed_workspace
from stpd.native_agent_sampled_source_spec import FIXTURE_COHORT, RELATION_SPEC


@pytest.fixture
def native_data_http(tmp_path, monkeypatch):
    app, *_ = paired_app(tmp_path, monkeypatch)
    workspace = create_managed_workspace(app.config.state_dir)
    store = ManifestArtifactStore(LocalBlobStore(app.config.state_dir / ROOT_NAME /
        workspace["workspace_id"] / "store", create=False))
    monkeypatch.setattr(app.local_training, "start", lambda *_args, **_kwargs:
                        pytest.fail("data preparation cannot start training"))
    monkeypatch.setattr(app.account, "status", lambda: pytest.fail("no Hub/account endpoint"))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def post(route, body, *, csrf=None, origin=None):
        request = Request(root + route, data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "Origin": root if origin is None else origin,
            "X-CSRF-Token": app.account.csrf if csrf is None else csrf})
        try:
            with client.open(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        yield app, store, root, client, post
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def body(original):
    return {"directory": str(original.directory), "cohort": FIXTURE_COHORT,
            "relation_id": RELATION_SPEC["id"]}


def test_browser_import_preview_publish_keep_auth_exact_body_and_closed_UI(
    native_data_http, original
):
    app, store, root, client, post = native_data_http
    route = "/api/local-recordings/import/native-agent"
    request = body(original)
    assert post(route, request)[0] == 403
    client.open(root + "/").close()
    assert post(route, request, csrf="wrong")[0] == 403
    assert post(route, request, origin="http://localhost:1")[0] == 403
    assert post(route, {**request, "human_origin_attested": True})[0] == 400
    assert post(route, {**request, "relation_id": "arbitrary-import"})[0] == 409
    assert not store.manifest_ids() and app.local_recording_import.thread is None
    assert post(route, request)[0] == 200
    saved = settled(app.local_recording_import)
    assert saved["status"] == "completed", saved
    assert saved["native_agent_support"]["product_entry_enabled"] is False
    preview_route = "/api/local-datasets/native-agent-preview"
    payload = {"artifact_ids": [saved["artifact_id"]]}
    assert post(preview_route, payload, csrf="wrong")[0] == 403
    assert post(preview_route, {**payload, "recipe": "arbitrary-trainer"})[0] == 400
    assert post(preview_route, payload)[0] == 200
    preview = settled(app.local_datasets)
    assert preview["status"] == "preview_ready" and preview["accepted_labels"] == 3
    assert post("/api/local-datasets/publish", {"preview_id": preview["preview_id"]})[0] == 200
    published = settled(app.local_datasets)
    assert published["status"] == "completed" and published["actual_training_use"] is False
    assert app.hub is None and app.models.client is None and app.models.process is None


def test_running_instance_drift_rejects_before_import_or_preview_thread(native_data_http, original):
    app, store, root, client, post = native_data_http
    client.open(root + "/").close()
    atomic_json(app.config.state_dir / "runtime.json",
                {"instance_id": "different-instance", "configuration_id": "different-config"})
    status, result = post("/api/local-recordings/import/native-agent", body(original))
    assert status == 409 and result["error"] == "running_configuration_mismatch"
    assert post("/api/local-datasets/native-agent-preview", {"artifact_ids": ["a" * 64]})[0] == 409
    assert not store.manifest_ids()
    assert app.local_recording_import.thread is None and app.local_datasets.thread is None
