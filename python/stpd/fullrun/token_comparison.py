"""Paired engineering comparisons of completed token runs on the same fixed dev view."""
from __future__ import annotations

import json
import math
from statistics import mean
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ArtifactStore

from ..workers.token_worker import _verify_completed
from .features import load_model_view
from .view_session import verified_model_views

METRICS = ("top1", "nll", "mrr")


def compare_token_results(store: ArtifactStore, identities: list[str]) -> dict[str, Any]:
    """First result is the reference; return a projection, never a new quality claim."""
    if len(identities) < 2 or len(set(identities)) != len(identities):
        raise BoundaryError("token_comparison", "distinct_completed_results_required")
    models: list[dict[str, Any]] = []
    aligned: list[dict[str, dict[str, Any]]] = []
    common_view: str | None = None
    with verified_model_views(store):
        for identity in identities:
            result = store.get_manifest(identity)
            run = store.get_manifest(result.parent("run"))
            _verify_completed(store, result, run)
            model = store.get_manifest(result.parent("model"))
            report = store.get_manifest(result.parent("offline_evaluation"))
            view_id = model.parent("model_view")
            if (report.parent("model_view") != view_id
                    or report.parameters.value().get("partition") != "dev"
                    or common_view is not None and common_view != view_id):
                raise BoundaryError("token_comparison", "same_fixed_dev_view_required")
            common_view = view_id
            _, samples = load_model_view(store, view_id)
            expected = {s.transition_id: s for s in samples if s.split == "dev"}
            metrics = json.loads(b"".join(store.read_payload(report.payload("metrics"))))
            rows = metrics["rows"]
            keyed = {r["transition_id"]: r for r in rows}
            if not expected or len(keyed) != len(rows) or set(keyed) != set(expected):
                raise BoundaryError("token_comparison", "complete_unique_dev_rows_required")
            for key, row in keyed.items():
                sample = expected[key]
                if (row["split"] != "dev" or row["run_id"] != sample.run_id
                        or row["candidate_count"] != len(sample.action_keys)):
                    raise BoundaryError("token_comparison", "dev_row_identity_mismatch")
                for metric in METRICS:
                    value = row[metric]
                    if (type(value) not in (float, int) or not math.isfinite(value)
                            or value < 0 or metric != "nll" and value > 1):
                        raise BoundaryError("token_comparison", "invalid_metric")
            multiple = [r for r in rows if r["candidate_count"] > 1]
            by_run: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                by_run.setdefault(row["run_id"], []).append(row)
            models.append({
                "result_id": identity, "run_id": run.artifact_id,
                "model_id": model.artifact_id, "evaluation_id": report.artifact_id,
                "source_revision": model.producer.source_revision,
                "config": model.parameters.value()["config"],
                "attempt_seconds": result.parameters.value()["attempt_seconds"],
                "decision_count": len(rows), "multi_candidate_count": len(multiple),
                "independent_runs": len(by_run),
                "decision_weighted": {m: mean(r[m] for r in rows) for m in METRICS},
                "run_weighted": {m: mean(mean(r[m] for r in group)
                                         for group in by_run.values()) for m in METRICS},
                "multi_candidate": ({m: mean(r[m] for r in multiple) for m in METRICS}
                                    if multiple else None),
            })
            aligned.append(keyed)
    reference = aligned[0]
    return {
        "schema": "stpd/stage1a-comparison-v1", "qualification": "engineering_only",
        "model_view_id": common_view, "partition": "dev", "models": models,
        "paired_delta_from_first": [
            {"result_id": identity, **{
                m: mean(rows[key][m] - reference[key][m] for key in reference)
                for m in METRICS}}
            for identity, rows in zip(identities[1:], aligned[1:], strict=True)
        ],
        "interpretation": "descriptive_paired_comparison_not_causal_or_quality_verdict",
        "cost_scope": "one_recorded_attempt_including_evaluation_not_total_research_cost",
    }
