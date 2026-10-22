"""SSH is faked; real supervisor locks, files and worker process groups are exercised.

The numerical worker is a bounded receipt-producing process fixture. Its original
dispatch/Store checks are covered by test_cloud_jobs, not claimed by these tests.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER

from spireagent.compute import ComputeSubmissionUnknown, ComputeTerminated
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.ssh_compute import (
    CUBLAS_WORKSPACE,
    SshCall,
    SshProvider,
    SshTarget,
    provider_from_target,
)
from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest


def target(tmp_path: Path, **changes) -> SshTarget:
    remote_root = Path("/fixture") if os.name == "nt" else tmp_path
    value = SshTarget(PRODUCER, str(tmp_path / "ssh_config"), "uq-fixture", "/fixed/checkout",
                      "/fixture/python" if os.name == "nt" else sys.executable,
                      (remote_root / "private.env").as_posix(), (remote_root / "jobs").as_posix(),
                      30, "none", FrozenObject.of({"python": "3.11.9", "torch": "2.7.0",
                                                   "cuda": "none", "gpu_name": "none",
                                                   "gpu_uuid": "none", "driver": "none"}))
    return replace(value, **changes)


def request(attempt="attempt-1") -> ComputeRequest:
    return ComputeRequest("training", "a" * 64, PRODUCER, attempt, stop_after=1)


def test_target_factory_identity_and_closed_configuration(tmp_path):
    selected = target(tmp_path)
    assert SshTarget.decode(selected.to_dict()) == selected
    assert isinstance(provider_from_target(selected.to_dict()), SshProvider)
    assert replace(selected, timeout_seconds=15).target_id != selected.target_id
    for changes in ({"host": "-oProxyCommand=bad"}, {"host": "user@host"},
                    {"private_env": "/fixed/checkout/secret.env"},
                    {"timeout_seconds": True}, {"gpu": "L4"},
                    {"interpreter": "python"}):
        with pytest.raises(BoundaryError):
            replace(selected, **changes)
    foreign = {**selected.to_dict(), "command": "anything"}
    with pytest.raises(BoundaryError):
        SshTarget.decode(foreign)
    handle = SshCall(selected.target_id, request(), request().request_id)
    with pytest.raises(BoundaryError, match="original_request_handle"):
        replace(handle, call_id="b" * 64)
    with pytest.raises(BoundaryError, match="foreign_target"):
        SshProvider(selected).restore_handle(replace(handle, target_id="c" * 64).to_dict())


@pytest.fixture
def transport(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("The remote school supervisor requires POSIX; provider codec is portable")
    import spireagent.ssh_compute_agent as agent

    selected = target(tmp_path)
    secret = Path(selected.private_env)
    secret.write_text("AWS_ACCESS_KEY_ID=never-request-or-receipt\nSTPD_S3_PREFIX=0.3\n")
    secret.chmod(0o600)
    threads, commands, children = [], [], []
    original_popen, original_run = subprocess.Popen, subprocess.run
    monkeypatch.setattr(agent, "verify_target", lambda value: None)
    worker = """
import json, os, sys, time
from spireagent.json_boundary import decode_json, json_bytes
from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest
request = ComputeRequest.decode(decode_json(open(sys.argv[1], 'rb').read()))
assert os.environ['AWS_ACCESS_KEY_ID'] == 'never-request-or-receipt'
time.sleep(float(os.environ['STPD_S3_PREFIX']))
receipt = ComputeReceipt(request.request_id, request.attempt_id, request.kind,
                         request.input_id, request.producer, 'paused', None, 'b' * 64)
sys.stdout.buffer.write(json_bytes(receipt.to_dict()))
"""

    def popen(argv, **kwargs):
        commands.append(list(argv))
        if "--supervise" in argv:
            directory = Path(argv[-1])
            thread = threading.Thread(target=agent.supervise, args=(directory,))
            thread.start()
            threads.append(thread)
            return SimpleNamespace(pid=0)
        assert argv[1:3] == ["-m", "stpd.cloud_jobs"]
        invocation = json.loads((Path(argv[4]).parent / "invocation.json").read_bytes())
        assert argv[-4:] == ["--store", "s3", "--wall-seconds",
                             str(invocation["target"]["timeout_seconds"])]
        kwargs["cwd"] = Path(__file__).parents[1]
        child = original_popen([sys.executable, "-c", worker, argv[4]], **kwargs)
        children.append(child)
        return child

    def run(argv, **kwargs):
        if argv[0] != "ssh":
            return original_run(argv, **kwargs)
        commands.append(list(argv))
        # Transport fake preserves the actual production command envelope/decoder.
        value = agent.exchange(json.loads(kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode())

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", run)
    yield selected, commands, children, agent, original_popen
    for child in children:
        if child.poll() is None:
            agent.terminate_group(child)
    for thread in threads:
        thread.join(timeout=8)
        assert not thread.is_alive()


def eventually(provider, handle, *, error=None):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            receipt = provider.poll(handle)
            if receipt is not None:
                assert error is None
                return receipt
        except BoundaryError as failure:
            if failure.code == error:
                return failure
            assert failure.code in {"result_unavailable", "transport_unavailable"}
        except ComputeTerminated as terminal:
            assert "execution_" + terminal.termination.outcome == error
            return terminal.termination
        threading.Event().wait(0.03)
    pytest.fail("original invocation did not settle")


def test_async_submit_observation_disconnect_restore_same_handle_and_original_receipt(
    transport, monkeypatch,
):
    selected, commands, _, _, _ = transport
    provider = SshProvider(selected)
    handle = provider.submit(request())
    saved = handle.to_dict()
    assert handle.call_id == request().request_id
    # Lose one observation after the original process has started; never submit again.
    exchange = provider._exchange
    monkeypatch.setattr(provider, "_exchange", lambda *_: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("ssh", 45)))
    with pytest.raises(BoundaryError, match="result_unavailable"):
        provider.poll(handle)
    observer = SshProvider(selected)
    restored = observer.restore_handle(saved)
    receipt = eventually(observer, restored)
    assert isinstance(receipt, ComputeReceipt) and receipt.state == "paused"
    assert restored.to_dict() == saved
    assert observer.poll(restored) == receipt
    assert len([argv for argv in commands if "--supervise" in argv]) == 1
    invocation = Path(selected.state_directory) / selected.target_id / handle.call_id
    assert "never-request-or-receipt" not in (invocation / "invocation.json").read_text()
    assert "never-request-or-receipt" not in (invocation / "result.json").read_text()
    assert not list(invocation.glob("*.tmp-*"))
    monkeypatch.setattr(provider, "_exchange", exchange)
    with pytest.raises(ComputeSubmissionUnknown, match="submission_unknown"):
        provider.submit(request())
    assert len([argv for argv in commands if "--supervise" in argv]) == 1


def test_original_supervisor_waits_for_observers_short_flock_without_resubmission(
    transport, monkeypatch,
):
    import fcntl

    selected, commands, children, _, _ = transport
    real_flock, launch = fcntl.flock, subprocess.Popen
    attempted = threading.Event()
    failures, observer_locks = [], []

    def observed_flock(stream, operation):
        supervisor = (threading.current_thread() is not threading.main_thread()
                      and Path(stream.name).name == "running.lock")
        if supervisor and operation & fcntl.LOCK_NB:
            # Record the actual kernel rejection before notifying the observer;
            # the old startup race therefore fails deterministically, not by timing.
            try:
                return real_flock(stream, operation)
            except BlockingIOError as error:
                failures.append(error)
                raise
            finally:
                attempted.set()
        if supervisor:
            attempted.set()
        return real_flock(stream, operation)

    def observer_first(argv, **kwargs):
        if "--supervise" in argv:
            stream = (Path(argv[-1]) / "running.lock").open("a+b")
            real_flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observer_locks.append(stream)
        return launch(argv, **kwargs)

    monkeypatch.setattr(fcntl, "flock", observed_flock)
    monkeypatch.setattr(subprocess, "Popen", observer_first)
    provider = SshProvider(selected)
    try:
        handle = provider.submit(request("observer-first"))
        saved = handle.to_dict()
        assert attempted.wait(timeout=3), "original supervisor never attempted its real flock"
        assert not failures, "short observer lock incorrectly killed the original supervisor"
        assert not children  # The original owner is waiting, not starting another process.
    finally:
        for stream in observer_locks:
            stream.close()
    restored = provider.restore_handle(saved)
    receipt = eventually(provider, restored)
    assert receipt.request_id == handle.request.request_id
    assert restored.to_dict() == saved
    assert len([argv for argv in commands if "--supervise" in argv]) == 1
    assert len([argv for argv in commands if argv[1:3] == ["-m", "stpd.cloud_jobs"]]) == 1


def test_lost_submit_ack_observes_deterministic_original_without_resubmission(
    transport, monkeypatch,
):
    selected, commands, _, _, _ = transport
    provider = SshProvider(selected)
    exchange = provider._exchange

    def lose_ack(action, handle):
        exchange(action, handle)
        raise subprocess.TimeoutExpired("ssh", 45)

    monkeypatch.setattr(provider, "_exchange", lose_ack)
    with pytest.raises(ComputeSubmissionUnknown, match="submission_unknown"):
        provider.submit(request("lost-ack"))
    original_request = request("lost-ack")  # Original durable submitting request, unchanged.
    observer = SshProvider(selected)
    saved = SshCall(selected.target_id, original_request, original_request.request_id).to_dict()
    receipt = eventually(observer, observer.restore_handle(saved))
    assert receipt.attempt_id == "lost-ack"
    assert len([argv for argv in commands if "--supervise" in argv]) == 1


def test_running_lock_is_fresh_original_process_fact_and_missing_original_is_unknown(transport):
    selected, _, children, _, _ = transport
    Path(selected.private_env).write_text(
        "AWS_ACCESS_KEY_ID=never-request-or-receipt\nSTPD_S3_PREFIX=60\n")
    provider = SshProvider(selected)
    handle = provider.submit(request("running"))
    deadline = time.monotonic() + 5
    while not children and time.monotonic() < deadline:
        threading.Event().wait(0.02)
    assert children
    assert provider.poll(handle) is None
    provider.cancel(handle)
    eventually(provider, handle, error="execution_cancelled")
    absent = request("absent")
    with pytest.raises((BoundaryError, OSError)):
        provider.poll(SshCall(selected.target_id, absent, absent.request_id))


def test_unconfirmed_group_exit_never_emits_terminal_admission(transport, monkeypatch):
    selected, _, _, agent, _ = transport
    stop = agent.terminate_group

    def unconfirmed(child):
        stop(child)
        return False  # Simulate inability to verify the whole original group.

    monkeypatch.setattr(agent, "terminate_group", unconfirmed)
    provider = SshProvider(selected)
    handle = provider.submit(request("unconfirmed"))
    directory = Path(selected.state_directory) / selected.target_id / handle.call_id
    deadline = time.monotonic() + 5
    while not (directory / "result.json").exists() and time.monotonic() < deadline:
        threading.Event().wait(0.03)
    terminal = json.loads((directory / "result.json").read_bytes())
    assert terminal["state"] == "unknown" and terminal["termination"] is None
    with pytest.raises(BoundaryError, match="result_unavailable"):
        provider.poll(handle)


def test_actual_ssh_handle_binds_same_operations_and_cancels_through_scheduler(transport, tmp_path):
    from test_cloud_jobs import job_fixture

    from spireagent.hub.database import Operations
    from spireagent.hub.scheduler import Scheduler

    selected, _, _, _, _ = transport
    Path(selected.private_env).write_text(
        "AWS_ACCESS_KEY_ID=never-request-or-receipt\nSTPD_S3_PREFIX=60\n")
    store, _, _, job, _ = job_fixture(tmp_path / "store")
    operations = Operations(tmp_path / "ops.sqlite")
    identity = operations.enqueue("feature", job.artifact_id, "ssh-stop", max_seconds=30,
                                  reserved_units=1, budget_limit=2)
    provider = SshProvider(selected)
    with Scheduler(operations, store, provider, budget_limit=2) as scheduler:
        assert scheduler.tick(1)["state"] == "submitted"
        saved = operations.compute_state(identity)["handle"]
        assert saved["schema"] == "spireagent/ssh-call-1"
        operations.cancel(identity)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = scheduler.tick(2)["state"]
            if state == "cancelled":
                break
            assert state in {"cancelling", "result_uncertain"}
            threading.Event().wait(0.03)
        assert operations.jobs()[0]["status"] == "cancelled"
        assert operations.compute_state(identity)["handle"] == saved
        assert operations.jobs()[0]["reserved_units"] == 1


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_stop_terminates_only_original_process_and_never_fabricates_paused(transport, stop):
    selected, _, children, _, original_popen = transport
    if stop == "timeout":
        selected = replace(selected, timeout_seconds=1)
    Path(selected.private_env).write_text(
        "AWS_ACCESS_KEY_ID=never-request-or-receipt\nSTPD_S3_PREFIX=60\n")
    provider = SshProvider(selected)
    unrelated = original_popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        handle = provider.submit(request(stop))
        deadline = time.monotonic() + 5
        while not children and time.monotonic() < deadline:
            threading.Event().wait(0.02)
        assert children and children[0].poll() is None
        if stop == "cancel":
            provider.cancel(handle)
        failure = eventually(provider, handle, error="execution_" +
                             ("cancelled" if stop == "cancel" else "timeout"))
        assert failure.process_exited is True
        assert children[0].poll() is not None
        assert unrelated.poll() is None
        path = Path(selected.state_directory) / selected.target_id / handle.call_id / "result.json"
        terminal = json.loads(path.read_bytes())
        assert terminal["state"] == "failed" and terminal["receipt"] is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_ssh_options_bounded_unknown_and_no_raw_transport_error(tmp_path, monkeypatch):
    observed = []

    def fail(argv, **kwargs):
        observed.append((argv, kwargs))
        raise subprocess.TimeoutExpired("must-not-echo-private-error", 45)

    monkeypatch.setattr(subprocess, "run", fail)
    provider = SshProvider(target(tmp_path))
    with pytest.raises(ComputeSubmissionUnknown, match="submission_unknown") as failure:
        provider.submit(request())
    assert "must-not-echo" not in str(failure.value)
    assert len(observed) == 1
    argv, kwargs = observed[0]
    assert argv[:3] == ["ssh", "-F", str(tmp_path / "ssh_config")]
    assert "StrictHostKeyChecking=yes" in argv and "BatchMode=yes" in argv
    assert "ConnectionAttempts=1" in argv and kwargs["timeout"] == 45
    assert argv[-2] == "uq-fixture"
    assert "spireagent.ssh_compute_agent" in argv[-1]
    launch = shlex.split(argv[-1])
    assert launch[3:] == ["/usr/bin/env", "CUBLAS_WORKSPACE_CONFIG=:4096:8",
                          provider.target.interpreter, "-m", "spireagent.ssh_compute_agent"]


def test_fixed_ssh_launch_environment_reaches_supervisor_and_original_worker_environment(
    tmp_path, monkeypatch,
):
    if os.name == "nt":
        pytest.skip("The school launch environment is POSIX")
    selected = target(tmp_path)
    private = Path(selected.private_env)
    private.write_text("AWS_ACCESS_KEY_ID=fixture-storage\nCUBLAS_WORKSPACE_CONFIG=:16:8\n")
    private.chmod(0o600)
    monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG", "wrong-parent-value")
    original_run = subprocess.run
    supervisor_fixture = """
import json, os, subprocess, sys
assert os.environ['CUBLAS_WORKSPACE_CONFIG'] == ':4096:8'
from spireagent.ssh_compute_agent import private_environment
value = json.load(sys.stdin)
environment = private_environment(__import__('pathlib').Path(value['target']['private_env']))
subprocess.run([sys.executable, '-c',
    "import os; assert os.environ['CUBLAS_WORKSPACE_CONFIG'] == ':4096:8'; "
    "assert os.environ['AWS_ACCESS_KEY_ID'] == 'fixture-storage'"],
    env=environment, check=True, stdout=subprocess.DEVNULL)
print(json.dumps({'target_id': value['handle']['target_id'],
                  'call_id': value['handle']['call_id'], 'state': 'running',
                  'receipt': None, 'error': None, 'termination': None}))
"""

    def local_ssh(argv, **kwargs):
        launch = shlex.split(argv[-1])[3:]
        assert launch[-2:] == ["-m", "spireagent.ssh_compute_agent"]
        # SSH is faked; execute the actual fixed env+interpreter prefix locally.
        # Only the remote endpoint is replaced by a supervisor process fixture.
        return original_run([*launch[:-2], "-c", supervisor_fixture], **kwargs)

    monkeypatch.setattr(subprocess, "run", local_ssh)
    provider = SshProvider(selected)
    original = request("fixed-environment")
    handle = SshCall(selected.target_id, original, original.request_id)
    assert provider.poll(handle) is None


@pytest.mark.parametrize("workspace", [None, "incorrect", ":16:8", ":4096:8"])
def test_gpu_target_requires_fixed_workspace_before_runtime_probe(tmp_path, monkeypatch, workspace):
    if os.name == "nt":
        pytest.skip("The school target uses POSIX checkout/interpreter paths")
    import spireagent.ssh_compute_agent as agent

    selected = target(tmp_path, checkout=str(Path(__file__).parents[2]), gpu="L40S",
                      runtime=FrozenObject.of({"python": "3.11.9", "torch": "2.7.0",
                                               "cuda": "12.8", "gpu_name": "NVIDIA L40S",
                                               "gpu_uuid": "GPU-fixture", "driver": "595"}))
    monkeypatch.setattr(agent, "source_identity", lambda root: PRODUCER)
    probes = []

    def runtime(gpu):
        probes.append(gpu)
        return selected.runtime.value()

    monkeypatch.setattr(agent, "runtime_identity", runtime)
    if workspace is None:
        monkeypatch.delenv("CUBLAS_WORKSPACE_CONFIG", raising=False)
    else:
        monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG", workspace)
    if workspace == CUBLAS_WORKSPACE:
        agent.verify_target(selected)
        assert probes == ["L40S"]
    else:
        with pytest.raises(BoundaryError, match="fixed_cublas_workspace_required"):
            agent.verify_target(selected)
        assert probes == []


def test_remote_identity_and_private_environment_reject_before_execution(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Remote POSIX environment checks")
    import spireagent.ssh_compute_agent as agent

    selected = target(tmp_path, checkout=str(Path(__file__).parents[2]),
                      interpreter=sys.executable)
    monkeypatch.setattr(agent, "source_identity", lambda root: PRODUCER)
    monkeypatch.setattr(agent, "runtime_identity", lambda gpu: selected.runtime.value())
    agent.verify_target(selected)
    monkeypatch.setattr(agent, "source_identity", lambda root: replace(
        PRODUCER, source_revision="d" * 40))
    with pytest.raises(BoundaryError, match="runtime_source_lock_mismatch"):
        agent.verify_target(selected)
    monkeypatch.setattr(agent, "source_identity", lambda root: PRODUCER)
    monkeypatch.setattr(agent, "runtime_identity", lambda gpu: {
        **selected.runtime.value(), "torch": "different"})
    with pytest.raises(BoundaryError, match="runtime_execution_identity_mismatch"):
        agent.verify_target(selected)
    private = tmp_path / "private.env"
    private.write_text("AWS_ACCESS_KEY_ID=private\n")
    private.chmod(0o644)
    with pytest.raises(BoundaryError, match="cloud_environment_private_file_required"):
        agent.private_environment(private)
    private.chmod(0o600)
    assert agent.private_environment(private)["AWS_ACCESS_KEY_ID"] == "private"
    for key in ("PYTHONPATH", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES"):
        monkeypatch.setenv(key, "original-runtime")
    private.write_text("AWS_ACCESS_KEY_ID=private\nPYTHONPATH=changed\n"
                       "LD_LIBRARY_PATH=changed\nCUDA_VISIBLE_DEVICES=changed\n")
    environment = agent.private_environment(private)
    assert all(environment[key] == "original-runtime" for key in
               ("PYTHONPATH", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES"))


def test_bad_original_response_and_receipt_binding_are_rejected(tmp_path, monkeypatch):
    selected = target(tmp_path)
    provider = SshProvider(selected)
    original = request()
    handle = SshCall(selected.target_id, original, original.request_id)
    foreign = request("foreign")
    receipt = ComputeReceipt(foreign.request_id, foreign.attempt_id, foreign.kind,
                             foreign.input_id, foreign.producer, "paused", None, "b" * 64)
    monkeypatch.setattr(provider, "_exchange", lambda *_: {
        "state": "completed", "error": None, "receipt": receipt.to_dict()})
    with pytest.raises(BoundaryError, match="request_binding_mismatch"):
        provider.poll(handle)


def test_normal_application_configuration_selects_ssh_without_provider_injection(
    tmp_path, monkeypatch,
):
    from spireagent.workbench.developer import ProjectConfig, RemoteComputeConfig
    from spireagent.workbench.remote_training import RemoteTrainingService

    selected = target(tmp_path)
    target_path = tmp_path / "target.json"
    target_path.write_text(json.dumps(selected.to_dict()))
    peer = {"kind": "s3", "bucket": "fixture", "endpoint": None,
            "prefix": "fixture", "region": "us-east-1"}
    deployment = RemoteComputeConfig(tmp_path / "ops.sqlite", target_path, 4, 1, peer)
    config = ProjectConfig(tmp_path / "state", "", "", None, "fixture", remote_compute=deployment)
    import spireagent.workbench.remote_training as remote

    monkeypatch.setattr(remote, "source_identity", lambda root: PRODUCER)
    monkeypatch.setattr(remote, "open_peer", lambda binding: "same-compute-peer")
    owner = SimpleNamespace(path=tmp_path / "curation.sqlite")
    service = RemoteTrainingService(config, lambda: (owner, "same-local-store", None))
    assert service.capabilities()["provider"] == "ssh"
    assert service.capabilities()["available"] is True
    assert service._context()[:2] == (owner, "same-local-store")
    assert isinstance(service._context()[2], SshProvider)
    assert service._context()[3] == "same-compute-peer"
