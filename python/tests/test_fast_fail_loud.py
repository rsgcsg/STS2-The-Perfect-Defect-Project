"""A broken fast service fails loudly and ends its process; its learner stops.

Friday v3 stage 1 (s1c): the service died on its first mu publish but its process
stayed alive, and the learner waited for data until its deadline (50 minutes).
"""
from __future__ import annotations

import contextlib
import multiprocessing
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from test_p1_fast_inference import (  # noqa: F401 - fixtures
    MU,
    FakeAssembler,
    Handoff,
    _service,
    model,
    vocabulary,
)
from test_rl_fast_learner import admission, fast_learner, game
from test_rl_fast_source import _sender, bootstrap_and_terminal, closed_for

from stpd.p1.fast_mu_follower import FastMuFollower
from stpd.rl.fast_assembly import MuHandoff
from stpd.rl.fast_handoff import (
    HANDOFF_END_SCHEMA,
    HandoffError,
    ShmSegmentSender,
    check_handoff_end,
    receive_handoff,
)
from stpd.rl.fast_source import ack, read_handoff


def _wait(condition: Any, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def _exitcode(process: Any, seconds: float = 30.0) -> int | None:
    """`process.exitcode` after it ended, robust to CPython's reaping race: the learner's
    pools start worker processes lazily from its reader thread, and every
    `Process.start()` runs `multiprocessing.process._cleanup()`, which polls (waitpid)
    *all* children, this test's sender included. When that poll reaps the sender while
    this thread is in `join()`, this thread's `waitpid` fails with ECHILD and
    `popen_fork.poll` returns None once; the other poll then stores the real code."""
    process.join(seconds)
    _wait(lambda: process.exitcode is not None, seconds)
    return process.exitcode


# -- service: any failure stops it, and the process ends non-zero --------------------

def test_a_failed_publish_stops_the_service_loudly(vocabulary, model):  # noqa: F811
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler)
    try:
        broken = model
        broken.unpicklable = threading.RLock()  # what a stray process-local cache does
        with pytest.raises(TypeError):
            service.publish(broken, MuHandoff("c" * 64, "gen-1", 2, 8))
        assert service.stop_reason == "publish_failed" and service.stopping.is_set()
        assert assembler.mu == Handoff(MU)  # nothing new was offered
    finally:
        del model.unpicklable
        service.close()


def test_a_refused_offer_is_an_answer_not_a_failure(vocabulary, model):  # noqa: F811
    assembler = FakeAssembler()
    service = _service(vocabulary, model, assembler)

    class Refused:
        accepted, evicted = False, ()

    assembler.offer_mu = lambda handoff: Refused()  # type: ignore[method-assign]
    try:
        assert service.publish(model, MuHandoff("c" * 64, "gen-1", 2, 8)) is False
        assert service.stop_reason is None and not service.stopping.is_set()
    finally:
        service.close()


def test_an_unhandled_background_thread_error_stops_the_service(vocabulary, model):  # noqa: F811
    service = _service(vocabulary, model, FakeAssembler())
    try:
        def broken() -> None:
            raise RuntimeError("batcher_bug")

        thread = threading.Thread(target=service._guarded(broken), name="rl-inference-3-x")
        thread.start()
        thread.join(10)
        assert service.stop_reason == "thread_failed" and service.stopping.is_set()
        # Every background thread the service starts runs under this guard.
        assert all(t._target.__qualname__.endswith("_guarded.<locals>.run")  # type: ignore[attr-defined]
                   for t in service.threads)
    finally:
        service.close()


def test_a_failure_stop_closes_and_ends_the_process_with_its_code(vocabulary, model):  # noqa: F811
    service = _service(vocabulary, model, FakeAssembler(), exit_code_on_failure=3)
    exits: list[int] = []
    service.exit_process = exits.append  # os._exit in production
    service._stop("device_job_failed", "test")
    assert _wait(lambda: exits == [3])
    assert service.closed.is_set()  # closed (bounded) before the exit


def test_an_orderly_close_never_ends_the_process(vocabulary, model):  # noqa: F811
    service = _service(vocabulary, model, FakeAssembler(), exit_code_on_failure=3)
    exits: list[int] = []
    service.exit_process = exits.append
    service.close()
    time.sleep(0.2)
    assert exits == [] and service.stop_reason is None


def test_the_failure_closer_and_the_launcher_close_together_once(vocabulary, model):  # noqa: F811
    """s1d: the exit watch's close and the launcher's close ran at once: EBADF on the second."""
    service = _service(vocabulary, model, FakeAssembler(), exit_code_on_failure=3)
    exits: list[int] = []
    service.exit_process = exits.append
    service._stop("mu_follower_stopped", "test")  # the failure closer starts closing
    answers: list[Any] = []
    launcher = threading.Thread(target=lambda: answers.append(service.close()))
    launcher.start()
    launcher.join(60)
    assert _wait(lambda: exits == [3])
    assert len(answers) == 1 and answers[0]["schema"] == service.close()["schema"]
    assert service.close() is answers[0]  # every later call gets the one close's answer


def test_exit_code_zero_is_refused(vocabulary, model):  # noqa: F811
    with pytest.raises(ValueError, match="exit_code_on_failure_must_be_non_zero"):
        _service(vocabulary, model, FakeAssembler(), exit_code_on_failure=0)


def _exits_on_failure(vocabulary: Any, model: Any, path: str) -> None:  # noqa: F811
    service = _service(vocabulary, model, FakeAssembler(), exit_code_on_failure=7,
                       exit_grace_seconds=20)
    Path(path).write_text("up")
    service._stop("device_process_gone", "test")
    time.sleep(120)  # a hung launcher main thread: the watcher still ends the process


def test_a_failed_service_process_exits_non_zero_instead_of_hanging(
        vocabulary, model, tmp_path):  # noqa: F811
    marker = tmp_path / "up"
    process = multiprocessing.get_context("spawn").Process(
        target=_exits_on_failure, args=(vocabulary, model, str(marker)))
    process.start()
    process.join(90)
    alive = process.is_alive()
    if alive:
        process.kill()
    assert marker.exists() and not alive and process.exitcode == 7


# -- the follower stops with its service ---------------------------------------------

def test_a_started_follower_stops_when_its_service_stops():
    class Service:
        compiler_identity: dict[str, Any] = {}
        stopping = threading.Event()

        def publish(self, scorer: Any, handoff: Any) -> bool:
            return True

    class Source:
        def latest(self) -> dict[str, Any]:
            return {"snapshot_sha256": "c" * 64, "training_generation": "g", "version": 1,
                    "updates": 1, "compiler_identity": {}}

        def load(self, identity: dict[str, Any]) -> Any:
            return object()

    service = Service()
    follower = FastMuFollower(Source(), service, interval=0.01).start()
    assert _wait(lambda: follower.offered is not None)
    service.stopping.set()
    assert follower.thread is not None
    follower.thread.join(10)
    assert follower.stopped() and follower.fatal is None


# -- the hand-off: an orderly end, and EOF without it fences the learner -------------

def test_the_sender_says_an_orderly_end_after_every_ack():
    segments = bootstrap_and_terminal()
    ours, theirs = multiprocessing.Pipe()
    process = multiprocessing.get_context("spawn").Process(
        target=_sender, args=(theirs, segments, closed_for(segments)))
    process.start()
    kinds = []
    while True:
        kind, value, _ = read_handoff(ours)
        kinds.append(kind)
        if kind == "segment":
            ack(ours, value)
        if kind == "end":
            assert value == {"schema": HANDOFF_END_SCHEMA,
                             "assembler_id": segments[0].header["assembler_instance"],
                             "handed_off": 2}
            break
    assert _exitcode(process) == 0 and kinds == ["segment", "segment", "closed", "end"]


def test_the_reference_reader_and_the_end_schema():
    ours, theirs = multiprocessing.Pipe()
    sender = ShmSegmentSender(theirs, assembler_id="node-1")
    sender.finish()  # nothing in flight: the end at once
    assert receive_handoff(ours) == ("end", {"schema": HANDOFF_END_SCHEMA,
                                             "assembler_id": "node-1", "handed_off": 0})
    for bad in ({"schema": HANDOFF_END_SCHEMA, "assembler_id": "", "handed_off": 0},
                {"schema": HANDOFF_END_SCHEMA, "assembler_id": "a", "handed_off": -1},
                {"schema": HANDOFF_END_SCHEMA, "assembler_id": "a", "handed_off": 0, "x": 1}):
        with pytest.raises(HandoffError, match="handoff_end_schema"):
            check_handoff_end(bad)


def _lost_sender(connection: Any, segments: list[Any], stacks: str, how: str) -> None:
    """Hand one segment off, wait for its ack, then end without the orderly end:
    `crash` dies (os._exit), `close` is a failure path's `finally: sender.close()`.

    Stacks are dumped on SIGUSR1 (sent by the test only on a hang). Never
    `faulthandler.dump_traceback_later` here: under the fork start method (Linux) the
    child inherits the parent's armed watchdog (pytest's `faulthandler_timeout`) without
    its thread, and re-arming it waits forever for that thread."""
    import faulthandler
    import signal

    with open(stacks, "w") as handle:  # only read when the test fails
        faulthandler.register(signal.SIGUSR1, file=handle, all_threads=True)
        sender = ShmSegmentSender(connection,
                                  assembler_id=segments[0].header["assembler_instance"])
        sender.send(segments[0], Path(__file__))
        sender._drain(block=True)
        if how == "close":
            sender.close()  # release only: never the orderly end
            faulthandler.unregister(signal.SIGUSR1)
            connection.close()
            return
        faulthandler.unregister(signal.SIGUSR1)
    os._exit(1)  # the service process dies: no end, the channel just reaches EOF


def _sender_stacks(process: Any, stacks: Path) -> str | None:
    """On a hang only: ask a still-running sender for its stacks (SIGUSR1)."""
    import signal

    if process.is_alive():
        with contextlib.suppress(ProcessLookupError):
            os.kill(process.pid, signal.SIGUSR1)
        time.sleep(1)
    return stacks.read_text() if stacks.exists() else None


@pytest.mark.parametrize("how,exitcode", [("crash", 1), ("close", 0)])
def test_a_channel_ending_without_the_end_fences_the_learner(tmp_path, monkeypatch, how,
                                                            exitcode):
    owner = fast_learner(tmp_path / "learner", monkeypatch)
    stacks = tmp_path / "sender-stacks.txt"
    process = None
    try:
        sealed = game(owner, "g1")
        admission(owner, "g1", 1)
        ours, theirs = multiprocessing.Pipe()
        process = multiprocessing.get_context("spawn").Process(
            target=_lost_sender, args=(theirs, sealed, str(stacks), how))
        process.start()
        theirs.close()
        reader = owner.attach_fast_handoff(ours)
        reader.join(45)  # under CI's faulthandler_timeout (60 s)
        state = {"reader_alive": reader.is_alive(), "fenced": owner.input_fenced,
                 "failure": owner.input_failure, "sender_exitcode": process.exitcode}
        if state["reader_alive"]:
            state["sender_stacks"] = _sender_stacks(process, stacks)
        # The learner's verdict first, then the sender.
        assert not state["reader_alive"], state
        assert owner.input_fenced and owner.newlease_stopped, state
        assert owner.input_failure is not None, state
        assert owner.input_failure["operation"] == "fast_handoff_lost", state
        assert owner.input_failure["error"] == "assembler_gone_without_end", state
        assert _exitcode(process) == exitcode, state
    finally:
        if process is not None and process.is_alive():
            process.kill()
        owner.close()


def test_an_end_that_does_not_match_the_channel_fences_the_learner(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        ours, theirs = multiprocessing.Pipe()
        reader = owner.attach_fast_handoff(ours)
        theirs.send({"schema": HANDOFF_END_SCHEMA, "assembler_id": "node-1",
                     "handed_off": 3})  # nothing was handed off on this channel
        reader.join(30)
        assert not reader.is_alive() and owner.input_fenced
        assert owner.input_failure is not None
        assert owner.input_failure["error"] == "handoff_end_mismatch"
    finally:
        owner.close()


def test_an_orderly_end_leaves_the_learner_running(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        sealed = game(owner, "g1")
        admission(owner, "g1", 1)
        ours, theirs = multiprocessing.Pipe()
        process = multiprocessing.get_context("spawn").Process(
            target=_sender, args=(theirs, sealed, closed_for(sealed)))
        process.start()
        theirs.close()
        reader = owner.attach_fast_handoff(ours)
        reader.join(30)
        assert _exitcode(process) == 0 and not reader.is_alive()
        assert not owner.input_fenced and owner.input_failure is None
    finally:
        owner.close()


def test_segment_handoff_says_the_end_only_on_a_clean_finish(tmp_path):
    from stpd.rl.fast_handoff import DurableSegmentWriter, SegmentHandoff

    class Sender:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def finish(self) -> None:
            self.calls.append("finish")

        def close(self) -> None:
            self.calls.append("close")

    def handoff(sender: Sender) -> SegmentHandoff:
        return SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id="node-1"),
                              sender)  # type: ignore[arg-type]

    clean, closed, failed = Sender(), Sender(), Sender()
    handoff(clean).finish()
    handoff(closed).close()  # a launcher's `finally`: release only
    broken = handoff(failed)
    broken.write_error = OSError("disk_full")
    with pytest.raises(HandoffError, match="segment_write_failed"):
        broken.finish()  # a failed assembler never says the orderly end
    assert (clean.calls, closed.calls, failed.calls) == (["finish"], ["close"], ["close"])
