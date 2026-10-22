"""Real tiny producers -> exact frozen comparison; no strategy quality claim."""
from __future__ import annotations

from dataclasses import replace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.p1.baseline_spec import BaselineConfig
from stpd.p1.baseline_worker import execute_baseline_run
from stpd.p1.baseline_workload import prepare_baseline_run
from stpd.p1.comparison import compare_test_results
from stpd.p1.spec import MStdConfig
from stpd.p1.workload import prepare_p1_run
from stpd.workers.p1_run import execute_p1_run


@pytest.fixture(scope="module")
def four_results(tmp_path_factory):
    root = tmp_path_factory.mktemp("four-agent-final-test")
    store = store_at(root / "store")
    projections = [projection_fixture(store, "same-source-" + str(i))[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    reporter = ObjectStoreRunReporter(store, store.blobs)
    results = {}
    for algorithm in ("random", "frequency", "linear"):
        run = prepare_baseline_run(store, snapshot.artifact_id, PRODUCER,
            BaselineConfig(algorithm=algorithm, max_epochs=1, cpu_threads=1), "a" * 32)
        result = execute_baseline_run(store, reporter, run.artifact_id, PRODUCER,
                                     attempt_id=algorithm)
        results[algorithm] = result.result_id
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER, MStdConfig(max_epochs=1), "b" * 32)
    result = execute_p1_run(store, reporter, run.artifact_id, PRODUCER, attempt_id="mstd")
    results["mstd"] = result.result_id
    return store, snapshot, results


def test_four_original_results_compare_same_members_without_store_writes(four_results):
    store, snapshot, results = four_results
    before = store.manifest_ids()
    value = compare_test_results(store, results, open_test=True)
    assert value["snapshot_id"] == snapshot.artifact_id
    assert value["partition_N"] == {"train": 1, "dev": 1, "test": 1}
    assert {row["agent"] for row in value["rows"]} == set(results)
    assert all(row["N"] == 1 and row["test"]["N"] == 1 for row in value["rows"])
    assert all(0 <= row["greedy_top1"] <= 1 for row in value["rows"])
    assert store.manifest_ids() == before
    assert value["scientific_verdict"] == "not_claimed"


def test_wrong_label_or_snapshot_rejects_before_final_metrics(four_results):
    store, snapshot, results = four_results
    swapped = {**results, "random": results["frequency"], "frequency": results["random"]}
    with pytest.raises(BoundaryError, match="agent_algorithm_mismatch"):
        compare_test_results(store, swapped, open_test=True)
    alternate = replace(snapshot, producer=replace(PRODUCER, source_revision="d" * 40))
    store.publish(alternate)
    run = prepare_baseline_run(store, alternate.artifact_id, PRODUCER,
        BaselineConfig(algorithm="random", cpu_threads=1), "c" * 32)
    # Deliberately incomplete result cannot reach its metrics: source mismatch comes first.
    invalid = Manifest("run_result", PRODUCER, (Parent("run", run.artifact_id),))
    store.publish(invalid)
    with pytest.raises(BoundaryError, match="same_frozen_snapshot_required"):
        compare_test_results(store, {**results, "random": invalid.artifact_id}, open_test=True)
