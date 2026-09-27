"""Private verified source index; immutable receipts locate already verified source bytes.

Only the installed projection owner writes this disposable index. Headers commit last
with ordered row digests, so interrupted indexing is never an admitted source. Membership
and source availability are rechecked by callers; this is not an access grant.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from spireagent.artifact_contracts import Payload
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.local_verified_bundle import VerifiedLocalBundle
from spireagent.storage.store import ArtifactStore

from .contracts import ResearchTransitionV2, SourceProjection
from .platform_bundle3 import ADAPTER_ID, PlatformBundle3SourceAdapter

if TYPE_CHECKING:
    from .decision_cache import VerifiedSourceCache

INDEX_SCHEMA = "stpd/private-verified-source-index-v1"
MAX_INDEX_BYTES = 1024**3


def index_ready(cache: VerifiedSourceCache, payload: Payload) -> bool:
    """Scheduling hint only; resolve_payload still verifies every cached row."""
    key = hashlib.sha256(json_bytes([INDEX_SCHEMA, cache.owner, payload.sha256])).hexdigest()
    with cache._connect() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='decision_source_index'"
        ).fetchone():
            return False
        row = db.execute(
            "SELECT body,sha256 FROM decision_source_index WHERE key=?", (key,),
        ).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != row[1]:
            return False
        try:
            header = json.loads(row[0])
            return bool(header["schema"] == INDEX_SCHEMA and header["owner"] == cache.owner
                        and header["source"] == payload.sha256 and header["size"] == payload.size)
        except (ValueError, KeyError, TypeError):
            return False


def resolve_payload(
    cache: VerifiedSourceCache, store: ArtifactStore, payload: Payload,
    *, verified_local: VerifiedLocalBundle | None = None,
) -> tuple[SourceProjection, dict[str, Any]]:
    from . import decision_cache
    from .decision_dataset import _versions, _versions_directory

    key = hashlib.sha256(json_bytes([INDEX_SCHEMA, cache.owner, payload.sha256])).hexdigest()
    with cache._connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS decision_source_index("
                   "key TEXT PRIMARY KEY,body BLOB NOT NULL,sha256 TEXT NOT NULL,"
                   "bytes INTEGER NOT NULL,used REAL NOT NULL,rows_key TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS decision_source_generation "
                   "ON decision_source_index(rows_key)")
        db.execute("CREATE TABLE IF NOT EXISTS decision_source_rows("
                   "source TEXT NOT NULL,ordinal INTEGER NOT NULL,"
                   "transition_id TEXT NOT NULL,body BLOB NOT NULL,"
                   "PRIMARY KEY(source,ordinal))")
        db.execute("CREATE INDEX IF NOT EXISTS decision_source_transition "
                   "ON decision_source_rows(source,transition_id)")
        db.execute("CREATE TABLE IF NOT EXISTS decision_source_writes("
                   "key TEXT PRIMARY KEY,token TEXT NOT NULL,updated REAL NOT NULL)")
        row = db.execute(
            "SELECT body,sha256 FROM decision_source_index WHERE key=?", (key,),
        ).fetchone()
        if row:
            try:
                if hashlib.sha256(row[0]).hexdigest() != row[1]:
                    raise ValueError("index checksum")
                header = json.loads(row[0])
                if (header["schema"] != INDEX_SCHEMA or header["owner"] != cache.owner
                        or header["source"] != payload.sha256 or header["size"] != payload.size):
                    raise ValueError("index identity")
                transitions: list[ResearchTransitionV2] = []
                digests = hashlib.sha256()
                for ordinal, body in db.execute(
                    "SELECT ordinal,body FROM decision_source_rows WHERE source=? ORDER BY ordinal",
                    (header["rows_key"],),
                ):
                    if ordinal != len(transitions):
                        raise ValueError("index row order")
                    digests.update(hashlib.sha256(body).digest())
                    transitions.append(ResearchTransitionV2.decode(json.loads(body)))
                if (len(transitions) != header["rows"]
                        or digests.hexdigest() != header["rows_sha256"]):
                    raise ValueError("index rows checksum")
                projection = SourceProjection(
                    ADAPTER_ID, payload.sha256, "platform_verified",
                    FrozenObject.of(header["run_proofs"]), tuple(transitions),
                    accounting=FrozenObject.of(header["accounting"]),
                )
                environments = FrozenObject.of(header["environments"]).value()
                db.execute("UPDATE decision_source_index SET used=? WHERE key=?",
                           (time.time(), key))
                cache.hits += 1
                return projection, environments
            except (ValueError, KeyError, TypeError, BoundaryError):
                cache.corrupt += 1
                db.execute("DELETE FROM decision_source_index WHERE key=?", (key,))
                # Damaged metadata cannot choose rows to delete. Orphaned derivative
                # rows are reclaimed by bounded maintenance, not admitted as a source.
    cache.misses += 1
    if verified_local is None:
        raw = b"".join(store.read_payload(payload))
        if len(raw) != payload.size or hashlib.sha256(raw).hexdigest() != payload.sha256:
            raise BoundaryError("decision_index", "source_identity_mismatch")
        projection = PlatformBundle3SourceAdapter().project(raw)
    else:
        if (verified_local.archive_size != payload.size
                or verified_local.archive_sha256 != payload.sha256):
            raise BoundaryError("decision_index", "source_identity_mismatch")
        projection = PlatformBundle3SourceAdapter()._project_verified_local(verified_local)
    environments = (_versions_directory(verified_local.directory)
                    if verified_local is not None else _versions(raw))
    header = {
        "schema": INDEX_SCHEMA, "owner": cache.owner, "source": payload.sha256,
        "size": payload.size, "run_proofs": projection.run_proofs.value(),
        "accounting": projection.accounting.value(), "environments": environments,
        "rows": len(projection.transitions), "rows_sha256": "0" * 64,
        "rows_key": uuid.uuid4().hex,
    }
    if len(json_bytes(header)) > decision_cache.MAX_ENTRY_BYTES:
        cache.bypassed += 1
        return projection, environments
    _persist(cache, key, header, projection)
    return projection, environments


def _persist(cache: VerifiedSourceCache, key: str, header: dict[str, Any],
             projection: SourceProjection) -> None:
    _collect_orphans(cache)
    token = header["rows_key"]
    # Small commits keep the operational writer available to UI requests, heartbeats,
    # cancellation and membership changes. Only the final header admits these rows.
    with cache._connect() as db:
        old = db.execute("SELECT token FROM decision_source_writes WHERE key=? AND updated<?",
                         (key, time.time() - 1200)).fetchone()
        if old:
            db.execute("DELETE FROM decision_source_writes WHERE key=? AND token=?", (key, old[0]))
        claimed = db.execute("INSERT OR IGNORE INTO decision_source_writes VALUES(?,?,?)",
                             (key, token, time.time())).rowcount
        if not claimed:
            cache.bypassed += 1
            return
    try:
        digests = hashlib.sha256()
        size = len(json_bytes(header))
        batch: list[tuple[str, int, str, bytes]] = []
        batch_bytes = 0

        def flush() -> None:
            with cache._connect() as db:
                owns = db.execute(
                    "UPDATE decision_source_writes SET updated=? WHERE key=? AND token=?",
                    (time.time(), key, token),
                ).rowcount
                if not owns:
                    raise BoundaryError("decision_index", "index_writer_superseded")
                db.executemany("INSERT INTO decision_source_rows VALUES(?,?,?,?)", batch)
            batch.clear()

        for ordinal, record in enumerate(projection.transitions):
            body = json_bytes(record.to_dict())
            size += len(body)
            if size > MAX_INDEX_BYTES:
                cache.bypassed += 1
                return
            digests.update(hashlib.sha256(body).digest())
            batch.append((token, ordinal, record.transition_id, body))
            batch_bytes += len(body)
            if len(batch) >= 32 or batch_bytes >= 4 * 1024**2:
                flush()
                batch_bytes = 0
        if batch:
            flush()
        header["rows_sha256"] = digests.hexdigest()
        encoded = json_bytes(header)
        with cache._connect() as db:
            owns = db.execute("SELECT 1 FROM decision_source_writes WHERE key=? AND token=?",
                              (key, token)).fetchone()
            if not owns:
                return
            db.execute("INSERT OR REPLACE INTO decision_source_index VALUES(?,?,?,?,?,?)",
                       (key, encoded, hashlib.sha256(encoded).hexdigest(),
                        size, time.time(), token))
            total = db.execute(
                "SELECT coalesce(sum(bytes),0) FROM decision_source_index",
            ).fetchone()[0]
            for stale, count in db.execute(
                "SELECT key,bytes FROM decision_source_index ORDER BY used,key"
            ).fetchall():
                if total <= MAX_INDEX_BYTES:
                    break
                db.execute("DELETE FROM decision_source_index WHERE key=?", (stale,))
                total -= count
    finally:
        with cache._connect() as db:
            db.execute("DELETE FROM decision_source_writes WHERE key=? AND token=?", (key, token))
        _collect_orphans(cache)


def _collect_orphans(cache: VerifiedSourceCache) -> None:
    """Reclaim interrupted derivatives in small commits, never raw evidence or tasks."""
    with cache._connect() as db:
        db.execute("DELETE FROM decision_source_writes WHERE updated<?", (time.time() - 1200,))
        sources = db.execute(
            "SELECT DISTINCT r.source FROM decision_source_rows r WHERE NOT EXISTS "
            "(SELECT 1 FROM decision_source_index i WHERE i.rows_key=r.source) AND NOT EXISTS "
            "(SELECT 1 FROM decision_source_writes w WHERE w.token=r.source)"
        ).fetchall()
    for (source,) in sources:
        while True:
            with cache._connect() as db:
                deleted = db.execute(
                    "DELETE FROM decision_source_rows WHERE rowid IN "
                    "(SELECT rowid FROM decision_source_rows WHERE source=? LIMIT 16)", (source,),
                ).rowcount
            if not deleted:
                break
