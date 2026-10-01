"""Pure reported-fact merging and bounded token evaluation projection."""

from __future__ import annotations

import re
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes

TOKEN_QUALIFICATION_FACT_KEYS = (
    "native_run_independence",
    "physical_game_independence",
    "historical_external_exposure",
    "clean_held_out_claim",
    "scientific_verdict",
    "evaluation_scope",
    "split_basis",
    "human_origin",
    "isolation",
    "sealed_test",
    "semantic_overlap",
    "ledger_scope",
    "qualified_run_ids",
)
TOKEN_ADMISSION_FACT_KEYS = frozenset({
    "evaluation_scope", "historical_external_exposure",
    "physical_game_independence", "clean_held_out_claim",
})
BOOTSTRAP_FACT_KEYS = frozenset({"status", "reason", "unit", "runs", "reported_run_groups"})
_PROJECTED_STATUSES = frozenset({
    "conflicting_reported_facts", "unverified_reported_facts",
})
_KNOWN_PHYSICAL_NON_INDEPENDENCE = frozenset({
    "shared_physical_game", "not_independent", "non_independent",
})
MAX_TOKEN_QUALIFICATION_EVIDENCE = 1024
MAX_TOKEN_QUALIFICATION_FACTS = 32
MAX_TOKEN_QUALIFICATION_ORIGIN = 512
_SAFE_STATIC_ORIGINS = frozenset({
    "current_dev_admission", "report:admission", "report:bootstrap",
})


def _safe_origin(value: object) -> bool:
    return isinstance(value, str) and len(value) <= MAX_TOKEN_QUALIFICATION_ORIGIN and (
        value in _SAFE_STATIC_ORIGINS
        or re.fullmatch(r"[0-9a-f]{64}(?::lineage)?", value) is not None
    )


def _projected_claims(value: object) -> list[dict[str, Any]] | None:
    if not isinstance(value, dict) or set(value) != {"status", "claims"}:
        return None
    if (not isinstance(value["status"], str)
            or value["status"] not in _PROJECTED_STATUSES
            or not isinstance(value["claims"], list)):
        raise BoundaryError("token_qualification", "invalid_projected_claims")
    claims = []
    for item in value["claims"]:
        if not isinstance(item, dict) or set(item) != {"origin", "value"}:
            raise BoundaryError("token_qualification", "invalid_projected_claims")
        if not _safe_origin(item["origin"]):
            raise BoundaryError("token_qualification", "invalid_projected_claims")
        claims.append(item)
    return claims


def reported_claims(
    evidence: list[dict[str, Any]], key: str, *, unknown: object = None,
) -> list[dict[str, Any]]:
    """Collect direct or previously projected claims without source access."""
    claims: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    for item in evidence:
        if (not isinstance(item, dict) or set(item) != {"origin", "facts"}
                or not _safe_origin(item.get("origin"))
                or not isinstance(item.get("facts"), dict)):
            raise BoundaryError("token_qualification", "invalid_evidence")
        if key not in item["facts"]:
            continue
        value = item["facts"][key]
        projected = _projected_claims(value)
        candidates = projected if projected is not None else [
            {"origin": item["origin"], "value": value}
        ]
        for claim in candidates:
            claim_value = claim["value"]
            if (claim_value is None or unknown is not None
                    and (claim_value == unknown or claim_value == "unknown")):
                continue
            encoded = json_bytes(claim)
            if encoded not in seen:
                seen.add(encoded)
                claims.append(claim)
    return claims


def known_physical_non_independence(value: object) -> bool:
    """Only explicit negative facts are recognized; every other value stays unverified."""
    return value is False or (
        isinstance(value, str) and value in _KNOWN_PHYSICAL_NON_INDEPENDENCE
    )


def merge_qualification_fact(
    evidence: list[dict[str, Any]], key: str, unknown: object,
) -> Any:
    claims = reported_claims(evidence, key, unknown=unknown)
    if not claims:
        return unknown
    values = {json_bytes(claim["value"]) for claim in claims}
    if len(values) > 1:
        return {"status": "conflicting_reported_facts", "claims": claims}
    if key == "physical_game_independence" and any(
        not known_physical_non_independence(claim["value"]) for claim in claims
    ):
        return {"status": "unverified_reported_facts", "claims": claims}
    return claims[0]["value"]


def consumer_physical_independence(evidence: list[dict[str, Any]]) -> Any:
    """Keep the producer's unresolved default while refusing affirmative proof."""
    value = merge_qualification_fact(evidence, "physical_game_independence", "unresolved")
    if (value == "unresolved" or known_physical_non_independence(value)
            or isinstance(value, dict) and value.get("status") in _PROJECTED_STATUSES):
        return value
    claims = reported_claims(evidence, "physical_game_independence", unknown="unresolved")
    if not claims:
        return "unresolved"
    return {"status": "unverified_reported_facts", "claims": claims}


def _safe_qualification_value(key: str, value: object) -> Any:
    projected = _projected_claims(value)
    if projected is not None:
        return {
            "status": value["status"],
            "claims": [
                {"origin": claim["origin"], "value": _safe_qualification_value(key, claim["value"])}
                for claim in projected
            ],
        }
    if key == "qualified_run_ids":
        if (isinstance(value, dict) and set(value) == {"redacted", "count"}
                and value["redacted"] is True
                and type(value["count"]) is int and value["count"] >= 0):
            return {"redacted": True, "count": value["count"]}
        count = len(value) if isinstance(value, list) else 0 if value is None else 1
        return {"redacted": True, "count": count}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise BoundaryError("token_qualification", "unsupported_fact_value")


def _safe_evidence_item(origin: str, facts: object) -> dict[str, Any]:
    if (not _safe_origin(origin) or not isinstance(facts, dict)
            or len(facts) > MAX_TOKEN_QUALIFICATION_FACTS):
        raise BoundaryError("token_qualification", "invalid_evidence")
    if origin == "report:bootstrap":
        # Bootstrap intervals and row-derived summaries are outside the Workbench detail contract.
        selected = {key: facts[key] for key in BOOTSTRAP_FACT_KEYS if key in facts}
        return {"origin": origin, "facts": selected}
    if set(facts) - set(TOKEN_QUALIFICATION_FACT_KEYS):
        raise BoundaryError("token_qualification", "unknown_qualification_fact")
    return {
        "origin": origin,
        "facts": {key: _safe_qualification_value(key, value)
                  for key, value in facts.items()},
    }


def project_token_report_qualification(
    report_id: str, parameters: dict[str, Any], metrics: dict[str, Any],
) -> dict[str, Any]:
    """Project recorded token restrictions for Workbench without reading source lineage."""
    if (not _safe_origin(report_id)
            or not isinstance(parameters, dict) or not isinstance(metrics, dict)):
        raise BoundaryError("token_qualification", "invalid_report_input")
    evidence: list[dict[str, Any]] = []

    prior = metrics.get("qualification_evidence", [])
    if not isinstance(prior, list):
        raise BoundaryError("token_qualification", "invalid_evidence")
    for item in prior:
        if not isinstance(item, dict) or set(item) != {"origin", "facts"}:
            raise BoundaryError("token_qualification", "invalid_evidence")
        evidence.append(_safe_evidence_item(item["origin"], item["facts"]))

    report_facts = {key: parameters[key] for key in TOKEN_QUALIFICATION_FACT_KEYS
                    if key in parameters}
    if report_facts:
        evidence.append(_safe_evidence_item(report_id, report_facts))

    admission = metrics.get("admission")
    if admission is not None:
        if not isinstance(admission, dict) or set(admission) != TOKEN_ADMISSION_FACT_KEYS:
            raise BoundaryError("token_qualification", "invalid_admission")
        evidence.append(_safe_evidence_item("report:admission", admission))

    summary = metrics.get("summary")
    if not isinstance(summary, dict):
        raise BoundaryError("token_qualification", "invalid_report_summary")
    bootstrap = summary.get("bootstrap")
    if bootstrap is not None:
        if not isinstance(bootstrap, dict):
            raise BoundaryError("token_qualification", "invalid_report_summary")
        evidence.append(_safe_evidence_item("report:bootstrap", bootstrap))

    if len(evidence) > MAX_TOKEN_QUALIFICATION_EVIDENCE:
        raise BoundaryError("token_qualification", "evidence_limit")
    # Collapse only byte-identical records; distinct origins remain visible.
    unique: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    for item in evidence:
        encoded = json_bytes(item)
        if encoded not in seen:
            seen.add(encoded)
            unique.append(item)
    unique.sort(key=lambda item: (item["origin"], json_bytes(item["facts"])))

    defaults = {
        "evaluation_scope": "unknown",
        "historical_external_exposure": "unknown",
        "physical_game_independence": "unresolved",
    }
    restrictions: dict[str, Any] = {}
    for key in TOKEN_QUALIFICATION_FACT_KEYS:
        claims = reported_claims(unique, key)
        if not claims:
            restrictions[key] = {"status": "unreported", "claims": []}
            continue
        distinct = {json_bytes(claim["value"]) for claim in claims}
        if key == "physical_game_independence":
            active_claims = reported_claims(unique, key, unknown="unresolved")
            active_distinct = {json_bytes(claim["value"]) for claim in active_claims}
            if not active_claims:
                status = "reported_unresolved"
            elif len(active_distinct) > 1:
                status = "conflicting_reported_facts"
            elif any(not known_physical_non_independence(claim["value"])
                     for claim in active_claims):
                status = "unverified_reported_facts"
            else:
                status = "reported_non_independence"
        elif key == "native_run_independence":
            status = "unverified_reported_facts"
        else:
            status = "conflicting_reported_facts" if len(distinct) > 1 else "reported_facts"
        restrictions[key] = {"status": status, "claims": claims}

    return {
        "native_run_independence": "unknown",
        "evaluation_scope": merge_qualification_fact(
            unique, "evaluation_scope", defaults["evaluation_scope"]
        ),
        "historical_external_exposure": merge_qualification_fact(
            unique, "historical_external_exposure", defaults["historical_external_exposure"]
        ),
        "physical_game_independence": consumer_physical_independence(unique),
        "clean_held_out_claim": False,
        "reported_restrictions": restrictions,
        "evidence": unique,
    }
