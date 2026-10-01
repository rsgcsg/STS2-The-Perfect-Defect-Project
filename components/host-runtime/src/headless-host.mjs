import { spawnSync } from "node:child_process";
import {
  createWriteStream,
  existsSync,
  mkdirSync,
  readFileSync,
  unlinkSync,
  writeFileSync
} from "node:fs";
import path from "node:path";
import { finished } from "node:stream/promises";
import { SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL } from "@rsgcsg/sts2-connector-client";
import { readGameProcessStartedAt, listGameProcesses } from "./game-processes.mjs";
import {
  readJson,
  requestHostProvenance,
  requestHostShutdown,
  shippedRuntimeLaunch,
  snapshotIsInteractive,
  stopChild,
  waitForEndpoint,
  waitForInteractiveSnapshot,
  validateHostDisplayMode
} from "./runtime-probe.mjs";
import { readDiskIdentity, readInstalledConnectorIdentity } from "./game-installation.mjs";
import { requireSupportedRuntime } from "./compatibility.mjs";
import { readProjectIdentity } from "./project-identity.mjs";
import {
  compareFilesystemSnapshots,
  sharedGameUserDataRoot,
  snapshotFilesystemTree
} from "./filesystem-sentinel.mjs";
import { publicProfileDescriptor, resolveLaunchProfile } from "./profile-isolation.mjs";

const DEFAULT_ENDPOINT = "http://127.0.0.1:15526";

function safeTimestamp() {
  return new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-");
}

function writeJson(file, value) {
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

export function readHostRecord(file) {
  if (!existsSync(file)) return null;
  return JSON.parse(readFileSync(file, "utf8"));
}

export function processCommandResult(pid, platform = process.platform) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return { status: "absent", command: null };
  if (platform === "win32") {
    const script = [
      `$p = Get-CimInstance Win32_Process -Filter \"ProcessId = ${pid}\" -ErrorAction SilentlyContinue`,
      "if ($null -ne $p) { $p.CommandLine }"
    ].join("; ");
    const result = spawnSync(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-Command", script],
      { encoding: "utf8", windowsHide: true }
    );
    if (result.error || result.status !== 0) return { status: "unknown", command: null };
    const command = result.stdout.trim();
    return command ? { status: "observed", command } : { status: "absent", command: null };
  }
  const result = spawnSync("ps", ["-p", String(pid), "-o", "command="], {
    encoding: "utf8"
  });
  if (result.error) return { status: "unknown", command: null };
  const command = result.stdout.trim();
  if (result.status === 0 && command) return { status: "observed", command };
  try {
    process.kill(pid, 0);
    return { status: "unknown", command: null };
  } catch (error) {
    if (error?.code === "ESRCH") return { status: "absent", command: null };
  }
  return { status: "unknown", command: null };
}

export function processCommand(pid, platform = process.platform) {
  const result = processCommandResult(pid, platform);
  return result.status === "observed" ? result.command : null;
}

export function commandOwnsHeadlessRuntime(command, executable) {
  return commandOwnsRuntime(command, executable, ["--headless", "--verbose"], "win32");
}

function tokenizeCommandLine(command) {
  const tokens = [];
  let token = "";
  let quote = null;
  for (const character of command) {
    if (quote != null) {
      if (character === quote) quote = null;
      else token += character;
    } else if (character === '"' || character === "'") {
      quote = character;
    } else if (/\s/u.test(character)) {
      if (token) tokens.push(token);
      token = "";
    } else {
      token += character;
    }
  }
  if (quote != null) return null;
  if (token) tokens.push(token);
  return tokens;
}

function normalizedExecutable(file, platform = process.platform) {
  const normalized = file.replaceAll("\\", "/").replace(/\/$/u, "");
  return platform === "win32" ? normalized.toLowerCase() : normalized;
}

export function commandOwnsRuntime(command, executable, expectedArgs, platform = process.platform) {
  if (typeof command !== "string" || typeof executable !== "string"
      || !Array.isArray(expectedArgs) || expectedArgs.some((arg) => typeof arg !== "string")) {
    return false;
  }
  const raw = command.trimStart();
  let actualExecutable;
  let remaining;
  const quote = raw[0] === '"' || raw[0] === "'" ? raw[0] : null;
  if (quote != null) {
    const end = raw.indexOf(quote, 1);
    if (end < 0) return false;
    actualExecutable = raw.slice(1, end);
    remaining = raw.slice(end + 1).trimStart();
  } else {
    const expected = normalizedExecutable(executable, platform);
    const normalizedRaw = normalizedExecutable(raw, platform);
    if (!normalizedRaw.startsWith(expected)) return false;
    const boundary = raw[expected.length];
    if (boundary != null && !/\s/u.test(boundary)) return false;
    actualExecutable = raw.slice(0, expected.length);
    remaining = raw.slice(expected.length).trimStart();
  }
  const actualArgs = tokenizeCommandLine(remaining);
  return normalizedExecutable(actualExecutable, platform) === normalizedExecutable(executable, platform)
    && actualArgs != null
    && actualArgs.join("\u0000") === expectedArgs.join("\u0000");
}

export function recordedDisplayMode(record) {
  const mode = record?.display_mode ?? "headless";
  try {
    return validateHostDisplayMode(mode);
  } catch {
    return null;
  }
}

export function evaluateHostCapabilities(
  capabilities,
  displayMode = "headless",
  expectedProtocol = SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
  installedConnector = null
) {
  const errors = [];
  const expectedHostKind = displayMode === "native_window" ? "live_ui" : "headless";
  if (capabilities?.protocol_version !== expectedProtocol) errors.push("protocol_mismatch");
  if (capabilities?.host?.host_kind !== expectedHostKind) errors.push("host_kind_mismatch");
  if (!["exact_player_environment_only", "exact_platform_modset"]
    .includes(capabilities?.game?.modset?.status)) {
    errors.push("unsupported_modset");
  }
  if (capabilities?.execution_available !== true) errors.push("execution_unavailable");
  if (displayMode === "native_window") {
    if (typeof capabilities?.host?.runtime_instance_id !== "string"
        || capabilities.host.runtime_instance_id.length < 1) errors.push("runtime_instance_id_missing");
    if (installedConnector?.status !== "verified") {
      errors.push("installed_connector_identity_unverified");
    } else {
      const implementation = capabilities?.host?.implementation;
      const expectedMvid = installedConnector.identity?.artifact_mvid;
      if (typeof expectedMvid !== "string" || expectedMvid.length < 1) {
        errors.push("installed_connector_mvid_missing");
      }
      if (implementation?.artifact_sha256 !== installedConnector.installed_sha256) {
        errors.push("loaded_connector_sha_mismatch");
      }
      if (typeof implementation?.module_version_id !== "string"
          || implementation.module_version_id !== expectedMvid) {
        errors.push("loaded_connector_mvid_mismatch");
      }
      if (implementation?.source_revision !== installedConnector.identity?.source_revision) {
        errors.push("loaded_connector_revision_mismatch");
      }
    }
  }
  return { ok: errors.length === 0, errors, expected_host_kind: expectedHostKind };
}

export function evaluateHeadlessCapabilities(
  capabilities,
  expectedProtocol = SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL
) {
  const result = evaluateHostCapabilities(capabilities, "headless", expectedProtocol);
  const errors = result.errors.map((error) => error === "host_kind_mismatch"
    ? "host_kind_not_headless"
    : error);
  return { ok: errors.length === 0, errors };
}

function hostFiles(localRoot) {
  const runtimeRoot = path.join(localRoot, "runtime");
  return {
    runtimeRoot,
    current: path.join(runtimeRoot, "current.json"),
    control: path.join(runtimeRoot, "current-control.json")
  };
}

function expectedLaunchArgs(record) {
  const displayMode = recordedDisplayMode(record);
  if (displayMode == null) return null;
  const profileMode = record.profile?.mode;
  if (displayMode === "native_window" && profileMode !== "isolated_local_profile") return null;
  if (!(["isolated_local_profile", "shared_steam_profile"].includes(profileMode))) return null;
  return [
    ...(displayMode === "headless" ? ["--headless"] : []),
    "--verbose",
    ...(profileMode === "isolated_local_profile" ? ["--force-steam=off", "--clientId=1"] : [])
  ];
}

function processGeneration(pid, readStartedAt = readGameProcessStartedAt) {
  try {
    return readStartedAt(pid);
  } catch {
    return null;
  }
}

async function waitForProcessGeneration(pid, child, readStartedAt, wait) {
  const deadline = Date.now() + 3_000;
  while (Date.now() <= deadline) {
    if (child.exitCode != null || child.signalCode != null) return null;
    const startedAt = processGeneration(pid, readStartedAt);
    if (startedAt != null) return startedAt;
    await wait(100);
  }
  return null;
}

export function inspectRecordedProcess(record, {
  getProcessCommand = null,
  getProcessStartedAt = readGameProcessStartedAt
} = {}) {
  if (!record?.pid || !record?.executable) return { exists: false, matches: false, reason: "recorded_process_identity_missing", command: null };
  const processResult = getProcessCommand == null
    ? processCommandResult(record.pid)
    : (() => {
        const command = getProcessCommand(record.pid);
        return command == null ? { status: "absent", command: null } : { status: "observed", command };
      })();
  if (processResult.status === "absent") return { exists: false, matches: false, reason: "process_exited", command: null };
  if (processResult.status !== "observed") {
    return { exists: true, matches: false, reason: "process_command_unavailable", command: null };
  }
  const command = processResult.command;
  if (!record.process_started_at) {
    return { exists: true, matches: false, reason: "recorded_process_generation_missing", command };
  }
  const startedAt = processGeneration(record.pid, getProcessStartedAt);
  if (startedAt == null) {
    return { exists: true, matches: false, reason: "process_generation_unavailable", command };
  }
  if (startedAt !== record.process_started_at) {
    return { exists: true, matches: false, reason: "process_generation_mismatch", command };
  }
  const expectedArgs = expectedLaunchArgs(record);
  if (expectedArgs == null || JSON.stringify(record.args) !== JSON.stringify(expectedArgs)
      || !commandOwnsRuntime(command, record.executable, expectedArgs)) {
    return { exists: true, matches: false, reason: "process_command_mismatch", command };
  }
  return { exists: true, matches: true, reason: null, command };
}

function lifecycleFileFromRecord(record, runtimeRoot) {
  if (typeof record?.session_directory !== "string") return null;
  const sessionDirectory = path.resolve(record.session_directory);
  const relative = path.relative(path.resolve(runtimeRoot), sessionDirectory);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) return null;
  return path.join(sessionDirectory, "lifecycle.json");
}

function persistClosedRecord(record, files, writeRecord = writeJson) {
  const lifecycleFile = lifecycleFileFromRecord(record, files.runtimeRoot);
  if (lifecycleFile) writeRecord(lifecycleFile, record);
  const current = readHostRecord(files.current);
  if (current?.pid === record.pid && current?.session_directory === record.session_directory) {
    unlinkSync(files.current);
  }
  if (existsSync(files.control)) {
    const control = JSON.parse(readFileSync(files.control, "utf8"));
    if (control?.pid === record.pid && control?.session_directory === record.session_directory) {
      unlinkSync(files.control);
    }
  }
}

function finalizeSharedProfileSentinel(record, {
  snapshotSharedProfile = () => snapshotFilesystemTree(sharedGameUserDataRoot()),
  compareSnapshots = compareFilesystemSnapshots
} = {}) {
  if (record.shared_profile_sentinel == null && record.shared_profile_sentinel_before != null) {
    try {
      record.shared_profile_sentinel = compareSnapshots(
        record.shared_profile_sentinel_before,
        snapshotSharedProfile()
      );
      record.shared_profile_integrity_status = record.shared_profile_sentinel.unchanged
        ? "unchanged"
        : "changed";
    } catch (error) {
      record.shared_profile_sentinel = {
        unchanged: false,
        error: error instanceof Error ? error.message : String(error)
      };
      record.shared_profile_integrity_status = "measurement_failed";
    }
  }
  return record.shared_profile_integrity_status ?? "not_required";
}

function childReference(child) {
  return {
    get exitCode() {
      return child.exitCode;
    },
    get signalCode() {
      return child.signalCode;
    }
  };
}

export async function queryHeadlessStatus({
  localRoot,
  endpoint = DEFAULT_ENDPOINT,
  getProcessCommand = null,
  getProcessStartedAt = readGameProcessStartedAt,
  readEndpoint = readJson
}) {
  const files = hostFiles(localRoot);
  const record = readHostRecord(files.current);
  const process = record?.pid
    ? inspectRecordedProcess(record, { getProcessCommand, getProcessStartedAt })
    : null;
  const endpointResult = await readEndpoint(endpoint, "/api/player-environment/capabilities", 1000);
  return {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    lifecycle: record,
    process: record?.pid
      ? {
          pid: record.pid,
          running: process.exists,
          command_matches_record: process.matches,
          ownership_error: process.reason,
          command: process.command,
          display_mode: recordedDisplayMode(record)
        }
      : null,
    endpoint: endpointResult.ok
      ? {
          reachable: true,
          protocol: endpointResult.value.protocol_version,
          host: endpointResult.value.host,
          game: endpointResult.value.game
        }
      : { reachable: false, error: endpointResult.error }
  };
}

export async function stopHeadlessHost({
  localRoot,
  endpoint = DEFAULT_ENDPOINT,
  dependencies = {}
}) {
  const {
    getProcessCommand = null,
    getProcessStartedAt = readGameProcessStartedAt,
    requestShutdown = requestHostShutdown,
    killProcess = process.kill.bind(process),
    wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    readEndpoint = readJson,
    snapshotSharedProfile = () => snapshotFilesystemTree(sharedGameUserDataRoot()),
    compareSnapshots = compareFilesystemSnapshots,
    writeRecord = writeJson
  } = dependencies;
  const files = hostFiles(localRoot);
  const record = readHostRecord(files.current);
  if (!record?.pid || !record.executable) {
    return { status: "not_running", detail: "No Host Runtime lifecycle record exists." };
  }
  const inspect = () => inspectRecordedProcess(record, { getProcessCommand, getProcessStartedAt });
  let owned = inspect();
  if (!owned.exists) {
    if (owned.reason === "process_exited") {
      const status = finalizeSharedProfileSentinel(record, { snapshotSharedProfile, compareSnapshots });
      if (status === "changed" || status === "measurement_failed") record.status = "failed";
      persistClosedRecord(record, files, writeRecord);
      return {
        status: status === "changed" || status === "measurement_failed" ? "stopped_integrity_failed" : "not_running",
        detail: "The recorded process no longer exists.",
        shared_profile_integrity_status: status
      };
    }
    throw new Error(`Refusing to signal PID ${record.pid}; ${owned.reason}.`);
  }
  if (!owned.matches) {
    throw new Error(`Refusing to signal PID ${record.pid}; ${owned.reason}.`);
  }
  const control = existsSync(files.control)
    ? JSON.parse(readFileSync(files.control, "utf8"))
    : null;
  const hostShutdown = await requestShutdown({
    endpoint,
    hostControlToken: control?.host_control_token ?? null,
    expectedRuntimeInstanceId: record.loaded_identity?.host?.runtime_instance_id ?? null
  });
  const started = Date.now();
  let stopped = false;
  while (hostShutdown.status === "requested" && Date.now() - started < 10_000) {
    owned = inspect();
    if (!owned.exists) {
      stopped = owned.reason === "process_exited";
      if (!stopped) return { status: "ownership_lost", pid: record.pid, host_shutdown: hostShutdown, reason: owned.reason };
      break;
    }
    if (!owned.matches) {
      return { status: "ownership_lost", pid: record.pid, host_shutdown: hostShutdown, reason: owned.reason };
    }
    await wait(100);
  }
  let forced = false;
  if (!stopped) {
    // PID and start-time are checked again immediately before any fallback signal.
    owned = inspect();
    if (!owned.exists && owned.reason === "process_exited") stopped = true;
    else if (!owned.matches) {
      return { status: "ownership_lost", pid: record.pid, host_shutdown: hostShutdown, reason: owned.reason };
    } else {
      try {
        killProcess(record.pid, "SIGKILL");
        forced = true;
      } catch (error) {
        owned = inspect();
        if (!owned.exists && owned.reason === "process_exited") stopped = true;
        else throw error;
      }
    }
  }
  if (!stopped && forced) {
    forced = true;
    const forcedStarted = Date.now();
    while (Date.now() - forcedStarted < 5_000) {
      owned = inspect();
      if (!owned.exists && owned.reason === "process_exited") {
        stopped = true;
        break;
      }
      if (!owned.matches) break;
      await wait(100);
    }
  }
  if (!stopped) {
    owned = inspect();
    if (!owned.exists && owned.reason === "process_exited") stopped = true;
    else if (!owned.matches) {
      return { status: "ownership_lost", pid: record.pid, host_shutdown: hostShutdown, reason: owned.reason };
    }
  }
  if (stopped) {
    const integrity = finalizeSharedProfileSentinel(record, { snapshotSharedProfile, compareSnapshots });
    if (integrity === "changed" || integrity === "measurement_failed") {
      record.status = "failed";
      record.error = `Shared game profile sentinel ${integrity}.`;
    }
    persistClosedRecord(record, files, writeRecord);
    const endpointAfter = await readEndpoint(endpoint, "/api/player-environment/capabilities", 1000);
    return {
      status: integrity === "changed" || integrity === "measurement_failed" ? "stopped_integrity_failed" : "stopped",
      pid: record.pid,
      host_shutdown: hostShutdown,
      forced,
      endpoint_released: !endpointAfter.ok,
      shared_profile_integrity_status: integrity,
      shared_profile_sentinel: record.shared_profile_sentinel ?? null
    };
  }
  const endpointAfter = await readEndpoint(endpoint, "/api/player-environment/capabilities", 1000);
  return {
    status: "still_running",
    pid: record.pid,
    host_shutdown: hostShutdown,
    forced,
    endpoint_released: !endpointAfter.ok,
    close_unconfirmed: true
  };
}

export async function runHeadlessHost({
  installation,
  localRoot,
  endpoint = DEFAULT_ENDPOINT,
  timeoutMs = 90_000,
  mirrorLogs = false,
  sharedProfileAcknowledged = false,
  isolatedProfileId = null,
  displayMode = "headless",
  dependencies = {}
}) {
  validateHostDisplayMode(displayMode);
  if (displayMode === "native_window" && (!isolatedProfileId || sharedProfileAcknowledged)) {
    throw new Error("Native-window Host launches require --isolated-profile and reject --shared-profile.");
  }
  const {
    launchRuntime = shippedRuntimeLaunch,
    getProcessCommand = null,
    getProcessStartedAt = readGameProcessStartedAt,
    enumerateGameProcesses = listGameProcesses,
    readEndpoint = readJson,
    waitEndpoint = waitForEndpoint,
    waitSnapshot = waitForInteractiveSnapshot,
    waitChildExit = (child) => new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal }))),
    requestProvenance = requestHostProvenance,
    stopRuntimeChild = stopChild,
    wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    snapshotSharedProfile = () => snapshotFilesystemTree(sharedGameUserDataRoot()),
    compareSnapshots = compareFilesystemSnapshots,
    getInstalledIdentity = readInstalledConnectorIdentity,
    getDiskIdentity = readDiskIdentity,
    requireSupported = requireSupportedRuntime,
    projectIdentity = readProjectIdentity,
    writeRecord = writeJson,
    signalSource = process,
    installSignalHandlers = true
  } = dependencies;
  const launchProfile = resolveLaunchProfile({
    localRoot,
    isolatedProfileId,
    sharedProfileAcknowledged
  });
  const files = hostFiles(localRoot);
  const diskIdentity = getDiskIdentity(installation);
  const compatibility = requireSupported(diskIdentity);
  const existing = await queryHeadlessStatus({
    localRoot,
    endpoint,
    getProcessCommand,
    getProcessStartedAt,
    readEndpoint
  });
  if (existing.process?.running || existing.endpoint.reachable) {
    throw new Error("A game process or Connector endpoint is already active; inspect it with `npm run status`.");
  }
  if (displayMode === "native_window") {
    const running = enumerateGameProcesses(undefined, { failClosed: true });
    if (running.length > 0) {
      throw new Error(`Refusing native-window launch beside an existing STS2 process:\n${running.join("\n")}`);
    }
  }

  const installedConnector = displayMode === "native_window" ? getInstalledIdentity(installation) : null;
  if (displayMode === "native_window" && installedConnector?.status !== "verified") {
    throw new Error(`Native-window launch requires a verified installed Connector identity; got ${installedConnector?.status ?? "missing"}.`);
  }
  const sharedProfileSentinelBefore = displayMode === "native_window"
    ? snapshotSharedProfile()
    : null;
  if (displayMode === "native_window"
      && (typeof sharedProfileSentinelBefore?.root !== "string"
        || typeof sharedProfileSentinelBefore.present !== "boolean")) {
    throw new Error("Could not establish the shared game-profile sentinel before visible launch.");
  }

  const sessionDirectory = path.join(files.runtimeRoot, `session-${safeTimestamp()}`);
  mkdirSync(sessionDirectory, { recursive: true });
  const stdoutFile = path.join(sessionDirectory, "stdout.log");
  const stderrFile = path.join(sessionDirectory, "stderr.log");
  mkdirSync(files.runtimeRoot, { recursive: true });
  const lifecycleFile = path.join(sessionDirectory, "lifecycle.json");
  const stdoutStream = createWriteStream(stdoutFile);
  const stderrStream = createWriteStream(stderrFile);
  let launch = null;
  let child = null;
  let record = {
    schema_version: 2,
    status: "starting",
    started_at: new Date().toISOString(),
    pid: null,
    executable: installation.executable,
    args: [],
    display_mode: displayMode,
    endpoint,
    connector: null,
    profile: publicProfileDescriptor(launchProfile),
    ...(displayMode === "headless" ? { headless: projectIdentity() } : { host_runtime_identity: projectIdentity() }),
    session_directory: sessionDirectory,
    stdout_file: stdoutFile,
    stderr_file: stderrFile,
    disk_identity: diskIdentity,
    compatibility,
    requested_display_mode: displayMode,
    loaded_host_kind: null,
    process_started_at: null,
    shared_profile_sentinel_before: sharedProfileSentinelBefore,
    shared_profile_integrity_status: displayMode === "native_window" ? "pending" : "not_required",
    loaded_identity: null
  };
  let signalPromise = null;
  let signalRequested = false;
  let signalHandlersInstalled = false;
  let completedSentinel = false;
  let childExit = null;
  let runtimeInstanceId = null;
  const beginSignalShutdown = () => {
    if (!signalRequested || signalPromise != null || child == null || !record.process_started_at) return;
    signalPromise = stopRuntimeChild(child, {
      endpoint,
      hostControlToken: launch?.hostControlToken ?? null,
      expectedRuntimeInstanceId: runtimeInstanceId,
      beforeSignal: () => inspectRecordedProcess(record, { getProcessCommand, getProcessStartedAt }).matches
    });
  };
  const onSignal = () => {
    signalRequested = true;
    beginSignalShutdown();
  };
  const writeLifecycle = () => writeRecord(lifecycleFile, record);
  const writeOwnedCurrent = () => {
    writeRecord(files.current, record);
    writeFileSync(files.control, `${JSON.stringify({
      schema_version: 1,
      pid: child.pid,
      process_started_at: record.process_started_at,
      display_mode: displayMode,
      session_directory: sessionDirectory,
      host_control_token: launch?.hostControlToken ?? null
    })}\n`, { mode: 0o600 });
  };
  const finalizeSentinelIfClosed = () => {
    if (displayMode !== "native_window" || completedSentinel || record.shared_profile_sentinel != null) return;
    const persisted = readHostRecord(lifecycleFile);
    if (persisted?.shared_profile_sentinel != null) {
      record = {
        ...record,
        shared_profile_sentinel: persisted.shared_profile_sentinel,
        shared_profile_integrity_status: persisted.shared_profile_integrity_status
      };
      completedSentinel = true;
      return;
    }
    record.shared_profile_integrity_status = finalizeSharedProfileSentinel(record, {
      snapshotSharedProfile,
      compareSnapshots
    });
    completedSentinel = true;
  };
  const closeConfirmed = () => child != null && (child.exitCode != null || child.signalCode != null || childExit != null);
  try {
    launch = launchRuntime(installation, {
      launchProfile,
      displayMode,
      connectorEndpoint: endpoint
    });
    child = launch.child;
    child.stdout.pipe(stdoutStream);
    child.stderr.pipe(stderrStream);
    if (mirrorLogs) {
      child.stdout.pipe(process.stdout);
      child.stderr.pipe(process.stderr);
    }
    child.once("exit", (code, signal) => { childExit = { code, signal }; });
    if (installSignalHandlers) {
      signalSource.once("SIGINT", onSignal);
      signalSource.once("SIGTERM", onSignal);
      signalHandlersInstalled = true;
    }
    if (!Number.isSafeInteger(child.pid) || child.pid <= 0) {
      throw new Error("The shipped runtime did not provide a valid owned process identifier.");
    }
    // Persist the child identity as soon as spawn returns. If a later readiness
    // gate fails before the process generation can be measured, the lifecycle
    // remains visible and blocks a second launch; it still cannot signal that
    // process without a verified generation stamp.
    record = {
      ...record,
      pid: child.pid,
      args: launch.args,
      connector: launch.connector
    };
    writeLifecycle();
    writeOwnedCurrent();
    const startedAt = await waitForProcessGeneration(child.pid, child, getProcessStartedAt, wait);
    if (startedAt == null) throw new Error("Could not establish the launched process generation.");
    record = {
      ...record,
      process_started_at: startedAt
    };
    writeLifecycle();
    writeOwnedCurrent();
    if (launch.hostControlToken == null) {
      throw new Error("Connector Host lifecycle control was not configured for the process.");
    }
    beginSignalShutdown();
    if (signalRequested) throw new Error("Host launch interrupted by an operator signal during startup.");

    const capabilitiesResult = await waitEndpoint(endpoint, timeoutMs, childReference(child));
    if (!capabilitiesResult.ok) {
      throw new Error(`Connector endpoint did not become ready: ${capabilitiesResult.error}`);
    }
    runtimeInstanceId = capabilitiesResult.value?.host?.runtime_instance_id ?? null;
    const capabilityGate = displayMode === "headless"
      ? evaluateHeadlessCapabilities(capabilitiesResult.value)
      : evaluateHostCapabilities(capabilitiesResult.value, displayMode,
        SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL, installedConnector);
    if (!capabilityGate.ok) {
      throw new Error(`Loaded environment failed the ${displayMode} Host gate: ${capabilityGate.errors.join(", ")}`);
    }
    if (displayMode === "native_window") {
      const provenance = await requestProvenance({
        endpoint,
        hostControlToken: launch.hostControlToken,
        expectedRuntimeInstanceId: runtimeInstanceId
      });
      if (provenance.status !== "observed"
          || provenance.response?.runtime_instance_id !== runtimeInstanceId) {
        throw new Error("Authenticated Host provenance did not bind the isolated native-window process to its endpoint.");
      }
    }
    const snapshots = await waitSnapshot(endpoint, timeoutMs, childReference(child));
    const snapshot = snapshots.at(-1);
    if (!snapshotIsInteractive(snapshot)) {
      throw new Error("The real runtime loaded but did not mount an interactive Player Environment decision.");
    }
    record = {
      ...record,
      status: "ready",
      ready_at: new Date().toISOString(),
      loaded_host_kind: capabilitiesResult.value.host.host_kind,
      loaded_identity: {
        protocol: capabilitiesResult.value.protocol_version,
        host: capabilitiesResult.value.host,
        game: capabilitiesResult.value.game
      },
      initial_snapshot: {
        snapshot_id: snapshot.value.snapshot_id,
        status: snapshot.value.status,
        interaction_kind: snapshot.value.interaction.kind,
        bound_action_count: snapshot.value.bound_actions.actions.length
      }
    };
    writeOwnedCurrent();
    writeLifecycle();
    console.log(JSON.stringify({ status: "ready", ...record.loaded_identity, initial_snapshot: record.initial_snapshot }, null, 2));
    const exitPromise = childExit == null ? waitChildExit(child) : Promise.resolve(childExit);
    let exit = null;
    if (signalPromise == null) {
      exit = await exitPromise;
    } else {
      const outcome = await Promise.race([
        exitPromise.then((value) => ({ kind: "exit", value })),
        signalPromise.then((value) => ({ kind: "signal", value }))
      ]);
      if (outcome.kind === "exit") exit = outcome.value;
      else {
        if (outcome.value == null && !closeConfirmed()) {
          throw new Error("Host signal shutdown did not confirm process exit; lifecycle ownership is retained.");
        }
        exit = childExit ?? { code: outcome.value?.code ?? null, signal: outcome.value?.signal ?? null };
      }
    }
    childExit = exit;
    record = { ...record, status: "exited", exited_at: new Date().toISOString(), exit };
    finalizeSentinelIfClosed();
    if (record.shared_profile_integrity_status === "changed"
        || record.shared_profile_integrity_status === "measurement_failed") {
      record = { ...record, status: "failed", error: "Shared game profile sentinel did not remain unchanged." };
    }
    writeLifecycle();
    persistClosedRecord(record, files, writeRecord);
    if (record.status === "failed") throw new Error(record.error);
    return record;
  } catch (error) {
    let stopResult = null;
    if (child != null && !closeConfirmed()) {
      try {
        stopResult = signalPromise == null
          ? await stopRuntimeChild(child, {
              endpoint,
              hostControlToken: launch?.hostControlToken ?? null,
              expectedRuntimeInstanceId: runtimeInstanceId,
              beforeSignal: () => inspectRecordedProcess(record, { getProcessCommand, getProcessStartedAt }).matches
            })
          : await signalPromise;
      } catch (stopError) {
        stopResult = { status: "stop_error", error: stopError instanceof Error ? stopError.message : String(stopError) };
      }
    }
    if (childExit == null && stopResult != null
        && typeof stopResult === "object"
        && (Object.hasOwn(stopResult, "code") || Object.hasOwn(stopResult, "signal"))) {
      childExit = { code: stopResult.code ?? null, signal: stopResult.signal ?? null };
    }
    const closed = child == null || closeConfirmed();
    record = {
      ...record,
      status: closed ? "failed" : "close_unconfirmed",
      failed_at: new Date().toISOString(),
      error: error instanceof Error ? error.message : String(error),
      stop_result: stopResult,
      exit: childExit
    };
    if (closed) {
      finalizeSentinelIfClosed();
      if (record.shared_profile_integrity_status === "changed"
          || record.shared_profile_integrity_status === "measurement_failed") {
        record.error = `${record.error}; shared game profile sentinel ${record.shared_profile_integrity_status}`;
      }
      writeLifecycle();
      persistClosedRecord(record, files, writeRecord);
    } else {
      record.shared_profile_integrity_status = displayMode === "native_window" ? "pending_process_close" : "not_required";
      writeLifecycle();
      if (child?.pid != null) writeOwnedCurrent();
    }
    throw error;
  } finally {
    if (signalHandlersInstalled) {
      signalSource.off("SIGINT", onSignal);
      signalSource.off("SIGTERM", onSignal);
    }
    if (child == null || closeConfirmed()) {
      stdoutStream.end();
      stderrStream.end();
    } else {
      child.once("exit", () => {
        stdoutStream.end();
        stderrStream.end();
      });
    }
    if (child == null || closeConfirmed()) {
      await Promise.allSettled([finished(stdoutStream), finished(stderrStream)]);
    }
  }
}
