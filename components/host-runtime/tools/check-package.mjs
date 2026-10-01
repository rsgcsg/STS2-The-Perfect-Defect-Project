#!/usr/bin/env node
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
const args = ["pack", "--dry-run", "--json"];
const result = typeof npmExecPath === "string" && npmExecPath.length > 0
  ? spawnSync(process.execPath, [npmExecPath, ...args], {
      cwd: root,
      encoding: "utf8",
      stdio: "pipe"
    })
  : spawnSync(process.platform === "win32" ? "npm.cmd" : "npm", args, {
      cwd: root,
      encoding: "utf8",
      stdio: "pipe",
      shell: process.platform === "win32"
    });
if (result.error) throw result.error;
if (result.status !== 0) throw new Error(result.stderr || result.stdout);

const report = JSON.parse(result.stdout)[0];
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
  "src/project-identity.mjs",
  "consumers/python/sts2_headless/__init__.py",
  "consumers/python/sts2_headless/client.py",
  "consumers/python/sts2_headless/managed_service.py",
  "experiments/managed-exact/manifest.json",
  "package.json",
  "README.md",
  "LICENSE"
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
  || file.split("/").some((segment) => segment === "bin" || segment === "obj")
  || /\.(?:dll|exe|pdb|pck|save)$/iu.test(file)
);
if (forbidden.length > 0) {
  throw new Error(`Host Runtime package contains forbidden files: ${forbidden.join(", ")}`);
}

const smokeRoot = mkdtempSync(path.join(os.tmpdir(), "sts2-host-runtime-package-smoke-"));
const packResult = typeof npmExecPath === "string" && npmExecPath.length > 0
  ? spawnSync(process.execPath, [npmExecPath, "pack", "--pack-destination", smokeRoot, "--json"], {
      cwd: root,
      encoding: "utf8",
      stdio: "pipe"
    })
  : spawnSync(process.platform === "win32" ? "npm.cmd" : "npm", [
      "pack",
      "--pack-destination",
      smokeRoot,
      "--json"
    ], {
      cwd: root,
      encoding: "utf8",
      stdio: "pipe",
      shell: process.platform === "win32"
    });
if (packResult.error) throw packResult.error;
if (packResult.status !== 0) throw new Error(packResult.stderr || packResult.stdout);
const packed = JSON.parse(packResult.stdout)[0];
const tarball = path.join(smokeRoot, packed.filename);
const installArgs = ["install", tarball, "--ignore-scripts", "--no-audit", "--no-fund"];
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
const installedSdkRoot = path.join(smokeRoot, "node_modules", "@rsgcsg", "sts2-connector-client");
const installedSdk = JSON.parse(readFileSync(path.join(installedSdkRoot, "package.json"), "utf8"));
const resolvedSdkRoot = realpathSync(installedSdkRoot);
if (resolvedSdkRoot.startsWith(`${realpathSync(root)}${path.sep}`)) {
  throw new Error("Standalone Host package resolved the Connector SDK from the Platform workspace");
}
console.log(JSON.stringify({
  status: "host_runtime_package_clean",
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
    connector_sdk_resolved_outside_workspace: true
  }
}, null, 2));
