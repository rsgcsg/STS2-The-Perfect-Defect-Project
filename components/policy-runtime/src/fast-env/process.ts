import { readFileSync, realpathSync } from "node:fs";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { POLICY_RUNTIME_VERSION } from "../contracts.js";
import { validateAgentManifest, type AgentManifest } from "../agent-session-contracts.js";
import { p1CoreIdentity } from "../p1-global/core-identity.js";
import { FAST_ENV_EVENT_SCHEMA, FastEnvContractError, parseFastEnvCommand, parseFastEnvConfig, type FastEnvCommand,
  type FastEnvConfig } from "./contracts.js";
import { ResponseBinding } from "./binding.js";
import { connectUnixDecisionLink, type DecisionLinkPort } from "./link.js";
import { MinimalJsonlEvidenceSink, type FastEvidenceSink } from "./evidence-sink.js";
import { hostSlotNativeFactory, type SlotNativeFactory } from "./host.js";
import { SlotKernel, emptyStats, type KernelContext, type KernelStats } from "./kernel.js";

const COMPACT_FORMAT = "managed-native-compact-1";

/** The manifest gate of today's Runtime (initializeGlobal) for a global-public-basis Agent. */
export function requireFastEnvManifest(value: unknown): AgentManifest {
  const manifest = validateAgentManifest(value);
  const expected = manifest.requirements.environment;
  if (!("kind" in expected) || expected.kind !== "global-public-basis" || !manifest.input.global
      || manifest.input.global.core_sha256 !== p1CoreIdentity(manifest.input.global.revision).module_digest)
    throw new Error("fast_env_global_core_or_environment_contract");
  return manifest;
}

export interface FastEnvDependencies {
  config: FastEnvConfig;
  manifest: AgentManifest;
  factory: SlotNativeFactory;
  link: DecisionLinkPort;
  evidence: FastEvidenceSink;
  commands: NodeJS.ReadableStream;
  write(line: string): void;
  sleep?(ms: number): Promise<void>;
  pid?: number;
}

/**
 * One K-game environment process: K isolated slots, NDJSON commands in and events
 * out. Resolves with the exit code: 0 after Stop, 1 after `fatal`. Never falls
 * back to the old chain and never retries a game.
 */
export class FastEnvProcess {
  readonly stats: KernelStats = emptyStats();
  readonly #slots: SlotKernel[];
  readonly #ctx: KernelContext;
  #stopping = false;
  #buildStopped: string | null = null;
  #gamesClosed = 0;
  #staleCloses = 0;
  #finished: Promise<number> | null = null;
  readonly #cpuStart = process.cpuUsage();

  constructor(private readonly deps: FastEnvDependencies) {
    let rowId = 0;
    const emit = (event: string, data: Record<string, unknown>) => {
      if (event === "closed") this.#gamesClosed += 1;
      deps.write(`${JSON.stringify({ schema: FAST_ENV_EVENT_SCHEMA, event, ...data })}\n`);
    };
    this.#ctx = {
      config: deps.config, manifest: deps.manifest, factory: deps.factory, link: deps.link, binding: new ResponseBinding(),
      evidence: deps.evidence, stats: this.stats, nextRowId: () => ++rowId, emit,
      stopBuildOutput: (reason, slot, gameInstanceId) => {
        this.#buildStopped ??= reason;
        emit("build_output_stopped", { reason, slot, game_instance_id: gameInstanceId });
      },
      stopping: () => this.#stopping,
      sleep: deps.sleep ?? (ms => delay(ms))
    };
    this.#slots = Array.from({ length: deps.config.slots }, (_, index) => new SlotKernel(index, this.#ctx));
    deps.link.setOpenInstances(id => this.#slots.some(slot => slot.gameInstanceId === id));
  }

  #emit(event: string, data: Record<string, unknown>): void { this.#ctx.emit(event, data); }

  /** Announces readiness and serves commands until Stop, stdin EOF or a fatal error. */
  run(): Promise<number> {
    const { config, manifest, link } = this.deps;
    link.send({ type: "hello", env_process_id: config.env_process_id, slots: config.slots,
      p1_revision: manifest.input.global!.revision, input_spec: manifest.input.input_spec });
    this.#emit("ready", { env_process_id: config.env_process_id, slots: config.slots, pid: this.deps.pid ?? process.pid,
      identity: { policy_runtime_version: POLICY_RUNTIME_VERSION, host_version: this.deps.factory.hostVersion,
        p1_revision: manifest.input.global!.revision, p1_core_sha256: manifest.input.global!.core_sha256,
        compact_format: config.compact.enabled ? COMPACT_FORMAT : null } });
    const lines = createInterface({ input: this.deps.commands, crlfDelay: Infinity });
    lines.on("line", line => { if (line.trim()) this.#accept(line); });
    lines.on("close", () => { void this.#finish(0, null); });
    return new Promise(resolve => { this.#resolveExit = resolve; });
  }

  #resolveExit: (code: number) => void = () => undefined;

  /** The decision link dropped: in-flight rows already resolved; every game closes, then fatal. */
  linkDisconnected(reason: string): void { void this.#finish(1, `link_disconnected:${reason}`); }

  #accept(line: string): void {
    let command: FastEnvCommand;
    try { command = parseFastEnvCommand(JSON.parse(line)); }
    catch (error) {
      // A malformed command is a manager bug: loud, never guessed at.
      void this.#finish(1, `command_invalid:${error instanceof FastEnvContractError ? error.field : "json"}`);
      return;
    }
    switch (command.cmd) {
      case "open": {
        const slot = this.#slots[command.slot];
        if (!slot) {
          this.#emit("open_refused", { command_id: command.command_id, slot: command.slot, game_ref: command.game_ref, reason: "slot_out_of_range" });
          return;
        }
        slot.open(command, this.#buildStopped !== null);
        return;
      }
      case "close_slot": {
        const slot = this.#slots[command.slot];
        // A close for a game that is not this slot's current one (already closed, or the next
        // game on the slot) never touches the slot: it is ignored and counted.
        if (!slot || slot.state === "idle"
            || command.game_instance_id !== null && slot.gameInstanceId !== command.game_instance_id) { this.#staleCloses += 1; return; }
        slot.closeRequested = true;
        return;
      }
      case "status":
        void this.#status(command.command_id);
        return;
      case "stop":
        void this.#finish(0, null);
        return;
    }
  }

  #counters(): Record<string, unknown> {
    const cpu = process.cpuUsage(this.#cpuStart);
    const round = (value: number) => Math.round(value * 1000) / 1000;
    const s = this.stats;
    return { late_replies_discarded: this.deps.link.lateReplies, invalid_replies_discarded: this.deps.link.invalidReplies,
      games: s.games, games_closed: this.#gamesClosed, stale_close_slot_ignored: this.#staleCloses, decisions: s.decisions, forced_steps: s.forced, native_steps: s.native_steps,
      settle_waits: s.settle_waits, build_output_stopped: this.#buildStopped,
      node_cpu_ms: round((cpu.user + cpu.system) / 1000),
      sync_ms: { capture: round(s.capture_ms), reduce: round(s.reduce_ms), input: round(s.input_ms), step: round(s.step_ms), receipt: round(s.receipt_ms) },
      link_wait_ms: round(s.link_wait_ms) };
  }

  async #status(commandId: string): Promise<void> {
    const native = await Promise.all(this.#slots.map(slot => slot.nativeCpuMs().catch(() => null)));
    this.#emit("status", { command_id: commandId,
      slots: this.#slots.map((slot, index) => ({ slot: slot.index, state: slot.state, game_instance_id: slot.gameInstanceId,
        decisions: slot.decisions, native_cpu_ms: native[index] })),
      counters: this.#counters() });
  }

  /** Stop: no new rows or native actions; in-flight work resolves within its own timeout; every game closes. */
  #finish(code: number, fatal: string | null): Promise<number> {
    if (this.#finished) return this.#finished;
    this.#stopping = true;
    // In-flight rows are not cancelled: each resolves by reply or timeout and is then
    // recorded stop_before_send (WP0 contract); Stop is bounded by the inference timeout.
    this.#finished = (async () => {
      const results = await Promise.allSettled(this.#slots.map(slot => slot.shutdown()));
      const failed = results.find(result => result.status === "rejected");
      await this.deps.link.close().catch(() => undefined);
      let exit = code, reason = fatal;
      if (failed && reason === null) { exit = 1; reason = `shutdown:${String((failed as PromiseRejectedResult).reason)}`; }
      if (reason !== null) this.#emit("fatal", { reason, counters: this.#counters() });
      else this.#emit("stopped", { games_closed: this.#gamesClosed, counters: this.#counters() });
      this.#resolveExit(exit);
      return exit;
    })();
    return this.#finished;
  }
}

function argument(argv: readonly string[], name: string): string {
  const index = argv.indexOf(name);
  const value = index >= 0 ? argv[index + 1] : undefined;
  if (!value) throw new Error(`fast_env_argument_required:${name}`);
  return value;
}

export async function main(argv: readonly string[] = process.argv.slice(2)): Promise<number> {
  const write = (line: string) => { process.stdout.write(line); };
  let run: FastEnvProcess | null = null;
  try {
    const config = parseFastEnvConfig(JSON.parse(readFileSync(argument(argv, "--config"), "utf8")));
    const manifest = requireFastEnvManifest(JSON.parse(readFileSync(config.agent_manifest, "utf8")));
    const factory = await hostSlotNativeFactory(config);
    const link = await connectUnixDecisionLink(config.decision_link.path, reason => run?.linkDisconnected(reason));
    run = new FastEnvProcess({ config, manifest, factory, link, evidence: new MinimalJsonlEvidenceSink(config.evidence.directory),
      commands: process.stdin, write });
    for (const signal of ["SIGTERM", "SIGINT"] as const) process.once(signal, () => process.stdin.destroy());
    return await run.run();
  } catch (error) {
    write(`${JSON.stringify({ schema: FAST_ENV_EVENT_SCHEMA, event: "fatal", reason: `startup:${error instanceof Error ? error.message : String(error)}` })}\n`);
    return 1;
  }
}

const invoked = process.argv[1] ? pathToFileURL(realpathSync(process.argv[1])).href : "";
if (invoked === import.meta.url) {
  main().then(code => { process.stdout.write("", () => process.exit(code)); });
}
