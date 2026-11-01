"""Opt-in learner execution choices, scoped away from exported actor packages.

TF32 flags are process global in Torch. Use this option only in the dedicated
learner process, whose background threads perform IO rather than inference.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import torch

from spireagent.json_boundary import BoundaryError
from stpd.p1.input import V2_INPUT_SPEC

if TYPE_CHECKING:
    from collections.abc import Iterator

    from stpd.p1.model import MStd
    from stpd.p1.tensor_input import ModelFrame, TensorFrame


@dataclass(frozen=True)
class PerformanceConfig:
    packed: bool = False
    tensor_cache: bool = False
    device_cache: bool = False
    reference_device_cache: bool = False
    joint_nodes: bool = False
    async_transfer: bool = False
    precompile_inputs: bool = False
    microbatch_size: int = 8
    inference_microbatch_size: int | None = None
    probe_inference_microbatches: bool = False
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
                self.async_transfer, self.precompile_inputs,
                self.bucket, self.defer_sync, self.tf32, self.probe_inference_microbatches)):
            raise BoundaryError("rl_performance", "invalid_boolean")
        if (type(self.microbatch_size) is not int or not 1 <= self.microbatch_size <= 64
                or any(type(value) is not int or not 1 <= value <= 2**28 for value in (
                    self.scene_attention_area, self.text_attention_area))
                or self.precision not in {"fp32", "bf16"}):
            raise BoundaryError("rl_performance", "invalid_execution_configuration")
        if (self.inference_microbatch_size is not None and
                (type(self.inference_microbatch_size) is not int
                 or not 1 <= self.inference_microbatch_size <= 64)):
            raise BoundaryError("rl_performance", "invalid_inference_microbatch")
        if self.tensor_cache and not self.packed:
            raise BoundaryError("rl_performance", "tensor_cache_requires_packed")
        if self.device_cache and not self.tensor_cache:
            raise BoundaryError("rl_performance", "device_cache_requires_tensor_cache")
        if self.joint_nodes and not self.tensor_cache:
            raise BoundaryError("rl_performance", "joint_nodes_requires_tensor_cache")
        if self.async_transfer and not self.tensor_cache:
            raise BoundaryError("rl_performance", "async_transfer_requires_tensor_cache")
        if self.precompile_inputs and not self.tensor_cache:
            raise BoundaryError("rl_performance", "precompile_inputs_requires_tensor_cache")


def grouped_indices(frames: list[ModelFrame], model: MStd,
                    config: PerformanceConfig | None) -> Iterator[list[int]]:
    """Reorder only the already-selected batch; callers scatter sequence outputs."""
    from stpd.p1.execution import microbatches
    from stpd.p1.tensor_input import TensorFrame

    indices = list(range(len(frames)))
    if config is not None and config.bucket:
        sizes = [frame.entity_count if isinstance(frame, TensorFrame)
                 else len(frame.entities) for frame in frames]
        indices.sort(key=sizes.__getitem__)
    return microbatches(frames, indices, model)


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

    On CUDA an explicit execution config also enforces its TF32 choice. BF16
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
        if device.type == "cuda":
            old_tf32 = torch.backends.cuda.matmul.allow_tf32
            torch.backends.cuda.matmul.allow_tf32 = config.tf32
        with torch.autocast(device_type=device.type, dtype=torch.bfloat16,
                            enabled=config.precision == "bf16"):
            yield
    finally:
        model.execution, reference.execution = previous
        model.nonblocking_inputs, reference.nonblocking_inputs = old_nonblocking
        if old_tf32 is not None:
            torch.backends.cuda.matmul.allow_tf32 = old_tf32



def probe_inference_microbatches(model: MStd, reference: MStd, frames: list[ModelFrame],
                                config: PerformanceConfig,
                                baseline: PerformanceConfig) -> dict[str, object]:
    """Bounded CUDA 8/32/64 forward-only comparison on actual restored frames.

    The historical path supplies same-weight complete scores and raw value heads.
    No optimizer/RNG update, rollout or target preparation occurs here.
    """
    import statistics
    import time

    if model.ids.weight.device.type != "cuda" or not frames:
        raise BoundaryError("rl_performance", "inference_probe_requires_CUDA_and_frames")
    original_training = model.training
    model.eval()

    def forward(execution: PerformanceConfig) -> tuple[list[torch.Tensor], torch.Tensor, list[int]]:
        scores: list[torch.Tensor] = [torch.empty(0)] * len(frames)
        values = torch.empty((len(frames), 2))
        sizes = []
        with prepare_models(model, reference, execution), torch.no_grad():
            for indices in grouped_indices(frames, model, execution):
                output, prediction = model.forward_with_values([frames[i] for i in indices])
                packed = torch.nn.utils.rnn.pad_sequence(output, batch_first=True).cpu()
                predicted = prediction.cpu()
                sizes.append(len(indices))
                for position, index in enumerate(indices):
                    scores[index] = packed[position, :len(frames[index].choices)].clone()
                    values[index] = predicted[position]
        return scores, values, sizes

    rows: list[dict[str, Any]] = []
    try:
        expected_scores, expected_values, _ = forward(baseline)
        for size in (8, 32, 64):
            execution = replace(config, microbatch_size=size)
            try:
                forward(execution)  # per-option warmup is outside the measured windows
                seconds = []
                torch.cuda.reset_peak_memory_stats()
                for _ in range(2):
                    torch.cuda.synchronize()
                    began = time.perf_counter()
                    scores, values, groups = forward(execution)
                    torch.cuda.synchronize()
                    seconds.append(time.perf_counter() - began)
                maximum = max(float((left - right).abs().max())
                              for left, right in zip(scores, expected_scores, strict=True))
                value_error = float((values - expected_values).abs().max())
                argmax_equal = all(int(left.argmax()) == int(right.argmax())
                                   for left, right in zip(scores, expected_scores, strict=True))
                passed = (argmax_equal and torch.allclose(values, expected_values,
                              atol=2e-5, rtol=3e-4)
                          and all(torch.allclose(left, right, atol=2e-5, rtol=3e-4)
                                  for left, right in zip(scores, expected_scores, strict=True)))
                row = {"microbatch_size": size, "seconds": seconds,
                    "median_seconds": statistics.median(seconds),
                    "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                    "actual_microbatch_sizes": groups,
                    "max_logit_abs_error": maximum, "max_raw_value_abs_error": value_error,
                    "complete_catalog_argmax_equal": argmax_equal,
                    "status": "passed" if passed else "numerical_gate_failed"}
            except torch.cuda.OutOfMemoryError:
                row = {"microbatch_size": size, "status": "CUDA_out_of_memory"}
                torch.cuda.empty_cache()
            rows.append(row)
        eligible = [row for row in rows if row["status"] == "passed"]
        if not eligible:
            raise BoundaryError("rl_performance", "no_inference_microbatch_passed")
        best = min(eligible, key=lambda row: float(row["median_seconds"]))
        return {"selected_microbatch_size": best["microbatch_size"],
                "frames": len(frames), "candidates": rows,
                "scope": "actual restored frames; same weights, full catalogs and raw value heads; no-grad",
                "numerical_gate": {"atol": 2e-5, "rtol": 3e-4, "argmax_equal": True},
                "method_changed": False, "training_updates": 0}
    finally:
        model.train(original_training)
