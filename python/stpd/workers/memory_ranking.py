"""Experimental CPU scratch M2 episode engine; no data admission or runtime recipe.

The caller owns source eligibility, the fixed episode order, tokenizer identity,
and the truth of optional previous actual actions and public feedback. Only
whole-episode boundaries can be checkpointed. A failed episode poisons this
instance because TBPTT may already have changed parameters.
"""

from __future__ import annotations

import hashlib
import math
import platform
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import torch
from torch import Tensor

from spireagent.json_boundary import BoundaryError, json_bytes

from ..models.dsimple_memory import ExperimentalDSimpleM2
from ..models.dsimple_sequence_training import (
    MAX_LEARN_STEPS,
    MAX_WINDOW_INPUT_TOKENS,
    MemorySequenceEpisode,
    MemorySequenceStep,
    _episode_chunk_plan,
    _validate_step,
    train_memory_episode,
)
from ..models.token_core import ScratchShape, ScratchTokenCore
from .checkpoint_codec import decode_checkpoint, encode_checkpoint

CHECKPOINT_SCHEMA = "stpd/experimental-m2-checkpoint-v1"
EXPORT_SCHEMA = "stpd/experimental-m2-weights-v1"


@dataclass(frozen=True)
class MemoryConfig:
    vocab_size: int
    episode_count: int
    slots: int = 1
    reset_each_step: bool = False
    gated: bool = False
    seed: int = 1701
    width: int = 8
    layers: int = 1
    heads: int = 2
    feedforward: int = 16
    dropout: float = 0.0
    max_tokens: int = 256
    learning_rate: float = 0.001
    weight_decay: float = 0.0
    gradient_clip: float = 1.0
    cpu_threads: int = 2
    max_total_input_tokens: int = 1_000_000
    max_episode_observations: int = 1024
    max_episode_input_tokens: int = 262_144
    max_chunk_steps: int = 32
    max_chunk_input_tokens: int = 65_536
    max_actions_per_step: int = 256

    def __post_init__(self) -> None:
        integers = (self.vocab_size, self.episode_count, self.seed, self.width,
                    self.layers, self.heads, self.feedforward, self.max_tokens,
                    self.cpu_threads, self.max_total_input_tokens,
                    self.max_episode_observations, self.max_episode_input_tokens,
                    self.max_chunk_steps, self.max_chunk_input_tokens,
                    self.max_actions_per_step)
        if (any(type(value) is not int for value in integers)
                or not 1 <= self.vocab_size <= 65_536
                or not 1 <= self.episode_count <= 1024
                or not 0 <= self.seed < 2**63
                or self.slots not in (1, 8) or type(self.slots) is not int
                or type(self.reset_each_step) is not bool or type(self.gated) is not bool
                or not 1 <= self.width <= 1024 or not 1 <= self.layers <= 8
                or not 1 <= self.heads <= 16 or not 1 <= self.feedforward <= 4096
                or not 2 * self.slots + 1 <= self.max_tokens <= 32_768
                or not 1 <= self.cpu_threads <= 16
                or not 1 <= self.max_total_input_tokens <= 16_000_000
                or not 1 <= self.max_episode_observations <= 16_000
                or not 1 <= self.max_episode_input_tokens <= self.max_total_input_tokens
                or not 1 <= self.max_chunk_steps <= MAX_LEARN_STEPS
                or not 1 <= self.max_chunk_input_tokens <= MAX_WINDOW_INPUT_TOKENS
                or not 1 <= self.max_actions_per_step <= 16_384):
            raise BoundaryError("memory_training", "invalid_config_limits")
        for value in (self.learning_rate, self.weight_decay, self.gradient_clip,
                      self.dropout):
            if type(value) not in {int, float} or not math.isfinite(value):
                raise BoundaryError("memory_training", "invalid_config_float")
        if (self.learning_rate <= 0 or self.weight_decay < 0
                or self.gradient_clip <= 0 or not 0 <= self.dropout < 1):
            raise BoundaryError("memory_training", "invalid_config_float")
        self.shape().validate()

    def shape(self) -> ScratchShape:
        return ScratchShape(self.vocab_size, self.width, self.layers, self.heads,
                            self.feedforward, self.dropout, self.max_tokens)


@dataclass(frozen=True)
class MemoryTrainingInput:
    source_id: str
    tokenizer_sha256: str
    episodes: tuple[MemorySequenceEpisode, ...]


@contextmanager
def _seeded_cpu(seed: int) -> Iterator[None]:
    prior = torch.get_rng_state()
    torch.random.default_generator.manual_seed(seed)
    try:
        yield
    finally:
        torch.set_rng_state(prior)


def _runtime_identity() -> dict[str, Any]:
    module_root = Path(__file__).resolve().parents[1]
    sources = ("workers/memory_ranking.py", "workers/checkpoint_codec.py",
               "models/dsimple_memory.py", "models/dsimple_sequence_training.py",
               "models/token_core.py", "models/losses.py")
    implementation = hashlib.sha256()
    for name in sources:
        implementation.update(name.encode("utf-8"))
        implementation.update((module_root / name).read_bytes())
    return {
        "torch": str(torch.__version__), "python": platform.python_version(),
        "system": platform.system(), "machine": platform.machine(),
        "threads": torch.get_num_threads(), "interop_threads": torch.get_num_interop_threads(),
        "deterministic": torch.are_deterministic_algorithms_enabled(),
        "mkldnn": torch.backends.mkldnn.enabled,
        "implementation_sha256": implementation.hexdigest(),
    }


def _construct(config: MemoryConfig) -> ExperimentalDSimpleM2:
    with _seeded_cpu(config.seed):
        return ExperimentalDSimpleM2(
            ScratchTokenCore(config.shape()), slots=config.slots,
            reset_each_step=config.reset_each_step, gated=config.gated,
        )


def _weights(model: ExperimentalDSimpleM2) -> dict[str, Tensor]:
    return {name: value.detach().cpu().contiguous().clone()
            for name, value in model.state_dict().items()}


def _tensor_digest(values: Mapping[str, Tensor]) -> str:
    digest = hashlib.sha256()
    for name in sorted(values):
        value = values[name].detach().cpu().contiguous()
        digest.update(json_bytes([name, str(value.dtype), list(value.shape)]))
        digest.update(value.numpy().tobytes())
    return digest.hexdigest()


def _validated_weights(model: ExperimentalDSimpleM2, value: object) -> dict[str, Tensor]:
    expected = model.state_dict()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise BoundaryError("memory_checkpoint", "model_inventory_mismatch")
    if any(not isinstance(value[name], Tensor)
           or value[name].shape != tensor.shape or value[name].dtype != tensor.dtype
           or not bool(torch.isfinite(value[name]).all())
           for name, tensor in expected.items()):
        raise BoundaryError("memory_checkpoint", "model_tensor_mismatch")
    return value


def _clone_token(value: Tensor | None) -> Tensor | None:
    if value is None:
        return None
    if not isinstance(value, Tensor) or value.device.type != "cpu":
        raise BoundaryError("memory_training", "cpu_tokens_required")
    return value.detach().clone().contiguous()


def _clone_required(value: Tensor) -> Tensor:
    result = _clone_token(value)
    if result is None:
        raise BoundaryError("memory_training", "required_tokens_missing")
    return result


def _input_digest(source_id: str, tokenizer_sha256: str,
                  episodes: tuple[MemorySequenceEpisode, ...]) -> str:
    digest = hashlib.sha256()
    digest.update(json_bytes([source_id, tokenizer_sha256, len(episodes)]))
    for episode in episodes:
        digest.update(json_bytes([episode.episode_id, len(episode.steps)]))
        for step in episode.steps:
            digest.update(json_bytes([
                step.episode_id, step.position, step.action_keys,
                step.label_key, step.reset_before,
            ]))
            for token in (step.page, *step.actions,
                          step.previous_actual_action, step.public_feedback):
                if token is None:
                    digest.update(b"N")
                else:
                    digest.update(b"T")
                    digest.update(json_bytes([str(token.dtype), list(token.shape)]))
                    digest.update(token.contiguous().numpy().tobytes())
    return digest.hexdigest()


def _copy_input(source: MemoryTrainingInput, config: MemoryConfig,
                model: ExperimentalDSimpleM2) -> tuple[MemorySequenceEpisode, ...]:
    if (not isinstance(source, MemoryTrainingInput)
            or not isinstance(source.source_id, str) or not source.source_id
            or not isinstance(source.tokenizer_sha256, str)
            or len(source.tokenizer_sha256) != 64
            or any(char not in "0123456789abcdef" for char in source.tokenizer_sha256)
            or not isinstance(source.episodes, tuple)
            or len(source.episodes) != config.episode_count):
        raise BoundaryError("memory_training", "input_identity_or_count")
    copied: list[MemorySequenceEpisode] = []
    total_tokens = 0
    seen_ids: set[str] = set()
    for episode in source.episodes:
        if (not isinstance(episode, MemorySequenceEpisode)
                or not isinstance(episode.episode_id, str) or not episode.episode_id
                or episode.episode_id in seen_ids or not isinstance(episode.steps, tuple)
                or not 1 <= len(episode.steps) <= config.max_episode_observations):
            raise BoundaryError("memory_training", "invalid_episode_identity_or_size")
        seen_ids.add(episode.episode_id)
        steps: list[MemorySequenceStep] = []
        episode_tokens = 0
        labels = 0
        for position, step in enumerate(episode.steps):
            if not isinstance(step, MemorySequenceStep):
                raise BoundaryError("memory_training", "invalid_episode_step")
            if not isinstance(step.action_keys, tuple) or not isinstance(step.actions, tuple):
                raise BoundaryError("memory_training", "invalid_episode_catalog")
            if len(step.action_keys) > config.max_actions_per_step:
                raise BoundaryError("memory_training", "candidate_limit")
            clone = replace(
                step, page=_clone_required(step.page),
                actions=tuple(_clone_required(action) for action in step.actions),
                previous_actual_action=_clone_token(step.previous_actual_action),
                public_feedback=_clone_token(step.public_feedback),
            )
            try:
                step_tokens = _validate_step(model, clone, episode.episode_id, position)
            except (ValueError, TypeError, AttributeError) as error:
                raise BoundaryError("memory_training", "invalid_episode_step") from error
            if step_tokens > config.max_chunk_input_tokens:
                raise BoundaryError("memory_training", "chunk_input_limit")
            episode_tokens += step_tokens
            total_tokens += step_tokens
            labels += int(clone.label_key is not None)
            if (episode_tokens > config.max_episode_input_tokens
                    or total_tokens > config.max_total_input_tokens):
                raise BoundaryError("memory_training", "total_input_limit")
            steps.append(clone)
        if not labels:
            raise BoundaryError("memory_training", "episode_without_label")
        copied.append(MemorySequenceEpisode(episode.episode_id, tuple(steps)))
    return tuple(copied)


def _optimizer_digest(state: Mapping[str, Any], names: tuple[str, ...]) -> str:
    tensors: dict[str, Tensor] = {}
    for index, values in state["state"].items():
        for key in ("step", "exp_avg", "exp_avg_sq"):
            tensors[f"{names[index]}:{key}"] = values[key]
    return _tensor_digest(tensors)


class MemoryRankingEngine:
    def __init__(self, source: MemoryTrainingInput, config: MemoryConfig) -> None:
        if torch.get_num_threads() != config.cpu_threads:
            raise BoundaryError("memory_training", "cpu_thread_identity_mismatch")
        self.config = config
        self.model = _construct(config)
        self._episodes = _copy_input(source, config, self.model)
        self.source_id = source.source_id
        self.tokenizer_sha256 = source.tokenizer_sha256
        self.input_digest = _input_digest(self.source_id, self.tokenizer_sha256, self._episodes)
        self.parameters = [parameter for parameter in self.model.parameters()
                           if parameter.requires_grad]
        self.parameter_names = tuple(name for name, parameter in self.model.named_parameters()
                                     if parameter.requires_grad)
        self.optimizer = torch.optim.AdamW(
            self.parameters, lr=config.learning_rate, weight_decay=config.weight_decay,
        )
        self.runtime = _runtime_identity()
        self.next_episode = 0
        self._failed = False

    def _assert_live(self) -> None:
        if self._failed:
            raise BoundaryError("memory_training", "failed_engine_requires_new_restore")
        try:
            unchanged = (_runtime_identity() == self.runtime
                         and _input_digest(self.source_id, self.tokenizer_sha256,
                                           self._episodes) == self.input_digest)
        except Exception:
            unchanged = False
        if not unchanged:
            self._failed = True
            raise BoundaryError("memory_training", "runtime_or_input_changed")

    def advance(self) -> float:
        self._assert_live()
        if self.next_episode >= self.config.episode_count:
            raise BoundaryError("memory_training", "exhausted")
        try:
            with _seeded_cpu((self.config.seed + self.next_episode + 1) % (2**63)):
                loss = train_memory_episode(
                    self.model, self.optimizer, self._episodes[self.next_episode],
                    max_observations=self.config.max_episode_observations,
                    max_input_tokens=self.config.max_episode_input_tokens,
                    max_chunk_steps=self.config.max_chunk_steps,
                    max_chunk_input_tokens=self.config.max_chunk_input_tokens,
                    gradient_clip=self.config.gradient_clip,
                )
        except Exception:
            self._failed = True
            raise
        self.next_episode += 1
        return loss

    def _optimizer_state(self) -> tuple[dict[str, Any], tuple[str, ...]]:
        state = self.optimizer.state_dict()
        active = tuple(self.parameter_names[index] for index in sorted(state["state"]))
        return state, active

    def _completed_update_context(self, completed: int) -> tuple[int, bool, bool]:
        updates = 0
        possible_previous = False
        possible_feedback = False
        for episode in self._episodes[:completed]:
            sizes = tuple(_validate_step(self.model, step, episode.episode_id, position)
                          for position, step in enumerate(episode.steps))
            for start, end in _episode_chunk_plan(
                sizes, self.config.max_chunk_steps, self.config.max_chunk_input_tokens,
            ):
                labeled = [position for position in range(start, end)
                           if episode.steps[position].label_key is not None]
                if not labeled:
                    continue
                updates += 1
                positions = (labeled if self.config.reset_each_step
                             else range(start, labeled[-1] + 1))
                possible_previous |= any(
                    episode.steps[position].previous_actual_action is not None
                    for position in positions
                )
                possible_feedback |= any(
                    episode.steps[position].public_feedback is not None
                    for position in positions
                )
        return updates, possible_previous, possible_feedback

    def checkpoint(self) -> bytes:
        self._assert_live()
        weights = _weights(self.model)
        optimizer, active = self._optimizer_state()
        return encode_checkpoint({
            "schema": CHECKPOINT_SCHEMA, "config": asdict(self.config),
            "source_id": self.source_id, "tokenizer_sha256": self.tokenizer_sha256,
            "input_digest": self.input_digest, "runtime": self.runtime,
            "next_episode": self.next_episode, "model": weights,
            "model_digest": _tensor_digest(weights), "optimizer": optimizer,
            "parameter_names": self.parameter_names, "active_parameter_names": active,
            "optimizer_digest": _optimizer_digest(optimizer, self.parameter_names),
        })

    def restore(self, raw: bytes) -> None:
        self._assert_live()
        try:
            value = decode_checkpoint(raw)
            if (set(value) != {"schema", "config", "source_id", "tokenizer_sha256",
                               "input_digest", "runtime", "next_episode", "model",
                               "model_digest", "optimizer", "parameter_names",
                               "active_parameter_names", "optimizer_digest"}
                    or value["schema"] != CHECKPOINT_SCHEMA
                    or value["config"] != asdict(self.config)
                    or value["source_id"] != self.source_id
                    or value["tokenizer_sha256"] != self.tokenizer_sha256
                    or value["input_digest"] != self.input_digest
                    or value["runtime"] != self.runtime
                    or value["parameter_names"] != self.parameter_names
                    or type(value["next_episode"]) is not int
                    or not 0 <= value["next_episode"] <= self.config.episode_count):
                raise BoundaryError("memory_checkpoint", "resume_identity_mismatch")
            weights = _validated_weights(self.model, value["model"])
            if value["model_digest"] != _tensor_digest(weights):
                raise BoundaryError("memory_checkpoint", "model_digest_mismatch")
            optimizer = value["optimizer"]
            if (not isinstance(optimizer, dict) or set(optimizer) != {"state", "param_groups"}
                    or optimizer["param_groups"] != self.optimizer.state_dict()["param_groups"]
                    or not isinstance(optimizer["state"], dict)):
                raise BoundaryError("memory_checkpoint", "optimizer_config_mismatch")
            state = optimizer["state"]
            if any(type(index) is not int or not 0 <= index < len(self.parameters)
                   for index in state):
                raise BoundaryError("memory_checkpoint", "optimizer_index_mismatch")
            active = tuple(self.parameter_names[index] for index in sorted(state))
            if (value["active_parameter_names"] != active
                    or bool(active) != bool(value["next_episode"])):
                raise BoundaryError("memory_checkpoint", "optimizer_inventory_mismatch")
            unused_prefixes = ("feedback_embedding.", "feedback_projection.")
            optional = {"previous_action_marker", "feedback_marker"}
            mandatory = {name for name in self.parameter_names
                         if name not in optional and not name.startswith(unused_prefixes)}
            if value["next_episode"] and not mandatory.issubset(active):
                raise BoundaryError("memory_checkpoint", "optimizer_inventory_mismatch")
            completed = self._episodes[:value["next_episode"]]
            labeled_steps = (step for episode in completed for step in episode.steps
                             if step.label_key is not None)
            used_previous = False
            used_feedback = False
            for step in labeled_steps:
                used_previous |= step.previous_actual_action is not None
                used_feedback |= step.public_feedback is not None
            if used_previous and "previous_action_marker" not in active:
                raise BoundaryError("memory_checkpoint", "optimizer_inventory_mismatch")
            if (used_feedback and not {name for name in self.parameter_names
                                       if name == "feedback_marker"
                                       or name.startswith(unused_prefixes)}.issubset(active)):
                raise BoundaryError("memory_checkpoint", "optimizer_inventory_mismatch")
            updates, possible_previous, possible_feedback = self._completed_update_context(
                value["next_episode"],
            )
            if "previous_action_marker" in active and not possible_previous:
                raise BoundaryError("memory_checkpoint", "optimizer_inventory_mismatch")
            if (any(name == "feedback_marker" or name.startswith(unused_prefixes)
                    for name in active)
                    and not possible_feedback):
                raise BoundaryError("memory_checkpoint", "optimizer_inventory_mismatch")
            for index, tensors in state.items():
                parameter = self.parameters[index]
                if (not isinstance(tensors, dict)
                        or set(tensors) != {"step", "exp_avg", "exp_avg_sq"}
                        or not isinstance(tensors["step"], Tensor)
                        or tensors["step"].ndim != 0
                        or tensors["step"].dtype != torch.float32
                        or not 1 <= float(tensors["step"]) <= updates
                        or not float(tensors["step"]).is_integer()
                        or any(not isinstance(tensors[key], Tensor)
                               or tensors[key].shape != parameter.shape
                               or tensors[key].dtype != parameter.dtype
                               or not bool(torch.isfinite(tensors[key]).all())
                               for key in ("exp_avg", "exp_avg_sq"))):
                    raise BoundaryError("memory_checkpoint", "optimizer_tensor_mismatch")
                if (self.parameter_names[index] in mandatory
                        and float(tensors["step"]) != updates):
                    raise BoundaryError("memory_checkpoint", "optimizer_step_mismatch")
            if value["optimizer_digest"] != _optimizer_digest(optimizer, self.parameter_names):
                raise BoundaryError("memory_checkpoint", "optimizer_digest_mismatch")
            self.model.load_state_dict(weights, strict=True)
            self.optimizer.load_state_dict(optimizer)
            self.next_episode = value["next_episode"]
        except Exception:
            self._failed = True
            raise

    def export(self) -> bytes:
        self._assert_live()
        if self.next_episode != self.config.episode_count:
            raise BoundaryError("memory_model", "training_incomplete")
        weights = _weights(self.model)
        return encode_checkpoint({
            "schema": EXPORT_SCHEMA, "config": asdict(self.config),
            "tokenizer_sha256": self.tokenizer_sha256,
            "weights": weights, "weights_digest": _tensor_digest(weights),
        })


def load_memory_export(raw: bytes, config: MemoryConfig,
                       tokenizer_sha256: str) -> ExperimentalDSimpleM2:
    value = decode_checkpoint(raw)
    if (set(value) != {"schema", "config", "tokenizer_sha256", "weights",
                       "weights_digest"}
            or value["schema"] != EXPORT_SCHEMA
            or value["config"] != asdict(config)
            or value["tokenizer_sha256"] != tokenizer_sha256):
        raise BoundaryError("memory_model", "export_identity_mismatch")
    model = _construct(config)
    weights = _validated_weights(model, value["weights"])
    if value["weights_digest"] != _tensor_digest(weights):
        raise BoundaryError("memory_model", "weights_digest_mismatch")
    model.load_state_dict(weights, strict=True)
    model.eval()
    return model
