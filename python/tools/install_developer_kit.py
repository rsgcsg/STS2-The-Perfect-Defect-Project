"""Prepare a fixed release directory from an independently approved developer-kit hash.

Uses the existing kit inventory and native lifecycle. Never builds a Mod, changes
consent, migrates a queue, starts gameplay or replaces an existing release directory.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

# `python -I tools/install_developer_kit.py ...` deliberately ignores the
# working directory and PYTHONPATH. Anchor only this application's source;
# Evidence must resolve from the interpreter's locked installed distribution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sts2_platform_evidence.collection_tool import CollectionTool  # noqa: E402

from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes  # noqa: E402
from spireagent.workbench.kit_environment import (  # noqa: E402
    PROFILE_FIELD,
    extras_for_python_environment_profile,
    resolve_python_environment_profile,
)
from spireagent.workbench.kit_runtime import (  # noqa: E402
    KIT_RUNTIME_PAIRS,
    M2_ARCHIVE_DESTINATION,
    M2_RUNTIME_ARCHIVE,
    M2_RUNTIME_DESTINATION,
    M2_RUNTIME_PROFILE,
    PRIVATE_HOST_ARCHIVE,
    PRIVATE_HOST_ARCHIVE_DESTINATION,
    PRIVATE_HOST_MANIFEST_KEY,
    PRIVATE_HOST_PACKAGE_DESTINATION,
    PRIVATE_HOST_PIN_DESTINATION,
    PRIVATE_HOST_PROFILE,
    PRIVATE_HOST_PROFILE_DESTINATION,
    TEXT_ARCHIVE_DESTINATION,
    TEXT_RUNTIME_ARCHIVE,
    TEXT_RUNTIME_DESTINATION,
    TEXT_RUNTIME_PROFILE,
    private_host_files,
    stage_private_host_runtime,
    text_runtime_pin,
    validate_private_host_package_directory,
)

# Retain the established public test/operator constants as importable aliases.
__all__ = ("M2_RUNTIME_ARCHIVE", "M2_RUNTIME_DESTINATION", "M2_RUNTIME_PROFILE",
           "TEXT_RUNTIME_ARCHIVE", "TEXT_RUNTIME_DESTINATION", "TEXT_RUNTIME_PROFILE",
           "PRIVATE_HOST_ARCHIVE", "PRIVATE_HOST_PROFILE")

REPOSITORY = "https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project.git"
LIMIT = 256 * 1024 * 1024
LAUNCHER_SCHEMA = "spireagent/workbench-launcher-v1"
LAUNCHER_SNAPSHOT_SCHEMA = "spireagent/workbench-launcher-snapshot-v1"
LAUNCHER_FILE_LIMITS = {"launcher.json": 64 * 1024, "open": 16 * 1024}
TOOL_BIN = "components/annotator/src/STS2HumanAnnotator.Tool/bin/Release/net9.0/"
STAGING = {
    "mod/STS2_PLATFORM.dll": "apps/game-mod/bin/Release/net9.0/STS2_PLATFORM.dll",
    "collection-tool/game-mod/build-provenance.json": (
        "apps/game-mod/bin/Release/net9.0/build-provenance.json"
    ),
    **{
        f"collection-tool/{name}": TOOL_BIN + name
        for name in (
            "sts2-human-annotator.dll",
            "sts2-human-annotator.deps.json",
            "sts2-human-annotator.runtimeconfig.json",
            "STS2HumanAnnotator.Core.dll",
        )
    },
}


def reject(code: str) -> NoReturn:
    raise BoundaryError("kit_install", code)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def text_runtime_files(manifest: dict[str, Any], files: dict[str, bytes], *,
                       memory: bool = False,
                       required_profile: str | None = None) -> bool:
    profile_id = required_profile or ("text-menu-m2-v1" if memory else "text-menu-v1")
    profile_name, archive_name, _, _, key, _ = KIT_RUNTIME_PAIRS[profile_id]
    identity = manifest.get(key)
    if identity is None:
        if profile_name in files or archive_name in files:
            reject("text_runtime_inventory_incomplete")
        return False
    if (not isinstance(identity, dict)
            or set(identity) != {"profile_sha256", "archive_sha256"}
            or profile_name not in files
            or archive_name not in files):
        reject("text_runtime_inventory_incomplete")
    for field, name in (("profile_sha256", profile_name),
                        ("archive_sha256", archive_name)):
        digest(identity[field], "kit_install.text_runtime_identity")
        if identity[field] != sha(files[name]):
            reject("text_runtime_inventory_mismatch")
    text_runtime_pin(files[profile_name], files[archive_name],
                     **({"required_profile": profile_id}
                        if profile_id == "text-menu-m2-v2" else
                        {"memory": profile_id == "text-menu-m2-v1"}))
    return True


def verified_archive(archive: Path, expected: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    digest(expected, "kit_install.archive")
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > LIMIT:
        reject("bounded_regular_archive_required")
    raw = archive.read_bytes()
    if len(raw) > LIMIT or sha(raw) != expected:
        reject("archive_checksum_mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos = z.infolist()
        if len(infos) > 4096 or sum(i.file_size for i in infos) > LIMIT:
            reject("archive_limits_exceeded")
        files = {}
        for info in infos:
            name = info.filename
            p = PurePosixPath(name)
            mode = info.external_attr >> 16
            if (
                name != info.orig_filename
                or name in files
                or p.is_absolute()
                or ".." in p.parts
                or str(p) != name
                or "\\" in name
                or ":" in name
                or info.is_dir()
                or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
            ):
                reject("unsafe_or_duplicate_archive_path")
            files[name] = z.read(info)
    manifest = decode_json(files.get("combination.json", b"{}"))
    if not isinstance(manifest, dict):
        reject("unsupported_kit_schema")
    schema = manifest.get("schema")
    if not isinstance(schema, str) or schema not in {
        "spireagent/developer-kit-v1", "spireagent/developer-kit-v2"
    }:
        reject("unsupported_kit_schema")
    if ((schema == "spireagent/developer-kit-v1" and PROFILE_FIELD in manifest)
            or (schema == "spireagent/developer-kit-v2" and PROFILE_FIELD not in manifest)):
        reject("kit_schema_python_environment_profile_mismatch")
    if ("workbench_launcher_schema" in manifest
            and manifest["workbench_launcher_schema"] != LAUNCHER_SCHEMA):
        reject("unsupported_workbench_launcher")
    inventory = manifest.get("files")
    if not isinstance(inventory, dict) or set(inventory) != set(files) - {"combination.json"}:
        reject("inventory_mismatch")
    for name, expected_file in inventory.items():
        if sha(files[name]) != expected_file:
            reject("file_checksum_mismatch")
    digest(manifest.get("stpd_source_revision"), "kit_install.source", length=40)
    digest(manifest.get("uv_lock_sha256"), "kit_install.lock")
    digest(manifest.get("collection_tool_release_id"), "kit_install.tool")
    for field, name in (
        ("mod_sha256", "mod/STS2_PLATFORM.dll"),
        ("mod_manifest_sha256", "mod/STS2_PLATFORM.json"),
        ("platform_bom_sha256", "platform-bom.json"),
        ("developer_combination_sha256", "developer-combination.json"),
    ):
        if name not in files or manifest.get(field) != sha(files[name]):
            reject("composition_identity_mismatch")
    if not set(STAGING).issubset(files):
        reject("native_installation_files_missing")
    try:
        resolve_python_environment_profile(manifest, files, KIT_RUNTIME_PAIRS)
    except ValueError as error:
        reject(str(error))
    for profile_id in KIT_RUNTIME_PAIRS:
        text_runtime_files(manifest, files, required_profile=profile_id)
    private_host_files(manifest, files, files["platform-bom.json"])
    return manifest, files


def run(command: list[str], cwd: Path, *, environment: dict[str, str] | None = None) -> str:
    executable = shutil.which(command[0])
    if executable is None:
        reject("required_program_missing_" + command[0])
    if command[0] == "uv":
        # A release checkout must use its own project and interpreter. Keep ordinary
        # network/certificate settings, but remove inherited import and uv targets.
        environment = dict(os.environ if environment is None else environment)
        for name in (
            "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
            "UV_WORKING_DIR", "UV_PROJECT", "UV_PYTHON", "UV_CONFIG_FILE", "UV_ENV_FILE",
        ):
            environment.pop(name, None)
    args = [str(executable), *command[1:]]
    result = subprocess.run(
        args, cwd=cwd, env=environment, capture_output=True, text=True, timeout=900
    )
    if result.returncode:
        # Git/credential helpers and subprocess exceptions can include secrets.
        reject("step_failed_" + command[0])
    return result.stdout


def prepare(archive: Path, expected: str, releases: Path) -> dict[str, Any]:
    manifest, files = verified_archive(archive, expected)
    if not releases.is_absolute() or any(p.is_symlink() for p in (releases, *releases.parents)):
        reject("absolute_non_symlink_release_root_required")
    if any((p / ".git").exists() for p in (releases, *releases.parents)):
        reject("release_directory_must_be_outside_development_checkout")
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / expected
    if target.exists() or target.is_symlink():
        reject("release_exists_use_status_not_overwrite")
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=releases) as temporary:
        stage = Path(temporary)
        kit, source = stage / "kit", stage / "source"
        shutil.copyfile(archive, stage / "package.zip")
        verified_archive(stage / "package.zip", expected)
        for name, raw in files.items():
            destination = kit / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        CollectionTool(kit / "collection-tool", manifest["collection_tool_release_id"])
        run(["git", "clone", "--no-checkout", REPOSITORY, str(source)], stage)
        run(["git", "checkout", "--detach", manifest["stpd_source_revision"]], source)
        if sha((source / "python/uv.lock").read_bytes()) != manifest["uv_lock_sha256"]:
            reject("source_lock_mismatch")
        if (source / "apps/game-mod/mod_manifest.json").read_bytes() != files[
            "mod/STS2_PLATFORM.json"
        ]:
            reject("source_mod_manifest_mismatch")
        if (source / "python/configs/developer/combination-v1.json").read_bytes() != files[
            "developer-combination.json"
        ]:
            reject("source_combination_mismatch")
        for name, relative in STAGING.items():
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(files[name])
        for profile_id, pair in KIT_RUNTIME_PAIRS.items():
            if not text_runtime_files(manifest, files, required_profile=profile_id):
                continue
            pairs = ((pair[0], pair[2]), (pair[1], pair[3]))
            for name, relative in pairs:
                destination = source / relative
                if any(p.is_symlink() for p in destination.parents):
                    reject("text_runtime_staging_path_unsafe")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() or destination.is_symlink():
                    reject("text_runtime_staging_exists")
                destination.write_bytes(files[name])
        private_host = private_host_files(manifest, files, files["platform-bom.json"],
                                          source_root=source)
        if private_host is not None:
            staged_pairs = (
                (PRIVATE_HOST_PROFILE, PRIVATE_HOST_PROFILE_DESTINATION),
                (PRIVATE_HOST_ARCHIVE, PRIVATE_HOST_ARCHIVE_DESTINATION),
                (None, PRIVATE_HOST_PIN_DESTINATION),
            )
            for archive_name, relative in staged_pairs:
                destination = source / relative
                if any(parent.is_symlink() for parent in (destination, *destination.parents)):
                    reject("private_host_staging_path_unsafe")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() or destination.is_symlink():
                    reject("private_host_staging_exists")
                raw = (files[archive_name] if archive_name is not None else json_bytes(
                    private_host["host_runtime"]))
                destination.write_bytes(raw)
        if run(["git", "status", "--porcelain"], source).strip():
            reject("staging_changed_tracked_source")
        # Rename before uv: virtualenv interpreter paths must use the permanent location.
        stage.rename(target)
    return status(target)


def status(directory: Path) -> dict[str, Any]:
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        reject("release_path_unsafe")
    manifest, files = verified_archive(directory / "package.zip", directory.name)
    combination_file = _read_tree_file(
        directory / "kit", "combination.json", "prepared_manifest"
    )
    if combination_file != files["combination.json"]:
        reject("prepared_manifest_changed")
    source = directory / "source"
    if run(["git", "rev-parse", "HEAD"], source).strip() != manifest["stpd_source_revision"]:
        reject("prepared_source_changed")
    if run(["git", "status", "--porcelain"], source).strip():
        reject("prepared_source_dirty")
    for name, expected in manifest["files"].items():
        p = directory / "kit" / name
        if any(a.is_symlink() for a in (p, *p.parents)):
            reject("prepared_kit_changed")
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            reject("prepared_kit_file_missing")
        if sha(raw) != expected:
            reject("prepared_kit_changed")
    for name, relative in STAGING.items():
        raw = _read_tree_file(source, relative, "staged_native")
        if sha(raw) != manifest["files"][name]:
            reject("staged_native_changed")
    for profile_id, pair in KIT_RUNTIME_PAIRS.items():
        if not text_runtime_files(manifest, files, required_profile=profile_id):
            continue
        pairs = ((pair[0], pair[2]), (pair[1], pair[3]))
        for name, relative in pairs:
            staged = _read_tree_file(source, relative, "staged_text_runtime")
            if sha(staged) != manifest["files"][name]:
                reject("staged_text_runtime_changed")
    private_host = private_host_files(manifest, files, files["platform-bom.json"],
                                      source_root=source)
    private_host_status = "not_bundled"
    private_host_identity = None
    private_host_selection = "not_observed"
    if private_host is not None:
        for name, relative in (
            (PRIVATE_HOST_PROFILE, PRIVATE_HOST_PROFILE_DESTINATION),
            (PRIVATE_HOST_ARCHIVE, PRIVATE_HOST_ARCHIVE_DESTINATION),
        ):
            staged = _read_tree_file(source, relative,
                                     "staged_private_host")
            if sha(staged) != manifest["files"][name]:
                reject("staged_private_host_changed")
        pin_raw = _read_tree_file(source, PRIVATE_HOST_PIN_DESTINATION,
                                  "staged_private_host_pin")
        if decode_json(pin_raw) != private_host["host_runtime"]:
            reject("staged_private_host_pin_changed")
        package_root = source / PRIVATE_HOST_PACKAGE_DESTINATION
        if package_root.exists() or package_root.is_symlink():
            try:
                validate_private_host_package_directory(package_root, private_host)
            except BoundaryError:
                reject("staged_private_host_package_changed")
            private_host_status = "installed_verified"
        else:
            private_host_status = "bundled_installation_not_checked"
        group = manifest[PRIVATE_HOST_MANIFEST_KEY]
        private_host_identity = {
            "distribution": private_host["distribution"],
            "profile_sha256": group["profile_sha256"],
            "archive_sha256": group["archive_sha256"],
            "host_runtime": private_host["host_runtime"],
            "component_source_digest_sha256": private_host[
                "component_source_digest_sha256"],
            "dependency_layout": private_host["dependency_layout"],
            "bundled_connector_pin": private_host["bundled_connector_pin"],
            "derivation": private_host.get("derivation"),
        }
    CollectionTool(directory / "kit/collection-tool", manifest["collection_tool_release_id"])
    try:
        effective_python_profile = resolve_python_environment_profile(
            manifest, files, KIT_RUNTIME_PAIRS)
    except ValueError as error:  # verified_archive already validates this invariant
        reject(str(error))
    return {
        "status": "prepared",
        "source_revision": manifest["stpd_source_revision"],
        "tool_release_id": manifest["collection_tool_release_id"],
        "mod_sha256": manifest["mod_sha256"],
        "uv_lock_sha256": manifest["uv_lock_sha256"],
        PROFILE_FIELD: effective_python_profile,
        "workbench_launcher_schema": manifest.get("workbench_launcher_schema"),
        "directory": str(directory),
        "installed": "not_checked",
        "loaded": "not_checked",
        "next": "initialize; then follow the native owner deploy/cold-load steps",
        **{pair[4]: ("bundled_installation_not_checked" if pair[4] in manifest
                     else "not_bundled") for pair in KIT_RUNTIME_PAIRS.values()},
        # The Workbench may read only these fixed staged pairs. These
        # hashes come from the already verified package inventory, not from a
        # caller-supplied profile or path.
        **{pair[4] + "_identity": manifest.get(pair[4])
           for pair in KIT_RUNTIME_PAIRS.values()},
        "private_host_runtime": private_host_status,
        "private_host_runtime_identity": private_host_identity,
        "private_host_runtime_selection": private_host_selection,
    }


def _read_tree_file(root: Path, relative: str, code: str) -> bytes:
    """Read one ordinary file below a trusted tree without following links."""
    parts = PurePosixPath(relative)
    if parts.is_absolute() or not parts.parts or ".." in parts.parts or "\\" in relative:
        reject(code + "_unsafe")
    current = root
    if current.is_symlink() or not current.is_dir():
        reject(code + "_unsafe")
    for index, part in enumerate(parts.parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            reject(code + "_missing")
        if stat.S_ISLNK(mode):
            reject(code + "_unsafe")
        if index < len(parts.parts) - 1 and not stat.S_ISDIR(mode):
            reject(code + "_missing")
    if not stat.S_ISREG(mode):
        reject(code + "_missing")
    return current.read_bytes()


def validate_package_tuple(combination: dict[str, Any], bom: dict[str, Any],
                           connector_release: dict[str, Any]) -> dict[str, Any]:
    """Bind the selected package pins to published BOM assets and protocol authority."""
    if (not isinstance(combination, dict) or not isinstance(bom, dict)
            or not isinstance(connector_release, dict)
            or combination.get("schema") != "stpd/developer-combination-v1"):
        reject("package_identity_incompatible")
    packages = combination.get("node_packages")
    if not isinstance(packages, list):
        reject("package_identity_incompatible")
    indexed: dict[str, dict[str, Any]] = {}
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = package.get("package")
        if not isinstance(name, str) or name not in {
            "@rsgcsg/sts2-host-runtime", "@rsgcsg/sts2-connector-client"
        }:
            continue
        if name in indexed:
            reject("package_identity_ambiguous")
        indexed[name] = package
    host, sdk = indexed.get("@rsgcsg/sts2-host-runtime"), indexed.get(
        "@rsgcsg/sts2-connector-client"
    )
    public = bom.get("public_packages")
    public_host = public.get("host_runtime") if isinstance(public, dict) else None
    public_sdk = public.get("typescript_sdk") if isinstance(public, dict) else None
    components = bom.get("components")
    if not all(isinstance(item, dict) for item in (host, sdk, public_host, public_sdk,
                                                    components)):
        reject("package_identity_missing")
    assert isinstance(host, dict)
    assert isinstance(sdk, dict)
    assert isinstance(public_host, dict)
    assert isinstance(public_sdk, dict)
    assert isinstance(components, dict)
    digest(host.get("source_revision"), "kit_install.host_source", length=40)
    digest(host.get("release_asset_sha256"), "kit_install.host_asset")
    digest(host.get("package_content_sha256"), "kit_install.host_content")
    digest(sdk.get("release_asset_sha256"), "kit_install.connector_client_asset")
    if (host.get("version") != public_host.get("version")
            or host.get("source_revision") != public_host.get("source_revision")
            or host.get("release_asset_sha256") != public_host.get("sha256")
            or host.get("package_content_sha256") != public_host.get(
                "package_content_digest_sha256")
            or public_host.get("asset") !=
            f"rsgcsg-sts2-host-runtime-{public_host.get('version')}.tgz"
            or public_host.get("release") != f"host-runtime/v{public_host.get('version')}"):
        reject("published_package_identity_mismatch")
    current_host = components.get("host_runtime", {})
    if not isinstance(current_host, dict):
        reject("package_identity_incompatible")
    expected_relation = ("same_component_version" if
                         public_host.get("version") == current_host.get("version") else
                         "published_package_precedes_current_source")
    if public_host.get("source_relation") != expected_relation:
        reject("published_package_relation_mismatch")
    asset_match = re.fullmatch(
        r"rsgcsg-sts2-connector-client-(.+)\.tgz", str(public_sdk.get("asset", ""))
    )
    if (not asset_match or sdk.get("version") != asset_match.group(1)
            or sdk.get("release_asset_sha256") != public_sdk.get("sha256")):
        reject("published_package_identity_mismatch")

    player_environment = connector_release.get("player_environment")
    release = connector_release.get("release")
    exact_runtime = bom.get("exact_runtime_candidate")
    v2 = bom.get("current_v2_candidate")
    if not all(isinstance(item, dict) for item in (player_environment, release,
                                                   exact_runtime, v2)):
        reject("package_identity_incompatible")
    assert isinstance(player_environment, dict)
    assert isinstance(release, dict)
    assert isinstance(exact_runtime, dict)
    assert isinstance(v2, dict)
    connector_protocol = player_environment.get("protocol")
    connector_version = release.get("version")
    exact_connector = exact_runtime.get("connector")
    v2_connector = v2.get("connector")
    current_connector = components.get("connector")
    if not all(isinstance(item, dict) for item in
               (exact_connector, v2_connector, current_connector)):
        reject("package_identity_incompatible")
    assert isinstance(exact_connector, dict)
    assert isinstance(v2_connector, dict)
    assert isinstance(current_connector, dict)
    if (not connector_protocol
            or connector_protocol != components.get("player_environment_protocol")
            or connector_protocol != exact_connector.get("protocol")
            or connector_protocol != v2_connector.get("protocol")
            or connector_version != current_connector.get("version")):
        reject("protocol_incompatible")
    return {
        "host": {
            "bom_anchored": {key: host[key] for key in (
                "package", "version", "source_revision", "release_asset_sha256",
                "package_content_sha256"
            )},
        },
        "connector_client": {
            "bom_anchored": {
                "asset": public_sdk["asset"],
                "release_asset_sha256": public_sdk["sha256"],
                "version_from_asset_name": asset_match.group(1),
            },
            "combination_claims_unverified": {
                "source_revision": sdk.get("source_revision")
                if re.fullmatch(r"[0-9a-f]{40}", str(sdk.get("source_revision", "")))
                else "unknown",
                "package_content_sha256": sdk.get("package_content_sha256")
                if re.fullmatch(r"[0-9a-f]{64}", str(sdk.get("package_content_sha256", "")))
                else "unknown",
                "provenance": "self_asserted_in_archive_bound_combination",
            },
        },
        "player_environment_protocol": connector_protocol,
    }


def _game_file(root: Path, absolute: str, code: str, *, root_alias: Path | None = None) -> bytes:
    candidate = Path(absolute)
    if not candidate.is_absolute() or ".." in candidate.parts:
        reject("game_identity_ambiguous")
    # Map the doctor's lexical paths onto the canonical root. This accepts harmless
    # symlinked ancestors (for example /tmp -> /private/tmp) while refusing links
    # within the selected game tree.
    root_alias = Path(os.path.abspath(root_alias or root))
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root_alias).as_posix()
    except ValueError:
        reject("game_identity_ambiguous")
    return _read_tree_file(root, relative, code)


def _runtime_dependency_report(source: Path) -> list[dict[str, Any]]:
    package = decode_json(_read_tree_file(source, "components/host-runtime/package.json",
                                          "runtime_dependency_metadata"))
    pyproject = _read_tree_file(source, "python/pyproject.toml", "runtime_dependency_metadata")
    requirements = re.search(rb"(?m)^requires-python\s*=\s*\"([^\"]+)\"", pyproject)
    python_spec = requirements.group(1).decode() if requirements else None
    node_spec = package.get("engines", {}).get("node") if isinstance(package, dict) else None
    if node_spec != ">=20" or python_spec != ">=3.11,<3.12":
        reject("runtime_dependency_metadata_incompatible")

    node_text = run(["node", "--version"], source).strip()
    node_match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?", node_text)
    if not node_match:
        reject("runtime_dependency_version_unreadable")
    node_version = tuple(int(part) for part in node_match.groups())
    if node_version < (20, 0, 0):
        reject("runtime_dependency_incompatible_node")

    python_version = (sys.version_info.major, sys.version_info.minor, sys.version_info.micro)
    if not (3, 11, 0) <= python_version < (3, 12, 0):
        reject("runtime_dependency_incompatible_python")

    runtimeconfig = decode_json(_read_tree_file(
        source, STAGING["collection-tool/sts2-human-annotator.runtimeconfig.json"],
        "runtime_dependency_metadata"))
    options = runtimeconfig.get("runtimeOptions") if isinstance(runtimeconfig, dict) else None
    frameworks = options.get("frameworks") if isinstance(options, dict) else None
    framework = options.get("framework") if isinstance(options, dict) else None
    declared_frameworks = (
        frameworks if isinstance(frameworks, list) else ([framework] if framework else [])
    )
    target = next(
        (entry for entry in declared_frameworks
         if isinstance(entry, dict) and entry.get("name") == "Microsoft.NETCore.App"),
        None,
    )
    target_version = target.get("version") if target else None
    version_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(target_version))
    if not version_match:
        reject("runtime_dependency_metadata_incompatible")
    target_tuple = tuple(int(part) for part in version_match.groups())

    dotnet = run(["dotnet", "--list-runtimes"], source)
    installed = [tuple(int(part) for part in match.groups()) for match in re.finditer(
        r"(?m)^Microsoft\.NETCore\.App (\d+)\.(\d+)\.(\d+)\b", dotnet
    )]
    compatible = [version for version in installed
                  if version[:2] == target_tuple[:2] and version >= target_tuple]
    if not compatible:
        reject("runtime_dependency_missing_dotnet")
    dotnet_version = max(compatible)
    # The release pins compatibility ranges/framework floor, not executable identities.
    return [
        {"name": "node", "observed_version": node_text, "declared": node_spec,
         "compatibility": "compatible", "release_pin": "unknown"},
        {"name": "python", "observed_version": ".".join(map(str, python_version)),
         "declared": python_spec, "compatibility": "compatible", "release_pin": "unknown"},
        {"name": "Microsoft.NETCore.App", "observed_version": ".".join(map(str, dotnet_version)),
         "declared": target_version, "compatibility": "compatible_framework_floor",
         "release_pin": "unknown"},
    ]


def preflight(directory: Path, game: Path) -> dict[str, Any]:
    """Read-only admission check; deliberately does not deploy, start or load anything."""
    prepared = status(directory)
    if not game.is_absolute():
        reject("game_identity_ambiguous")
    try:
        canonical_game_root = game.resolve(strict=True)
    except (OSError, RuntimeError):
        reject("game_identity_ambiguous")
    if not canonical_game_root.is_dir():
        reject("game_identity_ambiguous")
    source = directory / "source"
    archive_manifest, archive_files = verified_archive(directory / "package.zip", directory.name)
    try:
        combination_raw = archive_files["developer-combination.json"]
        bom_raw = archive_files["platform-bom.json"]
        source_combination = _read_tree_file(source, "python/configs/developer/combination-v1.json",
                                             "package_identity")
        source_bom = _read_tree_file(source, "platform-bom.json", "package_identity")
        if combination_raw != source_combination or bom_raw != source_bom:
            reject("package_source_identity_mismatch")
        combination = decode_json(combination_raw)
        bom = decode_json(bom_raw)
        connector_release = decode_json(_read_tree_file(
            source, "components/connector/release-manifest.json", "package_identity"))
        package_identity = validate_package_tuple(combination, bom, connector_release)
        provenance = decode_json(archive_files["collection-tool/game-mod/build-provenance.json"])
    except BoundaryError:
        raise
    except KeyError:
        reject("package_identity_missing")
    if not isinstance(provenance, dict):
        reject("package_identity_incompatible")
    package_identity["combination_archive_entry_sha256"] = archive_manifest["files"][
        "developer-combination.json"
    ]

    doctor_env = dict(os.environ, STS2_GAME_DIR=str(game))
    doctor = decode_json(run(["node", "apps/game-mod/lifecycle.mjs", "doctor"],
                             source, environment=doctor_env))
    if not isinstance(doctor, dict) or doctor.get("status") != "ok":
        reject("game_identity_unavailable")
    installation = doctor.get("installation")
    if not isinstance(installation, dict) or not isinstance(installation.get("game_dir"), str):
        reject("game_identity_ambiguous")
    doctor_game_alias = Path(installation["game_dir"])
    if (not doctor_game_alias.is_absolute() or ".." in doctor_game_alias.parts):
        reject("game_identity_ambiguous")
    try:
        discovered_game_root = doctor_game_alias.resolve(strict=True)
    except (OSError, RuntimeError):
        reject("game_identity_ambiguous")
    if canonical_game_root != discovered_game_root:
        reject("game_identity_ambiguous")
    game_running = doctor.get("game_running")
    if not isinstance(game_running, bool):
        reject("game_state_unavailable")
    target_platform = provenance.get("platform")
    target_architecture = provenance.get("architecture")
    if (not target_platform or not target_architecture
            or target_platform != doctor.get("platform")
            or target_architecture != doctor.get("architecture")):
        reject("target_platform_mismatch")
    native = provenance.get("game")
    if not isinstance(native, dict):
        reject("game_identity_unavailable")
    data_dir, release_info = installation.get("data_dir"), installation.get("release_info")
    if not isinstance(data_dir, str) or not isinstance(release_info, str):
        reject("game_identity_ambiguous")
    file_hashes = (
        ("sts2.dll", native.get("sts2", {}).get("sha256")),
        ("GodotSharp.dll", native.get("godotsharp_sha256")),
        ("0Harmony.dll", native.get("harmony_sha256")),
    )
    for name, expected in file_hashes:
        digest(expected, "kit_install.native_game_identity")
        raw = _game_file(canonical_game_root, str(Path(data_dir) / name), "native_game_file",
                         root_alias=doctor_game_alias)
        if sha(raw) != expected:
            reject("native_game_or_dependency_mismatch")
    release_raw = _game_file(canonical_game_root, release_info, "native_game_file",
                             root_alias=doctor_game_alias)
    if decode_json(release_raw) != native.get("release"):
        reject("native_release_metadata_mismatch")

    dependencies = _runtime_dependency_report(source)
    return {
        "status": "preflight_complete",
        "admission": "unqualified",
        "qualification_blockers": ["exact_system_runtime_identity_not_pinned"],
        "game_running": game_running,
        "package": {"archive_sha256": directory.name,
                    "source_revision": prepared["source_revision"],
                    **package_identity},
        "runtime_candidates": {
            "public_combination_dependency_tuple": package_identity,
            "private_host_candidate": prepared.get("private_host_runtime_identity"),
            "environment_profile_selection": "not_observed",
        },
        "target": {"platform": target_platform, "architecture": target_architecture,
                   "identity_source": "verified_build_provenance_and_read_only_doctor"},
        "game": {"identity": "matched", "identity_scope": "on_disk_files",
                 "loaded_bytes_identity": "not_observed",
                 "version": native.get("release", {}).get("version"),
                 "commit": native.get("release", {}).get("commit"),
                 "files_verified": 4},
        "dependencies": dependencies,
        "effects": {"installed": False, "started": False, "loaded": False},
        "non_claims": ["game_running_is_a_point_in_time_doctor_observation",
                       "running_process_loaded_bytes_are_not_observed",
                       "no_lock_against_concurrent_game_state_change",
                       "no_lock_against_concurrent_local_filesystem_replacement",
                       "deployment_must_recheck_native_game_identity"],
    }


def _launcher_directory(
    *, platform: str | None = None, home: Path | None = None,
) -> Path:
    selected = platform or sys.platform
    user_home = home or Path.home()
    if selected != "darwin":
        reject("launcher_platform_unsupported")
    return user_home / "Library" / "Application Support" / "spireagent" / "workbench"


def _launcher_files(root: Path) -> tuple[Path, Path]:
    return root / "launcher.json", root / "open"


def _check_no_symlink(path: Path, code: str) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            reject(code)


def _launcher_pair(root: Path) -> tuple[Path, Path, bytes | None, int | None,
                                        bytes | None, int | None]:
    """Read the fixed launcher pair without accepting links or partial installs."""
    binding_path, executable_path = _launcher_files(root)
    values: list[tuple[bytes | None, int | None]] = []
    for path, name in zip((binding_path, executable_path), LAUNCHER_FILE_LIMITS, strict=True):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            values.append((None, None))
            continue
        file_stat = path.stat()
        if (not stat.S_ISREG(mode) or file_stat.st_nlink != 1
                or (os.name != "nt" and file_stat.st_uid != os.geteuid())):
            reject("launcher_path_unsafe")
        if path.stat().st_size > LAUNCHER_FILE_LIMITS[name]:
            reject("launcher_file_too_large")
        values.append((path.read_bytes(), stat.S_IMODE(mode)))
    if (values[0][0] is None) != (values[1][0] is None):
        reject("launcher_pair_incomplete")
    return binding_path, executable_path, values[0][0], values[0][1], values[1][0], values[1][1]


def _prepare_launcher_root(root: Path, *, create: bool) -> Path:
    _check_no_symlink(root, "launcher_path_unsafe")
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        reject("launcher_not_installed")
    if not stat.S_ISDIR(root_stat.st_mode):
        reject("launcher_path_unsafe")
    if os.name != "nt":
        if root_stat.st_uid != os.geteuid():
            reject("launcher_path_unsafe")
        if stat.S_IMODE(root_stat.st_mode) & 0o077:
            root.chmod(0o700)
            root_stat = root.lstat()
            if stat.S_IMODE(root_stat.st_mode) & 0o077:
                reject("launcher_path_unsafe")
    lock_path = root / "install.lock"
    try:
        lock_stat = lock_path.lstat()
    except FileNotFoundError:
        return lock_path
    if (not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1
            or (os.name != "nt" and lock_stat.st_uid != os.geteuid())):
        reject("launcher_path_unsafe")
    return lock_path


def _launcher_binding_value(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        reject("launcher_binding_invalid")
    keys = {"schema", "release_directory", "kit_sha256", "source_revision",
            "workbench_sha256", "uv_lock_sha256", "config_path"}
    if not isinstance(value, dict) or set(value) != keys or value.get("schema") != LAUNCHER_SCHEMA:
        reject("launcher_binding_invalid")
    return value


def _launcher_script(directory: Path) -> str:
    source = directory / "source"
    python_root = source / "python"
    python = python_root / ".venv/bin/python"
    tool = python_root / "tools/install_developer_kit.py"
    if not python.is_file() or not tool.is_file():
        reject("launcher_python_unavailable")
    return ("#!/bin/sh\n"
            "set -eu\n"
            "unset PYTHONHOME PYTHONPATH PYTHONUSERBASE VIRTUAL_ENV UV_PROJECT_ENVIRONMENT "
            "UV_WORKING_DIR UV_PROJECT UV_PYTHON UV_CONFIG_FILE UV_ENV_FILE\n"
            f"cd {shlex.quote(str(python_root))}\n"
            f"exec {shlex.quote(str(python))} -I {shlex.quote(str(tool))} launch\n")


def _write_launcher_file(path: Path, contents: bytes, mode: int) -> None:
    if path.is_symlink():
        reject("launcher_path_unsafe")
    descriptor, name = tempfile.mkstemp(prefix=".launcher-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(mode)
        os.replace(temporary, path)
        if os.name != "nt":
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_executable(path: Path, contents: str) -> None:
    _write_launcher_file(path, contents.encode("utf-8"), 0o700)


def _launcher_binding(
    directory: Path, config_path: Path, prepared: dict[str, Any]
) -> dict[str, Any]:
    from spireagent.workbench.developer import ProjectConfig

    if (not config_path.is_absolute() or config_path.is_symlink() or not config_path.is_file()
            or config_path.resolve() != config_path
            or config_path.is_relative_to(directory.resolve())):
        reject("launcher_config_path_invalid")
    config = ProjectConfig.load(config_path, require_current_combination=False)
    archived_combination = prepared.get("developer_combination")
    if archived_combination is None:
        _, files = verified_archive(directory / "package.zip", directory.name)
        archived_combination = decode_json(files["developer-combination.json"])
    if not isinstance(archived_combination, dict) or config.combination != archived_combination:
        reject("launcher_config_combination_mismatch")
    identity = _workbench_identity_for_source(directory / "source")
    if (identity.get("working_tree_clean") is not True
            or identity.get("source_revision") != prepared.get("source_revision")
            or identity.get("uv_lock_sha256") != prepared.get("uv_lock_sha256")
            or not re.fullmatch(r"[a-f0-9]{64}", identity.get("workbench_sha256", ""))):
        reject("launcher_source_identity_mismatch")
    return {
        "schema": LAUNCHER_SCHEMA,
        "release_directory": str(directory.resolve()),
        "kit_sha256": directory.name,
        "source_revision": identity["source_revision"],
        "workbench_sha256": identity["workbench_sha256"],
        "uv_lock_sha256": identity["uv_lock_sha256"],
        "config_path": str(config_path),
    }


def _workbench_identity_for_source(source: Path) -> dict[str, Any]:
    root = source / "python"
    hasher = hashlib.sha256()
    paths = list((root / "spireagent/workbench").glob("*.py")) + [
        path for path in (root / "spireagent/console").glob("*")
        if path.suffix in {".py", ".css", ".js"}
    ]
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            reject("launcher_source_identity_mismatch")
        hasher.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return {
        "workbench_sha256": hasher.hexdigest(),
        "uv_lock_sha256": sha(_read_tree_file(root, "uv.lock", "launcher_source")),
        "source_revision": run(["git", "rev-parse", "HEAD"], source).strip(),
        "working_tree_clean": not bool(run(["git", "status", "--porcelain"], source).strip()),
    }


def _probe_launcher_target(directory: Path, binding: dict[str, Any]) -> None:
    """Verify the target's isolated interpreter and locked imports without launching it."""
    source = directory / "source"
    python_root = source / "python"
    interpreter = python_root / ".venv/bin/python"
    tool = python_root / "tools/install_developer_kit.py"
    script = "\n".join((
        "import importlib.metadata, json, pathlib, runpy, sys",
        "namespace = runpy.run_path(sys.argv[1], run_name='launcher_target_probe')",
        "import sts2_platform_evidence",
        "from spireagent.workbench.developer import tool_identity, evidence_identity",
        "prepared = namespace['status'](pathlib.Path(sys.argv[2]))",
        "distribution = importlib.metadata.distribution('rsgcsg-sts2-platform-evidence')",
        "print(json.dumps({'identity': tool_identity(), 'prefix': sys.prefix,",
        " 'python_version': list(sys.version_info[:3]),",
        " 'source_revision': prepared['source_revision'],",
        " 'uv_lock_sha256': prepared['uv_lock_sha256'],",
        " 'evidence': evidence_identity(prepared['evidence_source_revision']),",
        " 'installed_root': str(distribution.locate_file('')),",
        " 'import_file': sts2_platform_evidence.__file__}))",
    ))
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE", "VIRTUAL_ENV",
                 "UV_PROJECT_ENVIRONMENT", "UV_WORKING_DIR", "UV_PROJECT",
                 "UV_PYTHON", "UV_CONFIG_FILE", "UV_ENV_FILE"):
        environment.pop(name, None)
    try:
        observed = subprocess.run(
            [str(interpreter), "-I", "-c", script, str(tool), str(directory)],
            cwd=python_root, env=environment, capture_output=True, text=True,
            timeout=20, check=True,
        )
        if len(observed.stdout.encode("utf-8")) > 64 * 1024:
            reject("launcher_environment_unverified")
        result = json.loads(observed.stdout)
        identity = result["identity"]
        version = result["python_version"]
        prefix = Path(result["prefix"]).resolve()
        installed_root = Path(result["installed_root"]).resolve()
        imported = Path(result["import_file"]).resolve()
        if (not isinstance(identity, dict) or identity.get("working_tree_clean") is not True
                or any(identity.get(key) != binding[key] for key in
                       ("source_revision", "workbench_sha256", "uv_lock_sha256"))
                or result.get("source_revision") != binding["source_revision"]
                or result.get("uv_lock_sha256") != binding["uv_lock_sha256"]
                or not isinstance(version, list) or len(version) != 3
                or any(type(part) is not int for part in version)
                or version[:2] != [3, 11]
                or identity.get("python") != ".".join(map(str, version))
                or prefix != (python_root / ".venv").resolve()
                or not installed_root.is_relative_to(prefix)
                or not imported.is_relative_to(installed_root)
                or not isinstance(result.get("evidence"), dict)
                or result["evidence"].get("status") != "PASS"):
            reject("launcher_environment_unverified")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        reject("launcher_environment_unverified")


def _publish_launcher_snapshot(snapshot_directory: Path, binding_raw: bytes,
                              open_raw: bytes, binding_mode: int, open_mode: int,
                              manifest: dict[str, Any], forbidden: tuple[Path, ...]) -> str:
    if (not snapshot_directory.is_absolute() or snapshot_directory.exists()
            or snapshot_directory.is_symlink()
            or snapshot_directory.resolve() != snapshot_directory):
        reject("launcher_snapshot_path_invalid")
    parent = snapshot_directory.parent
    _check_no_symlink(parent, "launcher_snapshot_path_invalid")
    if not parent.is_dir():
        reject("launcher_snapshot_path_invalid")
    parent_stat = parent.stat()
    if os.name != "nt" and (
        parent_stat.st_uid != os.geteuid() or stat.S_IMODE(parent_stat.st_mode) & 0o022
    ):
        reject("launcher_snapshot_path_invalid")
    if any(snapshot_directory.is_relative_to(path.resolve()) for path in forbidden):
        reject("launcher_snapshot_path_invalid")
    stage = Path(tempfile.mkdtemp(prefix=".launcher-snapshot-", dir=parent))
    stage.chmod(0o700)
    try:
        _write_launcher_file(stage / "launcher.json", binding_raw, binding_mode)
        _write_launcher_file(stage / "open", open_raw, open_mode)
        manifest_raw = json_bytes(manifest)
        _write_launcher_file(stage / "snapshot.json", manifest_raw, 0o600)
        if snapshot_directory.exists() or snapshot_directory.is_symlink():
            reject("launcher_snapshot_path_invalid")
        os.replace(stage, snapshot_directory)
        if os.name != "nt":
            parent_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return sha(manifest_raw)


def backup_launcher(owner_directory: Path, snapshot_directory: Path,
                    expected_binding_sha256: str, expected_open_sha256: str) -> dict[str, Any]:
    """Archive an exact historical pair; this snapshot does not grant launch eligibility."""
    digest(expected_binding_sha256, "kit_install.launcher_binding")
    digest(expected_open_sha256, "kit_install.launcher_open")
    root = _launcher_directory()
    lock_path = _prepare_launcher_root(root, create=False)
    from spireagent.workbench.developer_server import instance_lock

    with instance_lock(lock_path):
        if os.name != "nt":
            lock_path.chmod(0o600)
        status(owner_directory)
        binding_path, executable_path, raw, binding_mode, script, script_mode = (
            _launcher_pair(root)
        )
        if raw is None or script is None:
            reject("launcher_not_installed")
        if (sha(raw) != expected_binding_sha256 or sha(script) != expected_open_sha256):
            reject("launcher_pair_changed")
        binding = _launcher_binding_value(raw)
        bound_directory = Path(binding["release_directory"])
        if (not bound_directory.is_absolute() or bound_directory.resolve() != bound_directory
                or bound_directory.name != binding.get("kit_sha256")):
            reject("launcher_release_mismatch")
        prepared = status(bound_directory)
        identity = _workbench_identity_for_source(bound_directory / "source")
        if (binding.get("kit_sha256") != bound_directory.name
                or binding.get("source_revision") != prepared.get("source_revision")
                or binding.get("uv_lock_sha256") != prepared.get("uv_lock_sha256")
                or binding.get("source_revision") != identity["source_revision"]
                or binding.get("uv_lock_sha256") != identity["uv_lock_sha256"]
                or binding.get("workbench_sha256") != identity["workbench_sha256"]
                or identity.get("working_tree_clean") is not True):
            reject("launcher_source_identity_mismatch")
        try:
            config_path = Path(binding["config_path"])
            if (not config_path.is_absolute() or config_path.is_symlink()
                    or config_path.resolve() != config_path or not config_path.is_file()
                    or config_path.is_relative_to(bound_directory.resolve())):
                reject("launcher_config_path_invalid")
        except (TypeError, OSError):
            reject("launcher_config_path_invalid")
        manifest = {
            "schema": LAUNCHER_SNAPSHOT_SCHEMA,
            "launchable": False,
            "restore_eligibility": "not_granted",
            "release_directory": str(bound_directory.resolve()),
            "kit_sha256": bound_directory.name,
            "source_revision": identity["source_revision"],
            "workbench_sha256": identity["workbench_sha256"],
            "uv_lock_sha256": identity["uv_lock_sha256"],
            "config_path": str(config_path),
            "files": {
                "launcher.json": {"sha256": sha(raw), "mode": binding_mode},
                "open": {"sha256": sha(script), "mode": script_mode},
            },
        }
        manifest_sha = _publish_launcher_snapshot(
            snapshot_directory, raw, script, binding_mode or 0o600, script_mode or 0o700,
            manifest, (bound_directory, owner_directory, root),
        )
    return {"status": "launcher_snapshot_created", "launchable": False,
            "snapshot_directory": str(snapshot_directory),
            "snapshot_manifest_sha256": manifest_sha}


def prepare_launcher_target(directory: Path, config_path: Path,
                            snapshot_directory: Path) -> dict[str, Any]:
    prepared = status(directory)
    if prepared.get("workbench_launcher_schema") != LAUNCHER_SCHEMA:
        reject("workbench_launcher_not_in_kit")
    binding = _launcher_binding(directory, config_path, prepared)
    binding_raw = json_bytes(binding)
    open_raw = _launcher_script(directory.resolve()).encode("utf-8")
    _probe_launcher_target(directory, binding)
    manifest = {
        "schema": LAUNCHER_SNAPSHOT_SCHEMA,
        "launchable": True,
        "restore_eligibility": "validated_prepared_target",
        "release_directory": str(directory.resolve()),
        "kit_sha256": directory.name,
        "source_revision": binding["source_revision"],
        "workbench_sha256": binding["workbench_sha256"],
        "uv_lock_sha256": binding["uv_lock_sha256"],
        "config_path": binding["config_path"],
        "files": {
            "launcher.json": {"sha256": sha(binding_raw), "mode": 0o600},
            "open": {"sha256": sha(open_raw), "mode": 0o700},
        },
    }
    manifest_sha = _publish_launcher_snapshot(
        snapshot_directory, binding_raw, open_raw, 0o600, 0o700, manifest,
        (directory, _launcher_directory()),
    )
    return {"status": "launcher_target_prepared", "launchable": True,
            "snapshot_directory": str(snapshot_directory),
            "snapshot_manifest_sha256": manifest_sha}


def _read_launcher_snapshot(snapshot_directory: Path,
                            expected_manifest_sha256: str) -> tuple[dict[str, Any], bytes, bytes]:
    if (not snapshot_directory.is_absolute() or snapshot_directory.is_symlink()
            or snapshot_directory.resolve() != snapshot_directory):
        reject("launcher_snapshot_path_invalid")
    _check_no_symlink(snapshot_directory, "launcher_snapshot_path_invalid")
    try:
        directory_stat = snapshot_directory.lstat()
    except FileNotFoundError:
        reject("launcher_snapshot_missing")
    if (not stat.S_ISDIR(directory_stat.st_mode)
            or (os.name != "nt" and (directory_stat.st_uid != os.geteuid()
                                     or stat.S_IMODE(directory_stat.st_mode) & 0o077))):
        reject("launcher_snapshot_path_invalid")
    if {path.name for path in snapshot_directory.iterdir()} != {
        "launcher.json", "open", "snapshot.json"
    }:
        reject("launcher_snapshot_inventory_invalid")
    values = {}
    for name, limit in {**LAUNCHER_FILE_LIMITS, "snapshot.json": 64 * 1024}.items():
        path = snapshot_directory / name
        if path.is_symlink():
            reject("launcher_snapshot_path_invalid")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            reject("launcher_snapshot_inventory_invalid")
        try:
            file_stat = os.fstat(descriptor)
            expected_mode = 0o700 if name == "open" else 0o600
            if (not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1
                    or file_stat.st_size > limit
                    or (os.name != "nt" and (
                        file_stat.st_uid != os.geteuid()
                        or stat.S_IMODE(file_stat.st_mode) != expected_mode))):
                reject("launcher_snapshot_path_invalid")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(limit + 1)
            if len(raw) > limit:
                reject("launcher_snapshot_path_invalid")
            values[name] = raw
        finally:
            os.close(descriptor)
    manifest_raw = values["snapshot.json"]
    if sha(manifest_raw) != expected_manifest_sha256:
        reject("launcher_snapshot_changed")
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        reject("launcher_snapshot_invalid")
    expected_fields = {"schema", "launchable", "restore_eligibility", "release_directory",
                       "kit_sha256", "source_revision", "workbench_sha256", "uv_lock_sha256",
                       "config_path", "files"}
    if (not isinstance(manifest, dict) or set(manifest) != expected_fields
            or manifest.get("schema") != LAUNCHER_SNAPSHOT_SCHEMA
            or manifest.get("launchable") is not True
            or manifest.get("restore_eligibility") != "validated_prepared_target"):
        reject("launcher_snapshot_not_launchable")
    for key in ("release_directory", "config_path"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            reject("launcher_snapshot_invalid")
    for key, length in (("kit_sha256", 64), ("source_revision", 40),
                        ("workbench_sha256", 64), ("uv_lock_sha256", 64)):
        digest(manifest.get(key), "kit_install.launcher_snapshot", length=length)
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {"launcher.json", "open"}:
        reject("launcher_snapshot_invalid")
    for name in ("launcher.json", "open"):
        record = files[name]
        expected_mode = 0o600 if name == "launcher.json" else 0o700
        if (not isinstance(record, dict) or set(record) != {"sha256", "mode"}
                or type(record.get("mode")) is not int
                or record.get("mode") != expected_mode
                or sha(values[name]) != record.get("sha256")):
            reject("launcher_snapshot_changed")
    return manifest, values["launcher.json"], values["open"]


def _write_launcher_pair(root: Path, binding_raw: bytes, open_raw: bytes,
                         binding_mode: int, open_mode: int) -> None:
    binding_path, open_path = _launcher_files(root)
    _, _, old_binding, old_binding_mode, old_open, old_open_mode = _launcher_pair(root)
    try:
        _write_launcher_file(binding_path, binding_raw, binding_mode)
        _write_launcher_file(open_path, open_raw, open_mode)
    except Exception:
        failures = []
        for path, content, mode in (
            (binding_path, old_binding, old_binding_mode),
            (open_path, old_open, old_open_mode),
        ):
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    _write_launcher_file(path, content, mode or 0o600)
            except Exception as error:
                failures.append(error)
        if failures:
            reject("launcher_recovery_required")
        raise


def restore_launcher(snapshot_directory: Path, expected_manifest_sha256: str,
                     expected_binding_sha256: str, expected_open_sha256: str) -> dict[str, Any]:
    digest(expected_manifest_sha256, "kit_install.launcher_snapshot")
    digest(expected_binding_sha256, "kit_install.launcher_binding")
    digest(expected_open_sha256, "kit_install.launcher_open")
    manifest, binding_raw, open_raw = _read_launcher_snapshot(
        snapshot_directory, expected_manifest_sha256,
    )
    directory = Path(manifest["release_directory"])
    if (not directory.is_absolute() or directory.resolve() != directory
            or directory.name != manifest.get("kit_sha256")):
        reject("launcher_release_mismatch")
    prepared = status(directory)
    if prepared.get("workbench_launcher_schema") != LAUNCHER_SCHEMA:
        reject("workbench_launcher_not_in_kit")
    identity = _workbench_identity_for_source(directory / "source")
    if (identity.get("working_tree_clean") is not True
            or manifest.get("source_revision") != prepared.get("source_revision")
            or manifest.get("source_revision") != identity.get("source_revision")
            or manifest.get("uv_lock_sha256") != prepared.get("uv_lock_sha256")
            or manifest.get("uv_lock_sha256") != identity.get("uv_lock_sha256")
            or manifest.get("workbench_sha256") != identity.get("workbench_sha256")):
        reject("launcher_source_identity_mismatch")
    config_path = Path(manifest.get("config_path", ""))
    if (not config_path.is_absolute() or config_path.is_symlink()
            or config_path.resolve() != config_path or not config_path.is_file()
            or config_path.is_relative_to(directory)):
        reject("launcher_config_path_invalid")
    binding = _launcher_binding(directory, config_path, prepared)
    if (binding_raw != json_bytes(binding)
            or open_raw != _launcher_script(directory).encode("utf-8")):
        reject("launcher_snapshot_target_mismatch")
    _probe_launcher_target(directory, binding)
    root = _launcher_directory()
    lock_path = _prepare_launcher_root(root, create=False)
    from spireagent.workbench.developer_server import instance_lock

    with instance_lock(lock_path):
        if os.name != "nt":
            lock_path.chmod(0o600)
        _, _, current_binding, _, current_open, _ = _launcher_pair(root)
        if current_binding is None or current_open is None:
            reject("launcher_not_installed")
        if (sha(current_binding) != expected_binding_sha256
                or sha(current_open) != expected_open_sha256):
            reject("launcher_pair_changed")
        _write_launcher_pair(root, binding_raw, open_raw, 0o600, 0o700)
    return {"status": "launcher_restored", "launchable": True,
            "release_directory": str(directory), "kit_sha256": directory.name}


def _install_open_launcher(
    directory: Path, config_path: Path, prepared: dict[str, Any], *, platform: str | None = None,
    expected_binding_sha256: str | None = None, expected_open_sha256: str | None = None,
) -> None:
    if (platform or sys.platform) != "darwin":
        reject("launcher_platform_unsupported")
    if prepared.get("workbench_launcher_schema") != LAUNCHER_SCHEMA:
        reject("workbench_launcher_not_in_kit")
    if (expected_binding_sha256 is None) != (expected_open_sha256 is None):
        reject("launcher_replacement_arguments_invalid")
    root = _launcher_directory(platform=platform)
    binding_path, executable_path = _launcher_files(root)
    lock_path = _prepare_launcher_root(root, create=True)
    script = _launcher_script(directory.resolve())
    binding = _launcher_binding(directory, config_path.resolve(), prepared)
    # Rebinding is an explicit installer operation against exact reviewed bytes.
    # Both ordinary installation and replacement share this owner lock.
    from spireagent.workbench.developer import atomic_json
    from spireagent.workbench.developer_server import instance_lock

    if expected_binding_sha256 is not None:
        digest(expected_binding_sha256, "kit_install.launcher_binding")
        digest(expected_open_sha256, "kit_install.launcher_open")
    with instance_lock(lock_path):
        if os.name != "nt":
            lock_path.chmod(0o600)
        binding_path, executable_path, raw, old_binding_mode, old_script, old_mode = (
            _launcher_pair(root)
        )
        if raw is not None:
            current = _launcher_binding_value(raw)
            if expected_binding_sha256 is not None:
                if (sha(raw) != expected_binding_sha256
                        or sha(old_script or b"") != expected_open_sha256):
                    reject("launcher_pair_changed")
            elif current.get("config_path") != binding["config_path"]:
                reject("launcher_config_binding_mismatch")
        elif expected_binding_sha256 is not None:
            reject("launcher_not_installed")
        try:
            atomic_json(binding_path, binding)
            _write_executable(executable_path, script)
        except Exception:
            rollback_errors = []
            for path, contents, mode in (
                (binding_path, raw, old_binding_mode),
                (executable_path, old_script, old_mode),
            ):
                try:
                    if contents is None:
                        path.unlink(missing_ok=True)
                    else:
                        _write_launcher_file(path, contents, mode or 0o600)
                except Exception as error:
                    rollback_errors.append(error)
            if rollback_errors:
                reject("launcher_recovery_required")
            raise


def launch_workbench() -> dict[str, Any]:
    """Validate this exact prepared release and open only its installed profile."""
    if sys.platform != "darwin":
        reject("launcher_platform_unsupported")
    from spireagent.workbench.developer import ProjectConfig, tool_identity
    from spireagent.workbench.developer_server import open_project

    directory = Path(__file__).resolve().parents[3]
    root = _launcher_directory()
    binding_path, _ = _launcher_files(root)
    if binding_path.is_symlink() or not binding_path.is_file():
        reject("launcher_not_installed")
    try:
        binding = json.loads(binding_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        reject("launcher_binding_invalid")
    keys = {"schema", "release_directory", "kit_sha256", "source_revision",
            "workbench_sha256", "uv_lock_sha256", "config_path"}
    if (not isinstance(binding, dict) or set(binding) != keys
            or binding.get("schema") != LAUNCHER_SCHEMA):
        reject("launcher_binding_invalid")
    if (binding["release_directory"] != str(directory)
            or binding["kit_sha256"] != directory.name
            or not re.fullmatch(r"[a-f0-9]{64}", str(binding["kit_sha256"]))):
        reject("launcher_release_mismatch")
    prepared = status(directory)
    if prepared.get("workbench_launcher_schema") != LAUNCHER_SCHEMA:
        reject("workbench_launcher_not_in_kit")
    identity = tool_identity()
    if (identity.get("working_tree_clean") is not True
            or identity.get("source_revision") != prepared["source_revision"]
            or identity.get("uv_lock_sha256") != prepared["uv_lock_sha256"]
            or any(binding.get(key) != identity.get(key) for key in
                   ("source_revision", "workbench_sha256", "uv_lock_sha256"))):
        reject("launcher_source_identity_mismatch")
    expected = identity
    config_path = Path(binding["config_path"])
    if (not config_path.is_absolute() or config_path.is_symlink() or not config_path.is_file()
            or config_path.resolve() != config_path
            or config_path.is_relative_to(directory)):
        reject("launcher_config_path_invalid")
    ProjectConfig.load(config_path)
    opened = open_project(config_path, browser=False, expected_identity=expected)
    return {"status": "running", "instance_id": opened["instance_id"],
            "gameplay_started": False}


def deploy(directory: Path, game: Path) -> dict[str, Any]:
    result = status(directory)
    if not game.is_absolute():
        reject("absolute_game_directory_required")
    source = directory / "source"
    env = dict(os.environ, STS2_GAME_DIR=str(game))
    doctor = json.loads(
        run(["node", "apps/game-mod/lifecycle.mjs", "doctor"], source, environment=env)
    )
    if doctor.get("status") != "ok" or doctor.get("game_running") is not False:
        reject("game_must_be_closed_and_discovered")
    provenance = doctor["build_provenance"]
    if (
        provenance["platform"] != doctor["platform"]
        or provenance["architecture"] != doctor["architecture"]
    ):
        reject("release_platform_mismatch")
    native = provenance["game"]
    data = Path(doctor["installation"]["data_dir"])
    for name, expected in (
        ("sts2.dll", native["sts2"]["sha256"]),
        ("GodotSharp.dll", native["godotsharp_sha256"]),
        ("0Harmony.dll", native["harmony_sha256"]),
    ):
        if sha((data / name).read_bytes()) != expected:
            reject("native_game_or_dependency_mismatch")
    if decode_json(Path(doctor["installation"]["release_info"]).read_bytes()) != native["release"]:
        reject("native_release_metadata_mismatch")
    # The owner rechecks running processes, source and artifact, and retains rollback.
    installed = json.loads(
        run(["node", "apps/game-mod/lifecycle.mjs", "deploy"], source, environment=env)
    )
    return {
        **result,
        "installed": installed,
        "loaded": "not_checked",
        "next": "cold launch and verify-loaded through the same native lifecycle",
    }


def register(directory: Path, config: Path) -> dict[str, Any]:
    result = status(directory)
    source = directory / "source"
    extras = _environment_extras(result)
    # Execute the selected release owner, not the engineering checkout's environment.
    args = [
        "uv",
        "run",
        "--project",
        "python",
        "--locked",
        *extras,
        "python",
        "-m",
        "spireagent.workbench",
        "project",
        "collection-tool",
        "--config",
        str(config),
        "--tool-directory",
        str(directory / "kit/collection-tool"),
        "--tool-release-id",
        result["tool_release_id"],
    ]
    return dict(json.loads(run(args, source)))


def _environment_extras(prepared: dict[str, Any]) -> list[str]:
    """Map the verified effective profile to the centrally approved fixed extras."""
    try:
        return extras_for_python_environment_profile(prepared[PROFILE_FIELD])
    except (KeyError, ValueError):
        reject("python_environment_profile_invalid")


def initialize(
    directory: Path, config_path: Path, *, defer_launcher: bool = False,
) -> dict[str, Any]:
    from contextlib import nullcontext

    from spireagent.workbench.developer import ProjectConfig
    from spireagent.workbench.developer_server import instance_lock

    if not config_path.is_absolute() or config_path.is_symlink():
        reject("absolute_private_profile_path_required")
    with instance_lock(directory / "initialize.lock"):
        prepared = status(directory)
        source = directory / "source"
        extras = _environment_extras(prepared)
        if (not config_path.exists() and
                (any(prepared.get(pair[4]) == "bundled_installation_not_checked"
                     for pair in KIT_RUNTIME_PAIRS.values())
                 or prepared.get(PROFILE_FIELD) == "cloud-local-models"
                 or prepared.get("private_host_runtime") == "bundled_installation_not_checked"
                 or prepared.get("workbench_launcher_schema") == LAUNCHER_SCHEMA)):
            # Let the selected release own the profile and its default private state.
            report = json.loads(run([
                "uv", "run", "--project", "python", "--locked", *extras,
                "python", "-m", "spireagent.workbench", "project", "setup",
                "--skip-install", "--config", str(config_path),
                "--state-dir", str(config_path.parent),
            ], source))
            if report.get("status") != "configured":
                reject("project_profile_setup_failed")
        config = (
            ProjectConfig.load(config_path, require_current_combination=False)
            if config_path.exists() else None
        )
        # Reuse the running Workbench's OS lock, not a second process tracker.
        with instance_lock(config.state_dir / "instance.lock") if config else nullcontext():
            status(directory)
            run(["npm", "ci"], source)
            # Workbench's transport SDKs are a separate locked consumer environment.
            run(["npm", "ci", "--prefix", "python"], source)
            run(["uv", "sync", "--project", "python", "--locked", *extras], source)
            result = status(directory)
        if result.get("private_host_runtime") == "bundled_installation_not_checked":
            staged_profile = _read_tree_file(
                source, PRIVATE_HOST_PROFILE_DESTINATION,
                "staged_private_host")
            staged_archive = _read_tree_file(
                source, PRIVATE_HOST_ARCHIVE_DESTINATION,
                "staged_private_host")
            bom_raw = _read_tree_file(source, "platform-bom.json", "private_host_bom")
            stage_private_host_runtime(staged_profile, staged_archive, bom_raw, source)
            result = status(directory)
        if result.get("text_runtime") == "bundled_installation_not_checked":
            # The selected CLI takes this same lock and checks Runtime liveness.
            report = json.loads(run([
                "uv", "run", "--project", "python", "--locked", *extras,
                "python", "-m", "spireagent.workbench", "project", "model",
                "--config", str(config_path), "--action", "install-runtime",
                "--runtime-profile", "text-menu-v1", "--runtime-archive",
                str(source / TEXT_ARCHIVE_DESTINATION),
            ], source))
            if report.get("status") != "runtime_installed":
                reject("text_runtime_install_failed")
            result = status(directory)
            result["text_runtime"] = "installed_verified_by_runtime_owner"
        if result.get("m2_runtime") == "bundled_installation_not_checked":
            report = json.loads(run([
                "uv", "run", "--project", "python", "--locked", *extras,
                "python", "-m", "spireagent.workbench", "project", "model",
                "--config", str(config_path), "--action", "install-runtime",
                "--runtime-profile", "text-menu-m2-v1", "--runtime-archive",
                str(source / M2_ARCHIVE_DESTINATION),
            ], source))
            if report.get("status") != "runtime_installed":
                reject("m2_runtime_install_failed")
            result = status(directory)
            result["m2_runtime"] = "installed_verified_by_runtime_owner"
        if result.get("m2_v2_runtime") == "bundled_installation_not_checked":
            report = json.loads(run([
                "uv", "run", "--project", "python", "--locked", *extras,
                "python", "-m", "spireagent.workbench", "project", "model",
                "--config", str(config_path), "--action", "install-runtime",
                "--runtime-profile", "text-menu-m2-v2", "--runtime-archive",
                str(source / KIT_RUNTIME_PAIRS["text-menu-m2-v2"][3]),
            ], source))
            if report.get("status") != "runtime_installed":
                reject("m2_v2_runtime_install_failed")
            result = status(directory)
            result["m2_v2_runtime"] = "installed_verified_by_runtime_owner"
        result["environment"] = "initialized"
        if (result.get("workbench_launcher_schema") == LAUNCHER_SCHEMA
                and sys.platform == "darwin"):
            if defer_launcher:
                result["workbench_launcher"] = "deferred"
                return result
            python = source / "python/.venv/bin/python"
            if os.name == "nt":
                python = source / "python/.venv/Scripts/python.exe"
            tool = source / "python/tools/install_developer_kit.py"
            environment = dict(os.environ)
            for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE", "VIRTUAL_ENV",
                         "UV_PROJECT_ENVIRONMENT", "UV_WORKING_DIR", "UV_PROJECT",
                         "UV_PYTHON", "UV_CONFIG_FILE", "UV_ENV_FILE"):
                environment.pop(name, None)
            try:
                bound = subprocess.run(
                    [str(python), "-I", str(tool), "install-launcher", "--config",
                     str(config_path.resolve())],
                    cwd=source / "python", env=environment, capture_output=True,
                    text=True, timeout=30, check=True,
                )
                owner_result = json.loads(bound.stdout)
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                reject("release_launcher_install_failed")
            if owner_result.get("status") != "launcher_installed":
                reject("release_launcher_install_failed")
            result["workbench_launcher"] = "installed"
        elif result.get("workbench_launcher_schema") == LAUNCHER_SCHEMA:
            result["workbench_launcher"] = "unsupported_platform"
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=(
            "plan", "prepare", "status", "preflight", "initialize", "deploy", "register",
            "launch", "install-launcher", "backup-launcher", "prepare-launcher-target",
            "restore-launcher",
        )
    )
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--releases", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--game-directory", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--expected-launcher-binding-sha256")
    parser.add_argument("--expected-open-sha256")
    parser.add_argument("--defer-launcher", action="store_true")
    parser.add_argument("--snapshot-directory", type=Path)
    parser.add_argument("--snapshot-manifest-sha256")
    args = parser.parse_args()
    try:
        launcher_commands = {"install-launcher", "backup-launcher", "restore-launcher"}
        if (args.expected_launcher_binding_sha256 is not None
                and args.command not in launcher_commands):
            reject("launcher_replacement_arguments_invalid")
        if (args.expected_open_sha256 is not None
                and args.command not in launcher_commands):
            reject("launcher_replacement_arguments_invalid")
        if (args.expected_launcher_binding_sha256 is None) != (args.expected_open_sha256 is None):
            reject("launcher_replacement_arguments_invalid")
        if args.defer_launcher and args.command != "initialize":
            reject("defer_launcher_arguments_invalid")
        if (args.snapshot_directory is not None
                and args.command not in {"backup-launcher", "prepare-launcher-target",
                                         "restore-launcher"}):
            reject("launcher_snapshot_arguments_invalid")
        if (args.snapshot_manifest_sha256 is not None
                and args.command != "restore-launcher"):
            reject("launcher_snapshot_arguments_invalid")
        if args.command == "launch":
            if any(value is not None for value in (
                args.archive, args.sha256, args.releases, args.directory,
                args.game_directory, args.config,
            )):
                reject("launch_arguments_not_allowed")
            result = launch_workbench()
        elif args.command == "install-launcher":
            if (any(value is not None for value in (
                    args.archive, args.sha256, args.releases, args.directory,
                    args.game_directory,
                )) or args.config is None):
                reject("launcher_arguments_invalid")
            directory = Path(__file__).resolve().parents[3]
            prepared = status(directory)
            _install_open_launcher(
                directory, args.config, prepared,
                expected_binding_sha256=args.expected_launcher_binding_sha256,
                expected_open_sha256=args.expected_open_sha256,
            )
            result = {"status": "launcher_installed"}
        elif args.command == "backup-launcher":
            if (any(value is not None for value in (
                    args.archive, args.sha256, args.releases, args.game_directory,
                    args.config, args.directory, args.snapshot_manifest_sha256,
                )) or args.snapshot_directory is None
                    or args.expected_launcher_binding_sha256 is None):
                reject("launcher_backup_arguments_invalid")
            result = backup_launcher(
                Path(__file__).resolve().parents[3], args.snapshot_directory,
                args.expected_launcher_binding_sha256, args.expected_open_sha256,
            )
        elif args.command == "prepare-launcher-target":
            if (any(value is not None for value in (
                    args.archive, args.sha256, args.releases, args.game_directory,
                    args.expected_launcher_binding_sha256, args.expected_open_sha256,
                    args.snapshot_manifest_sha256,
                )) or args.directory is None or args.config is None
                    or args.snapshot_directory is None):
                reject("launcher_target_arguments_invalid")
            result = prepare_launcher_target(args.directory, args.config,
                                             args.snapshot_directory)
        elif args.command == "restore-launcher":
            if (any(value is not None for value in (
                    args.archive, args.sha256, args.releases, args.directory,
                    args.game_directory, args.config,
                )) or args.snapshot_directory is None
                    or args.snapshot_manifest_sha256 is None
                    or args.expected_launcher_binding_sha256 is None):
                reject("launcher_restore_arguments_invalid")
            result = restore_launcher(
                args.snapshot_directory, args.snapshot_manifest_sha256,
                args.expected_launcher_binding_sha256, args.expected_open_sha256,
            )
        elif args.command in {"plan", "prepare"}:
            if args.archive is None or args.sha256 is None or args.releases is None:
                reject("archive_hash_release_root_required")
            if args.command == "prepare":
                result = prepare(args.archive, args.sha256, args.releases)
            else:
                manifest, _ = verified_archive(args.archive, args.sha256)
                result = {
                    "status": "plan_only",
                    "composition": manifest,
                    "target": str(args.releases / args.sha256),
                    "unchanged": ["game", "profile", "queue", "cloud"],
                    "steps": [
                        "verify bytes",
                        "fixed source checkout",
                        "stage existing binaries",
                        "initialize locked environment",
                        "native owner install/cold-load",
                        "account/consent or existing queue upgrade",
                    ],
                }
        else:
            if args.directory is None:
                reject("directory_required")
            if args.command == "preflight":
                if args.game_directory is None:
                    reject("game_directory_required")
                result = preflight(args.directory, args.game_directory)
            else:
                result = status(args.directory)
            if args.command == "deploy":
                if args.game_directory is None:
                    reject("game_directory_required")
                result = deploy(args.directory, args.game_directory)
            if args.command == "register":
                if args.config is None or not args.config.is_absolute():
                    reject("absolute_existing_profile_required")
                result = register(args.directory, args.config)
            if args.command == "initialize":
                if args.config is None:
                    reject("profile_path_required_for_stopped_workbench_check")
                result = initialize(args.directory, args.config,
                                    defer_launcher=args.defer_launcher)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ) as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": error.code
                    if isinstance(error, BoundaryError)
                    else type(error).__name__,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
