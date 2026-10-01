"""Recorded token report compatibility checks that do not construct an ML worker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_artifact_store_v1 import PRODUCER

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.local_evaluation import summary
from stpd.workers.report_schemas import TOKEN_EVALUATION_SCHEMA

ADMISSION = {
    "evaluation_scope": "engineering_dev_from_model_view",
    "historical_external_exposure": "unknown",
    "physical_game_independence": "unresolved",
    "clean_held_out_claim": False,
}
METRICS = {
    "count": 1,
    "top1": 1.0,
    "mrr": 1.0,
    "nll": 0.25,
    "confidence": 0.75,
    "margin": 0.5,
}


def _unknown_summary() -> dict[str, Any]:
    return {
        "overall": dict(METRICS),
        "by_candidate_count": {"2": dict(METRICS)},
        "bootstrap": {
            "status": "unknown",
            "reason": "native_run_independence_unknown_across_sessions",
            "unit": "session_scoped_run_group",
            "reported_run_groups": 1,
        },
    }


def _legacy_summary() -> dict[str, Any]:
    value = _unknown_summary()
    value["bootstrap"] = {
        "status": "insufficient_independent_runs_or_replicates",
        "unit": "whole_run",
        "runs": 1,
    }
    return value


def _recorded_report(
    root: Path,
    *,
    contract: str = "qualified",
    metric_change: str | None = None,
    metadata_change: str | None = None,
) -> tuple[ManifestArtifactStore, str]:
    store = ManifestArtifactStore(LocalBlobStore(root))
    view = Manifest(
        "model_view",
        PRODUCER,
        parameters=FrozenObject.of(
            {
                "schema": "stpd/public-observation-bc-view-v2",
                "serializer": "renderer-v1",
            }
        ),
    )
    store.publish(view)
    training_input = Manifest(
        "training_input",
        PRODUCER,
        parents=(Parent("model_view", view.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/stage1a-token-input-v1"}),
    )
    store.publish(training_input)
    config = {"steps": 1}
    run = Manifest(
        "run",
        PRODUCER,
        parents=(Parent("training_input", training_input.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/stage1a-run-v1", "config": config}),
    )
    store.publish(run)
    checkpoint = Manifest("checkpoint", PRODUCER)
    store.publish(checkpoint)
    model = Manifest(
        "model",
        PRODUCER,
        parents=(
            Parent("checkpoint", checkpoint.artifact_id),
            Parent("model_view", view.artifact_id),
            Parent("run", run.artifact_id),
            Parent("training_input", training_input.artifact_id),
        ),
        parameters=FrozenObject.of(
            {
                "schema": "stpd/stage1a-model-v1",
                "serializer": "renderer-v1",
                "config": config,
                "steps": 1,
            }
        ),
    )
    store.publish(model)

    bootstrap = _unknown_summary() if contract != "legacy-three" else _legacy_summary()
    metrics: dict[str, Any] = {
        "rows": [
            {
                "transition_id": "a" * 64,
                "run_id": "dev-run",
                "surface": "text_menu",
                "family": "observed_input",
                "split": "dev",
                "candidate_count": 2,
                **{
                    key: METRICS[key]
                    for key in (
                        "top1",
                        "mrr",
                        "nll",
                        "confidence",
                        "margin",
                    )
                },
            }
        ],
        "summary": bootstrap,
        "baselines": {
            "uniform_legal": json.loads(json.dumps(bootstrap)),
            "action_only": json.loads(json.dumps(bootstrap)),
        },
    }
    if contract in {"legacy-admission", "qualified"}:
        metrics["admission"] = dict(ADMISSION)
    if contract == "qualified":
        metrics["qualification_evidence"] = []
    if metric_change == "extra_field":
        metrics["unrecognized"] = "must be rejected"
    elif metric_change == "admission_extra":
        metrics["admission"]["unrecognized"] = "must be rejected"
    elif metric_change == "admission_independent":
        metrics["admission"]["physical_game_independence"] = "independent"
    elif metric_change == "computed_bootstrap":
        for report in (metrics["summary"], *metrics["baselines"].values()):
            report["bootstrap"] = {
                "status": "computed",
                "unit": "whole_run",
                "runs": 1,
            }

    parameters: dict[str, Any] = {
        "schema": TOKEN_EVALUATION_SCHEMA,
        "partition": "dev",
        "qualification": "engineering_only",
    }
    if contract in {"legacy-admission", "qualified"}:
        parameters.update(ADMISSION)
    if contract == "qualified":
        parameters["native_run_independence"] = False
    if metadata_change == "independent":
        parameters["native_run_independence"] = True
    elif metadata_change == "admission_mismatch":
        parameters["physical_game_independence"] = "independent"

    payload = store.put_bytes("metrics", json_bytes(metrics), "application/json")
    report = Manifest(
        "offline_evaluation",
        PRODUCER,
        parents=(Parent("model", model.artifact_id), Parent("model_view", view.artifact_id)),
        payloads=(payload,),
        parameters=FrozenObject.of(parameters),
    )
    store.publish(report)
    return store, report.artifact_id


def test_legacy_three_field_token_report_remains_readable_without_independence_claim(
    tmp_path: Path,
) -> None:
    store, identity = _recorded_report(tmp_path / "legacy-three", contract="legacy-three")
    result = summary(store, identity)
    assert result["decision_count"] == 1
    assert result["reported_run_groups"] == 1
    assert result["dev_qualification"] == {"native_run_independence": "unknown"}
    assert "independent_runs" not in json.dumps(result)


def test_legacy_admission_report_remains_readable_as_unknown(tmp_path: Path) -> None:
    store, identity = _recorded_report(tmp_path / "legacy-admission", contract="legacy-admission")
    result = summary(store, identity)
    assert result["decision_count"] == 1
    assert result["reported_run_groups"] == 1
    assert result["dev_qualification"] == {
        "native_run_independence": "unknown",
        **ADMISSION,
    }
    assert "independent_runs" not in json.dumps(result)


def test_qualified_public_report_and_baselines_are_read_without_source_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, identity = _recorded_report(tmp_path / "qualified")
    roles: list[str] = []
    original_read = store.read_payload

    def report_only(payload):
        roles.append(payload.role)
        yield from original_read(payload)

    monkeypatch.setattr(store, "read_payload", report_only)
    result = summary(store, identity)
    assert roles == ["metrics"]
    assert result["decision_count"] == 1
    assert result["reported_run_groups"] == 1
    assert set(result["baselines"]) == {"uniform_legal", "action_only"}
    assert result["dev_qualification"] == {
        "native_run_independence": "unknown",
        **ADMISSION,
    }
    assert "independent_runs" not in json.dumps(result)


@pytest.mark.parametrize(
    ("contract", "metric_change", "metadata_change"),
    [
        ("qualified", "extra_field", None),
        ("qualified", "admission_extra", None),
        ("qualified", "admission_independent", None),
        ("qualified", "computed_bootstrap", None),
        ("qualified", None, "independent"),
        ("qualified", None, "admission_mismatch"),
        ("legacy-three", "extra_field", None),
    ],
)
def test_token_report_rejects_unknown_fields_and_contradictory_qualification(
    tmp_path: Path,
    contract: str,
    metric_change: str | None,
    metadata_change: str | None,
) -> None:
    store, identity = _recorded_report(
        tmp_path / "invalid",
        contract=contract,
        metric_change=metric_change,
        metadata_change=metadata_change,
    )
    with pytest.raises(BoundaryError):
        summary(store, identity)
