#!/usr/bin/env python3
"""Friday resource adapter over the existing Managed ledger and save pipeline."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import signal
import socket
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA = "spireagent/friday-batch-v1"
POOL_SCHEMA = "spireagent/friday-diagnostic-seed-pool-v1"


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n").encode("utf-8")


def hashed(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_pinned(reference: dict[str, str]) -> Any:
    path = Path(reference["path"])
    if path.is_symlink() or hashed(path) != reference["sha256"]:
        raise ValueError("pinned_input_changed")
    return json.loads(path.read_bytes())


def private_root(path: Path) -> Path:
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError("canonical_absolute_private_root_required")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700 or path.stat().st_uid != os.getuid():
        raise ValueError("private_root_must_be_owned_700")
    return path


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
    temporary = Path(name)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def capacity(idle: int, queued: int, requested: int) -> int:
    if min(idle, queued, requested) < 0 or requested > 200:
        raise ValueError("invalid_capacity")
    return min(requested, idle // 4, 200, 32 if queued > 300 else 200)


def pool_groups(pool: dict[str, Any]) -> list[dict[str, Any]]:
    from spireagent.json_boundary import json_bytes
    from stpd.evaluation.suites import (
        diagnostic_reference_manifest,
        require_non_evaluation_seed,
    )

    if (pool.get("schema") != POOL_SCHEMA or pool.get("purpose") != "engineering_only"
            or not pool.get("groups") or not pool.get("comparison_cells")
            or len(set(pool["comparison_cells"])) != len(pool["comparison_cells"])):
        raise ValueError("explicit_engineering_pool_and_comparison_cells_required")
    seen: set[str] = set()
    for group in pool["groups"]:
        expected = diagnostic_reference_manifest(group["game_version"], group["group_id"],
                                                  group["episode_count"])
        expected["host"] = "Managed"
        if json_bytes(group) != json_bytes(expected):
            raise ValueError("only_canonical_non_evaluation_diagnostic_groups_supported")
        for seed in group["seeds"]:
            require_non_evaluation_seed(seed)
            if seed in seen:
                raise ValueError("duplicate_seed_in_pool")
            seen.add(seed)
    return pool["groups"]


def claim_group(root: Path, pool: dict[str, Any], cell: str, owner: dict[str, str]
                ) -> dict[str, Any] | None:
    """Never expire/reclaim a lease: a lost process remains an unresolved offer."""
    import fcntl

    groups = pool_groups(pool)
    if cell not in pool["comparison_cells"] or not cell or Path(cell).name != cell:
        raise ValueError("declared_comparison_cell_required")
    leases = root / "leases" / cell
    leases.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "leases.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for group in groups:
            keys = [hashlib.sha256(seed.encode()).hexdigest() for seed in group["seeds"]]
            if any((leases / (key + ".json")).exists() for key in keys):
                continue
            for seed, key in zip(group["seeds"], keys, strict=True):
                save(leases / (key + ".json"), {
                    "schema": "spireagent/friday-seed-lease-v1", "seed": seed,
                    "group_id": group["group_id"], "comparison_cell": cell,
                    "state": "reserved", "owner": owner, "reserved_at": time.time(),
                    "retry": False,
                })
            return group
    return None


def snapshot(source: Path, destination: Path) -> dict[str, Any]:
    """Copy closed/current evidence, then compare every byte; never read credentials."""
    if source.resolve() == destination.resolve():
        raise ValueError("independent_snapshot_required")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    subprocess.run(["rsync", "-a", "--chmod=Du=rwx,Dgo=,Fu=rw,Fgo=",
                    str(source) + "/", str(destination) + "/"], check=True,
                   stdout=subprocess.DEVNULL)
    files = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("scratch_symlink_not_supported")
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            identity = hashed(path)
            if hashed(destination / relative) != identity:
                raise ValueError("persistent_snapshot_readback_failed")
            files[relative] = identity
    return {"schema": "spireagent/friday-local-snapshot-v1", "files": files,
            "bytes": sum((source / name).stat().st_size for name in files),
            "full_readback": True}


def execution_on_node(configuration: dict[str, Any], scratch: Path) -> dict[str, Any]:
    """Keep an explicitly remote Store; stage local-peer runs on this node only.

    ManagedEpisodeExecutor already maps store='s3' to its pinned peer. Reusing
    that path avoids recursively copying model training ancestors to each node;
    model root payloads and all new outputs retain the existing exact checks.
    """
    execution = copy.deepcopy(configuration)
    if execution.get("store") == "s3":
        if execution.get("peer", {}).get("kind") != "s3":
            raise ValueError("explicit_remote_store_requires_remote_peer")
    else:
        execution["store"] = str(scratch / "store")
    execution["pipeline"] = {"save_workers": 1, "pending_limit": 1}
    return execution


def run(configuration_path: Path, cell: str, *, wall_seconds: float,
        episode_seconds: float, max_groups: int) -> dict[str, Any]:
    from spireagent.json_boundary import json_bytes
    from spireagent.workbench.evaluation_ledger import EvaluationLedger
    from spireagent.workbench.managed_pipeline import PipelinedManagedExecutor
    from spireagent.workbench.reference_suite import SuiteLimits

    configuration = json.loads(configuration_path.read_bytes())
    if configuration.get("schema") != SCHEMA:
        raise ValueError("unsupported_friday_configuration")
    execution = read_pinned(configuration["execution"])
    pool = read_pinned(configuration["pool"])
    pool_groups(pool)
    if execution.get("retention") != {
            "tier": "trajectory", "purpose": "training", "audit_every": 50}:
        raise ValueError("unscored_trajectory_training_intent_required")
    if not 60 <= wall_seconds <= 21600 or not 10 <= episode_seconds <= 1800:
        raise ValueError("finite_wall_and_episode_budget_required")
    persistent = private_root(Path(configuration["persistent_root"]))
    if (persistent / "STOP").exists():
        return {"status": "stopped", "reason": "stop_requested", "new_games": 0}
    job_id = os.environ.get("SLURM_JOB_ID", "manual")
    task_id = os.environ.get("SLURM_ARRAY_TASK_ID", "0")
    identity = hashlib.sha256(configuration_path.read_bytes()).hexdigest()
    # A new attempt gets a new scratch. Existing persistent ownership refuses reoffer.
    worker_id = f"{identity[:12]}-{cell}-{job_id}-{task_id}"
    worker = persistent / "workers" / worker_id
    worker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        worker.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("worker_already_offered_recover_original_save_only") from None
    ownership = worker / "owner.json"
    scratch = Path(tempfile.mkdtemp(prefix="spireagent-friday-")).resolve()
    scratch.chmod(0o700)
    owner = {"worker_id": worker_id, "host": socket.gethostname(), "job_id": job_id,
             "task_id": task_id, "scratch": str(scratch), "configuration_sha256": identity}
    save(ownership, owner)
    execution = execution_on_node(execution, scratch)
    began = time.monotonic()
    deadline = began + wall_seconds
    stopping = False
    active_ledger: Path | None = None
    results: list[dict[str, Any]] = []
    snapshot_costs: list[dict[str, Any]] = []

    def stop(_number: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True
        if active_ledger is not None:
            (active_ledger / "STOP").touch()

    previous = {number: signal.signal(number, stop)
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
    executor = None
    failure = None
    error_code = None
    try:
        executor = PipelinedManagedExecutor(execution, episode_seconds=episode_seconds,
                                           cleanup_seconds=120)
        save(scratch / "preparation.json", executor.preparation)
        for ordinal in range(max_groups):
            if stopping or (persistent / "STOP").exists():
                break
            if time.monotonic() + episode_seconds + 180 >= deadline:
                break
            group = claim_group(persistent, pool, cell, owner)
            if group is None:
                break
            group_root = scratch / f"group-{ordinal:04d}"
            group_root.mkdir(mode=0o700)
            manifest = group_root / "manifest.json"
            manifest.write_bytes(json_bytes(group))
            execution_path = group_root / "execution.json"
            execution_path.write_bytes(json_bytes(execution))
            selection = next(iter(execution["models"]))
            if len(execution["models"]) != 1:
                raise ValueError("one_frozen_model_per_batch_required")
            ledger = EvaluationLedger.create(group_root / "ledger", manifest, hashed(manifest),
                selections={"model": selection}, execution=execution_path)
            active_ledger = ledger.directory
            for slot in ledger.plan["slots"]:
                if (stopping or (persistent / "STOP").exists()
                        or time.monotonic() + episode_seconds + 180 >= deadline):
                    break
                result = ledger.run_managed(workers=1, max_episodes=1,
                    limits=SuiteLimits(150, episode_seconds, 120,
                                       max(1, deadline - time.monotonic() - 120), 1, 30),
                    executor=executor, slot_ids=(slot["slot_id"],))
                current = next(item for item in ledger.slots()
                               if item["slot_id"] == slot["slot_id"])
                results.append({"group_id": group["group_id"], "seed": slot["seed"],
                                "ledger_result": result, "slot": current})
                save(scratch / "results.json", results)
                snapshot_started = time.monotonic()
                receipt = snapshot(scratch, worker / "evidence")
                snapshot_costs.append({"episode": len(results), "bytes": receipt["bytes"],
                                       "seconds": time.monotonic() - snapshot_started})
                save(worker / "snapshot.json", receipt)
                save(worker / "snapshot-costs.json", snapshot_costs)
                if current["status"] not in {"saved", "finished"}:
                    # Close/preserve remains in the original owner; no next seed/array admission.
                    save(persistent / "STOP", {"reason": "nonterminal_or_unknown",
                                               "worker_id": worker_id, "slot": current})
                    stopping = True
                    break
            active_ledger = None
            if stopping:
                break
    except Exception as error:  # noqa: BLE001 -- top-level owner retains every failed attempt.
        failure = type(error).__name__  # Never serialize exception text that may contain env values.
        error_code = getattr(error, "code", None)
        save(persistent / "STOP", {"reason": "worker_failed", "worker_id": worker_id,
                                   "error_type": failure, "error_code": error_code})
    finally:
        if executor is not None:
            executor.close()
        for number, handler in previous.items():
            signal.signal(number, handler)
        summary = {"schema": "spireagent/friday-worker-result-v1", **owner,
                   "elapsed_seconds": time.monotonic() - began,
                   "groups_requested": max_groups, "episodes": len(results),
                   "status": "failed" if failure else "stopped" if stopping else "completed",
                   "error_type": failure, "error_code": error_code, "scratch_retained": True,
                   "results": results, "local_snapshot_costs": snapshot_costs,
                   "not_training_qualification": True}
        save(scratch / "worker-result.json", summary)
        receipt = snapshot(scratch, worker / "evidence")
        save(worker / "snapshot.json", receipt)
        save(worker / "result.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", type=Path)
    parser.add_argument("--cell", required=True)
    parser.add_argument("--wall-seconds", type=float, default=3600)
    parser.add_argument("--episode-seconds", type=float, default=900)
    parser.add_argument("--max-groups", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.max_groups <= 1000:
        parser.error("--max-groups must be 1..1000")
    os.umask(0o077)
    result = run(args.configuration.resolve(), args.cell, wall_seconds=args.wall_seconds,
                 episode_seconds=args.episode_seconds, max_groups=args.max_groups)
    print(json.dumps({key: result.get(key) for key in (
        "status", "worker_id", "elapsed_seconds", "episodes", "error_type", "error_code")}))
    raise SystemExit(1 if result["status"] == "failed" else 0)


if __name__ == "__main__":
    main()
