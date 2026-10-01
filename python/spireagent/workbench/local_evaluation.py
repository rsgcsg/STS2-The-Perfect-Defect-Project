"""Read one producer-recorded report without opening its research lineage."""

from __future__ import annotations

import math
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json, digest, object_fields
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.memory_recipe import recorded_memory_recipe
from stpd.fullrun.decision_training import VIEW_SCHEMA as DECISION_VIEW_SCHEMA
from stpd.fullrun.evaluation import EVALUATION_COLUMNS
from stpd.fullrun.evaluation import EVALUATION_SCHEMA as FULLRUN_SCHEMA
from stpd.fullrun.evaluation import MODEL_SCHEMA as FULLRUN_MODEL_SCHEMA
from stpd.fullrun.features import VIEW_SCHEMA as FULLRUN_VIEW_SCHEMA
from stpd.fullrun.public_bc import LEGACY_VIEW_SCHEMA as LEGACY_PUBLIC_BC_VIEW_SCHEMA
from stpd.fullrun.public_bc import VIEW_SCHEMA as PUBLIC_BC_VIEW_SCHEMA
from stpd.stage1a_recipes import RECIPES
from stpd.workers.report_schemas import (
    MEMORY_EVALUATION_PROTOCOL as MEMORY_PROTOCOL,
)
from stpd.workers.report_schemas import (
    MEMORY_EVALUATION_SCHEMA as MEMORY_SCHEMA,
)
from stpd.workers.report_schemas import (
    TOKEN_EVALUATION_SCHEMA as TOKEN_SCHEMA,
)

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
LEGACY_TOKEN_METRICS_FIELDS = frozenset({"rows", "summary", "baselines"})
LEGACY_ADMITTED_TOKEN_METRICS_FIELDS = LEGACY_TOKEN_METRICS_FIELDS | {"admission"}
QUALIFIED_TOKEN_METRICS_FIELDS = LEGACY_TOKEN_METRICS_FIELDS | {
    "admission", "qualification_evidence",
}
TOKEN_ADMISSION_FIELDS = frozenset({
    "evaluation_scope", "historical_external_exposure",
    "physical_game_independence", "clean_held_out_claim",
})
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


def _report(value: object, *, human_input: bool = False,
            unknown_run_independence: bool = False,
            expected_run_groups: int | None = None,
            ) -> tuple[dict[str, int | float], int, int]:
    if not isinstance(value, dict):
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    _finite(value)
    overall = _overall(value.get("overall"))
    bootstrap = value.get("bootstrap")
    groups = value.get("by_candidate_count")
    if not isinstance(bootstrap, dict) or not isinstance(groups, dict):
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    requires_unknown = human_input or unknown_run_independence
    if requires_unknown and (
        set(bootstrap) != {"status", "reason", "unit", "reported_run_groups"}
        or bootstrap["status"] != "unknown"
        or bootstrap["reason"] != "native_run_independence_unknown_across_sessions"
        or bootstrap["unit"] != "session_scoped_run_group"
    ):
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    runs = bootstrap.get("reported_run_groups" if requires_unknown else "runs")
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
    if (total != overall["count"] or runs > overall["count"]
            or (expected_run_groups is not None and runs != expected_run_groups)):
        raise BoundaryError("local_evaluation", "invalid_report_summary")
    return overall, runs, multiple


def _public(manifest: Any, model: Any, view: Any, report: object, *,
            baseline: str, baselines: object = None,
            unknown_run_independence: bool = False,
            expected_run_groups: int | None = None,
            dev_qualification: dict[str, Any] | None = None) -> dict[str, Any]:
    human_input = view.parameters.value().get("schema") in HUMAN_VIEW_SCHEMAS
    overall, runs, multiple = _report(
        report, human_input=human_input,
        unknown_run_independence=unknown_run_independence,
        expected_run_groups=expected_run_groups,
    )
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
    if dev_qualification is not None:
        result["dev_qualification"] = dev_qualification
    if baselines is not None:
        if not isinstance(baselines, dict) or set(baselines) != {"uniform_legal", "action_only"}:
            raise BoundaryError("local_evaluation", "invalid_report_baselines")
        reports = {name: _report(
            value, human_input=human_input,
            unknown_run_independence=unknown_run_independence,
            expected_run_groups=expected_run_groups,
        )[0]
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


def _token_qualification(
    manifest: Any, metrics: dict[str, Any], *, human_input: bool,
) -> tuple[bool, dict[str, Any]]:
    """Validate report-local qualification without reopening source lineage."""
    parameters = manifest.parameters.value()
    native_independence = parameters.get("native_run_independence")
    physical_independence = parameters.get("physical_game_independence")
    scientific_verdict = parameters.get("scientific_verdict")
    if (native_independence is True
            or physical_independence is True
            or isinstance(physical_independence, str)
            and physical_independence in {"independent", "independent_runs"}
            or "clean_held_out_claim" in parameters
            and parameters["clean_held_out_claim"] is not False
            or scientific_verdict is not None and scientific_verdict != "not_claimed"):
        raise BoundaryError("local_evaluation", "contradictory_token_qualification")

    fields = frozenset(metrics)
    if fields in {LEGACY_TOKEN_METRICS_FIELDS, LEGACY_ADMITTED_TOKEN_METRICS_FIELDS}:
        if "native_run_independence" in parameters and native_independence is not False:
            raise BoundaryError("local_evaluation", "contradictory_token_qualification")
        has_admission = fields == LEGACY_ADMITTED_TOKEN_METRICS_FIELDS
        admission = _token_admission(manifest, metrics["admission"]) if has_admission else None
        return (human_input or native_independence is False or has_admission,
                _dev_qualification(admission))
    if fields != QUALIFIED_TOKEN_METRICS_FIELDS:
        object_fields(metrics, set(LEGACY_TOKEN_METRICS_FIELDS), "local_evaluation.metrics")
        raise BoundaryError("local_evaluation", "invalid_token_metrics_contract")

    admission = _token_admission(manifest, metrics["admission"])
    evidence = metrics["qualification_evidence"]
    if not isinstance(evidence, list):
        raise BoundaryError("local_evaluation", "invalid_token_qualification_evidence")
    for item in evidence:
        record = object_fields(item, {"origin", "facts"},
                               "local_evaluation.token_qualification_evidence")
        if (not isinstance(record["origin"], str) or not record["origin"]
                or not isinstance(record["facts"], dict)):
            raise BoundaryError("local_evaluation", "invalid_token_qualification_evidence")
    if native_independence is not False:
        raise BoundaryError("local_evaluation", "contradictory_token_qualification")
    return True, _dev_qualification(admission)


def _dev_qualification(admission: dict[str, Any] | None) -> dict[str, Any]:
    value: dict[str, Any] = {"native_run_independence": "unknown"}
    if admission is not None:
        value.update(admission)
    return value


def _token_admission(manifest: Any, value: object) -> dict[str, Any]:
    admission = object_fields(value, set(TOKEN_ADMISSION_FIELDS),
                              "local_evaluation.token_admission")
    parameters = manifest.parameters.value()
    for key in TOKEN_ADMISSION_FIELDS - {"clean_held_out_claim"}:
        fact = admission[key]
        if isinstance(fact, dict):
            projection = object_fields(fact, {"status", "claims"},
                                       "local_evaluation.token_admission_fact")
            if (projection["status"] not in (
                    "conflicting_reported_facts", "unverified_reported_facts",
            ) or not isinstance(projection["claims"], list)):
                raise BoundaryError("local_evaluation", "invalid_token_admission")
            for claim in projection["claims"]:
                record = object_fields(claim, {"origin", "value"},
                                       "local_evaluation.token_admission_claim")
                if not isinstance(record["origin"], str) or not record["origin"]:
                    raise BoundaryError("local_evaluation", "invalid_token_admission")
        elif not isinstance(fact, str) and type(fact) is not bool:
            raise BoundaryError("local_evaluation", "invalid_token_admission")
    physical_independence = admission["physical_game_independence"]
    if (physical_independence is True
            or isinstance(physical_independence, str)
            and physical_independence in {"independent", "independent_runs"}
            or admission["clean_held_out_claim"] is not False
            or any(parameters.get(key) != admission[key]
                   for key in TOKEN_ADMISSION_FIELDS)):
        raise BoundaryError("local_evaluation", "contradictory_token_qualification")
    return admission


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
    value = _payload(store, manifest, "metrics", MAX_TOKEN_METRICS)
    if not isinstance(value, dict):
        raise BoundaryError("local_evaluation", "invalid_token_metrics_contract")
    view_schema = view.parameters.value().get("schema")
    unknown_run_independence, dev_qualification = _token_qualification(
        manifest, value, human_input=view_schema in HUMAN_VIEW_SCHEMAS,
    )
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
    run_groups = len({row["run_id"] for row in value["rows"]})
    result = _public(manifest, model, view, value["summary"], baseline="model",
                     baselines=value["baselines"],
                     unknown_run_independence=unknown_run_independence,
                     expected_run_groups=run_groups,
                     dev_qualification=dev_qualification)
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
                or manifest.parameters.value().get("native_run_independence") is not False
                or manifest.parameters.value().get("protocol") != MEMORY_PROTOCOL
                or manifest.parameters.value().get("strict_deduplicated_benchmark") is not False
                or type(manifest.parameters.value().get("semantic_overlap"))
                not in {type(None), bool}
                or manifest.parameters.value().get("model_selection_exposure") != "unknown"
                or type(manifest.parameters.value().get("train_dev_rendered_overlap_count"))
                is not int
                or manifest.parameters.value()["train_dev_rendered_overlap_count"] < 0):
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
                or evaluation_input.parameters.value().get("operation_id")
                != manifest.parameters.value().get("operation_id")
                or evaluation_input.parameters.value().get("protocol") != MEMORY_PROTOCOL
                or evaluation_input.parameters.value().get("semantic_overlap")
                != manifest.parameters.value().get("semantic_overlap")
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
            "model_recipe": recorded_memory_recipe(store, model),
            "evaluation_input_schema": evaluation_input.parameters.value()["schema"],
            "dev_source_id": manifest.parent("source"), "partition": "dev",
            "baseline": "model", "qualification": "engineering_only",
            "scientific_verdict": "not_claimed", "decision_count": overall["count"],
            "reported_run_groups": runs, "multi_candidate_count": multiple,
            "overall": overall, "grouping": "session_scoped_run_group",
            "protocol": MEMORY_PROTOCOL, "native_run_independence": False,
            "strict_deduplicated_benchmark": False,
            "semantic_overlap": manifest.parameters.value()["semantic_overlap"],
            "train_dev_rendered_overlap_count": manifest.parameters.value()[
                "train_dev_rendered_overlap_count"],
            "model_selection_exposure": "unknown",
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
