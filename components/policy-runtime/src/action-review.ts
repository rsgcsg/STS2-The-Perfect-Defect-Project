import { randomUUID } from "node:crypto";
import { RuntimeControlPreconditionError } from "./runtime-owner.js";

/** Optional application review barrier. No scoring, choosing or game semantics. */
export class ActionReview {
  enabled = false;
  private pending: { id: string; epoch: number; detail: Record<string, unknown>; resolve: () => void } | null = null;
  status(): { enabled: boolean; pending: { id: string; epoch: number; detail: Record<string, unknown> } | null } {
    return { enabled: this.enabled, pending: this.pending ? {
      id: this.pending.id, epoch: this.pending.epoch, detail: structuredClone(this.pending.detail)
    } : null };
  }
  configure(enabled: boolean): void {
    if (this.pending) throw new RuntimeControlPreconditionError("action_review_pending", 409);
    this.enabled = enabled;
  }
  approve(id: string, epoch: number): void {
    const p = this.pending;
    if (!p || p.id !== id || p.epoch !== epoch) throw new RuntimeControlPreconditionError("action_review_changed", 409);
    this.pending = null; p.resolve();
  }
  async wait(epoch: number, detail: Record<string, unknown>, signal: AbortSignal): Promise<void> {
    if (!this.enabled) return;
    signal.throwIfAborted();
    if (this.pending) throw new Error("action_review_overlap");
    const id = `review-${randomUUID()}`;
    await new Promise<void>((resolve, reject) => {
      const abort = () => { if (this.pending?.id === id) this.pending = null; reject(signal.reason); };
      this.pending = { id, epoch, detail: structuredClone(detail), resolve: () => {
        signal.removeEventListener("abort", abort); resolve();
      } };
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
    signal.throwIfAborted();
  }
}
