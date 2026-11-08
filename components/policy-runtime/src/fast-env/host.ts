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
  /** A sealed, verified public basis of the current state. */
  capture(): Promise<FullPublicBasis>;
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

class HostSlotNative implements SlotNative {
  #control: { token: string; epoch: number } | null = null;
  #continuity: string | null = null;
  #lines: string[] = [];
  #listener: ((line: string) => void) | null = null;
  #last: { line: string; episode_instance: string; seq: number; objects_root_sha256: string; sha?: string } | null = null;
  constructor(private readonly started: any, private readonly driver: InstanceType<HostModules["ManagedPeDriverSession"]>) {
    started.session.compactLineTap = (line: string | null, frame: Record<string, unknown>) => {
      if (typeof line !== "string") return;
      this.#last = { line, episode_instance: String(frame.episode_instance), seq: Number(frame.seq), objects_root_sha256: String(frame.objects_root_sha256) };
      if (this.#listener) this.#listener(line); else this.#lines.push(line);
    };
  }

  /** A new episode starts with an empty line buffer and no listener. */
  #beginEpisode(): void { this.#lines = []; this.#listener = null; this.#last = null; }

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
    this.#beginEpisode();
    await this.driver.handle({ command: "reset", request_id: rid(), seed: input.seed,
      ...(input.initialProgress ? { initial_progress: input.initialProgress } : {}),
      ...(input.r7 ? { compact_check_all: true } : {}),
      expected_runtime_instance_id: this.runtimeInstanceId }, MANAGER);
    return this.#claim();
  }

  async loadSave(input: { save: Record<string, unknown>; r7: boolean }): Promise<NativeEpisode> {
    if (this.#control) await this.release();
    this.#beginEpisode();
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
    this.#control = { token: String(claim.control_token), epoch: Number(claim.control_epoch) };
    return { gameInstanceId: this.started.session.episodeInstance ?? null, start: this.started.session.episodeStart ?? null };
  }

  async capture(): Promise<FullPublicBasis> {
    const result = await this.driver.handle({ command: "capture", request_id: rid(),
      expected_runtime_instance_id: this.runtimeInstanceId, expected_game_continuity_id: this.#continuity }, CLIENT);
    if (result?.type !== "capture_result") throw new Error("fast_env_capture_response_type");
    // Same verification as the Managed transport today (slice 3 makes this sampled).
    const verified = await verifyPublicBasis(result.full.capture, result.full.serializedObservation);
    if (canonicalJson(verified.basis) !== canonicalJson(result.full.basis)
        || verified.capture.game_continuity_id !== this.#continuity
        || verified.capture.session.runtime_instance_id !== this.runtimeInstanceId) throw new Error("fast_env_capture_world_drift");
    return verified;
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
        requestTimeoutMs: config.host.request_timeout_ms, quietDiagnostics: true,
        ...(config.compact.enabled ? { compactOutput: { reconcileInterval: config.compact.reconcile_interval, sampleRate: config.checks.sample_rate } } : {})
      });
      return new HostSlotNative(started, new host.ManagedPeDriverSession(started, { requestTimeoutMs: config.host.request_timeout_ms }));
    }
  };
}
