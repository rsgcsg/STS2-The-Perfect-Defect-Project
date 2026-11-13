/**
 * K-game environment process contracts (P-REWRITE fast_chain only; the old chain
 * never starts this process). The interface document for the manager (WP7) and the
 * node aggregator (WP3+4) lives with the WP2 result; this file is its executable form.
 */

export const FAST_ENV_CONFIG_SCHEMA = "sts2.policy-runtime/fast-env-config-1" as const;
export const FAST_ENV_COMMAND_SCHEMA = "sts2.policy-runtime/fast-env-command-1" as const;
export const FAST_ENV_EVENT_SCHEMA = "sts2.policy-runtime/fast-env-event-1" as const;
export const FAST_ENV_LINK_SCHEMA = "sts2.policy-runtime/fast-env-link-1" as const;
export const MAX_SLOTS = 16;

export interface ClassPolicy { temperature: number; uniform_mix: number }
/** Exactly rl-inference-4 `sampling`; carried to the aggregator unchanged. */
export interface Sampling {
  mode: "temperature_uniform"; seed: string; arm_id: string;
  classes: { combat: ClassPolicy; noncombat: ClassPolicy };
}

export interface FastEnvConfig {
  schema: typeof FAST_ENV_CONFIG_SCHEMA;
  env_process_id: string;
  slots: number;
  host: {
    package: string; candidate_directory: string; game_directory: string | null;
    character: string; language: string; ascension: number; request_timeout_ms: number;
  };
  agent_manifest: string;
  /** `projection_memo` (E5, off when absent or null; needs `enabled`): the Host keeps unchanged
   *  projected subtrees as the same deep-frozen objects across steps and compares a memo-off
   *  projection on frame 0 and then after random gaps uniform in ceil(N/2)..N, N = `check_interval`.
   *  A difference is `projection_memo_divergence`: the game ends unknown (never retried), is counted
   *  in `projection_memo_divergences` and stops this build's output. */
  compact: { enabled: boolean; reconcile_interval: number; projection_memo: { check_interval: number } | null };
  /** The other memo layers (WP8 v3 enablement; all off when `memo` is absent; a `reduce` key, the removed
   *  E6 SDK reduce memo, is refused). This process is fast_chain only.
   *  - `native_card` (#460): the native's deck / HUD card projection memo (STS2_HEADLESS_DECK_MEMO / _HUD_MEMO);
   *    the native compares every 64th render frame of a lane itself; a difference is compact_state_divergence.
   *  - `receipt_canonical` (E4): the receipt digest keeps the canonical text of deep-frozen subtrees;
   *    compared against the full walk on digest 0 and then after random gaps in ceil(N/2)..N; a
   *    difference is fatal for this whole process.
   *  E4 only hits when E5 (`compact.projection_memo`) is on: it keys on object identity, and only
   *  E5 hands out unchanged subtrees as the same deep-frozen objects. Without E5 it stays correct but
   *  is pure overhead (hits ≈ 0); allowed on their own so each layer can still be measured. */
  memo: { native_card: { deck: boolean; hud: boolean } | null;
    receipt_canonical: { check_interval: number } | null };
  /** `reduce_light` (C1, off when absent or null): a basis that passed the Host light check in this
   *  process skips the SDK reduce's repeated strict zod decode of its snapshot and Reads (and the
   *  repeated finite-number walk); the first one and then each with probability 1/`strict_interval`
   *  still decode strictly, and a failing sample stops this build's output (`reduce_strict_sample_failed`).
   *  A basis whose persistent part fails the strict persistent schema always decodes strictly.
   *  The sampled Host full checks (`sample_rate`) are unchanged. */
  checks: { sample_rate: number; reduce_light: { strict_interval: number } | null };
  decision_link: { kind: "unix"; path: string };
  inference: { timeout_ms: number; actor_id: string; sampling: Sampling; single_choice: "score" | "omit" };
  /** Under `single_choice: "omit"` `max_decisions_per_game` counts model rows only (multi-choice
   *  decisions); forced one-option steps are not decisions, so a game runs more native steps per
   *  decision than under `score`. Do not reuse an old-chain per-game decision cap unchanged; the
   *  native-step and wall caps bound the whole game in both modes. */
  budget: { max_decisions_per_game: number; max_native_steps_per_game: number; game_wall_ms: number };
  no_progress: { window_decisions: number; max_repeats: number } | null;
  settling_timeout_ms: number;
  /** Native cold starts (Host start + first reset) at once across this node's fast-env processes
   *  (WP8 §8.13); directory null = the node-local default. Default 10. */
  cold_start: { max_concurrent: number; directory: string | null };
  /** boundary_rows: model rows of one game between two `segmentBoundary` calls (default 64). */
  evidence: { directory: string; boundary_rows: number };
}

/** `save` = the Host `save_map` result object, passed to the Host `load_map` unchanged (the Host verifies it). */
export type HostSave = Record<string, unknown> & { sha256: string; act: number; floor: number };
export type GameStart = { kind: "new_run" } | { kind: "from_save"; save: HostSave };

export type FastEnvCommand =
  | { cmd: "open"; command_id: string; slot: number; game_ref: string; seed: string;
      initial_progress: Record<string, unknown> | null; start: GameStart; r7: boolean; retain_originals: boolean }
  /** game_instance_id (optional): the game this close is for; a close for any other game is ignored and counted. */
  | { cmd: "close_slot"; command_id: string; slot: number; game_instance_id: string | null }
  | { cmd: "stop"; command_id: string }
  | { cmd: "status"; command_id: string };

/** rl-inference-4 episode_close.reason. */
export type CloseReason = "terminal" | "not_started" | "unknown" | "stop" | "budget" | "env_crash";
/** rl-inference-4 step_outcome.reason, plus native_not_delivered (see the result doc). */
export type OutcomeReason = "inference_timeout" | "response_binding_mismatch" | "refused" | "stop_before_send"
  | "delivery_unknown" | "native_crash" | "compact_state_divergence" | "native_not_delivered";

export interface PublicFacts { act: number; floor: number; hp: number; max_hp: number }

export interface ScoredReply {
  type: "scored"; row_id: number; game_instance_id: string; catalog_sha256: string;
  mu_snapshot_sha256: string; segment_id: string; row_in_segment: number; selected_index: number;
  behavior_q: number[]; selected_q: number;
  applied: { arm_id: string; decision_class: "combat" | "noncombat"; temperature: number; uniform_mix: number };
}
export interface RefusedReply { type: "refused"; row_id: number; game_instance_id: string; catalog_sha256: string; error_code: string }
export type LinkReply = ScoredReply | RefusedReply;

export class FastEnvContractError extends Error {
  constructor(readonly field: string) { super(`fast_env_contract:${field}`); this.name = "FastEnvContractError"; }
}

const ID = /^[A-Za-z0-9._:-]{1,128}$/u;
const SHA = /^[0-9a-f]{64}$/u;
type Obj = Record<string, unknown>;
const isObj = (value: unknown): value is Obj => value !== null && typeof value === "object" && !Array.isArray(value);

function obj(value: unknown, field: string, keys?: readonly string[]): Obj {
  if (!isObj(value)) throw new FastEnvContractError(field);
  if (keys) for (const key of Object.keys(value)) if (!keys.includes(key)) throw new FastEnvContractError(`${field}.${key}`);
  return value;
}
function str(value: unknown, field: string, pattern?: RegExp): string {
  if (typeof value !== "string" || value.length === 0 || (pattern && !pattern.test(value))) throw new FastEnvContractError(field);
  return value;
}
function int(value: unknown, field: string, min: number, max: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < min || (value as number) > max) throw new FastEnvContractError(field);
  return value as number;
}
function num(value: unknown, field: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) throw new FastEnvContractError(field);
  return value;
}
function bool(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new FastEnvContractError(field);
  return value;
}

function classPolicy(value: unknown, field: string): ClassPolicy {
  const v = obj(value, field, ["temperature", "uniform_mix"]);
  return { temperature: num(v.temperature, `${field}.temperature`, 1, 3), uniform_mix: num(v.uniform_mix, `${field}.uniform_mix`, 0, 0.1) };
}

export function parseSampling(value: unknown, field = "sampling"): Sampling {
  const v = obj(value, field, ["mode", "seed", "arm_id", "classes"]);
  if (v.mode !== "temperature_uniform") throw new FastEnvContractError(`${field}.mode`);
  const seed = str(v.seed, `${field}.seed`);
  if (seed.length > 128) throw new FastEnvContractError(`${field}.seed`);
  const classes = obj(v.classes, `${field}.classes`, ["combat", "noncombat"]);
  return { mode: "temperature_uniform", seed, arm_id: str(v.arm_id, `${field}.arm_id`, ID),
    classes: { combat: classPolicy(classes.combat, `${field}.classes.combat`), noncombat: classPolicy(classes.noncombat, `${field}.classes.noncombat`) } };
}

export function parseFastEnvConfig(value: unknown): FastEnvConfig {
  const v = obj(value, "config", ["schema", "env_process_id", "slots", "host", "agent_manifest", "compact", "checks",
    "decision_link", "inference", "budget", "no_progress", "settling_timeout_ms", "cold_start", "evidence", "memo"]);
  if (v.schema !== FAST_ENV_CONFIG_SCHEMA) throw new FastEnvContractError("schema");
  const host = obj(v.host, "host", ["package", "candidate_directory", "game_directory", "character", "language", "ascension", "request_timeout_ms"]);
  const compact = obj(v.compact, "compact", ["enabled", "reconcile_interval", "projection_memo"]);
  let projectionMemo: FastEnvConfig["compact"]["projection_memo"] = null;
  if (compact.projection_memo !== null && compact.projection_memo !== undefined) {
    const m = obj(compact.projection_memo, "compact.projection_memo", ["check_interval"]);
    projectionMemo = { check_interval: int(m.check_interval, "compact.projection_memo.check_interval", 1, 1024) };
    if (compact.enabled !== true) throw new FastEnvContractError("compact.projection_memo");
  }
  const memo = obj(v.memo ?? {}, "memo", ["native_card", "receipt_canonical"]);
  const interval = (value: unknown, field: string) => value === null || value === undefined ? null
    : { check_interval: int(obj(value, field, ["check_interval"]).check_interval, `${field}.check_interval`, 1, 1024) };
  let nativeCard: FastEnvConfig["memo"]["native_card"] = null;
  if (memo.native_card !== null && memo.native_card !== undefined) {
    const n = obj(memo.native_card, "memo.native_card", ["deck", "hud"]);
    nativeCard = { deck: bool(n.deck, "memo.native_card.deck"), hud: bool(n.hud, "memo.native_card.hud") };
  }
  const checks = obj(v.checks, "checks", ["sample_rate", "reduce_light"]);
  const reduceLight = checks.reduce_light === null || checks.reduce_light === undefined ? null
    : { strict_interval: int(obj(checks.reduce_light, "checks.reduce_light", ["strict_interval"]).strict_interval,
      "checks.reduce_light.strict_interval", 1, 1024) };
  const link = obj(v.decision_link, "decision_link", ["kind", "path"]);
  if (link.kind !== "unix") throw new FastEnvContractError("decision_link.kind");
  const inference = obj(v.inference, "inference", ["timeout_ms", "actor_id", "sampling", "single_choice"]);
  if (inference.single_choice !== "score" && inference.single_choice !== "omit") throw new FastEnvContractError("inference.single_choice");
  const budget = obj(v.budget, "budget", ["max_decisions_per_game", "max_native_steps_per_game", "game_wall_ms"]);
  const evidence = obj(v.evidence, "evidence", ["directory", "boundary_rows"]);
  const coldStart = obj(v.cold_start ?? {}, "cold_start", ["max_concurrent", "directory"]);
  let noProgress: FastEnvConfig["no_progress"] = null;
  if (v.no_progress !== null && v.no_progress !== undefined) {
    const p = obj(v.no_progress, "no_progress", ["window_decisions", "max_repeats"]);
    const window = int(p.window_decisions, "no_progress.window_decisions", 2, 256);
    noProgress = { window_decisions: window, max_repeats: int(p.max_repeats, "no_progress.max_repeats", 2, window) };
  }
  return {
    schema: FAST_ENV_CONFIG_SCHEMA,
    env_process_id: str(v.env_process_id, "env_process_id", ID),
    slots: int(v.slots, "slots", 1, MAX_SLOTS),
    host: {
      package: str(host.package, "host.package"), candidate_directory: str(host.candidate_directory, "host.candidate_directory"),
      game_directory: host.game_directory == null ? null : str(host.game_directory, "host.game_directory"),
      character: str(host.character, "host.character"), language: str(host.language, "host.language"),
      ascension: int(host.ascension, "host.ascension", 0, 10),
      request_timeout_ms: int(host.request_timeout_ms, "host.request_timeout_ms", 1000, 600_000)
    },
    agent_manifest: str(v.agent_manifest, "agent_manifest"),
    compact: { enabled: bool(compact.enabled, "compact.enabled"), reconcile_interval: int(compact.reconcile_interval, "compact.reconcile_interval", 1, 1024),
      projection_memo: projectionMemo },
    memo: { native_card: nativeCard,
      receipt_canonical: interval(memo.receipt_canonical, "memo.receipt_canonical") },
    checks: { sample_rate: num(checks.sample_rate, "checks.sample_rate", 0, 1), reduce_light: reduceLight },
    decision_link: { kind: "unix", path: str(link.path, "decision_link.path") },
    inference: { timeout_ms: int(inference.timeout_ms, "inference.timeout_ms", 1, 600_000),
      actor_id: str(inference.actor_id, "inference.actor_id", ID), sampling: parseSampling(inference.sampling, "inference.sampling"),
      single_choice: inference.single_choice },
    budget: { max_decisions_per_game: int(budget.max_decisions_per_game, "budget.max_decisions_per_game", 1, 1_000_000),
      max_native_steps_per_game: int(budget.max_native_steps_per_game, "budget.max_native_steps_per_game", 1, 1_000_000),
      game_wall_ms: int(budget.game_wall_ms, "budget.game_wall_ms", 1, 86_400_000) },
    no_progress: noProgress,
    settling_timeout_ms: int(v.settling_timeout_ms ?? 30_000, "settling_timeout_ms", 50, 600_000),
    cold_start: { max_concurrent: int(coldStart.max_concurrent ?? 10, "cold_start.max_concurrent", 1, 1024),
      directory: coldStart.directory == null ? null : str(coldStart.directory, "cold_start.directory") },
    evidence: { directory: str(evidence.directory, "evidence.directory"),
      boundary_rows: int(evidence.boundary_rows ?? 64, "evidence.boundary_rows", 1, 4096) }
  };
}

export function parseFastEnvCommand(value: unknown): FastEnvCommand {
  const v = obj(value, "command");
  if (v.schema !== FAST_ENV_COMMAND_SCHEMA) throw new FastEnvContractError("command.schema");
  const commandId = str(v.command_id, "command.command_id", ID);
  switch (v.cmd) {
    case "open": {
      obj(v, "open", ["schema", "cmd", "command_id", "slot", "game_ref", "seed", "initial_progress", "start", "r7", "retain_originals"]);
      const start = obj(v.start, "open.start");
      let parsedStart: GameStart;
      if (start.kind === "new_run" && Object.keys(start).length === 1) parsedStart = { kind: "new_run" };
      else if (start.kind === "from_save" && Object.keys(start).length === 2) {
        const save = obj(start.save, "open.start.save");
        str(save.sha256, "open.start.save.sha256", /^[0-9a-f]{64}$/u);
        int(save.act, "open.start.save.act", 1, 100); int(save.floor, "open.start.save.floor", 0, 1000);
        parsedStart = { kind: "from_save", save: save as HostSave };
      }
      else throw new FastEnvContractError("open.start");
      return { cmd: "open", command_id: commandId, slot: int(v.slot, "open.slot", 0, MAX_SLOTS - 1),
        game_ref: str(v.game_ref, "open.game_ref", ID), seed: str(v.seed, "open.seed"),
        initial_progress: v.initial_progress == null ? null : obj(v.initial_progress, "open.initial_progress"),
        start: parsedStart, r7: v.r7 === undefined ? false : bool(v.r7, "open.r7"),
        retain_originals: v.retain_originals === undefined ? false : bool(v.retain_originals, "open.retain_originals") };
    }
    case "close_slot":
      obj(v, "close_slot", ["schema", "cmd", "command_id", "slot", "game_instance_id"]);
      return { cmd: "close_slot", command_id: commandId, slot: int(v.slot, "close_slot.slot", 0, MAX_SLOTS - 1),
        game_instance_id: v.game_instance_id === undefined ? null : str(v.game_instance_id, "close_slot.game_instance_id", ID) };
    case "stop":
      obj(v, "stop", ["schema", "cmd", "command_id"]);
      return { cmd: "stop", command_id: commandId };
    case "status":
      obj(v, "status", ["schema", "cmd", "command_id"]);
      return { cmd: "status", command_id: commandId };
    default: throw new FastEnvContractError("command.cmd");
  }
}

/** Structural decode of one aggregator reply. Binding to the pending row is separate. */
export function parseLinkReply(value: unknown): LinkReply {
  const v = obj(value, "reply");
  if (v.schema !== FAST_ENV_LINK_SCHEMA) throw new FastEnvContractError("reply.schema");
  const rowId = int(v.row_id, "reply.row_id", 1, Number.MAX_SAFE_INTEGER);
  const gid = str(v.game_instance_id, "reply.game_instance_id", ID);
  const catalog = str(v.catalog_sha256, "reply.catalog_sha256", SHA);
  if (v.type === "refused") {
    obj(v, "refused", ["schema", "type", "row_id", "game_instance_id", "catalog_sha256", "error_code"]);
    return { type: "refused", row_id: rowId, game_instance_id: gid, catalog_sha256: catalog, error_code: str(v.error_code, "reply.error_code") };
  }
  if (v.type !== "scored") throw new FastEnvContractError("reply.type");
  obj(v, "scored", ["schema", "type", "row_id", "game_instance_id", "catalog_sha256", "mu_snapshot_sha256", "segment_id",
    "row_in_segment", "selected_index", "behavior_q", "selected_q", "applied"]);
  if (!Array.isArray(v.behavior_q) || v.behavior_q.length === 0) throw new FastEnvContractError("reply.behavior_q");
  const q = v.behavior_q.map((item, index) => num(item, `reply.behavior_q.${index}`, 0, 1));
  const applied = obj(v.applied, "reply.applied", ["arm_id", "decision_class", "temperature", "uniform_mix"]);
  if (applied.decision_class !== "combat" && applied.decision_class !== "noncombat") throw new FastEnvContractError("reply.applied.decision_class");
  const selectedQ = num(v.selected_q, "reply.selected_q", 0, 1);
  if (selectedQ <= 0) throw new FastEnvContractError("reply.selected_q");
  return { type: "scored", row_id: rowId, game_instance_id: gid, catalog_sha256: catalog,
    mu_snapshot_sha256: str(v.mu_snapshot_sha256, "reply.mu_snapshot_sha256", SHA), segment_id: str(v.segment_id, "reply.segment_id", ID),
    row_in_segment: int(v.row_in_segment, "reply.row_in_segment", 0, 63), selected_index: int(v.selected_index, "reply.selected_index", 0, Number.MAX_SAFE_INTEGER),
    behavior_q: q, selected_q: selectedQ,
    applied: { arm_id: str(applied.arm_id, "reply.applied.arm_id", ID), decision_class: applied.decision_class,
      temperature: num(applied.temperature, "reply.applied.temperature", 1, 3), uniform_mix: num(applied.uniform_mix, "reply.applied.uniform_mix", 0, 0.1) } };
}
