"""R7 audit tools: pre-open sampler and the three comparators (fast_chain, design v2 §2.9)."""
from __future__ import annotations

import copy
import hashlib
from typing import Any

import pytest

from stpd.rl.r7_audit import (
    R7Sampler,
    compare_action_records,
    compare_compact_reconstruction,
    compare_encoded_row,
    dehandle,
    reference_row,
)


# ------------------------------------------------------------------- sampler
def test_sampler_quota_per_window_and_decided_before_open():
    sampler = R7Sampler(key="actor-group-1", quota=15, expected_games=600)
    picks = [sampler.select(10.0 + i) for i in range(600)]
    assert sum(picks) == 15
    # Same key, same window -> the same ordinals: the choice depends on nothing else.
    again = R7Sampler(key="actor-group-1", quota=15, expected_games=600)
    assert [again.select(3599.0) for _ in range(600)] == picks
    # Next window draws afresh and records the previous one.
    nxt = [sampler.select(3600.0 + i) for i in range(600)]
    assert sum(nxt) == 15 and nxt != picks
    history = sampler.report()["history"]
    assert history == [{"window": 0, "opened": 600, "chosen": 15,
                        "chosen_and_opened": history[0]["chosen_and_opened"],
                        "overflow": 0, "resumed_at": None}]
    assert len(history[0]["chosen_and_opened"]) == 15


def test_sampler_reports_overflow_beyond_expected_games():
    sampler = R7Sampler(key="k", quota=3, expected_games=100)
    picks = [sampler.select(0.0) for _ in range(300)]
    assert sum(picks) == 3 and not any(picks[100:])  # beyond expected: never chosen ...
    assert sampler.report()["current"]["overflow"] == 200  # ... and said so
    sampler.select(3600.0)
    assert sampler.report()["history"][0]["overflow"] == 200


def test_sampler_resume_continues_the_window_after_a_restart():
    reference = R7Sampler(key="k", quota=3, expected_games=100)
    picks = [reference.select(100.0) for _ in range(100)]
    restarted = R7Sampler(key="k", quota=3, expected_games=100)
    restarted.resume(200.0, 40)  # the launcher persisted 40 opened games in this window
    assert [restarted.select(200.0) for _ in range(60)] == picks[40:]
    assert sum(picks[:40]) + sum(picks[40:]) == 3  # no ordinal is chosen twice
    assert restarted.report()["current"]["resumed_at"] == 40
    with pytest.raises(ValueError, match="resume_only_before_first_select"):
        restarted.resume(200.0, 1)
    with pytest.raises(ValueError, match="resume_only_before_first_select"):
        R7Sampler(key="k", quota=3, expected_games=100).resume(0.0, -1)


def test_sampler_fractional_share_and_short_window():
    counts = []
    for window in range(400):
        sampler = R7Sampler(key="k", quota=2.5, expected_games=50)
        counts.append(sum(sampler.select(window * 3600.0) for _ in range(50)))
    assert set(counts) == {2, 3}
    assert 0.35 < counts.count(3) / len(counts) < 0.65
    short = R7Sampler(key="k", quota=20, expected_games=100)
    chosen = sum(short.select(0.0) for _ in range(30))
    report = short.report()["current"]
    assert report["opened"] == 30 and report["chosen"] == 20
    assert report["chosen_so_far"] == chosen < 20  # fewer games opened, fewer chosen, reported


def test_sampler_rejects_bad_configuration_and_backward_clock():
    for kwargs in ({"key": "", "quota": 1, "expected_games": 10},
                   {"key": "k", "quota": 11, "expected_games": 10},
                   {"key": "k", "quota": float("nan"), "expected_games": 10},
                   {"key": "k", "quota": 1, "expected_games": 0}):
        with pytest.raises(ValueError, match="invalid_r7_sampler"):
            R7Sampler(**kwargs)
    sampler = R7Sampler(key="k", quota=1, expected_games=10)
    sampler.select(7200.0)
    with pytest.raises(ValueError, match="clock_went_backwards"):
        sampler.select(10.0)


# ---------------------------------------------------------- action records
def test_dehandle_placeholders():
    value = {"id": "e12", "ref": "native-7", "card": "CARD_A",
             "token": "1b4e28ba-2fa1-11d2-883f-0016d3cca427", "digest": "a" * 64,
             "list": ["e3", "e3x"]}
    assert dehandle(value) == {"id": "<e>", "ref": "<native>", "card": "CARD_A",
                               "token": "<uuid>", "digest": "<hex>", "list": ["<e>", "e3x"]}


def _step(index: int, **overrides: Any) -> dict[str, Any]:
    step = {"step_index": index,
            "input": {"decision": "combat_play", "hand": [{"id": f"e{index}", "title": "Strike",
                                                           "text": "Deal 6 damage."}]},
            "content_sha256": hashlib.sha256(f"content-{index}".encode()).hexdigest(),
            "choice": {"content": {"kind": "play", "card": f"e{index}"}, "occurrence": 0,
                       "ambiguous": False},
            "receipt": {"delivery": "delivered", "reason": None},
            "native_steps": 1, "scores": [0.2, 0.5, 0.3], "selected_score": 0.5}
    step.update(overrides)
    return step


def _record(steps: int = 4) -> dict[str, Any]:
    return {"steps": [_step(i) for i in range(steps)], "decisions": steps,
            "native_steps": steps, "terminal": {"result": "defeat"}}


def test_identical_replay_passes_with_handles_renamed():
    fast, replay = _record(), _record()
    for step in replay["steps"]:  # the replay process allocates different entity handles
        step["input"]["hand"][0]["id"] = "e9" + str(step["step_index"])
        step["choice"]["content"]["card"] = step["input"]["hand"][0]["id"]
        step["scores"] = list(reversed(step["scores"]))  # multiset, order free
    report = compare_action_records(fast, replay)
    assert report.compared_steps == 4 and report.findings == [] and not report.stops_output


@pytest.mark.parametrize(("mutate", "kind"), [
    (lambda s: s["input"]["hand"][0].update(title="Defend"), "input"),
    (lambda s: s.update(content_sha256="0" * 64), "content_sha256"),
    (lambda s: s["choice"].update(occurrence=1), "choice"),
    (lambda s: s.update(receipt={"delivery": "unknown", "reason": "timeout"}), "receipt"),
    (lambda s: s.update(native_steps=2), "native_steps"),
    (lambda s: s.update(scores=[0.2, 0.5]), "score_count"),
])
def test_divergence_stops_output_at_the_first_fork(mutate, kind):
    fast, replay = _record(), _record()
    mutate(replay["steps"][1])
    report = compare_action_records(fast, replay)
    assert report.stops_output and report.compared_steps == 2
    assert [(f.step, f.kind) for f in report.findings if f.category == "divergence"] == [(1, kind)]


def _event(record: dict[str, Any], step: int, prompt: str) -> None:
    record["steps"][step]["input"]["scene"] = {"family": "event", "prompt": prompt}


def test_card_text_number_change_is_a_divergence():
    fast, replay = _record(), _record()
    replay["steps"][2]["input"]["hand"][0]["text"] = "Deal 9 damage."  # model input
    report = compare_action_records(fast, replay)
    assert report.stops_output
    assert [(f.step, f.kind, f.category) for f in report.findings] == [(2, "input",
                                                                       "divergence")]


def test_event_body_number_is_listed_separately():
    fast, replay = _record(), _record()
    _event(fast, 2, "You meet entrant 474.")
    _event(replay, 2, "You meet entrant 184.")
    report = compare_action_records(fast, replay)
    assert not report.stops_output
    assert report.counts() == {"display_text_number": 1}
    assert report.findings[0].detail == ["$.scene.prompt"]
    _event(replay, 2, "You meet rival 184.")  # more than a number differs
    assert compare_action_records(fast, replay).stops_output
    _event(replay, 2, "You meet entrant 184.")
    replay["steps"][2]["input"]["scene"]["family"] = "combat"  # not the whitelisted family
    assert compare_action_records(fast, replay).stops_output
    for record in (fast, replay):
        record["steps"][2]["input"]["scene"]["family"] = "shop"
    assert compare_action_records(fast, replay).stops_output
    _event(replay, 2, "You meet entrant 184.")
    _event(fast, 2, "You meet entrant 474.")
    replay["steps"][2]["input"]["hand"][0]["text"] = "Deal 9 damage."  # mixed with model input
    assert compare_action_records(fast, replay).stops_output


@pytest.mark.parametrize("score", [float("nan"), float("inf")])
def test_non_finite_score_is_a_divergence(score):
    fast, replay = _record(), _record()
    replay["steps"][1]["scores"] = [0.2, score, 0.3]
    report = compare_action_records(fast, replay)
    assert report.stops_output and report.findings[0].kind == "non_finite_score"
    fast, replay = _record(), _record()
    fast["steps"][1]["selected_score"] = score
    assert compare_action_records(fast, replay).findings[0].kind == "non_finite_score"


@pytest.mark.parametrize(("mutate", "problem"), [
    (lambda r: r.pop("terminal"), "record_keys"),
    (lambda r: r.update(steps={}), "steps"),
    (lambda r: r["steps"][1].pop("receipt"), "steps[1].keys"),
    (lambda r: r["steps"][2]["choice"].pop("occurrence"), "steps[2].types"),
    (lambda r: r["steps"][0].update(scores=["0.5"]), "steps[0].types"),
    (lambda r: r["steps"][0].update(selected_score=None), "steps[0].types"),
    (lambda r: r["steps"][3].update(step_index="3"), "steps[3].types"),
])
def test_malformed_record_is_a_typed_divergence(mutate, problem):
    fast, replay = _record(), _record()
    mutate(replay)
    report = compare_action_records(fast, replay)
    assert report.stops_output
    assert [f.as_dict() for f in report.findings] == [{
        "step": None, "kind": "record_shape", "category": "divergence",
        "detail": {"side": "replay", "problem": problem}}]
    assert compare_action_records(replay, fast).findings[0].detail["side"] == "fast"


def test_score_drift_reported_not_stopping():
    fast, replay = _record(), _record()
    replay["steps"][0]["scores"] = [0.2, 0.5, 0.3 + 1e-6]
    assert compare_action_records(fast, replay).findings == []
    replay["steps"][0]["scores"] = [0.2, 0.5, 0.31]
    replay["steps"][3]["selected_score"] = 0.51
    report = compare_action_records(fast, replay)
    assert not report.stops_output
    assert [(f.step, f.kind) for f in report.findings] == [(0, "score_multiset"),
                                                         (3, "selected_score")]


def test_fork_after_ambiguous_duplicate_match_still_fails():
    fast, replay = _record(), _record()
    for record in (fast, replay):
        record["steps"][1]["choice"]["ambiguous"] = True
    replay["steps"][3]["input"]["hand"][0]["title"] = "Bash"
    report = compare_action_records(fast, replay)
    assert report.stops_output
    divergence = [f for f in report.findings if f.category == "divergence"]
    assert divergence[0].step == 3 and divergence[0].detail["after_ambiguous_match"] is True
    assert report.counts()["ambiguous_match"] == 1


def test_step_count_decisions_and_terminal():
    fast, short = _record(4), _record(3)
    assert [f.kind for f in compare_action_records(fast, short).findings] == ["step_count"]
    replay = _record(4)
    replay["terminal"] = {"result": "victory"}
    report = compare_action_records(fast, replay)
    assert report.stops_output and [f.kind for f in report.findings] == ["terminal"]
    replay = _record(4)
    replay["native_steps"] = 5
    assert [f.kind for f in compare_action_records(fast, replay).findings] == ["native_steps"]


# ------------------------------------------------- compact vs legacy states
def test_compact_reconstruction_equals_legacy_after_rename():
    from test_prewrite_wp0_contracts import _lines, _raw, reconstruct

    rebuilt, legacy = reconstruct(_raw("compact-episode.jsonl")), _lines("legacy-episode.jsonl")
    assert rebuilt != legacy  # ref numbers differ between writers
    report = compare_compact_reconstruction(rebuilt, legacy)
    assert report.compared_steps == len(legacy) and not report.stops_output


def test_compact_reconstruction_collapsed_refs_and_tamper_fail():
    from test_prewrite_wp0_contracts import _lines, _raw, reconstruct

    legacy = _lines("legacy-episode.jsonl")
    collapsed = reconstruct(_raw("invalid-not-bijective.jsonl"))
    assert compare_compact_reconstruction(collapsed, legacy).stops_output
    tampered = copy.deepcopy(reconstruct(_raw("compact-episode.jsonl")))
    tampered[2]["player"]["hp"] -= 1
    report = compare_compact_reconstruction(tampered, legacy)
    assert report.stops_output and report.findings[0].step == 2
    assert report.findings[0].detail == ["$.player.hp"]
    assert compare_compact_reconstruction(tampered[:2], legacy).findings[0].kind == "state_count"


def test_compact_reconstruction_ignores_facts_and_diagnostics_only():
    from test_prewrite_wp0_contracts import _lines

    legacy = _lines("legacy-episode.jsonl")
    with_facts = copy.deepcopy(legacy)
    with_facts[0]["facts"] = {"combat_end": []}
    with_facts[1]["diagnostics"] = {"timing_ms": 3}
    assert not compare_compact_reconstruction(with_facts, legacy).stops_output
    reordered = copy.deepcopy(legacy)
    reordered[0] = dict(reversed(list(reordered[0].items())))
    assert compare_compact_reconstruction(reordered, legacy).stops_output  # key order matters


# ---------------------------------------------------------- encoding vs reference
def _compiler_and_row():
    from test_p1_input_v2 import public_state

    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.p1.input import V2_INPUT_SPEC
    from stpd.p1.vocabulary import public_vocabulary

    compiler = FrameCompiler(public_vocabulary(V2_INPUT_SPEC))
    observation = public_state()
    return compiler, observation, reference_row(compiler, observation)


def test_encoded_row_equal_to_reference():
    compiler, _, row = _compiler_and_row()
    digest = hashlib.sha256(row).hexdigest()
    assert compare_encoded_row(row, digest, row, validator=compiler) is None
    finding = compare_encoded_row(row, "0" * 64, row, validator=compiler)
    assert finding is not None and finding.detail == "recorded_row_sha256_mismatch"


def test_encoded_row_difference_names_the_tensor():
    from stpd.p1.rows_encoder_wire import encode_row

    compiler, observation, row = _compiler_and_row()
    changed = copy.deepcopy(observation)
    changed["deck"]["content"]["cards"][1]["cost"] = 3
    fast = encode_row(compiler.compile(changed))
    finding = compare_encoded_row(fast, hashlib.sha256(fast).hexdigest(), row,
                                  validator=compiler)
    assert finding is not None and finding.category == "divergence"
    assert isinstance(finding.detail, str) and finding.detail.startswith(("tensor:", "reduction:"))
    corrupt = bytes(row[:-8]) + b"\xff" * 8
    finding = compare_encoded_row(corrupt, hashlib.sha256(corrupt).hexdigest(), row,
                                  validator=compiler)
    assert finding is not None and finding.category == "divergence"


# ------------------------------------------------------- labels vs old chain
def test_terminal_label_compared_game_by_game():
    from stpd.rl.r7_audit import compare_terminal_label

    label = {"result": "defeat", "class": 18, "reward": 0.5 * (18 / 48) ** 2 + 0.1}
    legacy = {"class": 18, "reward": label["reward"], "typed_original": "c" * 64}
    assert compare_terminal_label(label, legacy) is None
    assert compare_terminal_label(None, None) is None
    assert compare_terminal_label(label, None).category == "divergence"
    assert compare_terminal_label({**label, "class": 50}, legacy).kind == "terminal_label"


def _hp_row(step: int, combat: bool, hp: int, terminal: dict | None = None) -> dict:
    return {"step_index": step, "in_combat": combat, "outcome": {
        "status": "completed", "terminal": terminal,
        "public_facts": {"act": 1, "floor": 3, "hp": hp, "max_hp": 80}}}


def test_public_battle_hp_candidate_and_comparison():
    from stpd.rl.r7_audit import compare_hp_labels, public_battle_hp

    rows = [_hp_row(0, False, 80), _hp_row(1, True, 74), _hp_row(2, True, 70),
            _hp_row(3, False, 70), _hp_row(4, True, 60), _hp_row(5, True, 0, {"result": "defeat"})]
    battles = public_battle_hp(rows)
    assert battles == [{"steps": [1, 2], "post_hp": 70}, {"steps": [4, 5], "post_hp": 0}]
    # A game closed in combat without a natural terminal gives no final label.
    assert public_battle_hp(rows[:5]) == [{"steps": [1, 2], "post_hp": 70}]
    assert compare_hp_labels(battles, battles) is None
    finding = compare_hp_labels(battles, [{"steps": [1, 2], "post_hp": 69}])
    assert finding is not None and finding.detail == {"first_battle": 0, "fast": 2, "legacy": 1}
