#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { readIdentityReport } from "../../../tools/component-identity.mjs";

export const componentRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(componentRoot, "../..");
const sdkRoot = path.resolve(componentRoot, "../connector/sdk/typescript");
const sdkName = "@rsgcsg/sts2-connector-client";
const sdkSourceDependency = "file:../connector/sdk/typescript";
export function npm(args, cwd) {
  const npmPath = process.env.npm_execpath;
  const result = npmPath
    ? spawnSync(process.execPath, [npmPath, ...args], { cwd, encoding: "utf8" })
    : spawnSync(process.platform === "win32" ? "npm.cmd" : "npm", args, { cwd, encoding: "utf8", shell: process.platform === "win32" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stderr || result.stdout);
  return result.stdout;
}

export function packageRuntime(outputDirectory, { allowDirty = false } = {}) {
  const source = JSON.parse(readFileSync(path.join(componentRoot, "package.json"), "utf8"));
  const lock = JSON.parse(readFileSync(path.join(componentRoot, "package-lock.json"), "utf8"));
  const sdkSource = JSON.parse(readFileSync(path.join(sdkRoot, "package.json"), "utf8"));
  const sdkLock = JSON.parse(readFileSync(path.join(sdkRoot, "package-lock.json"), "utf8"));
  const identities = readIdentityReport(repositoryRoot).components;
  const identity = identities["policy-runtime"];
  const sdkIdentity = identities.connector;
  if (!allowDirty && (identity.source_worktree_status !== "clean" || sdkIdentity.source_worktree_status !== "clean")) throw new Error("Commit Policy Runtime and Connector SDK source before creating an external candidate package");
  if (source.dependencies?.[sdkName] !== sdkSourceDependency || lock.packages[""]?.dependencies?.[sdkName] !== sdkSourceDependency
      || lock.packages[`node_modules/${sdkName}`]?.resolved !== "../connector/sdk/typescript"
      || lock.packages[`node_modules/${sdkName}`]?.link !== true
      || lock.packages["../connector/sdk/typescript"]?.version !== sdkSource.version
      || sdkSource.name !== sdkName) throw new Error("Policy Runtime requires the exact workspace Connector SDK dependency and lock");
  if (sdkLock.packages[""]?.version !== sdkSource.version || sdkSource.dependencies?.zod !== "^3.25.76") throw new Error("Connector SDK package and lock disagree");
  const zod = sdkLock.packages["node_modules/zod"];
  if (zod?.version !== "3.25.76" || !/^https:\/\/registry\.npmjs\.org\/zod\/-\/zod-3\.25\.76\.tgz$/u.test(zod.resolved ?? "") || !/^sha512-/u.test(zod.integrity ?? "")) throw new Error("Connector SDK requires exact locked registry zod");
  // Build the SDK whose source identity is recorded below. Never package a
  // previously copied node_modules SDK or an unreleased URL under that identity.
  npm(["run", "build"], sdkRoot);
  if (!existsSync(path.join(sdkRoot, "dist", "textMenu.js")) ||
      !readFileSync(path.join(sdkRoot, "dist", "index.js"), "utf8").includes('export * from "./textMenu.js"')) {
    throw new Error("Connector SDK build does not export the required text menu profile");
  }
  const stage = mkdtempSync(path.join(os.tmpdir(), "sts2-policy-package-stage-"));
  const zodInstall = mkdtempSync(path.join(os.tmpdir(), "sts2-policy-zod-stage-"));
  try {
    const zodPackage = { name: "sts2-policy-zod-lock-check", version: "1.0.0", private: true, dependencies: { zod: zod.version } };
    const zodLock = { name: zodPackage.name, version: zodPackage.version, lockfileVersion: 3, requires: true,
      packages: { "": zodPackage, "node_modules/zod": zod } };
    writeFileSync(path.join(zodInstall, "package.json"), `${JSON.stringify(zodPackage, null, 2)}\n`);
    writeFileSync(path.join(zodInstall, "package-lock.json"), `${JSON.stringify(zodLock, null, 2)}\n`);
    npm(["ci", "--ignore-scripts", "--no-audit", "--no-fund"], zodInstall);
    const sdkStage = path.join(stage, "node_modules", "@rsgcsg", "sts2-connector-client");
    mkdirSync(sdkStage, { recursive: true });
    cpSync(path.join(sdkRoot, "package.json"), path.join(sdkStage, "package.json"));
    cpSync(path.join(sdkRoot, "dist"), path.join(sdkStage, "dist"), { recursive: true });
    const zodStage = path.join(stage, "node_modules", "zod");
    cpSync(path.join(zodInstall, "node_modules", "zod"), zodStage, { recursive: true });
    const sdkBundleSha256 = fileTreeSha256(sdkStage);
    const zodBundleSha256 = fileTreeSha256(zodStage);
    const dependencies = { [sdkName]: sdkSource.version, zod: zod.version };
    const bundleDependencies = [sdkName, "zod"];
    const packedPackage = { ...source, dependencies, bundleDependencies, files: ["bin", "dist", "README.md", "LICENSE", "package-identity.json", "npm-shrinkwrap.json"] };
    delete packedPackage.devDependencies;
    delete packedPackage.scripts;
    const packages = { "": { name: source.name, version: source.version, license: source.license, dependencies, bundleDependencies, bin: source.bin, engines: source.engines } };
    for (const [key, value] of Object.entries(lock.packages)) {
      if (!key || value.dev) continue;
      if (key === `node_modules/${sdkName}` || key === "../connector/sdk/typescript") continue;
      if (value.link || !key.startsWith("node_modules/") || !/^https:\/\//u.test(value.resolved ?? "") || !/^sha512-/u.test(value.integrity ?? "")) throw new Error(`Nonportable production dependency: ${key}`);
      packages[key] = value;
    }
    packages[`node_modules/${sdkName}`] = { version: sdkSource.version, inBundle: true, license: sdkSource.license, dependencies: sdkSource.dependencies, engines: sdkSource.engines };
    packages["node_modules/zod"] = { version: zod.version, inBundle: true, license: zod.license, funding: zod.funding };
    const shrinkwrap = { name: source.name, version: source.version, lockfileVersion: 3, requires: true, packages };
    const packageIdentity = { schema: "sts2.policy-runtime/package-identity-1", ...identity, connector_sdk: { mode: "bundled_source_candidate", name: sdkName, version: sdkSource.version, source_revision: sdkIdentity.source_revision, component_tree_revision: sdkIdentity.component_tree_revision, component_source_digest_sha256: sdkIdentity.component_source_digest_sha256, public_contract_digest_sha256: sdkIdentity.public_contract_digest_sha256, bundle_sha256: sdkBundleSha256, transitive_zod: { version: zod.version, url: zod.resolved, integrity: zod.integrity, bundle_sha256: zodBundleSha256 } } };
    for (const name of ["bin", "dist", "README.md"]) cpSync(path.join(componentRoot, name), path.join(stage, name), { recursive: true });
    cpSync(path.resolve(componentRoot, "../../LICENSE"), path.join(stage, "LICENSE"));
    for (const [name, value] of Object.entries({ "package.json": packedPackage, "npm-shrinkwrap.json": shrinkwrap, "package-identity.json": packageIdentity })) writeFileSync(path.join(stage, name), `${JSON.stringify(value, null, 2)}\n`);
    mkdirSync(outputDirectory, { recursive: true });
    const report = JSON.parse(npm(["pack", "--ignore-scripts", "--json", "--pack-destination", outputDirectory], stage))[0];
    const tarball = path.join(outputDirectory, report.filename);
    const sha256 = createHash("sha256").update(readFileSync(tarball)).digest("hex");
    const result = { schema: "sts2.policy-runtime/package-report-1", name: report.name, version: report.version, filename: report.filename, sha256, integrity: report.integrity, identity: packageIdentity, files: report.files.map((entry) => entry.path) };
    writeFileSync(path.join(outputDirectory, "policy-runtime-package.json"), `${JSON.stringify(result, null, 2)}\n`);
    writeFileSync(path.join(outputDirectory, "checksums.sha256"), `${sha256}  ${report.filename}\n`);
    return { ...result, tarball };
  } finally { rmSync(stage, { recursive: true, force: true }); rmSync(zodInstall, { recursive: true, force: true }); }
}

export function fileTreeSha256(root) {
  const digest = createHash("sha256");
  function visit(directory, prefix = "") {
    for (const entry of readdirSync(directory, { withFileTypes: true }).sort((left, right) => left.name.localeCompare(right.name))) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(full, relative);
      else if (entry.isFile()) digest.update(relative).update("\0").update(readFileSync(full)).update("\0");
      else throw new Error(`Connector SDK package contains unsupported entry: ${relative}`);
    }
  }
  visit(root);
  return digest.digest("hex");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args.length !== 2 || args[0] !== "--output") throw new Error("Usage: npm run package -- --output /absolute/output-directory");
  console.log(JSON.stringify(packageRuntime(path.resolve(args[1])), null, 2));
}
