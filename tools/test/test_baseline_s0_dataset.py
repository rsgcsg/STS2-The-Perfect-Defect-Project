"""Synthetic real-format ledgers test offline joins; these are not native evidence."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("s0_dataset", ROOT / "tools/baseline-s0-dataset.py")
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def create_run(root, seed="1", suffix="one", *, extra_capture=True, continuity_change=False):
    root = root.resolve()
    directory = root / suffix
    directory.mkdir()
    (directory / "captures").mkdir()
    local = root / "host"
    template_root = local / "profile-templates/defect-a0-s0"
    template_root.mkdir(parents=True, exist_ok=True)
    inventory = []
    for name in ("default/1/settings.save", "default/1/profile1/saves/progress.save"):
        destination = template_root / "user-data" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        raw = b"synthetic fixture only"
        destination.write_bytes(raw)
        inventory.append({"path": name, "size": len(raw), "sha256": sha(raw)})
    checksum = hashlib.sha256()
    for item in inventory:
        checksum.update(f"{item['path']}\0{item['size']}\0{item['sha256']}\n".encode())
    game_identity = {
        "platform": "darwin",
        "architecture": "arm64",
        "game_version": "fixture",
        "game_commit": "commit",
        "executable_sha256": "a" * 64,
    }
    template = {
        "schema_version": 1,
        "template_id": "defect-a0-s0",
        "file_count": len(inventory),
        "files": inventory,
        "payload_sha256": checksum.hexdigest(),
        "game_identity": game_identity,
    }
    (template_root / "template.json").write_bytes(encode(template))
    profile_id = "profile-" + suffix
    (local / "profiles" / profile_id).mkdir(parents=True)
    host = {
        "runtime_instance_id": "runtime-" + suffix,
        "host_kind": "live_ui",
        "version": "1",
        "implementation": {
            "source_revision": "a" * 40,
            "artifact_sha256": "b" * 64,
            "module_version_id": "fixture-mvid",
        },
    }
    game = {
        "version": "fixture",
        "commit": "commit",
        "modset": {
            "status": "exact_platform_modset",
            "fingerprint": "c" * 64,
            "loaded_mod_ids": ["STS2_PLATFORM"],
        },
    }
    actor = {
        "id": "s0-public-menu-teacher",
        "version": "1.0.0",
        "code_sha256": "d" * 64,
        "role": "teacher",
    }
    run_id = "s0-collect-" + suffix
    source = {
        "source_kind": "agent",
        "input_spec": converter.S0_INPUT_SPEC,
        "actor": actor,
        "I": False,
        "F": False,
        "seed": seed,
        "template_id": "defect-a0-s0",
        "character_id": "DEFECT",
        "ascension": 0,
        "teacher_parameters": {"browse": False},
        "source_revision": "e" * 40,
        "source_diff_sha256": "f" * 64,
        "runner_code_sha256": "e" * 64,
        "records_code_sha256": "f" * 64,
    }
    episode = {
        "host": host,
        "game": game,
        "profile": {
            "template_id": "defect-a0-s0",
            "template_payload_sha256": template["payload_sha256"],
            "profile_id": profile_id,
            "profile_root": str(local / "profiles" / profile_id),
            "generation_id": "profile-generation-" + suffix,
            "game_identity": game_identity,
        },
        "requested_character_id": "DEFECT",
        "requested_ascension": 0,
        "episode_provenance": {
            "verdict": "provenance_pass",
            "errors": [],
            "requested_seed": converter.seed(seed),
            "actual_seed": converter.seed(seed),
            "runtime_instance_id": host["runtime_instance_id"],
            "host_status": "seed_observed",
            "transport_status": "observed",
        },
        "bootstrap_trace": [
            {
                "interaction_kind": "character_select",
                "label": "Embark",
                "delivery": "delivered",
                "successor_kind": "map_navigation",
            }
        ],
    }
    capabilities = {"host": host, "game": game, "environment_fingerprint": "environment-" + suffix}
    environment = {
        "host_kind": host["host_kind"],
        "connector_version": host["version"],
        "connector_source_revision": host["implementation"]["source_revision"],
        "connector_artifact_sha256": host["implementation"]["artifact_sha256"],
        "connector_module_version_id": host["implementation"]["module_version_id"],
        "modset_status": game["modset"]["status"],
        "modset_fingerprint": game["modset"]["fingerprint"],
        "loaded_mod_ids": game["modset"]["loaded_mod_ids"],
    }
    manifest = {
        "manifest_id": "fixture",
        "adapter": {
            "id": actor["id"],
            "version": actor["version"],
            "code_sha256": actor["code_sha256"],
            "protocol": "sts2.policy-runtime/decision-only-ndjson-2",
        },
        "artifact": {"sha256": actor["code_sha256"]},
        "representation": {"id": converter.S0_INPUT_SPEC},
        "requirements": {"environment": environment},
    }
    manifest_raw = encode(manifest)
    (directory / "policy-manifest.json").write_bytes(manifest_raw)
    runtime = {
        "version": "1",
        "code_sha256": "0" * 64,
        "manifest": manifest,
        "manifest_sha256": sha(manifest_raw),
    }
    rows = []

    def record(kind, payload):
        rows.append(
            {
                "schema": converter.RAW_SCHEMA,
                "record_index": len(rows) + 1,
                "run_id": run_id,
                "recorded_at": "2026-10-08T00:00:00Z",
                "type": kind,
                "payload": copy.deepcopy(payload),
            }
        )

    for kind, payload in (
        ("run_start", source),
        ("episode_identity", episode),
        (
            "bootstrap_controller_handoff",
            {
                "schema": "sts2.host-runtime/reference-controller-handoff-1",
                "runtime_instance_id": host["runtime_instance_id"],
                "controller": None,
                "basis": "fresh_control_observation_after_close",
            },
        ),
        ("capabilities", capabilities),
        ("runtime_identity", runtime),
    ):
        record(kind, payload)
    base = json.loads((ROOT / "python/tests/fixtures/text_menu_v2/targeted-root.json").read_text())
    base["interaction"].update(kind="map_navigation", stage="ready")
    base["interaction"]["content"] = {
        "surface": {"kind": "map_navigation"},
        "context": {"kind": "map"},
    }
    base["persistent"] = {
        "content": {
            "player": {"character_definition_id": "DEFECT", "hp": 70},
            "run": {"ascension": 0},
        }
    }
    base["session"] = {
        "runtime_instance_id": host["runtime_instance_id"],
        "environment_fingerprint": capabilities["environment_fingerprint"],
    }
    base["menu_actions"]["actions"] = [
        {
            "action_id": "travel-1",
            "kind": "native_input",
            "verb": "travel",
            "label": "Travel",
            "subject_referent_id": "card-C",
            "arguments": [],
            "effect_domain": "native_input",
        }
    ]
    base["menu_actions"].update(total_count=1, materialized_count=1)
    total_bytes = captures = 0

    def capture(snapshot):
        nonlocal total_bytes, captures
        captures += 1
        raw = json.dumps(snapshot, ensure_ascii=False, indent=2).encode()
        capture_id = f"capture-{suffix}-{captures}"
        filename = f"captures/{captures:05d}-{capture_id}.json"
        (directory / filename).write_bytes(raw)
        total_bytes += len(raw)
        envelope = {
            "schema": "sts2.player-environment/sealed-observation-1",
            "read_profile": "text-menu-v2-sealed-1",
            "input_profile": "text-menu-v2",
            "capture_id": capture_id,
            "capture_ordinal": captures,
            "source_snapshot_id": snapshot["snapshot_id"],
            "session": snapshot["session"],
            "generation_id": "sealed-" + suffix,
            "game_continuity_id": "game-" + suffix,
            "sha256": sha(raw),
            "total_bytes": len(raw),
        }
        record(
            "capture",
            {"capture": envelope, "snapshot_path": filename, "sha256": sha(raw), "bytes": len(raw)},
        )
        return envelope, filename

    for position in range(2):
        current = copy.deepcopy(base)
        current.update(sequence=20 + position, snapshot_id="snapshot-" + str(position))
        envelope, filename = capture(current)
        frame = converter.project_structured_snapshot(current)
        token = "segment-2" if continuity_change and position else "segment-1"
        record(
            "policy_offer",
            {
                "offer_id": f"offer-{position + 1}",
                "capture_id": envelope["capture_id"],
                "capture_ordinal": envelope["capture_ordinal"],
                "snapshot_path": filename,
                "snapshot_sha256": envelope["sha256"],
                "snapshot_id": current["snapshot_id"],
                "input_spec": converter.S0_INPUT_SPEC,
                "source_kind": "agent",
                "actor": actor,
                "continuity_token": token,
                "candidate_digest": frame.candidate_digest,
                "candidate_count": 1,
                "I": False,
                "F": False,
            },
        )
        output = {"candidate_digest": frame.candidate_digest, "scores": [1], "selected_index": 0}
        record(
            "policy_result",
            {
                "offer_id": f"offer-{position + 1}",
                "output": output,
                "completion": {
                    "continuity_token": token,
                    "snapshot_id": current["snapshot_id"],
                    "sequence": current["sequence"],
                },
                "chosen_action_id": "travel-1",
            },
        )
        action = current["menu_actions"]["actions"][0]
        decision = {
            "schema": "sts2.policy-runtime/decision-1",
            "run_id": run_id,
            "manifest_id": manifest["manifest_id"],
            "snapshot_id": current["snapshot_id"],
            "candidate_count": 1,
            **output,
        }
        record(
            "tick",
            {
                "type": "text_native_delivered",
                "decision": decision,
                "action": action,
                "result": {
                    "status": "applied",
                    "effect_domain": "native_input",
                    "request_id": f"request-{position + 1}",
                    "native_delivery": "delivered",
                    "action": action,
                },
                "status": {"tainted": False, "errors": []},
            },
        )
    if extra_capture:
        capture(copy.deepcopy(current))
    record(
        "runtime_stop",
        {
            "run_id": run_id,
            "lifecycle": "stopped",
            "mode": "human",
            "controller": "released",
            "tainted": False,
            "errors": [],
            "runtime": {"version": "1", "code_sha256": "0" * 64},
        },
    )
    record(
        "control_release_confirmation",
        {
            "confirmed": True,
            "basis": "fresh_control_observation_after_runtime_stop",
            "control": {
                "schema": "sts2.player-environment/control-1",
                "protocol_version": "1.0.0",
                "runtime_instance_id": host["runtime_instance_id"],
                "clients": [],
            },
        },
    )
    end = {
        "ticks": 2,
        "offers": 2,
        "captures": captures,
        "capture_bytes": total_bytes,
        "native_deliveries": 2,
        "stop_confirmed": True,
        "termination": "runtime_handoff_or_budget",
        "errors": [],
    }
    record("run_end", end)
    summary = {
        "schema": converter.SUMMARY_SCHEMA,
        "run_id": run_id,
        "mode": "collect",
        "source_kind": "agent",
        **{key: value for key, value in end.items() if key != "capture_bytes"},
    }
    write_rows(directory, rows)
    (directory / "summary.json").write_bytes(encode(summary))
    runtime_folder = directory / "runtime" / run_id
    runtime_folder.mkdir(parents=True)
    runtime_files = {
        "adapter-attestation.json": encode(
            {
                "run_id": run_id,
                "status": "attested",
                "actual": manifest["adapter"],
                "expected": manifest["adapter"],
            }
        ),
        "events.jsonl": encode(
            {"schema": "sts2.policy-runtime/agent-run-event-1", "sequence": 1, "kind": "stopped"}
        )
        + b"\n",
        "manifest.json": encode(
            {
                "schema": "sts2.policy-runtime/agent-run-1",
                "run_id": run_id,
                "status": "stopped",
                "mode": "human",
                "tainted": False,
                "ended_at": "2026-10-08T00:00:00Z",
                "append_only": True,
                "runtime_version": runtime["version"],
                "runtime_code_sha256": runtime["code_sha256"],
                "policy_manifest_sha256": converter.semantic_hash(manifest),
            }
        ),
        "policy-manifest.json": encode(manifest),
    }
    entries = [
        {"path": name, "bytes": len(raw), "sha256": sha(raw)}
        for name, raw in sorted(runtime_files.items())
    ]
    descriptor = {
        "schema": "sts2.policy-runtime/immutable-evidence-manifest-1",
        "run_id": run_id,
        "complete": True,
        "append_only": True,
        "files": entries,
        "manifest_sha256": converter.semantic_hash({"run_id": run_id, "files": entries}),
    }
    runtime_files["evidence-manifest.json"] = encode(descriptor)
    for name, raw in runtime_files.items():
        (runtime_folder / name).write_bytes(raw)
    (runtime_folder / "checksums.sha256").write_text(
        "".join(f"{sha(raw)}  {name}\n" for name, raw in sorted(runtime_files.items()))
    )
    return directory, rows


def write_rows(directory, rows):
    (directory / "records.jsonl").write_bytes(b"".join(encode(row) + b"\n" for row in rows))


def payload(rows, kind, number=0):
    return [row["payload"] for row in rows if row["type"] == kind][number]


def test_exact_capsules_offers_score_only_and_unoffered_lineage(tmp_path):
    directory, _rows = create_run(tmp_path)
    source = converter.convert_runs([("train", directory)])
    run = source["runs"][0]
    assert len(run["steps"]) == 2
    assert [row["advance"] for row in run["steps"]] == [True, False]
    assert run["identity"]["counts"]["excluded_unoffered_captures"] == 1
    assert len(run["identity"]["lineage"]) == 12
    assert all(row["capsule_json"].startswith('{\n  "protocol_version"') for row in run["steps"])


def test_explicit_continuity_change_resets_and_preserves_actual_rows(tmp_path):
    directory, _rows = create_run(tmp_path, continuity_change=True)
    source = converter.convert_runs([("train", directory)])
    assert [row["reset_before"] for row in source["runs"][0]["steps"]] == [True, True]
    assert [row["advance"] for row in source["runs"][0]["steps"]] == [True, True]


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda rows: rows[1].update(record_index=3), "ledger_order"),
        (
            lambda rows: payload(rows, "policy_offer").update(offer_id="offer-2"),
            "policy_offer_binding",
        ),
        (
            lambda rows: payload(rows, "policy_offer").update(candidate_digest="0" * 64),
            "policy_offer_binding",
        ),
        (
            lambda rows: payload(rows, "policy_result")["completion"].update(sequence=99),
            "policy_result_binding",
        ),
        (
            lambda rows: payload(rows, "policy_result").update(chosen_action_id="fabricated"),
            "selected_member",
        ),
        (
            lambda rows: payload(rows, "capture")["capture"].update(capture_ordinal=2),
            "capture_duplicate_or_gap",
        ),
        (
            lambda rows: payload(rows, "episode_identity")["episode_provenance"].update(
                actual_seed="BAD"
            ),
            "provenance_required",
        ),
        (lambda rows: payload(rows, "runtime_stop").update(tainted=True), "clean_runtime_stop"),
        (
            lambda rows: payload(rows, "control_release_confirmation")["control"].update(
                controller={"held": True}
            ),
            "control_release",
        ),
        (lambda rows: payload(rows, "tick").update(type="unknown"), "unknown_or_error"),
        (
            lambda rows: payload(rows, "tick")["result"].update(action={}),
            "native_delivery_choice_join",
        ),
    ],
)
def test_tampered_joins_flags_and_native_receipts_reject_whole_run(tmp_path, mutation, code):
    directory, rows = create_run(tmp_path)
    mutation(rows)
    write_rows(directory, rows)
    with pytest.raises(ValueError, match=code):
        converter.convert_runs([("train", directory)])


def test_capsule_corruption_and_symlink_and_traversal_are_rejected(tmp_path):
    directory, rows = create_run(tmp_path)
    capsule = directory / payload(rows, "capture")["snapshot_path"]
    original = capsule.read_bytes()
    capsule.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="capsule_bytes"):
        converter.convert_runs([("train", directory)])
    capsule.unlink()
    target = tmp_path / "outside.json"
    target.write_bytes(original)
    capsule.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        converter.convert_runs([("train", directory)])
    with pytest.raises(ValueError, match="unsafe_relative_path"):
        converter.relative_file(directory, "../outside.json")


def test_missing_result_and_ledger_truncation_fail_closed(tmp_path):
    directory, rows = create_run(tmp_path)
    rows = [
        row
        for row in rows
        if not (row["type"] == "policy_result" and row["payload"]["offer_id"] == "offer-2")
    ]
    for index, row in enumerate(rows, 1):
        row["record_index"] = index
    write_rows(directory, rows)
    with pytest.raises(ValueError):
        converter.convert_runs([("train", directory)])
    ledger = directory / "records.jsonl"
    ledger.write_bytes(ledger.read_bytes().rstrip(b"\n"))
    with pytest.raises(ValueError, match="ledger_truncated"):
        converter.convert_runs([("train", directory)])


def test_three_seed_groups_are_conditioned_on_shared_template_and_not_strong_independence(tmp_path):
    runs = [
        (split, create_run(tmp_path, seed=str(index), suffix=split)[0])
        for index, split in enumerate(("train", "dev", "test"), 1)
    ]
    source = converter.convert_runs(runs)
    assert len({run["source_group"] for run in source["runs"]}) == 3
    assert all(
        run["identity"]["statistically_strong_independence"] is False for run in source["runs"]
    )
    assert all(
        run["identity"]["grouping_policy"] == converter.GROUPING_POLICY for run in source["runs"]
    )
    output = tmp_path / "source.json"
    args = [sys.executable, str(ROOT / "tools/baseline-s0-dataset.py"), "--output", str(output)]
    for split, directory in runs:
        args += ["--run", f"{split}={directory}"]
    child = subprocess.run(args, check=True, capture_output=True, timeout=30)
    assert json.loads(child.stdout)["offers"] == 6
    assert json.loads(output.read_bytes()) == source


def test_same_canonical_seed_cross_split_and_teacher_parameter_changes_are_rejected(tmp_path):
    first, _ = create_run(tmp_path, seed="1", suffix="train")
    second, rows = create_run(tmp_path, seed="I", suffix="dev")
    with pytest.raises(ValueError, match="same_seed_cross_split"):
        converter.convert_runs([("train", first), ("dev", second)])
    payload(rows, "run_start")["teacher_parameters"]["browse"] = True
    write_rows(second, rows)
    with pytest.raises(ValueError, match="teacher_identity_or_parameters"):
        converter.convert_runs([("train", first), ("dev", second)])


def test_active_run_template_and_saved_midrun_bootstrap_are_rejected(tmp_path):
    directory, rows = create_run(tmp_path)
    template_path = tmp_path.resolve() / "host/profile-templates/defect-a0-s0/template.json"
    manifest = json.loads(template_path.read_bytes())
    manifest["files"][0]["path"] = "default/1/current_run.save"
    template_path.write_bytes(encode(manifest))
    with pytest.raises(ValueError, match="active_run_template"):
        converter.convert_runs([("train", directory)])
    create_run(tmp_path, suffix="fresh")
    payload(rows, "episode_identity")["bootstrap_trace"] = []
    write_rows(directory, rows)
    with pytest.raises(ValueError, match="fresh_native_new_run"):
        converter.convert_runs([("train", directory)])


def test_runtime_evidence_corruption_and_orphan_capsules_are_rejected(tmp_path):
    directory, rows = create_run(tmp_path)
    run_id = rows[0]["run_id"]
    events = directory / "runtime" / run_id / "events.jsonl"
    original = events.read_bytes()
    events.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="runtime_evidence_checksum_mismatch"):
        converter.convert_runs([("train", directory)])
    events.write_bytes(original)
    (directory / "captures/unrecorded.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="unrecorded_or_missing_capsule"):
        converter.convert_runs([("train", directory)])


def test_duplicate_native_request_and_unsafe_run_id_are_rejected(tmp_path):
    directory, rows = create_run(tmp_path)
    payload(rows, "tick", 1)["result"]["request_id"] = payload(rows, "tick")["result"]["request_id"]
    write_rows(directory, rows)
    with pytest.raises(ValueError, match="duplicate_native_delivery_request"):
        converter.convert_runs([("train", directory)])
    for row in rows:
        row["run_id"] = "/tmp/outside"
    write_rows(directory, rows)
    with pytest.raises(ValueError, match="safe_collect_run_id"):
        converter.convert_runs([("train", directory)])


def test_capture_runtime_environment_and_summary_error_reject_whole_run(tmp_path):
    directory, rows = create_run(tmp_path)
    payload(rows, "capture")["capture"]["session"]["environment_fingerprint"] = "other"
    write_rows(directory, rows)
    with pytest.raises(ValueError, match="capsule_bytes_or_identity"):
        converter.convert_runs([("train", directory)])
    summary_path = directory / "summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["errors"] = ["unknown"]
    summary_path.write_bytes(encode(summary))
    with pytest.raises(ValueError, match="clean_collect_summary"):
        converter.convert_runs([("train", directory)])
