import { randomUUID } from "node:crypto";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import {
  AGENT_LIMIT_MAXIMA, AGENT_SESSION_SCHEMA, AgentSessionError, sessionInteger, sessionObject, sessionText,
  validateAgentAdapter, validateAgentConsumption, validateAgentDirective,
  validateAgentNextInput, validateAgentQuery, validateAgentSessionContext,
  type AgentAdapterIdentity, type AgentConsumeAck, type AgentConsumeInput,
  type AgentConsumption, type AgentDirectiveOutput, type AgentLimits,
  type AgentManifest, type AgentNextInput, type AgentQuery, type AgentQueryResult, type AgentSessionContext
} from "./agent-session-contracts.js";
import { AgentJsonLineFramer, agentJsonByteLength, encodeBoundedAgentJson } from "./agent-session-json.js";
import { AgentByteBudget, type AgentByteReservation } from "./agent-session-budget.js";
import {
  acquireAgentStateBytes, requireAgentStateMetadata, validateAgentStateMetadata,
  type AgentOpaqueState, type AgentStateMetadata
} from "./agent-session-state.js";

/** Supplied by the existing Runtime from durable acknowledgement/request evidence. */
export interface AgentStateAuthorization {
  expected_metadata: AgentStateMetadata;
  pending_request: "none" | "reconciled";
  retained_prefix: "complete";
  assertCurrent(): void;
}
export interface AgentExportedState {
  state: AgentOpaqueState;
  bytes: Buffer;
  release(): void;
}
interface StateCall {
  manifest: AgentManifest;
  authorization: AgentStateAuthorization;
  input: { expected_metadata: AgentStateMetadata; state?: AgentOpaqueState };
}

export interface AgentPortHandlers {
  query(input: AgentQuery, signal: AbortSignal): Promise<AgentQueryResult>;
  consumed(report: AgentConsumption, signal: AbortSignal): Promise<AgentConsumeAck> | AgentConsumeAck;
}
interface Pending {
  kind: "consume" | "next" | "export_state" | "restore_state";
  context: AgentSessionContext;
  input: AgentConsumeInput | AgentNextInput;
  handlers?: AgentPortHandlers;
  signal: AbortSignal;
  resolve: (value: AgentConsumeAck | AgentDirectiveOutput | AgentExportedState | AgentStateMetadata) => void;
  reject: (error: Error) => void;
  cleanup: () => void;
  latestAck: AgentConsumeAck | null;
  queries: number;
  queryBytes: number;
  pendingQueries: number;
  reportPending: boolean;
  stateCall?: StateCall;
}

/** Strict duplex process transport. Controller and native SDK ownership stay outside. */
export class NdjsonAgentSessionPort {
  private readonly pending = new Map<string, Pending>();
  private readonly childIds = new Set<string>();
  private readonly readyPromise: Promise<AgentAdapterIdentity>;
  private resolveReady!: (value: AgentAdapterIdentity) => void;
  private rejectReady!: (error: Error) => void;
  private readyIdentity: AgentAdapterIdentity | null = null;
  private closed = false;
  private consumedInChild = false;
  private readonly framer: AgentJsonLineFramer;
  private readonly stderrChunks: Buffer[] = [];
  private stderrBytes = 0;

  constructor(private readonly child: ChildProcessWithoutNullStreams,
    private readonly expected: AgentAdapterIdentity, private readonly limits: AgentLimits,
    readonly byteBudget = new AgentByteBudget(limits.max_retained_acquisition_bytes)) {
    validateAgentAdapter(expected);
    sessionObject(limits, Object.keys(AGENT_LIMIT_MAXIMA));
    for (const [key, maximum] of Object.entries(AGENT_LIMIT_MAXIMA))
      sessionInteger(limits[key as keyof AgentLimits], true, maximum);
    this.readyPromise = new Promise((resolve, reject) => { this.resolveReady = resolve; this.rejectReady = reject; });
    // A pre-start process failure must not become an unhandled promise rejection.
    void this.readyPromise.catch(() => undefined);
    this.framer = new AgentJsonLineFramer(limits.max_message_bytes, value => this.handle(value));
    child.stdout.on("data", (data: Buffer) => {
      if (this.closed) return;
      try { this.framer.push(data); } catch (error) { this.fail(error); }
    });
    child.stdout.on("end", () => {
      if (this.closed) return;
      try { this.framer.end(); } catch (error) { this.fail(error); }
    });
    child.stderr.on("data", (data: Buffer) => {
      const kept = data.subarray(Math.max(0, data.length - 8192));
      this.stderrChunks.push(Buffer.from(kept)); this.stderrBytes += kept.length;
      while (this.stderrBytes > 8192 && this.stderrChunks.length > 1) this.stderrBytes -= this.stderrChunks.shift()!.length;
      // Fixed-size bounded diagnostics only; child stderr is never a control input.
    });
    child.stdin.on("error", error => this.fail(error));
    child.on("error", error => this.fail(error));
    child.on("close", () => this.fail(new AgentSessionError("agent_child_closed")));
  }

  static spawn(command: string, args: string[], expected: AgentAdapterIdentity, limits: AgentLimits,
    options: { cwd?: string; env?: NodeJS.ProcessEnv } = {}): NdjsonAgentSessionPort {
    // The trusted application explicitly chooses code/environment. No manifest selects a command.
    const inherited: NodeJS.ProcessEnv = {};
    for (const key of ["PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP", "PYTHONPATH", "VIRTUAL_ENV", "OMP_NUM_THREADS"])
      if (process.env[key] !== undefined) inherited[key] = process.env[key];
    return new NdjsonAgentSessionPort(spawn(command, args, { cwd: options.cwd,
      env: options.env ?? inherited, stdio: ["pipe", "pipe", "pipe"] }), expected, limits);
  }

  async ready(timeoutMs = this.limits.agent_timeout_ms): Promise<AgentAdapterIdentity> {
    if (this.readyIdentity) return this.readyIdentity;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([this.readyPromise, new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          const error = new AgentSessionError("agent_startup_timeout");
          this.fail(error); reject(error);
        }, timeoutMs);
      })]);
    } finally { if (timer !== undefined) clearTimeout(timer); }
  }

  consume(context: AgentSessionContext, input: AgentConsumeInput, handlers: AgentPortHandlers,
    signal: AbortSignal, onOffer: () => void): Promise<AgentConsumeAck> {
    sessionObject(input, ["acquisition_id", "input_spec", "continuity_token", "previous_consumption_id", "observation", "catalog"]);
    sessionText(input.acquisition_id); sessionText(input.continuity_token);
    sessionObject(input.observation);
    return this.request("consume", context, input, handlers, signal, onOffer) as Promise<AgentConsumeAck>;
  }

  next(context: AgentSessionContext, input: AgentNextInput, handlers: AgentPortHandlers,
    signal: AbortSignal, onOffer: () => void): Promise<AgentDirectiveOutput> {
    validateAgentNextInput(input);
    return this.request("next", context, input, handlers, signal, onOffer) as Promise<AgentDirectiveOutput>;
  }

  exportState(context: AgentSessionContext, manifest: AgentManifest, authorization: AgentStateAuthorization,
    signal: AbortSignal, onOffer: () => void): Promise<AgentExportedState> {
    return this.requestState("export_state", context, manifest, authorization, signal, onOffer) as Promise<AgentExportedState>;
  }

  restoreState(context: AgentSessionContext, manifest: AgentManifest, authorization: AgentStateAuthorization,
    state: AgentOpaqueState, signal: AbortSignal, onOffer: () => void): Promise<AgentStateMetadata> {
    if (manifest.input.state_recovery.mode === "none") return Promise.reject(new AgentSessionError("stateless_state_unsupported"));
    if (this.consumedInChild) return Promise.reject(new AgentSessionError("restore_after_consume"));
    requireAgentStateMetadata(state.metadata, authorization.expected_metadata, manifest);
    const checked = acquireAgentStateBytes(state.payload, manifest.input.state_recovery.max_state_bytes, this.byteBudget);
    checked.reservation.release();
    return this.requestState("restore_state", context, manifest, authorization, signal, onOffer, state) as Promise<AgentStateMetadata>;
  }

  close(): void { this.fail(new AgentSessionError("agent_port_closed")); }

  private request(kind: Pending["kind"], context: AgentSessionContext,
    input: AgentConsumeInput | AgentNextInput, handlers: AgentPortHandlers,
    signal: AbortSignal, onOffer: () => void): Promise<AgentConsumeAck | AgentDirectiveOutput> {
    if (this.closed) return Promise.reject(new AgentSessionError("agent_port_closed"));
    if (!this.readyIdentity) return Promise.reject(new AgentSessionError("agent_not_attested"));
    if (signal.aborted) return Promise.reject(new AgentSessionError("agent_call_cancelled"));
    if (this.pending.size !== 0) return Promise.reject(new AgentSessionError("concurrent_agent_call"));
    const checkedContext = validateAgentSessionContext(context);
    const requestId = `parent-${randomUUID()}`;
    const message = { schema: AGENT_SESSION_SCHEMA, message_type: kind, ...checkedContext, request_id: requestId, input };
    const prepared = this.prepareWrite(message);
    return new Promise((resolve, reject) => {
      const onAbort = () => this.fail(new AgentSessionError("agent_call_cancelled"));
      const timer = setTimeout(() => this.fail(new AgentSessionError("agent_call_timeout")), this.limits.agent_timeout_ms);
      const cleanup = () => { clearTimeout(timer); signal.removeEventListener("abort", onAbort); };
      const boundInput = kind === "consume" ? { ...input, input_spec: { ...(input as AgentConsumeInput).input_spec } } : { ...input };
      this.pending.set(requestId, { kind, context: { ...checkedContext }, input: boundInput, handlers, signal,
        resolve: value => resolve(value as AgentConsumeAck | AgentDirectiveOutput), reject, cleanup, latestAck: null, queries: 0, queryBytes: 0,
        pendingQueries: 0, reportPending: false });
      signal.addEventListener("abort", onAbort, { once: true });
      if (signal.aborted) { prepared.reservation.release(); onAbort(); return; }
      try {
        onOffer(); // Any subsequent failure leaves child consumption state uncertain.
        this.writeEncoded(prepared.encoded, prepared.reservation);
      } catch (error) { prepared.reservation.release(); this.fail(error); }
    });
  }

  private requestState(kind: "export_state" | "restore_state", context: AgentSessionContext,
    manifest: AgentManifest, authorization: AgentStateAuthorization, signal: AbortSignal,
    onOffer: () => void, state?: AgentOpaqueState): Promise<AgentExportedState | AgentStateMetadata> {
    if (manifest.input.state_recovery.mode === "none") return Promise.reject(new AgentSessionError("stateless_state_unsupported"));
    if (this.closed || !this.readyIdentity || this.pending.size !== 0 || signal.aborted)
      return Promise.reject(new AgentSessionError("state_call_not_ready"));
    if (!["none", "reconciled"].includes(authorization.pending_request) || authorization.retained_prefix !== "complete")
      return Promise.reject(new AgentSessionError("state_recovery_requires_reconciled_prefix"));
    authorization.assertCurrent();
    validateAgentStateMetadata(authorization.expected_metadata, manifest);
    if (manifest.adapter.id !== this.expected.id || manifest.adapter.version !== this.expected.version
      || manifest.adapter.protocol !== this.expected.protocol || manifest.adapter.code_sha256 !== this.expected.code_sha256)
      throw new AgentSessionError("state_adapter_identity");
    const checkedContext = validateAgentSessionContext(context);
    const requestId = `parent-${randomUUID()}`;
    const input = { expected_metadata: authorization.expected_metadata, ...(state ? { state } : {}) };
    const prepared = this.prepareWrite({ schema: AGENT_SESSION_SCHEMA, message_type: kind,
      ...checkedContext, request_id: requestId, input });
    return new Promise((resolve, reject) => {
      const onAbort = () => this.fail(new AgentSessionError("agent_call_cancelled"));
      const timer = setTimeout(() => this.fail(new AgentSessionError("agent_call_timeout")), this.limits.agent_timeout_ms);
      const cleanup = () => { clearTimeout(timer); signal.removeEventListener("abort", onAbort); };
      const binding = authorization.expected_metadata;
      this.pending.set(requestId, { kind, context: checkedContext,
        input: { continuity_token: binding.continuity_token, consumption_id: binding.consumption_id,
          state_version: binding.state_version, basis_acquisition_id: binding.last_acknowledged_basis.acquisition_id,
          received_cursor: binding.prefix.received_cursor }, signal, resolve: value => resolve(value as AgentExportedState | AgentStateMetadata), reject,
        cleanup, latestAck: null, queries: 0, queryBytes: 0, pendingQueries: 0, reportPending: false,
        stateCall: { manifest, authorization, input } });
      signal.addEventListener("abort", onAbort, { once: true });
      if (signal.aborted) { prepared.reservation.release(); onAbort(); return; }
      try { authorization.assertCurrent(); onOffer(); this.writeEncoded(prepared.encoded, prepared.reservation); }
      catch (error) { prepared.reservation.release(); this.fail(error); }
    });
  }

  private handle(value: unknown): void {
    if (this.closed) return;
    const message = sessionObject(value);
    if (message.message_type === "ready") {
      sessionObject(message, ["schema", "message_type", "adapter"]);
      if (message.schema !== AGENT_SESSION_SCHEMA || this.readyIdentity !== null) throw new AgentSessionError("invalid_or_duplicate_ready");
      const identity = validateAgentAdapter(message.adapter);
      if (JSON.stringify(identity) !== JSON.stringify(this.expected)) {
        // Key order is not identity: compare the closed attestation fields themselves.
        if (identity.id !== this.expected.id || identity.version !== this.expected.version
          || identity.protocol !== this.expected.protocol || identity.code_sha256 !== this.expected.code_sha256)
          throw new AgentSessionError("agent_attestation_mismatch");
      }
      this.readyIdentity = { ...identity }; this.resolveReady(this.readyIdentity); return;
    }
    if (message.schema !== AGENT_SESSION_SCHEMA) throw new AgentSessionError("agent_schema_mismatch");
    sessionText(message.request_id);
    const parentId = String(message.request_id);
    let pending = this.pending.get(parentId);
    const childRequest = message.message_type === "query" || (message.message_type === "consumed" && parentId.startsWith("child-"));
    if (childRequest) {
      if (!parentId.startsWith("child-") || this.childIds.has(parentId) || this.childIds.size >= 65_536)
        throw new AgentSessionError("child_request_id_reused_or_capacity");
      pending = [...this.pending.values()].find(item => item.kind === "next");
      if (!pending) throw new AgentSessionError("unsolicited_child_request");
      this.childIds.add(parentId);
    }
    if (!pending) throw new AgentSessionError("unknown_request_id");
    if (message.session_id !== pending.context.session_id || message.recovery_epoch !== pending.context.recovery_epoch)
      throw new AgentSessionError("agent_context_mismatch");
    const common = ["schema", "message_type", "session_id", "recovery_epoch", "request_id"];
    if (message.message_type === "state_exported" || message.message_type === "state_restored") {
      sessionObject(message, [...common, "output"]);
      const call = pending.stateCall;
      if (!call || (message.message_type === "state_exported") !== (pending.kind === "export_state"))
        throw new AgentSessionError("state_response_kind");
      call.authorization.assertCurrent();
      if (message.message_type === "state_restored") {
        const output = sessionObject(message.output, ["metadata"]);
        const metadata = requireAgentStateMetadata(output.metadata, call.authorization.expected_metadata, call.manifest);
        this.consumedInChild = true;
        this.finish(parentId, pending, metadata);
      } else {
        const output = sessionObject(message.output, ["metadata", "payload"]);
        const metadata = requireAgentStateMetadata(output.metadata, call.authorization.expected_metadata, call.manifest);
        const owned = acquireAgentStateBytes(output.payload, call.manifest.input.state_recovery.max_state_bytes, this.byteBudget);
        this.finish(parentId, pending, { state: { metadata, payload: output.payload as AgentOpaqueState["payload"] },
          bytes: owned.bytes, release: () => owned.reservation.release() });
      }
      return;
    }
    if (message.message_type === "error") {
      sessionObject(message, [...common, "error"]);
      const error = sessionObject(message.error, ["code", "message"]);
      sessionText(error.code); sessionText(error.message, 4096, true);
      this.fail(new AgentSessionError(`agent_error:${String(error.code)}`)); return;
    }
    if (message.message_type === "query") {
      sessionObject(message, [...common, "input"]);
      const query = validateAgentQuery(message.input);
      if (++pending.queries > this.limits.max_queries_per_turn || pending.pendingQueries >= this.limits.max_pending_queries)
        throw new AgentSessionError("agent_query_capacity");
      pending.queryBytes += encodeBoundedAgentJson(message, this.limits.max_message_bytes).length;
      if (pending.queryBytes > this.limits.max_query_bytes_per_turn) throw new AgentSessionError("agent_query_byte_capacity");
      pending.pendingQueries += 1;
      void this.handleQuery(parentId, pending, query).catch(error => this.fail(error)); return;
    }
    if (message.message_type === "consumed") {
      sessionObject(message, [...common, "completion"]);
      if ((pending.kind === "consume" && childRequest) || (pending.kind === "next" && !childRequest)
        || pending.stateCall !== undefined) throw new AgentSessionError("unexpected_consumption_response_kind");
      if (pending.reportPending) throw new AgentSessionError("consumption_report_in_flight");
      const report = validateAgentConsumption(message.completion);
      if (report.continuity_token !== pending.input.continuity_token) throw new AgentSessionError("agent_continuity_mismatch");
      if (pending.kind === "consume") {
        const input = pending.input as AgentConsumeInput;
        if (report.acquisition_id !== input.acquisition_id || report.previous_consumption_id !== input.previous_consumption_id
          || report.input_spec.id !== input.input_spec.id || report.input_spec.version !== input.input_spec.version
          || report.input_spec.sha256 !== input.input_spec.sha256) throw new AgentSessionError("consume_offer_binding");
      }
      pending.reportPending = true;
      void this.handleConsumed(parentId, pending, report, childRequest).catch(error => this.fail(error)); return;
    }
    if (message.message_type === "directive") {
      sessionObject(message, [...common, "output"]);
      if (pending.kind !== "next" || pending.reportPending || pending.pendingQueries !== 0) throw new AgentSessionError("directive_before_query_or_ack");
      const output = validateAgentDirective(message.output);
      const watermark = pending.latestAck ?? pending.input as AgentNextInput;
      if (output.continuity_token !== pending.input.continuity_token || output.consumption_id !== watermark.consumption_id
        || output.state_version !== watermark.state_version) throw new AgentSessionError("directive_watermark_mismatch");
      this.finish(parentId, pending, output); return;
    }
    throw new AgentSessionError("invalid_agent_message_type");
  }

  private async handleQuery(requestId: string, pending: Pending, query: AgentQuery): Promise<void> {
    const result = await pending.handlers!.query(query, pending.signal);
    if (this.closed || pending.signal.aborted) return;
    sessionObject(result, ["method", "value", "acquisition_id"]);
    if (result.method !== query.method || (query.method === "current") !== (result.acquisition_id !== null))
      throw new AgentSessionError("query_result_binding");
    if (result.acquisition_id !== null) sessionText(result.acquisition_id);
    const message = { schema: AGENT_SESSION_SCHEMA, message_type: "query_result", ...pending.context, request_id: requestId, result };
    const bytes = agentJsonByteLength(message, this.limits.max_message_bytes);
    pending.queryBytes += bytes;
    if (pending.queryBytes > this.limits.max_query_bytes_per_turn) throw new AgentSessionError("agent_query_byte_capacity");
    // Bind completion before the write can synchronously deliver to a child seam.
    pending.pendingQueries -= 1; this.write(message);
  }

  private async handleConsumed(requestId: string, pending: Pending, report: AgentConsumption, childRequest: boolean): Promise<void> {
    const ack = await pending.handlers!.consumed(report, pending.signal);
    if (this.closed || pending.signal.aborted) return;
    sessionObject(ack, ["consumption_id", "acquisition_id", "state_version", "advanced", "prefix"]);
    if (ack.consumption_id !== report.consumption_id || ack.acquisition_id !== report.acquisition_id
      || ack.state_version !== report.state_version || ack.advanced !== report.advanced
      || ack.prefix.continuity_token !== report.continuity_token) throw new AgentSessionError("consume_ack_binding");
    pending.latestAck = ack; pending.reportPending = false;
    this.consumedInChild = true;
    this.write({ schema: AGENT_SESSION_SCHEMA, message_type: "consume_ack", ...pending.context,
      request_id: requestId, completion: ack });
    if (!childRequest) this.finish(requestId, pending, ack);
  }

  private finish(requestId: string, pending: Pending, value: AgentConsumeAck | AgentDirectiveOutput | AgentExportedState | AgentStateMetadata): void {
    this.pending.delete(requestId); pending.cleanup(); pending.resolve(value);
  }
  private prepareWrite(value: unknown): { encoded: Buffer; reservation: AgentByteReservation } {
    const bytes = agentJsonByteLength(value, this.limits.max_message_bytes);
    // Charge both the encoded buffer and the queued newline-framed copy before allocation.
    const reservation = this.byteBudget.reserve(bytes * 2 + 1);
    try { return { encoded: encodeBoundedAgentJson(value, this.limits.max_message_bytes), reservation }; }
    catch (error) { reservation.release(); throw error; }
  }
  private write(value: unknown): void {
    const prepared = this.prepareWrite(value);
    try { this.writeEncoded(prepared.encoded, prepared.reservation); }
    catch (error) { prepared.reservation.release(); throw error; }
  }
  private writeEncoded(encoded: Buffer, reservation: AgentByteReservation): void {
    if (this.closed) throw new AgentSessionError("agent_port_closed");
    this.child.stdin.write(Buffer.concat([encoded, Buffer.from("\n")]), error => {
      reservation.release();
      if (error) this.fail(error);
    });
  }
  private fail(value: unknown): void {
    if (this.closed) return;
    this.closed = true;
    const error = value instanceof Error ? value : new AgentSessionError("agent_port_failed");
    if (!this.readyIdentity) this.rejectReady(error);
    for (const pending of this.pending.values()) { pending.cleanup(); pending.reject(error); }
    this.pending.clear();
    if (this.child.exitCode === null) this.child.kill("SIGKILL");
  }
}
