#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, realpathSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { componentRoot, fileTreeSha256, npm, packageRuntime } from "./package.mjs";

const root = mkdtempSync(path.join(os.tmpdir(), "sts2-policy-installed-check-"));
try {
  const first = packageRuntime(path.join(root, "first"), { allowDirty: true });
  const second = packageRuntime(path.join(root, "second"), { allowDirty: true });
  assert.equal(first.sha256, second.sha256, "identical source/build must pack identical bytes");
  for (const name of ["bin/policy-runtime.mjs", "bin/policy-port.mjs", "dist/index.js", "dist/index.d.ts", "package-identity.json", "npm-shrinkwrap.json", "LICENSE"]) assert.ok(first.files.includes(name), `missing package file ${name}`);
  assert.ok(first.files.some((name) => name === "node_modules/@rsgcsg/sts2-connector-client/dist/textMenu.js"), "text menu SDK missing from Runtime package");
  assert.ok(first.files.some((name) => name === "node_modules/zod/package.json"), "locked Zod missing from Runtime package");
  assert.ok(first.files.every((name) => /^(?:bin\/[^/]+\.mjs|dist\/[^/]+\.(?:js|d\.ts|js\.map)|node_modules\/@rsgcsg\/sts2-connector-client\/(?:package\.json|dist\/[A-Za-z0-9._/-]+\.(?:js|d\.ts|js\.map))|node_modules\/zod\/[A-Za-z0-9._/-]+|README\.md|LICENSE|package\.json|package-identity\.json|npm-shrinkwrap\.json)$/u.test(name)), "unexpected source or local package data");
  npm(["install", first.tarball, "--ignore-scripts", "--no-audit", "--no-fund"], root);
  const installed = path.join(root, "node_modules", "@rsgcsg", "sts2-policy-runtime");
  assert.ok(!realpathSync(installed).startsWith(`${realpathSync(componentRoot)}${path.sep}`));
  const installedLock = JSON.parse(readFileSync(path.join(root, "package-lock.json"), "utf8"));
  const sdkEntry = Object.entries(installedLock.packages).find(([name]) => name.endsWith("node_modules/@rsgcsg/sts2-connector-client"))?.[1];
  assert.equal(sdkEntry?.version, first.identity.connector_sdk.version);
  assert.equal(sdkEntry?.inBundle, true);
  const sdkDirectory = path.join(installed, "node_modules", "@rsgcsg", "sts2-connector-client");
  assert.ok(realpathSync(sdkDirectory).startsWith(`${realpathSync(installed)}${path.sep}`));
  assert.equal(fileTreeSha256(sdkDirectory), first.identity.connector_sdk.bundle_sha256);
  const zodEntry = Object.entries(installedLock.packages).find(([name]) => name.endsWith("node_modules/zod"))?.[1];
  assert.equal(zodEntry?.version, first.identity.connector_sdk.transitive_zod.version);
  assert.equal(zodEntry?.inBundle, true);
  assert.equal(fileTreeSha256(path.join(installed, "node_modules", "zod")), first.identity.connector_sdk.transitive_zod.bundle_sha256);
  copyFileSync(path.join(componentRoot, "tools/installed-smoke.mjs"), path.join(root, "smoke.mjs"));
  const smoke = spawnSync(process.execPath, ["smoke.mjs"], { cwd: root, encoding: "utf8", timeout: 20000 });
  if (smoke.error) throw smoke.error;
  assert.equal(smoke.status, 0, smoke.stderr || smoke.stdout);
  assert.equal(JSON.parse(smoke.stdout).version, first.version,
    "installed CLI runtime version must match the published package version");
  console.log(JSON.stringify({ status: "policy_runtime_package_clean", sha256: first.sha256, deterministic: true, connector_sdk: first.identity.connector_sdk, installed_cpu_smoke: JSON.parse(smoke.stdout) }, null, 2));
} finally { rmSync(root, { recursive: true, force: true }); }
