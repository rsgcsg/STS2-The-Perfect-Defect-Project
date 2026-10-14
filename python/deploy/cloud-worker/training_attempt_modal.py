"""One exact prepared-attempt Modal transport App; importing does not submit work.

No canonical storage Secret, volume, journal path or selection authority is
injected. Numerical worker runs in the image's locked checkout/venv.
"""

from __future__ import annotations

import os
import resource
import subprocess
import tempfile
from pathlib import Path

import modal

from spireagent.source import source_identity
from spireagent.training_execution_contracts import PreparedTrainingAttempt

_attempt = PreparedTrainingAttempt.from_bytes(
    os.environ["SPIREAGENT_PREPARED_TRAINING_ATTEMPT"].encode(),
)
if source_identity(Path(__file__).resolve().parents[2]) != _attempt.producer:
    raise ValueError("deployment_source_lock_mismatch")

app = modal.App(_attempt.app_name)
image = modal.Image.from_id(_attempt.placement.image_object_id)


@app.function(
    image=image,
    name="training_attempt",
    gpu=None if _attempt.placement.gpu == "none" else _attempt.placement.gpu,
    cpu=(_attempt.limits.cpu_millicores / 1000, _attempt.limits.cpu_millicores / 1000),
    memory=(_attempt.limits.memory_mib, _attempt.limits.memory_mib),
    timeout=_attempt.limits.wall_seconds,
    startup_timeout=_attempt.limits.startup_seconds,
    max_containers=1,
    min_containers=0,
    max_inputs=1,
    single_use_containers=True,
    scaledown_window=30,
    retries=0,
    secrets=[],
    volumes={},
    serialized=True,
    include_source=False,
)
def training_attempt(prepared: bytes, request: bytes, projection: bytes) -> bytes:
    selected = PreparedTrainingAttempt.from_bytes(prepared)
    if selected != _attempt or os.environ.get("STPD_IMAGE_PROFILE") != "worker":
        raise ValueError("prepared_attempt_or_worker_image_mismatch")
    selected.bind_payloads(request, projection)
    with tempfile.TemporaryDirectory(prefix="training-projection-") as directory:
        root = Path(directory)
        (root / "attempt.json").write_bytes(prepared)
        (root / "request.json").write_bytes(request)
        (root / "projection.bytes").write_bytes(projection)
        result_path = root / "candidate.bytes"
        worker_env = dict(os.environ)
        if selected.placement.device == "cuda:0":
            cublas = selected.runtime.value().get("cublas_workspace_config")
            if cublas != ":4096:8":
                raise ValueError("qualified_cublas_workspace_required")
            # Set before the isolated numerical process imports Torch.
            worker_env["CUBLAS_WORKSPACE_CONFIG"] = cublas

        def limits() -> None:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(
                resource.RLIMIT_FSIZE,
                (selected.limits.max_candidate_bytes, selected.limits.max_candidate_bytes),
            )

        completed = subprocess.run(
            [
                "/opt/stpd/python/.venv/bin/python",
                "-m",
                "spireagent.training_transport_worker",
                "--attempt",
                str(root / "attempt.json"),
                "--request",
                str(root / "request.json"),
                "--projection",
                str(root / "projection.bytes"),
                "--result",
                str(result_path),
            ],
            cwd="/opt/stpd/python",
            env=worker_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=selected.limits.wall_seconds,
            preexec_fn=limits,
            check=False,
        )
        if completed.returncode or not result_path.is_file() or result_path.is_symlink():
            raise RuntimeError("bounded_worker_failed_no_candidate")
        if not 1 <= result_path.stat().st_size <= selected.limits.max_candidate_bytes:
            raise RuntimeError("candidate_size_limit")
        result = result_path.read_bytes()
        if len(result) > selected.limits.max_candidate_bytes:
            raise RuntimeError("candidate_size_changed")
        return result
