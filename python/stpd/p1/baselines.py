"""G1 uniform random, G2 scene/action frequency, G3 listwise logistic + L2.

One engine provides bounded fit, exact checkpoint/resume and complete-catalog
scores. RNG belongs to the complete Agent's ACK commit, never to model scoring.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch.nn import functional as F

from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

from .baseline_features import (
    CATEGORIES,
    FEATURE_SPEC,
    candidate_features,
    fit_categories,
    vectorize,
)
from .baseline_spec import CHECKPOINT_SCHEMA, BaselineConfig
from .data import digest, json_bytes
from .numerics import TrainingNumerics, training_scope
from .spec import TARGET_SPEC


@dataclass(frozen=True)
class BaselinePopulation:
    observations: tuple[dict[str, Any], ...]
    targets: tuple[int, ...]
    point_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not len(self.observations) == len(self.targets) == len(self.point_ids):
            raise ValueError("baseline population lengths differ")
        for observation, target in zip(self.observations, self.targets, strict=True):
            rows = candidate_features(observation)
            if type(target) is not int or not 0 <= target < len(rows):
                raise ValueError("baseline target outside full catalog")


class BaselineModel:
    def __init__(
        self,
        algorithm: str,
        categories: dict[str, list[str]],
        counts: dict[str, dict[str, int]] | None = None,
        weights: torch.Tensor | None = None,
    ) -> None:
        if algorithm not in {"random", "frequency", "linear"}:
            raise ValueError("unsupported baseline algorithm")
        if not isinstance(categories, dict) or (
            set(categories) != set(CATEGORIES) if algorithm == "linear" else bool(categories)
        ):
            raise ValueError("baseline category inventory differs")
        for values in categories.values():
            if (
                not isinstance(values, list)
                or not values
                or values[0] != "[UNKNOWN]"
                or any(not isinstance(v, str) or not v for v in values)
                or len(values) != len(set(values))
            ):
                raise ValueError("baseline categories must be unique TRAIN strings")
        if counts is not None and (
            not isinstance(counts, dict)
            or any(
                not isinstance(scene, str)
                or not isinstance(kinds, dict)
                or any(
                    not isinstance(kind, str) or type(count) is not int or count < 1
                    for kind, count in kinds.items()
                )
                for scene, kinds in counts.items()
            )
        ):
            raise ValueError("baseline frequency counts must be positive integers")
        if algorithm != "frequency" and counts:
            raise ValueError("only frequency baseline owns counts")
        self.algorithm, self.categories = algorithm, categories
        self.counts = counts or {}
        width = sum(len(v) for v in categories.values()) + 18
        self.weights = torch.zeros(width) if weights is None else weights.detach().clone()
        if (
            self.weights.shape != (width,)
            or self.weights.dtype != torch.float32
            or not bool(torch.isfinite(self.weights).all())
        ):
            raise ValueError("invalid baseline weights")

    def scores(self, observation: dict[str, Any]) -> list[float]:
        rows = candidate_features(observation)
        if self.algorithm == "random":
            return [0.0] * len(rows)
        if self.algorithm == "frequency":
            counts = self.counts.get(observation["scene"]["family"], {})
            total = sum(counts.values())
            return [counts.get(row["action"], 0) / total if total else 0.0 for row in rows]
        if self.algorithm != "linear":
            raise ValueError("unsupported baseline algorithm")
        if not rows:
            return []
        features = torch.tensor(vectorize(observation, self.categories), dtype=torch.float32)
        scores = features @ self.weights
        if not bool(torch.isfinite(scores).all()):
            raise ValueError("non-finite baseline scores")
        return scores.tolist()

    def state(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "categories": copy.deepcopy(self.categories),
            "counts": copy.deepcopy(self.counts),
            "weights": self.weights.clone(),
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> BaselineModel:
        return cls(state["algorithm"], state["categories"], state["counts"], state["weights"])


def validate_checkpoint_state(
    state: dict[str, Any], config: BaselineConfig, train_points: int
) -> None:
    """The same progress/model inventory is checked by resume and result readers."""
    batches = math.ceil(train_points / config.batch_size)
    epochs = config.max_epochs if config.algorithm == "linear" else 1
    max_fit = (
        config.steps(train_points)
        if config.algorithm == "linear"
        else (batches if config.algorithm == "frequency" else 1)
    )
    for key, maximum in (
        ("step", config.steps(train_points)),
        ("fit_steps", max_fit),
        ("epoch", epochs),
        ("cursor", train_points),
    ):
        if type(state.get(key)) is not int or not 0 <= state[key] <= maximum:
            raise ValueError("invalid baseline checkpoint progress")
    order = state.get("order")
    if (
        not isinstance(order, list)
        or any(type(i) is not int for i in order)
        or sorted(order) != list(range(train_points))
    ):
        raise ValueError("invalid baseline checkpoint order")
    phase = state.get("phase")
    if phase not in {"training", "trained", "completed"}:
        raise ValueError("invalid baseline checkpoint phase")
    if phase == "training":
        expected = state["epoch"] * batches + math.ceil(state["cursor"] / config.batch_size)
        if (
            state["epoch"] >= epochs
            or state["cursor"] >= train_points
            or state["cursor"] % config.batch_size != 0
            or state["fit_steps"] != expected
        ):
            raise ValueError("invalid baseline checkpoint progress")
    elif (
        state["epoch"] != epochs or state["cursor"] != train_points or state["fit_steps"] != max_fit
    ):
        raise ValueError("incomplete baseline checkpoint claimed trained")
    if (
        config.algorithm == "linear"
        and state["step"] != state["fit_steps"]
        or config.algorithm != "linear"
        and state["step"] != 0
    ):
        raise ValueError("baseline optimizer updates differ from actual work")
    if config.algorithm == "random" and phase == "training" and state["fit_steps"] != 0:
        raise ValueError("random Agent has no partial fitting")
    model = BaselineModel.from_state(state["model"])
    if model.algorithm != config.algorithm:
        raise ValueError("baseline checkpoint algorithm differs")
    best = state.get("best_model")
    if best is not None:
        selected = BaselineModel.from_state(best)
        if (
            selected.algorithm != model.algorithm
            or selected.categories != model.categories
            or type(state.get("best_step")) is not int
            or not 0 <= state["best_step"] <= state["step"]
        ):
            raise ValueError("baseline selected model binding differs")
    if (
        config.algorithm == "linear"
        and state["epoch"] > 0
        and (
            best is None
            or state["best_step"] < 1
            or state["best_step"] % batches != 0
            or type(state.get("best_loss")) is not float
            or not math.isfinite(state["best_loss"])
        )
    ):
        raise ValueError("baseline DEV selection is missing or invalid")
    if phase != "completed" and state.get("test_result") is not None:
        raise ValueError("baseline TEST was evaluated before selection completed")
    if phase == "completed" and (best is None or not isinstance(state.get("test_result"), dict)):
        raise ValueError("baseline completed state lacks selected model or evaluation")
    if (config.algorithm == "linear") != isinstance(state.get("optimizer"), dict):
        raise ValueError("baseline optimizer inventory differs")


class BaselineTrainer:
    def __init__(
        self, populations: dict[str, BaselinePopulation], snapshot_id: str, config: BaselineConfig
    ) -> None:
        if set(populations) != {"train", "dev", "test"} or not populations["train"].point_ids:
            raise ValueError("explicit partitions and nonempty TRAIN required")
        all_ids = [key for p in populations.values() for key in p.point_ids]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("baseline partition overlap")
        self.populations, self.config = populations, config
        categories = (
            fit_categories(list(populations["train"].observations))
            if config.algorithm == "linear"
            else {}
        )
        self.model = BaselineModel(config.algorithm, categories)
        self.parameter = torch.nn.Parameter(self.model.weights.clone())
        self.optimizer = (
            torch.optim.Adam([self.parameter], lr=config.learning_rate)
            if config.algorithm == "linear"
            else None
        )
        self._numerics = TrainingNumerics(config.seed, config.cpu_threads, torch.device("cpu"))
        self.generator = torch.Generator().manual_seed(config.seed)
        self.order = self._order()
        self.cursor = self.epoch = self.step = self.fit_steps = 0
        self.phase = "training"
        self.test_result: dict[str, Any] | None = None
        self.dev_result: dict[str, Any] | None = None
        self.best_loss: float | None = None
        self.best_step: int | None = None
        self.best_model: dict[str, Any] | None = None
        self.identity = {
            "snapshot_id": snapshot_id,
            "config": config.to_dict(),
            "feature_spec": FEATURE_SPEC,
            "target_spec": TARGET_SPEC,
            "categories_sha256": digest(json_bytes(categories)),
            "partitions": {k: digest(json_bytes(p.point_ids)) for k, p in populations.items()},
            "code": {
                name: digest((Path(__file__).parent / name).read_bytes())
                for name in (
                    "baselines.py",
                    "baseline_features.py",
                    "baseline_spec.py",
                    "numerics.py",
                )
            },
            "runtime": {
                "torch": str(torch.__version__),
                "dtype": "float32",
                "cpu_threads": config.cpu_threads,
            },
        }

    def _order(self) -> list[int]:
        count = len(self.populations["train"].point_ids)
        return (
            torch.randperm(count, generator=self.generator).tolist()
            if self.config.algorithm == "linear"
            else list(range(count))
        )

    @training_scope
    def advance(self) -> dict[str, Any]:
        if self.phase != "training":
            raise ValueError("baseline fit already complete")
        train = self.populations["train"]
        indices = self.order[self.cursor : self.cursor + self.config.batch_size]
        loss_value = None
        if self.optimizer is not None:
            self.optimizer.zero_grad(set_to_none=True)
            losses = []
            for i in indices:
                features = torch.tensor(vectorize(train.observations[i], self.model.categories))
                scores = features @ self.parameter
                losses.append(
                    F.cross_entropy(scores.unsqueeze(0), torch.tensor([train.targets[i]]))
                )
            loss = torch.stack(losses).mean() + self.config.l2 * self.parameter.square().sum() / 2
            if not bool(torch.isfinite(loss)):
                raise ValueError("non-finite linear loss")
            loss.backward()
            if self.parameter.grad is None or not bool(torch.isfinite(self.parameter.grad).all()):
                raise ValueError("non-finite linear gradient")
            self.optimizer.step()
            self.model.weights = self.parameter.detach().clone()
            self.step += 1
            loss_value = float(loss.detach())
        elif self.config.algorithm == "frequency":
            for i in indices:
                observation, target = train.observations[i], train.targets[i]
                scene = observation["scene"]["family"]
                kind = observation["catalog"][target]["kind"]
                counts = self.model.counts.setdefault(scene, {})
                counts[kind] = counts.get(kind, 0) + 1
        self.fit_steps += 1
        self.cursor += len(indices)
        if self.config.algorithm == "random":
            self.cursor = len(self.order)  # G1 has no fitting, vocabulary or statistics.
        if self.cursor == len(self.order):
            self.epoch += 1
            if self.config.algorithm == "linear":
                dev = self.evaluate(self.populations["dev"])
                loss = dev["mean_score_softmax_N_loss"]
                if loss is None:
                    raise ValueError("linear DEV must be nonempty")
                if self.best_loss is None or loss < self.best_loss:
                    self.best_loss, self.best_step, self.dev_result = loss, self.step, dev
                    self.best_model = self.model.state()
            if self.config.algorithm != "linear" or self.epoch == self.config.max_epochs:
                self.phase = "trained"
            else:
                self.order, self.cursor = self._order(), 0
        return {
            "optimizer_updates": self.step,
            "fit_steps": self.fit_steps,
            "epoch": self.epoch,
            "examples": len(indices),
            "loss": loss_value,
            "phase": self.phase,
            "optimizer": "Adam+explicit_L2" if self.optimizer else None,
            "dev": self.dev_result if self.cursor == len(self.order) or self.cursor == 0 else None,
        }

    def evaluate(
        self, population: BaselinePopulation, model: BaselineModel | None = None
    ) -> dict[str, Any]:
        model = self.model if model is None else model
        loss_sum = correct = 0.0
        for observation, target in zip(population.observations, population.targets, strict=True):
            scores = model.scores(observation)
            if self.config.algorithm == "frequency":
                # G2 numbers are ranking proportions, not softmax logits.
                # A tied greedy Agent assigns equal probability among maxima.
                maxima = [i for i, x in enumerate(scores) if x == max(scores)]
                correct += float(target in maxima) / len(maxima)
            elif self.config.algorithm == "random":
                correct += 1 / len(scores)
            else:
                maxima = [i for i, x in enumerate(scores) if x == max(scores)]
                correct += float(target in maxima) / len(maxima)
            loss_sum += float(F.cross_entropy(torch.tensor([scores]), torch.tensor([target])))
        count = len(population.point_ids)
        return {
            "N": count,
            "mean_score_softmax_N_loss": loss_sum / count if count else None,
            "expected_greedy_correct": correct,
            "tie_semantics": "uniform among maxima",
        }

    def finish(self) -> None:
        if self.phase == "completed":
            return
        if self.phase != "trained":
            raise ValueError("baseline training must finish before heldout evaluation")
        if self.best_model is None:
            self.best_model, self.best_step = self.model.state(), self.step
            self.dev_result = self.evaluate(self.populations["dev"])
        self.test_result = self.evaluate(self.populations["test"], self.selected_model())
        self.phase = "completed"

    def selected_model(self) -> BaselineModel:
        if self.best_model is None:
            raise ValueError("baseline has no selected model")
        return BaselineModel.from_state(self.best_model)

    def checkpoint(self) -> bytes:
        return encode_checkpoint(
            {
                "schema": CHECKPOINT_SCHEMA,
                "identity": self.identity,
                "model": self.model.state(),
                "step": self.step,
                "fit_steps": self.fit_steps,
                "epoch": self.epoch,
                "cursor": self.cursor,
                "order": self.order,
                "generator": self.generator.get_state(),
                "phase": self.phase,
                "optimizer": self.optimizer.state_dict() if self.optimizer else None,
                "best_loss": self.best_loss,
                "best_step": self.best_step,
                "best_model": self.best_model,
                "dev_result": self.dev_result,
                "test_result": self.test_result,
            }
        )

    def restore(self, raw: bytes) -> None:
        state = decode_checkpoint(raw)
        if state.get("schema") != CHECKPOINT_SCHEMA or state.get("identity") != self.identity:
            raise ValueError("baseline checkpoint identity differs")
        model = BaselineModel.from_state(state["model"])
        if model.algorithm != self.config.algorithm or model.categories != self.model.categories:
            raise ValueError("baseline checkpoint model binding differs")
        validate_checkpoint_state(state, self.config, len(self.order))
        self.model = model
        with torch.no_grad():
            self.parameter.copy_(model.weights)
        if self.optimizer is not None:
            if state["optimizer"]["param_groups"] != self.optimizer.state_dict()["param_groups"]:
                raise ValueError("baseline optimizer configuration differs")
            self.optimizer.load_state_dict(state["optimizer"])
        elif state["optimizer"] is not None or state["step"] != 0:
            raise ValueError("non-optimizer baseline claimed optimizer updates")
        for name in (
            "step",
            "fit_steps",
            "epoch",
            "cursor",
            "order",
            "phase",
            "dev_result",
            "test_result",
            "best_loss",
            "best_step",
            "best_model",
        ):
            setattr(self, name, state[name])
        self.generator.set_state(state["generator"])
