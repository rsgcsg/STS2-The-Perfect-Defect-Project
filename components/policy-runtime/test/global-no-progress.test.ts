import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { createSession, reduce, type P1Observation } from "@rsgcsg/sts2-connector-client/p1-global";
import { GlobalNoProgressWindow } from "../src/global-no-progress.js";
import { globalRuntimeFixture, GlobalHttpFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";
const basis = JSON.parse(readFileSync(new URL("../../connector/sdk/typescript/test/fixtures/p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));
const observation = (): any => reduce(createSession("synthetic", "reference"), basis).observation;
describe("opt-in GLOBAL technical repetition stop", () => {
  it("recognizes a bounded two-state cycle without relying on c9 and resets repetition counts for new public progress", () => {
    const a = observation(), b = structuredClone(a);
    (b.player.content as any).hp -= 1;
    const rest = { choice_id: "c1", kind: "rest.upgrade", subject: null, target: null, label: "Smith", mapped: true };
    const finish = { choice_id: "c9", kind: "upgrade.finish", subject: null, target: null, label: "Finish selection", mapped: true };
    a.catalog = [rest]; b.catalog = [finish];
    const w = new GlobalNoProgressWindow({ windowDecisions: 8, maxRepeats: 3 });
    for (const state of [a, b, a, b, a]) expect(w.check(state, state.catalog[0]!).exhausted).toBe(false);
    expect(w.check(b, finish)).toMatchObject({ exhausted: true, repeats: 3, novel_state: false });
    const progress = structuredClone(a); (progress.player.content as any).hp = 20;
    expect(w.check(progress, rest)).toMatchObject({ exhausted: false, repeats: 1, novel_state: true });
    expect(w.check(b, finish)).toMatchObject({ exhausted: false, repeats: 1 });
    for (const change of [
      (o: any) => { o.deck.content.cards[0].is_upgraded = !o.deck.content.cards[0].is_upgraded; },
      (o: any) => { o.map.content.nodes[0].state = o.map.content.nodes[0].state === "traveled" ? "travelable" : "traveled"; },
      (o: any) => { o.deck.content.cards[0].description += " changed public detail"; }
    ]) {
      const advanced = structuredClone(a); change(advanced);
      expect(w.check(advanced, rest)).toMatchObject({ novel_state: true, exhausted: false, repeats: 1 });
    }
    const reordered = structuredClone(a); reordered.envelope.occurrence += 100;
    reordered.envelope.source_sequence += 100; reordered.catalog[0]!.choice_id = "c77";
    const identityWindow = new GlobalNoProgressWindow({ windowDecisions: 4, maxRepeats: 2 });
    identityWindow.check(a, rest);
    expect(identityWindow.check(reordered, reordered.catalog[0]!)).toMatchObject({ novel_state: false, exhausted: true });
    const changed = structuredClone(reordered); changed.player.content.hp = 99;
    expect(identityWindow.check(changed, rest)).toMatchObject({ novel_state: true, exhausted: false });
  });
  it("uses owned Stop to release control, seal evidence and never submit the repeated choice", async () => {
    const source = new GlobalHttpFixture(); source.terminalAfterStep = false;
    const f = await globalRuntimeFixture({ source, globalNoProgress: { windowDecisions: 4, maxRepeats: 2 } });
    try {
      expect((await f.runtime.tick()).type).toBe("delivered");
      const stopped = await f.runtime.tick();
      expect(stopped).toMatchObject({ type: "not_admitted", reason: "no_progress", status: { lifecycle: "stopped", controller: "released", mode: "human", tainted: false } });
      expect(source.steps).toBe(1); expect(source.controlHeld).toBe(false);
      expect((await replayGlobalAgentRun(f.evidence.directory))).toMatchObject({ verdict: "verified", technical_stop: "no_progress", results: 1 });
    } finally { await f.close(); }
  });
});
