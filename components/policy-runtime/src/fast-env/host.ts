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
  /** Gives control back between games. */
  release(): Promise<void>;
  taint(): { tainted: boolean; reason: string | null };
  /** Evidence: receives every compact line of the current episode in order, starting
   * with the lines read while it was mounted; null detaches. No-op without compact output. */
  attachLines(listener: ((line: string) => void) | null): void;
  /** The last compact frame read (the state a step is bound to); null without compact output. */
  frame(): FastFrameRef | null;
  nativeCpuMs(): Promise<number | null>;
  shutdown(force: boolean): Promise<void>;
}

/** `start` is what the native's first compact frame says (null when compact output is off). */
export interface NativeEpisode {
  gameInstanceId: string | null;
  start: { kind: "new_run" } | { kind: "from_save"; save_sha256: string } | null;
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
    status(): { game_continuity_id: string | null };
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
      if (frame.type === "compact_state") this.#last = { line, episode_instance: String(frame.episode_instance), seq: Number(frame.seq), objects_root_sha256: String(frame.objects_root_sha256) };
      if (this.#listener) this.#listener(line); else this.#lines.push(line);
    };
  }

  /** A new episode starts with an empty line buffer and no listener. */
  #beginEpisode(r7: boolean, drawKey: string): void {
    this.#lines = []; this.#listener = null; this.#last = null;
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

  async #claim(): Promise<NativeEpisode> {
    this.#continuity = this.driver.status().game_continuity_id;
    const claim = await this.driver.handle({ command: "claim_control", request_id: rid(),
      expected_runtime_instance_id: this.runtimeInstanceId, expected_game_continuity_id: this.#continuity }, CLIENT);
    if (claim?.type !== "claim_control_result") throw new Error("fast_env_claim_result_drift");
    if (typeof claim.control_token !== "string" || claim.control_epoch == null) throw new Error("fast_env_claim_result_drift");
    this.#control = { token: claim.control_token, epoch: claim.control_epoch };
    const episodeId: string | null = this.started.session.episodeInstance ?? null;
    // Fixed after mount and before the first capture: no model choice can move it. The
    // compact episode id when there is one, else what the open itself named (seed / save).
    const drawKey = episodeId === null ? this.#drawKey : `episode:${episodeId}`;
    this.#sampled = captureSampleSchedule(this.sampleRate, this.#r7, drawKey === null ? null : captureSampleOffset(drawKey));
    return { gameInstanceId: episodeId, start: this.started.session.episodeStart ?? null };
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
        ...(config.compact.enabled ? { compactOutput: { reconcileInterval: config.compact.reconcile_interval, sampleRate: config.checks.sample_rate } } : {})
      });
      return new HostSlotNative(started, new host.ManagedPeDriverSession(started, { requestTimeoutMs: config.host.request_timeout_ms }),
        config.checks.sample_rate);
    }
  };
}
