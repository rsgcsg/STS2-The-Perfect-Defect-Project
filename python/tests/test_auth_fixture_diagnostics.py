"""Observe real HTTP rejection without replacing protocol results or failures."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from auth_fixture_diagnostics import AuthFixtureDiagnostics


class Writer:
    def __init__(self):
        self.records = []
        self.active = 0

    def record(self, stage, **fields):
        self.active += 1
        assert self.active == 1
        time.sleep(0.001)
        self.records.append((stage, fields))
        self.active -= 1


@pytest.mark.parametrize("route, timeout, fixture", [
    ("/api/local-models/run-agent", 3, "native-entry-http"),
    ("/api/local-recordings/import/native-agent", 5, "native-agent-data-http"),
])
def test_actual_http_denial_body_and_original_exception_are_preserved(route, timeout, fixture):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, code, value, content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            self.wfile.write(value)

        def do_POST(self):
            self.respond(403, b'{"error":"synthetic-denied"}')

    writer = Writer()
    observer = AuthFixtureDiagnostics(writer, fixture=fixture)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    observer.observe_server(server)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}" + route,
                          data=b'{"secret":"never-record"}',
                          headers={"Authorization": "Bearer never-record"})
        with pytest.raises(HTTPError) as caught:
            observer.exchange(build_opener(ProxyHandler({})), request,
                              auth="none", body_length=len(request.data), timeout=timeout)
        assert caught.value.code == 403
        assert caught.value.read() == b'{"error":"synthetic-denied"}'
        caught.value.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
    records = writer.records
    assert "never-record" not in json.dumps(records)
    ids = {fields["request_id"] for _stage, fields in records}
    assert len(ids) == 1
    assert all("effects" not in fields and "requests" not in fields
               for _stage, fields in records)
    assert any(fields.get("timeout_seconds") == timeout for _stage, fields in records)
    assert all(fields["route"] == route for _stage, fields in records)
    assert any(stage == "server-response-written" and fields["status"] == 403
               for stage, fields in records)
    assert any(stage == "client-open-or-read-error" and fields["error_class"] == "HTTPError"
               for stage, fields in records)


@pytest.mark.parametrize("timeout", [3, 5])
@pytest.mark.parametrize("result", ["success", "abort", "decode"])
def test_diagnostic_failure_preserves_result_and_exact_original_exception(result, timeout):
    class Broken:
        def record(self, *_args, **_fields):
            raise ValueError("diagnostic failed")

    original = ConnectionAbortedError(53, "synthetic original")

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"accepted":true}' if result == "success" else b"invalid-json"

    class Opener:
        def open(self, request, **options):
            assert request.data == b"original-body"
            assert options == {"timeout": timeout}
            assert request.get_header("Authorization") == "original-token"
            if result == "abort":
                raise original
            return Response()

    observer = AuthFixtureDiagnostics(
        Broken(), fixture="native-entry-http" if timeout == 3 else "native-agent-data-http")
    request = Request("http://127.0.0.1/api/local-models/run-agent", data=b"original-body",
                      headers={"Authorization": "original-token"})
    if result == "success":
        value = observer.exchange(Opener(), request, auth="none", body_length=13,
                                  effects=[], requests=[], timeout=timeout,
                                  include_status=timeout == 5)
        assert value == ((200, {"accepted": True}) if timeout == 5 else {"accepted": True})
    elif result == "abort":
        with pytest.raises(ConnectionAbortedError) as caught:
            observer.exchange(Opener(), request, auth="none", body_length=13,
                              effects=[], requests=[], timeout=timeout)
        assert caught.value is original
    else:
        with pytest.raises(json.JSONDecodeError):
            observer.exchange(Opener(), request, auth="none", body_length=13,
                              effects=[], requests=[], timeout=timeout)


def test_concurrent_client_server_observations_serialize_writer():
    writer = Writer()
    observer = AuthFixtureDiagnostics(writer)
    threads = [threading.Thread(target=lambda: [observer.record("stage", "synthetic", status=403)
                                                for _ in range(20)]) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    assert all(not thread.is_alive() for thread in threads)
    assert len(writer.records) == 80


def test_server_observation_failure_preserves_original_handler_exception():
    original = OSError("original handler send failure")

    class Handler:
        def do_POST(self):
            raise original

    class Broken:
        def record(self, *_args, **_fields):
            raise ValueError("diagnostic write failure")

    class Server:
        RequestHandlerClass = Handler

        def handle_error(self, *_args):
            pass

    server = Server()
    observer = AuthFixtureDiagnostics(Broken())
    observer.observe_server(server)
    handler = server.RequestHandlerClass()
    handler.headers = {"X-Synthetic-Test-Request": "a" * 32}
    handler.path = "/api/local-models/run-agent"
    with pytest.raises(OSError) as caught:
        handler.do_POST()
    assert caught.value is original


@pytest.mark.parametrize("fixture, wrong_timeout", [
    ("native-entry-http", 5), ("native-agent-data-http", 3),
    ("native-entry-http", 30), ("native-agent-data-http", True),
])
def test_fixture_budget_cannot_be_widened_or_substituted(fixture, wrong_timeout):
    class MustNotOpen:
        def open(self, *_args, **_kwargs):
            pytest.fail("invalid diagnostic configuration cannot open a request")

    observer = AuthFixtureDiagnostics(Writer(), fixture=fixture)
    with pytest.raises(ValueError, match="unsupported fixture HTTP timeout"):
        observer.exchange(MustNotOpen(), Request("http://127.0.0.1/"), auth="none",
                          body_length=0, timeout=wrong_timeout)
