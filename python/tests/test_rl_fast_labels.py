"""fast_chain public terminal label (old-chain equal) and R7 mu archive (audit_retain_mu)."""
from __future__ import annotations

from typing import Any

import pytest
from test_rl_fast_assembly import Harness, mu

from stpd.p1.trajectory import boss_clearance
from stpd.rl.fast_assembly import (
    AssemblyError,
    MuHandoff,
    public_bosses_cleared,
    public_terminal_label,
)
from stpd.rl.fast_handoff import DurableMuArchive, HandoffError
from stpd.rl.heads import checked_terminal_class
from stpd.rl.reward import episode_reward


def old_chain_label(acts: list[int], result: str, floor: int) -> dict[str, Any]:
    """Today's label path: extract_trajectory outcome -> episode_reward -> checked class."""
    observations = [{"index": i, "observation": {"run": {"content": {"act": act}}}}
                    for i, act in enumerate(acts)]
    episode = {"identity": {"ascension": 0}, "outcome": {
        "status": "finished", "unknown": False,
        "terminal": {"status": "observed", "result": "win" if result == "victory" else "loss"},
        "max_observed_floor": floor, "boss_clearance": boss_clearance(observations)}}
    reward, disposition = episode_reward(episode)
    assert reward is not None
    return {"class": checked_terminal_class(result=disposition, floor=floor, reward=reward),
            "reward": reward}


CASES = [
    ([1, 1, 1], "defeat", 5),
    ([1] * 5 + [1], "defeat", 17),            # lost at the act 1 Boss: not cleared
    ([1, 1, 2, 2], "defeat", 18),             # act advance = Boss 1 cleared
    ([1, 2, 2, 3, 3], "defeat", 40),
    ([1, 2, 2, 2], "defeat", 35),             # Golden Compass: floor 35 with one Boss
    ([1, 2, 3, 3], "defeat", 35),             # floor 35 after two Bosses
    ([1, 2, 3, 3], "victory", 48),
    ([1, 2, 3, 3], "defeat", 48),             # lost at the final Boss
]


@pytest.mark.parametrize(("acts", "result", "floor"), CASES)
def test_public_terminal_label_equals_old_chain(acts, result, floor):
    label = public_terminal_label({"result": result, "max_floor": floor, "acts": acts})
    assert label == old_chain_label(acts, result, floor)


def test_public_terminal_label_refuses_what_the_old_chain_refuses():
    # From a save in act 2 nothing public shows Boss 1; both chains refuse the class.
    acts, floor = [2, 2, 2], 30
    with pytest.raises(ValueError, match="terminal_class_reward_mismatch"):
        old_chain_label(acts, "defeat", floor)
    with pytest.raises(ValueError, match="terminal_class_reward_mismatch"):
        public_terminal_label({"result": "defeat", "max_floor": floor, "acts": acts})
    assert public_bosses_cleared([1, 3, 4]) == 1  # a skipped act is no witness


def _terminal_game(h: Harness, game: str, acts: list[int], floor: int, *,
                   result: str = "defeat", start: dict | None = None) -> None:
    h.open(game, start=start)
    rows = []
    for act in acts[:-1]:
        _, row = h.step(game, outcome=False, floor=min(floor, 10))
        h.outcome(row, public_facts={"act": act, "floor": min(floor, 10), "hp": 40,
                                     "max_hp": 80})
        rows.append(row)
    _, row = h.step(game, outcome=False, floor=floor)
    h.outcome(row, terminal={"result": result},
              public_facts={"act": acts[-1], "floor": floor, "hp": 0, "max_hp": 80})
    h.close(game, "terminal", row["step_index"])


def test_default_labeler_seals_terminal_from_public_act_advances():
    from stpd.rl.fast_assembly import FastSegmentAssembler

    sealed: list[Any] = []
    assembler = FastSegmentAssembler(assembler_id="node-a",
                                     compiler_identity={"compiler_sha256": "b" * 64},
                                     emit=sealed.append)
    h = Harness()
    h.assembler, h.sealed = assembler, sealed
    assembler.offer_mu(mu(1))
    _terminal_game(h, "compass", [2, 2], 35)         # new_run starts in act 1 -> 1 Boss
    _terminal_game(h, "two", [2, 3, 3], 35)
    _terminal_game(h, "save", [2, 2], 30, start={"kind": "from_save", "save_sha256": "d" * 64,
                                                 "act": 2, "floor": 20})
    compass, two, save = sealed
    assert compass.kind == "terminal" and compass.header["seal"]["terminal"]["class"] == 50
    assert two.kind == "terminal" and two.header["seal"]["terminal"]["class"] == 35
    assert two.header["seal"]["terminal"] == {
        "result": "defeat", **old_chain_label([1, 2, 3, 3], "defeat", 35)}
    # The old chain refuses this class too; kept as original, counted, never trained.
    assert save.kind == "truncated"
    assert save.header["seal"]["truncation"]["reason"] == "terminal_label_refused"
    assert assembler.report()["terminal_label_refused"] == 1


def weighted(index: int, payload: bytes | None = None) -> MuHandoff:
    base = mu(index)
    return MuHandoff(base.snapshot_sha256, base.training_generation, base.version, base.updates,
                     weights=payload if payload is not None else f"weights-{index}".encode())


def test_audit_game_archives_every_bound_mu_before_scoring(tmp_path):
    archive = DurableMuArchive(tmp_path)
    calls: list[str] = []

    def recording(handoff: MuHandoff) -> None:
        calls.append(handoff.snapshot_sha256)
        archive(handoff)

    h = Harness(mu_archive=recording)
    h.assembler.offer_mu(weighted(1))
    h.open("plain")
    h.step("plain")
    assert calls == []  # an ordinary game archives nothing
    h.open("r7", audit_retain_mu=True)
    assert calls == [weighted(1).snapshot_sha256]
    for _ in range(64):
        h.step("r7")
    h.assembler.offer_mu(weighted(2))  # archived on offer while an R7 game is open
    plan, _ = h.step("r7")
    assert plan.mu == weighted(2) and calls == [mu(1).snapshot_sha256, mu(2).snapshot_sha256]
    h.close("r7", "stop")
    h.close("plain", "stop")
    r7 = [segment for segment in h.sealed if segment.header["game_instance_id"] == "r7"]
    assert r7 and all(segment.header["audit_retain_mu"] for segment in r7)
    assert not any(segment.header["audit_retain_mu"] for segment in h.sealed
                   if segment.header["game_instance_id"] == "plain")
    identity, weights = archive.read(mu(2).snapshot_sha256)
    assert identity == mu(2).identity() and weights == b"weights-2"
    archive(weighted(2))  # idempotent
    with pytest.raises(HandoffError, match="mu_archive_conflict"):
        archive(weighted(2, b"other bytes"))
    (tmp_path / "mu" / (mu(1).snapshot_sha256 + ".weights")).write_bytes(b"tampered")
    with pytest.raises(HandoffError, match="mu_archive_mismatch"):
        archive.read(mu(1).snapshot_sha256)


def test_audit_game_fails_loud_without_weights_or_archive(tmp_path):
    h = Harness()
    h.assembler.offer_mu(weighted(1))
    with pytest.raises(AssemblyError, match="audit_mu_archive_unconfigured"):
        h.open("r7", audit_retain_mu=True)
    h = Harness(mu_archive=DurableMuArchive(tmp_path))
    h.assembler.offer_mu(mu(1))  # no weights
    with pytest.raises(AssemblyError, match="audit_mu_weights_missing"):
        h.open("r7", audit_retain_mu=True)
    with pytest.raises(AssemblyError, match="invalid_episode_open"):
        h.open("bad", audit_retain_mu="yes")


def test_audit_game_never_scores_with_an_unarchived_mu(tmp_path):
    h = Harness(mu_archive=DurableMuArchive(tmp_path))
    h.assembler.offer_mu(weighted(1))
    h.open("r7", audit_retain_mu=True)
    h.assembler.archived.clear()  # e.g. a mu that arrived while the game was opening
    row, data = h.row("r7")
    with pytest.raises(AssemblyError, match="audit_mu_not_archived"):
        h.assembler.begin_row(row, data)
    assert h.assembler.games["r7"].inflight is None and not h.assembler.games["r7"].segments
