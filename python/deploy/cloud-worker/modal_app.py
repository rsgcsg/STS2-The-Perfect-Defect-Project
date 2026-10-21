"""Deploy with: uv run --locked --extra cloud modal deploy deploy/cloud-worker/modal_app.py.

STPD_MODAL_TARGET names a local JSON ModalTarget produced for the exact trusted image.
Only this deployment definition is sent from the local checkout; the remote computation
executes the fixed checkout in that image. Credentials are Modal Secrets, never JSON input.
"""

from __future__ import annotations

import importlib
import json
import os
import re
from pathlib import Path

from spireagent.json_boundary import BoundaryError, decode_json
from spireagent.source import source_identity
from stpd.cloud_jobs.modal import ModalTarget

modal = importlib.import_module("modal")
target = ModalTarget.decode(decode_json(Path(os.environ["STPD_MODAL_TARGET"]).read_bytes()))
if source_identity(Path(__file__).resolve().parents[2]) != target.producer:
    raise BoundaryError("modal_deploy", "wrapper_source_lock_mismatch")
deployed_target_id = target.target_id
app = modal.App(target.app_name)
image = (
    modal.Image.from_id(target.image)
    if re.fullmatch(r"im-[A-Za-z0-9]{22}", target.image)
    else modal.Image.from_registry(target.image, add_python="3.11")
)
volumes = {}
if target.qwen_volume_name:
    volumes["/qwen"] = modal.Volume.from_name(target.qwen_volume_name, create_if_missing=False)
# Persist no credentials in target or source. Secret contains only scoped store
# environment settings; a Qwen volume, when used, supplies STPD_QWEN_SNAPSHOT=/qwen/....
secrets = [modal.Secret.from_name(target.storage_secret_name)]


@app.function(
    image=image,
    gpu=None if target.gpu == "none" else target.gpu,
    timeout=target.timeout_seconds,
    cpu=(target.cpu_cores, target.cpu_cores),
    memory=(target.memory_mib, target.memory_mib),
    max_containers=1,
    min_containers=0,
    retries=0,
    secrets=secrets,
    volumes=volumes,
    serialized=True,
)
def compute(request: dict, target_id: str) -> dict:
    import subprocess
    import tempfile

    if os.environ.get("STPD_IMAGE_PROFILE") != "worker":
        raise ValueError("qualified_worker_image_required")
    if target_id != deployed_target_id:
        raise ValueError("deployed_target_mismatch")
    with tempfile.TemporaryDirectory(prefix="stpd-request-") as directory:
        request_path = Path(directory) / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        result = subprocess.run(
            [
                "/opt/stpd/python/.venv/bin/python",
                "-m",
                "stpd.cloud_jobs",
                "--request",
                str(request_path),
            ],
            cwd="/opt/stpd/python",
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode:
        # Do not forward cloud/sdk stderr, which can contain credential-bearing URLs.
        raise RuntimeError("compute_failed_inspect_immutable_run_events")
    return {"target_id": target_id, "receipt": json.loads(result.stdout)}
