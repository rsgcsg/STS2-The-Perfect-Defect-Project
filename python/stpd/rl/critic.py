"""Shared public-input value head, online warmup and fresh clipped-trace GAE.

The critic sees exactly the policy's Frame. Seeds split complete TRAIN episodes;
terminal outcomes label them but never enter the network. Actor exports keep
only action weights. Fresh trajectories reduce to ordinary PPO/GAE; asynchronous
ones use clipped future importance traces and V-trace(lambda) value targets.
"""

from __future__ import annotations

import collections
import hashlib
import math
import random
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from typing import Any

import torch
from torch import nn

from spireagent.json_boundary import BoundaryError
from stpd.p1.execution import microbatches
from stpd.p1.model import MStd, architecture
from stpd.p1.tensor_input import ModelFrame

from .performance import PerformanceConfig, prepare_models


@dataclass(frozen=True)
class CriticConfig:
    gamma: float = 0.999
    gae_lambda: float = 0.95
    warmup_lr: float = 0.0001
    batch_size: int = 64
    max_rows: int = 60000
    min_train_episodes: int = 32
    min_validation_episodes: int = 8
    min_updates: int = 128
    validation_every: int = 32
    warmup_min_seconds: float = 3600.0
    warmup_max_seconds: float = 7200.0
    checkpoint_seconds: float = 600.0
    min_explained_variance: float = 0.0
    max_grad_norm: float = 1.0
    normalize_targets: bool = False
    target_ema: float = 0.01
    target_std_floor: float = 0.01
    head_lr: float | None = None
    fixed_validation: bool = False

    def __post_init__(self) -> None:
        if (type(self.normalize_targets) is not bool or type(self.fixed_validation) is not bool
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                       for v in (self.target_ema, self.target_std_floor))
                or self.target_ema > 1
                or (self.head_lr is not None and
                    (type(self.head_lr) not in (int, float)
                     or not math.isfinite(self.head_lr) or self.head_lr <= 0))):
            raise BoundaryError("rl_critic", "invalid_target_scaling")
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 1
            for v in (self.gamma, self.gae_lambda)
        ):
            raise BoundaryError("rl_critic", "invalid_discount_or_lambda")
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or v <= 0
            for v in (
                self.warmup_lr,
                self.warmup_max_seconds,
                self.checkpoint_seconds,
                self.max_grad_norm,
            )
        ):
            raise BoundaryError("rl_critic", "invalid_positive_number")
        if (
            type(self.warmup_min_seconds) not in (int, float)
            or not math.isfinite(self.warmup_min_seconds)
            or not 0 <= self.warmup_min_seconds < self.warmup_max_seconds
        ):
            raise BoundaryError("rl_critic", "invalid_warmup_time")
        if any(
            type(v) is not int or v < 1
            for v in (
                self.batch_size,
                self.max_rows,
                self.min_train_episodes,
                self.min_validation_episodes,
                self.min_updates,
                self.validation_every,
            )
        ):
            raise BoundaryError("rl_critic", "invalid_positive_integer")
        if self.max_rows < self.batch_size or not 0 <= self.min_explained_variance < 1:
            raise BoundaryError("rl_critic", "invalid_warmup_bounds")


def enable_value_head(model: MStd, seed: int) -> None:
    """Reuse the existing value-head architecture; never load the historical heads."""
    if model.value_heads:
        raise BoundaryError("rl_critic", "action_only_start_package_required")
    device = model.ids.weight.device
    with torch.random.fork_rng(
        devices=[model.ids.weight.get_device()] if device.type == "cuda" else []
    ):
        torch.manual_seed(seed + 7919)
        model.win_head = nn.Linear(192, 1, device=device)
        model.progress_head = nn.Linear(192, 1, device=device)
        for head in (model.win_head, model.progress_head):
            nn.init.zeros_(head.weight)
            nn.init.zeros_(head.bias)
    model.value_heads = True
    model.architecture = architecture(True, model.model_size)


def held_out(seed: str) -> bool:
    """10% of TRAIN seeds: no value OR policy updates, including after warmup."""
    digest = hashlib.sha256(("rl3-critic-validation-v1:" + seed).encode()).hexdigest()
    return int(digest[:8], 16) % 10 == 0


def optimizer_groups(model: MStd, config: CriticConfig | None) -> Any:
    if config is None or config.head_lr is None:
        return model.parameters()
    head = list(model.progress_head.parameters())
    head_ids = {id(p) for p in head}
    return [{"params": [p for p in model.parameters() if id(p) not in head_ids]},
            {"params": head, "fixed_lr": config.head_lr, "lr": config.head_lr}]


def restore_optimizer(optimizer: torch.optim.Optimizer, model: MStd,
                      state: dict[str, Any], *, migrate: bool) -> None:
    """Explicit legacy single-group -> independent head LR; preserve Adam moments."""
    fixed = [group.get("fixed_lr") for group in optimizer.param_groups]
    if len(state["param_groups"]) != len(optimizer.param_groups):
        if not migrate or len(state["param_groups"]) != 1 or len(optimizer.param_groups) != 2:
            raise BoundaryError("rl_critic", "optimizer_group_migration_not_declared")
        old_ids = state["param_groups"][0]["params"]
        parameters = list(model.parameters())
        if len(old_ids) != len(parameters):
            raise BoundaryError("rl_critic", "optimizer_parameter_migration_mismatch")
        current = optimizer.state_dict()
        new_ids = {id(p): key for group, saved in zip(optimizer.param_groups,
                    current["param_groups"], strict=True)
                   for p, key in zip(group["params"], saved["params"], strict=True)}
        mapping = {key: new_ids[id(p)] for key, p in zip(old_ids, parameters, strict=True)}
        groups = [{**state["param_groups"][0], **group} for group in current["param_groups"]]
        state = {"state": {mapping[k]: v for k, v in state["state"].items()},
                 "param_groups": groups}
    optimizer.load_state_dict(state)
    for group, lr in zip(optimizer.param_groups, fixed, strict=True):
        if lr is not None:
            group["fixed_lr"] = group["lr"] = lr


def terminal_targets(reward: float, count: int, gamma: float) -> list[float]:
    return [reward * gamma ** (count - 1 - i) for i in range(count)]


def trace_gae(
    reward: float, values: list[float], ratios: list[float], gamma: float, lam: float
) -> tuple[list[float], list[float]]:
    """Natural-terminal sparse reward, no bootstrap; rho_bar=c_bar=1.

    A_t = delta_t + gamma*lambda*min(1,rho_{t+1})*A_{t+1}
    v_t = V_t + min(1,rho_t)*A_t.
    Thus v is the V-trace(lambda) target. The PPO ratio already supplies the
    current-action importance factor, so A is NOT multiplied by rho_t again.
    On-policy rho=1 gives standard GAE and its lambda-return value target.
    """
    if len(values) != len(ratios) or any(not math.isfinite(r) or r <= 0 for r in ratios):
        raise BoundaryError("rl_critic", "invalid_trace_ratios")
    advantages, targets = [0.0] * len(values), [0.0] * len(values)
    following_value = following_advantage = 0.0
    following_rho = 1.0
    for i in range(len(values) - 1, -1, -1):
        delta = (reward if i == len(values) - 1 else 0.0) + gamma * following_value - values[i]
        advantage = delta + gamma * lam * following_rho * following_advantage
        advantages[i] = advantage
        targets[i] = values[i] + min(1.0, ratios[i]) * advantage
        following_value, following_advantage = values[i], advantage
        following_rho = min(1.0, ratios[i])
    return advantages, targets


@dataclass
class CriticEpisode:
    run_id: str
    shard: str
    seed: str
    reward: float
    version: int
    frames: list[ModelFrame]
    actions: list[int]
    behavior: list[float]
    old_probabilities: list[list[float]]
    keys: list[str]
    started_at: float

    def identity(self) -> dict[str, Any]:
        return {
            key: getattr(self, key)
            for key in ("run_id", "shard", "seed", "reward", "version", "started_at")
        }


class OnlineCritic:
    def __init__(
        self,
        model: MStd,
        reference: MStd,
        optimizer: torch.optim.Optimizer,
        config: CriticConfig,
        seed: int,
        performance: PerformanceConfig | None,
    ) -> None:
        self.model, self.reference, self.optimizer = model, reference, optimizer
        self.config, self.performance = config, performance
        self.random = random.Random(seed + 7919)
        self.episodes: list[CriticEpisode] = []
        self.updates = self.revision = 0
        self.ready = False
        self.elapsed_seconds = 0.0
        self.last_validation: dict[str, Any] | None = None
        self.target_mean = 0.0
        self.target_second = 1.0
        self.target_batches = 0
        self.fixed_validation_ids: list[str] = []
        self.fixed_validation_initialized = False
        self.freeze_policy(True)

    def target_scale(self, targets: list[float]) -> float:
        """TRAIN targets only; raw model outputs, rewards and GAE never change units.

        This scales the squared residual, not the output parameters (not PopArt).
        The unweighted batch EMA and its state survive a normal checkpoint.
        """
        if not self.config.normalize_targets:
            return 1.0
        mean = sum(targets) / len(targets)
        second = sum(t * t for t in targets) / len(targets)
        beta = self.config.target_ema if self.target_batches else 1.0
        self.target_mean += beta * (mean - self.target_mean)
        self.target_second += beta * (second - self.target_second)
        self.target_batches += 1
        return self.target_std

    @property
    def target_std(self) -> float:
        return max(self.config.target_std_floor,
                   math.sqrt(max(0.0, self.target_second - self.target_mean ** 2)))

    def freeze_validation(self) -> None:
        if self.config.fixed_validation and not self.fixed_validation_initialized:
            self.fixed_validation_ids = [e.run_id for e in self.episodes if held_out(e.seed)]
            if len({e.seed for e in self.episodes if e.run_id in self.fixed_validation_ids}) \
                    < self.config.min_validation_episodes:
                self.fixed_validation_ids = []
                if not self.ready:
                    return  # early warmup restore continues collecting the cohort
                raise BoundaryError("rl_critic", "fixed_validation_not_enough_seeds")
            self.fixed_validation_initialized = True

    def freeze_policy(self, warmup: bool) -> None:
        for name, parameter in self.model.named_parameters():
            parameter.requires_grad_(
                name.startswith("progress_head.") if warmup else not name.startswith("win_head.")
            )

    def execution(self) -> Any:
        return (
            prepare_models(self.model, self.reference, self.performance)
            if self.performance is not None
            else nullcontext()
        )

    def add(self, episode: CriticEpisode) -> list[str]:
        self.episodes.append(episode)
        self.revision += 1
        evicted = []
        rows = sum(len(e.frames) for e in self.episodes
                   if e.run_id not in self.fixed_validation_ids)
        while (rows > self.config.max_rows and
               sum(e.run_id not in self.fixed_validation_ids for e in self.episodes) > 1):
            index = next(i for i, e in enumerate(self.episodes)
                         if e.run_id not in self.fixed_validation_ids)
            removed = self.episodes.pop(index)
            rows -= len(removed.frames)
            evicted.append(removed.run_id)
        return evicted

    def evaluate(self, episode: CriticEpisode) -> tuple[list[float], list[float], list[float]]:
        values = [0.0] * len(episode.frames)
        ratios = [0.0] * len(episode.frames)
        acting_kl = [0.0] * len(episode.frames)
        self.model.eval()  # actor-mode distributions; gradients are enabled only in update
        with self.execution(), torch.no_grad():
            for indices in microbatches(episode.frames, list(range(len(values))), self.model):
                scores, predicted = self.model.forward_with_values(
                    [episode.frames[i] for i in indices]
                )
                logits = nn.utils.rnn.pad_sequence(
                    [score.float() for score in scores], batch_first=True,
                    padding_value=-torch.inf)
                logs = logits.log_softmax(-1)
                actions = torch.tensor([episode.actions[i] for i in indices],
                                       device=logs.device)
                chosen_logs = logs.gather(1, actions[:, None]).squeeze(1)
                width = logs.shape[1]
                old = torch.tensor([episode.old_probabilities[i] +
                    [0.0] * (width - len(episode.old_probabilities[i])) for i in indices],
                    device=logs.device, dtype=torch.float64)
                # Actor probabilities are float64. Padding contributes zero;
                # a real zero with positive current mass retains infinite KL.
                current = logs.double().exp()
                terms = torch.where(current > 0, current * (logs.double() - old.log()), 0)
                kl_rows = terms.sum(-1)
                # One D2H transfer per microbatch, never float(CUDA) per row.
                packed = torch.stack((predicted[:, 1].double(),
                                      chosen_logs.double(), kl_rows), dim=1).cpu().tolist()
                for i, (value, chosen, kl) in zip(indices, packed, strict=True):
                    values[i], acting_kl[i] = value, kl
                    ratios[i] = math.exp(chosen - math.log(episode.behavior[i]))
        if not all(math.isfinite(v) for v in values + ratios + acting_kl):
            raise BoundaryError("rl_critic", "non_finite_prediction_or_ratio")
        return values, ratios, acting_kl

    def values(self, episode: CriticEpisode) -> list[float]:
        output = [0.0] * len(episode.frames)
        self.model.eval()
        with self.execution(), torch.no_grad():
            for indices in microbatches(episode.frames, list(range(len(output))), self.model):
                predictions = self.model.forward_values([episode.frames[i] for i in indices])
                for i, value in zip(indices, predictions[:, 1].cpu().tolist(), strict=True):
                    output[i] = value
        if not all(math.isfinite(v) for v in output):
            raise BoundaryError("rl_critic", "non_finite_prediction")
        return output

    def warmup_update(self) -> dict[str, Any] | None:
        if self.ready:
            return None
        rows = [
            (frame, target, key)
            for e in self.episodes
            if not held_out(e.seed)
            for frame, target, key in zip(
                e.frames,
                terminal_targets(e.reward, len(e.frames), self.config.gamma),
                e.keys,
                strict=True,
            )
        ]
        if len(rows) < self.config.batch_size:
            return None
        batch = self.random.sample(rows, self.config.batch_size)
        scale = self.target_scale([r[1] for r in batch])
        frames = [r[0] for r in batch]
        for group in self.optimizer.param_groups:
            group["lr"] = self.config.warmup_lr
        self.optimizer.zero_grad(set_to_none=True)
        self.model.eval()
        total = 0.0
        with self.execution():
            for indices in microbatches(frames, list(range(len(frames))), self.model):
                _, prediction = self.model.forward_with_values([frames[i] for i in indices])
                target = prediction.new_tensor([batch[i][1] for i in indices])
                loss = (((prediction[:, 1].float() - target) / scale) ** 2).sum() / len(batch)
                total += float(loss.detach())
                loss.backward()
        norm = float(nn.utils.clip_grad_norm_(self.model.parameters(), self.config.max_grad_norm))
        if not math.isfinite(total) or not math.isfinite(norm):
            raise BoundaryError("rl_critic", "non_finite_update")
        self.optimizer.step()
        self.updates += 1
        self.revision += 1
        return {
            "critic_mse": total,
            "critic_gradient_norm": norm,
            "critic_updates": self.updates,
            "ids": [r[2] for r in batch],
        }

    def validate(self) -> dict[str, Any]:
        train = [e for e in self.episodes if not held_out(e.seed)]
        if (self.config.fixed_validation and not self.fixed_validation_initialized
                and len({e.seed for e in self.episodes if held_out(e.seed)})
                >= self.config.min_validation_episodes):
            self.freeze_validation()
        valid = [e for e in self.episodes if (e.run_id in self.fixed_validation_ids
                 if self.config.fixed_validation else held_out(e.seed))]
        train_targets = [
            v for e in train for v in terminal_targets(e.reward, len(e.frames), self.config.gamma)
        ]
        per_game = [self.values(e) for e in valid]
        targets = [
            v for e in valid for v in terminal_targets(e.reward, len(e.frames), self.config.gamma)
        ]
        predictions = [v for values in per_game for v in values]
        train_seeds, validation_seeds = len({e.seed for e in train}), len({e.seed for e in valid})
        enough = (
            train_seeds >= self.config.min_train_episodes
            and validation_seeds >= self.config.min_validation_episodes
            and self.updates >= self.config.min_updates
        )
        report: dict[str, Any] = {
            "train_episodes": len(train),
            "validation_kind": "fixed" if self.config.fixed_validation else "rolling",
            "validation_run_ids": [e.run_id for e in valid],
            "target_std": self.target_std if self.config.normalize_targets else 1.0,
            "validation_episodes": len(valid),
            "train_seeds": train_seeds,
            "validation_seeds": validation_seeds,
            "validation_versions": dict(collections.Counter(str(e.version) for e in valid)),
            "validation_rows": len(targets),
            "updates": self.updates,
            "enough": enough,
            "passed": False,
            "explained_variance": None,
            "mse": None,
            "constant_mse": None,
        }
        if targets and train_targets:
            y, p = (
                torch.tensor(targets, dtype=torch.float64),
                torch.tensor(predictions, dtype=torch.float64),
            )
            variance = float(y.var(unbiased=False))
            mse = float(((p - y) ** 2).mean())
            constant = sum(train_targets) / len(train_targets)
            baseline = float(((y - constant) ** 2).mean())
            ev = 1 - float((y - p).var(unbiased=False)) / variance if variance > 1e-12 else None
            report.update(
                mse=mse,
                constant_mse=baseline,
                explained_variance=ev,
                passed=bool(
                    enough
                    and ev is not None
                    and ev > self.config.min_explained_variance
                    and mse < baseline
                ),
            )
            games: list[dict[str, Any]] = []
            buckets: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
            for e, values in zip(valid, per_game, strict=True):
                truth = terminal_targets(e.reward, len(e.frames), self.config.gamma)
                games.append(
                    {
                        "run_id": e.run_id,
                        "rows": len(truth),
                        "mse": sum((p - t) ** 2 for p, t in zip(values, truth, strict=True))
                        / len(truth),
                    }
                )
                for i, (prediction, target) in enumerate(zip(values, truth, strict=True)):
                    label = ("early", "middle", "late")[min(2, i * 3 // len(truth))]
                    buckets[label].append((prediction, target))
            report["games"] = games
            report["equal_game_mse"] = sum(g["mse"] for g in games) / len(games)
            report["decision_position_thirds"] = {
                name: {
                    "rows": len(rows),
                    "mse": sum((p - t) ** 2 for p, t in rows) / len(rows),
                    "prediction_mean": sum(p for p, _ in rows) / len(rows),
                    "target_mean": sum(t for _, t in rows) / len(rows),
                }
                for name, rows in buckets.items()
            }
        self.last_validation = report
        return report

    def state(self) -> dict[str, Any]:
        rng = self.random.getstate()
        return {
            "config": asdict(self.config),
            "target_statistics": {"mean": self.target_mean, "second": self.target_second,
                                  "batches": self.target_batches},
            "fixed_validation_ids": self.fixed_validation_ids,
            "fixed_validation_initialized": self.fixed_validation_initialized,
            "updates": self.updates,
            "revision": self.revision,
            "ready": self.ready,
            "elapsed_seconds": self.elapsed_seconds,
            "last_validation": self.last_validation,
            "episodes": [e.identity() for e in self.episodes],
            "random": [rng[0], list(rng[1]), rng[2]],
        }

    def restore(self, state: dict[str, Any], changes: dict[str, Any] | None = None) -> None:
        current = asdict(self.config)
        saved_config = {**asdict(CriticConfig()), **state["config"]}
        mutable = {"warmup_lr", "warmup_min_seconds", "warmup_max_seconds", "checkpoint_seconds"}
        changed = {k: {"old": saved_config[k], "new": current[k]}
                   for k in current if saved_config[k] != current[k] and k not in mutable}
        if changed != (changes or {}):
            raise BoundaryError("rl_critic", "resume_configuration_mismatch")
        self.resumed_config_changes = {k: {"old": saved_config[k], "new": current[k]}
                                       for k in current if saved_config[k] != current[k]}
        stats = state.get("target_statistics", {"mean": 0.0, "second": 1.0, "batches": 0})
        self.target_mean, self.target_second, self.target_batches = (
            stats["mean"], stats["second"], stats["batches"])
        self.fixed_validation_ids = state.get("fixed_validation_ids", [])
        self.fixed_validation_initialized = state.get("fixed_validation_initialized", False)
        self.updates, self.ready, self.revision = (
            state["updates"],
            state["ready"],
            state["revision"],
        )
        self.elapsed_seconds, self.last_validation = (
            state["elapsed_seconds"],
            state["last_validation"],
        )
        self.random.setstate((state["random"][0], tuple(state["random"][1]), state["random"][2]))
        self.freeze_policy(not self.ready)
