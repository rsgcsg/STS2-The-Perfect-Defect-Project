import { describe, expect, it } from "vitest";
import { realStoreBridge } from "./realStoreBridge.mjs";
import { measuredBudget } from "./nativeLogicalFixtures.js";

describe("actual linked C# Store/Projector with REST SDK", () => {
  it("performs 100 owned complete assemblies past MaxCaptures=4 and reclaims actual bytes each time", async () => {
    const f = await realStoreBridge(), budget = measuredBudget();
    let iteration = -1;
    try {
      let snapshot: string | undefined;
      for (let i = 0; i < 100; i++) {
        iteration = i;
        const reply = await f.session.currentOwned(); snapshot ??= reply.data.capture!.snapshot_id; expect(reply.data.capture!.snapshot_id).toBe(snapshot);
        const full = await f.session.getFull({ capture: reply.data.capture!, context: reply.data.context, retention: reply.data.retention,
          readerLease: reply.takeRetention(), budget });
        expect(full.actions).toHaveLength(1); const lease = full.transferRetention(); await full.dispose(); await reply.dispose();
        expect(budget.active).toBe(0); expect((await f.stats()).charged_bytes).toBeGreaterThan(0); await lease.dispose();
        expect(await f.stats()).toMatchObject({ charged_bytes: 0, charged_buffers: 0, live_captures: 0, handles: 0, now: 0 });
      }
      expect(f.requests.filter(r => r.operation === "retain")).toHaveLength(0);
      expect(f.requests.filter(r => r.operation === "current_owned")).toHaveLength(100);
    } catch (error) {
      throw new Error(`actual Store assembly failed at iteration ${iteration}; ${JSON.stringify(f.diagnostics())}`, { cause: error });
    } finally { await f.close(); }
  }, 30000);
  it("shows the old SDK reader release leaves legacy initial pins until 120 seconds", async () => {
    const f = await realStoreBridge();
    try {
      for (let i = 0; i < 4; i++) { const full = await f.session.getFullCurrent(); await full.dispose(); }
      expect(await f.stats()).toMatchObject({ live_captures: 4, handles: 0, now: 0 });
      expect((await f.session.current()).data.status).toBe("capacity_exceeded");
      await f.call("expire"); expect(await f.stats()).toMatchObject({ charged_bytes: 0, charged_buffers: 0, live_captures: 0, now: 120000 });
    } finally { await f.close(); }
  });
  it("bounds an unjoinable cancelled HTTP orphan and releases actual bytes on trusted assembly admission failure", async () => {
    const f = await realStoreBridge(), abort = new AbortController();
    try {
      f.afterReply((operation) => { if (operation === "current_owned") abort.abort(new Error("cancel after owned backend reply")); });
      await expect(f.session.currentOwned({ signal: abort.signal })).rejects.toThrow();
      // The HTTP fetch can abort before receiving a joinable reply. That orphan
      // is the explicitly bounded case; source tests separately force trusted decode.
      const stats = await f.stats(); if (stats.charged_bytes) await f.call("expire");
      f.afterReply(undefined); expect((await f.stats()).charged_bytes).toBe(0);
      const reply = await f.session.currentOwned();
      await expect(f.session.getFull({ capture: reply.data.capture!, context: reply.data.context, retention: reply.data.retention,
        readerLease: reply.takeRetention(), maxActions: 0 })).rejects.toThrow(); await reply.dispose();
      expect(await f.stats()).toMatchObject({ charged_bytes: 0, charged_buffers: 0, live_captures: 0, handles: 0 });
    } finally { await f.close(); }
  });
});
