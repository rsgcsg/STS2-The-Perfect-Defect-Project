import { randomUUID } from "node:crypto";
import type { PlayerEnvironmentBoundAction, PlayerEnvironmentReceipt, PlayerEnvironmentSnapshot, TextMenuV2Snapshot } from "@rsgcsg/sts2-connector-client";
import { admitWholeDecision } from "./admission.js";
import { AgentRunEvidence, canonicalJson } from "./evidence.js";
import { candidateOrderDigest } from "./digest.js";
import { DEFAULT_AUTONOMY_BUDGET, POLICY_RUNTIME_VERSION, assertAdapterDecision, decisionActionId, decisionActions, isTextMenuSnapshot, validateAdapterDecision, validatePolicyDecision, validatePolicyManifest, type AdapterDecision, type ApplicationResult, type AutonomyBudgetConfig, type AutonomyBudgetEndReason, type AutonomyBudgetExhaustionReason, type AnyDecisionBundle, type ConfirmedInteraction, type DecisionAction, type ManagedCapabilities, type ManagedControlConfirmation, type Policy, type PolicyConnector, type PolicyDecision, type PolicyDecisionInput, type PolicyManifest, type RuntimeCommand, type RuntimeMode, type RuntimeStatus, type StatefulPolicy, type StatefulPolicyDecisionInput, type ObservationCompletion, type TextAction, type TextActionResult, type TextInputProfile, type TextSnapshot, type TickResult } from "./contracts.js";
import { StaleWholeBundleError } from "./connector.js";
import { RUNTIME_ENVIRONMENT_SCHEMA, type RuntimeControlPreconditions, type RuntimeEnvironmentBinding } from "./contracts.js";

/** A known-unapplied control request, not an uncertain gameplay delivery. */
export class RuntimeControlPreconditionError extends Error {
  constructor(readonly code: string, readonly httpStatus: number) { super(code); }
}

export interface RuntimeOptions {
  manifest: PolicyManifest;
  connector: PolicyConnector;
  policy?: Policy;
  statefulPolicy?: StatefulPolicy;
  /** A child port marks an offer only when stdin.write is invoked. */
  statefulOfferBoundary?: "invocation" | "port_write";
  mode?: RuntimeMode;
  runId?: string;
  evidence?: AgentRunEvidence;
  staleRefresh?: { maxAttempts: number; baseBackoffMs: number };
  successorPoll?: { maxAttempts: number; baseBackoffMs: number };
  policyTimeoutMs?: number;
  sleep?: (milliseconds: number) => Promise<void>;
  /** Monotonic clock used for the finite Shadow/Auto authorization. */
  monotonicNow?: () => number;
  autoBudget?: Partial<AutonomyBudgetConfig>;
  now?: () => string;
  runtimeIdentity?: { version: string; code_sha256: string | null };
  /** Digest of the validated, separately sealed Managed environment binding. */
  managedBindingSha256?: string;
}

export interface Admission { admitted: boolean; reason: string; candidateDigest: string; candidateCount: number }

export function admitWholeDecisionBundle(bundle: AnyDecisionBundle, manifest?: PolicyManifest): Admission {
  const catalog = isTextMenuSnapshot(bundle.observation) ? bundle.observation.menu_actions : bundle.observation.bound_actions;
  const actions = decisionActions(bundle.observation);
  const candidateDigest = candidateOrderDigest(actions);
  if (manifest && bundle.observation.schema !== manifest.representation.input_schema) return { admitted: false, reason: "snapshot_profile_drift", candidateDigest, candidateCount: actions.length };
  if (bundle.observation.status !== "interactive") return { admitted: false, reason: `snapshot_${bundle.observation.status}`, candidateDigest, candidateCount: actions.length };
  if (bundle.observation.completeness.status !== "complete") return { admitted: false, reason: "snapshot_incomplete", candidateDigest, candidateCount: actions.length };
  if (catalog.status !== "complete" || catalog.materialized_count !== catalog.total_count || catalog.materialized_count !== actions.length || actions.length === 0) return { admitted: false, reason: "complete_catalog_required", candidateDigest, candidateCount: actions.length };
  if (new Set(actions.map(decisionActionId)).size !== actions.length) return { admitted: false, reason: isTextMenuSnapshot(bundle.observation) ? "duplicate_action_id" : "duplicate_bound_action_id", candidateDigest, candidateCount: actions.length };
  if (manifest && !manifest.support.interaction_kinds.includes(bundle.observation.interaction.kind)) return { admitted: false, reason: "unsupported_interaction_kind", candidateDigest, candidateCount: actions.length };
  if (manifest && actions.some((action) => !manifest.support.action_verbs.includes(action.verb))) return { admitted: false, reason: "unsupported_action_verb", candidateDigest, candidateCount: actions.length };
  return { admitted: true, reason: "whole_decision_admitted", candidateDigest, candidateCount: actions.length };
}

export async function refreshWholeDecisionBundle(connector: PolicyConnector, requiredReadKinds: readonly string[], options: { maxAttempts: number; baseBackoffMs: number; sleep?: (milliseconds: number) => Promise<void>; onStale?: (attempt: number, delayMs: number) => Promise<void> | void; inputProfile?: TextInputProfile }): Promise<AnyDecisionBundle | null> {
  if (!Number.isSafeInteger(options.maxAttempts) || options.maxAttempts < 1) throw new Error("maxAttempts must be a positive integer");
  if (options.baseBackoffMs < 0) throw new Error("baseBackoffMs must be non-negative");
  const sleep = options.sleep ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  for (let attempt = 1; attempt <= options.maxAttempts; attempt += 1) {
    try { return await connector.observeBundle(requiredReadKinds, options.inputProfile); } catch (error) {
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
  private nativeSubmissionsUsed = 0;
  private menuNavigationsUsed = 0;
  private operation: Promise<unknown> = Promise.resolve();
  private requestedMode: RuntimeMode | null = null;
  private stopRequested = false;
  private recoveryEpoch = 0;
  private recoveryEpochExhausted = false;
  private activePolicy: { controller: AbortController } | null = null;
  private continuity: { token: string; gameId: string; runtimeId: string; environment: string } | null = null;
  private confirmedInteraction: { value: ConfirmedInteraction; sequence: number; epoch: number } | null = null;
  private autonomyBudgetTimer: ReturnType<typeof setTimeout> | undefined;
  private autonomyBudgetGeneration = 0;
  private autonomyBudgetHandoffQueued = false;
  private autonomyBudgetRecoveryFenced = false;
  private autonomyBudgetHandoffFinished = false;
  private controllerReleaseUnconfirmed = false;
  private readonly runId: string;
  private readonly now: () => string;
  private readonly staleRefresh: { maxAttempts: number; baseBackoffMs: number };
  private readonly successorPoll: { maxAttempts: number; baseBackoffMs: number };
  private readonly policyTimeoutMs: number;
  private readonly sleep: (milliseconds: number) => Promise<void>;
  private readonly monotonicNow: () => number;
  private readonly autoBudget: AutonomyBudgetConfig;
  private autonomyBudgetState: {
    state: RuntimeStatus["autonomy_budget"]["state"];
    submissionsUsed: number;
    policyCallsUsed: number;
    startedAt: number | null;
    elapsedMs: number;
    exhaustedReason: AutonomyBudgetExhaustionReason | null;
    endedReason: AutonomyBudgetEndReason | null;
  };

  constructor(private readonly options: RuntimeOptions) {
    validatePolicyManifest(options.manifest);
    const managed = isManagedManifest(options.manifest);
    if (managed !== (options.managedBindingSha256 !== undefined)
        || (managed && !/^[a-f0-9]{64}$/u.test(options.managedBindingSha256 ?? ""))) {
      throw new Error("Managed Runtime requires one exact environment binding digest");
    }
    if (this.stateful && !options.statefulPolicy) throw new Error("stateful adapter requires a stateful policy");
    if (!this.stateful && !options.policy) throw new Error("v1 adapter requires a policy");
    if (this.interactionPort && !options.evidence) throw new Error("v3 confirmed interaction requires an Evidence writer");
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
    this.monotonicNow = options.monotonicNow ?? (() => performance.now());
    this.autoBudget = normalizeAutonomyBudget(options.autoBudget);
    const started = isAutonomyMode(this.mode);
    this.autonomyBudgetState = {
      state: started ? "active" : "inactive",
      submissionsUsed: 0,
      policyCallsUsed: 0,
      startedAt: started ? this.monotonicNow() : null,
      elapsedMs: 0,
      exhaustedReason: null,
      endedReason: null
    };
    if (started) this.scheduleAutonomyBudgetDeadline();
  }

  private get stateful(): boolean {
    return this.options.manifest.adapter.protocol !== "sts2.policy-runtime/decision-only-ndjson-1";
  }

  private get interactionPort(): boolean {
    return this.options.manifest.adapter.protocol === "sts2.policy-runtime/decision-only-ndjson-3";
  }

  private bindContinuity(gameId: string, snapshot: AnyDecisionBundle["observation"]): string {
    const runtimeId = snapshot.session.runtime_instance_id;
    const environment = snapshot.session.environment_fingerprint;
    if (!this.continuity || this.continuity.gameId !== gameId ||
        this.continuity.runtimeId !== runtimeId || this.continuity.environment !== environment) {
      this.confirmedInteraction = null;
      this.continuity = { token: randomUUID(), gameId, runtimeId, environment };
    }
    return this.continuity.token;
  }

  private rotateContinuity(): void {
    if (this.continuity) this.continuity = { ...this.continuity, token: randomUUID() };
  }

  private resetInteractionContinuity(): void {
    if (!this.interactionPort) return;
    this.confirmedInteraction = null;
    this.rotateContinuity();
  }

  private validateCompletion(completion: ObservationCompletion, input: StatefulPolicyDecisionInput): void {
    if (completion === null || typeof completion !== "object" ||
        Object.keys(completion).sort().join(",") !== (this.interactionPort ? "continuity_token,previous_interaction_request_id,sequence,snapshot_id" : "continuity_token,sequence,snapshot_id") ||
        (this.interactionPort && completion.previous_interaction_request_id !== (input.previous_interaction?.request_id ?? null)) ||
        completion.continuity_token !== input.continuity_token ||
        completion.snapshot_id !== input.bundle.observation.snapshot_id ||
        completion.sequence !== input.bundle.observation.sequence ||
        !Number.isSafeInteger(completion.sequence) || completion.sequence < 0) {
      throw new Error("policy observation completion watermark mismatch");
    }
  }

  status(): RuntimeStatus {
    const budget = this.autonomyBudgetStatus();
    return { schema: "sts2.policy-runtime/status-1", runtime: this.options.runtimeIdentity ?? { version: POLICY_RUNTIME_VERSION, code_sha256: null }, policy: { manifest_id: this.options.manifest.manifest_id, policy_id: this.options.manifest.policy.id, policy_version: this.options.manifest.policy.version, provider: this.options.manifest.policy.provider, architecture: this.options.manifest.policy.architecture, artifact_sha256: this.options.manifest.artifact.sha256 }, run_id: this.runId, lifecycle: this.stopped ? "stopped" : "running", mode: this.mode, controller: this.controllerStatus(), autonomy_budget: budget, tainted: this.tainted, taint_reason: this.taintReason, refreshing: this.refreshing, last_snapshot_id: this.lastSnapshotId, last_snapshot: this.lastSnapshot, last_decision: this.lastDecision, last_receipt: this.lastReceipt, reads: [...this.lastReads], invalidations: [...this.invalidations], errors: [...this.errors] , environment: this.environment };
  }

  private controllerStatus(): RuntimeStatus["controller"] {
    return isManagedManifest(this.options.manifest) && this.controllerReleaseUnconfirmed
      ? "unknown" : this.held ? "held" : "released";
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
      identity = isManagedCapabilities(capabilities)
        ? capabilities.environment.runtime_instance_id : capabilities.host.runtime_instance_id;
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
      this.resetInteractionContinuity();
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
        if (mode !== this.mode) this.resetInteractionContinuity();
        if (this.tainted && mode !== "human") throw new Error(`runtime is tainted: ${this.taintReason}`);
        if (mode === "human") {
          this.mode = "human";
          if (this.autonomyBudgetState.state === "active") this.endAutonomyBudget("human_recovery");
        }
        if (mode !== "auto") {
          try { await this.releaseController(); }
          catch (error) {
            if (mode !== "human") {
              this.mode = "human";
              this.endAutonomyBudget("mode_changed");
            }
            throw error;
          }
        }
        if (mode !== "human") this.checkRecoveryEpoch(expected?.recoveryEpoch);
        if (isAutonomyMode(mode) && !isAutonomyMode(this.mode)
            && this.autonomyBudgetState.state === "exhausted" && !this.autonomyBudgetHandoffFinished) {
          await this.handoffAutonomyBudget();
          if (this.tainted) throw new Error(`runtime is tainted: ${this.taintReason}`);
        }
        if (isAutonomyMode(mode) && !isAutonomyMode(this.mode)) this.beginAutonomyBudget();
        else if (!isAutonomyMode(mode) && mode !== "human" && this.autonomyBudgetState.state === "active") this.endAutonomyBudget("mode_changed");
        else if (mode === "human" && this.autonomyBudgetState.state === "active") this.endAutonomyBudget("human_recovery");
        if (mode === "shadow" && this.mode !== "shadow") this.lastPolicySnapshotId = null;
        this.mode = mode;
        if (mode === "auto") this.consecutiveStaleSubmissions = 0;
        if (!(await this.appendEvidence("mode_changed", { mode, autonomy_budget: this.autonomyBudgetStatus() }))) {
          this.mode = "human";
          if (this.autonomyBudgetState.state === "active") this.endAutonomyBudget("human_recovery");
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
    const preparationEpoch = this.recoveryEpoch;
    const representation = this.options.manifest.representation.input_schema;
    const inputProfile: TextInputProfile | undefined = representation === "sts2.player-environment/text-menu-snapshot-1"
      ? "text-menu-v1" : representation === "sts2.player-environment/text-menu-snapshot-2" ? "text-menu-v2" : undefined;
    const textMenu = inputProfile !== undefined;
    if (this.stopped) return { type: "not_admitted", reason: "runtime_stopped", status: this.status() };
    if (this.tainted) return { type: "not_admitted", reason: "runtime_tainted", status: this.status() };
    if (this.mode === "human") return { type: "human", status: this.status() };
    if (this.mutationCancellationRequested())
      return { type: "not_admitted", reason: "runtime_recovery_epoch_mismatch", status: this.status() };
    if (!this.autonomyBudgetAvailable()) {
      await this.handoffAutonomyBudget();
      return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() };
    }
    let capabilities: Awaited<ReturnType<PolicyConnector["capabilities"]>>;
    try {
      capabilities = await this.options.connector.capabilities({ inputProfile });
    } catch (error) {
      if (this.recoveryEpoch !== preparationEpoch || this.mutationCancellationRequested())
        return { type: "not_admitted", reason: "runtime_recovery_epoch_mismatch", status: this.status() };
      await this.failClosed(`capabilities_failed:${message(error)}`);
      return { type: "not_admitted", reason: "capabilities_failed", status: this.status() };
    }
    if (this.recoveryEpoch !== preparationEpoch || this.mutationCancellationRequested())
      return { type: "not_admitted", reason: "runtime_recovery_epoch_mismatch", status: this.status() };
    this.environment = environmentStatus(capabilities);
    const compatibilityReason = manifestCompatibilityReason(this.options.manifest, capabilities, this.options.managedBindingSha256);
    if (compatibilityReason) {
      await this.failClosed(compatibilityReason);
      return { type: "not_admitted", reason: compatibilityReason, status: this.status() };
    }
    const admittedEnvironmentKey = canonicalJson(this.environment);
    if (this.lastEvidenceEnvironmentFingerprint !== admittedEnvironmentKey) {
      const recorded = await this.appendEvidence("environment_admitted", {
        runtime: this.status().runtime,
        policy_artifact_sha256: this.options.manifest.artifact.sha256,
        environment: this.environment
      });
      if (!recorded) {
        await this.failClosed("agent_evidence_environment_write_failed");
        return { type: "not_admitted", reason: "agent_evidence_write_failed", status: this.status() };
      }
      this.lastEvidenceEnvironmentFingerprint = admittedEnvironmentKey;
    }
    if (this.recoveryEpoch !== preparationEpoch || this.mutationCancellationRequested())
      return { type: "not_admitted", reason: "runtime_recovery_epoch_mismatch", status: this.status() };
    this.refreshing = true;
    let bundle: AnyDecisionBundle | null;
    let gameContinuityId: string | null = null;
    try {
      if (this.stateful) {
        if (!this.options.connector.observeTextMenuContext) throw new Error("text_menu_context_unsupported");
        const context = await this.options.connector.observeTextMenuContext(inputProfile);
        if (context.schema !== (inputProfile === "text-menu-v2"
          ? "sts2.player-environment/text-menu-observation-context-2"
          : "sts2.player-environment/text-menu-observation-context-1")) throw new Error("text_menu_context_schema_mismatch");
        gameContinuityId = context.game_continuity_id;
        bundle = { observation: context.snapshot, reads: [] };
      } else {
        bundle = await refreshWholeDecisionBundle(this.options.connector, this.options.manifest.requirements.reads, { ...this.staleRefresh, inputProfile, sleep: this.sleep, onStale: async (attempt, delayMs) => { await this.appendEvidence("stale_whole_bundle_discarded", { attempt, delay_ms: delayMs, whole_bundle_discarded: true, action_submission_attempted: false }); } });
      }
    } catch (error) {
      this.refreshing = false;
      if (this.recoveryEpoch !== preparationEpoch || this.mutationCancellationRequested())
        return { type: "not_admitted", reason: "runtime_recovery_epoch_mismatch", status: this.status() };
      await this.failClosed(`observation_failed:${message(error)}`);
      return { type: "not_admitted", reason: "observation_failed", status: this.status() };
    }
    this.refreshing = false;
    if (this.recoveryEpoch !== preparationEpoch || this.mutationCancellationRequested())
      return { type: "not_admitted", reason: "runtime_recovery_epoch_mismatch", status: this.status() };
    if (!bundle) return { type: "not_admitted", reason: "stale_refresh_exhausted", status: this.status() };
    if (this.stateful && (typeof gameContinuityId !== "string" || gameContinuityId.length === 0)) {
      this.continuity = null;
      await this.failClosed("game_continuity_unavailable");
      return { type: "not_admitted", reason: "game_continuity_unavailable", status: this.status() };
    }
    if (isTextMenuSnapshot(bundle.observation) !== textMenu || bundle.observation.schema !== representation) {
      await this.failClosed("snapshot_profile_drift");
      return { type: "not_admitted", reason: "snapshot_profile_drift", status: this.status() };
    }
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
      if (this.mode === "auto" && bundle.observation.status !== "settling") {
        if (textMenu && !(await this.appendEvidence("text_observation_not_admitted", { reason: admission.reason, snapshot: bundle.observation }))) {
          await this.failClosed("agent_evidence_nonadmitted_observation_write_failed");
          return { type: "not_admitted", reason: "agent_evidence_write_failed", status: this.status() };
        }
        await this.releaseControllerAndReturnHuman();
      }
      return { type: "not_admitted", reason: admission.reason, status: this.status() };
    }
    if (this.mode === "shadow" && this.lastPolicySnapshotId === bundle.observation.snapshot_id) {
      return { type: "not_admitted", reason: "snapshot_already_scored", status: this.status() };
    }
    const input: PolicyDecisionInput = { run_id: this.runId, manifest: this.options.manifest, bundle, candidate_digest: admission.candidateDigest, candidate_count: admission.candidateCount };
    if (this.recoveryEpoch !== preparationEpoch || this.mutationCancellationRequested())
      return { type: "not_admitted", reason: "runtime_recovery_epoch_mismatch", status: this.status() };
    if (!this.consumePolicyCall()) {
      await this.handoffAutonomyBudget();
      return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() };
    }
    let adapterDecision: AdapterDecision;
    let observationContext: { continuity_token: string; previous_interaction_request_id: string | null } | null = null;
    const policyRecoveryEpoch = this.recoveryEpoch;
    const policyController = new AbortController();
    this.activePolicy = { controller: policyController };
    let offered = false;
    try {
      if (this.stateful) {
        const continuityToken = this.bindContinuity(gameContinuityId!, bundle.observation);
        const pendingInteraction = this.confirmedInteraction;
        const previousInteraction = pendingInteraction && pendingInteraction.epoch === this.recoveryEpoch
          && bundle.observation.sequence > pendingInteraction.sequence
          && bundle.observation.snapshot_id !== pendingInteraction.value.snapshot_id
          ? pendingInteraction.value : null;
        const statefulInput: StatefulPolicyDecisionInput = { ...input,
          continuity_token: continuityToken,
          ...(this.interactionPort ? { previous_interaction: previousInteraction } : {}) };
        const onOffer = () => {
          offered = true;
          if (this.interactionPort && previousInteraction && this.confirmedInteraction === pendingInteraction)
            this.confirmedInteraction = null;
        };
        if (this.options.statefulOfferBoundary !== "port_write") onOffer();
        const result = await withTimeout(
          Promise.resolve(this.options.statefulPolicy!(statefulInput, policyController.signal, onOffer)),
          this.policyTimeoutMs, "policy decision timed out", policyController.signal);
        if (!offered) throw new Error("stateful policy returned before offer");
        this.validateCompletion(result.completion, statefulInput);
        if (this.interactionPort) observationContext = {
          continuity_token: result.completion.continuity_token,
          previous_interaction_request_id: result.completion.previous_interaction_request_id ?? null
        };
        adapterDecision = result.output;
      } else {
        adapterDecision = await withTimeout(
          Promise.resolve(this.options.policy!(input, policyController.signal)),
          this.policyTimeoutMs, "policy decision timed out", policyController.signal);
      }
      assertAdapterDecision(adapterDecision);
      validateAdapterDecision(adapterDecision, admission.candidateDigest, admission.candidateCount);
    } catch (error) {
      if (this.stateful && offered) {
        // A timeout can win the race without aborting the pending port read.
        // Fence that request before another continuity token can be offered.
        policyController.abort();
        this.rotateContinuity();
        if (this.interactionPort) this.confirmedInteraction = null;
      }
      if (this.autonomyBudgetState.exhaustedReason === "deadline") {
        await this.handoffAutonomyBudget();
        return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() };
      }
      if (this.policyRecoveryCancelled(policyRecoveryEpoch, error)) {
        return { type: "not_admitted", reason: "runtime_recovery_epoch_mismatch", status: this.status() };
      }
      await this.failClosed(`policy_failed:${message(error)}`);
      return { type: "not_admitted", reason: "policy_failed", status: this.status() };
    } finally {
      if (this.activePolicy?.controller === policyController) this.activePolicy = null;
    }
    if (this.recoveryEpoch !== policyRecoveryEpoch || this.mutationCancellationRequested()) {
      this.resetInteractionContinuity();
      return { type: "not_admitted", reason: "runtime_recovery_epoch_mismatch", status: this.status() };
    }
    const decision = makeDecision(this.options.manifest, this.runId, bundle, adapterDecision, admission, this.now());
    this.lastPolicySnapshotId = bundle.observation.snapshot_id;
    let resolved: DecisionAction | null;
    try {
      resolved = admitWholeDecision(decision, bundle, this.options.manifest, this.runId).boundAction;
    } catch (error) {
      await this.failClosed(`policy_decision_admission_failed:${message(error)}`);
      return { type: "not_admitted", reason: "policy_decision_admission_failed", status: this.status() };
    }
    const resolvedActionId = resolved ? decisionActionId(resolved) : null;
    this.lastDecision = { decision_id: decision.decision_id, candidate_digest: decision.candidate_digest, candidate_count: decision.candidate_count, scores: [...decision.scores], selected_index: decision.selected_index, bound_action_id: resolvedActionId, bound_action_label: resolved?.label ?? null };
    if (textMenu && !(await this.appendEvidence("text_decision_input", {
      decision_id: decision.decision_id, snapshot: bundle.observation,
      ...(this.interactionPort ? { observation_context: observationContext } : {})
    }))) {
      await this.failClosed("agent_evidence_write_failed_before_submit");
      return { type: "not_admitted", reason: "agent_evidence_write_failed", status: this.status() };
    }
    if (!(await this.appendEvidence("decision", { decision, resolved_bound_action_id: resolvedActionId }))) {
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
      if (await this.finishAutonomyBudgetHandoffIfRequested())
        return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() };
      return { type: "not_admitted", reason: "mode_changed_before_submit", status: this.status() };
    }
    try {
      await this.acquireController();
    } catch (error) {
      if (isManagedManifest(this.options.manifest)) await this.taint(`controller_acquire_unknown:${message(error)}`);
      else await this.failClosed(`controller_acquire_failed:${message(error)}`);
      return { type: "not_admitted", reason: "controller_acquire_failed", status: this.status() };
    }
    if (this.mutationCancellationRequested()) {
      if (await this.finishAutonomyBudgetHandoffIfRequested())
        return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() };
      await this.releaseController();
      return { type: "not_admitted", reason: "mode_changed_before_submit", status: this.status() };
    }
    const requestId = `request-${this.runId}-${decision.decision_id}`;
    if (this.submittedRequestIds.has(requestId)) { await this.failClosed("duplicate_request_id"); return { type: "not_admitted", reason: "duplicate_request_id", status: this.status() }; }
    if (!this.consumeSubmissionAttempt()) {
      await this.handoffAutonomyBudget();
      return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() };
    }
    if (textMenu) {
      return this.submitTextMenuDecision(decision, resolved as TextAction, bundle.observation as TextSnapshot, requestId, this.recoveryEpoch);
    }
    this.submittedRequestIds.add(requestId);
    let receipt: PlayerEnvironmentReceipt;
    try {
      receipt = await this.options.connector.submit({ requestId, expectedSnapshotId: bundle.observation.snapshot_id, boundActionId: (resolved as PlayerEnvironmentBoundAction).bound_action_id }) as PlayerEnvironmentReceipt;
    } catch (error) {
      await this.taint(`unknown_delivery_after_submit:${message(error)}`);
      return { type: "unknown", decision, receipt: null, error: message(error), status: this.status() };
    }
    if (receipt.request_id !== requestId || receipt.action.bound_action_id !== (resolved as PlayerEnvironmentBoundAction).bound_action_id) {
      const reason = "receipt_correlation_failed";
      await this.appendEvidence("receipt_rejected", {
        decision_id: decision.decision_id,
        expected_request_id: requestId,
        expected_bound_action_id: (resolved as PlayerEnvironmentBoundAction).bound_action_id,
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
        await this.finishKnownNonDelivery(refreshable, "action_not_delivered");
      }
      return { type: "not_delivered", decision, receipt, status: this.status() };
    }
    this.consecutiveStaleSubmissions = 0;
    try {
      const successor = await this.stableSuccessor(bundle.observation);
      if (this.mutationCancellationRequested()) {
        if (await this.finishAutonomyBudgetHandoffIfRequested())
          return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() };
        return { type: "not_admitted", reason: "recovery_requested_after_delivery", status: this.status() };
      }
      if (!successor) { await this.taint("successor_not_stable"); return { type: "unknown", decision, receipt, error: "delivered action did not yield a stable distinct successor", status: this.status() }; }
      this.lastReceipt = { ...this.lastReceipt!, successor_snapshot_id: successor.snapshot_id };
      if (!(await this.appendEvidence("successor", { decision_id: decision.decision_id, successor }))) await this.taintWithoutEvidence("agent_evidence_write_failed_after_successor");
      if (this.mode === "one_step") await this.completeOneStep();
      return { type: "delivered", decision, bound_action: resolved as PlayerEnvironmentBoundAction, receipt, successor: successor as PlayerEnvironmentSnapshot, status: this.status() };
    } catch (error) {
      await this.taint(`unknown_successor_after_submit:${message(error)}`);
      return { type: "unknown", decision, receipt, error: message(error), status: this.status() };
    }
  }

  private async submitTextMenuDecision(decision: PolicyDecision, action: TextAction, previous: TextSnapshot, requestId: string, submissionEpoch: number): Promise<TickResult> {
    if (action.effect_domain === "text_menu") this.lastReceipt = null;
    if (!(await this.appendEvidence("text_menu_dispatch_attempt", { decision_id: decision.decision_id, action_id: action.action_id, effect_domain: action.effect_domain, native_submissions_used: this.nativeSubmissionsUsed + (action.effect_domain === "native_input" ? 1 : 0), menu_navigations_used: this.menuNavigationsUsed + (action.effect_domain === "text_menu" ? 1 : 0) }))) {
      await this.taint("agent_evidence_write_failed_before_submit");
      return { type: "not_admitted", reason: "agent_evidence_write_failed", status: this.status() };
    }
    // These are dispatch attempt counts, including an attempt cancelled before
    // Connector submission. Evidence has already recorded the same increment.
    if (action.effect_domain === "text_menu") this.menuNavigationsUsed += 1;
    else this.nativeSubmissionsUsed += 1;
    // Human/Stop/budget can enter while the durable append is pending. There
    // is no await between this fence and submit: a cancelled intent must not
    // issue a new Connector mutation. No Connector result exists to record.
    if (submissionEpoch !== this.recoveryEpoch || this.mutationCancellationRequested()) {
      if (!(await this.appendEvidence("text_menu_dispatch_cancelled", {
        decision_id: decision.decision_id, reason: "recovery_before_submit"
      }))) await this.taint("agent_evidence_dispatch_cancel_write_failed");
      if (await this.finishAutonomyBudgetHandoffIfRequested())
        return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() };
      return { type: "not_admitted", reason: "mode_changed_before_submit", status: this.status() };
    }
    this.submittedRequestIds.add(requestId);
    let result: TextActionResult;
    try {
      result = await this.options.connector.submit({ requestId, expectedSnapshotId: previous.snapshot_id,
        boundActionId: action.action_id, inputProfile: previous.input_profile,
        previousSnapshot: previous.schema === "sts2.player-environment/text-menu-snapshot-2" ? previous : undefined }) as TextActionResult;
    } catch (error) {
      await this.taint(`unknown_delivery_after_submit:${message(error)}`);
      return { type: "unknown", decision, receipt: null, error: message(error), status: this.status() };
    }
    if (result.request_id !== requestId || (result.action !== null && canonicalJson(result.action) !== canonicalJson(action))
        || (result.status !== "not_applied" && (result.action?.action_id !== action.action_id || result.effect_domain !== action.effect_domain))) {
      await this.appendEvidence("text_menu_result_rejected", { decision_id: decision.decision_id, expected_request_id: requestId, expected_action_id: action.action_id, result });
      await this.taint("text_menu_result_correlation_failed");
      return { type: "unknown", decision, receipt: result, error: "text_menu_result_correlation_failed", status: this.status() };
    }
    if ((result.status === "applied" && (result.native_delivery !== (action.effect_domain === "text_menu" ? null : "delivered") || result.retry !== "never"))
        || (result.status === "unknown" && (action.effect_domain !== "native_input" || result.native_delivery !== "unknown" || result.retry !== "never"))) {
      await this.taint("text_menu_result_invalid_delivery");
      return { type: "unknown", decision, receipt: result, error: "text_menu_result_invalid_delivery", status: this.status() };
    }
    if (result.status === "unknown") {
      await this.appendEvidence("text_native_unknown", { decision_id: decision.decision_id, result });
      await this.taint(`unknown_delivery:${result.reason_code ?? "unspecified"}`);
      return { type: "unknown", decision, receipt: result, error: "Connector returned unknown native delivery", status: this.status() };
    }
    if (result.status === "not_applied") {
      const recorded = await this.appendEvidence("text_menu_not_applied", { decision_id: decision.decision_id, result });
      if (!recorded) await this.taintWithoutEvidence("agent_evidence_write_failed_after_submit");
      this.confirmedInteraction = null;
      if (this.mode === "one_step") await this.completeOneStep();
      else if (this.mode === "auto") {
        // A new observation and policy call may follow only an explicitly
        // unapplied stale result. The old action, scores and request are spent.
        const refreshable = this.options.evidence != null && recorded && !this.tainted
          && submissionEpoch === this.recoveryEpoch && !this.mutationCancellationRequested()
          && previous.schema === "sts2.player-environment/text-menu-snapshot-2"
          && result.reason_code === "stale_snapshot" && result.retry === "reobserve"
          && result.native_delivery === null;
        await this.finishKnownNonDelivery(refreshable, "action_not_applied");
      }
      return { type: "text_not_applied", decision, result, status: this.status() };
    }
    this.consecutiveStaleSubmissions = 0;
    if (action.effect_domain === "text_menu") {
      if (result.native_delivery !== null || !result.successor || !this.validTextMenuSuccessor(previous, result.successor, true)) {
        await this.taint("menu_navigation_successor_invalid");
        return { type: "unknown", decision, receipt: result, error: "menu_navigation_successor_invalid", status: this.status() };
      }
      const navigationRecorded = await this.appendEvidence("menu_navigation", { decision_id: decision.decision_id, action_id: action.action_id, result });
      if (!navigationRecorded) await this.taintWithoutEvidence("agent_evidence_write_failed_after_submit");
      else this.rememberConfirmedInteraction(decision, action, requestId, previous, submissionEpoch);
      this.setObservedSnapshot(result.successor);
      if (this.mode === "one_step") await this.completeOneStep();
      return { type: "navigated", decision, action, result, successor: result.successor, status: this.status() };
    }
    this.lastReceipt = { request_id: result.request_id, delivery: "delivered", reason_code: result.reason_code, successor_snapshot_id: result.successor?.snapshot_id ?? null };
    const deliveryRecorded = await this.appendEvidence("text_native_delivery", { decision_id: decision.decision_id, result });
    if (!deliveryRecorded) await this.taintWithoutEvidence("agent_evidence_write_failed_after_submit");
    else this.rememberConfirmedInteraction(decision, action, requestId, previous, submissionEpoch);
    try {
      const successor = await this.stableSuccessor(previous);
      if (this.mutationCancellationRequested()) {
        if (await this.finishAutonomyBudgetHandoffIfRequested()) return { type: "not_admitted", reason: "autonomy_budget_exhausted", status: this.status() };
        return { type: "not_admitted", reason: "recovery_requested_after_delivery", status: this.status() };
      }
      if (!successor || !isTextMenuSnapshot(successor) || !this.validTextMenuSuccessor(previous, successor, false)) {
        await this.taint("text_native_successor_not_stable");
        return { type: "unknown", decision, receipt: result, error: "text_native_successor_not_stable", status: this.status() };
      }
      this.lastReceipt = { ...this.lastReceipt!, successor_snapshot_id: successor.snapshot_id };
      if (!(await this.appendEvidence("text_observed_successor", { decision_id: decision.decision_id, successor }))) await this.taintWithoutEvidence("agent_evidence_write_failed_after_successor");
      if (this.mode === "one_step") await this.completeOneStep();
      return { type: "text_native_delivered", decision, action, result, successor, status: this.status() };
    } catch (error) {
      await this.taint(`unknown_successor_after_submit:${message(error)}`);
      return { type: "unknown", decision, receipt: result, error: message(error), status: this.status() };
    }
  }

  private rememberConfirmedInteraction(decision: PolicyDecision, action: TextAction, requestId: string, previous: TextSnapshot, submissionEpoch: number): void {
    if (!this.interactionPort || submissionEpoch !== this.recoveryEpoch || this.mutationCancellationRequested()
        || this.tainted || this.mode === "human") return;
    this.confirmedInteraction = { value: {
      decision_id: decision.decision_id, snapshot_id: previous.snapshot_id,
      candidate_digest: decision.candidate_digest, action_id: action.action_id,
      request_id: requestId, effect_domain: action.effect_domain,
      result_kind: action.effect_domain === "text_menu" ? "menu_applied" : "native_input_delivered"
    }, sequence: previous.sequence, epoch: submissionEpoch };
  }

  private validTextMenuSuccessor(previous: TextSnapshot, next: TextSnapshot, navigation: boolean): boolean {
    return next.schema === previous.schema && next.input_profile === previous.input_profile
      && next.snapshot_id !== previous.snapshot_id && next.sequence > previous.sequence
      && next.session.runtime_instance_id === previous.session.runtime_instance_id
      && next.session.environment_fingerprint === previous.session.environment_fingerprint
      && (!navigation || next.menu.cursor !== previous.menu.cursor || next.menu.revision > previous.menu.revision);
  }

  private setObservedSnapshot(observed: AnyDecisionBundle["observation"]): void {
    this.lastSnapshotId = observed.snapshot_id;
    this.lastSnapshot = { snapshot_id: observed.snapshot_id, sequence: observed.sequence, status: observed.status, runtime_instance_id: observed.session.runtime_instance_id, environment_fingerprint: observed.session.environment_fingerprint };
  }

  private async finishKnownNonDelivery(refreshable: boolean, handoffReason: string): Promise<void> {
    if (!refreshable || ++this.consecutiveStaleSubmissions >= 3)
      await this.releaseControllerAndReturnHuman(handoffReason);
    else await this.releaseController();
  }

  async stop(): Promise<RuntimeStatus> {
    this.advanceRecoveryEpoch();
    this.resetInteractionContinuity();
    this.stopRequested = true;
    this.cancelActivePolicy();
    return this.serialize(async () => {
      if (this.stopped) return this.status();
      if (this.autonomyBudgetState.state === "active") this.endAutonomyBudget("stopped");
      await this.releaseController();
      if (!(await this.appendEvidence("stopped", { autonomy_budget: this.autonomyBudgetStatus(), controller: this.controllerStatus() }))) {
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

  private async acquireController(): Promise<void> {
    if (this.controllerReleaseUnconfirmed) throw new Error("controller_release_unconfirmed");
    if (this.held) return;
    try {
      const confirmation = await this.options.connector.acquireController();
      const payload = this.controlEvidence(confirmation, "held");
      this.held = true;
      if (!(await this.appendEvidence("controller_acquired", payload))) throw new Error("Agent evidence failed after controller acquisition");
    } catch (error) {
      // A Managed claim may have reached Host before a malformed or lost reply.
      // No later Runtime action may infer that the service is released.
      if (isManagedManifest(this.options.manifest)) this.controllerReleaseUnconfirmed = true;
      throw error;
    }
  }
  private async releaseController(): Promise<void> {
    if (!this.held) return;
    if (this.controllerReleaseUnconfirmed) throw new Error("controller_release_unconfirmed");
    let releasePayload: Record<string, unknown>;
    try {
      const confirmation = await this.options.connector.releaseController();
      releasePayload = this.controlEvidence(confirmation, "released");
    } catch (error) {
      const reason = `controller_release_failed:${message(error)}`;
      this.controllerReleaseUnconfirmed = true;
      if (this.tainted) {
        this.errors = [...this.errors, reason].slice(-20);
        this.invalidations = [...this.invalidations, reason].slice(-20);
      } else {
        await this.taintWithoutEvidence(reason, false);
      }
      await this.appendEvidence("controller_release_failed", { reason });
      await this.appendEvidence("runtime_tainted", { reason, retry: false });
      throw error;
    }
    this.held = false;
    if (!(await this.appendEvidence("controller_released", releasePayload))) {
      const reason = "agent_evidence_controller_release_write_failed";
      if (this.tainted) {
        this.errors = [...this.errors, reason].slice(-20);
        this.invalidations = [...this.invalidations, reason].slice(-20);
      } else {
        await this.taintWithoutEvidence(reason, false);
      }
      await this.appendEvidence("runtime_tainted", { reason, retry: false });
      throw new Error(reason);
    }
  }
  private controlEvidence(confirmation: void | ManagedControlConfirmation,
                          expected: "held" | "released"): Record<string, unknown> {
    if (!isManagedManifest(this.options.manifest)) {
      if (confirmation !== undefined) throw new Error("Connector control returned a Managed confirmation");
      return {};
    }
    const environment = this.environment;
    if (!confirmation || !environment || !("kind" in environment) || environment.kind !== "managed_text_v2"
        || confirmation.status !== expected
        || confirmation.service_instance_id !== environment.service_instance_id
        || confirmation.runtime_instance_id !== environment.runtime_instance_id
        || confirmation.game_continuity_id !== environment.game_continuity_id
        || typeof confirmation.control_epoch !== "string" || !confirmation.control_epoch) {
      throw new Error("Managed control confirmation differs from admitted environment");
    }
    return { status: expected, service_instance_id: confirmation.service_instance_id,
      runtime_instance_id: confirmation.runtime_instance_id,
      game_continuity_id: confirmation.game_continuity_id,
      control_epoch: confirmation.control_epoch };
  }
  private async releaseControllerAndReturnHuman(reason = "auto_surface_not_admitted"): Promise<void> { this.resetInteractionContinuity(); this.mode = "human"; this.endAutonomyBudget("mode_changed"); await this.releaseController(); await this.appendEvidence("handoff_to_human", { reason }); }
  private async completeOneStep(): Promise<void> { this.resetInteractionContinuity(); this.mode = "human"; this.endAutonomyBudget("mode_changed"); await this.releaseController(); await this.appendEvidence("one_step_completed", { autonomy_budget: this.autonomyBudgetStatus() }); }
  private async failClosed(reason: string): Promise<void> { this.resetInteractionContinuity(); this.errors = [...this.errors, reason].slice(-20); this.invalidations = [...this.invalidations, reason].slice(-20); this.mode = "human"; this.endAutonomyBudget("mode_changed"); await this.releaseController(); await this.appendEvidence("fail_closed", { reason }); }
  private async taint(reason: string): Promise<void> { await this.taintWithoutEvidence(reason); await this.appendEvidence("runtime_tainted", { reason, retry: false }); }
  private async taintWithoutEvidence(reason: string, release = true): Promise<void> { this.resetInteractionContinuity(); this.tainted = true; this.taintReason = reason; this.errors = [...this.errors, reason].slice(-20); this.invalidations = [...this.invalidations, reason].slice(-20); this.mode = "human"; this.endAutonomyBudget("mode_changed"); if (release) try { await this.releaseController(); } catch { /* retain held state; a failed release is not confirmation */ } }
  private async appendEvidence(kind: string, payload: Record<string, unknown>): Promise<boolean> { try { await this.options.evidence?.append(kind, payload, this.now()); return true; } catch (error) { const reason = `agent_evidence_write_failed:${message(error)}`; this.errors = [...this.errors, reason].slice(-20); this.invalidations = [...this.invalidations, reason].slice(-20); return false; } }

  private beginAutonomyBudget(): void {
    this.clearAutonomyBudgetDeadline();
    this.nativeSubmissionsUsed = 0;
    this.menuNavigationsUsed = 0;
    this.autonomyBudgetGeneration += 1;
    this.autonomyBudgetHandoffQueued = false;
    this.autonomyBudgetRecoveryFenced = false;
    this.autonomyBudgetHandoffFinished = false;
    this.autonomyBudgetState = {
      state: "active",
      submissionsUsed: 0,
      policyCallsUsed: 0,
      startedAt: this.monotonicNow(),
      elapsedMs: 0,
      exhaustedReason: null,
      endedReason: null
    };
    this.scheduleAutonomyBudgetDeadline();
  }

  private endAutonomyBudget(reason: AutonomyBudgetEndReason): void {
    if (this.autonomyBudgetState.state === "active") {
      this.autonomyBudgetState.elapsedMs = this.budgetElapsedMs();
      this.autonomyBudgetState.startedAt = null;
      this.autonomyBudgetState.state = "inactive";
      this.autonomyBudgetState.endedReason = reason;
    }
    this.clearAutonomyBudgetDeadline();
  }

  private budgetElapsedMs(): number {
    const state = this.autonomyBudgetState;
    if (state.startedAt === null) return state.elapsedMs;
    const current = this.monotonicNow();
    const elapsed = Number.isFinite(current) ? Math.max(0, current - state.startedAt) : state.elapsedMs;
    return Math.max(state.elapsedMs, elapsed);
  }

  private autonomyBudgetStatus(): RuntimeStatus["autonomy_budget"] {
    const state = this.autonomyBudgetState;
    const elapsedMs = this.budgetElapsedMs();
    if (state.state === "active" && state.exhaustedReason === null && elapsedMs >= this.autoBudget.deadlineMs) {
      this.markBudgetExhausted("deadline");
      this.requestAutonomyBudgetHandoff();
    }
    const effectiveElapsed = state.state === "active" ? Math.min(elapsedMs, this.autoBudget.deadlineMs) : Math.min(state.elapsedMs, this.autoBudget.deadlineMs);
    return {
      state: state.state,
      max_submissions: this.autoBudget.maxSubmissions,
      submissions_used: state.submissionsUsed,
      max_policy_calls: this.autoBudget.maxPolicyCalls,
      policy_calls_used: state.policyCallsUsed,
      deadline_ms: this.autoBudget.deadlineMs,
      elapsed_ms: Math.max(0, Math.round(effectiveElapsed)),
      remaining_ms: Math.max(0, this.autoBudget.deadlineMs - Math.round(effectiveElapsed)),
      exhausted_reason: state.exhaustedReason,
      ended_reason: state.endedReason
    };
  }

  private markBudgetExhausted(reason: AutonomyBudgetExhaustionReason): void {
    if (this.autonomyBudgetState.state === "active") {
      this.clearAutonomyBudgetDeadline();
      this.autonomyBudgetState.state = "exhausted";
      this.autonomyBudgetState.exhaustedReason = reason;
      this.autonomyBudgetState.elapsedMs = Math.min(this.budgetElapsedMs(), this.autoBudget.deadlineMs);
      this.autonomyBudgetState.startedAt = null;
    }
  }

  private autonomyBudgetAvailable(): boolean {
    if (this.autonomyBudgetState.state !== "active") return false;
    if (this.budgetElapsedMs() >= this.autoBudget.deadlineMs) { this.markBudgetExhausted("deadline"); return false; }
    if (this.autonomyBudgetState.submissionsUsed >= this.autoBudget.maxSubmissions) { this.markBudgetExhausted("submission_attempt_limit"); return false; }
    if (this.autonomyBudgetState.policyCallsUsed >= this.autoBudget.maxPolicyCalls) { this.markBudgetExhausted("policy_call_limit"); return false; }
    return true;
  }

  private consumePolicyCall(): boolean {
    if (!this.autonomyBudgetAvailable()) return false;
    this.autonomyBudgetState.policyCallsUsed += 1;
    return true;
  }

  private consumeSubmissionAttempt(): boolean {
    if (this.autonomyBudgetState.state !== "active") return false;
    if (this.budgetElapsedMs() >= this.autoBudget.deadlineMs) { this.markBudgetExhausted("deadline"); return false; }
    if (this.autonomyBudgetState.submissionsUsed >= this.autoBudget.maxSubmissions) { this.markBudgetExhausted("submission_attempt_limit"); return false; }
    this.autonomyBudgetState.submissionsUsed += 1;
    return true;
  }

  private clearAutonomyBudgetDeadline(): void {
    if (this.autonomyBudgetTimer !== undefined) clearTimeout(this.autonomyBudgetTimer);
    this.autonomyBudgetTimer = undefined;
  }

  private scheduleAutonomyBudgetDeadline(): void {
    if (this.autonomyBudgetState.state !== "active") return;
    const generation = this.autonomyBudgetGeneration;
    const remaining = this.autoBudget.deadlineMs - this.budgetElapsedMs();
    if (remaining <= 0) {
      this.requestAutonomyBudgetHandoff();
      return;
    }
    const timer = setTimeout(() => {
      if (generation !== this.autonomyBudgetGeneration || this.autonomyBudgetState.state !== "active") return;
      this.markBudgetExhausted("deadline");
      this.requestAutonomyBudgetHandoff();
    }, remaining);
    this.autonomyBudgetTimer = timer;
    if (typeof timer === "object" && timer !== null && "unref" in timer && typeof timer.unref === "function") timer.unref();
  }

  private requestAutonomyBudgetHandoff(): void {
    if (this.autonomyBudgetState.state === "active") this.markBudgetExhausted("deadline");
    if (!this.autonomyBudgetRecoveryFenced) {
      this.autonomyBudgetRecoveryFenced = true;
      this.advanceRecoveryEpoch();
      this.resetInteractionContinuity();
      this.cancelActivePolicy();
      this.mode = "human";
    }
    if (this.autonomyBudgetHandoffQueued || this.autonomyBudgetHandoffFinished) return;
    this.autonomyBudgetHandoffQueued = true;
    const generation = this.autonomyBudgetGeneration;
    void this.serialize(() => this.handoffAutonomyBudget(generation)).catch((error: unknown) => {
      const reason = `autonomy_budget_handoff_failed:${message(error)}`;
      this.errors = [...this.errors, reason].slice(-20);
      this.invalidations = [...this.invalidations, reason].slice(-20);
    });
  }

  private async handoffAutonomyBudget(expectedGeneration = this.autonomyBudgetGeneration): Promise<void> {
    if (expectedGeneration !== this.autonomyBudgetGeneration || this.autonomyBudgetHandoffFinished) return;
    const reason = this.autonomyBudgetState.exhaustedReason ?? "deadline";
    if (this.autonomyBudgetState.state === "active") this.markBudgetExhausted(reason);
    this.requestAutonomyBudgetHandoff();
    try { await this.releaseController(); } catch { /* releaseController records and taints its exact failure */ }
    const recorded = await this.appendEvidence("autonomy_budget_exhausted", { reason, budget: this.autonomyBudgetStatus(), controller: this.controllerStatus() });
    if (!recorded && !this.tainted) {
      const failure = "autonomy_budget_exhaustion_evidence_write_failed";
      await this.taintWithoutEvidence(failure, false);
      await this.appendEvidence("runtime_tainted", { reason: failure, retry: false });
    }
    this.autonomyBudgetHandoffFinished = true;
  }

  private async finishAutonomyBudgetHandoffIfRequested(): Promise<boolean> {
    if (!this.autonomyBudgetRecoveryFenced) return false;
    await this.handoffAutonomyBudget();
    return true;
  }
  private async stableSuccessor(previous: AnyDecisionBundle["observation"]): Promise<AnyDecisionBundle["observation"] | null> {
    for (let attempt = 1; attempt <= this.successorPoll.maxAttempts; attempt += 1) {
      if (this.mutationCancellationRequested()) return null;
      // One observation per attempt: do not multiply nested retry budgets.
      const next = await refreshWholeDecisionBundle(this.options.connector, [], { maxAttempts: 1, baseBackoffMs: 0,
        inputProfile: isTextMenuSnapshot(previous) ? previous.input_profile : undefined, sleep: this.sleep });
      if (next) {
        const observed = next.observation;
        if (observed.schema !== previous.schema) throw new Error("successor_profile_drift");
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
      || this.requestedMode === "shadow"
      || this.autonomyBudgetRecoveryFenced;
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

function makeDecision(manifest: PolicyManifest, runId: string, bundle: AnyDecisionBundle, adapter: AdapterDecision, admission: Admission, issuedAt: string): PolicyDecision {
  const decision = { schema: "sts2.policy-runtime/decision-1" as const, decision_id: `decision-${randomUUID()}`, run_id: runId, manifest_id: manifest.manifest_id, snapshot_id: bundle.observation.snapshot_id, candidate_digest: adapter.candidate_digest, candidate_count: admission.candidateCount, scores: [...adapter.scores], selected_index: adapter.selected_index, disposition: adapter.selected_index === null ? "abstain" as const : "admit" as const, issued_at: issuedAt } satisfies PolicyDecision;
  return validatePolicyDecision(decision);
}

function isStale(error: unknown): boolean { return error instanceof StaleWholeBundleError || (error instanceof Error && (error as Error & { code?: string }).code === "stale_state"); }
function isManagedManifest(manifest: PolicyManifest): boolean {
  return "kind" in manifest.requirements.environment
    && manifest.requirements.environment.kind === "managed_text_v2";
}
function isManagedCapabilities(capabilities: Awaited<ReturnType<PolicyConnector["capabilities"]>>): capabilities is ManagedCapabilities {
  return "kind" in capabilities && capabilities.kind === "managed_text_v2";
}
function manifestCompatibilityReason(manifest: PolicyManifest, capabilities: Awaited<ReturnType<PolicyConnector["capabilities"]>>,
                                     managedBindingSha256?: string): string | null {
  if (isManagedManifest(manifest)) {
    if (!isManagedCapabilities(capabilities)) return "managed_environment_required";
    const requirements = manifest.requirements as Extract<PolicyManifest["requirements"], { environment: { kind: "managed_text_v2" } }>;
    const admitted = capabilities.environment;
    if (capabilities.protocol_version !== requirements.environment.text_protocol_version
        || admitted.text_protocol_version !== requirements.environment.text_protocol_version
        || capabilities.input_profile !== "text-menu-v2"
        || capabilities.snapshot_schema !== manifest.representation.input_schema
        || capabilities.receipt_schema !== "sts2.player-environment/text-menu-action-result-2") return "managed_text_profile_unsupported";
    if (admitted.binding_sha256 !== managedBindingSha256) return "managed_binding_drift";
    if ((capabilities.control_held && !capabilities.control_owned)
        || capabilities.tainted || !capabilities.execution_available) return "managed_execution_unavailable";
    if (!manifest.support.game_versions.includes(admitted.game_version)
        || !manifest.support.game_commits.includes(admitted.game_commit)) return "game_identity_unsupported";
    if (manifest.support.interaction_kinds.some((kind) => !capabilities.interaction_kinds.includes(kind)
        && !capabilities.observed_terminal_kinds.includes(kind))
        || manifest.support.action_verbs.some((verb) => !capabilities.action_verbs.includes(verb))) {
      return "managed_text_vocabulary_unsupported";
    }
    return null;
  }
  if (isManagedCapabilities(capabilities)) return "connector_environment_required";
  const requirements = manifest.requirements as Extract<PolicyManifest["requirements"], { connector_protocol_version: string }>;
  if (capabilities.protocol_version !== requirements.connector_protocol_version) return "connector_protocol_unsupported";
  if (manifest.representation.input_schema !== "sts2.player-environment/snapshot-1") {
    const v2 = manifest.representation.input_schema === "sts2.player-environment/text-menu-snapshot-2";
    if (!("input_profile" in capabilities) || capabilities.input_profile !== (v2 ? "text-menu-v2" : "text-menu-v1")
        || capabilities.snapshot_schema !== manifest.representation.input_schema
        || capabilities.receipt_schema !== (v2 ? "sts2.player-environment/text-menu-action-result-2" : "sts2.player-environment/text-menu-action-result-1")) return "connector_text_menu_profile_unsupported";
  } else if (capabilities.snapshot_schema !== "sts2.player-environment/snapshot-1"
      || capabilities.receipt_schema !== "sts2.player-environment/receipt-1") return "connector_legacy_profile_unsupported";
  const environment = requirements.environment;
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
  if (isManagedCapabilities(capabilities)) return capabilities.environment;
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
function isAutonomyMode(mode: RuntimeMode): boolean { return mode === "auto" || mode === "shadow" || mode === "one_step"; }
function isDrivenMode(mode: RuntimeMode): boolean { return mode === "auto" || mode === "shadow"; }
function normalizeAutonomyBudget(value: Partial<AutonomyBudgetConfig> | undefined): AutonomyBudgetConfig {
  const budget = {
    maxSubmissions: value?.maxSubmissions ?? DEFAULT_AUTONOMY_BUDGET.maxSubmissions,
    maxPolicyCalls: value?.maxPolicyCalls ?? DEFAULT_AUTONOMY_BUDGET.maxPolicyCalls,
    deadlineMs: value?.deadlineMs ?? DEFAULT_AUTONOMY_BUDGET.deadlineMs
  };
  if (!Number.isSafeInteger(budget.maxSubmissions) || budget.maxSubmissions < 1
      || !Number.isSafeInteger(budget.maxPolicyCalls) || budget.maxPolicyCalls < 1
      || !Number.isSafeInteger(budget.deadlineMs) || budget.deadlineMs < 1) {
    throw new Error("autoBudget requires positive safe integer maxSubmissions, maxPolicyCalls and deadlineMs");
  }
  return budget;
}
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
