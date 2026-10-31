import { describe, expect, it } from "vitest";
import { globalRuntimeFixture, GlobalHttpFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";
import { startPolicyRuntimeHttpServer } from "../src/server.js";
import { extractTrajectory } from "../src/p1-global/trajectory.js";

const binding = async (runtime: Awaited<ReturnType<typeof globalRuntimeFixture>>["runtime"]) => {
  const value = await runtime.readEnvironment();
  return { gameInstanceId: value.runtime_instance_id, recoveryEpoch: value.recovery_epoch };
};
describe("manual ranking uses the same Runtime and original Receipt", () => {
  it("reads the whole current catalog without control/inference and executes only its first ranked choice", async () => {
    const f = await globalRuntimeFixture({ autoBudget: { mode: "continuous" } });
    try {
      await f.runtime.setMode("human");
      const view = await f.runtime.readHumanChoices();
      expect(view.ready).toBe(true); expect(f.source.steps).toBe(0); expect(f.source.controlHeld).toBe(false);
      expect((await f.events()).filter(e => e.kind === "global_consumed")).toHaveLength(0);
      const ids = (view.catalog as { choice_id: string }[]).map(c => c.choice_id).reverse();
      expect(ids.length).toBeGreaterThan(1);
      const result = await f.runtime.chooseHuman({ catalog_key: view.catalog_key as string, ranking: ids }, await binding(f.runtime));
      expect(result.type).toBe("delivered"); expect(f.source.steps).toBe(1);
      expect(f.runtime.status().mode).toBe("human"); expect(f.source.controlHeld).toBe(false);
      await f.runtime.stop();
      const events = await f.events();
      expect(events.find(e => e.kind === "human_directive").payload.ranking).toEqual(ids);
      expect(events.filter(e => e.kind === "agent_directive")).toHaveLength(0);
      expect(events.find(e => e.kind === "global_choice").payload.choice_id).toBe(ids[0]);
      expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
      const trajectory = await extractTrajectory(f.evidence.directory);
      expect(trajectory.points[0].chosen).toBe(ids[0]);
      expect(trajectory.points[0].decision_source).toBe("manual_protocol_choice");
    } finally { await f.close(); }
  });
  it("rejects a changed catalog, duplicate ranking and stale recovery epoch without dispatch", async () => {
    const f = await globalRuntimeFixture({ autoBudget: { mode: "continuous" } });
    try {
      await f.runtime.setMode("human");
      const view = await f.runtime.readHumanChoices();
      const id = (view.catalog as any[])[0].choice_id;
      await expect(f.runtime.chooseHuman({ catalog_key: view.catalog_key as string, ranking: [id, id] }, await binding(f.runtime))).rejects.toThrow("invalid_human_selection");
      const result = await f.runtime.chooseHuman({ catalog_key: "0".repeat(64), ranking: [id] }, await binding(f.runtime));
      expect(result.type).toBe("not_admitted"); expect(f.source.steps).toBe(0);
      const old = await binding(f.runtime); await f.runtime.setMode("human");
      await expect(f.runtime.chooseHuman({ catalog_key: view.catalog_key as string, ranking: [id] }, old)).rejects.toThrow("runtime_recovery_epoch_mismatch");
      expect(f.source.steps).toBe(0);
      await f.runtime.stop(); expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
    } finally { await f.close(); }
  });
  it("preserves unknown once and prevents another human submission", async () => {
    const source = new GlobalHttpFixture(); source.delivery = "unknown";
    const f = await globalRuntimeFixture({ source, autoBudget: { mode: "continuous" } });
    try {
      await f.runtime.setMode("human"); const view = await f.runtime.readHumanChoices();
      const value = { catalog_key: view.catalog_key as string, ranking: [(view.catalog as any[])[0].choice_id] };
      expect((await f.runtime.chooseHuman(value, await binding(f.runtime))).type).toBe("unknown");
      await expect(f.runtime.chooseHuman(value, await binding(f.runtime))).rejects.toThrow();
      expect(source.steps).toBe(1); await f.runtime.stop();
      expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(true);
    } finally { await f.close(); }
  });
  it.each(["hp", "energy", "cost"])("rejects old ranking when %s changes with identical legal choices", async fact => {
    const f = await globalRuntimeFixture({ autoBudget: { mode: "continuous" } });
    try {
      await f.runtime.setMode("human");
      const original = await f.runtime.readHumanChoices();
      const snapshot = f.source.basis.snapshot;
      snapshot.snapshot_id += "-changed"; snapshot.sequence++;
      for (const read of f.source.basis.reads)
        read.response.expected_snapshot_id = read.response.observed_snapshot_id = snapshot.snapshot_id;
      const combat = snapshot.interaction.content.context;
      if (fact === "hp") combat.enemies[0].hp--;
      else if (fact === "energy") combat.player.energy--;
      else combat.player.hand[0].cost = "0";
      const fresh = await f.runtime.readHumanChoices();
      expect(fresh.catalog).toEqual(original.catalog);
      expect(fresh.catalog_key).not.toBe(original.catalog_key);
      const result = await f.runtime.chooseHuman({ catalog_key: original.catalog_key as string,
        ranking: [(original.catalog as any[])[0].choice_id] }, await binding(f.runtime));
      expect(result.type).toBe("not_admitted"); expect(f.source.steps).toBe(0);
      await f.runtime.stop();
      expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
    } finally { await f.close(); }
  });
  it("keeps a ranking when only transient snapshot/capture identifiers change", async () => {
    const f = await globalRuntimeFixture({ autoBudget: { mode: "continuous" } });
    try {
      await f.runtime.setMode("human");
      const original = await f.runtime.readHumanChoices();
      const snapshot = f.source.basis.snapshot;
      snapshot.snapshot_id += "-refreshed"; snapshot.sequence++;
      for (const read of f.source.basis.reads)
        read.response.expected_snapshot_id = read.response.observed_snapshot_id = snapshot.snapshot_id;
      expect((await f.runtime.readHumanChoices()).catalog_key).toBe(original.catalog_key);
      expect((await f.runtime.chooseHuman({ catalog_key: original.catalog_key as string,
        ranking: [(original.catalog as any[])[0].choice_id] }, await binding(f.runtime))).type).toBe("delivered");
    } finally { await f.close(); }
  });
});


describe("manual choice HTTP and repeated acknowledged observations", () => {
  it("keeps successive manual choices readable when the semantic observation is unchanged", async () => {
    const source = new GlobalHttpFixture(); source.terminalAfterStep = false;
    const f = await globalRuntimeFixture({ source, autoBudget: { mode: "continuous" } });
    try {
      await f.runtime.setMode("human");
      for (let i = 0; i < 2; i++) {
        const view = await f.runtime.readHumanChoices();
        expect((await f.runtime.chooseHuman({ catalog_key: view.catalog_key as string,
          ranking: [(view.catalog as any[])[1].choice_id] }, await binding(f.runtime))).type).toBe("delivered");
      }
      await f.runtime.stop();
      expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
      expect((await extractTrajectory(f.evidence.directory)).points).toHaveLength(2);
    } finally { await f.close(); }
  });
  it("maps a passive GET and one exact game/run/epoch POST through the real HTTP owner", async () => {
    const f = await globalRuntimeFixture({ autoBudget: { mode: "continuous" } });
    const server = await startPolicyRuntimeHttpServer(f.runtime);
    try {
      await f.runtime.setMode("human");
      const view = await (await fetch(server.address + "/v2/human-choices")).json() as any;
      expect(view.run_id).toBe(f.evidence.runId); expect(view.choices.ready).toBe(true);
      const body = JSON.stringify({ catalog_key: view.choices.catalog_key, ranking: [view.choices.catalog[1].choice_id] });
      const denied = await fetch(server.address + "/v2/human-choice", { method: "POST", headers: { "Content-Type": "application/json" }, body });
      expect(denied.status).toBe(428); expect(f.source.steps).toBe(0);
      const expected = await binding(f.runtime);
      const accepted = await fetch(server.address + "/v2/human-choice", { method: "POST", body, headers: {
        "Content-Type": "application/json", "X-STS2-Policy-Run-ID": f.evidence.runId,
        "X-STS2-Game-Instance-ID": expected.gameInstanceId,
        "X-STS2-Recovery-Epoch": String(expected.recoveryEpoch) } });
      expect(accepted.status).toBe(200);
      const result = await accepted.json() as any;
      expect(result.result.type).toBe("delivered"); expect(result.status.mode).toBe("human");
      expect(f.source.steps).toBe(1);
    } finally { await server.close(); await f.close(); }
  });
});
