from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_managed_suite import snapshot

from spireagent.managed_task_outcome import (
    INDEX_SCHEMA,
    SOURCE_FILES,
    TASK_SPEC,
    ManagedEvidenceIndex,
    project_managed_episode,
)


def fixture(directory, *, terminal=True, unknown=False, cleanup_unknown=False):
    root = Path(__file__).resolve().parents[1]
    files = {}
    for relative in SOURCE_FILES:
        data = (root / relative).read_bytes()
        files["evaluation-source/" + relative] = data
    digest = hashlib.sha256()
    for relative in sorted(SOURCE_FILES[:4]):
        digest.update(
            ("python/" + relative).encode() + b"\0" + files["evaluation-source/" + relative]
        )
    config = {
        "task_spec": TASK_SPEC,
        "agent_input_scope": {
            "selection_input": "complete_bound_action_ids_only",
            "read_calls": [],
        },
        "manifest": {
            "seeds": ["STPD123"],
            "character": "Defect",
            "ascension": 0,
            "game_version": "v0.111.0",
        },
        "limits": {"max_steps": 10},
    }
    meta = {
        "agent_id": "random",
        "evaluation_code_sha256": digest.hexdigest(),
        "source_file_sha256": {
            r: hashlib.sha256(files["evaluation-source/" + r]).hexdigest() for r in SOURCE_FILES
        },
    }
    exact = {"version": "v0.111.0", "sts2_dll_sha256": "a" * 64}
    expected = {
        "artifact_sha256": "b" * 64,
        "artifact_mvid": "mvid",
        "source_patch_sha256": "c" * 64,
    }
    ready = {
        "exact_game": exact,
        "candidate_manifest": {
            "exact_game": exact,
            "expected_build": expected,
            "upstream": {"revision": "d" * 40},
        },
        "candidate_build": {
            **expected,
            "upstream_revision": "d" * 40,
            "original_sts2_sha256": "a" * 64,
            "runtime_sts2_sha256": "a" * 64,
        },
        "runtime_identity": {
            "host_assembly_sha256": "b" * 64,
            "host_module_mvid": "mvid",
            "sts2_assembly_sha256": "a" * 64,
        },
        "headless": {"source_digest_sha256": "e" * 64, "source_revision": "f" * 40},
        "adapter_runtime_instance_id": "r1",
        "environment_fingerprint": "e1",
    }
    events = [
        ("ready", ready),
        ("reset_offer", {"seed": "STPD123"}),
        ("reset_snapshot", snapshot()),
        (
            "episode_identity",
            {
                "episode_provenance": {
                    "verdict": "provenance_pass",
                    "requested_seed": "STPD123",
                    "actual_seed": "STPD123",
                    "runtime_instance_id": "r1",
                }
            },
        ),
        ("snapshot", snapshot()),
    ]
    if unknown:
        offer = {"request_id": "q1", "snapshot_id": "s1", "action_id": "a"}
        events.extend(
            [("step_offer", offer), ("step", {**offer, "receipt": {"delivery": "unknown"}})]
        )
    elif terminal:
        events.append(("snapshot", snapshot(True)))
    close = {
        "driver_exit_code": -15 if cleanup_unknown else 0,
        "forced_fallback": cleanup_unknown,
        "cleanup_error": "unconfirmed" if cleanup_unknown else None,
    }
    events.extend(
        [
            ("close_receipt", close),
            (
                "episode_end",
                {
                    "terminal_outcome": "loss" if terminal and not unknown else None,
                    "unknown_delivery": unknown,
                    "censor_reason": "unknown_delivery"
                    if unknown
                    else "cleanup_unknown"
                    if cleanup_unknown
                    else None
                    if terminal
                    else "step_budget",
                },
            ),
        ]
    )
    files["config.json"] = json.dumps(config).encode()
    files["meta.json"] = json.dumps(meta).encode()
    files["episode-0001/events.jsonl"] = b"".join(
        json.dumps({"sequence": i + 1, "kind": kind, "value": value}).encode() + b"\n"
        for i, (kind, value) in enumerate(events)
    )
    return files


def publish(directory, files):
    directory.mkdir(exist_ok=True)
    entries = []
    for relative, raw in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        entries.append(
            {"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    raw = json.dumps({"schema": INDEX_SCHEMA, "files": entries}).encode()
    (directory / "evidence-index.json").write_bytes(raw)
    return ManagedEvidenceIndex(directory, hashlib.sha256(raw).hexdigest())


def test_actual_owner_projects_natural_task_and_cleanup_separately(tmp_path):
    result = project_managed_episode(publish(tmp_path, fixture(tmp_path)), 1)
    assert result["evidence"]["verification"] == "managed_raw_files_verified"
    assert result["task_completion"]["status"] == "completed"
    assert result["terminal_observation"]["result"] == "loss"
    assert result["operation_end"]["status"] == "task_ended"
    assert result["evidence"]["sealed_policy_runtime_evidence"] is False
    files = fixture(tmp_path, cleanup_unknown=True)
    result = project_managed_episode(publish(tmp_path, files), 1)
    assert result["task_completion"]["status"] == "completed"
    assert result["operation_end"]["status"] == "unknown"


def test_unknown_remains_unproved_even_with_clean_exit(tmp_path):
    result = project_managed_episode(publish(tmp_path, fixture(tmp_path, unknown=True)), 1)
    assert result["task_completion"]["status"] == "unproved"
    assert result["operation_end"]["status"] == "unknown"
    assert result["terminal_observation"]["status"] == "not_observed"


@pytest.mark.parametrize(
    "change", ["source", "task", "seed", "runtime", "loaded", "terminal", "grammar", "read"]
)
def test_raw_binding_or_semantic_mismatch_never_completes(tmp_path, change):
    files = fixture(tmp_path)
    if change == "source":
        files["evaluation-source/" + SOURCE_FILES[0]] += b"\n# changed\n"
    elif change == "task":
        config = json.loads(files["config.json"])
        config["task_spec"] = {"id": "other"}
        files["config.json"] = json.dumps(config).encode()
    else:
        events = [json.loads(line) for line in files["episode-0001/events.jsonl"].splitlines()]
        if change == "seed":
            events[3]["value"]["episode_provenance"]["actual_seed"] = "OTHER"
        elif change == "runtime":
            events[5]["value"]["session"]["runtime_instance_id"] = "another"
        elif change == "loaded":
            events[0]["value"]["runtime_identity"]["host_assembly_sha256"] = "different"
        elif change == "terminal":
            events[5]["value"]["interaction"]["content"]["surface"]["victory"] = None
        elif change == "grammar":
            events.pop()
        elif change == "read":
            events[4]["kind"] = "read"
        files["episode-0001/events.jsonl"] = b"".join(
            json.dumps(e).encode() + b"\n" for e in events
        )
    result = project_managed_episode(publish(tmp_path, files), 1)
    assert result["task_completion"]["status"] == "unknown"
    assert result["evidence"]["verification"] == "managed_raw_unverified"


def test_file_changed_after_index_acceptance_is_detected(tmp_path):
    index = publish(tmp_path, fixture(tmp_path))
    with (tmp_path / "episode-0001/events.jsonl").open("ab") as handle:
        handle.write(b" ")
    assert project_managed_episode(index, 1)["task_completion"]["status"] == "unknown"


@pytest.mark.parametrize("missing", ["limits", "agent_id"])
def test_late_projection_failure_cannot_retain_partial_success(tmp_path, missing):
    files = fixture(tmp_path)
    target = "config.json" if missing == "limits" else "meta.json"
    value = json.loads(files[target])
    del value[missing]
    files[target] = json.dumps(value).encode()
    result = project_managed_episode(publish(tmp_path, files), 1)
    assert result["task_completion"]["status"] == "unknown"
    assert result["terminal_observation"]["status"] == "unknown"
    assert result["operation_end"]["status"] == "unknown"
    assert result["evidence"]["verification"] == "managed_raw_unverified"
