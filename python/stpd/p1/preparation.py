"""Read-only data/transfer accounting for the existing P1 Store path.

This reports a compute read set separately from generic copy_artifact's complete
provenance closure. It publishes nothing and never opens a provider or uploads.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ArtifactStore

from .dataset import SnapshotReader, projection_payload_role
from .spec import RUN_SCHEMA, SNAPSHOT_SCHEMA
from .workload import verify_p1_run


def snapshot_statistics(store: ArtifactStore, snapshot_id: str) -> dict[str, Any]:
    reader = SnapshotReader(store, snapshot_id)
    selection = reader.selection
    metadata = {s["projection_id"]: s["lineage"] for s in selection["sources"]}
    roles: Counter[str] = Counter()
    difficulty: Counter[str] = Counter()
    scenes: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    catalogs: Counter[str] = Counter()
    partitions: dict[str, Counter[str]] = {}
    for reference, observation in reader.iter_points():
        lineage = metadata[reference["projection_id"]]
        character = str(lineage.get("character") or "unknown")
        ascension = lineage.get("ascension")
        roles[character] += 1
        difficulty["unknown" if ascension is None else str(ascension)] += 1
        scenes[str(observation["scene"]["family"])] += 1
        catalogs[str(len(observation["catalog"]))] += 1
        chosen = next(c for c in observation["catalog"] if c["choice_id"] == reference["chosen"])
        kinds[chosen["kind"]] += 1
        partitions.setdefault(reference["split"], Counter())[character] += 1
    return {
        "snapshot_id": snapshot_id,
        "unique_N": selection["unique_N"],
        "partition_N": selection["partition_N"],
        "observed_groups": selection["observed_group_count"],
        "groups_with_known_seed_root_ancestry": sum(
            g["N"] > 0 and g["independence_metadata_complete"] for g in selection["groups"]
        ),
        "unknown_independence_groups": selection["unknown_independence_groups"],
        "groups": selection["groups"],
        "N_by_character": dict(roles),
        "N_by_ascension": dict(difficulty),
        "N_by_scene_family": dict(scenes),
        "N_by_chosen_kind": dict(kinds),
        "N_by_catalog_size": dict(catalogs),
        "partition_N_by_character": {k: dict(v) for k, v in partitions.items()},
        "qualification": "descriptive_engineering_only_no_statistical_admission",
    }


def input_transfer_inventory(store: ArtifactStore, identity: str) -> dict[str, Any]:
    item = store.get_manifest(identity)
    schema = item.parameters.value().get("schema")
    compute = []
    if schema == RUN_SCHEMA:
        workload = verify_p1_run(store, identity, item.producer)
        snapshot = workload.snapshot
        reader = workload.reader
        compute.append((workload.training, "vocabulary"))
    elif schema == SNAPSHOT_SCHEMA:
        snapshot = item
        reader = SnapshotReader(store, identity)
    else:
        raise BoundaryError("p1_preparation", "P1_snapshot_or_run_required")
    selection = reader.selection
    compute.append((snapshot, "selection"))
    for source in selection["sources"]:
        projection = store.get_manifest(source["projection_id"])
        compute.append((projection, projection_payload_role(projection)))

    def rows(pairs):
        unique: dict[str, Any] = {}
        for manifest, role in pairs:
            payload = manifest.payload(role)
            entry = unique.setdefault(
                payload.sha256,
                {
                    "sha256": payload.sha256,
                    "bytes": payload.size,
                    "references": [],
                },
            )
            entry["references"].append({"artifact_id": manifest.artifact_id, "role": role})
        return [unique[key] for key in sorted(unique)]

    seen: dict[str, Any] = {}
    pending = [identity]
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        if len(seen) >= 512:
            raise BoundaryError("p1_preparation", "lineage_limit")
        manifest = store.get_manifest(key)
        seen[key] = manifest
        pending.extend(p.artifact_id for p in manifest.parents)
    read_set = rows(compute)
    closure = rows((m, p.role) for m in seen.values() for p in m.payloads)
    return {
        "identity": identity,
        "snapshot_id": snapshot.artifact_id,
        "compute_payloads": read_set,
        "compute_unique_payload_bytes": sum(p["bytes"] for p in read_set),
        "provenance_manifest_ids": sorted(seen),
        "copy_artifact_payloads": closure,
        "copy_artifact_unique_payload_bytes": sum(p["bytes"] for p in closure),
        "copy_behavior": "all_parent_payloads_streamed_via_one_temporary_payload_file",
        "worker_reads": "named_projection_payloads_selection_vocabulary_and_ancestor_manifests",
        "worker_archive_payload_reads": False,
        "placement": "existing_ArtifactStore_binding_not_embedded_local_paths",
        "upload_performed": False,
    }
