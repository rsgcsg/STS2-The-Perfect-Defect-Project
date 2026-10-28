"""Normal worker I/O scheduling: frozen snapshots, failure recovery and fencing."""
from __future__ import annotations

import io
import threading
from dataclasses import replace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_periodic_safe_pause import longer_run
from test_p1_workload import small_run

from spireagent.json_boundary import BoundaryError
from spireagent.storage.blobs import StoreError
from spireagent.storage.store import ManifestArtifactStore
from stpd.cloud_jobs.contracts import ComputeRequest
from stpd.cloud_jobs.execution import CandidateReporter, dispatch
from stpd.p1.formal_training import MStdTrainer
from stpd.p1.spec import MStdConfig
from stpd.p1.training import maximum_difference
from stpd.p1.workload import prepare_p1_run, verify_p1_run
from stpd.workers.checkpoint_codec import decode_checkpoint
from stpd.workers.p1_run import execute_p1_run, verify_p1_checkpoint
from stpd.workers.training_io import ProgressCadence


def events(store, run, attempt):
    return [e.parameters.value() for e in CandidateReporter(store, run.artifact_id, PRODUCER)
            .events(run.artifact_id) if e.parameters.value()["attempt"] == attempt]


def test_sparse_progress_keeps_complete_checkpoint_history_and_every_dev(tmp_path, monkeypatch):
    import stpd.workers.p1_run as worker

    store, run = longer_run(tmp_path)
    # Deterministic step cadence rather than a host-speed-dependent timer.
    monkeypatch.setattr(worker, "PROGRESS_STEPS", 50)
    monkeypatch.setattr(worker, "PROGRESS_SECONDS", 1_000_000_000)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "sparse")
    result = dispatch(store, request, PRODUCER)
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    saved = verify_p1_checkpoint(store, result.checkpoint_id, workload)
    rows = events(store, run, request.attempt_id)
    progress = [r["details"] for r in rows if r["kind"] == "progress"]
    assert [r["step"] for r in rows] == list(range(1, len(rows) + 1))
    assert 1 < len(progress) < saved.state["step"] == 8
    assert [r["step"] for r in saved.state["history"]] == list(range(1, 9))
    measured = {r["step"] for r in progress if "dev" in r or "dev_checkpoint" in r}
    required = {r["step"] for key in ("dev_history", "checkpoint_dev_history")
                for r in saved.state[key]}
    assert measured == required
    # Every event can update the UI; a late checkpoint must not reverse it.
    updates = [r["details"]["optimizer_updates"] for r in rows]
    assert updates == sorted(updates)


def test_checkpoint_upload_overlaps_advance_but_state_and_lineage_stay_frozen(
    tmp_path, monkeypatch,
):
    store, snapshot, _ = small_run(tmp_path)
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER,
                         MStdConfig(max_epochs=3), "c" * 32)
    entered, release = threading.Event(), threading.Event()
    original_put, advance = ManifestArtifactStore.put_payload, MStdTrainer.advance
    captured = []
    owner = threading.get_ident()

    def blocked_upload(self, role, source, media_type="application/octet-stream"):
        if role == "checkpoint" and not entered.is_set():
            raw = source.read()
            captured.append((decode_checkpoint(raw)["step"], threading.get_ident()))
            entered.set()
            assert release.wait(10), "owner must advance and release the first upload"
            return original_put(self, role, io.BytesIO(raw), media_type)
        return original_put(self, role, source, media_type)

    def observed_advance(trainer):
        progress = advance(trainer)
        if trainer.step == 2:
            assert entered.wait(10)
            assert captured[0][0] == 1
            release.set()
        return progress

    monkeypatch.setattr(ManifestArtifactStore, "put_payload", blocked_upload)
    monkeypatch.setattr(MStdTrainer, "advance", observed_advance)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "overlap", stop_after=2)
    result = dispatch(store, request, PRODUCER)
    assert result.state == "paused" and release.is_set()
    assert captured[0][1] != owner
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    rows = events(store, run, request.attempt_id)
    for row in rows:
        if row["kind"] == "checkpoint":
            saved = verify_p1_checkpoint(store, row["details"]["checkpoint_id"], workload)
            assert saved.state["step"] == row["details"]["optimizer_updates"]
    assert not any(t.name.startswith("p1-checkpoint") for t in threading.enumerate())


def test_failed_background_checkpoint_keeps_prior_resume_exact(tmp_path, monkeypatch):
    store, _, run = small_run(tmp_path)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "reference")
    full = dispatch(store, request, PRODUCER)
    original_put = ManifestArtifactStore.put_payload
    calls = 0

    def fail_second_checkpoint(self, role, source, media_type="application/octet-stream"):
        nonlocal calls
        if role == "checkpoint":
            calls += 1
            if calls == 2:
                raise StoreError("simulated_checkpoint_upload_failure")
        return original_put(self, role, source, media_type)

    monkeypatch.setattr(ManifestArtifactStore, "put_payload", fail_second_checkpoint)
    broken = replace(request, attempt_id="broken")
    with pytest.raises(StoreError, match="simulated_checkpoint_upload_failure"):
        dispatch(store, broken, PRODUCER)
    rows = events(store, run, broken.attempt_id)
    assert not any(r["kind"] == "completed" for r in rows)
    prior = [r["details"]["checkpoint_id"] for r in rows if r["kind"] == "checkpoint"]
    assert len(prior) == 1
    assert not any(t.name.startswith("p1-checkpoint") for t in threading.enumerate())
    monkeypatch.setattr(ManifestArtifactStore, "put_payload", original_put)
    resumed = dispatch(store, replace(request, attempt_id="resume", resume=prior[0]), PRODUCER)
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    expected = verify_p1_checkpoint(store, full.checkpoint_id, workload)
    actual = verify_p1_checkpoint(store, resumed.checkpoint_id, workload)
    assert maximum_difference(expected.state, actual.state) == 0.0


def test_authorized_channel_stays_on_owner_and_rechecks_before_publication(tmp_path, monkeypatch):
    store, _, run = small_run(tmp_path)
    owner = threading.get_ident()
    original_put = store.put_payload
    revoked = False

    class Authority:
        def assert_current(self, *args):
            assert threading.get_ident() == owner
            if revoked:
                raise BoundaryError("p1_test", "writer_revoked")

        def authorize_resume(self, *args):
            raise AssertionError("this is a fresh Run")

    def revoke_after_payload(role, source, media_type="application/octet-stream"):
        nonlocal revoked
        assert threading.get_ident() == owner
        payload = original_put(role, source, media_type)
        if role == "checkpoint":
            revoked = True
        return payload

    monkeypatch.setattr(store, "put_payload", revoke_after_payload)
    reporter = CandidateReporter(store, run.artifact_id, PRODUCER)
    with pytest.raises(BoundaryError, match="writer_revoked"):
        execute_p1_run(store, reporter, run.artifact_id, PRODUCER,
                       attempt_id="fenced", authority=Authority())
    assert revoked
    assert not any(r["kind"] in {"checkpoint", "completed"}
                   for r in events(store, run, "fenced"))
    assert not any(store.get_manifest(i).kind == "checkpoint" for i in store.manifest_ids())


def test_progress_cadence_uses_step_or_time_and_retains_only_latest():
    clock = [0.0]
    cadence = ProgressCadence(0, clock=lambda: clock[0])
    assert not cadence.offer(1, {"loss": 2.0})
    assert cadence.offer(50, {"loss": 1.0})
    assert cadence.pending == {"loss": 1.0, "optimizer_updates": 50}
    cadence.published()
    assert cadence.pending is None
    clock[0] = 30.0
    assert cadence.offer(51, {"loss": .9})


def test_derived_checkpoint_rechecks_authority_after_payload_before_manifest(tmp_path, monkeypatch):
    from test_p1_state_derivation import derive, legacy_fixture

    store, snapshot, original, checkpoint = legacy_fixture(tmp_path)
    run = derive(store, snapshot, original, checkpoint)
    original_put = store.put_payload
    revoked = False

    class Authority:
        def assert_current(self, *args):
            if revoked:
                raise BoundaryError("p1_test", "derived_writer_revoked")

        def authorize_resume(self, *args):
            raise AssertionError("fresh derived Run")

    def revoke(role, source, media_type="application/octet-stream"):
        nonlocal revoked
        payload = original_put(role, source, media_type)
        if role == "checkpoint":
            revoked = True
        return payload

    monkeypatch.setattr(store, "put_payload", revoke)
    with pytest.raises(BoundaryError, match="derived_writer_revoked"):
        execute_p1_run(store, CandidateReporter(store, run.artifact_id, PRODUCER),
                       run.artifact_id, PRODUCER, attempt_id="derived-fenced",
                       authority=Authority())
    assert revoked
    assert not [store.get_manifest(i) for i in store.manifest_ids()
                if store.get_manifest(i).kind == "checkpoint"
                and store.get_manifest(i).parent("run") == run.artifact_id]
