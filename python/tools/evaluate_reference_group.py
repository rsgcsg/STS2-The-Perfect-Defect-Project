"""Start/read/control the same Application-owned diagnostic Reference group as the GUI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.developer_server import running
from spireagent.workbench.hub_client import NoRedirect


def command(config: ProjectConfig, action: str,
            body: dict[str, Any] | None = None) -> dict[str, Any]:
    current = running(config)
    if current is None:
        raise BoundaryError("reference_evaluation", "open_workbench_first")
    if action not in {"status", "start", "pause", "resume", "stop", "reconcile"}:
        raise ValueError("unsupported group action")
    request = Request(f"http://127.0.0.1:{current['port']}/api/reference-evaluations/{action}",
                      data=None if action == "status" else json_bytes(body),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + current["control_token"]})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("group response over limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid group response")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("action",
                        choices=("start", "status", "pause", "resume", "stop", "reconcile"))
    parser.add_argument("--input", type=Path, help="exact same request/control JSON as the API")
    args = parser.parse_args(argv)
    if (args.action == "status") != (args.input is None):
        parser.error("start/control require --input; status has no request body")
    try:
        body = json.loads(args.input.read_bytes()) if args.input else None
        print(json.dumps(command(ProjectConfig.load(args.config), args.action, body),
                         ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "unconfirmed", "error_code": (
            error.code if isinstance(error, BoundaryError) else type(error).__name__),
            "automatic_retry": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
