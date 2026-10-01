"""Purpose and exact-content checks also enforced by offline research entry points."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, decode_json
from spireagent.storage.store import ArtifactStore

from .contracts import ResearchTransitionV1
from .token_qualification import (
    TOKEN_QUALIFICATION_FACT_KEYS,
    merge_qualification_fact,
)


def training_sources(store: ArtifactStore, identity: str) -> tuple[Manifest, ...]:
    """Walk model/checkpoint ancestry too: a wrapper cannot relabel held-out data."""
    pending = [identity]
    found: dict[str, Manifest] = {}
    while pending:
        current = pending.pop()
        if current in found:
            continue
        if len(found) >= 512:
            raise BoundaryError("curation", "lineage_limit")
        item = store.get_manifest(current)
        found[current] = item
        if item.kind == "dataset" and item.parameters.value().get("purpose") in {"test", "gold"}:
            raise BoundaryError("curation", "held_out_data_cannot_train")
        pending.extend(parent.artifact_id for parent in item.parents)
    return tuple(item for item in found.values() if item.kind == "dataset")


def token_dev_qualification(
    store: ArtifactStore, view: Manifest, *, report: Manifest | None = None,
    metrics: dict[str, Any] | None = None, admission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project existing restrictions; no current token source proves physical independence.

    Allocation isolation and distinct recorded run IDs are not that proof. Preserve
    source/report statements with provenance, including historical summaries that
    assumed independence, without changing their immutable artifacts or adopting
    their assumptions as a fresh qualification.
    """
    evidence: list[dict[str, Any]] = []

    def retain(origin: str, value: Any) -> None:
        if not isinstance(value, dict):
            raise BoundaryError("curation", "invalid_dev_qualification")
        facts = {key: value[key] for key in TOKEN_QUALIFICATION_FACT_KEYS if key in value}
        if facts:
            evidence.append({"origin": origin, "facts": facts})

    for source in sorted(training_sources(store, view.artifact_id),
                         key=lambda item: item.artifact_id):
        retain(source.artifact_id, source.parameters.value())
    retain(view.artifact_id, view.parameters.value())
    # Human view loaders already verify their lineage projection byte for byte.
    for payload in view.payloads:
        if payload.role == "lineage":
            if payload.size > 64 * 1024**2:
                raise BoundaryError("curation", "lineage_size_limit")
            value = decode_json(b"".join(store.read_payload(payload)))
            if isinstance(value, dict):
                retain(view.artifact_id + ":lineage", value)
    if report is not None:
        retain(report.artifact_id, report.parameters.value())
    if metrics is not None:
        retain("report:admission", metrics.get("admission", {}))
        summary = metrics.get("summary", {})
        if not isinstance(summary, dict):
            raise BoundaryError("curation", "invalid_dev_qualification")
        bootstrap = summary.get("bootstrap")
        if bootstrap is not None:
            if not isinstance(bootstrap, dict):
                raise BoundaryError("curation", "invalid_dev_qualification")
            evidence.append({"origin": "report:bootstrap", "facts": bootstrap})
        prior_evidence = metrics.get("qualification_evidence", [])
        if not isinstance(prior_evidence, list):
            raise BoundaryError("curation", "invalid_dev_qualification")
        for item in prior_evidence:
            if (not isinstance(item, dict) or not isinstance(item.get("origin"), str)
                    or not isinstance(item.get("facts"), dict)):
                raise BoundaryError("curation", "invalid_dev_qualification")
            retain(item["origin"], item["facts"])
    if admission is not None:
        retain("current_dev_admission", admission)

    return {
        "native_run_independence": False,
        "evaluation_scope": merge_qualification_fact(
            evidence, "evaluation_scope", "engineering_dev_from_model_view"
        ),
        "historical_external_exposure": merge_qualification_fact(
            evidence, "historical_external_exposure", "unknown"
        ),
        "physical_game_independence": merge_qualification_fact(
            evidence, "physical_game_independence", "unresolved"
        ),
        "clean_held_out_claim": False,
        "evidence": evidence,
    }


def check_dataset_pair(store: ArtifactStore, training: str, test: str) -> dict[str, object]:
    from .curated_dataset import SCHEMA as CURATED_SCHEMA
    from .curated_dataset import load_selection
    from .data import DATASET_SCHEMA, load_dataset
    from .decision_store import load
    from .representation import decision_fingerprint

    def records(identity: str) -> Sequence[ResearchTransitionV1]:
        manifest = store.get_manifest(identity)
        schema = manifest.parameters.value().get("schema")
        if schema == CURATED_SCHEMA:
            return load_selection(store, manifest, cache=None).records
        if schema == DATASET_SCHEMA:
            return load_dataset(store, identity)[1].records
        return load(store, identity)[1].records

    training_sources(store, training)
    left = records(training)
    left_runs = {r.run_id for r in left}
    left_facts = {decision_fingerprint(r) for r in left}
    repeated_runs = set()
    repeated_facts = set()
    for record in records(test):
        if record.run_id in left_runs:
            repeated_runs.add(record.run_id)
        fact = decision_fingerprint(record)
        if fact in left_facts:
            repeated_facts.add(fact)
    if repeated_runs or repeated_facts:
        raise BoundaryError("curation", "training_test_overlap")
    return {"overlap": False, "basis": "whole_run_and_duplicate_decisions"}
