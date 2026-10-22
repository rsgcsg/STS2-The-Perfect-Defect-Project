"""Normal CLI/HTTP control over the existing Scheduler and a synthetic CPU provider."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_hub_scheduler import Provider
from test_p1_application import workspace

from spireagent.hub.database import Operations
from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.workbench.developer import ROOT, ProjectConfig, RemoteComputeConfig
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.p1_recording_storage import open_peer
from spireagent.workbench.p1_training_data import reserve_p1_snapshot
from spireagent.workbench.remote_training import RemoteTrainingService
from spireagent.workbench.training_cli import main, training_command
from stpd.p1.spec import MStdConfig
from stpd.p1.workload import verify_p1_run
from stpd.workers.p1_run import verify_p1_result


@contextmanager
def client(tmp_path, *, reserve=True, peer_binding=None):
    config, store, owner, snapshot = workspace(tmp_path)
    if reserve:
        reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    binding = peer_binding or {"kind": "local", "directory": str(tmp_path / "peer")}
    peer = open_peer(binding)
    deployment = RemoteComputeConfig(tmp_path / "ops.sqlite", tmp_path / "target.json", 4, 1,
                                     binding)
    config = replace(config, remote_compute=deployment)
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(config.to_dict()))
    assert ProjectConfig.load(config_path) == config
    operations = Operations(deployment.operations_path)
    provider = Provider(operations, peer, None, source_identity(ROOT))
    provider.target = replace(provider.target, gpu="none")
    app = Application(config, config_path=config_path, remote_provider=provider)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime = config.state_dir / "runtime.json"
    runtime.write_text(json.dumps({"instance_id": app.instance_id,
                                  "configuration_id": configuration_id(config),
                                  "port": server.server_port,
                                  "control_token": app.control_token}))

    def call(action, *, body=None, job_id=None, intent_id=None, wait_seconds=None):
        return training_command(config, action, body=body, operation_id=job_id,
                                execution="remote", intent_id=intent_id,
                                wait_seconds=wait_seconds)

    try:
        yield app, call, provider, store, owner, snapshot, config_path
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=10)


def settled(call, job_id, state):
    deadline = time.monotonic() + 60
    while True:
        value = call("status", job_id=job_id)
        assert value["scheduler_error"] is None, value
        row = value["jobs"][0]
        # Ops records running at claim; cancellation tests need the subsequently
        # persisted remote handle, not the earlier local claim observation.
        if row["status"] == state and (state != "running" or row["provider_ref"] is not None):
            return row
        assert time.monotonic() < deadline, value
        threading.Event().wait(0.05)


def test_normal_client_start_bounded_pause_same_run_resume_and_result(tmp_path, capsys):
    with client(tmp_path) as (app, call, provider, store, owner, snapshot, config_path):
        capabilities = call("capabilities")
        assert capabilities["live_pause_supported"] is False
        assert capabilities["gpu_qualification"] == "not_established"
        assert main(["capabilities", "--config", str(config_path),
                     "--execution", "remote"]) == 0
        assert json.loads(capsys.readouterr().out)["provider"] == "trusted_injected_engineering"
        provider.ready = True
        body = {"intent_id": "a" * 32, "source_id": snapshot.artifact_id,
                "config": MStdConfig(max_epochs=2).to_dict(), "max_seconds": 300,
                "stop_after": 1}
        first = call("start", body=body)["jobs"][0]
        # The initiating client is gone; the Application scheduler owns this task.
        paused = settled(call, first["id"], "paused")
        assert paused["result"]["checkpoint_id"]
        progress = call("progress", job_id=first["id"])
        assert progress["run_id"] == first["input_id"]
        assert progress["latest_progress"]["details"]["optimizer_updates"] == 1
        assert progress["latest_checkpoint"]["details"]["checkpoint_id"] == (
            paused["result"]["checkpoint_id"])
        assert progress["provider_queried"] is False
        cursor = progress["through_step"]
        unchanged = training_command(app.config, "progress", operation_id=first["id"],
                                     execution="remote", after_step=cursor)
        assert unchanged["events"] == []
        assert unchanged["through_step"] == cursor
        assert unchanged["latest_checkpoint"] == progress["latest_checkpoint"]
        assert main(["progress", "--config", str(config_path), "--execution", "remote",
                     "--operation-id", first["id"], "--after-step", str(cursor)]) == 0
        assert json.loads(capsys.readouterr().out)["events"] == []
        observation_config = replace(app.config, remote_compute=replace(
            app.config.remote_compute, observation_only=True))
        observation_path = config_path.with_name("observation.json")
        observation_path.write_text(json.dumps(observation_config.to_dict()))
        assert ProjectConfig.load(observation_path) == observation_config
        observer = RemoteTrainingService(observation_config,
                                        lambda: pytest.fail("observer selected a writer context"))
        observer.start_loop()
        assert observer._thread is None and observer._scheduler is None
        assert observer.progress(first["id"])["latest_progress"] == progress["latest_progress"]
        assert observer.capabilities()["supported_actions"] == ["status", "progress"]
        with observer._progress_lock, pytest.raises(
                BoundaryError, match="progress_read_in_progress"):
            observer.progress(first["id"])
        for action in ("prepare", "start", "pause", "resume", "cancel", "reconcile"):
            with pytest.raises(BoundaryError, match="observation_only_original_writer_required"):
                observer.command(action, {})
        assert len(provider.calls) == 1

        with pytest.raises(BoundaryError, match="live_pause_unsupported"):
            call("pause", body={"job_id": paused["id"],
                                "expected_attempt_id": paused["attempt_id"]})
        resume = {"job_id": paused["id"], "expected_attempt_id": paused["attempt_id"],
                  "intent_id": "b" * 32, "checkpoint_id": paused["result"]["checkpoint_id"],
                  "max_seconds": 300, "stop_after": None}
        with pytest.raises(BoundaryError, match="stale_remote_attempt"):
            call("resume", body={**resume, "expected_attempt_id": "0" * 32})
        second = call("resume", body=resume)["jobs"][0]
        completed = settled(call, second["id"], "completed")
        assert second["input_id"] == paused["input_id"]
        assert completed["result"]["output_id"] not in store.manifest_ids()
        assert call("reconcile", body={"job_id": second["id"],
                                      "expected_attempt_id": completed["attempt_id"]})[
                                          "jobs"][0]["result"] == completed["result"]
        workload = verify_p1_run(store, second["input_id"], provider.target.producer)
        _, model, checkpoint = verify_p1_result(store, completed["result"]["output_id"], workload)
        assert checkpoint.state["step"] == 2 and model.parent("run") == first["input_id"]
        from spireagent.workbench.local_evaluation import summary

        evaluation = store.get_manifest(completed["result"]["output_id"]).parent("evaluation")
        evaluation_summary = summary(store, evaluation)
        assert evaluation_summary["evaluation_schema"] == "stpd/p1-N-evaluation-1"
        assert evaluation_summary["best_dev_N_loss"] >= 0
        assert evaluation_summary["test_metrics"] == "sealed_not_exposed"
        assert "test" not in evaluation_summary and "rows" not in evaluation_summary

        assert len(provider.calls) == 2
        with owner.transaction() as db:
            assert db.execute("SELECT count(*) FROM curation_uses").fetchone() == (1,)
        with app.remote_training._operations.transaction() as db:
            assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'curation_%'")\
                .fetchall()
        assert not (owner.path.parent / "local-training-operation.json").exists()


def test_cancel_auth_and_unknown_never_resubmit(tmp_path):
    with client(tmp_path) as (app, call, provider, _store, _owner, snapshot, _path):
        body = {"intent_id": "c" * 32, "source_id": snapshot.artifact_id,
                "config": MStdConfig(max_epochs=1).to_dict(), "max_seconds": 300,
                "stop_after": None}
        started = call("start", body=body)["jobs"][0]
        running = settled(call, started["id"], "running")
        assert len(provider.calls) == 1
        ack = call("cancel", body={"job_id": running["id"],
                                   "expected_attempt_id": running["attempt_id"]})["jobs"][0]
        assert ack["status"] == "cancelling" and ack["result"] is None
        provider.ready = True
        cancelled = settled(call, running["id"], "cancelled")
        assert cancelled["result"]["state"] == "cancelled"
        assert "output_id" not in cancelled["result"]
        assert len(provider.cancels) == 1 and len(provider.calls) == 1
        provider.ambiguous = True
        unknown = call("start", body={**body, "intent_id": "d" * 32})["jobs"][0]
        failed = settled(call, unknown["id"], "submission_unknown")
        for _ in range(2):
            original = call("reconcile", body={"job_id": failed["id"],
                                               "expected_attempt_id": failed["attempt_id"]})
            assert original["jobs"][0]["status"] == "submission_unknown"
        assert len(provider.calls) == 2
        runtime = json.loads((app.config.state_dir / "runtime.json").read_bytes())
        with pytest.raises(HTTPError) as error:
            urlopen(Request(f"http://127.0.0.1:{runtime['port']}/api/remote-training/status"),
                    timeout=5)
        assert error.value.code == 401


def test_configured_hub_and_missing_local_purposes_are_not_bypassed(tmp_path):
    with client(tmp_path, reserve=False) as (app, call, provider, store, owner, snapshot, _path):
        service = RemoteTrainingService(replace(app.config, hub_url="https://hub.example"),
                                        lambda: (owner, store, tmp_path), provider=provider)
        with pytest.raises(BoundaryError, match="configured_hub_requires_hub_scheduler_route"):
            service.status()
        before = set(store.manifest_ids())
        with pytest.raises(BoundaryError, match="snapshot_purpose_reservation_required"):
            call("start", body={"intent_id": "e" * 32, "source_id": snapshot.artifact_id,
                                "config": MStdConfig(max_epochs=1).to_dict(),
                                "max_seconds": 300, "stop_after": None})
        assert set(store.manifest_ids()) == before and not provider.calls
        assert app.local_training.capabilities()["automatic_retry"] is False


def test_normal_prepare_verifies_separate_peer_without_enqueue_or_submit(tmp_path, monkeypatch):
    with client(tmp_path) as (app, call, provider, local, owner, snapshot, _path):
        body = {"intent_id": "f" * 32, "source_id": snapshot.artifact_id,
                "config": MStdConfig(max_epochs=1).to_dict(), "max_seconds": 300,
                "stop_after": None}
        from spireagent.workbench import training_cli

        waits = []
        original_request = training_cli._local_request

        def observe(*args, **kwargs):
            waits.append(kwargs["timeout"])
            return original_request(*args, **kwargs)

        monkeypatch.setattr(training_cli, "_local_request", observe)
        result = call("prepare", body=body, wait_seconds=600)
        assert waits == [600]
        assert result["state"] == "prepared" and not result["enqueued"]
        assert not result["provider_submitted"] and not provider.calls
        assert result["input"]["unique_N"] == 3
        assert result["input"]["catalog_choices"] == 6
        assert result["input"]["closure_payload_readback"] == "verified"
        assert app.remote_training._operations is None
        assert app.remote_training._thread is None
        assert provider.ops.jobs() == []
        peer = open_peer(app.config.remote_compute.compute_store)
        assert peer.blobs.root != local.blobs.root
        assert peer.get_manifest(result["run_id"]) == local.get_manifest(result["run_id"])
        for identity in peer.manifest_ids():
            manifest = peer.get_manifest(identity)
            for payload in manifest.payloads:
                assert b"".join(peer.read_payload(payload)) == b"".join(local.read_payload(payload))
        with owner.transaction() as db:
            assert db.execute("SELECT count(*) FROM curation_uses").fetchone() == (1,)
        normal_provider = RemoteTrainingService(app.config, app.local_datasets._selected)
        assert normal_provider.capabilities()["available"] is False
        with pytest.raises(BoundaryError, match="shared_compute_store_required"):
            normal_provider.command("prepare", body)

        def unavailable_peer():
            raise AssertionError("intent facts must not query peer/provider")

        monkeypatch.setattr(app.remote_training, "_context", unavailable_peer)
        observed = call("status", intent_id=body["intent_id"])
        assert observed["local_runs"][0]["run_id"] == result["run_id"]
        assert observed["jobs"] == []
        assert observed["peer_queried"] is observed["provider_queried"] is False
        assert observed["transfer_completion"] == "not_inferred_from_local_run"
        assert not provider.calls and provider.ops.jobs() == []


def test_normal_status_reads_durable_job_while_original_scheduler_poll_blocks(
    tmp_path, monkeypatch,
):
    with client(tmp_path) as (app, call, provider, _store, _owner, snapshot, _path):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original_poll = provider.poll

        def blocked_poll(handle):
            entered.set()
            assert release.wait(5), "test did not release synthetic provider poll"
            return original_poll(handle)

        monkeypatch.setattr(provider, "poll", blocked_poll)
        first = call("start", body={"intent_id": "1" * 32, "source_id": snapshot.artifact_id,
            "config": MStdConfig(max_epochs=1).to_dict(), "max_seconds": 300,
            "stop_after": None})["jobs"][0]
        assert entered.wait(5)
        row = provider.ops.jobs()[0]
        assert row["status"] == "running" and row["provider_ref"]
        observed, errors = [], []

        def read():
            try:
                observed.append(call("status", job_id=first["id"]))
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            prompt = finished.wait(1)
        finally:
            release.set()
            reader.join(timeout=5)
        assert prompt, "normal status waited for the Scheduler provider/network lock"
        assert not errors
        assert observed[0]["schema"] == "spireagent/remote-training-status-1"
        assert observed[0]["automatic_retry"] is False
        expected = {key: row[key] for key in (
            "id", "kind", "input_id", "status", "attempt_id", "fence", "max_seconds",
            "reserved_units", "provider_ref", "result")}
        expected["result"] = json.loads(expected["result"]) if expected["result"] else None
        compute = provider.ops.compute_state(row["id"])
        expected.update(compute_phase=compute["phase"], compute_error=compute["last_error"])
        assert expected["compute_phase"] == "submitted" and expected["compute_error"] is None
        assert observed[0]["jobs"] == [expected]
        assert len(provider.calls) == 1


def test_status_observes_no_jobs_without_initializing_or_querying_remote(tmp_path, monkeypatch):
    with client(tmp_path) as (app, call, provider, _store, _owner, _snapshot, _path):
        def no_remote_context():
            raise AssertionError("status must not query provider/Store or admit new work")

        monkeypatch.setattr(app.remote_training, "_context", no_remote_context)
        assert call("status") == {
            "schema": "spireagent/remote-training-status-1", "jobs": [],
            "scheduler_error": None, "automatic_retry": False}
        assert app.remote_training._scheduler is None
        assert app.remote_training._operations is None
        assert app.remote_training._thread is None and not provider.calls
        with pytest.raises(BoundaryError, match="job_not_found"):
            call("status", job_id="0" * 32)
        deployment = replace(app.config.remote_compute, operations_path=tmp_path / "absent.sqlite")
        service = RemoteTrainingService(replace(app.config, remote_compute=deployment),
                                        app.local_datasets._selected, provider=provider)
        assert service.status()["jobs"] == []
        assert not deployment.operations_path.exists()
        with pytest.raises(BoundaryError, match="job_not_found"):
            service.status("0" * 32)
