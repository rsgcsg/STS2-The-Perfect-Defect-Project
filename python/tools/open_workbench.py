"""Open the approved developer checkout with the lighter, locked collector profile."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    home = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share")
    parser.add_argument("--config", type=Path, default=home / "spireagent/workbench/project.json")
    parser.add_argument("--hub-url", default="")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    uv, npm = shutil.which("uv"), shutil.which("npm")
    if uv is None or npm is None:
        print("Install uv and Node 20+ once, then run this launcher again.")
        return 1
    # Never replace configuration, switch branches, download models or start gameplay.
    commands = [[uv, "sync", "--locked", "--extra", "cloud", "--extra", "recording-intake",
                 "--inexact"], [npm, "ci"]]
    base = [uv, "run", "--locked", "python", "-m", "spireagent.workbench", "project"]
    if not args.config.exists():
        commands.append(
            base
            + [
                "setup",
                "--skip-install",
                "--config",
                str(args.config),
                "--state-dir",
                str(args.config.parent),
                "--hub-url",
                args.hub_url,
            ]
        )
    commands.append(
        base
        + ["open", "--config", str(args.config)]
        + (["--no-browser"] if args.no_browser else [])
    )
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            print("Open did not complete. Existing recordings and project settings were retained.")
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
