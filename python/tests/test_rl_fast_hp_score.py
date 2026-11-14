"""RL5 W2: per-combat HP score (card 6.5): A_hp, segment format 4 and c_hp in the learner."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

import pytest
import torch
from test_p1_input_v2 import public_state
from test_rl_fast_source import COMPILER, SAMPLING, attest, mu, values

from spireagent.rl_contracts import HotPayloadRef, RunSequence
from stpd.p1.rows_encoder_wire import encode_row
from stpd.rl import batch_preparation as bp
from stpd.rl.fast_assembly import AssemblyError, FastSegmentAssembler, hp_advantages
from stpd.rl.fast_segment import (
    FORMAT_3,
    FORMAT_4,
    SegmentFormatError,
    decode_segment,
    encode_segment_columns,
)
from stpd.rl.fast_source import (
    CLOSE_REASONS,
    FastIntakeError,
    FastSegmentIntake,
    decode_fast_packet,
    fast_envelope,
)
from stpd.rl.heads import categorical_lambda_targets, class_reward_values
from stpd.rl.loss import LossConfig, batched_segment_loss
from stpd.rl.prepared_batch import BatchMember, PrepareRequest

INSTANCE = "node-a.000000000001"


def facts(hp: int, max_hp: int = 80) -> dict[str, Any]:
    return {"act": 1, "floor": 1, "hp": hp, "max_hp": max_hp}


def outcome(hp: int, max_hp: int = 80, terminal: str | None = None) -> dict[str, Any]:
    return {"status": "completed", "public_facts": facts(hp, max_hp),
            "terminal": None if terminal is None else {"result": terminal}}


# -- A_hp on constructed combats ----------------------------------------------------
def test_a_normal_combat_end_with_a_potion_and_a_max_hp_gain():
    # H = 70 65 60 58, M = 80 80 80 82; the second decision drinks a potion.
    steps = [(outcome(65), 62.5, False), (outcome(60), 50.0, True),
             (outcome(58, 82), 56.25, False)]
    # D_0 = (58-70) + (82-80) - 5 = -15, baseline 62.5-70: A = -7.5
    # D_1 = (58-65) + 2 - 5 = -10, baseline 50-65: A = 5
    # D_2 = (58-60) + 2 = 0 (the potion was before), baseline 56.25-60: A = 3.75
    assert hp_advantages(facts(70), steps) == [-7.5, 5.0, 3.75]


def test_death_scores_zero_hp_and_takes_back_the_combat_max_hp_gain():
    steps = [(outcome(40, 75), 45.0, False),
             (outcome(3, 75, terminal="defeat"), 20.0, True)]  # a defeat is 0 hp
    # H_T = 0, extra = -30 - (75 - 70) on the last step.
    # D_0 = (0-50) + (75-70) - 5 - 35 = -85, baseline 45-50: A = -80
    # D_1 = (0-40) + 0 - 5 - 35 = -80, baseline 20-40: A = -60
    assert hp_advantages(facts(50, 70), steps) == [-80.0, -60.0]
    won = [(outcome(40, 75), 45.0, False), (outcome(3, 75, terminal="victory"), 20.0, True)]
    assert hp_advantages(facts(50, 70), won) == [(3 - 50) + 5 - 5 - (45 - 50),
                                                 (3 - 40) - 5 - (20 - 40)]


@pytest.mark.parametrize("start,last", [
    (None, outcome(60)),                                  # the game started inside combat
    (facts(70), {"status": "completed", "public_facts": None, "terminal": None}),
    (facts(70), {"status": "completed", "public_facts": {"hp": 60}, "terminal": None}),
    (facts(70), {"status": "completed", "public_facts": {"hp": True, "max_hp": 80},
                 "terminal": None}),
])
def test_a_missing_public_fact_gives_no_score(start, last):
    assert hp_advantages(start, [(outcome(65), 60.0, False), (last, 60.0, False)]) is None


# -- the assembler ------------------------------------------------------------------
class HpGame:
    """Real assembler, real rows-v2 bytes; `hp` of a step = the public hp it led to."""

    def __init__(self, segment_format: str = FORMAT_4) -> None:
        self.sealed: list[Any] = []
        self.closed: list[Any] = []
        frame = COMPILER.compile(public_state())
        self.raw, self.width = encode_row(frame), len(frame.choices)
        self.hp_score = segment_format == FORMAT_4
        self.assembler = FastSegmentAssembler(
            assembler_id="node-a", compiler_identity=COMPILER.identity, emit=self.sealed.append,
            terminal_labeler=lambda _summary: {
                "class": 3, "reward": float(class_reward_values()[3])},
            on_closed=self.closed.append, segment_format=segment_format, verify_every=1)
        self.assembler.instance_id = INSTANCE
        self.assembler.offer_mu(mu(1))
        self.request = 0
        self.steps: dict[str, int] = {}

    def open(self, game: str, **extra: Any) -> None:
        self.assembler.open_episode({
            "schema": "stpd/rl-inference-4", "kind": "episode_open", "ascension": 0,
            "request_id": 1, "game_instance_id": game, "actor_id": "actor-1",
            "sampling": SAMPLING, "single_choice": "score", "start": {"kind": "new_run"},
            **extra})
        self.steps[game] = 0

    def step(self, game: str, *, combat: bool, hp: int | None = None, max_hp: int = 80,
             predicted: float = 0.5, potion: bool = False, terminal: str | None = None,
             status: str = "completed") -> dict[str, Any]:
        """One decision; `hp=None` sends no step outcome (the row is never played)."""
        self.request += 1
        step = self.steps[game]
        self.steps[game] += 1
        row = {"request_id": self.request, "game_instance_id": game, "step_index": step,
               "consumption_id": f"c-{self.request}", "catalog_len": self.width,
               "catalog_sha256": "c" * 64, "observation_sha256": "d" * 64,
               "in_combat": combat, "floor": 1,
               "payload": {"offset": 0, "length": len(self.raw),
                           "row_sha256": hashlib.sha256(self.raw).hexdigest()}}
        plan = self.assembler.begin_row(row, self.raw)
        q = [0.0] * self.width
        q[0], q[-1] = 0.25, 0.75
        scored = {"request_id": row["request_id"], "game_instance_id": game,
                  "catalog_sha256": row["catalog_sha256"], "status": "scored",
                  "mu_snapshot_sha256": plan.mu.snapshot_sha256, "segment_id": plan.segment_id,
                  "row_in_segment": plan.row_in_segment, "selected_index": self.width - 1,
                  "behavior_q": q, "selected_q": q[-1],
                  "applied": {"arm_id": "control",
                              "decision_class": "combat" if combat else "noncombat",
                              "temperature": 1.0, "uniform_mix": 0.0}}
        extra = {"hp": predicted, "potion": potion} if self.hp_score else {}
        self.assembler.finish_row(plan, scored, values(1, 0.01 * (step % 7)),
                                  values(1, 0.2) if plan.tail is not None else None, **extra)
        if hp is not None or status != "completed":
            message = {"schema": "stpd/rl-inference-4", "kind": "step_outcome",
                       "row_request_id": row["request_id"], "game_instance_id": game,
                       "step_index": step, "status": status}
            if status == "completed":
                message.update(receipt_sha256="e" * 64,
                               terminal=None if terminal is None else {"result": terminal},
                               public_facts=facts(hp, max_hp))  # type: ignore[arg-type]
            else:
                message["reason"] = "delivery_unknown"
            self.assembler.step_outcome(message)
        return row

    def close(self, game: str, reason: str) -> None:
        self.assembler.close_episode({"schema": "stpd/rl-inference-4", "kind": "episode_close",
                                      "request_id": 1, "game_instance_id": game,
                                      "reason": reason, "last_step_index": None})

    def scores(self) -> dict[str, int]:
        return self.assembler.report()["hp_score"]


def play_two_combats(g: HpGame) -> None:
    g.open("g")
    g.step("g", combat=False, hp=70)                            # map: the combat starts at 70/80
    g.step("g", combat=True, hp=65, predicted=0.625)
    g.step("g", combat=True, hp=60, predicted=0.5, potion=True)
    g.step("g", combat=True, hp=58, max_hp=82, predicted=0.5625)
    g.step("g", combat=False, hp=58, max_hp=82)                 # reward screen
    g.step("g", combat=True, hp=0, max_hp=82, predicted=0.25, terminal="defeat")
    g.close("g", "terminal")


def test_each_combat_is_scored_from_its_public_facts_into_the_format_4_column():
    g = HpGame()
    play_two_combats(g)
    [segment] = g.sealed
    assert segment.kind == "terminal" and segment.data[:8] == b"STPDFS04"
    header, rows = decode_segment(segment.data, expected_sha256=segment.sha256)
    assert header == segment.header and len(rows) == 6
    # second combat: D = (0-58) - 30 - (82-82) = -88, baseline 25-58: A = -55
    assert header["hp_adv"] == [None, -7.5, 5.0, 3.75, None, -55.0]
    assert header["group_id"] == "g" and header["ticket"] == {"start": "new_run", "end": "game"}
    assert g.scores() == {"battles": 2, "battles_unscored": 0, "potions": 1, "deaths": 1,
                          "rows_unscored": 0, "ticket_ends": 0}


def test_format_4_round_trip_is_format_3_plus_the_three_fields():
    three, four = HpGame(FORMAT_3), HpGame(FORMAT_4)
    play_two_combats(three)
    play_two_combats(four)
    left, right = three.sealed[0].header, four.sealed[0].header
    assert {**left, "format": FORMAT_4, "group_id": "g", "hp_adv": right["hp_adv"],
            "ticket": {"start": "new_run", "end": "game"}} == right
    assert decode_segment(four.sealed[0].data, header_only=True)[0] == right
    assert decode_segment(three.sealed[0].data)[1] == decode_segment(four.sealed[0].data)[1]
    assert "hp_score" not in three.assembler.report()


def _reencoded(header: dict[str, Any], rows: list[bytes]) -> Any:
    return encode_segment_columns(header, rows, bound=True)


def test_format_4_refuses_a_score_off_combat_a_bad_value_and_a_missing_field():
    g = HpGame()
    play_two_combats(g)
    header, rows = decode_segment(g.sealed[0].data)
    assert decode_segment(_reencoded(header, rows).data)[0] == header
    for change in ({"hp_adv": [1.0, *header["hp_adv"][1:]]},          # a non-combat row
                   {"hp_adv": [None, float("nan"), *header["hp_adv"][2:]]},
                   {"hp_adv": [None, 3, *header["hp_adv"][2:]]},      # int, not float
                   {"hp_adv": header["hp_adv"][:-1]},
                   {"ticket": {"start": "new_run", "end": "act"}},
                   {"group_id": ""}):
        with pytest.raises(SegmentFormatError):
            _reencoded({**header, **change}, rows)
    with pytest.raises(SegmentFormatError, match="segment_header_keys"):
        _reencoded({key: value for key, value in header.items() if key != "group_id"}, rows)
    # Format-3 bytes never acquire an hp_adv: the old format reads as itself.
    three = HpGame(FORMAT_3)
    play_two_combats(three)
    assert "hp_adv" not in decode_segment(three.sealed[0].data)[0]


def test_ticket_and_group_need_format_4_and_format_4_needs_the_hp_prediction():
    three = HpGame(FORMAT_3)
    for extra in ({"ticket": {"end": "combat"}}, {"group_id": "grp"}):
        with pytest.raises(AssemblyError, match="ticket_needs_segment_format_4"):
            three.open("x", **extra)
    four = HpGame()
    for extra in ({"ticket": {"end": "act"}}, {"ticket": {"end": "combat", "x": 1}},
                  {"ticket": "combat"}, {"group_id": ""}, {"group_id": 3}):
        with pytest.raises(AssemblyError, match="invalid_episode_open"):
            four.open("x", **extra)
    four.open("g")
    four.hp_score = False  # the service forgets the head output
    with pytest.raises(AssemblyError, match="hp_prediction_required"):
        four.step("g", combat=True, hp=60)


def test_a_full_segment_waits_for_its_combat_score_without_pinning_its_mu():
    g = HpGame()
    g.open("g")
    g.step("g", combat=False, hp=70)
    for _ in range(63):
        g.step("g", combat=True, hp=70)
    assert g.assembler.offer_mu(mu(2)).accepted
    g.step("g", combat=True, hp=66)        # row 64: the successor; the combat goes on
    assert not g.sealed                    # format 3 would seal here
    assert mu(1).snapshot_sha256 not in g.assembler.report()["referenced_mu"]
    g.step("g", combat=False, hp=66)       # the combat has ended
    [first] = g.sealed
    assert first.kind == "bootstrap"
    assert first.header["seal"]["bootstrap"]["successor_step_index"] == 64
    # every decision: D = 66 - 70, baseline 50 - 70
    assert first.header["hp_adv"] == [None] + [16.0] * 63


def test_a_combat_the_game_never_finished_gets_no_score_and_loses_no_segment():
    g = HpGame()
    g.open("g")
    g.step("g", combat=False, hp=70)
    for _ in range(64):
        g.step("g", combat=True, hp=70)
    g.close("g", "stop")
    first, second = g.sealed
    assert (first.kind, second.kind) == ("bootstrap", "truncated")  # as format 3
    assert first.header["hp_adv"] == [None] * 64
    assert g.scores()["rows_unscored"] == 63
    # A failed delivery while a segment is held: sealed as format 3 had, not truncated.
    kinds = []
    for segment_format in (FORMAT_3, FORMAT_4):
        g = HpGame(segment_format)
        g.open("g")
        g.step("g", combat=False, hp=70)
        for _ in range(64):
            g.step("g", combat=True, hp=70)
        g.step("g", combat=True, status="unknown")
        g.close("g", "unknown")
        kinds.append([(s.kind, len(s.header["rows"])) for s in g.sealed])
    assert kinds[0] == kinds[1] == [("bootstrap", 64), ("truncated", 2)]


def single_combat(g: HpGame, game: str = "t") -> None:
    g.open(game, ticket={"end": "combat"}, group_id="grp-1")
    g.step(game, combat=False, hp=70)                          # e.g. the map node chosen
    g.step(game, combat=True, hp=64, predicted=0.625)
    g.step(game, combat=True, hp=60, predicted=0.625, potion=True)
    g.step(game, combat=False)                                 # post-combat: scored, not played
    g.close(game, "ticket_end")


def test_a_single_combat_ticket_ends_as_a_truncation_on_the_post_combat_value():
    g = HpGame()
    single_combat(g)
    [segment] = g.sealed
    header = segment.header
    assert segment.kind == "bootstrap" and len(header["rows"]) == 3
    tail = header["seal"]["bootstrap"]
    assert tail["successor_step_index"] == 3 and tail["mu_snapshot_sha256"] == mu(1).snapshot_sha256
    assert tail["outcome_probabilities"] == values(1, 0.01 * 3)  # the post-combat row's value
    assert header["group_id"] == "grp-1" and header["ticket"] == {"start": "new_run",
                                                                   "end": "combat"}
    # D_0 = (60-70) - 5 = -15, baseline 62.5-70: -7.5; D_1 = (60-64) - 5 = -9, 62.5-64: -7.5
    assert header["hp_adv"] == [None, -7.5, -7.5]
    assert decode_segment(segment.data, expected_sha256=segment.sha256)[0] == header
    [closed] = g.closed
    assert closed.close_reason == "ticket_end" and closed.failure is None
    assert closed.segments == {"bootstrap": 1, "terminal": 0, "truncated": 0, "rejected": 0}
    assert g.scores()["ticket_ends"] == 1 and "ticket_end" in CLOSE_REASONS
    assert g.assembler.report()["referenced_mu"] == {}


def test_a_ticket_end_on_a_fresh_segment_leaves_the_tail_to_the_previous_one():
    g = HpGame()
    g.open("t", ticket={"end": "combat"})
    g.step("t", combat=False, hp=70)
    for _ in range(63):
        g.step("t", combat=True, hp=69)
    g.step("t", combat=False)  # row 64 opens segment 2 and is the tail of segment 1
    g.close("t", "ticket_end")
    [segment] = g.sealed
    assert segment.kind == "bootstrap" and len(segment.header["rows"]) == 64
    assert segment.header["seal"]["bootstrap"]["successor_step_index"] == 64
    assert g.closed[0].segments["bootstrap"] == 1


def test_a_short_segment_is_a_truncation_unless_the_ticket_ended_after_its_combat():
    g = HpGame()
    single_combat(g)
    header, rows = decode_segment(g.sealed[0].data)
    with pytest.raises(SegmentFormatError, match="segment_bootstrap_seal"):
        _reencoded({**header, "ticket": {"start": "new_run", "end": "game"}}, rows)
    # A combat ticket that ends any other way (a stop, a failure) is truncated as before.
    g = HpGame()
    g.open("t", ticket={"end": "combat"})
    g.step("t", combat=False, hp=70)
    g.step("t", combat=True, hp=64)
    g.close("t", "stop")
    assert [s.kind for s in g.sealed] == ["truncated"]


# -- the learner: c_hp ----------------------------------------------------------------
def envelope(segment: Any, game: str, coef: float) -> dict[str, Any]:
    a = attest(game)
    return fast_envelope(segment.header, size=len(segment.data), digest=segment.sha256,
                         run="run-1", attestation={key: a[key] for key in (
                             "actor", "intent_id", "seed", "training_split")},
                         gae_lambda=0.99, hp_advantage_coef=coef)


def sources(segments: list[Any], game: str, coef: float) -> list[Any]:
    return [decode_fast_packet(s.data, envelope=envelope(s, game, coef), compiler=COMPILER)[0]
            for s in segments]


def batch(segments: list[Any], prepared: list[Any], game: str) -> Any:
    count = sum(len(s.frames) for s in prepared)
    request = PrepareRequest(
        RunSequence("run-1", "gen-1", 1),
        tuple(BatchMember(s.segment_id, HotPayloadRef(f"run-1/gen-1/{i}", "f" * 64, 1),
                          game, len(s.header["rows"]), s.header["mu"]["snapshot_sha256"],
                          "intent-" + game) for i, s in enumerate(segments)),
        (), (tuple(range(count)),), (), COMPILER.identity, random.Random(1).getstate(),
        128 * 1024**2)
    return bp.assemble(request, tuple(prepared), microbatch_size=16, deduplicate_text=True,
                       prepared_layout="rows-v2")


def two_segments(segment_format: str) -> list[Any]:
    """A bootstrap segment (a combat across its end) and a terminal segment (a death)."""
    g = HpGame(segment_format)
    g.open("g")
    g.step("g", combat=False, hp=70)
    for index in range(70):
        g.step("g", combat=index % 20 != 19, hp=70 - index // 2, potion=index % 9 == 4,
               predicted=0.5 + 0.0078125 * (index % 5))
    g.step("g", combat=True, hp=0, terminal="defeat")
    g.close("g", "terminal")
    assert [s.kind for s in g.sealed] == ["bootstrap", "terminal"]
    return g.sealed


def test_c_hp_is_added_to_the_outcome_advantage_once_before_the_one_normalisation():
    segments = two_segments(FORMAT_4)
    coef = 0.003
    prepared = sources(segments, "g", coef)
    raw = []
    for segment, source in zip(segments, prepared, strict=True):
        header = segment.header
        seal = header["seal"]
        tail = (None if seal["terminal"]
                else torch.tensor(seal["bootstrap"]["outcome_probabilities"]))
        _, outcome_advantage = categorical_lambda_targets(
            torch.tensor([r["outcome_probabilities"] for r in header["rows"]]), tail,
            terminal_class=seal["terminal"]["class"] if seal["terminal"] else None)
        hp = torch.tensor([0.0 if v is None else v for v in header["hp_adv"]])
        assert any(v is not None for v in header["hp_adv"])
        assert torch.equal(source.raw_advantages, outcome_advantage + coef * hp)
        raw.append((outcome_advantage, hp))
    result = batch(segments, prepared, "g")
    outcome_all = torch.cat([a for a, _ in raw])
    hp_all = torch.cat([h for _, h in raw])
    assert None in segments[0].header["hp_adv"]  # a non-combat row adds nothing
    mask = result.policy_mask
    assert bool(mask.all())  # every row here has two choices

    def normalised(value: torch.Tensor) -> torch.Tensor:
        return (value - float(value.mean())) / max(float(value.std(unbiased=False)), 1e-8)

    assert torch.allclose(result.advantages, normalised(outcome_all + coef * hp_all), atol=1e-6)
    # not "normalise each, then add", nor "add after the normalisation"
    assert not torch.allclose(result.advantages,
                              normalised(outcome_all) + coef * normalised(hp_all), atol=1e-3)
    assert not torch.allclose(result.advantages, normalised(outcome_all) + coef * hp_all,
                              atol=1e-3)


def test_c_hp_zero_leaves_every_prepared_tensor_and_the_loss_unchanged():
    old, new = two_segments(FORMAT_3), two_segments(FORMAT_4)
    a, b = sources(old, "g", 0.0), sources(new, "g", 0.0)
    for left, right in zip(a, b, strict=True):
        for name in ("probabilities", "targets", "raw_advantages"):
            assert torch.equal(getattr(left, name), getattr(right, name)), name
    first, second = batch(old, a, "g"), batch(new, b, "g")
    assert torch.equal(first.advantages, second.advantages)
    with_hp = batch(new, sources(new, "g", 0.003), "g")
    assert not torch.equal(with_hp.advantages, second.advantages)

    def loss(prepared: Any) -> torch.Tensor:
        [minibatch] = prepared.minibatches
        t = minibatch.tensors
        rows, width = t["old_probabilities"].shape
        torch.manual_seed(5)
        scores = torch.randn(rows, width).masked_fill(~t["catalog_valid"], -torch.inf)
        logits = torch.randn(rows, 51).masked_fill(~t["outcome_valid"], -torch.inf)
        value, *_ = batched_segment_loss(
            scores, logits, torch.randn(rows), t, clip=0.2, entropy_coef=0.01,
            value_coef=0.2, terminal_coef=0.1, hp_coef=0.1, multi=minibatch.policy_rows,
            td_rows=minibatch.td_rows, terminal_rows=max(minibatch.terminal_rows, 1),
            hp_rows=max(minibatch.hp_rows, 1))
        return value

    assert torch.equal(loss(first), loss(second))
    assert not torch.equal(loss(with_hp), loss(second))


def test_c_hp_refuses_an_older_segment_format_loudly():
    old = two_segments(FORMAT_3)
    with pytest.raises(FastIntakeError, match="fast_segment_without_hp_score"):
        sources(old, "g", 0.003)
    assert len(sources(old, "g", 0.0)) == 2  # c_hp = 0: the older formats as before


def test_c_hp_is_a_frozen_loss_key_off_by_default_and_reaches_every_envelope():
    assert LossConfig().hp_advantage_coef == 0.0
    frozen = json.loads((Path(__file__).resolve().parents[1] / "configs/rl5.json").read_text())
    assert frozen["loss"]["hp_advantage_coef"] == 0.0
    for bad in (-0.1, float("nan"), "0.003"):
        with pytest.raises(Exception, match="invalid_PPO_configuration"):
            LossConfig(hp_advantage_coef=bad)  # type: ignore[arg-type]
    segment = two_segments(FORMAT_4)[0]
    intake = FastSegmentIntake(run="run-1", generation="gen-1", hp_advantage_coef=0.003)
    a = attest("g")
    made = intake._envelope(type("S", (), {"header": segment.header, "data": segment.data,
                                           "message": {"sha256": segment.sha256}})(), a)
    assert made["hp_advantage_coef"] == 0.003
    # A checkpoint from before c_hp existed resumes as c_hp = 0, and only as that.
    from dataclasses import asdict

    from stpd.rl.segment_learner import _resume_config
    config = {"loss": asdict(LossConfig()), "segments": {}}
    before = {"loss": {k: v for k, v in config["loss"].items() if k != "hp_advantage_coef"},
              "segments": {}}
    assert _resume_config(before) == _resume_config(config)
    changed = {"loss": {**config["loss"], "hp_advantage_coef": 0.003}, "segments": {}}
    assert _resume_config(before) != _resume_config(changed)
