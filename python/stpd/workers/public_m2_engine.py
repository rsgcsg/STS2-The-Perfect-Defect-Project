"""Scratch public-catalog M2 training over complete ordered chains.

The caller owns source admission and constructs the immutable chains. This
engine owns only numerical training, deterministic continuation, and a typed
weights export. One optimizer update covers each fixed window of decisions.
"""

from __future__ import annotations

import hashlib
import math
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
from torch import Tensor
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError, json_bytes

from ..light_action_codec import SPEC_SHA256
from ..models.light_action_m2 import (
    LIGHT_ACTION_M2_GRAPH,
    LIGHT_ACTION_M2_WIDTH,
    LightActionM2Scorer,
)
from ..models.light_action_m2_training_data import LightActionM2TrainingStep
from ..models.public_m2_window import preflight_public_m2_window, train_public_m2_window
from ..models.token_core import ScratchShape, ScratchTokenCore
from .checkpoint_codec import decode_checkpoint, encode_checkpoint

PUBLIC_M2_SEQUENCE_PROFILE = "stpd/public-m2-observation-only-v1"
PUBLIC_M2_PRIOR_ACTION_PROFILE = "stpd/public-m2-no-prior-action-v1"
PUBLIC_M2_FEEDBACK_PROFILE = "stpd/public-m2-no-feedback-v1"
PUBLIC_M2_CHECKPOINT_SCHEMA = "stpd/public-m2-engine-checkpoint-v1"
PUBLIC_M2_EXPORT_SCHEMA = "stpd/public-m2-engine-weights-v1"
_EXPORT_EPOCHS = frozenset({1, 3, 5})


def _checked_device(value: str, *, require_available: bool) -> torch.device:
    if type(value) is not str or type(require_available) is not bool:
        raise BoundaryError("public_m2_engine", "invalid_device")
    try:
        device = torch.device(value)
    except (ValueError, TypeError, RuntimeError) as error:
        raise BoundaryError("public_m2_engine", "invalid_device") from error
    if (device.type not in {"cpu", "cuda"}
            or device.type == "cpu" and device.index is not None
            or device.type == "cuda" and device.index is None):
        raise BoundaryError("public_m2_engine", "invalid_device")
    if (require_available and device.type == "cuda"
            and (not torch.cuda.is_available() or device.index >= torch.cuda.device_count())):
        raise BoundaryError("public_m2_engine", "unavailable_device")
    return device


@dataclass(frozen=True)
class PublicM2EngineChain:
    chain_id: str
    steps: tuple[LightActionM2TrainingStep, ...]


@dataclass(frozen=True)
class PublicM2EngineConfig:
    """Explicit source, graph, shape, limits, and optimizer identity.

    ``source_digest`` is a verified caller pin; ``input_digest`` is computed
    from the complete ordered train/dev chain values by this engine.
    """

    source_digest: str
    state_tokenizer_sha256: str
    shape: ScratchShape
    max_action_bytes: int
    max_actions_per_step: int
    max_chain_steps: int
    max_total_steps: int
    max_total_input_tokens: int
    slots: int = 8
    reset_each_step: bool = False
    seed: int = 1701
    learning_rate: float = 3e-4
    weight_decay: float = 0.0
    gradient_clip: float = 1.0
    epochs: int = 5
    window_steps: int = 8
    max_window_tokens: int = 65_536
    device: str = "cpu"
    graph: str = LIGHT_ACTION_M2_GRAPH
    action_codec_sha256: str = SPEC_SHA256
    sequence_profile: str = PUBLIC_M2_SEQUENCE_PROFILE
    prior_action_profile: str = PUBLIC_M2_PRIOR_ACTION_PROFILE
    feedback_profile: str = PUBLIC_M2_FEEDBACK_PROFILE

    def validate(self, *, require_device_available: bool = True) -> None:
        digests = (self.source_digest, self.state_tokenizer_sha256, self.action_codec_sha256)
        if (
            any(type(digest) is not str or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                for digest in digests)
            or self.action_codec_sha256 != SPEC_SHA256
            or self.graph != LIGHT_ACTION_M2_GRAPH
            or self.sequence_profile != PUBLIC_M2_SEQUENCE_PROFILE
            or self.prior_action_profile != PUBLIC_M2_PRIOR_ACTION_PROFILE
            or self.feedback_profile != PUBLIC_M2_FEEDBACK_PROFILE
            or not isinstance(self.shape, ScratchShape)
            or self.shape.vocab_size < 258
            or type(self.max_action_bytes) is not int or not 1 <= self.max_action_bytes <= 8192
            or type(self.max_actions_per_step) is not int
            or not 1 <= self.max_actions_per_step <= 16_384
            or type(self.max_chain_steps) is not int or self.max_chain_steps < 1
            or type(self.max_total_steps) is not int or self.max_total_steps < 1
            or type(self.max_total_input_tokens) is not int or self.max_total_input_tokens < 1
            or type(self.slots) is not int or self.slots not in (1, 8)
            or type(self.reset_each_step) is not bool
            or type(self.seed) is not int or not 0 <= self.seed < 2**63
            or type(self.epochs) is not int or not 1 <= self.epochs <= 5
            or type(self.window_steps) is not int or not 1 <= self.window_steps <= 8
            or type(self.max_window_tokens) is not int or not 1 <= self.max_window_tokens <= 98_304
        ):
            raise BoundaryError("public_m2_engine", "invalid_config")
        try:
            self.shape.validate()
        except (ValueError, TypeError, RuntimeError) as error:
            raise BoundaryError("public_m2_engine", "invalid_shape") from error
        _checked_device(self.device, require_available=require_device_available)
        if any(type(value) not in {float, int} or not math.isfinite(value)
               for value in (self.learning_rate, self.weight_decay, self.gradient_clip)):
            raise BoundaryError("public_m2_engine", "invalid_optimizer_config")
        if self.learning_rate <= 0 or self.weight_decay < 0 or self.gradient_clip <= 0:
            raise BoundaryError("public_m2_engine", "invalid_optimizer_config")


@dataclass(frozen=True)
class PublicM2WindowProgress:
    loss_mean: float
    label_count: int
    optimizer_updates: int
    completed_epochs: int
    chain_index: int
    window_cursor: int


@dataclass(frozen=True)
class PublicM2DevMetrics:
    loss_mean: float
    top1_accuracy: float
    label_count: int
    correct_count: int


def _tensor_digest(values: dict[str, Tensor]) -> str:
    digest = hashlib.sha256()
    for name in sorted(values):
        tensor = values[name].detach().cpu().contiguous()
        digest.update(name.encode())
        digest.update(str(tensor.dtype).encode())
        digest.update(str(tuple(tensor.shape)).encode())
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


def _weights(model: LightActionM2Scorer) -> dict[str, Tensor]:
    return {name: value.detach().cpu().contiguous().clone()
            for name, value in model.state_dict().items()}


def _optimizer_digest(state: dict[str, Any], names: tuple[str, ...]) -> str:
    tensors = {f"{names[index]}:{key}": value for index, entries in state["state"].items()
               for key, value in entries.items() if isinstance(value, Tensor)}
    return _tensor_digest(tensors)


def _implementation_digest() -> str:
    root = Path(__file__).resolve().parents[2]
    names = (
        "stpd/workers/public_m2_engine.py", "stpd/workers/checkpoint_codec.py",
        "stpd/models/public_m2_window.py", "stpd/models/light_action_m2.py",
        "stpd/models/light_action_m2_training_data.py",
        "stpd/models/light_action_encoder.py", "stpd/models/losses.py",
        "stpd/models/token_core.py", "stpd/light_action_codec.py",
    )
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def _chain_values(chains: tuple[PublicM2EngineChain, ...]) -> list[dict[str, Any]]:
    return [{"chain_id": chain.chain_id, "steps": [asdict(step) for step in chain.steps]}
            for chain in chains]


def _construct(config: PublicM2EngineConfig, *, device: torch.device | None = None
               ) -> LightActionM2Scorer:
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(config.seed)
        core = ScratchTokenCore(config.shape)
    return LightActionM2Scorer(
        core, max_action_bytes=config.max_action_bytes,
        initialization_seed=config.seed, slots=config.slots,
        reset_each_step=config.reset_each_step,
    ).to(torch.device(config.device) if device is None else device)


class PublicM2Engine:
    """One fixed-order scratch run with complete window-boundary continuation."""

    def __init__(self, train_chains: tuple[PublicM2EngineChain, ...],
                 dev_chains: tuple[PublicM2EngineChain, ...],
                 config: PublicM2EngineConfig) -> None:
        if not isinstance(config, PublicM2EngineConfig):
            raise BoundaryError("public_m2_engine", "typed_config_required")
        config.validate()
        if torch.get_default_dtype() != torch.float32:
            raise BoundaryError("public_m2_engine", "float32_default_dtype_required")
        if config.device == "cpu" and torch.get_num_threads() != 1:
            raise BoundaryError("public_m2_engine", "one_cpu_thread_required")
        self.config = config
        self._owned_config = config
        self._config_identity = asdict(config)
        self.device = torch.device(config.device)
        self.model = _construct(config)
        self._owned_model = self.model
        self.train_chains = self._preflight_chains(train_chains, "train")
        self.dev_chains = self._preflight_chains(dev_chains, "dev")
        self._owned_train_chains = self.train_chains
        self._owned_dev_chains = self.dev_chains
        if set(chain.chain_id for chain in self.train_chains) & set(
            chain.chain_id for chain in self.dev_chains
        ):
            raise BoundaryError("public_m2_engine", "train_dev_chain_overlap")
        self.input_digest = hashlib.sha256(json_bytes({
            "train": _chain_values(self.train_chains), "dev": _chain_values(self.dev_chains),
        })).hexdigest()
        if (sum(len(chain.steps) for chain in (*self.train_chains, *self.dev_chains))
                > config.max_total_steps or
                sum(len(step.page) + sum(map(len, step.byte_actions))
                    for chain in (*self.train_chains, *self.dev_chains)
                    for step in chain.steps) > config.max_total_input_tokens):
            raise BoundaryError("public_m2_engine", "total_input_limit_exceeded")
        self.parameter_names = tuple(name for name, parameter in self.model.named_parameters()
                                     if parameter.requires_grad)
        self.parameters = tuple(parameter for parameter in self.model.parameters()
                                if parameter.requires_grad)
        if len(self.parameters) != len(self.parameter_names) or not self.parameters:
            raise BoundaryError("public_m2_engine", "trainable_parameter_inventory")
        self.optimizer = torch.optim.AdamW(
            self.parameters, lr=config.learning_rate, weight_decay=config.weight_decay,
        )
        self._owned_optimizer = self.optimizer
        self._optimizer_group = self.optimizer.state_dict()["param_groups"]
        self._optimizer_group_options = tuple(sorted(
            (key, value) for key, value in self.optimizer.param_groups[0].items()
            if key != "params"
        ))
        self.runtime = {
            "torch": str(torch.__version__), "python": platform.python_version(),
            "platform": platform.platform(), "default_dtype": str(torch.get_default_dtype()),
            "cpu_threads": torch.get_num_threads(),
            "implementation_sha256": _implementation_digest(),
        }
        cpu_generator = torch.Generator(device="cpu")
        cpu_generator.manual_seed(config.seed ^ 0x4D325055)
        self._cpu_rng = cpu_generator.get_state()
        self._cuda_rng: Tensor | None = None
        if self.device.type == "cuda":
            cuda_generator = torch.Generator(device=self.device)
            cuda_generator.manual_seed(config.seed ^ 0x4D325043)
            self._cuda_rng = cuda_generator.get_state()
        self.completed_epochs = 0
        self.chain_index = 0
        self.window_cursor = 0
        self.memory = self.model.initial_memory().detach()
        self.label_count = 0
        self.optimizer_updates = 0
        self.loss_sum = 0.0
        self._failed = False

    def _preflight_chains(self, chains: tuple[PublicM2EngineChain, ...],
                          split: str) -> tuple[PublicM2EngineChain, ...]:
        if not isinstance(chains, tuple) or not chains:
            raise BoundaryError("public_m2_engine", f"empty_{split}_chains")
        ids: set[str] = set()
        total_steps = total_tokens = 0
        for chain in chains:
            if (not isinstance(chain, PublicM2EngineChain)
                    or type(chain.chain_id) is not str or not chain.chain_id
                    or chain.chain_id in ids or not isinstance(chain.steps, tuple)
                    or not chain.steps or len(chain.steps) > self.config.max_chain_steps):
                raise BoundaryError("public_m2_engine", "invalid_chain")
            ids.add(chain.chain_id)
            total_steps += len(chain.steps)
            for position, step in enumerate(chain.steps):
                if (not isinstance(step, LightActionM2TrainingStep)
                        or step.position != position
                        or step.reset_before is not (position == 0)
                        or step.previous_actual_action is not None
                        or step.target_action_id is None
                        or len(step.action_ids) > self.config.max_actions_per_step):
                    raise BoundaryError("public_m2_engine", "invalid_observation_only_step")
                total_tokens += len(step.page) + sum(map(len, step.byte_actions))
            for start in range(0, len(chain.steps), self.config.window_steps):
                window = chain.steps[start:start + self.config.window_steps]
                preflight_public_m2_window(
                    self.model, window, self.model.initial_memory(),
                    chain_start=(start == 0), max_window_steps=self.config.window_steps,
                    max_window_tokens=self.config.max_window_tokens,
                    require_targets=True,
                )
        if (total_steps > self.config.max_total_steps
                or total_tokens > self.config.max_total_input_tokens):
            raise BoundaryError("public_m2_engine", "total_input_limit_exceeded")
        return chains

    @property
    def finished(self) -> bool:
        return self.completed_epochs == self.config.epochs

    def _assert_live(self, *, full: bool = False) -> None:
        if self._failed:
            raise BoundaryError("public_m2_engine", "failed_engine")
        actual = tuple((name, parameter) for name, parameter in self.model.named_parameters()
                       if parameter.requires_grad)
        group = self.optimizer.param_groups[0] if len(self.optimizer.param_groups) == 1 else {}
        bound = (self.config is self._owned_config
                 and self.model is self._owned_model
                 and self.train_chains is self._owned_train_chains
                 and self.dev_chains is self._owned_dev_chains
                 and self.optimizer is self._owned_optimizer
                 and type(self.optimizer) is torch.optim.AdamW
                 and tuple(name for name, _ in actual) == self.parameter_names
                 and tuple(id(parameter) for _, parameter in actual)
                 == tuple(id(parameter) for parameter in self.parameters)
                 and isinstance(group.get("params"), list)
                 and tuple(id(parameter) for parameter in group["params"])
                 == tuple(id(parameter) for parameter in self.parameters)
                 and tuple(sorted((key, value) for key, value in group.items()
                                  if key != "params")) == self._optimizer_group_options
                 and self.optimizer.state_dict()["param_groups"] == self._optimizer_group)
        if (not bound or str(torch.__version__) != self.runtime["torch"]
                or str(torch.get_default_dtype()) != self.runtime["default_dtype"]
                or torch.get_num_threads() != self.runtime["cpu_threads"]):
            self._failed = True
            raise BoundaryError("public_m2_engine", "binding_changed")
        if full:
            try:
                current_input_digest = hashlib.sha256(json_bytes({
                    "train": _chain_values(self.train_chains),
                    "dev": _chain_values(self.dev_chains),
                })).hexdigest()
                unchanged = (
                    self._config_identity == asdict(self.config)
                    and self.input_digest == current_input_digest
                    and self.runtime["implementation_sha256"] == _implementation_digest()
                )
            except Exception:
                unchanged = False
            if not unchanged:
                self._failed = True
                raise BoundaryError("public_m2_engine", "identity_changed")

    def advance_window(self) -> PublicM2WindowProgress:
        self._assert_live()
        if self.finished:
            raise BoundaryError("public_m2_engine", "exhausted")
        chain = self.train_chains[self.chain_index]
        start = self.window_cursor
        steps = chain.steps[start:start + self.config.window_steps]
        external_cpu = torch.get_rng_state()
        external_cuda = (torch.cuda.get_rng_state(self.device)
                         if self._cuda_rng is not None else None)
        try:
            torch.set_rng_state(self._cpu_rng)
            if self._cuda_rng is not None:
                torch.cuda.set_rng_state(self._cuda_rng, self.device)
            result = train_public_m2_window(
                self.model, self.optimizer, steps, self.memory,
                chain_start=(start == 0), max_window_steps=self.config.window_steps,
                max_window_tokens=self.config.max_window_tokens,
                gradient_clip=self.config.gradient_clip,
            )
            if result.label_count != len(steps) or result.optimizer_updates != 1:
                raise BoundaryError("public_m2_engine", "window_result_mismatch")
            self.memory = result.memory.detach()
            self.label_count += result.label_count
            self.optimizer_updates += result.optimizer_updates
            self.loss_sum += result.loss_sum
            self._cpu_rng = torch.get_rng_state().clone()
            if self._cuda_rng is not None:
                self._cuda_rng = torch.cuda.get_rng_state(self.device).clone()
            self.window_cursor += len(steps)
            if self.window_cursor == len(chain.steps):
                self.window_cursor = 0
                self.chain_index += 1
                self.memory = self.model.initial_memory().detach()
                if self.chain_index == len(self.train_chains):
                    self.chain_index = 0
                    self.completed_epochs += 1
            return PublicM2WindowProgress(
                loss_mean=result.loss_mean, label_count=result.label_count,
                optimizer_updates=result.optimizer_updates,
                completed_epochs=self.completed_epochs, chain_index=self.chain_index,
                window_cursor=self.window_cursor,
            )
        except Exception:
            self._failed = True
            raise
        finally:
            torch.set_rng_state(external_cpu)
            if external_cuda is not None:
                torch.cuda.set_rng_state(external_cuda, self.device)

    def evaluate_dev(self) -> PublicM2DevMetrics:
        self._assert_live(full=True)
        was_training = self.model.training
        loss_sum = 0.0
        correct = count = 0
        try:
            self.model.eval()
            with torch.no_grad():
                for chain in self.dev_chains:
                    memory = self.model.initial_memory()
                    for step in chain.steps:
                        page = torch.tensor(step.page, dtype=torch.long, device=self.device)
                        actions = tuple(torch.tensor(action, dtype=torch.long, device=self.device)
                                        for action in step.byte_actions)
                        scores, memory = self.model.step(
                            page, actions, memory, previous_actual_action=None,
                            public_feedback=None, reset_before=step.reset_before,
                        )
                        target = step.action_ids.index(step.target_action_id)
                        loss_sum += float(F.cross_entropy(
                            scores.unsqueeze(0), torch.tensor([target], device=self.device),
                        ))
                        correct += int(int(scores.argmax()) == target)
                        count += 1
            return PublicM2DevMetrics(loss_sum / count, correct / count, count, correct)
        finally:
            self.model.train(was_training)

    def checkpoint(self) -> bytes:
        self._assert_live(full=True)
        weights = _weights(self.model)
        optimizer = self.optimizer.state_dict()
        memory = self.memory.detach().cpu().clone()
        cpu_rng = self._cpu_rng.detach().cpu().clone()
        cuda_rng = None if self._cuda_rng is None else self._cuda_rng.detach().cpu().clone()
        return encode_checkpoint({
            "schema": PUBLIC_M2_CHECKPOINT_SCHEMA, "config": asdict(self.config),
            "input_digest": self.input_digest, "runtime": self.runtime,
            "completed_epochs": self.completed_epochs,
            "chain_index": self.chain_index, "window_cursor": self.window_cursor,
            "memory": memory, "memory_digest": _tensor_digest({"memory": memory}),
            "label_count": self.label_count, "optimizer_updates": self.optimizer_updates,
            "loss_sum": self.loss_sum, "model": weights,
            "model_digest": _tensor_digest(weights), "optimizer": optimizer,
            "optimizer_digest": _optimizer_digest(optimizer, self.parameter_names),
            "parameter_names": self.parameter_names,
            "cpu_rng": cpu_rng, "cuda_rng": cuda_rng,
            "rng_digest": _tensor_digest({
                "cpu_rng": cpu_rng, **({} if cuda_rng is None else {"cuda_rng": cuda_rng}),
            }),
        })

    def _expected_progress(self, epoch: int, chain_index: int,
                           cursor: int) -> tuple[int, int]:
        all_steps = sum(len(chain.steps) for chain in self.train_chains)
        all_windows = sum((len(chain.steps) + self.config.window_steps - 1)
                          // self.config.window_steps for chain in self.train_chains)
        labels = epoch * all_steps + sum(len(chain.steps)
                                         for chain in self.train_chains[:chain_index]) + cursor
        updates = epoch * all_windows + sum(
            (len(chain.steps) + self.config.window_steps - 1) // self.config.window_steps
            for chain in self.train_chains[:chain_index]
        ) + ((cursor + self.config.window_steps - 1) // self.config.window_steps)
        return labels, updates

    def restore(self, raw: bytes) -> None:
        self._assert_live(full=True)
        try:
            value = decode_checkpoint(raw)
            if (set(value) != {"schema", "config", "input_digest", "runtime",
                               "completed_epochs", "chain_index", "window_cursor", "memory",
                               "memory_digest", "rng_digest",
                               "label_count", "optimizer_updates", "loss_sum", "model",
                               "model_digest", "optimizer", "optimizer_digest",
                               "parameter_names", "cpu_rng", "cuda_rng"}
                    or value["schema"] != PUBLIC_M2_CHECKPOINT_SCHEMA
                    or value["config"] != asdict(self.config)
                    or value["input_digest"] != self.input_digest
                    or value["runtime"] != self.runtime
                    or value["parameter_names"] != self.parameter_names):
                raise BoundaryError("public_m2_checkpoint", "identity_mismatch")
            epoch, chain_index, cursor = (value[key] for key in
                                          ("completed_epochs", "chain_index", "window_cursor"))
            if (any(type(item) is not int for item in (epoch, chain_index, cursor))
                    or not 0 <= epoch <= self.config.epochs
                    or not 0 <= chain_index < len(self.train_chains)
                    or epoch == self.config.epochs and (chain_index != 0 or cursor != 0)
                    or epoch < self.config.epochs and
                    cursor not in range(0, len(self.train_chains[chain_index].steps),
                                        self.config.window_steps)):
                raise BoundaryError("public_m2_checkpoint", "invalid_progress")
            expected_labels, expected_updates = self._expected_progress(epoch, chain_index, cursor)
            if (type(value["label_count"]) is not int
                    or value["label_count"] != expected_labels
                    or type(value["optimizer_updates"]) is not int
                    or value["optimizer_updates"] != expected_updates
                    or type(value["loss_sum"]) not in {float, int}
                    or not math.isfinite(value["loss_sum"])
                    or value["loss_sum"] < 0):
                raise BoundaryError("public_m2_checkpoint", "progress_mismatch")
            memory = value["memory"]
            if (not isinstance(memory, Tensor)
                    or memory.shape != (self.config.slots, LIGHT_ACTION_M2_WIDTH)
                    or memory.dtype != self.model.write_queries.dtype
                    or not bool(torch.isfinite(memory).all())
                    or cursor == 0 and bool(memory.any())
                    or value["memory_digest"] != _tensor_digest({"memory": memory})):
                raise BoundaryError("public_m2_checkpoint", "memory_mismatch")
            weights = value["model"]
            expected_weights = self.model.state_dict()
            if (not isinstance(weights, dict) or set(weights) != set(expected_weights)
                    or any(not isinstance(weights[name], Tensor)
                           or weights[name].shape != expected_weights[name].shape
                           or weights[name].dtype != expected_weights[name].dtype
                           for name in expected_weights)
                    or value["model_digest"] != _tensor_digest(weights)):
                raise BoundaryError("public_m2_checkpoint", "model_mismatch")
            optimizer = value["optimizer"]
            if (not isinstance(optimizer, dict) or set(optimizer) != {"state", "param_groups"}
                    or optimizer["param_groups"] != self._optimizer_group
                    or not isinstance(optimizer["state"], dict)
                    or value["optimizer_digest"] != _optimizer_digest(
                        optimizer, self.parameter_names)):
                raise BoundaryError("public_m2_checkpoint", "optimizer_mismatch")
            expected_active = set(self.parameter_names) - {
                "previous_action_marker", "feedback_marker",
            } if expected_updates else set()
            active = set()
            for index, state in optimizer["state"].items():
                if type(index) is not int or not 0 <= index < len(self.parameters):
                    raise BoundaryError("public_m2_checkpoint", "optimizer_index_mismatch")
                active.add(self.parameter_names[index])
                parameter = self.parameters[index]
                if (not isinstance(state, dict) or set(state) != {"step", "exp_avg", "exp_avg_sq"}
                        or any(not isinstance(state[key], Tensor) for key in state)
                        or state["step"].ndim != 0
                        or state["step"].dtype != torch.float32
                        or state["step"].device.type != "cpu"
                        or float(state["step"]) != expected_updates
                        or any(state[key].shape != parameter.shape
                               or state[key].dtype != parameter.dtype
                               for key in ("exp_avg", "exp_avg_sq"))
                        or bool((state["exp_avg_sq"] < 0).any())):
                    raise BoundaryError("public_m2_checkpoint", "optimizer_state_mismatch")
            if active != expected_active:
                raise BoundaryError("public_m2_checkpoint", "optimizer_active_mismatch")
            cpu_rng, cuda_rng = value["cpu_rng"], value["cuda_rng"]
            if (not isinstance(cpu_rng, Tensor) or cpu_rng.dtype != torch.uint8
                    or cpu_rng.shape != self._cpu_rng.shape
                    or (self._cuda_rng is None) != (cuda_rng is None)
                    or self._cuda_rng is not None and (
                        not isinstance(cuda_rng, Tensor) or cuda_rng.dtype != torch.uint8
                        or cuda_rng.shape != self._cuda_rng.shape)
                    or value["rng_digest"] != _tensor_digest({
                        "cpu_rng": cpu_rng,
                        **({} if cuda_rng is None else {"cuda_rng": cuda_rng}),
                    })):
                raise BoundaryError("public_m2_checkpoint", "rng_mismatch")
            torch.Generator(device="cpu").set_state(cpu_rng)
            if cuda_rng is not None:
                torch.Generator(device=self.device).set_state(cuda_rng)
            # Only mutation after every identity, progress, tensor, and RNG check.
            self.model.load_state_dict(weights, strict=True)
            self.optimizer.load_state_dict(optimizer)
            self.completed_epochs, self.chain_index, self.window_cursor = epoch, chain_index, cursor
            self.memory = memory.to(self.device).detach()
            self.label_count, self.optimizer_updates = expected_labels, expected_updates
            self.loss_sum = float(value["loss_sum"])
            self._cpu_rng = cpu_rng.clone()
            self._cuda_rng = None if cuda_rng is None else cuda_rng.clone()
        except Exception:
            self._failed = True
            raise

    def export_weights(self) -> bytes:
        self._assert_live(full=True)
        if (self.completed_epochs not in _EXPORT_EPOCHS
                or self.chain_index != 0 or self.window_cursor != 0):
            raise BoundaryError("public_m2_export", "epoch_boundary_required")
        checkpoint_digest = hashlib.sha256(self.checkpoint()).hexdigest()
        weights = _weights(self.model)
        return encode_checkpoint({
            "schema": PUBLIC_M2_EXPORT_SCHEMA, "config": asdict(self.config),
            "input_digest": self.input_digest,
            "implementation_sha256": self.runtime["implementation_sha256"],
            "completed_epochs": self.completed_epochs,
            "optimizer_updates": self.optimizer_updates,
            "run_complete": self.finished, "checkpoint_digest": checkpoint_digest,
            "weights": weights, "weights_digest": _tensor_digest(weights),
        })


def load_public_m2_weights(
    raw: bytes, config: PublicM2EngineConfig, *, input_digest: str,
    completed_epochs: int, inference_device: str = "cpu",
) -> LightActionM2Scorer:
    """Load exact training weights onto an independently selected inference device."""
    config.validate(require_device_available=False)
    target_device = _checked_device(inference_device, require_available=True)
    try:
        value = decode_checkpoint(raw)
        if (
            set(value) != {
                "schema", "config", "input_digest", "implementation_sha256",
                "completed_epochs", "optimizer_updates", "run_complete",
                "checkpoint_digest", "weights", "weights_digest",
            }
            or value["schema"] != PUBLIC_M2_EXPORT_SCHEMA
            or value["config"] != asdict(config)
            or value["input_digest"] != input_digest
            or value["implementation_sha256"] != _implementation_digest()
            or type(completed_epochs) is not int
            or completed_epochs not in _EXPORT_EPOCHS
            or completed_epochs > config.epochs
            or value["completed_epochs"] != completed_epochs
            or value["run_complete"] is not (completed_epochs == config.epochs)
            or type(value["optimizer_updates"]) is not int
            or value["optimizer_updates"] < completed_epochs
            or type(value["checkpoint_digest"]) is not str
            or len(value["checkpoint_digest"]) != 64
            or any(char not in "0123456789abcdef" for char in value["checkpoint_digest"])
        ):
            raise BoundaryError("public_m2_export", "identity_mismatch")
        model = _construct(config, device=target_device)
        expected = model.state_dict()
        weights = value["weights"]
        if (
            not isinstance(weights, dict) or set(weights) != set(expected)
            or any(not isinstance(weights[name], Tensor)
                   or weights[name].shape != expected[name].shape
                   or weights[name].dtype != expected[name].dtype
                   for name in expected)
            or value["weights_digest"] != _tensor_digest(weights)
        ):
            raise BoundaryError("public_m2_export", "weights_mismatch")
        model.load_state_dict(weights, strict=True)
        model.eval()
        return model
    except BoundaryError:
        raise
    except Exception as error:
        raise BoundaryError("public_m2_export", "invalid_export") from error
