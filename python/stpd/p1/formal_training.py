"""M-std epochs/dev/best/resume over the delivered T0 graph and checkpoint codec.

The workload owns publication and application controls. This engine has no Store,
provider, data admission, local registry, game access or implicit restart.
"""

from __future__ import annotations

import math
import os
import platform
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

from .data import digest, json_bytes
from .execution import MStdExecution, microbatches
from .export import input_spec
from .input import Frame
from .model import ARCHITECTURE, MStd
from .numerics import TrainingNumerics, training_scope
from .optimization import optimizer_update
from .spec import CHECKPOINT_FRACTIONS, CHECKPOINT_SCHEMA, TARGET_SPEC, MStdConfig


@dataclass(frozen=True)
class Population:
    frames: tuple[Frame, ...]
    targets: tuple[int, ...]
    point_ids: tuple[str, ...]
    weights: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if len(self.frames) != len(self.targets) or len(self.frames) != len(self.point_ids):
            raise BoundaryError("p1_training", "population_length_mismatch")
        if self.weights and (len(self.weights) != len(self.frames) or any(
                type(w) not in {int, float} or not math.isfinite(w) or w <= 0
                for w in self.weights)):
            raise BoundaryError("p1_training", "invalid_population_weights")
        for frame, target in zip(self.frames, self.targets, strict=True):
            if type(target) is not int or not 0 <= target < len(frame.choices):
                raise BoundaryError("p1_training", "target_outside_full_catalog")


FORMAL_CODE_FILES = ("formal_training.py", "spec.py", "input.py", "model.py", "numerics.py",
                     "execution.py", "optimization.py", "weighting.py")


class MStdTrainer:
    """One real epoch visits each train point once, including the true tail batch."""

    def __init__(
        self,
        populations: dict[str, Population],
        vocabulary: dict[str, Any],
        snapshot_id: str,
        config: MStdConfig,
        execution: MStdExecution | None = None,
        *, checkpoint_policy: str | None = None,
    ) -> None:
        if set(populations) != {"train", "dev", "test"}:
            raise BoundaryError("p1_training", "partition_inventory_mismatch")
        if not populations["train"].frames or not populations["dev"].frames:
            raise BoundaryError("p1_training", "nonempty_train_and_dev_required")
        ids = [point for p in populations.values() for point in p.point_ids]
        if len(set(ids)) != len(ids):
            raise BoundaryError("p1_training", "point_leakage_between_partitions")
        self.populations, self.vocabulary, self.config = populations, vocabulary, config
        self.execution = execution or MStdExecution()
        if self.execution.cpu_threads != config.cpu_threads:
            raise BoundaryError("p1_training", "formal_recipe_cpu_threads_mismatch")
        from .weighting import CHECKPOINT_POLICY

        if checkpoint_policy not in {None, CHECKPOINT_POLICY}:
            raise BoundaryError("p1_training", "unsupported_checkpoint_policy")
        self.checkpoint_policy = checkpoint_policy
        self.device = torch.device(config.device)
        cuda_runtime = None
        if self.device.type == "cuda":
            if not torch.cuda.is_available():
                raise BoundaryError("p1_training", "cuda_unavailable_no_fallback")
            if os.environ.get("CUBLAS_WORKSPACE_CONFIG") not in {":4096:8", ":16:8"}:
                raise BoundaryError("p1_training", "deterministic_cublas_workspace_required")
            properties = torch.cuda.get_device_properties(self.device)
            cuda_runtime = {
                "name": properties.name,
                "capability": [properties.major, properties.minor],
                "total_memory": properties.total_memory,
                "cublas_workspace": os.environ["CUBLAS_WORKSPACE_CONFIG"],
                "matmul_tf32": False,
                "cudnn_tf32": False,
                "cudnn_benchmark": False,
            }
        self._numerics = TrainingNumerics(config.seed, self.execution.cpu_threads, self.device)
        with self._numerics.scope():
            self.model = MStd(vocabulary, self.execution).to(self.device)
            self.optimizer = torch.optim.AdamW(
                self.model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
            )
        self.generator = torch.Generator().manual_seed(config.seed + 1000)
        self.order = torch.randperm(
            len(populations["train"].frames), generator=self.generator
        ).tolist()
        self.cursor = self.epoch = self.step = self.bad_epochs = 0
        self.phase = "training"
        self.stop_reason: str | None = None
        self.best_loss: float | None = None
        self.best_epoch_loss: float | None = None
        self.best_step: int | None = None
        self.best_epoch: int | None = None
        self.best_weights: dict[str, torch.Tensor] | None = None
        self.history: list[dict[str, Any]] = []
        self.dev_history: list[dict[str, Any]] = []
        self.checkpoint_dev_history: list[dict[str, Any]] = []
        self.test_result: dict[str, Any] | None = None
        self.planned_steps = config.steps(len(populations["train"].frames))
        self.checkpoint_steps = {
            fraction: max(1, math.ceil(self.planned_steps * fraction / 100))
            for fraction in CHECKPOINT_FRACTIONS
        }
        per_epoch = math.ceil(len(populations["train"].frames) / config.batch_size)
        self.early_checkpoint_steps = ({max(1, math.ceil(per_epoch * q / 4))
                                        for q in range(1, 13)} if checkpoint_policy else set())
        root = Path(__file__).parent
        self.identity = {
            "snapshot_id": snapshot_id,
            "config": config.to_dict(),
            "architecture": ARCHITECTURE,
            "input_spec": input_spec(vocabulary),
            "target_spec": TARGET_SPEC,
            "partitions": {
                k: {
                    "points_sha256": digest(json_bytes(p.point_ids)),
                    "targets_sha256": digest(json_bytes(p.targets)),
                }
                for k, p in populations.items()
            },
            "vocabulary_sha256": digest(json_bytes(vocabulary)),
            "planned_steps": self.planned_steps,
            "execution": self.execution.to_dict(),
            **({"checkpoint_policy": checkpoint_policy} if checkpoint_policy else {}),
            **({"train_weights_sha256": digest(json_bytes(populations["train"].weights))}
               if populations["train"].weights else {}),
            "runtime": {
                "python": platform.python_version(),
                "torch": str(torch.__version__),
                "device": config.device,
                "dtype": config.dtype,
                "cuda": torch.version.cuda if self.device.type == "cuda" else None,
                "cuda_runtime": cuda_runtime,
                "cpu_threads": self.execution.cpu_threads,
            },
            "code": {
                name: digest((root / name).read_bytes())
                for name in FORMAL_CODE_FILES
            },
            "codec_sha256": digest((root.parent / "workers/checkpoint_codec.py").read_bytes()),
        }

    def _lr(self) -> float:
        warmup = max(1, math.ceil(self.planned_steps * self.config.warmup_fraction))
        if self.step < warmup:
            return self.config.learning_rate * (self.step + 1) / warmup
        progress = (self.step + 1 - warmup) / (self.planned_steps - warmup)
        return self.config.learning_rate * (1 + math.cos(math.pi * progress)) / 2

    @staticmethod
    def batches(
        frames: tuple[Frame, ...], indices: list[int], model: MStd | None = None
    ) -> Iterator[list[int]]:
        """Bound temporary padded attention, without changing the optimizer batch.

        A large single observation is still processed whole. Frame order is kept;
        no length sorting changes the sampler or which examples share an update.
        """
        yield from microbatches(frames, indices, model)

    @staticmethod
    def evaluate(model: MStd, population: Population) -> dict[str, Any]:
        model.eval()
        device = model.ids.weight.device
        rows = []
        with torch.inference_mode():
            indices_in_order = list(range(len(population.frames)))
            for indices in MStdTrainer.batches(population.frames, indices_in_order, model):
                scores = model.forward_batch([population.frames[i] for i in indices])
                packed = torch.nn.utils.rnn.pad_sequence(
                    scores, batch_first=True, padding_value=-torch.inf
                )
                targets = torch.tensor([population.targets[i] for i in indices], device=device)
                losses = F.cross_entropy(packed, targets, reduction="none")
                correct = packed.argmax(1) == targets
                multi = torch.tensor(
                    [len(population.frames[i].choices) > 1 for i in indices], device=device
                )
                rows.append(torch.stack((losses, correct, multi, correct & multi), dim=1))
            # One host read for the full evaluation, preserving original loss sum order.
            values = torch.cat(rows).cpu().tolist() if rows else []
        return {
            "N": len(values),
            "mean_N_loss": sum(row[0] for row in values) / len(values) if values else None,
            "correct": int(sum(row[1] for row in values)),
            "multi_N": int(sum(row[2] for row in values)),
            "multi_correct": int(sum(row[3] for row in values)),
        }

    @training_scope
    def advance(self) -> dict[str, Any]:
        if self.phase != "training":
            raise BoundaryError("p1_training", "training_already_complete")
        started = time.perf_counter()
        train = self.populations["train"]
        indices = self.order[self.cursor : self.cursor + self.config.batch_size]
        lr = self._lr()
        update = optimizer_update(self.model, self.optimizer,
                                  [train.frames[i] for i in indices],
                                  [train.targets[i] for i in indices], lr,
                                  **({"weights": [train.weights[i] for i in indices]}
                                     if train.weights else {}))
        train_seconds = time.perf_counter() - started
        self.step += 1
        self.cursor += len(indices)
        progress: dict[str, Any] = {
            "step": self.step,
            "epoch": self.epoch + 1,
            "examples": len(indices),
            "batch_sha256": digest(json_bytes([train.point_ids[i] for i in indices])),
            "mean_N_loss": update["mean_N_loss"],
            "lr": lr,
            "gradient_norm": update["gradient_norm"],
        }
        self.history.append(dict(progress))
        improved = False
        fractions = [
            fraction for fraction, step in self.checkpoint_steps.items() if step == self.step
        ]
        if self.cursor == len(self.order):
            self.epoch += 1
            dev = self.evaluate(self.model, self.populations["dev"])
            dev_loss = dev["mean_N_loss"]
            if dev_loss is None or not math.isfinite(dev_loss):
                raise BoundaryError("p1_training", "non_finite_dev_loss")
            improved = self._select_best(dev_loss, self.epoch)
            # Patience is measured in completed epochs, independently of extra
            # dev checks at the approved percentage checkpoints.
            if self.best_epoch_loss is None or dev_loss < self.best_epoch_loss:
                self.best_epoch_loss = dev_loss
                self.bad_epochs = 0
            else:
                self.bad_epochs += 1
            self.dev_history.append({"epoch": self.epoch, "step": self.step, **dev})
            progress["dev"] = dev
            if self.bad_epochs >= self.config.patience:
                self.stop_reason = "dev_patience_exhausted"
            elif self.epoch == self.config.max_epochs:
                self.stop_reason = "epoch_budget_exhausted"
            if self.stop_reason is not None:
                self.phase = "trained"
            else:
                self.order = torch.randperm(len(train.frames), generator=self.generator).tolist()
                self.cursor = 0
        elif fractions or self.step in self.early_checkpoint_steps:
            dev = self.evaluate(self.model, self.populations["dev"])
            dev_loss = dev["mean_N_loss"]
            if dev_loss is None or not math.isfinite(dev_loss):
                raise BoundaryError("p1_training", "non_finite_dev_loss")
            improved = self._select_best(dev_loss, self.epoch + 1)
            self.checkpoint_dev_history.append(
                {"step": self.step, "epoch": self.epoch + 1, "fractions": fractions, **dev}
            )
            progress["dev_checkpoint"] = dev
        if self.checkpoint_policy:
            progress["retain_checkpoint"] = (self.step in self.early_checkpoint_steps
                                              or "dev" in progress)
        progress["best_improved"] = improved
        progress["checkpoint_fractions"] = fractions
        progress["phase"] = self.phase
        # Wall measurements are events, never checkpoint state or selection inputs.
        progress["timing"] = {
            "train_seconds": train_seconds,
            "dev_and_selection_seconds": time.perf_counter() - started - train_seconds,
            "advance_seconds": time.perf_counter() - started,
            "train_N_per_second": len(indices) / train_seconds,
        }
        return progress

    def _select_best(self, loss: float, epoch: int) -> bool:
        if self.best_loss is not None and loss >= self.best_loss:
            return False
        self.best_loss, self.best_step, self.best_epoch = loss, self.step, epoch
        self.best_weights = {
            name: value.detach().cpu().clone() for name, value in self.model.state_dict().items()
        }
        return True

    def selected_model(self) -> MStd:
        if self.best_weights is None:
            raise BoundaryError("p1_training", "no_dev_selected_model")
        # Construction must not consume training RNG or mutate the continuing last model.
        with torch.random.fork_rng(devices=[]):
            model = MStd(self.vocabulary, self.execution)
        model.load_state_dict(self.best_weights, strict=True)
        return model.eval()

    @training_scope
    def finish(self) -> dict[str, Any]:
        if self.phase not in {"trained", "completed"}:
            raise BoundaryError("p1_training", "test_before_model_selection")
        if self.test_result is None:
            self.test_result = self.evaluate(
                self.selected_model().to(self.device), self.populations["test"]
            )
            self.phase = "completed"
        return self.test_result

    def checkpoint(self) -> bytes:
        return encode_checkpoint(
            {
                "schema": CHECKPOINT_SCHEMA,
                "identity": self.identity,
                "model": dict(self.model.state_dict()),
                "optimizer": self.optimizer.state_dict(),
                "rng": self._numerics.rng,
                "sampler": {
                    "generator": self.generator.get_state(),
                    "order": self.order,
                    "cursor": self.cursor,
                },
                "epoch": self.epoch,
                "step": self.step,
                "phase": self.phase,
                "stop_reason": self.stop_reason,
                "bad_epochs": self.bad_epochs,
                "best_loss": self.best_loss,
                "best_epoch_loss": self.best_epoch_loss,
                "best_epoch": self.best_epoch,
                "best_step": self.best_step,
                "best_model": self.best_weights,
                "history": self.history,
                "dev_history": self.dev_history,
                "checkpoint_dev_history": self.checkpoint_dev_history,
                "test_result": self.test_result,
            }
        )

    def restore(self, raw: bytes) -> None:
        value = decode_checkpoint(raw)
        if value.get("schema") != CHECKPOINT_SCHEMA or value.get("identity") != self.identity:
            raise BoundaryError("p1_training", "checkpoint_execution_identity_mismatch")
        self.model.load_state_dict(value["model"], strict=True)
        self.optimizer.load_state_dict(value["optimizer"])
        self._numerics.rng = value["rng"]
        self.generator.set_state(value["sampler"]["generator"])
        self.order, self.cursor = value["sampler"]["order"], value["sampler"]["cursor"]
        for key in (
            "epoch",
            "step",
            "phase",
            "stop_reason",
            "bad_epochs",
            "best_loss",
            "best_epoch_loss",
            "best_epoch",
            "best_step",
            "history",
            "dev_history",
            "checkpoint_dev_history",
            "test_result",
        ):
            setattr(self, key, value[key])
        self.best_weights = value["best_model"]
        self.optimizer.zero_grad(set_to_none=True)
