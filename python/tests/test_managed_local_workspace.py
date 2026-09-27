from __future__ import annotations

from pathlib import Path

import pytest

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench import managed_local_workspace as managed


def test_managed_workspace_is_idempotent_owned_and_readable_after_restart(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    first = managed.create_managed_workspace(state_dir)
    second = managed.create_managed_workspace(state_dir)

    assert first["workspace_id"] == second["workspace_id"]
    assert first["created_at"] == second["created_at"]
    assert first["workspace"].inventory()["total"] == 0
    workspace_dir = state_dir / managed.ROOT_NAME / first["workspace_id"]
    assert (workspace_dir / "workspace.json").is_file()
    assert (workspace_dir / "store").is_dir()
    assert (workspace_dir / "registry.sqlite").is_file()

    store = ManifestArtifactStore(LocalBlobStore(workspace_dir / "store"))
    payload = store.put_bytes("records", b"synthetic-only")
    manifest = Manifest(
        "dataset",
        Producer("local/test", "a" * 40, "b" * 64),
        payloads=(payload,),
        parameters=FrozenObject.of({"name": "synthetic fixture"}),
    )
    store.publish(manifest)
    sync_registry(store, SQLiteRegistry(workspace_dir / "registry.sqlite"))

    reopened = managed.inspect_managed_workspace(state_dir)
    assert reopened["workspace_id"] == first["workspace_id"]
    assert reopened["workspace"].inventory()["items"][0]["artifact_id"] == manifest.artifact_id


def test_managed_workspace_never_adopts_or_clears_unregistered_directories(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    orphan = state_dir / managed.ROOT_NAME / ("f" * 32)
    orphan.mkdir(parents=True)
    (orphan / "keep.txt").write_text("old directory", encoding="utf-8")

    status = managed.inspect_managed_workspace(state_dir)
    assert status["status"] == "not_created"
    assert status["orphaned_initializations"] == 1
    created = managed.create_managed_workspace(state_dir)
    assert created["workspace_id"] != "f" * 32
    assert (orphan / "keep.txt").read_text(encoding="utf-8") == "old directory"
    assert created["orphaned_initializations"] == 1


def test_broken_registration_is_not_replaced(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registration = state_dir / managed.REGISTRATION_NAME
    registration.write_text('{"schema":"old"}', encoding="utf-8")
    original = registration.read_bytes()

    with pytest.raises(BoundaryError, match="registration_invalid"):
        managed.create_managed_workspace(state_dir)
    assert registration.read_bytes() == original
    assert not (state_dir / managed.ROOT_NAME).exists()


def test_symlink_registration_is_never_followed(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    external = tmp_path / "external.json"
    external.write_text('{"keep":true}', encoding="utf-8")
    try:
        (state_dir / managed.REGISTRATION_NAME).symlink_to(external)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    with pytest.raises(BoundaryError, match="registration_invalid"):
        managed.create_managed_workspace(state_dir)
    assert external.read_text(encoding="utf-8") == '{"keep":true}'

    assert not (state_dir / managed.ROOT_NAME).exists()


def test_uuid_collision_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    collision_id = "c" * 32
    collision = state_dir / managed.ROOT_NAME / collision_id
    collision.mkdir(parents=True)
    sentinel = collision / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    class FixedId:
        hex = collision_id

    monkeypatch.setattr(managed.uuid, "uuid4", lambda: FixedId())
    with pytest.raises(FileExistsError):
        managed.create_managed_workspace(state_dir)
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not (state_dir / managed.REGISTRATION_NAME).exists()


def test_registration_write_failure_leaves_only_visible_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    real_atomic_json = managed.atomic_json

    def fail_registration(path: Path, value) -> None:
        if path.name == managed.REGISTRATION_NAME:
            raise OSError("synthetic pointer write failure")
        real_atomic_json(path, value)

    monkeypatch.setattr(managed, "atomic_json", fail_registration)
    with pytest.raises(BoundaryError, match="workspace_registration_failed"):
        managed.create_managed_workspace(state_dir)
    status = managed.inspect_managed_workspace(state_dir)
    assert status["status"] == "not_created"
    assert status["orphaned_initializations"] == 1
    assert not (state_dir / managed.REGISTRATION_NAME).exists()


def test_initialization_failure_leaves_no_registration_and_reports_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    def fail_registry(*_args, **_kwargs):
        raise OSError("synthetic registry failure")

    monkeypatch.setattr(managed, "SQLiteRegistry", fail_registry)
    with pytest.raises(BoundaryError, match="workspace_initialization_failed"):
        managed.create_managed_workspace(state_dir)
    assert not (state_dir / managed.REGISTRATION_NAME).exists()
    status = managed.inspect_managed_workspace(state_dir)
    assert status["status"] == "not_created"
    assert status["orphaned_initializations"] == 1


def test_symlinked_workspace_root_is_rejected_without_following_it(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (state_dir / managed.ROOT_NAME).symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    with pytest.raises(BoundaryError, match="workspace_root_invalid"):
        managed.create_managed_workspace(state_dir)
    assert list(target.iterdir()) == []
