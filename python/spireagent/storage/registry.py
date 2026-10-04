"""Rebuildable SQLite projection; manifests, not SQL rows, retain durable identity."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, digest
from spireagent.storage.store import ArtifactStore


class Registry(Protocol):
    def rebuild(
        self, manifests: Iterable[Manifest], cached_ids: frozenset[str] = frozenset()
    ) -> int: ...
    def get(self, artifact_id: str) -> Manifest: ...
    def manifests(self, kind: str | None = None) -> tuple[Manifest, ...]: ...
    def lineage(self, artifact_id: str) -> tuple[Manifest, ...]: ...
    def is_cached(self, artifact_id: str) -> bool: ...


class SQLiteRegistry:
    SCHEMA_VERSION = 1

    def __init__(self, path: Path, *, readonly: bool = False) -> None:
        self.path = path.expanduser().resolve()
        self.readonly = readonly
        if readonly and not self.path.is_file():
            raise BoundaryError("registry", "not_configured")
        if not readonly:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if readonly and version != self.SCHEMA_VERSION:
                raise BoundaryError("registry", "unsupported_or_uninitialized_cache")
            if version not in {0, self.SCHEMA_VERSION}:
                raise BoundaryError(
                    "registry", "unsupported_cache_schema", "rebuild a separate cache"
                )
            if version == 0:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if tables:
                    raise BoundaryError("registry", "unversioned_nonempty_database")
                connection.executescript("""
                    CREATE TABLE artifacts (
                        id TEXT PRIMARY KEY, kind TEXT NOT NULL, manifest BLOB NOT NULL,
                        cached INTEGER NOT NULL CHECK(cached IN (0, 1))
                    );
                    CREATE TABLE edges (
                        child TEXT NOT NULL REFERENCES artifacts(id),
                        parent TEXT NOT NULL REFERENCES artifacts(id),
                        role TEXT NOT NULL, PRIMARY KEY(child, parent, role)
                    );
                    PRAGMA user_version = 1;
                """)
            if readonly:
                try:
                    connection.execute("SELECT id, kind, manifest, cached FROM artifacts LIMIT 0")
                    connection.execute("SELECT child, parent, role FROM edges LIMIT 0")
                except sqlite3.DatabaseError as error:
                    raise BoundaryError("registry", "unsupported_or_uninitialized_cache") from error

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            f"{self.path.as_uri()}?mode=ro" if self.readonly else self.path,
            timeout=30,
            uri=self.readonly,
        )
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def rebuild(
        self, manifests: Iterable[Manifest], cached_ids: frozenset[str] = frozenset()
    ) -> int:
        if self.readonly:
            raise BoundaryError("registry", "read_only")
        records: dict[str, Manifest] = {}
        for manifest in manifests:
            previous = records.setdefault(manifest.artifact_id, manifest)
            if previous.to_bytes() != manifest.to_bytes():
                raise BoundaryError("registry", "identity_collision")
        for manifest in records.values():
            if any(parent.artifact_id not in records for parent in manifest.parents):
                raise BoundaryError(
                    "registry", "dangling_parent", "sync the complete manifest closure"
                )
        if not cached_ids <= records.keys():
            raise BoundaryError("registry", "cache_state_without_manifest")
        with self._connection() as connection:
            connection.execute("DELETE FROM edges")
            connection.execute("DELETE FROM artifacts")
            connection.executemany(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?)",
                [
                    (identity, manifest.kind, manifest.to_bytes(), int(identity in cached_ids))
                    for identity, manifest in sorted(records.items())
                ],
            )
            connection.executemany(
                "INSERT INTO edges VALUES (?, ?, ?)",
                [
                    (identity, parent.artifact_id, parent.role)
                    for identity, manifest in sorted(records.items())
                    for parent in manifest.parents
                ],
            )
        return len(records)

    def get(self, artifact_id: str) -> Manifest:
        digest(artifact_id, "registry.id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT manifest, kind FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise BoundaryError("registry", "not_indexed", "sync or rebuild from manifests")
        manifest = Manifest.from_bytes(row[0], artifact_id)
        if manifest.kind != row[1]:
            raise BoundaryError("registry", "corrupt_kind_index", "rebuild from manifests")
        return manifest

    def manifests(self, kind: str | None = None) -> tuple[Manifest, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, manifest, kind FROM artifacts WHERE (? IS NULL OR kind=?) ORDER BY id",
                (kind, kind),
            ).fetchall()
        values = []
        for row in rows:
            manifest = Manifest.from_bytes(row[1], row[0])
            if manifest.kind != row[2]:
                raise BoundaryError("registry", "corrupt_kind_index", "rebuild from manifests")
            values.append(manifest)
        return tuple(values)

    def is_cached(self, artifact_id: str) -> bool:
        digest(artifact_id, "registry.id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT cached FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
        return row is not None and row[0] == 1

    def dependents(self, artifact_id: str, *, kind: str) -> tuple[Manifest, ...]:
        """Small reverse metadata projection; consumers recheck each CAS manifest."""
        digest(artifact_id, "registry.id")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT a.id,a.manifest FROM artifacts a JOIN edges e ON e.child=a.id "
                "WHERE e.parent=? AND a.kind=? ORDER BY a.id LIMIT 101",
                (artifact_id, kind),
            ).fetchall()
        if len(rows) > 100:
            raise BoundaryError("registry", "dependent_limit")
        values = tuple(Manifest.from_bytes(row[1], row[0]) for row in rows)
        if any(m.kind != kind or not any(p.artifact_id == artifact_id for p in m.parents)
               for m in values):
            raise BoundaryError("registry", "corrupt_parent_index")
        return values

    def lineage(self, artifact_id: str) -> tuple[Manifest, ...]:
        pending = [artifact_id]
        found: dict[str, Manifest] = {}
        while pending:
            identity = pending.pop()
            if identity in found:
                continue
            manifest = self.get(identity)
            found[identity] = manifest
            pending.extend(parent.artifact_id for parent in manifest.parents)
        return tuple(found[key] for key in sorted(found))


def sync_registry(
    store: ArtifactStore, registry: Registry, cached_ids: frozenset[str] = frozenset()
) -> int:
    pending = list(store.manifest_ids())
    records: dict[str, Manifest] = {}
    while pending:
        identity = pending.pop()
        if identity in records:
            continue
        manifest = store.get_manifest(identity)
        records[identity] = manifest
        pending.extend(parent.artifact_id for parent in manifest.parents)
    return registry.rebuild(records.values(), cached_ids)
