"""Candidate-aligned offline metrics and whole-run uncertainty; no model-quality verdict."""

from __future__ import annotations

import io
import math
import random
import statistics
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes, unsigned
from spireagent.storage.store import ArtifactStore

from .features import ModelSample, load_model_view

EVALUATION_SCHEMA = "stpd/offline-ranking-evaluation-v1"
MODEL_SCHEMA = "stpd/scheme1-model-v1"
PROTOCOL_SCHEMA = "stpd/fullrun-protocol-v1"
BASELINES = frozenset({"model", "uniform_legal", "action_only"})
EVALUATION_COLUMNS = frozenset(
    {
        "transition_id",
        "run_id",
        "surface",
        "family",
        "split",
        "candidate_count",
        "top1",
        "mrr",
        "nll",
        "confidence",
        "margin",
    }
)


@dataclass(frozen=True)
class LoadedEvaluation:
    manifest: Manifest
    model: Manifest
    view: Manifest
    rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def candidate_metrics(scores: Sequence[float], acceptable: Sequence[int]) -> dict[str, float]:
    values = tuple(float(value) for value in scores)
    labels = tuple(acceptable)
    if (
        not values
        or any(not math.isfinite(value) for value in values)
        or not labels
        or len(set(labels)) != len(labels)
        or any(type(index) is not int or not 0 <= index < len(values) for index in labels)
    ):
        raise BoundaryError("evaluation", "invalid_scores_or_acceptable_actions")
    top = max(values)
    winners = [index for index, value in enumerate(values) if value == top]
    top1 = len(set(winners) & set(labels)) / len(winners)
    reciprocal = 0.0
    for score in sorted(set(values), reverse=True):
        block = [index for index, value in enumerate(values) if value == score]
        good = len(set(block) & set(labels))
        if not good:
            continue
        above = sum(value > score for value in values)
        total = len(block)
        survival = 1.0
        for offset in range(total - good + 1):
            first_probability = survival * good / (total - offset)
            reciprocal += first_probability / (above + offset + 1)
            survival *= (total - offset - good) / (total - offset)
        break
    exponentials = [math.exp(value - top) for value in values]
    denominator = sum(exponentials)
    accepted_max = max(values[index] for index in labels)
    accepted_offset_logsum = math.log(
        sum(math.exp(values[i] - accepted_max) for i in labels)
    )
    # Subtract large logits before adding logarithms. Adding log(N) to 1e308
    # would round away the entire probability term even for an equal-score tie.
    nll = (top - accepted_max) + math.log(denominator) - accepted_offset_logsum
    ordered = sorted(values, reverse=True)
    result = {
        "top1": top1,
        "mrr": reciprocal,
        "nll": max(0.0, nll),
        "confidence": 1.0 / denominator,
        "margin": ordered[0] - ordered[1] if len(ordered) > 1 else 0.0,
    }

    if any(not math.isfinite(value) for value in result.values()):
        raise BoundaryError("evaluation", "metric_overflow")
    return result


def action_only_prior(
    samples: tuple[ModelSample, ...],
) -> Callable[[ModelSample], tuple[float, ...]]:
    counts: Counter[str] = Counter()
    for sample in samples:
        if sample.split == "train":
            counts[sample.action_texts[sample.chosen_index]] += 1
    if not counts:
        raise BoundaryError("evaluation", "action_prior_requires_training_data")
    return lambda sample: tuple(math.log(counts[action] + 1) for action in sample.action_texts)


def summarize_rows(
    rows: list[dict[str, Any]], *, seed: int, bootstrap: int = 200,
    native_run_independence: bool = True,
) -> dict[str, Any]:
    unsigned(seed, "evaluation.seed")
    unsigned(bootstrap, "evaluation.bootstrap")
    if not rows:
        raise BoundaryError("evaluation", "empty_evaluation")

    def mean(selected: list[dict[str, Any]]) -> dict[str, float | int]:
        return {
            "count": len(selected),
            **{
                metric: statistics.fmean(row[metric] for row in selected)
                for metric in ("top1", "mrr", "nll", "confidence", "margin")
            },
        }

    result: dict[str, Any] = {"overall": mean(rows)}
    for dimension in ("surface", "family", "candidate_count", "run_id"):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row[dimension])].append(row)
        result["by_" + dimension] = {key: mean(value) for key, value in sorted(grouped.items())}
    bins: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bins[min(9, int(row["confidence"] * 10))].append(row)
    result["ece_10_bins"] = sum(
        len(values)
        / len(rows)
        * abs(
            statistics.fmean(row["confidence"] for row in values)
            - statistics.fmean(row["top1"] for row in values)
        )
        for values in bins.values()
    )
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_run[row["run_id"]].append(row)
    if not native_run_independence:
        result["bootstrap"] = {
            "status": "unknown",
            "reason": "native_run_independence_unknown_across_sessions",
            "unit": "session_scoped_run_group",
            "reported_run_groups": len(by_run),
        }
    elif len(by_run) < 2 or bootstrap < 20:
        result["bootstrap"] = {
            "status": "insufficient_independent_runs_or_replicates",
            "unit": "whole_run",
            "runs": len(by_run),
        }
    else:
        rng = random.Random(seed)
        run_ids = sorted(by_run)
        draws: dict[str, list[float]] = defaultdict(list)
        for _ in range(bootstrap):
            selected = [row for _ in run_ids for row in by_run[rng.choice(run_ids)]]
            for metric in ("top1", "mrr", "nll"):
                draws[metric].append(statistics.fmean(row[metric] for row in selected))
        result["bootstrap"] = {
            "status": "computed",
            "unit": "whole_run",
            "runs": len(run_ids),
            "seed": seed,
            "replicates": bootstrap,
            "percentile_95": {
                metric: [
                    sorted(values)[int(bootstrap * 0.025)],
                    sorted(values)[min(bootstrap - 1, int(bootstrap * 0.975))],
                ]
                for metric, values in draws.items()
            },
        }
    return result


def evaluate_samples(
    samples: tuple[ModelSample, ...],
    scorer: Callable[[int], Sequence[float]],
    *,
    partition: str = "dev",
    seed: int = 0,
    bootstrap: int = 200,
    permit_test: bool = False,
    native_run_independence: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if partition not in {"dev", "test"} or (partition == "test" and not permit_test):
        raise BoundaryError("evaluation", "sealed_test_requires_explicit_protocol_admission")
    rows = []
    for index, sample in enumerate(samples):
        if sample.split != partition:
            continue
        scores = tuple(scorer(index))
        if len(scores) != len(sample.action_texts):
            raise BoundaryError("evaluation", "candidate_alignment_mismatch")
        metrics = candidate_metrics(scores, (sample.chosen_index,))
        rows.append(
            {
                "transition_id": sample.transition_id,
                "run_id": sample.run_id,
                "surface": sample.surface,
                "family": sample.family,
                "split": sample.split,
                "candidate_count": len(scores),
                **metrics,
            }
        )
    return rows, summarize_rows(
        rows, seed=seed, bootstrap=bootstrap,
        native_run_independence=native_run_independence,
    )


def _validate_protocol(
    store: ArtifactStore,
    protocol_id: str,
    view_id: str,
    training_input: Manifest,
) -> None:
    protocol = store.get_manifest(protocol_id)
    info = protocol.parameters.value()
    if (
        protocol.kind != "protocol"
        or info.get("schema") != PROTOCOL_SCHEMA
        or info.get("status") != "frozen"
        or not isinstance(info.get("allowed_model_views"), list)
        or view_id not in info["allowed_model_views"]
        or info.get("dataset_id") != training_input.parent("dataset")
        or training_input.parent("protocol") != protocol_id
    ):
        raise BoundaryError("evaluation", "sealed_test_protocol_mismatch")


def _validate_model_view_lineage(
    store: ArtifactStore, model: Manifest, view_id: str, view: Manifest
) -> Manifest:
    if model.kind != "model" or model.parameters.value().get("schema") != MODEL_SCHEMA:
        raise BoundaryError("evaluation", "model_contract_mismatch")
    external_test = model.parent("model_view") != view_id
    if external_test:
        held_out = store.get_manifest(view.parent("dataset"))
        if held_out.parameters.value().get("purpose") not in {"test", "gold"}:
            raise BoundaryError("evaluation", "model_view_identity_mismatch")
    from ..workers.contracts import load_training_input

    training_input, config, features = load_training_input(
        store, model.parent("training_input"), model.producer
    )
    if external_test:
        from .dataset_policy import check_dataset_pair, training_sources

        # Include initialization/checkpoint ancestors, not just the latest named
        # training set. Separate held-out views never weaken the model's own lineage.
        for source in training_sources(store, model.artifact_id):
            check_dataset_pair(store, source.artifact_id, view.parent("dataset"))
    run = store.get_manifest(model.parent("run"))
    checkpoint = store.get_manifest(model.parent("checkpoint"))
    if (
        run.kind != "run"
        or run.producer != model.producer
        or run.parent("training_input") != training_input.artifact_id
        or checkpoint.kind != "checkpoint"
        or checkpoint.producer != model.producer
        or checkpoint.parent("run") != run.artifact_id
        or checkpoint.parent("training_input") != training_input.artifact_id
        or model.parameters.value().get("head") != config.head
        or model.parameters.value().get("hidden_size") != features.matrix.shape[1]
    ):
        raise BoundaryError("evaluation", "model_checkpoint_lineage_mismatch")
    model_parameters = model.parameters.value()
    view_parameters = view.parameters.value()
    if (
        training_input.kind != "training_input"
        or training_input.parent("model_view") != model.parent("model_view")
        or training_input.parameters.value().get("qwen") != model_parameters.get("qwen")
        or model_parameters.get("scope") != view_parameters.get("scope")
        or model_parameters.get("serializer") != view_parameters.get("serializer")
        or {payload.role for payload in model.payloads} != {"weights"}
    ):
        raise BoundaryError("evaluation", "model_view_identity_mismatch")
    for _ in store.read_payload(model.payload("weights")):
        pass
    return training_input


def _validate_rows(
    expected_samples: tuple[ModelSample, ...],
    rows: list[dict[str, Any]],
    partition: str,
) -> None:
    expected = {
        sample.transition_id: sample for sample in expected_samples if sample.split == partition
    }
    if any(not isinstance(row, dict) for row in rows):
        raise BoundaryError("evaluation", "row_schema_mismatch")
    transition_ids = [row.get("transition_id") for row in rows]
    if (
        any(not isinstance(value, str) for value in transition_ids)
        or len(set(transition_ids)) != len(rows)
        or set(transition_ids) != set(expected)
    ):
        raise BoundaryError("evaluation", "row_inventory_mismatch")
    for row in rows:
        if set(row) != EVALUATION_COLUMNS:
            raise BoundaryError("evaluation", "row_schema_mismatch")
        sample = expected.get(row["transition_id"])
        if sample is None or any(
            row[field] != getattr(sample, field)
            for field in ("run_id", "surface", "family", "split")
        ):
            raise BoundaryError("evaluation", "row_input_mismatch")
        if type(row["candidate_count"]) is not int or row["candidate_count"] < 1:
            raise BoundaryError("evaluation", "invalid_candidate_count")
        if row["candidate_count"] != len(sample.action_texts):
            raise BoundaryError("evaluation", "candidate_alignment_mismatch")
        metrics = tuple(row[name] for name in ("top1", "mrr", "nll", "confidence", "margin"))
        if any(type(value) not in {int, float} or not math.isfinite(value) for value in metrics):
            raise BoundaryError("evaluation", "invalid_metric")
        if (
            not 0 <= row["top1"] <= 1
            or not 0 <= row["mrr"] <= 1
            or not 0 <= row["confidence"] <= 1
            or row["nll"] < 0
            or row["margin"] < 0
        ):
            raise BoundaryError("evaluation", "metric_out_of_range")


def publish_evaluation(
    store: ArtifactStore,
    model: Manifest,
    view_id: str,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    producer: Producer,
    *,
    partition: str,
    baseline: str = "model",
    seed: int,
    bootstrap: int,
    protocol_id: str | None = None,
) -> Manifest:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if baseline not in BASELINES:
        raise BoundaryError("evaluation", "unknown_baseline")
    if model.producer != producer:
        raise BoundaryError("evaluation", "model_producer_mismatch")
    if partition not in {"dev", "test"}:
        raise BoundaryError("evaluation", "invalid_evaluation_partition")
    if partition == "test" and protocol_id is None:
        raise BoundaryError("evaluation", "sealed_test_protocol_required")
    view, expected_samples = load_model_view(store, view_id)
    if view_id != model.parent("model_view") and partition != "test":
        raise BoundaryError("evaluation", "external_test_requires_sealed_partition")
    training_input = _validate_model_view_lineage(store, model, view_id, view)
    if partition == "test":
        assert protocol_id is not None
        _validate_protocol(store, protocol_id, view_id, training_input)
    elif protocol_id is not None:
        raise BoundaryError("evaluation", "protocol_only_for_sealed_test")
    _validate_rows(expected_samples, rows, partition)
    if summary != summarize_rows(rows, seed=seed, bootstrap=bootstrap):
        raise BoundaryError("evaluation", "summary_mismatch")

    with tempfile.TemporaryFile("w+b") as handle:
        pq.write_table(pa.Table.from_pylist(rows), handle, compression="zstd", version="2.6")
        handle.seek(0)
        row_payload = store.put_payload("metrics", handle, "application/vnd.apache.parquet")
    summary_payload = store.put_payload(
        "summary", io.BytesIO(json_bytes(summary)), "application/json"
    )
    parents = [Parent("model", model.artifact_id), Parent("model_view", view_id)]
    if protocol_id is not None:
        parents.append(Parent("protocol", protocol_id))
    manifest = Manifest(
        "offline_evaluation",
        producer,
        tuple(parents),
        (row_payload, summary_payload),
        FrozenObject.of(
            {
                "schema": EVALUATION_SCHEMA,
                "partition": partition,
                "baseline": baseline,
                "tie_policy": "uniform_within_equal_score_blocks",
                "rows": len(rows),
                "scope": model.parameters.value()["scope"],
                "seed": seed,
                "bootstrap": bootstrap,
                "protocol": protocol_id,
                "scientific_verdict": "not_claimed",
            }
        ),
    )
    store.publish(manifest)
    return manifest


def load_evaluation(
    store: ArtifactStore, evaluation_id: str, *, permit_test: bool = False
) -> LoadedEvaluation:
    """Load and verify an immutable evaluation and its complete input lineage."""
    import pyarrow.parquet as pq

    manifest = store.get_manifest(evaluation_id)
    parameters = manifest.parameters.value()
    if manifest.kind != "offline_evaluation" or parameters.get("schema") != EVALUATION_SCHEMA:
        raise BoundaryError("evaluation", "unsupported_contract")
    partition = parameters.get("partition")
    baseline = parameters.get("baseline")
    if partition not in {"dev", "test"} or baseline not in BASELINES:
        raise BoundaryError("evaluation", "invalid_contract_parameters")
    expected_roles = {"model", "model_view"}
    protocol_id = parameters.get("protocol")
    if partition == "test":
        if not permit_test or not isinstance(protocol_id, str):
            raise BoundaryError("evaluation", "sealed_test_requires_explicit_protocol_admission")
        expected_roles.add("protocol")
    elif protocol_id is not None:
        raise BoundaryError("evaluation", "protocol_only_for_sealed_test")
    if {parent.role for parent in manifest.parents} != expected_roles:
        raise BoundaryError("evaluation", "parent_inventory_mismatch")
    if {payload.role for payload in manifest.payloads} != {"metrics", "summary"}:
        raise BoundaryError("evaluation", "payload_inventory_mismatch")
    model = store.get_manifest(manifest.parent("model"))
    view, samples = load_model_view(store, manifest.parent("model_view"))
    if view.artifact_id != model.parent("model_view") and partition != "test":
        raise BoundaryError("evaluation", "external_test_requires_sealed_partition")
    training_input = _validate_model_view_lineage(store, model, view.artifact_id, view)
    if partition == "test":
        assert isinstance(protocol_id, str)
        _validate_protocol(store, protocol_id, view.artifact_id, training_input)
    if manifest.producer != model.producer or parameters.get("scientific_verdict") != "not_claimed":
        raise BoundaryError("evaluation", "producer_or_verdict_mismatch")
    if partition == "test" and manifest.parent("protocol") != protocol_id:
        raise BoundaryError("evaluation", "protocol_parent_mismatch")
    if manifest.payload("summary").size > 16 * 1024 * 1024:
        raise BoundaryError("evaluation", "summary_size_limit")
    summary_raw = b"".join(store.read_payload(manifest.payload("summary")))
    summary = decode_json(summary_raw)
    if not isinstance(summary, dict) or summary_raw != json_bytes(summary):
        raise BoundaryError("evaluation", "summary_integrity_mismatch")
    seed = unsigned(parameters.get("seed"), "evaluation.seed")
    bootstrap = unsigned(parameters.get("bootstrap"), "evaluation.bootstrap")
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryFile("w+b") as handle:
        for chunk in store.read_payload(manifest.payload("metrics")):
            handle.write(chunk)
        handle.seek(0)
        parquet = pq.ParquetFile(handle)
        if set(parquet.schema_arrow.names) != EVALUATION_COLUMNS:
            raise BoundaryError("evaluation", "metrics_columns_mismatch")
        for batch in parquet.iter_batches(batch_size=1024):
            rows.extend(batch.to_pylist())
    _validate_rows(tuple(samples), rows, partition)
    if summary != summarize_rows(rows, seed=seed, bootstrap=bootstrap):
        raise BoundaryError("evaluation", "summary_mismatch")
    if parameters.get("rows") != len(rows) or parameters.get(
        "scope"
    ) != view.parameters.value().get("scope"):
        raise BoundaryError("evaluation", "evaluation_identity_mismatch")
    return LoadedEvaluation(manifest, model, view, tuple(rows), summary)
