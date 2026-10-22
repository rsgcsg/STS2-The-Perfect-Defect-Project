"""Fixed remote SSH process transport; no task/result-selection authority.

The supervisor owns only its Popen child process group. Transport requests never
contain credentials or shell commands. Durable worker receipts remain unchanged.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from spireagent.compute import ComputeTermination
from spireagent.json_boundary import BoundaryError, decode_json, json_bytes, object_fields
from spireagent.source import source_identity
from spireagent.ssh_compute import SshCall, SshTarget
from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    with temporary.open("xb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def runtime_identity(gpu: str) -> dict[str, str]:
    import platform

    import torch

    result = {"python": platform.python_version(), "torch": str(torch.__version__),
              "cuda": "none", "gpu_name": "none", "gpu_uuid": "none", "driver": "none"}
    if gpu != "none":
        observed = subprocess.run(["nvidia-smi", "--query-gpu=name,uuid,driver_version",
                                   "--format=csv,noheader"], check=True, capture_output=True,
                                  text=True, timeout=10).stdout.strip().splitlines()
        if len(observed) != 1 or not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise BoundaryError("ssh", "single_gpu_required")
        name, uuid, driver = [value.strip() for value in observed[0].split(",")]
        if gpu not in name:
            raise BoundaryError("ssh", "gpu_identity_mismatch")
        result.update(cuda=str(torch.version.cuda), gpu_name=name, gpu_uuid=uuid, driver=driver)
    return result


def private_environment(path: Path) -> dict[str, str]:
    """Read the existing dotenv mechanism privately, without echo or request copy."""
    from spireagent.workbench.service_process import cloud_environment

    environment = dict(os.environ)
    # The original bounded private-file parser admits only cloud storage keys.
    # Runtime variables (PYTHONPATH/CUDA_VISIBLE_DEVICES/LD_LIBRARY_PATH) cannot
    # change between this process's target check and its worker's environment.
    environment.update(cloud_environment(path))
    return environment


def verify_target(target: SshTarget) -> None:
    root = Path(__file__).resolve().parents[1]
    if root != Path(target.checkout).resolve() / "python":
        raise BoundaryError("ssh", "executing_checkout_mismatch")
    if Path(sys.executable).absolute() != Path(target.interpreter).absolute():
        raise BoundaryError("ssh", "executing_interpreter_mismatch")
    if source_identity(root) != target.producer:
        raise BoundaryError("ssh", "runtime_source_lock_mismatch")
    if runtime_identity(target.gpu) != target.runtime.value():
        raise BoundaryError("ssh", "runtime_execution_identity_mismatch")


def envelope(handle: SshCall, state: str, *, receipt: object = None,
             error: str | None = None, termination: object = None) -> dict[str, Any]:
    return {"target_id": handle.target_id, "call_id": handle.call_id, "state": state,
            "receipt": receipt, "error": error, "termination": termination}


def _original(directory: Path, target: SshTarget, handle: SshCall) -> None:
    saved = decode_json((directory / "invocation.json").read_bytes())
    if saved != {"target": target.to_dict(), "handle": handle.to_dict()}:
        raise BoundaryError("ssh", "original_invocation_mismatch")


def exchange(value: object) -> dict[str, Any]:
    import fcntl

    obj = object_fields(value, {"action", "target", "handle"}, "ssh.command")
    target, handle = SshTarget.decode(obj["target"]), SshCall.decode(obj["handle"])
    if handle.target_id != target.target_id or handle.request.producer != target.producer:
        raise BoundaryError("ssh", "foreign_target")
    if obj["action"] not in {"submit", "poll", "cancel"}:
        raise BoundaryError("ssh", "unsupported_action")
    directory = Path(target.state_directory) / handle.target_id / handle.call_id
    if obj["action"] == "submit":
        verify_target(target)
        # Validate permissions before accepting execution; never serialize secrets.
        private_environment(Path(target.private_env))
        directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.mkdir(mode=0o700)  # Existing original invocation is never launched again.
        atomic_json(directory / "invocation.json", {"target": target.to_dict(),
                                                    "handle": handle.to_dict()})
        atomic_json(directory / "request.json", handle.request.to_dict())
        with open(os.devnull, "wb") as sink:
            subprocess.Popen([target.interpreter, "-m", "spireagent.ssh_compute_agent",
                              "--supervise", str(directory)], stdin=subprocess.DEVNULL,
                             stdout=sink, stderr=sink, start_new_session=True,
                             cwd=Path(target.checkout) / "python")
        return envelope(handle, "accepted")
    _original(directory, target, handle)
    result = directory / "result.json"
    if result.exists():
        return dict(decode_json(result.read_bytes()))
    if obj["action"] == "cancel":
        (directory / "cancel").touch(mode=0o600, exist_ok=True)
        return envelope(handle, "cancel_requested")
    # A fresh OS-owned lock proves the original supervisor still exists. An
    # absent/dead process or launch gap is unknown, never guessed pending/paused.
    with (directory / "running.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return envelope(handle, "running")
    if result.exists():
        return dict(decode_json(result.read_bytes()))
    return envelope(handle, "unknown", error="result_unavailable")


def terminate_group(child: subprocess.Popen[Any]) -> bool:
    # Only the child created in this supervisor can be signalled. No persisted
    # PID is ever trusted for kill, so PID reuse cannot target another user's job.
    if child.returncode is None:
        # Do not reap/poll the leader between signals: even an exited zombie
        # reserves its PID, preventing process-group ID reuse during cleanup.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(child.pid, signal.SIGTERM)
        time.sleep(0.2)
        # Darwin can report EPERM for an already-exited unreaped group leader.
        # It is not exit evidence: wait + absence observation below still decide.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(child.pid, signal.SIGKILL)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
    # A naturally reaped leader no longer reserves its PID. Never signal that
    # number again: an unexpected surviving group stays unknown for its owner.
    # A successful signal or leader exit alone does not prove the group ended.
    # Never signal a persisted PID during recovery; this is the just-owned child.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.02)
    return False


def supervise(directory: Path) -> None:
    import fcntl

    value = decode_json((directory / "invocation.json").read_bytes())
    target = SshTarget.decode(value["target"])
    handle = SshCall.decode(value["handle"])
    with (directory / "running.lock").open("a+b") as lock:
        # submit's unique mkdir admits one supervisor. An observer can briefly
        # hold this lock before startup; wait instead of abandoning that invocation.
        fcntl.flock(lock, fcntl.LOCK_EX)
        child: subprocess.Popen[Any] | None = None
        result = envelope(handle, "failed", error="execution_failed")
        try:
            if (directory != Path(target.state_directory) / handle.target_id / handle.call_id
                    or handle.target_id != target.target_id
                    or handle.request.producer != target.producer
                    or ComputeRequest.decode(decode_json((directory / "request.json").read_bytes()))
                    != handle.request):
                raise BoundaryError("ssh", "original_invocation_mismatch")
            verify_target(target)
            environment = private_environment(Path(target.private_env))
            with (directory / "worker-output.json").open("wb") as output:
                os.chmod(directory / "worker-output.json", 0o600)
                child = subprocess.Popen([target.interpreter, "-m", "stpd.cloud_jobs",
                                          "--request", str(directory / "request.json"),
                                          "--store", "s3"], env=environment,
                                         cwd=Path(target.checkout) / "python",
                                         stdin=subprocess.DEVNULL, stdout=output,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
                deadline = time.monotonic() + target.timeout_seconds
                while child.poll() is None:
                    if (directory / "cancel").exists():
                        result = envelope(handle, "failed", error="execution_cancelled")
                        break
                    if time.monotonic() >= deadline:
                        result = envelope(handle, "failed", error="execution_timeout")
                        break
                    time.sleep(0.1)
                else:
                    if child.returncode == 0:
                        with (directory / "worker-output.json").open("rb") as stream:
                            raw = stream.read(65537)
                        if len(raw) > 65536:
                            raise BoundaryError("ssh", "receipt_size_limit")
                        receipt = ComputeReceipt.decode(decode_json(raw))
                        receipt.bind(handle.request)
                        result = envelope(handle, "completed", receipt=receipt.to_dict())
        except Exception:
            # Error messages, SDK output and private env never enter transport receipts.
            result = envelope(handle, "failed", error="execution_failed")
        finally:
            stopped = child is None or terminate_group(child)
            if not stopped:
                result = envelope(handle, "unknown", error="result_unavailable")
            elif result["state"] == "failed":
                outcome = {"execution_cancelled": "cancelled", "execution_timeout": "timeout"}.get(
                    result["error"], "failed")
                result["termination"] = ComputeTermination(
                    handle.target_id, handle.request.request_id, handle.request.attempt_id,
                    handle.call_id, outcome, True).to_dict()
            atomic_json(directory / "result.json", result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--supervise", type=Path)
    args = parser.parse_args(argv)
    if args.supervise is not None:
        def interrupted(_signum: int, _frame: object) -> None:
            raise InterruptedError()

        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        supervise(args.supervise)
        return 0
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise BoundaryError("ssh", "request_size_limit")
        result = exchange(decode_json(raw))
        sys.stdout.buffer.write(json_bytes(result))
        return 0
    except Exception:
        return 1  # SSH provider records ambiguity without exposing exception text.


if __name__ == "__main__":
    raise SystemExit(main())
