"""Neutral OS-held owner lock shared by application services."""

from __future__ import annotations

import contextlib
import importlib
import os
from collections.abc import Iterator
from pathlib import Path

from spireagent.json_boundary import BoundaryError


@contextlib.contextmanager
def instance_lock(path: Path, *, create: bool = True) -> Iterator[None]:
    """OS-held lock; process death releases it without PID guesses or stale deletion.

    Observation callers use create=False to avoid initializing an owner path.
    """
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b" if create else "r+b") as handle:
        seek = handle.seek
        seek(0)
        # Windows permits a byte lock beyond EOF; do not read another owner's
        # locked byte merely to initialize the lock file.
        try:
            descriptor = handle.fileno()
            is_windows = os.name == "nt"
            if is_windows:
                msvcrt = importlib.import_module("msvcrt")
                native_lock = msvcrt.locking
                unlock_mode = msvcrt.LK_UNLCK
                native_lock(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                native_lock = fcntl.flock
                unlock_mode = fcntl.LOCK_UN
                native_lock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BoundaryError("project", "already_running") from None
        try:
            yield
        finally:
            # A retained process-lifetime owner can finalize after the import
            # table and module globals are cleared. Keep acquisition's bindings.
            if is_windows:
                seek(0)
                native_lock(descriptor, unlock_mode, 1)
            else:
                native_lock(descriptor, unlock_mode)
