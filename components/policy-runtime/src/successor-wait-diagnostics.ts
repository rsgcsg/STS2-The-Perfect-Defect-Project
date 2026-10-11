import type { AnyDecisionBundle } from "./contracts.js";
import type { Writable } from "node:stream";

type Observation = AnyDecisionBundle["observation"];
export type SuccessorWaitCategory = "same_basis" | "settling" | "accepted" | "no_whole_bundle"
  | "cancelled" | "profile_drift" | "environment_identity_drift" | "sequence_not_newer" | "acquisition_error" | "wait_error";
export interface SuccessorWaitPollDiagnostic {
  attempt: number;
  elapsed_ms: number | null;
  snapshot_id: string | null;
  sequence: number | null;
  status: string | null;
  interaction_kind: string | null;
  completeness: string | null;
  same_basis: boolean | null;
  settling: boolean | null;
  category: SuccessorWaitCategory;
  fields_omitted: boolean;
}
export interface SuccessorWaitFailureDiagnostic {
  diagnostic: "successor_wait_failure";
  run_id: string | null;
  request_id: string | null;
  decision_id: string | null;
  basis_snapshot_id: string | null;
  basis_sequence: number;
  fields_omitted: boolean;
  outcome: "exhausted" | "cancelled" | "error";
  elapsed_ms: number | null;
  poll_count: number;
  max_attempts: number;
  interval_ms: number;
  rows_total: number;
  rows_dropped: number;
  truncated: boolean;
  polls: SuccessorWaitPollDiagnostic[];
}
export type SuccessorWaitDiagnostic = SuccessorWaitFailureDiagnostic | {
  diagnostic: "successor_wait_diagnostics_limit";
  failure_records_emitted: 16;
};
export type SuccessorWaitDiagnosticSink = (record: SuccessorWaitDiagnostic) => void;

const MAX_ROWS = 64;
const MAX_RECORD_BYTES = 32 * 1024;
const MAX_FAILURE_RECORDS = 16;

// Bound the serialized value as well as UTF-8: control characters cannot amplify
// a short opaque ID into an unbounded stderr line. Never retain an omitted value.
function bounded(value: string, limit: number): string | null {
  return value.length <= limit && Buffer.byteLength(JSON.stringify(value), "utf8") <= limit ? value : null;
}
function elapsed(clock: () => number, started: number): number | null {
  const value = clock() - started;
  return Number.isFinite(value) ? Math.max(0, value) : null;
}

/** Only the generic successor wait uses this private diagnostic seam. */
export class SuccessorWaitDiagnostics {
  private disabled = false;
  private emitted = 0;
  private capReported = false;
  private pending: SuccessorWaitFailureDiagnostic | undefined;
  constructor(private readonly sink: SuccessorWaitDiagnosticSink, private readonly clock: () => number) {}

  begin(context: { runId: string; requestId: string; decisionId: string; previous: Observation;
    maxAttempts: number; intervalMs: number }): SuccessorWaitTrace | undefined {
    if (this.disabled || this.capReported) return undefined;
    try { return new SuccessorWaitTrace(context, this.clock, record => { this.pending = record; }); }
    catch { this.disabled = true; return undefined; }
  }

  // Flush at the end of tick, after its existing recovery/taint path. A sink is
  // synchronous and cannot add an awaited operation to the controller lifecycle.
  flush(): void {
    const record = this.pending; this.pending = undefined;
    if (!record || this.disabled) return;
    try {
      if (this.emitted < MAX_FAILURE_RECORDS) { this.sink(record); this.emitted += 1; }
      else if (!this.capReported) {
        this.capReported = true;
        this.sink({ diagnostic: "successor_wait_diagnostics_limit", failure_records_emitted: 16 });
      }
    } catch { this.disabled = true; }
  }
}

class SuccessorWaitTrace {
  private readonly started: number;
  private readonly record: SuccessorWaitFailureDiagnostic;
  private failed = false;
  constructor(context: { runId: string; requestId: string; decisionId: string; previous: Observation;
    maxAttempts: number; intervalMs: number }, private readonly clock: () => number,
    private readonly queue: (record: SuccessorWaitFailureDiagnostic) => void) {
    this.started = clock();
    const values = [context.runId, context.requestId, context.decisionId, context.previous.snapshot_id].map(value => bounded(value, 256));
    this.record = { diagnostic: "successor_wait_failure", run_id: values[0]!, request_id: values[1]!,
      decision_id: values[2]!, basis_snapshot_id: values[3]!, basis_sequence: context.previous.sequence,
      fields_omitted: values.some(value => value === null), outcome: "exhausted", elapsed_ms: null,
      poll_count: 0, max_attempts: context.maxAttempts, interval_ms: context.intervalMs,
      rows_total: 0, rows_dropped: 0, truncated: false, polls: [] };
  }
  pollStarted(): void { this.record.poll_count += 1; }
  row(attempt: number, category: SuccessorWaitCategory, observed?: Observation, sameBasis?: boolean): void {
    if (this.failed) return;
    try {
      const values = observed ? [bounded(observed.snapshot_id, 256), bounded(observed.status, 64),
        bounded(observed.interaction.kind, 64), bounded(observed.completeness.status, 64)] : [null, null, null, null];
      this.record.polls.push({ attempt, elapsed_ms: elapsed(this.clock, this.started), snapshot_id: values[0]!,
        sequence: observed?.sequence ?? null, status: values[1]!, interaction_kind: values[2]!, completeness: values[3]!,
        same_basis: observed ? sameBasis ?? false : null, settling: observed ? observed.status === "settling" : null,
        category, fields_omitted: Boolean(observed && values.some(value => value === null)) });
      this.record.rows_total += 1;
      if (this.record.polls.length > MAX_ROWS) this.record.polls.shift();
    } catch { this.failed = true; }
  }
  finish(outcome: "exhausted" | "cancelled" | "error"): void {
    if (this.failed) return;
    try {
      this.record.outcome = outcome;
      this.record.elapsed_ms = elapsed(this.clock, this.started);
      do {
        this.record.rows_dropped = this.record.rows_total - this.record.polls.length;
        this.record.truncated = this.record.rows_dropped > 0;
        if (Buffer.byteLength(JSON.stringify(this.record), "utf8") + 1 <= MAX_RECORD_BYTES) {
          this.queue(this.record); return;
        }
        this.record.polls.shift();
      } while (this.record.polls.length > 0);
    } catch { this.failed = true; }
  }
}

export function successorWaitStderrSink(env: NodeJS.ProcessEnv, stderr: Pick<Writable, "write" | "on">): SuccessorWaitDiagnosticSink | undefined {
  if (env.STS2_POLICY_RUNTIME_SUCCESSOR_DIAGNOSTICS !== "1") return undefined;
  let disabled = false;
  const disable = (): void => { disabled = true; };
  // Created once by the generic CLI. Keep this sole listener until process exit:
  // a write callback runs before its error event, which can also arrive after
  // Runtime Stop. Removing the listener at either boundary would revive EPIPE.
  stderr.on("error", disable);
  return record => {
    if (disabled) return;
    try {
      stderr.write(`${JSON.stringify(record)}\n`, error => { if (error) disable(); });
    } catch { disable(); }
  };
}
