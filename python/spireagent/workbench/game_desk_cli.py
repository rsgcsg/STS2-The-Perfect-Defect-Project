"""Mouse GUI and CLI call the same game-desk Application use cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from spireagent.json_boundary import BoundaryError

from .developer import ProjectConfig
from .developer_server import running
from .hub_client import NoRedirect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "status",
            "choices",
            "recording",
            "choices-open",
            "record-start",
            "record-stop",
            "model-auto",
            "model-step",
            "model-stop",
            "rank",
            "mark",
            "mistake",
            "reconnect",
        ),
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--selection-id")
    parser.add_argument("--catalog-key")
    parser.add_argument("--choice", action="append", default=[])
    parser.add_argument("--note")
    parser.add_argument("--step-id")
    parser.add_argument("--artifact", help="read one exact stored recording index")
    args = parser.parse_args(argv)
    config = ProjectConfig.load(args.config)
    current = running(config)
    if current is None:
        raise BoundaryError("game_desk", "open_workbench_first")
    body = None
    route = "/api/game-desk"
    if args.action in {"choices", "recording"}:
        route += "/" + args.action
        if args.action == "recording" and args.artifact:
            route += "?" + urlencode({"artifact": args.artifact})
    elif args.action != "status":
        route += "/" + args.action.replace("-", "_")
        body = (
            {"selection_id": args.selection_id}
            if args.action in {"model-auto", "model-step"}
            else {"catalog_key": args.catalog_key, "ranking": args.choice}
            if args.action == "rank"
            else {"note": args.note, "step_id": args.step_id}
            if args.action == "mark"
            else {"step_id": args.step_id}
            if args.action == "mistake"
            else {}
        )
    request = Request(
        f"http://127.0.0.1:{current['port']}" + route,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + current["control_token"],
        },
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=45) as response:
            raw = response.read(8 * 1024**2 + 1)
        if len(raw) > 8 * 1024**2:
            raise ValueError("response_limit")
        print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
        return 0
    except HTTPError as error:
        value = json.loads(error.read(4096))
        print(
            json.dumps(
                {"error": value.get("error", "request_unconfirmed"), "automatic_retry": False},
                ensure_ascii=False,
            )
        )
        return 1
    except (URLError, OSError, ValueError):
        print(
            json.dumps(
                {
                    "error": "result_unconfirmed" if body is not None else "status_unavailable",
                    "automatic_retry": False,
                },
                ensure_ascii=False,
            )
        )
        return 1
