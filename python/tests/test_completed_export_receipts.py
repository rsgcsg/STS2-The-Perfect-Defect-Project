"""Completed verification survives the active slot; UNKNOWN never becomes proof."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.local_model_export import (
    COMPLETED_ROOT,
    OPERATION_FILE,
    SCHEMA,
    LocalModelExport,
)


def service(tmp_path, monkeypatch):
    config = ProjectConfig(tmp_path, "", "", None, combination())
    owner = LocalModelExport(config)
    monkeypatch.setattr(owner, "_workspace", lambda: SimpleNamespace(
        store=SimpleNamespace(blobs=SimpleNamespace(root=tmp_path / "store"))))
    return owner


def operation(tmp_path, identity="a" * 64, *, status="completed"):
    return {"schema": SCHEMA, "status": status, "model_id": identity,
            "operation_id": "b" * 32, "store_root": str(tmp_path / "store"),
            **({"payload_bytes": 17} if status == "completed" else {})}


def test_known_completion_is_archived_before_another_active_model(tmp_path, monkeypatch):
    owner = service(tmp_path, monkeypatch)
    first = operation(tmp_path)
    atomic_json(owner._path(), {**first, "status": "pending"})
    owner._finish(first["operation_id"], status="completed", payload_bytes=17)
    assert json.loads((tmp_path / COMPLETED_ROOT / ("a" * 64 + ".json")).read_bytes()) == first
    second = operation(tmp_path, "c" * 64, status="pending")
    atomic_json(owner._path(), second)
    assert owner.status_for_model("a" * 64)["operation"]["status"] == "completed"
    assert owner.status_for_model("c" * 64)["operation"]["status"] == "interrupted"
    assert owner._read() == second
    assert not (tmp_path / COMPLETED_ROOT / ("c" * 64 + ".json")).exists()


def test_pending_cannot_be_archived_or_loaded_as_completed(tmp_path, monkeypatch):
    owner = service(tmp_path, monkeypatch)
    pending = operation(tmp_path, status="pending")
    atomic_json(tmp_path / OPERATION_FILE, pending)
    with pytest.raises(BoundaryError, match="completed_export_required"):
        owner._archive_completed(pending)
    root = tmp_path / COMPLETED_ROOT
    root.mkdir()
    atomic_json(root / ("c" * 64 + ".json"), operation(tmp_path, "c" * 64, status="pending"))
    with pytest.raises(BoundaryError, match="operation_recovery_required"):
        owner.status_for_model("c" * 64)
    assert owner._read() == pending


def test_archive_cannot_cross_model_or_hide_workspace_change(tmp_path, monkeypatch):
    owner = service(tmp_path, monkeypatch)
    first = operation(tmp_path)
    owner._archive_completed(first)
    monkeypatch.setattr(owner, "_workspace", lambda: SimpleNamespace(
        store=SimpleNamespace(blobs=SimpleNamespace(root=tmp_path / "different-store"))))
    assert owner.status_for_model("a" * 64)["availability"] == "workspace_changed"
    atomic_json(tmp_path / COMPLETED_ROOT / ("a" * 64 + ".json"), operation(tmp_path, "c" * 64))
    with pytest.raises(BoundaryError, match="operation_recovery_required"):
        owner.status_for_model("a" * 64)


def test_failed_completion_publication_never_archives_pending(tmp_path, monkeypatch):
    import spireagent.workbench.local_model_export as module

    owner = service(tmp_path, monkeypatch)
    pending = operation(tmp_path, status="pending")
    atomic_json(owner._path(), pending)
    monkeypatch.setattr(module, "atomic_json", lambda *args: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        owner._finish(pending["operation_id"], status="completed", payload_bytes=17)
    assert owner._read() == pending
    assert not (tmp_path / COMPLETED_ROOT).exists()


def test_completed_model_reuse_cannot_hide_another_pending_export(tmp_path, monkeypatch):
    owner = service(tmp_path, monkeypatch)
    first = operation(tmp_path)
    owner._archive_completed(first)
    pending = operation(tmp_path, "c" * 64, status="pending")
    atomic_json(owner._path(), pending)
    with pytest.raises(BoundaryError, match="previous_export_outcome_unknown"):
        owner.start("a" * 64)
    assert owner._read() == pending


def test_cached_activation_uses_the_existing_cross_process_export_lock(tmp_path, monkeypatch):
    from spireagent.workbench.developer_server import instance_lock
    from spireagent.workbench.local_model_export import LOCK_FILE

    owner = service(tmp_path, monkeypatch)
    first = operation(tmp_path)
    atomic_json(owner._path(), first)
    archive = SimpleNamespace(blobs=SimpleNamespace(root=tmp_path / "store"),
        get_manifest=lambda _: SimpleNamespace(parameters=SimpleNamespace(value=lambda: {
            "schema": "stpd/stage1a-light-action-m0-public-model-v1"})))
    monkeypatch.setattr(owner, "_source", lambda _: archive)
    monkeypatch.setattr(owner, "verified_for_registration",
                        lambda *_: pytest.fail("shared lock lost"))
    with (instance_lock(tmp_path / LOCK_FILE),
          pytest.raises(BoundaryError, match="export_in_progress")):
        owner.start("a" * 64)
    assert owner._read() == first
