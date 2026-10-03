"""Matched stateless M0 training on the admitted public M2 chain input.

This is the A01 numerical control: the exact same ordered public decisions and
window update schedule as M2, with no memory. Source admission stays upstream.
"""

from __future__ import annotations

import hashlib
import math
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import torch
from tokenizers import Tokenizer
from torch import Tensor

from spireagent.json_boundary import BoundaryError

from ..fullrun.public_m2_sequences import PublicM2Chain, PublicM2Input
from ..models.light_action_m2_training_data import LightActionM2TrainingStep
from ..models.losses import listwise_rank_loss
from ..models.stage1a import LightActionM0Scorer
from ..models.token_core import ScratchShape, ScratchTokenCore
from .checkpoint_codec import decode_checkpoint, encode_checkpoint
from .token_ranking import LightActionM0Config, _scoring_seed, construct_model

CHECKPOINT_SCHEMA = "stpd/public-m0-matched-checkpoint-v1"
EXPORT_SCHEMA = "stpd/public-m0-matched-weights-v1"
_EXPORT_EPOCHS = frozenset({1, 3, 5})
_STAGE = "public_m0_matched_engine"


def _device(value: str, *, available: bool) -> torch.device:
    if type(value) is not str:
        raise BoundaryError(_STAGE, "invalid_device")
    try:
        device = torch.device(value)
    except (ValueError, TypeError, RuntimeError) as error:
        raise BoundaryError(_STAGE, "invalid_device") from error
    if (device.type == "cpu" and device.index is None):
        return device
    if (device.type != "cuda" or device.index is None
            or available and (not torch.cuda.is_available()
                              or device.index >= torch.cuda.device_count())):
        raise BoundaryError(_STAGE, "unavailable_device")
    return device


@dataclass(frozen=True)
class PublicM0MatchedConfig:
    source_digest: str
    max_chain_steps: int
    max_total_steps: int
    max_total_input_tokens: int
    max_actions_per_step: int
    seed: int = 1701
    learning_rate: float = 3e-4
    weight_decay: float = 0.0
    gradient_clip: float = 1.0
    epochs: int = 5
    window_steps: int = 8
    max_window_tokens: int = 65_536
    device: str = "cpu"
    shape_override: ScratchShape | None = None

    def validate(self, *, require_device: bool = True) -> None:
        if (type(self.source_digest) is not str or len(self.source_digest) != 64
                or any(char not in "0123456789abcdef" for char in self.source_digest)
                or type(self.max_chain_steps) is not int or self.max_chain_steps < 1
                or type(self.max_total_steps) is not int or self.max_total_steps < 1
                or type(self.max_total_input_tokens) is not int
                or self.max_total_input_tokens < 1
                or type(self.max_actions_per_step) is not int
                or not 1 <= self.max_actions_per_step <= 16_384
                or type(self.seed) is not int or not 0 <= self.seed < 2**63
                or type(self.epochs) is not int or not 1 <= self.epochs <= 5
                or type(self.window_steps) is not int or not 1 <= self.window_steps <= 8
                or type(self.max_window_tokens) is not int
                or not 1 <= self.max_window_tokens <= 65_536
                or self.shape_override is not None
                and not isinstance(self.shape_override, ScratchShape)):
            raise BoundaryError(_STAGE, "invalid_config")
        if any(type(value) not in {int, float} or not math.isfinite(value)
               for value in (self.learning_rate, self.weight_decay, self.gradient_clip)):
            raise BoundaryError(_STAGE, "invalid_optimizer_config")
        if self.learning_rate <= 0 or self.weight_decay < 0 or self.gradient_clip <= 0:
            raise BoundaryError(_STAGE, "invalid_optimizer_config")
        if self.shape_override is not None:
            self.shape_override.validate()
        _device(self.device, available=require_device)


@dataclass(frozen=True)
class PublicM0WindowProgress:
    loss_mean: float
    label_count: int
    optimizer_updates: int
    completed_epochs: int
    chain_index: int
    window_cursor: int


@dataclass(frozen=True)
class PublicM0DevMetrics:
    loss_mean: float
    top1_accuracy: float
    label_count: int
    correct_count: int


def _digest_tensors(tensors: dict[str, Tensor]) -> str:
    digest = hashlib.sha256()
    for name in sorted(tensors):
        tensor = tensors[name].detach().cpu().contiguous()
        digest.update(name.encode())
        digest.update(str(tensor.dtype).encode())
        digest.update(str(tuple(tensor.shape)).encode())
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


def _weights(model: LightActionM0Scorer) -> dict[str, Tensor]:
    return {name: tensor.detach().cpu().contiguous().clone()
            for name, tensor in model.state_dict().items()}


def _optimizer_digest(state: dict[str, Any], names: tuple[str, ...]) -> str:
    return _digest_tensors({
        f"{names[index]}:{key}": tensor
        for index, entry in state["state"].items() for key, tensor in entry.items()
        if isinstance(tensor, Tensor)
    })


def _implementation_digest() -> str:
    root = Path(__file__).resolve().parents[2]
    files = (
        "stpd/workers/public_m0_matched_engine.py", "stpd/workers/token_ranking.py",
        "stpd/workers/checkpoint_codec.py", "stpd/models/stage1a.py",
        "stpd/models/token_core.py", "stpd/models/light_action_encoder.py",
        "stpd/models/losses.py", "stpd/light_action_codec.py",
        "stpd/fullrun/public_m2_sequences.py",
    )
    digest = hashlib.sha256()
    for name in files:
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def _shape(source: PublicM2Input, config: PublicM0MatchedConfig) -> ScratchShape:
    vocab_size = Tokenizer.from_str(source.state_tokenizer.decode()).get_vocab_size()
    expected = ScratchShape(vocab_size, 384, 2, 6, 1536, 0.1, source.max_state_tokens)
    shape = expected if config.shape_override is None else config.shape_override
    if shape.vocab_size != vocab_size or shape.max_tokens != source.max_state_tokens:
        raise BoundaryError(_STAGE, "shape_source_mismatch")
    return shape


def _construct(source: PublicM2Input, config: PublicM0MatchedConfig,
               *, target: torch.device, total_updates: int) -> LightActionM0Scorer:
    shape = _shape(source, config)
    if config.shape_override is None:
        old = LightActionM0Config(
            seed=config.seed, steps=total_updates, learning_rate=config.learning_rate,
            weight_decay=config.weight_decay, gradient_clip=config.gradient_clip,
            device="cpu", max_state_tokens=source.max_state_tokens,
            max_action_bytes=source.max_action_bytes, public_profile="public_compact",
        )
        state_codec = {
            "family": "train-only-byte-bpe", "fit_scope": "exact-frozen-train-membership",
            "sha256": hashlib.sha256(source.state_tokenizer).hexdigest(),
            "vocab_size": shape.vocab_size, "max_tokens": source.max_state_tokens,
        }
        model, _ = construct_model(old, shape.vocab_size, state_codec=state_codec)
        if not isinstance(model, LightActionM0Scorer):
            raise BoundaryError(_STAGE, "m0_scratch_model_required")
    else:
        # Explicit identity-bearing small shape for synthetic engineering tests.
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(config.seed)
            model = LightActionM0Scorer(
                ScratchTokenCore(shape), max_action_bytes=source.max_action_bytes,
                initialization_seed=_scoring_seed(config.seed), public_profile="public_compact",
            )
    return model.to(target)


class PublicM0MatchedEngine:
    """Stateless M0 with complete-chain, fixed-window, decision-mean updates."""

    def __init__(self, source: PublicM2Input, config: PublicM0MatchedConfig) -> None:
        if not isinstance(source, PublicM2Input) or not isinstance(config, PublicM0MatchedConfig):
            raise BoundaryError(_STAGE, "typed_input_required")
        config.validate()
        if torch.get_default_dtype() != torch.float32:
            raise BoundaryError(_STAGE, "float32_required")
        if config.device == "cpu" and torch.get_num_threads() != 1:
            raise BoundaryError(_STAGE, "one_cpu_thread_required")
        if source.identity != config.source_digest:
            raise BoundaryError(_STAGE, "source_identity_mismatch")
        self.source, self.config = source, config
        self.device = _device(config.device, available=True)
        self.train_chains = tuple(chain for chain in source.chains if chain.split == "train")
        self.dev_chains = tuple(chain for chain in source.chains if chain.split == "dev")
        if not self.train_chains or not self.dev_chains:
            raise BoundaryError(_STAGE, "train_dev_required")
        self._preflight_limits()
        self.total_windows = sum(self._window_count(chain) for chain in self.train_chains)
        self.total_updates = self.total_windows * config.epochs
        if self.total_updates > 100_000:
            raise BoundaryError(_STAGE, "update_limit_exceeded")
        self.model = _construct(source, config, target=self.device,
                                total_updates=self.total_updates)
        self.parameters = tuple(parameter for parameter in self.model.parameters()
                                if parameter.requires_grad)
        self.parameter_names = tuple(name for name, parameter in self.model.named_parameters()
                                     if parameter.requires_grad)
        if (not self.parameters or len(self.parameters) != len(self.parameter_names)
                or any(not p.requires_grad for p in self.model.parameters())):
            raise BoundaryError(_STAGE, "scratch_parameter_inventory")
        self._preflight_model_inputs()
        self.optimizer = torch.optim.AdamW(self.parameters, lr=config.learning_rate,
                                          weight_decay=config.weight_decay)
        self._owned = (source, config, self.model, self.optimizer)
        self._optimizer_group = self.optimizer.state_dict()["param_groups"]
        self.runtime = {
            "torch": str(torch.__version__), "python": platform.python_version(),
            "platform": platform.platform(), "cpu_threads": torch.get_num_threads(),
            "dtype": str(torch.get_default_dtype()),
            "cuda_index": self.device.index if self.device.type == "cuda" else None,
            "implementation_sha256": _implementation_digest(),
        }
        cpu = torch.Generator(device="cpu")
        cpu.manual_seed(config.seed ^ 0x4D304D41)
        self._cpu_rng = cpu.get_state()
        self._cuda_rng: Tensor | None = None
        if self.device.type == "cuda":
            cuda = torch.Generator(device=self.device)
            cuda.manual_seed(config.seed ^ 0x4D304D43)
            self._cuda_rng = cuda.get_state()
        self.completed_epochs = self.chain_index = self.window_cursor = 0
        self.label_count = self.optimizer_updates = 0
        self.loss_sum = 0.0
        self._failed = False

    def _window_count(self, chain: PublicM2Chain) -> int:
        return (len(chain.steps) + self.config.window_steps - 1) // self.config.window_steps

    def _preflight_limits(self) -> None:
        steps = tokens = 0
        seen_ids: set[str] = set()
        for chain in self.source.chains:
            if (not isinstance(chain, PublicM2Chain) or not isinstance(chain.chain_id, str)
                    or not chain.chain_id or chain.chain_id in seen_ids
                    or chain.split not in {"train", "dev"}
                    or not isinstance(chain.steps, tuple) or not chain.steps
                    or len(chain.steps) > self.config.max_chain_steps):
                raise BoundaryError(_STAGE, "chain_limit_exceeded")
            seen_ids.add(chain.chain_id)
            steps += len(chain.steps)
            for position, step in enumerate(chain.steps):
                if (not isinstance(step, LightActionM2TrainingStep)
                        or type(step.position) is not int or step.position != position
                        or type(step.reset_before) is not bool
                        or step.reset_before is not (position == 0)
                        or step.previous_actual_action is not None
                        or not isinstance(step.page, tuple) or not step.page
                        or any(type(token) is not int for token in step.page)
                        or not isinstance(step.action_ids, tuple) or not step.action_ids
                        or len(step.action_ids) > self.config.max_actions_per_step
                        or any(type(key) is not str or not key for key in step.action_ids)
                        or len(set(step.action_ids)) != len(step.action_ids)
                        or not isinstance(step.byte_actions, tuple)
                        or len(step.byte_actions) != len(step.action_ids)
                        or any(not isinstance(action, tuple)
                               or any(type(token) is not int for token in action)
                               for action in step.byte_actions)
                        or type(step.target_action_id) is not str
                        or step.action_ids.count(step.target_action_id) != 1):
                    raise BoundaryError(_STAGE, "invalid_observation_step")
                tokens += len(step.page) + sum(map(len, step.byte_actions))
            for start in range(0, len(chain.steps), self.config.window_steps):
                window = chain.steps[start:start + self.config.window_steps]
                if sum(len(step.page) + sum(map(len, step.byte_actions))
                       for step in window) > self.config.max_window_tokens:
                    raise BoundaryError(_STAGE, "window_limit_exceeded")
        if steps > self.config.max_total_steps or tokens > self.config.max_total_input_tokens:
            raise BoundaryError(_STAGE, "total_input_limit_exceeded")

    def _preflight_model_inputs(self) -> None:
        for chain in self.source.chains:
            for step in chain.steps:
                page = torch.tensor(step.page, dtype=torch.long, device=self.device)
                self.model.core.validate_tokens(page)
                for action in step.byte_actions:
                    self.model._validate_action(
                        torch.tensor(action, dtype=torch.long, device=self.device), self.device,
                    )

    @property
    def finished(self) -> bool:
        return self.completed_epochs == self.config.epochs

    def _assert_live(self, *, full: bool = False) -> None:
        if self._failed:
            raise BoundaryError(_STAGE, "failed_engine")
        group = self.optimizer.param_groups[0] if len(self.optimizer.param_groups) == 1 else {}
        bound = (self.source is self._owned[0] and self.config is self._owned[1]
                 and self.model is self._owned[2] and self.optimizer is self._owned[3]
                 and tuple(id(p) for p in group.get("params", ()))
                 == tuple(id(p) for p in self.parameters)
                 and self.optimizer.state_dict()["param_groups"] == self._optimizer_group
                 and str(torch.__version__) == self.runtime["torch"]
                 and torch.get_num_threads() == self.runtime["cpu_threads"]
                 and str(torch.get_default_dtype()) == self.runtime["dtype"])
        if not bound:
            self._failed = True
            raise BoundaryError(_STAGE, "binding_changed")
        if full:
            try:
                unchanged = (self.source.identity == self.config.source_digest
                             and _implementation_digest() == self.runtime["implementation_sha256"])
            except Exception:
                unchanged = False
            if not unchanged:
                self._failed = True
                raise BoundaryError(_STAGE, "identity_changed")

    def _scores(self, step: Any) -> Tensor:
        page = torch.tensor(step.page, dtype=torch.long, device=self.device)
        actions = tuple(torch.tensor(action, dtype=torch.long, device=self.device)
                        for action in step.byte_actions)
        return cast(Tensor, self.model(page, actions))

    def advance_window(self) -> PublicM0WindowProgress:
        self._assert_live()
        if self.finished:
            raise BoundaryError(_STAGE, "exhausted")
        chain = self.train_chains[self.chain_index]
        steps = chain.steps[self.window_cursor:self.window_cursor + self.config.window_steps]
        external_cpu = torch.get_rng_state()
        external_cuda = (torch.cuda.get_rng_state(self.device)
                         if self._cuda_rng is not None else None)
        try:
            torch.set_rng_state(self._cpu_rng)
            if self._cuda_rng is not None:
                torch.cuda.set_rng_state(self._cuda_rng, self.device)
            self.model.train()
            losses = [listwise_rank_loss(self._scores(step),
                                         step.action_ids.index(step.target_action_id))
                      for step in steps]
            values = torch.stack(losses)
            mean = values.mean()
            if not bool(torch.isfinite(mean)):
                raise BoundaryError(_STAGE, "nonfinite_loss")
            self.optimizer.zero_grad(set_to_none=True)
            mean.backward()
            torch.nn.utils.clip_grad_norm_(self.parameters, self.config.gradient_clip,
                                           error_if_nonfinite=True)
            self.optimizer.step()
            if any(not bool(torch.isfinite(parameter).all()) for parameter in self.parameters):
                raise BoundaryError(_STAGE, "nonfinite_parameter")
            self._cpu_rng = torch.get_rng_state().clone()
            if self._cuda_rng is not None:
                self._cuda_rng = torch.cuda.get_rng_state(self.device).clone()
            self.label_count += len(steps)
            self.optimizer_updates += 1
            self.loss_sum += float(values.detach().sum())
            self.window_cursor += len(steps)
            if self.window_cursor == len(chain.steps):
                self.window_cursor = 0
                self.chain_index += 1
                if self.chain_index == len(self.train_chains):
                    self.chain_index = 0
                    self.completed_epochs += 1
            return PublicM0WindowProgress(float(mean.detach()), len(steps), 1,
                                          self.completed_epochs, self.chain_index,
                                          self.window_cursor)
        except Exception:
            self._failed = True
            raise
        finally:
            torch.set_rng_state(external_cpu)
            if external_cuda is not None:
                torch.cuda.set_rng_state(external_cuda, self.device)

    def evaluate_dev(self) -> PublicM0DevMetrics:
        self._assert_live(full=True)
        prior = self.model.training
        total = 0.0
        correct = count = 0
        try:
            self.model.eval()
            with torch.no_grad():
                for chain in self.dev_chains:
                    for step in chain.steps:
                        scores = self._scores(step)
                        target = step.action_ids.index(step.target_action_id)
                        total += float(listwise_rank_loss(scores, target))
                        correct += int(int(scores.argmax()) == target)
                        count += 1
            return PublicM0DevMetrics(total / count, correct / count, count, correct)
        finally:
            self.model.train(prior)

    def _expected_progress(self, epoch: int, chain_index: int,
                           cursor: int) -> tuple[int, int]:
        labels = (epoch * sum(len(chain.steps) for chain in self.train_chains)
                  + sum(len(chain.steps) for chain in self.train_chains[:chain_index]) + cursor)
        updates = (epoch * self.total_windows
                   + sum(self._window_count(chain) for chain in self.train_chains[:chain_index])
                   + (cursor + self.config.window_steps - 1) // self.config.window_steps)
        return labels, updates

    def checkpoint(self) -> bytes:
        self._assert_live(full=True)
        weights = _weights(self.model)
        optimizer = self.optimizer.state_dict()
        cpu_rng = self._cpu_rng.detach().cpu().clone()
        cuda_rng = None if self._cuda_rng is None else self._cuda_rng.detach().cpu().clone()
        rng = {"cpu": cpu_rng, **({} if cuda_rng is None else {"cuda": cuda_rng})}
        return encode_checkpoint({
            "schema": CHECKPOINT_SCHEMA, "config": asdict(self.config),
            "source_digest": self.source.identity, "runtime": self.runtime,
            "completed_epochs": self.completed_epochs, "chain_index": self.chain_index,
            "window_cursor": self.window_cursor, "label_count": self.label_count,
            "optimizer_updates": self.optimizer_updates, "loss_sum": self.loss_sum,
            "model": weights, "model_digest": _digest_tensors(weights),
            "optimizer": optimizer,
            "optimizer_digest": _optimizer_digest(optimizer, self.parameter_names),
            "parameter_names": self.parameter_names, "cpu_rng": cpu_rng,
            "cuda_rng": cuda_rng, "rng_digest": _digest_tensors(rng),
        })

    def restore(self, raw: bytes) -> None:
        self._assert_live(full=True)
        try:
            value = decode_checkpoint(raw)
            if (set(value) != {"schema", "config", "source_digest", "runtime",
                               "completed_epochs", "chain_index", "window_cursor",
                               "label_count", "optimizer_updates", "loss_sum", "model",
                               "model_digest", "optimizer", "optimizer_digest",
                               "parameter_names", "cpu_rng", "cuda_rng", "rng_digest"}
                    or value["schema"] != CHECKPOINT_SCHEMA
                    or value["config"] != asdict(self.config)
                    or value["source_digest"] != self.source.identity
                    or value["runtime"] != self.runtime
                    or value["parameter_names"] != self.parameter_names):
                raise BoundaryError(_STAGE, "checkpoint_identity_mismatch")
            epoch, chain_index, cursor = (value[key] for key in
                                          ("completed_epochs", "chain_index", "window_cursor"))
            if (any(type(item) is not int for item in (epoch, chain_index, cursor))
                    or not 0 <= epoch <= self.config.epochs
                    or not 0 <= chain_index < len(self.train_chains)
                    or epoch == self.config.epochs and (chain_index != 0 or cursor != 0)
                    or epoch < self.config.epochs and cursor not in range(
                        0, len(self.train_chains[chain_index].steps), self.config.window_steps)):
                raise BoundaryError(_STAGE, "checkpoint_progress_mismatch")
            labels, updates = self._expected_progress(epoch, chain_index, cursor)
            if (type(value["label_count"]) is not int or value["label_count"] != labels
                    or type(value["optimizer_updates"]) is not int
                    or value["optimizer_updates"] != updates
                    or type(value["loss_sum"]) not in {int, float}
                    or not math.isfinite(value["loss_sum"]) or value["loss_sum"] < 0):
                raise BoundaryError(_STAGE, "checkpoint_count_mismatch")
            weights = value["model"]
            expected = self.model.state_dict()
            if (not isinstance(weights, dict) or set(weights) != set(expected)
                    or any(not isinstance(weights[name], Tensor)
                           or weights[name].shape != expected[name].shape
                           or weights[name].dtype != expected[name].dtype for name in expected)
                    or value["model_digest"] != _digest_tensors(weights)):
                raise BoundaryError(_STAGE, "checkpoint_model_mismatch")
            optimizer = value["optimizer"]
            if (not isinstance(optimizer, dict) or set(optimizer) != {"state", "param_groups"}
                    or optimizer["param_groups"] != self._optimizer_group
                    or not isinstance(optimizer["state"], dict)
                    or value["optimizer_digest"] != _optimizer_digest(
                        optimizer, self.parameter_names)
                    or set(optimizer["state"]) != (set(range(len(self.parameters)))
                                                   if updates else set())):
                raise BoundaryError(_STAGE, "checkpoint_optimizer_mismatch")
            for index, state in optimizer["state"].items():
                parameter = self.parameters[index]
                if (not isinstance(state, dict) or set(state) != {"step", "exp_avg", "exp_avg_sq"}
                        or any(not isinstance(state[key], Tensor) for key in state)
                        or state["step"].shape != () or state["step"].dtype != torch.float32
                        or state["step"].device.type != "cpu"
                        or float(state["step"]) != updates
                        or any(state[key].shape != parameter.shape
                               or state[key].dtype != parameter.dtype
                               for key in ("exp_avg", "exp_avg_sq"))
                        or bool((state["exp_avg_sq"] < 0).any())):
                    raise BoundaryError(_STAGE, "checkpoint_optimizer_state_mismatch")
            cpu_rng, cuda_rng = value["cpu_rng"], value["cuda_rng"]
            if (not isinstance(cpu_rng, Tensor) or cpu_rng.dtype != torch.uint8
                    or cpu_rng.shape != self._cpu_rng.shape
                    or (self._cuda_rng is None) != (cuda_rng is None)
                    or self._cuda_rng is not None and (
                        not isinstance(cuda_rng, Tensor) or cuda_rng.dtype != torch.uint8
                        or cuda_rng.shape != self._cuda_rng.shape)
                    or value["rng_digest"] != _digest_tensors({
                        "cpu": cpu_rng, **({} if cuda_rng is None else {"cuda": cuda_rng}),
                    })):
                raise BoundaryError(_STAGE, "checkpoint_rng_mismatch")
            torch.Generator(device="cpu").set_state(cpu_rng)
            if cuda_rng is not None:
                torch.Generator(device=self.device).set_state(cuda_rng)
            # No state mutation until the complete checkpoint passes preflight.
            self.model.load_state_dict(weights, strict=True)
            self.optimizer.load_state_dict(optimizer)
            self.completed_epochs, self.chain_index, self.window_cursor = epoch, chain_index, cursor
            self.label_count, self.optimizer_updates, self.loss_sum = labels, updates, float(
                value["loss_sum"])
            self._cpu_rng = cpu_rng.clone()
            self._cuda_rng = None if cuda_rng is None else cuda_rng.clone()
        except Exception:
            self._failed = True
            raise

    def export_weights(self) -> bytes:
        self._assert_live(full=True)
        if (self.completed_epochs not in _EXPORT_EPOCHS
                or self.chain_index != 0 or self.window_cursor != 0):
            raise BoundaryError(_STAGE, "epoch_export_boundary_required")
        weights = _weights(self.model)
        return encode_checkpoint({
            "schema": EXPORT_SCHEMA, "config": asdict(self.config),
            "source_digest": self.source.identity,
            "implementation_sha256": self.runtime["implementation_sha256"],
            "completed_epochs": self.completed_epochs,
            "optimizer_updates": self.optimizer_updates, "run_complete": self.finished,
            "checkpoint_digest": hashlib.sha256(self.checkpoint()).hexdigest(),
            "weights": weights, "weights_digest": _digest_tensors(weights),
        })


def load_public_m0_matched_weights(
    raw: bytes, source: PublicM2Input, config: PublicM0MatchedConfig,
    *, completed_epochs: int, inference_device: str = "cpu",
) -> LightActionM0Scorer:
    """Load an exact epoch export without requiring its training CUDA device."""
    config.validate(require_device=False)
    target = _device(inference_device, available=True)
    try:
        value = decode_checkpoint(raw)
        if (set(value) != {"schema", "config", "source_digest", "implementation_sha256",
                           "completed_epochs", "optimizer_updates", "run_complete",
                           "checkpoint_digest", "weights", "weights_digest"}
                or value["schema"] != EXPORT_SCHEMA
                or value["config"] != asdict(config)
                or value["source_digest"] != source.identity
                or value["implementation_sha256"] != _implementation_digest()
                or type(completed_epochs) is not int or completed_epochs not in _EXPORT_EPOCHS
                or completed_epochs > config.epochs
                or value["completed_epochs"] != completed_epochs
                or value["run_complete"] is not (completed_epochs == config.epochs)
                or type(value["optimizer_updates"]) is not int
                or value["optimizer_updates"] < completed_epochs
                or type(value["checkpoint_digest"]) is not str
                or len(value["checkpoint_digest"]) != 64):
            raise BoundaryError(_STAGE, "export_identity_mismatch")
        windows = sum((len(chain.steps) + config.window_steps - 1) // config.window_steps
                      for chain in source.chains if chain.split == "train")
        model = _construct(source, config, target=target, total_updates=windows * config.epochs)
        weights = value["weights"]
        expected = model.state_dict()
        if (not isinstance(weights, dict) or set(weights) != set(expected)
                or any(not isinstance(weights[name], Tensor)
                       or weights[name].shape != expected[name].shape
                       or weights[name].dtype != expected[name].dtype for name in expected)
                or value["weights_digest"] != _digest_tensors(weights)):
            raise BoundaryError(_STAGE, "export_weights_mismatch")
        model.load_state_dict(weights, strict=True)
        model.eval()
        return model
    except BoundaryError:
        raise
    except Exception as error:
        raise BoundaryError(_STAGE, "invalid_export") from error
