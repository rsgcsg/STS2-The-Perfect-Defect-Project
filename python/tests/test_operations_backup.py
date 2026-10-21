"""The original Scheduler handle survives Store transfer into a paused staged owner."""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from test_hub_scheduler import pipeline as pipeline

from spireagent.hub.database import Operations
from spireagent.hub.scheduler import Scheduler
from spireagent.json_boundary import BoundaryError
from spireagent.storage.config import open_store
from spireagent.storage.store import copy_artifact
from spireagent.workbench.developer import ProjectConfig, RemoteComputeConfig, combination
from spireagent.workbench.operations_backup import publish_backup, restore_backup


def configuration(tmp_path, operations):
    return ProjectConfig(tmp_path / "state", "", "", None, combination(), remote_compute=(
        RemoteComputeConfig(operations.path, tmp_path / "target.json", 10, 1,
                            {"kind": "local", "directory": str(tmp_path / "peer")})))


def test_private_operations_roundtrip_keeps_handle_but_never_dispatches(pipeline, tmp_path):
    ops, store, provider, job = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        assert scheduler.tick(1)["state"] == "submitted"
    original = ops.compute_state(job)
    manifest = publish_backup(configuration(tmp_path, ops), store, provider.target.producer,
                              scratch=tmp_path / "scratch")
    peer = open_store(str(tmp_path / "peer"))
    copy_artifact(store, peer, manifest.artifact_id)
    destination = tmp_path / "restored"
    result = restore_backup(peer, manifest.artifact_id, destination)
    assert result["status"] == "verified_staged" and result["dispatch"] == "paused"
    restored = Operations(destination / "operations.sqlite")
    assert restored.compute_state(job) == original
    with closing(sqlite3.connect(restored.path)) as db:
        assert db.execute("SELECT lease_hash FROM jobs").fetchone() == (None,)
    assert ops.jobs()[0]["status"] == "running" and not ops.paused()
    with Scheduler(restored, store, provider, budget_limit=10) as scheduler:
        assert scheduler.tick(2)["state"] == "paused_by_operator"
    assert len(provider.calls) == 1 and not provider.polls
    with pytest.raises(BoundaryError, match="new_restore_directory_required"):
        restore_backup(peer, manifest.artifact_id, destination)


def test_account_or_device_database_is_not_published(pipeline, tmp_path):
    ops, store, provider, _ = pipeline
    with ops.transaction() as db:
        db.execute("INSERT INTO devices(id,token_hash,active) VALUES('private-account','hash',1)")
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="private_scheduler_database_required"):
        publish_backup(configuration(tmp_path, ops), store, provider.target.producer,
                       scratch=tmp_path / "scratch")
    assert store.manifest_ids() == before
