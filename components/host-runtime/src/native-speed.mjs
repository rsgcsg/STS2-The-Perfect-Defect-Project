import path from "node:path";

export const NATIVE_SPEED_ENVIRONMENT_VARIABLES = [
  "STS2_CONNECTOR_NATIVE_SPEED", "STS2_CONNECTOR_PROFILE_ROOT",
  "STS2_CONNECTOR_PROFILE_GENERATION", "STS2_CONNECTOR_USER_DATA_ROOT"
];

export function validateNativeSpeed(mode) {
  if (mode == null) return null;
  if (!["normal", "fast", "instant"].includes(mode)) throw new Error("unsupported_native_speed");
  return mode;
}

export function resolveNativeSpeed(mode, launchProfile) {
  return validateNativeSpeed(mode)
    ?? (launchProfile?.mode === "isolated_local_profile" ? "instant" : null);
}

export function nativeSpeedLaunchEnvironment(nativeSpeed, profile) {
  const mode = validateNativeSpeed(nativeSpeed);
  if (mode == null && profile?.mode !== "isolated_local_profile") return {};
  if (profile?.mode !== "isolated_local_profile"
      || profile.steam !== "disabled_before_platform_initialization" || profile.client_id !== "1"
      || typeof profile.generation_id !== "string" || !/^[a-zA-Z0-9-]{1,128}$/u.test(profile.generation_id)
      || typeof profile.profile_root !== "string" || !path.isAbsolute(profile.profile_root)
      || typeof profile.expected_user_data_root !== "string" || !path.isAbsolute(profile.expected_user_data_root)
      || !contained(profile.profile_root, profile.expected_user_data_root)) {
    throw new Error("native_speed_requires_isolated_profile");
  }
  return {
    ...(mode == null ? {} : { STS2_CONNECTOR_NATIVE_SPEED: mode }),
    STS2_CONNECTOR_PROFILE_ROOT: profile.profile_root,
    STS2_CONNECTOR_PROFILE_GENERATION: profile.generation_id,
    STS2_CONNECTOR_USER_DATA_ROOT: profile.expected_user_data_root
  };
}

function contained(root, target, pathApi = path) {
  const relative = pathApi.relative(root, target);
  return relative !== "" && !relative.startsWith(`..${pathApi.sep}`) && relative !== ".." && !pathApi.isAbsolute(relative);
}

function samePath(first, second, platform) {
  if (typeof first !== "string" || typeof second !== "string") return false;
  const pathApi = platform === "win32" ? path.win32 : path.posix;
  if (!pathApi.isAbsolute(first) || !pathApi.isAbsolute(second)) return false;
  const normalize = (value) => platform === "win32" ? pathApi.resolve(value).toLowerCase() : pathApi.resolve(value);
  return normalize(first) === normalize(second);
}

export function requireNativeSpeedProvenance({ nativeSpeed, launchProfile, runtimeInstanceId, response,
  platform = process.platform, preparedBinding = null }) {
  const mode = validateNativeSpeed(nativeSpeed);
  if (mode == null) return null;
  const speed = response?.response?.native_speed;
  const errors = [];
  if (response?.status !== "observed") errors.push("provenance_not_observed");
  if (speed?.status !== "native_speed_ready" || speed?.ready !== true) errors.push("speed_not_ready");
  if (speed?.requested !== mode || !["normal", "fast", "instant"].includes(speed?.effective)
      || preparedBinding == null && speed?.effective !== mode) errors.push("native_speed_mismatch");
  if (speed?.runtime_instance_id !== runtimeInstanceId) errors.push("runtime_instance_changed");
  if (speed?.profile_generation_id !== launchProfile?.generation_id) errors.push("profile_generation_changed");
  if (!samePath(speed?.profile_root, launchProfile?.profile_root, platform)) errors.push("profile_root_changed");
  if (!samePath(speed?.user_data_root, launchProfile?.expected_user_data_root, platform)) errors.push("user_data_root_changed");
  if (typeof speed?.identity_key !== "string" || speed.identity_key.length === 0) errors.push("identity_unproven");
  if (!Number.isSafeInteger(speed?.native_profile_id) || speed.native_profile_id < 1
      || typeof speed?.native_profile_path !== "string"
      || !contained(launchProfile?.expected_user_data_root ?? "", speed.native_profile_path,
        platform === "win32" ? path.win32 : path.posix)) {
    errors.push("native_profile_unproven");
  }
  if (preparedBinding != null && (speed?.identity_key !== preparedBinding.identity_key
    || speed?.native_profile_id !== preparedBinding.native_profile_id
    || !samePath(speed?.native_profile_path, preparedBinding.native_profile_path, platform))) errors.push("prepared_binding_changed");
  if (errors.length) throw new Error(`native_speed_unverified:${errors.join(",")}`);
  return speed;
}

export async function prepareNativeSpeedBeforeBootstrap({ nativeSpeed, launchProfile, runtimeInstanceId,
  prepare, observeProvenance }) {
  if (validateNativeSpeed(nativeSpeed) == null) return null;
  const preparation = await prepare();
  if (preparation?.status !== "prepared") throw new Error(`native_speed_preparation_failed:${preparation?.status ?? "missing"}`);
  const response = await observeProvenance();
  return requireNativeSpeedProvenance({ nativeSpeed, launchProfile, runtimeInstanceId, response });
}
