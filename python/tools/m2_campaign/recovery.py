"""Root-approved adapter transition and one-use unsubmitted-prepare replacement.

No cloud lookup, owner/store access or training imports occur here. Operator
receipts attest current quiescence; they do not establish historical zero cost.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .authority import money, timestamp
from .config import Settings, bounded_file, digest, fields, path, sha
from .core import CampaignConfig, RunSpec
from .journal import (
    MAX_METADATA_BYTES,
    CampaignJournal,
    JournalError,
    _json,
    _mkdir,
    _read,
    _write,
    canonical,
)

RECEIPT_SCHEMA = "stpd/m2-campaign-recovery-receipt-v1"
EVENT_SCHEMA = "stpd/m2-campaign-recovery-event-v1"
RECEIPT_FIELDS = {
    "schema", "action", "origin_settings_sha256", "from_settings_sha256",
    "to_settings_sha256", "to_settings", "journal_root", "budget_scope_id",
    "before_state_sha256", "before_history", "attempt", "request_sha256", "grant_sha256",
    "provider_adapter_before", "provider_adapter_after", "implementation_closure",
    "provider_observation", "local_prepare_evidence", "replacement_limit",
    "reservations", "retained_raw_ceiling_usd", "issued_at_utc", "expires_at_utc",
}
EVENT_FIELDS = {
    "schema", "receipt", "settings", "before_state", "failed_attempt", "evidence",
    "disposition", "historical_outcome", "historical_cost", "hold_retained",
}
EVIDENCE_KEYS = {"implementation_closure", "provider_observation", "local_prepare_evidence"}


def core_config(settings: Settings) -> dict[str, Any]:
    return asdict(CampaignConfig(tuple(RunSpec(row.run_id, row.max_requests)
                                      for row in settings.runs),
                                settings.value["max_windows"], settings.value["goal_epochs"]))


def adapter_transition(before: Settings, after: Settings) -> None:
    a, b = dict(before.value), dict(after.value)
    old, new = a.pop("provider_adapter"), b.pop("provider_adapter")
    if a != b or old == new:
        raise ValueError("only_provider_adapter_transition_allowed")


def _reference(reference: object) -> bytes:
    value = fields(reference, {"path", "sha256"})
    raw = bounded_file(path(value["path"]), MAX_METADATA_BYTES)
    if sha(raw) != digest(value["sha256"]):
        raise ValueError("recovery_reference_digest_mismatch")
    return raw


def _decoded(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw)
    if type(value) is not dict or canonical(value) != raw:
        raise ValueError("canonical_recovery_metadata_required")
    return value


def _receipt(raw: bytes, before: Settings, after: Settings, journal: CampaignJournal,
             state_raw: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    value = fields(_decoded(raw), RECEIPT_FIELDS)
    adapter_transition(before, after)
    if (value["schema"] != RECEIPT_SCHEMA
            or value["action"] != "abandon_unsubmitted_prepare_and_transition"
            or value["origin_settings_sha256"] != journal.origin_settings_sha256
            or value["from_settings_sha256"] != before.identity
            or value["to_settings_sha256"] != after.identity
            or value["to_settings"]["sha256"] != after.identity
            or value["journal_root"] != str(journal.root)
            or after.value["journal_root"] != str(journal.root)
            or value["budget_scope_id"] != before.value["budget_scope_id"]
            or value["provider_adapter_before"] != before.value["provider_adapter"]
            or value["provider_adapter_after"] != after.value["provider_adapter"]
            or type(value["replacement_limit"]) is not int or value["replacement_limit"] != 1
            or value["before_state_sha256"] != sha(state_raw)):
        raise ValueError("recovery_binding_mismatch")
    fields(value["to_settings"], {"path", "sha256"})
    state = fields(_decoded(state_raw), {"schema", "config_sha256", "attempts"})
    if (state["schema"] != "m2-campaign-journal-v1"
            or state["config_sha256"] != journal._config_sha
            or type(state["attempts"]) is not list or not state["attempts"]):
        raise ValueError("recovery_state_invalid")
    matches = [row for row in state["attempts"]
               if {key: row.get(key) for key in ("attempt_id", "run_id", "ordinal", "slice")}
               == value["attempt"]]
    if len(matches) != 1 or matches[0] != state["attempts"][-1]:
        raise ValueError("recovery_requires_latest_exact_attempt")
    failed = matches[0]
    if (failed["phase"] != "deploy_intent" or failed["approval"] is None
            or any(failed[key] is not None for key in ("target", "handle", "result", "stop"))
            or value["request_sha256"] != failed["request"]["sha256"]
            or value["grant_sha256"] != failed["approval"]["sha256"]
            or journal.accepted(failed) is not None):
        raise ValueError("not_unsubmitted_prepare_boundary")
    history = fields(value["before_history"], {"name", "sha256"})
    name = history["name"]
    if type(name) is not str or len(name) != 17 or not name[:12].isdigit() or name[12:] != ".json":
        raise ValueError("invalid_history_reference")
    if (digest(history["sha256"]) != sha(state_raw)
            or _read(journal.root / "history" / name, MAX_METADATA_BYTES) != state_raw):
        raise ValueError("recovery_history_mismatch")
    # Scan immutable metadata only. Never project or decode the training input.
    for location in journal._history()[:int(name[:12]) + 1]:
        old = _json(location)
        for row in old["attempts"]:
            if row["attempt_id"] == failed["attempt_id"] and (
                    row["phase"] not in {"waiting_approval", "deploy_intent"}
                    or any(row[key] is not None for key in ("target", "handle", "result", "stop"))):
                raise ValueError("historical_submission_or_target_evidence")
    return value, failed


def _reservations(journal: CampaignJournal, rows: list[dict[str, Any]],
                  fallback: Settings) -> list[dict[str, str]]:
    result = []
    for row in rows:
        if row["approval"] is None:
            continue
        grant = json.loads(journal.read(row["approval"], limit=MAX_METADATA_BYTES))
        version_raw = journal.settings_for(row)
        version = fallback if version_raw is None else Settings.decode(version_raw)
        expected = {key: row[key] for key in ("attempt_id", "run_id", "ordinal", "slice")}
        if (grant.get("schema") != "stpd/m2-campaign-attempt-grant-v1"
                or grant.get("config_sha256") != version.identity
                or grant.get("attempt") != expected
                or grant.get("request_sha256") != row["request"]["sha256"]
                or grant.get("producer") != version.value["producer"]
                or grant.get("provider_adapter_sha256")
                != version.value["provider_adapter"]["sha256"]
                or money(grant.get("raw_ceiling_usd")) <= 0):
            raise ValueError("historical_reservation_binding_mismatch")
        result.append({"attempt_id": row["attempt_id"], "grant_sha256": row["approval"]["sha256"],
                       "raw_ceiling_usd": grant["raw_ceiling_usd"]})
    return result


def verify_closure(raw: bytes, settings: Settings) -> None:
    value = fields(_decoded(raw), {"schema", "files"})
    if (value["schema"] != "stpd/m2-campaign-implementation-closure-v1"
            or type(value["files"]) is not list or not 1 <= len(value["files"]) <= 64):
        raise ValueError("implementation_closure_invalid")
    package = Path(__file__).resolve().parent
    required = {str(package / name) for name in (
        "__init__.py", "cli.py", "config.py", "core.py", "journal.py", "authority.py",
        "backend.py", "recovery.py",
    )} | {str(package.parent / "public_m2_campaign.py"), settings.value["provider_adapter"]["path"]}
    seen: set[str] = set()
    for row in value["files"]:
        row = fields(row, {"path", "sha256", "size"})
        location = path(row["path"])
        if (row["path"] in seen or type(row["size"]) is not int
                or not 1 <= row["size"] <= MAX_METADATA_BYTES):
            raise ValueError("implementation_closure_invalid")
        seen.add(row["path"])
        source = bounded_file(location, MAX_METADATA_BYTES)
        if len(source) != row["size"] or sha(source) != digest(row["sha256"]):
            raise ValueError("implementation_closure_source_mismatch")
        if (row["path"] == settings.value["provider_adapter"]["path"]
                and row["sha256"] != settings.value["provider_adapter"]["sha256"]):
            raise ValueError("implementation_closure_adapter_mismatch")
    if not required <= seen:
        raise ValueError("implementation_closure_incomplete")


def _observation(raw: bytes, receipt: dict[str, Any], provider: dict[str, Any],
                 *, fresh: bool) -> None:
    value = fields(_decoded(raw), {
        "schema", "config_sha256", "attempt_id", "request_sha256", "grant_sha256", "provider",
        "deployment_name", "observed_at_utc", "complete", "exact_name_absent",
        "active_apps", "active_gpu", "active_tasks", "active_containers", "active_sandboxes",
        "historical_outcome", "historical_cost",
    })
    if (value["schema"] != "stpd/m2-campaign-unsubmitted-prepare-observation-v1"
            or value["config_sha256"] != receipt["from_settings_sha256"]
            or value["attempt_id"] != receipt["attempt"]["attempt_id"]
            or value["request_sha256"] != receipt["request_sha256"]
            or value["grant_sha256"] != receipt["grant_sha256"]
            or value["provider"] != provider
            or value["deployment_name"] != "stpd-public-m2-" + receipt["attempt"]["attempt_id"]
            or value["complete"] is not True or value["exact_name_absent"] is not True
            or any(type(value[key]) is not int or value[key] != 0 for key in (
                "active_apps", "active_gpu", "active_tasks", "active_containers",
                "active_sandboxes"))
            or value["historical_outcome"] != "unknown" or value["historical_cost"] != "unknown"):
        raise ValueError("incomplete_or_conflicting_provider_observation")
    age = (dt.datetime.now(dt.UTC) - timestamp(value["observed_at_utc"])).total_seconds()
    if fresh and not 0 <= age <= 300:
        raise ValueError("recovery_observation_expired")


def _local(raw: bytes, receipt: dict[str, Any], journal: CampaignJournal, provider: dict[str, Any],
           *, reread: bool) -> None:
    value = fields(_decoded(raw), {"schema", "attempt_id", "request_sha256", "grant_sha256",
                                  "directory", "files", "no_submit_evidence"})
    directory = journal.root / "provider" / receipt["attempt"]["attempt_id"]
    if (value["schema"] != "stpd/m2-campaign-local-prepare-evidence-v1"
            or value["attempt_id"] != receipt["attempt"]["attempt_id"]
            or value["request_sha256"] != receipt["request_sha256"]
            or value["grant_sha256"] != receipt["grant_sha256"]
            or value["directory"] != str(directory) or value["no_submit_evidence"] is not True
            or type(value["files"]) is not list or len(value["files"]) > 16):
        raise ValueError("local_prepare_evidence_mismatch")
    names: set[str] = set()
    runtime = fields(provider.get("runtime_receipt"), {"path", "sha256"})
    runtime_sha = digest(runtime["sha256"])
    path(runtime["path"])
    for row in value["files"]:
        row = fields(row, {"name", "sha256", "size"})
        # This recovery route admits only the known pre-deployment artifacts.
        name = row["name"]
        if name not in {"grant.json", "runtime.json"} or name in names:
            raise ValueError("local_provider_boundary_unknown")
        names.add(row["name"])
        digest(row["sha256"])
        if type(row["size"]) is not int or not 1 <= row["size"] <= MAX_METADATA_BYTES:
            raise ValueError("local_evidence_size_limit")
        if reread:
            source = bounded_file(directory / row["name"], MAX_METADATA_BYTES)
            if len(source) != row["size"] or sha(source) != row["sha256"]:
                raise ValueError("local_provider_inventory_changed")
        if row["name"] == "grant.json" and row["sha256"] != receipt["grant_sha256"]:
            raise ValueError("local_provider_grant_mismatch")
        if row["name"] == "runtime.json" and row["sha256"] != runtime_sha:
            raise ValueError("local_provider_runtime_mismatch")
    if names != {"grant.json", "runtime.json"}:
        raise ValueError("local_provider_boundary_unknown")
    if reread:
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("local_provider_directory_unsafe")
        # Bound the enumeration and reject all subdirectories/unknown objects.
        actual = set()
        for item in directory.iterdir():
            actual.add(item.name)
            if len(actual) > 16 or item.is_symlink() or not item.is_file():
                raise ValueError("local_provider_boundary_unknown")
        if actual != names:
            raise ValueError("local_provider_inventory_changed")


def validate_event(journal: CampaignJournal, event: dict[str, Any], before_raw: bytes) -> None:
    """Replay checks immutable evidence; expired approval cannot undo committed recovery."""
    fields(event, EVENT_FIELDS)
    if (event["schema"] != EVENT_SCHEMA or event["disposition"] != "abandoned_before_submit"
            or event["historical_outcome"] != "unknown" or event["historical_cost"] != "unknown"
            or event["hold_retained"] is not True):
        raise JournalError("recovery_event_invalid")
    before, after = Settings.decode(before_raw), Settings.decode(journal.read(event["settings"]))
    state_raw = journal.read(event["before_state"], limit=MAX_METADATA_BYTES)
    receipt, failed = _receipt(journal.read(event["receipt"], limit=MAX_METADATA_BYTES),
                               before, after, journal, state_raw)
    if event["failed_attempt"] != failed:
        raise JournalError("recovery_failed_attempt_conflict")
    evidence = fields(event["evidence"], EVIDENCE_KEYS)
    for key in EVIDENCE_KEYS:
        raw = journal.read(evidence[key], limit=MAX_METADATA_BYTES)
        if sha(raw) != receipt[key]["sha256"]:
            raise JournalError("recovery_evidence_conflict")
    grant = json.loads(journal.read(failed["approval"], limit=MAX_METADATA_BYTES))
    _observation(
        journal.read(evidence["provider_observation"]), receipt, grant["provider"], fresh=False,
    )
    _local(
        journal.read(evidence["local_prepare_evidence"]), receipt, journal,
        grant["provider"], reread=False,
    )


def recover(settings: Settings, journal: CampaignJournal, raw: bytes,
            approved_sha256: str) -> dict[str, Any]:
    """Apply one externally approved receipt under the existing campaign writer lock."""
    if sha(raw) != digest(approved_sha256):
        raise ValueError("exact_root_recovery_approval_required")
    for name in ("settings.json", "config.json", "state.json"):
        _read(journal.root / name, MAX_METADATA_BYTES)
    with journal.session(core_config(settings), settings_bytes=settings.raw, recovery=True):
        for event in journal.recoveries():
            if event["receipt"]["sha256"] == sha(raw):
                if journal.current_settings_bytes() != settings.raw:
                    raise ValueError("recovery_replay_not_current")
                return event  # Same committed outcome; no second permit.
        before = Settings.decode(journal.current_settings_bytes())
        state_raw = _read(journal.root / "state.json", MAX_METADATA_BYTES)
        receipt, failed = _receipt(raw, before, settings, journal, state_raw)
        if journal.resolution(failed) is not None:
            raise ValueError("attempt_already_reconciled")
        if _reference(receipt["to_settings"]) != settings.raw:
            raise ValueError("new_settings_reference_mismatch")
        if receipt["before_history"]["name"] != journal._history()[-1].name:
            raise ValueError("recovery_snapshot_not_current")
        rows = journal.records(verify_objects=False)
        reservations = _reservations(journal, rows, before)
        if receipt["reservations"] != reservations:
            raise ValueError("recovery_must_retain_all_reservations")
        grant = json.loads(journal.read(failed["approval"], limit=MAX_METADATA_BYTES))
        if money(receipt["retained_raw_ceiling_usd"]) != money(grant["raw_ceiling_usd"]):
            raise ValueError("retained_hold_mismatch")
        evidence_raw = {key: _reference(receipt[key]) for key in EVIDENCE_KEYS}
        verify_closure(evidence_raw["implementation_closure"], settings)
        _local(
            evidence_raw["local_prepare_evidence"], receipt, journal,
            grant["provider"], reread=True,
        )
        _observation(evidence_raw["provider_observation"], receipt, grant["provider"], fresh=True)
        now = dt.datetime.now(dt.UTC)
        if not timestamp(receipt["issued_at_utc"]) <= now < timestamp(receipt["expires_at_utc"]):
            raise ValueError("root_recovery_receipt_expired_or_future")
        # Immutable evidence copies precede the sole commit point. Interrupted
        # copies can leave harmless CAS objects, never an active replacement.
        event = {
            "schema": EVENT_SCHEMA, "receipt": journal.put(raw),
            "settings": journal.put(settings.raw),
            "before_state": journal.put(state_raw), "failed_attempt": failed,
            "evidence": {key: journal.put(value) for key, value in evidence_raw.items()},
            "disposition": "abandoned_before_submit", "historical_outcome": "unknown",
            "historical_cost": "unknown", "hold_retained": True,
        }
        # Recheck the exact boundary and files after validation, immediately
        # before committing. The writer lock prevents another campaign writer.
        if _read(journal.root / "state.json", MAX_METADATA_BYTES) != state_raw:
            raise ValueError("recovery_snapshot_changed")
        for key, value in evidence_raw.items():
            if _reference(receipt[key]) != value:
                raise ValueError("recovery_evidence_changed")
        if (_reference(receipt["to_settings"]) != settings.raw
                or journal.current_settings_bytes() != before.raw
                or receipt["before_history"]["name"] != journal._history()[-1].name):
            raise ValueError("recovery_snapshot_changed")
        verify_closure(evidence_raw["implementation_closure"], settings)
        _local(
            evidence_raw["local_prepare_evidence"], receipt, journal,
            grant["provider"], reread=True,
        )
        _observation(evidence_raw["provider_observation"], receipt, grant["provider"], fresh=True)
        if dt.datetime.now(dt.UTC) >= timestamp(receipt["expires_at_utc"]):
            raise ValueError("root_recovery_receipt_expired_or_future")
        directory = journal.root / "recoveries"
        _mkdir(directory)
        _write(
            directory / f"{len(journal.recoveries()):012d}.json", canonical(event), immutable=True,
        )
        return event


def validate_execution(journal: CampaignJournal) -> None:
    events = journal.recoveries()
    if events:
        event = events[-1]
        verify_closure(journal.read(event["evidence"]["implementation_closure"]),
                       Settings.decode(journal.current_settings_bytes()))
