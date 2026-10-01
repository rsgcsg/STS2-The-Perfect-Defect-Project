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
ZOD_INTEGRITY = "sha512-" + base64.b64encode(b"synthetic zod integrity").decode()
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
