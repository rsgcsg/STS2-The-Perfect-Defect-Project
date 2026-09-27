"""Read-only local research artifact inventory for Workbench surfaces.

This module projects manifest metadata from the configured ArtifactStore. It never
opens payload bytes, creates artifacts, or talks to Hub/cloud APIs. The supplied
store should be the caller's local ArtifactStore instance.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from spireagent.artifact_contracts import KINDS, Manifest
from spireagent.json_boundary import BoundaryError, digest
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import Registry, SQLiteRegistry
from spireagent.storage.store import ArtifactStore, ManifestArtifactStore
from spireagent.workbench.dashboard import _safe_value
from spireagent.workbench.developer import LocalResearchWorkspaceConfig

INVENTORY_SCHEMA = "stpd/local-workspace-inventory-v1"
ARTIFACT_SCHEMA = "stpd/local-workspace-artifact-v1"
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
CATEGORIES = frozenset({"recordings", "datasets", "models", "reports", "all"})
CATEGORY_KINDS = {"datasets": "dataset", "models": "model",
                  "reports": "offline_evaluation"}
RECORDING_SCHEMAS = frozenset({"stpd/local-verified-bundle-v1",
                              "stpd/received-bundle-v1"})


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
        return {
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
    store = ManifestArtifactStore(
        LocalBlobStore(config.store_dir, create=False, readonly=True)
    )
    return LocalWorkspace(registry, store)
