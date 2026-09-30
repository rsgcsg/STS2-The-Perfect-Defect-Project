"""Local account BFF. Browser pages never receive Hub session or device credentials."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import stat
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request, build_opener

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.hub_client import NoRedirect


def deadline(value: object) -> float:
    if not isinstance(value, str):
        raise BoundaryError("identity", "invalid_hub_deadline")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.timestamp()
    except ValueError:
        raise BoundaryError("identity", "invalid_hub_deadline") from None


def private_read(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size > 32768
        or (os.name != "nt" and info.st_mode & 0o077)
    ):
        raise BoundaryError("identity", "private_credential_file_required")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise BoundaryError("identity", "invalid_local_identity")
    return value


class LocalIdentity:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.path = config.state_dir / "account.json"
        self.device_path = config.state_dir / "device.json"
        self.flow_path = config.state_dir / "login-flow.json"
        self.lock = threading.RLock()
        self.opener = build_opener(NoRedirect())
        self.csrf = secrets.token_urlsafe(32)
        self.cookie = secrets.token_urlsafe(32)
        # Cookies share a host across ports. Scope the name to this durable local
        # project; the value still rotates with each running Workbench instance.
        self.cookie_name = (
            "spireagent_local_"
            + hashlib.sha256(str(config.state_dir.resolve()).encode()).hexdigest()
        )

    def request(
        self, route: str, *, body: dict[str, Any] | None = None, token: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.config.hub_url:
            raise BoundaryError("identity", "hub_not_configured")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            self.config.hub_url + route,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            # Member data requests can verify multiple remote immutable manifests.
            # Stay below the browser's 15s read / 25s mutation deadlines; login
            # polling retains its short timeout. A background transfer may provide
            # its own budget. Never retry a submitted mutation.
            member = route.startswith("/v1/identity/member/")
            request_timeout = (20 if body is not None else 10) if member else 4
            with self.opener.open(
                request, timeout=request_timeout if timeout is None else timeout
            ) as response:
                raw = response.read(1048577)
            if len(raw) > 1048576:
                raise ValueError
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except HTTPError as error:
            raise BoundaryError("identity", "http_" + str(error.code)) from None
        except (URLError, OSError, ValueError):
            raise BoundaryError(
                "identity", "request_unknown" if body is not None else "hub_unavailable"
            ) from None

    def flow(self) -> dict[str, Any]:
        value = private_read(self.flow_path)
        if value and value.get("hub_url") != self.config.hub_url:
            raise BoundaryError("identity", "flow_hub_mismatch")
        return value

    def device(self) -> dict[str, Any]:
        value = private_read(self.device_path)
        if value and value.get("hub_url") != self.config.hub_url:
            raise BoundaryError("identity", "device_hub_mismatch")
        return value

    def device_token(self) -> str:
        value = self.device()
        return str(value.get("token") or os.environ.get("STPD_HUB_TOKEN") or "")

    def session(self, *, include_expired: bool = False) -> dict[str, Any]:
        value = private_read(self.path)
        if value and value.get("hub_url") != self.config.hub_url:
            raise BoundaryError("identity", "account_hub_mismatch")
        return value if include_expired or value.get("expires_at", 0) > time.time() else {}

    def status(self) -> dict[str, Any]:
        with self.lock:
            saved = self.session(include_expired=True)
            session = saved if saved.get("expires_at", 0) > time.time() else {}
            result: dict[str, Any] = {
                "status": "signed_out",
                "csrf_token": self.csrf,
                "device_name": self.device().get("name") or platform.node()[:80] or "我的电脑",
                "device_id": self.device().get("device_id"),
                "device_credential_present": bool(self.device_token()),
                "delivery_configured": self.config.delivery_config is not None,
                "hub_configured": bool(self.config.hub_url),
                "upload_independent_of_login": True,
            }
            # Older account records may contain an email. It is only a local
            # historical hint, never inferred from the device name or an authority.
            email = saved.get("email")
            if (isinstance(email, str) and len(email) <= 254
                    and re.fullmatch(r"[^\s@\x00-\x1f]+@[^\s@\x00-\x1f]+", email)):
                result["previous_account_email"] = email
            flow = self.flow()
            if flow.get("expires_at", 0) > time.time():
                result["flow"] = {
                    key: flow[key] for key in ("flow_id", "approval_url", "user_code", "expires_at")
                }
        if session:
            try:
                observed = self.request("/v1/identity/me", token=session["session_token"])
                with self.lock:
                    if self.session().get("session_token") != session["session_token"]:
                        raise BoundaryError("identity", "account_changed")
                    result.update(observed, status="signed_in")
            except BoundaryError as error:
                result.update(status="reconnect_required", error=error.code)
        return result

    def begin(self, name: object) -> dict[str, Any]:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise BoundaryError("identity", "device_name_required")
        with self.lock:
            previous = self.flow()
            if previous.get("expires_at", 0) > time.time():
                return {
                    key: previous[key]
                    for key in ("flow_id", "approval_url", "user_code", "expires_at")
                }
            body: dict[str, Any] = {
                "client_secret": secrets.token_urlsafe(32),
                "device_name": name.strip(),
            }
            device_token = self.device_token()
            if device_token:
                observed = self.request("/v1/identity/device", token=device_token)
                body.update(device_id=observed["device_id"], device_token=device_token)
            value = self.request("/v1/identity/flows", body=body)
            if not re.fullmatch(r"[a-f0-9]{32}", value.get("flow_id", "")):
                raise BoundaryError("identity", "invalid_flow_response")
            expected = "/app/?view=connect&flow=" + value["flow_id"]
            if value.get("approval_path") != expected:
                raise BoundaryError("identity", "invalid_approval_path")
            public = {key: value[key] for key in ("flow_id", "user_code", "expires_at")}
            public["expires_at"] = deadline(value["expires_at"])
            public["approval_url"] = self.config.hub_url + expected
            atomic_json(
                self.flow_path,
                {
                    **public,
                    "hub_url": self.config.hub_url,
                    "client_secret": body["client_secret"],
                    "name": name.strip(),
                    "existing_device": body.get("device_id"),
                },
            )
            return public

    def poll(self) -> dict[str, Any]:
        with self.lock:
            flow = self.flow()
            if not flow or flow.get("expires_at", 0) <= time.time():
                self.flow_path.unlink(missing_ok=True)
                return {"status": "expired"}
            route = "/v1/identity/flows/" + flow["flow_id"]
            body = {"client_secret": flow["client_secret"]}
            if flow.get("published"):
                self.request(route + "/ack", body=body)
                self.flow_path.unlink(missing_ok=True)
                return {"status": "approved"}
            value = self.request(route + "/poll", body=body)
            if value.get("status") != "approved":
                status = value.get("status", "pending")
                if status in {"denied", "expired"}:
                    self.flow_path.unlink(missing_ok=True)
                return {"status": status}
            device = value["device"]
            existing = self.device()
            expected = existing.get("device_id") or flow.get("existing_device")
            if expected and device["device_id"] != expected:
                raise BoundaryError("identity", "device_identity_changed")
            token = device.get("token") or self.device_token()
            if not token:
                raise BoundaryError("identity", "device_grant_missing")
            # Durable local publication precedes acknowledgement; no queue/config/owner rewrite.
            atomic_json(
                self.device_path,
                {
                    "hub_url": self.config.hub_url,
                    "device_id": device["device_id"],
                    "token": token,
                    "name": flow["name"],
                },
            )
            atomic_json(
                self.path,
                {
                    "hub_url": self.config.hub_url,
                    "session_token": value["session_token"],
                    "expires_at": deadline(value["expires_at"]),
                },
            )
            atomic_json(self.flow_path, {**flow, "published": True})
            self.request(route + "/ack", body=body)
            self.flow_path.unlink(missing_ok=True)
            return {"status": "approved"}

    def logout(self) -> dict[str, Any]:
        with self.lock:
            session = self.session()
            revoked = False
            try:
                if session:
                    self.request("/v1/identity/logout", body={}, token=session["session_token"])
                    revoked = True
            except BoundaryError:
                pass
            finally:
                self.path.unlink(missing_ok=True)
                self.flow_path.unlink(missing_ok=True)
            return {
                "status": "signed_out",
                "remote_revoked": revoked,
                "device_authorization_retained": True,
            }

    def read(self, route: str) -> dict[str, Any]:
        path, _, query = route.partition("?")
        if not re.fullmatch(
            r"(?:overview|system|collections|datasets|models|jobs|statistics|training|evaluations|analyses)(?:/[a-f0-9]{32,64})?",
            path,
        ):
            raise BoundaryError("identity", "invalid_console_route")
        values = parse_qs(query, strict_parsing=True, max_num_fields=7)
        if (set(values) - {"limit", "offset", "device", "q", "from", "to", "selectable", "archived"}
                or ("q" in values and path != "datasets")
                or ("archived" in values
                    and path not in {"training", "models", "evaluations", "analyses"})
                or (set(values) & {"from", "to", "selectable"} and path != "collections")
                or any(len(v) != 1 for v in values.values())):
            raise BoundaryError("identity", "invalid_console_query")
        with self.lock:
            session = self.session()
            if not session:
                raise BoundaryError("identity", "sign_in_required")
        result = self.request("/v1/identity/console/" + route, token=session["session_token"])
        with self.lock:
            if self.session().get("session_token") != session["session_token"]:
                raise BoundaryError("identity", "account_changed")
        return result

    def replace_credential(self, source: Path) -> dict[str, Any]:
        """Explicit stopped-workbench recovery. Never change the logical device or campaign."""
        with self.lock:
            old = self.device()
            replacement = private_read(source)
            if (
                not old
                or replacement.get("hub_url") != self.config.hub_url
                or replacement.get("device_id") != old.get("device_id")
                or not isinstance(replacement.get("token"), str)
            ):
                raise BoundaryError("identity", "same_device_replacement_required")
            observed = self.request("/v1/identity/device", token=replacement["token"])
            if observed.get("device_id") != old["device_id"]:
                raise BoundaryError("identity", "device_identity_changed")
            atomic_json(self.device_path, {**old, "token": replacement["token"]})
            return {
                "status": "credential_replaced",
                "device_id": old["device_id"],
                "outbox_changed": False,
                "next": "open workbench; resume uploads",
            }
