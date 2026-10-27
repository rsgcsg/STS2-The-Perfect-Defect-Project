#!/usr/bin/env node
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync
} from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import {
  evaluateBuildProvenance,
  playerEnvironmentSourceIdentity as readPlayerEnvironmentSourceIdentity,
  readInstalledProvenance,
  readOptionalJson
} from "./connector-provenance.mjs";
import { resolveGameDir, resolveModsDir } from "./steam-paths.mjs";
import { componentGitState } from "../../../tools/component-git.mjs";
import { resolveProjectPython } from "../../../tools/project-python.mjs";

export { evaluateBuildProvenance } from "./connector-provenance.mjs";
export { resolveGameDir, resolveModsDir } from "./steam-paths.mjs";

const WORKSPACE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DEFAULT_ENDPOINT = "http://127.0.0.1:15526";
const DEFAULT_HOST_WAIT_MS = 60_000;
const DEFAULT_HOST_POLL_MS = 500;

export function clientDependenciesAvailable() {
  const sdkPackage = path.join(WORKSPACE, "sdk/typescript/package.json");
  try {
    createRequire(sdkPackage).resolve("typescript");
    return true;
  } catch {
    return false;
  }
}

export function sha256File(file) {
  if (!existsSync(file)) return null;
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

export function evaluateLoadedArtifact({
  csharpProtocol,
  clientProtocol,
  builtSha,
  installedSha,
  builtMvid = null,
  installedMvid = null,
  builtSourceRevision = null,
  capabilities
}) {
  const errors = [];
  const loadedHost = capabilities?.host ?? null;
  const loadedSha = loadedHost?.implementation?.artifact_sha256 ?? null;
  const loadedProtocol = capabilities?.protocol_version ?? null;
  const loadedSourceRevision = loadedHost?.implementation?.source_revision ?? null;
  if (csharpProtocol !== clientProtocol) errors.push("source_protocol_mismatch");
  if (!builtSha) errors.push("release_artifact_missing");
  if (!installedSha) errors.push("installed_artifact_missing");
  if (!capabilities) errors.push("host_not_loaded_or_unreachable");
  if (builtSha && installedSha && builtSha !== installedSha) errors.push("built_installed_sha_mismatch");
  if (builtMvid && installedMvid && builtMvid !== installedMvid) errors.push("built_installed_mvid_mismatch");
  if (installedSha && loadedSha && installedSha !== loadedSha) errors.push("installed_loaded_sha_mismatch");
  const loadedMvid = loadedHost?.implementation?.module_version_id ?? null;
  if (installedMvid && loadedMvid && installedMvid !== loadedMvid) {
    errors.push("installed_loaded_mvid_mismatch");
  }
  if (loadedProtocol && loadedProtocol !== csharpProtocol) errors.push("source_loaded_protocol_mismatch");
  if (builtSourceRevision && loadedSourceRevision
      && builtSourceRevision !== loadedSourceRevision) {
    errors.push("built_loaded_source_revision_mismatch");
  }
  return {
    ok: errors.length === 0,
    artifact_identity_ok: errors.length === 0,
    errors,
    source_protocol: csharpProtocol,
    client_protocol: clientProtocol,
    built_sha256: builtSha,
    installed_sha256: installedSha,
    built_mvid: builtMvid,
    installed_mvid: installedMvid,
    loaded_sha256: loadedSha,
    loaded_protocol: loadedProtocol,
    loaded_source_revision: loadedSourceRevision,
    loaded_mvid: loadedMvid,
    runtime_instance_id: loadedHost?.runtime_instance_id ?? null,
    game: capabilities?.game
      ? {
          version: capabilities.game.version ?? null,
          commit: capabilities.game.commit ?? null,
          branch: capabilities.game.branch ?? null,
          main_assembly_hash: capabilities.game.main_assembly_hash ?? null,
          release_declared_main_assembly_hash:
            capabilities.game.release_declared_main_assembly_hash ?? null
        }
      : null
  };
}

export function evaluateEnvironmentReadiness(
  capabilities,
  expectedProtocol = capabilities?.protocol_version ?? null
) {
  const compatibility = capabilities?.game?.compatibility;
  const modset = capabilities?.game?.modset;
  const protocolSupported = typeof expectedProtocol === "string"
    && expectedProtocol.length > 0
    && capabilities?.protocol_version === expectedProtocol;
  const observationReady = protocolSupported
    && compatibility?.observation_allowed === true;
  const mutationReady = protocolSupported
    && capabilities?.execution_available === true;
  const blockers = [];
  if (!capabilities) blockers.push("host_unreachable");
  if (capabilities && !protocolSupported) blockers.push("unsupported_player_environment_protocol");
  if (!observationReady) blockers.push("player_snapshot_disabled");
  if (!mutationReady) blockers.push("player_input_delivery_disabled");
  return {
    environment_ready: Boolean(capabilities) && observationReady,
    observation_ready: observationReady,
    read_ready: observationReady,
    mutation_ready: mutationReady,
    modset_status: modset?.status ?? null,
    compatibility_status: compatibility?.status ?? null,
    blockers
  };
}

export function inspectModInstallation(modsDir) {
  const canonicalManifest = path.join(modsDir, "STS2_MCP.json");
  const manifests = [];
  if (existsSync(modsDir)) {
    for (const file of walkFiles(modsDir)) {
      if (path.extname(file).toLowerCase() !== ".json") continue;
      try {
        const parsed = JSON.parse(readFileSync(file, "utf8"));
        if (parsed?.id !== "STS2_MCP") continue;
        manifests.push({
          path: file,
          relative_path: path.relative(modsDir, file),
          canonical: path.resolve(file) === path.resolve(canonicalManifest),
          version: typeof parsed.version === "string" ? parsed.version : null
        });
      } catch {
        // Non-manifest JSON is not an installation candidate.
      }
    }
  }
  const duplicateManifests = manifests.filter((manifest) => !manifest.canonical);
  return {
    status: duplicateManifests.length === 0
      ? "single_host_manifest"
      : "duplicate_host_manifests_detected",
    canonical_manifest: existsSync(canonicalManifest) ? canonicalManifest : null,
    manifests,
    duplicate_manifests: duplicateManifests,
    duplicate_installation_blocker: duplicateManifests.length > 0
  };
}

function sourceProtocol(file, pattern) {
  const match = readFileSync(file, "utf8").match(pattern);
  if (!match) throw new Error(`Could not read protocol from ${path.relative(WORKSPACE, file)}`);
  return match[1];
}

function paths(options = {}) {
  const gameDir = path.resolve(options.gameDir ?? resolveGameDir());
  const modsDir = resolveModsDir(gameDir);
  const localRoot = path.join(WORKSPACE, ".local");
  const installationKey = createHash("sha256").update(gameDir).digest("hex").slice(0, 16);
  return {
    gameDir,
    modsDir,
    builtDll: path.join(WORKSPACE, "host/out/STS2_MCP/STS2_MCP.dll"),
    sourceManifest: path.join(WORKSPACE, "host/mod_manifest.json"),
    installedDll: path.join(modsDir, "STS2_MCP.dll"),
    installedManifest: path.join(modsDir, "STS2_MCP.json"),
    installedIdentitySidecar: path.join(modsDir, "STS2_MCP.identity"),
    runtimeConfig: path.join(modsDir, "STS2_MCP.conf"),
    localRoot,
    buildIdentity: path.join(WORKSPACE, "host/out/STS2_MCP/build-identity.json"),
    installedIdentity: path.join(localRoot, "installations", `${installationKey}.json`)
  };
}

export function sourceProtocols() {
  return {
    csharp: sourceProtocol(
      path.join(WORKSPACE, "host/PlayerEnvironment/Protocol/PlayerEnvironmentContracts.cs"),
      /ProtocolVersion\s*=\s*"([^"]+)"/u
    ),
    client: sourceProtocol(
      path.join(WORKSPACE, "sdk/typescript/src/protocol.ts"),
      /SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL\s*=\s*"([^"]+)"/u
    )
  };
}

export function processListHasGame(processList) {
  return processList.split("\n").some((line) => {
    const command = line.replace(/^\s*\d+\s+/u, "").trim();
    return command === "Slay the Spire 2"
      || command === "SlayTheSpire2"
      || command.endsWith("/Contents/MacOS/Slay the Spire 2")
      || command.endsWith("/SlayTheSpire2");
  });
}

export function windowsTaskListHasGame(taskList) {
  return taskList.split("\n").some((line) =>
    /^"?SlayTheSpire2\.exe"?(?:,|\s|$)/iu.test(line.trim())
  );
}

function gameProcessRunning() {
  if (process.platform === "win32") {
    const result = spawnSync(
      "tasklist",
      ["/FI", "IMAGENAME eq SlayTheSpire2.exe", "/FO", "CSV", "/NH"],
      { encoding: "utf8" }
    );
    if (result.error) throw result.error;
    if (result.status !== 0) {
      throw new Error(`Could not determine STS2 process state (tasklist exited ${result.status}).`);
    }
    return windowsTaskListHasGame(result.stdout);
  }
  const result = spawnSync("ps", ["-Ao", "pid=,comm="], { encoding: "utf8" });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`Could not determine STS2 process state (ps exited ${result.status}).`);
  }
  return processListHasGame(result.stdout);
}

function run(command, args, options = {}) {
  const result = spawnPortable(command, args, {
    cwd: options.cwd ?? WORKSPACE,
    env: options.env ?? process.env,
    encoding: "utf8",
    stdio: options.capture ? "pipe" : "inherit"
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture ? `\n${result.stderr || result.stdout}` : "";
    throw new Error(`${command} exited with ${result.status}${detail}`);
  }
  return result.stdout ?? "";
}

export function resolveExecutable(command, platform = process.platform) {
  return platform === "win32" && (command === "npm" || command === "npx")
    ? `${command}.cmd`
    : command;
}

function spawnPortable(command, args, options) {
  const npmExecPath = options.env?.npm_execpath ?? process.env.npm_execpath;
  if (process.platform === "win32"
      && command === "npm"
      && typeof npmExecPath === "string"
      && npmExecPath.length > 0) {
    return spawnSync(process.execPath, [npmExecPath, ...args], options);
  }
  const executable = resolveExecutable(command);
  return spawnSync(executable, args, {
    ...options,
    shell: process.platform === "win32" && executable.endsWith(".cmd")
  });
}

export function playerEnvironmentSourceIdentity() {
  return readPlayerEnvironmentSourceIdentity(WORKSPACE);
}

function writeBuildIdentity(resolved) {
  const currentSource = playerEnvironmentSourceIdentity();
  const protocols = sourceProtocols();
  const identity = artifactIdentity(resolved.builtDll);
  if (!currentSource || !identity) throw new Error("Could not establish Player Environment build provenance.");
  const metadata = {
    schema_version: 1,
    built_at: new Date().toISOString(),
    source_revision: currentSource.revision,
    workspace_revision: currentSource.workspaceRevision,
    component_tree_revision: currentSource.componentTreeRevision,
    player_environment_source_digest: currentSource.sourceDigest,
    source_worktree_status: currentSource.worktreeStatus,
    workspace_worktree_status: currentSource.workspaceWorktreeStatus,
    source_file_count: currentSource.fileCount,
    source_protocol: protocols.csharp,
    artifact_sha256: identity.sha256,
    artifact_mvid: identity.module_version_id
  };
  writeFileSync(resolved.buildIdentity, `${JSON.stringify(metadata, null, 2)}\n`);
  return metadata;
}

function artifactIdentity(file) {
  if (!existsSync(file)) return null;
  const output = run("dotnet", [
    "run",
    "--project", "host/tools/STS2.ArtifactIdentity/STS2.ArtifactIdentity.csproj",
    "-c", "Release",
    "--", file
  ], { capture: true });
  return JSON.parse(output);
}

async function readJson(endpoint, route, required = false) {
  const result = await readJsonResult(endpoint, route);
  if (!result.ok && required) throw new Error(`${route} unavailable: ${result.error}`);
  return result.ok ? result.value : null;
}

async function readJsonResult(endpoint, route) {
  try {
    const response = await fetch(`${endpoint.replace(/\/$/u, "")}${route}`, {
      signal: AbortSignal.timeout(2500)
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return { ok: true, value: await response.json(), error: null };
  } catch (error) {
    return {
      ok: false,
      value: null,
      error: error instanceof Error ? error.message : String(error)
    };
  }
}

export async function waitForPlayerEnvironmentHost({
  endpoint = DEFAULT_ENDPOINT,
  timeoutMs = DEFAULT_HOST_WAIT_MS,
  pollMs = DEFAULT_HOST_POLL_MS
} = {}) {
  const startedAt = Date.now();
  let attempts = 0;
  let lastError = "not_attempted";
  while (Date.now() - startedAt <= timeoutMs) {
    attempts += 1;
    const result = await readJsonResult(endpoint, "/api/player-environment/capabilities");
    if (result.ok) {
      return {
        ready: true,
        attempts,
        waited_ms: Date.now() - startedAt,
        capabilities: result.value
      };
    }
    lastError = result.error;
    if (Date.now() - startedAt >= timeoutMs) break;
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
  return {
    ready: false,
    attempts,
    waited_ms: Date.now() - startedAt,
    capabilities: null,
    error: lastError
  };
}

function summarizeHostWait(result) {
  const host = result.capabilities?.host ?? null;
  return {
    ready: result.ready,
    attempts: result.attempts,
    waited_ms: result.waited_ms,
    protocol_version: result.capabilities?.protocol_version ?? null,
    loaded_sha256: host?.implementation?.artifact_sha256
      ?? host?.assembly_file_sha256
      ?? null,
    loaded_mvid: host?.implementation?.module_version_id
      ?? host?.module_version_id
      ?? null,
    runtime_instance_id: host?.runtime_instance_id ?? null,
    game: result.capabilities?.game
      ? {
          version: result.capabilities.game.version ?? null,
          commit: result.capabilities.game.commit ?? null,
          main_assembly_hash: result.capabilities.game.main_assembly_hash ?? null
        }
      : null,
    error: result.error ?? null
  };
}

function parseOptions(args) {
  const options = { passthrough: [] };
  for (let index = 0; index < args.length; index += 1) {
    const value = args[index];
    if (value === "--game-dir") options.gameDir = args[++index];
    else if (value === "--endpoint") options.endpoint = args[++index];
    else if (value === "--out") options.out = args[++index];
    else if (value === "--backup") options.backup = args[++index];
    else if (value === "--run") options.run = args[++index];
    else if (value === "--runs") options.runs = args[++index];
    else if (value === "--enabled") options.enabled = args[++index];
    else if (value === "--kind") options.kind = args[++index];
    else if (value === "--snapshot-id") options.snapshotId = args[++index];
    else if (value === "--runtime-instance-id") options.runtimeInstanceId = args[++index];
    else if (value === "--session") options.session = args[++index];
    else if (value === "--wait") options.wait = true;
    else if (value === "--allow-dirty") options.allowDirty = true;
    else if (value === "--wait-ms") options.waitMs = parseIntegerOption(value, args[++index], true);
    else if (value === "--poll-ms") options.pollMs = parseIntegerOption(value, args[++index], false);
    else if (value === "--") options.passthrough.push(...args.slice(index + 1));
    else options.passthrough.push(value);
  }
  return options;
}

export function configurePlayerEnvironmentEvidenceProfile(
  configPath,
  enabled
) {
  if (typeof enabled !== "boolean") {
    throw new Error("evidence-profile configure requires --enabled true or --enabled false");
  }
  let config = { port: 15526 };
  if (existsSync(configPath)) {
    const parsed = JSON.parse(readFileSync(configPath, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(`Runtime config is not a JSON object: ${configPath}`);
    }
    config = { ...config, ...parsed };
  }
  delete config.permission_mode;
  delete config.qualification_store;
  delete config.human_equivalence_enabled;
  config.player_environment_native_page_evidence_enabled = enabled;
  mkdirSync(path.dirname(configPath), { recursive: true });
  const temporary = `${configPath}.tmp-${process.pid}`;
  writeFileSync(temporary, `${JSON.stringify(config, null, 2)}\n`, "utf8");
  renameSync(temporary, configPath);
  return {
    status: "configured",
    profile: "native_pages.v1",
    enabled,
    config_path: configPath,
    default_consumer_flow: false,
    creates_action_authority: false,
    enters_action_ledger: false,
    requires_cold_load: true
  };
}

function parseBooleanOption(value, name) {
  if (value === "true") return true;
  if (value === "false") return false;
  throw new Error(`${name} requires true or false`);
}

async function connectorProtocolRequest(endpoint, route, init = {}) {
  const response = await fetch(`${endpoint.replace(/\/$/u, "")}${route}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init.headers ?? {})
    },
    signal: AbortSignal.timeout(10_000)
  });
  const text = await response.text();
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`${route} returned non-JSON HTTP ${response.status}`);
  }
  return { ok: response.ok, http_status: response.status, value };
}

async function evidenceProfile(options) {
  const action = options.passthrough[0];
  const endpoint = options.endpoint ?? DEFAULT_ENDPOINT;
  if (action === "configure") {
    const resolved = paths(options);
    return configurePlayerEnvironmentEvidenceProfile(
      resolved.runtimeConfig,
      parseBooleanOption(options.enabled, "--enabled")
    );
  }
  if (action === "status") {
    const capabilities = await readJson(endpoint, "/api/player-environment/capabilities", true);
    return {
      protocol_version: capabilities.protocol_version,
      loaded_runtime_instance_id: capabilities.host.runtime_instance_id,
      evidence_profiles: capabilities.evidence_profiles
    };
  }
  if (action === "open") {
    if (!options.kind) throw new Error("evidence-profile open requires --kind");
    const [capabilities, snapshot] = await Promise.all([
      readJson(endpoint, "/api/player-environment/capabilities", true),
      readJson(endpoint, "/api/player-environment/snapshot", true)
    ]);
    const result = await connectorProtocolRequest(
      endpoint,
      "/api/player-environment/evidence/native-pages/sessions",
      {
        method: "POST",
        body: JSON.stringify({
          profile: "native_pages.v1",
          kind: options.kind,
          expected_snapshot_id: options.snapshotId ?? snapshot.snapshot_id,
          expected_runtime_instance_id:
            options.runtimeInstanceId ?? capabilities.host.runtime_instance_id
        })
      }
    );
    return { action, ...result };
  }
  if (action === "read") {
    if (!options.session || !options.runtimeInstanceId) {
      throw new Error("evidence-profile read requires --session and --runtime-instance-id");
    }
    const route = "/api/player-environment/evidence/native-pages/sessions/"
      + `${encodeURIComponent(options.session)}?expected_runtime_instance_id=`
      + encodeURIComponent(options.runtimeInstanceId);
    return { action, ...await connectorProtocolRequest(endpoint, route) };
  }
  if (action === "return" || action === "recover") {
    if (!options.session || !options.runtimeInstanceId) {
      throw new Error(`evidence-profile ${action} requires --session and --runtime-instance-id`);
    }
    const route = "/api/player-environment/evidence/native-pages/sessions/"
      + `${encodeURIComponent(options.session)}/return`;
    const result = await connectorProtocolRequest(endpoint, route, {
      method: "POST",
      body: JSON.stringify({
        profile: "native_pages.v1",
        expected_runtime_instance_id: options.runtimeInstanceId
      })
    });
    return { action, ...result };
  }
  throw new Error(
    "evidence-profile requires configure, status, open, read, return or recover"
  );
}

async function inspect(options, requireLoaded = false) {
  const resolved = paths(options);
  const protocols = sourceProtocols();
  const currentSource = playerEnvironmentSourceIdentity();
  const endpoint = options.endpoint ?? DEFAULT_ENDPOINT;
  const waited = options.wait
    ? await waitForPlayerEnvironmentHost({ endpoint, timeoutMs: options.waitMs, pollMs: options.pollMs })
    : null;
  if (options.wait && !waited.ready && requireLoaded) {
    throw new Error(`Player Environment Host did not become ready within ${waited.waited_ms}ms: ${waited.error}`);
  }
  const capabilities = waited?.capabilities
    ?? await readJson(endpoint, "/api/player-environment/capabilities", requireLoaded);
  const readinessCapabilities = capabilities;
  const builtIdentity = artifactIdentity(resolved.builtDll);
  const installedIdentity = artifactIdentity(resolved.installedDll);
  const buildMetadata = readOptionalJson(resolved.buildIdentity);
  // The artifact-local sidecar is authoritative for both source deploys and
  // public release installs. The workspace record may be absent or stale.
  const installedProvenance = readInstalledProvenance(
    resolved.installedIdentitySidecar,
    resolved.installedIdentity
  );
  const installedMetadata = installedProvenance.metadata;
  const evaluation = evaluateLoadedArtifact({
    csharpProtocol: protocols.csharp,
    clientProtocol: protocols.client,
    builtSha: sha256File(resolved.builtDll),
    installedSha: sha256File(resolved.installedDll),
    builtMvid: builtIdentity?.module_version_id ?? null,
    installedMvid: installedIdentity?.module_version_id ?? null,
    builtSourceRevision: buildMetadata?.source_revision ?? null,
    capabilities
  });
  const provenance = evaluateBuildProvenance({
    currentSource,
    sourceProtocol: protocols.csharp,
    builtSha: evaluation.built_sha256,
    builtMvid: evaluation.built_mvid,
    buildMetadata,
    installedSha: evaluation.installed_sha256,
    installedMvid: evaluation.installed_mvid,
    installedMetadata
  });
  const errors = [...new Set([...evaluation.errors, ...provenance.errors])];
  return {
    ...evaluation,
    ok: errors.length === 0,
    artifact_identity_ok: errors.length === 0,
    errors,
    source_identity: currentSource,
    build_provenance: buildMetadata,
    installed_provenance: installedMetadata,
    installed_provenance_location: installedProvenance.location,
    ...evaluateEnvironmentReadiness(readinessCapabilities, protocols.csharp),
    game_dir: resolved.gameDir,
    mods_dir: resolved.modsDir,
    game_process_running: gameProcessRunning(),
    endpoint,
    host_wait: waited ? summarizeHostWait(waited) : null,
    mod_installation: inspectModInstallation(resolved.modsDir),
    compatibility_status: capabilities?.game?.compatibility?.status ?? null,
    note: "Player Environment C is the only production path. It exposes canonical UI facts and reads, binds a complete finite action projection to exact Host-local operands, and returns delivery plus successor."
  };
}

function build(options) {
  const resolved = paths(options);
  const source = playerEnvironmentSourceIdentity();
  if (!source) throw new Error("Could not establish Player Environment source identity before build.");
  rmSync(path.dirname(resolved.builtDll), { recursive: true, force: true });
  run("dotnet", [
    "build",
    "host/STS2Connector.Host.csproj",
    "-c", "Release",
    "-o", "host/out/STS2_MCP",
    "--no-incremental",
    `-p:STS2GameDir=${resolved.gameDir}`,
    `-p:SourceRevision=${source.revision}`,
    `-p:PlayerEnvironmentSourceDigest=${source.sourceDigest}`,
    "-p:UseSharedCompilation=false",
    "-p:Deterministic=true",
    "-p:ContinuousIntegrationBuild=true",
    `-p:PathMap=${WORKSPACE}=/_/sts2-connector`
  ]);
  run("dotnet", [
    "build",
    "host/tools/STS2.ArtifactIdentity/STS2.ArtifactIdentity.csproj",
    "-c", "Release"
  ]);
  run("npm", ["--prefix", "sdk/typescript", "run", "build"]);
  return writeBuildIdentity(resolved);
}

function test(options) {
  const resolved = paths(options);
  run("dotnet", [
    "test",
    "host/STS2Connector.sln",
    `-p:STS2GameDir=${resolved.gameDir}`,
    "-p:UseSharedCompilation=false"
  ]);
  run(resolveProjectPython().executable, [
    "-m", "py_compile", "transports/mcp/server.py"
  ]);
  artifactIdentity(resolved.builtDll);
  run("npm", ["--prefix", "sdk/typescript", "run", "check"]);
  for (const script of [
    "check:cli",
    "check:docs"
  ]) run("npm", ["run", script]);
}

function install(options) {
  const resolved = paths(options);
  if (gameProcessRunning()) {
    throw new Error("Slay the Spire 2 is running. Close it before replacing the Player Environment Host artifact.");
  }
  if (!existsSync(resolved.builtDll)) throw new Error("Release DLL is missing; run connector:build first.");
  const currentSource = playerEnvironmentSourceIdentity();
  if (!currentSource) {
    throw new Error("Could not establish Player Environment source identity before install.");
  }
  if (!options.allowDirty
      && currentSource.worktreeStatus !== "clean") {
    throw new Error(
      "Release install requires a clean Host source tree. Commit the intended source or use npm run dev-deploy for an explicitly non-release install."
    );
  }
  const protocols = sourceProtocols();
  const builtIdentity = artifactIdentity(resolved.builtDll);
  const buildMetadata = readOptionalJson(resolved.buildIdentity);
  const buildProvenance = evaluateBuildProvenance({
    currentSource,
    sourceProtocol: protocols.csharp,
    builtSha: builtIdentity?.sha256 ?? null,
    builtMvid: builtIdentity?.module_version_id ?? null,
    buildMetadata,
    installedSha: null,
    installedMvid: null,
    installedMetadata: null
  });
  if (!buildProvenance.ok) {
    throw new Error(
      `Release build does not match current Player Environment source: ${buildProvenance.errors.join(", ")}. Run connector build before install.`
    );
  }
  if (!options.allowDirty && buildMetadata?.source_worktree_status !== "clean") {
    throw new Error(
      "Release install refused a build recorded from dirty Host source. Rebuild from clean source or use npm run dev-deploy."
    );
  }
  mkdirSync(resolved.modsDir, { recursive: true });
  const modInstallation = inspectModInstallation(resolved.modsDir);
  if (modInstallation.duplicate_installation_blocker) {
    throw new Error(
      `Installation refused before changing the Host artifact because duplicate STS2_MCP manifests exist under the scanned mods tree: ${modInstallation.duplicate_manifests.map((item) => item.relative_path).join(", ")}. Run connector repair-installation with the game closed.`
    );
  }
  const builtSha = sha256File(resolved.builtDll);
  const installedSha = sha256File(resolved.installedDll);
  if (builtSha === installedSha && existsSync(resolved.installedManifest)) {
    const installedProvenance = {
      ...buildMetadata,
      installed_at: new Date().toISOString()
    };
    mkdirSync(path.dirname(resolved.installedIdentity), { recursive: true });
    writeFileSync(resolved.installedIdentity, `${JSON.stringify(installedProvenance, null, 2)}\n`);
    writeFileSync(resolved.installedIdentitySidecar, `${JSON.stringify(installedProvenance, null, 2)}\n`);
    return {
      status: "already_installed",
      sha256: builtSha,
      mvid: builtIdentity.module_version_id,
      installed_dll: resolved.installedDll,
      player_environment_source_digest: buildMetadata.player_environment_source_digest,
      provenance_tier: options.allowDirty ? "dirty_development" : "clean_source"
    };
  }

  const backupDir = path.join(
    resolved.localRoot,
    "deployments",
    new Date().toISOString().replace(/[:.]/gu, "-")
  );
  mkdirSync(backupDir, { recursive: true });
  if (existsSync(resolved.installedDll)) copyFileSync(resolved.installedDll, path.join(backupDir, "STS2_MCP.dll"));
  if (existsSync(resolved.installedManifest)) copyFileSync(resolved.installedManifest, path.join(backupDir, "STS2_MCP.json"));
  if (existsSync(resolved.installedIdentity)) {
    copyFileSync(resolved.installedIdentity, path.join(backupDir, "installed-identity.json"));
  }
  if (existsSync(resolved.installedIdentitySidecar)) {
    copyFileSync(resolved.installedIdentitySidecar, path.join(backupDir, "STS2_MCP.identity"));
  }
  const previousIdentity = artifactIdentity(resolved.installedDll);
  writeFileSync(path.join(backupDir, "deployment.json"), `${JSON.stringify({
    schema_version: 1,
    created_at: new Date().toISOString(),
    previous_installed_sha256: installedSha,
    previous_installed_mvid: previousIdentity?.module_version_id ?? null,
    replacement_sha256: builtSha,
    replacement_mvid: builtIdentity.module_version_id,
    replacement_source_revision: buildMetadata.source_revision,
    replacement_player_environment_source_digest:
      buildMetadata.player_environment_source_digest,
    replacement_protocol: buildMetadata.source_protocol,
    game_dir: resolved.gameDir,
    scope: "player_environment_host_artifact_only_not_game_or_modset"
  }, null, 2)}\n`);

  copyFileSync(resolved.builtDll, resolved.installedDll);
  copyFileSync(resolved.sourceManifest, resolved.installedManifest);
  const copiedSha = sha256File(resolved.installedDll);
  if (copiedSha !== builtSha) throw new Error("Installed Host SHA does not match the Release artifact.");
  const installedProvenance = {
    ...buildMetadata,
    installed_at: new Date().toISOString()
  };
  mkdirSync(path.dirname(resolved.installedIdentity), { recursive: true });
  writeFileSync(resolved.installedIdentity, `${JSON.stringify(installedProvenance, null, 2)}\n`);
  writeFileSync(resolved.installedIdentitySidecar, `${JSON.stringify(installedProvenance, null, 2)}\n`);
  return {
    status: "installed_game_must_be_cold_started",
    sha256: copiedSha,
    mvid: builtIdentity.module_version_id,
    installed_dll: resolved.installedDll,
    player_environment_source_digest: buildMetadata.player_environment_source_digest,
    provenance_tier: options.allowDirty ? "dirty_development" : "clean_source",
    rollback_backup: backupDir
  };
}

function deploy(options) {
  if (gameProcessRunning()) {
    throw new Error("Slay the Spire 2 is running. Close it before starting the verified deploy workflow.");
  }
  const source = playerEnvironmentSourceIdentity();
  if (!source) throw new Error("Could not establish Player Environment source identity before deploy.");
  if (!options.allowDirty
      && source.worktreeStatus !== "clean") {
    throw new Error(
      "Release deploy requires a clean Host source tree. Commit the intended source or run npm run dev-deploy for an explicitly non-release install."
    );
  }
  test(options);
  const buildMetadata = build(options);
  const installation = install(options);
  return {
    status: options.allowDirty
      ? "dirty_development_build_installed_game_must_be_cold_started"
      : "verified_clean_source_build_installed_game_must_be_cold_started",
    source_revision: buildMetadata.source_revision,
    player_environment_source_digest: buildMetadata.player_environment_source_digest,
    protocol: buildMetadata.source_protocol,
    artifact_sha256: buildMetadata.artifact_sha256,
    artifact_mvid: buildMetadata.artifact_mvid,
    installation,
    provenance_tier: options.allowDirty ? "dirty_development" : "clean_source",
    loaded: "non_claim"
  };
}

function repairInstallation(options) {
  const resolved = paths(options);
  if (gameProcessRunning()) {
    throw new Error("Slay the Spire 2 is running. Close it before relocating duplicate mod manifests.");
  }
  const before = inspectModInstallation(resolved.modsDir);
  if (!before.duplicate_installation_blocker) {
    return { status: "installation_clean", moved: [], inspection: before };
  }

  const unsafe = before.duplicate_manifests.filter((manifest) =>
    !path.relative(resolved.modsDir, manifest.path).split(path.sep).includes("backups"));
  if (unsafe.length > 0) {
    return {
      status: "manual_review_required",
      moved: [],
      blocked_manifests: unsafe,
      detail: "Only duplicate manifests inside an explicit backups directory can be relocated automatically."
    };
  }

  const quarantineRoot = path.join(
    resolved.localRoot,
    "mod-installation-quarantine",
    new Date().toISOString().replace(/[:.]/gu, "-")
  );
  const sourceDirectories = [...new Set(before.duplicate_manifests.map((manifest) => path.dirname(manifest.path)))];
  const moved = [];
  for (const source of sourceDirectories) {
    const relative = path.relative(resolved.modsDir, source);
    const destination = path.join(quarantineRoot, relative);
    mkdirSync(path.dirname(destination), { recursive: true });
    renameSync(source, destination);
    moved.push({ source, destination });
  }
  return {
    status: "duplicate_backup_manifests_relocated",
    moved,
    inspection: inspectModInstallation(resolved.modsDir),
    rollback: "Move each quarantined directory back to its recorded source path while the game is closed."
  };
}

function restoreKnownEnvironment(options) {
  const resolved = paths(options);
  if (gameProcessRunning()) throw new Error("Close Slay the Spire 2 before restoring a Host artifact.");
  if (!options.backup) throw new Error("restore-known-environment requires --backup DIR.");
  const backup = path.resolve(options.backup);
  const dll = path.join(backup, "STS2_MCP.dll");
  if (!existsSync(dll)) throw new Error("The backup does not contain STS2_MCP.dll.");
  mkdirSync(resolved.modsDir, { recursive: true });
  copyFileSync(dll, resolved.installedDll);
  const manifest = path.join(backup, "STS2_MCP.json");
  if (existsSync(manifest)) copyFileSync(manifest, resolved.installedManifest);
  const installedIdentity = path.join(backup, "installed-identity.json");
  if (existsSync(installedIdentity)) {
    mkdirSync(path.dirname(resolved.installedIdentity), { recursive: true });
    copyFileSync(installedIdentity, resolved.installedIdentity);
    const sidecar = path.join(backup, "STS2_MCP.identity");
    copyFileSync(
      existsSync(sidecar) ? sidecar : installedIdentity,
      resolved.installedIdentitySidecar
    );
  } else {
    rmSync(resolved.installedIdentity, { force: true });
    rmSync(resolved.installedIdentitySidecar, { force: true });
  }
  return {
    status: "host_artifact_restored_game_must_be_cold_started",
    sha256: sha256File(resolved.installedDll),
    scope: "player_environment_host_artifact_only",
    non_claims: ["Steam game build not restored", "Modset not restored", "artifact not loaded"]
  };
}

async function collectEvidence(options) {
  const endpoint = options.endpoint ?? DEFAULT_ENDPOINT;
  const waited = await waitForPlayerEnvironmentHost({
    endpoint,
    timeoutMs: options.waitMs,
    pollMs: options.pollMs
  });
  if (!waited.ready) {
    throw new Error(`Player Environment Host did not become ready within ${waited.waited_ms}ms: ${waited.error}`);
  }
  const capabilities = waited.capabilities;
  const state = await readJson(endpoint, "/api/player-environment/snapshot", true);
  const controller = await readJsonResult(endpoint, "/api/player-environment/controller");
  const partialFailures = [
    ...(controller.ok ? [] : [{ route: "/api/player-environment/controller", error: controller.error }])
  ];
  const resolved = paths(options);
  const output = path.resolve(options.out ?? path.join(
    resolved.localRoot,
    "evidence",
    `connector-readonly-${new Date().toISOString().replace(/[:.]/gu, "-")}.json`
  ));
  mkdirSync(path.dirname(output), { recursive: true });
  writeFileSync(output, `${JSON.stringify({
    schema_version: 3,
    captured_at: new Date().toISOString(),
    evidence_kind: "read_only_loaded_connector_snapshot",
    authorization_effect: "none",
    built_sha256: sha256File(resolved.builtDll),
    installed_sha256: sha256File(resolved.installedDll),
    capabilities,
    state,
    optional_diagnostics: {
      controller: controller.value
    },
    partial_failures: partialFailures
  }, null, 2)}\n`);
  return {
    status: "read_only_evidence_collected",
    output,
    protocol_version: capabilities.protocol_version,
    loaded_sha256: capabilities.host?.implementation?.artifact_sha256 ?? null,
    snapshot_id: state.snapshot_id,
    owner_id: state.owner?.owner_id ?? null,
    partial_failures: partialFailures
  };
}

function probeCommand(command, args = ["--version"]) {
  const result = spawnPortable(command, args, {
    cwd: WORKSPACE,
    env: process.env,
    encoding: "utf8",
    stdio: "pipe"
  });
  const output = `${result.stdout ?? ""}\n${result.stderr ?? ""}`.trim();
  return {
    available: !result.error && result.status === 0,
    version: output.split(/\r?\n/u).find(Boolean) ?? null,
    error: result.error?.message ?? (result.status === 0 ? null : `exit_${result.status}`)
  };
}

function gitWorkspaceState() {
  const state = componentGitState(WORKSPACE);
  return {
    workspace_head: state.workspaceRevision,
    source_revision: state.componentSourceRevision,
    component_tree_revision: state.componentTreeRevision,
    component_path: state.componentPath,
    component_worktree: state.componentWorktreeStatus,
    workspace_worktree: state.workspaceWorktreeStatus
  };
}

export function recommendDoctorSteps({
  prerequisites,
  gameDirExists,
  clientDependenciesInstalled,
  status,
  inspectionError = null
}) {
  const steps = [];
  const missing = Object.entries(prerequisites)
    .filter(([, value]) => value.required && !value.available)
    .map(([name]) => name);
  if (missing.length > 0) steps.push(`Install required tools: ${missing.join(", ")}.`);
  if (!gameDirExists) steps.push("Install STS2 or set STS2_GAME_DIR to the exact Steam game directory.");
  if (!clientDependenciesInstalled) steps.push("Run npm run bootstrap from the repository root.");
  if (inspectionError) steps.push(`Resolve Connector inspection failure: ${inspectionError}`);

  const deployErrors = new Set([
    "release_artifact_missing",
    "installed_artifact_missing",
    "build_provenance_missing",
    "installed_provenance_missing",
    "source_build_revision_mismatch",
    "source_build_digest_mismatch",
    "source_build_protocol_mismatch",
    "build_provenance_sha_mismatch",
    "build_provenance_mvid_mismatch",
    "build_installed_sha_mismatch",
    "build_installed_mvid_mismatch",
    "build_installed_provenance_mismatch",
    "build_installed_revision_mismatch"
  ]);
  if (status?.errors?.some((error) => deployErrors.has(error))) {
    steps.push("Fully close STS2, then run npm run deploy from the repository root.");
  }
  if (status?.mod_installation?.duplicate_installation_blocker) {
    steps.push("Fully close STS2, then diagnose and repair duplicate STS2_MCP manifests.");
  }
  if (status?.errors?.some((error) => [
    "installed_loaded_sha_mismatch",
    "installed_loaded_mvid_mismatch",
    "source_loaded_protocol_mismatch",
    "built_loaded_source_revision_mismatch"
  ].includes(error))) {
    steps.push("After a verified deploy, cold-restart STS2 so the installed Player Environment Host is actually loaded.");
  } else if (status?.errors?.includes("host_not_loaded_or_unreachable")) {
    steps.push("Start STS2, wait for a stable menu, then run npm run verify:loaded.");
  }
  if (status?.ok === true
      && status.environment_ready !== true) {
    steps.push(`Resolve loaded environment blockers: ${(status.blockers ?? ["observation_not_ready"]).join(", ")}.`);
  }
  if (status?.ok === true
      && status.environment_ready === true
      && status.mutation_ready !== true) {
    steps.push("The loaded Host cannot deliver input; keep actions Fail Closed and inspect exact identity.");
  }
  return [...new Set(steps)];
}

async function doctor(options) {
  let python;
  try { python = probeCommand(resolveProjectPython().executable, ["--version"]); }
  catch (error) { python = { available: false, version: null, error: error.message }; }
  const prerequisites = {
    node: {
      required: true,
      available: Number(process.versions.node.split(".")[0]) >= 20,
      version: process.version,
      error: null
    },
    npm: { required: true, ...probeCommand("npm") },
    dotnet: { required: true, ...probeCommand("dotnet", ["--version"]) },
    git: { required: true, ...probeCommand("git", ["--version"]) },
    python: { required: false, ...python },
    uv: { required: false, ...probeCommand("uv", ["--version"]) }
  };
  let gameDir = null;
  let gameDirExists = false;
  let gameDirError = null;
  try {
    gameDir = path.resolve(options.gameDir ?? resolveGameDir());
    gameDirExists = existsSync(gameDir);
  } catch (error) {
    gameDirError = error instanceof Error ? error.message : String(error);
  }
  const clientDependenciesInstalled = clientDependenciesAvailable();
  let status = null;
  let inspectionError = gameDirError;
  if (gameDirExists && prerequisites.dotnet.available) {
    try {
      status = await inspect({ ...options, gameDir });
    } catch (error) {
      inspectionError = error instanceof Error ? error.message : String(error);
    }
  }
  const nextSteps = recommendDoctorSteps({
    prerequisites,
    gameDirExists,
    clientDependenciesInstalled,
    status,
    inspectionError
  });
  return {
    status: nextSteps.length === 0 ? "ready" : "action_required",
    repository: gitWorkspaceState(),
    prerequisites,
    game_dir: gameDir,
    game_dir_exists: gameDirExists,
    client_dependencies_installed: clientDependenciesInstalled,
    connector: status,
    inspection_error: inspectionError,
    next_steps: nextSteps,
    non_claims: [
      "doctor is read-only",
      "installed identity is not loaded identity",
      "loaded identity is not Live journey evidence"
    ]
  };
}

function* walkFiles(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isFile()) yield fullPath;
    else if (entry.isDirectory()) yield* walkFiles(fullPath);
  }
}

function parseIntegerOption(name, value, allowZero) {
  const parsed = Number(value);
  const valid = Number.isInteger(parsed) && (allowZero ? parsed >= 0 : parsed > 0);
  if (!valid) throw new Error(`${name} requires ${allowZero ? "a non-negative" : "a positive"} integer.`);
  return parsed;
}

function usage() {
  return `Usage: npm run connector -- <command> [options]\n\n`
    + `Commands:\n`
    + `  doctor                            Diagnose prerequisites, checkout and deployment drift\n`
    + `  deploy                            Test, build, back up and install with the game closed\n`
    + `  inspect | show-status             Read source, disk and optional loaded identity\n`
    + `  test                              Run Host, client SDK and repository checks\n`
    + `  build                             Build Release Host and client SDK\n`
    + `  install                           Backup and install the built Host with game closed\n`
    + `  diagnose-installation              Find duplicate STS2_MCP manifests in the Mod scan tree\n`
    + `  repair-installation                Relocate known backup manifests with game closed\n`
    + `  wait-for-host                      Bounded read-only capabilities readiness wait\n`
    + `  verify-loaded-artifact [--wait]   Require source/built/installed/loaded identity agreement\n`
    + `  collect-evidence [--out FILE]     Capture read-only capabilities/state/controller/clients\n`
    + `  evidence-profile <operation>       Configure or exercise optional native-page evidence\n`
    + `  restore-known-environment --backup DIR  Restore only a backed-up Host artifact\n\n`
    + `Common options: --game-dir DIR --endpoint URL --wait-ms N --poll-ms N\n`
    + `Development only: --allow-dirty (use npm run dev-deploy; never release this artifact)`;
}

export async function main(argv = process.argv.slice(2)) {
  const command = argv[0];
  const options = parseOptions(argv.slice(1));
  if (!command || command === "help" || command === "--help") {
    console.log(usage());
    return;
  }
  if (command === "doctor") {
    console.log(JSON.stringify(await doctor(options), null, 2));
    return;
  }
  if (command === "deploy") {
    console.log(JSON.stringify(deploy(options), null, 2));
    return;
  }
  if (command === "inspect" || command === "show-status") {
    console.log(JSON.stringify(await inspect(options), null, 2));
    return;
  }
  if (command === "verify-loaded-artifact") {
    const status = await inspect(options, true);
    console.log(JSON.stringify(status, null, 2));
    if (!status.ok) process.exitCode = 1;
    return;
  }
  if (command === "test") return test(options);
  if (command === "build") {
    console.log(JSON.stringify(build(options), null, 2));
    return;
  }
  if (command === "install") {
    console.log(JSON.stringify(install(options), null, 2));
    return;
  }
  if (command === "diagnose-installation") {
    console.log(JSON.stringify(inspectModInstallation(paths(options).modsDir), null, 2));
    return;
  }
  if (command === "repair-installation") {
    const result = repairInstallation(options);
    console.log(JSON.stringify(result, null, 2));
    if (result.status === "manual_review_required") process.exitCode = 1;
    return;
  }
  if (command === "wait-for-host") {
    const result = await waitForPlayerEnvironmentHost({
      endpoint: options.endpoint ?? DEFAULT_ENDPOINT,
      timeoutMs: options.waitMs,
      pollMs: options.pollMs
    });
    console.log(JSON.stringify(summarizeHostWait(result), null, 2));
    if (!result.ready) process.exitCode = 1;
    return;
  }
  if (command === "restore-known-environment") {
    console.log(JSON.stringify(restoreKnownEnvironment(options), null, 2));
    return;
  }
  if (command === "collect-evidence") {
    console.log(JSON.stringify(await collectEvidence(options), null, 2));
    return;
  }
  if (command === "evidence-profile") {
    const result = await evidenceProfile(options);
    console.log(JSON.stringify(result, null, 2));
    if (result?.ok === false) process.exitCode = 1;
    return;
  }
  if (command === "audit-run-identity") {
    const args = [];
    if (options.run) args.push("--run", options.run);
    if (options.runs) args.push("--runs", options.runs);
    run("node", [path.join(WORKSPACE, "tools/connector-run-identity-audit.mjs"), ...args]);
    return;
  }
  throw new Error(`Unknown command ${command}.\n${usage()}`);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
