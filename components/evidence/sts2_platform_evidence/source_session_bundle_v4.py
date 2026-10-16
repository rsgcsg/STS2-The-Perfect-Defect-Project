"""Strict Source4 adapter and independent neutral-original integrity checks."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from .core import VerificationResult, VerifierDescriptor
from .source_session_bundle import _require, _object, _identifier, _json
from .source_session_bundle_v2 import SourceSessionBundleV2Verifier, _SourceFormat, _text
from .source_session_bundle_v3 import SourceSessionBundleV3

PUBLIC_BASIS_PROFILE = {
    "schema": "sts2.player-environment/public-basis-1", "profile": "public-basis-1", "revision": "1",
    "capture_scope": "current_public_snapshot_and_advertised_read_instances",
}


def _complete(value: Any) -> bool:
    return isinstance(value, dict) and value.get("status") == "complete" and value.get("missing") == []


def validate_public_basis(data: bytes, snapshot_id: str, environment: Mapping[str, Any]) -> None:
    root = _object(_json(data))
    _require(set(root) == {"schema", "revision", "capture_scope", "capture_status", "snapshot", "reads", "missing"}, "source_public_basis_invalid")
    _require(all(root.get(key) == PUBLIC_BASIS_PROFILE[key] for key in ("schema", "revision", "capture_scope")), "source_public_basis_invalid")
    snapshot = _object(root["snapshot"])
    _require(snapshot.get("schema") == "sts2.player-environment/snapshot-1" and snapshot.get("snapshot_id") == snapshot_id
             and snapshot.get("protocol_version") == environment["player_environment_protocol"]
             and _object(snapshot.get("session")).get("runtime_instance_id") == environment["runtime_instance_id"]
             and snapshot["session"].get("environment_fingerprint") == environment["environment_fingerprint"]
             and _object(snapshot.get("information_policy")).get("includes_hidden_information") is False, "source_public_basis_invalid")
    opportunities, captured = snapshot.get("reads"), root["reads"]
    _require(isinstance(opportunities, list) and isinstance(captured, list) and len(opportunities) <= 10_000
             and len(opportunities) == len(captured), "source_public_basis_invalid")
    advertised = {}
    for value in opportunities:
        value = _object(value); read_id = value.get("read_id")
        _require(isinstance(read_id, str) and bool(read_id) and read_id not in advertised, "source_public_basis_invalid")
        advertised[read_id] = value
    seen = set(); missing = []
    for value in captured:
        value = _object(value)
        _require(set(value) == {"read_id", "kind", "response", "error"}, "source_public_basis_invalid")
        read_id = value["read_id"]
        _require(isinstance(read_id, str) and read_id in advertised and read_id not in seen, "source_public_basis_invalid")
        seen.add(read_id); opportunity = advertised[read_id]
        _require(value["kind"] == opportunity["kind"], "source_public_basis_invalid")
        if value["response"] is None:
            _require(isinstance(value["error"], str) and bool(value["error"]), "source_public_basis_invalid")
            missing.append("read:" + read_id + ":" + value["error"]); continue
        response = _object(value["response"])
        _require(value["error"] is None and response.get("schema") == "sts2.player-environment/read-1"
                 and response.get("protocol_version") == environment["player_environment_protocol"]
                 and response.get("read_id") == read_id and response.get("kind") == value["kind"]
                 and response.get("expected_snapshot_id") == snapshot_id and response.get("observed_snapshot_id") == snapshot_id
                 and response.get("session") == snapshot.get("session")
                 and response.get("information_policy") == snapshot.get("information_policy"), "source_public_basis_invalid")
        _require(all(response.get(field) == opportunity.get(field) for field in
                     ("target_referent_id", "content_schema", "ordering_semantics", "visibility_basis"))
                 and _object(response.get("completeness")).get("hidden_by_policy") == opportunity.get("hidden_by_policy"), "source_public_basis_invalid")
        if not _complete(response["completeness"]): missing.append("read:" + read_id + ":partial")
    if not _complete(snapshot.get("completeness")): missing.append("snapshot:partial")
    _require(root["missing"] == missing and root["capture_status"] == ("partial" if missing else "complete"), "source_public_basis_invalid")


def validate_native_facts(value: Any) -> None:
    value = _object(value)
    _require(set(value) == {"origin", "owner_ref", "subject_ref", "arguments", "carriers", "missing_reason"}
             and value["origin"] in {"native_input", "protocol_input", "missing"}, "source_native_facts_invalid")
    for field in ("owner_ref", "subject_ref"):
        if value[field] is not None: _identifier(value[field])
    arguments = _object(value["arguments"]); carriers = value["carriers"]
    _require(len(arguments) <= 32 and isinstance(carriers, list) and len(carriers) <= 8, "source_native_facts_invalid")
    for role, reference in arguments.items(): _text(role); _identifier(reference)
    seen = set()
    for carrier in carriers:
        carrier = _object(carrier)
        _require(set(carrier) == {"kind", "carrier_ref"} and carrier["kind"] in
                 {"card_play_created", "game_action_submitted", "game_action_accepted", "card_play_canceled"}, "source_native_facts_invalid")
        _identifier(carrier["carrier_ref"]); key = (carrier["kind"], carrier["carrier_ref"])
        _require(key not in seen, "source_native_facts_invalid"); seen.add(key)
    _require((value["origin"] == "missing") == (value["missing_reason"] is not None), "source_native_facts_invalid")
    if value["missing_reason"] is not None: _text(value["missing_reason"])


def validate_native_stage(value: Any) -> None:
    value = _object(value)
    _require(set(value) == {"kind", "owner_ref", "subject_ref", "arguments", "evidence", "no_submission"}
             and value["kind"] == "card_play_canceled" and value["evidence"] == "native_finished_false"
             and (value["no_submission"] is None or value["no_submission"] is True), "source_native_stage_invalid")
    _identifier(value["owner_ref"]); _identifier(value["subject_ref"])
    arguments = _object(value["arguments"])
    _require(set(arguments) == {"hand"}, "source_native_stage_invalid")
    _identifier(arguments["hand"])


@dataclass(frozen=True)
class SourceSessionBundleV4(SourceSessionBundleV3):
    pass

DESCRIPTOR = VerifierDescriptor("source-session-bundle-v4", "sts2.annotator/source-session-bundle-4", 4, SourceSessionBundleV4)

class SourceSessionBundleV4Verifier(SourceSessionBundleV2Verifier):
    descriptor = DESCRIPTOR
    format = _SourceFormat(4)
    value_type = SourceSessionBundleV4


def verify_source_session_bundle_v4(source: str | Path, expected: Mapping[str, object] | None = None) -> VerificationResult[SourceSessionBundleV4]:
    return SourceSessionBundleV4Verifier().verify(source, expected)
