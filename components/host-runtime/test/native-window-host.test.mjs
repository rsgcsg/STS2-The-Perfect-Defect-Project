import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import { finished } from "node:stream/promises";
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL } from "@rsgcsg/sts2-connector-client";
import {
  commandOwnsHeadlessRuntime,
  commandOwnsRuntime,
  evaluateHeadlessCapabilities,
  evaluateHostCapabilities,
  inspectRecordedProcess,
  processCommand,
  processCommandResult,
  queryHeadlessStatus,
  runHeadlessHost,
  stopHeadlessHost
} from "../src/headless-host.mjs";
import { shippedRuntimeLaunch, stopChild } from "../src/runtime-probe.mjs";
import { SOURCE_CANARY_ENVIRONMENT_VARIABLE } from "../src/connector-endpoint.mjs";

function temporaryDirectory(prefix) {
  return mkdtempSync(path.join(os.tmpdir(), prefix));
}

function fakeChild(pid = 12001) {
  const child = new EventEmitter();
  child.pid = pid;
  child.exitCode = null;
  child.signalCode = null;
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.pipeDestinations = new Set();
  for (const source of [child.stdout, child.stderr]) {
    const pipe = source.pipe.bind(source);
    source.pipe = (destination, ...args) => {
      child.pipeDestinations.add(destination);
      return pipe(destination, ...args);
    };
  }
  child.kill = (signal) => {
    child.signalCode = signal;
    child.emit("exit", null, signal);
    return true;
  };
  return child;
}

async function waitForFakeChildPipeDestinations(child) {
  await Promise.all([...child.pipeDestinations].map((destination) => finished(destination)));
}

async function closeFakeChildOutput(child) {
  child.stdout.destroy();
  child.stderr.destroy();
  await waitForFakeChildPipeDestinations(child);
  assert.ok([...child.pipeDestinations].every((destination) => destination.closed));
}

test("fake child cleanup waits for actual pipe destination completion", async () => {
  const child = fakeChild();
  const destination = new Writable({ write: (_chunk, _encoding, callback) => callback() });
  child.stdout.pipe(destination);
  let completed = false;
  const waiting = waitForFakeChildPipeDestinations(child).then(() => { completed = true; });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(completed, false);
  destination.end();
  await waiting;
  assert.equal(destination.closed, true);
  child.stdout.destroy();
  child.stderr.destroy();
});

function exactCapabilities({
  hostKind = "live_ui",
  executionAvailable = true,
  implementation = {}
} = {}) {
  return {
    protocol_version: SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
    execution_available: executionAvailable,
    host: {
      host_kind: hostKind,
      runtime_instance_id: "runtime-native-1",
      implementation: {
        source_revision: "a".repeat(40),
        artifact_sha256: "b".repeat(64),
        module_version_id: "12345678-1234-1234-1234-123456789abc",
        ...implementation
      }
    },
    game: { modset: { status: "exact_platform_modset" } }
  };
}

function isolatedProfile() {
  const profileRoot = path.join(os.tmpdir(), "native-test");
  const home = path.join(profileRoot, "home");
  const environment = { HOME: home, USERPROFILE: home };
  let expectedUserDataRoot;
  if (process.platform === "win32") {
    environment.APPDATA = path.join(home, "AppData", "Roaming");
    environment.LOCALAPPDATA = path.join(home, "AppData", "Local");
    expectedUserDataRoot = path.join(environment.APPDATA, "SlayTheSpire2");
  } else if (process.platform === "linux") {
    environment.XDG_DATA_HOME = path.join(home, ".local", "share");
    expectedUserDataRoot = path.join(environment.XDG_DATA_HOME, "SlayTheSpire2");
  } else {
    expectedUserDataRoot = path.join(home, "Library", "Application Support", "SlayTheSpire2");
  }
  return {
    mode: "isolated_local_profile",
    isolation_status: "source_backed_experimental",
    profile_id: "native-test",
    generation_id: "profile-generation-1",
    profile_root: profileRoot,
    expected_user_data_root: expectedUserDataRoot,
    steam: "disabled_before_platform_initialization",
    client_id: "1",
    args: ["--force-steam=off", "--clientId=1"],
    environment
  };
}

function nativeStartFixture(prefix, { sourceRevision = "a".repeat(40) } = {}) {
  const root = temporaryDirectory(prefix);
  const modsDir = path.join(root, "mods");
  mkdirSync(modsDir, { recursive: true });
  const dll = path.join(modsDir, "STS2_PLATFORM.dll");
  const artifact = Buffer.from("verified native Connector test artifact");
  writeFileSync(dll, artifact);
  const artifactSha = createHash("sha256").update(artifact).digest("hex");
  const identity = {
    source_revision: sourceRevision,
    artifact_sha256: artifactSha,
    artifact_mvid: "12345678-1234-1234-1234-123456789abc"
  };
  writeFileSync(path.join(modsDir, "STS2_PLATFORM.identity"), `${JSON.stringify(identity)}\n`);
  return {
    root,
    localRoot: path.join(root, "host"),
    installation: {
      executable: "/game/Slay the Spire 2",
      executable_cwd: "/game",
      mods_dir: modsDir
    },
    identity,
    artifactSha
  };
}

function nativeStartDependencies(fake, capabilities, observed) {
  observed.provenanceCalls = 0;
  return {
    getDiskIdentity: () => ({ exact: true }),
    requireSupported: () => ({ status: "supported_exact" }),
    enumerateGameProcesses: () => [],
    readEndpoint: async () => ({ ok: false, error: "endpoint_clear" }),
    snapshotSharedProfile: () => ({ root: "/shared", present: true, tree_sha256: "stable" }),
    compareSnapshots: (before, after) => ({ unchanged: before.tree_sha256 === after.tree_sha256, before, after }),
    launchRuntime: (installation, options) => {
      observed.launchOptions = options;
      const launch = shippedRuntimeLaunch(installation, {
        ...options,
        spawnProcess: (_executable, _args, spawnOptions) => {
          observed.spawnEnvironment = spawnOptions.env;
          return fake;
        }
      });
      observed.launch = launch;
      return launch;
    },
    getProcessStartedAt: () => "2026-10-01T00:00:00.000Z",
    waitEndpoint: async () => ({ ok: true, value: capabilities }),
    requestProvenance: async ({ expectedRuntimeInstanceId }) => {
      observed.provenanceCalls += 1;
      return {
        status: "observed",
        response: { runtime_instance_id: expectedRuntimeInstanceId }
      };
    },
    waitSnapshot: async () => [{ ok: true, value: {
      status: "interactive",
      snapshot_id: "snapshot-native-start",
      interaction: { interaction_id: "interaction-native-start", kind: "main_menu" },
      bound_actions: { status: "complete", actions: [{ bound_action_id: "native-start-action" }] }
    } }],
    waitChildExit: async () => {
      fake.exitCode = 0;
      fake.emit("exit", 0, null);
      return { code: 0, signal: null };
    },
    stopRuntimeChild: async (child) => {
      child.exitCode = 1;
      child.emit("exit", 1, null);
      return { code: 1, signal: null };
    },
    installSignalHandlers: false,
    projectIdentity: () => ({ version: "test" })
  };
}

function nativeLifecycleRecord(fixture) {
  const runtimeRoot = path.join(fixture.localRoot, "runtime");
  const sessions = readdirSync(runtimeRoot).filter((name) => name.startsWith("session-"));
  assert.equal(sessions.length, 1);
  return JSON.parse(readFileSync(path.join(runtimeRoot, sessions[0], "lifecycle.json"), "utf8"));
}

test("Headless launch remains the default and native-window launch uses the isolated profile", () => {
  const calls = [];
  const launch = shippedRuntimeLaunch({ executable: "/game/Slay the Spire 2", executable_cwd: "/game" }, {
    spawnProcess: (...args) => {
      calls.push(args);
      return fakeChild();
    }
  });
  assert.deepEqual(launch.args, ["--headless", "--verbose"]);
  assert.equal(launch.hostConfiguration.display_driver, "headless");
  assert.equal(launch.hostConfiguration.audio_driver, "Dummy");

  const profile = isolatedProfile();
  const visible = shippedRuntimeLaunch({ executable: "/game/Slay the Spire 2", executable_cwd: "/game" }, {
    displayMode: "native_window",
    launchProfile: profile,
    connectorEndpoint: "http://127.0.0.1:15526",
    spawnProcess: (...args) => {
      calls.push(args);
      return fakeChild(12002);
    }
  });
  assert.deepEqual(visible.args, ["--verbose", "--force-steam=off", "--clientId=1"]);
  assert.equal(visible.hostConfiguration.requested_display_mode, "native_window");
  assert.equal("display_driver" in visible.hostConfiguration, false);
  assert.equal("audio_driver" in visible.hostConfiguration, false);
  assert.equal(calls[1][2].env.HOME, profile.environment.HOME);
  assert.equal(calls[1][2].env.USERPROFILE, profile.environment.USERPROFILE);
  assert.equal(calls[1][2].env.STS2_CONNECTOR_PORT, "15526");
  assert.equal(calls[1][2].env.STS2_CONNECTOR_HOST_CONTROL_TOKEN, visible.hostControlToken);
  assert.equal(calls[1][2].cwd, "/game");
  assert.throws(() => shippedRuntimeLaunch({ executable: "/game", executable_cwd: "/game" }, {
    displayMode: "native_window",
    launchProfile: { mode: "shared_steam_profile", args: [], environment: {} },
    spawnProcess: () => assert.fail("shared profile must be rejected before spawn")
  }), /Host-owned isolated profile/u);
  assert.throws(() => shippedRuntimeLaunch({ executable: "/game", executable_cwd: "/game" }, {
    displayMode: "native_window",
    launchProfile: { ...profile, environment: { HOME: "/wrong", USERPROFILE: "/wrong" } },
    spawnProcess: () => assert.fail("forged profile must be rejected before spawn")
  }), /exact Host-owned isolated profile/u);
  let spawnCount = 0;
  assert.throws(() => shippedRuntimeLaunch({ executable: "/game", executable_cwd: "/game" }, {
    displayMode: "native_window",
    launchProfile: profile,
    extraEnvironment: { HOME: "/normal/home" },
    spawnProcess: () => { spawnCount += 1; return fakeChild(); }
  }), /cannot override protected native Host variable HOME/u);
  assert.throws(() => shippedRuntimeLaunch({ executable: "/game", executable_cwd: "/game" }, {
    displayMode: "native_window",
    launchProfile: profile,
    connectorEndpoint: "http://127.0.0.1:15526",
    extraEnvironment: { STS2_CONNECTOR_HOST_CONTROL_TOKEN: "forged" },
    spawnProcess: () => { spawnCount += 1; return fakeChild(); }
  }), /protected native Host variable STS2_CONNECTOR_HOST_CONTROL_TOKEN/u);
  assert.throws(() => shippedRuntimeLaunch({ executable: "/game", executable_cwd: "/game" }, {
    displayMode: "native_window",
    launchProfile: profile,
    connectorEndpoint: "http://127.0.0.1:15526",
    extraEnvironment: { STS2_CONNECTOR_PORT: "15527" },
    spawnProcess: () => { spawnCount += 1; return fakeChild(); }
  }), /protected native Host variable STS2_CONNECTOR_PORT/u);
  assert.throws(() => shippedRuntimeLaunch({ executable: "/game", executable_cwd: "/game" }, {
    displayMode: "native_window",
    launchProfile: profile,
    extraEnvironment: { SteamAppId: "646570" },
    spawnProcess: () => { spawnCount += 1; return fakeChild(); }
  }), /protected native Host variable SteamAppId/u);
  assert.equal(spawnCount, 0);
});

test("Windows process lookup distinguishes explicit absence from query failure or unknown command", () => {
  const invoke = (result) => processCommandResult(7310, "win32", {
    spawnProcess: (_command, args) => {
      assert.equal(args[0], "-NoProfile");
      assert.match(args[3], /-ErrorAction Stop/u);
      assert.match(args[3], /HOST_STATUS=ABSENT/u);
      return result;
    }
  });
  assert.deepEqual(invoke({ status: 0, stdout: "HOST_STATUS=ABSENT\n" }), {
    status: "absent",
    command: null
  });
  assert.deepEqual(invoke({ status: 0, stdout: "", stderr: "Access is denied" }), {
    status: "unknown",
    command: null
  });
  assert.deepEqual(invoke(null), { status: "unknown", command: null });
  assert.deepEqual(invoke({ status: 0, stdout: "HOST_STATUS=ABSENT\n", stderr: "Access is denied" }), {
    status: "unknown",
    command: null
  });
  assert.deepEqual(invoke({ status: 2, stdout: "", stderr: "Access is denied" }), {
    status: "unknown",
    command: null
  });
  assert.deepEqual(invoke({ status: 0, stdout: "HOST_STATUS=UNKNOWN\n" }), {
    status: "unknown",
    command: null
  });
  assert.deepEqual(invoke({ status: 0, stdout: "HOST_STATUS=ABSENT\nunexpected\n" }), {
    status: "unknown",
    command: null
  });
  assert.deepEqual(invoke({ status: 0, stdout: "HOST_STATUS=OBSERVED\n/game/game.exe --verbose\n" }), {
    status: "observed",
    command: "/game/game.exe --verbose"
  });
});

test("Headless admission stays stable while visible mode requires live_ui and exact installed identity", () => {
  const headless = {
    protocol_version: SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
    execution_available: true,
    host: { host_kind: "headless" },
    game: { modset: { status: "exact_player_environment_only" } }
  };
  assert.deepEqual(evaluateHeadlessCapabilities(headless), { ok: true, errors: [] });
  const installed = {
    status: "verified",
    installed_sha256: "b".repeat(64),
    identity: { source_revision: "a".repeat(40), artifact_mvid: "12345678-1234-1234-1234-123456789abc" }
  };
  assert.deepEqual(evaluateHostCapabilities(exactCapabilities(), "native_window",
    SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL, installed), {
    ok: true,
    errors: [],
    expected_host_kind: "live_ui"
  });
  assert.ok(evaluateHostCapabilities(exactCapabilities({ hostKind: "headless" }), "native_window",
    SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL, installed).errors.includes("host_kind_mismatch"));
  assert.ok(evaluateHostCapabilities({
    ...exactCapabilities(),
    host: { ...exactCapabilities().host, implementation: { artifact_sha256: "wrong" } }
  }, "native_window", SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL, installed).errors.includes("loaded_connector_sha_mismatch"));
});

test("recorded process ownership requires exact executable, launch arguments, and process generation", () => {
  const record = {
    pid: 321,
    executable: "/games/Slay the Spire 2",
    args: ["--verbose", "--force-steam=off", "--clientId=1"],
    display_mode: "native_window",
    process_started_at: "2026-10-01T00:00:00.000Z",
    profile: { mode: "isolated_local_profile" }
  };
  const command = '"/games/Slay the Spire 2" --verbose --force-steam=off --clientId=1';
  assert.equal(commandOwnsRuntime(command, record.executable, record.args), true);
  assert.equal(commandOwnsHeadlessRuntime(command, record.executable), false);
  assert.equal(commandOwnsRuntime(`${command} --unowned`, record.executable, record.args), false);
  assert.deepEqual(inspectRecordedProcess(record, {
    getProcessCommand: () => command,
    getProcessStartedAt: () => record.process_started_at
  }).matches, true);
  assert.equal(inspectRecordedProcess(record, {
    getProcessCommand: () => command,
    getProcessStartedAt: () => "2026-10-01T00:00:01.000Z"
  }).reason, "process_generation_mismatch");
  assert.equal(inspectRecordedProcess({ ...record, process_started_at: null }, {
    getProcessCommand: () => command,
    getProcessStartedAt: () => record.process_started_at
  }).reason, "recorded_process_generation_missing");
  assert.equal(processCommand(0), null);
});

test("child fallback refuses a signal when its owner-generation check fails", async () => {
  const child = fakeChild(3210);
  let checks = 0;
  try {
    const result = await stopChild(child, {
      beforeSignal: (signal) => {
        checks += 1;
        assert.equal(signal, "SIGINT");
        return false;
      }
    });
    assert.equal(result, null);
    assert.equal(checks, 1);
    assert.equal(child.signalCode, null);
  } finally {
    child.stdout.destroy();
    child.stderr.destroy();
  }
});

function writeOwnedRecord(localRoot, { displayMode = "native_window", startedAt = "2026-10-01T00:00:00.000Z" } = {}) {
  const runtimeRoot = path.join(localRoot, "runtime");
  const sessionDirectory = path.join(runtimeRoot, "session-test");
  mkdirSync(sessionDirectory, { recursive: true });
  const record = {
    schema_version: 2,
    status: "ready",
    pid: 7001,
    executable: "/game/Slay the Spire 2",
    args: displayMode === "native_window"
      ? ["--verbose", "--force-steam=off", "--clientId=1"]
      : ["--headless", "--verbose", "--force-steam=off", "--clientId=1"],
    display_mode: displayMode,
    process_started_at: startedAt,
    profile: { mode: "isolated_local_profile" },
    session_directory: sessionDirectory,
    loaded_identity: { host: { runtime_instance_id: "runtime-native-1" } },
    shared_profile_sentinel_before: {
      root: "/shared",
      present: true,
      file_count: 1,
      directory_count: 1,
      symlink_count: 0,
      total_file_bytes: 4,
      tree_sha256: "before"
    }
  };
  writeFileSync(path.join(runtimeRoot, "current.json"), JSON.stringify(record));
  writeFileSync(path.join(runtimeRoot, "current-control.json"), JSON.stringify({
    pid: record.pid,
    session_directory: sessionDirectory,
    host_control_token: "secret-test-only"
  }));
  return { record, runtimeRoot, sessionDirectory };
}

const ownedCommand = '"/game/Slay the Spire 2" --verbose --force-steam=off --clientId=1';

function visibleSignalDependencies({ child, signalSource, signalAt = null, stopBehavior = "null", onReady = () => {} }) {
  const stamp = "2026-10-01T00:00:00.000Z";
  const maybeSignal = (stage) => {
    if (signalAt === stage) signalSource.emit("SIGINT");
  };
  let stopCalls = 0;
  const dependencies = {
    getDiskIdentity: () => ({}),
    requireSupported: () => ({ status: "supported_exact" }),
    getInstalledIdentity: () => ({
      status: "verified",
      installed_sha256: "b".repeat(64),
      identity: { source_revision: "a".repeat(40), artifact_mvid: "12345678-1234-1234-1234-123456789abc" }
    }),
    enumerateGameProcesses: () => [],
    readEndpoint: async () => ({ ok: false, error: "endpoint_clear" }),
    snapshotSharedProfile: () => ({ root: "/shared", present: true, tree_sha256: "stable" }),
    compareSnapshots: () => ({ unchanged: true }),
    launchRuntime: () => ({
      child,
      args: ["--verbose", "--force-steam=off", "--clientId=1"],
      connector: { endpoint: "http://127.0.0.1:15526" },
      hostControlToken: "test-only"
    }),
    getProcessCommand: () => ownedCommand,
    getProcessStartedAt: () => stamp,
    waitEndpoint: async () => {
      maybeSignal("endpoint");
      return { ok: true, value: exactCapabilities() };
    },
    requestProvenance: async ({ expectedRuntimeInstanceId }) => {
      maybeSignal("provenance");
      return { status: "observed", response: { runtime_instance_id: expectedRuntimeInstanceId } };
    },
    waitSnapshot: async () => {
      maybeSignal("snapshot");
      return [{ ok: true, value: {
        status: "interactive",
        snapshot_id: "signal-snapshot",
        interaction: { interaction_id: "signal-interaction", kind: "main_menu" },
        bound_actions: { status: "complete", actions: [{ bound_action_id: "signal-action" }] }
      } }];
    },
    waitChildExit: (ownedChild) => {
      onReady();
      return new Promise((resolve) => ownedChild.once("exit", (code, signal) => resolve({ code, signal })));
    },
    stopRuntimeChild: async () => {
      stopCalls += 1;
      if (stopBehavior === "reject") throw new Error("synthetic stop failure");
      if (stopBehavior === "never") return new Promise(() => {});
      if (stopBehavior === "close") {
        child.exitCode = 0;
        child.emit("exit", 0, null);
        return { code: 0, signal: null };
      }
      return null;
    },
    signalSource,
    installSignalHandlers: true,
    projectIdentity: () => ({ version: "test" })
  };
  return { dependencies, get stopCalls() { return stopCalls; } };
}

test("status binds the recorded mode and process generation to the exact command", async () => {
  const localRoot = temporaryDirectory("sts2-visible-status-");
  try {
    const { record } = writeOwnedRecord(localRoot);
    const status = await queryHeadlessStatus({
      localRoot,
      getProcessCommand: () => ownedCommand,
      getProcessStartedAt: () => record.process_started_at,
      readEndpoint: async () => ({ ok: false, error: "closed" })
    });
    assert.equal(status.process.running, true);
    assert.equal(status.process.command_matches_record, true);
    assert.equal(status.process.display_mode, "native_window");
  } finally {
    rmSync(localRoot, { recursive: true, force: true });
  }
});

test("stop refuses PID reuse before host shutdown or any signal", async () => {
  const localRoot = temporaryDirectory("sts2-visible-pid-reuse-");
  try {
    writeOwnedRecord(localRoot);
    let shutdownCalls = 0;
    let signalCalls = 0;
    await assert.rejects(stopHeadlessHost({ localRoot, dependencies: {
      getProcessCommand: () => ownedCommand,
      getProcessStartedAt: () => "2026-10-01T00:00:01.000Z",
      requestShutdown: async () => { shutdownCalls += 1; return { status: "requested" }; },
      killProcess: () => { signalCalls += 1; }
    } }), /process_generation_mismatch/u);
    assert.equal(shutdownCalls, 0);
    assert.equal(signalCalls, 0);
  } finally {
    rmSync(localRoot, { recursive: true, force: true });
  }
});

test("stop authenticates shutdown before revalidated fallback and persists a changed sentinel as failure", async () => {
  const localRoot = temporaryDirectory("sts2-visible-stop-");
  try {
    const { record, runtimeRoot, sessionDirectory } = writeOwnedRecord(localRoot);
    let alive = true;
    const calls = [];
    const result = await stopHeadlessHost({ localRoot, dependencies: {
      getProcessCommand: () => {
        calls.push(["inspect_command"]);
        return alive ? ownedCommand : null;
      },
      getProcessStartedAt: () => {
        calls.push(["inspect_generation"]);
        return record.process_started_at;
      },
      requestShutdown: async (request) => {
        calls.push(["shutdown", request.expectedRuntimeInstanceId, request.hostControlToken]);
        return { status: "unavailable" };
      },
      killProcess: (pid, signal) => {
        calls.push(["kill", pid, signal]);
        alive = false;
      },
      readEndpoint: async () => ({ ok: false, error: "closed" }),
      snapshotSharedProfile: () => ({ ...record.shared_profile_sentinel_before, tree_sha256: "after" }),
      compareSnapshots: (before, after) => ({ unchanged: before.tree_sha256 === after.tree_sha256, before, after }),
      writeRecord: (file, value) => {
        if (file.endsWith("lifecycle.json")) writeFileSync(file, JSON.stringify(value));
        else {
          mkdirSync(path.dirname(file), { recursive: true });
          writeFileSync(file, JSON.stringify(value));
        }
      }
    } });
    assert.equal(result.status, "stopped_integrity_failed");
    assert.equal(result.shared_profile_integrity_status, "changed");
    const shutdownIndex = calls.findIndex(([name]) => name === "shutdown");
    const killIndex = calls.findIndex(([name]) => name === "kill");
    assert.ok(shutdownIndex > 0);
    assert.ok(killIndex > shutdownIndex);
    assert.deepEqual(calls[killIndex].slice(1), [7001, "SIGKILL"]);
    assert.equal(calls[killIndex - 1][0], "inspect_generation");
    const closed = JSON.parse(readFileSync(path.join(sessionDirectory, "lifecycle.json"), "utf8"));
    assert.equal(closed.status, "failed");
    assert.equal(closed.shared_profile_sentinel.unchanged, false);
    assert.equal(readFileSync(path.join(sessionDirectory, "lifecycle.json"), "utf8").includes("secret-test-only"), false);
  } finally {
    rmSync(localRoot, { recursive: true, force: true });
  }
});

test("native start is sealed-only by default and strips ambient source canaries", async () => {
  const fixture = nativeStartFixture("sts2-native-start-default-");
  const fake = fakeChild(7300);
  const observed = {};
  const previousCanary = process.env[SOURCE_CANARY_ENVIRONMENT_VARIABLE];
  process.env[SOURCE_CANARY_ENVIRONMENT_VARIABLE] = "c".repeat(40);
  try {
    const capabilities = exactCapabilities({
      executionAvailable: false,
      implementation: {
        source_revision: fixture.identity.source_revision,
        artifact_sha256: fixture.artifactSha,
        module_version_id: fixture.identity.artifact_mvid
      }
    });
    await assert.rejects(runHeadlessHost({
      installation: fixture.installation,
      localRoot: fixture.localRoot,
      isolatedProfileId: "native-start-default",
      displayMode: "native_window",
      dependencies: nativeStartDependencies(fake, capabilities, observed)
    }), /execution_unavailable/u);
    assert.equal(observed.launchOptions.connectorCanary, null);
    assert.equal(SOURCE_CANARY_ENVIRONMENT_VARIABLE in observed.spawnEnvironment, false);
    assert.deepEqual(nativeLifecycleRecord(fixture).connector_authority, {
      profile: "sealed_only",
      source_revision: fixture.identity.source_revision,
      artifact_sha256: fixture.artifactSha
    });
    assert.equal(observed.provenanceCalls, 0);
  } finally {
    if (previousCanary == null) delete process.env[SOURCE_CANARY_ENVIRONMENT_VARIABLE];
    else process.env[SOURCE_CANARY_ENVIRONMENT_VARIABLE] = previousCanary;
    await closeFakeChildOutput(fake);
    rmSync(fixture.root, { recursive: true, force: true });
  }
});

test("native start admits and records only the exact requested installed source canary", async () => {
  const fixture = nativeStartFixture("sts2-native-start-canary-");
  const fake = fakeChild(7306);
  const observed = {};
  const previousCanary = process.env[SOURCE_CANARY_ENVIRONMENT_VARIABLE];
  process.env[SOURCE_CANARY_ENVIRONMENT_VARIABLE] = "c".repeat(40);
  try {
    const capabilities = exactCapabilities({
      implementation: {
        source_revision: fixture.identity.source_revision,
        artifact_sha256: fixture.artifactSha,
        module_version_id: fixture.identity.artifact_mvid
      }
    });
    const record = await runHeadlessHost({
      installation: fixture.installation,
      localRoot: fixture.localRoot,
      isolatedProfileId: "native-start-canary",
      displayMode: "native_window",
      expectedExperimentalConnectorSource: fixture.identity.source_revision,
      dependencies: nativeStartDependencies(fake, capabilities, observed)
    });
    assert.deepEqual(observed.launchOptions.connectorCanary, {
      game_id: null,
      source_revision: fixture.identity.source_revision,
      artifact_sha256: fixture.artifactSha
    });
    assert.equal(observed.spawnEnvironment[SOURCE_CANARY_ENVIRONMENT_VARIABLE], fixture.identity.source_revision);
    assert.deepEqual(record.connector_authority, {
      profile: "exact_process_local_canary",
      source_revision: fixture.identity.source_revision,
      artifact_sha256: fixture.artifactSha
    });
    assert.equal(observed.launch.hostConfiguration.authority_profile, record.connector_authority.profile);
    assert.equal(record.status, "exited");
    assert.equal(observed.provenanceCalls, 1);
  } finally {
    if (previousCanary == null) delete process.env[SOURCE_CANARY_ENVIRONMENT_VARIABLE];
    else process.env[SOURCE_CANARY_ENVIRONMENT_VARIABLE] = previousCanary;
    await closeFakeChildOutput(fake);
    rmSync(fixture.root, { recursive: true, force: true });
  }
});

test("native source opt-in rejects malformed, headless, and mismatched revisions before spawn", async () => {
  const sourceRevision = "a".repeat(40);
  const malformed = "A".repeat(40);
  const noLaunch = () => assert.fail("invalid source authority must be rejected before spawn");
  await assert.rejects(runHeadlessHost({
    installation: {},
    localRoot: "/unused",
    expectedExperimentalConnectorSource: malformed,
    dependencies: { launchRuntime: noLaunch }
  }), /lowercase 40-character Git revision/u);
  await assert.rejects(runHeadlessHost({
    installation: {},
    localRoot: "/unused",
    expectedExperimentalConnectorSource: sourceRevision,
    dependencies: { launchRuntime: noLaunch }
  }), /limited to native-window/u);
  await assert.rejects(runHeadlessHost({
    installation: {},
    localRoot: "/unused",
    isolatedProfileId: "native-start-shared-rejection",
    sharedProfileAcknowledged: true,
    displayMode: "native_window",
    expectedExperimentalConnectorSource: sourceRevision,
    dependencies: { launchRuntime: noLaunch }
  }), /reject --shared-profile/u);

  const fixture = nativeStartFixture("sts2-native-start-mismatch-");
  const fake = fakeChild(7307);
  const observed = {};
  try {
    await assert.rejects(runHeadlessHost({
      installation: fixture.installation,
      localRoot: fixture.localRoot,
      isolatedProfileId: "native-start-mismatch",
      displayMode: "native_window",
      expectedExperimentalConnectorSource: "d".repeat(40),
      dependencies: nativeStartDependencies(fake, exactCapabilities(), observed)
    }), /does not match the verified installed identity/u);
    assert.equal(observed.launchOptions, undefined);
    assert.equal(observed.provenanceCalls, 0);
  } finally {
    await closeFakeChildOutput(fake);
    rmSync(fixture.root, { recursive: true, force: true });
  }
});

test("exact source canary does not bypass the loaded Connector identity gate", async () => {
  const fixture = nativeStartFixture("sts2-native-start-loaded-drift-");
  const fake = fakeChild(7308);
  const observed = {};
  try {
    const capabilities = exactCapabilities({
      implementation: {
        source_revision: "d".repeat(40),
        artifact_sha256: fixture.artifactSha,
        module_version_id: fixture.identity.artifact_mvid
      }
    });
    await assert.rejects(runHeadlessHost({
      installation: fixture.installation,
      localRoot: fixture.localRoot,
      isolatedProfileId: "native-start-loaded-drift",
      displayMode: "native_window",
      expectedExperimentalConnectorSource: fixture.identity.source_revision,
      dependencies: nativeStartDependencies(fake, capabilities, observed)
    }), /loaded_connector_revision_mismatch/u);
    assert.deepEqual(observed.launchOptions.connectorCanary, {
      game_id: null,
      source_revision: fixture.identity.source_revision,
      artifact_sha256: fixture.artifactSha
    });
    const record = nativeLifecycleRecord(fixture);
    assert.equal(record.status, "failed");
    assert.deepEqual(record.connector_authority, {
      profile: "exact_process_local_canary",
      source_revision: fixture.identity.source_revision,
      artifact_sha256: fixture.artifactSha
    });
    assert.equal(observed.provenanceCalls, 0);
  } finally {
    await closeFakeChildOutput(fake);
    rmSync(fixture.root, { recursive: true, force: true });
  }
});

test("visible launch rejects shared profiles before invoking any launch seam", async () => {
  const localRoot = temporaryDirectory("sts2-visible-profile-guard-");
  try {
    let launches = 0;
    await assert.rejects(runHeadlessHost({
      installation: { executable: "/game/Slay the Spire 2", executable_cwd: "/game" },
      localRoot,
      displayMode: "native_window",
      sharedProfileAcknowledged: true,
      dependencies: { launchRuntime: () => { launches += 1; } }
    }), /require --isolated-profile/u);
    assert.equal(launches, 0);
  } finally {
    rmSync(localRoot, { recursive: true, force: true });
  }
});

test("changed shared-profile sentinel makes a visible lifecycle fail after normal close", async () => {
  const localRoot = temporaryDirectory("sts2-visible-lifecycle-");
  const source = exactCapabilities();
  const before = {
    root: "/shared",
    present: true,
    file_count: 1,
    directory_count: 1,
    symlink_count: 0,
    total_file_bytes: 4,
    tree_sha256: "before"
  };
  const after = { ...before, tree_sha256: "after" };
  const fake = fakeChild(7301);
    let launches = 0;
  let sentinelReads = 0;
  let provenanceCalls = 0;
  const signalSource = new EventEmitter();
  try {
    await assert.rejects(runHeadlessHost({
      installation: { executable: "/game/Slay the Spire 2", executable_cwd: "/game" },
      localRoot,
      isolatedProfileId: "native-test",
      displayMode: "native_window",
      dependencies: {
        getDiskIdentity: () => ({ exact: true }),
        requireSupported: () => ({ status: "supported_exact" }),
        getInstalledIdentity: () => ({
          status: "verified",
          installed_sha256: "b".repeat(64),
          identity: { source_revision: "a".repeat(40), artifact_mvid: "12345678-1234-1234-1234-123456789abc" }
        }),
        enumerateGameProcesses: (_platform, options) => {
          assert.equal(options.failClosed, true);
          return [];
        },
        readEndpoint: async () => ({ ok: false, error: "endpoint_clear" }),
        snapshotSharedProfile: () => (++sentinelReads === 1 ? before : after),
        compareSnapshots: (left, right) => ({ unchanged: left.tree_sha256 === right.tree_sha256, before: left, after: right }),
        launchRuntime: (_installation, options) => {
          launches += 1;
          assert.equal(options.displayMode, "native_window");
          assert.equal(options.launchProfile.profile_id, "native-test");
          return { child: fake, args: ["--verbose", "--force-steam=off", "--clientId=1"], connector: { url: "http://127.0.0.1:15526" }, hostControlToken: "token" };
        },
        getProcessStartedAt: () => "2026-10-01T00:00:00.000Z",
        waitEndpoint: async () => ({ ok: true, value: source }),
        requestProvenance: async ({ expectedRuntimeInstanceId, hostControlToken }) => {
          provenanceCalls += 1;
          assert.equal(expectedRuntimeInstanceId, "runtime-native-1");
          assert.equal(hostControlToken, "token");
          return { status: "observed", response: { runtime_instance_id: expectedRuntimeInstanceId } };
        },
        waitSnapshot: async () => [{ ok: true, value: {
          status: "interactive",
          snapshot_id: "snapshot-1",
          interaction: { interaction_id: "interaction-1", kind: "main_menu" },
          bound_actions: { status: "complete", actions: [{ bound_action_id: "a" }] }
        } }],
        waitChildExit: async () => {
          fake.exitCode = 0;
          fake.emit("exit", 0, null);
          return { code: 0, signal: null };
        },
        installSignalHandlers: false,
        projectIdentity: () => ({ version: "test" })
      }
    }), /Shared game profile sentinel/u);
    assert.equal(launches, 1);
    assert.equal(provenanceCalls, 1);
    assert.equal(sentinelReads, 2);
  } finally {
    fake.stdout.destroy();
    fake.stderr.destroy();
    rmSync(localRoot, { recursive: true, force: true });
  }
});

test("unchanged shared-profile sentinel closes a visible lifecycle without a Headless claim", async () => {
  const localRoot = temporaryDirectory("sts2-visible-lifecycle-pass-");
  const shared = {
    root: "/shared",
    present: true,
    file_count: 1,
    directory_count: 1,
    symlink_count: 0,
    total_file_bytes: 4,
    tree_sha256: "stable"
  };
  const fake = fakeChild(7302);
  let snapshots = 0;
  try {
    const record = await runHeadlessHost({
      installation: { executable: "/game/Slay the Spire 2", executable_cwd: "/game" },
      localRoot,
      isolatedProfileId: "native-pass",
      displayMode: "native_window",
      dependencies: {
        getDiskIdentity: () => ({ exact: true }),
        requireSupported: () => ({ status: "supported_exact" }),
        getInstalledIdentity: () => ({
          status: "verified",
          installed_sha256: "b".repeat(64),
          identity: { source_revision: "a".repeat(40), artifact_mvid: "12345678-1234-1234-1234-123456789abc" }
        }),
        enumerateGameProcesses: () => [],
        readEndpoint: async () => ({ ok: false, error: "endpoint_clear" }),
        snapshotSharedProfile: () => { snapshots += 1; return shared; },
        compareSnapshots: (before, after) => ({ unchanged: before.tree_sha256 === after.tree_sha256, before, after }),
        launchRuntime: () => ({
          child: fake,
          args: ["--verbose", "--force-steam=off", "--clientId=1"],
          connector: { url: "http://127.0.0.1:15526" },
          hostControlToken: "token"
        }),
        getProcessStartedAt: () => "2026-10-01T00:00:00.000Z",
        waitEndpoint: async () => ({ ok: true, value: exactCapabilities() }),
        requestProvenance: async ({ expectedRuntimeInstanceId }) => ({
          status: "observed",
          response: { runtime_instance_id: expectedRuntimeInstanceId }
        }),
        waitSnapshot: async () => [{ ok: true, value: {
          status: "interactive",
          snapshot_id: "snapshot-2",
          interaction: { interaction_id: "interaction-2", kind: "main_menu" },
          bound_actions: { status: "complete", actions: [{ bound_action_id: "a" }] }
        } }],
        waitChildExit: async () => {
          fake.exitCode = 0;
          fake.emit("exit", 0, null);
          return { code: 0, signal: null };
        },
        installSignalHandlers: false,
        projectIdentity: () => ({ version: "test" })
      }
    });
    assert.equal(record.display_mode, "native_window");
    assert.equal(record.loaded_host_kind, "live_ui");
    assert.equal(record.shared_profile_integrity_status, "unchanged");
    assert.equal("headless" in record, false);
    assert.equal(snapshots, 2);
  } finally {
    fake.stdout.destroy();
    fake.stderr.destroy();
    rmSync(localRoot, { recursive: true, force: true });
  }
});

test("visible startup endpoint collision blocks launch and failed startup closes through its owned child", async () => {
  const localRoot = temporaryDirectory("sts2-visible-startup-");
  const fake = fakeChild(7303);
  let launches = 0;
  let sentinelReads = 0;
  try {
    await assert.rejects(runHeadlessHost({
      installation: { executable: "/game/Slay the Spire 2", executable_cwd: "/game" },
      localRoot,
      isolatedProfileId: "native-collision",
      displayMode: "native_window",
      dependencies: {
        getDiskIdentity: () => ({}),
        requireSupported: () => ({}),
        readEndpoint: async () => ({ ok: true, value: { protocol_version: "1" } }),
        launchRuntime: () => { launches += 1; return {}; }
      }
    }), /already active/u);
    assert.equal(launches, 0);

    const signalSource = new EventEmitter();
    await assert.rejects(runHeadlessHost({
      installation: { executable: "/game/Slay the Spire 2", executable_cwd: "/game" },
      localRoot,
      isolatedProfileId: "native-failed",
      displayMode: "native_window",
      dependencies: {
        getDiskIdentity: () => ({}),
        requireSupported: () => ({}),
        getInstalledIdentity: () => ({
          status: "verified",
          installed_sha256: "b".repeat(64),
          identity: { source_revision: "a".repeat(40), artifact_mvid: "12345678-1234-1234-1234-123456789abc" }
        }),
        enumerateGameProcesses: () => [],
        readEndpoint: async () => ({ ok: false, error: "endpoint_clear" }),
        snapshotSharedProfile: () => { sentinelReads += 1; return { tree_sha256: "same", root: "/shared", present: true }; },
        compareSnapshots: (before, after) => ({ unchanged: before.tree_sha256 === after.tree_sha256, before, after }),
        launchRuntime: () => {
          launches += 1;
          return { child: fake, args: ["--verbose", "--force-steam=off", "--clientId=1"], connector: {}, hostControlToken: "token" };
        },
        getProcessCommand: () => ownedCommand,
        getProcessStartedAt: () => "2026-10-01T00:00:00.000Z",
        waitEndpoint: async () => {
          signalSource.emit("SIGINT");
          return { ok: false, error: "startup_timeout" };
        },
        stopRuntimeChild: async (child, { beforeSignal }) => {
          assert.equal(beforeSignal(), true);
          child.exitCode = 1;
          child.emit("exit", 1, null);
          return { code: 1, signal: null };
        },
        projectIdentity: () => ({ version: "test" }),
        signalSource,
        wait: async () => {},
        installSignalHandlers: true
      }
    }), /Host launch interrupted by an operator signal/u);
    assert.equal(launches, 1);
    assert.equal(sentinelReads, 2);
    assert.equal(signalSource.listenerCount("SIGINT"), 0);
    assert.equal(signalSource.listenerCount("SIGTERM"), 0);
  } finally {
    fake.stdout.destroy();
    fake.stderr.destroy();
    rmSync(localRoot, { recursive: true, force: true });
  }
});

test("visible startup with unconfirmed close retains ownership records and defers sentinel comparison", async () => {
  const localRoot = temporaryDirectory("sts2-visible-close-pending-");
  const runtimeRoot = path.join(localRoot, "runtime");
  const fake = fakeChild(7304);
  let sentinelReads = 0;
  try {
    await assert.rejects(runHeadlessHost({
      installation: { executable: "/game/Slay the Spire 2", executable_cwd: "/game" },
      localRoot,
      isolatedProfileId: "native-pending",
      displayMode: "native_window",
      dependencies: {
        getDiskIdentity: () => ({}),
        requireSupported: () => ({}),
        getInstalledIdentity: () => ({
          status: "verified",
          installed_sha256: "b".repeat(64),
          identity: { source_revision: "a".repeat(40), artifact_mvid: "12345678-1234-1234-1234-123456789abc" }
        }),
        enumerateGameProcesses: () => [],
        getProcessCommand: () => ownedCommand,
        getProcessStartedAt: () => "2026-10-01T00:00:00.000Z",
        readEndpoint: async () => ({ ok: false, error: "endpoint_clear" }),
        snapshotSharedProfile: () => { sentinelReads += 1; return { tree_sha256: "same", root: "/shared", present: true }; },
        compareSnapshots: () => assert.fail("sentinel cannot be compared before actual close"),
        launchRuntime: () => ({
          child: fake,
          args: ["--verbose", "--force-steam=off", "--clientId=1"],
          connector: {},
          hostControlToken: "secret-test-only"
        }),
        waitEndpoint: async () => ({ ok: false, error: "startup_timeout" }),
        stopRuntimeChild: async (_child, { beforeSignal }) => {
          assert.equal(beforeSignal(), true);
          return null;
        },
        projectIdentity: () => ({ version: "test" }),
        installSignalHandlers: false
      }
    }), /startup_timeout/u);
    assert.equal(sentinelReads, 1);
    const current = JSON.parse(readFileSync(path.join(runtimeRoot, "current.json"), "utf8"));
    const control = JSON.parse(readFileSync(path.join(runtimeRoot, "current-control.json"), "utf8"));
    const lifecycle = JSON.parse(readFileSync(path.join(current.session_directory, "lifecycle.json"), "utf8"));
    assert.equal(current.status, "close_unconfirmed");
    assert.equal(current.pid, fake.pid);
    assert.equal(control.pid, fake.pid);
    assert.equal(control.host_control_token, "secret-test-only");
    assert.equal(lifecycle.shared_profile_integrity_status, "pending_process_close");
  } finally {
    fake.kill("SIGKILL");
    await closeFakeChildOutput(fake);
    rmSync(localRoot, { recursive: true, force: true });
  }
});

test("visible startup without Host control retains the child record without signaling it", async () => {
  const localRoot = temporaryDirectory("sts2-visible-generation-pending-");
  const runtimeRoot = path.join(localRoot, "runtime");
  const fake = fakeChild(7305);
  const shared = { root: "/shared", present: true, tree_sha256: "same" };
  let sentinelReads = 0;
  try {
    await assert.rejects(runHeadlessHost({
      installation: { executable: "/game/Slay the Spire 2", executable_cwd: "/game" },
      localRoot,
      isolatedProfileId: "native-generation-pending",
      displayMode: "native_window",
      dependencies: {
        getDiskIdentity: () => ({}),
        requireSupported: () => ({}),
        getInstalledIdentity: () => ({ status: "verified" }),
        enumerateGameProcesses: () => [],
        readEndpoint: async () => ({ ok: false, error: "endpoint_clear" }),
        snapshotSharedProfile: () => { sentinelReads += 1; return shared; },
        compareSnapshots: () => assert.fail("sentinel cannot be compared before actual close"),
        launchRuntime: () => ({ child: fake, args: ["--verbose", "--force-steam=off", "--clientId=1"], connector: {} }),
        getProcessStartedAt: () => "2026-10-01T00:00:00.000Z",
        stopRuntimeChild: async (_child, { beforeSignal }) => {
          assert.equal(beforeSignal(), false);
          return null;
        },
        projectIdentity: () => ({ version: "test" }),
        installSignalHandlers: false
      }
    }), /Host lifecycle control was not configured/u);
    const current = JSON.parse(readFileSync(path.join(runtimeRoot, "current.json"), "utf8"));
    const control = JSON.parse(readFileSync(path.join(runtimeRoot, "current-control.json"), "utf8"));
    const lifecycle = JSON.parse(readFileSync(path.join(current.session_directory, "lifecycle.json"), "utf8"));
    assert.equal(current.status, "close_unconfirmed");
    assert.equal(current.pid, fake.pid);
    assert.equal(current.process_started_at, "2026-10-01T00:00:00.000Z");
    assert.equal(control.pid, fake.pid);
    assert.equal(control.host_control_token, null);
    assert.equal(lifecycle.shared_profile_integrity_status, "pending_process_close");
    assert.equal(sentinelReads, 1);
  } finally {
    fake.kill("SIGKILL");
    await new Promise((resolve) => setImmediate(resolve));
    fake.stdout.destroy();
    fake.stderr.destroy();
    rmSync(localRoot, { recursive: true, force: true });
  }
});

for (const stage of ["endpoint", "provenance", "snapshot"]) {
  test(`operator signal during ${stage} startup gate prevents ready publication`, async () => {
    const localRoot = temporaryDirectory(`sts2-visible-signal-${stage}-`);
    const child = fakeChild(7400 + ["endpoint", "provenance", "snapshot"].indexOf(stage));
    const signalSource = new EventEmitter();
    const fixture = visibleSignalDependencies({ child, signalSource, signalAt: stage });
    const originalLog = console.log;
    let readyLogs = 0;
    console.log = (line) => { if (String(line).includes('"status": "ready"')) readyLogs += 1; };
    try {
      await assert.rejects(runHeadlessHost({
        installation: { executable: "/game/Slay the Spire 2", executable_cwd: "/game" },
        localRoot,
        isolatedProfileId: "signal-startup",
        displayMode: "native_window",
        dependencies: fixture.dependencies
      }), /Host signal shutdown did not confirm process exit/u);
      const current = JSON.parse(readFileSync(path.join(localRoot, "runtime", "current.json"), "utf8"));
      assert.equal(current.status, "close_unconfirmed");
      assert.equal(current.ready_at, undefined);
      assert.equal(current.loaded_identity, null);
      assert.equal(readyLogs, 0);
      assert.equal(fixture.stopCalls, 1);
      assert.equal(current.shared_profile_integrity_status, "pending_process_close");
    } finally {
      console.log = originalLog;
      child.kill("SIGKILL");
      await closeFakeChildOutput(child);
      rmSync(localRoot, { recursive: true, force: true });
    }
  });
}

for (const stopBehavior of ["null", "reject", "never"]) {
  test(`ready-phase signal records ${stopBehavior} shutdown as bounded close-unconfirmed`, async () => {
    const localRoot = temporaryDirectory(`sts2-visible-ready-stop-${stopBehavior}-`);
    const child = fakeChild(7410 + ["null", "reject", "never"].indexOf(stopBehavior));
    const signalSource = new EventEmitter();
    let resolveReady;
    const ready = new Promise((resolve) => { resolveReady = resolve; });
    const fixture = visibleSignalDependencies({ child, signalSource, stopBehavior, onReady: resolveReady });
    const originalLog = console.log;
    const unhandled = [];
    const onUnhandled = (error) => unhandled.push(error);
    process.on("unhandledRejection", onUnhandled);
    console.log = () => {};
    try {
      const run = runHeadlessHost({
        installation: { executable: "/game/Slay the Spire 2", executable_cwd: "/game" },
        localRoot,
        isolatedProfileId: "signal-ready",
        displayMode: "native_window",
        dependencies: { ...fixture.dependencies, signalStopTimeoutMs: stopBehavior === "never" ? 20 : 100 }
      });
      await ready;
      signalSource.emit("SIGINT");
      await assert.rejects(run, /Host signal shutdown did not confirm process exit/u);
      await new Promise((resolve) => setImmediate(resolve));
      const current = JSON.parse(readFileSync(path.join(localRoot, "runtime", "current.json"), "utf8"));
      assert.equal(current.status, "close_unconfirmed");
      assert.equal(current.shared_profile_integrity_status, "pending_process_close");
      assert.equal(fixture.stopCalls, 1);
      assert.deepEqual(unhandled, []);
    } finally {
      console.log = originalLog;
      process.off("unhandledRejection", onUnhandled);
      child.kill("SIGKILL");
      await new Promise((resolve) => setImmediate(resolve));
      child.stdout.destroy();
      child.stderr.destroy();
      rmSync(localRoot, { recursive: true, force: true });
    }
  });
}

test("ready-phase signal persists and closes only after child exit is observed", async () => {
  const localRoot = temporaryDirectory("sts2-visible-ready-stop-closed-");
  const child = fakeChild(7420);
  const signalSource = new EventEmitter();
  let resolveReady;
  const ready = new Promise((resolve) => { resolveReady = resolve; });
  const fixture = visibleSignalDependencies({ child, signalSource, stopBehavior: "close", onReady: resolveReady });
  try {
    const record = await (async () => {
      const run = runHeadlessHost({
        installation: { executable: "/game/Slay the Spire 2", executable_cwd: "/game" },
        localRoot,
        isolatedProfileId: "signal-ready-close",
        displayMode: "native_window",
        dependencies: fixture.dependencies
      });
      await ready;
      signalSource.emit("SIGINT");
      return run;
    })();
    assert.equal(record.status, "exited");
    assert.equal(record.exit.code, 0);
    assert.equal(record.shared_profile_integrity_status, "unchanged");
    assert.equal(fixture.stopCalls, 1);
    assert.equal(readFileSync(path.join(record.session_directory, "lifecycle.json"), "utf8").includes("test-only"), false);
  } finally {
    child.stdout.destroy();
    child.stderr.destroy();
    rmSync(localRoot, { recursive: true, force: true });
  }
});
