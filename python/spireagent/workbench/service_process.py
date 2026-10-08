"""Process resources owned by the long-lived Workbench, not its installer phase."""

from __future__ import annotations

import importlib
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError


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


def service_environment(state_dir: Path) -> dict[str, str]:
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
    environment.update({name: str(directory.resolve()) for name in ("TMPDIR", "TMP", "TEMP")})
    return environment


@contextmanager
def service_process(state_dir: Path) -> Iterator[dict[str, Any]]:
    """Direct serve and launcher-spawned serve obey the same process contract."""
    environment = service_environment(state_dir)
    names = ("TMPDIR", "TMP", "TEMP")
    previous = {name: os.environ.get(name) for name in names}
    previous_temp = tempfile.tempdir
    try:
        for name in names:
            os.environ[name] = environment[name]
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
