#!/usr/bin/env python3
"""Resume chain of a fast-chain long run: generations, automatic resume, hourly readout,
2 h evaluations (login node; runs nothing on GPUs itself).

    supervise.py start CHAIN --site S --cards N --card-type T [--layout L] [--set k=v]...
                 [--resume-checkpoint CP] [--eval-execution E --eval-manifest M
                 [--eval-baseline-plan P | --eval-no-baseline] [--eval-a10-manifest M10]
                 --eval-slot-registered]
                 [--dry-run]
    supervise.py stop CHAIN --site S     (the operator's stop; see below)

One chain is one operating window: `long_wall_seconds` (<= 72000 s), one learner budget id
(`c-CHAIN`) and one immutable deadline, kept by the learner across restorations
(learner_config.window). Generation 0 is `launch.py submit CHAIN --long-run`; each later
generation `CHAIN-rN` is a NEW run directory and a NEW training generation, started from
the newest CP of the chain (RL4.1 long-run lessons: continuation = new dir + reconciled
state; newest CP by published_at; re-freeze on code change).

Resume rules (user 11-13 C5: automatic; lead 11-14: the simple version):
- normal = gpu.sh exited 0 (its `gpu-rc` file), the learner report has no failure and a
  window end, and STOP names no failure;
- resumable = the learner's own signal: its report failure names
  `input_owner_fenced_restore_last_checkpoint` (the product's instruction: the fenced
  owner restores its last CP; `input_failure.operation` says why, e.g. a lost hand-off);
  or the GPU job ended without any learner report, no STOP explains it, and gpu-rc says
  why (lead 11-17): absent (node death), a signal exit 129/130/143 (Slurm kill,
  preemption, time limit; before V6 attempt 2 a false 0) or 6 (the learner process died
  without its report: OOM, segfault). Any other rc without a report is final. A bare
  scancel of the GPU job is a kill and IS resumed: stop a chain only with
  `supervise.py stop`. The old generation's jobs are cancelled first;
- not resumed: any other learner failure, including a setup failure (an unrestorable CP
  writes a report too, so the chain stops at once), a "service failure" STOP alone (gpu
  preflight, GPU count, MPS, memory guard: they would repeat), a "service stop" STOP
  (operator stop, env_lost: ours; "stopped"), a "launcher gone" STOP or another gpu-rc
  without a learner report, a refused preflight or failed submit, a disk-watermark stop,
  a code/profile/site/source change since the chain started (RL4.1 lesson: re-freeze on
  code change);
- at most `resume_max_total` resumes (default 3);
- environment-side recipe at a generation boundary (I8): `chain-CHAIN/env-recipe.txt`, one
  `name=value` per line as `--set` takes it, only `ENV_RECIPE_KNOBS` (exploration ladder,
  ascension mix, K, the ladder's lowest temperature, save starts); read when a later
  generation is submitted and recorded with it. The learner recipe never changes in a chain
  (a new chain from a CP may declare changes, `declared_changes`, RL5-METHODS C).
  Another knob or a refused value ends the chain. No automatic curriculum: a generation
  boundary is a resume, not a schedule;
- the restore itself is the learner's: CP-only restore (fresh generation, in-flight input
  discarded), shard count recorded in the CP. Not covered: a CP that fails to restore is
  not skipped for an older one (the next generation fails and the chain stops), and
  evaluations already submitted are not re-checked across generations;
- a submission is never repeated: a launcher failure after jobs were submitted (unknown
  delivery) ends the chain;
- GPU queue time counts against the window (review #540: the deadline is fixed when the
  chain starts and the learner keeps it across CPs). `launch.py submit` returns once gpu.sh
  confirmed its start, so each generation records `queue_seconds` and the window left at
  that moment, prints both on stderr, and FINAL.json sums `queue_seconds_total`. Not
  chosen: opening the window at learner.ready (the learner would set the deadline and the
  supervisor would have to read it back);
- the launcher (in this process) is the GPU job's liveness owner until gpu.sh confirmed
  its start: SIGTERM/SIGHUP cancel the generation's jobs, and gpu.sh refuses on a stale
  launcher-alive (SIGKILL). After the start no signal cancels anything (lead 11-16: a
  login-node reboot or the login service's logout cleanup sends SIGTERM, nohup does not
  block it, and a 20 h run must not die of it): on SIGTERM supervise prints one loud line
  (the chain is now unsupervised; how to stop it) and exits 143; SIGHUP keeps its default
  (supervise dies) or nohup's ignore. Either way the generation and the evaluation
  watchers run on without resume, readout or new evaluations of later CPs being
  supervised. Re-attaching a supervise to a running chain is not supported (#587);
- the operator's stop is `supervise.py stop CHAIN --site S`, with or without a live
  supervise, from the chain's recorded job ids (state.json, the watchers' state.json):
  it writes `chain-CHAIN/STOP-REQUESTED` (a live supervise never resumes after it), ends
  the evaluation watchers (process groups whose pid state.json recorded, checked to still
  be fast_eval.py; run it on the login node that started the chain) and cancels their
  live Slurm jobs, writes the run's STOP ("service stop: ...", which the learner and the
  services take as their stop), waits up to `stop_grace_seconds` for the GPU job to end
  on its own, then cancels what is left (dependents first, the GPU job last;
  launch.cancel_all). It reports in `chain-CHAIN/stop-report.json`; exit 1 when a job may
  still be alive or a cancel failed;
- an environment side that is gone while the GPU job runs, before the GPU side asked it to
  leave (no DRAIN or STOP, not inside the late-join window before `env-drain-at`) and
  before the GPU side reported anything (no learner report, no `gpu-rc`: then the resume
  rules decide), for `env_lost_grace_seconds` (V6 smoke: every driver died at start and
  two cards waited for data) ends the generation with the same STOP, grace and cancel and
  the chain stops with `env_lost`, never resumed, the env array never resubmitted (0 =
  off). A driver never ends by its own planned lifetime first: preflight refuses a
  driver_warm_seconds + driver_steady_seconds shorter than the generation's wall;
- the chain ends loudly with the first cause (FINAL.json, exit 1); a normal end of the
  window exits 0.
Schedules: readout of the current generation every `readout_every_seconds` (readout.txt),
its run record (`readout.py --json` plus this chain's facts) at every checkpoint the
learner publishes, before a resume and at the chain's end (`run-record.json`, the latest;
`run-records.jsonl`, one line per write; a failed write is noted, never stops the run).
Both come from one readout.Readout per generation that reads only the new bytes each time
(dropped after an error, so the next tick starts a fresh one),
CP retention at every published CP (cp_retention.py; `checkpoint_keep_newest`, default
0 = keep every CP): the newest N, evaluation claims, generation starts and milestones stay,
the run record's `chain.checkpoint_retention` counts kept / deleted / payload bytes kept,
and the same for model publications on the peer (`model_keep_newest`, default 0 = keep
all; `chain.model_retention`),
and with --eval-*: one standing tools/friday/fast_eval.py watcher per suite (#528) on the
run's peer: the newest CP every `eval_interval_seconds`, `eval_seeds` games of
`eval_suite` (empty = the fixed A0 100), never stopping training; a second watcher on the
A10 suite when `eval_a10_enabled`. The watchers outlive the chain, so the last CP is
evaluated on their next tick; FINAL.json names their pids (stop them after that report).
Each watcher's baseline: --eval-baseline-plan (a0), else the site's `eval_baselines`
entry; --eval-no-baseline: none (e.g. the first round on a new Host pin). Every
watcher's start checks (`fast_eval.py --check`: manifest, baseline pins, settings) run
before generation 0 (and in --dry-run), and a refusal stops the chain there. A watcher
that dies later is DEAD in the readout.
--eval-execution is a template (models, retention, pipeline): the chain writes its copy
with `"storage_mode": "shared_local"` on the site's peer (no R2).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cp_retention  # noqa: E402
import launch  # noqa: E402
import readout  # noqa: E402
import run_profile  # noqa: E402

SCHEMA = "spireagent/friday-fastchain-chain-1"
RESUMABLE = ("input_owner_fenced_restore_last_checkpoint",)
# STOP first lines that explain a GPU job ending without a learner report: they repeat
# (gpu preflight, GPU count, MPS, memory guard) or the launcher is gone; never resumed.
NO_REPORT_FINAL = ("service failure", "learner failure", "launcher gone", "disk stop")
# gpu-rc values that resume a generation without a learner report (lead 11-17): a signal
# exit (gpu.sh: 128+N for HUP, INT, TERM) or 6, the learner process died without a report.
NO_REPORT_RESUMABLE_RC = (129, 130, 143, 6)
# Environment-side knobs a chain may change at a generation boundary (I8); not in the
# frozen learner recipe, checked per game by the fast env and the assembler.
ENV_RECIPE_KNOBS = ("exploration_ladder", "ascension_mix", "replay_k",
                    "sampling_temperature_min", "save_starts")
# #603: the learner keeps up to 30 s for its last dataset (finish_reserve), so a run that
# trained to its budget ends at most this short of the target
TARGET_TOLERANCE_SECONDS = 60
CHAIN_SLACK_SECONDS = 900  # deadline = start + window + slack: generation 0 ends by its wall


class Stopped(BaseException):
    """SIGTERM to supervise: not an Exception, so no `except Exception` swallows it."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(profile: Path, site: Path, overrides: list[str],
             src: dict[str, Any]) -> dict[str, Any]:
    """What a resume must not change: harness code, profile, site, overrides, source."""
    return {"harness": {name: digest(HERE / name)
                        for name in (*launch.HARNESS, "launch.py", "supervise.py",
                                     "cp_retention.py")},
            "profile": digest(profile), "site": digest(site), "overrides": list(overrides),
            "source": src}


def source_identity(site: dict[str, Any]) -> dict[str, Any]:
    src = Path(site["src_dir"])
    rev = src / "REV"
    return {**launch.checkout_facts(src),
            "rev": rev.read_text().strip() if rev.exists() else None}


def outcome(run: Path) -> dict[str, Any]:
    """How a generation ended, from its own files only (review 11-14 D1/D2).

    normal: gpu.sh exit 0 (`gpu-rc`), a learner report without failure that reached its
    window end, and no failure STOP. "stopped": our own "service stop" STOP (operator
    stop) and no learner failure, with or without a report; never resumed. resumable:
    the learner's own signal (its report failure names
    `input_owner_fenced_restore_last_checkpoint`), or no learner report, no STOP that
    explains the end (NO_REPORT_FINAL) and a `gpu-rc` of NO_REPORT_RESUMABLE_RC: absent
    (node death), a signal exit (Slurm kill, preemption, time limit) or 6 (the learner
    process died without a report: OOM, segfault; lead 11-17: worth one resume). Any other
    rc without a report (gpu.sh's own error) is final. Everything else repeats: stop.
    """
    report_path, stop, rc_path = run / "learner-report.json", run / "STOP", run / "gpu-rc"
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    failure = (report or {}).get("failure")
    disk = (run / "disk-stop").read_text().strip() if (run / "disk-stop").exists() else None
    first = stop.read_text().strip() if stop.exists() else None
    text = rc_path.read_text().strip() if rc_path.exists() else ""
    rc = int(text) if text.lstrip("-").isdigit() else None
    rc_ok = rc in NO_REPORT_RESUMABLE_RC if rc_path.exists() else True  # absent: node death
    window_end = ((report or {}).get("summary") or {}).get("window_end")
    failed_stop = (first or "").startswith(("service failure", "learner failure"))
    if disk:
        kind = "disk_stop"
    elif rc == 0 and report is not None and failure is None and window_end and not failed_stop:
        kind = "normal"
    elif failure is None and (first or "").startswith("service stop"):
        kind = "stopped"  # our own stop (operator, env_lost): never resumed
    elif (failure and any(code in failure for code in RESUMABLE)) or (
            report is None and rc_ok and not (first or "").startswith(NO_REPORT_FINAL)):
        kind = "resumable"
    else:
        kind = "failure"
    return {"kind": kind, "failure": failure, "stop": first, "disk_stop": disk,
            "gpu_rc": rc, "window_end": window_end,
            "input_failure": (report or {}).get("input_failure")}


class Chain:
    def __init__(self, args: argparse.Namespace, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 launch_main: Callable[[list[str]], int] = launch.main,
                 squeue: Callable[[list[str]], set[str] | None] | None = None,
                 scancel: Callable[[list[str]], None] | None = None,
                 checkpoints: Callable[[set[str]], list[dict[str, Any]]] | None = None,
                 spawn: Callable[[list[str], Path], Any] | None = None,
                 check: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
                 end_watcher: Callable[[int], str] | None = None) -> None:
        self.args, self.clock, self.sleep, self.launch_main = args, clock, sleep, launch_main
        self.squeue = squeue or live_jobs
        self.scancel = scancel or (lambda ids: subprocess.run(["scancel", *ids],
                                                              capture_output=True, text=True))
        self.site = run_profile.load_site(args.site)
        self.knobs = run_profile.values(run_profile.load_profile(args.profile, args.set))
        self.checkpoints = checkpoints or self.peer_checkpoints
        self.spawn = spawn or (lambda cmd, log: subprocess.Popen(
            cmd, stdout=log.open("a"), stderr=subprocess.STDOUT, start_new_session=True))
        self.check = check or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True,
                                                          timeout=600))
        self.end_watcher = end_watcher or end_watcher_group
        self.dir = Path(self.site["work_root"]) / f"chain-{args.chain}"
        self.state_path = self.dir / "state.json"
        self.eval_loops: dict[str, Any] = {}
        self.reader: readout.Readout | None = None  # the current generation's readout
        self.deleter: cp_retention.Deleter | None = None  # CP retention (cp_retention.py)
        self.retention: dict[str, Any] | None = None  # its facts for the next run record
        self.model_retention: dict[str, Any] | None = None  # the same for model publications
        self.peer: Any = None  # the peer Exchange retention reads (lazily opened)
        self.model_records: dict[tuple[str, int], dict[str, Any]] = {}
        self.models_deleted: set[tuple[str | None, int]] = set()

    # ---------------- state ----------------
    def note(self, kind: str, **fields: Any) -> None:
        entry = {"t": self.clock(), "kind": kind, **fields}
        self.state["log"].append(entry)
        print(json.dumps(entry, default=str), flush=True)
        self.persist()

    def persist(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        os.replace(tmp, self.state_path)

    def final(self, ok: bool, cause: str) -> int:
        resumable = [g["outcome"] for g in self.state["generations"]
                     if (g.get("outcome") or {}).get("kind") not in (None, "normal")]
        record = {"ok": ok, "cause": cause, "first_cause": resumable[0] if resumable else None,
                  "generations": len(self.state["generations"]),
                  "resumes": max(0, len(self.state["generations"]) - 1), "t": self.clock(),
                  # GPU queue time is not given back to the window (see the docstring)
                  "queue_seconds_total": round(sum(g.get("queue_seconds") or 0
                                                   for g in self.state["generations"]), 1),
                  # standing watchers outlive the chain (the last CP is evaluated on
                  # their next tick); stop them with `kill PID` after its report.
                  "evaluation_watchers": {name: getattr(loop, "pid", None)
                                          for name, loop in self.eval_loops.items()},
                  "clock": self.clock_facts()}
        if self.state["generations"] and self.record(
                Path(self.state["generations"][-1]["run"]), "final"):
            record["run_record"] = str(self.dir / "run-record.json")
        self.state["final"] = record
        (self.dir / "FINAL.json").write_text(json.dumps(record, indent=1, default=str))
        self.note("chain_end", **record)
        if not ok:
            print(f"\n*** CHAIN {self.args.chain} STOPPED: {cause}\n*** first cause: "
                  f"{json.dumps(record['first_cause'], default=str)}\n", file=sys.stderr,
                  flush=True)
        return 0 if ok else 1

    def clock_facts(self) -> dict[str, Any]:
        """Training and wall seconds of the chain (#603): the learner's training count
        from the last generation's report (operating budget consumed at its start + its
        own training window; a resume continues the count of the CP it restored), the
        chain's wall seconds since it started, and the target (None = off)."""
        target = self.state.get("train_target_seconds") or None
        training = None
        if self.state["generations"]:
            path = Path(self.state["generations"][-1]["run"]) / "learner-report.json"
            if path.exists():
                training = ((json.loads(path.read_text()).get("clock") or {})
                            .get("chain_training_seconds"))
        return {"training_seconds": training, "train_target_seconds": target,
                "train_target_reached": (None if target is None or training is None
                                         else training >= target - TARGET_TOLERANCE_SECONDS),
                "wall_seconds": round(self.clock() - self.state["started_at"], 1),
                "wall_limit_seconds": self.knobs["long_wall_seconds"]}

    # ---------------- generations ----------------
    def runtag(self, number: int) -> str:
        return self.args.chain if number == 0 else f"{self.args.chain}-r{number}"

    def chain_facts(self, number: int) -> dict[str, Any]:
        target = self.state.get("train_target_seconds") or 0
        return {"id": f"c-{self.args.chain}", "deadline_at": self.state["deadline_at"],
                "total_seconds": target or self.knobs["long_wall_seconds"],
                "train_target_seconds": target, "generation": number}

    def env_recipe(self) -> list[str]:
        """The `env-recipe.txt` lines for the next generation ([] without the file)."""
        path = self.dir / "env-recipe.txt"
        if not path.exists():
            return []
        lines = []
        for line in path.read_text().splitlines():
            name, sep, text = line.partition("=")
            if line.strip() and not line.lstrip().startswith("#"):
                if not sep or name.strip() not in ENV_RECIPE_KNOBS:
                    raise ValueError(f"env-recipe.txt line {line!r}: only {ENV_RECIPE_KNOBS} "
                                     "may change at a generation boundary")
                lines.append(f"{name.strip()}={text.strip()}")
        run_profile.load_profile(self.args.profile, [*self.args.set, *lines])  # refuses loudly
        return lines

    def submit(self, number: int, checkpoint: str | None) -> dict[str, Any] | str:
        """One generation through the launcher; a refusal or failure is the chain's end."""
        tag = self.runtag(number)
        try:
            recipe = self.env_recipe()
        except (ValueError, run_profile.ProfileError) as error:
            return f"generation {number} not submitted: {error}"
        chain_json = self.dir / f"chain-{number}.json"
        chain_json.write_text(json.dumps(self.chain_facts(number)))
        argv = ["preflight" if self.args.dry_run else "submit", tag,
                "--profile", str(self.args.profile), "--site", str(self.args.site),
                "--cards", str(self.args.cards), "--card-type", self.args.card_type,
                "--long-run", "--chain-json", str(chain_json)]
        argv += ["--layout", self.args.layout] if self.args.layout else []
        argv += ["--resume-checkpoint", checkpoint] if checkpoint else []
        for item in [*self.args.set, *recipe]:  # a later --set of the same knob wins
            argv += ["--set", item]
        generation = {"number": number, "runtag": tag, "checkpoint": checkpoint,
                      "run": str(Path(self.site["work_root"]) / f"run-{tag}"),
                      "submitted_at": self.clock(), "argv": argv, "env_recipe": recipe}
        self.state["generations"].append(generation)
        self.persist()  # the intent is durable before anything is submitted
        try:
            code = self.launch_main(argv)
        except Stopped:
            raise  # run(): unsupervised; `supervise.py stop` reads jobs.json
        except BaseException as error:  # noqa: BLE001 - jobs may exist: never repeated
            generation["outcome"] = {"kind": "submit_failed", "failure": repr(error)}
            return f"generation {number} submit failed ({error!r}); not repeated"
        if code != 0:
            generation["outcome"] = {"kind": "refused", "failure": f"launcher exit {code}"}
            return f"generation {number} refused by the launcher (exit {code}, see preflight)"
        jobs_path = Path(generation["run"]) / "jobs.json"
        generation["jobs"] = json.loads(jobs_path.read_text()) if jobs_path.exists() else {}
        started = self.clock()  # the launcher returned: gpu.sh confirmed its start
        generation["queue_seconds"] = round(started - generation["submitted_at"], 1)
        generation["window_left_seconds"] = round(self.state["deadline_at"] - started)
        self.note("generation_started", number=number, runtag=tag, checkpoint=checkpoint,
                  jobs=generation["jobs"], env_recipe=recipe,
                  queue_seconds=generation["queue_seconds"],
                  window_left_seconds=generation["window_left_seconds"])
        if not self.args.dry_run:
            print(f"*** generation {number} started after {generation['queue_seconds']:.0f} s "
                  f"in the queue; {generation['window_left_seconds']} s of the chain window "
                  f"({self.knobs['long_wall_seconds']} s, fixed at chain start) left",
                  file=sys.stderr, flush=True)
        return generation

    def newest_checkpoint(self) -> str | None:
        """Newest CP (published_at) of any generation of this chain; else the chain's
        own starting CP (None = the start package)."""
        generations = {f"g-fc-{g['runtag']}" for g in self.state["generations"]}
        records = [r for r in self.checkpoints(generations)
                   if r.get("training_generation") in generations]
        if records:
            return max(records, key=lambda r: r["published_at"])["checkpoint_artifact_id"]
        return self.state["start_checkpoint"]

    def peer_checkpoints(self, generations: set[str]) -> list[dict[str, Any]]:
        from spireagent.rl_exchange import Exchange

        exchange = Exchange({"kind": "local", "directory": self.site["peer_dir"]},
                            self.site["run_name"])
        records = exchange.checkpoint_records()
        for record in records:
            if record.get("training_generation") in generations:
                manifest = exchange.store.get_manifest(record["checkpoint_artifact_id"])
                if manifest.kind != "checkpoint":
                    raise ValueError(f"CP record {record['checkpoint_artifact_id']} does not "
                                     "name a checkpoint artifact")
        return records

    def wait_generation(self, generation: dict[str, Any]) -> dict[str, Any]:
        """Until every job of the generation is gone; readout and evaluation meanwhile."""
        ids = [str(job) for job in generation["jobs"].values()]
        gpu = str(generation["jobs"].get("gpu"))
        run = Path(generation["run"])
        while True:
            live = self.squeue(ids)
            lost = self.env_lost(generation, live)
            if lost:
                done = self.end_generation(generation, f"service stop: env side lost: {lost}")
                return {"kind": "env_lost", "failure": f"{lost}; {done}"}
            if live is not None and gpu not in live:
                if live:  # GPU side ended: the env/prep jobs of this generation go too
                    self.scancel(sorted(live))
                    self.sleep(30)
                    if self.squeue(ids):
                        return {"kind": "jobs_left", "failure": "jobs of the old generation "
                                f"still alive after scancel: {sorted(live)}"}
                return outcome(run)
            self.tick(run)
            self.sleep(self.knobs["supervise_poll_seconds"])

    def env_lost(self, generation: dict[str, Any], live: set[str] | None) -> str | None:
        """Why the generation has no environment side while its GPU job runs, once that
        lasted `env_lost_grace_seconds` (see the docstring); None otherwise."""
        grace, run = self.knobs["env_lost_grace_seconds"], Path(generation["run"])
        env = [str(job) for name, job in generation["jobs"].items() if name.startswith("env")]
        gpu = str(generation["jobs"].get("gpu"))
        if (not grace or not env or live is None or gpu not in live or live & set(env)
                or any((run / name).exists() for name in (
                    "DRAIN", "STOP", "learner-report.json", "gpu-rc"))):
            generation.pop("env_gone_at", None)
            return None
        now = self.clock()
        try:  # a node may leave on its own this close to the env drain (env.sh)
            drain_at = float((run / "env-drain-at").read_text())
        except (OSError, ValueError):
            drain_at = None
        if drain_at is not None and drain_at - now < self.knobs["env_min_join_seconds"]:
            return None
        since = generation.setdefault("env_gone_at", now)
        if now - since < grace:
            return None
        return (f"every env job ({', '.join(env)}) ended while GPU job {gpu} runs and the GPU "
                f"side never asked them to leave (no DRAIN/STOP, {now - since:.0f} s): no "
                "training data can arrive; see the run's slurm-env-*.out and driver logs")

    def end_generation(self, generation: dict[str, Any], why: str) -> str:
        """Stop the generation: STOP (never over an earlier reason; a text starting with
        "service" is what the learner and services stop on, roles.py), up to
        `stop_grace_seconds` for the GPU job to end, then launch.cancel_all on what is left.
        Returns what happened; "unknown" when squeue cannot tell."""
        jobs = generation.get("jobs") or {}
        run = Path(generation["run"])
        if not jobs and (run / "jobs.json").exists():
            jobs = json.loads((run / "jobs.json").read_text())
        if not jobs:
            return "no jobs recorded"
        stop = "STOP written"
        try:
            with open(run / "STOP", "x") as handle:
                handle.write(why + "\n")
        except FileExistsError:  # an earlier reason stays
            stop = "STOP already there"
        ids, gpu = [str(job) for job in jobs.values()], str(jobs.get("gpu"))
        waited = 0.0
        live = self.squeue(ids)
        while live is None or gpu in live:
            if waited >= self.knobs["stop_grace_seconds"]:
                break
            self.sleep(10)
            waited += 10
            live = self.squeue(ids)
        if live is not None and not live:
            return f"{stop}; every job ended within {waited:.0f} s"
        cancelled = launch.cancel_all(
            {name: job for name, job in jobs.items() if live is None or str(job) in live},
            run_scancel=self.scancel)
        self.sleep(30)
        left = self.squeue(ids)
        if left is None:
            return f"{stop}; {cancelled}; whether any job is still alive is unknown " \
                "(squeue failed)"
        return f"{stop}; {cancelled}" + (f"; STILL ALIVE: {sorted(left)}" if left else "")

    def end_watchers(self, pids: dict[str, int]) -> list[str]:
        """End each evaluation watcher (its process group) and cancel the live Slurm jobs
        its state names (prepare and evaluation jobs, fast_eval.py)."""
        ended = []
        for name, pid in pids.items():
            if pid is None:
                ended.append(f"{name} pid not recorded: may still be alive")
                continue
            try:
                ended.append(f"{name} {self.end_watcher(int(pid))}")
            except OSError as error:  # already gone
                ended.append(f"{name} ({error!r})")
        ids = []
        for path in sorted(self.dir.glob("eval-*/state.json")):
            try:
                items = json.loads(path.read_text()).get("evaluations", [])
            except (OSError, ValueError) as error:
                ended.append(f"{path.parent.name} state unreadable ({error!r}): its jobs may "
                             "still be alive")
                continue
            ids += [str(item[key]) for item in items for key in ("prepare_job", "evaluation_job")
                    if item.get(key)]
            ids += [str(job) for item in items  # a batched round's jobs (#591)
                    for job in (item.get("evaluation_jobs") or {}).values() if job]
        live = self.squeue(ids) if ids else set()
        targets = sorted(set(ids) if live is None else live)
        if targets:
            out = self.scancel(targets)
            rc = getattr(out, "returncode", 0)
            ended.append(f"evaluation jobs {targets} " + ("cancelled" if rc == 0 else
                         f"SCANCEL rc {rc}: may still be alive"))
        return ended

    def stop(self) -> int:
        """`supervise.py stop CHAIN`: the operator's stop (see the docstring). Never writes
        state.json (a live supervise owns it)."""
        if not self.state_path.exists():
            print(f"no chain at {self.dir}", file=sys.stderr)
            return 2
        self.state = json.loads(self.state_path.read_text())
        (self.dir / "STOP-REQUESTED").write_text(f"{self.clock()}\n")
        ended = self.end_watchers(self.state.get("eval_pids") or {})
        generations = self.state["generations"]
        done = (self.end_generation(generations[-1], "service stop: operator stop "
                                    "(supervise.py stop)") if generations
                else "no generation submitted")
        report = {"t": self.clock(), "chain": self.args.chain,
                  "generation": generations[-1]["number"] if generations else None,
                  "generation_stop": done, "watchers": ended}
        (self.dir / "stop-report.json").write_text(json.dumps(report, indent=1))
        print(json.dumps(report), flush=True)
        doubt = ("FAILED", "STILL ALIVE", "unknown", "may still be alive")
        return 1 if any(word in text for text in [done, *ended] for word in doubt) else 0

    def tick(self, run: Path) -> None:
        now = self.clock()
        if now >= self.state.get("next_readout", 0) and (run / "plan.json").exists():
            self.state["next_readout"] = now + self.knobs["readout_every_seconds"]
            try:
                text = readout.render(self.read(run))
            except Exception as error:  # noqa: BLE001 - a readout never stops the run
                text = f"readout failed: {error!r}"
            with (self.dir / "readout.txt").open("a") as handle:
                handle.write(f"\n## {time.strftime('%Y-%m-%d %H:%M:%S')} {run.name}\n"
                             f"{text}\n{self.eval_status()}\n")
            self.persist()
        if self.new_checkpoint(run):
            self.retain()
            self.record(run, "checkpoint")
        for name, loop in list(self.eval_loops.items()):
            if loop.poll() is not None:
                self.state.setdefault("eval_ended", {})[name] = loop.returncode
                self.note("evaluation_loop_ended", watcher=name, code=loop.returncode)
                print(f"*** evaluation watcher {name} DEAD (exit {loop.returncode}): "
                      f"see {self.dir / f'eval-{name}.log'}", file=sys.stderr, flush=True)
                del self.eval_loops[name]

    def new_checkpoint(self, run: Path) -> bool:
        """Did the learner publish a CP since the last look? Reads only the new whole lines
        of learner-events.jsonl (the offset is kept in state.json). A read error (NFS) is
        noted and counts as no new CP this tick: it must never end supervise."""
        path = run / "learner-events.jsonl"
        offsets = self.state.setdefault("record_offsets", {})
        offset = offsets.get(run.name, 0)
        try:
            size = path.stat().st_size if path.exists() else 0
            if size <= offset:
                return False
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read(size - offset)
        except OSError as error:
            print(f"checkpoint scan of {run.name} failed: {error!r}", file=sys.stderr,
                  flush=True)
            self.note("checkpoint_scan_failed", run=run.name, error=repr(error))
            return False
        whole = chunk[:chunk.rfind(b"\n") + 1]  # a line still being written waits
        offsets[run.name] = offset + len(whole)
        return b'"checkpoint_published"' in whole

    def read(self, run: Path) -> dict[str, Any]:
        """The readout record of `run`, folding only what was appended since the last read
        (a new generation, or an error last time, starts a fresh Readout). The rolling
        windows run on across resumes: the chain's earlier generations seed them."""
        if self.reader is None or self.reader.run != run:
            runs = [Path(g["run"]) for g in self.state["generations"]]
            earlier = runs[:runs.index(run)] if run in runs else []
            self.reader = readout.Readout(run, 1200.0, seed=earlier)
        reader, self.reader = self.reader, None
        result = reader.update().record()
        self.reader = reader
        return result

    def record(self, run: Path, why: str) -> bool:
        """Write the run record of `run`: readout's `--json` result plus this chain's facts.
        True when written; a failure is noted (state log, stderr), never stops the run."""
        if not (run / "plan.json").exists():
            return False
        try:
            record = self.read(run)
            generation = next((g for g in self.state["generations"] if g["run"] == str(run)),
                              {})
            record["chain"] = {
                "chain": self.args.chain, "why": why, "written_at": self.clock(),
                "generation": generation.get("number"), "runtag": generation.get("runtag"),
                "generation_checkpoint": generation.get("checkpoint"),
                "env_recipe": generation.get("env_recipe"),
                "generations": len(self.state["generations"]),
                "resumes": len(self.state["resumes_at"]),
                "start_checkpoint": self.state["start_checkpoint"],
                "deadline_at": self.state["deadline_at"], "identity": self.state["identity"],
                "checkpoint_retention": self.retention,
                "model_retention": self.model_retention}
            text = json.dumps(record, ensure_ascii=False, default=str)
            tmp = self.dir / "run-record.json.tmp"
            tmp.write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str))
            os.replace(tmp, self.dir / "run-record.json")
            with (self.dir / "run-records.jsonl").open("a") as handle:
                handle.write(text + "\n")
        except Exception as error:  # noqa: BLE001 - a record never stops the run
            print(f"run record of {run.name} ({why}) failed: {error!r}", file=sys.stderr,
                  flush=True)
            self.note("run_record_failed", run=run.name, why=why, error=repr(error))
            return False
        self.persist()
        return True

    # ---------------- checkpoint retention ----------------
    def retain(self) -> None:
        """After a published CP: apply cp_retention to this chain's CPs and model
        publications on the peer (delete only with checkpoint_keep_newest /
        model_keep_newest > 0) and keep the facts for the run record. A failed unlink is
        loud (stderr, state log) and counted, and is tried again next time; any other
        error (an unreadable claim file included) skips this round loudly. Neither ever
        stops the run."""
        totals = self.state.setdefault("checkpoint_retention",
                                       {"deleted": [], "deleted_bytes": 0, "delete_failed": 0})
        models = self.state.setdefault("model_retention",
                                       {"deleted": 0, "deleted_bytes": 0, "delete_failed": 0})
        try:
            if self.deleter is None:
                from spireagent.rl_exchange import Exchange

                self.peer = Exchange({"kind": "local", "directory": self.site["peer_dir"]},
                                     self.site["run_name"])
                self.deleter = cp_retention.Deleter(self.peer.store)
            kept_records = self.retain_checkpoints(totals)
            self.retain_models(models, kept_records)
        except Exception as error:  # noqa: BLE001 - retention never stops the run
            print(f"*** CP/model retention skipped: {error!r}", file=sys.stderr, flush=True)
            self.note("checkpoint_retention_failed", error=repr(error))
            self.retention = {"error": repr(error), "deleted": len(totals["deleted"]),
                              "delete_failed": totals["delete_failed"]}
            self.model_retention = {"error": repr(error), **models}
        self.persist()

    def retain_checkpoints(self, totals: dict[str, Any]) -> list[dict[str, Any]]:
        """The CP pass; returns the retained CP records (they tie their model version)."""
        assert self.deleter is not None
        keep_newest = self.knobs["checkpoint_keep_newest"]
        generations = {f"g-fc-{g['runtag']}" for g in self.state["generations"]}
        records = [r for r in self.checkpoints(generations)
                   if r.get("training_generation") in generations]
        protected = {g["checkpoint"] for g in self.state["generations"] if g["checkpoint"]}
        protected |= cp_retention.claims(self.dir)
        kept, drop = cp_retention.select(
            records, keep_newest=keep_newest,
            milestone_hours=self.knobs["checkpoint_milestone_hours"],
            started_at=self.state["started_at"], protected=protected)
        for artifact in (a for a in drop if a not in totals["deleted"]):
            done = self.deleter.delete(artifact)
            totals["deleted_bytes"] += done["freed_bytes"]
            if done["failed"]:
                totals["delete_failed"] += len(done["failed"])
                print(f"*** CP retention: {len(done['failed'])} chunk(s) of {artifact} "
                      f"not deleted: {done['failed'][:3]}", file=sys.stderr, flush=True)
                self.note("checkpoint_delete_failed", checkpoint=artifact,
                          failed=done["failed"])
                continue  # tried again after the next CP
            totals["deleted"].append(artifact)
            self.note("checkpoint_deleted", checkpoint=artifact, **{
                k: v for k, v in done.items() if k != "failed"})
        self.retention = {
            "keep_newest": keep_newest,
            "milestone_hours": self.knobs["checkpoint_milestone_hours"],
            "kept": len(kept), "deleted": len(totals["deleted"]),
            "delete_failed": totals["delete_failed"],
            "deleted_bytes": totals["deleted_bytes"],
            "payload_bytes_kept": sum(self.deleter.payload_bytes(a) for a in kept)}
        kept_ids = set(kept)
        return [r for r in records if r["checkpoint_artifact_id"] in kept_ids]

    def retain_models(self, totals: dict[str, Any], kept_cps: list[dict[str, Any]]) -> None:
        """The model-publication pass (cp_retention.select_models). Records are immutable:
        each is read once per process. Deleted ones are remembered in memory only (a
        repeated delete after a supervisor restart frees nothing and is harmless); state
        keeps the counts, and one note per pass names them."""
        assert self.deleter is not None
        keep_newest = self.knobs["model_keep_newest"]
        for g in self.state["generations"]:
            generation = f"g-fc-{g['runtag']}"
            for version in self.peer.model_versions(training_generation=generation):
                if (generation, version) not in self.model_records:
                    self.model_records[(generation, version)] = self.peer.model(
                        version, training_generation=generation)
        tied = {(r.get("training_generation"), r["version"]) for r in kept_cps}
        kept, drop = cp_retention.select_models(list(self.model_records.values()),
                                                keep_newest=keep_newest, tied=tied)
        deleted = freed = failed = 0
        for record in drop:
            key = (record.get("training_generation"), record["version"])
            if key in self.models_deleted:
                continue
            done = self.deleter.delete_model(record)
            freed += done["freed_bytes"]
            if done["failed"]:
                failed += len(done["failed"])
                print(f"*** model retention: {len(done['failed'])} chunk(s) of version "
                      f"{key} not deleted: {done['failed'][:3]}", file=sys.stderr, flush=True)
                self.note("model_delete_failed", version=key, failed=done["failed"])
                continue
            self.models_deleted.add(key)
            deleted += 1
        totals["deleted"] += deleted
        totals["deleted_bytes"] += freed
        totals["delete_failed"] += failed
        if deleted:
            self.note("models_deleted", count=deleted, freed_bytes=freed)
        self.model_retention = {
            "keep_newest": keep_newest, "kept": len(kept), **totals,
            "payload_bytes_kept": sum(self.deleter.payload_bytes(r["model_artifact_id"])
                                 + self.deleter.payload_bytes(r["heads_artifact_id"])
                                 for r in kept)}

    # ---------------- evaluation ----------------
    def watchers(self) -> dict[str, dict[str, Any]]:
        """Standing fast_eval.py watchers (#528): one per suite, each its own output."""
        out = {"a0": {"suite": self.knobs["eval_suite"], "manifest": self.args.eval_manifest,
                      "seeds": self.knobs["eval_seeds"],
                      "batch_seeds": self.knobs["eval_batch_seeds"]}}
        if self.knobs["eval_a10_enabled"]:
            out["a10"] = {"suite": self.knobs["eval_a10_suite"],
                          "manifest": self.args.eval_a10_manifest,
                          "seeds": self.knobs["eval_a10_seeds"], "batch_seeds": 0}
        registered = self.site.get("eval_baselines") or {}
        for name, watcher in out.items():
            if name == "a0" and self.args.eval_baseline_plan:
                watcher["baseline"] = {"plan": str(self.args.eval_baseline_plan),
                                       "sha256": None, "source": "explicit"}
            elif name in registered and not self.args.eval_no_baseline:
                watcher["baseline"] = {**registered[name], "source": "site"}
            else:  # refused at the start check unless --eval-no-baseline
                watcher["baseline"] = None
        return out

    def eval_command(self, name: str, watcher: dict[str, Any]) -> list[str]:
        """No --checkpoint/--once/--deadline-at: the newest CP of this chain (--chain:
        its generations only, never another chain's on a shared peer) every
        eval_interval_seconds, and it keeps going after the chain so the last CP gets its
        evaluation too."""
        config = self.dir / "eval-learner.json"
        config.write_text(json.dumps({"peer": {"kind": "local",
                                               "directory": self.site["peer_dir"]},
                                      "run": self.site["run_name"]}))
        # The execution template (models, retention, pipeline) with the shared-local
        # storage of this site's peer: no R2 (lead 11-12, evaluate.validate_execution).
        execution = self.dir / "eval-execution.json"
        execution.write_text(json.dumps({
            **json.loads(Path(self.args.eval_execution).read_text()),
            "storage_mode": "shared_local", "store": self.site["peer_dir"],
            "peer": {"kind": "local", "directory": self.site["peer_dir"]}}, indent=1))
        cmd = [self.site["python"], str(Path(self.site["src_dir"]) / "tools/friday/fast_eval.py"),
               "--learner-config", str(config), "--execution", str(execution),
               "--manifest", str(watcher["manifest"]), "--output", str(self.dir / f"eval-{name}"),
               "--interval", str(self.knobs["eval_interval_seconds"]),
               "--seeds", str(watcher["seeds"]), "--chain", self.args.chain,
               # the site release dir with env/, node, game/, dotnet (#590)
               "--actor-root", self.site.get("eval_actor_root") or self.site["node_release"]]
        cmd += ["--batch-seeds", str(watcher["batch_seeds"])] if watcher["batch_seeds"] else []
        cmd += ["--suite", watcher["suite"]] if watcher["suite"] else []
        baseline = watcher["baseline"]
        if baseline is not None:
            cmd += ["--baseline-plan", baseline["plan"], "--baseline-source", baseline["source"]]
            cmd += ["--baseline-sha256", baseline["sha256"]] if baseline["sha256"] else []
        elif self.args.eval_no_baseline:
            cmd += ["--no-baseline"]
        cmd += ["--slot-registered"] if self.args.eval_slot_registered else ["--dry-run"]
        return cmd

    def check_eval(self) -> str | None:
        """Every watcher's start checks, before generation 0: training never runs with an
        evaluation that cannot start. The refusal names the watcher and the reason."""
        if not self.args.eval_execution:
            return None
        for name, watcher in self.watchers().items():
            if watcher["baseline"] is None and not self.args.eval_no_baseline:
                return (f"evaluation watcher {name}: no baseline (site eval_baselines.{name} "
                        "or --eval-baseline-plan for a0); pass --eval-no-baseline for none")
            done = self.check([*self.eval_command(name, watcher), "--check"])
            if done.returncode != 0:
                tail = (done.stderr or done.stdout or "").strip().splitlines()[-1:]
                return f"evaluation watcher {name} refused at start: {' '.join(tail)}"
            self.note("evaluation_watcher_checked", watcher=name, baseline=watcher["baseline"])
        return None

    def start_eval(self) -> None:
        if not self.args.eval_execution or self.args.dry_run:
            return
        for name, watcher in self.watchers().items():
            self.eval_loops[name] = self.spawn(self.eval_command(name, watcher),
                                               self.dir / f"eval-{name}.log")
            # `supervise.py stop` ends it by this pid (its own session: spawn)
            self.state.setdefault("eval_pids", {})[name] = getattr(
                self.eval_loops[name], "pid", None)
            self.note("evaluation_watcher_started", watcher=name, suite=watcher["suite"])

    def eval_status(self) -> str:
        if not self.args.eval_execution:
            return "evaluation: off"
        parts = []
        ended = self.state.get("eval_ended", {})
        for name in self.watchers():
            dead = f"DEAD (exit {ended[name]}) " if name in ended else ""
            path = self.dir / f"eval-{name}" / "state.json"
            if not path.exists():
                parts.append(f"{name} {dead}no state yet")
                continue
            stages: dict[str, int] = {}
            for item in json.loads(path.read_text()).get("evaluations", []):
                stages[item["stage"]] = stages.get(item["stage"], 0) + 1
            parts.append(f"{name} {dead}" + ", ".join(f"{k} {v}"
                                                     for k, v in sorted(stages.items())))
        return "evaluation: " + "; ".join(parts)

    # ---------------- the chain ----------------
    def run(self) -> int:
        if self.dir.exists():
            raise SystemExit(f"{self.dir} exists: a chain is started once")
        self.dir.mkdir(parents=True, mode=0o700)
        self.state = {
            "schema": SCHEMA, "chain": self.args.chain, "started_at": self.clock(),
            "deadline_at": self.clock() + self.knobs["long_wall_seconds"]
            + CHAIN_SLACK_SECONDS,
            "train_target_seconds": self.knobs["train_target_seconds"],
            "start_checkpoint": self.args.resume_checkpoint,
            "identity": identity(self.args.profile, self.args.site, self.args.set,
                                 source_identity(self.site)),
            "generations": [], "resumes_at": [], "log": []}
        self.persist()
        handlers = self.catch_signals()
        try:
            refused = self.check_eval()
            if refused:
                return self.final(False, refused)
            self.start_eval()
            checkpoint, number = self.args.resume_checkpoint, 0
            while True:
                generation = self.submit(number, checkpoint)
                if isinstance(generation, str):
                    return self.final(False, generation)
                if self.args.dry_run:
                    return self.final(True, "dry run: generation 0 preflight passed")
                result = self.wait_generation(generation)
                generation["outcome"], generation["ended_at"] = result, self.clock()
                self.note("generation_ended", number=number, outcome=result)
                if result["kind"] == "normal":
                    return self.final(True, self.normal_cause(result))
                if result["kind"] != "resumable":
                    return self.final(False, f"generation {number} ended {result['kind']}: "
                                      f"{result.get('failure') or result.get('stop')}")
                why = self.refuse_resume()
                if why:
                    return self.final(False, why)
                self.record(Path(generation["run"]), "generation_end")
                checkpoint, number = self.newest_checkpoint(), number + 1
                self.state["resumes_at"].append(self.clock())
                self.note("resume", number=number, checkpoint=checkpoint)
        except Stopped as stop:
            self.note("supervisor_signal", signal=str(stop))
            print(f"\n*** supervise got {stop}: chain {self.args.chain} is now UNSUPERVISED. Its "
                  "jobs and evaluation watchers keep running, with no resume, readout or run "
                  f"record. Stop it: supervise.py stop {self.args.chain} --site "
                  f"{self.args.site}. Re-attaching a supervise is not supported (#587).\n",
                  file=sys.stderr, flush=True)
            return 143
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            self.persist()

    def normal_cause(self, result: dict[str, Any]) -> str:
        cause = f"window complete ({result['window_end']})"
        clock = self.clock_facts()
        if clock["train_target_seconds"] is None:
            return cause
        trained = clock["training_seconds"]
        shown = "unknown" if trained is None else f"{trained:.0f}"
        verdict = ("target reached" if clock["train_target_reached"] else
                   "TARGET NOT REACHED (the chain's wall limit or another window end)")
        return (f"{cause}; training {shown} of {clock['train_target_seconds']} s: {verdict}; "
                f"wall {clock['wall_seconds']:.0f} s")

    def catch_signals(self) -> dict[int, Any]:
        """SIGTERM raises Stopped (unless already ignored): one loud line, then exit; no
        cancel. SIGHUP is left alone (see the docstring)."""
        def stop(signum: int, frame: Any) -> None:
            signal.signal(signum, signal.SIG_IGN)
            raise Stopped(signal.Signals(signum).name)
        if signal.getsignal(signal.SIGTERM) is signal.SIG_IGN:
            return {}
        return {signal.SIGTERM: signal.signal(signal.SIGTERM, stop)}

    def refuse_resume(self) -> str | None:
        if (self.dir / "STOP-REQUESTED").exists():
            return "operator stop requested (supervise.py stop): never resumed"
        now = self.clock()
        done = self.state["resumes_at"]
        if len(done) >= self.knobs["resume_max_total"]:
            return f"resume budget exhausted ({len(done)} of {self.knobs['resume_max_total']})"
        left = self.state["deadline_at"] - now
        if left < self.knobs["min_generation_seconds"]:
            return f"only {left:.0f} s of the window left (< min_generation_seconds)"
        current = identity(self.args.profile, self.args.site, self.args.set,
                           source_identity(self.site))
        changed = sorted(k for k in current if current[k] != self.state["identity"][k])
        if changed:
            return "code/profile changed since the chain started (" + ", ".join(changed) \
                + "): never resumed across a change"
        return None


def end_watcher_group(pid: int) -> str:
    """End a watcher's process group (its own session) if `pid` is still fast_eval.py."""
    out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                         text=True)
    if "fast_eval.py" not in out.stdout:
        return f"pid {pid} not running here (already ended, or another login node)"
    os.killpg(pid, signal.SIGTERM)
    return f"pid {pid} ended"


def live_jobs(ids: list[str]) -> set[str] | None:
    """Job ids (array parents included) still queued or running; None if unreadable."""
    if not ids:
        return set()
    out = subprocess.run(["squeue", "-h", "-j", ",".join(ids), "-o", "%F %i"],
                         capture_output=True, text=True)
    if out.returncode != 0 and "Invalid job id" not in out.stderr:
        return None
    alive = set()
    for line in out.stdout.split("\n"):
        alive.update(part.split("_")[0] for part in line.split())
    return alive & set(ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("start", "stop"))
    parser.add_argument("chain")
    parser.add_argument("--profile", type=Path, default=HERE / "rl5-fastchain.toml")
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--cards", type=int)
    parser.add_argument("--card-type")
    parser.add_argument("--layout")
    parser.add_argument("--set", action="append", default=[])
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--eval-execution", type=Path)
    parser.add_argument("--eval-manifest", type=Path)
    parser.add_argument("--eval-baseline-plan", type=Path,
                        help="plan.json of the reference round for the A0 watcher's report "
                             "(default: the site's eval_baselines.a0)")
    parser.add_argument("--eval-no-baseline", action="store_true",
                        help="no baseline for any watcher, not even the site's registered "
                             "ones (e.g. the first evaluation on a new Host pin)")
    parser.add_argument("--eval-a10-manifest", type=Path,
                        help="prepare-manifest output of eval_a10_suite (eval_a10_enabled)")
    parser.add_argument("--eval-slot-registered", action="store_true",
                        help="a GAME-SLOT line for the evaluation games is registered")
    parser.add_argument("--dry-run", action="store_true",
                        help="generation 0 preflight only; nothing is submitted")
    args = parser.parse_args(argv)
    if args.command == "stop":
        # the chain's own knobs (stop_grace_seconds): its recorded --set overrides
        site = run_profile.load_site(args.site.resolve())
        state = Path(site["work_root"]) / f"chain-{args.chain}" / "state.json"
        if state.exists():
            args.set = json.loads(state.read_text())["identity"]["overrides"]
        args.profile, args.site = args.profile.resolve(), args.site.resolve()
        return Chain(args).stop()
    if args.cards is None or not args.card_type:
        parser.error("start needs --cards and --card-type")
    if not launch.TAG.match(args.chain) or len(args.chain) > 30:
        parser.error("chain: lowercase letters, digits and '-', at most 30")
    if re.search(r"-r\d+$", args.chain):  # generation names are CHAIN-rN (review D4)
        parser.error("chain: must not end in -rN (that names a generation of another chain)")
    if bool(args.eval_execution) != bool(args.eval_manifest):
        parser.error("--eval-execution and --eval-manifest go together")
    if args.eval_execution and bool(args.eval_a10_manifest) != bool(
            run_profile.values(run_profile.load_profile(args.profile, args.set))
            ["eval_a10_enabled"]):
        parser.error("--eval-a10-manifest goes with eval_a10_enabled = true "
                     "(without A10: --set eval_a10_enabled=false)")
    if args.eval_baseline_plan and args.eval_no_baseline:
        parser.error("--eval-baseline-plan or --eval-no-baseline, not both")
    args.profile, args.site = args.profile.resolve(), args.site.resolve()
    return Chain(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
