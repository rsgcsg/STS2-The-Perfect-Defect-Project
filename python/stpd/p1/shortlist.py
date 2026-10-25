"""DEV-only checkpoint shortlist from complete immutable events; never runs a game."""
from __future__ import annotations

import math
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore

from .spec import RUN_SCHEMAS


def checkpoint_shortlist(store: ManifestArtifactStore, run_id: str) -> dict[str, Any]:
    run = store.get_manifest(run_id)
    if run.kind != "run" or run.parameters.value().get("schema") not in RUN_SCHEMAS:
        raise BoundaryError("p1_shortlist", "M_std_run_required")
    events = sorted(ObjectStoreRunReporter(store, store.blobs, read_concurrency=8).events(run_id),
                    key=lambda e: e.parameters.value()["step"])
    if [e.parameters.value()["step"] for e in events] != list(range(1, len(events) + 1)):
        raise BoundaryError("p1_shortlist", "complete_event_index_required")
    if any(e.producer != run.producer or e.parent("run") != run_id for e in events):
        raise BoundaryError("p1_shortlist", "event_producer_or_run_mismatch")
    if not any(e.parameters.value()["kind"] == "completed" for e in events):
        raise BoundaryError("p1_shortlist", "completed_run_required")
    metrics: dict[tuple[str, int], dict[str, Any]] = {}
    candidates: dict[int, dict[str, Any]] = {}
    progress_count = 0
    for event in events:
        info = event.parameters.value()
        details = info["details"]
        if info["kind"] == "progress":
            progress_count += 1
            dev = details.get("dev") or details.get("dev_checkpoint")
            if dev is not None:
                loss, n, correct = dev["mean_N_loss"], dev["N"], dev["correct"]
                if (type(loss) not in {float, int} or not math.isfinite(loss)
                        or type(n) is not int or n < 1 or type(correct) is not int
                        or not 0 <= correct <= n):
                    raise BoundaryError("p1_shortlist", "invalid_DEV_metrics")
                metrics[(info["attempt"], details["optimizer_updates"])] = {
                    "N": n, "mean_N_loss": loss, "correct": correct, "top1": correct / n,
                    "metric_event_id": event.artifact_id}
        elif info["kind"] == "checkpoint":
            step = details["optimizer_updates"]
            dev = metrics.get((info["attempt"], step))
            if dev is None:
                continue
            checkpoint = store.get_manifest(details["checkpoint_id"])
            if (checkpoint.kind != "checkpoint" or checkpoint.producer != run.producer
                    or checkpoint.parent("run") != run_id
                    or checkpoint.parent("training_input") != run.parent("training_input")
                    or checkpoint.parameters.value()["optimizer_updates"] != step
                    or checkpoint.parameters.value()["attempt"] != info["attempt"]):
                raise BoundaryError("p1_shortlist", "checkpoint_binding_mismatch")
            candidates[step] = {"checkpoint_id": checkpoint.artifact_id, "step": step,
                                "selection": "current", **dev}
    if not candidates:
        raise BoundaryError("p1_shortlist", "measured_checkpoints_required")
    rows = list(candidates.values())
    chosen: list[dict[str, Any]] = []

    def add(row: dict[str, Any], reason: str) -> None:
        if all(r["step"] != row["step"] for r in chosen):
            chosen.append({**row, "reason": reason})

    nll = sorted(rows, key=lambda r: (r["mean_N_loss"], r["step"]))
    add(nll[0], "DEV NLL minimum")
    add(min(rows, key=lambda r: (-r["top1"], r["mean_N_loss"], r["step"])),
        "DEV top1 maximum")
    add(min(rows, key=lambda r: r["step"]), "earliest measured retained state")
    for row in nll:
        if len(chosen) >= 4:
            break
        add(row, "next distinct DEV NLL candidate")
    training = store.get_manifest(run.parent("training_input"))
    return {"schema": "stpd/p1-DEV-checkpoint-shortlist-1", "run_id": run_id,
            "training_input_id": training.artifact_id, "snapshot_id": training.parent("source"),
            "training_producer": run.producer.to_dict(), "event_count": len(events),
            "progress_observations": progress_count, "measured_checkpoints": len(rows),
            "candidates": chosen, "test_scored": False, "gameplay_run": False,
            "selection_rule": "NLL-best, top1-best, earliest, then distinct NLL; at most four",
            "next": "export CURRENT weights; register; bind alongside first-run step438; plan only",
            "incomplete_shortlist": len(chosen) < 3}
