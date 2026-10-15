"""Bounded diagnostics for synthetic CI fixtures; never runtime authority.

Callers select the fields. This module never reads environments, frame locals,
configuration files or arbitrary directories, and never decides a test outcome.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import time
import traceback
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

_OPERATION_FIELDS = {
    "schema", "operation_id", "intent_id", "recipe_id", "attempt_id", "status", "stage",
    "validation_state", "worker_state", "writer_terminal", "error_code", "elapsed_seconds",
    "requested_action", "checkpoint_id", "run_id", "input_id", "model_id", "result_id",
}


def operation_snapshot(operation: dict[str, Any]) -> dict[str, Any]:
    """Do not copy private request/config/credentials or arbitrary nested values."""
    result = {key: value for key, value in operation.copy().items()
              if key in _OPERATION_FIELDS
              and (value is None or type(value) in {str, int, float, bool})}
    for name, fields in (
        ("progress", {"unit", "completed", "total"}),
        ("error", {"code", "automatic_retry"}),
        ("child_exit", {"attempt_id", "exit_code", "forced", "elapsed_seconds", "ended_at"}),
    ):
        value = operation.get(name)
        if isinstance(value, dict):
            result[name] = {key: item for key, item in value.items()
                            if key in fields
                            and (item is None or type(item) in {str, int, float, bool})}
    return result


def failure_snapshot(value: dict[str, Any] | None) -> dict[str, Any]:
    """The production parent's existing safe diagnostic, with explicit fields."""
    if not isinstance(value, dict):
        return {}
    result = {key: item for key, item in value.copy().items()
              if key in {"stage", "exception_type", "errno", "winerror"}
              and (item is None or type(item) in {str, int})}
    for name, fields in (("owner_call", {"file", "function", "line"}),
                         ("origin", {"file", "function", "line"}),
                         ("child", {"error_code", "exception_type"})):
        item = value.get(name)
        if isinstance(item, dict):
            result[name] = {key: detail for key, detail in item.items() if key in fields
                            and (detail is None or type(detail) in {str, int})}
    return result


def thread_snapshot(thread: threading.Thread) -> dict[str, Any]:
    frame = sys._current_frames().get(thread.ident) if thread.ident is not None else None
    return {"ident": thread.ident, "native_id": thread.native_id,
            "alive": thread.is_alive(),
            # StackSummary contains file/line/function/source, never frame locals.
            "stack": traceback.format_stack(frame, limit=32) if frame is not None else []}


class FixtureDiagnostics:
    def __init__(self, fixture: str, *, root: Path | None = None) -> None:
        if not re.fullmatch(r"[a-z0-9-]+", fixture):
            raise ValueError("invalid fixture diagnostic name")
        self.fixture = fixture
        self.started = time.monotonic()
        self.directory = (root or Path(__file__).resolve().parents[1] / ".local" /
                          "fixture-diagnostics") / (fixture + "-" + uuid.uuid4().hex)
        self.sequence = 0

    def record(self, phase: str, **details: Any) -> str:
        """Persist the entered phase before a bounded wait; do not mask its result."""
        self.sequence += 1
        path = self.directory / f"{self.sequence:03d}-{phase}.json"
        value = {"fixture": self.fixture, "phase": phase,
                 "elapsed_seconds": time.monotonic() - self.started,
                 "platform": platform.system(), "python": platform.python_version(), **details}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            return str(path)
        except (OSError, TypeError, ValueError) as error:
            # Exception type only: an arbitrary exception message can carry data.
            with suppress(OSError):
                print(f"fixture_diagnostic_write_failed={type(error).__name__}", flush=True)
            return "diagnostic_unavailable"

    def training_log(self, directory: Path, operation: dict[str, Any]) -> dict[str, Any]:
        """Only the exact synthetic attempt log, with bounded original byte tail."""
        operation_id, attempt_id = (operation.get(key) for key in ("operation_id", "attempt_id"))
        if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value)
                   for value in (operation_id, attempt_id)):
            return {"status": "unbound"}
        path = directory / f"local-training-{operation_id}-{attempt_id}.log"
        try:
            if path.is_symlink():
                return {"status": "symlink_rejected"}
            with path.open("rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - 8192))
                tail = stream.read(8192)
            return {"status": "observed", "name": path.name, "bytes": size,
                    "tail_original": repr(tail), "truncated": size > len(tail)}
        except OSError as error:
            return {"status": "unavailable", "error_type": type(error).__name__}
