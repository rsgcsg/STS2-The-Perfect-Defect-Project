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
    thread_snapshot,
)


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
def test_product_join_failure_stays_failed_when_diagnostics_observe_late_completion(
    tmp_path, monkeypatch, release_after_deadline,
):
    import test_native_agent_product_data as owner

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
