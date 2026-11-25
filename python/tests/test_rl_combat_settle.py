"""Combat-end settlement (`segments.combat_end_kappa`): combat rows' policy advantage from
G' = (1 - k) [in-battle trace ended at V(s_end)] + k G; value targets and noncombat rows keep G."""
from __future__ import annotations

import dataclasses
import hashlib
import itertools
import random
from typing import Any

import pytest
import torch
from test_rl_fast_source import COMPILER, Game, attest, closed_for, intake, message
from test_rl_fast_value_target import closed, distribution, one_hot, reference

from stpd.rl.fast_source import (
    COMBAT_SETTLE_KEY,
    FastIntakeError,
    _combat_settled,
    combat_settle_tails,
    decode_fast_packet,
    game_target_tails,
)
from stpd.rl.heads import categorical_lambda_targets, class_reward_values
from stpd.rl.learning_diagnostics import combat_settle_fields
from stpd.rl.segment_learner import _resume_config
from stpd.rl.segments import SegmentConfig

KAPPAS = (0.0, 0.5, 1.0)
R = class_reward_values().double()


def definition(rows: list[list[float]], flags: list[bool], end: list[float], lam: float,
               kappa: float, floors: list[Any] | None = None) -> torch.Tensor:
    """The lead's definition, written out per battle (consecutive combat rows on one floor):
    G' = (1 - k) C + k G on the rows of a battle whose next scored row is noncombat or on
    another floor (C ends at that row's p); G elsewhere (game end, gap, floor missing)."""
    floors = [1] * len(rows) if floors is None else floors
    ok = [type(f) is int and f >= 0 for f in floors]
    p = torch.tensor(rows, dtype=torch.float64)
    g = reference(rows, end, lam)
    out = g.clone()
    t = 0
    while t < len(rows):
        if not flags[t]:
            t += 1
            continue
        e = t
        while (e + 1 < len(rows) and flags[e + 1] and ok[e] and ok[e + 1]
               and floors[e + 1] == floors[e]):
            e += 1
        if e + 1 < len(rows) and (not flags[e + 1] or (ok[e] and ok[e + 1])):  # s_end
            c = p[e + 1].clone()
            for row in range(e, t - 1, -1):
                if row < e:
                    c = (1 - lam) * p[row + 1] + lam * c
                out[row] = (1 - kappa) * c + kappa * g[row]
        t = e + 1
    return out


def settled_parts(parts: list[Any], flags: list[list[bool]], lam: float, kappa: float,
                  terminal_class: int | None, floors: list[list[Any]] | None = None,
                  ) -> tuple[list[torch.Tensor], list[torch.Tensor], list[list[str]]]:
    """What decode does per segment: G (categorical_lambda_targets on the stitched tail),
    then G' (`_combat_settled` on the intake's settled tail)."""
    floors = [[1] * len(f) for f in flags] if floors is None else floors
    tails = game_target_tails(parts, lam)
    settles, _counts = combat_settle_tails(parts, flags, floors, tails, lam, kappa)
    targets, settled, states = [], [], []
    for (_i, rows, seal, terminal), (tail, _w, _c), (s_tail, state), f, fl in zip(
            parts, tails, settles, flags, floors, strict=True):
        p = torch.tensor(rows)
        g, _ = categorical_lambda_targets(
            p, None if terminal else torch.tensor(tail if tail is not None else seal),
            terminal_class=terminal_class if terminal else None, gae_lambda=lam)
        h, row_states = _combat_settled(p, g, f, fl, {"kappa": kappa, "tail": s_tail,
                                                      "state": state}, lam)
        targets.append(g)
        settled.append(h)
        states.append(row_states)
    return targets, settled, states


def split(rows: list[Any], cuts: tuple[int, ...]) -> list[list[Any]]:
    bounds = (0, *cuts, len(rows))
    return [rows[a:b] for a, b in itertools.pairwise(bounds)]


# --- the hand example: two battles, noncombat rows between, a terminal noncombat end ------

# rows: 0 nc, 1 c, 2 c, 3 nc (s_end 1), 4 c, 5 c, 6 nc (s_end 2), then the terminal class
CLASSES = [10, 20, 30, 40, 5, 15, 25]
FLAGS = [False, True, True, False, True, True, False]
TERMINAL = 48


@pytest.mark.parametrize("kappa", KAPPAS)
@pytest.mark.parametrize("cuts", [(), (3,), (2,), (1, 5), (3, 6), (1, 2, 4, 5)])
def test_hand_example_two_battles(kappa, cuts):
    lam = 0.5
    v = [float(R[c]) for c in CLASSES]
    z = float(R[TERMINAL])
    # by hand, lambda 0.5: G (whole game) and C (battle traces ended at V(s_end))
    g = [0.0] * 7
    g[6] = z
    for t in range(5, -1, -1):
        g[t] = 0.5 * v[t + 1] + 0.5 * g[t + 1]
    c = {2: v[3], 1: 0.5 * v[2] + 0.5 * v[3], 5: v[6], 4: 0.5 * v[5] + 0.5 * v[6]}
    expected = [c[t] * (1 - kappa) + kappa * g[t] if t in c else g[t] for t in range(7)]
    rows = [one_hot(k) for k in CLASSES]
    assert torch.allclose(definition(rows, FLAGS, one_hot(TERMINAL), lam, kappa) @ R,
                          torch.tensor(expected, dtype=torch.float64), atol=1e-12)
    pieces = split(rows, cuts)
    flags = split(FLAGS, cuts)
    parts = [(n, piece, one_hot(TERMINAL), n == len(pieces) - 1)
             for n, piece in enumerate(pieces)]
    targets, settled, states = settled_parts(parts, flags, lam, kappa, TERMINAL)
    g_rows = torch.cat(targets).double()
    h_rows = torch.cat(settled).double()
    p = torch.tensor(rows, dtype=torch.float64)
    # the value targets stay the whole-game G; the policy's advantage uses G'
    assert torch.allclose(g_rows @ R, torch.tensor(g, dtype=torch.float64), atol=1e-6)
    assert torch.allclose((h_rows - p) @ R, torch.tensor(expected, dtype=torch.float64)
                          - p @ R, atol=1e-6)
    assert [s for part in states for s in part] == [
        "noncombat", "settled", "settled", "noncombat", "settled", "settled", "noncombat"]
    for t in (0, 3, 6):  # noncombat rows: G' row is G, bit for bit
        assert torch.equal(h_rows[t], g_rows[t])
    if kappa == 1.0:
        assert torch.allclose(h_rows, g_rows, atol=1e-6)


@pytest.mark.parametrize("kappa", KAPPAS)
@pytest.mark.parametrize("lam", [0.99, 0.5, 1.0, 0.0])
@pytest.mark.parametrize("terminal", [True, False])
def test_every_split_of_a_random_game_matches_the_definition(kappa, lam, terminal):
    rng = random.Random(f"{kappa}-{lam}-{terminal}")
    n = 23
    rows = [distribution(rng) for _ in range(n)]
    flags = [rng.random() < 0.6 for _ in range(n)]
    flags[-3:] = [True, True, True]  # the game ends inside a battle
    floors = list(itertools.accumulate(rng.random() < 0.2 for _ in range(n)))
    floors[-3:] = [floors[-3]] * 3
    end = one_hot(7) if terminal else distribution(rng)
    whole = definition(rows, flags, end, lam, kappa, floors)
    for cuts in [(), (5,), (11, 12), (4, 9, 17, 20), (1, 2, 3, 22)]:
        pieces, fl = split(rows, cuts), split(flags, cuts)
        parts = [(i, piece, end if i == len(pieces) - 1 else distribution(rng),
                  terminal and i == len(pieces) - 1) for i, piece in enumerate(pieces)]
        _g, settled, states = settled_parts(parts, fl, lam, kappa, 7, split(floors, cuts))
        assert torch.allclose(torch.cat(settled).double(), whole, atol=2e-6)
        # the final battle has no s_end: terminal (G' = G) or lost (bootstrap end)
        assert [s for part in states for s in part][-3:] == [
            "terminal" if terminal else "lost"] * 3


def test_a_gap_after_a_battle_keeps_g_for_that_battle_and_is_lost():
    rng = random.Random(5)
    rows = [[distribution(rng) for _ in range(4)] for _ in range(3)]
    flags = [[False, False, True, True], [True, False, True, True], [False, True, True, False]]
    parts = [(0, rows[0], distribution(rng), False), (1, rows[1], distribution(rng), False),
             (3, rows[2], one_hot(7), True)]  # index 2 never arrived
    tails = game_target_tails(parts, 0.99)
    settles, counts = combat_settle_tails(parts, flags, [[1] * 4] * 3, tails, 0.99, 0.0)
    assert [state for _t, state in settles] == ["settled", "lost", "noncombat"]
    assert counts == {"end_noncombat": 2, "end_floor": 0, "lost": 1, "lost_floor": 0}
    targets, settled, states = settled_parts(parts, flags, 0.99, 0.0, 7)
    assert states[1] == ["settled", "noncombat", "lost", "lost"]
    assert torch.equal(settled[1][2:], targets[1][2:])  # lost battle: G exactly
    assert not torch.allclose(settled[0][2:], targets[0][2:])  # its s_end is part 1 row 1
    assert states[2] == ["noncombat", "settled", "settled", "noncombat"]


# --- omitted boundaries (--single-choice omit): a floor change between combat rows -------

# rows: 0 nc, 1-2 battle A (floor 1), 3-4 battle B (floor 2; A's post-battle states were
# all single-choice, so no scored row between), 5 nc, then the terminal class
OMIT_CLASSES = [10, 20, 30, 12, 22, 32]
OMIT_FLAGS = [False, True, True, True, True, False]
OMIT_FLOORS = [1, 1, 1, 2, 2, 2]


@pytest.mark.parametrize("kappa", KAPPAS)
@pytest.mark.parametrize("cuts", [(), (3,), (2,), (4,), (1, 3, 5)])
def test_a_floor_change_ends_battle_a_and_battle_b_settles_itself(kappa, cuts):
    lam = 0.5
    v = [float(R[c]) for c in OMIT_CLASSES]
    z = float(R[TERMINAL])
    g = [0.0] * 6
    g[5] = z
    for t in range(4, -1, -1):
        g[t] = 0.5 * v[t + 1] + 0.5 * g[t + 1]
    # A ends at row 2 with B's first row's prediction; B ends at row 4 with row 5's
    c = {2: v[3], 1: 0.5 * v[2] + 0.5 * v[3], 4: v[5], 3: 0.5 * v[4] + 0.5 * v[5]}
    expected = [c[t] * (1 - kappa) + kappa * g[t] if t in c else g[t] for t in range(6)]
    rows = [one_hot(k) for k in OMIT_CLASSES]
    pieces, flags, floors = split(rows, cuts), split(OMIT_FLAGS, cuts), split(OMIT_FLOORS, cuts)
    parts = [(n, piece, one_hot(TERMINAL), n == len(pieces) - 1)
             for n, piece in enumerate(pieces)]
    _t, settled, states = settled_parts(parts, flags, lam, kappa, TERMINAL, floors)
    p = torch.tensor(rows, dtype=torch.float64)
    assert torch.allclose((torch.cat(settled).double() - p) @ R,
                          torch.tensor(expected, dtype=torch.float64) - p @ R, atol=1e-6)
    assert torch.allclose(definition(rows, OMIT_FLAGS, one_hot(TERMINAL), lam, kappa,
                                     OMIT_FLOORS) @ R,
                          torch.tensor(expected, dtype=torch.float64), atol=1e-12)
    assert [x for part in states for x in part] == ["noncombat"] + ["settled"] * 4 + [
        "noncombat"]
    _s, counts = combat_settle_tails(parts, flags, floors, game_target_tails(parts, lam),
                                     lam, kappa)
    assert counts == {"end_noncombat": 1, "end_floor": 1, "lost": 0, "lost_floor": 0}


def test_combat_rows_on_one_floor_stay_one_battle():
    flags, floors = [False, True, True, True, True, False], [1, 3, 3, 3, 3, 3]
    rows = [one_hot(k) for k in OMIT_CLASSES]
    parts = [(0, rows[:3], one_hot(TERMINAL), False), (1, rows[3:], one_hot(TERMINAL), True)]
    pf, pfl = [flags[:3], flags[3:]], [floors[:3], floors[3:]]
    _s, counts = combat_settle_tails(parts, pf, pfl, game_target_tails(parts, 0.5), 0.5, 0.0)
    assert counts == {"end_noncombat": 1, "end_floor": 0, "lost": 0, "lost_floor": 0}
    _t, settled, _st = settled_parts(parts, pf, 0.5, 0.0, TERMINAL, pfl)
    assert torch.allclose(torch.cat(settled).double(),
                          definition(rows, flags, one_hot(TERMINAL), 0.5, 0.0, floors),
                          atol=1e-6)
    # k = 0: every row of the one battle ends at row 5's prediction (lam 0.5 trace)
    assert torch.cat(settled)[4].tolist() == pytest.approx(one_hot(OMIT_CLASSES[5]))


@pytest.mark.parametrize("cuts", [(), (1,), (2,)])
def test_battle_a_ending_in_death_keeps_g(cuts):
    flags, floors = [False, True, True], [1, 1, 1]
    rows = [one_hot(k) for k in OMIT_CLASSES[:3]]
    pieces = split(rows, cuts)
    parts = [(n, piece, one_hot(2), n == len(pieces) - 1) for n, piece in enumerate(pieces)]
    targets, settled, states = settled_parts(parts, split(flags, cuts), 0.5, 0.0, 2,
                                             split(floors, cuts))
    assert torch.equal(torch.cat(settled), torch.cat(targets))
    assert [x for part in states for x in part] == ["noncombat", "terminal", "terminal"]
    _s, counts = combat_settle_tails(parts, split(flags, cuts), split(floors, cuts),
                                     game_target_tails(parts, 0.5), 0.5, 0.0)
    assert counts == dict.fromkeys(counts, 0)


@pytest.mark.parametrize("cuts", [(), (2,), (3,)])
@pytest.mark.parametrize("missing", [2, 3])
def test_a_missing_floor_loses_that_battle_and_counts_it(cuts, missing):
    floors: list[Any] = list(OMIT_FLOORS)
    floors[missing] = None if missing == 2 else 2.0
    rows = [one_hot(k) for k in OMIT_CLASSES]
    pieces = split(rows, cuts)
    parts = [(n, piece, one_hot(TERMINAL), n == len(pieces) - 1)
             for n, piece in enumerate(pieces)]
    pf, pfl = split(OMIT_FLAGS, cuts), split(floors, cuts)
    _s, counts = combat_settle_tails(parts, pf, pfl, game_target_tails(parts, 0.5), 0.5, 0.0)
    assert counts == {"end_noncombat": 1, "end_floor": 0, "lost": 0, "lost_floor": 1}
    targets, settled, states = settled_parts(parts, pf, 0.5, 0.0, TERMINAL, pfl)
    flat = [x for part in states for x in part]
    lost = 3 if missing == 2 else 4  # a bad floor on B's first row loses B's row 3 too
    assert flat == ["noncombat", *["lost_floor"] * (lost - 1),
                    *["settled"] * (5 - lost), "noncombat"]
    assert torch.equal(torch.cat(settled)[1:lost], torch.cat(targets)[1:lost])  # keeps G
    assert torch.allclose(torch.cat(settled).double(), definition(
        rows, OMIT_FLAGS, one_hot(TERMINAL), 0.5, 0.0, floors), atol=1e-6)


def test_bad_kappa_and_mismatched_flags_are_refused():
    parts = [(0, [one_hot(1)], one_hot(7), True)]
    tails = game_target_tails(parts, 0.99)
    with pytest.raises(ValueError, match="kappa"):
        combat_settle_tails(parts, [[True]], [[1]], tails, 0.99, 1.5)
    with pytest.raises(ValueError, match="flags"):
        combat_settle_tails(parts, [[True, False]], [[1, 1]], tails, 0.99, 0.5)
    with pytest.raises(ValueError, match="flags"):
        combat_settle_tails(parts, [[True]], [[1, 1]], tails, 0.99, 0.5)
    p = torch.tensor([one_hot(1)])
    for bad in ({"kappa": 0.5, "tail": None, "state": "settled"},
                {"kappa": 0.5, "tail": None, "state": "noncombat"},
                {"kappa": 1.5, "tail": None, "state": "terminal"},
                {"kappa": 0.5, "tail": None, "state": "other"}):
        with pytest.raises(FastIntakeError, match="combat_settle"):
            _combat_settled(p, p, [True], [1], bad, 0.99)


def test_a_settled_tail_gets_the_value_tails_own_checks():
    p = torch.tensor([one_hot(1)])
    good = [float(x) for x in one_hot(3)]
    settled, _ = _combat_settled(p, p, [True], [1], {"kappa": 0.5, "tail": good,
                                                "state": "settled"}, 0.99)
    assert torch.equal(settled[0], torch.tensor(good))
    nan = [*good[:-1], float("nan")]
    for bad in (good[:-1], nan, [2 * x for x in good], [-x for x in good]):
        with pytest.raises(ValueError, match="invalid_segment_probability_mass"):
            _combat_settled(p, p, [True], [1],
                            {"kappa": 0.5, "tail": bad, "state": "settled"}, 0.99)
    for bad in ("x", [str(x) for x in good], [True] * 51):
        with pytest.raises(FastIntakeError, match="combat_settle"):
            _combat_settled(p, p, [True], [1],
                            {"kappa": 0.5, "tail": bad, "state": "settled"}, 0.99)


# --- the real path: assembler segments, "game" intake, decode ------------------------------

def combat(step: int) -> bool:
    # a battle across the 64-row seal (s_end row 70), one ending on a seal's last row
    # (s_end = the next segment's row 0) and one the terminal ends
    return 10 <= step < 70 or 100 <= step < 128 or step >= 130


def settle_game(name: str = "g1") -> list[Any]:
    g = Game()
    g.open(name)
    for step in range(64 * 2 + 9):
        g.step(name, in_combat=combat(step))
    g.step(name, terminal=True)  # row 137, in combat
    g.close(name, "terminal")
    return g.sealed


def released(tmp_path: Any, sealed: list[Any], **kwargs: Any) -> list[tuple[Any, Any]]:
    i = intake(value_target="game", spill_dir=tmp_path / "spill", **kwargs)
    i.attest(attest("g1"))
    for n, segment in enumerate(sealed, 1):
        i.offer(message(segment, n), segment.data)
    decision = i.closed(closed(sealed))
    return [(s, e) for s, e in decision.admit], i


def test_kappa_one_is_the_game_targets_bit_for_bit(tmp_path):
    """Pin: k = 1 (default) adds no envelope key and gives the baseline (8e417bb3) targets;
    advantages are the same formula on them. Digest computed on that commit."""
    sealed = settle_game()
    default, _ = released(tmp_path / "a", sealed)
    one, _ = released(tmp_path / "b", sealed, combat_end_kappa=1.0)
    digest = hashlib.sha256()
    for (segment, e0), (_s, e1) in zip(default, one, strict=True):
        assert e0 == e1 and COMBAT_SETTLE_KEY not in e0
        source, _ = decode_fast_packet(segment.data, envelope=e0, compiler=COMPILER)
        assert "combat_settle" not in source.metadata[0]
        assert torch.equal(source.raw_advantages,
                           (source.targets - source.probabilities) @ class_reward_values())
        digest.update(source.targets.numpy().tobytes())
    assert digest.hexdigest() == BASELINE_TARGETS_SHA256


BASELINE_TARGETS_SHA256 = "09daaa91d23e46947804daaba1dde6a3420205c558b5fa1371ced893de90bda6"


@pytest.mark.parametrize("kappa", [0.0, 0.5])
def test_the_released_game_settles_combat_rows_only(tmp_path, kappa):
    sealed = settle_game()
    base, _ = released(tmp_path / "base", sealed)
    admitted, i = released(tmp_path / "k", sealed, combat_end_kappa=kappa)
    rows = [meta["outcome_probabilities"] for s in sealed for meta in s.header["rows"]]
    flags = [bool(meta["in_combat"]) for s in sealed for meta in s.header["rows"]]
    end = one_hot(sealed[-1].header["seal"]["terminal"]["class"])
    whole = definition(rows, flags, end, 0.99, kappa)
    p = torch.tensor(rows, dtype=torch.float64)
    cursor, metadata = 0, []
    for (segment, envelope), (_s, plain) in zip(admitted, base, strict=True):
        assert envelope[COMBAT_SETTLE_KEY]["kappa"] == kappa
        source, _ = decode_fast_packet(segment.data, envelope=envelope, compiler=COMPILER)
        reference_source, _ = decode_fast_packet(segment.data, envelope=plain,
                                                 compiler=COMPILER)
        n = len(segment.header["rows"])
        # value targets unchanged, bit for bit; advantages = (G' - p) r on combat rows
        assert torch.equal(source.targets, reference_source.targets)
        expected = (whole[cursor:cursor + n] - p[cursor:cursor + n]) @ R
        assert torch.allclose(source.raw_advantages.double(), expected, atol=2e-6)
        for row in range(n):
            if not flags[cursor + row] or cursor + row >= 130:  # noncombat or terminal battle
                assert source.raw_advantages[row] == reference_source.raw_advantages[row]
        metadata.append(source.metadata[0])
        cursor += n
    combat_rows = sum(flags)
    fields = combat_settle_fields(metadata)
    assert fields[:3] == [combat_rows, 60 + 28, 0]
    old = torch.tensor([a for (s, e) in base for a, f in zip(
        decode_fast_packet(s.data, envelope=e, compiler=COMPILER)[0].raw_advantages.tolist(),
        [m["in_combat"] for m in s.header["rows"]], strict=True) if f], dtype=torch.float64)
    assert fields[3] == pytest.approx(float(old.sum()), rel=1e-6)
    assert fields[4] == pytest.approx(float((old ** 2).sum()), rel=1e-6)
    assert i.report()["game_target_settle_lost_battles"] == 0


def test_the_reviewers_omitted_boundary_through_the_real_path(tmp_path):
    # battle A (floor 1) is followed directly by battle B (floor 2): A's post-battle states
    # were single-choice, so no scored row between; A settles with B's first row, B alone
    g = Game()
    g.open("g1")
    plan = [(False, 1)] * 5 + [(True, 1)] * 10 + [(True, 2)] * 60 + [(False, 2)] * 6
    for in_combat, floor in plan:
        g.step("g1", in_combat=in_combat, floor=floor)
    g.step("g1", terminal=True, in_combat=False, floor=2)
    g.close("g1", "terminal")
    sealed = g.sealed
    admitted, i = released(tmp_path, sealed, combat_end_kappa=0.5)
    rows = [meta["outcome_probabilities"] for s in sealed for meta in s.header["rows"]]
    flags = [bool(meta["in_combat"]) for s in sealed for meta in s.header["rows"]]
    floors = [meta["floor"] for s in sealed for meta in s.header["rows"]]
    assert floors[14:16] == [1, 2] and flags[14:16] == [True, True]
    end = one_hot(sealed[-1].header["seal"]["terminal"]["class"])
    whole = definition(rows, flags, end, 0.99, 0.5, floors)
    merged = definition(rows, flags, end, 0.99, 0.5)  # the old, floor-blind battles
    assert not torch.allclose(whole[5:15], merged[5:15])
    p = torch.tensor(rows, dtype=torch.float64)
    cursor, metadata = 0, []
    for segment, envelope in admitted:
        source, _ = decode_fast_packet(segment.data, envelope=envelope, compiler=COMPILER)
        n = len(segment.header["rows"])
        assert torch.allclose(source.raw_advantages.double(),
                              (whole[cursor:cursor + n] - p[cursor:cursor + n]) @ R, atol=2e-6)
        metadata.append(source.metadata[0])
        cursor += n
    assert combat_settle_fields(metadata)[:3] == [70, 70, 0]
    report = i.report()
    assert [report[f"game_target_settle_{k}_battles"] for k in (
        "end_noncombat", "end_floor", "lost", "lost_floor")] == [1, 1, 0, 0]


def test_a_stop_closed_game_that_ends_in_a_battle_falls_back_and_counts(tmp_path):
    g = Game()
    g.open("g1")
    for step in range(64 * 2 + 3):
        g.step("g1", in_combat=step >= 100)
    g.close("g1", "stop")
    trainable = [s for s in g.sealed if s.kind == "bootstrap"]
    i = intake(value_target="game", spill_dir=tmp_path / "spill", combat_end_kappa=0.5)
    i.attest(attest("g1"))
    for n, segment in enumerate(trainable, 1):
        i.offer(message(segment, n), segment.data)
    decision = i.closed(closed_for(trainable, close_reason="stop", segments={
        "bootstrap": 2, "terminal": 0, "truncated": 1, "rejected": 0}))
    envelopes = [e for _s, e in decision.admit]
    assert [e[COMBAT_SETTLE_KEY]["state"] for e in envelopes] == ["noncombat", "lost"]
    assert i.report()["game_target_settle_lost_battles"] == 1
    source, _ = decode_fast_packet(decision.admit[1][0].data, envelope=envelopes[1],
                                   compiler=COMPILER)
    assert source.metadata[0]["combat_settle"][:3] == [28, 0, 28]
    assert torch.equal(source.raw_advantages,
                       (source.targets - source.probabilities) @ class_reward_values())


def test_kappa_below_one_needs_game_targets():
    with pytest.raises(ValueError, match="kappa"):
        intake(combat_end_kappa=0.5)
    with pytest.raises(ValueError, match="kappa"):
        intake(value_target="game", spill_dir="unused", combat_end_kappa=-0.1)
    with pytest.raises(ValueError):
        SegmentConfig(combat_end_kappa=0.5)
    with pytest.raises(ValueError):
        SegmentConfig(value_target="game", combat_end_kappa=1.5)
    assert SegmentConfig(value_target="game", combat_end_kappa=0.5).combat_end_kappa == 0.5


def test_the_default_is_left_out_of_the_resume_recipe():
    base = {**dataclasses.asdict(SegmentConfig()), "value_target": "game"}
    old = {"loss": {}, "segments": base}
    one = {"loss": {}, "segments": {**base, "combat_end_kappa": 1.0}}
    half = {"loss": {}, "segments": {**base, "combat_end_kappa": 0.5}}
    assert "combat_end_kappa" not in _resume_config(one)["segments"]
    assert _resume_config(old) == _resume_config(one) != _resume_config(half)


def test_a_local_learner_with_kappa_trains_and_reports_the_settlement(tmp_path, monkeypatch):
    from test_rl41_w1 import configuration
    from test_rl_fast_learner import admission
    from test_rl_fast_value_target import learner_game

    from stpd.p1.local_inference import AUTH_ENV
    from stpd.rl.segment_learner import SegmentLearner

    config, io = configuration(tmp_path, monkeypatch)  # as test_rl_fast_value_target
    monkeypatch.setenv(AUTH_ENV, "ab" * 32)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, steps=64, dataset_rows=64, episode_rows=128, capacity_rows=1024,
        value_target="game", target_kl=0.9, combat_end_kappa=0.5))
    dataplane = {"lanes": 1, "root": str(tmp_path / "live"), "source_slots": 8,
                 "prepare_slots": 2, "source_bytes": 16 * 1024**2,
                 "preparation_workers": 1, "assembly_workers": 1, "fast_chain": True}
    owner = SegmentLearner(config, tmp_path / "owner", dataclasses.replace(io, dataplane=dataplane))
    try:
        assert owner.fast_intake.combat_end_kappa == 0.5
        for index, name in enumerate(("g1", "g2"), 1):
            sealed = learner_game(owner, name)
            assert admission(owner, name, index)["admitted"]
            for n, segment in enumerate(sealed, 1):
                owner._fast_apply(owner.fast_intake.offer(message(segment, n), segment.data))
            owner._fast_apply(owner.fast_intake.closed(closed(sealed)))
        assert owner.fast_status()["fast_chain"]["intake"][
            "game_target_settle_lost_battles"] == 0
        settle = None
        while settle is None and owner._prepare():
            while owner.active is not None:
                result = owner._step()
                settle = settle or result.get("combat_settle")
        assert owner.updates >= 1 and settle is not None
        assert settle[0] > 0 and 0 < settle[1] <= settle[0] and settle[2] == 0
    finally:
        owner.close()


def test_segment_config_kappa_reaches_the_intake_options(tmp_path):
    from stpd.rl.segment_learner import value_target_options
    game = SegmentConfig(value_target="game", combat_end_kappa=0.5)
    assert value_target_options(game, tmp_path)["combat_end_kappa"] == 0.5
    assert "combat_end_kappa" not in value_target_options(
        SegmentConfig(value_target="game"), tmp_path)
