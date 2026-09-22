"""Completed-run comparison rejects mismatched cohorts and misleading row inventories."""
import io
import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from test_stage1a_training import tiny_config, token_inputs

from spireagent.artifact_contracts import Parent
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from stpd.fullrun.token_comparison import compare_token_results
from stpd.workers.token_worker import execute_tokens, prepare_token_run


@dataclass(frozen=True)
class _CompletedPairSource:
    """Read-only producer output from which each test gets an isolated store."""

    store: ManifestArtifactStore
    results: tuple[str, str]


def _produce_pair(root: Path) -> _CompletedPairSource:
    owner, inputs = token_inputs(root)
    assert isinstance(owner.store, ManifestArtifactStore)
    results = []
    for recipe in ("stage1a.dsimple.s.v1", "stage1a.b.s.v1"):
        config = replace(tiny_config(recipe), steps=1)
        run = prepare_token_run(owner.store, inputs, config, owner.producer)
        reporter = ObjectStoreRunReporter(owner.store, owner.store.blobs)
        outcome = execute_tokens(owner.store, reporter, run.artifact_id, owner.producer)
        assert outcome.result_id is not None
        results.append(outcome.result_id)
    assert len(results) == 2
    result_ids: tuple[str, str] = (results[0], results[1])
    # Keep one direct producer -> comparator path in addition to the transferred copies.
    compare_token_results(owner.store, list(result_ids))
    return _CompletedPairSource(owner.store, result_ids)


def _clone_pair(
    source: _CompletedPairSource, root: Path,
) -> tuple[ManifestArtifactStore, list[str]]:
    store = ManifestArtifactStore(LocalBlobStore(root / "store"))
    for result_id in source.results:
        copy_artifact(source.store, store, result_id)
    return store, list(source.results)


@pytest.fixture(scope="module")
def completed_pair_source(tmp_path_factory: pytest.TempPathFactory) -> _CompletedPairSource:
    return _produce_pair(tmp_path_factory.mktemp("completed-pair-source"))


@pytest.fixture
def completed_pair(
    tmp_path: Path, completed_pair_source: _CompletedPairSource,
) -> tuple[ManifestArtifactStore, list[str]]:
    return _clone_pair(completed_pair_source, tmp_path)


def test_comparison_aligns_completed_configs_and_rejects_duplicate_results(completed_pair):
    store, results = completed_pair
    comparison = compare_token_results(store, results)
    left, right = comparison["models"]
    assert left["config"]["recipe"] != right["config"]["recipe"]
    assert left["decision_count"] == right["decision_count"] == 1
    assert left["independent_runs"] == right["independent_runs"] == 1
    assert comparison["paired_delta_from_first"][0]["top1"] == (
        right["decision_weighted"]["top1"] - left["decision_weighted"]["top1"])
    with pytest.raises(BoundaryError, match="distinct_completed"):
        compare_token_results(store, [results[0], results[0]])


@pytest.mark.parametrize("damage", ["duplicate", "omit", "split", "view", "negative"])
def test_comparison_rejects_corrupt_or_different_report(completed_pair, damage):
    store, results = completed_pair
    result = store.get_manifest(results[1])
    report = store.get_manifest(result.parent("offline_evaluation"))
    metrics = json.loads(b"".join(store.read_payload(report.payload("metrics"))))
    if damage == "duplicate":
        metrics["rows"] *= 2
    elif damage == "omit":
        metrics["rows"] = []
    elif damage == "split":
        metrics["rows"][0]["split"] = "train"
    elif damage == "negative":
        metrics["rows"][0]["nll"] = -1
    if damage == "view":
        report = replace(report, parents=tuple(
            Parent(p.role, result.parent("training_input")) if p.role == "model_view" else p
            for p in report.parents))
    else:
        payload = store.put_payload("metrics", io.BytesIO(json_bytes(metrics)), "application/json")
        report = replace(report, payloads=(payload,))
    store.publish(report)
    changed = replace(result, parents=tuple(
        Parent(p.role, report.artifact_id) if p.role == "offline_evaluation" else p
        for p in result.parents))
    store.publish(changed)
    with pytest.raises(BoundaryError, match="token_comparison"):
        compare_token_results(store, [results[0], changed.artifact_id])


def test_fixture_copies_are_independent_and_leave_baseline_untouched(
    tmp_path: Path, completed_pair_source: _CompletedPairSource,
):
    first, first_results = _clone_pair(completed_pair_source, tmp_path / "first")
    second, second_results = _clone_pair(completed_pair_source, tmp_path / "second")
    assert isinstance(first.blobs, LocalBlobStore)
    assert isinstance(second.blobs, LocalBlobStore)
    assert isinstance(completed_pair_source.store.blobs, LocalBlobStore)
    assert first.blobs.root.is_absolute()
    assert second.blobs.root.is_absolute()
    assert first.blobs.root != second.blobs.root
    assert first.blobs.root != completed_pair_source.store.blobs.root
    assert not (first.blobs.root.parent / "operations.sqlite").exists()

    baseline = compare_token_results(
        completed_pair_source.store, list(completed_pair_source.results)
    )
    result = first.get_manifest(first_results[1])
    report = first.get_manifest(result.parent("offline_evaluation"))
    metrics = json.loads(b"".join(first.read_payload(report.payload("metrics"))))
    metrics["rows"] *= 2
    payload = first.put_payload("metrics", io.BytesIO(json_bytes(metrics)), "application/json")
    damaged_report = replace(report, payloads=(payload,))
    first.publish(damaged_report)
    damaged_result = replace(result, parents=tuple(
        Parent(p.role, damaged_report.artifact_id) if p.role == "offline_evaluation" else p
        for p in result.parents
    ))
    first.publish(damaged_result)

    with pytest.raises(BoundaryError, match="token_comparison"):
        compare_token_results(first, [first_results[0], damaged_result.artifact_id])
    assert compare_token_results(second, second_results) == baseline
    assert compare_token_results(
        completed_pair_source.store, list(completed_pair_source.results)
    ) == baseline
