"""One portable repository gate; no game, model-weight, or service authority."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = (
    "README.md", "AGENTS.md", "CONTRIBUTING.md", "docs/DOCUMENT_MAP.md",
    "docs/DEVELOPMENT_WORKFLOW.md", "docs/PROJECT_SYSTEM.md", "docs/CODE_STYLE.md",
    "docs/ENGINEERING_GOVERNANCE.md", "docs/TESTING.md", "docs/NEW_ENGINEER_GUIDE.md",
    "docs/memory/CURRENT.md",
)


class RepositoryGateError(RuntimeError):
    """A source/governance failure, never a scientific verdict."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RepositoryGateError(message)




def validate_repository(root: Path) -> None:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    require(project["project"]["requires-python"] == ">=3.11,<3.12", "Python contract drift")
    require((root / ".python-version").read_text().strip() == "3.11", "Python bootstrap drift")
    require((root / "uv.lock").is_file(), "missing uv.lock")
    require("* text=auto eol=lf" in (root / ".gitattributes").read_text(), "missing LF contract")
    for route in ROUTES:
        require((root / route).is_file(), f"missing canonical route: {route}")
    current = (root / "docs/memory/CURRENT.md").read_bytes()
    require(len(current) <= 3072, "CURRENT exceeds 3 KiB; move evidence to canonical records")
    for path in ("AGENTS.md", "CONTRIBUTING.md"):
        text = (root / path).read_text(encoding="utf-8")
        require("uv sync --locked --all-extras" in text, f"{path}: locked bootstrap missing")
        require("tools/project.py check" in text, f"{path}: common gate missing")
        require("pip install -e ." not in text and "python3 -m unittest" not in text,
                f"{path}: obsolete second bootstrap/test path")
    require("Python 3.9" not in (root / "docs/CODE_STYLE.md").read_text(),
            "code style baseline drift")
    for route in ROUTES:
        source = root / route
        for link in re.findall(r"\]\(([^)]+)\)", source.read_text(encoding="utf-8")):
            link = link.split("#", 1)[0]
            if not link or "://" in link or not link.endswith(".md"):
                continue
            target = (source.parent / link).resolve()
            require(target.is_relative_to(root.resolve().parent) and target.is_file(),
                    f"{route}: broken local document link: {link}")


def portable_commands(python: str = sys.executable) -> tuple[tuple[str, ...], ...]:
    return (
        (python, "tools/doctor.py"),
        (python, "-m", "ruff", "check", "."),
        (python, "-m", "mypy", "stpd", "spireagent", "tools"),
        ("npm", "run", "check:connector-sdk"),
        (python, "-m", "pytest", "-v", "-ra", "--durations=30",
         "-o", "faulthandler_timeout=60",
         "--junitxml=.local/pytest.xml"),
        (python, "-m", "spireagent.workbench", "e2e", "--output", ".local/cpu-e2e.json"),
        (python, "-m", "stpd.cloud_jobs.smoke", "--output", ".local/cloud-worker-cpu.json"),
        (python, "-m", "compileall", "-q", "stpd", "spireagent", "tests", "tools", "deploy"),
        ("uv", "build"),
        ("git", "diff", "--check"),
        ("git", "show", "--format=", "--check", "HEAD"),
    )


def invoke(command: tuple[str, ...], root: Path) -> None:
    executable = shutil.which(command[0])
    require(executable is not None, f"missing executable: {command[0]}")
    arguments = [str(executable), *command[1:]]
    if os.name == "nt" and Path(str(executable)).suffix.lower() in {".cmd", ".bat"}:
        arguments = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c",
                     subprocess.list2cmdline(arguments)]
    print(json.dumps({"stage": "portable", "command": list(command)}), flush=True)
    started = time.monotonic()
    try:
        subprocess.run(arguments, cwd=root, check=True)
    finally:
        print(json.dumps({"stage": "portable_duration", "command": list(command),
                          "seconds": round(time.monotonic() - started, 3)}), flush=True)


def check(root: Path, base: str | None = None) -> None:
    require(sys.version_info[:2] == (3, 11), "run via the locked Python 3.11 uv environment")
    validate_repository(root)
    for command in portable_commands():
        invoke(command, root)
    if base and base != "0" * 40:
        require(re.fullmatch(r"[0-9a-f]{40}", base) is not None, "base must be an exact SHA")
        invoke(("git", "diff", "--check", f"{base}...HEAD"), root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("context", "check", "closeout"))
    parser.add_argument("--base", help="exact base SHA for committed patch hygiene")
    args = parser.parse_args(argv)
    try:
        if args.command == "context":
            print(json.dumps({"schema": "stpd-project-context-1", "routes": ROUTES,
                              "python": ">=3.11,<3.12", "integration": "develop",
                              "evidence": "routing only"}, indent=2))
            return 0
        check(ROOT, args.base)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
        require(not dirty, "checkout changed during portable gate; commit and rerun")
        print(json.dumps({"schema": "stpd-portable-result-1", "source": head,
                          "dirty": dirty, "verdict": "PORTABLE_LOCAL_PASS",
                          "non_claims": ["Windows/Linux CI", "runtime", "data", "training",
                                         "scientific readiness", "pre-Full-Run readiness"]},
                         indent=2))
        return 0
    except (RepositoryGateError, OSError, subprocess.CalledProcessError,
            ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"stage": "repository", "verdict": "FAIL", "error": str(exc)}),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
