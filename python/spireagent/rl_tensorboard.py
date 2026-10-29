"""TensorBoard scalars for an RL run, stored as closed append-only event files.

The learner rotates at checkpoints. Friday evaluation can call this module with
its original ledger summary; it does not need to change the frozen actor package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import uuid
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.rl_exchange import Exchange


class TensorBoardMetrics:
    def __init__(self, exchange: Exchange, directory: Path) -> None:
        self.exchange = exchange
        self.directory = directory / uuid.uuid4().hex
        self.writer: Any = None
        self.published: set[Path] = set()

    def _writer(self):
        from torch.utils.tensorboard import SummaryWriter

        if self.writer is None:
            self.writer = SummaryWriter(log_dir=str(self.directory))
            self.writer.add_text('algorithm/value_loss', 'not applicable: no critic', 0)
            self.writer.add_text('algorithm/state_value', 'not applicable: no critic', 0)
        return self.writer

    def scalars(self, values: dict[str, Any], step: int, walltime: float | None = None) -> None:
        writer = self._writer()
        for name, value in values.items():
            if value is not None:
                writer.add_scalar(name, float(value), step, walltime=walltime)

    def histograms(self, values: dict[str, list[int | float]], step: int) -> None:
        import numpy as np

        for name, rows in values.items():
            if not rows:
                continue
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in rows):
                raise BoundaryError('rl_tensorboard', 'nonfinite_histogram_value')
            self._writer().add_histogram(name, np.asarray(rows, dtype=float), step)

    def text(self, name: str, value: str, step: int) -> None:
        self._writer().add_text(name, value, step)

    def publish(self) -> list[str]:
        """Close before upload: no event object is ever overwritten as it grows."""
        if self.writer is None:
            return []
        self.writer.close()
        self.writer = None
        keys = []
        for path in sorted(self.directory.glob("events.out.tfevents.*")):
            if path in self.published:
                continue
            raw = path.read_bytes()
            # A content digest makes publication idempotent after a transfer error.
            key = (f"{self.exchange.prefix}tb/"
                   f"events.out.tfevents.{hashlib.sha256(raw).hexdigest()}")
            self.exchange.blobs.put_if_absent(key, raw)
            self.published.add(path)
            keys.append(key)
        return keys


def publish_evaluation(exchange: Exchange, directory: Path,
                       result: dict[str, Any]) -> list[str]:
    """Write caller-supplied completed evaluation facts, retaining unknown counts."""
    for name in ("version", "updates", "wins", "unknown"):
        if type(result.get(name)) is not int or result[name] < 0:
            raise BoundaryError("rl_tensorboard", "invalid_evaluation_metrics")
    floor = result.get("mean_max_floor")
    if floor is not None and (type(floor) not in (int, float) or not math.isfinite(floor)):
        raise BoundaryError("rl_tensorboard", "invalid_evaluation_metrics")
    kind = result.get("evaluation_kind", "fixed20")
    if kind not in ("fixed20", "confirmation", "quick", "milestone"):
        raise BoundaryError("rl_tensorboard", "invalid_evaluation_kind")
    planned = result.get("planned")
    if planned is not None and (type(planned) is not int or planned <= 0):
        raise BoundaryError("rl_tensorboard", "invalid_evaluation_metrics")
    prefix = "evaluation" if kind == "fixed20" else kind
    metrics = TensorBoardMetrics(exchange, directory)
    metrics.scalars({f"{prefix}/wins": result["wins"],
                     f"{prefix}/mean_max_floor": floor,
                     f"{prefix}/unknown": result["unknown"],
                     f"{prefix}/planned": planned,
                     f"{prefix}/version": result["version"]}, result["updates"],
                    result.get("evaluated_at", time.time()))
    return metrics.publish()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer-config", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    peer = json.loads(args.peer_config.read_bytes())
    result = json.loads(args.evaluation.read_bytes())
    keys = publish_evaluation(Exchange(peer, args.run), args.work, result)
    print(json.dumps({"event_keys": keys}))


if __name__ == "__main__":
    main()
