"""Synthetic archived Managed reports through the existing local purpose owner."""

from __future__ import annotations

import json
import sqlite3
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest
from test_managed_text_menu_import import _archive

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.store import copy_artifact
from spireagent.workbench import local_managed_source as managed_source
from spireagent.workbench import managed_local_workspace as workspace
from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.local_environment import LocalEnvironmentService
from spireagent.workbench.local_managed_source import LocalManagedSourceService


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir()
    selected = workspace.create_managed_workspace(state)
    config = ProjectConfig(state, "", "", None, combination())
    environment = LocalEnvironmentService(config)
    archive, report_id, _ = _archive(tmp_path / "synthetic")
    report_store = environment._report_store(create=True)
    assert report_store is not None
    copy_artifact(archive, report_store, report_id)
    from test_artifact_store_v1 import PRODUCER

    monkeypatch.setattr(managed_source, "source_identity", lambda _: PRODUCER)
    service = LocalManagedSourceService(config, environment)
    owner = selected["curation_owner"]
    assert owner is not None
    return service, environment, report_id, owner


def _uses(owner) -> tuple[int, int]:
    with sqlite3.connect(owner.path) as db:
        return (db.execute("SELECT count(*) FROM curation_uses").fetchone()[0],
                db.execute("SELECT count(*) FROM curation_source_uses").fetchone()[0])


def test_archived_report_import_is_idempotent_and_get_does_not_write_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, environment, report_id, owner = _setup(tmp_path, monkeypatch)
    assert environment.status()["session"]["status"] == "idle"
    first = service.import_report(report_id, "training")
    again = service.import_report(report_id, "training")
    assert first == again
    assert first["report_artifact_id"] == report_id
    assert first["actor"] == "unverified"
    assert first["scope"] == "engineering_control"
    assert first["status"] == "admitted"
    assert owner.ledger.dataset(first["artifact_id"]) == (
        "training", {first["split_run_id"]})
    assert service.binding(first["artifact_id"])["curation_purpose"] == "training"
    assert service.binding(first["artifact_id"])["status"] == "admitted"
    assert _uses(owner) == (0, 0)
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (0,)


def test_conflicting_purpose_rejected_without_new_pending_or_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, report_id, owner = _setup(tmp_path, monkeypatch)
    first = service.import_report(report_id, "test")
    with pytest.raises(BoundaryError, match="reservation_identity_conflict"):
        service.import_report(report_id, "training")
    assert service.binding(first["artifact_id"])["curation_purpose"] == "test"
    assert _uses(owner) == (0, 0)
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (0,)


def test_registry_failure_keeps_pending_and_explicit_retry_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, report_id, owner = _setup(tmp_path, monkeypatch)
    original = managed_source.sync_registry

    def broken(*_args, **_kwargs):
        raise BoundaryError("local_managed_source", "synthetic_registry_failure")

    monkeypatch.setattr(managed_source, "sync_registry", broken)
    with pytest.raises(BoundaryError, match="synthetic_registry_failure"):
        service.import_report(report_id, "training")
    with owner.transaction() as db:
        pending = db.execute("SELECT artifact,status FROM local_source_pending").fetchone()
    assert pending is not None and pending[1] == "published"
    assert service.binding(pending[0])["curation_purpose"] == "training"
    assert service.binding(pending[0])["status"] == "recovery_required"
    assert _uses(owner) == (0, 0)
    monkeypatch.setattr(managed_source, "sync_registry", original)
    retried = service.import_report(report_id, "training")
    assert retried["artifact_id"] == pending[0]
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (0,)


def test_http_import_requires_browser_csrf_exact_body_and_running_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination())
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    atomic_json(state / "runtime.json", {
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
    })
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(app.local_managed_sources, "import_report", lambda report, purpose:
                        calls.append((report, purpose)) or {"artifact_id": "b" * 64})
    monkeypatch.setattr(app.local_managed_sources, "binding", lambda source:
                        {"artifact_id": source, "curation_purpose": "training"})
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def post(body: dict[str, str], *, csrf: bool = True) -> int:
        request = Request(root + "/api/local-environment/reports/import",
                          data=json.dumps(body).encode(), method="POST",
                          headers={"Content-Type": "application/json", "Origin": root,
                                   **({"X-CSRF-Token": app.account.csrf} if csrf else {})})
        try:
            with client.open(request) as response:
                return response.status
        except HTTPError as error:
            return error.code

    try:
        valid = {"report_artifact_id": "a" * 64, "purpose": "training"}
        assert post(valid) == 403
        with pytest.raises(HTTPError) as unauthenticated:
            client.open(root + "/api/local-managed-sources/binding/" + "b" * 64)
        assert unauthenticated.value.code == 401
        client.open(root + "/").close()
        assert post(valid, csrf=False) == 403
        assert post({**valid, "host_package_pin": "forged"}) == 400
        assert post(valid) == 200
        with client.open(root + "/api/local-managed-sources/binding/" + "b" * 64) as response:
            assert json.load(response)["curation_purpose"] == "training"
        assert calls == [("a" * 64, "training")]
        atomic_json(state / "runtime.json", {
            "instance_id": "different", "configuration_id": configuration_id(config),
        })
        assert post(valid) == 409
        assert calls == [("a" * 64, "training")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_unknown_report_and_bad_purpose_do_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, report_id, owner = _setup(tmp_path, monkeypatch)
    with pytest.raises(BoundaryError):
        service.import_report("a" * 64, "training")
    with pytest.raises(BoundaryError, match="managed_purpose_invalid"):
        service.import_report(report_id, "gold")
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (0,)
    assert _uses(owner) == (0, 0)


def test_old_unpinned_report_and_related_test_split_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, environment, report_id, owner = _setup(tmp_path, monkeypatch)
    first = service.import_report(report_id, "training")
    report_store = environment._report_store(create=True)
    assert report_store is not None
    old = environment.report(report_id)
    del old["host_package_pin"]
    original = report_store.get_manifest(report_id)
    unpinned = Manifest("analysis", original.producer, parents=original.parents,
                        payloads=(report_store.put_bytes("report", json_bytes(old)),),
                        parameters=original.parameters)
    report_store.publish(unpinned)
    with pytest.raises(BoundaryError, match="archived_identity_unavailable"):
        service.import_report(unpinned.artifact_id, "test")
    another, related_id, _ = _archive(tmp_path / "related", session="later-session",
                                       scenario="renamed-scenario")
    copy_artifact(another, report_store, related_id)
    with pytest.raises(BoundaryError, match="managed_split_purpose_overlap"):
        service.import_report(related_id, "test")
    assert service.binding(first["artifact_id"])["status"] == "admitted"
    assert _uses(owner) == (0, 0)
