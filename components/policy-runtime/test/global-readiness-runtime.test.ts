import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { GlobalHttpFixture, globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";

// Synthetic transport fixture for the actual Reference occurrence: native
// no_action/settling is observable while its advertised map Read is unavailable.
// It proves Runtime handling, not the native producer's game qualification.
function noInput(source: GlobalHttpFixture, partial: boolean) {
  const b = structuredClone(source.basis);
  b.snapshot.status = "settling";
  b.snapshot.interaction.kind = "no_action";
  b.snapshot.interaction.stage = "settling";
  b.snapshot.interaction.content.surface = { kind: "no_action", reason: "settling", message: "Synthetic native transition" };
  b.snapshot.bound_actions.actions = []; b.snapshot.bound_actions.materialized_count = 0; b.snapshot.bound_actions.total_count = 0;
  if (partial) {
    const r = b.reads.find((r: any) => r.kind === "map");
    r.response = null; r.error = "unsupported";
    b.capture_status = "partial"; b.missing = [`read:${r.read_id}:unsupported`];
  }
  return b;
}
function terminal(source: GlobalHttpFixture) {
  const b = JSON.parse(readFileSync(new URL("../../connector/sdk/typescript/test/fixtures/p1-global/managed-game_over.json", import.meta.url), "utf8"));
  b.snapshot.session = source.basis.snapshot.session;
  for (const r of b.reads) r.response.session = b.snapshot.session;
  return b;
}
function gate() { let release!: () => void; return { promise: new Promise<void>(r => { release = r; }), get release() { return release; } }; }
const within = <T>(promise: Promise<T>) => Promise.race([promise, new Promise<never>((_, reject) => {
  const timer = setTimeout(() => reject(new Error("native readiness fixture stalled")), 5000); timer.unref();
})]);

describe("GLOBAL waits for native readiness, not incomplete-capture grace", () => {
  it.each(["stop", "budget"])("%s preserves capture response ordering when cancellation overtakes its evidence write", async reason => {
    const source = new GlobalHttpFixture(), writing = gate(), finishWrite = gate(), released = gate(), captureSettled = gate(), exhausted = gate();
    source.nextBases = [noInput(source, true)]; source.releaseObserved = released.release;
    const f = await globalRuntimeFixture({ source, budgetMs: 1000 });
    const store = f.evidence.storeGlobalArtifact.bind(f.evidence), capture = f.transport.capture.bind(f.transport), append = f.evidence.append.bind(f.evidence);
    f.evidence.append = async (...args) => {
      await append(...args);
      if (args[0] === "autonomy_budget_exhausted") exhausted.release();
    };
    let original: Buffer | undefined;
    f.evidence.storeGlobalArtifact = async (role, bytes) => {
      if (role === "response" && source.steps && !original
        && JSON.parse(Buffer.from(bytes).toString("utf8")).schema === "sts2.player-environment/public-basis-handle-1") {
        original = Buffer.from(bytes); writing.release(); await finishWrite.promise;
      }
      return store(role, bytes);
    };
    f.transport.capture = async options => {
      try { return await capture(options); }
      finally { if (source.steps) captureSettled.release(); }
    };
    try {
      const tick = f.runtime.tick(); await within(writing.promise);
      const stopping = reason === "stop" ? f.runtime.stop() : undefined;
      // Expiry/Stop releases real control even while original response storage
      // is blocked. The SDK has cancelled the capture, but its hook still runs.
      await within(released.promise); await within(captureSettled.promise);
      expect(source.controlHeld).toBe(false);
      finishWrite.release(); await within(tick); if (stopping) await within(stopping);
      if (reason === "budget") await within(exhausted.promise);
      expect(source.steps).toBe(1); expect(source.releases).toBe(1); expect(source.controlHeld).toBe(false);
      await f.runtime.stop();
      const events = await f.events(), error = events.find(e => e.kind === "global_capture_error");
      const response = events.find(e => e.kind === "global_transport_response" && e.payload.capture_request_id === error.payload.request_id);
      expect(await readFile(join(f.evidence.directory, response.payload.artifact.path))).toEqual(original);
      expect(events.find(e => e.kind === "global_result").payload.result).toMatchObject({
        status: "interrupted_known", steps: [{ delivery: "delivered" }], commitment_delta: null
      });
      expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(false);
      expect(response.sequence).toBeLessThan(error.sequence);
    } finally { finishWrite.release(); await f.close(); }
  });
  it.each([false, true])("keeps one delivered operation pending through native settling (partial=%s)", async partial => {
    const source = new GlobalHttpFixture(), transient = noInput(source, partial), ready = terminal(source);
    let postCaptures = 0; source.nextBases = [transient];
    source.captureObserver = () => { if (source.steps === 1 && ++postCaptures === 3) source.basis = ready; };
    const f = await globalRuntimeFixture({ source });
    try {
      expect((await within(f.runtime.tick())).type).toBe("delivered");
      expect(postCaptures).toBe(3); expect(source.steps).toBe(1);
      expect((await f.runtime.tick()).type).toBe("closed");
      const events = await f.events(); expect(events.filter(e => e.kind === "global_choice")).toHaveLength(1);
      expect(events.filter(e => e.kind === "global_source_retained")).toHaveLength(3);
      expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(false);
    } finally { await f.close(); }
  });
  it("stops on genuine ready-page missing information even if a later read would be complete", async () => {
    const source = new GlobalHttpFixture(), partial = structuredClone(source.basis), later = terminal(source);
    const r = partial.reads[0]; r.response = null; r.error = "unavailable";
    partial.capture_status = "partial"; partial.missing = [`read:${r.read_id}:unavailable`];
    source.nextBases = [partial]; let postCaptures = 0;
    source.captureObserver = () => { if (source.steps === 1 && ++postCaptures === 2) source.basis = later; };
    const f = await globalRuntimeFixture({ source });
    try {
      expect((await within(f.runtime.tick())).type).toBe("not_delivered");
      expect(postCaptures).toBe(1); expect(source.steps).toBe(1); expect(source.controlHeld).toBe(false);
      const result = (await f.events()).find(e => e.kind === "global_result").payload.result;
      expect(result).toMatchObject({ status: "interrupted_known", steps: [{ delivery: "delivered" }], commitment_delta: null });
      await f.runtime.stop(); expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(false);
    } finally { await f.close(); }
  });
  it.each(["stop", "budget"])("%s interrupts a native transition without a second tick or repeated submission", async reason => {
    const source = new GlobalHttpFixture(), entered = gate(); source.nextBases = [noInput(source, true)];
    source.captureObserver = () => { if (source.steps) entered.release(); };
    const f = await globalRuntimeFixture({ source, budgetMs: 1000 });
    try {
      const tick = f.runtime.tick(); await within(entered.promise);
      if (reason === "stop") await within(f.runtime.stop());
      await within(tick);
      expect(source.steps).toBe(1); expect(source.controlHeld).toBe(false);
      const events = await f.events(), result = events.find(e => e.kind === "global_result").payload.result;
      expect(result).toMatchObject({ status: "interrupted_known", steps: [{ delivery: "delivered" }] });
      if (reason === "budget") {
        // Expiry's cleanup writer finishes asynchronously after the aborted tick;
        // observe its original event without a second tick or status-triggered cleanup.
        await within((async () => {
          while (!(await f.events()).some(e => e.kind === "autonomy_budget_exhausted"))
            await new Promise(r => setTimeout(r, 10));
        })());
      }
      await f.runtime.stop(); expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(false);
    } finally { await f.close(); }
  });
});
