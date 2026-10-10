"""Command line interface for the Platform Evidence component."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path

from .agent_run_evidence import AgentRunEvidenceVerifier
from .agent_session_run_evidence import AgentSessionRunEvidenceVerifier
from .source_session_bundle_v2 import SourceSessionBundleV2Verifier
from .source_session_bundle_v3 import SourceSessionBundleV3Verifier
from .core import VerifierRegistry
from .human_session_bundle import (
    HumanSessionBundleV2Verifier,
    HumanSessionBundleVerifier,
    VersionedHumanSessionBundleVerifier,
    load_collection_profile,
)
from .store import ContentAddressedStore
from .transfer import DirectoryReceiver, DirectoryTransferManifest


def registry() -> VerifierRegistry:
    result = VerifierRegistry()
    for verifier in (
        VersionedHumanSessionBundleVerifier(),
        HumanSessionBundleVerifier(),
        HumanSessionBundleV2Verifier(),
        AgentRunEvidenceVerifier(),
        AgentSessionRunEvidenceVerifier(),
        SourceSessionBundleV2Verifier(),
        SourceSessionBundleV3Verifier(),
    ):
        result.register(verifier.descriptor, verifier.verify)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sts2-evidence")
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify-human-bundle")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--profile", type=Path)

    agent_run = commands.add_parser("verify-agent-run")
    agent_run.add_argument("directory", type=Path)
    agent_session_run = commands.add_parser("verify-agent-session-run")
    agent_session_run.add_argument("directory", type=Path)
    source_v2 = commands.add_parser("verify-source-bundle-v2")
    source_v2.add_argument("directory", type=Path)
    source_v3 = commands.add_parser("verify-source-bundle-v3")
    source_v3.add_argument("directory", type=Path)

    manifest = commands.add_parser("transfer-manifest")
    manifest.add_argument("directory", type=Path)
    manifest.add_argument("--content-id", required=True)
    manifest.add_argument("--artifact-type", default="directory")
    manifest.add_argument("--output", type=Path, required=True)

    store = commands.add_parser("store-put")
    store.add_argument("directory", type=Path)
    store.add_argument("--root", type=Path, required=True)

    receive = commands.add_parser("receive")
    receive.add_argument("directory", type=Path)
    receive.add_argument("manifest", type=Path)
    receive.add_argument("--root", type=Path, required=True)
    receive.add_argument("--verify-type", choices=["human-session-bundle", "policy-runtime-agent-run",
        "policy-runtime-agent-session-run", "source-session-bundle-v2", "source-session-bundle-v3"])
    receive.add_argument("--receipt", type=Path)

    args = parser.parse_args(argv)
    if args.command == "verify-human-bundle":
        expected = load_collection_profile(args.profile) if args.profile else None
        result = registry().verify("human-session-bundle", args.directory, expected)
        print(json.dumps(_jsonable(result), indent=2, sort_keys=True))
        return 0 if result.passed else 1
    if args.command == "verify-agent-run":
        result = registry().verify("policy-runtime-agent-run", args.directory)
        print(json.dumps(_jsonable(result), indent=2, sort_keys=True))
        return 0 if result.passed else 1
    if args.command == "verify-agent-session-run":
        result = registry().verify("policy-runtime-agent-session-run", args.directory)
        print(json.dumps(_jsonable(result), indent=2, sort_keys=True))
        return 0 if result.passed else 1
    if args.command in {"verify-source-bundle-v2", "verify-source-bundle-v3"}:
        result = registry().verify("source-session-bundle-v" + args.command[-1], args.directory)
        print(json.dumps(_jsonable(result), indent=2, sort_keys=True))
        return 0 if result.passed else 1
    if args.command == "transfer-manifest":
        value = DirectoryTransferManifest.from_directory(
            args.directory,
            content_id=args.content_id,
            artifact_type=args.artifact_type,
        )
        value.write(args.output)
        print(json.dumps({"status": "pass", "manifest_sha256": value.manifest_sha256}, sort_keys=True))
        return 0
    if args.command == "store-put":
        receipt = ContentAddressedStore(args.root).put_directory(args.directory)
        print(json.dumps(_jsonable(receipt), indent=2, sort_keys=True))
        return 0
    promotion_verifier = None
    if args.verify_type == "human-session-bundle":
        promotion_verifier = _verify_human_bundle_promotion
    elif args.verify_type == "policy-runtime-agent-run":
        promotion_verifier = _verify_agent_run_promotion
    elif args.verify_type == "policy-runtime-agent-session-run":
        promotion_verifier = _verify_agent_session_run_promotion
    elif args.verify_type in {"source-session-bundle-v2", "source-session-bundle-v3"}:
        promotion_verifier = _verify_source_v2_promotion if args.verify_type.endswith("v2") else _verify_source_v3_promotion
    receipt = DirectoryReceiver(
        ContentAddressedStore(args.root), promotion_verifier=promotion_verifier
    ).receive(args.directory, args.manifest)
    receipt_value = _jsonable(receipt)
    _write_json_atomic(
        args.root / "store-status.json",
        {
            "schema": "sts2.evidence/store-status-1",
            "observed_at": datetime.now(UTC).isoformat(),
            "last_receipt": receipt_value,
        },
    )
    if args.receipt:
        _write_json_atomic(args.receipt, receipt_value)
    print(json.dumps(receipt_value, indent=2, sort_keys=True))
    return 0 if receipt.status in {"promoted", "reused"} else 1


def _verify_human_bundle_promotion(directory: Path, manifest: DirectoryTransferManifest) -> None:
    if manifest.artifact_type != "human-session-bundle":
        raise ValueError("typed Human verification requires artifact_type=human-session-bundle")
    result = VersionedHumanSessionBundleVerifier().verify(directory)
    bundle = result.require_value()
    if bundle.bundle_content_id != manifest.content_id:
        raise ValueError("transfer content ID differs from verified Human bundle content ID")


def _verify_agent_run_promotion(directory: Path, manifest: DirectoryTransferManifest) -> None:
    if manifest.artifact_type != "policy-runtime-agent-run":
        raise ValueError("typed Policy Runtime verification requires artifact_type=policy-runtime-agent-run")
    verified = AgentRunEvidenceVerifier().verify(directory).require_value()
    if verified.content_id != manifest.content_id:
        raise ValueError("transfer content ID differs from verified Agent Run bytes")


def _verify_agent_session_run_promotion(directory: Path, manifest: DirectoryTransferManifest) -> None:
    if manifest.artifact_type != "policy-runtime-agent-session-run":
        raise ValueError(
            "native Session verification requires artifact_type=policy-runtime-agent-session-run"
        )
    verified = AgentSessionRunEvidenceVerifier().verify(directory).require_value()
    if verified.content_id != manifest.content_id:
        raise ValueError("transfer content ID differs from verified native Agent Session bytes")


def _verify_source_v2_promotion(directory: Path, manifest: DirectoryTransferManifest) -> None:
    _verify_source_promotion(directory, manifest, SourceSessionBundleV2Verifier())


def _verify_source_v3_promotion(directory: Path, manifest: DirectoryTransferManifest) -> None:
    _verify_source_promotion(directory, manifest, SourceSessionBundleV3Verifier())


def _verify_source_promotion(directory: Path, manifest: DirectoryTransferManifest, verifier: SourceSessionBundleV2Verifier) -> None:
    if manifest.artifact_type != verifier.descriptor.type_id:
        raise ValueError("typed Source verification requires artifact_type=" + verifier.descriptor.type_id)
    verified = verifier.verify(directory).require_value()
    if verified.content_id != manifest.content_id:
        raise ValueError("transfer content ID differs from verified Source bundle content ID")


def _write_json_atomic(path: Path, value: object) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, type):
        return value.__name__
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


if __name__ == "__main__":
    raise SystemExit(main())
