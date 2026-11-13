"""Design a': fast hand-off reads in verify worker processes (`fast_verify_workers`)."""
from __future__ import annotations

import hashlib
import multiprocessing
import threading
import time
from multiprocessing import shared_memory
from typing import Any

import pytest
from test_rl_fast_learner import admission, fast_learner, learner_mu
from test_rl_fast_source import (
    _handoff_block,
    _sender,
    attest,
    bootstrap_and_terminal,
    closed_for,
    combat_game,
    intake,
    message,
)

from stpd.p1.frame_compiler import FrameCompiler
from stpd.rl.fast_handoff import HandoffError
from stpd.rl.fast_segment import FORMAT, FORMAT_2, SegmentFormatError
from stpd.rl.fast_source import (
    FastIntakeError,
    IntakeDecision,
    ack,
    build_instance_labels,
    read_handoff,
)
from stpd.rl.fast_verify import (
    REPLY_BYTES,
    FastVerifyClosed,
    FastVerifyError,
    FastVerifyPool,
    VerifyArena,
)

AUX = frozenset({"terminal", "hp"})


def pool(workers: int = 1, slots: int = 8, slot_bytes: int = 2 * 1024**2) -> FastVerifyPool:
    return FastVerifyPool(workers=workers, slots=slots, slot_bytes=slot_bytes)


def summary(decision: IntakeDecision) -> Any:
    def segment(value: Any) -> Any:
        return value.segment_id, value.header, bytes(value.data)

    return ([(*segment(s), envelope) for s, envelope in decision.admit],
            decision.exclude_instance, decision.reply,
            [(kind, identity, [(*segment(r["segment"]), r["envelope"], r["row"],
                                r["step_index"], r["label"]) for r in rows])
             for kind, identity, rows in decision.auxiliary])


def receive_all(verifier: FastVerifyPool | None, sealed: list[Any]) -> tuple[list[Any], Any]:
    """One real sender process: two segments, labels, close, end; every decision."""
    i = intake(aux_labels=AUX)
    i.attest(attest("g1"))
    ours, theirs = multiprocessing.Pipe()
    process = multiprocessing.Process(target=_sender,
                                      args=(theirs, sealed, closed_for(sealed), True))
    process.start()
    theirs.close()
    decisions = []
    try:
        while True:
            if verifier is None:
                kind, value, data = read_handoff(ours)
                checked = None
            else:
                kind, value, data, checked = verifier.read_handoff(ours, decode_headers=True)
            if kind == "end":
                break
            if kind == "closed":
                decisions.append(summary(i.closed(value)))
                continue
            assert data is not None
            call = i.offer if kind == "segment" else i.labels
            decision = call(value, data) if checked is None else call(value, data, checked)
            decisions.append((kind, summary(decision)))
            ack(ours, value)
    finally:
        process.join(30)
    assert process.exitcode == 0
    return decisions, i.report()


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("segment_format", [FORMAT, FORMAT_2])
def test_verify_workers_give_the_same_decisions_and_counts(workers, segment_format):
    sealed = combat_game() if segment_format == FORMAT else bootstrap_and_terminal(
        segment_format=FORMAT_2)
    expected = receive_all(None, sealed)
    assert [d[0] for d in expected[0][:3]] == ["segment", "segment", "labels"]
    assert expected[0][2][1][3]  # the labels decision carries aux groups
    verifier = pool(workers)
    try:
        assert receive_all(verifier, sealed) == expected
        status = verifier.status()
        assert sum(status["requests"]) == 3 and status["dead"] == []
        assert status["arena"]["in_use"] == 0
    finally:
        verifier.close()


def raised(call: Any) -> tuple[type, str]:
    with pytest.raises(BaseException) as info:
        call()
    return info.type, str(info.value)


def test_worker_errors_are_raised_with_their_own_class_and_code():
    verifier = pool()
    data = bytes(range(256)) * 4
    block, _, ours, theirs = _handoff_block(data)
    try:
        assert read_handoff(ours)[2] == data  # the block's own valid message
        segment = bootstrap_and_terminal()[0]

        def both(change: dict[str, Any]) -> tuple[Any, Any]:
            theirs.send({**message(segment), "shm_name": block.name, **change})
            theirs.send({**message(segment), "shm_name": block.name, **change})
            return (raised(lambda: read_handoff(ours)),
                    raised(lambda: verifier.read_handoff(ours, decode_headers=False)))

        digest = hashlib.sha256(data).hexdigest()
        local, remote = both({"size": block.size + 1, "sha256": digest})
        assert local == remote == (HandoffError, "handoff_size")
        local, remote = both({"size": len(data), "sha256": "0" * 64})
        assert local == remote == (HandoffError, "handoff_digest")
        # Bytes that pass the digest but are no segment: the header parse's own
        # error, raised by offer() (before any intake state) on both paths.
        theirs.send({**message(segment), "shm_name": block.name, "size": len(data),
                     "sha256": digest})
        _, value, raw, checked = verifier.read_handoff(ours, decode_headers=False)
        assert raw == data and checked is not None
        assert (raised(lambda: intake().offer(value, data))
                == raised(lambda: intake().offer(value, data, checked))
                == (SegmentFormatError, "segment_magic_or_size"))
    finally:
        block.close()
        block.unlink()
    local, remote = both({"size": len(data), "sha256": digest})
    assert local[0] is remote[0] is FileNotFoundError
    assert verifier.status()["dead"] == [] and verifier.status()["arena"]["in_use"] == 0
    verifier.close()


def labels_block(fields: dict[str, Any], data: bytes) -> tuple[Any, dict[str, Any]]:
    block = shared_memory.SharedMemory(create=True, size=max(len(data), 1))
    assert block.buf is not None
    block.buf[:len(data)] = data
    return block, {**fields, "handoff_sequence": 9, "shm_name": block.name,
                   "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_labels_fences_keep_their_codes_through_the_worker():
    verifier = pool()
    try:
        for flip, code in ((100, "fast_labels_segment_bytes_changed"),
                           (None, "fast_labels_rule_changed")):
            for checked_path in (False, True):
                i = intake(aux_labels=AUX)
                i.attest(attest("g1"))
                sealed = combat_game()
                for n, segment in enumerate(sealed, 1):
                    i.offer(message(segment, n), segment.data)
                fields, data = build_instance_labels(sealed)
                if flip is not None:
                    data = data[:flip] + bytes([data[flip] ^ 1]) + data[flip + 1:]
                else:
                    fields = {**fields, "outcome_rows": fields["outcome_rows"][1:]}
                block, value = labels_block(fields, data)
                try:
                    ours, theirs = multiprocessing.Pipe()
                    theirs.send(value)
                    checked = None
                    if checked_path:
                        _, value, raw, checked = verifier.read_handoff(ours,
                                                                       decode_headers=True)
                    else:
                        _, value, raw = read_handoff(ours)
                    assert raw is not None
                    with pytest.raises(FastIntakeError) as info:
                        i.labels(value, raw, checked)
                    assert str(info.value) == code
                finally:
                    block.close()
                    block.unlink()
    finally:
        verifier.close()


def test_a_killed_worker_is_reported_dead_and_never_retried():
    verifier = pool(workers=1)
    segment = bootstrap_and_terminal()[0]
    block = shared_memory.SharedMemory(create=True, size=len(segment.data))
    assert block.buf is not None
    block.buf[:len(segment.data)] = segment.data
    try:
        value = {**message(segment), "shm_name": block.name}
        verifier.verify("segment", value, decode_headers=False)  # alive and serving
        verifier.processes[0].kill()
        verifier.processes[0].join(10)
        with pytest.raises(FastVerifyError, match="fast_verify_worker_died worker=0"):
            verifier.verify("segment", value, decode_headers=False)
        assert verifier.status()["dead"] == [0]
        assert verifier.status()["arena"]["in_use"] == 0
    finally:
        block.close()
        block.unlink()
        verifier.close()


def test_a_full_arena_blocks_and_drops_nothing():
    arena = VerifyArena(slots=2, slot_bytes=16)
    try:
        with pytest.raises(FastVerifyError, match="fast_verify_arena_too_small"):
            arena.acquire(33)
        with pytest.raises(FastVerifyError, match="fast_verify_arena_too_small"):
            arena.acquire(20, reserve=13)  # 24 (8-aligned) + 13 > 32
        first = arena.acquire(20)  # two slots: the arena is full
        assert first == (0, 2) and arena.acquire(0) == (0, 0)
        got: list[Any] = []
        waiter = threading.Thread(target=lambda: got.append(arena.acquire(5)))
        waiter.start()
        time.sleep(0.2)
        assert waiter.is_alive() and not got  # blocked, not refused or dropped
        arena.release(*first)
        waiter.join(5)
        assert got == [(0, 1)] and arena.waits == 1 and arena.peak == 2
        arena.release(*got[0])
    finally:
        arena.close()
    # Through the pool: six concurrent readers share one slot, every read arrives.
    segment = bootstrap_and_terminal()[0]
    block = shared_memory.SharedMemory(create=True, size=len(segment.data))
    assert block.buf is not None
    block.buf[:len(segment.data)] = segment.data
    verifier = pool(workers=2, slots=1, slot_bytes=len(segment.data) + REPLY_BYTES)
    results: list[bytes] = []
    try:
        value = {**message(segment), "shm_name": block.name}
        threads = [threading.Thread(target=lambda: results.extend(
            verifier.verify("segment", value, decode_headers=False)[0] for _ in range(5)))
            for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        assert results == [segment.data] * 30
        assert verifier.status()["arena"]["peak"] == 1
    finally:
        block.close()
        block.unlink()
        verifier.close()


def handoff(owner: Any, sealed: list[Any]) -> Any:
    ours, theirs = multiprocessing.Pipe()
    process = multiprocessing.Process(target=_sender,
                                      args=(theirs, sealed, closed_for(sealed), True))
    process.start()
    theirs.close()
    reader = owner.attach_fast_handoff(ours)
    reader.join(30)
    process.join(30 if not owner.input_fenced else 1)
    if process.is_alive():  # a fenced reader never acks: the sender waits for it
        process.kill()
        process.join(10)
    return process, reader


@pytest.mark.parametrize("workers", [0, 2])
def test_learner_admits_the_same_with_and_without_verify_workers(tmp_path, monkeypatch,
                                                                  workers):
    owner = fast_learner(tmp_path, monkeypatch, aux_labels=["terminal", "hp"],
                         dataplane_extra={"fast_verify_workers": workers})
    try:
        assert (owner.fast_verifier is None) is (workers == 0)
        sealed = combat_game(handoff=learner_mu(owner),
                             compiler=FrameCompiler(owner.model.vocabulary),
                             assembler_id="node-g1")
        admission(owner, "g1", 1)
        process, reader = handoff(owner, sealed)
        assert process.exitcode == 0 and not reader.is_alive() and not owner.input_fenced
        assert [r["segment_id"] for r in owner.queue.pending] == [s.segment_id for s in sealed]
        assert list(owner.terminal.groups) == ["g1"] and len(owner.terminal.groups["g1"]) == 64
        assert list(owner.hp.groups) == ["g1:0", "g1:20"]
        status = owner.fast_status()["fast_chain"]
        assert status["learner"]["admitted"] == 2 and status["learner"]["admitted_rows"] == 70
        assert status["intake"]["labels_terminal_rows"] == 64
        assert status["intake"]["labels_hp_rows"] == 20
        assert ("verify" in status) is (workers > 0)
        assert owner._prepare() and owner.active is not None
    finally:
        owner.close()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_learner_fences_when_its_verify_worker_dies(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch, dataplane_extra={"fast_verify_workers": 1})
    try:
        owner.fast_verifier.processes[0].kill()
        owner.fast_verifier.processes[0].join(10)
        sealed = combat_game(handoff=learner_mu(owner),
                             compiler=FrameCompiler(owner.model.vocabulary),
                             assembler_id="node-g1")
        admission(owner, "g1", 1)
        _process, reader = handoff(owner, sealed)
        assert not reader.is_alive() and owner.input_fenced
        assert owner.input_failure is not None
        assert owner.input_failure["error"].startswith("fast_verify_worker_died")
        assert owner.input_failure["error_type"] == "FastVerifyError"
        assert not owner.queue.pending
    finally:
        owner.close()


def test_close_refuses_new_reads_and_waiting_ones_without_a_died_report():
    segment = bootstrap_and_terminal()[0]
    block = shared_memory.SharedMemory(create=True, size=len(segment.data))
    assert block.buf is not None
    block.buf[:len(segment.data)] = segment.data
    verifier = pool(workers=1, slots=1, slot_bytes=len(segment.data) + REPLY_BYTES)
    value = {**message(segment), "shm_name": block.name}
    try:
        held = verifier.arena.acquire(1)  # the only slot: the next read waits
        errors: list[BaseException] = []

        def read() -> None:
            try:
                verifier.verify("segment", value, decode_headers=False)
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        waiter = threading.Thread(target=read)
        waiter.start()
        time.sleep(0.2)
        assert waiter.is_alive()
        closer = threading.Thread(target=verifier.close)
        closer.start()
        waiter.join(10)
        assert [type(e) for e in errors] == [FastVerifyClosed]
        assert str(errors[0]) == "fast_verify_pool_closed"
        verifier.arena.release(*held)
        closer.join(30)
        assert not closer.is_alive() and verifier.status()["dead"] == []
        with pytest.raises(FastVerifyClosed, match="fast_verify_pool_closed"):
            verifier.verify("segment", value, decode_headers=False)
        verifier.close()  # idempotent
    finally:
        block.close()
        block.unlink()


def test_closing_the_learner_while_a_read_is_in_flight_does_not_fence(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch, dataplane_extra={"fast_verify_workers": 1})
    verifier = owner.fast_verifier
    assert verifier is not None
    entered, go = threading.Event(), threading.Event()
    real = verifier.verify

    def gated(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        go.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(verifier, "verify", gated)
    sealed = combat_game(handoff=learner_mu(owner),
                         compiler=FrameCompiler(owner.model.vocabulary),
                         assembler_id="node-g1")
    admission(owner, "g1", 1)
    ours, theirs = multiprocessing.Pipe()
    process = multiprocessing.Process(target=_sender,
                                      args=(theirs, sealed, closed_for(sealed), True))
    process.start()
    theirs.close()
    reader = owner.attach_fast_handoff(ours)
    try:
        assert entered.wait(30)  # a segment message is in this reader's hands
        owner.close()  # a normal stop with the read still in flight
        go.set()
        reader.join(30)
        assert not reader.is_alive()
        assert not owner.input_fenced and owner.input_failure is None
    finally:
        go.set()
        process.kill()
        process.join(10)
