#!/usr/bin/env node
// Installed collection setup is a Game Mod operation; it never delivers gameplay.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { discoverGameDirectory, resolveInstallation } from "../../components/host-runtime/src/game-installation.mjs";
import { listGameProcesses, readGameProcessStartedAt } from "../../components/host-runtime/src/game-processes.mjs";
import { readAnnotatorConfiguration } from "./annotator-configuration.mjs";
import { evaluateCollectionLoadedEvidence, extractGameProcessIds } from "./loaded-evidence.mjs";
import { resolvePlatformInstallation } from "./platform-installation.mjs";

const schema = "sts2.platform/collection-setup-1";
const readJson = file => JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/u, ""));
const hash = bytes => crypto.createHash("sha256").update(bytes).digest("hex");
const overrides = ["STS2_HUMAN_ANNOTATOR_RECORDING_ROOT", "STS2_HUMAN_ANNOTATOR_STATUS_PATH"];

function localApplicationData(platform, env) {
  if (platform === "win32") return env.LOCALAPPDATA ?? path.join(os.homedir(), "AppData", "Local");
  if (platform === "darwin") return path.join(os.homedir(), "Library", "Application Support");
  return env.XDG_DATA_HOME ?? path.join(os.homedir(), ".local", "share");
}

function requireSafePath(value) {
  if (typeof value !== "string" || !path.isAbsolute(value) || value.includes("\0"))
    throw new Error("absolute_path_required");
  const absolute = path.resolve(value);
  for (let current = absolute; ; current = path.dirname(current)) {
    try { if (fs.lstatSync(current).isSymbolicLink()) throw new Error("symlink_path_refused"); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
    if (path.dirname(current) === current) break;
  }
  return absolute;
}

function isInside(root, value) {
  const relative = path.relative(root, value);
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

function requireRecordingRoot(root, installation, modDirectory) {
  requireSafePath(root);
  if (root === path.parse(root).root || isInside(modDirectory, root) || isInside(root, modDirectory)
      || isInside(installation.game_dir, root)
      || isInside(root, installation.game_dir)) throw new Error("unsafe_recordings_root");
  for (let current = root; ; current = path.dirname(current)) {
    if (fs.existsSync(path.join(current, "recording-manifest.json")))
      throw new Error("recording_session_is_not_root");
    if (path.dirname(current) === current) break;
  }
  if (fs.existsSync(root) && !fs.statSync(root).isDirectory()) throw new Error("recordings_root_is_not_directory");
}

function latestIdentity(log, prefix) {
  return log.split(/\r?\n/u).flatMap(line => {
    const index = line.indexOf(prefix);
    if (index < 0) return [];
    try { return [JSON.parse(line.slice(index + prefix.length))]; } catch { return []; }
  }).at(-1);
}

// An open Recorder store uses root / exact session_id. Successful Close disposes
// the store and reports the configured root, while retaining the closed session ID.
// No directory scan or last recording receipt supplies a current process destination.
export function nativeRecordingRoot(status) {
  const directory = status?.recording_directory;
  if (typeof directory !== "string" || !path.isAbsolute(directory) || directory.includes("\0")) return null;
  if (status.status === "recording_closed") {
    const session = status.session_id;
    if (typeof session !== "string" || !session || ["none", ".", ".."].includes(session)
        || /[/\\\0]/u.test(session)) return null;
    return path.resolve(directory);
  }
  if (status.session_id === "none") return path.resolve(directory);
  if (typeof status.session_id !== "string" || path.basename(directory) !== status.session_id) return null;
  return path.dirname(path.resolve(directory));
}

async function fetchCapabilities(port) {
  const response = await fetch(`http://127.0.0.1:${port}/api/player-environment/capabilities`, {
    signal: AbortSignal.timeout(3000), redirect: "error"
  });
  if (!response.ok) throw new Error("connector_unavailable");
  return response.json();
}

export async function collectionSetup(command, options, dependencies = {}) {
  const env = dependencies.env ?? process.env;
  const platform = dependencies.platform ?? process.platform;
  const processes = dependencies.listProcesses ?? (() => listGameProcesses(platform, { failClosed: true }));
  const installation = dependencies.installation ?? resolveInstallation(options.game_dir ?? discoverGameDirectory({ env }));
  const result = {
    schema, status: "blocked", reason: "installation_unavailable", next_action: "review_runtime",
    observed_at: new Date().toISOString(), game_running: null, configured: false, connected: false, bound: false,
    recordings_root: options.recordings_root ?? null, configured_recordings_root: null, actual_recordings_root: null,
    recording_directory: null, runtime_status_path: null, game_directory: installation?.game_dir ?? null,
    installed_artifact: null, installation_kind: null, workshop_item_id: null,
    legacy_workshop_state: false, loaded_identity: null, execution_available: null, compatibility: null, errors: [],
    non_claims: ["configuration_is_not_loaded", "loaded_is_not_human_evidence", "no_gameplay_operations"]
  };
  try {
    if (!["status", "bind"].includes(command)) throw new Error("unsupported_setup_command");
    if (!installation) throw new Error("installation_unavailable");
    for (const file of [installation.game_dir, installation.executable, installation.mods_dir]) requireSafePath(file);
    if (!fs.existsSync(installation.executable) || !fs.existsSync(installation.mods_dir)) throw new Error("installation_unavailable");
    const provenance = readJson(requireSafePath(options.mod_provenance));
    if (!["sts2.platform/game-mod-build-provenance-1", "sts2.platform/game-mod-installed-provenance-1"].includes(provenance.schema)
        || !/^[0-9a-f]{64}$/u.test(provenance.artifact?.sha256 ?? "")
        || typeof provenance.artifact?.module_version_id !== "string"
        || !provenance.source?.components?.live_ui || !provenance.source?.platform)
      throw new Error("mod_provenance_invalid");
    const tool = path.resolve(options.mod_provenance, "../../sts2-human-annotator.dll");
    const mod = (dependencies.resolvePlatform ?? resolvePlatformInstallation)(installation, { identityTool: tool });
    requireSafePath(mod.directory);
    result.installation_kind = mod.kind;
    result.workshop_item_id = mod.item_id;
    result.legacy_workshop_state = mod.kind === "workshop" && [
      "recordings", "STS2_HUMAN_ANNOTATOR.runtime.json", "STS2_HUMAN_ANNOTATOR.conf", "STS2_MCP.conf"
    ].some(name => fs.existsSync(path.join(mod.directory, name)));
    const root = requireSafePath(options.recordings_root);
    result.recordings_root = root;
    requireRecordingRoot(root, installation, mod.directory);
    const userState = localApplicationData(platform, env);
    const writable = mod.kind === "workshop"
      ? requireSafePath(path.join(userState, "spireagent", "annotator"))
      : installation.mods_dir;
    const configPath = requireSafePath(path.join(writable, "STS2_HUMAN_ANNOTATOR.conf"));
    const configuration = readAnnotatorConfiguration(configPath, {
      recording_root: path.join(writable, "recordings"),
      runtime_status_path: path.join(writable, "STS2_HUMAN_ANNOTATOR.runtime.json")
    });
    requireSafePath(configuration.recording_root);
    requireSafePath(configuration.runtime_status_path);
    result.configured_recordings_root = path.resolve(configuration.recording_root);
    result.runtime_status_path = configuration.runtime_status_path;
    result.configured = result.configured_recordings_root === root;
    const processIds = extractGameProcessIds(processes(), platform);
    result.game_running = processIds.length > 0;
    if (processIds.length > 1) throw new Error("ambiguous_game_processes");
    if (overrides.some(name => env[name] !== undefined)) throw new Error("annotator_environment_override");
    for (const name of ["STS2_MCP.json", "STS2_HUMAN_ANNOTATOR.json", "STS2_PLATFORM_LIVE_UI.json"])
      if (fs.existsSync(path.join(installation.mods_dir, name))) throw new Error("ambiguous_mod_installation");
    if (mod.artifact.sha256 !== provenance.artifact.sha256
        || mod.artifact.module_version_id !== provenance.artifact.module_version_id)
      throw new Error("installed_artifact_mismatch");
    result.installed_artifact = mod.artifact;
    if (command === "bind") {
      if (result.game_running) throw new Error("game_must_be_stopped");
      fs.mkdirSync(root, { recursive: true });
      if (!result.configured) {
        fs.mkdirSync(writable, { recursive: true });
        const before = fs.existsSync(configPath) ? fs.readFileSync(configPath) : null;
        const backup = `${configPath}.collection-backup-${crypto.randomUUID()}`;
        const temporary = `${configPath}.tmp-${crypto.randomUUID()}`;
        try {
          if (before !== null) fs.writeFileSync(backup, before, { flag: "wx", mode: 0o600 });
          fs.writeFileSync(temporary, `${JSON.stringify({ ...configuration, recording_root: root }, null, 2)}\n`, { flag: "wx", mode: 0o600 });
          // Recheck immediately before commit. Never silently rebind a now-running game.
          if (processes().length !== 0) throw new Error("game_must_be_stopped");
          const current = fs.existsSync(configPath) ? fs.readFileSync(configPath) : null;
          if (before === null ? current !== null : current === null || !before.equals(current)) throw new Error("configuration_changed");
          requireSafePath(configPath);
          (dependencies.replaceConfiguration ?? fs.renameSync)(temporary, configPath);
          result.backup_path = before === null ? null : backup;
        } finally { fs.rmSync(temporary, { force: true }); }
        result.configured_recordings_root = root;
        result.configured = true;
      }
    }
    if (!result.game_running) {
      result.status = result.configured ? "configured" : "blocked";
      result.reason = result.configured ? "configured_game_stopped" : "recording_root_not_configured";
      result.next_action = result.configured ? "launch_game" : "bind_recording_root";
      return result;
    }
    const legacyStatus = path.join(mod.directory, "STS2_HUMAN_ANNOTATOR.runtime.json");
    const statusPath = mod.kind === "workshop" && !fs.existsSync(configuration.runtime_status_path)
      && fs.existsSync(legacyStatus) ? legacyStatus : configuration.runtime_status_path;
    result.legacy_workshop_state ||= statusPath === legacyStatus;
    const status = readJson(statusPath);
    if (status.schema !== "sts2.human-annotator/runtime-status-2") throw new Error("runtime_status_schema_unsupported");
    const log = fs.readFileSync(installation.log_file, "utf8");
    const platformIdentity = latestIdentity(log, "[STS2 Platform] identity ");
    const liveUiIdentity = latestIdentity(log, "[STS2 Platform Live UI] identity ");
    const uiIndex = log.lastIndexOf("[STS2 Platform Live UI] identity ");
    const operatorConnector = path.join(userState, "spireagent", "connector", "STS2_MCP.conf");
    const connectorPath = fs.existsSync(operatorConnector) ? operatorConnector : path.join(mod.directory, "STS2_MCP.conf");
    const connectorConfig = fs.existsSync(connectorPath) ? readJson(connectorPath) : { port: 15526 };
    const port = connectorConfig.port ?? 15526;
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("connector_port_invalid");
    const capabilities = await (dependencies.fetchCapabilities ?? fetchCapabilities)(port);
    result.execution_available = capabilities.execution_available === true;
    result.compatibility = capabilities.game?.compatibility ?? capabilities.support ?? null;
    const evaluation = evaluateCollectionLoadedEvidence({ status, capabilities, platformIdentity, liveUiIdentity,
      installed: provenance, uiPanelReady: uiIndex >= 0 && log.slice(uiIndex).includes("[STS2 Platform Live UI] panel ready; input=launcher"),
      gameProcessIds: extractGameProcessIds(processes(), platform) });
    const processStartedAt = (dependencies.readProcessStartedAt ?? (pid => readGameProcessStartedAt(pid, platform)))(status.process_id);
    if (!(Date.parse(platformIdentity?.loaded_at) >= Date.parse(processStartedAt))) {
      evaluation.errors.push("platform_loaded_process_generation_mismatch");
      evaluation.ready = false;
    }
    if (status.environment?.runtime_instance_id !== undefined
        && status.environment.runtime_instance_id !== capabilities.host?.runtime_instance_id) {
      evaluation.errors.push("recording_runtime_instance_mismatch");
      evaluation.ready = false;
    }
    result.errors = evaluation.errors;
    result.loaded_identity = evaluation.ready && platformIdentity ? { ...platformIdentity, process_id: status.process_id,
      process_started_at: processStartedAt,
      game: { version: capabilities.game?.version ?? null, revision: capabilities.game?.commit ?? null,
        assembly_sha256: status.environment?.game?.main_assembly_sha256 ?? null,
        module_version_id: status.environment?.game?.main_assembly_module_version_id ?? null } } : null;
    result.connected = evaluation.ready;
    if (!evaluation.ready) throw new Error("runtime_identity_unconfirmed");
    result.actual_recordings_root = nativeRecordingRoot(status);
    result.recording_directory = status.recording_directory ?? null;
    result.bound = result.actual_recordings_root === root;
    result.status = result.bound ? "bound" : "blocked";
    result.reason = result.bound ? "current_runtime_bound" : "current_runtime_root_mismatch";
    result.next_action = result.bound ? "none" : "close_game";
    return result;
  } catch (error) {
    // OS/parser diagnostics can contain private paths. Stable codes are the public projection.
    const code = /^[a-z][a-z_]+$/u.test(error.message ?? "") ? error.message : "setup_inspection_failed";
    result.reason = code;
    result.errors = [...new Set([...result.errors, code])];
    result.next_action = code === "game_must_be_stopped" ? "close_game" : "review_runtime";
    return result;
  }
}

if (process.argv[1] && fs.existsSync(process.argv[1]) && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [command, ...arguments_] = process.argv.slice(2);
  const options = {};
  for (let index = 0; index < arguments_.length; index += 2) {
    const key = { "--game-dir": "game_dir", "--recordings-root": "recordings_root", "--mod-provenance": "mod_provenance" }[arguments_[index]];
    if (!key || !arguments_[index + 1] || options[key] !== undefined) throw new Error("invalid_setup_arguments");
    options[key] = arguments_[index + 1];
  }
  const result = await collectionSetup(command, options);
  process.stdout.write(`${JSON.stringify(result)}\n`);
}
