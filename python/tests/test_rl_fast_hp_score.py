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
from stpd.rl.fast_assembly import (
    DEFAULT_HP_WEIGHTS,
    AssemblyError,
    FastSegmentAssembler,
    hp_advantages,
)
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
from stpd.rl.loss import HP_ADVANTAGE_SCOPES, LossConfig, batched_segment_loss
from stpd.rl.prepared_batch import BatchMember, PrepareRequest

INSTANCE = "node-a.000000000001"
# `two_segments(FORMAT_3, pinned identity)` sealed on the base before W2 (568da206):
# same game, same ids.
GOLDEN_FORMAT_3_BOOTSTRAP = "ce5fdd65c9052c3f9093850267201faa4d367efdc0045d62807f262627f1e9bd"
GOLDEN_FORMAT_3_TERMINAL = "6392aa3fe3c7c765c6e529628368c1127f4f05412806625e8c6e286e86ed3fe9"


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

    def __init__(self, segment_format: str = FORMAT_4, *, compiler: Any = COMPILER,
                 handoff: Any = None, compiler_identity: Any = None,
                 hp_weights: dict[str, float] | None = None) -> None:
        self.sealed: list[Any] = []
        self.closed: list[Any] = []
        frame = compiler.compile(public_state())
        self.raw, self.width = encode_row(frame), len(frame.choices)
        self.hp_score = segment_format == FORMAT_4
        self.assembler = FastSegmentAssembler(
            assembler_id="node-a", compiler_identity=compiler_identity or compiler.identity,
            emit=self.sealed.append,
            terminal_labeler=lambda _summary: {
                "class": 3, "reward": float(class_reward_values()[3])},
            on_closed=self.closed.append, segment_format=segment_format, verify_every=1,
            hp_weights=hp_weights)
        self.assembler.instance_id = INSTANCE
        self.assembler.offer_mu(handoff or mu(1))
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
             status: str = "completed", encounter: str | None = "normal",
             public_hp: Any = None) -> dict[str, Any]:
        """One decision; `hp=None` sends no step outcome (the row is never played).
        A combat row carries `encounter` as the aggregator sends it (A2), and
        `public_hp` (its own state's public HP, B) when one is given."""
        self.request += 1
        step = self.steps[game]
        self.steps[game] += 1
        row = {"request_id": self.request, "game_instance_id": game, "step_index": step,
               "consumption_id": f"c-{self.request}", "catalog_len": self.width,
               "catalog_sha256": "c" * 64, "observation_sha256": "d" * 64,
               "in_combat": combat, "floor": 1, "encounter": encounter if combat else None,
               "payload": {"offset": 0, "length": len(self.raw),
                           "row_sha256": hashlib.sha256(self.raw).hexdigest()}}
        if public_hp is not None:
            row["public_hp"] = public_hp
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
    # Per encounter (A2): net HP change (58-70) + (82-80) = -10, then a death from 58: -58.
    none = {"battles": 0, "deaths": 0, "hp_change": 0}
    by_encounter = {"normal": {"battles": 2, "deaths": 1, "hp_change": -68},
                    "elite": none, "boss": none, "unknown": none}
    assert g.scores() == {"battles": 2, "battles_unscored": 0, "potions": 1, "deaths": 1,
                          "rows_unscored": 0, "ticket_ends": 0, "by_encounter": by_encounter,
                          "by_arm": {"control": by_encounter}}  # RL5-METHODS R: per arm


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
def envelope(segment: Any, game: str, coef: float,
             scope: str = "combat_ticket") -> dict[str, Any]:
    a = attest(game)
    return fast_envelope(segment.header, size=len(segment.data), digest=segment.sha256,
                         run="run-1", attestation={key: a[key] for key in (
                             "actor", "intent_id", "seed", "training_split")},
                         gae_lambda=0.99, hp_advantage_coef=coef, hp_advantage_scope=scope)


def sources(segments: list[Any], game: str, coef: float,
            scope: str = "combat_ticket", compiler: Any = COMPILER) -> list[Any]:
    return [decode_fast_packet(s.data, envelope=envelope(s, game, coef, scope),
                               compiler=compiler)[0]
            for s in segments]


def outcome_advantages(segment: Any) -> torch.Tensor:
    """The segment's raw outcome advantage, as the learner computes it without c_hp."""
    header, seal = segment.header, segment.header["seal"]
    tail = None if seal["terminal"] else torch.tensor(seal["bootstrap"]["outcome_probabilities"])
    return categorical_lambda_targets(
        torch.tensor([r["outcome_probabilities"] for r in header["rows"]]), tail,
        terminal_class=seal["terminal"]["class"] if seal["terminal"] else None)[1]


def hp_column(segment: Any) -> torch.Tensor:
    return torch.tensor([0.0 if v is None else v for v in segment.header["hp_adv"]])


def normalised(value: torch.Tensor) -> torch.Tensor:
    return (value - float(value.mean())) / max(float(value.std(unbiased=False)), 1e-8)


def batch(segments: list[Any], prepared: list[Any], game: str,
          compiler: Any = COMPILER) -> Any:
    count = sum(len(s.frames) for s in prepared)
    request = PrepareRequest(
        RunSequence("run-1", "gen-1", 1),
        tuple(BatchMember(s.segment_id, HotPayloadRef(f"run-1/gen-1/{i}", "f" * 64, 1),
                          game, len(s.header["rows"]), s.header["mu"]["snapshot_sha256"],
                          "intent-" + game) for i, s in enumerate(segments)),
        (), (tuple(range(count)),), (), compiler.identity, random.Random(1).getstate(),
        128 * 1024**2)
    return bp.assemble(request, tuple(prepared), microbatch_size=16, deduplicate_text=True,
                       prepared_layout="rows-v2")


def full_game(g: HpGame, game: str = "g") -> None:
    """A bootstrap segment (a combat across its end) and a terminal segment (a death)."""
    g.open(game)
    g.step(game, combat=False, hp=70)
    for index in range(70):
        g.step(game, combat=index % 20 != 19, hp=70 - index // 2, potion=index % 9 == 4,
               predicted=0.5 + 0.0078125 * (index % 5))
    g.step(game, combat=True, hp=0, terminal="defeat")
    g.close(game, "terminal")


def two_segments(segment_format: str, compiler_identity: Any = None) -> list[Any]:
    g = HpGame(segment_format, compiler_identity=compiler_identity)
    full_game(g)
    assert [s.kind for s in g.sealed] == ["bootstrap", "terminal"]
    return g.sealed


def test_format_3_with_c_hp_off_seals_the_same_bytes_as_before_the_hp_score():
    # Format 3 (the launcher default) with c_hp off: not one sealed byte changed by W2.
    # The header's compiler_sha256 binds the installed torch/tokenizers/safetensors
    # versions (torch is "2.13.0+cpu" on Linux), so the golden pins that one field.
    identity = {**COMPILER.identity, "compiler_sha256": "0" * 64}
    assert [hashlib.sha256(s.data).hexdigest()
            for s in two_segments(FORMAT_3, identity)] == [
        GOLDEN_FORMAT_3_BOOTSTRAP, GOLDEN_FORMAT_3_TERMINAL]


def test_c_hp_is_added_to_the_outcome_advantage_once_before_the_one_normalisation():
    segments = two_segments(FORMAT_4)  # a full game: the "all_combats" experiment arm
    coef = 0.003
    prepared = sources(segments, "g", coef, "all_combats")
    raw = []
    for segment, source in zip(segments, prepared, strict=True):
        outcome_advantage, hp = outcome_advantages(segment), hp_column(segment)
        assert any(v is not None for v in segment.header["hp_adv"])
        assert torch.equal(source.raw_advantages, outcome_advantage + coef * hp)
        raw.append((outcome_advantage, hp))
    result = batch(segments, prepared, "g")
    outcome_all = torch.cat([a for a, _ in raw])
    hp_all = torch.cat([h for _, h in raw])
    assert None in segments[0].header["hp_adv"]  # a non-combat row adds nothing
    mask = result.policy_mask
    assert bool(mask.all())  # every row here has two choices
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
    with_hp = batch(new, sources(new, "g", 0.003, "all_combats"), "g")
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


def test_c_hp_scores_single_combat_tickets_only_unless_the_arm_is_all_combats():
    full = two_segments(FORMAT_4)
    g = HpGame()
    single_combat(g)
    ticket = g.sealed
    assert [s.header["ticket"]["end"] for s in full + ticket] == ["game", "game", "combat"]
    for scope in HP_ADVANTAGE_SCOPES:
        for game, segments in (("g", full), ("t", ticket)):
            scored = game == "t" or scope == "all_combats"
            for segment, off, on in zip(segments, sources(segments, game, 0.0, scope),
                                        sources(segments, game, 0.003, scope), strict=True):
                assert torch.equal(off.raw_advantages, outcome_advantages(segment))
                assert torch.equal(on.raw_advantages, off.raw_advantages
                                   + (0.003 * hp_column(segment) if scored else 0.0)), scope
    # A full-game format-3 segment is still refused under either scope: c_hp > 0 never
    # reads an older format as A_hp = 0.
    for scope in HP_ADVANTAGE_SCOPES:
        with pytest.raises(FastIntakeError, match="fast_segment_without_hp_score"):
            sources(two_segments(FORMAT_3), "g", 0.003, scope)


def test_c_hp_and_its_scope_are_frozen_loss_keys_off_by_default_in_every_envelope():
    assert LossConfig().hp_advantage_coef == 0.0
    assert LossConfig().hp_advantage_scope == "combat_ticket"
    assert HP_ADVANTAGE_SCOPES == ("combat_ticket", "all_combats")
    frozen = json.loads((Path(__file__).resolve().parents[1] / "configs/rl5.json").read_text())
    assert frozen["loss"]["hp_advantage_coef"] == 0.0
    assert frozen["loss"]["hp_advantage_scope"] == "combat_ticket"
    for bad in ({"hp_advantage_coef": -0.1}, {"hp_advantage_coef": float("nan")},
                {"hp_advantage_coef": "0.003"}, {"hp_advantage_scope": "full_game"},
                {"hp_advantage_scope": None}):
        with pytest.raises(Exception, match="invalid_PPO_configuration"):
            LossConfig(**bad)  # type: ignore[arg-type]
    segment = two_segments(FORMAT_4)[0]
    intake = FastSegmentIntake(run="run-1", generation="gen-1", hp_advantage_coef=0.003,
                               hp_advantage_scope="all_combats")
    a = attest("g")
    made = intake._envelope(type("S", (), {"header": segment.header, "data": segment.data,
                                           "message": {"sha256": segment.sha256}})(), a)
    assert (made["hp_advantage_coef"], made["hp_advantage_scope"]) == (0.003, "all_combats")
    assert FastSegmentIntake(run="run-1", generation="gen-1")._envelope(
        type("S", (), {"header": segment.header, "data": segment.data,
                       "message": {"sha256": segment.sha256}})(), a)[
        "hp_advantage_scope"] == "combat_ticket"
    # A checkpoint from before c_hp existed resumes as c_hp = 0 with the default scope,
    # and only as that.
    from dataclasses import asdict

    from stpd.rl.segment_learner import _resume_config
    config = {"loss": asdict(LossConfig()), "segments": {}}
    before = {"loss": {k: v for k, v in config["loss"].items()
                       if k not in ("hp_advantage_coef", "hp_advantage_scope")},
              "segments": {}}
    assert _resume_config(before) == _resume_config(config)
    for change in ({"hp_advantage_coef": 0.003}, {"hp_advantage_scope": "all_combats"}):
        changed = {"loss": {**config["loss"], **change}, "segments": {}}
        assert _resume_config(before) != _resume_config(changed)


def test_the_learner_admits_prepares_and_steps_a_short_combat_ticket_segment(
        tmp_path, monkeypatch):
    from test_rl_fast_learner import admission, fast_learner, learner_mu, offer, queued

    from stpd.p1.frame_compiler import FrameCompiler
    coef = 0.003  # default scope: only the single-combat ticket gets c_hp * A_hp
    owner = fast_learner(tmp_path, monkeypatch, hp_advantage_coef=coef)
    try:
        g = HpGame(compiler=FrameCompiler(owner.model.vocabulary), handoff=learner_mu(owner))
        single_combat(g, "t")
        full_game(g, "g")
        ticket, bootstrap = g.sealed[0], g.sealed[1]
        assert ticket.kind == "bootstrap" and len(ticket.header["rows"]) == 3
        assert len(bootstrap.header["rows"]) == 64
        for index, name in enumerate(("t", "g"), 1):
            assert admission(owner, name, index)["admitted"]
        for segment in (ticket, bootstrap):
            offer(owner, segment)
        assert queued(owner) == [ticket.segment_id, bootstrap.segment_id]
        assert owner._prepare()  # one dataset: the 3-row ticket and the 64-row segment
        assert owner.active is not None and owner.active_batch is not None
        assert [r["segment_id"] for r in owner.active["segments"]] == [
            ticket.segment_id, bootstrap.segment_id]
        prepared = owner.active_batch
        ppo = torch.tensor([kind == "ppo" for kind in prepared.kinds])
        assert int(ppo.sum()) == 67 and bool(prepared.policy_mask[ppo].all())
        raw = torch.cat([outcome_advantages(ticket) + coef * hp_column(ticket),
                         outcome_advantages(bootstrap)])
        assert torch.allclose(prepared.advantages[ppo], normalised(raw), atol=1e-6)
        published: list[dict[str, Any]] = []
        publish = owner.exchange.publish_event
        monkeypatch.setattr(owner.exchange, "publish_event", lambda kind, **fields: (
            published.append({"kind": kind, **fields}), publish(kind, **fields))[1])
        result = owner._step()
        assert result["rows"] > 0 and result["approx_kl_mu_theta"] > 0
        # RL5-METHODS R: the same step's clip and KL per arm, from the step's per-row
        # facts and the sources' exploration tables (both games are the control arm).
        for future in list(owner.telemetry_futures):
            future.result()
        progress = [e for e in published if e["kind"] == "progress"]
        rows, kl, clipped, bad = progress[-1]["policy_by_arm"]["control"]
        assert set(progress[-1]["policy_by_arm"]) == {"control"}
        assert rows == result["multi_rows"] > 0 and bad == 0
        assert kl / rows == pytest.approx(result["approx_kl_mu_theta"], rel=1e-3)
        assert clipped / rows == pytest.approx(result["clip_fraction"], abs=1e-6)
    finally:
        owner.close()


# -- RL5-METHODS A2: the combat-type weight, the encounter, the run record, the chain ---
WEIGHTS = {"normal": 1.0, "elite": 1.0, "boss": 0.3}  # plan v3 §3.2: the first experiment


def test_the_weight_scales_the_hp_change_and_its_baseline_never_potion_or_death_points():
    survive = [(outcome(65), 62.5, False), (outcome(60), 50.0, True),
               (outcome(58, 82), 56.25, False)]
    died = [(outcome(40, 75), 45.0, False), (outcome(3, 75, terminal="defeat"), 20.0, True)]
    # w = 1 (every type's default) is pinned to the #538 floats in the next test.
    # Survive with a potion and a max-HP gain: h_0 = (58-70) + (82-80) = -10, baseline
    # 62.5-70; h_1 = (58-65) + 2 = -5, baseline 50-65; h_2 = 0, baseline 56.25-60.
    assert hp_advantages(facts(70), survive, 0.3) == pytest.approx(
        [0.3 * (-10 + 7.5) - 5, 0.3 * (-5 + 15) - 5, 0.3 * (0 + 3.75)])
    # Die: H_T = 0 and the combat's max-HP gain taken back are HP change (scaled), the -30
    # and the potion are not: h_0 = (0-50) + (75-70) - 5, h_1 = (0-40) + 0 - 5.
    assert hp_advantages(facts(50, 70), died, 0.3) == pytest.approx(
        [0.3 * (-50 + 5) - 5 - 30, 0.3 * (-45 + 20) - 5 - 30])
    # w = 0 leaves exactly the unscaled points.
    assert hp_advantages(facts(70), survive, 0.0) == [-5.0, -5.0, 0.0]
    assert hp_advantages(facts(50, 70), died, 0.0) == [-35.0, -35.0]


# #538's A_hp_t = returned_t - (predicted_t - H_t), `returned_t` the integer points, written
# out as floats (computed with the #538 code at 18a56576). Predictions that are not binary
# fractions make a rewrite that is equal in exact maths but rounds differently fail here
# (#548 review: `w (h - (predicted - H_t)) + unscaled` gives 50.93157673898216 below).
PINNED_538 = {
    # Three potions, survive: returned = 7 - 15, 3 - 10, 2 - 5.
    "potions": (facts(81, 90), [(outcome(85, 90), 22.068423261017834, True),
                                (outcome(86, 90), 30.1, True), (outcome(88, 90), 40.7, True)],
                [50.931576738982166, 47.9, 42.3]),
    # Max-HP gain and a potion, survive: returned = -13 + 3 - 5, -7 + 3 - 5, -2 + 3.
    "max_hp_gain": (facts(70), [(outcome(64), 61.7, False), (outcome(59), 48.3, True),
                                (outcome(57, 83), 52.9, False)],
                    [-6.700000000000003, 6.700000000000003, 7.100000000000001]),
    # Death (H_T = 0, the max-HP gain taken back, -30): returned = -50 + 5 - 5 - 35,
    # -41 - 35, -12 - 35.
    "death": (facts(50, 70), [(outcome(41, 75), 89.47253489848646, True),
                              (outcome(12, 75), 58.37624240224966, False),
                              (outcome(4, 75, "defeat"), 4.076967704366093, False)],
              [-124.47253489848646, -93.37624240224966, -39.07696770436609]),
}


@pytest.mark.parametrize("case", sorted(PINNED_538))
def test_w_1_is_the_538_hp_score_to_the_float(case):
    start, steps, expected = PINNED_538[case]
    assert hp_advantages(start, steps) == expected
    assert hp_advantages(start, steps, 1.0) == expected


def three_types(g: HpGame) -> None:
    g.open("g")
    g.step("g", combat=False, hp=70)                                      # map: 70/80
    g.step("g", combat=True, encounter="boss", hp=50, predicted=0.6)
    g.step("g", combat=True, encounter="boss", hp=40, predicted=0.5)      # boss dead at 40
    g.step("g", combat=False, hp=40)                                      # its reward
    g.step("g", combat=False, hp=80)                                      # the act heal
    g.step("g", combat=True, encounter="elite", hp=72, predicted=0.75)
    g.step("g", combat=False, hp=72)
    g.step("g", combat=True, encounter="unknown", hp=0, terminal="defeat")  # an event fight
    g.close("g", "terminal")


def test_a_boss_is_scored_before_the_act_heal_and_each_type_takes_its_weight():
    plain, weighted = HpGame(), HpGame(hp_weights={"normal": 0.5, "elite": 2.0, "boss": 0.3})
    three_types(plain)
    three_types(weighted)
    # Boss: from 70 to 40 (the last boss row's outcome, not the healed 80 later):
    # h = -30, baseline 60-70 -> -20; h = -10, baseline 50-50 -> -10. Elite from the
    # healed 80: h = -8, baseline 75-80 -> -3. Event fight (unknown: the normal weight),
    # death from 72: h = -72, baseline 50-72, -30 unscaled -> -80.
    assert plain.sealed[0].header["hp_adv"] == [None, -20.0, -10.0, None, None, -3.0, None,
                                                -80.0]
    assert weighted.sealed[0].header["hp_adv"] == pytest.approx(
        [None, -6.0, -3.0, None, None, -6.0, None, 0.5 * -50 - 30])
    none = {"battles": 0, "deaths": 0, "hp_change": 0}
    for g in (plain, weighted):  # counted unweighted, per encounter
        assert g.scores()["by_encounter"] == {
            "normal": none, "elite": {"battles": 1, "deaths": 0, "hp_change": -8},
            "boss": {"battles": 1, "deaths": 0, "hp_change": -30},
            "unknown": {"battles": 1, "deaths": 1, "hp_change": -72}}


def test_default_weights_seal_the_same_bytes_and_bad_weights_are_refused():
    same = [HpGame(), HpGame(hp_weights={"normal": 1, "elite": 1.0, "boss": 1.0})]
    for g in same:
        play_two_combats(g)
    assert same[0].sealed[0].data == same[1].sealed[0].data

    def assembler(segment_format: str, weights: Any) -> FastSegmentAssembler:
        return FastSegmentAssembler(assembler_id="node-a", emit=lambda _segment: None,
                                    compiler_identity=COMPILER.identity,
                                    segment_format=segment_format, hp_weights=weights)

    assert assembler(FORMAT_3, None).hp_weights == DEFAULT_HP_WEIGHTS
    assert assembler(FORMAT_3, {"normal": 1, "elite": 1, "boss": 1}).hp_weights == \
        DEFAULT_HP_WEIGHTS
    assert assembler(FORMAT_4, WEIGHTS).hp_weights == WEIGHTS
    for bad in ({"normal": 1.0, "elite": 1.0}, {**WEIGHTS, "unknown": 1.0},
                {**WEIGHTS, "boss": -0.1}, {**WEIGHTS, "boss": float("nan")},
                {**WEIGHTS, "boss": float("inf")}, {**WEIGHTS, "boss": "0.3"},
                {**WEIGHTS, "boss": True}):
        with pytest.raises(AssemblyError, match="invalid_hp_weights"):
            assembler(FORMAT_4, bad)
    with pytest.raises(AssemblyError, match="hp_weights_need_segment_format_4"):
        assembler(FORMAT_3, WEIGHTS)


@pytest.mark.parametrize("encounter", [None, "Boss", "monster"])
def test_a_format_4_combat_row_must_name_its_public_encounter(encounter):
    g = HpGame()
    g.open("g")
    g.step("g", combat=False, hp=70, encounter=None)  # off combat nothing is needed
    with pytest.raises(AssemblyError, match="hp_encounter_required"):
        g.step("g", combat=True, hp=60, encounter=encounter)
    three = HpGame(FORMAT_3)  # format 3 reads no encounter
    three.open("g")
    three.step("g", combat=True, hp=60, encounter=encounter)


SAVE_START = {"kind": "from_save", "save_sha256": "d" * 64, "act": 1, "floor": 16}


def boss_fight(g: HpGame, game: str, public_hp: Any = None) -> None:
    g.step(game, combat=True, encounter="boss", hp=60, predicted=0.625, public_hp=public_hp)
    g.step(game, combat=True, encounter="boss", hp=0, predicted=0.25, terminal="defeat")
    g.close(game, "terminal")


def test_a_save_start_loaded_into_a_boss_fight_starts_at_its_first_rows_public_hp():
    """#553 review B: a pre-Boss save loads onto the map, the Boss node is a forced
    step, so the fight has no row before it; its first row's public HP is the start,
    and it scores as the same fight after a row in a new run."""
    run, save, bare = HpGame(), HpGame(), HpGame()
    run.open("r")
    run.step("r", combat=False, hp=70)               # the rest site: the Boss starts at 70/80
    boss_fight(run, "r")
    save.open("s", start=SAVE_START)
    boss_fight(save, "s", public_hp={"hp": 70, "max_hp": 80})
    bare.open("b", start=SAVE_START)
    boss_fight(bare, "b")                            # no public_hp: no start fact
    # Death from 70: D = -70 - 30, baselines 62.5-70 and 25-60.
    assert run.sealed[0].header["hp_adv"] == [None, -92.5, -55.0]
    assert save.sealed[0].header["hp_adv"] == [-92.5, -55.0]
    assert save.scores()["by_encounter"]["boss"] == run.scores()["by_encounter"]["boss"] == {
        "battles": 1, "deaths": 1, "hp_change": -70}
    assert bare.sealed[0].header["hp_adv"] == [None, None]
    assert (bare.scores()["battles"], bare.scores()["battles_unscored"]) == (0, 1)
    # Per arm (RL5-METHODS R): a save start's combats count apart from the arm's new runs.
    assert set(save.scores()["by_arm"]) == {"control+save"}
    assert set(run.scores()["by_arm"]) == {"control"}
    assert save.scores()["by_arm"]["control+save"]["boss"] == {
        "battles": 1, "deaths": 1, "hp_change": -70}


def test_only_a_save_start_takes_its_first_combat_start_from_the_row():
    """#553 should-fix 2: the first-row public HP start is for `from_save` only; a new run
    whose first row were in combat stays unscored, as before the save starts."""
    g = HpGame()
    g.open("n")
    boss_fight(g, "n", public_hp={"hp": 70, "max_hp": 80})
    assert g.sealed[0].header["hp_adv"] == [None, None]
    assert (g.scores()["battles"], g.scores()["battles_unscored"]) == (0, 1)
    assert g.scores()["by_arm"] == {}


@pytest.mark.parametrize("public_hp", [
    {"hp": -1, "max_hp": 80}, {"hp": 70, "max_hp": 0}, {"hp": "70", "max_hp": 80},
    {"hp": 70}, {"hp": 70, "max_hp": 80, "block": 5}, [70, 80]])
def test_a_row_with_a_malformed_public_hp_is_refused(public_hp):
    g = HpGame()
    g.open("g", start=SAVE_START)
    with pytest.raises(AssemblyError, match="invalid_row"):
        g.step("g", combat=True, encounter="boss", hp=60, public_hp=public_hp)


@pytest.fixture(scope="module")
def link_model() -> Any:
    from stpd.p1.input import V2_INPUT_SPEC
    from stpd.p1.model import MStd, MStdExecution
    from stpd.p1.vocabulary import public_vocabulary
    torch.manual_seed(3)
    model = MStd(public_vocabulary(V2_INPUT_SPEC), MStdExecution(
        implementation="experimental-packed-v1", microbatch_size=4,
        scene_attention_area=67108864, text_attention_area=67108864, cpu_threads=1))
    model.enable_rl4_heads(7)
    return model.eval().requires_grad_(False)


def test_the_aggregator_hands_the_service_each_combat_rows_encounter_and_hp(link_model):
    """Env line -> node aggregator (encoder pool) -> service -> assembler.begin_row."""
    import os
    import tempfile

    import test_p1_fast_inference as link

    class Rows(link.FakeAssembler):
        def __init__(self) -> None:
            super().__init__()
            self.rows: list[dict[str, Any]] = []

        def begin_row(self, row: dict[str, Any], row_bytes: bytes) -> Any:
            self.rows.append(dict(row))
            return super().begin_row(row, row_bytes)

    assembler = Rows()
    service = link._service(link_model.vocabulary, link_model, assembler)
    path = os.path.join(tempfile.mkdtemp(), "link.sock")
    aggregator = link._aggregator(link_model.vocabulary, service, path)
    try:
        env = link.FakeEnv(path)
        env.send({"type": "hello", "env_process_id": "env-1", "slots": 1, "p1_revision": "x",
                  "input_spec": link.AGENT_SPEC})
        env.send({"type": "episode_open", "ascension": 0, "game_instance_id": "g1",
                  "actor_id": "actor-1", "sampling": link.SAMPLING, "single_choice": "score",
                  "start": {"kind": "new_run"}})
        fight = public_state()
        fight["combat"] = {"availability": "available", "content": {"encounter_type": "elite"}}
        fight["player"] = {"content": {"hp": 50, "max_hp": 70}}
        lines = [link._decision_line(1, "g1", 0, public_state()),
                 link._decision_line(2, "g1", 1, fight).replace(b'"in_combat":false',
                                                                 b'"in_combat":true')]
        for step, line in enumerate(lines):
            env.socket.sendall(line)
            assert env.reply()["type"] == "scored"
            env.send({"type": "step_outcome", "row_id": step + 1, "game_instance_id": "g1",
                      "step_index": step, "status": "completed", "receipt_sha256": "b" * 64,
                      "public_facts": {"act": 1, "floor": 3, "hp": 50, "max_hp": 70},
                      "terminal": None})
    finally:
        stats = aggregator.close()
        service.close()
    assert stats["failure"] is None
    assert [(row["in_combat"], row["encounter"], row["public_hp"])
            for row in assembler.rows] == [
        (False, None, None), (True, "elite", {"hp": 50, "max_hp": 70})]
    assert all(type(hp) is float for _, hp, _ in assembler.hp)  # the HP head, for format 4


def test_the_inference_bench_sends_each_combat_rows_public_encounter(link_model):
    """#548 review: the bench's rows carry `encounter` as the aggregator's do, so a
    format-4 bench run is not refused on every combat row."""
    import importlib.util

    from stpd.p1.rows_encoder import RowsEncoder

    spec = importlib.util.spec_from_file_location(
        "bench_rl_inference_3", Path(__file__).parents[1] / "tools" / "bench_rl_inference_3.py")
    assert spec is not None and spec.loader is not None
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    encoder = RowsEncoder(link_model.vocabulary)
    fight = public_state()
    fight["combat"] = {"availability": "available", "content": {"encounter_type": "boss"}}
    sent = []
    for observation in (public_state(), fight):
        row = bench.bench_row(encoder, json.dumps(observation).encode())
        assert row is not None
        sent.append(bench.wire_row(row[0], row[1], 1, "g1", 0, 0))
    assert [(wire["in_combat"], wire["encounter"]) for wire in sent] == [
        (False, None), (True, "boss")]


def mixed_game(g: HpGame, game: str = "g") -> None:
    """A full game: normal, elite and boss combats in the first 64-row segment, a death."""
    g.open(game)
    g.step(game, combat=False, hp=70)
    for index in range(70):
        g.step(game, combat=index % 20 != 19,
               encounter=("normal", "elite", "boss")[index // 20 % 3], hp=70 - index // 2,
               potion=index % 9 == 4, predicted=0.5 + 0.0078125 * (index % 5))
    g.step(game, combat=True, hp=0, terminal="defeat")
    g.close(game, "terminal")


def test_format_4_with_c_hp_and_weights_trains_full_games_through_the_handoff(
        tmp_path, monkeypatch):
    """Assembler (format 4, weights 1 / 1 / 0.3) -> shm handoff -> learner intake ->
    preparation (c_hp > 0, scope all_combats) -> one SGD step, with the HP share."""
    import multiprocessing

    from test_rl_fast_learner import admission, fast_learner, learner_mu, queued
    from test_rl_fast_source import _sender, closed_for

    from stpd.p1.frame_compiler import FrameCompiler
    coef = 0.003
    owner = fast_learner(tmp_path, monkeypatch, hp_advantage_coef=coef,
                         hp_advantage_scope="all_combats")
    try:
        compiler = FrameCompiler(owner.model.vocabulary)
        g = HpGame(compiler=compiler, handoff=learner_mu(owner), hp_weights=WEIGHTS)
        mixed_game(g)
        plain = HpGame(compiler=compiler, handoff=learner_mu(owner))
        mixed_game(plain)
        sealed = g.sealed
        assert [s.kind for s in sealed] == ["bootstrap", "terminal"]
        first = sealed[0]
        # Only the boss rows (segment rows 41-59) carry the 0.3.
        changed = [row for row, (a, b) in enumerate(zip(
            first.header["hp_adv"], plain.sealed[0].header["hp_adv"], strict=True)) if a != b]
        assert changed == list(range(41, 60))
        assert admission(owner, "g", 1)["admitted"]
        ours, theirs = multiprocessing.Pipe()
        process = multiprocessing.Process(target=_sender, args=(theirs, sealed,
                                                                closed_for(sealed)))
        process.start()
        theirs.close()
        reader = owner.attach_fast_handoff(ours)
        process.join(30)
        assert process.exitcode == 0
        reader.join(30)
        assert not reader.is_alive() and not owner.input_fenced
        assert queued(owner) == [s.segment_id for s in sealed]
        assert owner._prepare()  # one dataset: the 64-row segment
        prepared = owner.active_batch
        assert prepared is not None
        ppo = torch.tensor([kind == "ppo" for kind in prepared.kinds])
        assert int(ppo.sum()) == 64 and bool(prepared.policy_mask[ppo].all())
        raw = outcome_advantages(first) + coef * hp_column(first)
        assert torch.allclose(prepared.advantages[ppo], normalised(raw), atol=1e-6)
        hp, centred = coef * hp_column(first).double(), raw.double() - raw.double().mean()
        share = float(((hp - hp.mean()) * centred).mean() / centred.square().mean())
        normalization = prepared.normalization
        assert normalization.hp_std == pytest.approx(float(hp.std(unbiased=False)))
        assert normalization.hp_share == pytest.approx(share, rel=1e-4)
        assert 0 < normalization.hp_share < 1
        result = owner._step()
        assert result["rows"] > 0
        assert result["advantage_raw"]["hp_share"] == normalization.hp_share
    finally:
        owner.close()


def test_c_hp_zero_hands_no_hp_part_and_records_no_hp_share():
    old, new = two_segments(FORMAT_3), two_segments(FORMAT_4)
    for segments in (old, new):
        prepared = sources(segments, "g", 0.0)
        assert all(source.hp_advantages is None for source in prepared)
        normalization = batch(segments, prepared, "g").normalization
        assert (normalization.hp_std, normalization.hp_share) == (None, None)
    with_hp = batch(new, sources(new, "g", 0.003, "all_combats"), "g").normalization
    assert with_hp.hp_std is not None and with_hp.hp_share is not None
