"""Recording intake with real Store/cache/Curation/Snapshot, synthetic source bytes.

Only game observation and HTTP transport are replaced. No Human/cloud/runtime
qualification is claimed by these fixtures.
"""
from __future__ import annotations

import errno
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_data_preparation import workspace
from test_p1_t0 import observation

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_recording_import import LocalRecordingImporter
from spireagent.workbench.p1_cohort_cli import freeze_preview, reconcile_freeze
from spireagent.workbench.p1_cohort_intake import (
    DEFAULT_POLICY,
    PREVIEW,
    load_policy,
    refresh_cohort,
    render_summary,
    select_versions,
)
from spireagent.workbench.p1_data_preparation import discover_p1_projections, freeze_p1_data
from spireagent.workbench.p1_recording_maintenance import P1RecordingMaintenance
from spireagent.workbench.p1_training_data import record_p1_training_use
from stpd.p1.data import digest, json_bytes
from stpd.p1.dataset import SOURCE_SCHEMA, TARGETS_SCHEMA

TOOL = "a" * 64


def projection(store, name, *, level=0, result="win", tool=TOOL, valid_N=True):
    branch = {"session_id": "session-" + name, "timeline_id": "timeline-" + name,
              "run_id": "run-0001"}
    journal = [{"kind": "run_started_native", "run_id": branch["run_id"]}]
    if result is not None:
        journal.append({"kind": "run_ended_native", "run_id": branch["run_id"],
                        "detail": "RunManager.OnEnded(isVictory=" +
                        ("true" if result == "win" else "false") + ")"})
    original = Manifest("evidence", PRODUCER,
        payloads=(store.put_bytes("archive", b"synthetic-original-" + name.encode()),),
        parameters=FrozenObject.of({
            "schema": SOURCE_SCHEMA, "content_id": digest(name.encode()),
            "declared_origin": "declared_human", "source_profile": "human-p1-native-decisions-1",
            "session_id": branch["session_id"], "timeline_id": branch["timeline_id"],
            "original_recording": {"recorder_source_revision": "b" * 40},
            "run_journal": journal,
            "lineage": {"root_run_id": name, "seed": "seed-" + name,
                        "ancestor_root_ids": [], "character": "DEFECT", "ascension": level,
                        "mode": "standard", "language": "en"}}))
    store.publish(original)
    obs = observation()
    obs["run"] = {"content": {"ascension": level, "floor": 48 if result == "win" else 24}}
    obs["player"] = {"content": {"character_definition_id": "DEFECT"}}
    point = {"point_id": "point-" + name, "observation": obs, "chosen": "c9",
             "source_decision_id": "decision-" + name, "source_event_id": "event-" + name,
             "mask": {"N": valid_N, "next": False, "end": result is not None},
             "end_target": {"result": result} if result else None}
    value = {"schema": TARGETS_SCHEMA, "profile": "p1-global-r1", "revision": "r1.5",
             "branch": branch, "source_content_id": digest(name.encode()), "points": [point]}
    manifest = Manifest("analysis", PRODUCER, (Parent("source", original.artifact_id),),
        (store.put_bytes("targets", json_bytes(value)),), FrozenObject.of({
            "schema": TARGETS_SCHEMA, "projection_id": "native-timeline-targets-1",
            "tool_release_id": tool, "profile": "p1-global-r1", "revision": "r1.5",
            "run_id": branch["run_id"], "source_artifact_id": original.artifact_id,
            "source_content_id": digest(name.encode()), "summary": {}}))
    store.publish(manifest)
    return manifest


def fixture(tmp_path):
    owner, store, _ = workspace(tmp_path, names=())
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    policy = {**load_policy(DEFAULT_POLICY), "tool_release_priority": [TOOL],
              "holdout_targets": {"dev": {"0": 1, "10": 1}, "test": {"0": 1, "10": 1}}}
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    for name in ("a", "b", "c"):
        projection(store, name)
    return owner, store, config, path


def test_incremental_summary_preserves_exposed_roles_and_fills_holdouts_first(tmp_path):
    owner, store, config, policy = fixture(tmp_path)
    first = refresh_cohort(config, policy)
    plan = json.loads((config.state_dir / PREVIEW).read_bytes())
    frozen = freeze_p1_data(owner, store, PRODUCER, projection_ids=[s["projection_id"]
        for s in plan["sources"]], selected_decisions=plan["selection"],
        split_by_group=plan["splits"], split_salt="initial", reserve=True)
    record_p1_training_use(owner, store, frozen["snapshot_id"], "c" * 32)
    prior = {r["root_run_id"]: r["split"] for r in plan["runs"]}
    assert first["runs"] == first["N"] == 3
    assert first["holdout_shortfall"] == {"dev": {"0": 0, "10": 1},
                                            "test": {"0": 0, "10": 1}}
    projection(store, "d", level=10, result="loss")
    second = refresh_cohort(config, policy, previous_N=first["N"])
    assert second["cache"]["decoded_projections"] == 1
    assert second["cache"]["hits"] == 3 and second["added_N"] == 1
    plan2 = json.loads((config.state_dir / PREVIEW).read_bytes())
    roles = {r["root_run_id"]: r["split"] for r in plan2["runs"]}
    assert all(roles[root] == role for root, role in prior.items())
    assert roles["d"] == "dev"
    projection(store, "e", level=10, result="loss")
    projection(store, "f", result=None)
    third = refresh_cohort(config, policy, previous_N=second["N"])
    assert third["holdout_shortfall"] == {"dev": {"0": 0, "10": 0},
                                            "test": {"0": 0, "10": 0}}
    assert third["outcomes"] == {"win": 3, "loss": 2, "unknown": 1}
    assert third["A0"] == 4 and third["A10"] == 2
    assert third["cache"]["decoded_projections"] == 2
    assert third["configuration_id"] != first["configuration_id"]
    again = refresh_cohort(config, policy, previous_N=third["N"])
    assert again["cache"]["decoded_projections"] == 0
    assert again["configuration_id"] == third["configuration_id"]
    page = render_summary(config, {"status": "failed", "error_code": "<disk_full>",
                                   "policy": {"enabled": False}})
    assert "6 局" in page and "未知 1" in page and "&lt;disk_full&gt;" in page


def test_version_rules_select_one_view_and_stop_unknown_tools(tmp_path):
    _, store, _, path = fixture(tmp_path)
    policy = load_policy(path)
    selected = select_versions(store, policy, discover_p1_projections(store))[0]
    original = store.get_manifest(selected[0])
    newer = replace(original, parameters=FrozenObject.of({**original.parameters.value(),
                                                         "tool_release_id": "d" * 64}))
    store.publish(newer)
    with pytest.raises(BoundaryError, match="unapproved_tool_release:" + "d" * 64):
        select_versions(store, policy, discover_p1_projections(store))
    explicit = {**policy, "tool_release_priority": ["d" * 64, TOOL]}
    ids, excluded = select_versions(store, explicit, discover_p1_projections(store))
    assert len(ids) == 3 and newer.artifact_id in ids and original.artifact_id not in ids
    assert any(row["reason"] == "superseded_by_approved_priority" for row in excluded)
    # User keeps the 25 prior original views even after approving a new Tool.
    # Approved priority is explicit; it must not implicitly mean latest wins.
    prior_first = {**policy, "tool_release_priority": [TOOL, "d" * 64]}
    kept = select_versions(store, prior_first, discover_p1_projections(store))[0]
    assert original.artifact_id in kept and newer.artifact_id not in kept


def test_existing_cache_over_budget_does_not_starve_summary_refresh(tmp_path, monkeypatch):
    _, _, config, policy = fixture(tmp_path)
    importer = LocalRecordingImporter(config, None)
    service = P1RecordingMaintenance(importer)
    service.configure(True, None, cohort_policy=str(policy))
    importer._maintenance["status"] = "cache_over_budget"
    # The real _tick only returns this status after positive game-exit and space
    # checks and after the original trim finished. No concurrent heavy operation.
    monkeypatch.setattr(service, "_tick", lambda: dict(importer._maintenance))
    result = service.tick()
    assert result["status"] == "cache_over_budget"
    assert result["cohort_N"] == 3
    assert (config.state_dir / "recording-summary.html").exists()


def test_zero_eligible_decisions_remain_visible_and_do_not_fill_holdouts(tmp_path):
    _, store, config, policy = fixture(tmp_path)
    projection(store, "empty-N", level=10, valid_N=False)
    summary = refresh_cohort(config, policy)
    assert summary["runs"] == 3 and summary["A10"] == 0
    assert summary["holdout_shortfall"]["dev"]["10"] == 1
    assert summary["holdout_shortfall"]["test"]["10"] == 1
    assert any(row["reason"] == "no_eligible_N" for row in summary["pending_sources"])


def test_freeze_lost_ack_reconciles_original_without_republication(tmp_path, monkeypatch):
    from spireagent.workbench import p1_data_cli
    from spireagent.workbench.local_recordings import LocalRecordingCatalog

    owner, store, config, policy = fixture(tmp_path)
    refresh_cohort(config, policy)
    plan = json.loads((config.state_dir / PREVIEW).read_bytes())
    monkeypatch.setattr(LocalRecordingCatalog, "read", lambda _: {
        "status": "ready", "game_running": False})
    offers = []

    def lost_ack(_config, action, body, **kwargs):
        assert action == "freeze" and body["reserve"] is True
        receipt = freeze_p1_data(owner, store, PRODUCER, **body["selection"], reserve=True)
        offers.append(receipt["snapshot_id"])
        raise BoundaryError("p1_data", "request_failed_inspect_snapshot_no_automatic_retry")

    monkeypatch.setattr(p1_data_cli, "p1_data_command", lost_ack)
    with pytest.raises(BoundaryError, match="no_automatic_retry"):
        freeze_preview(config, plan)
    with pytest.raises(BoundaryError, match="original_freeze_requires"):
        freeze_preview(config, plan)
    assert len(offers) == 1
    receipt = reconcile_freeze(config, plan["configuration_id"], offers[0])
    assert receipt["reconciled_read_only"] is True
    assert freeze_preview(config, plan)["reused_receipt"] is True
    assert len(offers) == 1


@pytest.mark.parametrize("game,free,status", [
    (True, 30 * 1024**3, "waiting_for_game_exit"),
    (None, 30 * 1024**3, "waiting_for_game_exit"),
    (False, 14 * 1024**3, "low_space"),
])
def test_maintenance_observes_only_before_game_exit_and_capacity(tmp_path, game, free, status):
    config = ProjectConfig(tmp_path, "", "", None, combination())
    catalog = SimpleNamespace(read=lambda: {"status": "ready", "game_running": game,
        "space": {"free_bytes": free}, "candidates": [{"candidate_id": "a" * 64,
            "recording_type": "p1_native", "session_id": "next-user-session"}]})
    importer = LocalRecordingImporter(config, catalog)
    service = P1RecordingMaintenance(importer)
    service.configure(True, None)
    assert service.tick()["status"] == status
    assert importer.thread is None and importer.operation["status"] == "idle"
    assert importer._maintenance["pending_candidates"][0]["session_id"] == "next-user-session"


def test_disk_full_during_failure_save_does_not_kill_worker_or_retry(tmp_path, monkeypatch):
    config = ProjectConfig(tmp_path, "", "", None, combination())
    importer = LocalRecordingImporter(config, None)
    service = P1RecordingMaintenance(importer)
    service.configure(True, None)
    calls = []

    def tick():
        calls.append(importer._maintenance["policy"]["enabled"])
        if calls[-1]:
            raise OSError(errno.ENOSPC, "synthetic full disk")

    monkeypatch.setattr(service, "tick", tick)
    monkeypatch.setattr(importer, "_save", lambda: (_ for _ in ()).throw(
        OSError(errno.ENOSPC, "synthetic full disk")))
    waits = iter((False, False, True))
    service.stop = SimpleNamespace(wait=lambda _: next(waits))
    service._run()
    assert calls == [True, False]
    assert importer._maintenance["error_code"] == "disk_full"
    assert importer._maintenance["persistence_error"] == "ENOSPC"
