"""Opt-in learner execution choices, scoped away from exported actor packages.

TF32 flags are process global in Torch. Use this option only in the dedicated
learner process, whose background threads perform IO rather than inference.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import torch

from spireagent.json_boundary import BoundaryError
from stpd.p1.input import V2_INPUT_SPEC

if TYPE_CHECKING:
    from collections.abc import Iterator

    from stpd.p1.model import MStd
    from stpd.p1.tensor_input import TensorFrame


@dataclass(frozen=True)
class PerformanceConfig:
    packed: bool = False
    tensor_cache: bool = False
    device_cache: bool = False
    reference_device_cache: bool = False
    joint_nodes: bool = False
    async_transfer: bool = False
    microbatch_size: int = 8
    scene_attention_area: int = 262144
    text_attention_area: int = 262144
    bucket: bool = False
    defer_sync: bool = False
    precision: str = "fp32"
    tf32: bool = False

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in (
                self.packed, self.tensor_cache, self.device_cache,
                self.reference_device_cache, self.joint_nodes,
                self.async_transfer, self.bucket, self.defer_sync, self.tf32)):
            raise BoundaryError("rl_performance", "invalid_boolean")
        if (type(self.microbatch_size) is not int or not 1 <= self.microbatch_size <= 64
                or any(type(value) is not int or not 1 <= value <= 2**28 for value in (
                    self.scene_attention_area, self.text_attention_area))
                or self.precision not in {"fp32", "bf16"}):
            raise BoundaryError("rl_performance", "invalid_execution_configuration")
        if self.tensor_cache and not self.packed:
            raise BoundaryError("rl_performance", "tensor_cache_requires_packed")
        if self.device_cache and not self.tensor_cache:
            raise BoundaryError("rl_performance", "device_cache_requires_tensor_cache")
        if self.joint_nodes and not self.tensor_cache:
            raise BoundaryError("rl_performance", "joint_nodes_requires_tensor_cache")
        if self.async_transfer and not self.tensor_cache:
            raise BoundaryError("rl_performance", "async_transfer_requires_tensor_cache")


def cache_on_device(frame: TensorFrame, device: torch.device) -> TensorFrame:
    """Move immutable input metadata once; no weights, dropout or padding are cached.

    Cold use pays each metadata transfer. Warm use shares these allocations
    between learner/reference forwards until the ordinary sample cache expires.
    """
    from stpd.p1.tensor_input import Reduction, TensorFrame

    return TensorFrame(
        {key: value.to(device) for key, value in frame.tensors.items()},
        {key: Reduction(tuple((width, count, indices.to(device))
                              for width, count, indices in value.buckets), value.inverse.to(device))
         for key, value in frame.reductions.items()}, frame.choices, frame.entity_count,
        host=frame.host or frame)


def frame_storage_bytes(frame: TensorFrame) -> int:
    """Logical metadata bytes for benchmark capacity accounting (not CUDA allocator overhead)."""
    values = list(frame.tensors.values())
    for reduction in frame.reductions.values():
        values.extend(indices for _, _, indices in reduction.buckets)
        values.append(reduction.inverse)
    return sum(value.numel() * value.element_size() for value in values)


@contextmanager
def prepare_models(model: MStd, reference: MStd,
                   config: PerformanceConfig) -> Iterator[None]:
    """Select training execution temporarily; package and reference identity survive.

    No TF32 global is read or written on the default path. A selected BF16 path
    requires CUDA support; it never silently falls back to another precision.
    """
    device = model.ids.weight.device
    if config.precision == "bf16" and (
            device.type != "cuda" or not torch.cuda.is_bf16_supported()):
        raise BoundaryError("rl_performance", "bf16_requires_supported_cuda")
    if config.tf32 and device.type != "cuda":
        raise BoundaryError("rl_performance", "tf32_requires_cuda")
    if config.tensor_cache and model.vocabulary.get("input_spec") != V2_INPUT_SPEC:
        raise BoundaryError("rl_performance", "tensor_cache_requires_v2")
    if config.async_transfer and device.type not in {"cpu", "cuda"}:
        raise BoundaryError("rl_performance", "async_transfer_requires_cpu_or_cuda")
    previous = model.execution, reference.execution
    old_nonblocking = model.nonblocking_inputs, reference.nonblocking_inputs
    old_tf32 = None
    try:
        for member in (model, reference):
            # Immutable pageable host tensors, H2D only; consumers use the same
            # CUDA stream. No pinned-memory mutation or D2H async reads occur.
            member.nonblocking_inputs = config.async_transfer
            member.execution = replace(
                member.execution,
                implementation=("experimental-joint-nodes-v1" if config.joint_nodes else
                                "experimental-packed-v1" if config.packed
                                else member.execution.implementation),
                microbatch_size=config.microbatch_size,
                scene_attention_area=config.scene_attention_area,
                text_attention_area=config.text_attention_area)
        if config.tf32:
            old_tf32 = torch.backends.cuda.matmul.allow_tf32
            torch.backends.cuda.matmul.allow_tf32 = True
        with torch.autocast(device_type=device.type, dtype=torch.bfloat16,
                            enabled=config.precision == "bf16"):
            yield
    finally:
        model.execution, reference.execution = previous
        model.nonblocking_inputs, reference.nonblocking_inputs = old_nonblocking
        if old_tf32 is not None:
            torch.backends.cuda.matmul.allow_tf32 = old_tf32
