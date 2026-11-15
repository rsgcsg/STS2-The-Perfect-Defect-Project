"""RL5-METHODS V: "game" value targets stitched to the end of the game (fast chain)."""
from __future__ import annotations

import collections
import dataclasses
import os
import random
from typing import Any

import pytest
import torch
from test_rl_fast_source import (
    COMPILER,
    Game,
    attest,
    closed_for,
    envelope_for,
    intake,
    message,
    mu,
    published,
)

from stpd.rl.fast_source import (
    ENVELOPE_KEYS,
    GAME_TARGET_KEYS,
    SPILL_SUFFIX,
    FastIntakeError,
    FastSegment,
    FastSegmentIntake,
    decode_fast_packet,
    game_target_tails,
)
from stpd.rl.heads import OUTCOME_CLASSES, categorical_lambda_targets, class_reward_values
from stpd.rl.learning_diagnostics import value_target_fields
from stpd.rl.segment_learner import (
    SegmentLearner,
    _resume_config,
    game_end_counters,
    lag_summary,
)
from stpd.rl.segments import SegmentConfig

LAMBDAS = (0.99, 0.997, 0.5, 1.0, 0.0)


def reference(rows: list[list[float]], tail: list[float], lam: float) -> torch.Tensor:
    """The plain whole-chain recursion: G[-1] = tail, G[t] = (1-l) p[t+1] + l G[t+1]."""
    p = torch.tensor(rows, dtype=torch.float64)
    g = torch.empty_like(p)
    g[-1] = torch.tensor(tail, dtype=torch.float64)
    for t in range(len(p) - 2, -1, -1):
        g[t] = (1 - lam) * p[t + 1] + lam * g[t + 1]
    return g


def distribution(rng: random.Random) -> list[float]:
    raw = [rng.random() ** 3 for _ in range(OUTCOME_CLASSES)]
    total = sum(raw)
    return [x / total for x in raw]


def one_hot(label: int) -> list[float]:
    out = [0.0] * OUTCOME_CLASSES
    out[label] = 1.0
    return out


def synthetic(sizes: list[int], rng: random.Random, *, terminal: bool = True,
              indices: list[int] | None = None) -> list[Any]:
    parts = []
    for position, size in enumerate(sizes):
        last = position == len(sizes) - 1
        is_terminal = terminal and last
        seal = one_hot(7) if is_terminal else distribution(rng)
        parts.append(((indices or list(range(len(sizes))))[position],
                      [distribution(rng) for _ in range(size)], seal, is_terminal))
    return parts


@pytest.mark.parametrize("lam", LAMBDAS)
@pytest.mark.parametrize("terminal", [True, False])
def test_stitched_targets_equal_one_recursion_over_the_whole_game(lam, terminal):
    rng = random.Random(f"{lam}-{terminal}")
    parts = synthetic([64, 64, 64, 17], rng, terminal=terminal)
    tails = game_target_tails(parts, lam)
    rows = [row for _index, part_rows, _seal, _t in parts for row in part_rows]
    whole = reference(rows, parts[-1][2], lam)
    cursor = 0
    for (_index, part_rows, seal, is_terminal), (tail, weight, chained) in zip(
            parts, tails, strict=True):
        last = cursor + len(part_rows) == len(rows)
        assert chained is not last and (tail is None) is last
        targets, advantages = categorical_lambda_targets(
            torch.tensor(part_rows), None if is_terminal else torch.tensor(
                tail if tail is not None else seal), terminal_class=7 if is_terminal else None,
            gae_lambda=lam)
        expected = whole[cursor:cursor + len(part_rows)]
        assert torch.allclose(targets.double(), expected, atol=2e-6)
        expected_adv = (expected - torch.tensor(part_rows, dtype=torch.float64)) @ \
            class_reward_values().double()
        assert torch.allclose(advantages.double(), expected_adv, atol=2e-6)
        # Outcome weight of the tail: lambda^(rows from the segment's last row to the end).
        end_weight = 1.0 if terminal else 0.0
        assert weight == pytest.approx(end_weight * lam ** (len(rows) - 1 - (
            cursor + len(part_rows) - 1)), abs=1e-12)
        cursor += len(part_rows)


def test_a_gap_in_segment_index_ends_the_chain_with_the_own_seal():
    rng = random.Random(3)
    parts = synthetic([64, 64, 64, 9], rng, indices=[0, 1, 3, 4])  # index 2 never arrived
    tails = game_target_tails(parts, 0.99)
    assert [chained for _t, _w, chained in tails] == [True, False, True, False]
    assert tails[1][0] is None and tails[1][1] == 0.0  # recorded bootstrap, no outcome
    # Before the gap: one recursion over parts 0-1 ending at part 1's own bootstrap.
    whole = reference(parts[0][1] + parts[1][1], parts[1][2], 0.99)
    targets, _ = categorical_lambda_targets(torch.tensor(parts[0][1]),
                                            torch.tensor(tails[0][0]), gae_lambda=0.99)
    assert torch.allclose(targets.double(), whole[:64], atol=2e-6)


def test_lambda_out_of_range_and_empty_segments_are_refused():
    rng = random.Random(4)
    with pytest.raises(ValueError, match="lambda"):
        game_target_tails(synthetic([3], rng), 1.5)
    with pytest.raises(ValueError, match="empty"):
        game_target_tails([(0, [], one_hot(1), True)], 0.99)


def three_segment_game(name: str = "g1", generation: str = "gen-1") -> list[Any]:
    g = Game(generation)
    g.open(name)
    for step in range(64 * 2 + 9):
        g.step(name, in_combat=step % 3 != 0)
    g.step(name, terminal=True)
    g.close(name, "terminal")
    assert [s.kind for s in g.sealed] == ["bootstrap", "bootstrap", "terminal"]
    return g.sealed


def game_intake(tmp_path: Any, **kwargs: Any) -> FastSegmentIntake:
    return intake(value_target="game", spill_dir=tmp_path / "spill", **kwargs)


def closed(sealed: list[Any], **change: Any) -> dict[str, Any]:
    return closed_for(sealed, segments={"bootstrap": len(sealed) - 1, "terminal": 1,
                                        "truncated": 0, "rejected": 0}, **change)


def spilled(tmp_path: Any) -> list[str]:
    return sorted(os.listdir(tmp_path / "spill"))


def test_a_held_game_is_spilled_then_admitted_at_close_with_whole_game_targets(tmp_path):
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    for n, segment in enumerate(sealed, 1):
        decision = i.offer(message(segment, n), segment.data)
        assert not decision.admit
    assert spilled(tmp_path) == sorted(s.segment_id + SPILL_SUFFIX for s in sealed)
    report = i.report()
    total = sum(len(s.data) for s in sealed)
    assert report["held_rows"] == 138 and report["held_instances"] == 1
    assert report["spill_bytes"] == report["spill_written_bytes"] == total
    assert report["spill_files"] == 3
    assert i.held_segment_ids() == {s.segment_id for s in sealed}
    # Held segments keep no bytes and no parsed header in memory (review S1).
    held = i.instances["g1"].held
    assert [dataclasses.asdict(h) for h in held] == [
        {"segment_id": s.segment_id, "index": n, "rows": len(s.header["rows"]),
         "size": len(s.data),
         "sha256": message(s)["sha256"]} for n, s in enumerate(sealed)]
    decision = i.closed(closed(sealed))
    assert [s.segment_id for s, _ in decision.admit] == [s.segment_id for s in sealed]
    assert spilled(tmp_path) == []
    report = i.report()
    assert report["spill_bytes"] == 0 and report["spill_files"] == 0
    assert report["spill_read_bytes"] == total and report["held_rows"] == 0
    assert report["game_target_terminal_end_rows"] == 138
    assert report["game_target_chained_segments"] == 2
    assert report["game_target_released_games"] == 1
    # Decoded targets = one recursion over every recorded row of the game.
    rows = [meta["outcome_probabilities"] for s in sealed for meta in s.header["rows"]]
    whole = reference(rows, one_hot(sealed[-1].header["seal"]["terminal"]["class"]), 0.99)
    assert [e["rows_to_game_end"] for _s, e in decision.admit] == [74, 10, 0]
    cursor, weights = 0, []
    for segment, envelope in decision.admit:
        assert segment.data == next(s.data for s in sealed if s.segment_id == segment.segment_id)
        assert set(envelope) == ENVELOPE_KEYS | GAME_TARGET_KEYS
        source, _ = decode_fast_packet(segment.data, envelope=envelope, compiler=COMPILER)
        n = len(segment.header["rows"])
        assert torch.allclose(source.targets.double(), whole[cursor:cursor + n], atol=2e-6)
        weights.append(source.metadata[0]["value_target"])
        cursor += n
    # Outcome weight per row = lambda^(rows to the game's end).
    expected = [0.99 ** (137 - r) for r in range(138)]
    folded = value_target_fields([{"value_target": w} for w in weights])
    assert folded["rows"] == 138
    assert folded["outcome_weight_mean"] == pytest.approx(sum(expected) / 138, rel=1e-9)
    noncombat = [w for r, w in enumerate(expected) if r % 3 == 0]
    assert folded["noncombat_rows"] == len(noncombat)
    assert folded["noncombat_outcome_weight_mean"] == pytest.approx(
        sum(noncombat) / len(noncombat), rel=1e-9)


def test_the_segment_rule_is_unchanged_and_reports_its_own_outcome_weight(tmp_path):
    sealed = three_segment_game()
    i = intake()  # default: segment
    assert i.value_target == "segment"
    i.attest(attest("g1"))
    admitted = [i.offer(message(s, n), s.data).admit for n, s in enumerate(sealed, 1)]
    assert all(len(a) == 1 and set(a[0][1]) == ENVELOPE_KEYS for a in admitted)
    report = i.report()
    assert not any(k.startswith(("spill", "held", "game_target", "value_target"))
                   for k in report)
    weights = []
    for segment in sealed:
        source, _ = decode_fast_packet(segment.data, envelope=envelope_for(segment),
                                       compiler=COMPILER)
        weights.append(source.metadata[0]["value_target"])
    # Only the terminal segment's 10 rows carry the outcome (lambda^k); 0 elsewhere.
    assert [w[0] for w in weights] == [64, 64, 10]
    # R: per source, the rows and the summed entropy (nats) of their value targets.
    for segment in sealed:
        source, _ = decode_fast_packet(segment.data, envelope=envelope_for(segment),
                                       compiler=COMPILER)
        t = source.targets.double()
        rows, total = source.metadata[0]["target_entropy"]
        assert rows == len(segment.header["rows"])
        assert total == pytest.approx(float(-(t[t > 0] * t[t > 0].log()).sum()), rel=1e-5)
    terminal = decode_fast_packet(sealed[-1].data, envelope=envelope_for(sealed[-1]),
                                  compiler=COMPILER)[0]
    assert terminal.metadata[0]["target_entropy"][1] / 10 < 3.93  # below uniform (ln 51)
    assert weights[0][1] == weights[1][1] == 0
    assert weights[2][1] == pytest.approx(sum(0.99 ** k for k in range(10)))


def test_a_stop_closed_game_stitches_to_its_last_bootstrap(tmp_path):
    g = Game()
    g.open("g1")
    for _ in range(64 * 2 + 3):
        g.step("g1")
    g.close("g1", "stop")
    trainable = [s for s in g.sealed if s.kind == "bootstrap"]
    assert len(trainable) == 2
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    for n, segment in enumerate(trainable, 1):
        i.offer(message(segment, n), segment.data)
    decision = i.closed(closed_for(trainable, close_reason="stop", segments={
        "bootstrap": 2, "terminal": 0, "truncated": 1, "rejected": 0}))
    envelopes = [e for _s, e in decision.admit]
    assert envelopes[0]["value_target_tail"] is not None and envelopes[1]["value_target_tail"] \
        is None
    assert envelopes[0]["outcome_tail_weight"] == envelopes[1]["outcome_tail_weight"] == 0
    assert i.report()["game_target_bootstrap_end_rows"] == 128


@pytest.mark.parametrize("change", [{"close_reason": "env_crash"},
                                    {"failure": "unknown:receipt_delivery_unknown"}])
def test_an_excluded_game_is_dropped_with_its_spill_files(tmp_path, change):
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    for n, segment in enumerate(sealed, 1):
        i.offer(message(segment, n), segment.data)
    decision = i.closed(closed(sealed, **change))
    assert not decision.admit and decision.exclude_instance == "g1"
    assert spilled(tmp_path) == []
    report = i.report()
    assert report["game_target_excluded_dropped_rows"] == 138
    assert report["spill_files"] == 0 and report["held_rows"] == 0


def test_an_actor_exclusion_while_held_drops_the_game(tmp_path):
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    i.offer(message(sealed[0]), sealed[0].data)
    i.exclude({"actor": "actor-1", "game_instance_id": "g1", "reason": "seed_refused",
               "run": "run-1", "training_generation": "gen-1"})
    assert spilled(tmp_path) == [] and i.report()["game_target_excluded_dropped_rows"] == 64
    assert not i.offer(message(sealed[1], 2), sealed[1].data).admit
    assert spilled(tmp_path) == []


def test_a_digest_mismatch_on_read_back_drops_the_whole_game_loudly(tmp_path, capsys):
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    for n, segment in enumerate(sealed, 1):
        i.offer(message(segment, n), segment.data)
    path = tmp_path / "spill" / (sealed[1].segment_id + SPILL_SUFFIX)
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))
    decision = i.closed(closed(sealed))
    assert not decision.admit  # no segment of it, no fallback to the shared original
    assert spilled(tmp_path) == []
    report = i.report()
    assert report["spill_readback_failed"] == 1
    assert report["game_target_readback_dropped_rows"] == 138
    assert "fast_value_target_readback_failed instance=g1 digest" in capsys.readouterr().err


def test_a_failed_spill_write_drops_the_game_at_close_loudly(tmp_path, capsys):
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    i.offer(message(sealed[0]), sealed[0].data)
    # The second write cannot create its file (a directory already has that name).
    os.mkdir(tmp_path / "spill" / (sealed[1].segment_id + SPILL_SUFFIX))
    i.offer(message(sealed[1], 2), sealed[1].data)
    i.offer(message(sealed[2], 3), sealed[2].data)
    assert "fast_value_target_spill_write_failed" in capsys.readouterr().err
    decision = i.closed(closed(sealed))
    assert not decision.admit
    report = i.report()
    assert report["spill_write_failed"] == 1
    # The failed segment and the later one, which is never written (review S6).
    assert report["game_target_spill_failed_rows"] == 64 + 10
    assert report["spill_written_bytes"] == len(sealed[0].data)
    assert report["game_target_spill_failed_dropped_rows"] == 64
    assert report["spill_files"] == 0 and report["held_rows"] == 0
    os.rmdir(tmp_path / "spill" / (sealed[1].segment_id + SPILL_SUFFIX))
    assert spilled(tmp_path) == []


def test_a_generation_change_drops_held_games(tmp_path):
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    i.offer(message(sealed[0]), sealed[0].data)
    i.set_generation("gen-2")
    assert spilled(tmp_path) == []
    assert i.report()["game_target_generation_dropped_rows"] == 64


def test_segments_buffered_before_the_attestation_are_held_not_admitted(tmp_path):
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    assert not i.offer(message(sealed[0]), sealed[0].data).admit
    assert spilled(tmp_path) == []  # buffered in memory as before
    assert not i.attest(attest("g1")).admit
    assert spilled(tmp_path) == [sealed[0].segment_id + SPILL_SUFFIX]
    for n, segment in enumerate(sealed[1:], 2):
        i.offer(message(segment, n), segment.data)
    assert len(i.closed(closed(sealed)).admit) == 3


def test_stop_and_restart_discard_held_games_with_counts(tmp_path, capsys):
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    i.offer(message(sealed[0]), sealed[0].data)
    assert i.discard_held("stop") == 64
    assert spilled(tmp_path) == [] and i.report()["game_target_stop_dropped_rows"] == 64
    assert "fast_value_target_held_dropped reason=stop rows=64" in capsys.readouterr().err
    # A process that died with held games leaves files; the next start empties them.
    j = game_intake(tmp_path)
    j.attest(attest("g1"))
    j.offer(message(sealed[0]), sealed[0].data)
    j.offer(message(sealed[1], 2), sealed[1].data)
    restarted = game_intake(tmp_path)
    assert spilled(tmp_path) == []
    assert restarted.report()["spill_cleared_at_start_files"] == 2
    assert "fast_value_target_spill_cleared files=2" in capsys.readouterr().err


def test_after_the_stop_no_segment_is_spilled_and_each_is_counted(tmp_path):
    """Review S2: a reader thread still offering after `discard_held("stop")`."""
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    i.offer(message(sealed[0]), sealed[0].data)
    i.discard_held("stop")
    assert not i.offer(message(sealed[1], 2), sealed[1].data).admit
    assert spilled(tmp_path) == []
    report = i.report()
    assert report["game_target_stop_dropped_rows"] == 128 and report["spill_files"] == 0
    # A write already under way when the stop lands: deleted, counted as stop.
    j = game_intake(tmp_path / "j")
    j.attest(attest("g1"))
    first = FastSegment(message(sealed[0]), sealed[0].header, sealed[0].data)
    decision = j._place(first, first.message, [], None)
    j.discard_held("stop")
    j._spill_offered(decision.spill[0])
    assert os.listdir(tmp_path / "j" / "spill") == []
    assert j.report()["game_target_stop_dropped_rows"] == 64


def test_a_rejected_middle_segment_of_a_stop_closed_game_is_not_a_lost_end(tmp_path):
    """#564: a rejected segment keeps its segment_index, so the game's last bootstrap is
    index bootstrap + terminal + rejected - 1; that is the game's own end, not "lost"."""
    g = Game()
    g.open("g1")
    for _ in range(64 * 3 + 3):
        g.step("g1")
    g.close("g1", "stop")
    trainable = [s for s in g.sealed if s.kind == "bootstrap"]
    assert len(trainable) == 3
    kept = [trainable[0], trainable[2]]  # index 1 was rejected by the assembler
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    for n, segment in enumerate(kept, 1):
        i.offer(message(segment, n), segment.data)
    decision = i.closed(closed_for(kept, close_reason="stop", segments={
        "bootstrap": 2, "terminal": 0, "truncated": 1, "rejected": 1}))
    assert len(decision.admit) == 2
    report = i.report()
    assert report["game_target_bootstrap_end_rows"] == 128
    assert "game_target_lost_end_rows" not in report and report["game_target_gaps"] == 1


def test_game_end_selected_counts_every_record_of_a_dataset():
    """#564: a dataset whose first record has no `rows_to_game_end` still counts the rest."""
    owner = type("Owner", (), {})()
    owner.fast_counters, owner.wal = game_end_counters(), None
    records = [{"segment_id": "old", "rows": 5},  # no envelope (not a "game" fast record)
               {"segment_id": "a", "rows": 7, "envelope": {"rows_to_game_end": 10}},
               {"segment_id": "b", "rows": 3, "envelope": {"rows_to_game_end": 500}}]
    SegmentLearner._dataset_released(owner, records)
    assert owner.fast_counters["game_end_lt128_selected_rows"] == 7
    assert owner.fast_counters["game_end_384_767_selected_rows"] == 3
    plain = type("Owner", (), {})()
    plain.fast_counters, plain.wal = {}, None  # "segment" targets: no counters, no error
    SegmentLearner._dataset_released(plain, records)
    assert plain.fast_counters == {}


def test_a_lost_final_segment_is_its_own_end_and_a_lost_first_one_a_gap(tmp_path):
    """Review S3."""
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    for n, segment in enumerate(sealed[:2], 1):  # the terminal segment never arrived
        i.offer(message(segment, n), segment.data)
    assert len(i.closed(closed(sealed)).admit) == 2
    report = i.report()
    assert report["game_target_lost_end_rows"] == 128
    assert "game_target_bootstrap_end_rows" not in report
    assert report["closed_segment_count_short"] == 1
    j = game_intake(tmp_path / "j")
    j.attest(attest("g1"))
    for n, segment in enumerate(sealed[1:], 2):  # the first one never arrived
        j.offer(message(segment, n), segment.data)
    assert len(j.closed(closed(sealed)).admit) == 2
    report = j.report()
    assert report["game_target_gaps"] == 1 and report["game_target_terminal_end_rows"] == 74


def test_a_failed_delete_keeps_the_bytes_counted(tmp_path, monkeypatch, capsys):
    """Review S6: `spill_bytes` drops only when the file is gone."""
    from stpd.rl import fast_source

    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    i.offer(message(sealed[0]), sealed[0].data)

    def refuse(path: Any) -> None:
        raise PermissionError("refused")

    monkeypatch.setattr(fast_source.os, "unlink", refuse)
    i.discard_held("stop")
    monkeypatch.undo()
    report = i.report()
    assert report["spill_unlink_failed"] == 1
    assert report["spill_bytes"] == len(sealed[0].data) and report["spill_files"] == 1
    assert "fast_value_target_spill_unlink_failed" in capsys.readouterr().err


def test_a_spill_name_collision_fails_loudly_and_keeps_the_existing_file(tmp_path, capsys):
    """Review S6: `open(..., "xb")` on an existing file never deletes that file."""
    sealed = three_segment_game()
    i = game_intake(tmp_path)
    i.attest(attest("g1"))
    existing = tmp_path / "spill" / (sealed[0].segment_id + SPILL_SUFFIX)
    existing.write_bytes(b"keep")
    i.offer(message(sealed[0]), sealed[0].data)
    assert existing.read_bytes() == b"keep"
    assert i.report()["spill_write_failed"] == 1
    assert "FileExistsError" in capsys.readouterr().err


def test_game_targets_need_the_spill_dir_and_a_known_rule(tmp_path):
    with pytest.raises(ValueError, match="spill_dir"):
        FastSegmentIntake(run="r", generation="g", value_target="game")
    with pytest.raises(ValueError, match="spill_dir"):
        FastSegmentIntake(run="r", generation="g", spill_dir=tmp_path)
    with pytest.raises(ValueError, match="spill_dir"):
        FastSegmentIntake(run="r", generation="g", value_target="episode", spill_dir=tmp_path)
    with pytest.raises(ValueError, match="invalid_segment_configuration"):
        SegmentConfig(value_target="episode")
    with pytest.raises(ValueError, match="invalid_segment_configuration"):
        SegmentConfig(capacity_victim="newest")


def test_decode_refuses_a_stitched_tail_on_a_terminal_segment():
    terminal = three_segment_game()[-1]
    envelope = {**envelope_for(terminal), "value_target_tail": one_hot(3),
                "outcome_tail_weight": 1.0, "rows_to_game_end": 0}
    with pytest.raises(FastIntakeError, match="terminal"):
        decode_fast_packet(terminal.data, envelope=envelope, compiler=COMPILER)
    envelope = {**envelope_for(terminal), "value_target_tail": None,
                "outcome_tail_weight": 2.0, "rows_to_game_end": 0}
    with pytest.raises(FastIntakeError, match="weight"):
        decode_fast_packet(terminal.data, envelope=envelope, compiler=COMPILER)
    envelope = {**envelope_for(terminal), "value_target_tail": None,
                "outcome_tail_weight": 1.0, "rows_to_game_end": -1}
    with pytest.raises(FastIntakeError, match="rows_to_end"):
        decode_fast_packet(terminal.data, envelope=envelope, compiler=COMPILER)
    with pytest.raises(FastIntakeError, match="fields"):
        decode_fast_packet(terminal.data, envelope={**envelope_for(terminal),
                                                    "value_target_tail": None},
                           compiler=COMPILER)


def test_the_default_rule_leaves_the_resume_recipe_as_before():
    base = dataclasses.asdict(SegmentConfig())
    assert base["value_target"] == "segment"
    old = {"loss": {}, "segments": {k: v for k, v in base.items() if k != "value_target"}}
    new = {"loss": {}, "segments": base}
    assert _resume_config(old) == _resume_config(new)
    game = {"loss": {}, "segments": {**base, "value_target": "game"}}
    assert _resume_config(game) != _resume_config(new)
    # The victim rule is operational (the queue is never restored): free at resume.
    assert base["capacity_victim"] == "oldest"
    hashed = {"loss": {}, "segments": {**base, "capacity_victim": "hash"}}
    assert _resume_config(hashed) == _resume_config(new)


def test_lag_summary_gives_mean_and_p95():
    assert lag_summary([]) == {"n": 0, "mean": None, "p95": None}
    summary = lag_summary(list(range(100)))
    assert summary == {"n": 100, "mean": 49.5, "p95": 95}


def test_intake_mu_publication_unchanged_for_game_targets(tmp_path):
    i = game_intake(tmp_path)
    i.publish_mu(published(mu(2)))
    assert len(i.published) == 2


# -- the learner: a small local chain with "game" targets --------------------------------
def game_learner(tmp_path: Any, monkeypatch: Any, value_target: str = "game",
                 capacity_victim: str = "oldest", **dataplane_extra: Any) -> Any:
    from test_rl41_w1 import configuration

    from stpd.p1.local_inference import AUTH_ENV
    from stpd.rl.segment_learner import SegmentLearner

    tmp_path.mkdir(parents=True, exist_ok=True)
    config, io = configuration(tmp_path, monkeypatch)
    monkeypatch.setenv(AUTH_ENV, "ab" * 32)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, steps=64, dataset_rows=64, episode_rows=128, capacity_rows=1024,
        value_target=value_target, capacity_victim=capacity_victim, target_kl=0.9))
    dataplane = {"lanes": 1, "root": str(tmp_path / "live"), "source_slots": 8,
                 "prepare_slots": 2, "source_bytes": 16 * 1024**2,
                 "preparation_workers": 1, "assembly_workers": 1, "fast_chain": True,
                 **dataplane_extra}
    return SegmentLearner(config, tmp_path / "owner", dataclasses.replace(io, dataplane=dataplane))


def learner_game(owner: Any, name: str, steps: int = 64 * 2 + 9) -> list[Any]:
    from test_rl_fast_learner import learner_mu

    from stpd.p1.frame_compiler import FrameCompiler

    g = Game(owner.generation, compiler=FrameCompiler(owner.model.vocabulary),
             assembler_id="node-" + name, handoff=learner_mu(owner))
    g.open(name)
    for step in range(steps):
        g.step(name, in_combat=step % 3 != 0)
    g.step(name, terminal=True)
    g.close(name, "terminal")
    return g.sealed


def test_a_local_chain_with_game_targets_trains_several_updates(tmp_path, monkeypatch):
    from test_rl_fast_learner import admission, queued

    owner = game_learner(tmp_path, monkeypatch)
    spill = tmp_path / "live" / "value-target-spill"
    try:
        assert owner.fast_intake.value_target == "game"
        games = {f"g{k}": learner_game(owner, f"g{k}") for k in range(1, 4)}
        written = 0
        for index, (name, sealed) in enumerate(games.items(), 1):
            assert admission(owner, name, index)["admitted"]
            for n, segment in enumerate(sealed, 1):
                owner._fast_apply(owner.fast_intake.offer(message(segment, n), segment.data))
                written += len(segment.data)
            assert queued(owner) == [s.segment_id for g in list(games.values())[:index - 1]
                                     for s in g]  # held until the close
            assert len(os.listdir(spill)) == len(sealed)
            owner._fast_apply(owner.fast_intake.closed(closed(sealed)))
            assert os.listdir(spill) == []
        intake = owner.fast_status()["fast_chain"]["intake"]
        assert intake["spill_written_bytes"] == intake["spill_read_bytes"] == written
        assert intake["game_target_terminal_end_rows"] == 3 * 138
        records = []
        while owner._prepare():
            records.append(None)
            while owner.active is not None:
                result = owner._step()
                if "value_target" in result:
                    records[-1] = result
            if len(records) >= 5:
                break
        assert owner.updates >= 3
        first = records[0]
        assert first["value_target"]["rows"] == 64
        assert first["value_target"]["outcome_weight_mean"] > 0  # stitched: 0 under segment
        assert first["advantage_raw"]["variance"] == pytest.approx(
            first["advantage_raw"]["std"] ** 2)
        # R: the learning-settings diagnostics ride on the same progress results.
        assert first["value_target"]["target_entropy_mean"] > 0
        assert first["gradient_clipped"] == (first["gradient_norm"]
                                             > owner.config.loss.max_grad_norm)
        assert len(first["value_calibration"]) == 10
        assert isinstance(first["policy_entropy"], dict)
        if first["stepped"]:
            age = first["first_use_mu_age_seconds"]
            assert age["missing"] == 0 and age["n"] > 0 and age["sum"] >= 0
    finally:
        owner.close()


def test_learner_stop_discards_held_games_and_a_restart_empties_the_spill(
        tmp_path, monkeypatch, capsys):
    from test_rl_fast_learner import admission

    owner = game_learner(tmp_path / "a", monkeypatch)
    sealed = learner_game(owner, "g1")
    admission(owner, "g1", 1)
    owner._fast_apply(owner.fast_intake.offer(message(sealed[0]), sealed[0].data))
    spill = tmp_path / "a" / "live" / "value-target-spill"
    assert len(os.listdir(spill)) == 1
    intake = owner.fast_intake
    owner.close()
    assert os.listdir(spill) == [] and intake.report()["game_target_stop_dropped_rows"] == 64
    assert "fast_value_target_held_dropped reason=stop rows=64" in capsys.readouterr().err
    # A learner killed with a held game leaves its file; the next start removes it.
    left = tmp_path / "b" / "live" / "value-target-spill"
    left.mkdir(parents=True)
    (left / ("left" + SPILL_SUFFIX)).write_bytes(b"x")
    again = game_learner(tmp_path / "b", monkeypatch)
    try:
        assert again.fast_status()["fast_chain"]["intake"]["spill_cleared_at_start_files"] == 1
        assert os.listdir(left) == []
    finally:
        again.close()


def test_a_stop_drops_held_games_before_the_final_status_and_spills_nothing_after(
        tmp_path, monkeypatch):
    """Review S2: the run's stop discard (`_discard_stopped_queue`, before the last
    status of `_run`) drops held games, so the run record has them."""
    from test_rl_fast_learner import admission

    owner = game_learner(tmp_path, monkeypatch)
    try:
        sealed = learner_game(owner, "g1")
        admission(owner, "g1", 1)
        owner._fast_apply(owner.fast_intake.offer(message(sealed[0]), sealed[0].data))
        with owner.condition:
            owner.newlease_stopped = True
            owner._discard_stopped_queue()
        owner._fast_apply(owner.fast_intake.offer(message(sealed[1], 2), sealed[1].data))
        intake = owner.fast_status()["fast_chain"]["intake"]
        assert intake["game_target_stop_dropped_rows"] == 128
        assert intake["spill_files"] == 0
        assert os.listdir(tmp_path / "live" / "value-target-spill") == []
    finally:
        owner.close()


def test_release_never_marks_a_held_segment_done(tmp_path, monkeypatch):
    """Review S5: `fast_release` sees held segments as not done."""
    from test_rl_fast_learner import admission

    owner = game_learner(tmp_path, monkeypatch, release_trained_segments=True)
    try:
        sealed = learner_game(owner, "g1")
        admission(owner, "g1", 1)
        owner._fast_apply(owner.fast_intake.offer(message(sealed[0]), sealed[0].data))
        assert owner.queue.pending == collections.deque()  # held, not queued
        assert sealed[0].segment_id in owner.segment_release.held()
    finally:
        owner.close()


def overflow_victims(tmp_path: Any, monkeypatch: Any,
                     victim: str) -> tuple[Any, list[Any], set[str]]:
    """Nine 138-row games into a 1,024-row queue: about one game is evicted."""
    from test_rl_fast_learner import admission

    owner = game_learner(tmp_path, monkeypatch, capacity_victim=victim)
    order: list[Any] = []
    for index in range(1, 10):
        name = f"g{index}"
        sealed = learner_game(owner, name)
        admission(owner, name, index)
        for n, segment in enumerate(sealed, 1):
            owner._fast_apply(owner.fast_intake.offer(message(segment, n), segment.data))
        owner._fast_apply(owner.fast_intake.closed(closed(sealed)))
        order.extend(sealed)
    queued = {record["segment_id"] for record in owner.queue.pending}
    return owner, order, {s.segment_id for s in order} - queued


def expected_victims(order: list[Any], key: Any) -> set[str]:
    """The queue model: admit in order; when full, drop by `key` until it fits."""
    queue: list[Any] = []
    evicted: set[str] = set()
    for segment in order:
        while sum(len(s.header["rows"]) for s in queue) + len(segment.header["rows"]) > 1024:
            victim = min(queue, key=key)
            queue.remove(victim)
            evicted.add(victim.segment_id)
        queue.append(segment)
    return evicted


@pytest.mark.parametrize("victim", ["oldest", "hash"])
def test_a_full_queue_drops_by_the_configured_victim_rule(tmp_path, monkeypatch, victim):
    """Review M1. "oldest" (default, the 11-09 rule) drops the queue head; "hash"
    drops the lowest packet digest, whatever its age or place in its game. Both
    count selected/evicted rows by distance to the game's end under "game"."""
    owner, order, evicted = overflow_victims(tmp_path, monkeypatch, victim)
    try:
        position = {s.segment_id: n for n, s in enumerate(order)}
        key = ((lambda s: position[s.segment_id]) if victim == "oldest"
               else (lambda s: message(s)["sha256"][:16]))
        assert evicted and evicted == expected_victims(order, key)
        counters = owner.fast_status()["fast_chain"]["learner"]
        evicted_rows = sum(len(s.header["rows"]) for s in order if s.segment_id in evicted)
        assert counters["capacity_evicted_rows"] == evicted_rows
        assert sum(counters[f"game_end_{b}_evicted_rows"]
                   for b in ("lt128", "128_383", "384_767", "ge768")) == evicted_rows
        while owner._prepare():
            while owner.active is not None:
                owner._step()
        assert counters is not owner.fast_status()["fast_chain"]["learner"]
        after = owner.fast_status()["fast_chain"]["learner"]
        assert after["game_end_lt128_selected_rows"] > 0
    finally:
        owner.close()


def test_the_default_victim_rule_is_oldest_first_and_adds_no_counters(tmp_path, monkeypatch):
    assert SegmentConfig().capacity_victim == "oldest"
    owner = game_learner(tmp_path, monkeypatch, value_target="segment")
    try:
        assert not any(k.startswith("game_end_") for k in owner.fast_counters)
    finally:
        owner.close()


def test_segment_rule_learner_has_no_spill(tmp_path, monkeypatch):
    owner = game_learner(tmp_path, monkeypatch, value_target="segment")
    try:
        assert owner.fast_intake.value_target == "segment"
        assert not (tmp_path / "live" / "value-target-spill").exists()
    finally:
        owner.close()


def test_two_shards_hold_spill_and_release_games_in_their_own_scratch(tmp_path, monkeypatch):
    """The standard chain runs `dataplane.shards`: each shard is its own intake
    with its own spill directory under its scratch, and trains on stitched rows."""
    from test_rl_dataplane_shards import game, hand_off, parts, train

    config, io = parts(tmp_path, monkeypatch, 2, prestart=True)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, value_target="game"))
    from stpd.rl.segment_learner import SegmentLearner

    owner = SegmentLearner(config, tmp_path / "owner", io)
    try:
        first, second = game(owner, 1), game(owner, 2)
        hand_off(owner, first)   # shard 0
        hand_off(owner, second)  # shard 1
        per_shard = owner.fast_status()["fast_chain"]["shards"]["per_shard"]
        for shard, sealed in zip(per_shard, (first, second), strict=True):
            intake = shard["intake"]
            assert intake["value_target"] == "game"
            assert intake["game_target_released_games"] == 1
            assert intake["spill_written_bytes"] == intake["spill_read_bytes"] == sum(
                len(s.data) for s in sealed)
            assert intake["spill_bytes"] == intake["spill_files"] == intake["held_rows"] == 0
        for index in (0, 1):
            assert os.listdir(owner.live_work / f"shard-{index}" / "value-target-spill") == []
        requests, steps = train(owner, 2)
        assert len(requests) == 2 and owner.updates > 0 and steps
        assert not owner.input_fenced
        learner = owner.fast_status()["fast_chain"]["learner"]
        assert learner["game_end_lt128_selected_rows"] > 0  # summed over the shards
    finally:
        owner.close()
