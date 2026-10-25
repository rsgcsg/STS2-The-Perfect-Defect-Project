"""Cache deletion joins exact Store inventory; typed transport is isolated here."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import evaluation_cache as owner


def fixture_app(tmp_path, monkeypatch, *, typed="pass", changed=False):
    state = tmp_path / "state"
    directory = state / "models/agent-runs/run-original"
    directory.mkdir(parents=True)
    (directory / "original.bin").write_bytes(b"immutable original")
    restored = tmp_path / "empty-readback"
    restored.mkdir()
    (restored / "original.bin").write_bytes(b"changed" if changed else b"immutable original")
    receipt = {"status": "completed", "run_id": "run-original", "artifact_id": "a" * 64,
               "peer": {"kind": "local"}, "peer_readback": {
                   "run_id": "run-original", "full_readback": True, "typed_verification": typed}}
    app = SimpleNamespace(
        config=SimpleNamespace(state_dir=state),
        models=SimpleNamespace(lock=threading.RLock(), directory=state / "models", thread=None,
            stopped_agent_evidence_context=lambda _run: {
                "directory": str(directory), "expected": {"run_id": "run-original"}}),
        local_recording_import=SimpleNamespace(lock=threading.RLock(), status=lambda: receipt),
        reference_evaluation=SimpleNamespace(check_access=lambda _action: None))

    @contextmanager
    def materialize(peer, artifact, *, expected, scratch):
        assert artifact == "a" * 64 and expected["run_id"] == "run-original"
        yield SimpleNamespace(run_id="run-original", content_id="b" * 64, directory=restored)

    monkeypatch.setattr(owner, "open_peer", lambda peer: peer)
    monkeypatch.setattr(owner, "materialize_global_evidence", materialize)
    monkeypatch.setattr(owner, "no_handles", lambda _directory: None)
    return app, directory


def test_exact_restored_duplicate_cools_and_retains_receipt(tmp_path, monkeypatch):
    app, directory = fixture_app(tmp_path, monkeypatch)
    receipt = owner.cool_completed(app, "run-original")
    assert receipt["status"] == "verified_duplicate_cooled"
    assert receipt["typed_empty_directory_readback"] is True
    assert receipt["full_inventory_equal"] is True and not directory.exists()
    assert (app.config.state_dir / "evaluation-cache-receipts/run-original.json").is_file()


@pytest.mark.parametrize("typed,changed", [("fail", False), ("pass", True)])
def test_unqualified_or_changed_readback_preserves_original(tmp_path, monkeypatch, typed, changed):
    app, directory = fixture_app(tmp_path, monkeypatch, typed=typed, changed=changed)
    with pytest.raises(BoundaryError, match="readback_required|inventory_changed"):
        owner.cool_completed(app, "run-original")
    assert (directory / "original.bin").read_bytes() == b"immutable original"


def test_reader_appearing_after_restore_prevents_cooling(tmp_path, monkeypatch):
    app, directory = fixture_app(tmp_path, monkeypatch)
    calls = []

    def handles(_directory):
        calls.append(True)
        if len(calls) == 2:
            raise BoundaryError("native_episode", "native_process_occupancy_unproved")

    monkeypatch.setattr(owner, "no_handles", handles)
    with pytest.raises(BoundaryError, match="occupancy_unproved"):
        owner.cool_completed(app, "run-original")
    assert (directory / "original.bin").read_bytes() == b"immutable original"
