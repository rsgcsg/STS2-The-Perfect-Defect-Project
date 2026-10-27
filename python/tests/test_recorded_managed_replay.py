"""Q's real original/Store/readback seam; Host execution is explicitly replaced."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_recording_store import original, tool  # noqa: F401

from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench import recorded_managed_replay as replay
from spireagent.workbench.p1_recording_store import publish_p1_original

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path, monkeypatch, original, tool):  # noqa: F811
    state = tmp_path / "state"
    state.mkdir()
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    remote = tmp_path / "remote"
    raw = publish_p1_original(store, original, tool, PRODUCER, scratch=state)
    metadata = raw.parameters.value()
    session = {
        "session_id": metadata["session_id"],
        "scope_facts": {
            "closed_at": "2026-10-27T00:00:00+00:00",
            "declared_origin": "declared_human",
        },
    }
    snapshots = []
    importer = SimpleNamespace(
        config=SimpleNamespace(state_dir=state),
        thread=None,
        operation_admission_guard=None,
        lock=threading.RLock(),
        _maintenance={
            "policy": {"enabled": True, "peer": {"kind": "local", "directory": str(remote)}},
            "status": "up_to_date",
            "active": None,
            "sessions": {raw.artifact_id: session},
        },
    )
    importer._save = lambda: snapshots.append(json.loads(json.dumps(importer._maintenance)))
    maintenance = SimpleNamespace(importer=importer, stop=threading.Event())
    config = {
        "schema": replay.SCHEMA,
        "enabled": True,
        "source_root": str(ROOT),
        "candidate": str(tmp_path / "candidate"),
        "language": "zh",
        "budget_ms": 1000,
        "closed_after": "2026-10-26T00:00:00+00:00",
        "execution": {"test": "bound"},
    }
    (state / replay.CONFIG).write_text(json.dumps(config))
    monkeypatch.setattr(replay, "_execution_identity", lambda root: config["execution"])
    monkeypatch.setattr(replay, "_game_idle", lambda root: True)
    monkeypatch.setattr(replay, "source_identity", lambda root: PRODUCER)
    from spireagent.workbench import local_recording_import

    monkeypatch.setattr(
        local_recording_import, "_selected_store", lambda *args, **kwargs: (store, None)
    )
    return maintenance, raw, store, remote, original, snapshots


def test_recording_prevents_dispatch_and_no_attempt_is_created(setup, monkeypatch):
    maintenance, raw, store, _remote, _original, _snapshots = setup
    monkeypatch.setattr(replay, "_game_idle", lambda root: False)
    monkeypatch.setattr(replay, "_run", lambda *args, **kwargs: pytest.fail("game must win"))
    replay.tick(maintenance)
    assert maintenance.importer._maintenance["managed_replays"] == {}
    assert store.manifest_ids() == (raw.artifact_id,)


@pytest.mark.parametrize("unknown", [False, True])
def test_one_durable_attempt_real_original_full_analysis_readback_and_no_retry(
    setup, monkeypatch, unknown
):
    maintenance, raw, store, remote, original_dir, snapshots = setup
    before = {
        str(p.relative_to(original_dir)): p.read_bytes()
        for p in original_dir.rglob("*")
        if p.is_file()
    }
    calls = []

    def run(command, output, root, owner, budget_ms, on_started):
        assert (
            owner.importer._maintenance["managed_replays"][raw.artifact_id]["status"] == "running"
        )
        with pytest.raises(Exception, match="diagnostic_in_progress"):
            owner.importer.operation_admission_guard()
        assert any(s["managed_replays"][raw.artifact_id]["status"] == "running" for s in snapshots)
        on_started(999999999)
        bundle = Path(command[command.index("--bundle") + 1])
        assert (bundle / "content-identity.json").read_bytes() == (
            original_dir / "content-identity.json"
        ).read_bytes()
        calls.append(command)
        result = output / "replay"
        result.mkdir()
        (result / "replay.jsonl").write_text('{"kind":"synthetic-native-replacement"}\n')
        (result / "report.json").write_text(
            json.dumps(
                {
                    "schema": "spireagent/recorded-managed-diagnostic-1",
                    "completed": False,
                    "unknown_delivery": unknown,
                    "planned_points": 10,
                    "compared_points": 2,
                    "matching_points": 1,
                    "planned_surfaces": {"combat_turn": 10},
                    "by_surface": {"combat_turn": {"compared": 2, "matching": 1}},
                    "stopped_surface": "combat_turn",
                    "differences": [{"index": 1}],
                    "first_divergence": {"index": 1},
                    "source_session_id": raw.parameters.value()["session_id"],
                }
            )
        )
        return "finished"

    monkeypatch.setattr(replay, "_run", run)
    replay.tick(maintenance)
    job = maintenance.importer._maintenance["managed_replays"][raw.artifact_id]
    assert job["status"] == "failed" and job["unknown_delivery"] is unknown
    assert job["storage_receipt"]["full_readback"] is True
    assert job["storage_status"] == "preserved"
    restored = ManifestArtifactStore(LocalBlobStore(remote))
    manifest = restored.get_manifest(job["diagnostic_artifact_id"])
    assert manifest.kind == "analysis" and manifest.parent("source") == raw.artifact_id
    assert replay.verify_diagnostic(
        restored, manifest.artifact_id, maintenance.importer.config.state_dir
    )["full_readback"]
    page = (maintenance.importer.config.state_dir / replay.PAGE).read_text()
    assert "combat_turn" in page and ":D1" in page
    replay.tick(maintenance)
    assert len(calls) == 1
    assert maintenance.importer.operation_admission_guard is None
    assert before == {
        str(p.relative_to(original_dir)): p.read_bytes()
        for p in original_dir.rglob("*")
        if p.is_file()
    }


def test_restart_interrupted_attempt_preserves_existing_files_without_world_retry(
    setup, monkeypatch
):
    maintenance, raw, _store, _remote, _original, _snapshots = setup
    output = maintenance.importer.config.state_dir / "managed-replays" / raw.artifact_id
    output.mkdir(parents=True)
    (output / "partial.jsonl").write_text('{"kind":"original-prefix"}\n')
    maintenance.importer._maintenance["managed_replays"] = {
        raw.artifact_id: {
            "status": "running",
            "worker_pid": 999999999,
            "source_artifact_id": raw.artifact_id,
            "session_id": raw.parameters.value()["session_id"],
        }
    }
    monkeypatch.setattr(
        replay, "_run", lambda *args, **kwargs: pytest.fail("unknown must not retry")
    )
    replay.tick(maintenance)
    job = maintenance.importer._maintenance["managed_replays"][raw.artifact_id]
    assert job["status"] == "failed" and job["unknown_delivery"] is None
    assert job["storage_receipt"]["full_readback"] is True
    page = (maintenance.importer.config.state_dir / replay.PAGE).read_text()
    assert "previous_attempt_unconfirmed_no_retry" in page and "unknown 未确认" in page


def test_renderer_includes_skipped_owner_row_without_output_directory(tmp_path):
    identity = "a" * 64
    snapshot = tmp_path / "view.json"
    snapshot.write_text(
        json.dumps(
            {
                "schema": "spireagent/managed-replay-view-1",
                "jobs": {
                    identity: {"status": "skipped", "error": "original_launch_witness_missing"}
                },
            }
        )
    )
    page = tmp_path / "summary.html"
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/p1-render-coverage.mjs"),
            "--directory",
            str(tmp_path / "not-created"),
            "--ledger",
            str(snapshot),
            "--out",
            str(page),
        ],
        check=True,
    )
    assert "original_launch_witness_missing" in page.read_text()


@pytest.mark.skipif(os.name != "posix", reason="M3 worker group support is POSIX only")
@pytest.mark.parametrize("observer_error", [False, True])
def test_owned_group_cleanup_even_after_leader_exit(tmp_path, monkeypatch, observer_error):
    child_pid = tmp_path / "child.pid"
    program = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"open({str(child_pid)!r},'w').write(str(p.pid));"
        + ("time.sleep(60)" if observer_error else "sys.exit(0)")
    )
    maintenance = SimpleNamespace(stop=threading.Event())

    def idle(_root):
        if observer_error:
            raise RuntimeError("observer down")
        return True

    monkeypatch.setattr(replay, "_game_idle", idle)
    disposition = replay._run(
        [sys.executable, "-c", program], tmp_path, tmp_path, maintenance, 1000
    )
    pid = int(child_pid.read_text())
    try:
        observed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True
        )
        assert (
            observed.returncode != 0
            or not observed.stdout.strip()
            or observed.stdout.strip().startswith("Z")
        )
        assert disposition == ("game_observer_failed_unknown" if observer_error else "finished")
    finally:
        with __import__("contextlib").suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def test_renderer_keeps_unknown_owner_row_when_worker_report_is_half_written(tmp_path):
    identity = "b" * 64
    snapshot = tmp_path / "view.json"
    snapshot.write_text(
        json.dumps(
            {
                "schema": "spireagent/managed-replay-view-1",
                "jobs": {
                    identity: {
                        "status": "failed",
                        "unknown_delivery": True,
                        "error": "worker_interrupted",
                    }
                },
            }
        )
    )
    directory = tmp_path / "replays"
    output = directory / identity / "replay"
    output.mkdir(parents=True)
    partial = b'{"completed":false,'
    (output / "report.json").write_bytes(partial)
    page = tmp_path / "summary.html"
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/p1-render-coverage.mjs"),
            "--directory",
            str(directory),
            "--ledger",
            str(snapshot),
            "--out",
            str(page),
        ],
        check=True,
    )
    content = page.read_text()
    assert identity in content and "unknown true" in content
    assert "incomplete_or_invalid_report_json" in content
    assert (output / "report.json").read_bytes() == partial
