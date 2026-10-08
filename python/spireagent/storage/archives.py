"""Existing bounded deterministic original-byte transport, shared without admission.

Archives preserve originals; extraction grants no origin, research or use authority.
The historical Platform bundle adapter imports these same helpers unchanged.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import tempfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

from spireagent.json_boundary import BoundaryError

MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 20000


def archive_bundle(directory: Path) -> bytes:
    """Deterministic transport of an existing bundle; does not edit or attest it."""
    paths = sorted(directory.rglob("*"))
    if any(p.is_symlink() for p in paths):
        raise BoundaryError("source_archive", "symlink_forbidden")
    files = [p for p in paths if p.is_file()]
    if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > MAX_BYTES:
        raise BoundaryError("source_archive", "size_limit")
    target = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=target, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for path in files:
            content = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(directory).as_posix())
            info.size = len(content)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(content))
    return target.getvalue()


def _extract(raw: bytes, directory: Path) -> None:
    if len(raw) > MAX_BYTES:
        raise BoundaryError("source_archive", "size_limit")
    try:
        # Bound the entire decompressed stream, including PAX/longname headers
        # that tarfile consumes before yielding a member for our inventory checks.
        with tempfile.TemporaryFile() as expanded:
            total_expanded = 0
            with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as compressed:
                while chunk := compressed.read(1024 * 1024):
                    total_expanded += len(chunk)
                    if total_expanded > MAX_BYTES:
                        raise BoundaryError("source_archive", "expanded_size_limit")
                    expanded.write(chunk)
            expanded.seek(0)
            _extract_tar(expanded, directory)
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, zlib.error) as error:
        raise BoundaryError("source_archive", "invalid_archive") from error


def _extract_tar(expanded: Any, directory: Path) -> None:
    # Streaming mode prevents a malicious metadata size from issuing an enormous
    # direct file read, even though the expanded archive is already disk-bounded.
    with tarfile.open(fileobj=expanded, mode="r|") as archive:
        names: set[str] = set()
        total = 0
        for info in archive:
            total += info.size
            if len(names) >= MAX_FILES or total > MAX_BYTES:
                raise BoundaryError("source_archive", "size_limit")
            path = PurePosixPath(info.name)
            folded = info.name.casefold()
            if (
                not info.name
                or "\\" in info.name
                or path.is_absolute()
                or any(p in {".", ".."} for p in info.name.split("/"))
                or ":" in info.name
                or str(path) != info.name
                or not info.isfile()
                or folded in names
                or info.size < 0
            ):
                raise BoundaryError("source_archive", "unsafe_or_duplicate_member")
            names.add(folded)
            member = archive.extractfile(info)
            if member is None:
                raise BoundaryError("source_archive", "missing_member")
            content = member.read(info.size + 1)
            if len(content) != info.size:
                raise BoundaryError("source_archive", "member_size_mismatch")
            target = directory / info.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
