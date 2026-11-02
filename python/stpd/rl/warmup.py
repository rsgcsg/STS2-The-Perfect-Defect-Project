"""Bounded frozen-trunk RL4 head warmup over verified TRAIN scene features.

Features come from MStd.value_features on the normal public-input reader, once
with the selected frozen starting trunk. They never include terminal/HP labels
as model inputs. This module owns numerical fitting, not storage or admission.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import random
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, overload

import torch
from torch import Tensor
from torch.nn import functional as F

from stpd.p1.model import MStd

from .heads import (
    OUTCOME_CLASSES,
    checked_terminal_class,
    class_reward_values,
    outcome_cross_entropy,
    outcome_mask,
)


@dataclass(frozen=True)
class WarmupExample:
    seed: str
    scene: Tensor
    mean: Tensor
    floor: int
    outcome: int | None = None
    hp: float | None = None  # absolute post-combat HP / 100, including native healing

    def __post_init__(self) -> None:
        if (not self.seed or self.scene.ndim != 2 or self.scene.shape[1] != 192
                or not len(self.scene) or self.mean.shape != (192,)
                or not bool(torch.isfinite(self.scene).all())
                or not bool(torch.isfinite(self.mean).all())
                or type(self.floor) is not int or self.floor < 0
                or (self.outcome is not None and (type(self.outcome) is not int
                    or not 0 <= self.outcome < OUTCOME_CLASSES
                    or not bool(outcome_mask([self.floor])[0, self.outcome])))
                or (self.hp is not None and (not math.isfinite(self.hp) or self.hp < 0))
                or (self.outcome is None and self.hp is None)):
            raise ValueError("invalid_warmup_example")
        if self.scene.requires_grad or self.mean.requires_grad:
            raise ValueError("warmup_requires_frozen_features")


@dataclass(frozen=True)
class WarmupConfig:
    seed: int = 20261103
    learning_rate: float = 3e-4
    max_epochs: int = 3
    max_seconds: float = 2700
    batch_size: int = 16
    max_grad_norm: float = 1.0

    def __post_init__(self) -> None:
        if (type(self.max_epochs) is not int or not 1 <= self.max_epochs <= 3
                or type(self.batch_size) is not int or self.batch_size < 1
                or not all(math.isfinite(v) and v > 0 for v in (
                    self.learning_rate, self.max_seconds, self.max_grad_norm))
                or self.max_seconds > 2700):
            raise ValueError("invalid_warmup_bounds")


def validation_seed(seed: str) -> bool:
    """New 10% whole-seed DEV split inside the already admitted RL3 TRAIN set."""
    value = hashlib.sha256(("rl4-warmup-v1:" + seed).encode()).hexdigest()
    return int(value[:8], 16) % 10 == 0


class FeatureExamples(Sequence[WarmupExample]):
    """One verified per-game feature block in memory; immutable original references."""

    def __init__(self, manifest: Path, expected_package_sha256: str) -> None:
        value = json.loads(manifest.read_bytes())
        if value["model_package_sha256"] != expected_package_sha256:
            raise ValueError("warmup_feature_trunk_mismatch")
        self.terminal_encoding = value.get("terminal_label_encoding", "rl4-class-1")
        if self.terminal_encoding not in {"floor-win-1", "rl4-class-1"}:
            raise ValueError("unsupported_warmup_terminal_label_encoding")
        self.root = manifest.parent
        self.records = value["episodes"]
        self.ends: list[int] = []
        count = 0
        for record in self.records:
            if (type(record["rows"]) is not int or not 1 <= record["rows"] <= 64
                    or not isinstance(record["seed"], str) or not record["seed"]):
                raise ValueError("invalid_warmup_feature_record")
            count += record["rows"]
            self.ends.append(count)
        self._current = -1
        self._block: dict[str, Any] = {}

    def __len__(self) -> int:
        return self.ends[-1] if self.ends else 0

    def seed_at(self, index: int) -> str:
        return str(self.records[bisect.bisect_right(self.ends, index)]["seed"])

    @overload
    def __getitem__(self, index: int) -> WarmupExample: ...
    @overload
    def __getitem__(self, index: slice) -> Sequence[WarmupExample]: ...
    def __getitem__(self, index: int | slice) -> WarmupExample | Sequence[WarmupExample]:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        block_index = bisect.bisect_right(self.ends, index)
        if block_index != self._current:
            from stpd.workers.checkpoint_codec import decode_checkpoint

            record = self.records[block_index]
            raw = (self.root / record["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError("warmup_feature_hash_mismatch")
            block = decode_checkpoint(raw)
            count = record["rows"]
            if (block["seed"] != record["seed"] or len(block["scene"]) != count
                    or len(block["mean"]) != count or len(block["floors"]) != count
                    or len(block["hp"]) != count):
                raise ValueError("warmup_feature_block_mismatch")
            self._block, self._current = block, block_index
        local = index - (self.ends[block_index - 1] if block_index else 0)
        block = self._block
        label = block["outcome_class"]
        if self.terminal_encoding == "floor-win-1":
            label = checked_terminal_class(result="win" if label == 49 else "loss",
                floor=48 if label == 49 else label, reward=block["reward"])
        return WarmupExample(block["seed"], block["scene"][local], block["mean"][local],
            block["floors"][local], label, block["hp"][local])


class _IndexedExamples(Sequence[WarmupExample]):
    def __init__(self, examples: Sequence[WarmupExample], indices: list[int]) -> None:
        self.examples, self.indices = examples, indices

    def __len__(self) -> int:
        return len(self.indices)

    def seed_at(self, index: int) -> str:
        return _seed(self.examples, self.indices[index])

    @overload
    def __getitem__(self, index: int) -> WarmupExample: ...
    @overload
    def __getitem__(self, index: slice) -> Sequence[WarmupExample]: ...
    def __getitem__(self, index: int | slice) -> WarmupExample | Sequence[WarmupExample]:
        if isinstance(index, slice):
            return [self.examples[i] for i in self.indices[index]]
        return self.examples[self.indices[index]]


def _seed(examples: Sequence[WarmupExample], index: int) -> str:
    if isinstance(examples, (FeatureExamples, _IndexedExamples)):
        return examples.seed_at(index)
    return examples[index].seed


def _batch(model: MStd, examples: Sequence[WarmupExample]
           ) -> tuple[Tensor, Tensor, Tensor, Tensor, Tensor, Tensor]:
    device = model.ids.weight.device
    scenes = [row.scene.to(device=device, dtype=torch.float32) for row in examples]
    packed = torch.nn.utils.rnn.pad_sequence(scenes, batch_first=True)
    lengths = torch.tensor([len(row) for row in scenes], device=device)
    mask = torch.arange(packed.shape[1], device=device)[None, :] >= lengths[:, None]
    means = torch.stack([row.mean.to(device=device, dtype=torch.float32) for row in examples])
    logits, hp = model.rl4_heads(packed, mask, means, [row.floor for row in examples])
    outcome_valid = torch.tensor([row.outcome is not None for row in examples], device=device)
    hp_valid = torch.tensor([row.hp is not None for row in examples], device=device)
    labels = torch.tensor([row.outcome if row.outcome is not None else 49
                           for row in examples], device=device)
    target = F.one_hot(labels, OUTCOME_CLASSES).float()
    hp_target = torch.tensor([row.hp or 0.0 for row in examples], device=device)
    return logits, hp, outcome_valid, hp_valid, target, hp_target


def evaluate_heads(
    model: MStd,
    examples: Sequence[WarmupExample],
    *,
    batch_size: int = 16,
    return_predictions: bool = False,
) -> dict[str, Any]:
    """Held-out diagnostics and same-row constants; no cross-dataset EV comparison."""
    losses: list[float] = []
    predicted: list[float] = []
    actual: list[float] = []
    hp_errors: list[float] = []
    hp_losses: list[float] = []
    by_game: dict[str, list[tuple[float, float]]] = defaultdict(list)
    labels: list[int] = []
    floors: list[int] = []
    brier: list[float] = []
    prediction_rows: list[dict[str, Any]] = []
    previous = model.rl4_heads.training
    model.rl4_heads.eval()
    try:
        with torch.no_grad():
            for offset in range(0, len(examples), batch_size):
                rows = examples[offset:offset + batch_size]
                logits, hp, ov, hv, target, hp_target = _batch(model, rows)
                if bool(ov.any()):
                    nll = outcome_cross_entropy(logits[ov], target[ov]).cpu().tolist()
                    losses.extend(nll)
                    probabilities = logits[ov].softmax(-1)
                    brier.extend(((probabilities - target[ov]).square().sum(-1)).cpu().tolist())
                    values = logits[ov].softmax(-1) @ class_reward_values(device=logits.device)
                    truth = target[ov] @ class_reward_values(device=logits.device)
                    p, a = values.cpu().tolist(), truth.cpu().tolist()
                    predicted.extend(p)
                    actual.extend(a)
                    selected = [row for row in rows if row.outcome is not None]
                    labels.extend(int(row.outcome) for row in selected if row.outcome is not None)
                    floors.extend(row.floor for row in selected)
                    for row, prediction, answer in zip(selected, p, a, strict=True):
                        by_game[row.seed].append((prediction, answer))
                    if return_predictions:
                        original_indices = [
                            i for i, row in enumerate(rows) if row.outcome is not None
                        ]
                        for i, local in enumerate(original_indices):
                            prediction_rows.append(
                                {
                                    "index": offset + local,
                                    "seed": rows[local].seed,
                                    "prediction": p[i],
                                    "target": a[i],
                                    "floor": rows[local].floor,
                                    "outcome_class": rows[local].outcome,
                                    "nll": nll[i],
                                    "probabilities": probabilities[i].cpu().tolist(),
                                    "hp_prediction": float(hp[local]),
                                    "hp_target": rows[local].hp,
                                }
                            )
                if bool(hv.any()):
                    hp_errors.extend(((hp[hv] - hp_target[hv]).abs() * 100).cpu().tolist())
                    hp_losses.extend(F.huber_loss(hp[hv], hp_target[hv], reduction="none",
                                                 delta=0.1).cpu().tolist())
    finally:
        model.rl4_heads.train(previous)
    report: dict[str, Any] = {
        "rows": len(examples),
        "games": len({_seed(examples, i) for i in range(len(examples))}),
        "outcome_rows": len(losses),
        "hp_rows": len(hp_errors),
        "nll": sum(losses) / len(losses) if losses else None,
        "brier": sum(brier) / len(brier) if brier else None,
        "hp_mae_points": sum(hp_errors) / len(hp_errors) if hp_errors else None,
        "hp_huber": sum(hp_losses) / len(hp_losses) if hp_losses else None,
    }
    if actual:
        errors = [(p - a) ** 2 for p, a in zip(predicted, actual, strict=True)]
        average = sum(actual) / len(actual)
        variance = sum((a - average) ** 2 for a in actual) / len(actual)
        residual_mean = sum(p - a for p, a in zip(predicted, actual, strict=True)) / len(actual)
        residual_variance = sum((p - a - residual_mean) ** 2
            for p, a in zip(predicted, actual, strict=True)) / len(actual)
        frequencies = torch.bincount(torch.tensor(labels), minlength=OUTCOME_CLASSES).float()
        probabilities = (frequencies + 1e-3).expand(len(labels), -1).clone()
        probabilities.masked_fill_(~outcome_mask(floors), 0)
        probabilities /= probabilities.sum(1, keepdim=True)
        report.update(
            {
                "mse": sum(errors) / len(errors),
                "constant_mse": variance,
                "explained_variance": 1 - residual_variance / variance if variance > 0 else None,
                "bias": residual_mean,
                "equal_game_mse": sum(
                    sum((p - a) ** 2 for p, a in rows) / len(rows) for rows in by_game.values()
                )
                / len(by_game),
                "constant_nll": float(
                    -probabilities[torch.arange(len(labels)), torch.tensor(labels)].log().mean()
                ),
                "constant_brier": float(
                    (probabilities - F.one_hot(torch.tensor(labels), OUTCOME_CLASSES))
                    .square()
                    .sum(-1)
                    .mean()
                ),
            }
        )
    if return_predictions:
        report["predictions"] = prediction_rows
    return report


def warmup_heads(model: MStd, examples: Sequence[WarmupExample],
                 config: WarmupConfig | None = None) -> dict[str, Any]:
    """Fit only new heads, select the best DEV epoch, preserve trunk and its flags."""
    if not hasattr(model, "rl4_heads"):
        raise ValueError("rl4_heads_not_enabled")
    config = config or WarmupConfig()
    train = _IndexedExamples(examples, [i for i in range(len(examples))
                             if not validation_seed(_seed(examples, i))])
    dev = _IndexedExamples(examples, [i for i in range(len(examples))
                           if validation_seed(_seed(examples, i))])
    if not train or not dev:
        raise ValueError("warmup_requires_train_and_seed_holdout")
    began = time.monotonic()
    rng = random.Random(config.seed)
    parameters = list(model.parameters())
    previous = [p.requires_grad for p in parameters]
    mode = model.rl4_heads.training
    optimizer = torch.optim.AdamW(model.rl4_heads.parameters(), lr=config.learning_rate,
                                  weight_decay=0)
    epochs: list[dict[str, Any]] = []
    best = math.inf
    best_epoch = 0
    best_state: dict[str, Tensor] | None = None
    updates = 0
    status = "max_epochs"
    try:
        for parameter in parameters:
            parameter.requires_grad_(False)
        model.rl4_heads.requires_grad_(True)
        initial = evaluate_heads(model, dev, batch_size=config.batch_size)
        best = (initial["nll"] or 0) + (initial["hp_huber"] or 0)
        best_state = {k: v.detach().cpu().clone()
                      for k, v in model.rl4_heads.state_dict().items()}
        for epoch in range(1, config.max_epochs + 1):
            groups: dict[str, list[int]] = defaultdict(list)
            for index in range(len(train)):
                groups[_seed(train, index)].append(index)
            games = list(groups)
            rng.shuffle(games)
            order = [index for seed in games for index in groups[seed]]
            consumed = 0
            model.rl4_heads.train()
            for offset in range(0, len(order), config.batch_size):
                if time.monotonic() - began >= config.max_seconds:
                    status = "time_limit"
                    break
                batch = [train[index] for index in order[offset:offset + config.batch_size]]
                logits, hp, ov, hv, targets, hp_target = _batch(model, batch)
                terms = []
                if bool(ov.any()):
                    terms.append(outcome_cross_entropy(logits[ov], targets[ov]).mean())
                if bool(hv.any()):
                    terms.append(F.huber_loss(hp[hv], hp_target[hv], delta=0.1))
                loss = torch.stack(terms).sum()
                if not bool(torch.isfinite(loss)):
                    raise ValueError("non_finite_warmup_loss")
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                norm = torch.nn.utils.clip_grad_norm_(model.rl4_heads.parameters(),
                                                      config.max_grad_norm)
                if not bool(torch.isfinite(norm)):
                    raise ValueError("non_finite_warmup_gradient")
                optimizer.step()
                updates += 1
                consumed += len(batch)
            validation = evaluate_heads(model, dev, batch_size=config.batch_size)
            score = (validation["nll"] or 0) + (validation["hp_huber"] or 0)
            epochs.append({"epoch": epoch, "train_rows_consumed": consumed,
                           "validation": validation, "updates": updates})
            if score < best:
                best, best_epoch = score, epoch
                best_state = {k: v.detach().cpu().clone()
                              for k, v in model.rl4_heads.state_dict().items()}
            else:
                status = "validation_not_improving"
                break
            if status == "time_limit":
                break
        if best_state is not None:
            model.rl4_heads.load_state_dict(best_state)
        return {"status": status, "selected_epoch": best_epoch, "updates": updates,
            "train_games": len({_seed(train, i) for i in range(len(train))}),
            "validation_games": len({_seed(dev, i) for i in range(len(dev))}),
            "train_rows": len(train),
            "validation_rows": len(dev), "initial_validation": initial,
            "epochs": epochs, "elapsed_seconds": time.monotonic() - began,
            "optimizer_retained": False,
            "selection_criterion": "DEV NLL plus HP Huber(delta=0.1), including epoch zero",
            "split": "10% whole-seed rl4-warmup-v1 inside admitted TRAIN"}
    finally:
        for parameter, flag in zip(parameters, previous, strict=True):
            parameter.requires_grad_(flag)
        model.rl4_heads.train(mode)


def main() -> None:
    """Fit heads from normal-reader feature blocks; no World or resource submission."""
    from stpd.p1.export import load_model
    from stpd.workers.checkpoint_codec import encode_checkpoint

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seconds", type=float, default=2700)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--seed", type=int, default=20261103)
    arguments = parser.parse_args()
    config = WarmupConfig(seed=arguments.seed, max_seconds=arguments.seconds,
                          max_epochs=arguments.epochs, batch_size=arguments.batch_size)
    descriptor = json.loads((arguments.package / "model.json").read_bytes())
    package_sha = descriptor["package_sha256"]
    model, _ = load_model(arguments.package / "model.tensor-tree", package_sha)
    examples = FeatureExamples(arguments.features, package_sha)
    # Per-game frequencies preserve the selected TRAIN cohort's terminal mix.
    frequencies = [0.0] * OUTCOME_CLASSES
    for end in examples.ends:
        row = examples[end - 1]
        if row.outcome is not None and not validation_seed(row.seed):
            frequencies[row.outcome] += 1
    model.to(torch.device(arguments.device)).eval()
    model.enable_rl4_heads(arguments.seed, frequencies)
    arguments.output.mkdir(parents=True, exist_ok=False)
    report = warmup_heads(model, examples, config)
    raw = encode_checkpoint({"schema": "stpd/rl4-head-warmup-1",
        "model_package_sha256": package_sha,
        "feature_manifest_sha256": hashlib.sha256(arguments.features.read_bytes()).hexdigest(),
        "heads": model.rl4_heads.state_dict(), "report": report})
    (arguments.output / "heads.tensor-tree").write_bytes(raw)
    report["heads_sha256"] = hashlib.sha256(raw).hexdigest()
    (arguments.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
