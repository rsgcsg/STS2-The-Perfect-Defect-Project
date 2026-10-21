"""Refresh one immutable Modal image from an exact, clean project Git bundle.

This is an image build only: no function deployment, credentials or compute calls.
Run from python/ in the exact candidate checkout with the locked Modal SDK.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def refresh_checkout(
    parent_source: str, parent_lock: str, source: str, lock: str, bundle_path: str,
) -> None:
    # Keep the serialized builder independent of the controller's STPD installation.
    import hashlib
    import json
    import os
    import subprocess
    from pathlib import Path

    root = Path("/opt/stpd/python")

    def run(command: list[str], *, timeout: int = 30) -> str:
        result = subprocess.run(
            command, cwd=root, check=True, text=True, capture_output=True, timeout=timeout,
        )
        return result.stdout.strip()

    if os.environ.get("STPD_IMAGE_PROFILE") != "worker":
        raise ValueError("qualified_worker_parent_required")
    if run(["git", "rev-parse", "HEAD"]) != parent_source:
        raise ValueError("parent_source_mismatch")
    if run(["git", "status", "--porcelain"]):
        raise ValueError("clean_parent_required")
    if hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest() != parent_lock:
        raise ValueError("parent_lock_mismatch")
    run(["git", "bundle", "verify", bundle_path])
    run(["git", "fetch", bundle_path, "HEAD"])
    run(["git", "checkout", "--detach", source])
    if hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest() != lock:
        raise ValueError("new_lock_mismatch")
    # Existing locked CUDA packages stay in their dependency layer. A changed pin
    # still goes through the ordinary full locked synchronization, never --no-deps.
    run(
        ["uv", "sync", "--locked", "--all-extras", "--default-index", "https://pypi.org/simple"],
        timeout=720,
    )
    run(["uv", "pip", "check", "--python", ".venv/bin/python"], timeout=60)
    identity = run([
        ".venv/bin/python", "-c",
        "import json; from pathlib import Path; from spireagent.source import source_identity; "
        "print(json.dumps(source_identity(Path.cwd()).to_dict(), sort_keys=True))",
    ])
    observed = json.loads(identity)
    if observed["source_revision"] != source or observed["uv_lock_sha256"] != lock:
        raise ValueError("built_source_identity_mismatch")
    print(json.dumps({"schema": "stpd/modal-image-source-check-1", "producer": observed}))


def main() -> int:
    import importlib.metadata
    import sys

    import modal

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from spireagent.source import source_identity
    from stpd.cloud_jobs.modal import MODAL_SDK_VERSION

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-image", required=True)
    parser.add_argument("--parent-source", required=True)
    parser.add_argument("--parent-lock", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if importlib.metadata.version("modal") != MODAL_SDK_VERSION:
        raise ValueError("locked_modal_sdk_required")
    if not re.fullmatch(r"im-[A-Za-z0-9]{22}", args.parent_image):
        raise ValueError("exact_native_parent_image_required")
    for value, size in ((args.source, 40), (args.parent_source, 40), (args.parent_lock, 64)):
        if not re.fullmatch("[0-9a-f]{" + str(size) + "}", value):
            raise ValueError("exact_source_and_lock_required")
    if args.output.exists():
        raise ValueError("fresh_output_required_preserve_prior_build")
    producer = source_identity(root)
    if producer.source_revision != args.source:
        raise ValueError("current_source_mismatch")
    subprocess.run(["git", "bundle", "verify", str(args.bundle)], cwd=root,
                   check=True, capture_output=True, timeout=30)
    heads = subprocess.check_output(["git", "bundle", "list-heads", str(args.bundle)],
                                    cwd=root, text=True, timeout=30)
    if args.source not in [line.split()[0] for line in heads.splitlines()]:
        raise ValueError("exact_source_bundle_required")
    bundle_sha = hashlib.sha256(args.bundle.read_bytes()).hexdigest()
    receipt = {
        "schema": "stpd/modal-image-build-1", "state": "building",
        "producer": producer.to_dict(), "parent_image_id": args.parent_image,
        "parent_source": args.parent_source, "parent_lock": args.parent_lock,
        "bundle_sha256": bundle_sha,
        "recipe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "resources": {"cpu": 2, "memory_mib": 8192, "gpu": None, "timeout_seconds": 900},
        "image_identity_kind": "modal_native_immutable_id", "oci_digest": None,
        "environment": args.environment, "compute_calls": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        args.output.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")

    save()
    app = modal.App("stpd-image-build-" + args.source[:12])
    image = (
        modal.Image.from_id(args.parent_image)
        .add_local_file(args.bundle, "/tmp/stpd-candidate.bundle", copy=True)
        .run_function(
            refresh_checkout,
            args=(args.parent_source, args.parent_lock, args.source,
                  producer.uv_lock_sha256, "/tmp/stpd-candidate.bundle"),
            cpu=2, memory=8192, timeout=900,
        )
    )
    try:
        with modal.enable_output(), app.run(environment_name=args.environment):
            receipt["app_id"] = app.app_id
            save()
            image.build(app)
            receipt.update(state="built", image_id=image.object_id)
            save()
    except Exception as error:
        receipt.update(state="build_failed_or_unknown", error_type=type(error).__name__)
        save()
        raise
    print(json.dumps({"state": receipt["state"], "image_id": receipt["image_id"],
                      "receipt": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
