from __future__ import annotations

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore


def test_validated_keys_do_not_resolve_mutable_hard_link_names(tmp_path: Path, monkeypatch) -> None:
    store = LocalBlobStore(tmp_path)

    def forbidden_resolve(*args, **kwargs):
        raise AssertionError("a validated object key must not resolve a mutable leaf")

    monkeypatch.setattr(Path, "resolve", forbidden_resolve)
    assert store.put_if_absent("objects/a", b"content")
    assert not store.put_if_absent("objects/a", b"content")
    assert store.get("objects/a") == b"content"


def test_windows_reparse_point_is_rejected_without_following_it(
    tmp_path: Path, monkeypatch
) -> None:
    store = LocalBlobStore(tmp_path)
    original = os.lstat

    def lstat(path, *args, **kwargs):
        if Path(path) == tmp_path / "objects":
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(StoreError, match="reparse_object_path"):
        store.put_if_absent("objects/a", b"content")


def test_repeated_concurrent_publish_is_exact_and_idempotent(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for index in range(20):
            key = f"objects/{index}"
            results = list(
                pool.map(lambda _, key=key: store.put_if_absent(key, b"same"), range(16))
            )
            assert sum(results) == 1
            assert store.get(key) == b"same"
    assert not list(tmp_path.rglob(".pending-*"))


def test_directory_prefix_walk_stays_inside_requested_subtree(
    tmp_path: Path, monkeypatch
) -> None:
    store = LocalBlobStore(tmp_path)
    for key in (
        "manifests/a.json",
        "manifests/nested/b.json",
        "manifests/nested/.pending-c.json",
        "objects/sha256/chunk",
        "objects-other/chunk",
    ):
        assert store.put_if_absent(key, key.encode())

    walked: list[Path] = []
    original_rglob = Path.rglob

    def counted_rglob(path: Path, pattern: str):
        walked.append(path)
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", counted_rglob)
    assert store.keys("manifests/") == ("manifests/a.json", "manifests/nested/b.json")
    assert walked == [tmp_path / "manifests"]


def test_keys_preserve_string_prefix_and_empty_prefix_validation(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    for key in ("objects/a", "objects/ab", "objects-extra/b", "other/c"):
        assert store.put_if_absent(key, key.encode())

    assert store.keys("objects/") == ("objects/a", "objects/ab")
    assert store.keys("objects/a") == ("objects/a", "objects/ab")
    assert store.keys("objects") == (
        "objects-extra/b",
        "objects/a",
        "objects/ab",
    )
    with pytest.raises(StoreError, match="invalid_object_key"):
        store.keys("")


def test_directory_prefix_does_not_follow_linked_scan_root(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path / "store")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"not an object")
    try:
        (store.root / "manifests").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    assert store.keys("manifests/") == ()
