import { describe, expect, it } from "vitest";
import { NativeLogicalSession } from "../src/index.js";
import { nativeHarness, nativeScenario } from "./nativeLogicalFixtures.js";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return { promise, resolve };
}

describe("native submit preparation and original response lifetime", () => {
  it("does not acquire control or dispatch after Human stops during capability negotiation", async () => {
    const fake = nativeScenario();
    const entered = deferred<void>();
    const response = deferred<unknown>();
    let capabilities = 0;
    const source = await nativeHarness(3, url => {
      if (url.pathname.endsWith("/capabilities") && ++capabilities === 2) {
        entered.resolve();
        return response.promise;
      }
    });
    const session = new NativeLogicalSession(source.client, source.controller);
    const stop = new AbortController();
    try {
      const pending = session.submit({ requestId: "before-negotiation", expectedSnapshotId: source.capture.snapshot_id,
        actionId: source.actions[0]!.action_id, preSubmitSignal: stop.signal });
      const rejected = expect(pending).rejects.toThrow(/Human/u);
      await entered.promise;
      stop.abort(new Error("Human stopped"));
      await source.controller.releaseControl();
      response.resolve(fake.fixture.wire_samples.capabilities);
      await rejected;
      expect(source.calls.some(call => call.url.pathname.endsWith("/controller/acquire"))).toBe(false);
      expect(source.calls.some(call => call.url.pathname.endsWith("/actions"))).toBe(false);
      expect(source.controller.snapshot().controller_lease_id).toBeNull();
    } finally { await source.controller.close(); }
  });

  it("cleans the actual late credentials and makes no POST when Human stops during acquisition", async () => {
    const fake = nativeScenario();
    const entered = deferred<void>();
    const response = deferred<unknown>();
    const source = await nativeHarness(3, (url, body) => {
      if (url.pathname.endsWith("/controller/acquire")) { entered.resolve(); return response.promise; }
      return undefined;
    });
    const stop = new AbortController();
    try {
      const pending = source.session.submit({ requestId: "before-credentials", expectedSnapshotId: source.capture.snapshot_id,
        actionId: source.actions[0]!.action_id, preSubmitSignal: stop.signal });
      const rejected = expect(pending).rejects.toThrow(/released/u);
      await entered.promise;
      stop.abort(new Error("Human stopped"));
      const release = source.controller.releaseControl();
      response.resolve(fake.route(new URL("http://fixture/controller/acquire"), {}));
      await rejected;
      await release;
      expect(source.calls.some(call => call.url.pathname.endsWith("/actions"))).toBe(false);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/controller/acquire"))).toHaveLength(1);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/controller/release"))).toHaveLength(1);
      expect(source.controller.snapshot().controller_lease_id).toBeNull();
    } finally { await source.controller.close(); }
  });

  it("does not dispatch after reentrant Stop in the synchronous admission notification", async () => {
    const source = await nativeHarness();
    const stop = new AbortController();
    let release: Promise<void> | undefined;
    let notices = 0;
    try {
      await expect(source.session.submit({ requestId: "at-admission", expectedSnapshotId: source.capture.snapshot_id,
        actionId: source.actions[0]!.action_id, preSubmitSignal: stop.signal,
        onSubmitStart() { notices++; stop.abort(new Error("Human stopped")); release = source.controller.releaseControl(); } }))
        .rejects.toThrow(/Human/u);
      await release;
      expect(notices).toBe(1);
      expect(source.calls.some(call => call.url.pathname.endsWith("/actions"))).toBe(false);
      expect(source.controller.snapshot().controller_lease_id).toBeNull();
    } finally { await source.controller.close(); }
  });

  it("retains the original bounded Result after POST starts while control is immediately released", async () => {
    const fake = nativeScenario();
    const entered = deferred<void>();
    const response = deferred<unknown>();
    const source = await nativeHarness(3, url => {
      if (url.pathname.endsWith("/actions")) { entered.resolve(); return response.promise; }
      return undefined;
    });
    const stop = new AbortController();
    let notices = 0;
    try {
      const pending = source.session.submit({ requestId: "after-dispatch", expectedSnapshotId: source.capture.snapshot_id,
        actionId: source.actions[0]!.action_id, preSubmitSignal: stop.signal, onSubmitStart() { notices++; } });
      await entered.promise;
      stop.abort(new Error("Human stopped"));
      await source.controller.releaseControl();
      expect(source.controller.snapshot().controller_lease_id).toBeNull();
      expect(source.calls.find(call => call.url.pathname.endsWith("/actions"))!.init.signal!.aborted).toBe(false);
      response.resolve(fake.route(new URL("http://fixture/api/player-environment/actions"), { request_id: "after-dispatch" }));
      const result = await pending;
      expect(notices).toBe(1);
      expect(result.status).toBe("terminal");
      if (result.status === "terminal") expect(result.result.data.delivery).toBe("unknown");
      expect(source.calls.filter(call => call.url.pathname.endsWith("/actions"))).toHaveLength(1);
      expect(source.calls.some(call => call.url.pathname.includes("/actions/"))).toBe(false);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/controller/release"))).toHaveLength(1);
    } finally { await source.controller.close(); }
  });

  it("rejects invalid native route arguments before the admission notification", async () => {
    const source = await nativeHarness();
    let notices = 0;
    try {
      for (const actionId of ["a".repeat(129), "不合规", "has space"]) {
        await expect(source.session.submit({ requestId: "invalid-action", expectedSnapshotId: source.capture.snapshot_id,
          actionId, onSubmitStart() { notices++; } })).rejects.toThrow();
      }
      expect(notices).toBe(0);
      expect(source.calls.some(call => call.url.pathname.endsWith("/actions"))).toBe(false);
    } finally { await source.controller.close(); }
  });
});
