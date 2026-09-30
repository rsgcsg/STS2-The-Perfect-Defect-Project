"""Attach to one Host-owned Managed service without owning its native child."""

from __future__ import annotations

from dataclasses import dataclass, field
from http.client import HTTPException
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


ATTACHMENT_SCHEMA = "sts2.host-runtime/managed-service-attachment-1"
READY_SCHEMA = "sts2.host-runtime/managed-service-ready-1"
RESULT_SCHEMA = "sts2.host-runtime/managed-service-result-1"
ERROR_SCHEMA = "sts2.host-runtime/managed-service-error-1"
# Only explicit rejections that the Host emits without offering this command
# to native mutation authority can settle an offered POST as not applied.
KNOWN_POST_REJECTIONS = frozenset({
    "managed_service_closing", "managed_service_host_not_allowed",
    "managed_service_origin_not_allowed", "managed_service_unauthorized",
    "managed_service_manager_required", "managed_service_not_found",
    "managed_service_instance_mismatch", "managed_service_json_required",
    "managed_service_command_not_allowed", "managed_service_control_required",
    "managed_service_episode_precondition_required",
    "managed_service_recovery_precondition_required",
    "managed_service_control_epoch_mismatch",
    "managed_service_close_body_invalid", "managed_service_request_id_required",
    "managed_service_body_too_large", "managed_service_body_required",
    "managed_service_body_must_be_object", "driver_closed",
    "managed_episode_unavailable_reset_required", "managed_control_held",
    "managed_control_intent_stale", "managed_control_not_held",
    "managed_control_not_authorized", "managed_control_credential_stale",
    "managed_control_runtime_identity_unavailable",
    "managed_text_state_owner_required", "managed_text_state_owner_invalid",
    "managed_session_tainted_after_unknown",
    "managed_session_tainted_after_successor_projection_failure",
    "stale_game_continuity", "stale_managed_runtime_instance",
    "text_request_id_conflict_across_episodes",
    "mutation_request_id_conflict_between_routes",
})


class ManagedHostServiceError(RuntimeError):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


class ManagedHostUncertainError(ManagedHostServiceError):
    """An offered POST lost its reply; native delivery must remain unknown."""


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ManagedHostServiceError("managed_host_attachment_directory_not_private")


def _attachment(path: Path, role: str) -> dict[str, Any]:
    info = path.stat()
    if os.name != "nt" and info.st_mode & 0o077:
        raise ManagedHostServiceError("managed_host_attachment_not_private")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != ATTACHMENT_SCHEMA \
            or value.get("role") != role:
        raise ManagedHostServiceError("managed_host_attachment_invalid")
    endpoint = value.get("endpoint")
    parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
    if parsed is None or parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"} \
            or parsed.port is None or parsed.username or parsed.password or parsed.path not in {"", "/"}:
        raise ManagedHostServiceError("managed_host_endpoint_not_loopback")
    if not isinstance(value.get("service_instance_id"), str) \
            or not value["service_instance_id"].startswith("managed_service_") \
            or not isinstance(value.get("token"), str) or not value["token"] \
            or not isinstance(value.get("host_identity"), dict):
        raise ManagedHostServiceError("managed_host_attachment_invalid")
    return value


class ManagedHostServiceClient:
    def __init__(self, attachment: Mapping[str, Any], *, timeout: float = 90.0):
        self.endpoint = str(attachment["endpoint"]).rstrip("/")
        self.service_instance_id = str(attachment["service_instance_id"])
        self.host_identity = dict(attachment["host_identity"])
        self._token = str(attachment["token"])
        self._timeout = timeout
        self._opener = build_opener(ProxyHandler({}))

    @classmethod
    def from_attachment(cls, path: Path | str) -> ManagedHostServiceClient:
        return cls(_attachment(Path(path), "client"))

    def _call(self, method: str, route: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}",
                   "X-STS2-Managed-Service-ID": self.service_instance_id}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(f"{self.endpoint}{route}", data=data, headers=headers, method=method)
        uncertain = method == "POST"
        error_type = ManagedHostUncertainError if uncertain else ManagedHostServiceError
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 200:
                    raise error_type("managed_host_unexpected_http_status", response.status)
                value = json.load(response)
        except HTTPError as exc:
            try:
                value = json.load(exc)
            except (ValueError, OSError, HTTPException):
                raise error_type("managed_host_http_error", exc.code) from exc
            if not isinstance(value, dict) or value.get("schema") != ERROR_SCHEMA \
                    or value.get("service_instance_id") != self.service_instance_id:
                raise error_type("managed_host_error_identity_mismatch", exc.code) from exc
            code = value.get("error")
            if not isinstance(code, str) or not (400 <= exc.code < 500) \
                    or (uncertain and code not in KNOWN_POST_REJECTIONS):
                raise error_type("managed_host_http_outcome_unknown", exc.code) from exc
            raise ManagedHostServiceError(code, exc.code) from exc
        except (URLError, TimeoutError, OSError, HTTPException, ValueError) as exc:
            raise error_type("managed_host_reply_unknown" if uncertain
                             else "managed_host_unreachable") from exc
        if not isinstance(value, dict) or value.get("service_instance_id") != self.service_instance_id:
            raise error_type("managed_host_service_identity_mismatch")
        return value

    def ready(self) -> dict[str, Any]:
        value = self._call("GET", "/v1/ready")
        if value.get("schema") != READY_SCHEMA or value.get("host_identity") != self.host_identity:
            raise ManagedHostServiceError("managed_host_ready_identity_mismatch")
        return value

    def request(self, command: Mapping[str, Any]) -> dict[str, Any]:
        body = dict(command)
        body.setdefault("request_id", uuid4().hex)
        value = self._call("POST", "/v1/command", body)
        if value.get("schema") != RESULT_SCHEMA or not isinstance(value.get("result"), dict) \
                or value["result"].get("request_id") != body["request_id"]:
            raise ManagedHostUncertainError("managed_host_result_correlation_mismatch")
        return value


class ManagedHostServiceManager(ManagedHostServiceClient):
    @classmethod
    def from_attachment(cls, path: Path | str) -> ManagedHostServiceManager:
        return cls(_attachment(Path(path), "manager"))

    def status(self) -> dict[str, Any]:
        value = self._call("GET", "/v1/admin/status")
        if value.get("schema") != READY_SCHEMA or not isinstance(value.get("status"), dict):
            raise ManagedHostServiceError("managed_host_status_invalid")
        return value

    def _admin(self, route: str, body: dict[str, Any]) -> dict[str, Any]:
        body.setdefault("request_id", uuid4().hex)
        value = self._call("POST", route, body)
        if value.get("schema") != RESULT_SCHEMA or not isinstance(value.get("result"), dict) \
                or value["result"].get("request_id") != body["request_id"]:
            raise ManagedHostUncertainError("managed_host_result_correlation_mismatch")
        return value

    def reset(self, seed: str, *, expected_runtime_instance_id: str,
              expected_game_continuity_id: str | None) -> dict[str, Any]:
        return self._admin("/v1/admin/reset", {
            "seed": seed,
            "expected_runtime_instance_id": expected_runtime_instance_id,
            "expected_game_continuity_id": expected_game_continuity_id,
        })

    def recover_control(self, *, expected_control_epoch: str,
                        expected_runtime_instance_id: str,
                        expected_game_continuity_id: str) -> dict[str, Any]:
        return self._admin("/v1/admin/recover-control", {
            "expected_service_instance_id": self.service_instance_id,
            "expected_control_epoch": expected_control_epoch,
            "expected_runtime_instance_id": expected_runtime_instance_id,
            "expected_game_continuity_id": expected_game_continuity_id,
        })

    def close_host(self) -> dict[str, Any]:
        return self._admin("/v1/admin/close", {})


@dataclass
class ManagedHostServiceLaunch:
    pid: int
    service_instance_id: str
    endpoint: str
    client_attachment: Path
    manager_attachment: Path
    process: subprocess.Popen = field(repr=False)


def launch_managed_host_service(*, host_package_dir: Path | str,
                                candidate_dir: Path | str,
                                game_dir: Path | str | None = None,
                                client_attachment: Path | str,
                                manager_attachment: Path | str,
                                log_file: Path | str,
                                port: int = 0,
                                character: str = "Ironclad",
                                timeout_ms: int = 10_000,
                                startup_timeout: float = 120.0) -> ManagedHostServiceLaunch:
    """Explicitly start a detached Host. Dropping this object never closes it."""
    package = Path(host_package_dir).resolve()
    tool = package / "tools" / "managed-host-service.mjs"
    if not tool.is_file():
        raise ManagedHostServiceError("managed_host_package_service_missing")
    client_file = Path(client_attachment).resolve()
    manager_file = Path(manager_attachment).resolve()
    log_path = Path(log_file).resolve()
    if client_file == manager_file or client_file in {log_path} or manager_file in {log_path}:
        raise ManagedHostServiceError("managed_host_attachment_paths_conflict")
    for path in (client_file, manager_file, log_path):
        _private_directory(path.parent)
    if client_file.exists() or manager_file.exists():
        raise ManagedHostServiceError("managed_host_attachment_already_exists")
    node = shutil.which("node")
    if node is None:
        raise ManagedHostServiceError("managed_host_node_unavailable")
    args = [node, str(tool), "--candidate", str(Path(candidate_dir).resolve()),
            "--client-attachment", str(client_file),
            "--manager-attachment", str(manager_file),
            "--port", str(port), "--character", character,
            "--timeout-ms", str(timeout_ms), "--quiet-diagnostics"]
    if game_dir is not None:
        args.extend(["--game-dir", str(Path(game_dir).resolve())])
    log_descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(log_descriptor, "wb") as log:
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            process = subprocess.Popen(args, cwd=package, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != "nt", creationflags=flags)
    except Exception:
        log_path.unlink(missing_ok=True)
        raise
    try:
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ManagedHostServiceError("managed_host_launch_exited")
            if client_file.is_file() and manager_file.is_file():
                client = ManagedHostServiceClient.from_attachment(client_file)
                manager = ManagedHostServiceManager.from_attachment(manager_file)
                ready = client.ready()
                if ready["service_instance_id"] != manager.service_instance_id:
                    raise ManagedHostServiceError("managed_host_attachment_identity_mismatch")
                return ManagedHostServiceLaunch(process.pid, client.service_instance_id,
                                                client.endpoint, client_file, manager_file, process)
            time.sleep(0.05)
        raise ManagedHostServiceError("managed_host_launch_timeout")
    except Exception:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait(timeout=5)
                raise ManagedHostServiceError("managed_host_launch_cleanup_unconfirmed") from exc
        raise
