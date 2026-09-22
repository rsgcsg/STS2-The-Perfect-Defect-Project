import { randomUUID } from "node:crypto";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import type { Policy, AdapterDecision, PolicyDecisionInput, PolicyManifest, PolicyPortDecisionRequest, PolicyPortDecisionResponse, PolicyPortErrorResponse, PolicyPortReadyResponse } from "./contracts.js";
import { POLICY_PORT_SCHEMA, assertAdapterDecision, validateAdapterDecision, validatePolicyManifest } from "./contracts.js";

export const DEFAULT_POLICY_ADAPTER_STARTUP_TIMEOUT_MS = 30_000;

export class NdjsonPolicyPort {
  private readonly pending = new Map<string, { expectedDigest: string; expectedCount: number; resolve: (choice: AdapterDecision) => void; reject: (error: Error) => void }>();
  private readonly cancelled = new Set<string>();
  private closed = false;
  private stderrTail = "";
  private readyAdapter?: PolicyManifest["adapter"];
  private readonly readyPromise: Promise<PolicyManifest["adapter"]>;
  private resolveReady!: (adapter: PolicyManifest["adapter"]) => void;
  private rejectReady!: (error: Error) => void;

  constructor(private readonly child: ChildProcessWithoutNullStreams) {
    this.readyPromise = new Promise((resolve, reject) => {
      this.resolveReady = resolve;
      this.rejectReady = reject;
    });
    const lines = createInterface({ input: child.stdout });
    lines.on("line", (line) => this.handleLine(line));
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => {
      this.stderrTail = `${this.stderrTail}${chunk}`.slice(-8_192);
    });
    child.on("error", (error) => this.failAll(error instanceof Error ? error : new Error(String(error))));
    child.on("close", (code, signal) => {
      const diagnostic = this.stderrTail.trim();
      this.failAll(new Error(`policy child port closed (code=${String(code)}, signal=${String(signal)})${diagnostic ? `: ${diagnostic}` : ""}`));
    });
  }

  static spawn(command: string, args: string[] = [], options: { cwd?: string; env?: NodeJS.ProcessEnv } = {}): NdjsonPolicyPort {
    return new NdjsonPolicyPort(spawn(command, args, { cwd: options.cwd, env: options.env, stdio: ["pipe", "pipe", "pipe"] }));
  }

  decide(input: PolicyDecisionInput, signal?: AbortSignal): Promise<AdapterDecision> {
    if (this.closed) return Promise.reject(new Error("policy child port is closed"));
    if (signal?.aborted) return Promise.reject(new Error("policy decision cancelled"));
    const requestId = randomUUID();
    const request: PolicyPortDecisionRequest = { schema: POLICY_PORT_SCHEMA, message_type: "decide", request_id: requestId, input };
    return new Promise<AdapterDecision>((resolve, reject) => {
      let settled = false;
      const onAbort = () => {
        if (settled) return;
        settled = true;
        if (this.pending.delete(requestId)) this.rememberCancelled(requestId);
        signal?.removeEventListener("abort", onAbort);
        reject(new Error("policy decision cancelled"));
      };
      const settle = <T>(callback: (value: T) => void) => (value: T) => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener("abort", onAbort);
        callback(value);
      };
      this.pending.set(requestId, {
        expectedDigest: input.candidate_digest,
        expectedCount: input.candidate_count,
        resolve: settle(resolve),
        reject: settle(reject)
      });
      signal?.addEventListener("abort", onAbort, { once: true });
      if (signal?.aborted) { onAbort(); return; }
      this.child.stdin.write(`${JSON.stringify(request)}\n`, (error) => {
        if (error) {
          if (this.pending.delete(requestId)) {
            this.rememberCancelled(requestId);
            settled = true;
            signal?.removeEventListener("abort", onAbort);
            reject(error);
          }
        }
      });
    });
  }

  async ready(
    timeoutMs = DEFAULT_POLICY_ADAPTER_STARTUP_TIMEOUT_MS
  ): Promise<PolicyManifest["adapter"]> {
    if (this.readyAdapter) return this.readyAdapter;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error("policy child startup attestation timed out")), timeoutMs);
    });
    try {
      return await Promise.race([this.readyPromise, timeout]);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  async attest(
    expected: PolicyManifest["adapter"],
    timeoutMs = DEFAULT_POLICY_ADAPTER_STARTUP_TIMEOUT_MS
  ): Promise<PolicyManifest["adapter"]> {
    const actual = await this.ready(timeoutMs);
    if (actual.id !== expected.id
        || actual.version !== expected.version
        || actual.protocol !== expected.protocol
        || actual.code_sha256 !== expected.code_sha256) {
      throw new Error("policy adapter startup identity differs from Policy Manifest");
    }
    return actual;
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.failAll(new Error("policy child port closed by parent"));
    this.child.kill();
  }

  private handleLine(line: string): void {
    let value: unknown;
    try { value = JSON.parse(line); } catch { this.failAll(new Error("policy child port emitted invalid JSON")); return; }
    if (value === null || typeof value !== "object" || Array.isArray(value)) { this.failAll(new Error("policy child port emitted a non-object")); return; }
    const response = value as { schema?: unknown; request_id?: unknown; message_type?: unknown; adapter?: unknown; error?: { message?: unknown }; output?: unknown };
    if (response.schema === POLICY_PORT_SCHEMA && response.message_type === "ready") {
      try {
        if (this.readyAdapter) throw new Error("policy child emitted duplicate startup attestation");
        const adapter = validateReadyAdapter(response.adapter);
        this.readyAdapter = adapter;
        this.resolveReady(adapter);
      } catch (error) {
        this.failAll(error instanceof Error ? error : new Error(String(error)));
      }
      return;
    }
    if (response.schema !== POLICY_PORT_SCHEMA || typeof response.request_id !== "string" || (response.message_type !== "decision" && response.message_type !== "error")) { this.failAll(new Error("policy child port emitted an invalid response contract")); return; }
    const pending = this.pending.get(response.request_id);
    if (!pending) {
      if (this.cancelled.delete(response.request_id)) return;
      this.failAll(new Error("policy child port response has an unknown request id"));
      return;
    }
    this.pending.delete(response.request_id);
    if (response.message_type === "error") {
      pending.reject(new Error(typeof response.error?.message === "string" ? response.error.message : "policy child returned an error"));
      return;
    }
    try { pending.resolve(validateAdapterDecision(response.output, pending.expectedDigest, pending.expectedCount)); } catch (error) { pending.reject(error instanceof Error ? error : new Error(String(error))); }
  }

  private failAll(error: Error): void {
    this.closed = true;
    if (!this.readyAdapter) this.rejectReady(error);
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
  }

  private rememberCancelled(requestId: string): void {
    this.cancelled.add(requestId);
    if (this.cancelled.size > 256) this.cancelled.delete(this.cancelled.values().next().value!);
  }
}

function validateReadyAdapter(value: unknown): PolicyManifest["adapter"] {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("policy child startup adapter must be an object");
  const adapter = value as Record<string, unknown>;
  if (Object.keys(adapter).sort().join(",") !== "code_sha256,id,protocol,version"
      || typeof adapter.id !== "string" || !adapter.id
      || typeof adapter.version !== "string" || !adapter.version
      || adapter.protocol !== "sts2.policy-runtime/decision-only-ndjson-1"
      || typeof adapter.code_sha256 !== "string" || !/^[a-f0-9]{64}$/u.test(adapter.code_sha256)) {
    throw new Error("policy child emitted an invalid startup adapter identity");
  }
  return adapter as unknown as PolicyManifest["adapter"];
}

export async function servePolicyPort(policy: Policy, input: NodeJS.ReadableStream = process.stdin, output: NodeJS.WritableStream = process.stdout): Promise<void> {
  const lines = createInterface({ input });
  for await (const line of lines) {
    let value: unknown;
    try { value = JSON.parse(line); } catch { writePort(output, errorResponse("unknown", "invalid_json", "request was not JSON")); continue; }
    try {
      const request = validateRequest(value);
      const outputChoice = await policy(request.input);
      assertAdapterDecision(outputChoice);
      validateAdapterDecision(outputChoice, request.input.candidate_digest, request.input.candidate_count);
      writePort(output, { schema: POLICY_PORT_SCHEMA, message_type: "decision", request_id: request.request_id, output: outputChoice });
    } catch (error) {
      const requestId = value !== null && typeof value === "object" && typeof (value as Record<string, unknown>).request_id === "string" ? String((value as Record<string, unknown>).request_id) : "unknown";
      writePort(output, errorResponse(requestId, "policy_error", error instanceof Error ? error.message : String(error)));
    }
  }
}

function validateRequest(value: unknown): PolicyPortDecisionRequest {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("policy port request must be an object");
  const request = value as Record<string, unknown>;
  const keys = Object.keys(request).sort().join(",");
  if (keys !== "input,message_type,request_id,schema" || request.schema !== POLICY_PORT_SCHEMA || request.message_type !== "decide" || typeof request.request_id !== "string") throw new Error("invalid policy port request contract");
  const input = request.input;
  if (input === null || typeof input !== "object" || Array.isArray(input)) throw new Error("policy port input must be an object");
  const typedInput = input as Record<string, unknown>;
  const inputKeys = Object.keys(typedInput).sort().join(",");
  const candidateDigest = typedInput.candidate_digest;
  if (inputKeys !== "bundle,candidate_count,candidate_digest,manifest,run_id" || typeof typedInput.run_id !== "string" || typeof candidateDigest !== "string" || !/^[a-f0-9]{64}$/u.test(candidateDigest) || !Number.isSafeInteger(typedInput.candidate_count) || Number(typedInput.candidate_count) < 0 || typedInput.bundle === undefined) throw new Error("policy port input is incomplete");
  validatePolicyManifest(typedInput.manifest);
  return request as unknown as PolicyPortDecisionRequest;
}

function errorResponse(requestId: string, code: string, message: string): PolicyPortErrorResponse {
  return { schema: POLICY_PORT_SCHEMA, message_type: "error", request_id: requestId, error: { code, message } };
}

function writePort(output: NodeJS.WritableStream, value: PolicyPortReadyResponse | PolicyPortDecisionResponse | PolicyPortErrorResponse): void {
  output.write(`${JSON.stringify(value)}\n`);
}
