"""Bounded, private application diagnostics; never execution or Evidence state.

Only explicit application scopes enable the shared phase markers. Exception
messages, source lines, locals and request values are intentionally not serialized.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_BYTES = 1024 * 1024
BACKUPS = 2
_write_lock = threading.Lock()


class TimedRLock:
    """Opt-in bounded lock timings; Condition sleep is excluded from hold time."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.local = threading.local()
        self.rows: dict[tuple[str, str], dict[str, float]] = {}

    def _caller(self) -> str:
        frame = sys._getframe(2)
        while frame.f_back and frame.f_code.co_filename in {__file__, threading.__file__}:
            frame = frame.f_back
        return f"{frame.f_code.co_name}:{frame.f_lineno}"

    def _record(self, caller: str, field: str, seconds: float) -> None:
        key = threading.current_thread().name, caller
        # Every writer already owns self.lock. A second mutex here adds a
        # scheduling boundary inside every measured critical section.
        if key not in self.rows and len(self.rows) >= 128:
            key = "other", "other"
        row = self.rows.setdefault(key, {"wait_seconds": 0., "hold_seconds": 0.,
                                          "acquires": 0.})
        row[field] += seconds
        if field == "wait_seconds":
            row["acquires"] += 1

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        caller, began = self._caller(), time.perf_counter()
        acquired = self.lock.acquire(blocking, timeout)
        if acquired:
            arrived = time.perf_counter()
            if not getattr(self.local, "depth", 0):
                self.local.started, self.local.caller = arrived, caller
            self.local.depth = getattr(self.local, "depth", 0) + 1
            self._record(caller, "wait_seconds", arrived - began)
        return acquired

    def release(self) -> None:
        if self.local.depth == 1:
            self._record(self.local.caller, "hold_seconds",
                         time.perf_counter() - self.local.started)
        self.local.depth -= 1
        self.lock.release()

    def _release_save(self) -> Any:
        depth, caller = self.local.depth, self.local.caller
        self._record(caller, "hold_seconds", time.perf_counter() - self.local.started)
        self.local.depth = 0
        return self.lock._release_save(), depth, caller  # type: ignore[attr-defined]

    def _acquire_restore(self, state: Any) -> None:
        raw, depth, caller = state
        began = time.perf_counter()
        self.lock._acquire_restore(raw)  # type: ignore[attr-defined]
        arrived = time.perf_counter()
        self.local.depth, self.local.caller = depth, caller
        self.local.started = arrived
        self._record(caller, "wait_seconds", arrived - began)

    def _is_owned(self) -> bool:
        return self.lock._is_owned()  # type: ignore[attr-defined, no-any-return]

    def __enter__(self) -> TimedRLock:
        self.acquire()
        return self

    def __exit__(self, *_error: Any) -> None:
        self.release()

    def snapshot(self) -> list[dict[str, Any]]:
        # Read under the same uninstrumented RLock; no second lock order and no
        # recursive diagnostic acquire is introduced by observing the counters.
        with self.lock:
            return [{"thread": thread, "caller": caller, **row}
                    for (thread, caller), row in self.rows.items()]


@dataclass
class _Trace:
    log: LocalDiagnostics
    path: str
    action: str
    started: float = field(default_factory=time.monotonic)
    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    errors: dict[int, tuple[BaseException, str]] = field(default_factory=dict)
    bindings: dict[str, str] = field(default_factory=dict)
    phases: list[str] = field(default_factory=list)


_current: ContextVar[_Trace | None] = ContextVar("local_diagnostic_trace", default=None)


def exception_facts(error: BaseException) -> dict[str, Any]:
    """Bounded stack locations for durable failures, without messages or locals."""
    result: dict[str, Any] = {
        "exception_type": type(error).__name__,
        "stack": [{"file": frame.filename[-512:], "line": frame.lineno,
                   "function": frame.name[:128]}
                  for frame in traceback.extract_tb(error.__traceback__, limit=32)],
    }
    remote = getattr(error, "remote_diagnostic", None)
    if remote is not None:
        result["remote"] = remote
    return result


class LocalDiagnostics:
    def __init__(self, state_dir: Path) -> None:
        self.directory = state_dir / "logs"

    def _write(self, name: str, value: dict[str, Any]) -> None:
        # Diagnostics cannot change the original request/dispatch outcome on an
        # unwritable/full disk. There is no alternative control/status ledger.
        try:
            raw = (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
            with _write_lock:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                path = self.directory / name
                if path.exists() and path.stat().st_size + len(raw) > MAX_BYTES:
                    for index in range(BACKUPS, 0, -1):
                        old = path if index == 1 else path.with_name(name + f".{index - 1}")
                        if old.exists():
                            old.replace(path.with_name(name + f".{index}"))
                flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags, 0o600)
                with os.fdopen(descriptor, "ab") as handle:
                    handle.write(raw)
        except OSError:
            return

    @contextmanager
    def operation(self, path: str, action: str) -> Iterator[None]:
        active = _current.get()
        if active is not None:
            if active.path == "http":
                active.path, active.action = path, action
            yield
            return
        trace = _Trace(self, path, action)
        token = _current.set(trace)
        try:
            yield
        except BaseException as error:
            self.error(error)
            raise
        finally:
            _current.reset(token)

    def error(self, error: BaseException, *, path: str | None = None,
              action: str | None = None, stage: str | None = None) -> str:
        trace = _current.get() or _Trace(self, path or "application", action or "request")
        previous = trace.errors.get(id(error))
        if previous is not None and previous[0] is error:
            return previous[1]
        error_id = uuid.uuid4().hex
        trace.errors[id(error)] = (error, error_id)
        facts = exception_facts(error)
        self._write("errors.jsonl", {
            **_facts(trace), "event": "exception", "error_id": error_id,
            "path": path or trace.path, "action": action or trace.action,
            "stage": stage or (trace.phases[-1] if trace.phases else "request"),
            **facts,
        })
        return error_id


def _facts(trace: _Trace) -> dict[str, Any]:
    return {"time_utc": datetime.now(UTC).isoformat(),
            "operation_id": trace.operation_id, "path": trace.path, "action": trace.action,
            "elapsed_ms": round((time.monotonic() - trace.started) * 1000, 3),
            **trace.bindings}


def bind_job(job_id: str, *, parent_job_id: str | None = None) -> None:
    """Correlate already-owned local jobs across HTTP and scheduler threads."""
    trace = _current.get()
    if trace is not None:
        trace.bindings["job_id"] = job_id
        if parent_job_id is not None:
            trace.bindings["parent_job_id"] = parent_job_id


@contextmanager
def phase(name: str) -> Iterator[None]:
    """No-op outside an explicitly enabled local application operation."""
    trace = _current.get()
    if trace is None:
        yield
        return
    started = time.monotonic()
    trace.phases.append(name)
    trace.log._write("phases.jsonl", {**_facts(trace), "event": "phase_started", "stage": name})
    outcome, error_id = "completed", None
    try:
        yield
    except BaseException as error:
        outcome = "failed"
        error_id = trace.log.error(error, stage=name)
        raise
    finally:
        trace.log._write("phases.jsonl", {
            **_facts(trace), "event": "phase_finished", "stage": name, "outcome": outcome,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            **({"error_id": error_id} if error_id is not None else {}),
        })
        trace.phases.pop()
