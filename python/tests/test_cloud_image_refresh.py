"""Execute the image's actual POSIX refresh command against local locked wheels."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux OCI recipe uses a POSIX shell")


def _run(path: Path, *command: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(
        command, cwd=path, env=env, text=True, stderr=subprocess.DEVNULL
    ).strip()


def _wheel(directory: Path, version: str) -> Path:
    stem = f"refresh_fixture-{version}"
    files = {
        "refresh_fixture.py": f"version = {version!r}\n",
        f"{stem}.dist-info/METADATA": (
            f"Metadata-Version: 2.1\nName: refresh-fixture\nVersion: {version}\n"
        ),
        f"{stem}.dist-info/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    records = []
    wheel = directory / f"{stem}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as output:
        for name, content in files.items():
            raw = content.encode()
            digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=")
            records.append(f"{name},sha256={digest.decode()},{len(raw)}")
            output.writestr(name, raw)
        record_name = f"{stem}.dist-info/RECORD"
        output.writestr(record_name, "\n".join([*records, f"{record_name},,"]) + "\n")
    return wheel


@dataclass(frozen=True)
class Refresh:
    clone: Path
    origin: Path
    command: str
    env: dict[str, str]


@pytest.fixture(params=["Pefect", "Perfect"])
def refresh(tmp_path: Path, request) -> Refresh:
    return _make_refresh(tmp_path, request.param, dict(os.environ))


def _make_refresh(tmp_path: Path, repository_spelling: str, inherited: dict[str, str]) -> Refresh:
    assert shutil.which("uv"), "the repository's supported uv bootstrap is required"
    env = {**inherited, "UV_PYTHON": sys.executable, "UV_PYTHON_DOWNLOADS": "never"}
    # These subprocesses install synthetic wheels into a temporary checkout. Never
    # inherit an absolute target that redirects uv sync into the caller's environment.
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    env.pop("VIRTUAL_ENV", None)
    origin_repo = tmp_path / "origin"
    origin_repo.mkdir()
    origin = origin_repo / "python"
    origin.mkdir()
    _run(origin_repo, "git", "init")
    _run(origin, "git", "config", "user.name", "Fixture")
    _run(origin, "git", "config", "user.email", "fixture@example.invalid")
    (origin / ".gitignore").write_text(".venv/\n__pycache__/\n")

    def version(value: str) -> tuple[str, str]:
        wheel = _wheel(tmp_path, value)
        (origin / "pyproject.toml").write_text(
            '[project]\nname = "refresh-application"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11,<3.12"\n'
            f'dependencies = ["refresh-fixture @ {wheel.as_uri()}"]\n'
        )
        _run(origin, "uv", "lock", "--offline", env=env)
        _run(origin, "git", "add", ".")
        _run(origin, "git", "commit", "-m", value)
        return (
            _run(origin, "git", "rev-parse", "HEAD"),
            hashlib.sha256((origin / "uv.lock").read_bytes()).hexdigest(),
        )

    old_head, old_lock = version("1.0")
    clone = tmp_path / "cached-clone"
    _run(tmp_path, "git", "clone", str(origin_repo), str(clone))
    repository = f"https://github.com/rsgcsg/STS2-The-{repository_spelling}-Defect-Project.git"
    _run(clone, "git", "remote", "set-url", "origin", repository)
    for spelling in ("Pefect", "Perfect"):
        _run(
            clone,
            "git",
            "config",
            "--add",
            "url." + str(origin_repo) + ".insteadOf",
            f"https://github.com/rsgcsg/STS2-The-{spelling}-Defect-Project.git",
        )
    clone = clone / "python"
    _run(clone, "uv", "sync", "--locked", "--all-extras", "--offline", env=env)
    new_head, new_lock = version("2.0")
    recipe = Path(__file__).resolve().parents[1] / "deploy/cloud-worker/refresh.Dockerfile"
    command = recipe.read_text().split("RUN ", 1)[1].replace("\\\n", "\n")
    return Refresh(
        clone=clone,
        origin=origin,
        command=command,
        env={
            **env,
            "QUALIFIED_WORKER_IMAGE": "registry.invalid/image@sha256:" + "a" * 64,
            "STPD_IMAGE_PROFILE": "worker",
            "STPD_SOURCE_REVISION": new_head,
            "QUALIFIED_SOURCE_REVISION": old_head,
            "QUALIFIED_LOCK_SHA256": old_lock,
            "EXPECTED_NEW_LOCK_SHA256": new_lock,
        },
    )


def _execute(refresh: Refresh, **overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-c", refresh.command],
        cwd=refresh.clone,
        env={**refresh.env, **overrides},
        capture_output=True,
        text=True,
    )


def _installed(refresh: Refresh) -> str:
    clone = refresh.clone
    return _run(
        clone,
        str(clone / ".venv/bin/python"),
        "-B",
        "-c",
        "import refresh_fixture; print(refresh_fixture.version)",
    )


def test_explicit_lock_refresh_replaces_the_actual_installed_dependency(refresh: Refresh) -> None:
    assert _installed(refresh) == "1.0"
    result = _execute(refresh)
    assert result.returncode == 0, result.stderr
    assert _installed(refresh) == "2.0"
    clone = refresh.clone
    assert _run(clone, "git", "status", "--porcelain") == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"EXPECTED_NEW_LOCK_SHA256": ""},
        {"EXPECTED_NEW_LOCK_SHA256": "not-a-digest"},
        {"EXPECTED_NEW_LOCK_SHA256": "b" * 64},
        {"QUALIFIED_SOURCE_REVISION": ""},
        {"QUALIFIED_SOURCE_REVISION": "b" * 40},
        {"QUALIFIED_LOCK_SHA256": "b" * 64},
        {"QUALIFIED_LOCK_SHA256": ""},
    ],
)
def test_identity_mismatch_stops_before_dependency_replacement(
    refresh: Refresh, overrides: dict[str, str]
) -> None:
    assert _execute(refresh, **overrides).returncode != 0
    assert _installed(refresh) == "1.0"


def test_default_unchanged_lock_refresh_still_runs_offline(refresh: Refresh) -> None:
    env = refresh.env
    for name in ("pyproject.toml", "uv.lock"):
        original = _run(
            refresh.origin, "git", "show", env["QUALIFIED_SOURCE_REVISION"] + ":python/" + name
        )
        (refresh.origin / name).write_text(original + "\n")
    (refresh.origin / "README.md").write_text("New source; unchanged dependency lock.\n")
    _run(refresh.origin, "git", "add", ".")
    _run(refresh.origin, "git", "commit", "-m", "source-only")
    target = _run(refresh.origin, "git", "rev-parse", "HEAD")
    result = _execute(
        refresh,
        EXPECTED_NEW_LOCK_SHA256="",
        QUALIFIED_SOURCE_REVISION="",
        QUALIFIED_LOCK_SHA256="",
        STPD_SOURCE_REVISION=target,
    )
    assert result.returncode == 0, result.stderr
    assert _installed(refresh) == "1.0"
    assert "--offline" in refresh.command


def test_explicit_digest_does_not_authorize_an_outdated_lock(refresh: Refresh) -> None:
    origin = refresh.origin
    pyproject = origin / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('version = "0.1.0"', 'version = "0.2.0"'))
    _run(origin, "git", "commit", "-am", "outdated-lock")
    target = _run(origin, "git", "rev-parse", "HEAD")
    result = _execute(refresh, STPD_SOURCE_REVISION=target)
    assert result.returncode != 0
    assert _installed(refresh) == "1.0"


def test_dirty_parent_does_not_admit_the_new_source(refresh: Refresh) -> None:
    clone = refresh.clone
    (clone / "untracked").write_text("unreviewed")
    assert _execute(refresh).returncode != 0
    assert _installed(refresh) == "1.0"


def test_fixture_refresh_does_not_modify_callers_environment(tmp_path):
    foreign = tmp_path / "caller-environment"
    foreign.mkdir()
    marker = foreign / "keep.txt"
    marker.write_bytes(b"caller-owned environment")
    inherited = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(foreign),
                 "VIRTUAL_ENV": str(foreign)}
    fixture = _make_refresh(tmp_path, "Perfect", inherited)
    assert _installed(fixture) == "1.0"
    result = _execute(fixture)
    assert result.returncode == 0, result.stderr
    assert _installed(fixture) == "2.0"
    assert list(foreign.iterdir()) == [marker]
    assert marker.read_bytes() == b"caller-owned environment"
    assert inherited["UV_PROJECT_ENVIRONMENT"] == str(foreign)
