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
import contextlib
import copy
import dataclasses
import hashlib
import json
import os
import pickle
import queue
import re
import signal
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import AuthenticationError
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, NamedTuple, Protocol, cast

import torch

from spireagent.json_boundary import BoundaryError

from .data import digest, json_bytes
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


# ---------------------------------------------------------------------------
# stpd/rl-inference-3 (P-REWRITE WP4, fast_chain only; the S2-1 service above is
# the old chain and is unchanged). One message = u32 envelope length, the
# envelope (json_bytes), then the binary section that score_request rows point
# into. Rows from every connection share one queue (no per-connection or 8-row
# cap). Spawned prepare processes decode and prepare; one spawned device process
# only forwards (no GIL shared with this process's per-row work); segments and
# the per-segment mu belong to the WP5 assembler, called around every row.
# ---------------------------------------------------------------------------
V3_SCHEMA = "stpd/rl-inference-3"
V3_REFUSALS = frozenset({"MU_NOT_RESIDENT", "EPISODE_NOT_OPEN", "IDENTITY_MISMATCH",
                         "ROW_DECODE_FAILED", "NONFINITE_SCORES", "SERVICE_STOPPING"})
_ENVELOPE = struct.Struct("<I")
V3_MAX_MESSAGE = 256 * 1024 * 1024


def pack_message(envelope: dict[str, Any], binary: bytes | bytearray = b"") -> bytes:
    body = json_bytes(envelope)
    return _ENVELOPE.pack(len(body)) + body + bytes(binary)


def unpack_message(data: bytes) -> tuple[dict[str, Any], memoryview]:
    if len(data) < _ENVELOPE.size:
        raise BoundaryError("rl_inference_3", "message_truncated")
    (length,) = _ENVELOPE.unpack_from(data)
    if _ENVELOPE.size + length > len(data):
        raise BoundaryError("rl_inference_3", "message_truncated")
    try:
        envelope = json.loads(data[_ENVELOPE.size:_ENVELOPE.size + length])
    except ValueError as error:
        raise BoundaryError("rl_inference_3", "envelope_json") from error
    if not isinstance(envelope, dict) or envelope.get("schema") != V3_SCHEMA:
        raise BoundaryError("rl_inference_3", "envelope_schema")
    return envelope, memoryview(data)[_ENVELOPE.size + length:]


def python_env_sha256() -> str:
    """Interpreter plus every installed distribution and version (S2 r4)."""
    import importlib.metadata
    import platform

    packages = sorted({(d.metadata["Name"] or "").lower(): d.version
                       for d in importlib.metadata.distributions()}.items())
    return digest(json_bytes({"python": sys.version,
                              "implementation": platform.python_implementation(),
                              "machine": platform.machine(), "packages": packages}))


def _audited_decode(raw: bytes, audit_sha: str | None, compiler: Any
                    ) -> tuple[Any, str | None, dict[str, Any] | None]:
    """Hot path: decode with the row layout's own structure checks only.

    An audited row (design §2.7: fixed before the game opens, about 1%, plus every
    R7 game) also recomputes `row_sha256` and runs the full index/shape validator.
    Returns (frame or None, refusal code, mismatch detail); `audit_mismatch`
    means only the audit caught it, which stops this build's output. The detail
    (expected vs recomputed digest, validator error) is kept with the original.
    """
    from .rows_encoder_wire import decode_row, row_sha256

    try:
        return decode_row(raw, validator=compiler if audit_sha else None,
                          expected_sha256=audit_sha), None, None
    except ValueError as error:
        if audit_sha is None:
            return None, "ROW_DECODE_FAILED", None
        audit_error = str(error)[:500]
    try:
        decode_row(raw, validator=None, expected_sha256=None)
    except ValueError:
        return None, "ROW_DECODE_FAILED", None
    return None, "audit_mismatch", {"expected_row_sha256": audit_sha,
                                    "recomputed_row_sha256": row_sha256(raw),
                                    "error": audit_error}


_SAMPLING_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def check_sampling(sampling: Any) -> str | None:
    """The episode_open `sampling` against the rl-inference-3 schema; None if valid.

    T in [1, 3], uniform_mix in [0, 0.1], both classes, seed and arm_id strings,
    no other keys (#431 review M1): the draw must never meet a value it cannot
    use, and out-of-range arms would silently change the training data.
    """
    if not isinstance(sampling, dict):
        return "sampling_not_an_object"
    if set(sampling) != {"mode", "seed", "arm_id", "classes"}:
        return "sampling_keys"
    if sampling["mode"] != "temperature_uniform":
        return "sampling_mode"
    seed = sampling["seed"]
    if type(seed) is not str or not 1 <= len(seed) <= 128:
        return "sampling_seed"
    if type(sampling["arm_id"]) is not str or not _SAMPLING_ID.match(sampling["arm_id"]):
        return "sampling_arm_id"
    classes = sampling["classes"]
    if not isinstance(classes, dict) or set(classes) != {"combat", "noncombat"}:
        return "sampling_classes"
    for policy in classes.values():
        if not isinstance(policy, dict) or set(policy) != {"temperature", "uniform_mix"}:
            return "sampling_class_keys"
        temperature, mix = policy["temperature"], policy["uniform_mix"]
        if type(temperature) not in (int, float) or not 1.0 <= temperature <= 3.0:
            return "sampling_temperature"
        if type(mix) not in (int, float) or not 0.0 <= mix <= 0.1:
            return "sampling_uniform_mix"
    return None


class _ArenaPart(NamedTuple):
    """A prepared part written in the shared arena: where it is, and its rows."""

    part: Any  # stpd.rl.batch_preparation.ForwardPart
    rows: int
    slot: tuple[int, int]  # the job's slot in the arena (parts, then the join plan)


class _JoinRef(NamedTuple):
    """A job joined ahead of the device: its C-SCALE JoinPlan, in arena[start:end]."""

    plan: Any  # stpd.rl.batch_preparation.JoinPlan
    start: int
    end: int


def _plan_join(arena: torch.Tensor, live: list[_ArenaPart]) -> tuple[_JoinRef, list[int]]:
    """Plan a job's join in its own slot, right after its parts (one H2D for both)."""
    from stpd.rl.batch_preparation import plan_forward_join

    start, end = live[0].slot
    parts = [dataclasses.replace(item.part, start=item.part.start - start,
                                 end=item.part.end - start) for item in live]
    counts = [item.rows for item in live]
    plan = plan_forward_join(arena[start:end], parts, max(part.end for part in parts),
                             rows=tuple(range(sum(counts))))
    return _JoinRef(plan, start, end), counts


def _prepare_worker(jobs: Connection, outputs: list[Connection], vocabulary: dict[str, Any],
                    cpu: int | None, keep: int, arena: torch.Tensor | None = None) -> None:
    """Spawned CPU preparation for one sub-group: decode rows, then rows-v2 groups.

    Jobs come from the service's dispatcher; the prepared part goes straight to
    the device process, so the service process never unpickles a group. Tensors
    cross as torch shared-memory handles (torch.multiprocessing, file_system
    strategy): the device process maps them instead of copying them. The last
    `keep` parts stay referenced here so a segment is never released before the
    device process has mapped it (at most in_flight parts are unconsumed).

    With an `arena` (the default service shape), the part's forward inputs are
    written in place into the job's slice of the shared arena (C-SCALE
    `write_forward_inputs`, int32 indices) and only the small `ForwardPart`
    crosses: no group pickling, no mapping, no `concatenate_groups`.
    """
    import collections
    from multiprocessing.reduction import ForkingPickler

    import torch.multiprocessing

    if cpu is not None:
        os.sched_setaffinity(0, {cpu})  # type: ignore[attr-defined]
    torch.set_num_threads(1)
    # Pipe ends received at spawn are inheritable; the torch_shm_manager this
    # process starts would keep them open and hide this worker's death from the
    # device process (no EOF). Keep them private to this process.
    for end in (jobs, *outputs):
        os.set_inheritable(end.fileno(), False)
    torch.multiprocessing.set_sharing_strategy("file_system")
    sent: collections.deque[dict[str, Any]] = collections.deque(maxlen=keep)
    from .frame_compiler import FrameCompiler
    from .rows_scorer import prepare

    compiler = FrameCompiler(vocabulary)
    jobs.send_bytes(b"ready")
    while True:
        try:
            message = jobs.recv_bytes()
        except EOFError:
            return
        if not message:
            return
        # rows: [(raw, audit_sha256 | None, catalog_len, floor, (tail_mu, tail_floor) | None)]
        job_id, part, parts, mu, rows, span = pickle.loads(message)
        frames, floors, kept, failed, audit = [], [], [], {}, []
        tails: dict[str, list[tuple[int, Any, int]]] = {}
        for index, (raw, audit_sha, catalog_len, floor, tail) in enumerate(rows):
            frame, code, detail = _audited_decode(raw, audit_sha, compiler)
            if code == "audit_mismatch":
                audit.append((index, detail))
                code = "ROW_DECODE_FAILED"
            if frame is None:
                failed[index] = code
                continue
            if len(frame.choices) != catalog_len:  # #418 review S5 (every row)
                failed[index] = "IDENTITY_MISMATCH"
                continue
            frames.append(frame)
            floors.append(floor)
            kept.append(index)
            if tail is not None:
                tails.setdefault(tail[0], []).append((index, frame, tail[1]))
        group: Any = None
        overflow = None
        try:
            group = prepare(frames, floors) if frames else None
        except ValueError:
            failed.update(dict.fromkeys(kept, "ROW_DECODE_FAILED"))
            kept, tails = [], {}
        if group is not None and arena is not None:
            from stpd.rl.batch_preparation import write_forward_inputs
            start, end, slot_start, slot_end = span
            try:
                written = write_forward_inputs(arena[start:end], 0, group)
            except ValueError as error:
                if str(error) != "forward_arena_overflow":
                    raise
                # The job's slice is too small: stop loudly, never truncate or
                # send the group another way (the service stops on this).
                from stpd.rl.batch_preparation import forward_arena_bytes
                overflow = {"need": forward_arena_bytes(group), "have": end - start,
                            "rows": len(kept)}
                failed.update(dict.fromkeys(kept, "SERVICE_STOPPING"))
                kept, tails, group = [], {}, None
            else:
                group = _ArenaPart(dataclasses.replace(written, start=written.start + start,
                                                       end=written.end + start), len(kept),
                                   (slot_start, slot_end))
        tail_parts = []
        for tail_mu, members in tails.items():
            indices = [index for index, _, _ in members]
            try:
                tail_group = prepare([frame for _, frame, _ in members],
                                     [floor for _, _, floor in members])
            except ValueError:
                tail_parts.append((tail_mu, indices, None))
                continue
            tail_parts.append((tail_mu, indices, tail_group))
        prepared = {"job": job_id, "part": part, "parts": parts, "mu": mu, "group": group,
                    "kept": kept, "lengths": [len(frame.choices) for frame in frames],
                    "failed": failed, "audit": audit, "tails": tail_parts,
                    "overflow": overflow}
        # every part of one job goes to the same joiner (or the device itself)
        outputs[job_id % len(outputs)].send_bytes(
            bytes(ForkingPickler.dumps(prepared, pickle.HIGHEST_PROTOCOL)))
        sent.append(prepared)


def audit_pick(game_instance_id: str, step_index: Any, threshold: int) -> bool:
    """Reproducible spot-check pick: seeded by the game id and step, no unseeded random."""
    pick = hashlib.sha256(f"rl-inference-3-audit\0{game_instance_id}\0{step_index}".encode())
    return int.from_bytes(pick.digest()[:8], "big") < threshold


def _join_parts(groups: list[Any]) -> tuple[Any, list[int]]:
    """One forward group from a job's prepared sub-groups.

    The sub-groups are joined by the C-SCALE `concatenate_groups` (lead 11-09 (a);
    one logic with `prepare_group`); each part's rows count from 0, so the joined
    rows are `range(total)` and output rows come back in part order.
    """
    counts = [len(group.rows) for group in groups]
    if len(groups) == 1:
        return groups[0], counts
    from stpd.rl.batch_preparation import concatenate_groups

    return concatenate_groups(groups, rows=tuple(range(sum(counts)))), counts


def _forward_parts(scorer: Any, groups: list[Any]) -> list[tuple[torch.Tensor, ...]]:
    """Score one job's prepared sub-groups with one mu in one forward."""
    return _score_joined(scorer, *_join_parts(groups))


def _score_joined(scorer: Any, joined: Any, counts: list[int]) -> list[tuple[torch.Tensor, ...]]:
    tensors = scorer.forward_tensors(joined)
    out, start = [], 0
    for count in counts:
        out.append(tuple(value[start:start + count] for value in tensors))
        start += count
    return out


class _Collector:
    """Waits for all parts of a job, then joins their groups into one batch."""

    def __init__(self, join: Callable[[list[Any]], tuple[Any, list[int]]] | None = None
                 ) -> None:
        self.waiting: dict[int, list[dict[str, Any]]] = {}
        self.join = join or _join_parts

    def add(self, part: dict[str, Any]) -> tuple[list[dict[str, Any]], Any] | None:
        parts = self.waiting.setdefault(part["job"], [])
        parts.append(part)
        if len(parts) < part["parts"]:
            return None
        del self.waiting[part["job"]]
        parts.sort(key=lambda item: item["part"])
        live = [p["group"] for p in parts if p["group"] is not None]
        joining = time.perf_counter()
        try:
            group: Any = self.join(live) if live else None
        except Exception as failure:  # refused loudly by the service
            group = RuntimeError(f"{type(failure).__name__}: {str(failure)[:200]}")
        parts[0]["join_seconds"] = time.perf_counter() - joining
        parts[0]["load_seconds"] = sum(p["load_seconds"] for p in parts)
        return parts, group


def _receive(reader: Connection) -> dict[str, Any]:
    loading = time.perf_counter()
    message = cast(dict[str, Any], pickle.loads(reader.recv_bytes()))
    message["load_seconds"] = time.perf_counter() - loading
    return message


def _join_worker(inputs: list[Connection], output: Connection, keep: int,
                 arena: torch.Tensor | None = None) -> None:
    """Spawned joiner: maps a job's prepared parts and joins them into one group.

    The joined group goes to the device process as shared memory too, so the
    device loader maps one group per job instead of mapping and joining every
    part (the loader thread was the limit). Any prepare worker gone ends this
    process, so the device process sees it and the service fails loudly.

    With the arena, a job's parts are already in its slot: the joiner only plans
    the join (C-SCALE `plan_forward_join`, all host index maps) and writes the
    plan after the parts, so the device process runs `stage_joined` alone.
    """
    import collections
    from multiprocessing.connection import wait
    from multiprocessing.reduction import ForkingPickler

    import torch.multiprocessing

    torch.set_num_threads(1)
    for end in (*inputs, output):
        os.set_inheritable(end.fileno(), False)
    torch.multiprocessing.set_sharing_strategy("file_system")
    collector = (_Collector() if arena is None
                 else _Collector(lambda live: _plan_join(arena, live)))
    sent: collections.deque[Any] = collections.deque(maxlen=keep)
    while True:
        for source in wait(inputs):
            try:
                part = _receive(cast(Connection, source))
            except EOFError:
                return
            done = collector.add(part)
            if done is None:
                continue
            parts, group = done
            message = {"joined": True, "parts": parts, "group": group}
            output.send_bytes(bytes(ForkingPickler.dumps(message, pickle.HIGHEST_PROTOCOL)))
            sent.append(message)


def _device_worker(control: Connection, inputs: list[Connection], output: Connection,
                   device: str, bf16: bool, compile: bool, threads: int,
                   arena: torch.Tensor | None = None) -> None:
    """Spawned device process: models and forwards only, no per-row Python work.

    The loading thread reads joined jobs from the joiners (or, without joiners,
    prepared parts from every prepare worker, joining a job's parts once all are
    there); the forward thread scores the previous job
    meanwhile and sends CPU tensors back. The service process does the per-row
    checks, draws and replies (no GIL shared with the forward).
    """
    import io
    from multiprocessing.connection import wait

    import torch.multiprocessing

    from .rows_scorer import RowsScorer

    torch.set_num_threads(threads)
    torch.multiprocessing.set_sharing_strategy("file_system")
    scorers: dict[str, Any] = {}
    collector = _Collector()
    if arena is not None:
        from stpd.rl.batch_preparation import pin_host_arena, stage_forward_parts, stage_joined

        target = torch.device(device)
        if target.type == "cuda":
            pin_host_arena(arena)  # one async DMA per job; raises if CUDA refuses

        def stage(written: list[Any]) -> tuple[Any, list[int]]:
            counts = [item.rows for item in written]
            return stage_forward_parts(arena, [item.part for item in written], target,
                                       rows=tuple(range(sum(counts)))), counts

        collector = _Collector(stage)
    joined: queue.Queue[tuple[list[dict[str, Any]], Any] | None] = queue.Queue(maxsize=2)

    def forward() -> None:
        try:
            while (item := joined.get()) is not None:
                parts, group = item
                output.send_bytes(pickle.dumps(_device_job(scorers, parts, group),
                                               protocol=pickle.HIGHEST_PROTOCOL))
        except BaseException as failure:
            # The forward thread is gone (#431 review S1): end the whole device
            # process, so the service sees EOF and stops loudly, never waits.
            print(json.dumps({"rl_inference_3_forward_thread_failed":
                              f"{type(failure).__name__}: {str(failure)[:200]}"}),
                  file=sys.stderr, flush=True)
            os._exit(1)

    threading.Thread(target=forward, daemon=True, name="rl-inference-3-forward").start()
    control.send_bytes(b"ready")
    for end in (control, *inputs, output):
        os.set_inheritable(end.fileno(), False)
    sources = list(inputs)  # every joiner or prepare worker, for the whole service life
    while True:
        ready = wait([control, *sources])
        if control in ready:  # models first: a published mu is resident before its rows
            message = control.recv_bytes()
            if not message:
                return
            kind, key, blob = pickle.loads(message)
            if kind == "model":
                model = torch.load(io.BytesIO(blob), map_location=device, weights_only=False)
                model.eval().requires_grad_(False)
                scorers[key] = RowsScorer(model, bf16=bf16, compile=compile)
            else:
                scorers.pop(key, None)
            control.send_bytes(b"ok")
            continue
        for source in ready:
            try:
                part = _receive(cast(Connection, source))
            except EOFError:
                # A joiner or prepare worker is gone: its parts never arrive. End
                # this process so the service fails every outstanding row loudly.
                return
            if part.get("joined"):
                parts, group = part["parts"], part["group"]
                parts[0]["load_seconds"] += part["load_seconds"]
                if arena is not None and isinstance(group, tuple):
                    ref, counts = group
                    staging = time.perf_counter()
                    try:
                        group = (stage_joined(arena[ref.start:ref.end], ref.plan, target),
                                 counts)
                    except Exception as failure:  # refused loudly by the service
                        group = RuntimeError(f"{type(failure).__name__}: {str(failure)[:200]}")
                    parts[0]["stage_seconds"] = time.perf_counter() - staging
                joined.put((parts, group))
                continue
            done = collector.add(part)
            if done is not None:
                joined.put(done)


def _device_job(scorers: dict[str, Any], parts: list[dict[str, Any]],
                joined: Any) -> list[dict[str, Any]]:
    out = []
    mu = parts[0]["mu"]
    scorer = scorers.get(mu)
    live = [part for part in parts if part["group"] is not None]
    started = time.perf_counter()
    outputs: dict[int, Any] = {}
    error: tuple[str, str] | None = None
    overflow = None
    # A join, stage or forward that raises is the fast chain's own failure, not a
    # fact of the rows (#431 review S3, design §2.6): SERVICE_STOPPING and the
    # service stops. Only non-finite scores (RowsScorer.rows) are NONFINITE_SCORES.
    fatal: str | None = None
    if isinstance(joined, Exception) and "forward_arena_overflow" in str(joined):
        error = ("SERVICE_STOPPING", "")  # the join plan does not fit its slot
        overflow = {"plan": str(joined)[:200]}
    elif isinstance(joined, Exception):
        fatal = f"join {type(joined).__name__}: {str(joined)[:200]}"
    elif live and scorer is None:
        error = ("MU_NOT_RESIDENT", "")
    elif live:
        try:
            for part, tensors in zip(live, _score_joined(scorer, *joined), strict=True):
                outputs[part["part"]] = tensors
        except Exception as failure:
            fatal = f"forward {type(failure).__name__}: {str(failure)[:200]}"
    part_tails: list[list[tuple[Any, Any]]] = []
    for part in parts:
        tails: list[tuple[Any, Any]] = []
        part_tails.append(tails)
        for tail_mu, indices, group in part["tails"]:
            tail_scorer = scorers.get(tail_mu)
            if group is None:
                tails.append((indices, "ROW_DECODE_FAILED"))
            elif tail_scorer is None:
                tails.append((indices, "MU_NOT_RESIDENT"))
            else:
                try:
                    tails.append((indices, tail_scorer.forward_tensors(group)))
                except Exception as failure:
                    fatal = fatal or f"tail {type(failure).__name__}: {str(failure)[:200]}"
                    tails.append((indices, "SERVICE_STOPPING"))
    if fatal is not None:
        error = ("SERVICE_STOPPING", "")
        outputs = {}
    for part, tails in zip(parts, part_tails, strict=True):
        out.append({"job": part["job"], "part": part["part"], "kept": part["kept"],
                    "lengths": part["lengths"], "failed": part["failed"],
                    "audit": part["audit"], "tails": tails, "error": error,
                    "overflow": part.get("overflow") or overflow, "fatal": fatal,
                    "outputs": outputs.get(part["part"])})
    if out:
        out[0]["device_seconds"] = time.perf_counter() - started
        out[0]["forwards"] = 1 if live and error is None else 0  # parts are joined
        out[0]["forward_rows"] = sum(len(part["kept"]) for part in live)
        out[0]["load_seconds"] = parts[0].get("load_seconds", 0.0)
        out[0]["join_seconds"] = parts[0].get("join_seconds", 0.0)
        out[0]["stage_seconds"] = parts[0].get("stage_seconds", 0.0)
    return out


def _begin_refusal(error: BaseException) -> str:
    """Refusal code for an assembler `begin_row` rejection."""
    if type(error).__name__ == "MuNotResident":
        return "MU_NOT_RESIDENT"
    code = error.args[0] if error.args else ""
    if code in {"episode_not_open", "episode_already_failed", "row_after_terminal"}:
        return "EPISODE_NOT_OPEN"
    return "IDENTITY_MISMATCH"


@dataclass
class _V3Row:
    link: _V3Link
    packet: _V3Packet
    index: int
    row: dict[str, Any]
    raw: bytes
    sampling: dict[str, Any]
    plan: Any = None
    audit: bool = False


@dataclass
class _V3Packet:
    packet_id: int
    results: list[dict[str, Any] | None]
    remaining: int


class _V3Link:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.send_lock = threading.Lock()
        self.attached = False
        self.aggregator_id: str | None = None
        self.last_request_id = 0

    def send(self, envelope: dict[str, Any]) -> None:
        with self.send_lock:
            self.connection.send_bytes(pack_message(envelope))


class FastInferenceService:
    """`stpd/rl-inference-3` scorer for many node aggregators on one device.

    `assembler` is the WP5 `FastSegmentAssembler` (or anything with its
    open_episode / begin_row / finish_row / abort_row / step_outcome /
    close_episode / offer_mu methods). Models are published per mu handoff;
    a mu the assembler evicts is dropped here.

    Processes: this one (links, assembler, per-row checks, draws, replies),
    `prepare_workers` spawned CPU preparers and one spawned device process that
    only forwards. Batching is adaptive: at most `in_flight` jobs are between
    dispatch and reply, so under load rows wait for a slot and the next job
    (up to `max_job_rows`) grows; with a free slot a job leaves after `window_ms`.
    """

    def __init__(self, vocabulary: dict[str, Any], compiler_identity: dict[str, Any],
                 assembler: Any, *, training_generation: str,
                 address: tuple[str, int] | None = None, authkey: bytes | None = None,
                 max_rows: int = 1024, window_ms: float = 1.0, prepare_workers: int = 2,
                 prepare_cpus: list[int] | None = None, in_flight: int | None = None,
                 max_job_rows: int = 256, job_parts: int = 1, join_workers: int = 0,
                 arena_row_bytes: int | None = 256 * 1024, device: str | None = None,
                 device_threads: int = 2, bf16: bool = True, compile: bool = False,
                 audit_fraction: float = 0.01, failure_dir: str | Path | None = None,
                 encoder_sha256: str | None = None, env_sha256: str | None = None) -> None:
        import multiprocessing
        from multiprocessing.connection import Listener

        from .rows_encoder import encoder_digest
        from .rows_encoder_wire import FORMAT

        if type(max_rows) is not int or not 1 <= max_rows <= 4096:
            raise ValueError("invalid_rl_inference_3_max_rows")
        if prepare_cpus is not None and len(prepare_cpus) != prepare_workers:
            raise ValueError("prepare_cpus_per_worker")
        if not 1 <= job_parts <= prepare_workers or max_job_rows < 1 or join_workers < 0:
            raise ValueError("invalid_rl_inference_3_job_shape")
        if arena_row_bytes is not None and arena_row_bytes < 1:
            raise ValueError("invalid_rl_inference_3_arena")
        if not 0.0 <= audit_fraction <= 1.0:
            raise ValueError("invalid_rl_inference_3_audit_fraction")
        self.vocabulary = copy.deepcopy(vocabulary)
        self.compiler_identity = copy.deepcopy(compiler_identity)
        self.assembler = assembler
        self.training_generation = training_generation
        self.max_rows, self.window = max_rows, window_ms / 1000
        self.max_job_rows, self.job_parts = max_job_rows, job_parts
        self.bf16, self.compile = bf16, compile
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.expected_encoder = {"implementation": "python",
                                 "sha256": encoder_sha256 or encoder_digest()}
        self.row_format = FORMAT
        self.env_sha256 = env_sha256 or python_env_sha256()
        self.service_instance = "svc-" + os.urandom(8).hex()
        # Spot checks (design §2.7): a row is audited when its game is marked
        # (R7) or a digest seeded by the game id and step picks it, so the pick
        # is fixed before the game opens and reproducible (#434 review).
        self.audit_fraction = audit_fraction
        self.audit_threshold = int(audit_fraction * 2 ** 64)
        self.full_audit: set[str] = set()
        self.audits: dict[str, int] = {}  # full checks per game
        self.failure_dir = Path(failure_dir) if failure_dir is not None else None
        self.audit_failed = threading.Event()
        self.stop_reason: str | None = None  # why the service stopped serving
        self.sampling: dict[str, dict[str, Any]] = {}
        self.queue: queue.Queue[_V3Row | None] = queue.Queue()
        self.stopping = threading.Event()
        self.stats: dict[str, Any] = {"rows": 0, "batches": 0, "jobs": 0, "refused": {},
                                      "attach_refused": 0, "batch_rows_max": 0,
                                      "job_rows_max": 0, "device_seconds": 0.0,
                                      "load_seconds": 0.0, "join_seconds": 0.0,
                                      "stage_seconds": 0.0,
                                      "finish_seconds": 0.0, "dispatch_seconds": 0.0,
                                      "audited": 0, "audit_failures": 0,
                                      "episodes_refused": 0}
        context: Any = multiprocessing.get_context("spawn")
        control, control_child = context.Pipe(duplex=True)
        results, results_child = context.Pipe(duplex=False)
        worker_pipes = [context.Pipe(duplex=True) for _ in range(prepare_workers)]
        self.slot_count = max(1, in_flight if in_flight is not None else prepare_workers)
        # One shared host arena (C-SCALE round 3): slot s holds one in-flight job,
        # part p of a job owns slice p of its slot; the device process pins it.
        self.arena: torch.Tensor | None = None
        self.slot_bytes = 0
        if arena_row_bytes is not None:
            from stpd.rl.batch_preparation import allocate_forward_arena

            self.slot_bytes = arena_row_bytes * max_job_rows
            self.arena = allocate_forward_arena(self.slot_bytes * self.slot_count, shared=True)
        self.free_slots = list(range(self.slot_count))
        # Prepared parts go to joiner j = job % join_workers (pipes[worker][j]),
        # each joiner sends joined jobs to the device; without joiners every
        # worker sends its parts to the device, which joins them itself.
        sinks = max(1, join_workers)
        part_pipes = [[context.Pipe(duplex=False) for _ in range(sinks)]
                      for _ in range(prepare_workers)]
        joined_pipes = [context.Pipe(duplex=False) for _ in range(join_workers)]
        device_inputs = ([reader for reader, _ in joined_pipes] if join_workers
                         else [pipes[0][0] for pipes in part_pipes])
        self.device_process = context.Process(
            target=_device_worker, daemon=True, name="rl-inference-3-device",
            args=(control_child, device_inputs, results_child,
                  self.device, bf16, compile, device_threads, self.arena))
        self.device_process.start()
        self.joiners: list[Any] = []
        for index, (_, writer) in enumerate(joined_pipes):
            process = context.Process(target=_join_worker, daemon=True,
                                      name=f"rl-inference-3-join-{index}",
                                      args=([pipes[index][0] for pipes in part_pipes], writer,
                                            self.slot_count + 2, self.arena))
            process.start()
            self.joiners.append(process)
        self.workers: list[tuple[Any, Connection, queue.SimpleQueue[bytes]]] = []
        for index, ((parent, child), pipes) in enumerate(
                zip(worker_pipes, part_pipes, strict=True)):
            process = context.Process(target=_prepare_worker, daemon=True,
                                      name=f"rl-inference-3-prepare-{index}",
                                      args=(child, [writer for _, writer in pipes],
                                            self.vocabulary,
                                            None if prepare_cpus is None else prepare_cpus[index],
                                            self.slot_count + 2, self.arena))
            process.start()
            child.close()
            self.workers.append((process, parent, queue.SimpleQueue()))
        for pipes in part_pipes:
            for reader, writer in pipes:
                reader.close()
                writer.close()
        control_child.close()
        results_child.close()
        self.control, self.results = control, results
        self.control_lock = threading.Lock()
        if control.recv_bytes() != b"ready":
            raise BoundaryError("rl_inference_3", "device_process_start_failed")
        for reader, writer in joined_pipes:  # the joiners and the device own them now
            reader.close()
            writer.close()
        for _, connection, _ in self.workers:
            if connection.recv_bytes() != b"ready":
                raise BoundaryError("rl_inference_3", "prepare_worker_start_failed")
        self.outstanding = [0] * prepare_workers
        self.jobs: dict[int, list[Any]] = {}  # job id -> [remaining parts, parts' rows, workers]
        self.jobs_lock = threading.Lock()
        self.results_done = False
        self.job_ids = 0
        self.slots = threading.BoundedSemaphore(self.slot_count)
        self.auth = authkey or os.urandom(32)
        ip = socket.gethostbyname(socket.gethostname())
        # Listener's default backlog is 1: aggregators connecting together would
        # sit in SYN retries for seconds; every node may (re)attach at once.
        self.listener = Listener(address or (ip, 0), authkey=self.auth, backlog=256)
        self.address = cast(tuple[str, int], self.listener.address)
        self.links: list[threading.Thread] = []
        self.threads = [threading.Thread(target=self._accept, daemon=True,
                                         name="rl-inference-3-accept"),
                        threading.Thread(target=self._batch, daemon=True,
                                         name="rl-inference-3-batch"),
                        threading.Thread(target=self._results, daemon=True,
                                         name="rl-inference-3-results")]
        self.threads += [threading.Thread(target=self._sender, args=(index,), daemon=True,
                                          name=f"rl-inference-3-send-{index}")
                         for index in range(prepare_workers)]
        for thread in self.threads:
            thread.start()

    # -- mu ------------------------------------------------------------------
    def _device_control(self, kind: str, key: str, blob: bytes | None) -> None:
        self.control.send_bytes(pickle.dumps((kind, key, blob), protocol=pickle.HIGHEST_PROTOCOL))
        if self.control.recv_bytes() != b"ok":
            raise BoundaryError("rl_inference_3", "device_control_failed")

    def publish(self, model: Any, handoff: Any) -> bool:
        """Offer a mu to the assembler; the device holds it only when accepted.

        The model reaches the device process before the assembler can plan a
        row on it, so a planned row never finds its mu missing in transit.
        """
        import io

        from .rows_scorer import RowsScorer

        RowsScorer(model, bf16=self.bf16, compile=self.compile)  # immutable eval snapshot
        # Ship parameters and construction, not this process's runtime caches
        # (a torch.compile wrapper set by rows_forward cannot be pickled).
        shipped = copy.copy(model)
        shipped.__dict__ = {key: value for key, value in model.__dict__.items()
                            if not key.startswith("_rows_block")}
        blob = io.BytesIO()
        torch.save(shipped, blob)
        key = handoff.snapshot_sha256
        with self.control_lock:
            self._device_control("model", key, blob.getvalue())
            offer = self.assembler.offer_mu(handoff)
            if not offer.accepted:
                self._device_control("drop", key, None)
            for evicted in offer.evicted:
                self._device_control("drop", evicted, None)
            if getattr(offer, "generation_changed", False):
                self.training_generation = handoff.training_generation
        return bool(offer.accepted)

    def mark_full_audit(self, game_instance_id: str) -> None:
        """Audit every row of this game (an R7 game); call before its episode_open."""
        self.full_audit.add(game_instance_id)

    def _audited(self, row: dict[str, Any]) -> bool:
        game = str(row.get("game_instance_id"))
        if game in self.full_audit:
            return True
        return audit_pick(game, row.get("step_index"), self.audit_threshold)

    def audit_count(self, game_instance_id: str) -> int:
        """Rows of this game that got the full check (row hash + validator)."""
        return self.audits.get(game_instance_id, 0)

    # -- links ---------------------------------------------------------------
    def _accept(self) -> None:
        while not self.stopping.is_set():
            try:
                connection = self.listener.accept()
            except (AuthenticationError, EOFError, ConnectionError):
                continue
            except OSError:
                return
            if self.stopping.is_set():
                connection.close()
                return
            tcp_nodelay(connection)
            thread = threading.Thread(target=self._link, args=(_V3Link(connection),),
                                      daemon=True, name="rl-inference-3-link")
            self.links[:] = [item for item in self.links if item.is_alive()]
            self.links.append(thread)
            thread.start()

    def _link(self, link: _V3Link) -> None:
        try:
            while not self.stopping.is_set():
                data = link.connection.recv_bytes(V3_MAX_MESSAGE)
                envelope, binary = unpack_message(data)
                request_id = envelope.get("request_id")
                kind = envelope.get("kind")
                if kind == "score_request":
                    self._score_request(link, envelope, binary)
                    continue
                if kind == "step_outcome":  # names its row; carries no request_id
                    if not link.attached:
                        raise BoundaryError("rl_inference_3", "attach_required")
                    self.assembler.step_outcome(envelope)
                    continue
                if type(request_id) is not int or request_id <= link.last_request_id:
                    raise BoundaryError("rl_inference_3", "request_id_order")
                link.last_request_id = request_id
                if kind == "attach":
                    self._attach(link, envelope)
                elif not link.attached:
                    raise BoundaryError("rl_inference_3", "attach_required")
                elif kind == "episode_open":
                    problem = check_sampling(envelope.get("sampling"))
                    if problem is not None:
                        # Refused loudly: the game is never opened, so its rows
                        # come back EPISODE_NOT_OPEN; other games are untouched.
                        self.stats["episodes_refused"] += 1
                        print(json.dumps({"rl_inference_3_episode_refused": problem,
                                          "game_instance_id":
                                              envelope.get("game_instance_id")}),
                              file=sys.stderr, flush=True)
                        continue
                    self.assembler.open_episode(envelope)
                    self.sampling[envelope["game_instance_id"]] = envelope["sampling"]
                elif kind == "episode_close":
                    self.assembler.close_episode(envelope)
                    self.sampling.pop(str(envelope.get("game_instance_id")), None)
                else:
                    raise BoundaryError("rl_inference_3", "unexpected_message")
        except EOFError:
            pass  # the aggregator closed the link
        except (OSError, ValueError, TypeError, KeyError) as error:
            # A failed link owns only its own rows (they time out on the
            # environment side as not_started); nothing is retried here.
            print(json.dumps({"rl_inference_3_link_closed": type(error).__name__,
                              "code": getattr(error, "code", None)}), file=sys.stderr,
                  flush=True)
        finally:
            link.connection.close()

    def _attach(self, link: _V3Link, envelope: dict[str, Any]) -> None:
        checks = (envelope.get("chain") == "fast_chain",
                  envelope.get("python_env_sha256") == self.env_sha256,
                  envelope.get("compiler_identity") == self.compiler_identity,
                  envelope.get("encoder") == self.expected_encoder,
                  envelope.get("row_format") == self.row_format,
                  not link.attached)
        if not all(checks):
            self.stats["attach_refused"] += 1
            names = ("chain", "python_env_sha256", "compiler_identity", "encoder",
                     "row_format", "attach_once")
            raise BoundaryError("rl_inference_3", "attach_identity_differs:" + ",".join(
                name for name, ok in zip(names, checks, strict=True) if not ok))
        link.attached, link.aggregator_id = True, envelope.get("aggregator_id")
        link.send({"schema": V3_SCHEMA, "kind": "attached", "request_id": envelope["request_id"],
                   "service_instance": self.service_instance,
                   "training_generation": self.training_generation,
                   "max_rows_per_packet": self.max_rows})

    def _score_request(self, link: _V3Link, envelope: dict[str, Any],
                       binary: memoryview) -> None:
        if not link.attached:
            raise BoundaryError("rl_inference_3", "attach_required")
        rows = envelope.get("rows")
        if (type(envelope.get("packet_id")) is not int or not isinstance(rows, list)
                or not 1 <= len(rows) <= self.max_rows):
            raise BoundaryError("rl_inference_3", "invalid_score_request")
        packet = _V3Packet(envelope["packet_id"], [None] * len(rows), len(rows))
        for index, row in enumerate(rows):
            request_id = row.get("request_id") if isinstance(row, dict) else None
            if type(request_id) is not int or request_id <= link.last_request_id:
                raise BoundaryError("rl_inference_3", "request_id_order")
            link.last_request_id = request_id
            payload = row.get("payload") or {}
            offset, length = payload.get("offset"), payload.get("length")
            if (type(offset) is not int or type(length) is not int or offset < 0
                    or length < 1 or offset + length > len(binary)):
                raise BoundaryError("rl_inference_3", "row_payload_range")
            sampling = self.sampling.get(str(row.get("game_instance_id")))
            item = _V3Row(link, packet, index, row, bytes(binary[offset:offset + length]),
                          sampling or {}, audit=self._audited(row))
            if type(row.get("catalog_len")) is not int or not 1 <= row["catalog_len"] <= 4096:
                self._refuse(item, "IDENTITY_MISMATCH", abort=False)
                continue
            if self.stopping.is_set() or self.audit_failed.is_set():
                self._refuse(item, "SERVICE_STOPPING", abort=False)
                continue
            if sampling is None:
                self._refuse(item, "EPISODE_NOT_OPEN", abort=False)
                continue
            try:
                item.plan = self.assembler.begin_row(row, item.raw)
            except ValueError as error:  # AssemblyError: the plan was not created
                self._refuse(item, _begin_refusal(error), abort=False)
                continue
            if item.audit:
                self.stats["audited"] += 1
                game = str(row.get("game_instance_id"))
                self.audits[game] = self.audits.get(game, 0) + 1
            self.queue.put(item)

    # -- batching (adaptive: a job waits for a slot, so jobs grow under load) --
    def _batch(self) -> None:
        pending: list[_V3Row] = []
        closing = False
        while not (closing and not pending):
            if not pending:
                item = self.queue.get()
                if item is None:
                    break
                pending.append(item)
            slot_waited = not self.slots.acquire(blocking=False)
            if slot_waited:
                self.slots.acquire()
            closing = self._drain(pending, closing)
            if self.stopping.is_set() and self.stop_reason is not None:
                # Stopped by a failure (#431 review S2): rows that began in the
                # assembler are refused (and aborted) instead of dispatched.
                for item in pending:
                    if item.packet.results[item.index] is None:
                        self._refuse(item, "SERVICE_STOPPING")
                pending = []
                with contextlib.suppress(ValueError):
                    self.slots.release()
                continue
            if not slot_waited and not closing and len(pending) < self.max_job_rows:
                deadline = time.perf_counter() + self.window
                while len(pending) < self.max_job_rows:
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    try:
                        item = self.queue.get(timeout=remaining)
                    except queue.Empty:
                        break
                    if item is None:
                        closing = True
                        break
                    pending.append(item)
            # One forward per mu: rows of another mu stay pending for the next job.
            mu = pending[0].plan.mu.snapshot_sha256
            job: list[_V3Row] = []
            rest: list[_V3Row] = []
            for item in pending:
                (job if len(job) < self.max_job_rows
                 and item.plan.mu.snapshot_sha256 == mu else rest).append(item)
            pending = rest
            self._dispatch(job)

    def _drain(self, pending: list[_V3Row], closing: bool) -> bool:
        while not closing:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                return True
            pending.append(item)
        return closing

    def _dispatch(self, rows: list[_V3Row]) -> None:
        dispatching = time.perf_counter()
        parts = min(self.job_parts, len(rows))
        size = -(-len(rows) // parts)
        chunks = [rows[start:start + size] for start in range(0, len(rows), size)]
        with self.jobs_lock:
            gone = self.results_done or not self.free_slots
            if not gone:
                self.job_ids += 1
                job_id = self.job_ids
                workers = sorted(range(len(self.workers)), key=self.outstanding.__getitem__)
                workers = workers[:len(chunks)]
                for worker in workers:
                    self.outstanding[worker] += 1
                slot = self.free_slots.pop()  # a slot semaphore is held: one is free
                self.jobs[job_id] = [len(chunks), chunks, workers, slot]
        if gone:  # the results thread has ended (#431 review S2): never dispatched
            for item in rows:
                self._refuse(item, "SERVICE_STOPPING")
            return
        # parts share the first 3/4 of the slot (64 B aligned); a joiner writes the
        # join plan after them (C-SCALE: plan ~30% of the parts' bytes)
        share = self.slot_bytes * 3 // 4 // len(chunks) // 64 * 64
        base = slot * self.slot_bytes
        self.stats["jobs"] += 1
        self.stats["job_rows_max"] = max(self.stats["job_rows_max"], len(rows))
        mu = rows[0].plan.mu.snapshot_sha256
        for part, (chunk, worker) in enumerate(zip(chunks, workers, strict=True)):
            payload = []
            for item in chunk:
                tail = item.plan.tail
                needs_tail = tail is not None and (
                    tail.mu != item.plan.mu.snapshot_sha256 or tail.floor != item.row["floor"])
                payload.append((item.raw,
                                item.row["payload"]["row_sha256"] if item.audit else None,
                                item.row["catalog_len"], item.row["floor"],
                                (tail.mu, tail.floor) if needs_tail else None))
            span = (base + part * share, base + (part + 1) * share, base,
                    base + self.slot_bytes)
            self.workers[worker][2].put(pickle.dumps(
                (job_id, part, len(chunks), mu, payload, span), protocol=pickle.HIGHEST_PROTOCOL))
        self.stats["dispatch_seconds"] += time.perf_counter() - dispatching

    def _sender(self, index: int) -> None:
        _, connection, outbox = self.workers[index]
        while True:
            message = outbox.get()
            try:
                connection.send_bytes(message)
            except (OSError, ValueError):
                return  # the device side sees the worker gone; close() fails loudly
            if not message:
                return

    def _results(self) -> None:
        try:
            while True:
                try:
                    message = self.results.recv_bytes()
                except (EOFError, OSError):
                    break
                finishing = time.perf_counter()
                for part in pickle.loads(message):
                    self._finish_part(part)
                self.stats["finish_seconds"] += time.perf_counter() - finishing
        except Exception as error:  # never die silently (#431 review M1)
            self._stop("results_failed", f"{type(error).__name__}: {str(error)[:200]}")
            print(json.dumps({"rl_inference_3_results_failed": type(error).__name__}),
                  file=sys.stderr, flush=True)
        with self.jobs_lock:
            left = list(self.jobs.values())
            self.jobs.clear()
            self.results_done = True  # no job dispatched from now on is ever finished
        if not self.stopping.is_set():
            self._stop("device_process_gone", len(left))
        self.stopping.set()
        # Unblock the batcher (#431 review S2): it refuses what it holds.
        with contextlib.suppress(ValueError):
            while True:
                self.slots.release()
        for _, chunks, *_ in left:
            for chunk in chunks:
                for item in chunk:
                    if item.packet.results[item.index] is None:
                        self._refuse(item, "SERVICE_STOPPING")

    def _finish_part(self, part: dict[str, Any]) -> None:
        from .rows_scorer import RowsScorer

        with self.jobs_lock:
            job = self.jobs[part["job"]]
            rows = job[1][part["part"]]
        if "device_seconds" in part:
            self.stats["device_seconds"] += part["device_seconds"]
            self.stats["load_seconds"] += part["load_seconds"]
            self.stats["join_seconds"] += part["join_seconds"]
            self.stats["stage_seconds"] += part["stage_seconds"]
            self.stats["batches"] += part["forwards"]
            self.stats["batch_rows_max"] = max(self.stats["batch_rows_max"],
                                               part["forward_rows"] * part["forwards"])
        if part.get("overflow"):
            print(json.dumps({"rl_inference_3_arena_overflow": part["overflow"]}),
                  file=sys.stderr, flush=True)
            self.stopping.set()
        for index, code in part["failed"].items():
            self._refuse(rows[index], code)
        if part["audit"]:
            self._audit_failure([(rows[index], detail) for index, detail in part["audit"]])
        kept = [rows[index] for index in part["kept"]]
        if part.get("fatal"):
            self._stop("device_job_failed", part["fatal"])
        if self.audit_failed.is_set():
            for item in kept:  # scored or not, nothing more enters a segment
                self._refuse(item, "SERVICE_STOPPING")
        elif part["error"] is not None:
            code, message = part["error"]
            if message:
                print(json.dumps({"rl_inference_3_forward_failed": message}), file=sys.stderr,
                      flush=True)
            for item in kept:
                self._refuse(item, code)
        elif kept:
            tails: dict[int, list[float] | str] = {}
            for indices, values in part["tails"]:
                if isinstance(values, str):
                    tails.update(dict.fromkeys(indices, values))
                    continue
                lengths = [rows[index].row["catalog_len"] for index in indices]
                for index, value in zip(indices, RowsScorer.rows(values, lengths), strict=True):
                    tails[index] = ("NONFINITE_SCORES" if value is None
                                    else value.outcome_probabilities)
            outputs = RowsScorer.rows(part["outputs"], part["lengths"])
            for index, item, output in zip(part["kept"], kept, outputs, strict=True):
                tail = tails.get(index)
                if output is None:
                    self._refuse(item, "NONFINITE_SCORES")
                elif isinstance(tail, str):
                    self._refuse(item, tail)
                else:
                    self._scored(item, output, tail)
        with self.jobs_lock:
            job[0] -= 1
            self.outstanding[job[2][part["part"]]] -= 1
            done = job[0] == 0
            if done:
                del self.jobs[part["job"]]
                # its outputs are on the host: the device read its arena slice
                self.free_slots.append(job[3])
        if done:
            self.slots.release()

    def _stop(self, reason: str, detail: Any = None) -> None:
        """Stop serving loudly: later rows are refused SERVICE_STOPPING."""
        if self.stop_reason is None:
            self.stop_reason = reason
            print(json.dumps({"rl_inference_3_stopping": reason, "detail": detail}),
                  file=sys.stderr, flush=True)
        self.stopping.set()

    def build_output_stopped(self) -> str | None:
        """Why this build's output must stop (a spot-check mismatch), else None.

        For WP7 / the learner (#431 review S4): once set, no row of this service
        reaches a segment any more and the build's output is to be stopped.
        """
        return "audit_mismatch" if self.audit_failed.is_set() else None

    def _audit_failure(self, rows: list[tuple[_V3Row, dict[str, Any] | None]]) -> None:
        """A spot check differs: stop this build's output, keep the originals (§2.7)."""
        self.audit_failed.set()
        self._stop("audit_mismatch")
        self.stats["audit_failures"] += len(rows)
        for item, detail in rows:
            meta = {"rl_inference_3_audit_mismatch": {"detail": detail, **{
                key: item.row.get(key) for key in ("game_instance_id", "step_index",
                                                   "consumption_id", "catalog_sha256")}}}
            print(json.dumps(meta), file=sys.stderr, flush=True)
            if self.failure_dir is not None:
                self.failure_dir.mkdir(parents=True, exist_ok=True)
                name = f"audit-{self.service_instance}-{item.row.get('request_id')}"
                (self.failure_dir / f"{name}.row").write_bytes(item.raw)
                (self.failure_dir / f"{name}.json").write_bytes(json_bytes(
                    {"row": item.row, "aggregator_id": item.link.aggregator_id,
                     "mismatch": detail,
                     "game_full_checks": self.audits.get(str(item.row.get("game_instance_id")),
                                                         0)}))

    def _scored(self, item: _V3Row, output: Any, tail: list[float] | None) -> None:
        from .sampling import distribution, draw

        row, plan, sampling = item.row, item.plan, item.sampling
        decision_class = "combat" if row["in_combat"] else "noncombat"
        policy = sampling["classes"][decision_class]
        try:
            _, behavior = distribution(output.scores, policy["temperature"],
                                       policy["uniform_mix"])
        except ValueError:
            self._refuse(item, "NONFINITE_SCORES")
            return
        selected = draw(behavior, sampling["seed"], row["consumption_id"])
        scored = {"request_id": row["request_id"], "game_instance_id": row["game_instance_id"],
                  "catalog_sha256": row["catalog_sha256"], "status": "scored",
                  "mu_snapshot_sha256": plan.mu.snapshot_sha256,
                  "segment_id": plan.segment_id, "row_in_segment": plan.row_in_segment,
                  "selected_index": selected, "behavior_q": behavior,
                  "selected_q": behavior[selected],
                  "applied": {"arm_id": sampling["arm_id"],
                              "decision_class": decision_class,
                              "temperature": policy["temperature"],
                              "uniform_mix": policy["uniform_mix"]}}
        try:
            self.assembler.finish_row(plan, scored, output.outcome_probabilities, tail)
        except ValueError:
            self._refuse(item, "IDENTITY_MISMATCH")
            return
        self.stats["rows"] += 1
        self._complete(item, scored)

    def _refuse(self, item: _V3Row, code: str, *, abort: bool = True) -> None:
        if abort and item.plan is not None:
            # A plan that is already resolved raises; the refusal stands.
            with contextlib.suppress(ValueError):
                self.assembler.abort_row(item.plan, code)
        refused = self.stats["refused"]
        refused[code] = refused.get(code, 0) + 1
        self._complete(item, {"request_id": item.row.get("request_id"),
                              "game_instance_id": item.row.get("game_instance_id"),
                              "catalog_sha256": item.row.get("catalog_sha256"),
                              "status": "refused", "error_code": code})

    def _complete(self, item: _V3Row, result: dict[str, Any]) -> None:
        packet = item.packet
        with item.link.send_lock:
            packet.results[item.index] = result
            packet.remaining -= 1
            done = packet.remaining == 0
        if done:
            # A gone link drops the response; its rows time out as not_started.
            with contextlib.suppress(OSError, ValueError):
                item.link.send({"schema": V3_SCHEMA, "kind": "score_response",
                                "packet_id": packet.packet_id, "results": packet.results})

    def close(self) -> dict[str, Any]:
        self.stopping.set()
        try:
            with socket.create_connection(self.address, timeout=2):
                pass
        except OSError:
            pass
        self.listener.close()
        self.queue.put(None)
        self.threads[1].join(60)
        deadline = time.perf_counter() + 60
        while self.jobs and time.perf_counter() < deadline and self.device_process.is_alive():
            time.sleep(0.01)
        for _, _, outbox in self.workers:
            outbox.put(b"")
        for process, connection, _ in self.workers:
            process.join(5)
            if process.is_alive():
                process.kill()
            connection.close()
        for process in self.joiners:  # they end once every prepare worker is gone
            process.join(5)
            if process.is_alive():
                process.kill()
        with self.control_lock, contextlib.suppress(OSError):
            self.control.send_bytes(b"")
        self.device_process.join(10)
        if self.device_process.is_alive():
            self.device_process.kill()
        self.threads[2].join(10)
        self.control.close()
        batches = self.stats["batches"]
        return {"schema": V3_SCHEMA, "service_instance": self.service_instance, **self.stats,
                "audited_games": len(self.audits),
                "batch_rows_mean": self.stats["rows"] / batches if batches else 0}
