#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { managedSelectorArtifactErrors } from "./generate-managed-selector-validator.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const predecessorRelease = /https:\/\/github\.com\/rsgcsg\/(?:STS2-Connector|STS2-headless|STS2-human-Annotator)(?:\/|["'])/u;
const localWorkspacePath = /(?:\/Users\/[^/\s]+\/|\/home\/[^/\s]+\/|[A-Za-z]:\\Users\\[^\\\s]+\\)/u;

export function textBoundaryErrors(relative, contents) {
  const errors = [];
  const normalized = relative.split(path.sep).join("/");
  const historyOnly = normalized.startsWith("migration/")
    || normalized.includes("/docs/evidence/")
    || normalized.includes("/archive/");
  if (!historyOnly && predecessorRelease.test(contents)) {
    errors.push(`${normalized}: active predecessor repository URL`);
  }
  if (!historyOnly && localWorkspacePath.test(contents)) {
    errors.push(`${normalized}: user-specific absolute path in production source`);
  }
  if (normalized.startsWith("components/connector/")
      && /(?:from\s+|import\s*\()\s*["'][^"']*(?:host-runtime|annotator)/u.test(contents)) {
    errors.push(`${normalized}: Connector imports a downstream component`);
  }
  if (normalized.startsWith("components/native-foundation/")
      && /STS2Connector|STS2HumanAnnotator|HarmonyLib|System\.Text\.Json/u.test(contents)) {
    errors.push(`${normalized}: Native Foundation imports transport, evidence, or patch ownership`);
  }
  if (normalized.startsWith("components/host-runtime/")
      && /(?:from\s+|import\s*\()\s*["'][^"']*(?:\.\.\/)+(?:connector|annotator)(?:\/|["'])/u.test(contents)) {
    errors.push(`${normalized}: Host Runtime imports another component implementation`);
  }
  return errors;
}

export function packageEntrypointErrors(
  relativePackage,
  packageJson,
  trackedFiles,
  exists = () => true
) {
  const errors = [];
  const packageDirectory = path.posix.dirname(relativePackage);
  const declared = typeof packageJson.bin === "string"
    ? [packageJson.bin]
    : Object.values(packageJson.bin ?? {});
  for (const target of declared) {
    if (typeof target !== "string" || target.length === 0) {
      errors.push(`${relativePackage}: invalid bin target`);
      continue;
    }
    const relativeTarget = path.posix.normalize(path.posix.join(packageDirectory, target));
    if (!exists(relativeTarget)) {
      errors.push(`${relativePackage}: bin target is missing: ${relativeTarget}`);
    }
    if (!trackedFiles.has(relativeTarget)) {
      errors.push(`${relativePackage}: bin target is not tracked: ${relativeTarget}`);
    }
  }
  return errors;
}

function visit(workspaceRoot, directory, errors) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (["bin", "obj", "out", "dist", "node_modules", ".local"].includes(entry.name)) continue;
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) visit(workspaceRoot, file, errors);
    else if (entry.name.includes(".test.") || entry.name.startsWith("test-")) continue;
    else if (/\.(?:cs|csproj|js|mjs|ts|json)$/u.test(entry.name)) {
      const relative = path.relative(workspaceRoot, file);
      errors.push(...textBoundaryErrors(relative, fs.readFileSync(file, "utf8")));
    }
  }
}

export function collectBoundaryErrors(workspaceRoot = root) {
  const errors = managedSelectorArtifactErrors(workspaceRoot);
  const rootPackage = JSON.parse(fs.readFileSync(path.join(workspaceRoot, "package.json"), "utf8"));
  const expectedWorkspaces = [
    "components/connector/sdk/typescript",
    "components/host-runtime",
    "components/annotator",
    "components/evidence",
    "components/policy-runtime",
    "apps/workbench",
    "apps/ingame-ui",
    "apps/game-mod"
  ];
  if (JSON.stringify(rootPackage.workspaces) !== JSON.stringify(expectedWorkspaces)) {
    errors.push("package.json: workspace dependency graph is not the admitted Platform graph");
  }

  let trackedFiles = new Set();
  try {
    trackedFiles = new Set(execFileSync(
      "git",
      ["-C", workspaceRoot, "ls-files", "-z"],
      { encoding: "utf8" }
    ).split("\0").filter(Boolean));
  } catch (error) {
    errors.push(`git index unavailable for source-completeness check: ${error.message}`);
  }
  for (const workspace of expectedWorkspaces) {
    const relativePackage = `${workspace}/package.json`;
    const packageJson = JSON.parse(fs.readFileSync(path.join(workspaceRoot, relativePackage), "utf8"));
    errors.push(...packageEntrypointErrors(
      relativePackage,
      packageJson,
      trackedFiles,
      (relative) => fs.existsSync(path.join(workspaceRoot, relative))
    ));
  }

  const hostPackage = JSON.parse(fs.readFileSync(
    path.join(workspaceRoot, "components/host-runtime/package.json"),
    "utf8"
  ));
  const sdkDependency = hostPackage.dependencies?.["@rsgcsg/sts2-connector-client"];
  if (!/^https:\/\/github\.com\/rsgcsg\/STS2-The-Pefect-Defect-Project\/releases\/download\/compat\/platform-import-v1\//u.test(
    sdkDependency ?? ""
  )) {
    errors.push("Host Runtime must consume the versioned public Platform Connector SDK asset");
  }

  const connectorRelease = fs.readFileSync(
    path.join(workspaceRoot, "components/host-runtime/src/connector-release.mjs"),
    "utf8"
  );
  if (!/rsgcsg\/STS2-The-Pefect-Defect-Project\/releases\/download\/compat\/platform-import-v1/u.test(connectorRelease)) {
    errors.push("Host Runtime setup must consume the versioned Platform Connector Host asset");
  }

  const workstationAdapter = fs.readFileSync(
    path.join(workspaceRoot, "components/annotator/tools/workstation-platform.mjs"),
    "utf8"
  );
  if (!workstationAdapter.includes('"workstation-api.mjs"')) {
    errors.push("Annotator must consume the explicit Host Runtime workstation API seam");
  }
  for (const forbidden of ["game-installation.mjs", "runtime-probe.mjs", "headless-host.mjs"]) {
    if (workstationAdapter.includes(forbidden)) {
      errors.push(`Annotator directly imports Host Runtime implementation: ${forbidden}`);
    }
  }

  const annotatorProject = fs.readFileSync(
    path.join(workspaceRoot, "components/annotator/src/STS2HumanAnnotator.Mod/STS2HumanAnnotator.Mod.csproj"),
    "utf8"
  );
  if (!annotatorProject.includes("../../../connector/host/out/STS2_MCP/STS2_MCP.dll")) {
    errors.push("Annotator Mod must consume the exact component-local Connector build artifact");
  }
  if (annotatorProject.includes('ProjectReference Include="../../../connector')) {
    errors.push("Annotator Mod must not create an untracked second Connector build");
  }

  for (const relative of [
    "components/native-foundation/src",
    "components/connector/host",
    "components/connector/sdk",
    "components/connector/tools",
    "components/host-runtime/src",
    "components/host-runtime/tools",
    "components/annotator/src",
    "components/annotator/tools",
    "components/evidence/sts2_platform_evidence",
    "components/policy-runtime/src",
    "apps/workbench/src",
    "apps/workbench/bin",
    "apps/ingame-ui",
    "apps/game-mod"
  ]) {
    visit(workspaceRoot, path.join(workspaceRoot, relative), errors);
  }
  return errors;
}

function main() {
  const errors = collectBoundaryErrors();
  if (errors.length > 0) throw new Error(errors.join("\n"));
  console.log("platform dependency boundary checks passed");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
