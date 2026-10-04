import { listGameProcesses } from "./game-processes.mjs";
import { spawn, spawnSync } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { createWriteStream, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { finished } from "node:stream/promises";
import {
  EnvironmentControllerSession,
  PlayerEnvironmentRestClient
} from "@rsgcsg/sts2-connector-client";
import { evaluateMenuControlGate, evaluateShippedProbe } from "./probe-verdict.mjs";
import {
  readDiskIdentity,
  readInstalledConnectorIdentity,
  STS2_APP_ID
} from "./game-installation.mjs";
import { evaluateRuntimeCompatibility } from "./compatibility.mjs";
import { readProjectIdentity } from "./project-identity.mjs";
import { publicProfileDescriptor, resolveLaunchProfile } from "./profile-isolation.mjs";
import {
  CONNECTOR_PORT_ENVIRONMENT_VARIABLE,
  GAME_CANARY_ENVIRONMENT_VARIABLE,
  HOST_CONTROL_TOKEN_ENVIRONMENT_VARIABLE,
  resolveConnectorEndpoint,
  SOURCE_CANARY_ENVIRONMENT_VARIABLE
} from "./connector-endpoint.mjs";
import { validateRequestedHostExecutionProfile } from "./host-execution-profile.mjs";

const NATIVE_LIFECYCLE_ENVIRONMENT_NAMES = Object.freeze([
  CONNECTOR_PORT_ENVIRONMENT_VARIABLE,
  HOST_CONTROL_TOKEN_ENVIRONMENT_VARIABLE,
  GAME_CANARY_ENVIRONMENT_VARIABLE,
  SOURCE_CANARY_ENVIRONMENT_VARIABLE,
  "STS2_CONNECTOR_RUN_SEED",
  "STS2_CONNECTOR_HOST_EXECUTION_PROFILE",
  "SteamAppId",
  "SteamGameId"
]);

export function resolveExperimentalConnectorCanary({
  installation,
  compatibility,
  acknowledged
}) {
  if (!acknowledged) return null;
  const installed = readInstalledConnectorIdentity(installation);
  if (installed.status !== "verified") {
    throw new Error(
      `Experimental Connector authority requires a verified installed identity sidecar; got ${installed.status}.`
    );
  }
  return {
    game_id: compatibility?.status === "known_experimental"
      ? compatibility.support_id
      : null,
    source_revision: installed.identity.source_revision,
    artifact_sha256: installed.installed_sha256
  };
}

export function withExplicitConnectorCanary(environment, connectorCanary) {
  const resolved = { ...environment };
  delete resolved[GAME_CANARY_ENVIRONMENT_VARIABLE];
  delete resolved[SOURCE_CANARY_ENVIRONMENT_VARIABLE];
  if (connectorCanary == null) return resolved;

  if (connectorCanary.game_id != null) {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(connectorCanary.game_id)) {
      throw new Error("Experimental Connector game canaries require one bounded support id.");
    }
    resolved[GAME_CANARY_ENVIRONMENT_VARIABLE] = connectorCanary.game_id;
  }
  if (connectorCanary.source_revision != null) {
    if (!/^[0-9a-f]{40}$/u.test(connectorCanary.source_revision)) {
      throw new Error("Experimental Connector source canaries require one exact Git revision.");
    }
    resolved[SOURCE_CANARY_ENVIRONMENT_VARIABLE] = connectorCanary.source_revision;
  }
  return resolved;
}

export { listGameProcesses } from "./game-processes.mjs";

export const HOST_DISPLAY_MODES = Object.freeze(["headless", "native_window"]);

export function validateHostDisplayMode(displayMode = "headless") {
  if (!HOST_DISPLAY_MODES.includes(displayMode)) {
    throw new Error(`Host display mode must be ${HOST_DISPLAY_MODES.join(" or ")}.`);
  }
  return displayMode;
}

function environmentNameKey(name) {
  return process.platform === "win32" ? name.toUpperCase() : name;
}

function nativeWindowProfileEnvironment(launchProfile) {
  const profileRoot = launchProfile?.profile_root;
  const profileHome = path.join(profileRoot ?? "", "home");
  const expected = {
    HOME: path.resolve(profileHome),
    USERPROFILE: path.resolve(profileHome)
  };
  if (process.platform === "win32") {
    expected.APPDATA = path.resolve(profileHome, "AppData", "Roaming");
    expected.LOCALAPPDATA = path.resolve(profileHome, "AppData", "Local");
  } else if (process.platform === "linux") {
    expected.XDG_DATA_HOME = path.resolve(profileHome, ".local", "share");
  }
  const expectedByName = new Map(Object.entries(expected)
    .map(([name, value]) => [environmentNameKey(name), value]));
  const profileEnvironment = launchProfile?.environment;
  const environmentMatches = profileEnvironment != null
    && Object.entries(profileEnvironment).every(([name, value]) => {
      const requiredValue = expectedByName.get(environmentNameKey(name));
      return requiredValue == null || value === requiredValue;
    })
    && Object.entries(expected).every(([name, value]) => profileEnvironment[name] === value);
  if (launchProfile?.mode !== "isolated_local_profile"
      || launchProfile?.isolation_status !== "source_backed_experimental"
      || !/^[a-z0-9][a-z0-9._-]{0,63}$/u.test(launchProfile?.profile_id ?? "")
      || typeof launchProfile?.generation_id !== "string"
      || launchProfile.generation_id.length < 1
      || launchProfile?.steam !== "disabled_before_platform_initialization"
      || JSON.stringify(launchProfile?.args) !== JSON.stringify(["--force-steam=off", "--clientId=1"])
      || !environmentMatches) {
    throw new Error("Native-window Host launches require an exact Host-owned isolated profile.");
  }
  return expected;
}

function removeEnvironmentNames(environment, names) {
  const normalized = new Set(names.map(environmentNameKey));
  for (const name of Object.keys(environment)) {
    if (normalized.has(environmentNameKey(name))) delete environment[name];
  }
}

function validateComposedNativeEnvironment(environment, expectedProfileEnvironment, expectedLifecycleEnvironment) {
  const expectedValues = {
    ...expectedProfileEnvironment,
    ...expectedLifecycleEnvironment
  };
  const protectedNames = new Set([
    ...Object.keys(expectedProfileEnvironment),
    ...NATIVE_LIFECYCLE_ENVIRONMENT_NAMES
  ].map(environmentNameKey));
  const expected = new Map(Object.entries(expectedValues)
    .map(([name, value]) => [environmentNameKey(name), value]));
  for (const [name, value] of Object.entries(environment)) {
    const normalizedName = environmentNameKey(name);
    if (!protectedNames.has(normalizedName)) continue;
    const required = expected.get(environmentNameKey(name));
    if (required == null) {
      throw new Error(`Native-window launch environment added protected lifecycle variable ${name}.`);
    }
    if (required != null && value !== required) {
      throw new Error(`Native-window launch environment overrides isolated profile path ${name}.`);
    }
  }
  for (const [name, value] of Object.entries(expectedValues)) {
    if (environment[name] !== value) {
      throw new Error(`Native-window launch environment lost Host-owned value ${name}.`);
    }
  }
}

export async function readJson(endpoint, route, timeoutMs = 2500) {
  try {
    const response = await fetch(`${endpoint.replace(/\/$/u, "")}${route}`, {
      signal: AbortSignal.timeout(timeoutMs)
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return { ok: true, value: await response.json(), error: null };
  } catch (error) {
    return { ok: false, value: null, error: error instanceof Error ? error.message : String(error) };
  }
}

export async function waitForEndpoint(endpoint, timeoutMs, child) {
  const started = Date.now();
  let last = null;
  while (Date.now() - started < timeoutMs) {
    last = await readJson(endpoint, "/api/player-environment/capabilities");
    if (last.ok) return last;
    if (child.exitCode != null || child.signalCode != null) return last;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return last ?? { ok: false, value: null, error: "timeout" };
}

export function snapshotIsInteractive(result) {
  return result.ok
    && result.value?.status === "interactive"
    && result.value?.bound_actions?.status === "complete"
    && result.value?.bound_actions?.actions?.length > 0;
}

export async function waitForInteractiveSnapshot(endpoint, timeoutMs, child) {
  const started = Date.now();
  const observed = [];
  let lastFingerprint = null;
  while (Date.now() - started < timeoutMs) {
    const current = await readJson(endpoint, "/api/player-environment/snapshot");
    const fingerprint = current.ok
      ? `${current.value?.snapshot_id}:${current.value?.status}:${current.value?.interaction?.kind}`
      : `error:${current.error}`;
    if (fingerprint !== lastFingerprint) {
      observed.push(current);
      lastFingerprint = fingerprint;
    }
    if (snapshotIsInteractive(current)) return observed;
    if (child.exitCode != null || child.signalCode != null) return observed;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return observed;
}

async function waitForSuccessor(client, previousInteractionId, timeoutMs, child) {
  const started = Date.now();
  let latest = null;
  while (Date.now() - started < timeoutMs) {
    latest = (await client.observe()).data;
    if (latest.status === "interactive"
        && latest.interaction.interaction_id !== previousInteractionId) {
      return latest;
    }
    if (child.exitCode != null || child.signalCode != null) return latest;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return latest;
}

async function exerciseMenuControl(endpoint, child, timeoutMs, productVersion) {
  const client = new PlayerEnvironmentRestClient(endpoint, 5_000);
  const capabilities = (await client.capabilities()).data;
  const initialSnapshot = (await client.observe()).data;
  if (initialSnapshot.interaction.kind !== "main_menu") {
    throw new Error(`Expected main_menu, observed ${initialSnapshot.interaction.kind}.`);
  }
  if (initialSnapshot.bound_actions.status !== "complete"
      || initialSnapshot.bound_actions.actions.length !== 1) {
    throw new Error("The main-menu control gate requires one complete advertised action.");
  }

  const action = initialSnapshot.bound_actions.actions[0];
  const session = new EnvironmentControllerSession(client, {
    productId: "sts2-headless-probe",
    productName: "STS2 Headless Probe",
    productVersion
  });
  await session.register(capabilities.host, capabilities.control);
  try {
    const credentials = await session.credentials();
    const request = {
      requestId: `headless-menu-${randomUUID()}`,
      expectedSnapshotId: initialSnapshot.snapshot_id,
      boundActionId: action.bound_action_id,
      clientSessionId: credentials.clientSessionId,
      controllerLeaseId: credentials.controllerLeaseId,
      controllerGeneration: credentials.controllerGeneration
    };
    const receipt = (await client.submit(request)).data;
    const duplicateReceipt = (await client.submit(request)).data;
    const successorSnapshot = await waitForSuccessor(
      client,
      initialSnapshot.interaction.interaction_id,
      timeoutMs,
      child
    );
    const staleReceipt = (await client.submit({
      ...request,
      requestId: `headless-stale-${randomUUID()}`
    })).data;
    return {
      strict_sdk_protocol: capabilities.protocol_version,
      action,
      initial_snapshot: initialSnapshot,
      receipt,
      duplicate_receipt: duplicateReceipt,
      stale_receipt: staleReceipt,
      successor_snapshot: successorSnapshot,
      verdict: evaluateMenuControlGate({
        initialSnapshot,
        receipt,
        duplicateReceipt,
        staleReceipt,
        successorSnapshot
      })
    };
  } finally {
    await session.close();
  }
}

export async function waitForExit(child, timeoutMs) {
  if (child.exitCode != null || child.signalCode != null) {
    return { code: child.exitCode, signal: child.signalCode };
  }
  return await Promise.race([
    new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal }))),
    new Promise((resolve) => setTimeout(() => resolve(null), timeoutMs))
  ]);
}

export async function requestHostShutdown({
  endpoint,
  hostControlToken,
  expectedRuntimeInstanceId,
  timeoutMs = 5_000
}) {
  const result = await requestHostControl({
    endpoint,
    hostControlToken,
    expectedRuntimeInstanceId,
    route: "/api/host-control/shutdown",
    timeoutMs
  });
  if (result.status === "response") {
    return {
      ...result,
      status: result.response?.status === "shutdown_requested" ? "requested" : "rejected"
    };
  }
  return result;
}

async function requestHostControl({
  endpoint,
  hostControlToken,
  expectedRuntimeInstanceId,
  route,
  timeoutMs
}) {
  if (!endpoint || !hostControlToken || !expectedRuntimeInstanceId) {
    return { status: "unavailable", response: null, error: "host_control_not_configured" };
  }
  try {
    const response = await fetch(`${endpoint.replace(/\/$/u, "")}${route}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        expected_runtime_instance_id: expectedRuntimeInstanceId,
        host_control_token: hostControlToken
      }),
      signal: AbortSignal.timeout(timeoutMs)
    });
    const body = await response.json();
    return {
      status: response.ok ? "response" : "rejected",
      http_status: response.status,
      response: body,
      error: response.ok ? null : body?.error?.code ?? `HTTP ${response.status}`
    };
  } catch (error) {
    return {
      status: "transport_error",
      response: null,
      error: error instanceof Error ? error.message : String(error)
    };
  }
}

export async function requestHostProvenance({
  endpoint,
  hostControlToken,
  expectedRuntimeInstanceId,
  timeoutMs = 5_000
}) {
  const result = await requestHostControl({
    endpoint,
    hostControlToken,
    expectedRuntimeInstanceId,
    route: "/api/host-control/provenance",
    timeoutMs
  });
  if (result.status === "response") return { ...result, status: "observed" };
  return result;
}

export async function stopChild(child, {
  endpoint = null,
  hostControlToken = null,
  expectedRuntimeInstanceId = null,
  beforeSignal = () => true
} = {}) {
  const hostShutdown = await requestHostShutdown({
    endpoint,
    hostControlToken,
    expectedRuntimeInstanceId
  });
  if (hostShutdown.status === "requested" || hostShutdown.status === "transport_error") {
    const gracefulExit = await waitForExit(child, 10_000);
    if (gracefulExit != null) return { ...gracefulExit, host_shutdown: hostShutdown, forced: false };
  }
  if (!beforeSignal("SIGINT")) return null;
  child.kill("SIGINT");
  let exit = await waitForExit(child, 5_000);
  if (exit != null) return { ...exit, host_shutdown: hostShutdown, forced: true };
  if (!beforeSignal("SIGTERM")) return null;
  child.kill("SIGTERM");
  exit = await waitForExit(child, 5_000);
  if (exit != null) return { ...exit, host_shutdown: hostShutdown, forced: true };
  if (!beforeSignal("SIGKILL")) return null;
  child.kill("SIGKILL");
  exit = await waitForExit(child, 3_000);
  return exit == null ? null : { ...exit, host_shutdown: hostShutdown, forced: true };
}

function safeTimestamp() {
  return new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-");
}

export function shippedRuntimeLaunch(installation, {
  stdout = "pipe",
  stderr = "pipe",
  extraEnvironment = {},
  launchProfile = null,
  displayMode = "headless",
  connectorEndpoint = null,
  runSeed = null,
  connectorCanary = null,
  hostExecutionProfile = null,
  spawnProcess = spawn
} = {}) {
  validateHostDisplayMode(displayMode);
  const expectedProfileEnvironment = displayMode === "native_window"
    ? nativeWindowProfileEnvironment(launchProfile)
    : null;
  if (expectedProfileEnvironment != null) {
    const protectedNames = new Set([
      ...Object.keys(expectedProfileEnvironment),
      ...NATIVE_LIFECYCLE_ENVIRONMENT_NAMES
    ].map(environmentNameKey));
    const extraProfileOverride = Object.keys(extraEnvironment)
      .find((name) => protectedNames.has(environmentNameKey(name)));
    if (extraProfileOverride != null) {
      throw new Error(`Extra environment cannot override protected native Host variable ${extraProfileOverride}.`);
    }
  }
  const requestedHostExecutionProfile = validateRequestedHostExecutionProfile(hostExecutionProfile);
  const connector = connectorEndpoint == null
    ? null
    : resolveConnectorEndpoint(connectorEndpoint);
  const hostControlToken = connector == null ? null : randomBytes(32).toString("hex");
  const args = [
    ...(displayMode === "headless" ? ["--headless"] : []),
    "--verbose",
    ...(launchProfile?.args ?? [])
  ];
  const inheritedEnvironment = { ...process.env };
  if (expectedProfileEnvironment != null) {
    removeEnvironmentNames(inheritedEnvironment, [
      ...Object.keys(expectedProfileEnvironment),
      ...NATIVE_LIFECYCLE_ENVIRONMENT_NAMES
    ]);
  }
  const expectedLifecycleEnvironment = {
    ...(connector?.process_environment ?? {}),
    ...(hostControlToken == null ? {} : { [HOST_CONTROL_TOKEN_ENVIRONMENT_VARIABLE]: hostControlToken }),
    ...(runSeed == null ? {} : { STS2_CONNECTOR_RUN_SEED: runSeed }),
    ...(requestedHostExecutionProfile == null
      ? {}
      : { STS2_CONNECTOR_HOST_EXECUTION_PROFILE: requestedHostExecutionProfile }),
    ...(connectorCanary?.game_id == null ? {} : { [GAME_CANARY_ENVIRONMENT_VARIABLE]: connectorCanary.game_id }),
    ...(connectorCanary?.source_revision == null
      ? {}
      : { [SOURCE_CANARY_ENVIRONMENT_VARIABLE]: connectorCanary.source_revision })
  };
  const environment = withExplicitConnectorCanary({
    ...inheritedEnvironment,
    SteamAppId: process.env.SteamAppId ?? STS2_APP_ID,
    SteamGameId: process.env.SteamGameId ?? STS2_APP_ID,
    ...(launchProfile?.environment ?? {}),
    ...(connector?.process_environment ?? {}),
    ...(hostControlToken == null
      ? {}
      : { [HOST_CONTROL_TOKEN_ENVIRONMENT_VARIABLE]: hostControlToken }),
    ...(runSeed == null ? {} : { STS2_CONNECTOR_RUN_SEED: runSeed }),
    ...(requestedHostExecutionProfile == null
      ? {}
      : { STS2_CONNECTOR_HOST_EXECUTION_PROFILE: requestedHostExecutionProfile }),
    ...extraEnvironment
  }, connectorCanary);
  if (launchProfile?.steam === "disabled_before_platform_initialization") {
    if (expectedProfileEnvironment != null) {
      removeEnvironmentNames(environment, ["SteamAppId", "SteamGameId"]);
    } else {
      delete environment.SteamAppId;
      delete environment.SteamGameId;
    }
  }
  if (expectedProfileEnvironment != null) {
    validateComposedNativeEnvironment(environment, expectedProfileEnvironment, expectedLifecycleEnvironment);
  }
  const child = spawnProcess(installation.executable, args, {
    cwd: installation.executable_cwd,
    env: environment,
    stdio: ["ignore", stdout, stderr]
  });
  return {
    child,
    args,
    environment,
    connector,
    hostControlToken,
    hostConfiguration: {
      ...(displayMode === "headless"
        ? { display_driver: "headless", audio_driver: "Dummy" }
        : { requested_display_mode: displayMode }),
      scene_thread_mode: "default",
      authority_profile: connectorCanary == null
        ? "sealed_only"
        : "exact_process_local_canary",
      canary_game_id: connectorCanary?.game_id ?? null,
      canary_source_revision: connectorCanary?.source_revision ?? null,
      requested_execution_profile: requestedHostExecutionProfile
    }
  };
}

export async function runShippedProbe({
  installation,
  localRoot,
  endpoint = "http://127.0.0.1:15526",
  timeoutMs = 90_000,
  evidenceRoot,
  exerciseMenu = false,
  sharedProfileAcknowledged = false,
  isolatedProfileId = null,
  experimentalBuildAcknowledged = false
}) {
  const launchProfile = resolveLaunchProfile({
    localRoot,
    isolatedProfileId,
    sharedProfileAcknowledged
  });
  const existing = listGameProcesses();
  if (existing.length > 0) {
    throw new Error(`Refusing to launch beside an existing STS2 process:\n${existing.join("\n")}`);
  }
  if (!existsSync(installation.executable)) {
    throw new Error(`Game executable not found: ${installation.executable}`);
  }

  const diskIdentityBefore = readDiskIdentity(installation);
  const compatibility = evaluateRuntimeCompatibility(diskIdentityBefore);
  const headlessIdentity = readProjectIdentity();
  if (compatibility.status !== "supported_exact" && !experimentalBuildAcknowledged) {
    throw new Error(
      `Unsupported STS2 runtime (${compatibility.mismatches.join(", ")}); `
      + "pass --experimental-build only to collect non-support evidence."
    );
  }
  const connectorCanary = resolveExperimentalConnectorCanary({
    installation,
    compatibility,
    acknowledged: experimentalBuildAcknowledged
  });

  const evidenceDir = path.join(evidenceRoot, `shipped-h0-${safeTimestamp()}`);
  mkdirSync(evidenceDir, { recursive: true });
  const stdoutFile = path.join(evidenceDir, "stdout.log");
  const stderrFile = path.join(evidenceDir, "stderr.log");
  const reportFile = path.join(evidenceDir, "report.json");
  const beforeLog = installation.log_file && existsSync(installation.log_file)
    ? readFileSync(installation.log_file, "utf8")
    : null;
  if (beforeLog != null) writeFileSync(path.join(evidenceDir, "godot.before.log"), beforeLog);

  const endpointBefore = await readJson(endpoint, "/api/player-environment/capabilities", 1000);
  const { child, args, connector, hostControlToken } = shippedRuntimeLaunch(installation, {
    launchProfile,
    connectorEndpoint: endpoint,
    connectorCanary
  });
  const stdoutStream = createWriteStream(stdoutFile);
  const stderrStream = createWriteStream(stderrFile);
  child.stdout.pipe(stdoutStream);
  child.stderr.pipe(stderrStream);
  const capabilitiesResult = await waitForEndpoint(endpoint, timeoutMs, child);
  let snapshots = [];
  if (capabilitiesResult.ok) {
    snapshots = await waitForInteractiveSnapshot(endpoint, timeoutMs, child);
  }
  let controlGate = null;
  if (exerciseMenu && snapshotIsInteractive(snapshots.at(-1))) {
    controlGate = await exerciseMenuControl(endpoint, child, timeoutMs, headlessIdentity.version);
  }

  const processExit = await stopChild(child, {
    endpoint,
    hostControlToken,
    expectedRuntimeInstanceId: capabilitiesResult.value?.host?.runtime_instance_id ?? null
  });
  await Promise.allSettled([finished(stdoutStream), finished(stderrStream)]);

  const stdout = existsSync(stdoutFile) ? readFileSync(stdoutFile, "utf8") : "";
  const stderr = existsSync(stderrFile) ? readFileSync(stderrFile, "utf8") : "";
  if (installation.log_file && existsSync(installation.log_file)) {
    writeFileSync(path.join(evidenceDir, "godot.after.log"), readFileSync(installation.log_file, "utf8"));
  }
  const verdict = evaluateShippedProbe({
    endpointWasClear: !endpointBefore.ok,
    processStarted: child.pid != null,
    processExit,
    capabilities: capabilitiesResult.ok ? capabilitiesResult.value : null,
    snapshots,
    stdout,
    stderr
  });
  const report = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    headless: headlessIdentity,
    command: {
      executable: installation.executable,
      args,
      connector,
      authority_canary: connectorCanary,
      steam_app_environment: launchProfile.steam === "enabled"
        ? { SteamAppId: STS2_APP_ID, SteamGameId: STS2_APP_ID }
        : null
    },
    profile: publicProfileDescriptor(launchProfile),
    disk_identity_before: diskIdentityBefore,
    disk_identity_after: readDiskIdentity(installation),
    compatibility,
    evidence_mode: compatibility.status === "supported_exact" ? "supported" : "experimental",
    endpoint_before: endpointBefore,
    endpoint_after_launch: capabilitiesResult,
    snapshots,
    control_gate: controlGate,
    verdict
  };
  writeFileSync(reportFile, `${JSON.stringify(report, null, 2)}\n`);
  return { report, evidenceDir, reportFile };
}
