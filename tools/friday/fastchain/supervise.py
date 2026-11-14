#!/usr/bin/env python3
"""Resume chain of a fast-chain long run: generations, automatic resume, hourly readout,
2 h evaluations (login node; runs nothing on GPUs itself).

    supervise.py start CHAIN --site S --cards N --card-type T [--layout L] [--set k=v]...
                 [--resume-checkpoint CP] [--eval-execution E --eval-manifest M
                 [--eval-baseline-plan P] [--eval-a10-manifest M10] --eval-slot-registered]
                 [--dry-run]

One chain is one operating window: `long_wall_seconds` (<= 72000 s), one learner budget id
(`c-CHAIN`) and one immutable deadline, kept by the learner across restorations
(learner_config.window). Generation 0 is `launch.py submit CHAIN --long-run`; each later
generation `CHAIN-rN` is a NEW run directory and a NEW training generation, started from
the newest CP of the chain (RL4.1 long-run lessons: continuation = new dir + reconciled
state; newest CP by published_at; re-freeze on code change).

Resume rules (user 11-13 C5: automatic; lead 11-14: the simple version):
- normal = gpu.sh exited 0 (its `gpu-rc` file), the learner report has no failure and a
  window end, and STOP names no failure;
- resumable = only the learner's own signal: its report failure names
  `input_owner_fenced_restore_last_checkpoint` (the product's instruction: the fenced
  owner restores its last CP; `input_failure.operation` says why, e.g. a lost hand-off),
  or there is neither a learner report nor a `gpu-rc` (node failure, preemption); the old
  generation's jobs are cancelled first;
- not resumed: any other learner failure, including a setup failure (an unrestorable CP
  writes a report too, so the chain stops at once), a "service failure" STOP alone (gpu
  preflight, GPU count, MPS, memory guard: they would repeat), a refused preflight or
  failed submit, a disk-watermark stop, a code/profile/site/source change since the
  chain started (RL4.1 lesson: re-freeze on code change);
- at most `resume_max_total` resumes (default 3);
- environment-side recipe at a generation boundary (I8): `chain-CHAIN/env-recipe.txt`, one
  `name=value` per line as `--set` takes it, only `ENV_RECIPE_KNOBS` (exploration ladder,
  ascension mix, K); read when a later generation is submitted and recorded with it. The
  learner recipe never changes in a chain. Another knob or a refused value ends the chain.
  No automatic curriculum: a generation boundary is a resume, not a schedule;
- the restore itself is the learner's: CP-only restore (fresh generation, in-flight input
  discarded), shard count recorded in the CP. Not covered: a CP that fails to restore is
  not skipped for an older one (the next generation fails and the chain stops), and
  evaluations already submitted are not re-checked across generations;
- a submission is never repeated: a launcher failure after jobs were submitted (unknown
  delivery) ends the chain;
- the chain ends loudly with the first cause (FINAL.json, exit 1); a normal end of the
  window exits 0.
Schedules: readout of the current generation every `readout_every_seconds` (readout.txt),
its run record (`readout.py --json` plus this chain's facts) at every checkpoint the
learner publishes, before a resume and at the chain's end (`run-record.json`, the latest;
`run-records.jsonl`, one line per write; a failed write is noted, never stops the run),
and with --eval-*: one standing tools/friday/fast_eval.py watcher per suite (#528) on the
run's peer: the newest CP every `eval_interval_seconds`, `eval_seeds` games of
`eval_suite` (empty = the fixed A0 100), never stopping training; a second watcher on the
A10 suite when `eval_a10_enabled`. The watchers outlive the chain, so the last CP is
evaluated on their next tick; FINAL.json names their pids (stop them after that report).
--eval-execution is a template (models, retention, pipeline): the chain writes its copy
with `"storage_mode": "shared_local"` on the site's peer (no R2).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import launch  # noqa: E402
import readout  # noqa: E402
import run_profile  # noqa: E402

SCHEMA = "spireagent/friday-fastchain-chain-1"
RESUMABLE = ("input_owner_fenced_restore_last_checkpoint",)
# Environment-side knobs a chain may change at a generation boundary (I8); not in the
# frozen learner recipe, checked per game by the fast env and the assembler.
ENV_RECIPE_KNOBS = ("exploration_ladder", "ascension_mix", "replay_k")
CHAIN_SLACK_SECONDS = 900  # deadline = start + window + slack: generation 0 ends by its wall


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(profile: Path, site: Path, overrides: list[str],
             src: dict[str, Any]) -> dict[str, Any]:
    """What a resume must not change: harness code, profile, site, overrides, source."""
    return {"harness": {name: digest(HERE / name)
                        for name in (*launch.HARNESS, "launch.py", "supervise.py")},
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
    window end, and no failure STOP. resumable: only the learner's own signal (its report
    failure names `input_owner_fenced_restore_last_checkpoint`), or no learner report and
    no `gpu-rc` (the node died or the job was preempted). Everything else repeats: stop.
    """
    report_path, stop, rc_path = run / "learner-report.json", run / "STOP", run / "gpu-rc"
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    failure = (report or {}).get("failure")
    disk = (run / "disk-stop").read_text().strip() if (run / "disk-stop").exists() else None
    first = stop.read_text().strip() if stop.exists() else None
    text = rc_path.read_text().strip() if rc_path.exists() else ""
    rc = int(text) if text.lstrip("-").isdigit() else None
    window_end = ((report or {}).get("summary") or {}).get("window_end")
    failed_stop = (first or "").startswith(("service failure", "learner failure"))
    if disk:
        kind = "disk_stop"
    elif rc == 0 and report is not None and failure is None and window_end and not failed_stop:
        kind = "normal"
    elif (failure and any(code in failure for code in RESUMABLE)) or (
            report is None and rc is None):
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
                 spawn: Callable[[list[str], Path], Any] | None = None) -> None:
        self.args, self.clock, self.sleep, self.launch_main = args, clock, sleep, launch_main
        self.squeue = squeue or live_jobs
        self.scancel = scancel or (lambda ids: subprocess.run(["scancel", *ids], check=False))
        self.site = run_profile.load_site(args.site)
        self.knobs = run_profile.values(run_profile.load_profile(args.profile, args.set))
        self.checkpoints = checkpoints or self.peer_checkpoints
        self.spawn = spawn or (lambda cmd, log: subprocess.Popen(
            cmd, stdout=log.open("a"), stderr=subprocess.STDOUT, start_new_session=True))
        self.dir = Path(self.site["work_root"]) / f"chain-{args.chain}"
        self.state_path = self.dir / "state.json"
        self.eval_loops: dict[str, Any] = {}

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
                  "resumes": len(self.state["generations"]) - 1, "t": self.clock(),
                  # standing watchers outlive the chain (the last CP is evaluated on
                  # their next tick); stop them with `kill PID` after its report.
                  "evaluation_watchers": {name: getattr(loop, "pid", None)
                                          for name, loop in self.eval_loops.items()}}
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

    # ---------------- generations ----------------
    def runtag(self, number: int) -> str:
        return self.args.chain if number == 0 else f"{self.args.chain}-r{number}"

    def chain_facts(self, number: int) -> dict[str, Any]:
        return {"id": f"c-{self.args.chain}", "deadline_at": self.state["deadline_at"],
                "total_seconds": self.knobs["long_wall_seconds"], "generation": number}

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
        except BaseException as error:  # noqa: BLE001 - jobs may exist: never repeated
            generation["outcome"] = {"kind": "submit_failed", "failure": repr(error)}
            return f"generation {number} submit failed ({error!r}); not repeated"
        if code != 0:
            generation["outcome"] = {"kind": "refused", "failure": f"launcher exit {code}"}
            return f"generation {number} refused by the launcher (exit {code}, see preflight)"
        jobs_path = Path(generation["run"]) / "jobs.json"
        generation["jobs"] = json.loads(jobs_path.read_text()) if jobs_path.exists() else {}
        self.note("generation_started", number=number, runtag=tag, checkpoint=checkpoint,
                  jobs=generation["jobs"], env_recipe=recipe)
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

    def tick(self, run: Path) -> None:
        now = self.clock()
        if now >= self.state.get("next_readout", 0) and (run / "plan.json").exists():
            self.state["next_readout"] = now + self.knobs["readout_every_seconds"]
            try:
                text = readout.render(readout.build(run, 1200.0))
            except Exception as error:  # noqa: BLE001 - a readout never stops the run
                text = f"readout failed: {error!r}"
            with (self.dir / "readout.txt").open("a") as handle:
                handle.write(f"\n## {time.strftime('%Y-%m-%d %H:%M:%S')} {run.name}\n"
                             f"{text}\n{self.eval_status()}\n")
            self.persist()
        if self.new_checkpoint(run):
            self.record(run, "checkpoint")
        for name, loop in list(self.eval_loops.items()):
            if loop.poll() is not None:
                self.note("evaluation_loop_ended", watcher=name, code=loop.returncode)
                del self.eval_loops[name]

    def new_checkpoint(self, run: Path) -> bool:
        """Did the learner publish a CP since the last look? Reads only the new whole lines
        of learner-events.jsonl (the offset is kept in state.json)."""
        path = run / "learner-events.jsonl"
        offsets = self.state.setdefault("record_offsets", {})
        offset = offsets.get(run.name, 0)
        size = path.stat().st_size if path.exists() else 0
        if size <= offset:
            return False
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(size - offset)
        whole = chunk[:chunk.rfind(b"\n") + 1]  # a line still being written waits
        offsets[run.name] = offset + len(whole)
        return b'"checkpoint_published"' in whole

    def record(self, run: Path, why: str) -> bool:
        """Write the run record of `run`: readout's `--json` result plus this chain's facts.
        True when written; a failure is noted (state log, stderr), never stops the run."""
        if not (run / "plan.json").exists():
            return False
        try:
            record = readout.build(run, 1200.0)
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
                "deadline_at": self.state["deadline_at"], "identity": self.state["identity"]}
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

    # ---------------- evaluation ----------------
    def watchers(self) -> dict[str, dict[str, Any]]:
        """Standing fast_eval.py watchers (#528): one per suite, each its own output."""
        out = {"a0": {"suite": self.knobs["eval_suite"], "manifest": self.args.eval_manifest,
                      "seeds": self.knobs["eval_seeds"],
                      "baseline": self.args.eval_baseline_plan}}
        if self.knobs["eval_a10_enabled"]:
            out["a10"] = {"suite": self.knobs["eval_a10_suite"],
                          "manifest": self.args.eval_a10_manifest,
                          "seeds": self.knobs["eval_a10_seeds"], "baseline": None}
        return out

    def eval_command(self, name: str, watcher: dict[str, Any]) -> list[str]:
        """No --checkpoint/--once/--deadline-at: the newest CP every eval_interval_seconds,
        and it keeps going after the chain so the last CP gets its evaluation too."""
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
               "--seeds", str(watcher["seeds"])]
        cmd += ["--suite", watcher["suite"]] if watcher["suite"] else []
        cmd += ["--baseline-plan", str(watcher["baseline"])] if watcher["baseline"] else []
        cmd += ["--slot-registered"] if self.args.eval_slot_registered else ["--dry-run"]
        return cmd

    def start_eval(self) -> None:
        if not self.args.eval_execution or self.args.dry_run:
            return
        for name, watcher in self.watchers().items():
            self.eval_loops[name] = self.spawn(self.eval_command(name, watcher),
                                               self.dir / f"eval-{name}.log")
            self.note("evaluation_watcher_started", watcher=name, suite=watcher["suite"])

    def eval_status(self) -> str:
        if not self.args.eval_execution:
            return "evaluation: off"
        parts = []
        for name in self.watchers():
            path = self.dir / f"eval-{name}" / "state.json"
            if not path.exists():
                parts.append(f"{name} no state yet")
                continue
            stages: dict[str, int] = {}
            for item in json.loads(path.read_text()).get("evaluations", []):
                stages[item["stage"]] = stages.get(item["stage"], 0) + 1
            parts.append(f"{name} " + ", ".join(f"{k} {v}" for k, v in sorted(stages.items())))
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
            "start_checkpoint": self.args.resume_checkpoint,
            "identity": identity(self.args.profile, self.args.site, self.args.set,
                                 source_identity(self.site)),
            "generations": [], "resumes_at": [], "log": []}
        self.persist()
        self.start_eval()
        checkpoint, number = self.args.resume_checkpoint, 0
        try:
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
                    return self.final(True, f"window complete ({result['window_end']})")
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
        finally:
            self.persist()

    def refuse_resume(self) -> str | None:
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
    parser.add_argument("command", choices=("start",))
    parser.add_argument("chain")
    parser.add_argument("--profile", type=Path, default=HERE / "rl5-fastchain.toml")
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--cards", type=int, required=True)
    parser.add_argument("--card-type", required=True)
    parser.add_argument("--layout")
    parser.add_argument("--set", action="append", default=[])
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--eval-execution", type=Path)
    parser.add_argument("--eval-manifest", type=Path)
    parser.add_argument("--eval-baseline-plan", type=Path,
                        help="plan.json of the reference round for the A0 watcher's report")
    parser.add_argument("--eval-a10-manifest", type=Path,
                        help="prepare-manifest output of eval_a10_suite (eval_a10_enabled)")
    parser.add_argument("--eval-slot-registered", action="store_true",
                        help="a GAME-SLOT line for the evaluation games is registered")
    parser.add_argument("--dry-run", action="store_true",
                        help="generation 0 preflight only; nothing is submitted")
    args = parser.parse_args(argv)
    if not launch.TAG.match(args.chain) or len(args.chain) > 30:
        parser.error("chain: lowercase letters, digits and '-', at most 30")
    if re.search(r"-r\d+$", args.chain):  # generation names are CHAIN-rN (review D4)
        parser.error("chain: must not end in -rN (that names a generation of another chain)")
    if bool(args.eval_execution) != bool(args.eval_manifest):
        parser.error("--eval-execution and --eval-manifest go together")
    if args.eval_execution and bool(args.eval_a10_manifest) != bool(
            run_profile.values(run_profile.load_profile(args.profile, args.set))
            ["eval_a10_enabled"]):
        parser.error("--eval-a10-manifest goes with eval_a10_enabled = true")
    args.profile, args.site = args.profile.resolve(), args.site.resolve()
    return Chain(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
