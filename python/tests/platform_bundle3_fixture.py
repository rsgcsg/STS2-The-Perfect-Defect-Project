"""Synthetic owner-format fixture; never distributed as Human evidence."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from test_human_evidence_v2 import _refresh_checksums, _sha_file, _v2_bundle

from spireagent.json_boundary import decode_json, json_bytes
from stpd.canonical import semantic_hash


def load(path: Path) -> dict[str, Any]:
    return decode_json(path.read_bytes())


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))


def stream(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_bytes(b"".join(json_bytes(row) for row in rows))


def rows(path: Path) -> list[dict[str, Any]]:
    return [decode_json(row) for row in path.read_bytes().splitlines()]


def seal(bundle: Path) -> None:
    raw = bundle / "raw"
    export = bundle / "export/canonical-transitions.jsonl"
    export.write_bytes((raw / "canonical-transitions.jsonl").read_bytes())
    manifest = load(bundle / "session-bundle-manifest.json")
    audit = load(bundle / "audit/audit-report.json")
    manifest["export_sha256"] = _sha_file(export)
    identity = {
        key: manifest[key]
        for key in (
            "schema",
            "session_id",
            "timeline_id",
            "capture_profile_id",
            "capture_profile_sha256",
            "campaign_id",
            "worker_id",
            "human_origin_attestation",
            "canonical_count",
            "run_ids",
            "export_sha256",
            "packer",
        )
    }
    identity.update(
        raw_file_sha256={
            path.relative_to(raw).as_posix(): _sha_file(path)
            for path in sorted(raw.rglob("*"))
            if path.is_file()
        },
        audit_sha256=_sha_file(bundle / "audit/audit-report.json"),
        audit={
            key: audit[key]
            for key in (
                "status",
                "valid_records",
                "invalid_records",
                "invalidations",
                "canonical_count",
            )
        },
    )
    manifest.update(content_identity=identity, bundle_content_id=semantic_hash(identity))
    write(bundle / "session-bundle-manifest.json", manifest)
    _refresh_checksums(bundle)


def bundle3(tmp_path: Path, *, runs: int = 3, public_bindings: bool = False) -> Path:
    bundle = _v2_bundle(tmp_path)
    raw = bundle / "raw"
    old = load(raw / "run-0001.jsonl")
    recording = load(raw / "recording-manifest.json")
    recording.update(decision_schema_version=2, disposition_schema_version=1)
    write(raw / "recording-manifest.json", recording)
    (raw / "run-0001.jsonl").unlink()
    (bundle / "export/decisions.jsonl").unlink()
    profile = load(raw / "capture-profile.json")
    profile["supported_action_families"] = ["ordinary_combat.play_card", "nested_selector.decision"]
    write(raw / "capture-profile.json", profile)
    write(bundle / "profile/capture-profile.json", profile)
    recording["capture_profile_sha256"] = semantic_hash(profile)
    write(raw / "recording-manifest.json", recording)
    trace = []
    canonical = []
    journal = []

    def journal_event(kind: str, run_id: str, when: str) -> None:
        journal.append(
            {
                "schema_version": 2,
                "schema": "sts2.human-annotator/run-journal-event-2",
                "session_id": recording["session_id"],
                "timeline_id": recording["timeline_id"],
                "run_id": run_id,
                "kind": kind,
                "sequence": len(journal) + 1,
                "event_id": f"journal-{len(journal)}",
                "recorded_at": when,
            }
        )

    def object_ref(value: dict[str, Any], prefix: str) -> dict[str, Any]:
        content = json_bytes(value)
        import hashlib

        sha = hashlib.sha256(content).hexdigest()
        relative = f"{prefix}/sha256/{sha[:2]}/{sha}.json"
        (raw / relative).parent.mkdir(parents=True, exist_ok=True)
        (raw / relative).write_bytes(content)
        return {"object_ref": relative, "content_sha256": sha}

    journal_event("session_started", "run-0001", "2026-09-01T00:00:00Z")
    for run in range(runs):
        run_id = f"run-{run + 1:04}"
        hour = f"{run:02}"
        journal_event("run_started_native", run_id, f"2026-09-01T{hour}:01:00Z")
        parent = f"decision-{run}-0"
        root = f"root-{run}"
        for step in range(2):
            witness = root if step == 0 else f"child-{run}"
            selected = {
                "bound_action_id": f"bound-{run}-{step}",
                "verb": "select",
                "subject_referent_id": "card-1",
                "arguments": {},
            }
            refs = []
            for phase in ("pre", "successor"):
                frame = copy.deepcopy(old[phase])
                snapshot = frame["snapshot"]
                snapshot_id = f"snapshot-{run}-{step}-{phase}"
                frame["snapshot_id"] = snapshot["snapshot_id"] = snapshot_id
                snapshot["session"]["environment_fingerprint"] = "e" * 64
                snapshot["persistent"] = {"content": {"player": {"hp": 50 + run}}}
                snapshot["bound_actions"].update(
                    status="complete",
                    total_count=1,
                    materialized_count=1,
                    actions=[{**selected, "arguments": []}],
                )
                snapshot["completeness"] = {"status": "complete"}
                snapshot["interaction"]["content"] = {"surface": {"kind": "fixture"}}
                snapshot["interaction"]["kind"] = (
                    "game_over" if step == 1 and phase == "successor" else "combat_turn"
                )
                frame["catalog_count"] = 1
                if public_bindings:
                    snapshot["schema"] = "sts2.player-environment/snapshot-1"
                    snapshot["information_policy"]["includes_hidden_information"] = False
                    snapshot["interaction"]["interaction_id"] = "interaction-fixture"
                    for candidate in snapshot["bound_actions"]["actions"]:
                        candidate["interaction_id"] = "interaction-fixture"
                for read in frame["reads"]:
                    read["snapshot_id"] = snapshot_id
                refs.append({**object_ref(frame, "semantic-frames"), "snapshot_id": snapshot_id})
            decision = {
                "schema_version": 2,
                "decision_id": f"decision-{run}-{step}",
                "causal_root_id": root,
                "parent_decision_id": parent if step else None,
                "decision_kind": "nested_selector" if step else "root",
                "native_owner_witness_id": "owner" if step else None,
                "surface": "fixture_selector" if step else "combat_turn",
                "family": "nested_selector.decision" if step else "ordinary_combat.play_card",
            }
            action = {
                "action_witness_id": witness,
                "record_id": f"record-{run}-{step}",
                "action_sequence": run * 2 + step + 1,
                "run_id": run_id,
                "native_mechanism": "direct_ui_commit",
                "decision": decision,
                "bound_action": selected,
            }
            common = {
                "schema_version": 4,
                "schema": "sts2.human-annotator/semantic-evidence-event-4",
                "session_id": recording["session_id"],
                "timeline_id": recording["timeline_id"],
                "run_id": run_id,
                "action": action,
                "observed_at": f"2026-09-01T{hour}:02:0{step}Z",
            }
            if public_bindings:
                action["human_observation_snapshot_id"] = refs[0]["snapshot_id"]
                action["mapping"] = {"status": "exact_unique", "match_count": 1,
                                     "basis": "reference_equality_to_frozen_host_binding"}
                common["human_observation_ref"] = refs[0]
            trace.append({**common, "sequence": len(trace) + 1, "kind": "action_accepted"})
            trace.append({**common, "sequence": len(trace) + 1, "kind": "action_finished"})
            trace.append(
                {
                    **common,
                    "sequence": len(trace) + 1,
                    "kind": "transition_proved",
                    "execution_pre_ref": refs[0],
                    "successor_ref": refs[1],
                }
            )
            canonical.append(
                {
                    "schema_version": 3,
                    "schema": "sts2.human-annotator/canonical-transition-evidence-3",
                    "collection_mode": "causal_human_native_observation",
                    "proof_status": "canonical_s_a_s_prime",
                    "session_id": recording["session_id"],
                    "timeline_id": recording["timeline_id"],
                    "run_id": run_id,
                    "transition_id": "canonical-" + action["record_id"],
                    "action_sequence": action["action_sequence"],
                    "action_witness_id": witness,
                    "native_mechanism": action["native_mechanism"],
                    "decision": decision,
                    "action": selected,
                    "pre_state_ref": refs[0],
                    "successor_ref": refs[1],
                    "action_space_authority": "public_bound_actions",
                }
            )
        journal_event("run_ended_native", run_id, f"2026-09-01T{hour}:03:00Z")
    journal_event("session_closed", run_id, "2026-09-02T00:00:00Z")
    stream(raw / "semantic-boundary-trace.jsonl", trace)
    stream(raw / "canonical-transitions.jsonl", canonical)
    stream(raw / "run-journal.jsonl", journal)
    manifest = load(bundle / "session-bundle-manifest.json")
    manifest.update(
        schema="sts2.human-annotator/session-bundle-3",
        schema_version=3,
        canonical_count=len(canonical),
        run_ids=[f"run-{i + 1:04}" for i in range(runs)],
        capture_profile_sha256=semantic_hash(profile),
        packer={
            "product": "STS2 Native UI Human Annotator Tool",
            "version": "fixture",
            "source_revision": "c" * 40,
        },
    )
    manifest.pop("record_count")
    write(bundle / "session-bundle-manifest.json", manifest)
    write(
        bundle / "audit/audit-report.json",
        {
            "schema": "sts2.human-annotator/session-bundle-audit-3",
            "status": "pass",
            "canonical_count": len(canonical),
            "valid_records": 0,
            "invalid_records": 0,
            "invalidations": 0,
            "errors": {},
        },
    )
    seal(bundle)
    return bundle
