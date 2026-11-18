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
  1800 s per game by default (`--episode-seconds`, profile eval_episode_seconds, at most
  evaluate.MAX_EPISODE_SECONDS; a game cut there is unknown/censored, never retried),
  unknowns continue cleaned and are excluded per pair, never voiding the round;
- job size (EVAL-SPEED-CHECK 11-14, lead decision a/e): 16 workers, 16 live games
  (`--pending-limit`: playing + saving + waiting for a shard) and 4 save threads, the
  code maximums; memory 4 GB per live game; job wall from the number of games and
  the expected mean steps per game (`planned_wall_seconds`, `--expected-steps`), so
  longer games of a stronger CP get a longer wall. All are fast_eval settings,
  frozen in state.json and passed to `evaluate.py prepare-checkpoint`;
- after the evaluation job ends, the owner's report (`stpd.evaluation.rl_observation`:
  per-floor distribution, max floor, Boss counts, wins, mean reward, paired deltas)
  is written next to it against the baseline and the previous evaluation when their
  observed protocol and seed manifest match; a different protocol is skipped and
  named, never compared;
- the baseline is chosen explicitly: `--baseline-plan` (pinned by `--baseline-sha256`;
  supervise passes the site's registered round, `eval_baselines`) or `--no-baseline`
  (e.g. the first evaluation on a new Host pin). Pairing is never dropped silently:
  at start the baseline round must have this execution's Host and Runtime pins and
  initial progress and this seed manifest, else the loop refuses to start (`--check`
  runs exactly these start checks and exits); what is only known after the games
  (input spec, policy) is compared in the report, and a skipped baseline is a loud
  `evaluation_baseline_skipped` (state log and stderr). summary.json and the
  `evaluation_reported` note name the baseline `{plan, sha256, source}`;
- the job keeps `evaluate.CLOSE_RESERVE_SECONDS` of its wall for the save tail, the
  report and the evidence; a job that had to skip its in-job report is
  `report_deferred` here (loud): `evaluate.py project-report` writes it later.

Start checks (`--check`, which supervise runs before generation 0, and again before the
loop starts), so an evaluation that cannot run is refused before any GPU or CPU job:
- the seed manifest has the seeds; 1 <= seeds <= `evaluate.MAX_DISPATCH_SLOTS` in one job
  (one job admits at most 200 games), any number up to the manifest with `--batch-seeds`;
- the actor root (`--actor-root`, default the source checkout's parent; supervise passes
  the site release directory, #590) holds `evaluate.ACTOR_LAYOUT` (env, Node, game, .NET);
- the execution template's `agent_adapter` equals this source's
  `export.adapter_identity()` (the executor's own `frozen_agent_implementation_changed`
  check, run early; re-freeze a stale template with `tools/friday/eval_template.py
  refreeze`, never by hand); an interpreter that cannot import it is refused too;
- a baseline round frozen under another agent adapter pairs only through the re-freeze
  record named by this template's `refrozen_from` that links exactly the baseline's
  adapter to this one; both adapters and producers, `adapter_differs` and the record
  are written into summary.json, eval-record.json and the report (`adapter_pairing`).

Batches (#591): with `--batch-seeds B` a round of `--seeds N` games (e.g. the A0 300 suite)
is one preparation (one export) and ceil(N/B) evaluation jobs of manifest ordinals 1..B,
B+1..2B, ... run in parallel, each with the wall of its own games; `round/batches.json`
(written last) names their plans. The round is reported once every job has ended: one
merged cohort, report, summary.json and eval-record.json (per-batch times inside). A batch
that ends without a report, or could not be submitted, is named with its game count in
the report and summary and its games are absent from the cohort; it is never retried.
Unknown games stay excluded and counted as in one job. A reference (baseline or previous
round) pairs by seed when one round's seed manifest is a prefix of the other's (the A0 300
starts with the A0 100 seeds), and may be named by its `batches.json`.

Batches of one round each carry their own ledger admission: a batch whose formal-audit
game did not finish is closed unscored by its ledger (its games stay rows, never scores)
and is named in `batches_not_admitted`; the batches still merge (#612). Batches with
another checkpoint binding, model selection, host kind, character or ascension are refused.

Node class (#612): `--partition` / `--constraint` (or a site profile's `eval_partition` /
`eval_constraint` through `--site`; supervise passes them) place the evaluation jobs;
none given = today's submission (partition general, no constraint). The report's first
lines name them. A round whose planned games that are not finished-and-scored exceed
`--degraded-share` (default 0.10, the rolling unknown gate) of its planned games (a
batch without a report keeps its games in the planned count) is reported all the same,
labelled DEGRADED with its counts by bucket (loud `evaluation_round_degraded`), and
refused as a baseline at start.

A round of fewer than DECISION_GAMES (100, lead decision) games is a smoke: summary.json,
eval-record.json and the report say "smoke, not a decision result", and no reference is
paired (no paired-delta line).

`--until-file F` (supervise: the chain's FINAL.json, #598): once F exists the schedule
stops; the final CP (F's `final_checkpoint`, else the newest) gets one round if none has
it, waited for while not yet visible on the peer (loud once; until --deadline-at's
headroom, if given, or --final-grace-seconds after F was first seen:
`final_checkpoint_not_visible_after_grace`), every started round is waited for until it leaves the waiting
stages (reported, deferred, failed, unknown: never retried), then the loop exits with a
loud `evaluation_watcher_done` and its reason (`final_checkpoint_round_unknown` when the
final round's delivery is unknown). Nothing is
cancelled; a live job keeps it waiting. Without it the loop runs until stopped.

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
import math
import re
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
EPISODE_SECONDS = 1800
SEEDS = 100
MAX_LIVE = 2  # evaluations every 2 h overlap by at most one
WORKERS = 16  # RL4.2 final eval: 25 games on 8 workers took 1,700-2,450 s of play
# Job 1470561 (EVAL-SPEED-CHECK 11-14): 8 live slots held 16 cores to ~4 playing games.
PENDING_LIMIT = 16  # live games per job: playing + saving + waiting for a shard
SAVE_WORKERS = 4  # save threads overlap without slowing each other (91 vs 97 ms/step)
# Job 1471903: MaxRSS 34.1 GB at 16 live games = 2.1 GB each; 4 GB (the old 4 GB per
# worker) keeps ~1.9x, since an out-of-memory kill loses the whole job.
MEMORY_GB_PER_LIVE = 4
EXPECTED_STEPS = 1200  # mean steps per game the wall plans for: 2x the measured 604
# Live-slot seconds per step incl. its save queue, at 16 live (job 1471903):
# 4,568 s x 16 / 59,814 = 1.22 (0.92 at 8 live; queued saves hold slots longer).
STEP_SECONDS = 1.25
# After play: save tail, observation report (548 s for 100 games), evidence tar (8 s).
CLOSE_SECONDS = evaluate.CLOSE_RESERVE_SECONDS
MAX_WALL_SECONDS = 21600  # evaluate.prepare's bound
# Compared at start: the execution's pins against the baseline round's own execution.
START_PINS = ("host_pin", "runtime_pin", "initial_progress")
DECISION_GAMES = 100  # fewer games: a smoke, not a decision result (lead decision 11-17)
REFREEZE_SCHEMA = "spireagent/evaluation-template-refreeze-1"  # eval_template.py
PLAN_GRACE_SECONDS = 300  # shared disk: a job's last write can trail its end (#403)
RESULT_GRACE_SECONDS = 900  # #398: an ended evaluation job without a result
PREPARE = {"cpus": 1, "minutes": 20, "memory_gb": 6}
LIVE_STATES = "PD,R,CG,CF,S,RQ,RS"  # queued or running; a purged id is simply absent
SPLIT = 'exec "$actor_root/env/bin/python"'
WAITING = ("preparing", "submitted")
UNKNOWN = ("prepare_submit_unknown", "submit_unknown")  # delivery unknown: never retried
RETRY_BEFORE_SUBMISSION = ("requested_evaluation_workers_exceed_current_fair_share",)
PARTITIONS = ("smp", "general", "debug")  # evaluate.submit's CPU partitions
DEFAULT_PARTITION = "general"
# #612: a round whose planned games that are not finished-and-scored exceed this share
# is degraded (the project's 10% rolling unknown gate): reported and labelled, never voided,
# never a pairing baseline. --degraded-share / profile eval_degraded_share (both [0, 0.99]).
DEGRADED_SHARE = 0.10
MAX_DEGRADED_SHARE = 0.99
DEGRADED_STATUSES = ("unknown", "censored", "not_started")


def chain_generation(chain: str) -> re.Pattern[str]:
    """The training generations of one fast chain: g-fc-CHAIN and g-fc-CHAIN-rN (a chain
    name never ends in -rN, supervise.py), so no other chain's CP matches."""
    return re.compile(rf"g-fc-{re.escape(chain)}(-r\d+)?")


def newest_checkpoint(peer: dict[str, Any], run: str, generation: str | None = None,
                      chain: str | None = None) -> dict[str, Any] | None:
    """Newest CP record in the learner peer and its archive (watcher rule), of one
    generation or of one fast chain's generations when given (V6 smoke attempt 2: chains
    sharing a peer evaluated each other's CPs)."""
    from spireagent.rl_exchange import Exchange

    records = Exchange(peer, run).checkpoint_records()
    if peer.get("archive") is not None:
        records += Exchange(peer["archive"], run).checkpoint_records()
    if generation is not None:
        records = [r for r in records if r.get("training_generation") == generation]
    if chain is not None:
        own = chain_generation(chain)
        records = [r for r in records if own.fullmatch(str(r.get("training_generation")))]
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


def planned_wall_seconds(seeds: int, expected_steps: int, live: int,
                         episode_seconds: int) -> int:
    """Job wall = planned play + one episode for the last game started + report/evidence.

    Planned play = games x expected mean steps x live-slot seconds per step / live slots.
    100 games at 1200 steps on 16 slots: 9,375 + 1,800 + 900 = 12,075 s; 50 games: 7,388 s.
    """
    play = math.ceil(seeds * expected_steps * STEP_SECONDS / live)
    return min(MAX_WALL_SECONDS, play + episode_seconds + CLOSE_SECONDS)


def round_execution(plan: Path) -> dict[str, Any]:
    """A round's execution: the copy beside its plan.json, pinned by the plan's sha256
    (a reference round copied to a stable place stays readable)."""
    bound = json.loads(plan.read_bytes())["execution"]
    path = plan.parent / Path(bound["path"]).name
    if not path.is_file() or hashed(path) != bound["sha256"]:
        raise ValueError("baseline_execution_changed")
    return json.loads(path.read_bytes())


def round_reference(root: Path) -> Path:
    """A finished round's reference file: `round/batches.json` (batched) or its plan."""
    batches = root / "round" / evaluate.BATCHES
    return batches if batches.is_file() else root / "round/evaluation/plan.json"


def reference_plans(path: Path) -> list[Path]:
    """The plans a reference names: itself, or each plan of a `batches.json` (by sha256)."""
    if path.name != evaluate.BATCHES:
        return [path]
    plans = []
    for entry in json.loads(path.read_bytes())["batches"]:
        plan = path.parent / entry["directory"] / "plan.json"
        if not plan.is_file() or hashed(plan) != entry["plan_sha256"]:
            raise ValueError("round_batch_plan_changed")
        plans.append(plan)
    return plans


SUBMISSION_FAILURE = "submission-failure.json"  # beside a batch plan never delivered


def reported_plans(path: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    """The plans of a reference that have an observation report, and the batches that
    have none: `{batch, games, status, reason}` (a refused or unknown submission: its
    stage and reason; else the job's status, or `no_result`). A single plan is taken as
    is (its reader refuses it if the report is missing)."""
    if path.name != evaluate.BATCHES:
        return [path], []
    plans, missing = [], []
    for plan in reference_plans(path):
        result, failure = plan.parent / "result.json", plan.parent / SUBMISSION_FAILURE
        status = json.loads(result.read_bytes()) if result.is_file() else {}
        if "observation_report" in status:
            plans.append(plan)
            continue
        refused = json.loads(failure.read_bytes()) if failure.is_file() else {}
        missing.append({"batch": plan.parent.name,
                        "games": json.loads(plan.read_bytes()).get("planned"),
                        "status": refused.get("stage") or status.get("status", "no_result"),
                        "reason": refused.get("reason") or status.get(
                            "observation_report_skipped") or status.get(
                            "observation_report_error")})
    return plans, missing


def manifest_seeds(plan: Path) -> list[str] | None:
    """The seeds of a round's frozen manifest (the copy beside its plan, by sha256)."""
    bound = json.loads(plan.read_bytes())["manifest"]
    path = plan.parent / Path(bound["path"]).name
    if not path.is_file() or hashed(path) != bound["sha256"]:
        return None
    return json.loads(path.read_bytes()).get("seeds")


def seeds_pair(one: list[str] | None, other: list[str] | None) -> bool:
    """Two seed manifests pair when one's seeds are a prefix of the other's."""
    if not one or not other:
        return False
    short, long = sorted((one, other), key=len)
    return long[:len(short)] == short


DERIVED_KEYS = ("storage_mode", "store", "peer")  # supervise's storage overrides


def derived_template(execution: dict[str, Any], template: Path) -> Path:
    """The template this execution file was written from, proven by content (#598).

    supervise writes the evaluation execution into the chain directory: the template
    with only the storage keys (DERIVED_KEYS) replaced and `derived_from` {path, sha256,
    keys} added. The template's re-freeze record pins the template, not that copy, so
    pairing goes through the template once (a) the file at `path` still has `sha256` and
    (b) the copy equals it in every other key. Anything else is refused. A file without
    `derived_from` is its own template (unchanged)."""
    derived = execution.get("derived_from")
    if derived is None:
        return template
    if not isinstance(derived, dict) or tuple(derived.get("keys") or ()) != DERIVED_KEYS:
        raise ValueError("derived_execution_keys_changed")
    original = Path(str(derived.get("path")))
    if not original.is_file() or hashed(original) != derived.get("sha256"):
        raise ValueError(f"derived_execution_template_missing_or_changed: {original}")
    source = json.loads(original.read_bytes())
    if "derived_from" in source:
        raise ValueError("derived_execution_template_is_itself_derived")
    ignored = {*DERIVED_KEYS, "derived_from"}
    differing = sorted(k for k in source.keys() | execution.keys()
                       if k not in ignored and source.get(k) != execution.get(k))
    if differing:
        raise ValueError("derived_execution_differs_from_its_template: " + ", ".join(differing))
    return original


def adapter_pairing(baseline_execution: dict[str, Any], execution: dict[str, Any],
                    template: Path) -> dict[str, Any]:
    """Which agent adapter and producer each side was frozen with. When the adapter
    differs, the re-freeze record named by the template's `refrozen_from` (beside it)
    must link exactly the baseline's adapter to this one and be the record of this
    template; otherwise the pairing is refused. A supervise-written execution is checked
    against the template it names (`derived_template`), whose record it is."""
    def side(value: dict[str, Any]) -> dict[str, Any]:
        return {"agent_adapter": (value.get("agent_adapter") or {}).get("code_sha256"),
                "producer": value.get("producer")}

    theirs, ours = side(baseline_execution), side(execution)
    pairing: dict[str, Any] = {
        "baseline": theirs, "current": ours,
        "adapter_differs": theirs["agent_adapter"] != ours["agent_adapter"],
        "refreeze_record": None}
    if not pairing["adapter_differs"]:
        return pairing
    template = derived_template(execution, template)
    named = (execution.get("refrozen_from") or {}).get("record")
    path = template.parent / Path(named).name if isinstance(named, str) else None
    record = json.loads(path.read_bytes()) if path is not None and path.is_file() else {}
    linked = record.get("agent_adapter") or {}
    if (record.get("schema") != REFREEZE_SCHEMA
            or (linked.get("old") or {}).get("code_sha256") != theirs["agent_adapter"]
            or (linked.get("new") or {}).get("code_sha256") != ours["agent_adapter"]
            or (record.get("template_out") or {}).get("sha256") != hashed(template)):
        raise ValueError(
            f"baseline_adapter_differs_without_refreeze_record: the baseline round ran "
            f"agent_adapter {theirs['agent_adapter']}, this template pins "
            f"{ours['agent_adapter']}; no eval_template.py refreeze record beside the "
            "template links exactly these two")
    assert path is not None
    pairing["refreeze_record"] = {"path": str(path), "sha256": hashed(path),
                                  "template": str(template),
                                  "reason": record.get("reason")}
    return pairing


def pairing_note(pairing: dict[str, Any] | None) -> str:
    """The report's line on which adapter each side ran."""
    if pairing is None:
        return "Agent adapter pairing: not checked (watcher started before this check)."
    base, current = pairing["baseline"]["agent_adapter"], pairing["current"]["agent_adapter"]
    if not pairing["adapter_differs"]:
        return f"Agent adapter: baseline and current both {current}."
    record = pairing["refreeze_record"]
    return (f"Agent adapter DIFFERS: baseline {base}, current {current}; paired through "
            f"re-freeze record sha256 {record['sha256']} ({record['reason']}).")


def degradation(cohort: dict[str, Any], threshold: float,
                missing: list[dict[str, Any]] = ()) -> dict[str, Any]:
    """Share of the round's planned games that are not finished and scored; `degraded`
    above `threshold` (#612, lead rule). Planned = the reported batches' games plus the
    games of `missing` batches (no report). The buckets add up to planned - scored:
    unknown, censored, not_started, other (save_failed or any other unfinished status),
    finished_unscored (finished, not scored: a batch its ledger did not admit) and
    missing_batch. The games stay in the cohort, excluded and counted as before."""
    statuses = cohort["status_counts"]
    counts = {status: statuses.get(status, 0) for status in DEGRADED_STATUSES}
    finished = statuses.get("finished", 0)
    scored = sum(1 for row in cohort["rows"] if row["scored"])
    missing_games = sum(int(batch["games"]) for batch in missing)
    planned = cohort["planned"] + missing_games
    share = (planned - scored) / planned if planned else 0.0
    return {"degraded": share > threshold, "share": share, "threshold": threshold,
            "planned": planned, "scored": scored, **counts,
            "other": cohort["planned"] - finished - sum(counts.values()),
            "finished_unscored": finished - scored, "missing_batch": missing_games}


def degradation_text(value: dict[str, Any]) -> str:
    return (f"{value['planned'] - value['scored']} of {value['planned']} planned games not "
            f"finished and scored ({value['share']:.1%}, threshold {value['threshold']:.0%}): "
            f"unknown {value['unknown']}, censored {value['censored']}, not started "
            f"{value['not_started']}, save_failed/other {value['other']}, finished but "
            f"unscored (batch not admitted) {value['finished_unscored']}, missing batch "
            f"{value['missing_batch']}")


def read_round(reference: Path, plans: list[Path] | None = None, *,
               validate_plan: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    """One round's cohort (its batches merged) and its first plan, from the saved,
    hash-bound observation files. Batches of one round share one checkpoint binding (one
    preparation, one export): another binding is another model and is refused."""
    from stpd.evaluation.rl_observation import merge_cohorts

    read = [evaluate.read_observation_report(plan.parent / "result.json", plan,
                                             validate_plan=validate_plan)
            for plan in (plans or reference_plans(reference))]
    if len({json.dumps(frozen.get("checkpoint_binding"), sort_keys=True)
            for frozen, _, _ in read}) > 1:
        raise ValueError("merged_batches_must_share_one_checkpoint_binding")
    return merge_cohorts([files["cohort"] for _, _, files in read]), read[0][0]


def baseline_problem(baseline: dict[str, Any] | None, threshold: float) -> str | None:
    """Why the baseline round cannot be a pairing baseline: degraded (#612). Checked at
    start (`start_problems`), so the watcher never starts on it; None = usable."""
    if baseline is None:
        return None
    group, _ = read_round(Path(baseline["plan"]), validate_plan=False)
    value = degradation(group, threshold)
    if value["degraded"]:
        return (f"baseline_round_degraded: {baseline['plan']}: {degradation_text(value)}; "
                "a degraded round is never a pairing baseline: under supervise name another "
                "round in the site's eval_baselines.<name> (or --eval-baseline-plan) or "
                "pass --eval-no-baseline; by hand pass another --baseline-plan or "
                "--no-baseline")
    return None


def resolve_baseline(plan: Path | None, sha256: str | None, source: str,
                     execution: dict[str, Any], manifest: Path,
                     template: Path) -> dict[str, Any] | None:
    """`{plan, sha256, source, adapter_pairing}` of the baseline round, or None
    (`--no-baseline`).

    Refused at start, never dropped later without a word: a plan that is missing or not
    the pinned sha256, or a round on other Host/Runtime pins, initial progress or seed
    manifest than this watcher (the differing keys are named).
    """
    if plan is None:
        return None
    plan = plan.resolve()
    if not plan.is_file() or sha256 is not None and hashed(plan) != sha256:
        raise ValueError("baseline_plan_missing_or_changed")
    plans = reference_plans(plan)  # a batched reference round: its batches.json
    absent = reported_plans(plan)[1]
    if absent:  # a baseline pairs whole, never on part of its games
        raise ValueError("baseline_round_has_batches_without_a_report: "
                         + ", ".join(m["batch"] for m in absent))
    theirs = round_execution(plans[0])
    differing = [key for key in START_PINS if theirs.get(key) != execution.get(key)]
    ours = json.loads(manifest.read_bytes()).get("seeds")
    if any(json.loads(p.read_bytes())["manifest"]["sha256"] != hashed(manifest)
           and not seeds_pair(manifest_seeds(p), ours) for p in plans):
        differing.append("seed_manifest")
    if differing:
        raise ValueError("baseline_round_differs_from_this_watcher: " + ", ".join(differing))
    return {"plan": str(plan), "sha256": hashed(plan), "source": source,
            "adapter_pairing": adapter_pairing(theirs, execution, template)}


def code_adapter_identity() -> dict[str, str]:
    """This source's agent adapter identity: what the Managed executor compares the frozen
    execution's `agent_adapter` with (needs the evaluation interpreter: torch)."""
    from stpd.p1 import export

    return export.adapter_identity()


def start_problems(settings: dict[str, Any], execution: dict[str, Any]) -> list[str]:
    """Why an evaluation of these settings cannot run, found before any job is submitted:
    a degraded baseline round (#612), the actor layout and the frozen adapter identity (the
    seed count and manifest are checked by `main` and `check_manifest`). Empty = it can
    start."""
    problems = []
    try:
        degraded = baseline_problem(settings.get("baseline"),
                                    settings.get("degraded_share", DEGRADED_SHARE))
    except (ValueError, OSError, KeyError) as error:
        degraded = f"baseline_round_unreadable: {type(error).__name__}: {error}"
    if degraded is not None:
        problems.append(degraded)
    actor = evaluate.actor_root({"source_root": settings["runtime_source"],
                                 "actor_root": settings.get("actor_root")})
    try:
        evaluate.require_actor_layout(actor)
    except ValueError as error:
        problems.append(str(error))
    try:
        current = code_adapter_identity()
    except ImportError as error:
        return [*problems, f"evaluation_interpreter_cannot_import_the_adapter: {error}"]
    pinned = execution.get("agent_adapter")
    if pinned != current:
        shown = (pinned or {}).get("code_sha256") if isinstance(pinned, dict) else pinned
        problems.append(f"frozen_agent_implementation_changed: the execution template pins "
                        f"agent_adapter code_sha256 {shown}, this source computes "
                        f"{current['code_sha256']}; re-freeze the template with "
                        "tools/friday/eval_template.py refreeze (records both)")
    return problems


def prepare_script(settings: dict[str, Any], checkpoint: str, root: Path) -> str:
    """The watcher's preparation command, for one CP (no R2 environment when shared_local)."""
    launcher = evaluate.launcher(root / "not-offered-plan.json",
                                 {"source_root": settings["runtime_source"],
                                  "actor_root": settings.get("actor_root")},
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
               "--episode-seconds", str(settings["episode_seconds"]),
               "--pending-limit", str(settings["pending_limit"]),
               "--save-workers", str(settings["save_workers"]),
               "--memory-gb", str(settings["memory_gb"])]
    if settings.get("deadline_at") is not None:
        command += ["--deadline-at", str(settings["deadline_at"])]
    if settings.get("actor_root") is not None:
        command += ["--actor-root", shlex.quote(settings["actor_root"])]
    if settings.get("batch_seeds") is not None:
        command += ["--batch-seeds", str(settings["batch_seeds"])]
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


def jobs_of(item: dict[str, Any]) -> list[str]:
    """The evaluation job ids of a round: its batches' jobs, or its one job."""
    jobs = item.get("evaluation_jobs") or {"evaluation": item.get("evaluation_job")}
    return [str(job) for job in jobs.values() if job]


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
            settings["peer"], settings["run"], settings.get("training_generation"),
            settings.get("chain"))))
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

    def ending(self) -> bool:
        """The chain has ended (`until_file`, supervise: the chain's FINAL.json exists)."""
        return self.final() is not False

    def final(self) -> Any:
        """False before the chain ends (or while its file is not yet readable JSON); else
        the final CP id the file names (`final_checkpoint`, supervise #598: None = the
        chain produced none), or True when the file names none (then: the newest CP)."""
        until = self.settings.get("until_file")
        if until is None or not Path(until).is_file():
            return False
        try:
            value = json.loads(Path(until).read_bytes())
        except (OSError, ValueError):  # mid-write: read again next tick
            return False
        if isinstance(value, dict) and "final_checkpoint" in value:
            return value["final_checkpoint"]
        return True

    def final_target(self) -> tuple[bool, str | None]:
        """(named, id): the CP the last round must be for, and whether the file named it."""
        final = self.final()
        if final is True:
            record = self.newest()
            return False, record["checkpoint_artifact_id"] if record is not None else None
        return True, final

    def tick(self) -> None:
        if self.advance() is False:  # liveness unknown: nothing decided this tick
            self.persist()
            return
        if self.ending():  # no schedule now: the final CP once, if no round has it yet
            self.state.setdefault("final_seen_at", self.clock())  # the grace counts from here
            _, target = self.final_target()
            record = self.newest()
            visible = record is not None and record["checkpoint_artifact_id"] == target
            if visible and not any(e["checkpoint"] == target
                                   for e in self.state["evaluations"]):
                self.start()
            elif target is not None and not visible and not self.state.get("final_waiting"):
                # shared-disk lag: the chain's final CP is not on this peer view yet
                self.state["final_waiting"] = target
                self.loud("evaluation_final_checkpoint_not_visible", checkpoint=target,
                          newest=record["checkpoint_artifact_id"] if record else None)
        elif self.clock() >= self.state["next_due"]:
            self.state["next_due"] = self.clock() + self.settings["interval"]
            self.start()
        self.persist()

    def done(self) -> str | None:
        """Why the watcher may exit after its chain ended, or None: every round it started
        has left the waiting stages and the newest CP has a round (any stage: never
        retried), or the absolute deadline leaves no room to start it. Nothing is
        cancelled: a live job keeps the watcher waiting."""
        if not self.ending():
            return None
        if any(e["stage"] in WAITING for e in self.state["evaluations"]):
            return None
        _, target = self.final_target()
        if target is None:
            return "no_checkpoint"
        rounds = [e for e in self.state["evaluations"] if e["checkpoint"] == target]
        if rounds:  # never retried: an unknown submission is said, not called evaluated
            return ("final_checkpoint_round_unknown" if rounds[-1]["stage"] in UNKNOWN
                    else "final_checkpoint_evaluated")
        deadline = self.settings.get("deadline_at")
        if deadline is not None and self.clock() >= deadline - self.settings[
                "deadline_headroom_seconds"]:
            return "final_checkpoint_not_started_absolute_deadline"
        grace = self.settings.get("final_grace_seconds") or 0  # 0 / absent: wait until stop
        seen = self.state.get("final_seen_at")
        record = self.newest()
        if (grace and seen is not None and self.clock() >= seen + grace
                and (record is None or record["checkpoint_artifact_id"] != target)):
            return "final_checkpoint_not_visible_after_grace"
        return None

    def directories(self) -> list[str]:
        """The evaluation directories of each round under `round/` (one, or one per batch)."""
        return evaluate.batch_directories(self.settings["seeds"],
                                          self.settings.get("batch_seeds"))

    def advance(self) -> bool:
        """Preparation finished: submit its plans once; a dead job without them fails.

        A round whose evaluation jobs have all ended gets its report; one that ended
        without any result (after the shared-disk grace) is recorded, never resubmitted.
        A batched round with some results waits the same grace for the rest, then is
        reported with the missing batches named.
        """
        preparing = [e for e in self.state["evaluations"] if e["stage"] == "preparing"]
        submitted = [e for e in self.state["evaluations"]
                     if e["stage"] == "submitted" and jobs_of(e)]
        live = self.live([e["prepare_job"] for e in preparing]
                         + [job for e in submitted for job in jobs_of(e)])
        if live is None:
            return False
        for item in submitted:
            if any(job in live for job in jobs_of(item)):
                continue
            results = [Path(item["root"]) / "round" / d / "result.json"
                       for d in self.directories() if d not in item.get("batch_failures", {})]
            if not all(r.is_file() for r in results):
                ended = item.setdefault("ended_seen_at", self.clock())
                if self.clock() - ended < RESULT_GRACE_SECONDS:
                    continue
                if not any(r.is_file() for r in results):
                    item["stage"] = "evaluation_ended_without_result"
                    self.note("evaluation_ended_without_result", checkpoint=item["checkpoint"],
                              job=item.get("evaluation_job"), jobs=item.get("evaluation_jobs"))
                    continue
            self.report(item)
        for item in preparing:
            marker = Path(item["root"]) / "round" / (  # batches.json: written last
                evaluate.BATCHES if len(self.directories()) > 1 else "evaluation/plan.json")
            if item["prepare_job"] in live:
                continue
            if not marker.is_file():
                ended = item.setdefault("ended_seen_at", self.clock())
                if self.clock() - ended < PLAN_GRACE_SECONDS:
                    continue
                item["stage"] = "prepare_failed"
                self.note("evaluation_prepare_failed", checkpoint=item["checkpoint"],
                          job=item["prepare_job"])
                continue
            self.submit_round(item)
        return True

    def submit_round(self, item: dict[str, Any]) -> None:
        """Submits each plan of the round once, in order. A refused pre-submission check
        stops here and resumes next tick with the plans not yet submitted; a failed or
        unknown delivery of one batch is recorded (never retried) and the others go on.
        No job at all: the round's stage is that failure; else it is submitted."""
        directories = self.directories()
        batched = len(directories) > 1
        jobs = item.setdefault("evaluation_jobs", {}) if batched else {}
        failures = item.setdefault("batch_failures", {}) if batched else {}
        for directory in directories:
            if directory in jobs or directory in failures:
                continue
            plan = Path(item["root"]) / "round" / directory / "plan.json"
            try:
                result = self.submit(plan, partition=self.settings["partition"],
                                     constraint=self.settings.get("constraint"),
                                     exclude_nodes=self.settings.get("exclude_nodes") or None,
                                     dry_run=self.dry_run)
            except ValueError as error:
                if str(error) in RETRY_BEFORE_SUBMISSION:  # nothing was delivered
                    self.note("evaluation_submit_deferred", reason=str(error),
                              batch=directory if batched else None)
                    return
                stage, reason = "submit_failed", str(error)
            except (OSError, subprocess.SubprocessError) as error:
                # Delivery unknown (the owner's intent may exist): recorded, never retried.
                stage, reason = "submit_unknown", type(error).__name__
            else:
                jobs[directory] = result.get("job_id")
                item.setdefault("first_submitted_at", self.clock())
                if batched:
                    item.setdefault("batch_submitted_at", {})[directory] = self.clock()
                continue
            self.note(f"evaluation_{stage}", checkpoint=item["checkpoint"], reason=reason,
                      **({"batch": directory} if batched else {}))
            if not batched:
                item.update(stage=stage, reason=reason)
                return
            failures[directory] = {"stage": stage, "reason": reason}
            save(plan.parent / SUBMISSION_FAILURE, failures[directory])  # for the report
        if batched and not jobs:
            item.update(stage="submit_failed", reason="no_batch_submitted")
            return
        if not batched:
            item["evaluation_job"] = jobs["evaluation"]
        item.pop("ended_seen_at", None)  # the plan grace's mark; the result grace starts anew
        item.update(stage="submitted", submitted_at=self.clock())
        self.note("evaluation_submitted", checkpoint=item["checkpoint"],
                  job=item.get("evaluation_job"), jobs=item.get("evaluation_jobs"))

    def report(self, item: dict[str, Any]) -> None:
        previous = [e for e in self.state["evaluations"]
                    if e["stage"] == "reported" and e["number"] < item["number"]]
        results = [json.loads(path.read_bytes()) for path in (
            Path(item["root"]) / "round" / d / "result.json" for d in self.directories())
            if path.is_file()]
        result = results[0] if len(results) == 1 else {
            "status": sorted({r.get("status") for r in results}, key=str)}
        if not any("observation_report" in r for r in results):  # skipped, failed, interrupted
            item.update(stage="report_deferred", status=result.get("status"))
            self.loud("evaluation_report_deferred", checkpoint=item["checkpoint"],
                      status=result.get("status"),
                      reason=result.get("observation_report_skipped")
                      or result.get("observation_report_error"),
                      then="evaluate.py project-report --result/--plan of round/evaluation")
            return
        try:
            summary = write_evaluation_report(
                Path(item["root"]), self.settings.get("baseline"),
                str(round_reference(Path(previous[-1]["root"]))) if previous else None,
                degraded_share=self.settings.get("degraded_share", DEGRADED_SHARE),
                submission=submission(self.settings))
        except Exception as error:  # noqa: BLE001 -- originals stay; the report is a projection
            item.update(stage="report_failed", reason=f"{type(error).__name__}: {error}")
            self.note("evaluation_report_failed", checkpoint=item["checkpoint"],
                      reason=item["reason"])
            return
        item.update(stage="reported", summary=summary)
        failed = summary.get("evaluation_record_failed")
        if failed is None:
            try:
                self.add_watcher_facts(item)
            except Exception as error:  # noqa: BLE001 -- the report stands; the record is extra
                failed = f"{type(error).__name__}: {error}"
        if failed is not None:
            self.loud("evaluation_record_failed", checkpoint=item["checkpoint"], reason=failed)
        current = summary["cohorts"]["current"]
        self.note("evaluation_reported", checkpoint=item["checkpoint"],
                  report=summary["report_directory"], scored=current["scored"],
                  wins=current["wins"], mean_floor=current["mean_floor"],
                  reward=current["reward_mean"], conservative=current["conservative"],
                  baseline=summary["baseline"], paired=sorted(summary["paired"]),
                  skipped=summary["skipped_references"],
                  degraded=summary["degraded"]["degraded"])
        if summary["degraded"]["degraded"]:  # reported all the same: excluded and counted
            self.loud("evaluation_round_degraded", checkpoint=item["checkpoint"],
                      **summary["degraded"])
        if summary.get("batches_not_admitted"):
            self.loud("evaluation_batches_not_score_admitted", checkpoint=item["checkpoint"],
                      batches=summary["batches_not_admitted"])
        if summary.get("batches_missing"):
            self.loud("evaluation_batches_missing", checkpoint=item["checkpoint"],
                      batches=summary["batches_missing"])
        if "baseline" in summary["skipped_references"]:
            self.loud("evaluation_baseline_skipped", checkpoint=item["checkpoint"],
                      **summary["skipped_references"]["baseline"])

    def loud(self, kind: str, **fields: Any) -> None:
        """A note that must not go unseen: the state log and stderr."""
        self.note(kind, **fields)
        print(f"*** fast_eval {kind}: {json.dumps(fields, sort_keys=True)}", file=sys.stderr,
              flush=True)

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
                             "evaluation_job": item.get("evaluation_job"),
                             "evaluation_jobs": item.get("evaluation_jobs"),
                             "batch_failures": item.get("batch_failures")}
        # the first job's submission: a partly deferred round's last one would understate
        first = item.get("first_submitted_at", item.get("submitted_at"))
        record["watcher"]["batch_submitted_at"] = item.get("batch_submitted_at")
        record["stage_seconds"].update(
            prepare_wall=_span(preparing, first),
            evaluation_queue=_span(first, record.get("process_started_at")))
        save(path, record)

    def start(self) -> None:
        deadline = self.settings.get("deadline_at")
        headroom = self.settings["deadline_headroom_seconds"]
        if deadline is not None and self.clock() >= deadline - headroom:
            return self.note("evaluation_skipped", reason="absolute_deadline_headroom")
        evaluations = self.state["evaluations"]
        if any(e["stage"] == "preparing" for e in evaluations):
            return self.note("evaluation_skipped", reason="preparation_still_running")
        submitted = [e for e in evaluations if e["stage"] == "submitted" and jobs_of(e)]
        running = self.live([job for e in submitted for job in jobs_of(e)])
        if running is None:
            return self.note("evaluation_skipped", reason="scheduler_unreachable")
        # max_live counts rounds (a batched round's jobs together), not jobs
        if sum(any(job in running for job in jobs_of(e)) for e in submitted) >= \
                self.settings["max_live"]:
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


def submission(settings: dict[str, Any]) -> dict[str, Any]:
    """Where the evaluation jobs were submitted: partition and node constraint (#612)."""
    return {"partition": settings["partition"], "constraint": settings.get("constraint")}


def write_evaluation_report(root: Path, baseline: dict[str, Any] | None,
                            previous_plan: str | None, *,
                            degraded_share: float = DEGRADED_SHARE,
                            submission: dict[str, Any] | None = None) -> dict[str, Any]:
    """The owner's report for one finished evaluation round (`<root>/report`).

    Reads only the saved, hash-bound observation files of each round
    (`evaluate.read_observation_report`; the baseline by its pinned plan sha256, from
    wherever its round was copied). A reference with another observed protocol
    (Host/Runtime/input/progress pins, policy) or another seed manifest is skipped
    and named (`skipped_references`), never compared. Unknown/failed games stay
    unscored rows (excluded per pair, listed with the floor they reached); the round
    is never voided. Beside it, `<root>/eval-record.json` (`evaluation_record`; the
    watcher adds its own times). A batched round (#591) is one merged cohort of its
    batches with an observation report; the others are named in `batches_missing`, and
    a batch its ledger closed unscored (formal-audit slot not finished) in
    `batches_not_admitted`. A round above `degraded_share` of planned games (missing
    batches included) not finished and scored is labelled degraded (`degraded`, the report's first line); `submission`
    (partition, constraint) heads the report.
    """
    from stpd.evaluation.rl_observation import write_report

    began = time.time()
    def cohort(reference: Path, *, pinned: str | None = None,
               plans: list[Path] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if pinned is not None and hashed(reference) != pinned:
            raise ValueError("baseline_plan_changed")
        return read_round(reference, plans, validate_plan=pinned is None)

    round_file = round_reference(root)
    # never retried: a batch without a report is named and its games are absent
    plans, missing = reported_plans(round_file)
    if not plans:
        raise ValueError("no_batch_has_an_observation_report")
    current, plan = cohort(round_file, plans=plans)
    partial: dict[str, Any] = {}  # a previous round compared on its reported batches only
    cohorts: dict[str, dict[str, Any]] = {}
    skipped: dict[str, Any] = {}
    references = (("baseline", baseline["plan"] if baseline else None),
                  ("previous", previous_plan))
    smoke = current["planned"] < DECISION_GAMES
    not_compared = [name for name, reference in references if reference] if smoke else []
    pairing = baseline.get("adapter_pairing") if baseline else None
    degraded = degradation(current, degraded_share, missing)
    admitted = current.get("score_admitted_batches") or [current["score_admitted"]]
    not_admitted = [p.parent.name for p, ok in zip(plans, admitted, strict=True) if not ok]
    notes = []
    if submission is not None:
        notes.append(f"Evaluation jobs: partition {submission['partition']}, node constraint "
                     f"{submission.get('constraint') or 'none'}.")
    if degraded["degraded"]:
        notes.append(f"DEGRADED ROUND: {degradation_text(degraded)}; reported, never used "
                     "as a pairing baseline.")
    if not_admitted:
        notes.append("BATCHES NOT SCORE-ADMITTED (their ledger closed them unscored: a "
                     "formal-audit game did not finish; their games are rows, not scores): "
                     + ", ".join(not_admitted) + ".")
    if smoke:
        notes.append(f"SMOKE: {current['planned']} games < {DECISION_GAMES}: smoke, not a "
                     "decision result; no reference is paired.")
    if baseline:
        notes.append(pairing_note(pairing))
    for name, reference in references:
        if reference is None or smoke:
            continue
        theirs, absent = reported_plans(Path(reference))  # a baseline has none (start check)
        if not theirs:
            skipped[name] = {"plan": reference, "differing": ["no_batch_has_a_report"]}
            continue
        if absent:
            partial[name] = {"batches_reported": len(theirs),
                             "batches": len(theirs) + len(absent),
                             "games_missing": sum(m["games"] or 0 for m in absent),
                             "missing": absent}
        group, frozen = cohort(Path(reference), pinned=baseline["sha256"]
                               if name == "baseline" and baseline else None, plans=theirs)
        old, new = group["protocol_observed"], current["protocol_observed"]
        keys = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
        keys += [k for k in ("host_kind", "character", "ascension") if group[k] != current[k]]
        if frozen["manifest"]["sha256"] != plan["manifest"]["sha256"] and not seeds_pair(
                manifest_seeds(reference_plans(Path(reference))[0]), manifest_seeds(plans[0])):
            keys.append("seed_manifest")
        if keys:
            skipped[name] = {"plan": reference, "differing": keys}
            continue
        cohorts[name] = group
    cohorts["current"] = current
    ours = {row["seed"] for row in current["rows"]}
    seed_sets = {name: {"common_seeds": len(ours & {r["seed"] for r in group["rows"]}),
                        "seed_sets_equal": ours == {r["seed"] for r in group["rows"]},
                        "reference_seeds": len(group["rows"]), "current_seeds": len(ours)}
                 for name, group in cohorts.items() if name != "current"}
    for name, sets in seed_sets.items():
        notes.append(f"{name}: paired on {sets['common_seeds']} common seeds"
                     + ("." if sets["seed_sets_equal"] else
                        f" (current {sets['current_seeds']}, {name} {sets['reference_seeds']}"
                        " seeds: the headline numbers of each cohort cover different seed "
                        "sets; compare the paired lines)."))
    for name, part in partial.items():
        notes.append(f"{name.upper()} IS PARTIAL: {part['batches_reported']} of "
                     f"{part['batches']} batches, {part['games_missing']} games without a "
                     "report are not in it.")
    if missing:
        notes.append("BATCHES WITHOUT A REPORT (their games are not in this report; never "
                     "retried): " + json.dumps(missing, sort_keys=True) + ".")
    output = root / "report"
    report = write_report(cohorts, output, scope_note=" ".join([*notes, (
        "Managed fixed pool, argmax; unknown/failed games are excluded per pair and listed, "
        "never retried; the round is never voided."
        + (" Skipped references (different protocol or seeds): "
           + json.dumps(skipped, sort_keys=True) if skipped else ""))]))
    summary: dict[str, Any] = {
        "schema": SCHEMA + "/report", "report_directory": str(output),
        "baseline": baseline, "adapter_pairing": pairing if baseline else None,
        "smoke": smoke, "decision": ("smoke, not a decision result" if smoke
                                     else f"decision size (>= {DECISION_GAMES} games)"),
        "references_not_compared": not_compared, "skipped_references": skipped,
        "batches": ([p.parent.name for p in reference_plans(round_file)]
                    if round_file.name == evaluate.BATCHES else None),
        "batches_missing": missing, "references_partial": partial,
        "batches_not_admitted": not_admitted, "degraded": degraded,
        "submission": submission, "cohorts": {}, "paired": {}}
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
                "mean_delta"),
            **seed_sets[name]}
    save(output / "summary.json", summary)
    try:  # the report above stands even if its record cannot be built
        save(root / "eval-record.json", evaluation_record(
            root, plan, current, report, summary, time.time() - began, plans))
    except Exception as error:  # noqa: BLE001 -- noted loudly by the watcher
        summary["evaluation_record_failed"] = f"{type(error).__name__}: {error}"
    return summary


RECORD_SCHEMA = "spireagent/friday-fast-evaluation-record-1"


def _span(start: Any, end: Any) -> float | None:
    return end - start if isinstance(start, (int, float)) and isinstance(
        end, (int, float)) else None


def evaluation_record(root: Path, plan: dict[str, Any], current: dict[str, Any],
                      report: dict[str, Any], summary: dict[str, Any],
                      report_seconds: float, plans: list[Path] | None = None) -> dict[str, Any]:
    """One round's record: identity, live games, stage seconds, per-seed outcomes, paired.

    Stage seconds: the job's own phases from result.json (execution_and_pipeline_save =
    play + save, observation_report, snapshot, ...), `report` (this report) and
    `ended_to_report` (job finished -> report written: the watcher's polling and grace);
    `Loop.add_watcher_facts` adds `prepare_wall` and `evaluation_queue`. Per-step value/HP
    predictions are not in the trajectories: the evaluated package strips the auxiliary
    heads, so `value_predictions` stays unrecorded (the heads remain in round/export).
    A batched round: `batches` holds each batch's status, games, phases and times; the
    round's process wall is the longest batch's, it started with the first batch and
    `ended_to_report` counts from the last batch's end.
    """
    paths = [p.parent / "result.json" for p in plans] if plans else [
        root / "round/evaluation/result.json"]
    loaded = [json.loads(p.read_bytes()) if p.is_file() else {} for p in paths]
    now = time.time()
    batches = None
    if len(loaded) == 1:
        result = loaded[0]
        timing = result.get("timing") or {}
        phases = timing.get("phases") or {}
    else:
        batches = [{"batch": path.parent.name, "status": one.get("status"),
                    "planned": json.loads((path.parent / "plan.json").read_bytes()).get(
                        "planned"),
                    "phases": (one.get("timing") or {}).get("phases"),
                    **{k: (one.get("timing") or {}).get(k) for k in (
                        "process_wall_seconds", "process_started_at", "process_finished_at")}}
                   for path, one in zip(paths, loaded, strict=True)]
        statuses = {b["status"] for b in batches}
        result = {"status": statuses.pop() if len(statuses) == 1 else "mixed",
                  "stop_requested": any(one.get("stop_requested") for one in loaded)}
        def edge(key: str, pick: Callable[..., Any]) -> Any:
            values = [b[key] for b in batches if isinstance(b[key], (int, float))]
            return pick(values) if values else None
        timing = {"process_wall_seconds": edge("process_wall_seconds", max),
                  "process_started_at": edge("process_started_at", min),
                  "process_finished_at": edge("process_finished_at", max)}
        phases = {}
    stages: dict[str, Any] = {
        **phases,
        "evaluation_process_wall": timing.get("process_wall_seconds"),
        "report": report_seconds,
        "ended_to_report": _span(timing.get("process_finished_at"), now)}
    parallelism = plan.get("parallelism") or {}
    rewards = report["rewards"]["current"]
    official, conservative = rewards.get("by_seed", {}), rewards.get("conservative_by_seed", {})
    return {
        "schema": RECORD_SCHEMA, "written_at": now,
        "process_started_at": timing.get("process_started_at"),
        "suite": {"manifest_sha256": plan["manifest"]["sha256"],
                  "planned": (sum(b["planned"] or 0 for b in batches) if batches
                              else plan.get("planned")),
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
        "batches": batches, "batches_missing": summary.get("batches_missing", []),
        "references_partial": summary.get("references_partial", {}),
        "outcome": summary["cohorts"]["current"],
        "seeds": [{"seed": row["seed"], "status": row["status"], "result": row["result"],
                   "scored": row["scored"], "floor": row["floor"],
                   "boss_cleared_acts": row["boss_cleared_acts"],
                   "elapsed_seconds": row["elapsed_seconds"],
                   "reward": official.get(row["seed"]),
                   "conservative_reward": conservative.get(row["seed"])}
                  for row in current["rows"]],
        "smoke": summary["smoke"], "decision": summary["decision"],
        "degraded": summary["degraded"], "submission": summary["submission"],
        "batches_not_admitted": summary["batches_not_admitted"],
        "adapter_pairing": summary["adapter_pairing"],
        "paired": summary["paired"], "skipped_references": summary["skipped_references"],
        "value_predictions": {"recorded": False,
                              "reason": "evaluated_package_strips_auxiliary_heads"}}


def check_storage(execution: dict[str, Any], peer: dict[str, Any]) -> bool:
    """True when the evaluation jobs need the R2 environment (not shared_local)."""
    if not evaluate.needs_r2(execution) and (
            set(peer) != {"kind", "directory"} or peer.get("kind") != "local"):
        raise ValueError("shared_local_evaluation_requires_a_local_peer_without_archive")
    return evaluate.needs_r2(execution)


def site_submission(path: Path | None) -> dict[str, str]:
    """The evaluation jobs' node class from a site profile (#612): `eval_partition` and
    `eval_constraint` of its [site] table; absent or "" = today's submission."""
    if path is None:
        return {}
    import tomllib

    site = tomllib.loads(path.read_text()).get("site") or {}
    out = {key: site.get(f"eval_{key}") or "" for key in ("partition", "constraint")}
    if out["partition"] and out["partition"] not in PARTITIONS or not all(
            isinstance(value, str) for value in out.values()):
        raise ValueError(f"site_eval_partition_or_constraint_invalid: {out}")
    return {key: value for key, value in out.items() if value}


def one_value(name: str, given: str | None, site: str | None) -> str | None:
    """A flag or the site's value; both given and different is refused (no precedence)."""
    if given and site and given != site:
        raise ValueError(f"evaluation_{name}_given_twice: --{name} {given}, site "
                         f"eval_{name} {site}")
    return given or site


def settings_from(args: argparse.Namespace) -> dict[str, Any]:
    config = json.loads(args.learner_config.read_bytes())
    execution = json.loads(args.execution.read_bytes())
    live = min(args.workers, args.pending_limit)
    baseline = resolve_baseline(args.baseline_plan, args.baseline_sha256, args.baseline_source,
                                execution, args.manifest.resolve(), args.execution.resolve())
    # Only when given: the default (the source parent) keeps earlier state.json settings.
    actor = ({"actor_root": str(args.actor_root.resolve())}
             if getattr(args, "actor_root", None) is not None else {})
    batch = getattr(args, "batch_seeds", None)
    until = getattr(args, "until_file", None)  # only when given: older states unchanged
    actor.update({"until_file": str(until.resolve())} if until is not None else {})
    grace = getattr(args, "final_grace_seconds", None)
    actor.update({"final_grace_seconds": grace} if grace is not None else {})
    games = min(args.seeds, batch or args.seeds)  # per job: each job's wall is its own
    site = site_submission(getattr(args, "site", None))
    # Only when given: today's states (no constraint, default threshold) are unchanged.
    constraint = one_value("constraint", getattr(args, "constraint", None),
                           site.get("constraint"))
    actor.update({"constraint": constraint} if constraint else {})
    share = getattr(args, "degraded_share", None)
    actor.update({"degraded_share": share} if share is not None else {})
    return {**actor, **({"batch_seeds": batch} if batch is not None else {}),
            "learner_config": str(args.learner_config.resolve()),
            "peer": config["peer"], "run": config["run"],
            "training_generation": args.training_generation, "chain": args.chain,
            "execution": str(args.execution.resolve()), "manifest": str(args.manifest.resolve()),
            "runtime_source": str(Path(__file__).resolve().parents[2]),
            "workers": args.workers, "seeds": args.seeds,
            "pending_limit": args.pending_limit, "save_workers": args.save_workers,
            "memory_gb": args.memory_gb if args.memory_gb is not None
            else MEMORY_GB_PER_LIVE * live,
            "expected_steps": args.expected_steps,
            "wall_seconds": args.wall_seconds if args.wall_seconds is not None
            else planned_wall_seconds(games, args.expected_steps, live, args.episode_seconds),
            # --deadline-at: the last evaluation starts while the expected wall (games at
            # the mean steps, without the planned 2x) still fits; the job itself stops
            # admitting games in time for its deadline (evaluate.run).
            "deadline_headroom_seconds": planned_wall_seconds(
                games, max(1, args.expected_steps // 2), live, args.episode_seconds),
            "episode_seconds": args.episode_seconds, "interval": args.interval,
            "max_live": args.max_live,
            "partition": one_value("partition", args.partition,
                                   site.get("partition")) or DEFAULT_PARTITION,
            "prepare_partition": args.prepare_partition,
            "exclude_nodes": args.exclude_node or [], "deadline_at": args.deadline_at,
            "checkpoint": args.checkpoint, "r2": check_storage(execution, config["peer"]),
            "baseline": baseline}


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
    parser.add_argument("--chain", help="only CPs of this fast chain's generations "
                        "(g-fc-CHAIN, g-fc-CHAIN-rN; supervise.py passes it)")
    parser.add_argument("--interval", type=int, default=INTERVAL_SECONDS)
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--seeds", type=int, default=SEEDS,
                        help="games per round: the first N seeds of the manifest")
    parser.add_argument("--batch-seeds", type=int,
                        help="games per job: a round of more --seeds runs as parallel jobs "
                             "of at most this many (e.g. 300 seeds, 100 per job; #591)")
    parser.add_argument("--pending-limit", type=int, default=PENDING_LIMIT,
                        help="live games per job (playing + saving + waiting for a shard)")
    parser.add_argument("--save-workers", type=int, default=SAVE_WORKERS)
    parser.add_argument("--memory-gb", type=int,
                        help=f"job memory (default {MEMORY_GB_PER_LIVE} GB per live game)")
    parser.add_argument("--expected-steps", type=int, default=EXPECTED_STEPS,
                        help="expected mean steps per game; sets the job wall")
    parser.add_argument("--wall-seconds", type=int,
                        help="job wall (default: planned_wall_seconds of games and steps)")
    parser.add_argument("--episode-seconds", type=int, default=EPISODE_SECONDS,
                        help="per-game wall cap (Host preparation + play; a game cut there "
                             "is unknown/censored, never retried; #614)")
    parser.add_argument("--max-live", type=int, default=MAX_LIVE)
    parser.add_argument("--partition", choices=PARTITIONS,
                        help=f"evaluation jobs' partition (default: --site eval_partition, "
                             f"else {DEFAULT_PARTITION})")
    parser.add_argument("--constraint",
                        help="evaluation jobs' Slurm node constraint (a node feature, e.g. the "
                             "fast node class; default: --site eval_constraint, else none)")
    parser.add_argument("--site", type=Path,
                        help="site profile whose eval_partition / eval_constraint place the "
                             "evaluation jobs (#612); a flag that differs from it is refused")
    parser.add_argument("--degraded-share", type=float,
                        help=f"a round above this share of unknown + censored + not-started "
                             f"games is labelled degraded and never a baseline "
                             f"(default {DEGRADED_SHARE})")
    parser.add_argument("--prepare-partition", choices=("smp", "general", "debug"),
                        default="debug")
    parser.add_argument("--exclude-node", action="append")
    parser.add_argument("--deadline-at", type=float)
    parser.add_argument("--checkpoint",
                        help="evaluate exactly this CP artifact id (else the newest CP)")
    parser.add_argument("--actor-root", type=Path,
                        help="directory holding env/, node22.23.4/, game/ and "
                             "dotnet-sdk9.0.117/ for the evaluation jobs, e.g. the release "
                             "the source was built for (default: the source checkout's parent)")
    parser.add_argument("--baseline-plan", type=Path,
                        help="plan.json of the reference round (e.g. the baseline CP's own "
                             "fast_eval round) for the paired report: same Host/Runtime "
                             "pins, initial progress and seed manifest, else refused")
    parser.add_argument("--baseline-sha256", help="the pinned sha256 of --baseline-plan")
    parser.add_argument("--baseline-source", choices=("explicit", "site"), default="explicit",
                        help="how the baseline was chosen (supervise: site eval_baselines)")
    parser.add_argument("--no-baseline", action="store_true",
                        help="no baseline in the report (e.g. the first round on a new Host pin)")
    parser.add_argument("--check", action="store_true",
                        help="run the start checks (manifest, baseline, settings), print the "
                             "settings and exit; nothing is written")
    parser.add_argument("--once", action="store_true",
                        help="one evaluation, then exit once its report is written "
                             "(or it ended without one)")
    parser.add_argument("--until-file", type=Path,
                        help="once this file exists (supervise: the chain's FINAL.json), "
                             "evaluate the final CP if no round has it, wait for every "
                             "started round to finish, then exit (#598); nothing is cancelled")
    parser.add_argument("--final-grace-seconds", type=int,
                        help="with --until-file: exit loudly if the final CP it names is "
                             "still not visible this long after the file was first seen; "
                             "0 or absent = wait until stopped")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--slot-registered", action="store_true",
                        help="a GAME-SLOT line for these Managed games is registered")
    args = parser.parse_args()
    if not args.dry_run and not args.slot_registered:
        parser.error("register a GAME-SLOT line first (working rule 5), then --slot-registered")
    if not 600 <= args.interval <= 86400 or not 1 <= args.max_live <= 4:
        parser.error("interval 600..86400 s, max-live 1..4")
    if not 1 <= args.save_workers <= 4 or not args.save_workers <= args.pending_limit <= 16:
        parser.error("save-workers 1..4, pending-limit save-workers..16 (pipeline maximums)")
    if bool(args.baseline_plan) == bool(args.no_baseline):
        parser.error("exactly one of --baseline-plan or --no-baseline")
    if args.degraded_share is not None and not 0 <= args.degraded_share <= MAX_DEGRADED_SHARE:
        parser.error(f"--degraded-share in [0, {MAX_DEGRADED_SHARE}]")
    if args.constraint is not None and not args.constraint.strip():
        parser.error("--constraint is a node feature expression (omit for none)")
    if args.baseline_sha256 and not args.baseline_plan:
        parser.error("--baseline-sha256 goes with --baseline-plan")
    if any(value is not None and value <= 0 for value in (args.memory_gb, args.wall_seconds)):
        parser.error("--memory-gb and --wall-seconds are positive (omit for the defaults)")
    if not 10 <= args.episode_seconds <= evaluate.MAX_EPISODE_SECONDS:
        parser.error(f"--episode-seconds 10..{evaluate.MAX_EPISODE_SECONDS}")
    if args.wall_seconds is not None and not (
            args.episode_seconds + 180 < args.wall_seconds <= MAX_WALL_SECONDS):
        parser.error(f"--wall-seconds above --episode-seconds + 180, at most {MAX_WALL_SECONDS}")
    one_job = evaluate.MAX_DISPATCH_SLOTS
    if args.batch_seeds is not None and not 1 <= args.batch_seeds <= one_job:
        parser.error(f"--batch-seeds 1..{one_job}: one evaluation job admits at most that many")
    if args.seeds < 1 or args.batch_seeds is None and args.seeds > one_job:
        parser.error(f"--seeds 1..{one_job} in one job; more seeds (e.g. 300) need "
                     "--batch-seeds (e.g. 100: three parallel jobs)")
    try:  # supervise runs --check before generation 0: a refusal stops the chain
        check_manifest(args.manifest.resolve(), args.suite, args.seeds)
        settings = settings_from(args)
        problems = start_problems(settings, json.loads(args.execution.read_bytes()))
    except (ValueError, OSError, KeyError, ImportError) as error:
        problems = [f"{type(error).__name__}: {error}"]
    if problems:  # one line: supervise shows the last stderr line of a refused check
        print("fast_eval check refused: " + " | ".join(problems), file=sys.stderr, flush=True)
        sys.exit(2)
    if args.check:
        print(json.dumps({"check": "ok", "settings": settings}, sort_keys=True), flush=True)
        sys.exit(0)
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
        loop = Loop(settings, output, dry_run=args.dry_run)
        while True:
            loop.tick()
            stages = [e["stage"] for e in loop.state["evaluations"]]
            if args.dry_run or args.once and stages and stages[-1] not in WAITING:
                break
            reason = loop.done()
            if reason is not None:
                loop.loud("evaluation_watcher_done", reason=reason,
                          stages={s: stages.count(s) for s in sorted(set(stages))})
                loop.persist()
                break
            time.sleep(args.poll_seconds)
    print(json.dumps({"evaluations": loop.state["evaluations"]}, sort_keys=True), flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
