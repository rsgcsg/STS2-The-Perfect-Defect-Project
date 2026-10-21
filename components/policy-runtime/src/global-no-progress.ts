import { modelView, type P1Observation, type P1Choice } from "@rsgcsg/sts2-connector-client/p1-global";
import type { AgentByteReservation } from "./agent-session-budget.js";
import { canonicalJson } from "./evidence.js";
import { globalDigest } from "./global-transport.js";
import { comparePublicPoint } from "./p1-global/compare.js";

export interface GlobalNoProgressConfig { windowDecisions: number; maxRepeats: number }
export interface GlobalProgressCheck { state_sha256: string; choice_sha256: string; repeats: number; novel_state: boolean; exhausted: boolean }
interface Point { observation: P1Observation; choice: P1Choice; state: string; key: string; reservation?: AgentByteReservation }
/** Opt-in technical repetition budget. Reuse the protocol's existing complete
 * public graph comparison: one injective entity/choice mapping and only declared
 * inventories as multisets. Incomparable never counts as a repeated state.
 * modelView removes exactly the existing binding envelope; no public field is
 * erased here. Hashes identify the first retained representative, not proof. */
export class GlobalNoProgressWindow {
  private history: Point[] = [];
  private counts = new Map<string, number>();
  private last: GlobalProgressCheck | null = null;
  status() { return { ...this.config, last: this.last ? { ...this.last } : null }; }
  constructor(readonly config: GlobalNoProgressConfig, private readonly budget?: { reserve(bytes: number): AgentByteReservation }) {
    if (!Number.isSafeInteger(config.windowDecisions) || config.windowDecisions < 2 || config.windowDecisions > 256
      || !Number.isSafeInteger(config.maxRepeats) || config.maxRepeats < 2 || config.maxRepeats > config.windowDecisions)
      throw new Error("global_no_progress_config");
  }
  close(): void {
    for (const point of new Set(this.history)) point.reservation?.release();
    this.history = []; this.counts.clear();
  }
  check(observation: P1Observation, choice: P1Choice): GlobalProgressCheck {
    const visited = new Set<string>();
    const state = this.history.find(item => {
      if (visited.has(item.state)) return false;
      visited.add(item.state);
      return item.observation.scene.native_kind === observation.scene.native_kind
        && item.observation.status === observation.status
        && comparePublicPoint(item.observation, observation, null, null).verdict === "equal";
    });
    const point = state && this.history.find(item => item.state === state.state
      && comparePublicPoint(item.observation, observation, item.choice.choice_id, choice.choice_id).verdict === "equal");
    const stateSha = state?.state ?? globalDigest(modelView(observation));
    const choiceSha = point?.key ?? globalDigest({ observation: modelView(observation), chosen: choice.choice_id });
    const novel = !state;
    // A new complete public graph clears repetition counts while retaining the
    // bounded known-state window: alternating A/B states remain detectable.
    if (novel) this.counts.clear();
    this.history.push(point ?? { observation, choice, state: stateSha, key: choiceSha,
      ...(this.budget ? { reservation: this.budget.reserve(Buffer.byteLength(canonicalJson(observation))) } : {}) });
    const repeats = (this.counts.get(choiceSha) ?? 0) + 1; this.counts.set(choiceSha, repeats);
    if (this.history.length > this.config.windowDecisions) {
      const old = this.history.shift()!;
      if (!this.history.includes(old)) old.reservation?.release();
      const retained = Math.min(this.counts.get(old.key) ?? 0, this.history.filter(item => item.key === old.key).length);
      if (retained) this.counts.set(old.key, retained); else this.counts.delete(old.key);
    }
    this.last = { state_sha256: stateSha, choice_sha256: choiceSha, repeats, novel_state: novel, exhausted: repeats >= this.config.maxRepeats };
    return { ...this.last };
  }
}
