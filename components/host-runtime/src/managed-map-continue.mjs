import { createHash } from "node:crypto";
import { closeSync, lstatSync, openSync, readFileSync } from "node:fs";
import { isAbsolute } from "node:path";

const SHA256 = /^[a-f0-9]{64}$/u;
const PENDING = ["action_executor_running", "pending_host_operation", "pending_card_selection",
  "pending_relic_selection", "pending_card_reward", "pending_reward_set", "pending_bundle"];
const SCHEMA = "sts2.host-runtime/managed-map-continue-1";

export class ManagedMapContinueFailure extends Error {}
export class ManagedMapContinueUnknown extends ManagedMapContinueFailure {
  constructor(cause) { super("managed_map_continue_unknown_replace_process", { cause }); }
}

function candidate(started) {
  const { artifact_sha256, runtime_sts2_sha256 } = started.runtime.build ?? {};
  if (!SHA256.test(artifact_sha256 ?? "") || !SHA256.test(runtime_sts2_sha256 ?? "")) {
    throw new Error("managed_map_continue_candidate_identity_required");
  }
  return { artifact_sha256, runtime_sts2_sha256 };
}

function savePath(path) {
  if (typeof path !== "string" || !isAbsolute(path)) {
    throw new Error("managed_map_continue_absolute_path_required");
  }
  return path;
}

function fileIdentity(path) {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size <= 0 || stat.size > 8 * 1024 * 1024) {
    throw new Error("managed_map_continue_invalid_file");
  }
  const bytes = readFileSync(path);
  return { bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") };
}

export async function assertManagedMapReady(started, timeoutMs, expected = null) {
  const snapshot = started.session.observe();
  if (started.session.tainted || snapshot.interaction?.kind !== "map_navigation"
    || snapshot.completeness?.status !== "complete") {
    throw new Error("managed_map_continue_quiescent_map_required");
  }
  const identity = await started.runtime.process.request({ cmd: "run_identity" }, timeoutMs);
  if (identity?.type !== "run_identity" || identity.active !== true
    || typeof identity.seed !== "string" || !identity.seed
    || !Number.isSafeInteger(identity.act) || identity.act < 1
    || !Number.isSafeInteger(identity.floor) || identity.floor < 0
    || PENDING.some(flag => identity[flag] !== false)
    || expected && ["seed", "act", "floor"].some(key => identity[key] !== expected[key])) {
    throw new Error("managed_map_continue_quiescent_map_required");
  }
  return { seed: identity.seed, act: identity.act, floor: identity.floor };
}

/** An explicit operator save. Existing files are never overwritten. */
export async function saveManagedMapContinue(started, path, timeoutMs) {
  path = savePath(path);
  const native = candidate(started);
  const position = await assertManagedMapReady(started, timeoutMs);
  // Reserve only the operator's requested output; a failed native write remains
  // preserved at this path for diagnosis and is never automatically retried.
  closeSync(openSync(path, "wx", 0o600));
  let result;
  try { result = await started.runtime.process.request({ cmd: "write_continue_save", path }, timeoutMs); }
  catch (cause) { throw new ManagedMapContinueUnknown(cause); }
  // The pinned native producer owns completion checks. A completed room can
  // still be the physical room while its public map presentation is open.
  const mapBoundary = result?.room_type === "MapRoom" || result?.save_boundary === "map_presentation";
  if (result?.type !== "save_result" || result.success !== true
    || !mapBoundary || result.path !== path) {
    throw new ManagedMapContinueFailure("managed_map_continue_native_save_failed");
  }
  try {
    await assertManagedMapReady(started, timeoutMs, position);
    return { schema: SCHEMA, path, ...fileIdentity(path), candidate: native, ...position,
      save_boundary: result.save_boundary ?? "map_room",
      source_room_type: result.source_room_type ?? result.room_type };
  } catch (cause) { throw new ManagedMapContinueFailure("managed_map_continue_saved_unverified", { cause }); }
}

/** Validate immutable operator-provided save identity before touching a run. */
export function validateManagedMapContinue(started, save) {
  if (save?.schema !== SCHEMA || !SHA256.test(save.sha256 ?? "")
    || !Number.isSafeInteger(save.bytes) || save.bytes <= 0
    || typeof save.seed !== "string" || !save.seed
    || typeof save.parent_runtime_instance_id !== "string" || !save.parent_runtime_instance_id
    || typeof save.parent_game_continuity_id !== "string" || !save.parent_game_continuity_id
    || !Number.isSafeInteger(save.act) || save.act < 1
    || !Number.isSafeInteger(save.floor) || save.floor < 0) {
    throw new Error("managed_map_continue_identity_required");
  }
  savePath(save.path);
  const current = candidate(started);
  if (Object.keys(current).some(key => current[key] !== save.candidate?.[key])) {
    throw new Error("managed_map_continue_candidate_changed");
  }
  const file = fileIdentity(save.path);
  if (file.bytes !== save.bytes || file.sha256 !== save.sha256) {
    throw new Error("managed_map_continue_file_changed");
  }
  return save;
}
