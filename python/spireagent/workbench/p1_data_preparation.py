"""Incremental P1 preparation on the existing Store and Curation owner.

The adjacent private cache is disposable, like decision_cache; it contains no use
reservations. A frozen snapshot is always published and re-read by dataset.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from collections import Counter
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from stpd.p1.data import digest, json_bytes
from stpd.p1.dataset import (
    SUPPORTED_PROJECTIONS,
    Projection,
    _points,
    inspect_projections,
    load_snapshot,
    merge_projection_inspections,
    projection_payload_role,
    publish_snapshot,
)
from stpd.p1.input import INPUT_SPEC, LEGACY_INPUT_SPEC

from .p1_training_data import _ledger

CACHE_LIMIT = 64 * 1024**2
ENTRY_LIMIT = 16 * 1024**2
DEPENDENCIES = (
    "stpd/p1/dataset.py",
    "stpd/p1/input.py",
    "stpd/p1/data.py",
    "stpd/p1/spec.py",
    "stpd/fullrun/data.py",
    "spireagent/json_boundary.py",
    "spireagent/artifact_contracts.py",
    "spireagent/storage/store.py",
    "spireagent/storage/local.py",
    "spireagent/storage/blobs.py",
    "spireagent/workbench/p1_data_preparation.py",
    "spireagent/encoding.py",
    "spireagent/instance_lock.py",
    "uv.lock",
)


def preparation_identity() -> str:
    root = Path(__file__).resolve().parents[2]
    return digest(json_bytes([[name, digest((root / name).read_bytes())] for name in DEPENDENCIES]))


class P1PreparationCache:
    """Bounded private derivative beside the owning ledger, never a second ledger."""

    def __init__(self, ledger_path: Path) -> None:
        self.path = ledger_path.with_name(ledger_path.stem + ".p1-preparation-cache.sqlite")
        descriptor = os.open(
            self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
                raise BoundaryError("p1_preparation", "private_regular_cache_required")
        finally:
            os.close(descriptor)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS projection_cache("
                "id TEXT PRIMARY KEY,identity TEXT,payload_sha256 TEXT,"
                "body BLOB,sha256 TEXT,used REAL)"
            )
        self.identity = preparation_identity()
        self.hits = self.misses = self.corrupt = self.decoded = self.verified_bytes = 0

    def inspect(
        self, store: Any, identity: str, spec: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest = store.get_manifest(identity)
        # Check the source still exists, even on a cache hit. Manifest bytes are
        # independently identity-checked by Store rather than trusting SQLite.
        store.get_manifest(manifest.parent("source"))
        payload = manifest.payload(projection_payload_role(manifest))
        key = digest(json_bytes([self.identity, identity, spec]))
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT identity,payload_sha256,body,sha256 FROM projection_cache WHERE id=?",
                (identity,),
            ).fetchone()
            if row and row[0] == key and row[1] == payload.sha256:
                try:
                    if digest(row[2]) == row[3]:
                        value = json.loads(row[2])
                        index, cached_quality = value["index"], value["quality"]
                        # Keep the owning Store's integrity, peer recovery and
                        # lease semantics. Cache only the expensive decode/Input
                        # validation, never turn a checksum row into read authority.
                        for chunk in store.read_payload(payload):
                            self.verified_bytes += len(chunk)
                        self.hits += 1
                        db.execute(
                            "UPDATE projection_cache SET used=? WHERE id=?", (time.time(), identity)
                        )
                        return index, cached_quality
                except (ValueError, KeyError, TypeError):
                    pass
                self.corrupt += 1
        self.misses += 1
        value, quality = _inspect_with_quality(store, identity, spec)
        self.decoded += 1
        self.verified_bytes += payload.size
        raw = json_bytes({"index": value, "quality": quality})
        if len(raw) <= ENTRY_LIMIT:
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "INSERT OR REPLACE INTO projection_cache VALUES(?,?,?,?,?,?)",
                    (identity, key, payload.sha256, raw, digest(raw), time.time()),
                )
                while (
                    db.execute(
                        "SELECT coalesce(sum(length(body)),0) FROM projection_cache"
                    ).fetchone()[0]
                    > CACHE_LIMIT
                ):
                    db.execute(
                        "DELETE FROM projection_cache WHERE id=(SELECT id FROM projection_cache "
                        "ORDER BY used LIMIT 1)"
                    )
        return value, quality


def _quality(projection: Any) -> dict[str, Any]:
    masks: Counter[str] = Counter()
    scenes: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    for _, _, point in _points(projection):
        for target in ("N", "next", "end"):
            masks[
                target
                + (
                    "_available"
                    if point.get("mask", {}).get(target) is True
                    else "_missing_or_not_applicable"
                )
            ] += 1
        if point.get("mask", {}).get("N") is True:
            observation = point["observation"]
            scenes[str(observation.get("scene", {}).get("family", "unknown"))] += 1
            chosen = next(c for c in observation["catalog"] if c["choice_id"] == point["chosen"])
            actions[str(chosen.get("kind", "unknown"))] += 1
    source = projection.source.parameters.value()
    return {
        "declared_origin": source.get("declared_origin", "unknown"),
        "character": projection.lineage.get("character", "unknown"),
        "masks": dict(masks),
        "scene_N": dict(scenes),
        "chosen_action_N": dict(actions),
        "quality_scope": "projection_masks_not_native_Human_or_research_qualification",
    }


def _inspect_with_quality(
    store: Any,
    identity: str,
    spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    classification: dict[str, Any] = {}

    def capture(projection: Projection) -> None:
        classification.update(_quality(projection))

    index = inspect_projections(store, [identity], input_projection=spec, on_projection=capture)
    return index, classification


def discover_p1_projections(store: Any) -> list[dict[str, Any]]:
    """Discover completed immutable named projections; active recordings are not inputs."""
    result = []
    for identity in store.manifest_ids():
        manifest = store.get_manifest(identity)
        info = manifest.parameters.value()
        if (
            manifest.kind != "analysis"
            or (info.get("schema"), info.get("projection_id")) not in SUPPORTED_PROJECTIONS
        ):
            continue
        source = store.get_manifest(manifest.parent("source"))
        original = source.parameters.value()
        result.append(
            {
                "projection_id": identity,
                "source_id": source.artifact_id,
                "revision": info.get("revision"),
                "projection": info.get("projection_id"),
                "tool_release_id": info.get("tool_release_id"),
                "declared_origin": original.get("declared_origin", "unknown"),
            }
        )
    return result


def _string_list(value: object, field: str, *, optional: bool = True) -> None:
    if value is None and optional:
        return
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
        raise BoundaryError("p1_preparation", "invalid_" + field)


def _selection_parameters(
    projection_ids: object,
    selected_decisions: object,
    split_salt: object,
    *,
    projections_required: bool = False,
) -> None:
    _string_list(projection_ids, "projection_ids", optional=not projections_required)
    if selected_decisions is not None:
        if not isinstance(selected_decisions, dict):
            raise BoundaryError("p1_preparation", "invalid_selected_decisions")
        for identity, decisions in selected_decisions.items():
            if not isinstance(identity, str) or not identity:
                raise BoundaryError("p1_preparation", "invalid_selected_decisions")
            _string_list(decisions, "selected_decisions", optional=False)
    if not isinstance(split_salt, str) or not split_salt:
        raise BoundaryError("p1_preparation", "explicit_split_identity_required")


def prepare_p1_data(
    owner: Any,
    store: Any,
    *,
    revision: str = "r1.5",
    declared_origins: list[str] | None = None,
    projection_ids: list[str] | None = None,
    projection_names: list[str] | None = None,
    tool_release_ids: list[str] | None = None,
    selected_decisions: dict[str, list[str]] | None = None,
    split_salt: str = "p1-incremental-engineering-1",
    cache: bool = True,
    allocation_policy: str = "preserve_existing",
) -> dict[str, Any]:
    """Discover/filter/index/classify and draft allocation; never reserve or record use."""
    from spireagent.p1_curation import draft_p1_allocation

    if type(cache) is not bool:
        raise BoundaryError("p1_preparation", "invalid_cache_boolean")
    for field, values in (
        ("declared_origins", declared_origins),
        ("projection_names", projection_names),
        ("tool_release_ids", tool_release_ids),
    ):
        _string_list(values, field)
    _selection_parameters(projection_ids, selected_decisions, split_salt)
    if not isinstance(allocation_policy, str) or allocation_policy not in {
        "preserve_existing",
        "observed_groups_engineering",
    }:
        raise BoundaryError("p1_preparation", "unsupported_allocation_policy")
    started = time.monotonic()
    ledger = _ledger(owner, store)
    specs = {s["revision"]: s for s in (INPUT_SPEC, LEGACY_INPUT_SPEC)}
    if not isinstance(revision, str) or revision not in specs:
        raise BoundaryError("p1_preparation", "unsupported_revision")
    discovered = discover_p1_projections(store)
    requested = None if projection_ids is None else set(projection_ids)
    if requested is not None and (
        not requested
        or len(requested) != len(projection_ids or [])
        or requested - {r["projection_id"] for r in discovered}
    ):
        raise BoundaryError("p1_preparation", "explicit_unique_named_projections_required")
    rows, excluded = [], []
    for row in discovered:
        reason = (
            "not_explicitly_selected"
            if requested is not None and row["projection_id"] not in requested
            else "revision_filter"
            if row["revision"] != revision
            else "projection_name_filter"
            if projection_names is not None and row["projection"] not in projection_names
            else "tool_release_filter"
            if tool_release_ids is not None and row["tool_release_id"] not in tool_release_ids
            else "declared_origin_filter"
            if declared_origins is not None and row["declared_origin"] not in declared_origins
            else None
        )
        if reason:
            excluded.append({**row, "reason": reason})
        else:
            rows.append(row)
    derived = P1PreparationCache(owner.path) if cache else None
    inspections, quality, conflicts = [], {}, []
    for row in rows:
        identity = row["projection_id"]
        try:
            if derived is not None:
                current_index, classification = derived.inspect(store, identity, specs[revision])
            else:
                current_index, classification = _inspect_with_quality(
                    store, identity, specs[revision]
                )
            inspections.append(current_index)
            quality[identity] = classification
        except (BoundaryError, ValueError, KeyError, TypeError, OSError) as error:
            conflicts.append(
                {"projection_id": identity, "code": getattr(error, "code", "invalid_projection")}
            )
    index = None
    allocation = None
    selection = None
    if inspections:
        try:
            index = merge_projection_inspections(inspections, selected_decisions=selected_decisions)
            allocation = draft_p1_allocation(
                ledger, index, split_salt=split_salt, allocation_policy=allocation_policy
            )
            quality_filtered = allocation["quality_exclusions"]
            if quality_filtered:
                removed = {x["source_decision_id"] for x in quality_filtered}
                filtered = {}
                for value in inspections:
                    identity = value["sources"][0]["projection_id"]
                    wanted = (
                        set(selected_decisions[identity])
                        if selected_decisions and identity in selected_decisions
                        else {p["source_decision_id"] for p in value["points"]}
                    )
                    filtered[identity] = sorted(wanted - removed)
                index = merge_projection_inspections(inspections, selected_decisions=filtered)
                allocation = draft_p1_allocation(
                    ledger, index, split_salt=split_salt, allocation_policy=allocation_policy
                )
            allocation["quality_filtered_N"] = quality_filtered
            if revision != INPUT_SPEC["revision"]:
                allocation["blockers"].append("legacy_revision_inventory_only")
                allocation["ready_to_freeze"] = False
            selection = {
                "projection_ids": [s["projection_id"] for s in index["sources"]],
                "selected_decisions": index["selection"],
                "split_salt": split_salt,
                "split_by_group": allocation["split_by_group"],
            }
        except BoundaryError as error:
            conflicts.append(
                {
                    "code": error.code,
                    "resolution": (
                        "select exact projection IDs or explicit per-projection native decision IDs"
                    ),
                }
            )
    if conflicts:
        selection = None
    return {
        "schema": "stpd/p1-data-preparation-1",
        "revision": revision,
        "discovered": discovered,
        "excluded": excluded,
        "classification": quality,
        "classification_scope": {
            "unit": "per_projection_points_before_explicit_decision_selection_and_N_dedup",
            "mask_counts": "all_projection_points",
            "scene_and_chosen_action_counts": (
                "N_true_projection_entries_including_alias_duplicates"
            ),
            "unique_N_count": "index.unique_N",
        },
        "filters": {
            "declared_origins": declared_origins,
            "projection_names": projection_names,
            "tool_release_ids": tool_release_ids,
            "projection_ids": projection_ids,
        },
        "index": index,
        "allocation": allocation,
        "selection": selection,
        "conflicts": conflicts,
        "ready_to_freeze": bool(selection and allocation and allocation["ready_to_freeze"]),
        "cache": {
            "hits": derived.hits if derived else 0,
            "misses": derived.misses if derived else len(inspections),
            "corrupt": derived.corrupt if derived else 0,
            "decoded_projections": derived.decoded if derived else len(inspections),
            "implementation_identity": derived.identity if derived else preparation_identity(),
            "verified_payload_bytes": derived.verified_bytes
            if derived
            else sum(
                store.get_manifest(x["sources"][0]["projection_id"])
                .payload(
                    projection_payload_role(store.get_manifest(x["sources"][0]["projection_id"]))
                )
                .size
                for x in inspections
            ),
            "verification_policy": "normal_Store_stream_hash_and_lease_decode_cache_only",
        },
        "elapsed_seconds": time.monotonic() - started,
        "purpose_reserved": False,
        "training_use_recorded": False,
        "research_admission": "not_evaluated",
    }


def freeze_p1_data(
    owner: Any,
    store: Any,
    producer: Producer,
    *,
    projection_ids: list[str],
    split_salt: str,
    split_by_group: dict[str, str] | None = None,
    selected_decisions: dict[str, list[str]] | None = None,
    reserve: bool = False,
) -> dict[str, Any]:
    """Freeze exact members through the authoritative reader, then optionally reserve."""
    from .p1_training_data import reserve_p1_snapshot

    if type(reserve) is not bool:
        raise BoundaryError("p1_preparation", "invalid_reserve_boolean")
    _selection_parameters(projection_ids, selected_decisions, split_salt, projections_required=True)
    if split_by_group is not None and (
        not isinstance(split_by_group, dict)
        or any(
            not isinstance(group, str)
            or not group
            or not isinstance(role, str)
            or role not in {"train", "dev", "test"}
            for group, role in split_by_group.items()
        )
    ):
        raise BoundaryError("p1_preparation", "invalid_split_by_group")
    _ledger(owner, store)
    manifest = publish_snapshot(
        store,
        projection_ids,
        producer,
        split_salt=split_salt,
        split_by_group=split_by_group,
        selected_decisions=selected_decisions,
    )
    _, value = load_snapshot(store, manifest.artifact_id)
    allocation = reserve_p1_snapshot(owner, store, manifest.artifact_id) if reserve else None
    return {
        "snapshot_id": manifest.artifact_id,
        "partition_N": value["partition_N"],
        "unique_N": value["unique_N"],
        "exact_duplicate_N": value["exact_duplicate_N"],
        "unknown_independence_groups": value["unknown_independence_groups"],
        "allocation": allocation,
        "purpose_reserved": reserve,
        "training_use_recorded": False,
        "research_admission": "not_evaluated",
    }
