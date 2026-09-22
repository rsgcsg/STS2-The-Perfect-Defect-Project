"""BC from exact Human observation bindings, separate from execution S-a-S' views.

The owner verifier and fixed allocation admit the source. This view adds stricter
public-input requirements; it never invents a public mapping for native-only rows.
"""
from __future__ import annotations

import io
import tempfile
from collections import Counter
from pathlib import Path

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from .decision_dataset import _identity
from .decision_spool import SpoolSelection
from .decision_training import load_allocation
from .features import ModelSample
from .platform_bundle3 import _extract, _lines, _load
from .public_inputs import COMPACT_IDENTITY, IDENTITY, PublicInput, project_public_snapshot

LEGACY_VIEW_SCHEMA = "stpd/public-observation-bc-view-v1"
VIEW_SCHEMA = "stpd/public-observation-bc-view-v2"
BINDING_BASES = frozenset({
    "reference_equality_to_frozen_host_binding",
    "exact_native_owner_operation_and_frozen_host_binding",
})


def bound_choice(
    frame: dict, reference: dict, action: dict, *, compact: bool = False,
) -> tuple[PublicInput, int]:
    """Called only with an owner-verified proof; bind its exact H, not execution S."""
    try:
        snapshot = frame["snapshot"]
        identity = action["human_observation_snapshot_id"]
        if (not isinstance(identity, str) or not identity
                or any(value != identity for value in
                       (reference["snapshot_id"], frame["snapshot_id"], snapshot["snapshot_id"]))):
            raise BoundaryError("public_bc", "human_observation_binding_mismatch")
        selected = action.get("bound_action")
        if not isinstance(selected, dict) or action.get("native_input") is not None:
            raise BoundaryError("public_bc", "exact_public_binding_required")
        mapping = action.get("mapping", {})
        if (mapping.get("status") != "exact_unique" or type(mapping.get("match_count")) is not int
                or mapping["match_count"] != 1 or mapping.get("basis") not in BINDING_BASES):
            raise BoundaryError("public_bc", "exact_owner_mapping_required")
        public = project_public_snapshot(snapshot, compact=compact)
        candidates = snapshot["bound_actions"]["actions"]
        matches = [i for i, value in enumerate(candidates)
                   if all(value.get(k) == selected.get(k)
                          for k in ("bound_action_id", "verb", "subject_referent_id"))
                   and {a["role"]: a["referent_id"] for a in value["arguments"]}
                   == selected["arguments"]]
        if len(matches) != 1:
            raise BoundaryError("public_bc", "exact_public_choice_not_unique")
        # IDs select the witnessed occurrence; they never become model text.
        return public, matches[0]
    except (KeyError, TypeError, AttributeError) as error:
        raise BoundaryError("public_bc", "incomplete_human_binding") from error


def project_allocation(store: ArtifactStore, allocation_id: str, *, compact: bool = True) -> tuple:
    allocation, dataset, members = load_allocation(store, allocation_id)
    selected = {m["occurrence"]: m for m in members["members"]}
    records = ([dataset.records.owner[key] for key in sorted(selected)]
               if isinstance(dataset.records, SpoolSelection)
               else [r for r in dataset.records if _identity(r) in selected])
    if len(records) != len(selected):
        raise BoundaryError("public_bc", "allocation_members_missing")
    pending, archives = [allocation_id], {}
    seen: set[str] = set()
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        if len(seen) >= 512:
            raise BoundaryError("public_bc", "lineage_limit")
        seen.add(identity)
        manifest = store.get_manifest(identity)
        if manifest.kind == "evidence":
            payload = manifest.payload("archive")
            archives[payload.sha256] = payload
        pending.extend(p.artifact_id for p in manifest.parents)
    samples, dispositions = [], []
    for source in sorted({r.provenance.bundle_sha256 for r in records}):
        if source not in archives:
            raise BoundaryError("public_bc", "original_archive_missing")
        with tempfile.TemporaryDirectory(prefix="public-bc-") as name:
            directory = Path(name)
            _extract(b"".join(store.read_payload(archives[source])), directory)
            proofs = {semantic_hash(e): e for e in
                      _lines(directory / "raw/semantic-boundary-trace.jsonl")
                      if e["kind"] == "transition_proved"}
            for record in records:
                if record.provenance.bundle_sha256 != source:
                    continue
                evidence = record.source_evidence.value()
                proof = proofs.get(evidence["proof_ref"])
                if (proof is None or proof.get("human_observation_ref")
                        != evidence.get("human_observation_ref")):
                    raise BoundaryError("public_bc", "verified_proof_reference_mismatch")
                reference = evidence.get("human_observation_ref")
                row = {"occurrence": _identity(record), "transition_id": record.transition_id,
                       "source_archive_sha256": source, "proof_ref": evidence["proof_ref"],
                       "split": selected[_identity(record)]["split"], "surface": record.surface}
                try:
                    if not isinstance(reference, dict):
                        raise BoundaryError("public_bc", "human_observation_missing")
                    # Owner verification has checked the exact object path/hash and
                    # immutable proof/canonical/action linkage before this read.
                    frame = _load(directory / "raw" / reference["object_ref"])
                    public, index = bound_choice(frame, reference, proof["action"], compact=compact)
                    samples.append(ModelSample(
                        record.transition_id, record.run_id, row["split"],
                        frame["snapshot"]["interaction"]["kind"], record.family,
                        public.state_text, public.action_texts,
                        tuple(a.key for a in public.actions), index,
                    ))
                    row.update(status="included", human_frame_sha256=reference["content_sha256"],
                               candidates=len(public.actions), chosen_index=index)
                except BoundaryError as error:
                    row.update(status="excluded", reason=error.code)
                dispositions.append(row)
    report = {
        "schema": VIEW_SCHEMA if compact else LEGACY_VIEW_SCHEMA, "allocation_id": allocation_id,
        "serializer": COMPACT_IDENTITY if compact else IDENTITY,
        "label_boundary": "exact_human_observation_bound_action",
        "successor_supervision": False, "rows": dispositions,
        "counts": dict(Counter(row["status"] for row in dispositions)),
        "exclusions": dict(Counter(row["reason"] for row in dispositions if "reason" in row)),
        "by_split": {split: dict(Counter(r["status"] for r in dispositions if r["split"] == split))
                     for split in ("train", "dev")},
        "included_surfaces": dict(Counter(sample.surface for sample in samples)),
    }
    if len(dispositions) != len(selected):
        raise BoundaryError("public_bc", "projection_coverage_mismatch")
    return allocation, dataset, tuple(samples), report


def _parameters(dataset, samples, report):
    return {"schema": report["schema"], "serializer": report["serializer"],
            "scope": "platform_verified",
            "samples": len(samples), "dataset_logical_id": dataset.logical_id,
            "purpose": "engineering", "label_boundary": report["label_boundary"],
            "successor_supervision": False, "counts": report["counts"]}


def _encoded(samples, report):
    return {"samples": b"".join(json_bytes(sample.to_dict()) for sample in samples),
            "dispositions": json_bytes(report)}


def publish_public_bc_view(
    store: ArtifactStore, allocation_id: str, producer: Producer,
) -> Manifest:
    allocation, dataset, samples, report = project_allocation(store, allocation_id)
    if {s.split for s in samples} != {"train", "dev"}:
        raise BoundaryError("public_bc", "nonempty_train_dev_required")
    payloads = tuple(store.put_payload(role, io.BytesIO(raw),
                    "application/x-ndjson" if role == "samples" else "application/json")
                     for role, raw in _encoded(samples, report).items())
    manifest = Manifest(
        "model_view", producer,
        (Parent("allocation", allocation_id), Parent("dataset", allocation.parent("dataset"))),
        payloads, FrozenObject.of(_parameters(dataset, samples, report)),
    )
    store.publish(manifest)
    return manifest


def load_public_bc_view(store: ArtifactStore, manifest: Manifest) -> tuple:
    schema = manifest.parameters.value().get("schema")
    if (manifest.kind != "model_view" or schema not in {VIEW_SCHEMA, LEGACY_VIEW_SCHEMA}
            or sorted(p.role for p in manifest.parents) != ["allocation", "dataset"]
            or sorted(p.role for p in manifest.payloads) != ["dispositions", "samples"]):
        raise BoundaryError("public_bc", "unsupported_contract")
    allocation, dataset, samples, report = project_allocation(
        store, manifest.parent("allocation"), compact=schema == VIEW_SCHEMA,
    )
    if (allocation.parent("dataset") != manifest.parent("dataset")
            or manifest.parameters.value() != _parameters(dataset, samples, report)
            or {s.split for s in samples} != {"train", "dev"}):
        raise BoundaryError("public_bc", "view_identity_mismatch")
    for role, raw in _encoded(samples, report).items():
        payload = manifest.payload(role)
        if payload.size != len(raw) or b"".join(store.read_payload(payload)) != raw:
            raise BoundaryError("public_bc", "source_projection_mismatch")
    return manifest, samples
