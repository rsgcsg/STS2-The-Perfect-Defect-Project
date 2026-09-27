"""Bounded private verification cache, never an externally supplied projection format.

The Hub passes its private operations DB; no HTTP endpoint imports cache rows. Original
archive bytes are always read and hashed. Only a successful installed owner verifier may
populate this cache. Source/code/lock changes miss; corrupt entries reverify. Sharing and
membership remain outside this cache and must be checked on every operation.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

import sts2_platform_evidence

from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes

from .contracts import ResearchTransitionV2, SourceProjection
from .platform_bundle3 import ADAPTER_ID, PlatformBundle3SourceAdapter

MAX_CACHE_BYTES = 64 * 1024**2
MAX_ENTRY_BYTES = 16 * 1024**2
CACHE_SCHEMA = "stpd/private-verified-decision-source-v1"


@lru_cache(maxsize=1)
def implementation_identity() -> str:
    """Bind the installed verifier and projection code, independently of release labels."""
    owner = Path(sts2_platform_evidence.__file__).parent
    files = [("evidence/" + p.relative_to(owner).as_posix(), p)
             for p in owner.rglob("*.py")]
    root = Path(__file__).parent
    files.extend(("research/" + name, root / name) for name in (
        "decision_cache.py", "decision_index.py", "decision_dataset.py",
        "decision_preview.py", "decision_spool.py", "decision_store.py",
        "decision_union.py", "data.py",
        "platform_bundle3.py", "contracts.py", "representation.py",
    ))
    app = root.parents[1] / "spireagent"
    files.extend(("app/" + name, app / name) for name in (
        "encoding.py", "json_boundary.py", "local_verified_bundle.py",
    ))
    return hashlib.sha256(json_bytes([
        [name, hashlib.sha256(path.read_bytes()).hexdigest()]
        for name, path in sorted(files)
    ])).hexdigest()


class VerifiedSourceCache:
    """Private, disposable derivative in the existing trusted operational storage.

    A checksum detects corruption, not hostile rewriting of the private Hub DB. An actor
    who can rewrite that DB already controls membership and sharing. Cache records are
    never imported from artifact manifests, uploads or a user's JSON request.
    """

    def __init__(self, database: Path, producer_identity: str) -> None:
        # Disposable large projections must not inflate the backed-up authority DB
        # or contend with its membership/lease writer during a long cache read.
        self.database = database.with_name(database.stem + ".decision-cache.sqlite")
        if self.database.is_symlink():
            raise BoundaryError("decision_cache", "private_regular_cache_required")
        fd = os.open(self.database, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
                raise BoundaryError("decision_cache", "private_regular_cache_required")
        finally:
            os.close(fd)
        self.owner = hashlib.sha256(json_bytes([
            CACHE_SCHEMA, ADAPTER_ID, implementation_identity(), producer_identity,
        ])).hexdigest()
        self.hits = self.misses = self.corrupt = self.bypassed = self.preview_hits = 0
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS decision_source_cache("
                "key TEXT PRIMARY KEY,owner TEXT NOT NULL,body BLOB NOT NULL,"
                "sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,used REAL NOT NULL)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # The private operational database must already exist; never create a world-readable cache.
        db = sqlite3.connect(self.database.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def metrics(self) -> dict[str, int]:
        return {"cache_hits": self.hits, "cache_misses": self.misses,
                "cache_corrupt": self.corrupt, "cache_bypassed": self.bypassed,
                "preview_hits": self.preview_hits}

    def resolve(self, source: bytes) -> tuple[SourceProjection, dict[str, Any]]:
        import json

        from .decision_dataset import _versions

        source_sha = hashlib.sha256(source).hexdigest()
        key = hashlib.sha256(json_bytes([self.owner, source_sha])).hexdigest()
        with self._connect() as db:
            row = db.execute(
                "SELECT body,sha256 FROM decision_source_cache WHERE key=? AND owner=?",
                (key, self.owner),
            ).fetchone()
        if row:
            try:
                raw = row[0]
                if len(raw) > MAX_ENTRY_BYTES or hashlib.sha256(raw).hexdigest() != row[1]:
                    raise ValueError("cache checksum")
                value = json.loads(raw)
                if (value["schema"] != CACHE_SCHEMA or value["owner"] != self.owner
                        or value["source"] != source_sha or value["bytes"] != len(source)):
                    raise ValueError("cache identity")
                projection = SourceProjection(
                    ADAPTER_ID, source_sha, "platform_verified",
                    FrozenObject.of(value["run_proofs"]),
                    tuple(ResearchTransitionV2.decode(r) for r in value["transitions"]),
                    accounting=FrozenObject.of(value["accounting"]),
                )
                environments = FrozenObject.of(value["environments"]).value()
                with self._connect() as db:
                    db.execute("UPDATE decision_source_cache SET used=? WHERE key=?",
                               (time.time(), key))
                self.hits += 1
                return projection, environments
            except (ValueError, KeyError, TypeError, BoundaryError):
                self.corrupt += 1
                with self._connect() as db:
                    db.execute("DELETE FROM decision_source_cache WHERE key=?", (key,))
        self.misses += 1
        projection = PlatformBundle3SourceAdapter().project(source)
        environments = _versions(source)
        if projection.adapter_id != ADAPTER_ID or projection.scope != "platform_verified":
            raise BoundaryError("decision_cache", "unexpected_owner_projection")
        raw = json_bytes({
            "schema": CACHE_SCHEMA, "owner": self.owner,
            "source": source_sha, "bytes": len(source),
            "run_proofs": projection.run_proofs.value(),
            "transitions": [r.to_dict() for r in projection.transitions],
            "accounting": projection.accounting.value(), "environments": environments,
        })
        if len(raw) > MAX_ENTRY_BYTES:
            self.bypassed += 1
        else:
            with self._connect() as db:
                db.execute("INSERT OR REPLACE INTO decision_source_cache VALUES(?,?,?,?,?,?)",
                           (key, self.owner, raw, hashlib.sha256(raw).hexdigest(),
                            len(raw), time.time()))
                total = db.execute(
                    "SELECT coalesce(sum(bytes),0) FROM decision_source_cache"
                ).fetchone()[0]
                for stale, size in db.execute(
                    "SELECT key,bytes FROM decision_source_cache ORDER BY used,key"
                ).fetchall():
                    if total <= MAX_CACHE_BYTES:
                        break
                    db.execute("DELETE FROM decision_source_cache WHERE key=?", (stale,))
                    total -= size
        return projection, environments
