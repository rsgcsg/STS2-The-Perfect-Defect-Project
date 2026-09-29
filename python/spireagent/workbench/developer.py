"""Developer project configuration, fixed public dependencies and local diagnostics.

This application composes Platform tools. It owns neither recording evidence nor
research admission, and never starts gameplay as a side effect of opening its UI.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from spireagent.json_boundary import BoundaryError, decode_json, digest, object_fields, text
from spireagent.package_identity import PackageIdentityError, validate_installed_package
from spireagent.source import REPOSITORY_URLS

CONFIG_SCHEMA = "stpd/developer-project-v1"
COMBINATION_SCHEMA = "stpd/developer-combination-v1"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / ".local" / "developer" / "project.json"
PUBLIC_REPOSITORY = REPOSITORY_URLS[0]


def endpoint(value: object, *, optional: bool = False) -> str:
    if value == "" and optional:
        return ""
    raw = text(value, "project.endpoint", maximum=2048)
    try:
        parsed = urlsplit(raw)
        permitted = parsed.scheme == "https" or (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )
        if (
            not permitted
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError
        _ = parsed.port
    except ValueError as error:
        raise BoundaryError("project", "invalid_endpoint") from error
    return raw.rstrip("/")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def combination(root: Path = ROOT) -> dict[str, Any]:
    """Read the explicit project composition, never infer compatibility from sibling repos."""
    obj = object_fields(
        decode_json((root / "configs/developer/combination-v1.json").read_bytes()),
        {
            "schema",
            "platform_repository",
            "platform_source_revision",
            "evidence_source_revision",
            "policy_mode",
            "node_packages",
        },
        "project.combination",
    )
    if obj["schema"] != COMBINATION_SCHEMA or obj["platform_repository"] not in REPOSITORY_URLS:
        raise BoundaryError("project", "unsupported_combination")
    digest(obj["platform_source_revision"], "project.platform", length=40)
    digest(obj["evidence_source_revision"], "project.evidence", length=40)
    if obj["policy_mode"] != "existing-adapter-only":
        raise BoundaryError("project", "unsupported_policy_mode")
    return dict(obj)


@dataclass(frozen=True)
class LocalResearchWorkspaceConfig:
    """An explicit pointer to existing local research objects and their index."""

    store_dir: Path
    registry_path: Path

    def to_dict(self) -> dict[str, str]:
        return {"store_dir": str(self.store_dir), "registry_path": str(self.registry_path)}


@dataclass(frozen=True)
class ProjectConfig:
    state_dir: Path
    hub_url: str
    platform_url: str
    delivery_config: Path | None
    combination: dict[str, Any]
    research_workspace: LocalResearchWorkspaceConfig | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema": CONFIG_SCHEMA,
            "state_dir": str(self.state_dir),
            "hub_url": self.hub_url,
            "platform_url": self.platform_url,
            "delivery_config": str(self.delivery_config) if self.delivery_config else None,
            "combination": self.combination,
        }
        if self.research_workspace is not None:
            value["research_workspace"] = self.research_workspace.to_dict()
        return value

    @classmethod
    def load(cls, path: Path, *, require_current_combination: bool = True) -> ProjectConfig:
        raw = decode_json(path.read_bytes())
        if not isinstance(raw, dict):
            raise BoundaryError("project.config", "missing_or_unknown_fields")
        fields = {
            "schema",
            "state_dir",
            "hub_url",
            "platform_url",
            "delivery_config",
            "combination",
        }
        if frozenset(raw) not in {
            frozenset(fields),
            frozenset(fields | {"research_workspace"}),
        }:
            raise BoundaryError("project.config", "missing_or_unknown_fields")
        obj = object_fields(raw, set(raw), "project.config")
        if obj["schema"] != CONFIG_SCHEMA or not isinstance(obj["combination"], dict):
            raise BoundaryError("project", "unsupported_project_config")
        if require_current_combination and obj["combination"] != combination():
            raise BoundaryError("project", "combination_changed_rerun_setup")
        state = Path(text(obj["state_dir"], "project.state_dir"))
        delivery = obj["delivery_config"]
        if delivery is not None:
            delivery = text(delivery, "project.delivery_config")
        if not state.is_absolute() or (delivery is not None and not Path(delivery).is_absolute()):
            raise BoundaryError("project", "absolute_local_path_required")
        research_workspace = None
        if "research_workspace" in obj:
            research = object_fields(
                obj["research_workspace"],
                {"store_dir", "registry_path"},
                "project.research_workspace",
            )
            store_dir = Path(text(research["store_dir"], "project.research_store_dir"))
            registry_path = Path(text(research["registry_path"], "project.research_registry_path"))
            if not store_dir.is_absolute() or not registry_path.is_absolute():
                raise BoundaryError("project", "absolute_local_path_required")
            research_workspace = LocalResearchWorkspaceConfig(store_dir, registry_path)
        return cls(
            state,
            endpoint(obj["hub_url"], optional=True),
            endpoint(obj["platform_url"], optional=True),
            Path(delivery) if delivery is not None else None,
            dict(obj["combination"]),
            research_workspace,
        )


def setup(
    path: Path,
    *,
    state_dir: Path,
    hub_url: str = "",
    platform_url: str = "",
    delivery_config: Path | None = None,
    install: bool = True,
    replace_config: bool = False,
) -> dict[str, Any]:
    from spireagent.workbench.developer_server import instance_lock

    # Serialize configuration writers, including a relocation between state directories.
    # The same state lock excludes a live workbench or a stopped maintenance operation.
    with instance_lock(path.with_name(f".{path.name}.configure.lock")):
        current = (
            ProjectConfig.load(path, require_current_combination=False) if path.exists() else None
        )
        states = {state_dir.expanduser().resolve()}
        if current is not None:
            states.add(current.state_dir)
        with contextlib.ExitStack() as held:
            for state in sorted(states):
                held.enter_context(instance_lock(state / "instance.lock"))
            return _setup_unlocked(
                path,
                state_dir=state_dir,
                hub_url=hub_url,
                platform_url=platform_url,
                delivery_config=delivery_config,
                install=install,
                replace_config=replace_config,
            )


def _setup_unlocked(
    path: Path,
    *,
    state_dir: Path,
    hub_url: str = "",
    platform_url: str = "",
    delivery_config: Path | None = None,
    install: bool = True,
    replace_config: bool = False,
) -> dict[str, Any]:
    if delivery_config is not None and not hub_url:
        delivery_value = decode_json(delivery_config.read_bytes())
        if isinstance(delivery_value, dict):
            hub_url = endpoint(delivery_value.get("hub_url"))
    # Setup replaces composition/settings, not the independently selected workspace.
    current_config = (
        ProjectConfig.load(path, require_current_combination=False)
        if path.exists()
        else None
    )
    config = ProjectConfig(
        state_dir.expanduser().resolve(),
        endpoint(hub_url, optional=True),
        endpoint(platform_url, optional=True),
        delivery_config.expanduser().resolve() if delivery_config else None,
        combination(),
        current_config.research_workspace if current_config is not None else None,
    )
    if path.exists() and not replace_config:
        current = ProjectConfig.load(path)
        if current != config:
            raise BoundaryError("project", "existing_config_differs_choose_new_config")
    for name in ("logs", "downloads"):
        (config.state_dir / name).mkdir(parents=True, exist_ok=True)
    if install:
        log = config.state_dir / "logs" / "setup.log"
        with log.open("ab") as output:
            for command in (
                ["uv", "sync", "--locked", "--extra", "cloud", "--inexact"],
                ["npm", "ci"],
            ):
                executable = shutil.which(command[0])
                if executable is None:
                    raise BoundaryError("setup", "bootstrap_tool_missing")
                result = subprocess.run(
                    [executable, *command[1:]],
                    cwd=ROOT,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                if result.returncode:
                    raise BoundaryError("setup", "locked_dependency_install_failed")
    atomic_json(path, config.to_dict())
    return {
        "schema": CONFIG_SCHEMA,
        "status": "configured",
        "installed": install,
        "hub_configured": bool(hub_url),
        "delivery_configured": delivery_config is not None,
        "next": "project doctor; project open",
    }


def tool_identity() -> dict[str, Any]:
    source = hashlib.sha256()
    paths = list((ROOT / "spireagent/workbench").glob("*.py")) + [
        path
        for path in (ROOT / "spireagent/console").glob("*")
        if path.suffix in {".py", ".css", ".js"}
    ]
    for path in sorted(paths):
        source.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    result: dict[str, Any] = {
        "workbench_sha256": source.hexdigest(),
        "uv_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "python": ".".join(map(str, sys.version_info[:3])),
    }
    try:
        result["source_revision"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        result["working_tree_clean"] = not subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        result["source_revision"] = None
        result["working_tree_clean"] = None
    return result


def _evidence_imports(recorded: set[Path], package: Path) -> tuple[bool, bool]:
    """Inspect origins without importing a possibly shadowed package initializer."""
    name = "sts2_platform_evidence"
    initializer = package / "__init__.py"

    def matches(spec: importlib.machinery.ModuleSpec | None, path: Path) -> bool:
        return bool(spec and spec.origin and Path(spec.origin).resolve() == path)

    # Inspect both the current process and a fresh lookup: an already imported
    # package must not conceal a later PYTHONPATH/cwd shadow from doctor.
    fresh = importlib.machinery.PathFinder.find_spec(name)
    current = importlib.util.find_spec(name)
    if initializer not in recorded or any(
        not matches(spec, initializer)
        or spec is None
        or spec.submodule_search_locations is None
        or [Path(p).resolve() for p in spec.submodule_search_locations] != [package]
        for spec in (fresh, current)
    ):
        return False, False
    loaded = sys.modules.get(name)
    if loaded is not None and (
        Path(getattr(loaded, "__file__", "")).resolve() != initializer
        or [Path(p).resolve() for p in getattr(loaded, "__path__", ())] != [package]
    ):
        return False, False

    entry_name = name + ".delivery_cli"
    entry = package / "delivery_cli.py"
    spec = importlib.machinery.PathFinder.find_spec(entry_name, [str(package)])
    loaded_entry = sys.modules.get(entry_name)
    if loaded_entry is not None and (
        not matches(getattr(loaded_entry, "__spec__", None), entry)
        or Path(getattr(loaded_entry, "__file__", "")).resolve() != entry
    ):
        return False, False
    if spec is None:
        return True, False  # Historical public versions can omit the delivery tool.
    verified_entry = entry in recorded and matches(spec, entry)
    return verified_entry, verified_entry


def evidence_identity(expected: str) -> dict[str, Any]:
    try:
        distribution = importlib.metadata.distribution("rsgcsg-sts2-platform-evidence")
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        actual = direct.get("vcs_info", {}).get("commit_id")
        matched = (
            direct.get("url") in REPOSITORY_URLS
            and actual == expected
            and direct.get("subdirectory") == "components/evidence"
        )
        files = distribution.files
        content = hashlib.sha256()
        recorded: set[Path] = set()
        verified = 0
        if matched and files:
            for entry in sorted(files, key=str):
                if entry.hash is None:
                    continue
                path = Path(str(distribution.locate_file(entry)))
                if path.is_symlink() or not path.is_file():
                    return {"status": "INSTALLED_BYTES_MISMATCH"}
                with path.open("rb") as handle:
                    actual_hash = hashlib.file_digest(handle, entry.hash.mode).digest()
                encoded = base64.urlsafe_b64encode(actual_hash).rstrip(b"=").decode()
                if encoded != entry.hash.value:
                    return {"status": "INSTALLED_BYTES_MISMATCH"}
                content.update(str(entry).encode() + b"\0" + actual_hash)
                recorded.add(path.resolve())
                verified += 1
        imports_match, delivery_verified = False, False
        if matched and verified:
            package = Path(str(distribution.locate_file("sts2_platform_evidence"))).resolve()
            imports_match, delivery_verified = _evidence_imports(recorded, package)
        return {
            "status": ("PASS" if imports_match else "IMPORT_ORIGIN_MISMATCH")
            if matched and verified
            else "PIN_MISMATCH",
            "source_revision": actual,
            "version": distribution.version,
            "installed_record_sha256": content.hexdigest() if verified else None,
            "delivery_entrypoint_verified": delivery_verified,
        }
    except (importlib.metadata.PackageNotFoundError, ImportError, OSError, ValueError, TypeError):
        return {"status": "NOT_INSTALLED"}


def dependency_checks(config: ProjectConfig) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "python": {"status": "PASS" if sys.version_info[:2] == (3, 11) else "UNSUPPORTED"},
        "evidence": evidence_identity(config.combination["evidence_source_revision"]),
    }
    try:
        raw = subprocess.check_output(["node", "--version"], text=True, stderr=subprocess.DEVNULL)
        match = re.fullmatch(r"v(\d+)\.\d+\.\d+\s*", raw)
        checks["node"] = {"status": "PASS" if match and int(match[1]) >= 20 else "UNSUPPORTED"}
    except (OSError, subprocess.SubprocessError):
        checks["node"] = {"status": "NOT_INSTALLED"}
    expected_names = {"@rsgcsg/sts2-connector-client", "@rsgcsg/sts2-host-runtime"}
    pins = config.combination["node_packages"]
    if not isinstance(pins, list) or {p.get("package") for p in pins} != expected_names:
        raise BoundaryError("project", "invalid_public_package_pins")
    for pin in pins:
        name = pin["package"]
        try:
            observed = validate_installed_package(
                ROOT / "node_modules" / name, pin, required_paths=("package.json",)
            )
            checks[name] = {"status": "PASS", "content_sha256": observed["package_content_sha256"]}
        except (OSError, ValueError, PackageIdentityError):
            checks[name] = {"status": "NOT_INSTALLED_OR_PIN_MISMATCH"}
    with (ROOT / "pyproject.toml").open("rb") as handle:
        deps = tomllib.load(handle)["project"]["dependencies"]
    pin = config.combination["evidence_source_revision"]
    checks["evidence_declared_pin"] = {
        "status": "PASS"
        if any(f"{url}@{pin}#" in dep for url in REPOSITORY_URLS for dep in deps)
        else "PIN_MISMATCH"
    }
    return checks


def credential_present(config: ProjectConfig) -> bool:
    from spireagent.workbench.identity import LocalIdentity

    return bool(LocalIdentity(config).device_token())


def doctor(config: ProjectConfig) -> dict[str, Any]:
    checks = dependency_checks(config)
    if config.delivery_config is not None:
        checks["delivery_config"] = {
            "status": "PASS" if config.delivery_config.is_file() else "NOT_FOUND"
        }
        verified = checks["evidence"].get("delivery_entrypoint_verified", False)
        checks["delivery_tool"] = {"status": "PASS" if verified else "NOT_VERIFIED"}
        if config.delivery_config.is_file() and verified:
            # Platform owns config, attestation, release/runtime and outbox checks.
            # Isolated invocation retains the installed public package boundary;
            # it neither imports sibling source nor initializes/enrolls an outbox.
            environment = dict(os.environ)
            from spireagent.workbench.identity import LocalIdentity

            device_token = LocalIdentity(config).device_token()
            if device_token:
                environment["STPD_HUB_TOKEN"] = device_token
            for name in ("STPD_HUB_ADMIN_TOKEN", "PYTHONPATH", "PYTHONHOME"):
                environment.pop(name, None)
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-m",
                        "sts2_platform_evidence.delivery_cli",
                        "doctor",
                        "--config",
                        str(config.delivery_config),
                    ],
                    env=environment,
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                observed = decode_json(result.stdout)
                if (
                    not isinstance(observed, dict)
                    or observed.get("schema") != "sts2.evidence/delivery-doctor-1"
                    or observed.get("status") not in {"PASS", "BLOCKED"}
                    or not isinstance(observed.get("checks"), dict)
                ):
                    raise ValueError("invalid_owner_preflight")
                ready = result.returncode == 0 and observed["status"] == "PASS"
                checks["delivery_preflight"] = {
                    "status": "PASS" if ready else "BLOCKED",
                    "owner_checks": observed["checks"],
                    "discovered_sessions": observed.get("discovered_sessions"),
                }
                owner_hub = observed.get("hub_url")
                checks["delivery_hub"] = {
                    "status": (
                        "NOT_CHECKED"
                        if owner_hub is None
                        else "PASS"
                        if owner_hub == config.hub_url
                        else "ENDPOINT_MISMATCH"
                    )
                }
            except (OSError, ValueError, BoundaryError, subprocess.SubprocessError):
                checks["delivery_preflight"] = {"status": "UNAVAILABLE"}
    return {
        "schema": "stpd/developer-doctor-v1",
        "status": "PASS" if all(v["status"] == "PASS" for v in checks.values()) else "BLOCKED",
        "checks": checks,
        "tool_identity": tool_identity(),
        "hub": "configured" if config.hub_url else "not_configured",
        "hub_credential": "configured" if credential_present(config) else "not_configured",
        "delivery": "configured" if config.delivery_config else "not_configured",
        "platform_runtime": "not_probed",
        "policy": "existing_adapter_only",
        "non_claims": ["native loaded identity", "cloud account qualification", "model quality"],
    }
