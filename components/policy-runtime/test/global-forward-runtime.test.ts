import { describe, it, expect } from "vitest";
import { cp, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { globalRuntimeFixture, GlobalHttpFixture } from "./global-runtime-fixtures.js";
import { rewardForwardPair } from "../../connector/sdk/typescript/test/fixtures/reward-forward.mjs";
import { replayGlobalAgentRun } from "../src/global-replay.js";

function gate() { let release!: () => void; return { promise: new Promise<void>(r => { release = r; }), get release() { return release; } }; }
const bounded = <T>(p: Promise<T>) => Promise.race([p, new Promise<never>((_, reject) => {
  const timer = setTimeout(() => reject(new Error("forward fixture timeout")), 5000); timer.unref();
})]);
function rewardSource() {
  const source = new GlobalHttpFixture(), pair = rewardForwardPair();
  source.basis = pair.outer; source.nextBases = [pair.child];
  return source;
}
describe("forward intention through actual HTTP, NDJSON and original replay", () => {
  it("executes the chosen exact card as two steps with one Agent choice and proves the complete prefix", async () => {
    const f = await globalRuntimeFixture({ source: rewardSource(), behavior: "choose:reward.take_card" });
    try {
      expect((await f.runtime.tick()).type).toBe("delivered");
      expect(f.source.steps).toBe(2); expect((await f.runtime.tick()).type).toBe("closed");
      const rows = await f.events(), result = rows.find(e => e.kind === "global_result").payload.result;
      expect(result.status).toBe("completed"); expect(result.steps.map((s: any) => s.native_verb)).toEqual(["activate", "select"]);
      expect(rows.filter(e => e.kind === "global_choice")).toHaveLength(1);
      expect((await replayGlobalAgentRun(f.evidence.directory)).results).toBe(1);
      // Journal-level semantic falsifiers retain every original artifact. Hash
      // inventory verification is separate; no missing artifact is needed here.
      for (const variant of ["wrong_step", "wrong_occurrence", "fake_commitment", "drop_post_source", "early_completed"]) {
        const dir = join(f.root, variant); await cp(f.evidence.directory, dir, { recursive: true });
        let altered = structuredClone(rows); const r = altered.find(e => e.kind === "global_result").payload.result;
        if (variant === "wrong_step") r.steps[1].native_verb = "end_turn";
        if (variant === "wrong_occurrence") r.occurrence++;
        if (variant === "fake_commitment") r.commitment_delta = { reward_page_left: true };
        if (variant === "drop_post_source") altered = altered.filter(e => e.kind !== "global_source_retained");
        if (variant === "early_completed") {
          const firstPost = altered.findIndex(e => e.kind === "global_source_retained");
          const early = structuredClone(altered.find(e => e.kind === "global_result")); early.payload.result.steps = early.payload.result.steps.slice(0, 1);
          altered = [...altered.slice(0, firstPost + 1), early, ...altered.slice(firstPost + 1)];
        }
        await writeFile(join(dir, "events.jsonl"), altered.map((e, i) => JSON.stringify({ ...e, sequence: i + 1 })).join("\n") + "\n");
        await expect(replayGlobalAgentRun(dir), variant).rejects.toThrow();
      }
    } finally { await f.close(); }
  });
  it("Stop between real open and select releases control and never attempts the remaining step", async () => {
    const source = rewardSource(), entered = gate(), release = gate();
    source.postCaptureGate = release.promise; source.postCaptureEntered = entered.release;
    const f = await globalRuntimeFixture({ source, behavior: "choose:reward.take_card" });
    try {
      const tick = f.runtime.tick(); await bounded(entered.promise);
      const stop = f.runtime.stop(); await bounded(tick); await bounded(stop);
      expect(source.steps).toBe(1); expect(source.controlHeld).toBe(false);
      const result = (await f.events()).find(e => e.kind === "global_result").payload.result;
      expect(result).toMatchObject({ status: "interrupted_known", steps: [{ delivery: "delivered" }], commitment_delta: null });
      expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(false);
    } finally { release.release(); await f.close(); }
  });
  it("does not continue after full successor information changes even when the same card/action ids remain", async () => {
    const source = rewardSource(); source.nextBases[0].snapshot.interaction.content.surface.cards[0].description += " newly changed";
    const f = await globalRuntimeFixture({ source, behavior: "choose:reward.take_card" });
    try {
      expect((await f.runtime.tick()).type).toBe("not_delivered"); expect(source.steps).toBe(1);
      await f.runtime.stop();
      expect((await f.events()).find(e => e.kind === "global_result").payload.result.status).toBe("interrupted_known");
      expect((await replayGlobalAgentRun(f.evidence.directory)).results).toBe(1);
    } finally { await f.close(); }
  });
});
