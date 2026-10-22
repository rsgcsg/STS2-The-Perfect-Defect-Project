"""Frozen CPU recipes for the three ordinary-N comparison Agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, object_fields

RECIPE = "p1-baselines-N-v1"
RUN_SCHEMA = "stpd/p1-baseline-run-1"
TRAINING_SCHEMA = "stpd/p1-baseline-training-input-1"
CHECKPOINT_SCHEMA = "stpd/p1-baseline-checkpoint-1"
MODEL_SCHEMA = "stpd/p1-baseline-trained-model-1"


@dataclass(frozen=True)
class BaselineConfig:
    algorithm: str = "random"
    seed: int = 1
    max_epochs: int = 30
    batch_size: int = 64
    learning_rate: float = 0.01
    l2: float = 0.01
    cpu_threads: int = 2
    device: str = "cpu"

    def __post_init__(self) -> None:
        if self.algorithm not in {"random", "frequency", "linear"}:
            raise BoundaryError("p1_baseline", "unknown_algorithm")
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise BoundaryError("p1_baseline", "invalid_seed")
        if (
            type(self.max_epochs) is not int
            or not 1 <= self.max_epochs <= 30
            or type(self.batch_size) is not int
            or not 1 <= self.batch_size <= 256
            or self.learning_rate != 0.01
            or self.l2 != 0.01
            or type(self.cpu_threads) is not int
            or self.cpu_threads not in {1, 2}
            or self.device != "cpu"
        ):
            raise BoundaryError("p1_baseline", "unsupported_cpu_recipe")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def decode(cls, value: object) -> BaselineConfig:
        return cls(**object_fields(value, set(cls.__dataclass_fields__), "p1_baseline.config"))

    def steps(self, train_points: int) -> int:
        """Actual optimizer-update upper bound; counting is not an optimizer."""
        if type(train_points) is not int or train_points < 1:
            raise BoundaryError("p1_baseline", "nonempty_train_required")
        return (
            self.max_epochs * ((train_points + self.batch_size - 1) // self.batch_size)
            if self.algorithm == "linear"
            else 0
        )
