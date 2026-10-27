"""Preserve every available failed Runtime byte without claiming typed success."""
from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from sts2_platform_evidence import DirectoryTransferManifest

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.encoding import semantic_hash
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.store import CHUNK_BYTES, ManifestArtifactStore

from .global_evidence_store import MAX_BYTES, MAX_FILES
from .local_recording_import import _archive_verified_bundle
from .p1_recording_storage import require_workspace_space
from .p1_recording_store import _verify_archive

SCHEMA = "spireagent/managed-failed-original-1"


def publish_failure(store: ManifestArtifactStore, source: Path | None, producer: Producer,
                    facts: dict[str, Any], *, scratch: Path) -> Manifest:
    with tempfile.TemporaryDirectory(prefix="managed-failure-", dir=scratch) as tmp:
        root = Path(tmp)
        if source is None or not source.exists():
            source = root / "no-runtime-original"
            source.mkdir()
            (source / "failure.json").write_bytes(json_bytes(facts))
        # Only Runtime evidence, never private manager/registration credentials.
        transfer = DirectoryTransferManifest.from_directory(
            source, content_id="0" * 64, artifact_type=SCHEMA)
        transfer = replace(transfer, content_id=semantic_hash(transfer.to_dict()["files"]))
        bound = sum(f.bytes for f in transfer.files) + 4096 * len(transfer.files) + 1024**2
        require_workspace_space(scratch, bound + CHUNK_BYTES)
        archive = root / "original.tar.gz"
        _archive_verified_bundle(source, archive, transfer, max_bytes=MAX_BYTES,
                                 max_files=MAX_FILES, max_archive_bytes=bound)
        _verify_archive(archive, transfer)
        with archive.open("rb") as stream:
            payload = store.put_payload("archive", stream, "application/gzip")
        index = store.put_bytes("transfer", json_bytes(transfer.to_dict()))
    manifest = Manifest("analysis", producer,
        parents=(Parent("model", facts["model_artifact_id"]),), payloads=(payload, index),
        parameters=FrozenObject.of({"schema": SCHEMA, "facts": facts,
            "content_id": transfer.content_id, "typed_recording_qualified": False,
            "score_eligible": False, "research_admission": "not_evaluated"}))
    store.publish(manifest)
    return manifest


def verify_failure(store: ManifestArtifactStore, artifact_id: str, *, facts: dict[str, Any],
                   scratch: Path) -> dict[str, Any]:
    manifest = store.get_manifest(artifact_id)
    info = manifest.parameters.value()
    if (manifest.kind != "analysis" or info.get("schema") != SCHEMA
            or info.get("facts") != facts or info.get("typed_recording_qualified") is not False
            or info.get("score_eligible") is not False
            or manifest.parents != (Parent("model", facts["model_artifact_id"]),)
            or {p.role for p in manifest.payloads} != {"archive", "transfer"}
            or manifest.payload("transfer").size > 16 * 1024**2
            or manifest.payload("archive").size > MAX_BYTES + 256 * 1024**2):
        raise BoundaryError("managed_failure", "failed_original_identity")
    with tempfile.TemporaryDirectory(prefix="managed-failure-read-", dir=scratch) as tmp:
        root = Path(tmp)
        transfer = DirectoryTransferManifest.read(store.materialize(
            manifest.payload("transfer"), root / "transfer.json"))
        if (transfer.artifact_type != SCHEMA or transfer.content_id != info["content_id"]
                or transfer.content_id != semantic_hash(transfer.to_dict()["files"])
                or len(transfer.files) > MAX_FILES
                or sum(f.bytes for f in transfer.files) > MAX_BYTES):
            raise BoundaryError("managed_failure", "failed_original_inventory")
        archive = store.materialize(manifest.payload("archive"), root / "original.tar.gz")
        _verify_archive(archive, transfer)
    return {"artifact_id": artifact_id, "full_readback": True,
            "typed_recording_qualified": False, "scope": "all_available_failure_originals"}
