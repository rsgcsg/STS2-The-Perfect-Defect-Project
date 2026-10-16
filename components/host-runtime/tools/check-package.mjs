#!/usr/bin/env node
import assert from "node:assert/strict";
import { packageHostRuntime } from "./package.mjs";
import { packageFileTreeSha256 } from "../../../tools/bundle-connector-sdk.mjs";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, realpathSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  moduleSpecifierForPath,
  packageEntryHasLaunchAuthority
} from "./package-entry-admission.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const npmExecPath = process.env.npm_execpath;
const smokeRoot = mkdtempSync(path.join(os.tmpdir(), "sts2-host-runtime-package-smoke-"));
const report = packageHostRuntime(path.join(smokeRoot, "first"), { allowDirty: true });
const second = packageHostRuntime(path.join(smokeRoot, "second"), { allowDirty: true });
assert.equal(report.sha256, second.sha256, "same source/build must produce identical Host archive bytes");
if (report?.name !== "@rsgcsg/sts2-host-runtime") {
  throw new Error(`Host Runtime pack resolved the wrong package: ${report?.name ?? "missing"}`);
}
const files = new Set(report.files.map((entry) => entry.path));
const fileMetadata = new Map(report.files.map((entry) => [entry.path, entry]));
const sourcePackage = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
for (const required of [
  "tools/headless.mjs",
  "tools/managed-pe-driver.mjs",
  "tools/managed-host-service.mjs",
  "src/managed-player-environment.mjs",
  "src/managed-host-service.mjs",
  "src/native-card-selection.generated.mjs",
  "src/project-identity.mjs",
  "src/native-episode-tuple.mjs",
  "tools/reference-pe-driver.mjs",
  "consumers/python/sts2_headless/__init__.py",
  "consumers/python/sts2_headless/client.py",
  "consumers/python/sts2_headless/managed_service.py",
  "experiments/managed-exact/manifest.json",
  "package.json",
  "README.md",
  "LICENSE", "src/managed-public-basis.mjs", "package-identity.json", "npm-shrinkwrap.json",
  "node_modules/@rsgcsg/sts2-connector-client/dist/publicBasis.js", "node_modules/zod/package.json"
]) {
  if (!files.has(required)) throw new Error(`Host Runtime package is missing ${required}`);
}
for (const executable of ["tools/headless.mjs", "tools/managed-pe-driver.mjs",
  "tools/managed-host-service.mjs"]) {
  const mode = fileMetadata.get(executable)?.mode;
  const index = spawnSync("git", ["ls-files", "--stage", "--", executable], {
    cwd: root,
    encoding: "utf8",
    stdio: "pipe"
  });
  if (index.error) throw index.error;
  if (index.status !== 0) throw new Error(index.stderr || index.stdout);
  const gitMode = index.stdout.trim().split(/\s+/u)[0];
  const declaredBin = Object.values(sourcePackage.bin ?? {}).includes(executable);
  if (!packageEntryHasLaunchAuthority({
    platform: process.platform,
    npmMode: mode,
    gitMode,
    declaredBin
  })) {
    throw new Error(`Host Runtime package entry is not executable: ${executable}`);
  }
}
const forbidden = [...files].filter((file) =>
  file.startsWith(".local/")
  || file.startsWith("test/")
  || file.startsWith("docs/evidence/")
  || file.split("/").some((segment) => segment === "bin" || segment === "obj" || segment === "__pycache__")
  || /\.(?:dll|exe|pdb|pck|save|pyc|pyo)$/iu.test(file)
);
if (forbidden.length > 0) {
  throw new Error(`Host Runtime package contains forbidden files: ${forbidden.join(", ")}`);
}

const tarball = report.tarball;
const installArgs = ["install", tarball, "--offline", "--ignore-scripts", "--no-audit", "--no-fund"];
const installResult = typeof npmExecPath === "string" && npmExecPath.length > 0
  ? spawnSync(process.execPath, [npmExecPath, ...installArgs], {
      cwd: smokeRoot,
      encoding: "utf8",
      stdio: "pipe"
    })
  : spawnSync(process.platform === "win32" ? "npm.cmd" : "npm", installArgs, {
      cwd: smokeRoot,
      encoding: "utf8",
      stdio: "pipe",
      shell: process.platform === "win32"
    });
if (installResult.error) throw installResult.error;
if (installResult.status !== 0) {
  throw new Error(installResult.stderr || installResult.stdout);
}
const installedRoot = path.join(smokeRoot, "node_modules", "@rsgcsg", "sts2-host-runtime");
const importResult = spawnSync(process.execPath, [
  "--input-type=module",
  "--eval",
  [
    `import { readProjectIdentity } from ${JSON.stringify(moduleSpecifierForPath(
      path.join(installedRoot, "src/project-identity.mjs")))};`,
    `import ${JSON.stringify(moduleSpecifierForPath(path.join(installedRoot, "src/managed-host-service.mjs")))};`,
    `import { projectManagedTextMenuV1 } from ${JSON.stringify(moduleSpecifierForPath(path.join(installedRoot, "src/managed-text-menu-map.mjs")))};`,
    `import * as sdk from ${JSON.stringify(moduleSpecifierForPath(path.join(installedRoot, "node_modules/@rsgcsg/sts2-connector-client/dist/index.js")))};`,
    "if (typeof sdk.decodePublicBasis !== 'function' || typeof sdk.verifyPublicBasis !== 'function') process.exit(5);",
    "if (typeof projectManagedTextMenuV1 !== 'function') process.exit(4);",
    `const identity = readProjectIdentity(${JSON.stringify(installedRoot)});`,
    "if (identity.distribution_kind !== 'installed_package') process.exit(3);",
    "console.log(JSON.stringify(identity));"
  ].join("\n")
], { encoding: "utf8", stdio: "pipe" });
if (importResult.error) throw importResult.error;
if (importResult.status !== 0) {
  throw new Error(importResult.stderr || importResult.stdout);
}
const installedIdentity = JSON.parse(importResult.stdout);
const installedPackage = JSON.parse(readFileSync(path.join(installedRoot, "package.json"), "utf8"));
const installedSdkRoot = path.join(installedRoot, "node_modules", "@rsgcsg", "sts2-connector-client");
const installedSdk = JSON.parse(readFileSync(path.join(installedSdkRoot, "package.json"), "utf8"));
const resolvedSdkRoot = realpathSync(installedSdkRoot);
if (resolvedSdkRoot.startsWith(`${realpathSync(root)}${path.sep}`)) {
  throw new Error("Standalone Host package resolved the Connector SDK from the Platform workspace");
}
const packageIdentity = JSON.parse(readFileSync(path.join(installedRoot, "package-identity.json"), "utf8"));
assert.equal(packageIdentity.connector_sdk.mode, "bundled_source_candidate");
assert.equal(packageFileTreeSha256(installedSdkRoot), packageIdentity.connector_sdk.bundle_sha256);
assert.equal(packageFileTreeSha256(path.join(installedRoot, "node_modules/zod")), packageIdentity.connector_sdk.transitive_zod.bundle_sha256);
assert.equal(installedPackage.dependencies["@rsgcsg/sts2-connector-client"], installedSdk.version);
assert.ok(!JSON.stringify(installedPackage.dependencies).includes("file:"), "no workspace dependency escapes the package");
const installedLock=JSON.parse(readFileSync(path.join(smokeRoot,"package-lock.json"),"utf8"));
assert.ok(Object.entries(installedLock.packages).some(([name,value])=>name.endsWith("node_modules/@rsgcsg/sts2-connector-client")&&value.inBundle===true));
console.log(JSON.stringify({
  status: "host_runtime_package_clean",
  package_report: path.join(smokeRoot, "first", "host-runtime-package.json"),
  package_sha256: report.sha256,
  name: report.name,
  version: report.version,
  filename: report.filename,
  file_count: files.size,
  packed_size: report.size,
  unpacked_size: report.unpackedSize,
  integrity: report.integrity,
  executable_validation: process.platform === "win32"
    ? "declared_bin_and_git_index_mode"
    : "npm_package_entry_mode",
  standalone_smoke: {
    package_name: installedPackage.name,
    package_version: installedPackage.version,
    distribution_kind: installedIdentity.distribution_kind,
    source_digest_sha256: installedIdentity.source_digest_sha256,
    connector_sdk_version: installedSdk.version,
    connector_sdk_resolved_outside_workspace: true,
    managed_menu_and_service_imported: true,
    neutral_capture_sdk_export_verified: true,
    exact_sdk_zod_bundle_hashes_verified: true,
    offline_install: true,
    deterministic_bytes: true
  }
}, null, 2));
