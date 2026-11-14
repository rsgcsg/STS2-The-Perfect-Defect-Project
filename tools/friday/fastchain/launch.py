#!/usr/bin/env python3
"""The one fast-chain launcher: resources + layout + run profile -> Slurm jobs.

    launch.py plan      RUNTAG --profile P --site S --cards N --card-type T [options]
    launch.py preflight RUNTAG ... (plan + every start check; submits nothing)
    launch.py submit    RUNTAG ... (preflight, then prep -> wait ready -> GPU + env)

Options: --layout L (default: the profile's default layout for N cards; "shared",
"1i+1t", "1i+2t" or an explicit per-card list, see layout.py), --env-cores C (cap on
environment cores), --set knob=value (repeatable, recorded as an override), --smoke
(5 min, the smallest balanced env), --long-run (wall = long_wall_seconds, 20 h in
one GPU job), --probe-only (allow the placeholder producer
identity outside --smoke: CPs void, recorded in plan.json), --fallback (fewer cards
with their default layouts when N do not fit), --probe-json F (a recorded sinfo probe
instead of sinfo).

What this replaces: the private fc-launch.py tiers (1/2/3/h100) and the hand-edited
fc_gpu.py / fc-gpu3.sh / fc-env2.sh / fc-prep3w.sh. A layout is data; every knob comes
from the run profile; the effective profile, the plan and a frozen copy of the harness
(with sha256) are written into a NEW run directory, and jobs run only that copy.
--resume-checkpoint CP starts from a CP. A long run with automatic resume, the hourly
readout and the 2 h evaluations goes through supervise.py, which calls this launcher once
per generation (--chain-json). Not here yet: the segment-deletion knob (product side).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import layout as layouts  # noqa: E402
import preflight  # noqa: E402
import run_profile  # noqa: E402

HARNESS = ("roles.py", "preflight.py", "run_profile.py", "layout.py", "readout.py",
           "learner_config.py",
           "gpu.sh", "env.sh", "prep.sh", "env_driver.mjs", "env_aggregator.py",
           "env_admission.py", "proc_cpu.py", "proc_sampler.py", "native_nice.sh",
           "elu_preload.mjs")
CANDIDATE_DLL = "src/Sts2Headless/bin/Release/net9.0/Sts2Headless.dll"
TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def probe(nodes: list[str], gres: str) -> list[dict[str, Any]]:
    """Free cards / idle cores / free memory per GPU node (sinfo), as fc-launch did."""
    out = sh("sinfo", "-N", "-h", "-n", ",".join(nodes), "-O",
             "nodelist:20,statelong:20,gres:40,gresused:60,cpusstate:30,memory:12,allocmem:12")
    return parse_probe(out, gres)


def parse_probe(text: str, gres: str) -> list[dict[str, Any]]:
    seen, rows = set(), []
    pattern = re.compile(rf"gpu:{re.escape(gres)}:(\d+)")
    for line in text.splitlines():
        f = line.split()
        if len(f) < 7 or f[0] in seen:
            continue
        seen.add(f[0])
        total_m, used_m = pattern.search(f[2]), pattern.search(f[3])
        total = int(total_m.group(1)) if total_m else 0
        used = int(used_m.group(1)) if used_m else 0
        idle = int(f[4].split("/")[1])
        usable = f[1] in ("idle", "mixed")
        rows.append({"node": f[0], "state": f[1], "gpus": total,
                     "gpus_free": total - used if usable else 0,
                     "cores_idle": idle if usable else 0,
                     "mem_free_g": (int(f[5]) - int(f[6])) // 1024 if usable else 0})
    return rows


def balanced_env_nodes(budget_nodes: int, aggregators: int, services: int) -> int:
    """Largest node count <= budget whose aggregators spread evenly over services."""
    step = services // math.gcd(aggregators, services)
    return (budget_nodes // step) * step


def split_slow(env_nodes: int, slow_opt_in: int) -> tuple[int, int]:
    """(fast nodes, c8220 nodes); each c8220 node counts 0.5 node toward the budget."""
    slow = min(slow_opt_in, 2 * env_nodes)
    return max(0, env_nodes - (slow + 1) // 2), slow


def build_plan(runtag: str, knobs: dict[str, Any], site: dict[str, Any], *, cards: int,
               card_type: str, layout_spec: str | None, env_cores: int | None, smoke: bool,
               probe_rows: list[dict[str, Any]], fallback: bool = False,
               probe_only: bool = False, long_run: bool = False,
               resume_checkpoint: str | None = None,
               chain: dict[str, Any] | None = None) -> dict[str, Any]:
    """`chain` (supervise.py): {"id", "deadline_at", "total_seconds", "generation"}; the
    wall is then what is left of the chain's window."""
    if card_type not in site["gpu_types"]:
        raise SystemExit(f"card type {card_type} not in the site profile")
    gpu = site["gpu_types"][card_type]
    tried, chosen = [], None
    for count in (range(cards, 0, -1) if fallback else (cards,)):
        spec = layout_spec if count == cards and layout_spec else \
            knobs["default_layouts"].get(str(count))
        if not spec:
            tried.append(f"{count} cards: no default layout")
            continue
        place = layouts.place(spec, count, card_type, service_cores=knobs["service_cores"],
                              rank_cores=knobs["rank_cores"])
        mem = knobs["job_mem_g"].get(str(count))
        if mem is None:
            raise SystemExit(f"job_mem_g has no entry for {count} cards")
        fits = [r["node"] for r in probe_rows if r["gpus_free"] >= count
                and r["cores_idle"] >= place.gpu_cores and r["mem_free_g"] >= mem]
        if fits:
            chosen = (count, spec, place, mem, fits[0])
            tried.append(f"{place.name}: {fits[0]} fits ({count} free {card_type}, "
                         f"{place.gpu_cores} cores, {mem}G)")
            break
        tried.append(f"{place.name}: no node with {count} free {card_type}, "
                     f"{place.gpu_cores} idle cores, {mem}G")
    if chosen is None:  # report the requested layout with no node
        spec = layout_spec or knobs["default_layouts"][str(cards)]
        place = layouts.place(spec, cards, card_type, service_cores=knobs["service_cores"],
                              rank_cores=knobs["rank_cores"])
        chosen = (cards, spec, place, knobs["job_mem_g"][str(cards)], None)
    count, spec, place, mem, node = chosen
    key = str(count)
    cap = knobs["core_cap"][key]
    budget = min(knobs["env_core_budget"][key], cap - place.gpu_cores,
                 env_cores if env_cores is not None else 10**9)
    step_nodes = balanced_env_nodes(max(budget, 0) // knobs["env_node_cores"],
                                    knobs["env_aggregators"], knobs["services"])
    if smoke:  # the smallest env that still spreads evenly over the services
        step_nodes = knobs["services"] // math.gcd(knobs["env_aggregators"], knobs["services"])
    fast, slow = split_slow(step_nodes, 0 if smoke else knobs["env_slow_type_nodes"])
    if smoke and long_run:
        raise SystemExit("--smoke and --long-run exclude each other")
    wall = (knobs["smoke_wall_seconds"] if smoke else
            knobs["long_wall_seconds"] if long_run else knobs["wall_seconds"])
    if chain is not None:
        if smoke:
            raise SystemExit("--smoke is never a chain generation")
        wall = max(0, min(wall, int(chain["deadline_at"] - time.time())))
    limit = mem * preflight.GIB
    target = layouts.target(count, knobs["targets_rows_h"], knobs["ready_rows_h"])
    cap_rows = knobs["card_capacity_rows_h"].get(card_type)
    # Perfect packing is the ideal for split cards only: on one shared card inference and
    # training overlap under MPS, so N / (1/inf + 1/train) is not an upper bound there.
    if cap_rows and not place.shared_inference_card:
        target["perfect_packing_rows_h"] = round(layouts.perfect_packing(
            count, cap_rows["inference"], cap_rows["training"]))
    prep_hosts = sum(g["nodes"] for g in knobs["prep_groups"]) if knobs["remote_prepare"] else 0
    return {
        "schema": "spireagent/friday-fastchain-plan-1", "runtag": runtag, "t": time.time(),
        "run": str(Path(site["work_root"]) / f"run-{runtag}"),
        "requested": {"cards": cards, "card_type": card_type, "layout": layout_spec,
                      "env_cores": env_cores, "fallback": fallback},
        "why": tried, "smoke": smoke, "long_run": long_run, "wall_seconds": wall,
        "producer_identity": knobs["producer_identity"], "probe_only": probe_only,
        "resume_checkpoint": resume_checkpoint, "chain": chain,
        "checkpoints_void": knobs["producer_identity"] == "placeholder",
        "placement": place.as_dict(), "layout_spec": spec, "name": place.name,
        "target": target, "gpu_node": node, "gpu_node_why": "; ".join(tried),
        "gpu_partition": gpu["partition"], "gpu_gres": gpu["gres"],
        "job_mem_g": mem, "core_cap": cap,
        # Fractions of the job's cgroup limit (--mem), so they always sit below it.
        "mem_guard_bytes": int(knobs["mem_guard_fraction"] * limit),
        "mem_hard_bytes": int(knobs["mem_hard_fraction"] * limit),
        "env_nodes_max": fast + slow, "env_fast_nodes": fast, "env_slow_nodes": slow,
        "env_cores_max": fast * knobs["env_node_cores"]
        + (slow + 1) // 2 * knobs["env_node_cores"],
        "env_node_index_ranges": {"fast": [0, fast], "c8220": [fast, slow]},
        "prep_hosts": prep_hosts,
        "gpu_minutes": (wall + 240) // 60 + 1,
        "env_minutes": (knobs["env_wait_seconds"] + wall + 600) // 60,
        "prep_minutes": (wall + 240 + int(knobs["prep_start_timeout_seconds"])) // 60 + 1,
        "partition_max_minutes": {
            role: site["partition_max_minutes"].get(part) for role, part in (
                ("gpu", gpu["partition"]), ("env", site["env_partition"]),
                ("prep", site["prep_partition"]))},
        "knobs": knobs, "site": site,
    }


def _time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def commands(plan: dict[str, Any]) -> dict[str, list[str]]:
    run, k, site = plan["run"], plan["knobs"], plan["site"]
    harness = f"{run}/harness"
    export = f"--export=ALL,FC_RUN={run}"
    p = plan["placement"]
    gpu = ["sbatch", "--parsable", f"--job-name=fc-gpu-{plan['runtag']}",
           f"--partition={plan['gpu_partition']}",
           *([f"--nodelist={plan['gpu_node']}"] if plan["gpu_node"] else []),
           f"--gres=gpu:{plan['gpu_gres']}:{p['cards']}", "--nodes=1",
           f"--cpus-per-task={p['gpu_cores']}", f"--mem={plan['job_mem_g']}G",
           f"--time={_time(plan['gpu_minutes'])}", "--no-requeue",
           f"--output={run}/slurm-gpu-%j.out", export, f"{harness}/gpu.sh"]

    def env_array(nodes: int, constraint: str, base: int, label: str) -> list[str]:
        return ["sbatch", "--parsable", f"--job-name=fc-env-{plan['runtag']}",
                f"--partition={site['env_partition']}", f"--array=0-{nodes - 1}",
                f"--constraint={constraint}", "--nodes=1",
                f"--cpus-per-task={k['env_node_cores']}", f"--mem={k['env_node_mem_g']}G",
                f"--time={_time(plan['env_minutes'])}", "--no-requeue",
                "--signal=B:USR1@120", f"--output={run}/slurm-env-{label}-%A_%a.out",
                f"{export},FC_NODEIDX_BASE={base}", f"{harness}/env.sh"]

    out: dict[str, list[str]] = {"gpu": gpu}
    if plan["env_fast_nodes"]:
        out["env"] = env_array(plan["env_fast_nodes"], "|".join(k["env_machine_types"]), 0,
                               "fast")
    if plan["env_slow_nodes"]:
        out["env_c8220"] = env_array(plan["env_slow_nodes"], "c8220", plan["env_fast_nodes"],
                                     "c8220")
    if plan["prep_hosts"]:
        prep = ["sbatch", "--parsable", f"--job-name=fc-prep-{plan['runtag']}",
                "--no-requeue", f"--output={run}/slurm-prep-%j.out"]
        groups = [g for g in k["prep_groups"] if g["nodes"]]
        for i, g in enumerate(groups):
            if i:
                prep.append(":")
            prep += [f"--partition={site['prep_partition']}",
                     f"--time={_time(plan['prep_minutes'])}", f"--constraint={g['type']}",
                     f"--nodes={g['nodes']}", "--ntasks-per-node=1",
                     f"--cpus-per-task={g['cpus']}", f"--mem={g['mem_g']}G"]
            if site["excluded_prep_hosts"]:
                prep.append("--exclude=" + ",".join(site["excluded_prep_hosts"]))
            if i == 0:
                prep.append(export)
        out["prep"] = prep + [f"{harness}/prep.sh"]
    return out


def job_env(plan: dict[str, Any]) -> str:
    """Shell exports for the job scripts. Paths and knobs only; secrets stay in files."""
    p, site, k = plan["placement"], plan["site"], plan["knobs"]
    lines = ["# written by launch.py; jobs source this file. No secret values here."]
    lines += run_profile.env_lines(k)
    for key, value in sorted(site.items()):
        if key in ("gpu_types",):
            continue
        text = json.dumps(value, separators=(",", ":")) if isinstance(value, (list, dict)) \
            else str(value)
        lines.append(f"export FC_SITE_{key.upper()}={shlex.quote(text)}")
    groups = [{**g, "scratch": site["prep_scratch"][g["type"]],
               "workers": g["workers"]} for g in k["prep_groups"] if g["nodes"]]
    derived = {
        "FC_HARNESS": f"{plan['run']}/harness", "FC_RUNTAG": plan["runtag"],
        "FC_CARDS": p["cards"], "FC_SERVICE_CARD": p["service_card"],
        "FC_LEARNER_VISIBLE": ",".join(map(str, p["learner_visible"])),
        "FC_LEARNER_DEVICE": p["learner_device"], "FC_RANKS": p["ranks"],
        "FC_LEARNER_CORES": p["learner_cores"], "FC_SHARED_CARD": int(p["shared_inference_card"]),
        "FC_WALL": plan["wall_seconds"], "FC_SMOKE": int(plan["smoke"]),
        "FC_MIN_JOIN_S": 60 if plan["smoke"] else k["env_min_join_seconds"],
        "FC_WAIT_N": k["env_wait_seconds"] // 5, "FC_PREP_HOSTS": plan["prep_hosts"],
        "FC_PREP_GROUPS_JSON": json.dumps(groups, separators=(",", ":")),
        "FC_MEM_GUARD_BYTES": plan["mem_guard_bytes"], "FC_MEM_HARD_BYTES": plan["mem_hard_bytes"],
        "FC_GENERATION": f"g-fc-{plan['runtag']}", "FC_NAME": plan["name"],
        "FC_ENV_NODES": plan["env_nodes_max"],  # R7: each node takes 1/N of rate and cap
        "FC_RESUME_CHECKPOINT": plan.get("resume_checkpoint") or "",
        **({"FC_CHAIN_ID": plan["chain"]["id"],
            "FC_CHAIN_DEADLINE": repr(float(plan["chain"]["deadline_at"])),
            "FC_CHAIN_TOTAL_SECONDS": plan["chain"]["total_seconds"]}
           if plan.get("chain") else {}),
        # remote_prepare --scratch-min-free-bytes (#523 a437612d) = the in-flight budget.
        "FC_PREP_MIN_FREE_BYTES": int(k["prep_host_inflight_gb"] * 1e9)
        if k["prep_scratch_min_free"] else 0,
    }
    lines += [f"export {key}={shlex.quote(str(value))}" for key, value in derived.items()]
    return "\n".join(lines) + "\n"


def collect_facts(plan: dict[str, Any], cmds: dict[str, list[str]]) -> dict[str, Any]:
    site = plan["site"]
    facts: dict[str, Any] = {}
    src = Path(site["src_dir"])
    facts["src_checkout"] = checkout_facts(src)
    facts["src_rev"] = (facts["src_checkout"].get("head") if facts["src_checkout"]["git"]
                        else (src / "REV").read_text().strip() if (src / "REV").exists()
                        else None)
    facts["src_features"] = source_features(src)
    frozen = src / plan["knobs"]["frozen_learner_config"]
    facts["frozen_recipe"] = json.loads(frozen.read_text()) if frozen.exists() else None
    gate = Path(site["gate_dir"]) / "learner-base.json"
    facts["gate_learner_base"] = json.loads(gate.read_text()) if gate.exists() else None
    # A dump is a directory; sgd_process warm() loads offline_step.TREE inside it.
    facts["compile_warmup_dumps"] = {
        path: os.path.isfile(os.path.join(path, "offline-step.tensor-tree"))
        for path in site["compile_warmup_dumps"]}
    rev = Path(site["env_pkg"]) / "SOURCE_REVISION"
    facts["env_pkg_rev"] = rev.read_text().strip() if rev.exists() else None
    dll = Path(site["env_candidate"]) / CANDIDATE_DLL
    facts["env_candidate_sha256"] = (hashlib.sha256(dll.read_bytes()).hexdigest()
                                     if dll.exists() else None)
    facts["secret_files"] = {
        site[key]: (os.stat(site[key]).st_mode & 0o777 if os.path.exists(site[key]) else None)
        for key in ("inference_auth_file", "prepare_auth_file")}
    facts["prep_test_start_seconds"] = (test_start(cmds["prep"]) if "prep" in cmds else 0.0)
    return facts


# feature -> (source file, text that only a build with it contains).
FEATURES = {
    "shards": ("python/stpd/rl/segment_learner.py", '"shards"'),  # #523
    "budget_counts_remote": ("python/stpd/rl/remote_prepare.py",  # #523 a437612d
                             "--scratch-min-free-bytes"),
    "adopt_unsharded": ("python/stpd/rl/segment_learner.py",  # #523 a437612d
                        "shards_adopt_unsharded_checkpoint"),
    "stage_options": ("python/stpd/rl/sgd_process.py", "sgd_stage_stream_reuse"),  # #525
    "residual": ("python/stpd/p1/rows_forward.py", "def residual_stream"),  # #526
    "gc_freeze": ("python/stpd/rl/sgd_process.py", '"sgd_gc_freeze"'),  # #525 b40d31db
    "step_probe": ("python/stpd/rl/sgd_process.py", '"sgd_step_probe"'),  # #525
    "compile_warmup": ("python/stpd/rl/sgd_process.py", '"warmup"'),  # WP8 sgd_compile
    "retention": ("python/stpd/rl/fast_release.py", "release_trained_segments"),  # #529
    "mu_archive": ("python/stpd/rl/fast_mu_pack.py", "def local_mu_archive"),  # #529
    "r7_max_games": ("python/spireagent/workbench/fast_chain_episodes.py",  # #529
                     '"max_games"'),
    # R7 games' episode_open must ask the assembler to archive their mu (not yet in #529)
    "r7_audit_mu": ("components/policy-runtime/src/fast-env/kernel.ts", "audit_retain_mu"),
    "segment_format_3": ("python/stpd/rl/fast_segment.py", "stpd/rl-fast-segment-3"),  # #530
    "segment_format_4": ("python/stpd/rl/fast_segment.py", "stpd/rl-fast-segment-4"),  # W2
    "ascension": ("components/host-runtime/src/managed-player-environment.mjs",  # #530
                  "managed_ascension_mismatch"),
    "eval_suite": ("tools/friday/fast_eval.py", '"--suite"'),  # #528
}


def checkout_facts(src: Path) -> dict[str, Any]:
    """Is src_dir a clean git checkout (what source_identity needs), and at which commit."""
    if not (src / ".git").exists():
        return {"git": False}

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(src), *args], capture_output=True,
                              text=True).stdout.strip()

    return {"git": True, "head": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain"))}


def source_features(src: Path) -> dict[str, bool]:
    out = {}
    for name, (rel, marker) in FEATURES.items():
        path = src / rel
        out[name] = path.exists() and marker in path.read_text(errors="replace")
    return out


def test_start(cmd: list[str]) -> float | None:
    """Seconds until Slurm expects the prep job to start (`sbatch --test-only`)."""
    # The run's harness copy does not exist before submit: test with the source script.
    trial = [cmd[0], "--test-only", *[c for c in cmd[1:-1] if c != "--parsable"],
             str(HERE / Path(cmd[-1]).name)]
    out = subprocess.run(trial, capture_output=True, text=True)
    return parse_test_only(out.stdout + out.stderr, time.time())


def parse_test_only(text: str, now: float) -> float | None:
    match = re.search(r"to start at (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", text)
    if not match:
        return None
    start = time.mktime(time.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S"))
    return max(0.0, start - now)


def freeze(plan: dict[str, Any], profile: dict[str, Any], cmds: dict[str, list[str]]) -> Path:
    run = Path(plan["run"])
    if run.exists():
        raise SystemExit(f"{run} exists: every run (and every continuation) gets a new "
                         "directory (RL4.1 long-run lesson)")
    (run / "harness").mkdir(parents=True, mode=0o700)
    digests = {}
    for name in HARNESS:
        shutil.copy2(HERE / name, run / "harness" / name)
        digests[name] = hashlib.sha256((HERE / name).read_bytes()).hexdigest()
    effective = {"schema": run_profile.SCHEMA, "knobs": profile, "name": plan["name"],
                 "harness_sha256": digests}
    (run / "effective-profile.json").write_text(json.dumps(effective, indent=1, sort_keys=True))
    (run / "job-env.sh").write_text(job_env(plan))
    record = {k: v for k, v in plan.items() if k not in ("knobs", "site")}
    record["commands"] = cmds
    (run / "plan.json").write_text(json.dumps(record, indent=1, default=str))
    return run


def wait_prep(run: Path, job: str, hosts: int, timeout: float) -> str | None:
    """None when every prep host is ready; else the refusal."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        refused = sorted((run / "prep-refused").glob("*")) if (run / "prep-refused").exists() \
            else []
        if refused:
            return "prep host refused: " + "; ".join(f.read_text().strip() for f in refused)
        ready = list((run / "prep-ready").glob("ready-*")) if (run / "prep-ready").exists() \
            else []
        if len(ready) >= hosts:
            return None
        state = subprocess.run(["squeue", "-h", "-j", job, "-o", "%T"], capture_output=True,
                               text=True).stdout.strip()
        if not state:
            return f"prep job {job} ended before every host was ready"
        time.sleep(5)
    return f"prep hosts not ready within {timeout:.0f} s"


def submit(run: Path, plan: dict[str, Any], cmds: dict[str, list[str]],
           *, run_cmd: Any = None, wait: Any = None) -> dict[str, Any]:
    """prep -> wait every host ready -> GPU -> env arrays. Any failure or interrupt on the
    way (a later sbatch failing, a prep refusal, Ctrl-C during the wait) cancels every job
    already submitted, then re-raises. Nothing is retried."""
    run_cmd = run_cmd or (lambda cmd: sh(*cmd))
    wait = wait or wait_prep
    jobs: dict[str, Any] = {}

    def go(name: str) -> str:
        job = run_cmd(cmds[name]).strip().split(";")[0]
        jobs[name] = job
        (run / "jobs.json").write_text(json.dumps(jobs, indent=1))
        return job

    try:
        if "prep" in cmds:
            prep = go("prep")
            why = wait(run, prep, plan["prep_hosts"],
                       plan["knobs"]["prep_start_timeout_seconds"] + 300)
            if why:
                raise SystemExit(f"refused after prep submission: {why}; GPU job not "
                                 "submitted")
        gpu = go("gpu")
        (run / "gpu-job.txt").write_text(gpu + "\n")
        for name in ("env", "env_c8220"):
            if name in cmds:
                go(name)
    except BaseException as error:
        cancelled = cancel_all(jobs)
        (run / "submit-failed.txt").write_text(f"{error!r}\ncancelled {cancelled}\n")
        print(f"submit failed ({error!r}); cancelled {cancelled}", file=sys.stderr)
        raise
    return jobs


def cancel_all(jobs: dict[str, Any]) -> list[str]:
    ids = [str(job) for job in jobs.values()]
    if ids:
        subprocess.run(["scancel", *ids], check=False)
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("plan", "preflight", "submit"))
    parser.add_argument("runtag")
    parser.add_argument("--profile", type=Path, default=HERE / "rl5-fastchain.toml")
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--cards", type=int, required=True)
    parser.add_argument("--card-type", required=True)
    parser.add_argument("--layout")
    parser.add_argument("--env-cores", type=int)
    parser.add_argument("--set", action="append", default=[], dest="overrides")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--fallback", action="store_true")
    parser.add_argument("--long-run", action="store_true",
                        help="wall = long_wall_seconds (20 h, one GPU job)")
    parser.add_argument("--probe-only", action="store_true",
                        help="allow producer_identity=placeholder outside --smoke (CPs void)")
    parser.add_argument("--probe-json", type=Path)
    parser.add_argument("--resume-checkpoint", help="start from this CP (first start from a "
                        "no-shard CP: --set shards_adopt_unsharded_checkpoint=true)")
    parser.add_argument("--chain-json", type=Path,
                        help="supervise.py only: this run is one generation of a chain")
    parser.add_argument("--out", type=Path, help="plan: also write the full plan.json here "
                        "(e.g. for `readout.py --plan` on a run made before this launcher)")
    args = parser.parse_args(argv)
    if not TAG.match(args.runtag):
        raise SystemExit("runtag: lowercase letters, digits and '-'")
    try:
        profile = run_profile.load_profile(args.profile, args.overrides)
        site = run_profile.load_site(args.site)
        knobs = run_profile.values(profile)
        gpu = site["gpu_types"].get(args.card_type)
        if gpu is None:
            raise SystemExit(f"card type {args.card_type} not in the site profile")
        rows = (json.loads(args.probe_json.read_text()) if args.probe_json
                else probe(gpu["nodes"], gpu["gres"]))
        plan = build_plan(args.runtag, knobs, site, cards=args.cards,
                          card_type=args.card_type, layout_spec=args.layout,
                          env_cores=args.env_cores, smoke=args.smoke, probe_rows=rows,
                          fallback=args.fallback, probe_only=args.probe_only,
                          long_run=args.long_run, resume_checkpoint=args.resume_checkpoint,
                          chain=(json.loads(args.chain_json.read_text())
                                 if args.chain_json else None))
    except (run_profile.ProfileError, layouts.LayoutError) as error:
        print(error, file=sys.stderr)
        return 2
    cmds = commands(plan)  # without a free GPU node: shown, refused by check_gpu_node
    summary = {k: plan[k] for k in ("name", "target", "gpu_node", "why", "env_nodes_max",
                                     "env_cores_max", "prep_hosts", "wall_seconds",
                                     "producer_identity", "checkpoints_void")}
    summary["placement"] = plan["placement"]
    summary["commands"] = cmds
    if args.command == "plan":
        if args.out:
            args.out.write_text(json.dumps(plan, indent=1, ensure_ascii=False, default=str))
        print(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
        return 0
    facts = collect_facts(plan, cmds)
    results = preflight.checks(plan, facts)
    print(preflight.report(results))
    if not all(r.ok for r in results):
        return 3
    if args.command == "preflight":
        return 0
    run = freeze(plan, profile, cmds)
    (run / "preflight.txt").write_text(preflight.report(results) + "\n")
    # The source this run was frozen from: the identity of its run record (readout.py).
    (run / "facts.json").write_text(json.dumps({key: facts.get(key) for key in (
        "src_checkout", "src_rev", "src_features", "env_pkg_rev", "env_candidate_sha256")},
        indent=1))
    jobs = submit(run, plan, cmds)
    print(json.dumps({"run": str(run), "name": plan["name"], "jobs": jobs}, ensure_ascii=False),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
