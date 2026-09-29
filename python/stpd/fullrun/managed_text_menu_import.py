"""Explicit engineering import of a closed Managed text-menu-v2 session.

The Workbench report and its event parents remain the authority for what was
observed. This source records control inputs, not Human actions, Commit, or S'.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, digest
from spireagent.storage.store import ArtifactStore, copy_artifact

from ..canonical import semantic_hash
from .text_menu_inputs import project_text_menu_v2_snapshot

REPORT_SCHEMA = "stpd/local-managed-environment-report-v1"
EVENT_SCHEMA = "stpd/local-managed-environment-event-v1"
SESSION_SCHEMA = "stpd/local-managed-environment-v1"
SOURCE_SCHEMA = "stpd/managed-text-menu-observed-source-v1"
CONTEXT_SCHEMA = "sts2.player-environment/text-menu-observation-context-2"
RESULT_SCHEMA = "sts2.player-environment/text-menu-action-result-2"
PROFILE = "text-menu-v2"
MAX_EVENTS = 4096
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ManagedImportExpectation:
    """Exact caller-supplied operation facts; this is not actor attestation."""

    session_id: str
    scenario_id: str
    seed: str
    host_package_pin: FrozenObject
    candidate_build: FrozenObject
    environment_fingerprint: str
    runtime_instance_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id, "scenario_id": self.scenario_id,
            "seed": self.seed, "host_package_pin": self.host_package_pin.value(),
            "candidate_build": self.candidate_build.value(),
            "environment_fingerprint": self.environment_fingerprint,
            "runtime_instance_id": self.runtime_instance_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ManagedImportExpectation:
        if not isinstance(value, dict) or set(value) != {
            "session_id", "scenario_id", "seed", "host_package_pin",
            "candidate_build", "environment_fingerprint", "runtime_instance_id",
        }:
            _fail("import_expectation_invalid")
        if any(not isinstance(value[key], str) or not value[key] for key in (
            "session_id", "scenario_id", "seed", "environment_fingerprint",
            "runtime_instance_id",
        )) or not isinstance(value["host_package_pin"], dict) or not isinstance(
            value["candidate_build"], dict
        ):
            _fail("import_expectation_invalid")
        return cls(value["session_id"], value["scenario_id"], value["seed"],
                   FrozenObject.of(value["host_package_pin"]),
                   FrozenObject.of(value["candidate_build"]),
                   value["environment_fingerprint"], value["runtime_instance_id"])


@dataclass(frozen=True)
class ManagedInput:
    sequence: int
    event_artifact_id: str
    request_id: str
    action_id: str
    before_context: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True)
class ManagedSource:
    manifest: Manifest
    report_id: str
    report: dict[str, Any]
    inputs: tuple[ManagedInput, ...]
    # Repeated seed and exact candidate/game identity share one split component,
    # even when Workbench session IDs or UI scenario labels differ.
    split_run_id: str
    import_expectation: ManagedImportExpectation


def _fail(code: str) -> None:
    raise BoundaryError("managed_text_menu_import", code)


def _snapshot(value: Any, *, interactive: bool) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != (
        "sts2.player-environment/text-menu-snapshot-2"
    ) or value.get("input_profile") != PROFILE:
        _fail("snapshot_invalid")
    if interactive:
        project_text_menu_v2_snapshot(value)
    else:
        # Public result observations may be terminal or temporarily settling.
        # They carry no candidate label and never enter the next input unless
        # another complete interactive page was actually recorded.
        if value.get("status") == "interactive":
            project_text_menu_v2_snapshot(value)
        elif (value.get("status") not in {"observed", "settling", "visible_unsupported"}
              or set(value) != {"protocol_version", "schema", "input_profile",
                                "snapshot_id", "sequence", "observed_at", "status",
                                "persistent", "interaction", "referents", "completeness",
                                "session", "information_policy", "menu", "menu_actions"}
              or value.get("protocol_version") != "1.0.0"
              or not isinstance(value.get("observed_at"), str)
              or not value["observed_at"]
              or not isinstance(value.get("interaction"), dict)
              or value["interaction"].get("capabilities") != []
              or not isinstance(value["interaction"].get("content"), dict)
              or not isinstance(value.get("referents"), list)
              or not isinstance(value.get("completeness"), dict)
              or not isinstance(value.get("information_policy"), dict)
              or value["information_policy"].get("includes_hidden_information") is not False
              or not isinstance(value.get("menu"), dict)
              or set(value["menu"]) != {
                  "cursor", "revision", "native_snapshot_id", "selection"}
              or value["menu"].get("cursor") != "root"
              or value["menu"].get("selection") != []
              or type(value["menu"].get("revision")) is not int
              or value["menu"]["revision"] < 0
              or not isinstance(value["menu"].get("native_snapshot_id"), str)
              or not value["menu"]["native_snapshot_id"]
              or not isinstance(value.get("menu_actions"), dict)
              or set(value["menu_actions"]) != {
                  "status", "materialized_count", "total_count",
                  "ordering_semantics", "actions"}
              or value["menu_actions"].get("actions") != []
              or value["menu_actions"].get("materialized_count") != 0
              or value["menu_actions"].get("total_count") != 0
              or value["menu_actions"].get("status") != (
                  "complete" if value["status"] == "observed" else "unavailable")
              or not isinstance(value["menu_actions"].get("ordering_semantics"), str)
              or not value["menu_actions"]["ordering_semantics"]):
            _fail("noninteractive_successor_invalid")
    if (type(value.get("sequence")) is not int or value["sequence"] < 1
            or not isinstance(value.get("snapshot_id"), str)
            or not value["snapshot_id"] or not isinstance(value.get("session"), dict)
            or set(value["session"]) != {"runtime_instance_id", "environment_fingerprint"}
            or any(not isinstance(item, str) or not item for item in value["session"].values())):
        _fail("snapshot_identity_invalid")
    return dict(value)


def _context(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {
        "schema", "snapshot", "game_continuity_id"
    } or value["schema"] != CONTEXT_SCHEMA or not isinstance(
        value["game_continuity_id"], str
    ) or not value["game_continuity_id"]):
        _fail("context_invalid")
    _snapshot(value["snapshot"], interactive=True)
    return dict(value)


def _ui_successor(action: dict[str, Any], before: dict[str, Any],
                  after: dict[str, Any]) -> None:
    current, next_menu = before["menu"], after["menu"]
    if (after["status"] != "interactive"
            or next_menu["native_snapshot_id"] != current["native_snapshot_id"]
            or next_menu["revision"] <= current["revision"]):
        _fail("menu_successor_mismatch")
    verb = action["verb"]
    selection = current["selection"]
    next_selection = next_menu["selection"]
    if verb == "select_card":
        valid = (current["cursor"] == "root"
                 and next_menu["cursor"] in {"card_targets", "card_confirmation"}
                 and next_selection == [{"role": "card",
                                         "referent_id": action["subject_referent_id"]}])
    elif verb == "select_target":
        valid = (current["cursor"] == "card_targets"
                 and next_menu["cursor"] == "card_confirmation"
                 and next_selection == selection + [{
                     "role": "target", "referent_id": action["subject_referent_id"]}])
    elif verb == "cancel_selection":
        valid = next_menu["cursor"] == "root" and next_selection == []
    elif verb == "back":
        if current["cursor"] == "card_confirmation" and len(selection) == 2:
            valid = (next_menu["cursor"] == "card_targets"
                     and next_selection == selection[:1])
        else:
            valid = next_menu["cursor"] in {"root", "information"} and next_selection == []
    elif verb == "open_information":
        valid = current["cursor"] == "root" and next_menu["cursor"] == "information"
    else:
        valid = next_menu["cursor"] == verb.removeprefix("open_")
    if not valid:
        _fail("menu_successor_mismatch")


def _closed_report(store: ArtifactStore, report_id: str, *,
                   expected: ManagedImportExpectation | None = None) -> tuple[
                       Manifest, dict[str, Any], tuple[ManagedInput, ...], str]:
    report_manifest = store.get_manifest(report_id)
    params = report_manifest.parameters.value()
    if (report_manifest.kind != "analysis" or len(report_manifest.payloads) != 1
            or report_manifest.payloads[0].role != "report"
            or params.get("schema") != REPORT_SCHEMA
            or params.get("scope") != "managed_text_menu_engineering_only"
            or params.get("status") != "stopped"):
        _fail("closed_report_required")
    payload = report_manifest.payload("report")
    if payload.size > MAX_REPORT_BYTES:
        _fail("report_too_large")
    report = decode_json(b"".join(store.read_payload(payload)))
    if (not isinstance(report, dict) or report.get("schema") != SESSION_SCHEMA
            or report.get("status") != "stopped" or report.get("error_code") is not None
            or report.get("input_profile") != PROFILE
            or report.get("session_id") != params.get("session_id")
            or report.get("scenario_id") != params.get("scenario_id")
            or not isinstance(report.get("seed"), str) or not report["seed"]
            or not isinstance(report.get("events"), list)
            or not 1 <= len(report["events"]) <= MAX_EVENTS
            or len(report_manifest.parents) != len(report["events"])):
        _fail("report_identity_mismatch")
    # The report producer must state the exact Host package and profile used.
    # An old report lacking this contract cannot be upgraded by inference.
    pin = report.get("host_package_pin")
    if (not isinstance(pin, dict) or set(pin) != {
        "schema", "package", "version", "source_revision",
        "component_tree_revision", "release_asset_sha256", "package_content_sha256",
    } or pin.get("schema") != "stpd/platform-host-runtime-pin-v1"
            or pin.get("package") != "@rsgcsg/sts2-host-runtime"):
        _fail("host_lineage_missing")
    for field, length in (("source_revision", 40), ("component_tree_revision", 40),
                          ("release_asset_sha256", 64), ("package_content_sha256", 64)):
        digest(pin[field], f"managed.{field}", length=length)
    if not isinstance(pin["version"], str) or not pin["version"].startswith("1.1.0-rc."):
        _fail("host_lineage_missing")
    episode = report.get("episode_identity")
    if (not isinstance(episode, dict) or not isinstance(episode.get("candidate_build"), dict)
            or not isinstance(episode.get("episode_provenance"), dict)
            or episode["episode_provenance"].get("verdict") != "provenance_pass"
            or episode["episode_provenance"].get("requested_seed") != report["seed"]
            or episode["episode_provenance"].get("actual_seed") != report["seed"]
            or not isinstance(episode.get("environment_fingerprint"), str)):
        _fail("episode_lineage_invalid")
    digest(episode["environment_fingerprint"], "managed.environment_fingerprint")
    build = episode["candidate_build"]
    if set(build) != {"upstream_revision", "source_patch_sha256", "artifact_sha256",
                      "artifact_mvid", "original_sts2_sha256", "runtime_sts2_sha256"}:
        _fail("episode_lineage_invalid")
    for key in ("source_patch_sha256", "artifact_sha256", "original_sts2_sha256",
                "runtime_sts2_sha256"):
        digest(build[key], f"managed.{key}")
    if expected is not None and expected.to_dict() != {
        "session_id": report["session_id"], "scenario_id": report["scenario_id"],
        "seed": report["seed"], "host_package_pin": pin,
        "candidate_build": build,
        "environment_fingerprint": episode["environment_fingerprint"],
        "runtime_instance_id": episode["episode_provenance"].get("runtime_instance_id"),
    }:
        _fail("import_expectation_mismatch")
    session = {"runtime_instance_id": episode["episode_provenance"].get(
        "runtime_instance_id"), "environment_fingerprint": episode["environment_fingerprint"]}
    if any(not isinstance(item, str) or not item for item in session.values()):
        _fail("episode_lineage_invalid")
    initial: dict[str, Any] | None = None
    prior: dict[str, Any] | None = None
    inputs: list[ManagedInput] = []
    seen_requests: set[str] = set()
    seen_events: set[str] = set()
    for index, (summary, parent) in enumerate(zip(report["events"],
                                                   report_manifest.parents, strict=True)):
        if (not isinstance(summary, dict) or parent.role != f"event-{index}"
                or summary.get("event_artifact_id") != parent.artifact_id
                or parent.artifact_id in seen_events):
            _fail("event_parent_mismatch")
        seen_events.add(parent.artifact_id)
        event_manifest = store.get_manifest(parent.artifact_id)
        event_params = event_manifest.parameters.value()
        if (event_manifest.kind != "run_event" or event_manifest.parents
                or event_manifest.producer != report_manifest.producer
                or len(event_manifest.payloads) != 1
                or event_manifest.payloads[0].role != "event"
                or event_params != {
                    "schema": EVENT_SCHEMA, "scenario_id": report["scenario_id"],
                    "session_id": report["session_id"],
                    "request_id": summary.get("request_id"),
                } or event_manifest.payload("event").size > MAX_EVENT_BYTES):
            _fail("event_identity_mismatch")
        event = decode_json(b"".join(store.read_payload(event_manifest.payload("event"))))
        if not isinstance(event, dict) or set(event) != {
            "request_id", "action_id", "before_context", "expected_snapshot_id",
            "result", "error_code",
        }:
            _fail("event_shape_invalid")
        request = event["request_id"]
        before = _context(event["before_context"])
        snapshot = before["snapshot"]
        action = event["action_id"]
        if (not isinstance(request, str) or not request or request in seen_requests
                or not isinstance(action, str) or not action
                or snapshot["session"] != session
                or event["expected_snapshot_id"] != snapshot.get("snapshot_id")
                or prior is not None and before != prior
                or initial is not None and before["game_continuity_id"] !=
                initial["game_continuity_id"]):
            _fail("request_or_continuity_mismatch")
        seen_requests.add(request)
        if initial is None:
            initial = before
        projected = project_text_menu_v2_snapshot(snapshot)
        if projected.action_ids.count(action) != 1:
            _fail("action_not_in_complete_catalog")
        result = event["result"]
        selected = next(item for item in snapshot["menu_actions"]["actions"]
                        if item["action_id"] == action)
        if (event["error_code"] is not None or not isinstance(result, dict)
                or set(result) != {"protocol_version", "schema", "input_profile",
                                  "request_id", "status", "effect_domain",
                                  "native_delivery", "action", "reason_code", "detail",
                                  "retry", "successor", "attribution"}
                or result.get("protocol_version") != "1.0.0"
                or result.get("schema") != RESULT_SCHEMA
                or result.get("input_profile") != PROFILE
                or result.get("request_id") != request
                or result.get("status") != "applied"
                or result.get("action") != selected
                or result.get("effect_domain") != selected["effect_domain"]
                or result.get("native_delivery") != (
                    "delivered" if selected["effect_domain"] == "native_input" else None)
                or result.get("reason_code") is not None
                or not isinstance(result.get("detail"), str)
                or result.get("retry") != "never"
                or result.get("attribution") is not None
                or not isinstance(result.get("successor"), dict)
                or summary != {
                    "request_id": request, "action_id": action,
                    "expected_snapshot_id": event["expected_snapshot_id"],
                    "error_code": None, "result_status": "applied",
                    "native_delivery": result["native_delivery"],
                    "event_artifact_id": parent.artifact_id,
                }):
            _fail("unconfirmed_or_mismatched_result")
        successor = result["successor"]
        _snapshot(successor, interactive=False)
        if (successor["session"] != session or successor["sequence"] <= snapshot["sequence"]
                or successor["snapshot_id"] == snapshot["snapshot_id"]
                or index < len(report["events"]) - 1 and successor["status"] != "interactive"):
            _fail("successor_identity_or_order_mismatch")
        if selected["effect_domain"] == "text_menu":
            _ui_successor(selected, snapshot, successor)
        prior = {"schema": CONTEXT_SCHEMA, "snapshot": successor,
                 "game_continuity_id": before["game_continuity_id"]}
        inputs.append(ManagedInput(index + 1, parent.artifact_id, request, action,
                                   before, result))
    if report.get("context") != prior:
        _fail("final_context_mismatch")
    # UI scenario names are mutable and cannot establish independent games.
    run = "managed:" + semantic_hash([
        report["seed"], build["source_patch_sha256"], build["artifact_sha256"],
        build["runtime_sts2_sha256"],
    ])
    return report_manifest, report, tuple(inputs), run


def load_managed_text_menu_source(store: ArtifactStore, source_id: str, *,
                                  expected: ManagedImportExpectation | None = None
                                  ) -> ManagedSource:
    manifest = store.get_manifest(source_id)
    if (manifest.kind != "dataset" or manifest.payloads
            or len(manifest.parents) != 1 or manifest.parents[0].role != "managed_report"):
        _fail("managed_source_required")
    report_id = manifest.parents[0].artifact_id
    recorded = ManagedImportExpectation.from_dict(
        manifest.parameters.value().get("import_expectation"))
    if expected is not None and recorded != expected:
        _fail("import_expectation_mismatch")
    _, report, inputs, run = _closed_report(store, report_id, expected=recorded)
    expected_parameters = {
        "schema": SOURCE_SCHEMA, "scope": "engineering_control",
        "source_kind": "managed_control_input_stream", "actor": "unverified",
        "purpose": "observed_input", "report_id": report_id,
        "session_id": report["session_id"], "scenario_id": report["scenario_id"],
        "split_run_id": run, "event_count": len(inputs),
        "import_expectation": recorded.to_dict(),
        "claim": "applied_control_inputs_only_no_human_or_causal_successor",
    }
    if manifest.parameters.value() != expected_parameters:
        _fail("source_identity_mismatch")
    return ManagedSource(manifest, report_id, report, inputs, run, recorded)


def import_managed_text_menu_report(
    report_store: ArtifactStore, research_store: ArtifactStore, report_id: str,
    producer: Producer, *, expected: ManagedImportExpectation,
) -> ManagedSource:
    """Validate before copying; publication creates no training-use proof."""
    if not isinstance(expected, ManagedImportExpectation):
        _fail("import_expectation_required")
    _, report, inputs, run = _closed_report(report_store, report_id, expected=expected)
    copy_artifact(report_store, research_store, report_id)
    manifest = Manifest("dataset", producer, (Parent("managed_report", report_id),), (),
                        FrozenObject.of({
        "schema": SOURCE_SCHEMA, "scope": "engineering_control",
        "source_kind": "managed_control_input_stream", "actor": "unverified",
        "purpose": "observed_input", "report_id": report_id,
        "session_id": report["session_id"], "scenario_id": report["scenario_id"],
        "split_run_id": run, "event_count": len(inputs),
        "import_expectation": expected.to_dict(),
        "claim": "applied_control_inputs_only_no_human_or_causal_successor",
    }))
    research_store.publish(manifest)
    return load_managed_text_menu_source(research_store, manifest.artifact_id)
