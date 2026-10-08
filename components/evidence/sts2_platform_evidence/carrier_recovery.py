"""Explicit exact-inventory carrier recovery; original evidence never changes.

Only unlisted, enumerated filesystem metadata may be excluded from a NEW carrier.
The existing bundle verifier and typed receiver remain the qualification owners.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .human_session_bundle import CollectionProfile, verify_human_session_bundle
from .human_session_bundle_v1 import _read_checksums
from .store import ContentAddressedStore
from .transfer import DirectoryReceiver, DirectoryTransferManifest, TransferFile, TransferReceipt

RECEIPT_SCHEMA = "sts2.evidence/carrier-recovery-1"
METADATA_POLICY = "macos-finder-ds-store-1"
PERMITTED_METADATA_NAMES = frozenset({".DS_Store"})
MAX_FILES = 50_000
MAX_BYTES = 512 * 1024 * 1024
MAX_CHECKSUM_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


class CarrierRecoveryError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code + (": " + detail if detail else ""))


@dataclass(frozen=True)
class CarrierRecoveryResult:
    transfer: TransferReceipt
    receipt_path: Path
    receipt_sha256: str
    receipt_bytes: bytes

    @property
    def receipt(self) -> dict[str, Any]:
        return json.loads(self.receipt_bytes)


@dataclass(frozen=True)
class _File:
    relative: str
    bytes: int
    sha256: str
    identity: tuple[int, int, int, int, int]

    def transfer_file(self) -> TransferFile:
        return TransferFile(self.relative, self.bytes, self.sha256)


@dataclass(frozen=True)
class _Snapshot:
    files: tuple[_File, ...]
    directories: tuple[tuple[str, int, int], ...]


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _is_link(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & 0x400)


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or path.as_posix() != value):
        raise CarrierRecoveryError("unsafe_path", value)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise CarrierRecoveryError("unsafe_path", "non-scalar file name") from error
    return value


def _alias(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _stream(path: Path, expected: tuple[int, int, int, int, int],
            sink: BinaryIO | None = None) -> tuple[int, str]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or _identity(before) != expected:
            raise CarrierRecoveryError("source_changed", path.name)
        size = 0
        digest = hashlib.sha256()
        while chunk := handle.read(CHUNK_BYTES):
            size += len(chunk)
            if size > expected[2]:
                raise CarrierRecoveryError("source_changed", path.name)
            digest.update(chunk)
            if sink is not None:
                sink.write(chunk)
        if size != expected[2] or _identity(os.fstat(handle.fileno())) != expected:
            raise CarrierRecoveryError("source_changed", path.name)
    return size, digest.hexdigest()


def _snapshot(root: Path) -> _Snapshot:
    root_stat = root.lstat()
    if _is_link(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
        raise CarrierRecoveryError("source_directory_required")
    directories = [("", root_stat.st_dev, root_stat.st_ino)]
    files: list[_File] = []
    paths: set[str] = set()
    identities: set[tuple[int, int]] = set()
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir(), key=lambda p: p.name):
            relative = _relative(path.relative_to(root).as_posix())
            alias = _alias(relative)
            if alias in paths:
                raise CarrierRecoveryError("duplicate_path_alias", relative)
            paths.add(alias)
            if len(paths) > MAX_FILES:
                raise CarrierRecoveryError("carrier_limit", "too many entries")
            value = path.lstat()
            if _is_link(value):
                raise CarrierRecoveryError("source_link", relative)
            if stat.S_ISDIR(value.st_mode):
                directories.append((relative, value.st_dev, value.st_ino))
                pending.append(path)
                continue
            if not stat.S_ISREG(value.st_mode):
                raise CarrierRecoveryError("source_not_regular", relative)
            inode = (value.st_dev, value.st_ino)
            if inode in identities:
                raise CarrierRecoveryError("duplicate_file_alias", relative)
            identities.add(inode)
            total += value.st_size
            if len(files) >= MAX_FILES or total > MAX_BYTES:
                raise CarrierRecoveryError("carrier_limit", "byte/file limit")
            size, digest = _stream(path, _identity(value))
            files.append(_File(relative, size, digest, _identity(value)))
    return _Snapshot(tuple(sorted(files, key=lambda f: f.relative)), tuple(sorted(directories)))


def _small(root: Path, value: _File, maximum: int) -> bytes:
    if value.bytes > maximum:
        raise CarrierRecoveryError("carrier_limit", value.relative)
    result = io.BytesIO()
    _, digest = _stream(root / value.relative, value.identity, result)
    if digest != value.sha256:
        raise CarrierRecoveryError("source_changed", value.relative)
    return result.getvalue()


def _copy_file(source: Path, destination: Path, value: _File) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        _, digest = _stream(source, value.identity, handle)
        handle.flush()
        os.fsync(handle.fileno())
    if digest != value.sha256:
        raise CarrierRecoveryError("source_changed", value.relative)


def _write_receipt(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".recovery-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise CarrierRecoveryError("receipt_collision")
    finally:
        temporary.unlink(missing_ok=True)


def _destination_layout(root: Path, content_id: str) -> None:
    for path in (root, root / "objects", root / "objects" / content_id,
                 root / "quarantine", root / "recovery-receipts"):
        try:
            value = path.lstat()
        except FileNotFoundError:
            continue
        if _is_link(value) or not stat.S_ISDIR(value.st_mode):
            raise CarrierRecoveryError("destination_link_or_not_directory", path.name)


def recover_human_bundle_carrier(
    source_directory: str | Path,
    destination_store: str | Path,
    *,
    expected: CollectionProfile | dict[str, object] | None = None,
) -> CarrierRecoveryResult:
    """Explicitly recover a directory carrier without editing or reattesting evidence.

    A typed promotion can already exist if receipt publication fails. Preserve it
    and reconcile the same explicit operation; never rewrite the original source.
    """
    source_input, destination_input = Path(source_directory), Path(destination_store)
    if source_input.is_symlink() or destination_input.is_symlink():
        raise CarrierRecoveryError("source_or_destination_link")
    source, destination = source_input.resolve(), destination_input.resolve()
    if destination == source or destination.is_relative_to(source):
        raise CarrierRecoveryError("destination_inside_source")
    original = _snapshot(source)
    files = {item.relative: item for item in original.files}
    if not {"checksums.sha256", "session-bundle-manifest.json"} <= files.keys():
        raise CarrierRecoveryError("bundle_inventory_missing")
    manifest_bytes = _small(source, files["session-bundle-manifest.json"], MAX_CHECKSUM_BYTES)
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict):
        raise CarrierRecoveryError("bundle_manifest_invalid")
    declared_id = manifest.get("bundle_content_id")
    if (not isinstance(declared_id, str) or len(declared_id) != 64
            or any(c not in "0123456789abcdef" for c in declared_id)):
        raise CarrierRecoveryError("bundle_identity_missing")
    original_transfer = DirectoryTransferManifest(
        declared_id, "human-session-bundle", tuple(item.transfer_file() for item in original.files))
    original_transfer.validate()
    checksum_bytes = _small(source, files["checksums.sha256"], MAX_CHECKSUM_BYTES)
    _destination_layout(destination, declared_id)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".carrier-recovery-", dir=destination) as name:
        staging = Path(name)
        (staging / "checksums.sha256").write_bytes(checksum_bytes)
        checksums = _read_checksums(staging / "checksums.sha256")
        if "checksums.sha256" in checksums or "session-bundle-manifest.json" not in checksums:
            raise CarrierRecoveryError("checksum_inventory_invalid")
        included = set(checksums) | {"checksums.sha256"}
        aliases: set[str] = set()
        for relative in sorted(included):
            _relative(relative)
            alias = _alias(relative)
            if alias in aliases:
                raise CarrierRecoveryError("duplicate_path_alias", relative)
            aliases.add(alias)
        for relative in sorted(included):
            item = files.get(relative)
            if item is None:
                raise CarrierRecoveryError("listed_file_missing", relative)
            if relative in checksums and checksums[relative] != item.sha256:
                raise CarrierRecoveryError("listed_file_checksum_mismatch", relative)
        excluded = tuple(item for item in original.files if item.relative not in included)
        if any(PurePosixPath(item.relative).name not in PERMITTED_METADATA_NAMES for item in excluded):
            raise CarrierRecoveryError("unknown_extra_file")
        for relative in sorted(included - {"checksums.sha256"}):
            _copy_file(source / relative, staging / relative, files[relative])
        if _snapshot(source) != original:
            raise CarrierRecoveryError("source_changed")
        verified = verify_human_session_bundle(staging, expected)
        if not verified.passed:
            raise CarrierRecoveryError("bundle_verification_failed",
                                       ",".join(finding.code for finding in verified.findings))
        bundle = verified.require_value()
        if bundle.bundle_content_id != declared_id:
            raise CarrierRecoveryError("bundle_identity_changed")
        recovered_transfer = DirectoryTransferManifest.from_directory(
            staging, content_id=bundle.bundle_content_id, artifact_type="human-session-bundle")

        def promote(directory: Path, transfer: DirectoryTransferManifest) -> None:
            result = verify_human_session_bundle(directory, expected)
            if not result.passed or result.require_value().bundle_content_id != transfer.content_id:
                raise CarrierRecoveryError("bundle_verification_failed",
                                           ",".join(finding.code for finding in result.findings))

        _destination_layout(destination, declared_id)
        receiver = DirectoryReceiver(ContentAddressedStore(destination), promotion_verifier=promote)
        transfer_result = receiver.receive(staging, recovered_transfer)
        if transfer_result.status not in {"promoted", "reused"}:
            raise CarrierRecoveryError("carrier_promotion_failed", transfer_result.status)
        receipt = {
            "schema": RECEIPT_SCHEMA, "source_form": "directory", "metadata_policy": METADATA_POLICY,
            "bundle_schema": manifest["schema"], "bundle_content_id": bundle.bundle_content_id,
            "original_carrier": original_transfer.to_dict(),
            "original_carrier_manifest_sha256": original_transfer.manifest_sha256,
            "original_bundle_manifest_sha256": files["session-bundle-manifest.json"].sha256,
            "original_checksums_sha256": files["checksums.sha256"].sha256,
            "excluded_metadata": [item.transfer_file().to_dict() for item in excluded],
            "recovered_carrier": recovered_transfer.to_dict(),
            "recovered_carrier_manifest_sha256": recovered_transfer.manifest_sha256,
            "claims": {"listed_bytes_preserved": True, "new_human_attestation": False,
                       "machine_proved_human_origin": False, "history_added": False,
                       "research_admission": False, "use_or_gold_changed": False},
        }
        receipt_bytes = json.dumps(receipt, ensure_ascii=False, allow_nan=False, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8")
        receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
        receipt_path = destination / "recovery-receipts" / (receipt_sha256 + ".json")
        try:
            _destination_layout(destination, declared_id)
            _write_receipt(receipt_path, receipt_bytes)
        except (OSError, ValueError) as error:
            raise CarrierRecoveryError("receipt_publication_failed") from error
        return CarrierRecoveryResult(transfer_result, receipt_path, receipt_sha256, receipt_bytes)
