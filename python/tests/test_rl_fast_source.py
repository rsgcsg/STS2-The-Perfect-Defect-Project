"""WP8 learner side of fast_chain: segment -> PreparedSource, admission, exclusion, hand-off."""
from __future__ import annotations

import hashlib
import multiprocessing
import random
import sys
from pathlib import Path
from typing import Any

import pytest
import torch
from test_p1_input_v2 import public_state

from spireagent.rl_contracts import HotPayloadRef, RunSequence
from stpd.evaluation.suites import derive_game_seed, seed_membership
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.input import V2_INPUT_SPEC
from stpd.p1.rows_encoder_wire import encode_row
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl import batch_preparation as bp
from stpd.rl.critic import held_out
from stpd.rl.fast_assembly import FastSegmentAssembler, MuHandoff
from stpd.rl.fast_handoff import HandoffError, ShmSegmentSender
from stpd.rl.fast_segment import FORMAT, FORMAT_2, SealedSegment, SegmentFormatError
from stpd.rl.fast_source import (
    CLOSED_SCHEMA,
    FastIntakeError,
    FastSegmentIntake,
    ack,
    decode_fast_packet,
    fast_envelope,
    read_handoff,
)
from stpd.rl.heads import OUTCOME_CLASSES, categorical_lambda_targets, class_reward_values
from stpd.rl.prepared_batch import BatchMember, PrepareRequest

SAMPLING = {"mode": "temperature_uniform", "seed": "draw-seed", "arm_id": "control",
            "classes": {"combat": {"temperature": 1.0, "uniform_mix": 0.0},
                        "noncombat": {"temperature": 1.0, "uniform_mix": 0.0}}}
COMPILER = FrameCompiler(public_vocabulary(V2_INPUT_SPEC))
FRAME = COMPILER.compile(public_state())


def train_seed(index: int = 1) -> str:
    return next(seed for i in range(index, index + 200)
                if not held_out(seed := derive_game_seed("wp8-fast", i))
                and not seed_membership(seed))


def held_seed() -> str:
    return next(seed for i in range(1, 2000)
                if held_out(seed := derive_game_seed("wp8-fast-held", i)))


def mu(index: int, generation: str = "gen-1") -> MuHandoff:
    return MuHandoff(hashlib.sha256(f"mu-{index}".encode()).hexdigest(), generation, index, index)


def values(floor: int = 1, shift: float = 0.0) -> list[float]:
    out = [0.0] * OUTCOME_CLASSES
    out[floor] = 0.5 + shift
    out[49] = 0.5 - shift
    return out


class Game:
    """Real assembler, real rows-v2 bytes, real compiler identity."""

    def __init__(self, generation: str = "gen-1", terminal_class: int = 3,
                 compiler: FrameCompiler = COMPILER, assembler_id: str = "node-a",
                 handoff: MuHandoff | None = None, segment_format: str = FORMAT,
                 instance_id: str | None = None) -> None:
        self.sealed: list[Any] = []
        self.row = encode_row(compiler.compile(public_state()))
        self.width = len(compiler.compile(public_state()).choices)
        self.assembler = FastSegmentAssembler(
            assembler_id=assembler_id, compiler_identity=compiler.identity,
            emit=self.sealed.append,
            terminal_labeler=lambda _summary: {
                "class": terminal_class, "reward": float(class_reward_values()[terminal_class])},
            segment_format=segment_format, verify_every=1)
        if instance_id is not None:  # format comparisons: the same segment ids
            self.assembler.instance_id = instance_id
        self.assembler.offer_mu(handoff or mu(1, generation))
        self.request = 0
        self.steps: dict[str, int] = {}

    def open(self, game: str) -> None:
        self.assembler.open_episode({
            "schema": "stpd/rl-inference-3", "kind": "episode_open", "request_id": 1,
            "game_instance_id": game, "actor_id": "actor-1", "sampling": SAMPLING,
            "single_choice": "score", "start": {"kind": "new_run"}})
        self.steps[game] = 0

    def step(self, game: str, *, terminal: bool = False, status: str = "completed",
             reason: str | None = None, in_combat: bool = True, hp: int = 50) -> None:
        self.request += 1
        step = self.steps[game]
        self.steps[game] += 1
        n, raw = self.width, self.row
        row = {"request_id": self.request, "game_instance_id": game, "step_index": step,
               "consumption_id": f"c-{self.request}", "catalog_len": n,
               "catalog_sha256": "c" * 64, "observation_sha256": "d" * 64,
               "in_combat": in_combat,
               "floor": 1, "payload": {"offset": 0, "length": len(raw),
                                       "row_sha256": hashlib.sha256(raw).hexdigest()}}
        plan = self.assembler.begin_row(row, raw)
        q = [0.0] * n
        q[0], q[-1] = 0.25, 0.75
        if n == 1:
            q = [1.0]
        scored = {"request_id": row["request_id"], "game_instance_id": game,
                  "catalog_sha256": row["catalog_sha256"], "status": "scored",
                  "mu_snapshot_sha256": plan.mu.snapshot_sha256, "segment_id": plan.segment_id,
                  "row_in_segment": plan.row_in_segment, "selected_index": n - 1,
                  "behavior_q": q, "selected_q": q[-1],
                  "applied": {"arm_id": "control",
                              "decision_class": "combat" if in_combat else "noncombat",
                              "temperature": 1.0, "uniform_mix": 0.0}}
        shift = 0.01 * (step % 7)
        self.assembler.finish_row(plan, scored, values(1, shift),
                                  values(1, 0.2) if plan.tail is not None else None)
        message = {"schema": "stpd/rl-inference-3", "kind": "step_outcome",
                   "row_request_id": row["request_id"], "game_instance_id": game,
                   "step_index": step, "status": status}
        if status == "completed":
            message.update(receipt_sha256="e" * 64,
                           terminal={"result": "defeat"} if terminal else None,
                           public_facts={"act": 1, "floor": 1, "hp": hp, "max_hp": 75})
        else:
            message["reason"] = reason
        self.assembler.step_outcome(message)

    def close(self, game: str, reason: str) -> None:
        self.assembler.close_episode({"schema": "stpd/rl-inference-3", "kind": "episode_close",
                                      "request_id": 1, "game_instance_id": game,
                                      "reason": reason, "last_step_index": None})


def bootstrap_and_terminal(game: str = "g1", generation: str = "gen-1",
                           segment_format: str = FORMAT,
                           instance_id: str | None = None) -> list[SealedSegment]:
    g = Game(generation, segment_format=segment_format, instance_id=instance_id)
    g.open(game)
    for _ in range(64 + 5):
        g.step(game)
    g.step(game, terminal=True)
    g.close(game, "terminal")
    assert [s.kind for s in g.sealed] == ["bootstrap", "terminal"]
    return g.sealed


def message(segment: SealedSegment, sequence: int = 1) -> dict[str, Any]:
    header = segment.header
    return {"schema": "stpd/rl-fast-segment-handoff-1", "handoff_sequence": sequence,
            "assembler_id": header["assembler_instance"], "segment_id": segment.segment_id,
            "training_generation": header["mu"]["training_generation"],
            "mu_snapshot_sha256": header["mu"]["snapshot_sha256"], "rows": len(header["rows"]),
            "shm_name": "unused", "size": len(segment.data), "sha256": segment.sha256,
            "path": "unused"}


def attest(game: str, seed: str | None = None, generation: str = "gen-1") -> dict[str, Any]:
    return {"actor": "actor-1", "intent_id": "intent-" + game, "seed": seed or train_seed(),
            "game_instance_id": game, "training_split": "TRAIN", "run": "run-1",
            "training_generation": generation}


def envelope_for(segment: SealedSegment, game: str = "g1") -> dict[str, Any]:
    a = attest(game)
    return fast_envelope(segment.header, size=len(segment.data), digest=segment.sha256,
                         run="run-1", attestation={k: a[k] for k in (
                             "actor", "intent_id", "seed", "training_split")}, gae_lambda=0.99)


@pytest.mark.parametrize("segment_format", [FORMAT, FORMAT_2])
def test_fast_segment_becomes_the_same_prepared_source_targets_as_the_old_formula(
        segment_format):
    for segment in bootstrap_and_terminal(segment_format=segment_format):
        source, header = decode_fast_packet(segment.data, envelope=envelope_for(segment),
                                            compiler=COMPILER)
        rows = segment.header["rows"]
        p = torch.tensor([r["outcome_probabilities"] for r in rows])
        seal = segment.header["seal"]
        tail = (None if seal["terminal"] else
                torch.tensor(seal["bootstrap"]["outcome_probabilities"]))
        targets, advantages = categorical_lambda_targets(
            p, tail, terminal_class=seal["terminal"]["class"] if seal["terminal"] else None)
        assert torch.equal(source.probabilities, p)
        assert torch.equal(source.targets, targets)
        assert torch.equal(source.raw_advantages, advantages)
        assert source.actions == tuple(r["selected_index"] for r in rows)
        assert source.policy_probabilities[0] == tuple(rows[0]["behavior_q"])
        assert source.frames[0].choices == FRAME.choices
        assert source.row_refs[3]["key"] == f"g1:{rows[3]['step_index']}"
        assert source.labels["terminal"] == seal["terminal"]
        assert header["witness"]["identity"]["seed"] == envelope_for(segment)["seed"]
        bp.validate_training_source(header["witness"], frozenset())


def test_columnar_segments_become_the_same_prepared_sources():
    """T3: format 2 reads into the same PreparedSource as format 1, field by field."""
    old = bootstrap_and_terminal(instance_id="node-a.000000000001")
    new = bootstrap_and_terminal(segment_format=FORMAT_2, instance_id="node-a.000000000001")
    assert [s.data[:8] for s in new] == [b"STPDFS02"] * 2
    for left, right in zip(old, new, strict=True):
        assert {**left.header, "format": FORMAT_2} == right.header
        a = decode_fast_packet(left.data, envelope=envelope_for(left), compiler=COMPILER)[0]
        b = decode_fast_packet(right.data, envelope=envelope_for(right), compiler=COMPILER)[0]
        for name in ("segment_id", "run_id", "floors", "row_refs", "actions",
                     "policy_probabilities", "metadata", "labels"):
            assert getattr(a, name) == getattr(b, name), name
        for name in ("probabilities", "targets", "raw_advantages"):
            assert torch.equal(getattr(a, name), getattr(b, name)), name
        assert [f.choices for f in a.frames] == [f.choices for f in b.frames]


@pytest.mark.parametrize("segment_format", [FORMAT, FORMAT_2])
def test_fast_sources_assemble_into_a_rows_v2_batch(segment_format):
    segments = bootstrap_and_terminal(segment_format=segment_format)
    sources = [decode_fast_packet(s.data, envelope=envelope_for(s), compiler=COMPILER)[0]
               for s in segments]
    count = sum(len(s.frames) for s in sources)
    request = PrepareRequest(
        RunSequence("run-1", "gen-1", 1),
        tuple(BatchMember(s.segment_id, HotPayloadRef(f"run-1/gen-1/{i}", "f" * 64, 1),
                          "g1", len(s.header["rows"]), s.header["mu"]["snapshot_sha256"],
                          "intent-g1") for i, s in enumerate(segments)),
        (), (tuple(range(count)),), (), COMPILER.identity, random.Random(1).getstate(),
        128 * 1024**2)
    batch = bp.assemble(request, tuple(sources), microbatch_size=16, deduplicate_text=True,
                        prepared_layout="rows-v2")
    assert len(batch.metadata) == count


@pytest.mark.parametrize("segment_format", [FORMAT, FORMAT_2])
def test_tampered_or_rebound_fast_packet_is_refused(segment_format):
    segment = bootstrap_and_terminal(segment_format=segment_format)[0]
    envelope = envelope_for(segment)
    with pytest.raises(SegmentFormatError):
        decode_fast_packet(segment.data[:-8] + b"\1" * 8, envelope=envelope, compiler=COMPILER)
    with pytest.raises(FastIntakeError, match="binding"):
        decode_fast_packet(segment.data, envelope={**envelope, "run_id": "other"},
                           compiler=COMPILER)
    with pytest.raises(FastIntakeError, match="compiler"):
        decode_fast_packet(segment.data, envelope={**envelope, "compiler_identity": {"x": 1}},
                           compiler=COMPILER)


def published(handoff: MuHandoff) -> dict[str, Any]:
    return {"snapshot_sha256": handoff.snapshot_sha256,
            "training_generation": handoff.training_generation,
            "version": handoff.version, "updates": handoff.updates}


def intake(**kwargs: Any) -> FastSegmentIntake:
    value = FastSegmentIntake(run="run-1", generation="gen-1", **kwargs)
    value.publish_mu(published(mu(1)))  # the learner's own publication of the Game mu
    return value


def closed_for(sealed: list[SealedSegment], **change: Any) -> dict[str, Any]:
    header = sealed[0].header
    value = {"schema": CLOSED_SCHEMA, "assembler_id": header["assembler_instance"],
             "game_instance_id": header["game_instance_id"], "close_reason": "terminal",
             "failure": None, "segments": {"bootstrap": 1, "terminal": 1, "truncated": 0,
                                           "rejected": 0}}
    value.update(change)
    return value


def test_segments_before_attestation_are_buffered_then_admitted_in_order():
    segments = bootstrap_and_terminal()
    i = intake()
    assert not i.offer(message(segments[0]), segments[0].data).admit
    decision = i.attest(attest("g1"))
    assert decision.reply == {"admitted": True, "game_instance_id": "g1"}
    assert [s.segment_id for s, _ in decision.admit] == [segments[0].segment_id]
    envelope = decision.admit[0][1]
    assert envelope["episode"] == "intent-g1" and envelope["run_id"] == "g1"
    later = i.offer(message(segments[1], 2), segments[1].data)
    assert [s.segment_id for s, _ in later.admit] == [segments[1].segment_id]
    assert i.closed(closed_for(segments)).exclude_instance is None
    assert i.report()["open_instances"] == 0


def test_held_out_seed_and_old_generation_are_refused():
    i = intake()
    assert i.attest(attest("g1", seed=held_seed())).reply == {
        "admitted": False, "reason": "seed_held_out"}
    assert i.attest(attest("g2", generation="gen-0")).reply["reason"] == "generation_changed"
    old = bootstrap_and_terminal("g3", generation="gen-0")[0]
    i.attest(attest("g3"))
    assert not i.offer(message(old), old.data).admit
    assert i.report()["old_generation_dropped"] == 1


@pytest.mark.parametrize("change,reason", [
    ({"close_reason": "unknown", "failure": "unknown:delivery_unknown"}, "close:unknown"),
    ({"close_reason": "env_crash"}, "close:env_crash"),
    ({"close_reason": "not_started", "failure": "unknown:compact_state_divergence"},
     "failure:unknown:compact_state_divergence"),
])
def test_unknown_divergence_and_env_crash_exclude_the_instance(change, reason):
    segments = bootstrap_and_terminal()
    i = intake()
    i.attest(attest("g1"))
    i.offer(message(segments[0]), segments[0].data)
    decision = i.closed(closed_for(segments, **change,
                                   segments={"bootstrap": 1, "terminal": 0, "truncated": 1,
                                             "rejected": 0}))
    assert decision.exclude_instance == "g1"
    assert i.report()["excluded_by_reason"] == {reason: 1}


def test_not_started_close_keeps_already_sealed_segments():
    segments = bootstrap_and_terminal()
    i = intake()
    i.attest(attest("g1"))
    i.offer(message(segments[0]), segments[0].data)
    decision = i.closed(closed_for(segments, close_reason="not_started",
                                   failure="not_started:inference_timeout",
                                   segments={"bootstrap": 1, "terminal": 0, "truncated": 1,
                                             "rejected": 0}))
    assert decision.exclude_instance is None


def test_actor_evidence_failure_excludes_before_or_after_close():
    segments = bootstrap_and_terminal()
    i = intake()
    i.attest(attest("g1"))
    i.offer(message(segments[0]), segments[0].data)
    i.closed(closed_for(segments, segments={"bootstrap": 1, "terminal": 0, "truncated": 0,
                                            "rejected": 0}))
    request = {"actor": "actor-1", "game_instance_id": "g1", "reason": "evidence_failed",
               "run": "run-1", "training_generation": "gen-1"}
    decision = i.exclude(request)
    assert decision.reply == {"excluded": True, "game_instance_id": "g1"}
    assert decision.exclude_instance == "g1"
    assert i.exclude(request).exclude_instance is None  # once
    early = intake()
    early.attest(attest("g1"))
    assert early.exclude(request).exclude_instance == "g1"
    assert not early.offer(message(segments[1]), segments[1].data).admit
    assert early.report()["excluded_dropped_rows"] == len(segments[1].header["rows"])


def test_never_attested_instance_is_truncated_and_buffer_is_bounded():
    a = bootstrap_and_terminal("g1")
    b = bootstrap_and_terminal("g2")
    i = intake(buffer_segments=1)
    i.offer(message(a[0]), a[0].data)
    i.offer(message(b[0]), b[0].data)  # drops a[0], the oldest
    assert i.report()["pre_attestation_overflow_dropped"] == 1
    assert not i.attest(attest("g1")).admit
    i.closed(closed_for(b, segments={"bootstrap": 1, "terminal": 0, "truncated": 0,
                                     "rejected": 0}))
    assert i.report()["unattested_dropped"] == 1
    assert i.attest(attest("g2")).reply == {"admitted": False, "reason": "instance_closed"}


def test_broken_sender_order_is_loud():
    segments = bootstrap_and_terminal()
    i = intake()
    i.attest(attest("g1"))
    i.offer(message(segments[0]), segments[0].data)
    i.offer(message(segments[1]), segments[1].data)
    with pytest.raises(FastIntakeError, match="more_than_sealed"):
        i.closed(closed_for(segments, segments={"bootstrap": 1, "terminal": 0, "truncated": 0,
                                                "rejected": 0}))
    done = intake()
    done.attest(attest("g1"))
    done.closed(closed_for(segments, segments={"bootstrap": 0, "terminal": 0, "truncated": 0,
                                               "rejected": 0}))
    # The fence names the instance, this assembler and the one it closed under.
    with pytest.raises(FastIntakeError,
                       match=r"^fast_segment_after_instance_closed instance=g1 "
                             rf"assembler={segments[1].header['assembler_instance']} "
                             rf"earlier_assembler={segments[1].header['assembler_instance']}$"):
        done.offer(message(segments[1]), segments[1].data)
    with pytest.raises(FastIntakeError, match=r"^fast_instance_closed_twice instance=g1 "):
        done.closed(closed_for(segments))
    two = intake()
    two.offer(message(segments[0]), segments[0].data)
    first = segments[0].header["assembler_instance"]
    with pytest.raises(FastIntakeError, match=r"^fast_instance_from_two_assemblers instance=g1 "
                                              rf"assembler=other earlier_assembler={first}$"):
        two.closed(closed_for(segments, assembler_id="other"))
    with pytest.raises(HandoffError):
        i.offer({**message(segments[0]), "rows": 3}, segments[0].data)


def send_labels(sender: ShmSegmentSender, segments: list[SealedSegment]) -> None:
    """Test stand-in for the sender side (甲): labels go like a segment, acked."""
    from multiprocessing import shared_memory

    from stpd.rl.fast_source import build_instance_labels

    fields, data = build_instance_labels(segments)
    block = shared_memory.SharedMemory(create=True, size=max(len(data), 1))
    buffer = block.buf
    assert buffer is not None
    buffer[:len(data)] = data
    sender.sequence += 1
    sender.connection.send({**fields, "handoff_sequence": sender.sequence,
                            "shm_name": block.name})
    sender.inflight[sender.sequence] = (block, len(data))
    sender.inflight_bytes += len(data)


def _sender(connection: Any, segments: list[SealedSegment], close: dict[str, Any],
            labels: bool = False) -> None:
    sender = ShmSegmentSender(connection, assembler_id=segments[0].header["assembler_instance"])
    for segment in segments:
        sender.send(segment, Path(__file__))
    if labels:
        send_labels(sender, segments)
    connection.send(close)
    sender.finish()  # waits for every ack (#445), then the orderly end


def test_read_handoff_verifies_acks_and_reads_the_close_in_order():
    segments = bootstrap_and_terminal()
    ours, theirs = multiprocessing.Pipe()
    # The sender owns the shared memory in its own process, as in production.
    process = multiprocessing.Process(target=_sender,
                                      args=(theirs, segments, closed_for(segments)))
    process.start()
    i = intake()
    i.attest(attest("g1"))
    admitted = []
    for _ in range(3):
        kind, value, data = read_handoff(ours)
        if kind == "segment":
            assert data is not None
            admitted += [s.segment_id for s, _ in i.offer(value, data).admit]
            ack(ours, value)
        else:
            assert i.closed(value).exclude_instance is None
    process.join(30)
    assert process.exitcode == 0
    assert admitted == [s.segment_id for s in segments]


def _handoff_block(data: bytes, extra: int = 0) -> tuple[Any, dict[str, Any], Any, Any]:
    """A sender-owned block holding `data`, and a message claiming `size + extra` bytes."""
    from multiprocessing import shared_memory

    segment = bootstrap_and_terminal()[0]
    block = shared_memory.SharedMemory(create=True, size=max(len(data), 1))
    assert block.buf is not None
    block.buf[:len(data)] = data
    ours, theirs = multiprocessing.Pipe()
    theirs.send({**message(segment), "shm_name": block.name, "size": len(data) + extra,
                 "sha256": hashlib.sha256(data).hexdigest()})
    return block, message(segment), ours, theirs


def test_read_handoff_refuses_a_short_block_a_bad_digest_and_a_missing_block(monkeypatch):
    from multiprocessing import resource_tracker

    # The reader never registers with or unregisters from the resource tracker.
    def forbidden(*_: Any) -> None:
        raise AssertionError("resource tracker touched by the reader")

    data = bytes(range(256)) * 4
    block, _, ours, _ = _handoff_block(data)
    try:
        # Patched only around the reads: creating and unlinking (sender side) track.
        monkeypatch.setattr(resource_tracker, "register", forbidden)
        monkeypatch.setattr(resource_tracker, "unregister", forbidden)
        kind, _, copied = read_handoff(ours)
        assert (kind, copied) == ("segment", data)
        # Claim more bytes than the block holds (block.size is page-rounded on macOS).
        ours, theirs = multiprocessing.Pipe()
        theirs.send({**message(bootstrap_and_terminal()[0]), "shm_name": block.name,
                     "size": block.size + 1, "sha256": hashlib.sha256(data).hexdigest()})
        with pytest.raises(HandoffError, match="handoff_size"):
            read_handoff(ours)
        for bad in (-1, True, 1.0):
            theirs.send({**message(bootstrap_and_terminal()[0]), "shm_name": block.name,
                         "size": bad, "sha256": hashlib.sha256(data).hexdigest()})
            with pytest.raises(HandoffError, match="handoff_size"):
                read_handoff(ours)
        theirs.send({**message(bootstrap_and_terminal()[0]), "shm_name": block.name,
                     "size": len(data), "sha256": hashlib.sha256(b"other").hexdigest()})
        with pytest.raises(HandoffError, match="handoff_digest"):
            read_handoff(ours)
    finally:
        monkeypatch.undo()
        block.close()
        block.unlink()
    # The sender already unlinked it: fail loudly, no fallback.
    theirs.send({**message(bootstrap_and_terminal()[0]), "shm_name": block.name,
                 "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    with pytest.raises(FileNotFoundError):
        read_handoff(ours)


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="the pread path (Linux)")
def test_read_handoff_short_pread_is_handoff_size(monkeypatch):
    import os

    data = b"x" * 5000
    block, _, ours, _ = _handoff_block(data)
    real = os.pread
    reads: list[int] = []

    def short(fd: int, length: int, offset: int) -> bytes:
        reads.append(offset)
        # The first read returns part, the second ends early.
        return real(fd, min(length, 1000), offset) if len(reads) == 1 else b""

    monkeypatch.setattr(os, "pread", short)
    try:
        with pytest.raises(HandoffError, match="handoff_size"):
            read_handoff(ours)
        assert reads == [0, 1000]
    finally:
        block.close()
        block.unlink()


def test_segment_under_a_mu_the_learner_never_published_is_refused_loudly():
    i = FastSegmentIntake(run="run-1", generation="gen-1")
    i.attest(attest("g1"))
    segment = bootstrap_and_terminal()[0]
    with pytest.raises(FastIntakeError, match="fast_segment_mu_not_published"):
        i.offer(message(segment), segment.data)
    # Same digest, another version/updates: also not this learner's publication.
    i.publish_mu({**published(mu(1)), "version": 7})
    with pytest.raises(FastIntakeError, match="fast_segment_mu_not_published"):
        i.offer(message(segment), segment.data)
    assert i.report()["unpublished_mu_refused"] == 2


def test_published_mu_ledger_is_typed_and_per_generation():
    i = intake()
    with pytest.raises(FastIntakeError, match="fast_published_mu_generation_changed"):
        i.publish_mu(published(mu(2, "gen-0")))
    with pytest.raises(FastIntakeError, match="fast_published_mu_identity_fields"):
        i.publish_mu({**published(mu(2)), "version": "2"})
    with pytest.raises(FastIntakeError, match="fast_published_mu_digest_reused"):
        i.publish_mu({**published(mu(1)), "updates": 9})
    i.set_generation("gen-2")
    assert not i.published


# ---------------------------------------------------------- instance labels (gap a)
def combat_game(game: str = "g1", generation: str = "gen-1",
                handoff: MuHandoff | None = None, compiler: FrameCompiler = COMPILER,
                assembler_id: str = "node-a") -> list[SealedSegment]:
    """70 rows: combat 0-9 (post hp 40), map 10-19, combat 20-29 (post hp 30), then
    noncombat to a natural terminal; two segments (bootstrap + terminal)."""
    g = Game(generation, compiler=compiler, assembler_id=assembler_id, handoff=handoff)
    g.open(game)
    for step in range(70):
        combat = step < 10 or 20 <= step < 30
        hp = 40 if step == 9 else 30 if step == 29 else 60
        g.step(game, in_combat=combat, hp=hp, terminal=step == 69)
    g.close(game, "terminal")
    assert [s.kind for s in g.sealed] == ["bootstrap", "terminal"]
    return g.sealed


def labels_message(fields: dict[str, Any], sequence: int = 9) -> dict[str, Any]:
    return {**fields, "handoff_sequence": sequence, "shm_name": "unused"}


def test_instance_labels_follow_the_old_rules():
    from stpd.rl.fast_source import build_instance_labels
    from stpd.rl.segments import uniform_depth_indices

    sealed = combat_game()
    fields, data = build_instance_labels(sealed)
    ids = [s.segment_id for s in sealed]
    keys = [(ids[i // 64], i % 64) for i in range(70)]
    assert [(r["segment_id"], r["row"]) for r in fields["outcome_rows"]] == [
        keys[i] for i in uniform_depth_indices(70)]
    assert fields["terminal"] == sealed[-1].header["seal"]["terminal"]
    battles = [(b["battle_id"], b["post_hp"], [r["step_index"] for r in b["states"]])
               for b in fields["hp_battles"]]
    assert battles == [("g1:0", 40, list(range(10))), ("g1:20", 30, list(range(20, 30)))]
    # The data carries the referenced originals, byte for byte, 8-byte aligned.
    assert [e["segment_id"] for e in fields["segments"]] == ids
    for entry, segment in zip(fields["segments"], sealed, strict=True):
        assert data[entry["offset"]:entry["offset"] + entry["size"]] == segment.data


def labelled_intake(**kwargs: Any) -> tuple[FastSegmentIntake, list[SealedSegment]]:
    i = intake(**kwargs)
    i.attest(attest("g1"))
    sealed = combat_game()
    for n, segment in enumerate(sealed, 1):
        assert i.offer(message(segment, n), segment.data).admit
    return i, sealed


def test_labels_become_terminal_and_hp_groups_only_when_enabled():
    from stpd.rl.fast_source import build_instance_labels

    i, sealed = labelled_intake(aux_labels=frozenset({"terminal", "hp"}))
    fields, data = build_instance_labels(sealed)
    decision = i.labels(labels_message(fields), data)
    kinds = [(kind, identity, len(rows)) for kind, identity, rows in decision.auxiliary]
    assert kinds == [("terminal", "g1", 64), ("hp", "g1:0", 10), ("hp", "g1:20", 10)]
    hp_row = decision.auxiliary[1][2][0]
    assert hp_row["label"] == 0.4 and hp_row["segment"].data == sealed[0].data
    assert hp_row["envelope"]["packet_sha256"] == sealed[0].sha256
    assert decision.auxiliary[0][2][0]["label"] == sealed[-1].header["seal"]["terminal"]["class"]
    assert i.closed(closed_for(sealed)).exclude_instance is None
    assert "closed_without_labels" not in i.report()

    off, sealed = labelled_intake()
    fields, data = build_instance_labels(sealed)
    assert not off.labels(labels_message(fields), data).auxiliary
    assert off.report()["labels_disabled"] == 1


def test_labels_that_differ_from_the_received_headers_fence():
    from stpd.rl.fast_source import build_instance_labels

    def refused(change: Any, match: str) -> None:
        i, sealed = labelled_intake(aux_labels=frozenset({"terminal", "hp"}))
        fields, data = build_instance_labels(sealed)
        fields, data = change(fields, data)
        with pytest.raises(FastIntakeError, match=match):
            i.labels(labels_message(fields), data)

    def other_hp(f: dict[str, Any], d: bytes) -> tuple[dict[str, Any], bytes]:
        battles = [dict(f["hp_battles"][0], post_hp=41), *f["hp_battles"][1:]]
        return {**f, "hp_battles": battles}, d

    def fewer_rows(f: dict[str, Any], d: bytes) -> tuple[dict[str, Any], bytes]:
        return {**f, "outcome_rows": f["outcome_rows"][1:]}, d

    def flipped(f: dict[str, Any], d: bytes) -> tuple[dict[str, Any], bytes]:
        return f, d[:100] + bytes([d[100] ^ 1]) + d[101:]

    def dropped(f: dict[str, Any], d: bytes) -> tuple[dict[str, Any], bytes]:
        return {**f, "segments": f["segments"][:1]}, d

    refused(other_hp, "fast_labels_rule_changed")
    refused(fewer_rows, "fast_labels_rule_changed")
    refused(flipped, "fast_labels_segment_bytes_changed")
    refused(dropped, "fast_labels_segments_changed")
    i, sealed = labelled_intake(aux_labels=frozenset({"terminal"}))
    fields, data = build_instance_labels(sealed)
    i.labels(labels_message(fields), data)
    with pytest.raises(FastIntakeError, match="fast_labels_twice"):
        i.labels(labels_message(fields), data)


def test_terminal_close_without_labels_is_counted_when_enabled():
    i, sealed = labelled_intake(aux_labels=frozenset({"terminal"}))
    i.closed(closed_for(sealed))
    assert i.report()["closed_without_labels"] == 1


def test_header_only_parse_skips_row_digests_but_the_decode_worker_still_binds_them():
    from stpd.rl.fast_segment import SegmentFormatError, decode_segment, sha256

    data = bytes(bootstrap_and_terminal()[0].data)
    header, rows = decode_segment(data)
    quick, none = decode_segment(data, header_only=True)
    assert quick == header and none == [] and rows
    # One flipped row byte: the header parse (bytes already bound by the hand-off
    # digest) does not hash rows; every full decode refuses the bytes loudly.
    length = int.from_bytes(data[8:16], "little")
    row0 = 16 + length + (-length % 8)
    bad = bytearray(data)
    bad[row0] ^= 0xFF
    assert decode_segment(bytes(bad), header_only=True)[0] == header
    with pytest.raises(SegmentFormatError, match="row_digest"):
        decode_segment(bytes(bad))
    with pytest.raises(SegmentFormatError, match="segment_digest"):
        decode_segment(bytes(bad), expected_sha256=sha256(data))
    # Layout checks stay in the header parse: trailing bytes are still refused.
    with pytest.raises(SegmentFormatError, match="trailing|out_of_bounds|offsets"):
        decode_segment(data + b"\x00" * 8, header_only=True)



# ------------------------------------------------ intake lock: no heavy work under it
def test_label_body_corrupted_after_offer_fails_loudly():
    """Labels bind every carried body to its offer() digest (outside the intake
    lock); rows are bound to their own sha256 later, by the decode worker."""
    from stpd.rl.fast_segment import decode_segment, sha256
    from stpd.rl.fast_source import build_instance_labels

    # (a) Bytes changed after offer: the body no longer matches the offered digest.
    i, sealed = labelled_intake(aux_labels=frozenset({"terminal", "hp"}))
    fields, data = build_instance_labels(sealed)
    entry = fields["segments"][1]
    bad = bytearray(data)
    bad[entry["offset"] + entry["size"] - 1] ^= 0xFF  # last row byte of segment 2
    with pytest.raises(FastIntakeError, match="fast_labels_segment_bytes_changed"):
        i.labels(labels_message(fields), bytes(bad))

    # (b) A row whose bytes do not match its row sha256 under a consistent whole
    # digest: offer() and labels() both parse the header only (the body is bound
    # to its offered digest), so labels admits it; the decode worker's full decode
    # of that same body refuses it loudly before any of its rows is used.
    i = intake(aux_labels=frozenset({"terminal", "hp"}))
    i.attest(attest("g1"))
    sealed = combat_game()
    raw = bytearray(sealed[0].data)
    length = int.from_bytes(raw[8:16], "little")
    raw[16 + length + (-length % 8)] ^= 0xFF  # first byte of row 0
    raw = bytes(raw)
    assert decode_segment(raw, header_only=True)[0] == sealed[0].header
    first = {**message(sealed[0], 1), "sha256": sha256(raw)}
    assert i.offer(first, raw).admit
    assert i.offer(message(sealed[1], 2), sealed[1].data).admit
    fields, data = build_instance_labels(sealed)
    entry = fields["segments"][0]
    data = data[:entry["offset"]] + raw + data[entry["offset"] + entry["size"]:]
    fields["segments"][0] = {**entry, "sha256": sha256(raw)}
    decision = i.labels(labels_message(fields), data)
    carried = [row for _, _, rows in decision.auxiliary for row in rows
               if row["segment"].data == raw]
    assert carried
    with pytest.raises(SegmentFormatError, match="row_digest"):
        decode_segment(carried[0]["segment"].data,
                       expected_sha256=carried[0]["envelope"]["packet_sha256"])


def _blocking_decode(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """Make labels()'s unlocked header decode wait until released.

    Only the thread named "labels-worker" waits; offer() on other threads parses
    the same way (header only) and must never wait for it.
    """
    import threading

    import stpd.rl.fast_source as fs

    entered, release = threading.Event(), threading.Event()
    real = fs.decode_segment

    def decode(data: Any, **kwargs: Any) -> Any:
        if threading.current_thread().name == "labels-worker":
            entered.set()
            assert release.wait(10)
        return real(data, **kwargs)

    monkeypatch.setattr(fs, "decode_segment", decode)
    return entered, release


def test_labels_decode_runs_outside_the_intake_lock(monkeypatch):
    """While one reader decodes a labels body, other readers offer, attest and
    report without waiting for it (the six hand-off readers stay parallel)."""
    import threading

    from stpd.rl.fast_source import build_instance_labels

    i, sealed = labelled_intake(aux_labels=frozenset({"terminal", "hp"}))
    fields, data = build_instance_labels(sealed)
    entered, release = _blocking_decode(monkeypatch)
    result: dict[str, Any] = {}
    worker = threading.Thread(name="labels-worker",
        target=lambda: result.setdefault("d", i.labels(labels_message(fields), data)))
    worker.start()
    try:
        assert entered.wait(10)
        done = threading.Event()

        def other() -> None:
            i.set_generation("gen-1")
            i.attest(attest("g2"))
            for n, segment in enumerate(bootstrap_and_terminal("g2"), 1):
                assert i.offer(message(segment, n), segment.data).admit
            i.report()
            done.set()

        threading.Thread(target=other, daemon=True).start()
        assert done.wait(10), "another reader waited for a labels decode"
    finally:
        release.set()
        worker.join(10)
    assert [kind for kind, _, _ in result["d"].auxiliary] == ["terminal", "hp", "hp"]


def test_generation_change_during_unlocked_labels_work_drops_the_labels(monkeypatch):
    import threading

    from stpd.rl.fast_source import build_instance_labels

    i, sealed = labelled_intake(aux_labels=frozenset({"terminal", "hp"}))
    fields, data = build_instance_labels(sealed)
    entered, release = _blocking_decode(monkeypatch)
    result: dict[str, Any] = {}
    worker = threading.Thread(name="labels-worker",
        target=lambda: result.setdefault("d", i.labels(labels_message(fields), data)))
    worker.start()
    assert entered.wait(10)
    i.set_generation("gen-2")  # not blocked by the decode in flight
    release.set()
    worker.join(10)
    decision = result["d"]
    assert not decision.auxiliary and not decision.admit and decision.reply is None
    assert i.report()["labels_old_generation_dropped"] == 1
    assert i.report()["open_instances"] == 0
    # Nothing of gen-1 is attested or trained under gen-2.
    i.publish_mu(published(mu(1, "gen-2")))
    old = combat_game()
    assert not i.offer(message(old[0], 1), old[0].data).admit


def test_unchanged_generation_never_waits_for_the_intake_lock():
    import threading

    i = intake()
    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        with i.lock:
            held.set()
            release.wait(10)

    threading.Thread(target=holder, daemon=True).start()
    assert held.wait(10)
    done = threading.Event()
    threading.Thread(target=lambda: (i.set_generation("gen-1"), done.set()),
                     daemon=True).start()
    try:
        assert done.wait(5)
    finally:
        release.set()


def _short_game(node: str, game: str) -> list[SealedSegment]:
    g = Game("gen-1", assembler_id=node)
    g.open(game)
    g.step(game, terminal=True)
    g.close(game, "terminal")
    return g.sealed


def _deliver(i: FastSegmentIntake, game: str, sealed: list[SealedSegment]) -> Any:
    reply = i.attest(attest(game)).reply
    for seg in sealed:
        i.offer(message(seg), seg.data)
    counts = {"bootstrap": 0, "terminal": 0, "truncated": 0, "rejected": 0}
    for seg in sealed:
        counts[seg.kind] += 1
    return reply, i.closed(closed_for(sealed, segments=counts))


def test_two_env_nodes_with_the_same_native_id_stay_distinct_once_host_scoped():
    # Native ids are `native-compact:{pid}:{counter}`; PIDs repeat across machines
    # (h100d env-n6/n7 both on 565295). The fast-env Host scopes the id with its own
    # launch id (`{runtime_instance_id}:{native}`, fast-env/host.ts fastGameInstanceId).
    native = "native-compact:565295:1"
    raw = intake()
    _deliver(raw, native, _short_game("node-a", native))
    with pytest.raises(FastIntakeError, match="after_instance_closed|closed_twice|two_assemblers"):
        _deliver(raw, native, _short_game("node-b", native))
    scoped = intake()
    for node, runtime in (("node-a", "3f" * 16), ("node-b", "a1" * 16)):
        game = f"{runtime}:{native}"
        reply, closed = _deliver(scoped, game, _short_game(node, game))
        assert reply == {"admitted": True, "game_instance_id": game}
        assert closed.exclude_instance is None
    assert scoped.report()["open_instances"] == 0
