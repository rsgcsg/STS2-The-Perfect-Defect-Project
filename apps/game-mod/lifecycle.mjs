#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  loadHostRuntimeWorkstationApi,
  prepareSoleWindowsModSettings,
  resolveConnectorCanaryEnvironment,
  resolveWindowsSteamSettings,
  resolveWorkstationInstallation
} from "../../components/annotator/tools/workstation-platform.mjs";
import { evaluateLoadedEvidence, extractGameProcessIds } from "./loaded-evidence.mjs";
import { waitForLoadedReadiness } from "./loaded-readiness.mjs";
import { assertGameModVersions, sourceSetIdentity, sourceSetMatches } from "./source-identity.mjs";
import { readAnnotatorConfiguration, effectiveAnnotatorConfiguration } from "./annotator-configuration.mjs";

const appRoot = import.meta.dirname;
const platformRoot = path.resolve(appRoot, "../..");
const annotatorRoot = path.join(platformRoot, "components/annotator");
const outputRoot = path.join(appRoot, "bin/Release/net9.0");
const builtDll = path.join(outputRoot, "STS2_PLATFORM.dll");
const buildProvenance = path.join(outputRoot, "build-provenance.json");
const manifestSource = path.join(appRoot, "mod_manifest.json");
const localRoot = path.join(appRoot, ".local");
const installedProvenance = path.join(localRoot, "installed-provenance.json");
const runtimeStatus = path.join(annotatorRoot, ".local/runtime-status.json");
const identityTool = path.join(
  annotatorRoot,
  "src/STS2HumanAnnotator.Tool/bin/Release/net9.0/sts2-human-annotator.dll"
);
const hostApi = await loadHostRuntimeWorkstationApi(annotatorRoot);
const installation = resolveWorkstationInstallation({ headlessApi: hostApi });
const installedDll = path.join(installation.mods_dir, "STS2_PLATFORM.dll");
const installedManifest = path.join(installation.mods_dir, "STS2_PLATFORM.json");
const installedIdentity = path.join(installation.mods_dir, "STS2_PLATFORM.identity");
const annotatorConfig = path.join(installation.mods_dir, "STS2_HUMAN_ANNOTATOR.conf");
const retiredProductionFiles = [
  "STS2_MCP.dll",
  "STS2_MCP.json",
  "STS2_MCP.identity",
  "STS2_HUMAN_ANNOTATOR.dll",
  "STS2_HUMAN_ANNOTATOR.json",
  "STS2_PLATFORM_LIVE_UI.dll",
  "STS2_PLATFORM_LIVE_UI.json"
];
const managedModFiles = [
  "STS2_PLATFORM.dll",
  "STS2_PLATFORM.json",
  "STS2_PLATFORM.identity",
  ...retiredProductionFiles
];
const managedConfigFiles = ["STS2_MCP.conf", "STS2_HUMAN_ANNOTATOR.conf"];
const windowsSettingsSchema = 8;
const platformModId = "STS2_PLATFORM";
const command = process.argv[2] ?? "doctor";

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/u, ""));
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.tmp-${crypto.randomUUID()}`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`);
  fs.renameSync(temporary, file);
}

function annotatorConfiguration() {
  return readAnnotatorConfiguration(annotatorConfig, {
    recording_root: path.join(annotatorRoot, ".local/recordings"),
    runtime_status_path: runtimeStatus
  });
}

function exactIdentity(file) {
  if (!fs.existsSync(identityTool)) throw new Error("Run npm run game-mod:build first.");
  const result = spawnSync("dotnet", [identityTool, "identity", file], {
    cwd: platformRoot,
    encoding: "utf8"
  });
  if (result.status !== 0) throw new Error(result.stderr || `identity tool exited with ${result.status}`);
  return JSON.parse(result.stdout);
}

function sameIdentity(left, right) {
  return left?.sha256 === right?.sha256
    && left?.module_version_id === right?.module_version_id;
}

function gameProcesses() {
  if (hostApi) return hostApi.listGameProcesses(process.platform, { failClosed: true });
  if (process.platform === "win32") throw new Error("Strict Windows process detection requires Platform Host Runtime.");
  const result = spawnSync("pgrep", ["-f", "SlayTheSpire2"], { encoding: "utf8" });
  if (result.error || ![0, 1].includes(result.status)) throw new Error("Could not enumerate STS2 processes.");
  return result.status === 0 ? result.stdout.trim().split(/\s+/u).filter(Boolean) : [];
}

function gameRunning() {
  return gameProcesses().length > 0;
}

function requireBuild() {
  assertGameModVersions(platformRoot);
  if (!fs.existsSync(buildProvenance) || !fs.existsSync(builtDll)) {
    throw new Error("Run npm run game-mod:build first.");
  }
  const provenance = readJson(buildProvenance);
  if (provenance.schema !== "sts2.platform/game-mod-build-provenance-1") {
    throw new Error("Game Mod build provenance schema is unsupported.");
  }
  const current = sourceSetIdentity(platformRoot);
  if (current.platform.workspace_worktree_status !== "clean") {
    throw new Error("Commit the exact Platform source before production deployment.");
  }
  if (!sourceSetMatches(provenance.source, current)) {
    throw new Error("Game Mod build provenance differs from current exact source.");
  }
  const built = exactIdentity(builtDll);
  if (!sameIdentity(built, provenance.artifact)) {
    throw new Error("Built Game Mod differs from build provenance.");
  }
  return { provenance, built };
}

function archiveTarget(backup, location, file) {
  const existed = fs.existsSync(file);
  const archiveName = `${location}--${path.basename(file)}`;
  if (existed) fs.copyFileSync(file, path.join(backup, archiveName));
  return { location, name: path.basename(file), archive_name: archiveName, existed };
}

function restoreTarget(entry) {
  if (entry.location === "mods") return path.join(installation.mods_dir, entry.name);
  if (entry.location === "local" && entry.name === path.basename(installedProvenance)) {
    return installedProvenance;
  }
  if (entry.location === "settings" && entry.name === "settings.save") {
    return resolveWindowsSteamSettings({ expectedSchema: windowsSettingsSchema }).file;
  }
  throw new Error(`Unsupported Game Mod rollback target: ${entry.location}/${entry.name}`);
}

function validateRollbackManifest(manifest) {
  if (manifest.schema !== "sts2.platform/game-mod-rollback-1" || !Array.isArray(manifest.files)) {
    throw new Error("Game Mod rollback manifest is unsupported.");
  }
  const allowedMods = new Set([...managedModFiles, ...managedConfigFiles]);
  for (const entry of manifest.files) {
    const validLocation = ["mods", "local", "settings"].includes(entry?.location);
    const validName = typeof entry?.name === "string"
      && path.basename(entry.name) === entry.name
      && entry.name !== "."
      && entry.name !== "..";
    const allowedName = entry?.location === "mods"
      ? allowedMods.has(entry.name)
      : entry?.location === "local"
        ? entry?.name === path.basename(installedProvenance)
        : entry?.name === "settings.save";
    const expectedArchive = validLocation && validName
      ? `${entry.location}--${entry.name}`
      : null;
    if (!validLocation
        || !validName
        || !allowedName
        || entry.archive_name !== expectedArchive
        || typeof entry.existed !== "boolean") {
      throw new Error("Game Mod rollback manifest contains an invalid target.");
    }
  }
  return manifest;
}

function restoreEntries(backup, entries) {
  for (const entry of entries) {
    const target = restoreTarget(entry);
    if (entry.existed) fs.copyFileSync(path.join(backup, entry.archive_name), target);
    else fs.rmSync(target, { force: true });
  }
}

function rollbackDirectory(value) {
  const resolved = path.resolve(value ?? "");
  const root = path.resolve(localRoot, "deployments");
  if (!resolved.startsWith(`${root}${path.sep}`)) {
    throw new Error("Game Mod rollback directory is outside the local deployment archive.");
  }
  return resolved;
}

function doctor() {
  assertGameModVersions(platformRoot);
  const report = {
    status: fs.existsSync(installation.executable) ? "ok" : "action_required",
    platform: process.platform,
    architecture: process.arch,
    game_running: gameRunning(),
    installation,
    source: (() => { try { return sourceSetIdentity(platformRoot); } catch { return null; } })(),
    built: fs.existsSync(builtDll) ? exactIdentity(builtDll) : null,
    installed: fs.existsSync(installedDll) ? exactIdentity(installedDll) : null,
    legacy_production_files_present: retiredProductionFiles.filter((name) =>
      fs.existsSync(path.join(installation.mods_dir, name))),
    build_provenance: fs.existsSync(buildProvenance) ? readJson(buildProvenance) : null,
    installed_provenance: fs.existsSync(installedProvenance) ? readJson(installedProvenance) : null,
    non_claims: ["doctor_is_read_only", "installed_is_not_loaded", "loaded_is_not_human_or_policy_evidence"]
  };
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

function deploy() {
  if (gameRunning()) throw new Error("Fully close Slay the Spire 2 before deployment.");
  const exact = requireBuild();
  // Validate before any installed bytes/configuration can change.
  const recordingConfiguration = annotatorConfiguration();
  fs.mkdirSync(installation.mods_dir, { recursive: true });
  const backup = path.join(localRoot, "deployments", new Date().toISOString().replaceAll(":", "-"));
  fs.mkdirSync(backup, { recursive: true });
  const settings = resolveWindowsSteamSettings({ expectedSchema: windowsSettingsSchema });
  const entries = [
    ...managedModFiles.map((name) => archiveTarget(
      backup,
      "mods",
      path.join(installation.mods_dir, name))),
    ...managedConfigFiles.map((name) => archiveTarget(
      backup,
      "mods",
      path.join(installation.mods_dir, name))),
    archiveTarget(backup, "local", installedProvenance),
    ...(settings ? [archiveTarget(backup, "settings", settings.file)] : [])
  ];
  writeJson(path.join(backup, "rollback-manifest.json"), {
    schema: "sts2.platform/game-mod-rollback-1",
    files: entries
  });

  try {
    for (const name of managedModFiles) {
      fs.rmSync(path.join(installation.mods_dir, name), { force: true });
    }
    fs.copyFileSync(builtDll, installedDll);
    fs.copyFileSync(manifestSource, installedManifest);
    writeJson(installedIdentity, {
      schema: "sts2.platform/game-mod-installed-identity-1",
      source_revision: exact.provenance.source.components.connector.source_revision,
      workspace_revision: exact.provenance.source.platform.workspace_revision,
      artifact_sha256: exact.built.sha256,
      artifact_mvid: exact.built.module_version_id,
      installed_at: new Date().toISOString()
    });
    const connectorConfig = path.join(installation.mods_dir, "STS2_MCP.conf");
    writeJson(connectorConfig, {
      port: 15526,
      player_environment_native_page_evidence_enabled: false
    });
    writeJson(annotatorConfig, recordingConfiguration);
    if (settings) {
      writeJson(settings.file, prepareSoleWindowsModSettings({
        settings: settings.value,
        enabledModId: platformModId
      }));
    }
    const installed = {
      schema: "sts2.platform/game-mod-installed-provenance-1",
      installed_at: new Date().toISOString(),
      source: exact.provenance.source,
      game: exact.provenance.game,
      artifact: exactIdentity(installedDll),
      manifest: readJson(installedManifest),
      enabled_mod_ids: [platformModId],
      retired_production_files: retiredProductionFiles,
      rollback: backup
    };
    if (!sameIdentity(installed.artifact, exact.built)) {
      throw new Error("Installed Game Mod identity differs from the exact build.");
    }
    writeJson(installedProvenance, installed);
    process.stdout.write(`${JSON.stringify(installed, null, 2)}\n`);
  } catch (error) {
    restoreEntries(backup, entries);
    throw error;
  }
}

function rollback() {
  if (gameRunning()) throw new Error("Fully close Slay the Spire 2 before rollback.");
  if (!fs.existsSync(installedProvenance)) throw new Error("Game Mod installed provenance is unavailable.");
  const installed = readJson(installedProvenance);
  const rollback = rollbackDirectory(installed.rollback);
  const manifest = validateRollbackManifest(readJson(path.join(rollback, "rollback-manifest.json")));
  restoreEntries(rollback, manifest.files);
  process.stdout.write(`${JSON.stringify({ status: "restored", rollback }, null, 2)}\n`);
}

function launch() {
  if (gameRunning()) throw new Error("Slay the Spire 2 is already running; cold-load requires a closed process.");
  if (!fs.existsSync(installedProvenance)) throw new Error("Deploy the unified Game Mod first.");
  if (!["darwin", "win32"].includes(process.platform)) {
    throw new Error(`Automated cold launch is unsupported on ${process.platform}.`);
  }
  const installed = readJson(installedProvenance);
  const currentInstalled = exactIdentity(installedDll);
  if (!sameIdentity(currentInstalled, installed.artifact)) {
    throw new Error("Installed Game Mod drifted from installed provenance.");
  }
  const connectorCanary = resolveConnectorCanaryEnvironment({
    compatibility: readJson(path.join(
      platformRoot,
      "components/connector/contracts/host-compatibility.json"
    )),
    connectorBuild: installed.source.components.connector,
    gameRelease: installed.game.release,
    gameIdentity: installed.game.sts2
  });
  const env = {
    ...process.env,
    ...connectorCanary.environment
  };
  env.SteamAppId ??= "2868840";
  env.SteamGameId ??= "2868840";
  const child = spawn(installation.executable, [], {
    cwd: installation.executable_cwd,
    env,
    detached: true,
    stdio: "ignore",
    windowsHide: false
  });
  child.unref();
  const record = {
    schema: "sts2.platform/game-mod-launch-1",
    status: "launched",
    launched_at: new Date().toISOString(),
    pid: child.pid,
    executable: installation.executable,
    connector_runtime: connectorCanary.runtime,
    connector_environment: connectorCanary.environment,
    expected_artifact: installed.artifact,
    expected_modset_status: "exact_platform_modset"
  };
  writeJson(path.join(localRoot, "last-launch.json"), record);
  process.stdout.write(`${JSON.stringify(record, null, 2)}\n`);
}

async function fetchJson(route) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3000);
  try {
    const response = await fetch(`http://127.0.0.1:15526/${route}`, { signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

function latestIdentity(log, prefix) {
  return log.split(/\r?\n/u).flatMap((line) => {
    const index = line.indexOf(prefix);
    if (index < 0) return [];
    try { return [JSON.parse(line.slice(index + prefix.length))]; } catch { return []; }
  }).at(-1);
}

async function verifyLoaded() {
  if (!gameRunning()) throw new Error("Slay the Spire 2 is not running.");
  if (!fs.existsSync(installedProvenance)) throw new Error("Game Mod installed provenance is unavailable.");
  if (!installation.log_file || !fs.existsSync(installation.log_file)) throw new Error("STS2 runtime log is unavailable.");
  const installed = readJson(installedProvenance);
  const expected = installed.artifact;
  const processIds = extractGameProcessIds(gameProcesses(), process.platform);
  const statusPath = effectiveAnnotatorConfiguration(annotatorConfiguration(), process.env).runtime_status_path;
  const loaded = await waitForLoadedReadiness(async () => {
    if (!fs.existsSync(statusPath)) throw new Error("Annotator runtime status is unavailable.");
    const status = readJson(statusPath);
    const capabilities = await fetchJson("api/player-environment/capabilities");
    const log = fs.readFileSync(installation.log_file, "utf8");
    const platformIdentity = latestIdentity(log, "[STS2 Platform] identity ");
    const liveUiIdentity = latestIdentity(log, "[STS2 Platform Live UI] identity ");
    const latestUiIdentityIndex = log.lastIndexOf("[STS2 Platform Live UI] identity ");
    const uiLog = log.slice(Math.max(0, latestUiIdentityIndex));
    const evaluation = evaluateLoadedEvidence({
      status,
      capabilities,
      platformIdentity,
      liveUiIdentity,
      installed,
      uiPanelReady: latestUiIdentityIndex >= 0
        && uiLog.includes("[STS2 Platform Live UI] panel ready; input=launcher"),
      gameProcessIds: processIds
    });
    return { ...evaluation, status, capabilities, platformIdentity, liveUiIdentity, log, uiLog };
  });
  const { errors, status, capabilities, platformIdentity, liveUiIdentity, uiLog } = loaded;
  const result = {
    status: errors.length ? "fail" : "pass",
    errors,
    installed,
    platform_loaded_identity: platformIdentity,
    live_ui_loaded_identity: liveUiIdentity,
    runtime: status,
    connector_capabilities: capabilities,
    ui_toggle_runtime_canary: uiLog.includes(
      "[STS2 Platform Live UI] toggle; input=launcher; visible=true")
      ? "observed"
      : "not_observed",
    owner_ui_visibility: "pending human runtime evidence",
    non_claims: [
      "loaded_is_not_human_action_evidence",
      "ui_ready_is_not_ui_visible_evidence",
      "input_canary_is_not_owner_visibility_evidence"
    ]
  };
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  if (errors.length) process.exitCode = 1;
}

try {
  if (command === "doctor") doctor();
  else if (command === "deploy") deploy();
  else if (command === "rollback") rollback();
  else if (command === "launch") launch();
  else if (command === "verify-loaded") await verifyLoaded();
  else throw new Error(`Unknown command: ${command}`);
} catch (error) {
  process.stderr.write(`${JSON.stringify({
    status: "error",
    command,
    error: error instanceof Error ? error.message : String(error)
  }, null, 2)}\n`);
  process.exitCode = 1;
}
