"""Single-Hub transactional operations, revocable identities and fenced attempts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from typing import Any, cast

from spireagent.json_boundary import BoundaryError

CURRENT_SCHEMA = 4


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_private_database(path: Path) -> None:
    """Own the new inode before SQLite writes private state or creates WAL sidecars.

    Exclusive creation never adjusts an existing database or a symlink target.
    Existing deployment permissions remain an explicit operator/preflight contract.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:
            path.chmod(0o600)
    finally:
        os.close(descriptor)


class Operations:
    """One authoritative operational DB; do not delete it like a Registry cache."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with suppress(FileExistsError):
            create_private_database(path)
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, 2, 3, CURRENT_SCHEMA}:
                raise BoundaryError("hub", "unsupported_operations_schema")
            schema = """
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO settings VALUES('paused','0');
                CREATE TABLE IF NOT EXISTS devices(
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS uploads(
                    id TEXT PRIMARY KEY, device TEXT NOT NULL, content_id TEXT NOT NULL,
                    manifest_sha TEXT NOT NULL, intent TEXT NOT NULL, status TEXT NOT NULL,
                    receipt TEXT, verify_attempts INTEGER NOT NULL DEFAULT 0,
                    retry_at REAL NOT NULL DEFAULT 0, last_error TEXT, UNIQUE(device,content_id));
                CREATE INDEX IF NOT EXISTS upload_verification_queue ON uploads(status,retry_at);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                    input_id TEXT NOT NULL, max_seconds INTEGER NOT NULL,
                    reserved_units INTEGER NOT NULL, status TEXT NOT NULL,
                    fence INTEGER NOT NULL DEFAULT 0, attempt_id TEXT, lease_hash TEXT,
                    lease_until REAL, deadline REAL, provider_ref TEXT, result TEXT,
                    options TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS compute_attempts(
                    job_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, fence INTEGER NOT NULL,
                    request_id TEXT NOT NULL, request TEXT NOT NULL, target_id TEXT NOT NULL,
                    phase TEXT NOT NULL, handle TEXT, cancel_state TEXT, last_error TEXT,
                    receipt TEXT);
                CREATE TABLE IF NOT EXISTS events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                    actor TEXT NOT NULL, operation TEXT NOT NULL, subject TEXT NOT NULL,
                    detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_users(
                    subject TEXT PRIMARY KEY, issuer TEXT NOT NULL,
                    access_subject TEXT NOT NULL, email TEXT NOT NULL,
                    UNIQUE(issuer,access_subject));
                CREATE TABLE IF NOT EXISTS identity_flows(
                    id TEXT PRIMARY KEY, client_hash TEXT NOT NULL, device_name TEXT NOT NULL,
                    device_id TEXT, device_proof_hash TEXT, user_code TEXT NOT NULL,
                    created_at REAL NOT NULL, expires_at REAL NOT NULL, status TEXT NOT NULL,
                    key_id TEXT NOT NULL, subject TEXT, session_hash TEXT,
                    session_expires_at REAL, new_device INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS identity_sessions(
                    token_hash TEXT PRIMARY KEY, subject TEXT NOT NULL,
                    expires_at REAL NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_rates(
                    source TEXT NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY(source,bucket));
                CREATE TABLE IF NOT EXISTS identity_members(
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, issuer TEXT NOT NULL,
                    access_subject TEXT, subject TEXT UNIQUE,
                    role TEXT NOT NULL CHECK(role IN ('member','admin')),
                    status TEXT NOT NULL CHECK(status IN ('invited','active','disabled')),
                    enroll_devices INTEGER NOT NULL DEFAULT 1 CHECK(enroll_devices IN (0,1)),
                    device_quota INTEGER NOT NULL DEFAULT 3 CHECK(device_quota BETWEEN 0 AND 128),
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_claim_scopes(
                    member_id TEXT NOT NULL, device_id TEXT NOT NULL,
                    PRIMARY KEY(member_id,device_id));
            """
            # These fixed statements contain no embedded semicolons. executescript would
            # implicitly commit our transaction and expose a partially applied migration.
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(statement)
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "options" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN options TEXT NOT NULL DEFAULT '{}'")
            device_columns = {row[1] for row in db.execute("PRAGMA table_info(devices)")}
            for name, kind in (("name", "TEXT"), ("owner_subject", "TEXT"), ("last_seen", "REAL")):
                if name not in device_columns:
                    db.execute(f"ALTER TABLE devices ADD COLUMN {name} {kind}")
            from spireagent.hub.campaigns import create_campaign_tables

            create_campaign_tables(db)
            db.execute(f"PRAGMA user_version={CURRENT_SCHEMA}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw",
            uri=True,
            timeout=30,
            isolation_level=None,
        )
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db: sqlite3.Connection, actor: str, op: str, subject: str, detail: Any) -> None:
        db.execute(
            "INSERT INTO events(at,actor,operation,subject,detail) VALUES(?,?,?,?,?)",
            (time.time(), actor, op, subject, json.dumps(detail, sort_keys=True)),
        )

    def register(self, device: str, token: str) -> None:
        if not device or len(device) > 128 or len(token) < 32:
            raise BoundaryError("hub", "invalid_device_credential")
        with self.transaction() as db:
            db.execute(
                "INSERT INTO devices(id,token_hash) VALUES(?,?)", (device, token_hash(token))
            )
            self._event(db, "admin", "device_registered", device, {})

    def authenticate(self, token: str) -> str:
        with self.transaction() as db:
            return str(self.authenticated_device(db, token)["id"])

    @staticmethod
    def device_authorized(db: sqlite3.Connection, device: str) -> bool:
        """Every credential/capability seam also respects its bound owner's revocation."""
        return (
            db.execute(
                "SELECT 1 FROM devices d WHERE d.id=? AND d.active=1 AND "
                "(d.owner_subject IS NULL OR EXISTS(SELECT 1 FROM identity_members m "
                "WHERE m.subject=d.owner_subject AND m.status='active'))",
                (device,),
            ).fetchone()
            is not None
        )

    @classmethod
    def authenticated_device(cls, db: sqlite3.Connection, token: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM devices WHERE token_hash=?", (token_hash(token),)
        ).fetchone()
        if row is None or not cls.device_authorized(db, row["id"]):
            raise BoundaryError("hub", "unauthorized")
        return cast(sqlite3.Row, row)

    def revoke(self, device: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE devices SET active=0 WHERE id=?", (device,))
            self._event(db, "admin", "device_revoked", device, {})

    def rotate_device(self, device: str, token: str) -> None:
        """Explicit operator rotation preserves device/upload identity and restores access."""
        if len(token) < 32 or any(c in token for c in "\r\n"):
            raise BoundaryError("hub", "invalid_device_credential")
        with self.transaction() as db:
            row = db.execute(
                "SELECT token_hash,active,owner_subject FROM devices WHERE id=?", (device,)
            ).fetchone()
            if row is None or row["token_hash"] == token_hash(token):
                raise BoundaryError("hub", "device_rotation_not_applicable")
            if (
                row["owner_subject"]
                and db.execute(
                    "SELECT 1 FROM identity_members WHERE subject=? AND status='active'",
                    (row["owner_subject"],),
                ).fetchone()
                is None
            ):
                raise BoundaryError("hub", "device_owner_disabled")
            db.execute(
                "UPDATE devices SET token_hash=?,active=1 WHERE id=?", (token_hash(token), device)
            )
            self._event(
                db,
                "admin",
                "device_credential_rotated",
                device,
                {"previously_active": bool(row["active"])},
            )

    def create_upload(
        self, device: str, content_id: str, manifest_sha: str, intent: dict[str, Any]
    ) -> dict[str, Any]:
        encoded = json.dumps(intent, sort_keys=True, separators=(",", ":"))
        with self.transaction() as db:
            old = db.execute(
                "SELECT * FROM uploads WHERE device=? AND content_id=?", (device, content_id)
            ).fetchone()
            if old is not None:
                if old["manifest_sha"] != manifest_sha:
                    raise BoundaryError("hub", "content_manifest_conflict")
                if old["intent"] != encoded and old["status"] != "verified":
                    raise BoundaryError("hub", "upload_transport_changed")
                return dict(old)
            upload_id = secrets.token_hex(16)
            pending = db.execute(
                "SELECT COUNT(*) FROM uploads WHERE device=? AND status IN "
                "('awaiting_upload','verification_pending')",
                (device,),
            ).fetchone()[0]
            if pending >= 16:
                raise BoundaryError("hub", "upload_queue_limit")
            db.execute(
                "INSERT INTO uploads(id,device,content_id,manifest_sha,intent,status,receipt) "
                "VALUES(?,?,?,?,?,?,NULL)",
                (upload_id, device, content_id, manifest_sha, encoded, "awaiting_upload"),
            )
            self._event(db, device, "upload_created", upload_id, {"content_id": content_id})
            row = db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
            return dict(row)

    def uploads(
        self,
        device: str | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
        statuses: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Bounded status projection; immutable transport intent is loaded only by ID."""
        if (
            type(limit) is not int
            or not 1 <= limit <= 1000
            or type(offset) is not int
            or offset < 0
        ):
            raise BoundaryError("hub", "invalid_upload_pagination")
        allowed = {
            "awaiting_upload",
            "verification_pending",
            "verified",
            "quarantined",
            "transfer_failed",
        }
        if statuses is not None and (
            not isinstance(statuses, tuple)
            or any(not isinstance(value, str) or value not in allowed for value in statuses)
        ):
            raise BoundaryError("hub", "invalid_upload_status_filter")
        conditions: list[str] = []
        values: list[Any] = []
        if device is not None:
            conditions.append("device=?")
            values.append(device)
        if statuses is not None:
            if not statuses:
                return []
            conditions.append("status IN (" + ",".join("?" for _ in statuses) + ")")
            values.extend(statuses)
        query = (
            "SELECT id,device,content_id,status,receipt,retry_at,verify_attempts,last_error "
            "FROM uploads"
        )
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY rowid DESC LIMIT ? OFFSET ?"
        values.extend((limit, offset))
        with self.transaction() as db:
            rows = db.execute(query, values).fetchall()
            return [dict(row) for row in rows]

    def pending_upload(self, now: float) -> dict[str, Any] | None:
        """Return one due verification candidate without materializing historical intents."""
        if not math.isfinite(now):
            raise BoundaryError("hub", "invalid_verification_time")
        with self.transaction() as db:
            row = db.execute(
                "SELECT id,device,content_id,status,receipt,retry_at,verify_attempts,last_error "
                "FROM uploads WHERE status='verification_pending' AND retry_at<=? "
                "ORDER BY retry_at,rowid LIMIT 1",
                (now,),
            ).fetchone()
            return None if row is None else dict(row)

    def upload_counts(self, device: str | None = None) -> dict[str, int]:
        with self.transaction() as db:
            query = "SELECT status,COUNT(*) FROM uploads"
            values: tuple[str, ...] = ()
            if device is not None:
                query += " WHERE device=?"
                values = (device,)
            return {
                str(row[0]): int(row[1])
                for row in db.execute(query + " GROUP BY status", values).fetchall()
            }

    def upload(self, upload_id: str, device: str | None = None) -> dict[str, Any]:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
            if row is None or (device is not None and row["device"] != device):
                raise BoundaryError("hub", "upload_not_found")
            return dict(row)

    def request_verification(self, upload_id: str) -> None:
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE uploads SET status='verification_pending' "
                "WHERE id=? AND status='awaiting_upload'",
                (upload_id,),
            )
            if changed.rowcount == 1:
                self._event(db, "receiver", "upload_verification_requested", upload_id, {})

    def verification_failure(self, upload_id: str, code: str, *, now: float) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT verify_attempts,status FROM uploads WHERE id=?", (upload_id,)
            ).fetchone()
            if row is None or row["status"] != "verification_pending":
                return
            attempts = int(row[0]) + 1
            status = "transfer_failed" if attempts >= 5 else "verification_pending"
            db.execute(
                "UPDATE uploads SET verify_attempts=?,retry_at=?,last_error=?,status=? WHERE id=?",
                (attempts, now + min(300, 2**attempts), code, status, upload_id),
            )
            self._event(
                db,
                "receiver",
                "verification_retry",
                upload_id,
                {"attempt": attempts, "code": code, "status": status},
            )

    def retry_upload(self, upload_id: str) -> None:
        """Explicit operational retry retains the exact immutable transport intent."""
        with self.transaction() as db:
            row = db.execute("SELECT status FROM uploads WHERE id=?", (upload_id,)).fetchone()
            if row is None or row[0] != "transfer_failed":
                raise BoundaryError("hub", "upload_retry_not_applicable")
            db.execute(
                "UPDATE uploads SET status='awaiting_upload',verify_attempts=0,"
                "retry_at=0,last_error=NULL WHERE id=?",
                (upload_id,),
            )
            self._event(db, "admin", "upload_retry_authorized", upload_id, {})

    def finish_upload(self, upload_id: str, receipt: dict[str, Any]) -> None:
        if receipt.get("status") not in {"verified", "quarantined"}:
            raise BoundaryError("hub", "invalid_receive_disposition")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
            if row is None or (receipt.get("content_id"), receipt.get("manifest_sha256")) != (
                row["content_id"],
                row["manifest_sha"],
            ):
                raise BoundaryError("hub", "receipt_identity_mismatch")
            encoded = json.dumps(receipt, sort_keys=True)
            if row["receipt"] is not None and row["receipt"] != encoded:
                raise BoundaryError("hub", "receipt_conflict")
            db.execute(
                "UPDATE uploads SET status=?,receipt=? WHERE id=?",
                (receipt["status"], encoded, upload_id),
            )
            self._event(db, "verifier", "upload_receipt", upload_id, receipt)

    def enqueue(
        self,
        kind: str,
        input_id: str,
        request_key: str,
        *,
        max_seconds: int,
        reserved_units: int,
        budget_limit: int,
        options: dict[str, Any] | None = None,
    ) -> str:
        options = {} if options is None else options
        if not isinstance(options, dict) or set(options) - {"resume", "stop_after"}:
            raise BoundaryError("hub", "invalid_job_options")
        resume, stop_after = options.get("resume"), options.get("stop_after")
        if resume is not None and (
            not isinstance(resume, str)
            or len(resume) != 64
            or any(c not in "0123456789abcdef" for c in resume)
        ):
            raise BoundaryError("hub", "invalid_resume")
        if stop_after is not None and (type(stop_after) is not int or stop_after <= 0):
            raise BoundaryError("hub", "invalid_stop_after")
        if kind == "feature" and (resume is not None or stop_after is not None):
            raise BoundaryError("hub", "feature_resume_not_supported")
        encoded_options = json.dumps(options, sort_keys=True, separators=(",", ":"))
        if (
            kind not in {"feature", "training"}
            or type(max_seconds) is not int
            or not 0 < max_seconds <= 86400
        ):
            raise BoundaryError("hub", "invalid_job")
        if len(input_id) != 64 or any(c not in "0123456789abcdef" for c in input_id):
            raise BoundaryError("hub", "invalid_job_input")
        if (
            not request_key
            or type(reserved_units) is not int
            or type(budget_limit) is not int
            or reserved_units <= 0
            or budget_limit <= 0
        ):
            raise BoundaryError("hub", "invalid_budget")
        with self.transaction() as db:
            old = db.execute("SELECT * FROM jobs WHERE request_key=?", (request_key,)).fetchone()
            if old is not None:
                if (
                    old["kind"],
                    old["input_id"],
                    old["max_seconds"],
                    old["reserved_units"],
                    old["options"],
                ) != (
                    kind,
                    input_id,
                    max_seconds,
                    reserved_units,
                    encoded_options,
                ):
                    raise BoundaryError("hub", "job_request_conflict")
                return str(old["id"])
            # Reservations remain consumed after completion until an explicit new campaign.
            total = int(
                db.execute("SELECT COALESCE(SUM(reserved_units),0) FROM jobs").fetchone()[0]
            )
            if total + reserved_units > budget_limit:
                raise BoundaryError("hub", "budget_exhausted")
            job_id = secrets.token_hex(16)
            db.execute(
                "INSERT INTO jobs(id,request_key,kind,input_id,max_seconds,reserved_units,"
                "status,options) VALUES(?,?,?,?,?,?,'queued',?)",
                (job_id, request_key, kind, input_id, max_seconds, reserved_units, encoded_options),
            )
            self._event(db, "admin", "job_enqueued", job_id, {"input_id": input_id})
            return job_id

    def jobs(self) -> list[dict[str, Any]]:
        with self.transaction() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY rowid")]

    def claim(self, owner: str, *, now: float, lease_seconds: int = 120) -> dict[str, Any] | None:
        if type(lease_seconds) is not int or not 0 < lease_seconds <= 600 or not math.isfinite(now):
            raise BoundaryError("hub", "invalid_lease")
        with self.transaction() as db:
            if db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0] == "1":
                return None
            active = db.execute(
                "SELECT 1 FROM jobs WHERE status IN "
                "('running','submission_unknown','uncertain','cancelling')"
            ).fetchone()
            if active:
                return None
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            token = secrets.token_urlsafe(32)
            attempt = secrets.token_hex(16)
            deadline = now + int(row["max_seconds"])
            db.execute(
                "UPDATE jobs SET status='running',fence=fence+1,attempt_id=?,lease_hash=?,"
                "lease_until=?,deadline=?,provider_ref=NULL WHERE id=?",
                (
                    attempt,
                    token_hash(token),
                    min(now + lease_seconds, deadline),
                    deadline,
                    row["id"],
                ),
            )
            self._event(db, owner, "job_claimed", row["id"], {"attempt_id": attempt})
            claimed = dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
            claimed.pop("lease_hash")
            return {**claimed, "lease_token": token}

    @staticmethod
    def _live(db: sqlite3.Connection, job: str, token: str, now: float) -> sqlite3.Row:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if (
            not math.isfinite(now)
            or row is None
            or row["status"] != "running"
            or row["lease_until"] <= now
            or not (secrets.compare_digest(row["lease_hash"] or "", token_hash(token)))
        ):
            raise BoundaryError("hub", "stale_attempt")
        return cast(sqlite3.Row, row)

    def heartbeat(self, job: str, token: str, *, now: float, lease_seconds: int = 120) -> None:
        if type(lease_seconds) is not int or not 0 < lease_seconds <= 600 or not math.isfinite(now):
            raise BoundaryError("hub", "invalid_lease")
        with self.transaction() as db:
            row = self._live(db, job, token, now)
            db.execute(
                "UPDATE jobs SET lease_until=? WHERE id=?",
                (min(now + lease_seconds, row["deadline"]), job),
            )

    def submitted(self, job: str, token: str, reference: str, *, now: float) -> None:
        if not reference or len(reference) > 512:
            raise BoundaryError("hub", "invalid_provider_reference")
        with self.transaction() as db:
            row = self._live(db, job, token, now)
            if row["provider_ref"] not in {None, reference}:
                raise BoundaryError("hub", "provider_reference_conflict")
            db.execute("UPDATE jobs SET provider_ref=? WHERE id=?", (reference, job))

    def submission_unknown(self, job: str, token: str, *, now: float) -> None:
        with self.transaction() as db:
            self._live(db, job, token, now)
            db.execute("UPDATE jobs SET status='submission_unknown' WHERE id=?", (job,))
            self._event(db, "scheduler", "submission_unknown", job, {})

    def expire(self, *, now: float) -> int:
        if not math.isfinite(now):
            raise BoundaryError("hub", "invalid_lease")
        with self.transaction() as db:
            result = db.execute(
                "UPDATE jobs SET status='uncertain' WHERE status='running' AND lease_until<=?",
                (now,),
            )
            return result.rowcount

    def complete(self, job: str, token: str, result: dict[str, Any], *, now: float) -> None:
        encoded = json.dumps(result, sort_keys=True)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if row is not None and row["status"] == "completed":
                if row["result"] == encoded and secrets.compare_digest(
                    row["lease_hash"] or "", token_hash(token)
                ):
                    return
                raise BoundaryError("hub", "completion_conflict")
            self._live(db, job, token, now)
            db.execute(
                "UPDATE jobs SET status='completed',result=? WHERE id=?",
                (encoded, job),
            )
            self._event(db, "scheduler", "job_completed", job, result)

    def cancel(self, job: str) -> None:
        with self.transaction() as db:
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()
            if row is None:
                raise BoundaryError("hub", "job_not_found")
            status = "cancelled" if row[0] == "queued" else "cancelling"
            if row[0] in {"completed", "paused", "failed", "cancelled"}:
                return
            db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job))
            self._event(db, "admin", "cancel_requested", job, {})

    def reconcile_stopped(self, job: str, evidence: str, *, requeue: bool = False) -> None:
        if not evidence.strip():
            raise BoundaryError("hub", "provider_stop_evidence_required")
        with self.transaction() as db:
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()
            if row is None or row[0] not in {"uncertain", "submission_unknown", "cancelling"}:
                raise BoundaryError("hub", "reconciliation_not_applicable")
            if requeue:
                raise BoundaryError("hub", "new_budgeted_job_required")
            target = "cancelled" if row[0] == "cancelling" else "failed"
            db.execute("UPDATE jobs SET status=?,lease_hash=NULL WHERE id=?", (target, job))
            self._event(db, "admin", "provider_stopped", job, {"evidence": evidence})

    def pause(self, paused: bool) -> None:
        with self.transaction() as db:
            if (
                not paused
                and db.execute(
                    "SELECT 1 FROM jobs WHERE status IN "
                    "('running','uncertain','submission_unknown','cancelling')"
                ).fetchone()
            ):
                raise BoundaryError("hub", "reconcile_external_jobs_before_unpause")
            db.execute("UPDATE settings SET value=? WHERE key='paused'", ("1" if paused else "0",))
            self._event(db, "admin", "pause_changed", "hub", {"paused": paused})

    def paused(self) -> bool:
        with self.transaction() as db:
            return bool(
                db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0] == "1"
            )

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            create_private_database(destination)
        except FileExistsError:
            raise BoundaryError("hub", "backup_exists") from None
        with (
            closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as source,
            closing(
                sqlite3.connect(destination.resolve().as_uri() + "?mode=rw", uri=True)
            ) as target,
        ):
            source.backup(target)
            target.execute("UPDATE settings SET value='1' WHERE key='paused'")
            target.commit()
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BoundaryError("hub", "backup_integrity")

    @staticmethod
    def _compute_fenced(
        db: sqlite3.Connection,
        job: str,
        token: str,
        attempt: str,
        fence: int,
    ) -> sqlite3.Row:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if (
            row is None
            or row["attempt_id"] != attempt
            or row["fence"] != fence
            or not secrets.compare_digest(row["lease_hash"] or "", token_hash(token))
        ):
            raise BoundaryError("hub", "stale_attempt")
        return cast(sqlite3.Row, row)

    def recover_compute(
        self,
        job: str,
        attempt: str,
        fence: int,
        *,
        now: float,
    ) -> dict[str, Any]:
        """Internal scheduler handoff under its OS singleton lock; never resubmit.

        Rotate only the local scheduler secret, keeping the external request and fence.
        Expiry stays uncertain; an unknown worker is not declared stopped by recovery.
        """
        if not math.isfinite(now):
            raise BoundaryError("hub", "invalid_lease")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if (
                row is None
                or row["attempt_id"] != attempt
                or row["fence"] != fence
                or row["status"] not in {"running", "uncertain", "submission_unknown", "cancelling"}
            ):
                raise BoundaryError("hub", "stale_attempt")
            token = secrets.token_urlsafe(32)
            status = row["status"]
            if status == "running" and row["lease_until"] <= now:
                status = "uncertain"
            db.execute(
                "UPDATE jobs SET lease_hash=?,status=? WHERE id=?", (token_hash(token), status, job)
            )
            self._event(
                db,
                "scheduler",
                "attempt_owner_recovered",
                job,
                {"attempt_id": attempt, "fence": fence},
            )
            updated = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone())
            updated.pop("lease_hash")
            return {**updated, "lease_token": token}

    def compute_state(self, job: str) -> dict[str, Any] | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM compute_attempts WHERE job_id=?", (job,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            for name in ("request", "handle", "receipt"):
                result[name] = json.loads(result[name]) if result[name] is not None else None
            return result

    def prepare_compute(
        self,
        job: str,
        token: str,
        request: dict[str, Any],
        target_id: str,
        *,
        now: float,
    ) -> bool:
        """Persist the external invocation intent before calling the provider exactly once."""
        from spireagent.json_boundary import digest
        from stpd.cloud_jobs.contracts import ComputeRequest

        value = ComputeRequest.decode(request)
        digest(target_id, "compute.target_id")
        encoded = json.dumps(request, sort_keys=True)
        with self.transaction() as db:
            row = self._live(db, job, token, now)
            if (
                value.attempt_id != row["attempt_id"]
                or value.input_id != row["input_id"]
                or value.kind != {"feature": "features", "training": "training"}[row["kind"]]
            ):
                raise BoundaryError("hub", "compute_request_job_mismatch")
            options = json.loads(row["options"])
            if (value.resume, value.stop_after) != (
                options.get("resume"),
                options.get("stop_after"),
            ):
                raise BoundaryError("hub", "compute_request_options_mismatch")
            prior = db.execute("SELECT * FROM compute_attempts WHERE job_id=?", (job,)).fetchone()
            if prior is not None:
                if (prior["request"], prior["target_id"], prior["fence"]) != (
                    encoded,
                    target_id,
                    row["fence"],
                ):
                    raise BoundaryError("hub", "compute_request_conflict")
                return False
            db.execute(
                "INSERT INTO compute_attempts(job_id,attempt_id,fence,request_id,request,"
                "target_id,phase) VALUES(?,?,?,?,?,?,'submitting')",
                (job, value.attempt_id, row["fence"], value.request_id, encoded, target_id),
            )
            self._event(
                db,
                "scheduler",
                "compute_submission_intent",
                job,
                {"request_id": value.request_id, "target_id": target_id},
            )
            return True

    def bind_compute_handle(
        self,
        job: str,
        token: str,
        attempt: str,
        fence: int,
        handle: dict[str, Any],
    ) -> None:
        from spireagent.compute import decode_compute_handle

        value = decode_compute_handle(handle)
        encoded = json.dumps(handle, sort_keys=True)
        with self.transaction() as db:
            row = self._compute_fenced(db, job, token, attempt, fence)
            current = db.execute("SELECT * FROM compute_attempts WHERE job_id=?", (job,)).fetchone()
            if (
                row["status"] not in {"running", "uncertain", "submission_unknown", "cancelling"}
                or current is None
                or current["request_id"] != value.request.request_id
                or current["target_id"] != value.target_id
                or value.request.attempt_id != attempt
            ):
                raise BoundaryError("hub", "compute_handle_mismatch")
            if current["handle"] is not None and current["handle"] != encoded:
                raise BoundaryError("hub", "provider_reference_conflict")
            db.execute(
                "UPDATE compute_attempts SET handle=?,phase='submitted' WHERE job_id=?",
                (encoded, job),
            )
            db.execute("UPDATE jobs SET provider_ref=? WHERE id=?", (value.call_id, job))
            self._event(
                db,
                "scheduler",
                "compute_handle_bound",
                job,
                {"request_id": value.request.request_id, "provider_ref": value.call_id},
            )

    def compute_uncertain(
        self,
        job: str,
        token: str,
        attempt: str,
        fence: int,
        code: str,
    ) -> None:
        from spireagent.json_boundary import text

        text(code, "compute.code", maximum=128)
        with self.transaction() as db:
            row = self._compute_fenced(db, job, token, attempt, fence)
            if row["status"] not in {"running", "uncertain", "submission_unknown", "cancelling"}:
                raise BoundaryError("hub", "stale_attempt")
            current = db.execute("SELECT * FROM compute_attempts WHERE job_id=?", (job,)).fetchone()
            no_handle = current is None or current["handle"] is None
            status = "submission_unknown" if no_handle else "uncertain"
            if row["status"] == "cancelling":
                status = "cancelling"
            if current is None and row["status"] == status:
                return
            if current is not None:
                if current["last_error"] == code and row["status"] == status:
                    return
                db.execute("UPDATE compute_attempts SET last_error=? WHERE job_id=?", (code, job))
            db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job))
            self._event(db, "scheduler", "compute_uncertain", job, {"code": code})

    def fail_before_compute(self, job: str, token: str, code: str, *, now: float) -> None:
        """Only the invocation owner may reject preflight before creating external intent."""
        from spireagent.json_boundary import text

        text(code, "compute.code", maximum=128)
        with self.transaction() as db:
            row = self._live(db, job, token, now)
            if (
                row["provider_ref"] is not None
                or db.execute("SELECT 1 FROM compute_attempts WHERE job_id=?", (job,)).fetchone()
            ):
                raise BoundaryError("hub", "submission_may_exist")
            db.execute(
                "UPDATE jobs SET status='failed',result=? WHERE id=?",
                (json.dumps({"stage": "preflight", "code": code}), job),
            )
            self._event(db, "scheduler", "compute_preflight_failed", job, {"code": code})

    def accept_compute(
        self,
        job: str,
        token: str,
        attempt: str,
        fence: int,
        receipt: dict[str, Any],
        *,
        cancelled: bool = False,
    ) -> None:
        """Fenced selection after owner validation, including exact late completion.

        A cancelling job can only settle cancelled; its output is never selected.
        A paused bounded invocation is terminal and requires a new budgeted resume job.
        """
        from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest

        value = ComputeReceipt.decode(receipt)
        encoded = json.dumps(receipt, sort_keys=True)
        target = "cancelled" if cancelled else "paused" if value.state == "paused" else "completed"
        with self.transaction() as db:
            row = self._compute_fenced(db, job, token, attempt, fence)
            current = db.execute("SELECT * FROM compute_attempts WHERE job_id=?", (job,)).fetchone()
            if (
                current is None
                or current["handle"] is None
                or current["attempt_id"] != attempt
                or current["fence"] != fence
            ):
                raise BoundaryError("hub", "compute_handle_required")
            value.bind(ComputeRequest.decode(json.loads(current["request"])))
            if row["status"] == target and current["receipt"] == encoded:
                return
            permitted = (
                {"cancelling"} if cancelled else {"running", "uncertain", "submission_unknown"}
            )
            if row["status"] not in permitted:
                raise BoundaryError("hub", "stale_attempt")
            selected = (
                json.dumps(
                    {
                        "state": "cancelled",
                        "request_id": value.request_id,
                        "terminal_receipt_recorded": True,
                    }
                )
                if cancelled
                else encoded
            )
            db.execute("UPDATE jobs SET status=?,result=? WHERE id=?", (target, selected, job))
            db.execute(
                "UPDATE compute_attempts SET phase=?,receipt=? WHERE job_id=?",
                (target, encoded, job),
            )
            self._event(db, "scheduler", "compute_" + target, job, receipt)

    def accept_compute_termination(
        self, job: str, token: str, attempt: str, fence: int, termination: dict[str, Any],
    ) -> str:
        """Settle only a verified original process exit, retaining budget and handle."""
        from spireagent.compute import ComputeTermination, decode_compute_handle
        from stpd.cloud_jobs.contracts import ComputeRequest

        value = ComputeTermination.decode(termination)
        encoded = json.dumps(termination, sort_keys=True)
        with self.transaction() as db:
            row = self._compute_fenced(db, job, token, attempt, fence)
            current = db.execute("SELECT * FROM compute_attempts WHERE job_id=?", (job,)).fetchone()
            if (current is None or current["handle"] is None
                    or current["attempt_id"] != attempt or current["fence"] != fence):
                raise BoundaryError("hub", "compute_handle_required")
            handle = decode_compute_handle(json.loads(current["handle"]))
            value.bind(handle)
            if (handle.request != ComputeRequest.decode(json.loads(current["request"]))
                    or current["target_id"] != value.target_id
                    or row["provider_ref"] != value.call_id):
                raise BoundaryError("hub", "compute_termination_binding_mismatch")
            if row["status"] in {"failed", "cancelled"} and row["result"] == encoded:
                return str(row["status"])
            if row["status"] not in {"running", "uncertain", "submission_unknown", "cancelling"}:
                raise BoundaryError("hub", "stale_attempt")
            status = "cancelled" if row["status"] == "cancelling" else "failed"
            db.execute("UPDATE jobs SET status=?,result=? WHERE id=?", (status, encoded, job))
            db.execute("UPDATE compute_attempts SET phase=?,last_error=? WHERE job_id=?",
                       (status, "execution_" + value.outcome, job))
            self._event(db, "scheduler", "compute_process_terminated", job, termination)
            return status

    def begin_compute_cancel(self, job: str, token: str, attempt: str, fence: int) -> bool:
        with self.transaction() as db:
            row = self._compute_fenced(db, job, token, attempt, fence)
            current = db.execute("SELECT * FROM compute_attempts WHERE job_id=?", (job,)).fetchone()
            if row["status"] != "cancelling" or current is None or current["handle"] is None:
                raise BoundaryError("hub", "cancel_handle_required")
            if current["cancel_state"] is not None:
                return False
            db.execute(
                "UPDATE compute_attempts SET cancel_state='requested' WHERE job_id=?", (job,)
            )
            self._event(db, "scheduler", "compute_cancel_requested", job, {})
            return True

    def record_compute_cancel(
        self,
        job: str,
        token: str,
        attempt: str,
        fence: int,
        *,
        acknowledged: bool,
    ) -> None:
        with self.transaction() as db:
            row = self._compute_fenced(db, job, token, attempt, fence)
            if row["status"] != "cancelling":
                raise BoundaryError("hub", "stale_attempt")
            state = "acknowledged" if acknowledged else "unknown"
            db.execute("UPDATE compute_attempts SET cancel_state=? WHERE job_id=?", (state, job))
            self._event(db, "scheduler", "compute_cancel_" + state, job, {})
