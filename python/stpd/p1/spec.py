"""The N-only M-std training recipe; no tensor imports or execution authority."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from spireagent.json_boundary import BoundaryError, object_fields

RECIPE = "p1-mstd-N-v1"
SNAPSHOT_SCHEMA = "stpd/p1-N-snapshot-1"
TRAINING_SCHEMA = "stpd/p1-mstd-training-input-1"
RUN_SCHEMA = "stpd/p1-mstd-run-1"
DERIVED_RUN_SCHEMA = "stpd/p1-mstd-derived-run-1"
RUN_SCHEMAS = frozenset({RUN_SCHEMA, DERIVED_RUN_SCHEMA})
DERIVED_CHECKPOINT_SCHEMA = "stpd/p1-mstd-derived-checkpoint-1"
CHECKPOINT_SCHEMA = "stpd/p1-mstd-formal-checkpoint-1"
MODEL_SCHEMA = "stpd/p1-mstd-trained-model-1"
PACKAGE_SCHEMA = "stpd/p1-mstd-model-1"
EXECUTION_PACKAGE_SCHEMA = "stpd/p1-mstd-model-2"
MSTD_PACKAGE_SCHEMAS = frozenset({PACKAGE_SCHEMA, EXECUTION_PACKAGE_SCHEMA})
TARGET_SPEC = {
    "id": "p1-current-catalog-N-1",
    "version": "1",
    "N": "complete current public Input and exact chosen in the full legal catalog",
    "next": "not consumed or qualified by this recipe",
    "end": "not consumed by this recipe",
    "context": "N=false and partial observations are never model inputs",
}
CHECKPOINT_FRACTIONS = (5, 10, 20, 40, 60, 80, 100)


@dataclass(frozen=True)
class MStdConfig:
    seed: int = 1
    max_epochs: int = 30
    batch_size: int = 64
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    warmup_fraction: float = 0.05
    patience: int = 5
    device: str = "cpu"
    dtype: str = "float32"
    cpu_threads: int = 2
    value_heads: bool = False
    model_size: str = "standard"

    def __post_init__(self) -> None:
        if type(self.model_size) is not str or self.model_size not in {"standard", "large"}:
            raise BoundaryError("p1_training", "unsupported_model_size")
        if self.model_size == "large" and not self.value_heads:
            raise BoundaryError("p1_training", "large_model_requires_value_protocol")
        if type(self.value_heads) is not bool:
            raise BoundaryError("p1_training", "boolean_value_heads_required")
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise BoundaryError("p1_training", "invalid_seed")
        if type(self.max_epochs) is not int or not 1 <= self.max_epochs <= 30:
            raise BoundaryError("p1_training", "invalid_epoch_budget")
        if (
            type(self.batch_size) is not int
            or self.batch_size != 64
            or type(self.patience) is not int
            or self.patience != 5
            or type(self.cpu_threads) is not int
            or self.cpu_threads != 2
            or self.dtype != "float32"
            or self.learning_rate != 3e-4
            or self.weight_decay != 0.01
            or self.warmup_fraction != 0.05
        ):
            raise BoundaryError("p1_training", "unsupported_M_std_recipe")
        if not isinstance(self.device, str) or (
            self.device != "cpu"
            and not (self.device.startswith("cuda:") and self.device[5:].isdigit())
        ):
            raise BoundaryError("p1_training", "explicit_device_required")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if not self.value_heads:
            value.pop("value_heads")
        if self.model_size == "standard":
            value.pop("model_size")
        return value

    @classmethod
    def decode(cls, value: object) -> MStdConfig:
        fields = set(cls.__dataclass_fields__)
        if isinstance(value, dict) and "value_heads" not in value:
            fields.remove("value_heads")
        if isinstance(value, dict) and "model_size" not in value:
            fields.remove("model_size")
        return cls(**object_fields(value, fields, "p1_training.config"))

    def steps(self, train_points: int) -> int:
        if type(train_points) is not int or train_points < 1:
            raise BoundaryError("p1_training", "nonempty_train_required")
        return self.max_epochs * math.ceil(train_points / self.batch_size)
