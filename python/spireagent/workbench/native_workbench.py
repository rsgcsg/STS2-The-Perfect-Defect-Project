"""Register this Workbench's local browser origin with the exact running STS2 Mod."""

from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

GAME_BRIDGE_URL = "http://127.0.0.1:15528"
WORKBENCH_OPEN_SCHEMA = "sts2.platform/workbench-open-1"
WORKBENCH_CLOSE_SCHEMA = "sts2.platform/workbench-close-1"
MAX_RESPONSE_BYTES = 16384
WORKBENCH_INSTANCE_ID = re.compile(r"[a-f0-9]{32}\Z")
LOCAL_WORKBENCH_URL = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/\Z")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


class NativeWorkbenchRegistrar:
    """Best-effort registration. It never launches a browser or a game."""

    address = GAME_BRIDGE_URL

    def __init__(self, *, opener: Any | None = None) -> None:
        self.opener = opener or build_opener(ProxyHandler({}), _NoRedirect())

    @staticmethod
    def _read_json(response: Any) -> dict[str, Any]:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("response_too_large")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate_json_key")
                value[key] = item
            return value

        value = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError("response_not_object")
        return value

    def _request(
        self, method: str, route: str, *, body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json", **(headers or {})}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(self.address + route, data=data, headers=headers, method=method)
        with self.opener.open(request, timeout=2) as response:
            return self._read_json(response)

    @staticmethod
    def _valid_url(value: object) -> bool:
        if not isinstance(value, str):
            return False
        match = LOCAL_WORKBENCH_URL.fullmatch(value)
        return match is not None and 1 <= int(match[1]) <= 65535

    @staticmethod
    def _valid_instance_id(value: object) -> bool:
        return isinstance(value, str) and WORKBENCH_INSTANCE_ID.fullmatch(value) is not None

    def register(self, url: object, instance_id: object) -> dict[str, str]:
        """Publish only an exact loopback Workbench root and opaque process ID."""
        if not self._valid_url(url) or not self._valid_instance_id(instance_id):
            return {"status": "unavailable", "reason": "invalid_workbench_identity"}
        registration_sent = False
        try:
            current = self._request("GET", "/v1/workbench/status")
            if (
                set(current)
                != {
                    "schema",
                    "status",
                    "runtime_instance_id",
                    "workbench_url",
                    "workbench_instance_id",
                }
                or current.get("schema") != "sts2.platform/workbench-open-status-1"
                or not isinstance(current.get("runtime_instance_id"), str)
                or not current["runtime_instance_id"]
                or current.get("status") not in {"registered", "unregistered"}
            ):
                return {"status": "unavailable", "reason": "game_identity_unavailable"}
            runtime_instance_id = current["runtime_instance_id"]
            expected_workbench_instance_id: str | None = None
            if current["status"] == "registered":
                current_instance = current.get("workbench_instance_id")
                current_url = current.get("workbench_url")
                if not self._valid_instance_id(current_instance) or not self._valid_url(
                    current_url
                ):
                    return {"status": "unavailable", "reason": "invalid_game_status"}
                if current_instance != instance_id or current_url != url:
                    return {"status": "conflict", "reason": "another_workbench_registered"}
                expected_workbench_instance_id = str(instance_id)
            elif (
                current.get("workbench_instance_id") is not None
                or current.get("workbench_url") is not None
            ):
                return {"status": "unavailable", "reason": "invalid_game_status"}
            body = {
                "schema": WORKBENCH_OPEN_SCHEMA,
                "workbench_url": url,
                "workbench_instance_id": instance_id,
                "runtime_instance_id": runtime_instance_id,
                "expected_workbench_instance_id": expected_workbench_instance_id,
            }
            registration_sent = True
            result = self._request("POST", "/v1/workbench/register", body=body)
            if (
                set(result) != {"schema", "status", "runtime_instance_id", "workbench_instance_id"}
                or result.get("schema") != WORKBENCH_OPEN_SCHEMA
                or result.get("status") != "registered"
                or result.get("runtime_instance_id") != runtime_instance_id
                or result.get("workbench_instance_id") != instance_id
            ):
                return {"status": "unavailable", "reason": "registration_rejected"}
            return {
                "status": "registered",
                "runtime_instance_id": runtime_instance_id,
                "workbench_instance_id": instance_id,
            }
        except HTTPError as error:
            reason = (
                "registration_rejected"
                if registration_sent and error.code in {400, 409}
                else "game_bridge_unavailable"
            )
            return {"status": "unavailable", "reason": reason}
        except (URLError, OSError, ValueError, TypeError):
            return {"status": "unavailable", "reason": "game_bridge_unavailable"}

    def register_native(self, url: str, instance_id: str, access: Any) -> dict[str, str]:
        from spireagent.json_boundary import BoundaryError
        from spireagent.workbench.developer_server import configuration_id
        from spireagent.workbench.native_workbench_access import (
            ACK_SCHEMA,
            BINDING_FIELDS,
            PAIR_SCHEMA,
            NativePair,
        )

        try:
            bootstrap = access.bootstrap()
            current = self._request("GET", "/v1/workbench/status")
            if (current.get("status") != "registered" or
                    current.get("workbench_url") != url or
                    current.get("workbench_instance_id") != instance_id):
                raise BoundaryError("native_workbench", "native_registration_changed")
            pair = access.current()
            config_id = configuration_id(access.app.config)
            if (pair is None or pair.runtime_instance_id != current.get("runtime_instance_id")
                    or pair.configuration_id != config_id or pair.expires_at <= time.time() + 60):
                pair = NativePair(current["runtime_instance_id"], instance_id, config_id,
                                  url, secrets.token_hex(16), int(time.time()) + 600)
            body = {"schema": PAIR_SCHEMA, **pair.to_dict(),
                    "signature": pair.sign(bootstrap.secret, "native-register-v1")}
            ack = self._request("POST", "/v1/workbench/native-register", body=body)
            if (set(ack) != {"schema", "signature", *BINDING_FIELDS}
                    or ack["schema"] != ACK_SCHEMA or
                    {key: ack[key] for key in BINDING_FIELDS} != pair.to_dict()
                    or not isinstance(ack["signature"], str) or
                    not hmac.compare_digest(ack["signature"],
                                            pair.sign(bootstrap.secret, "native-register-ack-v1"))):
                raise BoundaryError("native_workbench", "native_pair_ack_invalid")
            access.install(pair, self)
            return {"native_status": "paired"}
        except BoundaryError as error:
            return {"native_status": "unavailable", "native_reason": error.code}
        except (HTTPError, URLError, OSError, ValueError, TypeError, KeyError):
            return {"native_status": "unavailable", "native_reason": "native_pair_unavailable"}

    def unregister(self, url: object, instance_id: object) -> dict[str, str]:
        """Clear only this Workbench's exact registration on the current game."""
        if not self._valid_url(url) or not self._valid_instance_id(instance_id):
            return {"status": "unavailable", "reason": "invalid_workbench_identity"}
        try:
            current = self._request("GET", "/v1/workbench/status")
            if (
                set(current)
                != {
                    "schema",
                    "status",
                    "runtime_instance_id",
                    "workbench_url",
                    "workbench_instance_id",
                }
                or current.get("schema") != "sts2.platform/workbench-open-status-1"
                or not isinstance(current.get("runtime_instance_id"), str)
                or not current["runtime_instance_id"]
            ):
                return {"status": "unavailable", "reason": "game_identity_unavailable"}
            runtime_id = current["runtime_instance_id"]
            if current["status"] == "unregistered":
                if (
                    current.get("workbench_url") is not None
                    or current.get("workbench_instance_id") is not None
                ):
                    return {"status": "unavailable", "reason": "invalid_game_status"}
                return {"status": "unregistered", "runtime_instance_id": runtime_id}
            if (
                current["status"] != "registered"
                or current.get("workbench_instance_id") != instance_id
                or current.get("workbench_url") != url
            ):
                return {"status": "conflict", "reason": "another_workbench_registered"}
            result = self._request(
                "POST",
                "/v1/workbench/unregister",
                body={
                    "schema": WORKBENCH_CLOSE_SCHEMA,
                    "workbench_instance_id": instance_id,
                    "runtime_instance_id": runtime_id,
                },
            )
            if (
                set(result) != {"schema", "status", "runtime_instance_id", "workbench_instance_id"}
                or result.get("schema") != WORKBENCH_CLOSE_SCHEMA
                or result.get("status") != "unregistered"
                or result.get("runtime_instance_id") != runtime_id
                or result.get("workbench_instance_id") != instance_id
            ):
                return {"status": "unavailable", "reason": "unregistration_rejected"}
            return {"status": "unregistered", "runtime_instance_id": runtime_id}
        except HTTPError as error:
            reason = (
                "unregistration_rejected" if error.code in {400, 409} else "game_bridge_unavailable"
            )
            return {"status": "unavailable", "reason": reason}
        except (URLError, OSError, ValueError, TypeError):
            return {"status": "unavailable", "reason": "game_bridge_unavailable"}


class WorkbenchRegistrationLoop:
    """Retry only the fixed native registration/status routes for game start/restart."""

    def __init__(
        self,
        url: str,
        instance_id: str,
        *,
        registrar: NativeWorkbenchRegistrar | None = None,
        stop_event: threading.Event | None = None,
        wait: Callable[[float], bool] | None = None,
        access: Any | None = None,
    ) -> None:
        self.url = url
        self.instance_id = instance_id
        self.registrar = registrar or NativeWorkbenchRegistrar()
        self.access = access
        self._stop_event = stop_event or threading.Event()
        self._wait = wait or self._stop_event.wait
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_result: dict[str, str] = {"status": "not_started"}
        self._ever_registered = False

    @property
    def last_result(self) -> dict[str, str]:
        with self._lock:
            return dict(self._last_result)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="workbench-game-registration",
                daemon=True,
            )
            self._thread.start()

    def close(self, *, timeout: float = 4.5) -> None:
        self._stop_event.set()
        if self.access is not None:
            self.access.revoke()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
            if not thread.is_alive() and self._ever_registered:
                self.registrar.unregister(self.url, self.instance_id)

    def _run(self) -> None:
        delay = 10.0
        while not self._stop_event.is_set():
            result = self.registrar.register(self.url, self.instance_id)
            if result.get("status") == "registered" and self.access is not None:
                result = {**result, **self.registrar.register_native(
                    self.url, self.instance_id, self.access)}
            with self._lock:
                self._last_result = dict(result)
                if result.get("status") == "registered":
                    self._ever_registered = True
            if self._wait(delay):
                break
            delay = min(30.0, delay * 2.0)


def start_workbench_registration(url: str, instance_id: str, *,
                                 access: Any | None = None) -> WorkbenchRegistrationLoop:
    """Create/start the loop from Workbench ``serve`` when platform is configured."""
    loop = WorkbenchRegistrationLoop(url, instance_id, access=access)
    loop.start()
    return loop
