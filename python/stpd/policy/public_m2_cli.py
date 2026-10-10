"""Public M2 export/verification and the trusted decision-only Port4 process."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from spireagent.json_boundary import BoundaryError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--store", required=True, type=Path)
    export.add_argument("--model", required=True)
    export.add_argument("--stage", required=True)
    export.add_argument("--destination", required=True, type=Path)
    verify = commands.add_parser("verify-export")
    verify.add_argument("--destination", required=True, type=Path)
    port = commands.add_parser("serve")
    port.add_argument("--config", required=True, type=Path)
    port.add_argument("--manifest", required=True, type=Path)
    port.add_argument("--binding-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            from spireagent.storage.local import LocalBlobStore
            from spireagent.storage.store import ManifestArtifactStore

            from .public_m2_export import export_model

            store = ManifestArtifactStore(LocalBlobStore(args.store, create=False, readonly=True))
            value = export_model(store, args.model, args.destination, stage_id=args.stage)
            print(json.dumps(value))
            return 0
        if args.command == "verify-export":
            from .public_m2_export import receipt, validate_package

            validate_package(args.destination, load_weights=True)
            print(json.dumps(receipt(args.destination)))
            return 0
        from stpd.public_m2_policy_installation import validate

        from .public_m2_export import FILES, validate_package
        from .public_m2_port import PublicM2PolicyAdapter, serve

        root = Path(__file__).resolve().parents[2]
        config, manifest = validate(root, args.config, args.manifest,
                                    binding_root=args.binding_root)
        directory = Path(config["export_path"])
        model, engine_config, _lineage = validate_package(directory)
        info = model.parameters.value()
        adapter = PublicM2PolicyAdapter.from_export(
            (directory / FILES["weights"]).read_bytes(), engine_config,
            input_digest=info["engine_input_digest"], completed_epochs=info["epoch"],
            tokenizer_bytes=(directory / FILES["state_tokenizer"]).read_bytes(),
            manifest=manifest, inference_device="cpu",
        )
        return serve(adapter, sys.stdin, sys.stdout)
    except (BoundaryError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"stage": "public_m2_consumer",
                          "error": getattr(error, "code", "consumer_failed")}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
