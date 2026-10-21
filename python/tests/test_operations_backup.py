"""The original Scheduler handle survives Store transfer into a paused staged owner."""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from test_hub_scheduler import pipeline as pipeline

from spireagent.hub.console_index import ConsoleIndex
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
    index = ConsoleIndex(ops)
    index.artifact_closure(store, tuple(store.manifest_ids()))
    projections = ("console_artifacts", "console_artifact_policy", "console_lineage")
    with closing(sqlite3.connect(ops.path)) as db:
        assert all(db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                   for table in projections)
        original_database = tuple(db.iterdump())
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
        assert all(db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
                   for table in projections)
        assert db.execute("PRAGMA freelist_count").fetchone() == (0,)
    with closing(sqlite3.connect(ops.path)) as db:
        assert tuple(db.iterdump()) == original_database
    assert ops.jobs()[0]["status"] == "running" and not ops.paused()
    with Scheduler(restored, store, provider, budget_limit=10) as scheduler:
        assert scheduler.tick(2)["state"] == "paused_by_operator"
    assert len(provider.calls) == 1 and not provider.polls
    with pytest.raises(BoundaryError, match="new_restore_directory_required"):
        restore_backup(peer, manifest.artifact_id, destination)


@pytest.mark.parametrize("statement", [
    "INSERT INTO devices(id,token_hash,active) VALUES('private-account','hash',1)",
    "INSERT INTO identity_users VALUES('subject','issuer','access','private@example.test')",
    "INSERT INTO console_collections VALUES('private-upload',1,'{}','available',1)",
    "INSERT INTO console_artifact_visibility VALUES('artifact','private-owner',0)",
    "CREATE TABLE unknown_private AS SELECT 'private value' AS value",
])
def test_account_or_device_database_is_not_published(pipeline, tmp_path, statement):
    ops, store, provider, _ = pipeline
    ConsoleIndex(ops)
    with ops.transaction() as db:
        db.execute(statement)
    with closing(sqlite3.connect(ops.path)) as db:
        original_database = tuple(db.iterdump())
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="private_scheduler_database_required"):
        publish_backup(configuration(tmp_path, ops), store, provider.target.producer,
                       scratch=tmp_path / "scratch")
    assert store.manifest_ids() == before
    with closing(sqlite3.connect(ops.path)) as db:
        assert tuple(db.iterdump()) == original_database
