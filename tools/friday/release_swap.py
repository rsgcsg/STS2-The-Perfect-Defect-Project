#!/usr/bin/env python3
"""Prepare a verified long-run continuation on a new release. It never launches anything.

    release_swap.py prepare --sha <develop SHA> --bundle <git bundle> \
        --predecessor <stopped watch work dir> --name <cNN> [window options]
    release_swap.py prepare --dry-run --release-dir <existing release> --predecessor ... --name ...

Card U4 E (11-07): the three failed 11-06/07 release swaps all came from re-freezing
identities by hand. One run of this command does the following:

  1 build    a release from the SHA, the way the DEPLOY scripts did (clone of the predecessor
             release, bundle fetch, detached clean HEAD, env links, ENV file). The Host/Runtime
             stage is reused, so a SHA that changes components/contracts/apps/locks is refused.
  2 freeze   every code-bytes identity in the actor interpreter: execution producer and
             agent_adapter, learner compiler (input_spec and vocabulary must not change),
             printed as an old-vs-new table with the reason for each change.
  3 inputs   execution and learner json files (the resume CP is the one the predecessor
             watcher would pick: watch.SegmentWatcher.select_checkpoint).
  4 watch    the watch config. Same window (previous_watch_state), new window
             (previous_window_state) or close-only is chosen from the predecessor's stop,
             budget and deadline.
  5 verify   read_segment_config, the continuation/new-window rule (watch.checkpoint_link,
             watch.require_watch_continuation, watch.require_completed_previous_window), the
             agent-child identity check with its negative control, and one real Managed game
             through evaluate.py prepare-checkpoint/prepare/submit.
  6 ready    the staging dir is renamed into place, re-checked, and READY.json is written
             last. The checklist and the exact launch command are printed.

Every rule is the watcher's own code, imported from the release that will run it. A step
that fails stops the run with a typed message, and its staging dir is renamed to a dotted
.FAILED dir. Re-running with the same inputs rebuilds the deterministic files and compares
them with READY.json; nothing is rewritten. ENV contents, credentials and the R2 endpoint
are never printed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

REUSED_STAGE_PATHS = ('components', 'contracts', 'apps', 'uv.lock', 'python/uv.lock',
                      'package-lock.json')
DEADLINE_HEADROOM = 600  # watch.tick_segment: deadline_at - 600 ends with run_budget_complete
MAX_WINDOW_SECONDS = 72000  # read_segment_config bound
HOLDER_MARGIN = 1800  # c13: deadline_at = holder end - 30 min
PYTHON_CHANGE_HINTS = ('python/stpd', 'python/spireagent')
DETERMINISTIC = ('decision.json', 'identities.json', 'checkpoint.json', 'execution.json',
                 'learner.json', 'watch.json')
CREDENTIALS = '${FRIDAY_R2_ENV:-/data/$(id -un)/.config/spireagent/friday-r2.env}'
WRAPPER = r'''set -eo pipefail
source "$SWAP_ENV_FILE" >/dev/null
c=''' + CREDENTIALS + r'''
[[ -f "$c" && ! -L "$c" && $(stat -c '%a' "$c") = 600 && $(stat -c '%u' "$c") = $(id -u) ]] || {
  echo "release_swap: private R2 credential file unavailable" >&2; exit 97; }
set -a; source "$c" >/dev/null; set +a
exec python "$SWAP_TOOL" "$@"
'''


class SwapError(Exception):
    """A typed refusal: ``code`` is stable, ``detail`` is safe to print."""

    def __init__(self, code: str, detail: str = '') -> None:
        super().__init__(code)
        self.code, self.detail = code, detail


def safe(text: Any) -> str:
    return re.sub(r'https?://\S+', '<url>', str(text))[:600]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    """Same bytes as batch.canonical/json_bytes (the watcher pins configs by them)."""
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
            + '\n').encode('utf-8')


def write(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(canonical(value))
    os.replace(temporary, path)
    return sha256(path)


def stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')


# ---------------------------------------------------------------- decision logic (pure)

def decide(previous: dict[str, Any], prior: dict[str, Any], *, now: float, margin: float,
           completed_stops: frozenset[str], active_terminal: bool | None = None
           ) -> dict[str, Any]:
    """Which continuation the predecessor watch admits.

    new_window   its window ended (run_budget_complete / absolute_deadline): link with
                 previous_window_state; require_completed_previous_window decides the rest.
    same_window  it stopped for any other reason with budget and deadline left: continue
                 with previous_watch_state, launched before deadline_at - 600 - margin.
    close_window it stopped for another reason and its budget is used up or deadline_at - 600
                 has passed. A same-window watch on the predecessor's own release then
                 stops on its first tick (run_budget_complete / absolute_deadline) before
                 start_attempt, so no learner or actor starts; launch it to close the window,
                 then prepare again against it (c12c -> c12d -> c13, without the guesswork).
    Between deadline_at - 600 - margin and deadline_at - 600 neither is safe (a watch launched
    there would still start an attempt), so that is refused with the time to re-run.

    A stopped watch may still name its active attempt (c13 crashed with attempt-0001 open).
    A same-window continuation reconciles it on its first tick, so that is accepted when the
    attempt's learner job is terminal in sacct (``active_terminal``, watch.job_accounting).
    """
    stop = previous.get('stop')
    if stop is None:
        raise SwapError('predecessor_watch_not_stopped',
                        'state has no stop; the watcher may still be running')
    if previous.get('active') is not None and active_terminal is not True:
        raise SwapError('predecessor_watch_still_active',
                        f"attempt index {previous['active']} has a learner job that is not "
                        'terminal; wait for it or reconcile first')
    if stop in completed_stops:
        if previous.get('active') is not None:
            raise SwapError('completed_window_with_active_attempt',
                            'require_completed_previous_window refuses it: reconcile first')
        return {'mode': 'new_window', 'predecessor_stop': stop,
                'why': f'predecessor window ended with {stop}'}
    deadline = prior['deadline_at'] - DEADLINE_HEADROOM
    budget_left = prior['training_seconds'] - previous['training_seconds']
    if budget_left <= 0:
        return {'mode': 'close_window', 'predecessor_stop': stop,
                'why': f'{stop}, and the window budget is used up'}
    if now >= deadline:
        return {'mode': 'close_window', 'predecessor_stop': stop,
                'why': f'{stop}, and deadline_at - 600 ({stamp(deadline)}) has passed'}
    if deadline - now <= margin:
        raise SwapError('too_close_to_the_window_deadline',
                        f'a same-window watch could not train {margin:.0f} s, and launched '
                        f'now it would still start an attempt; re-run after {stamp(deadline)} '
                        'to prepare a close-only watch')
    return {'mode': 'same_window', 'predecessor_stop': stop, 'launch_by': deadline - margin,
            'why': f'{stop}, with {budget_left:.0f} s of budget and the deadline still ahead'}


def chain_training_seconds(previous: dict[str, Any],
                           read_pinned: Callable[[dict[str, str]], Any]) -> list[float]:
    """Training seconds of each window in the previous_window_state chain, oldest first."""
    seconds, state = [], previous
    for _ in range(64):
        seconds.append(float(state['training_seconds']))
        link = read_pinned(state['configuration']).get('previous_window_state')
        if not link:
            return seconds[::-1]
        state = read_pinned(link)
    raise SwapError('window_chain_too_long')


def next_budget_id(prior_id: str) -> str:
    match = re.fullmatch(r'(.*-window)(\d+)', prior_id)
    return f'{match[1]}{int(match[2]) + 1}' if match else f'{prior_id}-window2'


def new_window_fields(prior: dict[str, Any], windows: list[float], *, now: float,
                      margin: float, deadline_at: float | None, budget_id: str | None,
                      training_seconds: int | None,
                      total_training_seconds: int | None) -> dict[str, Any]:
    if deadline_at is None:
        raise SwapError('new_window_deadline_required',
                        'pass --deadline-at or a RUNNING --held-job to derive it from')
    if deadline_at - DEADLINE_HEADROOM - margin <= now:
        raise SwapError('new_window_deadline_too_close',
                        f'a new window must start before deadline_at - 600 - margin '
                        f'({stamp(deadline_at - DEADLINE_HEADROOM - margin)})')
    budget = budget_id or next_budget_id(prior['budget_id'])
    if budget == prior['budget_id']:
        raise SwapError('new_window_budget_id_must_differ')
    consumed = sum(windows)
    if training_seconds is None:
        if total_training_seconds is None:
            raise SwapError('new_window_training_seconds_required',
                            'pass --training-seconds or --total-training-seconds')
        training_seconds = min(MAX_WINDOW_SECONDS, round(total_training_seconds - consumed))
        why = (f'min(72000, total {total_training_seconds} - consumed {consumed:.0f} '
               f'over {len(windows)} window(s))')
    else:
        why = 'given'
    if not 600 <= training_seconds <= MAX_WINDOW_SECONDS:
        raise SwapError('total_training_target_reached' if training_seconds < 600
                        else 'window_training_seconds_out_of_bounds', f'{training_seconds} s')
    return {'budget_id': budget, 'deadline_at': float(deadline_at),
            'training_seconds': int(training_seconds), 'launch_by':
            deadline_at - DEADLINE_HEADROOM - margin, 'training_seconds_reason': why,
            'consumed_before_window': consumed}


def attempts_needing_reconciliation(previous: dict[str, Any], dataplane: bool,
                                    exists: Callable[[Path], bool]) -> list[int]:
    """Attempts whose lifecycle cursor a continuation would poll over a dead socket.

    watch._socket_event_rows reads learner/control-wal-final.json once a learner is gone;
    without it (c10, attempt-0009) the new watcher stops with dataplane_events_unavailable.
    """
    if not dataplane:
        return []
    return [n + 1 for n, a in enumerate(previous.get('attempts', []))
            if a.get('actor_plans') and not a.get('socket_events_closed')
            and not exists(Path(a['root']) / 'learner/control-wal-final.json')]


def _short(value: Any) -> str:
    if isinstance(value, str):
        return value[:12]
    if isinstance(value, dict):
        for key in ('code_sha256', 'source_revision', 'compiler_sha256'):
            if key in value:
                return f'{key}={str(value[key])[:12]}'
    return hashlib.sha256(canonical(value)).hexdigest()[:12]


def identity_rows(old_execution: dict[str, Any], old_learner: dict[str, Any],
                  new: dict[str, Any], changed_python: list[str]) -> list[dict[str, Any]]:
    """Old-vs-new table of every frozen code-bytes identity, with the reason for a change."""
    hint = (f"{len(changed_python)} python file(s) differ, e.g. {', '.join(changed_python[:4])}"
            if changed_python else 'no python file differs')
    old_c = old_learner['segments']['input_compiler_identity']
    new_c = new['compiler']
    old_p, new_p = old_execution['producer'], new['producer']
    rows = [
        ('execution.agent_adapter', old_execution['agent_adapter'], new['agent_adapter'],
         f'adapter closure code bytes changed ({hint})'),
        ('execution.producer', old_p, new_p,
         f"source_revision {str(old_p.get('source_revision'))[:8]} -> "
         f"{str(new_p.get('source_revision'))[:8]}"
         + ('; uv.lock changed' if old_p.get('uv_lock_sha256') != new_p.get('uv_lock_sha256')
            else '')),
        ('learner.segments.input_compiler_identity.compiler_sha256',
         old_c['compiler_sha256'], new_c['compiler_sha256'],
         f'compiler_digest changed: encoder/codec code bytes, uv.lock or '
         f'torch/tokenizers/safetensors version ({hint})'),
        ('learner.segments.input_compiler_identity.input_spec', old_c['input_spec'],
         new_c['input_spec'], 'model input changed'),
        ('learner.segments.input_compiler_identity.vocabulary_sha256',
         old_c['vocabulary_sha256'], new_c['vocabulary_sha256'], 'vocabulary changed'),
    ]
    table = [{'identity': name, 'old': old, 'new': value, 'changed': old != value,
              'reason': reason if old != value else 'unchanged'}
             for name, old, value, reason in rows]
    model_input = [r['identity'] for r in table[3:] if r['changed']]
    if model_input:
        raise SwapError('model_input_changed',
                        f'{model_input}: a new model input is a new learning run, not a swap')
    return table


def render_identities(rows: list[dict[str, Any]]) -> str:
    lines = [f"{'identity':58} {'old':28} {'new':28} reason"]
    for row in rows:
        lines.append(f"{row['identity']:58} {_short(row['old']):28} {_short(row['new']):28} "
                     f"{row['reason']}")
    return '\n'.join(lines)


def build_execution(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    return {**old, 'agent_adapter': new['agent_adapter'], 'producer': new['producer']}


def build_learner(base: dict[str, Any], compiler: dict[str, Any],
                  cp: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(base)
    value['segments']['input_compiler_identity'] = compiler
    value['resume_checkpoint'] = cp['checkpoint_artifact_id']
    value['resume_source'] = cp['producer']  # the watcher rebinds it to the CP producer anyway
    return value


def build_watch(mode: str, prior: dict[str, Any], *, previous_ref: dict[str, str],
                release: Path | None, execution: Path | None, learner: Path | None,
                cp_id: str | None, held_job: str | None, exclude: list[str],
                auto_excluded: list[str], window: dict[str, Any] | None,
                acknowledgements: dict[str, str]) -> dict[str, Any]:
    cfg = copy.deepcopy(prior)
    if held_job:
        cfg['held_job_id'] = held_job
    if mode == 'new_window':
        # Acknowledgements name events of their own budget (read_segment_config).
        cfg.pop('acknowledged_integrity_events', None)
    if acknowledgements:
        cfg['acknowledged_integrity_events'] = {
            **cfg.get('acknowledged_integrity_events', {}), **acknowledgements}
    if mode == 'close_window':
        # Unchanged release: it only closes the window; no actor or learner starts.
        cfg['previous_watch_state'] = previous_ref
        return cfg
    assert release and execution and learner and cp_id
    cfg['execution'] = str(execution)
    cfg['runtime_source'] = str(release / 'source')
    old_release = str(Path(prior['runtime_source']).parent) + '/'
    if prior['learner_python'].startswith(old_release):  # same links in the new release
        cfg['learner_python'] = str(release) + '/' + prior['learner_python'][len(old_release):]
    cfg['learner_config'] = str(learner)
    if cfg.get('evaluation') is not None:
        cfg['evaluation']['execution'] = str(execution)
        cfg['evaluation']['initial_checkpoint'] = cp_id  # read_segment_config: = resume CP
    extra = set(exclude)
    if mode == 'same_window':
        # previous_window_state stays as it is (#391 refuses a continuation that drops it);
        # the predecessor state keeps its auto-excluded nodes.
        cfg['previous_watch_state'] = previous_ref
    else:
        assert window is not None
        cfg.pop('previous_watch_state', None)
        cfg['previous_window_state'] = previous_ref
        cfg['budget_id'] = window['budget_id']
        cfg['deadline_at'] = window['deadline_at']
        cfg['training_seconds'] = window['training_seconds']
        extra |= set(auto_excluded)  # a new window starts with a new state (doc: not inherited)
    cfg['exclude_nodes'] = sorted(set(cfg.get('exclude_nodes', [])) | extra)
    return cfg


def launch_command(cfg: dict[str, Any], env_file: Path, watch_file: Path, work: Path,
                   driver: Path) -> str:
    return (f"cd {cfg['runtime_source']} && source {env_file} && source \"{CREDENTIALS}\" && "
            f"nohup python tools/friday/watch.py --config {watch_file} --work {work} "
            f"> {driver} 2>&1 < /dev/null &")


def checklist(*, decision: dict[str, Any], rows: list[dict[str, Any]] | None,
              cp: dict[str, Any] | None, cfg: dict[str, Any], verification: dict[str, Any],
              command: str, ready: Path, dry_run: bool, name: str) -> str:
    out = [f'# Release swap {name}: {decision["mode"]}' + (' (DRY RUN: do not launch)'
                                                           if dry_run else ''),
           f'- why: {decision["why"]}',
           f'- budget {cfg["budget_id"]}, training_seconds {cfg["training_seconds"]}, '
           f'deadline_at {stamp(cfg["deadline_at"])}']
    if decision['mode'] == 'close_window':
        out.append('- close-only: runs on the predecessor release and stops on its first tick '
                   '(no learner, no actor); then prepare the new window against it')
    if decision.get('launch_by'):
        out.append(f'- [ ] launch before {stamp(decision["launch_by"])} '
                   '(deadline_at - 600 s - margin); later it only closes the window')
    if rows:
        changed = [r['identity'] for r in rows if r['changed']]
        out.append(f'- identities re-frozen; changed: {changed or "none"}')
    if cp:
        budget = cp.get('operating_budget') or {}
        out.append(f"- resume CP {cp['checkpoint_artifact_id'][:16]} update {cp['updates']} "
                   f"generation {cp['training_generation']} budget {budget.get('id')}")
    for key, value in verification.items():
        out.append(f'- verified {key}: {value}')
    out += [f'- [ ] {ready} exists (written last; no READY = not prepared)',
            f"- [ ] holder {cfg.get('held_job_id')} RUNNING (squeue), at most 2 GPU cards held",
            '- [ ] no other watcher holds this run; predecessor work dir left untouched',
            f"- [ ] exclude_nodes reviewed ({len(cfg.get('exclude_nodes', []))} nodes)",
            '- [ ] write launch-intent-<name>.json (at, reason, retry=false, config_sha256)',
            '- [ ] after launch: first-CP check (step-walls.jsonl / checkpoint-pauses.jsonl)',
            '', 'Launch (lead only):', command]
    return '\n'.join(out) + '\n'


# ------------------------------------------------------------- Friday side effects

def run(command: list[str], *, cwd: Path | None = None, check: bool = True,
        **kwargs: Any) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, **kwargs)
    if check and result.returncode != 0:
        raise SwapError('command_failed', f'{command[0]} {command[1:3]} rc={result.returncode}: '
                        f'{safe(result.stderr.strip()[-300:])}')
    return result


def git(source: Path, *args: str, check: bool = True) -> str:
    return run(['git', '-C', str(source), *args], check=check).stdout.strip()


def release_name(old_release: Path, sha: str) -> str:
    match = re.fullmatch(r'(.*-)[0-9a-f]{8}', old_release.name)
    if match is None:
        raise SwapError('release_directory_name_unrecognized', old_release.name)
    return match[1] + sha[:8]


def release_links(old_release: Path) -> dict[str, str]:
    links = {}
    for entry in sorted(old_release.iterdir()):
        if entry.name == 'source':
            continue
        if not entry.is_symlink():
            raise SwapError('release_entry_not_a_link', entry.name)
        links[entry.name] = str(entry.resolve())
    return links


def check_release(release: Path, sha: str, links: dict[str, str]) -> None:
    head = git(release / 'source', 'rev-parse', 'HEAD')
    if head != sha:
        raise SwapError('existing_release_has_other_head', f'{release.name}: {head[:12]}')
    if git(release / 'source', 'status', '--porcelain'):
        raise SwapError('existing_release_source_not_clean', release.name)
    actual = {e.name: str(e.resolve()) for e in release.iterdir() if e.name != 'source'}
    if actual != links:
        raise SwapError('existing_release_links_differ', release.name)


def build_release(old_release: Path, sha: str, bundle: Path | None) -> tuple[Path, bool]:
    """DEPLOY-795622dc/R14 pattern. Returns (release, reused)."""
    new = old_release.parent / release_name(old_release, sha)
    links = release_links(old_release)
    old_head = git(old_release / 'source', 'rev-parse', 'HEAD')
    if new.exists():
        check_release(new, sha, links)
        if not (new / 'source/tools/friday/release_swap.py').is_file():
            raise SwapError('release_predates_release_swap', sha[:12])
        return new, True
    staging = old_release.parent / f'.{new.name}.staging-{os.getpid()}'
    try:
        staging.mkdir()
        source = staging / 'source'
        run(['git', 'clone', '--quiet', '--no-hardlinks', str(old_release / 'source'),
             str(source)])
        if bundle is not None:
            run(['git', '-C', str(source), 'bundle', 'verify', '--quiet', str(bundle)])
            git(source, 'fetch', '--quiet', str(bundle), '+refs/*:refs/remotes/swap-bundle/*')
        if git(source, 'cat-file', '-t', sha, check=False) != 'commit':
            raise SwapError('sha_not_in_bundle_or_release', f'{sha[:12]}: pass --bundle')
        git(source, '-c', 'advice.detachedHead=false', 'checkout', '--quiet', '--detach', sha)
        if git(source, 'status', '--porcelain'):
            raise SwapError('release_source_not_clean')
        changed = git(source, 'diff', '--name-only', old_head, sha, '--', *REUSED_STAGE_PATHS)
        if changed:
            files = changed.splitlines()
            raise SwapError('host_stage_rebuild_required',
                            f'{len(files)} file(s) under {"/".join(REUSED_STAGE_PATHS)} differ '
                            f'from {old_head[:8]} (e.g. {", ".join(files[:4])}); this tool '
                            'only reuses the Host/Runtime stage and env, build those first')
        if not (source / 'tools/friday/release_swap.py').is_file():
            raise SwapError('release_predates_release_swap', sha[:12])
        for name, target in links.items():
            (staging / name).symlink_to(target)
        os.rename(staging, new)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return new, False


def write_env(env_dir: Path, old_release: Path, new_release: Path) -> tuple[Path, bool]:
    """ENV-<short>.sh = the predecessor's with only the release path changed; never printed."""
    old_file = env_dir / f'ENV-{old_release.name[-8:]}.sh'
    new_file = env_dir / f'ENV-{new_release.name[-8:]}.sh'
    if not old_file.is_file():
        raise SwapError('predecessor_env_file_missing', old_file.name)
    old_text = old_file.read_text()
    needle = f'releases/{old_release.name}'
    if needle not in old_text:
        raise SwapError('predecessor_env_file_does_not_name_its_release', old_file.name)
    text = old_text.replace(needle, f'releases/{new_release.name}')
    if new_file.exists():
        if new_file.read_text() != text:
            raise SwapError('existing_env_file_differs', new_file.name)
        return new_file, True
    temporary = new_file.with_name(new_file.name + '.tmp')
    temporary.write_text(text)
    shutil.copymode(old_file, temporary)
    os.replace(temporary, new_file)
    return new_file, False


def holder_end(job: str) -> tuple[str, float | None]:
    out = run(['squeue', '-h', '-j', job, '-o', '%T|%e'], check=False).stdout.strip()
    if not out:
        return 'absent', None
    state, end = out.splitlines()[0].split('|')
    try:
        return state, datetime.strptime(end, '%Y-%m-%dT%H:%M:%S').timestamp()
    except ValueError:
        return state, None


def predecessor_checkpoint(watch: Any, prior: dict[str, Any], previous: dict[str, Any],
                           work: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The CP the predecessor watcher's next attempt would resume from.

    watch.SegmentWatcher.select_checkpoint itself, on an owner that is never constructed:
    __init__ would publish the run deadline and freeze a work dir. Telemetry stays local.
    """
    owner = watch.SegmentWatcher.__new__(watch.SegmentWatcher)
    owner.config, owner.state, owner.work = prior, copy.deepcopy(previous), work
    owner.exchange = watch.Exchange(prior['peer'], prior['run'])
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    notes: list[dict[str, Any]] = []
    owner.telemetry = lambda kind, **fields: notes.append({'kind': kind, **fields})
    receipt = owner.select_checkpoint()
    return {k: v for k, v in receipt.items() if k != 'at'}, notes  # 'at' is read time


def find_registration(previous: dict[str, Any],
                      read_pinned: Callable[[dict[str, str]], Any]) -> Path:
    """An original actor registration (vocabulary + agent binding) that kept its model file.

    Newer actor retention drops the model from registrations, so this walks the
    predecessor's attempts, then each linked earlier window, newest first. The model's age
    does not matter: the check is about code identity, and the vocabulary is compared with
    the learner's own vocabulary_sha256 in step 2.
    """
    state: dict[str, Any] | None = previous
    for _ in range(16):
        assert state is not None
        for attempt in reversed(state.get('attempts', [])):
            actors = Path(attempt['root']) / 'actors/actors'
            if not actors.is_dir():
                continue
            for actor in sorted(actors.iterdir())[:4]:
                hits = sorted((actor / 'evidence/actor/ledgers').glob(
                    '*/ledger/attempts/*/private/registration/config.json'))[:40]
                for hit in hits:
                    package = json.loads(hit.read_bytes()).get('package_path', '')
                    if (hit.parent / Path(package).name).is_file():
                        return hit.parent
        link = read_pinned(state['configuration']).get('previous_window_state')
        state = read_pinned(link) if link else None
        if state is None:
            break
    raise SwapError('no_actor_registration_with_model_found', 'pass --registration')


def actor_environment(release: Path, tmp: Path, adapter_keys: list[str]) -> dict[str, str]:
    """The agent child's environment: Policy Runtime agentProcessEnvironment (PATH, TMPDIR,
    PYTHONPATH) plus the execution's adapter_environment_keys. PYTHONDONTWRITEBYTECODE is the
    one addition: it keeps a release in use free of new __pycache__ files."""
    tmp.mkdir(parents=True, exist_ok=True)
    defaults = {'FRIDAY_INFERENCE_THREADS': '1'}
    return {'PATH': os.environ['PATH'], 'TMPDIR': str(tmp),
            'PYTHONPATH': f'{release}/source/python:{release}/source/components/evidence',
            'PYTHONDONTWRITEBYTECODE': '1',
            **{key: os.environ.get(key, defaults.get(key, '')) for key in adapter_keys}}


def freeze_identities(python: str, release: Path, registration: Path, tmp: Path,
                      adapter_keys: list[str]) -> dict[str, Any]:
    result = run([python, '-P', str(Path(__file__).resolve()), '_identities',
                  '--vocabulary-file', str(registration / 'local-inference.json'),
                  '--release-source', str(release / 'source')],
                 env=actor_environment(release, tmp, adapter_keys), check=False, timeout=600)
    if result.returncode != 0:
        raise SwapError('identity_freeze_failed', safe(result.stderr.strip()[-400:]))
    return json.loads(result.stdout.strip().splitlines()[-1])


def identities_main(args: argparse.Namespace) -> None:
    """Runs in the actor interpreter, as the Runtime-spawned child and the executor would."""
    from spireagent.source import source_identity
    from spireagent.workbench import managed_evaluation
    from stpd.p1 import export
    from stpd.p1.frame_compiler import FrameCompiler

    if Path(managed_evaluation.ROOT).resolve() != (args.release_source / 'python').resolve():
        raise SystemExit('release source is not the actor interpreter import root')
    vocabulary = json.loads(args.vocabulary_file.read_bytes())['vocabulary']
    print(json.dumps({'agent_adapter': export.adapter_identity(),
                      'producer': source_identity(managed_evaluation.ROOT).to_dict(),
                      'compiler': FrameCompiler(vocabulary).identity}, sort_keys=True))


def child_check(root: Path, registration: Path, python: str, release: Path,
                adapter: dict[str, Any], compiler: dict[str, Any],
                adapter_keys: list[str]) -> dict[str, Any]:
    """Start the real agent child on a copy of an original registration (stdin closed)."""
    shutil.copytree(registration, root / 'registration')
    reg = (root / 'registration').resolve()
    config = json.loads((reg / 'config.json').read_bytes())
    manifest = json.loads((reg / 'manifest.json').read_bytes())
    attachment = json.loads((reg / 'local-inference.json').read_bytes())
    model = reg / Path(config['package_path']).name
    config['package_path'] = manifest['artifact']['path'] = str(model)
    manifest['adapter'] = attachment['adapter'] = adapter
    attachment['compiler_identity'] = compiler
    if isinstance(attachment.get('learner_endpoint'), dict):
        attachment['learner_endpoint']['adapter'] = adapter
        attachment['learner_endpoint']['compiler_identity'] = compiler
    for name, value in (('config.json', config), ('manifest.json', manifest),
                        ('local-inference.json', attachment)):
        (reg / name).write_text(json.dumps(value))
    with open(os.devnull) as stdin, (root / 'child.stderr').open('w') as stderr:
        result = subprocess.run(
            [python, '-m', 'stpd.p1.agent', '--config', str(reg / 'config.json'),
             '--manifest', str(reg / 'manifest.json'), '--binding-root', str(reg),
             '--local-inference', str(reg / 'local-inference.json')],
            cwd=root, stdin=stdin, stdout=subprocess.DEVNULL, stderr=stderr,
            env=actor_environment(release, root / 'tmp', adapter_keys), timeout=300,
            check=False)
    text = (root / 'child.stderr').read_text()
    code = next((c for c in ('RL_producer_compiler_identity_differs',
                             'P1 model installation binding differs',
                             'RL inference attachment differs') if c in text), None)
    return {'rc': result.returncode, 'stderr_bytes': len(text), 'code': code}


def real_game(evaluate: Any, root: Path, cfg: dict[str, Any], execution: Path, learner: Path,
              cp_id: str, *, timeout: float, episode_seconds: int) -> dict[str, Any]:
    """One Managed game with the new release + resume CP through the normal submit path."""
    evaluation = cfg['evaluation']
    evaluate.read_pinned(evaluation['manifest'])
    manifest = Path(evaluation['manifest']['path'])
    evaluate.prepare_checkpoint(learner, cp_id, execution, manifest, root / 'cp', workers=1,
                                seeds=evaluation.get('seeds', 50),
                                episode_seconds=episode_seconds)
    evaluate.prepare(root / 'cp/evaluation/execution.json', root / 'one',
                     selections={'checkpoint': 'checkpoint'}, workers=1, limit=1,
                     manifest_path=manifest, episode_seconds=episode_seconds,
                     unknown_policy='continue_cleaned')
    submitted = evaluate.submit(root / 'one/plan.json', partition=evaluation.get(
        'partition', 'general'), constraint=evaluation.get('constraint'),
        exclude_nodes=cfg.get('exclude_nodes') or None)  # as watch submits evaluations
    job = submitted.get('job_id')
    print(f'   real game submitted as job {job}; waiting (up to {timeout:.0f} s)', flush=True)
    started, result_path = time.time(), root / 'one/result.json'
    while True:
        result = _read_json(result_path)
        if result is not None and result.get('status'):
            break
        if time.time() - started > timeout:
            raise SwapError('real_game_timeout', f'job {job} still running; inspect, then '
                            'scancel it yourself if needed')
        state = run(['sacct', '-n', '-P', '-X', '-j', str(job), '-o', 'State'],
                    check=False).stdout.split()
        if state and state[0] in {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'NODE_FAIL',
                                  'OUT_OF_MEMORY', 'BOOT_FAIL', 'DEADLINE', 'PREEMPTED'}:
            time.sleep(30)
            if _read_json(result_path) is None:
                raise SwapError('real_game_job_ended_without_result', f'job {job}: {state[0]}')
        time.sleep(15)
    # The plan holds every manifest seed; limit=1 selects one slot, the rest stay not_started.
    selected = set(result.get('selected_slot_ids') or [])
    played = [s for s in result['ledger_report']['slots'] if s.get('slot_id') in selected]
    slot = played[0] if len(played) == 1 else {}
    summary = {'job_id': job, 'status': result['status'], 'error_type': result.get('error_type'),
               'slot_status': slot.get('status'), 'reason': slot.get('execution_reason'),
               'result': slot.get('natural_result'), 'floor': slot.get('max_observed_floor'),
               'seed_ordinal': slot.get('seed_ordinal')}
    if (result['status'] != 'completed' or result.get('error_type') or len(played) != 1
            or slot.get('status') != 'finished'
            or slot.get('execution_reason') != 'natural_terminal'
            or result['ledger_report'].get('save_failed')):
        raise SwapError('real_game_not_a_natural_end', json.dumps(summary))
    return summary


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def view(cfg: dict[str, Any], final: Path, staging: Path) -> dict[str, Any]:
    """The staged watch config with its own file references pointed into staging."""
    return json.loads(json.dumps(cfg).replace(json.dumps(str(final) + '/')[1:-1],
                                              json.dumps(str(staging) + '/')[1:-1]))


# ------------------------------------------------------------------- orchestration

class Steps:
    def __init__(self) -> None:
        self.name = 'arguments'

    def __call__(self, name: str, text: str) -> None:
        self.name = name
        print(f'== {name}: {text}', flush=True)


def phase_a(args: argparse.Namespace, steps: Steps) -> None:
    """Stdlib only: build the release, then re-exec phase B inside its ENV."""
    if not os.environ.get('EBROOTPYTHON') or shutil.which('node') is None:
        raise SwapError('friday_modules_not_loaded',
                        'module load python/3.11.5-gcccore-13.2.0 nodejs/20.9.0-gcccore-13.2.0')
    predecessor = args.predecessor.resolve()
    prior = json.loads((predecessor / 'watch-config.json').read_bytes())
    old_release = Path(prior['runtime_source']).parent
    env_dir = args.env_dir or predecessor.parent.parent
    if args.dry_run:
        steps('1 build', 'skipped (dry run)')
        if args.release_dir is None:
            raise SwapError('dry_run_requires_release_dir')
        release = args.release_dir.resolve()
        sha = git(release / 'source', 'rev-parse', 'HEAD')
        env_file = env_dir / f'ENV-{release.name[-8:]}.sh'
        if not env_file.is_file():
            raise SwapError('release_env_file_missing', env_file.name)
        tool = Path(__file__).resolve()
    else:
        if not args.sha or not re.fullmatch(r'[0-9a-f]{40}', args.sha):
            raise SwapError('full_40_hex_sha_required')
        steps('1 build', f'release from {args.sha[:12]} beside {old_release.name}')
        release, reused = build_release(old_release, args.sha, args.bundle)
        env_file, env_reused = write_env(env_dir, old_release, release)
        print(f'   release {release} ({"reused, HEAD/clean/links checked" if reused else "built"});'
              f' Host/Runtime stage and env reused; {env_file.name} '
              f'({"unchanged" if env_reused else "written"}, only the release path differs)')
        sha = args.sha
        tool = release / 'source/tools/friday/release_swap.py'
    forwarded = [a for a in sys.argv[1:] if a != '--phase-b']
    command = ['bash', '-c', WRAPPER, 'release_swap', *forwarded, '--phase-b',
               '--resolved-release', str(release), '--resolved-sha', sha,
               '--resolved-env', str(env_file)]
    environment = {**os.environ, 'SWAP_ENV_FILE': str(env_file), 'SWAP_TOOL': str(tool),
                   'PYTHONDONTWRITEBYTECODE': '1'}  # never dirty a release in use
    sys.stdout.flush()
    os.execvpe('bash', command, environment)


def phase_b(args: argparse.Namespace, steps: Steps) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import evaluate
    import watch

    release, env_file = args.resolved_release, args.resolved_env
    if not args.dry_run and Path(watch.__file__).resolve().parent != (
            release / 'source/tools/friday').resolve():
        raise SwapError('rules_not_from_the_new_release', watch.__file__)
    predecessor = args.predecessor.resolve()
    state_path = (args.predecessor_state or predecessor / 'watch-state.json').resolve()
    previous = json.loads(state_path.read_bytes())
    prior = evaluate.read_pinned(previous['configuration'])
    previous_ref = {'path': str(state_path), 'sha256': sha256(state_path)}
    root = (args.output_root or predecessor.parent).resolve()
    suffix = '-dry-run' if args.dry_run else ''
    final = root / f'swap-{args.name}{suffix}'
    work = root / f'watch-{args.name}'
    if work.exists():
        raise SwapError('work_directory_exists', str(work))
    now, margin = time.time(), float(args.launch_margin)

    steps('decide', f'predecessor {predecessor.name} (stop {previous.get("stop")})')
    active = previous.get('active')
    job = previous['attempts'][active].get('job_id') if active is not None else None
    decision = decide(previous, prior, now=now, margin=margin,
                      completed_stops=watch.COMPLETED_WINDOW_STOPS,
                      active_terminal=watch.job_accounting(job)['terminal'] if job else None)
    if args.expect_mode and decision['mode'] != args.expect_mode:
        raise SwapError('mode_differs_from_expected', f"{decision['mode']}: {decision['why']}")
    print(f"   mode {decision['mode']}: {decision['why']}")
    mode = decision['mode']
    held = args.held_job or prior.get('held_job_id')
    holder_state, end = holder_end(held) if held else ('none', None)
    print(f'   holder {held}: {holder_state}')
    if mode != 'close_window' and holder_state not in {'RUNNING', 'PENDING'}:
        raise SwapError('held_job_not_running', f'{held}: {holder_state}')
    stuck = attempts_needing_reconciliation(previous, prior.get('dataplane') is not None,
                                            Path.exists)
    if stuck and mode != 'new_window':
        raise SwapError('attempt_needs_reconciliation', f'attempts {stuck} have no '
                        'control-wal-final.json: write a reconciled state (c10) and pass '
                        '--predecessor-state')
    if mode == 'close_window':
        # It runs on the predecessor's release: launch it with that release's ENV.
        env_file = env_file.parent / f"ENV-{Path(prior['runtime_source']).parent.name[-8:]}.sh"
        if not env_file.is_file():
            raise SwapError('predecessor_env_file_missing', env_file.name)

    staging = root / f'.swap-{args.name}{suffix}.staging-{os.getpid()}'
    evidence = root / f'swap-{args.name}{suffix}.evidence' / f'run-{os.getpid()}'
    staging.mkdir(parents=True, mode=0o700)
    try:
        prepared = _prepare(args, steps, watch, evaluate, decision, previous, prior,
                            previous_ref, release, env_file, staging, final, evidence, work,
                            now, margin, held, end)
    except BaseException:
        failed = root / f'.swap-{args.name}{suffix}.FAILED-{os.getpid()}'
        if staging.exists():
            os.rename(staging, failed)
            print(f'   staging kept as {failed} (not valid; inspect, then delete)')
        raise
    _finish(args, steps, watch, staging, final, evidence, *prepared)


def _prepare(args, steps, watch, evaluate, decision, previous, prior, previous_ref, release,
             env_file, staging, final, evidence, work, now, margin, held, end):
    mode = decision['mode']
    rows = cp = window = None
    verification: dict[str, Any] = {}
    files: dict[str, Any] = {}
    if mode == 'close_window':
        steps('2 freeze', 'skipped: a close-only watch keeps the predecessor release')
        steps('3 inputs', 'skipped: no learner or actor starts')
    else:
        old_execution = json.loads(Path(prior['execution']).read_bytes())
        old_learner = json.loads(Path(prior['learner_config']).read_bytes())
        steps('2 freeze', 'identities in the actor interpreter '
              + old_execution['python_executable'])
        registration = args.registration or find_registration(previous, evaluate.read_pinned)
        new = freeze_identities(old_execution['python_executable'], release, registration,
                                evidence / 'tmp', old_execution.get('adapter_environment_keys', []))
        old_head = git(Path(prior['runtime_source']), 'rev-parse', 'HEAD')
        changed = git(release / 'source', 'diff', '--name-only', old_head, 'HEAD', '--',
                      *PYTHON_CHANGE_HINTS).splitlines()
        rows = identity_rows(old_execution, old_learner, new, changed)
        print(render_identities(rows))
        files['identities.json'] = {'from_revision': old_head, 'to_revision': args.resolved_sha,
                                    'rows': rows, 'registration': str(registration)}

        steps('3 inputs', 'resume CP as the predecessor watcher picks it; execution + learner')
        cp, notes = predecessor_checkpoint(watch, prior, previous, evidence / 'cp-select')
        print(f"   CP {cp['checkpoint_artifact_id'][:16]} update {cp['updates']} "
              f"generation {cp['training_generation']} {[n['kind'] for n in notes]}")
        files['checkpoint.json'] = cp
        files['execution.json'] = build_execution(old_execution, new)
        files['learner.json'] = build_learner(old_learner, new['compiler'], cp)
    steps('4 watch', f'{mode} config')
    if mode == 'new_window':
        windows = chain_training_seconds(previous, evaluate.read_pinned)
        deadline = args.deadline_at if args.deadline_at is not None else (
            end - HOLDER_MARGIN if end is not None else None)
        window = new_window_fields(prior, windows, now=now, margin=margin,
                                   deadline_at=deadline, budget_id=args.budget_id,
                                   training_seconds=args.training_seconds,
                                   total_training_seconds=args.total_training_seconds)
        decision = {**decision, 'launch_by': window['launch_by']}
        print(f"   budget {window['budget_id']}, training_seconds {window['training_seconds']} "
              f"({window['training_seconds_reason']}), deadline {stamp(window['deadline_at'])}")
    auto = sorted((previous.get('node_exclusion') or {}).get('excluded', {}))
    cfg = build_watch(mode, prior, previous_ref=previous_ref, release=release,
                      execution=final / 'execution.json', learner=final / 'learner.json',
                      cp_id=cp and cp['checkpoint_artifact_id'], held_job=held,
                      exclude=args.exclude_node, auto_excluded=auto, window=window,
                      acknowledgements=dict(a.split('=', 1) for a in args.acknowledge))
    files['watch.json'] = cfg
    files['decision.json'] = {**decision, 'predecessor': str(args.predecessor.resolve()),
                              'predecessor_state': previous_ref, 'window': window,
                              'release': str(release), 'sha': args.resolved_sha,
                              'auto_excluded_carried': auto if mode == 'new_window' else [],
                              'dry_run': args.dry_run}
    for name, value in files.items():
        write(staging / name, value)
    hashes = {name: sha256(staging / name) for name in sorted(files)}
    if final.exists():
        return ('existing', decision, rows, cp, cfg, verification, hashes, env_file, work)

    steps('5 verify', 'read_segment_config, continuation rule, agent child, real game')
    staged = staging / 'verify-view.json'
    write(staged, view(cfg, final, staging))
    checked = watch.read_segment_config(staged)
    verification['read_segment_config'] = 'OK'
    if mode == 'new_window':
        watch.require_completed_previous_window(previous, prior, checked)
        verification['require_completed_previous_window'] = 'OK'
    else:
        watch.require_watch_continuation(previous, prior, checked)
        watch.require_acknowledged_findings(previous, checked)  # pass --acknowledge KEY=SHA
        verification['require_watch_continuation'] = 'OK (stop reset by the continuation)'
    if cp is not None:
        linked = watch.checkpoint_link(cp, checked, has_history=True)
        if linked != (mode == 'new_window'):
            raise SwapError('checkpoint_link_mode_differs', (
                f"new_window={linked}: the newest CP {cp['checkpoint_artifact_id'][:12]} has "
                f"budget {(cp.get('operating_budget') or {}).get('id')}; a later window or "
                'continuation of this run already exists, prepare against that one'))
        verification['checkpoint_link'] = f'OK (new_window={linked})'
    watch.read_compatible_fixes()
    verification['compatible_fixes'] = (f"{watch.COMPATIBLE_FIXES_PATH} sha256 "
                                        f"{sha256(watch.COMPATIBLE_FIXES_PATH)[:12]}")
    if mode != 'close_window':
        adapter = files['execution.json']['agent_adapter']
        compiler = files['learner.json']['segments']['input_compiler_identity']
        python = files['execution.json']['python_executable']
        registration = Path(files['identities.json']['registration'])
        keys = files['execution.json'].get('adapter_environment_keys', [])
        good = child_check(evidence / 'child-new', registration, python, release, adapter,
                           compiler, keys)
        old_sha = rows[2]['old'] if rows[2]['changed'] else '0' * 64
        bad = child_check(evidence / 'child-negative', registration, python, release, adapter,
                          {**compiler, 'compiler_sha256': old_sha}, keys)
        print(f'   agent child new identity rc={good["rc"]}; negative control rc={bad["rc"]} '
              f'{bad["code"]}')
        if good['rc'] != 0 or good['code'] is not None:
            raise SwapError('agent_child_refused_new_identity', json.dumps(good))
        if bad['rc'] == 0 or bad['code'] != 'RL_producer_compiler_identity_differs':
            raise SwapError('agent_child_negative_control_not_refused', json.dumps(bad))
        verification['agent_child'] = 'accepts the new identity; refuses a wrong compiler'
        if args.dry_run:
            verification['real_game'] = 'skipped (dry run)'
        else:
            if cfg.get('evaluation') is None:
                raise SwapError('evaluation_config_required_for_the_real_game')
            game = real_game(evaluate, evidence / 'game', cfg, staging / 'execution.json',
                staging / 'learner.json', cp['checkpoint_artifact_id'],
                timeout=args.game_timeout, episode_seconds=cfg['evaluation'].get(
                    'episode_seconds', watch.EVALUATION_EPISODE_SECONDS))
            verification['real_game'] = (f"job {game['job_id']} natural {game['result']} at "
                                         f"floor {game['floor']}, unknown 0")
    os.remove(staged)
    write(staging / 'verification.json', {**verification, 'evidence': str(evidence)})
    return ('new', decision, rows, cp, cfg, verification, hashes, env_file, work)


def _finish(args, steps, watch, staging, final, evidence, kind, decision, rows, cp, cfg,
            verification, hashes, env_file, work):
    driver = final.parent / f'watch-driver-{args.name}.out'
    command = launch_command(cfg, env_file, final / 'watch.json', work, driver)
    if args.dry_run:  # never launchable: the real game was skipped
        command = '# DRY RUN, not verified with a real game; do not launch:\n# ' + command
    ready = final / ('DRY-RUN.json' if args.dry_run else 'READY.json')

    def in_time() -> None:
        # Verification (the real game) can outlast the launch margin: the c12d mistake.
        if decision.get('launch_by') and time.time() > decision['launch_by']:
            raise SwapError('launch_deadline_passed', f"launch_by {stamp(decision['launch_by'])} "
                            'has passed; prepare again (a close-only watch, or a new window)')
    if kind == 'existing':
        steps('6 ready', f'{final.name} already exists: comparing')
        shutil.rmtree(staging)
        old = _read_json(ready)
        if old is None:
            raise SwapError('previous_prepare_incomplete', f'{final} has no READY.json; move '
                            'it aside and run again')
        on_disk = {name: sha256(final / name) if (final / name).is_file() else None
                   for name in old['files']}
        differ = sorted(k for k in set(hashes) | set(old['files'])
                        if not old['files'].get(k) == hashes.get(k) == on_disk.get(k))
        if differ:
            raise SwapError('prepared_output_differs', f'{differ}: the inputs or the prepared '
                            f'files changed since {final.name} was prepared; use a new --name')
        in_time()
        verification = _read_json(final / 'verification.json') or {}
        verification.pop('evidence', None)
        print('   unchanged: same files and hashes; verification from the first run kept')
    else:
        steps('6 ready', f'rename into {final.name}, re-check, write READY.json last')
        os.rename(staging, final)
        try:
            checked = watch.read_segment_config(final / 'watch.json')
            if decision['mode'] != 'new_window':
                previous = watch.evaluate.read_pinned(cfg['previous_watch_state'])
                watch.require_watch_continuation(
                    previous, watch.evaluate.read_pinned(previous['configuration']), checked)
            in_time()
        except BaseException:
            failed = final.parent / f'.{final.name}.FAILED-{os.getpid()}'
            os.rename(final, failed)
            raise
    text = checklist(decision=decision, rows=rows, cp=cp, cfg=cfg, verification=verification,
                     command=command, ready=ready, dry_run=args.dry_run, name=args.name)
    if kind != 'existing':
        (final / 'CHECKLIST.txt').write_text(text)
        write(ready, {'files': hashes, 'launch': command,
                      'watch_config_sha256': hashes['watch.json'], 'dry_run': args.dry_run})
    print('\n' + text)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__,
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = value.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--predecessor', type=Path, required=True,
                         help='stopped watch work dir (holds watch-state.json/watch-config.json)')
    prepare.add_argument('--name', required=True, help='continuation name, e.g. c14')
    prepare.add_argument('--sha', help='40-hex develop SHA to build the release from')
    prepare.add_argument('--bundle', type=Path, help='git bundle that contains --sha')
    prepare.add_argument('--dry-run', action='store_true',
                         help='skip the build and the real game; use --release-dir')
    prepare.add_argument('--release-dir', type=Path, help='existing release (dry run)')
    prepare.add_argument('--predecessor-state', type=Path,
                         help='a reconciled state instead of <predecessor>/watch-state.json')
    prepare.add_argument('--registration', type=Path,
                         help='an original actor registration dir (default: found in the '
                              'predecessor attempts)')
    prepare.add_argument('--env-dir', type=Path, help='dir of ENV-<short>.sh files')
    prepare.add_argument('--output-root', type=Path, help='default: the long-run dir')
    prepare.add_argument('--held-job', help="GPU holder (default: the predecessor's)")
    prepare.add_argument('--exclude-node', action='append', default=[])
    prepare.add_argument('--acknowledge', action='append', default=[],
                         help='diagnosed integrity finding KEY=RECORD_SHA256')
    prepare.add_argument('--expect-mode', choices=('same_window', 'new_window', 'close_window'))
    prepare.add_argument('--launch-margin', type=float, default=1800,
                         help='seconds the lead needs to review and launch (default 1800)')
    window = prepare.add_argument_group('new window')
    window.add_argument('--budget-id')
    window.add_argument('--deadline-at', type=float, help='UTC epoch; default holder end - 1800')
    window.add_argument('--training-seconds', type=int)
    window.add_argument('--total-training-seconds', type=int,
                        help='target over all linked windows, e.g. 129600 for 36 h')
    prepare.add_argument('--game-timeout', type=float, default=3600)
    prepare.add_argument('--phase-b', action='store_true', help=argparse.SUPPRESS)
    prepare.add_argument('--resolved-release', type=Path, help=argparse.SUPPRESS)
    prepare.add_argument('--resolved-sha', help=argparse.SUPPRESS)
    prepare.add_argument('--resolved-env', type=Path, help=argparse.SUPPRESS)
    identities = commands.add_parser('_identities')
    identities.add_argument('--vocabulary-file', type=Path, required=True)
    identities.add_argument('--release-source', type=Path, required=True)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == '_identities':
        identities_main(args)
        return 0
    os.umask(0o077)
    steps = Steps()
    try:
        (phase_b if args.phase_b else phase_a)(args, steps)
    except SwapError as error:
        print(f'release_swap FAILED at {steps.name}: {error.code}'
              + (f' ({safe(error.detail)})' if error.detail else ''), file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 -- a typed line, never a credential dump
        print(f'release_swap FAILED at {steps.name}: {type(error).__name__}: {safe(error)}',
              file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
