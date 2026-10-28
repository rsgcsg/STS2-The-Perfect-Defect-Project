#!/usr/bin/env python3
"""Run a versioned Managed suite, or query reserved evaluation seed membership."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spireagent.managed_task_outcome import (  # noqa: E402
    INDEX_SCHEMA,
    SOURCE_FILES,
    TASK_SPEC,
    ManagedEvidenceIndex,
    project_managed_episode,
)
from stpd.evaluation.managed_suite import (  # noqa: E402
    AGENT_ID,
    AGENT_INPUT_SCOPE,
    SuiteLimits,
    run_suite,
)
from stpd.evaluation.suites import load_suite, seed_membership, suite_names  # noqa: E402
from stpd.host_runtime_client import (  # noqa: E402
    DEFAULT_HOST_RUNTIME,
    activate_host_runtime_client,
    load_host_runtime_pin,
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def code_identity() -> dict:
    repo = ROOT.parent

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    paths = [
        ROOT / "stpd/evaluation/gates.py",
        ROOT / "stpd/evaluation/suites.py",
        ROOT / "stpd/evaluation/managed_suite.py",
        Path(__file__).resolve(),
    ]
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(repo).as_posix().encode() + b"\0" + path.read_bytes())
    return {
        "git_sha": git("rev-parse", "HEAD"),
        "git_status": git("status", "--porcelain"),
        "evaluation_code_sha256": digest.hexdigest(),
        "source_file_sha256": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCE_FILES
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=suite_names(), default="smoke-20")
    parser.add_argument("--seed-membership")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument(
        "--source-host-runtime",
        type=Path,
        help="Explicit source consumer mode; no released-package qualification claim",
    )
    parser.add_argument("--host-runtime", type=Path, default=DEFAULT_HOST_RUNTIME)
    parser.add_argument("--host-runtime-pin", type=Path)
    parser.add_argument("--allow-test", action="store_true")
    parser.add_argument("--test-purpose", help="Required held-out opening declaration")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--random-seed", type=int, default=20261014)
    parser.add_argument("--max-steps", type=int, default=10000)
    parser.add_argument("--max-episode-seconds", type=float, default=120)
    parser.add_argument("--max-suite-seconds", type=float, default=600)
    parser.add_argument(
        "--max-episodes", type=int, help="Canary limit; remaining planned seeds stay censored"
    )
    args = parser.parse_args()
    if args.seed_membership:
        print(
            json.dumps(
                {"seed": args.seed_membership, "suites": seed_membership(args.seed_membership)}
            )
        )
        return 0
    manifest = load_suite(args.suite)
    if manifest["host"] != "Managed":
        parser.error(
            "Reference suite requires the Reference owner; reuse its manifest in paired evaluation"
        )
    if args.suite == "test-1000" and (not args.allow_test or not args.test_purpose):
        parser.error("test-1000 requires --allow-test and --test-purpose; never use for tuning")
    if args.candidate is None or args.run_dir is None:
        parser.error("--candidate and --run-dir required")
    limits = SuiteLimits(
        args.max_steps, args.max_episode_seconds, args.max_suite_seconds, args.workers
    )
    if args.max_episodes is not None and not 1 <= args.max_episodes <= len(manifest["seeds"]):
        parser.error("invalid --max-episodes")
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=False)  # immutable previous attempts
    host = (args.source_host_runtime or args.host_runtime).resolve()
    mode = "source_consumer" if args.source_host_runtime else "released_package"
    if args.source_host_runtime:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(host / "consumers/python"))
    else:
        activate_host_runtime_client(
            host, load_host_runtime_pin(args.host_runtime_pin) if args.host_runtime_pin else None
        )
    from spireagent.resource_slot import require_managed_slot

    require_managed_slot()
    from sts2_headless import FiniteActionView, ManagedPlayerEnvironment

    identity = {
        **code_identity(),
        "agent_id": AGENT_ID,
        "host_consumer_mode": mode,
        "host_runtime": str(host),
        "machine": platform.platform(),
        "python": sys.version,
        "pid": os.getpid(),
        "eval_id": run_dir.name,
    }
    config = {
        "manifest": manifest,
        "random_seed": args.random_seed,
        "limits": vars(limits),
        "max_episodes": args.max_episodes,
        "task_spec": TASK_SPEC,
        "agent_input_scope": AGENT_INPUT_SCOPE,
        "test_purpose": args.test_purpose,
        "host_runtime": str(host),
        "candidate": str(args.candidate.resolve()),
    }
    frozen = run_dir / "evaluation-source"
    for relative in SOURCE_FILES:
        target = frozen / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    write_json(run_dir / "config.json", config)
    began = time.time()
    write_json(run_dir / "meta.json", {**identity, "started_at_unix": began, "status": "running"})

    def factory(**kwargs):
        return ManagedPlayerEnvironment(
            [
                "node",
                str(host / "tools/managed-pe-driver.mjs"),
                "--candidate",
                str(args.candidate.resolve()),
                "--character",
                manifest["character"],
                "--timeout-ms",
                "5000",
                "--quiet-diagnostics",
            ],
            response_timeout_seconds=10,
            **kwargs,
        )

    def emit_factory(index):
        directory = run_dir / f"episode-{index + 1:04d}"
        directory.mkdir()
        sequence = 0

        def emit(kind, value):
            nonlocal sequence
            sequence += 1
            with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "sequence": sequence,
                            "kind": kind,
                            "elapsed_unix": time.time(),
                            "value": value,
                        }
                    )
                    + "\n"
                )

            return {
                "path": (directory / "events.jsonl").relative_to(run_dir).as_posix(),
                "event_sequence": sequence,
            }

        return emit

    report = run_suite(
        manifest,
        factory,
        FiniteActionView,
        limits=limits,
        random_seed=args.random_seed,
        emit_factory=emit_factory,
        identity=identity,
        max_episodes=args.max_episodes,
    )
    write_json(
        run_dir / "meta.json",
        {
            **identity,
            "started_at_unix": began,
            "ended_at_unix": time.time(),
            "status": "raw_episode_collection_ended",
        },
    )
    entries = [
        {
            "path": p.relative_to(run_dir).as_posix(),
            "bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(run_dir.rglob("*"))
        if p.is_file()
    ]
    write_json(run_dir / "evidence-index.json", {"schema": INDEX_SCHEMA, "files": entries})
    index_sha = hashlib.sha256((run_dir / "evidence-index.json").read_bytes()).hexdigest()
    index = ManagedEvidenceIndex(run_dir, index_sha)
    report["evidence_index_sha256"] = index_sha
    report["raw_verdict"] = report["verdict"]
    for ordinal, row in enumerate(report["episodes"], 1):
        if row["started"]:
            row["task_outcome"] = project_managed_episode(index, ordinal)
            if row["task_outcome"]["evidence"]["verification"] != "managed_raw_files_verified":
                report["verdict"] = "fail"
    write_json(run_dir / "report.json", report)
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"episodes", "b6"}},
            sort_keys=True,
        )
    )
    return 0 if report["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
