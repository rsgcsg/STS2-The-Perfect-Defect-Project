import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { RuntimeControlPreconditionError, type PolicyRuntime } from "./runtime.js";
import type { RuntimeControlPreconditions, TickResult } from "./contracts.js";

const HTTP_SCHEMA = "sts2.policy-runtime/http-2" as const;

export interface PolicyRuntimeHttpOptions {
  host?: "127.0.0.1" | "localhost" | "::1";
  port?: number;
  maxBodyBytes?: number;
  maxAutoTicks?: number;
  autoDrive?: boolean;
  deferAutoDrive?: boolean;
  autoIdleMs?: number;
  /** CLI owners may close after stop succeeds and its response finishes or disconnects. */
  onStopped?: () => Promise<void>;
}

export interface RunningPolicyRuntimeHttpServer {
  readonly server: Server;
  readonly address: string;
  startDriving(): void;
  close(): Promise<void>;
}

export async function startPolicyRuntimeHttpServer(runtime: PolicyRuntime, options: PolicyRuntimeHttpOptions = {}): Promise<RunningPolicyRuntimeHttpServer> {
  const host = options.host ?? "127.0.0.1";
  if (!["127.0.0.1", "localhost", "::1"].includes(host)) throw new Error("Policy Runtime HTTP service is loopback-only");
  const maxBodyBytes = options.maxBodyBytes ?? 8 * 1024;
  const maxAutoTicks = options.maxAutoTicks ?? 16;
  const autoIdleMs = options.autoIdleMs ?? 25;
  if (!Number.isSafeInteger(maxBodyBytes) || maxBodyBytes < 1 || !Number.isSafeInteger(maxAutoTicks) || maxAutoTicks < 1) throw new Error("HTTP bounds must be positive integers");
  if (!Number.isSafeInteger(autoIdleMs) || autoIdleMs < 0) throw new Error("autoIdleMs must be a non-negative integer");
  let closing = false;
  let stoppedNotified = false;
  let autoWorker: Promise<void> | null = null;
  const ensureAutoWorker = (): void => {
    if (!options.autoDrive || autoWorker || closing || !isDrivenMode(runtime.status().mode)) return;
    autoWorker = (async () => {
      try {
        while (!closing && isDrivenMode(runtime.status().mode) && runtime.status().autonomy_budget.state === "active" && !runtime.status().tainted) {
          const result = await runtime.tick();
          if (result.type === "unknown" || !isDrivenMode(runtime.status().mode) || runtime.status().autonomy_budget.state !== "active") return;
          if (autoIdleMs > 0) await new Promise((resolve) => setTimeout(resolve, autoIdleMs));
          else await new Promise((resolve) => setImmediate(resolve));
        }
      } catch {
        try { await runtime.setMode("human"); } catch { /* Runtime already failed closed. */ }
      }
    })().finally(() => { autoWorker = null; if (!closing && runtime.status().autonomy_budget.state === "active") ensureAutoWorker(); });
  };
  const onStopped = options.onStopped ? (): void => {
    if (stoppedNotified) return;
    stoppedNotified = true;
    void options.onStopped!().catch((error: unknown) => serverStopError(error));
  } : undefined;
  const server = createServer((request, response) => { void dispatch(runtime, request, response, maxBodyBytes, maxAutoTicks, ensureAutoWorker, onStopped); });
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(options.port ?? 0, host, () => { server.removeListener("error", reject); resolve(); }); });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Policy Runtime HTTP service did not expose a socket address");
  if (!options.deferAutoDrive) ensureAutoWorker();
  return { server, address: `http://${host === "::1" ? "[::1]" : host}:${(address as AddressInfo).port}`, startDriving: ensureAutoWorker, close: async () => {
    closing = true;
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    await autoWorker;
  } };
}

async function dispatch(runtime: PolicyRuntime, request: IncomingMessage, response: ServerResponse, maxBodyBytes: number, maxAutoTicks: number, ensureAutoWorker: () => void, onStopped?: () => void): Promise<void> {
  try {
    if (request.method === "GET" && request.url === "/status") { json(response, 200, { schema: HTTP_SCHEMA, status: runtime.status() }); return; }
    if (request.method === "GET" && request.url === "/v2/environment") {
      const denied = localRequestError(request);
      if (denied) { json(response, denied.status, { schema: HTTP_SCHEMA, error: denied.error }); return; }
      json(response, 200, await runtime.readEnvironment()); return;
    }
    if (request.method !== "POST" || !["/v2/mode", "/v2/tick", "/v2/stop"].includes(request.url ?? "")) { json(response, 404, { schema: HTTP_SCHEMA, error: "not_found" }); return; }
    const denied = mutationRequestError(request);
    if (denied) { request.resume(); json(response, denied.status, { schema: HTTP_SCHEMA, error: denied.error }); return; }
    const runHeader = "x-sts2-policy-run-id";
    const runHeaderCount = request.rawHeaders.filter((_value, index) => index % 2 === 0 && request.rawHeaders[index]?.toLowerCase() === runHeader).length;
    const expectedRun = request.headers[runHeader];
    if (runHeaderCount !== 1 || typeof expectedRun !== "string" || expectedRun.trim() === "") {
      request.resume(); json(response, 428, { schema: HTTP_SCHEMA, error: "runtime_run_precondition_required" }); return;
    }
    // This Runtime's runId is immutable. Validate at the mutation owner, not
    // through a caller's earlier GET to a port another process can reuse.
    if (expectedRun !== runtime.status().run_id) {
      request.resume(); json(response, 409, { schema: HTTP_SCHEMA, error: "runtime_run_mismatch" }); return;
    }
    const body = await readBody(request, maxBodyBytes);
    if (request.url === "/v2/mode") {
      const value = strictObject(body, ["mode"]);
      if (value.mode !== "human" && value.mode !== "shadow" && value.mode !== "one_step" && value.mode !== "auto") throw new Error("mode is invalid");
      const status = await runtime.setMode(value.mode, value.mode === "human" ? undefined : controlPreconditions(request));
      if (value.mode === "auto" || value.mode === "shadow") ensureAutoWorker();
      json(response, 200, { schema: HTTP_SCHEMA, status });
      return;
    }
    if (request.url === "/v2/stop") {
      strictObject(body, []);
      const status = await runtime.stop();
      if (onStopped) {
        const notify = (): void => {
          response.off("finish", notify);
          response.off("close", notify);
          onStopped();
        };
        response.once("finish", notify);
        response.once("close", notify);
        if (response.destroyed || response.writableFinished) notify();
      }
      if (!response.destroyed) json(response, 200, { schema: HTTP_SCHEMA, status });
      return;
    }
    const value = body === undefined ? {} : strictObject(body, ["max_ticks"]);
    const requestedValue = value.max_ticks;
    const requested = requestedValue === undefined ? 1 : requestedValue;
    if (typeof requested !== "number" || !Number.isSafeInteger(requested) || requested < 1 || requested > maxAutoTicks) throw new Error(`max_ticks must be between 1 and ${maxAutoTicks}`);
    const limit = runtime.status().mode === "one_step" ? 1 : requested;
    const results: TickResult[] = [];
    const expected = controlPreconditions(request);
    for (let index = 0; index < limit; index += 1) {
      try {
        const result = await runtime.tick(expected);
        results.push(result);
        if (result.type === "unknown" || runtime.status().mode === "human" || runtime.status().autonomy_budget.state !== "active" || runtime.status().tainted) break;
      } catch (error) {
        // A later fence failure cannot erase already executed ticks or advertise
        // the entire POST as known-unapplied. Preserve the completed prefix.
        if (!(error instanceof RuntimeControlPreconditionError) || results.length === 0) throw error;
        results.push({ type: "not_admitted", reason: error.code, status: runtime.status() });
        break;
      }
    }
    json(response, 200, { schema: `${HTTP_SCHEMA}/tick-1`, results, status: runtime.status() });
  } catch (error) {
    if (error instanceof RuntimeControlPreconditionError) {
      json(response, error.httpStatus, { schema: HTTP_SCHEMA, error: error.code }); return;
    }
    const status = error instanceof Error && error.message.includes("body") ? 413 : 400;
    json(response, status, { schema: HTTP_SCHEMA, error: error instanceof Error ? error.message : String(error) });
  }
}

function mutationRequestError(request: IncomingMessage): { status: number; error: string } | null {
  const denied = localRequestError(request);
  if (denied) return denied;
  if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/iu.test(request.headers["content-type"] ?? "")) return { status: 415, error: "mutation_requires_application_json" };
  return null;
}

function localRequestError(request: IncomingMessage): { status: number; error: string } | null {
  const authority = request.headers.host;
  const port = request.socket.localPort;
  const hosts = ["127.0.0.1", "localhost", "[::1]"].map((host) => `${host}:${port}`);
  const hostCount = request.rawHeaders.filter((_value, index) => index % 2 === 0 && request.rawHeaders[index]?.toLowerCase() === "host").length;
  if (hostCount !== 1 || !authority || !hosts.includes(authority)) return { status: 403, error: "mutation_host_not_allowed" };
  const origin = request.headers.origin;
  if (origin !== undefined && origin !== `http://${authority}`) return { status: 403, error: "mutation_origin_not_allowed" };
  return null;
}

function controlPreconditions(request: IncomingMessage): RuntimeControlPreconditions | undefined {
  const optionalHeader = (name: string, error: string): string | undefined => {
    const count = request.rawHeaders.filter((_value, index) => index % 2 === 0 && request.rawHeaders[index]?.toLowerCase() === name).length;
    if (count === 0) return undefined;
    const value = request.headers[name];
    if (count !== 1 || typeof value !== "string" || value.trim() === "")
      throw new RuntimeControlPreconditionError(error, 428);
    return value;
  };
  const gameInstanceId = optionalHeader("x-sts2-game-instance-id", "runtime_game_precondition_required");
  const epoch = optionalHeader("x-sts2-recovery-epoch", "runtime_recovery_precondition_required");
  let recoveryEpoch: number | undefined;
  if (epoch !== undefined) {
    recoveryEpoch = Number(epoch);
    if (!/^(?:0|[1-9][0-9]*)$/u.test(epoch) || !Number.isSafeInteger(recoveryEpoch))
      throw new RuntimeControlPreconditionError("runtime_recovery_precondition_required", 428);
  }
  return gameInstanceId === undefined && recoveryEpoch === undefined
    ? undefined : { gameInstanceId, recoveryEpoch };
}

function serverStopError(error: unknown): void {
  process.stderr.write(`Policy Runtime stop cleanup failed: ${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
}

function isDrivenMode(mode: string): boolean { return mode === "auto" || mode === "shadow"; }

async function readBody(request: IncomingMessage, maxBytes: number): Promise<Record<string, unknown> | undefined> {
  let bytes = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    const data = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += data.byteLength;
    if (bytes > maxBytes) throw new Error("request body exceeds configured body limit");
    chunks.push(data);
  }
  if (chunks.length === 0) return undefined;
  const value: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("request body must be a JSON object");
  return value as Record<string, unknown>;
}

function strictObject(value: Record<string, unknown> | undefined, keys: string[]): Record<string, unknown> {
  if (value === undefined) { if (keys.length === 0) return {}; throw new Error("request body is required"); }
  const expected = new Set(keys);
  const actual = Object.keys(value);
  if (actual.length !== expected.size || actual.some((key) => !expected.has(key))) throw new Error("request body has unknown or missing fields");
  return value;
}

function json(response: ServerResponse, statusCode: number, value: unknown): void {
  const body = `${JSON.stringify(value)}\n`;
  response.writeHead(statusCode, { "content-type": "application/json", "content-length": Buffer.byteLength(body), "cache-control": "no-store" });
  response.end(body);
}
