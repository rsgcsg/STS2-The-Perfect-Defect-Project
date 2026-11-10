"""WP8 labels end to end: real assembler -> SegmentHandoff (real labeler, real shm sender)
-> a learner process reading with `read_handoff` into `FastSegmentIntake` (#450 review S3)."""
from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import Any

from test_rl_fast_source import Game, attest, mu, published

from stpd.rl.fast_handoff import DurableSegmentWriter, SegmentHandoff, ShmSegmentSender
from stpd.rl.fast_source import FastSegmentIntake, ack, build_instance_labels, read_handoff

GAMES = ("g1", "g2", "g3")


def _learner(connection: Any, handoff: dict[str, Any], out: Any) -> None:
    """The SGD side: attest, take segments and labels (acked), read every close."""
    intake = FastSegmentIntake(run="run-1", generation="gen-1",
                               aux_labels=frozenset({"terminal", "hp"}))
    intake.publish_mu(handoff)
    for game in GAMES:
        intake.attest(attest(game))
    trace: list[Any] = []
    closed = 0
    while closed < len(GAMES):
        kind, message, data = read_handoff(connection)
        if kind == "segment":
            assert data is not None
            admitted = intake.offer(message, data).admit
            trace.append(("segment", message["segment_id"], len(admitted)))
            ack(connection, message)
        elif kind == "labels":
            assert data is not None
            groups = intake.labels(message, data).auxiliary
            trace.append(("labels", message["game_instance_id"],
                          [(k, identity, len(rows)) for k, identity, rows in groups]))
            ack(connection, message)
        else:
            closed += 1
            decision = intake.closed(message)
            trace.append(("closed", message["game_instance_id"], decision.exclude_instance))
    out.put((trace, intake.report()))


def _combat_steps(game: Game, name: str, steps: range) -> None:
    for step in steps:  # combat 0-9 (post hp 40), map, combat 20-29 (post hp 30), terminal 69
        combat = step < 10 or 20 <= step < 30
        hp = 40 if step == 9 else 30 if step == 29 else 60
        game.step(name, in_combat=combat, hp=hp, terminal=step == 69)


def test_real_labeler_and_learner_agree_end_to_end(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    ours, theirs = context.Pipe()
    out = context.Queue()
    game = Game()
    instance = game.assembler.instance_id
    # Game offers mu(1); the learner publishes the same identity (its own publication).
    process = context.Process(target=_learner, args=(theirs, published(mu(1)), out),
                              daemon=True)
    process.start()
    try:
        handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=instance),
                                 ShmSegmentSender(ours, assembler_id=instance, ack_timeout=20),
                                 labeler=build_instance_labels)
        emitted: list[tuple[str, str]] = []  # (game, segment_id) in emit order

        def emit(segment: Any) -> None:
            emitted.append((segment.header["game_instance_id"], segment.segment_id))
            handoff.submit(segment)

        game.assembler.emit = emit
        game.assembler.on_closed = handoff.submit_closed
        for name in GAMES:
            game.open(name)
        # g1 and g2 interleave and end naturally; g3 ends with an unknown delivery.
        _combat_steps(game, "g1", range(0, 40))
        _combat_steps(game, "g2", range(0, 70))
        _combat_steps(game, "g1", range(40, 70))
        game.close("g2", "terminal")
        game.close("g1", "terminal")
        _combat_steps(game, "g3", range(0, 65))
        game.step("g3", status="unknown", reason="delivery_unknown")
        game.close("g3", "unknown")
        handoff.close()
        trace, report = out.get(timeout=60)
        process.join(timeout=30)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)

    labels = {entry[1]: entry[2] for entry in trace if entry[0] == "labels"}
    expected = [("terminal", None, 64), ("hp", None, 10), ("hp", None, 10)]
    for name in ("g1", "g2"):
        assert [(k, None, n) for k, _identity, n in labels[name]] == expected
        assert [i for _k, i, _n in labels[name]][1:] == [f"{name}:0", f"{name}:20"]
        # Order per game: its own last segment, then its labels, then its close.
        last = [segment_id for owner, segment_id in emitted if owner == name][-1]
        at = trace.index(("labels", name, labels[name]))
        assert trace[at + 1] == ("closed", name, None)
        assert trace[at - 1][:2] == ("segment", last)
    assert "g3" not in labels and ("closed", "g3", "g3") in trace
    assert handoff.counters["labels_sent"] == 2
    assert handoff.counters["labels_ineligible"] == 1
    assert handoff.counters["labels_failed"] == 0
    assert report["labels_received"] == 2 and report["labels_terminal_rows"] == 128
    assert report["labels_hp_rows"] == 40
