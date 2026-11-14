"""CARD-RL5 §12.6 B: trained fast-chain originals are deleted once a durable CP covers them."""
from __future__ import annotations

import dataclasses
import multiprocessing
import time
from pathlib import Path
from typing import Any

import pytest
from test_rl_dataplane_shards import game, learner, parts, train
from test_rl_fast_source import closed_for, send_labels
from test_rl_segment_learner import read_cp

from spireagent.json_boundary import BoundaryError
from stpd.rl.fast_handoff import ShmSegmentSender
from stpd.rl.fast_release import SegmentRelease
from stpd.rl.segment_learner import SegmentLearner

# -- the owner alone ------------------------------------------------------------------


class Plane:
    """A fake data-plane owner around one SegmentRelease."""

    def __init__(self, root: Path) -> None:
        self.root, self.holding, self.is_fenced = root, set(), False
        self.alarms: list[tuple[str, dict[str, Any]]] = []
        self.release = SegmentRelease(
            held=lambda: set(self.holding), fenced=lambda: self.is_fenced,
            alarm=lambda kind, **fields: self.alarms.append((kind, fields)))

    def segment(self, instance: str, index: int, *, r7: bool = False) -> Path:
        segment_id = f"{instance}-asm.{index}"
        path = self.root / "bootstrap" / (segment_id + ".stfs")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 100)
        self.release.received({"segment_id": segment_id, "path": str(path), "size": 100},
                              {"game_instance_id": instance, "audit_retain_mu": r7})
        return path

    def close(self, instance: str, count: int, **change: Any) -> None:
        message = {"game_instance_id": instance, "close_reason": "terminal", "failure": None,
                   "segments": {"bootstrap": count - 1, "terminal": 1, "truncated": 0,
                                "rejected": 0}}
        message.update(change)
        self.release.closed(message, excluded=False)

    def checkpoint(self, sequence: int) -> None:
        self.release.capture(sequence)
        self.release.covered(sequence)

    def drain(self) -> dict[str, Any]:
        self.release.close()
        return self.release.status()


def test_clean_game_is_deleted_after_one_capture_of_grace_and_a_published_cp(tmp_path):
    plane = Plane(tmp_path)
    paths = [plane.segment("g1", i) for i in range(2)]
    plane.close("g1", 2)
    plane.checkpoint(1)  # the close is newer than this capture: grace
    assert all(path.exists() for path in paths)
    plane.release.capture(2)  # marked, not yet covered by a published CP
    time.sleep(0.05)
    assert all(path.exists() for path in paths)
    plane.release.covered(2)
    status = plane.drain()
    assert not any(path.exists() for path in paths)
    assert status["deleted"] == 2 and status["deleted_bytes"] == 200
    assert status["pending_bytes"] == 0 and status["peak_pending_bytes"] == 200


def test_held_segments_wait_and_open_games_are_never_marked(tmp_path):
    plane = Plane(tmp_path)
    held = plane.segment("g1", 0)
    done = plane.segment("g1", 1)
    still_open = plane.segment("g2", 0)
    plane.close("g1", 2)
    plane.holding = {"g1-asm.0"}
    plane.checkpoint(1)
    plane.checkpoint(2)
    time.sleep(0.05)
    assert held.exists() and still_open.exists()
    plane.holding = set()  # trained now
    plane.checkpoint(3)
    status = plane.drain()
    assert not held.exists() and not done.exists() and still_open.exists()
    assert status["deleted"] == 2 and status["tracked_open_bytes"] == 100


@pytest.mark.parametrize("change", [
    {"close_reason": "unknown"}, {"close_reason": "env_crash"},
    {"failure": "failed:host_receipt_mismatch"},
    {"segments": {"bootstrap": 1, "terminal": 1, "truncated": 0, "rejected": 1}},
    {"segments": {"bootstrap": 2, "terminal": 1, "truncated": 0, "rejected": 0}},  # short
    # Censored (stop/budget, a truncated tail) keeps everything too.
    {"close_reason": "stop", "segments": {"bootstrap": 1, "terminal": 1, "truncated": 1,
                                          "rejected": 0}},
    {"close_reason": "budget"},
])
def test_abnormal_games_keep_every_original(tmp_path, change):
    plane = Plane(tmp_path)
    paths = [plane.segment("g1", i) for i in range(2)]
    plane.close("g1", 2, **change)
    for sequence in (1, 2, 3):
        plane.checkpoint(sequence)
    status = plane.drain()
    assert all(path.exists() for path in paths)
    assert status["kept_abnormal_segments"] == 2 and status["deleted"] == 0


def test_r7_games_and_exclusions_keep_every_original(tmp_path):
    plane = Plane(tmp_path)
    audit = [plane.segment("r7", 0, r7=True), plane.segment("r7", 1)]
    late = [plane.segment("late", 0)]
    plane.close("r7", 2)
    plane.close("late", 1)
    plane.release.excluded("late")  # the actor's evidence report after the close
    plane.release.excluded("never-here")  # another shard's instance: ignored
    for sequence in (1, 2, 3):
        plane.checkpoint(sequence)
    status = plane.drain()
    assert all(path.exists() for path in audit + late)
    assert status["kept_r7_segments"] == 2 and status["kept_abnormal_segments"] == 1
    assert status.get("excluded_after_release", 0) == 0


def test_an_exclusion_after_partial_marking_is_reported(tmp_path):
    plane = Plane(tmp_path)
    plane.segment("g1", 0)
    held = plane.segment("g1", 1)
    plane.close("g1", 2)
    plane.holding = {"g1-asm.1"}
    plane.release.capture(1)
    plane.release.capture(2)  # g1-asm.0 marked, g1-asm.1 still held
    plane.release.excluded("g1")
    status = plane.drain()
    assert held.exists()
    assert status["excluded_after_release"] == 1 and status["kept_abnormal_segments"] == 1


def test_nothing_is_deleted_after_a_fence(tmp_path):
    plane = Plane(tmp_path)
    path = plane.segment("g1", 0)
    plane.close("g1", 1)
    plane.checkpoint(1)
    plane.is_fenced = True
    plane.checkpoint(2)
    status = plane.drain()
    assert path.exists() and status["kept_fenced"] == 1 and status["pending_bytes"] == 0


def test_a_failed_delete_is_counted_and_loud_and_the_rest_go_on(tmp_path, capsys):
    plane = Plane(tmp_path)
    gone = plane.segment("g1", 0)
    other = plane.segment("g1", 1)
    plane.close("g1", 2)
    gone.unlink()  # someone removed it first
    plane.checkpoint(1)
    plane.checkpoint(2)
    status = plane.drain()
    assert not other.exists()
    assert status["delete_failed"] == 1 and status["deleted"] == 1
    assert plane.alarms and plane.alarms[0][0] == "fast_segment_release_delete_failed"
    assert "delete_failed" in capsys.readouterr().err
    # An exclusion that arrives after the release is reported, never silent.
    plane2 = Plane(tmp_path / "b")
    plane2.segment("g9", 0)
    plane2.close("g9", 1)
    plane2.checkpoint(1)
    plane2.checkpoint(2)
    plane2.release.excluded("g9")
    assert plane2.drain()["excluded_after_release"] == 1


def test_a_path_that_does_not_name_the_segment_is_never_tracked(tmp_path):
    plane = Plane(tmp_path)
    plane.release.received({"segment_id": "g1-asm.0", "path": __file__, "size": 1},
                           {"game_instance_id": "g1"})
    plane.close("g1", 1)
    plane.checkpoint(1)
    plane.checkpoint(2)
    assert plane.drain()["untracked_path"] == 1 and Path(__file__).exists()


# -- the real learner (shards 0 and 1) ------------------------------------------------


def _sender_at(connection: Any, segments: list[Any], close: dict[str, Any],
               root: str) -> None:
    """The assembler service's order: original on disk, hand-off naming it, close."""
    sender = ShmSegmentSender(connection, assembler_id=segments[0].header["assembler_instance"])
    for segment in segments:
        path = Path(root) / segment.header["seal"]["kind"] / (segment.segment_id + ".stfs")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(segment.data)
        sender.send(segment, path)
    send_labels(sender, segments)
    connection.send(close)
    sender.finish()


def hand_off_at(owner: SegmentLearner, sealed: list[Any], root: Path, **close: Any) -> None:
    ours, theirs = multiprocessing.Pipe()
    process = multiprocessing.Process(target=_sender_at, args=(
        theirs, sealed, closed_for(sealed, **close), str(root)))
    process.start()
    theirs.close()
    reader = owner.attach_fast_handoff(ours)
    process.join(60)
    assert process.exitcode == 0
    reader.join(60)
    assert not reader.is_alive()


def release_status(owner: SegmentLearner) -> dict[str, Any]:
    return dict(owner.fast_status()["fast_chain"].get("segment_release", {}))


def originals(root: Path, sealed: list[Any]) -> list[Path]:
    return [root / s.header["seal"]["kind"] / (s.segment_id + ".stfs") for s in sealed]


@pytest.mark.parametrize("shards", [0, 1])
def test_learner_deletes_trained_clean_originals_keeps_abnormal_and_restores_from_cp(
        tmp_path, monkeypatch, shards):
    config, io = parts(tmp_path / "a", monkeypatch, shards, release_trained_segments=True)
    owner = SegmentLearner(config, tmp_path / "a" / "owner", io)
    root = tmp_path / "segments"
    try:
        clean = [game(owner, 1), game(owner, 2)]
        crashed = game(owner, 3)
        for sealed in clean:
            hand_off_at(owner, sealed, root)
        hand_off_at(owner, crashed, root, close_reason="env_crash")
        assert not owner.input_fenced
        train(owner, 2)
        owner.checkpoint(final=True)  # first capture after the closes: grace
        owner.checkpoint(final=True)  # marks the trained ones; published -> deleted
        record, cp = read_cp(owner)
        if shards:
            queued_rows = sum(shard["queue_rows"] for shard in owner.fast_status()[
                "fast_chain"]["shards"]["per_shard"])
        else:
            held = {r["segment_id"] for r in owner.queue.pending}
            queued_rows = owner.queue.rows
    finally:
        owner.close()  # finishes the deletions already due (each shard's too)
    status = release_status(owner)
    kept_rows = 0
    for sealed in clean:
        for segment, path in zip(sealed, originals(root, sealed), strict=True):
            # Trained (or dropped) -> gone; still queued -> kept for training.
            kept_rows += len(segment.header["rows"]) if path.exists() else 0
            if not shards:
                assert path.exists() == (segment.segment_id in held), segment.segment_id
    assert kept_rows == queued_rows
    assert all(path.exists() for path in originals(root, crashed))
    assert status["deleted"] >= 2 and status["kept_abnormal_segments"] == len(crashed)
    assert status.get("delete_failed", 0) == 0

    # A restore reads the CP only: with every remaining original gone it resumes the same.
    for path in root.rglob("*.stfs"):
        path.unlink()
    request = dataclasses.replace(
        config, start_package=None, resume_checkpoint=record["checkpoint_artifact_id"],
        segments=dataclasses.replace(config.segments, training_generation="g-restored"))
    restored = SegmentLearner(request, tmp_path / "restored", dataclasses.replace(
        io, dataplane={**io.dataplane, "root": str(tmp_path / "live-restored")}))
    try:
        assert restored.updates == cp.counters.updates
        assert not restored.input_fenced
    finally:
        restored.close()


def test_release_is_off_by_default_and_the_option_is_checked(tmp_path, monkeypatch):
    owner = learner(tmp_path / "a", monkeypatch, 0)
    try:
        assert owner.segment_release is None and "segment_release" not in (
            owner.fast_status()["fast_chain"])
    finally:
        owner.close()
    with pytest.raises(ValueError, match="release_trained_segments_must_be_bool"):
        learner(tmp_path / "b", monkeypatch, 0, release_trained_segments="yes")
    with pytest.raises(BoundaryError, match="release_trained_segments_requires_fast_chain"):
        learner(tmp_path / "c", monkeypatch, 0, fast_chain=False,
                release_trained_segments=True)
