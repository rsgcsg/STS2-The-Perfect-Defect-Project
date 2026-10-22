"""Explicit final TEST opening for four frozen P1 Agent results.

Consumes the original result verifiers; never fits, selects, republishes or
changes Curation permissions. Ordinary DEV summaries remain sealed.
"""
from __future__ import annotations

import math
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ArtifactStore

AGENTS = ("random", "frequency", "linear", "mstd")
SCHEMA = "stpd/p1-four-agent-test-comparison-1"


def compare_test_results(
    store: ArtifactStore, results: dict[str, str], *, open_test: bool = False,
) -> dict[str, Any]:
    if open_test is not True:
        raise BoundaryError("p1_comparison", "explicit_test_opening_required")
    if set(results) != set(AGENTS) or len(set(results.values())) != 4:
        raise BoundaryError("p1_comparison", "four_distinct_agent_results_required")
    # Import neural dependencies only after explicit TEST access.
    from ..workers.p1_run import verify_p1_result
    from .baseline_worker import verify_baseline_result
    from .baseline_workload import BaselineWorkload, verify_baseline_run
    from .workload import P1Workload, verify_p1_run

    workloads: dict[str, P1Workload | BaselineWorkload] = {}
    workload: P1Workload | BaselineWorkload
    for agent in AGENTS:
        result = store.get_manifest(results[agent])
        run = store.get_manifest(result.parent("run"))
        if agent == "mstd":
            workload = verify_p1_run(store, run.artifact_id, run.producer)
        else:
            workload = verify_baseline_run(store, run.artifact_id, run.producer)
            if workload.config.algorithm != agent:
                raise BoundaryError("p1_comparison", "agent_algorithm_mismatch")
        workloads[agent] = workload
    snapshots = {w.snapshot.artifact_id for w in workloads.values()}
    if len(snapshots) != 1:
        raise BoundaryError("p1_comparison", "same_frozen_snapshot_required")
    first = workloads["mstd"]
    counts = first.training.parameters.value()["partition_N"]
    if counts.get("test", 0) < 1:
        raise BoundaryError("p1_comparison", "nonempty_test_required")
    rows = []
    for agent in AGENTS:
        workload = workloads[agent]
        if isinstance(workload, P1Workload):
            result, model, _ = verify_p1_result(store, results[agent], workload)
        else:
            result, model, _baseline_checkpoint = verify_baseline_result(
                store, results[agent], workload)
        evaluation = store.get_manifest(result.parent("evaluation"))
        metrics = evaluation.parameters.value()["test"]
        loss_key = "mean_N_loss" if agent == "mstd" else "mean_score_softmax_N_loss"
        correct_key = "correct" if agent == "mstd" else "expected_greedy_correct"
        loss, correct = metrics.get(loss_key), metrics.get(correct_key)
        if (type(metrics.get("N")) is not int or metrics["N"] != counts["test"]
                or type(loss) not in {int, float} or not math.isfinite(loss) or loss < 0
                or type(correct) not in {int, float} or not math.isfinite(correct)
                or not 0 <= correct <= counts["test"]):
            raise BoundaryError("p1_comparison", "invalid_test_metrics")
        rows.append({"agent": agent, "run_id": workload.run.artifact_id,
                     "result_id": result.artifact_id, "model_id": model.artifact_id,
                     "evaluation_id": evaluation.artifact_id,
                     "input_spec": workload.training.parameters.value()["input_spec"],
                     "test": metrics, "N": metrics["N"], "score_softmax_N_loss": loss,
                     "greedy_top1": correct / metrics["N"],
                     "tie_semantics": "first maximum" if agent == "mstd"
                                      else "uniform among maxima"})
    return {"schema": SCHEMA, "partition": "test", "test_opened_explicitly": True,
            "snapshot_id": first.snapshot.artifact_id,
            "snapshot_selection_sha256": first.snapshot.payload("selection").sha256,
            "partition_N": counts, "rows": rows,
            "validation_scope": "original_completed_result_checkpoint_model_and_frozen_snapshot",
            "scientific_verdict": "not_claimed",
            "interpretation": "same members; declared Agent Inputs and tie rules; "
                              "TEST exposure prevents independent holdout claims after tuning"}
