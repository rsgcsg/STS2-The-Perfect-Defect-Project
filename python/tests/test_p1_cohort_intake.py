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


def test_failure_page_is_published_immediately_and_recovery_clears_alert(tmp_path):
    _, _, config, policy = fixture(tmp_path)
    candidates = [{"candidate_id": "a" * 64, "recording_type": "p1_native",
                   "session_id": "new-session"}]
    catalog = SimpleNamespace(read=lambda: {"status": "ready", "game_running": True,
                                           "candidates": candidates})
    importer = LocalRecordingImporter(config, catalog)
    service = P1RecordingMaintenance(importer)
    service.configure(True, None, cohort_policy=str(policy))
    service._failed(OSError(errno.EMFILE, "synthetic failure"))
    page = (config.state_dir / "recording-summary.html").read_text()
    assert "class='status danger' role='alert'" in page
    assert "自动维护失败" in page and "状态最后核对" in page
    assert page.index("maintenance-status") < page.index("<h1>")
    assert importer._maintenance["status_since"]
    # Persisted failure still observes new Close metadata without dispatching work.
    reopened = LocalRecordingImporter(config, catalog)
    service = P1RecordingMaintenance(reopened)
    state = service.tick()
    assert state["pending_candidates"][0]["session_id"] == "new-session"
    assert reopened.thread is None and reopened.operation["status"] == "idle"
    assert "已封存待处理 1 份" in (config.state_dir / "recording-summary.html").read_text()
    service.configure(True, None)
    service.tick()
    page = (config.state_dir / "recording-summary.html").read_text()
    assert "自动维护失败" not in page
    assert "class='status normal' role='status'" in page


def test_cohort_conflict_preserves_complete_view_and_does_not_disable_intake(tmp_path, monkeypatch):
    from spireagent.workbench.p1_data_preparation import P1PreparationCache

    _, store, config, policy = fixture(tmp_path)
    refresh_cohort(config, policy)
    before = {name: (config.state_dir / name).read_bytes()
              for name in ("recording-summary.json", "cohort-preview.json")}
    importer = LocalRecordingImporter(config, None)
    service = P1RecordingMaintenance(importer)
    service.configure(True, None, cohort_policy=str(policy))
    monkeypatch.setattr(service, "_tick", lambda: {**importer._maintenance, "status": "up_to_date"})
    calls = []

    def failed_inspection(*args):
        calls.append(args[2])
        raise OSError(errno.EMFILE, "synthetic historical projection read failure")

    original = P1PreparationCache.inspect
    monkeypatch.setattr(P1PreparationCache, "inspect", failed_inspection)
    state = service.tick()
    assert state["policy"]["enabled"] is True
    assert state["cohort_error"].startswith("projection_conflicts:")
    assert {name: (config.state_dir / name).read_bytes() for name in before} == before
    assert "盘点失败：入库与保全仍可继续" in (
        config.state_dir / "recording-summary.html").read_text()
    attempts = len(calls)
    service.tick()
    assert len(calls) == attempts  # No unbounded repeated reads of the failed generation.
    projection(store, "new-closed-run")
    service.tick()
    assert len(calls) > attempts  # New inputs are still discovered with dispatch enabled.
    monkeypatch.setattr(P1PreparationCache, "inspect", original)
    importer._maintenance.pop("cohort_failed_signature")  # Explicit operator refresh.
    state = service.tick()
    assert "cohort_error" not in state
    assert state["cohort_N"] == 4


def test_empty_sqlite_wal_does_not_cause_recount_loop(tmp_path, monkeypatch):
    owner, _, config, policy = fixture(tmp_path)
    importer = LocalRecordingImporter(config, None)
    service = P1RecordingMaintenance(importer)
    service.configure(True, None, cohort_policy=str(policy))
    monkeypatch.setattr(service, "_tick", lambda: {**importer._maintenance, "status": "up_to_date"})
    first = service.tick()["cohort_updated_at"]
    for _ in range(4):
        assert service.tick()["cohort_updated_at"] == first
    # The real ledger still invalidates the view when its actual contents change.
    owner.ledger.use(["a"], "training", "synthetic-usage")
    assert service.tick()["cohort_updated_at"] != first
    again = importer._maintenance["cohort_updated_at"]
    assert service.tick()["cohort_updated_at"] == again


def test_page_write_failure_does_not_stop_recording_preservation(tmp_path, monkeypatch, caplog):
    from spireagent.workbench import p1_cohort_intake

    _, _, config, policy = fixture(tmp_path)
    catalog = SimpleNamespace(read=lambda: {"status": "ready", "game_running": True,
                                           "candidates": []})
    importer = LocalRecordingImporter(config, catalog)
    service = P1RecordingMaintenance(importer)
    service.configure(True, None, cohort_policy=str(policy))
    monkeypatch.setattr(p1_cohort_intake, "write_page", lambda *a: (_ for _ in ()).throw(
        OSError(errno.EACCES, "synthetic view permission failure")))
    assert service.tick()["policy"]["enabled"] is True
    assert importer.operation["status"] == "idle"
    assert "summary page could not be written" in caplog.text


def test_unknown_operation_updates_queue_metadata_but_never_retries(tmp_path):
    config = ProjectConfig(tmp_path, "", "", None, combination())
    catalog = SimpleNamespace(read=lambda: {"status": "ready", "game_running": True,
        "candidates": [{"candidate_id": "b" * 64, "recording_type": "p1_native"}]})
    importer = LocalRecordingImporter(config, catalog)
    service = P1RecordingMaintenance(importer)
    service.configure(True, None)
    importer.operation.update(status="publication_unknown", operation_id="original-unknown")
    state = service.tick()
    assert state["status"] == "blocked" and len(state["pending_candidates"]) == 1
    assert importer.thread is None
    assert importer.operation["operation_id"] == "original-unknown"
    assert importer.operation["status"] == "publication_unknown"


@pytest.mark.parametrize("busy,checked_age,status_age,warning", [
    (False, 20, 20, None),
    (False, 120, 120, "无法确认自动入库仍在工作"),
    (True, 30, 601, "当前步骤已持续"),
    (True, 601, 601, "无法确认自动入库仍在工作"),
])
def test_saved_page_clock_detects_stale_or_slow_state(busy, checked_age, status_age, warning):
    import shutil
    import subprocess

    from spireagent.console.page import asset

    node = shutil.which("node")
    assert node, "the project portable gate requires Node"
    script = asset("recording-summary.js")[1].decode()
    # Execute the shipped script with a frozen clock and the three real DOM IDs.
    bootstrap = """
const now = Date.parse('2026-10-26T06:00:00Z');
Date.now = () => now;
const elements = {
 'maintenance-status': {dataset: DATA},
 'maintenance-stale': {hidden: true, textContent: ''},
 'maintenance-duration': {textContent: ''}
};
globalThis.document = {getElementById: id => elements[id]};
globalThis.setInterval = () => {};
"""
    data = {"busy": str(busy).lower(),
            "checkedAt": "2026-10-26T06:00:00Z", "since": "2026-10-26T06:00:00Z"}
    bootstrap = bootstrap.replace("DATA", json.dumps(data))
    bootstrap += (f"elements['maintenance-status'].dataset.checkedAt = "
                  f"new Date(now - {checked_age} * 1000).toISOString();\n"
                  f"elements['maintenance-status'].dataset.since = "
                  f"new Date(now - {status_age} * 1000).toISOString();\n")
    result = subprocess.run([node, "-e", bootstrap + script +
        "console.log(JSON.stringify(elements['maintenance-stale']));"],
        text=True, capture_output=True, check=True)
    visible = json.loads(result.stdout)
    assert visible["hidden"] is (warning is None)
    if warning:
        assert warning in visible["textContent"]


def test_fatal_intake_failure_takes_priority_over_prior_cohort_error(tmp_path):
    config = ProjectConfig(tmp_path, "", "", None, combination())
    page = render_summary(config, {"status": "failed", "policy": {"enabled": False},
        "error_code": "disk_full", "cohort_error": "old_projection_error"})
    assert "自动维护失败" in page and "磁盘写入失败" in page
    assert "入库与保全仍可继续" not in page


def test_ledger_change_after_summary_write_is_not_marked_as_already_counted(tmp_path, monkeypatch):
    from spireagent.workbench import p1_cohort_intake

    owner, _, config, policy = fixture(tmp_path)
    importer = LocalRecordingImporter(config, None)
    service = P1RecordingMaintenance(importer)
    service.configure(True, None, cohort_policy=str(policy))
    monkeypatch.setattr(service, "_tick", lambda: {**importer._maintenance, "status": "up_to_date"})
    refresh = p1_cohort_intake.refresh_cohort
    calls = []

    def concurrent_usage(*args, **kwargs):
        result = refresh(*args, **kwargs)
        calls.append(result["configuration_id"])
        if len(calls) == 1:
            root = next(row["root_run_id"] for row in result["weights"] if row["split"] == "dev")
            owner.ledger.use([root], "training", "concurrent-synthetic-usage")
        return result

    monkeypatch.setattr(p1_cohort_intake, "refresh_cohort", concurrent_usage)
    first = service.tick()["cohort_configuration_id"]
    second = service.tick()["cohort_configuration_id"]
    assert len(calls) == 2 and first != second


def test_new_weight_cli_requires_interactive_yes(tmp_path, monkeypatch, capsys):
    from spireagent.workbench import p1_cohort_cli

    _, _, config, policy = fixture(tmp_path)
    refresh_cohort(config, policy)
    monkeypatch.setattr(p1_cohort_cli.ProjectConfig, "load", lambda *a, **k: config)
    calls = []
    monkeypatch.setattr(p1_cohort_cli, "freeze_preview", lambda *a: calls.append(a))
    args = ["freeze", "--config", str(tmp_path / "project.json"),
            "--weight-rule", "a10-progress-v1"]
    with pytest.raises(BoundaryError, match="a10_rule_requires_interactive_yes"):
        p1_cohort_cli.main([*args, "--confirm", "anything"])
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert p1_cohort_cli.main(args) == 0
    assert calls == [] and "未冻结" in capsys.readouterr().out


def test_new_rule_frozen_receipt_reads_through_snapshot_consumer(tmp_path, monkeypatch):
    from spireagent.workbench import p1_data_cli
    from spireagent.workbench.local_recordings import LocalRecordingCatalog
    from spireagent.workbench.training_cohort import checked_cohort_plan, cohort_weighting
    from stpd.p1.cohort import with_a10_progress

    owner, store, config, policy = fixture(tmp_path)
    for name in ("d", "e", "f"):
        projection(store, name, level=10, result="loss")
    refresh_cohort(config, policy)
    old = json.loads((config.state_dir / PREVIEW).read_bytes())
    plan = with_a10_progress(old)
    monkeypatch.setattr(LocalRecordingCatalog, "read", lambda _: {
        "status": "ready", "game_running": False})

    def freeze(_config, action, body, **kwargs):
        assert action == "freeze"
        return freeze_p1_data(owner, store, PRODUCER, **body["selection"], reserve=True)

    monkeypatch.setattr(p1_data_cli, "p1_data_command", freeze)
    receipt = freeze_preview(config, plan)
    frozen = json.loads((config.state_dir / "cohorts" / plan["configuration_id"] /
                         "weight-candidates.json").read_bytes())
    selected = checked_cohort_plan(frozen, plan["configuration_id"])
    weights = cohort_weighting(config, selected, plan["configuration_id"], receipt["snapshot_id"])
    assert weights["definition"]["rule"] == "progress-boss-a10-progress-v1"
    for row in plan["runs"]:
        if row["split"] == "train":
            assert weights["weights_by_root"][row["root_run_id"]] == row["train_weight"]
