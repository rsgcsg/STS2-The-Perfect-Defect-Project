#!/usr/bin/env python3
"""Inspect exact P1 versions and export whole-run weight candidates without freezing."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.local_recording_import import _selected_curation_owner
from spireagent.workbench.p1_data_preparation import discover_p1_projections
from spireagent.workbench.p1_recording_store import query_p1_sources
from spireagent.workbench.service_process import cloud_environment
from stpd.p1.cohort import inspect_cohort


def selection_ids(
    store: ManifestArtifactStore, request: dict[str, Any], discovered: list[dict[str, Any]]
) -> list[str]:
    ids: list[str] | None = request.get("projection_ids")
    if ids is not None and (not isinstance(ids, list)
                            or any(not isinstance(identity, str) for identity in ids)):
        raise BoundaryError("p1_cohort", "explicit_projection_ids_required")
    if ids is None:
        tools = request.get("tool_release_ids")
        if not isinstance(tools, list) or not tools or any(not isinstance(t, str) for t in tools):
            raise BoundaryError("p1_cohort", "explicit_projection_versions_required")
        ids = [r["projection_id"] for r in discovered
               if r["tool_release_id"] in tools and r["revision"] == "r1.5"
               and r["declared_origin"] == "declared_human"]
    elif request.get("new_source_tool_release_ids") is not None:
        tools = request["new_source_tool_release_ids"]
        if not isinstance(tools, list) or not tools or any(not isinstance(t, str) for t in tools):
            raise BoundaryError("p1_cohort", "explicit_new_source_tool_versions_required")
        anchored = {store.get_manifest(identity).parent("source") for identity in ids}
        fresh: dict[str, list[str]] = {}
        for row in discovered:
            if (row["source_id"] not in anchored and row["tool_release_id"] in tools
                    and row["revision"] == "r1.5" and row["declared_origin"] == "declared_human"):
                fresh.setdefault(row["source_id"], []).append(row["projection_id"])
        if any(len(versions) != 1 for versions in fresh.values()):
            raise BoundaryError("p1_cohort", "new_source_projection_version_ambiguous")
        ids = [*ids, *(versions[0] for _, versions in sorted(fresh.items()))]
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True,
                        help="existing explicit selection, or explicit tool_release_ids filter")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--holdout-targets", type=Path,
                        help="DEV/TEST group targets per difficulty; prior roles preserved")
    args = parser.parse_args()
    # Inventory reads the configured Store owner even if the running App is older.
    # It does not start/upgrade the App or admit its installed combination.
    config = ProjectConfig.load(args.config, require_current_combination=False)
    if config.cloud_environment_file:
        os.environ.update(cloud_environment(config.cloud_environment_file))
    owner = _selected_curation_owner(config)
    if owner is None:
        raise BoundaryError("p1_cohort", "curation_owner_required")
    if min(shutil.disk_usage(args.output.parent).free,
           shutil.disk_usage(owner.store_dir).free) < 15 * 1024**3:
        raise BoundaryError("p1_cohort", "fifteen_GiB_free_required")
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False, readonly=True))
    request = json.loads(args.selection.read_text())
    request = request.get("selection", request)
    ids = selection_ids(store, request, discover_p1_projections(store))
    result = inspect_cohort(store, owner.ledger, ids,
                            selected_decisions=request.get("selected_decisions"),
                            split_salt=request.get("split_salt", "p1-next-training-20261025-v1"),
                            holdout_targets=(json.loads(args.holdout_targets.read_text())
                                             if args.holdout_targets else None))
    result["source_catalog"] = query_p1_sources(store)
    selected_sources = {row["source_id"] for row in result["index"]["sources"]}
    result["pending_sources"] = [
        {"source_id": row["artifact_id"], "session_id": row["session_id"],
         "reason": "no_projection" if not row["projections"] else "projection_version_not_selected"}
        for row in result["source_catalog"]
        if row["declared_origin"] == "declared_human" and row["artifact_id"] not in selected_sources
    ]
    args.output.mkdir(parents=True, exist_ok=False)
    for name, body in result.items():
        (args.output / (name + ".json")).write_text(
            json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "unique_N": result["index"]["unique_N"],
                      "purpose_reserved": False, "training_use_recorded": False}))


if __name__ == "__main__":
    main()
