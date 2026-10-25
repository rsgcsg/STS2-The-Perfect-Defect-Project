"""Recording-maintenance projections over the existing P1 cache and Curation.

The preview and summary are replaceable views. Only the normal Snapshot/Curation
freeze reserves purposes; this module owns neither sources nor a second ledger.
"""
from __future__ import annotations

import html
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, digest
from stpd.p1.cohort import plan_cohort

from .developer import ROOT, ProjectConfig, atomic_json
from .p1_data_preparation import discover_p1_projections, prepare_p1_data

DEFAULT_POLICY = ROOT / "configs/p1-cohort-policy.json"
PREVIEW = "cohort-preview.json"
SUMMARY = "recording-summary.json"
PAGE = "recording-summary.html"
MIN_FREE_BYTES = 15 * 1024**3


def require_intake_space(*paths: Path) -> None:
    if min(shutil.disk_usage(path).free for path in paths) < MIN_FREE_BYTES:
        raise BoundaryError("p1_cohort", "fifteen_GiB_free_required")


def load_policy(path: Path) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 64 * 1024:
        raise BoundaryError("p1_cohort", "absolute_regular_policy_required")
    value = json.loads(path.read_bytes())
    fields = {"schema", "revision", "projection", "tool_release_priority",
              "superseded_tool_release_ids", "split_salt", "holdout_targets"}
    if (not isinstance(value, dict) or set(value) != fields
            or value["schema"] != "spireagent/p1-cohort-policy-1"
            or value["revision"] != "r1.5"
            or value["projection"] != "native-timeline-targets-1"
            or not isinstance(value["split_salt"], str) or not value["split_salt"]):
        raise BoundaryError("p1_cohort", "invalid_intake_policy")
    releases: list[str] = []
    for field in ("tool_release_priority", "superseded_tool_release_ids"):
        if not isinstance(value[field], list) or (field == "tool_release_priority"
                                                 and not value[field]):
            raise BoundaryError("p1_cohort", "explicit_tool_versions_required")
        releases.extend(digest(v, "p1_cohort.tool") for v in value[field])
    if len(releases) != len(set(releases)):
        raise BoundaryError("p1_cohort", "duplicate_tool_versions")
    targets = value["holdout_targets"]
    if (not isinstance(targets, dict) or set(targets) != {"dev", "test"}
            or any(not isinstance(v, dict) or set(v) != {"0", "10"}
                   or any(type(n) is not int or n < 0 for n in v.values())
                   for v in targets.values())):
        raise BoundaryError("p1_cohort", "invalid_holdout_targets")
    return value


def select_versions(store: Any, policy: dict[str, Any],
                    discovered: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Select one approved version per original/run; unknown tools stop dispatch.

    A priority order is explicit operator policy, never inferred from timestamps.
    Known superseded versions remain visible, without unioning their decisions.
    """
    priority = {v: n for n, v in enumerate(policy["tool_release_priority"])}
    known = set(priority) | set(policy["superseded_tool_release_ids"])
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = {}
    excluded = []
    for row in discovered:
        if (row["declared_origin"] == "declared_human"
                and row["tool_release_id"] not in known):
            raise BoundaryError("p1_cohort", "unapproved_tool_release:" +
                                str(row["tool_release_id"]))
        reason = (
            "origin_filter" if row["declared_origin"] != "declared_human"
            else "revision_filter" if row["revision"] != policy["revision"]
            else "projection_filter" if row["projection"] != policy["projection"]
            else "superseded_tool" if row["tool_release_id"] in known - set(priority)
            else None)
        if reason:
            excluded.append({**row, "reason": reason})
            continue
        if row["tool_release_id"] not in known:
            raise BoundaryError("p1_cohort", "unapproved_tool_release:" + row["tool_release_id"])
        run = store.get_manifest(row["projection_id"]).parameters.value().get("run_id")
        if not isinstance(run, str) or not run:
            raise BoundaryError("p1_cohort", "projection_run_binding_required")
        candidates.setdefault((row["source_id"], run), []).append(row)
    ids = []
    for rows in candidates.values():
        rows.sort(key=lambda r: priority[r["tool_release_id"]])
        if len(rows) > 1 and rows[0]["tool_release_id"] == rows[1]["tool_release_id"]:
            raise BoundaryError("p1_cohort", "approved_projection_version_ambiguous")
        ids.append(rows[0]["projection_id"])
        excluded.extend({**row, "reason": "superseded_by_approved_priority"} for row in rows[1:])
    return sorted(ids), excluded


def refresh_cohort(config: ProjectConfig, policy_path: Path, *,
                   previous_N: int | None = None,
                   output_dir: Path | None = None) -> dict[str, Any]:
    """Recount through the same Store, verified preparation cache and use ledger."""
    import os

    from .local_recording_import import _selected_curation_owner, _selected_store
    from .p1_recording_store import query_p1_sources
    from .service_process import cloud_environment

    os.environ.update(cloud_environment(config.cloud_environment_file))

    policy = load_policy(policy_path)
    owner = _selected_curation_owner(config)
    if owner is None:
        raise BoundaryError("p1_cohort", "curation_owner_required")
    require_intake_space(config.state_dir, owner.store_dir)
    store, _ = _selected_store(config, require_curation=False)
    ids, excluded = select_versions(store, policy, discover_p1_projections(store))
    if not ids:
        raise BoundaryError("p1_cohort", "no_approved_projections")
    prepared = prepare_p1_data(owner, store, projection_ids=ids, split_salt=policy["split_salt"])
    if prepared["conflicts"] or prepared["index"] is None:
        raise BoundaryError("p1_cohort", "projection_conflicts:" +
                            json.dumps(prepared["conflicts"]))
    facts: dict[str, dict[str, Any]] = {}
    for row in prepared["classification"].values():
        fact = row["cohort_facts"]
        root = fact["root_run_id"]
        if root in facts and fact != facts[root]:
            raise BoundaryError("p1_cohort", "conflicting_run_facts_select_one_projection")
        facts[root] = fact
    result = plan_cohort(prepared["index"], facts, owner.ledger,
                         split_salt=policy["split_salt"],
                         holdout_targets=policy["holdout_targets"])
    plan = result["plan"]
    selected_sources = {row["source_id"] for row in plan["sources"]}
    pending = [{"source_id": row["artifact_id"], "session_id": row["session_id"],
                "reason": "no_approved_projection"}
               for row in query_p1_sources(store)
               if row["declared_origin"] == "declared_human"
               and row["artifact_id"] not in selected_sources]
    pending.extend({"source_id": row["source_id"], "projection_id": row["projection_id"],
                    "session_id": row["branch"]["session_id"], "reason": "no_eligible_N"}
                   for row in plan["sources"] if row["selected_N_before_dedup"] == 0)
    runs = plan["runs"]
    counts = Counter(row["result"] or "unknown" for row in runs)
    total_N = result["index"]["unique_N"]
    summary = {
        "schema": "spireagent/p1-recording-summary-1",
        "updated_at": datetime.now(UTC).isoformat(),
        "configuration_id": plan["configuration_id"],
        "runs": len(runs), "A0": sum(r["ascension"] == 0 for r in runs),
        "A10": sum(r["ascension"] == 10 for r in runs),
        "outcomes": {k: counts[k] for k in ("win", "loss", "unknown")},
        "N": total_N, "added_N": total_N - previous_N if previous_N is not None else 0,
        "partitions": plan["partitions"],
        "holdout_shortfall": result["allocation"].get("holdout_shortfall_groups"),
        "ready_to_freeze": result["allocation"]["ready_to_freeze"],
        "pending_sources": pending, "excluded_versions": excluded,
        "weights": [{k: row[k] for k in ("root_run_id", "split", "ascension", "result", "N",
                                         "train_weight", "proposed_run_weight")} for row in runs],
        "cache": prepared["cache"], "elapsed_seconds": prepared["elapsed_seconds"],
        "isolation_claim": plan["isolation_claim"],
    }
    destination = output_dir or config.state_dir
    atomic_json(destination / PREVIEW, plan)
    atomic_json(destination / SUMMARY, summary)
    return summary


STATUS_TEXT = {
    "disabled": "自动维护已暂停", "failed": "自动维护失败，请按下方原因处理",
    "blocked": "已停止派发，先核对原操作", "unavailable": "录制目录暂不可用",
    "waiting_for_game_exit": "等游戏退出后处理；现在只读封存元数据",
    "low_space": "空间不足 15 GiB，已停止派发", "running": "正在串行处理封存录制",
    "completed": "本局处理完成", "up_to_date": "已更新，等待下一局",
    "operation_in_progress": "正在处理原操作", "refreshing_cohort": "正在增量盘点",
    "cooling": "正在按原策略周转热副本", "cache_trimming": "正在维护原 Store 缓存",
    "cache_over_budget": "缓存暂超上限，原件保留；盘点仍按已验证数据更新",
}


def render_summary(config: ProjectConfig, maintenance: dict[str, Any], *,
                   standalone: bool = False) -> str:
    """Render saved counts and live failure status; no Store reads or work dispatch."""
    try:
        value = json.loads((config.state_dir / SUMMARY).read_bytes())
    except (FileNotFoundError, ValueError):
        value = {}
    def esc(value: Any) -> str:
        return html.escape(str(value))

    status = maintenance.get("status", "disabled")
    message = STATUS_TEXT.get(status, status)
    if not maintenance.get("policy", {}).get("enabled") and status != "failed":
        message = "自动维护已暂停 · " + message
    style = (("<style>" + (ROOT / "spireagent/console/recording-summary.css").read_text() +
              "</style>") if standalone
             else "<link rel='stylesheet' href='/assets/recording-summary.css'>")
    parts = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'>",
             "<meta http-equiv='refresh' content='15'><title>录制数据小结</title>",
             style,
             "<h1>录制数据小结</h1><p class='status'>" + esc(message) + "</p>"]
    if maintenance.get("error_code"):
        parts.append("<p>卡住原因：<code>" + esc(maintenance["error_code"]) + "</code></p>")
    if maintenance.get("persistence_error"):
        parts.append("<p>状态未能写盘：<code>" +
                     esc(maintenance["persistence_error"]) + "</code></p>")
    last = maintenance.get("last_intake") or {}
    if last.get("sealed_to_summary_seconds") is not None:
        parts.append(f"<p>最近一份：封存到小结 {last['sealed_to_summary_seconds']:.1f} 秒"
                     f"（含等待游戏退出）；处理 {last['processing_seconds']:.1f} 秒。</p>")
    if value:
        outcomes = value["outcomes"]
        parts += [f"<h2>{value['runs']} 局 · A0 {value['A0']} · A10 {value['A10']}</h2>",
                  f"<p>胜 {outcomes['win']} · 负 {outcomes['loss']} · 未知 {outcomes['unknown']}"
                  f"<br>有效决策 {value['N']:,} 步，本次新增 {value['added_N']:+,} 步</p>",
                  "<table><tr><th>用途</th><th>现有局数</th><th>A0 还缺</th><th>A10 还缺</th></tr>"]
        for role in ("train", "dev", "test"):
            gaps = (value.get("holdout_shortfall") or {}).get(role, {})
            parts.append(f"<tr><td>{role.upper()}</td>"
                         f"<td>{value['partitions'][role]['runs']}</td>"
                         f"<td>{gaps.get('0', '—')}</td><td>{gaps.get('10', '—')}</td></tr>")
        parts += ["</table><p>最后盘点：" + esc(value["updated_at"]) +
                  "；本页每 15 秒刷新。录制中不重算历史数据。</p>"]
    else:
        parts.append("<p>尚无盘点结果；等待游戏退出后首次更新。</p>")
    pending = maintenance.get("pending_candidates", [])
    sources = value.get("pending_sources", [])
    parts.append(f"<p>封存后待处理 {len(pending)} 份；Store 内未入选 {len(sources)} 份。"
                 "一份录制可能含多局，未验证前不加进上面的局数。</p>")
    if pending or sources:
        parts.append("<details><summary>查看卡住或排队的录制</summary><ul>")
        parts.extend("<li><code>" + esc(row.get("session_id") or row.get("candidate_id")
                                         or row.get("source_id")) + "</code> · " +
                     esc({"no_eligible_N": "没有可用决策步，未计入局数或补足DEV/TEST缺口",
                          "no_approved_projection": "尚无已批准版本的派生结果"}.get(
                              row.get("reason"), row.get("reason", message))) + "</li>"
                     for row in [*pending, *sources])
        parts.append("</ul></details>")
    if value:
        parts += ["<details><summary>冻结前权重表</summary>",
                  "<p>胜局 3；负/未知按原进度与 Boss 公式。DEV/TEST 不加权。"
                  "确认本表的配置 ID 后，运行 SOP 中的一条冻结命令。</p>",
                  "<table><tr><th>局</th><th>用途</th><th>难度</th><th>结果</th>"
                  "<th>决策步</th><th>TRAIN 权重</th></tr>"]
        for n, row in enumerate(value["weights"], 1):
            weight = row["train_weight"]
            parts.append(f"<tr><td title='{esc(row['root_run_id'])}'>{n}</td>"
                         f"<td>{esc(row['split'].upper())}</td><td>A{row['ascension']}</td>"
                         f"<td>{esc(row['result'] or '未知')}</td><td>{row['N']}</td>"
                         f"<td>{format(weight, '.6f') if weight is not None else '—'}</td></tr>")
        parts += ["</table></details><p>配置 ID：<code>" + esc(value["configuration_id"]) +
                  "</code></p><small>这些是观察到的整局分组；未知祖先/种子不等于独立性证明。"
                  "盘点不冻结、不训练；模型强弱以真实游戏评估为准。</small>"]
    return "".join(parts) + "</html>"


def write_page(config: ProjectConfig, maintenance: dict[str, Any]) -> None:
    # This replaceable view has no effect on the original operation or ledger.
    content = render_summary(config, maintenance, standalone=True)
    path = config.state_dir / PAGE
    temporary = config.state_dir / (PAGE + ".pending")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
