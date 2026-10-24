"""Run a bounded serial Reference suite through an already-running Workbench."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.workbench.developer import ROOT, ProjectConfig
from spireagent.workbench.reference_suite import (
    SuiteLimits,
    WorkbenchReferenceClient,
    run_suite,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    selections = parser.add_mutually_exclusive_group(required=True)
    selections.add_argument("--selection")
    selections.add_argument("--selections", type=Path,
                            help="four Agent names to exact registered selection IDs")
    parser.add_argument("--evidence-peer", type=Path,
                        help="existing Store/R2 binding JSON without credentials")
    parser.add_argument("--run-profile", choices=("short", "extended", "continuous"), required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--display-mode", choices=("headless", "native-ui"), default="headless")
    parser.add_argument("--speed", choices=("normal", "fast", "instant"), default="instant")
    parser.add_argument("--max-episodes", type=int, default=1)
    parser.add_argument("--preparation-seconds", type=float, required=True)
    parser.add_argument("--episode-seconds", type=float, required=True)
    parser.add_argument("--cleanup-seconds", type=float, default=180)
    parser.add_argument("--suite-seconds", type=float, required=True)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    args = parser.parse_args(argv)
    try:
        producer = source_identity(ROOT)
        limits = SuiteLimits(args.preparation_seconds, args.episode_seconds,
                             args.cleanup_seconds, args.suite_seconds, args.poll_seconds)
        client = WorkbenchReferenceClient(ProjectConfig.load(args.config))
        selected = json.loads(args.selections.read_bytes()) if args.selections else None
        peer = json.loads(args.evidence_peer.read_bytes()) if args.evidence_peer else None
        report = run_suite(args.manifest, args.manifest_sha256, client=client,
                           run_dir=args.run_dir, selection=args.selection or "",
                           selections=selected, evidence_peer=peer,
                           run_profile=args.run_profile, limits=limits,
                           max_episodes=args.max_episodes, display_mode=args.display_mode,
                           speed=args.speed)
        # This is invocation provenance, not a replacement for original owner identities.
        print(json.dumps({"status": report["status"], "report": str(args.run_dir / "report.json"),
                          "attempt_id": report["attempt_id"], "planned": report["planned"],
                          "source_revision": producer.source_revision,
                          "stop_reason": report["stop_reason"]}, ensure_ascii=False))
        return 0 if report["status"] == "completed" else 1
    except (OSError, ValueError, BoundaryError) as error:
        print(json.dumps({"status": "blocked", "error_code":
                          error.code if isinstance(error, BoundaryError)
                          else type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
