"""Bind card A's frozen cohort proposal to the exact existing Snapshot.

This adapter does not choose a rule, calculate weights, move split roles or
reserve training use. Those remain the upstream cohort/Curation owners.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from stpd.p1.data import digest, json_bytes
from stpd.p1.dataset import SnapshotReader
from stpd.p1.weighting import SCHEMA, checked_weights

from .developer import ProjectConfig
from .inplace_curation import configured_owner
from .managed_local_workspace import inspect_managed_workspace


def cohort_weighting(config: ProjectConfig, plan: dict[str, Any],
                     configuration_id: str, snapshot_id: str) -> dict[str, Any]:
    body = {k: v for k, v in plan.items() if k != "configuration_id"}
    if (plan.get("schema") != "stpd/p1-cohort-plan-1"
            or plan.get("configuration_id") != configuration_id
            or digest(json_bytes(body)) != configuration_id):
        raise BoundaryError("training_cohort", "exact_cohort_configuration_required")
    owner = (configured_owner(config) if config.research_workspace else
             inspect_managed_workspace(config.state_dir).get("curation_owner"))
    if owner is None:
        raise BoundaryError("training_cohort", "original_curation_owner_required")
    import os

    from .service_process import cloud_environment

    os.environ.update(cloud_environment(config.cloud_environment_file))
    store = ManifestArtifactStore(
        LocalBlobStore(Path(owner.store_dir), create=False, readonly=True))
    reader = SnapshotReader(store, snapshot_id)
    selection = reader.selection
    sources = {s["projection_id"] for s in plan["sources"]}
    if (sources != {p.artifact_id for p in reader.manifest.parents}
            or plan["selection"] != selection["selection"]
            or plan["splits"] != selection["split_by_group"]):
        raise BoundaryError("training_cohort", "cohort_snapshot_selection_mismatch")
    counts = Counter((p["root_run_id"], p["group_id"], p["split"]) for p in selection["points"])
    actual = {(r["root_run_id"], r["group_id"], r["split"]): r["N"] for r in plan["runs"]}
    if len(actual) != len(plan["runs"]) or actual != dict(counts):
        raise BoundaryError("training_cohort", "cohort_snapshot_run_counts_mismatch")
    weighting = {"schema": SCHEMA, "rule_id": configuration_id,
                 "definition": plan["definition"],
                 "weights_by_root": {r["root_run_id"]: r["train_weight"]
                                     for r in plan["runs"] if r["split"] == "train"}}
    return checked_weights(weighting, selection)
