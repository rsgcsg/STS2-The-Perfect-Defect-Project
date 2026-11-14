#!/usr/bin/env python3
"""Run a frozen fixed-seed Managed evaluation in one Friday Slurm allocation."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import signal
import sys
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import submit as scheduler
from batch import (
    capacity,
    hashed,
    private_root,
    publish_exclusive,
    read_pinned,
    save,
)

if TYPE_CHECKING:
    from spireagent.workbench.evaluation_ledger import EvaluationLedger


SCHEMA = "spireagent/friday-evaluation-v1"
RETENTION = {"tier": "trajectory", "purpose": "evaluation", "audit_every": 50}
# execution["storage_mode"]: "remote" (default: S3, or a local peer with an S3
# archive) or SHARED_LOCAL (a local shared-disk peer, no archive, no R2).
SHARED_LOCAL = "shared_local"
# The evidence kept next to a finished evaluation (`keep_evidence`): one tar of
# every scratch file not already held in the peer, plus the observation files.
EVIDENCE_SCHEMA = "spireagent/friday-evaluation-evidence-v2"
EVIDENCE_ARCHIVE = "evidence.tar"
REPORT_FILES = ("report-source.json", "report-details.json", "report-cohort.json")


def validate_execution(execution: dict[str, Any], selections: dict[str, str]) -> None:
    from spireagent.storage.config import validate_peer
    from spireagent.workbench.managed_evaluation import storage_configuration
    from spireagent.workbench.managed_retention import (
        pipeline_configuration,
        retention_configuration,
    )

    peer = execution.get("peer", {})
    mode = execution.get("storage_mode", "remote")
    if mode == SHARED_LOCAL:
        # Named mode (lead 11-12): a fast-chain CP evaluated without R2. The
        # learner peer is one local directory on shared disk, no archive; the
        # evaluation jobs read and write it there. Never chosen implicitly.
        directory = peer.get("directory")
        valid = (set(peer) == {"kind", "directory"} and peer["kind"] == "local"
                 and isinstance(directory, str) and Path(directory).is_absolute()
                 and execution.get("store") == directory)
    elif mode == "remote":
        shared = (peer.get("kind") == "local" and "archive" in peer
                  and peer["archive"].get("kind") == "s3"
                  and execution.get("store") == peer.get("directory"))
        valid = shared or execution.get("store") == "s3" and peer.get("kind") == "s3"
    else:
        valid = False
    if (execution.get("schema") != "spireagent/managed-evaluation-execution-v1"
            or execution.get("retention") != RETENTION or not valid):
        raise ValueError("fixed_remote_evaluation_configuration_required")
    validate_peer(peer)
    if (not selections or len(set(selections.values())) != len(selections)
            or set(selections.values()) != set(execution["models"])):
        raise ValueError("exact_distinct_model_selections_required")
    storage_configuration(execution)
    retention_configuration(execution)
    pipeline_configuration(execution)


def prepare(execution_path: Path, output: Path, *, selections: dict[str, str],
            workers: int = 4, wall_seconds: int = 7200, episode_seconds: int = 900,
            manifest_path: Path | None = None, limit: int | None = None,
            seed_prefix: int | None = None, seed_ordinals: list[int] | None = None,
            runtime_source: Path | None = None,
            observation_report: bool = False, unknown_policy: str = "stop",
            deadline_at: float | None = None, memory_gb: int | None = None) -> dict[str, Any]:
    """`memory_gb`: the job's memory request (default 4 GB per worker, as before)."""
    from spireagent.json_boundary import json_bytes
    from spireagent.workbench.managed_retention import pipeline_configuration
    from spireagent.workbench.reference_suite import load_manifest
    from stpd.evaluation.suites import FOUR_AGENT_SUITE_ID, load_suite

    if (type(workers) is not int or not 1 <= workers <= 16
            or type(wall_seconds) is not int or not 300 <= wall_seconds <= 21600
            or type(episode_seconds) is not int or not 10 <= episode_seconds <= 1800
            or wall_seconds <= episode_seconds + 180
            or limit is not None and (type(limit) is not int or not 1 <= limit <= 200)
            or memory_gb is not None and (type(memory_gb) is not int
                                          or not 1 <= memory_gb <= 512)):
        raise ValueError("bounded_allocation_and_episode_limits_required")
    if unknown_policy not in ('stop', 'continue_cleaned'):
        raise ValueError('explicit_evaluation_unknown_policy_required')
    if deadline_at is not None and (type(deadline_at) not in (int, float)
            or not math.isfinite(deadline_at) or deadline_at <= 0):
        raise ValueError('absolute_evaluation_deadline_required')
    if type(observation_report) is not bool or observation_report and len(selections) != 1:
        raise ValueError('single_model_observation_report_required')
    if runtime_source is not None and (not runtime_source.is_absolute()
            or runtime_source.is_symlink() or runtime_source.resolve() != runtime_source
            or not (runtime_source / 'tools/friday/evaluate.py').is_file()):
        raise ValueError('canonical_frozen_runtime_source_required')
    execution_raw = execution_path.read_bytes()
    execution = json.loads(execution_raw)
    validate_execution(execution, selections)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    private_root(output)
    manifest = output / "manifest.json"
    if manifest_path is None:
        value = load_suite("ref-20")
        value["host"] = "Managed"
        manifest.write_bytes(json_bytes(value))
    else:
        manifest.write_bytes(manifest_path.read_bytes())
    _, value = load_manifest(manifest, hashed(manifest))
    if (value["host"] != "Managed" or value["purpose"] == "diagnostic_reference"
            or value["suite_id"] == FOUR_AGENT_SUITE_ID):
        raise ValueError("canonical_fixed_managed_evaluation_manifest_required")
    if seed_prefix is not None and (
            type(seed_prefix) is not int or not 1 <= seed_prefix <= len(value["seeds"])):
        raise ValueError("bounded_canonical_seed_prefix_required")
    if seed_ordinals is not None and (
            seed_prefix is not None or not isinstance(seed_ordinals, list) or not seed_ordinals
            or any(type(n) is not int or not 1 <= n <= len(value['seeds']) for n in seed_ordinals)
            or seed_ordinals != sorted(set(seed_ordinals))):
        raise ValueError('bounded_canonical_seed_ordinals_required')
    planned = (len(seed_ordinals) if seed_ordinals is not None
               else seed_prefix or len(value["seeds"])) * len(selections)
    selected_count = min(limit or planned, planned)
    if selected_count > 200:
        raise ValueError("single_dispatch_maximum200_selected_slots")
    pipeline = pipeline_configuration(execution)
    frozen_execution = output / "execution.json"
    frozen_execution.write_bytes(execution_raw)
    plan = {"schema": SCHEMA,
            "execution": {"path": str(frozen_execution), "sha256": hashed(frozen_execution)},
            "manifest": {"path": str(manifest), "sha256": hashed(manifest)},
            "selections": selections, "workers": workers, "wall_seconds": wall_seconds,
            "memory_gb": memory_gb or 4 * workers,
            "observation_report": observation_report,
            "episode_seconds": episode_seconds, "selected_limit": selected_count,
            "planned": planned, "parallelism": {"requested_workers": workers,
                "pending_limit": pipeline["pending_limit"], "save_workers": pipeline["save_workers"],
                "execution_upper_bound": min(workers, pipeline["pending_limit"])},
            "source_root": str(runtime_source or Path(__file__).resolve().parents[2]),
            **({"seed_prefix": seed_prefix} if seed_prefix is not None else {}),
            **({"seed_ordinals": seed_ordinals} if seed_ordinals is not None else {}),
            **({"unknown_policy": unknown_policy} if unknown_policy != "stop" else {}),
            "target": "friday", "automatic_mac_fallback": False, "retry_unknown": False}
    if deadline_at is not None:
        plan['deadline_at'] = deadline_at
    save(output / "plan.json", plan)
    return plan


def create_ledger(plan: dict[str, Any], directory: Path) -> EvaluationLedger:
    from spireagent.workbench.evaluation_ledger import EvaluationLedger

    execution = read_pinned(plan["execution"])
    read_pinned(plan["manifest"])
    validate_execution(execution, plan["selections"])
    options: dict[str, Any] = ({"seed_prefix": plan["seed_prefix"]}
                               if "seed_prefix" in plan else {})
    if 'seed_ordinals' in plan:
        options['seed_ordinals'] = plan['seed_ordinals']
    return EvaluationLedger.create(directory, Path(plan["manifest"]["path"]),
        plan["manifest"]["sha256"], selections=plan["selections"],
        execution=Path(plan["execution"]["path"]), **options)


def load_plan(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    if (value.get("schema") != SCHEMA or value.get("target") != "friday"
            or value.get("automatic_mac_fallback") is not False
            or value.get("retry_unknown") is not False
            or type(value.get("observation_report", False)) is not bool
            or value.get("unknown_policy", "stop") not in ('stop', 'continue_cleaned')):
        raise ValueError("frozen_friday_evaluation_plan_required")
    validate_execution(read_pinned(value["execution"]), value["selections"])
    if value.get('deadline_at') is not None and (
            type(value['deadline_at']) not in (int, float)
            or not math.isfinite(value['deadline_at']) or value['deadline_at'] <= 0):
        raise ValueError('absolute_evaluation_deadline_required')
    read_pinned(value["manifest"])
    if 'checkpoint_binding' in value:
        binding = read_pinned(value['checkpoint_binding'])
        execution = read_pinned(value['execution'])
        models = list(execution['models'].values())
        if (len(models) != 1 or models[0]['artifact_id'] != binding['model_artifact_id']
                or json.loads((Path(models[0]['package_directory']) / 'model.json').read_bytes())[
                    'package_sha256'] != binding['model_sha256']):
            raise ValueError('checkpoint_evaluation_model_binding_changed')
    return value


def prepare_learning_curve_manifest(game_directory: Path, output: Path,
                                    suite: str | None = None) -> dict[str, Any]:
    """Use the exact shipped version, including its v prefix; never guess a version.

    `suite`: the fixed A0 100 (default) or the fixed A10 50, each frozen and reported
    on its own.
    """
    from stpd.evaluation.suites import (
        RL3_LEARNING_CURVE_SUITE_ID,
        RL5_A10_CURVE_SUITE_ID,
        rl3_learning_curve_manifest,
        rl5_a10_curve_manifest,
    )

    builders = {RL3_LEARNING_CURVE_SUITE_ID: rl3_learning_curve_manifest,
                RL5_A10_CURVE_SUITE_ID: rl5_a10_curve_manifest}
    if (suite or RL3_LEARNING_CURVE_SUITE_ID) not in builders:
        raise ValueError('fixed_learning_curve_suite_required')
    release = json.loads((game_directory / 'release_info.json').read_bytes())
    manifest = builders[suite or RL3_LEARNING_CURVE_SUITE_ID](release['version'])
    save(output, manifest)
    return manifest


def prepare_checkpoint(configuration: Path, checkpoint: str, execution_path: Path,
                       manifest: Path, output: Path, *, workers: int = 4,
                       seeds: int = 50, wall_seconds: int = 7200,
                       episode_seconds: int = 900,
                       construction_package: Path | None = None,
                       deadline_at: float | None = None,
                       pending_limit: int | None = None, save_workers: int | None = None,
                       memory_gb: int | None = None) -> dict[str, Any]:
    """`pending_limit`/`save_workers` (given together) replace the execution template's
    pipeline; `memory_gb` is the evaluation job's memory request (fast_eval settings)."""
    from spireagent.source import source_identity
    from spireagent.storage.config import open_peer
    from spireagent.workbench.global_evidence_store import publish_detached_p1_model
    from stpd.evaluation.rl_checkpoint import export_checkpoint

    if type(seeds) is not int or not 50 <= seeds <= 100:
        raise ValueError('fixed_fifty_to_one_hundred_evaluation_seeds_required')
    config = json.loads(configuration.read_bytes())
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    store = open_peer(config['peer'])
    binding = export_checkpoint(store, checkpoint, output / 'export',
                                construction_package=construction_package)
    if binding['run'] != config['run']:
        raise ValueError('checkpoint_evaluation_run_changed')
    producer = source_identity(Path(__file__).resolve().parents[2] / 'python')
    model = publish_detached_p1_model(store, Path(binding['package_directory']), producer)
    binding['model_artifact_id'] = model.artifact_id
    save(output / 'checkpoint-binding.json', binding)
    execution = json.loads(execution_path.read_bytes())
    execution.update(peer=config['peer'], store=config['peer'].get('directory', 's3'),
        producer=producer.to_dict(), retention=RETENTION,
        models={'checkpoint': {'artifact_id': model.artifact_id,
                              'package_directory': binding['package_directory']}})
    if pending_limit is not None or save_workers is not None:
        execution['pipeline'] = {'pending_limit': pending_limit, 'save_workers': save_workers}
    save(output / 'input-execution.json', execution)
    plan = prepare(output / 'input-execution.json', output / 'evaluation',
        selections={'checkpoint': 'checkpoint'}, workers=workers, wall_seconds=wall_seconds,
        episode_seconds=episode_seconds, manifest_path=manifest, seed_prefix=seeds,
        runtime_source=Path(__file__).resolve().parents[2], observation_report=True,
        unknown_policy='continue_cleaned', deadline_at=deadline_at, memory_gb=memory_gb)
    plan['checkpoint_binding'] = {'path': str(output / 'checkpoint-binding.json'),
                                 'sha256': hashed(output / 'checkpoint-binding.json')}
    save(output / 'evaluation/plan.json', plan)
    return plan


def launcher(plan_path: Path, plan: dict[str, Any], *, program: str = 'evaluate.py',
             credentials: bool = True) -> str:
    """Nonsensitive user launcher; the program loads the user's private credentials.

    ``credentials=False`` only for an execution validated as SHARED_LOCAL (a local
    shared-disk peer, no archive): nothing in that job opens R2, so the R2
    environment is neither required nor loaded. Every other mode keeps it.
    """
    if program not in {'evaluate.py', 'quick.py'}:
        raise ValueError('normal_evaluation_entrypoint_required')
    source = Path(plan["source_root"])
    actor = source.parent
    replacements = {"__ACTOR__": shlex.quote(str(actor)),
                    "__SOURCE__": shlex.quote(str(source)),
                    "__PLAN__": shlex.quote(str(plan_path)), "__SLOT_MODE__": "evaluation",
                    "__PROGRAM__": program}
    script = r'''#!/bin/bash
set +x
set -euo pipefail
umask 077
actor_root=__ACTOR__
source_root=__SOURCE__
__CREDENTIALS__if ! type module >/dev/null 2>&1; then set +u; source /etc/profile; set -u; fi
module load python/3.11.5-gcccore-13.2.0 nodejs/20.9.0-gcccore-13.2.0
test -x "$actor_root/node22.23.4/bin/node" || { echo 'pinned Node22.23.4 required' >&2; exit 2; }
export PATH="$actor_root/env/bin:$actor_root/dotnet-sdk9.0.117:/data/$(id -un)/dotnet9:$PATH"
export PATH="$actor_root/node22.23.4/bin:$PATH"
export PYTHONPATH="$source_root/python:$source_root/components/evidence"
export STS2_GAME_DIR="$actor_root/game"
export DOTNET_PROCESSOR_COUNT=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
export FRIDAY_INFERENCE_THREADS=${FRIDAY_INFERENCE_THREADS:-1}
exec "$actor_root/env/bin/python" "$source_root/tools/friday/allocation.py" \
  --mode __SLOT_MODE__ --configuration __PLAN__ --source-root "$source_root" -- \
  "$actor_root/env/bin/python" "$source_root/tools/friday/__PROGRAM__" run --plan __PLAN__
'''
    for token, value in replacements.items():
        script = script.replace(token, value)
    return script.replace('__CREDENTIALS__', R2_ENVIRONMENT if credentials else
                          '# shared_local storage: no R2 environment is read\n')


R2_ENVIRONMENT = r'''credentials=${FRIDAY_R2_ENV:-/data/$(id -un)/.config/spireagent/friday-r2.env}
[[ -f "$credentials" && ! -L "$credentials" \
   && $(stat -c '%a' "$credentials") = 600 \
   && $(stat -c '%u' "$credentials") = $(id -u) ]] || {
  echo 'Friday evaluation needs the user-owned600 R2 environment' >&2; exit 2;
}
set -a
source "$credentials"
set +a
'''


def needs_r2(execution: dict[str, Any]) -> bool:
    """Only a validated SHARED_LOCAL execution runs without the R2 environment."""
    return execution.get('storage_mode', 'remote') != SHARED_LOCAL


def submit(plan_path: Path, *, constraint: str | None = None,
           dry_run: bool = False, begin: str | None = None,
           partition: str = 'smp', exclude_nodes: list[str] | None = None,
           nodelist: str | None = None) -> dict[str, Any]:
    if partition not in {'smp', 'general', 'debug'}:
        raise ValueError('supported_Friday_CPU_partition_required')
    # One named node (OPS node probe); a set would need every node for a 1-node job.
    if nodelist is not None and (not isinstance(nodelist, str)
                                 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,62}',
                                                     nodelist)):
        raise ValueError('single_node_name_required')
    # Slurm owns the timer; this is an earliest start, not a resource reservation.
    if begin is not None:
        parsed = datetime.fromisoformat(begin)
        if parsed.tzinfo is None or parsed.timestamp() <= time.time():
            raise ValueError("future_begin_with_explicit_timezone_required")
        begin = parsed.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    plan = load_plan(plan_path)
    if plan.get('deadline_at') is not None and time.time() >= plan['deadline_at'] - 900:
        raise ValueError('absolute_deadline_prevents_new_evaluation_submission')
    root = private_root(plan_path.parent)
    with scheduler.user_submission_lock():
        job_name = 'spireagent-eval-' + hashlib.sha256(str(plan_path).encode()).hexdigest()[:12]
        existing = scheduler.output(['squeue', '-h', '-u', scheduler.username(), '-n',
                                     job_name, '-o', '%i'])
        if existing:
            raise ValueError('existing_evaluation_plan_must_drain_before_new_submission')
        if (root / 'owner.json').exists() or (root / 'STOP').exists():
            raise ValueError('previous_evaluation_requires_original_owner_reconciliation')
        if (root / 'submission-intent.json').exists():
            raise ValueError('previous_evaluation_submission_requires_owner_reconciliation')
        idle = sum(int(line.split('/')[1]) for line in scheduler.output(
            ['sinfo', '-h', '-p', partition, '-o', '%C']).splitlines())
        queued = len(scheduler.output(
            ['squeue', '-h', '-p', partition, '-t', 'PD', '-o', '%i']).splitlines())
        if capacity(idle, queued, plan['workers']) < plan['workers']:
            raise ValueError('requested_evaluation_workers_exceed_current_fair_share')
        script = root / 'launch.sh'
        script.write_text(launcher(plan_path, plan,
                                   credentials=needs_r2(read_pinned(plan['execution']))),
                          encoding='utf-8')
        script.chmod(0o700)
        logs = private_root(root / 'slurm')
        minutes = (plan['wall_seconds'] + 300 + 59) // 60
        if partition == 'debug' and minutes > 60 or partition == 'general' and minutes > 1440:
            raise ValueError('evaluation_exceeds_CPU_partition_wall_limit')
        command = ['sbatch', '--parsable', '--partition=' + partition, '--nodes=1', '--ntasks=1',
                   f"--cpus-per-task={plan['workers']}",
                   f"--mem={plan.get('memory_gb', 4 * plan['workers'])}G",
                   f'--time={minutes}', '--no-requeue', '--signal=B:USR1@300',
                   f'--job-name={job_name}', f'--output={logs}/%j.out',
                   f'--error={logs}/%j.err']
        if constraint:
            command.append('--constraint=' + constraint)
        if exclude_nodes:
            command.append('--exclude=' + ','.join(exclude_nodes))
        if nodelist is not None:
            command.append('--nodelist=' + nodelist)
        if begin is not None:
            command.append('--begin=' + begin)
        command.append(str(script))
        result = {'schema': 'spireagent/friday-evaluation-submit-v1',
                  'idle_smp_cores': idle, 'queued_smp_jobs': queued, 'partition': partition,
                  'cores': plan['workers'], 'command': command, 'dry_run': dry_run,
                  'earliest_begin_local': begin, 'scheduler_start_guaranteed': False}
        if not dry_run:
            result['submission_requested_at'] = time.time()
            save(root / 'submission-intent.json', result)
            result['job_id'] = scheduler.output(command)
            result['submission_confirmed_at'] = time.time()
            save(root / 'submission.json', result)
        return result


class StopOnUncertain:
    """Delegate unchanged owner execution and halt NEW admission on uncertainty."""
    def __init__(self, delegate: Any, stop: Path, *, unknown_policy: str = 'stop'):
        if unknown_policy not in ('stop', 'continue_cleaned'):
            raise ValueError('explicit_evaluation_unknown_policy_required')
        self.delegate, self.stop, self.unknown_policy = delegate, stop, unknown_policy

    def inspect(self, row: dict[str, Any]) -> None:
        # Cleanup is not a proof of data integrity: under the strict policy a typed
        # failure stops admission. A cleaned one under continue_cleaned is one
        # excluded pair, recorded as unknown and never scored (user 11-06).
        integrity_failed = row.get('execution_reason') == 'typed_original_verification_failed'
        if integrity_failed and not (self.unknown_policy == 'continue_cleaned'
                                     and row.get('status') == 'unknown'
                                     and row.get('cleanup_accepted') is True):
            self.stop.touch()
            return
        # A closed game whose save failed is one excluded pair (on the audit slot,
        # an unscored cohort); the ledger retries that save once at batch end
        # (card RL5 3E). Admission continues under either policy unless the game
        # itself was uncertain.
        if (row.get('status') == 'save_failed' and row.get('cleanup_accepted') is True
                and (self.unknown_policy == 'continue_cleaned'
                     or row.get('execution_status') != 'unknown')):
            return
        if (self.unknown_policy == 'continue_cleaned'
                and row.get('cleanup_accepted') is True):
            return
        if (row.get('status') in {'unknown', 'save_failed'}
                or row.get('execution_status') == 'unknown'
                or row.get('world_mutation_offered') and not row.get('cleanup_accepted')):
            self.stop.touch()

    def __call__(self, row, manifest, artifacts, checkpoint):
        try:
            self.delegate(row, manifest, artifacts, checkpoint)
            self.inspect(row)
        except Exception:  # Preserve the original failure and stop new admission.
            self.stop.touch()
            raise

    def preserve_report(self, path):
        report = self.delegate.preserve_report(path)
        for row in report['episodes']:
            self.inspect(row)
        return report

    def finalize_trajectories(self, paths):
        results = self.delegate.finalize_trajectories(paths)
        for _path, report in results:
            for row in report['episodes']:
                self.inspect(row)
        return results


def prepare_observation_report(plan: dict[str, Any], summary: dict[str, Any],
                               scratch: Path) -> dict[str, Any]:
    """Expensive verified trajectory reads stay in the evaluation allocation."""
    from spireagent.json_boundary import json_bytes
    from spireagent.storage.config import open_peer
    from spireagent.workbench.global_evidence_store import materialize_global_evidence
    from spireagent.workbench.managed_trajectory_store import read_shard
    from stpd.evaluation.rl_observation import load_cohort, trajectory_details
    from stpd.p1.trajectory import extract_trajectory

    slots = summary['ledger_report']['slots']
    execution = read_pinned(plan['execution'])
    store = open_peer(execution['peer'])
    wanted = {slot['run_id']: slot for slot in slots if slot.get('run_id') is not None}
    episodes = []
    readback = scratch / 'report-readback'
    readback.mkdir(mode=0o700, exist_ok=True)
    for shard in sorted({slot['trajectory_shard_id'] for slot in slots
                         if slot.get('trajectory_shard_id')}):
        for episode in read_shard(store, shard, scratch=readback):
            if episode['run_id'] not in wanted:
                continue
            if episode['identity']['seed'] != wanted[episode['run_id']]['seed']:
                raise ValueError('evaluation_detail_seed_binding_mismatch')
            episodes.append(trajectory_details(episode))
    # Historical/full-peer evaluation batches have no trajectory shard. Reuse
    # the normal by-ID typed reader; never replay a World or upgrade a failed row.
    seen = {episode['run_id'] for episode in episodes}
    for slot in slots:
        if (slot.get('run_id') in seen or slot.get('status') != 'finished'
                or not slot.get('stored_evidence_id')):
            continue
        original = Path(slot['report']['path'])
        if not original.exists() and summary.get('scratch_retained'):
            original = retained_original(Path(plan['result_root']), summary['scratch_retained'],
                                         original, readback)
        if hashed(original) != slot['report']['sha256']:
            raise ValueError('original_evaluation_report_changed')
        row = json.loads(original.read_bytes())['episodes'][0]
        if (row['run_id'] != slot['run_id'] or row['seed'] != slot['seed']
                or row.get('stored_evidence_id') != slot['stored_evidence_id']):
            raise ValueError('original_evaluation_slot_binding_changed')
        ref = row['save_job']
        job_path = original.parent / ref['path']
        if hashed(job_path) != ref['sha256']:
            raise ValueError('original_evaluation_save_job_changed')
        job = json.loads(job_path.read_bytes())
        with materialize_global_evidence(store, slot['stored_evidence_id'], expected=job['expected'],
                scratch=readback, read_concurrency=4) as verified:
            identity = {key: job[key] for key in ('producer', 'host_pin', 'runtime_pin',
                                                'model_artifact_id')}
            identity.update(model_sha256=job['expected']['agent_artifact_sha256'],
                input_spec=verified.agent_manifest['input']['input_spec'], seed=row['seed'],
                character=row['scenario']['character'], ascension=row['scenario']['ascension'],
                retention=row['retention'], behavior_policy=row.get('behavior_policy') or {
                    'mode': 'argmax', 'temperature': None, 'uniform_mix': 0},
                initial_progress_sha256=execution['initial_progress']['progress_sha256'])
            trajectory = extract_trajectory(verified, identity=identity,
                outcome=row['agent_task_outcome'], status=row['execution_status'],
                reason=row['execution_reason'], max_floor=row['game_metrics']['max_observed_floor'])
            episodes.append(trajectory_details(trajectory))
    source = scratch / 'report-source.json'
    details = scratch / 'report-details.json'
    cohort = scratch / 'report-cohort.json'
    save(source, summary)
    save(details, {'episodes': episodes})
    value = load_cohort(source, details=details)
    save(cohort, value)
    return {'schema': 'spireagent/evaluation-observation-files-1',
            'ledger_report_sha256': hashlib.sha256(json_bytes(summary['ledger_report'])).hexdigest(),
            'files': {name: {'path': path.name, 'sha256': hashed(path)} for name, path in
                      (('source', source), ('details', details), ('cohort', cohort))}}


def read_observation_report(result_path: Path, plan_path: Path, *,
                            projection: dict[str, str] | None = None) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Read the normal saved report, preserving its original ledger qualification."""
    from spireagent.json_boundary import json_bytes

    plan = load_plan(plan_path)
    original = json.loads(result_path.read_bytes())
    metadata = read_pinned(projection) if projection is not None else original.get('observation_report')
    root = Path(projection['path']).parent if projection is not None else result_path.parent / 'evidence'
    if projection is not None and metadata.get('original_result_sha256') != hashed(result_path):
        raise ValueError('observation_projection_original_result_changed')
    if (original.get('plan_sha256') != hashed(plan_path)
            or metadata is None
            or metadata.get('schema') != 'spireagent/evaluation-observation-files-1'
            or metadata.get('ledger_report_sha256') != hashlib.sha256(
                json_bytes(original['ledger_report'])).hexdigest()):
        raise ValueError('evaluation_job_observation_report_required')
    files = {}
    for name in ('source', 'details', 'cohort'):
        reference = metadata['files'][name]
        if Path(reference['path']).name != reference['path']:
            raise ValueError('evaluation_report_file_name_required')
        files[name] = read_pinned({'path': str(root / reference['path']),
                                  'sha256': reference['sha256']})
    if (files['source']['ledger_report'] != original['ledger_report']
            or files['source']['plan_sha256'] != original['plan_sha256']
            or files['cohort']['source_sha256'] != metadata['files']['source']['sha256']
            or files['cohort']['details_sha256'] != metadata['files']['details']['sha256']):
        raise ValueError('evaluation_report_original_binding_mismatch')
    return plan, original, files


def project_observation_report(result_path: Path, plan_path: Path, output: Path) -> dict[str, Any]:
    """Read-only enrichment of a completed original batch, including full-peer rows."""
    plan = load_plan(plan_path)
    summary = json.loads(result_path.read_bytes())
    if summary['plan_sha256'] != hashed(plan_path):
        raise ValueError('original_evaluation_plan_changed')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    metadata = prepare_observation_report({**plan, 'result_root': str(result_path.parent)},
                                          summary, output)
    metadata['original_result_sha256'] = hashed(result_path)
    from spireagent.source import source_identity
    metadata['projector'] = source_identity(Path(__file__).resolve().parents[2] / 'python').to_dict()
    save(output / 'observation-projection.json', metadata)
    return metadata


def fixed_baseline(batches: list[dict[str, Any]], binding: dict[str, Any],
                   manifest: dict[str, str], *, seeds: int = 50) -> dict[str, Any]:
    """Project original admitted rows; continue only seeds whose World was never offered.

    This is the existing multi-batch evaluation projection, not an admission or
    ledger rewrite. Failed/unknown rows remain unscored with their original status.
    """
    import quick
    from spireagent.json_boundary import json_bytes

    frozen = read_pinned(manifest)
    expected = frozen['seeds'][:seeds]
    selected: dict[str, dict[str, Any]] = {}
    slots: dict[str, dict[str, Any]] = {}
    protocol = None
    character = ascension = None
    original_sources = []
    for batch in batches:
        for key in ('plan', 'result', 'snapshot'):
            read_pinned(batch[key])
        if not originals_retained(read_pinned(batch['snapshot'])):
            raise ValueError('baseline_full_snapshot_readback_required')
        plan, result, files = read_observation_report(
            Path(batch['result']['path']), Path(batch['plan']['path']),
            projection=batch.get('observation_projection'))
        execution = read_pinned(plan['execution'])
        models = list(execution['models'].values())
        if (plan['manifest']['sha256'] != manifest['sha256'] or len(models) != 1
                or models[0]['artifact_id'] != binding['model_artifact_id']
                or result.get('error_type') or not result['ledger_report']['score_admitted']):
            raise ValueError('exact_original_baseline_checkpoint_and_admissions_required')
        package = json.loads((Path(models[0]['package_directory']) / 'model.json').read_bytes())
        if package['package_sha256'] != binding['model_sha256']:
            raise ValueError('baseline_checkpoint_package_changed')
        for episode in files['details']['episodes']:
            identity = episode['trajectory']['identity']
            if (identity['model_artifact_id'] != binding['model_artifact_id']
                    or identity['model_sha256'] != binding['model_sha256']):
                raise ValueError('baseline_trajectory_checkpoint_binding_changed')
        cohort = files['cohort']
        observed = cohort['protocol_observed']
        required = {'host_pin', 'runtime_pin', 'input_spec', 'initial_progress_sha256',
                    'behavior_policy'}
        if cohort['floor_n']:
            if not required <= observed.keys() or observed['behavior_policy']['mode'] != 'argmax':
                raise ValueError('complete_argmax_evaluation_protocol_required')
            if protocol is not None and observed != protocol:
                raise ValueError('baseline_observed_protocol_changed')
            protocol = observed
        character, ascension = cohort['character'], cohort['ascension']
        by_seed = {row['seed']: row for row in result['ledger_report']['slots']}
        for row in cohort['rows']:
            seed = row['seed']
            slot = by_seed[seed]
            if seed not in expected:
                raise ValueError('baseline_outside_frozen_seed_set')
            prior = slots.get(seed)
            attempted = (slot['status'] != 'not_started' or slot.get('world_mutation_offered')
                         or slot.get('run_id') is not None)
            prior_attempted = prior is not None and (prior['status'] != 'not_started'
                or prior.get('world_mutation_offered') or prior.get('run_id') is not None)
            if prior_attempted and attempted:
                raise ValueError('baseline_World_already_offered_never_retry')
            if prior is None or not prior_attempted:
                selected[seed], slots[seed] = row, slot
        original_sources.append(batch)
    if (set(selected) != set(expected) or protocol is None
            or character is None or ascension is None):
        raise ValueError('full_frozen_baseline_denominator_required')
    rows = [selected[seed] for seed in expected]
    cohort = quick.aggregate(rows, 'baseline', protocol, character, ascension)
    complete = all(slot['status'] in {'finished', 'unknown', 'save_failed'}
                   and slot.get('cleanup_accepted') is True for slot in slots.values())
    cohort.update(source_sha256=hashlib.sha256(json_bytes(original_sources)).hexdigest(),
        status_counts={status: sum(r['status'] == status for r in rows)
                       for status in sorted({r['status'] for r in rows})})
    return {'cohort': cohort, 'offers_accounted': complete, 'source_batches': original_sources,
            'frozen_seeds': expected,
            'excluded': [{'seed': r['seed'], 'status': r['status']} for r in rows if not r['scored']],
            'qualification': 'Original per-row admission only; no failed row is upgraded.'}


def peer_held(slot: dict[str, Any], scratch: Path) -> dict[str, Any] | None:
    """The peer receipts of one normal scored game, or None (its files are then all kept).

    A `trajectory_and_full_peer` game was saved to the peer twice, each read back in
    full when saved (managed_pipeline): its sealed run directory as global evidence
    (typed verification) and its trajectory in a shard. Receipts are read by their
    pinned sha256; any mismatch or read error keeps every file of that game.
    """
    if (slot.get('status') != 'finished' or not slot.get('report')
            or slot.get('preservation_route') != 'trajectory_and_full_peer'):
        return None
    attempt = Path(slot['report']['path']).parent
    try:
        row = read_pinned(slot['report'])['episodes'][0]

        def receipt(name: str) -> Any:
            return read_pinned({'path': str(attempt / row[name]['path']),
                                'sha256': row[name]['sha256']})
        full, shard = receipt('preservation'), receipt('trajectory_preservation')
        readback = full['peer_readback']
        if not (row['status'] == 'finished' and row['run_id'] == slot['run_id']
                and full['status'] == 'completed' and full['run_id'] == row['run_id']
                and full['artifact_id'] == row['stored_evidence_id'] == slot['stored_evidence_id']
                and readback['full_readback'] is True and readback['typed_verification'] == 'pass'
                and shard['status'] == 'completed' and shard['full_readback'] is True
                and shard['trajectory_verification'] == 'pass'
                and shard['run_id'] == row['run_id']
                and shard['artifact_id'] == row['trajectory_shard_id']
                == slot['trajectory_shard_id']
                and shard['episode_sha256'] == row['trajectory']['sha256']
                and shard['original_content_id'] == readback['content_id']
                and (attempt / 'runs' / row['run_id']).is_dir()):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return {'attempt': attempt.relative_to(scratch).as_posix(), 'run_id': row['run_id'],
            'stored_evidence_id': full['artifact_id'], 'content_id': readback['content_id'],
            'trajectory_shard_id': shard['artifact_id'],
            'episode_sha256': shard['episode_sha256'], 'trajectory': row['trajectory']['path'],
            'preservation': row['preservation'],
            'trajectory_preservation': row['trajectory_preservation'],
            'full_readback': True, 'typed_verification': 'pass',
            'trajectory_verification': 'pass'}


class _HashingWriter:
    """The bytes handed to the shared disk, hashed as they are written."""

    def __init__(self, stream: Any) -> None:
        self.stream, self.digest, self.size = stream, hashlib.sha256(), 0

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        self.size += len(data)
        return int(self.stream.write(data))

    def tell(self) -> int:
        return int(self.stream.tell())


def _durable_replace(temporary: Path, path: Path) -> None:
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def keep_evidence(scratch: Path, root: Path, summary: dict[str, Any],
                  execution: dict[str, Any]) -> dict[str, Any]:
    """Keep every original of this evaluation once (replaces the per-file snapshot).

    Not copied again: the run directory and trajectory of each `peer_held` game (in
    the peer, each with its full readback receipt named here), and each attempt's
    copy of the evaluated model when it is byte-identical to the model package the
    plan already keeps. Everything else under the scratch, including every file of
    any other game (unknown, failed, abnormal), goes into `evidence/evidence.tar`:
    written once, its sha256 taken while writing and again from the shared disk.
    The observation files stay plain in `evidence/` (read by name and bound by
    sha256 in result.json). `tar -xf evidence/evidence.tar -C evidence` restores the
    old copy minus the peer-held files.
    """
    evidence = private_root(root / 'evidence')
    held = [h for h in (peer_held(slot, scratch) for slot in summary['ledger_report']['slots'])
            if h is not None]
    skipped = {scratch / h['attempt'] / 'runs' / h['run_id'] for h in held}
    skipped |= {scratch / h['attempt'] / h['trajectory'] for h in held}
    models = {hashed(path) for model in execution['models'].values()
              if (path := Path(model.get('package_directory') or '/nonexistent')
                  / 'model.tensor-tree').is_file()}
    plain = {}
    for name in REPORT_FILES:
        if (scratch / name).is_file():
            data = (scratch / name).read_bytes()
            temporary = evidence / (name + '.tmp')
            with temporary.open('wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            _durable_replace(temporary, evidence / name)
            plain[name] = hashlib.sha256(data).hexdigest()
            if hashed(evidence / name) != plain[name]:
                raise ValueError('persistent_snapshot_readback_failed')
    copies = files = 0
    temporary = evidence / (EVIDENCE_ARCHIVE + '.tmp')
    with temporary.open('wb') as raw:
        writer = _HashingWriter(raw)
        with tarfile.open(fileobj=writer, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for directory, folders, names in os.walk(scratch):
                base = Path(directory)
                for name in folders + names:
                    if (base / name).is_symlink():
                        raise ValueError('scratch_symlink_not_supported')
                folders[:] = sorted(f for f in folders if base / f not in skipped)
                for name in sorted(names):
                    path = base / name
                    if path in skipped or base == scratch and name in REPORT_FILES:
                        continue
                    if (name == 'model.tensor-tree' and base.name == 'registration'
                            and hashed(path) in models):
                        copies += 1
                        continue
                    archive.add(path, arcname=path.relative_to(scratch).as_posix(),
                                recursive=False)
                    files += 1
        raw.flush()
        os.fsync(raw.fileno())
    _durable_replace(temporary, evidence / EVIDENCE_ARCHIVE)
    written = writer.digest.hexdigest()
    if hashed(evidence / EVIDENCE_ARCHIVE) != written:
        raise ValueError('persistent_snapshot_readback_failed')
    return {'schema': EVIDENCE_SCHEMA,
            'archive': {'path': EVIDENCE_ARCHIVE, 'sha256': written, 'bytes': writer.size,
                        'files': files},
            'plain': plain, 'peer_held': held,
            'model_copies': {'package_sha256': sorted(models), 'not_copied': copies},
            'bytes': writer.size + sum((evidence / name).stat().st_size for name in plain),
            'full_readback': True}


def originals_retained(receipt: dict[str, Any]) -> bool:
    """Every original of a finished evaluation is kept and was read back in full.

    v1 (`batch.snapshot`): every scratch file copied and compared. v2
    (`keep_evidence`): the tar and observation files read back from the shared disk,
    and each game left in the peer named with its full typed peer and shard readback.
    """
    if receipt.get('full_readback') is not True:
        return False
    if receipt.get('schema') != EVIDENCE_SCHEMA:
        return True
    return all(h.get('full_readback') is True and h.get('typed_verification') == 'pass'
               and h.get('trajectory_verification') == 'pass' for h in receipt['peer_held'])


def retained_original(result_root: Path, scratch_retained: str, path: Path,
                      workspace: Path) -> Path:
    """One attempt file after its scratch is gone: the v1 copy, or extracted from the v2 tar
    (its whole attempt directory, into `workspace`)."""
    relative = path.relative_to(scratch_retained)
    copied = result_root / 'evidence' / relative
    archive = result_root / 'evidence' / EVIDENCE_ARCHIVE
    if copied.exists() or not archive.is_file():
        return copied
    prefix = relative.parent.as_posix() + '/'
    with tarfile.open(archive) as stream:
        stream.extractall(workspace, members=[m for m in stream if m.name.startswith(prefix)],
                          filter='data')
    return workspace / relative


def run(plan_path: Path, *, on_stop: Callable[[], None] | None = None) -> dict[str, Any]:
    from spireagent.workbench.managed_pipeline import PipelinedManagedExecutor
    from spireagent.workbench.reference_suite import SuiteLimits

    process_started_at = time.time()
    process_began = time.monotonic()
    plan = load_plan(plan_path)
    if (sys.platform != 'linux' or not os.environ.get('SLURM_JOB_ID')
            or int(os.environ.get('SLURM_CPUS_PER_TASK', '0')) < plan['workers']):
        raise ValueError('Friday_single_node_allocation_required_no_mac_fallback')
    root = private_root(plan_path.parent)
    if (root / 'STOP').exists():
        return {'status': 'stopped', 'reason': 'stop_requested', 'new_games': 0}
    # One plan gets one allocation owner. Lost jobs are reconciled, never rerun as new games.
    if (root / 'owner.json').exists():
        raise FileExistsError(root / 'owner.json')
    scratch = Path(tempfile.mkdtemp(prefix='spireagent-friday-evaluation-')).resolve()
    scratch.chmod(0o700)
    publish_exclusive(root / 'owner.json',
        {'job_id': os.environ['SLURM_JOB_ID'], 'scratch': str(scratch),
         'plan_sha256': hashed(plan_path)})
    ledger = create_ledger(plan, scratch / 'ledger')
    ledger_prepared_seconds = time.monotonic() - process_began
    selected = tuple(row['slot_id'] for row in ledger.plan['slots'][:plan['selected_limit']])
    save(root / 'admission.json', {'original_ledger_plan': ledger.plan,
        'selected_slot_ids': selected, 'unknown_retry': False})
    # All fixed seeds/models/audit slots/selection are durable before the first native offer.
    stopping = threading.Event()
    ledger_stop = ledger.directory / 'STOP'
    remaining_wall = plan['wall_seconds']
    if plan.get('deadline_at') is not None:
        remaining_wall = min(remaining_wall, plan['deadline_at'] - time.time() - 900)
        if remaining_wall <= plan['episode_seconds'] + 180:
            ledger_stop.touch()
    world_closed = False

    def stop(_number=None, _frame=None):
        if not world_closed:
            ledger_stop.touch()
        if on_stop is not None:
            on_stop()

    def observe_stop():
        while not stopping.wait(1):
            if ((root / 'STOP').exists() or plan.get('deadline_at') is not None
                    and time.time() >= plan['deadline_at'] - 900):
                stop()
                return

    previous = {number: signal.signal(number, stop)
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
    watcher = threading.Thread(target=observe_stop, daemon=True)
    watcher.start()
    executor = None
    began = time.monotonic()
    error_type = error_code = None
    result = None
    phases: dict[str, float] = {'ledger_preparation': ledger_prepared_seconds}
    try:
        execution = read_pinned(plan['execution'])
        phase_began = time.monotonic()
        executor = PipelinedManagedExecutor(execution, episode_seconds=plan['episode_seconds'],
                                           cleanup_seconds=120)
        save(scratch / 'preparation.json', executor.preparation)
        phases['executor_preparation'] = time.monotonic() - phase_began
        limits = SuiteLimits(150, plan['episode_seconds'], 120,
                            max(1, remaining_wall - plan['episode_seconds'] - 180), 1, 30)
        phase_began = time.monotonic()
        result = ledger.run_managed(workers=plan['workers'], max_episodes=len(selected),
            limits=limits, executor=StopOnUncertain(executor, ledger_stop,
                unknown_policy=plan.get('unknown_policy', 'stop')), slot_ids=selected)
        phases['execution_and_pipeline_save'] = time.monotonic() - phase_began
    except Exception as error:  # noqa: BLE001 -- the existing attempt is retained, never retried.
        error_type, error_code = type(error).__name__, getattr(error, 'code', None)
        stop()
    finally:
        phase_began = time.monotonic()
        if executor is not None:
            try:
                executor.close()
            except Exception as error:  # noqa: BLE001 -- retain close failures with the same attempt.
                if error_type is None:
                    error_type, error_code = type(error).__name__, getattr(error, 'code', None)
        stopping.set()
        watcher.join(timeout=2)
        phases['executor_close'] = time.monotonic() - phase_began
        if ledger_stop.exists() and on_stop is not None:
            # StopOnUncertain can touch the original ledger flag directly,
            # including a typed failure in the final offered slot.
            on_stop()
        world_closed = True
        report = ledger.report()
        summary = {'schema': 'spireagent/friday-evaluation-result-v1',
            'status': 'failed' if error_type else (result or {}).get('status', 'failed'),
            'plan_sha256': hashed(plan_path), 'job_id': os.environ['SLURM_JOB_ID'],
            'elapsed_seconds': time.monotonic() - began, 'dispatch': result,
            'error_type': error_type, 'error_code': error_code, 'ledger_report': report,
            'stop_requested': ledger_stop.exists(),
            'selected_slot_ids': selected, 'scratch_retained': str(scratch),
            'full_planned_denominator': plan['planned'], 'retry_unknown': False}
        summary['timing'] = {'process_started_at': process_started_at, 'phases': phases}
        save(scratch / 'result.json', summary)
        try:
            if plan.get('observation_report', False):
                phase_began = time.monotonic()
                try:
                    summary['observation_report'] = prepare_observation_report(plan, summary, scratch)
                except Exception as error:  # noqa: BLE001 -- preserve original results on report failure
                    summary['observation_report_error'] = {
                        'error_type': type(error).__name__, 'error_code': getattr(error, 'code', None)}
                phases['observation_report'] = time.monotonic() - phase_began
                save(scratch / 'result.json', summary)
            phase_began = time.monotonic()
            receipt = keep_evidence(scratch, root, summary, read_pinned(plan['execution']))
            phases['snapshot'] = time.monotonic() - phase_began
            save(root / 'snapshot.json', receipt)
            summary['timing'].update(process_finished_at=time.time(),
                                     process_wall_seconds=time.monotonic() - process_began)
            # The original elapsed_seconds remains backward compatible. Full
            # process time includes ledger/preparation/snapshot; Slurm adds queue.
            save(root / 'result.json', summary)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    manifest_parser = commands.add_parser('prepare-manifest')
    manifest_parser.add_argument('--game-directory', type=Path, required=True)
    manifest_parser.add_argument('--output', type=Path, required=True)
    manifest_parser.add_argument('--suite', help='fixed suite id (default: the A0 100 seeds)')
    projection_parser = commands.add_parser('project-report')
    projection_parser.add_argument('--result', type=Path, required=True)
    projection_parser.add_argument('--plan', type=Path, required=True)
    projection_parser.add_argument('--output', type=Path, required=True)
    export_parser = commands.add_parser('export-checkpoint')
    export_parser.add_argument('--learner-config', type=Path, required=True)
    export_parser.add_argument('--checkpoint', required=True)
    export_parser.add_argument('--output', type=Path, required=True)
    export_parser.add_argument('--construction-package', type=Path)
    check_parser = commands.add_parser(
        'check-checkpoint', help='dry mode: every export check on one CP; writes nothing')
    check_parser.add_argument('--learner-config', type=Path, required=True)
    check_parser.add_argument('--checkpoint', required=True)
    check_parser.add_argument('--training-generation')
    cp_parser = commands.add_parser('prepare-checkpoint')
    cp_parser.add_argument('--learner-config', type=Path, required=True)
    cp_parser.add_argument('--checkpoint', required=True)
    cp_parser.add_argument('--execution', type=Path, required=True)
    cp_parser.add_argument('--manifest', type=Path, required=True)
    cp_parser.add_argument('--output', type=Path, required=True)
    cp_parser.add_argument('--workers', type=int, default=4)
    cp_parser.add_argument('--seeds', type=int, default=50)
    cp_parser.add_argument('--wall-seconds', type=int, default=7200)
    cp_parser.add_argument('--episode-seconds', type=int, default=900)
    cp_parser.add_argument('--deadline-at', type=float)
    cp_parser.add_argument('--construction-package', type=Path)
    cp_parser.add_argument('--pending-limit', type=int,
                           help='with --save-workers: the save pipeline of this evaluation')
    cp_parser.add_argument('--save-workers', type=int)
    cp_parser.add_argument('--memory-gb', type=int,
                           help='job memory request (default 4 GB per worker)')
    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('--execution', type=Path, required=True)
    prepare_parser.add_argument('--output', type=Path, required=True)
    prepare_parser.add_argument('--manifest', type=Path)
    prepare_parser.add_argument('--selection', action='append', required=True,
                                help='model label=existing execution selection ID')
    prepare_parser.add_argument('--workers', type=int, default=4)
    prepare_parser.add_argument('--runtime-source', type=Path)
    prepare_parser.add_argument('--wall-seconds', type=int, default=7200)
    prepare_parser.add_argument('--episode-seconds', type=int, default=900)
    prepare_parser.add_argument('--limit', type=int)
    prepare_parser.add_argument('--unknown-policy', choices=('stop', 'continue_cleaned'),
                                default='stop', help='continue only cleaned unknown; never retry')
    prepare_parser.add_argument('--observation-report', action='store_true',
                                help='prepare verified trajectory details/cohort in this allocation')
    prepare_parser.add_argument('--seed-prefix', type=int,
                                help='freeze this original canonical seed prefix in a NEW ledger')
    prepare_parser.add_argument('--seed-ordinals', type=int, nargs='+',
                                help='freeze increasing original 1-based ordinals; keep full manifest')
    for name in ('submit', 'run'):
        command = commands.add_parser(name)
        command.add_argument('--plan', type=Path, required=True)
        if name == 'submit':
            command.add_argument('--partition', choices=('smp', 'general', 'debug'), default='smp')
            command.add_argument('--exclude-node', action='append', dest='exclude_nodes')
            command.add_argument('--nodelist', help='one node name (OPS node probe)')
            command.add_argument('--constraint')
            command.add_argument('--dry-run', action='store_true')
            command.add_argument('--begin', help='earliest start ISO8601 with timezone; no reservation')
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'prepare-manifest':
        result = prepare_learning_curve_manifest(args.game_directory, args.output, args.suite)
    elif args.command == 'project-report':
        result = project_observation_report(args.result, args.plan, args.output)
    elif args.command == 'check-checkpoint':
        from spireagent.storage.config import open_peer
        from stpd.evaluation.rl_checkpoint import inspect_checkpoint
        config = json.loads(args.learner_config.read_bytes())
        result = inspect_checkpoint(open_peer(config['peer']), args.checkpoint,
                                    run=config['run'], generation=args.training_generation)
    elif args.command == 'export-checkpoint':
        from spireagent.source import source_identity
        from spireagent.storage.config import open_peer
        from spireagent.workbench.global_evidence_store import publish_detached_p1_model
        from stpd.evaluation.rl_checkpoint import export_checkpoint
        config = json.loads(args.learner_config.read_bytes())
        store = open_peer(config['peer'])
        result = export_checkpoint(store, args.checkpoint, args.output.resolve(),
                                   construction_package=args.construction_package)
        manifest = publish_detached_p1_model(store, Path(result['package_directory']),
            source_identity(Path(__file__).resolve().parents[2] / 'python'))
        result['model_artifact_id'] = manifest.artifact_id
        save(args.output / 'checkpoint-binding.json', result)
    elif args.command == 'prepare-checkpoint':
        result = prepare_checkpoint(args.learner_config, args.checkpoint, args.execution,
            args.manifest, args.output.resolve(), workers=args.workers, seeds=args.seeds,
            wall_seconds=args.wall_seconds, episode_seconds=args.episode_seconds,
            construction_package=args.construction_package, deadline_at=args.deadline_at,
            pending_limit=args.pending_limit, save_workers=args.save_workers,
            memory_gb=args.memory_gb)
    elif args.command == 'prepare':
        pairs = [value.split('=', 1) for value in args.selection]
        if any(len(pair) != 2 or not all(pair) for pair in pairs):
            parser.error('--selection requires label=selection')
        selections = dict(pairs)
        if len(selections) != len(pairs):
            parser.error('duplicate selection labels')
        result = prepare(args.execution.resolve(), args.output.resolve(), selections=selections,
            workers=args.workers, wall_seconds=args.wall_seconds,
            episode_seconds=args.episode_seconds,
            manifest_path=args.manifest.resolve() if args.manifest else None, limit=args.limit,
            seed_prefix=args.seed_prefix, seed_ordinals=args.seed_ordinals,
            runtime_source=args.runtime_source,
            observation_report=args.observation_report, unknown_policy=args.unknown_policy)
    elif args.command == 'submit':
        result = submit(args.plan.resolve(), constraint=args.constraint,
                        dry_run=args.dry_run, begin=args.begin,
                        partition=args.partition, exclude_nodes=args.exclude_nodes,
                        nodelist=args.nodelist)
    else:
        result = run(args.plan.resolve())
    print(json.dumps(result, sort_keys=True))
    if args.command == 'run' and result.get('error_type'):
        raise SystemExit(1)
    if args.command == 'check-checkpoint' and not result['evaluable']:
        raise SystemExit(3)


if __name__ == '__main__':
    main()
