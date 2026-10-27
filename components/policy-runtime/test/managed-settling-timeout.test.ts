import { readFileSync } from "node:fs";
import { it, expect } from "vitest";
import { PlayerEnvironmentRestClient } from "@rsgcsg/sts2-connector-client";
import { ReferenceGlobalTransport, type GlobalTransport } from "../src/global-transport.js";
import { GlobalHttpFixture, globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";

// Real SDK HTTP, evidence, NDJSON child and Runtime. Only native facts and
// the monotonic clock are synthetic; this does not replay M051's game offer.
function managed(source: GlobalHttpFixture): GlobalTransport {
  let delegate: ReferenceGlobalTransport;
  return {
    kind: "managed",
    async negotiate(signal) {
      delegate ??= new ReferenceGlobalTransport(new PlayerEnvironmentRestClient(source.address, 2000),
        { productVersion: "synthetic", clientInstanceId: "synthetic" });
      return { ...await delegate.negotiate(signal), kind: "managed" };
    },
    capture: options => delegate.capture(options), step: (input, options) => delegate.step(input, options),
    acquire: signal => delegate.acquire(signal), controlStatus: () => delegate.controlStatus(),
    release: () => delegate.release(), close: () => delegate.close()
  };
}
function settling(source: GlobalHttpFixture) {
  const b = structuredClone(source.basis);
  b.snapshot.status = "settling";
  b.snapshot.interaction.stage = "settling";
  b.snapshot.bound_actions.actions = [];
  b.snapshot.bound_actions.materialized_count = b.snapshot.bound_actions.total_count = 0;
  return b;
}
it.each([false, true])("stops unchanged settling before/after delivery (post=%s), seals unknown and releases control", async post => {
  const source = new GlobalHttpFixture();
  if (post) source.nextBases = [settling(source)]; else source.basis = settling(source);
  let now = 0;
  source.captureObserver = () => { now += 50; };
  const f = await globalRuntimeFixture({ source, transport: managed(source),
    managedSettlingTimeoutMs: 100, monotonicNow: () => now });
  try {
    await f.runtime.tick();
    expect(f.runtime.status()).toMatchObject({ tainted: post, controller: "released" });
    expect(JSON.stringify(f.runtime.status())).toContain("managed_settling_stalled");
    expect(source.steps).toBe(Number(post)); // never reoffer the delivered action
    await f.runtime.stop();
    expect(f.runtime.status()).toMatchObject({ lifecycle: "stopped", controller: "released" });
    const events = await f.events();
    expect(events.some(e => e.kind === "global_source_rejected")).toBe(false);
    expect(events.some(e => ["runtime_tainted", "fail_closed"].includes(e.kind)
      && e.payload.reason.includes("managed_settling_stalled"))).toBe(true);
    expect(events.at(-1).kind).toBe("stopped");
    expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
  } finally { await f.close(); }
});
it("resets the settling clock when the native snapshot advances and accepts later readiness", async () => {
  const source = new GlobalHttpFixture(); source.nextBases = [settling(source)];
  let now = 0, captures = 0;
  source.captureObserver = () => {
    now += 60;
    if (source.steps && ++captures < 4) {
      source.basis.snapshot.snapshot_id += "-progress";
      source.basis.snapshot.sequence++;
      for (const r of source.basis.reads) if (r.response)
        r.response.expected_snapshot_id = r.response.observed_snapshot_id = source.basis.snapshot.snapshot_id;
    } else if (source.steps) {
      const b = JSON.parse(readFileSync(new URL("../../connector/sdk/typescript/test/fixtures/p1-global/managed-game_over.json", import.meta.url), "utf8"));
      b.snapshot.session = source.basis.snapshot.session;
      for (const r of b.reads) r.response.session = b.snapshot.session;
      source.basis = b;
    }
  };
  const f = await globalRuntimeFixture({ source, transport: managed(source),
    managedSettlingTimeoutMs: 100, monotonicNow: () => now });
  try {
    await f.runtime.tick();
    expect(f.runtime.status().tainted).toBe(false);
    expect(source.steps).toBe(1); expect(captures).toBe(4);
  } finally { await f.close(); }
});
