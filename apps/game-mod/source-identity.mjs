import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  componentGitFiles,
  componentGitState
} from "../../tools/component-git.mjs";
import {
  playerEnvironmentSourceIdentity,
  sourceRevisionForFiles
} from "../../components/connector/tools/connector-provenance.mjs";

export function evaluateGameModVersions({ packageVersion, manifestVersion, nativeSource, projectSource }) {
  const native = String(nativeSource).replace(/\/\*[\s\S]*?\*\//gu, "");
  const project = String(projectSource).replace(/<!--[\s\S]*?-->/gu, "");
  const nativeValues = [...native.matchAll(/^\s*public const string Version = "([^"\r\n]+)";/gmu)];
  const projectValues = [...project.matchAll(/<Version>\s*([^<>]+?)\s*<\/Version>/gu)];
  const nativeVersion = nativeValues.length === 1 ? nativeValues[0][1] : null;
  const projectVersion = projectValues.length === 1 ? projectValues[0][1] : null;
  const errors = [];
  if (typeof packageVersion !== "string" || packageVersion.length === 0)
    errors.push("Game Mod package version is missing");
  if (manifestVersion !== packageVersion) errors.push("Game Mod manifest version differs from package");
  if (nativeVersion === null) errors.push("UnifiedPlatformMod.Version requires exactly one literal declaration");
  else if (nativeVersion !== packageVersion) errors.push("UnifiedPlatformMod.Version differs from package");
  if (projectVersion === null) errors.push("Game Mod project requires exactly one literal Version");
  else if (projectVersion !== packageVersion) errors.push("Game Mod project Version differs from package");
  return { ok: errors.length === 0, nativeVersion, projectVersion, errors };
}

export function readGameModVersions(platformRoot) {
  const appRoot = path.join(platformRoot, "apps/game-mod");
  const json = name => JSON.parse(fs.readFileSync(path.join(appRoot, name), "utf8"));
  return {
    packageVersion: json("package.json").version,
    manifestVersion: json("mod_manifest.json").version,
    nativeSource: fs.readFileSync(path.join(appRoot, "UnifiedPlatformMod.cs"), "utf8"),
    projectSource: fs.readFileSync(path.join(appRoot, "STS2Platform.GameMod.csproj"), "utf8")
  };
}

export function assertGameModVersions(platformRoot) {
  const result = evaluateGameModVersions(readGameModVersions(platformRoot));
  if (!result.ok) throw new Error(`Game Mod product version identity failed:\n${result.errors.join("\n")}`);
  return result;
}

function digestFiles(componentRoot, files) {
  const digest = crypto.createHash("sha256");
  for (const relative of files) {
    digest.update(relative).update("\0");
    digest.update(fs.readFileSync(path.join(componentRoot, relative))).update("\0");
  }
  return digest.digest("hex");
}

function componentIdentity(componentRoot, filter = () => true) {
  const state = componentGitState(componentRoot);
  const files = componentGitFiles(componentRoot).filter(filter);
  // A newly introduced component can be entirely uncommitted during a local
  // candidate build. Its exact digest still binds the bytes; HEAD is the only
  // honest predecessor revision until the first component commit exists.
  const sourceRevision = sourceRevisionForFiles(componentRoot, files)
    ?? state.workspaceRevision;
  return {
    source_revision: sourceRevision,
    source_digest_sha256: digestFiles(componentRoot, files),
    component_tree_revision: state.componentTreeRevision,
    component_worktree_status: state.componentWorktreeStatus,
    file_count: files.length
  };
}

export function compiledSourceDigest(components) {
  const compiledInputs = Object.fromEntries(
    Object.entries(components)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([name, component]) => [name, {
        source_revision: component.source_revision,
        source_digest_sha256: component.source_digest_sha256
      }])
  );
  return crypto.createHash("sha256")
    .update(JSON.stringify(compiledInputs))
    .digest("hex");
}

export function isGameModCompiledSource(relative) {
  return path.extname(relative) === ".cs"
    || relative === "STS2Platform.GameMod.csproj"
    || relative === "mod_manifest.json";
}

export function sourceSetIdentity(platformRoot) {
  const connectorRoot = path.join(platformRoot, "components/connector");
  const connector = playerEnvironmentSourceIdentity(connectorRoot);
  if (!connector) throw new Error("Connector native source identity is unavailable.");
  const workspace = componentGitState(platformRoot);
  const nativeFoundation = componentIdentity(
    path.join(platformRoot, "components/native-foundation"),
    (relative) => path.extname(relative) === ".cs"
  );
  const annotator = componentIdentity(
    path.join(platformRoot, "components/annotator"),
    (relative) => /^(?:src\/STS2HumanAnnotator\.Core|src\/STS2HumanAnnotator\.Mod)\//u.test(relative)
      && [".cs", ".csproj", ".json"].includes(path.extname(relative))
  );
  const liveUi = componentIdentity(
    path.join(platformRoot, "apps/ingame-ui"),
    (relative) => path.extname(relative) === ".cs"
  );
  const gameMod = componentIdentity(
    path.join(platformRoot, "apps/game-mod"),
    isGameModCompiledSource
  );
  const components = {
    native_foundation: nativeFoundation,
    connector: {
      source_revision: connector.revision,
      source_digest_sha256: connector.sourceDigest,
      component_tree_revision: connector.componentTreeRevision,
      component_worktree_status: connector.worktreeStatus,
      file_count: connector.fileCount
    },
    annotator,
    live_ui: liveUi,
    game_mod: gameMod
  };
  const platformDigest = compiledSourceDigest(components);
  return {
    platform: {
      source_revision: gameMod.source_revision,
      source_digest_sha256: platformDigest,
      workspace_revision: workspace.workspaceRevision,
      workspace_worktree_status: workspace.workspaceWorktreeStatus
    },
    components
  };
}

export function sourceSetMatches(left, right) {
  if (!left || !right) return false;
  return left.platform?.source_revision === right.platform?.source_revision
    && left.platform?.source_digest_sha256 === right.platform?.source_digest_sha256
    && Object.keys(right.components).every((name) =>
      left.components?.[name]?.source_revision === right.components[name].source_revision
      && left.components?.[name]?.source_digest_sha256 === right.components[name].source_digest_sha256);
}
