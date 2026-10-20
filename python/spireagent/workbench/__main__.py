"""One local-first command surface: python -m spireagent.workbench --help."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.storage.config import open_store
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import copy_artifact
from spireagent.workbench.control import doctor, launch_packet
from spireagent.workbench.readiness import read_receipt, readiness


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "recordings":
        from spireagent.workbench.p1_recordings_cli import main as recordings_main

        return recordings_main(arguments[1:])
    if arguments and arguments[0] == "project":
        from spireagent.workbench.developer_cli import main as project_main

        return project_main(arguments[1:])
    parser = argparse.ArgumentParser(
        description=__doc__, epilog="Developer entry: python -m spireagent.workbench project --help"
    )
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "show",
            "lineage",
            "registry-rebuild",
            "sync",
            "push",
            "pull",
            "doctor",
            "worker",
            "launch",
            "e2e",
            "readiness",
            "analysis",
            "dashboard",
        ),
    )
    parser.add_argument("--store", default=".local/research-store")
    parser.add_argument("--registry", type=Path, default=Path(".local/registry.sqlite"))
    parser.add_argument("--other-store", help="transfer peer: local directory or s3")
    parser.add_argument("--artifact", help="exact artifact SHA256; required for push/pull/show")
    parser.add_argument("--run", help="exact immutable Run ID")
    parser.add_argument("--resume", help="exact immutable Checkpoint ID")
    parser.add_argument("--stop-after", type=int, help="pause after this many steps")
    parser.add_argument(
        "--smoke", action="store_true", help="doctor writes isolated immutable probe"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="exact source checkout")
    parser.add_argument("--evidence", type=Path, help="reviewed exact-source qualification receipt")
    parser.add_argument("--output", type=Path, help="optional local report file")
    parser.add_argument("--fixture", action="store_true", help="explicit synthetic FakeQwen input")
    parser.add_argument("--dataset", help="exact Dataset ID")
    parser.add_argument("--config", type=Path, help="complete TrainingConfig JSON")
    parser.add_argument("--profile", choices=("lite", "standard", "full"), default="standard")
    parser.add_argument("--qwen-snapshot", type=Path)
    parser.add_argument("--control", choices=("pretrained", "random"), default="pretrained")
    parser.add_argument("--random-seed", type=int)
    parser.add_argument("--protocol", help="exact frozen scientific protocol ID")
    args = parser.parse_args(argv)
    try:
        result: Any
        if args.command == "readiness":
            result = readiness(args.root, read_receipt(args.evidence))
        elif args.command == "e2e":
            from spireagent.workbench.e2e import run_e2e

            result = run_e2e(args.root)
        else:
            store = open_store(args.store)
            if args.command == "prepare":
                from spireagent.json_boundary import decode_json
                from spireagent.workbench.preparation import prepare
                from stpd.workers.contracts import TrainingConfig

                if args.config is None:
                    raise BoundaryError("cli", "explicit_training_config_required")
                config = TrainingConfig.decode(decode_json(args.config.read_bytes()))
                result = prepare(
                    store,
                    source_identity(args.root),
                    dataset_id=args.dataset,
                    fixture=args.fixture,
                    config=config,
                    profile=args.profile,
                    qwen_snapshot=args.qwen_snapshot,
                    control=args.control,
                    random_seed=args.random_seed,
                    protocol_id=args.protocol,
                )
            elif args.command == "doctor":
                result = doctor(store, smoke=args.smoke)
            elif args.command in {"worker", "launch"}:
                from stpd.workers.worker import execute

                if not args.run:
                    raise BoundaryError("cli", "exact_run_required")
                runtime = source_identity(args.root)
                if args.command == "launch":
                    result = launch_packet(store, args.run, runtime)
                else:
                    result = asdict(
                        execute(
                            store,
                            ObjectStoreRunReporter(store, store.blobs),
                            args.run,
                            runtime,
                            resume=args.resume,
                            stop_after=args.stop_after,
                        )
                    )
            elif args.command in {"push", "pull"}:
                if not args.artifact or not args.other_store:
                    raise BoundaryError("cli", "exact_artifact_and_peer_required")
                peer = open_store(args.other_store)
                source, target = (store, peer) if args.command == "push" else (peer, store)
                result = {"artifact_id": copy_artifact(source, target, args.artifact)}
            elif args.command == "show":
                if not args.artifact:
                    raise BoundaryError("cli", "exact_artifact_required")
                result = json.loads(store.get_manifest(args.artifact).to_bytes())
            else:
                registry = SQLiteRegistry(args.registry)
                count = sync_registry(store, registry)
                if args.command in {"sync", "registry-rebuild"}:
                    result = {"indexed_manifests": count, "authority": "ArtifactStore"}
                elif args.command == "lineage":
                    if not args.artifact:
                        raise BoundaryError("cli", "exact_artifact_required")
                    result = [json.loads(m.to_bytes()) for m in registry.lineage(args.artifact)]
                else:
                    result = projection_command(args.command, store, registry)
        rendered = (
            result if isinstance(result, str) else json.dumps(result, indent=2, sort_keys=True)
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8", newline="\n")
        else:
            print(rendered)
        return 0
    except (BoundaryError, OSError, ValueError) as error:
        # Never echo external exception text or credential-bearing paths/configuration.
        code = error.code if isinstance(error, BoundaryError) else type(error).__name__
        print(json.dumps({"verdict": "FAIL", "code": code}))
        return 1


def projection_command(command: str, store: Any, registry: Any) -> Any:
    from spireagent.workbench.analysis import analyze
    from spireagent.workbench.dashboard import project, render_html

    report = analyze(registry, store)
    return (
        report.to_dict()
        if command == "analysis"
        else render_html(project(registry, store, analysis=report))
    )


if __name__ == "__main__":
    raise SystemExit(main())
