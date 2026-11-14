#!/usr/bin/env python3
"""Start checks for the fast chain: any failure refuses the run, with a clear message.

Two halves, both decided by the pure functions below:
- login side (`checks(plan, facts)`), before anything is submitted: layout placement,
  the GPU request (sinfo snapshot and --test-only printed only), machine types, prep
  scratch, prep jobs, speed switches, source features the knobs need, producer identity,
  env package identity and memo, env nodes and shard balance, memory budget, cross-node
  identity, hot path, secret files, time limits, core cap, the queue (printed only);
- job side (`preflight.py job-gpu | prep-host | env-node`), when a role starts on its
  node: dependencies + python_env_sha256, node-local hot path, prep scratch free space,
  env sha equal to the GPU side's.

Every item comes from a run that hit it (playbook §11.4/§12, V3-c3c 11-13).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import learner_config  # noqa: E402
import run_profile  # noqa: E402

GIB = 1024**3
# Node-local file systems (`stat -f -c %T` names). Anything else, including "unknown"
# and the "UNKNOWN (0x...)" that BeeGFS/Ceph mounts print, is refused.
LOCAL_FS = ("tmpfs", "xfs", "ext2/ext3", "ext4", "btrfs", "overlayfs", "overlay", "zfs")
HEX12 = re.compile(r"^[0-9a-f]{12,64}$")
# sgd_process.DECLARED_BYTES (the learner's own memory budget bases).
CONTROLLER_BASE, WORKER, PUBLISHER, SGD_BASE = 6 * GIB, 1.5 * GIB, 1 * GIB, 2.5 * GIB
HEADROOM = 0.20  # sgd_process memory_headroom_fraction default
SEED_STRIDE_PROCS = 32  # env.sh seed offset = (node * 32 + p) * 600


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str
    warn: bool = False  # printed as WARN, never refuses


def _under(path: str, prefixes: list[str]) -> bool:
    clean = os.path.normpath(path)
    return any(clean == p.rstrip("/") or clean.startswith(p.rstrip("/") + "/")
               for p in prefixes)


def check_placement(plan: dict[str, Any]) -> Result:
    p = plan["placement"]
    problems = []
    if p["ranks"] != len(p["train_cards"]):
        problems.append("ranks != training cards")
    if not p["shared_inference_card"] and p["service_card"] in p["learner_visible"] \
            and p["ranks"] == 1:
        problems.append("single-rank learner can see the inference card (c2a: it landed there)")
    if p["ranks"] == 1 and (p["learner_device"] != "cuda" or len(p["learner_visible"]) != 1):
        problems.append("single rank must see only its card and use plain 'cuda' (c2b)")
    if p["ranks"] > 1:
        first = p["train_cards"][0]
        if p["learner_device"] != f"cuda:{first}" or \
                not set(p["train_cards"]) <= set(p["learner_visible"]):
            problems.append("data-parallel ranks must start at cuda:<first training card>")
    roles = ", ".join(f"card {i}: {'+'.join(card)}" for i, card in enumerate(p["layout"]))
    detail = (f"{p['name']} | {roles} | services -> card {p['service_card']} | learner sees "
              f"{list(p['learner_visible'])} device {p['learner_device']} ranks {p['ranks']}")
    return Result("placement", not problems, "; ".join(problems + [detail]))


def check_gpu_request(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    """What the GPU job asks Slurm for, never a node pinned from a snapshot. The sinfo
    snapshot and `sbatch --test-only` are printed only (CLUSTER-QUEUE-DIAG 11-14: free
    cards did not mean "can start"; --test-only ignores higher-priority jobs and partition
    locks); the launcher bounds the real wait (gpu_pending_max_seconds)."""
    ask = (f"partition {plan['gpu_partition']}, gpu:{plan['gpu_gres']}:"
           f"{plan['placement']['cards']}"
           + (f", constraint {plan['gpu_constraint']}" if plan["gpu_constraint"] else "")
           + (f", nodelist {plan['gpu_nodelist']} (site pin)" if plan["gpu_nodelist"] else ""))
    free, start = plan["gpu_nodes_free_now"], facts.get("gpu_test_start_seconds")
    return Result("gpu_request", True, "; ".join([
        ask, f"sinfo now: room on {', '.join(free)}" if free else
        "sinfo now: no node with room (the job will pend)",
        "sbatch --test-only: no estimate" if start is None else
        f"sbatch --test-only: start in {start:.0f} s (informational)",
        f"pending bound {plan['gpu_pending_max_seconds']} s"]), warn=not free)


def check_machine_types(plan: dict[str, Any]) -> Result:
    k = plan["knobs"]
    slow = set(k["env_slow_types"])
    problems = []
    if slow & set(k["env_machine_types"]):
        held = sorted(slow & set(k["env_machine_types"]))
        problems.append(f"env_machine_types holds slow types {held}"
                        " (opt in with env_slow_type_nodes instead)")
    if k["env_slow_type_nodes"] and "c8220" not in slow:
        problems.append("env_slow_type_nodes needs c8220 listed in env_slow_types")
    prep_slow = sorted({g["type"] for g in k["prep_groups"]} & slow)
    if prep_slow:
        problems.append(f"prep groups use slow types {prep_slow}")
    detail = (f"env {k['env_machine_types']}, c8220 opt-in nodes {k['env_slow_type_nodes']} "
              "(0.5 node each), c6220 never")
    return Result("machine_types", not problems, "; ".join(problems + [detail]))


def check_prep_scratch(plan: dict[str, Any]) -> Result:
    k, site = plan["knobs"], plan["site"]
    if not k["remote_prepare"]:
        return Result("prep_scratch", True, "remote prepare off")
    problems = []
    for group in k["prep_groups"]:
        path = site["prep_scratch"].get(group["type"])
        if not path:
            problems.append(f"no site prep_scratch for host type {group['type']}")
        elif _under(path, site["shared_fs_prefixes"]):
            problems.append(f"{group['type']} scratch {path} is on a shared disk")
    need = 2 * k["prep_host_inflight_gb"]
    return Result("prep_scratch", not problems, "; ".join(problems + [
        f"each host must show >= {need:.0f} GB free at start (2x in-flight; checked on the host)"]))


def check_prep_schedulable(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    k = plan["knobs"]
    if not k["remote_prepare"]:
        return Result("prep_schedulable", True, "remote prepare off")
    groups = [g for g in k["prep_groups"] if g["nodes"]]
    hosts = sum(g["nodes"] for g in groups)
    if hosts < 1:
        return Result("prep_schedulable", False, "remote prepare on but no prep hosts")
    return Result("prep_schedulable", True,
                  f"{hosts} prep hosts in {len(groups)} plain jobs (no heterogeneous job), "
                  "each starting after the GPU job started; the learner waits "
                  f"{plan['prep_wait_seconds']} s for all of them (not all up by then: the "
                  "learner exits, the run ends, nothing is resubmitted)")


def check_switches(plan: dict[str, Any]) -> Result:
    k = plan["knobs"]
    allowed = set(k["allow_switch_off"])
    off = [n for n in run_profile.REQUIRED_ON if not k[n] and n not in allowed]
    wrong = [f"{n}={k[n]}" for n, v in run_profile.REQUIRED_VALUES.items()
             if k[n] != v and n not in allowed and k["remote_prepare"]]
    eff = {n: k[n] for n in (*run_profile.REQUIRED_ON, *run_profile.REQUIRED_VALUES,
                             "prepared_kernels", "prepared_bucket_rows", "shards",
                             "service_compile")}
    return Result("speed_switches", not off and not wrong,
                  ("off: " + ", ".join(off + wrong) + "; " if off or wrong else "")
                  + "effective " + json.dumps(eff, sort_keys=True))


def check_env_package(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    k = plan["knobs"]
    problems = []
    build, rev = k["env_pkg_build"], facts.get("env_pkg_rev")
    src = facts.get("src_rev") or ""
    if not build:
        problems.append("env_pkg_build is empty")
    if rev is None:
        problems.append("env package has no SOURCE_REVISION file")
    elif not build or not rev.split()[0].startswith(build):
        problems.append(f"env package SOURCE_REVISION {rev.split()[0][:12]} is not the "
                        f"env_pkg_build {build}")
    head = (src.split() or [""])[0]
    if build and not head.startswith(build):
        problems.append(f"source {head[:12] or '-'} is not the env build {build} (build the "
                        "env package from the run's source)")
    cand = facts.get("env_candidate_sha256")
    prefix = k["env_candidate_artifact_sha256"]
    if not HEX12.match(prefix):
        problems.append(f"env_candidate_artifact_sha256 {prefix!r} is not a sha256 prefix of "
                        ">= 12 lowercase hex characters")
    elif not cand or not cand.startswith(prefix):
        problems.append(f"candidate bin Sts2Headless.dll {str(cand)[:16]} does not start with "
                        f"{k['env_candidate_artifact_sha256']} (PKG and CAND change together)")
    if not k["env_memo"]:
        problems.append("memo is off (env_memo empty; cost +1.65-2.2 core-ms/decision)")
    return Result("env_package", not problems, "; ".join(problems) or
                  f"pkg {build} = src '{src}', candidate {str(cand)[:16]}, memo on")


def check_shards(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    k = plan["knobs"]
    shards, services = k["shards"], k["services"]
    aggregators = plan["env_nodes_max"] * k["env_aggregators"]
    problems = []
    need = services // math.gcd(k["env_aggregators"], services)
    if plan["env_nodes_max"] < need:
        problems.append(f"{plan['env_nodes_max']} env nodes < {need}, the fewest whose "
                        f"aggregators reach all {services} services (env core budget too "
                        "small: core_cap, env_core_budget or --env-cores)")
    if aggregators % services:
        problems.append(f"{aggregators} aggregators do not spread evenly over {services} services")
    if shards:
        if not facts.get("src_features", {}).get("shards"):
            problems.append("source build does not accept dataplane.shards (#523)")
        if services % shards:
            problems.append(f"{services} services (hand-off connections) over {shards} shards "
                            "is uneven")
        if k["remote_prepare"] and k["remote_prepare_connections"] % shards:
            problems.append(f"{k['remote_prepare_connections']} prepare connections over "
                            f"{shards} shards is uneven")
    return Result("shards", not problems, "; ".join(problems) or
                  f"shards {shards}, services {services}, aggregators {aggregators} "
                  f"({aggregators // services} per service)")


def required_features(plan: dict[str, Any]) -> dict[str, str]:
    """Source features this run's knobs need -> why (PR that brings it)."""
    k = plan["knobs"]
    need = {}
    if k["shards"]:
        need["shards"] = "dataplane.shards (#523)"
    if k["prep_scratch_min_free"] and k["remote_prepare"]:
        need["budget_counts_remote"] = "remote_prepare --scratch-min-free-bytes (#523 a437612d)"
    if (k["shards"] and k["remote_prepare"]
            and max(k["preparation_workers"], k["assembly_workers"]) > 2):
        need["budget_counts_remote"] = ("SGD budget that does not count idle local pools "
                                        "(#523 a437612d); older builds need "
                                        "preparation/assembly_workers <= 2")
    if k["shards_adopt_unsharded_checkpoint"]:
        need["adopt_unsharded"] = "dataplane.shards_adopt_unsharded_checkpoint (#523 a437612d)"
    if (k["sgd_stage_stream_reuse"] or k["sgd_stage_lean_tree"] or k["sgd_switch_interval_seconds"]
            or k["sgd_verify_threads"] != 4
            or (k["sgd_verify_split"] and plan["placement"]["ranks"] > 1)):
        need["stage_options"] = "sgd staging options (#525)"
    if k["levers"] and "residual" in k["prepared_kernels"].split("+"):
        need["residual"] = "prepared_kernels residual lever (#526)"
    if k["sgd_gc_freeze"]:
        need["gc_freeze"] = "sgd_gc_freeze (#525 b40d31db)"
    if k["sgd_step_probe"]:
        need["step_probe"] = "sgd_step_probe (#525)"
    if k["levers"] and k["compile_warmup"]:
        need["compile_warmup"] = "sgd_compile.warmup (WP8)"
    if k["release_trained_segments"]:
        need["retention"] = "dataplane.release_trained_segments (#529)"
    if k["r7_games_per_hour"] > 0:
        need["mu_archive"] = "local_mu_archive / node_cache_weights (#529)"
        need["r7_max_games"] = "R7Selection max_games (#529)"
        need["r7_audit_mu"] = ("episode_open.audit_retain_mu from the fast env (product "
                               "gap: without it R7 games keep originals but no mu)")
    if k["segment_format"] == run_profile.SEGMENT_FORMAT_3:
        need["segment_format_3"] = "segment format 3 (#530 S-1)"
    if k["segment_format"] == run_profile.SEGMENT_FORMAT_4:
        need["segment_format_4"] = "segment format 4 + per-combat HP score (W2)"
    if any(int(level) > 0 and w > 0 for level, w in k["ascension_mix"].items()):
        need["ascension"] = "Managed sessions at ascension > 0 (#530 S-1)"
    if k["eval_suite"] or k["eval_a10_enabled"]:
        need["eval_suite"] = "fast_eval.py --suite (#528)"
    return need


def check_features(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    have = facts.get("src_features") or {}
    need = required_features(plan)
    missing = [f"{name}: {why}" for name, why in need.items() if not have.get(name)]
    # A warning only (owner 11-14): the product itself refuses a missing feature loudly.
    return Result("source_features", True,
                  ("source may lack " + "; ".join(missing)) if missing else
                  "source has " + (", ".join(sorted(need)) or "(nothing extra needed)"),
                  warn=bool(missing))


def check_producer(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    mode = plan["knobs"]["producer_identity"]
    if mode == "checkout":
        co = facts.get("src_checkout") or {}
        if not co.get("git"):
            return Result("producer_identity", False, "producer_identity=checkout needs "
                          "src_dir to be a git checkout (source_identity reads HEAD)")
        if co.get("dirty"):
            return Result("producer_identity", False, f"src_dir checkout {co['head'][:12]} "
                          "is dirty (source_identity requires a clean checkout)")
        return Result("producer_identity", True, f"real source identity {co['head'][:12]}")
    ok = plan["smoke"] or plan.get("probe_only", False)
    return Result("producer_identity", ok,
                  "placeholder producer: CPs void (" +
                  ("smoke" if plan["smoke"] else "--probe-only") + ")" if ok else
                  "placeholder producer identity outside --smoke: its CPs would look valid "
                  "but carry a fake producer; use producer_identity=checkout, or pass "
                  "--probe-only for a measurement run whose CPs are void")


def check_frozen_recipe(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    """A9: the learner config this run will write equals the frozen long-run parameters,
    except the operational/speed keys in learner_config.ALLOWED_DRIFT."""
    frozen, base = facts.get("frozen_recipe"), facts.get("gate_learner_base")
    if frozen is None or base is None:
        missing = [name for name, value in (
            ("frozen config " + plan["knobs"]["frozen_learner_config"], frozen),
            ("gate learner-base.json", base)) if value is None]
        return Result("frozen_recipe", False, "missing " + " and ".join(missing))
    effective = learner_config.build(base, plan["knobs"], {
        "run_name": "preflight", "peer_dir": "-", "start_package": "-",
        "wall_seconds": plan["wall_seconds"], "device": plan["placement"]["learner_device"],
        "generation": "-", "warmup_artifact_id": base["segments"].get("warmup_artifact_id"),
        "rendezvous_path": "-", "ranks": plan["placement"]["ranks"],
        "resume_checkpoint": None})
    drift = learner_config.recipe_drift(effective, frozen)
    return Result("frozen_recipe", not drift, ("drift from the frozen parameters: "
                  + "; ".join(drift)) if drift else
                  f"effective learner config = {plan['knobs']['frozen_learner_config']} "
                  f"(allowed: {', '.join(sorted(learner_config.ALLOWED_DRIFT))})")


def check_hp_score(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    """c_hp (the gate's `loss.hp_advantage_coef`, equal to the frozen config's by A9) > 0
    needs segment format 4: the learner refuses every older segment
    (`fast_segment_without_hp_score`), so refuse the run before it starts. Its scope
    (`loss.hp_advantage_scope`) comes from the gate the same way."""
    base, fmt = facts.get("gate_learner_base"), plan["knobs"]["segment_format"]
    loss = (base or {}).get("loss") or {}
    coef, scope = loss.get("hp_advantage_coef"), loss.get("hp_advantage_scope")
    if base is None:
        return Result("hp_score", False, "no gate learner-base.json")
    if type(coef) not in (int, float) or scope not in ("combat_ticket", "all_combats"):
        return Result("hp_score", False, "gate learner-base.json lacks loss.hp_advantage_coef "
                      "/ loss.hp_advantage_scope: patch it once (RL_LONG_RUN.zh-CN.md, c_hp)")
    if coef > 0 and fmt != run_profile.SEGMENT_FORMAT_4:
        return Result("hp_score", False, f"c_hp {coef} > 0 needs segment_format = "
                      f"{run_profile.SEGMENT_FORMAT_4!r} (have {fmt!r}: no hp_adv column)")
    return Result("hp_score", True, f"c_hp {coef}, scope {scope}, segment format {fmt}")


def check_compile_warmup(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    k = plan["knobs"]
    if not (k["levers"] and k["compile_warmup"]):
        return Result("compile_warmup", True, "off (graphs compile on first online use)")
    dumps = facts.get("compile_warmup_dumps") or {}
    missing = [Path(p).name for p, ok in dumps.items() if not ok]
    ok = 1 <= len(dumps) <= 8 and not missing
    return Result("compile_warmup", ok, f"{len(dumps)} warm-up dumps" if ok else
                  f"sgd_compile.warmup needs 1-8 existing dumps (site compile_warmup_dumps; "
                  f"{len(dumps)} listed, missing {missing})")


def memory_estimate(plan: dict[str, Any]) -> dict[str, Any]:
    """Resident memory of the GPU job's learner side, counting only what runs.

    Under remote prepare the local preparation/assembly pools do no work, so they are
    not counted (c3s1 was refused by a budget that counted 73 idle workers = 110 GB).
    Bases are sgd_process.DECLARED_BYTES; file-backed bounds count only on tmpfs.
    """
    k, p = plan["knobs"], plan["placement"]
    copies = max(1, k["shards"])
    local = 0 if k["remote_prepare"] else copies * (
        k["preparation_workers"] + k["assembly_workers"] + 1)
    workers = local + 4 * copies
    prepared = k["prepared_gib"] * GIB
    items = {"controller": CONTROLLER_BASE * copies, "workers": workers * WORKER,
             "publisher": PUBLISHER, "sgd": SGD_BASE, "pinned": prepared,
             "followers": (p["ranks"] - 1) * (SGD_BASE + prepared)}
    if plan["site"]["gpu_local_tmp"].startswith("/dev/shm"):
        items["file_backed_on_tmpfs"] = (k["source_gb"] * GIB * 2 + prepared
                                         + k["wal_max_gib"] * GIB)
    limit = plan["job_mem_g"] * GIB
    return {"items": items, "workers": workers, "counted": sum(items.values()),
            "limit": limit, "usable": limit * (1 - HEADROOM)}


def check_memory(plan: dict[str, Any]) -> Result:
    est = memory_estimate(plan)
    problems = []
    if est["counted"] > est["usable"]:
        problems.append(f"learner side {est['counted'] / GIB:.0f} GiB > usable "
                        f"{est['usable'] / GIB:.0f} GiB of the job")
    guard = plan["mem_guard_bytes"]
    if not guard < plan["mem_hard_bytes"] <= est["limit"]:
        problems.append("memory guard must be < hard stop <= job memory")
    return Result("memory", not problems, "; ".join(problems) or
                  f"learner side {est['counted'] / GIB:.0f} of {est['usable'] / GIB:.0f} GiB "
                  f"usable ({est['workers']} running workers; idle local pools not counted); "
                  f"guard {guard / 1e9:.0f} GB, hard {plan['mem_hard_bytes'] / 1e9:.0f} GB, "
                  f"job {est['limit'] / 1e9:.0f} GB")


def check_identity(plan: dict[str, Any]) -> Result:
    k = plan["knobs"]
    ranges = plan["env_node_index_ranges"]
    problems = []
    seen: set[int] = set()
    for name, (start, count) in ranges.items():
        block = set(range(start, start + count))
        if block & seen:
            problems.append(f"env node indices of {name} overlap another array")
        seen |= block
    procs = max(1, int(k["env_node_cores"] * k["env_procs_fraction"]))
    if procs > SEED_STRIDE_PROCS:
        problems.append(f"{procs} drivers per node > seed stride {SEED_STRIDE_PROCS}")
    return Result("cross_node_identity", not problems, "; ".join(problems) or
                  f"node indices {ranges}; ids = job id + node index + driver; "
                  f"{procs} drivers per node")


def check_hot_path(plan: dict[str, Any]) -> Result:
    site = plan["site"]
    tmp = site["gpu_local_tmp"]
    if _under(tmp, site["shared_fs_prefixes"]):
        return Result("hot_path", False, f"gpu_local_tmp {tmp} is on a shared disk "
                      "(learner live root, mu cache and attachments must be node-local)")
    return Result("hot_path", True, f"learner live root, mu cache, attachment copies under {tmp}"
                  " (fs type checked again on the GPU node)")


def check_secrets(facts: dict[str, Any]) -> Result:
    problems = []
    for path, mode in (facts.get("secret_files") or {}).items():
        if mode is None:
            problems.append(f"{Path(path).name} missing")
        elif mode & 0o077:
            problems.append(f"{Path(path).name} mode {oct(mode)} (must be 0600)")
    return Result("secret_files", not problems, "; ".join(problems) or
                  "auth files present, 0600 (contents never read here)")


def check_time_limits(plan: dict[str, Any]) -> Result:
    """Every job fits its partition's time limit; prep must outlive the GPU job.

    Found by the first dry run: the prep partition caps jobs at 1 h, so a 50 min wall
    plus start margins was refused by Slurm itself (and a long run cannot use it)."""
    limits = plan["partition_max_minutes"]
    k = plan["knobs"]
    problems, shown = [], []
    for role in ("gpu", "env", "prep"):
        if role == "prep" and not plan["prep_hosts"]:
            continue
        need, cap = plan[f"{role}_minutes"], limits.get(role)
        shown.append(f"{role} {need} min (limit {cap or 'none'})")
        if cap is None:
            problems.append(f"{role} partition has no entry in partition_max_minutes "
                            "(unknown limit; 0 = no limit)")
        elif cap and need > cap:
            problems.append(f"{role} job needs {need} min > partition limit {cap} min")
    # Elastic join: an env node may start any time before the drain minus the minimum
    # join time; that window must exist, and the env job must outlive the GPU wall.
    window = plan["wall_seconds"] - k["env_drain_seconds"] - k["env_min_join_seconds"]
    if not plan["smoke"] and window <= 0:
        problems.append(f"no env join window: wall {plan['wall_seconds']} s - drain "
                        f"{k['env_drain_seconds']} s - min join {k['env_min_join_seconds']} s")
    if plan["env_minutes"] * 60 < plan["wall_seconds"] + plan["env_wait_seconds"]:
        problems.append("env job shorter than the GPU wall plus the env wait")
    shown.append(f"env nodes may join for {max(window, 0) // 60} min")
    if problems:
        problems.append("shorten wall_seconds (--set) or use a partition without that limit")
    return Result("time_limits", not problems, "; ".join(problems or shown))


def check_cores(plan: dict[str, Any]) -> Result:
    total = plan["placement"]["gpu_cores"] + plan["env_cores_max"]
    cap = plan["core_cap"]
    return Result("core_cap", total <= cap, f"GPU {plan['placement']['gpu_cores']} + env "
                  f"{plan['env_cores_max']} = {total} cores (cap {cap})")


def check_resume(plan: dict[str, Any]) -> Result:
    """A resume or chain generation: a real producer and enough window left."""
    k, chain, cp = plan["knobs"], plan.get("chain"), plan.get("resume_checkpoint")
    bad = []
    if chain is None:
        return Result("resume", not bad, "; ".join(bad) or
                      (f"start from CP {cp}" if cp else "fresh start from the start package"))
    if plan["checkpoints_void"]:
        bad.append("a chain needs real CPs (producer_identity = checkout)")
    if plan["wall_seconds"] < k["min_generation_seconds"]:
        bad.append(f"window left {plan['wall_seconds']} s < min_generation_seconds "
                   f"{k['min_generation_seconds']}")
    return Result("resume", not bad, "; ".join(bad) or
                  f"chain {chain['id']} generation {chain['generation']}, "
                  f"{plan['wall_seconds']} s of the window left, from "
                  f"{cp or 'the start package'}")


def check_queue(facts: dict[str, Any]) -> Result:
    """Printed, never enforced (CLUSTER-QUEUE-DIAG 11-14): this user's fair-share factor and,
    in the GPU partition and the prep partition, how many pending jobs rank above a job
    submitted now. Backfill looks at only the first bf_max_job_test of them per cycle (500
    there), so a long list means a long wait. The prep jobs queue only once the GPU job
    started and must be up within remote_prepare_wait_seconds, or the learner gives up."""
    q = facts.get("queue") or {}

    def shown(value: Any) -> str:
        return "unknown" if value is None else str(value)

    parts = q.get("partitions") or {}
    ranks = "; ".join(f"{role} partition {shown(p.get('name'))}: {shown(p.get('above'))} of "
                      f"{shown(p.get('pending'))} pending jobs rank above it"
                      for role, p in parts.items()) or "pending jobs unknown"
    return Result("queue", True, f"fair-share {shown(q.get('fairshare'))}; a job submitted "
                  f"now has priority ~{shown(q.get('priority_estimate'))} (partition + "
                  f"fair-share weights); {ranks}",
                  warn=not parts or any(p.get("above") != 0 for p in parts.values()))


def checks(plan: dict[str, Any], facts: dict[str, Any]) -> list[Result]:
    return [check_placement(plan), check_gpu_request(plan, facts), check_machine_types(plan),
            check_prep_scratch(plan), check_prep_schedulable(plan, facts),
            check_switches(plan), check_features(plan, facts), check_producer(plan, facts),
            check_frozen_recipe(plan, facts), check_hp_score(plan, facts),
            check_compile_warmup(plan, facts),
            check_env_package(plan, facts), check_shards(plan, facts),
            check_memory(plan), check_identity(plan), check_hot_path(plan),
            check_secrets(facts), check_time_limits(plan), check_cores(plan),
            check_resume(plan), check_queue(facts)]


def report(results: list[Result]) -> str:
    lines = [f"{'FAIL' if not r.ok else 'WARN' if r.warn else 'PASS'} {r.name}: {r.detail}"
             for r in results]
    failed = [r.name for r in results if not r.ok]
    lines.append("preflight: " + (f"REFUSED ({', '.join(failed)})" if failed else "ok"))
    return "\n".join(lines)


# ---------------- job side ----------------

def scratch_ok(free_bytes: int, inflight_gb: float) -> tuple[bool, str]:
    need = 2 * inflight_gb * 1e9
    return free_bytes >= need, f"free {free_bytes / 1e9:.1f} GB, need {need / 1e9:.1f} GB"


def local_fs(fstype: str) -> bool:
    return fstype.lower() in LOCAL_FS


def _fstype(path: str) -> str:
    out = subprocess.run(["stat", "-f", "-c", "%T", path], capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def _env_sha() -> str:
    import bz2  # noqa: F401 - F6: inductor needs libbz2

    import lz4.frame  # noqa: F401 - remote prepare codec

    from stpd.p1.inference_service import python_env_sha256

    return python_env_sha256()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def job_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("job-gpu", "prep-host", "env-node"))
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--path", default="")
    parser.add_argument("--index", default="0")
    args = parser.parse_args(argv)
    profile = json.loads((args.run / "effective-profile.json").read_text())
    knobs = {name: knob["value"] for name, knob in profile["knobs"].items()}
    try:
        sha = _env_sha()
    except Exception as error:  # noqa: BLE001 - reported as the refusal
        detail = f"dependency import failed: {error!r}"
        if args.role == "prep-host":  # the learner stops waiting at once
            _write(args.run / "prep-refused" / args.index, f"{os.uname().nodename} {detail}\n")
        print(f"preflight {args.role}: {detail}", flush=True)
        return 9
    if args.role == "job-gpu":
        fstype = _fstype(args.path)
        if not local_fs(fstype):
            print(f"preflight job-gpu: local tmp {args.path} is {fstype}, not one of "
                  f"{LOCAL_FS}", flush=True)
            return 9
        _write(args.run / "env-sha.txt", sha + "\n")
        print(f"preflight job-gpu ok: bz2+lz4, env_sha {sha}, local tmp {fstype}", flush=True)
        return 0
    if args.role == "prep-host":
        Path(args.path).mkdir(parents=True, exist_ok=True)
        fstype = _fstype(args.path)
        ok, detail = scratch_ok(shutil.disk_usage(args.path).free, knobs["prep_host_inflight_gb"])
        if not local_fs(fstype):
            ok, detail = False, f"scratch is {fstype}, not one of {LOCAL_FS}"
        _write(args.run / "prep-env" / args.index, sha + "\n")
        if not ok:
            _write(args.run / "prep-refused" / args.index, f"{os.uname().nodename} {detail}\n")
            print(f"preflight prep-host refused: {detail}", flush=True)
            return 9
        print(f"preflight prep-host ok: {detail}, env_sha {sha}", flush=True)
        return 0
    gpu = (args.run / "env-sha.txt").read_text().strip()
    if sha != gpu:
        print(f"preflight env-node: env_sha {sha} != GPU side {gpu}", flush=True)
        return 9
    print(f"preflight env-node ok: env_sha {sha}", flush=True)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(job_main(sys.argv[1:]))
