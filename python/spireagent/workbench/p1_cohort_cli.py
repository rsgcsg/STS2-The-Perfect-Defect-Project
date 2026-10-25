"""One-command, explicitly confirmed cohort freeze through the existing Application."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, digest

from .developer import ProjectConfig, atomic_json
from .instance_lock import instance_lock
from .p1_cohort_intake import PREVIEW, SUMMARY, require_intake_space
from .training_cohort import checked_cohort_plan, freeze_cohort


def weight_table(plan: dict[str, Any]) -> str:
    rows = ["用途   难度  结果      决策步  TRAIN权重  录制分组"]
    for row in plan["runs"]:
        weight = row["train_weight"]
        rows.append(f"{row['split'].upper():5} A{row['ascension']!s:3} "
                    f"{(row['result'] or 'unknown'):7} {row['N']:6} "
                    f"{format(weight, '.6f') if weight is not None else '—':>9}  "
                    f"{row['root_run_id']}")
    return "\n".join(rows)


def freeze_preview(config: ProjectConfig, plan: dict[str, Any]) -> dict[str, Any]:
    """Persist the original request before offering it once; unknown is not retried."""
    with instance_lock(config.state_dir / "cohort-freeze.lock"):
        return _freeze_preview(config, plan)


def _freeze_preview(config: ProjectConfig, plan: dict[str, Any]) -> dict[str, Any]:
    plan = checked_cohort_plan(plan, plan["configuration_id"])
    directory = config.state_dir / "cohorts" / plan["configuration_id"]
    target = directory / "weight-candidates.json"
    if target.exists():
        original = json.loads(target.read_bytes())
        if original.get("status") == "frozen_user_confirmed":
            checked_cohort_plan(original, plan["configuration_id"])
            return {"file": str(target), "snapshot_id": original["snapshot_id"],
                    "configuration_id": plan["configuration_id"], "reused_receipt": True}
        raise BoundaryError("p1_cohort", "original_freeze_requires_read_only_reconciliation")
    from .local_recordings import LocalRecordingCatalog

    catalog = LocalRecordingCatalog(config).read()
    if catalog.get("status") != "ready" or catalog.get("game_running") is not False:
        raise BoundaryError("p1_cohort", "freeze_waits_for_game_exit")
    require_intake_space(config.state_dir)
    request = {
        "schema": "training-data-weight-selection-20261025-v1",
        "status": "freeze_requested", "selected_configuration_id": plan["configuration_id"],
        "configuration": plan,
        "authorization": "operator confirmed the displayed configuration via recordings cohort",
    }
    directory.mkdir(parents=True, exist_ok=True)
    atomic_json(target, request)
    snapshot_id = freeze_cohort(config, plan, plan["configuration_id"], wait_seconds=3600)
    atomic_json(target, {**request, "status": "frozen_user_confirmed", "snapshot_id": snapshot_id})
    return {"file": str(target), "snapshot_id": snapshot_id,
            "configuration_id": plan["configuration_id"], "reused_receipt": False}


def reconcile_freeze(config: ProjectConfig, configuration_id: str,
                      snapshot_id: str) -> dict[str, Any]:
    """Read the original Snapshot and ledger, without repeating publication/reservation."""
    digest(configuration_id, "p1_cohort.configuration_id")
    digest(snapshot_id, "p1_cohort.snapshot_id")
    with instance_lock(config.state_dir / "cohort-freeze.lock"):
        return _reconcile_freeze(config, configuration_id, snapshot_id)


def _reconcile_freeze(config: ProjectConfig, configuration_id: str,
                       snapshot_id: str) -> dict[str, Any]:
    from .local_recording_import import _selected_curation_owner, _selected_store
    from .p1_training_data import require_p1_snapshot
    from .training_cohort import cohort_weighting

    target = config.state_dir / "cohorts" / configuration_id / "weight-candidates.json"
    request = json.loads(target.read_bytes())
    if (request.get("status") == "frozen_user_confirmed"
            and request.get("snapshot_id") != snapshot_id):
        raise BoundaryError("p1_cohort", "frozen_receipt_is_immutable")
    plan = checked_cohort_plan(request["configuration"], configuration_id)
    store, _ = _selected_store(config, require_curation=False)
    owner = _selected_curation_owner(config)
    require_p1_snapshot(owner, store, snapshot_id)
    cohort_weighting(config, plan, configuration_id, snapshot_id)
    atomic_json(target, {**request, "status": "frozen_user_confirmed", "snapshot_id": snapshot_id})
    return {"file": str(target), "snapshot_id": snapshot_id, "configuration_id": configuration_id,
            "reconciled_read_only": True}


def main(argv: list[str]) -> int:
    from .developer_server import _local_request, running

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preview", "refresh", "freeze", "reconcile"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--confirm",
                        help="exact reviewed configuration ID; omit for interactive table")
    parser.add_argument("--snapshot-id",
                        help="original Snapshot for read-only freeze reconciliation")
    args = parser.parse_args(argv)
    config = ProjectConfig.load(args.config, require_current_combination=False)
    if args.action == "refresh":
        current = running(config)
        if current is None:
            raise BoundaryError("p1_cohort", "open_workbench_first")
        result = _local_request(
            f"http://127.0.0.1:{current['port']}/api/local-recordings/cohort/refresh",
            token=current["control_token"], body={})
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.action == "reconcile":
        if not args.confirm or not args.snapshot_id:
            parser.error("reconcile requires --confirm and --snapshot-id from the original request")
        print(json.dumps(reconcile_freeze(config, args.confirm, args.snapshot_id),
                         ensure_ascii=False))
        return 0
    plan = json.loads((config.state_dir / PREVIEW).read_bytes())
    plan = checked_cohort_plan(plan, plan["configuration_id"])
    print(weight_table(plan))
    print("配置 ID：", plan["configuration_id"])
    print("DEV / TEST 缺口：", json.dumps(plan.get("holdout_shortfall_groups"), ensure_ascii=False))
    if args.action == "preview":
        print("小结：", config.state_dir / SUMMARY)
        return 0
    if args.confirm is not None:
        if args.confirm != plan["configuration_id"]:
            raise BoundaryError("p1_cohort", "preview_changed_review_new_configuration")
    elif input("确认按上表冻结？输入 yes：").strip() != "yes":
        print("未冻结。")
        return 0
    print(json.dumps(freeze_preview(config, plan), ensure_ascii=False))
    return 0
