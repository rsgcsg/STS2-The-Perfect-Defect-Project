"""Completed-run comparison rejects mismatched cohorts and misleading row inventories."""
import io
import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from test_stage1a_training import tiny_config, token_inputs

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
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
    assert comparison["schema"] == "stpd/stage1a-comparison-v2"
    left, right = comparison["models"]
    assert left["config"]["recipe"] != right["config"]["recipe"]
    assert left["decision_count"] == right["decision_count"] == 1
    assert left["reported_run_groups"] == right["reported_run_groups"] == 1
    assert all("independent_runs" not in item for item in (left, right))
    assert comparison["comparison_mode"] == "same-view"
    assert comparison["model_view_id"] == left["model_view_id"] == right["model_view_id"]
    for item in (left, right):
        report = store.get_manifest(item["evaluation_id"])
        metrics = json.loads(b"".join(store.read_payload(report.payload("metrics"))))
        assert report.parameters.value()["native_run_independence"] is False
        assert metrics["summary"]["bootstrap"]["status"] == "unknown"
        assert all(b["bootstrap"]["status"] == "unknown"
                   for b in metrics["baselines"].values())
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


@pytest.fixture(scope="module")
def fixed_dev_source(tmp_path_factory):
    from test_decision_training import prepared

    from stpd.fullrun.decision_training import (
        AllocationSpec,
        publish_allocation,
        publish_decision_view,
    )
    from stpd.fullrun.representation import FullRunSerializer
    from stpd.fullrun.token_inputs import load_token_inputs, publish_token_inputs

    owner, dataset = prepared(tmp_path_factory.mktemp("fixed-dev-source"))
    results = []
    for train_count in (1, 4):
        allocation = publish_allocation(owner.store, dataset,
                                        AllocationSpec(max_train=train_count, max_dev=2),
                                        owner.producer)
        view = publish_decision_view(owner.store, allocation.artifact_id,
                                     FullRunSerializer("lite"), owner.producer)
        token = publish_token_inputs(owner.store, view.artifact_id, "s", owner.producer)
        inputs = load_token_inputs(owner.store, token.artifact_id)
        run = prepare_token_run(owner.store, inputs, replace(tiny_config(), steps=1),
                                owner.producer)
        reporter = ObjectStoreRunReporter(owner.store, owner.store.blobs)
        outcome = execute_tokens(owner.store, reporter, run.artifact_id, owner.producer)
        assert outcome.result_id is not None
        results.append(outcome.result_id)
    return _CompletedPairSource(owner.store, (results[0], results[1]))


def test_real_fixed_dev_comparison_accepts_train_growth_and_cli_option(
    tmp_path, fixed_dev_source, monkeypatch,
):
    from test_light_action_m0_canonical_cli import _cli

    store, results = _clone_pair(fixed_dev_source, tmp_path)
    with pytest.raises(BoundaryError, match="same_fixed_dev_view_required"):
        compare_token_results(store, results)
    comparison = compare_token_results(store, results, comparison_mode="fixed-dev")
    assert comparison["schema"] == "stpd/stage1a-comparison-v2"
    left, right = comparison["models"]
    assert [left["train_row_count"], right["train_row_count"]] == [1, 4]
    assert left["model_view_id"] != right["model_view_id"]
    assert left["dev_inputs_sha256"] == right["dev_inputs_sha256"]
    assert left["steps"] == right["steps"] == 1
    assert left["renderer"] == right["renderer"]
    assert comparison["model_view_id"] is None
    assert comparison["training_data_changed"] is True
    assert comparison["tokenizer_changed"] is True
    assert comparison["train_only_tokenizer_covaries"] is True
    assert all(m["tokenizer"]["fit_scope"] == "train_only" for m in (left, right))
    assert all(m["dev_qualification"]["clean_held_out_claim"] is False for m in (left, right))
    assert comparison["interpretation"] == \
        "descriptive_paired_comparison_not_causal_or_quality_verdict"
    cli = _cli(monkeypatch, "--store", str(store.blobs.root), "compare-tokens",
               "--result", results[0], "--result", results[1], "--comparison-mode", "fixed-dev")
    assert cli == comparison
    with pytest.raises(BoundaryError, match="unsupported_comparison_mode"):
        compare_token_results(store, results, comparison_mode="unchecked")


@pytest.mark.parametrize("damage", [
    "state_text", "action_texts", "action_keys", "candidate_order", "chosen_index",
    "transition_id", "run_id", "surface", "family", "split", "omit", "duplicate", "extra",
])
def test_fixed_dev_rejects_every_changed_sample_fact(fixed_dev_source, monkeypatch, damage):
    import stpd.fullrun.token_comparison as module

    store = fixed_dev_source.store
    results = list(fixed_dev_source.results)
    second_input = store.get_manifest(results[1]).parent("training_input")
    original_loader = module._load_inputs

    def changed_loader(store, identity):
        # Exercise the comparison gate after the real typed loader has verified its source.
        loaded = original_loader(store, identity)
        if identity != second_input:
            return loaded
        samples = list(loaded.samples)
        index = next(i for i, sample in enumerate(samples) if sample.split == "dev")
        sample = samples[index]
        if damage == "omit":
            del samples[index]
        elif damage == "duplicate":
            samples.append(sample)
        elif damage == "extra":
            samples.append(replace(sample, transition_id="f" * 64))
        elif damage == "candidate_order":
            assert len(sample.action_keys) > 1
            samples[index] = replace(sample, action_keys=sample.action_keys[::-1],
                                     action_texts=sample.action_texts[::-1],
                                     chosen_index=len(sample.action_keys) - 1 - sample.chosen_index)
        else:
            value = getattr(sample, damage)
            if damage in {"action_keys", "action_texts"}:
                value = (value[0] + " altered", *value[1:])
            elif damage == "chosen_index":
                value = (value + 1) % len(sample.action_keys)
            elif damage == "split":
                value = "train"
            else:
                value += " altered"
            samples[index] = replace(sample, **{damage: value})
        return replace(loaded, samples=tuple(samples))

    monkeypatch.setattr(module, "_load_inputs", changed_loader)
    with pytest.raises(BoundaryError, match="complete_dev|unique_dev"):
        compare_token_results(store, results, comparison_mode="fixed-dev")


@pytest.mark.parametrize("mode", ["same-view", "fixed-dev"])
@pytest.mark.parametrize("damage", ["duplicate", "omit", "run_id", "surface", "family", "view"])
def test_each_report_remains_bound_complete_and_unique(completed_pair, mode, damage):
    store, results = completed_pair
    result = store.get_manifest(results[1])
    report = store.get_manifest(result.parent("offline_evaluation"))
    metrics = json.loads(b"".join(store.read_payload(report.payload("metrics"))))
    if damage == "view":
        report = replace(report, parents=tuple(
            Parent(p.role, result.parent("training_input")) if p.role == "model_view" else p
            for p in report.parents))
    else:
        if damage == "duplicate":
            metrics["rows"] *= 2
        elif damage == "omit":
            metrics["rows"] = []
        else:
            metrics["rows"][0][damage] += " altered"
        payload = store.put_payload("metrics", io.BytesIO(json_bytes(metrics)), "application/json")
        report = replace(report, payloads=(payload,))
    store.publish(report)
    changed = replace(result, parents=tuple(
        Parent(p.role, report.artifact_id) if p.role == "offline_evaluation" else p
        for p in result.parents))
    store.publish(changed)
    with pytest.raises(BoundaryError, match="token_comparison"):
        compare_token_results(store, [results[0], changed.artifact_id], comparison_mode=mode)


def assert_m0_comparison_preserves_qualification(store, result_id):
    """A second synthetic receipt exercises projection without another optimizer run."""
    result = store.get_manifest(result_id)
    variant = replace(result, parameters=FrozenObject.of({
        **result.parameters.value(),
        "attempt_seconds": result.parameters.value()["attempt_seconds"] + 1,
    }))
    store.publish(variant)
    comparison = compare_token_results(store, [result_id, variant.artifact_id])
    for model in comparison["models"]:
        assert "independent_runs" not in model
        assert model["native_run_independence"] == "unknown_across_sessions"
        qualification = model["dev_qualification"]
        assert qualification["physical_game_independence"] == "unresolved"
        assert qualification["historical_external_exposure"] == "unknown"
        assert qualification["clean_held_out_claim"] is False
        assert any(item["origin"] == model["evaluation_id"] and
                   item["facts"]["physical_game_independence"] == "unresolved"
                   for item in qualification["evidence"])
    return comparison


def test_legacy_immutable_independence_assumption_is_preserved_but_not_adopted(completed_pair):
    from stpd.fullrun.evaluation import summarize_rows

    store, results = completed_pair
    result = store.get_manifest(results[1])
    report = store.get_manifest(result.parent("offline_evaluation"))
    metrics = json.loads(b"".join(store.read_payload(report.payload("metrics"))))
    metrics.pop("admission")
    metrics.pop("qualification_evidence")
    metrics["summary"] = summarize_rows(metrics["rows"], seed=1701)
    parameters = {key: report.parameters.value()[key]
                  for key in ("schema", "partition", "qualification")}
    legacy_report = replace(report, parameters=FrozenObject.of(parameters), payloads=(
        store.put_payload("metrics", io.BytesIO(json_bytes(metrics)), "application/json"),))
    store.publish(legacy_report)
    legacy_result = replace(result, parents=tuple(
        Parent(p.role, legacy_report.artifact_id) if p.role == "offline_evaluation" else p
        for p in result.parents))
    store.publish(legacy_result)
    before = store.manifest_ids()
    comparison = compare_token_results(store, [results[0], legacy_result.artifact_id])
    assert store.manifest_ids() == before
    assert store.get_manifest(legacy_report.artifact_id) == legacy_report
    model = comparison["models"][1]
    assert "independent_runs" not in model
    assert model["dev_qualification"]["native_run_independence"] is False
    assert any(item["origin"] == "report:bootstrap" and
               item["facts"]["unit"] == "whole_run"
               for item in model["dev_qualification"]["evidence"])
    assert any(item["facts"].get("historical_external_exposure") == "unknown"
               for item in model["dev_qualification"]["evidence"])


@pytest.mark.parametrize("schema", [
    "stpd/decision-model-view-v1", "stpd/public-observation-bc-view-v2",
    "stpd/human-text-input-bc-view-v2", "future-unqualified-view",
])
def test_qualification_facts_preserve_restrictions_without_schema_inference(tmp_path, schema):
    from test_artifact_store_v1 import PRODUCER

    from stpd.fullrun.dataset_policy import token_dev_qualification

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    source = Manifest("dataset", PRODUCER, parameters=FrozenObject.of({
        "purpose": "training", "historical_external_exposure": "known_prior_exposure",
        "physical_game_independence": "shared_physical_game",
    }))
    store.publish(source)
    view = Manifest("model_view", PRODUCER, (Parent("dataset", source.artifact_id),),
                    parameters=FrozenObject.of({"schema": schema}))
    store.publish(view)
    report = Manifest("offline_evaluation", PRODUCER, parameters=FrozenObject.of({
        "native_run_independence": True, "historical_external_exposure": "unknown",
        "physical_game_independence": "unresolved", "clean_held_out_claim": False,
    }))
    store.publish(report)
    before = store.manifest_ids()
    facts = token_dev_qualification(store, view, report=report)
    assert facts["native_run_independence"] is False
    assert facts["clean_held_out_claim"] is False
    assert facts["evaluation_scope"] == "engineering_dev_from_model_view"
    assert facts["historical_external_exposure"] == "known_prior_exposure"
    assert facts["physical_game_independence"] == "shared_physical_game"
    assert any(item["origin"] == source.artifact_id and item["facts"] == {
        "historical_external_exposure": "known_prior_exposure",
        "physical_game_independence": "shared_physical_game",
    } for item in facts["evidence"])
    assert any(item["origin"] == report.artifact_id and
               item["facts"]["native_run_independence"] is True
               for item in facts["evidence"])
    assert store.manifest_ids() == before
    assert store.get_manifest(report.artifact_id) == report


@pytest.mark.parametrize("source_facts,report_facts,admission,field,expected", [
    ({"historical_external_exposure": "known_prior_exposure"},
     {"historical_external_exposure": "unknown"}, None,
     "historical_external_exposure", "known_prior_exposure"),
    ({"historical_external_exposure": "unknown"},
     {"historical_external_exposure": "known_prior_exposure"}, None,
     "historical_external_exposure", "known_prior_exposure"),
    ({}, {}, {"historical_external_exposure": "known_prior_exposure"},
     "historical_external_exposure", "known_prior_exposure"),
    ({"physical_game_independence": "shared_physical_game"},
     {"physical_game_independence": "unresolved"}, None,
     "physical_game_independence", "shared_physical_game"),
    ({}, {"physical_game_independence": False}, None, "physical_game_independence", False),
    ({"evaluation_scope": "restricted_source_subset"}, {}, None,
     "evaluation_scope", "restricted_source_subset"),
    ({}, {}, {"evaluation_scope": "within_training_purpose_allocation"},
     "evaluation_scope", "within_training_purpose_allocation"),
    ({}, {}, None, "evaluation_scope", "engineering_dev_from_model_view"),
    ({}, {}, None, "historical_external_exposure", "unknown"),
    ({}, {}, None, "physical_game_independence", "unresolved"),
])
def test_qualification_facts_known_restrictions_override_only_unknown_defaults(
    tmp_path, source_facts, report_facts, admission, field, expected,
):
    from test_artifact_store_v1 import PRODUCER

    from stpd.fullrun.dataset_policy import token_dev_qualification

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    source = Manifest("dataset", PRODUCER, parameters=FrozenObject.of({
        "purpose": "training", **source_facts,
    }))
    store.publish(source)
    view = Manifest("model_view", PRODUCER, (Parent("dataset", source.artifact_id),))
    store.publish(view)
    report = Manifest("offline_evaluation", PRODUCER, parameters=FrozenObject.of(report_facts))
    facts = token_dev_qualification(store, view, report=report, admission=admission)
    assert facts[field] == expected
    assert facts["native_run_independence"] is False
    assert facts["clean_held_out_claim"] is False


@pytest.mark.parametrize("field,left,right", [
    ("historical_external_exposure", "known_prior_exposure", "another_explicit_exposure"),
    ("physical_game_independence", "shared_physical_game", "independent"),
    ("evaluation_scope", "restricted_source_subset", "within_training_purpose_allocation"),
])
def test_qualification_facts_conflicts_preserve_origins_and_survive_reprojection(
    tmp_path, field, left, right,
):
    from test_artifact_store_v1 import PRODUCER

    from stpd.fullrun.dataset_policy import token_dev_qualification

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    source = Manifest("dataset", PRODUCER,
                      parameters=FrozenObject.of({"purpose": "training", field: left}))
    store.publish(source)
    view = Manifest("model_view", PRODUCER, (Parent("dataset", source.artifact_id),))
    store.publish(view)
    report = Manifest("offline_evaluation", PRODUCER, parameters=FrozenObject.of({field: right}))
    unknown = "unresolved" if field == "physical_game_independence" else "unknown"
    facts = token_dev_qualification(store, view, report=report, admission={field: unknown})
    conflict = facts[field]
    assert conflict["status"] == "conflicting_reported_facts"
    assert {item["origin"]: item["value"] for item in conflict["claims"]} == {
        source.artifact_id: left, report.artifact_id: right,
    }
    projected_report = replace(report, parameters=FrozenObject.of({field: conflict}))
    repeated = token_dev_qualification(store, view, report=projected_report, metrics={
        "admission": {field: conflict}, "qualification_evidence": facts["evidence"],
    })
    assert repeated[field] == conflict
    assert repeated["native_run_independence"] is False
    assert repeated["clean_held_out_claim"] is False


@pytest.mark.parametrize("claim", [
    True, "independent", "proven_independent", "future_positive_claim",
])
def test_qualification_facts_positive_claims_never_supply_a_proof_entry(tmp_path, claim):
    from test_artifact_store_v1 import PRODUCER

    from stpd.fullrun.dataset_policy import token_dev_qualification

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    source = Manifest("dataset", PRODUCER, parameters=FrozenObject.of({"purpose": "training"}))
    store.publish(source)
    view = Manifest("model_view", PRODUCER, (Parent("dataset", source.artifact_id),))
    store.publish(view)
    report = Manifest("offline_evaluation", PRODUCER, parameters=FrozenObject.of({
        "physical_game_independence": claim, "native_run_independence": True,
        "clean_held_out_claim": True,
    }))
    facts = token_dev_qualification(store, view, report=report)
    assert facts["native_run_independence"] is False
    assert facts["clean_held_out_claim"] is False
    assert facts["physical_game_independence"] == {
        "status": "unverified_reported_facts",
        "claims": [{"origin": report.artifact_id, "value": claim}],
    }
