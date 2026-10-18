import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { NORMAL_BASELINE_RECIPE, nativeBaselineLaunchEnvironment, requireNativeBaselineProvenance,
  prepareNativeHostBeforeBootstrap } from "../src/native-baseline.mjs";
import { shippedRuntimeLaunch } from "../src/runtime-probe.mjs";
import { resolveLaunchProfile } from "../src/profile-isolation.mjs";

const profile = { mode: "isolated_local_profile", steam: "disabled_before_platform_initialization", client_id: "1",
  generation_id: "generation-1", profile_root: "/isolated", expected_user_data_root: "/isolated/data" };
const progress = '{"enable_ftues":false}';
function response(overrides = {}) {
  return { status: "observed", response: { baseline: {
    ready: true, status: "baseline_ready", recipe: NORMAL_BASELINE_RECIPE,
    runtime_instance_id: "runtime-1", profile_generation_id: "generation-1",
    native_profile_id: 1, native_profile_path: "/isolated/data/profile1",
    prepared: { progress_json: progress, progress_sha256: createHash("sha256").update(progress).digest("hex"),
      tutorials_disabled: true, unlock_state: { number_of_runs: 1 } }, ...overrides } } };
}
const requireBaseline = (value) => requireNativeBaselineProvenance({ baselineRecipe: NORMAL_BASELINE_RECIPE,
  launchProfile: profile, runtimeInstanceId: "runtime-1", response: value });

test("baseline admission binds actual saved snapshot, runtime and isolated native profile", () => {
  assert.equal(requireBaseline(response()).ready, true);
  for (const bad of [{ recipe: "other" }, { ready: false }, { runtime_instance_id: "old" },
    { profile_generation_id: "old" }, { native_profile_path: "/personal/profile1" },
    { run_unlock_state_matches: false }, { prepared: { ...response().response.baseline.prepared, progress_json: "changed" } }])
    assert.throws(() => requireBaseline(response(bad)), /native_baseline_unverified/u);
  assert.throws(() => nativeBaselineLaunchEnvironment(NORMAL_BASELINE_RECIPE, null));
  assert.throws(() => nativeBaselineLaunchEnvironment("arbitrary", profile));
});

test("one authenticated preparation and independent read precede any bootstrap", async () => {
  const events = [];
  const result = await prepareNativeHostBeforeBootstrap({ baselineRecipe: NORMAL_BASELINE_RECIPE, nativeSpeed: null,
    launchProfile: profile, runtimeInstanceId: "runtime-1",
    prepare: async () => { events.push("prepare"); return { status: "prepared" }; },
    observeProvenance: async () => { events.push("observe"); return response(); } });
  assert.equal(result.baseline.prepared.progress_json, progress);
  assert.deepEqual(events, ["prepare", "observe"]);
  await assert.rejects(() => prepareNativeHostBeforeBootstrap({ baselineRecipe: NORMAL_BASELINE_RECIPE, nativeSpeed: null,
    prepare: async () => ({ status: "transport_error" }), observeProvenance: async () => { throw new Error("must not read"); } }),
  /native_host_preparation_failed/u);
});

test("real low-level launcher only opts in explicitly and clears ambient baseline", async (context) => {
  const root = mkdtempSync(path.join(os.tmpdir(), "sts2-native-baseline-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  const launchProfile = resolveLaunchProfile({ localRoot: root, isolatedProfileId: "worker" });
  const prior = process.env.STS2_CONNECTOR_BASELINE;
  process.env.STS2_CONNECTOR_BASELINE = NORMAL_BASELINE_RECIPE;
  try {
    for (const baselineRecipe of [null, NORMAL_BASELINE_RECIPE]) {
      const launch = shippedRuntimeLaunch({ executable: process.execPath, executable_cwd: root }, {
        launchProfile, baselineRecipe, connectorEndpoint: "http://127.0.0.1:15526", stdout: "ignore", stderr: "ignore" });
      await new Promise((resolve) => launch.child.once("exit", resolve));
      assert.equal(launch.environment.STS2_CONNECTOR_BASELINE, baselineRecipe ?? undefined);
      assert.equal(launch.hostConfiguration.requested_baseline_recipe, baselineRecipe);
    }
  } finally {
    if (prior === undefined) delete process.env.STS2_CONNECTOR_BASELINE;
    else process.env.STS2_CONNECTOR_BASELINE = prior;
  }
  assert.throws(() => shippedRuntimeLaunch({}, { launchProfile, baselineRecipe: NORMAL_BASELINE_RECIPE,
    connectorEndpoint: "http://127.0.0.1:15526", extraEnvironment: { STS2_CONNECTOR_BASELINE: "other" } }), /override/u);
});
