"""Private synthetic Curation recovery; no live Application or project ledger is changed."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing

import pytest
from test_inplace_curation import _config, _settle

from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from spireagent.workbench.curation_backup import publish_backup, restore_backup
from spireagent.workbench.inplace_curation import InplaceCurationPreparation, configured_owner


def ready(tmp_path):
    config, source, store = _config(tmp_path)
    preparation = InplaceCurationPreparation(config)
    preparation.start()
    assert _settle(preparation)["status"] == "ready"
    owner = configured_owner(config)
    runs = owner.ledger.source_runs(source)
    owner.ledger.claim("d" * 64, "training", runs)
    owner.ledger.bind("d" * 64, "d" * 64)
    owner.ledger.use(runs, "training", "prior-operation")
    owner.ledger.use_source(source, "training", "prior-operation")
    return config, source, store, owner, runs


def test_native_snapshot_transfer_restore_and_same_owner_consumes_original_uses(tmp_path):
    config, source, store, owner, runs = ready(tmp_path)
    marker = (owner.store_dir / ".curation-owner.json").read_bytes()
    manifest = publish_backup(owner, store, store.get_manifest(source).producer,
                              scratch=tmp_path / "scratch")
    remote = ManifestArtifactStore(LocalBlobStore(tmp_path / "peer"))
    assert copy_artifact(store, remote, manifest.artifact_id) == manifest.artifact_id
    staged = tmp_path / "restored"
    result = restore_backup(remote, manifest.artifact_id, staged)
    assert result["status"] == "verified_staged" and result["live_replaced"] is False
    assert result["counts"]["curation_claims"] == 1
    assert result["counts"]["curation_uses"] == len(runs)
    assert (owner.store_dir / ".curation-owner.json").read_bytes() == marker
    assert owner.ledger.dataset("d" * 64) == ("training", runs)
    with pytest.raises(BoundaryError, match="new_restore_directory_required"):
        restore_backup(remote, manifest.artifact_id, staged)

    # Only this offline fixture simulates loss and installs the staged database.
    # The production restore command never overwrites an existing owner.
    owner.path.rename(tmp_path / "lost.sqlite")
    shutil.copyfile(staged / "curation.sqlite", owner.path)
    recovered = configured_owner(config)
    assert recovered.identity == owner.identity
    assert recovered.ledger.dataset("d" * 64) == ("training", runs)
    assert recovered.ledger.source_runs(source) == runs
    with recovered.transaction() as db:
        assert {tuple(row) for row in db.execute(
            "SELECT run,kind,reference FROM curation_uses")} == {
                (run, "training", "prior-operation") for run in runs}
        assert db.execute("SELECT source,kind,reference FROM curation_source_uses").fetchall() == [
            (source, "training", "prior-operation")]


def test_private_owner_backup_rejects_shared_account_database_before_publication(tmp_path):
    _, source, store, owner, _ = ready(tmp_path)
    with closing(sqlite3.connect(owner.path)) as db:
        db.execute("CREATE TABLE accounts(token TEXT)")
        db.execute("INSERT INTO accounts VALUES('fixture-secret-not-for-export')")
        db.commit()
    before = set(store.manifest_ids())
    with pytest.raises(BoundaryError, match="private_curation_database_required"):
        publish_backup(owner, store, store.get_manifest(source).producer,
                       scratch=tmp_path / "scratch")
    assert set(store.manifest_ids()) == before
