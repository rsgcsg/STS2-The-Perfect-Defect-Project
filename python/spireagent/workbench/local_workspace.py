"""Read-only local research artifact inventory for Workbench surfaces.

This module projects manifest metadata from the configured ArtifactStore. It never
opens payload bytes, creates artifacts, or talks to Hub/cloud APIs. The supplied
store should be the caller's local ArtifactStore instance.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast

from spireagent.artifact_contracts import KINDS, Manifest
from spireagent.json_boundary import BoundaryError, digest
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import Registry, SQLiteRegistry
from spireagent.storage.store import ArtifactStore, ManifestArtifactStore
from spireagent.workbench.dashboard import _safe_value
from spireagent.workbench.developer import LocalResearchWorkspaceConfig
from spireagent.workbench.memory_recipe import recorded_memory_recipe

INVENTORY_SCHEMA = "stpd/local-workspace-inventory-v1"
ARTIFACT_SCHEMA = "stpd/local-workspace-artifact-v1"
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
CATEGORIES = frozenset({"recordings", "datasets", "models", "reports", "all"})
CATEGORY_KINDS = {"datasets": "dataset", "models": "model", "reports": "offline_evaluation"}
RECORDING_SCHEMAS = frozenset({"stpd/local-verified-bundle-v1", "stpd/received-bundle-v1"})
DATA_FACT_MANIFEST_LIMIT = 10000
DATA_FACT_LINEAGE_LIMIT = 512
DATA_FACT_REFERENCE_LIMIT = 500


def _manifest_summary(
    manifest: Manifest, *, related_completion_status: str | None = None
) -> dict[str, Any]:
    parameters = _parameters(manifest)
    status = parameters.get("completion_status", parameters.get("state", parameters.get("status")))
    if not isinstance(status, str) or status not in {
        "completed",
        "failed",
        "cancelled",
        "interrupted_unknown",
        "running",
    }:
        status = "completed" if parameters.get("completed") is True else "unknown"
    if status == "unknown" and related_completion_status == "completed":
        status = "completed"
    return {
        "artifact_id": manifest.artifact_id,
        "kind": manifest.kind,
        "schema": parameters.get("schema") if isinstance(parameters.get("schema"), str) else None,
        "purpose": parameters.get("purpose")
        if isinstance(parameters.get("purpose"), str)
        else None,
        "parents": [
            {"role": parent.role, "artifact_id": parent.artifact_id}
            for parent in sorted(manifest.parents)
        ],
        "producer_source_revision": manifest.producer.source_revision,
        "producer_completion_status": status,
    }


def _recorded_boundary_count(parameters: dict[str, Any], field: str) -> dict[str, Any]:
    value = parameters.get(field)
    if type(value) is int and value >= 0:
        return {"known": True, "value": value}
    return {"known": False, "value": None}


def _data_facts(
    store: ArtifactStore,
    artifact_id: str,
    curation_owner: Any | None,
) -> dict[str, Any]:
    """Project bounded manifest ancestry and read-only ledger references.

    This path deliberately uses manifests and indexed run/use identities only. It
    never calls ``read_payload`` or the LocalCurationOwner ledger writer API.
    """
    manifests: dict[str, Manifest] = {}
    inventory_truncated = False
    try:
        identities = store.manifest_ids()
    except (BoundaryError, OSError, ValueError, sqlite3.DatabaseError):
        identities = ()
        inventory_truncated = True
    if len(identities) > DATA_FACT_MANIFEST_LIMIT:
        identities = identities[:DATA_FACT_MANIFEST_LIMIT]
        inventory_truncated = True
    for identity in identities:
        try:
            manifests[identity] = store.get_manifest(identity)
        except (BoundaryError, OSError, ValueError):
            inventory_truncated = True

    try:
        selected = manifests.get(artifact_id) or store.get_manifest(artifact_id)
        manifests[artifact_id] = selected
    except (BoundaryError, OSError, ValueError):
        return {
            "schema": "stpd/local-data-facts-v1",
            "status": "unavailable",
            "reason": "artifact_manifest_unavailable",
        }

    children: dict[str, list[str]] = {}
    for child_id, manifest in manifests.items():
        for parent in manifest.parents:
            children.setdefault(parent.artifact_id, []).append(child_id)

    descendants: list[str] = []
    pending = [artifact_id]
    seen = set()
    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        descendants.append(current)
        if len(seen) >= DATA_FACT_LINEAGE_LIMIT:
            if any(item not in seen for item in pending) or any(
                child not in seen for child in children.get(current, ())
            ):
                inventory_truncated = True
            break
        pending.extend(children.get(current, ()))

    lineage: dict[str, Manifest] = {}
    pending = [artifact_id]
    while pending and len(lineage) < DATA_FACT_LINEAGE_LIMIT:
        current = pending.pop(0)
        if current in lineage:
            continue
        lineage_manifest = manifests.get(current)
        if lineage_manifest is None:
            try:
                lineage_manifest = store.get_manifest(current)
            except (BoundaryError, OSError, ValueError):
                inventory_truncated = True
                continue
        lineage[current] = lineage_manifest
        pending.extend(
            parent.artifact_id
            for parent in lineage_manifest.parents
            if parent.artifact_id not in lineage and parent.artifact_id not in pending
        )
    if any(item not in lineage for item in pending):
        inventory_truncated = True

    data_manifests = [manifest for manifest in lineage.values() if manifest.kind == "dataset"]
    bundles = [
        manifest
        for manifest in lineage.values()
        if manifest.kind == "evidence" and _parameters(manifest).get("schema") in RECORDING_SCHEMAS
    ]
    allocations = [
        manifest
        for manifest in lineage.values()
        if _parameters(manifest).get("schema") == "stpd/decision-allocation-v1"
    ]
    models = [
        candidate
        for identity in descendants[1:]
        if (candidate := manifests.get(identity)) is not None and candidate.kind == "model"
    ]
    evaluations = [
        candidate
        for identity in descendants[1:]
        if (candidate := manifests.get(identity)) is not None
        and candidate.kind == "offline_evaluation"
    ]
    run_results = [
        candidate
        for identity in descendants[1:]
        if (candidate := manifests.get(identity)) is not None and candidate.kind == "run_result"
    ]
    completed_models = {
        parent.artifact_id
        for result in run_results
        if _parameters(result).get("state") == "completed"
        for parent in result.parents
        if parent.role == "model"
    }
    completed_evaluations = {
        parent.artifact_id
        for result in run_results
        if _parameters(result).get("state") == "completed"
        for parent in result.parents
        if parent.role == "offline_evaluation"
    }

    # Prefer an explicitly curated wrapper, then a canonical source dataset.
    dataset: Manifest | None
    if selected.kind == "dataset":
        dataset = selected
    else:
        dataset = next(
            (
                item
                for item in data_manifests
                if _parameters(item).get("schema") == "stpd/curated-decision-dataset-v1"
            ),
            next(
                (
                    item
                    for item in data_manifests
                    if _parameters(item).get("schema")
                    in {
                        "stpd/fullrun-dataset-v1",
                        "stpd/decision-dataset-v1",
                        "stpd/decision-union-v1",
                    }
                ),
                None,
            ),
        )
    dataset_parameters = _parameters(dataset) if dataset is not None else {}
    schema_versions = [
        {
            "role": "curated"
            if _parameters(item).get("schema") == "stpd/curated-decision-dataset-v1"
            else "source",
            "schema": _parameters(item).get("schema"),
            "producer_source_revision": item.producer.source_revision,
            "artifact_id": item.artifact_id,
        }
        for item in sorted(data_manifests, key=lambda value: value.artifact_id)
    ]
    selected_records = dataset_parameters.get("records")
    selected_count = (
        selected_records if type(selected_records) is int and selected_records >= 0 else None
    )
    excluded_count = None
    exclusions = dataset_parameters.get("exclusions")
    if type(exclusions) is int and exclusions >= 0:
        excluded_count = exclusions
    elif isinstance(exclusions, list):
        excluded_count = len(exclusions)

    source_ids = tuple(sorted(item.artifact_id for item in bundles))
    known_runs: tuple[str, ...] = ()
    source_index_complete = False
    source_index_status = "missing_or_unverified"
    uses: list[dict[str, str]] = []
    use_truncated = False
    ledger_status = "not_available"
    if curation_owner is not None:
        path = getattr(curation_owner, "path", None)
        owner_store = getattr(curation_owner, "store_dir", None)
        actual_store = getattr(getattr(store, "blobs", None), "root", None)
        if (
            isinstance(path, Path)
            and isinstance(owner_store, Path)
            and actual_store is not None
            and owner_store.resolve() == actual_store.resolve()
            and not path.is_symlink()
            and path.is_file()
        ):
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    db.execute("PRAGMA query_only=ON")
                    tables = {
                        row[0]
                        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    }
                    required = {
                        "local_curation_identity",
                        "curation_sources",
                        "curation_exact_source_index",
                        "curation_source_runs",
                        "curation_uses",
                        "curation_source_uses",
                    }
                    owner_identity = getattr(curation_owner, "identity", None)
                    identity_row = (
                        db.execute(
                            "SELECT workspace,ledger,store,store_path FROM local_curation_identity"
                        ).fetchone()
                        if "local_curation_identity" in tables
                        else None
                    )
                    identity_valid = (
                        isinstance(owner_identity, tuple)
                        and identity_row == owner_identity
                        and db.execute("SELECT count(*) FROM local_curation_identity").fetchone()[0]
                        == 1
                    )
                    if required <= tables and identity_valid:
                        run_rows: set[str] = set()
                        indexed_run_sources: set[str] = set()
                        source_rows: set[str] = set()
                        exact_index_rows: set[str] = set()
                        for start in range(0, len(source_ids), 400):
                            batch = source_ids[start : start + 400]
                            if not batch:
                                continue
                            marks = ",".join("?" for _ in batch)
                            source_rows.update(
                                row[0]
                                for row in db.execute(
                                    "SELECT id FROM curation_sources "
                                    f"WHERE complete=1 AND id IN ({marks})",
                                    batch,
                                )
                            )
                            indexed = {
                                row[0]
                                for row in db.execute(
                                    "SELECT source FROM curation_exact_source_index "
                                    f"WHERE source IN ({marks})",
                                    batch,
                                )
                            }
                            exact_index_rows.update(indexed)
                            indexed_source_runs = list(
                                db.execute(
                                    "SELECT source,run FROM curation_source_runs "
                                    f"WHERE source IN ({marks})",
                                    batch,
                                )
                            )
                            run_rows.update(row[1] for row in indexed_source_runs)
                            indexed_run_sources.update(row[0] for row in indexed_source_runs)
                        source_index_complete = bool(source_ids) and not inventory_truncated and (
                            len(source_rows) == len(source_ids)
                            and len(exact_index_rows) == len(source_ids)
                            and len(indexed_run_sources) == len(source_ids)
                        )
                        known_runs = tuple(sorted(run_rows))
                        source_index_status = (
                            "complete" if source_index_complete else "stale_or_missing"
                        )
                        ref_ids = tuple(sorted(set(source_ids) | run_rows))
                        for start in range(0, len(ref_ids), 400):
                            batch = ref_ids[start : start + 400]
                            if not batch:
                                continue
                            marks = ",".join("?" for _ in batch)
                            for row in db.execute(
                                "SELECT run,kind,reference FROM curation_uses "
                                f"WHERE run IN ({marks}) "
                                "ORDER BY kind,reference,run LIMIT ?",
                                (*batch, DATA_FACT_REFERENCE_LIMIT + 1),
                            ):
                                uses.append(
                                    {
                                        "scope": "qualified_run",
                                        "identity": row[0],
                                        "kind": row[1],
                                        "reference": row[2],
                                    }
                                )
                                if len(uses) > DATA_FACT_REFERENCE_LIMIT:
                                    use_truncated = True
                                    uses = uses[:DATA_FACT_REFERENCE_LIMIT]
                                    break
                            if not use_truncated:
                                for row in db.execute(
                                    "SELECT source,kind,reference FROM curation_source_uses "
                                    f"WHERE source IN ({marks}) "
                                    "ORDER BY kind,reference,source LIMIT ?",
                                    (*batch, DATA_FACT_REFERENCE_LIMIT + 1),
                                ):
                                    uses.append(
                                        {
                                            "scope": "source",
                                            "identity": row[0],
                                            "kind": row[1],
                                            "reference": row[2],
                                        }
                                    )
                                    if len(uses) > DATA_FACT_REFERENCE_LIMIT:
                                        use_truncated = True
                                        uses = uses[:DATA_FACT_REFERENCE_LIMIT]
                                        break
                        ledger_status = "available"
                    else:
                        ledger_status = "unavailable"
            except (OSError, sqlite3.DatabaseError, ValueError):
                ledger_status = "unavailable"

    sessions = sorted({run.rpartition("/")[0] for run in known_runs if "/" in run})
    native_starts = [
        _recorded_boundary_count(_parameters(item), "native_starts") for item in bundles
    ]
    native_ends = [_recorded_boundary_count(_parameters(item), "native_ends") for item in bundles]
    start_known = all(item["known"] for item in native_starts) and bool(native_starts)
    end_known = all(item["known"] for item in native_ends) and bool(native_ends)
    def operation_id_for(manifest: Manifest) -> str | None:
        value = _parameters(manifest).get("operation_id")
        if isinstance(value, str) and value:
            return value
        for parent in manifest.parents:
            if parent.role != "run":
                continue
            run = manifests.get(parent.artifact_id)
            if run is None:
                try:
                    run = store.get_manifest(parent.artifact_id)
                except (BoundaryError, OSError, ValueError):
                    return None
            value = _parameters(run).get("operation_id")
            if isinstance(value, str) and value:
                return value
        return None

    exact_use_identities = set(source_ids) | set(known_runs)

    def has_exact_operation_uses(manifests_to_check: list[Manifest], kind: str) -> bool:
        if not manifests_to_check or not exact_use_identities or use_truncated:
            return False
        for manifest in manifests_to_check:
            operation_id = operation_id_for(manifest)
            if operation_id is None:
                return False
            matched_identities = {
                item["identity"]
                for item in uses
                if item["kind"] == kind and item["reference"] == operation_id
            }
            if not exact_use_identities <= matched_identities:
                return False
        return True

    has_exact_training_use = has_exact_operation_uses(models, "training")
    has_exact_evaluation_use = has_exact_operation_uses(evaluations, "evaluation")
    known_run_result_models = {
        parent.artifact_id
        for result in run_results
        for parent in result.parents
        if parent.role == "model"
    }
    training_history_unbound = bool(models or run_results) and (
        not has_exact_training_use
        or not known_run_result_models <= {item.artifact_id for item in models}
    )
    evaluation_history_unbound = bool(evaluations) and not has_exact_evaluation_use
    ledger_incomplete = (
        ledger_status != "available"
        or not source_index_complete
        or inventory_truncated
        or use_truncated
        or training_history_unbound
        or evaluation_history_unbound
    )
    allocation_roles = []
    for item in allocations:
        counts = _parameters(item).get("counts")
        if isinstance(counts, dict):
            allocation_roles.append(
                {
                    "artifact_id": item.artifact_id,
                    "purpose": _parameters(item).get("purpose"),
                    "train_count": counts.get("train")
                    if type(counts.get("train")) is int
                    else None,
                    "dev_count": counts.get("dev") if type(counts.get("dev")) is int else None,
                    "membership_detail": "not_read_from_payload",
                }
            )

    return {
        "schema": "stpd/local-data-facts-v1",
        "status": "partial" if inventory_truncated or ledger_incomplete else "available",
        "dataset": (
            {
                "artifact_id": dataset.artifact_id,
                "logical_id": dataset_parameters.get("logical_id"),
                "schema": dataset_parameters.get("schema"),
                "producer_source_revision": dataset.producer.source_revision,
                "schema_versions": schema_versions,
                "purpose": dataset_parameters.get("purpose"),
                "samples": {
                    "selected": selected_count,
                    "excluded": excluded_count,
                    "excluded_known": excluded_count is not None,
                },
            }
            if dataset is not None
            else None
        ),
        "recording": {
            "bundle_count": len(bundles),
            "session_count": len(sessions) if source_index_complete else None,
            "qualified_run_occurrence_count": len(known_runs) if source_index_complete else None,
            "native_starts": {
                "known": start_known,
                "value": sum(item["value"] for item in native_starts) if start_known else None,
            },
            "native_ends": {
                "known": end_known,
                "value": sum(item["value"] for item in native_ends) if end_known else None,
            },
            "physical_game_independence": "unresolved",
        },
        "purpose_and_allocation": {
            "purpose": dataset_parameters.get("purpose"),
            "allocation_roles": allocation_roles,
        },
        "user_declaration": {"status": "not_durably_registered", "source": None, "time": None},
        "descendants": {
            "models": [
                _manifest_summary(
                    item,
                    related_completion_status=(
                        "completed" if item.artifact_id in completed_models else None
                    ),
                )
                for item in sorted(models, key=lambda value: value.artifact_id)[:100]
            ],
            "evaluations": [
                _manifest_summary(
                    item,
                    related_completion_status=(
                        "completed" if item.artifact_id in completed_evaluations else None
                    ),
                )
                for item in sorted(evaluations, key=lambda value: value.artifact_id)[:100]
            ],
            "run_results": [
                {
                    "artifact_id": item.artifact_id,
                    "state": (
                        _parameters(item).get("state")
                        if isinstance(_parameters(item).get("state"), str)
                        and _parameters(item).get("state")
                        in {"completed", "failed", "cancelled", "interrupted_unknown", "running"}
                        else "unknown"
                    ),
                    "parents": [
                        {"role": parent.role, "artifact_id": parent.artifact_id}
                        for parent in sorted(item.parents)
                    ],
                }
                for item in sorted(run_results, key=lambda value: value.artifact_id)[:100]
            ],
            "truncated": inventory_truncated
            or len(models) > 100
            or len(evaluations) > 100
            or len(run_results) > 100,
        },
        "ledger": {
            "status": ledger_status,
            "coverage": "incomplete" if ledger_incomplete else "current_records_only",
            "label": (
                "历史使用记录不完整"
                if ledger_incomplete
                else "已记录的使用"
                if uses
                else "当前没有用途引用记录；不代表从未使用"
            ),
            "source_index_status": source_index_status,
            "uses": uses,
            "uses_truncated": use_truncated,
            "historical_manual_exposure": "unknown",
        },
        "eligibility": {
            "engineering_training": "authorized_training_purpose_only",
            "diagnostic_dev": "diagnostic_only",
            "clean_dev_test_claim": False,
            "gold_claim": False,
        },
        "lineage": {
            "artifact": _manifest_summary(selected),
            "datasets": [
                _manifest_summary(item)
                for item in sorted(data_manifests, key=lambda value: value.artifact_id)[:100]
            ],
            "recording_bundles": [
                _manifest_summary(item)
                for item in sorted(bundles, key=lambda value: value.artifact_id)[:100]
            ],
            "qualified_run_occurrences": list(known_runs[:100]) if source_index_complete else [],
            "sessions": sessions[:100] if source_index_complete else [],
            "truncated": inventory_truncated
            or len(data_manifests) > 100
            or len(bundles) > 100
            or len(known_runs) > 100
            or len(sessions) > 100,
        },
    }


def _payloads(manifest: Manifest) -> list[dict[str, Any]]:
    return [
        {
            "role": item.role,
            "sha256": item.sha256,
            "size": item.size,
            "media_type": item.media_type,
        }
        for item in sorted(manifest.payloads)
    ]


def _parameters(manifest: Manifest) -> dict[str, Any]:
    return cast(dict[str, Any], _safe_value(manifest.parameters.value()))


class LocalWorkspace:
    """Browse local artifact manifests without opening research data payloads."""

    def __init__(self, registry: Registry, store: ArtifactStore) -> None:
        self.registry = registry
        self.store = store
        self.curation_owner: Any | None = None

    def inventory(
        self,
        *,
        kind: str | None = None,
        category: str | None = None,
        query: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a bounded metadata-only page from the configured ArtifactStore."""
        if kind is not None and kind not in KINDS:
            raise BoundaryError("local_workspace", "unknown_artifact_kind")
        if category is not None and category not in CATEGORIES:
            raise BoundaryError("local_workspace", "unknown_category")
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise BoundaryError("local_workspace", "invalid_page_size")
        if type(offset) is not int or offset < 0:
            raise BoundaryError("local_workspace", "invalid_page_offset")
        if query is not None and (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 128
            or any(ord(character) < 32 for character in query)
        ):
            raise BoundaryError("local_workspace", "invalid_search_query")

        search = query.strip().casefold() if query is not None else None
        category_kind = CATEGORY_KINDS.get(category or "")
        matches: list[tuple[Manifest, dict[str, Any]]] = []
        for artifact_id in self.store.manifest_ids():
            manifest = self.store.get_manifest(artifact_id)
            if kind is not None and manifest.kind != kind:
                continue
            if category == "recordings" and manifest.kind != "evidence":
                continue
            if category_kind is not None and manifest.kind != category_kind:
                continue
            parameters = _parameters(manifest)
            if category == "recordings":
                schema = parameters.get("schema")
                if not isinstance(schema, str) or schema not in RECORDING_SCHEMAS:
                    continue
            if search is not None:
                searchable = " ".join(
                    (
                        manifest.artifact_id,
                        manifest.kind,
                        json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                    )
                ).casefold()
                if search not in searchable:
                    continue
            matches.append((manifest, parameters))

        matches.sort(key=lambda pair: pair[0].artifact_id)
        page = matches[offset : offset + limit]
        items = []
        for manifest, parameters in page:
            indexed = True
            try:
                indexed_manifest = self.registry.get(manifest.artifact_id)
                if indexed_manifest != manifest:
                    raise BoundaryError("local_workspace", "registry_manifest_mismatch")
            except BoundaryError as error:
                if error.code != "not_indexed":
                    raise
                indexed = False
            items.append(
                {
                    "artifact_id": manifest.artifact_id,
                    "kind": manifest.kind,
                    "parameters": parameters,
                    "parents": [
                        {"role": parent.role, "artifact_id": parent.artifact_id}
                        for parent in sorted(manifest.parents)
                    ],
                    "payloads": _payloads(manifest),
                    "registry_indexed": indexed,
                    "registry_cached": (
                        self.registry.is_cached(manifest.artifact_id) if indexed else None
                    ),
                }
            )

        return {
            "schema": INVENTORY_SCHEMA,
            "source": "configured_local_artifact_store",
            "kind": kind,
            **({"category": category} if category is not None else {}),
            "query": query.strip() if query is not None else None,
            "limit": limit,
            "offset": offset,
            "total": len(matches),
            "items": items,
        }

    def artifact(self, artifact_id: str) -> dict[str, Any]:
        """Return one exact local manifest and safe descriptors, with no payload reads."""
        digest(artifact_id, "local_workspace.artifact_id")
        manifest = self.store.get_manifest(artifact_id)
        indexed = True
        try:
            indexed_manifest = self.registry.get(artifact_id)
            if indexed_manifest != manifest:
                raise BoundaryError("local_workspace", "registry_manifest_mismatch")
        except BoundaryError as error:
            if error.code != "not_indexed":
                raise
            indexed = False
        result = {
            "schema": ARTIFACT_SCHEMA,
            "source": "configured_local_artifact_store",
            "artifact_id": manifest.artifact_id,
            "kind": manifest.kind,
            "producer": {
                "repository": _safe_value(manifest.producer.repository),
                "source_revision": manifest.producer.source_revision,
                "uv_lock_sha256": manifest.producer.uv_lock_sha256,
            },
            "parameters": _parameters(manifest),
            "parents": [
                {"role": parent.role, "artifact_id": parent.artifact_id}
                for parent in sorted(manifest.parents)
            ],
            "payloads": _payloads(manifest),
            "registry_indexed": indexed,
            "registry_cached": self.registry.is_cached(artifact_id) if indexed else None,
        }
        result["data_facts"] = _data_facts(self.store, artifact_id, self.curation_owner)
        parameters = manifest.parameters.value()
        if manifest.kind == "model" and parameters.get("schema") == "stpd/experimental-m2-model-v1":
            result["workbench_memory_recipe"] = recorded_memory_recipe(self.store, manifest)
        return result


def open_registered_workspace(
    config: LocalResearchWorkspaceConfig | None,
) -> LocalWorkspace | None:
    """Open only an explicitly registered, already-existing local store and index."""
    if config is None:
        return None
    if not config.store_dir.is_dir():
        raise BoundaryError("local_workspace", "store_not_found")
    if not config.registry_path.is_file():
        raise BoundaryError("local_workspace", "registry_not_found")
    try:
        registry = SQLiteRegistry(config.registry_path, readonly=True)
    except BoundaryError as error:
        if error.stage == "registry":
            raise BoundaryError("local_workspace", error.code) from error
        raise
    except sqlite3.DatabaseError as error:
        raise BoundaryError("local_workspace", "registry_unavailable") from error
    store = ManifestArtifactStore(LocalBlobStore(config.store_dir, create=False, readonly=True))
    return LocalWorkspace(registry, store)
