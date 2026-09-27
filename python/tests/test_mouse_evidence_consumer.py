from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
import sysconfig
from pathlib import Path

import pytest
from sts2_platform_evidence.human_text_inputs import verify_human_text_inputs

from spireagent.workbench.developer import combination

MOUSE_MECHANISMS = (
    ("mouse_canceled_input_signal", "cancel_card_play"),
    ("mouse_target_finish_input", "confirm_target"),
    ("mouse_target_canceled_input", "cancel_card_play"),
)


def _synthetic_row(mechanism: str, verb: str) -> dict[str, object]:
    action: dict[str, object] = {
        "action_id": "mouse-action",
        "kind": "native_input",
        "verb": verb,
        "label": "Mouse input",
        "subject_referent_id": "subject",
        "arguments": [],
        "effect_domain": "native_input",
    }
    snapshot: dict[str, object] = {
        "schema": "sts2.player-environment/text-menu-snapshot-1",
        "input_profile": "text-menu-v1",
        "protocol_version": "1.0.0",
        "snapshot_id": "snapshot-1",
        "sequence": 1,
        "observed_at": "2026-09-27T00:00:00Z",
        "persistent": None,
        "status": "interactive",
        "completeness": {"status": "complete"},
        "session": {
            "runtime_instance_id": "runtime-1",
            "environment_fingerprint": "environment-1",
        },
        "interaction": {
            "interaction_id": "combat-1",
            "kind": "combat_card_operation",
            "content_schema": "combat_card_operation-1",
            "content": {"surface": {"kind": "combat"}, "context": {}},
        },
        "referents": [
            {"referent_id": "subject", "kind": "entity", "role": "card", "state": {"visible": True}}
        ],
        "information_policy": {"scope": "current_page", "includes_hidden_information": False},
        "menu": {"cursor": "root", "revision": 0, "native_snapshot_id": "native-1"},
        "menu_actions": {
            "status": "complete",
            "materialized_count": 1,
            "total_count": 1,
            "ordering_semantics": "native_order_with_fixed_information_groups",
            "actions": [action],
        },
    }
    snapshot_bytes = json.dumps(snapshot, separators=(",", ":")).encode("utf-8")
    artifact = {
        "product": "synthetic",
        "version": "1",
        "source_revision": "b" * 40,
        "source_digest_sha256": "a" * 64,
        "sha256": "a" * 64,
        "module_version_id": "11111111-1111-1111-1111-111111111111",
    }
    return {
        "schema_version": 1,
        "schema": "sts2.human-annotator/human-text-input-1",
        "sequence": 1,
        "record_id": "mouse-record-1",
        "session_id": "session-1",
        "timeline_id": "timeline-1",
        "run_id": "run-1",
        "observed_at": "2026-09-27T00:00:00Z",
        "recorded_at": "2026-09-27T00:00:01Z",
        "environment": {
            "game": {
                "main_assembly_sha256": "a" * 64,
                "main_assembly_module_version_id": artifact["module_version_id"],
            },
            "connector": artifact,
            "annotator": artifact,
            "player_environment_protocol": "1.0.0",
            "runtime_instance_id": "runtime-1",
            "environment_fingerprint": "environment-1",
            "modset_status": "exact",
            "modset_fingerprint": "a" * 64,
        },
        "snapshot": snapshot,
        "snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        "chosen_action": action,
        "mapping_status": "exact_unique",
        "match_count": 1,
        "mapping_basis": "text_menu_native_reference_equality",
        "native_owner_witness_id": "carrier-1",
        "native_subject_witness_id": "subject-witness-1",
        "native_carrier_witness_id": "carrier-1",
        "native_mechanism": mechanism,
        "disposition": "accepted_input",
        "external_controller_active": False,
    }


@pytest.mark.parametrize(("mechanism", "verb"), MOUSE_MECHANISMS)
def test_locked_evidence_consumer_accepts_native_mouse_text_input(
    tmp_path: Path, mechanism: str, verb: str
) -> None:
    expected_revision = combination()["evidence_source_revision"]
    package = importlib.metadata.distribution("rsgcsg-sts2-platform-evidence")
    direct_url = json.loads(package.read_text("direct_url.json") or "{}")
    installed_revision = direct_url.get("vcs_info", {}).get("commit_id")
    source_file = Path(inspect.getfile(verify_human_text_inputs)).resolve()
    purelib = Path(sysconfig.get_paths()["purelib"]).resolve()
    assert installed_revision == expected_revision
    assert source_file.is_relative_to(purelib)

    row = _synthetic_row(mechanism, verb)
    contents = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
    raw = tmp_path / mechanism
    raw.mkdir()
    (raw / "human-text-inputs.jsonl").write_bytes(contents)
    result = verify_human_text_inputs(
        raw,
        {"text_input_schema_version": 1, "session_id": "session-1", "timeline_id": "timeline-1"},
        {
            "human_text_input_count": 1,
            "human_text_inputs_sha256": hashlib.sha256(contents).hexdigest(),
        },
        ("run-1",),
    )
    assert result[0]["native_mechanism"] == mechanism
    assert result[0]["chosen_action"]["verb"] == verb
