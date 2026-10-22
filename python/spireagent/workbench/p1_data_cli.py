"""Normal Application client for incremental P1 preparation and fixed snapshots."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.developer_server import _local_request, running


def p1_data_command(config: ProjectConfig, action: str, body: object) -> dict[str, Any]:
    """Offer once; callers inspect the original immutable selection after uncertainty."""
    if action not in {"prepare", "freeze"} or not isinstance(body, dict):
        raise BoundaryError("p1_data", "invalid_data_request")
    current = running(config)
    if current is None:
        raise BoundaryError("p1_data", "open_workbench_first")
    try:
        return _local_request(
            f"http://127.0.0.1:{current['port']}/api/p1-data/{action}",
            token=current["control_token"], body=body, method="POST", timeout=600,
        )
    except HTTPError as error:
        value = json.loads(error.read(1024 * 1024))
        code = value.get("error") if isinstance(value, dict) else None
        raise BoundaryError("p1_data", code or "application_request_rejected") from error
    except (OSError, ValueError) as error:
        raise BoundaryError(
            "p1_data", "request_failed_inspect_snapshot_no_automatic_retry"
        ) from error
