"""Small M-std numerical loop, using the existing tensor-tree checkpoint codec.

Effective batch 64 is accumulated one full observation at a time on CPU; there
is no field/token truncation. This is neither a worker service nor a new store.
"""

from __future__ import annotations

import math
import platform
from pathlib import Path
from typing import Any

import torch
from torch.nn import functional as F

from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

from .data import digest, json_bytes
from .input import Frame
from .model import ARCHITECTURE, MStd
from .numerics import TrainingNumerics, training_scope

CONFIG: dict[str, Any] = {
    "seed": 1,
    "batch_size": 64,
    "optimizer": "AdamW",
    "lr": 3e-4,
    "weight_decay": 0.01,
    "warmup_fraction": 0.05,
    "cpu_threads": 2,
    "device": "cpu",
    "dtype": "float32",
}


def model_hash(model: MStd) -> str:
    return digest(
        b"".join(
            name.encode() + tensor.detach().cpu().contiguous().numpy().tobytes()
            for name, tensor in model.state_dict().items()
        )
    )


class Trainer:
    def __init__(
        self,
        frames: list[Frame],
        targets: list[int],
        vocabulary: dict[str, Any],
        source_sha256: str,
        total_steps: int,
    ):
        if total_steps < 2 or total_steps > 32 or len(frames) != len(targets) or not frames:
            raise ValueError("T0 needs a nonempty population and 2..32 bounded CPU updates")
        self.frames, self.targets, self.total_steps = frames, targets, total_steps
        self._numerics = TrainingNumerics(
            CONFIG["seed"], CONFIG["cpu_threads"], torch.device("cpu")
        )
        with self._numerics.scope():
            self.model = MStd(vocabulary)
            self.optimizer = torch.optim.AdamW(
                self.model.parameters(), lr=CONFIG["lr"], weight_decay=CONFIG["weight_decay"]
            )
        self.sampler = torch.Generator().manual_seed(CONFIG["seed"] + 1000)
        self.order = torch.randperm(len(frames), generator=self.sampler).tolist()
        self.cursor = self.step = self.epoch = 0
        self.history: list[dict[str, Any]] = []
        code_root = Path(__file__).parent
        self.identity = {
            "source_sha256": source_sha256,
            "vocabulary_sha256": digest(json_bytes(vocabulary)),
            "architecture": ARCHITECTURE,
            "config": CONFIG,
            "total_steps": total_steps,
            "runtime": {
                "torch": str(torch.__version__),
                "python": platform.python_version(),
                "device": "cpu",
                "threads": CONFIG["cpu_threads"],
            },
            "code": {
                name: digest((code_root / name).read_bytes())
                for name in ("data.py", "input.py", "model.py", "training.py", "numerics.py")
            },
            "codec_sha256": digest((code_root.parent / "workers/checkpoint_codec.py").read_bytes()),
            "frame_count": len(frames),
            "targets_sha256": digest(json_bytes(targets)),
        }
        # The population identity includes every projected fact and binding.
        from dataclasses import asdict

        self.identity["population_sha256"] = digest(json_bytes([asdict(f) for f in frames]))

    def batch(self) -> list[int]:
        selected = []
        for _ in range(CONFIG["batch_size"]):
            if self.cursor == len(self.order):
                self.order = torch.randperm(len(self.frames), generator=self.sampler).tolist()
                self.cursor = 0
                self.epoch += 1
            selected.append(self.order[self.cursor])
            self.cursor += 1
        return selected

    @training_scope
    def advance(self) -> dict[str, Any]:
        if self.step >= self.total_steps:
            raise ValueError("bounded T0 update budget exhausted")
        warmup = max(1, math.ceil(self.total_steps * CONFIG["warmup_fraction"]))
        scale = (
            (self.step + 1) / warmup
            if self.step < warmup
            else (1 + math.cos(math.pi * (self.step - warmup) / (self.total_steps - warmup))) / 2
        )
        for group in self.optimizer.param_groups:
            group["lr"] = CONFIG["lr"] * scale
        indices = self.batch()
        self.model.train()
        self.optimizer.zero_grad(set_to_none=True)
        loss_sum = 0.0
        for i in indices:
            logits = self.model(self.frames[i])
            loss = F.cross_entropy(logits.unsqueeze(0), torch.tensor([self.targets[i]]))
            if not bool(torch.isfinite(loss)):
                raise ValueError("non-finite M-std loss")
            (loss / len(indices)).backward()
            loss_sum += float(loss.detach())
        self.optimizer.step()
        self.step += 1
        value = {
            "step": self.step,
            "batch_indices": indices,
            "loss": loss_sum / len(indices),
            "lr": self.optimizer.param_groups[0]["lr"],
            "examples": len(indices),
        }
        self.history.append(value)
        return value

    def checkpoint(self) -> bytes:
        return encode_checkpoint(
            {
                "schema": "stpd/p1-mstd-training-checkpoint-1",
                "identity": self.identity,
                "model": self.model.state_dict(),
                "optimizer": self.optimizer.state_dict(),
                "rng": {k: v for k, v in self._numerics.rng.items() if k != "torch_cuda"},
                "sampler": {
                    "generator": self.sampler.get_state(),
                    "order": self.order,
                    "cursor": self.cursor,
                    "epoch": self.epoch,
                },
                "step": self.step,
                "history": self.history,
            }
        )

    def restore(self, raw: bytes) -> None:
        value = decode_checkpoint(raw)
        if (
            value["schema"] != "stpd/p1-mstd-training-checkpoint-1"
            or value["identity"] != self.identity
        ):
            raise ValueError("checkpoint identity does not match this M-std execution")
        self.model.load_state_dict(value["model"], strict=True)
        self.optimizer.load_state_dict(value["optimizer"])
        self._numerics.rng = {
            "torch_cpu": value["rng"]["torch_cpu"],
            "python": value["rng"]["python"],
            "torch_cuda": None,
        }
        sampler = value["sampler"]
        self.sampler.set_state(sampler["generator"])
        self.order, self.cursor, self.epoch = sampler["order"], sampler["cursor"], sampler["epoch"]
        self.step, self.history = value["step"], value["history"]


def maximum_difference(left: Any, right: Any) -> float:
    if isinstance(left, torch.Tensor):
        if (
            not isinstance(right, torch.Tensor)
            or left.shape != right.shape
            or left.dtype != right.dtype
        ):
            raise ValueError("resume tensor structure differs")
        return float((left.double() - right.double()).abs().max()) if left.numel() else 0.0
    if isinstance(left, dict):
        if left.keys() != right.keys():
            raise ValueError("resume mapping differs")
        return max((maximum_difference(v, right[k]) for k, v in left.items()), default=0.0)
    if isinstance(left, (list, tuple)):
        if len(left) != len(right):
            raise ValueError("resume sequence differs")
        return max(
            (maximum_difference(a, b) for a, b in zip(left, right, strict=True)), default=0.0
        )
    if left != right:
        raise ValueError("resume scalar differs")
    return 0.0
