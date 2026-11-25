"""RL5 third training: Boss progress (per-enemy accounting, baseline, format 5, learner)."""
from __future__ import annotations

import random
from typing import Any

import pytest
import torch
from test_p1_input_v2 import public_state
from test_rl_fast_hp_score import HpGame, link_model, sources  # noqa: F401 (fixture)
from test_rl_fast_source import COMPILER, mu

from stpd.p1.fast_aggregator import boss_enemies
from stpd.rl.boss_progress import (
    UNSCORED,
    BossBaselines,
    fight_progress,
    public_enemies,
)
from stpd.rl.fast_assembly import AssemblyError, FastSegmentAssembler
from stpd.rl.fast_segment import (
    FORMAT_4,
    FORMAT_5,
    SegmentFormatError,
    decode_segment,
    encode_segment_columns,
)
from stpd.rl.heads import class_reward_values

INSTANCE = "node-a.000000000001"


def enemy(entity: str, hp: int | None, max_hp: int | None, *, definition: str = "BOSS",
          minion: bool = False, mode: str = "normal") -> list[Any]:
    return [entity, definition, hp, max_hp, minion, mode]


def progress(tables: list[Any], won: bool = False, ascension: int = 0) -> Any:
    return fight_progress(tables, won=won, ascension=ascension)


# -- accounting --------------------------------------------------------------------
def test_a_single_boss_is_one_minus_hp_left_over_its_first_max_and_a_win_is_one():
    tables = [[enemy("e1", 100, 100)], [enemy("e1", 60, 100)], [enemy("e1", 40, 100)]]
    assert progress(tables) == (pytest.approx(0.6), None, "a0:BOSS")
    assert progress(tables, won=True) == (1.0, None, "a0:BOSS")
    # a heal counts as it is (progress goes back), never above the first max
    healed = [*tables, [enemy("e1", 70, 100)]]
    assert progress(healed)[0] == pytest.approx(0.3)


def test_minions_never_count_their_death_is_no_progress_and_their_arrival_no_setback():
    start = [enemy("b", 200, 200, definition="QUEEN"),
             enemy("m1", 30, 30, definition="GUARD", minion=True)]
    tables = [start,
              [enemy("b", 150, 200, definition="QUEEN")],                # the minion died
              [enemy("b", 150, 200, definition="QUEEN"),                 # a new minion
               enemy("m2", 40, 40, definition="GUARD", minion=True)]]
    value, reason, key = progress(tables)
    assert (value, reason, key) == (pytest.approx(0.25), None, "a0:QUEEN")
    assert progress(tables[:2])[0] == progress(tables)[0]  # arrival: no step back
    only_minions = [[enemy("m1", 30, 30, minion=True)]]
    assert progress(only_minions)[1] == "no_target"
    flipped = [start, [enemy("b", 150, 200, definition="QUEEN"),
                       enemy("m1", 30, 30, definition="GUARD", minion=False)]]
    assert progress(flipped)[1] == "minion_changed"


def test_kaiser_crab_both_claws_count_and_a_claw_gone_is_dead():
    first = [enemy("c", 200, 200, definition="CRUSHER"), enemy("r", 190, 190, definition="ROCKET")]
    tables = [first, [enemy("c", 100, 200, definition="CRUSHER"),
                      enemy("r", 190, 190, definition="ROCKET")],
              [enemy("r", 95, 190, definition="ROCKET")]]               # the crusher is gone
    assert progress(tables, ascension=10) == (pytest.approx(1 - 95 / 390), None,
                                               "a10:CRUSHER+ROCKET")


def test_waterfall_infinite_bar_is_hp_done_and_an_infinite_first_bar_is_unscored():
    tables = [[enemy("w", 250, 250, definition="WATERFALL_GIANT")],
              [enemy("w", 80, 250, definition="WATERFALL_GIANT")],
              [enemy("w", None, None, definition="WATERFALL_GIANT",
                     mode="infinite_without_numbers")]]
    assert progress(tables) == (1.0, None, "a0:WATERFALL_GIANT")
    numbers = [*tables[:2], [enemy("w", 100_000_000, 100_000_000,
                                   definition="WATERFALL_GIANT")]]
    assert progress(numbers)[0] == 1.0  # >= 1e8 shown as "normal" is the same bar
    assert progress([tables[2]])[1] == "infinite_at_first"


TS = "TEST_SUBJECT"


def subject(hp: int, max_hp: int) -> list[Any]:
    return [enemy("t", hp, max_hp, definition=TS)]


def test_the_a0_test_subject_is_one_enemy_with_three_lives_and_progress_never_falls():
    # 100 -> 200 -> 300 (denominator 600); an empty list ends a life, it is no death.
    fight = [subject(100, 100), subject(30, 100), [], subject(200, 200),
             subject(120, 200), subject(300, 300), subject(280, 300)]
    values = [progress(fight[:n])[0] for n in range(1, len(fight) + 1)]
    assert values == pytest.approx([0.0, 70 / 600, 100 / 600, 100 / 600, 180 / 600,
                                    300 / 600, 320 / 600])
    assert values == sorted(values)
    # the next life without an empty list between is the same accounting
    direct = [subject(100, 100), subject(200, 200), subject(150, 200)]
    assert progress(direct)[0] == pytest.approx(150 / 600)  # life 1 done, 50 of life 2
    assert progress(fight, won=True)[0] == 1.0
    # back from an empty list with the same life, or a life skipped: not certain
    assert progress([subject(100, 100), [], subject(100, 100)])[1] == \
        "test_subject_phase"
    assert progress([subject(100, 100), subject(300, 300)])[1] == "test_subject_phase"
    assert progress([subject(150, 150)])[1] == "test_subject_phase"   # no such life
    # a fight first seen in its second life (a save start): the first life is done
    assert progress([subject(200, 200)])[0] == pytest.approx(100 / 600)


def test_an_a10_test_subject_is_never_scored_only_counted():
    assert progress([subject(110, 110)], ascension=10) == (
        None, "test_subject_ascension", "a10:TEST_SUBJECT")


@pytest.mark.parametrize("tables,reason", [
    ([], "enemies_missing"),
    ([[enemy("e", 10, 10)], None], "enemies_missing"),
    ([[enemy("e", 10, 10)], [enemy("e", 5, 10), enemy("x", 10, 10)]], "new_enemy"),
    ([[enemy("e", 10, 10), enemy("f", 10, 10)], [enemy("e", 5, 10)],
      [enemy("e", 5, 10), enemy("f", 10, 10)]], "enemy_returned"),
    ([[enemy("e", 10, 10)], [enemy("e", 5, 12)]], "max_hp_changed"),
    ([[enemy("e", None, 10)]], "hp_hidden"),
    ([[enemy("e", 10, 10)], [enemy("e", None, 10)]], "hp_hidden"),
    ([[enemy("e", 10, 10), enemy("e", 10, 10)]], "duplicate_entity"),
    # only the Waterfall Giant's infinite bar is "hp done" (review 11-26)
    ([[enemy("q", 100, 100, definition="QUEEN")],
      [enemy("q", None, None, definition="QUEEN", mode="infinite_without_numbers")]],
     "infinite_not_waterfall"),
    ([[enemy("q", 100, 100)], [enemy("q", 999_999_999, 999_999_999)]], "infinite_not_waterfall"),
    # a negative claw never offsets the other one; hp above max is no reading either
    ([[enemy("c", 100, 100, definition="CRUSHER"), enemy("r", 100, 100, definition="ROCKET")],
      [enemy("c", -10, 100, definition="CRUSHER"), enemy("r", 5, 100, definition="ROCKET")]],
     "hp_inconsistent"),
    ([[enemy("e", 10, 10)], [enemy("e", 11, 10)]], "hp_inconsistent"),
    ([[enemy("e", 12, 10)]], "hp_inconsistent"),
    ([[enemy("e", 0, 0)]], "hp_inconsistent"),
])
def test_a_fight_that_cannot_be_read_with_certainty_is_unscored_with_its_reason(tables, reason):
    value, got, _key = progress(tables)
    assert value is None and got == reason and reason in UNSCORED


def test_the_row_table_is_the_public_enemy_list():
    observation = public_state()
    observation["combat"] = {"availability": "available", "content": {
        "encounter_type": "boss", "enemies": [
            {"entity_id": "e1", "definition_id": "QUEEN", "hp": 90, "max_hp": 100,
             "hp_display_mode": "normal", "statuses": []},
            {"entity_id": "e2", "definition_id": "GUARD", "hp": 9, "max_hp": 9,
             "hp_display_mode": "normal",
             "statuses": [{"definition_id": "MINION_POWER", "amount": 1}]}]}}
    table = [["e1", "QUEEN", 90, 100, False, "normal"], ["e2", "GUARD", 9, 9, True, "normal"]]
    assert boss_enemies(observation, True) == table
    assert boss_enemies(observation, False) is None              # off combat
    observation["combat"]["content"]["encounter_type"] = "elite"
    assert boss_enemies(observation, True) is None               # Boss fights only
    observation["combat"]["content"]["encounter_type"] = "unknown"
    assert boss_enemies(observation, True) == table              # a combat's first read
    assert public_enemies(None) is None
    # any other shape is "no table", never an exception (the encoder pool lives on)
    one = observation["combat"]["content"]["enemies"][0]
    for bad in ({**one, "statuses": 1}, {**one, "statuses": [3]}, {**one, "hp": "9"},
                {**one, "entity_id": None}, 7):
        assert public_enemies([bad]) is None
    observation["combat"] = {"content": 5}
    assert boss_enemies(observation, True) is None


# -- baseline ----------------------------------------------------------------------
def test_the_baseline_is_read_before_the_fight_and_is_the_row_weighted_running_mean():
    b = BossBaselines(decay=0.25)
    assert b.baseline("a0:X") is None
    seen = []
    fights = ((0.2, 10), (0.4, 30), (0.6, 10), (0.8, 50), (1.0, 20), (0.0, 10))
    for value, rows in fights:
        seen.append(b.baseline("a0:X"))
        b.add("a0:X", value, rows)
    # the row-weighted mean of the first 1/decay = 4 fights, then sums kept x 0.75
    s, r = 2 + 12 + 6 + 40, 100
    assert seen[0] is None and seen[1:] == pytest.approx(
        [0.2, 14 / 40, 20 / 50, s / r, (0.75 * s + 20) / (0.75 * r + 20)])
    s, r = 0.75 * s + 20, 0.75 * r + 20
    assert b.baseline("a0:X") == pytest.approx(0.75 * s / (0.75 * r + 10))
    assert b.baseline("a0:Y") is None  # another Boss has its own
    with pytest.raises(ValueError):
        BossBaselines(decay=0)
    with pytest.raises(ValueError):
        b.add("a0:X", 0.5, 0)


def test_the_score_is_mean_zero_per_row_when_long_fights_go_further():
    # Rows and progress correlated (a long fight goes further): the per-fight mean of the
    # score is then not zero, the per-row mean is.
    rng = random.Random(7)
    b = BossBaselines(decay=0.05)
    per_row = {"a0:A": [0.0, 0], "a0:B": [0.0, 0]}
    for _ in range(40_000):
        key = rng.choice(list(per_row))
        value = rng.betavariate(2, 5) if key == "a0:A" else rng.betavariate(5, 2)
        rows = 5 + int(60 * value) + rng.randrange(10)
        base = b.baseline(key)
        if base is not None:
            per_row[key][0] += rows * (value - base)
            per_row[key][1] += rows
        b.add(key, value, rows)
    for total, rows in per_row.values():
        assert abs(total / rows) < 0.01


# -- the assembler and format 5 ----------------------------------------------------
class BossGame(HpGame):
    """HpGame with the format-5 Boss score; each step may carry its row's enemy table."""

    def __init__(self, segment_format: str = FORMAT_5, **boss: Any) -> None:
        self.sealed, self.closed = [], []
        frame = COMPILER.compile(public_state())
        from stpd.p1.rows_encoder_wire import encode_row

        self.raw, self.width = encode_row(frame), len(frame.choices)
        self.hp_score = True
        self.assembler = FastSegmentAssembler(
            assembler_id="node-a", compiler_identity=COMPILER.identity,
            emit=self.sealed.append,
            terminal_labeler=lambda _summary: {
                "class": 3, "reward": float(class_reward_values()[3])},
            on_closed=self.closed.append, segment_format=segment_format, verify_every=1,
            **({"boss_progress": boss} if boss else {}))
        self.assembler.instance_id = INSTANCE
        self.assembler.offer_mu(mu(1))
        self.request = 0
        self.steps = {}
        self.table: Any = None
        begin = self.assembler.begin_row

        def begin_row(row: dict[str, Any], raw: bytes) -> Any:
            if self.table is not None:
                row["enemies"] = self.table
            return begin(row, raw)

        self.assembler.begin_row = begin_row  # type: ignore[method-assign]
        self.combat_end: Any = None
        outcome = self.assembler.step_outcome

        def step_outcome(message: dict[str, Any]) -> Any:
            if self.combat_end is not None:
                message["combat_end"] = self.combat_end
            return outcome(message)

        self.assembler.step_outcome = step_outcome  # type: ignore[method-assign]

    def boss(self, game: str, hp: int, table: Any, *, encounter: str = "boss",
             combat_end: Any = None, **kw: Any) -> None:
        self.table, self.combat_end = table, combat_end
        self.step(game, combat=True, hp=hp, encounter=encounter, **kw)
        self.table = self.combat_end = None


def boss_game(g: BossGame, game: str = "g", left: tuple[int, ...] = (60, 40),
              end: str = "victory") -> None:
    """A normal combat, then a Boss fight (100 max) that ends with `end`."""
    g.open(game)
    g.step(game, combat=False, hp=70)
    g.step(game, combat=True, hp=65)
    g.step(game, combat=False, hp=65)
    boss = getattr(g, "boss", lambda game, hp, _table, **kw: g.step(
        game, combat=True, hp=hp, encounter="boss", **kw))   # format 4: no table
    boss(game, 60, [enemy("e1", 100, 100)])
    for hp in left:
        boss(game, 50, [enemy("e1", hp, 100)])
    if end == "victory":
        g.step(game, combat=False, hp=50)   # the fight was won: the reward screen
        g.close(game, "stop")
    else:
        boss(game, 0, [enemy("e1", left[-1], 100)], terminal="defeat")
        g.close(game, "terminal")


def test_format_5_is_format_4_plus_the_table_and_progress_columns():
    four, five = HpGame(FORMAT_4), BossGame()
    for g in (four, five):
        boss_game(g, end="defeat")
    left, right = four.sealed[0].header, five.sealed[0].header
    assert right["enemies"] == [None, None, None, [enemy("e1", 100, 100)],
                                [enemy("e1", 60, 100)], [enemy("e1", 40, 100)],
                                [enemy("e1", 40, 100)]]
    assert right["boss_progress"] == [None] * 3 + [pytest.approx(0.6)] * 4
    assert right["boss_adv"] == [None] * 7  # coef 0: nothing to add
    rest = {k: v for k, v in right.items() if k not in ("enemies", "boss_progress", "boss_adv")}
    assert {**left, "format": FORMAT_5} == rest
    assert five.sealed[0].data[:8] == b"STPDFS05"
    assert decode_segment(five.sealed[0].data)[0] == right
    assert decode_segment(four.sealed[0].data)[1] == decode_segment(five.sealed[0].data)[1]
    report = five.assembler.report()["boss_progress"]
    assert report["battles"] == 1 and report["baseline_missing"] == 1
    assert report["by_boss"] == {"a0:BOSS": {"battles": 1, "wins": 0,
                                             "progress": pytest.approx(0.6)}}


def test_coef_zero_trains_exactly_as_format_4_and_coef_adds_coef_times_progress_minus_baseline():
    four, off, on = HpGame(FORMAT_4), BossGame(), BossGame(coef=0.05, decay=0.5)
    for g in (four, off, on):
        boss_game(g, "g1", left=(60, 40), end="defeat")   # first: no baseline (counted)
        boss_game(g, "g2", left=(70, 20), end="defeat")   # 0.8 against 0.6
    for game, (a, b, c) in enumerate(zip(four.sealed, off.sealed, on.sealed, strict=True)):
        name = f"g{game + 1}"
        base, same, scored = (sources([s], name, 0.003, "all_combats")[0] for s in (a, b, c))
        for field in ("probabilities", "targets", "raw_advantages"):
            assert torch.equal(getattr(base, field), getattr(same, field)), field
        extra = [0.0 if v is None else v for v in c.header["boss_adv"]]
        assert torch.equal(scored.raw_advantages, base.raw_advantages
                           + torch.tensor(extra, dtype=base.raw_advantages.dtype))
    assert on.sealed[0].header["boss_adv"] == [None] * 7
    assert on.sealed[1].header["boss_adv"] == [None] * 3 + [pytest.approx(0.05 * 0.2)] * 4
    report = on.assembler.report()["boss_progress"]
    assert (report["battles"], report["baseline_missing"], report["rows_scored"]) == (2, 1, 4)


def test_a_won_fight_is_one_an_unreadable_one_is_counted_and_an_unended_one_unfinished():
    g = BossGame(coef=0.01, decay=0.05)
    boss_game(g, "w", end="victory")
    assert g.sealed[0].header["boss_progress"][3:] == [1.0, 1.0, 1.0, None]  # + reward row
    g.open("x")                                       # no table on a Boss row
    g.step("x", combat=True, hp=60, encounter="boss")
    g.step("x", combat=False, hp=60)
    g.close("x", "stop")
    g.open("u")                                       # the game closed inside the fight
    g.boss("u", 60, [enemy("e1", 100, 100)])
    g.close("u", "stop")
    report = g.assembler.report()["boss_progress"]
    assert report["rows_without_enemies"] == 1 and report["unscored"]["enemies_missing"] == 1
    assert report["unfinished"] == 1 and report["battles"] == 1


@pytest.mark.parametrize("boss", [{"coef": 0.06, "decay": 0.05}, {"coef": -0.01, "decay": 0.05},
                                  {"coef": 0.01, "decay": 0}, {"coef": 0.01, "x": 1}])
def test_coef_above_the_cap_and_bad_settings_are_refused(boss):
    with pytest.raises(AssemblyError, match="invalid_boss_progress"):
        BossGame(**boss)


def test_boss_progress_needs_format_5():
    with pytest.raises(AssemblyError, match="boss_progress_needs_segment_format_5"):
        BossGame(FORMAT_4, coef=0.01, decay=0.05)


def test_format_5_refuses_a_table_off_combat_or_progress_without_a_table():
    g = BossGame()
    boss_game(g, end="defeat")
    header, rows = decode_segment(g.sealed[0].data)
    assert decode_segment(encode_segment_columns(header, rows, bound=True).data)[0] == header
    n = len(rows)
    for change in ({"enemies": [[enemy("e1", 1, 1)], *header["enemies"][1:]]},   # map row
                   {"enemies": [None, None, None, [["e1"]], *header["enemies"][4:]]},
                   {"boss_progress": [0.5, *header["boss_progress"][1:]]},       # no table
                   {"boss_progress": [None] * 3 + [1.5] * 4},
                   {"boss_adv": [None] * 3 + [float("nan")] * 4},
                   {"boss_adv": [1.0] + [None] * (n - 1)}):
        with pytest.raises(SegmentFormatError):
            encode_segment_columns({**header, **change}, rows, bound=True)


@pytest.mark.parametrize("boss_enemies", [False, True])
def test_the_aggregator_reads_enemy_tables_under_format_5_only_and_never_fails_on_them(
        link_model, boss_enemies):  # noqa: F811
    """A malformed public `statuses` (1, not a list) on a Boss row: off format 5 the
    aggregator never reads it; under format 5 that row has no table (its fight unscored),
    and the encoder pool keeps running."""
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
    aggregator = link._aggregator(link_model.vocabulary, service, path,
                                  boss_enemies=boss_enemies)
    good = [{"entity_id": "e1", "definition_id": "QUEEN", "hp": 90, "max_hp": 100,
             "hp_display_mode": "normal", "statuses": []}]
    bad = [{**good[0], "statuses": 1}]
    try:
        env = link.FakeEnv(path)
        env.send({"type": "hello", "env_process_id": "env-1", "slots": 1, "p1_revision": "x",
                  "input_spec": link.AGENT_SPEC})
        env.send({"type": "episode_open", "ascension": 0, "game_instance_id": "g1",
                  "actor_id": "actor-1", "sampling": link.SAMPLING, "single_choice": "score",
                  "start": {"kind": "new_run"}})
        lines = []
        for step, enemies in enumerate((good, bad)):
            fight = public_state()
            fight["combat"] = {"availability": "available",
                               "content": {"encounter_type": "boss", "enemies": enemies}}
            fight["player"] = {"content": {"hp": 50, "max_hp": 70}}
            lines.append(link._decision_line(step + 1, "g1", step, fight).replace(
                b'"in_combat":false', b'"in_combat":true'))
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
    assert stats["failure"] is None and len(assembler.rows) == 2
    if boss_enemies:
        assert [row["enemies"] for row in assembler.rows] == [
            [["e1", "QUEEN", 90, 100, False, "normal"]], None]
    else:
        assert not any("enemies" in row for row in assembler.rows)


def combat_end(act: int = 3, floor: int = 48) -> list[dict[str, Any]]:
    """A native combat_end fact as the fast env copies it onto the row whose step ended
    the combat (kernel.ts #collectCombatEnd; fields as in the env's own tests)."""
    return [{"index": 7, "act": act, "floor": floor, "room_kind": "boss",
             "encounter_id": "ENCOUNTER-BOSS", "result": "victory", "hp_start": 70,
             "hp_end": 41, "max_hp_start": 80, "max_hp_end": 80, "potions_used": 1,
             "turns": 9}]


def test_the_last_boss_won_and_the_run_ended_in_victory_is_one():
    # The run's last Boss: its last row's step kills it; that outcome carries the native
    # combat_end victory and the terminal victory, and no row follows (eval-win shape).
    g = BossGame(coef=0.05, decay=0.05)
    for name, end in (("a", "defeat"), ("b", "won")):
        g.open(name)
        g.step(name, combat=False, hp=70)
        g.boss(name, 70, [enemy("e1", 300, 300)])
        g.boss(name, 60, [enemy("e1", 120, 300)])
        if end == "defeat":
            g.boss(name, 0, [enemy("e1", 120, 300)], terminal="defeat")
        else:
            g.boss(name, 41, [enemy("e1", 15, 300)], terminal="victory",
                   combat_end=combat_end())
        g.close(name, "terminal")
    won = g.sealed[-1].header
    assert won["boss_progress"] == [None, 1.0, 1.0, 1.0]
    assert won["boss_adv"] == [None] + [pytest.approx(0.05 * (1.0 - 0.6))] * 3
    report = g.assembler.report()["boss_progress"]
    assert (report["battles"], report["unfinished"]) == (2, 0)
    assert report["by_boss"]["a0:BOSS"]["wins"] == 1


def test_a_boss_win_confirmed_by_combat_end_then_a_stop_is_one_not_unfinished():
    # The win's combat_end rides on the last Boss row; the game then stops before any
    # non-combat model row (review 11-26: was counted unfinished).
    g = BossGame(coef=0.05, decay=0.05)
    g.open("s")
    g.step("s", combat=False, hp=70)
    g.boss("s", 60, [enemy("e1", 100, 100)])
    g.boss("s", 55, [enemy("e1", 20, 100)], combat_end=combat_end(1, 17))
    g.close("s", "stop")
    assert g.sealed[-1].header["boss_progress"] == [None, 1.0, 1.0]
    report = g.assembler.report()["boss_progress"]
    assert (report["battles"], report["unfinished"], report["baseline_missing"]) == (1, 0, 1)


def test_a_boss_fight_first_read_as_unknown_is_still_scored_and_a_mixed_one_counted():
    g = BossGame()
    g.open("k")
    g.step("k", combat=False, hp=70)
    g.boss("k", 60, [enemy("e1", 100, 100)], encounter="unknown")   # first read
    g.boss("k", 55, [enemy("e1", 40, 100)])
    g.step("k", combat=False, hp=55)
    g.close("k", "stop")
    assert g.sealed[-1].header["boss_progress"] == [None, 1.0, 1.0, None]
    g.open("m")                                    # a row of another type in it
    g.step("m", combat=False, hp=70)
    g.boss("m", 60, [enemy("e1", 100, 100)], encounter="normal")
    g.boss("m", 55, [enemy("e1", 40, 100)])
    g.step("m", combat=False, hp=55)
    g.close("m", "stop")
    report = g.assembler.report()["boss_progress"]
    assert report["battles"] == 1 and report["unscored"]["encounter_mixed"] == 1
    assert report["by_boss"] == {"a0:BOSS": {"battles": 1, "wins": 1, "progress": 1.0}}


def test_the_reader_refuses_a_boss_adv_beyond_the_cap():
    g = BossGame(coef=0.05, decay=0.05)
    boss_game(g, "g1", end="defeat")
    boss_game(g, "g2", left=(70, 20), end="defeat")
    header, rows = decode_segment(g.sealed[-1].data)
    adv = header["boss_adv"]
    assert encode_segment_columns(header, rows, bound=True)  # 0.01: inside
    for value in (0.0501, -0.06, 1e300):
        bad = [None if a is None else value for a in adv]
        with pytest.raises(SegmentFormatError, match="segment_boss_progress"):
            encode_segment_columns({**header, "boss_adv": bad}, rows, bound=True)
