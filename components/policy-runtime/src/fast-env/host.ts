import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { createHash, randomUUID } from "node:crypto";
import { verifyPublicBasis, type FullPublicBasis } from "@rsgcsg/sts2-connector-client";
import { canonicalJson } from "../evidence.js";
import type { FastEnvConfig } from "./contracts.js";
import type { FastFrameRef } from "./evidence-sink.js";

/**
 * One slot's native game, driven in-process through the Host's own driver
 * (`ManagedPeDriverSession.handle`), so every check the Host makes today (control
 * epoch, expected episode, snapshot binding, execute-time revalidation) is kept.
 * No HTTP, no attachment files. The slot kernel never sees native operands.
 */
export interface SlotNative {
  readonly runtimeInstanceId: string;
  /** Mounts a new episode (reset when the process already ran one) and claims control. */
  reset(input: { seed: string; initialProgress: Record<string, unknown> | null; r7: boolean }): Promise<NativeEpisode>;
  /** Loads a Host map save (Host `load_map`; this process must be fresh) and claims control. */
  loadSave(input: { save: Record<string, unknown>; r7: boolean }): Promise<NativeEpisode>;
  /** A sealed public basis of the current state: light-checked every capture, fully
   * verified on the sampled ones (throws SampledCheckFailure when that disagrees). */
  capture(): Promise<FullPublicBasis>;
  /** How many captures of the current episode got the full check so far. */
  fullChecks(): number;
  /** Submits one Host BoundAction. Resolves with the Host's original receipt object. */
  step(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }): Promise<unknown>;
  /** Host `save_map` of the current settled map state into `path` (absolute, new). Control is handed back
   * for the save (the Host refuses a save while it is held) and claimed again after it. `refused`: one of the
   * Host's known refusals before any file reservation or native write, and the game is as it was. Throws
   * SaveMapFailure on anything else (the native save failed or its outcome is unknown, a timeout, a drift):
   * this native is not used again. */
  saveMap(path: string): Promise<SaveMapResult>;
  /** Gives control back between games. */
  release(): Promise<void>;
  taint(): { tainted: boolean; reason: string | null };
  /** Evidence: receives every compact line of the current episode in order, starting
   * with the lines read while it was mounted; null detaches. Without compact output the only
   * line can be the native's own compact_state_divergence report. */
  attachLines(listener: ((line: string) => void) | null): void;
  /** The last compact frame read (the state a step is bound to); null without compact output. */
  frame(): FastFrameRef | null;
  /** The native combat_end facts (managed-native-compact-1 rule 12) of the frames read since the
   * last call, unchanged and in order; empty without compact output. The kernel only carries them
   * to the step outcome; nothing reads them before the fair-projection review. */
  takeCombatEnd(): Record<string, unknown>[];
  nativeCpuMs(): Promise<number | null>;
  /** E5 counters of this native's Host session since it started (null when the projection memo is off). */
  projectionMemo?(): { hits: number; misses: number; checks: number } | null;
  shutdown(force: boolean): Promise<void>;
}

/** `save` is the Host's own `managed-map-continue-1` descriptor, unchanged. */
export type SaveMapResult = { kind: "saved"; save: Record<string, unknown> } | { kind: "refused"; reason: string };

/** The Host ended the episode during a save: `unknown` = the Host could not tell whether the native wrote it. */
export class SaveMapFailure extends Error {
  constructor(readonly code: string, readonly outcome: "failed" | "unknown") {
    super(`${outcome === "unknown" ? "save_unknown" : "save_failed"}:${code}`);
    this.name = "SaveMapFailure";
  }
}

/** The Host's code for a save whose native outcome is unknown (managed-map-continue.mjs ManagedMapContinueUnknown). */
const SAVE_UNKNOWN = "managed_map_continue_unknown_replace_process";
/** The Host's save_map refusals thrown before it reserves the file or asks the native anything that could
 * leave a reply behind (managed-pe-driver-session.mjs save_map, managed-map-continue.mjs). A quiescence
 * refusal after a completed run_identity reply is one of them: that reply was consumed. */
const SAVE_REFUSED_BEFORE_WRITE = new Set(["managed_control_held", "managed_map_continue_absolute_path_required",
  "managed_map_continue_candidate_identity_required", "managed_map_continue_quiescent_map_required"]);
function saveRefusedBeforeWrite(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  // The reservation itself (openSync(path, "wx")) found the path taken: nothing was written or asked.
  if ((error as NodeJS.ErrnoException).code === "EEXIST") return true;
  return SAVE_REFUSED_BEFORE_WRITE.has(error.message);
}

/** `start` is what the native's first compact frame says (null when compact output is off). */
export interface NativeEpisode {
  gameInstanceId: string | null;
  start: { kind: "new_run" } | { kind: "from_save"; save_sha256: string } | null;
}

/**
 * The fast chain's game_instance_id: the native episode id namespaced by this Host
 * launch's own runtime instance id (a fresh random id per Host start). The native id
 * (`native-compact:{pid}:{counter}`) is only unique inside one machine, and PIDs repeat
 * across env nodes; the Host owns exact identity, so it scopes the id here, once, where
 * the id enters the fast chain. The native fact itself is unchanged (compact lines and
 * frame refs keep `episode_instance` as the native wrote it; the capture sample draw
 * stays keyed on it). Downstream treats the result as an opaque id.
 */
export function fastGameInstanceId(runtimeInstanceId: string, nativeEpisodeId: string): string {
  return `${runtimeInstanceId}:${nativeEpisodeId}`;
}

export interface SlotNativeFactory {
  start(): Promise<SlotNative>;
  readonly hostVersion: string;
}

/** A sampled full check (byte re-hash, JSON re-parse, strict decode, basis equality)
 * disagreed with what the light check accepted. The kernel stops this build's output. */
export class SampledCheckFailure extends Error {
  /** The originals the light path accepted, exactly as the Host returned them. */
  readonly mismatch: SampledCheckMismatch;
  constructor(cause: unknown, original: FullPublicBasis, ordinal: number) {
    const text = cause instanceof Error ? cause.message : String(cause);
    super(`sampled_check_failed:${text}`, { cause });
    this.name = "SampledCheckFailure";
    this.mismatch = { capture_ordinal: ordinal, cause: text, capture: original.capture,
      serialized_observation: original.serializedObservation, basis: original.basis };
  }
}

/** One sampled full check that disagreed: the Host's handle, bytes and light basis. */
export interface SampledCheckMismatch {
  capture_ordinal: number;
  cause: string;
  capture: FullPublicBasis["capture"];
  serialized_observation: string;
  basis: FullPublicBasis["basis"];
}

/** The per-episode offset of the full-check draw: derived from the episode's own id, so a
 * re-run or an audit recomputes exactly which captures were fully checked. */
export function captureSampleOffset(episodeId: string): number {
  return createHash("sha256").update(`fast-env-full-check-v1\n${episodeId}`, "utf8").digest().readUInt32BE(0);
}

/** Which captures of one episode get the full check (design v2 §2.7), fixed before the
 * first capture: the first capture always, every capture for R7, else about `rate`. A
 * null offset (no episode id to derive it from) checks every capture. */
export function captureSampleSchedule(rate: number, all: boolean, offset: number | null): (ordinal: number) => boolean {
  if (all || rate >= 1 || offset === null) return () => true;
  if (rate <= 0) return ordinal => ordinal === 0;
  const threshold = Math.round(rate * 0x1_0000_0000);
  return ordinal => ordinal === 0 || (Math.imul(ordinal ^ offset, 0x9e3779b1) >>> 0) < threshold;
}

const MANAGER = { principal: "manager", sharedService: true } as const;
const CLIENT = { principal: "client", sharedService: true } as const;
const rid = () => randomUUID().replaceAll("-", "");

interface HostModules {
  startManagedPlayerEnvironmentSession(options: Record<string, unknown>): Promise<any>;
  ManagedPeDriverSession: new (started: unknown, options: { requestTimeoutMs: number }) => {
    handle(request: Record<string, unknown>, intent: Record<string, unknown>): Promise<any>;
    status(): { game_continuity_id: string | null; episode_available: boolean };
    shutdown(options?: { force?: boolean }): Promise<unknown>;
  };
  discoverGameDirectory(): string | null;
  resolveInstallation(directory: string): unknown;
  readDiskIdentity(installation: unknown): unknown;
}

async function loadHost(hostPackage: string): Promise<HostModules> {
  const load = (name: string) => import(pathToFileURL(join(hostPackage, "src", name)).href);
  const [environment, driver, installation] = await Promise.all([
    load("managed-player-environment.mjs"), load("managed-pe-driver-session.mjs"), load("game-installation.mjs")]);
  return { startManagedPlayerEnvironmentSession: environment.startManagedPlayerEnvironmentSession,
    ManagedPeDriverSession: driver.ManagedPeDriverSession, discoverGameDirectory: installation.discoverGameDirectory,
    resolveInstallation: installation.resolveInstallation, readDiskIdentity: installation.readDiskIdentity };
}

export class HostSlotNative implements SlotNative {
  /** The Host's credential exactly as issued (its epoch is an opaque id, not a number). */
  #control: { token: string; epoch: unknown } | null = null;
  #continuity: string | null = null;
  #lines: string[] = [];
  #listener: ((line: string) => void) | null = null;
  #combatEnd: Record<string, unknown>[] = [];
  #last: { line: string; episode_instance: string; seq: number; objects_root_sha256: string; sha?: string } | null = null;
  #sampled: (ordinal: number) => boolean = () => true;
  #captures = 0;
  #fullChecks = 0;
  #r7 = false;
  #drawKey: string | null = null;
  constructor(private readonly started: any, private readonly driver: InstanceType<HostModules["ManagedPeDriverSession"]>,
              private readonly sampleRate = 1) {
    started.session.compactLineTap = (line: string | null, frame: Record<string, unknown>) => {
      if (typeof line !== "string") return;
      // The native's own divergence report is an original line, not a frame a step acted on.
      if (frame.type === "compact_state") {
        this.#last = { line, episode_instance: String(frame.episode_instance), seq: Number(frame.seq), objects_root_sha256: String(frame.objects_root_sha256) };
        // Shape-checked by the Host reconstructor (facts.combat_end, at most four per frame).
        const facts = frame.facts as { combat_end?: unknown } | undefined;
        if (facts && Array.isArray(facts.combat_end)) for (const fact of facts.combat_end) this.#combatEnd.push(fact as Record<string, unknown>);
      }
      if (this.#listener) this.#listener(line); else this.#lines.push(line);
    };
  }

  /** A new episode starts with an empty line buffer and no listener. */
  #beginEpisode(r7: boolean, drawKey: string): void {
    this.#lines = []; this.#listener = null; this.#last = null; this.#combatEnd = [];
    this.#captures = 0; this.#fullChecks = 0; this.#r7 = r7; this.#drawKey = drawKey; this.#sampled = () => true;
  }

  fullChecks(): number { return this.#fullChecks; }

  attachLines(listener: ((line: string) => void) | null): void {
    this.#listener = listener;
    if (!listener) { this.#lines = []; return; }
    const buffered = this.#lines;
    this.#lines = [];
    for (const line of buffered) listener(line);
  }

  takeCombatEnd(): Record<string, unknown>[] {
    const facts = this.#combatEnd;
    if (facts.length > 0) this.#combatEnd = [];
    return facts;
  }

  frame(): FastFrameRef | null {
    const last = this.#last;
    if (!last) return null;
    last.sha ??= createHash("sha256").update(last.line, "utf8").digest("hex");
    return { episode_instance: last.episode_instance, seq: last.seq, objects_root_sha256: last.objects_root_sha256, line_sha256: last.sha };
  }

  get runtimeInstanceId(): string { return this.started.runtime.adapterRuntimeInstanceId; }

  async reset(input: { seed: string; initialProgress: Record<string, unknown> | null; r7: boolean }): Promise<NativeEpisode> {
    if (this.#control) await this.release();
    this.#beginEpisode(input.r7, `seed:${input.seed}`);
    await this.driver.handle({ command: "reset", request_id: rid(), seed: input.seed,
      ...(input.initialProgress ? { initial_progress: input.initialProgress } : {}),
      ...(input.r7 ? { compact_check_all: true } : {}),
      expected_runtime_instance_id: this.runtimeInstanceId }, MANAGER);
    return this.#claim();
  }

  async loadSave(input: { save: Record<string, unknown>; r7: boolean }): Promise<NativeEpisode> {
    if (this.#control) await this.release();
    this.#beginEpisode(input.r7, `save:${createHash("sha256").update(canonicalJson(input.save), "utf8").digest("hex")}`);
    await this.driver.handle({ command: "load_map", request_id: rid(), save: input.save,
      ...(input.r7 ? { compact_check_all: true } : {}),
      expected_runtime_instance_id: this.runtimeInstanceId, expected_game_continuity_id: null }, MANAGER);
    return this.#claim();
  }

  async #claimControl(): Promise<void> {
    const claim = await this.driver.handle({ command: "claim_control", request_id: rid(),
      expected_runtime_instance_id: this.runtimeInstanceId, expected_game_continuity_id: this.#continuity }, CLIENT);
    if (claim?.type !== "claim_control_result") throw new Error("fast_env_claim_result_drift");
    if (typeof claim.control_token !== "string" || claim.control_epoch == null) throw new Error("fast_env_claim_result_drift");
    this.#control = { token: claim.control_token, epoch: claim.control_epoch };
  }

  async saveMap(path: string): Promise<SaveMapResult> {
    await this.release();
    let result: any;
    try {
      result = await this.driver.handle({ command: "save_map", request_id: rid(), path,
        expected_runtime_instance_id: this.runtimeInstanceId, expected_game_continuity_id: this.#continuity }, MANAGER);
    } catch (error) {
      const code = error instanceof Error ? error.message : String(error);
      const status = this.driver.status();
      // Only the Host's known refusals before any file reservation or native write leave the game as it
      // was. Anything else (a native request timeout whose late reply may still be queued, a drift, the
      // Host clearing the episode after a failed or unknown native save) ends it and replaces the process.
      if (status.episode_available && status.game_continuity_id === this.#continuity && saveRefusedBeforeWrite(error)) {
        await this.#claimControl();
        return { kind: "refused", reason: code };
      }
      throw new SaveMapFailure(code, code === SAVE_UNKNOWN ? "unknown" : "failed");
    }
    if (result?.type !== "save_map_result" || result.save === null || typeof result.save !== "object")
      throw new SaveMapFailure("fast_env_save_result_drift", "failed");
    // The capture sample schedule of this episode stays as it was fixed at mount.
    await this.#claimControl();
    return { kind: "saved", save: result.save };
  }

  async #claim(): Promise<NativeEpisode> {
    this.#continuity = this.driver.status().game_continuity_id;
    await this.#claimControl();
    const episodeId: string | null = this.started.session.episodeInstance ?? null;
    // Fixed after mount and before the first capture: no model choice can move it. The
    // compact episode id when there is one, else what the open itself named (seed / save).
    const drawKey = episodeId === null ? this.#drawKey : `episode:${episodeId}`;
    this.#sampled = captureSampleSchedule(this.sampleRate, this.#r7, drawKey === null ? null : captureSampleOffset(drawKey));
    return { gameInstanceId: episodeId === null ? null : fastGameInstanceId(this.runtimeInstanceId, episodeId),
      start: this.started.session.episodeStart ?? null };
  }

  async capture(): Promise<FullPublicBasis> {
    // The Host always runs the light check here (every semantic gate, no strict shape
    // decode); the sampled captures re-hash the bytes, re-parse them and decode strictly.
    const result = await this.driver.handle({ command: "capture", request_id: rid(), full_check: false,
      expected_runtime_instance_id: this.runtimeInstanceId, expected_game_continuity_id: this.#continuity }, CLIENT);
    if (result?.type !== "capture_result") throw new Error("fast_env_capture_response_type");
    const full = result.full as FullPublicBasis;
    if (full.capture.game_continuity_id !== this.#continuity
        || full.capture.session.runtime_instance_id !== this.runtimeInstanceId
        || full.capture.source_snapshot_id !== full.basis.snapshot.snapshot_id
        || typeof full.serializedObservation !== "string") throw new Error("fast_env_capture_world_drift");
    const ordinal = this.#captures++;
    if (!this.#sampled(ordinal)) return full;
    this.#fullChecks += 1;
    try {
      const verified = await verifyPublicBasis(full.capture, full.serializedObservation);
      if (canonicalJson(verified.basis) !== canonicalJson(full.basis)) throw new Error("basis_differs_from_bytes");
      return verified;
    } catch (error) { throw new SampledCheckFailure(error, full, ordinal); }
  }

  async step(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }): Promise<unknown> {
    const control = this.#control;
    if (!control) throw new Error("fast_env_control_not_held");
    const result = await this.driver.handle({ command: "step", request_id: rid(), mutation_request_id: input.requestId,
      expected_snapshot_id: input.expectedSnapshotId, bound_action_id: input.boundActionId,
      control_token: control.token, control_epoch: control.epoch,
      expected_runtime_instance_id: this.runtimeInstanceId, expected_game_continuity_id: this.#continuity }, CLIENT);
    if (result?.type !== "step_result") throw new Error("fast_env_step_result_type");
    return result.receipt;
  }

  async release(): Promise<void> {
    const control = this.#control;
    if (!control) return;
    this.#control = null;
    await this.driver.handle({ command: "release_control", request_id: rid(), control_token: control.token, control_epoch: control.epoch,
      expected_runtime_instance_id: this.runtimeInstanceId, expected_game_continuity_id: this.#continuity }, CLIENT);
  }

  taint() { return { tainted: this.started.session.tainted === true, reason: this.started.session.taintReason ?? null }; }

  async nativeCpuMs(): Promise<number | null> {
    try { const metrics = await this.started.session.processMetrics(5_000); return typeof metrics?.cpu_total_ms === "number" ? metrics.cpu_total_ms : null; }
    catch { return null; }
  }

  projectionMemo(): { hits: number; misses: number; checks: number } | null {
    const stats = this.started.session.projectionMemo;
    return stats ? { hits: stats.hits, misses: stats.misses, checks: stats.checks } : null;
  }

  async shutdown(force: boolean): Promise<void> {
    this.#control = null;
    await this.driver.shutdown({ force });
  }
}

export async function hostSlotNativeFactory(config: FastEnvConfig): Promise<SlotNativeFactory> {
  const host = await loadHost(config.host.package);
  const version = String(JSON.parse(readFileSync(join(config.host.package, "package.json"), "utf8")).version);
  const gameDirectory = config.host.game_directory ?? host.discoverGameDirectory();
  if (!gameDirectory) throw new Error("fast_env_game_directory_unavailable");
  const diskIdentity = host.readDiskIdentity(host.resolveInstallation(gameDirectory));
  return {
    hostVersion: version,
    async start() {
      const started = await host.startManagedPlayerEnvironmentSession({
        root: config.host.package, candidateDirectory: config.host.candidate_directory, diskIdentity,
        character: config.host.character, language: config.host.language, ascension: config.host.ascension,
        // This process is fast_chain only: the native fast path is always on here.
        requestTimeoutMs: config.host.request_timeout_ms, quietDiagnostics: true, fastChain: true,
        // Strict SDK decodes of the projection run on the sampled captures (verifyPublicBasis)
        // and on every receipt in the kernel; the Host does not repeat them per step.
        validateSdk: false,
        ...(config.compact.enabled ? { compactOutput: { reconcileInterval: config.compact.reconcile_interval, sampleRate: config.checks.sample_rate, shareUnchanged: true } } : {}),
        // E5: unchanged subtrees of driver.handle's basis stay the same frozen objects (kernel/SDK caches hit).
        ...(config.compact.enabled && config.compact.projection_memo ? { projectionMemo: { checkInterval: config.compact.projection_memo.check_interval } } : {}),
        // #460: the native card projection memo lanes (each off unless named).
        ...(config.memo.native_card?.deck ? { deckMemo: true } : {}),
        ...(config.memo.native_card?.hud ? { hudMemo: true } : {})
      });
      return new HostSlotNative(started, new host.ManagedPeDriverSession(started, { requestTimeoutMs: config.host.request_timeout_ms }),
        config.checks.sample_rate);
    }
  };
}
