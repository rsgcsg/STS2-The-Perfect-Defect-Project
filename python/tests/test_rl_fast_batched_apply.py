"""`dataplane.fast_batched_apply`: hand-off readers decide, one applier applies.

Same results as the per-reader apply (queue, counters, eviction, exclusion races,
aux rows), per-connection order and ack-after-apply kept (acks on each
connection's own writer, never on the shared applier), a bounded applier queue,
loud fences on apply errors (fence first, the first failure kept), and a normal
close that is not a fence and applies queued work before the owners shut down.
"""
from __future__ import annotations

import collections
import multiprocessing
import random
import threading
import time
from typing import Any, cast

import pytest
from test_rl_fast_learner import (
    _CountingCondition,
    _stub_owner,
    _stub_queue,
    _stub_segment,
    admission,
    fast_learner,
    game,
    learner_mu,
    queued,
)
from test_rl_fast_source import _sender, closed_for, combat_game

from spireagent.json_boundary import BoundaryError
from stpd.p1.frame_compiler import FrameCompiler
from stpd.rl.fast_source import IntakeDecision
from stpd.rl.segment_learner import (
    FastApplyClosed,
    SegmentLearner,
    _FastHandled,
    _FastStream,
    _FastTicket,
)


class _Connection:
    """Records acks and whether the learner condition was held while sending."""
    # Sent from the stream's own ack writer thread; `_open` + `_flush` below.

    def __init__(self, owner: SegmentLearner, fail: bool = False) -> None:
        self.owner, self.fail = owner, fail
        self.sent: list[tuple[bool, list[str], Any]] = []

    def send(self, message: Any) -> None:
        if self.fail:
            raise BrokenPipeError("sender gone")
        self.sent.append((cast(Any, self.owner.condition)._is_owned(),
                          queued(self.owner), message["ack"]))


def _batched(owner: SegmentLearner, capacity: int = 64) -> list[Any]:
    events: list[Any] = []
    stub = cast(Any, owner)
    stub._event = lambda name, **fields: events.append((name, fields))
    stub._notify_capacity = lambda force=False: None
    stub.fast_apply_stats = collections.defaultdict(int)
    owner.input_failure = None
    owner.fast_batched_apply, owner.fast_apply_capacity = True, capacity
    owner.fast_apply_items = collections.deque()
    owner.fast_apply_lock = threading.Condition()
    owner.fast_apply_stopping = False
    owner.fast_apply_failure = None
    owner.fast_apply_thread = None
    return events


def _open(owner: SegmentLearner, connection: Any) -> _FastStream:
    return owner._fast_open_stream(connection)


def _flush(owner: SegmentLearner, *streams: _FastStream) -> None:
    """Wait for every queued ack of these connections (their writers end)."""
    for stream in streams:
        owner._fast_ack_close(stream)
        assert stream.ack_thread is not None and not stream.ack_thread.is_alive()


def _start(owner: SegmentLearner) -> None:
    owner.fast_apply_thread = threading.Thread(target=owner._fast_applier, daemon=True)
    owner.fast_apply_thread.start()


def _ticket_message(sequence: int) -> dict[str, Any]:
    return {"handoff_sequence": sequence}


def test_batched_apply_is_an_explicit_bool(tmp_path, monkeypatch):
    with pytest.raises(BoundaryError, match="fast_batched_apply_must_be_explicit_bool"):
        fast_learner(tmp_path, monkeypatch, dataplane_extra={"fast_batched_apply": "yes"})
    with pytest.raises(BoundaryError, match="fast_apply_queue_must_be_int_1_to_4096"):
        fast_learner(tmp_path / "q", monkeypatch,
                     dataplane_extra={"fast_batched_apply": True, "fast_apply_queue": 0})


def test_one_hold_applies_every_ready_decision_in_order_and_acks_after_it():
    owner = _stub_owner(10**6, set())
    _batched(owner)
    stream = _open(owner, _Connection(owner))
    other = _open(owner, _Connection(owner))
    for i in range(5):
        owner._fast_submit(stream, IntakeDecision(admit=[_stub_segment(f"a{i}", "r1", 8)]),
                           _ticket_message(i + 1))
        owner._fast_submit(other, IntakeDecision(admit=[_stub_segment(f"b{i}", "r2", 8)]),
                           _ticket_message(i + 1))
    owner._fast_submit(stream, IntakeDecision(), None)  # a closed message: never acked
    _start(owner)
    owner._fast_drain(stream)
    owner._fast_drain(other)
    _flush(owner, stream, other)
    order = queued(owner)
    assert [s for s in order if s.startswith("a")] == [f"a{i}" for i in range(5)]
    assert [s for s in order if s.startswith("b")] == [f"b{i}" for i in range(5)]
    # Ten admissions, one hold: one wake-up, one prefetch hint.
    assert cast(_CountingCondition, owner.condition).notified == 1
    assert owner.source_prefetch_changed.is_set()
    assert owner.fast_apply_stats["batches"] == 1 and owner.fast_apply_stats["items"] == 11
    for connection, prefix in ((stream.connection, "a"), (other.connection, "b")):
        sent = connection.sent
        assert [a for _, _, a in sent] == [1, 2, 3, 4, 5]
        # Sent after the hold (off the applier), every segment of the hold queued.
        assert all(not owned and len(q) == 10 for owned, q, _ in sent), prefix
    assert stream.pending == other.pending == 0
    owner._fast_apply_stop()
    assert owner.fast_apply_thread is not None and not owner.fast_apply_thread.is_alive()


def test_batched_results_match_per_decision_apply_on_the_same_input():
    """Random full queues, read-ahead holds, old-chain records, raced exclusions and
    refusals: queue, every counter, releases and discarded rows are the same."""
    rng = random.Random(20261113)
    for case in range(150):
        capacity = rng.choice((128, 256, 640))
        records: list[dict[str, Any]] = []
        while True:
            rows = rng.randint(1, 64)
            if sum(r["rows"] for r in records) + rows > capacity:
                break
            fast = rng.random() > 0.1
            records.append({"segment_id": f"q{case}-{len(records)}", "rows": rows,
                            "run_id": f"r{rng.randint(0, 5)}", **({"fast": True} if fast
                            else {"actor": "a", "episode": "e"})})
        head = rng.randint(0, len(records))
        referenced = sorted(r["segment_id"] for r in records[:head]
                            if r.get("fast") and rng.random() > 0.1)
        excluded = {f"r{i}" for i in range(6) if rng.random() < 0.15}
        decisions = [IntakeDecision(admit=[
            _stub_segment(f"n{case}-{d}-{i}", f"r{rng.randint(0, 5)}", rng.randint(1, 64))
            for i in range(rng.randint(0, 3))]) for d in range(rng.randint(1, 6))]
        owners = []
        for batched in (False, True):
            owner = _stub_owner(capacity, excluded)
            _stub_queue(owner, [dict(r) for r in records])
            if referenced:
                owner.ahead_batches.append(({"segments": [
                    {"segment_id": sid} for sid in referenced]}, cast(Any, None)))
            if batched:
                _batched(owner)
                stream = _open(owner, _Connection(owner))
                for index, decision in enumerate(decisions):
                    owner._fast_submit(stream, decision, _ticket_message(index + 1))
                _start(owner)
                owner._fast_drain(stream)
                owner._fast_apply_stop()
                _flush(owner, stream)
                assert len(stream.connection.sent) == len(decisions)
            else:
                for decision in decisions:
                    owner._fast_apply(decision)
            owners.append(owner)
        plain, batched_owner = owners
        assert queued(plain) == queued(batched_owner), case
        assert dict(plain.fast_counters) == dict(batched_owner.fast_counters), case
        assert cast(Any, plain).released == cast(Any, batched_owner).released, case
        assert plain.queue.rows == batched_owner.queue.rows, case
        assert dict(plain.fast_admitted_rows) == dict(batched_owner.fast_admitted_rows), case


def test_an_apply_error_fences_and_that_connection_gets_nothing_more():
    owner = _stub_owner(10**6, set())
    events = _batched(owner)
    bad = _open(owner, _Connection(owner))
    good = _open(owner, _Connection(owner))
    owner._fast_submit(bad, IntakeDecision(admit=[_stub_segment("a0", "r1", 8)]),
                       _ticket_message(1))
    owner._fast_submit(bad, IntakeDecision(admit=[
        _stub_segment("a1", "r1", 8, compiler={"compiler": 2})]), _ticket_message(2))
    owner._fast_submit(good, IntakeDecision(admit=[_stub_segment("b0", "r2", 8)]),
                       _ticket_message(1))
    owner._fast_submit(bad, IntakeDecision(admit=[_stub_segment("a2", "r1", 8)]),
                       _ticket_message(3))
    _start(owner)
    owner._fast_drain(bad)
    owner._fast_drain(good)
    _flush(owner, bad, good)
    assert owner.input_fenced and owner.newlease_stopped
    assert owner.input_failure is not None
    assert owner.input_failure["operation"] == "fast_handoff"
    assert "compiler_identity_changed" in owner.input_failure["error"]
    # a0 applied and acked; a1 raised (not acked); a2 never applied.
    assert queued(owner) == ["a0"]
    assert [a for _, _, a in bad.connection.sent] == [1]
    # The other connection's decision ran after the fence: discarded, counted, acked.
    assert owner.training_discarded_rows == 8
    assert [a for _, _, a in good.connection.sent] == [1]
    # The broken connection's reader is told to stop (no second fence).
    with pytest.raises(_FastHandled):
        owner._fast_submit(bad, IntakeDecision(), None)
    assert not events  # per-decision failures fence like the reader did; no crash event
    # A later failure of that connection keeps the first one (no re-fence).
    first = dict(owner.input_failure)
    owner._fast_stream_failed(bad, RuntimeError("later"))
    assert owner.input_failure == first
    owner._fast_apply_stop()


def test_an_applier_crash_fences_loudly_and_readers_stop():
    owner = _stub_owner(10**6, set())
    events = _batched(owner)

    def crash(batch: Any) -> None:
        raise RuntimeError("applier bug")

    owner._fast_apply_tickets = crash  # type: ignore[method-assign]
    stream = _open(owner, _Connection(owner))
    owner._fast_submit(stream, IntakeDecision(admit=[_stub_segment("a0", "r1", 8)]),
                       _ticket_message(1))
    _start(owner)
    assert owner.fast_apply_thread is not None
    owner.fast_apply_thread.join(10)
    assert not owner.fast_apply_thread.is_alive()
    assert owner.input_fenced and owner.input_failure is not None
    assert owner.input_failure["operation"] == "fast_apply"
    assert events and events[0][0] == "fast_applier_failed"
    owner._fast_drain(stream)  # returns: the applier is gone
    _flush(owner, stream)
    assert stream.pending == 0 and not stream.connection.sent
    with pytest.raises(_FastHandled):
        owner._fast_submit(stream, IntakeDecision(), None)


def test_the_applier_queue_is_bounded_and_a_full_queue_blocks_the_reader():
    owner = _stub_owner(10**6, set())
    _batched(owner, capacity=2)
    stream = _open(owner, _Connection(owner))
    for i in range(2):
        owner._fast_submit(stream, IntakeDecision(admit=[_stub_segment(f"a{i}", "r1", 8)]),
                           _ticket_message(i + 1))
    third = threading.Thread(target=owner._fast_submit, args=(
        stream, IntakeDecision(admit=[_stub_segment("a2", "r1", 8)]), _ticket_message(3)))
    third.start()
    time.sleep(0.3)
    assert third.is_alive()  # waits for room; nothing dropped, nothing grows
    assert len(owner.fast_apply_items) == 2 and owner.fast_apply_stats["submit_waits"] == 1
    _start(owner)
    third.join(10)
    assert not third.is_alive()
    owner._fast_drain(stream)
    _flush(owner, stream)
    assert queued(owner) == ["a0", "a1", "a2"]
    assert owner.fast_apply_stats["peak_queue"] == 2
    assert [a for _, _, a in stream.connection.sent] == [1, 2, 3]
    owner._fast_apply_stop()


def test_stop_applies_what_is_queued_and_later_submits_end_the_reader():
    owner = _stub_owner(10**6, set())
    _batched(owner)
    stream = _open(owner, _Connection(owner))
    _start(owner)
    owner._fast_submit(stream, IntakeDecision(admit=[_stub_segment("a0", "r1", 8)]),
                       _ticket_message(1))
    owner._fast_apply_stop()
    assert owner.fast_apply_thread is not None and not owner.fast_apply_thread.is_alive()
    assert queued(owner) == ["a0"] and stream.pending == 0
    with pytest.raises(FastApplyClosed):
        owner._fast_submit(stream, IntakeDecision(), None)
    assert not owner.input_fenced


def test_an_ack_that_fails_while_stopping_is_recorded_not_fenced():
    owner = _stub_owner(10**6, set())
    events = _batched(owner)
    stream = _open(owner, _Connection(owner, fail=True))
    owner.newlease_stopped = True  # the learner is closing
    owner._fast_submit(stream, IntakeDecision(admit=[_stub_segment("a0", "r1", 8)]),
                       _ticket_message(1))
    _start(owner)
    owner._fast_drain(stream)
    _flush(owner, stream)
    assert not owner.input_fenced and owner.input_failure is None
    assert [name for name, _ in events] == ["fast_ack_failed_while_stopping"]
    assert stream.quiet and stream.failure is not None
    owner._fast_apply_stop()


def test_an_ack_that_fails_while_running_fences_and_that_connection_gets_no_more():
    owner = _stub_owner(10**6, set())
    _batched(owner)
    broken = _open(owner, _Connection(owner, fail=True))
    owner._fast_submit(broken, IntakeDecision(admit=[_stub_segment("a0", "r1", 8)]),
                       _ticket_message(1))
    _start(owner)
    owner._fast_drain(broken)
    assert broken.ack_thread is not None
    broken.ack_thread.join(10)
    assert owner.input_fenced and owner.input_failure is not None
    assert owner.input_failure["operation"] == "fast_handoff"
    assert owner.input_failure["error_type"] == "BrokenPipeError"
    assert not broken.quiet and broken.ack_closed
    with pytest.raises(_FastHandled):
        owner._fast_submit(broken, IntakeDecision(), None)
    owner._fast_apply_stop()


def test_a_peer_that_reads_no_acks_stalls_only_its_own_writer():
    """The applier never writes a socket: one stuck peer leaves the others flowing."""
    owner = _stub_owner(10**6, set())
    _batched(owner)
    release = threading.Event()

    class Stuck(_Connection):
        def send(self, message: Any) -> None:
            release.wait(30)  # a full socket buffer: send blocks
            super().send(message)

    stuck = _open(owner, Stuck(owner))
    flowing = _open(owner, _Connection(owner))
    _start(owner)
    for i in range(20):
        owner._fast_submit(stuck, IntakeDecision(admit=[_stub_segment(f"s{i}", "r1", 8)]),
                           _ticket_message(i + 1))
        owner._fast_submit(flowing, IntakeDecision(admit=[_stub_segment(f"f{i}", "r2", 8)]),
                           _ticket_message(i + 1))
    owner._fast_drain(stuck)
    owner._fast_drain(flowing)
    _flush(owner, flowing)
    assert [a for _, _, a in flowing.connection.sent] == list(range(1, 21))
    assert len(queued(owner)) == 40 and not stuck.connection.sent
    release.set()
    _flush(owner, stuck)
    assert [a for _, _, a in stuck.connection.sent] == list(range(1, 21))
    assert not owner.input_fenced
    owner._fast_apply_stop()


def _run_handoffs(owner: SegmentLearner) -> dict[str, Any]:
    """Three assemblers on three connections: an attested combat game with labels,
    an attested plain game and one never attested (buffered, then closed)."""
    from test_rl_fast_source import combat_game

    compiler = FrameCompiler(owner.model.vocabulary)
    games = [combat_game(handoff=learner_mu(owner), compiler=compiler,
                         assembler_id="node-g1"),
             game(owner, "g2"), game(owner, "g3")]
    admission(owner, "g1", 1)
    admission(owner, "g2", 2)
    processes, readers = [], []
    for sealed, labels in zip(games, (True, False, False), strict=True):
        ours, theirs = multiprocessing.Pipe()
        process = multiprocessing.Process(target=_sender,
                                          args=(theirs, sealed, closed_for(sealed), labels))
        process.start()
        theirs.close()
        processes.append(process)
        readers.append(owner.attach_fast_handoff(ours))
    for process in processes:
        process.join(60)
        assert process.exitcode == 0
    for reader in readers:
        reader.join(30)
        assert not reader.is_alive()
    assert not owner.input_fenced
    status = owner.fast_status()["fast_chain"]
    return {"queued": queued(owner), "learner": status["learner"],
            "intake": status["intake"], "terminal": list(owner.terminal.groups),
            "hp": list(owner.hp.groups), "sources": status["fast_source_segments"],
            "order": {name: [s.segment_id for s in sealed]
                      for name, sealed in zip(("g1", "g2", "g3"), games, strict=True)},
            "apply": status.get("apply")}


def test_real_handoffs_give_the_same_state_batched_or_not(tmp_path, monkeypatch):
    results = []
    for batched in (False, True):
        owner = fast_learner(tmp_path / str(batched), monkeypatch,
                             aux_labels=["terminal", "hp"],
                             dataplane_extra={"fast_batched_apply": batched})
        try:
            results.append(_run_handoffs(owner))
        finally:
            owner.close()
        assert not owner.input_fenced and owner.input_failure is None
    plain, batched_result = results
    for key in ("learner", "intake", "terminal", "hp", "sources"):
        assert plain[key] == batched_result[key], key

    def positions(result: dict[str, Any]) -> list[tuple[str, int]]:
        # Segment ids carry a per-run random part: compare (game, index) instead.
        return sorted((name, ids.index(sid)) for sid in result["queued"]
                      for name, ids in result["order"].items() if sid in ids)

    assert positions(plain) == positions(batched_result)
    assert len(positions(plain)) == len(plain["queued"]) > 0
    for result in results:
        for name in ("g1", "g2"):  # per-connection order is kept
            assert [s for s in result["queued"] if s in result["order"][name]] == \
                result["order"][name]
        assert not set(result["order"]["g3"]) & set(result["queued"])
    assert plain["apply"] is None
    assert batched_result["apply"]["items"] >= 1
    assert batched_result["apply"]["queue"] == 0


def test_a_batched_reader_ends_without_a_fence_on_a_normal_close(tmp_path, monkeypatch):
    """#518: a learner close is not reported as fenced. The reader is mid-stream
    (sender alive, no end yet); after close its next decision finds the applier
    stopped and the reader just ends."""
    owner = fast_learner(tmp_path, monkeypatch, dataplane_extra={"fast_batched_apply": True})
    sealed = game(owner, "g1")
    # The next message: another instance's close from the same assembler.
    later = {**closed_for(game(owner, "g2")),
             "assembler_id": sealed[0].header["assembler_instance"]}
    gate = threading.Event()
    messages = [closed_for(sealed)]

    class Held:
        def recv(self) -> Any:
            if messages:
                return messages.pop()
            gate.wait(30)
            return later

        def send(self, message: Any) -> None:
            raise AssertionError("a closed message is never acked")

    reader = owner.attach_fast_handoff(Held())
    deadline = time.monotonic() + 10
    while owner.fast_apply_stats["items"] < 1:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    owner.close()
    assert owner.fast_apply_thread is not None and not owner.fast_apply_thread.is_alive()
    gate.set()
    reader.join(10)
    assert not reader.is_alive()
    assert not owner.input_fenced and owner.input_failure is None


def test_an_applier_crash_whose_event_raises_still_fences_and_the_reader_raises(
        tmp_path, monkeypatch):
    """Review #522 item 1: the fence comes first, so a raising crash event cannot
    skip it, and the reader stops loudly on the applier failure (no quiet return)."""
    owner = fast_learner(tmp_path, monkeypatch, dataplane_extra={"fast_batched_apply": True})
    events: list[str] = []
    original_event = owner._event

    def event(name: str, **fields: Any) -> None:
        events.append(name)
        if name == "fast_applier_failed":
            raise RuntimeError("telemetry down")
        original_event(name, **fields)

    def crash(batch: Any) -> None:
        raise RuntimeError("applier bug")

    monkeypatch.setattr(owner, "_event", event)
    monkeypatch.setattr(owner, "_fast_apply_tickets", crash)
    sealed = game(owner, "g1")
    later = {**closed_for(game(owner, "g2")),
             "assembler_id": sealed[0].header["assembler_instance"]}
    messages = [closed_for(sealed)]

    class Held:
        def recv(self) -> Any:
            if messages:
                return messages.pop()
            thread = owner.fast_apply_thread
            assert thread is not None
            thread.join(10)  # the next message arrives after the applier crashed
            return later

        def send(self, message: Any) -> None:
            raise AssertionError("a closed message is never acked")

    owner.fast_apply_thread = threading.Thread(target=owner._fast_applier, daemon=True)
    owner.fast_apply_thread.start()
    try:
        with pytest.raises(_FastHandled, match="fast_applier_failed"):
            owner._fast_receive(Held())
        assert "fast_applier_failed" in events
        assert owner.input_fenced and owner.input_failure is not None
        assert owner.input_failure["operation"] == "fast_apply"
        assert "applier bug" in owner.input_failure["error"]
    finally:
        owner.close()


def test_a_normal_close_applies_queued_exclusions_and_aux_rows_before_shutdown(
        tmp_path, monkeypatch):
    """Review #522 item 3: decisions still queued at close (an env_crash exclusion
    and aux labels) are applied while the pool, preparer and releaser are still
    up: no fence on a normal stop, every count as in a running learner."""
    owner = fast_learner(tmp_path, monkeypatch, aux_labels=["terminal", "hp"],
                         dataplane_extra={"fast_batched_apply": True})
    sealed = combat_game(handoff=learner_mu(owner),
                         compiler=FrameCompiler(owner.model.vocabulary),
                         assembler_id="node-g1")
    admission(owner, "g1", 1)
    log: list[str] = []
    reached, gate = threading.Event(), threading.Event()
    held: list[_FastTicket] = []
    apply_tickets = owner._fast_apply_tickets

    def gated(batch: list[_FastTicket]) -> None:
        # Admissions apply at once; the labels / close decisions wait at the gate.
        cut = next((i for i, t in enumerate(batch)
                    if t.decision.auxiliary or t.decision.exclude_instance), len(batch))
        if cut:
            apply_tickets(batch[:cut])
        if cut < len(batch):
            held.extend(batch[cut:])
            reached.set()
            assert gate.wait(30)
            apply_tickets(batch[cut:])

    def logged(name: str, function: Any) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            log.append(name)
            return function(*args, **kwargs)
        return call

    monkeypatch.setattr(owner, "_fast_apply_tickets", gated)
    monkeypatch.setattr(owner, "_fast_exclude", logged("exclude", owner._fast_exclude))
    monkeypatch.setattr(owner, "_add_auxiliary", logged("aux", owner._add_auxiliary))
    monkeypatch.setattr(owner.pool, "close", logged("pool_closed", owner.pool.close))
    monkeypatch.setattr(owner.handoff_releaser, "shutdown",
                        logged("releaser_shutdown", owner.handoff_releaser.shutdown))
    ours, theirs = multiprocessing.Pipe()
    process = multiprocessing.Process(target=_sender, args=(
        theirs, sealed, closed_for(sealed, close_reason="env_crash"), True))
    process.start()
    theirs.close()
    reader = owner.attach_fast_handoff(ours)
    assert reached.wait(30)
    deadline = time.monotonic() + 30
    while not any(t.decision.exclude_instance
                  for t in [*held, *list(owner.fast_apply_items)]):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert queued(owner)  # the admissions applied: the exclusion has rows to drop
    closer = threading.Thread(target=owner.close)
    closer.start()
    # Released only once close stops the applier: whatever close shut down before
    # that point is already gone when the queued decisions apply.
    while not owner.fast_apply_stopping:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    gate.set()
    closer.join(60)
    assert not closer.is_alive()
    process.join(60)
    reader.join(30)
    assert process.exitcode == 0 and not reader.is_alive()
    assert log.index("exclude") < log.index("pool_closed")
    assert log.index("aux") < log.index("pool_closed")
    assert log.index("pool_closed") < log.index("releaser_shutdown")
    assert not owner.input_fenced and owner.input_failure is None
    assert owner.fast_counters["excluded_instances"] == 1
    assert owner.fast_counters["excluded_dropped_rows"] > 0
    assert owner.fast_counters["excluded_aux_rows"] > 0
    assert not queued(owner)
