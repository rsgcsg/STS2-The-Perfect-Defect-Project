"""Read-only shard preparation shared by thread and spawned-process readers.

Admission, sampling, optimizer state and publication stay with the learner owner.
Process IPC is private to children launched from this source, never artifact input.
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

import torch

from spireagent.json_boundary import BoundaryError
from spireagent.rl_exchange import Exchange
from spireagent.storage.blobs import StoreError
from stpd.p1.export import input_spec, load_model, runtime_projection
from stpd.p1.input import project
from stpd.p1.model import MStd
from stpd.p1.tensor_input import ModelFrame, compile_frame

from .reward import episode_reward

if TYPE_CHECKING:
    from .learner import LearnerConfig

T = TypeVar("T")
Rows = list[dict[str, Any]]
Samples = list[tuple[Any, int, float, float, str]]


def project_episode(episode: dict[str, Any], *, spec: dict[str, Any],
                    projection: dict[str, Any], reference: MStd, precompile: bool,
                    timing: dict[str, float] | None = None) -> Samples:
    if episode["identity"]["input_spec"] != spec:
        raise BoundaryError("rl_learner", "trajectory_input_spec_differs")
    frames = []
    for step in episode["steps"]:
        observation = episode["observations"][step["observation_index"]]["observation"]
        frame = project(observation, input_projection=projection)
        action = [c["choice_id"] for c in frame.choices].index(step["action_id"])
        prepared: ModelFrame = frame
        if precompile:
            began = time.monotonic()
            with torch.device("cpu"):
                prepared = compile_frame(frame, reference)
            if timing is not None:
                timing["input_prepare"] = (timing.get("input_prepare", 0.0)
                                           + time.monotonic() - began)
        behavior = step["behavior"]
        frames.append((prepared, action, behavior["selected_probability"],
                       behavior["model_probabilities"][step["action_index"]],
                       f"{episode['run_id']}:{step['index']}"))
    return frames


def episode_started_at(episode: dict[str, Any], record: dict[str, Any]) -> float:
    games = [g for g in record.get("games", ()) if g.get("run_id") == episode["run_id"]]
    value = games[0].get("started_at") if len(games) == 1 else None
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value)):
        raise BoundaryError("rl_learner", "time_freshness_requires_original_game_start")
    return float(value)


def episode_stale(episode: dict[str, Any], record: dict[str, Any], *,
                  max_age: float | None, max_staleness: int, version: int, warmup: bool) -> bool:
    if max_age is None:
        return bool(version - record["model_version"] > max_staleness)
    if episode_reward(episode)[0] is None or warmup:
        return False
    return time.time() - episode_started_at(episode, record) > max_age


def read_once(exchange: Exchange, read_shard: Callable[..., Iterable[dict[str, Any]]],
              work: Path, key: str, *, restoring: bool,
              stale: Callable[[dict[str, Any], dict[str, Any]], bool],
              projector: Callable[[dict[str, Any], dict[str, float]], Samples]) -> Rows:
    began = time.monotonic()
    record = exchange.shard_record(key)
    rows: Rows = []
    if record.get("shard_artifact_id") is None:
        return [{"record": record, "episode": None, "key": key,
                 "reader_seconds": time.monotonic() - began}]
    with tempfile.TemporaryDirectory(prefix="rl-shard-", dir=work) as tmp:
        for episode in read_shard(exchange.store, record["shard_artifact_id"], scratch=Path(tmp)):
            if episode["identity"]["model_artifact_id"] != record["model_artifact_id"]:
                raise BoundaryError("rl_learner", "shard_record_model_mismatch")
            eligible = restoring or (not stale(episode, record)
                and not {s["behavior"]["mode"] for s in episode["steps"]} - {"temperature_uniform"}
                and episode_reward(episode)[0] is not None)
            projection_began = time.monotonic()
            timing: dict[str, float] = {}
            samples = projector(episode, timing) if eligible else []
            rows.append({"record": record, "episode": episode, "key": key, "samples": samples,
                "projection_seconds": time.monotonic() - projection_began,
                "input_prepare_seconds": timing.get("input_prepare", 0.0)})
    if rows:
        rows[-1]["reader_seconds"] = time.monotonic() - began
    return rows


def log_io_error(work: Path, operation: str, error: StoreError, attempt: int, retry: bool) -> None:
    value = {"at": time.time(), "kind": "read_failed", "operation": operation,
        "error_type": type(error).__name__, "error_code": error.code,
        "attempt": attempt, "retry": retry, **getattr(error, "diagnostic", {})}
    with (work / "io-errors.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")


def bounded_read(call: Callable[[], T], *, attempts: int, retry_seconds: float,
                 on_error: Callable[[StoreError, int, bool], None],
                 on_elapsed: Callable[[float], None]) -> T:
    for attempt in range(1, attempts + 1):
        began = time.monotonic()
        try:
            return call()
        except StoreError as error:
            retry = bool(getattr(error, "diagnostic", {}).get("retryable")) and attempt < attempts
            on_error(error, attempt, retry)
            if not retry:
                raise
            time.sleep(retry_seconds * attempt)
        finally:
            on_elapsed(time.monotonic() - began)
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class ReadRequest:
    key: str
    version: int
    warmup: bool
    restoring: bool = False


class ProcessReader:
    def __init__(self, config: LearnerConfig, work: Path, read_shard: Callable[..., Any],
                 reference_package: Path, reference_sha256: str) -> None:
        self.config, self.work, self.read_shard = config, work, read_shard
        torch.set_num_threads(1)
        self.exchange = Exchange(config.peer, config.run)
        # Independently verify and load the same frozen CPU reference. Never copy
        # a live model, optimizer, CUDA context or parent's RNG into a child.
        self.reference, _ = load_model(reference_package / "model.tensor-tree", reference_sha256)
        self.reference.eval()
        self.projection = runtime_projection(self.reference.vocabulary)
        self.spec = input_spec(self.reference.vocabulary, self.projection)
        print(json.dumps({"stage": "rl_reader_process_ready", "pid": os.getpid(),
            "torch_threads": torch.get_num_threads(), "reference_sha256": reference_sha256,
            "input_spec": self.spec}), file=sys.stderr, flush=True)

    def read(self, request: ReadRequest) -> Rows:
        return read_once(self.exchange, self.read_shard, self.work, request.key,
            restoring=request.restoring,
            stale=lambda episode, record: episode_stale(episode, record,
                max_age=self.config.max_episode_age_seconds,
                max_staleness=self.config.max_staleness, version=request.version,
                warmup=request.warmup),
            projector=lambda episode, timing: project_episode(episode, spec=self.spec,
                projection=self.projection, reference=self.reference,
                precompile=bool(self.config.performance
                                and self.config.performance.precompile_inputs),
                timing=timing))


_worker: ProcessReader


def initialize_process_reader(config: LearnerConfig, work: Path, read_shard: Callable[..., Any],
                              reference_package: Path, reference_sha256: str) -> None:
    global _worker
    _worker = ProcessReader(config, work, read_shard, reference_package, reference_sha256)


def process_read(request: ReadRequest) -> bytes:
    seconds = 0.0
    operation = "resume_shard" if request.restoring else "read_shard"

    def elapsed(value: float) -> None:
        nonlocal seconds
        seconds += value

    error = None
    rows: Rows = []
    try:
        rows = bounded_read(lambda: _worker.read(request),
            attempts=_worker.config.read_retry_attempts,
            retry_seconds=_worker.config.read_retry_seconds,
            on_error=lambda e, n, retry: log_io_error(_worker.work, operation, e, n, retry),
            on_elapsed=elapsed)
    except BoundaryError as exc:
        # BoundaryError's constructor differs from Exception.args; transport its
        # classified fields explicitly instead of losing it in spawn unpickling.
        error = {"store": isinstance(exc, StoreError), "stage": exc.stage,
                 "code": exc.code, "recovery": exc.recovery,
                 "diagnostic": getattr(exc, "diagnostic", None)}
    # One byte payload avoids one shared-memory file descriptor per small tensor.
    return pickle.dumps({"rows": rows, "seconds": seconds, "operation": operation,
                         "error": error}, protocol=5)


def decode_process_read(raw: bytes, timings: dict[str, float]) -> Rows:
    began = time.monotonic()
    value = pickle.loads(raw)
    timings["read_" + value["operation"]] += value["seconds"]
    timings["read_process_decode"] += time.monotonic() - began
    error = value["error"]
    if error is not None:
        exc = (StoreError(error["code"], error["recovery"]) if error["store"]
               else BoundaryError(error["stage"], error["code"], error["recovery"]))
        if error["diagnostic"] is not None:
            cast(Any, exc).diagnostic = error["diagnostic"]
        raise exc
    return cast(Rows, value["rows"])
