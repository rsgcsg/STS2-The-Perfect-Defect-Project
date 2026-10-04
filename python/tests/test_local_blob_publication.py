"""Exact idempotent reads avoid writes; first publication stays durable and atomic."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from spireagent.storage import blobs, local
from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore


def test_identical_publication_checks_bytes_without_temporary_write(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path)
    assert store.put_if_absent("objects/item", b"exact")
    reads = []
    original = store.get

    def observed(key):
        reads.append(key)
        return original(key)

    monkeypatch.setattr(store, "get", observed)
    monkeypatch.setattr(local.tempfile, "mkstemp", lambda **_: pytest.fail("redundant write"))
    assert store.put_if_absent("objects/item", b"exact") is False
    assert reads == ["objects/item"]
    with pytest.raises(StoreError, match="immutable_key_collision"):
        store.put_if_absent("objects/item", b"other")
    assert original("objects/item") == b"exact"


def test_first_publication_still_syncs_and_cleans_pending_file(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path)
    synced = []
    original = local.os.fsync

    def observed(fd):
        synced.append(fd)
        original(fd)

    monkeypatch.setattr(local.os, "fsync", observed)
    assert store.put_if_absent("objects/item", b"exact") is True
    assert len(synced) == (2 if os.name == "posix" else 1)
    assert store.get("objects/item") == b"exact"
    assert not list(tmp_path.rglob(".pending-*"))


def test_first_publication_does_not_read_an_uncreated_key(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path)
    monkeypatch.setattr(store, "get", lambda _: pytest.fail("read before publication"))
    assert store.put_if_absent("objects/item", b"exact") is True
    assert (tmp_path / "objects/item").read_bytes() == b"exact"


def test_precheck_metadata_error_is_not_absence(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path)
    assert store.put_if_absent("objects/item", b"exact")
    path = tmp_path / "objects/item"
    monkeypatch.setattr(store, "_path", lambda _: path)

    def unavailable(_path):
        raise OSError(errno.ELOOP, "synthetic metadata failure")

    monkeypatch.setattr(local.os, "lstat", unavailable)
    with pytest.raises(OSError, match="synthetic metadata failure"):
        store.put_if_absent("objects/item", b"exact")
    assert path.read_bytes() == b"exact"


@pytest.mark.parametrize("winner", [b"exact", b"different", b"ex"])
def test_publisher_after_absent_precheck_is_reconciled_by_atomic_link(
    tmp_path, monkeypatch, winner,
):
    store = LocalBlobStore(tmp_path)
    original = local.tempfile.mkstemp

    def concurrent_publication(**kwargs):
        # Insert a competing winner after the early absent precheck. Even a partial
        # out-of-contract external write must not be accepted as matching bytes.
        (tmp_path / "objects/item").write_bytes(winner)
        return original(**kwargs)

    monkeypatch.setattr(local.tempfile, "mkstemp", concurrent_publication)
    if winner == b"exact":
        assert store.put_if_absent("objects/item", b"exact") is False
    else:
        with pytest.raises(StoreError, match="immutable_key_collision"):
            store.put_if_absent("objects/item", b"exact")
    assert store.get("objects/item") == winner
    assert not list(tmp_path.rglob(".pending-*"))


def test_deleted_winner_before_collision_read_fails_closed(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path)
    original = local.os.link

    def disappeared_winner(source, destination):
        Path(destination).write_bytes(b"exact")
        try:
            original(source, destination)
        finally:
            Path(destination).unlink()

    monkeypatch.setattr(local.os, "link", disappeared_winner)
    with pytest.raises(StoreError, match="object_not_found"):
        store.put_if_absent("objects/item", b"exact")
    assert not (tmp_path / "objects/item").exists()
    assert not list(tmp_path.rglob(".pending-*"))


def test_missing_after_existing_path_read_uses_durable_publication(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path)
    assert store.put_if_absent("objects/item", b"old")
    original = store.get
    first = True

    def deleted_before_open(key):
        nonlocal first
        if first:
            first = False
            (tmp_path / key).unlink()
        return original(key)

    monkeypatch.setattr(store, "get", deleted_before_open)
    assert store.put_if_absent("objects/item", b"new") is True
    assert original("objects/item") == b"new"


def test_observation_error_is_not_treated_as_a_missing_object(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path)
    assert store.put_if_absent("objects/item", b"exact")

    def unreadable(_key):
        raise StoreError("synthetic_read_failure")

    monkeypatch.setattr(store, "get", unreadable)
    with pytest.raises(StoreError, match="synthetic_read_failure"):
        store.put_if_absent("objects/item", b"exact")
    assert (tmp_path / "objects/item").read_bytes() == b"exact"
    assert not list(tmp_path.rglob(".pending-*"))


def test_existing_path_retains_incoming_and_observed_size_bounds(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path)
    assert store.put_if_absent("objects/item", b"1234")
    monkeypatch.setattr(blobs, "MAX_BLOB_BYTES", 3)
    monkeypatch.setattr(local, "MAX_BLOB_BYTES", 3)
    for value in (b"1234", bytearray(b"12"), b"12"):
        with pytest.raises(StoreError, match="blob_exceeds_bounded_transport"):
            store.put_if_absent("objects/item", value)
    assert (tmp_path / "objects/item").read_bytes() == b"1234"


def test_read_only_republication_fails_before_observation(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path)
    assert store.put_if_absent("objects/item", b"exact")
    readonly = LocalBlobStore(tmp_path, readonly=True)
    monkeypatch.setattr(readonly, "get", lambda _: pytest.fail("readonly publication read"))
    with pytest.raises(StoreError, match="read_only_store"):
        readonly.put_if_absent("objects/item", b"exact")
