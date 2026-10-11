import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { shippedRuntimeLaunch, requestHostPreparation } from "../src/runtime-probe.mjs";
import { resolveLaunchProfile } from "../src/profile-isolation.mjs";
import { evaluateShippedCapabilities } from "../src/headless-host.mjs";
import {
  validateNativeSpeed,
  nativeSpeedLaunchEnvironment,
  requireNativeSpeedProvenance,
  prepareNativeSpeedBeforeBootstrap
} from "../src/native-speed.mjs";

const profile = {
  mode: "isolated_local_profile", steam: "disabled_before_platform_initialization",
  client_id: "1", profile_id: "worker", generation_id: "generation-1",
  profile_root: "/isolated/profiles/worker",
  expected_user_data_root: "/isolated/profiles/worker/home/.local/share/SlayTheSpire2"
};
function provenance(mode, overrides = {}) {
  return { status: "observed", response: { native_speed: {
    status: "native_speed_ready", ready: true, requested: mode, before: "normal",
    effective: mode, runtime_instance_id: "runtime-1",
    profile_generation_id: profile.generation_id,
    user_data_root: profile.expected_user_data_root,
    profile_root: profile.profile_root, native_profile_id: 1,
    identity_key: "exact-game-mod-config",
    native_profile_path: `${profile.expected_user_data_root}/default/1/modded/profile1`,
    ...overrides
  } } };
}

test("all three speeds are one closed optional run configuration", () => {
  assert.equal(validateNativeSpeed(null), null);
  for (const mode of ["normal", "fast", "instant"]) assert.equal(validateNativeSpeed(mode), mode);
  for (const mode of ["None", "Normal", "turbo", "", 3]) assert.throws(() => validateNativeSpeed(mode));
  assert.deepEqual(nativeSpeedLaunchEnvironment(null, null), {});
  assert.throws(() => nativeSpeedLaunchEnvironment("instant", { mode: "shared_steam_profile" }));
  assert.throws(() => nativeSpeedLaunchEnvironment("instant", { ...profile, generation_id: null }));
  assert.equal(nativeSpeedLaunchEnvironment("instant", profile).STS2_CONNECTOR_NATIVE_SPEED, "instant");
});

test("post-boot Instant clamp and every missing binding block admission", () => {
  for (const mode of ["normal", "fast", "instant"]) {
    assert.equal(requireNativeSpeedProvenance({ nativeSpeed: mode, launchProfile: profile,
      runtimeInstanceId: "runtime-1", response: provenance(mode) }).effective, mode);
  }
  for (const overrides of [
    { effective: "fast" }, { ready: false }, { status: "native_speed_not_prepared" },
    { requested: "normal" }, { runtime_instance_id: "old-runtime" },
    { user_data_root: "/personal" }, { profile_generation_id: "old-generation" },
    { profile_root: "/personal" }, { native_profile_id: null }, { native_profile_path: "/personal/profile1" }, { identity_key: "" }
  ]) assert.throws(() => requireNativeSpeedProvenance({ nativeSpeed: "instant", launchProfile: profile,
    runtimeInstanceId: "runtime-1", response: provenance("instant", overrides) }));
});

test("Windows native path spelling normalizes without weakening profile identity", () => {
  const windowsProfile = { ...profile, profile_root: "C:\\Isolated\\Profiles\\worker",
    expected_user_data_root: "C:\\Isolated\\Profiles\\worker\\home\\AppData\\Roaming\\SlayTheSpire2" };
  const response = provenance("instant", { profile_root: "c:/isolated/profiles/worker/",
    user_data_root: "c:/isolated/profiles/worker/home/AppData/Roaming/SlayTheSpire2/",
    native_profile_path: "c:/isolated/profiles/worker/home/AppData/Roaming/SlayTheSpire2/default/1/modded/profile1" });
  assert.equal(requireNativeSpeedProvenance({ nativeSpeed: "instant", launchProfile: windowsProfile,
    runtimeInstanceId: "runtime-1", response, platform: "win32" }).ready, true);
  assert.throws(() => requireNativeSpeedProvenance({ nativeSpeed: "instant", launchProfile: windowsProfile,
    runtimeInstanceId: "runtime-1", response, platform: "win32",
    preparedBinding: { ...response.response.native_speed, native_profile_id: 2 } }));
});

test("preparation requires a separate observed getter match before bootstrap", async () => {
  for (const mode of ["normal", "fast", "instant"]) {
    const events = [];
    await prepareNativeSpeedBeforeBootstrap({ nativeSpeed: mode, launchProfile: profile,
      runtimeInstanceId: "runtime-1",
      prepare: async () => { events.push("prepare"); return { status: "prepared" }; },
      observeProvenance: async () => { events.push("observe"); return provenance(mode); }
    });
    events.push("bootstrap");
    assert.deepEqual(events, ["prepare", "observe", "bootstrap"]);
  }
  let actions = 0;
  await assert.rejects(async () => {
    await prepareNativeSpeedBeforeBootstrap({ nativeSpeed: "instant", launchProfile: profile,
      runtimeInstanceId: "runtime-1", prepare: async () => ({ status: "prepared" }),
      observeProvenance: async () => provenance("instant", { effective: "fast" }) });
    actions += 1;
  });
  assert.equal(actions, 0);
  let reads = 0;
  await assert.rejects(() => prepareNativeSpeedBeforeBootstrap({ nativeSpeed: "instant", launchProfile: profile,
    runtimeInstanceId: "runtime-1", prepare: async () => ({ status: "transport_error" }),
    observeProvenance: async () => { reads += 1; return provenance("instant"); } }));
  assert.equal(reads, 0);
});

test("the shipped episode wires speed before bootstrap and checks after bootstrap", () => {
  const source = readFileSync(new URL("../src/shipped-player-environment.mjs", import.meta.url), "utf8");
  assert.match(source, /nativeSpeed[\s\S]*shippedRuntimeLaunch[\s\S]*nativeSpeed/u);
  assert.ok(source.indexOf("await prepareNativeSpeedBeforeBootstrap(") >= 0);
  assert.ok(source.indexOf("await prepareNativeSpeedBeforeBootstrap(") < source.indexOf("await controller.register("));
  assert.match(source, /const refreshProvenance[\s\S]*requireNativeSpeedProvenance/u);
  assert.match(source, /shippedRuntimeLaunch[\s\S]*displayMode/u);
  assert.match(source, /evaluateShippedCapabilities\(capabilities, displayMode\)/u);
});

test("actual production launch forwards every mode and presentation with one protected isolated binding", async (context) => {
  const root = mkdtempSync(path.join(os.tmpdir(), "sts2-native-speed-launch-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  const launchProfile = resolveLaunchProfile({ localRoot: root, isolatedProfileId: "worker" });
  for (const displayMode of ["headless", "native-ui"]) for (const nativeSpeed of [null, "normal", "fast", "instant"]) {
    // The real launcher serializes and spawns a Node fixture executable that
    // rejects game arguments and exits; this never launches STS2.
    const launch = shippedRuntimeLaunch({ executable: process.execPath, executable_cwd: root }, {
      launchProfile, connectorEndpoint: "http://127.0.0.1:15526", nativeSpeed, displayMode,
      stdout: "ignore", stderr: "ignore"
    });
    await new Promise((resolve) => launch.child.once("exit", resolve));
    assert.equal(launch.environment.STS2_CONNECTOR_NATIVE_SPEED, nativeSpeed ?? undefined);
    assert.equal(launch.environment.STS2_CONNECTOR_PROFILE_GENERATION, launchProfile.generation_id);
    assert.equal(launch.environment.STS2_CONNECTOR_USER_DATA_ROOT, launchProfile.expected_user_data_root);
    assert.equal(launch.hostConfiguration.requested_native_speed, nativeSpeed);
    assert.equal(launch.args.includes("--headless"), displayMode === "headless");
  }
  for (const key of ["STS2_CONNECTOR_NATIVE_SPEED", "STS2_CONNECTOR_PROFILE_ROOT",
    "STS2_CONNECTOR_PROFILE_GENERATION", "STS2_CONNECTOR_USER_DATA_ROOT", "HOME"]) {
    assert.throws(() => shippedRuntimeLaunch({ executable: process.execPath, executable_cwd: root }, {
      launchProfile, connectorEndpoint: "http://127.0.0.1:15526", nativeSpeed: "instant",
      extraEnvironment: { [key]: "foreign" }
    }), /override/u);
  }
  const prior = process.env.STS2_CONNECTOR_NATIVE_SPEED;
  process.env.STS2_CONNECTOR_NATIVE_SPEED = "instant";
  try {
    const launch = shippedRuntimeLaunch({ executable: process.execPath, executable_cwd: root }, {
      stdout: "ignore", stderr: "ignore"
    });
    await new Promise((resolve) => launch.child.once("exit", resolve));
    assert.equal(launch.environment.STS2_CONNECTOR_NATIVE_SPEED, undefined);
    assert.equal(launch.environment.STS2_CONNECTOR_PROFILE_GENERATION, undefined);
  } finally {
    if (prior === undefined) delete process.env.STS2_CONNECTOR_NATIVE_SPEED;
    else process.env.STS2_CONNECTOR_NATIVE_SPEED = prior;
  }
});

test("presentation admission verifies existing host kinds without a protocol fork", () => {
  const capabilities = { protocol_version: "1.0.0", host: { host_kind: "headless" },
    game: { modset: { status: "exact_platform_modset" } }, execution_available: true };
  assert.equal(evaluateShippedCapabilities(capabilities, "headless", "1.0.0").ok, true);
  assert.equal(evaluateShippedCapabilities(capabilities, "native-ui", "1.0.0").ok, false);
  capabilities.host.host_kind = "live_ui";
  assert.equal(evaluateShippedCapabilities(capabilities, "native-ui", "1.0.0").ok, true);
  assert.equal(evaluateShippedCapabilities(capabilities, "headless", "1.0.0").ok, false);
  assert.throws(() => evaluateShippedCapabilities(capabilities, "new-host"));
});

test("preparation producer sends only the existing exact runtime and token envelope", async (context) => {
  const previous = globalThis.fetch;
  context.after(() => { globalThis.fetch = previous; });
  let observed;
  globalThis.fetch = async (url, options) => {
    observed = { url, body: JSON.parse(options.body) };
    return { ok: true, status: 200, json: async () => ({ ready: true, status: "native_speed_ready" }) };
  };
  const result = await requestHostPreparation({ endpoint: "http://127.0.0.1:15526",
    hostControlToken: "a".repeat(64), expectedRuntimeInstanceId: "runtime-1" });
  assert.equal(result.status, "prepared");
  assert.deepEqual(observed, { url: "http://127.0.0.1:15526/api/host-control/prepare",
    body: { expected_runtime_instance_id: "runtime-1", host_control_token: "a".repeat(64) } });
});
