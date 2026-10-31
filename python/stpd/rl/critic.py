"""Optional, independent public-input return critic for the existing RL learner.

The actor and its package are unchanged. This predicts discounted reward-v2 return,
not a win probability. Natural complete episodes alone provide terminal targets;
hash-held-out training seeds never fit the critic. No episode metadata enters its
network: only the same projected Frame used by the policy.
"""
from __future__ import annotations

import collections
import copy
import hashlib
import math
import random
from dataclasses import asdict, dataclass
from typing import Any

import torch
from torch import nn

from spireagent.json_boundary import BoundaryError
from stpd.p1.execution import microbatches
from stpd.p1.model import MStd
from stpd.p1.tensor_input import ModelFrame


@dataclass(frozen=True)
class CriticConfig:
    gamma: float = 1.0
    gae_lambda: float = 1.0
    lr: float = 0.0001
    batch_size: int = 64
    max_rows: int = 60000
    min_train_episodes: int = 32
    min_validation_episodes: int = 8
    min_updates: int = 128
    validation_every: int = 32
    warmup_max_updates: int = 2000
    warmup_seconds: float = 2400.0
    min_explained_variance: float = 0.05
    max_grad_norm: float = 1.0

    def __post_init__(self) -> None:
        for value in (self.gamma, self.gae_lambda):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1:
                raise BoundaryError("rl_critic", "invalid_discount_or_lambda")
        for value in (self.lr, self.warmup_seconds, self.max_grad_norm):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise BoundaryError("rl_critic", "invalid_positive_number")
        for value in (self.batch_size, self.max_rows, self.min_train_episodes,
                      self.min_validation_episodes, self.min_updates, self.validation_every,
                      self.warmup_max_updates):
            if type(value) is not int or value < 1:
                raise BoundaryError("rl_critic", "invalid_positive_integer")
        if (self.max_rows < self.batch_size or self.min_updates > self.warmup_max_updates
                or not 0 <= self.min_explained_variance < 1):
            raise BoundaryError("rl_critic", "invalid_warmup_bounds")


def held_out(seed: str) -> bool:
    """Stable by seed across runs/replicates; these are TRAIN seeds, never evaluation."""
    digest = hashlib.sha256(("rl-critic-validation-v1:" + seed).encode()).hexdigest()
    return int(digest[:8], 16) % 5 == 0


def terminal_targets(reward: float, count: int, gamma: float) -> list[float]:
    return [reward * gamma ** (count - 1 - i) for i in range(count)]


def gae(reward: float, values: list[float], gamma: float, lam: float) -> list[float]:
    """Sparse terminal reward, natural terminal V=0; never bootstrap an unknown."""
    result = [0.0] * len(values)
    following = advantage = 0.0
    for i in range(len(values) - 1, -1, -1):
        delta = (reward if i == len(values) - 1 else 0.0) + gamma * following - values[i]
        advantage = delta + gamma * lam * advantage
        result[i] = advantage
        following = values[i]
    return result


class ReturnNetwork(nn.Module):
    def __init__(self, policy: MStd) -> None:
        super().__init__()
        self.encoder = copy.deepcopy(policy)
        # Existing exported policy packages have no historical T2 value heads.
        self.readout = nn.Sequential(nn.Linear(192, 128), nn.Tanh(), nn.Linear(128, 1))
        nn.init.zeros_(self.readout[-1].weight)
        nn.init.zeros_(self.readout[-1].bias)
        self.to(policy.ids.weight.device)
        self.eval()  # gradients still enabled; deterministic critic, no dropout/id masking

    def forward(self, frames: list[ModelFrame]) -> torch.Tensor:
        _, current = self.encoder._forward_batch(frames)
        return self.readout(current).squeeze(-1)


@dataclass
class CriticEpisode:
    run_id: str
    shard: str
    seed: str
    reward: float
    version: int
    frames: list[ModelFrame]
    keys: list[str]

    def identity(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in ("run_id", "shard", "seed", "reward", "version")}


class OnlineCritic:
    def __init__(self, policy: MStd, config: CriticConfig, seed: int) -> None:
        self.config = config
        # Adding the critic must not consume the policy's dropout RNG stream.
        device = policy.ids.weight.device
        devices = [device.index] if device.type == "cuda" else []
        with torch.random.fork_rng(devices=devices):
            torch.manual_seed(seed + 7919)
            self.network = ReturnNetwork(policy)
        self.optimizer = torch.optim.AdamW(self.network.parameters(), lr=config.lr, weight_decay=0)
        self.random = random.Random(seed + 7919)
        self.episodes: list[CriticEpisode] = []
        self.updates = 0
        self.revision = 0
        self.ready = False
        self.elapsed_seconds = 0.0
        self.last_validation: dict[str, Any] | None = None

    def add(self, episode: CriticEpisode) -> None:
        self.episodes.append(episode)
        self.revision += 1
        # Whole-episode FIFO: never turn a truncated prefix into a terminal target.
        rows = sum(len(e.frames) for e in self.episodes)
        while rows > self.config.max_rows and len(self.episodes) > 1:
            rows -= len(self.episodes.pop(0).frames)

    def predict(self, frames: list[ModelFrame]) -> list[float]:
        values = []
        self.network.eval()
        with torch.no_grad():
            for indices in microbatches(frames, list(range(len(frames))), self.network.encoder):
                values.extend(self.network([frames[i] for i in indices]).cpu().tolist())
        if not all(math.isfinite(v) for v in values):
            raise BoundaryError("rl_critic", "non_finite_prediction")
        return values

    def update(self) -> dict[str, Any] | None:
        train = [e for e in self.episodes if not held_out(e.seed)]
        rows = [(frame, target, key) for e in train for frame, target, key in zip(
            e.frames, terminal_targets(e.reward, len(e.frames), self.config.gamma),
            e.keys, strict=True)]
        if len(rows) < self.config.batch_size:
            return None
        batch = self.random.sample(rows, self.config.batch_size)
        frames = [r[0] for r in batch]
        self.network.eval()
        self.optimizer.zero_grad(set_to_none=True)
        total = 0.0
        for indices in microbatches(frames, list(range(len(frames))), self.network.encoder):
            predictions = self.network([frames[i] for i in indices])
            target = predictions.new_tensor([batch[i][1] for i in indices])
            loss = ((predictions - target) ** 2).sum() / len(batch)
            total += float(loss.detach())
            loss.backward()
        norm = float(nn.utils.clip_grad_norm_(self.network.parameters(), self.config.max_grad_norm))
        if not math.isfinite(total) or not math.isfinite(norm):
            raise BoundaryError("rl_critic", "non_finite_update")
        self.optimizer.step()
        self.updates += 1
        self.revision += 1
        return {"critic_mse": total, "critic_gradient_norm": norm,
                "critic_updates": self.updates, "ids": [row[2] for row in batch]}

    def validate(self) -> dict[str, Any]:
        train = [e for e in self.episodes if not held_out(e.seed)]
        valid = [e for e in self.episodes if held_out(e.seed)]
        train_targets = [v for e in train for v in terminal_targets(
            e.reward, len(e.frames), self.config.gamma)]
        targets = [v for e in valid for v in terminal_targets(
            e.reward, len(e.frames), self.config.gamma)]
        per_game = [self.predict(e.frames) for e in valid]
        predictions = [v for values in per_game for v in values]
        train_seeds, validation_seeds = len({e.seed for e in train}), len({e.seed for e in valid})
        enough = (train_seeds >= self.config.min_train_episodes
                  and validation_seeds >= self.config.min_validation_episodes
                  and self.updates >= self.config.min_updates)
        report: dict[str, Any] = {"train_episodes": len(train), "validation_episodes": len(valid),
            "train_seeds": train_seeds, "validation_seeds": validation_seeds,
            "validation_versions": dict(collections.Counter(str(e.version) for e in valid)),
            "validation_rows": len(targets), "updates": self.updates, "enough": enough,
            "passed": False, "explained_variance": None, "mse": None, "constant_mse": None}
        if targets and train_targets:
            y = torch.tensor(targets, dtype=torch.float64)
            p = torch.tensor(predictions, dtype=torch.float64)
            variance = float(y.var(unbiased=False))
            mse = float(((p - y) ** 2).mean())
            constant = sum(train_targets) / len(train_targets)
            baseline = float(((y - constant) ** 2).mean())
            ev = 1 - float((y - p).var(unbiased=False)) / variance if variance > 1e-12 else None
            report.update(mse=mse, constant_mse=baseline, explained_variance=ev,
                passed=bool(enough and ev is not None
                    and ev > self.config.min_explained_variance and mse < baseline))
            # Equal-game metrics expose the effect of unequal episode lengths.
            games = []
            buckets: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
            for e, values in zip(valid, per_game, strict=True):
                truth = terminal_targets(e.reward, len(e.frames), self.config.gamma)
                games.append({"run_id": e.run_id, "rows": len(truth),
                    "mse": sum((p - t) ** 2 for p, t in zip(values, truth, strict=True))
                           / len(truth)})
                for i, (prediction, target) in enumerate(zip(values, truth, strict=True)):
                    label = ("early", "middle", "late")[min(2, i * 3 // len(truth))]
                    buckets[label].append((prediction, target))
            report["games"] = games
            report["equal_game_mse"] = sum(g["mse"] for g in games) / len(games)
            report["decision_position_thirds"] = {name: {
                "rows": len(rows), "mse": sum((p - t) ** 2 for p, t in rows) / len(rows),
                "prediction_mean": sum(p for p, _ in rows) / len(rows),
                "target_mean": sum(t for _, t in rows) / len(rows)}
                for name, rows in buckets.items()}
        self.last_validation = report
        return report

    def state(self) -> dict[str, Any]:
        rng = self.random.getstate()
        return {"config": asdict(self.config), "updates": self.updates,
            "revision": self.revision, "ready": self.ready,
            "elapsed_seconds": self.elapsed_seconds, "last_validation": self.last_validation,
            "episodes": [e.identity() for e in self.episodes],
            "random": [rng[0], list(rng[1]), rng[2]]}

    def restore(self, state: dict[str, Any]) -> None:
        if state["config"] != asdict(self.config):
            raise BoundaryError("rl_critic", "resume_configuration_mismatch")
        self.updates, self.ready = state["updates"], state["ready"]
        self.revision = state["revision"]
        self.elapsed_seconds = state["elapsed_seconds"]
        self.last_validation = state["last_validation"]
        self.random.setstate((state["random"][0], tuple(state["random"][1]), state["random"][2]))
