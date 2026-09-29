"""One bounded private research child lifecycle for local operations."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ROOT


def private_child(command: list[str], log_path: Path,
                  environment: dict[str, str], *,
                  on_started: Callable[[], None] | None = None,
                  timeout_seconds: float | None = None) -> tuple[int, bytes]:
    """Drain both pipes and retain bounded private diagnostics plus machine stdout."""
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("positive_child_timeout_required")
    log_fd = os.open(log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    captured = bytearray()
    with os.fdopen(log_fd, "wb") as log:
        with subprocess.Popen(command, cwd=ROOT, env=environment,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as child:
            if on_started is not None:
                on_started()
            assert child.stdout is not None and child.stderr is not None
            write_lock = threading.Lock()
            remaining = [128 * 1024]
            read_errors: list[OSError] = []
            log_errors: list[OSError] = []

            def drain(stream: Any, *, machine: bool) -> None:
                try:
                    while chunk := os.read(stream.fileno(), 4096):
                        with write_lock:
                            if machine and len(captured) < 8192:
                                captured.extend(chunk[:8192 - len(captured)])
                            if remaining[0] and not log_errors:
                                part = chunk[:remaining[0]]
                                try:
                                    log.write(part)
                                    remaining[0] -= len(part)
                                except OSError as error:
                                    # Keep draining both pipes until the child exits.
                                    log_errors.append(error)
                except OSError as error:
                    read_errors.append(error)

            stdout_reader = threading.Thread(target=drain, args=(child.stdout,),
                                             kwargs={"machine": True})
            stderr_reader = threading.Thread(target=drain, args=(child.stderr,),
                                             kwargs={"machine": False})
            stdout_reader.start()
            stderr_reader.start()
            deadline = (time.monotonic() + timeout_seconds
                        if timeout_seconds is not None else None)
            timed_out = False
            while True:
                try:
                    remaining_time = (deadline - time.monotonic()
                                      if deadline is not None else None)
                    if remaining_time is not None and remaining_time <= 0:
                        timed_out = True
                        child.kill()
                        exit_code = child.wait()
                        break
                    exit_code = child.wait(timeout=min(0.25, remaining_time)
                                           if remaining_time is not None else 0.25)
                    break
                except subprocess.TimeoutExpired:
                    if read_errors:
                        child.kill()
                        exit_code = child.wait()
                        break
            stdout_reader.join()
            stderr_reader.join()
            if read_errors:
                raise read_errors[0]
            if log_errors:
                raise log_errors[0]
            if timed_out:
                raise BoundaryError("research_process", "private_child_timeout")
        log.flush()
        os.fsync(log.fileno())
    return exit_code, bytes(captured)
