"""Learner-local FULL SQLite journal: durable facts, never a replay engine.

A single writer commits complete original requests and responses in bounded
batches. Callers apply their in-memory state only after append returns. Reopened
or replicated journals provide receipts but never authorize reusing old grants.
"""

from __future__ import annotations

import hashlib
import json
import queue
import sqlite3
import threading
import time
from concurrent.futures import Future
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from spireagent.json_boundary import json_bytes
from spireagent.rl_dataplane_ports import WAL_SCHEMA


class WalUncertain(RuntimeError):
    code = "WAL_UNCERTAIN"


class LocalControlWAL:
    def __init__(
        self,
        path: Path,
        *,
        run: str,
        generation: str,
        authoritative: bool = True,
        batch_ms: float = 5,
        batch_size: int = 64,
        max_bytes: int = 4 * 1024**3,
        max_pending_bytes: int = 64 * 1024**2,
    ) -> None:
        if not 0 <= batch_ms <= 5 or not 1 <= batch_size <= 64:
            raise ValueError("invalid_wal_group_commit_bounds")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.run, self.generation = path, run, generation
        self.authoritative = authoritative
        self.batch_seconds, self.batch_size = batch_ms / 1000, batch_size
        self.lock = threading.RLock()
        self.failure: BaseException | None = None
        self.closed = False
        self.pending: queue.Queue[Any] = queue.Queue(maxsize=2048)
        self.pending_keys: set[str] = set()
        self.pending_bytes = 0
        self.max_bytes, self.max_pending_bytes = max_bytes, max_pending_bytes
        self.changed = threading.Event()
        from spireagent.instance_lock import instance_lock

        self.guard = ExitStack()
        if authoritative:
            self.guard.enter_context(instance_lock(Path(str(path) + ".owner.lock")))
        try:
            self._open_database(path, run, generation)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self.guard.close()
            raise
        # WAL readers use a separate connection. FULL commit/fsync belongs only
        # to the writer; enqueue/status/receipt never contend on that IO lock.
        self.reader_lock = threading.Lock()
        self.reader = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.reader.execute("PRAGMA query_only=ON")
        self.writer = threading.Thread(target=self._write, name="rl-wal", daemon=True)
        self.writer.start()

    def _open_database(self, path: Path, run: str, generation: str) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("CREATE TABLE IF NOT EXISTS owner (binding BLOB NOT NULL)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS facts (
            seq INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
            request BLOB NOT NULL, response BLOB NOT NULL, previous TEXT,
            hash TEXT NOT NULL, created REAL NOT NULL)""")
        binding = json_bytes({"schema": WAL_SCHEMA, "run": run, "generation": generation})
        owner = self.db.execute("SELECT binding FROM owner").fetchone()
        self.reopened = owner is not None
        if owner is None:
            self.db.execute("INSERT INTO owner VALUES (?)", (binding,))
        elif bytes(owner[0]) != binding:
            self.db.close()
            raise ValueError("wal_owner_identity_changed")
        self.sequence, self.head = 0, None
        for seq, key, kind, raw, response, previous, digest, created in self.db.execute(
            "SELECT * FROM facts ORDER BY seq"
        ):
            if not isinstance(raw, bytes) or not isinstance(response, bytes):
                self.db.close()
                raise ValueError("wal_chain_integrity_failure")
            body = self._body(seq, key, kind, bytes(raw), bytes(response), previous, created)
            if (
                seq != self.sequence + 1
                or previous != self.head
                or hashlib.sha256(body).hexdigest() != digest
            ):
                self.db.close()
                raise ValueError("wal_chain_integrity_failure")
            self.sequence, self.head = seq, digest

    @staticmethod
    def _body(
        seq: int,
        key: str,
        kind: str,
        request: bytes,
        response: bytes,
        previous: str | None,
        created: float,
    ) -> bytes:
        return json_bytes(
            {
                "schema": WAL_SCHEMA,
                "sequence": seq,
                "key": key,
                "kind": kind,
                "request": json.loads(request),
                "response": json.loads(response),
                "previous": previous,
                "created": created,
            }
        )

    def append(
        self, key: str, request: dict[str, Any], response: dict[str, Any], *, kind: str = "input"
    ) -> dict[str, Any]:
        return self.append_async(key, request, response, kind=kind).result()

    def append_async(
        self, key: str, request: dict[str, Any], response: dict[str, Any], *, kind: str = "input"
    ) -> Future[dict[str, Any]]:
        # Freeze before enqueue; a caller cannot change a queued original body.
        raw, reply = json_bytes(request), json_bytes(response)
        future: Future[dict[str, Any]] = Future()
        # Submission cannot be revoked once its durability outcome is unknown.
        future.set_running_or_notify_cancel()
        with self.lock:
            if self.closed or self.failure is not None or not self.authoritative or self.reopened:
                raise WalUncertain("wal_owner_fenced")
            if self.pending_bytes + len(raw) + len(reply) > self.max_pending_bytes:
                raise WalUncertain("wal_pending_byte_limit")
            self.pending.put_nowait((key, kind, raw, reply, future))
            self.pending_keys.add(key)
            self.pending_bytes += len(raw) + len(reply)
        return future

    def _write(self) -> None:
        while True:
            item = self.pending.get()
            if item is None:
                return
            batch = [item]
            until = time.monotonic() + self.batch_seconds
            while len(batch) < self.batch_size:
                try:
                    next_item = self.pending.get_nowait()
                except queue.Empty:
                    break
                if next_item is None:
                    self.pending.put_nowait(None)
                    break
                batch.append(next_item)
                if time.monotonic() >= until:
                    break
            results: list[dict[str, Any]] = []
            try:
                with self.lock:
                    if self.failure is not None:
                        raise WalUncertain("wal_owner_fenced")
                    seq, head = self.sequence, self.head
                if (
                    self._bytes() + sum(len(item[2]) + len(item[3]) for item in batch)
                    > self.max_bytes
                ):
                    raise WalUncertain("wal_disk_byte_limit")
                self.db.execute("BEGIN IMMEDIATE")
                for key, kind, raw, reply, _future in batch:
                    old = self.db.execute("SELECT * FROM facts WHERE key=?", (key,)).fetchone()
                    if old is not None:
                        if old[2] != kind or bytes(old[3]) != raw or bytes(old[4]) != reply:
                            raise ValueError("wal_original_request_or_response_changed")
                        results.append(self._record(old))
                        continue
                    seq += 1
                    created = time.time()
                    digest = hashlib.sha256(
                        self._body(seq, key, kind, raw, reply, head, created)
                    ).hexdigest()
                    row = (seq, key, kind, raw, reply, head, digest, created)
                    self.db.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?)", row)
                    results.append(self._record(row))
                    head = digest
                self.db.execute("COMMIT")
                with self.lock:
                    self.sequence, self.head = seq, head
                    for key, *_parts in batch:
                        self.pending_keys.discard(key)
                    self.changed.set()
            except BaseException as error:
                with self.lock:
                    self.failure = error
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                for *_parts, future in batch:
                    future.set_exception(WalUncertain(str(error)))
            else:
                for item, result in zip(batch, results, strict=True):
                    item[-1].set_result(result)
            finally:
                with self.lock:
                    self.pending_bytes -= sum(len(item[2]) + len(item[3]) for item in batch)

    @staticmethod
    def _record(row: Any) -> dict[str, Any]:
        seq, key, kind, raw, response, previous, digest, created = row
        return {
            "schema": WAL_SCHEMA,
            "sequence": seq,
            "key": key,
            "kind": kind,
            "request": json.loads(raw),
            "response": json.loads(response),
            "previous": previous,
            "hash": digest,
            "created": created,
        }

    def receipt(self, key: str, request: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            with self.reader_lock:
                row = self.reader.execute("SELECT * FROM facts WHERE key=?", (key,)).fetchone()
            if row is not None:
                if bytes(row[3]) != json_bytes(request):
                    raise ValueError("wal_original_request_changed")
                return {
                    "status": "FOUND",
                    "found": True,
                    "response": json.loads(row[4]),
                    "durable_position": row[0],
                    "record_hash": row[6],
                }
            # A prefix, reopened owner or failed writer cannot certify absence.
            proven = (
                self.authoritative
                and not self.reopened
                and not self.closed
                and self.failure is None
                and key not in self.pending_keys
                and request.get("run") == self.run
                and request.get("training_generation") == self.generation
            )
            return {
                "status": "NOT_ACCEPTED" if proven else "UNKNOWN",
                "found": False,
                "response": None,
                "durable_position": self.sequence,
            }

    def events_since(self, sequence: int, *, limit: int = 256) -> list[dict[str, Any]]:
        if type(sequence) is not int or sequence < 0 or not 1 <= limit <= 1024:
            raise ValueError("invalid_event_cursor")
        with self.reader_lock:
            rows = self.reader.execute(
                "SELECT * FROM facts WHERE seq>? AND kind='event' ORDER BY seq LIMIT ?",
                (sequence, limit),
            ).fetchall()
        return [self._record(row) for row in rows]

    def records_since(self, sequence: int, *, limit: int = 256) -> list[dict[str, Any]]:
        with self.reader_lock:
            rows = self.reader.execute(
                "SELECT * FROM facts WHERE seq>? ORDER BY seq LIMIT ?", (sequence, limit)
            ).fetchall()
        return [self._record(row) for row in rows]

    def _bytes(self) -> int:
        return sum(
            p.stat().st_size for p in (self.path, Path(str(self.path) + "-wal")) if p.exists()
        )

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "schema": WAL_SCHEMA,
                "local_durable_position": self.sequence,
                "local_durable_hash": self.head,
                "replicated_durable_position": None,
                "input_fenced": self.failure is not None or self.reopened,
                "pending_operations": self.pending.qsize(),
                "pending_bytes": self.pending_bytes,
                "wal_max_bytes": self.max_bytes,
                "wal_bytes": self._bytes(),
            }

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
        self.pending.put(None)
        self.writer.join(timeout=10)
        if self.writer.is_alive():
            raise WalUncertain("wal_writer_did_not_close")
        with self.lock, self.reader_lock:
            self.reader.close()
            self.db.close()
        self.guard.close()


def read_replica_block(raw: bytes) -> dict[str, Any]:
    """Validate an immutable prefix block. Missing later blocks always mean UNKNOWN."""
    block: dict[str, Any] = json.loads(raw)
    if (
        block.get("schema") != WAL_SCHEMA
        or block.get("kind") != "replica_block"
        or not isinstance(block.get("records"), list)
        or not block["records"]
    ):
        raise ValueError("invalid_wal_replica_block")
    previous, seq = block["previous_hash"], block["first_sequence"] - 1
    for record in block["records"]:
        seq += 1
        body = LocalControlWAL._body(
            seq,
            record["key"],
            record["kind"],
            json_bytes(record["request"]),
            json_bytes(record["response"]),
            previous,
            record["created"],
        )
        if (
            record["sequence"] != seq
            or record["previous"] != previous
            or hashlib.sha256(body).hexdigest() != record["hash"]
        ):
            raise ValueError("wal_replica_hash_chain_changed")
        previous = record["hash"]
    if seq != block["last_sequence"] or previous != block["head_hash"]:
        raise ValueError("wal_replica_range_changed")
    return block


class WalReplication:
    """Bounded logical export to the original immutable Store/archive adapter.

    Publication and readback run only in this worker. They never participate in
    admission ACK. The local WAL remains authoritative if a remote prefix lags.
    """

    def __init__(
        self, wal: LocalControlWAL, target: Any, *, interval: float = 1, block_bytes: int = 1024**2
    ) -> None:
        if interval <= 0 or block_bytes < 1:
            raise ValueError("invalid_wal_replication_bounds")
        self.wal, self.target = wal, target
        self.interval, self.block_bytes = interval, block_bytes
        self.position, self.head = 0, None
        self.error: dict[str, Any] | None = None
        self.pending_since: float | None = None
        self.pending_bytes = 0
        self.last_verified_at: float | None = None
        self.stopping = threading.Event()
        self.lock = threading.RLock()
        self.worker = threading.Thread(target=self._run, name="rl-wal-replica", daemon=True)
        self.worker.start()

    def step(self) -> bool:
        records: list[dict[str, Any]] = []
        size = 0
        for record in self.wal.records_since(self.position):
            length = len(json_bytes(record))
            if records and size + length > self.block_bytes:
                break
            records.append(record)
            size += length
        with self.lock:
            self.pending_bytes = size
            if records and self.pending_since is None:
                self.pending_since = time.time()
        if not records:
            return False
        block = {
            "schema": WAL_SCHEMA,
            "kind": "replica_block",
            "run": self.wal.run,
            "training_generation": self.wal.generation,
            "first_sequence": records[0]["sequence"],
            "last_sequence": records[-1]["sequence"],
            "previous_hash": records[0]["previous"],
            "head_hash": records[-1]["hash"],
            "records": records,
        }
        raw = json_bytes(block)
        read_replica_block(raw)
        key = (
            f"exchange/rl/{self.wal.run}/generations/{self.wal.generation}/control-wal/"
            f"{block['first_sequence']:012d}-{block['last_sequence']:012d}-{block['head_hash']}.json"
        )
        self.target.put_if_absent(key, raw)
        if self.target.get(key) != raw:
            raise ValueError("wal_replica_readback_changed")
        with self.lock:
            self.position, self.head = block["last_sequence"], block["head_hash"]
            self.error = self.pending_since = None
            self.pending_bytes = 0
            self.last_verified_at = time.time()
        return True

    def _run(self) -> None:
        while not self.stopping.wait(self.interval):
            try:
                # Drain bounded blocks, yield promptly when stopping. No scan of
                # shared files or synchronous remote calls in the control owner.
                while not self.stopping.is_set() and self.step():
                    pass
            except Exception as error:
                with self.lock:
                    self.error = {
                        "type": type(error).__name__,
                        "code": getattr(error, "code", None),
                    }

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "replicated_durable_position": self.position,
                "replicated_durable_hash": self.head,
                "replication_error": self.error,
                "replication_pending_bytes": self.pending_bytes,
                "replication_lag_operations": max(0, self.wal.sequence - self.position),
                "replication_oldest_age": (
                    time.time() - self.pending_since if self.pending_since else 0.0
                ),
                "replication_last_verified_at": self.last_verified_at,
            }

    def close(self) -> None:
        self.stopping.set()
        self.worker.join(timeout=5)
        if self.worker.is_alive():
            raise RuntimeError("wal_replication_close_pending_local_journal_retained")
