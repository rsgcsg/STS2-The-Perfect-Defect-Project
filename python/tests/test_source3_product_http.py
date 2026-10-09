"""Bounded loopback-only browser/native proof over actual synthetic data owners."""

from __future__ import annotations

import json
import sys
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import pytest
from source3_product_fixture import settled, setup
from test_native_workbench_access import paired_app

from spireagent.workbench.developer_server import create_server
from spireagent.workbench.native_workbench_access import CURRENT_SCHEMA, NativePair
from spireagent.workbench.native_workbench_api import COMMAND_SCHEMA, PREFIX
from stpd.ordered_source_spec import DEFAULT_VIEW


@pytest.fixture
def source3_http(tmp_path, monkeypatch):
    app, old_pair, _, secret, peer = paired_app(tmp_path, monkeypatch)
    objects = setup(tmp_path / "synthetic-data", monkeypatch, config=app.config)
    importer, dataset, catalog, *_ = objects
    app.local_recording_import = importer
    app.local_datasets, app.local_recordings = dataset, catalog
    monkeypatch.setattr(app.local_training, "start", lambda *_args, **_kwargs:
                        pytest.fail("saving/preparing cannot start training"))
    monkeypatch.setattr(app.account, "status", lambda: pytest.fail("no Hub/team endpoint"))
    monkeypatch.setattr(app, "native_recording_status", lambda: {
        "schema": "synthetic-status-only", "state": "closed", "availability": "unavailable",
    })
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    pair = NativePair(old_pair.runtime_instance_id, old_pair.workbench_instance_id,
                      old_pair.configuration_id, root + "/", old_pair.pair_id, old_pair.expires_at)
    def current_peer(method, route, *, headers):
        assert route == "/v1/workbench/native-status" and method == "GET"
        assert headers == pair.headers(secret)
        return {"schema": CURRENT_SCHEMA, **pair.to_dict(),
                "signature": pair.sign(secret, "native-current-v1")}
    monkeypatch.setattr(peer, "_request", current_peer)
    app.native_access.install(pair, peer)
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        yield app, objects, root, client, pair, secret
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def browser_post(app, root, client, path, payload, *, csrf=None, origin=None):
    request = Request(root + path, data=json.dumps(payload).encode(), method="POST", headers={
        "Content-Type": "application/json", "Origin": root if origin is None else origin,
        "X-CSRF-Token": app.account.csrf if csrf is None else csrf,
    })
    try:
        with client.open(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_source3_browser_import_prepare_routes_keep_auth_and_exact_body(source3_http):
    app, objects, root, client, _, _ = source3_http
    importer, dataset, _, candidate, store, _, _, tool = objects
    def post(path, body, **kw):
        return browser_post(app, root, client, path, body, **kw)
    route = "/api/local-recordings/import"
    assert post(route, {"candidate_id": candidate})[0] == 403
    client.open(root + "/").close()
    assert post(route, {"candidate_id": candidate}, csrf="wrong")[0] == 403
    assert post(route, {"candidate_id": candidate}, origin="http://localhost:1")[0] == 403
    assert post(route, {"candidate_id": candidate, "raw_path": "/private/raw"})[0] == 400
    assert post(route, {"candidate_id": candidate, "human_origin_attested": True})[0] == 409
    assert not tool.calls and not store.manifest_ids()
    assert post(route, {"candidate_id": candidate})[0] == 200
    saved = settled(importer)
    assert saved["status"] == "completed", saved
    preview_body = {"artifact_ids": [saved["artifact_id"]],
                    "cohort": "agent_protocol", "view": DEFAULT_VIEW}
    preview_route = "/api/local-datasets/source3-preview"
    assert post(preview_route, preview_body, csrf="wrong")[0] == 403
    assert post(preview_route, {**preview_body, "recipe": "arbitrary-trainer"})[0] == 400
    assert post(preview_route, {**preview_body, "cohort": "unknown"})[0] == 409
    assert dataset.thread is None
    assert post(preview_route, preview_body)[0] == 200
    preview = settled(dataset)
    assert preview["can_publish"] and preview["human_origin_verified"] is False
    assert post("/api/local-datasets/publish", {"preview_id": preview["preview_id"]})[0] == 200
    ready = settled(dataset)
    assert ready["status"] == "completed" and ready["actual_training_use"] is False
    assert app.hub is None and "torch" not in sys.modules


def test_source3_native_pair_actions_share_original_import_and_dataset_owners(source3_http):
    app, objects, root, _, pair, secret = source3_http
    importer, dataset, _, candidate, _, _, _, tool = objects
    def command(action, payload, *, headers=None):
        body = {"schema": COMMAND_SCHEMA, "request_id": "c" * 32, "payload": payload}
        request = Request(root + PREFIX + "/actions/" + action,
                          data=json.dumps(body).encode(), headers={
                              **(pair.headers(secret) if headers is None else headers),
                              "Content-Type": "application/json",
                          })
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.loads(error.read())
    status, refused = command("recordings.import", {"candidate_id": candidate}, headers={})
    assert status == 409 and refused["error"] == "native_pair_mismatch"
    assert not tool.calls
    request = Request(root + PREFIX + "/view?page=data", headers=pair.headers(secret))
    with urlopen(request, timeout=5) as response:
        view = json.load(response)
    capability = view["capabilities"]["source_aware_prepare"]
    assert capability["enabled"] is True and capability["reason"] is None
    assert capability["scope"] == "saved_source3_to_ordered_training_partition"
    assert capability["automatic_training"] is False
    form = next(action for action in view["capabilities"]["actions"]
                if action["action_id"] == "datasets.source3-preview")
    assert form["enabled"] is True
    assert capability["required_action"] == form["action_id"]
    _, accepted = command("recordings.import", {"candidate_id": candidate})
    assert accepted["status"] == "accepted", accepted
    saved = settled(importer)
    valid = {"artifact_ids": [saved["artifact_id"]],
             "cohort": "agent_protocol", "view": DEFAULT_VIEW}
    _, bad = command("datasets.source3-preview", {**valid, "source_path": "/private/raw"})
    assert bad["status"] == "rejected" and dataset.thread is None
    peer_binding = {**pair.headers(secret), "X-SpireAgent-Pair-ID": "d" * 32}
    assert command("datasets.source3-preview", valid, headers=peer_binding)[0] in {401, 409}
    _, accepted = command("datasets.source3-preview", valid)
    assert accepted["status"] == "accepted", accepted
    preview = settled(dataset)
    _, accepted = command("datasets.publish", {"preview_id": preview["preview_id"]})
    assert accepted["status"] == "accepted", accepted
    assert settled(dataset)["status"] == "completed"
    assert app.hub is None and "torch" not in sys.modules
