import { describe, it, expect } from "vitest";
import { globalRuntimeFixture, GlobalHttpFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";
import { ActionReview } from "../src/action-review.js";
import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

async function until(test: () => boolean) {
  const end = Date.now() + 4000;
  while (!test()) {
    if (Date.now() > end) throw new Error("review did not reach its bounded state");
    await new Promise(r => setTimeout(r, 10));
  }
}
describe("optional review in the original Runtime owner", () => {
  it.each(["delivered", "unknown"])("Pause waits for the already sent original %s Receipt and never replays it", async delivery => {
    const source = new GlobalHttpFixture(); source.terminalAfterStep = false; source.delivery = delivery;
    let entered!: () => void, reply!: () => void;
    const sent = new Promise<void>(r => { entered = r; });
    source.actionEntered = entered; source.actionReplyGate = new Promise<void>(r => { reply = r; });
    const f = await globalRuntimeFixture({ source, autoBudget: { mode: "continuous" } });
    const binding = async () => { const e = await f.runtime.readEnvironment(); return {
      gameInstanceId: e.runtime_instance_id, recoveryEpoch: e.recovery_epoch }; };
    try {
      await f.runtime.setMode("human");
      await f.runtime.controlActionReview!({ enabled: true }, await binding());
      await f.runtime.setMode("auto", await binding());
      const tick = f.runtime.tick();
      await until(() => !!f.runtime.actionReviewStatus!().pending);
      await f.runtime.controlActionReview!({ approve: f.runtime.actionReviewStatus!().pending!.id }, await binding());
      await sent;
      let paused = false;
      const pause = f.runtime.setMode("human").then(() => { paused = true; });
      await new Promise(r => setTimeout(r, 30));
      expect(paused).toBe(false); expect(source.steps).toBe(1);
      reply(); await tick; await pause;
      expect(f.runtime.status().last_result?.delivery).toBe(delivery);
      await f.runtime.tick(); expect(source.steps).toBe(1);
      await f.runtime.stop();
      expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(delivery === "unknown");
    } finally { reply(); await f.close(); }
  });
  it("cancels an unsent choice, resumes repeatedly and keeps receipts bound to the original run", async () => {
    const source = new GlobalHttpFixture(); source.terminalAfterStep = false;
    const f = await globalRuntimeFixture({ source, autoBudget: { mode: "continuous" } });
    const binding = async () => { const e = await f.runtime.readEnvironment(); return {
      gameInstanceId: e.runtime_instance_id, recoveryEpoch: e.recovery_epoch }; };
    try {
      await f.runtime.setMode("human");
      await f.runtime.controlActionReview!({ enabled: true }, await binding());
      for (let i = 0; i < 4; i++) {
        await f.runtime.setMode("auto", await binding());
        const tick = f.runtime.tick();
        await until(() => !!f.runtime.actionReviewStatus!().pending);
        const pending = f.runtime.actionReviewStatus!().pending!;
        expect(source.steps).toBe(Math.floor(i / 2));
        if (i % 2 === 0) {
          const pause = f.runtime.setMode("human");
          await tick; await pause;
          await expect(f.runtime.controlActionReview!({ approve: pending.id }, await binding())).rejects.toThrow("action_review_changed");
        } else {
          await f.runtime.controlActionReview!({ approve: pending.id }, await binding());
          expect((await tick).type).toBe("delivered");
          await f.runtime.setMode("human");
        }
      }
      expect(source.steps).toBe(2);
      await f.runtime.stop();
      const events = await f.events();
      expect(events.filter(e => e.kind === "global_control_fence").length).toBeGreaterThanOrEqual(5);
      expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(false);
      if (process.env.STS2_H1_RUNTIME_FIXTURE_OUT) {
        const out = resolve(process.env.STS2_H1_RUNTIME_FIXTURE_OUT);
        await mkdir(out, { recursive: true });
        await cp(f.evidence.directory, join(out, f.evidence.runId), { recursive: true, errorOnExist: true, force: false });
        const manifest = JSON.parse(await readFile(join(f.evidence.directory, "manifest.json"), "utf8"));
        await writeFile(join(out, "expected.json"), JSON.stringify({ schema: "sts2.policy-runtime/agent-session-startup-2",
          run_id: f.evidence.runId, runtime_version: manifest.runtime_version, runtime_code_sha256: manifest.runtime_code_sha256,
          global_runtime_directory: resolve("dist") }));
      }
    } finally { await f.close(); }
  });
  it("does not release a pending choice through a changed epoch or reused approval", async () => {
    const review = new ActionReview(); review.configure(true);
    const abort = new AbortController(), pending = review.wait(7, { choice: "a" }, abort.signal);
    const id = review.status().pending!.id;
    expect(() => review.approve(id, 8)).toThrow("action_review_changed");
    expect(() => review.configure(false)).toThrow("action_review_pending");
    review.approve(id, 7); await pending;
    expect(() => review.approve(id, 7)).toThrow("action_review_changed");
  });
});
