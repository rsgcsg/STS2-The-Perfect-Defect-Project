#!/usr/bin/env python3
"""Collect bounded current-runtime STPD transitions and run B0/token evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stpd.canonical import canonical_json, semantic_hash  # noqa: E402
from stpd.data import DataSource, build_canonical_dataset  # noqa: E402
from stpd.environment import collect_managed_runtime  # noqa: E402
from stpd.host_runtime_client import (  # noqa: E402
    DEFAULT_HOST_RUNTIME,
    activate_host_runtime_client,
)
from stpd.qwen.l1 import cache_snapshot_path, inspect_cache, load_pin, profile_records  # noqa: E402
from stpd.training_smoke import driver_command  # noqa: E402


def _source_identity() -> dict[str, str]:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    if status:
        raise RuntimeError("current-runtime evidence requires a clean STPD source checkout")
    return {"revision": revision, "worktree": "clean"}


def _write_jsonl(path: Path, values: tuple[Any, ...]) -> None:
    path.write_text("".join(canonical_json(value) + "\n" for value in values), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-runtime", type=Path, default=DEFAULT_HOST_RUNTIME)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", default="STPDV0COLLECT01")
    parser.add_argument("--max-environment-actions", type=int, default=64)
    parser.add_argument("--max-transitions", type=int, default=16)
    parser.add_argument("--split-salt", default="stpd-current-runtime-v0")
    parser.add_argument("--tokenizer-cache", type=Path)
    parser.add_argument(
        "--ranking-supervision",
        choices=("none", "canonical-semantic-first"),
        default="none",
        help=(
            "Optionally mark the executed canonical-semantic-first behavior as full-listwise "
            "ranking supervision. This is a transparent plumbing fixture, not a teacher oracle."
        ),
    )
    args = parser.parse_args()

    source = _source_identity()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"refusing to overwrite collection output: {output}")
    host_runtime = args.host_runtime.resolve()
    candidate = args.candidate.resolve()
    activate_host_runtime_client(host_runtime)
    from sts2_headless import ManagedPlayerEnvironment

    from spireagent.resource_slot import require_managed_slot

    require_managed_slot()
    with ManagedPlayerEnvironment(driver_command(host_runtime, candidate)) as environment:
        collection = collect_managed_runtime(
            environment,
            seed=args.seed,
            episode_id=f"runtime-{args.seed.lower()}",
            max_environment_actions=args.max_environment_actions,
            max_transitions=args.max_transitions,
            ranking_supervision=args.ranking_supervision,
        )
        episode_identity = environment.episode_identity()
    provenance = episode_identity.get("episode_provenance", {})
    if provenance.get("verdict") != "provenance_pass" or provenance.get(
        "requested_seed"
    ) != provenance.get("actual_seed"):
        raise RuntimeError("managed runtime did not prove the requested episode seed")

    output.mkdir(parents=True)
    transitions = tuple(item.to_dict() for item in collection.transitions)
    _write_jsonl(output / "transitions.jsonl", transitions)
    _write_jsonl(output / "runtime.jsonl", collection.raw_records)
    _write_jsonl(output / "token-profile-input.jsonl", collection.token_profile_records)

    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    data_source = DataSource(
        source_id=f"managed-runtime-{semantic_hash(collection.environment)[:16]}",
        kind="current_headless_runtime",
        source_revision=collection.environment.player_environment_revision,
        license_spdx="LicenseRef-Local-Proprietary-Game-Evidence",
        provenance_uri=(output / "runtime.jsonl").as_uri(),
    )
    manifest, b0 = build_canonical_dataset(
        list(transitions),
        output_dir=output / "dataset",
        schema_root=ROOT / "schemas",
        source=data_source,
        stpd_source_revision=source["revision"],
        created_at=generated_at,
        split_salt=args.split_salt,
    )

    token_report = None
    if args.tokenizer_cache is not None:
        pin = load_pin()
        inspect_cache(args.tokenizer_cache, pin)
        tokenizer_path = cache_snapshot_path(args.tokenizer_cache, pin) / "tokenizer.json"
        token_report = profile_records(tokenizer_path, collection.token_profile_records, pin=pin)
        (output / "token-profile-report.json").write_text(
            json.dumps(token_report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        overlength = [
            violation for violation in token_report["violations"] if "exceeds" in violation
        ]
        if overlength:
            raise RuntimeError("current runtime token profile exceeded a frozen length gate")

    action_counts = Counter(len(item.legal_actions) for item in collection.transitions)
    report = {
        "schema": "stpd/current-runtime-collection-v0",
        "generated_at": generated_at,
        "status": "current_runtime_collection_pass",
        "stpd": source,
        "environment": collection.environment.__dict__,
        "episode_provenance": provenance,
        "environment_actions": collection.environment_actions,
        "transitions": len(collection.transitions),
        "termination_reason": collection.termination_reason,
        "ranking_supervision": args.ranking_supervision,
        "family_counts": collection.family_counts,
        "legal_action_count_histogram": dict(sorted(action_counts.items())),
        "b0": b0.to_dict(),
        "dataset_manifest": manifest.to_dict(),
        "token_profile": token_report,
        "non_claims": [
            (
                "The deterministic probe policy is transition-eligible but not ranking supervision."
                if args.ranking_supervision == "none"
                else "Canonical-semantic-first labels are transparent behavior-fixture "
                "supervision for optimizer plumbing, not optimal-Q or policy-quality evidence."
            ),
            "A bounded Managed Exact sample is not Reference transfer or formal H1.0 evidence.",
            "Missing natural selector families remain unmeasured rather than fixture-filled.",
        ],
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output / "report.json"),
                "transitions": report["transitions"],
                "family_counts": report["family_counts"],
                "b0": report["b0"]["verdict"],
                "token_profile_passed": None if token_report is None else token_report["passed"],
                "token_profile_violations": []
                if token_report is None
                else token_report["violations"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
