from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from test_human_annotator import _record

from stpd.data.human_annotator import (
    HumanRecordRejection,
    import_human_recording,
    import_verified_human_bundle,
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _refresh_checksums(bundle: Path) -> None:
    lines = [
        f"{_sha_file(path)}  {path.relative_to(bundle).as_posix()}"
        for path in sorted(bundle.rglob("*"))
        if path.is_file() and path.name != "checksums.sha256"
    ]
    (bundle / "checksums.sha256").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )


def _v2_bundle(
    tmp_path: Path, *, selector: bool = False, session_id: str | None = None,
) -> Path:
    bundle = tmp_path / ("selector-bundle" if selector else "combat-bundle")
    raw = bundle / "raw"
    for relative in ("audit", "export", "profile"):
        (bundle / relative).mkdir(parents=True)
    (raw / "blobs" / "sha256").mkdir(parents=True)
    record = copy.deepcopy(_record())
    record.update(
        {
            "session_id": session_id or record["session_id"],
            "schema_version": 2,
            "schema": "sts2.human-annotator/decision-record-2",
            "timeline_id": f"timeline-v2-{session_id}" if session_id else "timeline-v2-test",
            "capture_profile_id": "human-combat-read-rich-v2",
        }
    )
    profile = {
        "schema_version": 2,
        "schema": "sts2.ai-platform/human-capture-profile-2",
        "profile_id": "human-combat-read-rich-v2",
        "record_schema": "sts2.human-annotator/decision-record-2",
        "supported_action_families": [
            "ordinary_combat.play_card",
            "native_generated_card_choice.select",
        ],
        "reads": [
            {"phase": phase, "kind": kind, "required": True}
            for phase in ("pre", "successor")
            for kind in ("run_deck", "combat_piles")
        ],
        "non_claims": ["fixture_not_live"],
    }
    profile_sha = _sha_bytes(_canonical(profile).encode())
    blobs: dict[str, tuple[str, str]] = {}
    for kind in ("run_deck", "combat_piles"):
        payload = (
            _canonical(
                {
                    "kind": kind,
                    "cards": [{"name": "Strike"}],
                    "zones": [{"name": "draw", "cards": [{"name": "Defend"}]}],
                }
            )
            + "\n"
        )
        digest = _sha_bytes(payload.encode())
        relative = f"blobs/sha256/{digest[:2]}/{digest}.json"
        path = raw / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8", newline="\n")
        blobs[kind] = (relative, digest)

    def reads(phase: str, snapshot_id: str) -> list[dict[str, Any]]:
        return [
            {
                "schema_version": 2,
                "schema": "sts2.human-annotator/read-evidence-2",
                "read_evidence_id": f"read-{phase}-{kind}",
                "read_id": f"read:{kind}",
                "kind": kind,
                "snapshot_id": snapshot_id,
                "runtime_instance_id": "runtime-1",
                "environment_fingerprint": "environment-1",
                "status": "materialized",
                "content_schema": f"sts2.player-environment/read/{kind}-1",
                "completeness": {"status": "complete", "missing": []},
                "payload_ref": blobs[kind][0],
                "payload_sha256": blobs[kind][1],
                "captured_at": "2026-08-25T00:00:00Z",
                "error_code": None,
                "detail": None,
            }
            for kind in ("run_deck", "combat_piles")
        ]

    record["pre"]["reads"] = reads("pre", record["pre"]["snapshot_id"])
    record["successor"]["reads"] = reads("successor", record["successor"]["snapshot_id"])
    if selector:
        snapshot = record["pre"]["snapshot"]
        snapshot["interaction"]["kind"] = "native_generated_card_choice"
        snapshot["referents"][0]["role"] = "card_choice"
        action = {
            "bound_action_id": "bound-select",
            "verb": "select",
            "interaction_id": "interaction-1",
            "subject_referent_id": "card-1",
            "arguments": [],
            "label": "Select Strike",
        }
        snapshot["bound_actions"].update(
            {
                "actions": [action],
                "materialized_count": 1,
                "total_count": 1,
            }
        )
        record["pre"].update(
            {
                "interaction_kind": "native_generated_card_choice",
                "catalog_count": 1,
                "catalog_digest": hashlib.sha256(
                    json.dumps(
                        snapshot["bound_actions"],
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            }
        )
        record["action"] = {
            "bound_action_id": "bound-select",
            "verb": "select",
            "subject_referent_id": "card-1",
            "arguments": {},
            "label": "Select Strike",
        }
        record["native_witness"].update(
            {
                "origin": "native_generated_card_choice_ui",
                "native_action_type": "NChooseACardSelectionScreen.SelectHolder",
            }
        )
        record["decision_family"] = "native_generated_card_choice"

    # CatalogDigest binds the producer's ordered BoundAction JSON, so preserve
    # the recorder field order rather than canonicalizing the evidence record.
    line = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
    (raw / "run-0001.jsonl").write_text(line, encoding="utf-8", newline="\n")
    (bundle / "export" / "decisions.jsonl").write_text(line, encoding="utf-8", newline="\n")
    recording = {
        "schema_version": 2,
        "schema": "sts2.human-annotator/recording-manifest-2",
        "session_id": record["session_id"],
        "timeline_id": record["timeline_id"],
        "capture_profile_id": profile["profile_id"],
        "capture_profile_sha256": profile_sha,
    }
    (raw / "recording-manifest.json").write_text(
        _canonical(recording) + "\n", encoding="utf-8", newline="\n"
    )
    (raw / "capture-profile.json").write_text(
        _canonical(profile) + "\n", encoding="utf-8", newline="\n"
    )
    (bundle / "profile" / "capture-profile.json").write_text(
        _canonical(profile) + "\n", encoding="utf-8", newline="\n"
    )
    (raw / "coverage.json").write_text(
        _canonical(
            {
                "schema_version": 2,
                "schema": "sts2.human-annotator/coverage-2",
                "session_id": record["session_id"],
                "admitted_records": 1,
            }
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (raw / "invalidations.jsonl").write_text("", encoding="utf-8", newline="\n")
    (raw / "run-journal.jsonl").write_text(
        _canonical(
            {
                "schema_version": 2,
                "schema": "sts2.human-annotator/run-journal-event-2",
                "event_id": "journal-1",
                "session_id": record["session_id"],
                "run_id": record["run_id"],
                "timeline_id": record["timeline_id"],
                "sequence": 1,
            }
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    audit = {
        "schema": "sts2.human-annotator/session-bundle-audit-2",
        "status": "pass",
        "valid_records": 1,
        "invalid_records": 0,
        "invalidations": 0,
    }
    (bundle / "audit" / "audit-report.json").write_text(
        _canonical(audit) + "\n", encoding="utf-8", newline="\n"
    )
    export_sha = _sha_file(bundle / "export" / "decisions.jsonl")
    attestation = {
        "attested": True,
        "method": "explicit_owner_pack",
        "worker_id": "human-001",
        "machine_verifiable": False,
    }
    raw_hashes = {
        path.relative_to(raw).as_posix(): _sha_file(path)
        for path in sorted(raw.rglob("*"))
        if path.is_file()
    }
    identity = {
        "schema": "sts2.human-annotator/session-bundle-2",
        "session_id": record["session_id"],
        "timeline_id": record["timeline_id"],
        "capture_profile_id": profile["profile_id"],
        "capture_profile_sha256": profile_sha,
        "campaign_id": "human-read-rich-2026-08",
        "worker_id": "human-001",
        "human_origin_attestation": attestation,
        "record_count": 1,
        "run_ids": [record["run_id"]],
        "export_sha256": export_sha,
        "raw_file_sha256": raw_hashes,
        "audit": {
            "status": "pass",
            "valid_records": 1,
            "invalid_records": 0,
            "invalidations": 0,
        },
    }
    manifest = {
        "schema_version": 2,
        "schema": "sts2.human-annotator/session-bundle-2",
        "bundle_content_id": _sha_bytes(_canonical(identity).encode()),
        "session_id": record["session_id"],
        "timeline_id": record["timeline_id"],
        "capture_profile_id": profile["profile_id"],
        "capture_profile_sha256": profile_sha,
        "campaign_id": "human-read-rich-2026-08",
        "worker_id": "human-001",
        "human_origin_attestation": attestation,
        "record_count": 1,
        "run_ids": [record["run_id"]],
        "export_sha256": export_sha,
        "audit_status": "pass",
        "content_identity": identity,
    }
    (bundle / "session-bundle-manifest.json").write_text(
        _canonical(manifest) + "\n", encoding="utf-8", newline="\n"
    )
    _refresh_checksums(bundle)
    return bundle


def test_verified_v2_bundle_materializes_reads_into_research_projection(tmp_path: Path) -> None:
    bundle = _v2_bundle(tmp_path)
    report = import_verified_human_bundle(bundle)

    assert report.accepted_count == 1 and report.rejected_count == 0
    imported = report.accepted[0]
    assert imported.evidence_schema_version == 2
    assert set(imported.transition.state.reads) == {"run_deck", "combat_piles"}
    assert imported.transition.state.reads["run_deck"]["cards"][0]["name"] == "Strike"
    assert imported.transition.successor is not None
    assert imported.transition.successor.reads["combat_piles"]["zones"][0]["name"] == "draw"


def test_v2_export_without_verified_bundle_is_rejected(tmp_path: Path) -> None:
    bundle = _v2_bundle(tmp_path)
    report = import_human_recording(bundle / "export" / "decisions.jsonl")

    assert report.accepted_count == 0
    assert report.rejected[0].reason is HumanRecordRejection.INVALID_SCHEMA


def test_verified_generated_choice_select_uses_same_bound_action_authority(tmp_path: Path) -> None:
    report = import_verified_human_bundle(_v2_bundle(tmp_path, selector=True))

    assert report.accepted_count == 1 and report.rejected_count == 0
    transition = report.accepted[0].transition
    assert transition.state.decision_family.value == "card_choice"
    assert transition.chosen_action.kind == "choose_card"
    assert len(transition.legal_actions) == 1
