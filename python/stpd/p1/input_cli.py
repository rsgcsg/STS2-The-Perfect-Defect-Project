"""Prepare versioned research inputs; never starts training or a game."""
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

from .data import json_bytes
from .input import SORTED_INPUT_SPEC, V2_INPUT_SPEC
from .spec import MStdConfig
from .weighting import CHECKPOINT_POLICY


def request(*, max_seconds: int, max_epochs: int, stop_after: int | None) -> dict[str, Any]:
    if type(max_seconds) is not int or max_seconds <= 300:
        raise ValueError("training allowance must include the existing 300 second save margin")
    if stop_after is not None and (type(stop_after) is not int or stop_after < 1):
        raise ValueError("positive bounded optimizer updates required")
    return {"intent_id": uuid.uuid4().hex, "config": MStdConfig(
        device="cuda:0", max_epochs=max_epochs).to_dict(), "max_seconds": max_seconds,
        "stop_after": stop_after, "input_projection": V2_INPUT_SPEC,
        "checkpoint_policy": CHECKPOINT_POLICY}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    recipe = commands.add_parser("request")
    recipe.add_argument("--output", type=Path, required=True)
    recipe.add_argument("--max-seconds", type=int, required=True)
    recipe.add_argument("--max-epochs", type=int, default=15)
    recipe.add_argument("--stop-after", type=int)
    sorted_model = commands.add_parser("register-sorted")
    sorted_model.add_argument("--package", type=Path, required=True)
    sorted_model.add_argument("--manifest", type=Path, required=True)
    sorted_model.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "request":
        value = request(max_seconds=args.max_seconds, max_epochs=args.max_epochs,
                        stop_after=args.stop_after)
        with args.output.open("xb") as handle:
            handle.write(json_bytes(value))
        print(json.dumps({"request": str(args.output.resolve()), "training_started": False}))
    else:
        from .export import register

        descriptor = json.loads((args.package / "model.json").read_bytes())
        entry = register(args.package, args.output, json.loads(args.manifest.read_bytes()),
                         entry_id="p1-mstd-content-sorted-" + descriptor["package_sha256"][:16],
                         label="M-std content sorted (original weights)",
                         input_projection=SORTED_INPUT_SPEC)
        print(json.dumps({"entry": entry, "registration": str(args.output.resolve()),
                          "activated": False, "game_started": False}))


if __name__ == "__main__":
    main()
