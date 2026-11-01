#!/usr/bin/env python3
"""Freeze, collect and score a current-policy critic diagnostic through normal APIs."""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import shlex
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, cast

PLAN = "rl3/current-policy-critic-evaluation-plan-1"
SEEDS = "rl3/fixed-current-policy-critic-seeds-1"


def hashed(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def bound(ref: dict[str, Any]) -> dict[str, Any]:
    path = Path(ref["path"])
    if hashed(path) != ref["sha256"]:
        raise ValueError("value_validation_frozen_reference_changed")
    return cast(dict[str, Any], json.loads(path.read_bytes()))


def fixed_seeds(count: int) -> dict[str, Any]:
    from stpd.evaluation.suites import diagnostic_reference_manifest, seed_membership
    from stpd.rl.critic import held_out

    if type(count) is not int or not 1 <= count <= 200:
        raise ValueError("value_validation_count_1_to_200_required")
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    index = 0
    while len(groups) < count:
        group = hashlib.sha256(
            ("rl3-current-policy-value-validation-fixed-v1:" + str(index)).encode()).hexdigest()[:32]
        index += 1
        manifest = diagnostic_reference_manifest("v0.111.0", group, 1)
        manifest["host"] = "Managed"
        seed = manifest["seeds"][0]
        if held_out(seed) and not seed_membership(seed) and seed not in seen:
            seen.add(seed)
            groups.append(manifest)
    return {"schema": SEEDS, "groups": groups, "seeds": sorted(seen),
        "permanent_exclusion": "held_out(seed): no policy or value optimizer admission",
        "selection": "first canonical one-seed groups passing permanent TRAIN holdout hash; no outcomes"}


def prepare(root: Path, learner_config: Path, execution_template: Path, runtime_source: Path, *,
            count: int = 48, workers: int = 12, wall_seconds: int = 2700,
            episode_seconds: int = 900, seeds_path: Path | None = None) -> dict[str, Any]:
    import evaluate
    import watch
    from spireagent.rl_exchange import Exchange
    from stpd.rl.checkpoint import learner_state
    from stpd.workers.checkpoint_codec import decode_checkpoint

    if (root.exists() or not 1 <= workers <= 16 or not 600 <= wall_seconds <= 21600
            or not 10 <= episode_seconds <= 1800 or wall_seconds <= episode_seconds + 450):
        raise ValueError("new_bounded_value_validation_point_required")
    config = json.loads(learner_config.read_bytes())
    exchange = Exchange(config["peer"], config["run"])
    checkpoint = exchange.latest_checkpoint()
    if checkpoint is None:
        raise ValueError("complete_checkpoint_required")
    model = exchange.model(checkpoint["version"])
    root.mkdir(parents=True, mode=0o700)
    binding = watch.Watcher({"peer": config["peer"], "run": config["run"],
        "started_at": time.time()}, root / "normal-CP-reader").checkpoint_binding(checkpoint, model)
    manifest = exchange.store.get_manifest(checkpoint["checkpoint_artifact_id"])
    raw = b"".join(exchange.store.read_payload(manifest.payload("checkpoint")))
    state = decode_checkpoint(raw)
    saved = learner_state(state)
    if saved.get("critic", {}).get("ready") is not True:
        raise ValueError("ready_critic_checkpoint_required")
    (root / "checkpoint.bin").write_bytes(raw)
    package = exchange.materialize_model(model, root / "model")
    seeds = json.loads(seeds_path.read_bytes()) if seeds_path else fixed_seeds(count)
    if seeds.get("schema") != SEEDS or len(seeds["groups"]) != count:
        raise ValueError("fixed_seed_plan_count_mismatch")
    selected_seeds = [g["seeds"][0] for g in seeds["groups"]]
    if len(set(selected_seeds)) != count or sorted(selected_seeds) != seeds["seeds"]:
        raise ValueError("distinct_fixed_seed_plan_required")
    save(root / "fixed-seeds.json", seeds)
    point = {"schema": "rl3/current-policy-critic-point-1", "point_id": root.name,
        "frozen_at": time.time(), "checkpoint": checkpoint, "model": model,
        "normal_full_CP_binding": binding, "checkpoint_file": str(root / "checkpoint.bin"),
        "checkpoint_payload_sha256": manifest.payload("checkpoint").sha256,
        "package": str(package), "learner_config": binding["checkpoint_parameters"]["config"],
        "critic_config": saved["critic"]["config"],
        "critic_target_statistics": saved["critic"]["target_statistics"],
        "critic_policy_updates": saved["updates"],
        "seed_plan": {"path": str(root / "fixed-seeds.json"),
                      "sha256": hashed(root / "fixed-seeds.json")},
        "behavior": {"mode": "temperature_uniform", "temperature": 1.0, "uniform_mix": 0},
        "World_calls": 0, "optimizer_updates": 0}
    save(root / "point.json", point)
    template = root / "execution-template.json"
    template.write_bytes(execution_template.read_bytes())
    controller = root / "value-validation-controller.py"
    controller.write_bytes(Path(__file__).read_bytes())
    request = {"runtime_source": str(runtime_source), "execution_template": str(template),
        "workers": workers, "wall_seconds": wall_seconds, "episode_seconds": episode_seconds,
        "controller": {"path": str(controller), "sha256": hashed(controller)},
        "tool_source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2], text=True).strip()}
    save(root / "preparation-request.json", request)
    env = {**os.environ, "PYTHONPATH": str(runtime_source / "python"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([sys.executable, str(controller), "prepare-execution", "--root", str(root)],
                   env=env, check=True)
    plan: dict[str, Any] = json.loads((root / "plan.json").read_bytes())
    prefix = evaluate.launcher(root / "plan.json", {"source_root": str(runtime_source)}).split(
        "\nexec ", 1)[0]
    command = [sys.executable, str(runtime_source / "tools/friday/allocation.py"),
        "--mode", "evaluation", "--configuration", str(root / "plan.json"),
        "--source-root", str(runtime_source), "--", sys.executable, str(controller),
        "collect", "--plan", str(root / "plan.json")]
    (root / "launch.sh").write_text(prefix + "\nexec " + shlex.join(command) + "\n")
    return plan


def prepare_execution(root: Path) -> dict[str, Any]:
    from spireagent.instance_lock import instance_lock
    from spireagent.source import source_identity
    from spireagent.workbench.evaluation_ledger import EvaluationLedger
    from spireagent.workbench.managed_pipeline import PipelinedManagedExecutor
    from stpd.p1.export import adapter_identity
    from stpd.rl.critic import held_out

    request = json.loads((root / "preparation-request.json").read_bytes())
    source = Path(request["runtime_source"])
    point = json.loads((root / "point.json").read_bytes())
    seeds = bound(point["seed_plan"])
    execution = copy.deepcopy(json.loads(Path(request["execution_template"]).read_bytes()))
    if (execution["producer"] != source_identity(source / "python").to_dict()
            or execution["agent_adapter"] != adapter_identity()):
        raise ValueError("frozen_execution_source_adapter_required")
    execution.update(retention={"tier": "trajectory", "purpose": "training", "audit_every": 50},
        pipeline={"pending_limit": 2, "save_workers": 1},
        models={"critic-current": {"artifact_id": point["model"]["model_artifact_id"],
            "package_directory": point["package"], "behavior_policy": point["behavior"]}})
    path = root / "execution.json"
    save(path, execution)
    executor = PipelinedManagedExecutor(execution, episode_seconds=request["episode_seconds"],
                                        cleanup_seconds=120)
    preflight = executor.preparation
    executor.close()
    groups = []
    for ordinal, manifest in enumerate(seeds["groups"], 1):
        if (manifest["host"] != "Managed" or len(manifest["seeds"]) != 1
                or not held_out(manifest["seeds"][0])):
            raise ValueError("permanent_TRAIN_holdout_required")
        group = root / "groups" / f"{ordinal:03d}"
        group.mkdir(parents=True, mode=0o700)
        seed_file, directory = group / "manifest.json", group / "ledger"
        save(seed_file, manifest)
        with instance_lock(group / "ledger.lock"):
            EvaluationLedger.create(directory, seed_file, hashed(seed_file),
                                    selections={"model": "critic-current"}, execution=path)
        groups.append({"ordinal": ordinal, "seed": manifest["seeds"][0], "ledger": str(directory),
            "plan_sha256": hashed(directory / "plan.json"), "manifest_sha256": hashed(seed_file)})
    plan = {"schema": PLAN, "point": {"path": str(root / "point.json"),
            "sha256": hashed(root / "point.json")}, "fixed_seed_plan": point["seed_plan"],
        "execution": {"path": str(path), "sha256": hashed(path)}, "source_root": str(source),
        "workers": request["workers"], "selected_limit": len(groups),
        "wall_seconds": request["wall_seconds"], "episode_seconds": request["episode_seconds"],
        "cleanup_seconds": 120, "created_at": time.time(),
        "not_after": time.time() + request["wall_seconds"] + 600, "groups": groups,
        "retry_unknown": False, "announce_training_shards": False,
        "controller": request["controller"], "tool_source_revision": request["tool_source_revision"],
        "preflight": preflight, "World_calls_before_submission": 0}
    save(root / "plan.json", plan)
    return plan


def load_plan(path: Path) -> dict[str, Any]:
    plan: dict[str, Any] = json.loads(path.read_bytes())
    if (plan.get("schema") != PLAN or plan.get("retry_unknown") is not False
            or plan.get("announce_training_shards") is not False
            or not 1 <= plan["workers"] <= 16
            or not 1 <= len(plan["groups"]) == plan["selected_limit"] <= 200):
        raise ValueError("frozen_bounded_unscored_value_validation_plan_required")
    for name in ("point", "fixed_seed_plan", "execution"):
        bound(plan[name])
    if "controller" in plan and hashed(Path(plan["controller"]["path"])) != plan["controller"]["sha256"]:
        raise ValueError("value_validation_controller_changed")
    for group in plan["groups"]:
        if hashed(Path(group["ledger"]) / "plan.json") != group["plan_sha256"]:
            raise ValueError("value_validation_ledger_plan_changed")
    return plan


def collect(path: Path) -> dict[str, Any]:
    from spireagent.instance_lock import instance_lock
    from spireagent.workbench.evaluation_ledger import EvaluationLedger
    from spireagent.workbench.managed_pipeline import PipelinedManagedExecutor
    from spireagent.workbench.reference_suite import SuiteLimits

    root, plan = path.parent, load_plan(path)
    if (root / "run.intent.json").exists() or (root / "result.json").exists():
        raise ValueError("original_collection_requires_reconciliation_no_retry")
    save(root / "run.intent.json", {"at": time.time(), "plan_sha256": hashed(path), "pid": os.getpid()})
    started = time.time()
    deadline = min(started + plan["wall_seconds"], plan["not_after"])
    stopping, signals, results = False, [], []

    def stop(reason: str) -> None:
        nonlocal stopping
        stopping = True
        if not (root / "STOP").exists():
            save(root / "STOP", {"at": time.time(), "reason": reason})
        for group in plan["groups"]:
            (Path(group["ledger"]) / "STOP").touch(exist_ok=True)

    def on_signal(number: int, _frame: Any) -> None:
        signals.append(number)
        stop("signal:" + str(number))

    previous = {n: signal.signal(n, on_signal) for n in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT)}
    executor, error = None, None
    try:
        executor = PipelinedManagedExecutor(bound(plan["execution"]),
            episode_seconds=plan["episode_seconds"], cleanup_seconds=plan["cleanup_seconds"])

        def run_one(group: dict[str, Any]) -> dict[str, Any]:
            directory = Path(group["ledger"])
            required = 150 + plan["episode_seconds"] + plan["cleanup_seconds"] + 180
            if stopping or (root / "STOP").exists() or deadline - time.time() < required:
                return {"ordinal": group["ordinal"], "seed": group["seed"], "status": "not_started",
                        "reason": "stop_or_insufficient_original_budget"}
            try:
                with instance_lock(directory.parent / "ledger.lock"):
                    ledger = EvaluationLedger(directory)
                    result = ledger.run_managed(max_episodes=1, workers=1,
                        limits=SuiteLimits(150, plan["episode_seconds"], plan["cleanup_seconds"],
                            max(1, deadline - time.time()), 1, 30), executor=executor)
                    slots = ledger.slots()
                    if result.get("status") == "stopped":
                        stop("normal_ledger_stop:" + str(result.get("stop_reason")))
                    for row in slots:
                        saved = (row["status"] == "saved" and row.get("execution_status") == "finished"
                            and row.get("score_eligible") is False and row.get("trajectory_shard_id"))
                        if (row.get("cleanup_accepted") is not True
                                or not saved and row["status"] not in ("unknown", "censored")):
                            stop("normal_slot_requires_owner_read")
                    save(directory.parent / "normal-result.json", result)
                    save(directory.parent / "normal-report.json", ledger.report())
                    return {"ordinal": group["ordinal"], "seed": group["seed"],
                            "normal_result": result, "slots": slots}
            except Exception as exc:  # noqa: BLE001 -- stop and retain classified per-ledger failure
                stop("normal_ledger_exception")
                return {"ordinal": group["ordinal"], "seed": group["seed"],
                        "error_type": type(exc).__name__, "error_code": getattr(exc, "code", None)}

        with ThreadPoolExecutor(max_workers=plan["workers"]) as pool:
            for future in as_completed([pool.submit(run_one, group) for group in plan["groups"]]):
                result = future.result()
                results.append(result)
                print(json.dumps({"completed_ledgers": len(results), "ordinal": result["ordinal"],
                                  "statuses": [s["status"] for s in result.get("slots", [])]}), flush=True)
    except BaseException as exc:  # noqa: BLE001 -- preserve all completed work and restore handlers
        error = {"error_type": type(exc).__name__, "error_code": getattr(exc, "code", None)}
        stop("supervisor_exception")
    finally:
        if executor is not None:
            try:
                executor.close()
            except Exception as exc:  # noqa: BLE001 -- preserve close failure with original denominator
                error = error or {"error_type": type(exc).__name__,
                                  "error_code": getattr(exc, "code", None)}
        for number, handler in previous.items():
            signal.signal(number, handler)
        result = {"schema": "rl3/current-policy-critic-collection-result-1", "started_at": started,
            "finished_at": time.time(), "plan_sha256": hashed(path), "planned": plan["selected_limit"],
            "workers": plan["workers"], "stopped": stopping, "signals": signals, "error": error,
            "groups": sorted(results, key=lambda g: g["ordinal"]), "unknown_retried": False,
            "exchange_announcements": 0, "optimizer_updates": 0}
        save(root / "result.json", result)
    return result


def submit(path: Path, constraint: str | None = None) -> dict[str, Any]:
    import evaluate

    root, plan = path.parent, load_plan(path)
    with evaluate.scheduler.user_submission_lock():
        if any((root / n).exists() for n in ("submission.intent.json", "run.intent.json", "STOP")):
            raise ValueError("existing_value_validation_requires_owner_reconciliation")
        idle = sum(int(s.split("/")[1]) for s in evaluate.scheduler.output(
            ["sinfo", "-h", "-p", "smp", "-o", "%C"]).splitlines())
        queued = len(evaluate.scheduler.output(["squeue", "-h", "-p", "smp", "-t", "PD",
                                                "-o", "%i"]).splitlines())
        if (evaluate.capacity(idle, queued, plan["workers"]) < plan["workers"]
                or time.time() + 150 + plan["episode_seconds"] + 300 > plan["not_after"]):
            raise ValueError("insufficient_fair_capacity_or_original_window")
        command = ["sbatch", "--parsable", "--partition=smp", "--nodes=1", "--ntasks=1",
            f"--cpus-per-task={plan['workers']}", f"--mem={4 * plan['workers']}G",
            f"--time={math.ceil((plan['wall_seconds'] + 600) / 60)}", "--no-requeue",
            "--signal=B:USR1@600", "--job-name=spireagent-value-validation",
            f"--output={root}/job-%j.out", f"--error={root}/job-%j.err"]
        if constraint:
            command.append("--constraint=" + constraint)
        command.append(str(root / "launch.sh"))
        receipt = {"at": time.time(), "command": command, "plan_sha256": hashed(path),
                   "launch_sha256": hashed(root / "launch.sh")}
        save(root / "submission.intent.json", receipt)
        receipt.update(job_id=evaluate.scheduler.output(command).split(";")[0],
                       submitted_at=time.time(), submitted_once=True)
        save(root / "submission-receipt.json", receipt)
    return receipt


def score(path: Path, output: Path) -> dict[str, Any]:
    import torch
    from spireagent.rl_exchange import Exchange
    from spireagent.workbench.evaluation_ledger import EvaluationLedger
    from spireagent.workbench.managed_pipeline import verify_unscored_save
    from spireagent.workbench.managed_trajectory_store import read_shard
    from stpd.evaluation.value_quality import summarize
    from stpd.p1.export import input_spec, load_model, runtime_projection
    from stpd.p1.input import project
    from stpd.rl.checkpoint import learner_state
    from stpd.rl.critic import enable_value_head, held_out, terminal_targets
    from stpd.rl.reward import REWARD_SPEC, episode_reward
    from stpd.workers.checkpoint_codec import decode_checkpoint

    plan = load_plan(path)
    point = bound(plan["point"])
    execution = bound(plan["execution"])
    original = json.loads((path.parent / "result.json").read_bytes())
    if original["plan_sha256"] != hashed(path):
        raise ValueError("normal_collection_plan_binding_mismatch")
    checkpoint = Path(point["checkpoint_file"])
    if hashed(checkpoint) != point["checkpoint_payload_sha256"]:
        raise ValueError("critic_checkpoint_payload_changed")
    state = decode_checkpoint(checkpoint.read_bytes())
    saved = learner_state(state)
    binding = point["normal_full_CP_binding"]
    if (saved["updates"] != point["model"]["training"]["updates"]
            or saved["version"] != point["model"]["version"]
            or binding["model_artifact_id"] != point["model"]["model_artifact_id"]
            or binding["model_sha256"] != point["model"]["model_sha256"]):
        raise ValueError("same_frozen_policy_critic_required")
    torch.set_num_threads(4)
    model, _ = load_model(Path(point["package"]) / "model.tensor-tree", point["model"]["model_sha256"])
    enable_value_head(model, point["learner_config"]["seed"],
                      saved["critic"]["config"].get("head_kind", "linear"))
    model.load_state_dict(state["weights"])
    model.eval()
    projection = runtime_projection(model.vocabulary)
    spec = input_spec(model.vocabulary, projection)
    exchange = Exchange(point["learner_config"]["peer"], point["learner_config"]["run"])
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    games: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    score_began = time.monotonic()
    gamma = saved["critic"]["config"]["gamma"]
    for group in plan["groups"]:
        ledger = EvaluationLedger(Path(group["ledger"]))
        slots = ledger.slots()
        if len(slots) != 1 or slots[0]["seed"] != group["seed"] or not held_out(group["seed"]):
            raise ValueError("fixed_single_slot_permanent_holdout_required")
        slot = slots[0]
        if (slot["status"] != "saved" or slot.get("cleanup_accepted") is not True
                or slot.get("score_eligible") is not False or not slot.get("trajectory_shard_id")):
            excluded.append({"ordinal": group["ordinal"], "seed": group["seed"],
                **{k: slot.get(k) for k in ("status", "reason", "execution_reason", "run_id")}})
            continue
        episodes = list(read_shard(exchange.store, slot["trajectory_shard_id"], scratch=output))
        matches = [e for e in episodes if e["run_id"] == slot["run_id"]]
        if len(matches) != 1:
            raise ValueError("normal_trajectory_run_binding_mismatch")
        episode = matches[0]
        identity = episode["identity"]
        attempt = bound(slot["report"])
        original_rows = [row for row in attempt["episodes"] if row.get("run_id") == slot["run_id"]]
        if len(original_rows) != 1:
            raise ValueError("normal_attempt_run_binding_required")
        original_row = original_rows[0]
        verify_unscored_save(original_row, Path(slot["report"]["path"]))
        sampling = {**point["behavior"], "seed": original_row["intent_id"]}
        if (identity["seed"] != group["seed"] or identity["model_artifact_id"] != binding["model_artifact_id"]
                or identity["model_sha256"] != binding["model_sha256"] or identity["input_spec"] != spec
                or any(identity[k] != execution[k]
                       for k in ("producer", "host_pin", "runtime_pin"))
                or identity["initial_progress_sha256"]
                    != execution["initial_progress"]["progress_sha256"]
                or identity["behavior_policy"] != sampling
                or original_row.get("behavior_policy") != sampling):
            raise ValueError("frozen_trajectory_model_behavior_protocol_required")
        reward, disposition = episode_reward(episode)
        if reward is None:
            excluded.append({"ordinal": group["ordinal"], "seed": group["seed"],
                             "status": disposition, "run_id": episode["run_id"]})
            continue
        frames = [project(episode["observations"][s["observation_index"]]["observation"],
                          input_projection=projection) for s in episode["steps"]]
        values = []
        with torch.no_grad():
            for start in range(0, len(frames), 8):
                values.extend(model.forward_values(frames[start:start + 8])[:, 1].cpu().tolist())
        targets = terminal_targets(reward, len(values), gamma)
        game = {"run_id": episode["run_id"], "seed": group["seed"],
            "floor": episode["outcome"]["max_observed_floor"], "result": disposition,
            "predictions": values, "targets": targets}
        games.append(game)
        print(json.dumps({"stage": "current_policy_value_scored", "games": len(games),
            "rows": len(values), "wall_seconds": time.monotonic() - score_began}), flush=True)
        predictions.extend({"run_id": game["run_id"], "seed": game["seed"], "step": i,
            "prediction": p, "target": t} for i, (p, t) in enumerate(zip(values, targets, strict=True)))
    report = {"schema": "rl3/current-policy-value-report-1", "point": point,
        "plan_sha256": hashed(path), "collection_result_sha256": hashed(path.parent / "result.json"),
        "planned": plan["selected_limit"], "verified_natural_games": len(games), "excluded": excluded,
        "reward_spec": REWARD_SPEC, "gamma": gamma, "targets": "terminal Monte Carlo; no lambda/bootstrap",
        "collection_complete": (not original.get("error")
            and len(original["groups"]) == plan["selected_limit"]
            and all(len(g.get("slots", [])) == 1 and g["slots"][0]["status"]
                in ("saved", "unknown", "censored") and g["slots"][0].get("cleanup_accepted") is True
                for g in original["groups"])),
        "metrics": summarize(games) if games else None, "scoring_device": "CPU FP32; microbatch8; no optimizer",
        "scope": "one frozen model/critic, training-equivalent sampling, permanent holdouts; Managed only"}
    save(output / "report.json", report)
    with (output / "predictions.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["run_id", "seed", "step", "prediction", "target"])
        writer.writeheader()
        writer.writerows(predictions)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    for flag in ("root", "learner-config", "execution-template", "runtime-source"):
        p.add_argument("--" + flag, type=Path, required=True)
    p.add_argument("--seed-plan", type=Path)
    p.add_argument("--count", type=int, default=48)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--wall-seconds", type=int, default=2700)
    p.add_argument("--episode-seconds", type=int, default=900)
    p = commands.add_parser("prepare-execution")
    p.add_argument("--root", type=Path, required=True)
    for name in ("collect", "submit", "score"):
        p = commands.add_parser(name)
        p.add_argument("--plan", type=Path, required=True)
        if name == "submit":
            p.add_argument("--constraint")
        if name == "score":
            p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.root.resolve(), args.learner_config, args.execution_template,
            args.runtime_source.resolve(), count=args.count, workers=args.workers,
            wall_seconds=args.wall_seconds, episode_seconds=args.episode_seconds, seeds_path=args.seed_plan)
    elif args.command == "prepare-execution":
        result = prepare_execution(args.root.resolve())
    elif args.command == "collect":
        result = collect(args.plan.resolve())
    elif args.command == "submit":
        result = submit(args.plan.resolve(), args.constraint)
    else:
        result = score(args.plan.resolve(), args.output.resolve())
    print(json.dumps({k: result.get(k) for k in ("schema", "point_id", "planned",
        "selected_limit", "job_id", "verified_natural_games", "error") if k in result}), flush=True)
    if args.command == "collect" and result.get("error"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
