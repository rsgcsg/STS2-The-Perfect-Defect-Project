"""Content-selected N snapshots on the application's existing ArtifactStore.

Only selection references are persisted here. Original bundles and named P1
projections stay in the same Store owned by the recording application. Native
facts, a derived N label and research admission remain different claims.
"""

from __future__ import annotations

import copy
import io
import json
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.store import ArtifactStore
from stpd.fullrun.data import group_run_fingerprints, split_run_fingerprints

from .data import digest, json_bytes
from .input import INPUT_SPEC, LEGACY_INPUT_SPEC, project, validate_input_spec
from .spec import SNAPSHOT_SCHEMA, TARGET_SPEC

SOURCE_SCHEMA = "sts2.human-annotator/p1-native-session-bundle-1"
SEGMENTS_SCHEMA = "sts2.p1/native-segments-1"
TARGETS_SCHEMA = "sts2.p1/native-targets-1"
SUPPORTED_PROJECTIONS = {
    (SEGMENTS_SCHEMA, "native-selector-finalized-d-1"): "segments",
    (TARGETS_SCHEMA, "native-timeline-targets-1"): "targets",
}


def projection_payload_role(manifest: Manifest) -> str:
    info = manifest.parameters.value()
    pair = info.get("schema"), info.get("projection_id")
    if manifest.kind != "analysis" or pair not in SUPPORTED_PROJECTIONS:
        raise BoundaryError("p1_snapshot", "unsupported_named_projection")
    return SUPPORTED_PROJECTIONS[pair]


@dataclass(frozen=True)
class Projection:
    manifest: Manifest
    source: Manifest
    value: dict[str, Any]
    root: str
    lineage: dict[str, Any]


def _payload_json(store: ArtifactStore, manifest: Manifest, role: str) -> dict[str, Any]:
    # read_payload already verifies whole/chunk hashes and lengths. Only one
    # source is decoded at a time; no second raw-data inventory/materialization.
    value = json.loads(b"".join(store.read_payload(manifest.payload(role))))
    if not isinstance(value, dict):
        raise BoundaryError("p1_snapshot", "object_payload_required")
    return value


def load_projection(store: ArtifactStore, artifact_id: str) -> Projection:
    manifest = store.get_manifest(artifact_id)
    info = manifest.parameters.value()
    role = projection_payload_role(manifest)
    if (
        {p.role for p in manifest.parents} != {"source"}
        or not isinstance(info.get("tool_release_id"), str)
        or not info["tool_release_id"]
    ):
        raise BoundaryError("p1_snapshot", "projection_source_or_tool_binding_required")
    source = store.get_manifest(manifest.parent("source"))
    original = source.parameters.value()
    if source.kind != "evidence" or original.get("schema") != SOURCE_SCHEMA:
        raise BoundaryError("p1_snapshot", "native_original_source_required")
    value = _payload_json(store, manifest, role)
    if (
        value.get("schema") != info["schema"]
        or (value.get("profile"), value.get("revision")) != ("p1-global-r1", info.get("revision"))
        or info.get("profile") != "p1-global-r1"
        or info.get("revision") not in {INPUT_SPEC["revision"], LEGACY_INPUT_SPEC["revision"]}
        or value.get("source_content_id") != original.get("content_id")
    ):
        raise BoundaryError("p1_snapshot", "projection_identity_mismatch")
    branch = value["branch"]
    if (
        not isinstance(branch, dict)
        or set(branch) != {"session_id", "timeline_id", "run_id"}
        or any(not isinstance(v, str) or not v for v in branch.values())
    ):
        raise BoundaryError("p1_snapshot", "source_branch_required")
    if info.get("run_id") != branch["run_id"]:
        raise BoundaryError("p1_snapshot", "projection_run_binding_mismatch")
    lineage = original.get("lineage", {})
    if not isinstance(lineage, dict):
        raise BoundaryError("p1_snapshot", "invalid_source_lineage")
    # The observed branch is known even when birth/seed/SL ancestry is unknown.
    # Unknown fields remain unknown; they are reported, never filled as new games.
    root = lineage.get("root_run_id") or "/".join(
        branch[k] for k in ("session_id", "timeline_id", "run_id")
    )
    if not isinstance(root, str) or not root:
        raise BoundaryError("p1_snapshot", "invalid_source_root")
    return Projection(manifest, source, value, root, lineage)


def _points(projection: Projection) -> Iterator[tuple[str, int, dict[str, Any]]]:
    value = projection.value
    if value["schema"] == SEGMENTS_SCHEMA:
        for segment in value["segments"]:
            if segment["branch"] != value["branch"]:
                raise BoundaryError("p1_snapshot", "foreign_segment_branch")
            for index, point in enumerate(segment["points"]):
                yield segment["segment_id"], index, point
    else:
        for index, point in enumerate(value["points"]):
            if point.get("branch", value["branch"]) != value["branch"]:
                raise BoundaryError("p1_snapshot", "foreign_point_branch")
            yield "points", index, point


def _N_fact(point: dict[str, Any], input_projection: dict[str, Any]) -> str:
    observation, chosen = point["observation"], point["chosen"]
    if (
        not isinstance(observation, dict)
        or observation.get("schema") != "sts2.p1/observation-1"
        or observation.get("profile") != "p1-global-r1"
        or observation.get("revision") != input_projection["revision"]
        or observation.get("status") != "decision"
        or not isinstance(observation.get("envelope"), dict)
        or observation["envelope"].get("required_information_complete") is not True
    ):
        raise BoundaryError("p1_snapshot", "N_requires_complete_current_Input")
    catalog = observation["catalog"]
    if not isinstance(catalog, list) or not catalog:
        raise BoundaryError("p1_snapshot", "N_requires_full_catalog")
    if any(not isinstance(c, dict) for c in catalog):
        raise BoundaryError("p1_snapshot", "invalid_catalog_choice")
    ids = [c.get("choice_id") for c in catalog]
    if (
        any(not isinstance(key, str) or not key for key in ids)
        or len(set(ids)) != len(ids)
        or ids.count(chosen) != 1
        or any(c.get("mapped") is not True for c in catalog)
    ):
        raise BoundaryError("p1_snapshot", "chosen_not_unique_in_complete_catalog")
    # Bind the model-visible public state and label. Transport occurrence numbers
    # may differ between named projections; they do not create another N example.
    return digest(
        json_bytes(
            {
                "model_view": {k: v for k, v in observation.items() if k != "envelope"},
                "chosen": chosen,
            }
        )
    )


def inspect_projections(
    store: ArtifactStore,
    projection_ids: Sequence[str],
    *,
    selected_decisions: Mapping[str, Sequence[str]] | None = None,
    input_projection: dict[str, Any] | None = None,
    on_projection: Callable[[Projection], None] | None = None,
) -> dict[str, Any]:
    """Reproduce an exact selection; no implicit latest/union/admission rule."""
    if not projection_ids or len(set(projection_ids)) != len(projection_ids):
        raise BoundaryError("p1_snapshot", "explicit_unique_projections_required")
    spec = validate_input_spec(INPUT_SPEC if input_projection is None else input_projection)
    selected = dict(selected_decisions or {})
    if set(selected) - set(projection_ids):
        raise BoundaryError("p1_snapshot", "selection_has_foreign_projection")
    fingerprints: list[tuple[str, str]] = []
    sources = []
    points: dict[str, dict[str, Any]] = {}
    aliases: dict[str, list[dict[str, Any]]] = {}
    exclusions: Counter[str] = Counter()
    duplicates = 0
    for artifact_id in sorted(projection_ids):
        projection = load_projection(store, artifact_id)
        if projection.value["revision"] != spec["revision"]:
            raise BoundaryError("p1_snapshot", "projection_InputSpec_revision_mismatch")
        root_id, lineage = projection.root, projection.lineage
        fingerprints.append((root_id, "root:" + root_id))
        seed = lineage.get("seed")
        if seed is not None:
            if not isinstance(seed, str) or not seed:
                raise BoundaryError("p1_snapshot", "invalid_seed_lineage")
            fingerprints.append((root_id, "seed:" + seed))
        ancestors = lineage.get("ancestor_root_ids")
        if ancestors is None:
            ancestors = []  # Still unknown in the retained lineage/qualification report.
        if not isinstance(ancestors, list) or any(
            not isinstance(x, str) or not x for x in ancestors
        ):
            raise BoundaryError("p1_snapshot", "invalid_restore_ancestry")
        for ancestor in ancestors:
            fingerprints.append((root_id, "root:" + ancestor))
        # Identical source content is one original even after a tool repack/import.
        fingerprints.append((root_id, "content:" + projection.value["source_content_id"]))
        wanted = None if artifact_id not in selected else set(selected[artifact_id])
        if wanted is not None and len(wanted) != len(selected[artifact_id]):
            raise BoundaryError("p1_snapshot", "duplicate_selection_key")
        found: set[str] = set()
        source_N = source_selected = 0
        for segment, index, point in _points(projection):
            if not isinstance(point, dict):
                raise BoundaryError("p1_snapshot", "invalid_source_point")
            mask = point.get("mask")
            if not isinstance(mask, dict) or type(mask.get("N")) is not bool:
                raise BoundaryError("p1_snapshot", "invalid_N_mask")
            if not mask["N"]:
                exclusions["N_false_not_a_model_input"] += 1
                continue
            source_N += 1
            decision, event = point.get("source_decision_id"), point.get("source_event_id")
            if (
                not isinstance(decision, str)
                or not decision
                or not isinstance(event, str)
                or not event
            ):
                raise BoundaryError("p1_snapshot", "exact_native_label_origin_required")
            fingerprints.append((root_id, "decision:" + decision))
            if wanted is not None and decision not in wanted:
                exclusions["explicit_selection"] += 1
                continue
            found.add(decision)
            fact = _N_fact(point, spec)
            observation = point["observation"]
            # Exercise the real, complete InputSpec; do not fit vocabulary here.
            project(observation, input_projection=spec)
            reference = {
                "projection_id": artifact_id,
                "source_id": projection.source.artifact_id,
                "segment_id": segment,
                "point_index": index,
                "source_event_id": event,
                "source_decision_id": decision,
                "root_run_id": root_id,
                "observation_sha256": digest(json_bytes(observation)),
                "N_fact_sha256": fact,
                "chosen": point["chosen"],
            }
            source_selected += 1
            if decision in points:
                if points[decision]["N_fact_sha256"] != fact:
                    raise BoundaryError(
                        "p1_snapshot", "overlapping_projection_N_conflict_select_one"
                    )
                duplicates += 1
                aliases[decision].append(reference)
            else:
                points[decision] = reference
                aliases[decision] = []
        if wanted is not None and found != wanted:
            raise BoundaryError("p1_snapshot", "selected_N_not_present")
        sources.append(
            {
                "projection_id": artifact_id,
                "source_id": projection.source.artifact_id,
                "source_content_id": projection.value["source_content_id"],
                projection_payload_role(projection.manifest) + "_sha256":
                    projection.manifest.payload(projection_payload_role(projection.manifest)).sha256,
                "projection": projection.manifest.parameters.value(),
                "branch": projection.value["branch"],
                "root_run_id": root_id,
                "lineage": lineage,
                "available_N": source_N,
                "selected_N_before_dedup": source_selected,
            }
        )
        if on_projection is not None:
            on_projection(projection)
        del projection
    components = group_run_fingerprints(fingerprints)
    group_ids = {
        root_id: digest(json_bytes(component)) for component in components for root_id in component
    }
    for decision, point in points.items():
        point["group_id"] = group_ids[point["root_run_id"]]
        point["point_id"] = digest(
            json_bytes({"native_decision": decision, "N_fact_sha256": point["N_fact_sha256"]})
        )
        point["aliases"] = aliases[decision]
    groups = []
    for component in components:
        group_id = group_ids[component[0]]
        members = [s for s in sources if s["root_run_id"] in component]
        groups.append(
            {
                "group_id": group_id,
                "keys": sorted({key for run, key in fingerprints if run in component}),
                "root_run_ids": sorted({s["root_run_id"] for s in members}),
                "N": sum(p["group_id"] == group_id for p in points.values()),
                "independence_metadata_complete": all(
                    s["lineage"].get("seed") is not None
                    and s["lineage"].get("root_run_id") is not None
                    and s["lineage"].get("ancestor_root_ids") is not None
                    for s in members
                ),
            }
        )
    return {
        "schema": SNAPSHOT_SCHEMA,
        "sources": sources,
        "points": sorted(points.values(), key=lambda p: p["point_id"]),
        "groups": groups,
        "unique_N": len(points),
        "exact_duplicate_N": duplicates,
        "exclusions": dict(exclusions),
        "selection": {k: sorted(v) for k, v in sorted(selected.items())},
        "observed_group_count": len(groups),
        "independent_group_count": sum(g["N"] > 0 for g in groups),
        "unknown_independence_groups": sum(
            g["N"] > 0 and not g["independence_metadata_complete"] for g in groups
        ),
        "input_projection": spec,
        "target_spec": TARGET_SPEC,
        "use_scope": "engineering_only_not_research_admission",
    }


def merge_projection_inspections(
    inspections: Sequence[dict[str, Any]],
    *,
    selected_decisions: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Combine verified per-projection indexes without decoding immutable payloads.

    This is a disposable preparation index. Publication and training readers still
    reproduce the selection from original Store parents, independently of a cache.
    """
    if not inspections:
        raise BoundaryError("p1_snapshot", "explicit_unique_projections_required")
    spec = inspections[0]["input_projection"]
    sources: list[dict[str, Any]] = []
    fingerprints: list[tuple[str, str]] = []
    selected = dict(selected_decisions or {})
    points: dict[str, dict[str, Any]] = {}
    exclusions: Counter[str] = Counter()
    duplicates = 0
    ordered = sorted(inspections, key=lambda x: x["sources"][0]["projection_id"])
    projection_ids = [x["sources"][0]["projection_id"] for x in ordered]
    if len(set(projection_ids)) != len(projection_ids) or set(selected) - set(projection_ids):
        raise BoundaryError("p1_snapshot", "explicit_unique_projections_required")
    for value in ordered:
        if len(value["sources"]) != 1 or value["input_projection"] != spec or value["selection"]:
            raise BoundaryError("p1_snapshot", "complete_single_projection_inspection_required")
        source = dict(value["sources"][0])
        identity = source["projection_id"]
        wanted = None if identity not in selected else set(selected[identity])
        if wanted is not None and len(wanted) != len(selected[identity]):
            raise BoundaryError("p1_snapshot", "duplicate_selection_key")
        available = {p["source_decision_id"] for p in value["points"]}
        if wanted is not None and not wanted <= available:
            raise BoundaryError("p1_snapshot", "selected_N_not_present")
        for group in value["groups"]:
            fingerprints.extend(
                (root, key) for root in group["root_run_ids"] for key in group["keys"]
            )
        exclusions.update(value["exclusions"])
        count = 0
        for original in value["points"]:
            refs = [
                {k: v for k, v in original.items() if k not in {"aliases", "group_id", "point_id"}},
                *original["aliases"],
            ]
            decision = original["source_decision_id"]
            if wanted is not None and decision not in wanted:
                exclusions["explicit_selection"] += len(refs)
                continue
            count += len(refs)
            for ref in refs:
                if decision in points:
                    if points[decision]["N_fact_sha256"] != ref["N_fact_sha256"]:
                        raise BoundaryError(
                            "p1_snapshot", "overlapping_projection_N_conflict_select_one"
                        )
                    points[decision]["aliases"].append(dict(ref))
                    duplicates += 1
                else:
                    points[decision] = {**ref, "aliases": []}
        source["selected_N_before_dedup"] = count
        sources.append(source)
    components = group_run_fingerprints(fingerprints)
    group_ids = {root: digest(json_bytes(group)) for group in components for root in group}
    for decision, point in points.items():
        point["group_id"] = group_ids[point["root_run_id"]]
        point["point_id"] = digest(
            json_bytes({"native_decision": decision, "N_fact_sha256": point["N_fact_sha256"]})
        )
    groups = []
    for component in components:
        identity = group_ids[component[0]]
        members = [s for s in sources if s["root_run_id"] in component]
        groups.append(
            {
                "group_id": identity,
                "keys": sorted({key for root, key in fingerprints if root in component}),
                "root_run_ids": sorted({s["root_run_id"] for s in members}),
                "N": sum(p["group_id"] == identity for p in points.values()),
                "independence_metadata_complete": all(
                    s["lineage"].get("seed") is not None
                    and s["lineage"].get("root_run_id") is not None
                    and s["lineage"].get("ancestor_root_ids") is not None
                    for s in members
                ),
            }
        )
    return {
        "schema": SNAPSHOT_SCHEMA,
        "sources": sources,
        "points": sorted(points.values(), key=lambda p: p["point_id"]),
        "groups": groups,
        "unique_N": len(points),
        "exact_duplicate_N": duplicates,
        "exclusions": dict(exclusions),
        "selection": {k: sorted(v) for k, v in sorted(selected.items())},
        "observed_group_count": len(groups),
        "independent_group_count": sum(g["N"] > 0 for g in groups),
        "unknown_independence_groups": sum(
            g["N"] > 0 and not g["independence_metadata_complete"] for g in groups
        ),
        "input_projection": spec,
        "target_spec": TARGET_SPEC,
        "use_scope": "engineering_only_not_research_admission",
    }


def publish_snapshot(
    store: ArtifactStore,
    projection_ids: Sequence[str],
    producer: Producer,
    *,
    split_salt: str,
    split_by_group: Mapping[str, str] | None = None,
    selected_decisions: Mapping[str, Sequence[str]] | None = None,
) -> Manifest:
    value = inspect_projections(store, projection_ids, selected_decisions=selected_decisions)
    groups = {g["group_id"] for g in value["groups"] if g["N"] > 0}
    if not isinstance(split_salt, str) or not split_salt:
        raise BoundaryError("p1_snapshot", "explicit_split_identity_required")
    if split_by_group is None and value["unknown_independence_groups"]:
        # Observed branches do not establish seed/restore independence. Keep a
        # new default selection readable, but require explicit engineering use
        # instead of silently assigning unknown relatives to held-out roles.
        splits = dict.fromkeys(groups, "unassigned")
        split_method = "unknown_lineage_unassigned"
    elif split_by_group is None:
        try:
            splits = split_run_fingerprints(
                ((g, g) for g in sorted(groups)), int(digest(split_salt.encode())[:16], 16)
            ).value()
            split_method = "existing_whole_run_component_allocation"
        except BoundaryError as error:
            if error.code != "insufficient_independent_run_components":
                raise
            splits = dict.fromkeys(groups, "unassigned")
            split_method = "insufficient_components_unassigned"
    else:
        splits = dict(split_by_group)
        if set(splits) != groups or any(s not in {"train", "dev", "test"} for s in splits.values()):
            raise BoundaryError("p1_snapshot", "exact_group_partition_required")
        split_method = "explicit_engineering_groups_not_statistical_qualification"
    for point in value["points"]:
        point["split"] = splits[point["group_id"]]
    value.update(split_salt=split_salt, split_method=split_method, split_by_group=splits)
    value["partition_N"] = dict(Counter(point["split"] for point in value["points"]))
    raw = json_bytes(value)
    payload = store.put_payload("selection", io.BytesIO(raw), "application/json")
    manifest = Manifest(
        "dataset",
        producer,
        tuple(Parent(f"projection_{i:04d}", key) for i, key in enumerate(sorted(projection_ids))),
        (payload,),
        FrozenObject.of(
            {
                "schema": SNAPSHOT_SCHEMA,
                "selection_sha256": digest(raw),
                "purpose": "engineering",
                "research_admission": "not_evaluated",
                "input_projection": INPUT_SPEC,
                "target_spec": TARGET_SPEC,
                "partition_N": value["partition_N"],
                "group_count": len(groups),
            }
        ),
    )
    store.publish(manifest)
    return manifest


def load_snapshot(store: ArtifactStore, snapshot_id: str, *,
                  inspection_cache: Any = None) -> tuple[Manifest, dict[str, Any]]:
    """Verify once per explicit read session; never accept an external trust tuple.

    The existing private preparation cache can supply content/code-bound source
    inspections. It still hashes each source through Store; selection, aliases,
    groups and splits are reproduced and checked by this owning reader.
    """
    from spireagent.storage.read_session import VerifiedReadSession

    if isinstance(store, VerifiedReadSession):
        manifest, value = store.memoize(
            "p1.snapshot", snapshot_id,
            lambda: _load_snapshot(store, snapshot_id, inspection_cache=inspection_cache))
        return manifest, copy.deepcopy(value)
    return _load_snapshot(store, snapshot_id, inspection_cache=inspection_cache)


def _load_snapshot(store: ArtifactStore, snapshot_id: str, *,
                   inspection_cache: Any = None) -> tuple[Manifest, dict[str, Any]]:
    manifest = store.get_manifest(snapshot_id)
    info = manifest.parameters.value()
    if manifest.kind != "dataset" or info.get("schema") != SNAPSHOT_SCHEMA:
        raise BoundaryError("p1_snapshot", "snapshot_schema_required")
    value = _payload_json(store, manifest, "selection")
    spec = value.get("input_projection")
    try:
        spec = validate_input_spec(spec)
    except ValueError as error:
        raise BoundaryError("p1_snapshot", "snapshot_InputSpec_unsupported") from error
    if (
        digest(json_bytes(value)) != info.get("selection_sha256")
        or value.get("schema") != SNAPSHOT_SCHEMA
        or info.get("input_projection") != spec
        or value.get("target_spec") != TARGET_SPEC
        or {p.artifact_id for p in manifest.parents}
        != {s["projection_id"] for s in value["sources"]}
    ):
        raise BoundaryError("p1_snapshot", "snapshot_selection_binding_mismatch")
    if len({p["source_decision_id"] for p in value["points"]}) != len(value["points"]):
        raise BoundaryError("p1_snapshot", "snapshot_duplicate_native_N")
    if value["partition_N"] != dict(Counter(p["split"] for p in value["points"])):
        raise BoundaryError("p1_snapshot", "snapshot_partition_count_mismatch")
    # Match the existing decision-selection/union contract: re-read immutable
    # parents and reproduce the selected facts, aliases and lineage groups. A
    # checksum alone cannot make a forged group/split allocation trustworthy.
    if inspection_cache is None:
        expected = inspect_projections(
            store,
            [s["projection_id"] for s in value["sources"]],
            selected_decisions=value["selection"],
            input_projection=spec,
        )
    else:
        inspections = [inspection_cache.inspect(store, s["projection_id"], spec)[0]
                       for s in value["sources"]]
        expected = merge_projection_inspections(
            inspections, selected_decisions=value["selection"])
    reproduced = {
        k: v
        for k, v in value.items()
        if k not in {"split_salt", "split_method", "split_by_group", "partition_N"}
    }
    reproduced["points"] = [{k: v for k, v in p.items() if k != "split"} for p in value["points"]]
    if reproduced != expected:
        raise BoundaryError("p1_snapshot", "snapshot_parent_reprojection_mismatch")
    if any(p["split"] != value["split_by_group"].get(p["group_id"]) for p in value["points"]):
        raise BoundaryError("p1_snapshot", "snapshot_group_partition_mismatch")
    return manifest, value


class SnapshotReader:
    """One operation's verified selection, bound to its actual Store instance.

    No external verified tuple or persisted trust token is accepted. Reusing this
    reader avoids reproducing the snapshot twice when a consumer needs both its
    metadata and points. Each point iteration still reads/hashes source payloads.
    """

    def __init__(self, store: ArtifactStore, snapshot_id: str) -> None:
        self._store = store
        self.manifest, self._selection = load_snapshot(store, snapshot_id)

    @property
    def selection(self) -> dict[str, Any]:
        return copy.deepcopy(self._selection)

    def iter_points(self) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        return _iter_selected_points(self._store, self._selection)

    def iter_supervised_points(self) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        return _iter_selected_points(self._store, self._selection, run_facts=True)


def iter_snapshot_points(
    store: ArtifactStore, snapshot_id: str
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Read and verify one immutable projection at a time, by its exact Store ID."""
    yield from SnapshotReader(store, snapshot_id).iter_points()


def _iter_selected_points(
    store: ArtifactStore, snapshot: dict[str, Any], *, run_facts: bool = False,
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    selected: dict[str, dict[tuple[str, int], dict[str, Any]]] = {}
    for ref in snapshot["points"]:
        selected.setdefault(ref["projection_id"], {})[(ref["segment_id"], ref["point_index"])] = ref
    for artifact_id in sorted(selected):
        projection = load_projection(store, artifact_id)
        facts = None
        if run_facts:
            from .cohort import projection_facts

            facts = projection_facts(projection)
        found = set()
        for segment, index, point in _points(projection):
            key = segment, index
            ref = selected[artifact_id].get(key)
            if ref is None:
                continue
            if (
                point.get("mask", {}).get("N") is not True
                or point.get("source_decision_id") != ref["source_decision_id"]
                or point.get("source_event_id") != ref["source_event_id"]
                or digest(json_bytes(point["observation"])) != ref["observation_sha256"]
                or _N_fact(point, snapshot["input_projection"]) != ref["N_fact_sha256"]
                or ref["chosen"] != point["chosen"]
                or ref["source_id"] != projection.source.artifact_id
                or ref["split"] != snapshot["split_by_group"][ref["group_id"]]
            ):
                raise BoundaryError("p1_snapshot", "source_N_selection_drift")
            found.add(key)
            reference = copy.deepcopy(ref)
            if facts is not None:
                reference["auxiliary_run_facts"] = facts
            yield reference, point["observation"]
        if found != set(selected[artifact_id]):
            raise BoundaryError("p1_snapshot", "selected_point_missing")
