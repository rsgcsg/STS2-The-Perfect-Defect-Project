"""Non-GUI client of the existing Application-owned training operation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode

from spireagent.json_boundary import BoundaryError, digest
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.developer_server import _local_request, running
from spireagent.workbench.recipe_contracts import TrainingRequest

ACTIONS = ("capabilities", "status", "start", "pause", "resume", "cancel", "reconcile",
           "amend_wall_budget")


def training_command(
    config: ProjectConfig, action: str, *, body: dict[str, Any] | None = None,
    operation_id: str | None = None, execution: str = "local",
) -> dict[str, Any]:
    """Offer once to the live owner; a transport failure never retries a write."""
    if execution not in {"local", "remote"}:
        raise BoundaryError("local_training", "unsupported_execution")
    if action not in ACTIONS:
        raise BoundaryError("local_training", "unsupported_training_action")
    reading = action in {"status", "capabilities"}
    if (reading and body is not None) or (not reading and body is None):
        raise BoundaryError("local_training", "training_body_mismatch")
    if operation_id is not None and action != "status":
        raise BoundaryError("local_training", "operation_query_requires_status")
    if action == "start" and execution == "local":
        TrainingRequest.from_dict(body)
    current = running(config)
    if current is None:
        raise BoundaryError("local_training", "open_workbench_first")
    route = ("/api/local-training/" if execution == "local" else "/api/remote-training/") + action
    if operation_id is not None:
        route += "?" + urlencode({"operation_id" if execution == "local" else "job_id": digest(
            operation_id, "local_training.operation_id", length=32)})
    try:
        return _local_request(
            f"http://127.0.0.1:{current['port']}" + route,
            token=current["control_token"], body=body,
            method="GET" if reading else "POST", timeout=15,
        )
    except HTTPError as error:
        value = json.loads(error.read(1024 * 1024))
        code = value.get("error") if isinstance(value, dict) else None
        raise BoundaryError("local_training", code or "application_request_rejected") from error
    except (OSError, ValueError) as error:
        raise BoundaryError(
            "local_training", "application_request_failed_check_original_status_no_retry"
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input", type=Path, help="exact start request or control JSON body")
    parser.add_argument("--operation-id",
                        help="optional exact local operation or remote job status")
    parser.add_argument("--execution", choices=("local", "remote"), default="local")
    args = parser.parse_args(argv)
    try:
        body = json.loads(args.input.read_bytes()) if args.input is not None else None
        if body is not None and not isinstance(body, dict):
            raise BoundaryError("local_training", "training_body_mismatch")
        result = training_command(ProjectConfig.load(args.config), args.action,
                                  body=body, operation_id=args.operation_id,
                                  execution=args.execution)
    except (BoundaryError, OSError, ValueError) as error:
        code = error.code if isinstance(error, BoundaryError) else type(error).__name__
        print(json.dumps({"status": "failed", "error_code": code}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0
