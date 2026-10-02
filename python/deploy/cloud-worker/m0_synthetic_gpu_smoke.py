"""One-shot, synthetic-only CUDA smoke for the existing M0 engine.

This file is deliberately separate from ``modal_app.py``: it has no S3 mount,
storage secret, dataset input, or owner/admitter callback.  The default CLI path
only prints a validated plan.  A live Modal call requires ``--execute``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPOSITORY = "rsgcsg/STS2-The-Perfect-Defect-Project"
IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
MODAL_IMAGE_ID_RE = re.compile(r"^im-[A-Za-z0-9_-]+$")
OCI_IMAGE_KIND = "oci_registry_digest"
MODAL_IMAGE_KIND = "modal_image_id"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_FUNCTION_SECONDS = 30
MAX_STARTUP_SECONDS = 30
MAX_SCALEDOWN_SECONDS = 30
MAX_FUNCTION_CALL_WALL_SECONDS = MAX_FUNCTION_SECONDS + MAX_STARTUP_SECONDS
MAX_CLEANUP_SECONDS = 20
MAX_CLEANUP_COMMAND_SECONDS = 5
MAX_OUTER_WALL_SECONDS = (
    MAX_FUNCTION_CALL_WALL_SECONDS + MAX_CLEANUP_SECONDS + 40
)
ESTIMATED_FUNCTION_UPPER_SECONDS = (
    MAX_FUNCTION_SECONDS + MAX_STARTUP_SECONDS + MAX_SCALEDOWN_SECONDS
)
LOSS_COMPARISON_REL_TOL = 1e-6
LOSS_COMPARISON_ABS_TOL = 1e-7
WEIGHT_COMPARISON_ABS_TOL = 1e-6
MODAL_L4_USD_PER_SECOND = 0.000222
MODAL_CPU_USD_PER_CORE_SECOND = 0.0000131
MODAL_MEMORY_USD_PER_GIB_SECOND = 0.00000222
MODAL_MAX_REGION_MULTIPLIER = 1.75
MODAL_PRICING_AS_OF = "2026-10-02"
SMOKE_RECEIPT_SCHEMA = "spireagent/m0-synthetic-cuda-smoke-receipt-v2"
EXECUTION_OUTCOME_SCHEMA = "spireagent/m0-synthetic-cuda-smoke-execution-v1"
MODAL_CALL_STATUS_PROBE_SCRIPT = """
import json
import runpy
import sys

import modal

namespace = runpy.run_path(sys.argv[1], run_name="_m0_modal_status_probe")
call = modal.FunctionCall.from_id(sys.argv[2])
state = namespace["_call_status_from_call"](call, modal)
print(json.dumps({"status": state}))
"""


class PlanError(ValueError):
    """The requested smoke cannot be proven to stay inside its fixed scope."""


class SmokeExecutionError(RuntimeError):
    """A one-shot execution failed, including whether Modal stop was confirmed."""

    def __init__(self, reason: str, outcome: dict[str, Any]) -> None:
        super().__init__(reason)
        self.outcome = outcome


@dataclass(frozen=True)
class SmokePlan:
    run_id: str
    app_name: str
    environment: str
    image: str
    image_kind: str
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
    estimated_function_upper_seconds: int = ESTIMATED_FUNCTION_UPPER_SECONDS
    function_call_wall_seconds: int = MAX_FUNCTION_CALL_WALL_SECONDS
    outer_wall_seconds: int = MAX_OUTER_WALL_SECONDS
    secrets: tuple[str, ...] = ()
    volumes: tuple[str, ...] = ()
    input_scope: str = "synthetic-only"


def build_plan(
    *,
    image: str,
    image_kind: str = OCI_IMAGE_KIND,
    environment: str,
    source_revision: str,
    uv_lock_sha256: str,
    harness_sha256: str,
    run_id: str | None = None,
) -> SmokePlan:
    """Validate all identity pins and return the fixed single-GPU plan."""
    if image_kind == OCI_IMAGE_KIND:
        if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
            raise PlanError("image_must_be_immutable_registry_digest")
    elif image_kind == MODAL_IMAGE_KIND:
        if not isinstance(image, str) or not MODAL_IMAGE_ID_RE.fullmatch(image):
            raise PlanError("modal_image_id_required")
    else:
        raise PlanError("unsupported_image_kind")
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
        image_kind=image_kind,
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


def _compare_model_state_tensors(
    torch: Any,
    resumed_state: dict[str, Any],
    continuous_state: dict[str, Any],
) -> dict[str, Any]:
    """Compare full final tensor inventories and values for resumed/continuous runs."""
    if not isinstance(resumed_state, dict) or not isinstance(continuous_state, dict):
        raise RuntimeError("checkpoint_resume_model_state_invalid")
    if not resumed_state or set(resumed_state) != set(continuous_state):
        raise RuntimeError("checkpoint_resume_continuous_weight_keys_mismatch")

    inventory: list[dict[str, Any]] = []
    element_count = 0
    max_abs_difference = 0.0
    for key in sorted(resumed_state):
        resumed_tensor = resumed_state[key]
        continuous_tensor = continuous_state[key]
        if (resumed_tensor.shape != continuous_tensor.shape
                or resumed_tensor.dtype != continuous_tensor.dtype):
            raise RuntimeError("checkpoint_resume_continuous_weight_shape_or_dtype_mismatch")
        if (not bool(torch.isfinite(resumed_tensor).all())
                or not bool(torch.isfinite(continuous_tensor).all())):
            raise RuntimeError("checkpoint_resume_continuous_non_finite_weight")
        count = int(resumed_tensor.numel())
        element_count += count
        inventory.append({
            "key": key,
            "shape": list(resumed_tensor.shape),
            "dtype": str(resumed_tensor.dtype),
        })
        if count:
            difference = torch.abs(
                resumed_tensor.detach().to(dtype=torch.float64)
                - continuous_tensor.detach().to(dtype=torch.float64)
            )
            max_abs_difference = max(max_abs_difference, float(difference.max().item()))

    if element_count <= 0:
        raise RuntimeError("checkpoint_resume_empty_model_state")
    inventory_sha256 = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "resumed_weight_inventory_sha256": inventory_sha256,
        "continuous_weight_inventory_sha256": inventory_sha256,
        "resumed_weight_tensor_count": len(inventory),
        "continuous_weight_tensor_count": len(inventory),
        "resumed_weight_element_count": element_count,
        "continuous_weight_element_count": element_count,
        "weight_max_abs_difference": max_abs_difference,
        "weight_comparison_tolerance": {"abs_tol": WEIGHT_COMPARISON_ABS_TOL},
        "resume_weights_match_continuous": (
            max_abs_difference <= WEIGHT_COMPARISON_ABS_TOL
        ),
    }


def run_engine_smoke(plan_data: dict[str, str]) -> dict[str, Any]:
    """Run two steps, compare resumed and continuous training, and hash the export."""
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

    continuous = TokenRankingEngine(inputs, config)
    continuous.advance()
    continuous_loss = continuous.advance()
    if not torch.isfinite(torch.tensor(continuous_loss)) or continuous.step != 2:
        raise RuntimeError("continuous_training_non_finite_or_incomplete")
    resume_loss_abs_error = abs(resumed_loss - continuous_loss)
    resume_loss_matches_continuous = math.isclose(
        resumed_loss,
        continuous_loss,
        rel_tol=LOSS_COMPARISON_REL_TOL,
        abs_tol=LOSS_COMPARISON_ABS_TOL,
    )
    if not resume_loss_matches_continuous:
        raise RuntimeError("checkpoint_resume_continuous_training_mismatch")

    weight_comparison = _compare_model_state_tensors(
        torch, resumed.model.state_dict(), continuous.model.state_dict(),
    )
    if not weight_comparison["resume_weights_match_continuous"]:
        raise RuntimeError("checkpoint_resume_continuous_weights_mismatch")

    weights = resumed.model_bytes()
    if not weights:
        raise RuntimeError("empty_candidate_export")
    state = torch.cuda.get_device_properties(0)
    if "L4" not in state.name:
        raise RuntimeError("requested_gpu_identity_mismatch")
    return {
        "schema": SMOKE_RECEIPT_SCHEMA,
        "run_id": plan.run_id,
        "environment": plan.environment,
        "input_scope": "synthetic-only",
        "synthetic_input_artifact_id": inputs.manifest.artifact_id,
        "repository": plan.repository,
        "source_revision": plan.source_revision,
        "uv_lock_sha256": plan.uv_lock_sha256,
        "harness_sha256": plan.harness_sha256,
        "image": plan.image,
        "image_kind": plan.image_kind,
        "gpu": plan.gpu,
        "device": device.type,
        "device_index": device.index,
        "device_name": state.name,
        "torch_version": str(torch.__version__),
        "cuda_runtime_version": torch.version.cuda,
        "completed_steps": resumed.step,
        "resume_checkpoint_step": 1,
        "continuous_steps": continuous.step,
        "first_loss": first_loss,
        "first_loss_finite": True,
        "resumed_loss": resumed_loss,
        "resumed_loss_finite": True,
        "continuous_loss": continuous_loss,
        "continuous_loss_finite": True,
        "resume_loss_abs_error": resume_loss_abs_error,
        "resume_loss_matches_continuous": resume_loss_matches_continuous,
        "resume_matches_continuous": (
            resume_loss_matches_continuous
            and weight_comparison["resume_weights_match_continuous"]
        ),
        "loss_comparison_tolerance": {
            "rel_tol": LOSS_COMPARISON_REL_TOL,
            "abs_tol": LOSS_COMPARISON_ABS_TOL,
        },
        "checkpoint_sha256": hashlib.sha256(checkpoint).hexdigest(),
        "export_sha256": hashlib.sha256(weights).hexdigest(),
        "export_bytes": len(weights),
        **weight_comparison,
    }


def _run_pinned_image_smoke(payload: dict[str, str]) -> dict[str, Any]:
    """Verify and run this script from the exact worker image checkout."""
    import os

    from spireagent.source import source_identity

    python_root = Path(__file__).resolve().parents[2]
    producer = source_identity(python_root)
    plan = SmokePlan(**payload)
    actual_harness_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    actual_modal_image_id = _validate_runtime_modal_image_id(plan, os.environ)
    if (producer.repository != plan.repository
            or producer.source_revision != plan.source_revision
            or producer.uv_lock_sha256 != plan.uv_lock_sha256
            or actual_harness_sha256 != plan.harness_sha256
            or os.environ.get("STPD_IMAGE_PROFILE") != "worker"):
        raise PlanError("pinned_worker_image_identity_mismatch")
    receipt = run_engine_smoke(payload)
    receipt["runtime_modal_image_id"] = actual_modal_image_id
    return receipt


def _validate_runtime_modal_image_id(plan: SmokePlan, environ: Any) -> str:
    """Bind the receipt to Modal's reserved runtime image id."""
    actual_image_id = environ.get("MODAL_IMAGE_ID")
    if not isinstance(actual_image_id, str) or not MODAL_IMAGE_ID_RE.fullmatch(actual_image_id):
        raise PlanError("runtime_modal_image_id_missing_or_invalid")
    if plan.image_kind == MODAL_IMAGE_KIND and actual_image_id != plan.image:
        raise PlanError("runtime_modal_image_id_mismatch")
    return actual_image_id


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


def _preflight_environment(environment_name: str) -> None:
    """List names through a timed CLI subprocess; never create credentials/envs."""
    environments = _modal_cli_json(["environment", "list"], timeout_seconds=30)
    if not isinstance(environments, list):
        raise PlanError("modal_environment_list_json_array_required")
    if not any(
        isinstance(environment, dict) and environment.get("name") == environment_name
        for environment in environments
    ):
        raise PlanError("named_existing_modal_environment_not_found")


def _wall_deadline_supported() -> bool:
    """Return whether this thread can enforce the launcher’s signal deadline."""
    return (
        threading.current_thread() is threading.main_thread()
        and callable(getattr(signal, "signal", None))
        and callable(getattr(signal, "getsignal", None))
        and callable(getattr(signal, "getitimer", None))
        and callable(getattr(signal, "setitimer", None))
        and hasattr(signal, "SIGALRM")
        and hasattr(signal, "ITIMER_REAL")
    )


def _require_wall_deadline_support() -> None:
    """Fail before the Modal SDK when the host cannot bound synchronous calls."""
    if not _wall_deadline_supported():
        raise PlanError("bounded_modal_call_deadline_unavailable")


def _with_wall_deadline(seconds: float, callback: Any) -> Any:
    """Interrupt a synchronous Modal client call while reserving cleanup time."""
    if seconds <= 0 or not _wall_deadline_supported():
        raise TimeoutError("bounded_modal_call_deadline_unavailable")
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    previous_handler = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    effective_seconds = min(seconds, previous_delay) if previous_delay > 0 else seconds

    def deadline(_signum: int, _frame: Any) -> None:
        raise TimeoutError("bounded_modal_call_deadline_exceeded")

    try:
        signal.signal(signal.SIGALRM, deadline)
        # Repeat while a context manager is unwinding after its first deadline.
        signal.setitimer(signal.ITIMER_REAL, effective_seconds, 0.5)
        return callback()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay > 0:
            remaining = previous_delay - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("bounded_modal_call_deadline_exceeded")
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_interval)


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


def _finite_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA64_RE.fullmatch(value) is not None


def validate_receipt(receipt: object, plan: SmokePlan) -> dict[str, Any]:
    """Reject incomplete, malformed, or unpinned worker receipts at the caller."""
    if not isinstance(receipt, dict):
        raise PlanError("invalid_smoke_receipt_object")
    expected_pins = {
        "schema": SMOKE_RECEIPT_SCHEMA,
        "run_id": plan.run_id,
        "environment": plan.environment,
        "input_scope": "synthetic-only",
        "repository": plan.repository,
        "source_revision": plan.source_revision,
        "uv_lock_sha256": plan.uv_lock_sha256,
        "harness_sha256": plan.harness_sha256,
        "image": plan.image,
        "image_kind": plan.image_kind,
        "gpu": "L4",
        "device": "cuda",
        "device_index": 0,
        "completed_steps": 2,
        "resume_checkpoint_step": 1,
        "continuous_steps": 2,
    }
    for field, expected in expected_pins.items():
        if type(receipt.get(field)) is not type(expected) or receipt.get(field) != expected:
            raise PlanError(f"smoke_receipt_{field}_mismatch")

    runtime_modal_image_id = receipt.get("runtime_modal_image_id")
    if (not isinstance(runtime_modal_image_id, str)
            or not MODAL_IMAGE_ID_RE.fullmatch(runtime_modal_image_id)):
        raise PlanError("smoke_receipt_runtime_modal_image_id_invalid")
    if (plan.image_kind == MODAL_IMAGE_KIND
            and runtime_modal_image_id != plan.image):
        raise PlanError("smoke_receipt_runtime_modal_image_id_mismatch")

    device_name = receipt.get("device_name")
    if not isinstance(device_name, str) or "l4" not in device_name.lower():
        raise PlanError("smoke_receipt_actual_l4_device_required")
    for field in ("synthetic_input_artifact_id",):
        value = receipt.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PlanError(f"smoke_receipt_{field}_required")
    for field in ("torch_version", "cuda_runtime_version"):
        value = receipt.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PlanError(f"smoke_receipt_{field}_required")

    for field in (
        "first_loss_finite",
        "resumed_loss_finite",
        "continuous_loss_finite",
        "resume_loss_matches_continuous",
        "resume_weights_match_continuous",
        "resume_matches_continuous",
    ):
        if type(receipt.get(field)) is not bool or receipt[field] is not True:
            raise PlanError(f"smoke_receipt_{field}_must_be_true")
    for field in ("first_loss", "resumed_loss", "continuous_loss", "resume_loss_abs_error"):
        if not _finite_number(receipt.get(field)):
            raise PlanError(f"smoke_receipt_{field}_must_be_finite_number")
    if receipt["resume_loss_abs_error"] < 0:
        raise PlanError("smoke_receipt_resume_loss_abs_error_must_be_nonnegative")

    tolerance = receipt.get("loss_comparison_tolerance")
    expected_tolerance = {
        "rel_tol": LOSS_COMPARISON_REL_TOL,
        "abs_tol": LOSS_COMPARISON_ABS_TOL,
    }
    if (not isinstance(tolerance, dict)
            or set(tolerance) != set(expected_tolerance)
            or any(type(tolerance[key]) not in (int, float)
                   or tolerance[key] != expected_tolerance[key]
                   for key in expected_tolerance)):
        raise PlanError("smoke_receipt_loss_comparison_tolerance_mismatch")
    observed_error = abs(receipt["resumed_loss"] - receipt["continuous_loss"])
    if not math.isclose(
        receipt["resume_loss_abs_error"], observed_error, rel_tol=0, abs_tol=1e-12,
    ) or not math.isclose(
        receipt["resumed_loss"], receipt["continuous_loss"],
        rel_tol=LOSS_COMPARISON_REL_TOL,
        abs_tol=LOSS_COMPARISON_ABS_TOL,
    ):
        raise PlanError("smoke_receipt_resume_continuous_comparison_failed")

    inventory_hashes = (
        receipt.get("resumed_weight_inventory_sha256"),
        receipt.get("continuous_weight_inventory_sha256"),
    )
    if (not all(_valid_sha256(value) for value in inventory_hashes)
            or inventory_hashes[0] != inventory_hashes[1]):
        raise PlanError("smoke_receipt_weight_inventory_mismatch")
    for field in (
        "resumed_weight_tensor_count",
        "continuous_weight_tensor_count",
        "resumed_weight_element_count",
        "continuous_weight_element_count",
    ):
        if type(receipt.get(field)) is not int or receipt[field] <= 0:
            raise PlanError(f"smoke_receipt_{field}_must_be_positive")
    if (receipt["resumed_weight_tensor_count"]
            != receipt["continuous_weight_tensor_count"]
            or receipt["resumed_weight_element_count"]
            != receipt["continuous_weight_element_count"]):
        raise PlanError("smoke_receipt_weight_count_mismatch")
    max_weight_difference = receipt.get("weight_max_abs_difference")
    if not _finite_number(max_weight_difference) or max_weight_difference < 0:
        raise PlanError("smoke_receipt_weight_max_abs_difference_invalid")
    weight_tolerance = receipt.get("weight_comparison_tolerance")
    expected_weight_tolerance = {"abs_tol": WEIGHT_COMPARISON_ABS_TOL}
    if (not isinstance(weight_tolerance, dict)
            or set(weight_tolerance) != set(expected_weight_tolerance)
            or type(weight_tolerance.get("abs_tol")) not in (int, float)
            or weight_tolerance["abs_tol"] != WEIGHT_COMPARISON_ABS_TOL
            or max_weight_difference > WEIGHT_COMPARISON_ABS_TOL):
        raise PlanError("smoke_receipt_weight_comparison_tolerance_mismatch")

    for field in ("checkpoint_sha256", "export_sha256"):
        if not _valid_sha256(receipt.get(field)):
            raise PlanError(f"smoke_receipt_{field}_invalid")
    export_bytes = receipt.get("export_bytes")
    if type(export_bytes) is not int or export_bytes <= 0:
        raise PlanError("smoke_receipt_export_bytes_must_be_positive")
    worker_seconds = receipt.get("worker_function_seconds")
    if not _finite_number(worker_seconds) or worker_seconds < 0:
        raise PlanError("smoke_receipt_worker_function_seconds_invalid")
    return receipt


def _billing_estimate(plan: SmokePlan) -> dict[str, Any]:
    memory_gib = plan.memory_mb / 1024
    per_second = (
        MODAL_L4_USD_PER_SECOND
        + plan.cpu * MODAL_CPU_USD_PER_CORE_SECOND
        + memory_gib * MODAL_MEMORY_USD_PER_GIB_SECOND
    )
    base_usd = per_second * plan.estimated_function_upper_seconds
    return {
        "pricing_as_of": MODAL_PRICING_AS_OF,
        "currency": "USD",
        "estimated_function_upper_seconds": plan.estimated_function_upper_seconds,
        "base_region_estimate_usd": round(base_usd, 8),
        "max_region_multiplier": MODAL_MAX_REGION_MULTIPLIER,
        "max_region_estimate_usd": round(base_usd * MODAL_MAX_REGION_MULTIPLIER, 8),
        "status": "estimate_only_not_provider_usage",
        "assumption": "one L4 container; startup + function timeout + scaledown window",
        "image_build_usd": None,
        "image_build_status": "separate_not_included_in_function_estimate",
    }


def _modal_cli_json(args: list[str], *, timeout_seconds: float) -> Any:
    cli = Path(sys.executable).with_name("modal")
    if not cli.is_file():
        raise PlanError("modal_cli_required_for_stop_confirmation")
    try:
        completed = subprocess.run(
            [str(cli), *args, "--json"], capture_output=True, text=True,
            check=False, timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PlanError("modal_stop_confirmation_query_failed") from error
    if completed.returncode != 0:
        raise PlanError("modal_stop_confirmation_query_failed")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise PlanError("modal_stop_confirmation_invalid_json") from error


def _call_status_from_call(call: Any, modal: Any) -> str:
    """Classify a completed SDK poll; this runs only in the killable probe process."""
    try:
        call.get(timeout=0)
    except modal.exception.FunctionTimeoutError:
        return "failed_terminal"
    except modal.exception.OutputExpiredError:
        return "failed_terminal"
    except modal.exception.RemoteError:
        return "failed_terminal"
    except modal.exception.TimeoutError:
        return "pending"
    except Exception:
        return "unknown"
    return "completed"


def _probe_call_status(function_call_id: str, *, timeout_seconds: float) -> str:
    """Probe by ID in a killable child so SDK retries cannot outlive cleanup."""
    if timeout_seconds <= 0:
        return "unknown"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                MODAL_CALL_STATUS_PROBE_SCRIPT,
                str(Path(__file__).resolve()),
                function_call_id,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps the child when this deadline expires.
        return "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return "unknown"
    status = result.get("status") if isinstance(result, dict) else None
    if status not in {"unknown", "pending", "failed_terminal", "completed"}:
        return "unknown"
    return status


def _terminal_call_status(
    function_call_id: str | None,
    submission_attempted: bool,
    *,
    timeout_seconds: float,
) -> str:
    if not submission_attempted:
        return "not_submitted"
    if not isinstance(function_call_id, str) or not function_call_id:
        return "unknown"
    return _probe_call_status(function_call_id, timeout_seconds=timeout_seconds)


def _stop_evidence(
    app_id: str, environment: str, *, deadline: float,
) -> dict[str, Any]:
    def command_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PlanError("modal_stop_confirmation_deadline_exceeded")
        return min(float(MAX_CLEANUP_COMMAND_SECONDS), remaining)

    app_rows = _modal_cli_json(
        ["app", "list", "--env", environment],
        timeout_seconds=command_timeout(),
    )
    if not isinstance(app_rows, list):
        raise PlanError("modal_app_list_json_array_required")
    matching = [row for row in app_rows if isinstance(row, dict) and row.get("app_id") == app_id]
    if len(matching) != 1:
        raise PlanError("modal_app_lifecycle_not_found_or_ambiguous")
    app = matching[0]
    if app.get("state") != "stopped" or app.get("tasks") != "0":
        raise PlanError("modal_app_not_stopped_with_zero_tasks")

    container_rows = _modal_cli_json(
        ["container", "list", "--app-id", app_id, "--env", environment],
        timeout_seconds=command_timeout(),
    )
    if not isinstance(container_rows, list):
        raise PlanError("modal_container_list_json_array_required")
    if container_rows:
        raise PlanError("modal_containers_still_running")
    return {"app_state": "stopped", "app_tasks": 0, "running_containers": 0}


def _execution_error(
    reason: str,
    *,
    plan: SmokePlan,
    app_id: str | None,
    function_call_id: str | None,
    call_terminal: str,
    stop_confirmation: str,
    call_wall_seconds: float,
    cancel_error: str | None,
) -> SmokeExecutionError:
    call_state_confirmed = call_terminal in {
        "completed", "failed_terminal", "not_submitted", "terminal_after_app_stop",
    }
    outcome = {
        "schema": EXECUTION_OUTCOME_SCHEMA,
        "status": (
            "failed"
            if stop_confirmation == "confirmed" and call_state_confirmed
            else "unknown"
        ),
        "reason": reason,
        "run_id": plan.run_id,
        "app_name": plan.app_name,
        "app_id": app_id,
        "function_call_id": function_call_id,
        "call_terminal": call_terminal,
        "stop_confirmation": stop_confirmation,
        "cancel_error": cancel_error,
        "call_wall_seconds": round(call_wall_seconds, 3),
        "estimated_function_cost": _billing_estimate(plan),
        "actual_provider_usage": "unknown_not_returned_by_function_call",
    }
    return SmokeExecutionError(reason, outcome)


def _cancel_call(call: Any, *, timeout_seconds: float) -> None:
    _with_wall_deadline(
        timeout_seconds,
        lambda: call.cancel(terminate_containers=True),
    )


def execute(plan: SmokePlan) -> dict[str, Any]:
    """Run the developer smoke with bounded POSIX main-thread Modal calls.

    The host launcher requires ``signal.setitimer``; worker runtime platform
    support does not make this synchronous launcher portable to every host OS.
    """
    _require_wall_deadline_support()
    _require_cuda_configuration()
    try:
        import modal
    except ImportError as error:
        raise PlanError("modal_sdk_unavailable") from error

    started = time.monotonic()
    outer_deadline = started + plan.outer_wall_seconds
    _preflight_environment(plan.environment)
    app = modal.App(plan.app_name)
    image = (
        modal.Image.from_id(plan.image)
        if plan.image_kind == MODAL_IMAGE_KIND
        else modal.Image.from_registry(plan.image)
    )

    @app.function(
        image=image,
        gpu="L4",
        cpu=(2.0, 2.0),
        memory=(4096, 4096),
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
        worker_started = time.monotonic()
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
        receipt["worker_function_seconds"] = round(time.monotonic() - worker_started, 6)
        return receipt

    payload = asdict(plan)
    app_id: str | None = None
    call: Any | None = None
    function_call_id: str | None = None
    worker_receipt: object | None = None
    call_error: str | None = None
    cancel_error: str | None = None
    call_started: float | None = None
    call_wall_seconds = 0.0
    submission_attempted = False
    cancellation_attempted = False
    call_terminal = "unknown"

    def cancel_pending_call() -> None:
        nonlocal cancellation_attempted, cancel_error
        if (not submission_attempted or call is None
                or call_terminal not in {"unknown", "pending"}
                or cancellation_attempted):
            return
        cancellation_attempted = True
        try:
            cancel_timeout = min(
                MAX_CLEANUP_COMMAND_SECONDS,
                max(0.1, outer_deadline - time.monotonic()),
            )
            _cancel_call(call, timeout_seconds=cancel_timeout)
        except Exception as error:
            cancel_error = f"modal_function_call_cancel_{type(error).__name__}"

    def run_ephemeral_app() -> None:
        nonlocal app_id, call, function_call_id, worker_receipt, call_error
        nonlocal call_started, call_wall_seconds, submission_attempted, call_terminal
        with app.run(environment_name=plan.environment):
            app_id = getattr(app, "app_id", None)
            if not isinstance(app_id, str) or not app_id:
                call_error = "modal_app_id_unavailable_before_submit"
                return
            remaining = outer_deadline - time.monotonic() - MAX_CLEANUP_SECONDS
            allowed_wait = min(plan.function_call_wall_seconds, remaining)
            if allowed_wait <= 0:
                call_error = "outer_wall_deadline_before_submit"
                return

            call_started = time.monotonic()
            submission_attempted = True
            try:
                call = _worker.spawn(payload)
                function_call_id = getattr(call, "object_id", None)
                if not isinstance(function_call_id, str) or not function_call_id:
                    call_error = "modal_function_call_id_unavailable"
                else:
                    remaining = min(
                        allowed_wait,
                        outer_deadline - time.monotonic() - MAX_CLEANUP_SECONDS,
                    )
                    if remaining <= 0:
                        call_error = "outer_wall_deadline_after_submit"
                        call_terminal = "pending"
                    else:
                        worker_receipt = call.get(timeout=remaining)
                        call_terminal = "completed"
            except modal.exception.FunctionTimeoutError:
                call_error = "modal_function_execution_timeout"
                call_terminal = "failed_terminal"
            except modal.exception.OutputExpiredError:
                call_error = "modal_function_output_expired"
                call_terminal = "failed_terminal"
            except modal.exception.RemoteError:
                call_error = "modal_function_call_remote_failure"
                call_terminal = "failed_terminal"
            except modal.exception.TimeoutError:
                call_error = "modal_function_call_wall_deadline"
                call_terminal = "pending" if call is not None else "unknown"
                cancel_pending_call()
            except TimeoutError as error:
                outer_deadline_fired = str(error) == "bounded_modal_call_deadline_exceeded"
                call_error = (
                    "modal_outer_wall_deadline"
                    if outer_deadline_fired
                    else "modal_function_call_wall_deadline"
                )
                call_terminal = "pending" if call is not None else "unknown"
                if not outer_deadline_fired:
                    cancel_pending_call()
            except Exception as error:
                call_error = f"modal_function_call_{type(error).__name__}"
                call_terminal = "unknown"
                cancel_pending_call()
            finally:
                call_wall_seconds = time.monotonic() - call_started

    # App.run creates an ephemeral App. Exiting it is Modal's stop path. Bound
    # startup, the call, and context teardown while keeping cleanup time available.
    try:
        run_budget = outer_deadline - time.monotonic() - MAX_CLEANUP_SECONDS
        _with_wall_deadline(run_budget, run_ephemeral_app)
    except Exception as error:
        if call_error is None:
            call_error = f"modal_ephemeral_app_{type(error).__name__}"
        if submission_attempted and call is not None and call_terminal == "unknown":
            call_terminal = "pending"

    cancel_pending_call()

    if call_error is None:
        try:
            worker_receipt = validate_receipt(worker_receipt, plan)
        except PlanError as error:
            call_error = str(error)

    cleanup_deadline = min(outer_deadline, time.monotonic() + MAX_CLEANUP_SECONDS)
    stop_evidence: dict[str, Any] | None = None
    if not submission_attempted:
        call_terminal = "not_submitted"
    elif call_terminal in {"unknown", "pending"}:
        remaining = cleanup_deadline - time.monotonic()
        if remaining > 0:
            call_terminal = _terminal_call_status(
                function_call_id,
                submission_attempted,
                timeout_seconds=min(float(MAX_CLEANUP_COMMAND_SECONDS), remaining),
            )
    while time.monotonic() < cleanup_deadline:
        if time.monotonic() >= cleanup_deadline:
            break
        if stop_evidence is None and app_id:
            with suppress(PlanError):
                stop_evidence = _stop_evidence(
                    app_id, plan.environment, deadline=cleanup_deadline,
                )
        if (stop_evidence is not None and call is not None and call_terminal == "pending"):
            call_terminal = "terminal_after_app_stop"
        if (call_terminal in {
                "completed", "failed_terminal", "not_submitted", "terminal_after_app_stop",
            }
                and stop_evidence is not None):
            break
        if stop_evidence is not None and call is None and submission_attempted:
            break
        remaining = cleanup_deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.25, remaining))

    stop_confirmation = "confirmed" if stop_evidence is not None else "unknown"
    if call_error is not None or call_terminal != "completed" or stop_confirmation != "confirmed":
        reason = call_error or cancel_error or "modal_terminal_or_zero_container_unconfirmed"
        raise _execution_error(
            reason,
            plan=plan,
            app_id=app_id,
            function_call_id=function_call_id,
            call_terminal=call_terminal,
            stop_confirmation=stop_confirmation,
            call_wall_seconds=call_wall_seconds,
            cancel_error=cancel_error,
        )

    result = dict(worker_receipt)
    result["execution"] = {
        "schema": EXECUTION_OUTCOME_SCHEMA,
        "status": "completed",
        "app_name": plan.app_name,
        "app_id": app_id,
        "function_call_id": function_call_id,
        "call_terminal": call_terminal,
        "stop_confirmation": stop_confirmation,
        **(stop_evidence or {}),
        "call_wall_seconds": round(call_wall_seconds, 3),
        "worker_function_seconds": worker_receipt["worker_function_seconds"],
        "estimated_function_cost": _billing_estimate(plan),
        "actual_provider_usage": "unknown_not_returned_by_function_call",
    }
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    image_group = parser.add_mutually_exclusive_group(required=True)
    image_group.add_argument(
        "--image", help="immutable registry image@sha256 digest (OCI path)",
    )
    image_group.add_argument(
        "--modal-image-id", help="existing Modal image object ID (im-...)",
    )
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
        if args.modal_image_id is not None:
            image = args.modal_image_id
            image_kind = MODAL_IMAGE_KIND
        else:
            image = args.image
            image_kind = OCI_IMAGE_KIND
        plan = build_plan(
            image=image,
            image_kind=image_kind,
            environment=args.environment,
            source_revision=args.source_revision,
            uv_lock_sha256=args.uv_lock_sha256,
            harness_sha256=harness_sha256,
        )
        result = (execute(plan) if args.execute
                  else {"mode": "dry-run", "plan": asdict(plan)})
    except PlanError as error:
        raise SystemExit(f"M0 synthetic CUDA smoke blocked: {error}") from error
    except SmokeExecutionError as error:
        raise SystemExit(json.dumps(error.outcome, sort_keys=True)) from error
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
