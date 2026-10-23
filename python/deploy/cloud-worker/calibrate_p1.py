"""One explicitly budgeted, isolated Modal P1 calibration; no formal job control.

The existing exact worker image/Secret/Store are reused. This engineering entry
does not retry, deploy a persistent function, select a Model or touch a live Run.
Keep the original App/call receipt and inspect it after any unknown transport.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def definition(app: Any, sdk: Any, image: str, *, timeout: int, cpu: int, memory: int,
               secret_name: str) -> Any:
    """Serialize only primitives; injected Python need not import STPD."""
    frozen_timeout = int(timeout)

    @app.function(
        image=sdk.Image.from_id(image), gpu="L40S", cpu=(cpu, cpu), memory=(memory, memory),
        timeout=timeout, startup_timeout=120, scaledown_window=2, retries=0,
        max_containers=1, min_containers=0, buffer_containers=0,
        single_use_containers=True, secrets=[sdk.Secret.from_name(secret_name)], serialized=True,
    )
    def calibrate(input_id: str, plan: dict, expected_source: str, expected_lock: str) -> dict:
        import json
        import os
        import subprocess
        import tempfile
        from pathlib import Path

        if os.environ.get("STPD_IMAGE_PROFILE") != "worker":
            raise ValueError("qualified_worker_parent_required")
        with tempfile.TemporaryDirectory(prefix="p1-TRAIN-calibration-") as folder:
            path = Path(folder)
            request = path / "plan.json"
            request.write_text(json.dumps(plan), encoding="utf-8")
            environment = dict(os.environ)
            environment["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
            result = subprocess.run(
                ["/opt/stpd/python/.venv/bin/python", "-m", "stpd.p1.calibration",
                 "--store", "s3", "--input", input_id, "--plan", str(request),
                 "--expected-source", expected_source, "--expected-lock", expected_lock,
                 "--trace", str(path / "trace.json")], cwd="/opt/stpd/python",
                env=environment, text=True, capture_output=True, timeout=frozen_timeout - 10,
            )
        # No SDK/worker stderr or arbitrary exception text crosses this boundary.
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError):
            return {"state": "failed", "error_code": "worker_output_unavailable",
                    "worker_returncode": result.returncode}
        if not isinstance(value, dict) or value.get("state") not in {"completed", "failed"}:
            return {"state": "failed", "error_code": "worker_output_invalid"}
        if value["state"] == "completed" and result.returncode:
            return {"state": "failed", "error_code": "worker_exit_result_mismatch"}
        return value

    return calibrate


def main() -> int:
    import importlib.metadata
    import sys
    import time

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    import modal

    from spireagent.json_boundary import digest
    from spireagent.source import source_identity
    from stpd.cloud_jobs.modal import MODAL_SDK_VERSION
    from stpd.p1.execution import MStdExecution

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--environment", default="spireagent-b")
    parser.add_argument("--secret", default="stpd-worker-storage")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--cpu", type=int, default=2)
    parser.add_argument("--memory-mib", type=int, default=16384)
    args = parser.parse_args()
    if importlib.metadata.version("modal") != MODAL_SDK_VERSION:
        raise ValueError("locked_Modal_SDK_required")
    producer = source_identity(root)
    digest(args.input, "calibration.input_id")
    if not re.fullmatch(r"im-[A-Za-z0-9]{22}", args.image):
        raise ValueError("exact_immutable_native_worker_image_required")
    if args.receipt.exists() or not (60 <= args.timeout <= 1800
                                   and 1 <= args.cpu <= 8 and 8192 <= args.memory_mib <= 32768):
        raise ValueError("fresh_receipt_and_bounded_resources_required")
    plan = json.loads(args.plan.read_bytes())
    if not isinstance(plan, dict) or set(plan) != {"variants", "profile"}:
        raise ValueError("exact_calibration_plan_required")
    if (not isinstance(plan["variants"], list) or not 1 <= len(plan["variants"]) <= 8
            or type(plan["profile"]) is not bool):
        raise ValueError("bounded_explicit_calibration_plan_required")
    for value in plan["variants"]:
        MStdExecution.decode(value)
    identity = {"producer": producer.to_dict(), "image": args.image,
                "input_id": args.input, "plan": plan, "cpu": args.cpu,
                "memory_mib": args.memory_mib, "gpu": "L40S", "timeout": args.timeout}
    run_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    app = modal.App("stpd-TRAIN-calibration-" + run_key[:40])
    function = definition(app, modal, args.image, timeout=args.timeout, cpu=args.cpu,
                          memory=args.memory_mib, secret_name=args.secret)
    receipt = {"schema": "stpd/Modal-P1-calibration-operation-1", "state": "offered",
               **identity, "startup_allowance_seconds": 120, "scaledown_seconds": 2,
               "automatic_retry": False, "formal_training_changed": False,
               "offered_at_unix": time.time()}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")

    save()
    try:
        with modal.enable_output(), app.run(environment_name=args.environment):
            receipt["app_id"] = app.app_id
            save()
            call = function.spawn(args.input, plan, producer.source_revision,
                                  producer.uv_lock_sha256)
            receipt.update(state="submitted", call_id=call.object_id)
            save()
            result = call.get(timeout=args.timeout + 180)
            receipt.update(state=result["state"], result=result, returned_at_unix=time.time())
            save()
    except Exception as error:
        receipt.update(state="failed_or_unknown", error_type=type(error).__name__,
                       observed_at_unix=time.time())
        save()
        print(json.dumps({"state": receipt["state"], "error_type": type(error).__name__,
                          "receipt": str(args.receipt)}))
        return 1
    print(json.dumps({"state": receipt["state"], "result": receipt["result"],
                      "receipt": str(args.receipt)}))
    return 0 if receipt["state"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
