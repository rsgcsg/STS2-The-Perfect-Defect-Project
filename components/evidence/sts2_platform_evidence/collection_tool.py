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

P1_NATIVE_SUPPORT = {
    "schema": "sts2.evidence/p1-native-collection-support-1",
    "audit_command": "audit-p1-native",
    "pack_command": "pack-p1-native",
    "recording_schema": "sts2.human-annotator/recording-manifest-2",
    "source_profile": "human-p1-native-decisions-1",
    "bundle_schema": "sts2.human-annotator/p1-native-session-bundle-1",
    "prefix_schema": "sts2.human-annotator/p1-native-prefix-1",
    "segment_schema": "sts2.p1/native-segments-1",
    "type_id": "p1-native-session-bundle-v1",
    "converter_entrypoint": "p1/tools/p1-native-to-p1.mjs",
    "recording_client_entrypoint": "setup/tools/p1-native-recording.mjs",
    "selector_transaction_projection": "native-selector-finalized-d-1",
}
P1_INPUT_PROJECTION = "native-input-v1"
P1_SELECTOR_PROJECTION = P1_NATIVE_SUPPORT["selector_transaction_projection"]

_P1_READ = """
import {pathToFileURL} from 'node:url';
const {readVerifiedP1NativeBundle} = await import(pathToFileURL(process.argv[2]).href);
const source = readVerifiedP1NativeBundle(process.argv[3]);
const json = name => JSON.parse(source.bytes(name).toString('utf8'));
console.log(JSON.stringify({manifest:source.manifest, recording:json('recording-manifest.json'),
  close:json('p1-native-close.json'), journal:source.journal,
  human_origin_verified:false}));
"""

_P1_DERIVE = """
import {pathToFileURL} from 'node:url';
import {writeFileSync} from 'node:fs';
const {convertP1NativeBundle} = await import(pathToFileURL(process.argv[2]).href);
const options = {bundle:process.argv[3], runId:process.argv[4], includeSegments:true};
if (process.argv[5] !== 'native-input-v1') options.selectorTransactions=process.argv[5];
const result = await convertP1NativeBundle(options);
writeFileSync(process.argv[6], JSON.stringify(result.segment_output,null,2)+'\\n',{flag:'wx'});
const segments=result.segment_output;
const points=segments.segments.flatMap(segment=>segment.points);
console.log(JSON.stringify({schema:segments.schema, source_content_id:segments.source_content_id,
  branch:segments.branch, profile:segments.profile, revision:segments.revision,
  projection_claims:segments.projection_claims, whole_run_complete:segments.whole_run_complete,
  source_conversion_complete:segments.source_conversion_complete,
  segments:segments.segments.length, observations:points.length,
  candidate_N:points.filter(point=>point.mask.N).length,
  next_edges:points.filter(point=>point.mask.next).length,
  end_masks:points.filter(point=>point.mask.end).length,
  gaps:segments.gaps, discarded_choices:segments.discarded_choices,
  blocking_issues:result.blocking_issues,
  training_loader_consumed:false, research_admission:'not_evaluated'}));
"""


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
        if "p1_native_support" in identity:
            required = {"p1/tools/p1-native-bundle.mjs", P1_NATIVE_SUPPORT["converter_entrypoint"],
                        P1_NATIVE_SUPPORT["recording_client_entrypoint"],
                        "p1/components/connector/sdk/typescript/dist/p1Global/index.js"}
            if (identity["p1_native_support"] != P1_NATIVE_SUPPORT
                    or not required <= {row["path"] for row in actual}):
                raise ValueError("unsupported or incomplete P1-native collection capability")
        return manifest

    def supports_p1_native(self) -> bool:
        return self.verify()["identity"].get("p1_native_support") == P1_NATIVE_SUPPORT

    def _p1(self) -> dict[str, Any]:
        identity = self.verify()["identity"]
        if identity.get("p1_native_support") != P1_NATIVE_SUPPORT:
            raise CollectionFailure("P1-native support is unavailable in this fixed tool")
        return identity

    def pack_p1_native(self, session: Path, output: Path, worker: str, campaign: str,
                       *, human_origin_attested: bool = False) -> None:
        identity = self._p1()
        arguments = [self.dotnet, str(self.directory / identity["entrypoint"]),
                     P1_NATIVE_SUPPORT["pack_command"], str(session), worker, campaign,
                     str(output), identity["source_revision"]]
        if human_origin_attested:
            arguments.append("human_origin_attested")
        try:
            result = subprocess.run(arguments, capture_output=True, text=True,
                                    timeout=600, check=False)
        except subprocess.TimeoutExpired:
            raise CollectionFailure("P1-native packing exceeded bounded runtime") from None
        if result.returncode:
            raise CollectionFailure("P1-native audit/pack failed", result.stderr[-16384:])

    def _p1_node(self, driver: str, arguments: list[str], *, node: str) -> dict[str, Any]:
        self._p1()
        try:
            result = subprocess.run([node, "--input-type=module", "--eval", driver, "p1-application", *arguments],
                                    capture_output=True, text=True, timeout=600, check=False)
        except subprocess.TimeoutExpired:
            raise CollectionFailure("P1-native reader/converter exceeded bounded runtime") from None
        if result.returncode:
            raise CollectionFailure("P1-native reader/converter failed", result.stderr[-16384:])
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise CollectionFailure("P1-native owner returned an invalid result")
        return value

    def verify_p1_native(self, bundle: Path, *, node: str = "node") -> dict[str, Any]:
        """Delegate integrity and stream accounting to the pinned original reader."""
        return self._p1_node(_P1_READ,
                             [str(self.directory / "p1/tools/p1-native-bundle.mjs"), str(bundle)],
                             node=node)

    def derive_p1_native(self, bundle: Path, output: Path, *, run_id: str,
                         projection_id: str = P1_SELECTOR_PROJECTION,
                         node: str = "node") -> dict[str, Any]:
        """Write only the named segments and compact diagnostics, never a giant debug trace."""
        if projection_id not in {P1_INPUT_PROJECTION, P1_SELECTOR_PROJECTION}:
            raise CollectionFailure("unsupported P1-native projection")
        return self._p1_node(_P1_DERIVE,
                             [str(self.directory / P1_NATIVE_SUPPORT["converter_entrypoint"]),
                              str(bundle), run_id, projection_id, str(output)], node=node)

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
