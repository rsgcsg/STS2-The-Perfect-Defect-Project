"""Actual shared application boundary using immutable synthetic fixtures only."""

from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_local_training import _ready
from test_structured_s0 import PRODUCER, sample, source

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.inplace_curation import configured_owner
from spireagent.workbench.local_training import OPERATION_FILE, LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.recipes import structured as adapter_module
from spireagent.workbench.trusted_recipes import STRUCTURED_RECIPE


def ready(tmp_path, monkeypatch, *, source_kind="synthetic", qualification="synthetic_fixture",
          split="train"):
    config, _legacy, _, store = _ready(tmp_path, monkeypatch)
    value = source([sample(20), sample(21, hp=11), sample(22, hp=10)],
                   source_kind=source_kind, split=split)
    raw = json_bytes(value)
    payload = store.put_payload("source", io.BytesIO(raw), "application/json")
    manifest = Manifest("dataset", PRODUCER, payloads=(payload,), parameters=FrozenObject.of({
        "schema": value["schema"], "source_kind": source_kind,
        "source_sha256": payload.sha256, "qualification": qualification,
    }))
    store.publish(manifest)
    owner = configured_owner(config)
    runs = {run["run_id"] for run in value["runs"]}
    # Synthetic test fixtures prepare existing ledger authority explicitly; the
    # application never creates these records from caller-supplied source JSON.
    with owner.transaction() as db:
        db.execute("INSERT INTO curation_sources VALUES(?,?,1)", (manifest.artifact_id, "fixture"))
        db.execute("INSERT INTO curation_exact_source_index VALUES(?)", (manifest.artifact_id,))
        db.executemany("INSERT INTO curation_source_runs VALUES(?,?)",
                       [(manifest.artifact_id, run) for run in runs])
        db.execute("INSERT INTO curation_claims VALUES(?,?,?,?)",
                   ("fixture", "training", manifest.artifact_id, 1))
        db.executemany("INSERT INTO curation_claim_runs VALUES(?,?)",
                       [("fixture", run) for run in runs])
    request = TrainingRequest("1" * 32, STRUCTURED_RECIPE, manifest.artifact_id,
                              {"epochs": 1}, limits={"wall_seconds": 600})
    service = LocalTrainingService(config)
    return service, request, owner, store


def settle(service):
    assert service._thread is not None
    service._thread.join(timeout=30)
    assert not service._thread.is_alive()
    return service.status()["operation"]


class CheckpointWorkerObservation:
    """Observe the fixture worker separately from a possible Windows launcher."""

    def __init__(self, owner, store, processes, phases):
        self.owner, self.store, self.processes, self.phases = owner, store, processes, phases
        self.binding = None
        self.handle = None
        self.winapi = None
        phases["worker_process"] = {"binding": None, "observations": [], "cleanup": []}

    def bind_before_cancel(self, value):
        if self.binding is not None:
            assert (value["operation_id"], value["attempt_id"]) == (
                self.binding["operation_id"], self.binding["attempt_id"])
            return
        trace = self.owner.path.parent / ("fixture-checkpoint-" + value["attempt_id"] + ".jsonl")
        deadline = time.monotonic() + 5
        # The machine stdout event may arrive before its child trace write closes.
        while True:
            raw = trace.read_bytes() if trace.exists() else b""
            rows = [json.loads(line) for line in raw.splitlines()] if raw.endswith(b"\n") else []
            checkpoints = [row for row in rows if row["phase"] == "checkpoint_emitted"]
            if checkpoints:
                break
            assert time.monotonic() < deadline, "fixture worker trace did not close before cancel"
            threading.Event().wait(0.005)
        assert len(checkpoints) == 1 and len(self.processes) == 1
        row, launcher = checkpoints[0], self.processes[0]
        event = self.store.get_manifest(value["details"]["event_id"])
        checkpoint = self.store.get_manifest(row["checkpoint_id"])
        assert (row["operation_id"], row["attempt_id"], row["event_id"]) == (
            value["operation_id"], value["attempt_id"], value["details"]["event_id"])
        assert row["run_id"] == event.parent("run") == checkpoint.parent("run")
        assert checkpoint.parameters.value()["attempt"] == row["attempt_id"]
        assert type(row["pid"]) is int and row["pid"] > 0
        assert type(row["ppid"]) is int and row["ppid"] > 0
        direct = row["pid"] == launcher.pid
        assert direct or os.name == "nt" and row["ppid"] == launcher.pid, rows
        assert launcher.poll() is None, "fixture launcher exited before cancellation"
        executable = Path(launcher.args[0])
        self.binding = {
            **row, "launcher_pid": launcher.pid,
            "relation": "direct" if direct else "windows_launcher_parent",
            "launcher_executable": str(executable),
            "launcher_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "observed_before_cancel_at": time.monotonic(),
        }
        configuration = executable.parent.parent / "pyvenv.cfg"
        if configuration.is_file():
            self.binding["pyvenv_cfg_sha256"] = hashlib.sha256(
                configuration.read_bytes()).hexdigest()
        self.phases["worker_process"]["binding"] = self.binding
        if os.name == "nt":
            # CPython's native C binding preserves HANDLE width on Win64. Do not
            # use ctypes' default c_int return ABI or reopen a PID after the kill.
            import _winapi

            self.winapi = _winapi
            self.handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE | 0x1000 | 0x0001,
                                              False, row["pid"])
            # Rights: SYNCHRONIZE, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE.
            self.phases["worker_process"]["native_handle"] = {
                "api": "_winapi", "pointer_bits": 64 if sys.maxsize > 2**32 else 32,
                "opened_before_cancel_at": time.monotonic(), "inherited": False,
            }
        before = self.observe("before_cancel")
        assert before["terminated"] is False, before

    def observe(self, phase):
        result = {"phase": phase, "observed_at": time.monotonic(),
                  "bound": self.binding is not None, "terminated": None}
        self.phases["worker_process"]["observations"].append(result)
        if self.binding is not None:
            result.update(worker_pid=self.binding["pid"],
                          launcher_pid=self.binding["launcher_pid"],
                          operation_id=self.binding["operation_id"],
                          attempt_id=self.binding["attempt_id"])
            if self.winapi is not None and self.handle is not None:
                result["method"] = "preheld_native_worker_handle"
                try:
                    result["wait_result"] = self.winapi.WaitForSingleObject(self.handle, 0)
                    result["exit_code"] = self.winapi.GetExitCodeProcess(self.handle)
                except OSError as error:
                    result["error"] = {"type": type(error).__qualname__,
                                       "winerror": getattr(error, "winerror", None),
                                       "errno": error.errno}
                    raise
                assert result["wait_result"] in {
                    self.winapi.WAIT_OBJECT_0, self.winapi.WAIT_TIMEOUT}, result
                result["terminated"] = result["wait_result"] == self.winapi.WAIT_OBJECT_0
            elif os.name != "nt":
                result["method"] = "direct_owned_popen"
                result["exit_code"] = self.processes[0].poll()
                result["terminated"] = result["exit_code"] is not None
        return result

    def cleanup(self):
        # These are cleanup observations, never the preceding SUT stop evidence.
        record = {"phase": "fixture_cleanup", "observed_at": time.monotonic(),
                  "terminated_by_fixture": False}
        self.phases["worker_process"]["cleanup"].append(record)
        try:
            if self.winapi is not None and self.handle is not None:
                wait = self.winapi.WaitForSingleObject(self.handle, 0)
                record["initial_wait_result"] = wait
                if wait == self.winapi.WAIT_TIMEOUT:
                    self.winapi.TerminateProcess(self.handle, 1)
                    record["terminated_by_fixture"] = True
                record["final_wait_result"] = self.winapi.WaitForSingleObject(self.handle, 5000)
                record["exit_code"] = self.winapi.GetExitCodeProcess(self.handle)
                assert record["final_wait_result"] == self.winapi.WAIT_OBJECT_0, record
        finally:
            try:
                if self.winapi is not None and self.handle is not None:
                    self.winapi.CloseHandle(self.handle)
                    self.handle = None
                    record["native_handle_closed"] = True
            finally:
                for process in self.processes:
                    if process.poll() is None:
                        process.kill()
                        record["terminated_by_fixture"] = True
                    code = process.wait(timeout=5)
                    record.setdefault("launcher_exits", []).append({"pid": process.pid,
                                                                    "exit_code": code})


def fixture_failure_diagnostic(service, owner, phases, observer, error):
    with suppress(Exception):
        observer.observe("failure_before_fixture_cleanup")
    phases["fixture_failure"] = {"type": type(error).__qualname__, "message": str(error)[:512]}
    operation = service.status()["operation"]
    return cancellation_diagnostic(
        service, owner, operation, phases, suffix="-failure",
        child_attempt=observer.binding["attempt_id"] if observer.binding else None)


def fixture_cleanup_diagnostic(service, owner, phases, observer, pytest_request):
    try:
        observer.cleanup()
    finally:
        if service._thread is not None:
            service._thread.join(timeout=5)
        operation = service.status()["operation"]
        diagnostic = cancellation_diagnostic(
            service, owner, operation, phases, suffix="-cleanup",
            child_attempt=observer.binding["attempt_id"] if observer.binding else None)
        # JUnit properties retain the complete bounded observation on both a
        # passing case and an early failure; a temporary pathname alone cannot.
        pytest_request.node.user_properties.append(("fixture_process_observation", diagnostic))


def test_capabilities_are_metadata_only_without_torch_import(tmp_path):
    script = """
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'torch' or name.startswith('torch.'):
        raise AssertionError('tensor import during discovery')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.developer import ProjectConfig, combination
from pathlib import Path
service = LocalTrainingService(ProjectConfig(Path(sys.argv[1]), '', '', None, combination()))
value = service.capabilities()
assert any(item['recipe_id'] == 'structured-m2-cpu-v2' for item in value['recipes'])
for item in value['recipes']:
    if item['limits']:
        assert item['placement_ids'] == ['local-cpu']
        if item['recipe_id'] == 'reward-m0-legacycompact-deck-pair-v1':
            assert item['wall_budget_amendment'] is False
            assert 'amend_wall_budget' not in item['supported_actions']
        else:
            assert 'amend_wall_budget' in item['supported_actions']
    else:
        assert 'amend_wall_budget' not in item['supported_actions']
assert 'torch' not in sys.modules
assert not Path(sys.argv[1]).exists()
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path / "unused")],
                            env=dict(os.environ), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_actual_structured_service_training_result_use_and_idempotency(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    before = set(store.manifest_ids())
    started = service.start(request)["operation"]
    assert started["status"] == "pending"
    completed = settle(service)
    assert completed["status"] == "completed", completed
    assert completed["worker_state"] == "terminal"
    assert completed["validation_state"] == "verified"
    result = store.get_manifest(completed["result_id"])
    assert result.parent("model") == completed["model_id"]
    assert result.parent("checkpoint") == completed["checkpoint_id"]
    assert result.parent("run") == completed["run_id"]
    owner.ledger.require_training_use(request.source_id, {request.source_id},
                                      {"group-1-run"}, completed["operation_id"])
    count = set(store.manifest_ids())
    assert count > before
    assert service.start(request)["operation"]["attempt_id"] == completed["attempt_id"]
    assert set(store.manifest_ids()) == count
    with pytest.raises(BoundaryError, match="intent_payload_mismatch"):
        service.start(replace(request, config={"epochs": 2}))


def test_cancel_ack_safe_checkpoint_and_explicit_resume(tmp_path, monkeypatch, request):
    pytest_request = request
    service, request, owner, store = ready(tmp_path, monkeypatch)
    entered, released = threading.Event(), threading.Event()
    phases, processes = {}, []
    observer = CheckpointWorkerObservation(owner, store, processes, phases)

    def message(raw, callback):
        value = json.loads(raw)
        if (value["kind"] == "event" and store.get_manifest(
                value["details"]["event_id"]).parameters.value()["kind"] == "checkpoint"):
            observer.bind_before_cancel(value)
            entered.set()
            assert released.wait(3)
        callback(raw)

    original_popen = subprocess.Popen
    original = checkpoint_child(monkeypatch, on_line=message, phases=phases,
                                diagnose_exit=True, cancel_exit="cooperative", processes=processes)
    try:
        started = service.start(request)["operation"]
        assert entered.wait(15)
        assert observer.binding["operation_id"] == started["operation_id"]
        assert observer.binding["attempt_id"] == started["attempt_id"]
        assert observer.binding["run_id"] == service.status()["operation"]["run_id"]
        with pytest.raises(BoundaryError, match="stale_operation_attempt"):
            service.cancel(started["operation_id"], "0" * 32)
        ack = service.cancel(started["operation_id"], started["attempt_id"])["operation"]
        phases["cancel_requested_at"] = service._cancel_requested_monotonic
        assert ack["status"] == "pending" and ack["worker_state"] == "running"
        assert ack["requested_action"] == "cancel" and ack["selected_result"] is False
        with pytest.raises(BoundaryError, match="writer_still_running"):
            service.reconcile(started["operation_id"], started["attempt_id"])
        released.set()
        cancelled = settle(service)
        worker_exit = observer.observe("post_sut_stop_before_fixture_cleanup")
        diagnostic = cancellation_diagnostic(service, owner, cancelled, phases)
        pytest_request.node.user_properties.append(("fixture_cancel_observation", diagnostic))
        assert worker_exit["terminated"] is True, diagnostic
        assert cancelled["status"] == "cancelled", diagnostic
        assert cancelled["worker_state"] == "terminal" and cancelled.get("checkpoint_id"), cancelled
        assert cancelled["domain_completion_state"] == "not_completed"
        assert cancelled["child_exit"]["forced"] is False
        assert cancelled["child_exit"]["exit_code"] == 0
        assert "result_id" not in cancelled
        with pytest.raises(BoundaryError, match="cumulative_limits"):
            service.resume(cancelled["operation_id"], cancelled["attempt_id"],
                           cancelled["checkpoint_id"], "2" * 32, {"wall_seconds": 601})
        # The opt-in fixture controls this cancelled child's OS-exit precondition.
        # Resume must use the original adapter and its ordinary -m child entry.
        monkeypatch.setattr(adapter_module, "_private_child", original)
        monkeypatch.setattr(subprocess, "Popen", original_popen)
        assert adapter_module._private_child is original
        assert subprocess.Popen is original_popen
        resumed = service.resume(cancelled["operation_id"], cancelled["attempt_id"],
                                 cancelled["checkpoint_id"], "2" * 32, request.limits)["operation"]
        assert resumed["attempt_id"] != cancelled["attempt_id"]
        completed = settle(service)
        assert completed["status"] == "completed", completed
        assert completed["run_id"] == cancelled["run_id"]
        assert completed["selected_result"] is True
        assert completed["child_exit"]["exit_code"] == 0
        assert completed["child_exit"]["forced"] is False
        assert not (owner.path.parent / (
            "fixture-checkpoint-" + resumed["attempt_id"] + ".jsonl")).exists()
        journal = json.loads((owner.path.parent / OPERATION_FILE).read_bytes())
        assert journal["attempts"][-1]["writer_terminal"] is True
        assert completed["elapsed_seconds"] >= cancelled["elapsed_seconds"]
    except Exception as error:
        raise AssertionError(fixture_failure_diagnostic(
            service, owner, phases, observer, error)) from error
    finally:
        released.set()
        fixture_cleanup_diagnostic(service, owner, phases, observer, pytest_request)


def test_cancelled_terminal_and_main_return_do_not_prove_os_exit(tmp_path, monkeypatch, request):
    pytest_request = request
    service, request, owner, store = ready(tmp_path, monkeypatch)
    entered, released = threading.Event(), threading.Event()
    phases, processes = {}, []
    observer = CheckpointWorkerObservation(owner, store, processes, phases)

    def message(raw, callback):
        value = json.loads(raw)
        if (value["kind"] == "event" and store.get_manifest(
                value["details"]["event_id"]).parameters.value()["kind"] == "checkpoint"):
            observer.bind_before_cancel(value)
            entered.set()
            assert released.wait(3)
        callback(raw)

    checkpoint_child(monkeypatch, on_line=message, phases=phases, diagnose_exit=True,
                     cancel_exit="hold", processes=processes)
    try:
        started = service.start(request)["operation"]
        assert entered.wait(15)
        assert observer.binding["operation_id"] == started["operation_id"]
        assert observer.binding["attempt_id"] == started["attempt_id"]
        assert observer.binding["run_id"] == service.status()["operation"]["run_id"]
        ack = service.cancel(started["operation_id"], started["attempt_id"])["operation"]
        phases["cancel_requested_at"] = service._cancel_requested_monotonic
        assert ack["status"] == "pending" and ack["requested_action"] == "cancel"
        released.set()
        stopped = settle(service)
        worker_exit = observer.observe("post_sut_stop_before_fixture_cleanup")
        diagnostic = cancellation_diagnostic(service, owner, stopped, phases)
        trace = owner.path.parent / ("fixture-checkpoint-" + started["attempt_id"] + ".jsonl")
        records = [json.loads(line) for line in trace.read_bytes().splitlines()]
        assert all(row["operation_id"] == started["operation_id"] and
                   row["attempt_id"] == started["attempt_id"] for row in records), diagnostic
        returned = next(row for row in records if row["phase"] == "main_return")
        held = next(row for row in records if row["phase"] == "shutdown_hold_entered")
        terminal = next(row for row in records if row["phase"] == "terminal_emitted")
        killed = next(row for row in phases["process_observations"]
                      if row["phase"] == "kill_entered")
        assert worker_exit["terminated"] is True, diagnostic
        assert len(processes) == 1 and killed["pid"] == processes[0].pid, diagnostic
        assert returned["pid"] == held["pid"] == observer.binding["pid"], diagnostic
        assert returned["ppid"] == held["ppid"] == observer.binding["ppid"], diagnostic
        assert all(row.get("pid") == observer.binding["pid"] and
                   row.get("ppid") == observer.binding["ppid"] for row in records), diagnostic
        assert killed["operation_id"] == started["operation_id"]
        assert killed["attempt_id"] == started["attempt_id"]
        assert terminal["state"] == "cancelled" and returned["exit_code"] == 0, diagnostic
        assert returned["observed_at"] <= held["observed_at"] <= killed["observed_at"]
        assert phases["terminals"][0]["callback_completed_at"] <= killed["observed_at"]
        assert killed["observed_at"] - phases["cancel_requested_at"] >= 1.0
        assert stopped["status"] == "interrupted_unknown", diagnostic
        assert stopped["child_exit"]["forced"] is True
        assert stopped["child_exit"]["exit_code"] != 0
        assert stopped["child_exit"]["attempt_id"] == started["attempt_id"]
        assert phases["exits"][0]["code"] == stopped["child_exit"]["exit_code"]
        assert processes[0].returncode == stopped["child_exit"]["exit_code"]
        assert stopped["worker_state"] == "terminal" and stopped.get("checkpoint_id")
        assert stopped["domain_completion_state"] == "unknown"
        assert stopped["selected_result"] is False and "result_id" not in stopped
        assert stopped["error"] == {"code": "private_child_stop_requested",
                                    "automatic_retry": False}
        assert service.status()["operation"]["attempt_id"] == started["attempt_id"]
        assert len(processes) == 1  # No second dispatch follows the unknown.
    except Exception as error:
        raise AssertionError(fixture_failure_diagnostic(
            service, owner, phases, observer, error)) from error
    finally:
        released.set()
        fixture_cleanup_diagnostic(service, owner, phases, observer, pytest_request)


def test_source_json_and_unverified_agent_provenance_cannot_admit_training(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch, source_kind="agent",
                                         qualification="engineering_only")
    before = set(store.manifest_ids())
    with pytest.raises(BoundaryError, match="structured_source_verifier_required"):
        service.start(request)
    assert not (owner.path.parent / OPERATION_FILE).exists()
    assert set(store.manifest_ids()) == before
    with pytest.raises(BoundaryError, match="invalid_recipe_config"):
        service.start(replace(request, config={"program": "/tmp/anything"}))
    with pytest.raises(BoundaryError, match="unsupported_placement"):
        service.start(replace(request, placement_id="/tmp/worker"))


def child_script(monkeypatch, body, *, on_line=None, phases=None, diagnose_exit=False,
                 cancel_exit=None, processes=None):
    assert cancel_exit in {None, "cooperative", "hold"}
    assert cancel_exit is None or diagnose_exit
    original = adapter_module._private_child
    script = "import sys\nfrom spireagent.workbench.recipes import structured_child as child\n"
    if diagnose_exit:
        script += body
        if cancel_exit is not None:
            script += """
# Record only an original successfully emitted terminal, never cancel intent.
import os
_fixture_terminal = None
_fixture_original_terminal_emit = child.ChildChannel.emit
def _fixture_capture_terminal(self, kind, **details):
    global _fixture_terminal
    _fixture_original_terminal_emit(self, kind, **details)
    if kind == 'terminal':
        assert _fixture_terminal is None
        _fixture_terminal = (self.operation_id, self.attempt_id, details['state'])
child.ChildChannel.emit = _fixture_capture_terminal
"""
        script += """
_fixture_main_code = child.main(sys.argv[1:])
_fixture_diagnostics_closed = False
try:
    _fixture_main_returned_at = time.monotonic()
    from pathlib import Path
    _fixture_args = sys.argv[1:]
    _fixture_operation = _fixture_args[_fixture_args.index('--operation-id') + 1]
    _fixture_attempt = _fixture_args[_fixture_args.index('--attempt-id') + 1]
    _fixture_directory = Path(_fixture_args[_fixture_args.index('--operation-file') + 1]).parent
    _fixture_trace = _fixture_directory / ('fixture-checkpoint-' + _fixture_attempt + '.jsonl')
    with _fixture_trace.open('ab') as _fixture_stream:
        _fixture_stream.write(json_bytes({
            'operation_id': _fixture_operation, 'attempt_id': _fixture_attempt,
            'phase': 'main_return', 'observed_at': _fixture_main_returned_at,
            'exit_code': _fixture_main_code,
            'pid': child.os.getpid(), 'ppid': child.os.getppid(),
        }))
    _fixture_diagnostics_closed = True
except Exception:
    pass  # Best-effort private diagnostics never replace the actual main code.
"""
        if cancel_exit is not None:
            script += """
if (_fixture_main_code == 0 and _fixture_terminal ==
        (_fixture_operation, _fixture_attempt, 'cancelled')):
    assert _fixture_diagnostics_closed
    sys.stdout.flush()
    sys.stderr.flush()
    if CANCEL_EXIT == 'cooperative':
        # Voluntary real OS exit, after actual main and all fixture writes.
        # This does not qualify default interpreter shutdown within one second.
        os._exit(_fixture_main_code)
    with _fixture_trace.open('ab') as _fixture_stream:
        _fixture_stream.write(json_bytes({
            'operation_id': _fixture_operation, 'attempt_id': _fixture_attempt,
            'phase': 'shutdown_hold_entered', 'observed_at': time.monotonic(),
            'pid': os.getpid(), 'ppid': os.getppid(),
        }))
    while True:
        time.sleep(0.05)  # The original parent cancellation timer must kill us.
""".replace("CANCEL_EXIT", repr(cancel_exit))
        script += "raise SystemExit(_fixture_main_code)\n"
        original_popen = subprocess.Popen

        class ObservedPopen(original_popen):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                if (processes is not None and isinstance(self.args, (list, tuple))
                        and tuple(self.args[1:3]) == ("-c", script)):
                    processes.append(self)

            def observe(self, phase, observed_at, **details):
                try:
                    values = phases.setdefault("process_observations", [])
                    if len(values) < 16:
                        command = self.args
                        values.append({
                            "phase": phase, "observed_at": observed_at, "pid": self.pid,
                            "known_returncode": self.returncode,
                            "operation_id": command[command.index("--operation-id") + 1],
                            "attempt_id": command[command.index("--attempt-id") + 1],
                            **details,
                        })
                except Exception:
                    pass

            def wait(self, *args, **kwargs):
                code = super().wait(*args, **kwargs)
                self.observe("wait_returned", time.monotonic(), exit_code=code)
                return code

            def kill(self):
                self.observe("kill_entered", time.monotonic())
                return super().kill()

        monkeypatch.setattr(subprocess, "Popen", ObservedPopen)
    else:
        script += body+"\nraise SystemExit(child.main(sys.argv[1:]))\n"

    def injected(command, *args, **kwargs):
        if phases is not None:
            callback_exit = kwargs["on_exited"]

            def exited(code, forced, seconds):
                phases.setdefault("exits", []).append({
                    "code": code, "forced": forced, "seconds": seconds,
                    "observed_at": time.monotonic(),
                })
                callback_exit(code, forced, seconds)

            kwargs["on_exited"] = exited
            callback_line = kwargs["on_stdout_line"]

            def observed(raw):
                value = json.loads(raw)
                if value["kind"] == "terminal":
                    terminal = {
                        "attempt_id": value["attempt_id"], "state": value["details"]["state"],
                        "observed_at": time.monotonic(),
                    }
                    phases.setdefault("terminals", []).append(terminal)
                try:
                    callback_line(raw)
                except BaseException as error:
                    if diagnose_exit and value["kind"] == "terminal":
                        with suppress(Exception):
                            terminal["callback_error_at"] = time.monotonic()
                            terminal["callback_error_type"] = type(error).__qualname__[:128]
                    raise
                else:
                    if diagnose_exit and value["kind"] == "terminal":
                        with suppress(Exception):
                            terminal["callback_completed_at"] = time.monotonic()

            kwargs["on_stdout_line"] = observed
        if on_line is not None:
            callback = kwargs["on_stdout_line"]
            kwargs["on_stdout_line"] = lambda raw: on_line(raw, callback)
        actual = [command[0], "-c", script, *command[3:]]
        return original(actual, *args, **kwargs)

    monkeypatch.setattr(adapter_module, "_private_child", injected)
    return original


def checkpoint_child(monkeypatch, *, on_line, phases, allow_advance=False,
                     expected_action="cancel", diagnose_exit=False,
                     cancel_exit=None, processes=None):
    assert expected_action in {"cancel", "pause"}
    assert cancel_exit is None or expected_action == "cancel" and not allow_advance
    # Parent notification does not hold the actual child at a numerical boundary.
    # Gate this fixture's first checkpoint on the existing exact journal control;
    # production publication, control polling and process exit stay authoritative.
    body = """
import os
import time
from dataclasses import asdict
from spireagent.json_boundary import json_bytes
from stpd.models.structured_engine import StructuredTrainingEngine
original_emit = child.ChildReporter.emit
original_advance = StructuredTrainingEngine.advance_chunk
original_channel_emit = child.ChildChannel.emit
first = None
expected_action = EXPECTED_ACTION

def record(phase, **details):
    fence = first['fence']
    path = fence.path.parent / ('fixture-checkpoint-' + fence.attempt_id + '.jsonl')
    with path.open('ab') as stream:
        stream.write(json_bytes({
            'operation_id': fence.operation_id, 'attempt_id': fence.attempt_id,
            'event_id': first['event_id'], 'checkpoint_id': first['checkpoint_id'],
            'run_id': first['run_id'], 'pid': os.getpid(), 'ppid': os.getppid(),
            'executable': sys.executable, 'base_executable': sys._base_executable,
            'phase': phase, 'observed_at': time.monotonic(), **details,
        }))

def await_control():
    deadline = time.monotonic() + 5
    while first['fence'].requested_action() != expected_action:
        if time.monotonic() >= deadline:
            raise AssertionError('fixture exact checkpoint ' + expected_action + ' not observed')
        time.sleep(0.005)
    record(expected_action + '_observed')

def emit(self, event):
    global first
    identity = original_emit(self, event)
    info = event.parameters.value()
    if first is None and info['kind'] == 'checkpoint' and self.fence.operation()['mode'] == 'start':
        current = self.fence.operation()
        checkpoint_id = info['details']['checkpoint_id']
        checkpoint = self.fence.store.get_manifest(checkpoint_id)
        assert info['attempt'] == self.fence.attempt_id
        assert event.parent('run') == current['run_id']
        assert checkpoint.parameters.value()['attempt'] == self.fence.attempt_id
        assert checkpoint.parent('run') == current['run_id']
        assert checkpoint.parent('training_input') == current['input_id']
        assert checkpoint.producer == event.producer
        first = {'fence': self.fence, 'event_id': identity, 'checkpoint_id': checkpoint_id,
                 'run_id': current['run_id']}
        record('checkpoint_emitted', sequence=self.channel.sequence,
               requested_action=current['requested_action'])
        if not ALLOW_ADVANCE:
            await_control()
    return identity

def advance(self):
    if first is None:
        raise AssertionError('fixture checkpoint must precede numerical advance')
    before = first['fence'].requested_action()
    progress = original_advance(self)
    record('chunk_advanced', requested_action_before=before, progress=asdict(progress))
    await_control()
    return progress

def channel_emit(self, kind, **details):
    original_channel_emit(self, kind, **details)
    if kind == 'terminal' and first is not None:
        record('terminal_emitted', state=details['state'])

child.ChildReporter.emit = emit
child.ChildChannel.emit = channel_emit
if ALLOW_ADVANCE:
    StructuredTrainingEngine.advance_chunk = advance
""".replace("ALLOW_ADVANCE", repr(allow_advance)).replace("EXPECTED_ACTION", repr(expected_action))
    return child_script(monkeypatch, body, on_line=on_line, phases=phases,
                        diagnose_exit=diagnose_exit, cancel_exit=cancel_exit,
                        processes=processes)


def cancellation_diagnostic(service, owner, operation, phases, *, suffix="", child_attempt=None):
    attempt = operation.get("attempt_id", "unstarted")
    trace_attempt = child_attempt if child_attempt is not None else attempt
    path = owner.path.parent / ("fixture-checkpoint-" + trace_attempt + ".jsonl")
    records = [json.loads(line) for line in path.read_bytes().splitlines()] if path.exists() else []
    raw = json_bytes({"operation": operation, "parent": phases, "child": records,
                      "child_trace_attempt": trace_attempt, "failure": service._failure_diagnostic})
    assert len(raw) <= 65536, "bounded fixture cancellation diagnostic required"
    destination = owner.path.parent / (
        "fixture-cancel-diagnostic-" + attempt + suffix + ".json")
    with destination.open("xb") as stream:
        stream.write(raw)
    print("fixture_cancel_diagnostic=" + str(destination))
    return raw.decode("utf-8")


def test_unknown_attempt_requires_reconcile_no_automatic_retry(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)

    def fail_checkpoint(raw, callback):
        callback(raw)
        message = json.loads(raw)
        if message["kind"] == "event":
            event = store.get_manifest(message["details"]["event_id"])
            if event.parameters.value()["kind"] == "checkpoint":
                raise BoundaryError("fixture", "uncertain_after_checkpoint")

    original = child_script(monkeypatch, """
import time
from stpd.models.structured_engine import StructuredTrainingEngine
def hung_boundary(self):
    time.sleep(30)
StructuredTrainingEngine.advance_chunk = hung_boundary
""", on_line=fail_checkpoint)
    service.start(request)
    unknown = settle(service)
    assert unknown["status"] == "interrupted_unknown", unknown
    assert unknown["child_exit"]["forced"] is True
    count = set(store.manifest_ids())
    assert service.status()["operation"]["attempt_id"] == unknown["attempt_id"]
    assert set(store.manifest_ids()) == count
    monkeypatch.setattr(adapter_module, "_private_child", original)
    reconciled = service.reconcile(unknown["operation_id"], unknown["attempt_id"])["operation"]
    assert reconciled["status"] == "pending"
    terminal = settle(service)
    assert terminal["status"] == "interrupted_unknown"
    assert terminal["worker_state"] == "terminal"
    assert terminal["error"]["code"] == "no_completed_result_reconcile_required"
    assert set(store.manifest_ids()) == count


def test_completion_journal_uncertainty_reconciles_without_training(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    original = service._finish_attempt
    once = [False]

    def fail_completion(*args, **kwargs):
        if kwargs.get("status") == "completed" and not once[0]:
            once[0] = True
            raise BoundaryError("fixture", "completion_journal_uncertainty")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_finish_attempt", fail_completion)
    service.start(request)
    unknown = settle(service)
    assert unknown["status"] == "interrupted_unknown"
    before = set(store.manifest_ids())
    service.reconcile(unknown["operation_id"], unknown["attempt_id"])
    completed = settle(service)
    assert completed["status"] == "completed", completed
    assert set(store.manifest_ids()) == before
    assert completed["run_id"] == unknown["run_id"]


def test_cumulative_wall_limit_kills_hung_child_and_cannot_reset(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    request = replace(request, limits={"wall_seconds": 1})
    child_script(monkeypatch, """
import time
def hung(args, channel):
    channel.emit('started')
    time.sleep(30)
child.run_child = hung
""")
    service.start(request)
    stopped = settle(service)
    assert stopped["status"] == "interrupted_unknown", stopped
    assert stopped["child_exit"]["forced"] is True
    assert stopped["child_exit"]["exit_code"] != 0
    assert stopped["worker_state"] == "terminal"
    assert stopped["elapsed_seconds"] >= 1
    assert stopped["error"]["code"] == "private_child_timeout"
    with pytest.raises(BoundaryError, match="cumulative_budget_exhausted"):
        service.resume(stopped["operation_id"], stopped["attempt_id"], "0" * 64,
                       "2" * 32, request.limits)


def test_wrong_or_v1_checkpoint_rejected_before_new_attempt(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    phases = {}

    def message(raw, callback):
        callback(raw)
        value = json.loads(raw)
        if (value["kind"] == "event" and store.get_manifest(
                value["details"]["event_id"]).parameters.value()["kind"] == "checkpoint"):
            service.pause(value["operation_id"], value["attempt_id"])

    # Wrong-checkpoint admission requires a real stopped writer, independently
    # of whether Windows process teardown fits the cooperative cancel grace.
    # Actual safe/forced cancellation behavior remains covered separately.
    checkpoint_child(monkeypatch, on_line=message, phases=phases, expected_action="pause")
    service.start(request)
    saved = settle(service)
    diagnostic = cancellation_diagnostic(service, owner, saved, phases)
    assert saved["status"] == "paused" and saved.get("checkpoint_id"), diagnostic
    assert saved["domain_completion_state"] == "not_completed"
    assert saved["worker_state"] == "terminal" and saved["requested_action"] == "pause"
    assert saved["child_exit"]["forced"] is False
    assert saved["child_exit"]["exit_code"] == 0
    assert service._cancel_requested_monotonic is None
    cp = store.get_manifest(saved["checkpoint_id"])
    from spireagent.workbench.recipes.structured import verify_resume_checkpoint

    verify_resume_checkpoint(store, service._read(owner.path.parent / OPERATION_FILE,
                                                owner.identity), cp.artifact_id)
    assert cp.parent("run") == saved["run_id"]
    assert cp.parent("training_input") == saved["input_id"]
    assert cp.parameters.value()["attempt"] == saved["attempt_id"]
    assert cp.parameters.value()["optimizer_updates"] == 0
    wrong = Manifest(cp.kind, cp.producer, cp.parents, cp.payloads,
                     FrozenObject.of({**cp.parameters.value(),
                                      "schema": "stpd/structured-m2-training-checkpoint-v1"}))
    store.publish(wrong)
    journal = (owner.path.parent / OPERATION_FILE).read_bytes()
    artifacts = set(store.manifest_ids())
    with pytest.raises(BoundaryError, match="checkpoint_run_identity_mismatch"):
        service.resume(saved["operation_id"], saved["attempt_id"], wrong.artifact_id,
                       "2" * 32, request.limits)
    assert (owner.path.parent / OPERATION_FILE).read_bytes() == journal
    assert set(store.manifest_ids()) == artifacts


def _cancel_after_observed_advance(service, store, value, record, phases):
    assert record["phase"] == "chunk_advanced"
    assert record["operation_id"] == value["operation_id"]
    assert record["attempt_id"] == value["attempt_id"]
    assert record["event_id"] == value["details"]["event_id"]
    assert record["checkpoint_id"] == store.get_manifest(
        record["event_id"]).parameters.value()["details"]["checkpoint_id"]
    assert record["requested_action_before"] == "continue"
    assert record["progress"]["boundary"] >= 1
    # The verified child record is already visible to the parent before this
    # actual cancel call. A coarse monotonic clock may give both events one tick.
    phases["advance_observed_before_cancel"] = {
        "record": record.copy(), "observed_at": time.monotonic(),
    }
    service.cancel(value["operation_id"], value["attempt_id"])
    phases["cancel_requested_at"] = service._cancel_requested_monotonic


def _assert_advance_precedes_cancel(record, phases, diagnostic):
    observed = phases["advance_observed_before_cancel"]
    assert observed["record"] == record, diagnostic
    assert (record["observed_at"] <= observed["observed_at"]
            <= phases["cancel_requested_at"]), diagnostic


def test_checkpoint_advance_causal_marker_accepts_quantized_clock(monkeypatch):
    stamp = 1845.281
    monkeypatch.setattr(time, "monotonic", lambda: stamp)
    value = {"operation_id": "1" * 32, "attempt_id": "2" * 32,
             "details": {"event_id": "3" * 64}}
    record = {"operation_id": value["operation_id"], "attempt_id": value["attempt_id"],
              "event_id": value["details"]["event_id"], "checkpoint_id": "4" * 64,
              "phase": "chunk_advanced", "requested_action_before": "continue",
              "progress": {"boundary": 1}, "observed_at": stamp}
    phases, cancelled = {}, []
    store = SimpleNamespace(get_manifest=lambda _: SimpleNamespace(
        parameters=FrozenObject.of({"details": {"checkpoint_id": record["checkpoint_id"]}})))
    service = SimpleNamespace(_cancel_requested_monotonic=None)

    def cancel(operation_id, attempt_id):
        assert phases["advance_observed_before_cancel"]["record"] == record
        cancelled.append((operation_id, attempt_id))
        service._cancel_requested_monotonic = time.monotonic()

    service.cancel = cancel
    _cancel_after_observed_advance(service, store, value, record, phases)
    assert cancelled == [(value["operation_id"], value["attempt_id"])]
    assert phases["cancel_requested_at"] == record["observed_at"]
    _assert_advance_precedes_cancel(record, phases, "quantized clock still preserves causality")
    # Equal timestamps require the exact prior observation; later or rebound
    # records cannot acquire ordering merely because the clock has low precision.
    with pytest.raises(AssertionError):
        _assert_advance_precedes_cancel(
            {**record, "attempt_id": "5" * 32}, phases, "rebound child")
    later = {**record, "observed_at": stamp + 1}
    with pytest.raises(AssertionError):
        _assert_advance_precedes_cancel(later, {
            **phases, "advance_observed_before_cancel": {"record": later, "observed_at": stamp},
        }, "child observed after cancel")


def test_checkpoint_notification_does_not_hold_child_before_parent_cancel(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    phases, advanced = {}, []

    def message(raw, callback):
        callback(raw)
        value = json.loads(raw)
        if (value["kind"] != "event" or store.get_manifest(
                value["details"]["event_id"]).parameters.value()["kind"] != "checkpoint"
                or advanced):
            return
        path = owner.path.parent / ("fixture-checkpoint-" + value["attempt_id"] + ".jsonl")
        deadline = time.monotonic() + 5
        while not advanced:
            if path.exists():
                raw_records = path.read_bytes()
                if raw_records.endswith(b"\n"):
                    advanced.extend(record for line in raw_records.splitlines()
                                    if (record := json.loads(line))["phase"] == "chunk_advanced")
            if time.monotonic() >= deadline:
                raise AssertionError("unbound child did not advance before parent cancel")
            if not advanced:
                threading.Event().wait(0.005)
        record = advanced[0]
        _cancel_after_observed_advance(service, store, value, record, phases)

    checkpoint_child(monkeypatch, on_line=message, phases=phases, allow_advance=True)
    service.start(request)
    saved = settle(service)
    diagnostic = cancellation_diagnostic(service, owner, saved, phases)
    assert len(advanced) == 1, diagnostic
    _assert_advance_precedes_cancel(advanced[0], phases, diagnostic)
    assert saved["requested_action"] == "cancel", diagnostic
    assert saved["selected_result"] is False and saved["worker_state"] == "terminal", diagnostic
    assert saved["domain_completion_state"] != "completed" and "result_id" not in saved, diagnostic


def test_typed_legacy_request_preserves_previous_v1_and_legacy_idempotency(tmp_path, monkeypatch):
    config, dataset_id, _, _store = _ready(tmp_path, monkeypatch)
    service = LocalTrainingService(config)
    service.start(dataset_id)
    legacy = settle(service)
    assert legacy["status"] == "completed", legacy
    request = TrainingRequest("1" * 32, "stage1a.dsimple.s.v1", dataset_id, {}, limits={},
                              after_completed_operation_id=legacy["operation_id"])
    service.start(request)
    typed = settle(service)
    assert typed["status"] == "completed", typed
    assert typed["previous_completed"] == legacy
    assert typed["attempt_id"] and typed["evaluation_id"]
    assert typed["recipe_id"] == "stage1a.dsimple.s.v1"
    assert typed["supported_actions"] == []
    assert service.start(dataset_id)["operation"]["operation_id"] == typed["operation_id"]
    assert service.start(request)["operation"]["attempt_id"] == typed["attempt_id"]


@pytest.mark.parametrize("table", ["curation_claims", "curation_exact_source_index"])
def test_source_claim_and_exact_index_are_required_before_journal_admission(
    tmp_path, monkeypatch, table,
):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    with owner.transaction() as db:
        db.execute("DELETE FROM " + table)
    before = set(store.manifest_ids())
    with pytest.raises(BoundaryError, match="training_claim_mismatch|source_index_incomplete"):
        service.start(request)
    assert not (owner.path.parent / OPERATION_FILE).exists()
    assert set(store.manifest_ids()) == before


def test_reconcile_preserves_cancelled_completion_selection(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    original = service._finish_attempt
    cancelled_once = [False]

    def cancel_before_completion(path, current_owner, operation_id, **updates):
        if updates.get("status") == "completed" and not cancelled_once[0]:
            cancelled_once[0] = True
            current = service._read(path, current_owner.identity)
            service.cancel(operation_id, current["attempt_id"])
        return original(path, current_owner, operation_id, **updates)

    monkeypatch.setattr(service, "_finish_attempt", cancel_before_completion)
    service.start(request)
    cancelled = settle(service)
    assert cancelled["status"] == "cancelled" and cancelled["selected_result"] is False
    before = set(store.manifest_ids())
    service.reconcile(cancelled["operation_id"], cancelled["attempt_id"])
    reconciled = settle(service)
    assert reconciled["status"] == "cancelled", reconciled
    assert reconciled["selected_result"] is False
    assert reconciled["domain_completion_state"] == "completed"
    assert reconciled["result_id"] == cancelled["result_id"]
    assert reconciled["validation_state"] == "verified"
    assert set(store.manifest_ids()) == before
    journal = json.loads((owner.path.parent / OPERATION_FILE).read_bytes())
    assert journal["selected_result"] is False


def test_numeric_runtime_error_retains_unknown_actual_exit_and_private_diagnostic(
    tmp_path, monkeypatch,
):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    child_script(monkeypatch, """
from stpd.models.structured_engine import StructuredTrainingEngine
def failing(self):
    raise RuntimeError('synthetic numeric failure')
StructuredTrainingEngine.advance_chunk = failing
""")
    service.start(request)
    unknown = settle(service)
    assert unknown["status"] == "interrupted_unknown", unknown
    assert unknown["error"]["code"] == "structured_worker_runtime_error"
    assert unknown["child_exit"]["exit_code"] != 0
    assert unknown["child_exit"]["forced"] is False
    assert unknown["worker_state"] == "terminal" and unknown["checkpoint_id"]
    assert service._failure_diagnostic["child"]["exception_type"] == "builtins.RuntimeError"
    assert "synthetic numeric failure" not in json.dumps(unknown)
    log = owner.path.parent/("local-training-"+unknown["operation_id"]+"-"+
                             unknown["attempt_id"]+".log")
    assert b'synthetic numeric failure' in log.read_bytes()


def test_training_rejects_heldout_partition_before_use_or_journal(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch, split="dev")
    before = set(store.manifest_ids())
    with pytest.raises(BoundaryError, match="train_only_source_required"):
        service.start(request)
    assert not (owner.path.parent / OPERATION_FILE).exists()
    assert set(store.manifest_ids()) == before
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_uses WHERE reference=?",
                          (request.intent_id,)).fetchone()[0] == 0


def test_parent_tensor_rng_and_threads_are_unchanged_by_actual_child(tmp_path, monkeypatch):
    import torch

    service, request, owner, store = ready(tmp_path, monkeypatch)
    previous_rng, previous_threads = torch.get_rng_state().clone(), torch.get_num_threads()
    service.start(request)
    completed = settle(service)
    assert completed["status"] == "completed", completed
    assert completed["worker_isolation"] == "private_child-v1"
    assert completed["child_exit"]["exit_code"] == 0
    assert torch.equal(previous_rng, torch.get_rng_state())
    assert torch.get_num_threads() == previous_threads


def test_invalid_cadence_rejected_and_default_storage_cadence_is_bounded(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    for cadence in (0, 101, True):
        with pytest.raises(BoundaryError, match="invalid_checkpoint_cadence"):
            service.start(replace(request, config={"checkpoint_every_boundaries": cadence}))
    descriptor = next(item for item in service.capabilities()["recipes"]
                      if item["recipe_id"] == STRUCTURED_RECIPE)
    assert descriptor["config_defaults"]["checkpoint_every_boundaries"] == 100
    assert descriptor["max_total_attempts"] == 32


def test_typed_protocol_source_uses_original_train_rows_and_excludes_heldout_ancestry(
    tmp_path, monkeypatch,
):
    from test_protocol_source import PROJECTION, closure, publish_run

    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.managed_local_workspace import create_managed_workspace
    from stpd.fullrun.protocol_source import publish_protocol_source_partition

    state = tmp_path/"profile"
    state.mkdir()
    created = create_managed_workspace(state)
    owner = created["curation_owner"]
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    refs, partitions = {}, {}
    for seed, split in enumerate(("train", "dev", "test"), start=1):
        ref, _, _ = publish_run(store, tmp_path, str(seed), split)
        refs[split] = ref
        partitions[split] = publish_protocol_source_partition(store, (ref,), split, PROJECTION)
        owner.reserve_verified_protocol_source(store, partitions[split].manifest.artifact_id)
    service = LocalTrainingService(ProjectConfig(state, "", "", None, combination()))
    request = TrainingRequest("1" * 32, STRUCTURED_RECIPE,
                              partitions["train"].manifest.artifact_id, {"epochs": 1})
    service.start(request)
    completed = settle(service)
    assert completed["status"] == "completed", completed
    owner.require_verified_protocol_training_use(store, request.source_id,
                                                completed["operation_id"])
    ancestry = closure(store, completed["model_id"])
    for split in ("dev", "test"):
        assert partitions[split].manifest.artifact_id not in ancestry
        assert refs[split].raw_id not in ancestry and refs[split].report_id not in ancestry
    with owner.transaction() as db:
        uses = {tuple(row) for row in db.execute("SELECT source,kind FROM curation_source_uses")}
        assert uses == {(refs["train"].raw_id, "training")}
        assert db.execute("SELECT count(*) FROM curation_occurrences").fetchone()[0] == 0


def test_child_never_calls_mutable_operation_journal_writer(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    child_script(monkeypatch, """
import spireagent.storage.replaceable_file as journal
def forbidden(*args, **kwargs):
    raise AssertionError('child must not rewrite the operation journal')
journal.write_replaceable_json = forbidden
""")
    service.start(request)
    completed = settle(service)
    assert completed["status"] == "completed", completed
    assert completed["child_exit"]["exit_code"] == 0


def test_artifact_reservation_exhaustion_precedes_immutable_publication(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    request = replace(request, limits={"wall_seconds": 600, "scratch_bytes": 16 * 1024 * 1024})
    before = set(store.manifest_ids())
    child_script(monkeypatch, """
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
def over_budget(args, channel):
    store = ManifestArtifactStore(LocalBlobStore(args.store, create=False))
    fence = child.ReadOnlyAttemptFence(args.operation_file,args.operation_id,args.attempt_id,
                                      args.remaining_seconds,store)
    fence.channel = channel
    channel.emit('started')
    fence.reserve_artifact('payload',17*1024*1024)
    raise AssertionError('publication must not become reachable')
child.run_child = over_budget
""")
    service.start(request)
    unknown = settle(service)
    assert unknown["status"] == "interrupted_unknown", unknown
    assert unknown["error"]["code"] == "artifact_budget_exhausted"
    assert unknown["child_exit"]["forced"] is True
    assert unknown["artifact_reserved_bytes"] == 0
    assert set(store.manifest_ids()) == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent-death/orphan process fixture")
def test_surviving_orphan_child_blocks_reconciliation_without_pid_guesses(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    pid_path = tmp_path/"owned-orphan-pid"
    script = """
import sys
from pathlib import Path
from spireagent.workbench.developer import ProjectConfig, LocalResearchWorkspaceConfig, combination
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.recipes import structured as adapter
original = adapter._private_child
child_script = '''import os,time,sys
from pathlib import Path
from spireagent.workbench.recipes import structured_child as child
def hung(args, channel):
    Path(sys.argv[-1]).write_text(str(os.getpid()))
    channel.emit('started')
    time.sleep(30)
child.run_child = hung
raise SystemExit(child.main(sys.argv[1:-1]))
'''
def launch(command,*args,**kwargs):
    actual = [command[0],'-c',child_script,*command[3:],sys.argv[5]]
    return original(actual,*args,**kwargs)
adapter._private_child = launch
config = ProjectConfig(Path(sys.argv[1]),'', '',None,combination(),
                       LocalResearchWorkspaceConfig(Path(sys.argv[2]),Path(sys.argv[3])))
service = LocalTrainingService(config)
service.start(TrainingRequest('1'*32,'structured-m2-cpu-v2',sys.argv[4],{'epochs':1}))
service._thread.join()
"""
    supervisor = subprocess.Popen(
        [sys.executable, "-c", script, str(service.config.state_dir), str(owner.store_dir),
         str(service.config.research_workspace.registry_path), request.source_id, str(pid_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
    child_pid = None
    try:
        deadline = time.monotonic()+15
        while not pid_path.exists():
            assert supervisor.poll() is None, supervisor.communicate()
            assert time.monotonic() < deadline
            time.sleep(0.02)
        child_pid = int(pid_path.read_text())  # Exact trusted fixture child reports its own PID.
        supervisor.kill()
        supervisor.communicate(timeout=5)
        operation = service.status()["operation"]
        assert operation["status"] == "interrupted_unknown"
        journal = (owner.path.parent/OPERATION_FILE).read_bytes()
        with pytest.raises(BoundaryError, match="orphan_child_still_running"):
            service.reconcile(operation["operation_id"], operation["attempt_id"])
        assert (owner.path.parent/OPERATION_FILE).read_bytes() == journal
        os.kill(child_pid, signal.SIGKILL)
        child_pid = None
        # The application relies on OS ownership, never this test's cleanup PID.
        child_path = service._child_path(owner.path.parent/OPERATION_FILE,
                                         json.loads(journal))
        from spireagent.workbench.instance_lock import instance_lock

        deadline = time.monotonic()+5
        while True:
            try:
                with instance_lock(child_path, create=False):
                    break
            except BoundaryError:
                assert time.monotonic() < deadline
                time.sleep(0.02)
        reconciled = service.reconcile(operation["operation_id"],
                                       operation["attempt_id"])["operation"]
        assert reconciled["status"] == "failed"
        assert reconciled["error"]["code"] == "reconciled_preparation_without_run"
        assert "child_exit" not in reconciled  # No invented orphan exit receipt.
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.communicate(timeout=5)
        if child_pid is not None:
            os.kill(child_pid, signal.SIGKILL)


def test_forced_cancel_during_engine_initialization_retains_unknown_without_checkpoint(
    tmp_path, monkeypatch,
):
    service, request, owner, store = ready(tmp_path, monkeypatch)

    def cancel_prepared(raw, callback):
        callback(raw)
        value = json.loads(raw)
        if value["kind"] == "prepared":
            service.cancel(value["operation_id"], value["attempt_id"])

    child_script(monkeypatch, """
import time
from stpd.models.structured_engine import StructuredTrainingEngine
def blocked_initialization(self, *args, **kwargs):
    time.sleep(30)
StructuredTrainingEngine.__init__ = blocked_initialization
""", on_line=cancel_prepared)
    service.start(request)
    stopped = settle(service)
    assert stopped["status"] == "interrupted_unknown", stopped
    assert stopped["domain_completion_state"] == "unknown"
    assert stopped["requested_action"] == "cancel"
    assert stopped["application_disposition"] == "cancel_requested"
    assert stopped["selected_result"] is False
    assert stopped["worker_state"] == "terminal"
    assert stopped["child_exit"]["forced"] is True
    assert stopped["child_exit"]["exit_code"] != 0
    assert stopped["error"]["code"] == "private_child_stop_requested"
    assert "checkpoint_id" not in stopped and "result_id" not in stopped
    assert stopped["supported_actions"] == ["reconcile"]
    before = set(store.manifest_ids())
    journal = (owner.path.parent / OPERATION_FILE).read_bytes()
    assert service.status()["operation"]["attempt_id"] == stopped["attempt_id"]
    assert (owner.path.parent / OPERATION_FILE).read_bytes() == journal
    assert set(store.manifest_ids()) == before
