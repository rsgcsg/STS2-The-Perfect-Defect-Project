"""Local CLI client for the already-owned workbench model service."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, decode_json, digest
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.developer_server import instance_lock, running
from spireagent.workbench.hub_client import NoRedirect


def model_command(
    config: ProjectConfig,
    action: str,
    *,
    selection: str | None = None,
    artifact: str | None = None,
    runtime_archive: Path | None = None,
    runtime_profile: str | None = None,
) -> dict[str, Any]:
    if runtime_profile is not None and (action != "install-runtime" or runtime_archive is None):
        raise BoundaryError("local_model", "runtime_profile_requires_offline_install")
    if runtime_profile not in {None, "text-menu-v1", "text-menu-m2-v1",
                               "text-menu-m2-v2"}:
        raise BoundaryError("local_model", "unsupported_runtime_profile")
    if runtime_archive is not None:
        if action != "install-runtime":
            raise BoundaryError("local_model", "archive_requires_install_runtime_action")
        with instance_lock(config.state_dir / "instance.lock"):
            if running(config) is not None:
                raise BoundaryError("local_model", "close_workbench_before_offline_runtime_install")
            from spireagent.workbench.local_models import LocalModelService
            from spireagent.workbench.runtime_install import install_runtime

            service = LocalModelService(config)
            if service.state["status"] == "recovery_required":
                raise BoundaryError("local_model", "previous_operation_requires_recovery")
            if runtime_profile in {"text-menu-v1", "text-menu-m2-v1",
                                   "text-menu-m2-v2"}:
                # Legacy profiles may stage the current kit's inventoried pin.
                # v2 requires an already provisioned application-owned pin;
                # no source-tree or v1 kit fallback can create it.
                from spireagent.policy_files import _object_file
                from spireagent.workbench.kit_runtime import text_runtime_pin
                from spireagent.workbench.local_models import TEXT_PROFILES

                _, legacy_name, _, _ = TEXT_PROFILES[runtime_profile]
                destination = service.private_root / Path(legacy_name).name
                if destination.is_symlink():
                    raise BoundaryError("local_model", "private_model_state_unsafe")
                if (runtime_profile != "text-menu-m2-v2"
                        and not destination.exists() and not destination.is_symlink()):
                    staged = service.root / legacy_name
                    if staged.exists() or staged.is_symlink():
                        if (staged.is_symlink() or runtime_archive.is_symlink()
                                or runtime_archive.stat().st_size > 32 * 1024 * 1024):
                            raise BoundaryError("local_model", "runtime_profile_staging_unsafe")
                        raw = staged.read_bytes()
                        text_runtime_pin(raw, runtime_archive.read_bytes(),
                                         memory=runtime_profile == "text-menu-m2-v1")
                        service.private_root.mkdir(parents=True, exist_ok=True)
                        if service.private_root.is_symlink():
                            raise BoundaryError("local_model", "private_model_state_unsafe")
                        atomic_json(destination, _object_file(staged))
                if selection is not None:
                    selected = service.selection(selection)
                    if selected.get("runtime_profile") != runtime_profile:
                        raise BoundaryError("local_model", "selection_runtime_profile_mismatch")
                directory, pin = (service.text_runtime_profile()
                                  if runtime_profile == "text-menu-v1"
                                  else service.text_runtime_profile(runtime_profile))
            else:
                directory, pin = service.runtime_profile(selection)
            installed = install_runtime(
                directory,
                pin,
                service._connector_pin(),
                archive=runtime_archive,
            )
            if runtime_profile == "text-menu-m2-v2":
                from spireagent.workbench.runtime_install import (
                    CONNECTOR_PACKAGE,
                    RUNTIME_PACKAGE,
                    v2_sdk_available,
                )

                sdk = (directory / "runtime/node_modules" / RUNTIME_PACKAGE /
                       "node_modules" / CONNECTOR_PACKAGE / "dist/index.js")
                if not v2_sdk_available(sdk):
                    raise BoundaryError("local_model", "v2_runtime_contract_unavailable")
            return installed
    current = running(config)
    if current is None:
        raise BoundaryError("local_model", "open_workbench_first")
    route = "/api/local-models"
    body = None
    if action == "status":
        route += "/status"
    elif action == "readiness":
        if not selection:
            raise BoundaryError("local_model", "selection_required")
        route += "/readiness?" + urlencode({"selection_id": selection})
    elif action == "start":
        if not selection:
            raise BoundaryError("local_model", "selection_required")
        route += "/start"
        body = {"selection_id": selection}
    elif action == "download":
        route += "/download"
        body = {"artifact_id": digest(artifact, "local_model.artifact")}
    elif action == "install-runtime":
        route += "/install-runtime"
        body = {}
    elif action in {"human", "shadow", "one_step", "auto", "stop"}:
        route += "/command"
        body = {"action": action}
    elif action != "catalog":
        raise BoundaryError("local_model", "unsupported_local_command")
    request = Request(
        f"http://127.0.0.1:{current['port']}" + route,
        data=canonical_json(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + current["control_token"],
        },
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=25) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError
        result = decode_json(raw)
        if not isinstance(result, dict) or result.get("schema") != "stpd/local-models-v1":
            raise ValueError
        return result
    except (OSError, ValueError):
        raise BoundaryError(
            "local_model", "workbench_request_failed_check_status_before_retry"
        ) from None
