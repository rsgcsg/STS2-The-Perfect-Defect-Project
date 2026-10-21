"""Neutral OS-held owner lock shared by application services."""

from __future__ import annotations

import contextlib
import importlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path

from spireagent.json_boundary import BoundaryError


def _windows_shared_lease(descriptor: int) -> Callable[[], None]:
    """Win32 shared byte lock; CRT LK_NBRLCK is still exclusive."""
    import ctypes
    from ctypes import wintypes

    msvcrt = importlib.import_module("msvcrt")
    windows = importlib.import_module("ctypes")

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    kernel = windows.WinDLL("kernel32", use_last_error=True)
    lock, unlock = kernel.LockFileEx, kernel.UnlockFileEx
    pointer = ctypes.POINTER(Overlapped)
    lock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                     wintypes.DWORD, wintypes.DWORD, pointer]
    unlock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, pointer]
    lock.restype = unlock.restype = wintypes.BOOL
    handle, offset = msvcrt.get_osfhandle(descriptor), Overlapped()
    byref = ctypes.byref
    winerror, last_error = windows.WinError, windows.get_last_error
    if not lock(handle, 1, 0, 1, 0, byref(offset)):
        raise winerror(last_error())

    def release() -> None:
        if not unlock(handle, 0, 1, 0, byref(offset)):
            raise winerror(last_error())

    return release


@contextlib.contextmanager
def instance_lock(path: Path, *, create: bool = True, shared: bool = False) -> Iterator[None]:
    """OS-held lock; process death releases it without PID guesses or stale deletion.

    Observation callers use create=False to avoid initializing an owner path.
    """
    if shared and create:
        raise ValueError("shared lease requires an existing owner path")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b" if create else "rb" if shared else "r+b") as handle:
        seek = handle.seek
        seek(0)
        # Windows permits a byte lock beyond EOF; do not read another owner's
        # locked byte merely to initialize the lock file.
        try:
            descriptor = handle.fileno()
            is_windows = os.name == "nt"
            if is_windows and shared:
                release_shared = _windows_shared_lease(descriptor)
            elif is_windows:
                msvcrt = importlib.import_module("msvcrt")
                native_lock = msvcrt.locking
                unlock_mode = msvcrt.LK_UNLCK
                native_lock(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                native_lock = fcntl.flock
                unlock_mode = fcntl.LOCK_UN
                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                native_lock(descriptor, mode | fcntl.LOCK_NB)
        except OSError:
            raise BoundaryError("project", "already_running") from None
        try:
            yield
        finally:
            # A retained process-lifetime owner can finalize after the import
            # table and module globals are cleared. Keep acquisition's bindings.
            if is_windows and shared:
                release_shared()
            elif is_windows:
                seek(0)
                native_lock(descriptor, unlock_mode, 1)
            else:
                native_lock(descriptor, unlock_mode)
