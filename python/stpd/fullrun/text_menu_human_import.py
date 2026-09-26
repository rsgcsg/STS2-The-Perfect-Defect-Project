"""Owner-attested Human text-input observations for engineering BC.

This source is a verified recording archive, not a transition dataset. A native
begin witness labels only the selected current menu action; it says nothing
about Connector delivery, Receipt, effects, or a successor state.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sts2_platform_evidence.human_session_bundle_v3 import HumanSessionBundleV3Verifier
from sts2_platform_evidence.human_text_inputs import verify_human_text_inputs

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from .features import ModelSample
from .platform_bundle3 import MAX_BYTES, _extract, archive_bundle
from .text_menu_inputs import IDENTITY, project_text_menu_snapshot

EVIDENCE_SCHEMA = "stpd/verified-human-text-input-bundle-v1"
SOURCE_SCHEMA = "stpd/human-text-input-source-v1"
VIEW_SCHEMA = "stpd/human-text-input-bc-view-v1"
ROW_SCHEMA = "sts2.human-annotator/human-text-input-1"
MAX_BUNDLES = 256
MAX_ROWS = 100000


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _verified(directory: Path) -> tuple[Any, tuple[dict, ...]]:
    result = HumanSessionBundleV3Verifier().verify(directory)
    if result.status != "pass" or result.value is None:
        raise BoundaryError("human_text_import", "bundle3_verification_failed")
    bundle = result.value
    if bundle.text_input_schema_version != 1:
        raise BoundaryError("human_text_import", "declared_human_text_stream_required")
    raw = directory / "raw"
    recording = decode_json((raw / "recording-manifest.json").read_bytes())
    receipt = decode_json((raw / "session-close-receipt.json").read_bytes())
    # Keep the side-schema verifier an explicit dependency: an older V3 verifier
    # which silently ignores this stream cannot admit Human input labels.
    checked = tuple(verify_human_text_inputs(raw, recording, receipt, bundle.run_ids))
    rows = tuple(_plain(row) for row in bundle.human_text_inputs)
    if rows != tuple(_plain(row) for row in checked):
        raise BoundaryError("human_text_import", "typed_stream_verifier_mismatch")
    if len(rows) > MAX_ROWS:
        raise BoundaryError("human_text_import", "human_text_row_limit")
    for row in rows:
        if row.get("schema") != ROW_SCHEMA or row.get("schema_version") != 1:
            raise BoundaryError("human_text_import", "human_text_schema_mismatch")
    return bundle, rows


def _verified_archive(packed: bytes) -> tuple[Any, tuple[dict, ...]]:
    if len(packed) > MAX_BYTES:
        raise BoundaryError("human_text_import", "archive_size_limit")
    with tempfile.TemporaryDirectory(prefix="stpd-human-text-") as name:
        directory = Path(name)
        _extract(packed, directory)
        return _verified(directory)


def publish_verified_human_text_bundle(
    store: ArtifactStore, directory: Path, producer: Producer,
) -> Manifest:
    # First verify the supplied directory, then verify the exact archived bytes.
    _verified(directory)
    packed = archive_bundle(directory)
    bundle, rows = _verified_archive(packed)
    payload = store.put_payload("archive", io.BytesIO(packed), "application/gzip")
    manifest = Manifest("evidence", producer, (), (payload,), FrozenObject.of({
        "schema": EVIDENCE_SCHEMA,
        "bundle_content_id": bundle.bundle_content_id,
        "session_id": bundle.session_id,
        "archive_sha256": hashlib.sha256(packed).hexdigest(),
        "row_count": len(rows),
        "stream_digest": semantic_hash(list(rows)),
        "verification": "bundle3_and_human_text_inputs_typed_pass",
        "origin": "owner_attested_human_not_machine_verifiable",
    }))
    store.publish(manifest)
    return manifest


def load_verified_human_text_bundle(
    store: ArtifactStore, identity: str,
) -> tuple[Manifest, Any, tuple[dict, ...]]:
    manifest = store.get_manifest(identity)
    if (manifest.kind != "evidence" or manifest.parents
            or [p.role for p in manifest.payloads] != ["archive"]):
        raise BoundaryError("human_text_import", "verified_archive_required")
    payload = manifest.payload("archive")
    if payload.size > MAX_BYTES:
        raise BoundaryError("human_text_import", "archive_size_limit")
    packed = b"".join(store.read_payload(payload))
    bundle, rows = _verified_archive(packed)
    expected = {
        "schema": EVIDENCE_SCHEMA,
        "bundle_content_id": bundle.bundle_content_id,
        "session_id": bundle.session_id,
        "archive_sha256": hashlib.sha256(packed).hexdigest(),
        "row_count": len(rows),
        "stream_digest": semantic_hash(list(rows)),
        "verification": "bundle3_and_human_text_inputs_typed_pass",
        "origin": "owner_attested_human_not_machine_verifiable",
    }
    if manifest.parameters.value() != expected:
        raise BoundaryError("human_text_import", "archive_identity_mismatch")
    return manifest, bundle, rows


def _parents(ids: tuple[str, ...]) -> tuple[Parent, ...]:
    return tuple(Parent(f"verified_human_bundle_{index:06d}", identity)
                 for index, identity in enumerate(ids))


def _parent_ids(parents: tuple[Parent, ...]) -> tuple[str, ...]:
    if not parents or tuple(p.role for p in parents) != tuple(
            f"verified_human_bundle_{index:06d}" for index in range(len(parents))):
        raise BoundaryError("human_text_import", "exact_bundle_parents_required")
    ids = tuple(p.artifact_id for p in parents)
    if len(set(ids)) != len(ids) or len(ids) > MAX_BUNDLES:
        raise BoundaryError("human_text_import", "duplicate_or_excessive_bundle")
    return ids


def publish_human_text_source(
    store: ArtifactStore, evidence_ids: tuple[str, ...], producer: Producer,
) -> Manifest:
    ids = _parent_ids(_parents(evidence_ids))
    grouped = [load_verified_human_text_bundle(store, identity) for identity in ids]
    sessions = [bundle.session_id for _, bundle, _ in grouped]
    if len(set(sessions)) != len(sessions):
        raise BoundaryError("human_text_import", "duplicate_session")
    rows = tuple(row for _, _, batch in grouped for row in batch)
    if len(rows) > MAX_ROWS:
        raise BoundaryError("human_text_import", "human_text_row_limit")
    manifest = Manifest("dataset", producer, _parents(ids), (), FrozenObject.of({
        "schema": SOURCE_SCHEMA, "scope": "engineering", "purpose": "bc_input_observation",
        "rows": len(rows), "source_digest": semantic_hash(list(rows)),
        "disposition_counts": dict(Counter(row["disposition"] for row in rows)),
        "sessions": sessions,
        "claim": "owner_attested_human_input_only_no_commit_or_successor",
    }))
    store.publish(manifest)
    return manifest


def load_human_text_source(
    store: ArtifactStore, identity: str,
) -> tuple[Manifest, tuple[dict, ...]]:
    manifest = store.get_manifest(identity)
    if manifest.kind != "dataset" or manifest.payloads:
        raise BoundaryError("human_text_import", "verified_source_required")
    ids = _parent_ids(manifest.parents)
    grouped = [load_verified_human_text_bundle(store, item) for item in ids]
    sessions = [bundle.session_id for _, bundle, _ in grouped]
    if len(set(sessions)) != len(sessions):
        raise BoundaryError("human_text_import", "duplicate_session")
    rows = tuple(row for _, _, batch in grouped for row in batch)
    if len(rows) > MAX_ROWS:
        raise BoundaryError("human_text_import", "human_text_row_limit")
    expected = {
        "schema": SOURCE_SCHEMA, "scope": "engineering", "purpose": "bc_input_observation",
        "rows": len(rows), "source_digest": semantic_hash(list(rows)),
        "disposition_counts": dict(Counter(row["disposition"] for row in rows)),
        "sessions": sessions,
        "claim": "owner_attested_human_input_only_no_commit_or_successor",
    }
    if manifest.parameters.value() != expected:
        raise BoundaryError("human_text_import", "source_identity_mismatch")
    return manifest, rows


def _project(rows: tuple[dict, ...]) -> tuple[tuple[ModelSample, ...], dict]:
    parent: dict[str, str] = {}
    inputs: dict[str, str] = {}
    accepted: list[tuple[dict, Any, str]] = []
    report_rows: list[dict] = []

    def root(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for row in rows:
        group = row["session_id"]
        parent.setdefault(group, group)
        snapshot = row.get("snapshot")
        if isinstance(snapshot, dict):
            try:
                visible = project_text_menu_snapshot(snapshot)
            except BoundaryError:
                if row["disposition"] == "accepted_input":
                    raise
            else:
                fingerprint = semantic_hash([visible.state_text, list(visible.action_texts)])
                previous = inputs.setdefault(fingerprint, group)
                left, right = root(previous), root(group)
                parent[max(left, right)] = min(left, right)
        if row["disposition"] != "accepted_input":
            continue
        chosen = row.get("chosen_action")
        if (row["mapping_status"] != "exact_unique" or row["match_count"] != 1
                or row["mapping_basis"] != "text_menu_native_reference_equality"
                or row["native_mechanism"] != "begin_card_play_exact_factory_return"
                or row["external_controller_active"] is not False
                or not isinstance(snapshot, dict) or not isinstance(chosen, dict)):
            raise BoundaryError("human_text_import", "exact_begin_input_required")
        public = project_text_menu_snapshot(snapshot)
        actions = snapshot["menu_actions"]["actions"]
        matches = [index for index, action in enumerate(actions) if action == chosen]
        if (len(matches) != 1 or chosen.get("verb") != "begin_card_play"
                or chosen.get("effect_domain") != "native_input"
                or public.action_ids[matches[0]] != chosen.get("action_id")):
            raise BoundaryError("human_text_import", "chosen_catalog_binding_mismatch")
        accepted.append((row, public, group))
    groups = sorted({root(group) for _, _, group in accepted})
    if len(groups) < 2:
        raise BoundaryError("human_text_import", "independent_groups_required")
    dev = groups[0]
    samples = []
    for ordinal, row in enumerate(rows):
        group = row["session_id"]
        split = "dev" if root(group) == dev else "train"
        included = row["disposition"] == "accepted_input"
        report_rows.append({
            "source_index": ordinal, "record_id": row["record_id"],
            "session_id": row["session_id"], "run_id": row["run_id"],
            "sequence": row["sequence"], "disposition": row["disposition"],
            "reason_code": row.get("reason_code"), "split": split,
            "status": "included" if included else "excluded",
            "snapshot_id": (row.get("snapshot", {}).get("snapshot_id")
                            if isinstance(row.get("snapshot"), dict) else None),
            "selected_action_id": (row.get("chosen_action", {}).get("action_id")
                                   if isinstance(row.get("chosen_action"), dict) else None),
        })
        if included:
            public = project_text_menu_snapshot(row["snapshot"])
            samples.append(ModelSample(
                semantic_hash([row["session_id"], row["record_id"]]),
                semantic_hash([row["session_id"], row["run_id"]]), split,
                row["snapshot"]["interaction"]["kind"],
                "text_menu_human_input", public.state_text, public.action_texts,
                public.action_ids, public.action_ids.index(row["chosen_action"]["action_id"]),
            ))
    if {sample.split for sample in samples} != {"train", "dev"}:
        raise BoundaryError("human_text_import", "nonempty_train_dev_required")
    return tuple(samples), {
        "schema": VIEW_SCHEMA, "serializer": IDENTITY,
        "label_boundary": "owner_attested_human_exact_native_begin_input",
        "human_origin": "explicit_owner_attestation_not_machine_verifiable",
        "native_successor_supervision": False,
        "split_basis": "whole_session_run_and_duplicate_visible_current_input",
        "rows": report_rows,
    }


def publish_human_text_bc_view(
    store: ArtifactStore, source_id: str, producer: Producer,
) -> Manifest:
    source, rows = load_human_text_source(store, source_id)
    samples, report = _project(rows)
    payloads = (
        store.put_payload("samples", io.BytesIO(b"".join(json_bytes(s.to_dict()) for s in samples)),
                          "application/x-ndjson"),
        store.put_payload("lineage", io.BytesIO(json_bytes(report)), "application/json"),
    )
    manifest = Manifest("model_view", producer, (Parent("dataset", source_id),), payloads,
                        FrozenObject.of({"schema": VIEW_SCHEMA, "serializer": IDENTITY,
                                         "scope": "engineering", "samples": len(samples),
                                         "source_digest": source.parameters.value()[
                                             "source_digest"],
                                         "label_boundary": report["label_boundary"]}))
    store.publish(manifest)
    return manifest


def load_human_text_bc_view(
    store: ArtifactStore, manifest: Manifest,
) -> tuple[Manifest, tuple[ModelSample, ...]]:
    if (manifest.kind != "model_view" or len(manifest.parents) != 1
            or [p.role for p in manifest.parents] != ["dataset"]
            or sorted(p.role for p in manifest.payloads) != ["lineage", "samples"]):
        raise BoundaryError("human_text_import", "unsupported_view")
    source, rows = load_human_text_source(store, manifest.parent("dataset"))
    samples, report = _project(rows)
    expected = {"schema": VIEW_SCHEMA, "serializer": IDENTITY, "scope": "engineering",
                "samples": len(samples), "source_digest": source.parameters.value()[
                    "source_digest"],
                "label_boundary": report["label_boundary"]}
    if manifest.parameters.value() != expected:
        raise BoundaryError("human_text_import", "view_identity_mismatch")
    if (b"".join(store.read_payload(manifest.payload("samples")))
            != b"".join(json_bytes(s.to_dict()) for s in samples)
            or b"".join(store.read_payload(manifest.payload("lineage"))) != json_bytes(report)):
        raise BoundaryError("human_text_import", "view_projection_mismatch")
    return manifest, samples
