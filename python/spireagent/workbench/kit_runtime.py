"""Optional text Runtime identities carried by an approved developer kit."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes
from spireagent.package_identity import PackageIdentityError, validate_installed_package
from spireagent.workbench.runtime_install import (
    ARCHIVE_LIMIT,
    BUNDLED_LAYOUT,
    CONNECTOR_PACKAGE,
    RUNTIME_PACKAGE,
)

TEXT_RUNTIME_PROFILE = "text-runtime/profile.json"
TEXT_RUNTIME_ARCHIVE = "text-runtime/runtime.tgz"
TEXT_RUNTIME_DESTINATION = "python/.local/text-menu-runtime-v1.json"
TEXT_ARCHIVE_DESTINATION = "python/.local/text-menu-runtime-v1.tgz"
M2_RUNTIME_PROFILE = "m2-runtime/profile.json"
M2_RUNTIME_ARCHIVE = "m2-runtime/runtime.tgz"
M2_RUNTIME_DESTINATION = "python/.local/text-menu-m2-runtime-v1.json"
M2_ARCHIVE_DESTINATION = "python/.local/text-menu-m2-runtime-v1.tgz"
V2_M2_RUNTIME_PROFILE = "m2-v2-runtime/profile.json"
V2_M2_RUNTIME_ARCHIVE = "m2-v2-runtime/runtime.tgz"
V2_M2_RUNTIME_DESTINATION = "python/.local/text-menu-m2-runtime-v2.json"
V2_M2_ARCHIVE_DESTINATION = "python/.local/text-menu-m2-runtime-v2.tgz"
PRIVATE_HOST_PROFILE = "private-host-runtime/profile.json"
PRIVATE_HOST_ARCHIVE = "private-host-runtime/runtime.tgz"
PRIVATE_HOST_PIN_DESTINATION = "python/.local/private-host-runtime/host-package-pin.json"
PRIVATE_HOST_PROFILE_DESTINATION = "python/.local/private-host-runtime/profile.json"
PRIVATE_HOST_ARCHIVE_DESTINATION = "python/.local/private-host-runtime/runtime.tgz"
PRIVATE_HOST_PACKAGE_DESTINATION = "python/.local/private-host-runtime/package"
PRIVATE_HOST_MANIFEST_KEY = "private_host_runtime"
PRIVATE_HOST_SCHEMA = "stpd/private-host-runtime-kit-v1"
HOST_PACKAGE = "@rsgcsg/sts2-host-runtime"
HOST_ARCHIVE_LIMIT = 64 * 1024 * 1024
HOST_REQUIRED_PATHS = (
    "consumers/python/sts2_headless/__init__.py",
    "consumers/python/sts2_headless/client.py",
    "tools/managed-pe-driver.mjs",
    "tools/managed-exact.mjs",
)
HOST_PIN_FIELDS = frozenset({
    "schema", "package", "version", "source_revision", "component_tree_revision",
    "release_asset_sha256", "package_content_sha256",
})

# The selected release carries only these fixed, independently inventoried pairs.
# (profile, archive, staged profile, staged archive, manifest key, schema)
KIT_RUNTIME_PAIRS = {
    "text-menu-v1": (TEXT_RUNTIME_PROFILE, TEXT_RUNTIME_ARCHIVE,
                     TEXT_RUNTIME_DESTINATION, TEXT_ARCHIVE_DESTINATION,
                     "text_runtime", "stpd/local-text-runtime-v1"),
    "text-menu-m2-v1": (M2_RUNTIME_PROFILE, M2_RUNTIME_ARCHIVE,
                        M2_RUNTIME_DESTINATION, M2_ARCHIVE_DESTINATION,
                        "m2_runtime", "stpd/local-text-m2-runtime-v1"),
    "text-menu-m2-v2": (V2_M2_RUNTIME_PROFILE, V2_M2_RUNTIME_ARCHIVE,
                        V2_M2_RUNTIME_DESTINATION, V2_M2_ARCHIVE_DESTINATION,
                        "m2_v2_runtime", "stpd/local-text-m2-runtime-v2"),
}


def private_host_files(manifest: dict[str, Any], files: dict[str, bytes], bom_raw: bytes
                       ) -> dict[str, Any] | None:
    """Validate the optional fixed private Host profile/archive group."""
    if PRIVATE_HOST_MANIFEST_KEY not in manifest:
        if PRIVATE_HOST_PROFILE in files or PRIVATE_HOST_ARCHIVE in files:
            raise BoundaryError("developer_kit", "private_host_inventory_incomplete")
        return None
    identity = manifest[PRIVATE_HOST_MANIFEST_KEY]
    if (not isinstance(identity, dict)
            or set(identity) != {"profile_sha256", "archive_sha256"}
            or PRIVATE_HOST_PROFILE not in files or PRIVATE_HOST_ARCHIVE not in files):
        raise BoundaryError("developer_kit", "private_host_inventory_incomplete")
    for field, name in (("profile_sha256", PRIVATE_HOST_PROFILE),
                        ("archive_sha256", PRIVATE_HOST_ARCHIVE)):
        digest(identity.get(field), "developer_kit.private_host_identity")
        if identity[field] != hashlib.sha256(files[name]).hexdigest():
            raise BoundaryError("developer_kit", "private_host_inventory_mismatch")
    return private_host_runtime_pin(
        files[PRIVATE_HOST_PROFILE], files[PRIVATE_HOST_ARCHIVE], bom_raw
    )


def private_host_runtime_pin(profile_raw: bytes, archive_raw: bytes,
                             bom_raw: bytes) -> dict[str, Any]:
    """Bind a closed private Host archive to the selected BOM components."""
    if len(archive_raw) > HOST_ARCHIVE_LIMIT:
        raise BoundaryError("developer_kit", "private_host_archive_too_large")
    profile = decode_json(profile_raw)
    if (not isinstance(profile, dict)
            or set(profile) != {"schema", "distribution", "host_runtime",
                                "component_source_digest_sha256", "dependency_layout",
                                "bundled_connector_pin"}
            or profile.get("schema") != PRIVATE_HOST_SCHEMA
            or profile.get("distribution") != "private_kit_candidate"
            or profile.get("dependency_layout") != BUNDLED_LAYOUT):
        raise BoundaryError("developer_kit", "private_host_profile_invalid")
    host = profile.get("host_runtime")
    if not isinstance(host, dict) or set(host) != HOST_PIN_FIELDS:
        raise BoundaryError("developer_kit", "private_host_profile_invalid")
    if (host.get("schema") != "stpd/platform-host-runtime-pin-v1"
            or host.get("package") != HOST_PACKAGE
            or not isinstance(host.get("version"), str)
            or re.fullmatch(r"1\.1\.0-rc\.(\d+)", host["version"]) is None
            or int(host["version"].rsplit(".", 1)[1]) < 20):
        raise BoundaryError("developer_kit", "private_host_profile_invalid")
    for field, length in (("source_revision", 40), ("component_tree_revision", 40),
                          ("release_asset_sha256", 64), ("package_content_sha256", 64)):
        digest(host.get(field), "developer_kit.private_host." + field, length=length)
    digest(profile.get("component_source_digest_sha256"),
           "developer_kit.private_host.component_source_digest")
    if host["release_asset_sha256"] != hashlib.sha256(archive_raw).hexdigest():
        raise BoundaryError("developer_kit", "private_host_archive_checksum_mismatch")

    bom = decode_json(bom_raw)
    components = bom.get("components") if isinstance(bom, dict) else None
    host_component = components.get("host_runtime") if isinstance(components, dict) else None
    connector_component = components.get("connector") if isinstance(components, dict) else None
    sdk_version = components.get("typescript_sdk") if isinstance(components, dict) else None
    if (not isinstance(host_component, dict) or not isinstance(connector_component, dict)
            or not isinstance(sdk_version, str)
            or re.fullmatch(r"[0-9A-Za-z.+-]{1,80}", sdk_version) is None):
        raise BoundaryError("developer_kit", "private_host_bom_identity_missing")
    host_fields = {
        "version": "version", "source_revision": "source_revision",
        "component_tree_revision": "component_tree_revision",
    }
    if any(host.get(profile_field) != host_component.get(bom_field)
           for profile_field, bom_field in host_fields.items()) or (
        profile["component_source_digest_sha256"]
        != host_component.get("component_source_digest_sha256")
    ):
        raise BoundaryError("developer_kit", "private_host_bom_identity_mismatch")
    for field in ("source_revision", "component_tree_revision"):
        digest(connector_component.get(field), "developer_kit.private_host.connector_" + field,
               length=40)
    digest(connector_component.get("component_source_digest_sha256"),
           "developer_kit.private_host.connector_source_digest")

    connector = profile.get("bundled_connector_pin")
    connector_fields = {"package", "version", "source_revision", "component_tree_revision",
                        "component_source_digest_sha256", "bundle_sha256", "transitive_zod"}
    if not isinstance(connector, dict) or set(connector) != connector_fields:
        raise BoundaryError("developer_kit", "private_host_profile_invalid")
    if (connector.get("package") != CONNECTOR_PACKAGE
            or not isinstance(connector.get("version"), str)
            or not re.fullmatch(r"[0-9A-Za-z.+-]{1,80}", connector["version"])
            or connector.get("version") != sdk_version
            or connector.get("source_revision") != connector_component.get("source_revision")
            or connector.get("component_tree_revision") != connector_component.get(
                "component_tree_revision")
            or connector.get("component_source_digest_sha256") != connector_component.get(
                "component_source_digest_sha256")):
        raise BoundaryError("developer_kit", "private_host_connector_bom_identity_mismatch")
    digest(connector.get("bundle_sha256"), "developer_kit.private_host.connector_bundle")
    zod = connector.get("transitive_zod")
    if (not isinstance(zod, dict)
            or set(zod) != {"version", "url", "integrity", "bundle_sha256"}
            or not isinstance(zod.get("version"), str)
            or not re.fullmatch(r"\d+\.\d+\.\d+", zod["version"])
            or zod.get("url") != f"https://registry.npmjs.org/zod/-/zod-{zod['version']}.tgz"
            or not isinstance(zod.get("integrity"), str)
            or re.fullmatch(r"sha512-[A-Za-z0-9+/]+={0,2}", zod["integrity"]) is None):
        raise BoundaryError("developer_kit", "private_host_profile_invalid")
    digest(zod.get("bundle_sha256"), "developer_kit.private_host.zod_bundle")

    entries = _private_host_archive_entries(archive_raw)
    if _tree_sha256(entries) != host["package_content_sha256"]:
        raise BoundaryError("developer_kit", "private_host_content_mismatch")
    _validate_private_host_package(entries, host, connector, zod)
    return {
        "distribution": profile["distribution"],
        "host_runtime": dict(host),
        "component_source_digest_sha256": profile["component_source_digest_sha256"],
        "dependency_layout": profile["dependency_layout"],
        "bundled_connector_pin": dict(connector),
    }


def _private_host_archive_entries(archive_raw: bytes) -> dict[str, bytes]:
    return {name: raw for name, (raw, _mode) in _private_host_archive_records(archive_raw).items()}


def _private_host_archive_records(archive_raw: bytes) -> dict[str, tuple[bytes, int]]:
    entries: dict[str, tuple[bytes, int]] = {}
    total = 0
    count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_raw), mode="r|gz") as archive:
            for member in archive:
                count += 1
                if count > 8192:
                    raise ValueError
                name = member.name
                path = PurePosixPath(name)
                normalized = str(path)
                if (not name.startswith("package/") or path.is_absolute() or ".." in path.parts
                        or "\\" in name or ":" in name or normalized != name.rstrip("/")):
                    raise ValueError
                if member.isdir():
                    continue
                if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE} or name in entries:
                    raise ValueError
                total += member.size
                if member.size < 0 or total > HOST_ARCHIVE_LIMIT:
                    raise ValueError
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError
                raw = handle.read(member.size + 1)
                if len(raw) != member.size:
                    raise ValueError
                relative = name[len("package/"):]
                mode = 0o755 if member.mode & 0o111 else 0o644
                entries[relative] = (raw, mode)
    except (OSError, tarfile.TarError, ValueError, EOFError):
        raise BoundaryError("developer_kit", "private_host_archive_unsafe_or_invalid") from None
    if not entries:
        raise BoundaryError("developer_kit", "private_host_archive_invalid")
    return entries


def _tree_sha256(files: dict[str, bytes]) -> str:
    digestor = hashlib.sha256()
    for name, raw in sorted(files.items()):
        digestor.update(name.encode("utf-8"))
        digestor.update(b"\0")
        digestor.update(raw)
        digestor.update(b"\0")
    return digestor.hexdigest()


def _subtree_sha256(files: dict[str, bytes], root: str) -> str:
    prefix = root.rstrip("/") + "/"
    selected = {name[len(prefix):]: raw for name, raw in files.items()
                if name.startswith(prefix)}
    if not selected:
        raise BoundaryError("developer_kit", "private_host_bundle_missing")
    return _tree_sha256(selected)


def _json_entry(files: dict[str, bytes], name: str) -> dict[str, Any]:
    value = decode_json(files.get(name, b""))
    if not isinstance(value, dict):
        raise BoundaryError("developer_kit", "private_host_package_metadata_invalid")
    return value


def _validate_private_host_package(files: dict[str, bytes], host: dict[str, Any],
                                   connector: dict[str, Any], zod: dict[str, Any]) -> None:
    required = {"package.json", "npm-shrinkwrap.json", *HOST_REQUIRED_PATHS}
    if not required.issubset(files):
        raise BoundaryError("developer_kit", "private_host_package_files_missing")
    package = _json_entry(files, "package.json")
    sdk_name = CONNECTOR_PACKAGE
    dependencies = {sdk_name: connector["version"], "zod": zod["version"]}
    if (package.get("name") != HOST_PACKAGE or package.get("version") != host["version"]
            or package.get("dependencies") != dependencies
            or package.get("bundleDependencies") != [sdk_name, "zod"]):
        raise BoundaryError("developer_kit", "private_host_dependency_closure_invalid")
    sdk_root = f"node_modules/{sdk_name}"
    sdk_package = _json_entry(files, sdk_root + "/package.json")
    zod_package = _json_entry(files, "node_modules/zod/package.json")
    if (sdk_package.get("name") != sdk_name or sdk_package.get("version") != connector["version"]
            or sdk_package.get("dependencies") != {"zod": f"^{zod['version']}"}
            or zod_package.get("name") != "zod" or zod_package.get("version") != zod["version"]):
        raise BoundaryError("developer_kit", "private_host_dependency_closure_invalid")
    if (_subtree_sha256(files, sdk_root) != connector["bundle_sha256"]
            or _subtree_sha256(files, "node_modules/zod") != zod["bundle_sha256"]):
        raise BoundaryError("developer_kit", "private_host_bundle_hash_mismatch")
    shrinkwrap = _json_entry(files, "npm-shrinkwrap.json")
    packages = shrinkwrap.get("packages")
    if not isinstance(packages, dict):
        raise BoundaryError("developer_kit", "private_host_shrinkwrap_invalid")
    sdk_lock = packages.get(sdk_root)
    zod_lock = packages.get("node_modules/zod")
    root_lock = packages.get("")
    if not all(isinstance(item, dict) for item in (root_lock, sdk_lock, zod_lock)):
        raise BoundaryError("developer_kit", "private_host_shrinkwrap_invalid")
    assert isinstance(root_lock, dict)
    assert isinstance(sdk_lock, dict)
    assert isinstance(zod_lock, dict)
    if (shrinkwrap.get("lockfileVersion") != 3
            or root_lock.get("dependencies") != dependencies
            or root_lock.get("bundleDependencies") != [sdk_name, "zod"]
            or sdk_lock.get("version") != connector["version"]
            or sdk_lock.get("inBundle") is not True
            or sdk_lock.get("dependencies") != {"zod": f"^{zod['version']}"}
            or zod_lock.get("version") != zod["version"]
            or zod_lock.get("inBundle") is not True
            or zod_lock.get("resolved") != zod["url"]
            or zod_lock.get("integrity") != zod["integrity"]):
        raise BoundaryError("developer_kit", "private_host_shrinkwrap_invalid")


def verify_private_host_offline_install(profile_raw: bytes, archive_raw: bytes,
                                        bom_raw: bytes) -> None:
    """Prove the fixed archive installs/imports with isolated npm config and no network."""
    identity = private_host_runtime_pin(profile_raw, archive_raw, bom_raw)
    npm = shutil.which("npm")
    node = shutil.which("node")
    if npm is None or node is None:
        raise BoundaryError("developer_kit", "private_host_node_tooling_missing")
    with tempfile.TemporaryDirectory(prefix=".kit-private-host-offline-") as temporary:
        stage = Path(temporary)
        archive_path = stage / "runtime.tgz"
        archive_path.write_bytes(archive_raw)
        (stage / "package.json").write_bytes(json_bytes({
            "name": "stpd-private-host-offline-check", "version": "0.0.0",
            "private": True, "dependencies": {HOST_PACKAGE: "file:runtime.tgz"},
        }))
        user_config = stage / "user.npmrc"
        global_config = stage / "global.npmrc"
        user_config.touch(mode=0o600)
        global_config.touch(mode=0o600)
        cache = stage / "empty-cache"
        environment = {key: value for key, value in os.environ.items()
                       if key in {"PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP"}}
        environment.update({
            "HOME": str(stage / "home"), "USERPROFILE": str(stage / "home"),
            "NPM_CONFIG_USERCONFIG": str(user_config),
            "NPM_CONFIG_GLOBALCONFIG": str(global_config),
            "NPM_CONFIG_CACHE": str(cache), "NPM_CONFIG_OFFLINE": "true",
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        })
        Path(environment["HOME"]).mkdir()
        result = subprocess.run(
            [npm, "install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=stage, env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=300, check=False,
        )
        if result.returncode != 0:
            raise BoundaryError("developer_kit", "private_host_offline_install_failed")
        installed = stage / "node_modules" / HOST_PACKAGE
        try:
            observed = validate_installed_package(
                installed, identity["host_runtime"], required_paths=HOST_REQUIRED_PATHS
            )
        except (OSError, ValueError, PackageIdentityError):
            raise BoundaryError(
                "developer_kit", "private_host_offline_install_identity_mismatch"
            ) from None
        if observed["package_content_sha256"] != identity["host_runtime"]["package_content_sha256"]:
            raise BoundaryError("developer_kit", "private_host_offline_install_identity_mismatch")
        check = subprocess.run(
            [node, "--input-type=module", "--eval",
             "import * as host from '@rsgcsg/sts2-host-runtime';"
             "import * as sdk from '@rsgcsg/sts2-connector-client';"
             "import 'zod'; if (!Object.keys(host).length || !Object.keys(sdk).length)"
             " process.exit(1);"],
            cwd=installed, env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30, check=False,
        )
        if check.returncode != 0:
            raise BoundaryError("developer_kit", "private_host_offline_import_failed")


def validate_private_host_package_directory(package_root: Path,
                                            identity: dict[str, Any]) -> str:
    """Validate the staged immutable Host package and its bundled SDK/Zod files."""
    if package_root.is_symlink() or not package_root.is_dir():
        raise BoundaryError("developer_kit", "private_host_installed_package_missing")
    entries: dict[str, bytes] = {}
    for path in package_root.rglob("*"):
        mode = path.lstat().st_mode
        relative = path.relative_to(package_root).as_posix()
        if path.is_symlink():
            raise BoundaryError("developer_kit", "private_host_installed_package_unsafe")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise BoundaryError("developer_kit", "private_host_installed_package_unsafe")
        entries[relative] = path.read_bytes()
    pin = identity.get("host_runtime")
    connector = identity.get("bundled_connector_pin")
    if not isinstance(pin, dict) or not isinstance(connector, dict):
        raise BoundaryError("developer_kit", "private_host_installed_package_changed")
    zod = connector.get("transitive_zod")
    expected_content = pin.get("package_content_sha256")
    if (not isinstance(zod, dict) or not isinstance(expected_content, str)
            or _tree_sha256(entries) != expected_content):
        raise BoundaryError("developer_kit", "private_host_installed_package_changed")
    _validate_private_host_package(entries, pin, connector, zod)
    return expected_content


def stage_private_host_runtime(profile_raw: bytes, archive_raw: bytes,
                               bom_raw: bytes, source_root: Path) -> None:
    """Atomically unpack the verified npm package into the fixed private kit path."""
    identity = private_host_runtime_pin(profile_raw, archive_raw, bom_raw)
    entries = _private_host_archive_records(archive_raw)
    target = source_root / PRIVATE_HOST_PACKAGE_DESTINATION
    parent = target.parent
    _safe_create_directories(parent, source_root)
    if target.is_symlink():
        raise BoundaryError("developer_kit", "private_host_staging_path_unsafe")
    if target.exists():
        try:
            validate_private_host_package_directory(target, identity)
        except (OSError, ValueError, PackageIdentityError):
            raise BoundaryError("developer_kit", "private_host_staging_collision") from None
        return
    with tempfile.TemporaryDirectory(prefix=".private-host-stage-", dir=parent) as temp:
        package_root = Path(temp) / "package"
        package_root.mkdir()
        for name, (raw, mode) in entries.items():
            destination = package_root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(raw)
            destination.chmod(mode)
        try:
            validate_private_host_package_directory(package_root, identity)
        except (OSError, ValueError, PackageIdentityError):
            raise BoundaryError("developer_kit", "private_host_staging_identity_mismatch") from None
        package_root.rename(target)


def _safe_create_directories(path: Path, trusted_root: Path) -> None:
    try:
        relative = path.relative_to(trusted_root)
    except ValueError:
        raise BoundaryError("developer_kit", "private_host_staging_path_unsafe") from None
    current = trusted_root
    if current.is_symlink() or not current.is_dir():
        raise BoundaryError("developer_kit", "private_host_staging_path_unsafe")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise BoundaryError("developer_kit", "private_host_staging_path_unsafe")
        current.mkdir(exist_ok=True)
        if not current.is_dir():
            raise BoundaryError("developer_kit", "private_host_staging_path_unsafe")


def text_runtime_pin(profile_raw: bytes, archive_raw: bytes, *,
                     memory: bool = False,
                     required_profile: str | None = None) -> dict[str, Any]:
    """Check the externally approved profile against inventoried archive bytes."""
    if len(archive_raw) > ARCHIVE_LIMIT:
        raise BoundaryError("developer_kit", "text_runtime_archive_too_large")
    pin = strict_text_runtime_profile(profile_raw, memory=memory,
                                      required_profile=required_profile)
    if pin["release_asset_sha256"] != hashlib.sha256(archive_raw).hexdigest():
        raise BoundaryError("developer_kit", "text_runtime_archive_checksum_mismatch")
    return pin


def strict_text_runtime_profile(profile_raw: bytes, *, memory: bool = False,
                                required_profile: str | None = None) -> dict[str, Any]:
    """Validate all kit profile fields when archived package bytes are unavailable."""
    profile = decode_json(profile_raw)
    if not isinstance(profile, dict) or set(profile) != {"schema", "runtime_package"}:
        raise BoundaryError("developer_kit", "text_runtime_profile_invalid")
    pin = profile["runtime_package"]
    expected_schema = (KIT_RUNTIME_PAIRS[required_profile][5] if required_profile is not None
                       else ("stpd/local-text-m2-runtime-v1" if memory
                             else "stpd/local-text-runtime-v1"))
    if (profile["schema"] != expected_schema
            or not isinstance(pin, dict)
            or set(pin) != {"package", "version", "source_revision",
                            "component_tree_revision", "release_asset_sha256",
                            "package_content_sha256", "dependency_layout",
                            "bundled_connector_pin"}
            or pin.get("package") != RUNTIME_PACKAGE
            or pin.get("dependency_layout") != BUNDLED_LAYOUT):
        raise BoundaryError("developer_kit", "text_runtime_profile_invalid")
    for field in ("source_revision", "component_tree_revision"):
        digest(pin.get(field), "developer_kit.text_runtime_source", length=40)
    digest(pin.get("package_content_sha256"), "developer_kit.text_runtime_content")
    if (not isinstance(pin.get("version"), str)
            or not re.fullmatch(r"[0-9A-Za-z.+-]{1,80}", pin["version"])
            or not isinstance(pin.get("bundled_connector_pin"), dict)):
        raise BoundaryError("developer_kit", "text_runtime_profile_invalid")
    digest(pin.get("release_asset_sha256"), "developer_kit.text_runtime_archive")
    return pin
