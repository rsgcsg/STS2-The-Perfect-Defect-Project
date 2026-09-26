"""Local model preparation and owned Platform Runtime supervision.

Only reviewed registry entries select code. Downloaded manifests are data, never
commands. Platform owns gameplay delivery; this module owns local process and
request state, including uncertain requests that must never be retried.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from spireagent.artifact_contracts import Manifest
from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, decode_json, digest, object_fields
from spireagent.package_identity import PackageIdentityError
from spireagent.policies import SUPPORTED_ADAPTERS, policy_support
from spireagent.policy_files import _inside, _object_file
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json, endpoint
from spireagent.workbench.hub_client import HubClient, NoRedirect
from spireagent.workbench.native_tasks import NativeTasks
from spireagent.workbench.runtime_install import install_runtime, validate_runtime_install

SCHEMA = "stpd/local-models-v1"
RUNTIME_PACKAGE = "@rsgcsg/sts2-policy-runtime"
MODES = frozenset({"human", "shadow", "one_step", "auto"})
JSON_LIMIT = 1024 * 1024






def _check_runtime_port(port: int) -> None:
    # Match Node's listener semantics: closed connections in TIME_WAIT are not
    # another Runtime. This still rejects an active listener; never enable REUSEPORT.
    with socket.socket() as probe:
        if sys.platform == "win32":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise BoundaryError("local_model", "runtime_port_already_in_use") from None


def _loopback(url: str) -> str:
    result = endpoint(url)
    if not result.startswith(("http://127.0.0.1:", "http://localhost:", "http://[::1]:")):
        raise BoundaryError("local_model", "loopback_runtime_required")
    return str(result)






@dataclass(frozen=True)
class RuntimeControlBinding:
    """One Runtime-owned recovery observation, never refreshed inside an intent."""

    runtime_instance_id: str
    recovery_epoch: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.runtime_instance_id, str)
            or not 1 <= len(self.runtime_instance_id) <= 256
            or any(not 33 <= ord(char) <= 126 for char in self.runtime_instance_id)
            or type(self.recovery_epoch) is not int
            or not 0 <= self.recovery_epoch <= 9007199254740991
        ):
            raise BoundaryError("local_model", "invalid_runtime_control_binding")

    @classmethod
    def from_environment(cls, value: object, expected_run: str) -> RuntimeControlBinding:
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "run_id", "runtime_instance_id", "recovery_epoch"}
            or value.get("schema") != "sts2.policy-runtime/environment-1"
            or value.get("run_id") != expected_run
        ):
            raise BoundaryError("local_model", "runtime_environment_unavailable_or_identity_drift")
        return cls(value["runtime_instance_id"], value["recovery_epoch"])

    def headers(self) -> dict[str, str]:
        return {
            "X-STS2-Game-Instance-ID": self.runtime_instance_id,
            "X-STS2-Recovery-Epoch": str(self.recovery_epoch),
        }


class RuntimeClient:
    """Bounded loopback transport. A lost POST response is always uncertain."""

    def __init__(self, address: str, startup: dict[str, Any]) -> None:
        self.address = _loopback(address)
        self.startup = startup
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(
        self, route: str, body: dict[str, Any] | None = None,
        *, binding: RuntimeControlBinding | None = None,
    ) -> dict[str, Any]:
        if (
            route not in {"/status", "/environment", "/mode", "/tick", "/stop"}
            or (route == "/environment" and body is not None)
            or (binding is not None and (body is None or route not in {"/mode", "/tick"}))
        ):
            raise BoundaryError("local_model", "invalid_runtime_route")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if body is not None:
            # The endpoint can be reused by a different Runtime process between
            # observation and dispatch. The server must reject before any effect.
            headers["X-STS2-Policy-Run-ID"] = self.startup["run_id"]
            if binding is not None:
                headers.update(binding.headers())
        request = Request(
            self.address + ("/v2" if body is not None or route == "/environment" else "") + route,
            data=canonical_json(body).encode() if body is not None else None,
            headers=headers,
        )
        fallback = (
            "runtime_command_unknown" if body is not None else
            "runtime_environment_unavailable_or_identity_drift" if route == "/environment" else
            "runtime_status_unavailable_or_identity_drift"
        )
        try:
            with self.opener.open(request, timeout=45 if body is not None else 2) as response:
                raw = response.read(JSON_LIMIT + 1)
            if len(raw) > JSON_LIMIT:
                raise ValueError
            value = decode_json(raw)
            if route == "/environment":
                RuntimeControlBinding.from_environment(value, self.startup["run_id"])
                return cast(dict[str, Any], value)
            expected = "sts2.policy-runtime/http-2" + ("/tick-1" if route == "/tick" else "")
            if not isinstance(value, dict) or value.get("schema") != expected:
                raise ValueError
            self.validate_status(value.get("status"))
            return value
        except HTTPError as error:
            if route == "/environment" and error.code == 404:
                raise BoundaryError(
                    "local_model", "runtime_upgrade_required_for_model_control"
                ) from None
            # Only known owner precondition rejections establish non-dispatch.
            # An unstructured response or any transport failure remains unknown.
            rejection_code = None
            try:
                raw = error.read(JSON_LIMIT + 1)
                if len(raw) > JSON_LIMIT:
                    raise ValueError
                rejected = decode_json(raw)
                code = rejected.get("error") if isinstance(rejected, dict) else None
                allowed = {
                    409: {"runtime_game_mismatch", "runtime_recovery_epoch_mismatch"},
                    428: {
                        "runtime_game_precondition_required",
                        "runtime_recovery_precondition_required",
                    },
                    503: {"runtime_environment_unavailable"},
                }
                if (
                    isinstance(rejected, dict) and set(rejected) == {"schema", "error"}
                    and rejected["schema"] == "sts2.policy-runtime/http-2"
                    and isinstance(code, str) and code in allowed.get(error.code, set())
                ):
                    rejection_code = code
            except (OSError, ValueError, TypeError):
                pass
            raise BoundaryError("local_model", rejection_code or fallback) from None
        except (URLError, OSError, ValueError, TypeError, BoundaryError):
            raise BoundaryError("local_model", fallback) from None

    def validate_status(self, status: object) -> None:
        if not isinstance(status, dict):
            raise ValueError
        policy, runtime = status.get("policy"), status.get("runtime")
        if (
            status.get("schema") != "sts2.policy-runtime/status-1"
            or status.get("run_id") != self.startup["run_id"]
            or status.get("mode") not in MODES
            or status.get("lifecycle") not in {"running", "stopped"}
            or status.get("controller") not in {"held", "released"}
            or type(status.get("tainted")) is not bool
            or not isinstance(policy, dict)
            or policy.get("manifest_id") != self.startup["manifest_id"]
            or policy.get("artifact_sha256") != self.startup["policy_artifact_sha256"]
            or not isinstance(runtime, dict)
            or runtime.get("version") != self.startup["runtime_version"]
            or runtime.get("code_sha256") != self.startup["runtime_code_sha256"]
        ):
            raise ValueError


class LocalModelService:
    def __init__(self, config: ProjectConfig, hub: HubClient | None = None) -> None:
        self.config, self.hub = config, hub
        self.root = ROOT
        self.directory = config.state_dir / "models"
        self.lock = threading.RLock()
        self.control_send_lock = threading.Lock()
        self.intent_generation = 0
        self.process: subprocess.Popen[bytes] | None = None
        self.client: RuntimeClient | None = None
        self.thread: threading.Thread | None = None
        self.threads: list[threading.Thread] = []
        self.closed = False
        self.native_tasks = NativeTasks()
        self.observer_stop = threading.Event()
        self.observer: threading.Thread | None = None
        self.state: dict[str, Any] = {
            "schema": SCHEMA,
            "status": "idle",
            "loaded": False,
            "operation": None,
            "runtime": None,
        }
        previous = self.directory / "session.json"
        if previous.is_file():
            try:
                old = _object_file(previous)
                if old.get("status") not in {"stopped", "idle", "failed"}:
                    self.state.update(
                        status="recovery_required",
                        previous_session=old.get("previous_session") or old,
                        error_code="previous_runtime_not_confirmed_stopped",
                    )
            except (OSError, ValueError, BoundaryError):
                self.state.update(status="recovery_required", error_code="session_record_invalid")

    def registry(self) -> dict[str, Any]:
        value = _object_file(self.root / "configs/developer/local-policies-v1.json")
        object_fields(value, {"schema", "runtime_package", "policies"}, "local_model.registry")
        if value["schema"] != "stpd/local-policy-registry-v1" or not isinstance(
            value["policies"], list
        ):
            raise BoundaryError("local_model", "unsupported_registry")
        # Operator-created local registrations complement the shipped catalog.
        # They can select only the token adapter, never a command or downloaded code.
        # A text profile refers to a separate operator-pinned local Runtime bundle.
        local_path = self.root / ".local/token-policies-v1.json"
        if local_path.exists():
            local = _object_file(local_path)
            object_fields(local, {"schema", "policies"}, "local_model.local_registry")
            if (local["schema"] != "stpd/local-token-policies-v1"
                    or not isinstance(local["policies"], list)
                    or any(not isinstance(entry, dict) or entry.get("adapter") != "token-v1"
                           for entry in local["policies"])):
                raise BoundaryError("local_model", "invalid_local_token_registry")
            value["policies"] = [*value["policies"], *local["policies"]]
        seen = set()
        for entry in value["policies"]:
            if not isinstance(entry, dict):
                raise BoundaryError("local_model", "invalid_policy_entry")
            profile = entry.get("runtime_profile")
            if "runtime_profile" in entry and (
                profile != "text-menu-v1" or entry.get("adapter") != "token-v1"
            ):
                raise BoundaryError("local_model", "unsupported_runtime_profile")
            object_fields(
                {key: item for key, item in entry.items() if key != "runtime_profile"},
                {"id", "label", "adapter", "manifest", "config"}, "local_model.policy"
            )
            if (
                entry["adapter"] not in SUPPORTED_ADAPTERS
                or not isinstance(entry["id"], str)
                or not re.fullmatch(r"[a-z0-9-]{1,80}", entry["id"])
            ):
                raise BoundaryError("local_model", "unsupported_trusted_adapter")
            if entry["id"] in seen:
                raise BoundaryError("local_model", "duplicate_policy_selection")
            seen.add(entry["id"])
            _inside(self.root, entry["manifest"])
            _inside(self.root, entry["config"])
        return value

    def selection(self, identity: str) -> dict[str, Any]:
        for entry in self.registry()["policies"]:
            if entry["id"] == identity:
                return dict(entry)
        raise BoundaryError("local_model", "unregistered_policy_selection")

    def runtime_profile(self, identity: str | None = None) -> tuple[Path, dict[str, Any]]:
        """Resolve operator-owned pins; a downloaded model cannot select executable code."""
        entry = self.selection(identity) if identity is not None else None
        if entry is not None and entry.get("runtime_profile") == "text-menu-v1":
            manifest = _object_file(_inside(self.root, entry["manifest"]))
            representation = manifest.get("representation")
            if not isinstance(representation, dict) or representation.get("input_schema") != (
                "sts2.player-environment/text-menu-snapshot-1"
            ):
                raise BoundaryError("local_model", "text_runtime_requires_text_model")
            profile = _object_file(_inside(self.root, ".local/text-menu-runtime-v1.json"))
            object_fields(profile, {"schema", "runtime_package"}, "local_model.runtime_profile")
            pin = profile["runtime_package"]
            if (profile["schema"] != "stpd/local-text-runtime-v1"
                    or not isinstance(pin, dict)
                    or pin.get("dependency_layout") != "bundled_source_candidate"):
                raise BoundaryError("local_model", "unsupported_runtime_profile")
            directory = self.directory / "text-menu-v1"
            if directory.is_symlink():
                raise BoundaryError("local_model", "runtime_install_path_unsafe")
        else:
            directory, pin = self.directory, self.registry()["runtime_package"]
        if not isinstance(pin, dict) or pin.get("package") != RUNTIME_PACKAGE:
            raise BoundaryError("local_model", "runtime_package_not_pinned")
        return directory, pin

    def _runtime_package(self, identity: str | None = None) -> dict[str, Any]:
        directory, pin = self.runtime_profile(identity)
        try:
            return validate_runtime_install(
                self._node_modules(identity), pin, self._connector_pin()
            )
        except (OSError, ValueError, StopIteration, PackageIdentityError):
            raise BoundaryError(
                "local_model", "text_runtime_local_install_required"
                if directory != self.directory else "runtime_package_missing_or_drifted"
            ) from None

    def _connector_pin(self) -> dict[str, Any]:
        return next(
            p
            for p in self.config.combination["node_packages"]
            if p.get("package") == "@rsgcsg/sts2-connector-client"
        )

    def _node_modules(self, identity: str | None = None) -> Path:
        directory, _ = self.runtime_profile(identity)
        private = directory / "runtime"
        if private.is_symlink():
            raise BoundaryError("local_model", "runtime_install_path_unsafe")
        # A text profile never falls back to the legacy source-tree installation.
        return (private / "node_modules" if private.exists() or directory != self.directory
                else self.root / "node_modules")

    def _public_manifest_contract(self, manifest_path: Path, identity: str | None = None) -> None:
        self._runtime_package(identity)
        script = (
            "import {readFile} from 'node:fs/promises';"
            "const {validatePolicyManifest}=await import(process.argv[1]);"
            "validatePolicyManifest(JSON.parse(await readFile(process.argv[2],'utf8')));"
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP"}
        }
        result = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                script,
                (self._node_modules(identity) / RUNTIME_PACKAGE / "dist/index.js").as_uri(),
                str(manifest_path),
            ],
            env=environment,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise BoundaryError("local_model", "public_policy_manifest_incompatible")

    def install_runtime(self) -> dict[str, Any]:
        if self.client is not None or (self.process is not None and self.process.poll() is None):
            raise BoundaryError("local_model", "stop_runtime_before_install")

        def install() -> None:
            report = install_runtime(
                self.directory, self.registry()["runtime_package"], self._connector_pin()
            )
            with self.lock:
                self.state.update(status="idle", last_runtime_install=report)

        return self._begin("install-runtime", install)

    def catalog(self) -> dict[str, Any]:
        entries = []
        for entry in self.registry()["policies"]:
            manifest = _object_file(_inside(self.root, entry["manifest"]))
            entries.append(
                {
                    "selection_id": entry["id"],
                    "label": entry["label"],
                    "support": manifest.get("support"),
                    "claims": manifest.get("claims"),
                    "artifact_sha256": manifest.get("artifact", {}).get("sha256"),
                    "readiness": "check_required",
                }
            )
        downloads = []
        parent = self.config.state_dir / "downloads"
        if parent.is_dir():
            for directory in sorted(parent.iterdir()):
                if directory.is_symlink() or not re.fullmatch(r"[a-f0-9]{64}", directory.name):
                    continue
                try:
                    manifest_path = directory / "manifest.json"
                    _object_file(manifest_path)
                    downloaded = Manifest.from_bytes(manifest_path.read_bytes(), directory.name)
                    if downloaded.kind != "model":
                        continue
                    downloads.append(
                        {
                            "artifact_id": downloaded.artifact_id,
                            "kind": downloaded.kind,
                            "model_schema": downloaded.parameters.value().get("schema"),
                            "local_download": (directory / "download.json").is_file(),
                            "loaded": False,
                            "support_status": "unsupported",
                            "reason": "no_compatible_live_adapter_and_input_parity",
                        }
                    )
                except (OSError, ValueError, BoundaryError):
                    continue
        return {
            "schema": SCHEMA,
            "policies": entries,
            "downloaded_models": downloads,
            "session": self.status(),
            "evaluations": self.evaluations(),
            "evaluation_scope": "bounded_runtime_operation",
        }

    def evaluations(self) -> list[dict[str, Any]]:
        directory = self.directory / "evaluations"
        if not directory.is_dir():
            return []
        result = []
        for path in sorted(directory.glob("*.json"))[-100:]:
            try:
                value = _object_file(path)
                identity = value.pop("evaluation_id", None)
                if identity != hashlib.sha256(canonical_json(value).encode()).hexdigest():
                    continue
                result.append({**value, "evaluation_id": identity})
            except (OSError, ValueError, BoundaryError):
                continue
        return result

    def readiness(self, identity: str) -> dict[str, Any]:
        entry = self.selection(identity)
        checks: dict[str, dict[str, str]] = {}
        manifest_path = _inside(self.root, entry["manifest"])
        config_path = _inside(self.root, entry["config"])
        manifest, policy_config = _object_file(manifest_path), _object_file(config_path)

        def check(name: str, operation: Callable[[], object]) -> None:
            try:
                operation()
                checks[name] = {"status": "pass"}
            except (
                OSError,
                ValueError,
                KeyError,
                BoundaryError,
                subprocess.SubprocessError,
            ) as error:
                checks[name] = {
                    "status": "blocked",
                    "code": error.code
                    if isinstance(error, BoundaryError)
                    else name + "_missing_or_drifted",
                }

        checks.update(policy_support(entry["adapter"]).inspect(
            self.root, entry, manifest, policy_config
        ))
        check("runtime_package", lambda: self._runtime_package(identity) and None)
        check("public_contract", lambda: self._public_manifest_contract(manifest_path, identity))
        checks["node"] = {
            "status": "pass" if shutil.which("node") else "blocked",
            "code": "node_available" if shutil.which("node") else "node_missing",
        }
        ready = all(value["status"] == "pass" for value in checks.values())
        return {
            "schema": SCHEMA,
            "selection_id": identity,
            "status": "ready_to_load" if ready else "blocked",
            "checks": checks,
            "loaded": False,
            "environment_compatible": "checked_by_runtime_before_decision",
            "qwen_weights": "checked_by_adapter_during_loading",
            "checkpoint_identity": "checked_by_adapter_during_loading",
            "support": manifest["support"],
        }

    def _save(self) -> None:
        atomic_json(self.directory / "session.json", self.state)

    def _begin(
        self, action: str, operation: Callable[[], None], *, recovery: bool = False
    ) -> dict[str, Any]:
        with self.lock:
            if self.closed:
                raise BoundaryError("local_model", "service_closed")
            if self.thread and self.thread.is_alive() and not recovery:
                raise BoundaryError("local_model", "operation_in_progress")
            if self.state["status"] in {"command_unknown", "recovery_required"} and not recovery:
                raise BoundaryError("local_model", "previous_operation_requires_recovery")
            current = {"id": uuid4().hex, "action": action, "status": "pending"}
            self.state["operation"] = current
            self._save()

            def run() -> None:
                try:
                    operation()
                    with self.lock:
                        current["status"] = "completed"
                except (
                    OSError,
                    ValueError,
                    TypeError,
                    KeyError,
                    BoundaryError,
                    subprocess.SubprocessError,
                ) as error:
                    with self.lock:
                        code = (
                            error.code
                            if isinstance(error, BoundaryError)
                            else "local_operation_failed"
                        )
                        if self.state["operation"] is current and not self.closed:
                            pending_runtime = self.client is not None or bool(
                                self.state.get("previous_session")
                            )
                            unknown = code in {
                                "runtime_command_unknown", "native_task_command_unknown"
                            }
                            safe_precondition = code in {
                                "native_task_unavailable", "native_task_game_identity_mismatch",
                                "runtime_game_identity_required",
                                "runtime_connector_binding_required",
                                "connector_identity_unavailable",
                                "recording_close_pending_or_failed",
                                "runtime_upgrade_required_for_model_control",
                                "runtime_environment_unavailable_or_identity_drift",
                                "runtime_environment_unavailable", "runtime_game_mismatch",
                                "runtime_recovery_epoch_mismatch",
                                "runtime_game_precondition_required",
                                "runtime_recovery_precondition_required",
                            }
                            self.state.update(
                                status="command_unknown"
                                if unknown
                                else ("loaded" if safe_precondition and self.client is not None
                                      else ("recovery_required" if pending_runtime else "failed")),
                                error_code=code,
                            )
                        current["status"] = (
                            "unknown" if code in {
                                "runtime_command_unknown", "native_task_command_unknown"
                            } else "failed"
                        )
                finally:
                    with self.lock:
                        self._save()

            self.thread = threading.Thread(target=run, daemon=True)
            self.threads = [thread for thread in self.threads if thread.is_alive()]
            self.threads.append(self.thread)
            self.thread.start()
            return cast(dict[str, Any], json.loads(json.dumps(self.state)))

    def prepare(self, artifact_id: str) -> dict[str, Any]:
        identity = digest(artifact_id, "local_model.artifact")
        if self.hub is None:
            raise BoundaryError("local_model", "hub_not_configured")

        def download() -> None:
            assert self.hub is not None
            receipt = self.hub.download(identity, self.config.state_dir / "downloads")
            with self.lock:
                self.state["last_download"] = receipt
                if self.client is None:
                    self.state["status"] = "idle"

        return self._begin("download", download)

    def start(self, identity: str) -> dict[str, Any]:
        self.selection(identity)
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise BoundaryError("local_model", "runtime_already_running")
            intent = self.intent_generation
        return self._begin("start", lambda: self._start(identity, intent))

    def prepare_and_load(self, identity: str) -> dict[str, Any]:
        """Prepare a reviewed selection, then load in Human mode; never fetch model weights.

        Only the existing bounded, hash-pinned Runtime installer is automatic.
        Adapter/weights/backend requirements remain explicit owning readiness checks.
        """
        self.selection(identity)
        with self.lock:
            if self.client is not None or (
                self.process is not None and self.process.poll() is None
            ):
                raise BoundaryError("local_model", "runtime_already_running")
            intent = self.intent_generation

        def prepare() -> None:
            report = self.readiness(identity)
            with self.lock:
                self.state.update(
                    selection_id=identity, readiness=report, preparation_stage="checking"
                )
            if any(
                check["status"] != "pass"
                for name, check in report["checks"].items()
                if name not in {"runtime_package", "public_contract"}
            ):
                raise BoundaryError("local_model", "model_readiness_blocked")
            if report["checks"].get("runtime_package", {}).get("status") != "pass":
                directory, pin = self.runtime_profile(identity)
                if directory != self.directory:
                    # Private candidate bundles have no invented release URL. The
                    # offline CLI installs their explicitly selected archive first.
                    raise BoundaryError("local_model", "text_runtime_local_install_required")
                with self.lock:
                    if self.closed:
                        raise BoundaryError("local_model", "service_closed")
                    self.state["preparation_stage"] = "installing_runtime"
                installed = install_runtime(
                    directory, pin, self._connector_pin()
                )
                with self.lock:
                    self.state["last_runtime_install"] = installed
            with self.lock:
                self._require_intent(intent)
                self.state["preparation_stage"] = "loading"
            self._start(identity, intent)
            with self.lock:
                self.state["preparation_stage"] = "ready"

        return self._begin("prepare-and-load", prepare)

    def _require_intent(self, intent: int) -> None:
        with self.lock:
            if self.closed or intent != self.intent_generation:
                raise BoundaryError("local_model", "command_superseded")

    def _send_control(
        self, client: RuntimeClient, route: str, body: dict[str, Any], intent: int,
        binding: RuntimeControlBinding | None = None,
    ) -> dict[str, Any]:
        # Keep model mutations serialized, but never queue recovery behind their
        # potentially slow HTTP replies. Runtime's shared epoch fences effects;
        # our intent checks fence stale replies and a One-Step's later tick.
        recovery = route == "/stop" or (route == "/mode" and body.get("mode") == "human")
        with nullcontext() if recovery else self.control_send_lock:
            self._require_intent(intent)
            if binding is None and (route == "/tick" or (
                route == "/mode" and body.get("mode") != "human"
            )):
                raise BoundaryError("local_model", "runtime_recovery_precondition_required")
            value = (client.request(route, body, binding=binding)
                     if binding is not None else client.request(route, body))
            self._require_intent(intent)
            return value

    def _start(self, identity: str, intent: int | None = None) -> None:
        if intent is None:
            intent = self.intent_generation
        report = self.readiness(identity)
        with self.lock:
            self._require_intent(intent)
            self.state.update(selection_id=identity, readiness=report)
        if report["status"] != "ready_to_load":
            raise BoundaryError("local_model", "model_readiness_blocked")
        entry = self.selection(identity)
        manifest_path = _inside(self.root, entry["manifest"])
        manifest = _object_file(manifest_path)
        package = self._runtime_package(identity)
        _check_runtime_port(15527)
        connector = _loopback(self.config.platform_url or "http://127.0.0.1:15526")
        command = [
            "node",
            str(self._node_modules(identity) / RUNTIME_PACKAGE / "dist/cli.js"),
            "--manifest",
            str(manifest_path),
            "--adapter-command",
            sys.executable,
            "--adapter-cwd",
            str(self.root),
            *["--adapter-arg=" + arg for arg in policy_support(entry["adapter"]).arguments(entry)],
            "--connector-endpoint",
            connector,
            "--listen-port",
            "15527",
            "--evidence-root",
            str(self.directory / "agent-runs"),
            "--mode",
            "human",
        ]
        environment = dict(os.environ)
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        for name in tuple(environment):
            if name.startswith(("STPD_HUB_", "AWS_", "MODAL_", "CLOUDFLARE_")) or name in {
                "PYTHONPATH",
                "PYTHONHOME",
                "NODE_OPTIONS",
            }:
                environment.pop(name, None)
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self._require_intent(intent)
            with (self.directory / "runtime.log").open("ab") as log:
                process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=log,
                )
            self.process = process
            # A new Human-mode child must not inherit a prior run's attestation
            # or evaluation if its own startup fails.
            for key in ("startup", "runtime", "evaluation", "recovery_evidence"):
                self.state.pop(key, None)
            self.state.update(status="loading", loaded=False, connector_endpoint=connector)
            self._save()
        lines: queue.Queue[bytes] = queue.Queue(maxsize=1)
        stdout = process.stdout
        assert stdout is not None
        threading.Thread(
            target=lambda: lines.put(stdout.readline(JSON_LIMIT + 1)), daemon=True
        ).start()
        try:
            raw = lines.get(timeout=180)
            startup = decode_json(raw)
            if not isinstance(startup, dict) or len(raw) > JSON_LIMIT:
                raise ValueError
            expected = {
                "schema": "sts2.policy-runtime/startup-1",
                "mode": "human",
                "manifest_id": manifest["manifest_id"],
                "policy_artifact_sha256": manifest["artifact"]["sha256"],
                "policy_manifest_sha256": hashlib.sha256(
                    canonical_json(manifest).encode()
                ).hexdigest(),
                "runtime_version": package["version"],
                "runtime_code_sha256": package["code_sha256"],
                "address": "http://127.0.0.1:15527",
            }
            if any(
                startup.get(key) != value for key, value in expected.items()
            ) or not re.fullmatch(r"run-[a-f0-9-]{36}", startup.get("run_id", "")):
                raise ValueError
            client = RuntimeClient(startup["address"], startup)
            runtime = client.request("/status")["status"]
            with self.lock:
                self._require_intent(intent)
                if self.closed:
                    raise BoundaryError("local_model", "service_closed")
                self.client = client
                self.state.update(
                    status="loaded", loaded=True, startup=startup, runtime=runtime, error_code=None
                )
            self.start_observer()
        except (queue.Empty, OSError, ValueError, TypeError, BoundaryError):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise BoundaryError("local_model", "runtime_load_or_attestation_failed") from None

    def status(self) -> dict[str, Any]:
        with self.lock:
            client = self.client
            if (
                self.process is not None
                and self.process.poll() is not None
                and self.state["status"] == "loaded"
            ):
                self.state.update(status="runtime_exited", loaded=False)
        if client is not None:
            try:
                runtime = client.request("/status")["status"]
                with self.lock:
                    if self.client is client:
                        self.state["runtime"] = runtime
                        self.state.pop("observation_error", None)
            except BoundaryError as error:
                with self.lock:
                    if self.client is client:
                        self.state["observation_error"] = error.code
        with self.lock:
            return cast(dict[str, Any], json.loads(json.dumps(self.state)))

    def command(self, action: str) -> dict[str, Any]:
        if action not in MODES | {"stop"}:
            raise BoundaryError("local_model", "unsupported_local_command")
        with self.lock:
            recovery = action in {"human", "stop"}
            if not recovery and self.thread is not None and self.thread.is_alive():
                raise BoundaryError("local_model", "operation_in_progress")
            if self.client is None or not self.state["loaded"]:
                if recovery and self.state.get("previous_session"):
                    self.intent_generation += 1
                    intent = self.intent_generation
                    return self._begin(
                        action, lambda: self._recover(action, intent), recovery=True
                    )
                if recovery and self.thread is not None and self.thread.is_alive() and (
                    (self.state.get("operation") or {}).get("action")
                    in {"start", "prepare-and-load"}
                ):
                    self.intent_generation += 1
                    intent = self.intent_generation
                    return self._begin(
                        action, lambda: self._cancel_loading(intent), recovery=True
                    )
                raise BoundaryError("local_model", "model_not_loaded")
            self.intent_generation += 1
            intent = self.intent_generation
            client = self.client
            connector_endpoint = self.state.get("connector_endpoint")

        def execute() -> None:
            assert client is not None
            self._require_intent(intent)
            # Observe exact instance before every mutation; never address a new
            # process that reused the same port after our owned process exited.
            observation = client.request("/status")["status"]
            binding = None
            if action in {"shadow", "one_step", "auto"}:
                self._require_intent(intent)
                bound_endpoint = NativeTasks.bound_connector(connector_endpoint)
                binding = RuntimeControlBinding.from_environment(
                    client.request("/environment"), observation["run_id"]
                )
                # Capture the shared Runtime epoch before native preparation.
                # A recovery from either UI invalidates this exact observation;
                # never refresh its epoch to make a stale intent eligible again.
                instance = self.native_tasks.connector_instance(bound_endpoint)
                if instance != binding.runtime_instance_id:
                    raise BoundaryError("local_model", "runtime_game_mismatch")
                NativeTasks.confirm_runtime(observation, binding.runtime_instance_id)
                self._require_intent(intent)
                native = self.native_tasks.prepare_model(observation, bound_endpoint)
                if native["runtime_instance_id"] != binding.runtime_instance_id:
                    raise BoundaryError("local_model", "runtime_game_mismatch")
                # Native Close cannot authorize a replacement Runtime or game.
                latest = client.request("/status")["status"]
                NativeTasks.confirm_runtime(latest, binding.runtime_instance_id)
            if action == "stop":
                runtime = self._send_control(client, "/stop", {}, intent)["status"]
            else:
                runtime = self._send_control(
                    client, "/mode", {"mode": action}, intent, binding
                )["status"]
                if action == "one_step":
                    runtime = self._send_control(
                        client, "/tick", {"max_ticks": 1}, intent, binding
                    )["status"]
            with self.lock:
                self._require_intent(intent)
                if not self.closed and self.state["status"] != "stopped":
                    self.state.update(runtime=runtime, status="loaded", error_code=None)
            if action == "stop":
                self._stop_process()
                self._evaluation_handoff()
                with self.lock:
                    self.state.update(status="stopped", loaded=False)
                    self.client = None

        return self._begin(action, execute, recovery=action in {"human", "stop"})

    def _cancel_loading(self, intent: int) -> None:
        with self.control_send_lock, self.lock:
            self._require_intent(intent)
            self._stop_process()
            self.client = None
            self.state.update(status="stopped", loaded=False, error_code=None)

    def _recover(self, action: str, intent: int) -> None:
        previous = self.state["previous_session"]
        if action == "stop" and self._recover_finalized_stop(previous, intent):
            return
        startup = previous.get("startup")
        entry = self.selection(previous.get("selection_id", ""))
        manifest = _object_file(_inside(self.root, entry["manifest"]))
        package = self._runtime_package(entry["id"])
        if not isinstance(startup, dict) or any(
            startup.get(key) != expected
            for key, expected in {
                "manifest_id": manifest["manifest_id"],
                "policy_artifact_sha256": manifest["artifact"]["sha256"],
                "policy_manifest_sha256": hashlib.sha256(
                    canonical_json(manifest).encode()
                ).hexdigest(),
                "runtime_version": package["version"],
                "runtime_code_sha256": package["code_sha256"],
                "address": "http://127.0.0.1:15527",
            }.items()
        ):
            raise BoundaryError("local_model", "recovery_identity_drift")
        client = RuntimeClient(startup["address"], startup)
        observed = client.request("/status")["status"]
        if observed["lifecycle"] == "running":
            observed = (
                self._send_control(client, "/stop", {}, intent)["status"]
                if action == "stop"
                else self._send_control(client, "/mode", {"mode": "human"}, intent)["status"]
            )
        # Older sessions did not persist their Connector endpoint. Their exact
        # Runtime can still be returned to Human or stopped, but cannot safely be
        # retargeted from today's project config. Stop then load again to bind it.
        try:
            connector_endpoint = NativeTasks.bound_connector(previous.get("connector_endpoint"))
        except BoundaryError:
            connector_endpoint = None
        with self.lock:
            self._require_intent(intent)
            self.client = client if observed["lifecycle"] == "running" else None
            self.state.update(
                selection_id=entry["id"],
                startup=startup,
                runtime=observed,
                loaded=self.client is not None,
                status="loaded" if self.client is not None else "stopped",
                previous_session=None,
                connector_endpoint=connector_endpoint,
                error_code=(
                    "runtime_connector_binding_required"
                    if self.client is not None and connector_endpoint is None else None
                ),
            )
        if self.client is None:
            self._evaluation_handoff()
        else:
            self.start_observer()

    def _recover_finalized_stop(self, previous: dict[str, Any], intent: int) -> bool:
        """Explicit Stop may retire a sealed old run without its old package installed.

        An empty port alone proves nothing about past delivery. Require the owning
        verifier and a terminal Stop event bound to the persisted startup identity.
        This does not retry an action or clear the old run's taint.
        """
        from sts2_platform_evidence import verify_agent_run_evidence

        startup = previous.get("startup")
        required = {
            "run_id", "manifest_id", "policy_manifest_sha256", "policy_artifact_sha256",
            "runtime_version", "runtime_code_sha256",
        }
        if not isinstance(startup, dict) or any(
            not isinstance(startup.get(key), str) or not startup[key] for key in required
        ):
            return False
        if startup.get("address") != "http://127.0.0.1:15527" or not re.fullmatch(
            r"run-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            startup["run_id"],
        ):
            return False
        directory = self.directory / "agent-runs" / startup["run_id"]
        result = verify_agent_run_evidence(directory, startup)
        if not result.passed or result.value is None:
            return False
        raw = (directory / "events.jsonl").read_bytes()
        entry = next(item for item in result.value.evidence_manifest["files"]
                     if item["path"] == "events.jsonl")
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            return False
        lines = raw.splitlines()
        if not lines or json.loads(lines[-1])["kind"] != "stopped":
            return False
        _check_runtime_port(15527)
        with self.lock:
            self._require_intent(intent)
            archived = canonical_json(previous).encode()
            identity = hashlib.sha256(archived).hexdigest()
            archive = self.directory / "session-archives" / (identity + ".json")
            archive.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open("xb") as output:
                    output.write(archived)
                    output.flush()
                    os.fsync(output.fileno())
            except FileExistsError:
                if archive.is_symlink() or archive.read_bytes() != archived:
                    raise BoundaryError(
                        "local_model", "session_archive_identity_collision"
                    ) from None
            self.state.update(startup=startup, selection_id=previous.get("selection_id"))
            self._evaluation_handoff()
            if self.state["evaluation"]["evidence_verification"] != "pass":
                raise BoundaryError("local_model", "finalized_stop_evidence_changed")
            self.state.update(
                status="stopped", loaded=False, runtime=None, previous_session=None,
                error_code=None, recovery_evidence={
                    "kind": "verified_finalized_stop", "run_id": startup["run_id"],
                    "content_id": result.value.content_id, "session_archive_id": identity,
                    "tainted": result.value.manifest["tainted"],
                },
            )
        return True

    def start_observer(self) -> None:
        """Observe owned runtime termination independently of page reads."""
        with self.lock:
            if self.closed or (self.observer is not None and self.observer.is_alive()):
                return
            self.observer_stop.clear()

            def observe() -> None:
                while not self.observer_stop.wait(0.5):
                    self.observe_once()

            self.observer = threading.Thread(target=observe, daemon=True)
            self.observer.start()

    def observe_once(self) -> None:
        with self.lock:
            client = self.client
            if self.closed or client is None or (
                self.thread is not None and self.thread.is_alive()
            ):
                return
            owned_exited = self.process is not None and self.process.poll() is not None
        try:
            observed = None if owned_exited else client.request("/status")["status"]
            if observed is not None and observed["lifecycle"] != "stopped":
                return
            with self.lock:
                if self.client is not client or self.closed or (
                    self.thread is not None and self.thread.is_alive()
                ):
                    return
                self.state["runtime"] = observed
                self.state["runtime_stop_observed"] = observed is not None
                # Fence new commands until the already finalized run is projected.
                # No /stop or gameplay request is issued by this observer.
                self._stop_process()
                self._evaluation_handoff()
                if self.client is client:
                    self.state.update(status="stopped", loaded=False)
                    self.client = None
                    self._save()
        except (OSError, ValueError, KeyError, BoundaryError) as error:
            with self.lock:
                if self.client is client:
                    self.state["observation_error"] = (
                        error.code if isinstance(error, BoundaryError)
                        else "runtime_finalization_unavailable"
                    )

    def _stop_process(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def _evaluation_handoff(self) -> None:
        from sts2_platform_evidence import verify_agent_run_evidence

        startup = self.state["startup"]
        directory = self.directory / "agent-runs" / startup["run_id"]
        result = verify_agent_run_evidence(directory, startup)
        report = {
            "schema": "stpd/local-runtime-evaluation-handoff-v1",
            "selection_id": self.state["selection_id"],
            "run_id": startup["run_id"],
            "model_sha256": startup["policy_artifact_sha256"],
            "policy_manifest_sha256": startup["policy_manifest_sha256"],
            "runtime_code_sha256": startup["runtime_code_sha256"],
            "evidence_verification": result.status,
            "findings": [finding.code for finding in result.findings],
            "scope": "bounded_runtime_operation",
            "game_outcome": "not_measured",
            "scientific_verdict": "not_claimed",
            "training_admission": "not_claimed",
        }
        if result.value is not None:
            report.update(
                evidence_content_id=result.value.content_id, event_count=result.value.event_count
            )
            counts: Counter[str] = Counter()
            deliveries: Counter[str] = Counter()
            # Only read events after the owning verifier accepted exact bytes and
            # their relationship to this model/runtime. Counts are operational.
            with (directory / "events.jsonl").open(encoding="utf-8") as events:
                for line in events:
                    event = json.loads(line)
                    counts[event["kind"]] += 1
                    if event["kind"] == "receipt":
                        deliveries[event["payload"]["receipt"]["delivery"]] += 1
            report.update(event_counts=dict(counts), delivery_counts=dict(deliveries))
        identity = hashlib.sha256(canonical_json(report).encode()).hexdigest()
        report["evaluation_id"] = identity
        target = self.directory / "evaluations" / (identity + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = canonical_json(report).encode()
        try:
            with target.open("xb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError:
            if target.is_symlink() or target.read_bytes() != raw:
                raise BoundaryError("local_model", "evaluation_identity_collision") from None
        with self.lock:
            self.state["evaluation"] = report

    def close(self) -> None:
        self.observer_stop.set()
        with self.lock:
            self.closed = True
            self.intent_generation += 1
            client = self.client
        # Stop only the exact Runtime. A recovered client has no owned process
        # handle, so a lost reply cannot be turned into a confirmed shutdown.
        observed = None
        try:
            if client is not None:
                # Shutdown recovery must also reach the exact Runtime while an
                # earlier model request is still awaiting its response.
                observed = client.request("/stop", {})["status"]
        except BoundaryError:
            pass
        finally:
            self._stop_process()
        for thread in self.threads:
            thread.join(timeout=1)
        with self.lock:
            if self.client is not None:
                confirmed = (observed is not None and observed["lifecycle"] == "stopped") or (
                    self.process is not None and self.process.poll() is not None
                )
                if confirmed:
                    self.state.update(status="stopped", loaded=False)
                    if observed is not None:
                        self.state["runtime"] = observed
                    try:
                        self._evaluation_handoff()
                    except (OSError, ValueError, ImportError):
                        self.state["evidence_status"] = "verification_unavailable"
                else:
                    self.state.update(
                        status="command_unknown",
                        loaded=False,
                        error_code="runtime_command_unknown",
                    )
                self.client = None
            self._save()
