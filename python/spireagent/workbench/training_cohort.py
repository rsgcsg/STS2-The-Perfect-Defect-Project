"""Bind card A's frozen cohort proposal to the exact existing Snapshot.

Upstream owns rule choice and weights. Snapshot publication and purpose
reservation use the original Application/Curation owner; this adapter never
records training use or submits compute by itself.
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


def checked_cohort_plan(raw: object, configuration_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BoundaryError("training_cohort", "cohort_object_required")
    value: dict[str, Any] = raw
    if value.get("schema") == "training-data-weight-selection-20261025-v1":
        if (value.get("status") != "frozen_user_confirmed"
                or value.get("selected_configuration_id") != configuration_id
                or not isinstance(value.get("configuration"), dict)):
            raise BoundaryError("training_cohort", "selected_cohort_configuration_required")
        value = value["configuration"]
    required = {"schema", "configuration_id", "sources", "definition", "selection",
                "splits", "runs"}
    if not required <= value.keys():
        raise BoundaryError("training_cohort", "cohort_fields_required")
    body = {k: v for k, v in value.items() if k != "configuration_id"}
    if (value["schema"] != "stpd/p1-cohort-plan-1"
            or value["configuration_id"] != configuration_id
            or digest(json_bytes(body)) != configuration_id):
        raise BoundaryError("training_cohort", "exact_cohort_configuration_required")
    if (any(not isinstance(value[k], dict) for k in ("definition", "selection", "splits"))
            or not isinstance(value["sources"], list) or not value["sources"]
            or not isinstance(value["runs"], list) or not value["runs"]):
        raise BoundaryError("training_cohort", "cohort_containers_required")
    if any(not isinstance(row, dict) or not isinstance(row.get("projection_id"), str)
           for row in value["sources"]):
        raise BoundaryError("training_cohort", "cohort_projection_ids_required")
    for row in value["runs"]:
        if (not isinstance(row, dict)
                or any(not isinstance(row.get(k), str)
                       for k in ("root_run_id", "group_id", "split"))
                or row["split"] not in {"train", "dev", "test"}
                or type(row.get("N")) is not int or row["N"] < 1
                or "train_weight" not in row):
            raise BoundaryError("training_cohort", "cohort_run_counts_required")
    # Validate numeric weights before any Snapshot publication/reservation. The
    # actual point membership is still checked against SnapshotReader below.
    checked_weights(_loss_weighting(value), {"points": value["runs"]})
    return value


def _loss_weighting(plan: dict[str, Any]) -> dict[str, Any]:
    return {"schema": SCHEMA, "rule_id": plan["configuration_id"],
            "definition": plan["definition"],
            "weights_by_root": {r["root_run_id"]: r["train_weight"]
                                for r in plan["runs"] if r["split"] == "train"}}


def freeze_cohort(config: ProjectConfig, value: object, configuration_id: str) -> str:
    """An explicitly selected upstream plan goes through the original data owner."""
    from .p1_data_cli import p1_data_command

    plan = checked_cohort_plan(value, configuration_id)
    receipt = p1_data_command(config, "freeze", {"reserve": True, "selection": {
        "projection_ids": sorted(s["projection_id"] for s in plan["sources"]),
        "selected_decisions": plan["selection"], "split_by_group": plan["splits"],
        "split_salt": "cohort-" + configuration_id}})
    if receipt.get("purpose_reserved") is not True:
        raise BoundaryError("training_cohort", "normal_snapshot_reservation_required")
    return str(receipt["snapshot_id"])


def cohort_weighting(config: ProjectConfig, value: object,
                     configuration_id: str, snapshot_id: str) -> dict[str, Any]:
    plan = checked_cohort_plan(value, configuration_id)
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
    return checked_weights(_loss_weighting(plan), selection)
