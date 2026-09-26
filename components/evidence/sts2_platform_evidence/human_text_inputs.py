"""Portable verification of the declared native Human text-input side stream.

These rows attest an observed input and its exact native binding. They do not
assert Connector delivery, a canonical transition, or a causal successor.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .human_session_bundle_v1 import BundleVerificationError, _sha256_file

SCHEMA = "sts2.human-annotator/human-text-input-1"
# Producer-specific provenance is interpreted here, never by the model or its
# research projection. Several native mechanisms can prove one public verb.
MECHANISM_VERBS = {
    "begin_card_play_exact_factory_return": "begin_card_play",
    "controller_confirmed_input_signal": "confirm_card",
    "controller_canceled_input_signal": "cancel_card_play",
    "controller_target_finish_input": "confirm_target",
    "controller_target_canceled_input": "cancel_card_play",
}
MAPPING_BASIS = "text_menu_native_reference_equality"
DISPOSITIONS = {"accepted_input", "not_mapped", "capture_failed", "rejected_or_cancelled"}
HEX_SHA = re.compile(r"[0-9a-fA-F]{64}\Z")
HEX_REVISION = re.compile(r"[0-9a-fA-F]{40}\Z")
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
FIELDS = {
    "schema_version", "schema", "sequence", "record_id", "session_id", "timeline_id",
    "run_id", "observed_at", "recorded_at", "environment", "snapshot",
    "snapshot_sha256", "chosen_action", "mapping_status", "match_count",
    "mapping_basis", "native_owner_witness_id", "native_subject_witness_id",
    "native_carrier_witness_id", "native_mechanism", "disposition", "reason_code",
    "external_controller_active",
}


def _check(condition: bool, code: str) -> None:
    if not condition:
        raise BundleVerificationError(code, code)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _exact_environment(value: Mapping[str, Any]) -> bool:
    game, connector, annotator = (value.get(key) for key in ("game", "connector", "annotator"))
    if not all(isinstance(item, dict) for item in (game, connector, annotator)):
        return False
    if (not isinstance(game.get("main_assembly_sha256"), str)
            or not HEX_SHA.fullmatch(game["main_assembly_sha256"])
            or not isinstance(game.get("main_assembly_module_version_id"), str)
            or not UUID.fullmatch(game["main_assembly_module_version_id"])):
        return False
    for artifact in (connector, annotator):
        if (not all(_nonempty(artifact.get(key)) for key in ("product", "version"))
                or not isinstance(artifact.get("source_revision"), str)
                or not HEX_REVISION.fullmatch(artifact["source_revision"])
                or not all(isinstance(artifact.get(key), str) and HEX_SHA.fullmatch(artifact[key])
                           for key in ("source_digest_sha256", "sha256"))
                or not isinstance(artifact.get("module_version_id"), str)
                or not UUID.fullmatch(artifact["module_version_id"])):
            return False
    return all(_nonempty(value.get(key)) for key in (
        "runtime_instance_id", "environment_fingerprint", "player_environment_protocol",
        "modset_status", "modset_fingerprint"))


def _timestamp(value: Any) -> datetime:
    _check(_nonempty(value), "human_text_input_identity_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _check(parsed.tzinfo is not None, "human_text_input_identity_invalid")
        return parsed
    except ValueError as error:
        raise BundleVerificationError("human_text_input_identity_invalid", "invalid timestamp") from error


def _row_and_snapshot_bytes(line: bytes) -> tuple[dict[str, Any], bytes | None]:
    """Preserve the producer's exact STJ snapshot bytes for its declared SHA256."""
    try:
        source = line.decode("utf-8")
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate JSON property")
                value[key] = item
            return value

        decoder = json.JSONDecoder(
            object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        index = 0
        after_comma = False

        def space(at: int) -> int:
            while at < len(source) and source[at] in " \t\r\n":
                at += 1
            return at

        index = space(index)
        _check(source[index] == "{", "human_text_input_json_invalid")
        index += 1
        row: dict[str, Any] = {}
        snapshot_bytes = None
        while True:
            index = space(index)
            if source[index] == "}":
                _check(not after_comma, "human_text_input_json_invalid")
                index += 1
                break
            after_comma = False
            key, index = decoder.raw_decode(source, index)
            _check(isinstance(key, str) and key not in row, "human_text_input_json_invalid")
            index = space(index)
            _check(source[index] == ":", "human_text_input_json_invalid")
            index = space(index + 1)
            start = index
            value, index = decoder.raw_decode(source, index)
            row[key] = value
            if key == "snapshot" and value is not None:
                snapshot_bytes = source[start:index].encode("utf-8")
            index = space(index)
            if source[index] == ",":
                index += 1
                after_comma = True
                continue
            _check(source[index] == "}", "human_text_input_json_invalid")
            index += 1
            break
        _check(space(index) == len(source), "human_text_input_json_invalid")
        return row, snapshot_bytes
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BundleVerificationError("human_text_input_json_invalid", "invalid text input JSON") from error


def _validate_row(row: Mapping[str, Any], snapshot_bytes: bytes | None,
                  session: str, timeline: str, runs: set[str], sequence: int) -> None:
    _check(set(row) <= FIELDS and row.get("schema_version") == 1 and row.get("schema") == SCHEMA,
           "human_text_input_schema_invalid")
    _check(type(row.get("sequence")) is int and row["sequence"] == sequence
           and all(_nonempty(row.get(key)) for key in ("record_id", "session_id", "timeline_id", "run_id"))
           and row["session_id"] == session and row["timeline_id"] == timeline
           and row["run_id"] in runs,
           "human_text_input_identity_invalid")
    _check(_timestamp(row.get("recorded_at")) >= _timestamp(row.get("observed_at")),
           "human_text_input_identity_invalid")
    _check(row.get("external_controller_active") is False,
           "human_text_input_external_controller")
    mechanism = row.get("native_mechanism")
    _check(isinstance(mechanism, str) and mechanism in MECHANISM_VERBS,
           "human_text_input_native_mechanism_invalid")
    _check(_nonempty(row.get("native_owner_witness_id"))
           and (row.get("disposition") == "capture_failed"
                or _nonempty(row.get("native_subject_witness_id"))),
           "human_text_input_native_witness_invalid")
    disposition = row.get("disposition")
    _check(disposition in DISPOSITIONS, "human_text_input_disposition_invalid")
    accepted = disposition == "accepted_input"
    _check((row.get("reason_code") is None) if accepted else _nonempty(row.get("reason_code")),
           "human_text_input_failure_reason_invalid")
    _check(type(row.get("match_count")) is int and row["match_count"] >= 0,
           "human_text_input_match_count_invalid")
    if accepted:
        _check(row.get("mapping_status") == "exact_unique" and row["match_count"] == 1
               and row.get("mapping_basis") == MAPPING_BASIS
               and _nonempty(row.get("native_carrier_witness_id")),
               "human_text_input_exact_mapping_missing")
    snapshot = row.get("snapshot")
    if snapshot is None:
        _check(not accepted and disposition == "capture_failed"
               and row.get("snapshot_sha256") is None and row.get("chosen_action") is None,
               "human_text_input_snapshot_missing")
        return
    _check(isinstance(snapshot, dict) and snapshot_bytes is not None,
           "human_text_input_snapshot_invalid")
    _check(row.get("snapshot_sha256") == hashlib.sha256(snapshot_bytes).hexdigest(),
           "human_text_input_snapshot_digest_mismatch")
    environment = row.get("environment")
    _check(isinstance(environment, dict) and _exact_environment(environment),
           "human_text_input_environment_missing")
    session_value = snapshot.get("session")
    _check(snapshot.get("schema") == "sts2.player-environment/text-menu-snapshot-1"
           and snapshot.get("input_profile") == "text-menu-v1"
           and snapshot.get("protocol_version") == environment["player_environment_protocol"]
           and _nonempty(snapshot.get("snapshot_id"))
           and isinstance(session_value, dict)
           and session_value.get("runtime_instance_id") == environment["runtime_instance_id"]
           and session_value.get("environment_fingerprint") == environment["environment_fingerprint"],
           "human_text_input_snapshot_environment_mismatch")
    if not accepted:
        return
    completeness, menu, catalog = (snapshot.get(key) for key in (
        "completeness", "menu", "menu_actions"))
    interaction = snapshot.get("interaction")
    policy = snapshot.get("information_policy")
    _check(snapshot.get("status") == "interactive"
           and type(snapshot.get("sequence")) is int and snapshot["sequence"] > 0
           and "persistent" in snapshot
           and _nonempty(snapshot.get("observed_at"))
           and isinstance(interaction, dict)
           and all(_nonempty(interaction.get(key)) for key in (
               "interaction_id", "kind", "content_schema"))
           and isinstance(interaction.get("content"), dict)
           and isinstance(interaction["content"].get("surface"), dict)
           and _nonempty(interaction["content"]["surface"].get("kind"))
           and isinstance(interaction["content"].get("context"), dict)
           and isinstance(snapshot.get("referents"), list)
           and isinstance(policy, dict) and _nonempty(policy.get("scope"))
           and policy.get("includes_hidden_information") is False
           and isinstance(completeness, dict) and completeness.get("status") == "complete"
           and isinstance(menu, dict) and menu.get("cursor") == "root"
           and type(menu.get("revision")) is int
           and _nonempty(menu.get("native_snapshot_id"))
           and isinstance(catalog, dict) and catalog.get("status") == "complete"
           and catalog.get("ordering_semantics") == "native_order_with_fixed_information_groups"
           and isinstance(catalog.get("actions"), list)
           and len(catalog["actions"]) > 0
           and type(catalog.get("total_count")) is int
           and type(catalog.get("materialized_count")) is int
           and catalog["total_count"] == catalog["materialized_count"] == len(catalog["actions"]),
           "human_text_input_catalog_incomplete")
    _timestamp(snapshot["observed_at"])
    referents: dict[str, bool] = {}
    for referent in snapshot["referents"]:
        _check(isinstance(referent, dict) and _nonempty(referent.get("referent_id"))
               and all(_nonempty(referent.get(key)) for key in ("kind", "role"))
               and isinstance(referent.get("state"), dict)
               and type(referent["state"].get("visible")) is bool
               and referent["referent_id"] not in referents,
               "human_text_input_catalog_incomplete")
        referents[referent["referent_id"]] = referent["state"]["visible"]
    ids: set[str] = set()
    for action in catalog["actions"]:
        _check(isinstance(action, dict) and _nonempty(action.get("action_id"))
               and action["action_id"] not in ids
               and action.get("kind") in {"native_input", "system_navigation"}
               and all(_nonempty(action.get(key)) for key in ("verb", "label"))
               and action.get("effect_domain") == (
                   "native_input" if action["kind"] == "native_input" else "text_menu")
               and isinstance(action.get("arguments"), list),
               "human_text_input_catalog_incomplete")
        ids.add(action["action_id"])
        subject = action.get("subject_referent_id")
        _check(subject is None or referents.get(subject) is True,
               "human_text_input_catalog_incomplete")
        for argument in action["arguments"]:
            _check(isinstance(argument, dict) and _nonempty(argument.get("role"))
                   and referents.get(argument.get("referent_id")) is True,
                   "human_text_input_catalog_incomplete")
    chosen = row.get("chosen_action")
    _check(isinstance(chosen, dict) and chosen.get("kind") == "native_input"
           and chosen.get("effect_domain") == "native_input"
           and chosen.get("verb") == MECHANISM_VERBS[mechanism]
           and _nonempty(chosen.get("action_id"))
           and _nonempty(chosen.get("subject_referent_id"))
           and isinstance(chosen.get("arguments"), list)
           and sum(action == chosen for action in catalog["actions"]) == 1,
           "human_text_input_chosen_action_not_unique")


def verify_human_text_inputs(raw: Path, recording: Mapping[str, Any],
                             close_receipt: Mapping[str, Any] | None,
                             run_ids: tuple[str, ...]) -> tuple[Mapping[str, Any], ...]:
    # A producer-latched append failure remains disqualifying even if a later
    # pack or interrupted-recovery copy reseals every surviving byte.
    _check(not (raw / "human-text-input-failure.json").exists(),
           "human_text_input_append_failure")
    path = raw / "human-text-inputs.jsonl"
    version = recording.get("text_input_schema_version")
    _check(version is None or type(version) is int and version == 1,
           "human_text_input_schema_invalid")
    if version is None:
        _check(not path.exists(), "undeclared_human_text_input_stream")
        return ()
    _check(close_receipt is None or close_receipt.get("recovery") is None,
           "human_text_input_recovery_unsupported")
    _check(path.is_file() and close_receipt is not None,
           "human_text_input_stream_or_close_seal_missing")
    content = path.read_bytes()
    _check(not content or content.endswith(b"\n"), "human_text_input_torn_tail")
    lines = content.splitlines(keepends=True)
    _check(all(line.endswith(b"\n") and line[:-1].strip() for line in lines),
           "human_text_input_json_invalid")
    rows = []
    seen: set[str] = set()
    for sequence, line in enumerate(lines, start=1):
        row, snapshot_bytes = _row_and_snapshot_bytes(line[:-1])
        _validate_row(row, snapshot_bytes, str(recording["session_id"]),
                      str(recording["timeline_id"]), set(run_ids), sequence)
        _check(row["record_id"] not in seen, "human_text_input_duplicate_record_id")
        seen.add(row["record_id"])
        rows.append(row)
    _check(type(close_receipt.get("human_text_input_count")) is int
           and close_receipt["human_text_input_count"] == len(rows)
           and close_receipt.get("human_text_inputs_sha256") == _sha256_file(path),
           "human_text_input_close_seal_mismatch")
    return tuple(rows)


def freeze_json(value: Any) -> Any:
    """Keep verifier-returned evidence facts immutable across consumer code."""
    if isinstance(value, dict):
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_json(item) for item in value)
    return value
