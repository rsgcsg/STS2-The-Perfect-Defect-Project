"""Normal local P1 import/derive/read entry; no recorder launch or cloud writes."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sts2_platform_evidence.collection_tool import P1_SELECTOR_PROJECTION, CollectionTool

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.storage.config import open_store
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT, atomic_json
from spireagent.workbench.local_recording_import import OPERATION_FILE, SCHEMA
from spireagent.workbench.p1_recording_store import (
    BUNDLE_SCHEMA,
    PROJECTIONS,
    SEGMENTS_SCHEMA,
    _matching,
    derive_p1_recording,
    p1_projection,
    publish_p1_original,
    query_p1_recordings,
)


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (BoundaryError, OSError, ValueError) as error:
        print(json.dumps({"status": "failed",
                          "error_code": getattr(error, "code", type(error).__name__)}))
        return 1


def _main(argv: list[str] | None = None) -> int:
    import sys

    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "control":
        return _control(arguments[1:])
    parser = argparse.ArgumentParser(description=__doc__,
        epilog="Recording control: recordings control --config PROJECT "
               "status|start|pause|resume|prefix|close")
    parser.add_argument("action", choices=("import-bundle", "derive", "list", "read", "reconcile"))
    parser.add_argument("--store", required=True, help="existing local ArtifactStore binding")
    parser.add_argument("--state", type=Path, required=True, help="application private state")
    parser.add_argument("--tool", type=Path)
    parser.add_argument("--tool-release")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--artifact")
    parser.add_argument("--run-id", default="run-0001")
    parser.add_argument("--projection", choices=sorted(PROJECTIONS), default=P1_SELECTOR_PROJECTION)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preserved-manifest", type=Path)
    parser.add_argument("--preserved-archive", type=Path)
    parser.add_argument("--preserved-index", type=Path)
    args = parser.parse_args(arguments)
    if args.store == "s3":
        parser.error("this entry is local-only; explicit push/pull owns cloud transfer")
    args.state.mkdir(parents=True, exist_ok=True)
    store = open_store(args.store)
    result: Any
    if args.action == "list":
        result = query_p1_recordings(store)
    elif args.action == "read":
        if args.artifact is None or args.output is None:
            parser.error("read requires --artifact and --output")
        manifest = p1_projection(store, args.artifact)
        store.materialize(manifest.payload("segments"), args.output)
        result = {"artifact_id": manifest.artifact_id,
                  "source_artifact_id": manifest.parent("source"),
                  "output": str(args.output), "payload": manifest.payload("segments").to_dict()}
    elif args.action == "reconcile":
        from spireagent.workbench.developer_server import instance_lock

        with instance_lock(args.state / "instance.lock"):
            path = args.state / OPERATION_FILE
            operation = json.loads(path.read_bytes())
            result = _reconcile(store, operation)
            atomic_json(path, result)
    else:
        if args.tool is None or args.tool_release is None:
            parser.error("import/derive require the exact --tool and --tool-release")
        if args.action == "import-bundle" and args.bundle is None:
            parser.error("import-bundle requires --bundle")
        if args.action == "derive" and args.artifact is None:
            parser.error("derive requires --artifact")
        from spireagent.workbench.developer_server import instance_lock

        tool = CollectionTool(args.tool, args.tool_release)
        producer = source_identity(ROOT)
        content_id = (tool.verify_p1_native(args.bundle)["manifest"]["bundle_content_id"]
                      if args.action == "import-bundle" else None)
        operation = {"schema": SCHEMA, "recording_type": "p1_native",
                     "action": args.action, "status": "pending",
                     "started_at": datetime.now(UTC).isoformat(),
                     "source_artifact_id": args.artifact, "projection_id": args.projection,
                     "content_id": content_id, "run_id": args.run_id,
                     "tool_release_id": args.tool_release}
        with instance_lock(args.state / "instance.lock"):
            path = args.state / OPERATION_FILE
            if path.exists():
                previous = json.loads(path.read_bytes())
                if previous.get("status") in {
                    "pending", "publication_unknown", "interrupted_unknown",
                }:
                    raise BoundaryError("p1_store", "explicit_operation_reconciliation_required")
            atomic_json(path, operation)
            try:
                if args.action == "import-bundle":
                    original = (Manifest.from_bytes(args.preserved_manifest.read_bytes())
                                if args.preserved_manifest is not None else None)
                    manifest = publish_p1_original(
                        store, args.bundle, tool, producer, scratch=args.state,
                        preserved_manifest=original, preserved_archive=args.preserved_archive,
                        preserved_index=args.preserved_index)
                else:
                    manifest = derive_p1_recording(
                        store, args.artifact, tool, producer, scratch=args.state,
                        run_id=args.run_id, projection_id=args.projection)
                result = {**operation, "status": "completed", "artifact_id": manifest.artifact_id,
                          "parents": [p.to_dict() for p in manifest.parents],
                          "parameters": manifest.parameters.value(),
                          "finished_at": datetime.now(UTC).isoformat()}
                atomic_json(path, result)
            except Exception as error:
                # Resolve durable publication by its original intent. Never dispatch again.
                failed = {**operation, "error_code": getattr(error, "code", type(error).__name__),
                          "_diagnostic": getattr(error, "diagnostic", "")[-16384:]}
                try:
                    result = _reconcile(store, failed)
                except Exception:
                    result = {**failed, "status": "publication_unknown"}
                atomic_json(path, result)
                raise
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _reconcile(store: ManifestArtifactStore, operation: dict[str, Any]) -> dict[str, Any]:
    """Read the original intent only; absence is known only after a successful local scan."""
    if operation.get("recording_type") != "p1_native":
        raise BoundaryError("p1_store", "p1_operation_required")
    if operation["action"] == "import-bundle":
        manifest = _matching(store, BUNDLE_SCHEMA, content_id=operation["content_id"])
    else:
        manifest = _matching(store, SEGMENTS_SCHEMA,
                             source_artifact_id=operation["source_artifact_id"],
                             tool_release_id=operation["tool_release_id"],
                             projection_id=operation["projection_id"], run_id=operation["run_id"])
    if manifest is None:
        return {**operation, "status": "failed", "publication": "absent",
                "finished_at": datetime.now(UTC).isoformat()}
    for payload in manifest.payloads:
        for _ in store.read_payload(payload):
            pass
    return {**operation, "status": "completed", "artifact_id": manifest.artifact_id,
            "parents": [p.to_dict() for p in manifest.parents],
            "parameters": manifest.parameters.value(),
            "finished_at": datetime.now(UTC).isoformat()}


def _control(argv: list[str]) -> int:
    """CLI and GUI call the same running Application; Recorder owns the command ledger."""
    from urllib.error import HTTPError
    from uuid import uuid4

    from spireagent.workbench.developer import ProjectConfig
    from spireagent.workbench.developer_server import _local_request, running
    from spireagent.workbench.native_tasks import P1_ORIGINS, P1_PROFILE

    parser = argparse.ArgumentParser(description="Control P1 recording through the open workbench")
    parser.add_argument("action", choices=("status", "start", "pause", "resume", "prefix", "close"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--origin", choices=sorted(P1_ORIGINS))
    parser.add_argument("--command-id")
    args = parser.parse_args(argv)
    if (args.action == "start") != (args.origin is not None):
        parser.error("start requires an explicit --origin; other commands have no origin field")
    current = running(ProjectConfig.load(args.config))
    if current is None:
        raise BoundaryError("recording", "open_workbench_first")
    base = f"http://127.0.0.1:{current['port']}"
    view = _local_request(base + "/api/native-recording/status", token=current["control_token"],
                          method="GET")
    if args.action != "status":
        observed = view["status"]
        kind = {"start": "start_new_session", "prefix": "export_p1_native_prefix"}.get(
            args.action, args.action)
        if args.action != "start" and observed["capture_profile_id"] != P1_PROFILE:
            raise BoundaryError("recording", "p1_recording_session_required")
        body = {"kind": kind, "runtime_instance_id": observed["runtime_instance_id"],
                "recording_session_id": observed["recording_session_id"],
                "source_segment_id": (observed["source"]["segment_id"]
                                      if observed["source"] else None),
                "source_kind": args.origin, "actor_id": None,
                "command_id": args.command_id or str(uuid4())}
        if args.action == "start":
            body["capture_profile_id"] = P1_PROFILE
        try:
            view = _local_request(base + "/api/native-recording/command",
                token=current["control_token"], body=body, timeout=45)
        except HTTPError as error:
            try:
                code = json.loads(error.read(4096))["error"]
            except (ValueError, KeyError, TypeError):
                code = "check_original_status_no_retry"
            raise BoundaryError("recording", code) from None
        except OSError as error:
            raise BoundaryError("recording", "check_original_status_no_retry") from error
    print(json.dumps(view, ensure_ascii=False, sort_keys=True))
    return 0
