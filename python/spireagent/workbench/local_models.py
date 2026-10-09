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
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
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
from spireagent.workbench.kit_runtime import (
    KIT_RUNTIME_PAIRS,
    text_runtime_pin,
)
from spireagent.workbench.managed_model_target import (
    confirm_target,
    managed_manifest,
    public_target,
    runtime_arguments,
)
from spireagent.workbench.native_tasks import NativeTasks
from spireagent.workbench.runtime_install import (
    ARCHIVE_LIMIT,
    CONNECTOR_PACKAGE,
    install_runtime,
    v2_sdk_available,
    validate_runtime_install,
)
from stpd.structured_code_scope import is_structured_model_schema

SCHEMA = "stpd/local-models-v1"
RUNTIME_PACKAGE = "@rsgcsg/sts2-policy-runtime"
MODES = frozenset({"human", "shadow", "one_step", "auto"})
AGENT_STARTUP = "sts2.policy-runtime/agent-session-startup-1"
AGENT_STATUS = "sts2.policy-runtime/agent-session-status-1"
JSON_LIMIT = 1024 * 1024
RUN_PROFILES = {
    "short": {"max_submissions": 16, "max_policy_calls": 32, "deadline_ms": 60_000},
    "extended": {"max_submissions": 2_000, "max_policy_calls": 4_000,
                 "deadline_ms": 1_800_000},
}
TEXT_PROFILES = {"text-menu-v1": ("token-v1", ".local/text-menu-runtime-v1.json",
                                  "stpd/local-text-runtime-v1", "text-menu-v1"),
                 "text-menu-m2-v1": ("stpd-m2-decision-adapter",
                                     ".local/text-menu-m2-runtime-v1.json",
                                     "stpd/local-text-m2-runtime-v1", "text-menu-m2-v1"),
                 "text-menu-m2-v2": ("stpd-m2-decision-adapter",
                                     ".local/text-menu-m2-runtime-v2.json",
                                     "stpd/local-text-m2-runtime-v2", "text-menu-m2-v2")}
NATIVE_PROFILE = "native-logical-v1"
NATIVE_ADAPTER = "stpd-native-structured-m2-agent"


def _validate_text_profile(profile: dict[str, Any], schema: str,
                           profile_id: str) -> dict[str, Any]:
    try:
        object_fields(profile, {"schema", "runtime_package"}, "local_model.runtime_profile")
    except BoundaryError as error:
        raise BoundaryError("local_model", "text_runtime_profile_invalid") from error
    pin = profile["runtime_package"]
    if (profile["schema"] != schema or not isinstance(pin, dict)
            or pin.get("dependency_layout") != "bundled_source_candidate"
            or pin.get("package") != RUNTIME_PACKAGE):
        raise BoundaryError("local_model", "text_runtime_profile_invalid")
    if profile_id == "text-menu-m2-v2":
        expected = {"package", "version", "source_revision",
                    "component_tree_revision", "release_asset_sha256",
                    "package_content_sha256", "dependency_layout",
                    "bundled_connector_pin"}
        if (set(pin) != expected
                or not isinstance(pin.get("version"), str)
                or not re.fullmatch(r"[0-9A-Za-z.+-]{1,80}", pin["version"])
                or not isinstance(pin.get("bundled_connector_pin"), dict)):
            raise BoundaryError("local_model", "text_runtime_profile_invalid")
        try:
            digest(pin["source_revision"], "local_model.v2_source", length=40)
            digest(pin["component_tree_revision"], "local_model.v2_tree", length=40)
            digest(pin["release_asset_sha256"], "local_model.v2_archive")
            digest(pin["package_content_sha256"], "local_model.v2_package")
        except BoundaryError as error:
            raise BoundaryError("local_model", "text_runtime_profile_invalid") from error
    return pin
_BUDGET_STATES = frozenset({"inactive", "active", "exhausted"})
_BUDGET_EXHAUSTION = frozenset({"submission_attempt_limit", "policy_call_limit", "deadline"})
_BUDGET_END = frozenset({"human_recovery", "mode_changed", "stopped"})


def _recorded_budget(value: object) -> dict[str, Any] | None:
    """Project only typed Runtime counters from verified event bytes."""
    if (not isinstance(value, dict) or not isinstance(value.get("state"), str)
            or value["state"] not in _BUDGET_STATES):
        return None
    fields = ("max_submissions", "submissions_used", "max_policy_calls",
              "policy_calls_used", "deadline_ms", "elapsed_ms")
    if any(type(value.get(key)) is not int or value[key] < 0 for key in fields):
        return None
    exhausted = value.get("exhausted_reason")
    ended = value.get("ended_reason")
    if (exhausted is not None and (
            not isinstance(exhausted, str) or exhausted not in _BUDGET_EXHAUSTION)
            or ended is not None and (
                not isinstance(ended, str) or ended not in _BUDGET_END)):
        return None
    return {key: value[key] for key in fields} | {
        "state": value["state"], "exhausted_reason": exhausted, "ended_reason": ended,
    }


def _terminal_screen(value: object) -> str | None:
    """An exact text-menu Game Over page observation, not a game outcome claim."""
    if not isinstance(value, dict) or (
        value.get("schema"), value.get("input_profile")) not in {
            ("sts2.player-environment/text-menu-snapshot-1", "text-menu-v1"),
            ("sts2.player-environment/text-menu-snapshot-2", "text-menu-v2"),
    }:
        return None
    interaction = value.get("interaction")
    if not isinstance(interaction, dict) or interaction.get("kind") != "game_over":
        return None
    content = interaction.get("content")
    if not isinstance(content, dict):
        return None
    surface, context = content.get("surface"), content.get("context")
    if (not isinstance(surface, dict) or surface.get("kind") != "game_over"
            or not isinstance(context, dict) or context.get("kind") != "game_over"):
        return None
    result = context.get("result")
    return result if isinstance(result, str) and result in {"win", "loss"} else None






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


def _startup_identity(manifest: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    native = manifest.get("schema") == "sts2.policy-runtime/agent-manifest-1"
    prefix = "agent" if native else "policy"
    return {
        "schema": AGENT_STARTUP if native else "sts2.policy-runtime/startup-1",
        "agent_manifest_id" if native else "manifest_id": manifest["manifest_id"],
        f"{prefix}_artifact_sha256": manifest["artifact"]["sha256"],
        f"{prefix}_manifest_sha256": hashlib.sha256(canonical_json(manifest).encode()).hexdigest(),
        "runtime_version": package["version"],
        "runtime_code_sha256": package["code_sha256"],
        "address": "http://127.0.0.1:15527",
        **({"adapter": manifest["adapter"], "managed_environment": None} if native else {}),
    }


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
            route not in {"/status", "/environment", "/mode", "/tick", "/stop", "/reconcile"}
            or (route == "/environment" and body is not None)
            or (
                binding is not None
                and (body is None or route not in {"/mode", "/tick", "/reconcile"})
            )
            or (
                route == "/reconcile"
                and (
                    self.startup.get("schema") != AGENT_STARTUP
                    or binding is None
                    or not isinstance(body, dict)
                    or set(body) != {"request_id"}
                    or not isinstance(body["request_id"], str)
                    or not body["request_id"]
                )
            )
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
            if route == "/reconcile" and (
                set(value) != {"schema", "request_id", "resolution", "status"}
                or value["request_id"] != body["request_id"]  # type: ignore[index]
                or value["resolution"] not in {"resolved", "pending", "unresolved", "tainted"}
            ):
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
                if self.startup.get("schema") == AGENT_STARTUP:
                    allowed[409].update({"runtime_run_mismatch", "runtime_pending_request_mismatch",
                                         "runtime_profile_reconcile_unsupported",
                                         "runtime_reconcile_requires_human"})
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
        if self.startup.get("schema") == AGENT_STARTUP:
            self._validate_agent_status(status)
            return
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
        startup_budget = self.startup.get("autonomy_budget")
        if isinstance(startup_budget, dict) and all(
            type(startup_budget.get(key)) is int
            for key in ("maxSubmissions", "maxPolicyCalls", "deadlineMs")
        ):
            current_budget = status.get("autonomy_budget")
            if not isinstance(current_budget, dict) or any(
                current_budget.get(status_key) != startup_budget[startup_key]
                for startup_key, status_key in (
                    ("maxSubmissions", "max_submissions"),
                    ("maxPolicyCalls", "max_policy_calls"),
                    ("deadlineMs", "deadline_ms"),
                )
            ):
                raise ValueError

    def _validate_agent_status(self, status: dict[str, Any]) -> None:
        fields = {
            "schema",
            "runtime",
            "run_id",
            "agent_manifest_sha256",
            "agent",
            "lifecycle",
            "mode",
            "controller",
            "autonomy_budget",
            "tainted",
            "taint_reason",
            "refreshing",
            "invalidations",
            "errors",
            "environment",
            "session",
            "last_observation",
            "last_directive",
            "last_result",
            "pending_request",
        }
        agent, runtime = status.get("agent"), status.get("runtime")
        if (
            set(status) != fields
            or status["schema"] != AGENT_STATUS
            or status["run_id"] != self.startup["run_id"]
            or status["agent_manifest_sha256"] != self.startup["agent_manifest_sha256"]
            or status["mode"] not in MODES
            or status["lifecycle"] not in {"running", "stopped"}
            or status["controller"] not in {"held", "released", "unknown"}
            or type(status["tainted"]) is not bool
            or type(status["refreshing"]) is not bool
            or (status["taint_reason"] is not None and not isinstance(status["taint_reason"], str))
            or not isinstance(agent, dict)
            or set(agent)
            != {
                "manifest_id",
                "agent_id",
                "agent_version",
                "provider",
                "architecture",
                "artifact_id",
                "artifact_sha256",
                "adapter",
            }
            or agent["manifest_id"] != self.startup["agent_manifest_id"]
            or agent["artifact_sha256"] != self.startup["agent_artifact_sha256"]
            or agent["adapter"] != self.startup["adapter"]
            or not isinstance(runtime, dict)
            or runtime.get("version") != self.startup["runtime_version"]
            or runtime.get("code_sha256") != self.startup["runtime_code_sha256"]
        ):
            raise ValueError
        for key in ("errors", "invalidations"):
            if not isinstance(status[key], list) or any(
                not isinstance(item, str) for item in status[key]
            ):
                raise ValueError
        budget = status["autonomy_budget"]
        if (
            not isinstance(budget, dict)
            or set(budget)
            != {
                "state",
                "max_submissions",
                "submissions_used",
                "max_policy_calls",
                "policy_calls_used",
                "deadline_ms",
                "elapsed_ms",
                "remaining_ms",
                "exhausted_reason",
                "ended_reason",
            }
            or type(budget.get("remaining_ms")) is not int
            or budget["remaining_ms"] < 0
        ):
            raise ValueError
        if _recorded_budget(budget) is None or any(
            budget[status_key] != self.startup["autonomy_budget"][startup_key]
            for startup_key, status_key in (
                ("maxSubmissions", "max_submissions"),
                ("maxPolicyCalls", "max_policy_calls"),
                ("deadlineMs", "deadline_ms"),
            )
        ):
            raise ValueError
        pending = status["pending_request"]
        if pending is not None and (
            not isinstance(pending, dict)
            or set(pending)
            != {
                "request_id",
                "run_id",
                "runtime_instance_id",
                "session_id",
                "submission_epoch",
                "basis_acquisition_id",
                "snapshot_id",
                "action_id",
                "status",
                "reason",
            }
            or pending["run_id"] != self.startup["run_id"]
            or any(
                not isinstance(pending[key], str) or not pending[key]
                for key in (
                    "request_id",
                    "runtime_instance_id",
                    "session_id",
                    "basis_acquisition_id",
                    "snapshot_id",
                    "action_id",
                )
            )
            or type(pending["submission_epoch"]) is not int
            or pending["submission_epoch"] < 0
            or pending["status"] not in {"pending", "unresolved"}
            or (pending["reason"] is not None and not isinstance(pending["reason"], str))
        ):
            raise ValueError


class LocalModelService:
    def __init__(self, config: ProjectConfig, hub: HubClient | None = None, *,
                 managed_target: Callable[[], dict[str, Any]] | None = None) -> None:
        self.config, self.hub = config, hub
        self.managed_target = managed_target
        self.root = ROOT
        self.directory = config.state_dir / "models"
        # The checkout supplies executable code and the shipped catalog. This
        # application-owned directory survives a change of checkout.
        self.private_root = self.directory
        self._private_ids: set[str] = set()
        self.lock = threading.RLock()
        self.control_send_lock = threading.Lock()
        self.intent_generation = 0
        self._recording_source_reservation: object | None = None
        self._recording_admission_guard: Callable[[], bool] | None = None
        self._native_authorizer: Callable[[], None] | None = None
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
        if self.private_root.is_symlink():
            raise BoundaryError("local_model", "private_model_state_unsafe")
        value = _object_file(self.root / "configs/developer/local-policies-v1.json")
        object_fields(value, {"schema", "runtime_package", "policies"}, "local_model.registry")
        if value["schema"] != "stpd/local-policy-registry-v1" or not isinstance(
            value["policies"], list
        ):
            raise BoundaryError("local_model", "unsupported_registry")
        # Operator-created local registrations complement the shipped catalog.
        # They can select only reviewed adapters, never a command or downloaded code.
        # A text profile refers to a separate operator-pinned local Runtime bundle.
        shipped = value["policies"]
        local_path = self.private_root / "token-policies-v1.json"
        if local_path.exists() or local_path.is_symlink():
            local = _object_file(local_path)
            object_fields(local, {"schema", "policies"}, "local_model.local_registry")
            if (local["schema"] != "stpd/local-token-policies-v1"
                    or not isinstance(local["policies"], list)
                    or any(not isinstance(entry, dict)
                           or entry.get("adapter") not in {"token-v1",
                                                            "stpd-m2-decision-adapter",
                                                            "stpd-s0-structured-adapter",
                                                            NATIVE_ADAPTER}
                           or (entry.get("adapter") == NATIVE_ADAPTER
                               and entry.get("runtime_profile") != NATIVE_PROFILE)
                           or (entry.get("adapter") == "stpd-s0-structured-adapter"
                               and entry.get("runtime_profile") != "text-menu-m2-v2")
                           or (entry.get("adapter") == "stpd-m2-decision-adapter"
                               and entry.get("runtime_profile") not in
                               {"text-menu-m2-v1", "text-menu-m2-v2"})
                           for entry in local["policies"])):
                raise BoundaryError("local_model", "invalid_local_token_registry")
            value["policies"] = [*shipped, *local["policies"]]
        seen = set()
        private_ids: set[str] = set()
        for index, entry in enumerate(value["policies"]):
            if not isinstance(entry, dict):
                raise BoundaryError("local_model", "invalid_policy_entry")
            profile = entry.get("runtime_profile")
            if entry.get("adapter") == NATIVE_ADAPTER and profile != NATIVE_PROFILE:
                raise BoundaryError("local_model", "unsupported_runtime_profile")
            if (entry.get("adapter") == "stpd-s0-structured-adapter"
                    and profile != "text-menu-m2-v2"):
                raise BoundaryError("local_model", "unsupported_runtime_profile")
            if "runtime_profile" in entry and (
                (profile == NATIVE_PROFILE and entry.get("adapter") != NATIVE_ADAPTER)
                or (profile != NATIVE_PROFILE and (profile not in TEXT_PROFILES
                or (entry.get("adapter") != TEXT_PROFILES[profile][0]
                    and not (profile == "text-menu-m2-v2" and
                             entry.get("adapter") == "stpd-s0-structured-adapter"))))
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
            owner = self.root if index < len(shipped) else self.private_root
            _inside(owner, entry["manifest"])
            _inside(owner, entry["config"])
            if index >= len(shipped):
                private_ids.add(entry["id"])
        self._private_ids = private_ids
        return value

    def entry_root(self, entry: dict[str, Any]) -> Path:
        return self.private_root if entry["id"] in self._private_ids else self.root

    def entry_path(self, entry: dict[str, Any], key: str) -> Path:
        return _inside(self.entry_root(entry), entry[key])

    def adapter_arguments(self, entry: dict[str, Any]) -> list[str]:
        if entry["adapter"] in {"token-v1", "stpd-m2-decision-adapter"}:
            return ["-m", "stpd.policy.token_port" if entry["adapter"] == "token-v1"
                    else "stpd.policy.memory_port", "--config",
                    str(self.entry_path(entry, "config")), "--manifest",
                    str(self.entry_path(entry, "manifest")), "--binding-root",
                    str(self.entry_root(entry))]
        resolved = {**entry, "config": str(self.entry_path(entry, "config")),
                    "manifest": str(self.entry_path(entry, "manifest"))}
        return cast(list[str], policy_support(entry["adapter"]).arguments(resolved))

    def selection(self, identity: str) -> dict[str, Any]:
        for entry in self.registry()["policies"]:
            if entry["id"] == identity:
                return dict(entry)
        raise BoundaryError("local_model", "unregistered_policy_selection")

    def runtime_profile(self, identity: str | None = None) -> tuple[Path, dict[str, Any]]:
        """Resolve operator-owned pins; a downloaded model cannot select executable code."""
        entry = self.selection(identity) if identity is not None else None
        if entry is not None and entry.get("runtime_profile") in TEXT_PROFILES:
            manifest = _object_file(self.entry_path(entry, "manifest"))
            representation = manifest.get("representation")
            expected_schema = ("sts2.player-environment/text-menu-snapshot-2"
                               if entry["runtime_profile"] == "text-menu-m2-v2" else
                               "sts2.player-environment/text-menu-snapshot-1")
            if (not isinstance(representation, dict)
                    or representation.get("input_schema") != expected_schema):
                raise BoundaryError("local_model", "text_runtime_requires_text_model")
            directory, pin = self.text_runtime_profile(entry["runtime_profile"])
        else:
            directory, pin = self.directory, self.registry()["runtime_package"]
            if entry is not None and entry.get("runtime_profile") == NATIVE_PROFILE:
                manifest = _object_file(self.entry_path(entry, "manifest"))
                if (manifest.get("schema") != "sts2.policy-runtime/agent-manifest-1"
                        or manifest.get("input", {}).get("profile") != NATIVE_PROFILE):
                    raise BoundaryError("local_model", "native_runtime_requires_native_agent")
        if not isinstance(pin, dict) or pin.get("package") != RUNTIME_PACKAGE:
            raise BoundaryError("local_model", "runtime_package_not_pinned")
        return directory, pin

    def text_runtime_profile(self, profile_id: str = "text-menu-v1") -> tuple[Path, dict[str, Any]]:
        """Resolve the exact private text profile before a selection exists."""
        if profile_id not in TEXT_PROFILES:
            raise BoundaryError("local_model", "unsupported_runtime_profile")
        _, profile_path, schema, slot = TEXT_PROFILES[profile_id]
        profile_name = Path(profile_path).name
        profile_file = self.private_root / profile_name
        if self.private_root.is_symlink() or profile_file.is_symlink():
            raise BoundaryError("local_model", "runtime_profile_path_unsafe")
        if not profile_file.exists() and not profile_file.is_symlink():
            raise BoundaryError("local_model", "text_runtime_profile_required")
        profile = _object_file(_inside(self.private_root, profile_name))
        directory = self.directory / slot
        if directory.is_symlink():
            raise BoundaryError("local_model", "runtime_install_path_unsafe")
        from spireagent.workbench.runtime_generation import generation_profile

        resolved = generation_profile(profile, schema, profile_id, directory)
        if resolved is not None:
            generation_dir, pin = resolved
            return generation_dir, pin
        pin = _validate_text_profile(profile, schema, profile_id)
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
        from spireagent.workbench.native_agent_support import (
            PUBLICATION_PROFILE_ID,
            PUBLICATION_PROFILE_SHA256,
        )

        self._runtime_package(identity)
        native = _object_file(manifest_path).get("schema") == "sts2.policy-runtime/agent-manifest-1"
        script = (
            "import {readFile} from 'node:fs/promises';"
            "const runtime=await import(process.argv[1]);"
            "const manifest=JSON.parse(await readFile(process.argv[2],'utf8'));"
            "if(manifest.schema==='sts2.policy-runtime/agent-manifest-1'){"
            "if(typeof runtime.PolicyRuntime?.forAgent!=='function'||"
            "typeof runtime.validateAgentManifest!=='function')"
            "throw Error('native_runtime_required');"
            "const sdk=await import(process.argv[3]);"
            "if(typeof sdk.NativeLogicalSession!=='function'||"
            "typeof sdk.decodeNativeLogicalCapabilities!=='function')"
            "throw Error('native_sdk_required');"
            "const target=sdk.NATIVE_LOGICAL_PUBLICATION_PROFILE;"
            "if(!target||target.profile_id!==process.argv[4]||"
            "sdk.NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256!==process.argv[5])"
            "throw Error('fixed_native_profile_required');"
            "const seams=v=>JSON.stringify(v.map(x=>[x.source_seam,x.version,x.coverage]));"
            "if(seams(manifest.input.attachment.required_seams)!==seams(target.required_seams)||"
            "JSON.stringify(manifest.input.attachment.eager_scope)!==JSON.stringify(target.eager_scope)||"
            "manifest.input.attachment.delivery_mode!==target.delivery_mode)"
            "throw Error('fixed_native_profile_mismatch');"
            "runtime.validateAgentManifest(manifest);"
            "}else{runtime.validatePolicyManifest(manifest);}"
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
                (self._node_modules(identity) / RUNTIME_PACKAGE / "node_modules" /
                 CONNECTOR_PACKAGE / "dist/index.js").as_uri(),
                PUBLICATION_PROFILE_ID,
                PUBLICATION_PROFILE_SHA256,
            ],
            env=environment,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise BoundaryError("local_model", "native_runtime_contract_unavailable" if native
                                else "public_policy_manifest_incompatible")
        if native:
            import sts2_platform_evidence

            if not callable(
                getattr(sts2_platform_evidence, "verify_agent_session_run_evidence", None)
            ):
                raise BoundaryError("local_model", "native_evidence_contract_unavailable")

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

    def _require_stopped_runtime(self) -> None:
        with self.lock:
            if self.closed:
                raise BoundaryError("local_model", "service_closed")
            if (self.client is not None or self.state["loaded"]
                    or self.process is not None and self.process.poll() is None):
                raise BoundaryError("local_model", "stop_runtime_before_install")

    def _selected_kit_text_runtime(self, profile_id: str) -> tuple[bytes, Path, dict[str, Any]]:
        """Read one fixed pair from this process's already selected release only."""
        source = self.root.parent
        release = source.parent
        if (self.root.name != "python" or source.name != "source"
                or not re.fullmatch(r"[a-f0-9]{64}", release.name)
                or release / "source/python" != self.root):
            raise BoundaryError("local_model", "trusted_text_runtime_kit_unavailable")
        _, _, profile_name, archive_name, status_key, _ = KIT_RUNTIME_PAIRS[profile_id]
        identity_key = status_key + "_identity"
        verifier = ROOT / "tools/install_developer_kit.py"
        environment = {key: value for key, value in os.environ.items()
                       if key in {"PATH", "HOME", "SYSTEMROOT", "SystemRoot",
                                  "TMPDIR", "TEMP", "TMP"}}
        try:
            checked = subprocess.run(
                [sys.executable, "-I", str(verifier), "status", "--directory", str(release)],
                cwd=self.root, env=environment, capture_output=True, timeout=120, check=False,
            )
            receipt = decode_json(checked.stdout) if checked.returncode == 0 else None
        except (OSError, ValueError, BoundaryError, subprocess.SubprocessError):
            receipt = None
        if not isinstance(receipt, dict) or receipt.get("status") != "prepared":
            raise BoundaryError("local_model", "trusted_text_runtime_kit_invalid")
        if receipt.get("directory") != str(release):
            raise BoundaryError("local_model", "trusted_text_runtime_kit_invalid")
        if receipt.get(status_key) == "not_bundled":
            raise BoundaryError("local_model", "trusted_text_runtime_asset_not_bundled")
        identity = receipt.get(identity_key)
        if (receipt.get(status_key) != "bundled_installation_not_checked"
                or not isinstance(identity, dict)
                or set(identity) != {"profile_sha256", "archive_sha256"}):
            raise BoundaryError("local_model", "trusted_text_runtime_kit_invalid")
        profile_path, archive_path = source / profile_name, source / archive_name
        if (profile_path.is_symlink() or archive_path.is_symlink()
                or not profile_path.is_file() or not archive_path.is_file()):
            raise BoundaryError("local_model", "trusted_text_runtime_kit_invalid")
        if profile_path.stat().st_size > JSON_LIMIT or archive_path.stat().st_size > ARCHIVE_LIMIT:
            raise BoundaryError("local_model", "trusted_text_runtime_kit_invalid")
        profile_raw, archive_raw = profile_path.read_bytes(), archive_path.read_bytes()
        if (hashlib.sha256(profile_raw).hexdigest() != identity["profile_sha256"]
                or hashlib.sha256(archive_raw).hexdigest() != identity["archive_sha256"]):
            raise BoundaryError("local_model", "trusted_text_runtime_kit_changed")
        pin = text_runtime_pin(profile_raw, archive_raw,
                               **({"required_profile": profile_id}
                                  if profile_id == "text-menu-m2-v2" else
                                  {"memory": profile_id == "text-menu-m2-v1"}))
        return profile_raw, archive_path, pin

    def prepare_text_runtime(self, profile_id: str) -> dict[str, Any]:
        """Explicitly prepare an exact private text Runtime; never load a model."""
        if not isinstance(profile_id, str) or profile_id not in TEXT_PROFILES:
            raise BoundaryError("local_model", "unsupported_runtime_profile")

        def prepare() -> None:
            self._require_stopped_runtime()
            try:
                directory, pin = self.text_runtime_profile(profile_id)
            except BoundaryError as error:
                if error.code != "text_runtime_profile_required":
                    raise
                directory, pin = None, None
            if directory is not None and pin is not None:
                if directory.parent.name == "generations":
                    from spireagent.workbench.runtime_generation import _verify

                    _verify(directory, pin, self._connector_pin(), profile_id)
                    with self.lock:
                        self._require_stopped_runtime()
                        self.state.update(status="idle", last_text_runtime_preparation={
                            "runtime_profile": profile_id, "status": "ready", "reused": True,
                        })
                    return
                try:
                    installed = validate_runtime_install(
                        directory / "runtime/node_modules", pin, self._connector_pin())
                except (OSError, ValueError, StopIteration, PackageIdentityError):
                    installed = None
                if installed is not None and profile_id == "text-menu-m2-v2":
                    sdk = (directory / "runtime/node_modules" / RUNTIME_PACKAGE /
                           "node_modules" / CONNECTOR_PACKAGE / "dist/index.js")
                    if not v2_sdk_available(sdk):
                        raise BoundaryError("local_model", "v2_runtime_contract_unavailable")
                if installed is not None:
                    with self.lock:
                        self._require_stopped_runtime()
                        self.state.update(status="idle", last_text_runtime_preparation={
                            "runtime_profile": profile_id, "status": "ready", "reused": True,
                        })
                    return
            profile_raw, archive, kit_pin = self._selected_kit_text_runtime(profile_id)
            if pin is not None and pin != kit_pin:
                raise BoundaryError("local_model", "private_profile_collision")
            self._require_stopped_runtime()
            if pin is None:
                # The durable state owner publishes complete bytes, without
                # replacing a different operator pin or touching the source tree.
                from spireagent.workbench.model_state_migration import _publish_profile

                if self.private_root.is_symlink():
                    raise BoundaryError("local_model", "private_model_state_unsafe")
                self.private_root.mkdir(parents=True, exist_ok=True)
                _publish_profile(self.private_root / Path(TEXT_PROFILES[profile_id][1]).name,
                                 profile_raw)
            directory, current_pin = self.text_runtime_profile(profile_id)
            if current_pin != kit_pin:
                raise BoundaryError("local_model", "private_profile_collision")
            self._require_stopped_runtime()
            installed = install_runtime(directory, current_pin, self._connector_pin(),
                                        archive=archive,
                                        **({"required_profile": profile_id}
                                           if profile_id == "text-menu-m2-v2" else {}))
            with self.lock:
                self._require_stopped_runtime()
                self.state.update(status="idle", last_runtime_install=installed,
                                  last_text_runtime_preparation={
                                      "runtime_profile": profile_id, "status": "ready",
                                      "reused": False,
                                  })

        return self._begin("prepare-text-runtime", prepare,
                           admission=self._require_stopped_runtime)

    def catalog(self) -> dict[str, Any]:
        entries = []
        for entry in self.registry()["policies"]:
            manifest = _object_file(self.entry_path(entry, "manifest"))
            bounded = entry.get("runtime_profile") in {*TEXT_PROFILES, NATIVE_PROFILE}
            profiles = [{"id": "short", "label": "短时检查" if bounded else "默认运行",
                         "limits": RUN_PROFILES["short"] if bounded else None}]
            if bounded:
                profiles.append({"id": "extended",
                                 "label": "较长尝试（每次自主授权最多 30 分钟）",
                                 "limits": RUN_PROFILES["extended"]})
            entries.append(
                {
                    "selection_id": entry["id"],
                    "label": entry["label"],
                    "support": manifest.get("support"),
                    "claims": manifest.get("claims"),
                    "artifact_sha256": manifest.get("artifact", {}).get("sha256"),
                    "readiness": "check_required",
                    **({"agent_scope": "s0_text_v2_compatibility_only"}
                       if entry["adapter"] == "stpd-s0-structured-adapter" else {}),
                    "default_run_profile": "short",
                    "run_profiles": profiles,
                    "run_profile_unavailable_reason": (
                        None if bounded
                        else "extended_requires_text_menu_runtime"
                    ),
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
                            "support_status": ("export_and_registration_required" if
                                is_structured_model_schema(
                                    downloaded.parameters.value().get("schema")) else
                                "unsupported"),
                            "reason": ("s0_text_v2_only" if
                                is_structured_model_schema(
                                    downloaded.parameters.value().get("schema")) else
                                "no_compatible_live_adapter_and_input_parity"),
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
        manifest_path = self.entry_path(entry, "manifest")
        config_path = self.entry_path(entry, "config")
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

        if entry["adapter"] in {"token-v1", "stpd-m2-decision-adapter",
                                 "stpd-s0-structured-adapter", NATIVE_ADAPTER}:
            from spireagent.workbench.local_model_dependencies import (
                local_models_available,
                native_models_available,
            )

            available = (native_models_available() if entry["adapter"] == NATIVE_ADAPTER
                         else local_models_available())
            if available:
                adapter = policy_support(entry["adapter"])
                checks.update(adapter.inspect(self.root, entry, manifest, policy_config,
                                              binding_root=self.entry_root(entry)))
            else:
                checks["policy_identity"] = {
                    "status": "blocked", "code": ("native_models_extra_required"
                                                  if entry["adapter"] == NATIVE_ADAPTER
                                                  else "local_models_extra_required"),
                }
        else:
            adapter = policy_support(entry["adapter"])
            checks.update(adapter.inspect(self.root, entry, manifest, policy_config))
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

    def _recording_recovery_required(self, model: dict[str, Any]) -> bool:
        operation = model.get("operation")
        if model.get("status") in {"loading", "command_unknown", "recovery_required"} or (
            isinstance(operation, dict)
            and operation.get("status") == "pending"
            and operation.get("action") not in {"human", "stop", "reconcile"}
        ):
            return True
        if not model.get("loaded") and self.client is None:
            return False
        runtime = model.get("runtime")
        return (
            model.get("observation_error") is not None
            or not isinstance(runtime, dict)
            or runtime.get("mode") != "human"
            or runtime.get("controller") != "released"
            or runtime.get("tainted") is True
            or runtime.get("pending_request") is not None
        )

    def recording_recovery_required(self, *, fresh: bool = False) -> bool:
        observed = self.status() if fresh else None
        with self.lock:
            return self._recording_recovery_required(self.state) or (
                observed is not None and self._recording_recovery_required(observed)
            )

    @contextmanager
    def reserve_recording_source_mutation(self, *, require_human: bool) -> Iterator[None]:
        """Reserve application sequencing, never gameplay or external SDK authority."""
        reservation = object()
        with self.lock:
            if require_human and (self.closed or self._recording_recovery_required(self.state)):
                raise BoundaryError("recording", "model_recovery_required")
            if self._recording_source_reservation is not None:
                raise BoundaryError("recording", "native_recording_command_pending")
            self._recording_source_reservation = reservation
        try:
            # HTTP is outside the lock. Existing model admissions reject immediately;
            # Human/Stop retain the independent recovery lane while this intent waits.
            if require_human and self.recording_recovery_required(fresh=True):
                raise BoundaryError("recording", "model_recovery_required")
            yield
        finally:
            with self.lock:
                if self._recording_source_reservation is reservation:
                    self._recording_source_reservation = None

    def bind_recording_admission_guard(self, guard: Callable[[], bool]) -> None:
        """Application's existing unresolved notice is read under this owner lock."""
        with self.lock:
            self._recording_admission_guard = guard

    def _require_model_admission(self) -> None:
        if self._recording_source_reservation is not None or (
            self._recording_admission_guard is not None and self._recording_admission_guard()
        ):
            raise BoundaryError("local_model", "native_recording_command_pending")

    def _begin(
        self, action: str, operation: Callable[[], None], *, recovery: bool = False,
        admission: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            if self.closed:
                raise BoundaryError("local_model", "service_closed")
            if not recovery:
                self._require_model_admission()
            if self.thread and self.thread.is_alive() and not recovery:
                raise BoundaryError("local_model", "operation_in_progress")
            if self.state["status"] in {"command_unknown", "recovery_required"} and not recovery:
                raise BoundaryError("local_model", "previous_operation_requires_recovery")
            if admission is not None:
                admission()
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
                                "native_pending_request_required", "native_pending_request_changed",
                                "runtime_pending_request_mismatch", "runtime_run_mismatch",
                                "runtime_profile_reconcile_unsupported",
                                "managed_environment_unavailable",
                                "managed_environment_target_unavailable",
                                "managed_environment_target_changed",
                                "managed_environment_target_invalid",
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

    def _run_profile(self, identity: str, profile: str) -> bool:
        if not isinstance(profile, str) or profile not in RUN_PROFILES:
            raise BoundaryError("local_model", "unsupported_run_profile")
        bounded = self.selection(identity).get("runtime_profile") in {
            *TEXT_PROFILES,
            NATIVE_PROFILE,
        }
        if profile == "extended" and not bounded:
            raise BoundaryError("local_model", "extended_requires_text_menu_runtime")
        return bounded

    def start(self, identity: str, run_profile: str = "short") -> dict[str, Any]:
        self._run_profile(identity, run_profile)
        intent = self.intent_generation

        def admit() -> None:
            nonlocal intent
            if self.process is not None and self.process.poll() is None:
                raise BoundaryError("local_model", "runtime_already_running")
            # A newly admitted ordinary start owns a new intent. It must not
            # inherit native authorization or an old request's recovery proof.
            # _begin runs this only after its rejection checks under our lock.
            self.intent_generation += 1
            intent = self.intent_generation
            self.state.pop("_native_intent", None)
            self._native_authorizer = None

        return self._begin(
            "start", lambda: self._start(identity, intent) if run_profile == "short"
            else self._start(identity, intent, run_profile), admission=admit,
        )

    def prepare_and_load(self, identity: str, run_profile: str = "short", *,
                         native_context: dict[str, Any] | None = None,
                         native_authorizer: Callable[[], None] | None = None) -> dict[str, Any]:
        """Prepare the existing selection in Human mode."""
        return self._prepare_and_load(identity, run_profile, takeover=False,
                                      native_context=native_context,
                                      native_authorizer=native_authorizer)

    def prepare_and_takeover(self, identity: str, run_profile: str = "short", *,
                            native_context: dict[str, Any] | None = None,
                            native_authorizer: Callable[[], None] | None = None) -> dict[str, Any]:
        """One application intent owns preparation, Human load and bounded Auto."""
        return self._prepare_and_load(identity, run_profile, takeover=True,
                                      native_context=native_context,
                                      native_authorizer=native_authorizer)

    def _prepare_and_load(self, identity: str, run_profile: str, *,
                          takeover: bool, native_context: dict[str, Any] | None,
                          native_authorizer: Callable[[], None] | None) -> dict[str, Any]:
        """Prepare a reviewed selection, then load in Human mode; never fetch model weights.

        Only the existing bounded, hash-pinned Runtime installer is automatic.
        Adapter/weights/backend requirements remain explicit owning readiness checks.
        """
        self._run_profile(identity, run_profile)
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
            if run_profile == "short":
                self._start(identity, intent)
            else:
                self._start(identity, intent, run_profile)
            if takeover:
                with self.lock:
                    self._require_intent(intent)
                    client = self.client
                    connector_endpoint = self.state.get("connector_endpoint")
                    managed_environment = self.state.get("managed_environment")
                if client is None:
                    raise BoundaryError("local_model", "model_not_loaded")
                self._execute_command("auto", intent, client, connector_endpoint,
                                      managed_environment)
            with self.lock:
                self._require_intent(intent)
                self.state["preparation_stage"] = "ready"

        def admit() -> None:
            self._require_intent(intent)
            if native_context is not None:
                if native_authorizer is None or set(native_context) != {"request_id", "binding"}:
                    raise BoundaryError("local_model", "native_intent_context_required")
                digest(native_context["request_id"], "local_model.native_request", length=32)
                from spireagent.workbench.native_workbench_access import NativePair

                NativePair.from_dict(native_context["binding"])
                self.state["_native_intent"] = {**json.loads(json.dumps(native_context)),
                                                 "intent_generation": intent}
                self._native_authorizer = native_authorizer
            else:
                self.state.pop("_native_intent", None)
                self._native_authorizer = None

        return self._begin("prepare-and-takeover" if takeover else "prepare-and-load", prepare,
                           admission=admit)

    def native_intent_context(self, request_id: object) -> dict[str, Any]:
        identity = digest(request_id, "local_model.native_request", length=32)
        with self.lock:
            context = self.state.get("_native_intent")
            if (not isinstance(context, dict) or context.get("request_id") != identity
                    or context.get("intent_generation") != self.intent_generation or self.closed):
                raise BoundaryError("local_model", "native_model_intent_superseded")
            return cast(dict[str, Any], json.loads(json.dumps(context)))

    def recover_native_intent(self, request_id: object, action: str) -> dict[str, Any]:
        if action not in {"human", "stop"}:
            raise BoundaryError("local_model", "native_recovery_action_required")
        with self.lock:
            self.native_intent_context(request_id)
            # Validation and the existing command's intent increment are atomic.
            return self.command(action)

    def _require_intent(self, intent: int) -> None:
        with self.lock:
            if self.closed or intent != self.intent_generation:
                raise BoundaryError("local_model", "command_superseded")

    def _managed_runtime_target(self) -> dict[str, Any]:
        if self.managed_target is None:
            raise BoundaryError("local_model", "managed_environment_target_unavailable")
        try:
            return self.managed_target()
        except (BoundaryError, OSError, ValueError) as error:
            # This callback is the environment owner's read-only precondition;
            # no model or native command has been sent by this operation.
            raise BoundaryError("local_model", "managed_environment_unavailable") from error

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
            native = self.state.get("_native_intent")
            if (
                route in {"/mode", "/tick"}
                and not recovery
                and isinstance(native, dict)
                and native.get("intent_generation") == intent
            ):
                if self._native_authorizer is None:
                    raise BoundaryError("local_model", "native_intent_authorization_required")
                self._native_authorizer()
                self._require_intent(intent)
            value = (client.request(route, body, binding=binding)
                     if binding is not None else client.request(route, body))
            self._require_intent(intent)
            return value

    def _start(self, identity: str, intent: int | None = None,
               run_profile: str = "short") -> None:
        bounded = self._run_profile(identity, run_profile)
        if intent is None:
            intent = self.intent_generation
        report = self.readiness(identity)
        with self.lock:
            self._require_intent(intent)
            self.state.update(selection_id=identity, readiness=report,
                              run_profile=run_profile)
        if report["status"] != "ready_to_load":
            raise BoundaryError("local_model", "model_readiness_blocked")
        entry = self.selection(identity)
        manifest_path = self.entry_path(entry, "manifest")
        manifest = _object_file(manifest_path)
        package = self._runtime_package(identity)
        _check_runtime_port(15527)
        managed = managed_manifest(manifest)
        native_context = self.state.get("_native_intent")
        if (managed and isinstance(native_context, dict)
                and native_context.get("intent_generation") == intent):
            raise BoundaryError("local_model", "native_model_target_unavailable")
        target = self._managed_runtime_target() if managed else None
        selected_target = public_target(target) if target is not None else None
        connector = (None if managed else
                     _loopback(self.config.platform_url or "http://127.0.0.1:15526"))
        if target is not None:
            environment_arguments = runtime_arguments(target, self.private_root)
        else:
            assert connector is not None
            if (isinstance(native_context, dict)
                    and native_context.get("intent_generation") == intent):
                if self._native_authorizer is None:
                    raise BoundaryError("local_model", "native_intent_authorization_required")
                self._native_authorizer()
                if (self.native_tasks.connector_instance(connector)
                        != native_context["binding"]["runtime_instance_id"]):
                    raise BoundaryError("local_model", "native_model_context_changed")
                self._require_intent(intent)
            environment_arguments = ["--connector-endpoint", connector]
        command = [
            "node",
            str(self._node_modules(identity) / RUNTIME_PACKAGE / "dist/cli.js"),
            "--manifest",
            str(manifest_path),
            "--adapter-command",
            sys.executable,
            "--adapter-cwd",
            str(self.root),
            *["--adapter-arg=" + arg for arg in self.adapter_arguments(entry)],
            *environment_arguments,
            "--listen-port",
            "15527",
            "--evidence-root",
            str(self.directory / "agent-runs"),
            "--mode",
            "human",
        ]
        if bounded:
            limits = RUN_PROFILES[run_profile]
            command.extend(("--max-auto-submissions", str(limits["max_submissions"]),
                            "--max-policy-calls", str(limits["max_policy_calls"]),
                            "--auto-deadline-ms", str(limits["deadline_ms"])))
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
            if selected_target is None:
                self.state.pop("managed_environment", None)
            else:
                self.state["managed_environment"] = selected_target
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
            expected = {**_startup_identity(manifest, package), "mode": "human"}
            if any(
                startup.get(key) != value for key, value in expected.items()
            ) or not re.fullmatch(r"run-[a-f0-9-]{36}", startup.get("run_id", "")):
                raise ValueError
            if expected["schema"] == AGENT_STARTUP and set(startup) != {
                *expected, "run_id", "autonomy_budget"}:
                raise ValueError
            if startup.get("managed_environment") != selected_target:
                raise ValueError
            if bounded and startup.get("autonomy_budget") != {
                "maxSubmissions": limits["max_submissions"],
                "maxPolicyCalls": limits["max_policy_calls"],
                "deadlineMs": limits["deadline_ms"],
            }:
                raise ValueError
            client = RuntimeClient(startup["address"], startup)
            runtime = client.request("/status")["status"]
            if bounded and (not isinstance(runtime.get("autonomy_budget"), dict)
                              or any(runtime["autonomy_budget"].get(key) != value
                                     for key, value in limits.items())):
                raise ValueError
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
            intent = self.intent_generation
            if (
                self.process is not None
                and self.process.poll() is not None
                and self.state["status"] == "loaded"
            ):
                self.state.update(status="runtime_exited", loaded=False)

        def current_observation() -> bool:
            operation = self.state.get("operation")
            return (
                not self.closed
                and self.client is client
                and self.intent_generation == intent
                and self.state["status"] != "stopped"
                and not (isinstance(operation, dict)
                         and operation.get("action") == "stop"
                         and operation.get("status") == "pending")
            )

        if client is not None:
            try:
                runtime = client.request("/status")["status"]
                with self.lock:
                    if current_observation():
                        self.state["runtime"] = runtime
                        self.state.pop("observation_error", None)
            except BoundaryError as error:
                with self.lock:
                    if current_observation():
                        self.state["observation_error"] = error.code
        with self.lock:
            return cast(dict[str, Any], json.loads(json.dumps(self.state)))

    @staticmethod
    def read_control_context(value: object) -> tuple[str, RuntimeControlBinding]:
        if (
            not isinstance(value, dict)
            or set(value) != {"runtime_run_id", "runtime_instance_id", "recovery_epoch"}
            or not NativeTasks._recording_identifier(value["runtime_run_id"])
            or not NativeTasks._recording_identifier(value["runtime_instance_id"])
        ):
            raise BoundaryError("local_model", "invalid_model_control_context")
        return value["runtime_run_id"], RuntimeControlBinding(
            value["runtime_instance_id"], value["recovery_epoch"]
        )

    def control_context(self) -> dict[str, Any]:
        """Readonly owned Runtime precondition; never infer ownership from a listening port."""
        with self.lock:
            client = self.client
            generation = self.intent_generation
            if client is None or not self.state["loaded"]:
                raise BoundaryError("local_model", "model_not_loaded")
        observed = client.request("/status")["status"]
        binding = RuntimeControlBinding.from_environment(
            client.request("/environment"), observed["run_id"]
        )
        NativeTasks.confirm_runtime(observed, binding.runtime_instance_id)
        NativeTasks.model_context(observed, observed["run_id"], binding.recovery_epoch)
        with self.lock:
            self._require_intent(generation)
            if self.client is not client or not self.state["loaded"]:
                raise BoundaryError("local_model", "native_model_context_changed")
        return {
            "schema": "spireagent/local-model-control-context-1",
            "runtime_run_id": observed["run_id"],
            "runtime_instance_id": binding.runtime_instance_id,
            "recovery_epoch": binding.recovery_epoch,
        }

    def command(
        self,
        action: str,
        *,
        request_id: str | None = None,
        expected_context: dict[str, Any] | None = None,
        native_context: dict[str, Any] | None = None,
        native_authorizer: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if action not in MODES | {"stop", "reconcile", "tick"} or (
            (action == "reconcile") != (request_id is not None)
        ):
            raise BoundaryError("local_model", "unsupported_local_command")
        expected = (
            self.read_control_context(expected_context) if expected_context is not None else None
        )
        if expected is not None and action not in {"auto", "shadow", "one_step", "tick"}:
            raise BoundaryError("local_model", "invalid_model_control_context")
        observed_generation = None
        if expected is not None:
            with self.lock:
                self._require_model_admission()
                if self.client is None or self.client.startup.get("run_id") != expected[0]:
                    raise BoundaryError("local_model", "runtime_run_mismatch")
                observed_generation = self.intent_generation
            fresh = self.control_context()
            if fresh["runtime_run_id"] != expected[0]:
                raise BoundaryError("local_model", "runtime_run_mismatch")
            if fresh["runtime_instance_id"] != expected[1].runtime_instance_id:
                raise BoundaryError("local_model", "runtime_game_mismatch")
            if fresh["recovery_epoch"] != expected[1].recovery_epoch:
                raise BoundaryError("local_model", "runtime_recovery_epoch_mismatch")
        with self.lock:
            if observed_generation is not None and observed_generation != self.intent_generation:
                raise BoundaryError("local_model", "native_model_context_changed")
            if expected is not None and (
                self.client is None or self.client.startup.get("run_id") != expected[0]
            ):
                raise BoundaryError("local_model", "runtime_run_mismatch")
            if native_context is not None:
                if native_authorizer is None or set(native_context) != {"request_id", "binding"}:
                    raise BoundaryError("local_model", "native_intent_context_required")
                digest(native_context["request_id"], "local_model.native_request", length=32)
                from spireagent.workbench.native_workbench_access import NativePair

                NativePair.from_dict(native_context["binding"])
            if action == "reconcile":
                observed = self.state.get("runtime")
                pending = observed.get("pending_request") if isinstance(observed, dict) else None
                if (
                    not isinstance(observed, dict)
                    or observed.get("schema") != AGENT_STATUS
                    or not isinstance(pending, dict)
                    or pending.get("request_id") != request_id
                ):
                    raise BoundaryError("local_model", "native_pending_request_required")
                pending_request = json.loads(json.dumps(pending))
            else:
                pending_request = None
            recovery = action in {"human", "stop", "reconcile"}
            if self.closed:
                raise BoundaryError("local_model", "service_closed")
            if not recovery:
                self._require_model_admission()
                if self.state["status"] in {"command_unknown", "recovery_required"}:
                    raise BoundaryError("local_model", "previous_operation_requires_recovery")
            if not recovery and self.thread is not None and self.thread.is_alive():
                raise BoundaryError("local_model", "operation_in_progress")
            if self.client is None or not self.state["loaded"]:
                if action in {"human", "stop"} and self.state.get("previous_session"):
                    self.intent_generation += 1
                    intent = self.intent_generation
                    return self._begin(
                        action, lambda: self._recover(action, intent), recovery=True
                    )
                if (
                    action in {"human", "stop"}
                    and self.thread is not None
                    and self.thread.is_alive()
                    and (
                        (self.state.get("operation") or {}).get("action")
                        in {"start", "prepare-and-load", "prepare-and-takeover"}
                    )
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
            managed_environment = self.state.get("managed_environment")
            if native_context is not None:
                self.state["_native_intent"] = {
                    **json.loads(json.dumps(native_context)),
                    "intent_generation": intent,
                }
                self._native_authorizer = native_authorizer

            if action == "reconcile":
                assert isinstance(pending_request, dict)
                return self._begin(
                    action,
                    lambda: self._execute_reconcile(intent, client, pending_request),
                    recovery=True,
                )
            return self._begin(
                action,
                lambda: self._execute_command(
                    action, intent, client, connector_endpoint, managed_environment, expected
                ),
                recovery=action in {"human", "stop"},
            )

    def _execute_reconcile(self, intent: int, client: RuntimeClient,
                           pending_request: dict[str, Any]) -> None:
        self._require_intent(intent)
        observed = client.request("/status")["status"]
        if (observed.get("schema") != AGENT_STATUS
                or observed.get("pending_request") != pending_request):
            raise BoundaryError("local_model", "native_pending_request_changed")
        binding = RuntimeControlBinding.from_environment(
            client.request("/environment"), pending_request["run_id"])
        if binding.runtime_instance_id != pending_request["runtime_instance_id"]:
            raise BoundaryError("local_model", "runtime_game_mismatch")
        result = self._send_control(client, "/reconcile",
                                    {"request_id": pending_request["request_id"]}, intent, binding)
        with self.lock:
            self._require_intent(intent)
            if self.client is not client:
                raise BoundaryError("local_model", "command_superseded")
            self.state.update(runtime=result["status"], status="loaded", error_code=None,
                              last_reconciliation={"request_id": result["request_id"],
                                                   "resolution": result["resolution"]})

    def _execute_command(
        self,
        action: str,
        intent: int,
        client: RuntimeClient,
        connector_endpoint: Any,
        managed_environment: Any,
        expected: tuple[str, RuntimeControlBinding] | None = None,
    ) -> None:
        assert client is not None
        self._require_intent(intent)
        # Observe exact instance before every mutation; never address a new
        # process that reused the same port after our owned process exited.
        observation = client.request("/status")["status"]
        binding = None
        if action in {"shadow", "one_step", "auto", "tick"}:
            self._require_intent(intent)
            binding = RuntimeControlBinding.from_environment(
                client.request("/environment"), observation["run_id"]
            )
            if expected is not None:
                if observation["run_id"] != expected[0]:
                    raise BoundaryError("local_model", "runtime_run_mismatch")
                if binding.runtime_instance_id != expected[1].runtime_instance_id:
                    raise BoundaryError("local_model", "runtime_game_mismatch")
                if binding.recovery_epoch != expected[1].recovery_epoch:
                    raise BoundaryError("local_model", "runtime_recovery_epoch_mismatch")
                binding = expected[1]
            native_context = self.state.get("_native_intent")
            if (isinstance(native_context, dict)
                    and native_context.get("intent_generation") == intent
                    and native_context["binding"]["runtime_instance_id"]
                    != binding.runtime_instance_id):
                raise BoundaryError("local_model", "native_model_context_changed")
            # Capture the shared Runtime epoch before native preparation.
            # A recovery from either UI invalidates this exact observation;
            # never refresh its epoch to make a stale intent eligible again.
            if managed_environment is not None:
                current_target = self._managed_runtime_target()
                confirm_target(managed_environment, current_target)
                if current_target["runtime_instance_id"] != binding.runtime_instance_id:
                    raise BoundaryError("local_model", "runtime_game_mismatch")
                # Managed attaches to the selected Host. It must never call
                # the native Mod's recorder preparation or reset the Host.
                self._require_intent(intent)
            else:
                bound_endpoint = NativeTasks.bound_connector(connector_endpoint)
                instance = self.native_tasks.connector_instance(bound_endpoint)
                if instance != binding.runtime_instance_id:
                    raise BoundaryError("local_model", "runtime_game_mismatch")
                NativeTasks.confirm_runtime(observation, binding.runtime_instance_id)
                self._require_intent(intent)
                context = NativeTasks.model_context(
                    observation, observation["run_id"], binding.recovery_epoch
                )
                native = self.native_tasks.prepare_model(
                    observation, bound_endpoint, model_context=context
                )
                if native["runtime_instance_id"] != binding.runtime_instance_id:
                    raise BoundaryError("local_model", "runtime_game_mismatch")
                # Native Close cannot authorize a replacement Runtime or game.
                latest = client.request("/status")["status"]
                NativeTasks.confirm_runtime(latest, binding.runtime_instance_id)
        if action == "tick":
            runtime = self._send_control(client, "/tick", {"max_ticks": 1}, intent, binding)[
                "status"
            ]
        elif action == "stop":
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
                self.state.pop("observation_error", None)
                self.client = None

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
        manifest = _object_file(self.entry_path(entry, "manifest"))
        package = self._runtime_package(entry["id"])
        expected_startup = _startup_identity(manifest, package)
        if manifest.get("schema") != "sts2.policy-runtime/agent-manifest-1":
            # Preserve the established legacy recovery identity contract.
            # Native startup provenance always requires its explicit namespace.
            expected_startup.pop("schema")
        if not isinstance(startup, dict) or any(
            startup.get(key) != expected
            for key, expected in expected_startup.items()
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
        managed_environment = (previous.get("managed_environment")
                               if managed_manifest(manifest) else None)
        if managed_manifest(manifest):
            connector_endpoint = None
            if not isinstance(managed_environment, dict) or (
                managed_environment != startup.get("managed_environment")
            ):
                managed_environment = None
        else:
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
                    ("managed_environment_target_unavailable" if managed_manifest(manifest)
                     else "runtime_connector_binding_required")
                    if self.client is not None and connector_endpoint is None
                    and managed_environment is None else None
                ),
            )
            if managed_environment is None:
                self.state.pop("managed_environment", None)
            else:
                self.state["managed_environment"] = managed_environment
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
        native = isinstance(startup, dict) and startup.get("schema") == AGENT_STARTUP
        required = {
            "run_id", "agent_manifest_id" if native else "manifest_id",
            "agent_manifest_sha256" if native else "policy_manifest_sha256",
            "agent_artifact_sha256" if native else "policy_artifact_sha256",
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
        if native:
            from sts2_platform_evidence import verify_agent_session_run_evidence

            result: Any = verify_agent_session_run_evidence(directory, startup)
        else:
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
        if startup.get("schema") == AGENT_STARTUP:
            self._native_operation_handoff(startup)
            return
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
            "recorded_action_verbs": None,
            "native_submission_attempts": None,
            "budget_summary": None,
            "budget_end_reason": None,
            "terminal_screen_observation": None,
        }
        if result.value is not None:
            report.update(
                evidence_content_id=result.value.content_id, event_count=result.value.event_count
            )
            counts: Counter[str] = Counter()
            deliveries: Counter[str] = Counter()
            verbs: Counter[str] = Counter()
            native_submissions: int | None = None
            budget_summary: dict[str, Any] | None = None
            budget_end_reason: str | None = None
            terminal_results: set[str] = set()
            terminal_count = 0
            first_terminal_sequence: int | None = None
            last_terminal_sequence: int | None = None
            # Only read events after the owning verifier accepted exact bytes and
            # their relationship to this model/runtime. Counts are operational.
            with (directory / "events.jsonl").open(encoding="utf-8") as events:
                for line in events:
                    event = json.loads(line)
                    counts[event["kind"]] += 1
                    payload = event["payload"]
                    snapshot = None
                    if event["kind"] in {"text_decision_input", "text_observation_not_admitted"}:
                        snapshot = payload.get("snapshot")
                    elif event["kind"] == "text_observed_successor":
                        snapshot = payload.get("successor")
                    elif event["kind"] == "menu_navigation":
                        result = payload.get("result")
                        if isinstance(result, dict):
                            snapshot = result.get("successor")
                    terminal = _terminal_screen(snapshot)
                    if terminal is not None:
                        terminal_results.add(terminal)
                        terminal_count += 1
                        sequence = event.get("sequence")
                        if type(sequence) is int and sequence > 0:
                            if first_terminal_sequence is None:
                                first_terminal_sequence = sequence
                            last_terminal_sequence = sequence
                    if (event["kind"] == "text_menu_dispatch_attempt"
                            and payload.get("effect_domain") in {"native_input", "text_menu"}):
                        native_submissions = (native_submissions or 0) + (
                            payload["effect_domain"] == "native_input"
                        )
                    if event["kind"] in {"menu_navigation", "text_native_delivery",
                                         "text_native_unknown", "text_menu_not_applied"}:
                        outcome = payload.get("result")
                        action = outcome.get("action") if isinstance(outcome, dict) else None
                        verb = action.get("verb") if isinstance(action, dict) else None
                        if isinstance(verb, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", verb):
                            verbs[verb] += 1
                    if event["kind"] == "receipt":
                        receipt = payload.get("receipt")
                        action = receipt.get("action") if isinstance(receipt, dict) else None
                        verb = action.get("verb") if isinstance(action, dict) else None
                        if isinstance(verb, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", verb):
                            verbs[verb] += 1
                    if event["kind"] in {"stopped", "mode_changed", "one_step_completed",
                                         "autonomy_budget_exhausted"}:
                        candidate = _recorded_budget(payload.get(
                            "budget" if event["kind"] == "autonomy_budget_exhausted"
                            else "autonomy_budget"
                        ))
                        if candidate is not None:
                            budget_summary = candidate
                            budget_end_reason = candidate["exhausted_reason"]
                    if event["kind"] == "receipt":
                        receipt = payload.get("receipt")
                        delivery = receipt.get("delivery") if isinstance(receipt, dict) else None
                        if isinstance(delivery, str) and delivery in {
                            "delivered", "not_delivered", "unknown",
                        }:
                            deliveries[delivery] += 1
                    elif event["kind"] in {
                        "text_native_delivery", "text_native_unknown", "text_menu_not_applied",
                    }:
                        outcome = payload.get("result")
                        # Navigation has no native delivery. Rejected/mismatched
                        # replies are diagnostics, not a correlated outcome.
                        if (isinstance(outcome, dict)
                                and outcome.get("effect_domain") == "native_input"
                                and isinstance(delivery := outcome.get("native_delivery"), str)
                                and delivery in {"delivered", "not_delivered", "unknown"}):
                            deliveries[delivery] += 1
            report.update(
                event_counts=dict(counts), delivery_counts=dict(deliveries),
                recorded_action_verbs=dict(verbs) if verbs else None,
                native_submission_attempts=native_submissions,
                budget_summary=budget_summary, budget_end_reason=budget_end_reason,
                terminal_screen_observation=(
                    {
                        "status": "observed" if len(terminal_results) == 1 else "ambiguous",
                        "result": next(iter(terminal_results)) if len(terminal_results) == 1
                        else None,
                        "observation_count": terminal_count,
                        "first_event_sequence": first_terminal_sequence,
                        "last_event_sequence": last_terminal_sequence,
                    } if terminal_count else None
                ),
            )
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

    def _native_operation_handoff(self, startup: dict[str, Any]) -> None:
        from sts2_platform_evidence import verify_agent_session_run_evidence

        directory = self.directory / "agent-runs" / startup["run_id"]
        result = verify_agent_session_run_evidence(directory, startup)
        report: dict[str, Any] = {
            "schema": "stpd/local-native-agent-operation-handoff-v1",
            "selection_id": self.state["selection_id"], "run_id": startup["run_id"],
            "model_sha256": startup["agent_artifact_sha256"],
            "agent_manifest_sha256": startup["agent_manifest_sha256"],
            "runtime_code_sha256": startup["runtime_code_sha256"],
            "evidence_verification": result.status,
            "findings": [finding.code for finding in result.findings],
            "scope": "bounded_native_agent_operation", "game_outcome": "not_measured",
            "scientific_verdict": "not_claimed", "training_admission": "not_claimed",
            "human_origin": "not_claimed", "native_coverage": "not_qualified",
            "tainted": None, "pending_request": None, "agent_state": None,
            "submission_attempt_count": None, "request_outcome_counts": None,
        }
        if result.value is not None:
            raw = (directory / "events.jsonl").read_bytes()
            entry = next(item for item in result.value.evidence_manifest["files"]
                         if item["path"] == "events.jsonl")
            if hashlib.sha256(raw).hexdigest() != entry["sha256"] or len(raw) != entry["bytes"]:
                raise BoundaryError("local_model", "finalized_stop_evidence_changed")
            events = [json.loads(line) for line in raw.splitlines()]
            counts = Counter(event["kind"] for event in events)
            outcomes: dict[str, dict[str, Any]] = {}
            stop = None
            for event in events:
                payload = event["payload"]
                if event["kind"] == "native_result" or (
                    event["kind"] == "native_request_reconciled" and payload["result"] is not None
                ):
                    outcomes[payload["result"]["request_id"]] = payload["result"]
                if event["kind"] == "stopped":
                    stop = payload
            report.update(
                evidence_content_id=result.value.content_id,
                event_count=result.value.event_count,
                event_counts=dict(counts),
                tainted=result.value.manifest["tainted"],
                submission_attempt_count=counts.get("native_submission_requested", 0),
                request_outcome_counts=dict(
                    Counter(item["delivery"] for item in outcomes.values())
                ),
                pending_request=stop["pending_request"] if stop is not None else None,
                agent_state=stop["agent_state"] if stop is not None else None,
                budget_summary=_recorded_budget(stop["autonomy_budget"])
                if stop is not None
                else None,
                controller_at_stop=stop["controller"] if stop is not None else None,
            )
        identity = hashlib.sha256(canonical_json(report).encode()).hexdigest()
        report["evaluation_id"] = (
            identity  # Existing operational journal ID; schema remains native.
        )
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
