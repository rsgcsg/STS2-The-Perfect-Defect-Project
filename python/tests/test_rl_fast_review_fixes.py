"""WP5 review fixes (PR #422): learner-aligned value checks, disk-first under hand-off
failure, exception paths keep originals, instance identity, input type checks."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import time
from multiprocessing import shared_memory
from pathlib import Path
from typing import Any

import pytest
from test_rl_fast_assembly import Harness, mu, values

from stpd.rl.fast_assembly import AssemblyError, FastSegmentAssembler, MuHandoff, MuLedger
from stpd.rl.fast_handoff import (
    HANDOFF_SCHEMA,
    DurableSegmentWriter,
    HandoffError,
    SegmentHandoff,
    ShmSegmentSender,
    receive_segment,
)
from stpd.rl.fast_segment import (
    SegmentFormatError,
    check_value_distribution,
    decode_segment,
    encode_segment,
    supported,
)
from stpd.rl.heads import OUTCOME_CLASSES, outcome_mask


def started(h: Harness, game: str = "g1", single_choice: str = "score") -> None:
    h.assembler.offer_mu(mu(1))
    h.open(game, single_choice)


# ---------------------------------------------------------------- M1 values
def test_floor_support_is_the_learner_mask():
    for floor in range(61):
        mask = outcome_mask([floor])[0].tolist()
        assert [supported(c, floor) for c in range(OUTCOME_CLASSES)] == mask


@pytest.mark.parametrize("broken,code", [
    (lambda v: v[:2], "segment_mu_value_distribution"),            # not 51 classes
    (lambda v: v[:49] + [v[49] + 5e-5, 0.0], "segment_mu_value_distribution"),  # mass 1+5e-5
    (lambda v: v[:49] + [v[49] - 5e-5, 0.0], "segment_mu_value_distribution"),
    (lambda v: v[:49] + [float("nan"), 0.0], "segment_mu_value_distribution"),
    (lambda v: [True] + v[1:], "segment_mu_value_distribution"),
    (lambda v: [0.5 if i == 2 else 0.0 for i in range(49)] + [0.5, 0.0],
     "segment_mu_value_floor_support"),  # mass on class 2 with observed floor 5
])
def test_row_values_must_pass_the_learner_check(broken, code):
    h = Harness()
    started(h)
    row, data = h.row("g1", floor=5)
    plan = h.assembler.begin_row(row, data)
    with pytest.raises(AssemblyError, match=code):
        h.assembler.finish_row(plan, h.scored(plan, row), broken(values(5)))
    within = values(5)
    within[49] += 5e-6  # inside the learner's 1e-5 tolerance
    h.assembler.finish_row(plan, h.scored(plan, row), within)  # state was unchanged


def test_tail_values_are_checked_against_the_tail_floor():
    h = Harness()
    started(h)
    for _ in range(64):
        h.step("g1", floor=1)
    row, data = h.row("g1", floor=10)
    plan = h.assembler.begin_row(row, data)
    assert plan.tail is not None and plan.tail.floor == 10
    with pytest.raises(AssemblyError, match="segment_mu_value_floor_support"):
        h.assembler.finish_row(plan, h.scored(plan, row), values(10), values(1))
    h.assembler.finish_row(plan, h.scored(plan, row), values(10), values(10))


def test_check_value_distribution_refuses_bad_floor():
    for floor in (-1, "3", None):
        with pytest.raises(SegmentFormatError, match="segment_mu_value_floor_support"):
            check_value_distribution(values(1), floor)


def test_encoder_refuses_non_integer_forced_steps():
    h = Harness()
    started(h)
    h.step("g1")
    h.close("g1", "stop")
    header, rows = decode_segment(h.sealed[0].data)
    header["rows"][0]["outcome"]["forced_steps"] = "1"
    with pytest.raises(SegmentFormatError, match="segment_forced_steps"):
        encode_segment(header, rows)


# --------------------------------------- M3 exception paths keep originals
@pytest.mark.parametrize("label,error", [
    (0, "terminal_before_observed_floor"),  # class 0 while floor 5 was observed
    (51, "segment_terminal_class"),
])
def test_encoder_refusal_is_written_as_rejected_original(tmp_path: Path, label, error):
    h = Harness(lambda summary: {"class": label, "reward": 0.1})
    handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=h.assembler.instance_id))
    h.assembler.emit = handoff.submit
    started(h)
    h.step("g1", floor=5)
    _, row = h.step("g1", outcome=False, floor=5)
    h.outcome(row, terminal={"result": "defeat"})
    with pytest.raises(AssemblyError, match=f"segment_rejected:{error}"):
        h.close("g1", "terminal", row["step_index"])
    handoff.close()
    [path] = list((tmp_path / "rejected").iterdir())
    original = json.loads(path.read_bytes())
    assert original["format"] == "stpd/rl-fast-segment-rejected-1"
    assert original["error"] == error and len(original["rows_base64"]) == 2
    assert original["header"]["seal"]["terminal"]["class"] == label
    report = h.assembler.report()
    assert report["segments"]["rejected"] == 1 and report["open_segments"] == 0
    assert report["referenced_mu"] == {}
    assert handoff.counters["rejected_written"] == 1


def test_same_weights_new_generation_registers_and_keeps_old_segments():
    h = Harness()
    started(h)  # mu(1) under gen-1
    for _ in range(3):
        h.step("g1")
    restored = mu(1, "gen-2")  # learner CP restore with unchanged weights
    assert restored.snapshot_sha256 == mu(1).snapshot_sha256
    offer = h.assembler.offer_mu(restored)
    assert offer.accepted and offer.generation_changed
    [segment] = h.sealed  # the gen-1 rows are an original, not lost
    assert segment.header["seal"]["truncation"] == {"reason": "generation_discarded",
                                                    "detail": "gen-1"}
    assert len(segment.header["rows"]) == 3
    assert h.assembler.ledger.resident[restored.snapshot_sha256] == restored
    plan, _ = h.step("g1")
    assert plan.mu == restored


def test_same_generation_other_identity_is_refused_before_any_change():
    h = Harness()
    started(h)
    h.step("g1")
    before = h.assembler.report()
    conflicting = MuHandoff(mu(1).snapshot_sha256, "gen-1", 7, 7)
    with pytest.raises(AssemblyError, match="mu_snapshot_identity_changed"):
        h.assembler.offer_mu(conflicting)
    assert h.assembler.report() == before and not h.sealed
    ledger = MuLedger()
    ledger.offer(mu(1))
    with pytest.raises(AssemblyError, match="mu_snapshot_identity_changed"):
        ledger.offer(MuHandoff(mu(1).snapshot_sha256, "gen-1", 2, 2))
    assert ledger.resident[mu(1).snapshot_sha256] == mu(1)


def test_emit_hands_on_every_segment_when_one_hand_off_fails():
    received: list[str] = []

    def emit(segment: Any) -> None:
        received.append(segment.segment_id)
        if len(received) == 1:
            raise RuntimeError("first hand-off failed")

    h = Harness()
    h.assembler.emit = emit
    h.assembler.offer_mu(mu(1, "gen-1"))
    for game in ("g1", "g2"):
        h.open(game)
        h.step(game)
    with pytest.raises(RuntimeError, match="first hand-off failed"):
        h.assembler.offer_mu(mu(2, "gen-2"))  # dooms both games' segments
    assert len(received) == 2
    assert h.assembler.report()["segments"]["truncated"] == 2


# ------------------------------------------- M2 disk first under SGD failure
class FailingSender:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, segment: Any, path: Path) -> int:
        self.calls += 1
        raise ConnectionResetError("sgd gone")

    def close(self) -> None:
        raise AssertionError("never closed after a hand-off failure")


def test_sgd_loss_still_writes_queued_and_later_segments(tmp_path: Path):
    h = Harness()
    started(h)
    for _ in range(129):
        h.step("g1")
    h.close("g1", "stop", 128)
    bootstrap_1, bootstrap_2, truncated = h.sealed
    sender = FailingSender()
    handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=h.assembler.instance_id),
                             sender)  # type: ignore[arg-type]
    handoff.submit(bootstrap_1)
    deadline = time.monotonic() + 30
    while handoff.send_error is None and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(HandoffError, match="segment_handoff_failed"):
        handoff.submit(bootstrap_2)  # queued (and written) first, then loud
    with pytest.raises(HandoffError, match="segment_handoff_failed"):
        handoff.submit(truncated)
    with pytest.raises(HandoffError, match="segment_handoff_failed"):
        handoff.close()
    assert sender.calls == 1
    assert handoff.counters["written"] == 3 and handoff.counters["handed_off"] == 0
    assert handoff.counters["not_handed_off"] == 2
    assert handoff.counters["truncated_written"] == 1
    assert sorted(p.name for p in (tmp_path / "bootstrap").iterdir()) == sorted(
        f"{s.segment_id}.stfs" for s in (bootstrap_1, bootstrap_2))
    assert (tmp_path / "truncated" / f"{truncated.segment_id}.stfs").read_bytes() == truncated.data


# ---------------------------------------------------------- M4 instance id
def test_every_start_is_a_new_instance_and_writer_refuses_existing_originals(tmp_path: Path):
    first, second = Harness(), Harness()
    a, b = first.assembler.instance_id, second.assembler.instance_id
    assert a != b and a.startswith("node-a.") and b.startswith("node-a.")
    started(first)
    first.step("g1")
    first.close("g1", "stop")
    [segment] = first.sealed
    assert segment.segment_id.startswith(a + ".")
    assert segment.header["assembler_instance"] == a
    DurableSegmentWriter(tmp_path, instance_id=a).write(segment)
    with pytest.raises(HandoffError, match="assembler_instance_already_has_originals"):
        DurableSegmentWriter(tmp_path, instance_id=a)
    other = DurableSegmentWriter(tmp_path, instance_id=b)  # a restart = a new instance
    with pytest.raises(HandoffError, match="segment_from_another_assembler_instance"):
        other.write(segment)
    parent, child = multiprocessing.Pipe()
    with pytest.raises(HandoffError, match="segment_from_another_assembler_instance"):
        ShmSegmentSender(parent, assembler_id=b).send(_trainable(), tmp_path / "x")


def test_assembler_id_must_be_non_empty_text():
    with pytest.raises(AssemblyError, match="invalid_assembler_id"):
        FastSegmentAssembler(assembler_id="", compiler_identity={}, emit=lambda s: None)


def _trainable() -> Any:
    h = Harness()
    started(h)
    for _ in range(65):
        h.step("g1")
    return h.sealed[0]


# ------------------------------------------------------------- S items
@pytest.mark.parametrize("forced", [-1, "2", 1.0, True])
def test_bad_forced_steps_change_nothing_and_a_resend_works(forced):
    h = Harness()
    started(h, single_choice="omit")
    _, row = h.step("g1", outcome=False)
    before = h.assembler.report()
    with pytest.raises(AssemblyError, match="invalid_forced_steps"):
        h.outcome(row, forced_steps=forced)
    assert h.assembler.report() == before
    h.outcome(row, forced_steps=2)
    assert h.assembler.report()["forced_steps"] == 2


@pytest.mark.parametrize("terminal", ["defeat", {"result": "draw"}, ["defeat"]])
def test_malformed_terminal_is_a_typed_error(terminal):
    h = Harness()
    started(h)
    _, row = h.step("g1", outcome=False)
    with pytest.raises(AssemblyError, match="invalid_terminal"):
        h.outcome(row, terminal=terminal)
    h.outcome(row, terminal={"result": "victory"})


def test_single_option_row_is_refused_under_omit():
    h = Harness()
    started(h, single_choice="omit")
    row, data = h.row("g1")
    row["catalog_len"] = 1
    with pytest.raises(AssemblyError, match="single_option_row_under_omit"):
        h.assembler.begin_row(row, data)


def test_inflight_bound_counts_sent_bytes(tmp_path: Path):
    segment = _trainable()
    parent, child = multiprocessing.Pipe()
    sender = ShmSegmentSender(parent, assembler_id=segment.header["assembler_instance"])
    sequence = sender.send(segment, tmp_path / "x")
    assert sender.inflight_bytes == len(segment.data) == sender.inflight[sequence][1]
    message = child.recv()
    assert message["size"] == len(segment.data)
    child.send({"schema": HANDOFF_SCHEMA, "ack": sequence})
    sender.close()
    assert sender.inflight_bytes == 0 and not sender.inflight


def _forged_receiver(connection: Any, count: int, out: Any) -> None:
    for _ in range(count):
        try:
            receive_segment(connection)
            out.put("ok")
        except HandoffError as error:
            out.put(str(error))


def test_receiver_checks_keys_instance_generation_and_mu(tmp_path: Path):
    segment = _trainable()
    header = segment.header
    good = {"schema": HANDOFF_SCHEMA, "handoff_sequence": 1,
            "assembler_id": header["assembler_instance"], "segment_id": segment.segment_id,
            "training_generation": header["mu"]["training_generation"],
            "mu_snapshot_sha256": header["mu"]["snapshot_sha256"],
            "rows": len(header["rows"]), "size": len(segment.data), "sha256": segment.sha256,
            "path": str(tmp_path / "x")}
    cases: list[tuple[dict[str, Any], str]] = [
        (good, "ok"),
        ({k: v for k, v in good.items() if k != "training_generation"}, "handoff_schema"),
        (dict(good, extra=1), "handoff_schema"),
        (dict(good, assembler_id="node-a.000000000000"), "handoff_segment_binding"),
        (dict(good, training_generation="gen-9"), "handoff_segment_binding"),
        (dict(good, mu_snapshot_sha256=hashlib.sha256(b"x").hexdigest()),
         "handoff_segment_binding"),
    ]
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    out = context.Queue()
    process = context.Process(target=_forged_receiver, args=(child, len(cases), out))
    process.start()
    blocks = []
    try:
        for message, expected in cases:
            block = shared_memory.SharedMemory(create=True, size=len(segment.data))
            blocks.append(block)
            assert block.buf is not None
            block.buf[:len(segment.data)] = segment.data
            parent.send(dict(message, shm_name=block.name))
            assert out.get(timeout=60) == expected
            if expected == "ok":
                assert parent.recv() == {"schema": HANDOFF_SCHEMA, "ack": 1}
        process.join(timeout=60)
        assert process.exitcode == 0
    finally:
        for block in blocks:
            block.close()
            block.unlink()

