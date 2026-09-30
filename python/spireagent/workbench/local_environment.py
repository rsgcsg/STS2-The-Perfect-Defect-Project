"""One explicitly started Managed text-menu engineering session per Workbench.

The browser selects a fixed seed scenario, never a program or filesystem path.
The installed public Host client owns the native process and gameplay delivery.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import stat
import subprocess
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, digest, json_bytes
from spireagent.package_identity import PackageIdentityError, validate_installed_package
from spireagent.source import REPOSITORY
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ProjectConfig, atomic_json, tool_identity

SCHEMA = "stpd/local-managed-environment-v1"
PROFILE_SCHEMA = "stpd/local-managed-host-profile-v1"
REPORT_SCHEMA = "stpd/local-managed-environment-report-v1"
EVENT_SCHEMA = "stpd/local-managed-environment-event-v1"
SCENE_SCHEMA = "stpd/local-managed-fixed-seed-start-v1"
COMPARISON_SCHEMA = "stpd/local-managed-start-comparison-v1"
PROFILE_FILE = "managed-host-profile-v1.json"
JOURNAL_FILE = "managed-environment-session-v1.json"
REPORT_ROOT = "managed-environment-reports"
HOST_PACKAGE = "@rsgcsg/sts2-host-runtime"
TEXT_STATE_OWNER_CONTRACT = "sts2.host-runtime/text-menu-v2-owner-1"
TEXT_PROFILES = {
    "text-menu-v1": (
        "sts2.player-environment/text-menu-observation-context-1",
        "sts2.player-environment/text-menu-snapshot-1",
    ),
    "text-menu-v2": (
        "sts2.player-environment/text-menu-observation-context-2",
        "sts2.player-environment/text-menu-snapshot-2",
    ),
}
SCENARIO = {
    "id": "managed-defect-a0-map-prefix-20260929",
    "label": "故障机器人 A0 · 固定种子开局",
    "seed": "M2H0ST20260929A",
    "character": "Defect",
    "scope": "固定种子新局的人工文字单步；不是完整游戏或精确存档恢复。",
    "source_patch_sha256": "bf3ac3d1aadee5ce556687745d2d64d37d0eea47e2b67904ca7c97b080d8b89e",
    "artifact_sha256": "dd726fba38f4fc097a57e9dd4a5fe7d220bd94ea3527e132be31a31c95963d93",
    "artifact_mvid": "145c95e9-ace0-42b5-bb46-3fef292ac645",
    "exact_game_assembly_sha256": (
        "9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4"
    ),
}
AUDIT_FIELDS = (
    "upstream_revision", "source_patch_sha256", "artifact_sha256", "artifact_mvid",
    "original_sts2_sha256", "runtime_sts2_sha256",
)
ACTIVE = frozenset({"starting", "resuming", "active", "control_held",
                    "submitting", "stopping"})
UNRESOLVED = frozenset({"unknown", "interrupted_unknown", "cleanup_unknown"})
TERMINAL = frozenset({"stopped", "stopped_outcome_unknown", "failed"})


class HostClientPreparationError(RuntimeError):
    """The public Host client was not constructed and no process was spawned."""

    cleanup_confirmed = True

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ManagedServiceEnvironment:
    """Workbench operations over the pinned Host-owned attachment API."""

    def __init__(self, client: Any, manager: Any, ready: dict[str, Any],
                 input_profile: str,
                 before_claim: Callable[[str, dict[str, str]], None] | None = None,
                 *, session_id: str | None = None) -> None:
        self._client = client
        self._manager = manager
        self.ready = ready
        self.input_profile = input_profile
        self.text_state_owner: str | None = None
        if input_profile == "text-menu-v2":
            contracts = ready.get("text_menu_contracts")
            v2 = [item for item in contracts if isinstance(item, Mapping)
                  and item.get("input_profile") == "text-menu-v2"] \
                if isinstance(contracts, list) else []
            if len(v2) != 1 or v2[0].get("text_state_owner_contract") != (
                TEXT_STATE_OWNER_CONTRACT
            ):
                raise BoundaryError(
                    "local_environment", "managed_text_state_owner_unsupported"
                )
            if (not isinstance(session_id, str)
                    or re.fullmatch(r"[a-f0-9]{32}", session_id) is None):
                raise BoundaryError("local_environment", "managed_text_state_owner_invalid")
            self.text_state_owner = f"workbench:{session_id}"
        self.control: dict[str, str] | None = None
        self.last_submit_result: dict[str, Any] | None = None
        self.before_claim = before_claim

    def refresh(self) -> dict[str, Any]:
        self.ready = self._client.ready()
        return self.ready

    def binding(self) -> dict[str, str]:
        episode = self.ready.get("episode")
        runtime = self.ready.get("adapter_runtime_instance_id")
        continuity = episode.get("game_continuity_id") if isinstance(episode, Mapping) else None
        service = self.ready.get("service_instance_id")
        if (not isinstance(service, str) or not service
                or not isinstance(runtime, str) or not runtime
                or not isinstance(continuity, str) or not continuity):
            raise BoundaryError("local_environment", "managed_service_episode_unavailable")
        return {"service_instance_id": service, "runtime_instance_id": runtime,
                "game_continuity_id": continuity}

    def request(self, command: str, **values: Any) -> dict[str, Any]:
        try:
            value = self._client.request({"command": command, **values})
        except Exception as error:
            if getattr(error, "code", None) == "managed_control_held":
                raise BoundaryError("local_environment", "managed_control_held") from error
            raise
        result = value.get("result")
        if not isinstance(result, dict):
            raise BoundaryError("local_environment", "managed_service_result_invalid")
        return result

    def reset(self, seed: str) -> dict[str, Any]:
        runtime = self.ready.get("adapter_runtime_instance_id")
        episode = self.ready.get("episode")
        continuity = episode.get("game_continuity_id") if isinstance(episode, Mapping) else None
        if not isinstance(runtime, str) or not runtime:
            raise BoundaryError("local_environment", "managed_service_identity_invalid")
        value = self._manager.reset(seed, expected_runtime_instance_id=runtime,
                                    expected_game_continuity_id=continuity)
        result = value.get("result")
        if not isinstance(result, dict) or result.get("type") != "reset_result":
            raise BoundaryError("local_environment", "managed_service_reset_unconfirmed")
        self.refresh()
        return result

    def episode_identity(self) -> dict[str, Any]:
        result = self.request("episode_identity")
        identity = result.get("identity")
        if not isinstance(identity, dict):
            raise BoundaryError("local_environment", "episode_identity_invalid")
        return identity

    def _claim(self) -> dict[str, str]:
        binding = self.binding()
        request_id = uuid.uuid4().hex
        if self.before_claim is not None:
            self.before_claim(request_id, binding)
        try:
            result = self.request("claim_control", expected_runtime_instance_id=binding[
                "runtime_instance_id"], expected_game_continuity_id=binding[
                    "game_continuity_id"], request_id=request_id,
                **({"text_state_owner": self.text_state_owner}
                   if self.text_state_owner is not None else {}))
        except Exception as error:
            if isinstance(error, BoundaryError) and error.code == "managed_control_held":
                raise
            if getattr(error, "code", None) in {
                "managed_text_state_owner_required", "managed_text_state_owner_invalid"
            }:
                # The Host explicitly rejected this claim before accepting it;
                # this is a known no-effect response, not an uncertain lease.
                raise BoundaryError(
                    "local_environment", cast(str, error.code)
                ) from error
            # The offer may have been accepted without its response. There is no
            # credential with which to release safely, so quarantine the handle.
            self.control = {"uncertain": "claim", "request_id": request_id}
            raise BoundaryError("local_environment", "managed_control_claim_unknown") from error
        token = result.get("control_token")
        epoch = result.get("control_epoch")
        if (result.get("type") != "claim_control_result" or not isinstance(token, str)
                or not token or not isinstance(epoch, str) or not epoch
                or result.get("runtime_instance_id") != binding["runtime_instance_id"]
                or result.get("game_continuity_id") != binding["game_continuity_id"]
                or (self.text_state_owner is not None
                    and result.get("text_state_owner") != self.text_state_owner)):
            self.control = {"uncertain": "claim", "request_id": request_id}
            raise BoundaryError("local_environment", "managed_control_claim_unconfirmed")
        self.control = {"token": token, "epoch": epoch, "request_id": request_id}
        return self.control

    def _release(self) -> None:
        control = self.control
        if control is None:
            return
        if "uncertain" in control:
            raise BoundaryError("local_environment", "managed_control_release_unknown")
        binding = self.binding()
        try:
            result = self.request("release_control", control_token=control["token"],
                                  control_epoch=control["epoch"],
                                  expected_runtime_instance_id=binding["runtime_instance_id"],
                                  expected_game_continuity_id=binding["game_continuity_id"])
        except Exception as error:
            self.control = {"uncertain": "release", "epoch": control["epoch"],
                            "request_id": control["request_id"]}
            raise BoundaryError("local_environment", "managed_control_release_unknown") from error
        if (result.get("type") != "release_control_result" or result.get("status") != "released"
                or result.get("control_epoch") != control["epoch"]
                or result.get("runtime_instance_id") != binding["runtime_instance_id"]
                or result.get("game_continuity_id") != binding["game_continuity_id"]):
            self.control = {"uncertain": "release", "epoch": control["epoch"],
                            "request_id": control["request_id"]}
            raise BoundaryError("local_environment", "managed_control_release_unconfirmed")
        self.control = None
        self.refresh()

    def observe_text_menu(self, *, input_profile: str | None = None) -> dict[str, Any]:
        binding = self.binding()
        self._claim()
        try:
            control = self.control
            assert control is not None
            result = self.request("text_observe", input_profile=input_profile or self.input_profile,
                                  control_token=control["token"],
                                  control_epoch=control["epoch"])
            context = result.get("context")
            if not isinstance(context, dict) or context.get("game_continuity_id") != binding[
                "game_continuity_id"]:
                raise BoundaryError("local_environment", "managed_observation_unconfirmed")
            return context
        finally:
            self._release()

    def submit_text_menu(
        self, action_id: str, snapshot_id: str, continuity_id: str,
        mutation_request_id: str, *, input_profile: str | None = None,
    ) -> dict[str, Any]:
        binding = self.binding()
        if binding["game_continuity_id"] != continuity_id:
            raise BoundaryError("local_environment", "stale_text_context")
        self.last_submit_result = None
        self._claim()
        try:
            control = self.control
            assert control is not None
            result = self.request(
                "text_submit", action_id=action_id, expected_snapshot_id=snapshot_id,
                expected_game_continuity_id=continuity_id,
                mutation_request_id=mutation_request_id,
                input_profile=input_profile or self.input_profile,
                control_token=control["token"], control_epoch=control["epoch"],
            )
            if result.get("type") != "text_submit_result":
                raise BoundaryError("local_environment", "managed_submit_unconfirmed")
            submitted = result.get("result", result)
            if not isinstance(submitted, dict):
                raise BoundaryError("local_environment", "managed_submit_unconfirmed")
            self.last_submit_result = submitted
            return submitted
        finally:
            self._release()

    def close(self, force: bool = False) -> None:
        # Workbench close and session Stop detach; only an explicit manager action
        # may close the Host-owned service/native child.
        self._release()


def _ordinary(path: Path, *, directory: bool) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    return stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)


def _validate_private_pin(pin: dict[str, Any]) -> None:
    if set(pin) != {"schema", "package", "version", "source_revision",
                    "component_tree_revision", "release_asset_sha256",
                    "package_content_sha256"} or (
        pin.get("schema") != "stpd/platform-host-runtime-pin-v1"
    ) or (
        pin.get("package") != HOST_PACKAGE
    ) or re.fullmatch(r"1\.1\.0-rc\.(\d+)", str(pin.get("version"))) is None:
        raise BoundaryError("local_environment", "host_package_pin_invalid")
    for key, length in (("source_revision", 40), ("component_tree_revision", 40),
                        ("release_asset_sha256", 64), ("package_content_sha256", 64)):
        digest(pin[key], f"local_environment.{key}", length=length)
    release = int(str(pin["version"]).rsplit(".", 1)[1])
    if release < 20:
        raise BoundaryError("local_environment", "host_cleanup_capability_required")


def _verify_host(host_root: Path, pin: dict[str, Any]) -> None:
    _validate_private_pin(pin)
    try:
        validate_installed_package(
            host_root, pin,
            required_paths=("consumers/python/sts2_headless/__init__.py",
                            "consumers/python/sts2_headless/client.py",
                            "tools/managed-pe-driver.mjs", "tools/managed-exact.mjs"),
        )
    except (OSError, ValueError, PackageIdentityError) as error:
        raise BoundaryError("local_environment", "host_package_unavailable") from error


def _audit(host_root: Path, candidate: Path) -> dict[str, str]:
    completed = subprocess.run(
        ["node", str(host_root / "tools/managed-exact.mjs"), "audit", "--candidate",
         str(candidate)],
        cwd=host_root, text=True, capture_output=True, timeout=45, check=False,
    )
    if completed.returncode != 0 or len(completed.stdout) > 65536:
        raise BoundaryError("local_environment", "managed_candidate_audit_failed")
    try:
        value = decode_json(completed.stdout)
        if not isinstance(value, dict):
            raise ValueError
        result = {key: value[key] for key in AUDIT_FIELDS}
        if any(not isinstance(item, str) or not item for item in result.values()):
            raise ValueError
        if value.get("candidate_directory") != str(candidate):
            raise ValueError
        if result["source_patch_sha256"] != SCENARIO["source_patch_sha256"] or (
            result["artifact_sha256"] != SCENARIO["artifact_sha256"]
            or result["artifact_mvid"] != SCENARIO["artifact_mvid"]
            or result["original_sts2_sha256"] != SCENARIO["exact_game_assembly_sha256"]
            or result["runtime_sts2_sha256"] != SCENARIO["exact_game_assembly_sha256"]
        ):
            raise ValueError
        return result
    except (BoundaryError, KeyError, TypeError, ValueError) as error:
        raise BoundaryError("local_environment", "managed_candidate_identity_mismatch") from error


def configure_managed_host(
    config: ProjectConfig, candidate: Path, *, host_root: Path, host_pin: dict[str, Any],
    input_profile: str,
    audit: Callable[[Path, Path], dict[str, str]] = _audit,
) -> dict[str, Any]:
    """Trusted stopped-Workbench setup; the web API has no equivalent path input."""
    from spireagent.workbench.developer_server import instance_lock

    if not _ordinary(config.state_dir, directory=True):
        raise BoundaryError("local_environment", "state_directory_unavailable")
    with instance_lock(config.state_dir / "instance.lock"):
        return _configure_managed_host(
            config, candidate, host_root=host_root, host_pin=host_pin,
            input_profile=input_profile, audit=audit,
        )


def _configure_managed_host(
    config: ProjectConfig, candidate: Path, *, host_root: Path, host_pin: dict[str, Any],
    input_profile: str,
    audit: Callable[[Path, Path], dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(input_profile, str) or input_profile not in TEXT_PROFILES:
        raise BoundaryError("local_environment", "text_profile_unsupported")
    if not candidate.is_absolute() or not _ordinary(candidate, directory=True) or (
        candidate.resolve() != candidate
    ):
        raise BoundaryError("local_environment", "candidate_directory_unsafe")
    if not host_root.is_absolute() or not _ordinary(host_root, directory=True) or (
        host_root.resolve() != host_root
    ):
        raise BoundaryError("local_environment", "host_package_path_unsafe")
    _verify_host(host_root, host_pin)
    observed = audit(host_root, candidate)
    if (observed.get("source_patch_sha256") != SCENARIO["source_patch_sha256"]
            or observed.get("artifact_sha256") != SCENARIO["artifact_sha256"]
            or observed.get("artifact_mvid") != SCENARIO["artifact_mvid"]
            or observed.get("original_sts2_sha256") != SCENARIO["exact_game_assembly_sha256"]
            or observed.get("runtime_sts2_sha256") != SCENARIO["exact_game_assembly_sha256"]):
        raise BoundaryError("local_environment", "managed_candidate_identity_mismatch")
    profile = {
        "schema": PROFILE_SCHEMA,
        "candidate_directory": str(candidate),
        "host_package_directory": str(host_root),
        "host_package_pin": host_pin,
        "input_profile": input_profile,
        "audit": observed,
    }
    target = config.state_dir / PROFILE_FILE
    if target.is_symlink():
        raise BoundaryError("local_environment", "profile_path_unsafe")
    if target.exists():
        old = _read_profile(config)
        if old != profile:
            raise BoundaryError("local_environment", "profile_collision")
    else:
        atomic_json(target, profile)
    return {
        "schema": PROFILE_SCHEMA, "status": "configured",
        "scenario_id": SCENARIO["id"],
        "input_profile": input_profile,
        "host_package_content_sha256": host_pin["package_content_sha256"],
    }


def _read_profile(config: ProjectConfig) -> dict[str, Any]:
    target = config.state_dir / PROFILE_FILE
    if (not _ordinary(config.state_dir, directory=True)
            or config.state_dir.resolve() != config.state_dir
            or not _ordinary(target, directory=False)):
        raise BoundaryError("local_environment", "profile_required")
    try:
        value = decode_json(target.read_bytes())
        if not isinstance(value, dict) or set(value) != {
            "schema", "candidate_directory", "host_package_directory", "host_package_pin",
            "input_profile", "audit"
        } or value["schema"] != PROFILE_SCHEMA:
            raise ValueError
        if value["input_profile"] not in TEXT_PROFILES:
            raise ValueError
        candidate = Path(value["candidate_directory"])
        host_root = Path(value["host_package_directory"])
        if not candidate.is_absolute() or candidate.resolve() != candidate or (
            not _ordinary(candidate, directory=True)
        ):
            raise ValueError
        if not host_root.is_absolute() or host_root.resolve() != host_root or (
            not _ordinary(host_root, directory=True)
        ):
            raise ValueError
        if not isinstance(value["audit"], dict) or set(value["audit"]) != set(AUDIT_FIELDS):
            raise ValueError
        if (value["audit"]["source_patch_sha256"] != SCENARIO["source_patch_sha256"]
                or value["audit"]["artifact_sha256"] != SCENARIO["artifact_sha256"]
                or value["audit"]["artifact_mvid"] != SCENARIO["artifact_mvid"]
                or value["audit"]["original_sts2_sha256"] != (
                    SCENARIO["exact_game_assembly_sha256"]
                ) or value["audit"]["runtime_sts2_sha256"] != (
                    SCENARIO["exact_game_assembly_sha256"]
                )):
            raise ValueError
        pin = value["host_package_pin"]
        if not isinstance(pin, dict) or pin.get("schema") != (
            "stpd/platform-host-runtime-pin-v1"
        ) or pin.get("package") != HOST_PACKAGE or not re.fullmatch(
            r"[0-9a-f]{64}", str(pin.get("package_content_sha256"))
        ):
            raise ValueError
        _validate_private_pin(pin)
        return value
    except (OSError, TypeError, KeyError, ValueError, BoundaryError) as error:
        raise BoundaryError("local_environment", "profile_invalid") from error


def _public_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    build = identity.get("candidate_build")
    provenance = identity.get("episode_provenance")
    if not isinstance(build, Mapping) or not isinstance(provenance, Mapping):
        raise BoundaryError("local_environment", "episode_identity_invalid")
    clean_build = {field: build.get(field) for field in AUDIT_FIELDS}
    return {
        "candidate_build": clean_build,
        "environment_fingerprint": identity.get("environment_fingerprint"),
        "episode_provenance": {
            key: provenance.get(key) for key in
            ("verdict", "requested_seed", "actual_seed", "runtime_instance_id")
        },
    }


class LocalEnvironmentService:
    def __init__(
        self, config: ProjectConfig, *,
        audit: Callable[[Path, Path], dict[str, str]] = _audit,
        client_factory: Callable[[list[str], Path, dict[str, Any]], Any] | None = None,
        seed_normalizer: Callable[[Path, dict[str, Any], object], str] | None = None,
    ) -> None:
        self.config = config
        self.audit = audit
        self.client_factory = client_factory or self._public_client
        self.seed_normalizer = seed_normalizer or self._host_seed
        self.lock = threading.RLock()
        self.lifecycle_lock = threading.RLock()
        self.client: Any | None = None
        self._unclosed_start_client: Any | None = None
        self.worker: threading.Thread | None = None
        self.stopping = False
        self.cleanup_confirmed = False
        self.stop_outcome_unknown = False
        self.record = self._load_journal()

    def _load_journal(self) -> dict[str, Any]:
        path = self.config.state_dir / JOURNAL_FILE
        if not path.exists() and not path.is_symlink():
            return {"status": "idle"}
        if not _ordinary(path, directory=False):
            return {"status": "interrupted_unknown", "error_code": "journal_invalid"}
        try:
            value = decode_json(path.read_bytes())
            if not isinstance(value, dict) or value.get("schema") != SCHEMA:
                raise ValueError
            if value.get("status") == "idle" and set(value) == {"schema", "status"}:
                return {"status": "idle"}
            if value.get("status") not in ACTIVE | UNRESOLVED | TERMINAL or (
                not isinstance(value.get("session_id"), str)
            ):
                raise ValueError
            if value["status"] in ACTIVE:
                return {**value, "status": "interrupted_unknown",
                        "error_code": "previous_session_outcome_unknown"}
            return value
        except (OSError, ValueError, BoundaryError):
            return {"status": "interrupted_unknown", "error_code": "journal_invalid"}

    def _save(self) -> None:
        atomic_json(self.config.state_dir / JOURNAL_FILE, self.record)

    def _try_save(self) -> bool:
        """Cleanup paths must continue even when the journal cannot be written."""
        try:
            self._save()
            return True
        except Exception:
            return False

    def _persist_claim_offer(self, session_id: str, request_id: str,
                             binding: dict[str, str]) -> None:
        """Persist the owner correlation before the Host can accept a claim."""
        with self.lock:
            if self.record.get("session_id") != session_id:
                raise BoundaryError("local_environment", "session_ownership_changed")
            self.record["service_binding"] = dict(binding)
            self.record["session_semantics"] = "workbench-segment-v2-host-owned-service"
            self.record["pending_control_claim_request_id"] = request_id
            if not self._try_save():
                raise BoundaryError("local_environment", "session_persistence_failed_before_offer")

    def _public_client(self, command: list[str], host_root: Path,
                       pin: dict[str, Any]) -> Any:
        from spireagent.host_runtime_client import activate_host_runtime_client

        try:
            activate_host_runtime_client(host_root, pin)
            if (not _ordinary(host_root / "consumers/python/sts2_headless/managed_service.py",
                              directory=False)
                    or not _ordinary(host_root / "tools/managed-host-service.mjs",
                                     directory=False)):
                raise HostClientPreparationError("host_managed_service_unavailable")
            api = importlib.import_module("sts2_headless")
            launch_service = api.launch_managed_host_service
            client_type = api.ManagedHostServiceClient
            manager_type = api.ManagedHostServiceManager
        except Exception as error:
            if isinstance(error, (BoundaryError, HostClientPreparationError)):
                raise
            raise HostClientPreparationError("host_public_client_unavailable") from error

        input_profile = self._profile_for_service(host_root, pin)
        try:
            candidate_index = command.index("--candidate") + 1
            candidate = Path(command[candidate_index])
        except (ValueError, IndexError) as error:
            raise BoundaryError(
                "local_environment", "managed_candidate_argument_invalid"
            ) from error
        directory = self.config.state_dir / "managed-host-service"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not _ordinary(directory, directory=True):
            raise BoundaryError("local_environment", "managed_attachment_directory_unsafe")
        if os.name != "nt" and directory.stat().st_mode & 0o077:
            raise BoundaryError("local_environment", "managed_attachment_directory_unsafe")
        client_file = directory / "client.json"
        manager_file = directory / "manager.json"
        log_file = directory / f"launch-{uuid.uuid4().hex}.log"
        existing = client_file.exists() or manager_file.exists()
        if existing:
            if (client_file.is_symlink() or manager_file.is_symlink()
                    or not client_file.is_file() or not manager_file.is_file()):
                raise BoundaryError("local_environment", "managed_attachment_incomplete")
            client = client_type.from_attachment(client_file)
            manager = manager_type.from_attachment(manager_file)
            if client.service_instance_id != manager.service_instance_id:
                raise BoundaryError("local_environment", "managed_attachment_identity_mismatch")
            ready = client.ready()
        else:
            # Launch exceptions remain uncertain: the owner may have spawned
            # the detached process before failing to return its attachment.
            launch_service(
                host_package_dir=host_root, candidate_dir=candidate,
                client_attachment=client_file, manager_attachment=manager_file,
                log_file=log_file, character=SCENARIO["character"],
            )
            client = client_type.from_attachment(client_file)
            manager = manager_type.from_attachment(manager_file)
            ready = client.ready()
        pin_host = ready.get("host_identity")
        if not isinstance(pin_host, Mapping) or (
            pin_host.get("version") != pin["version"]
            or pin_host.get("package_name") != HOST_PACKAGE
        ):
            raise BoundaryError("local_environment", "managed_host_identity_mismatch")
        with self.lock:
            session_id = self.record.get("session_id")
        callback = (lambda request_id, binding: self._persist_claim_offer(
            session_id, request_id, binding
        )) if isinstance(session_id, str) else None
        return _ManagedServiceEnvironment(client, manager, ready, input_profile, callback,
                                          session_id=session_id)

    def _profile_for_service(self, host_root: Path, pin: dict[str, Any]) -> str:
        profile = _read_profile(self.config)
        if profile["host_package_directory"] != str(host_root) or profile[
            "host_package_pin"
        ] != pin:
            raise BoundaryError("local_environment", "managed_profile_changed")
        return cast(str, profile["input_profile"])

    def _managed_service_handles(self, profile: dict[str, Any]) -> tuple[Any, Any, dict[str, Any]]:
        from spireagent.host_runtime_client import activate_host_runtime_client

        host_root = Path(profile["host_package_directory"])
        pin = profile["host_package_pin"]
        _verify_host(host_root, pin)
        directory = self.config.state_dir / "managed-host-service"
        client_file, manager_file = directory / "client.json", directory / "manager.json"
        if (directory.is_symlink() or not _ordinary(directory, directory=True)
                or client_file.is_symlink() or manager_file.is_symlink()
                or not client_file.is_file() or not manager_file.is_file()):
            raise BoundaryError("local_environment", "managed_service_not_attached")
        if os.name != "nt" and directory.stat().st_mode & 0o077:
            raise BoundaryError("local_environment", "managed_attachment_directory_unsafe")
        activate_host_runtime_client(host_root, pin)
        api = importlib.import_module("sts2_headless")
        client = api.ManagedHostServiceClient.from_attachment(client_file)
        manager = api.ManagedHostServiceManager.from_attachment(manager_file)
        ready = client.ready()
        if client.service_instance_id != manager.service_instance_id:
            raise BoundaryError("local_environment", "managed_attachment_identity_mismatch")
        identity = ready.get("host_identity")
        if not isinstance(identity, Mapping) or identity.get("package_name") != HOST_PACKAGE or (
            identity.get("version") != pin["version"]
        ):
            raise BoundaryError("local_environment", "managed_host_identity_mismatch")
        candidate = ready.get("candidate_build")
        exact_game = ready.get("exact_game")
        if not isinstance(candidate, Mapping) or any(
            candidate.get(key) != profile["audit"][key] for key in AUDIT_FIELDS
        ) or not isinstance(exact_game, Mapping) or exact_game.get(
            "sts2_dll_sha256"
        ) != SCENARIO["exact_game_assembly_sha256"]:
            raise BoundaryError("local_environment", "managed_candidate_identity_mismatch")
        return client, manager, ready

    def managed_runtime_target(self) -> dict[str, Any]:
        """Private backend contract for a Runtime attach; never exposed by HTTP."""
        profile = _read_profile(self.config)
        client, manager, ready = self._managed_service_handles(profile)
        manager_status = manager.status().get("status")
        episode = ready.get("episode")
        if not isinstance(episode, Mapping) or not isinstance(manager_status, Mapping):
            raise BoundaryError("local_environment", "managed_service_status_invalid")
        if (episode.get("closed") is not False or episode.get("tainted") is not False
                or episode.get("control_held") is not False):
            raise BoundaryError("local_environment", "managed_episode_unavailable")
        status_episode = manager_status.get("control")
        if manager_status.get("control_held") is not False or status_episode is not None:
            raise BoundaryError("local_environment", "managed_control_held")
        runtime = ready.get("adapter_runtime_instance_id")
        continuity = episode.get("game_continuity_id")
        if (not isinstance(runtime, str) or not runtime
                or not isinstance(continuity, str) or not continuity):
            raise BoundaryError("local_environment", "managed_service_episode_unavailable")
        profile_bytes = (self.config.state_dir / PROFILE_FILE).read_bytes()
        return {
            "schema": "stpd/workbench-managed-runtime-target-v1",
            "client_attachment": self.config.state_dir / "managed-host-service" / "client.json",
            "host_package_pin": dict(profile["host_package_pin"]),
            "candidate_build": dict(profile["audit"]),
            "profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
            "service_instance_id": client.service_instance_id,
            "runtime_instance_id": runtime,
            "game_continuity_id": continuity,
            "environment_fingerprint": ready.get("environment_fingerprint"),
            "exact_game": ready.get("exact_game"),
            "text_menu_contracts": ready.get("text_menu_contracts"),
            "input_profile": profile["input_profile"],
        }

    def close_environment(self, *, expected_service_instance_id: str | None = None,
                          expected_runtime_instance_id: str | None = None,
                          expected_game_continuity_id: str | None = None,
                          expected_session_id: str | None = None) -> dict[str, Any]:
        """Explicitly close the Host child; ordinary Stop/Workbench exit never calls this."""
        with self.lifecycle_lock:
            return self._close_environment_locked(
                expected_service_instance_id=expected_service_instance_id,
                expected_runtime_instance_id=expected_runtime_instance_id,
                expected_game_continuity_id=expected_game_continuity_id,
                expected_session_id=expected_session_id,
            )

    def _close_environment_locked(self, *, expected_service_instance_id: str | None,
                                  expected_runtime_instance_id: str | None,
                                  expected_game_continuity_id: str | None,
                                  expected_session_id: str | None) -> dict[str, Any]:
        with self.lock:
            if self.record.get("status") in ACTIVE or (
                self.worker is not None and self.worker.is_alive()
            ):
                raise BoundaryError("local_environment", "session_in_progress_or_unknown")
            current_session_id = self.record.get("session_id")
            if expected_session_id is not None and current_session_id != expected_session_id:
                raise BoundaryError("local_environment", "managed_service_binding_mismatch")
            if current_session_id is not None and expected_session_id != current_session_id:
                raise BoundaryError("local_environment", "managed_service_binding_mismatch")
        profile = _read_profile(self.config)
        _client, manager, ready = self._managed_service_handles(profile)
        episode = ready.get("episode")
        if isinstance(episode, Mapping) and episode.get("control_held") is not False:
            raise BoundaryError("local_environment", "managed_control_held")
        expected = self.record.get("service_binding")
        if isinstance(expected, Mapping) and expected.get("service_instance_id") != ready.get(
            "service_instance_id"
        ):
            raise BoundaryError("local_environment", "managed_service_binding_mismatch")
        if expected_service_instance_id is not None and expected_service_instance_id != ready.get(
            "service_instance_id"
        ):
            raise BoundaryError("local_environment", "managed_service_binding_mismatch")
        if expected_runtime_instance_id is not None and expected_runtime_instance_id != ready.get(
            "adapter_runtime_instance_id"
        ):
            raise BoundaryError("local_environment", "managed_service_binding_mismatch")
        if expected_game_continuity_id is not None and expected_game_continuity_id != (
            episode.get("game_continuity_id") if isinstance(episode, Mapping) else None
        ):
            raise BoundaryError("local_environment", "managed_service_binding_mismatch")
        directory = self.config.state_dir / "managed-host-service"
        for filename in ("client.json", "manager.json"):
            path = directory / filename
            if path.is_symlink() or not _ordinary(path, directory=False):
                raise BoundaryError("local_environment", "managed_attachment_invalid")
        response = manager.close_host()
        result = response.get("result") if isinstance(response, Mapping) else None
        if not isinstance(result, Mapping) or result.get("type") != "close_result":
            raise BoundaryError("local_environment", "managed_host_close_unconfirmed")
        for filename in ("client.json", "manager.json"):
            (directory / filename).unlink()
        with self.lock:
            self.client = None
            self.record["host_service_closed"] = True
            self._save()
        return {"status": "closed", "service_instance_id": ready["service_instance_id"]}

    def recover_control(self, *, expected_session_id: str | None = None,
                        expected_service_instance_id: str | None = None,
                        expected_runtime_instance_id: str | None = None,
                        expected_game_continuity_id: str | None = None) -> dict[str, Any]:
        """Explicitly release only a Workbench action whose control reply was lost."""
        with self.lifecycle_lock:
            return self._recover_control_locked(
                expected_session_id=expected_session_id,
                expected_service_instance_id=expected_service_instance_id,
                expected_runtime_instance_id=expected_runtime_instance_id,
                expected_game_continuity_id=expected_game_continuity_id,
            )

    def _recover_control_locked(self, *, expected_session_id: str | None,
                                expected_service_instance_id: str | None,
                                expected_runtime_instance_id: str | None,
                                expected_game_continuity_id: str | None) -> dict[str, Any]:
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise BoundaryError("local_environment", "session_in_progress_or_unknown")
            if expected_session_id is not None and self.record.get("session_id") != (
                expected_session_id
            ):
                raise BoundaryError("local_environment", "managed_service_binding_mismatch")
            uncertain_claim = self.record.get("error_code") == "managed_control_claim_unknown"
            uncertain_release = self.record.get("error_code") == "managed_control_release_unknown"
            if not uncertain_claim and not uncertain_release:
                raise BoundaryError("local_environment", "managed_control_recovery_not_applicable")
            expected = self.record.get("service_binding")
            if not isinstance(expected, Mapping):
                raise BoundaryError("local_environment", "managed_service_binding_unavailable")
            expected = dict(expected)
            expected_epoch = self.record.get("uncertain_control_epoch")
            expected_claim_id = self.record.get("uncertain_claim_request_id")
            if uncertain_release and (not isinstance(expected_epoch, str) or not expected_epoch):
                raise BoundaryError("local_environment", "managed_control_owner_unverifiable")
            if uncertain_claim and (not isinstance(expected_claim_id, str)
                                    or not expected_claim_id):
                raise BoundaryError("local_environment", "managed_control_owner_unverifiable")
            original_error = self.record["error_code"]

        profile = _read_profile(self.config)
        client, manager, ready = self._managed_service_handles(profile)
        if (client.service_instance_id != expected.get("service_instance_id")
                or ready.get("adapter_runtime_instance_id") != expected.get("runtime_instance_id")):
            raise BoundaryError("local_environment", "managed_service_binding_mismatch")
        if (expected_service_instance_id is not None
                and expected_service_instance_id != expected.get("service_instance_id")) or (
            expected_runtime_instance_id is not None
            and expected_runtime_instance_id != expected.get("runtime_instance_id")
        ) or (expected_game_continuity_id is not None
              and expected_game_continuity_id != expected.get("game_continuity_id")):
            raise BoundaryError("local_environment", "managed_service_binding_mismatch")
        episode = ready.get("episode")
        if not isinstance(episode, Mapping) or episode.get("game_continuity_id") != expected.get(
            "game_continuity_id"
        ) or episode.get("tainted") is True or episode.get("closed") is True:
            raise BoundaryError("local_environment", "managed_episode_unavailable")
        status_value = manager.status().get("status")
        if not isinstance(status_value, Mapping) or status_value.get("control_held") is not True:
            raise BoundaryError("local_environment", "managed_control_not_held")
        control = status_value.get("control")
        if not isinstance(control, Mapping) or any(
            not isinstance(control.get(key), str) or not control.get(key)
            for key in ("control_epoch", "runtime_instance_id", "game_continuity_id")
        ) or control.get("runtime_instance_id") != expected["runtime_instance_id"] or (
            control.get("game_continuity_id") != expected["game_continuity_id"]
        ) or (expected_epoch is not None and control.get("control_epoch") != expected_epoch) or (
            expected_claim_id is not None and control.get("claim_request_id") != expected_claim_id
        ):
            raise BoundaryError("local_environment", "managed_control_identity_mismatch")

        response = manager.recover_control(
            expected_control_epoch=control["control_epoch"],
            expected_runtime_instance_id=expected["runtime_instance_id"],
            expected_game_continuity_id=expected["game_continuity_id"],
        )
        result = response.get("result") if isinstance(response, Mapping) else None
        if not isinstance(result, Mapping) or result.get("type") != "release_control_result" or (
            result.get("status") != "released"
        ) or result.get("control_epoch") != control["control_epoch"] or (
            result.get("runtime_instance_id") != expected["runtime_instance_id"]
        ) or result.get("game_continuity_id") != expected["game_continuity_id"]:
            raise BoundaryError("local_environment", "managed_control_recovery_unconfirmed")

        after_ready = client.ready()
        after_status = manager.status().get("status")
        after_episode = after_ready.get("episode")
        if (not isinstance(after_episode, Mapping) or after_episode.get("control_held") is not False
                or after_episode.get("game_continuity_id") != expected["game_continuity_id"]
                or not isinstance(after_status, Mapping)
                or after_status.get("control_held") is not False
                or after_status.get("control") is not None):
            raise BoundaryError("local_environment", "managed_control_recovery_unconfirmed")
        with self.lock:
            if self.record.get("service_binding") != expected or self.record.get(
                "error_code"
            ) != original_error:
                raise BoundaryError("local_environment", "managed_service_binding_mismatch")
            # Control is released, but the earlier operation remains outcome-unknown.
            self.record["control_recovery"] = "released_acknowledged_outcome_still_unknown"
            if expected_claim_id is not None:
                self.record["uncertain_claim_request_id"] = expected_claim_id
            if expected_epoch is not None:
                self.record["uncertain_control_epoch"] = expected_epoch
            self._save()
        return {"status": "released", "outcome": "still_unknown",
                "service_instance_id": expected["service_instance_id"]}

    @staticmethod
    def _host_seed(host_root: Path, pin: dict[str, Any], seed: object) -> str:
        """Validate with the exact pinned Host client's canonical seed contract."""
        _verify_host(host_root, pin)
        from spireagent.host_runtime_client import activate_host_runtime_client

        try:
            activate_host_runtime_client(host_root, pin)
            canonicalize = importlib.import_module(
                "sts2_headless.client"
            ).canonicalize_episode_seed
        except Exception as error:
            raise BoundaryError("local_environment", "host_seed_contract_unavailable") from error
        try:
            canonical_seed = canonicalize(seed)
        except (TypeError, ValueError) as error:
            raise BoundaryError("local_environment", "scene_seed_invalid") from error
        if not isinstance(canonical_seed, str):
            raise BoundaryError("local_environment", "host_seed_contract_invalid")
        return canonical_seed

    def _report_store(self, *, create: bool) -> ManifestArtifactStore | None:
        root = self.config.state_dir / REPORT_ROOT
        if root.is_symlink() or (root.exists() and not _ordinary(root, directory=True)):
            raise BoundaryError("local_environment", "report_store_unsafe")
        if not create and not root.is_dir():
            return None
        return ManifestArtifactStore(LocalBlobStore(root, create=create, readonly=not create))

    def _artifact(self, artifact_id: object, schema: str, role: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or re.fullmatch(r"[0-9a-f]{64}", artifact_id) is None:
            raise BoundaryError("local_environment", "artifact_not_found")
        store = self._report_store(create=False)
        if store is None:
            raise BoundaryError("local_environment", "artifact_not_found")
        try:
            manifest = store.get_manifest(artifact_id)
            if manifest.kind != "analysis" or manifest.parameters.value().get("schema") != schema:
                raise ValueError
            value = decode_json(store.bytes(manifest.payload(role), maximum=8 * 1024 * 1024))
            if not isinstance(value, dict) or value.get("schema") != schema:
                raise ValueError
            return value
        except (OSError, KeyError, ValueError, BoundaryError) as error:
            raise BoundaryError("local_environment", "artifact_not_found") from error

    def scenes(self) -> dict[str, Any]:
        store = self._report_store(create=False)
        if store is None:
            return {"schema": SCENE_SCHEMA, "items": []}
        items = []
        for artifact_id in store.manifest_ids():
            manifest = store.get_manifest(artifact_id)
            if manifest.parameters.value().get("schema") == SCENE_SCHEMA:
                scene = self._artifact(artifact_id, SCENE_SCHEMA, "scene")
                items.append({"artifact_id": artifact_id, "name": scene["name"],
                              "seed": scene["seed"], "input_profile": scene["input_profile"]})
        return {"schema": SCENE_SCHEMA, "items": items[-20:]}

    def scene(self, artifact_id: object) -> dict[str, Any]:
        return self._artifact(artifact_id, SCENE_SCHEMA, "scene")

    def comparisons(self) -> dict[str, Any]:
        store = self._report_store(create=False)
        if store is None:
            return {"schema": COMPARISON_SCHEMA, "items": []}
        items = []
        for artifact_id in store.manifest_ids():
            manifest = store.get_manifest(artifact_id)
            if manifest.parameters.value().get("schema") == COMPARISON_SCHEMA:
                value = self._artifact(artifact_id, COMPARISON_SCHEMA, "comparison")
                items.append({"artifact_id": artifact_id,
                              "scene_artifact_id": value["scene_artifact_id"],
                              "report_artifact_ids": value["report_artifact_ids"]})
        return {"schema": COMPARISON_SCHEMA, "items": items[-20:]}

    def comparison(self, artifact_id: object) -> dict[str, Any]:
        return self._artifact(artifact_id, COMPARISON_SCHEMA, "comparison")

    def save_scene(self, name: object, seed: object = SCENARIO["seed"]) -> dict[str, Any]:
        if not isinstance(name, str) or not (1 <= len(name.strip()) <= 80) or (
            name != name.strip() or any(ord(char) < 32 for char in name)
        ):
            raise BoundaryError("local_environment", "scene_name_invalid")
        with self.lock:
            profile = _read_profile(self.config)
            selected_seed = self.seed_normalizer(
                Path(profile["host_package_directory"]), profile["host_package_pin"],
                seed,
            )
            # The artifact is a fixed-seed start recipe, never a prior process snapshot.
            scene = {"schema": SCENE_SCHEMA, "name": name, "scenario_id": SCENARIO["id"],
                     "seed": selected_seed, "character": SCENARIO["character"],
                     "input_profile": profile["input_profile"],
                     "host_package_pin": profile["host_package_pin"],
                     "candidate_build": profile["audit"],
                     "restoration": "fresh_managed_fixed_seed_new_run"}
            producer_info = self._producer()
            producer = Producer(REPOSITORY, producer_info["source_revision"],
                                producer_info["uv_lock_sha256"])
            store = self._report_store(create=True)
            assert store is not None
            payload = store.put_bytes("scene", json_bytes(scene))
            manifest = Manifest("analysis", producer, payloads=(payload,),
                                parameters=FrozenObject.of({"schema": SCENE_SCHEMA,
                                                            "name": name}))
            return {"artifact_id": store.publish(manifest), **scene}

    def _validate_scene_start(self, scene_id: object, profile: dict[str, Any]) -> None:
        scene = self.scene(scene_id)
        canonical_seed = self.seed_normalizer(
            Path(profile["host_package_directory"]), profile["host_package_pin"], scene.get("seed")
        )
        if (scene.get("scenario_id") != SCENARIO["id"]
                or not isinstance(scene.get("seed"), str) or canonical_seed != scene["seed"]
                or scene.get("character") != SCENARIO["character"]
                or scene.get("restoration") != "fresh_managed_fixed_seed_new_run"
                or scene.get("input_profile") != profile["input_profile"]
                or scene.get("host_package_pin") != profile["host_package_pin"]
                or scene.get("candidate_build") != profile["audit"]):
            raise BoundaryError("local_environment", "scene_profile_mismatch")

    @staticmethod
    def _start_proof(
        report: dict[str, Any], scene_id: str, scene: dict[str, Any]
    ) -> dict[str, Any]:
        identity = report.get("episode_identity")
        provenance = identity.get("episode_provenance") if isinstance(identity, dict) else None
        context = report.get("initial_context")
        snapshot = context.get("snapshot") if isinstance(context, dict) else None
        persistent = snapshot.get("persistent") if isinstance(snapshot, dict) else None
        persistent_content = (persistent.get("content")
                              if isinstance(persistent, dict) else None)
        run = persistent_content.get("run") if isinstance(persistent_content, dict) else None
        player = persistent_content.get("player") if isinstance(persistent_content, dict) else None
        menu = snapshot.get("menu_actions") if isinstance(snapshot, dict) else None
        service_binding = report.get("service_binding")
        legacy_closed_instance = service_binding is None and report.get("session_semantics") is None
        if (report.get("scene_artifact_id") != scene_id
                or report.get("status") != "stopped" or report.get("error_code") is not None
                or report.get("continued_from_session_id") is not None
                or report.get("input_profile") != scene["input_profile"]
                or report.get("host_package_pin") != scene["host_package_pin"]
                or not isinstance(identity, dict)
                or identity.get("candidate_build") != scene["candidate_build"]
                or not isinstance(provenance, dict)
                or provenance.get("verdict") != "provenance_pass"
                or report.get("seed") != scene["seed"]
                or provenance.get("requested_seed") != scene["seed"]
                or provenance.get("actual_seed") != scene["seed"]
                or not isinstance(run, dict) or type(run.get("ascension")) is not int
                or run.get("ascension") != 0
                or not isinstance(player, dict)
                or player.get("character_definition_id") != "DEFECT"
                or not isinstance(provenance.get("runtime_instance_id"), str)
                or not provenance["runtime_instance_id"]
                or (not legacy_closed_instance and (
                    not isinstance(service_binding, dict)
                    or not isinstance(service_binding.get("service_instance_id"), str)
                    or not isinstance(service_binding.get("game_continuity_id"), str)
                    or report.get("session_semantics") != (
                        "workbench-segment-v2-host-owned-service"
                    )
                ))
                or not isinstance(menu, dict) or menu.get("status") != "complete"
                or not isinstance(menu.get("actions"), list)
                or menu.get("materialized_count") != len(menu["actions"])
                or menu.get("total_count") != len(menu["actions"])):
            raise BoundaryError("local_environment", "repeatability_proof_unavailable")
        events = report.get("events")
        if not isinstance(events, list) or any(not isinstance(event, dict) or
            event.get("error_code") is not None for event in events):
            raise BoundaryError("local_environment", "repeatability_proof_unavailable")
        return {"session_id": report["session_id"],
                "runtime_instance_id": provenance["runtime_instance_id"],
                "service_instance_id": (service_binding.get("service_instance_id")
                                         if isinstance(service_binding, dict) else None),
                "game_continuity_id": (service_binding.get("game_continuity_id")
                                       if isinstance(service_binding, dict) else None),
                "session_semantics": report.get("session_semantics"),
                "actual_seed": provenance["actual_seed"],
                "input_profile": report["input_profile"],
                "candidate_build": identity["candidate_build"],
                "initial_menu_count": menu["total_count"],
                "initial_menu_complete": True, "action_count": len(events),
                "terminal_status": report["status"], "error_code": report.get("error_code")}

    def compare(self, scene_id: object, report_ids: object) -> dict[str, Any]:
        if (not isinstance(scene_id, str) or not isinstance(report_ids, list)
                or len(report_ids) != 2 or report_ids[0] == report_ids[1]):
            raise BoundaryError("local_environment", "comparison_inputs_invalid")
        with self.lock:
            scene = self.scene(scene_id)
            reports = [self.report(report_id) for report_id in report_ids]
            runs = [self._start_proof(report, scene_id, scene) for report in reports]
            if runs[0]["session_id"] == runs[1]["session_id"]:
                raise BoundaryError("local_environment", "same_episode")
            modern = [run["service_instance_id"] is not None for run in runs]
            if modern[0] != modern[1]:
                raise BoundaryError("local_environment", "comparison_semantics_mismatch")
            if modern[0]:
                if runs[0]["game_continuity_id"] == runs[1]["game_continuity_id"]:
                    raise BoundaryError("local_environment", "same_game_continuity")
                comparison_scope = (
                    "two distinct fixed-seed Host episodes; service/process may be shared; "
                    "only Workbench client actions are recorded; "
                    "no trajectory or policy equivalence"
                )
            else:
                if runs[0]["runtime_instance_id"] == runs[1]["runtime_instance_id"]:
                    raise BoundaryError("local_environment", "same_runtime_instance")
                comparison_scope = (
                    "legacy v1 proof of two independent closed fixed-seed starts; "
                    "no trajectory or policy equivalence"
                )
            result = {"schema": COMPARISON_SCHEMA, "scene_artifact_id": scene_id,
                      "report_artifact_ids": report_ids, "status": "verified_fixed_seed_starts",
                      "runs": runs,
                      "scope": comparison_scope,
                      "comparison_semantics": (
                          "host_episode_v2" if modern[0] else "closed_instance_v1"
                      )}
            source = self._producer()
            producer = Producer(REPOSITORY, source["source_revision"], source["uv_lock_sha256"])
            store = self._report_store(create=True)
            assert store is not None
            payload = store.put_bytes("comparison", json_bytes(result))
            manifest = Manifest("analysis", producer,
                                parents=(Parent("scene", scene_id),
                                         Parent("report-0", report_ids[0]),
                                         Parent("report-1", report_ids[1])),
                                payloads=(payload,),
                                parameters=FrozenObject.of({"schema": COMPARISON_SCHEMA}))
            return {"artifact_id": store.publish(manifest), **result}

    def _publish(self) -> None:
        if self.record["status"] not in TERMINAL:
            raise BoundaryError("local_environment", "report_not_terminal")
        if self.record.get("report_artifact_id"):
            return
        store = self._report_store(create=True)
        assert store is not None
        source = self.record["producer"]
        producer = Producer(REPOSITORY, source["source_revision"], source["uv_lock_sha256"])
        public = {key: value for key, value in self.record.items()
                  if key not in {"producer", "pending_request_id", "stop_requested",
                                 "pending_control_claim_request_id",
                                 "uncertain_claim_request_id", "uncertain_control_epoch"}}
        if self.record.get("service_binding") is not None:
            public.update(
                session_semantics="workbench-segment-v2-host-owned-service",
                host_service_status="not_closed_by_workbench",
                evidence_scope="workbench-client-actions-only",
            )
        report_bytes = json_bytes(public)
        if len(report_bytes) > 8 * 1024 * 1024:
            raise BoundaryError("local_environment", "report_index_too_large")
        payload = store.put_bytes("report", report_bytes)
        parents = tuple(
            Parent(f"event-{index}", event["event_artifact_id"])
            for index, event in enumerate(self.record["events"])
            if event.get("event_artifact_id")
        )
        manifest = Manifest(
            "analysis", producer, parents=parents, payloads=(payload,),
            parameters=FrozenObject.of({
                "schema": REPORT_SCHEMA, "session_id": self.record["session_id"],
                "scenario_id": self.record["scenario_id"], "status": self.record["status"],
                "scope": ("managed_text_menu_engineering_only; only operations made through "
                          "this Workbench client are recorded"),
                **({"session_semantics": "workbench-segment-v2-host-owned-service"}
                   if self.record.get("service_binding") is not None else {}),
            }),
        )
        self.record["report_artifact_id"] = store.publish(manifest)
        self._save()

    def status(self) -> dict[str, Any]:
        with self.lock:
            record = {key: value for key, value in self.record.items()
                      if key not in {"producer", "pending_request_id", "stop_requested",
                                     "pending_control_claim_request_id",
                                     "uncertain_claim_request_id", "uncertain_control_epoch"}}
            record = decode_json(json_bytes(record))
            try:
                profile = _read_profile(self.config)
                availability = "configured"
                input_profile = profile["input_profile"]
            except BoundaryError as error:
                availability = error.code
                input_profile = None
            host_control = None
            if isinstance(self.client, _ManagedServiceEnvironment):
                try:
                    ready = self.client.refresh()
                    episode = ready.get("episode")
                    manager_state = self.client._manager.status().get("status")
                    if isinstance(episode, Mapping) and isinstance(manager_state, Mapping) and (
                        manager_state.get("control_held") == episode.get("control_held")
                    ):
                        host_control = {"held": episode.get("control_held") is True,
                                        "tainted": episode.get("tainted") is True,
                                        "closed": episode.get("closed") is True,
                                        "service_instance_id": ready.get("service_instance_id"),
                                        "runtime_instance_id": ready.get(
                                            "adapter_runtime_instance_id"),
                                        "game_continuity_id": episode.get(
                                            "game_continuity_id")}
                    else:
                        host_control = {"status": "identity_mismatch"}
                except Exception:
                    host_control = {"status": "unavailable"}
            elif availability == "configured":
                paths = self.config.state_dir / "managed-host-service"
                if (paths / "client.json").is_file() or (paths / "manager.json").is_file():
                    try:
                        _client, manager, ready = self._managed_service_handles(
                            _read_profile(self.config)
                        )
                        episode = ready.get("episode")
                        manager_state = manager.status().get("status")
                        if isinstance(episode, Mapping) and isinstance(manager_state, Mapping):
                            host_control = {"held": episode.get("control_held") is True,
                                            "tainted": episode.get("tainted") is True,
                                            "closed": episode.get("closed") is True,
                                            "service_instance_id": ready.get("service_instance_id"),
                                            "runtime_instance_id": ready.get(
                                                "adapter_runtime_instance_id"),
                                            "game_continuity_id": episode.get(
                                                "game_continuity_id")}
                    except Exception:
                        host_control = {"status": "unavailable"}
            return {"schema": SCHEMA, "availability": availability,
                    "input_profile": input_profile,
                    "host_control": host_control,
                    "scenarios": [{key: SCENARIO[key] for key in
                                   ("id", "label", "seed", "character", "scope")}],
                    "session": record}

    def reports(self) -> dict[str, Any]:
        store = self._report_store(create=False)
        if store is None:
            return {"schema": REPORT_SCHEMA, "items": []}
        items = []
        for artifact_id in store.manifest_ids():
            manifest = store.get_manifest(artifact_id)
            if manifest.kind != "analysis":
                continue
            params = manifest.parameters.value()
            if params.get("schema") == REPORT_SCHEMA:
                items.append({"artifact_id": artifact_id, **params})
        return {"schema": REPORT_SCHEMA, "items": items[-20:]}

    def report_archive(self, artifact_id: str) -> ManifestArtifactStore:
        """Open the exact app-owned immutable report archive for explicit import."""
        if re.fullmatch(r"[0-9a-f]{64}", artifact_id) is None:
            raise BoundaryError("local_environment", "report_not_found")
        store = self._report_store(create=False)
        if store is None:
            raise BoundaryError("local_environment", "report_not_found")
        try:
            manifest = store.get_manifest(artifact_id)
            if (manifest.kind != "analysis"
                    or manifest.parameters.value().get("schema") != REPORT_SCHEMA):
                raise ValueError
        except (OSError, KeyError, ValueError, BoundaryError) as error:
            raise BoundaryError("local_environment", "report_not_found") from error
        return store

    def report(self, artifact_id: str) -> dict[str, Any]:
        store = self.report_archive(artifact_id)
        try:
            manifest = store.get_manifest(artifact_id)
            if manifest.kind != "analysis" or (
                manifest.parameters.value().get("schema") != REPORT_SCHEMA
            ):
                raise ValueError
            value = decode_json(store.bytes(manifest.payload("report"), maximum=8 * 1024 * 1024))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (OSError, KeyError, ValueError, BoundaryError) as error:
            raise BoundaryError("local_environment", "report_not_found") from error

    def event(self, artifact_id: str) -> dict[str, Any]:
        if re.fullmatch(r"[0-9a-f]{64}", artifact_id) is None:
            raise BoundaryError("local_environment", "event_not_found")
        store = self._report_store(create=False)
        if store is None:
            raise BoundaryError("local_environment", "event_not_found")
        try:
            manifest = store.get_manifest(artifact_id)
            if manifest.kind != "run_event" or (
                manifest.parameters.value().get("schema") != EVENT_SCHEMA
            ):
                raise ValueError
            value = decode_json(b"".join(store.read_payload(manifest.payload("event"))))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (OSError, KeyError, ValueError, BoundaryError) as error:
            raise BoundaryError("local_environment", "event_not_found") from error

    def _publish_event(self, value: dict[str, Any], session_id: str,
                       request_id: str) -> str:
        store = self._report_store(create=True)
        assert store is not None
        source = self.record["producer"]
        producer = Producer(REPOSITORY, source["source_revision"], source["uv_lock_sha256"])
        binding = self.record.get("service_binding")
        if isinstance(binding, Mapping):
            value["managed_episode_binding"] = {
                key: binding[key] for key in
                ("service_instance_id", "runtime_instance_id", "game_continuity_id")
            }
        payload = store.put_bytes("event", json_bytes(value))
        manifest = Manifest(
            "run_event", producer, payloads=(payload,),
            parameters=FrozenObject.of({
                "schema": EVENT_SCHEMA, "scenario_id": SCENARIO["id"],
                "session_id": session_id, "request_id": request_id,
                **({"service_instance_id": binding["service_instance_id"],
                    "runtime_instance_id": binding["runtime_instance_id"],
                    "game_continuity_id": binding["game_continuity_id"]}
                   if isinstance(binding, Mapping) else {}),
            }),
        )
        return store.publish(manifest)

    def _producer(self) -> dict[str, str]:
        identity = tool_identity()
        if not isinstance(identity.get("source_revision"), str) or not re.fullmatch(
            r"[0-9a-f]{40}", identity["source_revision"]
        ):
            raise BoundaryError("local_environment", "source_identity_unavailable")
        return {"source_revision": identity["source_revision"],
                "uv_lock_sha256": identity["uv_lock_sha256"]}

    def start(self, scenario_id: object, *, scene_artifact_id: object | None = None
              ) -> dict[str, Any]:
        with self.lifecycle_lock:
            return self._start_locked(scenario_id, scene_artifact_id=scene_artifact_id)

    def _start_locked(
        self, scenario_id: object, *, scene_artifact_id: object | None = None
    ) -> dict[str, Any]:
        if scenario_id != SCENARIO["id"]:
            raise BoundaryError("local_environment", "scenario_unknown")
        with self.lock:
            if self.record["status"] in ACTIVE | UNRESOLVED:
                raise BoundaryError("local_environment", "session_in_progress_or_unknown")
            if self.worker is not None and self.worker.is_alive():
                raise BoundaryError("local_environment", "session_in_progress_or_unknown")
            if self.record["status"] in TERMINAL and not self.record.get("report_artifact_id"):
                raise BoundaryError("local_environment", "report_recovery_required")
            profile = _read_profile(self.config)
            seed = SCENARIO["seed"]
            if scene_artifact_id is not None:
                scene = self.scene(scene_artifact_id)
                seed = scene["seed"]
            if scene_artifact_id is not None:
                self._validate_scene_start(scene_artifact_id, profile)
            producer = self._producer()
            session_id = uuid.uuid4().hex
            previous = self.record
            self.record = {"schema": SCHEMA, "status": "starting", "session_id": session_id,
                           "scenario_id": scenario_id, "seed": seed,
                           "input_profile": profile["input_profile"],
                           # The worker verifies these exact package bytes before activation.
                           # Capture only public identity, never private installation paths;
                           # report reads must not reconstruct it from a later profile.
                           "host_package_pin": dict(profile["host_package_pin"]),
                           "producer": producer, "events": [], "context": None,
                           "initial_context": None, "episode_identity": None,
                           "scene_artifact_id": scene_artifact_id}
            self.stopping = False
            self.cleanup_confirmed = False
            self._unclosed_start_client = None
            self.stop_outcome_unknown = False
            if not self._try_save():
                self.record = (previous if previous["status"] != "idle"
                               else {"schema": SCHEMA, "status": "idle"})
                if not self._try_save():
                    self.record = {"status": "interrupted_unknown",
                                   "error_code": "journal_restore_failed"}
                raise BoundaryError("local_environment", "session_persistence_failed")
            self.worker = threading.Thread(target=self._start_worker,
                                           args=(session_id, profile, seed), daemon=True)
            self.worker.start()
            return self.status()

    def resume(self, *, expected_session_id: str | None = None,
               expected_service_instance_id: str | None = None,
               expected_runtime_instance_id: str | None = None,
               expected_game_continuity_id: str | None = None) -> dict[str, Any]:
        with self.lifecycle_lock:
            return self._resume_locked(
                expected_session_id=expected_session_id,
                expected_service_instance_id=expected_service_instance_id,
                expected_runtime_instance_id=expected_runtime_instance_id,
                expected_game_continuity_id=expected_game_continuity_id,
            )

    def _resume_locked(self, *, expected_session_id: str | None,
                       expected_service_instance_id: str | None,
                       expected_runtime_instance_id: str | None,
                       expected_game_continuity_id: str | None) -> dict[str, Any]:
        """Continue the exact attached episode as a new Workbench report segment."""
        with self.lock:
            state = self.record.get("status")
            binding = self.record.get("service_binding")
            if state in {"starting", "resuming", "submitting", "stopping"}:
                raise BoundaryError("local_environment", "session_in_progress_or_unknown")
            if not isinstance(binding, Mapping) or set(binding) != {
                "service_instance_id", "runtime_instance_id", "game_continuity_id"
            }:
                raise BoundaryError("local_environment", "managed_service_binding_unavailable")
            if expected_session_id is not None and self.record.get("session_id") != (
                expected_session_id
            ):
                raise BoundaryError("local_environment", "managed_service_binding_mismatch")
            if (expected_service_instance_id is not None
                    and expected_service_instance_id != binding.get("service_instance_id")) or (
                expected_runtime_instance_id is not None
                and expected_runtime_instance_id != binding.get("runtime_instance_id")
            ) or (expected_game_continuity_id is not None
                  and expected_game_continuity_id != binding.get("game_continuity_id")):
                raise BoundaryError("local_environment", "managed_service_binding_mismatch")
            if self.worker is not None and self.worker.is_alive():
                raise BoundaryError("local_environment", "session_in_progress_or_unknown")
            profile = _read_profile(self.config)
            producer = self._producer()
            previous = self.record
            same_segment = state in {"control_held", "active"}
            uncertain_claim_id = previous.get("uncertain_claim_request_id") or previous.get(
                "pending_control_claim_request_id"
            )
            session_id = previous["session_id"] if same_segment else uuid.uuid4().hex
            self.record = {
                "schema": SCHEMA, "status": "resuming", "session_id": session_id,
                "scenario_id": SCENARIO["id"], "seed": previous.get("seed", SCENARIO["seed"]),
                "input_profile": profile["input_profile"],
                "host_package_pin": dict(profile["host_package_pin"]),
                "producer": producer, "events": previous.get("events", []) if same_segment else [],
                "context": None, "initial_context": previous.get("initial_context")
                if same_segment else None,
                "episode_identity": previous.get("episode_identity") if same_segment else None,
                "service_binding": dict(binding),
                "session_semantics": "workbench-segment-v2-host-owned-service",
                "continued_from_session_id": previous.get("continued_from_session_id")
                if same_segment else previous.get("session_id"),
                "continued_from_report_artifact_id": previous.get("report_artifact_id"),
                "scene_artifact_id": previous.get("scene_artifact_id"),
                **({"uncertain_claim_request_id": uncertain_claim_id}
                   if isinstance(uncertain_claim_id, str) else {}),
                **({"uncertain_control_epoch": previous["uncertain_control_epoch"]}
                   if isinstance(previous.get("uncertain_control_epoch"), str) else {}),
            }
            self.stopping = False
            self.cleanup_confirmed = False
            self.stop_outcome_unknown = False
            if not self._try_save():
                self.record = previous
                raise BoundaryError("local_environment", "session_persistence_failed")
            self.worker = threading.Thread(
                target=self._resume_worker, args=(session_id, profile, dict(binding)), daemon=True
            )
            self.worker.start()
            return self.status()

    def _resume_worker(self, session_id: str, profile: dict[str, Any],
                       expected: dict[str, Any]) -> None:
        client = None
        try:
            client_handle, manager, ready = self._managed_service_handles(profile)
            client = _ManagedServiceEnvironment(
                client_handle, manager, ready, profile["input_profile"],
                lambda request_id, binding: self._persist_claim_offer(
                    session_id, request_id, binding
                ),
                session_id=session_id,
            )
            episode = ready.get("episode")
            binding = client.binding()
            if any(binding[key] != expected[key] for key in expected):
                raise BoundaryError("local_environment", "managed_service_binding_mismatch")
            if not isinstance(episode, Mapping) or episode.get("closed") is True or (
                episode.get("tainted") is True
            ):
                raise BoundaryError("local_environment", "managed_episode_unavailable")
            with self.lock:
                if self.record.get("session_id") != session_id:
                    return
                self.client = client
            if episode.get("control_held") is True:
                manager_state = manager.status().get("status")
                control = manager_state.get("control") if isinstance(
                    manager_state, Mapping
                ) else None
                claim_id = self.record.get("uncertain_claim_request_id")
                epoch = self.record.get("uncertain_control_epoch")
                ownership_matches = isinstance(control, Mapping) and (
                    control.get("runtime_instance_id") == expected["runtime_instance_id"]
                    and control.get("game_continuity_id") == expected["game_continuity_id"]
                    and (not isinstance(claim_id, str)
                         or control.get("claim_request_id") == claim_id)
                    and (not isinstance(epoch, str) or control.get("control_epoch") == epoch)
                )
                with self.lock:
                    error_code = "managed_control_held"
                    if ownership_matches and isinstance(claim_id, str):
                        error_code = ("managed_control_release_unknown"
                                      if isinstance(epoch, str)
                                      else "managed_control_claim_unknown")
                    self.record.update(status="control_held", error_code=error_code)
                    self._save()
                return
            with self.lock:
                # An unheld lease cannot be the outstanding control offer.
                self.record.pop("pending_control_claim_request_id", None)
                self.record.pop("uncertain_claim_request_id", None)
                self.record.pop("uncertain_control_epoch", None)
            identity = _public_identity(client.episode_identity())
            if identity["episode_provenance"].get("runtime_instance_id") != expected[
                "runtime_instance_id"
            ]:
                raise BoundaryError("local_environment", "managed_service_binding_mismatch")
            context = self._context(
                client.observe_text_menu(input_profile=profile["input_profile"]),
                profile["input_profile"],
            )
            if context["game_continuity_id"] != expected["game_continuity_id"]:
                raise BoundaryError("local_environment", "managed_service_binding_mismatch")
            with self.lock:
                if self.record.get("session_id") == session_id:
                    self.record.update(status="active", context=context,
                                       initial_context=context, episode_identity=identity,
                                       error_code=None)
                    self._save()
        except Exception as error:
            with self.lock:
                if self.record.get("session_id") == session_id:
                    code = error.code if isinstance(error, BoundaryError) else getattr(
                        error, "code", "managed_resume_failed"
                    )
                    if isinstance(client, _ManagedServiceEnvironment) and client.control and (
                        "uncertain" in client.control
                    ):
                        code = ("managed_control_claim_unknown"
                                if client.control["uncertain"] == "claim"
                                else "managed_control_release_unknown")
                        if client.control["uncertain"] == "claim":
                            self.record["uncertain_claim_request_id"] = client.control.get(
                                "request_id"
                            )
                        if client.control["uncertain"] == "release":
                            self.record["uncertain_control_epoch"] = client.control.get("epoch")
                            self.record["uncertain_claim_request_id"] = client.control.get(
                                "request_id"
                            )
                    self.record.pop("pending_control_claim_request_id", None)
                    self.record.update(status="failed", error_code=code)
                    self._try_save()
                    with suppress(Exception):
                        self._publish()

    def _start_worker(self, session_id: str, profile: dict[str, Any], seed: str) -> None:
        client = None
        error_code = None
        factory_entered = False
        constructor_cleanup_confirmed = False
        active_saved = False
        try:
            host_root = Path(profile["host_package_directory"])
            pin = profile["host_package_pin"]
            _verify_host(host_root, pin)
            candidate = Path(profile["candidate_directory"])
            if self.audit(host_root, candidate) != profile["audit"]:
                raise BoundaryError("local_environment", "managed_candidate_changed")
            command = ["node", str(host_root / "tools/managed-pe-driver.mjs"),
                       "--candidate", str(candidate), "--character", SCENARIO["character"],
                       "--quiet-diagnostics"]
            factory_entered = True
            client = self.client_factory(command, host_root, pin)
            with self.lock:
                if self.stopping or self.record.get("session_id") != session_id:
                    return
                self.client = client
            ready_build = client.ready.get("candidate_build")
            is_managed_service = isinstance(client, _ManagedServiceEnvironment)
            if not isinstance(ready_build, Mapping) or any(
                ready_build.get(key) != profile["audit"][key] for key in AUDIT_FIELDS
            ) or (not is_managed_service and client.ready.get("protocol") != (
                "sts2.headless/managed-player-environment-driver-1"
            )) or not isinstance(client.ready.get("exact_game"), Mapping) or (
                client.ready["exact_game"].get("sts2_dll_sha256")
                != SCENARIO["exact_game_assembly_sha256"]
            ):
                raise BoundaryError("local_environment", "managed_candidate_changed")
            client.reset(seed)
            identity = _public_identity(client.episode_identity())
            provenance = identity["episode_provenance"]
            if (identity["candidate_build"] != profile["audit"]
                    or provenance["verdict"] != "provenance_pass"
                    or provenance["requested_seed"] != seed
                    or provenance["actual_seed"] != seed):
                raise BoundaryError("local_environment", "episode_identity_invalid")
            service_binding = client.binding() if isinstance(
                client, _ManagedServiceEnvironment
            ) else None
            if service_binding is not None:
                with self.lock:
                    if self.stopping or self.record.get("session_id") != session_id:
                        return
                    self.record.update(service_binding=service_binding,
                                       session_semantics=(
                                           "workbench-segment-v2-host-owned-service"
                                       ))
                    self._save()
            context = self._context(
                self._observe(client, profile["input_profile"]), profile["input_profile"]
            )
            persistent = context["snapshot"].get("persistent")
            content = persistent.get("content") if isinstance(persistent, Mapping) else None
            run = content.get("run") if isinstance(content, Mapping) else None
            player = content.get("player") if isinstance(content, Mapping) else None
            if (not isinstance(run, Mapping) or type(run.get("ascension")) is not int
                    or run.get("ascension") != 0
                    or not isinstance(player, Mapping)
                    or player.get("character_definition_id") != "DEFECT"):
                raise BoundaryError("local_environment", "initial_character_or_ascension_invalid")
            with self.lock:
                if self.stopping or self.record.get("session_id") != session_id:
                    return
                self.record.update(status="active", context=context, initial_context=context,
                                   episode_identity=identity, service_binding=service_binding)
                if service_binding is not None:
                    self.record["session_semantics"] = (
                        "workbench-segment-v2-host-owned-service"
                    )
                try:
                    self._save()
                except Exception as error:
                    raise BoundaryError(
                        "local_environment", "session_persistence_failed"
                    ) from error
                active_saved = True
        except Exception as error:
            error_code = (error.code if isinstance(error, BoundaryError) else
                          getattr(error, "code", "managed_start_failed"))
            if isinstance(client, _ManagedServiceEnvironment) and client.control and (
                "uncertain" in client.control
            ):
                error_code = ("managed_control_claim_unknown"
                              if client.control["uncertain"] == "claim"
                              else "managed_control_release_unknown")
            constructor_cleanup_confirmed = getattr(error, "cleanup_confirmed", None) is True
        finally:
            # A saved active session transfers client ownership to submit/stop.
            # Its status may already have advanced by the time this finally runs.
            with self.lock:
                same_session = self.record.get("session_id") == session_id
                handed_off = (active_saved and same_session and (
                    self.client is client or (self.stopping and self.client is None)))
            # A public constructor can start a child before it raises. Without
            # a returned handle, this owner cannot prove that child was closed.
            close_failed = (factory_entered and client is None
                            and not constructor_cleanup_confirmed)
            if client is not None and not handed_off:
                try:
                    client.close(force=True)
                except Exception:
                    close_failed = True
            with self.lock:
                if close_failed and client is not None and self.client is not client:
                    # A changed binding cannot erase the original child after
                    # its close failed. Stop must confirm both handles.
                    self._unclosed_start_client = client
                if self.record.get("session_id") != session_id or handed_off:
                    if (handed_off and self.stopping and self.cleanup_confirmed
                            and self.record.get("status") == "stopping"):
                        self._finish_stop()
                elif self.stopping:
                    if close_failed:
                        self.cleanup_confirmed = False
                    if not close_failed:
                        self.cleanup_confirmed = True
                        if self.client is client:
                            self.client = None
                    self._finish_stop()
                elif active_saved:
                    # The exact client binding changed without an owning stop.
                    # Keep the session unresolved instead of publishing failure.
                    self.record.update(status="cleanup_unknown",
                                       error_code="startup_client_ownership_changed")
                    self._try_save()
                else:
                    if isinstance(client, _ManagedServiceEnvironment) and client.control and (
                        "uncertain" in client.control
                    ):
                        self.record.pop("pending_control_claim_request_id", None)
                        self.record["uncertain_claim_request_id"] = client.control.get(
                            "request_id"
                        )
                        if client.control["uncertain"] == "release":
                            self.record["uncertain_control_epoch"] = client.control.get("epoch")
                    if (isinstance(client, _ManagedServiceEnvironment) and client.control
                            and client.control.get("uncertain") == "release"):
                        self.record["uncertain_control_epoch"] = client.control.get("epoch")
                    if close_failed and error_code not in {
                        "managed_control_claim_unknown", "managed_control_release_unknown"
                    }:
                        self.record.update(status="cleanup_unknown",
                                           error_code="host_cleanup_unknown")
                    else:
                        self.record.update(status="failed", error_code=error_code)
                    if not close_failed:
                        self.client = None
                    saved = self._try_save()
                    if not close_failed and saved:
                        self._publish()

    @staticmethod
    def _observe(client: Any, input_profile: str) -> Any:
        if input_profile == "text-menu-v2":
            return client.observe_text_menu(input_profile=input_profile)
        return client.observe_text_menu()

    @staticmethod
    def _context(value: Any, input_profile: str) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {
            "schema", "snapshot", "game_continuity_id"
        } or value["schema"] != TEXT_PROFILES[input_profile][0]:
            raise BoundaryError("local_environment", "text_context_invalid")
        snapshot = value["snapshot"]
        if not isinstance(snapshot, Mapping) or (
            snapshot.get("schema") != TEXT_PROFILES[input_profile][1]
            or snapshot.get("input_profile") != input_profile
            or not isinstance(snapshot.get("snapshot_id"), str)
            or not isinstance(value["game_continuity_id"], str)
        ):
            raise BoundaryError("local_environment", "text_context_invalid")
        return dict(value)

    def submit(self, session_id: object, action_id: object, snapshot_id: object,
               continuity_id: object) -> dict[str, Any]:
        with self.lock:
            if self.record.get("status") != "active" or self.client is None or (
                self.record.get("session_id") != session_id
            ):
                raise BoundaryError("local_environment", "session_not_active")
            context = self.record["context"]
            snapshot = context["snapshot"]
            menu = snapshot.get("menu_actions")
            if (context["game_continuity_id"] != continuity_id
                    or snapshot["snapshot_id"] != snapshot_id):
                raise BoundaryError("local_environment", "stale_text_context")
            if (snapshot.get("status") != "interactive" or not isinstance(menu, dict)
                    or menu.get("status") != "complete"
                    or not isinstance(menu.get("actions"), list)
                    or menu.get("materialized_count") != len(menu["actions"])
                    or menu.get("total_count") != len(menu["actions"])
                    or not any(isinstance(action, dict) and
                               action.get("action_id") == action_id
                               for action in menu["actions"])):
                raise BoundaryError("local_environment", "action_not_in_current_menu")
            request_id = uuid.uuid4().hex
            before_context = decode_json(json_bytes(context))
            self.record.update(status="submitting", pending_request_id=request_id)
            client = self.client
            if self._try_save():
                self.worker = threading.Thread(
                    target=self._submit_worker,
                    args=(self.record["session_id"], client, action_id, snapshot_id,
                          continuity_id, request_id, self.record["input_profile"],
                          before_context), daemon=True,
                )
                self.worker.start()
                return self.status()
            self.record.pop("pending_request_id", None)
            self.record.update(status="unknown",
                               error_code="session_persistence_failed_before_offer")
            self._try_save()
        try:
            client.close(force=True)
        except Exception:
            with self.lock:
                self.record.update(status="cleanup_unknown", error_code="host_cleanup_unknown")
                self._try_save()
        raise BoundaryError("local_environment", "session_persistence_failed_before_offer")

    def _submit_worker(self, session_id: str, client: Any, action_id: str,
                       snapshot_id: str, continuity_id: str, request_id: str,
                       input_profile: str, before_context: dict[str, Any]) -> None:
        result = None
        context = None
        error_code = None
        event_artifact_id = None
        control_held = False
        try:
            if input_profile == "text-menu-v2":
                result = client.submit_text_menu(
                    action_id, snapshot_id, continuity_id, request_id,
                    input_profile=input_profile,
                )
            else:
                result = client.submit_text_menu(
                    action_id, snapshot_id, continuity_id, request_id,
                )
            if result.get("status") != "unknown" and result.get("successor") is not None:
                if isinstance(client, _ManagedServiceEnvironment):
                    context = self._context({
                        "schema": TEXT_PROFILES[input_profile][0],
                        "snapshot": result["successor"],
                        "game_continuity_id": continuity_id,
                    }, input_profile)
                else:
                    context = self._context(
                        self._observe(client, input_profile), input_profile,
                    )
                    if context["snapshot"] != result["successor"] or (
                        context["game_continuity_id"] != continuity_id
                    ):
                        error_code = "successor_observation_mismatch"
            elif result.get("status") == "unknown":
                error_code = "native_delivery_unknown"
            else:
                error_code = "successor_unavailable"
        except BoundaryError as error:
            if error.code == "managed_control_held":
                control_held = True
                error_code = error.code
            else:
                error_code = error.code
                if isinstance(client, _ManagedServiceEnvironment):
                    result = client.last_submit_result
                    if client.control and "uncertain" in client.control:
                        error_code = ("managed_control_claim_unknown"
                                      if client.control["uncertain"] == "claim"
                                      else "managed_control_release_unknown")
        except Exception:
            error_code = "submission_outcome_unknown"
            if isinstance(client, _ManagedServiceEnvironment):
                result = client.last_submit_result
                if client.control and "uncertain" in client.control:
                    error_code = ("managed_control_claim_unknown"
                                  if client.control["uncertain"] == "claim"
                                  else "managed_control_release_unknown")
        if control_held:
            with self.lock:
                if self.record.get("session_id") == session_id:
                    self.record.update(status="control_held", error_code=error_code)
                    self.record.pop("pending_request_id", None)
                    self._try_save()
            return
        event = {"request_id": request_id, "action_id": action_id,
                 "before_context": before_context,
                 "expected_snapshot_id": snapshot_id, "result": result,
                 "error_code": error_code}
        try:
            event_artifact_id = self._publish_event(event, session_id, request_id)
        except Exception:
            error_code = "event_publication_unknown"
        summary = {
            "request_id": request_id, "action_id": action_id,
            "expected_snapshot_id": snapshot_id, "error_code": error_code,
            "result_status": result.get("status") if isinstance(result, Mapping) else None,
            "native_delivery": (result.get("native_delivery")
                                if isinstance(result, Mapping) else None),
            "event_artifact_id": event_artifact_id,
        }
        with self.lock:
            if self.record.get("session_id") != session_id:
                return
            self.record["events"].append(summary)
            if isinstance(client, _ManagedServiceEnvironment) and client.control and (
                "uncertain" in client.control
            ):
                self.record.pop("pending_control_claim_request_id", None)
                self.record["uncertain_claim_request_id"] = client.control.get("request_id")
                if client.control["uncertain"] == "release":
                    self.record["uncertain_control_epoch"] = client.control.get("epoch")
            self.record.pop("pending_request_id", None)
            if self.stopping:
                if error_code is not None:
                    self.record["error_code"] = error_code
                if not self._try_save():
                    self.stop_outcome_unknown = True
                    self.record["error_code"] = "session_persistence_failed"
                self._finish_stop()
                return
            if error_code is None:
                self.record.update(status="active", context=context)
            else:
                self.record.update(status="unknown", error_code=error_code)
            if not self._try_save():
                error_code = "session_persistence_failed"
                self.record.update(status="unknown", error_code=error_code)
                self._try_save()
        if error_code is not None:
            try:
                client.close(force=True)
            except Exception:
                with self.lock:
                    if self.record.get("session_id") == session_id:
                        self.record.update(status="cleanup_unknown",
                                           error_code="host_cleanup_unknown")
                        self._try_save()

    def _finish_stop(self) -> None:
        """Call under lock after close; a live worker keeps cleanup unresolved."""
        worker = self.worker
        worker_done = worker is None or not worker.is_alive() or (
            worker is threading.current_thread()
        )
        if not self.cleanup_confirmed or self._unclosed_start_client is not None:
            self.record.update(status="cleanup_unknown", error_code="host_cleanup_unknown")
            self._try_save()
            return
        if not worker_done:
            self.record.update(status="stopping", error_code="event_finalization_pending")
            self._try_save()
            return
        self.record["status"] = (
            "stopped_outcome_unknown" if self.stop_outcome_unknown else "stopped"
        )
        if self.record.get("error_code") == "event_finalization_pending":
            self.record.pop("error_code")
        if self.stop_outcome_unknown:
            self.record.setdefault("error_code", "action_or_observation_unknown")
        self.client = None
        if not self._try_save():
            self.record["error_code"] = "session_persistence_failed"
            return
        self._publish()

    def stop(self, session_id: object) -> dict[str, Any]:
        with self.lifecycle_lock:
            return self._stop_locked(session_id)

    def _stop_locked(self, session_id: object) -> dict[str, Any]:
        with self.lock:
            if self.record.get("session_id") != session_id:
                raise BoundaryError("local_environment", "session_not_found")
            state = self.record["status"]
            if state in TERMINAL:
                if not self._try_save():
                    raise BoundaryError("local_environment", "session_persistence_failed")
                if not self.record.get("report_artifact_id"):
                    self._publish()
                return self.status()
            if state == "stopping":
                return self.status()
            self.stopping = True
            uncertain = state in {"starting", "submitting", "unknown",
                                  "interrupted_unknown", "cleanup_unknown"}
            self.stop_outcome_unknown = uncertain
            self.record["status"] = "stopping"
            if not self._try_save():
                self.record["error_code"] = "session_persistence_failed"
            client = self.client
            unclosed_start_client = self._unclosed_start_client
            worker = self.worker
        close_failed = False
        if client is not None:
            try:
                client.close(force=uncertain)
            except Exception:
                close_failed = True
        if unclosed_start_client is not None and unclosed_start_client is not client:
            try:
                unclosed_start_client.close(force=True)
            except Exception:
                close_failed = True
            else:
                with self.lock:
                    if self._unclosed_start_client is unclosed_start_client:
                        self._unclosed_start_client = None
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2)
        with self.lock:
            if self.record.get("session_id") == session_id:
                if ((client is not None or unclosed_start_client is not None)
                        and not close_failed and self._unclosed_start_client is None):
                    self.cleanup_confirmed = True
                self._finish_stop()
            return self.status()

    def close(self) -> None:
        with self.lock:
            session_id = self.record.get("session_id")
            active = self.record["status"] in ACTIVE | UNRESOLVED
        if active and session_id is not None:
            self.stop(session_id)
