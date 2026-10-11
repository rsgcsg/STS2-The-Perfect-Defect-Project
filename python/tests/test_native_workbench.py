"""Workbench browser registration is a bounded, non-launching native handoff."""

from __future__ import annotations

import contextlib
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
    registration_result = {"status": "registered"}

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
    assert [call[0] for call in calls] == ["/v1/workbench/status"]


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

@pytest.fixture
def signed_bridge(tmp_path, monkeypatch):
    """Real registrar/Application HTTP over a strictly signed game-protocol fixture."""
    import hmac

    from test_native_workbench_access import paired_app

    from spireagent.workbench.developer import atomic_json
    from spireagent.workbench.developer_server import configuration_id, create_server
    from spireagent.workbench.native_workbench_access import (
        ACK_SCHEMA,
        BINDING_FIELDS,
        CLOSE_SCHEMA,
        CURRENT_SCHEMA,
        NativePair,
        NativeWorkbenchAccess,
    )

    app, _, _selected, secret, _ = paired_app(tmp_path, monkeypatch)
    app.native_access = NativeWorkbenchAccess(app)
    backend = create_server(app)
    backend_thread = threading.Thread(target=backend.serve_forever, daemon=True)
    backend_thread.start()
    url = f"http://127.0.0.1:{backend.server_port}/"
    atomic_json(app.config.state_dir / "runtime.json", {"instance_id": app.instance_id,
        "configuration_id": configuration_id(app.config), "port": backend.server_port})
    legacy = {"schema": "sts2.platform/workbench-open-status-1", "status": "registered",
        "runtime_instance_id": "fixture-current-game", "workbench_instance_id": "f" * 32,
        "workbench_url": "http://127.0.0.1:23456/"}
    before = dict(legacy)
    lock = threading.RLock()
    accepted, release, close_seen = threading.Event(), threading.Event(), threading.Event()
    release.set()
    state = {"current": None, "retired": set(), "drop_ack": False}
    calls = []

    def context(pair):
        return (pair.runtime_instance_id, pair.workbench_instance_id,
                pair.configuration_id, pair.workbench_url)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value, status=200):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(raw)

        def do_GET(self):
            calls.append((self.path, None))
            if self.path == "/v1/workbench/status":
                self.reply(legacy)
                return
            assert self.path == "/v1/workbench/native-status"
            with lock:
                pair = state["current"]
                if pair is None or any(self.headers.get(k) != v
                                       for k, v in pair.headers(secret).items()):
                    self.reply({"error": "native_pair_changed"}, 409)
                    return
                self.reply({"schema": CURRENT_SCHEMA, **pair.to_dict(),
                            "signature": pair.sign(secret, "native-current-v1")})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            assert self.headers.get("Origin") is None and self.headers.get("Cookie") is None
            if self.path == "/v1/workbench/native-register":
                assert set(body) == {"schema", "signature", *BINDING_FIELDS}
                pair = NativePair.from_dict({k: body[k] for k in BINDING_FIELDS})
                assert body["schema"] == "sts2.platform/native-workbench-pair-1"
                assert hmac.compare_digest(
                    body["signature"], pair.sign(secret, "native-register-v1"))
                with lock:
                    if context(pair) in state["retired"]:
                        self.reply({"error": "native_context_retired"}, 409)
                        return
                    current = state["current"]
                    assert current is None or context(current) == context(pair)
                    state["current"] = pair
                accepted.set()
                assert release.wait(3)
                if state["drop_ack"]:
                    state["drop_ack"] = False
                    self.close_connection = True
                    return
                self.reply({"schema": ACK_SCHEMA, **pair.to_dict(),
                            "signature": pair.sign(secret, "native-register-ack-v1")})
                return
            assert self.path == "/v1/workbench/native-unregister"
            pair = NativePair.from_dict(body)
            assert all(self.headers.get(k) == v for k, v in pair.headers(secret).items())
            with lock:
                current = state["current"]
                if current is not None and current != pair:
                    self.reply({"error": "native_pair_conflict"}, 409)
                    return
                state["current"] = None
                state["retired"].add(context(pair))
            close_seen.set()
            self.reply({"schema": CLOSE_SCHEMA, "status": "closed" if current else "already_closed",
                        "binding": pair.to_dict()})

    game = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    game_thread = threading.Thread(target=game.serve_forever, daemon=True)
    game_thread.start()
    monkeypatch.setattr(NativeWorkbenchRegistrar, "address", f"http://127.0.0.1:{game.server_port}")
    try:
        yield app, url, secret, state, calls, accepted, release, close_seen, legacy, before
    finally:
        release.set()
        backend.shutdown()
        backend.server_close()
        backend_thread.join(timeout=2)
        game.shutdown()
        game.server_close()
        game_thread.join(timeout=2)
        app.close()


def test_real_registrar_pairs_beside_foreign_legacy_and_closes_only_its_signed_peer(signed_bridge):
    from urllib.request import Request, urlopen

    app, url, secret, state, calls, _, _, _, legacy, before = signed_bridge
    registrar = NativeWorkbenchRegistrar()
    assert registrar.register(url, app.instance_id)["status"] == "conflict"
    assert registrar.register_native(
        url, app.instance_id, app.native_access) == {"native_status": "paired"}
    pair = app.native_access.current()
    # Real Application access validates its own saved/runtime configuration and
    # confirms the actual registrar peer before projecting this native view.
    request = Request(url + "api/native-workbench/v1/view?page=play", headers=pair.headers(secret))
    with urlopen(request, timeout=3) as response:
        assert json.load(response)["binding"] == pair.to_dict()
    loop = native_workbench.WorkbenchRegistrationLoop(url, app.instance_id,
        registrar=registrar, access=app.native_access)
    loop.close()
    assert loop.cleanup_result["status"] == "confirmed"
    assert state["current"] is None and legacy == before
    assert not any(path in {"/v1/workbench/register", "/v1/workbench/unregister"}
                   for path, _ in calls)


def test_lost_ack_reuses_same_candidate_then_exact_close_preserves_foreign_legacy(signed_bridge):
    app, url, _, state, calls, _, _, _, legacy, before = signed_bridge
    registrar = NativeWorkbenchRegistrar()
    state["drop_ack"] = True
    assert registrar.register_native(
        url, app.instance_id, app.native_access)["native_status"] == "unavailable"
    assert app.native_access.current() is None and state["current"] is not None
    assert registrar.register_native(
        url, app.instance_id, app.native_access)["native_status"] == "paired"
    registrations = [body for path, body in calls if path == "/v1/workbench/native-register"]
    assert len(registrations) == 2 and registrations[0] == registrations[1]
    loop = native_workbench.WorkbenchRegistrationLoop(url, app.instance_id,
        registrar=registrar, access=app.native_access)
    loop.close()
    assert loop.cleanup_result["status"] == "confirmed" and legacy == before


def test_late_ack_after_close_and_join_timeout_cannot_reinstall_and_closes_pending_candidate(
    signed_bridge,
):
    app, url, _, state, calls, accepted, release, close_seen, legacy, before = signed_bridge
    release.clear()
    loop = native_workbench.WorkbenchRegistrationLoop(
        url, app.instance_id, access=app.native_access)
    loop.start()
    assert accepted.wait(2)
    loop.close(timeout=0.01)
    assert app.native_access.current() is None
    assert loop.cleanup_result["status"] == "unconfirmed"
    release.set()
    assert close_seen.wait(3)
    loop._thread.join(timeout=3)
    assert not loop._thread.is_alive() and app.native_access.current() is None
    assert loop.last_result["native_reason"] == "native_lifecycle_closed"
    assert loop.cleanup_result["status"] == "confirmed"
    assert loop.cleanup_result["candidate_count"] == 1 and legacy == before
    assert state["current"] is None
    assert len([path for path, _ in calls if path == "/v1/workbench/native-unregister"]) == 1


def test_shutdown_snapshots_current_and_possibly_accepted_pending_renewal(signed_bridge):
    import time

    from spireagent.workbench.native_workbench_access import NativePair

    app, url, secret, state, _, accepted, release, close_seen, _, _ = signed_bridge
    registrar = NativeWorkbenchRegistrar()
    pair = NativePair("fixture-current-game", app.instance_id,
        native_workbench_configuration(app), url, "1" * 32, int(time.time()) + 50)
    registrar._request("POST", "/v1/workbench/native-register", body={"schema":
        "sts2.platform/native-workbench-pair-1", **pair.to_dict(),
        "signature": pair.sign(secret, "native-register-v1")})
    app.native_access.install(pair, registrar)
    accepted.clear()
    release.clear()
    loop = native_workbench.WorkbenchRegistrationLoop(url, app.instance_id,
        registrar=registrar, access=app.native_access)
    loop.start()
    assert accepted.wait(2)
    assert state["current"] != pair
    loop.close(timeout=0.01)
    release.set()
    assert close_seen.wait(3)
    loop._thread.join(timeout=3)
    assert loop.cleanup_result["status"] == "confirmed"
    assert loop.cleanup_result["candidate_count"] == 2
    assert state["current"] is None and app.native_access.current() is None


def native_workbench_configuration(app):
    from spireagent.workbench.developer_server import configuration_id
    return configuration_id(app.config)


def test_signed_registrar_refuses_arbitrary_callback_port_before_signing_post(signed_bridge):
    app, url, _secret, _state, calls, *_ = signed_bridge
    registrar = NativeWorkbenchRegistrar()
    wrong = "http://127.0.0.1:1/" if url != "http://127.0.0.1:1/" else "http://127.0.0.1:2/"
    result = registrar.register_native(wrong, app.instance_id, app.native_access)
    assert result == {"native_status": "unavailable", "native_reason": "native_workbench_changed"}
    assert [path for path, _ in calls] == ["/v1/workbench/status"]
    assert app.native_access.current() is None
