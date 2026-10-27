import path from "node:path";
import { createHash } from "node:crypto";
import { nativeSpeedLaunchEnvironment, requireNativeSpeedProvenance } from "./native-speed.mjs";

export const NORMAL_BASELINE_RECIPE = "normal-sp-all-unlocked-v1";
export const CAPTURED_PROGRESS_RECIPE = "captured-native-progress-v1";
export const NATIVE_BASELINE_ENVIRONMENT_VARIABLE = "STS2_CONNECTOR_BASELINE";
export function validateNativeBaseline(recipe) {
  if (recipe == null) return null;
  if (recipe !== NORMAL_BASELINE_RECIPE) throw new Error("unsupported_native_baseline");
  return recipe;
}
export function nativeBaselineLaunchEnvironment(recipe, launchProfile) {
  if (validateNativeBaseline(recipe) == null) return {};
  // Reuse the exact isolated profile binding, without requesting a speed change.
  nativeSpeedLaunchEnvironment(null, launchProfile);
  if (launchProfile?.mode !== "isolated_local_profile") throw new Error("native_baseline_requires_isolated_profile");
  return { [NATIVE_BASELINE_ENVIRONMENT_VARIABLE]: recipe };
}
export function requireNativeBaselineProvenance({ baselineRecipe, launchProfile, runtimeInstanceId,
  response, preparedBinding = null, platform = process.platform }) {
  if (validateNativeBaseline(baselineRecipe) == null) return null;
  const value = response?.response?.baseline;
  const pathApi = platform === "win32" ? path.win32 : path.posix;
  const relative = typeof value?.native_profile_path === "string"
    ? pathApi.relative(launchProfile.expected_user_data_root, value.native_profile_path) : null;
  const snapshot = value?.prepared;
  if (response?.status !== "observed" || value?.status !== "baseline_ready" || value.ready !== true
      || value.recipe !== baselineRecipe || value.runtime_instance_id !== runtimeInstanceId
      || value.profile_generation_id !== launchProfile.generation_id
      || !Number.isSafeInteger(value.native_profile_id) || value.native_profile_id < 1
      || relative == null || relative === "" || relative === ".." || relative.startsWith(`..${pathApi.sep}`) || pathApi.isAbsolute(relative)
      || snapshot?.tutorials_disabled !== true || !(snapshot.unlock_state?.number_of_runs > 0)
      || typeof snapshot.progress_json !== "string"
      || createHash("sha256").update(snapshot.progress_json).digest("hex") !== snapshot.progress_sha256
      || value.run_unlock_state_matches === false) throw new Error("native_baseline_unverified");
  if (preparedBinding != null && (value.native_profile_id !== preparedBinding.native_profile_id
      || value.native_profile_path !== preparedBinding.native_profile_path
      || snapshot.progress_sha256 !== preparedBinding.prepared.progress_sha256))
    throw new Error("native_baseline_prepared_binding_changed");
  return value;
}
export async function prepareNativeHostBeforeBootstrap({ nativeSpeed, baselineRecipe, launchProfile,
  runtimeInstanceId, prepare, observeProvenance }) {
  if (nativeSpeed == null && baselineRecipe == null) return { nativeSpeed: null, baseline: null };
  const result = await prepare();
  if (result?.status !== "prepared") throw new Error(`native_host_preparation_failed:${result?.status ?? "missing"}`);
  const response = await observeProvenance();
  return {
    nativeSpeed: requireNativeSpeedProvenance({ nativeSpeed, launchProfile, runtimeInstanceId, response }),
    baseline: requireNativeBaselineProvenance({ baselineRecipe, launchProfile, runtimeInstanceId, response })
  };
}

// Manager configuration only; never an Agent observation or a generic save import.
export function validateInitialProgress(value) {
  if (value == null) return null;
  if (typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).sort().join(",") !== "progress_json,progress_sha256,recipe"
      || ![NORMAL_BASELINE_RECIPE, CAPTURED_PROGRESS_RECIPE].includes(value.recipe) || typeof value.progress_json !== "string"
      || createHash("sha256").update(value.progress_json).digest("hex") !== value.progress_sha256)
    throw new Error("managed_initial_progress_invalid");
  return { recipe: value.recipe, progress_json: value.progress_json, progress_sha256: value.progress_sha256 };
}
