import assert from "node:assert/strict";
import test from "node:test";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
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
  queryHeadlessStatus,
  runHeadlessHost,
  stopHeadlessHost
} from "../src/headless-host.mjs";
import { shippedRuntimeLaunch, stopChild } from "../src/runtime-probe.mjs";

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
  child.kill = (signal) => {
    child.signalCode = signal;
    child.emit("exit", null, signal);
    return true;
  };
  return child;
}

function exactCapabilities({ hostKind = "live_ui" } = {}) {
  return {
    protocol_version: SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
    execution_available: true,
    host: {
      host_kind: hostKind,
      runtime_instance_id: "runtime-native-1",
      implementation: {
        source_revision: "a".repeat(40),
        artifact_sha256: "b".repeat(64),
        module_version_id: "12345678-1234-1234-1234-123456789abc"
      }
    },
    game: { modset: { status: "exact_platform_modset" } }
  };
}

function isolatedProfile() {
  return {
    mode: "isolated_local_profile",
    isolation_status: "source_backed_experimental",
    profile_id: "native-test",
    generation_id: "profile-generation-1",
    profile_root: "/tmp/native-test",
    expected_user_data_root: "/tmp/native-test/home/Library/Application Support/SlayTheSpire2",
    steam: "disabled_before_platform_initialization",
    client_id: "1",
    args: ["--force-steam=off", "--clientId=1"],
    environment: { HOME: "/tmp/native-test/home", USERPROFILE: "/tmp/native-test/home" }
  };
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
    }), /startup_timeout/u);
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
    await new Promise((resolve) => setImmediate(resolve));
    fake.stdout.destroy();
    fake.stderr.destroy();
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
