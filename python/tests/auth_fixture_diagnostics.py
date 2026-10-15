"""Test-only HTTP observations; never an authorization or transport adapter."""
from __future__ import annotations

import json
import sys
import threading
import uuid
from contextlib import suppress
from typing import Any
from urllib.error import HTTPError

from fixture_diagnostics import FixtureDiagnostics

HEADER = "X-Synthetic-Test-Request"
ROUTES = {
    "/", "/api/local-environment/native", "/api/local-environment/native/prepare",
    "/api/local-environment/native/finish", "/api/local-models/request",
    "/api/local-models/run-agent",
}


class AuthFixtureDiagnostics:
    def __init__(self, writer: FixtureDiagnostics | None = None) -> None:
        self.writer = writer if writer is not None else FixtureDiagnostics("native-entry-http")
        self.lock = threading.Lock()

    def record(self, stage: str, request_id: str, *, path: str = "", **fields: Any) -> None:
        # One lock covers sequence allocation and the complete write, including
        # client/server threads. Only enumerated primitive observations are kept.
        try:
            selected = {key: value for key, value in fields.items()
                        if key in {"method", "auth", "body_length", "status", "error_class",
                                   "errno", "winerror", "effects", "requests"}
                        and (value is None or type(value) in {str, int})}
            with self.lock:
                self.writer.record(stage, request_id=request_id,
                                   route=path if path in ROUTES else "other", **selected)
        except Exception:
            # No diagnostic failure can replace an original exception/result.
            pass

    def error(self, stage: str, request_id: str, path: str, error: BaseException) -> None:
        with suppress(Exception):
            self.record(stage, request_id, path=path, error_class=type(error).__name__,
                        errno=getattr(error, "errno", None),
                        winerror=getattr(error, "winerror", None))

    def observe_server(self, server: Any) -> None:
        original = server.RequestHandlerClass
        observer = self

        class Observed(original):
            def observation(self, stage: str, **fields: Any) -> None:
                try:
                    identity = self.headers.get(HEADER, "")
                    # Only our generated identity is retained, not arbitrary headers.
                    identity = uuid.UUID(hex=identity).hex if identity else "unassociated"
                    observer.record(stage, identity, path=self.path, **fields)
                except Exception:
                    pass

            def observation_error(self, error: BaseException) -> None:
                with suppress(Exception):
                    self.observation("server-handler-error", error_class=type(error).__name__,
                                     errno=getattr(error, "errno", None),
                                     winerror=getattr(error, "winerror", None))

            def do_GET(self) -> None:
                self.observation("server-handler-entered", method="GET")
                try:
                    super().do_GET()
                except BaseException as error:
                    self.observation_error(error)
                    raise
                finally:
                    self.observation("server-handler-exited", method="GET")

            def do_POST(self) -> None:
                self.observation("server-handler-entered", method="POST")
                try:
                    super().do_POST()
                except BaseException as error:
                    self.observation_error(error)
                    raise
                finally:
                    self.observation("server-handler-exited", method="POST")

            def respond(self, code: int, value: bytes,
                        content_type: str = "application/json") -> None:
                self.observation("server-response-started", status=code)
                super().respond(code, value, content_type)
                self.observation("server-response-written", status=code)

            def finish(self) -> None:
                self.observation("server-finish-started")
                try:
                    super().finish()
                finally:
                    self.observation("server-finish-exited")

        server.RequestHandlerClass = Observed
        original_error = server.handle_error

        def handle_error(request: Any, client_address: Any) -> None:
            error = sys.exc_info()[1]
            if error is not None:
                self.error("server-unassociated-error", "unassociated", "", error)
            original_error(request, client_address)

        server.handle_error = handle_error

    def exchange(self, opener: Any, request: Any, *, auth: str, body_length: int,
                 effects: list, requests: list) -> Any:
        identity = uuid.uuid4().hex
        request.add_header(HEADER, identity)
        path = request.selector
        self.record("client-open-started", identity, path=path, method=request.get_method(),
                    auth=auth if auth in {"browser", "bearer", "wrong-bearer", "none"} else "other",
                    body_length=body_length)
        try:
            with opener.open(request, timeout=3) as response:
                self.record("client-status-received", identity, path=path, status=response.status)
                result = json.load(response)
                self.record("client-body-decoded", identity, path=path)
                return result
        except BaseException as error:
            # Includes HTTPError without consuming/closing it before caller assertions.
            if isinstance(error, HTTPError):
                self.record("client-http-refusal", identity, path=path, status=error.code)
            self.error("client-open-or-read-error", identity, path, error)
            raise
        finally:
            self.record("client-exchange-ended", identity, path=path,
                        effects=len(effects), requests=len(requests))
