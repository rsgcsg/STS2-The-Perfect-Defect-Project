"""Stage 1a token engine: bounded BC, deterministic step RNG, safe model/checkpoint codec.

This engine owns computation only. ArtifactStore/Reporter own durability; datasets retain
their existing allocation and permissions. Frozen Qwen is referenced, never checkpointed.
"""
from __future__ import annotations

import math
import random
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import torch
from safetensors.torch import load, save
from torch import Tensor, nn

from spireagent.json_boundary import BoundaryError, object_fields

from ..canonical import semantic_hash
from ..fullrun.token_inputs import LoadedTokenInputs
from ..models.losses import listwise_rank_loss
from ..models.stage1a import BTokenScorer, DSimpleTokenScorer, build_scorer, recipe_for
from ..models.token_core import ScratchShape, ScratchTokenCore, TokenCore
from ..qwen.portable_backend import PortableQwenBackend
from ..qwen.readout_backend import FrozenQwenTokenCore
from .checkpoint_codec import decode_checkpoint, encode_checkpoint

CHECKPOINT_SCHEMA = "stpd/stage1a-checkpoint-v1"


@dataclass(frozen=True)
class TokenConfig:
    recipe: str = "stage1a.dsimple.s.v1"
    seed: int = 1701
    steps: int = 10
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    gradient_clip: float = 1.0
    device: str = "cpu"
    width: int = 384
    layers: int = 2
    heads: int = 6
    feedforward: int = 1536
    dropout: float = 0.1
    max_tokens: int = 8192

    def __post_init__(self) -> None:
        recipe_for(self.recipe)
        if (type(self.seed) is not int or not 0 <= self.seed < 2**63
                or type(self.steps) is not int or not 1 <= self.steps <= 100000):
            raise BoundaryError("token_training", "invalid_seed_or_steps")
        if self.device not in {"cpu", "mps"}:
            raise BoundaryError("token_training", "local_device_required")
        for name in ("learning_rate", "weight_decay", "gradient_clip"):
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise BoundaryError("token_training", "invalid_optimizer")
        if not self.learning_rate or not self.gradient_clip:
            raise BoundaryError("token_training", "zero_learning_rate_or_clip")
        self.shape(256).validate()
        if self.width > 1024 or self.layers > 8 or self.heads > 16 or self.feedforward > 4096:
            raise BoundaryError("token_training", "engineering_shape_limit")

    def shape(self, vocab_size: int) -> ScratchShape:
        return ScratchShape(vocab_size, self.width, self.layers, self.heads,
                            self.feedforward, self.dropout, self.max_tokens)

    @classmethod
    def decode(cls, value: object) -> TokenConfig:
        # Original exports omitted this field and retain the original 8192 budget.
        if isinstance(value, dict) and "max_tokens" not in value:
            value = {**value, "max_tokens": 8192}
        return cls(**object_fields(value, set(cls.__dataclass_fields__), "token_config"))


@contextmanager
def seeded_step(seed: int, device: str) -> Iterator[None]:
    """Step-local RNG reproduces dropout on resume without changing another caller's RNG."""
    cpu = torch.get_rng_state()
    mps = torch.mps.get_rng_state() if device == "mps" else None
    torch.random.default_generator.manual_seed(seed)
    if device == "mps":
        torch.mps.manual_seed(seed)
    try:
        yield
    finally:
        torch.set_rng_state(cpu)
        if mps is not None:
            torch.mps.set_rng_state(mps)


def construct_model(
    config: TokenConfig, vocab_size: int, snapshot: Path | None = None,
) -> tuple[BTokenScorer | DSimpleTokenScorer, dict[str, Any]]:
    if config.device == "mps" and not torch.backends.mps.is_available():
        raise BoundaryError("token_training", "mps_unavailable_no_fallback")
    recipe = recipe_for(config.recipe)
    core: TokenCore
    identity: dict[str, Any]
    with seeded_step(config.seed, config.device):
        if recipe.backbone == "s":
            if snapshot is not None:
                raise BoundaryError("token_training", "scratch_has_no_qwen_dependency")
            core = ScratchTokenCore(config.shape(vocab_size))
            identity = {"kind": "scratch", "shape": asdict(config.shape(vocab_size))}
        else:
            if snapshot is None:
                raise BoundaryError("token_training", "pinned_snapshot_required")
            backend = PortableQwenBackend(snapshot, device=config.device)
            core = FrozenQwenTokenCore(backend, max_tokens=config.max_tokens)
            identity = {"kind": "pf", "qwen": backend.identity.__dict__}
        initial = core.readout_initial() if isinstance(core, FrozenQwenTokenCore) else None
        model = build_scorer(config.recipe, core,
                             readout_initial=initial if recipe.family == "b" else None)
        model.to(config.device)
    return model, identity


def model_weights(model: nn.Module, *, frozen: bool) -> dict[str, Tensor]:
    return {name: tensor.detach().cpu().contiguous()
            for name, tensor in model.state_dict().items()
            if not (frozen and name.startswith("core."))}


def restore_weights(model: nn.Module, raw: bytes, *, frozen: bool) -> None:
    try:
        weights = load(raw)
        expected = model_weights(model, frozen=frozen)
        if set(weights) != set(expected) or any(
            weights[k].shape != v.shape or weights[k].dtype != v.dtype
            or not bool(torch.isfinite(weights[k]).all()) for k, v in expected.items()
        ):
            raise ValueError("weights_inventory_or_values")
        model.load_state_dict(weights, strict=not frozen)
    except Exception as error:
        raise BoundaryError("token_model", "invalid_weights") from error


class TokenRankingEngine:
    def __init__(self, inputs: LoadedTokenInputs, config: TokenConfig,
                 *, snapshot: Path | None = None) -> None:
        self.inputs, self.config = inputs, config
        self.frozen = recipe_for(config.recipe).backbone == "pf"
        info = inputs.manifest.parameters.value()
        if info["backbone"] != ("pf" if self.frozen else "s"):
            raise BoundaryError("token_training", "input_backbone_mismatch")
        if info["joint_lengths"]["max"] > config.max_tokens:
            raise BoundaryError("token_training", "increase_configured_token_budget")
        self.model, self.backbone = construct_model(config, info["vocab_size"], snapshot)
        self.parameters = [p for p in self.model.parameters() if p.requires_grad]
        self.optimizer = torch.optim.AdamW(self.parameters, lr=config.learning_rate,
                                          weight_decay=config.weight_decay)
        train = [i for i, sample in enumerate(inputs.samples) if sample.split == "train"]
        if not train:
            raise BoundaryError("token_training", "empty_train")
        self.plan: list[int] = []
        for epoch in range((config.steps + len(train) - 1) // len(train)):
            order = list(train)
            random.Random(f"stage1a:{config.seed}:{epoch}").shuffle(order)
            self.plan.extend(order)
        self.plan = self.plan[:config.steps]
        self.data_identity = semantic_hash({
            "input": inputs.manifest.artifact_id, "config": asdict(config),
            "plan": self.plan, "backbone": self.backbone,
        })
        self.step = 0

    def _scores(self, index: int) -> Tensor:
        row = self.inputs.rows[index]
        state = torch.tensor(row.state, dtype=torch.long, device=self.config.device)
        actions = tuple(torch.tensor(a, dtype=torch.long, device=self.config.device)
                        for a in row.actions)
        return cast(Tensor, self.model(state, actions))

    def advance(self) -> float:
        if self.step >= self.config.steps:
            raise BoundaryError("token_training", "exhausted")
        self.model.train()
        self.optimizer.zero_grad(set_to_none=True)
        index = self.plan[self.step]
        # Training randomness is explicitly a function of seed and completed updates.
        seed = (self.config.seed + self.step) % (2**63)
        with seeded_step(seed, self.config.device):
            scores = self._scores(index)
            loss = listwise_rank_loss(scores, self.inputs.samples[index].chosen_index)
            if not bool(torch.isfinite(loss)):
                raise BoundaryError("token_training", "non_finite_loss")
            loss.backward()
        torch.nn.utils.clip_grad_norm_(self.parameters, self.config.gradient_clip,
                                       error_if_nonfinite=True)
        if self.frozen and any(p.grad is not None for p in self.model.core.parameters()):
            raise BoundaryError("token_training", "frozen_gradient")
        self.optimizer.step()
        if any(not bool(torch.isfinite(p).all()) for p in self.parameters):
            raise BoundaryError("token_training", "non_finite_parameter")
        self.step += 1
        return float(loss.detach())

    def scores(self, index: int) -> tuple[float, ...]:
        self.model.eval()
        with torch.no_grad():
            values = self._scores(index)
        if not bool(torch.isfinite(values).all()):
            raise BoundaryError("token_model", "non_finite_scores")
        return tuple(float(v) for v in values.cpu().tolist())

    def model_bytes(self) -> bytes:
        return save(model_weights(self.model, frozen=self.frozen))

    def checkpoint(self) -> bytes:
        return encode_checkpoint({
            "schema": CHECKPOINT_SCHEMA, "data_identity": self.data_identity,
            "config": asdict(self.config), "torch_version": str(torch.__version__),
            "step": self.step, "model": model_weights(self.model, frozen=self.frozen),
            "optimizer": self.optimizer.state_dict(),
            "rng_protocol": "seed_plus_completed_steps_v1",
            "cpu_threads": torch.get_num_threads(),
        })

    def restore(self, raw: bytes) -> None:
        state = decode_checkpoint(raw)
        if (set(state) != {"schema", "data_identity", "config", "torch_version", "step",
                           "model", "optimizer", "rng_protocol", "cpu_threads"}
                or state["schema"] != CHECKPOINT_SCHEMA
                or state["data_identity"] != self.data_identity
                or state["config"] != asdict(self.config)
                or state["torch_version"] != str(torch.__version__)
                or state["rng_protocol"] != "seed_plus_completed_steps_v1"
                or state["cpu_threads"] != torch.get_num_threads()
                or type(state["step"]) is not int or not 0 <= state["step"] <= self.config.steps):
            raise BoundaryError("token_checkpoint", "resume_identity_mismatch")
        optimizer = state["optimizer"]
        if (not isinstance(optimizer, dict) or set(optimizer) != {"state", "param_groups"}
                or optimizer["param_groups"] != self.optimizer.state_dict()["param_groups"]
                or not isinstance(optimizer["state"], dict)):
            raise BoundaryError("token_checkpoint", "optimizer_config_mismatch")
        expected_indices = set(range(len(self.parameters))) if state["step"] else set()
        if set(optimizer["state"]) != expected_indices:
            raise BoundaryError("token_checkpoint", "optimizer_state_inventory")
        for index, values in optimizer["state"].items():
            parameter = self.parameters[index]
            if (not isinstance(values, dict) or set(values) != {"step", "exp_avg", "exp_avg_sq"}
                    or not isinstance(values["step"], Tensor) or values["step"].numel() != 1
                    or float(values["step"]) != state["step"]
                    or any(not isinstance(values[k], Tensor)
                           or values[k].shape != parameter.shape
                           or values[k].dtype != parameter.dtype
                           for k in ("exp_avg", "exp_avg_sq"))):
                raise BoundaryError("token_checkpoint", "optimizer_state_mismatch")
        restore_weights(self.model, save(state["model"]), frozen=self.frozen)
        self.optimizer.load_state_dict(optimizer)
        self.step = state["step"]
