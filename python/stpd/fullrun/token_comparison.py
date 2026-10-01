"""Paired engineering comparisons over verified complete dev inputs."""
from __future__ import annotations

import json
import math
from statistics import mean
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from ..workers.token_worker import _load_inputs, _verify_completed
from .dataset_policy import token_dev_qualification
from .features import ModelSample
from .view_session import verified_model_views

METRICS = ("top1", "nll", "mrr")


def _sample_commitment(samples: tuple[ModelSample, ...], split: str) -> list[dict[str, Any]]:
    # Normalize decision order for identity alignment; retain every candidate's order.
    return [s.to_dict() for s in sorted(samples, key=lambda s: s.transition_id)
            if s.split == split]


def compare_token_results(
    store: ArtifactStore, identities: list[str], *, comparison_mode: str = "same-view",
) -> dict[str, Any]:
    """First result is the reference; fixed-dev is an explicit input equality gate."""
    if comparison_mode not in {"same-view", "fixed-dev"}:
        raise BoundaryError("token_comparison", "unsupported_comparison_mode")
    if len(identities) < 2 or len(set(identities)) != len(identities):
        raise BoundaryError("token_comparison", "distinct_completed_results_required")
    models: list[dict[str, Any]] = []
    aligned: list[dict[str, dict[str, Any]]] = []
    view_ids: list[str] = []
    common_dev: list[dict[str, Any]] | None = None
    with verified_model_views(store):
        for identity in identities:
            result = store.get_manifest(identity)
            run = store.get_manifest(result.parent("run"))
            _verify_completed(store, result, run)
            model = store.get_manifest(result.parent("model"))
            report = store.get_manifest(result.parent("offline_evaluation"))
            view_id = model.parent("model_view")
            inputs = _load_inputs(store, result.parent("training_input"))
            if (report.parent("model_view") != view_id
                    or inputs.manifest.parent("model_view") != view_id
                    or report.parameters.value().get("partition") != "dev"):
                raise BoundaryError("token_comparison", "report_model_input_view_mismatch")
            if comparison_mode == "same-view" and view_ids and view_ids[0] != view_id:
                raise BoundaryError("token_comparison", "same_fixed_dev_view_required")
            view_ids.append(view_id)
            view = store.get_manifest(view_id)
            samples = inputs.samples
            dev = tuple(s for s in samples if s.split == "dev")
            expected = {s.transition_id: s for s in dev}
            if not dev or len(expected) != len(dev):
                raise BoundaryError("token_comparison", "complete_unique_dev_samples_required")
            commitment = _sample_commitment(samples, "dev")
            if common_dev is not None and common_dev != commitment:
                raise BoundaryError("token_comparison", "identical_complete_dev_inputs_required")
            common_dev = commitment
            payload = report.payload("metrics")
            if payload.size > 64 * 1024**2:
                raise BoundaryError("token_comparison", "evaluation_size_limit")
            metrics = json.loads(b"".join(store.read_payload(payload)))
            rows = metrics.get("rows")
            if (not isinstance(rows, list)
                    or any(not isinstance(r, dict) or not isinstance(r.get("transition_id"), str)
                           for r in rows)):
                raise BoundaryError("token_comparison", "invalid_dev_rows")
            keyed = {r["transition_id"]: r for r in rows}
            if len(keyed) != len(rows) or set(keyed) != set(expected):
                raise BoundaryError("token_comparison", "complete_unique_dev_rows_required")
            for key, row in keyed.items():
                sample = expected[key]
                if (row.get("split") != "dev" or row.get("run_id") != sample.run_id
                        or row.get("surface") != sample.surface
                        or row.get("family") != sample.family
                        or type(row.get("candidate_count")) is not int
                        or row["candidate_count"] != len(sample.action_keys)):
                    raise BoundaryError("token_comparison", "dev_row_identity_mismatch")
                for metric in METRICS:
                    value = row.get(metric)
                    if (type(value) not in (float, int) or not math.isfinite(value)
                            or value < 0 or metric != "nll" and value > 1):
                        raise BoundaryError("token_comparison", "invalid_metric")
            input_info = inputs.manifest.parameters.value()
            codec_roles = {p.role for p in inputs.manifest.payloads} & {
                "tokenizer", "state_tokenizer", "action_codec",
            }
            if any(model.payload(role) != inputs.manifest.payload(role) for role in codec_roles):
                raise BoundaryError("token_comparison", "model_input_codec_mismatch")
            tokenizer_role = "state_tokenizer" if "state_tokenizer" in codec_roles else "tokenizer"
            tokenizer = inputs.manifest.payload(tokenizer_role)
            renderer = (input_info["source_renderer"] if "source_renderer" in input_info
                        else view.parameters.value()["serializer"])
            qualification = token_dev_qualification(store, view, report=report, metrics=metrics)
            multiple = [r for r in rows if r["candidate_count"] > 1]
            by_run: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                by_run.setdefault(row["run_id"], []).append(row)
            grouped_means = {m: mean(mean(r[m] for r in group)
                                     for group in by_run.values()) for m in METRICS}
            models.append({
                "result_id": identity, "run_id": run.artifact_id,
                "model_id": model.artifact_id, "evaluation_id": report.artifact_id,
                "model_view_id": view_id, "training_input_id": inputs.manifest.artifact_id,
                "source_revision": model.producer.source_revision,
                "config": model.parameters.value()["config"],
                "steps": model.parameters.value()["steps"],
                "train_row_count": sum(s.split == "train" for s in samples),
                "train_inputs_sha256": semantic_hash(_sample_commitment(samples, "train")),
                "dev_inputs_sha256": semantic_hash(commitment),
                "renderer": renderer,
                "input_format": input_info["format"],
                "tokenizer": {
                    "sha256": tokenizer.sha256, "size": tokenizer.size,
                    "fit_scope": input_info.get("fit_scope", input_info.get("state_codec", {}).get(
                        "fit_scope")),
                    "codec": input_info.get("state_codec"),
                },
                "backbone": model.parameters.value()["backbone"],
                "action_codec": input_info.get("action_codec"),
                "dev_qualification": qualification,
                "attempt_seconds": result.parameters.value()["attempt_seconds"],
                "decision_count": len(rows), "multi_candidate_count": len(multiple),
                "reported_run_groups": len(by_run),
                "native_run_independence": "unknown_across_sessions",
                "reported_run_group_weighted": grouped_means,
                "decision_weighted": {m: mean(r[m] for r in rows) for m in METRICS},
                "multi_candidate": ({m: mean(r[m] for r in multiple) for m in METRICS}
                                    if multiple else None),
            })
            aligned.append(keyed)
    reference = aligned[0]
    training_changed = len({m["train_inputs_sha256"] for m in models}) > 1
    tokenizer_changed = len({m["tokenizer"]["sha256"] for m in models}) > 1
    return {
        "schema": "stpd/stage1a-comparison-v2", "qualification": "engineering_only",
        "comparison_mode": comparison_mode,
        "model_view_id": view_ids[0] if len(set(view_ids)) == 1 else None,
        "model_view_ids": view_ids, "dev_inputs_sha256": semantic_hash(common_dev),
        "partition": "dev", "models": models,
        "training_data_changed": training_changed, "tokenizer_changed": tokenizer_changed,
        "train_only_tokenizer_covaries": training_changed and tokenizer_changed and any(
            m["tokenizer"]["fit_scope"] == "train_only" for m in models),
        "paired_delta_from_first": [
            {"result_id": identity, **{
                m: mean(rows[key][m] - reference[key][m] for key in reference)
                for m in METRICS}}
            for identity, rows in zip(identities[1:], aligned[1:], strict=True)
        ],
        "interpretation": "descriptive_paired_comparison_not_causal_or_quality_verdict",
        "cost_scope": "one_recorded_attempt_including_evaluation_not_total_research_cost",
    }
