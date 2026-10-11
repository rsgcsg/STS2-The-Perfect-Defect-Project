// Synthetic Environment only. Real packaged CLI/HTTP/controller/ports/writer execute.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { publicSnapshot } from "../../components/policy-runtime/test/fixtures/public-observation-fixture.mjs";
const [cli, fixture, ...args] = process.argv.slice(2);
const { digestNativeLogicalActions } = await import(new URL(
  "../node_modules/@rsgcsg/sts2-connector-client/dist/index.js", pathToFileURL(cli)).href);
const emptyCatalogDigest = digestNativeLogicalActions([]);
const { capabilities, profile, result } = JSON.parse(readFileSync(fixture, "utf8"));
const source = JSON.parse(readFileSync(new URL("../../components/connector/contracts/fixtures/native-logical-v1.json", import.meta.url))).wire_samples;
const clone = value => structuredClone(value);
let client = null, lease = null, current = publicSnapshot(), ordinal = 0;
current.session.runtime_instance_id = capabilities.host.runtime_instance_id;
current.session.environment_fingerprint = capabilities.environment_fingerprint;
const captures = new Map();
const control = { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
  runtime_instance_id: capabilities.host.runtime_instance_id ?? capabilities.session.runtime_instance_id };
const response = value => new Response(JSON.stringify(value), { status: 200,
  headers: { "content-type": "application/json" } });
globalThis.fetch = async (url, init = {}) => {
  const method = new URL(url).pathname.split("/").at(-1);
  const body = init.body ? JSON.parse(init.body) : null;
  if (method === "capabilities") return response(capabilities);
  if (method === "register") {
    client = { client_session_id: "fixture-client", client_instance_id: body.client_instance_id };
    return response({ ...control, client, controller: null });
  }
  if (method === "acquire" || method === "renew" && new URL(url).pathname.includes("controller")) {
    lease = { controller_lease_id: "fixture-lease", controller_generation: 1,
      client_session_id: client.client_session_id, expires_at: new Date(Date.now() + 60000).toISOString() };
    return response({ ...control, status: "controller_acquired", detail: "synthetic", controller: lease, client });
  }
  if (method === "controller") return response({ ...control, clients: client ? [client] : [], controller: lease });
  if (method === "release" && new URL(url).pathname.includes("controller")) {
    lease = null;
    return response({ ...control, status: "controller_released", detail: "synthetic", controller: null, client });
  }
  if (method === "snapshot") return response(current);
  if (method === "actions") {
    assert.equal(body.expected_snapshot_id, current.snapshot_id);
    const action = current.bound_actions.actions.find(a => a.bound_action_id === body.bound_action_id);
    assert.ok(action);
    current = { ...current, snapshot_id: "snapshot-2", sequence: 2 };
    current.bound_actions = { ...current.bound_actions,
      actions: current.bound_actions.actions.map(a => ({ ...a })) };
    return response({ protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1",
      request_id: body.request_id, delivery: "delivered", action: { ...action, label: undefined,
        interaction_id: undefined }, reason_code: null, detail: null,
      retry: { allowed: false, reason: "never" }, successor: current, attribution: null });
  }
  if (method === "attach") return response({ ...clone(source.attach), subscription: {
    ...clone(source.attach.subscription), eager_scope: [], delivery_mode: "scoped",
    coverage: profile.required_seams, expires_at: new Date(Date.now() + 60000).toISOString() } });
  if (method === "events") return response({ ...clone(source.event_batch), events: [],
    next_cursor: source.attach.subscription.starting_cursor, high_watermark: source.attach.subscription.starting_cursor,
    retained_start_cursor: source.attach.subscription.starting_cursor });
  if (method === "current" || method === "current_owned") {
    ordinal++;
    const observation = clone(source.observation);
    observation.snapshot_id = "summary-" + ordinal; observation.revision = ordinal;
    observation.status = "interactive"; observation.referents = [];
    observation.interaction = { ...observation.interaction, kind: "game_over", stage: "summary",
      content_schema: "sts2.player-environment/surface/game_over-1",
      content: { context: { kind: "game_over", result, game_mode: "standard", score: 10,
        floor_reached: 1, ascension: 0 }, surface: { kind: "game_over", stage: "summary",
        can_return: true, can_advance_summary: false, return_destination: "timeline" } } };
    const digest = emptyCatalogDigest;
    observation.catalog = { ...observation.catalog, snapshot_id: observation.snapshot_id,
      catalog_ref: "catalog-" + ordinal, total_count: 0, digest };
    const raw = Buffer.from(JSON.stringify(observation));
    const capture = { ...clone(source.capture), capture_id: "capture-" + ordinal,
      snapshot_id: observation.snapshot_id, capture_ordinal: String(ordinal), byte_count: raw.length,
      sha256: createHash("sha256").update(raw).digest("hex"), read_cursor: "read-" + ordinal };
    const retention = { ...clone(source.retain.retention), retention_handle_id: "retention-" + ordinal,
      capture, read_cursor: "retained-" + ordinal };
    captures.set(capture.capture_id, { capture, raw, retention, observation });
    return response({ ...clone(source.current_retained), capture, retention,
      context: { ...clone(source.observation_context), capture_ref: capture.capture_id,
        observation_ref: observation.snapshot_id } });
  }
  if (method === "read") {
    const value = captures.get(body.capture_id);
    return response({ ...clone(source.read), capture_id: value.capture.capture_id,
      sha256: value.capture.sha256, total_bytes: value.raw.length, data_base64: value.raw.toString("base64") });
  }
  if (method === "catalog") return response({ ...clone(source.catalog_page),
    snapshot_id: "summary-" + ordinal, catalog_ref: body.catalog_ref, total_count: 0,
    filtered_count: 0, digest: emptyCatalogDigest,
    filtered_digest: emptyCatalogDigest, actions: [] });
  if (method === "release") return response({ ...clone(source.release), retention_handle_id: body.retention_handle_id });
  if (method === "detach") return response(source.detach);
  throw new Error("unexpected synthetic Environment route " + method);
};
process.argv = [process.argv[0], cli, ...args];
await import(pathToFileURL(cli).href);
