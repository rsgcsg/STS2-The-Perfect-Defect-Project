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
from spireagent.json_boundary import BoundaryError, digest, json_bytes
from spireagent.local_diagnostics import LocalDiagnostics
from spireagent.workbench.console import LocalConsole
from spireagent.workbench.dashboard import _safe_value
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json, doctor, tool_identity
from spireagent.workbench.hub_client import HubClient
from spireagent.workbench.identity import LocalIdentity, private_read
from spireagent.workbench.instance_lock import instance_lock as instance_lock
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.member_client import MemberClient
from spireagent.workbench.native_workbench import (
    WorkbenchRegistrationLoop,
    start_workbench_registration,
)
from spireagent.workbench.service_process import service_environment, service_process

# Fixed diagnostic labels only; this table never authorizes or dispatches a request.
_DIAGNOSTIC_PATHS = frozenset({
    '/api/remote-training/prepare',
    '/api/remote-training/start',
    '/api/remote-training/start-prepared',
    '/api/remote-training/resume',
    '/api/remote-training/pause',
    '/api/remote-training/cancel',
    '/api/remote-training/reconcile',
    '/api/remote-training/auto-continue',
    '/api/remote-training/stop-continuation',
    '/api/local-training/start',
    '/api/local-training/pause',
    '/api/local-training/cancel',
    '/api/local-training/resume',
    '/api/local-training/reconcile',
    '/api/local-training/amend_wall_budget',
    '/api/local-datasets/human-preview',
    '/api/local-datasets/native-agent-preview',
    '/api/local-datasets/preview',
    '/api/local-datasets/publish',
    '/api/local-datasets/source3-preview',
    '/api/local-datasets/status',
    '/api/local-memory-evaluations/start',
    '/api/local-memory-evaluations/status',
    '/api/local-model-exports/start',
    '/api/local-model-exports/status',
    '/api/local-model-exports/support',
    '/api/local-model-registrations/register',
    '/api/local-model-registrations/register-composition',
    '/api/local-model-registrations/register-global-program',
    '/api/local-model-registrations/register-teacher',
    '/api/local-model-registrations/status',
    '/api/local-recordings',
    '/api/local-recordings/derive',
    '/api/local-recordings/derive/reconcile',
    '/api/local-recordings/import',
    '/api/local-recordings/import/native-agent',
    '/api/local-recordings/import/reconcile',
    '/api/local-recordings/import/status',
    '/api/local-recordings/maintenance',
    '/api/local-recordings/preview',
    '/api/local-recordings/preview/status',
    '/api/local-recordings/storage',
    '/api/local-recordings/storage/peer',
    '/api/local-recordings/storage/reconcile',
    '/api/local-recordings/store',
    '/api/local-training/capabilities',
    '/api/local-training/status',
    '/api/local-workspace/curation',
    '/api/local-workspace/curation/prepare',
    '/api/local-workspace/managed',
    '/api/local-workspace/managed/create',
    '/api/native-recording/command',
    '/api/native-recording/status',
    '/api/p1-data/freeze',
    '/api/p1-data/prepare',
    '/api/remote-training/capabilities',
    '/api/remote-training/progress',
    '/api/remote-training/status',
})


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


def _local_request(url: str, *, token: str | None = None, timeout: float = 2,
                   body: dict[str, Any] | None = None,
                   method: str | None = None,
                   response_limit: int = 1024 * 1024) -> dict[str, Any]:
    request = Request(
        url,
        data=(json.dumps(body).encode() if body is not None
              else b"" if token and method != "GET" else None),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})},
        method=method,
    )
    with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
        raw = response.read(response_limit + 1)
    if len(raw) > response_limit:
        raise BoundaryError("project", "local_response_too_large")
    value: Any = json.loads(raw)
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
            environment = service_environment(
                config.state_dir, cloud_environment_file=config.cloud_environment_file)
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
    def __init__(self, config: ProjectConfig, *, config_path: Path | None = None,
                 remote_provider: Any = None) -> None:
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
            config,
            hub=self.hub,
            managed_target=self.local_environment.managed_runtime_target,
            native_episode_resolver=self.local_environment.native_episode_context,
            native_episode_authorizer=self.local_environment.authorize_native_episode,
            native_episode_guard=self.local_environment.native_admission_guard,
            native_episode_associate=self.local_environment.associate_native_actor,
            native_episode_creation_guard=self.local_environment.verify_retired_native_world,
        )
        from spireagent.workbench.reference_evaluation import ReferenceEvaluationService

        self.reference_evaluation = ReferenceEvaluationService(self)
        self.models.execution_admission_guard = self.reference_evaluation.check_access
        self._native_episode_shutdown = False
        self.local_environment.native_workbench_prepare = self._stage_native_workbench
        self.local_environment.bind_native_application(
            lock=self.models.lock,
            application_instance_id=self.instance_id,
            configuration_id=self.native_episode_configuration,
            admit_prepare=self._admit_native_preparation,
            actor_lost=self.models.native_episode_driver_lost,
        )
        # Presentation intent fence only; Recorder retains its sole command ledger/lifecycle.
        self._recording_intent_lock = self.models.lock
        self._recording_intent_pending = False
        self._recording_unknown_notice: dict[str, Any] | None = None
        self._recording_unknown_blocks = False
        self._recording_runtime_context: str | None = None
        self._recording_previous_unknown: list[dict[str, Any]] = []
        self._recording_last_result: dict[str, Any] | None = None
        intent = private_read(config.state_dir / "native-recording-intent.json")
        if intent:
            if intent.get("schema") != "spireagent/native-recording-intent-1":
                raise BoundaryError("recording", "invalid_saved_recording_intent")
            self._recording_last_result = intent.get("last_result")
            self._recording_unknown_notice = intent["unconfirmed"]
            self._recording_unknown_blocks = intent["blocks"]
            self._recording_previous_unknown = intent["previous_unconfirmed"]
            if intent["pending"] is not None:
                if self._recording_unknown_notice is not None:
                    self._recording_previous_unknown.append(self._recording_unknown_notice)
                self._recording_unknown_notice = intent["pending"]
                self._recording_unknown_blocks = True
        self.models.bind_recording_admission_guard(self._recording_model_admission_blocked)
        self.local_managed_sources = LocalManagedSourceService(config, self.local_environment)
        self.local_recordings = LocalRecordingCatalog(config)
        # Keep command-time owner observations separate from concurrent browser GET scans.
        self.local_recording_import = LocalRecordingImporter(config, LocalRecordingCatalog(config))
        self.local_recording_import.global_run_retired = self.local_environment.global_run_retired
        self.local_recording_import.operation_admission_guard = (
            self.reference_evaluation.check_access)
        from .p1_recording_maintenance import P1RecordingMaintenance

        self.p1_maintenance = P1RecordingMaintenance(self.local_recording_import)
        self.local_recording_preview = LocalRecordingPreview(self.local_research_workspace)
        self.local_datasets = LocalDatasetService(config)
        self.local_training = LocalTrainingService(config)
        from spireagent.workbench.remote_training import RemoteTrainingService

        self.remote_training = RemoteTrainingService(config, self.local_datasets._selected,
                                                     provider=remote_provider)
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

    def native_episode_configuration(self) -> str:
        if self._native_episode_shutdown:
            raise BoundaryError("native_episode", "application_closing")
        self.check_environment_instance()
        return configuration_id(self.config)

    def _admit_native_preparation(self) -> None:
        # Called only under the existing shared Model admission lock. Native
        # reservation itself is committed by Environment before that lock exits.
        self.models._require_source_admission()
        self.local_recording_import.require_publication_admission()
        if (
            self._native_episode_shutdown
            or self.models.closed
            or self.models.state.get("status") in {"command_unknown", "recovery_required"}
            or (
                self.models.client is not None
                or self.models.state.get("loaded") is True
                or self.models.process is not None
                and self.models.process.poll() is None
                or self.models.thread is not None
                and self.models.thread.is_alive()
            )
        ):
            raise BoundaryError("native_episode", "actor_active_or_unknown")

    def _stage_native_workbench(self, identity: dict[str, Any], state_root: Path) -> None:
        """Use the exact returned Host profile; never change the daily selected slot."""
        import sys

        from .native_workbench_access import (
            NativeBootstrap,
            selected_directory,
            stage_selected_native_access,
        )

        # Native embedding is opt-in through the original selected launcher. A
        # normal CLI-only preparation does not gain a bootstrap requirement.
        if (sys.platform != "darwin" or self.config_path is None
                or not (selected_directory() / "native-access.json").exists()):
            return
        try:
            NativeBootstrap.read(selected_directory(), self.config_path)
        except BoundaryError as error:
            if error.code == "native_configuration_mismatch":
                return  # A different selected launcher is not this App's opt-in.
            raise
        self.native_access.bootstrap()  # Exact current App source/config identity.
        profile = identity["profile"]
        root = Path(profile["profile_root"])
        profiles = state_root / "profiles"
        if (not root.is_absolute() or root.resolve() != root or root.parent != profiles
                or root.is_symlink() or not root.is_dir()):
            raise BoundaryError("native_workbench", "native_owned_profile_required")
        marker = json.loads((root / "headless-profile.json").read_bytes())
        if (marker.get("generation_id") != profile.get("generation_id")
                or marker.get("profile_id") != profile.get("profile_id")):
            raise BoundaryError("native_workbench", "native_owned_profile_changed")
        stage_selected_native_access(root / "home", self.config_path,
                                     identity["host"]["implementation"]["artifact_sha256"])

    def _workbench_runtime_identity(self, runtime: str) -> bool:
        """The fixed native bridge and selected Connector must identify the same game."""
        try:
            self.check_environment_instance()
            selected = self.models.native_tasks.connector_instance(self.config.platform_url)
            if selected != runtime:
                return False
            record = self.local_environment.native_episode_status()
            if record.get("phase") in {"idle", "closed", "failed"}:
                # Terminal resources retain historical admission identity. Only
                # an active resource constrains the selected game's new pair.
                return True
            context = record.get("admission_context")
            return (not isinstance(context, dict)
                    or context["binding"]["runtime_instance_id"] == runtime)
        except (OSError, ValueError, KeyError):
            return False

    def model_agent_request(self, identity: str, operation: str, run_profile: str,
                            *, native_episode_ref: object | None = None,
                            global_no_progress: object = None) -> dict[str, Any]:
        """One GUI/CLI/API mapping from selected Model to exact execution binding."""
        self.check_environment_instance()
        original = self.models.agent_request(identity, operation, run_profile,
            native_episode_ref=native_episode_ref, global_no_progress=global_no_progress)
        if native_episode_ref is not None:
            context = self.local_environment.native_episode_context(native_episode_ref)
            with self.models.lock:
                record = self.local_environment.native_episode_status()
                if record.get("phase") == "actor-associated":
                    actor = record.get("actor")
                    if not isinstance(actor, dict):
                        raise BoundaryError("native_episode", "actor_retirement_binding_changed")
                    proof = self.models.native_actor_retirement_proof(context, actor["run_id"])
                    self.local_environment.verify_native_actor_reuse(context)
                    self.local_environment.retire_native_actor(context, proof)
                self.models._require_model_admission(context, action="creation")
            identity = self.local_model_registration.bind_reference_selection(identity, context)
            if identity == original["selection_id"]:
                return original
        else:
            return original
        return self.models.agent_request(identity, operation, run_profile,
            native_episode_ref=native_episode_ref, global_no_progress=global_no_progress)

    def prepare_native_episode(self, value: object) -> dict[str, Any]:
        with self.models.lock:
            self.reference_evaluation.check_access()
        self.check_environment_instance()
        return self.local_environment.prepare_native_episode(value)

    def native_episode_status(self) -> dict[str, Any]:
        return self.local_environment.native_episode_status()

    def prepare_current_native_episode(self, runtime_id: str,
                                       environment_ref: object | None = None) -> str:
        """Bind a display to the current game, without resetting an attached game.

        Owned seeded episodes retain their exact reuse rules. An attached display
        starts a fresh cold segment only after the previous Actor has a clean seal.
        """
        self.check_environment_instance()
        record = self.native_episode_status()
        if environment_ref is not None and record.get("environment_ref") != environment_ref:
            raise BoundaryError("native_episode", "resource_binding_changed")
        if record.get("preparation", {}).get("ownership") == "owned":
            context = self.local_environment.native_episode_context(record["environment_ref"])
            if context["binding"]["runtime_instance_id"] != runtime_id:
                raise BoundaryError("native_episode", "native_model_context_changed")
            return cast(str, record["environment_ref"])
        if record.get("phase") == "actor-associated":
            context = self.local_environment.native_episode_context(record["environment_ref"])
            # This proof rejects running, tainted and unresolved Actors; Start never
            # implicitly stops an old model or retries its unknown operation.
            self.models.native_actor_retirement_proof(context, record["actor"]["run_id"])
            record = self.finish_native_episode(record["environment_ref"])
        if record.get("phase") in {"idle", "closed", "failed"}:
            record = self.prepare_native_episode({
                "ownership": "attached", "intent_id": secrets.token_hex(16)})
            worker = self.local_environment.native_worker
            if worker is not None:
                worker.join(timeout=10)
            record = self.native_episode_status()
        if record.get("phase") != "ready":
            raise BoundaryError("native_episode", "native_preparation_unconfirmed")
        context = self.local_environment.native_episode_context(record["environment_ref"])
        if context["binding"]["runtime_instance_id"] != runtime_id:
            raise BoundaryError("native_episode", "native_model_context_changed")
        return cast(str, record["environment_ref"])

    def finish_native_episode(self, environment_ref: object) -> dict[str, Any]:
        self.reference_evaluation.check_access("stop")
        record, client, first = self.local_environment.begin_native_finish(environment_ref)
        if not first:
            return record
        actor: dict[str, Any] = {
            "status": "not_associated",
            "run_id": None,
            "seal": "not_applicable",
        }
        context = record.get("admission_context")
        try:
            if isinstance(context, dict):
                # No Environment lifecycle lock is held across Model Stop/seal.
                actor = self.models.stop_native_episode_actor(
                    context, timeout=record["cleanup_timeout_seconds"]
                )
        except (OSError, ValueError) as error:
            actor = {
                "status": "unknown",
                "error_code": error.code
                if isinstance(error, BoundaryError)
                else "actor_stop_or_seal_failed",
            }
        return self.local_environment.close_native_after_actor(record, client, actor)

    def native_unknown_preview(self) -> dict[str, Any]:
        with self.models.lock, self.local_recording_import.lock:
            result = self.local_environment.native_unknown_preview()
            publication = self.local_recording_import.global_unknown_snapshot()
            if publication is not None:
                result["expected"].update(
                    expected_publication_operation_id=publication["operation_id"],
                    expected_publication_run_id=publication["run_id"],
                    expected_publication_sha256=publication["sha256"])
                result["publication"] = {
                    k: publication[k] for k in ("operation_id", "run_id", "sha256")}
                result["publication"]["requires_confirmation"] = (
                    not self.local_recording_import.global_unknown_released())
            return result

    def seal_native_unknown(self, body: dict[str, Any]) -> dict[str, Any]:
        """An operator release is not a retry or a successful Actor evaluation."""
        from .native_unknown import durable_readback, no_handles

        with self.models.lock, self.local_recording_import.lock:
            self.reference_evaluation.check_access("stop")
            self.models._require_stopped_runtime()
            if self.models.thread is not None and self.models.thread.is_alive():
                raise BoundaryError("native_episode", "actor_still_active")
            if (self.local_recording_import.thread is not None
                    and self.local_recording_import.thread.is_alive()):
                raise BoundaryError("native_episode", "publication_worker_still_active")
            preview = self.native_unknown_preview()
            if body != preview["expected"]:
                raise BoundaryError("native_episode", "native_unknown_release_identity_mismatch")
            if (preview.get("release") is not None
                    and not (preview.get("publication") or {}).get("requires_confirmation")):
                return {**preview["release"], "expected": body}
            no_handles(self.models.directory)
            native_expected = self.local_environment.native_unknown_preview()["expected"]
            publication = self.local_recording_import.global_unknown_snapshot()
            related = None
            store = None
            if publication is not None:
                if publication["run_id"] != native_expected["expected_run_id"]:
                    raise BoundaryError("native_episode", "publication_native_run_mismatch")
                retained = self.local_environment._native_operation_store(create=True)
                assert retained is not None
                store = retained[0]
                raw = json_bytes({
                    "schema": "spireagent/global-publication-unknown-snapshot-v1",
                    "result": "unknown", "operation_id": publication["operation_id"],
                    "run_id": publication["run_id"], "original_sha256": publication["sha256"],
                    "original_utf8": publication["raw"].decode("utf-8")})
                key = (f"operations/{native_expected['expected_environment_ref']}/publication/"
                       f"{publication['operation_id']}/{publication['sha256']}.json")
                store.put_if_absent(key, raw)
                durable_readback(store.root / key, raw)
                related = {"key": key, "sha256": hashlib.sha256(raw).hexdigest(),
                           "operation_id": publication["operation_id"],
                           "operation_sha256": publication["sha256"],
                           "run_id": publication["run_id"]}
            try:
                result = self.local_environment.seal_native_unknown(
                    native_expected, publication=related)
                if related is not None:
                    assert store is not None
                    key = f"retired-global-publications/{related['run_id']}.json"
                    retired = json_bytes({"schema": "spireagent/retired-global-publication-v1",
                                          "run_id": related["run_id"], "result": "unknown"})
                    store.put_if_absent(key, retired)
                    durable_readback(store.root / key, retired)
                    self.local_recording_import._released_global_unknown = {
                        "operation_id": related["operation_id"],
                        "operation_sha256": related["operation_sha256"], "store": store,
                        "archive": related, "confirmation": result["confirmation"]}
                    if not self.local_recording_import.global_unknown_released():
                        raise BoundaryError("local_import", "global_unknown_release_unconfirmed")
                return {**result, "expected": body}
            except (OSError, ValueError):
                ref = native_expected["expected_environment_ref"]
                self.local_environment._native_unknown_confirmations.pop(ref, None)
                self.local_recording_import._released_global_unknown = None
                raise

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

    def _save_recording_intent(self, pending: dict[str, Any] | None = None) -> None:
        # One presentation intent, not another Recorder command ledger or state machine.
        notice = ({key: pending[key] for key in (
            "command_id", "kind", "runtime_instance_id", "recording_session_id")}
            if pending is not None else None)
        atomic_json(self.config.state_dir / "native-recording-intent.json", {
            "schema": "spireagent/native-recording-intent-1", "pending": notice,
            "unconfirmed": self._recording_unknown_notice,
            "blocks": self._recording_unknown_blocks,
            "previous_unconfirmed": self._recording_previous_unknown[-8:],
            "last_result": self._recording_last_result,
        })

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
            "last_result": self._recording_last_result,
            "connection": endpoint,
            "model_recovery_required": self._recording_model_recovery_required(),
            "command_pending": pending,
            "unconfirmed": notice,
            "recovery_required": blocked,
            "previous_unconfirmed": previous,
        }

    def control_native_recording(self, body: dict[str, Any]) -> dict[str, Any]:
        from uuid import UUID, uuid4

        from spireagent.workbench.native_tasks import P1_ORIGINS, P1_PROFILE, NativeTasks

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
            or set(body) not in (fields, fields | {"capture_profile_id"})
            or (
                "capture_profile_id" in body
                and (
                    body.get("kind") != "start_new_session"
                    or not isinstance(body["capture_profile_id"], str)
                    or body["capture_profile_id"] not in {
                        "native-logical-source-v3", "native-logical-source-v4", P1_PROFILE
                    }
                )
            )
            or not isinstance(body["kind"], str)
            or body["kind"]
            not in {"start_new_session", "pause", "resume", "change_source", "close",
                    "export_p1_native_prefix"}
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
        p1_start = body.get("capture_profile_id") == P1_PROFILE
        if p1_start:
            if body["source_kind"] not in P1_ORIGINS or body["actor_id"] is not None:
                raise BoundaryError("recording", "invalid_native_recording_command")
        elif body["kind"] in {"start_new_session", "change_source"}:
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
            require_human=p1_start or (declaration is not None
                                       and body["source_kind"] != "agent_protocol")
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
                    (p1_start or declaration is not None)
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
                self._save_recording_intent(body)
                value = self.models.native_tasks.recording_command(
                    endpoint,
                    observed,
                    body["kind"],
                    source_declaration=declaration,
                    command_id=body["command_id"],
                    **({"capture_profile_id": body["capture_profile_id"]}
                       if "capture_profile_id" in body else {}),
                    **({"p1_native_origin": body["source_kind"]} if p1_start else {}),
                )
                if not value["accepted"]:
                    raise BoundaryError("recording", "native_recording_rejected")
                with self._recording_intent_lock:
                    self._recording_last_result = value
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
                    self._save_recording_intent()

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

    def start_local_native_agent_import(
        self, directory: object, cohort: object, relation_id: object, intent_id: object = None,
    ) -> dict[str, Any]:
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
        return self.local_recording_import.start_native_agent_run(
            directory, cohort, relation_id, intent_id)

    def start_local_native_agent_dataset_preview(self, artifact_ids: object) -> dict[str, Any]:
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
        return self.local_datasets.start_native_agent_preview(artifact_ids)

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
        if action == "amend_wall_budget":
            return self.local_training.amend_wall_budget(**body)
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

    def start_global_evidence(self, run_id: object, peer: object = None) -> dict[str, Any]:
        """Preserve a stopped owned Model run through the existing data operation."""
        self.check_environment_instance()
        if peer is not None and not isinstance(peer, dict):
            raise BoundaryError("p1_storage", "invalid_peer_binding")
        context = self.models.stopped_agent_evidence_context(run_id)
        return self.local_recording_import.start_global_evidence(context, peer)

    def reconcile_local_recording_import(self) -> dict[str, Any]:
        """Read the retained original import intent through its owning service."""
        self.check_environment_instance()
        importer = self.local_recording_import
        with importer.lock:
            if importer.status().get("action") == "global-evidence":
                return importer.reconcile_global_evidence()
            return importer.reconcile_p1_import()

    def register_local_model(self, model_id: object, *,
                             environment_kind: str = "native",
                             composition_options: dict[str, str] | None = None) -> dict[str, Any]:
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
        if composition_options is not None:
            return self.local_model_registration.register_composition(
                model_id, environment_kind=environment_kind, **composition_options
            )
        return self.local_model_registration.register(model_id, environment_kind=environment_kind)

    def register_builtin_teacher(
        self,
        *,
        variant: str = "continuous",
        global_program: bool = False,
        environment_kind: str = "native",
    ) -> dict[str, Any]:
        if self.config_path is None:
            raise BoundaryError("local_model_registration", "running_instance_unavailable")
        try:
            current = ProjectConfig.load(self.config_path)
            runtime = json.loads((self.config.state_dir / "runtime.json").read_text())
        except (OSError, ValueError, TypeError, BoundaryError) as error:
            raise BoundaryError(
                "local_model_registration", "running_instance_unavailable"
            ) from error
        if (not isinstance(runtime, dict) or current != self.config
                or runtime.get("instance_id") != self.instance_id
                or runtime.get("configuration_id") != configuration_id(self.config)):
            raise BoundaryError("local_model_registration", "running_configuration_mismatch")
        if global_program:
            return self.local_model_registration.register_global_program(
                environment_kind=environment_kind
            )
        return self.local_model_registration.register_teacher(variant=variant)

    def close(self) -> None:
        self.reference_evaluation.close()
        self.remote_training.close()
        self.p1_maintenance.close()
        # The same Application admission lock fences an in-flight handler before
        # this snapshot. Recovery/Stop remains enabled until its original owner
        # finishes; no lock is held across Stop, sealing or Host cleanup.
        with self.models.lock:
            self._native_episode_shutdown = True
            native = self.local_environment.native_episode_status()
        if native.get("environment_ref") is not None and native.get("phase") != "closed":
            with contextlib.suppress(OSError, ValueError), self.reference_evaluation.scope():
                self.finish_native_episode(native["environment_ref"])
            self.local_environment.native_finish_done.wait(timeout=90)
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
    diagnostics = LocalDiagnostics(app.config.state_dir)

    class Handler(BaseHTTPRequestHandler):
        def handle_one_request(self) -> None:
            with diagnostics.operation("http", "request"):
                super().handle_one_request()

        def diagnostic_bad_request(self, error: Exception, code: str, route: str) -> None:
            parsed_path = urlsplit(self.path).path
            safe_path = parsed_path if parsed_path in _DIAGNOSTIC_PATHS else route
            action = (safe_path.rsplit("/", 1)[-1]
                      if parsed_path in _DIAGNOSTIC_PATHS else self.command)
            error_id = diagnostics.error(error, path=safe_path, action=action, stage=safe_path)
            self.respond(400, json.dumps({"error": code, "error_id": error_id}).encode())

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
            if (path == "/api/local-models/request"
                    and {"selection_id", "operation", "run_profile"} <= set(body)
                    and not set(body) - {"selection_id", "operation", "run_profile",
                                         "native_episode_ref", "global_no_progress"}):
                app.check_environment_instance()
                return app.model_agent_request(
                    body["selection_id"],
                    body["operation"],
                    body["run_profile"],
                    **({"global_no_progress": body["global_no_progress"]}
                       if "global_no_progress" in body else {}),
                    **(
                        {"native_episode_ref": digest(body["native_episode_ref"],
                            "native_episode.reference", length=32)}
                        if "native_episode_ref" in body
                        else {}
                    ),
                )
            if path == "/api/local-models/run-agent":
                return app.models.run_agent(body)
            if (path == "/api/local-models/evidence/publish"
                    and set(body) in ({"run_id"}, {"run_id", "peer"})):
                return app.start_global_evidence(body["run_id"], body.get("peer"))
            if (path == "/api/local-models/prepare" and "selection_id" in body
                    and not set(body) - {"selection_id", "run_profile", "native_episode_ref"}):
                if "native_episode_ref" in body:
                    original = app.model_agent_request(body["selection_id"], "prepare-and-load",
                        body.get("run_profile", "short"),
                        native_episode_ref=digest(body["native_episode_ref"],
                            "native_episode.reference", length=32))
                    return app.models.run_agent(original)
                return app.models.prepare_and_load(
                    body["selection_id"], body.get("run_profile", "short")
                )
            if path == "/api/local-models/share" and set(body) == {"evaluation_id", "authorized"}:
                return app.evaluation_sharing.share(body["evaluation_id"], body["authorized"])
            if path == "/api/local-models/start" and set(body) in (
                {"selection_id"}, {"selection_id", "run_profile"}
            ):
                return app.models.start(body["selection_id"], body.get("run_profile", "short"))
            if path == "/api/local-models/export-checkpoint" and set(body) in (
                {"checkpoint_id"}, {"checkpoint_id", "selection"}
            ):
                return app.models.export_checkpoint(
                    body["checkpoint_id"], body.get("selection", "best"))
            if path == "/api/local-models/download" and set(body) in (
                {"artifact_id"}, {"artifact_id", "source"}
            ):
                return app.models.prepare(body["artifact_id"], body.get("source", "configured"))
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
            elif parsed.path == "/api/reference-evaluations/status":
                if not self.authenticated_browser() and not self.control_client():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_reference_group_request"}')
                    return
                self.respond(200, json.dumps({**app.reference_evaluation.status(),
                                              "csrf_token": app.account.csrf}).encode())
            elif (
                parsed.path == "/api/local-environment"
                or parsed.path == ("/api/local-environment/reports")
                or parsed.path == ("/api/local-environment/scenes")
                or parsed.path == ("/api/local-environment/comparisons")
                or parsed.path == ("/api/local-environment/native")
                or parsed.path == "/api/local-environment/native/unknown-release"
                or parsed.path.startswith(
                    (
                        "/api/local-environment/reports/",
                        "/api/local-environment/events/",
                        "/api/local-environment/scenes/",
                        "/api/local-environment/comparisons/",
                    )
                )
            ):
                if not self.authenticated_browser() and not (
                    parsed.path in {"/api/local-environment/native",
                                    "/api/local-environment/native/unknown-release"}
                    and self.control_client()
                ):
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    if parsed.query:
                        raise ValueError
                    if parsed.path == "/api/local-environment/native":
                        value = app.native_episode_status()
                    elif parsed.path == "/api/local-environment/native/unknown-release":
                        value = app.native_unknown_preview()
                    elif parsed.path == "/api/local-environment":
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
                if not self.local_host() or not (
                        self.authenticated_browser() or self.control_client()):
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
            elif parsed.path == "/api/local-recordings/store":
                if not self.authenticated_browser() and not self.control_client():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    from .p1_recording_store import query_p1_sources

                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if any(len(values) != 1 for values in query.values()):
                        raise ValueError
                    filters = {key: json.loads(values[0]) for key, values in query.items()}
                    workspace = app.local_research_workspace()
                    if workspace is None:
                        raise BoundaryError("p1_store", "workspace_required")
                    rows = query_p1_sources(workspace.store, filters=filters,
                        storage_receipts=app.local_recording_import.status()["storage_receipts"])
                    self.respond(200, json.dumps({"sources": rows}).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_p1_query",
                                                "/api/local-recordings/store")
            elif parsed.path == "/api/local-recordings/maintenance":
                if not self.authenticated_browser() and not self.control_client():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                value = app.local_recording_import.status()["maintenance"]
                self.respond(200, json.dumps(value).encode())
            elif parsed.path == "/api/local-recordings/storage/peer":
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                from .p1_recording_storage import peer_binding

                try:
                    value = {"status": "configured", "peer": peer_binding("s3")}
                except (BoundaryError, ValueError, ImportError):
                    value = {"status": "unconfigured", "peer": None}
                self.respond(200, json.dumps(value).encode())
            elif parsed.path == "/api/local-recordings/import/status":
                if not self.authenticated_browser() and not self.control_client():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                if parsed.query:
                    self.respond(400, b'{"error":"invalid_local_import_request"}')
                    return
                value = app.local_recording_import.status()
                if self.authenticated_browser():
                    value = {**value, "csrf_token": app.account.csrf,
                             "workbench_instance_id": app.instance_id,
                             "configuration_id": configuration_id(app.config)}
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
            elif parsed.path == "/api/remote-training/progress":
                if not (self.authenticated_browser() or self.control_client()):
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=2,
                                     keep_blank_values=True)
                    if (set(query) not in ({"job_id"}, {"job_id", "after_step"})
                            or any(len(values) != 1 for values in query.values())):
                        raise ValueError
                    value = app.remote_training.progress(
                        query["job_id"][0], after_step=int(query.get("after_step", ["0"])[0]))
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_remote_training_request",
                                                "/api/remote-training/progress")
            elif parsed.path in {"/api/remote-training/status",
                                 "/api/remote-training/capabilities"}:
                if not (self.authenticated_browser() or self.control_client()):
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    if parsed.path.endswith("/capabilities"):
                        if parsed.query:
                            raise ValueError
                        value = app.remote_training.capabilities()
                    else:
                        query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=1,
                                         keep_blank_values=True)
                        if query and (set(query) not in ({"job_id"}, {"intent_id"})
                                      or len(next(iter(query.values()))) != 1):
                            raise ValueError
                        if "intent_id" in query:
                            value = app.remote_training.preparation_status(query["intent_id"][0])
                        else:
                            value = app.remote_training.status(query.get("job_id", [None])[0])
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_remote_training_request",
                                                "/api/remote-training/{status,capabilities}")
            elif parsed.path in {"/api/local-training/status", "/api/local-training/capabilities"}:
                if not (self.authenticated_browser() or self.control_client()):
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
                    self.respond(200, json.dumps({**value, **(
                        {"csrf_token": app.account.csrf} if self.authenticated_browser() else {})
                    }).encode())
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
            elif parsed.path == "/api/local-model-exports/support":
                if not self.authenticated_browser():
                    self.respond(401, b'{"error":"browser_session_required"}')
                    return
                try:
                    query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=1,
                                     keep_blank_values=True)
                    if set(query) != {"model_id"} or len(query["model_id"]) != 1:
                        raise ValueError
                    value = app.local_model_export.support(query["model_id"][0])
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (ValueError, TypeError):
                    self.respond(400, b'{"error":"invalid_local_model_export_request"}')
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
                if not self.authenticated_browser() and not self.control_client():
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
            nonce = self.headers.get("X-Reference-Group-Scope")
            if nonce is None:
                return self.application_post()
            if not self.control_client():
                self.respond(403, b'{"error":"local_control_authentication_required"}')
                return
            try:
                with app.reference_evaluation.request_scope(nonce):
                    self.application_post()
            except BoundaryError as error:
                self.respond(403, json.dumps({"error": error.code}).encode())

        def application_post(self) -> None:
            if self.path.startswith("/api/reference-evaluations/"):
                if not self.browser_write() and not self.control_client():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    app.check_environment_instance()
                    body = self.json_body(maximum=8192, reject_duplicate=True)
                    action = self.path.removeprefix("/api/reference-evaluations/")
                    if action == "start":
                        value = app.reference_evaluation.start(body)
                    elif action == "reconcile":
                        value = app.reference_evaluation.reconcile(body)
                    elif action in {"pause", "resume", "stop"}:
                        value = app.reference_evaluation.control(action, body)
                    else:
                        raise BoundaryError("reference_evaluation", "route_not_found")
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError, KeyError):
                    self.respond(400, b'{"error":"invalid_reference_group_request"}')
                return
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
                if not self.browser_write() and not (
                    self.path.startswith("/api/local-environment/native/") and self.control_client()
                ):
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    with app.models.lock:
                        app.reference_evaluation.check_access(
                            "stop" if self.path.endswith("/finish") else "creation")
                    native_route = self.path.startswith("/api/local-environment/native/")
                    body = self.json_body(
                        maximum=1024 if native_route else 512, reject_duplicate=native_route
                    )
                    if self.path == "/api/local-environment/native/prepare":
                        value = app.prepare_native_episode(body)
                    elif self.path == "/api/local-environment/native/seal-unknown":
                        value = app.seal_native_unknown(body)
                    elif (self.path == "/api/local-environment/native/cool-evidence"
                          and set(body) == {"run_id"}):
                        from .evaluation_cache import cool_completed

                        value = cool_completed(app, body["run_id"])
                    elif self.path == "/api/local-environment/native/finish" and set(body) == {
                        "environment_ref"
                    }:
                        value = app.finish_native_episode(body["environment_ref"])
                    elif self.path == "/api/local-environment/start" and set(body) == {
                        "scenario_id"
                    }:
                        app.check_environment_instance()
                        value = app.local_environment.start(body["scenario_id"])
                    elif self.path == "/api/local-environment/start" and set(body) == {
                        "scenario_id",
                        "scene_artifact_id",
                    }:
                        app.check_environment_instance()
                        value = app.local_environment.start(
                            body["scenario_id"], scene_artifact_id=body["scene_artifact_id"]
                        )
                    elif self.path == "/api/local-environment/scenes/save" and set(body) in (
                        {"name"},
                        {"name", "seed"},
                    ):
                        app.check_environment_instance()
                        value = app.local_environment.save_scene(
                            body["name"], **({"seed": body["seed"]} if "seed" in body else {})
                        )
                    elif self.path == "/api/local-environment/compare" and set(body) == {
                        "scene_artifact_id",
                        "report_artifact_ids",
                    }:
                        app.check_environment_instance()
                        value = app.local_environment.compare(
                            body["scene_artifact_id"], body["report_artifact_ids"]
                        )
                    elif self.path == "/api/local-environment/submit" and set(body) == {
                        "session_id",
                        "action_id",
                        "expected_snapshot_id",
                        "expected_game_continuity_id",
                    }:
                        app.check_environment_instance()
                        value = app.local_environment.submit(
                            body["session_id"],
                            body["action_id"],
                            body["expected_snapshot_id"],
                            body["expected_game_continuity_id"],
                        )
                    elif self.path == "/api/local-environment/stop" and set(body) == {"session_id"}:
                        value = app.local_environment.stop(body["session_id"])
                    elif (
                        self.path == "/api/local-environment/resume"
                        and set(body)
                        == {
                            "session_id",
                            "service_instance_id",
                            "runtime_instance_id",
                            "game_continuity_id",
                        }
                        and all(isinstance(body[key], str) and body[key] for key in body)
                    ):
                        app.check_environment_instance()
                        value = app.local_environment.resume(
                            expected_session_id=body["session_id"],
                            expected_service_instance_id=body["service_instance_id"],
                            expected_runtime_instance_id=body["runtime_instance_id"],
                            expected_game_continuity_id=body["game_continuity_id"],
                        )
                    elif (
                        self.path == "/api/local-environment/close"
                        and set(body)
                        in (
                            {"service_instance_id", "runtime_instance_id", "game_continuity_id"},
                            {
                                "session_id",
                                "service_instance_id",
                                "runtime_instance_id",
                                "game_continuity_id",
                            },
                        )
                        and all(isinstance(body[key], str) and body[key] for key in body)
                    ):
                        app.check_environment_instance()
                        value = app.local_environment.close_environment(
                            expected_session_id=body.get("session_id"),
                            expected_service_instance_id=body["service_instance_id"],
                            expected_runtime_instance_id=body["runtime_instance_id"],
                            expected_game_continuity_id=body["game_continuity_id"],
                        )
                    elif (
                        self.path == "/api/local-environment/recover-control"
                        and set(body)
                        == {
                            "session_id",
                            "service_instance_id",
                            "runtime_instance_id",
                            "game_continuity_id",
                        }
                        and all(isinstance(body[key], str) and body[key] for key in body)
                    ):
                        app.check_environment_instance()
                        value = app.local_environment.recover_control(
                            expected_session_id=body["session_id"],
                            expected_service_instance_id=body["service_instance_id"],
                            expected_runtime_instance_id=body["runtime_instance_id"],
                            expected_game_continuity_id=body["game_continuity_id"],
                        )
                    elif self.path == "/api/local-environment/reports/import" and set(body) == {
                        "report_artifact_id",
                        "purpose",
                    }:
                        app.check_environment_instance()
                        value = app.local_managed_sources.import_report(
                            body["report_artifact_id"],
                            body["purpose"],
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
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_local_curation_request",
                                                "/api/local-workspace/curation/prepare")
                return
            if self.path.startswith("/api/native-recording/"):
                if not self.browser_write() and not self.control_client():
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
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_native_recording_command",
                                                "/api/native-recording/command")
                return
            if self.path == "/api/local-recordings/maintenance":
                if not self.browser_write() and not self.control_client():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    body = self.json_body(maximum=2048, reject_duplicate=True)
                    if (not {"enabled", "peer", "projection_id"} <= set(body)
                            or set(body) - {"enabled", "peer", "projection_id", "scope",
                                            "cool_recent"}):
                        raise ValueError
                    value = app.p1_maintenance.configure(**body)
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_p1_maintenance_policy",
                                                "/api/local-recordings/maintenance")
                return
            if self.path in {"/api/local-recordings/storage",
                              "/api/local-recordings/storage/reconcile"}:
                if not self.browser_write() and not self.control_client():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    body = self.json_body(maximum=1024, reject_duplicate=True)
                    if self.path == "/api/local-recordings/storage" and set(body) == {
                            "action", "artifact_id", "peer"}:
                        value = app.local_recording_import.start_p1_storage(**body)
                    elif self.path == "/api/local-recordings/storage/reconcile" and not body:
                        value = app.local_recording_import.reconcile_p1_storage()
                    else:
                        raise ValueError
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_p1_storage_request",
                                                "/api/local-recordings/storage")
                return
            if self.path.startswith("/api/local-recordings/derive"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    body = self.json_body(maximum=1024, reject_duplicate=True)
                    if self.path == "/api/local-recordings/derive" and set(body) == {
                            "source_artifact_id", "projection_id", "run_id"}:
                        value = app.local_recording_import.start_p1_derivation(**body)
                    elif self.path == "/api/local-recordings/derive/reconcile" and not body:
                        value = app.local_recording_import.reconcile_p1_derivation()
                    else:
                        raise ValueError
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_p1_derivation_request",
                                                "/api/local-recordings/derive")
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
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_local_preview_request",
                                                "/api/local-recordings/preview")
                return
            if self.path.startswith("/api/local-recordings/import"):
                if not self.browser_write() and not (
                        self.path == "/api/local-recordings/import/reconcile"
                        and self.control_client()):
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                if self.path not in {"/api/local-recordings/import",
                                     "/api/local-recordings/import/reconcile",
                                     "/api/local-recordings/import/native-agent"}:
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    if self.path == "/api/local-recordings/import/reconcile":
                        body = self.json_body(maximum=64)
                        if body:
                            raise ValueError
                        value = app.reconcile_local_recording_import()
                    elif self.path == "/api/local-recordings/import/native-agent":
                        body = self.json_body(maximum=8192)
                        if set(body) not in (
                            {"directory", "cohort", "relation_id"},
                            {"directory", "cohort", "relation_id", "intent_id"},
                        ):
                            raise ValueError
                        if "intent_id" in body:
                            digest(body["intent_id"], "local_import.intent_id", length=32)
                        value = app.start_local_native_agent_import(**body)
                    else:
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
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_local_import_request",
                                                "/api/local-recordings/import")
                return
            if self.path.startswith("/api/local-datasets/"):
                if not self.browser_write():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    maximum = (32768 if self.path in {
                        "/api/local-datasets/human-preview", "/api/local-datasets/source3-preview",
                        "/api/local-datasets/native-agent-preview",
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
                    elif self.path == "/api/local-datasets/native-agent-preview":
                        if set(body) != {"artifact_ids"}:
                            raise ValueError
                        value = app.start_local_native_agent_dataset_preview(body["artifact_ids"])
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
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_local_dataset_request",
                                                "/api/local-datasets/:action")
                return
            if self.path.startswith("/api/p1-data/"):
                if not (self.browser_write() or self.control_client()):
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                try:
                    from spireagent.source import source_identity
                    from spireagent.workbench.p1_data_preparation import (
                        freeze_p1_data,
                        prepare_p1_data,
                    )

                    app._require_training_instance()
                    owner, store, _ = app.local_datasets._selected()
                    body = self.json_body(maximum=1024 * 1024)
                    if self.path == "/api/p1-data/prepare":
                        if set(body) - {"revision", "declared_origins", "projection_ids",
                                        "selected_decisions", "split_salt", "cache",
                                        "allocation_policy", "projection_names",
                                        "tool_release_ids"}:
                            raise ValueError
                        value = prepare_p1_data(owner, store, **body)
                    elif self.path == "/api/p1-data/freeze":
                        if set(body) not in ({"selection"}, {"selection", "reserve"}):
                            raise ValueError
                        if type(body.get("reserve", False)) is not bool:
                            raise ValueError
                        selection = body["selection"]
                        if not isinstance(selection, dict) or set(selection) - {
                                "projection_ids", "split_salt", "split_by_group",
                                "selected_decisions"}:
                            raise ValueError
                        value = freeze_p1_data(owner, store, source_identity(ROOT),
                                               **selection, reserve=body.get("reserve", False))
                    else:
                        self.respond(404, b'{"error":"route_not_found"}')
                        return
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_p1_data_request",
                                                "/api/p1-data/:action")
                return
            if self.path.startswith("/api/remote-training/"):
                if not (self.browser_write() or self.control_client()):
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                action = self.path.removeprefix("/api/remote-training/")
                try:
                    app._require_training_instance()
                    value = app.remote_training.command(action, self.json_body(
                        maximum=1024 * 1024 if action in {"prepare", "start"} else 8192))
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_remote_training_request",
                                                "/api/remote-training/:action")
                return
            if self.path.startswith("/api/local-training/"):
                if not (self.browser_write() or self.control_client()):
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                action = self.path.removeprefix("/api/local-training/")
                if action not in {"start", "pause", "cancel", "resume", "reconcile",
                                  "amend_wall_budget"}:
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
                        elif action == "amend_wall_budget":
                            fields |= {"intent_id", "wall_seconds"}
                        if set(body) != fields:
                            raise ValueError
                        value = app.control_local_training(action, body)
                    self.respond(200, json.dumps(value).encode())
                except BoundaryError as error:
                    self.respond(409, json.dumps({"error": error.code}).encode())
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_local_training_request",
                                                "/api/local-training/:action")
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
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_local_memory_evaluation_request",
                                                "/api/local-memory-evaluations/:action")
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
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_local_model_export_request",
                                                "/api/local-model-exports/:action")
                return
            if self.path.startswith("/api/local-model-registrations/"):
                if not self.browser_write() and not self.control_client():
                    self.respond(403, b'{"error":"browser_action_denied"}')
                    return
                if self.path not in {"/api/local-model-registrations/register",
                                     "/api/local-model-registrations/register-teacher",
                                     "/api/local-model-registrations/register-global-program",
                                     "/api/local-model-registrations/register-composition"}:
                    self.respond(404, b'{"error":"route_not_found"}')
                    return
                try:
                    if self.path == "/api/local-model-registrations/register-global-program":
                        body = self.json_body(maximum=128)
                        if set(body) not in (set(), {"environment_kind"}):
                            raise ValueError
                        value = app.register_builtin_teacher(
                            global_program=True,
                            environment_kind=body.get("environment_kind", "native"),
                        )
                        self.respond(200, json.dumps(value).encode())
                        return
                    if self.path == "/api/local-model-registrations/register-teacher":
                        body = self.json_body(maximum=128)
                        if set(body) not in (set(), {"variant"}):
                            raise ValueError
                        value = app.register_builtin_teacher(
                            variant=body.get("variant", "continuous")
                        )
                        self.respond(200, json.dumps(value).encode())
                        return
                    if self.path == "/api/local-model-registrations/register-composition":
                        body = self.json_body(maximum=256)
                        if (not {"model_id", "task_option", "program_option"} <= set(body)
                                or set(body) - {"model_id", "environment_kind", "task_option",
                                                "program_option"}):
                            raise ValueError
                        value = app.register_local_model(
                            body["model_id"],
                            environment_kind=body.get("environment_kind", "native"),
                            composition_options={key: body[key] for key in
                                                 ("task_option", "program_option")},
                        )
                        self.respond(200, json.dumps(value).encode())
                        return
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
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_local_model_registration_request",
                                                "/api/local-model-registrations/:action")
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
                except (OSError, ValueError, TypeError) as error:
                    self.diagnostic_bad_request(error, "invalid_managed_workspace_request",
                                                "/api/local-workspace/managed/:action")
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
    with service_process(
        config.state_dir, cloud_environment_file=config.cloud_environment_file
    ) as resources, instance_lock(
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
            if config.platform_url.startswith(("http://127.0.0.1:", "http://localhost:")):
                registration = start_workbench_registration(
                    f"http://127.0.0.1:{server.server_port}/", app.instance_id,
                    access=app.native_access, runtime_identity=app._workbench_runtime_identity,
                )
            app.p1_maintenance.start()
            app.remote_training.start_loop()
            server.serve_forever(poll_interval=0.2)
        finally:
            if registration is not None:
                registration.close()
            app.close()
            server.server_close()
            (config.state_dir / "runtime.json").unlink(missing_ok=True)
            signal.signal(signal.SIGTERM, previous_signal)
    return {"status": "stopped"}
