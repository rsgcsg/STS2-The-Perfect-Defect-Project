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
        handle.seek(0)
        # Windows permits a byte lock beyond EOF; do not read another owner's
        # locked byte merely to initialize the lock file.
        try:
            if os.name == "nt":
                msvcrt = importlib.import_module("msvcrt")

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BoundaryError("project", "already_running") from None
        try:
            yield
        finally:
            if os.name == "nt":
                msvcrt = importlib.import_module("msvcrt")

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


