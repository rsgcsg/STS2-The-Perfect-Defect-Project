"""WP5: inference-side 64-row segments, mu binding/tail/eviction, truncation, hand-off."""
from __future__ import annotations

import contextlib
import hashlib
import json
import multiprocessing
import time
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
    FORMAT,
    FORMAT_2,
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
            "single_choice": single_choice, "start": start or {"kind": "new_run"},
            "ascension": 0, **extra})
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
                                "instances_closed_sent": 0, "instances_closed_not_sent": 0,
                                "labels_sent": 0, "labels_ineligible": 0,
                                "labels_failed": 0}
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
    # An original is never overwritten. With parallel writes the refusal can already
    # reach this submit (the segment was queued first); close() raises it in any case.
    with contextlib.suppress(HandoffError):
        handoff.submit(segment)
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


def test_close_on_another_thread_never_overtakes_a_sealed_segment():
    """h100i 11-12 (`fast_segment_after_instance_closed`): the scoring thread's
    finish_row seals the full segment (its successor row is scored) and hands it on
    outside the state lock; the link thread's episode_close (not_started) truncates
    the successor segment and queues the closed signal. The closed signal must not
    reach the hand-off queue before the segment sealed first."""
    import threading

    stream: list[Any] = []
    in_emit, release = threading.Event(), threading.Event()

    def emit(item: Any) -> None:
        if getattr(item, "kind", None) == "bootstrap":
            in_emit.set()
            assert release.wait(10)  # the scoring thread is slow to queue it
        stream.append(item)

    h = Harness()
    h.assembler.emit, h.assembler.on_closed = emit, stream.append
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    for _ in range(64):
        h.step("g1")
    row, data = h.row("g1")
    plan = h.assembler.begin_row(row, data)
    errors: list[BaseException] = []

    def run(target: Any) -> threading.Thread:
        def body() -> None:
            try:
                target()
            except BaseException as error:  # noqa: BLE001 - asserted below
                errors.append(error)
        thread = threading.Thread(target=body, daemon=True)
        thread.start()
        return thread

    scorer = run(lambda: h.assembler.finish_row(plan, h.scored(plan, row), values(),
                                                values()))
    assert in_emit.wait(10)
    link = run(lambda: h.close("g1", "not_started", row["step_index"]))
    link.join(0.3)  # without the emit order lock the close completes here
    release.set()
    scorer.join(10)
    link.join(10)
    assert not errors and not scorer.is_alive() and not link.is_alive()
    kinds = [item.kind if not isinstance(item, InstanceClosed) else "closed"
             for item in stream]
    assert kinds == ["bootstrap", "truncated", "closed"]
    assert stream[2].segments == {"bootstrap": 1, "terminal": 0, "truncated": 1,
                                  "rejected": 0}


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


def test_a_later_close_failure_never_hides_the_first():
    """Labeler, hand-off and closed signal all fail: the labeler's error is the one raised,
    and the hand-off and closed signal were still tried after it."""
    def broken(summary: Any) -> dict:
        raise RuntimeError("labeler bug")

    tried: list[str] = []

    def emit(item: Any) -> None:
        tried.append("emit")
        raise OSError("hand-off down")

    def on_closed(item: Any) -> None:
        tried.append("closed")
        raise ValueError("closed signal down")

    h = Harness(labeler=broken)
    h.assembler.emit, h.assembler.on_closed = emit, on_closed
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    _, row = h.step("g1", outcome=False)
    h.outcome(row, terminal={"result": "victory"})
    with pytest.raises(RuntimeError, match="labeler bug"):
        h.close("g1", "terminal", row["step_index"])
    assert tried == ["emit", "closed"]


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


def _mixed_receiver(connection: Any, count: int, out: Any, delay: float = 0.0) -> None:
    for _ in range(count):
        time.sleep(delay)  # a slow reader: blocks are still in flight when close() runs
        kind, value = receive_handoff(connection)
        if kind == "segment":
            message, header, rows = value
            out.put((kind, header["game_instance_id"], message["handoff_sequence"]))
        else:
            out.put((kind, value["game_instance_id"], value["close_reason"],
                     value["failure"], value["segments"]["bootstrap"]))


@pytest.mark.parametrize("delay", [0.0, 0.3])
def test_instance_closed_reaches_sgd_after_the_instance_segments(tmp_path: Path, delay: float):
    # delay > 0: close() must wait for every ack, not one, before unlinking (乙's review).
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    out = context.Queue()
    process = context.Process(target=_mixed_receiver, args=(child, 4, out, delay))
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


# ---------------------------------------------------------------- labels (WP8)
def _carry_all(segments: list[Any]) -> tuple[dict, bytes]:
    """A stand-in for `fast_source.build_instance_labels`: every segment, 8-byte aligned."""
    parts, entries, cursor = [], [], 0
    for segment in sorted(segments, key=lambda s: s.header["segment_index"]):
        entries.append({"segment_id": segment.segment_id, "sha256": segment.sha256,
                        "offset": cursor, "size": len(segment.data)})
        parts.append(segment.data + bytes(-len(segment.data) % 8))
        cursor += len(parts[-1])
    data = b"".join(parts)
    first = segments[0].header
    return ({"schema": "stpd/rl-fast-instance-labels-1",
             "assembler_id": first["assembler_instance"],
             "game_instance_id": first["game_instance_id"], "segments": entries,
             "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}, data)


def _labels_receiver(connection: Any, count: int, out: Any) -> None:
    for _ in range(count):
        kind, value = receive_handoff(connection)
        if kind == "segment":
            message, header, _rows = value
            out.put((kind, header["game_instance_id"], message["handoff_sequence"]))
        elif kind == "labels":
            message, data = value
            out.put((kind, message["game_instance_id"], message["handoff_sequence"],
                     [(e["segment_id"], e["offset"]) for e in message["segments"]],
                     hashlib.sha256(data).hexdigest()))
        else:
            out.put((kind, value["game_instance_id"], value["close_reason"]))


def _terminal_game(h: Harness, game: str, rows: int) -> None:
    h.open(game)
    for _ in range(rows - 1):
        h.step(game)
    _, row = h.step(game, outcome=False, floor=5)
    h.outcome(row, terminal={"result": "defeat"})
    h.close(game, "terminal", row["step_index"])


def test_labels_go_between_the_last_segment_and_instance_closed(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    out = context.Queue()
    # A failure here must turn red, not hang: daemon reader, short ack wait, terminate.
    process = context.Process(target=_labels_receiver, args=(child, 5, out), daemon=True)
    process.start()
    try:
        h = Harness(lambda summary: {"class": 5, "reward": 0.0054})
        instance = h.assembler.instance_id
        handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=instance),
                                 ShmSegmentSender(parent, assembler_id=instance, ack_timeout=10),
                                 labeler=_carry_all)
        h.assembler.emit, h.assembler.on_closed = handoff.submit, handoff.submit_closed
        h.assembler.offer_mu(mu(1))
        _terminal_game(h, "g1", 65)  # a bootstrap and a terminal segment
        h.open("g2")
        h.step("g2")
        h.close("g2", "stop")  # its only segment is truncated: no labels, never in part
        handoff.close()
        received = [out.get(timeout=30) for _ in range(5)]
        process.join(timeout=30)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
    originals = [tmp_path / "bootstrap" / f"{instance}.1.stfs",
                 tmp_path / "terminal" / f"{instance}.2.stfs"]
    first = originals[0].read_bytes()
    carried = first + bytes(-len(first) % 8) + originals[1].read_bytes()
    carried += bytes(-len(carried) % 8)
    assert received == [
        ("segment", "g1", 1), ("segment", "g1", 2),
        ("labels", "g1", 3, [(f"{instance}.1", 0), (f"{instance}.2", len(first)
                                                     + (-len(first) % 8))],
         hashlib.sha256(carried).hexdigest()),
        ("instance_closed", "g1", "terminal"),
        ("instance_closed", "g2", "stop")]
    assert handoff.counters["labels_sent"] == 1 and handoff.counters["labels_ineligible"] == 1
    assert handoff.counters["instances_closed_sent"] == 2 and not handoff._games


@pytest.mark.parametrize("ending", ["unknown", "generation"])
def test_failed_or_split_games_get_no_labels(tmp_path: Path, ending: str):
    seen: list[Any] = []
    h = Harness(lambda summary: {"class": 5, "reward": 0.0054})
    instance = h.assembler.instance_id
    sent: list[Any] = []

    def labeler(segments: list[Any]) -> tuple[dict, bytes]:
        seen.append(segments)
        return _carry_all(segments)

    class Recorder:  # a sender that only records (no SGD needed here)
        assembler_id = instance

        def send(self, segment: Any, path: Path) -> int:
            sent.append(segment.segment_id)
            return len(sent)

        def send_message(self, fields: dict, data: bytes) -> int:
            sent.append("labels")
            return len(sent)

        def send_closed(self, closed: Any) -> None:
            sent.append(("closed", closed.game_instance_id))

        def close(self) -> None:
            pass

    handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=instance),
                             Recorder(),  # type: ignore[arg-type]
                             labeler=labeler)
    h.assembler.emit, h.assembler.on_closed = handoff.submit, handoff.submit_closed
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    for _ in range(64):
        h.step("g1")
    if ending == "unknown":
        _, row = h.step("g1", outcome=False)
        h.outcome(row, "unknown", reason="delivery_unknown")
        h.close("g1", "unknown", row["step_index"])
    else:
        h.assembler.offer_mu(mu(2, generation="gen-2"))
        _, row = h.step("g1", outcome=False, floor=5)
        h.outcome(row, terminal={"result": "defeat"})
        h.close("g1", "terminal", row["step_index"])
    handoff.close()
    assert "labels" not in sent and not seen
    assert sent[-1] == ("closed", "g1")
    assert handoff.counters["labels_sent"] == 0 and handoff.counters["labels_ineligible"] == 1


def test_a_changed_original_stops_the_labels_loudly(tmp_path: Path):
    h = Harness(lambda summary: {"class": 5, "reward": 0.0054})
    instance = h.assembler.instance_id
    parent, child = multiprocessing.Pipe()
    sender = ShmSegmentSender(parent, assembler_id=instance, ack_timeout=2)

    def corrupt(segment: Any, path: Path) -> None:
        if segment.header["segment_index"] == 0:
            path.write_bytes(b"changed")

    handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=instance), sender,
                             on_written=corrupt, labeler=_carry_all)
    h.assembler.emit, h.assembler.on_closed = handoff.submit, handoff.submit_closed
    h.assembler.offer_mu(mu(1))
    try:
        _terminal_game(h, "g1", 65)
        with pytest.raises(HandoffError, match="segment_handoff_failed") as raised:
            handoff.close()
        assert str(raised.value.__cause__) == "labels_original_changed"
        assert handoff.counters["labels_failed"] == 1
        assert handoff.counters["instances_closed_not_sent"] == 1
    finally:
        for block, _ in sender.inflight.values():
            block.close()
            block.unlink()
        child.close()


def test_send_message_checks_its_own_binding(tmp_path: Path):
    parent, _child = multiprocessing.Pipe()
    sender = ShmSegmentSender(parent, assembler_id="node-a.1")
    data = b"x" * 9
    good = {"schema": "s", "assembler_id": "node-a.1", "size": 9,
            "sha256": hashlib.sha256(data).hexdigest()}
    for broken, code in [({"assembler_id": "node-b.1"}, "message_from_another_assembler"),
                         ({"size": 8}, "handoff_message_binding"),
                         ({"sha256": "0" * 64}, "handoff_message_binding"),
                         ({"shm_name": "x"}, "handoff_message_binding")]:
        with pytest.raises(HandoffError, match=code):
            sender.send_message({**good, **broken}, data)
    assert sender.sequence == 0 and not sender.inflight


class _Recorder:
    """A sender that only records (no SGD process needed)."""

    def __init__(self, assembler_id: str) -> None:
        self.assembler_id, self.sent = assembler_id, []

    def send(self, segment: Any, path: Path) -> int:
        self.sent.append(segment.segment_id)
        return len(self.sent)

    def send_message(self, fields: dict, data: bytes) -> int:
        self.sent.append("labels")
        return len(self.sent)

    def send_closed(self, closed: Any) -> None:
        self.sent.append(("closed", closed.game_instance_id))

    def close(self) -> None:
        pass


def _clean_game() -> tuple[str, list[SealedSegment], InstanceClosed]:
    """One cleanly closed game: a bootstrap and a terminal segment, and its close."""
    stream: list[Any] = []
    h = Harness(lambda summary: {"class": 5, "reward": 0.0054})
    h.assembler.emit, h.assembler.on_closed = stream.append, stream.append
    h.assembler.offer_mu(mu(1))
    _terminal_game(h, "g1", 65)
    *segments, closed = stream
    assert [s.kind for s in segments] == ["bootstrap", "terminal"]
    assert closed == InstanceClosed("g1", "terminal", None, {"bootstrap": 1, "terminal": 1,
                                                             "truncated": 0, "rejected": 0})
    return h.assembler.instance_id, segments, closed


def _other_generation(segment: SealedSegment) -> SealedSegment:
    header = dict(segment.header, mu=dict(segment.header["mu"], training_generation="gen-2"))
    return SealedSegment(header, segment.data)


def _counts(**change: int) -> dict[str, int]:
    return {**{"bootstrap": 1, "terminal": 1, "truncated": 0, "rejected": 0}, **change}


# Each case breaks exactly one condition of `_labelable`; everything else is clean.
@pytest.mark.parametrize("case", [
    "clean", "env_crash", "unknown", "failure", "truncated", "rejected", "count",
    "generation"])
def test_each_labels_condition_alone_withholds_labels(tmp_path: Path, case: str):
    instance, segments, closed = _clean_game()
    if case == "generation":  # changed between two sealed segments: nothing truncated
        segments = [segments[0], _other_generation(segments[1])]
    closed = {
        "clean": closed, "generation": closed,
        "env_crash": InstanceClosed("g1", "env_crash", None, _counts()),
        "unknown": InstanceClosed("g1", "unknown", None, _counts()),
        "failure": InstanceClosed("g1", "terminal", "unknown:delivery_unknown", _counts()),
        "truncated": InstanceClosed("g1", "terminal", None, _counts(truncated=1)),
        "rejected": InstanceClosed("g1", "terminal", None, _counts(rejected=1)),
        "count": InstanceClosed("g1", "terminal", None, _counts(bootstrap=2)),
    }[case]
    calls: list[Any] = []

    def labeler(kept: list[Any]) -> tuple[dict, bytes]:
        calls.append(kept)
        return _carry_all(kept)

    sender = _Recorder(instance)
    handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=instance),
                             sender,  # type: ignore[arg-type]
                             labeler=labeler)
    for segment in segments:
        handoff.submit(segment)
    handoff.submit_closed(closed)
    handoff.close()  # never stops the chain: the game is only not labelled
    assert sender.sent[-1] == ("closed", "g1")
    if case == "clean":
        assert sender.sent[-2] == "labels" and len(calls) == 1
        assert handoff.counters["labels_sent"] == 1
    else:
        assert "labels" not in sender.sent and not calls
        assert handoff.counters["labels_ineligible"] == 1
        assert handoff.counters["labels_sent"] == handoff.counters["labels_failed"] == 0


def test_a_missing_original_is_a_typed_handoff_error(tmp_path: Path):
    instance, segments, closed = _clean_game()

    def remove(segment: Any, path: Path) -> None:
        path.unlink()

    handoff = SegmentHandoff(DurableSegmentWriter(tmp_path, instance_id=instance),
                             _Recorder(instance),  # type: ignore[arg-type]
                             on_written=remove, labeler=_carry_all)
    for segment in segments:
        handoff.submit(segment)
    handoff.submit_closed(closed)
    with pytest.raises(HandoffError, match="segment_handoff_failed") as raised:
        handoff.close()
    assert str(raised.value.__cause__) == "labels_original_unreadable"
    assert handoff.counters["labels_failed"] == 1


# -- T3: columnar segment format (stpd/rl-fast-segment-2) ------------------------------
FORMAT_1_GOLDEN = "dca2b69270f5efea5037b737b77a1a02c9a636946e483938ddb21aba7b156e5d"


def _mixed_games(segment_format: str, **options: Any) -> Harness:
    """Every seal kind, omit/forced steps, a failed outcome and a stop, in one harness."""
    def labeler(summary: Any) -> dict | None:
        return {"class": 5, "reward": 0.0054} if summary["game_instance_id"] == "t" else None

    if segment_format != FORMAT:  # format 1 = the constructor as it was before T3
        options["segment_format"] = segment_format
    h = Harness(labeler, **options)
    h.assembler.instance_id = "node-a.000000000001"
    h.assembler.offer_mu(mu(1))
    h.open("b", "omit")  # bootstrap + an open tail segment closed by stop
    for index in range(66):
        _, row = h.step("b", outcome=False, floor=1 + index // 30, combat=index % 3 != 0)
        h.outcome(row, forced_steps=index % 3, combat_end=[{"won": True}] if index == 5 else [])
    h.close("b", "stop")
    h.open("t")  # natural terminal, labelled
    h.step("t")
    _, row = h.step("t", outcome=False, floor=5)
    h.outcome(row, terminal={"result": "defeat"})
    h.close("t", "terminal", row["step_index"])
    h.open("u")  # unknown outcome -> truncated
    h.step("u")
    _, row = h.step("u", outcome=False)
    h.outcome(row, "unknown", reason="delivery_unknown")
    h.close("u", "unknown", row["step_index"])
    return h


def test_columnar_segments_hold_the_same_segment_for_every_seal_kind():
    old, new = _mixed_games(FORMAT), _mixed_games(FORMAT_2, verify_every=1)
    assert [s.kind for s in old.sealed] == [s.kind for s in new.sealed] == [
        "bootstrap", "truncated", "terminal", "truncated"]
    for left, right in zip(old.sealed, new.sealed, strict=True):
        assert right.data[:8] == b"STPDFS02" and left.data[:8] == b"STPDFS01"
        old_header, old_rows = decode_segment(left.data, expected_sha256=left.sha256)
        new_header, new_rows = decode_segment(right.data, expected_sha256=right.sha256)
        assert new_header == right.header == {**old_header, "format": FORMAT_2}
        assert new_rows == old_rows
        assert decode_segment(right.data, header_only=True) == (new_header, [])
    assert new.assembler.report()["segments_verified"] == 4
    assert old.assembler.report()["segments"] == new.assembler.report()["segments"]
    # The format-1 path is the old one: these are the bytes the pre-T3 assembler
    # (int 1229b3e7) seals for this harness.
    assert hashlib.sha256(b"".join(s.data for s in old.sealed)).hexdigest() == FORMAT_1_GOLDEN


def test_format_3_is_format_2_plus_the_game_ascension():
    """A0 under format 3: the same logical segment as format 2 plus `ascension: 0`."""
    from stpd.rl.fast_segment import FORMAT_3, segment_ascension
    two, three = _mixed_games(FORMAT_2, verify_every=1), _mixed_games(FORMAT_3, verify_every=1)
    for left, right in zip(two.sealed, three.sealed, strict=True):
        assert right.data[:8] == b"STPDFS03"
        old_header, old_rows = decode_segment(left.data)
        new_header, new_rows = decode_segment(right.data)
        assert new_header == {**old_header, "format": FORMAT_3, "ascension": 0}
        assert new_rows == old_rows
        assert segment_ascension(old_header) == segment_ascension(new_header) == 0
    assert three.assembler.report()["segments_verified"] == 4
    assert three.assembler.report()["by_ascension"]["0"]["episodes"] == 3


def test_ascension_is_required_and_above_a0_needs_format_3():
    from stpd.rl.fast_segment import FORMAT_3, segment_ascension
    h = Harness(segment_format=FORMAT_2)
    h.assembler.offer_mu(mu(1))
    for bad in ({"ascension": None}, {"ascension": "5"}, {"ascension": 11}):
        with pytest.raises(AssemblyError, match="invalid_episode_open"):
            h.open("g", **bad)
    with pytest.raises(AssemblyError, match="ascension_needs_segment_format_3"):
        h.open("g", ascension=5)
    labels: list[dict] = []

    def labeler(summary: Any) -> dict:
        labels.append(summary)
        return {"class": 5, "reward": 0.1}

    h = Harness(labeler, segment_format=FORMAT_3, verify_every=1)
    h.assembler.offer_mu(mu(1))
    h.open("t", ascension=5)
    h.step("t")
    _, row = h.step("t", outcome=False, floor=5)
    h.outcome(row, terminal={"result": "defeat"})
    h.close("t", "terminal", row["step_index"])
    assert labels[0]["ascension"] == 5
    header, _ = decode_segment(h.sealed[-1].data)
    assert header["ascension"] == 5 and segment_ascension(header) == 5
    assert h.assembler.report()["by_ascension"]["5"]["segments"] == {"terminal": 1}
    # Formats 1/2 are A0 history, never a level of their own; format 3 must name one.
    for bad in ({"format": FORMAT_2, "ascension": 3}, {"format": FORMAT_3},
                {"format": FORMAT_3, "ascension": 11}, {"format": FORMAT_3, "ascension": True}):
        with pytest.raises(SegmentFormatError, match="segment_ascension"):
            segment_ascension(bad)


def test_columnar_verify_difference_keeps_the_original_and_fails_loudly(monkeypatch):
    import stpd.rl.fast_assembly as assembly

    real = assembly.encode_segment_columns

    def drifting(header: dict, rows: list, **kwargs: Any) -> Any:
        changed = dict(header, rows=[dict(r) for r in header["rows"]])
        changed["rows"][0]["consumption_id"] = "drifted"
        return real(changed, rows, **kwargs)

    monkeypatch.setattr(assembly, "encode_segment_columns", drifting)
    h = Harness(segment_format=FORMAT_2, verify_every=1)
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    h.step("g1")
    with pytest.raises(AssemblyError, match="segment_rejected:segment_columns_verify_mismatch"):
        h.close("g1", "stop")
    [kept] = h.sealed
    assert kept.kind == "rejected" and not kept.trainable
    assert h.assembler.report()["segments"]["rejected"] == 1


def test_columnar_verify_is_json_type_strict(monkeypatch):
    """An int q (accepted, as in format 1) is stored and returned as floats; a
    change of JSON type alone (step_index 0 -> 0.0) is a verify difference."""
    h = Harness(segment_format=FORMAT_2, verify_every=1)
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    row, data = h.row("g1")
    plan = h.assembler.begin_row(row, data)
    h.assembler.finish_row(plan, h.scored(plan, row, behavior_q=[0, 1], selected_q=1),
                           values(row["floor"]))
    h.outcome(row)
    h.close("g1", "stop")
    [sealed] = h.sealed
    [stored] = decode_segment(sealed.data)[0]["rows"]
    assert stored["behavior_q"] == [0.0, 1.0] and type(stored["selected_q"]) is float
    assert sealed.header["rows"][0]["behavior_q"] == [0.0, 1.0]

    import stpd.rl.fast_assembly as assembly

    real = assembly.encode_segment_columns

    def retyped(header: dict, rows: list, **kwargs: Any) -> Any:
        sealed = real(header, rows, **kwargs)
        sealed.header["rows"][0]["step_index"] = float(sealed.header["rows"][0]["step_index"])
        return sealed

    monkeypatch.setattr(assembly, "encode_segment_columns", retyped)
    h.open("g2")
    h.step("g2")
    with pytest.raises(AssemblyError, match="segment_rejected:segment_columns_verify_mismatch"):
        h.close("g2", "stop")


def test_columnar_encoder_refuses_values_format_1_would_coerce():
    h = Harness(segment_format=FORMAT_2)
    h.assembler.offer_mu(mu(1))
    h.open("g1")
    row, data = h.row("g1")
    row["in_combat"] = 1  # truthy, not a bool: format 2 stores a bool
    plan = h.assembler.begin_row(row, data)
    h.assembler.finish_row(plan, h.scored(plan, row), values(row["floor"]))
    with pytest.raises(AssemblyError, match="segment_rejected:segment_column_applied"):
        h.close("g1", "stop")
    assert h.sealed[0].kind == "rejected"


def test_columnar_segment_rejects_tampering():
    h = _mixed_games(FORMAT_2)
    segment = h.sealed[0]
    header_length = int.from_bytes(segment.data[8:16], "little")
    columns = 16 + header_length + (-header_length % 8)
    # The segment digest binds every byte (an int column changed in place still
    # decodes, as a changed JSON number does in format 1); the rest is structural.
    data = bytearray(segment.data)
    data[columns + 3] ^= 1
    with pytest.raises(SegmentFormatError, match="segment_digest_mismatch"):
        decode_segment(bytes(data), expected_sha256=segment.sha256)
    in_combat = columns + 6 * 64 * 8  # after the six int64 columns of 64 rows
    for position, value in ((in_combat, 2), (len(segment.data) - 9, None)):
        data = bytearray(segment.data)
        data[position] = value if value is not None else data[position] ^ 1
        with pytest.raises(SegmentFormatError):
            decode_segment(bytes(data))
    stored = json.loads(segment.data[16:16 + header_length])
    for change in ({"row_count": 63}, {"columns": stored["columns"][:-1]},
                   {"format": FORMAT}, {"text": {**stored["text"], "row_sha256": []}}):
        encoded = canonical_json({**stored, **change})
        body = segment.data[columns:]
        data = (b"STPDFS02" + len(encoded).to_bytes(8, "little") + encoded
                + b"\0" * (-len(encoded) % 8) + body)
        with pytest.raises(SegmentFormatError):
            decode_segment(data)
    with pytest.raises(SegmentFormatError, match="segment_magic_or_size"):
        decode_segment(b"STPDFS04" + segment.data[8:])


@pytest.mark.parametrize("options", [{"segment_format": "stpd/rl-fast-segment-4"},
                                     {"verify_every": 0}, {"verify_every": True}])
def test_segment_format_switch_values_are_checked(options: dict):
    with pytest.raises(AssemblyError, match="invalid_segment_format"):
        Harness(**options)
