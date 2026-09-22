"""Read-only replay audit of allocated source decisions against the online input contract."""
from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from .decision_dataset import _identity
from .decision_training import load_allocation
from .platform_bundle3 import _extract, _lines, _load
from .public_inputs import IDENTITY, match_recorded_choice, project_public_snapshot


def audit_allocation(store: ArtifactStore, allocation_id: str) -> dict:
    """Verify the existing allocation, then inspect only its original source frames.

    The report is not a new allocation, training authorization or runtime receipt.
    No source is changed and no recorded execution state is fed to the live projection.
    """
    allocation, dataset, members = load_allocation(store, allocation_id)
    selected = {m["occurrence"]: m for m in members["members"]}
    records = [r for r in dataset.records if _identity(r) in selected]
    if len(records) != len(selected):
        raise BoundaryError("live_audit", "allocation_members_missing")
    pending, archives = [allocation_id], {}
    seen: set[str] = set()
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        if len(seen) >= 512:
            raise BoundaryError("live_audit", "lineage_limit")
        seen.add(identity)
        manifest = store.get_manifest(identity)
        if manifest.kind == "evidence":
            payload = manifest.payload("archive")
            archives[payload.sha256] = payload
        pending.extend(p.artifact_id for p in manifest.parents)
    rows = []
    for source_hash in sorted({r.provenance.bundle_sha256 for r in records}):
        if source_hash not in archives:
            raise BoundaryError("live_audit", "original_archive_missing")
        with tempfile.TemporaryDirectory(prefix="stage1a-live-audit-") as name:
            directory = Path(name)
            _extract(b"".join(store.read_payload(archives[source_hash])), directory)
            # load_allocation has already run the owner verifier and reproduced
            # these records from these content-addressed source bytes.
            canonical = {semantic_hash(r): r for r in
                         _lines(directory / "export/canonical-transitions.jsonl")}
            for record in records:
                if record.provenance.bundle_sha256 != source_hash:
                    continue
                evidence = record.source_evidence.value()
                original = canonical.get(evidence["canonical_ref"])
                if original is None:
                    raise BoundaryError("live_audit", "canonical_record_missing")
                frame = _load(directory / "raw" / original["pre_state_ref"]["object_ref"])
                entry = {"occurrence": _identity(record), "transition_id": record.transition_id,
                         "split": selected[_identity(record)]["split"],
                         "surface": record.surface, "recorded_candidates": len(record.actions),
                         "has_execution_supplement": "execution" in record.state.decision.value()}
                try:
                    public = project_public_snapshot(frame["snapshot"])
                    entry["public_candidates"] = len(public.actions)
                    if record.chosen_key is None:
                        raise BoundaryError("public_input", "recorded_choice_missing")
                    entry["chosen_public_index"] = match_recorded_choice(
                        public, record.actions, record.chosen_key,
                    )
                    entry["status"] = "public_catalog_aligned_new_input_required"
                except BoundaryError as error:
                    entry.update(status="excluded_from_public_pilot", reason=error.code)
                rows.append(entry)
    if len(rows) != len(selected):
        raise BoundaryError("live_audit", "audit_coverage_mismatch")
    return {
        "schema": "stpd/stage1a-live-input-audit-v1", "allocation_id": allocation_id,
        "input_contract": IDENTITY, "rows": rows,
        "counts": dict(Counter(str(r["status"]) for r in rows)),
        "exclusions": dict(Counter(str(r["reason"]) for r in rows if "reason" in r)),
        "by_split": {split: dict(Counter(str(r["status"]) for r in rows if r["split"] == split))
                     for split in ("train", "dev")},
        "native_runtime": "not_tested", "existing_model_compatibility": "not_established",
        "effects": "read_only_no_data_deletion_no_training_no_game_commands",
    }
