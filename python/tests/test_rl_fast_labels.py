"""fast_chain public terminal label (old-chain equal) and R7 mu archive (audit_retain_mu)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_rl_fast_assembly import Harness, mu

from stpd.rl.fast_assembly import AssemblyError, MuHandoff, public_terminal_label
from stpd.rl.fast_handoff import DurableMuArchive, HandoffError

# (acts, result, max floor) -> (class, reward): the old chain's own values
# (boss_clearance -> episode_reward A0 -> checked_terminal_class), pinned.
CASES = [
    (([1, 1, 1], "defeat", 5), (5, 0.5 * (5 / 48) ** 2)),
    (([1] * 6, "defeat", 17), (17, 0.5 * (17 / 48) ** 2)),   # lost at the act 1 Boss
    (([1, 1, 2, 2], "defeat", 18), (18, 0.5 * (18 / 48) ** 2 + 0.1)),  # act advance
    (([1, 2, 2, 3, 3], "defeat", 40), (40, 0.5 * (40 / 48) ** 2 + 0.2)),
    (([1, 2, 2, 2], "defeat", 35), (50, 0.5 * (35 / 48) ** 2 + 0.1)),  # Golden Compass
    (([1, 2, 3, 3], "defeat", 35), (35, 0.5 * (35 / 48) ** 2 + 0.2)),
    (([1, 2, 3, 3], "victory", 48), (49, 1.0)),
    (([1, 2, 3, 3], "defeat", 48), (48, 0.5 + 0.2)),          # lost at the final Boss
]


@pytest.mark.parametrize(("case", "expected"), CASES)
def test_public_terminal_label_is_the_old_chain_label(case, expected):
    acts, result, floor = case
    label = public_terminal_label({"result": result, "max_floor": floor, "acts": acts})
    assert label["class"] == expected[0]
    assert label["reward"] == pytest.approx(expected[1], abs=1e-12)


def test_public_terminal_label_refuses_what_the_old_chain_refuses():
    # From a save in act 2 nothing public shows Boss 1; the old chain refuses the class.
    with pytest.raises(ValueError, match="terminal_class_reward_mismatch"):
        public_terminal_label({"result": "defeat", "max_floor": 30, "acts": [2, 2, 2]})
    # a skipped act is no witness: [1, 3, 4] clears one Boss, floor 40 needs two
    with pytest.raises(ValueError, match="terminal_class_reward_mismatch"):
        public_terminal_label({"result": "defeat", "max_floor": 40, "acts": [1, 3, 4]})
    for context in ({"result": "abandoned", "max_floor": 5, "acts": [1]},
                    {"result": "defeat", "max_floor": "5", "acts": [1]},
                    {"result": "defeat", "max_floor": 5}):
        with pytest.raises(ValueError, match="terminal_label_context"):
            public_terminal_label(context)


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
        "result": "defeat", **public_terminal_label(
            {"result": "defeat", "max_floor": 35, "acts": [1, 2, 3, 3]})}
    # The old chain refuses this class too; kept as original, counted, never trained.
    assert save.kind == "truncated"
    assert save.header["seal"]["truncation"]["reason"] == "terminal_label_refused"
    assert assembler.report()["terminal_label_refused"] == 1


def weighted(index: int, payload: bytes | None = None, generation: str = "gen-1") -> MuHandoff:
    base = mu(index, generation)
    return MuHandoff(base.snapshot_sha256, base.training_generation, base.version, base.updates,
                     weights=payload if payload is not None else f"weights-{index}".encode())


# Stand-in for WP4's snapshot identity of pushed weights (segment_learner digests).
SNAPSHOTS = {f"weights-{index}".encode(): mu(index).snapshot_sha256 for index in range(10)}


def snapshot_of(weights: bytes) -> str:
    return SNAPSHOTS.get(weights, "")


def archive_at(root: Path) -> DurableMuArchive:
    return DurableMuArchive(root, snapshot_of=snapshot_of)


def test_audit_game_archives_only_the_mu_it_binds_before_scoring(tmp_path):
    archive = archive_at(tmp_path)
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
    assert calls == []  # archived when a segment binds it, not at open
    h.step("r7")
    assert calls == [weighted(1).snapshot_sha256]
    for _ in range(32):
        h.step("r7")
    h.assembler.offer_mu(weighted(2))  # mid-segment: never bound by the R7 game
    for _ in range(31):
        h.step("r7")
    h.assembler.offer_mu(weighted(3))
    plan, _ = h.step("r7")  # the second segment binds mu3
    assert plan.mu == weighted(3) and calls == [mu(1).snapshot_sha256, mu(3).snapshot_sha256]
    assert h.assembler.report()["mu_archived"] == 2
    h.close("r7", "stop")
    h.close("plain", "stop")
    r7 = [segment for segment in h.sealed if segment.header["game_instance_id"] == "r7"]
    assert r7 and all(segment.header["audit_retain_mu"] for segment in r7)
    assert not any(segment.header["audit_retain_mu"] for segment in h.sealed
                   if segment.header["game_instance_id"] == "plain")
    with pytest.raises(HandoffError, match="mu_archive_mismatch"):
        archive.read(mu(2).snapshot_sha256, "gen-1")  # mu2 was never kept
    archive(weighted(2))
    identity, weights = archive.read(mu(2).snapshot_sha256, "gen-1")
    assert identity == mu(2).identity() and weights == b"weights-2"
    archive(weighted(2))  # idempotent
    with pytest.raises(HandoffError, match="mu_archive_conflict"):
        archive(MuHandoff(mu(2).snapshot_sha256, "gen-1", 9, 9, weights=b"weights-2"))
    (tmp_path / "mu" / (mu(1).snapshot_sha256 + ".weights")).write_bytes(b"tampered")
    with pytest.raises(HandoffError, match="mu_archive_mismatch"):
        archive.read(mu(1).snapshot_sha256, "gen-1")


def test_archive_checks_weights_against_the_snapshot(tmp_path):
    archive = archive_at(tmp_path)
    with pytest.raises(HandoffError, match="mu_archive_weights_not_snapshot"):
        archive(weighted(2, b"weights-3"))  # bytes of another snapshot
    assert not list((tmp_path / "mu").iterdir())
    with pytest.raises(HandoffError, match="mu_archive_requires_snapshot_check"):
        DurableMuArchive(tmp_path, snapshot_of=None)  # type: ignore[arg-type]


def test_archive_keeps_one_manifest_per_generation(tmp_path):
    archive = archive_at(tmp_path)
    archive(weighted(1))
    archive(weighted(1, generation="gen-2"))  # CP restore: same weights, new generation
    assert archive.read(mu(1).snapshot_sha256, "gen-1")[0] == mu(1).identity()
    assert archive.read(mu(1).snapshot_sha256, "gen-2")[0] == mu(1, "gen-2").identity()
    assert len(list((tmp_path / "mu").glob("*.json"))) == 2


def test_stale_temporary_never_blocks_and_bad_manifest_is_typed(tmp_path):
    archive = archive_at(tmp_path)
    snapshot = mu(1).snapshot_sha256
    stale = tmp_path / "mu" / f".{snapshot}.weights.tmp"
    stale.write_bytes(b"half")  # a crash between create and rename
    archive(weighted(1))
    assert stale.read_bytes() == b"half"  # leftover kept, not an original
    manifest = next((tmp_path / "mu").glob("*.json"))
    for broken in (b"{", b"{}", b'{"schema": 1, "mu": []}'):
        manifest.write_bytes(broken)
        with pytest.raises(HandoffError, match="mu_archive_mismatch"):
            archive.read(snapshot, "gen-1")
    with pytest.raises(HandoffError, match="mu_archive_mismatch"):
        archive.read(snapshot, "gen-missing")


def test_audit_game_fails_loud_without_weights_or_archive(tmp_path):
    h = Harness()
    h.assembler.offer_mu(weighted(1))
    with pytest.raises(AssemblyError, match="audit_mu_archive_unconfigured"):
        h.open("r7", audit_retain_mu=True)
    assert "r7" not in h.assembler.games  # a failed open leaves no game behind
    h = Harness(mu_archive=archive_at(tmp_path))
    h.assembler.offer_mu(mu(1))  # no weights
    h.open("r7", audit_retain_mu=True)
    row, data = h.row("r7")
    with pytest.raises(AssemblyError, match="audit_mu_weights_missing"):
        h.assembler.begin_row(row, data)  # its first segment would bind mu1
    assert h.assembler.games["r7"].inflight is None and not h.assembler.games["r7"].segments
    with pytest.raises(AssemblyError, match="invalid_episode_open"):
        h.open("bad", audit_retain_mu="yes")
    h.assembler.offer_mu(weighted(2))
    h.step("r7")  # the same row plans once a mu with weights is latest


def test_mu_that_arrives_while_an_audit_segment_archives_is_archived_and_bound(tmp_path):
    archive = archive_at(tmp_path)
    calls: list[str] = []
    h = Harness()

    def racing(handoff: MuHandoff) -> None:
        calls.append(handoff.snapshot_sha256)
        archive(handoff)
        if len(calls) == 1:  # a newer mu became latest while mu1 was being written
            with h.assembler.lock:
                h.assembler.ledger.offer(weighted(2))

    h.assembler.mu_archive = racing
    h.assembler.offer_mu(weighted(1))
    h.open("r7", audit_retain_mu=True)
    plan, _ = h.step("r7")
    assert calls == [mu(1).snapshot_sha256, mu(2).snapshot_sha256]
    assert plan.mu == weighted(2)


def test_audit_game_never_scores_with_an_unarchived_mu(tmp_path):
    archive = archive_at(tmp_path)
    full = {"on": True}

    def bounded(handoff: MuHandoff) -> None:
        if full["on"]:
            raise HandoffError("mu_archive_full")
        archive(handoff)

    h = Harness(mu_archive=bounded)
    h.assembler.offer_mu(weighted(1))
    h.open("r7", audit_retain_mu=True)
    row, data = h.row("r7")
    with pytest.raises(AssemblyError, match="audit_mu_archive_failed:mu_archive_full"):
        h.assembler.begin_row(row, data)  # refused loudly, nothing planned
    assert h.assembler.games["r7"].inflight is None and not h.assembler.games["r7"].segments
    assert h.assembler.report()["mu_archive_failed"] == 1
    full["on"] = False
    h.assembler.begin_row(row, data)  # an archive record lost is written again first
    assert archive.read(mu(1).snapshot_sha256, "gen-1")[0] == mu(1).identity()


@pytest.mark.parametrize("start", [
    {"kind": "from_save", "save_sha256": "d" * 64, "floor": 20},          # no act
    {"kind": "from_save", "save_sha256": "d" * 64, "act": 0, "floor": 20},
    {"kind": "from_save", "save_sha256": "d" * 64, "act": 2},             # no floor
    {"kind": "from_save", "save_sha256": "d" * 64, "act": 2, "floor": "20"},
    {"kind": "resume"},
])
def test_start_without_contract_fields_is_refused(start):
    h = Harness()
    h.assembler.offer_mu(mu(1))
    with pytest.raises(AssemblyError, match="invalid_episode_open"):
        h.open("g1", start=start)
    assert not h.assembler.games


def test_labeler_breaking_its_contract_keeps_the_original_and_raises():
    for labeler, error, detail in (
            (lambda summary: {"class": True, "reward": 0.1}, AssemblyError,
             "invalid_terminal_label"),
            (lambda summary: summary["missing"], KeyError, "KeyError")):
        h = Harness(labeler)
        h.assembler.offer_mu(mu(1))
        h.open("g1")
        _, row = h.step("g1", outcome=False)
        h.outcome(row, terminal={"result": "defeat"})
        with pytest.raises(error):
            h.close("g1", "terminal", row["step_index"])
        [segment] = h.sealed
        assert segment.kind == "truncated"
        assert segment.header["seal"]["truncation"] == {"reason": "terminal_label_invalid",
                                                        "detail": detail}
        assert h.assembler.report()["terminal_label_refused"] == 0
