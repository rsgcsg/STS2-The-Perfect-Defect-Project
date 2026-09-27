"""Read-only validation of an immutable, locally attested Human bundle source.

This is local evidence, not a Hub receipt or research admission. The installed
Evidence owner verifies the extracted bundle; callers may inspect its verified
directory only while the context is open.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sts2_platform_evidence import (
    DirectoryTransferManifest,
    HumanSessionBundleV3,
    verify_human_session_bundle,
)

from spireagent.artifact_contracts import Manifest
from spireagent.hub.uploads import MAX_ARCHIVE, transfer_from_json, unpack
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.store import ArtifactStore

EVIDENCE_SCHEMA = "stpd/local-verified-bundle-v1"
MAX_TRANSFER = 16 * 1024 * 1024


@dataclass(frozen=True)
class VerifiedLocalBundle:
    directory: Path
    bundle: object
    transfer: DirectoryTransferManifest
    archive_sha256: str
    archive_size: int

    def assert_directory_identity(self) -> None:
        """Keep a projection bound to the bytes verified in this private context."""
        if (not isinstance(self.bundle, HumanSessionBundleV3)
                or self.bundle.bundle_content_id != self.transfer.content_id
                or DirectoryTransferManifest.from_directory(
                    self.directory, content_id=self.transfer.content_id,
                    artifact_type="human-session-bundle",
                ) != self.transfer):
            raise BoundaryError("local_preview", "bundle_identity_mismatch")


@contextmanager
def verified_local_bundle(
    store: ArtifactStore, manifest: Manifest,
) -> Iterator[VerifiedLocalBundle]:
    """Recheck the selected store, exact transfer, raw hashes and typed bundle."""
    if store.get_manifest(manifest.artifact_id) != manifest:
        raise BoundaryError("local_preview", "not_local_verified_bundle")
    parameters = manifest.parameters.value()
    if (manifest.kind != "evidence" or manifest.parents
            or parameters.get("schema") != EVIDENCE_SCHEMA
            or parameters.get("disposition") != "locally_verified"
            or parameters.get("research_admission") != "not_evaluated"
            or parameters.get("human_origin_attested") is not True
            or parameters.get("hub_receipt") is not None
            or {item.role for item in manifest.payloads} != {"archive", "transfer"}
            or len(manifest.payloads) != 2):
        raise BoundaryError("local_preview", "not_local_verified_bundle")
    payloads = {item.role: item for item in manifest.payloads}
    if (payloads["archive"].size > MAX_ARCHIVE or payloads["archive"].size == 0
            or payloads["transfer"].size > MAX_TRANSFER or payloads["transfer"].size == 0
            or payloads["archive"].media_type != "application/gzip"
            or payloads["transfer"].media_type != "application/json"):
        raise BoundaryError("local_preview", "payload_size_or_type_invalid")
    raw_transfer = b"".join(store.read_payload(payloads["transfer"]))
    try:
        transfer = transfer_from_json(json.loads(raw_transfer))
    except (ValueError, TypeError, KeyError) as error:
        raise BoundaryError("local_preview", "transfer_invalid") from error
    if (raw_transfer != json_bytes(transfer.to_dict())
            or parameters.get("transfer_manifest_sha256") != transfer.manifest_sha256
            or parameters.get("content_id") != transfer.content_id):
        raise BoundaryError("local_preview", "transfer_identity_mismatch")
    with tempfile.TemporaryDirectory(prefix="stpd-local-verified-") as name:
        temporary = Path(name)
        archive = temporary / "bundle.tar.gz"
        archive_digest = hashlib.sha256()
        archive_size = 0
        with archive.open("xb") as target:
            for chunk in store.read_payload(payloads["archive"]):
                archive_size += len(chunk)
                if archive_size > payloads["archive"].size:
                    raise BoundaryError("local_preview", "archive_identity_mismatch")
                archive_digest.update(chunk)
                target.write(chunk)
        archive_sha256 = archive_digest.hexdigest()
        if (archive_size != payloads["archive"].size
                or archive_sha256 != payloads["archive"].sha256):
            raise BoundaryError("local_preview", "archive_identity_mismatch")
        extracted = temporary / "extracted"
        extracted.mkdir()
        unpack(archive, extracted, transfer)
        verification = verify_human_session_bundle(extracted)
        if not verification.passed:
            raise BoundaryError("local_preview", "typed_bundle_verification_failed")
        bundle = verification.require_value()
        for field, relative in (("manifest_sha256", "raw/recording-manifest.json"),
                                ("close_sha256", "raw/session-close-receipt.json")):
            path = extracted / relative
            if (not path.is_file() or path.stat().st_size > MAX_TRANSFER
                    or parameters.get(field) != hashlib.sha256(path.read_bytes()).hexdigest()):
                raise BoundaryError("local_preview", "recording_source_hash_mismatch")
        if (bundle.bundle_content_id != transfer.content_id
                or bundle.session_id != parameters.get("session_id")
                or getattr(bundle, "timeline_id", None) != parameters.get("timeline_id")
                or bundle.worker_id != parameters.get("worker_id")
                or bundle.campaign_id != parameters.get("campaign_id")
                or DirectoryTransferManifest.from_directory(
                    extracted, content_id=transfer.content_id,
                    artifact_type="human-session-bundle",
                ) != transfer):
            raise BoundaryError("local_preview", "bundle_identity_mismatch")
        yield VerifiedLocalBundle(extracted, bundle, transfer, archive_sha256, archive_size)
