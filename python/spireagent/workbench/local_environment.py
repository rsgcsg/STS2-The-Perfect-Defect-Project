"""One explicitly started Managed text-menu engineering session per Workbench.

The browser selects a fixed seed scenario, never a program or filesystem path.
The installed public Host client owns the native process and gameplay delivery.
"""

from __future__ import annotations

import importlib
import re
import stat
import subprocess
import threading
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

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
PROFILE_FILE = "managed-host-profile-v1.json"
JOURNAL_FILE = "managed-environment-session-v1.json"
REPORT_ROOT = "managed-environment-reports"
HOST_PACKAGE = "@rsgcsg/sts2-host-runtime"
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
ACTIVE = frozenset({"starting", "active", "submitting", "stopping"})
UNRESOLVED = frozenset({"unknown", "interrupted_unknown", "cleanup_unknown"})
TERMINAL = frozenset({"stopped", "stopped_outcome_unknown", "failed"})


class HostClientPreparationError(RuntimeError):
    """The public Host client was not constructed and no process was spawned."""

    cleanup_confirmed = True


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
    ) -> None:
        self.config = config
        self.audit = audit
        self.client_factory = client_factory or self._public_client
        self.lock = threading.RLock()
        self.client: Any | None = None
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

    @staticmethod
    def _public_client(command: list[str], host_root: Path, pin: dict[str, Any]) -> Any:
        from spireagent.host_runtime_client import activate_host_runtime_client

        try:
            activate_host_runtime_client(host_root, pin)
            client = importlib.import_module("sts2_headless.client")
            constructor = client.ManagedPlayerEnvironment
        except Exception as error:
            raise HostClientPreparationError("host_public_client_unavailable") from error
        return constructor(command, response_timeout_seconds=15)

    def _report_store(self, *, create: bool) -> ManifestArtifactStore | None:
        root = self.config.state_dir / REPORT_ROOT
        if root.is_symlink() or (root.exists() and not _ordinary(root, directory=True)):
            raise BoundaryError("local_environment", "report_store_unsafe")
        if not create and not root.is_dir():
            return None
        return ManifestArtifactStore(LocalBlobStore(root, create=create, readonly=not create))

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
                  if key not in {"producer", "pending_request_id", "stop_requested"}}
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
                "scope": "managed_text_menu_engineering_only",
            }),
        )
        self.record["report_artifact_id"] = store.publish(manifest)
        self._save()

    def status(self) -> dict[str, Any]:
        with self.lock:
            record = {key: value for key, value in self.record.items()
                      if key not in {"producer", "pending_request_id", "stop_requested"}}
            record = decode_json(json_bytes(record))
            try:
                profile = _read_profile(self.config)
                availability = "configured"
                input_profile = profile["input_profile"]
            except BoundaryError as error:
                availability = error.code
                input_profile = None
            return {"schema": SCHEMA, "availability": availability,
                    "input_profile": input_profile,
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

    def report(self, artifact_id: str) -> dict[str, Any]:
        if re.fullmatch(r"[0-9a-f]{64}", artifact_id) is None:
            raise BoundaryError("local_environment", "report_not_found")
        store = self._report_store(create=False)
        if store is None:
            raise BoundaryError("local_environment", "report_not_found")
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
        payload = store.put_bytes("event", json_bytes(value))
        manifest = Manifest(
            "run_event", producer, payloads=(payload,),
            parameters=FrozenObject.of({
                "schema": EVENT_SCHEMA, "scenario_id": SCENARIO["id"],
                "session_id": session_id, "request_id": request_id,
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

    def start(self, scenario_id: object) -> dict[str, Any]:
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
            producer = self._producer()
            session_id = uuid.uuid4().hex
            previous = self.record
            self.record = {"schema": SCHEMA, "status": "starting", "session_id": session_id,
                           "scenario_id": scenario_id, "seed": SCENARIO["seed"],
                           "input_profile": profile["input_profile"],
                           "producer": producer, "events": [], "context": None,
                           "episode_identity": None}
            self.stopping = False
            self.cleanup_confirmed = False
            self.stop_outcome_unknown = False
            if not self._try_save():
                self.record = (previous if previous["status"] != "idle"
                               else {"schema": SCHEMA, "status": "idle"})
                if not self._try_save():
                    self.record = {"status": "interrupted_unknown",
                                   "error_code": "journal_restore_failed"}
                raise BoundaryError("local_environment", "session_persistence_failed")
            self.worker = threading.Thread(target=self._start_worker,
                                           args=(session_id, profile), daemon=True)
            self.worker.start()
            return self.status()

    def _start_worker(self, session_id: str, profile: dict[str, Any]) -> None:
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
            if not isinstance(ready_build, Mapping) or any(
                ready_build.get(key) != profile["audit"][key] for key in AUDIT_FIELDS
            ) or client.ready.get("protocol") != (
                "sts2.headless/managed-player-environment-driver-1"
            ) or not isinstance(client.ready.get("exact_game"), Mapping) or (
                client.ready["exact_game"].get("sts2_dll_sha256")
                != SCENARIO["exact_game_assembly_sha256"]
            ):
                raise BoundaryError("local_environment", "managed_candidate_changed")
            client.reset(SCENARIO["seed"])
            identity = _public_identity(client.episode_identity())
            provenance = identity["episode_provenance"]
            if (identity["candidate_build"] != profile["audit"]
                    or provenance["verdict"] != "provenance_pass"
                    or provenance["requested_seed"] != SCENARIO["seed"]
                    or provenance["actual_seed"] != SCENARIO["seed"]):
                raise BoundaryError("local_environment", "episode_identity_invalid")
            context = self._context(
                self._observe(client, profile["input_profile"]), profile["input_profile"]
            )
            with self.lock:
                if self.stopping or self.record.get("session_id") != session_id:
                    return
                self.record.update(status="active", context=context, episode_identity=identity)
                try:
                    self._save()
                except Exception as error:
                    raise BoundaryError(
                        "local_environment", "session_persistence_failed"
                    ) from error
                active_saved = True
        except Exception as error:
            error_code = error.code if isinstance(error, BoundaryError) else "managed_start_failed"
            constructor_cleanup_confirmed = getattr(error, "cleanup_confirmed", None) is True
        finally:
            with self.lock:
                keep = (active_saved and self.client is client
                        and self.record.get("status") == "active")
            # A public constructor can start a child before it raises. Without
            # a returned handle, this owner cannot prove that child was closed.
            close_failed = (factory_entered and client is None
                            and not constructor_cleanup_confirmed)
            if client is not None and not keep:
                try:
                    client.close(force=True)
                except Exception:
                    close_failed = True
            with self.lock:
                if self.record.get("session_id") != session_id or keep:
                    pass
                elif self.stopping:
                    if not close_failed:
                        self.cleanup_confirmed = True
                        if self.client is client:
                            self.client = None
                    self._finish_stop()
                else:
                    self.record.update(
                        status="cleanup_unknown" if close_failed else "failed",
                        error_code="host_cleanup_unknown" if close_failed else error_code,
                    )
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
        except Exception:
            error_code = "submission_outcome_unknown"
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
        if not self.cleanup_confirmed:
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
            worker = self.worker
        close_failed = False
        if client is not None:
            try:
                client.close(force=uncertain)
            except Exception:
                close_failed = True
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2)
        with self.lock:
            if self.record.get("session_id") == session_id:
                if client is not None and not close_failed:
                    self.cleanup_confirmed = True
                self._finish_stop()
            return self.status()

    def close(self) -> None:
        with self.lock:
            session_id = self.record.get("session_id")
            active = self.record["status"] in ACTIVE | UNRESOLVED
        if active and session_id is not None:
            self.stop(session_id)
