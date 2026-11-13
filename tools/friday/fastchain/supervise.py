#!/usr/bin/env python3
"""Resume chain of a fast-chain long run: generations, automatic resume, hourly readout,
2 h evaluations (login node; runs nothing on GPUs itself).

    supervise.py start CHAIN --site S --cards N --card-type T [--layout L] [--set k=v]...
                 [--resume-checkpoint CP] [--eval-execution E --eval-manifest M
                 --eval-slot-registered] [--dry-run]

One chain is one operating window: `long_wall_seconds` (<= 72000 s), one learner budget id
(`c-CHAIN`) and one immutable deadline, kept by the learner across restorations
(learner_config.window). Generation 0 is `launch.py submit CHAIN --long-run`; each later
generation `CHAIN-rN` is a NEW run directory and a NEW training generation, started from
the newest CP of the chain (RL4.1 long-run lessons: continuation = new dir + reconciled
state; newest CP by published_at; re-freeze on code change).

Resume rules (user 11-13 C5: automatic; lead 11-14: the simple version):
- resumable = the learner reported `input_owner_fenced_restore_last_checkpoint` (the
  product's own instruction: the fenced owner restores its last CP), a service or prep
  host was lost (STOP "service failure ..."), or the GPU job died without a learner
  report (node failure, preemption); the old generation's jobs are cancelled first;
- not resumed: a normal end, any other learner failure (it would repeat), a refused
  preflight or failed submit, a disk-watermark stop, a code/profile/site/source change
  since the chain started (RL4.1 lesson: re-freeze on code change);
- at most `resume_max_total` resumes (default 3);
- the restore itself is the learner's: CP-only restore (fresh generation, in-flight input
  discarded), shard count recorded in the CP. Not covered: a CP that fails to restore is
  not skipped for an older one (the next generation fails and the chain stops), and
  evaluations already submitted are not re-checked across generations;
- a submission is never repeated: a launcher failure after jobs were submitted (unknown
  delivery) ends the chain;
- the chain ends loudly with the first cause (FINAL.json, exit 1); a normal end of the
  window exits 0.
Schedules: readout of the current generation every `readout_every_seconds` (readout.txt),
and with --eval-*: the source's tools/friday/fast_eval.py loop on the run's peer
(every `eval_interval_seconds`, `eval_seeds` games, never stopping training), plus one
evaluation of the newest CP once the chain ends.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    """How a generation ended, from its own files only."""
    report_path, stop = run / "learner-report.json", run / "STOP"
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    failure = (report or {}).get("failure")
    disk = (run / "disk-stop").read_text().strip() if (run / "disk-stop").exists() else None
    first = stop.read_text().strip() if stop.exists() else None
    lost = (first or "").startswith("service failure")
    if disk:
        kind = "disk_stop"
    elif report is not None and failure is None:
        kind = "normal"
    elif report is None or lost or any(code in failure for code in RESUMABLE):
        kind = "resumable"
    else:
        kind = "failure"
    return {"kind": kind, "failure": failure, "stop": first, "disk_stop": disk,
            "window_end": ((report or {}).get("summary") or {}).get("window_end")}


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
        self.eval_loop: Any = None

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
                  "resumes": len(self.state["generations"]) - 1, "t": self.clock()}
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

    def submit(self, number: int, checkpoint: str | None) -> dict[str, Any] | str:
        """One generation through the launcher; a refusal or failure is the chain's end."""
        tag = self.runtag(number)
        chain_json = self.dir / f"chain-{number}.json"
        chain_json.write_text(json.dumps(self.chain_facts(number)))
        argv = ["preflight" if self.args.dry_run else "submit", tag,
                "--profile", str(self.args.profile), "--site", str(self.args.site),
                "--cards", str(self.args.cards), "--card-type", self.args.card_type,
                "--long-run", "--chain-json", str(chain_json)]
        argv += ["--layout", self.args.layout] if self.args.layout else []
        argv += ["--resume-checkpoint", checkpoint] if checkpoint else []
        for item in self.args.set:
            argv += ["--set", item]
        generation = {"number": number, "runtag": tag, "checkpoint": checkpoint,
                      "run": str(Path(self.site["work_root"]) / f"run-{tag}"),
                      "submitted_at": self.clock(), "argv": argv}
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
                  jobs=generation["jobs"])
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
        if self.eval_loop is not None and self.eval_loop.poll() is not None:
            self.note("evaluation_loop_ended", code=self.eval_loop.returncode)
            self.eval_loop = None

    # ---------------- evaluation ----------------
    def eval_command(self, once: bool) -> list[str]:
        config = self.dir / "eval-learner.json"
        config.write_text(json.dumps({"peer": {"kind": "local",
                                               "directory": self.site["peer_dir"]},
                                      "run": self.site["run_name"]}))
        cmd = [self.site["python"], str(Path(self.site["src_dir"]) / "tools/friday/fast_eval.py"),
               "--learner-config", str(config), "--execution", str(self.args.eval_execution),
               "--manifest", str(self.args.eval_manifest), "--output", str(self.dir / "eval"),
               "--interval", str(self.knobs["eval_interval_seconds"]),
               "--seeds", str(self.knobs["eval_seeds"])]
        cmd += ["--once"] if once else ["--deadline-at", str(self.state["deadline_at"])]
        cmd += ["--slot-registered"] if self.args.eval_slot_registered else ["--dry-run"]
        return cmd

    def start_eval(self, once: bool = False) -> None:
        if not self.args.eval_execution or self.args.dry_run:
            return
        self.eval_loop = self.spawn(self.eval_command(once), self.dir / "eval.log")
        self.note("evaluation_loop_started", once=once)

    def eval_status(self) -> str:
        path = self.dir / "eval" / "state.json"
        if not path.exists():
            return "evaluation: " + ("off" if not self.args.eval_execution else "no state yet")
        stages: dict[str, int] = {}
        for item in json.loads(path.read_text()).get("evaluations", []):
            stages[item["stage"]] = stages.get(item["stage"], 0) + 1
        return "evaluation: " + ", ".join(f"{k} {v}" for k, v in sorted(stages.items()))

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
                checkpoint, number = self.newest_checkpoint(), number + 1
                self.state["resumes_at"].append(self.clock())
                self.note("resume", number=number, checkpoint=checkpoint)
        finally:
            if self.eval_loop is not None:
                self.eval_loop.terminate()  # its OS lock goes with it
                self.eval_loop.wait(timeout=60)
            self.start_eval(once=True)  # the chain's last CP gets its evaluation too
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
    parser.add_argument("--eval-slot-registered", action="store_true",
                        help="a GAME-SLOT line for the evaluation games is registered")
    parser.add_argument("--dry-run", action="store_true",
                        help="generation 0 preflight only; nothing is submitted")
    args = parser.parse_args(argv)
    if not launch.TAG.match(args.chain) or len(args.chain) > 30:
        parser.error("chain: lowercase letters, digits and '-', at most 30")
    if bool(args.eval_execution) != bool(args.eval_manifest):
        parser.error("--eval-execution and --eval-manifest go together")
    args.profile, args.site = args.profile.resolve(), args.site.resolve()
    return Chain(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
