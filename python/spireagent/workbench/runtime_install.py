"""Install only the reviewed optional Platform Runtime release, without lifecycle scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, digest
from spireagent.package_identity import (
    PackageIdentityError,
    directory_sha256,
    file_sha256,
    validate_installed_package,
)
from spireagent.source import REPOSITORY_ALIASES

RUNTIME_PACKAGE = "@rsgcsg/sts2-policy-runtime"
CONNECTOR_PACKAGE = "@rsgcsg/sts2-connector-client"
ARCHIVE_LIMIT = 32 * 1024 * 1024
RELEASE_PREFIXES = tuple(
    f"https://github.com/{name}/releases/download/" for name in REPOSITORY_ALIASES
)
BUNDLED_LAYOUT = "bundled_source_candidate"
PACKAGE_IDENTITY_SCHEMA = "sts2.policy-runtime/package-identity-1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BUNDLE_HASH_SCRIPT = """
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
function hash(root) {
  const digest = crypto.createHash('sha256');
  function visit(directory, prefix = '') {
    for (const entry of fs.readdirSync(directory, {withFileTypes: true})
      .sort((left, right) => left.name.localeCompare(right.name))) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(full, relative);
      else if (entry.isFile()) digest.update(relative).update('\\0')
        .update(fs.readFileSync(full)).update('\\0');
      else throw new Error(`unsupported bundle entry: ${relative}`);
    }
  }
  visit(root);
  return digest.digest('hex');
}
process.stdout.write(JSON.stringify(process.argv.slice(1).map(hash)));
"""


class ReleaseRedirect(HTTPRedirectHandler):
    """GitHub release assets redirect to GitHub-owned storage; never send credentials."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        parsed = urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname
            not in {
                "github.com",
                "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
            }
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            raise BoundaryError("local_model", "untrusted_runtime_release_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_runtime_install(
    node_modules: Path, pin: dict[str, Any], connector_pin: dict[str, Any]
) -> dict[str, str]:
    package = node_modules / RUNTIME_PACKAGE
    layout = pin.get("dependency_layout")
    if layout == BUNDLED_LAYOUT:
        _safe_package_tree(node_modules, package)
        observed = validate_installed_package(
            package, pin, required_paths=("dist/cli.js", "bin/policy-runtime.mjs")
        )
        _validate_bundled_dependencies(package, pin)
        return {**observed, "code_sha256": _runtime_code_sha256(package)}
    if layout is not None:
        raise PackageIdentityError("unsupported Runtime dependency layout")
    connector = node_modules / CONNECTOR_PACKAGE
    if any(
        path.is_symlink() or not path.resolve().is_relative_to(node_modules.resolve())
        for path in (package, connector)
    ):
        raise PackageIdentityError("sibling Platform packages are unsupported")
    observed = validate_installed_package(
        package, pin, required_paths=("dist/cli.js", "bin/policy-runtime.mjs")
    )
    validate_installed_package(connector, connector_pin, required_paths=("package.json",))
    dependencies = pin.get("dependency_content_sha256")
    if not isinstance(dependencies, dict) or set(dependencies) != {"zod"}:
        raise PackageIdentityError("Runtime dependency closure pin is absent")
    zod = node_modules / "zod"
    if zod.is_symlink() or directory_sha256(zod) != dependencies["zod"]:
        raise PackageIdentityError("Runtime dependency content differs from pin")
    return {**observed, "code_sha256": _runtime_code_sha256(package)}


def _runtime_code_sha256(package: Path) -> str:
    checksum = hashlib.sha256()
    for path in sorted((package / "dist").glob("*.js")):
        checksum.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return checksum.hexdigest()


def _safe_package_tree(node_modules: Path, package: Path) -> None:
    if not node_modules.is_dir() or node_modules.is_symlink():
        raise PackageIdentityError("Runtime node_modules path is unsafe")
    boundary = node_modules.resolve()
    current = node_modules
    for part in package.relative_to(node_modules).parts:
        current = current / part
        if current.is_symlink() or not current.resolve().is_relative_to(boundary):
            raise PackageIdentityError("Runtime package path is unsafe")
    if not package.is_dir():
        raise PackageIdentityError("Runtime package is absent")
    for entry in package.rglob("*"):
        if entry.is_symlink() or not entry.resolve().is_relative_to(package.resolve()):
            raise PackageIdentityError("Runtime bundle contains unsafe path")


def _package_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PackageIdentityError("Runtime bundle metadata is absent or invalid") from error
    if not isinstance(value, dict):
        raise PackageIdentityError("Runtime bundle metadata must be an object")
    return value


def _bundled_hashes(sdk: Path, zod: Path) -> tuple[str, str]:
    node = shutil.which("node")
    if not node:
        raise PackageIdentityError("Node is required to verify bundled dependency bytes")
    try:
        result = subprocess.run(
            [node, "-e", _BUNDLE_HASH_SCRIPT, str(sdk), str(zod)],
            capture_output=True, text=True, timeout=10, check=False,
            env={key: value for key, value in os.environ.items()
                 if key in {"PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP"}},
        )
        values = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise PackageIdentityError("Runtime bundle hash verification failed") from error
    if (not isinstance(values, list) or len(values) != 2
            or any(not isinstance(value, str) or not _SHA256.fullmatch(value)
                   for value in values)):
        raise PackageIdentityError("Runtime bundle hash verification failed")
    return values[0], values[1]


def _validate_bundled_dependencies(package: Path, pin: dict[str, Any]) -> None:
    if "dependency_content_sha256" in pin:
        raise PackageIdentityError("bundled Runtime pin mixes flat dependency identity")
    bundled = pin.get("bundled_connector_pin")
    connector_keys = {"mode", "name", "version", "source_revision",
                      "component_tree_revision", "component_source_digest_sha256",
                      "public_contract_digest_sha256", "bundle_sha256", "transitive_zod"}
    zod_keys = {"version", "url", "integrity", "bundle_sha256"}
    if (not isinstance(bundled, dict) or set(bundled) != connector_keys
            or bundled.get("mode") != BUNDLED_LAYOUT
            or bundled.get("name") != CONNECTOR_PACKAGE
            or not isinstance(bundled.get("version"), str) or not bundled["version"]
            or any(not isinstance(bundled.get(key), str)
                   or not re.fullmatch(r"[0-9a-f]{40}", bundled[key])
                   for key in ("source_revision", "component_tree_revision"))
            or any(not isinstance(bundled.get(key), str)
                   or not _SHA256.fullmatch(bundled[key])
                   for key in ("component_source_digest_sha256",
                               "public_contract_digest_sha256", "bundle_sha256"))):
        raise PackageIdentityError("bundled Connector pin is incomplete")
    zod_pin = bundled["transitive_zod"]
    if (not isinstance(zod_pin, dict) or set(zod_pin) != zod_keys
            or not isinstance(zod_pin.get("version"), str) or not zod_pin["version"]
            or zod_pin.get("url")
            != f"https://registry.npmjs.org/zod/-/zod-{zod_pin['version']}.tgz"
            or not isinstance(zod_pin.get("integrity"), str)
            or not zod_pin["integrity"].startswith("sha512-")
            or not isinstance(zod_pin.get("bundle_sha256"), str)
            or not _SHA256.fullmatch(zod_pin["bundle_sha256"])):
        raise PackageIdentityError("bundled Zod pin is incomplete")
    identity = _package_json(package / "package-identity.json")
    if (identity.get("schema") != PACKAGE_IDENTITY_SCHEMA
            or identity.get("component_id") != "policy-runtime"
            or identity.get("component_version") != pin.get("version")
            or identity.get("source_revision") != pin.get("source_revision")
            or identity.get("component_tree_revision") != pin.get("component_tree_revision")
            or identity.get("connector_sdk") != bundled):
        raise PackageIdentityError("Runtime package identity differs from bundled pin")
    sdk = package / "node_modules" / CONNECTOR_PACKAGE
    zod = package / "node_modules" / "zod"
    if not sdk.is_dir() or not zod.is_dir():
        raise PackageIdentityError("bundled Connector or Zod is absent")
    runtime_json = _package_json(package / "package.json")
    sdk_json = _package_json(sdk / "package.json")
    zod_json = _package_json(zod / "package.json")
    shrinkwrap = _package_json(package / "npm-shrinkwrap.json")
    entries = shrinkwrap.get("packages")
    if not isinstance(entries, dict) or any(
        not isinstance(entries.get(key), dict)
        for key in ("", f"node_modules/{CONNECTOR_PACKAGE}", "node_modules/zod")
    ):
        raise PackageIdentityError("Runtime bundled dependency metadata is invalid")
    dependencies = {CONNECTOR_PACKAGE: bundled["version"], "zod": zod_pin["version"]}
    sdk_dependencies = {"zod": f"^{zod_pin['version']}"}
    if (runtime_json.get("dependencies") != dependencies
            or runtime_json.get("bundleDependencies") != [CONNECTOR_PACKAGE, "zod"]
            or sdk_json.get("name") != CONNECTOR_PACKAGE
            or sdk_json.get("version") != bundled["version"]
            or sdk_json.get("dependencies") != sdk_dependencies
            or zod_json.get("name") != "zod"
            or zod_json.get("version") != zod_pin["version"]
            or shrinkwrap.get("lockfileVersion") != 3
            or entries.get("", {}).get("dependencies") != dependencies
            or entries.get("", {}).get("bundleDependencies") != [CONNECTOR_PACKAGE, "zod"]
            or entries.get(f"node_modules/{CONNECTOR_PACKAGE}", {}).get("version")
            != bundled["version"]
            or entries.get(f"node_modules/{CONNECTOR_PACKAGE}", {}).get("dependencies")
            != sdk_dependencies
            or entries.get(f"node_modules/{CONNECTOR_PACKAGE}", {}).get("inBundle") is not True
            or entries.get("node_modules/zod", {}).get("version") != zod_pin["version"]
            or entries.get("node_modules/zod", {}).get("resolved") != zod_pin["url"]
            or entries.get("node_modules/zod", {}).get("integrity") != zod_pin["integrity"]
            or entries.get("node_modules/zod", {}).get("inBundle") is not True):
        raise PackageIdentityError("Runtime bundled dependency metadata differs from pin")
    sdk_hash, zod_hash = _bundled_hashes(sdk, zod)
    if sdk_hash != bundled["bundle_sha256"] or zod_hash != zod_pin["bundle_sha256"]:
        raise PackageIdentityError("Runtime bundled dependency bytes differ from pin")


def install_runtime(
    directory: Path,
    pin: dict[str, Any],
    connector_pin: dict[str, Any],
    *,
    archive: Path | None = None,
) -> dict[str, Any]:
    """A local archive is an explicit CLI-only input, still bound to the release hash."""
    from spireagent.workbench.developer_server import instance_lock

    with instance_lock(directory / "runtime-install.lock"):
        try:
            return _install_runtime(directory, pin, connector_pin, archive=archive)
        except PackageIdentityError:
            raise BoundaryError(
                "local_model", "pinned_runtime_install_verification_failed"
            ) from None


def _install_runtime(
    directory: Path,
    pin: dict[str, Any],
    connector_pin: dict[str, Any],
    *,
    archive: Path | None,
) -> dict[str, Any]:
    expected = digest(pin.get("release_asset_sha256"), "local_model.runtime_archive")
    if pin.get("package") != RUNTIME_PACKAGE or not shutil.which("npm"):
        raise BoundaryError("local_model", "pinned_runtime_or_npm_missing")
    # Never replace the package beneath a runtime, including a previous workbench's process.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 15527))
        except OSError:
            raise BoundaryError("local_model", "stop_runtime_before_install") from None
    directory.mkdir(parents=True, exist_ok=True)
    stage = directory / (".runtime-install-" + uuid4().hex)
    target = directory / "runtime"
    backup = directory / (".runtime-backup-" + uuid4().hex)
    installed = False
    try:
        stage.mkdir(mode=0o700)
        payload = stage / "runtime.tgz"
        if archive is not None:
            if (
                archive.is_symlink()
                or not archive.is_file()
                or archive.stat().st_size > ARCHIVE_LIMIT
            ):
                raise BoundaryError("local_model", "runtime_archive_missing_or_unsafe")
            shutil.copyfile(archive, payload)
        else:
            url = pin.get("release_url")
            if not isinstance(url, str) or not url.startswith(RELEASE_PREFIXES):
                raise BoundaryError("local_model", "runtime_release_url_not_pinned")
            request = Request(url, headers={"Accept": "application/octet-stream"})
            size = 0
            try:
                with (
                    build_opener(ProxyHandler({}), ReleaseRedirect()).open(
                        request, timeout=30
                    ) as response,
                    payload.open("xb") as handle,
                ):
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > ARCHIVE_LIMIT:
                            raise BoundaryError("local_model", "runtime_archive_too_large")
                        handle.write(block)
            except OSError:
                raise BoundaryError("local_model", "pinned_runtime_release_unavailable") from None
        if file_sha256(payload) != expected:
            raise BoundaryError("local_model", "runtime_archive_checksum_mismatch")
        # The verified package carries an npm shrinkwrap with exact Connector and zod integrity.
        # npm gets a fixed argument vector and no user/global npm credentials or install scripts.
        (stage / "package.json").write_text(
            canonical_json(
                {
                    "name": "stpd-private-runtime",
                    "version": "0.0.0",
                    "private": True,
                    "dependencies": {RUNTIME_PACKAGE: "file:runtime.tgz"},
                }
            ),
            encoding="utf-8",
        )
        (stage / "user.npmrc").touch(mode=0o600)
        (stage / "global.npmrc").touch(mode=0o600)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP"}
        }
        environment.update(
            {
                "NPM_CONFIG_USERCONFIG": str(stage / "user.npmrc"),
                "NPM_CONFIG_GLOBALCONFIG": str(stage / "global.npmrc"),
                "NPM_CONFIG_CACHE": str(directory / "npm-cache"),
            }
        )
        with (stage / "install.log").open("wb") as log:
            result = subprocess.run(
                ["npm", "install", "--ignore-scripts", "--omit=dev", "--no-audit", "--no-fund"],
                cwd=stage,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=300,
                check=False,
            )
        if result.returncode != 0:
            raise BoundaryError("local_model", "pinned_runtime_npm_install_failed")
        observed = validate_runtime_install(stage / "node_modules", pin, connector_pin)
        if target.is_symlink():
            raise BoundaryError("local_model", "runtime_install_path_unsafe")
        if target.exists():
            target.rename(backup)
        try:
            stage.rename(target)
        except OSError:
            if backup.exists():
                backup.rename(target)
            raise
        installed = True
        return {
            "schema": "stpd/local-models-v1",
            "status": "runtime_installed",
            "runtime_package": observed,
            "loaded": False,
            "activated": False,
        }
    finally:
        if stage.is_dir():
            shutil.rmtree(stage)
        if installed and backup.is_dir():
            shutil.rmtree(backup)
