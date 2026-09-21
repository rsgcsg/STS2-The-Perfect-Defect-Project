import { randomUUID } from "node:crypto";
import type { PlayerEnvironmentBoundAction, PlayerEnvironmentReceipt, PlayerEnvironmentSnapshot } from "@rsgcsg/sts2-connector-client";
import { admitWholeDecision } from "./admission.js";
import { AgentRunEvidence } from "./evidence.js";
import { candidateOrderDigest } from "./digest.js";
import { POLICY_RUNTIME_VERSION, assertAdapterDecision, validateAdapterDecision, validatePolicyDecision, validatePolicyManifest, type AdapterDecision, type ApplicationResult, type DecisionBundle, type Policy, type PolicyConnector, type PolicyDecision, type PolicyDecisionInput, type PolicyManifest, type RuntimeCommand, type RuntimeMode, type RuntimeStatus, type TickResult } from "./contracts.js";
import { StaleWholeBundleError } from "./connector.js";
import { RUNTIME_ENVIRONMENT_SCHEMA, type RuntimeControlPreconditions, type RuntimeEnvironmentBinding } from "./contracts.js";

/** A known-unapplied control request, not an uncertain gameplay delivery. */
export class RuntimeControlPreconditionError extends Error {
  constructor(readonly code: string, readonly httpStatus: number) { super(code); }
}

export interface RuntimeOptions {
  manifest: PolicyManifest;
  connector: PolicyConnector;
  policy: Policy;
  mode?: RuntimeMode;
  runId?: string;
  evidence?: AgentRunEvidence;
  staleRefresh?: { maxAttempts: number; baseBackoffMs: number };
  successorPoll?: { maxAttempts: number; baseBackoffMs: number };
  policyTimeoutMs?: number;
  sleep?: (milliseconds: number) => Promise<void>;
  now?: () => string;
  runtimeIdentity?: { version: string; code_sha256: string | null };
}

export interface Admission { admitted: boolean; reason: string; candidateDigest: string; candidateCount: number }

export function admitWholeDecisionBundle(bundle: DecisionBundle, manifest?: PolicyManifest): Admission {
  const catalog = bundle.observation.bound_actions;
  const candidateDigest = candidateOrderDigest(catalog.actions);
  if (bundle.observation.status !== "interactive") return { admitted: false, reason: `snapshot_${bundle.observation.status}`, candidateDigest, candidateCount: catalog.actions.length };
  if (bundle.observation.completeness.status !== "complete") return { admitted: false, reason: "snapshot_incomplete", candidateDigest, candidateCount: catalog.actions.length };
  if (catalog.status !== "complete" || catalog.materialized_count !== catalog.total_count || catalog.materialized_count !== catalog.actions.length || catalog.actions.length === 0) return { admitted: false, reason: "complete_catalog_required", candidateDigest, candidateCount: catalog.actions.length };
  if (new Set(catalog.actions.map((action) => action.bound_action_id)).size !== catalog.actions.length) return { admitted: false, reason: "duplicate_bound_action_id", candidateDigest, candidateCount: catalog.actions.length };
  if (manifest && !manifest.support.interaction_kinds.includes(bundle.observation.interaction.kind)) return { admitted: false, reason: "unsupported_interaction_kind", candidateDigest, candidateCount: catalog.actions.length };
  if (manifest && catalog.actions.some((action) => !manifest.support.action_verbs.includes(action.verb))) return { admitted: false, reason: "unsupported_action_verb", candidateDigest, candidateCount: catalog.actions.length };
  return { admitted: true, reason: "whole_decision_admitted", candidateDigest, candidateCount: catalog.actions.length };
}

export async function refreshWholeDecisionBundle(connector: PolicyConnector, requiredReadKinds: readonly string[], options: { maxAttempts: number; baseBackoffMs: number; sleep?: (milliseconds: number) => Promise<void>; onStale?: (attempt: number, delayMs: number) => Promise<void> | void }): Promise<DecisionBundle | null> {
  if (!Number.isSafeInteger(options.maxAttempts) || options.maxAttempts < 1) throw new Error("maxAttempts must be a positive integer");
  if (options.baseBackoffMs < 0) throw new Error("baseBackoffMs must be non-negative");
  const sleep = options.sleep ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  for (let attempt = 1; attempt <= options.maxAttempts; attempt += 1) {
    try { return await connector.observeBundle(requiredReadKinds); } catch (error) {
      if (!isStale(error)) throw error;
      const delayMs = attempt < options.maxAttempts ? options.baseBackoffMs * (2 ** (attempt - 1)) : 0;
      await options.onStale?.(attempt, delayMs);
      if (delayMs > 0) await sleep(delayMs);
    }
  }
  return null;
}

export class PolicyRuntime {
  private mode: RuntimeMode;
  private held = false;
  private tainted = false;
  private stopped = false;
  private evidenceFinalized = false;
  private taintReason: string | null = null;
  private refreshing = false;
  private lastSnapshotId: string | null = null;
  private lastPolicySnapshotId: string | null = null;
  private lastEvidenceEnvironmentFingerprint: string | null = null;
  private lastSnapshot: RuntimeStatus["last_snapshot"] = null;
  private lastDecision: RuntimeStatus["last_decision"] = null;
  private lastReceipt: RuntimeStatus["last_receipt"] = null;
  private lastReads: RuntimeStatus["reads"] = [];
  private invalidations: string[] = [];
  private errors: string[] = [];
  private environment: RuntimeStatus["environment"] = null;
  private submittedRequestIds = new Set<string>();
  private tickActive = false;
  private consecutiveStaleSubmissions = 0;
  private operation: Promise<unknown> = Promise.resolve();
  private requestedMode: RuntimeMode | null = null;
  private stopRequested = false;
  private recoveryEpoch = 0;
  private recoveryEpochExhausted = false;
  private activePolicy: { controller: AbortController } | null = null;
  private readonly runId: string;
  private readonly now: () => string;
  private readonly staleRefresh: { maxAttempts: number; baseBackoffMs: number };
  private readonly successorPoll: { maxAttempts: number; baseBackoffMs: number };
  private readonly policyTimeoutMs: number;
  private readonly sleep: (milliseconds: number) => Promise<void>;

  constructor(private readonly options: RuntimeOptions) {
    validatePolicyManifest(options.manifest);
    if (options.evidence && !/^[a-f0-9]{64}$/u.test(options.runtimeIdentity?.code_sha256 ?? "")) {
      throw new Error("Agent evidence requires an exact Policy Runtime code SHA-256");
    }
    this.mode = options.mode ?? "human";
    this.runId = options.runId ?? `run-${randomUUID()}`;
    this.now = options.now ?? (() => new Date().toISOString());
    this.staleRefresh = options.staleRefresh ?? { maxAttempts: 3, baseBackoffMs: 25 };
    this.successorPoll = options.successorPoll ?? { maxAttempts: 41, baseBackoffMs: 250 };
    if (!Number.isSafeInteger(this.successorPoll.maxAttempts) || this.successorPoll.maxAttempts < 1
        || !Number.isFinite(this.successorPoll.baseBackoffMs) || this.successorPoll.baseBackoffMs < 0)
      throw new Error("successorPoll requires positive attempts and finite non-negative interval");
    this.policyTimeoutMs = options.policyTimeoutMs ?? 30_000;
    if (!Number.isSafeInteger(this.policyTimeoutMs) || this.policyTimeoutMs < 1) {
      throw new Error("policyTimeoutMs must be a positive integer");
    }
    this.sleep = options.sleep ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  }

  status(): RuntimeStatus {
    return { schema: "sts2.policy-runtime/status-1", runtime: this.options.runtimeIdentity ?? { version: POLICY_RUNTIME_VERSION, code_sha256: null }, policy: { manifest_id: this.options.manifest.manifest_id, policy_id: this.options.manifest.policy.id, policy_version: this.options.manifest.policy.version, provider: this.options.manifest.policy.provider, architecture: this.options.manifest.policy.architecture, artifact_sha256: this.options.manifest.artifact.sha256 }, run_id: this.runId, lifecycle: this.stopped ? "stopped" : "running", mode: this.mode, controller: this.held ? "held" : "released", tainted: this.tainted, taint_reason: this.taintReason, refreshing: this.refreshing, last_snapshot_id: this.lastSnapshotId, last_snapshot: this.lastSnapshot, last_decision: this.lastDecision, last_receipt: this.lastReceipt, reads: [...this.lastReads], invalidations: [...this.invalidations], errors: [...this.errors] , environment: this.environment };
  }

  async readEnvironment(): Promise<RuntimeEnvironmentBinding> {
    const recoveryEpoch = this.recoveryEpoch;
    this.checkRecoveryEpoch(recoveryEpoch);
    const runtimeInstanceId = await this.freshGameInstance();
    // A recovery during this read must not hand an old intent a fresh epoch.
    this.checkRecoveryEpoch(recoveryEpoch);
    return { schema: RUNTIME_ENVIRONMENT_SCHEMA, run_id: this.runId,
      runtime_instance_id: runtimeInstanceId, recovery_epoch: recoveryEpoch };
  }

  private advanceRecoveryEpoch(): void {
    if (this.recoveryEpoch === Number.MAX_SAFE_INTEGER) this.recoveryEpochExhausted = true;
    else this.recoveryEpoch += 1;
  }

  private checkRecoveryEpoch(expected: number | undefined): void {
    if (expected === undefined) return;
    if (!Number.isSafeInteger(expected) || expected < 0)
      throw new RuntimeControlPreconditionError("runtime_recovery_precondition_required", 428);
    if (this.recoveryEpochExhausted || expected !== this.recoveryEpoch)
      throw new RuntimeControlPreconditionError("runtime_recovery_epoch_mismatch", 409);
  }

  private async freshGameInstance(): Promise<string> {
    let identity: string;
    try {
      const capabilities = await this.options.connector.capabilities({ fresh: true });
      identity = capabilities.host.runtime_instance_id;
      if (typeof identity !== "string" || identity.trim() === "") throw new Error("missing identity");
    } catch { throw new RuntimeControlPreconditionError("runtime_environment_unavailable", 503); }
    if (this.environment !== null && this.environment.runtime_instance_id !== identity)
      throw new RuntimeControlPreconditionError("runtime_game_mismatch", 409);
    return identity;
  }

  private async checkControlPreconditions(expected?: RuntimeControlPreconditions): Promise<void> {
    if (!expected) return;
    this.checkRecoveryEpoch(expected.recoveryEpoch);
    if (expected.gameInstanceId !== undefined) {
      if (typeof expected.gameInstanceId !== "string" || expected.gameInstanceId.trim() === "")
        throw new RuntimeControlPreconditionError("runtime_game_precondition_required", 428);
      if (await this.freshGameInstance() !== expected.gameInstanceId)
        throw new RuntimeControlPreconditionError("runtime_game_mismatch", 409);
    }
    this.checkRecoveryEpoch(expected.recoveryEpoch);
  }

  async setMode(mode: RuntimeMode, expected?: RuntimeControlPreconditions): Promise<RuntimeStatus> {
    // Human/Stop invalidate preparation in every client as soon as they enter
    // this owner, even when an existing operation still holds the mutation queue.
    if (mode === "human") {
      this.advanceRecoveryEpoch();
      this.cancelActivePolicy();
    }
    if (mode === "human" || expected === undefined) this.requestedMode = mode;
    return this.serialize(async () => {
      try {
        if (mode !== "human") {
          await this.checkControlPreconditions(expected);
          this.requestedMode = mode;
        }
        if (this.stopped) throw new Error("runtime is stopped");
        if (this.tainted && mode !== "human") throw new Error(`runtime is tainted: ${this.taintReason}`);
        if (mode !== "auto") await this.releaseController();
        if (mode !== "human") this.checkRecoveryEpoch(expected?.recoveryEpoch);
        if (mode === "shadow" && this.mode !== "shadow") this.lastPolicySnapshotId = null;
        this.mode = mode;
        if (mode === "auto") this.consecutiveStaleSubmissions = 0;
        if (!(await this.appendEvidence("mode_changed", { mode }))) {
          this.mode = "human";
          await this.releaseController();
          throw new Error("Agent evidence is unavailable; mode change failed closed");
        }
        return this.status();
      } finally {
        if (this.requestedMode === mode) this.requestedMode = null;
      }
    });
  }

  async tick(expected?: RuntimeControlPreconditions): Promise<TickResult> {
    this.checkRecoveryEpoch(expected?.recoveryEpoch);
    if (this.tickActive) return { type: "not_admitted", reason: "tick_in_progress", status: this.status() };
    this.tickActive = true;
    try {
      return await this.serialize(async () => {
        await this.checkControlPreconditions(expected);
        return this.tickOnce();
      });
    } finally {
      this.tickActive = false;
    }
  }

  private async tickOnce(): Promise<TickResult> {
    if (this.stopped) return { type: "not_admitted", reason: "runtime_stopped", status: this.status() };
    if (this.tainted) return { type: "not_admitted", reason: "runtime_tainted", status: this.status() };
    if (this.mode === "human") return { type: "human", status: this.status() };
    let capabilities: Awaited<ReturnType<PolicyConnector["capabilities"]>>;
    try {
      capabilities = await this.options.connector.capabilities();
    } catch (error) {
      await this.failClosed(`capabilities_failed:${message(error)}`);
      return { type: "not_admitted", reason: "capabilities_failed", status: this.status() };
    }
    this.environment = environmentStatus(capabilities);
    const compatibilityReason = manifestCompatibilityReason(this.options.manifest, capabilities);
    if (compatibilityReason) {
      await this.failClosed(compatibilityReason);
      return { type: "not_admitted", reason: compatibilityReason, status: this.status() };
    }
    if (this.lastEvidenceEnvironmentFingerprint !== this.environment.environment_fingerprint) {
      const recorded = await this.appendEvidence("environment_admitted", {
        runtime: this.status().runtime,
        policy_artifact_sha256: this.options.manifest.artifact.sha256,
        environment: this.environment
      });
      if (!recorded) {
        await this.failClosed("agent_evidence_environment_write_failed");
        return { type: "not_admitted", reason: "agent_evidence_write_failed", status: this.status() };
      }
      this.lastEvidenceEnvironmentFingerprint = this.environment.environment_fingerprint;
    }
    this.refreshing = true;
    let bundle: DecisionBundle | null;
    try {
      bundle = await refreshWholeDecisionBundle(this.options.connector, this.options.manifest.requirements.reads, { ...this.staleRefresh, sleep: this.sleep, onStale: async (attempt, delayMs) => { await this.appendEvidence("stale_whole_bundle_discarded", { attempt, delay_ms: delayMs, whole_bundle_discarded: true, action_submission_attempted: false }); } });
    } catch (error) {
      this.refreshing = false;
      await this.failClosed(`observation_failed:${message(error)}`);
      return { type: "not_admitted", reason: "observation_failed", status: this.status() };
    }
    this.refreshing = false;
    if (!bundle) return { type: "not_admitted", reason: "stale_refresh_exhausted", status: this.status() };
    this.lastSnapshotId = bundle.observation.snapshot_id;
    this.lastSnapshot = { snapshot_id: bundle.observation.snapshot_id, sequence: bundle.observation.sequence, status: bundle.observation.status, runtime_instance_id: bundle.observation.session.runtime_instance_id, environment_fingerprint: bundle.observation.session.environment_fingerprint };
    this.lastReads = bundle.reads.map((read) => ({ read_id: read.read_id, kind: read.kind, content_schema: read.content_schema, target_referent_id: read.target_referent_id ?? null }));
    if (this.environment.runtime_instance_id !== bundle.observation.session.runtime_instance_id
        || this.environment.environment_fingerprint !== bundle.observation.session.environment_fingerprint) {
      await this.failClosed("environment_identity_drift");
      return { type: "not_admitted", reason: "environment_identity_drift", status: this.status() };
    }
    const admission = admitWholeDecisionBundle(bundle, this.options.manifest);
    if (!admission.admitted) {
      if (this.mode === "auto" && bundle.observation.status !== "settling") await this.releaseControllerAndReturnHuman();
      return { type: "not_admitted", reason: admission.reason, status: this.status() };
    }
    if (this.mode === "shadow" && this.lastPolicySnapshotId === bundle.observation.snapshot_id) {
      return { type: "not_admitted", reason: "snapshot_already_scored", status: this.status() };
    }
    const input: PolicyDecisionInput = { run_id: this.runId, manifest: this.options.manifest, bundle, candidate_digest: admission.candidateDigest, candidate_count: admission.candidateCount };
    let adapterDecision: AdapterDecision;
    const policyRecoveryEpoch = this.recoveryEpoch;
    const policyController = new AbortController();
    this.activePolicy = { controller: policyController };
    try {
      adapterDecision = await withTimeout(
        Promise.resolve(this.options.policy(input, policyController.signal)),
        this.policyTimeoutMs,
        "policy decision timed out",
        policyController.signal
      );
      assertAdapterDecision(adapterDecision);
      validateAdapterDecision(adapterDecision, admission.candidateDigest, admission.candidateCount);
    } catch (error) {
      if (this.policyRecoveryCancelled(policyRecoveryEpoch, error)) {
        return { type: "not_admitted", reason: "runtime_recovery_epoch_mismatch", status: this.status() };
      }
      await this.failClosed(`policy_failed:${message(error)}`);
      return { type: "not_admitted", reason: "policy_failed", status: this.status() };
    } finally {
      if (this.activePolicy?.controller === policyController) this.activePolicy = null;
    }
    const decision = makeDecision(this.options.manifest, this.runId, bundle, adapterDecision, admission, this.now());
    this.lastPolicySnapshotId = bundle.observation.snapshot_id;
    let resolved: PlayerEnvironmentBoundAction | null;
    try {
      resolved = admitWholeDecision(decision, bundle, this.options.manifest, this.runId).boundAction;
    } catch (error) {
      await this.failClosed(`policy_decision_admission_failed:${message(error)}`);
      return { type: "not_admitted", reason: "policy_decision_admission_failed", status: this.status() };
    }
    this.lastDecision = { decision_id: decision.decision_id, candidate_digest: decision.candidate_digest, candidate_count: decision.candidate_count, scores: [...decision.scores], selected_index: decision.selected_index, bound_action_id: resolved?.bound_action_id ?? null, bound_action_label: resolved?.label ?? null };
    if (!(await this.appendEvidence("decision", { decision, resolved_bound_action_id: resolved?.bound_action_id ?? null }))) {
      await this.failClosed("agent_evidence_write_failed_before_submit");
      return { type: "not_admitted", reason: "agent_evidence_write_failed", status: this.status() };
    }
    if (this.mode === "shadow") return { type: "shadow", decision, status: this.status() };
    if (!resolved) {
      if (this.mode === "one_step") await this.completeOneStep();
      else if (this.mode === "auto") await this.releaseControllerAndReturnHuman("policy_abstained");
      return { type: "not_executed", decision, status: this.status() };
    }
    if (this.mutationCancellationRequested()) {
      return { type: "not_admitted", reason: "mode_changed_before_submit", status: this.status() };
    }
    try {
      await this.acquireController();
    } catch (error) {
      await this.failClosed(`controller_acquire_failed:${message(error)}`);
      return { type: "not_admitted", reason: "controller_acquire_failed", status: this.status() };
    }
    if (this.mutationCancellationRequested()) {
      await this.releaseController();
      return { type: "not_admitted", reason: "mode_changed_before_submit", status: this.status() };
    }
    const requestId = `request-${this.runId}-${decision.decision_id}`;
    if (this.submittedRequestIds.has(requestId)) { await this.failClosed("duplicate_request_id"); return { type: "not_admitted", reason: "duplicate_request_id", status: this.status() }; }
    this.submittedRequestIds.add(requestId);
    let receipt: PlayerEnvironmentReceipt;
    try {
      receipt = await this.options.connector.submit({ requestId, expectedSnapshotId: bundle.observation.snapshot_id, boundActionId: resolved.bound_action_id });
    } catch (error) {
      await this.taint(`unknown_delivery_after_submit:${message(error)}`);
      return { type: "unknown", decision, receipt: null, error: message(error), status: this.status() };
    }
    if (receipt.request_id !== requestId || receipt.action.bound_action_id !== resolved.bound_action_id) {
      const reason = "receipt_correlation_failed";
      await this.appendEvidence("receipt_rejected", {
        decision_id: decision.decision_id,
        expected_request_id: requestId,
        expected_bound_action_id: resolved.bound_action_id,
        receipt
      });
      await this.taint(reason);
      return { type: "unknown", decision, receipt, error: reason, status: this.status() };
    }
    this.lastReceipt = { request_id: receipt.request_id, delivery: receipt.delivery, reason_code: receipt.reason_code ?? null, successor_snapshot_id: receipt.successor?.snapshot_id ?? null };
    const receiptRecorded = await this.appendEvidence("receipt", { decision_id: decision.decision_id, receipt });
    if (!receiptRecorded) await this.taintWithoutEvidence("agent_evidence_write_failed_after_submit");
    if (receipt.delivery === "unknown") { await this.taint(`unknown_delivery:${receipt.reason_code ?? "unspecified"}`); return { type: "unknown", decision, receipt, error: "Connector returned unknown delivery", status: this.status() }; }
    if (receipt.delivery === "not_delivered") {
      if (this.mode === "one_step") await this.completeOneStep();
      else if (this.mode === "auto") {
        // Only a correlated, explicitly unapplied stale submission allows a new
        // decision next tick. Never reuse its scores, action or request identity.
        const refreshable = receiptRecorded && !this.tainted
          && receipt.reason_code === "stale_snapshot" && receipt.retry.allowed
          && receipt.retry.reason === "fresh_snapshot_required";
        if (!refreshable || ++this.consecutiveStaleSubmissions >= 3)
          await this.releaseControllerAndReturnHuman("action_not_delivered");
        else await this.releaseController();
      }
      return { type: "not_delivered", decision, receipt, status: this.status() };
    }
    this.consecutiveStaleSubmissions = 0;
    try {
      const successor = await this.stableSuccessor(bundle.observation);
      if (this.mutationCancellationRequested())
        return { type: "not_admitted", reason: "recovery_requested_after_delivery", status: this.status() };
      if (!successor) { await this.taint("successor_not_stable"); return { type: "unknown", decision, receipt, error: "delivered action did not yield a stable distinct successor", status: this.status() }; }
      this.lastReceipt = { ...this.lastReceipt!, successor_snapshot_id: successor.snapshot_id };
      if (!(await this.appendEvidence("successor", { decision_id: decision.decision_id, successor }))) await this.taintWithoutEvidence("agent_evidence_write_failed_after_successor");
      if (this.mode === "one_step") await this.completeOneStep();
      return { type: "delivered", decision, bound_action: resolved, receipt, successor, status: this.status() };
    } catch (error) {
      await this.taint(`unknown_successor_after_submit:${message(error)}`);
      return { type: "unknown", decision, receipt, error: message(error), status: this.status() };
    }
  }

  async stop(): Promise<RuntimeStatus> {
    this.advanceRecoveryEpoch();
    this.stopRequested = true;
    this.cancelActivePolicy();
    return this.serialize(async () => {
      if (this.stopped) return this.status();
      await this.releaseController();
      if (!(await this.appendEvidence("stopped", {}))) {
        await this.taintWithoutEvidence("agent_evidence_write_failed_on_stop");
      }
      this.mode = "human";
      this.stopped = true;
      if (this.options.evidence && !this.evidenceFinalized) {
        await this.options.evidence.finalize({ status: this.tainted ? "tainted" : "stopped", tainted: this.tainted, mode: this.mode, now: this.now() });
        this.evidenceFinalized = true;
      }
      return this.status();
    });
  }

  private async acquireController(): Promise<void> { if (this.held) return; await this.options.connector.acquireController(); this.held = true; if (!(await this.appendEvidence("controller_acquired", {}))) throw new Error("Agent evidence failed after controller acquisition"); }
  private async releaseController(): Promise<void> {
    if (!this.held) return;
    try {
      await this.options.connector.releaseController();
    } catch (error) {
      const reason = `controller_release_failed:${message(error)}`;
      this.errors = [...this.errors, reason].slice(-20);
      this.invalidations = [...this.invalidations, reason].slice(-20);
      await this.appendEvidence("controller_release_failed", { reason });
      throw error;
    }
    this.held = false;
    await this.appendEvidence("controller_released", {});
  }
  private async releaseControllerAndReturnHuman(reason = "auto_surface_not_admitted"): Promise<void> { await this.releaseController(); this.mode = "human"; await this.appendEvidence("handoff_to_human", { reason }); }
  private async completeOneStep(): Promise<void> { await this.releaseController(); this.mode = "human"; await this.appendEvidence("one_step_completed", {}); }
  private async failClosed(reason: string): Promise<void> { this.errors = [...this.errors, reason].slice(-20); this.invalidations = [...this.invalidations, reason].slice(-20); this.mode = "human"; await this.releaseController(); await this.appendEvidence("fail_closed", { reason }); }
  private async taint(reason: string): Promise<void> { await this.taintWithoutEvidence(reason); await this.appendEvidence("runtime_tainted", { reason, retry: false }); }
  private async taintWithoutEvidence(reason: string): Promise<void> { this.tainted = true; this.taintReason = reason; this.errors = [...this.errors, reason].slice(-20); this.invalidations = [...this.invalidations, reason].slice(-20); this.mode = "human"; try { await this.releaseController(); } catch { /* retain held state; a failed release is not confirmation */ } }
  private async appendEvidence(kind: string, payload: Record<string, unknown>): Promise<boolean> { try { await this.options.evidence?.append(kind, payload, this.now()); return true; } catch (error) { const reason = `agent_evidence_write_failed:${message(error)}`; this.errors = [...this.errors, reason].slice(-20); this.invalidations = [...this.invalidations, reason].slice(-20); return false; } }
  private async stableSuccessor(previous: PlayerEnvironmentSnapshot): Promise<PlayerEnvironmentSnapshot | null> {
    for (let attempt = 1; attempt <= this.successorPoll.maxAttempts; attempt += 1) {
      if (this.mutationCancellationRequested()) return null;
      // One observation per attempt: do not multiply nested retry budgets.
      const next = await refreshWholeDecisionBundle(this.options.connector, [], { maxAttempts: 1, baseBackoffMs: 0, sleep: this.sleep });
      if (next) {
        const observed = next.observation;
        if (observed.session.runtime_instance_id !== previous.session.runtime_instance_id
            || observed.session.environment_fingerprint !== previous.session.environment_fingerprint) {
          throw new Error("successor_environment_identity_drift");
        }
        if (observed.snapshot_id !== previous.snapshot_id && observed.sequence <= previous.sequence) {
          throw new Error("successor_sequence_not_newer");
        }
        if (observed.snapshot_id !== previous.snapshot_id && observed.status !== "settling") {
          this.lastSnapshotId = observed.snapshot_id;
          this.lastSnapshot = { snapshot_id: observed.snapshot_id, sequence: observed.sequence, status: observed.status, runtime_instance_id: observed.session.runtime_instance_id, environment_fingerprint: observed.session.environment_fingerprint };
          this.lastReads = next.reads.map((read) => ({ read_id: read.read_id, kind: read.kind, content_schema: read.content_schema, target_referent_id: read.target_referent_id ?? null }));
          return observed;
        }
      }
      if (attempt < this.successorPoll.maxAttempts && this.successorPoll.baseBackoffMs > 0) await this.sleep(this.successorPoll.baseBackoffMs);
    }
    return null;
  }
  private mutationCancellationRequested(): boolean {
    return this.stopRequested
      || this.requestedMode === "human"
      || this.requestedMode === "shadow";
  }
  private cancelActivePolicy(): void {
    if (this.activePolicy && !this.activePolicy.controller.signal.aborted) this.activePolicy.controller.abort();
  }
  private policyRecoveryCancelled(expectedEpoch: number, error: unknown): boolean {
    return this.recoveryEpoch !== expectedEpoch
      || this.stopRequested
      || this.requestedMode === "human"
      || this.requestedMode === "shadow"
      || error instanceof PolicyRecoveryCancelledError;
  }
  private serialize<T>(operation: () => Promise<T>): Promise<T> {
    const current = this.operation.then(operation, operation);
    this.operation = current.then(() => undefined, () => undefined);
    return current;
  }
}

export class PolicyRuntimeApplicationService {
  constructor(private readonly runtime: PolicyRuntime) {}
  async execute(command: RuntimeCommand): Promise<ApplicationResult> {
    switch (command.type) {
      case "status": return { type: "status", status: this.runtime.status() };
      case "set_mode": return { type: "mode_changed", status: await this.runtime.setMode(command.mode) };
      case "tick": return { type: "tick", result: await this.runtime.tick() };
      case "stop": return { type: "stopped", status: await this.runtime.stop() };
      default: return assertNever(command);
    }
  }
}

function makeDecision(manifest: PolicyManifest, runId: string, bundle: DecisionBundle, adapter: AdapterDecision, admission: Admission, issuedAt: string): PolicyDecision {
  const decision = { schema: "sts2.policy-runtime/decision-1" as const, decision_id: `decision-${randomUUID()}`, run_id: runId, manifest_id: manifest.manifest_id, snapshot_id: bundle.observation.snapshot_id, candidate_digest: adapter.candidate_digest, candidate_count: admission.candidateCount, scores: [...adapter.scores], selected_index: adapter.selected_index, disposition: adapter.selected_index === null ? "abstain" as const : "admit" as const, issued_at: issuedAt } satisfies PolicyDecision;
  return validatePolicyDecision(decision);
}

function isStale(error: unknown): boolean { return error instanceof StaleWholeBundleError || (error instanceof Error && (error as Error & { code?: string }).code === "stale_state"); }
function manifestCompatibilityReason(manifest: PolicyManifest, capabilities: Awaited<ReturnType<PolicyConnector["capabilities"]>>): string | null {
  if (capabilities.protocol_version !== manifest.requirements.connector_protocol_version) return "connector_protocol_unsupported";
  const environment = manifest.requirements.environment;
  if (capabilities.host.host_kind !== environment.host_kind) return "environment_host_kind_drift";
  if (capabilities.host.version !== environment.connector_version) return "environment_connector_version_drift";
  if (capabilities.host.implementation.source_revision !== environment.connector_source_revision) return "environment_connector_source_revision_drift";
  if (capabilities.host.implementation.artifact_sha256 !== environment.connector_artifact_sha256) return "environment_connector_artifact_sha256_drift";
  if (capabilities.host.implementation.module_version_id !== environment.connector_module_version_id) return "environment_connector_module_version_id_drift";
  if (capabilities.game.modset.status !== environment.modset_status) return "environment_modset_status_drift";
  if (capabilities.game.modset.fingerprint !== environment.modset_fingerprint) return "environment_modset_fingerprint_drift";
  if (!sameStringArray(capabilities.game.modset.loaded_mod_ids, environment.loaded_mod_ids)) return "environment_loaded_mod_ids_drift";
  if (!capabilities.execution_available || capabilities.single_controller !== true) return "connector_execution_unavailable";
  if (!capabilities.game.version || !manifest.support.game_versions.includes(capabilities.game.version)) return "game_version_unsupported";
  if (!capabilities.game.commit || !manifest.support.game_commits.includes(capabilities.game.commit)) return "game_commit_unsupported";
  return null;
}
function environmentStatus(capabilities: Awaited<ReturnType<PolicyConnector["capabilities"]>>): NonNullable<RuntimeStatus["environment"]> {
  return {
    runtime_instance_id: capabilities.host.runtime_instance_id,
    environment_fingerprint: capabilities.environment_fingerprint,
    host_kind: capabilities.host.host_kind,
    connector_protocol_version: capabilities.protocol_version,
    connector_version: capabilities.host.version,
    connector_source_revision: capabilities.host.implementation.source_revision ?? null,
    connector_artifact_sha256: capabilities.host.implementation.artifact_sha256 ?? null,
    connector_module_version_id: capabilities.host.implementation.module_version_id ?? null,
    game_version: capabilities.game.version ?? null,
    game_commit: capabilities.game.commit ?? null,
    modset_status: capabilities.game.modset.status,
    modset_fingerprint: capabilities.game.modset.fingerprint,
    loaded_mod_ids: [...capabilities.game.modset.loaded_mod_ids]
  };
}
function sameStringArray(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}
function message(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function assertNever(value: never): never { throw new Error(`unknown runtime command: ${JSON.stringify(value)}`); }
class PolicyRecoveryCancelledError extends Error {
  constructor() { super("policy decision cancelled for recovery"); }
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number, detail: string, signal?: AbortSignal): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let onAbort: (() => void) | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error(detail)), timeoutMs);
  });
  const cancellation = signal === undefined
    ? null
    : new Promise<never>((_resolve, reject) => {
      onAbort = () => reject(new PolicyRecoveryCancelledError());
      if (signal.aborted) onAbort();
      else signal.addEventListener("abort", onAbort, { once: true });
    });
  const races: Promise<T | never>[] = [promise, timeout];
  if (cancellation) races.push(cancellation);
  return Promise.race(races).finally(() => {
    if (timer !== undefined) clearTimeout(timer);
    if (onAbort !== undefined && signal !== undefined) signal.removeEventListener("abort", onAbort);
  });
}
