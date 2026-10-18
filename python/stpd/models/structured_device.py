"""Training-only device adapter for the unchanged StructuredM2 inference graph.

No device is inferred from availability, no CPU/GPU resume conversion, and no
bitwise CPU/GPU parity claim. CPU export is separate from numerical resume.
"""

from __future__ import annotations

import os
import platform
import random
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

import numpy as np
import torch
from torch import Tensor

from spireagent.json_boundary import BoundaryError, FrozenObject

from ..fullrun.structured_sequences import StructuredDataset
from ..native_graph_spec import control_from_identity
from ..structured_profiles import CONTROL_SCOPES
from ..structured_remote_contracts import (
    CHECKPOINT_SCHEMA,
    StructuredDevicePolicy,
    numerical_projection,
    sha256,
)
from ..structured_workload_contracts import StructuredTrainingConfig
from ..workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from .structured_engine import MAX_CHECKPOINT_BYTES, StructuredTrainingEngine
from .structured_m2 import GRAPH_ID, StructuredM2

STAGE = "structured_device"


def structured_device_runtime(policy: StructuredDevicePolicy) -> dict[str, Any]:
    """Actual producer facts, suitable for prequalification and exact target binding."""
    if policy.device == "cuda:0" and not torch.cuda.is_available():
        raise BoundaryError(STAGE, "declared_cuda_unavailable")
    runtime: dict[str, Any] = {
        "torch_version": str(torch.__version__),
        "numpy_version": str(np.__version__),
        "python_version": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "device": policy.device,
        "dtype": "float32",
        "threads": torch.get_num_threads(),
        "interop_threads": torch.get_num_interop_threads(),
        "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        "mkldnn": torch.backends.mkldnn.enabled,
        "matmul_allow_tf32": torch.backends.cuda.matmul.allow_tf32,
        "cudnn_allow_tf32": torch.backends.cudnn.allow_tf32,
        "cudnn_benchmark": torch.backends.cudnn.benchmark,
    }
    if (
        runtime["deterministic_algorithms"] != policy.deterministic_algorithms
        or runtime["matmul_allow_tf32"] != policy.allow_tf32
        or runtime["cudnn_allow_tf32"] != policy.allow_tf32
        or runtime["cudnn_benchmark"] != policy.cudnn_benchmark
    ):
        raise BoundaryError(STAGE, "actual_numeric_policy_mismatch")
    if policy.device == "cuda:0":
        if os.environ.get("CUBLAS_WORKSPACE_CONFIG") not in {":4096:8", ":16:8"}:
            raise BoundaryError(STAGE, "cuda_deterministic_workspace_required")
        runtime.update(
            cuda_version=torch.version.cuda,
            cudnn_version=torch.backends.cudnn.version(),
            cuda_device_name=torch.cuda.get_device_name(0),
            cuda_device_capability=list(torch.cuda.get_device_capability(0)),
            cuda_rng_state_bytes=torch.cuda.get_rng_state(0).numel(),
            cublas_workspace_config=os.environ["CUBLAS_WORKSPACE_CONFIG"],
        )
    return runtime


@contextmanager
def structured_device_session(
    policy: StructuredDevicePolicy, *, cpu_threads: int
) -> Iterator[None]:
    """Restore process numerical settings and CPU/CUDA RNG after this isolated attempt."""
    if policy.device == "cuda:0" and not torch.cuda.is_available():
        raise BoundaryError(STAGE, "declared_cuda_unavailable")
    devices = [0] if policy.device == "cuda:0" else []
    previous = (
        torch.get_num_threads(),
        torch.are_deterministic_algorithms_enabled(),
        torch.is_deterministic_algorithms_warn_only_enabled(),
        torch.backends.cuda.matmul.allow_tf32,
        torch.backends.cudnn.allow_tf32,
        torch.backends.cudnn.benchmark,
    )
    python_rng, numpy_rng = random.getstate(), np.random.get_state()
    try:
        torch.set_num_threads(cpu_threads)
        torch.use_deterministic_algorithms(policy.deterministic_algorithms, warn_only=False)
        torch.backends.cuda.matmul.allow_tf32 = policy.allow_tf32
        torch.backends.cudnn.allow_tf32 = policy.allow_tf32
        torch.backends.cudnn.benchmark = policy.cudnn_benchmark
        with torch.random.fork_rng(devices=devices):
            structured_device_runtime(policy)
            yield
    finally:
        random.setstate(python_rng)
        np.random.set_state(numpy_rng)
        torch.set_num_threads(previous[0])
        torch.use_deterministic_algorithms(previous[1], warn_only=previous[2])
        torch.backends.cuda.matmul.allow_tf32 = previous[3]
        torch.backends.cudnn.allow_tf32 = previous[4]
        torch.backends.cudnn.benchmark = previous[5]


class _TrainingStructuredM2(StructuredM2):
    """Only finite FP32 device validation differs; E/W/N methods are inherited."""

    def __init__(self, *, policy: StructuredDevicePolicy, seed: int, model_control: Any) -> None:
        self.training_policy = policy
        super().__init__(seed=seed, model_control=model_control)
        self.to(torch.device(policy.device))
        self.validate_parameters()

    def validate_parameters(self) -> None:
        expected = torch.device(self.training_policy.device)
        if any(
            parameter.device != expected
            or parameter.dtype != torch.float32
            or not bool(torch.isfinite(parameter).all())
            for parameter in self.parameters()
        ):
            raise BoundaryError(STAGE, "declared_device_fp32_finite_parameters_required")


class DeviceStructuredTrainingEngine(StructuredTrainingEngine):
    """Reuse the existing TBPTT/cursor/optimizer validator with explicit device identity."""

    def __init__(
        self,
        dataset: StructuredDataset,
        config: StructuredTrainingConfig,
        *,
        code_scope: str,
        identity: dict[str, Any],
        policy: StructuredDevicePolicy,
    ) -> None:
        self.device_policy = policy
        self.declared_identity = FrozenObject.of(identity)
        if (
            identity.get("engine") != "structured-remote-tbptt-v1"
            or identity.get("config") != asdict(config)
            or identity.get("training_device_policy") != policy.to_dict()
            or identity.get("source_sha256") != dataset.source_sha256
            or identity.get("numerical_projection_sha256") != sha256(numerical_projection(dataset))
        ):
            raise BoundaryError(STAGE, "frozen_remote_numeric_identity_mismatch")
        control = control_from_identity(identity)
        if identity.get("graph_id") != (GRAPH_ID if control is None else control.id) or (
            control is not None
        ) != (code_scope in CONTROL_SCOPES):
            raise BoundaryError(STAGE, "declared_graph_control_scope_mismatch")
        super().__init__(dataset, config, code_scope=code_scope, model_control=control)
        self.checkpoint_schema = CHECKPOINT_SCHEMA

    def _execution_identity(self) -> dict[str, Any]:
        identity = self.declared_identity.value()
        if (
            structured_device_runtime(self.device_policy) != identity["runtime"]
            or sha256(numerical_projection(self.dataset)) != identity["numerical_projection_sha256"]
        ):
            raise BoundaryError(STAGE, "actual_runtime_or_projection_changed")
        return identity

    def _construct_model(self) -> StructuredM2:
        return _TrainingStructuredM2(
            policy=self.device_policy, seed=self.config.seed, model_control=self.model_control
        )

    def checkpoint(self) -> bytes:
        value = decode_checkpoint(super().checkpoint())
        value["device_rng"] = (
            torch.cuda.get_rng_state(0) if self.device_policy.device == "cuda:0" else None
        )
        raw = encode_checkpoint(value)
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise BoundaryError(STAGE, "checkpoint_byte_limit")
        return raw

    def restore(self, raw: bytes) -> None:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_CHECKPOINT_BYTES:
            raise BoundaryError(STAGE, "checkpoint_byte_limit")
        value = decode_checkpoint(raw)
        if "device_rng" not in value or value.get("identity") != self.declared_identity.value():
            raise BoundaryError(STAGE, "same_device_resume_identity_required")
        rng = value.pop("device_rng")
        if self.device_policy.device == "cuda:0":
            if (
                not isinstance(rng, Tensor)
                or rng.device.type != "cpu"
                or rng.dtype != torch.uint8
                or rng.shape != torch.cuda.get_rng_state(0).shape
            ):
                raise BoundaryError(STAGE, "cuda_rng_mismatch")
            torch.Generator(device="cuda:0").set_state(rng)
        elif rng is not None:
            raise BoundaryError(STAGE, "unexpected_cuda_rng")
        super().restore(encode_checkpoint(value))
        # The tensor-tree reader is CPU-only; optimizer.load_state_dict maps moments
        # to their parameter devices and keeps noncapturable AdamW step scalars on CPU.
        self.memory = self.memory.to(self.device_policy.device).detach().clone()
        if rng is not None:
            torch.cuda.set_rng_state(rng, device=0)
        self.model.validate_parameters()


def cpu_model_from_checkpoint(raw: bytes, identity: dict[str, Any]) -> StructuredM2:
    """Independent CPU FP32 export path; never an engine resume or identity rewrite."""
    value = decode_checkpoint(raw)
    if (
        value.get("schema") != CHECKPOINT_SCHEMA
        or value.get("identity") != identity
        or not isinstance(value.get("model"), dict)
    ):
        raise BoundaryError(STAGE, "export_checkpoint_identity_mismatch")
    model = StructuredM2(
        seed=identity["config"]["seed"], model_control=control_from_identity(identity)
    )
    expected = model.state_dict()
    weights = value["model"]
    if set(weights) != set(expected) or any(
        not isinstance(weights[name], Tensor)
        or weights[name].device.type != "cpu"
        or weights[name].dtype != expected[name].dtype
        or weights[name].shape != expected[name].shape
        for name in expected
    ):
        raise BoundaryError(STAGE, "export_weight_inventory_mismatch")
    model.load_state_dict(weights, strict=True)
    model.validate_parameters()
    return model.eval()
