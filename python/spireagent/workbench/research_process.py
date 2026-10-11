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
                  timeout_seconds: float | None = None,
                  on_stdout_line: Callable[[bytes], None] | None = None,
                  on_exited: Callable[[int, bool, float], None] | None = None,
                  stop_requested: Callable[[], bool] | None = None,
                  max_stdout_line_bytes: int = 16384,
                  max_stdout_channel_bytes: int = 32 * 1024 * 1024) -> tuple[int, bytes]:
    """Drain both pipes and retain bounded private diagnostics plus machine stdout."""
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("positive_child_timeout_required")
    if max_stdout_line_bytes < 1 or max_stdout_channel_bytes < max_stdout_line_bytes:
        raise ValueError("positive_bounded_channel_required")
    started_clock = time.monotonic()
    log_fd = os.open(log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    captured = bytearray()
    with os.fdopen(log_fd, "wb") as log:
        with subprocess.Popen(command, cwd=ROOT, env=environment,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as child:
            callback_errors: list[Exception] = []
            if on_started is not None:
                try:
                    on_started()
                except Exception as error:
                    callback_errors.append(error)
            assert child.stdout is not None and child.stderr is not None
            write_lock = threading.Lock()
            remaining = [128 * 1024]
            read_errors: list[OSError] = []
            log_errors: list[OSError] = []

            def drain(stream: Any, *, machine: bool) -> None:
                pending = bytearray()
                channel_bytes = 0
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
                        if machine and on_stdout_line is not None and not callback_errors:
                            channel_bytes += len(chunk)
                            pending.extend(chunk)
                            if channel_bytes > max_stdout_channel_bytes:
                                callback_errors.append(BoundaryError(
                                    "research_process", "private_child_channel_budget"))
                                pending.clear()
                                continue
                            while b"\n" in pending and not callback_errors:
                                line, _, rest = pending.partition(b"\n")
                                pending = bytearray(rest)
                                try:
                                    if not line or len(line) > max_stdout_line_bytes:
                                        raise BoundaryError("research_process",
                                                            "private_child_line_budget")
                                    on_stdout_line(bytes(line))
                                except Exception as error:
                                    callback_errors.append(error)
                                    pending.clear()
                            if len(pending) > max_stdout_line_bytes:
                                callback_errors.append(BoundaryError(
                                    "research_process", "private_child_line_budget"))
                                pending.clear()
                    if machine and on_stdout_line is not None and pending and not callback_errors:
                        callback_errors.append(BoundaryError(
                            "research_process", "private_child_channel_truncated"))
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
            forced = False
            control_stopped = False
            while True:
                try:
                    if callback_errors or read_errors:
                        forced = True
                        child.kill()
                        exit_code = child.wait()
                        break
                    if stop_requested is not None:
                        try:
                            stop = stop_requested()
                        except Exception as error:
                            callback_errors.append(error)
                            continue
                        if stop:
                            control_stopped = True
                            forced = True
                            child.kill()
                            exit_code = child.wait()
                            break
                    remaining_time = (deadline - time.monotonic()
                                      if deadline is not None else None)
                    if remaining_time is not None and remaining_time <= 0:
                        timed_out = True
                        forced = True
                        child.kill()
                        exit_code = child.wait()
                        break
                    exit_code = child.wait(timeout=min(0.25, remaining_time)
                                           if remaining_time is not None else 0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
            stdout_reader.join()
            stderr_reader.join()
            if on_exited is not None:
                try:
                    on_exited(exit_code, forced, time.monotonic()-started_clock)
                except Exception as error:
                    callback_errors.append(error)
            if callback_errors:
                raise callback_errors[0]
            if read_errors:
                raise read_errors[0]
            if log_errors:
                raise log_errors[0]
            if timed_out:
                raise BoundaryError("research_process", "private_child_timeout")
            if control_stopped:
                raise BoundaryError("research_process", "private_child_stop_requested")
        log.flush()
        os.fsync(log.fileno())
    return exit_code, bytes(captured)
