import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { vi } from "vitest";
import {
  digestNativeLogicalActions, EnvironmentControllerSession, NativeLogicalSession, PlayerEnvironmentRestClient,
  type NativeLogicalAction, type NativeLogicalCapture, type NativeLogicalCatalogPage, type NativeLogicalContext,
  type NativeLogicalObservation, type NativeLogicalByteBudget, type JsonObject
} from "../src/index.js";

export function nativeFixture(): {
  digest_cases: { name: string; actions: NativeLogicalAction[]; sha256: string }[];
  expression_cases: { expression: JsonObject; prefix_valid: boolean; resolve_valid: boolean }[];
  wire_samples: Record<string, JsonObject>;
  invalid_wire_cases: { name: string; type: string; wire: JsonObject }[];
} {
  return JSON.parse(readFileSync(new URL("../../../contracts/fixtures/native-logical-v1.json", import.meta.url), "utf8"));
}

export function nativeScenario(count = 3) {
  const fixture = nativeFixture();
  const observation = structuredClone(fixture.wire_samples.observation) as unknown as NativeLogicalObservation;
  const actions: NativeLogicalAction[] = Array.from({ length: count }, (_, index) => ({ action_id: `action-${index}`,
    kind: "native_input", verb: "选择", label: `动作 ${index} / 🐉 / café / é\r\n保留`,
    subject_referent_id: `public-${index}`, arguments: [], effect_domain: "native" }));
  const digest = digestNativeLogicalActions(actions);
  observation.catalog.total_count = count;
  observation.catalog.digest = digest;
  observation.status = count === 0 ? "settling" : "interactive";
  observation.referents = actions.map(action => ({ referent_id: action.subject_referent_id!, role: "control", kind: "control",
    label: action.label, state: { visible: true, enabled: true, selected: null, focused: null, observation_basis: "native_visible_fact" },
    properties_schema: null, properties: null }));
  (observation.persistent!.content as { text: string }).text = "中文完整 / 🐉 / café / é\n保留" + "x".repeat(2048);
  const before = JSON.stringify(observation).split("中文完整")[0]!;
  const padding = (1023 - Buffer.byteLength(before, "utf8") % 1024 + 1024) % 1024;
  (observation.persistent!.content as { text: string }).text = "x".repeat(padding) + (observation.persistent!.content as { text: string }).text;
  const serialized = JSON.stringify(observation);
  const bytes = Buffer.from(serialized, "utf8");
  const capture = structuredClone(fixture.wire_samples.capture) as unknown as NativeLogicalCapture;
  capture.byte_count = bytes.length;
  capture.sha256 = createHash("sha256").update(bytes).digest("hex");
  const context = structuredClone(fixture.wire_samples.observation_context) as unknown as NativeLogicalContext;
  const retention = { retention_handle_id: "retention-owned", capture, read_cursor: "fresh-reader-0", expires_at: "1970-01-01T00:03:00+00:00" };
  const read = (body: JsonObject) => {
    const cursor = String(body.cursor);
    const offset = cursor === capture.read_cursor ? 0 : Number(cursor.slice(cursor.lastIndexOf("-") + 1));
    const block = bytes.subarray(offset, offset + Number(body.max_bytes));
    const end = offset + block.length;
    return { schema: "sts2.player-environment/native-logical-read-1", capture_id: capture.capture_id,
      sha256: capture.sha256, offset, total_bytes: bytes.length, data_base64: block.toString("base64"),
      next_cursor: end === bytes.length ? null : `fresh-reader-${end}`, complete: end === bytes.length };
  };
  const catalog = (body: JsonObject): NativeLogicalCatalogPage => {
    const start = body.cursor === null ? 0 : Number(String(body.cursor).slice("page-".length));
    const selected = actions.slice(start, start + Number(body.limit));
    const end = start + selected.length;
    return { schema: "sts2.player-environment/native-logical-catalog-page-1", status: "complete",
      catalog_ref: observation.catalog.catalog_ref, snapshot_id: capture.snapshot_id, stream_generation: capture.stream_generation,
      total_count: count, digest, filtered_count: count, filtered_digest: digest, actions: selected,
      next_cursor: end < count ? `page-${end}` : null, minimum_required_bytes: null };
  };
  const current = { schema: "sts2.player-environment/native-logical-current-1", input_profile: "native-logical-v1", status: "captured",
    context, capture, retention: null, reason: null };
  const route = (url: URL, body: JsonObject): unknown => {
    const operation = url.pathname.split("/").at(-1)!;
    if (url.pathname.endsWith("/clients/register")) return { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
      runtime_instance_id: capture.session.runtime_instance_id,
      client: { client_session_id: "client-fixture", client_instance_id: body.client_instance_id }, controller: null };
    if (url.pathname.includes("/controller/")) return { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
      runtime_instance_id: capture.session.runtime_instance_id, status: `controller_${operation === "release" ? "released" : "acquired"}`,
      detail: "synthetic control", client: null, controller: operation === "release" ? null : {
        controller_lease_id: "lease-fixture", controller_generation: 1, client_session_id: "client-fixture",
        expires_at: new Date(Date.now() + 60000).toISOString() } };
    if (operation === "capabilities") return fixture.wire_samples.capabilities;
    if (operation === "attach") return fixture.wire_samples.attach;
    if (operation === "current") return current;
    if (operation === "read") return read(body);
    if (operation === "catalog") return catalog(body);
    if (operation === "retain") return { schema: "sts2.player-environment/native-logical-retain-1", input_profile: "native-logical-v1",
      status: "retained", retention, reason: null };
    if (operation === "release") return { schema: "sts2.player-environment/native-logical-release-1", input_profile: "native-logical-v1",
      status: "released", retention_handle_id: body.retention_handle_id, released: true, reason: null };
    if (operation === "events") return fixture.wire_samples.event_batch;
    if (operation === "await") return fixture.wire_samples.await;
    if (operation === "renew") return fixture.wire_samples.renew;
    if (operation === "cancel_wait") return { ...fixture.wire_samples.cancel_wait, wait_id: body.wait_id, subscription_id: body.subscription_id };
    if (operation === "detach") return { ...fixture.wire_samples.detach, subscription_id: body.subscription_id };
    if (operation === "resolve") return { schema: "sts2.player-environment/native-logical-resolve-1", status: "unique", action: actions[0] };
    if (operation === "actions" || url.pathname.includes("/actions/")) return { ...fixture.wire_samples.result,
      request_id: body.request_id ?? operation, snapshot_id: capture.snapshot_id, action: actions[0] ?? null,
      delivery: "unknown", execution: "unknown", effect: "unknown", stages: [] };
    throw new Error(`unhandled synthetic operation ${operation}`);
  };
  return { fixture, observation, actions, serialized, bytes, capture, context, retention, current, read, catalog, route };
}

export async function nativeHarness(count = 3, override?: (url: URL, body: JsonObject, init: RequestInit) => unknown | Promise<unknown>) {
  const source = nativeScenario(count);
  const calls: { url: URL; body: JsonObject; init: RequestInit }[] = [];
  const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
    const parsed = new URL(url);
    const body: JsonObject = init.body ? JSON.parse(String(init.body)) : {};
    calls.push({ url: parsed, body, init });
    const modified = await override?.(parsed, body, init);
    if (modified instanceof Response) return modified;
    return new Response(JSON.stringify(modified ?? source.route(parsed, body)), { headers: { "content-type": "application/json" } });
  });
  const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000, fetchImpl as typeof fetch);
  const controller = new EnvironmentControllerSession(client, { productId: "synthetic-sdk", productName: "Synthetic SDK", productVersion: "1", clientInstanceId: "sdk-fixture" });
  const session = new NativeLogicalSession(client, controller);
  const capabilities = (await session.capabilities()).data;
  await controller.register(capabilities.session, capabilities.control_policy);
  return { ...source, calls, fetchImpl, client, controller, session, capabilities };
}

export function measuredBudget(): NativeLogicalByteBudget & { active: number; peak: number; calls: [string, number][] } {
  const budget = {
    active: 0, peak: 0, calls: [] as [string, number][],
    reserve(input: { kind: "observation" | "catalog_page"; bytes: number }) {
      budget.calls.push([input.kind, input.bytes]);
      budget.active += input.bytes;
      budget.peak = Math.max(budget.peak, budget.active);
      let held = input.bytes;
      let released = false;
      return {
        resize(bytes: number) { if (released || bytes > held) throw new Error("invalid reservation resize"); budget.active += bytes - held; held = bytes; },
        release() { if (released) throw new Error("reservation released twice"); released = true; budget.active -= held; }
      };
    }
  };
  return budget;
}
