import { randomUUID } from "node:crypto";
import { join } from "node:path";
import type { HostKind } from "@rsgcsg/sts2-connector-client/p1-global";
import { LineProcess } from "./line-process.js";
import type { EpisodeRecorder } from "./recorder.js";

/**
 * The two existing Host drivers speak the same NDJSON command set
 * (reset/observe/read/step/close). P1 uses them unchanged; it never builds native
 * operands and never re-sends a mutation whose delivery is unknown.
 */
export interface HostLaunch {
  readonly kind: HostKind;
  readonly hostRuntimeRoot: string;
  /** Managed: --candidate dir. Reference: --local-root dir. */
  readonly location: string;
  readonly character: string;
  readonly extraArgs?: readonly string[];
  readonly requestTimeoutMs?: number;
  readonly startupTimeoutMs?: number;
}

export class HostDriverError extends Error {
  constructor(readonly code: string, message: string) { super(message); }
}

export class HostDriver {
  private process: LineProcess | null = null;
  private counter = 0;
  ready: Record<string, unknown> | null = null;

  constructor(readonly launch: HostLaunch, private readonly recorder: EpisodeRecorder) {}

  async start(): Promise<Record<string, unknown>> {
    const tool = this.launch.kind === "managed" ? "managed-pe-driver.mjs" : "reference-pe-driver.mjs";
    const args = [join(this.launch.hostRuntimeRoot, "tools", tool)];
    if (this.launch.kind === "managed") args.push("--candidate", this.launch.location, "--character", this.launch.character, "--quiet-diagnostics");
    else args.push("--local-root", this.launch.location, "--character", this.launch.character.toUpperCase(), "--native-speed", "instant");
    args.push(...(this.launch.extraArgs ?? []));
    this.recorder.append("env_request", { command: "launch", host_kind: this.launch.kind, argv: args.slice(1) });
    this.process = new LineProcess(process.execPath, args, { cwd: this.launch.hostRuntimeRoot });
    const raw = await this.process.next(this.launch.startupTimeoutMs ?? 120_000);
    this.recorder.appendRaw("env_response", raw, { command: "launch" });
    const ready = JSON.parse(raw) as Record<string, unknown>;
    if (ready.type !== "ready") throw new HostDriverError("host_not_ready", raw.slice(0, 500));
    this.ready = ready;
    this.recorder.append("host_ready", { protocol: ready.protocol, host_kind: this.launch.kind });
    return ready;
  }

  async request(command: Record<string, unknown>, timeoutMs?: number): Promise<Record<string, any>> {
    if (!this.process) throw new HostDriverError("host_not_started", "driver not started");
    const request = { ...command, request_id: `p1-${++this.counter}` };
    this.recorder.append("env_request", { request });
    this.process.send(request);
    let raw: string;
    try { raw = await this.process.next(timeoutMs ?? this.launch.requestTimeoutMs ?? 60_000); }
    catch (error) {
      this.recorder.append("env_error", { request_id: request.request_id, error: String((error as Error).message) });
      throw new HostDriverError("host_response_missing", String((error as Error).message));
    }
    this.recorder.appendRaw("env_response", raw, { request_id: request.request_id, command: command.command });
    const response = JSON.parse(raw) as Record<string, any>;
    if (response.request_id !== request.request_id) throw new HostDriverError("host_response_mismatch", raw.slice(0, 300));
    if (response.type === "error") throw new HostDriverError("host_error", String(response.message ?? "error"));
    return response;
  }

  newMutationId(): string { return `p1m-${randomUUID()}`; }

  async close(): Promise<Record<string, unknown>> {
    if (!this.process) return { status: "not_started" };
    let result: Record<string, unknown>;
    try { result = await this.request({ command: "close" }, 120_000); }
    catch (error) { result = { status: "close_failed", error: String((error as Error).message) }; }
    await this.process.terminate();
    const exit = this.process.exit;
    this.recorder.append("host_close", { result, process_exit: exit });
    this.process = null;
    return { result, process_exit: exit };
  }
}
