"""Small synthetic npm Host/Connector/Zod closure for developer-kit tests."""

from __future__ import annotations

import base64
import hashlib
import json
import tarfile
from io import BytesIO
from pathlib import Path

HOST = "@rsgcsg/sts2-host-runtime"
SDK = "@rsgcsg/sts2-connector-client"
HOST_VERSION = "1.1.0-rc.22"
SDK_VERSION = "1.3.0-rc.5"
ZOD_VERSION = "3.25.76"
HOST_SOURCE = "a" * 40
HOST_TREE = "b" * 40
HOST_DIGEST = "c" * 64
CONNECTOR_SOURCE = "d" * 40
CONNECTOR_TREE = "e" * 40
CONNECTOR_DIGEST = "f" * 64
ZOD_INTEGRITY = "sha512-" + base64.b64encode(b"z" * 64).decode()
ZOD_URL = f"https://registry.npmjs.org/zod/-/zod-{ZOD_VERSION}.tgz"


def tree_sha(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, raw in sorted(files.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(raw)
        digest.update(b"\0")
    return digest.hexdigest()


def private_host_fixture(root: Path, *, sdk_version: str = SDK_VERSION
                         ) -> tuple[bytes, bytes, bytes]:
    """Return (profile, npm tgz, selected BOM) with all identities derived here."""
    package_root = root / "host-package"
    files = {
        "package.json": {
            "name": HOST,
            "version": HOST_VERSION,
            "type": "module",
            "main": "index.js",
            "dependencies": {SDK: sdk_version, "zod": ZOD_VERSION},
            "bundleDependencies": [SDK, "zod"],
            "scripts": {"preinstall": "node -e \"throw new Error('must not run')\""},
        },
        "index.js": (b"import { sdkProbe } from '@rsgcsg/sts2-connector-client';\n"
                     b"export const hostProbe = () => sdkProbe();\n"),
        "npm-shrinkwrap.json": {
            "name": HOST,
            "version": HOST_VERSION,
            "lockfileVersion": 3,
            "requires": True,
            "packages": {
                "": {"name": HOST, "version": HOST_VERSION,
                     "dependencies": {SDK: sdk_version, "zod": ZOD_VERSION},
                     "bundleDependencies": [SDK, "zod"]},
                f"node_modules/{SDK}": {
                    "version": sdk_version,
                    "inBundle": True,
                    "dependencies": {"zod": f"^{ZOD_VERSION}"},
                },
                "node_modules/zod": {
                    "version": ZOD_VERSION,
                    "inBundle": True,
                    "resolved": ZOD_URL,
                    "integrity": ZOD_INTEGRITY,
                },
            },
        },
        f"node_modules/{SDK}/package.json": {
            "name": SDK,
            "version": sdk_version,
            "type": "module",
            "main": "index.js",
            "dependencies": {"zod": f"^{ZOD_VERSION}"},
        },
        f"node_modules/{SDK}/index.js": (
            b"import { z } from 'zod';\n"
            b"export const sdkProbe = () => z('ok').parse('ok');\n"
        ),
        "node_modules/zod/package.json": {
            "name": "zod", "version": ZOD_VERSION, "type": "module", "main": "index.js",
        },
        "node_modules/zod/index.js": b"export const z = () => ({ parse: value => value });\n",
        "consumers/python/sts2_headless/__init__.py": b"from .client import HostClient\n",
        "consumers/python/sts2_headless/client.py": b"class HostClient: pass\n",
        "tools/managed-pe-driver.mjs": b"// synthetic\n",
        "tools/managed-exact.mjs": b"// synthetic\n",
    }
    materialized: dict[str, bytes] = {}
    for name, value in files.items():
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() if isinstance(
            value, dict) else value
        path = package_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        materialized[name] = raw
    archive_path = root / "private-host.tgz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        for name, raw in sorted(materialized.items()):
            entry = tarfile.TarInfo(f"package/{name}")
            entry.size = len(raw)
            entry.mode = 0o644
            entry.mtime = 0
            archive.addfile(entry, BytesIO(raw))
    archive_raw = archive_path.read_bytes()
    bom = {
        "components": {
            "host_runtime": {"version": HOST_VERSION, "source_revision": HOST_SOURCE,
                             "component_tree_revision": HOST_TREE,
                             "component_source_digest_sha256": HOST_DIGEST},
            "connector": {"version": "1.3.0-rc.8", "source_revision": CONNECTOR_SOURCE,
                          "component_tree_revision": CONNECTOR_TREE,
                          "component_source_digest_sha256": CONNECTOR_DIGEST},
            "typescript_sdk": SDK_VERSION,
        },
    }
    connector_pin = {
        "package": SDK, "version": sdk_version, "source_revision": CONNECTOR_SOURCE,
        "component_tree_revision": CONNECTOR_TREE,
        "component_source_digest_sha256": CONNECTOR_DIGEST,
        "bundle_sha256": tree_sha({name.removeprefix(f"node_modules/{SDK}/"): raw
                                    for name, raw in materialized.items()
                                    if name.startswith(f"node_modules/{SDK}/")}),
        "transitive_zod": {
            "version": ZOD_VERSION, "url": ZOD_URL, "integrity": ZOD_INTEGRITY,
            "bundle_sha256": tree_sha({name.removeprefix("node_modules/zod/"): raw
                                        for name, raw in materialized.items()
                                        if name.startswith("node_modules/zod/")}),
        },
    }
    profile = {
        "schema": "stpd/private-host-runtime-kit-v1",
        "distribution": "private_kit_candidate",
        "host_runtime": {
            "schema": "stpd/platform-host-runtime-pin-v1",
            "package": HOST,
            "version": HOST_VERSION,
            "source_revision": HOST_SOURCE,
            "component_tree_revision": HOST_TREE,
            "release_asset_sha256": hashlib.sha256(archive_raw).hexdigest(),
            "package_content_sha256": tree_sha(materialized),
        },
        "component_source_digest_sha256": HOST_DIGEST,
        "dependency_layout": "bundled_source_candidate",
        "bundled_connector_pin": connector_pin,
    }
    return (json.dumps(profile, sort_keys=True, separators=(",", ":")).encode(),
            archive_raw,
            json.dumps(bom, sort_keys=True, separators=(",", ":")).encode())


def derived_private_host_fixture(root: Path) -> tuple[bytes, bytes, bytes]:
    """Return a v2 candidate with an rc1 source manifest and a BOM rc5 closure."""
    profile, legacy_archive, bom_raw = private_host_fixture(root)
    source_entries: dict[str, bytes] = {}
    sdk_entries: dict[str, bytes] = {}
    zod_entries: dict[str, bytes] = {}
    with tarfile.open(fileobj=BytesIO(legacy_archive), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            raw = archive.extractfile(member).read()
            name = member.name.removeprefix("package/")
            if name.startswith(f"node_modules/{SDK}/"):
                sdk_entries[name.removeprefix(f"node_modules/{SDK}/")] = raw
            elif name.startswith("node_modules/zod/"):
                zod_entries[name.removeprefix("node_modules/zod/")] = raw
            elif name not in {"npm-shrinkwrap.json"}:
                source_entries[name] = raw
    source_entries["src/project-identity.mjs"] = b"export const hostImportProbe = true;\n"
    source_manifest = json.loads(source_entries["package.json"])
    source_manifest.pop("bundleDependencies", None)
    source_manifest["dependencies"] = {
        SDK: "https://github.com/rsgcsg/STS2-The-Pefect-Defect-Project/releases/"
            "download/compat/platform-import-v1/rsgcsg-sts2-connector-client-1.1.0-rc.1.tgz",
    }
    source_manifest["files"] = ["index.js", "consumers", "tools", "src"]
    source_manifest_raw = json.dumps(source_manifest, sort_keys=True,
                                     separators=(",", ":")).encode()
    source_entries["package.json"] = source_manifest_raw

    source_archive_path = root / "synthetic-source.tgz"
    with tarfile.open(source_archive_path, mode="w:gz") as archive:
        for name, raw in sorted(source_entries.items()):
            entry = tarfile.TarInfo(f"package/{name}")
            entry.size = len(raw)
            entry.mode = 0o644
            entry.mtime = 0
            archive.addfile(entry, BytesIO(raw))
    source_archive_raw = source_archive_path.read_bytes()

    source_manifest_derived = dict(source_manifest)
    dependencies = {SDK: SDK_VERSION, "zod": ZOD_VERSION}
    source_manifest_derived["dependencies"] = dependencies
    source_manifest_derived["bundleDependencies"] = [SDK, "zod"]
    added = ["npm-shrinkwrap.json", "private-host-derivation.json"]
    source_manifest_derived["files"] = list(dict.fromkeys([*source_manifest["files"], *added]))
    derived_manifest_raw = json.dumps(source_manifest_derived, sort_keys=True,
                                      separators=(",", ":")).encode()

    def rows(values: dict[str, bytes]) -> dict[str, dict[str, object]]:
        return {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                       "mode": 0o644} for name, raw in sorted(values.items())}

    identity = json.loads(profile)
    derivation = {
        "schema": "stpd/private-host-runtime-derivation-v1",
        "recipe": "kit-boundary-bundled-closure-v1",
        "source_archive": {
            "package": HOST, "version": HOST_VERSION,
            "filename": f"rsgcsg-sts2-host-runtime-{HOST_VERSION}.tgz",
            "archive_sha256": hashlib.sha256(source_archive_raw).hexdigest(),
            "package_content_sha256": tree_sha(source_entries),
            "files": rows(source_entries),
            "package_json_utf8": source_manifest_raw.decode(),
        },
        "host_source": {
            "version": HOST_VERSION, "source_revision": HOST_SOURCE,
            "component_tree_revision": HOST_TREE,
            "component_source_digest_sha256": HOST_DIGEST, "source_file_count": 10,
        },
        "manifest_transform": {
            "changed_fields": ["bundleDependencies", "dependencies", "files"],
            "added_files": added,
            "source_package_json_sha256": hashlib.sha256(source_manifest_raw).hexdigest(),
            "derived_package_json_sha256": hashlib.sha256(derived_manifest_raw).hexdigest(),
            "source_dependencies": source_manifest["dependencies"],
            "derived_dependencies": dependencies,
        },
        "selected_sdk": {
            "package": SDK, "version": SDK_VERSION, "component_version": "1.3.0-rc.8",
            "source_revision": CONNECTOR_SOURCE, "component_tree_revision": CONNECTOR_TREE,
            "component_source_digest_sha256": CONNECTOR_DIGEST,
            "bundle_sha256": tree_sha(sdk_entries),
        },
        "zod": {
            "version": ZOD_VERSION, "url": ZOD_URL, "integrity": ZOD_INTEGRITY,
            "bundle_sha256": tree_sha(zod_entries),
        },
        "producer": {
            "workspace_revision": "9" * 40,
            "tool_files": {
                "python/tools/package_developer_kit.py": {
                    "git_blob_sha1": "1" * 40, "sha256": "2" * 64},
                "python/spireagent/workbench/kit_runtime.py": {
                    "git_blob_sha1": "3" * 40, "sha256": "4" * 64},
            },
            "node_version": "v22.12.0", "npm_version": "10.9.2",
            "typescript_version": "5.9.2",
            "typescript_integrity": "sha512-" + base64.b64encode(
                b"typescript-lock-bytes" * 3 + b"x").decode(),
        },
        "bundles": {"connector": {"files": rows(sdk_entries)},
                    "zod": {"files": rows(zod_entries)}},
    }
    # Make the synthetic compiler integrity the correctly sized SHA512 SRI token.
    identity["bundled_connector_pin"]["bundle_sha256"] = tree_sha(sdk_entries)
    identity["bundled_connector_pin"]["transitive_zod"]["bundle_sha256"] = tree_sha(zod_entries)
    identity["host_runtime"]["release_asset_sha256"] = "0" * 64
    identity["host_runtime"]["package_content_sha256"] = "0" * 64
    identity["schema"] = "stpd/private-host-runtime-kit-v2"
    identity["bundled_connector_pin"]["version"] = SDK_VERSION
    identity["bundled_connector_pin"]["source_revision"] = CONNECTOR_SOURCE
    identity["bundled_connector_pin"]["component_tree_revision"] = CONNECTOR_TREE
    identity["bundled_connector_pin"]["component_source_digest_sha256"] = CONNECTOR_DIGEST
    identity["derivation"] = derivation
    identity["bundled_connector_pin"]["transitive_zod"] = derivation["zod"]

    derived_entries = {**source_entries, "package.json": derived_manifest_raw,
                       "npm-shrinkwrap.json": json.dumps({
                           "name": HOST, "version": HOST_VERSION, "lockfileVersion": 3,
                           "requires": True, "packages": {
                               "": {"dependencies": dependencies,
                                    "bundleDependencies": [SDK, "zod"]},
                               f"node_modules/{SDK}": {
                                   "version": SDK_VERSION, "inBundle": True,
                                   "dependencies": {"zod": f"^{ZOD_VERSION}"}},
                               "node_modules/zod": {
                                   "version": ZOD_VERSION, "inBundle": True,
                                   "resolved": ZOD_URL, "integrity": ZOD_INTEGRITY},
                           }}, sort_keys=True, separators=(",", ":")).encode(),
                       "private-host-derivation.json": json.dumps(
                           derivation, sort_keys=True, separators=(",", ":")).encode()}
    derived_entries.update({f"node_modules/{SDK}/{name}": raw
                            for name, raw in sdk_entries.items()})
    derived_entries.update({f"node_modules/zod/{name}": raw
                            for name, raw in zod_entries.items()})
    output = BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, raw in sorted(derived_entries.items()):
            entry = tarfile.TarInfo(f"package/{name}")
            entry.size = len(raw)
            entry.mode = 0o644
            entry.mtime = 0
            archive.addfile(entry, BytesIO(raw))
    derived_archive = output.getvalue()
    identity["host_runtime"]["release_asset_sha256"] = hashlib.sha256(
        derived_archive).hexdigest()
    identity["host_runtime"]["package_content_sha256"] = tree_sha(derived_entries)
    return (json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(),
            derived_archive, bom_raw)
