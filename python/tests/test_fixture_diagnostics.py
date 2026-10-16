"""Failure diagnostics remain useful without changing the observed failure."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fixture_diagnostics import (
    FixtureDiagnostics,
    failure_snapshot,
    operation_snapshot,
    scoped_failure_snapshot,
    scoped_supervisor_failure,
    thread_snapshot,
)


def test_scoped_original_failure_projection_excludes_unknown_values_and_private_fields():
    private = "DO-NOT-PUBLISH-PRIVATE-CONTENT"
    value = {
        "replaced": False, "injection_stage": "not_seen", "status": "interrupted_unknown",
        "error_code": "scratch_inventory_invalid", "stderr": private, "path": private,
        "parent_failure": {
            "stage": "preparing_run", "exception_type": "spireagent.json_boundary.BoundaryError",
            "message": private, "origin": {"file": "training_scratch.py",
                                            "function": "retained_scratch_bytes", "line": 7,
                                            "locals": private},
            "scratch_inventory": {"scan_phase": "scandir_open", "reason": "os_error",
                                  "active_attempt": True, "attempt_scope": "current",
                                  "entry_scope": "descendant", "file_type": "directory",
                                  "entries": 2, "queued": 0, "path": private},
            "causes": [{"link": "cause", "exception_type": "builtins.PermissionError",
                        "errno": 13, "winerror": 5, "filename": private, "message": private}],
        },
    }
    observed = scoped_failure_snapshot(value)
    assert observed["replaced"] is False and observed["injection_stage"] == "not_seen"
    assert observed["parent_failure"]["scratch_inventory"]["attempt_scope"] == "current"
    assert observed["parent_failure"]["causes"][0]["winerror"] == 5
    assert observed["parent_failure"]["origin"]["line"] == 7
    assert private not in json.dumps(observed)
    value["injection_stage"] = private
    value["parent_failure"]["origin"].update(file=private, function=private)
    value["parent_failure"].update(errno=private, winerror=private)
    value["parent_failure"]["origin"]["line"] = private
    value["parent_failure"]["scratch_inventory"].update(scan_phase=private, entries=True)
    value["parent_failure"]["causes"] = [{"link": [], "exception_type": private,
                                          "errno": private, "winerror": True}] * 20
    observed = scoped_failure_snapshot(value)
    assert observed["injection_stage"] == "unknown"
    assert observed["parent_failure"]["origin"]["file"] == "unknown"
    assert observed["parent_failure"]["origin"]["line"] is None
    assert observed["parent_failure"]["errno"] is None
    assert observed["parent_failure"]["winerror"] is None
    assert observed["parent_failure"]["scratch_inventory"]["scan_phase"] == "unknown"
    assert observed["parent_failure"]["scratch_inventory"]["entries"] is None
    assert len(observed["parent_failure"]["causes"]) == 4
    assert observed["parent_failure"]["causes"][0] == {
        "link": "unknown", "exception_type": "unknown", "errno": None, "winerror": None}
    assert private not in json.dumps(observed)


@pytest.mark.parametrize("invalid_line", [True, 0, -1, 1_000_001])
def test_scoped_failure_numeric_fields_do_not_accept_bool_or_unbounded_locations(invalid_line):
    value = scoped_failure_snapshot({"parent_failure": {
        "errno": True, "winerror": ["PRIVATE"],
        "origin": {"line": invalid_line}, "owner_call": {"line": invalid_line},
    }})["parent_failure"]
    assert value["errno"] is None
    assert "winerror" not in value or value["winerror"] is None
    assert value["origin"]["line"] is None and value["owner_call"]["line"] is None


def test_early_supervisor_import_failure_retains_original_bytes_outside_ci_projection(tmp_path):
    import hashlib

    result = subprocess.run(
        [sys.executable, "-c", "raise ImportError('PRIVATE-IMPORT-FAILURE')"], capture_output=True)
    assert result.returncode != 0
    directory = tmp_path / "scoped-supervisor-failure"
    value = scoped_supervisor_failure(result.stdout, result.stderr, result.returncode, directory)
    assert value["diagnostic_found"] is False and value["parent_failure"] == {}
    assert value["supervisor_exit"] == result.returncode
    private = value["private_output"]
    assert private["location"] == "test_tmp_scoped_supervisor_failure"
    assert private["retention"] == "stored"
    assert (directory / "stderr.bin").read_bytes() == result.stderr
    assert (directory / "stdout.bin").read_bytes() == result.stdout
    assert private["stderr"]["captured_sha256"] == hashlib.sha256(result.stderr).hexdigest()
    assert private["stderr"]["retained_bytes"] == len(result.stderr)
    assert private["stderr"]["stored"] is True
    assert "PRIVATE" not in json.dumps(value) and str(tmp_path) not in json.dumps(value)


def test_supervisor_pipe_retention_is_bounded_and_reprojects_diagnostic(tmp_path):
    import hashlib

    record = {"replaced": False, "injection_stage": "not_seen", "token": "PRIVATE-TOKEN",
              "parent_failure": {"errno": "PRIVATE-ERRNO", "origin": {"line": "PRIVATE-LINE"}}}
    stdout = b"scoped_failure_diagnostic=" + json.dumps(record).encode() + b"\n" + b"x"*70000
    stderr = b"PRIVATE-RAW-STDERR\xff"*5000
    directory = tmp_path / "scoped-supervisor-failure"
    value = scoped_supervisor_failure(stdout, stderr, 1, directory)
    assert value["diagnostic_found"] is True and value["replaced"] is False
    assert value["parent_failure"]["errno"] is None
    assert value["parent_failure"]["origin"]["line"] is None
    assert "PRIVATE" not in json.dumps(value)
    for name, raw in (("stdout", stdout), ("stderr", stderr)):
        saved = (directory / (name + ".bin")).read_bytes()
        assert saved == raw[:64*1024]
        receipt = value["private_output"][name]
        assert receipt["truncated"] is True and receipt["retained_bytes"] == 64*1024
        assert receipt["captured_sha256"] == hashlib.sha256(raw).hexdigest()
        assert receipt["retained_sha256"] == hashlib.sha256(saved).hexdigest()
    unavailable = scoped_supervisor_failure(stdout, stderr, 1, directory)
    assert unavailable["supervisor_exit"] == 1 and unavailable["diagnostic_found"] is True
    assert unavailable["private_output"]["retention"] == "unavailable"
    assert unavailable["private_output"]["stdout"]["stored"] is False
    assert unavailable["private_output"]["stdout"]["retained_bytes"] is None
    assert (directory / "stdout.bin").read_bytes() == stdout[:64*1024]


def test_parent_failure_cause_chain_is_bounded_and_does_not_export_exception_attributes():
    from spireagent.json_boundary import BoundaryError
    from spireagent.workbench.local_training import _safe_parent_failure

    causes = [PermissionError(13, "SECRET-MESSAGE", "/SECRET-PATH") for _ in range(7)]
    for current, following in zip(causes, causes[1:], strict=False):
        current.__cause__ = following
    error = BoundaryError("local_training", "scratch_inventory_invalid")
    error.__cause__ = causes[0]
    error.private_token = "SECRET-TOKEN"
    value = _safe_parent_failure(error, "preparing_run")
    assert len(value["causes"]) == 4
    assert all(item["errno"] == 13 for item in value["causes"])
    assert "SECRET" not in json.dumps(value)
    causes[0].__cause__ = error
    assert len(_safe_parent_failure(error, "preparing_run")["causes"]) == 1
    error.__cause__ = None
    error.__context__ = RuntimeError("SECRET-CONTEXT")
    error.__suppress_context__ = False
    assert _safe_parent_failure(error, "preparing_run")["causes"][0] == {
        "link": "context", "exception_type": "builtins.RuntimeError",
        "errno": None, "winerror": None}


def test_whitelisted_operation_and_blocked_thread_survive_fixture_cleanup(tmp_path):
    diagnostic = FixtureDiagnostics("blocked-publisher", root=tmp_path / "retained")
    entered, release = threading.Event(), threading.Event()

    def publisher():
        private_value = "must-not-appear-in-stack"  # noqa: F841
        entered.set()
        release.wait()

    thread = threading.Thread(target=publisher)
    thread.start()
    try:
        assert entered.wait(timeout=2)
        operation = {"status": "pending", "_producer": {"token": "private"},
                     "config": {"credential": "private"},
                     "progress": {"completed": 1, "secret": "private"}}
        path = Path(diagnostic.record("join-ended", operation=operation_snapshot(operation),
                                      thread=thread_snapshot(thread)))
        observed = json.loads(path.read_text(encoding="utf-8"))
        assert observed["operation"] == {"status": "pending", "progress": {"completed": 1}}
        assert observed["thread"]["alive"] is True
        assert any("release.wait()" in frame for frame in observed["thread"]["stack"])
        assert "must-not-appear-in-stack" not in path.read_text(encoding="utf-8")
    finally:
        release.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert path.is_file()


def test_diagnostic_write_error_does_not_replace_original_failure(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied", encoding="utf-8")
    diagnostic = FixtureDiagnostics("unwritable", root=blocked)
    assert diagnostic.record("original-error", error_code="original") == "diagnostic_unavailable"


@pytest.mark.parametrize("release_after_deadline", [False, True])
@pytest.mark.parametrize("coarse_monotonic", [False, True], ids=["native-clock", "coarse-clock"])
def test_product_join_failure_stays_failed_when_diagnostics_observe_late_completion(
    tmp_path, monkeypatch, release_after_deadline, coarse_monotonic,
):
    import test_native_agent_product_data as owner

    if coarse_monotonic:
        # A real blocked join may finish within one Windows monotonic-clock tick.
        # Keep the actual thread/joins and high-resolution clock; only make the
        # helper's coarse clock return an unchanged sample across both waits.
        monkeypatch.setattr(owner, "time", SimpleNamespace(
            monotonic=lambda: 1000.0, perf_counter=owner.time.perf_counter,
        ))

    entered, release = threading.Event(), threading.Event()
    operation = {"status": "pending"}

    def worker():
        entered.set()
        release.wait()
        operation["status"] = "completed"

    class RetainedDiagnostic(FixtureDiagnostics):
        def __init__(self, name):
            super().__init__(name, root=tmp_path)

        def record(self, phase, **details):
            path = super().record(phase, **details)
            if phase == "join-ended" and release_after_deadline:
                release.set()
            return path

    monkeypatch.setattr(owner, "FixtureDiagnostics", RetainedDiagnostic)
    thread = threading.Thread(target=worker)
    service = SimpleNamespace(thread=thread, operation=operation)
    thread.start()
    try:
        assert entered.wait(timeout=2)
        with pytest.raises(AssertionError):
            owner.settled(service, join_timeout=0.01, diagnostic_timeout=0.05)
        first = json.loads(next(tmp_path.glob("*/002-join-ended.json")).read_text())
        tail = json.loads(next(tmp_path.glob("*/003-after-failed-join.json")).read_text())
        assert first["alive_at_join"] is True
        assert first["join_timeout_seconds"] == 0.01
        assert first["join_elapsed_seconds"] >= 0.01
        assert tail["original_join_failed"] is True
        assert tail["thread"]["alive"] is (not release_after_deadline)
        assert tail["operation"]["status"] == (
            "completed" if release_after_deadline else "pending")
        if not release_after_deadline:
            assert tail["diagnostic_join_elapsed_seconds"] > 0
    finally:
        release.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_unwritable_diagnostic_and_closed_stdout_keep_original_assertion(tmp_path, monkeypatch):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied", encoding="utf-8")
    diagnostic = FixtureDiagnostics("closed-stdout", root=blocked)
    closed_stdout = io.StringIO()
    closed_stdout.close()
    original = AssertionError("original fixture assertion")
    with pytest.raises(AssertionError) as caught, monkeypatch.context() as context:
        context.setattr(sys, "stdout", closed_stdout)
        try:
            raise original
        finally:
            diagnostic.record("original-error", error_code="original")
    assert caught.value is original


def test_attempt_log_requires_exact_binding_and_keeps_original_bounded_bytes(tmp_path):
    diagnostic = FixtureDiagnostics("attempt", root=tmp_path / "retained")
    operation = {"operation_id": "a" * 32, "attempt_id": "b" * 32}
    name = "local-training-" + "a" * 32 + "-" + "b" * 32 + ".log"
    original = b"prefix" * 2000 + b"\xff\r\nlast-error\n"
    (tmp_path / name).write_bytes(original)
    value = diagnostic.training_log(tmp_path, operation)
    assert value["tail_original"] == repr(original[-8192:])
    assert value["bytes"] == len(original) and value["truncated"] is True
    assert diagnostic.training_log(tmp_path, {**operation, "attempt_id": "../foreign"}) == {
        "status": "unbound"}
    assert operation_snapshot({"error": {"code": "original", "token": "private"}}) == {
        "error": {"code": "original"}}
    assert failure_snapshot({"exception_type": "builtins.OSError", "message": "private",
                             "origin": {"file": "child.py", "locals": "private"}}) == {
        "exception_type": "builtins.OSError", "origin": {"file": "child.py"}}


@pytest.mark.parametrize("outcome", ["timeout", "nonzero", "success"])
def test_bridge_phase_is_retained_after_class_cleanup_without_changing_build_cap(
    tmp_path, monkeypatch, outcome,
):
    import test_native_source3_real_store as owner

    monkeypatch.setattr(owner, "FixtureDiagnostics",
                        lambda name: FixtureDiagnostics(name, root=tmp_path / "retained"))
    monkeypatch.setattr(owner.shutil, "which", lambda _command: "synthetic-dotnet")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, b"9.0.synthetic\n", b"")
        assert kwargs["timeout"] == 60
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 60, output=b"Restore\r\nCoreCompile\n")
        if outcome == "nonzero":
            return subprocess.CompletedProcess(command, 1, b"CoreCompile\n", b"CS0000\n")
        output = Path(command[command.index("--output") + 1])
        (output / "STS2Connector.NativeLogical.OwnedCurrent.Bridge.dll").write_bytes(b"synthetic")
        return subprocess.CompletedProcess(command, 0, b"Build succeeded\n", b"")

    monkeypatch.setattr(owner.subprocess, "run", run)

    class Fixture(owner.RealStoreCollectionTests):
        pass

    try:
        if outcome == "success":
            Fixture.setUpClass()
        else:
            with pytest.raises(AssertionError):
                Fixture.setUpClass()
        temporary = Fixture.bridge_directory
    finally:
        Fixture.doClassCleanups()
    assert not temporary.exists()
    records = [json.loads(path.read_text(encoding="utf-8"))
               for path in sorted((tmp_path / "retained").rglob("*.json"))]
    phases = [record["phase"] for record in records]
    assert phases[:2] == ["sdk-probe-started", "bridge-build-started"]
    assert ("bridge-build-timeout" if outcome == "timeout" else "bridge-build-exited") in phases
    assert calls[1][1] == 60
    assert not any("-bl" in arg for arg in calls[1][0])
    if outcome == "timeout":
        assert records[-1]["stdout_original"] == repr(b"Restore\r\nCoreCompile\n")
        assert records[-1]["child_exit_observation"] == "unconfirmed"
