import { normalizeAutonomyBudget, type AutonomyBudgetInput, type AutonomyBudgetConfig, type AutonomyBudgetEndReason,
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

/** One selected profile owns this queue, fence, cancellation scope and
 * authorization. Neither profile nor HTTP creates another lifecycle owner. */
export class RuntimeLifecycleOwner {
  private operation: Promise<unknown> = Promise.resolve();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private epochExhausted = false;
  epoch = 0;
  generation = 0;
  active: { controller: AbortController } | null = null;
  state: RuntimeBudgetState;
  readonly budget: AutonomyBudgetConfig;

  constructor(value: AutonomyBudgetInput | undefined,
    readonly monotonicNow: () => number, mode: RuntimeMode) {
    this.budget = normalizeAutonomyBudget(value);
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
    if (this.state.state !== "active" || this.budget.mode === "continuous") return;
    this.clearDeadline(); this.state.state = "exhausted"; this.state.exhaustedReason = reason;
    this.state.elapsedMs = Math.min(this.elapsed(), this.budget.deadlineMs); this.state.startedAt = null;
  }
  available(): boolean {
    if (this.state.state !== "active") return false;
    if (this.budget.mode === "continuous") return true;
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
    if (this.budget.mode === "bounded") {
      if (this.elapsed() >= this.budget.deadlineMs) { this.exhaust("deadline"); return false; }
      if (this.state.submissionsUsed >= this.budget.maxSubmissions) { this.exhaust("submission_attempt_limit"); return false; }
    }
    this.state.submissionsUsed += 1; return true;
  }
  status(onDeadline: () => void): RuntimeStatus["autonomy_budget"] {
    const state = this.state, elapsed = this.elapsed();
    if (this.budget.mode === "continuous") return { mode: "continuous", state: state.state as "active" | "inactive",
      max_submissions: null, submissions_used: state.submissionsUsed, max_policy_calls: null, policy_calls_used: state.policyCallsUsed,
      deadline_ms: null, elapsed_ms: Math.max(0, Math.round(elapsed)), remaining_ms: null, exhausted_reason: null, ended_reason: state.endedReason };
    if (state.state === "active" && state.exhaustedReason === null && elapsed >= this.budget.deadlineMs) {
      this.exhaust("deadline"); onDeadline();
    }
    const effective = Math.min(state.state === "active" ? elapsed : state.elapsedMs, this.budget.deadlineMs);
    return { mode: "bounded", state: state.state, max_submissions: this.budget.maxSubmissions,
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
  scheduleDeadline(onDeadline: () => void,
    local?: { at: number; onDeadline: () => void }): void {
    this.clearDeadline();
    if (this.state.state !== "active") return;
    const generation = this.generation;
    const globalRemaining = this.budget.mode === "bounded" ? this.budget.deadlineMs - this.elapsed() : null;
    if (globalRemaining === null && !local) return;
    const localRemaining = local ? local.at - this.monotonicNow() : null;
    const remaining = globalRemaining === null ? localRemaining! : localRemaining === null ? globalRemaining : Math.min(globalRemaining, localRemaining);
    const expire = () => {
      if (generation !== this.generation || this.state.state !== "active") return;
      // The global wallet keeps priority, including equal absolute deadlines.
      if (this.budget.mode === "bounded" && this.elapsed() >= this.budget.deadlineMs) {
        this.exhaust("deadline"); onDeadline();
      } else if (local && this.monotonicNow() >= local.at) local.onDeadline();
      else this.scheduleDeadline(onDeadline, local);
    };
    if (remaining <= 0) { expire(); return; }
    const timer = setTimeout(expire, remaining);
    this.timer = timer;
    timer.unref?.();
  }
}
