"""Normal local P1 import/derive/read entry; no recorder launch or cloud writes."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError

from sts2_platform_evidence.collection_tool import P1_SELECTOR_PROJECTION, CollectionTool

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.storage.config import open_store
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ROOT, atomic_json
from spireagent.workbench.local_recording_import import (
    NATIVE_IMPORT_UNCERTAIN,
    OPERATION_FILE,
    SCHEMA,
)
from spireagent.workbench.p1_recording_store import (
    BUNDLE_SCHEMA,
    CATALOG_SCHEMA,
    PROJECTIONS,
    _matching,
    derive_p1_recording,
    p1_projection,
    projection_format,
    publish_p1_catalog_metadata,
    publish_p1_original,
    publish_p1_projection,
    query_p1_recordings,
    query_p1_sources,
)

_PERSISTENT_FIELDS = ("_maintenance", "_native_agent_intents", "_storage_receipts")


def _operation_guard(previous: dict[str, Any]) -> None:
    if previous and previous.get("schema") != SCHEMA:
        raise BoundaryError("p1_store", "operation_file_invalid")
    if previous.get("status") in {"pending", "publication_unknown", "interrupted_unknown"}:
        raise BoundaryError("p1_store", "explicit_operation_reconciliation_required")
    if any(
        row.get("status") in {"pending", *NATIVE_IMPORT_UNCERTAIN}
        for row in previous.get("_native_agent_intents", {}).values()
    ):
        raise BoundaryError("p1_store", "explicit_native_intent_reconciliation_required")


def _carry_operation_state(previous: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    return {**{key: previous[key] for key in _PERSISTENT_FIELDS if key in previous}, **operation}


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (BoundaryError, OSError, ValueError) as error:
        print(
            json.dumps(
                {"status": "failed", "error_code": getattr(error, "code", type(error).__name__)}
            )
        )
        return 1


def _main(argv: list[str] | None = None) -> int:
    import sys

    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "maintenance":
        return _maintenance(arguments[1:])
    if arguments and arguments[0] == "cohort":
        from .p1_cohort_cli import main as cohort_command

        return cohort_command(arguments[1:])
    if arguments and arguments[0] == "storage":
        return _storage(arguments[1:])
    if arguments and arguments[0] == "control":
        return _control(arguments[1:])
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Recording control: recordings control --config PROJECT "
        "status|start|pause|resume|prefix|close",
    )
    parser.add_argument(
        "action",
        choices=(
            "import-bundle",
            "derive",
            "publish-projection",
            "index",
            "list",
            "sources",
            "read",
            "reconcile",
        ),
    )
    parser.add_argument("--store", required=True, help="existing local ArtifactStore binding")
    parser.add_argument("--state", type=Path, required=True, help="application private state")
    parser.add_argument("--tool", type=Path)
    parser.add_argument("--tool-release")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--artifact")
    parser.add_argument("--run-id", default="run-0001")
    parser.add_argument("--filter", action="append", default=[], metavar="FIELD=JSON")
    parser.add_argument("--projection", choices=sorted(PROJECTIONS), default=P1_SELECTOR_PROJECTION)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--derivation-receipt", type=Path)
    parser.add_argument("--preserved-manifest", type=Path)
    parser.add_argument("--preserved-archive", type=Path)
    parser.add_argument("--preserved-index", type=Path)
    args = parser.parse_args(arguments)
    if args.store == "s3":
        parser.error("this entry is local-only; explicit push/pull owns cloud transfer")
    args.state.mkdir(parents=True, exist_ok=True)
    store = open_store(args.store)
    result: Any
    if args.action == "sources":
        filters = {}
        for item in args.filter:
            key, value = item.split("=", 1)
            filters[key] = json.loads(value)
        path = args.state / OPERATION_FILE
        operation = json.loads(path.read_bytes()) if path.exists() else {}
        result = query_p1_sources(
            store,
            filters=filters,
            storage_receipts=list(operation.get("_storage_receipts", {}).values()),
        )
    elif args.action == "list":
        result = query_p1_recordings(store)
    elif args.action == "read":
        if args.artifact is None or args.output is None:
            parser.error("read requires --artifact and --output")
        manifest = p1_projection(store, args.artifact)
        payload = manifest.payload(
            projection_format(manifest.parameters.value()["projection_id"])[1]
        )
        store.materialize(payload, args.output)
        result = {
            "artifact_id": manifest.artifact_id,
            "source_artifact_id": manifest.parent("source"),
            "output": str(args.output),
            "payload": payload.to_dict(),
        }
    elif args.action == "reconcile":
        from spireagent.workbench.developer_server import instance_lock

        with instance_lock(args.state / "instance.lock"):
            path = args.state / OPERATION_FILE
            operation = json.loads(path.read_bytes())
            result = _reconcile(store, operation)
            atomic_json(path, result)
    else:
        if args.action != "index" and (args.tool is None or args.tool_release is None):
            parser.error("import/derive require the exact --tool and --tool-release")
        if args.action == "import-bundle" and args.bundle is None:
            parser.error("import-bundle requires --bundle")
        if args.action in {"derive", "publish-projection", "index"} and args.artifact is None:
            parser.error("projection requires --artifact")
        if args.action == "publish-projection" and not (
            args.input and args.expected_sha256 and args.derivation_receipt
        ):
            parser.error(
                "publish-projection requires --input, --expected-sha256, --derivation-receipt"
            )
        from spireagent.workbench.developer_server import instance_lock

        tool = CollectionTool(args.tool, args.tool_release) if args.action != "index" else None
        producer = source_identity(ROOT)
        content_id = None
        if args.action == "import-bundle":
            assert tool is not None
            content_id = tool.verify_p1_native(args.bundle)["manifest"]["bundle_content_id"]
        operation = {
            "schema": SCHEMA,
            "recording_type": "p1_native",
            "action": args.action,
            "status": "pending",
            "started_at": datetime.now(UTC).isoformat(),
            "source_artifact_id": args.artifact,
            "projection_id": args.projection,
            "content_id": content_id,
            "run_id": args.run_id,
            "tool_release_id": args.tool_release,
            "expected_payload_sha256": args.expected_sha256,
        }
        with instance_lock(args.state / "instance.lock"):
            path = args.state / OPERATION_FILE
            previous = json.loads(path.read_bytes()) if path.exists() else {}
            _operation_guard(previous)
            operation = _carry_operation_state(previous, operation)
            atomic_json(path, operation)
            try:
                if args.action == "import-bundle":
                    assert tool is not None
                    original = (
                        Manifest.from_bytes(args.preserved_manifest.read_bytes())
                        if args.preserved_manifest is not None
                        else None
                    )
                    manifest = publish_p1_original(
                        store,
                        args.bundle,
                        tool,
                        producer,
                        scratch=args.state,
                        preserved_manifest=original,
                        preserved_archive=args.preserved_archive,
                        preserved_index=args.preserved_index,
                    )
                elif args.action == "index":
                    manifest = publish_p1_catalog_metadata(store, args.artifact, producer)
                elif args.action == "publish-projection":
                    assert tool is not None
                    manifest = publish_p1_projection(
                        store,
                        args.artifact,
                        tool,
                        producer,
                        output=args.input,
                        expected_sha256=args.expected_sha256,
                        projection_id=args.projection,
                        run_id=args.run_id,
                        derivation_receipt=json.loads(args.derivation_receipt.read_bytes()),
                    )
                else:
                    assert tool is not None
                    manifest = derive_p1_recording(
                        store,
                        args.artifact,
                        tool,
                        producer,
                        scratch=args.state,
                        run_id=args.run_id,
                        projection_id=args.projection,
                    )
                result = {
                    **operation,
                    "status": "completed",
                    "artifact_id": manifest.artifact_id,
                    "parents": [p.to_dict() for p in manifest.parents],
                    "parameters": manifest.parameters.value(),
                    "finished_at": datetime.now(UTC).isoformat(),
                }
                atomic_json(path, result)
            except Exception as error:
                # Resolve durable publication by its original intent. Never dispatch again.
                failed = {
                    **operation,
                    "error_code": getattr(error, "code", type(error).__name__),
                    "_diagnostic": getattr(error, "diagnostic", "")[-16384:],
                }
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
    if operation["action"] == "index":
        manifest = _matching(
            store, CATALOG_SCHEMA, projection_artifact_id=operation["source_artifact_id"]
        )
    elif operation["action"] == "import-bundle":
        manifest = _matching(store, BUNDLE_SCHEMA, content_id=operation["content_id"])
    else:
        manifest = _matching(
            store,
            projection_format(operation["projection_id"])[0],
            source_artifact_id=operation["source_artifact_id"],
            tool_release_id=operation["tool_release_id"],
            projection_id=operation["projection_id"],
            run_id=operation["run_id"],
        )
    if manifest is None:
        return {
            **operation,
            "status": "failed",
            "publication": "absent",
            "finished_at": datetime.now(UTC).isoformat(),
        }
    if operation["action"] == "publish-projection":
        role = projection_format(operation["projection_id"])[1]
        if manifest.payload(role).sha256 != operation.get("expected_payload_sha256"):
            return {
                **operation,
                "status": "failed",
                "publication": "different_payload",
                "error_code": "named_projection_bytes_differ",
            }
    for payload in manifest.payloads:
        for _ in store.read_payload(payload):
            pass
    return {
        **operation,
        "status": "completed",
        "artifact_id": manifest.artifact_id,
        "parents": [p.to_dict() for p in manifest.parents],
        "parameters": manifest.parameters.value(),
        "finished_at": datetime.now(UTC).isoformat(),
    }


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
    view = _local_request(
        base + "/api/native-recording/status", token=current["control_token"], method="GET"
    )
    if args.action != "status":
        observed = view["status"]
        kind = {"start": "start_new_session", "prefix": "export_p1_native_prefix"}.get(
            args.action, args.action
        )
        if args.action != "start" and observed["capture_profile_id"] != P1_PROFILE:
            raise BoundaryError("recording", "p1_recording_session_required")
        body = {
            "kind": kind,
            "runtime_instance_id": observed["runtime_instance_id"],
            "recording_session_id": observed["recording_session_id"],
            "source_segment_id": (observed["source"]["segment_id"] if observed["source"] else None),
            "source_kind": args.origin,
            "actor_id": None,
            "command_id": args.command_id or str(uuid4()),
        }
        if args.action == "start":
            body["capture_profile_id"] = P1_PROFILE
        try:
            view = _local_request(
                base + "/api/native-recording/command",
                token=current["control_token"],
                body=body,
                timeout=45,
            )
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


def _storage(argv: list[str]) -> int:
    from spireagent.workbench.developer_server import instance_lock

    from .p1_recording_storage import (
        peer_binding,
        reconcile_storage,
        run_storage,
        storage_error_status,
        storage_intent,
    )

    parser = argparse.ArgumentParser(description="P1 preservation and exact-ID restore")
    parser.add_argument("action", choices=(
        "preserve", "restore", "verify", "reconcile", "cool", "restore-expanded", "status",
    ))
    parser.add_argument("--store", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--peer", help="Store directory or s3 (credentials from environment)")
    parser.add_argument("--artifact")
    parser.add_argument("--config", type=Path,
                        help="open Application owner; required for expanded cooling/restore/status")
    args = parser.parse_args(argv)
    if args.store == "s3":
        parser.error("--store is the local application Store; --peer may be s3")
    if args.config is not None:
        from urllib.error import HTTPError

        from .developer import ProjectConfig
        from .developer_server import _local_request, running
        from .local_recording_import import _selected_store

        config = ProjectConfig.load(args.config)
        selected, _ = _selected_store(config, require_curation=False)
        if (config.state_dir.resolve() != args.state.resolve()
                or not isinstance(selected.blobs, LocalBlobStore)
                or selected.blobs.root != Path(args.store).resolve()):
            raise BoundaryError("p1_storage", "application_store_binding_mismatch")
        current = running(config)
        if current is None:
            raise BoundaryError("p1_storage", "open_workbench_first")
        if args.action not in {"status", "reconcile"} and (not args.artifact or not args.peer):
            parser.error("storage action requires --peer and --artifact")
        base = f"http://127.0.0.1:{current['port']}"
        route = ("/api/local-recordings/import/status" if args.action == "status"
                 else "/api/local-recordings/storage/reconcile" if args.action == "reconcile"
                 else "/api/local-recordings/storage")
        body = (None if args.action == "status" else {} if args.action == "reconcile" else {
            "action": args.action, "artifact_id": args.artifact, "peer": peer_binding(args.peer),
        })
        try:
            result = _local_request(
                base + route, token=current["control_token"], body=body,
                method="GET" if args.action == "status" else "POST", timeout=15,
            )
        except HTTPError as error:
            value = json.loads(error.read(4096))
            raise BoundaryError("p1_storage", value.get("error", "request_rejected")) from error
        except (OSError, ValueError) as error:
            raise BoundaryError("p1_storage", "check_original_storage_status_no_retry") from error
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.action in {"cool", "restore-expanded", "status"}:
        parser.error("expanded cooling/restore/status requires --config of the open Application")
    local = open_store(args.store)
    args.state.mkdir(parents=True, exist_ok=True)
    with instance_lock(args.state / "instance.lock"):
        path = args.state / OPERATION_FILE
        previous = json.loads(path.read_bytes()) if path.exists() else {}
        if args.action == "reconcile":
            if previous.get("action") != "storage":
                raise BoundaryError("p1_storage", "storage_intent_required")
            intent = previous
        else:
            _operation_guard(previous)
            if not args.peer or not args.artifact:
                parser.error("storage action requires --peer and --artifact")
            intent = {
                "schema": SCHEMA,
                **storage_intent(args.action, args.artifact, peer_binding(args.peer)),
            }
            intent = _carry_operation_state(previous, intent)
            atomic_json(path, intent)
        try:
            if args.action == "reconcile":
                result = reconcile_storage(local, intent, scratch=args.state)
            else:
                result = run_storage(local, intent, scratch=args.state)
        except Exception as error:
            atomic_json(
                path,
                {
                    **intent,
                    "status": storage_error_status(error),
                    "error_code": getattr(error, "code", type(error).__name__),
                },
            )
            raise
        if result["status"] == "completed" and result.get("storage_receipt") is not None:
            receipts = previous.get("_storage_receipts", {})
            receipts[result["operation_id"]] = {
                key: result[key]
                for key in (
                    "operation_id",
                    "storage_action",
                    "peer",
                    "artifact_id",
                    "storage_receipt",
                )
            }
            result["_storage_receipts"] = receipts
        atomic_json(path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "completed" else 1


def _maintenance(argv: list[str]) -> int:
    from .developer import ProjectConfig
    from .developer_server import _local_request, running
    from .p1_recording_storage import peer_binding

    parser = argparse.ArgumentParser(
        description="Configure the existing Workbench's closed-recording maintenance"
    )
    parser.add_argument("action", choices=("status", "enable", "disable"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--peer", help="explicit preservation Store; omitted keeps the current policy"
    )
    parser.add_argument("--candidate-id", action="append")
    parser.add_argument("--from-now", action="store_true")
    parser.add_argument("--cohort-policy", type=Path,
                        help="fixed intake/version/holdout policy; keeps the existing owner")
    parser.add_argument("--cool-recent", action=argparse.BooleanOptionalAction, default=None,
                        help="retain newest two scoped sessions after full independent restore")
    parser.add_argument(
        "--origin",
        choices=("declared_human", "agent_native_ui", "unknown"),
        default="declared_human",
    )
    args = parser.parse_args(argv)
    current = running(ProjectConfig.load(args.config))
    if current is None:
        raise BoundaryError("p1_maintenance", "open_workbench_first")
    url = f"http://127.0.0.1:{current['port']}/api/local-recordings/maintenance"
    try:
        value = _local_request(url, token=current["control_token"], method="GET")
    except TimeoutError:
        # Only this read may be queried again. A timed-out mutation is never retried.
        value = {"status": "busy", "message": "在忙，稍后再查", "retry_after_seconds": 1}
    except URLError as error:
        if not isinstance(error.reason, TimeoutError):
            raise
        value = {"status": "busy", "message": "在忙，稍后再查", "retry_after_seconds": 1}
    if args.action != "status":
        if value.get("status") == "busy":
            raise BoundaryError("p1_maintenance", "busy_try_later")
        policy = value["policy"]
        value = _local_request(
            url,
            token=current["control_token"],
            body={
                "enabled": args.action == "enable",
                "peer": peer_binding(args.peer) if args.peer else policy["peer"],
                "projection_id": policy["projection_id"],
                "scope": (
                    {"candidate_ids": args.candidate_id}
                    if args.candidate_id
                    else {"from_now": True, "declared_origins": [args.origin]}
                    if args.from_now
                    else policy.get("scope")
                ),
                "cool_recent": (args.cool_recent if args.cool_recent is not None
                                else policy.get("cool_recent", False)),
                **({"cohort_policy": str(args.cohort_policy.resolve())}
                   if args.cohort_policy else {}),
            },
        )
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0
