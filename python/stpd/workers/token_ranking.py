"""Stage 1a token engine: bounded BC, deterministic step RNG, safe model/checkpoint codec.

This engine owns computation only. ArtifactStore/Reporter own durability; datasets retain
their existing allocation and permissions. Frozen Qwen is referenced, never checkpointed.
"""
from __future__ import annotations

import hashlib
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
from ..fullrun.light_action_inputs import CANONICAL_SCHEMA as CANONICAL_LIGHT_ACTION_INPUT_SCHEMA
from ..fullrun.light_action_inputs import PUBLIC_SCHEMA as PUBLIC_LIGHT_ACTION_INPUT_SCHEMA
from ..fullrun.light_action_inputs import SCHEMA as LIGHT_ACTION_INPUT_SCHEMA
from ..fullrun.light_action_inputs import LoadedLightActionInputs
from ..fullrun.token_inputs import LoadedTokenInputs
from ..light_action_codec import SPEC_SHA256
from ..models.losses import listwise_rank_loss
from ..models.stage1a import (
    BTokenScorer,
    DSimpleTokenScorer,
    LightActionM0Scorer,
    build_scorer,
    recipe_for,
)
from ..models.token_core import ScratchShape, ScratchTokenCore, TokenCore
from ..qwen.portable_backend import PortableQwenBackend
from ..qwen.readout_backend import FrozenQwenTokenCore, LoRAQwenTokenCore
from ..stage1a_recipes import LIGHT_ACTION_M0_GRAPH
from .checkpoint_codec import decode_checkpoint, encode_checkpoint

CHECKPOINT_SCHEMA = "stpd/stage1a-checkpoint-v1"
LIGHT_ACTION_M0_CHECKPOINT_SCHEMA = "stpd/stage1a-light-action-m0-checkpoint-v1"
LIGHT_ACTION_M0_CONFIG_SCHEMA = "stpd/stage1a-light-action-m0-config-v1"


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
        recipe = recipe_for(self.recipe)
        if recipe.graph == LIGHT_ACTION_M0_GRAPH:
            raise BoundaryError("token_training", "use_explicit_light_action_m0_config")
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
    def text_menu_small_b(cls, **overrides: Any) -> TokenConfig:
        """Explicit scratch B engineering config with the shared-KV branch path enabled."""
        return cls(recipe="stage1a.b.s.v2", dropout=0.0, **overrides)

    @classmethod
    def decode(cls, value: object) -> TokenConfig:
        # Original exports omitted this field and retain the original 8192 budget.
        if isinstance(value, dict) and "max_tokens" not in value:
            value = {**value, "max_tokens": 8192}
        return cls(**object_fields(value, set(cls.__dataclass_fields__), "token_config"))


@dataclass(frozen=True)
class LightActionM0Config:
    """Closed configuration for the explicit stateless M0 backbone comparison."""

    recipe: str = "stage1a.dsimple.light-action.m0.s.v1"
    seed: int = 1701
    steps: int = 10
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    gradient_clip: float = 1.0
    device: str = "cpu"
    max_state_tokens: int = 8192
    max_action_bytes: int = 8192
    public_profile: str | None = None

    def __post_init__(self) -> None:
        recipe = recipe_for(self.recipe)
        if recipe.graph != LIGHT_ACTION_M0_GRAPH:
            raise BoundaryError("token_training", "light_action_m0_recipe_required")
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
        if self.public_profile not in {None, "public_lite", "public_compact"}:
            raise BoundaryError("token_training", "invalid_public_m0_profile")
        cap = 1_000_000 if self.public_profile is not None else 8192
        if (type(self.max_state_tokens) is not int or not 1 <= self.max_state_tokens <= cap
                or type(self.max_action_bytes) is not int
                or not 1 <= self.max_action_bytes <= cap):
            raise BoundaryError("token_training", "invalid_independent_length_limits")

    @classmethod
    def decode(cls, value: object) -> LightActionM0Config:
        if not isinstance(value, dict) or value.get("schema") != LIGHT_ACTION_M0_CONFIG_SCHEMA:
            raise BoundaryError("token_config", "unsupported_light_action_config")
        raw = {key: item for key, item in value.items() if key != "schema"}
        if "public_profile" not in raw:
            raw["public_profile"] = None
        return cls(**object_fields(raw, set(cls.__dataclass_fields__), "light_action_m0_config"))


Stage1aConfig = TokenConfig | LightActionM0Config


def config_payload(config: Stage1aConfig) -> dict[str, Any]:
    if isinstance(config, LightActionM0Config):
        raw = asdict(config)
        # Keep historical M0 config bytes and artifact identities unchanged.
        if raw["public_profile"] is None:
            del raw["public_profile"]
        return {"schema": LIGHT_ACTION_M0_CONFIG_SCHEMA, **raw}
    return asdict(config)


def decode_config(value: object) -> Stage1aConfig:
    if isinstance(value, dict) and value.get("schema") == LIGHT_ACTION_M0_CONFIG_SCHEMA:
        return LightActionM0Config.decode(value)
    return TokenConfig.decode(value)


def _scoring_seed(seed: int) -> int:
    digest = hashlib.sha256(f"stage1a:{LIGHT_ACTION_M0_GRAPH}:scoring:{seed}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**63)


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
    config: Stage1aConfig, vocab_size: int, snapshot: Path | None = None,
    *, state_codec: dict[str, Any] | None = None,
) -> tuple[BTokenScorer | DSimpleTokenScorer | LightActionM0Scorer, dict[str, Any]]:
    if config.device == "mps" and not torch.backends.mps.is_available():
        raise BoundaryError("token_training", "mps_unavailable_no_fallback")
    recipe = recipe_for(config.recipe)
    core: TokenCore
    identity: dict[str, Any]
    with seeded_step(config.seed, config.device):
        if isinstance(config, LightActionM0Config):
            if recipe.graph != LIGHT_ACTION_M0_GRAPH:
                raise BoundaryError("token_training", "light_action_graph_mismatch")
            if recipe.backbone == "s":
                if snapshot is not None:
                    raise BoundaryError("token_training", "scratch_has_no_qwen_dependency")
                shape = ScratchShape(
                    vocab_size, 384, 2, 6, 1536, 0.1, config.max_state_tokens,
                )
                core = ScratchTokenCore(shape)
                identity = {"kind": "scratch", "shape": asdict(shape)}
            else:
                if snapshot is None:
                    raise BoundaryError("token_training", "pinned_snapshot_required")
                backend = PortableQwenBackend(snapshot, device=config.device)
                if recipe.backbone == "pf":
                    core = FrozenQwenTokenCore(backend, max_tokens=config.max_state_tokens)
                    identity = {"kind": "pf", "qwen": backend.identity.__dict__}
                else:
                    core = LoRAQwenTokenCore(backend, max_tokens=config.max_state_tokens)
                    identity = {
                        "kind": "pl", "qwen_base": core.base_fingerprint,
                        "adapter_config": core.adapter_config, "peft_version": core.peft_version,
                        "adapter_tensor_names": list(core.adapter_tensor_names),
                    }
            identity["state_codec"] = state_codec or {}
            identity["core_fingerprint"] = semantic_hash({
                "core": identity,
                "graph": recipe.graph,
                "vocabulary_size": vocab_size,
                "max_state_tokens": config.max_state_tokens,
            })
            model = build_scorer(
                config.recipe, core, max_action_bytes=config.max_action_bytes,
                scoring_seed=_scoring_seed(config.seed),
                public_profile=config.public_profile,
            )
            model.to(config.device)
            return model, identity
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


def model_weights(
    model: nn.Module, *, frozen: bool, adapter_tensor_names: set[str] | None = None,
) -> dict[str, Tensor]:
    return {name: tensor.detach().cpu().contiguous()
            for name, tensor in model.state_dict().items()
            if (not (frozen and name.startswith("core."))
                and (adapter_tensor_names is None or not name.startswith("core.")
                     or name in adapter_tensor_names))}


def restore_weights(
    model: nn.Module, raw: bytes, *, frozen: bool,
    adapter_tensor_names: set[str] | None = None,
    strict_frozen_core: bool = False,
) -> None:
    try:
        weights = load(raw)
        state = model.state_dict()
        expected = model_weights(model, frozen=frozen,
                                 adapter_tensor_names=adapter_tensor_names)
        if set(weights) != set(expected) or any(
            weights[k].shape != v.shape or weights[k].dtype != v.dtype
            or not bool(torch.isfinite(weights[k]).all()) for k, v in expected.items()
        ):
            raise ValueError("weights_inventory_or_values")
        if adapter_tensor_names is None:
            if strict_frozen_core and frozen:
                missing, unexpected = model.load_state_dict(weights, strict=False)
                expected_missing = {name for name in state if name.startswith("core.")}
                if set(missing) != expected_missing or unexpected:
                    raise ValueError("frozen_core_state_inventory")
            else:
                model.load_state_dict(weights, strict=not frozen)
        else:
            if not adapter_tensor_names <= set(expected):
                raise ValueError("adapter_tensor_names_missing_from_weights")
            missing, unexpected = model.load_state_dict(weights, strict=False)
            allowed_missing = set(state) - set(expected)
            if (set(missing) != allowed_missing or unexpected
                    or any(not name.startswith("core.model.base_model.model.")
                           for name in allowed_missing)):
                raise ValueError("base_adapter_state_inventory")
    except Exception as error:
        raise BoundaryError("token_model", "invalid_weights") from error


class TokenRankingEngine:
    def __init__(self, inputs: LoadedTokenInputs | LoadedLightActionInputs, config: Stage1aConfig,
                 *, snapshot: Path | None = None) -> None:
        self.inputs, self.config = inputs, config
        recipe = recipe_for(config.recipe)
        self.frozen = recipe.backbone == "pf"
        info = inputs.manifest.parameters.value()
        self.is_light_action_m0 = isinstance(config, LightActionM0Config)
        self.adapter_tensor_names: set[str] | None = None
        if isinstance(config, LightActionM0Config):
            if not isinstance(inputs, LoadedLightActionInputs):
                raise BoundaryError("token_training", "light_action_dual_input_required")
            state_codec = info.get("state_codec")
            family = "scratch" if recipe_for(config.recipe).backbone == "s" else "pinned-qwen3"
            expected_family = ("train-only-byte-bpe" if family == "scratch"
                               else "pinned-qwen3")
            action_codec = info.get("action_codec")
            input_schema = info.get("schema")
            public = input_schema == PUBLIC_LIGHT_ACTION_INPUT_SCHEMA
            renderer = info.get("source_renderer")
            profile_matches = (
                config.public_profile is not None and public and isinstance(renderer, dict)
                and renderer.get("profile") == config.public_profile
            ) or (config.public_profile is None and not public)
            if (input_schema not in {
                    LIGHT_ACTION_INPUT_SCHEMA, CANONICAL_LIGHT_ACTION_INPUT_SCHEMA,
                    PUBLIC_LIGHT_ACTION_INPUT_SCHEMA,
            }
                    or not profile_matches
                    or info.get("graph") != LIGHT_ACTION_M0_GRAPH
                    or not isinstance(state_codec, dict)
                    or state_codec.get("family") != expected_family
                    or not isinstance(action_codec, dict)
                    or action_codec.get("sha256") != SPEC_SHA256
                    or info.get("max_state_tokens") != config.max_state_tokens
                    or info.get("max_action_bytes") != config.max_action_bytes
                    or state_codec.get("max_tokens") != config.max_state_tokens):
                raise BoundaryError("token_training", "light_action_codec_or_limit_mismatch")
            if state_codec.get("vocab_size") != inputs.state_tokenizer.get_vocab_size():
                raise BoundaryError("token_training", "light_action_state_vocab_mismatch")
            if (max(len(row.state) for row in inputs.rows) > config.max_state_tokens
                    or max(len(action) - 2 for row in inputs.rows for action in row.actions)
                    > config.max_action_bytes):
                raise BoundaryError("token_training", "light_action_input_limit_mismatch")
            state_codec_identity = state_codec
            vocab_size = state_codec["vocab_size"]
        else:
            if isinstance(inputs, LoadedLightActionInputs):
                raise BoundaryError("token_training", "legacy_recipe_rejects_dual_input")
            state_codec_identity = None
            vocab_size = info["vocab_size"]
        if isinstance(config, TokenConfig) and info["backbone"] != (
            "pf" if self.frozen else "s"
        ):
            raise BoundaryError("token_training", "input_backbone_mismatch")
        if isinstance(config, TokenConfig) and info["joint_lengths"]["max"] > config.max_tokens:
            raise BoundaryError("token_training", "increase_configured_token_budget")
        self.model, self.backbone = construct_model(
            config, vocab_size, snapshot, state_codec=state_codec_identity,
        )
        if isinstance(self.model, LightActionM0Scorer) and isinstance(
            self.model.core, LoRAQwenTokenCore,
        ):
            self.adapter_tensor_names = {
                f"core.model.{name}" for name in self.model.core.adapter_tensor_names
            }
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
        identity_payload = {
            "input": inputs.manifest.artifact_id, "config": config_payload(config),
            "plan": self.plan, "backbone": self.backbone,
        }
        if self.is_light_action_m0:
            identity_payload.update({"graph": recipe.graph, "recipe": recipe.recipe_id})
        self.data_identity = semantic_hash(identity_payload)
        self.step = 0

    def _weights(self) -> dict[str, Tensor]:
        return model_weights(self.model, frozen=self.frozen,
                             adapter_tensor_names=self.adapter_tensor_names)

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
        if isinstance(self.model, LightActionM0Scorer) and isinstance(
            self.model.core, LoRAQwenTokenCore,
        ) and any(not parameter.requires_grad and parameter.grad is not None
                  for parameter in self.model.core.model.parameters()):
            raise BoundaryError("token_training", "lora_base_gradient")
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
        return save(self._weights())

    def checkpoint(self) -> bytes:
        return encode_checkpoint({
            "schema": (LIGHT_ACTION_M0_CHECKPOINT_SCHEMA if self.is_light_action_m0
                       else CHECKPOINT_SCHEMA),
            "data_identity": self.data_identity,
            "config": config_payload(self.config), "torch_version": str(torch.__version__),
            "step": self.step, "model": self._weights(),
            "optimizer": self.optimizer.state_dict(),
            "rng_protocol": "seed_plus_completed_steps_v1",
            "cpu_threads": torch.get_num_threads(),
        })

    def restore(self, raw: bytes) -> None:
        state = decode_checkpoint(raw)
        schema = (LIGHT_ACTION_M0_CHECKPOINT_SCHEMA if self.is_light_action_m0
                  else CHECKPOINT_SCHEMA)
        if (set(state) != {"schema", "data_identity", "config", "torch_version", "step",
                           "model", "optimizer", "rng_protocol", "cpu_threads"}
                or state["schema"] != schema
                or state["data_identity"] != self.data_identity
                or state["config"] != config_payload(self.config)
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
        restore_weights(self.model, save(state["model"]), frozen=self.frozen,
                        adapter_tensor_names=self.adapter_tensor_names,
                        strict_frozen_core=self.is_light_action_m0)
        self.optimizer.load_state_dict(optimizer)
        self.step = state["step"]
