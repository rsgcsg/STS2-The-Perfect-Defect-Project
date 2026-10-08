"""Checksummed directory transfer manifests and fail-closed receiving."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import ContentAddressedStore

PromotionVerifier = Callable[[Path, "DirectoryTransferManifest"], None]


def _promote_directory_no_replace(source: Path, destination: Path) -> None:
    """One closed OS publication seam; unsupported primitives never fall back.

    macOS RENAME_EXCL=0x4; Linux RENAME_NOREPLACE=1; Windows rename refuses
    any existing destination. Both paths are owner-created same-filesystem dirs.
    """
    if sys.platform == "win32":
        os.rename(source, destination)
        return
    if sys.platform not in {"darwin", "linux"}:
        raise OSError(errno.ENOTSUP, "exclusive directory promotion unavailable")
    try:
        library = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            function = library.renamex_np
            function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            result = function(os.fsencode(source), os.fsencode(destination), 0x4)
        else:
            function = library.renameat2
            function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                                 ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            result = function(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    except AttributeError as error:
        raise OSError(errno.ENOTSUP, "exclusive directory promotion symbol unavailable") from error
    if result != 0:
        code = ctypes.get_errno() or errno.EIO
        raise OSError(code, os.strerror(code), destination)


def _regular_directory(directory: Path) -> os.stat_result:
    value = directory.lstat()
    if (stat.S_ISLNK(value.st_mode)
            or getattr(value, "st_file_attributes", 0) & 0x400
            or not stat.S_ISDIR(value.st_mode)):
        raise ValueError("destination is not a regular directory")
    return value


@dataclass(frozen=True)
class TransferFile:
    path: str
    bytes: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "bytes": self.bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class DirectoryTransferManifest:
    content_id: str
    artifact_type: str
    files: tuple[TransferFile, ...]
    schema: str = "sts2.evidence/directory-transfer-1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "content_id": self.content_id,
            "artifact_type": self.artifact_type,
            "files": [item.to_dict() for item in self.files],
        }

    @property
    def manifest_sha256(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()).encode("utf-8"))

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        *,
        content_id: str,
        artifact_type: str,
    ) -> "DirectoryTransferManifest":
        files = tuple(
            TransferFile(path, size, digest)
            for path, size, digest in _inventory(Path(directory).resolve())
        )
        result = cls(content_id, artifact_type, files)
        result.validate()
        return result

    @classmethod
    def read(cls, path: str | Path) -> "DirectoryTransferManifest":
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema") != cls.schema:
            raise ValueError("unsupported directory transfer manifest")
        raw_files = value.get("files")
        if not isinstance(raw_files, list):
            raise ValueError("transfer manifest files must be an array")
        result = cls(
            _text(value, "content_id"),
            _text(value, "artifact_type"),
            tuple(
                TransferFile(
                    _safe_relative(_text(item, "path")),
                    _positive_int(item, "bytes", allow_zero=True),
                    _digest(item, "sha256"),
                )
                for item in raw_files
                if isinstance(item, dict)
            ),
        )
        if len(result.files) != len(raw_files):
            raise ValueError("transfer manifest file entry is not an object")
        result.validate()
        return result

    def write(self, path: str | Path) -> Path:
        self.validate()
        destination = Path(path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")
        temporary.write_text(_canonical_json(self.to_dict()) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
        return destination

    def validate(self) -> None:
        if len(self.content_id) != 64 or any(character not in "0123456789abcdef" for character in self.content_id):
            raise ValueError("transfer content_id must be a lowercase SHA-256 digest")
        if not self.artifact_type:
            raise ValueError("transfer artifact_type is required")
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("transfer manifest contains duplicate paths")
        if paths != sorted(paths):
            raise ValueError("transfer manifest files must be sorted")


@dataclass(frozen=True)
class TransferReceipt:
    status: str
    content_id: str
    artifact_type: str
    manifest_sha256: str
    directory: Path | None
    quarantine: Path | None
    findings: tuple[str, ...] = ()


class DirectoryReceiver:
    def __init__(
        self,
        store: ContentAddressedStore,
        *,
        quarantine_root: str | Path | None = None,
        promotion_verifier: PromotionVerifier | None = None,
    ) -> None:
        self.store = store
        self.quarantine_root = Path(quarantine_root or (store.root / "quarantine")).resolve()
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        self.promotion_verifier = promotion_verifier

    def receive(
        self,
        source: str | Path,
        manifest: DirectoryTransferManifest | str | Path,
    ) -> TransferReceipt:
        source_path = Path(source).resolve()
        try:
            transfer = DirectoryTransferManifest.read(manifest) if isinstance(manifest, (str, Path)) else manifest
            transfer.validate()
            self._validate_source(source_path, transfer)
        except (OSError, TypeError, ValueError) as error:
            transfer = manifest if isinstance(manifest, DirectoryTransferManifest) else _unknown_manifest(manifest)
            quarantine = self._quarantine(source_path, "invalid-transfer")
            return TransferReceipt(
                "quarantined",
                transfer.content_id,
                transfer.artifact_type,
                transfer.manifest_sha256,
                None,
                quarantine,
                (str(error),),
            )

        destination = self.store.objects / transfer.content_id
        try:
            _regular_directory(self.store.objects)
        except (OSError, ValueError) as error:
            return TransferReceipt("quarantined", transfer.content_id, transfer.artifact_type,
                                   transfer.manifest_sha256, None,
                                   self._quarantine(source_path, "destination-invalid"), (str(error),))
        try:
            destination.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            return TransferReceipt("quarantined", transfer.content_id, transfer.artifact_type,
                                   transfer.manifest_sha256, None,
                                   self._quarantine(source_path, "destination-invalid"), (str(error),))
        else:
            return self._existing_destination(source_path, destination, transfer)

        staging = Path(tempfile.mkdtemp(prefix=f".{transfer.content_id}.", dir=self.store.objects))
        try:
            _copy_directory(source_path, staging)
            self._validate_source(staging, transfer)
            self._verify_artifact(staging, transfer)
            _regular_directory(self.store.objects)
            try:
                _promote_directory_no_replace(staging, destination)
            except FileExistsError:
                result = self._existing_destination(source_path, destination, transfer)
                shutil.rmtree(staging, ignore_errors=True)
                return result
        except (OSError, TypeError, ValueError) as error:
            shutil.rmtree(staging, ignore_errors=True)
            quarantine = self._quarantine(source_path, "promote-failed")
            return TransferReceipt(
                "quarantined",
                transfer.content_id,
                transfer.artifact_type,
                transfer.manifest_sha256,
                None,
                quarantine,
                (str(error),),
            )
        return TransferReceipt(
            "promoted",
            transfer.content_id,
            transfer.artifact_type,
            transfer.manifest_sha256,
            destination,
            None,
        )

    def _existing_destination(
        self, source: Path, destination: Path, manifest: DirectoryTransferManifest,
    ) -> TransferReceipt:
        # EEXIST is not proof of idempotency. Verify the actual same immutable
        # object, including its type, exact inventory and typed artifact facts.
        try:
            before = _regular_directory(destination)
            self._validate_source(destination, manifest)
        except (OSError, ValueError) as error:
            return TransferReceipt("collision", manifest.content_id, manifest.artifact_type,
                                   manifest.manifest_sha256, None,
                                   self._quarantine(source, "content-collision"), (str(error),))
        try:
            self._verify_artifact(destination, manifest)
            after = _regular_directory(destination)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError("existing object changed during typed verification")
            self._validate_source(destination, manifest)
        except (OSError, ValueError) as error:
            return TransferReceipt("quarantined", manifest.content_id, manifest.artifact_type,
                                   manifest.manifest_sha256, None,
                                   self._quarantine(source, "duplicate-integrity"), (str(error),))
        return TransferReceipt("reused", manifest.content_id, manifest.artifact_type,
                               manifest.manifest_sha256, destination, None)

    def _verify_artifact(self, directory: Path, manifest: DirectoryTransferManifest) -> None:
        if self.promotion_verifier is not None:
            self.promotion_verifier(directory, manifest)

    def _validate_source(self, source: Path, manifest: DirectoryTransferManifest) -> None:
        if not source.is_dir():
            raise ValueError("transfer source directory is absent")
        actual = {path.relative_to(source).as_posix(): path for path in source.rglob("*") if path.is_file()}
        expected = {item.path: item for item in manifest.files}
        if set(actual) != set(expected):
            raise ValueError("transfer is partial or has unexpected files")
        for relative, item in expected.items():
            path = actual[relative]
            if path.stat().st_size != item.bytes or _sha256_file(path) != item.sha256:
                raise ValueError(f"transfer checksum mismatch: {relative}")

    def _quarantine(self, source: Path, reason: str) -> Path | None:
        if not source.is_dir():
            return None
        destination = self.quarantine_root / f"{reason}-{uuid.uuid4().hex}"
        try:
            _copy_directory(source, destination)
        except (OSError, ValueError):
            shutil.rmtree(destination, ignore_errors=True)
            return None
        return destination


def _inventory(directory: Path) -> list[tuple[str, int, str]]:
    if not directory.is_dir():
        raise ValueError("directory is absent")
    result: list[tuple[str, int, str]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("symbolic links are not allowed")
        if path.is_file():
            result.append((path.relative_to(directory).as_posix(), path.stat().st_size, _sha256_file(path)))
    return result


def _copy_directory(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            raise ValueError("symbolic links are not allowed")
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _unknown_manifest(value: object) -> DirectoryTransferManifest:
    return DirectoryTransferManifest("0" * 64, "unknown", ())


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"missing text: {key}")
    return item


def _positive_int(value: dict[str, Any], key: str, *, allow_zero: bool = False) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or (item < 0 if allow_zero else item <= 0):
        raise ValueError(f"invalid integer: {key}")
    return item


def _digest(value: dict[str, Any], key: str) -> str:
    item = _text(value, key)
    if len(item) != 64 or any(character not in "0123456789abcdef" for character in item):
        raise ValueError(f"invalid digest: {key}")
    return item


def _safe_relative(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not value or "\\" in value:
        raise ValueError(f"unsafe relative path: {value}")
    return path.as_posix()
