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
- RL3 registered 100-seed suite (the RL4.2 final-evaluation seeds, PLAN v11), argmax,
  16 workers (100 games in about 1.5 h of play plus the snapshot; 4 workers could not
  finish 100 games inside the wall), 3 h job wall (OPS-2), 1800 s per game, unknowns
  continue cleaned and are excluded per pair, never voiding the round;
- after the evaluation job ends, the owner's report (`stpd.evaluation.rl_observation`:
  per-floor distribution, max floor, Boss counts, wins, mean reward, paired deltas)
  is written next to it against `--baseline-plan` (and the previous evaluation) when
  their observed protocol and seed manifest match; a different protocol is skipped
  and named, never compared.

`--checkpoint ID` evaluates exactly that CP once (also the baseline: the same command
on the baseline CP gives the comparable reference); `--once` then waits for its report.

Managed games need a GAME-SLOT line (working rule 5): `run` refuses to submit
without `--slot-registered`; `--dry-run` writes everything and submits nothing.
One loop per `--output` (an OS lock); state and logs stay there; no CP, model
or R2 object is deleted here. Storage without R2 (lead 11-12): the learner peer
is a local shared-disk directory and the execution file names
`"storage_mode": "shared_local"` (`evaluate.validate_execution`).

CP pruning contract: `<output>/protected-checkpoints.json` lists the CPs whose
preparation may still read them (`{"checkpoints": [...]}`, rewritten each tick).
The run's CP pruning must keep those; after preparation the evaluation reads the
published detached model, not the CP.
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
WORKERS = 16  # RL4.2 final eval: 25 games on 8 workers took 1,700-2,450 s of play
PLAN_GRACE_SECONDS = 300  # shared disk: a job's last write can trail its end (#403)
RESULT_GRACE_SECONDS = 900  # #398: an ended evaluation job without a result
PREPARE = {"cpus": 1, "minutes": 20, "memory_gb": 6}
LIVE_STATES = "PD,R,CG,CF,S,RQ,RS"  # queued or running; a purged id is simply absent
SPLIT = 'exec "$actor_root/env/bin/python"'
WAITING = ("preparing", "submitted")
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


def check_manifest(path: Path, suite: str | None = None, seeds: int = SEEDS) -> None:
    """The frozen manifest is the named suite (default: the fixed A0 100) with enough seeds.

    One suite per watcher; an A10 suite runs as its own watcher and is reported apart.
    """
    from spireagent.workbench.reference_suite import load_manifest
    from stpd.evaluation.suites import RL3_LEARNING_CURVE_SUITE_ID

    _, frozen = load_manifest(path, hashed(path))
    if frozen["suite_id"] != (suite or RL3_LEARNING_CURVE_SUITE_ID) or len(frozen["seeds"]) < seeds:
        raise ValueError("named_frozen_evaluation_suite_manifest_required")


def prepare_script(settings: dict[str, Any], checkpoint: str, root: Path) -> str:
    """The watcher's preparation command, for one CP (no R2 environment when shared_local)."""
    launcher = evaluate.launcher(root / "not-offered-plan.json",
                                 {"source_root": settings["runtime_source"]},
                                 credentials=settings.get("r2", True))
    if launcher.count(SPLIT) != 1:
        raise ValueError("evaluation_launcher_layout_changed")
    prefix = launcher.split(SPLIT, 1)[0]
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
    """The recorded ids still queued or running: this user's live jobs, intersected.

    `squeue -j <id>` fails once an id is purged and lists finished jobs within
    MinJobAge; the user's live list has neither problem. A failing squeue raises
    (the caller skips the tick, never guesses).
    """
    if not job_ids:
        return set()
    import submit as scheduler

    out = scheduler.output(["squeue", "-h", "-u", scheduler.username(), "-t", LIVE_STATES,
                            "-o", "%i"])
    live = {line.strip().split("_")[0] for line in out.splitlines() if line.strip()}
    return live & set(job_ids)


class Loop:
    def __init__(self, settings: dict[str, Any], output: Path, *, dry_run: bool,
                 clock: Callable[[], float] = time.time,
                 newest: Callable[[], dict[str, Any] | None] | None = None,
                 run_command: Callable[[list[str]], str] | None = None,
                 jobs: Callable[[list[str]], set[str]] = live_jobs,
                 submit: Callable[..., dict[str, Any]] = evaluate.submit) -> None:
        self.settings, self.output, self.dry_run, self.clock = settings, output, dry_run, clock
        fixed = settings.get("checkpoint")
        self.newest = newest or ((lambda: {"checkpoint_artifact_id": fixed, "published_at": None})
                                 if fixed else (lambda: newest_checkpoint(
            settings["peer"], settings["run"], settings.get("training_generation"))))
        self.run_command = run_command or (lambda c: subprocess.check_output(c, text=True)
                                           .strip())
        self.jobs, self.submit = jobs, submit
        self.state_path = output / "state.json"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_bytes())
            if self.state["schema"] != SCHEMA or self.state["settings"] != settings:
                raise ValueError("fast_evaluation_settings_changed_use_new_output")
            self.reconcile()
        else:
            self.state = {"schema": SCHEMA, "settings": settings, "next_due": clock(),
                          "evaluations": [], "log": []}

    def reconcile(self) -> None:
        """After a crash between sbatch and persist: the receipt names the job, if any."""
        for item in self.state["evaluations"]:
            if item["stage"] != "preparing" or "prepare_job" in item:
                continue
            receipt = Path(item["root"]) / "submission.json"
            if receipt.is_file():
                item["prepare_job"] = json.loads(receipt.read_bytes())["job_id"]
            else:  # sbatch may or may not have run: never submitted again
                item["stage"] = "prepare_submit_unknown"
                self.note("evaluation_prepare_submit_unknown", checkpoint=item["checkpoint"],
                          reason="restart_without_receipt")

    def protected(self) -> list[str]:
        return [e["checkpoint"] for e in self.state["evaluations"] if e["stage"] == "preparing"]

    def persist(self) -> None:
        if not self.dry_run:
            save(self.state_path, self.state)
            save(self.output / "protected-checkpoints.json",
                 {"schema": SCHEMA + "/protected", "checkpoints": self.protected()})

    def live(self, ids: list[str]) -> set[str] | None:
        try:
            return self.jobs(ids)
        except (OSError, subprocess.SubprocessError) as error:
            self.note("scheduler_unreachable", reason=type(error).__name__)
            return None

    def note(self, kind: str, **fields: Any) -> None:
        entry = {"at": self.clock(), "kind": kind, **fields}
        self.state["log"].append(entry)
        print(json.dumps(entry, sort_keys=True), flush=True)

    def tick(self) -> None:
        if self.advance() is False:  # liveness unknown: nothing decided this tick
            self.persist()
            return
        if self.clock() >= self.state["next_due"]:
            self.state["next_due"] = self.clock() + self.settings["interval"]
            self.start()
        self.persist()

    def advance(self) -> bool:
        """Preparation finished: submit its plan once; a dead job without a plan fails.

        A finished evaluation job gets its report; one that ended without a result
        (after the shared-disk grace) is recorded, never resubmitted.
        """
        preparing = [e for e in self.state["evaluations"] if e["stage"] == "preparing"]
        submitted = [e for e in self.state["evaluations"]
                     if e["stage"] == "submitted" and e.get("evaluation_job")]
        live = self.live([e["prepare_job"] for e in preparing]
                         + [e["evaluation_job"] for e in submitted])
        if live is None:
            return False
        for item in submitted:
            if item["evaluation_job"] in live:
                continue
            if not (Path(item["root"]) / "round/evaluation/result.json").is_file():
                ended = item.setdefault("ended_seen_at", self.clock())
                if self.clock() - ended >= RESULT_GRACE_SECONDS:
                    item["stage"] = "evaluation_ended_without_result"
                    self.note("evaluation_ended_without_result", checkpoint=item["checkpoint"],
                              job=item["evaluation_job"])
                continue
            self.report(item)
        for item in preparing:
            plan = Path(item["root"]) / "round/evaluation/plan.json"
            if item["prepare_job"] in live:
                continue
            if not plan.is_file():
                ended = item.setdefault("ended_seen_at", self.clock())
                if self.clock() - ended < PLAN_GRACE_SECONDS:
                    continue
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
            except (OSError, subprocess.SubprocessError) as error:
                # Delivery unknown (the owner's intent may exist): recorded, never retried.
                item.update(stage="submit_unknown", reason=type(error).__name__)
                self.note("evaluation_submit_unknown", checkpoint=item["checkpoint"],
                          reason=type(error).__name__)
                continue
            item.update(stage="submitted", evaluation_job=result.get("job_id"),
                        submitted_at=self.clock())
            self.note("evaluation_submitted", checkpoint=item["checkpoint"],
                      job=result.get("job_id"))
        return True

    def report(self, item: dict[str, Any]) -> None:
        previous = [e for e in self.state["evaluations"]
                    if e["stage"] == "reported" and e["number"] < item["number"]]
        try:
            summary = write_evaluation_report(
                Path(item["root"]), self.settings.get("baseline_plan"),
                str(Path(previous[-1]["root"]) / "round/evaluation/plan.json")
                if previous else None)
            self.add_watcher_facts(item)
        except Exception as error:  # noqa: BLE001 -- originals stay; the report is a projection
            item.update(stage="report_failed", reason=f"{type(error).__name__}: {error}")
            self.note("evaluation_report_failed", checkpoint=item["checkpoint"],
                      reason=item["reason"])
            return
        item.update(stage="reported", summary=summary)
        current = summary["cohorts"]["current"]
        self.note("evaluation_reported", checkpoint=item["checkpoint"],
                  report=summary["report_directory"], scored=current["scored"],
                  wins=current["wins"], mean_floor=current["mean_floor"],
                  reward=current["reward_mean"], conservative=current["conservative"])

    def add_watcher_facts(self, item: dict[str, Any]) -> None:
        """Adds the watcher's own facts and times to the round's `eval-record.json`."""
        path = Path(item["root"]) / "eval-record.json"
        record = json.loads(path.read_bytes())
        preparing = next((e["at"] for e in self.state["log"] if e["kind"] ==
                          "evaluation_preparing" and e.get("checkpoint") == item["checkpoint"]),
                         None)
        record["watcher"] = {"checkpoint": item["checkpoint"], "evaluation": item["number"],
                             "checkpoint_record": item.get("record"),
                             "prepare_job": item.get("prepare_job"),
                             "evaluation_job": item.get("evaluation_job")}
        record["stage_seconds"].update(
            prepare_wall=_span(preparing, item.get("submitted_at")),
            evaluation_queue=_span(item.get("submitted_at"), record.get("process_started_at")))
        save(path, record)

    def start(self) -> None:
        deadline = self.settings.get("deadline_at")
        if deadline is not None and self.clock() >= deadline - self.settings["wall_seconds"]:
            return self.note("evaluation_skipped", reason="absolute_deadline_headroom")
        evaluations = self.state["evaluations"]
        if any(e["stage"] == "preparing" for e in evaluations):
            return self.note("evaluation_skipped", reason="preparation_still_running")
        submitted = [e["evaluation_job"] for e in evaluations
                     if e["stage"] == "submitted" and e.get("evaluation_job")]
        running = self.live(submitted)
        if running is None:
            return self.note("evaluation_skipped", reason="scheduler_unreachable")
        if len(running) >= self.settings["max_live"]:
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
        intent = root / "submission-intent.json"
        if intent.exists():  # crash after the intent, before the state: never resubmitted
            evaluations.append({"number": number, "checkpoint": None, "root": str(root),
                                "stage": "orphan_intent"})
            return self.note("evaluation_skipped", reason="orphan_submission_intent",
                             root=str(root))
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
        self.persist()  # the job id is durable before anything else can fail
        self.note("evaluation_preparing", checkpoint=checkpoint, job=item["prepare_job"],
                  sequence=record.get("checkpoint_sequence"))
        return None


def write_evaluation_report(root: Path, baseline_plan: str | None,
                            previous_plan: str | None) -> dict[str, Any]:
    """The owner's report for one finished evaluation round (`<root>/report`).

    Reads only the saved, hash-bound observation files of each round
    (`evaluate.read_observation_report`). A reference with another observed protocol
    (Host/Runtime/input/progress pins, policy) or another seed manifest is skipped
    and named, never compared. Unknown/failed games stay unscored rows (excluded per
    pair, listed with the floor they reached); the round is never voided. Beside it,
    `<root>/eval-record.json` (`evaluation_record`; the watcher adds its own times).
    """
    from stpd.evaluation.rl_observation import write_report

    began = time.time()
    def cohort(plan: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        frozen, _, files = evaluate.read_observation_report(plan.parent / "result.json", plan)
        return files["cohort"], frozen

    current, plan = cohort(root / "round/evaluation/plan.json")
    cohorts: dict[str, dict[str, Any]] = {}
    skipped: dict[str, Any] = {}
    for name, reference in (("baseline", baseline_plan), ("previous", previous_plan)):
        if reference is None:
            continue
        group, frozen = cohort(Path(reference))
        old, new = group["protocol_observed"], current["protocol_observed"]
        keys = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
        keys += [k for k in ("host_kind", "character", "ascension") if group[k] != current[k]]
        if frozen["manifest"]["sha256"] != plan["manifest"]["sha256"]:
            keys.append("seed_manifest")
        if keys:
            skipped[name] = {"plan": reference, "differing": keys}
            continue
        cohorts[name] = group
    cohorts["current"] = current
    output = root / "report"
    report = write_report(cohorts, output, scope_note=(
        "Managed fixed pool, argmax; unknown/failed games are excluded per pair and listed, "
        "never retried; the round is never voided."
        + (" Skipped references (different protocol or seeds): "
           + json.dumps(skipped, sort_keys=True) if skipped else "")))
    summary: dict[str, Any] = {"schema": SCHEMA + "/report", "report_directory": str(output),
                               "skipped_references": skipped, "cohorts": {}, "paired": {}}
    for name, group in cohorts.items():
        bosses = {b["act"]: b for b in report["progress"][name]["bosses"]}
        reward = report["rewards"][name]
        summary["cohorts"][name] = {
            "planned": group["planned"], "scored": group["floor_n"], "wins": group["wins"],
            "mean_floor": group["mean_floor"], "median_floor": group["median_floor"],
            "max_floor": group["max_floor"],
            "act1_boss_cleared": bosses[1]["cleared_witnessed"],
            "act2_boss_cleared": bosses[2]["cleared_witnessed"],
            "act3_boss_reached": bosses[3]["reached_witnessed"],
            "loss_floor_counts": group["loss_floor_counts"],
            "reward_mean": reward.get("mean"),
            "conservative": (reward.get("conservative") or {}).get("score"),
            "excluded": reward.get("excluded_rows", [])}
    for name in cohorts:
        if name == "current":
            continue
        floor, reward = report["comparisons"][name], report["reward_comparisons"][name]
        summary["paired"][name] = {
            "floor_pairs": floor["paired_n"], "counts": floor["counts"],
            "floor_delta": floor["mean_delta"],
            "floor_bootstrap95": floor["bootstrap95_fixed_seed_sensitivity"],
            "reward": {k: reward["official"].get(k) for k in (
                "paired_n", "better", "same", "worse", "mean_delta",
                "bootstrap95_fixed_seed_sensitivity")},
            "conservative_reward_delta": (reward.get("conservative_paired") or {}).get(
                "mean_delta")}
    save(output / "summary.json", summary)
    save(root / "eval-record.json", evaluation_record(
        root, plan, current, report, summary, time.time() - began))
    return summary


RECORD_SCHEMA = "spireagent/friday-fast-evaluation-record-1"


def _span(start: Any, end: Any) -> float | None:
    return end - start if isinstance(start, (int, float)) and isinstance(
        end, (int, float)) else None


def evaluation_record(root: Path, plan: dict[str, Any], current: dict[str, Any],
                      report: dict[str, Any], summary: dict[str, Any],
                      report_seconds: float) -> dict[str, Any]:
    """One round's record: identity, live games, stage seconds, per-seed outcomes, paired.

    Stage seconds: the job's own phases from result.json (execution_and_pipeline_save =
    play + save, observation_report, snapshot, ...), `report` (this report) and
    `ended_to_report` (job finished -> report written: the watcher's polling and grace);
    `Loop.add_watcher_facts` adds `prepare_wall` and `evaluation_queue`. Per-step value/HP
    predictions are not in the trajectories: the evaluated package strips the auxiliary
    heads, so `value_predictions` stays unrecorded (the heads remain in round/export).
    """
    result_path = root / "round/evaluation/result.json"
    result = json.loads(result_path.read_bytes()) if result_path.is_file() else {}
    timing = result.get("timing") or {}
    now = time.time()
    stages: dict[str, Any] = {
        **(timing.get("phases") or {}),
        "evaluation_process_wall": timing.get("process_wall_seconds"),
        "report": report_seconds,
        "ended_to_report": _span(timing.get("process_finished_at"), now)}
    parallelism = plan.get("parallelism") or {}
    rewards = report["rewards"]["current"]
    official, conservative = rewards.get("by_seed", {}), rewards.get("conservative_by_seed", {})
    return {
        "schema": RECORD_SCHEMA, "written_at": now,
        "process_started_at": timing.get("process_started_at"),
        "suite": {"manifest_sha256": plan["manifest"]["sha256"], "planned": plan.get("planned"),
                  "selections": plan.get("selections"), "character": current["character"],
                  "ascension": current["ascension"], "host_kind": current["host_kind"],
                  "protocol_observed": current["protocol_observed"]},
        "policy": "argmax",
        "parallelism": {"workers": plan.get("workers"),
                        "live_games": parallelism.get("execution_upper_bound"),
                        "pending_limit": parallelism.get("pending_limit"),
                        "save_workers": parallelism.get("save_workers"),
                        "wall_seconds": plan.get("wall_seconds"),
                        "episode_seconds": plan.get("episode_seconds")},
        "stage_seconds": stages,
        "wall_used_fraction": (timing["process_wall_seconds"] / plan["wall_seconds"]
                               if timing.get("process_wall_seconds") and plan.get("wall_seconds")
                               else None),
        "status": result.get("status"), "stop_requested": result.get("stop_requested"),
        "outcome": summary["cohorts"]["current"],
        "seeds": [{"seed": row["seed"], "status": row["status"], "result": row["result"],
                   "scored": row["scored"], "floor": row["floor"],
                   "boss_cleared_acts": row["boss_cleared_acts"],
                   "elapsed_seconds": row["elapsed_seconds"],
                   "reward": official.get(row["seed"]),
                   "conservative_reward": conservative.get(row["seed"])}
                  for row in current["rows"]],
        "paired": summary["paired"], "skipped_references": summary["skipped_references"],
        "value_predictions": {"recorded": False,
                              "reason": "evaluated_package_strips_auxiliary_heads"}}


def check_storage(execution: dict[str, Any], peer: dict[str, Any]) -> bool:
    """True when the evaluation jobs need the R2 environment (not shared_local)."""
    if not evaluate.needs_r2(execution) and (
            set(peer) != {"kind", "directory"} or peer.get("kind") != "local"):
        raise ValueError("shared_local_evaluation_requires_a_local_peer_without_archive")
    return evaluate.needs_r2(execution)


def settings_from(args: argparse.Namespace) -> dict[str, Any]:
    config = json.loads(args.learner_config.read_bytes())
    execution = json.loads(args.execution.read_bytes())
    return {"learner_config": str(args.learner_config.resolve()),
            "peer": config["peer"], "run": config["run"],
            "training_generation": args.training_generation,
            "execution": str(args.execution.resolve()), "manifest": str(args.manifest.resolve()),
            "runtime_source": str(Path(__file__).resolve().parents[2]),
            "workers": args.workers, "seeds": args.seeds, "wall_seconds": args.wall_seconds,
            "episode_seconds": args.episode_seconds, "interval": args.interval,
            "max_live": args.max_live, "partition": args.partition,
            "prepare_partition": args.prepare_partition,
            "exclude_nodes": args.exclude_node or [], "deadline_at": args.deadline_at,
            "checkpoint": args.checkpoint, "r2": check_storage(execution, config["peer"]),
            "baseline_plan": str(args.baseline_plan.resolve()) if args.baseline_plan else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--learner-config", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="evaluate.py prepare-manifest output of --suite")
    parser.add_argument("--suite", help="frozen suite id (default: the fixed A0 100 seeds)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-generation")
    parser.add_argument("--interval", type=int, default=INTERVAL_SECONDS)
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--seeds", type=int, default=SEEDS)
    parser.add_argument("--wall-seconds", type=int, default=WALL_SECONDS)
    parser.add_argument("--episode-seconds", type=int, default=EPISODE_SECONDS)
    parser.add_argument("--max-live", type=int, default=MAX_LIVE)
    parser.add_argument("--partition", choices=("smp", "general", "debug"), default="general")
    parser.add_argument("--prepare-partition", choices=("smp", "general", "debug"),
                        default="debug")
    parser.add_argument("--exclude-node", action="append")
    parser.add_argument("--deadline-at", type=float)
    parser.add_argument("--checkpoint",
                        help="evaluate exactly this CP artifact id (else the newest CP)")
    parser.add_argument("--baseline-plan", type=Path,
                        help="plan.json of the reference round (e.g. the baseline CP's own "
                             "fast_eval round) for the paired report")
    parser.add_argument("--once", action="store_true",
                        help="one evaluation, then exit once its report is written "
                             "(or it ended without one)")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--slot-registered", action="store_true",
                        help="a GAME-SLOT line for these Managed games is registered")
    args = parser.parse_args()
    if not args.dry_run and not args.slot_registered:
        parser.error("register a GAME-SLOT line first (working rule 5), then --slot-registered")
    if not 600 <= args.interval <= 86400 or not 1 <= args.max_live <= 4:
        parser.error("interval 600..86400 s, max-live 1..4")
    check_manifest(args.manifest.resolve(), args.suite, args.seeds)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    import contextlib

    from spireagent.instance_lock import instance_lock
    from spireagent.json_boundary import BoundaryError

    with contextlib.ExitStack() as stack:
        try:  # one loop per output, OS-held (released on process death)
            stack.enter_context(instance_lock(output / "loop.lock"))
        except BoundaryError:
            parser.error("another fast_eval loop owns this --output")
        loop = Loop(settings_from(args), output, dry_run=args.dry_run)
        while True:
            loop.tick()
            stages = [e["stage"] for e in loop.state["evaluations"]]
            if args.dry_run or args.once and stages and stages[-1] not in WAITING:
                break
            time.sleep(args.poll_seconds)
    print(json.dumps({"evaluations": loop.state["evaluations"]}, sort_keys=True), flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
