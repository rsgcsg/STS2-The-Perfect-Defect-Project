#!/usr/bin/env node
// Build-only Git authority. Installed tools verify their pinned bytes, never a checkout.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { componentGitState } from "../../../tools/component-git.mjs";
import { sourceSetIdentity, sourceSetMatches } from "../../../apps/game-mod/source-identity.mjs";

export const setupFiles = [
  "apps/game-mod/collection-setup.mjs",
  "apps/game-mod/platform-installation.mjs",
  "apps/game-mod/annotator-configuration.mjs",
  "apps/game-mod/loaded-evidence.mjs",
  "components/host-runtime/src/game-installation.mjs",
  "components/host-runtime/src/game-processes.mjs"
];

export function copyCollectionSetup(workspaceRoot, staging, modProvenance) {
  for (const relative of setupFiles) {
    const destination = path.join(staging, "setup", relative);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(path.join(workspaceRoot, relative), destination);
  }
  if (modProvenance) {
    const provenance = JSON.parse(fs.readFileSync(modProvenance, "utf8"));
    if (provenance.schema !== "sts2.platform/game-mod-build-provenance-1"
        || !sourceSetMatches(provenance.source, sourceSetIdentity(workspaceRoot)))
      throw new Error("Collection setup requires exact current native build provenance.");
    const dll = path.join(path.dirname(modProvenance), "STS2_PLATFORM.dll");
    const sha = crypto.createHash("sha256").update(fs.readFileSync(dll)).digest("hex");
    if (sha !== provenance.artifact?.sha256) throw new Error("Collection setup native build bytes differ.");
    fs.mkdirSync(path.join(staging, "game-mod"), { recursive: true });
    fs.copyFileSync(modProvenance, path.join(staging, "game-mod/build-provenance.json"));
  }
}

export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

export function inventory(directory) {
  const files = [];
  function visit(current, prefix = "") {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const name = prefix + entry.name;
      if (entry.isSymbolicLink()) throw new Error("Collection tool release cannot contain symlinks");
      if (entry.isDirectory()) visit(path.join(current, entry.name), `${name}/`);
      else if (entry.isFile()) {
        const bytes = fs.readFileSync(path.join(current, entry.name));
        files.push({ path: name, bytes: bytes.length, sha256: crypto.createHash("sha256").update(bytes).digest("hex") });
      } else throw new Error("Unsupported collection tool file type");
    }
  }
  visit(directory);
  return files.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
}

export function publishCollectionTool(componentRoot, output, { dotnet = "dotnet", modProvenance } = {}) {
  const state = componentGitState(componentRoot);
  if (state.workspaceWorktreeStatus !== "clean")
    throw new Error("Collection tool release requires an exact clean workspace; commit first.");
  const destination = path.resolve(output);
  if (fs.existsSync(destination)) throw new Error("Collection tool output already exists; releases are immutable.");
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  const staging = fs.mkdtempSync(path.join(path.dirname(destination), ".collection-tool-"));
  try {
    execFileSync(dotnet, ["publish", path.join(componentRoot, "src/STS2HumanAnnotator.Tool/STS2HumanAnnotator.Tool.csproj"),
      "-c", "Release", "-o", staging, "--self-contained", "false", "-p:UseAppHost=false"],
    { stdio: "inherit" });
    fs.copyFileSync(path.join(state.workspaceRoot, "platform-bom.json"), path.join(staging, "platform-bom.json"));
    copyCollectionSetup(state.workspaceRoot, staging, modProvenance);
    if (componentGitState(componentRoot).workspaceWorktreeStatus !== "clean"
        || componentGitState(componentRoot).workspaceRevision !== state.workspaceRevision)
      throw new Error("Source identity changed during collection tool publication");
    const identity = {
      product: "STS2 Platform Collection Tool", version: 1, worktree: "clean",
      workspace_revision: state.workspaceRevision,
      source_revision: state.componentSourceRevision,
      component_tree_revision: state.componentTreeRevision,
      entrypoint: "sts2-human-annotator.dll",
      supported_recording_schema: "sts2.human-annotator/recording-manifest-2",
      output_schema: "sts2.human-annotator/session-bundle-3",
      interrupted_recovery_schema: "sts2.human-annotator/interrupted-recovery-1",
      collection_setup_entrypoint: "setup/apps/game-mod/collection-setup.mjs",
      ...(modProvenance ? { collection_setup_provenance: "game-mod/build-provenance.json" } : {}),
      files: inventory(staging)
    };
    const release_id = crypto.createHash("sha256").update(canonical(identity)).digest("hex");
    fs.writeFileSync(path.join(staging, "collection-tool.json"),
      `${canonical({ schema: "sts2.evidence/collection-tool-1", release_id, identity })}\n`);
    fs.renameSync(staging, destination);
    return { status: "published", directory: destination, release_id, identity };
  } catch (error) {
    fs.rmSync(staging, { recursive: true, force: true });
    throw error;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (![2, 4].includes(args.length) || args[0] !== "--output" || (args.length === 4 && args[2] !== "--mod-provenance"))
    throw new Error("usage: publish-collection-tool.mjs --output /absolute/new-release-directory [--mod-provenance /absolute/build-provenance.json]");
  console.log(JSON.stringify(publishCollectionTool(path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."), args[1], { modProvenance: args[3] }), null, 2));
}
