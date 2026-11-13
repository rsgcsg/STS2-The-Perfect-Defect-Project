import type { LinkReply, Sampling } from "./contracts.js";

/** The row as the environment sent it; the reply must name the same triple. */
export interface PendingRow {
  row_id: number; game_instance_id: string; catalog_sha256: string; catalog_len: number; in_combat: boolean;
}

export type BindingVerdict =
  | { verdict: "accept"; index: number }
  | { verdict: "not_started"; reason: "refused" | "response_binding_mismatch"; detail: string };

/**
 * Environment-side check before a catalog position becomes a BoundAction
 * (rl-inference-4 binding rules, mirrored from the WP0 reference). Any failure =
 * no native action, the game is closed, never retried. Late replies for closed
 * rows never reach here (the link discards and counts them).
 */
export class ResponseBinding {
  /** segment_id -> mu digest; a segment never changes its mu. Bounded, oldest evicted. */
  #segments = new Map<string, string>();
  constructor(private readonly maxSegments = 4096) {}

  check(row: PendingRow, reply: LinkReply, sampling: Sampling): BindingVerdict {
    const mismatch = (detail: string): BindingVerdict => ({ verdict: "not_started", reason: "response_binding_mismatch", detail });
    if (reply.row_id !== row.row_id || reply.game_instance_id !== row.game_instance_id || reply.catalog_sha256 !== row.catalog_sha256)
      return mismatch("identity_triple");
    if (reply.type === "refused") return { verdict: "not_started", reason: "refused", detail: reply.error_code };
    const q = reply.behavior_q;
    if (q.length !== row.catalog_len) return mismatch("behavior_q_length");
    if (reply.selected_index < 0 || reply.selected_index >= q.length) return mismatch("selected_index");
    let sum = 0;
    for (const value of q) sum += value;
    if (!(Math.abs(sum - 1) <= 1e-6)) return mismatch("behavior_q_sum");
    if (q[reply.selected_index] !== reply.selected_q) return mismatch("selected_q");
    const known = this.#segments.get(reply.segment_id);
    if (known !== undefined && known !== reply.mu_snapshot_sha256) return mismatch("segment_mu");
    const decisionClass = row.in_combat ? "combat" : "noncombat";
    const expected = sampling.classes[decisionClass];
    const applied = reply.applied;
    if (applied.arm_id !== sampling.arm_id || applied.decision_class !== decisionClass
        || applied.temperature !== expected.temperature || applied.uniform_mix !== expected.uniform_mix) return mismatch("applied");
    if (known === undefined) {
      this.#segments.set(reply.segment_id, reply.mu_snapshot_sha256);
      if (this.#segments.size > this.maxSegments) this.#segments.delete(this.#segments.keys().next().value!);
    }
    return { verdict: "accept", index: reply.selected_index };
  }
}
