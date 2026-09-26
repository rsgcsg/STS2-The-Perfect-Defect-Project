"""Local CLI client for the already-owned workbench model service."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, decode_json, digest
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.developer_server import instance_lock, running
from spireagent.workbench.hub_client import NoRedirect


def model_command(
    config: ProjectConfig,
    action: str,
    *,
    selection: str | None = None,
    artifact: str | None = None,
    runtime_archive: Path | None = None,
) -> dict[str, Any]:
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
            directory, pin = service.runtime_profile(selection)
            return install_runtime(
                directory,
                pin,
                service._connector_pin(),
                archive=runtime_archive,
            )
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
