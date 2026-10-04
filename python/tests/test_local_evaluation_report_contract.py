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
    evidence_change: str | None = None,
    physical_claim: object | None = None,
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
    if evidence_change == "historical_restrictions":
        metrics["qualification_evidence"] = [{
            "origin": "e" * 64,
            "facts": {
                "split_basis": "whole_run_and_duplicate_current_input",
                "human_origin": "explicit_owner_attestation_not_machine_verifiable",
                "isolation": "ordinary",
                "sealed_test": False,
                "semantic_overlap": True,
                "ledger_scope": "published_model",
                "qualified_run_ids": ["private-run-id-a", "private-run-id-b"],
            },
        }]

    parameters: dict[str, Any] = {
        "schema": TOKEN_EVALUATION_SCHEMA,
        "partition": "dev",
        "qualification": "engineering_only",
    }
    if contract in {"legacy-admission", "qualified"}:
        parameters.update(ADMISSION)
    if physical_claim is not None:
        metrics["admission"]["physical_game_independence"] = physical_claim
        parameters["physical_game_independence"] = physical_claim
    if contract == "qualified":
        parameters["native_run_independence"] = False
    if metadata_change == "independent":
        parameters["native_run_independence"] = True
    elif metadata_change == "admission_mismatch":
        parameters["physical_game_independence"] = "independent"
    elif metadata_change == "historical_restrictions":
        parameters.update({
            "split_basis": "report_split_basis",
            "human_origin": "report_human_origin",
            "isolation": "report_isolation",
            "sealed_test": False,
            "semantic_overlap": False,
            "ledger_scope": "report_ledger_scope",
            "qualified_run_ids": ["report-private-id"],
        })

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
    assert result["dev_qualification"]["native_run_independence"] == "unknown"
    assert result["dev_qualification"]["reported_restrictions"][
        "physical_game_independence"]["status"] == "unreported"
    assert result["dev_qualification"]["physical_game_independence"] == "unresolved"


def test_legacy_admission_report_remains_readable_as_unknown(tmp_path: Path) -> None:
    store, identity = _recorded_report(tmp_path / "legacy-admission", contract="legacy-admission")
    result = summary(store, identity)
    assert result["decision_count"] == 1
    assert result["reported_run_groups"] == 1
    qualification = result["dev_qualification"]
    assert qualification["native_run_independence"] == "unknown"
    assert qualification["evaluation_scope"] == ADMISSION["evaluation_scope"]
    assert qualification["historical_external_exposure"] == "unknown"
    assert qualification["physical_game_independence"] == "unresolved"
    assert qualification["reported_restrictions"][
        "physical_game_independence"]["status"] == "reported_unresolved"
    assert qualification["clean_held_out_claim"] is False


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
    qualification = result["dev_qualification"]
    assert qualification["native_run_independence"] == "unknown"
    assert qualification["evaluation_scope"] == ADMISSION["evaluation_scope"]
    assert qualification["historical_external_exposure"] == "unknown"
    assert qualification["physical_game_independence"] == "unresolved"
    assert qualification["reported_restrictions"][
        "physical_game_independence"]["status"] == "reported_unresolved"
    assert qualification["clean_held_out_claim"] is False


@pytest.mark.parametrize("fact,expected", [
    (True, "unverified_reported_facts"),
    ("proven_independent", "unverified_reported_facts"),
    (False, False),
    ("shared_physical_game", "shared_physical_game"),
])
def test_physical_independence_facts_do_not_promote_positive_claims(
    tmp_path: Path, fact: object, expected: object,
) -> None:
    store, identity = _recorded_report(
        tmp_path / "positive-physical-claim",
        physical_claim=fact,
    )
    qualification = summary(store, identity)["dev_qualification"]
    value = qualification["physical_game_independence"]
    if expected == "unverified_reported_facts":
        assert value["status"] == expected
    else:
        assert value == expected
    status = qualification["reported_restrictions"][
        "physical_game_independence"]["status"]
    assert status == ("unverified_reported_facts"
                      if expected == "unverified_reported_facts"
                      else "reported_non_independence")
    physical_claims = qualification["reported_restrictions"][
        "physical_game_independence"]["claims"]
    assert {claim["origin"] for claim in physical_claims} == {identity, "report:admission"}
    assert {item["value"] for item in physical_claims} == {fact}


def test_prior_evidence_and_manifest_restrictions_keep_origins_and_redact_run_ids(
    tmp_path: Path,
) -> None:
    store, identity = _recorded_report(
        tmp_path / "historical-restrictions",
        evidence_change="historical_restrictions",
        metadata_change="historical_restrictions",
    )
    qualification = summary(store, identity)["dev_qualification"]
    split_claims = qualification["reported_restrictions"]["split_basis"]["claims"]
    assert {claim["origin"]: claim["value"] for claim in split_claims} == {
        identity: "report_split_basis",
        "e" * 64: "whole_run_and_duplicate_current_input",
    }
    assert {claim["origin"]: claim["value"] for claim in
            qualification["reported_restrictions"]["semantic_overlap"]["claims"]} == {
        identity: False, "e" * 64: True,
    }
    run_id_claims = qualification["reported_restrictions"]["qualified_run_ids"]["claims"]
    assert {claim["origin"]: claim["value"] for claim in run_id_claims} == {
        identity: {"redacted": True, "count": 1},
        "e" * 64: {"redacted": True, "count": 2},
    }
    encoded = json.dumps(qualification)
    assert "private-run-id" not in encoded
    assert "report-private-id" not in encoded
    assert "transition_id" not in encoded
    assert "dev-run" not in encoded
    assert "a" * 64 not in encoded


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
