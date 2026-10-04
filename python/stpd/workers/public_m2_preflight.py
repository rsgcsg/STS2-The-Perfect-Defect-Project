"""CPU-only typed M2 preparation and remote artifact structural checks.

This is a transport boundary, not part of the numerical engine digest. A CUDA
RNG state is checked for typed integrity here and restored only on its GPU.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from types import SimpleNamespace
from typing import Any, cast

import torch
from torch import Tensor

from spireagent.json_boundary import BoundaryError, json_bytes

from .checkpoint_codec import decode_checkpoint, encode_checkpoint
from .public_m2_engine import (
    PUBLIC_M2_CHECKPOINT_SCHEMA,
    PublicM2Engine,
    PublicM2EngineChain,
    PublicM2EngineConfig,
    _chain_values,
    _construct,
    _implementation_digest,
    _tensor_digest,
    load_public_m2_weights,
)


@dataclass(frozen=True)
class PublicM2Preflight:
    config: PublicM2EngineConfig
    input_digest: str
    runtime: dict[str, Any]
    train_chains: tuple[PublicM2EngineChain, ...]
    dev_chains: tuple[PublicM2EngineChain, ...]
    parameter_names: tuple[str, ...]


def preflight_public_m2_engine(
    train_chains: tuple[PublicM2EngineChain, ...],
    dev_chains: tuple[PublicM2EngineChain, ...],
    config: PublicM2EngineConfig,
) -> PublicM2Preflight:
    """Validate complete input and graph on CPU without requiring CUDA locally."""
    if not isinstance(config, PublicM2EngineConfig):
        raise BoundaryError("public_m2_engine", "typed_config_required")
    config.validate(require_device_available=False)
    if torch.get_default_dtype() != torch.float32:
        raise BoundaryError("public_m2_engine", "float32_default_dtype_required")
    model = _construct(config, device=torch.device("cpu"))
    checker = cast(Any, SimpleNamespace(config=config, model=model))
    train = PublicM2Engine._preflight_chains(checker, train_chains, "train")
    dev = PublicM2Engine._preflight_chains(checker, dev_chains, "dev")
    if {chain.chain_id for chain in train} & {chain.chain_id for chain in dev}:
        raise BoundaryError("public_m2_engine", "train_dev_chain_overlap")
    if (sum(len(chain.steps) for chain in (*train, *dev)) > config.max_total_steps
            or sum(len(step.page) + sum(map(len, step.byte_actions))
                   for chain in (*train, *dev) for step in chain.steps)
            > config.max_total_input_tokens):
        raise BoundaryError("public_m2_engine", "total_input_limit_exceeded")
    input_digest = hashlib.sha256(json_bytes({
        "train": _chain_values(train), "dev": _chain_values(dev),
    })).hexdigest()
    names = tuple(name for name, parameter in model.named_parameters()
                  if parameter.requires_grad)
    if not names or len(names) != sum(parameter.requires_grad for parameter in model.parameters()):
        raise BoundaryError("public_m2_engine", "trainable_parameter_inventory")
    return PublicM2Preflight(
        config, input_digest, {"implementation_sha256": _implementation_digest()},
        train, dev, names,
    )


def validate_public_m2_checkpoint(
    raw: bytes, preflight: PublicM2Preflight, *, expected_runtime: dict[str, Any],
) -> dict[str, Any]:
    """Validate exact remote identity and all CPU-checkable restore structure.

    ``expected_runtime`` is the separately pinned attempt runtime, not a local
    CPU runtime substituted for a remote host. GPU RNG viability remains an
    exact-device restore check.
    """
    value = decode_checkpoint(raw)
    config = preflight.config
    if (set(value) != {
            "schema", "config", "input_digest", "runtime", "completed_epochs",
            "chain_index", "window_cursor", "memory", "memory_digest", "rng_digest",
            "label_count", "optimizer_updates", "loss_sum", "model", "model_digest",
            "optimizer", "optimizer_digest", "parameter_names", "cpu_rng", "cuda_rng",
        } or value["schema"] != PUBLIC_M2_CHECKPOINT_SCHEMA
            or value["config"] != asdict(config)
            or value["input_digest"] != preflight.input_digest
            or value["runtime"] != expected_runtime
            or not isinstance(expected_runtime, dict)
            or set(expected_runtime) != {
                "torch", "python", "platform", "default_dtype", "cpu_threads",
                "implementation_sha256",
            }
            or any(type(expected_runtime[key]) is not str or not expected_runtime[key]
                   for key in ("torch", "python", "platform"))
            or expected_runtime["default_dtype"] != "torch.float32"
            or type(expected_runtime["cpu_threads"]) is not int
            or expected_runtime["cpu_threads"] < 1
            or expected_runtime.get("implementation_sha256")
            != preflight.runtime["implementation_sha256"]
            or value["parameter_names"] != preflight.parameter_names):
        raise BoundaryError("public_m2_checkpoint", "identity_mismatch")
    cuda_rng = value["cuda_rng"]
    cpu_rng = value["cpu_rng"]
    if (not isinstance(cpu_rng, Tensor) or cpu_rng.dtype != torch.uint8
            or cpu_rng.ndim != 1
            or config.device == "cpu" and cuda_rng is not None
            or config.device != "cpu" and (
                not isinstance(cuda_rng, Tensor) or cuda_rng.dtype != torch.uint8
                or cuda_rng.ndim != 1 or cuda_rng.numel() == 0)):
        raise BoundaryError("public_m2_checkpoint", "rng_mismatch")
    if value["rng_digest"] != _tensor_digest({
        "cpu_rng": cpu_rng, **({} if cuda_rng is None else {"cuda_rng": cuda_rng}),
    }):
        raise BoundaryError("public_m2_checkpoint", "rng_mismatch")
    # The original M2 restore is the source of truth for shapes, optimizer
    # inventory, progress, memory, digests, and CPU RNG state. Only the checked
    # header/remote CUDA RNG are translated for this disposable CPU validator.
    cpu = PublicM2Engine(
        preflight.train_chains, preflight.dev_chains,
        replace(config, device="cpu"),
    )
    checked = dict(value)
    checked["config"] = asdict(cpu.config)
    checked["runtime"] = cpu.runtime
    checked["cuda_rng"] = None
    checked["rng_digest"] = _tensor_digest({"cpu_rng": cpu_rng})
    cpu.restore(encode_checkpoint(checked))
    if (any(not bool(torch.isfinite(tensor).all()) for tensor in value["model"].values())
            or any(not bool(torch.isfinite(tensor).all()) for state in
                   value["optimizer"]["state"].values() for tensor in state.values())):
        raise BoundaryError("public_m2_checkpoint", "nonfinite_tensor")
    return value


def validate_public_m2_export(
    raw: bytes, preflight: PublicM2Preflight, *, completed_epochs: int,
    checkpoint_raw: bytes, expected_runtime: dict[str, Any],
) -> dict[str, Any]:
    """Validate a stage export and its checkpoint/update binding on CPU."""
    checkpoint = validate_public_m2_checkpoint(
        checkpoint_raw, preflight, expected_runtime=expected_runtime,
    )
    value = decode_checkpoint(raw)
    windows = sum(
        (len(chain.steps) + preflight.config.window_steps - 1)
        // preflight.config.window_steps for chain in preflight.train_chains
    )
    if (type(completed_epochs) is not int
            or checkpoint["completed_epochs"] != completed_epochs
            or checkpoint["chain_index"] != 0 or checkpoint["window_cursor"] != 0
            or checkpoint["optimizer_updates"] != completed_epochs * windows
            or value.get("checkpoint_digest") != hashlib.sha256(checkpoint_raw).hexdigest()
            or value.get("optimizer_updates") != checkpoint["optimizer_updates"]
            or value.get("weights_digest") != checkpoint["model_digest"]):
        raise BoundaryError("public_m2_export", "checkpoint_mismatch")
    model = load_public_m2_weights(
        raw, preflight.config, input_digest=preflight.input_digest,
        completed_epochs=completed_epochs,
    )
    if any(not bool(torch.isfinite(tensor).all()) for tensor in model.state_dict().values()):
        raise BoundaryError("public_m2_export", "nonfinite_weights")
    return value
