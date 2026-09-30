"""Private files read while another owner may atomically replace them."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO


@contextmanager
def open_replaceable_read(path: Path) -> Iterator[BinaryIO]:
    """Open a snapshot without preventing a concurrent atomic replacement.

    The Windows CRT's ordinary read handle does not share delete access. A
    replacement needs that access even though the reader only reads the old
    version. Keep the handle open only for the caller's read.
    """
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
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
    with open_replaceable_read(path) as stream:
        return stream.read()
