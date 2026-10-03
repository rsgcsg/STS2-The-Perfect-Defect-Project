import { randomUUID } from "node:crypto";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { decodePlayerSnapshot, decodeTextMenuSnapshot, decodeTextMenuV2Snapshot } from "@rsgcsg/sts2-connector-client";
import type { Policy, AdapterDecision, PolicyDecisionInput, PolicyManifest, PolicyPortDecisionRequest, PolicyPortDecisionResponse, PolicyPortErrorResponse, PolicyPortReadyResponse, PolicyPortV2DecisionRequest, PolicyPortV2DecisionResponse, PolicyPortV2ErrorResponse, PolicyPortV2ReadyResponse, PolicyPortV3ReadyResponse, PolicyPortV3DecisionResponse, PolicyPortV3ErrorResponse, PolicyPortV4DecisionRequest, PolicyPortV4DecisionResponse, PolicyPortV4ErrorResponse, PolicyPortV4ReadyResponse, PublicStatefulAdapterDecision, PublicStatefulCompletion, PublicStatefulPolicy, PublicStatefulPolicyDecisionInput, StatefulAdapterDecision, StatefulPolicy, StatefulPolicyDecisionInput } from "./contracts.js";
import { POLICY_PORT_SCHEMA, POLICY_PORT_V2_SCHEMA, POLICY_PORT_V3_SCHEMA, POLICY_PORT_V4_SCHEMA, assertAdapterDecision, validateAdapterDecision, validatePolicyManifest } from "./contracts.js";
import { admitWholeDecisionBundle } from "./runtime.js";

export const DEFAULT_POLICY_ADAPTER_STARTUP_TIMEOUT_MS = 30_000;

export class NdjsonPolicyPort {
  private readonly pending = new Map<string, { expectedDigest: string; expectedCount: number; resolve: (choice: AdapterDecision) => void; reject: (error: Error) => void }>();
  private readonly pendingV2 = new Map<string, { input: StatefulPolicyDecisionInput; resolve: (choice: StatefulAdapterDecision) => void; reject: (error: Error) => void }>();
  private readonly pendingV4 = new Map<string, { input: PublicStatefulPolicyDecisionInput; resolve: (choice: PublicStatefulAdapterDecision) => void; reject: (error: Error) => void }>();
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
    // Write callbacks reject individual requests, but Writable also emits an
    // error event. Keep this listener after close: buffered writes can fail
    // after cancellation has killed the child.
    child.stdin.on("error", (error) => this.failAll(error));
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

  decideV2(input: StatefulPolicyDecisionInput, signal: AbortSignal, onOffer: () => void): Promise<StatefulAdapterDecision> {
    return this.decideStateful(input, signal, onOffer, POLICY_PORT_V2_SCHEMA);
  }

  decideV3(input: StatefulPolicyDecisionInput, signal: AbortSignal, onOffer: () => void): Promise<StatefulAdapterDecision> {
    return this.decideStateful(input, signal, onOffer, POLICY_PORT_V3_SCHEMA);
  }

  decideV4(input: PublicStatefulPolicyDecisionInput, signal: AbortSignal, onOffer: () => void): Promise<PublicStatefulAdapterDecision> {
    if (this.closed) return Promise.reject(new Error("policy child port is closed"));
    if (signal.aborted) return Promise.reject(new Error("policy decision cancelled"));
    const requestId = randomUUID();
    const wire = `${JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "decide", request_id: requestId, input })}\n`;
    return new Promise<PublicStatefulAdapterDecision>((resolve, reject) => {
      let settled = false;
      const onAbort = () => {
        if (settled) return;
        settled = true;
        if (this.pendingV4.delete(requestId)) this.rememberCancelled(requestId);
        signal.removeEventListener("abort", onAbort);
        reject(new Error("policy decision cancelled"));
      };
      const settle = <T>(callback: (value: T) => void) => (value: T) => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", onAbort);
        callback(value);
      };
      this.pendingV4.set(requestId, { input, resolve: settle(resolve), reject: settle(reject) });
      signal.addEventListener("abort", onAbort, { once: true });
      if (signal.aborted) { onAbort(); return; }
      let writeInvoked = false;
      try {
        onOffer();
        writeInvoked = true;
        this.child.stdin.write(wire, (error) => {
          if (error && this.pendingV4.delete(requestId)) {
            this.rememberCancelled(requestId);
            settle(reject)(error);
          }
        });
      } catch (error) {
        if (this.pendingV4.delete(requestId) && writeInvoked) this.rememberCancelled(requestId);
        settle(reject)(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }

  private decideStateful(input: StatefulPolicyDecisionInput, signal: AbortSignal, onOffer: () => void, schema: typeof POLICY_PORT_V2_SCHEMA | typeof POLICY_PORT_V3_SCHEMA): Promise<StatefulAdapterDecision> {
    if (this.closed) return Promise.reject(new Error("policy child port is closed"));
    if (signal.aborted) return Promise.reject(new Error("policy decision cancelled"));
    const requestId = randomUUID();
    const request = { schema,
      message_type: "decide", request_id: requestId, input };
    let wire: string;
    try { wire = `${JSON.stringify(request)}\n`; }
    catch (error) { return Promise.reject(error instanceof Error ? error : new Error(String(error))); }
    return new Promise<StatefulAdapterDecision>((resolve, reject) => {
      let settled = false;
      const onAbort = () => {
        if (settled) return;
        settled = true;
        if (this.pendingV2.delete(requestId)) this.rememberCancelled(requestId);
        signal.removeEventListener("abort", onAbort);
        reject(new Error("policy decision cancelled"));
      };
      const settle = <T>(callback: (value: T) => void) => (value: T) => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", onAbort);
        callback(value);
      };
      this.pendingV2.set(requestId, { input, resolve: settle(resolve), reject: settle(reject) });
      signal.addEventListener("abort", onAbort, { once: true });
      if (signal.aborted) { onAbort(); return; }
      let writeInvoked = false;
      try {
        onOffer(); // From this point the write may have partially reached the child.
        writeInvoked = true;
        this.child.stdin.write(wire, (error) => {
          if (error && this.pendingV2.delete(requestId)) {
            this.rememberCancelled(requestId);
            settle(reject)(error);
          }
        });
      } catch (error) {
        if (this.pendingV2.delete(requestId) && writeInvoked) this.rememberCancelled(requestId);
        settle(reject)(error instanceof Error ? error : new Error(String(error)));
      }
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
    if ((response.schema === POLICY_PORT_SCHEMA || response.schema === POLICY_PORT_V2_SCHEMA || response.schema === POLICY_PORT_V3_SCHEMA || response.schema === POLICY_PORT_V4_SCHEMA) && response.message_type === "ready") {
      try {
        if (this.readyAdapter) throw new Error("policy child emitted duplicate startup attestation");
        const adapter = validateReadyAdapter(response.adapter);
        if (response.schema !== (adapter.protocol === "sts2.policy-runtime/decision-only-ndjson-4" ? POLICY_PORT_V4_SCHEMA : adapter.protocol === "sts2.policy-runtime/decision-only-ndjson-3" ? POLICY_PORT_V3_SCHEMA : adapter.protocol === "sts2.policy-runtime/decision-only-ndjson-2" ? POLICY_PORT_V2_SCHEMA : POLICY_PORT_SCHEMA)) throw new Error("policy child ready schema/protocol mismatch");
        this.readyAdapter = adapter;
        this.resolveReady(adapter);
      } catch (error) {
        this.failAll(error instanceof Error ? error : new Error(String(error)));
      }
      return;
    }
    if (response.schema === POLICY_PORT_V4_SCHEMA) {
      this.handleV4Response(response as Record<string, unknown>);
      return;
    }
    if (response.schema === POLICY_PORT_V2_SCHEMA || response.schema === POLICY_PORT_V3_SCHEMA) {
      this.handleV2Response(response as Record<string, unknown>);
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

  private handleV2Response(response: Record<string, unknown>): void {
    if (typeof response.request_id !== "string" ||
        (response.message_type !== "decision" && response.message_type !== "error")) {
      this.failAll(new Error("policy child port emitted an invalid v2 response contract"));
      return;
    }
    const pending = this.pendingV2.get(response.request_id);
    if (!pending) {
      if (this.cancelled.delete(response.request_id)) return;
      this.failAll(new Error("policy child port response has an unknown request id"));
      return;
    }
    this.pendingV2.delete(response.request_id);
    const expectedSchema = pending.input.manifest.adapter.protocol === "sts2.policy-runtime/decision-only-ndjson-3" ? POLICY_PORT_V3_SCHEMA : POLICY_PORT_V2_SCHEMA;
    if (response.schema !== expectedSchema) { pending.reject(new Error("policy child response schema mismatch")); return; }
    if (response.message_type === "error") {
      if (!exactKeys(response, ["schema", "message_type", "request_id", "error"]) ||
          !isPortError(response.error)) pending.reject(new Error("policy child emitted malformed v2 error"));
      else pending.reject(new Error(response.error.message));
      return;
    }
    if (!exactKeys(response, ["schema", "message_type", "request_id", "output", "completion"])) {
      pending.reject(new Error("policy child emitted malformed v2 decision"));
      return;
    }
    try {
      const input = pending.input;
      const completion = response.completion;
      if (completion === null || typeof completion !== "object" || Array.isArray(completion) ||
          !exactKeys(completion as Record<string, unknown>, expectedSchema === POLICY_PORT_V3_SCHEMA ? ["continuity_token", "snapshot_id", "sequence", "previous_interaction_request_id"] : ["continuity_token", "snapshot_id", "sequence"]) ||
          (expectedSchema === POLICY_PORT_V3_SCHEMA && (completion as Record<string, unknown>).previous_interaction_request_id !== (input.previous_interaction?.request_id ?? null)) ||
          (completion as Record<string, unknown>).continuity_token !== input.continuity_token ||
          (completion as Record<string, unknown>).snapshot_id !== input.bundle.observation.snapshot_id ||
          (completion as Record<string, unknown>).sequence !== input.bundle.observation.sequence) {
        throw new Error("policy child completion watermark mismatch");
      }
      const output = validateAdapterDecision(response.output, input.candidate_digest, input.candidate_count);
      pending.resolve({ output, completion: completion as StatefulAdapterDecision["completion"] });
    } catch (error) { pending.reject(error instanceof Error ? error : new Error(String(error))); }
  }

  private handleV4Response(response: Record<string, unknown>): void {
    if (typeof response.request_id !== "string" || (response.message_type !== "decision" && response.message_type !== "error")) {
      this.failAll(new Error("policy child port emitted an invalid v4 response contract"));
      return;
    }
    const pending = this.pendingV4.get(response.request_id);
    if (!pending) {
      if (this.cancelled.delete(response.request_id)) return;
      this.failAll(new Error("policy child port response has an unknown request id"));
      return;
    }
    this.pendingV4.delete(response.request_id);
    if (response.schema !== POLICY_PORT_V4_SCHEMA) { pending.reject(new Error("policy child response schema mismatch")); return; }
    if (response.message_type === "error") {
      if (!exactKeys(response, ["schema", "message_type", "request_id", "error"]) || !isPortError(response.error))
        pending.reject(new Error("policy child emitted malformed v4 error"));
      else pending.reject(new Error(response.error.message));
      return;
    }
    if (!exactKeys(response, ["schema", "message_type", "request_id", "output", "completion"])) {
      pending.reject(new Error("policy child emitted malformed v4 decision"));
      return;
    }
    try {
      const input = pending.input;
      const completion = response.completion as PublicStatefulCompletion;
      validatePublicCompletion(completion, input);
      const output = validateAdapterDecision(response.output, input.candidate_digest, input.candidate_count);
      pending.resolve({ output, completion });
    } catch (error) { pending.reject(error instanceof Error ? error : new Error(String(error))); }
  }

  private failAll(error: Error): void {
    this.closed = true;
    if (!this.readyAdapter) this.rejectReady(error);
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
    for (const pending of this.pendingV2.values()) pending.reject(error);
    this.pendingV2.clear();
    for (const pending of this.pendingV4.values()) pending.reject(error);
    this.pendingV4.clear();
    this.cancelled.clear();
    if (this.child.exitCode === null) this.child.kill("SIGKILL");
  }

  private rememberCancelled(requestId: string): void {
    this.cancelled.add(requestId);
    if (this.cancelled.size > 256) this.failAll(new Error("policy child cancelled-request capacity exhausted"));
  }
}

function validateReadyAdapter(value: unknown): PolicyManifest["adapter"] {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("policy child startup adapter must be an object");
  const adapter = value as Record<string, unknown>;
  if (Object.keys(adapter).sort().join(",") !== "code_sha256,id,protocol,version"
      || typeof adapter.id !== "string" || !adapter.id
      || typeof adapter.version !== "string" || !adapter.version
      || (adapter.protocol !== "sts2.policy-runtime/decision-only-ndjson-1" && adapter.protocol !== "sts2.policy-runtime/decision-only-ndjson-2" && adapter.protocol !== "sts2.policy-runtime/decision-only-ndjson-3" && adapter.protocol !== "sts2.policy-runtime/decision-only-ndjson-4")
      || typeof adapter.code_sha256 !== "string" || !/^[a-f0-9]{64}$/u.test(adapter.code_sha256)) {
    throw new Error("policy child emitted an invalid startup adapter identity");
  }
  return adapter as unknown as PolicyManifest["adapter"];
}

function exactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}

function isPortError(value: unknown): value is { code: string; message: string } {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    exactKeys(value as Record<string, unknown>, ["code", "message"]) &&
    typeof (value as Record<string, unknown>).code === "string" &&
    typeof (value as Record<string, unknown>).message === "string";
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

/** Serial v2 child loop. A later reset token is processed only after older work ends. */
export async function serveStatefulPolicyPort(policy: StatefulPolicy,
  input: NodeJS.ReadableStream = process.stdin,
  output: NodeJS.WritableStream = process.stdout, protocol: "sts2.policy-runtime/decision-only-ndjson-2" | "sts2.policy-runtime/decision-only-ndjson-3" = "sts2.policy-runtime/decision-only-ndjson-2"): Promise<void> {
  const schema = protocol === "sts2.policy-runtime/decision-only-ndjson-3" ? POLICY_PORT_V3_SCHEMA : POLICY_PORT_V2_SCHEMA;
  const lines = createInterface({ input });
  for await (const line of lines) {
    let value: unknown;
    try { value = JSON.parse(line); }
    catch { writePort(output, { schema, message_type: "error",
      request_id: "unknown", error: { code: "invalid_json", message: "request was not JSON" } }); continue; }
    try {
      const request = validateV2Request(value, schema);
      const result = await policy(request.input, new AbortController().signal, () => {});
      assertAdapterDecision(result.output);
      validateAdapterDecision(result.output, request.input.candidate_digest, request.input.candidate_count);
      validateCompletion(result.completion, request.input, schema);
      writePort(output, { schema, message_type: "decision",
        request_id: request.request_id, output: result.output, completion: result.completion });
    } catch (error) {
      const requestId = value !== null && typeof value === "object" &&
        typeof (value as Record<string, unknown>).request_id === "string"
        ? String((value as Record<string, unknown>).request_id) : "unknown";
      writePort(output, { schema, message_type: "error",
        request_id: requestId, error: { code: "policy_error", message: error instanceof Error ? error.message : String(error) } });
    }
  }
}

/** Serial public Snapshot memory port. Its ordinal and token are Runtime supplied. */
export async function servePublicStatefulPolicyPort(policy: PublicStatefulPolicy,
  input: NodeJS.ReadableStream = process.stdin, output: NodeJS.WritableStream = process.stdout): Promise<void> {
  const lines = createInterface({ input });
  for await (const line of lines) {
    let value: unknown;
    try { value = JSON.parse(line); }
    catch { writePort(output, { schema: POLICY_PORT_V4_SCHEMA, message_type: "error", request_id: "unknown", error: { code: "invalid_json", message: "request was not JSON" } }); continue; }
    try {
      const request = validateV4Request(value);
      const result = await policy(request.input, new AbortController().signal, () => {});
      assertAdapterDecision(result.output);
      validateAdapterDecision(result.output, request.input.candidate_digest, request.input.candidate_count);
      validatePublicCompletion(result.completion, request.input);
      writePort(output, { schema: POLICY_PORT_V4_SCHEMA, message_type: "decision", request_id: request.request_id, output: result.output, completion: result.completion });
    } catch (error) {
      const requestId = value !== null && typeof value === "object" && typeof (value as Record<string, unknown>).request_id === "string" ? String((value as Record<string, unknown>).request_id) : "unknown";
      writePort(output, { schema: POLICY_PORT_V4_SCHEMA, message_type: "error", request_id: requestId, error: { code: "policy_error", message: error instanceof Error ? error.message : String(error) } });
    }
  }
}

function validateV4Request(value: unknown): PolicyPortV4DecisionRequest {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("v4 request must be an object");
  const request = value as Record<string, unknown>;
  if (!exactKeys(request, ["schema", "message_type", "request_id", "input"]) || request.schema !== POLICY_PORT_V4_SCHEMA || request.message_type !== "decide" || typeof request.request_id !== "string" || !request.request_id)
    throw new Error("invalid v4 request contract");
  const inputValue = request.input;
  if (inputValue === null || typeof inputValue !== "object" || Array.isArray(inputValue)) throw new Error("v4 input must be an object");
  const input = inputValue as Record<string, unknown>;
  if (!exactKeys(input, ["run_id", "manifest", "bundle", "candidate_digest", "candidate_count", "continuity_token", "episode_scope", "episode_id", "segment_id", "observation_ordinal", "previous_action"]) ||
      typeof input.run_id !== "string" || !input.run_id || typeof input.continuity_token !== "string" || !input.continuity_token ||
      (input.episode_scope !== "single_game_episode" && input.episode_scope !== "bounded_policy_segment") ||
      typeof input.episode_id !== "string" || !input.episode_id || typeof input.segment_id !== "string" || !input.segment_id ||
      !Number.isSafeInteger(input.observation_ordinal) || Number(input.observation_ordinal) < 1 ||
      typeof input.candidate_digest !== "string" || !/^[a-f0-9]{64}$/u.test(input.candidate_digest) ||
      !Number.isSafeInteger(input.candidate_count) || Number(input.candidate_count) < 0)
    throw new Error("invalid v4 input contract");
  const manifest = validatePolicyManifest(input.manifest);
  if (manifest.adapter.protocol !== "sts2.policy-runtime/decision-only-ndjson-4" || manifest.representation.input_schema !== "sts2.player-environment/snapshot-1" || (manifest.requirements as { reads: string[] }).reads.length !== 0)
    throw new Error("v4 request requires a public Snapshot port 4 manifest without Reads");
  validatePublicPreviousAction(input.previous_action);
  const bundle = input.bundle;
  if (bundle === null || typeof bundle !== "object" || Array.isArray(bundle) || !exactKeys(bundle as Record<string, unknown>, ["observation", "reads"]) || !Array.isArray((bundle as Record<string, unknown>).reads) || ((bundle as Record<string, unknown>).reads as unknown[]).length !== 0)
    throw new Error("v4 request requires a generic Snapshot bundle without Reads");
  const observation = decodePlayerSnapshot((bundle as Record<string, unknown>).observation).data;
  const admission = admitWholeDecisionBundle({ observation, reads: [] }, manifest);
  if (!admission.admitted || admission.candidateDigest !== input.candidate_digest || admission.candidateCount !== input.candidate_count)
    throw new Error("v4 request requires the exact complete Connector candidate catalog");
  return request as unknown as PolicyPortV4DecisionRequest;
}

function validatePublicPreviousAction(value: unknown): void {
  if (value === null) return;
  if (value === undefined || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid public previous action");
  const action = value as Record<string, unknown>;
  if (!exactKeys(action, ["decision_id", "source_snapshot_id", "candidate_digest", "bound_action_id", "request_id", "receipt", "successor"]) ||
      ["decision_id", "source_snapshot_id", "bound_action_id", "request_id"].some(key => typeof action[key] !== "string" || action[key] === "") ||
      typeof action.candidate_digest !== "string" || !/^[a-f0-9]{64}$/u.test(action.candidate_digest)) throw new Error("invalid public previous action");
  const receipt = action.receipt as Record<string, unknown> | null;
  const successor = action.successor as Record<string, unknown> | null;
  if (!receipt || !exactKeys(receipt, ["delivery", "reason_code"]) || receipt.delivery !== "delivered" || (receipt.reason_code !== null && typeof receipt.reason_code !== "string") ||
      !successor || !exactKeys(successor, ["snapshot_id", "sequence"]) || typeof successor.snapshot_id !== "string" || !successor.snapshot_id || !Number.isSafeInteger(successor.sequence) || Number(successor.sequence) < 0)
    throw new Error("invalid public previous action receipt/successor");
}

function validatePublicCompletion(completion: PublicStatefulCompletion, input: PublicStatefulPolicyDecisionInput): void {
  if (completion === null || typeof completion !== "object" || !exactKeys(completion as unknown as Record<string, unknown>, ["continuity_token", "episode_id", "segment_id", "observation_ordinal", "snapshot_id", "sequence", "previous_action_request_id"]) ||
      completion.continuity_token !== input.continuity_token || completion.episode_id !== input.episode_id || completion.segment_id !== input.segment_id ||
      completion.observation_ordinal !== input.observation_ordinal || completion.snapshot_id !== input.bundle.observation.snapshot_id || completion.sequence !== input.bundle.observation.sequence ||
      completion.previous_action_request_id !== (input.previous_action?.request_id ?? null) || !Number.isSafeInteger(completion.sequence) || completion.sequence < 0)
    throw new Error("public stateful completion watermark mismatch");
}

function validateV2Request(value: unknown, schema: typeof POLICY_PORT_V2_SCHEMA | typeof POLICY_PORT_V3_SCHEMA): PolicyPortV2DecisionRequest {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("v2 request must be an object");
  const request = value as Record<string, unknown>;
  if (!exactKeys(request, ["schema", "message_type", "request_id", "input"]) ||
      request.schema !== schema || request.message_type !== "decide" ||
      typeof request.request_id !== "string" || !request.request_id) throw new Error("invalid v2 request contract");
  const input = request.input;
  if (input === null || typeof input !== "object" || Array.isArray(input)) throw new Error("v2 input must be an object");
  const typed = input as Record<string, unknown>;
  if (!exactKeys(typed, schema === POLICY_PORT_V3_SCHEMA ? ["run_id", "manifest", "bundle", "candidate_digest", "candidate_count", "continuity_token", "previous_interaction"] : ["run_id", "manifest", "bundle", "candidate_digest", "candidate_count", "continuity_token"]) ||
      typeof typed.run_id !== "string" || !typed.run_id || typeof typed.continuity_token !== "string" || !typed.continuity_token ||
      typeof typed.candidate_digest !== "string" || !/^[a-f0-9]{64}$/u.test(typed.candidate_digest) ||
      !Number.isSafeInteger(typed.candidate_count) || Number(typed.candidate_count) < 0) throw new Error("invalid v2 input contract");
  const manifest = validatePolicyManifest(typed.manifest);
  if (manifest.adapter.protocol !== (schema === POLICY_PORT_V3_SCHEMA ? "sts2.policy-runtime/decision-only-ndjson-3" : "sts2.policy-runtime/decision-only-ndjson-2")) throw new Error("stateful request requires matching manifest");
  if (schema === POLICY_PORT_V3_SCHEMA) validateConfirmedInteraction(typed.previous_interaction);
  const bundle = typed.bundle;
  if (bundle === null || typeof bundle !== "object" || Array.isArray(bundle) ||
      !exactKeys(bundle as Record<string, unknown>, ["observation", "reads"]) ||
      !Array.isArray((bundle as Record<string, unknown>).reads) ||
      ((bundle as Record<string, unknown>).reads as unknown[]).length !== 0 ||
      ((bundle as Record<string, unknown>).observation as { schema?: unknown } | null)?.schema !==
      manifest.representation.input_schema) throw new Error("v2 request requires a text-menu bundle without Reads");
  const observation = manifest.representation.input_schema === "sts2.player-environment/text-menu-snapshot-2"
    ? decodeTextMenuV2Snapshot((bundle as Record<string, unknown>).observation).data
    : decodeTextMenuSnapshot((bundle as Record<string, unknown>).observation).data;
  const admission = admitWholeDecisionBundle({ observation, reads: [] }, manifest);
  if (!admission.admitted || admission.candidateDigest !== typed.candidate_digest ||
      admission.candidateCount !== typed.candidate_count)
    throw new Error("v2 request requires the exact complete Connector candidate catalog");
  return request as unknown as PolicyPortV2DecisionRequest;
}

function validateConfirmedInteraction(value: unknown): void {
  if (value === null) return;
  if (value === undefined || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid confirmed interaction");
  const interaction = value as Record<string, unknown>;
  if (!exactKeys(interaction, ["decision_id", "snapshot_id", "candidate_digest", "action_id", "request_id", "effect_domain", "result_kind"]) ||
      ["decision_id", "snapshot_id", "action_id", "request_id"].some(key => typeof interaction[key] !== "string" || interaction[key] === "") ||
      typeof interaction.candidate_digest !== "string" || !/^[a-f0-9]{64}$/u.test(interaction.candidate_digest) ||
      !((interaction.effect_domain === "text_menu" && interaction.result_kind === "menu_applied") ||
        (interaction.effect_domain === "native_input" && interaction.result_kind === "native_input_delivered"))) throw new Error("invalid confirmed interaction");
}

function validateCompletion(completion: StatefulAdapterDecision["completion"], input: StatefulPolicyDecisionInput, schema: typeof POLICY_PORT_V2_SCHEMA | typeof POLICY_PORT_V3_SCHEMA): void {
  if (completion === null || typeof completion !== "object" ||
      !exactKeys(completion as unknown as Record<string, unknown>, schema === POLICY_PORT_V3_SCHEMA ? ["continuity_token", "snapshot_id", "sequence", "previous_interaction_request_id"] : ["continuity_token", "snapshot_id", "sequence"]) ||
      (schema === POLICY_PORT_V3_SCHEMA && completion.previous_interaction_request_id !== (input.previous_interaction?.request_id ?? null)) ||
      completion.continuity_token !== input.continuity_token ||
      completion.snapshot_id !== input.bundle.observation.snapshot_id ||
      completion.sequence !== input.bundle.observation.sequence ||
      !Number.isSafeInteger(completion.sequence) || completion.sequence < 0) {
    throw new Error("v2 observation completion watermark mismatch");
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

function writePort(output: NodeJS.WritableStream, value: PolicyPortReadyResponse | PolicyPortDecisionResponse | PolicyPortErrorResponse | PolicyPortV2ReadyResponse | PolicyPortV2DecisionResponse | PolicyPortV2ErrorResponse | PolicyPortV3ReadyResponse | PolicyPortV3DecisionResponse | PolicyPortV3ErrorResponse | PolicyPortV4ReadyResponse | PolicyPortV4DecisionResponse | PolicyPortV4ErrorResponse): void {
  output.write(`${JSON.stringify(value)}\n`);
}
