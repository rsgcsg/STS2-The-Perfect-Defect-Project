"""Workbench browser registration is a bounded, non-launching native handoff."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from spireagent.workbench import native_workbench
from spireagent.workbench.native_workbench import NativeWorkbenchRegistrar


@pytest.fixture
def game_bridge(monkeypatch: pytest.MonkeyPatch):
    observed = {
        "schema": "sts2.platform/workbench-open-status-1",
        "status": "unregistered",
        "runtime_instance_id": "game-current",
        "workbench_url": None,
        "workbench_instance_id": None,
    }
    calls: list[tuple[str, dict[str, str], dict[str, object] | None]] = []
    registration_result = {"status": "registered", "allow_replacement": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_value(self, value: dict[str, object], status: int = 200) -> None:
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            calls.append((self.path, dict(self.headers), None))
            assert self.path == "/v1/workbench/status"
            self.send_value(observed)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, dict(self.headers), body))
            assert self.path in {"/v1/workbench/register", "/v1/workbench/unregister"}
            if (self.path == "/v1/workbench/register"
                and observed["status"] == "registered"
                and body["workbench_instance_id"] != observed["workbench_instance_id"]
                and not registration_result["allow_replacement"]):
                self.send_value({"error": "workbench_instance_conflict"}, 409)
                return
            if (
                self.path == "/v1/workbench/register"
                and registration_result["status"] == "registered"
            ):
                observed.update(
                    status="registered",
                    workbench_url=body.get("workbench_url"),
                    workbench_instance_id=body.get("workbench_instance_id"),
                    runtime_instance_id=body.get("runtime_instance_id"),
                )
            elif self.path == "/v1/workbench/unregister":
                observed.update(
                    status="unregistered", workbench_url=None, workbench_instance_id=None
                )
                self.send_value(
                    {
                        "schema": native_workbench.WORKBENCH_CLOSE_SCHEMA,
                        "status": "unregistered",
                        "runtime_instance_id": body.get("runtime_instance_id"),
                        "workbench_instance_id": body.get("workbench_instance_id"),
                    }
                )
                return
            self.send_value(
                {
                    "schema": native_workbench.WORKBENCH_OPEN_SCHEMA,
                    "status": registration_result["status"],
                    "runtime_instance_id": body.get("runtime_instance_id"),
                    "workbench_instance_id": body.get("workbench_instance_id"),
                },
                200 if registration_result["status"] == "registered" else 409,
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        NativeWorkbenchRegistrar, "address", f"http://127.0.0.1:{server.server_port}"
    )
    try:
        yield observed, calls, registration_result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_registration_is_typed_bound_to_current_game_and_does_not_open_browser(game_bridge):
    _, calls, _ = game_bridge
    result = NativeWorkbenchRegistrar().register("http://127.0.0.1:8787/", "a" * 32)
    assert result == {
        "status": "registered",
        "runtime_instance_id": "game-current",
        "workbench_instance_id": "a" * 32,
    }
    assert [call[0] for call in calls] == ["/v1/workbench/status", "/v1/workbench/register"]
    assert calls[0][1].get("Origin") is None
    assert calls[1][1].get("Origin") is None
    assert calls[1][2] == {
        "schema": "sts2.platform/workbench-open-1",
        "workbench_url": "http://127.0.0.1:8787/",
        "workbench_instance_id": "a" * 32,
        "runtime_instance_id": "game-current",
        "expected_workbench_instance_id": None,
    }
    assert all("token" not in str(call[2]).lower() for call in calls if call[2])


@pytest.mark.parametrize(
    "url,instance_id",
    [
        ("http://127.0.0.1/", "a" * 32),
        ("http://127.0.0.1:0/", "a" * 32),
        ("http://127.0.0.1:65536/", "a" * 32),
        ("http://localhost:8787/", "a" * 32),
        ("http://127.0.0.1:8787/path", "a" * 32),
        ("http://127.0.0.1:8787/?token=x", "a" * 32),
        ("http://name@127.0.0.1:8787/", "a" * 32),
        ("http://127.0.0.1:8787/", "not-an-instance"),
    ],
)
def test_invalid_origin_or_instance_is_rejected_before_contact(game_bridge, url, instance_id):
    _, calls, _ = game_bridge
    result = NativeWorkbenchRegistrar().register(url, instance_id)
    assert result == {"status": "unavailable", "reason": "invalid_workbench_identity"}
    assert calls == []


def test_missing_or_malformed_game_identity_never_registers(game_bridge):
    observed, calls, _ = game_bridge
    observed["runtime_instance_id"] = ""
    result = NativeWorkbenchRegistrar().register("http://127.0.0.1:8787/", "b" * 32)
    assert result == {"status": "unavailable", "reason": "game_identity_unavailable"}
    assert [call[0] for call in calls] == ["/v1/workbench/status"]


def test_mod_registration_rejection_is_reported_without_retry(game_bridge):
    _, calls, result = game_bridge
    result["status"] = "rejected"
    registered = NativeWorkbenchRegistrar().register("http://127.0.0.1:8787/", "c" * 32)
    assert registered == {"status": "unavailable", "reason": "registration_rejected"}
    assert [call[0] for call in calls] == ["/v1/workbench/status", "/v1/workbench/register"]


def test_different_live_workbench_registration_is_never_stolen(game_bridge):
    observed, calls, _ = game_bridge
    observed.update(
        status="registered",
        workbench_url="http://127.0.0.1:9999/",
        workbench_instance_id="d" * 32,
    )
    result = NativeWorkbenchRegistrar().register("http://127.0.0.1:8787/", "e" * 32)
    assert result == {"status": "conflict", "reason": "another_workbench_registered"}
    assert [call[0] for call in calls] == ["/v1/workbench/status", "/v1/workbench/register"]
    assert calls[-1][2]["expected_workbench_instance_id"] == "d" * 32
    assert observed["workbench_instance_id"] == "d" * 32


def test_stale_workbench_can_be_replaced_by_exact_compare_and_set(game_bridge):
    observed, calls, result = game_bridge
    observed.update(status="registered", workbench_url="http://127.0.0.1:9999/",
                    workbench_instance_id="d" * 32)
    result["allow_replacement"] = True
    registered = NativeWorkbenchRegistrar().register("http://127.0.0.1:8787/", "e" * 32)
    assert registered["status"] == "registered"
    assert calls[-1][2]["expected_workbench_instance_id"] == "d" * 32
    assert observed["workbench_instance_id"] == "e" * 32


def test_same_workbench_instance_refresh_uses_compare_and_set_id(game_bridge):
    observed, calls, _ = game_bridge
    observed.update(
        status="registered",
        workbench_url="http://127.0.0.1:8787/",
        workbench_instance_id="f" * 32,
    )
    result = NativeWorkbenchRegistrar().register("http://127.0.0.1:8787/", "f" * 32)
    assert result["status"] == "registered"
    assert calls[1][2]["expected_workbench_instance_id"] == "f" * 32


def test_game_restart_clears_registration_and_same_workbench_binds_new_instance(game_bridge):
    observed, calls, _ = game_bridge
    first = NativeWorkbenchRegistrar().register("http://127.0.0.1:8787/", "1" * 32)
    assert first["runtime_instance_id"] == "game-current"
    observed.update(
        status="unregistered",
        runtime_instance_id="game-restarted",
        workbench_url=None,
        workbench_instance_id=None,
    )
    second = NativeWorkbenchRegistrar().register("http://127.0.0.1:8787/", "1" * 32)
    assert second["runtime_instance_id"] == "game-restarted"
    assert calls[-1][2]["expected_workbench_instance_id"] is None


def test_unregister_clears_only_matching_owner(game_bridge):
    observed, calls, _ = game_bridge
    NativeWorkbenchRegistrar().register("http://127.0.0.1:8787/", "4" * 32)
    result = NativeWorkbenchRegistrar().unregister("http://127.0.0.1:8787/", "4" * 32)
    assert result == {"status": "unregistered", "runtime_instance_id": "game-current"}
    assert observed["status"] == "unregistered"
    assert calls[-1][0] == "/v1/workbench/unregister"
    assert calls[-1][2] == {
        "schema": native_workbench.WORKBENCH_CLOSE_SCHEMA,
        "workbench_instance_id": "4" * 32,
        "runtime_instance_id": "game-current",
    }


def test_unregister_refuses_to_clear_another_workbench(game_bridge):
    observed, calls, _ = game_bridge
    observed.update(
        status="registered",
        workbench_url="http://127.0.0.1:9999/",
        workbench_instance_id="5" * 32,
    )
    result = NativeWorkbenchRegistrar().unregister("http://127.0.0.1:8787/", "6" * 32)
    assert result == {"status": "conflict", "reason": "another_workbench_registered"}
    assert [call[0] for call in calls] == ["/v1/workbench/status"]


def test_registration_loop_retries_with_bounded_backoff_until_game_is_available():
    stop_event = threading.Event()
    delays: list[float] = []
    calls: list[dict[str, str]] = []
    completed = threading.Event()

    class Registrar:
        def register(self, url: str, instance_id: str) -> dict[str, str]:
            call = len(calls) + 1
            result = (
                {"status": "unavailable", "reason": "game_bridge_unavailable"}
                if call == 1
                else {
                    "status": "registered",
                    "runtime_instance_id": "game-a" if call == 2 else "game-b",
                    "workbench_instance_id": instance_id,
                }
            )
            calls.append(result)
            if call == 3:
                completed.set()
            return result

        def unregister(self, url: str, instance_id: str) -> dict[str, str]:
            calls.append({"status": "unregistered", "workbench_instance_id": instance_id})
            return {"status": "unregistered"}

    def wait(delay: float) -> bool:
        delays.append(delay)
        return stop_event.wait(0) or len(calls) >= 3

    loop = native_workbench.WorkbenchRegistrationLoop(
        "http://127.0.0.1:8787/", "2" * 32, registrar=Registrar(), stop_event=stop_event, wait=wait
    )
    loop.start()
    assert completed.wait(1)
    loop.close(timeout=1)
    assert [call["status"] for call in calls] == [
        "unavailable",
        "registered",
        "registered",
        "unregistered",
    ]
    assert [call["runtime_instance_id"] for call in calls[1:3]] == ["game-a", "game-b"]
    assert delays == [10.0, 20.0, 30.0]
    assert loop.last_result["runtime_instance_id"] == "game-b"


def test_registration_loop_close_interrupts_backoff():
    stop_event = threading.Event()
    waiting = threading.Event()
    calls: list[str] = []

    class Registrar:
        def register(self, url: str, instance_id: str) -> dict[str, str]:
            calls.append(instance_id)
            return {"status": "unavailable", "reason": "game_bridge_unavailable"}

        def unregister(self, url: str, instance_id: str) -> dict[str, str]:
            raise AssertionError("must not unregister when this loop never registered")

    def wait(_: float) -> bool:
        waiting.set()
        return stop_event.wait()

    loop = native_workbench.WorkbenchRegistrationLoop(
        "http://127.0.0.1:8787/", "3" * 32, registrar=Registrar(), stop_event=stop_event, wait=wait
    )
    loop.start()
    assert waiting.wait(1)
    loop.close(timeout=1)
    assert calls == ["3" * 32]
    assert loop.last_result["status"] == "unavailable"
