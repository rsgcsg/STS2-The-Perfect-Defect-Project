"""RL5 S2-1: standalone batched mu scorer, separate from learner and actors.

The learner keeps owning grants, leases, commits and mu publication. Actors keep
their own control/dataplane channel to the learner and only send compiled public
frames here for scores (`segments.scoring_service`, explicit, default off). This
process pulls each published mu itself through the same node-cache transfer and
the same load checks as the actor-local scorer, places it on one device and
reuses CentralInferencePool batching, stream and text cache.

It decides no legality, holds no World, adds no hidden input and never resends
a score. A missing or retired mu is a typed error, never a substitute.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Protocol

import torch

from spireagent.json_boundary import BoundaryError

from .data import json_bytes
from .local_inference import CentralInferencePool, tcp_nodelay
from .text_cache import TextVectorCache

ROLE = "inference_service"


class MuSource(Protocol):
    """Where published mu snapshots come from; loads stay on CPU."""

    def latest(self) -> dict[str, Any]: ...

    def load(self, identity: dict[str, Any]) -> Any: ...


class NodeMuSource:
    """Learner `get_mu` through NodeMuCache, following learner generations.

    The learner attachment file is reread on every refresh, so a learner
    restart (new port, pool and generation) is followed without a restart here.
    """

    def __init__(self, learner_attachment: Path, root: Path, *, interval: float = 0,
                 keep: int = 18) -> None:
        # keep >= the service's resident limit, so any mu it can still hold in
        # memory can also be reloaded from this node after an eviction.
        self.path, self.root, self.interval, self.keep = learner_attachment, root, interval, keep
        self.cache: Any = None
        self.generation: str | None = None

    def _cache(self) -> Any:
        from spireagent.rl_socket_transport import NodeMuCache

        attachment = json.loads(self.path.read_bytes())
        generation = attachment["training_generation"]
        if self.cache is None or generation != self.generation:
            self.cache = NodeMuCache(attachment, self.root / attachment["run"] / generation,
                                     interval=self.interval, keep=self.keep,
                                     max_bytes=self.keep * 64 * 1024**2)
            self.generation = generation
        return self.cache

    def latest(self) -> dict[str, Any]:
        with self._cache().latest() as identity:
            return dict(identity)

    def load(self, identity: dict[str, Any]) -> Any:
        from .local_mu import load_behavior_mu

        cache = self._cache()
        if not (cache.root / identity["snapshot_sha256"] / "COMPLETE").is_file():
            # get_mu serves only the learner's latest; an older grant that this
            # node never pulled (or already trimmed) is not fetched elsewhere.
            raise BoundaryError("rl_mu", "MU_NOT_ON_SERVICE_NODE")
        return load_behavior_mu(identity, node_cache=cache, exchange=None,
                                cache_root=self.root, text_cache=TextVectorCache())


def _to_device(model: Any, device: torch.device) -> Any:
    if device.type == "cuda":
        stream = torch.cuda.Stream(device=device)
        with torch.cuda.stream(stream):
            model.to(device)
        stream.synchronize()
    else:
        model.to(device)
    return model.eval().requires_grad_(False)


class InferenceService(CentralInferencePool):
    unkeyed_operations = frozenset({"status", "adopt"})

    def __init__(self, source: MuSource, *, device: str = "cpu", max_batch: int = 64,
                 window_ms: float = 2, address: tuple[str, int] | None = None,
                 segment_steps: int = 64, max_resident: int = 16,
                 idle_seconds: float = 900, rendezvous: Path | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if max_resident < 2:
            raise ValueError("inference_service_requires_two_resident_mu")
        self.source, self.target = source, torch.device(device)
        self.max_resident, self.idle_seconds, self.clock = max_resident, idle_seconds, clock
        self.adopt_lock = threading.RLock()
        self.last_used: dict[str, float] = {}
        self.service_stats: dict[str, Any] = {"adopt": 0, "loads": 0, "load_seconds": 0.,
            "refresh_errors": 0, "last_refresh_error": None, "generation_changes": 0,
            "rebound": 0, "evicted": 0}
        self.rendezvous = rendezvous
        identity = source.latest()
        model = _to_device(source.load(identity), self.target)
        self.generation = identity["training_generation"]
        self.latest_identity = dict(identity)
        super().__init__(model, identity, control=self._control, max_batch=max_batch,
                         window_ms=window_ms, address=address, segment_steps=segment_steps,
                         compiler_identity=identity.get("compiler_identity"))
        self.last_used[identity["snapshot_sha256"]] = clock()
        self._write_rendezvous()

    def attachment(self) -> dict[str, Any]:
        return {**super().attachment(), "role": ROLE, "training_generation": self.generation}

    def _write_rendezvous(self) -> None:
        if self.rendezvous is not None:
            pending = self.rendezvous.with_suffix(".pending")
            pending.write_bytes(json_bytes(self.attachment()))
            pending.replace(self.rendezvous)

    def _actor(self, connection: Connection) -> None:
        tcp_nodelay(connection)
        super()._actor(connection)

    def _touch(self, key: str) -> None:
        self.last_used[key] = self.clock()  # a scored mu is in use, never "idle"

    def _load(self, identity: dict[str, Any]) -> None:
        key = identity["snapshot_sha256"]
        if identity.get("compiler_identity", self.compiler_identity) != self.compiler_identity:
            raise BoundaryError("rl_mu", "MU_COMPILER_IDENTITY_DIFFERS")
        started = time.perf_counter()
        try:
            model = _to_device(self.source.load(identity), self.target)
        except BoundaryError:
            raise
        except Exception as error:
            raise BoundaryError("rl_mu", "MU_LOAD_FAILED") from error
        self.publish(model, identity)
        newest = self.latest_identity["snapshot_sha256"]
        with self.model_lock:
            if newest in self.models:
                self.latest = newest  # an old grant's reload is not "latest"
        self.service_stats["loads"] += 1
        self.service_stats["load_seconds"] += time.perf_counter() - started
        self.last_used[key] = self.clock()

    def _trim(self) -> None:
        # Least recently scored/adopted first. Scores refresh last_used, so a mu
        # still serving segments or tails is not idle; the latest always stays.
        now = self.clock()
        with self.model_lock:
            ordered = sorted(self.last_used, key=self.last_used.__getitem__, reverse=True)
            keep = {k for k in ordered[:self.max_resident]
                    if now - self.last_used[k] <= self.idle_seconds}
            before = len(self.models)
            self.retire(keep)
            self.service_stats["evicted"] += before - len(self.models)
            self.last_used = {k: v for k, v in self.last_used.items() if k in self.models}

    def _rebind(self, identity: dict[str, Any]) -> None:
        # Same immutable weights/heads/numerics (snapshot_sha256), newer grant
        # identity (e.g. first mu after a CP resume). Old-generation tails are
        # refused by status, so no reply can carry the replaced identity.
        with self.model_lock:
            self.snapshots[identity["snapshot_sha256"]] = dict(identity)
        self.service_stats["rebound"] += 1

    def refresh(self) -> bool:
        """Pull the learner's latest mu; returns True when a new one was loaded."""
        with self.adopt_lock:
            identity = self.source.latest()
            key = identity["snapshot_sha256"]
            self.latest_identity = dict(identity)
            if identity["training_generation"] != self.generation:
                # Old-generation tails are refused by status, never rescored
                # here; drop every old-generation resident except this key.
                self.generation = identity["training_generation"]
                self.service_stats["generation_changes"] += 1
                with self.model_lock:
                    stale = [k for k, v in self.snapshots.items()
                             if v["training_generation"] != self.generation and k != key]
                    self.retire(set(self.models) - set(stale))
                    self.last_used = {k: v for k, v in self.last_used.items()
                                      if k in self.models}
                self._write_rendezvous()
            if key in self.models:
                if self.snapshots[key] != identity:
                    self._rebind(identity)
                return False
            self._load(identity)
            self._trim()
            return True

    def _control(self, value: dict[str, Any]) -> dict[str, Any]:
        operation = value["operation"]
        if operation == "status":
            with self.model_lock:
                resident = len(self.models)
            return {"training_generation": self.generation, "role": ROLE,
                    "latest": self.latest, "resident_mu": resident,
                    "device": str(self.device),
                    "stats": {**{k: self.stats[k] for k in ("requests", "batches", "errors")},
                              "score_seconds": self.stats["score_seconds"],
                              "batch_histogram": dict(self.stats.get("batch_histogram", {})),
                              **self.service_stats}}
        if operation != "adopt":
            raise BoundaryError("rl_mu", "INFERENCE_SERVICE_SCORES_ONLY")
        identity = value["identity"]
        key = identity["snapshot_sha256"]
        with self.adopt_lock:
            self.service_stats["adopt"] += 1
            if identity["training_generation"] != self.generation:
                self.refresh()  # one owned pull; never a different generation's mu
                if identity["training_generation"] != self.generation:
                    raise BoundaryError("rl_mu", "INFERENCE_SERVICE_GENERATION_DIFFERS")
            if key not in self.models:
                # The grant is normally the learner's latest: one pull loads it.
                self.refresh()
            if key not in self.models:
                # An older grant must already be in this node's cache; a
                # failed load is reported as is, never replaced by another mu.
                self._load(identity)
                self._trim()
            if self.snapshots[key] != identity:
                if identity != self.latest_identity:
                    raise BoundaryError("rl_mu", "MU_IDENTITY_CHANGED")
                self._rebind(identity)
            self.last_used[key] = self.clock()
        return {"snapshot_sha256": key}

    def close(self) -> dict[str, Any]:
        summary = super().close()
        return {**summary, "role": ROLE, "service": dict(self.service_stats),
                "batch_histogram": dict(self.stats.get("batch_histogram", {}))}


def serve(arguments: argparse.Namespace) -> dict[str, Any]:
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    # Same numerics choice as the actor-local scorer; part of adapter_identity().
    torch.backends.mha.set_fastpath_enabled(False)
    if "STPD_P1_LOCAL_INFERENCE_AUTH" not in os.environ:
        raise SystemExit("inference_service_requires_inherited_capability")
    source = NodeMuSource(Path(arguments.learner_attachment), Path(arguments.cache),
                          keep=arguments.max_resident + 2)
    service = InferenceService(source, device=arguments.device, max_batch=arguments.max_batch,
        window_ms=arguments.window_ms,
        address=(arguments.host, arguments.port) if arguments.host else None,
        rendezvous=Path(arguments.rendezvous), max_resident=arguments.max_resident)
    stopping = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopping.set())
    try:
        while not stopping.wait(arguments.refresh_seconds):
            try:
                service.refresh()
            except Exception as error:
                # A failed pull leaves resident mu untouched; adopt reports its
                # own typed error. Counted, not retried beyond the next tick.
                service.service_stats["refresh_errors"] += 1
                service.service_stats["last_refresh_error"] = {
                    "type": type(error).__name__, "code": getattr(error, "code", None),
                    "at": time.time()}
                print(json.dumps({"inference_service_refresh_error":
                                  service.service_stats["last_refresh_error"]}),
                      file=sys.stderr, flush=True)
    finally:
        report = service.close()
    return copy.deepcopy(report)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--learner-attachment", required=True)
    parser.add_argument("--rendezvous", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--device", default="cuda")
    # Default: this node's resolvable address on an ephemeral port.
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--max-batch", type=int, default=64)
    parser.add_argument("--window-ms", type=float, default=2)
    parser.add_argument("--max-resident", type=int, default=16)
    parser.add_argument("--refresh-seconds", type=float, default=2)
    parser.add_argument("--report")
    arguments = parser.parse_args(argv)
    report = serve(arguments)
    if arguments.report:
        Path(arguments.report).write_bytes(json_bytes(report))


if __name__ == "__main__":
    main()
