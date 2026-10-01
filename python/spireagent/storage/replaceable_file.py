"""Private files read while another owner may atomically replace them."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, BinaryIO

_WINDOWS_READ_ATTEMPTS = 10
_WINDOWS_READ_DELAY = 0.01


@contextmanager
def open_replaceable_read(path: Path) -> Iterator[BinaryIO]:
    """Open a snapshot without preventing a concurrent atomic replacement.

    The Windows CRT's ordinary read handle does not share delete access. A
    replacement needs that access even though the reader only reads the old
    version. Keep the handle open only for the caller's read.
    """
    if sys.platform != "win32":
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    else:
        import importlib
        from ctypes import wintypes

        ctypes: Any = importlib.import_module("ctypes")
        msvcrt: Any = importlib.import_module("msvcrt")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD,
                                wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create_file.restype = wintypes.HANDLE
        handle = create_file(str(path), 0x80000000, 0x1 | 0x2 | 0x4,
                             None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(
                handle, os.O_RDONLY | os.__dict__["O_BINARY"])
        except Exception:
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(handle)
            raise
    with os.fdopen(descriptor, "rb") as stream:
        yield stream


def read_replaceable_bytes(path: Path) -> bytes:
    # ReplaceFileW combines rename/publication steps. A pathname can briefly be
    # absent or unavailable even though the writer will publish successfully.
    # Reconcile only Windows read observations, never a replacement or operation.
    for attempt in range(_WINDOWS_READ_ATTEMPTS):
        try:
            with open_replaceable_read(path) as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("replaceable file must be regular")
                return stream.read()
        except OSError as error:
            if (getattr(error, "winerror", None) not in {2, 3, 5, 32}
                    or attempt == _WINDOWS_READ_ATTEMPTS - 1):
                raise
            time.sleep(_WINDOWS_READ_DELAY)
    raise AssertionError("unreachable replaceable read")


def _replace_existing_windows(temporary: Path, path: Path, backup: Path) -> None:
    import importlib
    from ctypes import wintypes

    ctypes: Any = importlib.import_module("ctypes")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    replace_file = kernel32.ReplaceFileW
    replace_file.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                             wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID)
    replace_file.restype = wintypes.BOOL
    if not replace_file(str(path), str(temporary), str(backup), 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def has_unresolved_replacement(path: Path) -> bool:
    stem = "." + path.name
    return any(path.parent.glob(stem + ".pending-*")) or any(
        path.parent.glob(stem + ".previous-*"))


def _write_json(path: Path, value: Any,
                replace_existing: Callable[[Path, Path, Path], None] | None) -> None:
    """Publish a training journal while a share-delete reader holds the old file.

    The owner lock serializes writers. A failed Windows replacement retains its
    backup for manual recovery; it never retries or replays the operation.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    stem = "." + path.name
    descriptor, name = tempfile.mkstemp(prefix=stem + ".pending-", dir=path.parent)
    temporary = Path(name)
    backup: Path | None = None
    replaced = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if replace_existing is not None and (path.exists() or path.is_symlink()):
            backup = path.with_name(stem + ".previous-" + uuid.uuid4().hex)
            replace_existing(temporary, path, backup)
        else:
            os.replace(temporary, path)
        replaced = True
        if replace_existing is None:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if replaced:
            temporary.unlink(missing_ok=True)
        if replaced and backup is not None:
            # Publication succeeded. A failed cleanup leaves a visible recovery
            # copy; it must not turn a committed stage into a reported failure.
            with suppress(OSError):
                backup.unlink(missing_ok=True)


def write_replaceable_json(path: Path, value: Any) -> None:
    _write_json(path, value, _replace_existing_windows if os.name == "nt" else None)
