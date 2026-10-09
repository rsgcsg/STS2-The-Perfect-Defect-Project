"""Execute an operator-pinned collection tool without trusting a mutable checkout.

The release ID authenticates bytes only against the caller's trusted pin. It is
not a signature, Human attestation, game compatibility or research admission.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .transfer import _inventory

SOURCE_V3_SUPPORT = {
    "schema": "sts2.evidence/source3-collection-support-1",
    "command": "pack-source-v3",
    "recording_schema": "sts2.annotator/source-session-manifest-3",
    "source_profile": "native-logical-source-v3",
    "bundle_schema": "sts2.annotator/source-session-bundle-3",
    "type_id": "source-session-bundle-v3",
}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


class CollectionTool:
    """Release built from clean source; verify all executable/dependency bytes."""

    def __init__(self, directory: str | Path, expected_release_id: str, *, dotnet: str = "dotnet") -> None:
        self.directory = Path(directory).absolute()
        self.release_id = expected_release_id
        self.dotnet = dotnet
        self.manifest = self.verify()

    def verify(self) -> dict[str, Any]:
        if self.directory.is_symlink():
            raise ValueError("collection tool directory cannot be a symbolic link")
        manifest = read_json(self.directory / "collection-tool.json")
        identity = manifest.get("identity")
        if (manifest.get("schema") != "sts2.evidence/collection-tool-1"
                or not isinstance(identity, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", self.release_id)
                or manifest.get("release_id") != self.release_id
                or digest(identity) != self.release_id):
            raise ValueError("collection tool trusted release ID mismatch")
        if (identity.get("worktree") != "clean"
                or not re.fullmatch(r"[0-9a-f]{40}", str(identity.get("source_revision", "")))
                or identity.get("entrypoint") != "sts2-human-annotator.dll"
                or identity.get("supported_recording_schema") != "sts2.human-annotator/recording-manifest-2"):
            raise ValueError("unsupported collection tool identity")
        # Collection-tool publishers order relative POSIX paths with ordinal
        # string comparison.  ``_inventory`` intentionally retains the
        # historical filesystem traversal order used by transfer manifests;
        # on Windows, sorting Path objects is case-insensitive and therefore
        # can disagree with the publisher for mixed-case file names.  Sort
        # this verification boundary by the already-normalized path string,
        # while preserving the exact declared bytes, sizes, hashes and order.
        actual = sorted(
            ({"path": p, "bytes": n, "sha256": h} for p, n, h in _inventory(self.directory)
             if p != "collection-tool.json"),
            key=lambda row: row["path"],
        )
        if identity.get("files") != actual:
            raise ValueError("collection tool release bytes differ")
        if not any(row["path"] == "platform-bom.json" for row in actual):
            raise ValueError("collection tool BOM is missing")
        if ("source_v3_support" in identity
                and identity["source_v3_support"] != SOURCE_V3_SUPPORT):
            raise ValueError("unsupported Source3 collection tool capability")
        return manifest

    def supports_source_v3(self) -> bool:
        """Only a caller-pinned complete release may advertise Source3 support."""
        return self.verify()["identity"].get("source_v3_support") == SOURCE_V3_SUPPORT

    def pack_source_v3(self, session: Path, output: Path, worker: str, campaign: str) -> None:
        manifest = self.verify()
        if manifest["identity"].get("source_v3_support") != SOURCE_V3_SUPPORT:
            raise CollectionFailure("Source3 packing is unavailable in this fixed tool")
        try:
            result = subprocess.run(
                [self.dotnet, str(self.directory / manifest["identity"]["entrypoint"]),
                 "pack-source-v3", str(session), worker, campaign, str(output),
                 manifest["identity"]["source_revision"]],
                capture_output=True, text=True, timeout=600, check=False,
            )
        except subprocess.TimeoutExpired:
            raise CollectionFailure("Source3 collection tool exceeded bounded runtime") from None
        if result.returncode != 0:
            raise CollectionFailure("Source3 collection tool audit/pack failed", result.stderr[-16384:])

    def pack(self, session: Path, output: Path, worker: str, campaign: str) -> None:
        manifest = self.verify()
        try:
            result = subprocess.run(
                [self.dotnet, str(self.directory / manifest["identity"]["entrypoint"]), "pack-session",
                 str(session), worker, campaign, str(output), manifest["identity"]["source_revision"],
                 "human_origin_attested"], capture_output=True, text=True, timeout=600, check=False,
            )
        except subprocess.TimeoutExpired:
            raise CollectionFailure("collection tool exceeded bounded runtime") from None
        if result.returncode != 0:
            # Tool output can contain private paths/records. Preserve only in local incident diagnostics.
            raise CollectionFailure("collection tool audit/pack failed", result.stderr[-16384:])

    def setup_status(self, *, recordings_root: str | Path, game_directory: str | Path | None = None,
                     mod_provenance: str | Path | None = None, node: str = "node") -> dict[str, Any]:
        """Inspect the current native destination through the fixed Game Mod owner."""
        return self._setup("status", recordings_root, game_directory, mod_provenance, node)

    def recover_interrupted(self, recordings: Path, recovered: Path) -> dict[str, Any]:
        """The fixed recorder owner acquires the original session lease and seals a copy."""
        manifest = self.verify()
        schema = "sts2.human-annotator/interrupted-recovery-1"
        if manifest["identity"].get("interrupted_recovery_schema") != schema:
            return {"schema": schema, "status": "unsupported"}
        try:
            result = subprocess.run(
                [self.dotnet, str(self.directory / manifest["identity"]["entrypoint"]),
                 "recover-interrupted", str(recordings), str(recovered)],
                capture_output=True, text=True, timeout=120, check=False,
            )
        except subprocess.TimeoutExpired:
            raise CollectionFailure("interrupted recovery exceeded bounded runtime") from None
        if result.returncode != 0:
            raise CollectionFailure("interrupted recovery requires review", result.stderr[-16384:])
        value = json.loads(result.stdout)
        if (not isinstance(value, dict) or value.get("schema") != schema
                or value.get("status") not in {"recovered", "incident", "idle"}):
            raise CollectionFailure("invalid interrupted recovery result")
        return value

    def bind_recording_root(self, *, recordings_root: str | Path, game_directory: str | Path | None = None,
                            mod_provenance: str | Path | None = None, node: str = "node") -> dict[str, Any]:
        """Bind only while STS2 is stopped; preserve old evidence and unrelated config."""
        return self._setup("bind", recordings_root, game_directory, mod_provenance, node)

    def _setup(self, command: str, recordings_root: str | Path, game_directory: str | Path | None,
               mod_provenance: str | Path | None, node: str) -> dict[str, Any]:
        identity = self.verify()["identity"]
        entrypoint = "setup/apps/game-mod/collection-setup.mjs"
        if identity.get("collection_setup_entrypoint") != entrypoint:
            raise CollectionFailure("collection setup is unavailable in this fixed tool")
        provenance = Path(mod_provenance) if mod_provenance else self.directory / "game-mod/build-provenance.json"
        try:
            relative = provenance.absolute().relative_to(self.directory).as_posix()
        except ValueError:
            raise CollectionFailure("collection setup provenance must belong to the verified tool") from None
        inventory_paths = {row["path"] for row in identity["files"]}
        if relative not in inventory_paths or entrypoint not in inventory_paths:
            raise CollectionFailure("collection setup provenance is absent from the verified tool")
        arguments = [node, str(self.directory / entrypoint), command, "--recordings-root", str(recordings_root),
                     "--mod-provenance", str(provenance)]
        if game_directory is not None:
            arguments.extend(["--game-dir", str(game_directory)])
        try:
            result = subprocess.run(arguments, capture_output=True, text=True, timeout=15, check=False)
        except subprocess.TimeoutExpired:
            raise CollectionFailure("collection setup exceeded bounded runtime") from None
        if result.returncode != 0:
            raise CollectionFailure("collection setup owner failed", result.stderr[-16384:])
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get("schema") != "sts2.platform/collection-setup-1":
            raise CollectionFailure("collection setup owner returned unsupported status")
        return value


class CollectionFailure(ValueError):
    def __init__(self, message: str, diagnostic: str = "") -> None:
        super().__init__(message)
        self.diagnostic = diagnostic
