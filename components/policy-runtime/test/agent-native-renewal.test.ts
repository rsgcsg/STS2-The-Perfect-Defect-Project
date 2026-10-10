import { describe, expect, it } from "vitest";
import { nativeRuntimeFixture, type SyntheticNativeHttp } from "./native-runtime-fixtures.js";
import type { NativeLogicalSession } from "@rsgcsg/sts2-connector-client";

function gate() { let release!: () => void; const promise = new Promise<void>(resolve => { release = resolve; }); return { promise, release }; }
async function eventually(condition: () => boolean, timeout = 1500) {
  const start = performance.now();
  while (!condition()) {
    if (performance.now() - start > timeout) throw new Error("actual bounded HTTP condition not reached");
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}
const renews = (f: Awaited<ReturnType<typeof nativeRuntimeFixture>>) => f.source.requests.filter(r => r.path.endsWith("/renew")).length;
const expired = { schema: "sts2.player-environment/native-logical-renew-1", input_profile: "native-logical-v1", status: "subscription_expired",
  subscription: null, next_cursor: null, high_watermark: null, retained_start_cursor: null, gap: null, reason: "subscription_expired" };

describe("one passive renewal flight independent of the Model operation queue", () => {
  it("quiesces a transport renewal that ignores abort when initialized attachment evidence fails, before detach", async () => {
    const evidenceGate = gate(), renewGate = gate(); let source: SyntheticNativeHttp | undefined;
    const creating = nativeRuntimeFixture({ mode: "human", retentionMs: 400, beforeInitialize: value => {
      source = value.source; source.renewGate = renewGate.promise;
      const append = value.evidence.append.bind(value.evidence);
      value.evidence.append = async (...args) => { if (args[0] === "native_session_attached") { await evidenceGate.promise; throw new Error("attachment evidence failure"); } return append(...args); };
      const request = value.environment.nativeLogicalRequest.bind(value.environment);
      value.environment.nativeLogicalRequest = (method, body, options) => request(method, body,
        method === "renew" ? { ...options, signal: undefined } : options);
    } });
    let completed = false;
    const failure = creating.then(() => { completed = true; throw new Error("unexpected successful initialization"); }, error => { completed = true; return error; });
    try {
      await eventually(() => Boolean(source?.requests.some(r => r.path.endsWith("/renew"))));
      evidenceGate.release(); await new Promise(resolve => setTimeout(resolve, 25));
      expect(completed).toBe(false); expect(source!.requests.filter(r => r.path.endsWith("/detach"))).toHaveLength(0);
      renewGate.release(); expect(String(await failure)).toContain("attachment evidence failure");
      expect(source!.requests.filter(r => r.path.endsWith("/renew"))).toHaveLength(1);
      expect(source!.requests.filter(r => r.path.endsWith("/detach"))).toHaveLength(1);
      expect(source!.maxRenewInFlight).toBe(1);
    } finally { evidenceGate.release(); renewGate.release(); await failure; }
  });
  it("keeps an announced 400ms subscription alive throughout an owned five-second Await", async () => {
    const f = await nativeRuntimeFixture({ count: 0, mode: "auto", retentionMs: 400 }); const wait = gate(); f.source.waitGate = wait.promise;
    const route = f.source.route.bind(f.source);
    f.source.route = (url, body) => url.pathname.endsWith("/await") && Date.now() >= Date.parse(String(f.source.subscription.expires_at))
      ? { value: { schema: "sts2.player-environment/native-logical-await-1", status: "subscription_expired", event: null, gap: null, reason: "subscription_expired" } } : route(url, body);
    try {
      const tick = f.runtime.tick(); await eventually(() => f.source.requests.some(r => r.path.endsWith("/await")));
      expect(f.source.requests.find(r => r.path.endsWith("/await"))?.body.timeout_ms).toBe(5000);
      await new Promise(resolve => setTimeout(resolve, 5000)); wait.release();
      expect((await tick).type).toBe("awaited");
      expect(f.runtime.status()).toMatchObject({ mode: "auto", session: { state_version: 1, prefix: { omissions: { gap: null } } } });
      expect(renews(f)).toBeGreaterThanOrEqual(10); expect(renews(f)).toBeLessThanOrEqual(40);
      expect(f.source.maxRenewInFlight).toBe(1);
      expect(f.source.requests.filter(r => r.path.includes("/controller/") || r.path.endsWith("/actions"))).toHaveLength(0);
    } finally { wait.release(); await f.close(); }
  }, 15000);
  it("renews during a long real Consume without advancing or interrupting the pending Model input", async () => {
    const f = await nativeRuntimeFixture({ child: "slow_consume", mode: "shadow", retentionMs: 400 });
    try {
      const tick = f.runtime.tick(); await eventually(() => renews(f) >= 2);
      expect(f.runtime.status().session.state_version).toBe(0);
      expect((await tick).type).toBe("shadow"); expect(f.runtime.status().session.state_version).toBe(1);
      expect(renews(f)).toBeGreaterThanOrEqual(3); expect(renews(f)).toBeLessThanOrEqual(10);
      expect(f.source.maxRenewInFlight).toBe(1);
      expect(f.source.requests.filter(r => r.path.includes("/controller/"))).toHaveLength(0);
    } finally { await f.close(); }
  });
  it("preserves passive renewal through Human without acquiring a controller or advancing W", async () => {
    const f = await nativeRuntimeFixture({ mode: "human", retentionMs: 400 });
    try {
      await f.runtime.setMode("human"); await eventually(() => renews(f) >= 2);
      expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "released", session: { state_version: 0 } });
      expect(f.source.requests.filter(r => r.path.includes("/controller/") || r.path.endsWith("/detach"))).toHaveLength(0);
      await f.runtime.stop(); expect(f.source.requests.filter(r => r.path.endsWith("/detach"))).toHaveLength(1);
    } finally { await f.close(); }
  });
  it("waits for a late SDK renewal that ignores abort before detach, preventing subscription resurrection", async () => {
    const f = await nativeRuntimeFixture({ mode: "human", retentionMs: 400 }); const late = gate();
    const native = (f.runtime as unknown as { native: NativeLogicalSession }).native, renew = native.renew.bind(native);
    native.renew = async cursor => { const reply = await renew(cursor); await late.promise; return reply; };
    try {
      await eventually(() => renews(f) === 1);
      let stopped = false; const stop = f.runtime.stop().then(value => { stopped = true; return value; });
      await new Promise(resolve => setTimeout(resolve, 25));
      expect(stopped).toBe(false); expect(f.source.requests.filter(r => r.path.endsWith("/detach"))).toHaveLength(0);
      expect(renews(f)).toBe(1); late.release(); await stop;
      expect(native.subscription).toBeUndefined();
      expect(f.source.requests.filter(r => r.path.endsWith("/detach"))).toHaveLength(1);
      await new Promise(resolve => setTimeout(resolve, 100)); expect(renews(f)).toBe(1);
    } finally { late.release(); await f.close(); }
  });
  it("fences and releases an idle held controller on genuine renewal failure without another tick", async () => {
    const failedRenewal = gate();
    const f = await nativeRuntimeFixture({ mode: "auto", retentionMs: 400,
      beforeInitialize: ({ source }) => { source.renewGate = failedRenewal.promise; } });
    const route = f.source.route.bind(f.source);
    f.source.route = (url, body) => url.pathname.endsWith("/renew") ? { value: expired } : route(url, body);
    try {
      const delivered = await f.runtime.tick(); expect(delivered.type, JSON.stringify(delivered)).toBe("delivered");
      expect(f.runtime.status().controller).toBe("held");
      await eventually(() => renews(f) === 1);
      failedRenewal.release();
      await eventually(() => f.source.requests.some(r => r.path.endsWith("/controller/release")));
      await eventually(() => f.runtime.status().session.prefix.omissions.gap !== null);
      expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "released" });
      await expect(f.runtime.setMode("auto")).rejects.toMatchObject({ code: "runtime_subscription_unavailable" });
      const count = renews(f); await new Promise(resolve => setTimeout(resolve, 500)); expect(renews(f)).toBe(count); expect(count).toBe(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/controller/release"))).toHaveLength(1);
    } finally { failedRenewal.release(); await f.close(); }
  });
  it("cancels the active Await immediately when the passive subscription fails", async () => {
    const failedRenewal = gate();
    const f = await nativeRuntimeFixture({ count: 0, mode: "auto", retentionMs: 400,
      beforeInitialize: ({ source }) => { source.renewGate = failedRenewal.promise; } });
    const wait = gate(); f.source.waitGate = wait.promise;
    const route = f.source.route.bind(f.source); f.source.route = (url, body) => url.pathname.endsWith("/renew") ? { value: expired } : route(url, body);
    try {
      const tick = f.runtime.tick(); await eventually(() => f.source.requests.some(r => r.path.endsWith("/await")));
      await eventually(() => renews(f) === 1);
      failedRenewal.release();
      await tick;
      await eventually(() => f.runtime.status().session.prefix.omissions.gap !== null);
      expect(f.runtime.status()).toMatchObject({ mode: "human", session: { state_version: 1, agent_state: "known" } });
      expect(f.source.requests.filter(r => r.path.endsWith("/cancel_wait"))).toHaveLength(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(0);
      expect(renews(f)).toBe(1);
    } finally { failedRenewal.release(); wait.release(); await f.close(); }
  });
  it("writes a queued renewal gap before Stop seals an interrupted Close operation", async () => {
    const f = await nativeRuntimeFixture({ child: "close", mode: "shadow", retentionMs: 400 }); const paused = gate(), seen = gate();
    const append = f.evidence.append.bind(f.evidence), route = f.source.route.bind(f.source);
    f.evidence.append = async (...args) => { const result = await append(...args); if (args[0] === "agent_directive") { seen.release(); await paused.promise; } return result; };
    f.source.route = (url, body) => url.pathname.endsWith("/renew") ? { value: expired } : route(url, body);
    try {
      const tick = f.runtime.tick(); await seen.promise;
      await eventually(() => renews(f) === 1 && f.runtime.status().mode === "human");
      const stop = f.runtime.stop(); paused.release(); await tick; await stop;
      const events = await f.events(), gapIndex = events.findIndex(e => e.kind === "native_gap"), stoppedIndex = events.findIndex(e => e.kind === "stopped");
      expect(gapIndex).toBeGreaterThan(-1); expect(stoppedIndex).toBeGreaterThan(gapIndex);
      expect(stoppedIndex).toBe(events.length - 1);
      expect(f.runtime.status().session.prefix.omissions.gap).not.toBeNull();
    } finally { paused.release(); await f.close(); }
  });
});
