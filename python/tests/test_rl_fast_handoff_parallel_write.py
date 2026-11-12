"""SegmentHandoff writes originals in parallel, hands off in seal order (c3b 11-12).

c3b: one writer thread on NFS scratch managed ~3.3 segments/s; the bounded queue sat
at `max_queue` and `submit` (under the assembler's emit_lock) blocked the service's
scoring thread for 66% of its time. These tests reproduce that back-pressure with a
slow writer and check that parallel writes keep every hand-off rule.
"""
from __future__ import annotations

import ast
import contextlib
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stpd.rl.fast_handoff import HandoffError, SegmentHandoff
from stpd.rl.fast_segment import InstanceClosed


class SlowWriter:
    """Stands in for DurableSegmentWriter: a fixed or per-segment write latency."""

    def __init__(self, delay: float, delays: dict[str, float] | None = None,
                 fail: str | None = None) -> None:
        self.delay, self.delays, self.fail = delay, delays or {}, fail
        self.lock = threading.Lock()
        self.on_disk: set[str] = set()
        self.active = self.peak = 0

    def write(self, segment: Any) -> Path:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delays.get(segment.segment_id, self.delay))
            if segment.segment_id == self.fail:
                raise OSError("disk full")
            with self.lock:
                self.on_disk.add(segment.segment_id)
            return Path(f"/originals/{segment.segment_id}.stfs")
        finally:
            with self.lock:
                self.active -= 1


class RecordingSender:
    """Stands in for ShmSegmentSender: records hand-off order and disk-first."""

    def __init__(self, writer: SlowWriter) -> None:
        self.writer, self.sent, self.ended = writer, [], None

    def send(self, segment: Any, path: Path) -> int:
        # Disk first: a segment is handed off only after its own original exists.
        assert segment.segment_id in self.writer.on_disk
        assert path == Path(f"/originals/{segment.segment_id}.stfs")
        self.sent.append(segment.segment_id)
        return len(self.sent)

    def send_closed(self, closed: InstanceClosed) -> None:
        self.sent.append(f"closed:{closed.game_instance_id}")

    def finish(self) -> None:
        self.ended = "finish"

    def close(self) -> None:
        self.ended = "close"


def segment(name: str, *, trainable: bool = True) -> Any:
    return SimpleNamespace(segment_id=name, trainable=trainable, data=b"x" * 100,
                           header={"game_instance_id": "g1"})


def closed(game: str) -> InstanceClosed:
    return InstanceClosed(game_instance_id=game, close_reason="stop", failure=None,
                          segments={"bootstrap": 0, "terminal": 0, "truncated": 0,
                                    "rejected": 0})


def submit_all(handoff: SegmentHandoff, count: int) -> float:
    began = time.perf_counter()
    for index in range(count):
        handoff.submit(segment(f"s{index}"))
    return time.perf_counter() - began


def test_one_slow_writer_blocks_submit_and_parallel_writes_free_it():
    # The c3b back-pressure: 16 seals, 50 ms per durable write, queue of 4.
    one = SlowWriter(0.05)
    serial = SegmentHandoff(one, RecordingSender(one), max_queue=4, write_workers=1)
    blocked = submit_all(serial, 16)
    serial.close()
    four = SlowWriter(0.05)
    sender = RecordingSender(four)
    parallel = SegmentHandoff(four, sender, max_queue=4, write_workers=4)
    freed = submit_all(parallel, 16)
    parallel.finish()
    assert blocked >= 0.5  # >= (16 - 4 queued - 1 in hand) x 50 ms, the seal caller waits
    assert freed < blocked / 2
    assert one.peak == 1 and four.peak > 1 and four.peak <= 4
    assert sender.sent == [f"s{index}" for index in range(16)]  # seal order
    assert parallel.counters["written"] == parallel.counters["handed_off"] == 16
    assert sender.ended == "finish"


def test_a_slow_earlier_write_never_lets_a_later_segment_overtake_it():
    writer = SlowWriter(0.0, delays={"s0": 0.3})
    sender = RecordingSender(writer)
    handoff = SegmentHandoff(writer, sender, write_workers=4)
    handoff.submit(segment("s0"))
    handoff.submit(segment("s1"))
    handoff.submit_closed(closed("g1"))
    handoff.submit(segment("s2", trainable=False))  # truncated: written, never handed off
    handoff.finish()
    assert sender.sent == ["s0", "s1", "closed:g1"]
    assert writer.on_disk == {"s0", "s1", "s2"}
    assert handoff.counters["truncated_written"] == 1


def test_a_failed_write_is_counted_in_order_and_stays_loud():
    writer = SlowWriter(0.0, delays={"s0": 0.2}, fail="s1")
    sender = RecordingSender(writer)
    handoff = SegmentHandoff(writer, sender, write_workers=4)
    for name in ("s0", "s1", "s2"):
        # once s1's write failed; s2 was queued first and is still written
        with contextlib.suppress(HandoffError):
            handoff.submit(segment(name))
    with pytest.raises(HandoffError) as raised:
        handoff.finish()
    assert str(raised.value) == "segment_write_failed"
    assert str(raised.value.__cause__) == "disk full"
    # Nothing is retried; later segments are still written and handed off in order.
    assert sender.sent == ["s0", "s2"]
    assert handoff.counters["write_failed"] == 1 and handoff.counters["written"] == 2
    assert sender.ended == "close"  # never the orderly end after a failure
    with pytest.raises(HandoffError):
        handoff.submit(segment("s3"))  # every later caller sees the first failure


@pytest.mark.parametrize("workers", [0, -1, 1.5, True])
def test_write_workers_must_be_a_positive_int(workers: Any):
    with pytest.raises(HandoffError, match="positive_write_workers_required"):
        SegmentHandoff(SlowWriter(0.0), write_workers=workers)


def test_labels_read_back_runs_off_the_hand_off_thread_with_the_same_message(tmp_path: Path):
    # c3b: the in-order labels read-back took a median 1.35 s per labelled game.
    from test_rl_fast_assembly import _carry_all, _clean_game, _Recorder

    from stpd.rl import fast_handoff
    from stpd.rl.fast_handoff import DurableSegmentWriter

    instance, segments, closed_message = _clean_game()
    readers: list[str] = []
    original = fast_handoff.read_original

    def recording(path: Path, expected: str) -> bytes:
        readers.append(threading.current_thread().name)
        return original(path, expected)

    labels: list[tuple[dict, bytes]] = []

    def labeler(kept: list[Any]) -> tuple[dict, bytes]:
        labels.append(_carry_all(kept))
        return labels[-1]

    fast_handoff.read_original = recording
    try:
        sender = _Recorder(instance)
        handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=instance),
                                 sender, labeler=labeler)  # type: ignore[arg-type]
        for item in segments:
            handoff.submit(item)
        handoff.submit_closed(closed_message)
        handoff.close()
    finally:
        fast_handoff.read_original = original
    assert sender.sent == [segments[0].segment_id, segments[1].segment_id, "labels",
                           ("closed", "g1")]
    assert labels == [_carry_all(segments)]  # the same bytes as the originals in memory
    assert readers and all(name.startswith("fast-segment-read-back") for name in readers)
    assert handoff.counters["labels_sent"] == 1 and not handoff._prefetch
    assert not handoff._submitted


def _run_child(code: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    tests = Path(__file__).resolve().parent
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tests.parent), str(tests)])}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env,
                          capture_output=True, text=True, timeout=timeout)


_CHILD_SETUP = """
    import sys, tempfile, threading
    from pathlib import Path
    from test_rl_fast_assembly import _carry_all, _clean_game, _Recorder
    from stpd.rl.fast_handoff import DurableSegmentWriter, SegmentHandoff

    instance, segments, closed_message = _clean_game()
    root = Path(tempfile.mkdtemp())
"""


def test_exit_without_close_does_not_hang_on_a_stuck_hand_off(tmp_path: Path):
    # Review #504: a read-back used to wait (non-daemon) for the hand-off thread to pass
    # its segment, so a stuck `send` kept the process from exiting.
    result = _run_child(_CHILD_SETUP + """
    class Stuck(_Recorder):
        def send(self, segment, path):
            threading.Event().wait()  # never returns

    handoff = SegmentHandoff(DurableSegmentWriter(root, instance_id=instance),
                             Stuck(instance), labeler=_carry_all)
    for item in segments:
        handoff.submit(item)
    handoff.submit_closed(closed_message)
    sys.exit(3)
    """)
    assert result.returncode == 3, result.stderr


def test_close_returns_and_is_loud_when_the_hand_off_thread_dies(tmp_path: Path):
    result = _run_child(_CHILD_SETUP + """
    from stpd.rl.fast_handoff import HandoffError

    def dies(self, queued):
        raise RuntimeError("hand-off thread gone")  # not one of the per-segment cases

    SegmentHandoff._hand_off = dies
    handoff = SegmentHandoff(DurableSegmentWriter(root, instance_id=instance),
                             _Recorder(instance), labeler=_carry_all, max_queue=2)
    loud = []
    for step in [*(lambda item=item: handoff.submit(item) for item in segments),
                 lambda: handoff.submit_closed(closed_message), handoff.close]:
        try:
            step()
        except HandoffError as error:
            loud.append(str(error))
    print("loud", loud[-1])  # close() itself returned and raised
    sys.exit(0 if loud else 4)
    """)
    assert result.returncode == 0, result.stderr
    assert "loud segment_handoff_failed" in result.stdout


def test_two_threads_writing_the_same_original_one_wins(tmp_path: Path, monkeypatch):
    from test_rl_fast_assembly import _clean_game

    from stpd.rl import fast_handoff
    from stpd.rl.fast_handoff import DurableSegmentWriter

    instance, segments, _ = _clean_game()
    original = fast_handoff._durable_write
    entered = threading.Barrier(2, timeout=10)

    def slow(target: Path, data: bytes, code: str) -> None:
        time.sleep(0.2)  # both threads are past the claim check before either renames
        original(target, data, code)

    monkeypatch.setattr(fast_handoff, "_durable_write", slow)
    writer = DurableSegmentWriter(tmp_path, instance_id=instance)
    outcomes: list[str] = []

    def write() -> None:
        entered.wait()
        try:
            writer.write(segments[0])
            outcomes.append("written")
        except HandoffError as error:
            outcomes.append(str(error))

    threads = [threading.Thread(target=write) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["segment_original_already_exists", "written"]
    assert not writer._claimed  # a written target leaves the set
    with pytest.raises(HandoffError, match="segment_original_already_exists"):
        writer.write(segments[0])  # later: refused by the file on disk, same code
    assert not writer._claimed


def test_submit_on_a_full_queue_raises_when_the_hand_off_thread_is_dead(tmp_path: Path):
    # Re-review of #504: with the thread gone a full queue never drains, and the put
    # sits under the assembler's emit_lock, so the service hung instead of stopping.
    result = _run_child("""
    import sys, threading
    from pathlib import Path
    from types import SimpleNamespace
    from stpd.rl.fast_handoff import HandoffError, SegmentHandoff

    class Writer:
        def write(self, segment):
            return Path(f"/originals/{segment.segment_id}.stfs")

    class Sender:
        def send(self, segment, path):
            return 1

    def dies(self, queued):
        raise RuntimeError("hand-off thread gone")

    SegmentHandoff._hand_off = dies
    handoff = SegmentHandoff(Writer(), Sender(), max_queue=2)
    outcomes = []
    for index in range(4):
        segment = SimpleNamespace(segment_id=f"s{index}", trainable=True, data=b"x",
                                  header={"game_instance_id": "g1"})
        try:
            handoff.submit(segment)
            outcomes.append("ok")
        except HandoffError as error:
            outcomes.append(str(error))
        if index == 0:
            handoff.thread.join(10)  # dies on segment 1
    try:
        handoff.close()
        outcomes.append("close ok")
    except HandoffError as error:
        outcomes.append(f"close {error}")
    print(outcomes)
    """, timeout=30)
    assert result.returncode == 0, result.stderr
    outcomes = ast.literal_eval(result.stdout)
    assert outcomes[3] == "segment_handoff_failed"  # the 4th submit raises, not hangs
    assert outcomes[-1] == "close segment_handoff_failed"
