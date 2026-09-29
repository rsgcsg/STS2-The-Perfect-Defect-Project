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
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from sts2_platform_evidence.collection_tool import CollectionTool

from spireagent.json_boundary import BoundaryError, decode_json, digest
from spireagent.workbench.kit_runtime import (
    TEXT_ARCHIVE_DESTINATION,
    TEXT_RUNTIME_ARCHIVE,
    TEXT_RUNTIME_DESTINATION,
    TEXT_RUNTIME_PROFILE,
    text_runtime_pin,
)

REPOSITORY = "https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project.git"
LIMIT = 256 * 1024 * 1024
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


def text_runtime_files(manifest: dict[str, Any], files: dict[str, bytes]) -> bool:
    identity = manifest.get("text_runtime")
    if identity is None:
        if TEXT_RUNTIME_PROFILE in files or TEXT_RUNTIME_ARCHIVE in files:
            reject("text_runtime_inventory_incomplete")
        return False
    if (not isinstance(identity, dict)
            or set(identity) != {"profile_sha256", "archive_sha256"}
            or TEXT_RUNTIME_PROFILE not in files
            or TEXT_RUNTIME_ARCHIVE not in files):
        reject("text_runtime_inventory_incomplete")
    for field, name in (("profile_sha256", TEXT_RUNTIME_PROFILE),
                        ("archive_sha256", TEXT_RUNTIME_ARCHIVE)):
        digest(identity[field], "kit_install.text_runtime_identity")
        if identity[field] != sha(files[name]):
            reject("text_runtime_inventory_mismatch")
    text_runtime_pin(files[TEXT_RUNTIME_PROFILE], files[TEXT_RUNTIME_ARCHIVE])
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
    if not isinstance(manifest, dict) or manifest.get("schema") != "spireagent/developer-kit-v1":
        reject("unsupported_kit_schema")
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
    text_runtime_files(manifest, files)
    return manifest, files


def run(command: list[str], cwd: Path, *, environment: dict[str, str] | None = None) -> str:
    executable = shutil.which(command[0])
    if executable is None:
        reject("required_program_missing_" + command[0])
    args = [str(executable), *command[1:]]
    if os.name == "nt" and Path(str(executable)).suffix.lower() in {".cmd", ".bat"}:
        args = [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline(args),
        ]
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
        if text_runtime_files(manifest, files):
            for name, relative in ((TEXT_RUNTIME_PROFILE, TEXT_RUNTIME_DESTINATION),
                                   (TEXT_RUNTIME_ARCHIVE, TEXT_ARCHIVE_DESTINATION)):
                destination = source / relative
                if any(p.is_symlink() for p in destination.parents):
                    reject("text_runtime_staging_path_unsafe")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() or destination.is_symlink():
                    reject("text_runtime_staging_exists")
                destination.write_bytes(files[name])
        if run(["git", "status", "--porcelain"], source).strip():
            reject("staging_changed_tracked_source")
        # Rename before uv: virtualenv interpreter paths must use the permanent location.
        stage.rename(target)
    return status(target)


def status(directory: Path) -> dict[str, Any]:
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        reject("release_path_unsafe")
    manifest, files = verified_archive(directory / "package.zip", directory.name)
    if (directory / "kit/combination.json").read_bytes() != files["combination.json"]:
        reject("prepared_manifest_changed")
    source = directory / "source"
    if run(["git", "rev-parse", "HEAD"], source).strip() != manifest["stpd_source_revision"]:
        reject("prepared_source_changed")
    if run(["git", "status", "--porcelain"], source).strip():
        reject("prepared_source_dirty")
    for name, expected in manifest["files"].items():
        p = directory / "kit" / name
        if any(a.is_symlink() for a in (p, *p.parents)) or sha(p.read_bytes()) != expected:
            reject("prepared_kit_changed")
    for name, relative in STAGING.items():
        if sha((source / relative).read_bytes()) != manifest["files"][name]:
            reject("staged_native_changed")
    if text_runtime_files(manifest, files):
        for name, relative in ((TEXT_RUNTIME_PROFILE, TEXT_RUNTIME_DESTINATION),
                               (TEXT_RUNTIME_ARCHIVE, TEXT_ARCHIVE_DESTINATION)):
            staged = source / relative
            if (any(p.is_symlink() for p in (staged, *staged.parents))
                    or sha(staged.read_bytes()) != manifest["files"][name]):
                reject("staged_text_runtime_changed")
    CollectionTool(directory / "kit/collection-tool", manifest["collection_tool_release_id"])
    return {
        "status": "prepared",
        "source_revision": manifest["stpd_source_revision"],
        "tool_release_id": manifest["collection_tool_release_id"],
        "mod_sha256": manifest["mod_sha256"],
        "directory": str(directory),
        "installed": "not_checked",
        "loaded": "not_checked",
        "next": "initialize; then follow the native owner deploy/cold-load steps",
        "text_runtime": ("bundled_installation_not_checked" if "text_runtime" in manifest
                         else "not_bundled"),
    }


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
    # Execute the selected release owner, not the engineering checkout's environment.
    args = [
        "uv",
        "run",
        "--project",
        "python",
        "--locked",
        "--extra",
        "cloud",
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


def initialize(directory: Path, config_path: Path) -> dict[str, Any]:
    from contextlib import nullcontext

    from spireagent.workbench.developer import ProjectConfig
    from spireagent.workbench.developer_server import instance_lock

    if not config_path.is_absolute() or config_path.is_symlink():
        reject("absolute_private_profile_path_required")
    with instance_lock(directory / "initialize.lock"):
        prepared = status(directory)
        source = directory / "source"
        if (prepared.get("text_runtime") == "bundled_installation_not_checked"
                and not config_path.exists()):
            # New members do not yet have a selection or even a project profile.
            # Let the selected release own the profile and its default private state.
            report = json.loads(run([
                "uv", "run", "--project", "python", "--locked", "--extra", "cloud",
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
            run(["uv", "sync", "--project", "python", "--locked", "--extra", "cloud"], source)
            result = status(directory)
        if result.get("text_runtime") == "bundled_installation_not_checked":
            # The selected CLI takes this same lock and checks Runtime liveness.
            report = json.loads(run([
                "uv", "run", "--project", "python", "--locked", "--extra", "cloud",
                "python", "-m", "spireagent.workbench", "project", "model",
                "--config", str(config_path), "--action", "install-runtime",
                "--runtime-profile", "text-menu-v1", "--runtime-archive",
                str(source / TEXT_ARCHIVE_DESTINATION),
            ], source))
            if report.get("status") != "runtime_installed":
                reject("text_runtime_install_failed")
            result = status(directory)
            result["text_runtime"] = "installed_verified_by_runtime_owner"
        result["environment"] = "initialized"
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("plan", "prepare", "status", "initialize", "deploy", "register")
    )
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--releases", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--game-directory", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    try:
        if args.command in {"plan", "prepare"}:
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
                result = initialize(args.directory, args.config)
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
