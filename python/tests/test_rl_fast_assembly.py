"""WP5: inference-side 64-row segments, mu binding/tail/eviction, truncation, hand-off."""
from __future__ import annotations

import hashlib
import multiprocessing
from pathlib import Path
from typing import Any

import pytest
import torch

from stpd.rl.fast_assembly import (
    AssemblyError,
    FastSegmentAssembler,
    MuHandoff,
    MuLedger,
    MuNotResident,
)
from stpd.rl.fast_handoff import (
    DurableSegmentWriter,
    HandoffError,
    SegmentHandoff,
    ShmSegmentSender,
    check_instance_closed,
    receive_handoff,
    receive_segment,
)
from stpd.rl.fast_segment import (
    InstanceClosed,
    SealedSegment,
    SegmentFormatError,
    canonical_json,
    decode_segment,
    encode_segment,
)
from stpd.rl.heads import OUTCOME_CLASSES, categorical_lambda_targets

COMPILER = {"input_spec": {"id": "test"}, "vocabulary_sha256": "a" * 64,
            "compiler_sha256": "b" * 64}
SAMPLING = {"mode": "temperature_uniform", "seed": "draw-seed", "arm_id": "control",
            "classes": {"combat": {"temperature": 1.0, "uniform_mix": 0.0},
                        "noncombat": {"temperature": 1.0, "uniform_mix": 0.0}}}


def mu(index: int, generation: str = "gen-1") -> MuHandoff:
    return MuHandoff(hashlib.sha256(f"mu-{index}".encode()).hexdigest(), generation, index, index)


def values(floor: int = 1) -> list[float]:
    out = [0.0] * OUTCOME_CLASSES
    out[floor] = 0.5
    out[49] = 0.5
    return out


class Harness:
    def __init__(self, labeler: Any = None, **options: Any) -> None:
        self.sealed: list[SealedSegment] = []
        self.assembler = FastSegmentAssembler(assembler_id="node-a", compiler_identity=COMPILER,
                                              emit=self.sealed.append, terminal_labeler=labeler,
                                              **options)
        self.request = 0
        self.steps: dict[str, int] = {}

    def open(self, game: str, single_choice: str = "score", *,
             start: dict | None = None, **extra: Any) -> None:
        self.assembler.open_episode({
            "schema": "stpd/rl-inference-3", "kind": "episode_open", "request_id": 1,
            "game_instance_id": game, "actor_id": "actor-1", "sampling": SAMPLING,
            "single_choice": single_choice, "start": start or {"kind": "new_run"}, **extra})
        self.steps[game] = 0

    def row(self, game: str, *, floor: int = 1, combat: bool = True) -> tuple[dict, bytes]:
        self.request += 1
        step = self.steps[game]
        self.steps[game] += 1
        data = f"row-{game}-{step}".encode() * 3
        return {"request_id": self.request, "game_instance_id": game, "step_index": step,
                "consumption_id": f"c-{self.request}", "catalog_len": 2,
                "catalog_sha256": hashlib.sha256(f"cat-{step}".encode()).hexdigest(),
                "observation_sha256": hashlib.sha256(data).hexdigest(), "in_combat": combat,
                "floor": floor, "payload": {"offset": 0, "length": len(data),
                                            "row_sha256": hashlib.sha256(data).hexdigest()}}, data

    @staticmethod
    def scored(plan: Any, row: dict, **change: Any) -> dict:
        value = {"request_id": row["request_id"], "game_instance_id": row["game_instance_id"],
                 "catalog_sha256": row["catalog_sha256"], "status": "scored",
                 "mu_snapshot_sha256": plan.mu.snapshot_sha256, "segment_id": plan.segment_id,
                 "row_in_segment": plan.row_in_segment, "selected_index": 1,
                 "behavior_q": [0.25, 0.75], "selected_q": 0.75,
                 "applied": {"arm_id": "control",
                             "decision_class": "combat" if row["in_combat"] else "noncombat",
                             "temperature": 1.0, "uniform_mix": 0.0}}
        value.update(change)
        return value

    def outcome(self, row: dict, status: str = "completed", **extra: Any) -> None:
        message = {"schema": "stpd/rl-inference-3", "kind": "step_outcome",
                   "row_request_id": row["request_id"], "game_instance_id": row["game_instance_id"],
                   "step_index": row["step_index"], "status": status}
        if status == "completed":
            message.update(receipt_sha256="c" * 64, terminal=None,
                           public_facts={"act": row.get("act", 1), "floor": row["floor"],
                                         "hp": 50, "max_hp": 75})
        message.update(extra)
        self.assembler.step_outcome(message)

    def step(self, game: str, *, outcome: bool = True, tail: list[float] | None = None,
             **row_kwargs: Any) -> tuple[Any, dict]:
        row, data = self.row(game, **row_kwargs)
        plan = self.assembler.begin_row(row, data)
        tail_values = tail if tail is not None else (
            values(row["floor"]) if plan.tail is not None else None)
        self.assembler.finish_row(plan, self.scored(plan, row), values(row["floor"]), tail_values)
        if outcome:
            self.outcome(row)
        return plan, row

    def close(self, game: str, reason: str, last: int | None = None) -> None:
        self.assembler.close_episode({"schema": "stpd/rl-inference-3", "kind": "episode_close",
                                      "request_id": 1, "game_instance_id": game,
                                      "reason": reason, "last_step_index": last})


def test_full_segment_seals_bootstrap_with_old_mu_tail_and_new_mu_applies_next_segment():
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    plans = [h.step("g1")[0] for _ in range(10)]
    assert h.assembler.offer_mu(mu(2)).accepted  # newer mu arrives mid-segment
    plans += [h.step("g1")[0] for _ in range(54)]
    assert {p.mu for p in plans} == {mu(1)} and {p.segment_id for p in plans} == {
        f"{h.assembler.instance_id}.1"}
    assert [p.row_in_segment for p in plans] == list(range(64))
    assert not h.sealed  # a full segment waits for the tail on the successor row
    successor, row = h.step("g1", floor=2)
    assert successor.mu == mu(2) and successor.segment_id == f"{h.assembler.instance_id}.2"
    assert successor.tail is not None and successor.tail.mu == mu(1).snapshot_sha256
    assert successor.tail.floor == 2
    [segment] = h.sealed
    header, rows = decode_segment(segment.data, expected_sha256=segment.sha256)
    assert header["seal"]["kind"] == "bootstrap" and header["trainable"] is True
    assert header["mu"] == mu(1).identity() and len(rows) == 64
    assert header["seal"]["bootstrap"]["mu_snapshot_sha256"] == mu(1).snapshot_sha256
    assert header["seal"]["bootstrap"]["successor_request_id"] == row["request_id"]
    assert rows[0] == b"row-g1-0" * 3
    assert "seed" not in canonical_json(header["rows"]).decode()
    # the old mu is no longer referenced: the next offer may evict it
    assert mu(1).snapshot_sha256 in h.assembler.offer_mu(mu(3)).evicted


def test_targets_are_computable_from_the_segment_alone():
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    for _ in range(65):
        h.step("g1")
    header, _ = decode_segment(h.sealed[0].data)
    probabilities = torch.tensor([r["outcome_probabilities"] for r in header["rows"]])
    tail = torch.tensor(header["seal"]["bootstrap"]["outcome_probabilities"])
    targets, advantages = categorical_lambda_targets(probabilities, tail)
    assert targets.shape == (64, OUTCOME_CLASSES) and advantages.shape == (64,)


def test_mu_eviction_only_when_unreferenced_and_offer_declined_when_all_held():
    ledger = MuLedger(max_resident=3)
    h = Harness()
    h.assembler.ledger = ledger
    with pytest.raises(MuNotResident):
        h.open("g0") or h.assembler.begin_row(*h.row("g0"))
    for index, game in ((1, "g1"), (2, "g2"), (3, "g3")):
        assert h.assembler.offer_mu(mu(index)).accepted
        h.open(game)
        h.step(game)
    declined = h.assembler.offer_mu(mu(4))
    assert not declined.accepted and declined.evicted == ()
    assert set(ledger.resident) == {mu(i).snapshot_sha256 for i in (1, 2, 3)}
    h.close("g1", "stop")  # segment of mu1 sealed truncated, releasing mu1
    accepted = h.assembler.offer_mu(mu(4))
    assert accepted.accepted and accepted.evicted == (mu(1).snapshot_sha256,)
    assert h.assembler.report()["mu_declined"] == 1


def test_pinned_mu_is_not_evicted():
    ledger = MuLedger(max_resident=2)
    ledger.offer(mu(1))
    ledger.pin(mu(1).snapshot_sha256)
    ledger.offer(mu(2))
    assert not ledger.offer(mu(3)).accepted
    ledger.unpin(mu(1).snapshot_sha256)
    assert ledger.offer(mu(3)).evicted == (mu(1).snapshot_sha256, mu(2).snapshot_sha256)


def test_terminal_seal_uses_labeler_and_unlabelled_terminal_is_truncated():
    labels: list[dict] = []

    def labeler(summary: Any) -> dict | None:
        labels.append(dict(summary))
        return {"class": 5, "reward": 0.0054} if summary["game_instance_id"] == "g1" else None

    h = Harness(labeler)
    h.assembler.offer_mu(mu(1))
    for game in ("g1", "g2"):
        h.open(game)
        h.step(game)
        _, row = h.step(game, outcome=False, floor=5)
        h.outcome(row, terminal={"result": "defeat"}, combat_end=[{"won": False}])
        h.close(game, "terminal", row["step_index"])
    first, second = h.sealed
    assert first.kind == "terminal" and first.trainable
    assert first.header["seal"]["terminal"] == {"result": "defeat", "class": 5, "reward": 0.0054}
    assert labels[0]["max_floor"] == 5 and labels[0]["acts"] == [1, 1, 1]
    assert "combat_end" not in labels[0]  # never read before the fair-projection review
    assert second.kind == "truncated" and not second.trainable
    assert second.header["seal"]["truncation"]["reason"] == "terminal_unlabeled"


@pytest.mark.parametrize("status,reason,close", [
    ("not_started", "inference_timeout", "not_started"),
    ("unknown", "delivery_unknown", "unknown"),
])
def test_failed_outcome_truncates_and_keeps_every_row(status, reason, close):
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    for _ in range(64):
        h.step("g1")
    _, row = h.step("g1", outcome=False)
    h.outcome(row, status, reason=reason)
    h.close("g1", close, row["step_index"])
    # the full segment already had its tail and every outcome: it stays trainable
    assert [(s.kind, len(s.header["rows"])) for s in h.sealed] == [("bootstrap", 64),
                                                                     ("truncated", 1)]
    assert h.sealed[1].header["seal"]["truncation"] == {"reason": status, "detail": reason}
    assert h.sealed[1].header["rows"][0]["outcome"] == {"status": status, "reason": reason}
    h.open("g2")
    for _ in range(5):
        h.step("g2")
    _, row = h.step("g2", outcome=False)
    h.outcome(row, status, reason=reason)
    h.close("g2", close, row["step_index"])
    assert (h.sealed[2].kind, len(h.sealed[2].header["rows"])) == ("truncated", 6)
    report = h.assembler.report()
    assert report["segments"]["truncated"] == 2 and report["open_segments"] == 0
    assert report["referenced_mu"] == {}


def test_stop_close_truncates_open_segment_and_late_messages_are_counted():
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    _, row = h.step("g1")
    h.close("g1", "stop", row["step_index"])
    assert h.sealed[0].header["seal"]["truncation"] == {"reason": "stop", "detail": "stop"}
    h.outcome(row)
    h.close("g1", "stop")
    assert h.assembler.report()["late_discarded"] == 2
    with pytest.raises(AssemblyError):
        h.open("g1")  # a closed instance never reopens


def test_refused_row_enters_no_segment_and_dooms_the_game():
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    h.step("g1")
    row, data = h.row("g1")
    plan = h.assembler.begin_row(row, data)
    h.assembler.abort_row(plan, "ROW_DECODE_FAILED")
    [segment] = h.sealed
    assert len(segment.header["rows"]) == 1
    assert segment.header["seal"]["truncation"] == {"reason": "not_started", "detail": "refused"}
    with pytest.raises(AssemblyError):
        h.assembler.begin_row(*h.row("g1"))
    assert h.assembler.report()["aborted_rows"] == 1


def test_single_choice_omit_records_forced_steps():
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1", "omit")
    for index in range(65):
        _, row = h.step("g1", outcome=False)
        h.outcome(row, forced_steps=index % 3)
    header = h.sealed[0].header
    assert header["single_choice"] == "omit"
    assert header["forced_steps"] == sum(i % 3 for i in range(64))
    assert header["rows"][2]["outcome"]["forced_steps"] == 2
    h2 = Harness()
    h2.assembler.offer_mu(mu(1))
    h2.open("g1")
    _, row = h2.step("g1", outcome=False)
    with pytest.raises(AssemblyError, match="forced_steps_require_single_choice_omit"):
        h2.outcome(row, forced_steps=1)


@pytest.mark.parametrize("change", [
    {"mu_snapshot_sha256": "f" * 64},
    {"behavior_q": [0.3, 0.6], "selected_q": 0.6},
    {"selected_q": 0.25},
    {"applied": {"arm_id": "control", "decision_class": "noncombat", "temperature": 1.0,
                 "uniform_mix": 0.0}},
    {"row_in_segment": 5},
    {"catalog_sha256": "0" * 64},
])
def test_scored_result_binding_is_checked(change):
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    row, data = h.row("g1")
    plan = h.assembler.begin_row(row, data)
    with pytest.raises(AssemblyError, match="scored_result_binding"):
        h.assembler.finish_row(plan, h.scored(plan, row, **change), values())
    h.assembler.finish_row(plan, h.scored(plan, row), values())  # state was unchanged


def test_missing_value_or_tail_value_is_refused():
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    row, data = h.row("g1")
    plan = h.assembler.begin_row(row, data)
    with pytest.raises(AssemblyError, match="every_mu_value_required"):
        h.assembler.finish_row(plan, h.scored(plan, row), [])
    h.assembler.finish_row(plan, h.scored(plan, row), values())
    h.outcome(row)
    for _ in range(63):
        h.step("g1")
    h.assembler.offer_mu(mu(2))
    row, data = h.row("g1")
    plan = h.assembler.begin_row(row, data)
    with pytest.raises(AssemblyError, match="every_mu_value_required"):
        h.assembler.finish_row(plan, h.scored(plan, row), values(), None)


def test_row_payload_binding_and_order():
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    row, data = h.row("g1")
    with pytest.raises(AssemblyError, match="row_payload_binding"):
        h.assembler.begin_row(row, data + b"x")
    plan = h.assembler.begin_row(row, data)
    with pytest.raises(AssemblyError, match="row_already_in_flight"):
        h.assembler.begin_row(*h.row("g1"))
    h.assembler.finish_row(plan, h.scored(plan, row), values())
    with pytest.raises(AssemblyError, match="row_step_order"):
        h.assembler.begin_row(row, data)


def test_new_generation_truncates_older_generation_segments():
    h = Harness()
    h.assembler.offer_mu(mu(1, "gen-1"))
    h.open("g1")
    for _ in range(3):
        h.step("g1")
    offer = h.assembler.offer_mu(mu(2, "gen-2"))
    assert offer.accepted and offer.generation_changed
    assert mu(1, "gen-1").snapshot_sha256 in offer.evicted
    [segment] = h.sealed
    assert segment.header["seal"]["truncation"] == {"reason": "generation_discarded",
                                                    "detail": "gen-1"}
    plan, _ = h.step("g1")
    assert plan.mu == mu(2, "gen-2") and plan.tail is None


def test_segment_format_rejects_tampering():
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    for _ in range(65):
        h.step("g1")
    segment = h.sealed[0]
    data = bytearray(segment.data)
    data[-9] ^= 1  # inside the last row
    with pytest.raises(SegmentFormatError):
        decode_segment(bytes(data))
    with pytest.raises(SegmentFormatError, match="segment_digest_mismatch"):
        decode_segment(segment.data, expected_sha256="0" * 64)
    header, rows = decode_segment(segment.data)
    for key, value in (("trainable", False), ("forced_steps", 3)):
        broken = dict(header, **{key: value})
        with pytest.raises(SegmentFormatError):
            encode_segment(broken, rows)
    with pytest.raises(SegmentFormatError, match="segment_bootstrap_seal"):
        encode_segment(dict(header, rows=header["rows"][:63]), rows[:63])
    truncated = dict(header, seal={"kind": "truncated", "bootstrap": None, "terminal": None,
                                   "truncation": {"reason": "stop", "detail": "stop"}})
    with pytest.raises(SegmentFormatError, match="segment_truncated_is_never_trainable"):
        encode_segment(truncated, rows)


def _receiver(connection: Any, count: int, out: Any) -> None:
    for _ in range(count):
        message, header, rows = receive_segment(connection)
        out.put((message["handoff_sequence"], header["segment_id"], len(rows),
                 Path(message["path"]).is_file()))


def test_handoff_writes_to_disk_first_and_sends_only_trainable(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    out = context.Queue()
    process = context.Process(target=_receiver, args=(child, 2, out))
    process.start()
    h = Harness()
    instance = h.assembler.instance_id
    handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=instance),
                             ShmSegmentSender(parent, assembler_id=instance,
                                              max_inflight_bytes=1))
    h.assembler.emit = handoff.submit
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    for _ in range(129):
        h.step("g1")
    h.open("g2")
    h.step("g2")
    h.close("g2", "stop")
    handoff.close()
    received = [out.get(timeout=60) for _ in range(2)]
    process.join(timeout=60)
    assert process.exitcode == 0
    assert received == [(1, f"{instance}.1", 64, True), (2, f"{instance}.2", 64, True)]
    assert handoff.counters == {"written": 3, "handed_off": 2, "truncated_written": 1,
                                "rejected_written": 0, "bytes": handoff.counters["bytes"],
                                "write_failed": 0, "not_handed_off": 0,
                                "instances_closed_sent": 0, "instances_closed_not_sent": 0}
    assert len(list((tmp_path / "bootstrap").iterdir())) == 2
    [truncated] = list((tmp_path / "truncated").iterdir())
    assert decode_segment(truncated.read_bytes())[0]["seal"]["kind"] == "truncated"


def test_handoff_failure_is_loud_and_sticky(tmp_path: Path):
    h = Harness()
    writer = DurableSegmentWriter(tmp_path, instance_id=h.assembler.instance_id)
    handoff = SegmentHandoff(writer)
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    h.step("g1")
    h.close("g1", "stop")
    [segment] = h.sealed
    handoff.submit(segment)
    handoff.submit(segment)  # an original is never overwritten
    with pytest.raises(HandoffError) as raised:
        handoff.close()
    assert str(raised.value.__cause__) == "segment_original_already_exists"
    assert (tmp_path / "truncated" / f"{segment.segment_id}.stfs").read_bytes() == segment.data
    with pytest.raises(HandoffError):
        handoff.submit(segment)  # every later caller sees the first failure


def test_real_rows_v2_row_survives_the_segment():
    from test_p1_frame_compiler import assert_frame_equal
    from test_p1_input_v2 import public_state

    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.p1.input import V2_INPUT_SPEC
    from stpd.p1.rows_encoder_wire import decode_row, encode_row
    from stpd.p1.vocabulary import public_vocabulary

    compiler = FrameCompiler(public_vocabulary(V2_INPUT_SPEC))
    frame = compiler.compile(public_state())
    raw = encode_row(frame)
    h = Harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    row, _ = h.row("g1")
    row["catalog_len"] = len(frame.choices)
    row["payload"] = {"offset": 0, "length": len(raw),
                      "row_sha256": hashlib.sha256(raw).hexdigest()}
    plan = h.assembler.begin_row(row, raw)
    q = [1.0 / len(frame.choices)] * len(frame.choices)
    h.assembler.finish_row(plan, h.scored(plan, row, behavior_q=q, selected_index=0,
                                          selected_q=q[0]), values())
    h.close("g1", "stop")
    _, rows = decode_segment(h.sealed[0].data)
    assert_frame_equal(frame, decode_row(rows[0], validator=compiler,
                                         expected_sha256=row["payload"]["row_sha256"]))


# -- WP8: instance-closed signal ---------------------------------------------------------
def _closing_harness(**options: Any) -> tuple[Harness, list[Any]]:
    stream: list[Any] = []
    h = Harness(**options)
    h.assembler.emit = stream.append
    h.assembler.on_closed = stream.append
    return h, stream


def test_every_close_emits_instance_closed_after_its_segments():
    h, stream = _closing_harness()
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    for _ in range(64):
        h.step("g1")
    _, row = h.step("g1", outcome=False)
    h.outcome(row, "unknown", reason="compact_state_divergence")
    h.close("g1", "unknown", row["step_index"])
    h.open("g2")
    h.step("g2")
    h.close("g2", "stop")
    kinds = [item.kind if not isinstance(item, InstanceClosed) else "closed"
             for item in stream]
    assert kinds == ["bootstrap", "truncated", "closed", "truncated", "closed"]
    assert stream[2] == InstanceClosed("g1", "unknown", "unknown:compact_state_divergence",
                                       {"bootstrap": 1, "terminal": 0, "truncated": 1,
                                        "rejected": 0})
    assert stream[4] == InstanceClosed("g2", "stop", None,
                                       {"bootstrap": 0, "terminal": 0, "truncated": 1,
                                        "rejected": 0})
    assert h.assembler.report()["instances_closed"] == 2
    h.close("g2", "stop")  # a late close of a closed instance emits nothing
    assert len(stream) == 5


def test_instance_closed_follows_even_when_the_labeler_breaks():
    def broken(summary: Any) -> dict:
        raise RuntimeError("labeler bug")

    h, stream = _closing_harness(labeler=broken)
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    _, row = h.step("g1", outcome=False)
    h.outcome(row, terminal={"result": "victory"})
    with pytest.raises(RuntimeError):
        h.close("g1", "terminal", row["step_index"])
    assert [type(item).__name__ for item in stream] == ["SealedSegment", "InstanceClosed"]
    assert stream[1].close_reason == "terminal" and stream[1].segments["truncated"] == 1


@pytest.mark.parametrize("change", [
    {"schema": "other"}, {"extra": 1}, {"failure": 3}, {"close_reason": ""},
    {"segments": {"bootstrap": 1}},
    {"segments": {"bootstrap": -1, "terminal": 0, "truncated": 0, "rejected": 0}},
])
def test_instance_closed_message_is_checked_exactly(change):
    message = {"schema": "stpd/rl-fast-instance-closed-1", "assembler_id": "a.1",
               "game_instance_id": "g1", "close_reason": "terminal", "failure": None,
               "segments": {"bootstrap": 1, "terminal": 1, "truncated": 0, "rejected": 0}}
    assert check_instance_closed(dict(message)) == message
    with pytest.raises(HandoffError, match="instance_closed_schema"):
        check_instance_closed({**message, **change})


def _mixed_receiver(connection: Any, count: int, out: Any) -> None:
    for _ in range(count):
        kind, value = receive_handoff(connection)
        if kind == "segment":
            message, header, rows = value
            out.put((kind, header["game_instance_id"], message["handoff_sequence"]))
        else:
            out.put((kind, value["game_instance_id"], value["close_reason"],
                     value["failure"], value["segments"]["bootstrap"]))


def test_instance_closed_reaches_sgd_after_the_instance_segments(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    out = context.Queue()
    process = context.Process(target=_mixed_receiver, args=(child, 4, out))
    process.start()
    h = Harness()
    instance = h.assembler.instance_id
    handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=instance),
                             ShmSegmentSender(parent, assembler_id=instance))
    h.assembler.emit, h.assembler.on_closed = handoff.submit, handoff.submit_closed
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    for _ in range(128):
        h.step("g1")
    _, row = h.step("g1", outcome=False)
    h.outcome(row, "unknown", reason="compact_state_divergence")
    h.close("g1", "unknown", row["step_index"])
    h.open("g2")
    h.step("g2")
    h.close("g2", "stop")
    handoff.close()
    received = [out.get(timeout=60) for _ in range(4)]
    process.join(timeout=60)
    assert process.exitcode == 0
    assert received == [("segment", "g1", 1), ("segment", "g1", 2),
                        ("instance_closed", "g1", "unknown",
                         "unknown:compact_state_divergence", 2),
                        ("instance_closed", "g2", "stop", None, 0)]
    assert handoff.counters["instances_closed_sent"] == 2


def test_instance_closed_without_a_sender_is_counted_not_sent(tmp_path: Path):
    h = Harness()
    handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=h.assembler.instance_id))
    h.assembler.emit, h.assembler.on_closed = handoff.submit, handoff.submit_closed
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    h.step("g1")
    h.close("g1", "stop")
    handoff.close()
    assert handoff.counters["instances_closed_not_sent"] == 1
    assert handoff.counters["instances_closed_sent"] == 0
