"""Fast data plane in shard processes (`dataplane.shards` > 0; default 0 = off).

With 0 the learner process receives, queues, evicts, selects and dispatches
preparation itself, under one condition lock and one GIL. With N > 0 that work
runs in N spawned shard processes. Each shard owns a fixed subset of the
`fast_handoff` Connections (round-robin in attach order, for the Connection's
whole life) and has its own intake ledger, segment queue, capacity eviction,
aux pools, selection RNG, source copies and batch preparer. The learner process
keeps what is global: dataset numbering and take order, the SGD process,
first-use accounting, mu publication, checkpoints, stop and fence.

First slice. A shard runs the learner's own data-plane methods unchanged: the
shard object is a `SegmentLearner` subclass whose constructor builds only the
data-plane state (it never calls `SegmentLearner.__init__`), driven by messages
that arrive in the order the learner's main loop used to call those methods.
With one shard, an input stream therefore gives the same selections as 0.

What differs with N > 1 (lead decision 11-13): one dataset's PPO segments and
aux rows come from one shard's connections only; queue capacity and both aux
pools are split evenly, so eviction drops the oldest unreferenced segment of
that shard, not of the whole learner.

Never retried, never re-routed: a shard that fails an operation, fences its
input or dies fences the whole learner (`_shard_fenced`); its connections are
not moved to another shard and nothing is read twice. Actor control requests
(instance admission / exclusion) carry no connection, so they go to every
shard; `control` below says how the replies and counters are combined.
"""
from __future__ import annotations

import collections
import copy
import faulthandler
import hashlib
import itertools
import multiprocessing
import os
import pickle
import queue
import random
import signal
import sys
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import Any, cast

from spireagent.json_boundary import BoundaryError

from .fast_source import FastIntakeError, FastSegmentIntake, FastSources, RoutedSources
from .segment_learner import (
    FAST_INSTANCE_ADMISSION,
    SegmentLearner,
    _pending_receipt,
    binary_preparer_arguments,
)
from .segments import AuxiliaryPool, SegmentQueue
from .sgd_process import _parent_death_signal

START_SECONDS = 600.0
REPLY_SECONDS = 120.0
CLOSE_SECONDS = 300.0
FAILED_CLOSE_SECONDS = 20.0  # a shard already known as failed gets no long wait
OWNER_MEMORY = 262144
# What a shard may send; anything else is an unreadable message (fence at once).
SHARD_MESSAGES = frozenset({"reply", "started", "ready", "fenced", "event", "claims",
                            "finished_instances", "receiver_done"})
# Intake counters driven by the broadcast actor control stream: every shard
# counts the same request once, so the learner-wide value is the largest, not
# the sum. Everything else is driven by a shard's own hand-off connections.
BROADCAST_INTAKE_COUNTERS = frozenset({
    "attested", "admission_generation_refused", "admission_seed_refused"})


class DataplaneShardFailed(BoundaryError):
    def __init__(self, code: str, facts: dict[str, Any]) -> None:
        super().__init__("rl_dataplane_shard", code)
        self.facts = facts

    def __str__(self) -> str:
        return f"{super().__str__()}; facts={self.facts}"


def shard_settings(settings: Any, prepared_batches: int, count: int) -> tuple[Any, int]:
    """One shard's even share: queue capacity, both aux pools, read-ahead count.

    The per-dataset prepared byte budget (`prepared_bytes // (read-ahead + 1)`)
    is kept, so a shard's batches are bounded exactly like the learner's. With
    one shard every value is unchanged."""
    ahead = max(1, prepared_batches // count)
    per_dataset = settings.prepared_bytes // (prepared_batches + 1)
    return replace(settings,
                   capacity_rows=settings.capacity_rows // count,
                   capacity_limit_rows=(None if settings.capacity_limit_rows is None
                                        else settings.capacity_limit_rows // count),
                   terminal_games=max(1, settings.terminal_games // count),
                   hp_battles=max(1, settings.hp_battles // count),
                   prepared_bytes=(settings.prepared_bytes if count == 1
                                   else per_dataset * (ahead + 1))), ahead


def shard_dataplane(dataplane: dict[str, Any], count: int,
                    settings: Any = None) -> dict[str, Any]:
    """Byte, slot, worker and connection budgets a shard takes its even share of
    (defaults are derived from the shard's own read-ahead count). Local
    preparation workers are split too (rounded up, at least one each), so N
    shards never start N times the configured local workers."""
    shared = dict(dataplane)
    if count > 1:
        workers = {"preparation_workers": (settings.preparation_workers
                                           if settings is not None else None),
                   "assembly_workers": 2, "label_workers": 1}
        for key, default in workers.items():
            value = shared.get(key, default)
            if type(value) is int and value > 0:
                shared[key] = -(-value // count)
        for key in ("source_bytes", "prepare_source_bytes"):
            if key in shared:
                shared[key] = shared[key] // count
        if "prepare_slots" in shared:
            shared["prepare_slots"] = max(4, -(-shared["prepare_slots"] // count))
        remote = shared.get("remote_prepare")
        if isinstance(remote, dict):
            # Every shard connects to every prepare host: its share of the
            # configured connections per host and of the client processes, so the
            # hosts see the configured totals (rounded up, at least one each).
            remote = dict(remote)
            for key in ("connections", "client_processes"):
                if type(remote.get(key)) is int and remote[key] > 0:
                    remote[key] = -(-remote[key] // count)
            shared["remote_prepare"] = remote
    return shared


def shard_budget(dataplane: dict[str, Any], settings: Any, prepared_batches: int,
                 count: int) -> tuple[int, int, int]:
    """(prepare source bytes, prepared bytes, preparation processes) of all shards
    together, for the learner's memory budget."""
    share, ahead = shard_settings(settings, prepared_batches, count)
    arguments = binary_preparer_arguments(shard_dataplane(dataplane, count, settings),
                                          share, ahead)
    remote = arguments["remote_prepare"]
    # Processes that will exist per shard: the shard itself, plus either its
    # remote-prepare client processes or (no remote prepare) its local pools.
    # With remote prepare the local pools are never submitted to: no workers.
    pools = (remote.get("client_processes", 0) if remote is not None
             else arguments["workers"] + arguments["assembly_workers"]
             + arguments["label_workers"])
    return (count * arguments["max_source_bytes"], count * share.prepared_bytes,
            count * (1 + pools))


def adopted_rng_state(committed: tuple[Any, ...], generation: str,
                      index: int) -> tuple[Any, ...]:
    """Shard `index`'s selection RNG when a CP written without shards is continued
    with shards (`dataplane.shards_adopt_unsharded_checkpoint`): shard 0 continues
    the committed RNG itself; every other shard gets a stream fixed by that
    committed state, the new training generation and its index (two adoptions
    of one CP under different generations draw differently there)."""
    if index == 0:
        return committed
    digest = hashlib.sha256(repr((committed[0], tuple(committed[1]), committed[2],
                                  "dataplane-shard", generation, index)).encode()).hexdigest()
    return random.Random(int(digest, 16)).getstate()


def _kill_group(process: Any) -> None:
    """A shard leads its own process group (its preparation pools and remote-prepare
    clients are in it): killing the shard never leaves those behind."""
    if process.pid is not None:
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
    with suppress(OSError, AttributeError, ValueError):
        process.kill()


# ----------------------------------------------------------------------------------
# Learner side
# ----------------------------------------------------------------------------------
class _Slot:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: dict[str, Any] | None = None


class _Shard:
    def __init__(self, index: int, process: Any, connection: Any) -> None:
        self.index, self.process, self.connection = index, process, connection
        self.outbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.lock = threading.Lock()
        self.replies: dict[int, _Slot] = {}
        self.started = threading.Event()
        self.start_failure: dict[str, Any] | None = None
        self.pid: int | None = None
        self.dead: dict[str, Any] | None = None
        self.closing = False
        self.connections = 0
        self.failed = False  # fenced, failed an operation or missed a reply
        self.joining = False  # its pipe ended: the reader alone ends and reaps it
        self.exit: dict[str, Any] | None = None
        # The only thread that ever waits on the process (two concurrent waits on
        # one child lose its exit status); everyone else joins this thread.
        self.reader: threading.Thread | None = None


class ShardSet:
    """The learner's handle on its data-plane shards (learner process only)."""

    def __init__(self, owner: SegmentLearner, shards: list[_Shard]) -> None:
        self.owner, self.shards, self.count = owner, shards, len(shards)
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.ready: deque[tuple[int, int, Any]] = deque()
        self.deferred: queue.Queue[tuple[_Shard, dict[str, Any]] | None] = queue.Queue()
        self.numbers: dict[int, tuple[int, int]] = {}
        self.attached = 0
        # attach token -> (the event its returned thread waits on, shard index)
        self.receivers: dict[int, tuple[threading.Event, int]] = {}
        self.unowned_exclusions: dict[str, int] = {}
        # Which shard's connections carry each game instance (bounded, FIFO): the
        # learner-wide "one instance, one assembler, no segment after its close"
        # rule across shards. A second shard naming the same instance is a fence.
        self.owners: collections.OrderedDict[str, int] = collections.OrderedDict()
        self.balance_reported: Any = None
        self.final: list[dict[str, Any]] | None = None
        self.threads = [threading.Thread(target=self._dispatch, name="rl-shard-dispatch",
                                         daemon=True)]
        for shard in shards:
            shard.reader = threading.Thread(target=self._read, args=(shard,), daemon=True,
                                            name=f"rl-shard-read-{shard.index}")
            self.threads += [
                shard.reader,
                threading.Thread(target=self._write, args=(shard,), daemon=True,
                                 name=f"rl-shard-write-{shard.index}")]
        for thread in self.threads:
            thread.start()

    # -- start --------------------------------------------------------------------
    @classmethod
    def start(cls, owner: SegmentLearner) -> ShardSet:
        count = owner.shard_count
        dataplane = cast(dict[str, Any], owner.dataplane)
        settings, ahead = shard_settings(owner.settings, owner.prepared_batches, count)
        context = multiprocessing.get_context("spawn")
        shards = []
        try:
            for index in range(count):
                ours, theirs = context.Pipe()
                init = {
                    "index": index, "count": count, "parent": os.getpid(),
                    "config": replace(owner.config, segments=settings),
                    "generation": owner.generation,
                    "compiler_identity": owner.compiler_identity,
                    "vocabulary": owner.model.vocabulary,
                    "dataplane": shard_dataplane(dataplane, count, owner.settings),
                    "prepared_batches": ahead,
                    "preparation_options": owner.preparation_options,
                    "aux_labels": owner.fast_aux_labels,
                    "handoff": owner.sgd_options is not None,
                    "scratch": str(owner.live_work / f"shard-{index}"),
                    # Its stdout/stderr, fence facts with tracebacks and any fatal
                    # Python error, next to the learner's own files.
                    "log": str(owner.work / f"dataplane-shard-{index}.log"),
                    # A restore: the state this shard committed in the CP. A fresh
                    # start: shard 0 continues the learner's own selection RNG (with
                    # one shard: the same draws as without shards); the others get
                    # a stream of their own from the configured seed and the generation.
                    "rng": (owner.shard_rng_states[index]
                            if owner.shard_rng_states is not None
                            else owner.rng.getstate() if index == 0 else random.Random(
                                f"{owner.config.seed}/{owner.generation}/dataplane-shard/"
                                f"{index}").getstate()),
                }
                process = context.Process(target=_shard_main, args=(theirs, init),
                                          name=f"rl-dataplane-shard-{index}", daemon=False)
                process.start()
                theirs.close()
                shards.append(_Shard(index, process, ours))
        except BaseException:
            for shard in shards:
                _kill_group(shard.process)
            raise
        result = cls(owner, shards)
        try:
            deadline = time.monotonic() + START_SECONDS
            for shard in shards:
                if not shard.started.wait(max(0.0, deadline - time.monotonic())):
                    raise DataplaneShardFailed("dataplane_shard_start_timeout",
                                               {"shard": shard.index})
                if shard.start_failure is not None or shard.dead is not None:
                    raise DataplaneShardFailed("dataplane_shard_start_failed", {
                        "shard": shard.index, **(shard.start_failure or shard.dead or {})})
        except BaseException:
            result.kill()
            raise
        return result

    # -- pipes --------------------------------------------------------------------
    def _write(self, shard: _Shard) -> None:
        while True:
            message = shard.outbox.get()
            if message is None:
                return
            try:
                # A Connection travels as a duplicated descriptor (attach only);
                # everything else is plain pickle, so no tensor is ever shared.
                data = (bytes(ForkingPickler.dumps(message)) if message["kind"] == "attach"
                        else pickle.dumps(message, protocol=5))
                shard.connection.send_bytes(data)
            except (OSError, ValueError):
                return  # the reader reports the lost shard

    def _read(self, shard: _Shard) -> None:
        unreadable: dict[str, Any] = {}
        while True:
            try:
                data = shard.connection.recv_bytes()
            except (EOFError, OSError):
                break
            try:
                message = self._decode(data)
                kind = message["kind"]
                if kind not in SHARD_MESSAGES:
                    raise ValueError(f"unknown_shard_message_kind: {kind!r}")
            except BaseException as error:
                # Nothing after an unreadable or unknown message can be trusted:
                # the shard is treated as lost (fence now), never skipped past.
                unreadable = {"operation": "dataplane_shard_message_unreadable",
                              "error_type": type(error).__name__, "error": str(error)[:400]}
                break
            if kind == "reply":
                with shard.lock:
                    slot = shard.replies.pop(message["id"], None)
                if slot is not None:
                    slot.value = message
                    slot.event.set()
            elif kind == "started":
                shard.pid = message["pid"]
                shard.start_failure = message.get("failure")
                shard.started.set()
            else:
                self.deferred.put((shard, message))
        with shard.lock:
            orderly = shard.closing and not unreadable
            shard.joining = True
        if not orderly:
            # Before the join reaps it (its pid, the group id, is still its own):
            # whatever the lost shard's group still runs goes with it.
            _kill_group(shard.process)
        shard.process.join(30)
        lingering = shard.process.is_alive()
        if lingering:  # its pipe is closed but it does not exit
            _kill_group(shard.process)
            shard.process.join(30)
        facts = {"pid": shard.pid, "exitcode": shard.process.exitcode,
                 **({"killed_after_pipe_closed": True} if lingering else {}), **unreadable}
        with shard.lock:
            shard.dead = facts
            waiting, shard.replies = list(shard.replies.values()), {}
        shard.started.set()
        for slot in waiting:
            slot.event.set()  # value None: the caller raises `dataplane_shard_died`
        with self.lock:
            # Its hand-off threads are gone with it: the threads `attach` returned
            # end too (no `receiver_done` will come for them).
            for done, index in self.receivers.values():
                if index == shard.index:
                    done.set()
        if not orderly:
            self.deferred.put((shard, {"kind": "died", "facts": facts}))

    @staticmethod
    def _decode(data: bytes) -> dict[str, Any]:
        return cast(dict[str, Any], pickle.loads(data))

    def _kill(self, shard: _Shard) -> None:
        """Kill a shard and its group, unless its pipe already ended: the reader
        then ends and reaps it itself (killing whatever lingers), and after that
        its pid, the group id, could belong to someone else."""
        with shard.lock:
            if not shard.joining:
                _kill_group(shard.process)

    def _own(self, shard: _Shard, instance: str) -> None:
        previous = self.owners.get(instance)
        if previous is not None and previous != shard.index:
            raise ValueError(f"fast_instance_on_two_shards instance={instance} "
                             f"shards={previous},{shard.index}")
        self.owners[instance] = shard.index
        while len(self.owners) > OWNER_MEMORY:
            self.owners.popitem(last=False)

    def _dispatch(self) -> None:
        """Everything a shard sends unasked. Runs apart from the readers because it
        takes the learner's condition, which a caller waiting for a reply may hold."""
        owner = self.owner
        while True:
            item = self.deferred.get()
            if item is None:
                return
            shard, message = item
            kind = message["kind"]
            try:
                if kind == "ready":
                    with self.lock:
                        self.ready.append((shard.index, message["sequence"], message["batch"]))
                    with owner.condition:
                        owner.condition.notify_all()
                elif kind == "fenced":
                    if owner._shard_fenced(shard.index, message["failure"]):
                        shard.failed = True
                elif kind == "died":
                    owner._shard_fenced(shard.index, {
                        "operation": "dataplane_shard_died", **message["facts"]})
                elif kind == "event":
                    owner._event(message["event"], shard=shard.index, **message["fields"])
                elif kind == "claims":
                    for instance in message["instances"]:
                        self._own(shard, instance)
                elif kind == "finished_instances":
                    for instance, _reason, _assembler in message["instances"]:
                        self._own(shard, instance)
                    for other in self.shards:
                        if other is not shard:
                            self._cast(other, "closed_elsewhere",
                                       instances=message["instances"])
                elif kind == "receiver_done":
                    self.receivers[message["token"]][0].set()
            except BaseException as error:  # never lose the dispatcher silently
                owner._shard_fenced(shard.index, {
                    "operation": "dataplane_shard_message", "message": kind,
                    "error_type": type(error).__name__, "error": str(error)[:400]})

    def _cast(self, shard: _Shard, kind: str, **fields: Any) -> None:
        if shard.dead is None:  # a dead shard already fenced the learner
            shard.outbox.put({"kind": kind, **fields})

    def _send(self, shard: _Shard, kind: str, **fields: Any) -> _Slot:
        slot = _Slot()
        with shard.lock:
            if shard.dead is not None:
                raise DataplaneShardFailed("dataplane_shard_died",
                                           {"shard": shard.index, **shard.dead})
            identity = next(self.ids)
            shard.replies[identity] = slot
        shard.outbox.put({"kind": kind, "id": identity, **fields})
        return slot

    def _wait(self, shard: _Shard, kind: str, slot: _Slot, seconds: float) -> Any:
        if not slot.event.wait(seconds):
            failure = {"operation": "dataplane_shard_reply_timeout", "request": kind,
                       "seconds": seconds}
            shard.failed = True
            self.deferred.put((shard, {"kind": "fenced", "failure": failure}))
            raise DataplaneShardFailed("dataplane_shard_reply_timeout",
                                       {"shard": shard.index, **failure})
        if slot.value is None:
            raise DataplaneShardFailed("dataplane_shard_died",
                                       {"shard": shard.index, **(shard.dead or {})})
        if "failure" in slot.value:
            shard.failed = True
            raise DataplaneShardFailed("dataplane_shard_operation_failed", {
                "shard": shard.index, "request": kind, **slot.value["failure"]})
        return slot.value["value"]

    def _call(self, shard: _Shard, kind: str, *, seconds: float | None = None,
              **fields: Any) -> Any:
        return self._wait(shard, kind, self._send(shard, kind, **fields),
                          REPLY_SECONDS if seconds is None else seconds)

    def _call_all(self, kind: str, *, seconds: float | None = None,
                  **fields: Any) -> list[Any]:
        slots = [(shard, self._send(shard, kind, **fields)) for shard in self.shards]
        return [self._wait(shard, kind, slot, REPLY_SECONDS if seconds is None else seconds)
                for shard, slot in slots]

    # -- input --------------------------------------------------------------------
    def attach(self, connection: Any) -> threading.Thread:
        with self.lock:
            token = self.attached
            self.attached += 1
            shard = self.shards[token % self.count]
            done = threading.Event()
            self.receivers[token] = done, shard.index
        shard.connections += 1
        # The shard reads its own duplicate of the descriptor. The caller's object
        # is left as it is (the caller may still hold and close it); nothing in
        # this process ever reads from it.
        self._call(shard, "attach", connection=connection, token=token)
        thread = threading.Thread(target=done.wait, name=f"rl-fast-handoff-shard-{shard.index}",
                                  daemon=True)
        thread.start()
        return thread

    def control(self, operation: str, request: dict[str, Any]) -> dict[str, Any]:
        """One actor control request, applied by every shard (it names no connection).

        Each shard answers from its own ledger. A request the intake refuses to
        parse raises the same `FastIntakeError` here. Otherwise a refusal wins
        over an acceptance (only the shard that saw the instance close can know
        `instance_closed`), and the owning shard's reply over the others'.
        An exclusion no shard owns yet (no segment arrived anywhere) is counted
        once here; `status` subtracts what non-owning shards counted."""
        replies = self._call_all("control", operation=operation, request=request)
        for reply in replies:
            if "raised" in reply:
                raise FastIntakeError(reply["raised"])
        counted = [reply["counted"] for reply in replies if reply["counted"] is not None]
        if counted and not any(reply["owned"] for reply in replies):
            with self.lock:
                self.unowned_exclusions[counted[0]] = (
                    self.unowned_exclusions.get(counted[0], 0) + 1)
        ordered = sorted(replies, key=lambda reply: not reply["owned"])
        for reply in ordered:
            if reply["reply"].get("admitted") is False or reply["reply"].get("excluded") is False:
                return cast(dict[str, Any], reply["reply"])
        return cast(dict[str, Any], ordered[0]["reply"])

    def publish_mu(self, identity: dict[str, Any]) -> None:
        self._call_all("publish_mu", identity=identity)

    def flags(self, *, stopped: bool, fenced: bool) -> None:
        """Pass a False -> True flag change to every shard.

        A fence is cast: it may come from the dispatcher, which holds the learner's
        condition, and a fenced learner takes nothing more anyway. A stop is
        acknowledged: the learner goes on (and answers its stop request) only
        after every live shard applied it, so a shard that reports a hand-off
        lost reports one it saw before the stop, a real loss, and one that saw
        it after the stop only records it (`_fast_lost`). A shard that cannot
        acknowledge is already on its way to fencing the learner: a death is
        reported by its reader, a timeout by `_wait`, a failed operation by the
        shard itself."""
        if fenced or not stopped:
            for shard in self.shards:
                self._cast(shard, "flags", stopped=stopped, fenced=fenced)
            return
        slots: list[tuple[_Shard, _Slot]] = []
        for shard in self.shards:
            try:
                slots.append((shard, self._send(shard, "flags", stopped=True, fenced=False)))
            except DataplaneShardFailed:
                continue  # dead: its reader reported it
        for shard, slot in slots:
            try:
                self._wait(shard, "flags", slot, REPLY_SECONDS)
            except DataplaneShardFailed:
                continue  # reported as above; the learner is fenced by it

    # -- datasets -----------------------------------------------------------------
    def ahead(self) -> None:
        for shard in self.shards:
            self._cast(shard, "ahead")

    def drain_ready(self) -> list[tuple[int, int, Any]]:
        with self.lock:
            ready = list(self.ready)
            self.ready.clear()
        return ready

    def numbered(self, number: int, shard: int, local: int) -> None:
        self.numbers[number] = shard, local

    def take(self, active: dict[str, Any]) -> bool:
        return bool(self._call(self.shards[active["shard"]], "take",
                               sequence=active["shard_sequence"]))

    def staged(self, active: dict[str, Any]) -> None:
        self._cast(self.shards[active["shard"]], "staged", sequence=active["shard_sequence"])

    def release(self, number: int) -> None:
        shard, local = self.numbers[number]
        self._cast(self.shards[shard], "release", sequence=local)

    def stepped(self, active: dict[str, Any], minibatch: int) -> None:
        self._cast(self.shards[active["shard"]], "stepped",
                   sequence=active["shard_sequence"], minibatch=minibatch)

    def finished(self, active: dict[str, Any]) -> None:
        self.numbers.pop(active["request"].identity.sequence, None)
        self._cast(self.shards[active["shard"]], "finished",
                   sequence=active["shard_sequence"])

    def discard_stopped(self) -> tuple[list[dict[str, Any]], list[DataplaneShardFailed]]:
        """The stop discard on every shard: the records of each shard that answered
        (it has released those rows: they need their disposition even when
        another shard fails) and the failure of each one that did not."""
        records: list[dict[str, Any]] = []
        failures: list[DataplaneShardFailed] = []
        slots: list[tuple[_Shard, _Slot]] = []
        for shard in self.shards:
            try:
                slots.append((shard, self._send(shard, "discard")))
            except DataplaneShardFailed as error:
                failures.append(error)
        for shard, slot in slots:
            try:
                records.extend(self._wait(shard, "discard", slot, REPLY_SECONDS))
            except DataplaneShardFailed as error:
                failures.append(error)
        return records, failures

    def snapshot(self) -> dict[str, Any]:
        return {"count": self.count, "shards": self._call_all("snapshot")}

    # -- status -------------------------------------------------------------------
    def _statuses(self) -> list[dict[str, Any]]:
        """Every shard's status. A shard that cannot answer (dead, or no reply in
        time: the learner is fenced by then) is listed as `unavailable`, so the
        status of a failed run can still be read."""
        if self.final is not None:
            return self.final
        slots: list[tuple[_Shard, _Slot | DataplaneShardFailed]] = []
        for shard in self.shards:
            try:
                slots.append((shard, self._send(shard, "status")))
            except DataplaneShardFailed as error:
                slots.append((shard, error))
        statuses = []
        for shard, slot in slots:
            try:
                if isinstance(slot, DataplaneShardFailed):
                    raise slot
                statuses.append(self._wait(shard, "status", slot, REPLY_SECONDS))
            except DataplaneShardFailed as error:
                statuses.append({"index": shard.index, "unavailable": error.facts})
        return statuses

    def discarded_rows(self) -> int:
        return sum(status.get("training_discarded_rows", 0) for status in self._statuses())

    def status(self) -> dict[str, Any]:
        """The same `fast_chain` report, summed over shards, plus each shard's own.

        Sums are exact for everything a shard's own connections drive. The
        broadcast control counters take the maximum; exclusion counts subtract
        what a non-owning shard counted and add what no shard owned."""
        every = self._statuses()
        statuses = [status for status in every if "unavailable" not in status]
        with self.lock:
            unowned = dict(self.unowned_exclusions)
        learner: dict[str, int] = {}
        intake: dict[str, Any] = {}
        reasons: dict[str, int] = dict(unowned)
        sources: dict[str, int] = {}
        for status in statuses:
            for key, value in status["learner"].items():
                learner[key] = learner.get(key, 0) + value
            for key, value in status["intake"].items():
                if key == "excluded_by_reason":
                    for reason, number in value.items():
                        reasons[reason] = (reasons.get(reason, 0) + number
                                           - status["foreign_by_reason"].get(reason, 0))
                elif key == "open_instances":
                    intake[key] = intake.get(key, 0) + status["owned_open_instances"]
                elif key in BROADCAST_INTAKE_COUNTERS:
                    intake[key] = max(intake.get(key, 0), value)
                elif type(value) in (int, float):
                    intake[key] = intake.get(key, 0) + value
            for key, value in status["sources"].items():
                sources[key] = sources.get(key, 0) + value
        if "excluded_instances" in learner:
            learner["excluded_instances"] += sum(unowned.values()) - sum(
                status["foreign_exclusions"] for status in statuses)
        intake["excluded_by_reason"] = {k: v for k, v in sorted(reasons.items()) if v}
        return {"fast_chain": {
            "learner": learner, "intake": intake, **sources,
            "shards": {"count": self.count, "balance": self._balance(every),
                       "unowned_exclusions": unowned, "per_shard": every}}}

    def _balance(self, statuses: list[dict[str, Any]]) -> dict[str, Any]:
        """Whether every shard gets a fair share of the input. A dataset is drawn
        from one shard only, so the launcher must spread env nodes evenly over
        the services, and the services over the shards. Checked once enough rows
        arrived: a shard under half the mean in admitted rows or distinct actors,
        or one without a connection, is reported (status and one event)."""
        if any("unavailable" in status for status in statuses):
            return {"ok": False, "checked": False, "problems": ["shard_unavailable"]}
        rows = [status["learner"]["admitted_rows"] for status in statuses]
        actors = [status["actors"] for status in statuses]
        connections = [status["connections"] for status in statuses]
        warm = sum(rows) >= self.count * 16 * self.owner.settings.dataset_rows
        problems = []
        if self.attached and min(connections) == 0:
            problems.append("shard_without_connection")
        if warm and min(rows) * self.count * 2 < sum(rows):
            problems.append("admitted_rows_below_half_of_mean")
        if warm and min(actors) * self.count * 2 < sum(actors):
            problems.append("actors_below_half_of_mean")
        return {"ok": not problems, "checked": bool(warm), "problems": problems,
                "admitted_rows": rows, "actors": actors, "connections": connections}

    def health(self) -> None:
        """Heartbeat: one loud event when the balance verdict changes."""
        balance = self._balance(self._statuses())
        verdict = tuple(balance["problems"])
        if verdict != self.balance_reported and (verdict or self.balance_reported is not None):
            self.owner._event("dataplane_shard_imbalance" if verdict
                              else "dataplane_shard_balance_restored", **balance)
        self.balance_reported = verdict

    # -- end ----------------------------------------------------------------------
    def close(self) -> None:
        """Orderly end of every shard; the last status is kept for later reads. A
        shard that fails to close or does not exit is killed and raised here."""
        failures: list[BaseException] = []
        final: list[dict[str, Any]] = []
        slots: list[tuple[_Shard, _Slot | None]] = []
        for shard in self.shards:
            with shard.lock:
                shard.closing = True
            try:
                slots.append((shard, self._send(shard, "close")))
            except DataplaneShardFailed as error:
                slots.append((shard, None))
                failures.append(error)
        for shard, slot in slots:
            # A shard already known as failed (the learner is fenced) is not waited
            # for at length: the stop reserve belongs to the rest of the close.
            failed = shard.failed
            try:
                if slot is not None:
                    final.append(self._wait(shard, "close", slot,
                                            FAILED_CLOSE_SECONDS if failed else CLOSE_SECONDS))
            except DataplaneShardFailed as error:
                failures.append(error)
                failed = True
            shard.outbox.put(None)
            assert shard.reader is not None
            shard.reader.join(0 if slot is None else 5 if failed else 60)
            if shard.reader.is_alive():
                self._kill(shard)  # the reader then sees the end and reaps it
                shard.reader.join(90)
                failures.append(DataplaneShardFailed(
                    "dataplane_shard_killed_at_close", {"shard": shard.index}))
            shard.exit = dict(shard.dead or {"exitcode": None})
            if not failures and shard.exit.get("exitcode") != 0:
                failures.append(DataplaneShardFailed(
                    "dataplane_shard_exit_not_clean", {"shard": shard.index, **shard.exit}))
        if len(final) == self.count:
            self.final = final
        self.deferred.put(None)
        if failures:
            raise failures[0]

    def kill(self) -> None:
        for shard in self.shards:
            with shard.lock:
                shard.closing = True
            shard.outbox.put(None)
            self._kill(shard)
        for shard in self.shards:
            if shard.reader is not None:
                shard.reader.join(90)
        self.deferred.put(None)


# ----------------------------------------------------------------------------------
# Shard side
# ----------------------------------------------------------------------------------
class _Absent:
    """Stands where a shard has no such owner (control journal, actor upload spool):
    present, so the binary-chain branches run, and loud on any use."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attribute: str) -> Any:
        raise BoundaryError("rl_dataplane_shard", f"shard_has_no_{self._name}")


class _ShardIntake(FastSegmentIntake):
    """The same ledger, plus what keeps its identity rules learner-wide when there
    are several shards: it reports each instance its connections bind (`claims`)
    and close (`finished_instances`), and records instances closed on another
    shard as finished here, so a late or repeated segment of them is the same
    `fast_segment_after_instance_closed` fence as on one ledger."""

    notify: Any = None  # the shard's send, set only with more than one shard

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.claimed: set[str] = set()
        self.unclaimed: list[str] = []  # bound here, not yet reported (under `lock`)
        self.elsewhere: collections.OrderedDict[str, None] = collections.OrderedDict()

    def _bound(self, instance_id: str) -> None:
        # Under the intake lock, once per instance: O(1) per segment, never a scan
        # of the open instances (every shard holds every attested open game).
        if self.notify is not None and instance_id not in self.claimed:
            self.claimed.add(instance_id)
            self.unclaimed.append(instance_id)

    def offer(self, message: dict[str, Any], data: bytes, checked: Any = None) -> Any:
        try:
            return super().offer(message, data, checked)
        finally:
            # The offering thread reports what its own offer bound. Claims stay
            # asynchronous: a duplicate on a second shard is fenced when the
            # learner sees both claims, possibly after that shard admitted it
            # (it can sit in a prepared batch there). The fence stops training
            # before the next CP, so nothing of it is ever committed.
            if self.unclaimed:
                with self.lock:
                    new, self.unclaimed = self.unclaimed, []
                if new:
                    self.notify({"kind": "claims", "instances": new})

    def _finish(self, instance_id: str, instance: Any) -> None:
        super()._finish(instance_id, instance)
        if instance.closed is not None:
            self.claimed.discard(instance_id)
            if self.notify is not None:
                self.notify({"kind": "finished_instances", "instances": [
                    (instance_id, instance.excluded, instance.closed["assembler_id"])]})

    def owns(self, instance_id: Any) -> bool:
        """Whether a hand-off on this shard's connections ever named the instance."""
        with self.lock:
            if instance_id in self.elsewhere:
                return False
            if instance_id in self.finished:
                return True
            instance = self.instances.get(instance_id)
            return instance is not None and (
                instance.assembler_id is not None or instance.received > 0
                or bool(instance.buffered) or instance.closed is not None)

    def closed_elsewhere(self, records: list[tuple[str, str | None, str]]) -> None:
        with self.lock:
            for instance_id, reason, assembler in records:
                if self.owns(instance_id):
                    raise FastIntakeError(
                        f"fast_instance_on_two_shards instance={instance_id} "
                        f"assembler_elsewhere={assembler}")
                self.instances.pop(instance_id, None)  # at most an inert control record
                self.finished[instance_id] = reason
                self.finished.move_to_end(instance_id)
                self.finished_by[instance_id] = assembler
                self.elsewhere[instance_id] = None
                while len(self.finished) > self.closed_memory:
                    self.finished_by.pop(self.finished.popitem(last=False)[0], None)
                while len(self.elsewhere) > self.closed_memory:
                    self.elsewhere.popitem(last=False)


class _ShardLearner(SegmentLearner):
    """The learner's data-plane methods on one shard's own state."""

    def __init__(self, init: dict[str, Any], send: Any) -> None:
        # Deliberately not `SegmentLearner.__init__`: no model, SGD, exchange,
        # journal, publication or checkpoint exists in a shard.
        from spireagent.local_diagnostics import TimedRLock

        from .binary_preparation import BinaryBatchPreparer

        self.shards = None
        self.send = send
        self.applying_flags = False
        self.fence_report = threading.Event()
        self.shard_index, self.shard_count = init["index"], init["count"]
        config = init["config"]
        settings = config.segments
        self.config, self.settings = config, settings
        self.generation = init["generation"]
        self.compiler_identity = init["compiler_identity"]
        self.dataplane = dataplane = init["dataplane"]
        switch = dataplane.get("gil_switch_interval_seconds")
        if switch is not None:  # the learner validated it; the same opt-in per process
            sys.setswitchinterval(switch)
        self.work = self.live_work = Path(init["scratch"])
        self.work.mkdir(parents=True, exist_ok=True)
        self.wal = cast(Any, _Absent("control_journal"))
        self.binary_sources = cast(Any, _Absent("upload_spool"))
        self.sgd = None
        self.rng = random.Random()
        self.rng.setstate(init["rng"])
        self.committed_selection_rng = self.rng.getstate()
        self.queue = SegmentQueue(settings)
        self.input_gate = threading.RLock()
        self.control_lock = (TimedRLock() if dataplane.get("lock_timing", False)
                             else threading.RLock())
        self.condition = self.queue.condition = threading.Condition(
            cast(Any, self.control_lock))
        self.terminal = AuxiliaryPool(settings.terminal_games, settings.terminal_uses)
        self.hp = AuxiliaryPool(settings.hp_battles, settings.hp_uses)
        self.capacity_waiters = {}
        self.capacity_tickets = 0
        self.episode_load = {}
        self.lease_owners = {}
        self.actor_episodes = {}
        self.retired_actors = set()
        self.sources = {}
        self.hp_binary_members = {}
        self.hot_references = {}
        self.newlease_stopped = self.input_fenced = False
        self.input_failure = None
        self.training_discarded_rows = 0
        self.updates = self.datasets = 0
        self.active = None
        self.active_batch = None
        self.next_active = None
        self.pending_batch = None
        self.ahead_batches = deque()
        self.prepared_batches = init["prepared_batches"]
        self.ahead_while_waiting = False
        self.selection_deadline = None
        self.prefetch_referenced_only = dataplane.get("prefetch_referenced_only", False)
        if type(self.prefetch_referenced_only) is not bool:
            raise ValueError("prefetch_referenced_only_must_be_explicit")
        self.timing = {}
        self.fast_sources = self.shard_sources = FastSources()
        intake = _ShardIntake(
            run=config.run, generation=self.generation,
            excluded_seeds=frozenset(settings.excluded_seeds),
            buffer_segments=dataplane.get("fast_buffer_segments", 512),
            gae_lambda=settings.gae_lambda, aux_labels=frozenset(init["aux_labels"]))
        if self.shard_count > 1:
            intake.notify = send
        self.fast_intake = self.shard_intake = intake
        self.fast_verifier = None
        self.fast_admitted_rows = collections.OrderedDict()
        self.fast_counters = {key: 0 for key in (
            "admitted", "admitted_rows", "capacity_dropped", "capacity_dropped_rows",
            "capacity_evicted", "capacity_evicted_rows",
            "capacity_refused", "capacity_refused_rows",
            "excluded_instances", "excluded_dropped_rows", "excluded_used_rows",
            "excluded_trained_rows", "excluded_prefetched_rows",
            "excluded_admit_raced", "excluded_admit_raced_rows",
            "excluded_aux_rows")}
        self.fast_receivers = []
        self.source_prefetch_changed = threading.Event()
        self.source_prefetch_stopping = threading.Event()
        self.source_prefetch_gate = threading.Lock()
        self.dispatch_workers = dataplane.get("prepare_dispatch_workers", 4)
        if type(self.dispatch_workers) is not int or self.dispatch_workers < 1:
            raise ValueError("invalid_prepare_dispatch_workers")
        self.preparation_dispatch = ThreadPoolExecutor(
            max_workers=self.dispatch_workers, thread_name_prefix="rl-prepare-dispatch")
        self.dispatch_order = threading.Condition()
        self.dispatch_issued = self.dispatch_turn = 0
        self.handoff_releaser = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rl-handoff-release")
        self.handoff_releases = deque()
        self.handoff_release_lock = threading.Lock()
        self.pending_labels = deque()
        self.pending_label_requests = {}
        # Shard-only state.
        self.handoff = init["handoff"]
        self.announce_lock = threading.Lock()
        self.announced = 0
        self.announced_batches: dict[int, Any] = {}
        self.unreleased: dict[int, Any] = {}  # SGD hand-off files this shard still holds
        self.taken: dict[int, tuple[dict[str, Any], Any]] = {}
        self.connections = 0
        self.shard_actors: set[str] = set()
        self.foreign_exclusions = 0
        self.foreign_by_reason: dict[str, int] = {}
        self.input_preparer = BinaryBatchPreparer(
            provider=RoutedSources(self.binary_sources, self.shard_sources),
            vocabulary=init["vocabulary"], scratch=self.work, handoff=self.handoff,
            **binary_preparer_arguments(dataplane, settings, self.prepared_batches),
            **init["preparation_options"])
        self.source_prefetch_thread = self.shard_prefetch = threading.Thread(
            target=self._source_prefetch_loop, name="rl-source-prefetch", daemon=True)
        self.shard_prefetch.start()
        threading.Thread(target=self._report_fence, name="rl-shard-fence", daemon=True).start()

    # -- what the learner process owns --------------------------------------------
    def _flags_changed(self) -> None:
        if not self.applying_flags and getattr(self, "_input_fenced", False):
            self.fence_report.set()

    def _report_fence(self) -> None:
        self.fence_report.wait()
        with self.condition:  # the fencing block has set its failure by now
            failure = dict(self.input_failure or {"operation": "unknown"})
        _log(f"input fenced: {failure}")
        self.send({"kind": "fenced", "failure": failure})

    def _event(self, kind: str, *, _build: Any = None, **fields: Any) -> None:
        self.send({"kind": "event", "event": kind, "fields": fields})

    def _transport_failure(self, failure: dict[str, Any]) -> None:
        # The fence that precedes this call is already reported to the learner.
        self.send({"kind": "event", "event": "dataplane_shard_transport_failure",
                   "fields": {"failure": failure}})

    def _notify_capacity(self, *, force: bool = False) -> None:
        return  # old-chain lease waiters exist only in the learner process

    def _fast_admit(self, segment: Any, envelope: dict[str, Any], changed: list[str], *,
                    prepared: Any = None, referenced: list[set[str]] | None = None) -> None:
        super()._fast_admit(segment, envelope, changed, prepared=prepared,
                            referenced=referenced)
        if "admitted" in changed:
            self.shard_actors.add(envelope["actor"])

    # -- messages -----------------------------------------------------------------
    def serve(self, receive: Any) -> None:
        while True:
            message = receive()
            kind = message["kind"]
            try:
                value = getattr(self, "_on_" + kind)(message)
            except BaseException as error:
                _log(f"operation {kind} failed", error=error)
                failure = {"operation": "dataplane_shard_" + kind,
                           "error_type": type(error).__name__, "error": str(error)[:400]}
                if "id" in message:
                    self.send({"kind": "reply", "id": message["id"], "failure": failure})
                with self.condition:
                    if not self.input_fenced:
                        self.input_fenced = self.newlease_stopped = True
                        self.input_failure = failure
                    self.condition.notify_all()
                if kind == "close":
                    return
                continue
            if "id" in message:
                self.send({"kind": "reply", "id": message["id"], "value": value})
            if kind == "close":
                return

    def _on_attach(self, message: dict[str, Any]) -> None:
        connection, token = message["connection"], message["token"]

        def receive() -> None:
            try:
                self._fast_receive(connection)
            finally:
                self.send({"kind": "receiver_done", "token": token})

        thread = threading.Thread(target=receive, name="rl-fast-handoff", daemon=True)
        self.fast_receivers.append(thread)
        self.connections += 1
        thread.start()

    def _on_flags(self, message: dict[str, Any]) -> None:
        with self.condition:
            self.applying_flags = True
            try:
                if message["fenced"]:
                    self.input_fenced = True
                if message["stopped"]:
                    self.newlease_stopped = True
            finally:
                self.applying_flags = False
            self.condition.notify_all()
        self.source_prefetch_changed.set()

    def _on_control(self, message: dict[str, Any]) -> dict[str, Any]:
        operation, request = message["operation"], message["request"]
        intake = self.shard_intake
        intake.set_generation(self.generation)
        owned = intake.owns(request.get("game_instance_id"))
        try:
            decision = (intake.attest(request) if operation == FAST_INSTANCE_ADMISSION
                        else intake.exclude(request))
        except FastIntakeError as error:
            return {"raised": str(error)}
        try:
            self._fast_apply(decision)
        except BaseException as error:
            self._fast_fence("fast_control", error)
            raise
        counted = None
        if operation != FAST_INSTANCE_ADMISSION and decision.exclude_instance is not None:
            counted = "actor:" + request["reason"]
            if not owned:
                self.foreign_exclusions += 1
                self.foreign_by_reason[counted] = self.foreign_by_reason.get(counted, 0) + 1
        return {"reply": decision.reply, "owned": owned, "counted": counted}

    def _on_closed_elsewhere(self, message: dict[str, Any]) -> None:
        self.shard_intake.closed_elsewhere(message["instances"])

    def _on_publish_mu(self, message: dict[str, Any]) -> None:
        self.shard_intake.publish_mu(message["identity"])

    def _on_ahead(self, message: dict[str, Any]) -> None:
        """The learner loop's own order: releases, labels, then read-ahead."""
        self._check_releases()
        self._collect_labels()
        self._prepare_ahead()
        for active, future in self._pending_batches():
            if not active.get("announcing"):
                active["announcing"] = True
                future.add_done_callback(lambda _future: self._announce())

    def _announce(self) -> None:
        """Tell the learner about each prepared dataset, in selection order."""
        try:
            with self.announce_lock:
                with self.condition:
                    pending = self._pending_batches()
                for active, future in pending:
                    sequence = active["request"].identity.sequence
                    if sequence <= self.announced:
                        continue
                    if not future.done():
                        return
                    batch = future.result()  # a failed preparation raises: fence below
                    self.announced_batches[sequence] = batch
                    if self.handoff:
                        self.unreleased[sequence] = batch
                    self.announced = sequence
                    self.send({"kind": "ready", "sequence": sequence, "batch": batch})
        except BaseException as error:
            _log("a preparation failed", error=error)
            with self.condition:
                self.input_fenced = self.newlease_stopped = True
                self.input_failure = {"operation": "prepare", "code": "INVALID_PACKET",
                                      "producer_error_type": type(error).__name__,
                                      "error": str(error)[:400]}
                self.condition.notify_all()

    def _on_take(self, message: dict[str, Any]) -> bool:
        sequence = message["sequence"]
        with self.condition:
            if self.newlease_stopped:
                return False
            head = self.pending_batch
            if (head is None or head[0]["request"].identity.sequence != sequence
                    or sequence not in self.announced_batches):
                raise ValueError("dataplane_shard_take_out_of_order")
            active, batch = head[0], self.announced_batches[sequence]
            if not active["taken"]:
                if self.queue.dataset() != active["segments"]:
                    raise ValueError("prepared_dataset_membership_changed")
                self._dataset_released(active["segments"])
                active["taken"] = True
            self.pending_batch = self.ahead_batches.popleft() if self.ahead_batches else None
            if self.active is None:
                self.active, self.active_batch = active, batch
            elif self.next_active is None:
                self.next_active = active, batch
            else:
                raise ValueError("dataplane_shard_third_dataset_taken")
            self.taken[sequence] = active, batch
        return True

    def _on_staged(self, message: dict[str, Any]) -> None:
        _active, batch = self.taken[message["sequence"]]
        self._ready_labels(batch)
        self._release_taken(batch.request)

    def _on_release(self, message: dict[str, Any]) -> None:
        handoff = self.unreleased.pop(message["sequence"])
        release = cast(Any, self.input_preparer).release_handoff
        with self.handoff_release_lock:
            self.handoff_releases.append(self.handoff_releaser.submit(release, handoff))

    def _on_stepped(self, message: dict[str, Any]) -> None:
        active, batch = self.taken[message["sequence"]]
        self._charge_auxiliary(active, batch.request, message["minibatch"])

    def _on_finished(self, message: dict[str, Any]) -> None:
        with self.condition:
            active, batch = self.taken.pop(message["sequence"])
            if self.active is not active:
                raise ValueError("dataplane_shard_finished_out_of_order")
            self.announced_batches.pop(message["sequence"], None)
            self.committed_selection_rng = batch.request.selection_rng_state
            self.datasets += 1
            self.active = self.active_batch = None
            self._promote()
            self._forget_unreferenced()

    def _on_discard(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """`_discard_stopped_queue` without the journal (the learner writes the one
        durable disposition from what this returns)."""
        with self.condition:
            if not self.newlease_stopped:
                raise ValueError("dataplane_shard_discard_before_stop")
            records = list(self.queue.pending)
            for record in records:
                payload = self._member(record).payload
                owner = "ppo:" + record["segment_id"]
                if (payload.slot, owner) in self.hot_references:
                    self._release(payload, owner)
                for member in self.hp_binary_members.pop(record["segment_id"], ()):
                    self._release(member.payload, "labels:" + record["segment_id"])
            # As `_discard_stopped_queue`: counted, and the queue is empty after
            # (a second call reports nothing).
            self.training_discarded_rows += sum(record["rows"] for record in records)
            self.queue.pending.clear()
            self.queue.rows = 0
            return [{"segment_id": r["segment_id"], "rows": r["rows"]} for r in records]

    def _on_snapshot(self, message: dict[str, Any]) -> dict[str, Any]:
        with self.condition:
            return {"index": self.shard_index,
                    "committed_selection_rng": self.committed_selection_rng,
                    "speculative_selection_rng": self.rng.getstate(),
                    "pending_requests": [_pending_receipt(active["request"])
                                         for active, _ in self._pending_batches()],
                    "pending_labels": copy.deepcopy(self.pending_label_requests)}

    def _on_status(self, message: dict[str, Any]) -> dict[str, Any]:
        intake = self.shard_intake
        with self.condition:
            counters = dict(self.fast_counters)
            rows, discarded = self.queue.rows, self.training_discarded_rows
            pending = len(self._pending_batches())
            actors, taken = len(self.shard_actors), len(self.taken)
            fenced, failure = self.input_fenced, self.input_failure
            stopped = self.newlease_stopped
        with intake.lock:
            owned = sum(1 for instance_id in intake.instances if intake.owns(instance_id))
        timing = (self.control_lock.snapshot() if hasattr(self.control_lock, "snapshot")
                  else [])
        return {"index": self.shard_index, "pid": os.getpid(),
                "connections": self.connections,
                "receivers_alive": sum(thread.is_alive() for thread in self.fast_receivers),
                "learner": counters, "intake": intake.report(),
                "owned_open_instances": owned,
                "foreign_exclusions": self.foreign_exclusions,
                "foreign_by_reason": dict(self.foreign_by_reason),
                "sources": self.shard_sources.status(),
                "queue_rows": rows, "pending_batches": pending, "announced": self.announced,
                "taken": taken, "datasets": self.datasets, "actors": actors,
                "training_discarded_rows": discarded,
                "input_fenced": fenced, "input_failure": failure, "newlease_stopped": stopped,
                "preparation": cast(Any, self.input_preparer).summary(),
                "control_lock_timing": timing, "cpu_seconds": time.process_time()}

    def _on_close(self, message: dict[str, Any]) -> dict[str, Any]:
        self._on_flags({"stopped": True, "fenced": False})
        self.source_prefetch_stopping.set()
        self.source_prefetch_changed.set()
        self.shard_prefetch.join()
        try:
            self.preparation_dispatch.shutdown(wait=True, cancel_futures=False)
        finally:
            try:
                self.handoff_releaser.shutdown(wait=True)
            finally:
                self.input_preparer.shutdown(wait=True, cancel_futures=False)
        for future, _, _ in self.pending_labels:
            future.result()
        with self.condition:
            for (_slot, owner), payload in list(self.hot_references.items()):
                self._release(payload, owner)
        self._check_releases(wait=True)
        return self._on_status(message)


def _log(text: str, *, error: BaseException | None = None) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {text}", file=sys.stderr, flush=True)
    if error is not None:
        traceback.print_exception(error, file=sys.stderr)
        sys.stderr.flush()


def _shard_main(connection: Any, init: dict[str, Any]) -> None:
    os.setpgrp()  # own group: see `_kill_group`
    # Everything this process (and its pools and clients) prints goes to its own
    # log file; a crash of the interpreter leaves its stacks there too.
    with open(init["log"], "ab", buffering=0) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
    faulthandler.enable()
    _log(f"dataplane shard {init['index']} of {init['count']} started pid={os.getpid()}")
    # Stop signals belong to the learner's own stop path, like the SGD process.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _parent_death_signal(init["parent"])
    outbox: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def write() -> None:
        while True:
            message = outbox.get()
            if message is None:
                return
            try:
                data = pickle.dumps(message, protocol=5)
            except BaseException as error:
                # A message this shard cannot send is a lost message: say so and
                # end the shard (the learner fences), never carry on without it.
                data = pickle.dumps({"kind": "fenced", "failure": {
                    "operation": "dataplane_shard_message_unsendable",
                    "message": message.get("kind"), "error_type": type(error).__name__,
                    "error": str(error)[:400]}}, protocol=5)
                with suppress(OSError, ValueError):
                    connection.send_bytes(data)
                os._exit(72)
            try:
                connection.send_bytes(data)
            except (OSError, ValueError):
                os._exit(71)  # the learner is gone: nothing here may outlive it

    writer = threading.Thread(target=write, name="rl-shard-write", daemon=False)
    writer.start()

    def receive() -> dict[str, Any]:
        try:
            return cast(dict[str, Any], pickle.loads(connection.recv_bytes()))
        except (EOFError, OSError):
            os._exit(71)

    try:
        shard = _ShardLearner(init, outbox.put)
    except BaseException as error:
        _log("start failed", error=error)
        outbox.put({"kind": "started", "pid": os.getpid(), "failure": {
            "error_type": type(error).__name__, "error": str(error)[:400]}})
        outbox.put(None)
        writer.join()
        raise
    outbox.put({"kind": "started", "pid": os.getpid()})
    try:
        shard.serve(receive)
    finally:
        outbox.put(None)
        writer.join()
