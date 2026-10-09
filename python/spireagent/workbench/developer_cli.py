"""Unified developer entry: python -m spireagent.workbench project --help."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json, digest, text
from spireagent.workbench.developer import DEFAULT_CONFIG, ROOT, ProjectConfig, doctor, setup
from spireagent.workbench.developer_server import (
    instance_lock,
    open_project,
    serve,
    status_project,
    stop_project,
)
from spireagent.workbench.hub_client import HubClient
from spireagent.workbench.identity import LocalIdentity


def inspect_policy(path: Path) -> dict[str, Any]:
    """Inspect an existing immutable selection without loading weights or acquiring control."""
    raw = path.read_bytes()
    value = decode_json(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema") != "sts2.policy-runtime/policy-manifest-1"
    ):
        raise BoundaryError("policy", "unsupported_manifest")
    artifact, adapter = value.get("artifact"), value.get("adapter")
    if not isinstance(artifact, dict) or not isinstance(adapter, dict):
        raise BoundaryError("policy", "invalid_manifest")
    if adapter.get("protocol") != "sts2.policy-runtime/decision-only-ndjson-1":
        raise BoundaryError("policy", "unsupported_adapter_protocol")
    checkpoint = digest(artifact.get("sha256"), "policy.checkpoint")
    code = digest(adapter.get("code_sha256"), "policy.code")
    return {
        "schema": "stpd/policy-inspection-v1",
        "status": "manifest_inspected",
        "manifest_id": text(value.get("manifest_id"), "policy.manifest_id"),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "checkpoint_sha256": checkpoint,
        "adapter_code_sha256": code,
        "loaded": False,
        "activated": False,
        "non_claims": ["weights available", "current runtime compatible", "model quality"],
    }


def main(argv: list[str] | None = None) -> int:
    from spireagent.workbench.local_models import TEXT_PROFILES

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "setup",
            "doctor",
            "open",
            "status",
            "stop",
            "serve",
            "download",
            "policy",
            "model",
            "migrate-model-state",
            "environment-profile",
            "credential",
            "collection-tool",
            "collection-upgrade",
            "collect-source3",
        ),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local/developer")
    parser.add_argument("--hub-url", default="")
    parser.add_argument("--platform-url", default="")
    parser.add_argument("--delivery-config", type=Path)
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="only configure; doctor still requires the exact installed tools",
    )
    parser.add_argument(
        "--replace-config",
        action="store_true",
        help="explicitly replace local settings with these setup arguments",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--artifact")
    parser.add_argument(
        "--role", action="append", help="own payload role, repeat for multiple roles"
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--legacy-python-root", type=Path,
                        help="explicit old checkout python directory for model metadata archive")
    parser.add_argument("--selection", help="reviewed local policy registry selection")
    parser.add_argument(
        "--runtime-archive",
        type=Path,
        help="explicit local pinned Runtime archive; close workbench first",
    )
    parser.add_argument("--runtime-profile", choices=tuple(TEXT_PROFILES),
                        help="approved local Runtime profile for offline installation")
    parser.add_argument("--expected-active-sha256")
    parser.add_argument("--new-runtime-profile", type=Path)
    parser.add_argument("--expected-new-profile-sha256")
    parser.add_argument("--archived-profile-sha256")
    parser.add_argument(
        "--action",
        default="catalog",
        choices=(
            "catalog",
            "status",
            "readiness",
            "download",
            "install-runtime",
            "initialize-runtime-generation",
            "upgrade-runtime-generation",
            "rollback-runtime-generation",
            "start",
            "human",
            "shadow",
            "one_step",
            "auto",
            "stop",
            "reconcile",
        ),
        help="local model action; generation changes require a stopped Workbench",
    )
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--request-id", help="exact original pending native Runtime request ID")
    parser.add_argument(
        "--tool-directory", type=Path, help="absolute public CollectionTool directory"
    )
    parser.add_argument("--tool-release-id", help="exact trusted CollectionTool release ID")
    parser.add_argument(
        "--replace-tool",
        action="store_true",
        help="explicitly replace a previous private CollectionTool registration",
    )
    parser.add_argument("--enrollment-id")
    parser.add_argument("--phase", choices=("prepare", "activate"))
    parser.add_argument("--game-directory", type=Path)
    parser.add_argument(
        "--candidate-directory", type=Path,
        help="trusted exact Managed candidate; configure while Workbench is stopped",
    )
    parser.add_argument("--host-package-directory", type=Path,
                        help="private exact installed Host package for Managed environment")
    parser.add_argument("--host-package-pin", type=Path,
                        help="operator-provided exact private Host package pin JSON")
    parser.add_argument("--input-profile", choices=("text-menu-v1", "text-menu-v2"),
                        help="explicit Managed text-menu profile in the private Host setup")
    parser.add_argument("--host-local-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed")
    parser.add_argument("--target-choices", type=int, default=100)
    parser.add_argument("--max-submissions", type=int, default=100)
    parser.add_argument("--deadline-ms", type=int, default=900_000)
    parser.add_argument("--experimental-build-acknowledged", action="store_true")
    parser.add_argument("--experimental-connector-acknowledged", action="store_true")
    parser.add_argument(
        "--no-source3",
        action="store_true",
        help="explicitly omit Source3 overlay; direct Agent evidence still records",
    )
    parser.add_argument(
        "--predecessor-report-path",
        type=Path,
        help="explicit unknown predecessor report for a distinct fresh episode",
    )
    parser.add_argument("--predecessor-report-sha256")
    parser.add_argument("--predecessor-marker-sha256")
    parser.add_argument(
        "--predecessor-source3-bundle",
        type=Path,
        help="recorded predecessor only; forbidden for Source3-off; no preflight packing",
    )
    parser.add_argument(
        "--predecessor-source3-content-id",
        help="paired with the recorded predecessor's Source3 bundle",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="validate collector metadata without Application, SDK or game launch",
    )
    args = parser.parse_args(argv)
    try:
        result: Any
        if args.runtime_profile is not None and args.command != "model":
            raise BoundaryError("local_model", "runtime_profile_requires_model_command")
        if (args.command != "model" and any(value is not None for value in (
                args.expected_active_sha256, args.new_runtime_profile,
                args.expected_new_profile_sha256, args.archived_profile_sha256))):
            raise BoundaryError("local_model", "runtime_generation_arguments_require_model_command")
        if args.command == "setup":
            result = setup(
                args.config.resolve(),
                state_dir=args.state_dir,
                hub_url=args.hub_url,
                platform_url=args.platform_url,
                delivery_config=args.delivery_config,
                install=not args.skip_install,
                replace_config=args.replace_config,
            )
        elif args.command == "policy":
            if args.manifest is None:
                raise BoundaryError("policy", "manifest_required")
            result = inspect_policy(args.manifest)
        else:
            # A source upgrade must not strand its still-running predecessor.
            # Health-bound observation/stop needs only the validated local address;
            # opening, serving and downloading retain exact current-combination gates.
            config = ProjectConfig.load(
                args.config, require_current_combination=args.command not in {"status", "stop"}
            )
            if args.command == "collect-source3":
                from spireagent.workbench.native_source3_collection import (
                    CollectionRequest,
                    collect_source3,
                    metadata_preflight,
                )

                if (
                    args.game_directory is None
                    or args.host_local_root is None
                    or args.output is None
                    or args.seed is None
                ):
                    raise BoundaryError("source3_collection", "collection_paths_and_seed_required")
                request = CollectionRequest(
                    installation=args.game_directory,
                    host_local_root=args.host_local_root,
                    output=args.output,
                    seed=args.seed,
                    target_choices=args.target_choices,
                    max_submissions=args.max_submissions,
                    deadline_ms=args.deadline_ms,
                    experimental_build_acknowledged=args.experimental_build_acknowledged,
                    experimental_connector_acknowledged=args.experimental_connector_acknowledged,
                    record_source3=not args.no_source3,
                    predecessor_report_path=args.predecessor_report_path,
                    predecessor_report_sha256=args.predecessor_report_sha256,
                    predecessor_marker_sha256=args.predecessor_marker_sha256,
                    predecessor_source3_bundle=args.predecessor_source3_bundle,
                    predecessor_source3_content_id=args.predecessor_source3_content_id,
                )
                result = (
                    metadata_preflight(config, request)
                    if args.plan_only
                    else collect_source3(config, args.config, request)
                )
            elif args.command == "model":
                from spireagent.workbench.local_model_cli import model_command

                result = model_command(
                    config,
                    args.action,
                    selection=args.selection,
                    artifact=args.artifact,
                    request_id=args.request_id,
                    runtime_archive=args.runtime_archive,
                    runtime_profile=args.runtime_profile,
                    expected_active_sha256=args.expected_active_sha256,
                    new_runtime_profile=args.new_runtime_profile,
                    expected_new_profile_sha256=args.expected_new_profile_sha256,
                    archived_profile_sha256=args.archived_profile_sha256,
                )
            elif args.command == "environment-profile":
                from spireagent.workbench.local_environment import configure_managed_host

                if (args.candidate_directory is None or args.host_package_directory is None
                        or args.host_package_pin is None or args.input_profile is None):
                    raise BoundaryError("local_environment", "candidate_and_host_pin_required")
                pin = decode_json(args.host_package_pin.read_bytes())
                if not isinstance(pin, dict):
                    raise BoundaryError("local_environment", "host_package_pin_invalid")
                result = configure_managed_host(
                    config, args.candidate_directory,
                    host_root=args.host_package_directory, host_pin=pin,
                    input_profile=args.input_profile,
                )
            elif args.command == "migrate-model-state":
                from spireagent.workbench.model_state_migration import migrate_legacy_model_state

                if args.legacy_python_root is None:
                    raise BoundaryError("local_model", "legacy_python_root_required")
                result = migrate_legacy_model_state(config, args.legacy_python_root)
            elif args.command == "collection-upgrade":
                from spireagent.workbench.collection_upgrade import upgrade

                if not args.enrollment_id or not args.tool_release_id or not args.phase:
                    raise BoundaryError("collection_upgrade", "enrollment_tool_and_phase_required")
                result = upgrade(
                    args.config,
                    args.enrollment_id,
                    args.tool_release_id,
                    phase=args.phase,
                    game_directory=args.game_directory,
                )
            elif args.command == "collection-tool":
                from spireagent.workbench.collection_tool_registration import (
                    register_collection_tool,
                )

                if args.tool_directory is None or args.tool_release_id is None:
                    raise BoundaryError("collection_tool", "tool_directory_and_release_id_required")
                result = register_collection_tool(
                    config, args.tool_directory, args.tool_release_id, replace=args.replace_tool
                )
            elif args.command == "credential":
                if args.credential_file is None:
                    raise BoundaryError("identity", "private_credential_file_required")
                with instance_lock(config.state_dir / "instance.lock"):
                    result = LocalIdentity(config).replace_credential(args.credential_file)
            elif args.command == "doctor":
                result = doctor(config)
            elif args.command == "open":
                result = open_project(args.config.resolve(), browser=not args.no_browser)
            elif args.command == "stop":
                result = stop_project(config)
            elif args.command == "serve":
                result = serve(config, config_path=args.config)
            elif args.command == "status":
                result = status_project(config)
            else:
                if not args.artifact or not config.hub_url:
                    raise BoundaryError("download", "artifact_and_hub_required")
                result = HubClient(
                    config.hub_url, token=LocalIdentity(config).device_token
                ).download(
                    args.artifact,
                    config.state_dir / "downloads",
                    tuple(args.role) if args.role is not None else None,
                )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if (result.get("status") == "BLOCKED" or args.command == "collect-source3"
                     and result.get("status") in {"failed", "unknown"}) else 0
    except (BoundaryError, OSError, ValueError, subprocess.SubprocessError) as error:
        code = error.code if isinstance(error, BoundaryError) else type(error).__name__
        print(json.dumps({"status": "FAIL", "code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
