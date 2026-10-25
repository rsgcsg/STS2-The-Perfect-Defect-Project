"""Exact checkout compute entrypoint, shared by local processes and cloud wrappers."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes
from spireagent.source import source_identity
from spireagent.storage.config import open_store

from .contracts import ComputeRequest, load_feature_job
from .execution import FeatureBackend, dispatch


def execute_request(value: object, *, store_location: str = "s3",
                    wall_seconds: int | None = None, ready_file: Path | None = None) -> dict:
    request = ComputeRequest.decode(value)
    runtime = source_identity(Path(__file__).resolve().parents[2])
    if runtime != request.producer:
        raise BoundaryError("compute", "runtime_source_lock_mismatch")
    store = open_store(store_location)
    backend: FeatureBackend | None = None
    if request.kind == "features":
        _, spec = load_feature_job(store, request.input_id, runtime)
        qwen = spec.qwen.value()
        if qwen.get("model_id") == "stpd/deterministic-fake-qwen":
            from ..qwen.fake_backend import DeterministicFakeQwenBackend

            backend = DeterministicFakeQwenBackend(spec.hidden_size)
        else:
            from ..qwen.real_backend import RealQwenBackend

            snapshot = os.environ.get("STPD_QWEN_SNAPSHOT")
            if not snapshot:
                raise BoundaryError("compute", "qualified_qwen_snapshot_required")
            control = qwen.get("control")
            if control not in {"pretrained", "random"}:
                raise BoundaryError("compute", "unsupported_qwen_control")
            backend = RealQwenBackend(
                Path(snapshot), control=control, device=qwen["device"],
                random_seed=qwen.get("random_seed"),
            )
    def ready() -> None:
        if ready_file is None:
            return
        # Only the fixed SSH supervisor supplies this private local file. It is
        # an execution-phase observation, not a training/use or resume authority.
        raw = json_bytes({"request_id": request.request_id, "attempt_id": request.attempt_id,
                          "monotonic": time.monotonic(), "unix": time.time()})
        temporary = ready_file.with_suffix(".pending")
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(ready_file)

    return dispatch(store, request, runtime, backend=backend, wall_seconds=wall_seconds,
                    training_ready=ready if ready_file is not None else None).to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Frozen JSON request file")
    parser.add_argument("--store", default="s3")
    parser.add_argument("--wall-seconds", type=int,
                        help="provider hard timeout; P1 pauses 300 seconds before it")
    parser.add_argument("--ready-file", type=Path,
                        help="fixed supervisor's private training-start observation")
    args = parser.parse_args(argv)
    try:
        raw = Path(args.request).read_bytes()
        if len(raw) > 64 * 1024:
            raise BoundaryError("compute", "request_size_limit")
        receipt = execute_request(decode_json(raw), store_location=args.store,
                                  wall_seconds=args.wall_seconds,
                                  **({"ready_file": args.ready_file}
                                     if args.ready_file is not None else {}))
        print(json_bytes(receipt).decode(), end="")
        return 0
    except Exception as error:
        # Exceptions from SDKs may contain signed URLs; never echo arbitrary messages.
        code = error.code if isinstance(error, BoundaryError) else type(error).__name__
        print(json_bytes({"schema": "stpd/compute-error-v1", "code": code}).decode(), end="")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
