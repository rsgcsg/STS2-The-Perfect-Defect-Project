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

    def __post_init__(self) -> None:
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
        self.freeze_policy(True)

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
        rows = sum(len(e.frames) for e in self.episodes)
        while rows > self.config.max_rows and len(self.episodes) > 1:
            removed = self.episodes.pop(0)
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
                for row, (i, score) in enumerate(zip(indices, scores, strict=True)):
                    values[i] = float(predicted[row, 1])
                    log = score.float().log_softmax(-1)
                    chosen = float(log[episode.actions[i]])
                    ratios[i] = math.exp(chosen - math.log(episode.behavior[i]))
                    old = torch.tensor(
                        episode.old_probabilities[i], device=score.device, dtype=torch.float64
                    )
                    # Actor probabilities are recorded as float64 softmax values.
                    # Preserve zeros as infinite KL if current mass becomes positive.
                    current = log.double().exp()
                    terms = torch.where(current > 0, current * (log.double() - old.log()), 0)
                    acting_kl[i] = float(terms.sum())
        if not all(math.isfinite(v) for v in values + ratios + acting_kl):
            raise BoundaryError("rl_critic", "non_finite_prediction_or_ratio")
        return values, ratios, acting_kl

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
                loss = ((prediction[:, 1].float() - target) ** 2).sum() / len(batch)
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
        valid = [e for e in self.episodes if held_out(e.seed)]
        train_targets = [
            v for e in train for v in terminal_targets(e.reward, len(e.frames), self.config.gamma)
        ]
        per_game = [self.evaluate(e)[0] for e in valid]
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
            "updates": self.updates,
            "revision": self.revision,
            "ready": self.ready,
            "elapsed_seconds": self.elapsed_seconds,
            "last_validation": self.last_validation,
            "episodes": [e.identity() for e in self.episodes],
            "random": [rng[0], list(rng[1]), rng[2]],
        }

    def restore(self, state: dict[str, Any]) -> None:
        current = asdict(self.config)
        mutable = {"warmup_lr", "warmup_min_seconds", "warmup_max_seconds", "checkpoint_seconds"}
        if any(state["config"][k] != current[k] for k in current if k not in mutable):
            raise BoundaryError("rl_critic", "resume_configuration_mismatch")
        self.resumed_config_changes = {k: {"old": state["config"][k], "new": current[k]}
                                       for k in mutable if state["config"][k] != current[k]}
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
