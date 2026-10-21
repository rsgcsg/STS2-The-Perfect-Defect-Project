#!/usr/bin/env node
// Build-only Git authority. Installed tools verify their pinned bytes, never a checkout.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { componentGitState } from "../../../tools/component-git.mjs";
import { sourceSetIdentity, sourceSetMatches } from "../../../apps/game-mod/source-identity.mjs";

// Authenticated release capability, not an inference from a working CLI.
export const sourceV3Support = Object.freeze({
  schema: "sts2.evidence/source3-collection-support-1",
  command: "pack-source-v3",
  recording_schema: "sts2.annotator/source-session-manifest-3",
  source_profile: "native-logical-source-v3",
  bundle_schema: "sts2.annotator/source-session-bundle-3",
  type_id: "source-session-bundle-v3"
});

export const p1NativeSupport = Object.freeze({
  schema: "sts2.evidence/p1-native-collection-support-1",
  source_profile: "human-p1-native-decisions-1",
  recording_schema: "sts2.human-annotator/recording-manifest-2",
  bundle_schema: "sts2.human-annotator/p1-native-session-bundle-1",
  prefix_schema: "sts2.human-annotator/p1-native-prefix-1",
  audit_command: "audit-p1-native", pack_command: "pack-p1-native",
  type_id: "p1-native-session-bundle-v1",
  recording_client_entrypoint: "setup/tools/p1-native-recording.mjs",
  converter_entrypoint: "p1/tools/p1-native-to-p1.mjs",
  segment_schema: "sts2.p1/native-segments-1",
  selector_transaction_projection: "native-selector-finalized-d-1",
  target_projection: "native-timeline-targets-1",
  target_schema: "sts2.p1/native-targets-1"
});

export const p1NativeToolFiles = ["p1-native-to-p1.mjs", "p1-native-bundle.mjs",
  "p1-native-cancellation.mjs", "p1-native-presentation.mjs", "p1-native-segments.mjs",
  "p1-native-selector-transactions.mjs", "p1-native-targets.mjs", "p1-native-issue-spans.mjs"];

/** The fixed reader ships its executable SDK/zod closure, not a worktree path. */
export function copyP1NativeTools(workspaceRoot, staging) {
  for (const name of p1NativeToolFiles) {
    const destination = path.join(staging, "p1/tools", name);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(path.join(workspaceRoot, "tools", name), destination);
  }
  const client = path.join(staging, p1NativeSupport.recording_client_entrypoint);
  fs.mkdirSync(path.dirname(client), { recursive: true });
  fs.copyFileSync(path.join(workspaceRoot, "tools/p1-native-recording.mjs"), client);
  const sdk = path.join(workspaceRoot, "components/connector/sdk/typescript");
  const destination = path.join(staging, "p1/components/connector/sdk/typescript");
  fs.mkdirSync(destination, { recursive: true });
  fs.cpSync(path.join(sdk, "dist"), path.join(destination, "dist"), { recursive: true });
  fs.copyFileSync(path.join(sdk, "package.json"), path.join(destination, "package.json"));
  const zod = path.dirname(createRequire(path.join(sdk, "package.json")).resolve("zod/package.json"));
  fs.cpSync(zod, path.join(staging, "p1/node_modules/zod"), { recursive: true, dereference: true });
}

export const setupFiles = [
  "apps/game-mod/collection-setup.mjs",
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

export function collectionIdentity(state, files, modProvenance) {
  return {
    product: "STS2 Platform Collection Tool", version: 1, worktree: "clean",
    workspace_revision: state.workspaceRevision,
    source_revision: state.componentSourceRevision,
    component_tree_revision: state.componentTreeRevision,
    entrypoint: "sts2-human-annotator.dll",
    supported_recording_schema: "sts2.human-annotator/recording-manifest-2",
    output_schema: "sts2.human-annotator/session-bundle-3",
    source_v3_support: sourceV3Support,
    p1_native_support: p1NativeSupport,
    interrupted_recovery_schema: "sts2.human-annotator/interrupted-recovery-1",
    collection_setup_entrypoint: "setup/apps/game-mod/collection-setup.mjs",
    ...(modProvenance ? { collection_setup_provenance: "game-mod/build-provenance.json" } : {}),
    files
  };
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
    execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", ["run", "build"], {
      cwd: path.join(state.workspaceRoot, "components/connector/sdk/typescript"), stdio: "inherit"
    });
    copyP1NativeTools(state.workspaceRoot, staging);
    if (componentGitState(componentRoot).workspaceWorktreeStatus !== "clean"
        || componentGitState(componentRoot).workspaceRevision !== state.workspaceRevision)
      throw new Error("Source identity changed during collection tool publication");
    const identity = collectionIdentity(state, inventory(staging), modProvenance);
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
