"""Synthetic loopback browser auth/instance guards for the existing data owners."""

from __future__ import annotations

import json
import shutil
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
from spireagent.workbench import local_recording_import
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.developer_server import create_server
from spireagent.workbench.local_recording_import import LocalRecordingImporter
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
    assert saved["native_agent_support"]["product_entry_enabled"] is True
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


def test_native_import_intent_binds_exact_body_and_known_completion_before_original_IO(
    native_data_http, original, monkeypatch
):
    app, store, root, client, post = native_data_http
    client.open(root + "/").close()
    route = "/api/local-recordings/import/native-agent"
    request = {**body(original), "intent_id": "1" * 32}
    assert post(route, {**request, "intent_id": None})[0] == 409
    assert post(route, request)[0] == 200
    completed = settled(app.local_recording_import)
    assert completed["intent_id"] == request["intent_id"]
    assert "_native_agent_request" not in completed and "_native_agent_intents" not in completed
    assert str(original.directory) not in json.dumps(completed)
    identities = store.manifest_ids()
    monkeypatch.setattr(app.local_recording_import, "_native_agent_original", lambda *_args:
                        pytest.fail("same intent changed/known request cannot read originals"))
    assert post(route, {**request, "directory": "/not/an/original"}) == (
        409, {"error": "intent_payload_mismatch"})
    status, same = post(route, request)
    assert status == 200 and same["status"] == "completed"
    assert same["artifact_id"] == completed["artifact_id"]
    assert store.manifest_ids() == identities


def test_native_import_lost_publication_reply_reopens_same_intent_and_blocks_new_or_legacy(
    native_data_http, original, monkeypatch
):
    app, store, root, client, post = native_data_http
    client.open(root + "/").close()
    route = "/api/local-recordings/import/native-agent"
    request = {**body(original), "intent_id": "2" * 32}
    publish = ManifestArtifactStore.publish
    lost = False

    def reply_lost(self, manifest):
        nonlocal lost
        result = publish(self, manifest)
        if (manifest.parameters.value().get("schema") ==
                "stpd/native-agent-sampled-original-bundle-v1"
                and not lost):
            lost = True
            raise OSError("synthetic publication reply lost after owning immutable write")
        return result

    monkeypatch.setattr(ManifestArtifactStore, "publish", reply_lost)
    assert post(route, request)[0] == 200
    failed = settled(app.local_recording_import)
    assert failed["status"] == "published_index_unavailable"
    assert failed["intent_id"] == request["intent_id"]
    raw_id, identities = failed["artifact_id"], store.manifest_ids()
    producer = store.get_manifest(raw_id).producer
    app.local_recording_import = LocalRecordingImporter(
        app.config, app.local_recording_import.catalog)
    assert post(route, {**request, "intent_id": "3" * 32}) == (
        409, {"error": "original_intent_reconciliation_required"})
    assert post(route, body(original)) == (
        409, {"error": "original_intent_reconciliation_required"})
    assert post(route, {**request, "directory": "/changed"}) == (
        409, {"error": "intent_payload_mismatch"})
    assert post("/api/local-recordings/import", {"candidate_id": "a" * 64}) == (
        409, {"error": "original_intent_reconciliation_required"})
    assert post("/api/local-recordings/import", {"candidate_id": "a" * 64,
         "human_origin_attested": True}) == (
        409, {"error": "original_intent_reconciliation_required"})
    monkeypatch.setattr(ManifestArtifactStore, "publish", publish)
    assert post(route, request)[0] == 200
    completed = settled(app.local_recording_import)
    assert completed["intent_id"] == request["intent_id"] and completed["artifact_id"] == raw_id
    assert store.manifest_ids() == identities and store.get_manifest(raw_id).producer == producer


def test_native_import_old_intent_after_later_request_cannot_be_rebound_on_reopen(
    native_data_http, original, tmp_path, monkeypatch
):
    app, store, root, client, post = native_data_http
    client.open(root + "/").close()
    route = "/api/local-recordings/import/native-agent"
    first = {**body(original), "intent_id": "4" * 32}
    assert post(route, first)[0] == 200
    completed = settled(app.local_recording_import)
    copied = tmp_path / "same-original-copy"
    shutil.copytree(original.directory, copied)
    second = {**first, "directory": str(copied), "intent_id": "5" * 32}
    assert post(route, second)[0] == 200
    assert settled(app.local_recording_import)["artifact_id"] == completed["artifact_id"]
    identities = store.manifest_ids()
    app.local_recording_import = LocalRecordingImporter(
        app.config, app.local_recording_import.catalog)
    monkeypatch.setattr(app.local_recording_import, "_native_agent_original", lambda *_args:
                        pytest.fail("old intent exact binding precedes original IO"))
    assert post(route, {**first, "directory": str(copied)}) == (
        409, {"error": "intent_payload_mismatch"})
    assert post(route, first)[1]["intent_id"] == first["intent_id"]
    assert store.manifest_ids() == identities


def test_native_import_intent_capacity_rejects_without_evicting_known_ids(
    native_data_http, original, monkeypatch
):
    app, store, root, client, post = native_data_http
    client.open(root + "/").close()
    route = "/api/local-recordings/import/native-agent"
    monkeypatch.setattr(local_recording_import, "MAX_NATIVE_IMPORT_INTENTS", 1)
    request = {**body(original), "intent_id": "6" * 32}
    assert post(route, request)[0] == 200
    completed = settled(app.local_recording_import)
    identities = store.manifest_ids()
    assert post(route, {**request, "intent_id": "7" * 32}) == (
        409, {"error": "native_import_intent_capacity"})
    assert post(route, request)[1]["artifact_id"] == completed["artifact_id"]
    assert store.manifest_ids() == identities


def test_historical_unknown_snapshot_survives_current_legacy_overwrite_and_blocks_all_new_IO(
    native_data_http, original, monkeypatch
):
    app, store, root, client, post = native_data_http
    client.open(root + "/").close()
    route = "/api/local-recordings/import/native-agent"
    request = {**body(original), "intent_id": "8" * 32}
    assert post(route, request)[0] == 200
    completed = settled(app.local_recording_import)
    importer = app.local_recording_import
    journal = json.loads(importer.path.read_bytes())
    # Reproduce an old journal whose generic caller hid a historical pending
    # native intent. Reopening cannot relabel that unobserved writer as known.
    journal["_native_agent_intents"][request["intent_id"]]["status"] = "pending"
    importer.path.write_text(json.dumps({"schema": local_recording_import.SCHEMA,
        "status": "completed", "candidate_id": "a" * 64,
        "recording_type": "source3", "_native_agent_intents": journal["_native_agent_intents"]}))
    app.local_recording_import = LocalRecordingImporter(app.config, importer.catalog)
    monkeypatch.setattr(app.local_recording_import, "_fresh_candidate", lambda *_args:
                        pytest.fail("generic caller cannot perform original candidate IO"))
    monkeypatch.setattr(app.local_recording_import, "_native_agent_original", lambda *_args:
                        pytest.fail("new intent cannot perform native original IO"))
    assert post("/api/local-recordings/import", {"candidate_id": "a" * 64}) == (
        409, {"error": "original_intent_reconciliation_required"})
    assert post(route, {**request, "intent_id": "9" * 32}) == (
        409, {"error": "original_intent_reconciliation_required"})
    recovery = app.local_recording_import.status()["native_intent_recovery"]
    assert recovery == [{"intent_id": request["intent_id"], "status": "interrupted_unknown",
                         "cohort": request["cohort"], "relation_id": request["relation_id"]}]
    assert str(original.directory) not in json.dumps(app.local_recording_import.status())
    assert store.get_manifest(completed["artifact_id"]).kind == "evidence"
