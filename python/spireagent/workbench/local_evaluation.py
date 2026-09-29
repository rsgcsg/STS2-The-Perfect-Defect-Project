"""Read one producer-recorded report without opening its research lineage."""

from __future__ import annotations

import math
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json, digest, object_fields
from spireagent.storage.store import ManifestArtifactStore
from stpd.fullrun.decision_training import VIEW_SCHEMA as DECISION_VIEW_SCHEMA
from stpd.fullrun.evaluation import EVALUATION_COLUMNS
from stpd.fullrun.evaluation import EVALUATION_SCHEMA as FULLRUN_SCHEMA
from stpd.fullrun.evaluation import MODEL_SCHEMA as FULLRUN_MODEL_SCHEMA
from stpd.fullrun.features import VIEW_SCHEMA as FULLRUN_VIEW_SCHEMA
from stpd.fullrun.public_bc import LEGACY_VIEW_SCHEMA as LEGACY_PUBLIC_BC_VIEW_SCHEMA
from stpd.fullrun.public_bc import VIEW_SCHEMA as PUBLIC_BC_VIEW_SCHEMA
from stpd.models.stage1a import RECIPES
from stpd.workers.memory_evaluation import EVALUATION_SCHEMA as MEMORY_SCHEMA
from stpd.workers.token_worker import EVALUATION_SCHEMA as TOKEN_SCHEMA

SCHEMA = "stpd/local-offline-evaluation-summary-v1"
SCOPE = "recorded_report_and_parent_identities"
METRICS = ("top1", "mrr", "nll", "confidence", "margin")
MAX_SUMMARY = 16 * 1024 * 1024
MAX_TOKEN_METRICS = 64 * 1024 * 1024
HUMAN_VIEW_SCHEMAS = frozenset({
    "stpd/human-text-input-bc-view-v1", "stpd/human-text-input-bc-view-v2",
})
TOKEN_VIEW_SCHEMAS = frozenset({
    DECISION_VIEW_SCHEMA, LEGACY_PUBLIC_BC_VIEW_SCHEMA, PUBLIC_BC_VIEW_SCHEMA,
    "stpd/text-menu-bc-view-v1",
}) | HUMAN_VIEW_SCHEMAS
# FullRun feature compilation supports these two views, even though the shared
# view loader also serves other model families.
FULLRUN_VIEW_SCHEMAS = frozenset({FULLRUN_VIEW_SCHEMA, DECISION_VIEW_SCHEMA})


def _finite(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise BoundaryError("local_evaluation", "invalid_report_structure")
    if isinstance(value, dict):
        for child in value.values():
            _finite(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _finite(child, depth + 1)
    elif type(value) is float and not math.isfinite(value):
        raise BoundaryError("local_evaluation", "nonfinite_report_metric")


def _overall(value: object) -> dict[str, int | float]:
    if not isinstance(value, dict) or set(value) != {"count", *METRICS}:
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    count = value["count"]
    if type(count) is not int or count < 1:
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    for name in METRICS:
        metric = value[name]
        if type(metric) not in {int, float}:
            raise BoundaryError("local_evaluation", "invalid_report_summary")
        try:
            finite = math.isfinite(float(metric))
        except OverflowError:
            finite = False
        if (not finite or metric < 0
                or name in {"top1", "mrr", "confidence"} and metric > 1):
            raise BoundaryError("local_evaluation", "invalid_report_summary")
    return {name: value[name] for name in ("count", *METRICS)}


def _report(value: object, *, human_input: bool = False
            ) -> tuple[dict[str, int | float], int, int]:
    if not isinstance(value, dict):
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    _finite(value)
    overall = _overall(value.get("overall"))
    bootstrap = value.get("bootstrap")
    groups = value.get("by_candidate_count")
    if not isinstance(bootstrap, dict) or not isinstance(groups, dict):
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    if human_input and (
        set(bootstrap) != {"status", "reason", "unit", "reported_run_groups"}
        or bootstrap["status"] != "unknown"
        or bootstrap["reason"] != "native_run_independence_unknown_across_sessions"
        or bootstrap["unit"] != "session_scoped_run_group"
    ):
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    runs = bootstrap.get("reported_run_groups" if human_input else "runs")
    if type(runs) is not int or runs < 1:
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    multiple = total = 0
    for key, group in groups.items():
        if (not isinstance(key, str) or not key.isascii() or not key.isdecimal()
                or len(key) > 9 or int(key) < 1):
            raise BoundaryError("local_evaluation", "invalid_report_summary")
        count = int(_overall(group)["count"])
        total += count
        if int(key) > 1:
            multiple += count
    if total != overall["count"] or runs > overall["count"]:
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    return overall, runs, multiple


def _public(manifest: Any, model: Any, view: Any, report: object, *,
            baseline: str, baselines: object = None) -> dict[str, Any]:
    human_input = view.parameters.value().get("schema") in HUMAN_VIEW_SCHEMAS
    overall, runs, multiple = _report(report, human_input=human_input)
    config = model.parameters.value().get("config")
    recipe = config.get("recipe") if isinstance(config, dict) else None
    if recipe is not None and (not isinstance(recipe, str) or recipe not in RECIPES):
        raise BoundaryError("local_evaluation", "invalid_report_model")
    result = {
        "schema": SCHEMA,
        "validation_scope": SCOPE,
        "evaluation_id": manifest.artifact_id,
        "evaluation_schema": manifest.parameters.value()["schema"],
        "model_id": model.artifact_id,
        "model_view_id": view.artifact_id,
        "model_recipe": recipe,
        "view_schema": view.parameters.value().get("schema"),
        "partition": "dev",
        "baseline": baseline,
        "qualification": ("engineering_only" if manifest.parameters.value()["schema"]
                          == TOKEN_SCHEMA else "not_claimed"),
        "scientific_verdict": "not_claimed",
        "decision_count": overall["count"],
        "reported_run_groups": runs,
        "multi_candidate_count": multiple,
        "overall": overall,
        "interpretation": "producer_recorded_summary_not_full_lineage_or_quality_verification",
    }
    if human_input:
        result.update(grouping="session_scoped_run_group",
                      native_run_independence="unknown_across_sessions")
    if baselines is not None:
        if not isinstance(baselines, dict) or set(baselines) != {"uniform_legal", "action_only"}:
            raise BoundaryError("local_evaluation", "invalid_report_baselines")
        reports = {name: _report(value, human_input=human_input)[0]
                   for name, value in sorted(baselines.items())}
        if any(value["count"] != overall["count"] for value in reports.values()):
            raise BoundaryError("local_evaluation", "invalid_report_baselines")
        result["baselines"] = reports
    return result


def _parents(store: ManifestArtifactStore, manifest: Any) -> tuple[Any, Any]:
    if (manifest.kind != "offline_evaluation" or manifest.parameters.value().get("partition")
            != "dev" or sorted(parent.role for parent in manifest.parents)
            != ["model", "model_view"]):
        raise BoundaryError("local_evaluation", "invalid_report_parentage")
    model = store.get_manifest(manifest.parent("model"))
    view = store.get_manifest(manifest.parent("model_view"))
    if (model.kind != "model" or view.kind != "model_view"
            or model.producer != manifest.producer
            or model.parent("model_view") != view.artifact_id):
        raise BoundaryError("local_evaluation", "invalid_report_parentage")
    return model, view


def _payload(store: ManifestArtifactStore, manifest: Any, role: str, limit: int) -> Any:
    payload = manifest.payload(role)
    if payload.size > limit:
        raise BoundaryError("local_evaluation", "report_size_limit")
    return decode_json(b"".join(store.read_payload(payload)))


def _token_summary(store: ManifestArtifactStore, manifest: Any) -> dict[str, Any]:
    if (manifest.parameters.value().get("qualification") != "engineering_only"
            or [payload.role for payload in manifest.payloads] != ["metrics"]):
        raise BoundaryError("local_evaluation", "unsupported_token_evaluation")
    model, view = _parents(store, manifest)
    if (model.parameters.value().get("schema") != "stpd/stage1a-model-v1"
            or view.parameters.value().get("schema") not in TOKEN_VIEW_SCHEMAS
            or model.parameters.value().get("serializer")
            != view.parameters.value().get("serializer")
            or sorted(parent.role for parent in model.parents)
            != ["checkpoint", "model_view", "run", "training_input"]):
        raise BoundaryError("local_evaluation", "invalid_report_parentage")
    run = store.get_manifest(model.parent("run"))
    training_input = store.get_manifest(model.parent("training_input"))
    run_config = run.parameters.value().get("config")
    if (run.kind != "run" or run.parameters.value().get("schema") != "stpd/stage1a-run-v1"
            or run.producer != model.producer
            or training_input.kind != "training_input"
            or training_input.parameters.value().get("schema") != "stpd/stage1a-token-input-v1"
            or [parent.role for parent in training_input.parents] != ["model_view"]
            or training_input.parent("model_view") != view.artifact_id
            or not isinstance(run_config, dict)
            or model.parent("training_input") != run.parent("training_input")
            or model.parameters.value().get("config") != run_config
            or model.parameters.value().get("steps") != run_config.get("steps")):
        raise BoundaryError("local_evaluation", "invalid_report_parentage")
    value = object_fields(_payload(store, manifest, "metrics", MAX_TOKEN_METRICS),
                          {"rows", "summary", "baselines"}, "local_evaluation.metrics")
    _finite(value)
    if not isinstance(value["rows"], list):
        raise BoundaryError("local_evaluation", "invalid_report_structure")
    for row in value["rows"]:
        if (not isinstance(row, dict) or set(row) != EVALUATION_COLUMNS
                or row.get("split") != "dev"
                or type(row.get("candidate_count")) is not int
                or row["candidate_count"] < 1
                or any(not isinstance(row.get(key), str) for key in
                       ("transition_id", "run_id", "surface", "family"))):
            raise BoundaryError("local_evaluation", "invalid_report_structure")
        _overall({"count": 1, **{key: row[key] for key in METRICS}})
    result = _public(manifest, model, view, value["summary"], baseline="model",
                     baselines=value["baselines"])
    if result["decision_count"] != len(value["rows"]):
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    return result


def summary(store: ManifestArtifactStore, evaluation_id: str) -> dict[str, Any]:
    """Open one recorded report; full typed revalidation remains a separate owner path."""
    manifest = store.get_manifest(digest(evaluation_id, "local_evaluation.artifact_id"))
    schema = manifest.parameters.value().get("schema")
    if manifest.kind != "offline_evaluation" or schema not in {
        FULLRUN_SCHEMA, TOKEN_SCHEMA, MEMORY_SCHEMA,
    }:
        raise BoundaryError("local_evaluation", "unsupported_evaluation")
    if manifest.parameters.value().get("partition") != "dev":
        raise BoundaryError("local_evaluation", "sealed_test_evaluation")
    if schema == TOKEN_SCHEMA:
        return _token_summary(store, manifest)
    if schema == MEMORY_SCHEMA:
        if (sorted(parent.role for parent in manifest.parents)
                != ["evaluation_input", "model", "source"]
                or [payload.role for payload in manifest.payloads] != ["metrics"]
                or manifest.parameters.value().get("qualification") != "engineering_only"
                or manifest.parameters.value().get("scientific_verdict") != "not_claimed"
                or manifest.parameters.value().get("native_run_independence")
                != "unknown_across_sessions"):
            raise BoundaryError("local_evaluation", "invalid_memory_report")
        model = store.get_manifest(manifest.parent("model"))
        evaluation_input = store.get_manifest(manifest.parent("evaluation_input"))
        if (model.kind != "model"
                or model.parameters.value().get("schema") != "stpd/experimental-m2-model-v1"
                or evaluation_input.kind != "analysis"
                or evaluation_input.parameters.value().get("schema")
                != "stpd/experimental-m2-evaluation-input-v1"
                or evaluation_input.parent("model") != model.artifact_id
                or evaluation_input.parent("source") != manifest.parent("source")
                or model.producer != manifest.producer
                or evaluation_input.producer != manifest.producer):
            raise BoundaryError("local_evaluation", "invalid_memory_report")
        recorded = object_fields(
            _payload(store, manifest, "metrics", MAX_TOKEN_METRICS),
            {"rows", "summary"}, "local_evaluation.memory_metrics",
        )
        if not isinstance(recorded["rows"], list):
            raise BoundaryError("local_evaluation", "invalid_report_structure")
        for row in recorded["rows"]:
            if (not isinstance(row, dict) or set(row) != EVALUATION_COLUMNS
                    or row.get("split") != "dev"
                    or type(row.get("candidate_count")) is not int
                    or row["candidate_count"] < 1
                    or any(not isinstance(row.get(key), str) or not row[key]
                           for key in ("transition_id", "run_id", "surface", "family"))):
                raise BoundaryError("local_evaluation", "invalid_report_structure")
            _overall({"count": 1, **{key: row[key] for key in METRICS}})
        overall, runs, multiple = _report(recorded["summary"], human_input=True)
        if (len(recorded["rows"]) != manifest.parameters.value().get("rows")
                or len(recorded["rows"]) != overall["count"]):
            raise BoundaryError("local_evaluation", "invalid_report_summary")
        return {
            "schema": SCHEMA, "validation_scope": SCOPE,
            "evaluation_id": manifest.artifact_id, "evaluation_schema": schema,
            "model_id": model.artifact_id, "evaluation_input_id": evaluation_input.artifact_id,
            "dev_source_id": manifest.parent("source"), "partition": "dev",
            "baseline": "model", "qualification": "engineering_only",
            "scientific_verdict": "not_claimed", "decision_count": overall["count"],
            "reported_run_groups": runs, "multi_candidate_count": multiple,
            "overall": overall, "grouping": "session_scoped_run_group",
            "native_run_independence": "unknown_across_sessions",
            "interpretation": "producer_recorded_summary_not_full_lineage_or_quality_verification",
        }
    if ({payload.role for payload in manifest.payloads} != {"metrics", "summary"}
            or manifest.parameters.value().get("scientific_verdict") != "not_claimed"
            or manifest.parameters.value().get("baseline")
            not in {"model", "uniform_legal", "action_only"}):
        raise BoundaryError("local_evaluation", "unsupported_evaluation")
    model, view = _parents(store, manifest)
    if (model.parameters.value().get("schema") != FULLRUN_MODEL_SCHEMA
            or view.parameters.value().get("schema") not in FULLRUN_VIEW_SCHEMAS):
        raise BoundaryError("local_evaluation", "invalid_report_parentage")
    recorded = _payload(store, manifest, "summary", MAX_SUMMARY)
    result = _public(manifest, model, view, recorded,
                     baseline=manifest.parameters.value()["baseline"])
    if manifest.parameters.value().get("rows") != result["decision_count"]:
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    return result
