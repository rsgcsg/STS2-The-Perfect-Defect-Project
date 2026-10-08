import { DEFAULT_AUTONOMY_BUDGET, type AutonomyBudgetConfig, type AutonomyBudgetEndReason,
  type AutonomyBudgetExhaustionReason, type RuntimeMode, type RuntimeStatus } from "./contracts.js";

/** Known-unapplied owner fence, shared by every Runtime profile. */
export class RuntimeControlPreconditionError extends Error {
  constructor(readonly code: string, readonly httpStatus: number) { super(code); }
}

export interface RuntimeBudgetState {
  state: RuntimeStatus["autonomy_budget"]["state"];
  submissionsUsed: number; policyCallsUsed: number; startedAt: number | null;
  elapsedMs: number; exhaustedReason: AutonomyBudgetExhaustionReason | null;
  endedReason: AutonomyBudgetEndReason | null;
}

/** One selected profile owns this queue, fence, cancellation scope and finite
 * wallet. Neither profile nor the HTTP server creates another lifecycle owner. */
export class RuntimeLifecycleOwner {
  private operation: Promise<unknown> = Promise.resolve();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private epochExhausted = false;
  epoch = 0;
  generation = 0;
  active: { controller: AbortController } | null = null;
  state: RuntimeBudgetState;
  readonly budget: AutonomyBudgetConfig;

  constructor(value: Partial<AutonomyBudgetConfig> | undefined,
    readonly monotonicNow: () => number, mode: RuntimeMode) {
    this.budget = {
      maxSubmissions: value?.maxSubmissions ?? DEFAULT_AUTONOMY_BUDGET.maxSubmissions,
      maxPolicyCalls: value?.maxPolicyCalls ?? DEFAULT_AUTONOMY_BUDGET.maxPolicyCalls,
      deadlineMs: value?.deadlineMs ?? DEFAULT_AUTONOMY_BUDGET.deadlineMs
    };
    if (Object.values(this.budget).some(item => !Number.isSafeInteger(item) || item < 1))
      throw new Error("autoBudget requires positive safe integer maxSubmissions, maxPolicyCalls and deadlineMs");
    const started = mode !== "human";
    this.state = { state: started ? "active" : "inactive", submissionsUsed: 0,
      policyCallsUsed: 0, startedAt: started ? monotonicNow() : null,
      elapsedMs: 0, exhaustedReason: null, endedReason: null };
  }

  serialize<T>(operation: () => Promise<T>): Promise<T> {
    const current = this.operation.then(operation, operation);
    this.operation = current.then(() => undefined, () => undefined);
    return current;
  }
  advanceEpoch(): void {
    if (this.epoch === Number.MAX_SAFE_INTEGER) this.epochExhausted = true;
    else this.epoch += 1;
  }
  checkEpoch(expected: number | undefined): void {
    if (expected === undefined) return;
    if (!Number.isSafeInteger(expected) || expected < 0)
      throw new RuntimeControlPreconditionError("runtime_recovery_precondition_required", 428);
    if (this.epochExhausted || expected !== this.epoch)
      throw new RuntimeControlPreconditionError("runtime_recovery_epoch_mismatch", 409);
  }
  cancelActive(reason?: unknown): void {
    if (this.active && !this.active.controller.signal.aborted) this.active.controller.abort(reason);
  }
  begin(onDeadline: () => void): void {
    this.clearDeadline(); this.generation += 1;
    this.state = { state: "active", submissionsUsed: 0, policyCallsUsed: 0,
      startedAt: this.monotonicNow(), elapsedMs: 0, exhaustedReason: null, endedReason: null };
    this.scheduleDeadline(onDeadline);
  }
  end(reason: AutonomyBudgetEndReason): void {
    if (this.state.state === "active") {
      this.state.elapsedMs = this.elapsed(); this.state.startedAt = null;
      this.state.state = "inactive"; this.state.endedReason = reason;
    }
    this.clearDeadline();
  }
  elapsed(): number {
    if (this.state.startedAt === null) return this.state.elapsedMs;
    const current = this.monotonicNow();
    const elapsed = Number.isFinite(current) ? Math.max(0, current - this.state.startedAt) : this.state.elapsedMs;
    return Math.max(this.state.elapsedMs, elapsed);
  }
  exhaust(reason: AutonomyBudgetExhaustionReason): void {
    if (this.state.state !== "active") return;
    this.clearDeadline(); this.state.state = "exhausted"; this.state.exhaustedReason = reason;
    this.state.elapsedMs = Math.min(this.elapsed(), this.budget.deadlineMs); this.state.startedAt = null;
  }
  available(): boolean {
    if (this.state.state !== "active") return false;
    if (this.elapsed() >= this.budget.deadlineMs) { this.exhaust("deadline"); return false; }
    if (this.state.submissionsUsed >= this.budget.maxSubmissions) { this.exhaust("submission_attempt_limit"); return false; }
    if (this.state.policyCallsUsed >= this.budget.maxPolicyCalls) { this.exhaust("policy_call_limit"); return false; }
    return true;
  }
  consumeCall(): boolean {
    if (!this.available()) return false;
    this.state.policyCallsUsed += 1; return true;
  }
  consumeSubmission(): boolean {
    if (this.state.state !== "active") return false;
    if (this.elapsed() >= this.budget.deadlineMs) { this.exhaust("deadline"); return false; }
    if (this.state.submissionsUsed >= this.budget.maxSubmissions) { this.exhaust("submission_attempt_limit"); return false; }
    this.state.submissionsUsed += 1; return true;
  }
  status(onDeadline: () => void): RuntimeStatus["autonomy_budget"] {
    const state = this.state, elapsed = this.elapsed();
    if (state.state === "active" && state.exhaustedReason === null && elapsed >= this.budget.deadlineMs) {
      this.exhaust("deadline"); onDeadline();
    }
    const effective = Math.min(state.state === "active" ? elapsed : state.elapsedMs, this.budget.deadlineMs);
    return { state: state.state, max_submissions: this.budget.maxSubmissions,
      submissions_used: state.submissionsUsed, max_policy_calls: this.budget.maxPolicyCalls,
      policy_calls_used: state.policyCallsUsed, deadline_ms: this.budget.deadlineMs,
      elapsed_ms: Math.max(0, Math.round(effective)),
      remaining_ms: Math.max(0, this.budget.deadlineMs - Math.round(effective)),
      exhausted_reason: state.exhaustedReason, ended_reason: state.endedReason };
  }
  clearDeadline(): void {
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
  }
  scheduleDeadline(onDeadline: () => void): void {
    if (this.state.state !== "active") return;
    const generation = this.generation, remaining = this.budget.deadlineMs - this.elapsed();
    if (remaining <= 0) { onDeadline(); return; }
    const timer = setTimeout(() => {
      if (generation !== this.generation || this.state.state !== "active") return;
      this.exhaust("deadline"); onDeadline();
    }, remaining);
    this.timer = timer;
    timer.unref?.();
  }
}
