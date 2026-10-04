"""An image may reuse its old clone layer when a new exact source is requested."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def test_image_cached_clone_fetches_new_exact_source(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    recipe = (root / "deploy/cloud-worker/Dockerfile").read_text()
    fetch = re.search(r'git fetch origin "\$STPD_SOURCE_REVISION"', recipe)
    checkout = re.search(r'git checkout --detach "\$STPD_SOURCE_REVISION"', recipe)
    assert fetch and checkout and fetch.start() < checkout.start()

    def git(path: Path, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init")
    git(origin, "config", "user.name", "Fixture")
    git(origin, "config", "user.email", "fixture@example.invalid")
    (origin / "source").write_text("first")
    git(origin, "add", ".")
    git(origin, "commit", "-m", "first")
    clone = tmp_path / "cached-clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (origin / "source").write_text("approved-new-source")
    git(origin, "commit", "-am", "new")
    target = git(origin, "rev-parse", "HEAD")
    missing = subprocess.run(["git", "-C", str(clone), "cat-file", "-e", target],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert missing.returncode != 0
    git(clone, "fetch", "origin", target)
    git(clone, "checkout", "--detach", target)
    assert git(clone, "rev-parse", "HEAD") == target
    assert (clone / "source").read_text() == "approved-new-source"
    assert git(clone, "status", "--porcelain") == ""


def test_worker_sync_ignores_injected_index_and_keeps_locked_extras() -> None:
    root = Path(__file__).resolve().parents[1]
    recipe = (root / "deploy/cloud-worker/Dockerfile").read_text()
    worker = re.search(r"(?m)^\s*worker\)\s*(.*?)\s*;;\s*\\?$", recipe)

    assert worker is not None
    assert worker.group(1).split() == [
        "env", "-u", "UV_INDEX_URL", "uv", "sync", "--locked", "--all-extras",
    ]
