"""Closed isolated worker entry; no canonical Store, journal or completion owner.

The numerical domain handler consumes a finite immutable projection. Missing
capability is an explicit failure, never a fallback to another recipe/engine.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import time
from pathlib import Path

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.source import source_identity
from spireagent.training_execution_contracts import (
    TRAINING_WORKER_CAPABILITY,
    PreparedTrainingAttempt,
)
from spireagent.training_transport import decode_projection

CAPABILITY_MODULES = {
    TRAINING_WORKER_CAPABILITY: "stpd.workers.structured_remote_domain",
}


def installed_capabilities() -> tuple[str, ...]:
    """Discovery only: not image/device/runtime qualification or job permission."""
    return tuple(
        key
        for key, module in CAPABILITY_MODULES.items()
        if importlib.util.find_spec(module) is not None
    )


def execute_training_attempt(
    attempt: PreparedTrainingAttempt, request: bytes, projection: bytes
) -> bytes:
    started = time.monotonic()
    attempt.bind_payloads(request, projection)
    decode_projection(projection, attempt)
    root = Path(__file__).resolve().parents[1]
    if source_identity(root) != attempt.producer:
        raise BoundaryError("training_transport_worker", "executing_source_lock_mismatch")
    module_name = CAPABILITY_MODULES.get(attempt.worker_capability)
    if module_name is None or importlib.util.find_spec(module_name) is None:
        raise BoundaryError("training_transport_worker", "capability_unavailable")
    module = importlib.import_module(module_name)
    handler = getattr(module, "execute_structured_remote_request", None)
    if not callable(handler):
        raise BoundaryError("training_transport_worker", "capability_unavailable")
    # Handler owns numerical validation and bounded slice/checkpoint semantics.
    # No fabricated AttemptAuthority, original journal or real-time pause claim.
    result = handler(attempt, request, projection)
    if (
        type(result) is not bytes
        or not 1 <= len(result) <= attempt.limits.max_candidate_bytes
        or time.monotonic() - started > attempt.limits.wall_seconds
    ):
        raise BoundaryError("training_transport_worker", "candidate_or_wall_limit")
    return result


def _read(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= maximum:
        raise BoundaryError("training_transport_worker", "input_file_limit")
    raw = path.read_bytes()
    if len(raw) > maximum:
        raise BoundaryError("training_transport_worker", "input_file_changed")
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        attempt = PreparedTrainingAttempt.from_bytes(_read(args.attempt, 64 * 1024))
        result = execute_training_attempt(
            attempt,
            _read(args.request, attempt.limits.max_request_bytes),
            _read(args.projection, attempt.limits.max_projection_bytes),
        )
        if args.result.exists() or args.result.is_symlink():
            raise BoundaryError("training_transport_worker", "fresh_result_required")
        with args.result.open("xb") as stream:
            stream.write(result)
        return 0
    except Exception as error:
        code = error.code if isinstance(error, BoundaryError) else "worker_failed"
        # Never echo arbitrary exception text, SDK errors or credential URLs.
        print(
            json_bytes({"schema": "spireagent/training-transport-error-v1", "code": code}).decode(),
            end="",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
