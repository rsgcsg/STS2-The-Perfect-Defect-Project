"""Process resources owned by the long-lived Workbench, not its installer phase."""

from __future__ import annotations

import importlib
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError

CLOUD_ENVIRONMENT_KEYS = frozenset({
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE",
    "AWS_REGION", "AWS_DEFAULT_REGION",
    "STPD_S3_BUCKET", "STPD_S3_ENDPOINT", "STPD_S3_PREFIX", "STPD_S3_REGION",
})


def cloud_environment(path: Path | None) -> dict[str, str]:
    """Read an explicitly selected private SDK environment, never discover a .env."""
    if path is None:
        return {}
    try:
        info = path.lstat()
        if (not path.is_absolute() or not stat.S_ISREG(info.st_mode) or info.st_size > 32768
                or os.name != "nt" and (
                    info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077)):
            raise BoundaryError("project", "cloud_environment_private_file_required")
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise BoundaryError("project", "cloud_environment_private_file_required") from None
    from dotenv.parser import parse_stream

    values = {}
    for entry in parse_stream(StringIO(raw)):
        if entry.error:
            raise BoundaryError("project", "cloud_environment_invalid")
        if entry.key in CLOUD_ENVIRONMENT_KEYS:
            if entry.value is None:
                raise BoundaryError("project", "cloud_environment_invalid")
            values[entry.key] = entry.value
    return values


def require_service_file_limit() -> dict[str, Any]:
    """Reject inherited build limits; never try to raise an inherited hard limit.

    Dataset verification writes data-sized temporary SQLite files. Its operation
    budgets belong to the verification owner. A short installer file cap is not
    a supported resource policy for this service or its later child processes.
    """
    if os.name == "nt":
        return {"file_size_limit": "not_available_on_platform"}
    resource = importlib.import_module("resource")
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    if soft != resource.RLIM_INFINITY or hard != resource.RLIM_INFINITY:
        raise BoundaryError(
            "project", "workbench_file_limit_incompatible",
            "launch the service outside the installer file-size limit",
        )
    return {"file_size_limit": "unlimited"}


def service_environment(
    state_dir: Path, *, cloud_environment_file: Path | None = None,
) -> dict[str, str]:
    """Use private profile-owned scratch, retained for the service's lifetime."""
    require_service_file_limit()
    directory = state_dir / "workbench-tmp"
    if directory.is_symlink():
        raise BoundaryError("project", "workbench_temp_directory_invalid")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = directory.stat()
    geteuid = getattr(os, "geteuid", None)
    if (not stat.S_ISDIR(metadata.st_mode)
            or os.name != "nt" and (
                not callable(geteuid) or metadata.st_uid != geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            )):
        raise BoundaryError("project", "workbench_temp_directory_invalid")
    # Resolve once so children share the same physical profile-owned location.
    environment = dict(os.environ)
    for name, value in cloud_environment(cloud_environment_file).items():
        environment.setdefault(name, value)
    environment.update({name: str(directory.resolve()) for name in ("TMPDIR", "TMP", "TEMP")})
    return environment


@contextmanager
def service_process(
    state_dir: Path, *, cloud_environment_file: Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Direct serve and launcher-spawned serve obey the same process contract."""
    environment = service_environment(state_dir, cloud_environment_file=cloud_environment_file)
    names = ("TMPDIR", "TMP", "TEMP", *(
        sorted(CLOUD_ENVIRONMENT_KEYS) if cloud_environment_file is not None else ()))
    previous = {name: os.environ.get(name) for name in names}
    previous_temp = tempfile.tempdir
    try:
        for name in names:
            if name in environment:
                os.environ[name] = environment[name]
            else:
                os.environ.pop(name, None)
        # tempfile may have cached the installer's directory during imports.
        tempfile.tempdir = environment["TMPDIR"]
        yield {**require_service_file_limit(), "temporary_directory": tempfile.gettempdir()}
    finally:
        tempfile.tempdir = previous_temp
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
