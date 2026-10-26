import { GlobalNoProgressWindow, type GlobalNoProgressConfig } from "./global-no-progress.js";
import { normalizeGlobalCaptureResponse } from "./global-capture-storage.js";
import { type FullPublicBasis, serializePublicBasisRecord, decodePublicSourceReceipt } from "@rsgcsg/sts2-connector-client";
import { createSession, reduce, modelView, applyCommitment, resolveFollowBasis, commitmentView, P1G_LIMITS, type P1SessionState, type P1Result, type P1StepRecord } from "@rsgcsg/sts2-connector-client/p1-global";
import { buildP1Result } from "./p1-result.js";
import { type GlobalTransport, type GlobalIdentity, assertGlobalIdentity, assertGlobalProgramSupport, globalDigest } from "./global-transport.js";
import { p1CoreIdentity } from "./p1-global/core-identity.js";
import type { GlobalArtifact, GlobalCaptureStagingRef } from "./evidence.js";
import type { ConnectorPolicyRequirements } from "./contracts.js";
import { createHash, randomUUID } from "node:crypto";
import {
  EnvironmentControllerSession, NativeLogicalSession, PlayerEnvironmentHttpError,
  PlayerEnvironmentRestClient, validateNativeLogicalRequest,
  type JsonObject, type NativeLogicalAction, type NativeLogicalByteBudget,
  type NativeLogicalCapabilities, type NativeLogicalCapturedObservation,
  type NativeLogicalExpression, type NativeLogicalFullCapture, type NativeLogicalPrefix, type NativeLogicalResult,
  type NativeLogicalScopeField, type NativeLogicalRetentionLease, type NativeLogicalOwnedCurrentReply, type NativeLogicalDispatchBinding
} from "@rsgcsg/sts2-connector-client";
import { AgentRunEvidence, canonicalJson } from "./evidence.js";
import { RUNTIME_ENVIRONMENT_SCHEMA, type AutonomyBudgetInput, type RuntimeControlPreconditions,
  type RuntimeEnvironmentBinding, type RuntimeMode, type RuntimeStatus } from "./contracts.js";
import { RuntimeControlPreconditionError, RuntimeLifecycleOwner } from "./runtime-owner.js";
import { AgentByteBudget } from "./agent-session-budget.js";
import type { AgentByteReservation } from "./agent-session-budget.js";
import { agentJsonByteLength } from "./agent-session-json.js";
import { AgentConsumptionLedger, type AgentAcquisition, type AgentNativeUnitWitness } from "./agent-session-consumption.js";
import { NdjsonAgentSessionPort, type AgentPortHandlers } from "./agent-session-port.js";
import { AgentSessionError, sessionText, supportsProfileValue, validateAgentManifest, sameAgentExecutionPolicy,
  isKnownStaleResult, AGENT_KNOWN_NOT_STARTED_OUTCOME_SCHEMA, AGENT_KNOWN_STALE_WINDOW_POLICY_SCHEMA,
  type AgentConsumeInput, type AgentOperationalOutcome, type AgentDirectiveOutput, type AgentConsumption, type AgentManifest, type AgentSessionContext } from "./agent-session-contracts.js";
import { canonicalStateMetadata, type AgentOpaqueState, type AgentStateMetadata } from "./agent-session-state.js";
import type { AgentPendingRequest, AgentReconcileResult, AgentRuntimeStatus, AgentRuntimeTickResult,
  RuntimeServiceOwner } from "./agent-runtime-contracts.js";
import type { AgentAcquisitionWitness, AgentRuntimeEventKind, AgentRuntimeEventPayloads } from "./agent-runtime-events.js";


interface SharedAgentRuntimeOptions {
  manifest: AgentManifest;
  port: NdjsonAgentSessionPort;
  evidence: AgentRunEvidence;
  runtimeIdentity: { version: string; code_sha256: string };
  mode?: RuntimeMode;
  autoBudget?: AutonomyBudgetInput;
  globalNoProgress?: GlobalNoProgressConfig;
  monotonicNow?: () => number;
  now?: () => string;
}
export type NativeAgentRuntimeOptions = SharedAgentRuntimeOptions & (
  { environment: PlayerEnvironmentRestClient; globalTransport?: never } | { environment?: never; globalTransport: GlobalTransport });
interface GlobalSource { full: FullPublicBasis; artifact: GlobalArtifact; ordinal: number }
interface GlobalPrepared { source: GlobalSource; reduction: ReturnType<typeof reduce>; acquisition: AgentAcquisition; unit: string }
export interface NativeAgentRuntimeOwner extends RuntimeServiceOwner {
  status(): AgentRuntimeStatus;
  setMode(mode: RuntimeMode, expected?: RuntimeControlPreconditions): Promise<AgentRuntimeStatus>;
  stop(): Promise<AgentRuntimeStatus>;
  tick(expected?: RuntimeControlPreconditions): Promise<AgentRuntimeTickResult>;
  reconcileOriginalRequest(requestId: string, expected?: RuntimeControlPreconditions): Promise<AgentReconcileResult>;
  exportAgentState(): Promise<{ state: AgentOpaqueState; receipt: { path: string; metadata_path: string; bytes: number; sha256: string } }>;
  restoreAgentState(state: AgentOpaqueState, replacement: NdjsonAgentSessionPort): Promise<AgentStateMetadata>;
}

/** Selected only by PolicyRuntime.forAgent. The existing process/HTTP owner
 * constructs one SDK registration/controller, not a parallel gameplay service. */
class NativeAgentRuntime implements NativeAgentRuntimeOwner {
  private readonly manifest: AgentManifest;
  private readonly owner: RuntimeLifecycleOwner;
  private readonly controller: EnvironmentControllerSession | null;
  private readonly native: NativeLogicalSession | null;
  private globalIdentity?: GlobalIdentity;
  private globalState?: P1SessionState;
  private globalPrepared?: GlobalPrepared;
  private globalPendingSource: GlobalSource | null = null;
  private globalSourceOrdinal = 0;
  private globalEpisode: string | null = null;
  private readonly globalNoProgress?: GlobalNoProgressWindow;
  private readonly budget: AgentByteBudget;
  private readonly ledger: AgentConsumptionLedger;
  private readonly sessionId = `agent-${randomUUID()}`;
  private readonly manifestSha256: string;
  private readonly now: () => string;
  private port: NdjsonAgentSessionPort;
  private mode: RuntimeMode;
  private stopped = false;
  private stopping = false;
  private tickActive = false;
  private refreshing = false;
  private tainted = false;
  private taintReason: string | null = null;
  private agentUncertain = false;
  private environment: RuntimeStatus["environment"] = null;
  private capabilities: NativeLogicalCapabilities | null = null;
  private cursor: string | null = null;
  private readonly knownCursors = new Set<string>();
  private readonly knownAcquisitions = new Set<string>();
  private readonly knownActions = new Map<string, Map<string, NativeLogicalAction>>();
  private readonly actionReservations = new Map<string, AgentByteReservation[]>();
  private readonly readerLeases = new Map<string, NativeLogicalRetentionLease>();
  private freshDecision: { outcome: AgentOperationalOutcome; unit: Readonly<AgentNativeUnitWitness>; advancedTo: string | null } | null = null;
  // Deduplication of terminal evidence counters only; no request/delivery authority.
  private readonly countedTerminalResults = new Map<string, string>();
  private knownStaleRejections = 0;
  private consecutiveKnownStaleRejections = 0;
  private terminationReason: string | null = null;
  private knownStaleWindow: { deadline: number; epoch: number; generation: number } | null = null;
  private basisId: string | null = null;
  private readonly samplePayloads = new Map<string, { bytes: Buffer; reservation: AgentByteReservation; offered: boolean;
    stored: boolean }>();
  private sampleOfferEvidence: Promise<void> = Promise.resolve();
  private sampleSegmentStarted = false;
  private sampleSegmentEnded = false;
  private sampleNextRequestId: string | null = null;
  private lastAckMetadata: AgentStateMetadata | null = null;
  private lastObservation: AgentRuntimeStatus["last_observation"] = null;
  private lastDirective: AgentRuntimeStatus["last_directive"] = null;
  private lastResult: AgentRuntimeStatus["last_result"] = null;
  private pending: AgentPendingRequest | null = null;
  private errors: string[] = [];
  private invalidations: string[] = [];
  private budgetFenced = false;
  private budgetHandoff: Promise<void> | null = null;
  private renewalTimer: ReturnType<typeof setTimeout> | undefined;
  private renewalGeneration = 0;
  private renewalFlight: Promise<void> | null = null;
  private renewalAbort: AbortController | null = null;
  private renewalHandoff: { gap: Record<string, unknown>; released: Promise<void> } | null = null;
  private releaseInFlight = 0;
  private releaseOperation: Promise<void> | null = null;
  private renewalFailed = false;
  private readonly renewalAdvisories: Record<string, unknown>[] = [];

  constructor(private readonly options: NativeAgentRuntimeOptions) {
    const validated = validateAgentManifest(options.manifest);
    this.manifest = immutable(JSON.parse(canonicalJson(validated)) as AgentManifest);
    if (!sameAgentExecutionPolicy(this.manifest.execution_policy, options.port.executionPolicy))
      throw new AgentSessionError("agent_execution_policy_mismatch");
    if (!options.runtimeIdentity.version || !/^[a-f0-9]{64}$/u.test(options.runtimeIdentity.code_sha256))
      throw new AgentSessionError("exact_runtime_identity_required");
    if (options.globalNoProgress) {
      if (!options.globalTransport) throw new AgentSessionError("global_no_progress_profile_required");
      this.globalNoProgress = new GlobalNoProgressWindow(options.globalNoProgress, options.port.byteBudget);
    }
    this.manifestSha256 = createHash("sha256").update(canonicalJson(this.manifest)).digest("hex");
    this.now = options.now ?? (() => new Date().toISOString());
    this.mode = options.mode ?? "human";
    this.owner = new RuntimeLifecycleOwner(options.autoBudget, options.monotonicNow ?? (() => performance.now()), this.mode);
    this.budget = options.port.byteBudget;
    if (this.budget.maximum !== this.manifest.limits.max_retained_acquisition_bytes)
      throw new AgentSessionError("agent_shared_byte_budget_mismatch");
    this.ledger = new AgentConsumptionLedger(this.manifest, `continuity-${randomUUID()}`, this.budget);
    this.port = options.port;
    if ((this.manifest.input.profile === "p1-global-r1") !== Boolean(options.globalTransport)) throw new AgentSessionError("global_transport_profile_mismatch");
    this.controller = options.globalTransport ? null : new EnvironmentControllerSession(options.environment!, {
      productId: "sts2.policy-runtime", productName: "STS2 Policy Runtime", productVersion: options.runtimeIdentity.version,
      clientInstanceId: options.evidence.runId
    });
    this.native = options.globalTransport ? null : new NativeLogicalSession(options.environment!, this.controller!);
    if (options.globalTransport) this.port.setFrameObserver(async (direction, bytes) => {
      const artifact = await options.evidence.storeGlobalArtifact(direction === "in" ? "agent_in" : "agent_out", bytes);
      await this.emitGlobal("global_agent_frame", { direction, artifact });
    });
  }

  async initialize(): Promise<void> {
    const active = new AbortController(); this.owner.active = { controller: active };
    this.owner.scheduleDeadline(() => this.expireBudget());
    try {
      // Scoped opaque recovery requires a registered materialization-aware
      // InputSpec. The implemented second consumer explicitly declares none.
      if (this.manifest.input.history_mode === "scoped_query" && this.manifest.input.state_recovery.mode !== "none")
        throw new AgentSessionError("scoped_opaque_state_binding_unsupported");
      if (this.manifest.input.gap_policy !== "handoff") throw new AgentSessionError("native_explicit_reset_unsupported");
      await this.options.evidence.attestAdapter(await this.port.ready(undefined, active.signal));
      if (this.options.globalTransport) { await this.initializeGlobal(active.signal); return; }
      active.signal.throwIfAborted();
      const reply = await this.native!.capabilities(active.signal);
      this.admitCapabilities(reply.data); this.capabilities = reply.data;
      this.environment = nativeEnvironment(reply.data, (this.manifest.requirements.environment as ConnectorPolicyRequirements["environment"]).host_kind);
      await this.controller!.register(reply.data.session, reply.data.control_policy);
      active.signal.throwIfAborted();
      const attached = (await this.native!.attach({ eagerScope: this.manifest.input.attachment.eager_scope,
        requiredSeams: this.manifest.input.attachment.required_seams,
        deliveryMode: this.manifest.input.attachment.delivery_mode, signal: active.signal })).data;
      if (attached.status !== "attached" || !attached.subscription) throw new AgentSessionError(`native_attach_${attached.status}`);
      this.cursor = attached.subscription.starting_cursor;
      this.noteCursor(this.cursor, 0);
      this.scheduleRenewal();
      await this.emit("native_session_attached", { ...this.context(), subscription: attached.subscription, environment: this.environment });
      if (this.manifest.execution_policy)
        await this.emit("mode_changed", { ...this.context(), mode: this.mode, autonomy_budget: this.budgetStatus(), controller: this.controllerStatus() });
      active.signal.throwIfAborted();
    } catch (error) {
      this.owner.cancelActive(error); this.port.close();
      await this.release().catch(() => undefined);
      await this.quiesceRenewal();
      if (this.native?.subscription) await this.native!.detach().catch(() => undefined);
      this.owner.clearDeadline(); this.globalNoProgress?.close();
      // Deadline expiry owns serialized evidence even when initialization
      // aborts. Finish that write before the caller releases failed-run files.
      if (this.budgetHandoff) await this.budgetHandoff;
      throw error;
    } finally { if (this.owner.active?.controller === active) this.owner.active = null; }
  }

  status(): AgentRuntimeStatus {
    const budget = this.owner.status(() => this.expireBudget());
    return { schema: "sts2.policy-runtime/agent-session-status-2", runtime: { ...this.options.runtimeIdentity },
      agent: { manifest_id: this.manifest.manifest_id, agent_id: this.manifest.agent.id,
        agent_version: this.manifest.agent.version, provider: this.manifest.agent.provider,
        architecture: this.manifest.agent.architecture, artifact_id: this.manifest.artifact.id,
        artifact_sha256: this.manifest.artifact.sha256, adapter: { ...this.manifest.adapter } },
      agent_manifest_sha256: this.manifestSha256, run_id: this.options.evidence.runId,
      lifecycle: this.stopped ? "stopped" : "running", mode: this.mode, controller: this.controllerStatus(),
      autonomy_budget: budget, ...(this.globalNoProgress ? { global_no_progress: this.globalNoProgress.status() } : {}), tainted: this.tainted, taint_reason: this.taintReason, refreshing: this.refreshing,
      environment: this.environment, session: { ...this.context(), profile: this.manifest.input.profile,
        input_spec: { ...this.manifest.input.input_spec }, stream_generation: this.capabilities?.stream_generation ?? null,
        continuity_token: this.ledger.continuityToken, state_version: this.ledger.stateVersion,
        consumption_id: this.ledger.consumptionId, prefix: this.ledger.prefix(), agent_state: this.agentUncertain ? "uncertain" : "known" },
      last_observation: this.lastObservation, last_directive: this.lastDirective, last_result: this.lastResult,
      pending_request: this.pending ? { ...this.pending } : null, errors: [...this.errors], invalidations: [...this.invalidations] };
  }

  async readEnvironment(): Promise<RuntimeEnvironmentBinding> {
    const epoch = this.owner.epoch; this.owner.checkEpoch(epoch);
    const runtime = await this.freshRuntime(); this.owner.checkEpoch(epoch);
    return { schema: RUNTIME_ENVIRONMENT_SCHEMA, run_id: this.options.evidence.runId,
      runtime_instance_id: runtime, recovery_epoch: epoch };
  }

  async setMode(mode: RuntimeMode, expected?: RuntimeControlPreconditions): Promise<AgentRuntimeStatus> {
    if (mode === "human") this.revoke("human_recovery");
    const earlyRelease = mode === "human" ? this.release().catch(() => undefined) : Promise.resolve();
    return this.owner.serialize(async () => {
      if (mode !== "human") { await this.checkPreconditions(expected); this.admitMutation(); }
      if (this.stopped || this.stopping) throw new RuntimeControlPreconditionError("runtime_stopped", 409);
      await earlyRelease;
      if (mode === "human") { this.owner.end("human_recovery"); await this.endSampleSegment("human_recovery"); }
      else if (this.mode === "human" || this.owner.state.state !== "active") {
        this.knownStaleWindow = null;
        this.budgetFenced = false; this.budgetHandoff = null; this.terminationReason = null;
        this.owner.begin(() => this.expireBudget());
      }
      if (mode !== "auto") await this.release();
      this.mode = mode;
      await this.emit("mode_changed", { ...this.context(), mode, autonomy_budget: this.budgetStatus(), controller: this.controllerStatus() });
      return this.status();
    });
  }

  async tick(expected?: RuntimeControlPreconditions): Promise<AgentRuntimeTickResult> {
    this.owner.checkEpoch(expected?.recoveryEpoch);
    if (this.tickActive) return { type: "not_admitted", reason: "tick_in_progress", status: this.status() };
    this.tickActive = true;
    try { return await this.owner.serialize(async () => {
      this.checkKnownStaleDeadline();
      await this.checkPreconditions(expected);
      if (this.mode === "human" || this.stopped || this.stopping) return { type: "human", status: this.status() };
      this.admitMutation();
      const active = new AbortController(), epoch = this.owner.epoch;
      this.owner.active = { controller: active };
      const offer = { pending: false };
      const handlers = this.handlers(epoch);
      try {
        if (!this.owner.available()) { this.expireBudget(); return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() }; }
        await this.receive(active.signal, epoch, handlers, offer);
        this.checkActive(epoch, active.signal);
        if (this.manifest.input.history_mode === "full_reference" && this.basisId === null)
          return { type: "observation", status: this.status() };
        if (!this.owner.consumeCall()) { this.expireBudget(); return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() }; }
        const nextInput = { continuity_token: this.ledger.continuityToken,
          consumption_id: this.ledger.consumptionId, state_version: this.ledger.stateVersion,
          basis_acquisition_id: this.basisId, received_cursor: this.cursor,
          ...(this.manifest.execution_policy ? { operational_outcome: this.freshDecision?.outcome ?? null } : {}) };
        const output = await this.port.next(this.context(), nextInput, handlers, active.signal, requestId => {
          offer.pending = true;
          if (this.manifest.input.history_mode === "sampled_current") {
            this.agentUncertain = true; this.sampleNextRequestId = requestId;
            const context = this.context();
            this.sampleOfferEvidence = this.sampleOfferEvidence.then(() => this.emit("agent_sample_next_requested",
              { ...context, request_id: requestId, input: nextInput }));
            void this.sampleOfferEvidence.catch(() => undefined);
          }
        });
        offer.pending = false;
        await this.sampleOfferEvidence;
        this.checkActive(epoch, active.signal);
        this.requireWatermark(output);
        if (this.manifest.input.history_mode === "sampled_current") {
          if (this.sampleNextRequestId === null) throw new AgentSessionError("sample_next_request_binding");
          await this.emit("agent_sample_next_completed", { ...this.context(), request_id: this.sampleNextRequestId, output });
          this.checkActive(epoch, active.signal);
        }
        this.lastDirective = output.directive;
        await this.emit("agent_directive", { ...this.context(), output });
        if (this.manifest.input.history_mode === "sampled_current") { this.agentUncertain = false; this.sampleNextRequestId = null; }
        const fresh = this.freshDecision;
        if (fresh && fresh.advancedTo !== null && fresh.advancedTo === this.basisId) this.freshDecision = null;
        this.checkActive(epoch, active.signal);
        switch (output.directive.type) {
          case "act": return await this.act(output, epoch, active.signal);
          case "await": {
            if (!this.knownCursors.has(output.directive.after_cursor)) throw new AgentSessionError("unknown_await_cursor");
            const waitId = randomUUID().replaceAll("-", "");
            const remaining = this.budgetStatus().remaining_ms;
            const timeout = remaining === null ? output.directive.timeout_ms : Math.min(output.directive.timeout_ms, remaining);
            if (timeout <= 0) { this.expireBudget(); return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() }; }
            if (this.options.globalTransport) { await globalDelay(timeout, active.signal); this.checkActive(epoch, active.signal); return { type: "awaited", status: this.status() }; }
            const result = (await this.native!.await({ waitId, afterCursor: output.directive.after_cursor,
              condition: output.directive.condition, timeoutMs: timeout,
              controlDependent: this.mode === "auto" && this.controllerStatus() === "held", signal: active.signal })).data;
            this.checkActive(epoch, active.signal);
            await this.emit("native_await_result", { ...this.context(), wait_id: waitId, after_cursor: output.directive.after_cursor, result });
            if (result.status === "gap" && result.gap) {
              if (this.manifest.input.history_mode === "sampled_current")
                await this.emit("agent_sample_publication_gap", { ...this.context(), gap: result.gap, received_cursor: this.cursor! });
              else await this.gap(result.gap);
            }
            else if (!["event", "timeout"].includes(result.status)) await this.handoff(`native_await_${result.status}`);
            // Conditional Await can match a later publication. Its event never
            // skips earlier promised occurrences; receive() uses the old cursor.
            return { type: "awaited", status: this.status() };
          }
          case "abstain": await this.handoff(output.directive.reason); return { type: "not_admitted", reason: "agent_abstained", status: this.status() };
          case "close": await this.finishStop(); return { type: "closed", status: this.status() };
        }
      } catch (error) {
        if (offer.pending) this.agentUncertain = true;
        const reason = errorMessage(error);
        if (this.options.globalTransport && (this.budgetFenced || this.stopping))
          return { type: "not_admitted", reason: this.terminationReason ?? "stopped", status: this.status() };
        if (this.windowHandoffOwnsCleanup())
          return { type: "not_admitted", reason: this.terminationReason!, status: this.status() };
        await this.failClosed(reason);
        return { type: this.tainted ? "unknown" : "not_admitted", ...(this.tainted ? { error: reason } : { reason }), status: this.status() } as AgentRuntimeTickResult;
      } finally {
        try { await this.cleanupSampleQueries(offer.pending || this.agentUncertain); }
        catch (error) {
          if (!this.manifest.execution_policy) throw error;
          // Cleanup owns this failure even after a known result/normal Await.
          // Fence immediately; a caller need not issue another tick or Stop.
          this.terminationReason ??= "sample_query_cleanup_failed";
          this.mode = "human"; this.owner.end("mode_changed"); this.owner.advanceEpoch(); this.owner.cancelActive(error);
          const errors = [error];
          try { await this.endSampleSegment(this.terminationReason); } catch (cleanup) { errors.push(cleanup); }
          finally { try { await this.release(); } catch (cleanup) { errors.push(cleanup); } }
          throw new AggregateError(errors, "native tick owned-reader cleanup failed");
        }
        finally { if (this.owner.active?.controller === active) this.owner.active = null; }
      }
    }); } finally { this.tickActive = false; }
  }

  async reconcileOriginalRequest(requestId: string, expected?: RuntimeControlPreconditions): Promise<AgentReconcileResult> {
    return this.owner.serialize(async () => {
      sessionText(requestId, 128); await this.checkPreconditions(expected);
      const original = this.pending;
      if (!original || requestId !== original.request_id || original.run_id !== this.options.evidence.runId
        || original.session_id !== this.sessionId || original.runtime_instance_id !== this.environment?.runtime_instance_id)
        throw new RuntimeControlPreconditionError("runtime_pending_request_mismatch", 409);
      if (this.stopped || this.stopping) throw new RuntimeControlPreconditionError("runtime_stopped", 409);
      if (this.mode !== "human") throw new RuntimeControlPreconditionError("runtime_reconcile_requires_human", 409);
      try {
        const lookup = await this.native!.result(original.request_id, undefined, original.dispatch_binding);
        if (lookup.status === "pending") {
          await this.emit("native_request_reconciled", { ...this.context(), original, resolution: "pending", result: null });
          return { request_id: requestId, resolution: "pending", status: this.status() };
        }
        const result = lookup.result.data;
        if (result.snapshot_id !== original.snapshot_id || result.action !== null && result.action.action_id !== original.action_id)
          throw new AgentSessionError("native_original_request_basis_mismatch");
        this.recordResult(result);
        this.countOriginalTerminal(result, original.dispatch_binding);
        if (result.delivery === "unknown" || result.delivery === "partially_delivered") {
          await this.taint(`native_delivery_${result.delivery}`);
          await this.emit("native_request_reconciled", { ...this.context(), original, resolution: "tainted", result });
          return { request_id: requestId, resolution: "tainted", status: this.status() };
        }
        await this.emit("native_request_reconciled", { ...this.context(), original, resolution: "resolved", result });
        this.pending = null;
        return { request_id: requestId, resolution: "resolved", status: this.status() };
      } catch (error) {
        const reason = errorMessage(error);
        this.pending = immutable({ ...original, status: "unresolved", reason });
        await this.emit("native_request_unresolved", { ...this.context(), original, reason });
        return { request_id: requestId, resolution: "unresolved", status: this.status() };
      }
    });
  }

  async stop(): Promise<AgentRuntimeStatus> {
    if (!this.stopped) { this.stopping = true; this.revoke("stopped"); }
    const quiet = this.quiesceRenewal();
    const release = this.release().catch(() => undefined);
    return this.owner.serialize(async () => { await release; await quiet; await this.finishStop(); return this.status(); });
  }

  async exportAgentState() {
    return this.owner.serialize(async () => {
      const authorization = this.stateAuthorization();
      const active = new AbortController(); this.owner.active = { controller: active };
      try {
        const exported = await this.port.exportState(this.context(), this.manifest, authorization, active.signal, () => undefined);
        try {
          const receipt = await this.options.evidence.storeAgentState(exported.state.metadata, exported.bytes, exported.state.payload.sha256);
          await this.emit("agent_state_stored", { ...this.context(), metadata: exported.state.metadata, ...receipt });
          return { state: exported.state, receipt };
        } finally { exported.release(); }
      } finally { if (this.owner.active?.controller === active) this.owner.active = null; }
    });
  }

  async restoreAgentState(state: AgentOpaqueState, replacement: NdjsonAgentSessionPort): Promise<AgentStateMetadata> {
    if (this.mode !== "human") throw new AgentSessionError("restore_requires_human");
    return this.owner.serialize(async () => {
      if (this.mode !== "human") throw new AgentSessionError("restore_requires_human");
      if (!sameAgentExecutionPolicy(this.manifest.execution_policy, replacement.executionPolicy)) {
        replacement.close(); throw new AgentSessionError("agent_execution_policy_mismatch");
      }
      this.owner.advanceEpoch();
      const authorization = this.stateAuthorization(true);
      if (replacement.byteBudget !== this.budget) throw new AgentSessionError("agent_shared_byte_budget_mismatch");
      if (canonicalStateMetadata(state.metadata) !== canonicalStateMetadata(authorization.expected_metadata))
        throw new AgentSessionError("state_durable_prefix_mismatch");
      try {
        await this.freshRuntime();
        authorization.assertCurrent();
      } catch (error) { replacement.close(); throw error; }
      this.port.close();
      const active = new AbortController(); this.owner.active = { controller: active };
      try {
        await this.options.evidence.attestAdapter(await replacement.ready(undefined, active.signal));
        const metadata = await replacement.restoreState(this.context(), this.manifest, authorization, state, active.signal, () => undefined);
        await this.emit("agent_state_restored", { ...this.context(), metadata });
        this.port = replacement; this.agentUncertain = false;
        return metadata;
      } catch (error) { replacement.close(); this.agentUncertain = true; throw error; }
      finally { if (this.owner.active?.controller === active) this.owner.active = null; }
    });
  }

  private context(): AgentSessionContext { return { session_id: this.sessionId, recovery_epoch: this.owner.epoch }; }
  private controllerStatus(): RuntimeStatus["controller"] {
    if (this.options.globalTransport) return this.releaseInFlight > 0 ? "unknown" : this.options.globalTransport.controlStatus();
    const value = this.controller!.snapshot();
    return this.releaseInFlight > 0 || value.controller_release_uncertain === true || value.controller_acquire_uncertain === true ? "unknown"
      : value.controller_lease_id === null ? "released" : "held";
  }
  private budgetStatus() { return this.owner.status(() => this.expireBudget()); }
  private checkActive(epoch: number, signal: AbortSignal): void {
    this.checkKnownStaleDeadline();
    if (signal.aborted && signal.reason instanceof NativeRuntimeRevocation && epoch !== this.owner.epoch)
      throw signal.reason;
    this.owner.checkEpoch(epoch); signal.throwIfAborted();
  }
  private admitMutation(): void {
    this.checkKnownStaleDeadline();
    if (this.windowHandoffOwnsCleanup()) throw new RuntimeControlPreconditionError(this.terminationReason!, 409);
    if (this.sampleSegmentEnded) throw new RuntimeControlPreconditionError("runtime_sample_segment_ended", 409);
    if (this.pending) throw new RuntimeControlPreconditionError("runtime_pending_request_unresolved", 409);
    if (this.tainted) throw new RuntimeControlPreconditionError("runtime_tainted", 409);
    if (this.agentUncertain) throw new RuntimeControlPreconditionError("runtime_agent_state_uncertain", 409);
    if (this.controllerStatus() === "unknown") throw new RuntimeControlPreconditionError("runtime_controller_unresolved", 409);
    if (this.renewalFailed) throw new RuntimeControlPreconditionError("runtime_subscription_unavailable", 409);
    if (this.ledger.prefix().omissions.gap !== null) throw new RuntimeControlPreconditionError("runtime_source_gap", 409);
  }
  private revoke(reason: "human_recovery" | "stopped"): void {
    this.terminationReason ??= reason;
    this.knownStaleWindow = null;
    this.owner.advanceEpoch(); this.owner.cancelActive(new NativeRuntimeRevocation(reason)); this.mode = "human"; this.owner.end(reason);
  }
  private async freshRuntime(): Promise<string> {
    try {
      if (this.options.globalTransport) {
        const identity = await this.options.globalTransport.negotiate(new AbortController().signal);
        const expected = this.manifest.requirements.environment;
        if (!("kind" in expected)) throw new AgentSessionError("global_environment_contract");
        assertGlobalIdentity(identity, expected, this.globalIdentity); return identity.session.runtime_instance_id;
      }
      const capabilities = (await this.native!.capabilities()).data;
      this.admitCapabilities(capabilities);
      if (this.environment && capabilities.session.runtime_instance_id !== this.environment.runtime_instance_id)
        throw new RuntimeControlPreconditionError("runtime_game_mismatch", 409);
      return capabilities.session.runtime_instance_id;
    } catch (error) {
      if (error instanceof RuntimeControlPreconditionError) throw error;
      throw new RuntimeControlPreconditionError("runtime_environment_unavailable", 503);
    }
  }
  private async checkPreconditions(expected?: RuntimeControlPreconditions): Promise<void> {
    this.owner.checkEpoch(expected?.recoveryEpoch);
    if (expected?.gameInstanceId !== undefined) {
      if (!expected.gameInstanceId.trim()) throw new RuntimeControlPreconditionError("runtime_game_precondition_required", 428);
      if (await this.freshRuntime() !== expected.gameInstanceId) throw new RuntimeControlPreconditionError("runtime_game_mismatch", 409);
    }
    this.owner.checkEpoch(expected?.recoveryEpoch);
  }
  private admitCapabilities(c: NativeLogicalCapabilities): void {
    const e = this.manifest.requirements.environment as ConnectorPolicyRequirements["environment"];
    if (c.protocol_version !== this.manifest.requirements.connector_protocol_version || c.input_profile !== "native-logical-v1")
      throw new AgentSessionError("connector_native_profile_unsupported");
    if (c.host.host_kind !== e.host_kind || c.host.version !== e.connector_version
      || c.host.implementation.source_revision !== e.connector_source_revision
      || c.host.implementation.artifact_sha256 !== e.connector_artifact_sha256
      || c.host.implementation.module_version_id !== e.connector_module_version_id
      || c.game.modset.status !== e.modset_status || c.game.modset.fingerprint !== e.modset_fingerprint
      || canonicalJson(c.game.modset.loaded_mod_ids) !== canonicalJson(e.loaded_mod_ids)) throw new AgentSessionError("native_environment_identity_drift");
    if (c.game.version === null || !this.manifest.support.game_versions.includes(c.game.version)
      || c.game.commit === null || !this.manifest.support.game_commits.includes(c.game.commit)) throw new AgentSessionError("game_identity_unsupported");
    if (!c.game.compatibility.observation_allowed || this.manifest.requirements.required_methods.some(value => !c.supported_methods.includes(value)))
      throw new AgentSessionError("native_required_method_unsupported");
    if (this.manifest.execution_policy && !c.implemented_mechanisms.includes("native_current_reader_owned_v1"))
      throw new AgentSessionError("native_owned_current_mechanism_unsupported");
    if (this.manifest.input.attachment.required_seams.some(seam => !c.capture_coverage.some(value =>
      value.source_seam === seam.source_seam && value.version === seam.version && value.coverage === seam.coverage)))
      throw new AgentSessionError("native_required_seam_unsupported");
    if (this.capabilities && (c.stream_generation !== this.capabilities.stream_generation
      || c.session.environment_fingerprint !== this.capabilities.session.environment_fingerprint)) throw new AgentSessionError("native_generation_or_environment_drift");
  }
  private noteCursor(cursor: string, count: number): void {
    this.cursor = cursor; this.knownCursors.add(cursor);
    while (this.knownCursors.size > this.manifest.limits.max_acquisitions) this.knownCursors.delete(this.knownCursors.values().next().value!);
    this.ledger.noteReceived(cursor, count);
  }
  private async receive(signal: AbortSignal, epoch: number, handlers: AgentPortHandlers, offer: { pending: boolean }): Promise<void> {
    if (this.options.globalTransport) return this.receiveGlobal(signal, epoch, handlers, offer);
    if (this.cursor === null) throw new AgentSessionError("native_subscription_required");
    this.refreshing = true;
    try {
      for (;;) {
      const limit = Math.min(64, this.capabilities!.limits.max_events);
      const before = this.cursor;
      const batch = (await this.native!.events({ afterCursor: before, limit, signal })).data;
      this.checkActive(epoch, signal);
      if (batch.gap) {
        if (this.manifest.input.history_mode === "sampled_current") {
          // Advisory publication retention is not a missing Current sample.
          this.noteCursor(batch.next_cursor, 0);
          await this.emit("agent_sample_publication_gap", { ...this.context(), gap: batch.gap, received_cursor: batch.next_cursor });
          break;
        }
        await this.gap(batch.gap); throw new AgentSessionError("native_source_gap");
      }
      for (const original of batch.events) {
        // Every public source entry carries a captured view or its explicit
        // missing fact. Open event kind classifies Await, not input eligibility.
        this.noteCursor(original.event.cursor, 1);
        await this.emit("native_event_received", { ...this.context(), original, received_cursor: original.event.cursor });
        if (this.manifest.input.history_mode !== "full_reference") continue;
        if (original.availability !== "available" || original.event.payload_reference === null) {
          await this.gap({ reason: original.availability === "missing" ? original.event.missing_reason ?? "required_capture_missing" : "payload_expired",
            from_publication_index: original.event.publication_index, through_publication_index: original.event.publication_index });
          throw new AgentSessionError("required_capture_missing");
        }
        if (original.event.payload_reference.byte_count > this.manifest.limits.max_capture_bytes) throw new AgentSessionError("capture_byte_capacity");
        if (!this.owner.consumeCall()) { this.expireBudget(); throw new AgentSessionError("autonomy_budget_exhausted"); }
        const full = await this.native!.getFullEvent(original, { signal, budget: this.assemblyBudget(), maxActions: this.manifest.limits.max_catalog_actions });
        try {
          const id = await this.register(full, full.actions, original.event.publication_index);
          const a = this.ledger.get(id);
          await this.port.consume(this.context(), { acquisition_id: id, input_spec: this.manifest.input.input_spec,
            continuity_token: this.ledger.continuityToken, previous_consumption_id: this.ledger.consumptionId,
            observation: a.observation, catalog: a.catalog }, handlers, signal, () => { offer.pending = true; });
          offer.pending = false;
          this.checkActive(epoch, signal);
        } finally { await full.dispose(); }
      }
      this.checkActive(epoch, signal);
      this.noteCursor(batch.next_cursor, 0);
      await this.emit("native_event_batch_received", { ...this.context(), after_cursor: before, next_cursor: batch.next_cursor,
        high_watermark: batch.high_watermark, retained_start_cursor: batch.retained_start_cursor, event_count: batch.events.length });
      this.checkActive(epoch, signal);
      if (this.manifest.input.history_mode !== "full_reference" || batch.next_cursor === batch.high_watermark
        || batch.events.length === 0) break;
      if (batch.next_cursor === before) throw new AgentSessionError("native_event_cursor_no_progress");
      }
    } finally { this.refreshing = false; }
  }
  private assemblyBudget(): NativeLogicalByteBudget {
    return { reserve: ({ bytes, signal }) => {
      signal?.throwIfAborted(); let charged = bytes, held = this.budget.reserve(bytes), released = false;
      return { resize: next => {
        if (!Number.isSafeInteger(next) || next < 0 || next > charged || released) throw new AgentSessionError("assembly_reservation_resize");
        held.release(); charged = next; held = this.budget.reserve(next);
      }, release: () => { if (!released) { released = true; held.release(); } } };
    } };
  }
  private async register(capture: NativeLogicalCapturedObservation, catalog: readonly NativeLogicalAction[] | null, publication: string | null): Promise<string> {
    const id = `acquisition-${randomUUID()}`;
    const a: AgentAcquisition = { acquisition_id: id, capture: capture.capture as unknown as Record<string, unknown>,
      observation: capture.observation as unknown as Record<string, unknown>, catalog: catalog as readonly Record<string, unknown>[] | null,
      catalog_materialized: catalog !== null, publication_index: publication };
    this.ledger.register(a); this.knownAcquisitions.add(id);
    try {
    if (this.freshDecision) this.checkFreshUnit(this.ledger.nativeUnit(id), false);
    if (this.manifest.input.history_mode === "sampled_current") {
      const reservation = this.budget.reserve(Buffer.byteLength(capture.serializedObservation, "utf8"));
      try { this.samplePayloads.set(id, { bytes: Buffer.from(capture.serializedObservation), reservation, offered: false, stored: false }); }
      catch (error) { reservation.release(); throw error; }
    }
    const retained = this.ledger.get(id).catalog as unknown as readonly NativeLogicalAction[] | null;
    this.knownActions.set(id, new Map(retained?.map(action => [action.action_id, action]) ?? []));
    this.lastObservation = { acquisition_id: id, capture_sha256: capture.capture.sha256, snapshot_id: capture.observation.snapshot_id,
      revision: capture.observation.revision, status: capture.observation.status, publication_index: publication,
      included: [...capture.observation.completeness.included], missing: [...capture.observation.completeness.missing],
      catalog_count: capture.observation.catalog.total_count };
    await this.emit("native_acquisition_registered", { ...this.context(), witness: this.witness(id) });
    return id;
    } catch (error) {
      try { await this.releaseAcquisition(id); }
      catch (cleanup) { throw new AggregateError([error, cleanup], "native acquisition registration cleanup failed"); }
      throw error;
    }
  }
  private async releaseAcquisition(id: string): Promise<void> {
    const lease = this.readerLeases.get(id); this.readerLeases.delete(id);
    const errors: unknown[] = [];
    try {
      try { this.ledger.release(id); } catch (error) { errors.push(error); }
      this.knownAcquisitions.delete(id); this.knownActions.delete(id);
      for (const held of this.actionReservations.get(id) ?? [])
        try { held.release(); } catch (error) { errors.push(error); }
      this.actionReservations.delete(id);
      try { this.samplePayloads.get(id)?.reservation.release(); } catch (error) { errors.push(error); }
      this.samplePayloads.delete(id);
    } finally { try { await lease?.dispose(); } catch (error) { errors.push(error); } }
    if (errors.length) throw new AggregateError(errors, "native acquisition local/reader cleanup failed");
  }
  private async persistSample(id: string, disposition: "query_offered" | "consume_proposed", proposal: AgentSessionContext & { request_id: string; report: AgentConsumption } | null = null): Promise<void> {
    const payload = this.samplePayloads.get(id);
    if (!payload || !payload.offered) throw new AgentSessionError("sample_original_offer_required");
    const a = this.ledger.get(id), catalog = a.observation.catalog as Record<string, unknown>;
    if (a.catalog === null || !a.catalog_materialized || a.publication_index !== null)
      throw new AgentSessionError("sample_complete_catalog_required");
    const receipt = await this.options.evidence.storeSampleAcquisition({ acquisition_id: id,
      input_spec: this.manifest.input.input_spec, continuity_token: this.ledger.continuityToken,
      capture: a.capture, observation: payload.bytes, catalog: a.catalog, catalog_digest: String(catalog.digest) });
    payload.stored = true;
    await this.emit("agent_sample_input_stored", { ...this.context(), ...receipt, disposition, proposal });
  }
  private async cleanupSampleQueries(uncertain: boolean): Promise<void> {
    if (this.manifest.input.history_mode !== "sampled_current") return;
    const errors: unknown[] = [];
    try { await this.sampleOfferEvidence; } catch (error) { errors.push(error); }
    for (const [id, payload] of [...this.samplePayloads]) {
      if (id === this.basisId) continue;
      try {
        if (uncertain && payload.offered && !payload.stored) await this.persistSample(id, "query_offered");
        if (!payload.stored) await this.emit("agent_sample_query_discarded", { ...this.context(), acquisition_id: id,
          reason: payload.offered ? "readiness_check" : "not_offered" });
      } catch (error) { errors.push(error); }
      finally { try { await this.releaseAcquisition(id); } catch (error) { errors.push(error); } }
    }
    if (errors.length) {
      this.tainted = true; this.taintReason = "sample_query_cleanup_failed";
      throw new AggregateError(errors, "sample query evidence/reader cleanup failed");
    }
  }
  private async endSampleSegment(reason: string): Promise<void> {
    if (this.manifest.input.history_mode !== "sampled_current" || !this.sampleSegmentStarted || this.sampleSegmentEnded) return;
    this.sampleSegmentEnded = true;
    await this.emit("agent_sample_segment_ended", { ...this.context(), continuity_token: this.ledger.continuityToken,
      reason, state_version: this.ledger.stateVersion });
  }
  private witness(id: string): AgentAcquisitionWitness {
    const a = this.ledger.get(id), o = a.observation, c = o.catalog as Record<string, unknown>;
    const completeness = o.completeness as { included: string[]; missing: string[] };
    return { acquisition_id: id, capture: a.capture as unknown as AgentAcquisitionWitness["capture"],
      publication_index: a.publication_index, snapshot_id: String(o.snapshot_id), revision: Number(o.revision),
      owner_occurrence: o.owner_occurrence as Record<string, unknown>, status: String(o.status),
      included: completeness.included, missing: completeness.missing, catalog_digest: c.digest as string | null,
      catalog_count: c.total_count as number | null, catalog_materialized: a.catalog_materialized };
  }
  private handlers(epoch: number): AgentPortHandlers {
    if (this.options.globalTransport) return this.globalHandlers(epoch);
    return { queryOffered: (result, requestId) => {
      if (this.manifest.input.history_mode !== "sampled_current" || result.acquisition_id === null) return;
      const payload = this.samplePayloads.get(result.acquisition_id);
      if (!payload) throw new AgentSessionError("sample_original_payload_missing");
      payload.offered = true;
      const context = this.context();
      this.sampleOfferEvidence = this.sampleOfferEvidence.then(() => this.emit("agent_sample_query_offered",
        { ...context, acquisition_id: result.acquisition_id!, request_id: requestId }));
      void this.sampleOfferEvidence.catch(() => undefined);
    }, ackOffered: (acknowledgement, requestId) => {
      if (this.manifest.input.history_mode !== "sampled_current") return;
      const context = this.context();
      this.sampleOfferEvidence = this.sampleOfferEvidence.then(() => this.emit("agent_sample_consume_ack_offered",
        { ...context, acknowledgement, request_id: requestId }));
      void this.sampleOfferEvidence.catch(() => undefined);
    }, consumed: async (report, signal, requestId) => {
      this.checkActive(epoch, signal);
      if (this.freshDecision) this.checkFreshUnit(this.ledger.nativeUnit(report.acquisition_id), true);
      await this.sampleOfferEvidence;
      if (this.manifest.input.history_mode === "sampled_current") await this.persistSample(report.acquisition_id, "consume_proposed", { session_id: this.sessionId, recovery_epoch: epoch, request_id: requestId, report });
      this.checkActive(epoch, signal);
      const ack = this.ledger.accept(report), witness = this.witness(report.acquisition_id);
      if (this.manifest.input.history_mode === "sampled_current" && !this.sampleSegmentStarted) {
        this.sampleSegmentStarted = true;
        await this.emit("agent_sample_segment_started", { ...this.context(), continuity_token: this.ledger.continuityToken,
          acquisition_id: report.acquisition_id });
      }
      await this.emit("agent_consumed", { ...this.context(), report, acknowledgement: ack, witness });
      this.checkActive(epoch, signal);
      const a = this.ledger.get(report.acquisition_id), o = a.observation;
      this.lastAckMetadata = immutable({ agent_artifact_id: this.manifest.artifact.id, agent_artifact_sha256: this.manifest.artifact.sha256,
        adapter_code_sha256: this.manifest.adapter.code_sha256, model_bindings: this.manifest.input.state_recovery.model_bindings,
        input_spec: this.manifest.input.input_spec, profile: "native-logical-v1", state_format_version: this.manifest.input.state_format_version,
        stream_generation: String(a.capture.stream_generation), continuity_token: ack.prefix.continuity_token,
        consumption_id: ack.consumption_id, state_version: ack.state_version, prefix: ack.prefix,
        last_acknowledged_basis: { acquisition_id: report.acquisition_id, capture_sha256: String(a.capture.sha256),
          snapshot_id: String(o.snapshot_id), owner_occurrence: o.owner_occurrence as Record<string, unknown>,
          revision: Number(o.revision), included: witness.included as AgentStateMetadata["last_acknowledged_basis"]["included"], publication_index: a.publication_index } });
      const previous = this.basisId;
      this.basisId = report.acquisition_id;
      if (this.freshDecision) this.freshDecision.advancedTo = report.acquisition_id;
      if (previous !== null && previous !== this.basisId) {
        await this.releaseAcquisition(previous);
      }
      return ack;
    }, query: async (query, signal) => {
      this.checkActive(epoch, signal);
      if (!this.manifest.requirements.required_methods.includes(query.method)) throw new AgentSessionError("query_method_not_declared");
      const args = query.arguments as JsonObject;
      if (query.method === "current") {
        if (Object.hasOwn(args, "client_session_id")) throw new AgentSessionError("query_cannot_supply_credentials");
        const body = { ...args, client_session_id: this.controller!.clientIdentity()!.clientSessionId } as JsonObject;
        validateNativeLogicalRequest("current", body);
        let ownedReply: NativeLogicalOwnedCurrentReply | undefined;
        let captured: NativeLogicalCapturedObservation | undefined;
        let stackLease: NativeLogicalRetentionLease | undefined, committed = false;
        let registered: string | undefined, primary: unknown;
        try {
          const request = { eagerScope: body.eager_scope as NativeLogicalScopeField[],
            expectedSnapshotId: body.expected_snapshot_id as string | null, signal };
          const current = this.manifest.execution_policy
            ? (ownedReply = await this.native!.currentOwned(request)).data
            : (await this.native!.current(request)).data;
          if (!current.capture || !current.context || !["captured", "partial"].includes(current.status))
            throw new AgentSessionError(`query_current_${current.status}`);
          if (current.capture.byte_count > this.manifest.limits.max_capture_bytes) {
            if (!ownedReply && current.retention) await this.native!.release(current.retention.retention_handle_id);
            throw new AgentSessionError("capture_byte_capacity");
          }
          const assembly = { capture: current.capture, context: current.context, retention: current.retention,
            signal, budget: this.assemblyBudget(), ...(ownedReply ? { readerLease: ownedReply.takeRetention() } : {}) };
          captured = this.manifest.input.history_mode !== "scoped_query"
            ? await this.native!.getFull({ ...assembly, maxActions: this.manifest.limits.max_catalog_actions })
            : await this.native!.getCapture({ ...assembly, eagerScope: body.eager_scope as NativeLogicalScopeField[] });
          if (ownedReply) stackLease = captured.transferRetention();
          this.checkActive(epoch, signal);
          const catalog = this.manifest.input.history_mode !== "scoped_query" ? (captured as NativeLogicalFullCapture).actions : null;
          const id = await this.register(captured, catalog, null); registered = id;
          if (stackLease) { this.readerLeases.set(id, stackLease); committed = true; }
          const acquisition = this.ledger.get(id);
          return { method: query.method, acquisition_id: id, value: { capture: acquisition.capture, observation: acquisition.observation,
            catalog: acquisition.catalog, catalog_materialized: acquisition.catalog_materialized } };
        } catch (error) {
          primary = error;
          if (registered && this.knownAcquisitions.has(registered)) {
            try { await this.releaseAcquisition(registered); }
            catch (cleanup) { primary = new AggregateError([error, cleanup], "native Current registration/map cleanup failed"); }
          }
          throw primary;
        } finally {
          const errors: unknown[] = [];
          if (!committed) try { await stackLease?.dispose(); } catch (error) { errors.push(error); }
          try { await captured?.dispose(); } catch (error) { errors.push(error); }
          try { await ownedReply?.dispose(); } catch (error) { errors.push(error); }
          if (errors.length) throw new AggregateError(primary === undefined ? errors : [primary, ...errors], "native Current owner cleanup failed");
        }
      }

      validateNativeLogicalRequest(query.method, args);
      if (query.method === "read") {
        const capture = [...this.knownAcquisitions].find(id => this.ledger.get(id).capture.capture_id === args.capture_id);
        if (!capture) throw new AgentSessionError("query_read_unknown_capture");
        return { method: query.method, acquisition_id: null, value: (await this.native!.read({ captureId: args.capture_id as string,
          cursor: args.cursor as string, maxBytes: args.max_bytes as number, signal })).data };
      }
      const id = [...this.knownAcquisitions].find(id => {
        const a = this.ledger.get(id), c = a.observation.catalog as Record<string, unknown>;
        return c.catalog_ref === args.catalog_ref && a.capture.stream_generation === args.stream_generation;
      });
      if (!id) throw new AgentSessionError("query_unknown_catalog");
      const value = query.method === "catalog"
        ? (await this.native!.list({ catalogRef: args.catalog_ref as string, streamGeneration: args.stream_generation as string,
          prefix: args.prefix as NativeLogicalPrefix | null, cursor: args.cursor as string | null,
          limit: args.limit as number, maxPageBytes: args.max_page_bytes as number, signal })).data
        : (await this.native!.resolve({ catalogRef: args.catalog_ref as string, streamGeneration: args.stream_generation as string,
          expression: args.expression as NativeLogicalExpression, signal })).data;
      this.checkActive(epoch, signal);
      if ("actions" in value) {
        const a = this.ledger.get(id), descriptor = a.observation.catalog as Record<string, unknown>;
        if (value.snapshot_id !== a.observation.snapshot_id || value.total_count !== descriptor.total_count
          || value.digest !== descriptor.digest) throw new AgentSessionError("query_catalog_descriptor_binding");
      }
      const actions = "actions" in value ? value.actions : value.action ? [value.action] : [];
      for (const action of actions) {
        if (!supportsProfileValue(this.manifest.support.action_verbs, action.verb)) throw new AgentSessionError("unsupported_action_verb");
        const cache = this.knownActions.get(id)!, previous = cache.get(action.action_id);
        if (previous && canonicalJson(previous) !== canonicalJson(action)) throw new AgentSessionError("query_catalog_member_coherence");
        if (!previous) {
          if (cache.size >= this.manifest.limits.max_catalog_actions) throw new AgentSessionError("query_catalog_member_capacity");
          const reservation = this.budget.reserve(agentJsonByteLength(action, this.manifest.limits.max_message_bytes));
          const held = this.actionReservations.get(id) ?? []; held.push(reservation); this.actionReservations.set(id, held);
          cache.set(action.action_id, action);
        }
      }
      return { method: query.method, acquisition_id: null, value };
    } };
  }
  private requireWatermark(output: AgentDirectiveOutput): void {
    if (output.continuity_token !== this.ledger.continuityToken || output.consumption_id !== this.ledger.consumptionId
      || output.state_version !== this.ledger.stateVersion) throw new AgentSessionError("directive_durable_watermark_mismatch");
  }
  private async act(output: AgentDirectiveOutput, epoch: number, signal: AbortSignal): Promise<AgentRuntimeTickResult> {
    if (this.options.globalTransport) return this.actGlobal(output, epoch, signal);
    if (output.directive.type !== "act") throw new AgentSessionError("Act_required");
    const submissionGeneration = this.owner.generation;
    const directive = output.directive, a = this.ledger.get(directive.basis_acquisition_id), descriptor = a.observation.catalog as Record<string, unknown>;
    if (directive.basis_acquisition_id !== this.basisId || descriptor.status !== "complete") throw new AgentSessionError("Act_requires_acknowledged_complete_relation");
    if (directive.scores && (a.catalog === null || directive.scores.catalog_digest !== descriptor.digest
      || directive.scores.values.length !== a.catalog.length)) throw new AgentSessionError("diagnostic_scores_catalog_binding");
    let action: NativeLogicalAction | undefined;
    if (directive.selection.kind === "handle") action = this.knownActions.get(a.acquisition_id)?.get(directive.selection.action_id);
    else {
      if (!this.manifest.requirements.required_methods.includes("resolve")) throw new AgentSessionError("resolve_method_not_declared");
      const resolved = (await this.native!.resolve({ catalogRef: String(descriptor.catalog_ref), streamGeneration: String(a.capture.stream_generation),
        expression: directive.selection.expression as unknown as NativeLogicalExpression, signal })).data;
      if (resolved.status === "unique" && resolved.action) action = resolved.action;
    }
    if (!action || !supportsProfileValue(this.manifest.support.action_verbs, action.verb)) throw new AgentSessionError("Act_requires_known_native_member");
    this.checkActive(epoch, signal);
    if (this.mode === "shadow") return { type: "shadow", status: this.status() };
    await this.controller!.credentials(); this.checkActive(epoch, signal);
    await this.emit("controller_acquired", { ...this.context(), controller: "held" });
    this.checkActive(epoch, signal);
    if (this.freshDecision) throw new AgentSessionError("fresh_decision_requires_changed_acknowledged_input");
    const requestId = `request-${randomUUID()}`;
    const intent = { ...this.context(), request_id: requestId, basis_acquisition_id: a.acquisition_id,
      snapshot_id: String(a.observation.snapshot_id), action_id: action.action_id,
      catalog_digest: String(descriptor.digest), run_id: this.options.evidence.runId,
      runtime_instance_id: String((a.capture.session as Record<string, unknown>).runtime_instance_id) };
    const optIn = this.manifest.execution_policy !== undefined;
    let intentRecorded = false, dispatchBinding: Readonly<NativeLogicalDispatchBinding> | undefined;
    if (!optIn) { await this.emit("native_submission_requested", intent); intentRecorded = true; }
    let lookup, started = false;
    try {
      this.checkActive(epoch, signal);
      lookup = await this.native!.submit({ requestId, expectedSnapshotId: String(a.observation.snapshot_id), actionId: action.action_id,
        preSubmitSignal: signal, ...(optIn ? { onDispatchBinding: async (binding: Readonly<NativeLogicalDispatchBinding>) => {
          this.checkActive(epoch, signal);
          if (binding.runtime_instance_id !== intent.runtime_instance_id) throw new AgentSessionError("dispatch_runtime_binding");
          dispatchBinding = immutable({ ...binding });
          await this.emit("native_submission_requested", { ...intent, dispatch_binding: dispatchBinding });
          intentRecorded = true;
          this.checkActive(epoch, signal);
        } } : {}), onSubmitStart: () => {
          this.checkActive(epoch, signal);
          if (optIn && (!dispatchBinding || !intentRecorded)) throw new AgentSessionError("sdk_dispatch_binding_unavailable");
          if (!this.owner.consumeSubmission()) { this.expireBudget(); throw new AgentSessionError("autonomy_budget_exhausted"); }
          started = true;
        } });
    } catch (error) {
      if (!started) {
        if (intentRecorded) await this.emit("native_submission_not_started", { ...this.context(), request_id: requestId,
          submission_epoch: epoch, reason: errorMessage(error) });
        if (!optIn || !this.terminationReason) await this.handoff(`native_submit_not_started:${errorMessage(error)}`);
        return { type: "not_admitted", reason: optIn ? this.terminationReason ?? "native_submit_not_started" : "native_submit_not_started", status: this.status() };
      }
      await this.taint(`native_submit_unknown:${errorMessage(error)}`);
      return { type: "unknown", error: errorMessage(error), status: this.status() };
    }
    if (lookup.status === "pending") {
      this.pending = immutable({ request_id: requestId, run_id: this.options.evidence.runId,
        runtime_instance_id: this.environment!.runtime_instance_id, session_id: this.sessionId, submission_epoch: epoch,
        basis_acquisition_id: a.acquisition_id, snapshot_id: String(a.observation.snapshot_id), action_id: action.action_id,
        status: "pending", reason: null, ...(dispatchBinding ? { dispatch_binding: dispatchBinding } : {}) });
      this.lastResult = { request_id: requestId, snapshot_id: String(a.observation.snapshot_id), action_id: action.action_id,
        status: "pending", delivery: null, execution: null, effect: null, cancel: null, reason: null };
      await this.emit("native_request_pending", { ...this.context(), original: this.pending });
      await this.handoff("native_request_pending");
      return { type: "unknown", error: "native_request_pending", status: this.status() };
    }
    const result = lookup.result.data, receivedAt = this.owner.monotonicNow(); this.recordResult(result);
    const originalTerminal = this.countOriginalTerminal(result, dispatchBinding);
    if (originalTerminal) this.updateKnownStaleWindow(result, epoch, submissionGeneration, receivedAt, dispatchBinding);
    await this.emit("native_result", { ...this.context(), result });
    if (result.delivery === "partially_delivered" || result.delivery === "unknown") {
      await this.taint(`native_delivery_${result.delivery}`); return { type: "unknown", error: `native_delivery_${result.delivery}`, status: this.status() };
    }
    if (optIn && isKnownStaleResult(result)) {
      // Counting precedes this gate: a threshold or late Stop result still counts.
      const ineligible = dispatchBinding ? this.freshDecisionIneligibility(epoch, dispatchBinding) : "original_terminal_dispatch_binding";
      if (ineligible === null) {
        const policy = this.manifest.execution_policy!;
        const limit = policy.schema === "sts2.policy-runtime/agent-execution-policy-1"
          && this.knownStaleRejections >= policy.max_known_stale_rejections ? "known_stale_rejection_limit"
          : policy.schema !== AGENT_KNOWN_STALE_WINDOW_POLICY_SCHEMA
            && this.consecutiveKnownStaleRejections >= policy.max_consecutive_known_stale_rejections ? "known_stale_streak_limit" : null;
        if (limit) await this.handoff(limit);
        else {
          if (!this.ledger.consumptionId) throw new AgentSessionError("known_stale_requires_original_consumption");
          const outcome: AgentOperationalOutcome = immutable({ schema: AGENT_KNOWN_NOT_STARTED_OUTCOME_SCHEMA,
            basis_acquisition_id: a.acquisition_id, action_id: action.action_id, consumption_id: this.ledger.consumptionId,
            state_version: this.ledger.stateVersion, result });
          this.freshDecision = { outcome, unit: this.ledger.nativeUnit(a.acquisition_id), advancedTo: null };
          await this.emit("native_stale_decision_deferred", { ...this.context(), request_id: requestId,
            basis_acquisition_id: a.acquisition_id, consumption_id: outcome.consumption_id, state_version: outcome.state_version,
            known_stale_rejections: this.knownStaleRejections, consecutive_known_stale_rejections: this.consecutiveKnownStaleRejections });
          this.checkActive(epoch, signal);
          return { type: "fresh_decision_required", original_request_id: requestId, status: this.status() };
        }
      } else if (!this.terminationReason) await this.handoff(ineligible);
      return { type: "not_delivered", reason: this.terminationReason ?? "native_delivery_not_started", status: this.status() };
    }
    if (result.delivery !== "delivered" || this.mode === "one_step" || epoch !== this.owner.epoch || this.stopping) {
      if (!optIn || !this.terminationReason) await this.handoff(`native_delivery_${result.delivery}`);
    }
    if (!this.owner.available() && this.mode !== "human") this.expireBudget();
    if (result.delivery === "delivered") return { type: "delivered", status: this.status() };
    return { type: "not_delivered", ...(optIn ? { reason: this.terminationReason ?? `native_delivery_${result.delivery}` } : {}), status: this.status() };
  }
  private countOriginalTerminal(result: NativeLogicalResult, binding?: Readonly<NativeLogicalDispatchBinding>): boolean {
    if (!this.manifest.execution_policy) return false;
    if (!binding || !result.attribution || Object.keys(binding).some(key =>
      result.attribution![key as keyof NativeLogicalDispatchBinding] !== binding[key as keyof NativeLogicalDispatchBinding]))
      throw new AgentSessionError("original_terminal_dispatch_binding");
    const fingerprint = createHash("sha256").update(canonicalJson(result)).digest("hex");
    const known = this.countedTerminalResults.get(result.request_id);
    if (known !== undefined) {
      if (known !== fingerprint) throw new AgentSessionError("original_terminal_result_conflict");
      return false;
    }
    this.countedTerminalResults.set(result.request_id, fingerprint);
    if (result.delivery === "delivered") this.consecutiveKnownStaleRejections = 0;
    else if (isKnownStaleResult(result)) { this.knownStaleRejections++; this.consecutiveKnownStaleRejections++; }
    return true;
  }
  private updateKnownStaleWindow(result: NativeLogicalResult, epoch: number, generation: number, receivedAt: number,
    binding?: Readonly<NativeLogicalDispatchBinding>): void {
    const policy = this.manifest.execution_policy;
    if (policy?.schema !== AGENT_KNOWN_STALE_WINDOW_POLICY_SCHEMA) return;
    // Accounting is already complete. A won expiry/old epoch cannot be refunded
    // by the original result, including delivery received after Stop/release.
    this.checkKnownStaleDeadline();
    if (this.terminationReason || this.owner.epoch !== epoch || this.owner.generation !== generation
        || this.owner.state.state !== "active" || this.mode !== "auto" || this.stopping || this.stopped
        || this.agentUncertain || this.tainted || this.pending || this.sampleSegmentEnded || this.renewalFailed) return;
    if (result.delivery === "delivered") {
      this.knownStaleWindow = null;
      this.scheduleNativeDeadline();
    } else if (isKnownStaleResult(result) && binding && this.freshDecisionIneligibility(epoch, binding) === null
        && this.knownStaleWindow === null) {
      // Receipt-time anchor precedes the first native_result durable append.
      this.knownStaleWindow = { deadline: receivedAt + policy.known_stale_recovery_window_ms,
        epoch, generation };
      this.scheduleNativeDeadline();
    }
  }
  private scheduleNativeDeadline(): void {
    const episode = this.knownStaleWindow;
    this.owner.scheduleDeadline(() => this.expireBudget(), episode ? {
      at: episode.deadline, onDeadline: () => this.checkKnownStaleDeadline()
    } : undefined);
  }
  private checkKnownStaleDeadline(): void {
    const episode = this.knownStaleWindow;
    if (!episode || this.terminationReason || this.stopping || this.stopped || this.tainted
        || this.owner.state.state !== "active" || episode.epoch !== this.owner.epoch
        || episode.generation !== this.owner.generation || this.owner.monotonicNow() < episode.deadline) return;
    // All original global wallet limits, including equality, have priority.
    if (!this.owner.available()) { this.expireBudget(); return; }
    this.terminationReason = "known_stale_recovery_exhausted";
    this.knownStaleWindow = null;
    this.budgetFenced = true;
    this.owner.end("mode_changed"); // Freeze the earlier winner before any later status/global clock.
    this.owner.advanceEpoch(); this.owner.cancelActive(this.terminationReason); this.mode = "human";
    const generation = this.owner.generation;
    // Retain native control revocation while an original submitted result is
    // still owed. Serialized evidence/reader cleanup follows its classification.
    const released = this.release().catch(() => undefined);
    this.budgetHandoff = this.owner.serialize(async () => {
      if (generation !== this.owner.generation) return;
      const reason = this.terminationReason!;
      const errors: unknown[] = [];
      const attempt = async (operation: () => unknown | Promise<unknown>) => {
        try { await operation(); } catch (error) { errors.push(error); }
      };
      await attempt(() => this.endSampleSegment(reason));
      await attempt(() => this.sampleOfferEvidence);
      await attempt(() => this.quiesceRenewal());
      for (const id of new Set([...this.knownAcquisitions, ...this.readerLeases.keys()]))
        await attempt(() => this.releaseAcquisition(id));
      await released;
      if (this.native?.subscription) await attempt(() => this.native!.detach());
      this.ledger.close(); this.knownAcquisitions.clear(); this.knownActions.clear();
      for (const held of this.actionReservations.values()) for (const reservation of held) reservation.release();
      this.actionReservations.clear();
      for (const payload of this.samplePayloads.values()) payload.reservation.release();
      this.samplePayloads.clear();
      this.port.close(); this.freshDecision = null;
      if (errors.length) { this.tainted = true; this.taintReason ??= "known_stale_cleanup_failed"; }
      await this.emit("handoff_to_human", { ...this.context(), reason,
        autonomy_budget: this.budgetStatus(), controller: this.controllerStatus() });
    }).catch(error => { this.tainted = true; this.taintReason ??= `known_stale_handoff_failed:${errorMessage(error)}`; });
  }
  private windowHandoffOwnsCleanup(): boolean {
    return this.manifest.execution_policy?.schema === AGENT_KNOWN_STALE_WINDOW_POLICY_SCHEMA
      && this.budgetFenced && this.terminationReason === "known_stale_recovery_exhausted";
  }

  private freshDecisionIneligibility(epoch: number, binding: Readonly<NativeLogicalDispatchBinding>): string | null {
    this.checkKnownStaleDeadline();
    if (this.terminationReason) return this.terminationReason;
    if (this.stopping || this.stopped) return "stopped";
    if (this.mode !== "auto" || this.owner.epoch !== epoch) return "human_recovery";
    if (this.agentUncertain || this.tainted || this.pending || this.sampleSegmentEnded || this.renewalFailed)
      return "runtime_fresh_decision_fenced";
    if (!this.owner.available()) { this.expireBudget(); return this.terminationReason ?? "autonomy_budget_exhausted"; }
    const controller = this.controller!.snapshot();
    if (this.controllerStatus() !== "held" || controller.client_session_closed === true || controller.client_revoked === true)
      return "runtime_controller_unresolved";
    if (controller.client_session_id !== binding.client_session_id || controller.controller_lease_id !== binding.controller_lease_id
      || controller.controller_generation !== binding.controller_generation) return "runtime_controller_binding_changed";
    if (typeof controller.controller_expires_at !== "string" || !(Date.parse(controller.controller_expires_at) > Date.now()))
      return "runtime_controller_expired";
    return null;
  }
  private checkFreshUnit(candidate: Readonly<AgentNativeUnitWitness>, requireAdvance: boolean): void {
    const previous = this.freshDecision?.unit; if (!previous) return;
    if (candidate.generation !== previous.generation) throw new AgentSessionError("native_generation_requires_explicit_reset");
    if (candidate.revision < previous.revision) throw new AgentSessionError("native_revision_regressed");
    if (candidate.occurrence === previous.occurrence) {
      if (candidate.revision !== previous.revision) throw new AgentSessionError("same_occurrence_revision_drift");
      if (candidate.coherence !== previous.coherence) throw new AgentSessionError("same_occurrence_coherence_drift");
      if (requireAdvance) throw new AgentSessionError("fresh_decision_requires_changed_native_unit");
    } else if (candidate.revision <= previous.revision) throw new AgentSessionError("native_revision_not_newer");
  }

  private recordResult(result: NativeLogicalResult): void {
    this.lastResult = { request_id: result.request_id, snapshot_id: result.snapshot_id,
      action_id: result.action?.action_id ?? null, status: "terminal", delivery: result.delivery,
      execution: result.execution, effect: result.effect, cancel: result.cancel, reason: result.reason };
  }
  private async gap(gap: Record<string, unknown>): Promise<void> {
    this.ledger.recordGap(gap); await this.emit("native_gap", { ...this.context(), gap }); await this.handoff("native_source_gap");
  }
  private async release(): Promise<void> {
    if (this.releaseOperation) return this.releaseOperation;
    const before = this.controllerStatus();
    this.releaseInFlight += 1;
    // SDK network deduplication alone cannot deduplicate Runtime evidence.
    // Publish this same-owner flight before the SDK call and retain it through
    // its one durable classification, including overlapping expiry/Stop/Human.
    const flight = Promise.resolve().then(async () => {
      try {
        await (this.options.globalTransport ? this.options.globalTransport.release() : this.controller!.releaseControl());
        if (before !== "released") await this.emit("controller_released", { ...this.context(), controller: "released" });
      } catch (error) {
        this.tainted = true; this.taintReason = "controller_release_unconfirmed";
        await this.emit("controller_release_unknown", { ...this.context(), controller: "unknown", reason: errorMessage(error) }).catch(() => undefined);
        throw error;
      } finally { this.releaseInFlight -= 1; }
    });
    this.releaseOperation = flight;
    try { await flight; } finally { if (this.releaseOperation === flight) this.releaseOperation = null; }
  }
  private async handoff(reason: string): Promise<void> {
    if (this.windowHandoffOwnsCleanup()) return;
    this.terminationReason ??= reason;
    this.knownStaleWindow = null;
    await this.endSampleSegment(this.manifest.execution_policy ? this.terminationReason : reason);
    this.mode = "human"; this.owner.end("mode_changed");
    await this.release().catch(() => undefined);
    await this.emit("handoff_to_human", { ...this.context(), reason: this.manifest.execution_policy ? this.terminationReason : reason, autonomy_budget: this.budgetStatus(), controller: this.controllerStatus() });
  }
  private async failClosed(reason: string): Promise<void> {
    this.errors = [...this.errors, reason].slice(-20); this.invalidations = [...this.invalidations, reason].slice(-20);
    await this.handoff(reason);
    await this.emit("fail_closed", { ...this.context(), reason, agent_state: this.agentUncertain ? "uncertain" : "known" }).catch(() => undefined);
  }
  private async taint(reason: string): Promise<void> {
    if (this.manifest.execution_policy?.schema === AGENT_KNOWN_STALE_WINDOW_POLICY_SCHEMA) this.terminationReason ??= reason;
    this.knownStaleWindow = null;
    await this.endSampleSegment(reason);
    this.tainted = true; this.taintReason = reason; this.mode = "human"; this.owner.end("mode_changed");
    await this.release().catch(() => undefined);
    await this.emit("runtime_tainted", { ...this.context(), reason, retry: false });
  }
  private expireBudget(): void {
    if (this.budgetFenced || this.stopped) return;
    this.terminationReason ??= "autonomy_budget_exhausted";
    this.knownStaleWindow = null;
    this.budgetFenced = true; this.owner.exhaust("deadline"); this.owner.advanceEpoch();
    this.owner.cancelActive("autonomy_budget_exhausted"); this.mode = "human";
    const released = this.release().catch(() => undefined);
    this.budgetHandoff = this.owner.serialize(async () => {
      await released; await this.endSampleSegment("autonomy_budget_exhausted"); await this.emit("autonomy_budget_exhausted", { ...this.context(),
        reason: this.owner.state.exhaustedReason ?? "deadline", autonomy_budget: this.budgetStatus(), controller: this.controllerStatus() });
    }).catch(error => { this.tainted = true; this.taintReason = `autonomy_budget_handoff_failed:${errorMessage(error)}`; });
  }
  private scheduleRenewal(): void {
    if (this.options.globalTransport || this.stopped || this.stopping || this.renewalFailed || this.renewalFlight || this.renewalTimer !== undefined || !this.native!.subscription) return;
    const remaining = Date.parse(this.native!.subscription.expires_at) - Date.now();
    if (!Number.isFinite(remaining) || remaining <= 0) { this.failRenewal({ reason: "subscription_expired" }); return; }
    const generation = this.renewalGeneration;
    // Renew halfway through the actual remaining TTL, capped for ordinary long
    // subscriptions. A short valid TTL must not create a fixed-margin 1ms loop.
    const ms = Math.max(1, Math.min(5000, Math.floor(remaining / 2)));
    this.renewalTimer = setTimeout(() => {
      this.renewalTimer = undefined;
      if (generation !== this.renewalGeneration || this.stopped || this.stopping || this.cursor === null) return;
      const cursor = this.cursor, controller = new AbortController();
      this.renewalAbort = controller;
      const timeout = setTimeout(() => controller.abort("native_renew_timeout"), Math.max(1,
        Math.min(5000, this.manifest.limits.agent_timeout_ms, Date.parse(this.native!.subscription!.expires_at) - Date.now())));
      timeout.unref?.();
      // Passive resource renewal has no Model/ledger/action work. It cannot sit
      // behind a long owned Consume/Next/Await in the operational queue.
      const flight: Promise<void> = (async () => {
        const result = (await this.native!.renew(cursor, controller.signal)).data;
        if (generation !== this.renewalGeneration || this.stopped || this.stopping) return;
        if (result.status !== "renewed") this.failRenewal(result.gap ?? { reason: `subscription_${result.status}` });
        else if (result.gap) {
          if (this.manifest.input.history_mode !== "sampled_current") this.failRenewal(result.gap);
          else if (this.renewalAdvisories.length >= 8) this.failRenewal({ reason: "advisory_gap_pending_capacity" });
          else {
            this.renewalAdvisories.push(result.gap);
            // Preserve exact replies but serialize their accounting after any
            // in-flight event batch. The network flight never waits on this queue.
            void this.owner.serialize(() => this.flushRenewalAdvisories()).catch(error => {
              this.tainted = true; this.taintReason = `advisory_gap_evidence_failed:${errorMessage(error)}`;
            });
          }
        }
      })().catch(error => {
        if (generation === this.renewalGeneration && !this.stopped && !this.stopping)
          this.failRenewal({ reason: `native_renew_failed:${errorMessage(error)}` });
      }).finally(() => {
        clearTimeout(timeout);
        if (this.renewalAbort === controller) this.renewalAbort = null;
        if (this.renewalFlight === flight) this.renewalFlight = null;
        if (generation === this.renewalGeneration) this.scheduleRenewal();
      });
      this.renewalFlight = flight;
    }, ms);
    this.renewalTimer.unref?.();
  }
  private failRenewal(gap: Record<string, unknown>): void {
    if (this.renewalFailed || this.stopped || this.stopping) return;
    this.renewalFailed = true;
    if (this.manifest.execution_policy) this.terminationReason ??= "native_subscription_unavailable";
    this.owner.advanceEpoch();
    this.owner.cancelActive("native_subscription_unavailable"); this.mode = "human"; this.owner.end("mode_changed");
    const released = this.release().catch(() => undefined);
    this.renewalHandoff = { gap, released };
    // The network flight never waits on this queue: Stop may itself await the
    // flight before detach. New mutation is already fenced above.
    void this.owner.serialize(() => this.flushRenewalFailure())
      .catch(error => { this.tainted = true; this.taintReason = `native_renew_handoff_failed:${errorMessage(error)}`; });
  }
  private async flushRenewalFailure(): Promise<void> {
    const pending = this.renewalHandoff;
    if (!pending) return;
    this.renewalHandoff = null;
    const reason = this.manifest.execution_policy ? this.terminationReason ?? "native_subscription_unavailable" : "native_subscription_unavailable";
    await pending.released; await this.endSampleSegment(reason); this.ledger.recordGap(pending.gap);
    await this.emit("native_gap", { ...this.context(), gap: pending.gap });
    await this.emit("handoff_to_human", { ...this.context(), reason: this.manifest.execution_policy ? reason : "native_source_gap",
      autonomy_budget: this.budgetStatus(), controller: this.controllerStatus() });
  }
  private async flushRenewalAdvisories(): Promise<void> {
    while (this.renewalAdvisories.length) {
      const gap = this.renewalAdvisories.shift()!;
      await this.emit("agent_sample_publication_gap", { ...this.context(), gap, received_cursor: this.cursor! });
    }
  }
  private async quiesceRenewal(): Promise<void> {
    this.renewalGeneration += 1;
    if (this.renewalTimer !== undefined) { clearTimeout(this.renewalTimer); this.renewalTimer = undefined; }
    this.renewalAbort?.abort("native_passive_stopped");
    await this.renewalFlight;
  }
  private stateAuthorization(restore = false) {
    if (this.stopped || this.stopping || this.tainted || this.pending || this.renewalFailed || (!restore && this.agentUncertain)
      || this.manifest.input.state_recovery.mode !== "opaque" || this.lastAckMetadata === null
      || this.ledger.prefix().omissions.gap !== null || this.ledger.prefix().omissions.received_unconsumed_count !== 0)
      throw new AgentSessionError("state_requires_known_durable_prefix");
    const expected = this.lastAckMetadata, epoch = this.owner.epoch;
    return { expected_metadata: expected, pending_request: "none" as const, retained_prefix: "complete" as const,
      assertCurrent: () => {
        this.owner.checkEpoch(epoch);
        if (this.pending || this.tainted || this.stopping || this.stopped || this.lastAckMetadata !== expected
          || this.ledger.stateVersion !== expected.state_version || this.ledger.consumptionId !== expected.consumption_id)
          throw new AgentSessionError("state_durable_prefix_mismatch");
      } };
  }
  private async finishStop(): Promise<void> {
    if (!this.manifest.execution_policy && !this.options.globalTransport) return this.finishStopLegacy();
    if (this.stopped) return;
    this.stopping = true; this.terminationReason ??= "stopped"; this.owner.end("stopped");
    const errors: unknown[] = [];
    const attempt = async (operation: () => unknown | Promise<unknown>) => {
      try { await operation(); }
      catch (error) {
        errors.push(error); this.tainted = true;
        this.taintReason ??= `native_stop_cleanup_failed:${errorMessage(error)}`;
      }
    };
    try {
      await attempt(() => this.endSampleSegment(this.terminationReason!));
      await attempt(async () => { await this.sampleOfferEvidence; });
      await attempt(() => this.cleanupSampleQueries(this.agentUncertain));
      await attempt(() => this.quiesceRenewal());
      await attempt(() => this.flushRenewalAdvisories());
      await attempt(() => this.flushRenewalFailure());
      await attempt(() => this.release());
      if (this.pending) { this.tainted = true; this.taintReason = "stopped_with_unresolved_request"; }
      this.mode = "human";
    } finally {
      // Registration remains available for every original-reader release attempt.
      // One failure must not skip another lease or leave the child alive.
      for (const id of new Set([...this.knownAcquisitions, ...this.readerLeases.keys()]))
        await attempt(() => this.releaseAcquisition(id));
      if (this.native?.subscription) await attempt(async () => { await this.native!.detach(); });
      await attempt(() => this.options.globalTransport ? this.options.globalTransport.close() : this.controller!.close());
      await attempt(() => this.port.close());
      await attempt(() => this.port.framesDrained());
      await attempt(() => this.ledger.close()); this.globalNoProgress?.close(); this.knownAcquisitions.clear(); this.knownActions.clear();
      for (const held of this.actionReservations.values()) for (const reservation of held)
        await attempt(() => reservation.release());
      this.actionReservations.clear();
      for (const payload of this.samplePayloads.values()) await attempt(() => payload.reservation.release());
      this.samplePayloads.clear(); this.freshDecision = null;
      await attempt(() => this.emit("stopped", { ...this.context(), autonomy_budget: this.budgetStatus(), controller: this.controllerStatus(),
        pending_request: this.pending, agent_state: this.agentUncertain ? "uncertain" : "known" }));
      await attempt(() => this.options.evidence.finalizeForStop({ status: this.tainted ? "tainted" : "stopped", tainted: this.tainted, mode: "human", now: this.now() }));
      this.stopped = true;
    }
    if (errors.length) throw new AggregateError(errors, "native Stop attempted all owned cleanup");
  }
  private async finishStopLegacy(): Promise<void> {
    if (this.stopped) return;
    this.stopping = true; this.owner.end("stopped");
    await this.endSampleSegment("stopped");
    try { await this.sampleOfferEvidence; await this.cleanupSampleQueries(this.agentUncertain); }
    catch (error) { this.tainted = true; this.taintReason = `sample_stop_cleanup_failed:${errorMessage(error)}`; }
    await this.quiesceRenewal();
    await this.flushRenewalAdvisories();
    await this.flushRenewalFailure();
    await this.release().catch(() => undefined);
    if (this.pending) { this.tainted = true; this.taintReason = "stopped_with_unresolved_request"; }
    this.mode = "human";
    try {
      await this.emit("stopped", { ...this.context(), autonomy_budget: this.budgetStatus(), controller: this.controllerStatus(),
        pending_request: this.pending, agent_state: this.agentUncertain ? "uncertain" : "known" });
    } finally {
      // Failed evidence must not keep the child, passive subscription or owned
      // buffers alive. Finalization retains the tainted failure classification.
      if (this.native?.subscription) await this.native!.detach().catch(error => { this.tainted = true; this.taintReason = `native_detach_failed:${errorMessage(error)}`; });
      this.port.close();
      this.ledger.close(); this.knownAcquisitions.clear(); this.knownActions.clear();
      for (const held of this.actionReservations.values()) for (const reservation of held) reservation.release();
      this.actionReservations.clear();
      for (const payload of this.samplePayloads.values()) payload.reservation.release();
      this.samplePayloads.clear();
      try { await this.options.evidence.finalizeForStop({ status: this.tainted ? "tainted" : "stopped", tainted: this.tainted, mode: "human", now: this.now() }); }
      finally { this.stopped = true; }
    }
  }
  private async initializeGlobal(signal: AbortSignal): Promise<void> {
    const transport = this.options.globalTransport!, expected = this.manifest.requirements.environment;
    if (!("kind" in expected) || expected.kind !== "global-public-basis" || !this.manifest.input.global
      || this.manifest.input.global.core_sha256 !== p1CoreIdentity().module_digest) throw new AgentSessionError("global_core_or_environment_contract");
    const identity = await transport.negotiate(signal); this.checkActive(this.owner.epoch, signal);
    assertGlobalIdentity(identity, expected); assertGlobalProgramSupport(identity, this.manifest); this.globalIdentity = identity;
    this.globalState = createSession(this.options.evidence.runId, transport.kind);
    this.environment = { kind: "global_public_basis", ...identity.session, host_kind: identity.kind,
      producer_sha256: globalDigest(identity.producer), game_sha256: globalDigest(identity.game), game_continuity_id: identity.game_continuity_id };
    await this.emitGlobal("global_initialized", { identity, core: p1CoreIdentity() });
    if (this.globalNoProgress) await this.emitGlobal("global_no_progress_config", { ...this.globalNoProgress.config });
    await this.emit("mode_changed", { ...this.context(), mode: this.mode, autonomy_budget: this.budgetStatus(), controller: this.controllerStatus() });
  }
  private async emitGlobal(kind: string, data: Record<string, unknown>): Promise<void> {
    // Same error fence and original fsynced writer as every Runtime event.
    await this.emit(kind as AgentRuntimeEventKind, { ...this.context(), ...data } as never);
  }
  private async captureGlobal(signal: AbortSignal, epoch: number, postDelivery = false): Promise<GlobalSource> {
    const captureRequestId = `capture-request-${randomUUID()}`;
    await this.emitGlobal("global_capture_requested", { request_id: captureRequestId });
    const responseReservations: AgentByteReservation[] = [];
    let transportFailure: unknown, transportRevocation: NativeRuntimeRevocation | undefined;
    try {
    const responses: { bytes: Buffer; stage: GlobalCaptureStagingRef }[] = [];
    let responseBytes = 0;
    let responseWrite = Promise.resolve();
    let full: FullPublicBasis;
    try {
      try {
      full = await this.options.globalTransport!.capture({ signal, maxResponseBytes: Math.min(this.manifest.limits.max_capture_bytes, 8 * 1024 * 1024),
        onResponseBytes: bytes => {
          responseBytes += bytes.byteLength;
          if (responseBytes > 2 * this.manifest.limits.max_capture_bytes + 64 * 1024) throw new AgentSessionError("global_capture_response_capacity");
          responseReservations.push(this.budget.reserve(bytes.byteLength));
          const original = Buffer.from(bytes);
          responseWrite = responseWrite.then(async () => {
            const stage = await this.options.evidence.stageGlobalCaptureResponse(captureRequestId, responses.length + 1, original);
            responses.push({ bytes: original, stage });
          });
          return responseWrite;
        } });
      } catch (error) {
        // The SDK wraps fetch aborts in its typed transport error. Recover only
        // this revoked read's owner token; durable writes, cleanup aggregates
        // and native identity validation stay outside this classification.
        if (signal.aborted && signal.reason instanceof NativeRuntimeRevocation
          && signal.reason.reason === "human_recovery" && epoch !== this.owner.epoch
          && error instanceof PlayerEnvironmentHttpError && error.statusCode === undefined) {
          transportFailure = error; transportRevocation = signal.reason;
        }
        throw error;
      } finally {
        // Cancellation may overtake the bounded response hook. Finish the
        // original durable staging before flushing an interrupted capture.
        try { await responseWrite; }
        catch (error) { transportRevocation = undefined; throw error; }
      }
    } catch (error) {
      // Partial/failed captures have no source owner: retain their original
      // responses unchanged and account the original capture error below.
      for (const { bytes, stage } of responses) {
        const artifact = await this.options.evidence.storeGlobalArtifact("response", bytes);
        await this.emitGlobal("global_transport_response", { artifact, operation: "capture", capture_request_id: captureRequestId });
        await this.options.evidence.releaseGlobalCaptureResponse(stage);
      }
      throw error;
    }
    const artifact = await this.options.evidence.storeGlobalArtifact("source", Buffer.from(serializePublicBasisRecord(full), "utf8"));
    for (const { bytes } of responses) {
      const response = await this.options.evidence.storeGlobalArtifact("response", normalizeGlobalCaptureResponse(bytes, artifact, full));
      await this.emitGlobal("global_transport_response", { artifact: response, operation: "capture", capture_request_id: captureRequestId });
    }
    const ordinal = ++this.globalSourceOrdinal;
    await this.emitGlobal("global_source", { ordinal, artifact, capture_request_id: captureRequestId });
    for (const { stage } of responses) await this.options.evidence.releaseGlobalCaptureResponse(stage);
    try {
      if (!this.globalIdentity || canonicalJson(full.capture.session) !== canonicalJson(this.globalIdentity.session)
        || (this.globalIdentity.game_continuity_id !== null && full.capture.game_continuity_id !== this.globalIdentity.game_continuity_id)
        || (this.globalEpisode !== null && full.capture.game_continuity_id !== this.globalEpisode)
        || full.capture.total_bytes > this.manifest.limits.max_capture_bytes) throw new AgentSessionError("global_capture_identity_or_capacity");
      if (this.globalEpisode === null && full.capture.game_continuity_id !== null) {
        this.globalEpisode = full.capture.game_continuity_id;
        await this.emitGlobal("global_episode_bound", { game_continuity_id: this.globalEpisode, source_ordinal: ordinal });
      }
      this.checkActive(epoch, signal);
      if (postDelivery) await this.emitGlobal("global_source_retained", { source_ordinal: ordinal, disposition: "pending_next_receive" });
      return { full, artifact, ordinal };
    } catch (error) { await this.emitGlobal("global_source_rejected", { source_ordinal: ordinal, reason: errorMessage(error) }); throw error; }
    } catch (error) {
      await this.emitGlobal("global_capture_error", { request_id: captureRequestId, reason: errorMessage(error) });
      if (transportRevocation && error === transportFailure) throw transportRevocation;
      throw error;
    }
    finally { for (const reservation of responseReservations) reservation.release(); }
  }
  private async receiveGlobal(signal: AbortSignal, epoch: number, handlers: AgentPortHandlers, offer: { pending: boolean }): Promise<void> {
    this.refreshing = true;
    try { for (;;) {
      this.checkActive(epoch, signal);
      const source = this.globalPendingSource ?? await this.captureGlobal(signal, epoch); this.globalPendingSource = null;
      const before = this.globalState!;
      const reduction = reduce(before, source.full.basis);
      await this.emitGlobal("global_observation", { source_ordinal: source.ordinal, occurrence: reduction.observation.envelope.occurrence,
        complete: reduction.complete, missing: [...reduction.missing], status: reduction.observation.status, excluded: reduction.excluded,
        observation_sha256: globalDigest(reduction.observation) });
      const snapshot = source.full.basis.snapshot;
      if (snapshot.status === "settling" || snapshot.status === "observed" && reduction.observation.status !== "terminal") {
        await globalDelay(50, signal); continue;
      }
      if (source.full.basis.capture_status !== "complete" || !reduction.complete || reduction.observation.status === "unsupported"
        || this.globalEpisode === null && reduction.observation.status !== "terminal")
        throw new AgentSessionError("global_required_information_unavailable");
      if (!supportsProfileValue(this.manifest.support.interaction_kinds, reduction.observation.scene.native_kind))
        throw new AgentSessionError("global_interaction_unsupported");
      this.globalState = reduction.state;
      if (this.globalPrepared?.reduction.observation.envelope.occurrence === reduction.observation.envelope.occurrence) {
        if (canonicalJson(modelView(reduction.observation)) !== canonicalJson(this.globalPrepared.acquisition.observation)) throw new AgentSessionError("global_same_occurrence_drift");
        this.globalPrepared = { ...this.globalPrepared, source, reduction }; return;
      }
      const id = `acquisition-${randomUUID()}`, occurrence = reduction.observation.envelope.occurrence;
      const acquisition: AgentAcquisition = { acquisition_id: id, capture: { source_ordinal: source.ordinal, source_sha256: source.full.capture.sha256 },
        observation: modelView(reduction.observation) as unknown as Record<string, unknown>, catalog: reduction.observation.catalog as unknown as Record<string, unknown>[],
        catalog_materialized: true, publication_index: String(occurrence) };
      this.ledger.registerGlobal(acquisition, source.full.basis, before); this.knownAcquisitions.add(id);
      const prior = this.globalPrepared?.acquisition.acquisition_id;
      const unit = `unit-${randomUUID()}`;
      this.globalPrepared = { source, reduction, acquisition, unit };
      this.noteCursor(`cursor-${randomUUID()}`, 1);
      const input: AgentConsumeInput = { acquisition_id: id, input_spec: this.manifest.input.input_spec,
        continuity_token: this.ledger.continuityToken, previous_consumption_id: this.ledger.consumptionId,
        observation: acquisition.observation, catalog: acquisition.catalog, consumption_unit: { id: unit } };
      const publicInput = await this.options.evidence.storeGlobalArtifact("public_input", Buffer.from(canonicalJson(input)));
      await this.emitGlobal("global_input", { acquisition_id: id, source_ordinal: source.ordinal, occurrence, artifact: publicInput, unit });
      if (!this.owner.consumeCall()) { this.expireBudget(); throw new AgentSessionError("autonomy_budget_exhausted"); }
      await this.port.consume(this.context(), input, handlers, signal, () => { offer.pending = true; });
      offer.pending = false; this.checkActive(epoch, signal);
      if (prior && prior !== id) await this.releaseAcquisition(prior);
      this.lastObservation = { acquisition_id: id, capture_sha256: source.full.capture.sha256, snapshot_id: String(snapshot.snapshot_id),
        revision: occurrence, status: reduction.observation.status, publication_index: String(occurrence), included: [], missing: [], catalog_count: acquisition.catalog!.length };
      return;
    } } finally { this.refreshing = false; }
  }
  private globalHandlers(epoch: number): AgentPortHandlers {
    return { query: async () => { throw new AgentSessionError("global_program_query_unsupported"); },
      consumed: async (report, signal, requestId) => {
        this.checkActive(epoch, signal);
        if (report.acquisition_id !== this.globalPrepared?.acquisition.acquisition_id) throw new AgentSessionError("global_consumption_binding");
        const acknowledgement = this.ledger.accept(report);
        await this.emitGlobal("global_consumed", { request_id: requestId, report, acknowledgement });
        this.checkActive(epoch, signal); this.basisId = report.acquisition_id; return acknowledgement;
      } };
  }
  private async actGlobal(output: AgentDirectiveOutput, epoch: number, signal: AbortSignal): Promise<AgentRuntimeTickResult> {
    if (output.directive.type !== "act" || output.directive.selection.kind !== "handle") throw new AgentSessionError("global_handle_required");
    const choiceId = output.directive.selection.action_id;
    const prepared = this.globalPrepared!, directive = output.directive;
    if (directive.basis_acquisition_id !== this.basisId || directive.basis_acquisition_id !== prepared.acquisition.acquisition_id)
      throw new AgentSessionError("global_acknowledged_basis_required");
    const choice = prepared.reduction.observation.catalog.find(c => c.choice_id === choiceId), lowering = prepared.reduction.lowering[choiceId];
    if (!choice || !lowering || !supportsProfileValue(this.manifest.support.action_verbs, choice.kind)) throw new AgentSessionError("global_choice_not_in_catalog");
    if (directive.scores && (directive.scores.catalog_digest !== globalDigest(prepared.acquisition.catalog)
      || directive.scores.values.length !== prepared.acquisition.catalog!.length)) throw new AgentSessionError("global_scores_binding");
    this.checkActive(epoch, signal);
    if (this.mode === "shadow") return { type: "shadow", status: this.status() };
    if (this.globalNoProgress) {
      const check = this.globalNoProgress.check(prepared.reduction.observation, choice);
      if (check.exhausted) {
        this.terminationReason = "no_progress"; this.errors.push("no_progress");
        await this.emitGlobal("global_no_progress", { acquisition_id: this.basisId, choice_id: choiceId, ...check });
        await this.finishStop();
        return { type: "not_admitted", reason: "no_progress", status: this.status() };
      }
    }
    await this.emitGlobal("global_choice", { acquisition_id: this.basisId, occurrence: prepared.reduction.observation.envelope.occurrence, choice_id: choiceId, origin: "agent_choice" });
    const steps: P1StepRecord[] = []; let status: P1Result["status"] = "completed";
    let source = prepared.source, next = lowering.first, followIndex = 0;
    while (next) {
      if (steps.length >= P1G_LIMITS.macro_steps) { status = "interrupted_known"; break; }
      try {
        this.checkActive(epoch, signal); await this.options.globalTransport!.acquire(signal); this.checkActive(epoch, signal);
        await this.emit("controller_acquired", { ...this.context(), controller: "held" }); this.checkActive(epoch, signal);
      } catch (error) {
        await this.emitGlobal("global_dispatch_error", { request_id: null, sent: false, reason: `controller_acquire:${errorMessage(error)}` });
        status = steps.length ? "interrupted_known" : "not_started"; break;
      }
      const requestId = `request-${randomUUID()}`, step = next;
      const bound = (source.full.basis.snapshot.bound_actions as { actions: Record<string, unknown>[] }).actions.find(a => a.bound_action_id === step.bound_action_id);
      if (!bound) { status = steps.length ? "interrupted_known" : "not_started"; break; }
      let sent = false, classified = false;
      try {
        const receipt = await this.options.globalTransport!.step({ requestId, expectedSnapshotId: String(source.full.basis.snapshot.snapshot_id), boundActionId: step.bound_action_id }, {
          signal, maxResponseBytes: Math.min(this.manifest.limits.max_message_bytes, 8 * 1024 * 1024),
          beforeSend: async () => {
            this.checkActive(epoch, signal);
            if (!this.owner.available() || this.controllerStatus() !== "held" || this.mode === "human" || this.tainted) throw new AgentSessionError("global_dispatch_not_authorized");
            await this.emitGlobal("global_dispatch", { request_id: requestId, acquisition_id: this.basisId, source_ordinal: source.ordinal,
              choice_id: choiceId, step_index: steps.length, bound_action: bound, expected_snapshot_id: source.full.basis.snapshot.snapshot_id });
            this.checkActive(epoch, signal);
            if (!this.owner.consumeSubmission()) { this.expireBudget(); throw new AgentSessionError("autonomy_budget_exhausted"); }
            sent = true;
          },
          onResponseBytes: async bytes => { const artifact = await this.options.evidence.storeGlobalArtifact("response", bytes);
            await this.emitGlobal("global_transport_response", { artifact, operation: "step", request_id: requestId }); }
        });
        const decoded = decodePublicSourceReceipt(receipt).data;
        const expected = { bound_action_id: bound.bound_action_id, verb: bound.verb, subject_referent_id: bound.subject_referent_id ?? null, arguments: bound.arguments };
        const actual = { ...decoded.action, subject_referent_id: decoded.action.subject_referent_id ?? null };
        // A request-level not_delivered rejection (e.g. a stale snapshot) is issued before the
        // Host binds the action, so it echoes only the bound action id; nothing was applied.
        const bindingMatches = decoded.delivery === "not_delivered"
          ? actual.bound_action_id === expected.bound_action_id
          : canonicalJson(expected) === canonicalJson(actual);
        if (decoded.request_id !== requestId || !bindingMatches) throw new AgentSessionError("global_original_receipt_binding");
        await this.emitGlobal("global_receipt", { request_id: requestId, source_ordinal: source.ordinal, receipt: decoded });
        steps.push({ index: steps.length, origin: steps.length ? "fold" : "agent_choice", native_verb: step.verb, label: step.label,
          delivery: decoded.delivery, reason_code: decoded.reason_code ?? null });
        classified = true;
        this.lastResult = { request_id: requestId, snapshot_id: String(source.full.basis.snapshot.snapshot_id), action_id: choiceId,
          status: "terminal", delivery: decoded.delivery, execution: null, effect: null, cancel: null, reason: decoded.reason_code ?? null };
        const successorDrift = decoded.successor && canonicalJson(decoded.successor.session) !== canonicalJson(this.globalIdentity!.session);
        if (successorDrift && decoded.delivery !== "delivered") {
          await this.emitGlobal("global_dispatch_error", { request_id: requestId, sent, reason: "global_receipt_successor_identity_drift" });
          // Identity failure fences further submissions without rewriting the known Receipt.
          await this.taint("global_receipt_successor_identity_drift");
        }
        if (decoded.delivery === "unknown") { status = "unknown"; break; }
        if (decoded.delivery === "not_delivered") { status = steps.length === 1 ? "not_started" : "interrupted_known"; break; }
        if (successorDrift) throw new AgentSessionError("global_receipt_successor_identity_drift");
      } catch (error) {
        await this.emitGlobal("global_dispatch_error", { request_id: requestId, sent, reason: errorMessage(error) });
        if (classified) { status = "interrupted_known"; await this.taint("global_receipt_successor_identity_drift"); }
        else if (sent) { steps.push({ index: steps.length, origin: steps.length ? "fold" : "agent_choice", native_verb: step.verb, label: step.label, delivery: "unknown", reason_code: "response_unclassified" }); status = "unknown"; }
        else status = steps.length ? "interrupted_known" : "not_started";
        break;
      }
      // Every known delivery gets an actual sealed successor capture, including
      // single-step choices. The Receipt successor is not a substitute for it.
      const rule = lowering.follow[followIndex++];
      try {
        this.checkActive(epoch, signal);
        source = await this.captureGlobal(signal, epoch, true); this.globalPendingSource = source;
        // Native readiness and capture completeness are different facts. The
        // Reference Host already publishes settling/no_action during e.g. combat
        // setup, even when an advertised map Read is temporarily unavailable.
        // The delay only paces this pull-only transport; it never proves readiness.
        // The existing operation budget and Stop abort the wait and release control.
        while (source.full.basis.snapshot.status === "settling") {
          await globalDelay(50, signal); this.checkActive(epoch, signal);
          source = await this.captureGlobal(signal, epoch, true); this.globalPendingSource = source;
        }
        if (source.full.basis.capture_status !== "complete") throw new AgentSessionError("global_post_delivery_capture_incomplete");
        if (!rule) break;
        next = source.full.basis.capture_status === "complete" ? resolveFollowBasis(rule, lowering, source.full.basis) : null;
        if (!next && !rule.optional) status = "interrupted_known";
      } catch (error) {
        status = "interrupted_known";
        const humanRevoked = signal.aborted && signal.reason instanceof NativeRuntimeRevocation
          && signal.reason.reason === "human_recovery" && error === signal.reason && epoch !== this.owner.epoch;
        if (!this.stopping && !this.budgetFenced && !humanRevoked)
          await this.taint(`global_post_delivery_capture:${errorMessage(error)}`);
        break;
      }
    }
    if (status === "completed") {
      try { this.checkActive(epoch, signal); this.globalState = applyCommitment(this.globalState!, lowering, true); }
      catch { status = steps.length ? "interrupted_known" : "not_started"; }
    }
    const result = buildP1Result({ occurrence: prepared.reduction.observation.envelope.occurrence,
      choice_id: choiceId, status, steps, commitment_delta: status === "completed" && lowering.commitment ? commitmentView(this.globalState!) : null });
    await this.emitGlobal("global_result", { acquisition_id: this.basisId, result });
    if (status === "unknown") { await this.taint("global_delivery_unknown"); return { type: "unknown", error: "global_delivery_unknown", status: this.status() }; }
    if (this.mode === "one_step") await this.handoff("global_one_step_completed");
    if (!this.owner.available() && this.mode !== "human") this.expireBudget();
    return { type: status === "completed" ? "delivered" : "not_delivered", status: this.status() };
  }

  private async emit<K extends AgentRuntimeEventKind>(kind: K, payload: AgentRuntimeEventPayloads[K]): Promise<void> {
    try { await this.options.evidence.append(kind, payload as unknown as Record<string, unknown>, this.now()); }
    catch (error) {
      this.tainted = true; this.taintReason = `agent_evidence_write_failed:${errorMessage(error)}`;
      if (this.manifest.execution_policy?.schema === AGENT_KNOWN_STALE_WINDOW_POLICY_SCHEMA) this.terminationReason ??= this.taintReason;
      this.knownStaleWindow = null;
      this.mode = "human"; this.owner.end("mode_changed"); this.owner.cancelActive(error);
      // Evidence failure must not leave a held lease with its deadline cleared.
      // Direct SDK cleanup avoids recursive attempts to write failed evidence.
      await (this.options.globalTransport ? this.options.globalTransport.release() : this.controller!.releaseControl()).catch(() => undefined);
      throw error;
    }
  }
}

// Private operation-scope identity: intentional owner revocation is not a
// failed native capture. Keep the existing recovery error on the raw journal.
class NativeRuntimeRevocation extends RuntimeControlPreconditionError {
  constructor(readonly reason: "human_recovery" | "stopped") {
    super("runtime_recovery_epoch_mismatch", 409);
  }
}

export async function createNativeAgentRuntime(options: NativeAgentRuntimeOptions): Promise<NativeAgentRuntimeOwner> {
  const runtime = new NativeAgentRuntime(options); await runtime.initialize(); return runtime;
}
function immutable<T>(value: T): T {
  if (value !== null && typeof value === "object") { Object.freeze(value); for (const child of Object.values(value)) immutable(child); }
  return value;
}
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function nativeEnvironment(c: NativeLogicalCapabilities, hostKind: ConnectorPolicyRequirements["environment"]["host_kind"]): NonNullable<RuntimeStatus["environment"]> {
  return { runtime_instance_id: c.session.runtime_instance_id, environment_fingerprint: c.session.environment_fingerprint,
    host_kind: hostKind, connector_protocol_version: c.protocol_version, connector_version: c.host.version,
    connector_source_revision: c.host.implementation.source_revision, connector_artifact_sha256: c.host.implementation.artifact_sha256,
    connector_module_version_id: c.host.implementation.module_version_id, game_version: c.game.version, game_commit: c.game.commit,
    modset_status: c.game.modset.status, modset_fingerprint: c.game.modset.fingerprint, loaded_mod_ids: [...c.game.modset.loaded_mod_ids] };
}

async function globalDelay(ms: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted(); await new Promise<void>((resolve, reject) => { const done = () => { clearTimeout(timer); signal.removeEventListener("abort", abort); resolve(); };
    const abort = () => { clearTimeout(timer); signal.removeEventListener("abort", abort); reject(signal.reason); };
    const timer = setTimeout(done, ms); signal.addEventListener("abort", abort, { once: true }); });
}
