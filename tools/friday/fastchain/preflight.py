#!/usr/bin/env python3
"""Start checks for the fast chain: any failure refuses the run, with a clear message.

Two halves, both decided by the pure functions below:
- login side (`checks(plan, facts)`), before anything is submitted: layout placement,
  the GPU request (sinfo snapshot and --test-only printed only), machine types, prep
  scratch, prep jobs, speed switches, source features the knobs need, producer identity,
  declared recipe changes against the CP they name (RL5-METHODS C),
  env package identity and memo, env nodes and shard balance, memory budget (the
  learner's own), AF_UNIX socket path lengths, per-batch
  prepared cap, service CPUs, cross-node identity, hot path, secret files, time limits,
  core cap, the evaluation jobs' node class and degraded threshold and the queue (both
  printed only);
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
import layout  # noqa: E402
import learner_config  # noqa: E402
import run_profile  # noqa: E402

GIB = 1024**3
# Node-local file systems (`stat -f -c %T` names). Anything else, including "unknown"
# and the "UNKNOWN (0x...)" that BeeGFS/Ceph mounts print, is refused.
LOCAL_FS = ("tmpfs", "xfs", "ext2/ext3", "ext4", "btrfs", "overlayfs", "overlay", "zfs")
HEX12 = re.compile(r"^[0-9a-f]{12,64}$")
RANDOM_START = "stpd/rl-random-start-1"  # = stpd.rl.random_start.SCHEMA (pinned by a test)
# = sgd_process.DECLARED_BYTES, the learner's own memory budget bases (pinned by a test;
# the gate's dataplane.json `memory_budget` overrides them as in the learner).
DECLARED_BYTES = {"controller_base": 6 * GIB, "worker_process": 3 * GIB // 2,
                  "publisher_process": 1 * GIB, "sgd_base_cuda": 5 * GIB // 2,
                  "sgd_base_cpu": 2 * GIB}
HEADROOM = 0.20  # sgd_process memory_headroom_fraction default
# Linux sockaddr_un.sun_path is 108 bytes including the trailing NUL.
UNIX_PATH_MAX = 107
SLURM_JOB_ID_WIDTH = 10  # widest Slurm job id (32 bit): the env side's sockets carry one
BUDGET_CAP_SECONDS = 72000  # the product operating-budget cap (SegmentLearner)
PREPARED_BATCH_HEADROOM = 1.15  # per-batch cap >= 1.15 x the largest measured batch


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


def check_gpu_node_cores(plan: dict[str, Any]) -> Result:
    """#607: the GPU job's core request (service_cores + rank_cores x ranks) must fit one
    node of the card type (site gpu_types.<type>.node_cores). Slurm would leave a larger
    request pending for ever; a type without node_cores cannot be checked and is refused."""
    p, cores = plan["placement"], plan.get("gpu_node_cores")
    k = plan["knobs"]
    ask = (f"GPU job asks {p['gpu_cores']} cores (service_cores {k['service_cores']} + "
           f"rank_cores {k['rank_cores']} x {p['ranks']} ranks)")
    if cores is None:
        return Result("gpu_node_cores", False, f"{ask}; site gpu_types.{p['card_type']} has "
                      "no node_cores, so the request cannot be checked: add `node_cores = N` (the "
                      f"cores of one node of this type) to [site.gpu_types.\"{p['card_type']}\"]")
    if p["gpu_cores"] > cores:
        return Result("gpu_node_cores", False, f"{ask} > {cores} cores of one "
                      f"{p['card_type']} node: the job can never start (lower service_cores "
                      "or rank_cores)")
    return Result("gpu_node_cores", True, f"{ask} <= {cores} cores of one {p['card_type']} node")


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


def required_features(plan: dict[str, Any],
                      frozen: dict[str, Any] | None = None) -> dict[str, str]:
    """Source features this run's knobs need -> why (PR that brings it). `frozen` = the
    run's frozen learner config (`facts["frozen_recipe"]`): recipe switches that live only
    there (the noncombat barrier, observation cells) count too."""
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
    if k["sgd_gc_refreeze_objects"] != 200_000:
        need["gc_refreeze"] = "sgd_gc_refreeze_objects (RL5-METHODS F)"
    if k["sgd_step_probe"]:
        need["step_probe"] = "sgd_step_probe (#525)"
    if k["levers"] and k["compile_warmup"]:
        need["compile_warmup"] = "sgd_compile.warmup (WP8)"
    if k["release_trained_segments"]:
        need["retention"] = "dataplane.release_trained_segments (#529)"
    if k["learner_local_keep_models"] or k["learner_local_keep_checkpoints"]:
        need["local_retention"] = "dataplane.local_keep_models / local_keep_checkpoints (#558)"
    if k["r7_games_per_hour"] > 0:
        need["mu_archive"] = "local_mu_archive / node_cache_weights (#529)"
        need["r7_max_games"] = "R7Selection max_games (#529)"
        need["r7_audit_mu"] = ("episode_open.audit_retain_mu from the fast env (product "
                               "gap: without it R7 games keep originals but no mu)")
    if k["segment_format"] == run_profile.SEGMENT_FORMAT_3:
        need["segment_format_3"] = "segment format 3 (#530 S-1)"
    if k["segment_format"] == run_profile.SEGMENT_FORMAT_4:
        need["segment_format_4"] = "segment format 4 + per-combat HP score (W2)"
    if any(w != 1 for w in k["hp_combat_weights"].values()):
        need["hp_combat_weights"] = "HP-change weight per combat type (RL5-METHODS A2)"
    if k["hp_potion_mode"] != "points":
        need["hp_potion_mode"] = "potions judged by the value head in the HP score (ticket H)"
    if k["hp_potion_mode"] == "value_head_neutral":
        need["hp_potion_neutral"] = "potion rows take their combat's mean HP-change part (#608)"
    if k["hp_potion_mode"] == "plain":
        need["hp_potion_plain"] = "one HP formula for every combat row, no death / max-HP terms (recipe A)"
    if (k["save_starts"] or {}).get("act_floor"):
        need["save_act_floor"] = "save starts: each act's floor share of the act draw (recipe A)"
    if k["kl_stop_scope"] != "all":
        need["kl_stop_scope"] = "KL stop over control-arm rows (ticket K)"
    if k["lr_warmup_steps"]:
        need["lr_warmup_steps"] = "lr warm-up (ticket K)"
    if k["value_config_input"]:
        need["value_config_input"] = "value heads read the sampling config (owner 11-19)"
    if k["arm_policy_update"] != "normal":
        need["arm_policy_update"] = "each arm's rows update its own cell policy (owner 11-19)"
    if k["score_head_fp32"]:
        need["score_head_fp32"] = "the action score head in FP32 under BF16 (#632)"
    ladder = k["exploration_ladder"].values()
    if any(spec[name]["temperature"] == 0 for spec in ladder for name in ("combat", "noncombat")):
        need["greedy_class"] = "greedy classes, temperature 0 (user 2026-11-24)"
    recipe_loss = ((frozen or {}).get("loss") or {})
    recipe_segments = ((frozen or {}).get("segments") or {})
    if (any(spec.get("learn_policy") is False for spec in ladder)
            or recipe_segments.get("observe_arms")):
        need["observe_arms"] = "observation cells, learn_policy = false (user 2026-11-24)"
    if recipe_loss.get("noncombat_log_barrier_coef"):
        need["noncombat_barrier"] = ("noncombat log barrier, "
                                     "loss.noncombat_log_barrier_coef (user 2026-11-24)")
    if any(spec.get("new_runs_only") for spec in ladder):
        need["new_runs_only"] = "arms that never open from a save (user 2026-11-24)"
    if k.get("declared_changes"):
        need["declared_changes"] = "declared recipe changes at a continuation (RL5-METHODS C)"
    if any(int(level) > 0 and w > 0 for level, w in k["ascension_mix"].items()):
        need["ascension"] = "Managed sessions at ascension > 0 (#530 S-1)"
    if k["eval_suite"] or k["eval_a10_enabled"]:
        need["eval_suite"] = "fast_eval.py --suite (#528)"
    return need


def check_features(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    have = facts.get("src_features") or {}
    need = required_features(plan, facts.get("frozen_recipe"))
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
    # A9 walks the frozen keys only: a gate loss key the source lacks (a misspelling, or a
    # gate for newer code) would pass here and stop the learner (LossConfig) after the queue.
    unknown = sorted(set(base.get("loss") or {}) - set(frozen.get("loss") or {}))
    if unknown:
        return Result("frozen_recipe", False, "gate learner-base.json has loss keys the "
                      f"frozen config lacks: {', '.join(unknown)} (misspelt? a source older "
                      "than the gate needs its own gate)")
    try:
        effective, filled = learner_config.learner_base(base, frozen, plan["knobs"], {
            "run_name": "preflight", "peer_dir": "-", "start_package": "-",
            "wall_seconds": plan["wall_seconds"], "device": plan["placement"]["learner_device"],
            "generation": "-", "warmup_artifact_id": base["segments"].get("warmup_artifact_id"),
            "rendezvous_path": "-", "ranks": plan["placement"]["ranks"],
            "resume_checkpoint": None})
    except ValueError as error:  # e.g. own with the gate's explore_ratio_floor 0.3 kept
        floor = (base.get("loss") or {}).get("explore_ratio_floor")
        gate_floor = (plan["knobs"].get("arm_policy_update") == "own" and bool(floor)
                      and "loss.explore_ratio_floor" not in (
                          (plan["knobs"].get("declared_changes") or {}).get("changes") or {}))
        return Result("frozen_recipe", False, str(error) + (
            " (the gate's loss keys win over the frozen config's: gate learner-base.json "
            f"loss.explore_ratio_floor = {floor!r})" if gate_floor else ""))
    declared = frozenset((plan["knobs"].get("declared_changes") or {}).get("changes") or {})
    drift = learner_config.recipe_drift(effective, frozen, declared=declared)
    if plan["knobs"]["eval_only"]:
        # never trained: the learner recipe does not reach the served actions (the services
        # serve the start package / CP weights, heads checked by warmup_artifact)
        owned = [f"{s}.{k} = {effective[s][k]!r} (gate)" for s, k in (
            ("segments", "kl_stop_scope"), ("segments", "lr_warmup_steps"),
            ("segments", "value_config_input"), ("segments", "observe_arms"),
            ("loss", "arm_policy_update")) if k in (base.get(s) or {})]
        return Result("frozen_recipe", True, "eval_only: the learner never trains; "
                      f"{len(drift) + len(owned)} training-recipe items not applicable"
                      + "".join(f"\n  eval_only: not trained, not applicable: {item}"
                                for item in drift + owned))
    return Result("frozen_recipe", not drift, ("drift from the frozen parameters: "
                  + "; ".join(drift)) if drift else
                  f"effective learner config = {plan['knobs']['frozen_learner_config']} "
                  f"(allowed: {', '.join(sorted(learner_config.ALLOWED_DRIFT))})"
                  + (f"; declared changes (check declared_changes): {', '.join(sorted(declared))}"
                     if declared else "")
                  + (f"; not in the gate, from the frozen config: loss.{', loss.'.join(filled)}"
                     if filled else ""))


def check_declared_changes(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    """RL5-METHODS C (#573): a declared recipe change is checked against the CP it names
    before anything is submitted, with the learner's rules (stpd.rl.recipe_changes): each
    declared key's old value is the CP's, its new value this run's, the two differ, and no
    other declarable key differs. Keys outside DECLARABLE are compared by the learner at
    start (as without a declaration). Nothing declared: nothing read, nothing refused."""
    declared = plan["knobs"].get("declared_changes") or {}
    cp, chain = plan.get("resume_checkpoint"), plan.get("chain")
    if not declared:
        return Result("declared_changes", True, "none: the recipe must equal the CP's"
                      if cp else "none")
    named = declared["checkpoint"]
    if not cp:
        return Result("declared_changes", False, f"declared changes from CP {named} need "
                      "--resume-checkpoint (a fresh start has no recipe to change)")
    if cp != named:
        if chain is not None and chain.get("generation", 0) > 0:
            return Result("declared_changes", True, f"declared for CP {named}, this "
                          f"generation resumes {cp}: inactive, the recipe is locked to the "
                          "CP's (the declared new values)")
        return Result("declared_changes", False, f"declared for CP {named} but this run "
                      f"starts from {cp}")
    old = facts.get("resume_checkpoint_config")
    if old is None:
        return Result("declared_changes", False, f"cannot read CP {cp}'s config from the "
                      f"site peer: {facts.get('resume_checkpoint_config_error', 'not read')}")
    base, frozen = facts.get("gate_learner_base"), facts.get("frozen_recipe")
    if base is None or frozen is None:
        return Result("declared_changes", False, "no gate learner-base.json or frozen config")
    new, _ = learner_config.learner_base(base, frozen, plan["knobs"], {
        "run_name": "preflight", "peer_dir": "-", "start_package": "-",
        "wall_seconds": plan["wall_seconds"], "device": plan["placement"]["learner_device"],
        "generation": "-", "warmup_artifact_id": None, "rendezvous_path": "-",
        "ranks": plan["placement"]["ranks"], "resume_checkpoint": cp})
    def same(a: Any, b: Any) -> bool:  # as the learner (recipe_changes._same): 0.0 != False
        return a == b and (type(a) is bool) == (type(b) is bool)

    bad, shown = [], []
    for key in learner_config.DECLARABLE:
        have_old = learner_config.config_value(old, key)
        have_new = learner_config.config_value(new, key)
        change = declared["changes"].get(key)
        if change is None:
            if not same(have_old, have_new):
                bad.append(f"{key} changes {have_old!r} -> {have_new!r} undeclared")
            continue
        shown.append(f"{key} {change['old']!r} -> {change['new']!r}")
        if not same(change["old"], have_old):
            bad.append(f"{key}: declared old {change['old']!r}, CP has {have_old!r}")
        if not same(change["new"], have_new):
            bad.append(f"{key}: declared new {change['new']!r}, this run has {have_new!r}")
        if same(have_old, have_new):
            bad.append(f"{key}: {have_old!r} in both the CP and this run")
    return Result("declared_changes", not bad, "; ".join(bad) if bad else
                  f"from CP {cp}: " + ", ".join(shown) + " (model and optimizer state kept; "
                  "the learner checks the full recipe again at start)")


def check_hp_score(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    """c_hp (the gate's `loss.hp_advantage_coef`, equal to the frozen config's by A9) > 0
    needs segment format 4: the learner refuses every older segment
    (`fast_segment_without_hp_score`), so refuse the run before it starts. Its scope
    (`loss.hp_advantage_scope`) comes from the gate the same way; a gate made before them
    gets both from the frozen config (`learner_config.gate_recipe`)."""
    base, fmt = facts.get("gate_learner_base"), plan["knobs"]["segment_format"]
    potion = plan["knobs"]["hp_potion_mode"]
    if base is None:
        return Result("hp_score", False, "no gate learner-base.json")
    frozen = facts.get("frozen_recipe")
    base, filled = learner_config.gate_recipe(base, frozen) if frozen else (base, {})
    # A declared change of c_hp or its scope (RL5-METHODS C) is this run's value.
    base = learner_config.apply_declared(base, plan["knobs"].get("declared_changes") or {})
    loss = base.get("loss") or {}
    coef, scope = loss.get("hp_advantage_coef"), loss.get("hp_advantage_scope")
    if type(coef) not in (int, float) or scope not in ("combat_ticket", "all_combats"):
        return Result("hp_score", False, "neither the gate learner-base.json nor the frozen "
                      "config has a valid loss.hp_advantage_coef / loss.hp_advantage_scope "
                      "(a source before #538?)")
    # Ticket H: the potion mode changes A_hp only; off the default it needs the HP score.
    if potion != "points" and not coef > 0:
        return Result("hp_score", False, f"hp_potion_mode {potion!r} needs c_hp > 0 (have "
                      f"{coef}: no row receives A_hp)")
    if potion != "points" and fmt != run_profile.SEGMENT_FORMAT_4:
        return Result("hp_score", False, f"hp_potion_mode {potion!r} needs segment_format = "
                      f"{run_profile.SEGMENT_FORMAT_4!r} (have {fmt!r}: no HP score)")
    if coef > 0 and fmt != run_profile.SEGMENT_FORMAT_4:
        return Result("hp_score", False, f"c_hp {coef} > 0 needs segment_format = "
                      f"{run_profile.SEGMENT_FORMAT_4!r} (have {fmt!r}: no hp_adv column)")
    if coef > 0 and scope == "combat_ticket":
        # fast_source adds c_hp * A_hp only to segments whose ticket.end == "combat". No
        # source sends one: the fast env's episode_open names no ticket (kernel.ts), so the
        # assembler ends every game as {"end": "game"} and the term would add nothing.
        return Result("hp_score", False, f"c_hp {coef} > 0 with scope combat_ticket adds "
                      "nothing: the fast env sends no single-combat tickets, so no row gets "
                      'the HP term (#568); use loss.hp_advantage_scope = "all_combats" or c_hp 0')
    return Result("hp_score", True, f"c_hp {coef}, scope {scope}, segment format {fmt}"
                  + (f", potions {potion}" if potion != "points" else "")
                  + (" (from the frozen config; not in the gate)"
                     if {"hp_advantage_coef", "hp_advantage_scope"} & set(filled) else ""))


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


def check_warmup_artifact(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    """The learner loads the fitted heads (site warmup_file) from peer_dir at start; a new
    empty peer_dir failed V4 smoke-h1a at learner setup (object_not_found).

    Site warmup_file = "" loads none: the RL4 heads start from their constructor init. A
    random-init start package (python -m stpd.rl.random_start) needs that, since fitted
    heads are bound to the package they were fitted on (the learner refuses them). A
    trained start with no fitted heads changes the recipe (early values and advantages
    come from the constructor init, REVIEW-550 O1): refused unless the site says so with
    `trained_start_without_warmup = true`. A fresh start compares the package the heads
    were fitted on with the start package, as the learner does (`_start_model`). A resume
    restores its heads from the CP and loads neither."""
    warmup = facts.get("warmup_artifact") or {}
    start = facts.get("start_model") or {}
    identity = start.get("training_identity") or {}
    fresh = not plan.get("resume_checkpoint")
    opt_out = plan["site"].get("trained_start_without_warmup", False)
    random_init = (f"random-init start {str(start.get('package_sha256'))[:12]} seed "
                   f"{identity.get('seed')}" if identity.get("schema") == RANDOM_START else None)
    if warmup.get("none"):
        if fresh and start and not random_init and not opt_out:
            return Result("warmup_artifact", False, 'site warmup_file = "" with trained start '
                          f"{str(start.get('package_sha256'))[:12]}: its RL4 heads would start "
                          "from their constructor init; set warmup_file to the heads fitted on "
                          "it, or set site trained_start_without_warmup = true to mean it")
        return Result("warmup_artifact", True, 'none (site warmup_file = ""): RL4 heads start '
                      "from their constructor init" + (f"; {random_init}" if random_init else
                      "; site trained_start_without_warmup = true" if opt_out else ""))
    if opt_out and fresh:
        return Result("warmup_artifact", False, "site trained_start_without_warmup = true "
                      f"but warmup_file names {str(warmup.get('id'))[:12]}: set one of them")
    if random_init:
        return Result("warmup_artifact", False, f"{random_init} has no fitted heads: set site "
                      'warmup_file = "" (warmup artifact '
                      f"{str(warmup.get('id'))[:12]} was fitted on another package)")
    if not warmup.get("present"):
        return Result("warmup_artifact", False, f"warmup artifact {warmup.get('id')} has no "
                      "manifest in site peer_dir (copy its manifest, payload index and chunks "
                      "there)")
    fitted, error = warmup.get("package_sha256"), warmup.get("package_error")
    if fresh and start and error:
        return Result("warmup_artifact", False, f"warmup artifact {str(warmup.get('id'))[:12]}: "
                      f"cannot read the package its heads were fitted on ({error})")
    if fresh and start and fitted != start.get("package_sha256"):
        return Result("warmup_artifact", False, f"warmup artifact {str(warmup.get('id'))[:12]} "
                      f"was fitted on package {fitted}, the start package is "
                      f"{start.get('package_sha256')} (the learner refuses it: "
                      "fitted_heads_start_policy_changed_use_explicit_CP_conversion)")
    return Result("warmup_artifact", True, f"{str(warmup.get('id'))[:12]} in peer_dir"
                  + (f", fitted on the start package {str(fitted)[:12]}" if fresh and start
                     else ""))


def check_prepared_batch_cap(plan: dict[str, Any]) -> Result:
    """Per-batch prepared-output cap = prepared_bytes // (prepared_batches + 1), the same
    in the learner and every shard; one bigger batch kills its shard (V4 smoke-h1c: cap
    1.006 GB, batches up to 0.956 GB, died at 32 min). Refused below PREPARED_BATCH_HEADROOM
    x the profile's measured largest batch."""
    k = plan["knobs"]
    cap = int(k["prepared_gib"] * GIB) // (k["prepared_batches"] + 1)
    need = PREPARED_BATCH_HEADROOM * k["prepared_batch_expected_max_gb"] * 1e9
    ok = cap >= need
    return Result("prepared_batch_cap", ok,
                  f"per-batch cap {cap / 1e9:.3f} GB = prepared_gib {k['prepared_gib']} / "
                  f"(prepared_batches {k['prepared_batches']} + 1) {'>=' if ok else '<'} "
                  f"{PREPARED_BATCH_HEADROOM} x prepared_batch_expected_max_gb "
                  f"{k['prepared_batch_expected_max_gb']} = {need / 1e9:.3f} GB")


def check_service_cpus(plan: dict[str, Any]) -> Result:
    """Service prepare CPUs as roles.service assigns them (layout.service_cpus). Too few
    service cores make services share CPUs: a warning, they still run. A service short of
    `workers` CPUs is refused: FastInferenceService raises (V4 smoke-h1b)."""
    k = plan["knobs"]
    cores, services, workers = k["service_cores"], k["services"], k["service_prepare_workers"]
    slices = [set(layout.service_cpus(list(range(cores)), workers, i)) for i in range(services)]
    head = f"{services} services x {workers} prepare workers on {cores} service cores"
    if any(len(cpus) != workers for cpus in slices):
        return Result("service_cpus", False, f"{head}: a service gets fewer than {workers} CPUs")
    shared = sum(any(mine & other for j, other in enumerate(slices) if j != i)
                 for i, mine in enumerate(slices))
    if shared:
        return Result("service_cpus", True, f"{head} ({services * workers} for distinct CPUs): "
                      f"{shared} of {services} services share CPUs with another", warn=True)
    return Result("service_cpus", True, f"{head}, distinct CPUs")


def memory_estimate(plan: dict[str, Any], gate_dataplane: dict[str, Any],
                    model_bytes: int) -> dict[str, Any]:
    """The learner's own SGD memory budget, item for item: sgd_process.memory_budget with
    the inputs SegmentLearner._start_sgd_process gives it for the dataplane roles.py
    writes (the gate's dataplane.json + the profile). A test pins it equal to the
    learner's computation, so a job the learner would refuse
    (`sgd_process_memory_budget_exceeded`) is refused here before anything is queued.

    As in the learner: unsharded, the local pools count even under remote prepare; with
    shards, one idle 1+1+1 preparer plus each shard process and its remote clients (or
    local pools). The SGD offline dump counts when the gate's dataplane.json turns it on.
    Exception (as on the base): file-backed bounds (source spool, prepare arenas,
    prepared outputs, WAL) are counted here only when gpu_local_tmp starts with
    /dev/shm; the learner counts them on any memory filesystem (MEMORY_FILESYSTEMS:
    tmpfs, ramfs, devtmpfs, unknown), so a memory-backed gpu_local_tmp outside /dev/shm
    is undercounted here.
    """
    k, p = plan["knobs"], plan["placement"]
    declared = {**DECLARED_BYTES, **gate_dataplane.get("memory_budget", {})}
    count, batches = k["shards"], k["prepared_batches"]
    label = gate_dataplane.get("label_workers", 1)
    total = int(k["prepared_gib"] * GIB)  # settings.prepared_bytes (learner_config)
    source = k["source_gb"] * GIB  # source_bytes = prepare_source_bytes (roles.py)
    if count:  # dataplane_shards.shard_budget over shard_dataplane / shard_settings
        def share(value: int) -> int:
            return -(-value // count) if count > 1 and value > 0 else value
        clients = k["remote_prepare_client_processes"]
        pools = ((share(clients) if clients else 0) if k["remote_prepare"] else
                 share(k["preparation_workers"]) + share(k["assembly_workers"]) + share(label))
        workers = 3 + 4 + count * (1 + pools)
        prepare_source = count * (source // count if count > 1 else source)
        prepared = count * (total if count == 1 else
                            total // (batches + 1) * (max(1, batches // count) + 1))
    else:
        workers = k["preparation_workers"] + k["assembly_workers"] + label + 4
        prepare_source, prepared = source, total
    cuda = p["learner_device"].startswith("cuda")
    base = declared["sgd_base_cuda" if cuda else "sgd_base_cpu"]
    items = {"controller_base": declared["controller_base"],
             "checkpoint_assembly": 8 * model_bytes,
             "worker_processes": workers * declared["worker_process"],
             "publisher_process": declared["publisher_process"] + 3 * model_bytes,
             "sgd_base": base, "pinned_slots" if cuda else "owned_batches": total,
             "state_capture": 8 * model_bytes}
    if p["ranks"] > 1:
        items["data_parallel_ranks"] = (p["ranks"] - 1) * (base + total)
    if gate_dataplane.get("sgd_offline_dump_every") is not None:
        items["offline_dump"] = 8 * model_bytes + prepared // 2
    if plan["site"]["gpu_local_tmp"].startswith("/dev/shm"):
        items.update({"source_spool": source, "prepare_arenas": prepare_source,
                      "prepared_outputs": prepared, "wal": k["wal_max_gib"] * GIB})
    limit = gate_dataplane.get("memory_limit_bytes") or plan["job_mem_g"] * GIB
    headroom = gate_dataplane.get("memory_headroom_fraction", HEADROOM)
    return {"items": items, "workers": workers, "counted": sum(items.values()),
            "limit": limit, "usable": int(limit * (1 - headroom))}


def check_memory(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    """The learner's own budget (memory_estimate), with its file-backed exception."""
    dataplane, model = facts.get("gate_dataplane"), facts.get("model_bytes")
    if dataplane is None:
        return Result("memory", False, "no gate dataplane.json: the learner's memory budget "
                      "(SGD offline dump, memory_budget overrides) cannot be computed")
    est = memory_estimate(plan, dataplane, model or 0)
    shown = ", ".join(f"{name} {size / GIB:.2f}" for name, size in est["items"].items())
    unknown = ("; model bytes unknown (no start package model.tensor-tree): 16-19x model "
               "bytes not counted") if model is None else ""
    problems = []
    if est["counted"] > est["usable"]:
        problems.append(f"learner budget {est['counted'] / GIB:.2f} GiB > usable "
                        f"{est['usable'] / GIB:.2f} GiB = (1 - headroom) x "
                        f"{est['limit'] / GIB:.0f} GiB (the learner refuses: "
                        f"sgd_process_memory_budget_exceeded) [GiB: {shown}]")
    guard = plan["mem_guard_bytes"]
    if not guard < plan["mem_hard_bytes"] <= plan["job_mem_g"] * GIB:
        problems.append("memory guard must be < hard stop <= job memory")
    if problems:
        return Result("memory", False, "; ".join(problems) + unknown)
    return Result("memory", True, f"learner budget {est['counted'] / GIB:.2f} of "
                  f"{est['usable'] / GIB:.2f} GiB usable [GiB: {shown}] ({est['workers']} "
                  f"worker processes); guard {guard / 1e9:.0f} GB, hard "
                  f"{plan['mem_hard_bytes'] / 1e9:.0f} GB, job "
                  f"{plan['job_mem_g'] * GIB / 1e9:.0f} GB" + unknown, warn=bool(unknown))


def unix_socket_paths(plan: dict[str, Any]) -> dict[str, str]:
    """AF_UNIX paths the roles bind, built from site settings: roles.py's hand-off
    listener (learner) and env.sh's admission and aggregator sockets (env_tmp_root/<job
    id>/, the job id at its widest)."""
    env_tmp = os.path.join(plan["site"]["env_tmp_root"], "9" * SLURM_JOB_ID_WIDTH)
    last = plan["knobs"]["env_aggregators"] - 1
    return {"handoff": os.path.join(plan["run"], "handoff.sock"),
            "admission": os.path.join(env_tmp, "admit.sock"),
            "aggregator": os.path.join(env_tmp, f"agg-{last}.sock")}


def check_unix_sockets(plan: dict[str, Any]) -> Result:
    """A longer path kills its role at bind: `OSError: AF_UNIX path too long` (hwcheck
    11-19: the learner's handoff.sock at 111 bytes)."""
    sizes = {name: (path, len(path.encode())) for name, path in unix_socket_paths(plan).items()}
    long = [f"{name} socket {path} is {size} bytes" for name, (path, size) in sizes.items()
            if size > UNIX_PATH_MAX]
    if long:
        return Result("unix_sockets", False, "; ".join(long) + f" > the AF_UNIX limit "
                      f"{UNIX_PATH_MAX} bytes: shorten work_root / env_tmp_root or the run tag")
    return Result("unix_sockets", True, ", ".join(f"{name} {size}" for name, (_, size)
                                                  in sizes.items())
                  + f" bytes <= {UNIX_PATH_MAX}")


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
    # A driver stops on its own warm + steady after its fast env is ready (env_driver.mjs);
    # the GPU side expects it until DRAIN, so it must outlive the wall (#586 review M2).
    life = k["driver_warm_seconds"] + plan["driver_steady_seconds"]
    if life < plan["wall_seconds"]:
        problems.append(f"env drivers end by design after driver_warm_seconds + "
                        f"driver_steady_seconds = {life} s, before the GPU wall "
                        f"{plan['wall_seconds']} s: raise driver_steady_seconds")
    shown.append(f"env nodes may join for {max(window, 0) // 60} min")
    if problems:
        problems.append("shorten wall_seconds (--set) or use a partition without that limit")
    return Result("time_limits", not problems, "; ".join(problems or shown))


def check_training_clock(plan: dict[str, Any]) -> Result:
    """#603. Without a target a chain's training window is its wall window, which is
    also the product operating budget (<= 72000 s). With `train_target_seconds` the budget
    is the target (learner training seconds, carried in the CPs across resumes) and
    `long_wall_seconds` only the chain's wall limit: generation 0 must be able to train
    the whole target in it; a later generation with less wall left than training left is
    a warning (the chain then ends at its wall limit, said in its final cause)."""
    k, chain = plan["knobs"], plan.get("chain")
    target = k["train_target_seconds"]
    if not target:
        if chain is not None and k["long_wall_seconds"] > BUDGET_CAP_SECONDS:
            return Result("training_clock", False, f"long_wall_seconds "
                          f"{k['long_wall_seconds']} s is the chain's operating budget "
                          f"without train_target_seconds: at most {BUDGET_CAP_SECONDS} s")
        return Result("training_clock", True, "off: the chain window is long_wall_seconds "
                      "(wall time, fixed at chain start, queue time included)")
    if chain is None:
        return Result("training_clock", False, "train_target_seconds needs a supervise.py "
                      "chain (a plain run has no operating budget to count it)")
    left, wall_left = chain["train_left_seconds"], plan["chain_wall_left_seconds"]
    shown = (f"target {target} s learner training (counted across resumes), {left} s left; "
             f"chain wall limit long_wall_seconds {k['long_wall_seconds']} s, "
             f"{wall_left} s left; this job: wall {plan['wall_seconds']} s (the training "
             f"left) + end reserve {plan['end_reserve_seconds']} s; no absolute deadline "
             "in the learner or its CPs")
    if chain["generation"] == 0 and wall_left < target:
        return Result("training_clock", False, f"the chain's wall limit {wall_left} s "
                      f"cannot hold the training target {target} s: raise "
                      "long_wall_seconds (the partition limit is checked by time_limits) "
                      "or lower the target")
    return Result("training_clock", True, shown,
                  warn=chain["generation"] > 0 and wall_left < left)


def check_seeds(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    """#603: seeds-file length against the lines the run's driver streams can reach
    (seed_games_per_driver_hour is an upper bound, so this over-estimates). A wrap is
    allowed (each driver counts it in summary.json) but warned loudly. For a chain the
    reach is up to the chain's wall limit, else to this run's wall."""
    k, chain, count = plan["knobs"], plan.get("chain"), facts.get("seeds_file_count")
    if k["eval_only"]:
        listed, slots = plan.get("eval_only") or {}, plan["seed_stride"] * k["env_slots"]
        if not listed.get("seeds") or not listed.get("model"):
            return Result("seeds", False, "eval_only: no frozen eval list or no served model")
        return Result("seeds", True, f"EVALUATION (play, never train) of model "
                      f"{listed['model']}: {listed['suite']} "
                      f"sha256 {listed['sha256'][:12]}, {len(listed['seeds'])} seeds, one game "
                      f"each on {slots} slots, no wrap", warn=slots < len(listed["seeds"]))
    if not count:
        return Result("seeds", False, f"no seeds in site seeds_file {plan['site']['seeds_file']}")
    rate, stride, start = k["seed_games_per_driver_hour"], plan["seed_stride"], plan["seed_start"]
    seconds = k["long_wall_seconds"] if chain is not None else plan["wall_seconds"]
    if chain is not None and chain["generation"] > 0:
        seconds = plan["wall_seconds"]  # the rest of the chain from here
    games = math.ceil(seconds / 3600 * rate)
    reach = start + games * stride
    shown = (f"{count} lines; {stride} driver streams (stride) from line {start}, up to "
             f"{games} games each in {seconds} s at <= {rate:g}/driver hour: reach line "
             f"{reach}")
    if reach > count:
        return Result("seeds", True, f"WILL WRAP: {shown} > {count} (about "
                      f"{reach / count:.1f} passes; seeds repeat, counted per driver)", warn=True)
    return Result("seeds", True, shown)


def check_cores(plan: dict[str, Any]) -> Result:
    total = plan["placement"]["gpu_cores"] + plan["env_cores_max"]
    cap = plan["core_cap"]
    return Result("core_cap", total <= cap, f"GPU {plan['placement']['gpu_cores']} + env "
                  f"{plan['env_cores_max']} = {total} cores (cap {cap})")


def check_resume(plan: dict[str, Any], facts: dict[str, Any]) -> Result:
    """A fresh start needs its start package (the learner loads it); a resume restores the
    CP and never reads it. A resume or chain generation: a real producer and enough window
    left."""
    k, chain, cp = plan["knobs"], plan.get("chain"), plan.get("resume_checkpoint")
    bad = []
    if k["eval_only"] and cp:  # served in a fresh run: the learner's own refusals, now
        bad += served_checkpoint_problems(plan, facts)
    if not cp and facts.get("start_model") is None:
        bad.append(f"fresh start without a start package: no model.json in site "
                   f"start_package {plan['site']['start_package']}")
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


def served_checkpoint_problems(plan: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """eval_only --resume-checkpoint (LearnerConfig.serve_checkpoint): what the learner
    refuses at start, from the CP header (no torch): not a self-contained CP, restore
    parents, a CP of an evaluation run is fine (same weights), heads whose value-config input
    differs from this run's (stpd.rl.recipe_changes.served_checkpoint_refusal)."""
    from stpd.rl.recipe_changes import served_checkpoint_refusal

    cp, served = plan["resume_checkpoint"], facts.get("served_checkpoint") or {}
    if "config" not in served:
        return [f"served CP {cp}: {served.get('error', 'not read')}"]
    if served["kind"] != "checkpoint" or served["parents"]:
        return [f"served CP {cp}: manifest kind {served['kind']!r}, {served['parents']} "
                "parents (the learner needs a self-contained CP without parents)"]
    base, frozen = facts.get("gate_learner_base"), facts.get("frozen_recipe")
    if base is None or frozen is None:
        return ["no gate learner-base.json or frozen config"]
    config, _ = learner_config.learner_base(base, frozen, plan["knobs"], {
        "run_name": "preflight", "peer_dir": "-", "start_package": "-",
        "wall_seconds": plan["wall_seconds"], "device": plan["placement"]["learner_device"],
        "generation": "-", "warmup_artifact_id": None, "rendezvous_path": "-",
        "ranks": plan["placement"]["ranks"], "resume_checkpoint": cp,
        "served_config": served["config"]})
    refusal = served_checkpoint_refusal(served["config"],
                                        config["segments"]["value_config_input"])
    return [f"served CP {cp}: {refusal}"] if refusal else []


def check_queue(facts: dict[str, Any]) -> Result:
    """Printed, never enforced (CLUSTER-QUEUE-DIAG 11-14): this user's fair-share factor and,
    in the GPU partition and the prep partition, how many pending jobs rank above a job
    submitted now. Backfill looks at only the first bf_max_job_test of them per cycle (500
    there), so a long list means a long wait. The prep jobs queue first, at most
    prep_pending_max_seconds; the GPU job queues once every prep host runs."""
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


def check_evaluation_jobs(plan: dict[str, Any]) -> Result:
    """Printed (#612): where the evaluation watchers' jobs run (site eval_partition /
    eval_constraint; absent = today's partition general without a constraint) and the
    share of planned games not finished and scored above which a round is degraded. The
    node class decided usability on hardware: of 300 games the slow class cut 80 at the
    1800 s cap (plus 1 early TimeoutExpired and 3 not started, #614)."""
    site, k = plan["site"], plan["knobs"]
    partition = site.get("eval_partition") or "general (fast_eval default)"
    constraint = site.get("eval_constraint") or "none"
    return Result("evaluation_jobs", True, f"partition {partition}, node constraint "
                  f"{constraint}; a round above {k['eval_degraded_share']:.0%} of planned games "
                  "not finished and scored is labelled degraded, never a baseline; per-game "
                  f"cap {k['eval_episode_seconds']} s, job wall "
                  f"{k['eval_wall_seconds'] or 'planned from the games'}",
                  warn=not site.get("eval_constraint"))


def check_evaluation_cap(plan: dict[str, Any]) -> Result:
    """#614: a per-game cap above 1800 s with the planned job wall (eval_wall_seconds 0)
    keeps the fast-node start window (planned play), so on the slow node class the last
    games of a round may not start. Printed as a warning, never refused."""
    k = plan["knobs"]
    cap, wall = k["eval_episode_seconds"], k["eval_wall_seconds"]
    if cap > 1800 and not wall:
        return Result("evaluation_cap", True, f"eval_episode_seconds {cap} with "
                      "eval_wall_seconds 0: the planned job wall keeps the fast-node start "
                      "window, so slow-node rounds may leave games not started; set "
                      "eval_wall_seconds too (e.g. 17000 with 3600)", warn=True)
    return Result("evaluation_cap", True, f"per-game cap {cap} s, job wall "
                  f"{wall or 'planned from the games'}")


def checks(plan: dict[str, Any], facts: dict[str, Any]) -> list[Result]:
    return [check_placement(plan), check_gpu_request(plan, facts),
            check_gpu_node_cores(plan), check_machine_types(plan),
            check_prep_scratch(plan), check_prep_schedulable(plan, facts),
            check_switches(plan), check_features(plan, facts), check_producer(plan, facts),
            check_frozen_recipe(plan, facts), check_declared_changes(plan, facts),
            check_hp_score(plan, facts),
            check_compile_warmup(plan, facts), check_warmup_artifact(plan, facts),
            check_env_package(plan, facts), check_shards(plan, facts),
            check_memory(plan, facts), check_unix_sockets(plan),
            check_prepared_batch_cap(plan), check_service_cpus(plan),
            check_identity(plan), check_hot_path(plan),
            check_secrets(facts), check_time_limits(plan), check_training_clock(plan),
            check_seeds(plan, facts), check_cores(plan),
            check_resume(plan, facts), check_evaluation_jobs(plan), check_evaluation_cap(plan),
            check_queue(facts)]


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
