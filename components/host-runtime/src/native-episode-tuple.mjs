import { readInstalledConnectorIdentity } from "./game-installation.mjs";

const FIELDS = ["game_version", "game_commit", "game_assembly_sha256", "host_artifact_sha256",
  "host_module_version_id", "host_source_revision", "modset_fingerprint"];

export function validateExpectedNativeTuple(value) {
  if (value == null) return null;
  if (typeof value !== "object" || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...FIELDS].sort())
    || FIELDS.some(key => typeof value[key] !== "string" || value[key].length > 128 || !value[key])
    || ["game_assembly_sha256", "host_artifact_sha256", "modset_fingerprint"].some(key => !/^[a-f0-9]{64}$/u.test(value[key]))
    || !/^[a-f0-9]{40}$/u.test(value.host_source_revision)
    || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/u.test(value.host_module_version_id))
    throw new Error("invalid_expected_native_tuple");
  return { ...value };
}

export function requireExpectedNativeDiskTuple(expected, disk, installed) {
  const value = validateExpectedNativeTuple(expected);
  if (value == null) return;
  if (disk?.release?.version !== value.game_version || disk?.release?.commit !== value.game_commit
    || disk?.sts2_assembly?.sha256 !== value.game_assembly_sha256 || installed?.status !== "verified"
    || installed.installed_sha256 !== value.host_artifact_sha256
    || installed.identity?.source_revision !== value.host_source_revision
    || installed.identity?.artifact_mvid !== value.host_module_version_id)
    throw new Error("native_episode_installed_tuple_changed_before_spawn");
}

export function requireExpectedNativeInstallation(expected, installation, disk) {
  if (expected != null) requireExpectedNativeDiskTuple(expected, disk, readInstalledConnectorIdentity(installation));
}

export function requireExpectedNativeLoadedTuple(expected, caps) {
  const value = validateExpectedNativeTuple(expected);
  if (value == null) return;
  const implementation = caps?.host?.implementation;
  if (caps?.game?.version !== value.game_version || caps?.game?.commit !== value.game_commit
    || implementation?.artifact_sha256 !== value.host_artifact_sha256
    || implementation?.module_version_id !== value.host_module_version_id
    || implementation?.source_revision !== value.host_source_revision
    || caps?.game?.modset?.fingerprint !== value.modset_fingerprint)
    throw new Error("native_episode_loaded_tuple_changed_before_bootstrap");
}
