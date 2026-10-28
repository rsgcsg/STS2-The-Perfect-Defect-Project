"""P1 application artifacts over the existing ManifestArtifactStore.

The fixed collection release verifies/interprets originals. This module publishes
their existing schemas, lineage and diagnostics; it grants no research admission.
"""

from __future__ import annotations

import gzip
import hashlib
import json
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
    P1_TARGET_PROJECTION,
    P1_TARGET_SCHEMA,
    P1_TARGET_SUPPORT,
    CollectionTool,
)

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.archives import _extract_tar
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import CHUNK_BYTES, ManifestArtifactStore

CATALOG_SCHEMA = "stpd/p1-recording-catalog-v1"
BUNDLE_SCHEMA = P1_NATIVE_SUPPORT["bundle_schema"]
SEGMENTS_SCHEMA = P1_NATIVE_SUPPORT["segment_schema"]
PROJECTION_FORMATS = {
    P1_INPUT_PROJECTION: (SEGMENTS_SCHEMA, "segments"),
    P1_SELECTOR_PROJECTION: (SEGMENTS_SCHEMA, "segments"),
    P1_TARGET_PROJECTION: (P1_TARGET_SCHEMA, "targets"),
}
PROJECTIONS = frozenset(PROJECTION_FORMATS)


def _matching(store: ManifestArtifactStore, schema: str, **fields: Any) -> Manifest | None:
    matches = [
        m
        for identity in store.manifest_ids()
        if (m := store.get_manifest(identity)).parameters.value().get("schema") == schema
        and all(m.parameters.value().get(key) == value for key, value in fields.items())
    ]
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
    if (
        value.artifact_type != P1_NATIVE_SUPPORT["type_id"]
        or value.content_id != source.parameters.value().get("content_id")
        or len(value.files) > 50000
        or sum(f.bytes for f in value.files) > 2 * 1024**3
    ):
        raise BoundaryError("p1_store", "transfer_identity_or_size_mismatch")
    return value


def _verify_archive(archive: Path, transfer: DirectoryTransferManifest) -> None:
    expected = {f.path: f for f in transfer.files}
    seen: set[str] = set()
    with tarfile.open(archive, mode="r|gz") as stream:
        for member in stream:
            row = expected.get(member.name)
            if (
                row is None
                or member.name in seen
                or not member.isfile()
                or member.size != row.bytes
            ):
                raise BoundaryError("p1_store", "archive_inventory_mismatch")
            handle = stream.extractfile(member)
            if handle is None:
                raise BoundaryError("p1_store", "archive_member_missing")
            digest = hashlib.sha256()
            with handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != row.sha256:
                raise BoundaryError("p1_store", "archive_member_hash_mismatch")
            seen.add(member.name)
    if seen != expected.keys():
        raise BoundaryError("p1_store", "archive_members_missing")


def publish_p1_original(
    store: ManifestArtifactStore,
    bundle: Path,
    tool: CollectionTool,
    producer: Producer,
    *,
    scratch: Path,
    preserved_manifest: Manifest | None = None,
    preserved_archive: Path | None = None,
    preserved_index: Path | None = None,
) -> Manifest:
    """Verify once, stream one archive, and retain the original producer separately."""
    verified = tool.verify_p1_native(bundle)
    identity = verified["manifest"]["content_identity"]
    if verified["recording"].get("mixed_run_id") is not None:
        raise BoundaryError("p1_store", "mixed_run_requires_isolated_intake")
    content_id = verified["manifest"]["bundle_content_id"]
    existing = _matching(store, BUNDLE_SCHEMA, content_id=content_id)
    if existing is not None:
        for payload in existing.payloads:
            for _ in store.read_payload(payload):
                pass
        return existing
    transfer = DirectoryTransferManifest.from_directory(
        bundle, content_id=content_id, artifact_type=P1_NATIVE_SUPPORT["type_id"]
    )
    from .p1_recording_storage import require_workspace_space

    archive_estimate = (
        preserved_archive.stat().st_size
        if preserved_archive is not None
        else sum(f.bytes + 2048 for f in transfer.files) + 8192
    )
    metadata_estimate = len(json_bytes(transfer.to_dict()))
    if preserved_index is not None:
        metadata_estimate += preserved_index.stat().st_size
    require_workspace_space(
        scratch,
        CHUNK_BYTES + (archive_estimate if preserved_archive is None else 0),
        persistent_bytes=archive_estimate + metadata_estimate,
        destination=store.blobs.root if isinstance(store.blobs, LocalBlobStore) else None,
    )
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
            if (
                preserved_manifest.payload("archive") != payload
                or preserved_manifest.parameters.value().get("bundle_content_id") != content_id
            ):
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
        manifest = Manifest(
            "evidence",
            producer,
            parents=parents,
            payloads=(payload, index),
            parameters=FrozenObject.of(
                {
                    "schema": BUNDLE_SCHEMA,
                    "content_id": content_id,
                    "session_id": identity["session_id"],
                    "timeline_id": identity["timeline_id"],
                    "source_profile": identity["capture_profile_id"],
                    "declared_origin": identity["declared_origin"],
                    "human_origin_attestation": identity["human_origin_attestation"],
                    "human_origin_verified": False,
                    "research_admission": "not_evaluated",
                    "original_recording": verified["recording"],
                    "close": verified["close"],
                    "raw_file_sha256": identity["raw_file_sha256"],
                    "bundle_manifest_sha256": next(
                        f.sha256 for f in transfer.files if f.path == "session-bundle-manifest.json"
                    ),
                    "run_journal": verified["journal"],
                    "tool_release_id": tool.release_id,
                    "purpose": "verified_original_preservation",
                    "metadata": {
                        "created_at": verified["recording"].get("created_at"),
                        "closed_at": verified.get("session_close", {}).get("closed_at"),
                        "source_bytes": sum(f.bytes for f in transfer.files),
                        "source_files": len(transfer.files),
                        "character": None,
                        "ascension": None,
                        "mode": None,
                        "language": None,
                        "result": None,
                        "game_version": None,
                    },
                }
            ),
        )
        store.publish(manifest)
        return manifest


@contextmanager
def materialize_p1_original(
    store: ManifestArtifactStore,
    source_id: str,
    *,
    scratch: Path,
    tool: CollectionTool | None = None,
    additional_scratch_bytes: int = 0,
) -> Iterator[Path]:
    """Expand one verified working set; remove only this call's regenerable files."""
    source = store.get_manifest(source_id)
    if source.kind != "evidence" or source.parameters.value().get("schema") != BUNDLE_SCHEMA:
        raise BoundaryError("p1_store", "p1_original_required")
    transfer = _transfer(store, source)
    from .p1_recording_storage import require_workspace_space

    require_workspace_space(
        scratch,
        source.payload("archive").size
        + sum(f.bytes for f in transfer.files)
        + additional_scratch_bytes,
    )
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p1-materialize-", dir=scratch) as name:
        root = Path(name)
        archive = store.materialize(source.payload("archive"), root / "original.tar.gz")
        bundle = root / "bundle"
        bundle.mkdir()
        with gzip.open(archive, "rb") as stream:
            _extract_tar(stream, bundle, max_bytes=2 * 1024**3, max_files=50000)
        # Extraction reuses the existing path/type/size checks; exact inventory is separate.
        if (
            DirectoryTransferManifest.from_directory(
                bundle, content_id=transfer.content_id, artifact_type=transfer.artifact_type
            )
            != transfer
        ):
            raise BoundaryError("p1_store", "materialized_original_mismatch")
        if tool is not None:
            tool.verify_p1_native(bundle)
        yield bundle


def projection_format(projection_id: str) -> tuple[str, str]:
    try:
        return PROJECTION_FORMATS[projection_id]
    except KeyError:
        raise BoundaryError("p1_store", "unsupported_projection") from None


def inspect_p1_projection(
    path: Path, source: Manifest, projection_id: str, run_id: str, expected_sha256: str
) -> dict[str, Any]:
    """Check transport identity and expose counts without reinterpreting legality."""
    schema, _ = projection_format(projection_id)
    with path.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != expected_sha256:
        raise BoundaryError("p1_store", "projection_payload_hash_mismatch")
    data = json.loads(path.read_bytes())
    original = source.parameters.value()
    if (
        not isinstance(data, dict)
        or data.get("schema") != schema
        or data.get("source_content_id") != original["content_id"]
        or not isinstance(data.get("branch"), dict)
    ):
        raise BoundaryError("p1_store", "projection_source_mismatch")
    branch = data["branch"]
    if (
        branch.get("run_id", run_id) != run_id
        or branch.get("session_id", original["session_id"]) != original["session_id"]
        or branch.get("timeline_id", original["timeline_id"]) != original["timeline_id"]
    ):
        raise BoundaryError("p1_store", "projection_branch_mismatch")
    if projection_id == P1_TARGET_PROJECTION:
        if (
            data.get("projection_id") != projection_id
            or data.get("source_integrity_verified") is not True
        ):
            raise BoundaryError("p1_store", "projection_identity_mismatch")
        transfer = original.get("bundle_manifest_sha256")
        if transfer is not None and data.get("source_manifest_sha256") != transfer:
            raise BoundaryError("p1_store", "projection_source_manifest_mismatch")
        points = data["points"]
        extra = {
            "events": len(data["timeline"]["events"]),
            "native_spans": len(data["timeline"]["spans"]),
            "p1_action_chain_complete": data["p1_action_chain_complete"],
            "memory_continuity": data["memory_continuity"],
        }
    else:
        points = [p for segment in data["segments"] for p in segment["points"]]
        extra = {"segments": len(data["segments"])}
    return {
        "schema": schema,
        "source_content_id": data["source_content_id"],
        "branch": branch,
        "profile": data["profile"],
        "revision": data["revision"],
        "projection_claims": data["projection_claims"],
        "whole_run_complete": data.get("whole_run_complete"),
        "source_conversion_complete": data.get("source_conversion_complete"),
        "observations": len(points),
        "candidate_N": sum(p["mask"]["N"] is True for p in points),
        "next_edges": sum(p["mask"]["next"] is True for p in points),
        "end_masks": sum(p["mask"]["end"] is True for p in points),
        **extra,
    }


def publish_p1_projection(
    store: ManifestArtifactStore,
    source_id: str,
    tool: CollectionTool,
    producer: Producer,
    *,
    output: Path,
    expected_sha256: str,
    projection_id: str,
    run_id: str = "run-0001",
    derivation_receipt: dict[str, Any],
) -> Manifest:
    """Publish owner-produced bytes with their derivation receipt and original parent."""
    schema, role = projection_format(projection_id)
    source = store.get_manifest(source_id)
    if source.kind != "evidence" or source.parameters.value().get("schema") != BUNDLE_SCHEMA:
        raise BoundaryError("p1_store", "p1_original_required")
    identity = tool._p1()
    if (
        projection_id == P1_TARGET_PROJECTION
        and identity.get("p1_native_support") != P1_TARGET_SUPPORT
    ):
        raise BoundaryError("p1_store", "target_tool_support_required")
    if (
        derivation_receipt.get("source_content_id") != source.parameters.value()["content_id"]
        or derivation_receipt.get("tool_release_id") != tool.release_id
        or derivation_receipt.get("payload_sha256") != expected_sha256
        or derivation_receipt.get("full_readback") is not True
    ):
        raise BoundaryError("p1_store", "derivation_receipt_mismatch")
    summary = inspect_p1_projection(output, source, projection_id, run_id, expected_sha256)
    diagnostics = derivation_receipt.get("owner_diagnostics", {})
    for field, owner_field in (
        ("whole_run_complete", "whole_run_recording_complete"),
        ("source_conversion_complete", "source_conversion_complete"),
    ):
        fact = diagnostics.get(owner_field, diagnostics.get(field))
        if type(fact) is bool:
            summary[field] = fact

    existing = _matching(
        store,
        schema,
        source_artifact_id=source_id,
        tool_release_id=tool.release_id,
        projection_id=projection_id,
        run_id=run_id,
    )
    if existing is not None:
        if existing.payload(role).sha256 != expected_sha256:
            raise BoundaryError("p1_store", "named_projection_bytes_differ")
        for payload in existing.payloads:
            for _ in store.read_payload(payload):
                pass
        return existing
    from .p1_recording_storage import require_workspace_space

    require_workspace_space(
        output.parent,
        CHUNK_BYTES,
        persistent_bytes=output.stat().st_size
        + len(json_bytes(summary))
        + len(json_bytes(derivation_receipt)),
        destination=store.blobs.root if isinstance(store.blobs, LocalBlobStore) else None,
    )
    with output.open("rb") as handle:
        payload = store.put_payload(role, handle, "application/json")
    if payload.sha256 != expected_sha256:
        raise BoundaryError("p1_store", "projection_payload_changed")
    report = store.put_bytes("conversion_summary", json_bytes(summary))
    receipt = store.put_bytes("derivation_receipt", json_bytes(derivation_receipt))
    core_files = [
        f
        for f in identity["files"]
        if f["path"].startswith("p1/components/connector/sdk/typescript/dist/")
    ]
    manifest = Manifest(
        "analysis",
        producer,
        parents=(Parent("source", source_id),),
        payloads=(payload, report, receipt),
        parameters=FrozenObject.of(
            {
                "schema": schema,
                "source_artifact_id": source_id,
                "source_content_id": summary["source_content_id"],
                "run_id": run_id,
                "branch": summary["branch"],
                "projection_id": projection_id,
                "tool_release_id": tool.release_id,
                "tool_source_revision": identity["source_revision"],
                "core_inventory_sha256": hashlib.sha256(json_bytes(core_files)).hexdigest(),
                "profile": summary["profile"],
                "revision": summary["revision"],
                "purpose": "engineering_projection",
                "research_admission": "not_evaluated",
                "summary": {
                    key: value
                    for key, value in summary.items()
                    if key
                    not in {
                        "schema",
                        "source_content_id",
                        "branch",
                        "profile",
                        "revision",
                        "projection_claims",
                    }
                },
                "projection_claims": summary["projection_claims"],
                "source_bundle_path_semantics": (
                    "diagnostic_only_use_source_artifact_id_and_raw_member_refs"
                ),
            }
        ),
    )
    store.publish(manifest)
    return manifest


def derive_p1_recording(
    store: ManifestArtifactStore,
    source_id: str,
    tool: CollectionTool,
    producer: Producer,
    *,
    scratch: Path,
    run_id: str = "run-0001",
    projection_id: str = P1_SELECTOR_PROJECTION,
) -> Manifest:
    """Reuse a named projection; diagnostic source paths do not promise cross-host byte identity."""
    schema, _ = projection_format(projection_id)
    source = store.get_manifest(source_id)
    if source.parameters.value().get("schema") != BUNDLE_SCHEMA or source.kind != "evidence":
        raise BoundaryError("p1_store", "p1_original_required")
    existing = _matching(
        store,
        schema,
        source_artifact_id=source_id,
        tool_release_id=tool.release_id,
        projection_id=projection_id,
        run_id=run_id,
    )
    if existing is not None:
        tool._p1()
        for payload in existing.payloads:
            for _ in store.read_payload(payload):
                pass
        return existing
    expanded = sum(f.bytes for f in _transfer(store, source).files)
    with materialize_p1_original(
        store, source_id, scratch=scratch, additional_scratch_bytes=2 * expanded
    ) as bundle:
        output = bundle.parent / "projection.json"
        diagnostics = tool.derive_p1_native(
            bundle, output, run_id=run_id, projection_id=projection_id
        )
        with output.open("rb") as handle:
            payload_hash = hashlib.file_digest(handle, "sha256").hexdigest()
        return publish_p1_projection(
            store,
            source_id,
            tool,
            producer,
            output=output,
            expected_sha256=payload_hash,
            projection_id=projection_id,
            run_id=run_id,
            derivation_receipt={
                "source_content_id": source.parameters.value()["content_id"],
                "tool_release_id": tool.release_id,
                "payload_sha256": payload_hash,
                "full_readback": True,
                "owner_diagnostics": diagnostics,
            },
        )


def p1_projection(store: ManifestArtifactStore, artifact_id: str) -> Manifest:
    manifest = store.get_manifest(artifact_id)
    info = manifest.parameters.value()
    if (
        manifest.kind != "analysis"
        or info.get("projection_id") not in PROJECTIONS
        or info.get("schema") != projection_format(info["projection_id"])[0]
        or manifest.parent("source") != info.get("source_artifact_id")
    ):
        raise BoundaryError("p1_store", "unsupported_p1_projection")
    source = store.get_manifest(manifest.parent("source"))
    if (
        source.kind != "evidence"
        or source.parameters.value().get("schema") != BUNDLE_SCHEMA
        or source.parameters.value().get("content_id") != info.get("source_content_id")
    ):
        raise BoundaryError("p1_store", "projection_source_mismatch")
    return manifest


def query_p1_recordings(
    store: ManifestArtifactStore,
    *,
    projection_id: str | None = None,
) -> list[dict[str, Any]]:
    """A small rebuildable query over durable manifests; no second recording database."""
    results = []
    for identity in store.manifest_ids():
        manifest = store.get_manifest(identity)
        info = manifest.parameters.value()
        if info.get("schema") not in {SEGMENTS_SCHEMA, P1_TARGET_SCHEMA}:
            continue
        if projection_id is not None and info.get("projection_id") != projection_id:
            continue
        checked = p1_projection(store, identity)
        results.append(
            {
                "artifact_id": identity,
                "source_artifact_id": checked.parent("source"),
                **info,
                "training_admitted": False,
            }
        )
    return results


def publish_p1_catalog_metadata(
    store: ManifestArtifactStore, projection_id: str, producer: Producer
) -> Manifest:
    """Rebuild searchable facts from an exact named view; never rewrite its source or labels."""
    projection = p1_projection(store, projection_id)
    existing = _matching(store, CATALOG_SCHEMA, projection_artifact_id=projection_id)
    if existing is not None:
        return existing
    info = projection.parameters.value()
    role = projection_format(info["projection_id"])[1]
    payload = projection.payload(role)
    data = json.loads(b"".join(store.read_payload(payload)))
    points = (
        data["points"]
        if role == "targets"
        else [p for segment in data["segments"] for p in segment["points"]]
    )
    point = points[0] if points else {}
    observation = point.get("observation", {})
    player = observation.get("player", {})
    run = observation.get("run", {})

    def available_content(value: dict[str, Any]) -> dict[str, Any]:
        if value.get("availability") != "available":
            return {}
        content = value.get("content")
        if not isinstance(content, dict):
            raise BoundaryError("p1_catalog", "available_content_object_required")
        return content

    player_content = available_content(player)
    run_content = available_content(run)
    character = player_content.get("character_definition_id")
    ascension = run_content.get("ascension")
    terminals = (
        ([data["end_target"]] if data.get("end_target") is not None else [])
        if role == "targets"
        else [
            segment["end_target"]
            for segment in data["segments"]
            if segment.get("end_target") is not None
        ]
    )
    terminals = [t for t in terminals if t.get("branch", data["branch"]) == data["branch"]]
    results = {t["result"] for t in terminals if isinstance(t.get("result"), str)}
    result = next(iter(results)) if len(results) == 1 else None
    terminal = terminals[0] if terminals else {}
    metadata = {
        "character": character if isinstance(character, str) else None,
        "ascension": ascension if type(ascension) is int else None,
        "first_observed_act": run_content.get("act"),
        "first_observed_floor": run_content.get("floor"),
        "modifiers": run_content.get("modifiers"),
        "result": result if isinstance(result, str) else None,
        "mode": None,
        "language": None,
        "game_version": None,
        "seed": None,
        "root": None,
        "ancestry": None,
    }
    manifest = Manifest(
        "analysis",
        producer,
        parents=(
            Parent("source", projection.parent("source")),
            Parent("projection", projection_id),
        ),
        parameters=FrozenObject.of(
            {
                "schema": CATALOG_SCHEMA,
                "source_artifact_id": projection.parent("source"),
                "projection_artifact_id": projection_id,
                "run_id": info["run_id"],
                "metadata": metadata,
                "evidence": {
                    "projection_payload_sha256": payload.sha256,
                    "source_content_id": info["source_content_id"],
                    "first_source_event_id": point.get("source_event_id"),
                    "first_source_public_basis": point.get("source_public_basis"),
                    "end_source_event_id": terminal.get("source_event_id"),
                    "end_source_public_basis": terminal.get("source_public_basis"),
                    "end_targets": terminals,
                    "conflicting_results": sorted(results) if len(results) > 1 else [],
                },
                "unknown_scope": (
                    "mode_language_game_version_seed_root_ancestry_not_in_indexed_P1_view"
                ),
                "purpose": "rebuildable_recording_catalog",
                "research_admission": "not_evaluated",
            }
        ),
    )
    store.publish(manifest)
    return manifest


def query_p1_sources(
    store: ManifestArtifactStore,
    *,
    filters: dict[str, Any] | None = None,
    storage_receipts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Originals and named views remain separate; missing catalog facts stay unknown."""
    filters = filters or {}
    allowed = {
        "declared_origin",
        "source_profile",
        "character",
        "ascension",
        "mode",
        "language",
        "result",
        "game_version",
        "has_projection",
        "preservation_verified",
    }
    if not set(filters) <= allowed:
        raise BoundaryError("p1_store", "unsupported_query_filter")
    for field, value in filters.items():
        if field in {"has_projection", "preservation_verified"}:
            valid = type(value) is bool
        elif field == "ascension":
            valid = value is None or type(value) is int
        else:
            valid = value is None or isinstance(value, str)
        if not valid:
            raise BoundaryError("p1_store", "unsupported_query_filter_type")
    projections = query_p1_recordings(store)
    catalog = [
        m
        for identity in store.manifest_ids()
        if (m := store.get_manifest(identity)).parameters.value().get("schema") == CATALOG_SCHEMA
    ]
    results = []
    for identity in store.manifest_ids():
        source = store.get_manifest(identity)
        info = source.parameters.value()
        if info.get("schema") != BUNDLE_SCHEMA:
            continue
        metadata = dict(info.get("metadata", {}))
        indexed = [m for m in catalog if m.parent("source") == identity]
        conflicts = []
        for field in (
            "character",
            "ascension",
            "result",
            "mode",
            "language",
            "game_version",
            "first_observed_act",
            "first_observed_floor",
            "modifiers",
            "seed",
            "root",
            "ancestry",
        ):
            values = {
                json_bytes(m.parameters.value()["metadata"][field]): m.parameters.value()[
                    "metadata"
                ][field]
                for m in indexed
                if m.parameters.value()["metadata"].get(field) is not None
            }
            if len(values) > 1:
                metadata[field] = None
                conflicts.append(field)
            elif values:
                metadata[field] = next(iter(values.values()))
        views = [
            {
                "artifact_id": p["artifact_id"],
                "projection_id": p["projection_id"],
                "summary": p["summary"],
            }
            for p in projections
            if p["source_artifact_id"] == identity
        ]
        receipts = [
            row
            for row in storage_receipts or []
            if row["artifact_id"] == identity
            and row["storage_action"] == "preserve"
            and row["storage_receipt"].get("full_readback") is True
        ]
        row = {
            "artifact_id": identity,
            "content_id": info["content_id"],
            "session_id": info["session_id"],
            "timeline_id": info["timeline_id"],
            "declared_origin": info["declared_origin"],
            "source_profile": info["source_profile"],
            **{
                key: metadata.get(key)
                for key in (
                    "created_at",
                    "closed_at",
                    "source_bytes",
                    "source_files",
                    "character",
                    "ascension",
                    "mode",
                    "language",
                    "result",
                    "game_version",
                )
            },
            "recorder_source_revision": info["original_recording"]["recorder_source_revision"],
            "projections": views,
            "catalog_metadata_artifacts": [m.artifact_id for m in indexed],
            "metadata_conflicts": conflicts,
            "has_projection": bool(views),
            "preservation_verified": bool(receipts),
            "storage_receipts": receipts,
            "human_origin_verified": False,
            "research_admission": "not_evaluated",
        }
        if all(row[key] == value for key, value in filters.items()):
            results.append(row)
    return sorted(
        results, key=lambda row: (row["created_at"] or "", row["artifact_id"]), reverse=True
    )
