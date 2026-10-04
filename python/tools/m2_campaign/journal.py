"""Durable, one-writer campaign journal; contains no research or paid authority."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

SCHEMA = "m2-campaign-journal-v1"
MAX_METADATA_BYTES = 1024 * 1024
MAX_OBJECT_BYTES = 256 * 1024 * 1024


class JournalError(RuntimeError):
    """Missing, unsafe, corrupt or conflicting durable campaign state."""


def canonical(value: object) -> bytes:
    try:
        raw = (
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError) as error:
        raise JournalError("invalid_json") from error
    if len(raw) > MAX_METADATA_BYTES:
        raise JournalError("metadata_size_limit")
    return raw


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise JournalError("digest_invalid")
    return value


def _sync_dir(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _mkdir(path: Path) -> None:
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise JournalError("directory_unsafe")
    for directory in reversed(missing):
        directory.mkdir()
        _sync_dir(directory.parent)
    if path.is_symlink() or not path.is_dir():
        raise JournalError("directory_unsafe")


def _read(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise JournalError("file_missing_or_unsafe")
    if not 1 <= path.stat().st_size <= limit:
        raise JournalError("file_size_limit")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if not 1 <= len(raw) <= limit:
        raise JournalError("file_size_limit")
    return raw


def _json(path: Path) -> dict[str, Any]:
    raw = _read(path, MAX_METADATA_BYTES)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise JournalError("metadata_corrupt") from error
    if type(value) is not dict or canonical(value) != raw:
        raise JournalError("metadata_noncanonical")
    return value


def _write(path: Path, raw: bytes, *, immutable: bool) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise JournalError("file_unsafe")
    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if _read(path, max(len(raw), 1)) != raw:
                    raise JournalError("immutable_conflict") from None
        else:
            os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _lock(stream: BinaryIO) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise JournalError("campaign_writer_busy") from error


def _unlock(stream: BinaryIO) -> None:
    if sys.platform == "win32":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class CampaignJournal:
    """One root owns one configuration and one serial provider slot across its runs."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().absolute()
        self._active = False
        self._config_sha: str | None = None

    @contextmanager
    def session(self, config: dict[str, Any]) -> Iterator[CampaignJournal]:
        if self._active:
            raise JournalError("session_already_active")
        _mkdir(self.root)
        _mkdir(self.root / "objects")
        _mkdir(self.root / "accepted")
        _mkdir(self.root / "history")
        lock_path = self.root / "writer.lock"
        if lock_path.is_symlink():
            raise JournalError("lock_unsafe")
        with lock_path.open("a+b") as stream:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
            _lock(stream)
            try:
                self._active = True
                self._config_sha = sha256(canonical(config))
                expected = {"schema": SCHEMA, "config": config, "config_sha256": self._config_sha}
                _write(self.root / "config.json", canonical(expected), immutable=True)
                state_path = self.root / "state.json"
                if not state_path.exists():
                    if self._history():
                        raise JournalError("state_missing_with_history")
                    self.save([])
                self.records()
                yield self
            finally:
                self._active = False
                self._config_sha = None
                _unlock(stream)

    def _require_session(self) -> None:
        if not self._active:
            raise JournalError("campaign_lock_required")
        if any(
            path.is_symlink() or not path.is_dir()
            for path in (
                self.root,
                self.root / "objects",
                self.root / "accepted",
                self.root / "history",
            )
        ):
            raise JournalError("directory_unsafe")
        config = _json(self.root / "config.json")
        if (
            set(config) != {"schema", "config", "config_sha256"}
            or config["schema"] != SCHEMA
            or config["config_sha256"] != self._config_sha
            or sha256(canonical(config["config"])) != self._config_sha
        ):
            raise JournalError("config_corrupt")

    def put(self, raw: bytes, *, limit: int = MAX_OBJECT_BYTES) -> dict[str, Any]:
        self._require_session()
        if type(raw) is not bytes or not 1 <= len(raw) <= min(limit, MAX_OBJECT_BYTES):
            raise JournalError("object_size_limit")
        identity = sha256(raw)
        reference: dict[str, Any] = {"sha256": identity, "size": len(raw)}
        _write(self.root / "objects" / identity, raw, immutable=True)
        return reference

    def read(self, reference: dict[str, Any], *, limit: int = MAX_OBJECT_BYTES) -> bytes:
        self._require_session()
        if type(reference) is not dict or set(reference) != {"sha256", "size"}:
            raise JournalError("object_reference_invalid")
        identity = _digest(reference["sha256"])
        size = reference["size"]
        if type(size) is not int or not 1 <= size <= min(limit, MAX_OBJECT_BYTES):
            raise JournalError("object_size_limit")
        raw = _read(self.root / "objects" / identity, size)
        if len(raw) != size or sha256(raw) != identity:
            raise JournalError("object_corrupt")
        return raw

    def records(self) -> list[dict[str, Any]]:
        self._require_session()
        state = _json(self.root / "state.json")
        history = self._history()
        if not history or _json(history[-1]) != state:
            raise JournalError("state_history_conflict")
        if (
            set(state) != {"schema", "config_sha256", "attempts"}
            or state["schema"] != SCHEMA
            or state["config_sha256"] != self._config_sha
            or type(state["attempts"]) is not list
        ):
            raise JournalError("state_invalid")
        records: list[dict[str, Any]] = state["attempts"]
        ids: set[str] = set()
        for record in records:
            if type(record) is not dict or type(record.get("attempt_id")) is not str:
                raise JournalError("attempt_invalid")
            attempt_id = record["attempt_id"]
            if (
                len(attempt_id) != 32
                or any(c not in "0123456789abcdef" for c in attempt_id)
                or attempt_id in ids
            ):
                raise JournalError("attempt_id_invalid")
            ids.add(attempt_id)
            # Objects are checked before any recovery action, including accepted records.
            for key in ("request", "approval", "target", "handle", "result", "stop"):
                reference = record.get(key)
                if reference is not None:
                    self.read(reference)
            self.accepted(record)
        return records

    def save(self, records: list[dict[str, Any]]) -> None:
        self._require_session()
        raw = canonical({"schema": SCHEMA, "config_sha256": self._config_sha, "attempts": records})
        history = self._history()
        # An immutable snapshot prevents rolled-back/edited mutable metadata
        # from reviving an earlier paid transition. An interrupted pointer write
        # fails closed for external reconciliation, rather than dispatching again.
        _write(self.root / "history" / f"{len(history):012d}.json", raw, immutable=True)
        _write(self.root / "state.json", raw, immutable=False)

    def _history(self) -> list[Path]:
        files = sorted((self.root / "history").iterdir())
        for index, path in enumerate(files):
            if path.name != f"{index:012d}.json" or path.is_symlink() or not path.is_file():
                raise JournalError("history_unsafe")
        return files

    def accepted(self, record: dict[str, Any]) -> dict[str, Any] | None:
        self._require_session()
        attempt_id = record.get("attempt_id")
        if (
            type(attempt_id) is not str
            or len(attempt_id) != 32
            or any(c not in "0123456789abcdef" for c in attempt_id)
        ):
            raise JournalError("attempt_id_invalid")
        path = self.root / "accepted" / (attempt_id + ".json")
        if not path.exists() and not path.is_symlink():
            return None
        marker = _json(path)
        if (
            set(marker) != {"schema", "config_sha256", "attempt", "accepted"}
            or marker["schema"] != SCHEMA
            or marker["config_sha256"] != self._config_sha
            or marker["attempt"] != record
            or record.get("phase") != "stopped"
            or record.get("result") is None
            or record.get("stop") is None
            or type(marker["accepted"]) is not dict
        ):
            raise JournalError("accepted_marker_conflict")
        return marker["accepted"]

    def mark_accepted(self, record: dict[str, Any], accepted: dict[str, Any]) -> None:
        self._require_session()
        if record.get("phase") != "stopped":
            raise JournalError("accept_before_stop")
        _write(
            self.root / "accepted" / (record["attempt_id"] + ".json"),
            canonical(
                {
                    "schema": SCHEMA,
                    "config_sha256": self._config_sha,
                    "attempt": record,
                    "accepted": accepted,
                }
            ),
            immutable=True,
        )


__all__ = ["CampaignJournal", "JournalError"]
