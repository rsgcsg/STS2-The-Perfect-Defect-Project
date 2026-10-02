"""One-shot, synthetic-only CUDA smoke for the existing M0 engine.

This file is deliberately separate from ``modal_app.py``: it has no S3 mount,
storage secret, dataset input, or owner/admitter callback.  The default CLI path
only prints a validated plan.  A live Modal call requires ``--execute``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPOSITORY = "rsgcsg/STS2-The-Perfect-Defect-Project"
IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_FUNCTION_SECONDS = 30
MAX_STARTUP_SECONDS = 30
MAX_SCALEDOWN_SECONDS = 30
MAX_BILLABLE_SECONDS = (
    MAX_FUNCTION_SECONDS + MAX_STARTUP_SECONDS + MAX_SCALEDOWN_SECONDS
)


class PlanError(ValueError):
    """The requested smoke cannot be proven to stay inside its fixed scope."""


@dataclass(frozen=True)
class SmokePlan:
    run_id: str
    app_name: str
    environment: str
    image: str
    repository: str
    source_revision: str
    uv_lock_sha256: str
    harness_sha256: str
    gpu: str = "L4"
    cpu: float = 2.0
    memory_mb: int = 4096
    min_containers: int = 0
    max_containers: int = 1
    retries: int = 0
    timeout_seconds: int = MAX_FUNCTION_SECONDS
    startup_timeout_seconds: int = MAX_STARTUP_SECONDS
    scaledown_window_seconds: int = MAX_SCALEDOWN_SECONDS
    max_billable_seconds: int = MAX_BILLABLE_SECONDS
    secrets: tuple[str, ...] = ()
    volumes: tuple[str, ...] = ()
    input_scope: str = "synthetic-only"


def build_plan(
    *,
    image: str,
    environment: str,
    source_revision: str,
    uv_lock_sha256: str,
    harness_sha256: str,
    run_id: str | None = None,
) -> SmokePlan:
    """Validate all identity pins and return the fixed single-GPU plan."""
    if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
        raise PlanError("image_must_be_immutable_registry_digest")
    if not isinstance(environment, str) or not environment.strip() or any(
        character.isspace() for character in environment
    ):
        raise PlanError("existing_environment_name_required")
    if not isinstance(source_revision, str) or not SHA40_RE.fullmatch(source_revision):
        raise PlanError("source_revision_must_be_full_git_sha")
    if not isinstance(uv_lock_sha256, str) or not SHA64_RE.fullmatch(uv_lock_sha256):
        raise PlanError("uv_lock_sha256_required")
    if not isinstance(harness_sha256, str) or not SHA64_RE.fullmatch(harness_sha256):
        raise PlanError("harness_sha256_required")
    chosen_id = run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[0-9a-f]{32}", chosen_id):
        raise PlanError("run_id_must_be_unique_uuid_hex")
    return SmokePlan(
        run_id=chosen_id,
        app_name=f"m0-synthetic-cuda-{chosen_id[:12]}",
        environment=environment,
        image=image,
        repository=REPOSITORY,
        source_revision=source_revision,
        uv_lock_sha256=uv_lock_sha256,
        harness_sha256=harness_sha256,
    )


def _synthetic_inputs(source_revision: str, uv_lock_sha256: str) -> Any:
    """Build a tiny in-memory dual-codec M0 input; no store or user data is read."""
    from tokenizers import Tokenizer

    from spireagent.artifact_contracts import Manifest, Producer
    from spireagent.json_boundary import FrozenObject
    from stpd.fullrun.features import ModelSample
    from stpd.fullrun.light_action_inputs import (
        INPUT_FORMAT,
        SCHEMA,
        LightActionTokenRow,
        LoadedLightActionInputs,
        fit_state_bpe,
    )
    from stpd.fullrun.token_inputs import input_texts
    from stpd.light_action_codec import SPEC, SPEC_SHA256, encode_action
    from stpd.stage1a_recipes import LIGHT_ACTION_M0_GRAPH

    samples = (
        ModelSample(
            transition_id="synthetic-transition-train",
            run_id="synthetic-run-train",
            split="train",
            surface="synthetic",
            family="synthetic",
            state_text="synthetic state: train",
            action_texts=("left", "right"),
            action_keys=("left", "right"),
            chosen_index=0,
        ),
        ModelSample(
            transition_id="synthetic-transition-dev",
            run_id="synthetic-run-dev",
            split="dev",
            surface="synthetic",
            family="synthetic",
            state_text="synthetic state: dev",
            action_texts=("stay", "go"),
            action_keys=("stay", "go"),
            chosen_index=0,
        ),
    )
    tokenizer_bytes = fit_state_bpe(samples, vocab_size=256)
    tokenizer = Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
    rows = tuple(
        LightActionTokenRow(
            state=tuple(tokenizer.encode(input_texts(sample.state_text, ())[0]).ids),
            actions=tuple(encode_action(text, max_bytes=16) for text in sample.action_texts),
            action_ids=sample.action_keys,
        )
        for sample in samples
    )
    metadata = {
        "schema": SCHEMA,
        "format": INPUT_FORMAT,
        "graph": LIGHT_ACTION_M0_GRAPH,
        "source_schema": "synthetic-only",
        "source_renderer": {"schema": "m0-synthetic-smoke-renderer-v1"},
        "state_codec": {
            "schema": "stpd/state-byte-bpe-v1",
            "family": "train-only-byte-bpe",
            "sha256": hashlib.sha256(tokenizer_bytes).hexdigest(),
            "vocab_size": tokenizer.get_vocab_size(),
            "max_tokens": 128,
        },
        "action_codec": {**SPEC, "sha256": SPEC_SHA256},
        "max_state_tokens": 128,
        "max_action_bytes": 16,
        "samples": len(samples),
    }
    manifest = Manifest(
        "training_input",
        Producer(REPOSITORY, source_revision, uv_lock_sha256),
        parameters=FrozenObject.of(metadata),
    )
    return LoadedLightActionInputs(manifest, samples, rows, tokenizer)


def run_engine_smoke(plan_data: dict[str, str]) -> dict[str, Any]:
    """Run one optimizer step, resume from its checkpoint, and hash candidate weights."""
    import torch

    from stpd.workers.token_ranking import LightActionM0Config, TokenRankingEngine

    if not torch.cuda.is_available():
        raise RuntimeError("cuda_unavailable_no_fallback")
    torch.cuda.set_device(0)
    device = torch.device("cuda", torch.cuda.current_device())
    if device.type != "cuda":
        raise RuntimeError("cuda_device_mismatch_no_fallback")

    plan = SmokePlan(**plan_data)
    inputs = _synthetic_inputs(plan.source_revision, plan.uv_lock_sha256)
    config = LightActionM0Config(device="cuda", seed=113, steps=2,
                                 max_state_tokens=128, max_action_bytes=16)
    engine = TokenRankingEngine(inputs, config)
    first_loss = engine.advance()
    if not torch.isfinite(torch.tensor(first_loss)):
        raise RuntimeError("non_finite_loss")

    checkpoint = engine.checkpoint()
    resumed = TokenRankingEngine(inputs, config)
    resumed.restore(checkpoint)
    if resumed.step != 1:
        raise RuntimeError("checkpoint_resume_step_mismatch")
    resumed_loss = resumed.advance()
    if not torch.isfinite(torch.tensor(resumed_loss)) or resumed.step != 2:
        raise RuntimeError("checkpoint_resume_non_finite_or_incomplete")

    weights = resumed.model_bytes()
    state = torch.cuda.get_device_properties(0)
    if "L4" not in state.name:
        raise RuntimeError("requested_gpu_identity_mismatch")
    return {
        "schema": "spireagent/m0-synthetic-cuda-smoke-receipt-v1",
        "run_id": plan.run_id,
        "environment": plan.environment,
        "input_scope": "synthetic-only",
        "synthetic_input_artifact_id": inputs.manifest.artifact_id,
        "repository": plan.repository,
        "source_revision": plan.source_revision,
        "uv_lock_sha256": plan.uv_lock_sha256,
        "harness_sha256": plan.harness_sha256,
        "image": plan.image,
        "gpu": plan.gpu,
        "device": device.type,
        "device_index": device.index,
        "device_name": state.name,
        "torch_version": str(torch.__version__),
        "cuda_runtime_version": torch.version.cuda,
        "completed_steps": resumed.step,
        "first_loss": first_loss,
        "first_loss_finite": True,
        "resumed_loss": resumed_loss,
        "resumed_loss_finite": True,
        "checkpoint_sha256": hashlib.sha256(checkpoint).hexdigest(),
        "weights_sha256": hashlib.sha256(weights).hexdigest(),
        "weights_bytes": len(weights),
    }


def _run_pinned_image_smoke(payload: dict[str, str]) -> dict[str, Any]:
    """Verify and run this script from the exact worker image checkout."""
    import os

    from spireagent.source import source_identity

    python_root = Path(__file__).resolve().parents[2]
    producer = source_identity(python_root)
    plan = SmokePlan(**payload)
    actual_harness_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if (producer.repository != plan.repository
            or producer.source_revision != plan.source_revision
            or producer.uv_lock_sha256 != plan.uv_lock_sha256
            or actual_harness_sha256 != plan.harness_sha256
            or os.environ.get("STPD_IMAGE_PROFILE") != "worker"):
        raise PlanError("pinned_worker_image_identity_mismatch")
    return run_engine_smoke(payload)


def _run_worker_payload() -> int:
    import json

    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise PlanError("worker_payload_object_required")
        receipt = _run_pinned_image_smoke(payload)
    except Exception as error:
        raise SystemExit(f"M0 synthetic image worker blocked: {type(error).__name__}") from None
    print(json.dumps(receipt, sort_keys=True, indent=2))
    return 0


def _preflight_environment(modal: Any, environment_name: str) -> None:
    """Read existing environments only; never create an environment or credential."""
    if not hasattr(signal, "setitimer"):
        raise PlanError("bounded_read_only_preflight_unavailable")
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    if previous_delay > 0 or previous_interval > 0:
        raise PlanError("cannot_bound_preflight_while_process_timer_active")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def deadline(_signum: int, _frame: Any) -> None:
        raise PlanError("modal_environment_list_timeout_30s")

    try:
        signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, 30)
        environments = modal.Environment.objects.list()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    if not any(environment.name == environment_name for environment in environments):
        raise PlanError("named_existing_modal_environment_not_found")


def _require_cuda_configuration() -> None:
    """Reject the current CPU/MPS-only engine before making any cloud call."""
    python_root = Path(__file__).resolve().parents[2]
    if str(python_root) not in sys.path:
        sys.path.insert(0, str(python_root))
    try:
        from stpd.workers.token_ranking import LightActionM0Config

        config = LightActionM0Config(
            device="cuda", seed=113, steps=2,
            max_state_tokens=128, max_action_bytes=16,
        )
    except Exception as error:
        raise PlanError("candidate_m0_engine_does_not_accept_cuda") from error
    if config.device != "cuda":
        raise PlanError("candidate_m0_engine_does_not_accept_cuda")


def execute(plan: SmokePlan) -> dict[str, Any]:
    """Submit exactly one bounded synthetic function to an existing environment."""
    _require_cuda_configuration()
    try:
        import modal
    except ImportError as error:
        raise PlanError("modal_sdk_unavailable") from error

    _preflight_environment(modal, plan.environment)
    app = modal.App(plan.app_name)
    image = modal.Image.from_registry(plan.image)

    @app.function(
        image=image,
        gpu="L4",
        cpu=2.0,
        memory=4096,
        min_containers=0,
        max_containers=1,
        retries=0,
        timeout=MAX_FUNCTION_SECONDS,
        startup_timeout=MAX_STARTUP_SECONDS,
        scaledown_window=MAX_SCALEDOWN_SECONDS,
        block_network=True,
        serialized=True,
        name="m0-synthetic-gpu-smoke",
    )
    def _worker(payload: dict[str, str]) -> dict[str, Any]:
        import json
        import os
        import subprocess

        command = [
            "/opt/stpd/python/.venv/bin/python",
            "/opt/stpd/python/deploy/cloud-worker/m0_synthetic_gpu_smoke.py",
            "--worker-payload",
        ]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "/opt/stpd/python"
        try:
            completed = subprocess.run(
                command,
                cwd="/opt/stpd/python",
                env=environment,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                check=False,
                timeout=25,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("synthetic_image_worker_timeout") from error
        if completed.returncode != 0:
            raise RuntimeError("synthetic_image_worker_failed")
        try:
            receipt = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("synthetic_image_worker_invalid_receipt") from error
        if not isinstance(receipt, dict):
            raise RuntimeError("synthetic_image_worker_invalid_receipt")
        return receipt

    payload = asdict(plan)
    # App.run is transient and does not deploy a persistent application. No secrets,
    # volumes, or network filesystem are attached to the function.
    with app.run(environment_name=plan.environment):
        receipt = _worker.remote(payload)
    if (receipt.get("schema") != "spireagent/m0-synthetic-cuda-smoke-receipt-v1"
            or receipt.get("run_id") != plan.run_id
            or receipt.get("input_scope") != "synthetic-only"):
        raise RuntimeError("invalid_smoke_receipt")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="immutable registry image@sha256 digest")
    parser.add_argument(
        "--environment", required=True, help="name of an existing Modal environment",
    )
    parser.add_argument("--source-revision", required=True, help="full 40-character git SHA")
    parser.add_argument("--uv-lock-sha256", required=True, help="pinned python/uv.lock SHA-256")
    parser.add_argument("--execute", action="store_true", help="run the bounded Modal smoke")
    return parser


def _verify_local_pins(source_revision: str, uv_lock_sha256: str) -> None:
    python_root = Path(__file__).resolve().parents[2]
    repo_root = python_root.parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PlanError("cannot_resolve_local_source_revision") from error
    if result.stdout.strip() != source_revision:
        raise PlanError("source_revision_does_not_match_checkout_head")
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root, check=True,
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PlanError("cannot_verify_local_checkout_cleanliness") from error
    if dirty.stdout:
        raise PlanError("clean_checkout_required")
    local_lock_sha256 = hashlib.sha256((python_root / "uv.lock").read_bytes()).hexdigest()
    if local_lock_sha256 != uv_lock_sha256:
        raise PlanError("uv_lock_sha256_does_not_match_checkout")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--worker-payload"]:
        return _run_worker_payload()
    args = _parser().parse_args(arguments)
    harness_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        _verify_local_pins(args.source_revision, args.uv_lock_sha256)
        plan = build_plan(
            image=args.image,
            environment=args.environment,
            source_revision=args.source_revision,
            uv_lock_sha256=args.uv_lock_sha256,
            harness_sha256=harness_sha256,
        )
        result = (execute(plan) if args.execute
                  else {"mode": "dry-run", "plan": asdict(plan)})
    except PlanError as error:
        raise SystemExit(f"M0 synthetic CUDA smoke blocked: {error}") from error
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
