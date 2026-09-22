#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, realpathSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { componentRoot, npm, packageRuntime } from "./package.mjs";

const root = mkdtempSync(path.join(os.tmpdir(), "sts2-policy-installed-check-"));
try {
  const first = packageRuntime(path.join(root, "first"), { allowDirty: true });
  const second = packageRuntime(path.join(root, "second"), { allowDirty: true });
  assert.equal(first.sha256, second.sha256, "identical source/build must pack identical bytes");
  for (const name of ["bin/policy-runtime.mjs", "bin/policy-port.mjs", "dist/index.js", "dist/index.d.ts", "package-identity.json", "npm-shrinkwrap.json", "LICENSE"]) assert.ok(first.files.includes(name), `missing package file ${name}`);
  assert.ok(first.files.every((name) => /^(?:bin\/[^/]+\.mjs|dist\/[^/]+\.(?:js|d\.ts|js\.map)|README\.md|LICENSE|package\.json|package-identity\.json|npm-shrinkwrap\.json)$/u.test(name)), "unexpected source or local package data");
  npm(["install", first.tarball, "--ignore-scripts", "--no-audit", "--no-fund"], root);
  const installed = path.join(root, "node_modules", "@rsgcsg", "sts2-policy-runtime");
  assert.ok(!realpathSync(installed).startsWith(`${realpathSync(componentRoot)}${path.sep}`));
  const installedLock = JSON.parse(readFileSync(path.join(root, "package-lock.json"), "utf8"));
  const sdkEntry = Object.entries(installedLock.packages).find(([name]) => name.endsWith("node_modules/@rsgcsg/sts2-connector-client"))?.[1];
  assert.equal(sdkEntry?.resolved, first.identity.connector_sdk.url);
  assert.equal(sdkEntry?.integrity, first.identity.connector_sdk.integrity);
  copyFileSync(path.join(componentRoot, "tools/installed-smoke.mjs"), path.join(root, "smoke.mjs"));
  const smoke = spawnSync(process.execPath, ["smoke.mjs"], { cwd: root, encoding: "utf8", timeout: 20000 });
  if (smoke.error) throw smoke.error;
  assert.equal(smoke.status, 0, smoke.stderr || smoke.stdout);
  assert.equal(JSON.parse(smoke.stdout).version, first.version,
    "installed CLI runtime version must match the published package version");
  console.log(JSON.stringify({ status: "policy_runtime_package_clean", sha256: first.sha256, deterministic: true, connector_sdk: first.identity.connector_sdk, installed_cpu_smoke: JSON.parse(smoke.stdout) }, null, 2));
} finally { rmSync(root, { recursive: true, force: true }); }
