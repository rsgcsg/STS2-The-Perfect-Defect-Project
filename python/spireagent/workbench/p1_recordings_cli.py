"""Normal local P1 import/derive/read entry; no recorder launch or cloud writes."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from sts2_platform_evidence.collection_tool import P1_SELECTOR_PROJECTION, CollectionTool

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.storage.config import open_store
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
    parser = argparse.ArgumentParser(description=__doc__)
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
    args = parser.parse_args(argv)
    if args.store == "s3":
        parser.error("this entry is local-only; explicit push/pull owns cloud transfer")
    args.state.mkdir(parents=True, exist_ok=True)
    store = open_store(args.store)
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
                failed = {**operation, "error_code": getattr(error, "code", type(error).__name__)}
                try:
                    result = _reconcile(store, failed)
                except Exception:
                    result = {**failed, "status": "publication_unknown"}
                atomic_json(path, result)
                raise
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _reconcile(store, operation: dict) -> dict:
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
