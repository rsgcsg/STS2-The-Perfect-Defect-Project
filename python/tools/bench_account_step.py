"""CPU micro-benchmark: controller seconds per SGD answer in `_account_step`.

Realistic RL5 shape: 4,125-row datasets in 4 minibatches, one epoch, every
row first-used once with a policy observation, 256 terminal games / 128 HP
battles in the auxiliary pools (rl5.json), and a ~200k-row admitted queue on
the owner (untouched by `_account_step`; present so its size cannot hide a
per-answer scan). Events go to the real telemetry executor; `publish_event`
serializes the event to JSON the way the exchange writes it, so the backpressure
cost on the controller is included. Only the controller thread is timed.

    python tools/bench_account_step.py [--datasets 40]
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stpd.rl.segment_learner import SegmentLearner  # noqa: E402
from stpd.rl.segments import AuxiliaryPool  # noqa: E402

ROWS, MINIBATCHES, QUEUE_ROWS = 4125, 4, 200_000
TERMINAL_GAMES, TERMINAL_ROWS, HP_BATTLES, HP_ROWS = 256, 16, 128, 8


class Exchange:
    def __init__(self) -> None:
        self.bytes = 0

    def publish_event(self, kind: str, **fields: object) -> None:
        self.bytes += len(json.dumps({"kind": kind, **fields}, default=str))

    def release_hot_payload(self, payload: object, owner: str) -> None:
        pass


def owner(rng: random.Random) -> SegmentLearner:
    self = SegmentLearner.__new__(SegmentLearner)
    self.first_use = self.first_use_multi = self.updates = self.datasets = 0
    self.version = 40
    self.condition = threading.Condition(threading.RLock())
    self.terminal = AuxiliaryPool(TERMINAL_GAMES, 2)
    self.hp = AuxiliaryPool(HP_BATTLES, 1)
    self.sgd = SimpleNamespace()
    self.sgd_owns_theta, self.sgd_rates, self.sgd_memory = True, (3e-4, 3e-4), {"peak": 1}
    self.settings = SimpleNamespace(epochs=1)
    self.config = SimpleNamespace(tensorboard_steps=10, operating_budget=None)
    self.metrics, self.wal, self.generation = None, None, 1
    self.device = torch.device("cpu")
    self.exchange = Exchange()
    self.telemetry = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bench-telemetry")
    self.telemetry_futures = deque()
    self.hot_references = {}
    self.queue = SimpleNamespace(rows=list(range(QUEUE_ROWS)))
    if hasattr(self, "_init_progress_telemetry"):
        self._init_progress_telemetry()
    self._forget_unreferenced = lambda: None
    self._schedule_publication = lambda: None
    self._release = lambda payload, owner: None
    for index in range(TERMINAL_GAMES):
        add_group(self.terminal, f"t{index}", TERMINAL_ROWS)
    for index in range(HP_BATTLES):
        add_group(self.hp, f"h{index}", HP_ROWS)
    return self


def add_group(pool: AuxiliaryPool, identity: str, rows: int) -> None:
    pool.add(identity, [{"key": f"{identity}:{i}",
                         "member": SimpleNamespace(segment_id=f"{identity}:s{i // 4}",
                                                   payload=object())}
                        for i in range(rows)])


def dataset(self: SegmentLearner, sequence: int, rng: random.Random):
    order = list(range(ROWS))
    rng.shuffle(order)
    size = -(-ROWS // MINIBATCHES)
    minibatch_rows = [order[i:i + size] for i in range(0, ROWS, size)]
    terminal = self.terminal.sample(256, rng)
    hp = self.hp.sample(256, rng)
    now = time.time()
    batch = SimpleNamespace(
        request=SimpleNamespace(
            identity=SimpleNamespace(sequence=sequence), minibatch_rows=minibatch_rows,
            auxiliary_minibatches=[rng.randrange(MINIBATCHES)
                                   for _ in range(len(terminal) + len(hp))],
            selection_rng_state=None),
        policy_mask=torch.tensor([rng.random() < 0.7 for _ in range(ROWS)]),
        metadata=[{"generated_timestamp": now - rng.random() * 60, "floor": rng.randrange(50),
                   "mu_version": 39, "mu_updates": 0} for _ in range(ROWS)],
        minibatch_count=MINIBATCHES)
    active = {"minibatch": 0, "epoch": 0, "used": [], "terminal": terminal, "hp": hp,
              "start_updates": self.updates}
    return active, batch


def answer(batch, index: int, rng: random.Random) -> tuple[dict, list]:
    mask = batch.policy_mask.tolist()
    samples = [{"row": row, "kl_mu_theta": rng.random() * 0.05, "ratio": 0.8 + rng.random() * 0.4,
                "outside_clip": rng.random() < 0.1, "ppo_objective_clipped": rng.random() < 0.1,
                "ratio_above_one": rng.random() < 0.5}
               for row in batch.request.minibatch_rows[index] if mask[row]]
    result = {"stepped": True, "kl_stopped": False, "td_loss": 1.0, "policy_loss": 0.1,
              "entropy": 1.0, "approx_kl_mu_theta": 0.01, "clip_fraction": 0.1,
              "terminal_loss": 0.2, "hp_loss": 0.3, "gradient_norm": 1.0,
              "sgd_timing": {"optimizer_seconds": 0.05}}
    return result, samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", type=int, default=40)
    args = parser.parse_args()
    rng = random.Random(7)
    self = owner(rng)
    seconds = []
    thread_cpu = [self.telemetry.submit(time.thread_time).result()]
    for sequence in range(args.datasets):
        self.active, self.active_batch = dataset(self, sequence, rng)
        self.next_active = None  # read by the prestart build; absent before
        active, batch = self.active, self.active_batch
        for index in range(MINIBATCHES):
            result, samples = answer(batch, index, rng)
            began = time.perf_counter()
            self._account_step(active, batch, index, MINIBATCHES, result, samples,
                               time.time(), self.version, self.updates)
            seconds.append(time.perf_counter() - began)
        for index in range(TERMINAL_GAMES // 8):  # pool turnover, as completed games arrive
            add_group(self.terminal, f"t{sequence}:{index}", TERMINAL_ROWS)
            add_group(self.hp, f"h{sequence}:{index}", HP_ROWS)
    drained = time.perf_counter()
    for future in self.telemetry_futures:
        future.result()
    thread_cpu.append(self.telemetry.submit(time.thread_time).result())
    self.telemetry.shutdown(wait=True)
    seconds.sort()
    print(json.dumps({
        "answers": len(seconds), "mean_s": statistics.fmean(seconds),
        "median_s": seconds[len(seconds) // 2],
        "p95_s": seconds[int(len(seconds) * 0.95)], "max_s": seconds[-1],
        "telemetry_drain_after_s": time.perf_counter() - drained,
        "telemetry_thread_cpu_s_per_answer": (thread_cpu[1] - thread_cpu[0]) / len(seconds),
        "event_bytes": self.exchange.bytes}, indent=1))


if __name__ == "__main__":
    main()
