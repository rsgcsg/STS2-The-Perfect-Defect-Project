import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { NATIVE_LOGICAL_SCOPE, NativeLogicalSession, PlayerEnvironmentRestClient, EnvironmentControllerSession,
  type NativeLogicalCatalogPage } from "../src/index.js";
import { nativeHarness, nativeScenario, measuredBudget, nativeFixture } from "./nativeLogicalFixtures.js";

describe("native logical facade and whole capture", () => {
  it("bootstraps one existing registration and attaches passively before acquiring control", async () => {
    const source = await nativeHarness();
    try {
      expect(source.calls.map(call => call.url.pathname.split("/").at(-1))).toEqual(["capabilities", "register"]);
      await source.session.attach({ eagerScope: NATIVE_LOGICAL_SCOPE, requiredSeams: source.capabilities.capture_coverage,
        deliveryMode: "full_reference" });
      expect(source.controller.clientIdentity()?.clientSessionId).toBe("client-fixture");
      expect(source.calls.at(-1)?.body).toEqual({ client_session_id: "client-fixture", eager_scope: [...NATIVE_LOGICAL_SCOPE],
        required_seams: source.capabilities.capture_coverage, delivery_mode: "full_reference" });
      expect(source.calls.some(call => call.url.pathname.includes("/controller/"))).toBe(false);
      expect((source.session as any).controller).toBeUndefined();
      expect(() => JSON.stringify(source.session)).not.toThrow();
      expect(JSON.stringify(source.session)).not.toContain("lease");
    } finally { await source.controller.close(); }
  });

  it.each([0, 200, 500, 10000])("assembles one coherent capture and all %i ordered actions", async count => {
    const source = await nativeHarness(count);
    const budget = measuredBudget();
    try {
      const full = await source.session.getFullCurrent({ budget, chunkBytes: count >= 10000 ? 1024 * 1024 : 1024, pageLimit: 73 });
      expect(full.actions).toEqual(source.actions);
      expect(full.serializedObservation).toBe(source.serialized);
      expect(createHash("sha256").update(full.serializedObservation).digest("hex")).toBe(source.capture.sha256);
      const unicodeStart = Buffer.byteLength(source.serialized.split("中文完整")[0]!, "utf8");
      expect(unicodeStart % 1024).toBe(1023);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/current"))).toHaveLength(1);
      expect(source.calls.find(call => call.url.pathname.endsWith("/current"))?.body)
        .toEqual({ client_session_id: "client-fixture", eager_scope: [...NATIVE_LOGICAL_SCOPE], expected_snapshot_id: null });
      expect(source.calls.find(call => call.url.pathname.endsWith("/read"))?.body.cursor).toBe("fresh-reader-0");
      expect(source.calls.filter(call => call.url.pathname.endsWith("/catalog"))).toHaveLength(Math.max(1, Math.ceil(count / 73)));
      expect(budget.calls[0]).toEqual(["observation", source.bytes.length]);
      expect(budget.active).toBeGreaterThan(source.bytes.length);
      const identity = source.controller.clientIdentity();
      await full.dispose();
      await full.dispose();
      expect(budget.active).toBe(0);
      expect(() => full.observation).toThrow(/disposed/u);
      expect(() => full.actions).toThrow(/disposed/u);
      expect(() => full.serializedObservation).toThrow(/disposed/u);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/release"))).toHaveLength(1);
      expect(source.controller.clientIdentity()).toEqual(identity);
      expect(source.calls.some(call => call.url.pathname.includes("/controller/"))).toBe(false);
    } finally { await source.controller.close(); }
  });

  it("reserves each page before HTTP and shrinks to its actual wire bytes", async () => {
    const budget = measuredBudget();
    let pageRequests = 0;
    const actual: number[] = [];
    const fake = nativeScenario();
    const source = await nativeHarness(3, (url, body) => {
      if (url.pathname.endsWith("/read")) expect(budget.calls[0]).toEqual(["observation", fake.bytes.length]);
      if (url.pathname.endsWith("/catalog")) {
        pageRequests++;
        expect(budget.calls.filter(([kind]) => kind === "catalog_page")).toHaveLength(pageRequests);
        const page = fake.catalog(body);
        actual.push(Buffer.byteLength(JSON.stringify(page)));
        return page;
      }
    });
    try {
      const full = await source.session.getFullCurrent({ budget, pageLimit: 1 });
      expect(budget.active).toBe(source.bytes.length + actual.reduce((sum, bytes) => sum + bytes, 0));
      await full.dispose();
      expect(budget.active).toBe(0);
    } finally { await source.controller.close(); }
  });

  it.each([
    ["capture", (page: NativeLogicalCatalogPage) => ({ ...page, catalog_ref: "foreign" })],
    ["generation", (page: NativeLogicalCatalogPage) => ({ ...page, stream_generation: "foreign" })],
    ["count", (page: NativeLogicalCatalogPage) => ({ ...page, total_count: 9 })],
    ["filter", (page: NativeLogicalCatalogPage) => ({ ...page, filtered_count: 1 })],
    ["digest", (page: NativeLogicalCatalogPage) => ({ ...page, digest: "0".repeat(64) })],
    ["duplicate", (page: NativeLogicalCatalogPage) => ({ ...page, actions: [page.actions[0]!, page.actions[0]!] })],
    ["reorder", (page: NativeLogicalCatalogPage) => ({ ...page, actions: [...page.actions].reverse() })],
    ["empty-progress", (page: NativeLogicalCatalogPage) => ({ ...page, actions: [], next_cursor: "loop" })]
  ] as const)("rejects %s full-catalog corruption and releases owned pins/bytes", async (_, mutate) => {
    const fake = nativeScenario();
    const source = await nativeHarness(3, (url, body) => url.pathname.endsWith("/catalog") ? mutate(fake.catalog(body)) : undefined);
    const budget = measuredBudget();
    try {
      await expect(source.session.getFullCurrent({ budget })).rejects.toThrow();
      expect(budget.active).toBe(0);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/release"))).toHaveLength(1);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/current"))).toHaveLength(1);
    } finally { await source.controller.close(); }
  });

  it("uses no Current fallback for expired historical bytes", async () => {
    const source = await nativeHarness(3, url => url.pathname.endsWith("/read")
      ? new Response(JSON.stringify({ error: { code: "payload_expired", detail: "expired" } }), { status: 410 }) : undefined);
    const budget = measuredBudget();
    try {
      await expect(source.session.getFull({ capture: source.capture, budget })).rejects.toThrow(/410/u);
      expect(source.calls.some(call => call.url.pathname.endsWith("/current"))).toBe(false);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/read"))).toHaveLength(1);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/release"))).toHaveLength(1);
      expect(budget.active).toBe(0);
    } finally { await source.controller.close(); }
  });

  it("cancels after reservation and releases its newly acquired reader pin", async () => {
    const source = await nativeHarness();
    const abort = new AbortController();
    const budget = measuredBudget();
    const original = budget.reserve.bind(budget);
    budget.reserve = input => { const token = original(input); abort.abort(new Error("owner stopped")); return token; };
    try {
      await expect(source.session.getFullCurrent({ budget, signal: abort.signal })).rejects.toThrow(/owner stopped/u);
      expect(source.calls.some(call => call.url.pathname.endsWith("/read"))).toBe(false);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/release"))).toHaveLength(1);
      expect(budget.active).toBe(0);
    } finally { await source.controller.close(); }
  });

  it("keeps repeated event/cursor identity and does not acquire a lease or advance acknowledgement", async () => {
    const source = await nativeHarness();
    try {
      await source.session.attach({ eagerScope: NATIVE_LOGICAL_SCOPE, requiredSeams: source.capabilities.capture_coverage, deliveryMode: "full_reference" });
      const cursor = source.session.subscription!.starting_cursor;
      const first = await source.session.events({ afterCursor: cursor });
      const again = await source.session.events({ afterCursor: cursor });
      expect(again.data).toEqual(first.data);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/events")).map(call => call.body.after_cursor)).toEqual([cursor, cursor]);
      const original = source.session.subscription;
      await source.session.renew(first.data.next_cursor);
      expect(source.session.subscription!.starting_cursor).toBe(original!.starting_cursor);
      expect(source.session.subscription!.scope_id).toBe(original!.scope_id);
      expect(source.calls.some(call => call.url.pathname.includes("/controller/"))).toBe(false);
    } finally { await source.controller.close(); }
  });

  it("uses source query prefixes without creating operands or accepting a partial exact expression", async () => {
    const fake = nativeScenario();
    const source = await nativeHarness();
    try {
      await source.session.list({ catalogRef: fake.observation.catalog.catalog_ref, streamGeneration: fake.capture.stream_generation,
        prefix: { subject_referent_id: null } });
      expect(source.calls.at(-1)?.body.prefix).toEqual({ subject_referent_id: null });
      await source.session.resolve({ catalogRef: fake.observation.catalog.catalog_ref, streamGeneration: fake.capture.stream_generation,
        expression: { verb: fake.actions[0]!.verb, subject_referent_id: fake.actions[0]!.subject_referent_id, arguments: [] } });
      const before = source.calls.length;
      await expect(source.session.resolve({ catalogRef: "catalog", streamGeneration: "stream", expression: { verb: "选择" } as any })).rejects.toThrow();
      expect(source.calls).toHaveLength(before);
    } finally { await source.controller.close(); }
  });

  it("reports a tiny page-budget failure explicitly without changing the relation", async () => {
    const fake = nativeScenario();
    const source = await nativeHarness(3, (url, body) => url.pathname.endsWith("/catalog") ? {
      ...fake.catalog(body), status: "page_budget_too_small", actions: [], next_cursor: null, minimum_required_bytes: 1024
    } : undefined);
    try {
      const page = await source.session.list({ catalogRef: fake.observation.catalog.catalog_ref, streamGeneration: fake.capture.stream_generation, maxPageBytes: 1 });
      expect(page.data.status).toBe("page_budget_too_small");
      expect(page.data.total_count).toBe(3);
      const budget = measuredBudget();
      await expect(source.session.getFullCurrent({ budget, maxPageBytes: 1 })).rejects.toThrow(/page_budget_too_small/u);
      expect(budget.active).toBe(0);
    } finally { await source.controller.close(); }
  });

  it("keeps pending distinct from terminal unknown and never automatically polls or submits again", async () => {
    let pending = true;
    const fake = nativeScenario();
    const source = await nativeHarness(3, (url, body) => url.pathname.includes("/actions") ? pending
      ? new Response(JSON.stringify({ error: { code: "request_pending", detail: "original remains in flight" } }), { status: 202 })
      : new Response(JSON.stringify(fake.route(url, body)), { status: 202 }) : undefined);
    try {
      expect(await source.session.submit({ requestId: "request-one", expectedSnapshotId: source.capture.snapshot_id, actionId: source.actions[0]!.action_id }))
        .toEqual({ status: "pending", requestId: "request-one" });
      expect(source.calls.filter(call => call.url.pathname === "/api/player-environment/actions")).toHaveLength(1);
      expect(await source.session.result("request-one")).toEqual({ status: "pending", requestId: "request-one" });
      pending = false;
      const result = await source.session.result("request-one");
      expect(result.status).toBe("terminal");
      if (result.status === "terminal") expect(result.result.data.delivery).toBe("unknown");
      expect(source.calls.filter(call => call.url.pathname === "/api/player-environment/actions")).toHaveLength(1);
      expect(source.calls.find(call => call.url.pathname === "/api/player-environment/actions")?.body).toMatchObject({
        request_id: "request-one", input_profile: "native-logical-v1", client_session_id: "client-fixture", controller_lease_id: "lease-fixture" });
    } finally { await source.controller.close(); }
  });

  it("assembles an intentionally scoped Current without forced catalog or a Full claim", async () => {
    const fixture = nativeFixture();
    const header = fixture.wire_samples.current_partial as any;
    const source = await nativeHarness(3, (url, body) => {
      const operation = url.pathname.split("/").at(-1);
      if (operation === "current") { expect(body.eager_scope).toEqual(["interaction"]); return header; }
      if (operation === "retain") return { schema: "sts2.player-environment/native-logical-retain-1", input_profile: "native-logical-v1",
        status: "retained", retention: { retention_handle_id: "partial-owned", capture: header.capture,
          read_cursor: "partial-fresh", expires_at: "1970-01-01T00:03:00+00:00" }, reason: null };
      if (operation === "read") return fixture.wire_samples.current_partial_read;
    });
    const budget = measuredBudget();
    try {
      const capture = await source.session.getCurrentCapture({ eagerScope: ["interaction"], budget });
      expect(capture.observation.completeness.full_reference_complete).toBe(false);
      expect(capture.observation.completeness.missing).toEqual(["persistent", "referents", "catalog"]);
      expect(capture.observation.persistent).toBeNull();
      expect(capture.observation.catalog.status).toBe("not_captured");
      expect("actions" in capture).toBe(false);
      expect(source.calls.some(call => call.url.pathname.endsWith("/catalog"))).toBe(false);
      await capture.dispose();
      expect(budget.active).toBe(0);
      expect(source.calls.at(-1)?.body.retention_handle_id).toBe("partial-owned");
    } finally { await source.controller.close(); }
  });

  it("rejects a complete body that does not match its requested omitted scope", async () => {
    const source = await nativeHarness();
    const budget = measuredBudget();
    try {
      await expect(source.session.getCapture({ capture: source.capture, eagerScope: ["interaction"], budget }))
        .rejects.toThrow(/requested scope/u);
      expect(budget.active).toBe(0);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/release"))).toHaveLength(1);
      expect(source.calls.some(call => call.url.pathname.endsWith("/catalog"))).toBe(false);
    } finally { await source.controller.close(); }
  });

  it("cleans a scoped capture after failure halfway through its immutable bytes", async () => {
    const fake = nativeScenario();
    let reads = 0;
    const source = await nativeHarness(3, (url, body) => {
      if (url.pathname.endsWith("/read")) {
        if (++reads === 2) throw new Error("partial transfer failed");
        return fake.read(body);
      }
    });
    const budget = measuredBudget();
    try {
      await expect(source.session.getCapture({ capture: source.capture, eagerScope: NATIVE_LOGICAL_SCOPE, chunkBytes: 1024, budget }))
        .rejects.toThrow(/partial transfer failed/u);
      expect(budget.active).toBe(0);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/release"))).toHaveLength(1);
      expect(source.calls.some(call => call.url.pathname.endsWith("/catalog"))).toBe(false);
    } finally { await source.controller.close(); }
  });

  it("rejects a whole catalog above the consumer limit before pages or scores are allocated", async () => {
    const source = await nativeHarness(500);
    const budget = measuredBudget();
    try {
      await expect(source.session.getFullCurrent({ maxActions: 200, budget })).rejects.toThrow(/maxActions capacity/u);
      expect(source.calls.some(call => call.url.pathname.endsWith("/catalog"))).toBe(false);
      expect(budget.active).toBe(0);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/release"))).toHaveLength(1);
    } finally { await source.controller.close(); }
  });

  it.each(["session", "context"] as const)("releases a transferred reader pin when %s admission fails", async field => {
    const source = await nativeHarness();
    try {
      const capture = field === "session" ? { ...source.capture,
        session: { ...source.capture.session, runtime_instance_id: "foreign-runtime" } } : source.capture;
      const context = field === "context" ? { ...source.context, schema: "invalid" } as any : source.context;
      await expect(source.session.getFull({ capture, context, retention: source.retention })).rejects.toThrow();
      expect(source.calls.filter(call => call.url.pathname.endsWith("/release"))).toHaveLength(1);
      expect(source.calls.some(call => call.url.pathname.endsWith("/read"))).toBe(false);
      expect(source.calls.some(call => call.url.pathname.endsWith("/retain"))).toBe(false);
    } finally { await source.controller.close(); }
  });
});
