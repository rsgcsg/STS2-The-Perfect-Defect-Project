"""Assemble an offline developer kit from explicit, independently pinned public bytes.

This packages identities only. Release notes separately establish CI, native load,
supported systems, Human approval and cloud qualification. No installation or upload occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sts2_platform_evidence.collection_tool import CollectionTool

from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes
from spireagent.source import source_identity
from spireagent.workbench.developer import combination
from spireagent.workbench.kit_runtime import (
    KIT_RUNTIME_PAIRS,
    text_runtime_pin,
)
from spireagent.workbench.runtime_install import install_runtime

ROOT = Path(__file__).resolve().parents[1]
README = """# SpireAgent developer kit

Verify this archive's SHA256 against the approved GitHub release before extraction.
combination.json inventories packaged bytes and exact STPD source/lock. Read that release's
supported systems, actual qualification gates and rollback before installing.

Install Git, Python 3.11 with uv, Node 20+ and the collection tool's declared .NET runtime.
Node and .NET must stay on PATH: the fixed tool uses them for native setup and packaging.
Clone https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project.git and check out the exact
stpd_source_revision from combination.json. Model weights are not needed for collection.
Initial Mod installation uses the same repository at the release commit. Enter python/
and follow docs/DEVELOPER_KIT_INSTALL.md. Its install_developer_kit.py entrypoint prepares
a fixed release directory and stages these bytes without a native rebuild. The ZIP has no
standalone installer. Everyday member collection uses the fixed tool without a Platform clone.
Install mod/ with the game closed; retain the previous compatible Mod/tool pair. Keep
collection-tool/ complete, including its setup helper and provenance. Verify its embedded BOM
separately from the kit root's pinned distribution BOM; the two may have identical bytes.

Use one private absolute --config path for this computer on every command. The launcher default
is %LOCALAPPDATA%/spireagent/workbench/project.json on Windows, or
~/.local/share/spireagent/workbench/project.json elsewhere. Use its resolved absolute path;
other accounts need separate private directories. From python/ in the exact project checkout,
replace /ABS/project.json and /ABS/kit below with your chosen locations and quote paths with spaces:

```bash
python tools/open_workbench.py --config /ABS/project.json --hub-url https://hub.2-fire-2.com
```

An administrator invites your email in the cloud. In the local workbench open account/device
setup, log in with that email and approve the matching computer name and pairing code.
Then stop the workbench, register the complete tool with its independently approved release ID,
and reopen. Closing a browser tab alone does not release the registration lock.

```bash
uv run --locked python -m spireagent.workbench project stop --config /ABS/project.json
uv run --locked python -m spireagent.workbench project collection-tool --config /ABS/project.json \\
  --tool-directory /ABS/kit/collection-tool --tool-release-id EXACT_APPROVED_ID
uv run --locked python -m spireagent.workbench project open --config /ABS/project.json
```

Open 真人采集. Read the upload purpose and project-member access explanation, then select
同意并开启采集 once; login never supplies consent. The saved consent is reused for the same
purpose and scope. Choose 准备 / 继续检查 and follow the actual next step: provide the game
directory, close the game for binding, then reopen it and continue checks. Routine collection
uses one daily configuration, without publishing or joining an activity. Pause/resume automatic
upload on this page; viewing a page never grants consent or starts model control.

Start recording through the game Recorder. With the continuous-recording release, each observed
native victory, defeat or abandonment seals one segment and recording stays armed for the next
run. Orderly game exit seals a partial segment; it does not invent a game outcome. Press
结束录制 (Close) when you want to stop continuous recording. Check each segment's cloud receipt in
数据. Setup status is saved; the receipt remains separate. After reboot, reopen the same
configuration. Keep the workbench running for uploads; closing a tab does not stop delivery.

Daily consent v2 does not pin software, but an existing outbox retains its exact tool release.
Registering a new tool does not upgrade a saved queue. Use the documented stopped-workbench
collection-upgrade procedure for an eligible v2 completed queue; retain the old valid tool. Never
rewrite an outbox identity or enroll an old recording archive to force an upgrade.

Retain raw recordings, failed evidence and outbox IDs. Never copy credentials into reports.
See docs/B_PIPELINE_HANDOFF.md for the full workflow, docs/PROJECT_CONSOLE.md for screen meaning
and deploy/hub/RUNBOOK.md for operators. Packaging proves no native/Human/cloud qualification.
This is a developer distribution, not a player installer. Models are distributed separately.
"""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_regular(path: Path) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise BoundaryError("developer_kit", "regular_file_required")
    return path.read_bytes()


@dataclass(frozen=True)
class PinnedFile:
    path: Path
    sha256: str

    def read(self) -> bytes:
        digest(self.sha256, "developer_kit.file_digest")
        raw = read_regular(self.path)
        if sha256(raw) != self.sha256:
            raise BoundaryError("developer_kit", "pinned_file_changed")
        return raw


def package(
    *,
    mod_dll: PinnedFile,
    mod_manifest: PinnedFile,
    platform_bom: PinnedFile,
    collection_tool: Path,
    tool_release_id: str,
    output: Path,
    root: Path = ROOT,
    text_runtime_profile: PinnedFile | None = None,
    text_runtime_archive: PinnedFile | None = None,
    m2_runtime_profile: PinnedFile | None = None,
    m2_runtime_archive: PinnedFile | None = None,
    m2_v2_runtime_profile: PinnedFile | None = None,
    m2_v2_runtime_archive: PinnedFile | None = None,
) -> dict[str, Any]:
    """Verify with the owning tool contract, then publish one immutable deterministic ZIP."""
    if output.exists() or output.is_symlink():
        raise BoundaryError("developer_kit", "output_already_exists")
    producer = source_identity(root)
    project = combination(root)
    project_raw = (root / "configs/developer/combination-v1.json").read_bytes()
    supplied = {
        "text-menu-v1": (text_runtime_profile, text_runtime_archive),
        "text-menu-m2-v1": (m2_runtime_profile, m2_runtime_archive),
        "text-menu-m2-v2": (m2_v2_runtime_profile, m2_v2_runtime_archive),
    }
    for profile_id, (supplied_profile, supplied_archive) in supplied.items():
        if (supplied_profile is None) != (supplied_archive is None):
            raise BoundaryError("developer_kit", KIT_RUNTIME_PAIRS[profile_id][4] +
                                "_profile_and_archive_required")
    files = {
        "README.md": README.encode(),
        "mod/STS2_PLATFORM.dll": mod_dll.read(),
        "mod/STS2_PLATFORM.json": mod_manifest.read(),
        "platform-bom.json": platform_bom.read(),
        "developer-combination.json": project_raw,
    }
    for profile_id, (supplied_profile, supplied_archive) in supplied.items():
        if supplied_profile is None or supplied_archive is None:
            continue
        profile_name, archive_name, _, _, _, _ = KIT_RUNTIME_PAIRS[profile_id]
        profile_raw = supplied_profile.read()
        archive_raw = supplied_archive.read()
        pin = text_runtime_pin(profile_raw, archive_raw,
                               **({"required_profile": profile_id}
                                  if profile_id == "text-menu-m2-v2" else
                                  {"memory": profile_id == "text-menu-m2-v1"}))
        connector = next(
            (p for p in project["node_packages"]
             if p.get("package") == "@rsgcsg/sts2-connector-client"), None
        )
        if connector is None:
            raise BoundaryError("developer_kit", "connector_pin_required")
        # The profile is externally pinned; the ordinary installer verifies the
        # complete bundled closure in a disposable private directory.
        with tempfile.TemporaryDirectory(prefix=".kit-runtime-", dir=output.parent) as temp:
            candidate = Path(temp) / "runtime.tgz"
            candidate.write_bytes(archive_raw)
            install_runtime(Path(temp) / "models" / profile_id, pin, connector,
                            archive=candidate,
                            **({"required_profile": profile_id}
                               if profile_id == "text-menu-m2-v2" else {}))
        files[profile_name] = profile_raw
        files[archive_name] = archive_raw
    owner = CollectionTool(collection_tool, tool_release_id)
    tool_manifest = owner.manifest
    tool_manifest_raw = read_regular(collection_tool / "collection-tool.json")
    if decode_json(tool_manifest_raw) != tool_manifest or set(tool_manifest) != {
        "schema",
        "release_id",
        "identity",
    }:
        raise BoundaryError("developer_kit", "tool_manifest_changed_or_unexpected_fields")
    for row in tool_manifest["identity"]["files"]:
        raw = PinnedFile(collection_tool / row["path"], row["sha256"]).read()
        if len(raw) != row["bytes"]:
            raise BoundaryError("developer_kit", "tool_file_size_changed")
        files[f"collection-tool/{row['path']}"] = raw
    identity = tool_manifest["identity"]
    setup = "setup/apps/game-mod/collection-setup.mjs"
    provenance = "game-mod/build-provenance.json"
    if (
        identity.get("collection_setup_entrypoint") != setup
        or identity.get("collection_setup_provenance") != provenance
        or f"collection-tool/{setup}" not in files
        or f"collection-tool/{provenance}" not in files
    ):
        raise BoundaryError("developer_kit", "collection_setup_capability_required")
    native = decode_json(files[f"collection-tool/{provenance}"])
    if (
        not isinstance(native, dict)
        or native.get("schema") != "sts2.platform/game-mod-build-provenance-1"
        or not isinstance(native.get("artifact"), dict)
        or native["artifact"].get("sha256") != mod_dll.sha256
    ):
        raise BoundaryError("developer_kit", "collection_setup_mod_identity_mismatch")
    files["collection-tool/collection-tool.json"] = tool_manifest_raw
    if owner.verify() != tool_manifest:
        raise BoundaryError("developer_kit", "tool_changed_during_packaging")
    manifest = {
        "schema": "spireagent/developer-kit-v1",
        "stpd_source_revision": producer.source_revision,
        "uv_lock_sha256": producer.uv_lock_sha256,
        "platform_source_revision": project["platform_source_revision"],
        "evidence_source_revision": project["evidence_source_revision"],
        "developer_combination_sha256": sha256(project_raw),
        "collection_tool_release_id": tool_release_id,
        "collection_tool_source_revision": identity["source_revision"],
        "collection_tool_workspace_revision": identity.get("workspace_revision"),
        "mod_sha256": mod_dll.sha256,
        "mod_manifest_sha256": mod_manifest.sha256,
        "platform_bom_sha256": platform_bom.sha256,
        "files": {name: sha256(raw) for name, raw in sorted(files.items())},
    }
    for profile_id, (supplied_profile, supplied_archive) in supplied.items():
        if supplied_profile is not None and supplied_archive is not None:
            manifest[KIT_RUNTIME_PAIRS[profile_id][4]] = {
                "profile_sha256": supplied_profile.sha256,
                "archive_sha256": supplied_archive.sha256,
            }
    files["combination.json"] = json_bytes(manifest)
    # ZIP_STORED avoids zlib-version variance. The small developer kit favors reproducibility.
    with tempfile.TemporaryDirectory(prefix=".developer-kit-", dir=output.parent) as directory:
        pending = Path(directory) / "kit.zip"
        with zipfile.ZipFile(pending, "x") as archive:
            for name, raw in sorted(files.items()):
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(entry, raw)
        with zipfile.ZipFile(pending) as archive:
            if archive.testzip() is not None or archive.namelist() != sorted(files):
                raise BoundaryError("developer_kit", "archive_inventory_mismatch")
            if any(archive.read(name) != raw for name, raw in files.items()):
                raise BoundaryError("developer_kit", "archive_content_mismatch")
        if source_identity(root) != producer:
            raise BoundaryError("developer_kit", "source_changed_during_packaging")
        raw = pending.read_bytes()
        # Same-filesystem hard link is atomic and refuses an existing destination, including races.
        os.link(pending, output)
    return {
        "schema": "spireagent/developer-kit-packaging-v1",
        "sha256": sha256(raw),
        "bytes": len(raw),
        "files": len(files),
        "stpd_source_revision": producer.source_revision,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("mod-dll", "mod-manifest", "platform-bom"):
        parser.add_argument(f"--{name}", required=True, type=Path)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--collection-tool", required=True, type=Path)
    parser.add_argument("--tool-release-id", required=True)
    parser.add_argument("--text-runtime-profile", type=Path)
    parser.add_argument("--text-runtime-profile-sha256")
    parser.add_argument("--text-runtime-archive", type=Path)
    parser.add_argument("--text-runtime-archive-sha256")
    parser.add_argument("--m2-runtime-profile", type=Path)
    parser.add_argument("--m2-runtime-profile-sha256")
    parser.add_argument("--m2-runtime-archive", type=Path)
    parser.add_argument("--m2-runtime-archive-sha256")
    parser.add_argument("--m2-v2-runtime-profile", type=Path)
    parser.add_argument("--m2-v2-runtime-profile-sha256")
    parser.add_argument("--m2-v2-runtime-archive", type=Path)
    parser.add_argument("--m2-v2-runtime-archive-sha256")
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New ZIP in an existing directory outside Git or under ignored .local/",
    )
    args = parser.parse_args()
    try:
        if ((args.text_runtime_profile is None) != (args.text_runtime_profile_sha256 is None)
                or (args.text_runtime_archive is None)
                != (args.text_runtime_archive_sha256 is None)):
            raise BoundaryError("developer_kit", "text_runtime_path_and_hash_required")
        if ((args.m2_runtime_profile is None) != (args.m2_runtime_profile_sha256 is None)
                or (args.m2_runtime_archive is None)
                != (args.m2_runtime_archive_sha256 is None)):
            raise BoundaryError("developer_kit", "m2_runtime_path_and_hash_required")
        if ((args.m2_v2_runtime_profile is None) != (args.m2_v2_runtime_profile_sha256 is None)
                or (args.m2_v2_runtime_archive is None)
                != (args.m2_v2_runtime_archive_sha256 is None)):
            raise BoundaryError("developer_kit", "m2_v2_runtime_path_and_hash_required")
        receipt = package(
            mod_dll=PinnedFile(args.mod_dll, args.mod_dll_sha256),
            mod_manifest=PinnedFile(args.mod_manifest, args.mod_manifest_sha256),
            platform_bom=PinnedFile(args.platform_bom, args.platform_bom_sha256),
            collection_tool=args.collection_tool,
            tool_release_id=args.tool_release_id,
            output=args.output,
            text_runtime_profile=(PinnedFile(args.text_runtime_profile,
                                             args.text_runtime_profile_sha256)
                                  if args.text_runtime_profile is not None else None),
            text_runtime_archive=(PinnedFile(args.text_runtime_archive,
                                             args.text_runtime_archive_sha256)
                                  if args.text_runtime_archive is not None else None),
            m2_runtime_profile=(PinnedFile(args.m2_runtime_profile,
                                           args.m2_runtime_profile_sha256)
                                if args.m2_runtime_profile is not None else None),
            m2_runtime_archive=(PinnedFile(args.m2_runtime_archive,
                                           args.m2_runtime_archive_sha256)
                                if args.m2_runtime_archive is not None else None),
            m2_v2_runtime_profile=(PinnedFile(args.m2_v2_runtime_profile,
                                               args.m2_v2_runtime_profile_sha256)
                                    if args.m2_v2_runtime_profile is not None else None),
            m2_v2_runtime_archive=(PinnedFile(args.m2_v2_runtime_archive,
                                               args.m2_v2_runtime_archive_sha256)
                                    if args.m2_v2_runtime_archive is not None else None),
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        code = error.code if isinstance(error, BoundaryError) else type(error).__name__
        print(json.dumps({"status": "FAILED", "code": code}))
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
