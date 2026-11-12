#!/usr/bin/env python3
"""Every-2-h Managed full-chain evaluation of a fast-chain (segment learner) CP.

The fast-chain harness has no evaluation schedule, and the old watcher
(`watch.py schedule_segment_evaluation`) is bound to the old chain's actor plans.
This loop reuses the same two owners and nothing else:

1. `evaluate.py prepare-checkpoint` in a small CPU job (export the CP, publish the
   detached model, plan a fixed-seed Managed evaluation), exactly the watcher's
   command and job size;
2. `evaluate.submit` for that plan once the job has written it.

Rules (RL4.1 long-run lessons, lead 11-12):

- one evaluation per `interval` (default 7200 s) of wall time, on the newest CP
  record (learner peer plus its archive; newest publication wins, as the watcher);
  no new CP since the last one: skipped, never the same CP twice;
- the training run never waits on this loop; an evaluation that cannot start
  (busy, deadline, capacity) is skipped and logged, it never stops anything;
- a refused pre-submission check (fair share) is tried again next tick; a failure
  after a submission intent is written is never retried (unknown delivery);
- RL3 registered 100-seed suite, 3 h job wall (OPS-2), 1800 s per game, unknowns
  continue cleaned (as `prepare_checkpoint` fixes).

Managed games need a GAME-SLOT line (working rule 5): `run` refuses to submit
without `--slot-registered`; `--dry-run` writes everything and submits nothing.
State and logs stay under `--output`; no CP, model or R2 object is deleted here.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import evaluate
from batch import hashed, private_root, save

SCHEMA = "spireagent/friday-fast-evaluation-v1"
INTERVAL_SECONDS = 7200
WALL_SECONDS = 10800  # OPS-2: Act-3-deep games outgrow 2 h
EPISODE_SECONDS = 1800
SEEDS = 100
MAX_LIVE = 2  # 3 h evaluations every 2 h overlap by at most one
PREPARE = {"cpus": 1, "minutes": 20, "memory_gb": 6}
RETRY_BEFORE_SUBMISSION = ("requested_evaluation_workers_exceed_current_fair_share",)


def newest_checkpoint(peer: dict[str, Any], run: str,
                      generation: str | None = None) -> dict[str, Any] | None:
    """Newest CP record in the learner peer and its archive (watcher rule)."""
    from spireagent.rl_exchange import Exchange

    records = Exchange(peer, run).checkpoint_records()
    if peer.get("archive") is not None:
        records += Exchange(peer["archive"], run).checkpoint_records()
    if generation is not None:
        records = [r for r in records if r.get("training_generation") == generation]
    return max(records, key=lambda r: r["published_at"]) if records else None


def check_manifest(path: Path) -> None:
    from spireagent.workbench.reference_suite import load_manifest
    from stpd.evaluation.suites import RL3_LEARNING_CURVE_SUITE_ID

    _, frozen = load_manifest(path, hashed(path))
    if frozen["suite_id"] != RL3_LEARNING_CURVE_SUITE_ID or len(frozen["seeds"]) != 100:
        raise ValueError("RL3_registered100_evaluation_manifest_required")


def prepare_script(settings: dict[str, Any], checkpoint: str, root: Path) -> str:
    """The watcher's preparation command, for one CP."""
    prefix = evaluate.launcher(root / "not-offered-plan.json",
                               {"source_root": settings["runtime_source"]}).split(
        'exec "$actor_root/env/bin/python"', 1)[0]
    command = ['"$actor_root/env/bin/python"', '"$source_root/tools/friday/evaluate.py"',
               "prepare-checkpoint", "--learner-config",
               shlex.quote(settings["learner_config"]), "--checkpoint", shlex.quote(checkpoint),
               "--execution", shlex.quote(settings["execution"]),
               "--manifest", shlex.quote(settings["manifest"]),
               "--output", shlex.quote(str(root / "round")),
               "--workers", str(settings["workers"]), "--seeds", str(settings["seeds"]),
               "--wall-seconds", str(settings["wall_seconds"]),
               "--episode-seconds", str(settings["episode_seconds"])]
    if settings.get("deadline_at") is not None:
        command += ["--deadline-at", str(settings["deadline_at"])]
    return prefix + "exec " + " ".join(command) + "\n"


def sbatch_prepare(script: Path, root: Path, settings: dict[str, Any]) -> list[str]:
    command = ["sbatch", "--parsable", "--partition=" + settings["prepare_partition"],
               "--nodes=1", "--ntasks=1", f"--cpus-per-task={PREPARE['cpus']}",
               f"--mem={PREPARE['memory_gb']}G", f"--time={PREPARE['minutes']}",
               "--no-requeue", "--signal=B:USR1@120", "--export=ALL",
               "--job-name=fast-eval-prepare", "--output=" + str(root / "%j.out"),
               "--error=" + str(root / "%j.err")]
    if settings.get("exclude_nodes"):
        command.append("--exclude=" + ",".join(settings["exclude_nodes"]))
    return command + [str(script)]


def live_jobs(job_ids: list[str]) -> set[str]:
    if not job_ids:
        return set()
    out = subprocess.check_output(["squeue", "-h", "-j", ",".join(job_ids), "-o", "%i"],
                                  text=True)
    return {line.strip() for line in out.splitlines() if line.strip()}


class Loop:
    def __init__(self, settings: dict[str, Any], output: Path, *, dry_run: bool,
                 clock: Callable[[], float] = time.time,
                 newest: Callable[[], dict[str, Any] | None] | None = None,
                 run_command: Callable[[list[str]], str] | None = None,
                 jobs: Callable[[list[str]], set[str]] = live_jobs,
                 submit: Callable[..., dict[str, Any]] = evaluate.submit) -> None:
        self.settings, self.output, self.dry_run, self.clock = settings, output, dry_run, clock
        self.newest = newest or (lambda: newest_checkpoint(
            settings["peer"], settings["run"], settings.get("training_generation")))
        self.run_command = run_command or (lambda c: subprocess.check_output(c, text=True)
                                           .strip())
        self.jobs, self.submit = jobs, submit
        self.state_path = output / "state.json"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_bytes())
            if self.state["schema"] != SCHEMA or self.state["settings"] != settings:
                raise ValueError("fast_evaluation_settings_changed_use_new_output")
        else:
            self.state = {"schema": SCHEMA, "settings": settings, "next_due": clock(),
                          "evaluations": [], "log": []}

    def persist(self) -> None:
        if not self.dry_run:
            save(self.state_path, self.state)

    def note(self, kind: str, **fields: Any) -> None:
        entry = {"at": self.clock(), "kind": kind, **fields}
        self.state["log"].append(entry)
        print(json.dumps(entry, sort_keys=True), flush=True)

    def tick(self) -> None:
        self.advance()
        if self.clock() >= self.state["next_due"]:
            self.state["next_due"] = self.clock() + self.settings["interval"]
            self.start()
        self.persist()

    def advance(self) -> None:
        """Preparation finished: submit its plan once; a dead job without a plan fails."""
        preparing = [e for e in self.state["evaluations"] if e["stage"] == "preparing"]
        live = self.jobs([e["prepare_job"] for e in preparing])
        for item in preparing:
            plan = Path(item["root"]) / "round/evaluation/plan.json"
            if item["prepare_job"] in live:
                continue
            if not plan.is_file():
                item["stage"] = "prepare_failed"
                self.note("evaluation_prepare_failed", checkpoint=item["checkpoint"],
                          job=item["prepare_job"])
                continue
            try:
                result = self.submit(plan, partition=self.settings["partition"],
                                     exclude_nodes=self.settings.get("exclude_nodes") or None,
                                     dry_run=self.dry_run)
            except ValueError as error:
                if str(error) in RETRY_BEFORE_SUBMISSION:  # nothing was delivered
                    self.note("evaluation_submit_deferred", reason=str(error))
                    continue
                item.update(stage="submit_failed", reason=str(error))
                self.note("evaluation_submit_failed", checkpoint=item["checkpoint"],
                          reason=str(error))
                continue
            item.update(stage="submitted", evaluation_job=result.get("job_id"),
                        submitted_at=self.clock())
            self.note("evaluation_submitted", checkpoint=item["checkpoint"],
                      job=result.get("job_id"))

    def start(self) -> None:
        deadline = self.settings.get("deadline_at")
        if deadline is not None and self.clock() >= deadline - self.settings["wall_seconds"]:
            return self.note("evaluation_skipped", reason="absolute_deadline_headroom")
        evaluations = self.state["evaluations"]
        if any(e["stage"] == "preparing" for e in evaluations):
            return self.note("evaluation_skipped", reason="preparation_still_running")
        submitted = [e["evaluation_job"] for e in evaluations
                     if e["stage"] == "submitted" and e.get("evaluation_job")]
        if len(self.jobs(submitted)) >= self.settings["max_live"]:
            return self.note("evaluation_skipped", reason="evaluations_still_running")
        record = self.newest()
        if record is None:
            return self.note("evaluation_skipped", reason="no_checkpoint")
        checkpoint = record["checkpoint_artifact_id"]
        if any(e["checkpoint"] == checkpoint for e in evaluations):
            return self.note("evaluation_skipped", reason="no_new_checkpoint",
                             checkpoint=checkpoint)
        number = len(evaluations) + 1
        root = self.output / f"evaluation-{number:04d}"
        if self.dry_run:
            root.mkdir(parents=True, exist_ok=True)
        else:
            root = private_root(root)
        script = root / "prepare.sh"
        script.write_text(prepare_script(self.settings, checkpoint, root))
        script.chmod(0o700)
        command = sbatch_prepare(script, root, self.settings)
        item = {"number": number, "checkpoint": checkpoint, "root": str(root),
                "record": record, "stage": "preparing", "command": command}
        if self.dry_run:
            item["stage"] = "dry_run"
            evaluations.append(item)
            return self.note("evaluation_dry_run", checkpoint=checkpoint, command=command)
        intent = root / "submission-intent.json"
        save(intent, {"at": self.clock(), "command": command, "script_sha256": hashed(script)})
        evaluations.append(item)
        self.persist()  # the intent exists before the job does; never resubmitted
        try:
            item["prepare_job"] = self.run_command(command).split(";")[0]
        except (OSError, subprocess.SubprocessError) as error:
            item.update(stage="prepare_submit_unknown", reason=type(error).__name__)
            self.note("evaluation_prepare_submit_unknown", checkpoint=checkpoint)
            return None
        save(root / "submission.json", {"at": self.clock(), "job_id": item["prepare_job"]})
        self.note("evaluation_preparing", checkpoint=checkpoint, job=item["prepare_job"],
                  sequence=record.get("checkpoint_sequence"))
        return None


def settings_from(args: argparse.Namespace) -> dict[str, Any]:
    config = json.loads(args.learner_config.read_bytes())
    return {"learner_config": str(args.learner_config.resolve()),
            "peer": config["peer"], "run": config["run"],
            "training_generation": args.training_generation,
            "execution": str(args.execution.resolve()), "manifest": str(args.manifest.resolve()),
            "runtime_source": str(Path(__file__).resolve().parents[2]),
            "workers": args.workers, "seeds": args.seeds, "wall_seconds": args.wall_seconds,
            "episode_seconds": args.episode_seconds, "interval": args.interval,
            "max_live": args.max_live, "partition": args.partition,
            "prepare_partition": args.prepare_partition,
            "exclude_nodes": args.exclude_node or [], "deadline_at": args.deadline_at}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--learner-config", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="evaluate.py prepare-manifest output (RL3 100 seeds)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-generation")
    parser.add_argument("--interval", type=int, default=INTERVAL_SECONDS)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seeds", type=int, default=SEEDS)
    parser.add_argument("--wall-seconds", type=int, default=WALL_SECONDS)
    parser.add_argument("--episode-seconds", type=int, default=EPISODE_SECONDS)
    parser.add_argument("--max-live", type=int, default=MAX_LIVE)
    parser.add_argument("--partition", choices=("smp", "general", "debug"), default="general")
    parser.add_argument("--prepare-partition", choices=("smp", "general", "debug"),
                        default="debug")
    parser.add_argument("--exclude-node", action="append")
    parser.add_argument("--deadline-at", type=float)
    parser.add_argument("--once", action="store_true",
                        help="one evaluation of the newest CP, then exit once submitted")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--slot-registered", action="store_true",
                        help="a GAME-SLOT line for these Managed games is registered")
    args = parser.parse_args()
    if not args.dry_run and not args.slot_registered:
        parser.error("register a GAME-SLOT line first (working rule 5), then --slot-registered")
    if not 600 <= args.interval <= 86400 or not 1 <= args.max_live <= 4:
        parser.error("interval 600..86400 s, max-live 1..4")
    check_manifest(args.manifest.resolve())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    loop = Loop(settings_from(args), output, dry_run=args.dry_run)
    while True:
        loop.tick()
        stages = [e["stage"] for e in loop.state["evaluations"]]
        if args.dry_run or args.once and stages and stages[-1] != "preparing":
            break
        time.sleep(args.poll_seconds)
    print(json.dumps({"evaluations": loop.state["evaluations"]}, sort_keys=True), flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
