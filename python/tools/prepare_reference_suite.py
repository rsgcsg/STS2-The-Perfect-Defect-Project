"""Freeze the new ten-seed four-Agent Reference manifest for an exact game version."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from spireagent.json_boundary import json_bytes
from stpd.evaluation.suites import four_agent_reference_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    raw = json_bytes(four_agent_reference_manifest(args.game_version))
    with args.output.open("xb") as output:
        output.write(raw)
    print(json.dumps({"manifest": str(args.output), "sha256": hashlib.sha256(raw).hexdigest(),
                      "seeds": 10, "agents": 4, "planned_attempts": 40}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
