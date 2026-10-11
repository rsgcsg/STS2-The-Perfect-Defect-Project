import { createServer } from "node:http";
import { once } from "node:events";
import { describe, expect, it } from "vitest";
import { EnvironmentControllerSession, NativeLogicalSession, PlayerEnvironmentRestClient, type JsonObject } from "../src/index.js";
import { nativeHarness, nativeScenario, measuredBudget } from "./nativeLogicalFixtures.js";

const own = (source: ReturnType<typeof nativeScenario>) => ({ ...source.current, retention: source.retention });
const advertised = (source: ReturnType<typeof nativeScenario>) => {
  const original = source.route(new URL("http://fixture/native-logical/capabilities"), {}) as Record<string, unknown>;
  return { ...original, supported_methods: [...original.supported_methods as string[], "current_owned"],
    implemented_mechanisms: [...original.implemented_mechanisms as string[], "native_current_reader_owned_v1"] };
};

// Wire/ownership error tests; these scripted HTTP fixtures do not prove Store
// pressure. The linked C# Store/Projector bridge owns that separate gate.
describe("owned Current SDK temporary reader and actual dispatch binding", () => {
  it("refuses a missing capability without a Current fallback", async () => {
    const f = await nativeHarness(1);
    try {
      await expect(f.session.currentOwned()).rejects.toThrow(/not advertised/u);
      expect(f.calls.filter(c => /\/(current|current_owned)$/.test(c.url.pathname))).toHaveLength(0);
    } finally { await f.controller.close(); }
  });

  it("releases a coherent original reply even when registration closes before assembly", async () => {
    const scenario = nativeScenario(1); let close!: () => Promise<void>;
    const f = await nativeHarness(1, async url => {
      if (url.pathname.endsWith("/capabilities")) return advertised(scenario);
      if (url.pathname.endsWith("/current_owned")) { await close(); return own(scenario); }
    });
    close = () => f.controller.close();
    await expect(f.session.currentOwned()).rejects.toThrow();
    const releases = f.calls.filter(c => c.url.pathname.endsWith("/release") && c.body.retention_handle_id);
    expect(releases).toHaveLength(1); expect(releases[0]!.body.client_session_id).toBe("client-fixture");
    expect(releases[0]!.body.retention_handle_id).toBe(scenario.retention.retention_handle_id);
  });

  it("transfers one lease through assembly while freeing byte reservations", async () => {
    const scenario = nativeScenario(1);
    const f = await nativeHarness(1, url => url.pathname.endsWith("/capabilities") ? advertised(scenario)
      : url.pathname.endsWith("/current_owned") ? own(scenario) : undefined);
    const budget = measuredBudget();
    try {
      const reply = await f.session.currentOwned();
      const full = await f.session.getFull({ capture: reply.data.capture!, context: reply.data.context,
        retention: reply.data.retention, readerLease: reply.takeRetention(), budget });
      expect(() => reply.takeRetention()).toThrow();
      const lease = full.transferRetention(); expect(() => full.transferRetention()).toThrow();
      await full.dispose(); await reply.dispose(); expect(budget.active).toBe(0);
      expect(f.calls.filter(c => c.url.pathname.endsWith("/retain"))).toHaveLength(0);
      expect(f.calls.filter(c => c.url.pathname.endsWith("/release") && c.body.retention_handle_id)).toHaveLength(0);
      await lease.dispose(); await lease.dispose();
      expect(f.calls.filter(c => c.url.pathname.endsWith("/release") && c.body.retention_handle_id)).toHaveLength(1);
    } finally { await f.controller.close(); }
  });

  it("adopts the transferred reader before an aborted assembly admission", async () => {
    const scenario = nativeScenario(1);
    const f = await nativeHarness(1, url => url.pathname.endsWith("/capabilities") ? advertised(scenario)
      : url.pathname.endsWith("/current_owned") ? own(scenario) : undefined);
    try {
      const reply = await f.session.currentOwned(), signal = AbortSignal.abort(new Error("cancel before assembly"));
      await expect(f.session.getFull({ capture: reply.data.capture!, context: reply.data.context,
        retention: reply.data.retention, readerLease: reply.takeRetention(), signal })).rejects.toThrow(/cancel/u);
      await reply.dispose();
      expect(f.calls.filter(c => c.url.pathname.endsWith("/release") && c.body.retention_handle_id)).toHaveLength(1);
    } finally { await f.controller.close(); }
  });

  it.each([409, 429])("decodes the actual REST owned Current closed HTTP%s failure", async status => {
    const source = nativeScenario(1), seen: string[] = [];
    const failure = { schema: "sts2.player-environment/native-logical-current-1", input_profile: "native-logical-v1",
      status: status === 429 ? "capacity_exceeded" : "source_capture_incomplete", capture: null, context: null,
      retention: null, reason: status === 429 ? "capacity_exceeded" : "source_capture_incomplete" };
    const server = createServer(async (request, response) => {
      const bytes: Buffer[] = []; for await (const chunk of request) bytes.push(Buffer.from(chunk));
      const body = bytes.length ? JSON.parse(Buffer.concat(bytes).toString()) as JsonObject : {};
      const url = new URL(request.url!, "http://127.0.0.1"); seen.push(url.pathname);
      const owned = url.pathname.endsWith("/current_owned");
      const value = owned ? failure : url.pathname.endsWith("/capabilities") ? advertised(source) : source.route(url, body);
      response.writeHead(owned ? status : 200, { "content-type": "application/json" }); response.end(JSON.stringify(value));
    });
    server.listen(0, "127.0.0.1"); await once(server, "listening"); const address = server.address();
    if (!address || typeof address === "string") throw new Error("fixture listener unavailable");
    const client = new PlayerEnvironmentRestClient(`http://127.0.0.1:${address.port}`, 2000);
    const controller = new EnvironmentControllerSession(client, { productId: "test", productName: "Test", productVersion: "1", clientInstanceId: "fixture" });
    const session = new NativeLogicalSession(client, controller);
    try {
      const cap = (await session.capabilities()).data; await controller.register(cap.session, cap.control_policy);
      const reply = await session.currentOwned(); expect(reply.data).toEqual(failure); await reply.dispose();
      expect(seen.filter(path => path.endsWith("/current"))).toHaveLength(0);
    } finally {
      await controller.close(); server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve()));
    }
  });

  it("memoizes a failed original release for concurrent and repeated reply disposal", async () => {
    const scenario = nativeScenario(1);
    const f = await nativeHarness(1, url => url.pathname.endsWith("/capabilities") ? advertised(scenario)
      : url.pathname.endsWith("/current_owned") ? own(scenario)
      : url.pathname.endsWith("/release") ? new Response(JSON.stringify({ error: { code: "release_failed", detail: "original release failed" } }), { status: 500 }) : undefined);
    try {
      const reply = await f.session.currentOwned(), first = reply.dispose(), second = reply.dispose();
      expect(second).toBe(first);
      await expect(first).rejects.toThrow(); await expect(second).rejects.toThrow(); await expect(reply.dispose()).rejects.toThrow();
      expect(f.calls.filter(c => c.url.pathname.endsWith("/release") && c.body.retention_handle_id)).toHaveLength(1);
    } finally { await f.controller.close(); }
  });

  it("keeps the originally supplied hook and start callback even when the caller mutates options", async () => {
    const scenario = nativeScenario(1); let starts = 0;
    const f = await nativeHarness(1, (url, body) => url.pathname.endsWith("/actions") ? {
      ...scenario.route(url, body) as Record<string, unknown>, attribution: {
        runtime_instance_id: scenario.capture.session.runtime_instance_id, client_session_id: body.client_session_id,
        controller_lease_id: "wrong-original-lease", controller_generation: body.controller_generation,
        client_instance_id: "sdk-fixture", product_id: "test", product_name: "Test", product_version: "1" } } : undefined);
    try {
      const input: import("../src/index.js").NativeLogicalSubmitInput = { requestId: "hook-alias", expectedSnapshotId: f.capture.snapshot_id,
        actionId: f.actions[0]!.action_id, onSubmitStart: () => { starts++; },
        onDispatchBinding: async () => { input.onDispatchBinding = undefined; input.onSubmitStart = () => { throw new Error("replaced callback"); }; } };
      await expect(f.session.submit(input)).rejects.toThrow(/original actual dispatch/u);
      expect(starts).toBe(1); expect(f.calls.filter(c => c.url.pathname.endsWith("/actions"))).toHaveLength(1);
    } finally { await f.controller.close(); }
  });

  it("copies and validates recovery binding before an awaited original lookup", async () => {
    const scenario = nativeScenario(1); let entered!: () => void, answer!: (value: unknown) => void;
    const enteredPromise = new Promise<void>(resolve => { entered = resolve; });
    const replyPromise = new Promise<unknown>(resolve => { answer = resolve; });
    const f = await nativeHarness(1, url => { if (url.pathname.includes("/actions/")) { entered(); return replyPromise; } });
    try {
      const original = { runtime_instance_id: f.capture.session.runtime_instance_id, client_session_id: "client-fixture",
        controller_lease_id: "lease-fixture", controller_generation: 1 };
      const pending = f.session.result("original-lookup", undefined, original);
      const rejected = expect(pending).rejects.toThrow(/original actual dispatch/u); await enteredPromise;
      original.controller_generation = 2;
      answer({ ...scenario.route(new URL("http://fixture/actions/original-lookup"), {}) as Record<string, unknown>, attribution: {
        ...original, client_instance_id: "sdk-fixture", product_id: "test", product_name: "Test", product_version: "1" } });
      await rejected;
    } finally { await f.controller.close(); }
  });

  it("awaits the immutable actual binding before the unchanged sync start and sends that body", async () => {
    const scenario = nativeScenario(1); let seenBinding: Record<string, unknown> | undefined, start = 0;
    const f = await nativeHarness(1, (url, body) => {
      if (url.pathname.endsWith("/actions")) return { ...scenario.route(url, body) as Record<string, unknown>,
        attribution: { ...seenBinding, client_instance_id: "sdk-fixture", product_id: "test", product_name: "Test", product_version: "1" } };
    });
    try {
      const input = { requestId: "binding-request", expectedSnapshotId: f.capture.snapshot_id, actionId: f.actions[0]!.action_id,
        onDispatchBinding: async (binding: Readonly<Record<string, unknown>>) => {
          expect(Object.isFrozen(binding)).toBe(true); seenBinding = { ...binding };
          expect(start).toBe(0); expect(f.calls.filter(c => c.url.pathname.endsWith("/actions"))).toHaveLength(0);
          await Promise.resolve(); input.actionId = "changed-caller-input";
        }, onSubmitStart: () => { start++; } };
      const result = await f.session.submit(input); expect(result.status).toBe("terminal"); expect(start).toBe(1);
      const post = f.calls.find(c => c.url.pathname.endsWith("/actions"))!;
      expect(post.body.bound_action_id).toBe(f.actions[0]!.action_id);
      expect(seenBinding?.client_session_id).toBe(post.body.client_session_id);
      expect(seenBinding?.controller_generation).toBe(post.body.controller_generation);
    } finally { await f.controller.close(); }
  });

  it("does not start or POST when binding evidence fails or Stop cancels the awaited hook", async () => {
    for (const cancel of [false, true]) {
      const f = await nativeHarness(1), signal = new AbortController(); let starts = 0;
      try {
        await expect(f.session.submit({ requestId: "before-dispatch", expectedSnapshotId: f.capture.snapshot_id,
          actionId: f.actions[0]!.action_id, preSubmitSignal: signal.signal,
          onDispatchBinding: async () => { if (cancel) signal.abort(new Error("Stop")); else throw new Error("evidence failed"); },
          onSubmitStart: () => { starts++; } })).rejects.toThrow(cancel ? /Stop/u : /evidence/u);
        expect(starts).toBe(0); expect(f.calls.filter(c => c.url.pathname.endsWith("/actions"))).toHaveLength(0);
      } finally { await f.controller.close(); }
    }
  });
});

describe("original four-field terminal attribution", () => {
  it.each(["runtime_instance_id", "client_session_id", "controller_lease_id", "controller_generation"] as const)("rejects forged %s on the original one-POST response", async field => {
    const scenario = nativeScenario(1);
    const f = await nativeHarness(1, (url, body) => {
      if (!url.pathname.endsWith("/actions")) return undefined;
      const attribution: Record<string, unknown> = { runtime_instance_id: scenario.capture.session.runtime_instance_id,
        client_session_id: body.client_session_id, controller_lease_id: body.controller_lease_id, controller_generation: body.controller_generation,
        client_instance_id: "sdk-fixture", product_id: "test", product_name: "Test", product_version: "1" };
      attribution[field] = field === "controller_generation" ? 999 : "forged-original";
      return { ...scenario.route(url, body) as Record<string, unknown>, attribution };
    });
    try {
      await expect(f.session.submit({ requestId: "forgery-" + field, expectedSnapshotId: f.capture.snapshot_id,
        actionId: f.actions[0]!.action_id, onDispatchBinding: () => undefined })).rejects.toThrow();
      expect(f.calls.filter(c => c.url.pathname.endsWith("/actions"))).toHaveLength(1);
    } finally { await f.controller.close(); }
  });
});
