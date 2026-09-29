"""Synthetic SQLite host contract for the shared research reservation rules."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.research_curation import CurationLedger


class PrivateHost:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self.transaction() as db:
            db.execute("CREATE TABLE inventory_pending(value INTEGER NOT NULL)")
            db.execute("INSERT INTO inventory_pending VALUES(1)")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def test_gold_inventory_requires_a_host_check_in_the_claim_transaction(tmp_path: Path) -> None:
    host = PrivateHost(tmp_path / "curation.sqlite")
    without_inventory = CurationLedger(host)
    with pytest.raises(BoundaryError, match="gold_source_inventory_pending"):
        without_inventory.claim("sealed", "gold", {"run"}, require_inventory=True)

    inspected: list[bool] = []

    def pending(db: sqlite3.Connection) -> bool:
        inspected.append(db.in_transaction)
        return bool(db.execute("SELECT value FROM inventory_pending").fetchone()[0])

    ledger = CurationLedger(host, inventory_pending=pending)
    with pytest.raises(BoundaryError, match="gold_source_inventory_pending"):
        ledger.claim("sealed", "gold", {"run"}, require_inventory=True)
    with host.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_claims").fetchone()[0] == 0
        db.execute("UPDATE inventory_pending SET value=0")
    ledger.claim("sealed", "gold", {"run"}, require_inventory=True)
    assert inspected == [True, True]
    with host.transaction() as db:
        assert db.execute("SELECT purpose FROM curation_claims").fetchone()[0] == "gold"


def test_transitive_gold_merge_use_and_training_protection(tmp_path: Path) -> None:
    host = PrivateHost(tmp_path / "curation.sqlite")
    ledger = CurationLedger(host)
    with host.transaction() as db:
        db.executemany(
            "INSERT INTO curation_fingerprints VALUES(?,?)",
            [("x", "a"), ("x", "b"), ("y", "b"), ("y", "c")],
        )
    assert ledger.overlap({"a"}, {"c"})["overlap"]
    ledger.claim("first", "gold", {"a"})
    artifact = "a" * 64
    ledger.bind("first", artifact)
    with pytest.raises(BoundaryError, match="gold_requires_gold_merge"):
        ledger.claim("second", "gold", {"c"})
    ledger.claim("second", "gold", {"c"}, gold_parents=(artifact,))
    assert ledger.dataset(artifact) == ("gold", {"a"})
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        ledger.claim("training", "training", {"c"})
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        ledger.use({"b"}, "download", "export")
    ledger.use({"independent"}, "training", "model")
    with pytest.raises(BoundaryError, match="gold_previously_used_for_training"):
        ledger.claim("late", "gold", {"independent"})


def test_annotation_revision_and_repeat_reservation_identity(tmp_path: Path) -> None:
    host = PrivateHost(tmp_path / "curation.sqlite")
    ledger = CurationLedger(host)
    occurrence = "f" * 64
    with host.transaction() as db:
        db.execute("INSERT INTO curation_occurrences VALUES(?,?,?)", (occurrence, "run", "d"))
    revision = ledger.annotations((), runs={"run"})["revision"]
    sequence = ledger.annotate(occurrence, "reviewer", "exclude", "synthetic review")
    assert sequence > revision
    assert ledger.annotations((), runs={"run"})["items"][occurrence]["action"] == "exclude"
    with pytest.raises(BoundaryError, match="quality_annotations_changed"):
        ledger.claim("selection", "test", {"run"}, annotation_revision=revision)
    ledger.claim("selection", "test", {"run"}, annotation_revision=sequence)
    ledger.claim("selection", "test", {"run"}, annotation_revision=sequence)
    with pytest.raises(BoundaryError, match="reservation_identity_conflict"):
        ledger.claim("selection", "test", {"different"})
    with host.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_claim_runs").fetchone()[0] == 1


def test_exact_training_use_read_is_nonmutating_and_survives_later_work(tmp_path: Path) -> None:
    host = PrivateHost(tmp_path / "curation.sqlite")
    ledger = CurationLedger(host)
    dataset, source, operation = "a" * 64, "b" * 64, "c" * 32
    ledger.claim("claim", "training", {"run-a"})
    ledger.bind("claim", dataset)
    with host.transaction() as db:
        db.execute("INSERT INTO curation_sources VALUES(?,?,1)", (source, "d" * 64))
        db.execute("INSERT INTO curation_exact_source_index VALUES(?)", (source,))
        db.execute("INSERT INTO curation_source_runs VALUES(?,?)", (source, "run-a"))
    with pytest.raises(BoundaryError, match="training_source_use_missing"):
        ledger.require_training_use(dataset, {source}, {"run-a"}, operation)
    ledger.use_source(source, "training", operation)
    with pytest.raises(BoundaryError, match="training_run_use_missing"):
        ledger.require_training_use(dataset, {source}, {"run-a"}, operation)
    ledger.use({"run-a"}, "training", operation)
    ledger.require_training_use(dataset, {source}, {"run-a"}, operation)
    ledger.claim("later", "training", {"run-b"})
    ledger.require_training_use(dataset, {source}, {"run-a"}, operation)
    with pytest.raises(BoundaryError, match="training_claim_mismatch"):
        ledger.require_training_use(dataset, {source}, {"run-b"}, operation)
    with pytest.raises(BoundaryError, match="source_index_incomplete"):
        ledger.require_training_use(dataset, {source, "e" * 64}, {"run-a"}, operation)
