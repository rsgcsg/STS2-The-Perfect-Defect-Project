"""One loopback developer surface supervising the configured Platform delivery tool."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import hmac
import importlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from spireagent.console.page import CSP, asset, render_shell
from spireagent.json_boundary import BoundaryError, digest
from spireagent.workbench.console import LocalConsole
from spireagent.workbench.dashboard import _safe_value
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json, doctor, tool_identity
from spireagent.workbench.hub_client import HubClient
from spireagent.workbench.identity import LocalIdentity
from spireagent.workbench.instance_lock import instance_lock as instance_lock
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.member_client import MemberClient
from spireagent.workbench.native_workbench import (
    WorkbenchRegistrationLoop,
    start_workbench_registration,
)
from spireagent.workbench.service_process import service_environment, service_process


@contextlib.contextmanager
def open_lock(path: Path, *, timeout: float = 20.0) -> Iterator[None]:
    """Serialize open callers while leaving the serve process' instance lock free."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with path.open("a+b") as handle:
        while True:
            try:
                if os.name == "nt":
                    msvcrt = importlib.import_module("msvcrt")
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl = importlib.import_module("fcntl")
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                    raise
                if time.monotonic() >= deadline:
                    raise BoundaryError("project", "open_in_progress") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                msvcrt = importlib.import_module("msvcrt")
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def configuration_id(config: ProjectConfig) -> str:
    return hashlib.sha256(json.dumps(config.to_dict(), sort_keys=True).encode()).hexdigest()


def _local_request(url: str, *, token: str | None = None, timeout: float = 2) -> dict[str, Any]:
    request = Request(
        url,
        data=b"" if token else None,
        headers={"Authorization": "Bearer " + token} if token else {},
    )
    with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
        value: Any = json.loads(response.read(1024 * 1024))
    if not isinstance(value, dict):
        raise BoundaryError("project", "invalid_local_response")
    return value


def running(config: ProjectConfig) -> dict[str, Any] | None:
    try:
        value = json.loads((config.state_dir / "runtime.json").read_bytes())
        if value["configuration_id"] != configuration_id(config):
            raise BoundaryError("project", "running_configuration_mismatch")
        if not isinstance(value["port"], int) or not 0 < value["port"] < 65536:
            raise ValueError
        observed = _local_request(f"http://127.0.0.1:{value['port']}/health")
        if observed.get("instance_id") != value["instance_id"]:
            raise BoundaryError("project", "runtime_instance_mismatch")
        return cast(dict[str, Any], value)
    except (OSError, ValueError, KeyError):
        return None


def open_project(
    path: Path,
    *,
    browser: bool = True,
    expected_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = ProjectConfig.load(path)
    with open_lock(config.state_dir / "open.lock"):
        current = running(config)
        if (current is not None and expected_identity is not None
                and current.get("identity") != expected_identity):
            raise BoundaryError("project", "running_identity_mismatch")
        if current is None:
            report = doctor(config)
            if report["status"] != "PASS":
                raise BoundaryError("project", "doctor_blocked")
            config.state_dir.mkdir(parents=True, exist_ok=True)
            environment = service_environment(config.state_dir)
            environment.pop("STPD_HUB_ADMIN_TOKEN", None)
            command = [sys.executable]
            if expected_identity is not None:
                # The installed fixed launcher opts into an isolated child so
                # inherited Python/uv settings cannot redirect its imports.
                for name in (
                    "PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE", "VIRTUAL_ENV",
                    "UV_PROJECT_ENVIRONMENT", "UV_WORKING_DIR", "UV_PROJECT",
                    "UV_PYTHON", "UV_CONFIG_FILE", "UV_ENV_FILE",
                ):
                    environment.pop(name, None)
                command.append("-I")
            command.extend([
                "-m",
                "spireagent.workbench",
                "project",
                "serve",
                "--config",
                str(path),
            ])
            with (config.state_dir / "logs" / "workbench.log").open("ab") as log:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                )

            def stop_unhealthy_child() -> None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)

            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = running(config)
                if current is not None:
                    break
                if process.poll() is not None:
                    # A competing pre-existing server can own instance.lock. Re-read
                    # its exact configuration and identity before reporting success.
                    current = running(config)
                    if current is not None:
                        break
                    raise BoundaryError("project", "background_start_failed")
                time.sleep(0.1)
            if current is None:
                stop_unhealthy_child()
                raise BoundaryError("project", "background_start_timeout")
            if expected_identity is not None and current.get("identity") != expected_identity:
                if current.get("pid") == process.pid:
                    stop_unhealthy_child()
                raise BoundaryError("project", "running_identity_mismatch")
    url = f"http://127.0.0.1:{current['port']}/"
    if browser:
        webbrowser.open(url)
    return {
        "status": "running",
        "url": url,
        "instance_id": current["instance_id"],
        "delivery": current["delivery"],
        "gameplay_started": False,
    }


def stop_project(config: ProjectConfig) -> dict[str, Any]:
    current = running(config)
    if current is None:
        return {"status": "not_running"}
    return _local_request(
        f"http://127.0.0.1:{current['port']}/stop", token=current["control_token"]
    )


def status_project(config: ProjectConfig) -> dict[str, Any]:
    current = running(config)
    if current is None:
        return {"status": "not_running"}
    # A snapshot composes a 3-second delivery query and four 2-second Hub reads.
    # Leave response overhead without extending the independent health/stop requests.
    return _local_request(f"http://127.0.0.1:{current['port']}/api/status", timeout=15)


class Application:
    def __init__(self, config: ProjectConfig, *, config_path: Path | None = None) -> None:
        from spireagent.workbench.collection_flow import CollectionFlow
        from spireagent.workbench.collection_setup import CollectionSetup
        from spireagent.workbench.evaluation_sharing import EvaluationSharing
        from spireagent.workbench.inplace_curation import InplaceCurationPreparation
        from spireagent.workbench.local_dataset import LocalDatasetService
        from spireagent.workbench.local_environment import LocalEnvironmentService
        from spireagent.workbench.local_managed_source import LocalManagedSourceService
        from spireagent.workbench.local_memory_evaluation import LocalMemoryEvaluationService
        from spireagent.workbench.local_model_export import LocalModelExport
        from spireagent.workbench.local_model_registration import LocalModelRegistration
        from spireagent.workbench.local_recording_import import LocalRecordingImporter
        from spireagent.workbench.local_recording_preview import LocalRecordingPreview
        from spireagent.workbench.local_recordings import LocalRecordingCatalog
        from spireagent.workbench.local_training import LocalTrainingService

        self.config = config
        self.config_path = config_path
        self.instance_id = secrets.token_hex(16)
        self.control_token = secrets.token_hex(32)
        self.identity = tool_identity()
        self.account = LocalIdentity(config)
        from spireagent.workbench.native_workbench_access import NativeWorkbenchAccess
        from spireagent.workbench.native_workbench_api import NativeWorkbenchApi

        self.native_access = NativeWorkbenchAccess(self)
        self.native_api = NativeWorkbenchApi(self)
        self.members = MemberClient(self.account)
        self.collection = CollectionSetup(self.members)
        self.delivery: subprocess.Popen[bytes] | None = None
        self.delivery_log: Any = None
        self.operation_lock = threading.Lock()
        self.hub = (
            HubClient(config.hub_url, timeout=2, token=self.account.device_token)
            if config.hub_url
            else None
        )
        self.console = LocalConsole(
            config, self.hub, self.delivery_environment, self.delivery_process, self.identity
        )
        self.local_environment = LocalEnvironmentService(config)
        self.models = LocalModelService(
            config, hub=self.hub,
            managed_target=self.local_environment.managed_runtime_target,
        )
        # Presentation intent fence only; Recorder retains its sole command ledger/lifecycle.
        self._recording_intent_lock = self.models.lock
        self._recording_intent_pending = False
        self._recording_unknown_notice: dict[str, Any] | None = None
        self._recording_unknown_blocks = False
        self._recording_runtime_context: str | None = None
        self._recording_previous_unknown: list[dict[str, Any]] = []
        self.models.bind_recording_admission_guard(self._recording_model_admission_blocked)
        self.local_managed_sources = LocalManagedSourceService(config, self.local_environment)
        self.local_recordings = LocalRecordingCatalog(config)
        # Keep command-time owner observations separate from concurrent browser GET scans.
        self.local_recording_import = LocalRecordingImporter(config, LocalRecordingCatalog(config))
        self.local_recording_preview = LocalRecordingPreview(self.local_research_workspace)
        self.local_datasets = LocalDatasetService(config)
        self.local_training = LocalTrainingService(config)
        self.local_memory_evaluation = LocalMemoryEvaluationService(config)
        self.local_model_export = LocalModelExport(config)
        self.local_model_registration = LocalModelRegistration(
            config, self.local_model_export, self.models,
        )
        self.local_curation_preparation = InplaceCurationPreparation(config)
        self.evaluation_sharing = EvaluationSharing(self.models, self.members)
        self.delivery_error: str | None = None
        self.collection_flow = CollectionFlow(
            self.collection,
            delivery_process=self.delivery_process,
            activate=self.activate_collection,
            stop_delivery=self.pause_delivery,
        )

    def activate_collection(self, enrollment_id: str) -> dict[str, Any]:
        with self.operation_lock:
            if self.config_path is None or ProjectConfig.load(self.config_path) != self.config:
                raise BoundaryError("collection", "running_configuration_mismatch")
            target = self.collection.activation_config(enrollment_id)
            if self.config.delivery_config is not None and self.config.delivery_config != target:
                raise BoundaryError("collection", "another_collection_attached")
            updated = replace(self.config, delivery_config=target)
            if doctor(updated)["status"] != "PASS":
                raise BoundaryError("collection", "delivery_preflight_blocked")
            if self.config != updated:
                runtime_path = updated.state_dir / "runtime.json"
                runtime = json.loads(runtime_path.read_bytes())
                if runtime.get("instance_id") != self.instance_id:
                    raise BoundaryError("collection", "runtime_instance_mismatch")
                previous_config = self.config.to_dict()
                updated_runtime = {
                    **runtime,
                    "configuration_id": configuration_id(updated),
                    "delivery": "configured",
                }
                atomic_json(self.config_path, updated.to_dict())
                try:
                    atomic_json(runtime_path, updated_runtime)
                except OSError:
                    atomic_json(self.config_path, previous_config)
                    raise
                self.config = updated
                self.account.config = updated
                self.models.config = updated
                self.console = LocalConsole(
                    updated,
                    self.hub,
                    self.delivery_environment,
                    self.delivery_process,
                    self.identity,
                )
            if self.delivery is None or self.delivery.poll() is not None:
                self.close_delivery()
                self.delivery, self.delivery_log = None, None
                self.start_delivery()
            return self.collection.preparation(
                self.collection.enrollment(enrollment_id),
                self.delivery_process(),
            )

    def delivery_process(self) -> str:
        if self.delivery is None:
            return "not_configured"
        return "running" if self.delivery.poll() is None else "stopped"

    def delivery_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)
        token = self.account.device_token()
        if token:
            environment["STPD_HUB_TOKEN"] = token
        return environment

    def pause_delivery(self) -> None:
        with self.operation_lock:
            self.close_delivery()
            self.delivery, self.delivery_log = None, None

    def start_delivery(self) -> None:
        from spireagent.workbench.collection_flow import upload_preference_status

        preference = upload_preference_status(self.config)
        self.delivery_error = preference.get("error")
        if not preference["enabled"]:
            return
        if self.config.delivery_config is None or not self.account.device_token():
            return
        self.delivery_log = (self.config.state_dir / "logs" / "delivery.log").open("ab")
        self.delivery = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "sts2_platform_evidence.delivery_cli",
                "run",
                "--config",
                str(self.config.delivery_config),
            ],
            stdin=subprocess.DEVNULL,
            stdout=self.delivery_log,
            stderr=subprocess.STDOUT,
            cwd=self.config.state_dir,
            env=self.delivery_environment(),
        )

    def resume_auth(self) -> dict[str, Any]:
        with self.operation_lock:
            device = self.account.device()
            if not device or self.config.delivery_config is None:
                raise BoundaryError("project", "bound_device_and_delivery_required")
            observed = self.account.request(
                "/v1/identity/device", token=self.account.device_token()
            )
            if observed.get("device_id") != device["device_id"]:
                raise BoundaryError("project", "device_identity_changed")
            self.close_delivery()
            self.delivery, self.delivery_log = None, None
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-m",
                        "sts2_platform_evidence.delivery_cli",
                        "resume-auth",
                        "--config",
                        str(self.config.delivery_config),
                    ],
                    cwd=self.config.state_dir,
                    env=self.delivery_environment(),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                value = json.loads(result.stdout)
                if (
                    result.returncode
                    or not isinstance(value, dict)
                    or value.get("schema") != "sts2.evidence/delivery-auth-recovery-1"
                ):
                    raise BoundaryError("project", "owner_recovery_failed")
                return value
            finally:
                self.start_delivery()

    def delivery_status(self) -> dict[str, Any]:
        if self.delivery is None:
            return {
                "status": "blocked" if self.delivery_error else "not_configured",
                **({"error": self.delivery_error} if self.delivery_error else {}),
            }
        code = self.delivery.poll()
        state: dict[str, Any] = {
            "status": "running" if code is None else "stopped",
            "exit_code": code,
        }
        try:
            observed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "sts2_platform_evidence.delivery_cli",
                    "status",
                    "--config",
                    str(self.config.delivery_config),
                ],
                cwd=self.config.state_dir,
                env=self.delivery_environment(),
                capture_output=True,
                timeout=3,
                check=False,
            )
            if observed.returncode == 0:
                state["outbox"] = json.loads(observed.stdout)
            else:
                state["outbox"] = {"status": "unavailable"}
        except (OSError, ValueError, subprocess.SubprocessError):
            state["outbox"] = {"status": "unavailable"}
        return state

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": "stpd/developer-status-v1",
            "instance_id": self.instance_id,
            "identity": self.identity,
            "delivery": self.delivery_status(),
            "hub": self.hub.snapshot() if self.hub else {"status": "not_configured"},
        }

    def local_research_workspace(self) -> Any | None:
        from spireagent.workbench.local_workspace import open_registered_workspace

        if self.config.research_workspace is not None:
            return open_registered_workspace(self.config.research_workspace)
        managed = self.managed_local_workspace()
        workspace = managed.get("workspace")
        if workspace is not None:
            # This is a read-only projection over the verified owner path.
            workspace.curation_owner = managed.get("curation_owner")
        return workspace

    def managed_local_workspace(self) -> dict[str, Any]:
        if self.config.research_workspace is not None:
            return {
                "schema": "stpd/managed-local-workspace-registration-v1",
                "status": "legacy_workspace_configured",
                "requires_cloud_account": False,
                "curation_status": "recovery_required",
                "curation_recovery": "legacy_history_requires_explicit_migration",
            }
        from spireagent.workbench.managed_local_workspace import inspect_managed_workspace

        return inspect_managed_workspace(self.config.state_dir)

    def check_environment_instance(self) -> None:
        """Mutations belong to this exact running Workbench configuration."""
        if self.config_path is None:
            raise BoundaryError("local_environment", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_bytes())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_environment", "running_instance_unavailable") from error
        if (current != self.config or not isinstance(runtime, dict)
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_environment", "running_configuration_mismatch")

    def create_managed_local_workspace(self) -> dict[str, Any]:
        if self.config_path is None:
            raise BoundaryError("managed_workspace", "running_instance_unavailable")
        if self.config.research_workspace is not None:
            raise BoundaryError("managed_workspace", "legacy_workspace_configured")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError("managed_workspace", "running_instance_unavailable") from error
        if not isinstance(runtime, dict):
            raise BoundaryError("managed_workspace", "running_instance_unavailable")
        if (current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("managed_workspace", "running_configuration_mismatch")
        from spireagent.workbench.managed_local_workspace import create_managed_workspace

        result = create_managed_workspace(self.config.state_dir)
        result.pop("workspace", None)
        result.pop("curation_owner", None)
        return result

    def prepare_local_curation(self) -> dict[str, Any]:
        if self.config_path is None or self.config.research_workspace is None:
            raise BoundaryError("local_curation", "configured_workspace_required")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_curation", "running_instance_unavailable") from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_curation", "running_configuration_mismatch")
        return self.local_curation_preparation.start()

    def _recording_endpoint(self) -> str:
        from spireagent.workbench.native_tasks import NativeTasks

        return NativeTasks.bound_connector(self.config.platform_url or "http://127.0.0.1:15526")

    def _recording_model_admission_blocked(self) -> bool:
        # Called only under models.lock. The notice remains the sole unknown fact;
        # a separately validated new runtime merely makes its old scope inapplicable.
        notice = self._recording_unknown_notice
        return (
            self._recording_unknown_blocks
            and notice is not None
            and (
                self._recording_runtime_context is None
                or notice["runtime_instance_id"] == self._recording_runtime_context
            )
        )

    def _recording_model_recovery_required(self, *, fresh: bool = False) -> bool:
        return self.models.recording_recovery_required(fresh=fresh)

    def native_recording_status(self) -> dict[str, Any]:
        endpoint = self._recording_endpoint()
        owner = self.models.native_tasks.recording_status(endpoint)
        with self._recording_intent_lock:
            self._recording_runtime_context = owner["runtime_instance_id"]
            notice = (
                dict(self._recording_unknown_notice) if self._recording_unknown_notice else None
            )
            pending = self._recording_intent_pending
            blocked = (
                self._recording_unknown_blocks
                and notice is not None
                and (notice["runtime_instance_id"] == owner["runtime_instance_id"])
            )
            previous = [dict(item) for item in self._recording_previous_unknown]
        # Scalar owner query only. This view does not poll the whole game or a model Snapshot.
        return {
            "schema": "spireagent/native-recording-view-1",
            "status": owner,
            "connection": endpoint,
            "model_recovery_required": self._recording_model_recovery_required(),
            "command_pending": pending,
            "unconfirmed": notice,
            "recovery_required": blocked,
            "previous_unconfirmed": previous,
        }

    def control_native_recording(self, body: dict[str, Any]) -> dict[str, Any]:
        from uuid import UUID, uuid4

        from spireagent.workbench.native_tasks import NativeTasks

        fields = {
            "kind",
            "runtime_instance_id",
            "recording_session_id",
            "source_segment_id",
            "source_kind",
            "actor_id",
            "command_id",
        }
        if (
            not isinstance(body, dict)
            or set(body) != fields
            or not isinstance(body["kind"], str)
            or body["kind"]
            not in {"start_new_session", "pause", "resume", "change_source", "close"}
            or not NativeTasks._recording_identifier(body["runtime_instance_id"])
            or not NativeTasks._recording_identifier(body["recording_session_id"], nullable=True)
            or not NativeTasks._recording_identifier(body["source_segment_id"], nullable=True)
        ):
            raise BoundaryError("recording", "invalid_native_recording_command")
        try:
            if (
                not isinstance(body["command_id"], str)
                or str(UUID(body["command_id"])) != body["command_id"]
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise BoundaryError("recording", "invalid_native_recording_command") from None
        declaration = None
        if body["kind"] in {"start_new_session", "change_source"}:
            declaration = {
                "source_kind": body["source_kind"],
                "actor_id": body["actor_id"],
                "declaration_id": str(uuid4()),
                "machine_verifiable": False,
            }
            if not NativeTasks._source_declaration(declaration):
                raise BoundaryError("recording", "invalid_native_recording_command")
        elif body["source_kind"] is not None or body["actor_id"] is not None:
            raise BoundaryError("recording", "invalid_native_recording_command")
        reservation = self.models.reserve_recording_source_mutation(
            require_human=declaration is not None and body["source_kind"] != "agent_protocol"
        )
        with reservation:
            endpoint = self._recording_endpoint()
            observed = self.models.native_tasks.recording_status(endpoint)
            if (
                observed["runtime_instance_id"] != body["runtime_instance_id"]
                or observed["recording_session_id"] != body["recording_session_id"]
                or (observed["source"]["segment_id"] if observed["source"] is not None else None)
                != body["source_segment_id"]
            ):
                raise BoundaryError("recording", "native_recording_context_changed")
            with self._recording_intent_lock:
                self._recording_runtime_context = observed["runtime_instance_id"]
                if (
                    declaration is not None
                    and body["source_kind"] != "agent_protocol"
                    and self._recording_model_recovery_required()
                ):
                    raise BoundaryError("recording", "model_recovery_required")
                if self._recording_intent_pending:
                    raise BoundaryError("recording", "native_recording_command_pending")
                blocked = (
                    self._recording_unknown_blocks
                    and self._recording_unknown_notice is not None
                    and (
                        self._recording_unknown_notice["runtime_instance_id"]
                        == observed["runtime_instance_id"]
                    )
                )
                fresh_session = (
                    body["kind"] == "start_new_session"
                    and observed["recording_lifecycle"] == "closed"
                    and observed["closeout_status"] == "closed"
                )
                if blocked and body["kind"] != "close" and not fresh_session:
                    raise BoundaryError("recording", "native_recording_recovery_required")
                self._recording_intent_pending = True
            try:
                value = self.models.native_tasks.recording_command(
                    endpoint,
                    observed,
                    body["kind"],
                    source_declaration=declaration,
                    command_id=body["command_id"],
                )
                if not value["accepted"]:
                    raise BoundaryError("recording", "native_recording_rejected")
                with self._recording_intent_lock:
                    after = value["status"]
                    known_close = (
                        body["kind"] == "close"
                        and after["runtime_instance_id"] == observed["runtime_instance_id"]
                        and after["recording_session_id"] == observed["recording_session_id"]
                        and after["recording_lifecycle"] == "closed"
                        and after["closeout_status"] == "closed"
                    )
                    isolated_start = (
                        body["kind"] == "start_new_session"
                        and after["recording_session_id"] is not None
                        and after["recording_session_id"] != observed["recording_session_id"]
                        and (
                            fresh_session
                            or (
                                self._recording_unknown_notice is not None
                                and after["runtime_instance_id"]
                                != self._recording_unknown_notice["runtime_instance_id"]
                            )
                        )
                    )
                    # A distinct known Close/isolation can release sequencing; the
                    # original unknown notice/history is never resolved or erased.
                    if known_close or isolated_start:
                        self._recording_unknown_blocks = False
                return value
            except BoundaryError as error:
                if error.code == "native_recording_command_unknown":
                    with self._recording_intent_lock:
                        if self._recording_unknown_notice is not None:
                            self._recording_previous_unknown = [
                                *self._recording_previous_unknown[-7:],
                                self._recording_unknown_notice,
                            ]
                        self._recording_unknown_notice = {
                            "command_id": body["command_id"],
                            "kind": body["kind"],
                            "runtime_instance_id": body["runtime_instance_id"],
                            "recording_session_id": body["recording_session_id"],
                        }
                        self._recording_unknown_blocks = True
                raise
            finally:
                with self._recording_intent_lock:
                    self._recording_intent_pending = False

    def start_local_recording_import(self, candidate_id: object,
                                     human_origin_attested: object = None) -> dict[str, Any]:
        if self.config_path is None:
            raise BoundaryError("local_import", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_import", "running_instance_unavailable") from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_import", "running_configuration_mismatch")
        return self.local_recording_import.start(candidate_id, human_origin_attested)

    def start_local_dataset_preview(self, artifact_id: object, purpose: object,
                                    paired_training: object) -> dict[str, Any]:
        if self.config_path is None:
            raise BoundaryError("local_dataset", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_dataset", "running_instance_unavailable") from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_dataset", "running_configuration_mismatch")
        return self.local_datasets.start_preview(artifact_id, purpose, paired_training)

    def start_local_human_dataset_preview(self, artifact_ids: object) -> dict[str, Any]:
        if self.config_path is None:
            raise BoundaryError("local_dataset", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_dataset", "running_instance_unavailable") from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_dataset", "running_configuration_mismatch")
        return self.local_datasets.start_human_preview(artifact_ids)

    def start_local_source3_dataset_preview(self, artifact_ids: object, cohort: object,
                                          view: object) -> dict[str, Any]:
        if self.config_path is None:
            raise BoundaryError("local_dataset", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_dataset", "running_instance_unavailable") from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_dataset", "running_configuration_mismatch")
        return self.local_datasets.start_source3_preview(artifact_ids, cohort, view)

    def start_local_dataset_publish(self, preview_id: object) -> dict[str, Any]:
        if self.config_path is None:
            raise BoundaryError("local_dataset", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_dataset", "running_instance_unavailable") from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_dataset", "running_configuration_mismatch")
        return self.local_datasets.start_publish(preview_id)

    def start_local_training(self, dataset_id: object, *,
                             after_completed_operation_id: object | None = None,
                             recipe: object = "stage1a.dsimple.s.v1") -> dict[str, Any]:
        self._require_training_instance()
        return self.local_training.start(
            dataset_id, after_completed_operation_id=after_completed_operation_id,
            recipe=recipe)

    def _require_training_instance(self) -> None:
        if self.config_path is None:
            raise BoundaryError("local_training", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_training", "running_instance_unavailable") from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_training", "running_configuration_mismatch")

    def control_local_training(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        self._require_training_instance()
        if action == "resume":
            return self.local_training.resume(**body)
        if action == "pause":
            return self.local_training.pause(**body)
        if action == "cancel":
            return self.local_training.cancel(**body)
        if action == "reconcile":
            return self.local_training.reconcile(**body)
        raise BoundaryError("local_training", "unsupported_training_action")

    def start_local_memory_evaluation(self, model_id: object, source_id: object,
                                      *, max_settling_events: object = None) -> dict[str, Any]:
        if self.config_path is None:
            raise BoundaryError("local_memory_evaluation", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError(
                "local_memory_evaluation", "running_instance_unavailable") from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_memory_evaluation", "running_configuration_mismatch")
        return self.local_memory_evaluation.start(
            model_id, source_id, max_settling_events=max_settling_events)

    def start_local_model_export(self, model_id: object) -> dict[str, Any]:
        if self.config_path is None:
            raise BoundaryError("local_model_export", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError("local_model_export", "running_instance_unavailable") from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_model_export", "running_configuration_mismatch")
        return self.local_model_export.start(model_id)

    def register_local_model(self, model_id: object, *,
                             environment_kind: str = "native") -> dict[str, Any]:
        if self.config_path is None:
            raise BoundaryError("local_model_registration", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError(
                "local_model_registration", "running_instance_unavailable",
            ) from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_model_registration", "running_configuration_mismatch")
        return self.local_model_registration.register(model_id, environment_kind=environment_kind)

    def close(self) -> None:
        self.local_environment.close()
        self.evaluation_sharing.close()
        self.members.close()
        self.models.close()
        self.close_delivery()

    def close_delivery(self) -> None:
        if self.delivery is not None and self.delivery.poll() is None:
            self.delivery.terminate()
            try:
                self.delivery.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.delivery.kill()
                self.delivery.wait(timeout=3)
        if self.delivery_log is not None:
            self.delivery_log.close()


def create_server(app: Application) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def local_host(self) -> bool:
            expected = "127.0.0.1:" + str(cast(ThreadingHTTPServer, self.server).server_port)
            return self.headers.get("Host") == expected

        def authenticated_browser(self) -> bool:
            from http.cookies import SimpleCookie

            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            value = cookie.get(app.account.cookie_name)
            return bool(value and hmac.compare_digest(value.value, app.account.cookie))

        def control_client(self) -> bool:
            return self.local_host() and hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + app.control_token
            )

        def browser_write(self) -> bool:
            origin = "http://127.0.0.1:" + str(cast(ThreadingHTTPServer, self.server).server_port)
            return (
                self.local_host()
                and self.authenticated_browser()
                and self.headers.get("Origin") == origin
                and hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), app.account.csrf)
            )

        def json_body(self, maximum: int = 65536, *,
                      reject_duplicate: bool = False) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < length <= maximum
                or self.headers.get("Content-Type") != "application/json"
                or self.headers.get("Transfer-Encoding")
            ):
                raise ValueError
            raw = self.rfile.read(length)
            if reject_duplicate:
                from spireagent.workbench.native_workbench_access import unique_object

                body = json.loads(raw, object_pairs_hook=unique_object)
            else:
                body = json.loads(raw)
            if len(raw) != length or not isinstance(body, dict):
                raise ValueError
            return body

        def model_action(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
            if path == "/api/local-models/prepare" and set(body) in (
                {"selection_id"}, {"selection_id", "run_profile"}
            ):
                return app.models.prepare_and_load(
                    body["selection_id"], body.get("run_profile", "short")
                )
            if path == "/api/local-models/share" and set(body) == {"evaluation_id", "authorized"}:
                return app.evaluation_sharing.share(body["evaluation_id"], body["authorized"])
            if path == "/api/local-models/start" and set(body) in (
                {"selection_id"}, {"selection_id", "run_profile"}
            ):
                return app.models.start(body["selection_id"], body.get("run_profile", "short"))
            if path == "/api/local-models/download" and set(body) == {"artifact_id"}:
                return app.models.prepare(body["artifact_id"])
            if path == "/api/local-models/command" and set(body) == {"action"}:
                return app.models.command(body["action"])
            if (path == "/api/local-models/command" and set(body) == {"action", "request_id"}
                    and body["action"] == "reconcile"):
                return app.models.command("reconcile", request_id=body["request_id"])
            if path == "/api/local-models/install-runtime" and not body:
                return app.models.install_runtime()
            if path == "/api/local-models/prepare-text-runtime" and set(body) == {
                "runtime_profile"
            }:
                return app.models.prepare_text_runtime(body["runtime_profile"])
            raise BoundaryError("local_model", "invalid_local_command")

        def respond(self, code: int, value: bytes, content_type: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(value)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                CSP,
            )
            if content_type == "text/html":
                self.send_header(
                    "Set-Cookie",
                    app.account.cookie_name
                    + "="
                    + app.account.cookie
                    + "; HttpOnly; SameSite=Strict; Path=/",
                )
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(value)

        def do_GET(self) -> None:
            if not self.local_host():
                self.respond(403, b"{}")
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/api/native-workbench/v1/view":
                from spireagent.workbench.native_workbench_api import bounded_response

                try:
                    pair = app.native_access.authenticate(self.headers)
                    self.respond(200, bounded_response(app.native_api.view(parsed.query, pair)))
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError, KeyError):
                    self.respond(400, b'{"error":"invalid_native_request"}')
                return
            if parsed.path == "/health":
                self.respond(200, json.dumps({"instance_id": app.instance_id}).encode())
            elif parsed.path == "/api/status":
                self.respond(200, json.dumps(_safe_value(app.snapshot())).encode())
            elif parsed.path == "/api/local-environment" or parsed.path == (
                "/api/local-environment/reports"
            ) or parsed.path == (
                "/api/local-environment/scenes"
            ) or parsed.path == (
                "/api/local-environment/comparisons"
            ) or parsed.path.startswith(("/api/local-environment/reports/",
                                          "/api/local-environment/events/",
                                          "/api/local-environment/scenes/",
                                          "/api/local-environment/comparisons/")):
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    if parsed.query:
                        raise ValueError
                    if parsed.path == "/api/local-environment":
                        value = {
                            **app.local_environment.status(),
                            "csrf_token": app.account.csrf,
                        }
                    elif parsed.path == "/api/local-environment/reports":
                        value = app.local_environment.reports()
                    elif parsed.path == "/api/local-environment/scenes":
                        value = app.local_environment.scenes()
                    elif parsed.path == "/api/local-environment/comparisons":
                        value = app.local_environment.comparisons()
                    elif parsed.path.startswith("/api/local-environment/comparisons/"):
                        match = re.fullmatch(
                            r"/api/local-environment/comparisons/([a-f0-9]{64})", parsed.path
                        )
                        if match is None:
                            raise BoundaryError("local_environment", "artifact_not_found")
                        value = app.local_environment.comparison(match[1])
                    elif parsed.path.startswith("/api/local-environment/scenes/"):
                        match = re.fullmatch(
                            r"/api/local-environment/scenes/([a-f0-9]{64})", parsed.path
                        )
                        if match is None:
                            raise BoundaryError("local_environment", "artifact_not_found")
                        value = app.local_environment.scene(match[1])
                    elif parsed.path.startswith("/api/local-environment/events/"):
                        match = re.fullmatch(
                            r"/api/local-environment/events/([a-f0-9]{64})", parsed.path
                        )
                        if match is None:
                            raise BoundaryError("local_environment", "event_not_found")
                        value = app.local_environment.event(match[1])
                    else:
                        match = re.fullmatch(
                            r"/api/local-environment/reports/([a-f0-9]{64})", parsed.path
                        )
                        if match is None:
                            raise BoundaryError("local_environment", "report_not_found")
                        value = app.local_environment.report(match[1])
                    self.respond(200, json.dumps(value, ensure_ascii=False).encode())
                except BoundaryError as error:
                    status = 404 if error.code in {"report_not_found", "event_not_found",
                                                   "artifact_not_found"} else 409
                    self.respond(status, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, KeyError):
                    self.respond(400, b'{"error":"invalid_local_environment_request"}')
            elif parsed.path.startswith(("/api/member/", "/api/local-models")):
                if not self.authenticated_browser() and not (
                    parsed.path.startswith("/api/local-models") and self.control_client()
                ):
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    if parsed.path.startswith("/api/member/"):
                        route = parsed.path.removeprefix("/api/member/")
                        preparation = re.fullmatch(r"campaigns/([a-f0-9]{32})/preparation", route)
                        if route == "collection-flow" and not parsed.query:
                            value = app.collection_flow.status()
                        elif route == "collection-status" and not parsed.query:
                            value = app.collection.status(app.delivery_process())
                        elif preparation and not parsed.query:
                            value = app.collection.preparation(
                                app.collection.enrollment(preparation[1]),
                                app.delivery_process(),
                            )
                        elif route == "download-status" and not parsed.query:
                            value = app.members.download_status()
                        else:
                            value = app.members.request(
                                route + ("?" + parsed.query if parsed.query else "")
                            )
                    elif parsed.path == "/api/local-models/readiness":
                        query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=1)
                        if set(query) != {"selection_id"} or len(query["selection_id"]) != 1:
                            raise ValueError
                        value = app.models.readiness(query["selection_id"][0])
                    elif parsed.query:
                        raise ValueError
                    elif parsed.path == "/api/local-models":
                        value = app.models.catalog()
                    elif parsed.path == "/api/local-models/share-status":
                        value = app.evaluation_sharing.status()
                    elif parsed.path == "/api/local-models/status":
                        value = app.models.status()
                    else:
                        raise BoundaryError("member", "route_not_found")
                    self.respond(200, json.dumps(value, ensure_ascii=False).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, KeyError):
                    self.respond(400, b'{"error":"invalid_member_request"}')
            elif parsed.path == "/api/native-recording/status":
                if not self.local_host() or not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_native_recording_command"}')
                    return
                try:
                    value = {**app.native_recording_status(), "csrf_token": app.account.csrf}
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
            elif parsed.path == "/api/local-recordings":
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_local_recordings_request"}')
                    return
                value = app.local_recordings.read()
                self.respond(200, json.dumps(value, ensure_ascii=False).encode())
            elif parsed.path == "/api/local-recordings/import/status":
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_local_import_request"}')
                    return
                value = {**app.local_recording_import.status(), "csrf_token": app.account.csrf}
                self.respond(200, json.dumps(value).encode())
            elif parsed.path == "/api/local-recordings/preview/status":
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_local_preview_request"}')
                    return
                value = {**app.local_recording_preview.status(), "csrf_token": app.account.csrf}
                self.respond(200, json.dumps(value).encode())
            elif parsed.path == "/api/local-datasets/status":
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_local_dataset_request"}')
                    return
                try:
                    value = {**app.local_datasets.status(), "csrf_token": app.account.csrf}
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
            elif parsed.path.startswith("/api/local-datasets/binding/"):
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                matched = re.fullmatch(r"/api/local-datasets/binding/([a-f0-9]{64})",
                                       parsed.path)
                if matched is None or parsed.query:
                    self.respond(400, b'{"error":"invalid_local_dataset_request"}')
                    return
                try:
                    value = app.local_datasets.binding(matched[1])
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
            elif parsed.path.startswith("/api/local-managed-sources/binding/"):
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                matched = re.fullmatch(r"/api/local-managed-sources/binding/([a-f0-9]{64})",
                                       parsed.path)
                if matched is None or parsed.query:
                    self.respond(400, b'{"error":"invalid_local_managed_source_request"}')
                    return
                try:
                    value = app.local_managed_sources.binding(matched[1])
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
            elif parsed.path in {"/api/local-training/status", "/api/local-training/capabilities"}:
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    if parsed.path.endswith("/capabilities"):
                        if parsed.query:
                            raise ValueError
                        value = app.local_training.capabilities()
                    else:
                        query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=1,
                                         keep_blank_values=True)
                        if query and (set(query) != {"operation_id"} or
                                      len(query["operation_id"]) != 1):
                            raise ValueError
                        operation_id = query.get("operation_id", [None])[0]
                        if operation_id is not None:
                            operation_id = digest(
                                operation_id, "local_training.operation_id", length=32)
                        value = app.local_training.status(operation_id)
                    self.respond(200, json.dumps({**value,
                                                  "csrf_token": app.account.csrf}).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_training_request"}')
            elif parsed.path == "/api/local-memory-evaluations/status":
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_local_memory_evaluation_request"}')
                    return
                value = {**app.local_memory_evaluation.status(), "csrf_token": app.account.csrf}
                self.respond(200, json.dumps(value).encode())
            elif parsed.path == "/api/local-model-exports/status":
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_local_model_export_request"}')
                    return
                try:
                    value = {**app.local_model_export.status(), "csrf_token": app.account.csrf}
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
            elif parsed.path == "/api/local-model-registrations/status":
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=2,
                                     keep_blank_values=True)
                    if (set(query) not in ({"model_id"}, {"model_id", "environment_kind"})
                            or any(len(values) != 1 or not values[0] for values in query.values())):
                        raise ValueError
                    target = query.get("environment_kind", ["native"])[0]
                    value = {**app.local_model_registration.status(
                        query["model_id"][0], environment_kind=target),
                             "csrf_token": app.account.csrf}
                    self.respond(200, json.dumps(value).encode())
                except (BoundaryError, ValueError) as error:
                    if isinstance(error, BoundaryError):
                        self.respond(409, json.dumps({"error": error.code}).encode())
                    else:
                        self.respond(400, b'{"error":"invalid_local_model_registration_request"}')
            elif parsed.path == "/api/local-workspace/curation":
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_local_curation_request"}')
                    return
                value = {**app.local_curation_preparation.status(),
                         "csrf_token": app.account.csrf}
                self.respond(200, json.dumps(value).encode())
            elif parsed.path.startswith("/api/local-workspace/evaluations/"):
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_local_evaluation_request"}')
                    return
                try:
                    matched = re.fullmatch(
                        r"/api/local-workspace/evaluations/([a-f0-9]{64})", parsed.path
                    )
                    if matched is None:
                        raise BoundaryError("local_evaluation", "route_not_found")
                    workspace = app.local_research_workspace()
                    if workspace is None:
                        raise BoundaryError("local_evaluation", "workspace_required")
                    from spireagent.workbench.local_evaluation import summary

                    value = summary(workspace.store, matched[1])
                    self.respond(200, json.dumps(value, ensure_ascii=False).encode())
                except BoundaryError as error:
                    status = 404 if error.code in {"not_found", "route_not_found"} else 409
                    self.respond(status, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, KeyError, TypeError):
                    self.respond(409, b'{"error":"invalid_local_evaluation"}')
            elif (parsed.path == "/api/local-workspace"
                    or parsed.path.startswith("/api/local-workspace/artifacts/")
                    or parsed.path == "/api/local-workspace/managed"):
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    if parsed.path == "/api/local-workspace/managed":
                        if parsed.query:
                            raise ValueError
                        value = app.managed_local_workspace()
                        value.pop("workspace", None)
                        value.pop("curation_owner", None)
                        if value.get("status") == "not_created":
                            value["csrf_token"] = app.account.csrf
                    else:
                        workspace = app.local_research_workspace()
                        if workspace is None:
                            value = {
                                "schema": "stpd/local-workspace-status-v1",
                                "status": "not_configured",
                                "entry": "create a local workspace from the Workbench page",
                                "requires_cloud_account": False,
                            }
                        else:
                            artifact = re.fullmatch(
                                r"/api/local-workspace/artifacts/([a-f0-9]{64})", parsed.path
                            )
                            if artifact is not None and not parsed.query:
                                value = workspace.artifact(artifact[1])
                            elif parsed.path == "/api/local-workspace":
                                query = parse_qs(
                                    parsed.query, strict_parsing=True, keep_blank_values=True,
                                    max_num_fields=5,
                                )
                                if (
                                    any(len(items) != 1 for items in query.values())
                                    or set(query) - {
                                        "kind",
                                        "category",
                                        "q",
                                        "limit",
                                        "offset",
                                    }
                                ):
                                    raise ValueError
                                value = workspace.inventory(
                                    kind=query.get("kind", [None])[0],
                                    category=query.get("category", [None])[0],
                                    query=query.get("q", [None])[0],
                                    limit=int(query.get("limit", ["50"])[0]),
                                    offset=int(query.get("offset", ["0"])[0]),
                                )
                            else:
                                raise BoundaryError("local_workspace", "route_not_found")
                    self.respond(200, json.dumps(value, ensure_ascii=False).encode())
                except BoundaryError as error:
                    if error.stage == "managed_workspace" and error.code in {
                        "registration_invalid", "workspace_registry_invalid",
                        "workspace_storage_invalid", "workspace_marker_invalid",
                        "workspace_root_unavailable", "workspace_root_invalid",
                        "state_directory_unavailable",
                    }:
                        value = {
                            "schema": "stpd/managed-local-workspace-registration-v1",
                            "status": "unavailable",
                            "error_code": error.code,
                            "requires_cloud_account": False,
                        }
                        self.respond(200, json.dumps(value).encode())
                        return
                    if error.stage == "local_workspace" and error.code in {
                        "store_not_found",
                        "registry_not_found",
                        "unsupported_or_uninitialized_cache",
                        "registry_unavailable",
                    }:
                        self.respond(
                            200,
                            json.dumps(
                                {
                                    "schema": "stpd/local-workspace-status-v1",
                                    "status": "unavailable",
                                    "error_code": error.code,
                                    "requires_cloud_account": False,
                                }
                            ).encode(),
                        )
                        return
                    status = 404 if error.code in {"not_found", "route_not_found"} else 409
                    self.respond(status, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, KeyError):
                    self.respond(400, b'{"error":"invalid_local_workspace_request"}')
            elif parsed.path.startswith("/api/local-workspace/managed/"):
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                self.respond(404, b'{"error":"route_not_found"}')
            elif parsed.path == "/api/identity" or parsed.path.startswith("/api/project/"):
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    if parsed.path == "/api/identity":
                        value = app.account.status()
                    else:
                        route = parsed.path.removeprefix("/api/project/")
                        if parsed.query:
                            route += "?" + parsed.query
                        value = app.account.read(route)
                    self.respond(200, json.dumps(value, ensure_ascii=False).encode())
                except BoundaryError as error:
                    self.respond(
                        401 if error.code in {"sign_in_required", "http_401"} else 503,
                        json.dumps({"error": error.code}).encode(),
                    )
                except (OSError, ValueError, KeyError):
                    self.respond(503, b'{"error":"identity_unavailable"}')
            elif parsed.path.startswith("/api/console/"):
                try:
                    value = app.console.route(parsed.path[len("/api/console/") :], parsed.query)
                    self.respond(200, json.dumps(_safe_value(value), ensure_ascii=False).encode())
                except BoundaryError as error:
                    status = 404 if error.code in {"route_not_found", "record_not_found"} else 409
                    self.respond(status, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, subprocess.SubprocessError):
                    self.respond(503, b'{"error":"observation_unavailable"}')
            elif parsed.path.startswith("/assets/"):
                found = asset(parsed.path[len("/assets/") :])
                if found is None:
                    self.respond(404, b"{}")
                else:
                    kind, data = found
                    self.respond(200, data, kind)
            elif parsed.path == "/":
                self.respond(
                    200,
                    render_shell("local", "/api/console", app.config.hub_url).encode(),
                    "text/html",
                )
            else:
                self.respond(404, b"{}")

        def do_POST(self) -> None:
            if self.path.startswith("/api/native-workbench/v1/"):
                from spireagent.workbench.native_workbench_api import (
                    ACTIONS,
                    MAX_COMMAND_BYTES,
                    PREFIX,
                    bounded_response,
                )

                if not self.local_host():
                    self.respond(403, b'{"error":"native_loopback_required"}')
                    return
                action = self.path.removeprefix(PREFIX + "/actions/")
                if action not in ACTIONS:
                    self.respond(404, b'{"error":"native_action_not_supported"}')
                    return
                try:
                    body = self.json_body(maximum=MAX_COMMAND_BYTES, reject_duplicate=True)
                    payload = body.get("payload")
                    if (action in {"models.human", "models.stop"} and isinstance(payload, dict)
                            and set(payload) == {"native_request_id"}):
                        pair = app.native_access.authenticate_recovery(
                            self.headers, payload["native_request_id"])
                    else:
                        pair = app.native_access.authenticate(self.headers)
                    value = app.native_api.command(action, body, pair)
                    self.respond(200, bounded_response(value))
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError, KeyError):
                    self.respond(400, b'{"error":"invalid_native_request"}')
                return
            if self.path.startswith("/api/local-environment/"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    body = self.json_body(maximum=512)
                    if self.path == "/api/local-environment/start" and set(body) == {
                        "scenario_id"
                    }:
                        app.check_environment_instance()
                        value = app.local_environment.start(body["scenario_id"])
                    elif self.path == "/api/local-environment/start" and set(body) == {
                        "scenario_id", "scene_artifact_id"
                    }:
                        app.check_environment_instance()
                        value = app.local_environment.start(
                            body["scenario_id"], scene_artifact_id=body["scene_artifact_id"]
                        )
                    elif self.path == "/api/local-environment/scenes/save" and set(body) in (
                        {"name"}, {"name", "seed"}
                    ):
                        app.check_environment_instance()
                        value = app.local_environment.save_scene(
                            body["name"], **({"seed": body["seed"]} if "seed" in body else {})
                        )
                    elif self.path == "/api/local-environment/compare" and set(body) == {
                        "scene_artifact_id", "report_artifact_ids"
                    }:
                        app.check_environment_instance()
                        value = app.local_environment.compare(
                            body["scene_artifact_id"], body["report_artifact_ids"]
                        )
                    elif self.path == "/api/local-environment/submit" and set(body) == {
                        "session_id", "action_id", "expected_snapshot_id",
                        "expected_game_continuity_id"
                    }:
                        app.check_environment_instance()
                        value = app.local_environment.submit(
                            body["session_id"], body["action_id"],
                            body["expected_snapshot_id"],
                            body["expected_game_continuity_id"],
                        )
                    elif self.path == "/api/local-environment/stop" and set(body) == {
                        "session_id"
                    }:
                        value = app.local_environment.stop(body["session_id"])
                    elif self.path == "/api/local-environment/resume" and set(body) == {
                        "session_id", "service_instance_id", "runtime_instance_id",
                        "game_continuity_id"
                    } and all(isinstance(body[key], str) and body[key] for key in body):
                        app.check_environment_instance()
                        value = app.local_environment.resume(
                            expected_session_id=body["session_id"],
                            expected_service_instance_id=body["service_instance_id"],
                            expected_runtime_instance_id=body["runtime_instance_id"],
                            expected_game_continuity_id=body["game_continuity_id"],
                        )
                    elif self.path == "/api/local-environment/close" and set(body) in ({
                        "service_instance_id", "runtime_instance_id", "game_continuity_id"
                    }, {"session_id", "service_instance_id", "runtime_instance_id",
                        "game_continuity_id"}) and all(
                            isinstance(body[key], str) and body[key] for key in body
                        ):
                        app.check_environment_instance()
                        value = app.local_environment.close_environment(
                            expected_session_id=body.get("session_id"),
                            expected_service_instance_id=body["service_instance_id"],
                            expected_runtime_instance_id=body["runtime_instance_id"],
                            expected_game_continuity_id=body["game_continuity_id"],
                        )
                    elif self.path == "/api/local-environment/recover-control" and set(body) == {
                        "session_id", "service_instance_id", "runtime_instance_id",
                        "game_continuity_id"
                    } and all(isinstance(body[key], str) and body[key] for key in body):
                        app.check_environment_instance()
                        value = app.local_environment.recover_control(
                            expected_session_id=body["session_id"],
                            expected_service_instance_id=body["service_instance_id"],
                            expected_runtime_instance_id=body["runtime_instance_id"],
                            expected_game_continuity_id=body["game_continuity_id"],
                        )
                    elif self.path == "/api/local-environment/reports/import" and set(body) == {
                        "report_artifact_id", "purpose"
                    }:
                        app.check_environment_instance()
                        value = app.local_managed_sources.import_report(
                            body["report_artifact_id"], body["purpose"],
                        )
                    else:
                        raise ValueError
                    self.respond(200, json.dumps(value, ensure_ascii=False).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, KeyError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_environment_request"}')
                return
            if self.path.startswith("/api/local-workspace/curation/"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                if self.path != "/api/local-workspace/curation/prepare":
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    body = self.json_body(maximum=64)
                    if body:
                        raise ValueError
                    value = app.prepare_local_curation()
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_curation_request"}')
                return
            if self.path.startswith("/api/native-recording/"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                if self.path != "/api/native-recording/command":
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    body = self.json_body(maximum=4096, reject_duplicate=True)
                    value = app.control_native_recording(body)
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_native_recording_command"}')
                return
            if self.path.startswith("/api/local-recordings/preview"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                if self.path != "/api/local-recordings/preview":
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    body = self.json_body(maximum=128)
                    if set(body) != {"artifact_id"}:
                        raise ValueError
                    value = app.local_recording_preview.start(body["artifact_id"])
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_preview_request"}')
                return
            if self.path.startswith("/api/local-recordings/import"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                if self.path != "/api/local-recordings/import":
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    body = self.json_body(maximum=256)
                    if set(body) not in (
                        {"candidate_id"}, {"candidate_id", "human_origin_attested"},
                    ):
                        raise ValueError
                    value = app.start_local_recording_import(
                        body["candidate_id"], body.get("human_origin_attested"),
                    )
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_import_request"}')
                return
            if self.path.startswith("/api/local-datasets/"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    maximum = (32768 if self.path in {
                        "/api/local-datasets/human-preview", "/api/local-datasets/source3-preview",
                    } else 256)
                    body = self.json_body(maximum=maximum)
                    if self.path == "/api/local-datasets/preview":
                        if set(body) != {"artifact_id", "purpose", "paired_training"}:
                            raise ValueError
                        value = app.start_local_dataset_preview(
                            body["artifact_id"], body["purpose"], body["paired_training"],
                        )
                    elif self.path == "/api/local-datasets/human-preview":
                        if set(body) != {"artifact_ids"}:
                            raise ValueError
                        value = app.start_local_human_dataset_preview(body["artifact_ids"])
                    elif self.path == "/api/local-datasets/source3-preview":
                        if set(body) != {"artifact_ids", "cohort", "view"}:
                            raise ValueError
                        value = app.start_local_source3_dataset_preview(**body)
                    elif self.path == "/api/local-datasets/publish":
                        if set(body) != {"preview_id"}:
                            raise ValueError
                        value = app.start_local_dataset_publish(body["preview_id"])
                    else:
                        self.respond(404, b'{"error":"route_not_found"}')
                        return
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_dataset_request"}')
                return
            if self.path.startswith("/api/local-training/"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                action = self.path.removeprefix("/api/local-training/")
                if action not in {"start", "pause", "cancel", "resume", "reconcile"}:
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    body = self.json_body(maximum=4096 if action != "start" else 8192)
                    if (action == "start" and "schema" not in body and
                            int(self.headers.get("Content-Length", "0")) > 256):
                        raise ValueError
                    if action == "start" and "schema" in body:
                        from spireagent.workbench.recipe_contracts import TrainingRequest

                        value = app.start_local_training(TrainingRequest.from_dict(body))
                    elif action == "start":
                        # Keep the bounded legacy body and its original contract.
                        if (not {"dataset_id"} <= set(body) or not set(body) <= {
                                    "dataset_id", "after_completed_operation_id", "recipe"}):
                            raise ValueError
                        if ("after_completed_operation_id" in body and
                                not isinstance(body["after_completed_operation_id"], str)):
                            raise ValueError
                        value = app.start_local_training(
                            body["dataset_id"],
                            after_completed_operation_id=body.get("after_completed_operation_id"),
                            recipe=body.get("recipe", "stage1a.dsimple.s.v1"))
                    else:
                        fields = {"operation_id", "expected_attempt_id"}
                        if action == "resume":
                            fields |= {"checkpoint_id", "intent_id", "limits"}
                        if set(body) != fields:
                            raise ValueError
                        value = app.control_local_training(action, body)
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_training_request"}')
                return
            if self.path.startswith("/api/local-memory-evaluations/"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                if self.path != "/api/local-memory-evaluations/start":
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    body = self.json_body(maximum=256)
                    if (not {"model_id", "source_id"} <= set(body)
                            or not set(body) <= {
                                "model_id", "source_id", "max_settling_events"}):
                        raise ValueError
                    value = app.start_local_memory_evaluation(
                        body["model_id"], body["source_id"],
                        max_settling_events=body.get("max_settling_events"),
                    )
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_memory_evaluation_request"}')
                return
            if self.path.startswith("/api/local-model-exports/"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                if self.path != "/api/local-model-exports/start":
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    body = self.json_body(maximum=128)
                    if set(body) != {"model_id"}:
                        raise ValueError
                    value = app.start_local_model_export(body["model_id"])
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_model_export_request"}')
                return
            if self.path.startswith("/api/local-model-registrations/"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                if self.path != "/api/local-model-registrations/register":
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    body = self.json_body(maximum=128)
                    if set(body) not in ({"model_id"}, {"model_id", "environment_kind"}):
                        raise ValueError
                    value = app.register_local_model(
                        body["model_id"], environment_kind=body.get("environment_kind", "native"))
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    from spireagent.workbench.local_model_registration import (
                        RegistrationStorageError,
                    )

                    if isinstance(error, RegistrationStorageError):
                        self.respond(500, json.dumps(error.public_failure()).encode())
                    else:
                        self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_model_registration_request"}')
                return
            if self.path.startswith("/api/local-workspace/managed/"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                if self.path != "/api/local-workspace/managed/create":
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    body = self.json_body(maximum=64)
                    if body:
                        raise ValueError
                    with app.operation_lock:
                        value = app.create_managed_local_workspace()
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_managed_workspace_request"}')
                return
            if self.path.startswith(("/api/member/", "/api/local-models/")):
                if not self.browser_write() and not (
                    self.path.startswith("/api/local-models/") and self.control_client()
                ):
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    body = self.json_body()
                    if self.path.startswith("/api/local-models/"):
                        value = self.model_action(self.path, body)
                    else:
                        import re

                        route = self.path.removeprefix("/api/member/")
                        download = re.fullmatch(r"exports/([a-f0-9]{64})/download", route)
                        prepare = re.fullmatch(r"campaigns/([a-f0-9]{32})/prepare", route)
                        bind = re.fullmatch(r"campaigns/([a-f0-9]{32})/bind", route)
                        activate = re.fullmatch(r"campaigns/([a-f0-9]{32})/activate", route)
                        if route == "collection-flow/consent":
                            value = app.collection_flow.consent(body)
                        elif route == "collection-flow/prepare":
                            value = app.collection_flow.prepare(body)
                        elif route == "collection-flow/upload":
                            value = app.collection_flow.set_upload(body)
                        elif download and not body:
                            value = app.members.download(download[1])
                        elif prepare and not body:
                            value = app.members.prepare_campaign(prepare[1])
                        elif bind and isinstance(body, dict) and set(body) == {"game_directory"}:
                            with app.operation_lock:
                                value = app.collection.bind(bind[1], body["game_directory"])
                        elif activate and not body:
                            value = app.activate_collection(activate[1])
                        else:
                            value = app.members.request(route, body)
                    self.respond(200, json.dumps(value, ensure_ascii=False).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, KeyError, TypeError):
                    self.respond(400, b'{"error":"invalid_member_action"}')
                return
            if self.path.startswith("/api/identity/"):
                origin = "http://127.0.0.1:" + str(
                    cast(ThreadingHTTPServer, self.server).server_port
                )
                if (
                    not self.local_host()
                    or not self.authenticated_browser()
                    or self.headers.get("Origin") != origin
                    or not hmac.compare_digest(
                        self.headers.get("X-CSRF-Token", ""), app.account.csrf
                    )
                ):
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if (
                        not 0 < length <= 4096
                        or self.headers.get("Content-Type") != "application/json"
                    ):
                        raise ValueError
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise ValueError
                    action = self.path.removeprefix("/api/identity/")
                    if action == "login" and set(body) == {"device_name"}:
                        value = app.account.begin(body["device_name"])
                    elif action == "poll" and not body:
                        value = app.account.poll()
                    elif action == "resume-uploads" and not body:
                        value = app.resume_auth()
                    elif action == "logout" and not body:
                        app.members.close()
                        value = app.account.logout()
                        with app.console.cloud_cache.lock:
                            app.console.cloud_cache.values.clear()
                    else:
                        raise ValueError
                    self.respond(200, json.dumps(value, ensure_ascii=False).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, KeyError):
                    self.respond(400, b'{"error":"invalid_identity_action"}')
                return
            if (
                not self.local_host()
                or self.path != "/stop"
                or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + app.control_token
                )
            ):
                self.respond(403, b"{}")
                return
            self.respond(200, b'{"status":"stopping"}')
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    return server


def serve(config: ProjectConfig, *, config_path: Path | None = None) -> dict[str, Any]:
    if doctor(config)["status"] != "PASS":
        raise BoundaryError("project", "doctor_blocked")
    with service_process(config.state_dir) as resources, instance_lock(
        config.state_dir / "instance.lock"
    ):
        app = Application(config, config_path=config_path)
        server = create_server(app)
        previous_signal = signal.getsignal(signal.SIGTERM)
        signal.signal(
            signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start()
        )
        registration: WorkbenchRegistrationLoop | None = None
        try:
            app.start_delivery()
            atomic_json(
                config.state_dir / "runtime.json",
                {
                    "instance_id": app.instance_id,
                    "port": server.server_port,
                    "control_token": app.control_token,
                    "configuration_id": configuration_id(config),
                    "pid": os.getpid(),
                    "identity": app.identity,
                    "process_resources": resources,
                    "delivery": "configured" if config.delivery_config else "not_configured",
                },
            )
            # The Mod bridge belongs to this local native Connector. A remote or
            # custom Host configuration must not silently bind to another game.
            if config.platform_url in {"http://127.0.0.1:15526", "http://localhost:15526"}:
                registration = start_workbench_registration(
                    f"http://127.0.0.1:{server.server_port}/", app.instance_id,
                    access=app.native_access,
                )
            server.serve_forever(poll_interval=0.2)
        finally:
            if registration is not None:
                registration.close()
            app.close()
            server.server_close()
            (config.state_dir / "runtime.json").unlink(missing_ok=True)
            signal.signal(signal.SIGTERM, previous_signal)
    return {"status": "stopped"}
