"""Synthetic local owner tests; no real source or training is created."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench import managed_local_workspace as managed
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.local_curation import LEDGER_NAME, LocalCurationOwner
from spireagent.workbench.local_recording_import import _selected_store


def create(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    selected = managed.create_managed_workspace(state)
    directory = state / managed.ROOT_NAME / selected["workspace_id"]
    return state, directory, selected["curation_owner"]


def test_reopen_and_registry_rebuild_preserve_claims(tmp_path: Path) -> None:
    state, directory, owner = create(tmp_path)
    owner.ledger.claim("claim-a", "training", {"run-a"})
    (directory / "registry.sqlite").unlink()
    SQLiteRegistry(directory / "registry.sqlite")
    reopened = managed.inspect_managed_workspace(state)
    assert reopened["curation_status"] == "ready"
    with reopened["curation_owner"].transaction() as db:
        purpose = db.execute("SELECT purpose FROM curation_claims WHERE id='claim-a'").fetchone()
        assert purpose == ("training",)


def test_missing_replaced_or_wrong_owner_ledger_fails_closed(tmp_path: Path) -> None:
    state, directory, owner = create(tmp_path)
    owner.ledger.claim("claim-a", "gold", {"run-a"})
    store = ManifestArtifactStore(LocalBlobStore(directory / "store", create=False))
    store.publish(Manifest("dataset", Producer("local/test", "a" * 40, "b" * 64)))
    ledger = directory / LEDGER_NAME
    backup = tmp_path / "backup.sqlite"
    shutil.copy2(ledger, backup)
    ledger.unlink()
    missing = managed.inspect_managed_workspace(state)
    assert missing["workspace"].inventory()["total"] == 1
    assert missing["curation_status"] == "recovery_required"
    assert missing["curation_owner"] is None
    assert missing["curation_recovery"] == "curation_owner_recovery_required"
    assert managed.create_managed_workspace(state)["curation_owner"] is None
    assert not ledger.exists()
    managed_config = ProjectConfig(state, "", "", None, combination())
    with pytest.raises(BoundaryError, match="curation_owner_recovery_required"):
        _selected_store(managed_config)
    ledger.write_bytes(b"replacement")
    damaged = managed.inspect_managed_workspace(state)
    assert damaged["workspace"].inventory()["total"] == 1
    assert damaged["curation_status"] == "recovery_required"
    with pytest.raises(BoundaryError, match="curation_owner_recovery_required"):
        _selected_store(managed_config)
    ledger.unlink()
    shutil.copy2(backup, ledger)
    with pytest.raises(BoundaryError, match="store_identity_mismatch"):
        LocalCurationOwner(ledger, directory / "store", "0" * 32,
                           owner.identity[1], owner.identity[2])
    with pytest.raises(BoundaryError, match="store_identity_mismatch"):
        LocalCurationOwner(ledger, directory / "store", owner.identity[0],
                           "0" * 32, owner.identity[2])


def test_old_marker_remains_preview_only_and_does_not_create_ledger(tmp_path: Path) -> None:
    state, directory, _ = create(tmp_path)
    marker_path = directory / "workspace.json"
    marker = json.loads(marker_path.read_text())
    for field in ("curation_ledger", "ledger_id", "store_id"):
        del marker[field]
    marker["schema"] = managed.LEGACY_WORKSPACE_SCHEMA
    marker_path.write_text(json.dumps(marker))
    (directory / LEDGER_NAME).unlink()
    opened = managed.inspect_managed_workspace(state)
    assert opened["workspace"].inventory()["total"] == 0
    assert opened["curation_status"] == "recovery_required"
    assert opened["curation_recovery"] == "legacy_history_requires_explicit_migration"
    assert opened["curation_owner"] is None
    assert not (directory / LEDGER_NAME).exists()


def test_pending_survives_restart_and_blocks_gold(tmp_path: Path) -> None:
    state, _, owner = create(tmp_path)
    owner.begin_source("a" * 64)
    reopened = managed.inspect_managed_workspace(state)["curation_owner"]
    with pytest.raises(BoundaryError, match="gold_source_inventory_pending"):
        reopened.ledger.claim("gold-a", "gold", {"run-a"}, require_inventory=True)
    with pytest.raises(BoundaryError, match="source_pending_identity_conflict"):
        reopened.complete_index("a" * 64, "b" * 64)
    with reopened.transaction() as db:
        assert db.execute("SELECT status FROM local_source_pending").fetchone() == ("publishing",)


def test_pending_can_clear_only_for_its_published_exact_source(tmp_path: Path) -> None:
    _, _, owner = create(tmp_path)
    source, other = "a" * 64, "b" * 64
    owner.begin_source("c" * 64)
    owner.published_source("c" * 64, source)
    with owner.transaction() as db:
        db.executemany("INSERT INTO curation_sources VALUES(?,?,1)",
                       [(source, "d" * 64), (other, "e" * 64)])
        db.executemany("INSERT INTO curation_exact_source_index VALUES(?)",
                       [(source,), (other,)])
    with pytest.raises(BoundaryError, match="source_pending_identity_conflict"):
        owner.complete_index("c" * 64, other)
    owner.complete_index("c" * 64, source)
    owner.ledger.claim("gold", "gold", {"run"})


def test_dataset_manifest_does_not_count_as_unindexed_source(tmp_path: Path) -> None:
    _, directory, owner = create(tmp_path)
    store = ManifestArtifactStore(LocalBlobStore(directory / "store", create=False))
    store.publish(Manifest("dataset", Producer("local/test", "a" * 40, "b" * 64)))
    owner.ledger.claim("gold", "gold", {"run"})


def test_second_state_cannot_write_managed_store_as_legacy(tmp_path: Path) -> None:
    _, directory, owner = create(tmp_path)
    owner.ledger.claim("gold-a", "gold", {"run-a"})
    other_state = tmp_path / "other-state"
    other_state.mkdir()
    config = ProjectConfig(other_state, "", "", None, combination(),
                           LocalResearchWorkspaceConfig(directory / "store",
                                                        directory / "registry.sqlite"))
    with pytest.raises(BoundaryError, match="managed_store_owner_required"):
        _selected_store(config)
    with pytest.raises(BoundaryError, match="store_identity_mismatch"):
        LocalCurationOwner(other_state / LEDGER_NAME, directory / "store",
                           owner.identity[0], owner.identity[1], owner.identity[2], create=True)
    assert not (other_state / LEDGER_NAME).exists()


def test_gold_and_training_writer_race_has_one_winner(tmp_path: Path) -> None:
    _, _, owner = create(tmp_path)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def claim(identity: str, purpose: str) -> None:
        barrier.wait()
        try:
            owner.ledger.claim(identity, purpose, {"same-run"}, require_inventory=True)
            results.append(purpose)
        except BoundaryError:
            results.append("rejected")

    threads = [threading.Thread(target=claim, args=("gold", "gold")),
               threading.Thread(target=claim, args=("train", "training"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sorted(results) in (["gold", "rejected"], ["rejected", "training"])
