"""Optional text Runtime identities carried by an approved developer kit."""

from __future__ import annotations

import hashlib
import io
import json
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
from spireagent.source import source_identity
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
PRIVATE_HOST_DERIVED_SCHEMA = "stpd/private-host-runtime-kit-v2"
PRIVATE_HOST_DERIVATION_SCHEMA = "stpd/private-host-runtime-derivation-v1"
PRIVATE_HOST_DERIVATION_FILE = "private-host-derivation.json"
PRIVATE_HOST_DERIVATION_RECIPE = "kit-boundary-bundled-closure-v1"
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


def private_host_files(manifest: dict[str, Any], files: dict[str, bytes], bom_raw: bytes,
                       *, source_root: Path | None = None) -> dict[str, Any] | None:
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
        files[PRIVATE_HOST_PROFILE], files[PRIVATE_HOST_ARCHIVE], bom_raw,
        source_root=source_root,
    )


def private_host_runtime_pin(profile_raw: bytes, archive_raw: bytes,
                             bom_raw: bytes, *, source_root: Path | None = None
                             ) -> dict[str, Any]:
    """Bind a closed private Host archive to the selected BOM components."""
    if len(archive_raw) > HOST_ARCHIVE_LIMIT:
        raise BoundaryError("developer_kit", "private_host_archive_too_large")
    profile = decode_json(profile_raw)
    common_fields = {"distribution", "host_runtime", "component_source_digest_sha256",
                     "dependency_layout", "bundled_connector_pin"}
    derived = isinstance(profile, dict) and profile.get("schema") == PRIVATE_HOST_DERIVED_SCHEMA
    expected_fields = common_fields | ({"schema", "derivation"} if derived else {"schema"})
    if (not isinstance(profile, dict)
            or set(profile) != expected_fields
            or profile.get("schema") not in {PRIVATE_HOST_SCHEMA, PRIVATE_HOST_DERIVED_SCHEMA}
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
    if derived:
        derivation = profile.get("derivation")
        host_source = derivation.get("host_source") if isinstance(derivation, dict) else None
        selected_sdk = derivation.get("selected_sdk") if isinstance(derivation, dict) else None
        if (not isinstance(host_source, dict) or not isinstance(selected_sdk, dict)
                or host_source.get("version") != host_component.get("version")
                or host_source.get("source_revision") != host_component.get("source_revision")
                or host_source.get("component_tree_revision")
                != host_component.get("component_tree_revision")
                or host_source.get("component_source_digest_sha256")
                != host_component.get("component_source_digest_sha256")
                or selected_sdk.get("component_version") != connector_component.get("version")):
            raise BoundaryError("developer_kit", "private_host_derivation_bom_mismatch")
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
            or re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", zod["integrity"]) is None):
        raise BoundaryError("developer_kit", "private_host_profile_invalid")
    digest(zod.get("bundle_sha256"), "developer_kit.private_host.zod_bundle")

    records = _private_host_archive_records(archive_raw)
    entries = {name: raw for name, (raw, _mode) in records.items()}
    if _tree_sha256(entries) != host["package_content_sha256"]:
        raise BoundaryError("developer_kit", "private_host_content_mismatch")
    derivation = profile.get("derivation") if derived else None
    if derived and (not isinstance(derivation, dict)
                   or derivation.get("schema") != PRIVATE_HOST_DERIVATION_SCHEMA
                   or derivation.get("recipe") != PRIVATE_HOST_DERIVATION_RECIPE):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    _validate_private_host_package(entries, host, connector, zod,
                                   derivation=derivation, records=records)
    result = {
        "distribution": profile["distribution"],
        "host_runtime": dict(host),
        "component_source_digest_sha256": profile["component_source_digest_sha256"],
        "dependency_layout": profile["dependency_layout"],
        "bundled_connector_pin": dict(connector),
    }
    if derived:
        result["derivation"] = derivation
    if source_root is not None and derived:
        verify_private_host_source_binding(profile_raw, archive_raw, bom_raw, source_root)
    return result


def _private_host_archive_entries(archive_raw: bytes) -> dict[str, bytes]:
    return {name: raw for name, (raw, _mode) in _private_host_archive_records(archive_raw).items()}


def _private_host_archive_records(archive_raw: bytes) -> dict[str, tuple[bytes, int]]:
    entries: dict[str, tuple[bytes, int]] = {}
    directories: set[str] = set()
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
                # Package identity uses exact normalized POSIX spelling across hosts.
                # Do not apply the target OS's normcase/casefold rules here.
                relative = normalized[len("package/"):]
                parts = PurePosixPath(relative).parts
                parents = ["/".join(parts[:index]) for index in range(1, len(parts))]
                if member.isdir():
                    if relative in entries or any(parent in entries for parent in parents):
                        raise ValueError
                    directories.add(relative)
                    directories.update(parents)
                    continue
                if (member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}
                        or not relative or relative in entries or relative in directories
                        or any(parent in entries for parent in parents)):
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
                mode = 0o755 if member.mode & 0o111 else 0o644
                entries[relative] = (raw, mode)
                directories.update(parents)
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
                                   connector: dict[str, Any], zod: dict[str, Any], *,
                                   derivation: dict[str, Any] | None = None,
                                   records: dict[str, tuple[bytes, int]] | None = None) -> None:
    required = {"package.json", "npm-shrinkwrap.json", *HOST_REQUIRED_PATHS}
    if derivation is not None:
        required.update({PRIVATE_HOST_DERIVATION_FILE, "src/project-identity.mjs"})
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
    if derivation is not None:
        _validate_private_host_derivation(files, package, connector, zod, derivation, records)
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


def _safe_inventory_path(value: object) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or "\\" in value or ":" in value):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    return value


def _validate_private_host_derivation(
    files: dict[str, bytes], package: dict[str, Any], connector: dict[str, Any],
    zod: dict[str, Any], derivation: dict[str, Any],
    records: dict[str, tuple[bytes, int]] | None,
) -> None:
    expected_fields = {"schema", "recipe", "source_archive", "host_source",
                       "manifest_transform", "selected_sdk", "zod", "producer", "bundles"}
    if (set(derivation) != expected_fields
            or derivation.get("schema") != PRIVATE_HOST_DERIVATION_SCHEMA
            or derivation.get("recipe") != PRIVATE_HOST_DERIVATION_RECIPE):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    source_archive = derivation.get("source_archive")
    host_source = derivation.get("host_source")
    transform = derivation.get("manifest_transform")
    selected_sdk = derivation.get("selected_sdk")
    zod_identity = derivation.get("zod")
    producer = derivation.get("producer")
    bundles = derivation.get("bundles")
    if (not all(isinstance(item, dict) for item in
                (source_archive, host_source, transform, selected_sdk,
                 zod_identity, producer, bundles))):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    assert isinstance(source_archive, dict)
    assert isinstance(host_source, dict)
    assert isinstance(transform, dict)
    assert isinstance(selected_sdk, dict)
    assert isinstance(zod_identity, dict)
    assert isinstance(producer, dict)
    assert isinstance(bundles, dict)
    if (set(source_archive) != {"package", "version", "filename", "archive_sha256",
                                "package_content_sha256", "files", "package_json_utf8"}
            or source_archive.get("package") != HOST_PACKAGE
            or source_archive.get("version") != host_source.get("version")
            or source_archive.get("filename") != (
                f"rsgcsg-sts2-host-runtime-{source_archive.get('version')}.tgz")):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    digest(source_archive.get("archive_sha256"), "developer_kit.private_host.source_archive")
    digest(source_archive.get("package_content_sha256"),
           "developer_kit.private_host.source_content")
    digest(host_source.get("source_revision"), "developer_kit.private_host.source_revision",
           length=40)
    digest(host_source.get("component_tree_revision"),
           "developer_kit.private_host.source_tree", length=40)
    digest(host_source.get("component_source_digest_sha256"),
           "developer_kit.private_host.source_digest")
    digest(selected_sdk.get("source_revision"), "developer_kit.private_host.sdk_source",
           length=40)
    digest(selected_sdk.get("component_tree_revision"), "developer_kit.private_host.sdk_tree",
           length=40)
    digest(selected_sdk.get("component_source_digest_sha256"),
           "developer_kit.private_host.sdk_digest")
    digest(selected_sdk.get("bundle_sha256"), "developer_kit.private_host.sdk_bundle")
    if (set(host_source) != {"version", "source_revision", "component_tree_revision",
                            "component_source_digest_sha256", "source_file_count"}
            or type(host_source.get("source_file_count")) is not int
            or host_source["source_file_count"] < 1
            or set(selected_sdk) != {"package", "version", "source_revision",
                                     "component_version", "component_tree_revision",
                                     "component_source_digest_sha256", "bundle_sha256"}
            or selected_sdk.get("package") != CONNECTOR_PACKAGE
            or selected_sdk.get("version") != connector.get("version")
            or selected_sdk.get("source_revision") != connector.get("source_revision")
            or selected_sdk.get("component_tree_revision") != connector.get(
                "component_tree_revision")
            or selected_sdk.get("component_source_digest_sha256") != connector.get(
                "component_source_digest_sha256")
            or selected_sdk.get("bundle_sha256") != connector.get("bundle_sha256")
            or set(zod_identity) != {"version", "url", "integrity", "bundle_sha256"}
            or zod_identity != {**zod, "bundle_sha256": connector.get("transitive_zod", {}).get(
                "bundle_sha256")}):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    if (not isinstance(producer.get("workspace_revision"), str)
            or re.fullmatch(r"[0-9a-f]{40}", producer["workspace_revision"]) is None
            or set(producer) != {"workspace_revision", "tool_files", "node_version",
                                 "npm_version", "typescript_version", "typescript_integrity"}
            or not isinstance(producer.get("tool_files"), dict)
            or set(producer["tool_files"]) != {
                "python/tools/package_developer_kit.py",
                "python/spireagent/workbench/kit_runtime.py"}
            or not isinstance(producer.get("node_version"), str)
            or re.fullmatch(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?",
                            producer["node_version"]) is None
            or not isinstance(producer.get("npm_version"), str)
            or re.fullmatch(r"\d+\.\d+\.\d+", producer["npm_version"]) is None
            or not isinstance(producer.get("typescript_version"), str)
            or re.fullmatch(r"\d+\.\d+\.\d+", producer["typescript_version"]) is None):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    if (not isinstance(producer.get("typescript_integrity"), str)
            or re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", producer["typescript_integrity"])
            is None):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    for identity in producer["tool_files"].values():
        if (not isinstance(identity, dict) or set(identity) != {"git_blob_sha1", "sha256"}):
            raise BoundaryError("developer_kit", "private_host_derivation_invalid")
        digest(identity.get("git_blob_sha1"), "developer_kit.private_host.tool_blob", length=40)
        digest(identity.get("sha256"), "developer_kit.private_host.tool_sha256")

    original_manifest_raw = source_archive.get("package_json_utf8")
    source_files = source_archive.get("files")
    if not isinstance(original_manifest_raw, str) or not isinstance(source_files, dict):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    try:
        original_manifest_bytes = original_manifest_raw.encode("utf-8")
    except UnicodeEncodeError:
        raise BoundaryError("developer_kit", "private_host_derivation_invalid") from None
    if len(original_manifest_bytes) > 256 * 1024:
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    original_manifest = decode_json(original_manifest_bytes)
    if not isinstance(original_manifest, dict):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    if (not source_files or "package.json" not in source_files
            or "package-lock.json" in source_files
            or len(source_files) > 8192):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    source_entries: dict[str, bytes] = {}
    for name, row in source_files.items():
        _safe_inventory_path(name)
        if (not isinstance(row, dict) or set(row) != {"sha256", "bytes", "mode"}
                or type(row.get("bytes")) is not int or row["bytes"] < 0
                or type(row.get("mode")) is not int
                or row.get("mode") not in {0o644, 0o755}):
            raise BoundaryError("developer_kit", "private_host_derivation_invalid")
        digest(row.get("sha256"), "developer_kit.private_host.source_file")
        if name == "package.json":
            raw = original_manifest_bytes
        else:
            source_raw = files.get(name)
            if source_raw is None:
                raise BoundaryError("developer_kit", "private_host_source_content_mismatch")
            raw = source_raw
        if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise BoundaryError("developer_kit", "private_host_source_content_mismatch")
        if records is not None and name in records and records[name][1] != row["mode"]:
            raise BoundaryError("developer_kit", "private_host_source_content_mismatch")
        source_entries[name] = raw
    if (_tree_sha256(source_entries) != source_archive.get("package_content_sha256")
            or hashlib.sha256(original_manifest_bytes).hexdigest()
            != transform.get("source_package_json_sha256")):
        raise BoundaryError("developer_kit", "private_host_source_content_mismatch")
    source_dependencies = original_manifest.get("dependencies")
    derived_dependencies = {CONNECTOR_PACKAGE: connector.get("version"),
                            "zod": zod.get("version")}
    if (source_dependencies != transform.get("source_dependencies")
            or transform.get("derived_dependencies") != derived_dependencies
            or transform.get("changed_fields") != ["bundleDependencies", "dependencies", "files"]
            or transform.get("added_files") != ["npm-shrinkwrap.json",
                                                 PRIVATE_HOST_DERIVATION_FILE]
            or transform.get("derived_package_json_sha256")
            != hashlib.sha256(files["package.json"]).hexdigest()):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    expected_manifest = dict(original_manifest)
    expected_manifest["dependencies"] = derived_dependencies
    expected_manifest["bundleDependencies"] = [CONNECTOR_PACKAGE, "zod"]
    source_allowed_files = original_manifest.get("files")
    if (not isinstance(source_allowed_files, list)
            or any(not isinstance(item, str) for item in source_allowed_files)):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    expected_manifest["files"] = list(dict.fromkeys([
        *source_allowed_files, "npm-shrinkwrap.json", PRIVATE_HOST_DERIVATION_FILE]))
    if package != expected_manifest:
        raise BoundaryError("developer_kit", "private_host_manifest_transform_invalid")

    if (set(bundles) != {"connector", "zod"}
            or not isinstance(bundles.get("connector"), dict)
            or not isinstance(bundles.get("zod"), dict)):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    expected_files = set(source_files) | {"npm-shrinkwrap.json", PRIVATE_HOST_DERIVATION_FILE}
    for key, root in (("connector", f"node_modules/{CONNECTOR_PACKAGE}"),
                      ("zod", "node_modules/zod")):
        bundle = bundles[key]
        if set(bundle) != {"files"} or not isinstance(bundle.get("files"), dict):
            raise BoundaryError("developer_kit", "private_host_derivation_invalid")
        if not bundle["files"]:
            raise BoundaryError("developer_kit", "private_host_derivation_invalid")
        for relative, row in bundle["files"].items():
            _safe_inventory_path(relative)
            if (not isinstance(row, dict) or set(row) != {"sha256", "bytes", "mode"}
                    or type(row.get("bytes")) is not int or row["bytes"] < 0
                    or type(row.get("mode")) is not int
                    or row.get("mode") not in {0o644, 0o755}):
                raise BoundaryError("developer_kit", "private_host_derivation_invalid")
            digest(row.get("sha256"), "developer_kit.private_host.bundle_file")
            name = f"{root}/{relative}"
            expected_files.add(name)
            bundle_raw = files.get(name)
            if (bundle_raw is None or len(bundle_raw) != row["bytes"]
                    or hashlib.sha256(bundle_raw).hexdigest() != row["sha256"]):
                raise BoundaryError("developer_kit", "private_host_bundle_hash_mismatch")
            if records is not None and records[name][1] != row["mode"]:
                raise BoundaryError("developer_kit", "private_host_bundle_hash_mismatch")
    if set(files) != expected_files:
        raise BoundaryError("developer_kit", "private_host_unexpected_package_files")
    if decode_json(files[PRIVATE_HOST_DERIVATION_FILE]) != derivation:
        raise BoundaryError("developer_kit", "private_host_derivation_mismatch")


def _fresh_npm_environment(home: Path, *, offline: bool) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    user_config = home / "user.npmrc"
    global_config = home / "global.npmrc"
    user_config.touch(mode=0o600)
    global_config.touch(mode=0o600)
    cache = home / "cache"
    cache.mkdir()
    environment = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP"}}
    environment.update({
        "HOME": str(home / "home"), "USERPROFILE": str(home / "home"),
        "NPM_CONFIG_USERCONFIG": str(user_config),
        "NPM_CONFIG_GLOBALCONFIG": str(global_config),
        "NPM_CONFIG_CACHE": str(cache),
        "NPM_CONFIG_OFFLINE": "true" if offline else "false",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "NPM_CONFIG_AUDIT": "false",
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_IGNORE_SCRIPTS": "true",
    })
    Path(environment["HOME"]).mkdir()
    return environment


def _run_npm(npm: str, arguments: list[str], cwd: Path, environment: dict[str, str],
             failure_code: str, *, timeout: int = 900) -> str:
    try:
        result = subprocess.run(
            [npm, *arguments], cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BoundaryError("developer_kit", failure_code) from None
    if result.returncode != 0:
        raise BoundaryError("developer_kit", failure_code)
    return result.stdout


def _read_source_regular(root: Path, relative: str) -> tuple[bytes, int]:
    safe = _safe_inventory_path(relative)
    current = root
    for part in PurePosixPath(safe).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            raise BoundaryError("developer_kit", "private_host_source_file_missing") from None
        if stat.S_ISLNK(mode):
            raise BoundaryError("developer_kit", "private_host_source_file_unsafe")
    if not stat.S_ISREG(mode):
        raise BoundaryError("developer_kit", "private_host_source_file_unsafe")
    raw = current.read_bytes()
    file_mode = 0o755 if mode & 0o111 else 0o644
    return raw, file_mode


def _run_local(command: list[str], cwd: Path, environment: dict[str, str],
               failure_code: str, *, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BoundaryError("developer_kit", failure_code) from None
    if result.returncode != 0:
        raise BoundaryError("developer_kit", failure_code)
    return result.stdout.strip()


def _source_component_identity(
    source_root: Path, bom_raw: bytes, environment: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    node = shutil.which("node")
    if node is None:
        raise BoundaryError("developer_kit", "private_host_node_tooling_missing")
    raw = _run_local([node, "tools/component-identity.mjs"], source_root, environment,
                     "private_host_source_identity_unavailable", timeout=120)
    report = decode_json(raw)
    bom = decode_json(bom_raw)
    actual_components = report.get("components") if isinstance(report, dict) else None
    expected_components = bom.get("components") if isinstance(bom, dict) else None
    if not isinstance(actual_components, dict) or not isinstance(expected_components, dict):
        raise BoundaryError("developer_kit", "private_host_bom_identity_missing")
    result: dict[str, dict[str, Any]] = {}
    for component_id, bom_key in (("host-runtime", "host_runtime"),
                                  ("connector", "connector")):
        actual = actual_components.get(component_id)
        expected = expected_components.get(bom_key)
        if not isinstance(actual, dict) or not isinstance(expected, dict):
            raise BoundaryError("developer_kit", "private_host_source_identity_unavailable")
        if actual.get("source_worktree_status") != "clean":
            raise BoundaryError("developer_kit", "private_host_source_tree_dirty")
        if (actual.get("component_version") != expected.get("version")
                or actual.get("source_revision") != expected.get("source_revision")
                or actual.get("component_tree_revision") != expected.get(
                    "component_tree_revision")
                or actual.get("component_source_digest_sha256") != expected.get(
                    "component_source_digest_sha256")):
            raise BoundaryError("developer_kit", "private_host_source_bom_mismatch")
        result[component_id] = actual
    return result, expected_components


def _git_value(source_root: Path, arguments: list[str], failure_code: str) -> str:
    environment = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP"}}
    environment["HOME"] = str(tempfile.gettempdir())
    return _run_local(["git", *arguments], source_root, environment, failure_code)


def _copy_regular_tree(source: Path, target: Path) -> dict[str, tuple[bytes, int]]:
    if source.is_symlink() or not source.is_dir():
        raise BoundaryError("developer_kit", "private_host_bundle_source_unsafe")
    result: dict[str, tuple[bytes, int]] = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source).as_posix()
        _safe_inventory_path(relative)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise BoundaryError("developer_kit", "private_host_bundle_source_unsafe")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise BoundaryError("developer_kit", "private_host_bundle_source_unsafe")
        raw = path.read_bytes()
        file_mode = 0o755 if mode & 0o111 else 0o644
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        destination.chmod(file_mode)
        result[relative] = raw, file_mode
    return result


def _inventory(files: dict[str, tuple[bytes, int]]) -> dict[str, dict[str, Any]]:
    return {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "mode": mode}
            for name, (raw, mode) in sorted(files.items())}


def _write_package_file(root: Path, relative: str, raw: bytes, mode: int = 0o644) -> None:
    safe = _safe_inventory_path(relative)
    path = root / safe
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(mode)


def derive_private_host_candidate(original_archive_raw: bytes, original_archive_filename: str,
                                  expected_archive_sha256: str, bom_raw: bytes,
                                  source_root: Path) -> tuple[bytes, bytes, dict[str, Any]]:
    """Derive a BOM-bound Host archive using a fresh, locked SDK build/install cache."""
    root = source_root.resolve(strict=True)
    producer_identity = source_identity(root / "python")
    digest(expected_archive_sha256, "developer_kit.private_host.source_archive")
    if hashlib.sha256(original_archive_raw).hexdigest() != expected_archive_sha256:
        raise BoundaryError("developer_kit", "private_host_source_archive_checksum_mismatch")
    host_root = root / "components/host-runtime"
    sdk_root = root / "components/connector/sdk/typescript"
    component_roots = (root / "components", host_root, root / "components/connector",
                       root / "components/connector/sdk", sdk_root)
    if (Path(original_archive_filename).name != original_archive_filename
            or any(path.is_symlink() for path in component_roots)):
        raise BoundaryError("developer_kit", "private_host_source_tree_unsafe")
    if not re.fullmatch(r"rsgcsg-sts2-host-runtime-1\.1\.0-rc\.\d+\.tgz",
                        original_archive_filename):
        raise BoundaryError("developer_kit", "private_host_source_archive_identity_mismatch")
    with tempfile.TemporaryDirectory(prefix=".private-host-producer-") as temporary:
        work = Path(temporary)
        npm = shutil.which("npm")
        node = shutil.which("node")
        if npm is None or node is None:
            raise BoundaryError("developer_kit", "private_host_node_tooling_missing")
        # This identity report binds current clean component files to the kit BOM.
        base_env = _fresh_npm_environment(work / "identity-config", offline=True)
        source_identities, bom_components = _source_component_identity(root, bom_raw, base_env)
        host_identity = source_identities["host-runtime"]
        connector_identity = source_identities["connector"]
        host_manifest_raw, _ = _read_source_regular(host_root, "package.json")
        host_manifest = decode_json(host_manifest_raw)
        if (not isinstance(host_manifest, dict)
                or host_manifest.get("name") != HOST_PACKAGE
                or host_manifest.get("version") != host_identity.get("component_version")):
            raise BoundaryError("developer_kit", "private_host_source_manifest_mismatch")
        source_records = _private_host_archive_records(original_archive_raw)
        source_entries = {name: raw for name, (raw, _mode) in source_records.items()}
        if source_entries.get("package.json") != host_manifest_raw:
            raise BoundaryError("developer_kit", "private_host_source_manifest_mismatch")

        host_pack_env = _fresh_npm_environment(work / "host-pack-config", offline=True)
        pack_text = _run_npm(
            npm, ["pack", "--dry-run", "--json", "--ignore-scripts"], host_root,
            host_pack_env, "private_host_source_inventory_failed", timeout=120,
        )
        try:
            host_pack = json.loads(pack_text)[0]
            expected_host_files = {row["path"] for row in host_pack["files"]}
        except (IndexError, KeyError, json.JSONDecodeError, TypeError):
            raise BoundaryError("developer_kit", "private_host_source_inventory_failed") from None
        if expected_host_files != set(source_entries):
            raise BoundaryError("developer_kit", "private_host_source_inventory_mismatch")
        for name, (raw, mode) in source_records.items():
            current_raw, current_mode = _read_source_regular(host_root, name)
            if raw != current_raw or mode != current_mode:
                raise BoundaryError("developer_kit", "private_host_source_file_mismatch")
        source_dependencies = host_manifest.get("dependencies")
        if (not isinstance(source_dependencies, dict)
                or set(source_dependencies) != {CONNECTOR_PACKAGE}
                or not isinstance(source_dependencies.get(CONNECTOR_PACKAGE), str)):
            raise BoundaryError("developer_kit", "private_host_source_dependency_mismatch")

        sdk_manifest_raw, _ = _read_source_regular(sdk_root, "package.json")
        sdk_lock_raw, _ = _read_source_regular(sdk_root, "package-lock.json")
        sdk_manifest = decode_json(sdk_manifest_raw)
        sdk_lock = decode_json(sdk_lock_raw)
        sdk_version = bom_components.get("typescript_sdk")
        lock_packages = sdk_lock.get("packages") if isinstance(sdk_lock, dict) else None
        root_lock = lock_packages.get("") if isinstance(lock_packages, dict) else None
        zod_lock = (lock_packages.get("node_modules/zod")
                    if isinstance(lock_packages, dict) else None)
        typescript_lock = lock_packages.get("node_modules/typescript") if isinstance(
            lock_packages, dict) else None
        if (not isinstance(sdk_manifest, dict) or not isinstance(root_lock, dict)
                or not isinstance(zod_lock, dict) or not isinstance(typescript_lock, dict)
                or sdk_manifest.get("name") != CONNECTOR_PACKAGE
                or sdk_manifest.get("version") != sdk_version
                or root_lock.get("version") != sdk_version
                or root_lock.get("dependencies") != sdk_manifest.get("dependencies")
                or root_lock.get("devDependencies") != sdk_manifest.get("devDependencies")
                or sdk_manifest.get("dependencies", {}).get("zod") != "^3.25.76"
                or zod_lock.get("version") != "3.25.76"
                or zod_lock.get("resolved") !=
                "https://registry.npmjs.org/zod/-/zod-3.25.76.tgz"
                or not isinstance(zod_lock.get("integrity"), str)
                or not zod_lock["integrity"].startswith("sha512-")
                or not isinstance(typescript_lock.get("version"), str)
                or re.fullmatch(r"\d+\.\d+\.\d+", typescript_lock["version"]) is None
                or typescript_lock.get("resolved") != (
                    f"https://registry.npmjs.org/typescript/-/typescript-"
                    f"{typescript_lock.get('version')}.tgz")
                or not isinstance(typescript_lock.get("integrity"), str)
                or re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==",
                                typescript_lock["integrity"]) is None):
            raise BoundaryError("developer_kit", "private_host_sdk_source_incompatible")
        if (connector_identity.get("component_version") != bom_components.get(
                "connector", {}).get("version")
                or sdk_version != bom_components.get("typescript_sdk")):
            raise BoundaryError("developer_kit", "private_host_connector_bom_identity_mismatch")

        # Build from the committed Connector SDK source in an isolated copy. Its
        # npm ci uses the checked-in lockfile and a new cache; no author cache or
        # pre-existing node_modules/dist output can enter the candidate.
        sdk_build = work / "sdk-source"
        sdk_build.mkdir()
        # Use a byte-safe NUL-separated list so unusual filenames cannot be truncated.
        tracked_result = subprocess.run(
            ["git", "ls-files", "-z", "--", "components/connector/sdk/typescript"],
            cwd=root, env=base_env, stdin=subprocess.DEVNULL, capture_output=True,
            check=False,
        )
        if tracked_result.returncode != 0:
            raise BoundaryError("developer_kit", "private_host_sdk_source_inventory_failed")
        paths = [value.decode("utf-8") for value in tracked_result.stdout.split(b"\0") if value]
        if not paths or "components/connector/sdk/typescript/package-lock.json" not in paths:
            raise BoundaryError("developer_kit", "private_host_sdk_source_inventory_failed")
        for relative in paths:
            local = relative.removeprefix("components/connector/sdk/typescript/")
            raw, mode = _read_source_regular(root, relative)
            _write_package_file(sdk_build, local, raw, mode)
        build_env = _fresh_npm_environment(work / "sdk-build-config", offline=False)
        _run_npm(npm, ["ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                 sdk_build, build_env, "private_host_sdk_dependency_install_failed")
        _run_npm(npm, ["run", "build"], sdk_build, build_env,
                 "private_host_sdk_build_failed", timeout=300)
        try:
            built_typescript = json.loads((sdk_build / "node_modules/typescript/package.json")
                                          .read_bytes())
            built_zod = json.loads((sdk_build / "node_modules/zod/package.json").read_bytes())
        except (OSError, json.JSONDecodeError):
            raise BoundaryError("developer_kit", "private_host_sdk_build_failed") from None
        if (built_typescript.get("version") != typescript_lock["version"]
                or built_zod.get("version") != zod_lock["version"]):
            raise BoundaryError("developer_kit", "private_host_sdk_toolchain_mismatch")
        node_version = _run_local([node, "--version"], root, build_env,
                                  "private_host_node_tooling_missing")
        npm_version = _run_npm(npm, ["--version"], root, build_env,
                               "private_host_node_tooling_missing").strip()

        stage = work / "package"
        stage.mkdir()
        for name, (raw, mode) in source_records.items():
            if name != "package.json":
                _write_package_file(stage, name, raw, mode)
        sdk_stage = stage / "node_modules" / "@rsgcsg" / "sts2-connector-client"
        sdk_stage.mkdir(parents=True)
        _write_package_file(sdk_stage, "package.json", sdk_manifest_raw)
        sdk_dist = sdk_build / "dist"
        sdk_dist_records = _copy_regular_tree(sdk_dist, sdk_stage / "dist")
        if "index.js" not in sdk_dist_records or not sdk_dist_records:
            raise BoundaryError("developer_kit", "private_host_sdk_build_incomplete")
        zod_stage = stage / "node_modules/zod"
        zod_records = _copy_regular_tree(sdk_build / "node_modules/zod", zod_stage)
        if (not zod_records or "package.json" not in zod_records
                or json.loads((zod_stage / "package.json").read_bytes()).get("version")
                != zod_lock["version"]):
            raise BoundaryError("developer_kit", "private_host_sdk_dependency_install_failed")

        sdk_bundle = {"package.json": (sdk_manifest_raw, 0o644)}
        sdk_bundle.update({f"dist/{name}": value for name, value in sdk_dist_records.items()})
        sdk_bundle_sha = _tree_sha256({name: raw for name, (raw, _mode) in sdk_bundle.items()})
        zod_bundle_sha = _tree_sha256({name: raw for name, (raw, _mode) in zod_records.items()})
        derived_dependencies = {CONNECTOR_PACKAGE: sdk_version, "zod": zod_lock["version"]}
        transformed_manifest = dict(host_manifest)
        transformed_manifest["dependencies"] = derived_dependencies
        transformed_manifest["bundleDependencies"] = [CONNECTOR_PACKAGE, "zod"]
        added_files = ["npm-shrinkwrap.json", PRIVATE_HOST_DERIVATION_FILE]
        transformed_manifest["files"] = list(dict.fromkeys([
            *host_manifest.get("files", []), *added_files]))
        transformed_manifest_raw = (json.dumps(transformed_manifest, indent=2,
                                               ensure_ascii=False) + "\n").encode("utf-8")
        producer_files: dict[str, dict[str, str]] = {}
        for relative in ("python/tools/package_developer_kit.py",
                         "python/spireagent/workbench/kit_runtime.py"):
            tool_raw, _ = _read_source_regular(root, relative)
            blob = _git_value(root, ["hash-object", "--", relative],
                              "private_host_producer_identity_unavailable")
            producer_files[relative] = {"git_blob_sha1": blob,
                                        "sha256": hashlib.sha256(tool_raw).hexdigest()}
        derivation: dict[str, Any] = {
            "schema": PRIVATE_HOST_DERIVATION_SCHEMA,
            "recipe": PRIVATE_HOST_DERIVATION_RECIPE,
            "source_archive": {
                "package": HOST_PACKAGE,
                "version": host_manifest["version"],
                "filename": original_archive_filename,
                "archive_sha256": expected_archive_sha256,
                "package_content_sha256": _tree_sha256(source_entries),
                "files": _inventory(source_records),
                "package_json_utf8": host_manifest_raw.decode("utf-8"),
            },
            "host_source": {
                "version": host_identity["component_version"],
                "source_revision": host_identity["source_revision"],
                "component_tree_revision": host_identity["component_tree_revision"],
                "component_source_digest_sha256": host_identity[
                    "component_source_digest_sha256"],
                "source_file_count": host_identity["source_file_count"],
            },
            "manifest_transform": {
                "changed_fields": ["bundleDependencies", "dependencies", "files"],
                "added_files": added_files,
                "source_package_json_sha256": hashlib.sha256(host_manifest_raw).hexdigest(),
                "derived_package_json_sha256": hashlib.sha256(
                    transformed_manifest_raw).hexdigest(),
                "source_dependencies": source_dependencies,
                "derived_dependencies": derived_dependencies,
            },
            "selected_sdk": {
                "package": CONNECTOR_PACKAGE,
                "version": sdk_version,
                "component_version": connector_identity["component_version"],
                "source_revision": connector_identity["source_revision"],
                "component_tree_revision": connector_identity["component_tree_revision"],
                "component_source_digest_sha256": connector_identity[
                    "component_source_digest_sha256"],
                "bundle_sha256": sdk_bundle_sha,
            },
            "zod": {
                "version": zod_lock["version"],
                "url": zod_lock["resolved"],
                "integrity": zod_lock["integrity"],
                "bundle_sha256": zod_bundle_sha,
            },
            "producer": {
                "workspace_revision": producer_identity.source_revision,
                "tool_files": producer_files,
                "node_version": node_version,
                "npm_version": npm_version,
                "typescript_version": built_typescript["version"],
                "typescript_integrity": typescript_lock["integrity"],
            },
            "bundles": {
                "connector": {"files": _inventory(sdk_bundle)},
                "zod": {"files": _inventory(zod_records)},
            },
        }
        shrinkwrap_packages = {
            "": {
                "name": host_manifest["name"],
                "version": host_manifest["version"],
                "license": host_manifest.get("license"),
                "dependencies": derived_dependencies,
                "bundleDependencies": [CONNECTOR_PACKAGE, "zod"],
                "bin": host_manifest.get("bin"),
                "engines": host_manifest.get("engines"),
            },
            f"node_modules/{CONNECTOR_PACKAGE}": {
                "version": sdk_version,
                "inBundle": True,
                "license": sdk_manifest.get("license"),
                "dependencies": sdk_manifest.get("dependencies"),
                "engines": sdk_manifest.get("engines"),
            },
            "node_modules/zod": {
                "version": zod_lock["version"],
                "inBundle": True,
                "resolved": zod_lock["resolved"],
                "integrity": zod_lock["integrity"],
                "license": zod_lock.get("license"),
                "funding": zod_lock.get("funding"),
            },
        }
        shrinkwrap = {
            "name": host_manifest["name"], "version": host_manifest["version"],
            "lockfileVersion": 3, "requires": True, "packages": shrinkwrap_packages,
        }
        _write_package_file(stage, "package.json", transformed_manifest_raw)
        _write_package_file(stage, "npm-shrinkwrap.json", json_bytes(shrinkwrap))
        _write_package_file(stage, PRIVATE_HOST_DERIVATION_FILE, json_bytes(derivation))
        packed_raw = _run_npm(
            npm, ["pack", "--ignore-scripts", "--json", "--pack-destination", str(work)],
            stage, _fresh_npm_environment(work / "npm-pack-config", offline=True),
            "private_host_package_archive_failed", timeout=120,
        )
        try:
            pack_report = json.loads(packed_raw)[0]
            derived_archive = (work / pack_report["filename"]).read_bytes()
        except (IndexError, KeyError, OSError, json.JSONDecodeError, TypeError):
            raise BoundaryError("developer_kit", "private_host_package_archive_failed") from None
        derived_entries = _private_host_archive_entries(derived_archive)
        host_pin = {
            "schema": "stpd/platform-host-runtime-pin-v1",
            "package": HOST_PACKAGE,
            "version": host_identity["component_version"],
            "source_revision": host_identity["source_revision"],
            "component_tree_revision": host_identity["component_tree_revision"],
            "release_asset_sha256": hashlib.sha256(derived_archive).hexdigest(),
            "package_content_sha256": _tree_sha256(derived_entries),
        }
        connector_pin = {
            "package": CONNECTOR_PACKAGE,
            "version": sdk_version,
            "source_revision": connector_identity["source_revision"],
            "component_tree_revision": connector_identity["component_tree_revision"],
            "component_source_digest_sha256": connector_identity[
                "component_source_digest_sha256"],
            "bundle_sha256": sdk_bundle_sha,
            "transitive_zod": {
                "version": zod_lock["version"], "url": zod_lock["resolved"],
                "integrity": zod_lock["integrity"], "bundle_sha256": zod_bundle_sha,
            },
        }
        profile = {
            "schema": PRIVATE_HOST_DERIVED_SCHEMA,
            "distribution": "private_kit_candidate",
            "host_runtime": host_pin,
            "component_source_digest_sha256": host_identity[
                "component_source_digest_sha256"],
            "dependency_layout": BUNDLED_LAYOUT,
            "bundled_connector_pin": connector_pin,
            "derivation": derivation,
        }
        profile_raw = json_bytes(profile)
        identity = private_host_runtime_pin(profile_raw, derived_archive, bom_raw)
        verify_private_host_offline_install(profile_raw, derived_archive, bom_raw)
        # Recheck after all tool runs so a concurrent source change cannot produce
        # an archive whose source receipt was assembled from mixed snapshots.
        verify_private_host_source_binding(profile_raw, derived_archive, bom_raw, root)
        if source_identity(root / "python") != producer_identity:
            raise BoundaryError("developer_kit", "private_host_producer_source_changed")
        summary = {
            "schema": "stpd/private-host-derived-package-report-v1",
            "package": HOST_PACKAGE,
            "version": host_pin["version"],
            "source_archive_sha256": expected_archive_sha256,
            "source_package_content_sha256": derivation["source_archive"][
                "package_content_sha256"],
            "derived_archive_sha256": host_pin["release_asset_sha256"],
            "derived_package_content_sha256": host_pin["package_content_sha256"],
            "profile_sha256": hashlib.sha256(profile_raw).hexdigest(),
            "host_source_revision": host_identity["source_revision"],
            "sdk_version": sdk_version,
            "sdk_source_revision": connector_identity["source_revision"],
            "sdk_bundle_sha256": sdk_bundle_sha,
            "zod_version": zod_lock["version"],
            "zod_integrity": zod_lock["integrity"],
            "zod_bundle_sha256": zod_bundle_sha,
            "files": len(derived_entries),
            "offline_install_import": "passed",
            "host_running": False,
            "game_touched": False,
        }
        if identity.get("derivation") != derivation:
            raise BoundaryError("developer_kit", "private_host_derivation_mismatch")
        return profile_raw, derived_archive, summary


def verify_private_host_source_binding(profile_raw: bytes, archive_raw: bytes,
                                       bom_raw: bytes, source_root: Path) -> None:
    """Recheck private candidate claims against an exact clean Host/Connector source tree."""
    profile = decode_json(profile_raw)
    if (not isinstance(profile, dict)
            or profile.get("schema") != PRIVATE_HOST_DERIVED_SCHEMA
            or not isinstance(profile.get("derivation"), dict)):
        raise BoundaryError("developer_kit", "private_host_derivation_required")
    derivation = profile["derivation"]
    assert isinstance(derivation, dict)
    root = source_root.resolve(strict=True)
    repository_root = Path(_git_value(
        root, ["rev-parse", "--show-toplevel"],
        "private_host_source_identity_unavailable",
    )).resolve(strict=True)
    if repository_root != root:
        raise BoundaryError("developer_kit", "private_host_source_tree_unsafe")
    checkout_revision = _git_value(
        root, ["rev-parse", "HEAD"], "private_host_source_identity_unavailable",
    )
    if _git_value(root, ["status", "--porcelain"],
                  "private_host_source_identity_unavailable"):
        raise BoundaryError("developer_kit", "private_host_source_tree_dirty")
    host_root = root / "components/host-runtime"
    sdk_root = root / "components/connector/sdk/typescript"
    component_roots = (root / "components", host_root, root / "components/connector",
                       root / "components/connector/sdk", sdk_root)
    if (any(path.is_symlink() for path in component_roots) or not host_root.is_dir()
            or not sdk_root.is_dir()):
        raise BoundaryError("developer_kit", "private_host_source_tree_unsafe")
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node is None or npm is None:
        raise BoundaryError("developer_kit", "private_host_node_tooling_missing")
    with tempfile.TemporaryDirectory(prefix=".private-host-source-check-") as temporary:
        isolated = Path(temporary)
        env = _fresh_npm_environment(isolated / "npm-config", offline=True)
        try:
            identity_result = subprocess.run(
                [node, "tools/component-identity.mjs"], cwd=root, env=env,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=60, check=False,
            )
            identity_report = (json.loads(identity_result.stdout)
                               if identity_result.returncode == 0 else None)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            identity_report = None
        if not isinstance(identity_report, dict) or not isinstance(
                identity_report.get("components"), dict):
            raise BoundaryError("developer_kit", "private_host_source_identity_unavailable")
        bom = decode_json(bom_raw)
        components = bom.get("components") if isinstance(bom, dict) else None
        if not isinstance(components, dict):
            raise BoundaryError("developer_kit", "private_host_bom_identity_missing")
    checks = (
        ("host-runtime", "host_source", "host_runtime"),
        ("connector", "selected_sdk", "connector"),
    )
    for component_id, identity_key, bom_key in checks:
        actual = identity_report["components"].get(component_id)
        component = components.get(bom_key)
        claimed = derivation.get(identity_key)
        if not all(isinstance(item, dict) for item in (actual, component, claimed)):
            raise BoundaryError("developer_kit", "private_host_source_identity_unavailable")
        assert isinstance(actual, dict)
        assert isinstance(component, dict)
        assert isinstance(claimed, dict)
        if actual.get("source_worktree_status") != "clean":
            raise BoundaryError("developer_kit", "private_host_source_tree_dirty")
        fields = {
            "source_revision": "source_revision",
            "component_tree_revision": "component_tree_revision",
            "component_source_digest_sha256": "component_source_digest_sha256",
        }
        for actual_field, bom_field in fields.items():
            if (actual.get(actual_field) != component.get(bom_field)
                    or claimed.get(actual_field) != component.get(bom_field)):
                raise BoundaryError("developer_kit", "private_host_source_bom_mismatch")
        expected_version = component.get("version")
        claimed_version = claimed.get(
            "version" if identity_key == "host_source" else "component_version"
        )
        actual_version = actual.get("component_version")
        if actual_version != expected_version or claimed_version != expected_version:
            raise BoundaryError("developer_kit", "private_host_source_bom_mismatch")

    _verify_private_host_provenance_report(
        identity_report, derivation, checkout_revision,
    )

    sdk_version = components.get("typescript_sdk")
    if derivation["selected_sdk"].get("version") != sdk_version:
        raise BoundaryError("developer_kit", "private_host_source_bom_mismatch")
    sdk_manifest_raw, _ = _read_source_regular(sdk_root, "package.json")
    sdk_lock_raw, _ = _read_source_regular(sdk_root, "package-lock.json")
    sdk_manifest = decode_json(sdk_manifest_raw)
    sdk_lock = decode_json(sdk_lock_raw)
    lock_packages = sdk_lock.get("packages") if isinstance(sdk_lock, dict) else None
    root_lock = lock_packages.get("") if isinstance(lock_packages, dict) else None
    zod_lock = lock_packages.get("node_modules/zod") if isinstance(lock_packages, dict) else None
    typescript_lock = lock_packages.get("node_modules/typescript") if isinstance(
        lock_packages, dict) else None
    producer = derivation.get("producer")
    connector_pin = profile.get("bundled_connector_pin")
    assert isinstance(producer, dict)
    assert isinstance(connector_pin, dict)
    if (not isinstance(sdk_manifest, dict) or not isinstance(root_lock, dict)
            or not isinstance(zod_lock, dict) or not isinstance(typescript_lock, dict)
            or sdk_manifest.get("name") != CONNECTOR_PACKAGE
            or sdk_manifest.get("version") != sdk_version
            or root_lock.get("version") != sdk_version
            or root_lock.get("dependencies") != sdk_manifest.get("dependencies")
            or root_lock.get("devDependencies") != sdk_manifest.get("devDependencies")
            or sdk_manifest.get("dependencies", {}).get("zod") != "^3.25.76"
            or zod_lock.get("version") != derivation.get("zod", {}).get("version")
            or zod_lock.get("resolved") != derivation.get("zod", {}).get("url")
            or zod_lock.get("integrity") != derivation.get("zod", {}).get("integrity")
            or typescript_lock.get("version") != producer.get("typescript_version")
            or typescript_lock.get("resolved") != (
                f"https://registry.npmjs.org/typescript/-/typescript-"
                f"{producer.get('typescript_version')}.tgz")
            or typescript_lock.get("integrity") != producer.get("typescript_integrity")
            or connector_pin.get("version") != sdk_manifest.get("version")):
        raise BoundaryError("developer_kit", "private_host_source_sdk_lock_mismatch")
    for relative, record in producer.get("tool_files", {}).items():
        tool_raw, _ = _read_source_regular(root, relative)
        blob = _git_value(root, ["hash-object", "--", relative],
                          "private_host_producer_identity_unavailable")
        if (hashlib.sha256(tool_raw).hexdigest() != record.get("sha256")
                or blob != record.get("git_blob_sha1")):
            raise BoundaryError("developer_kit", "private_host_producer_identity_mismatch")

    archive_identity = derivation.get("source_archive")
    source_files = archive_identity.get("files") if isinstance(archive_identity, dict) else None
    source_manifest_raw = archive_identity.get("package_json_utf8") if isinstance(
        archive_identity, dict) else None
    if not isinstance(source_files, dict) or not isinstance(source_manifest_raw, str):
        raise BoundaryError("developer_kit", "private_host_derivation_invalid")
    source_manifest_bytes = source_manifest_raw.encode("utf-8")
    current_manifest, _ = _read_source_regular(host_root, "package.json")
    if source_manifest_bytes != current_manifest:
        raise BoundaryError("developer_kit", "private_host_source_manifest_mismatch")
    for name, row in source_files.items():
        raw, mode = _read_source_regular(host_root, name)
        if (not isinstance(row, dict) or hashlib.sha256(raw).hexdigest() != row.get("sha256")
                or len(raw) != row.get("bytes") or mode != row.get("mode")):
            raise BoundaryError("developer_kit", "private_host_source_file_mismatch")

    with tempfile.TemporaryDirectory(prefix=".private-host-source-pack-") as pack_temp:
        pack_home = Path(pack_temp)
        env = _fresh_npm_environment(pack_home / "npm-config", offline=True)
        try:
            packed = _run_npm(
                npm, ["pack", "--dry-run", "--json", "--ignore-scripts"], host_root,
                env, "private_host_source_inventory_failed", timeout=120,
            )
            pack_report = json.loads(packed)[0]
        except (IndexError, KeyError, json.JSONDecodeError, TypeError):
            raise BoundaryError("developer_kit", "private_host_source_inventory_failed") from None
        expected_paths = {item.get("path") for item in pack_report.get("files", [])
                          if isinstance(item, dict)}
        if expected_paths != set(source_files):
            raise BoundaryError("developer_kit", "private_host_source_inventory_mismatch")

    # The original Host tar is preserved by its record; every non-manifest source
    # payload is also byte-identical in the derived package and was just rebound
    # to the current BOM source tree above.
    records = _private_host_archive_records(archive_raw)
    for name, row in source_files.items():
        if name == "package.json":
            continue
        record = records.get(name)
        if record is None:
            raise BoundaryError("developer_kit", "private_host_source_file_mismatch")
        raw = record[0]
        if (hashlib.sha256(raw).hexdigest() != row.get("sha256")
                or record[1] != row.get("mode")):
            raise BoundaryError("developer_kit", "private_host_source_file_mismatch")


def _verify_private_host_provenance_report(identity_report: dict[str, Any],
                                           derivation: dict[str, Any],
                                           checkout_revision: str) -> None:
    """Bind receipt-only provenance fields to the exact clean source checkout."""
    components = identity_report.get("components")
    host_source = derivation.get("host_source")
    producer = derivation.get("producer")
    actual_host = components.get("host-runtime") if isinstance(components, dict) else None
    if not all(isinstance(item, dict) for item in (actual_host, host_source, producer)):
        raise BoundaryError("developer_kit", "private_host_source_identity_unavailable")
    assert isinstance(actual_host, dict)
    assert isinstance(host_source, dict)
    assert isinstance(producer, dict)
    if actual_host.get("source_file_count") != host_source.get("source_file_count"):
        raise BoundaryError("developer_kit", "private_host_source_file_count_mismatch")
    if (producer.get("workspace_revision") != identity_report.get("workspace_revision")
            or producer.get("workspace_revision") != checkout_revision):
        raise BoundaryError("developer_kit", "private_host_producer_workspace_mismatch")


def verify_private_host_offline_install(profile_raw: bytes, archive_raw: bytes,
                                        bom_raw: bytes, *, source_root: Path | None = None
                                        ) -> None:
    """Prove the fixed archive installs/imports with isolated npm config and no network."""
    identity = private_host_runtime_pin(profile_raw, archive_raw, bom_raw,
                                        source_root=source_root)
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
        import_check = (
            "import { readFile } from 'node:fs/promises';"
            "const pkg = JSON.parse(await readFile('./package.json', 'utf8'));"
            "const hostPath = pkg.main || pkg.exports ? '@rsgcsg/sts2-host-runtime' "
            ": './src/project-identity.mjs';"
            "const host = await import(hostPath);"
            "const sdk = await import('@rsgcsg/sts2-connector-client');"
            "const zod = await import('zod');"
            "if (!Object.keys(host).length || !Object.keys(sdk).length "
            "|| !Object.keys(zod).length) process.exit(1);"
        )
        check = subprocess.run(
            [node, "--input-type=module", "--eval", import_check],
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
