"""Manifest-first purpose-bound selections; full records are materialized on demand.

The selection receipt contains no state/action payload. Loading still reproduces the
same selection from owner-verified sources and checks its exact logical identity.
"""

from __future__ import annotations

import io
from collections import Counter
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    json_bytes,
    object_fields,
)
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from .decision_cache import VerifiedSourceCache
from .decision_dataset import DecisionDataset, SelectionRules, _facets, _identity
from .decision_spool import DecisionSpool, SpoolSelection, row_summary

SCHEMA = "stpd/curated-decision-dataset-v1"
PURPOSES = frozenset({"training", "test", "gold"})


def selection_annotations(annotations: dict[str, Any]) -> dict[str, Any]:
    """Comments stay in the private quality ledger, not a downloadable dataset."""
    return {"revision": annotations["revision"], "items": {
        key: {"sequence": value["sequence"], "action": value["action"], "reason": ""}
        for key, value in annotations["items"].items()
    }}


def curate(base: DecisionDataset, purpose: str, annotations: dict[str, Any]) -> DecisionDataset:
    if purpose not in PURPOSES:
        raise BoundaryError("curation", "invalid_dataset_purpose")
    snapshot = object_fields(annotations, {"revision", "items"}, "curation.annotations")
    if (
        type(snapshot["revision"]) is not int
        or snapshot["revision"] < 0
        or not isinstance(snapshot["items"], dict)
    ):
        raise BoundaryError("curation", "invalid_annotation_snapshot")
    excluded_ids = set()
    for key, value in snapshot["items"].items():
        item = object_fields(value, {"sequence", "action", "reason"}, "curation.annotation")
        if item["action"] not in {"exclude", "flag", "restore"}:
            raise BoundaryError("curation", "invalid_annotation_snapshot")
        if item["action"] == "exclude":
            excluded_ids.add(key)
    snapshot = selection_annotations(snapshot)
    report = base.report.value()
    spool = DecisionSpool()
    excluded = list(report["excluded"])
    summaries = (base.records.summaries() if isinstance(base.records, SpoolSelection)
                 else (row_summary(_identity(r), r) for r in base.records))
    retained: set[str] = set()
    for row in summaries:
        key = row["id"]
        parents = {
            semantic_hash(
                [
                    row["session_id"],
                    row["native_run_id"],
                    parent["action"]["decision"]["decision_id"],
                ]
            )
            for parent in report["context"].get(key, {}).get("parents", [])
        }
        if key in excluded_ids or parents & excluded_ids:
            excluded.append(
                {
                    "decision_id": key,
                    "reason": "human_quality_exclusion"
                    if key in excluded_ids
                    else "excluded_parent_decision",
                }
            )
        else:
            retained.add(key)
    if isinstance(base.records, SpoolSelection):
        spool.copy_selected(base.records, retained)
    else:
        for record in base.records:
            key = _identity(record)
            if key in retained:
                spool[key] = record
                spool.select(key)
    records = spool.selected()
    selected_runs = {row["run_id"] for row in records.summaries()}
    if purpose in {"test", "gold"}:
        splits = {run: "test" if purpose == "test" else "gold_test" for run in selected_runs}
        split_status = "purpose_assigned"
    else:
        # The existing independent-component split remains the owner. A separate test
        # dataset is now explicit, so its old internal test components join training.
        splits = {
            run: ("train" if partition == "test" else partition)
            for run, partition in report["splits"].items()
            if run in selected_runs
        }
        split_status = report["split_status"]
    report.update(
        curation={"purpose": purpose, "annotations": snapshot, "base_logical_id": base.logical_id},
        selected=len(records),
        excluded=excluded,
        exclusion_counts=dict(Counter(item["reason"] for item in excluded)),
        selected_facets=_facets(records, report["environments"]),
        splits=splits,
        split_status=split_status,
    )
    return DecisionDataset(records, FrozenObject.of(report))


def publish_selection(
    store: ArtifactStore,
    sources: tuple[Manifest, ...],
    rules: SelectionRules,
    producer: Producer,
    dataset: DecisionDataset,
    *,
    merging: bool,
    expected: str,
    paired_training: str | None,
    reservation: str | None = None,
) -> Manifest:
    if dataset.logical_id != expected:
        raise BoundaryError("curation", "preview_changed")
    if not dataset.records:
        raise BoundaryError("curation", "empty_selection")
    report = dataset.report.value()
    purpose = report["curation"]["purpose"]
    receipt = {
        "schema": SCHEMA,
        "logical_id": dataset.logical_id,
        # The internal report includes unselected source context. Export only its
        # commitment and the frozen selection decisions, never that raw context.
        "report_sha256": semantic_hash(report),
        "annotations": report["curation"]["annotations"],
        "paired_training": paired_training,
    }
    payload = store.put_payload("selection", io.BytesIO(json_bytes(receipt)), "application/json")
    parents = [
        Parent(("dataset_" if merging else "source_") + source.artifact_id, source.artifact_id)
        for source in sorted(sources, key=lambda s: s.artifact_id)
    ]
    # A training reference is a comparison constraint, not a data parent. Including it
    # as a data parent would falsely make disjoint test content contain training data.
    manifest = Manifest(
        "dataset",
        producer,
        parents=tuple(parents),
        payloads=(payload,),
        parameters=FrozenObject.of(
            {
                "schema": SCHEMA,
                "logical_id": dataset.logical_id,
                "purpose": purpose,
                "rules": rules.to_dict(),
                "merging": merging,
                "paired_training": paired_training,
                "records": len(dataset.records),
                "runs": len(report["splits"]),
                "scope": "platform_verified",
                "split_status": report["split_status"],
                "materialization": "on_demand",
                "sealed_test": purpose == "gold",
                "reservation": reservation if purpose == "gold" else None,
                "isolation": "managed_sealed" if purpose == "gold" else "ordinary",
                "historical_external_exposure": "unknown",
            }
        ),
    )
    store.publish(manifest)
    return manifest


def load_selection(
    store: ArtifactStore,
    manifest: Manifest,
    *,
    cache: VerifiedSourceCache | None,
    depth: int = 0,
    memo: dict[str, DecisionDataset] | None = None,
    visited: set[str] | None = None,
) -> DecisionDataset:
    from .selection_session import _load_selection as reuse_selection

    # Recursive lineage keeps the existing memo and depth checks. Only the outer
    # curated selection can reuse a complete, previously verified immutable result.
    if depth == 0:
        def owning_load(fresh: bool) -> DecisionDataset:
            selected_memo = {} if fresh else memo
            try:
                return _load_selection(
                    store, manifest, cache=cache, depth=depth,
                    memo=selected_memo, visited=visited,
                )
            finally:
                if fresh and memo is not None and selected_memo is not None:
                    memo.update(selected_memo)

        selected = reuse_selection(store, manifest, owning_load)
        if memo is not None:
            memo[manifest.artifact_id] = selected
        return selected
    return _load_selection(store, manifest, cache=cache, depth=depth, memo=memo, visited=visited)


def _load_selection(
    store: ArtifactStore,
    manifest: Manifest,
    *,
    cache: VerifiedSourceCache | None,
    depth: int = 0,
    memo: dict[str, DecisionDataset] | None = None,
    visited: set[str] | None = None,
) -> DecisionDataset:
    from .decision_store import load, preview
    from .decision_union import union_decisions

    memo = {} if memo is None else memo
    visited = set() if visited is None else visited
    if manifest.artifact_id in memo:
        return memo[manifest.artifact_id]
    visited.add(manifest.artifact_id)
    if depth > 8 or len(visited) > 256:
        raise BoundaryError("curation", "dataset_lineage_limit")
    info = manifest.parameters.value()
    if manifest.kind != "dataset" or info.get("schema") != SCHEMA:
        raise BoundaryError("curation", "unsupported_dataset_schema")
    payload = manifest.payload("selection")
    if payload.size > 32 * 1024**2:
        raise BoundaryError("curation", "selection_size_limit")
    value = object_fields(
        decode_json(b"".join(store.read_payload(payload))),
        {"schema", "logical_id", "report_sha256", "annotations", "paired_training"},
        "curation.selection",
    )
    if value["schema"] != SCHEMA:
        raise BoundaryError("curation", "unsupported_dataset_schema")
    parents = tuple(store.get_manifest(p.artifact_id) for p in manifest.parents)
    if not 1 <= len(parents) <= 100:
        raise BoundaryError("curation", "invalid_parents")
    rules = SelectionRules.decode(info["rules"])
    if info["merging"]:
        loaded = []
        for parent in parents:
            item = (
                load_selection(store, parent, cache=cache, depth=depth + 1,
                               memo=memo, visited=visited)
                if parent.parameters.value().get("schema") == SCHEMA
                else load(store, parent.artifact_id, cache=cache)[1]
            )
            loaded.append((parent.artifact_id, item))
        base = union_decisions(tuple(loaded), rules)
    else:
        base = preview(store, parents, rules, cache=cache)
    prefix = "dataset_" if info["merging"] else "source_"
    if any(p.role != prefix + p.artifact_id for p in manifest.parents):
        raise BoundaryError("curation", "parent_inventory_mismatch")
    selected = curate(base, info["purpose"], value["annotations"])
    if (
        selected.logical_id != info["logical_id"]
        or semantic_hash(selected.report.value()) != value["report_sha256"]
        or len(selected.records) != info["records"]
        or value["logical_id"] != info["logical_id"]
        or value["paired_training"] != info["paired_training"]
        or info["sealed_test"] != (info["purpose"] == "gold")
    ):
        raise BoundaryError("curation", "selection_reprojection_mismatch")
    memo[manifest.artifact_id] = selected
    return selected
