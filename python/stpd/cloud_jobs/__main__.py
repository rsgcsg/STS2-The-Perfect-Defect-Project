"""Exact checkout compute entrypoint, shared by local processes and cloud wrappers."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes
from spireagent.source import source_identity
from spireagent.storage.config import open_store

from .contracts import ComputeRequest, load_feature_job
from .execution import FeatureBackend, dispatch


def execute_request(value: object, *, store_location: str = "s3",
                    wall_seconds: int | None = None) -> dict:
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
    return dispatch(store, request, runtime, backend=backend, wall_seconds=wall_seconds).to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Frozen JSON request file")
    parser.add_argument("--store", default="s3")
    parser.add_argument("--wall-seconds", type=int,
                        help="provider hard timeout; P1 pauses 300 seconds before it")
    args = parser.parse_args(argv)
    try:
        raw = Path(args.request).read_bytes()
        if len(raw) > 64 * 1024:
            raise BoundaryError("compute", "request_size_limit")
        receipt = execute_request(decode_json(raw), store_location=args.store,
                                  wall_seconds=args.wall_seconds)
        print(json_bytes(receipt).decode(), end="")
        return 0
    except Exception as error:
        # Exceptions from SDKs may contain signed URLs; never echo arbitrary messages.
        code = error.code if isinstance(error, BoundaryError) else type(error).__name__
        print(json_bytes({"schema": "stpd/compute-error-v1", "code": code}).decode(), end="")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
