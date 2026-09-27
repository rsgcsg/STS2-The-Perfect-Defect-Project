"""The private SQLite spool closes its handle before directory cleanup."""

from __future__ import annotations

import gc
import sqlite3
import tempfile
import weakref
from pathlib import Path

import pytest

import stpd.fullrun.decision_spool as spool_module
from stpd.fullrun.decision_spool import DecisionSpool


def test_gc_cycle_closes_sqlite_before_directory_removal(monkeypatch) -> None:
    gc.collect()
    spool = DecisionSpool()
    directory = spool.directory
    connection = spool.db
    original = spool_module.shutil.rmtree
    observed: list[str] = []

    def inspect_cleanup(path, *args, **kwargs):
        if Path(path) == directory:
            try:
                connection.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                observed.append("closed")
            else:
                observed.append("open")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(spool_module.shutil, "rmtree", inspect_cleanup)
    spool.cycle = spool
    reference = weakref.ref(spool)
    del spool
    gc.collect()

    assert reference() is None
    assert observed == ["closed"]


def test_selection_keeps_owner_readable_and_explicit_close_is_idempotent() -> None:
    spool = DecisionSpool()
    directory = spool.directory
    selected = spool.selected()
    reference = weakref.ref(spool)
    del spool
    gc.collect()

    assert reference() is selected.owner
    assert directory.is_dir()
    assert len(selected) == 0
    assert list(selected.summaries()) == []
    selected.owner.close()
    selected.owner.close()
    assert not directory.exists()
    with pytest.raises(sqlite3.ProgrammingError):
        selected.owner.db.execute("SELECT 1")


def test_close_failure_does_not_attempt_directory_removal(monkeypatch) -> None:
    events: list[str] = []
    original_connect = sqlite3.connect
    original_rmtree = spool_module.shutil.rmtree

    class FailingConnection(sqlite3.Connection):
        def close(self) -> None:
            events.append("close")
            raise sqlite3.OperationalError("synthetic close failure")

        def force_close(self) -> None:
            super().close()

    def connect(path: Path):
        return original_connect(path, factory=FailingConnection)

    monkeypatch.setattr(spool_module.sqlite3, "connect", connect)
    spool = DecisionSpool()
    directory = spool.directory

    def remove_directory(path: Path) -> None:
        if path == directory:
            events.append("cleanup")

    monkeypatch.setattr(spool_module.shutil, "rmtree", remove_directory)
    try:
        with pytest.raises(sqlite3.OperationalError, match="synthetic close failure"):
            spool.close()
        assert events == ["close"]
        assert directory.is_dir()
    finally:
        spool.db.force_close()
        original_rmtree(directory)


def _private_directories(monkeypatch, tmp_path: Path) -> list[Path]:
    original = tempfile.mkdtemp
    created: list[Path] = []

    def private_directory(*, prefix: str):
        directory = original(prefix=prefix, dir=tmp_path)
        created.append(Path(directory))
        return directory

    monkeypatch.setattr(spool_module.tempfile, "mkdtemp", private_directory)
    return created


def test_connect_failure_removes_temporary_directory(monkeypatch, tmp_path: Path) -> None:
    created = _private_directories(monkeypatch, tmp_path)

    def fail_connect(_path: Path):
        raise sqlite3.OperationalError("synthetic connect failure")

    monkeypatch.setattr(spool_module.sqlite3, "connect", fail_connect)
    with pytest.raises(sqlite3.OperationalError, match="synthetic connect failure"):
        DecisionSpool()
    assert len(created) == 1
    assert not created[0].exists()


def test_schema_failure_closes_connection_before_directory_removal(
    monkeypatch, tmp_path: Path,
) -> None:
    created = _private_directories(monkeypatch, tmp_path)
    original_connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    class BrokenSchema(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("CREATE TABLE"):
                raise sqlite3.OperationalError("synthetic schema failure")
            return super().execute(sql, *args)

    def connect(path: Path):
        connection = original_connect(path, factory=BrokenSchema)
        connections.append(connection)
        return connection

    monkeypatch.setattr(spool_module.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="synthetic schema failure"):
        DecisionSpool()
    assert len(created) == len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")
    assert not created[0].exists()
