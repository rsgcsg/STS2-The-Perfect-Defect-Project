"""P1 application artifacts over the existing ManifestArtifactStore.

The fixed collection release verifies/interprets originals. This module publishes
their existing schemas, lineage and diagnostics; it grants no research admission.
"""

from __future__ import annotations

import gzip
import hashlib
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sts2_platform_evidence import DirectoryTransferManifest
from sts2_platform_evidence.collection_tool import (
    P1_INPUT_PROJECTION,
    P1_NATIVE_SUPPORT,
    P1_SELECTOR_PROJECTION,
    CollectionTool,
)

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.archives import _extract_tar
from spireagent.storage.store import ManifestArtifactStore

BUNDLE_SCHEMA = P1_NATIVE_SUPPORT["bundle_schema"]
SEGMENTS_SCHEMA = P1_NATIVE_SUPPORT["segment_schema"]
PROJECTIONS = frozenset({P1_INPUT_PROJECTION, P1_SELECTOR_PROJECTION})


def _matching(store: ManifestArtifactStore, schema: str, **fields: Any) -> Manifest | None:
    matches = [m for identity in store.manifest_ids()
               if (m := store.get_manifest(identity)).parameters.value().get("schema") == schema
               and all(m.parameters.value().get(key) == value for key, value in fields.items())]
    if len(matches) > 1:
        raise BoundaryError("p1_store", "ambiguous_artifact_identity")
    return matches[0] if matches else None


def _transfer(store: ManifestArtifactStore, source: Manifest) -> DirectoryTransferManifest:
    payload = source.payload("transfer")
    if payload.size > 16 * 1024**2:
        raise BoundaryError("p1_store", "transfer_size_limit")
    raw = b"".join(store.read_payload(payload))
    with tempfile.TemporaryDirectory(prefix="p1-transfer-") as name:
        path = Path(name) / "transfer.json"
        path.write_bytes(raw)
        value = DirectoryTransferManifest.read(path)
    if (value.artifact_type != P1_NATIVE_SUPPORT["type_id"]
            or value.content_id != source.parameters.value().get("content_id")
            or len(value.files) > 50000 or sum(f.bytes for f in value.files) > 2 * 1024**3):
        raise BoundaryError("p1_store", "transfer_identity_or_size_mismatch")
    return value


def _verify_archive(archive: Path, transfer: DirectoryTransferManifest) -> None:
    expected = {f.path: f for f in transfer.files}
    seen: set[str] = set()
    with tarfile.open(archive, mode="r|gz") as stream:
        for member in stream:
            row = expected.get(member.name)
            if (row is None or member.name in seen or not member.isfile()
                    or member.size != row.bytes):
                raise BoundaryError("p1_store", "archive_inventory_mismatch")
            handle = stream.extractfile(member)
            if handle is None:
                raise BoundaryError("p1_store", "archive_member_missing")
            with handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != row.sha256:
                    raise BoundaryError("p1_store", "archive_member_hash_mismatch")
            seen.add(member.name)
    if seen != expected.keys():
        raise BoundaryError("p1_store", "archive_members_missing")


def publish_p1_original(
    store: ManifestArtifactStore, bundle: Path, tool: CollectionTool, producer: Producer, *,
    scratch: Path, preserved_manifest: Manifest | None = None,
    preserved_archive: Path | None = None, preserved_index: Path | None = None,
) -> Manifest:
    """Verify once, stream one archive, and retain the original producer separately."""
    verified = tool.verify_p1_native(bundle)
    identity = verified["manifest"]["content_identity"]
    content_id = verified["manifest"]["bundle_content_id"]
    existing = _matching(store, BUNDLE_SCHEMA, content_id=content_id)
    if existing is not None:
        for payload in existing.payloads:
            for _ in store.read_payload(payload):
                pass
        return existing
    transfer = DirectoryTransferManifest.from_directory(
        bundle, content_id=content_id, artifact_type=P1_NATIVE_SUPPORT["type_id"])
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p1-import-", dir=scratch) as name:
        archive = preserved_archive
        if archive is None:
            # Same deterministic streaming owner as ordinary recording imports.
            from .local_recording_import import _archive_verified_bundle

            archive = Path(name) / "bundle.tar.gz"
            _archive_verified_bundle(bundle, archive, transfer)
        _verify_archive(archive, transfer)
        with archive.open("rb") as handle:
            payload = store.put_payload("archive", handle, "application/gzip")
        index = store.put_bytes("transfer", json_bytes(transfer.to_dict()))
        parents: tuple[Parent, ...] = ()
        if preserved_manifest is not None:
            # Adopt A's verified byte artifact without changing it or copying raw trees.
            if (preserved_manifest.payload("archive") != payload
                    or preserved_manifest.parameters.value().get(
                        "bundle_content_id") != content_id):
                raise BoundaryError("p1_store", "preservation_identity_mismatch")
            if preserved_index is None:
                raise BoundaryError("p1_store", "preservation_inventory_required")
            if DirectoryTransferManifest.read(preserved_index) != transfer:
                raise BoundaryError("p1_store", "preservation_inventory_mismatch")
            source_index = store.put_bytes("source-index", preserved_index.read_bytes())
            if source_index != preserved_manifest.payload("source-index"):
                raise BoundaryError("p1_store", "preservation_inventory_mismatch")
            store.publish(preserved_manifest)
            parents = (Parent("preservation", preserved_manifest.artifact_id),)
        manifest = Manifest("evidence", producer, parents=parents,
                            payloads=(payload, index), parameters=FrozenObject.of({
            "schema": BUNDLE_SCHEMA, "content_id": content_id,
            "session_id": identity["session_id"], "timeline_id": identity["timeline_id"],
            "source_profile": identity["capture_profile_id"],
            "declared_origin": identity["declared_origin"],
            "human_origin_attestation": identity["human_origin_attestation"],
            "human_origin_verified": False, "research_admission": "not_evaluated",
            "original_recording": verified["recording"], "close": verified["close"],
            "raw_file_sha256": identity["raw_file_sha256"],
            "run_journal": verified["journal"], "tool_release_id": tool.release_id,
            "purpose": "verified_original_preservation",
        }))
        store.publish(manifest)
        return manifest


@contextmanager
def materialize_p1_original(
    store: ManifestArtifactStore, source_id: str, *, scratch: Path,
    tool: CollectionTool | None = None,
) -> Iterator[Path]:
    """Expand one verified working set; remove only this call's regenerable files."""
    source = store.get_manifest(source_id)
    if source.kind != "evidence" or source.parameters.value().get("schema") != BUNDLE_SCHEMA:
        raise BoundaryError("p1_store", "p1_original_required")
    transfer = _transfer(store, source)
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p1-materialize-", dir=scratch) as name:
        root = Path(name)
        archive = store.materialize(source.payload("archive"), root / "original.tar.gz")
        bundle = root / "bundle"
        bundle.mkdir()
        with gzip.open(archive, "rb") as stream:
            _extract_tar(stream, bundle, max_bytes=2 * 1024**3, max_files=50000)
        # Extraction reuses the existing path/type/size checks; exact inventory is separate.
        if DirectoryTransferManifest.from_directory(
                bundle, content_id=transfer.content_id,
                artifact_type=transfer.artifact_type) != transfer:
            raise BoundaryError("p1_store", "materialized_original_mismatch")
        if tool is not None:
            tool.verify_p1_native(bundle)
        yield bundle


def derive_p1_recording(
    store: ManifestArtifactStore, source_id: str, tool: CollectionTool, producer: Producer, *,
    scratch: Path, run_id: str = "run-0001", projection_id: str = P1_SELECTOR_PROJECTION,
) -> Manifest:
    """The named derivation is deterministic for source, run, tool and projection."""
    if projection_id not in PROJECTIONS:
        raise BoundaryError("p1_store", "unsupported_projection")
    source = store.get_manifest(source_id)
    if source.parameters.value().get("schema") != BUNDLE_SCHEMA or source.kind != "evidence":
        raise BoundaryError("p1_store", "p1_original_required")
    existing = _matching(store, SEGMENTS_SCHEMA, source_artifact_id=source_id,
                         tool_release_id=tool.release_id,
                         projection_id=projection_id, run_id=run_id)
    if existing is not None:
        tool._p1()
        for payload in existing.payloads:
            for _ in store.read_payload(payload):
                pass
        return existing
    with materialize_p1_original(store, source_id, scratch=scratch) as bundle:
        output = bundle.parent / "segments.json"
        summary = tool.derive_p1_native(bundle, output, run_id=run_id,
                                        projection_id=projection_id)
        if summary["source_content_id"] != source.parameters.value()["content_id"]:
            raise BoundaryError("p1_store", "projection_source_mismatch")
        with output.open("rb") as handle:
            segments = store.put_payload("segments", handle, "application/json")
        report = store.put_bytes("conversion_summary", json_bytes(summary))
        identity = tool.manifest["identity"]
        core_files = [f for f in identity["files"]
                      if f["path"].startswith("p1/components/connector/sdk/typescript/dist/")]
        manifest = Manifest("analysis", producer, parents=(Parent("source", source_id),),
                            payloads=(segments, report), parameters=FrozenObject.of({
            "schema": SEGMENTS_SCHEMA, "source_artifact_id": source_id,
            "source_content_id": summary["source_content_id"], "run_id": run_id,
            "branch": summary["branch"], "projection_id": projection_id,
            "tool_release_id": tool.release_id, "tool_source_revision": identity["source_revision"],
            "core_inventory_sha256": hashlib.sha256(json_bytes(core_files)).hexdigest(),
            "profile": summary["profile"], "revision": summary["revision"],
            "purpose": "engineering_projection", "research_admission": "not_evaluated",
            "summary": {key: summary[key] for key in (
                "segments", "observations", "candidate_N", "next_edges", "end_masks",
                "whole_run_complete", "source_conversion_complete")},
            "projection_claims": summary["projection_claims"],
        }))
        store.publish(manifest)
        return manifest


def p1_projection(store: ManifestArtifactStore, artifact_id: str) -> Manifest:
    manifest = store.get_manifest(artifact_id)
    info = manifest.parameters.value()
    if (manifest.kind != "analysis" or info.get("schema") != SEGMENTS_SCHEMA
            or info.get("projection_id") not in PROJECTIONS
            or manifest.parent("source") != info.get("source_artifact_id")):
        raise BoundaryError("p1_store", "unsupported_p1_projection")
    source = store.get_manifest(manifest.parent("source"))
    if (source.kind != "evidence" or source.parameters.value().get("schema") != BUNDLE_SCHEMA
            or source.parameters.value().get("content_id") != info.get("source_content_id")):
        raise BoundaryError("p1_store", "projection_source_mismatch")
    return manifest


def query_p1_recordings(
    store: ManifestArtifactStore, *, projection_id: str | None = None,
) -> list[dict[str, Any]]:
    """A small rebuildable query over durable manifests; no second recording database."""
    results = []
    for identity in store.manifest_ids():
        manifest = store.get_manifest(identity)
        info = manifest.parameters.value()
        if info.get("schema") != SEGMENTS_SCHEMA:
            continue
        if projection_id is not None and info.get("projection_id") != projection_id:
            continue
        checked = p1_projection(store, identity)
        results.append({"artifact_id": identity, "source_artifact_id": checked.parent("source"),
                        **info, "training_admitted": False})
    return results
