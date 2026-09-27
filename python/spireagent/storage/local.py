"""Local immutable objects: fsynced temporary bytes, atomic no-overwrite publication."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from spireagent.storage.blobs import MAX_BLOB_BYTES, StoreError, bounded, safe_key


class LocalBlobStore:
    def __init__(self, root: Path, *, create: bool = True, readonly: bool = False) -> None:
        self.root = root.expanduser().resolve()
        self.readonly = readonly
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        elif not self.root.is_dir():
            raise StoreError("local_store_not_configured")

    def _path(self, key: str) -> Path:
        safe_key(key)
        path = self.root.joinpath(*key.split("/"))
        # safe_key forbids traversal. Do not resolve a concurrently linked leaf
        # through an OS-dependent alternate hard-link name. Reject all reparse points.
        current = self.root
        for part in key.split("/"):
            current = current / part
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or (
                getattr(metadata, "st_file_attributes", 0) & 0x400
            ):
                raise StoreError("reparse_object_path")
        return path

    def put_if_absent(self, key: str, data: bytes) -> bool:
        if self.readonly:
            raise StoreError("read_only_store")
        bounded(data)
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if self.get(key) != data:
                    raise StoreError("immutable_key_collision") from None
                return False
            if os.name == "posix":
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            return True
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            with path.open("rb") as handle:
                value = handle.read(MAX_BLOB_BYTES + 1)
        except FileNotFoundError as error:
            raise StoreError("object_not_found") from error
        bounded(value)
        return value

    def keys(self, prefix: str) -> tuple[str, ...]:
        safe_key(prefix, prefix=True)
        return tuple(
            sorted(
                path.relative_to(self.root).as_posix()
                for path in self.root.rglob("*")
                if path.is_file()
                and not path.name.startswith(".pending-")
                and path.relative_to(self.root).as_posix().startswith(prefix)
            )
        )
