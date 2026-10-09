/** Public SDK/consumer conformance. Only the public fetch and Host owners are injected. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";
import * as sdk from "../../components/connector/sdk/typescript/dist/index.js";
import { createCollectionLifetime, createPipePeer, PIPE_SCHEMA, runNativeSource3 }
  from "../native-source3-collector.mjs";

const fixturePath = new URL("../../components/connector/contracts/fixtures/native-logical-v1.json", import.meta.url);
const fixtureBytes = await readFile(fixturePath);
const fixture = JSON.parse(fixtureBytes);
const wire = fixture.wire_samples;
const catalog = fixture.digest_cases.find(value => value.actions.length > 0).actions;
const fixtureDigest = createHash("sha256").update(fixtureBytes).digest("hex");
const operation = "a".repeat(32);
const endpoint = "http://127.0.0.1:15526"; // In-memory fetch only; never a socket or actual endpoint.
const runtime = wire.capabilities.session.runtime_instance_id;
const exitReceipt = { code: 0, signal: null, forced: false };
const clone = value => structuredClone(value);
const response = value => new Response(JSON.stringify(value), { status: 200,
  headers: { "Content-Type": "application/json" } });
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function collect(t, { renewal = false, generationDrift = false } = {}) {
  const output = await mkdtemp(path.join(os.tmpdir(), "source3-public-sdk-"));
  t.after(() => rm(output, { recursive: true, force: true }));
  const seen = { requests: [], pipe: [], submits: [], retentionReleases: [], hostCloses: 0,
    sourceCloses: 0, observations: [], eventCursors: [], awaitCursors: [], renewCursors: [] };
  const captures = new Map(), retentions = new Map(), pages = new Map();
  let client = null, lease = null, currentOrdinal = 0, eventOrdinal = 0;
  let activeCursor = wire.attach.subscription.starting_cursor;
  let renewedCursor = null;
  const control = { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
    runtime_instance_id: runtime };
  const capabilities = { ...clone(wire.capabilities),
    capture_coverage: clone(sdk.NATIVE_LOGICAL_PUBLICATION_PROFILE.required_seams) };
  if (renewal) capabilities.limits.retention_ms = 1000;
  const subscription = { ...clone(wire.attach.subscription), eager_scope: [], delivery_mode: "scoped",
    coverage: clone(capabilities.capture_coverage) };
  // This strict authoritative response intentionally has NO top-level next_cursor.
  const attached = { ...clone(wire.attach), subscription };
  assert.deepEqual(Object.keys(attached).sort(), Object.keys(wire.attach).sort());
  assert.equal(Object.hasOwn(attached, "next_cursor"), false);

  const freshCurrent = () => {
    currentOrdinal++;
    const number = currentOrdinal;
    const observation = { ...clone(wire.observation), snapshot_id: `snapshot-${number}`,
      revision: number, status: "interactive" };
    observation.catalog = { ...observation.catalog, snapshot_id: observation.snapshot_id,
      catalog_ref: `catalog-${number}`, total_count: catalog.length,
      digest: sdk.digestNativeLogicalActions(catalog) };
    // Preserve the WHOLE multilingual/null/empty/ordered source catalog and provide
    // every subject/argument's current public referent. Nothing is filtered for the teacher.
    const ids = new Set(catalog.flatMap(action => [action.subject_referent_id,
      ...action.arguments.map(argument => argument.referent_id)]).filter(value => value !== null));
    observation.referents = [...ids].map(referent_id => ({ referent_id, role: "public-fixture",
      kind: "entity", label: referent_id, state: { visible: true, enabled: true, selected: false,
        focused: false, observation_basis: "synthetic_public_fixture" }, properties_schema: null, properties: null }));
    const bytes = Buffer.from(JSON.stringify(observation));
    const capture = { ...clone(wire.capture), capture_id: `capture-${number}`,
      snapshot_id: observation.snapshot_id, capture_ordinal: String(number),
      byte_count: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"),
      read_cursor: `read-${number}` };
    const context = { ...clone(wire.observation_context), capture_ref: capture.capture_id,
      observation_ref: observation.snapshot_id };
    const retained = { ...clone(wire.retain.retention), retention_handle_id: `retention-${number}`,
      capture, read_cursor: `retained-read-${number}` };
    captures.set(capture.capture_id, { capture, bytes, retained });
    retentions.set(retained.retention_handle_id, capture.capture_id);
    pages.set(observation.catalog.catalog_ref, { ...clone(wire.catalog_page),
      catalog_ref: observation.catalog.catalog_ref, snapshot_id: observation.snapshot_id,
      total_count: catalog.length, filtered_count: catalog.length, digest: observation.catalog.digest,
      filtered_digest: observation.catalog.digest, actions: clone(catalog) });
    seen.observations.push(observation);
    return { ...clone(wire.current_retained), context, capture, retention: retained };
  };

  const fetchFixture = async (url, init) => {
    const parsed = new URL(url);
    assert.equal(parsed.origin, endpoint);
    const body = init.body === undefined ? undefined : sdk.parseNativeLogicalJson(init.body);
    seen.requests.push({ path: parsed.pathname, method: init.method, body });
    if (parsed.pathname === "/api/player-environment/clients/register") {
      assert.equal(init.method, "POST");
      assert.equal(client, null, "only one original client registration");
      client = { client_session_id: "client-fixture", client_instance_id: body.client_instance_id };
      return response({ ...control, client, controller: null });
    }
    if (parsed.pathname === "/api/player-environment/controller/acquire") {
      assert.equal(init.method, "POST");
      assert.deepEqual(body, { client_session_id: client.client_session_id });
      assert.equal(lease, null);
      lease = { controller_lease_id: "lease-fixture", controller_generation: 1,
        client_session_id: client.client_session_id, expires_at: new Date(Date.now() + 60_000).toISOString() };
      return response({ ...control, status: "controller_acquired", detail: "synthetic exact lease", controller: lease });
    }
    if (parsed.pathname === "/api/player-environment/controller/release") {
      assert.equal(init.method, "POST");
      assert.deepEqual(body, { client_session_id: client.client_session_id,
        controller_lease_id: lease.controller_lease_id, controller_generation: lease.controller_generation });
      lease = null;
      return response({ ...control, status: "controller_released", detail: "synthetic original release", controller: null });
    }
    if (parsed.pathname === "/api/player-environment/controller") {
      assert.equal(init.method, "GET");
      return response({ ...control, clients: client ? [client] : [], controller: lease });
    }
    if (parsed.pathname === "/api/player-environment/actions") {
      assert.equal(init.method, "POST");
      assert.equal(body.client_session_id, client.client_session_id);
      assert.equal(body.controller_lease_id, lease.controller_lease_id);
      assert.equal(body.controller_generation, lease.controller_generation);
      const observation = seen.observations.at(-1);
      assert.equal(body.expected_snapshot_id, observation.snapshot_id);
      const action = catalog.find(value => value.action_id === body.bound_action_id);
      assert.ok(action, "submit must bind an original complete-C member");
      assert.ok(!seen.submits.some(value => value.request_id === body.request_id), "no automatic resubmit");
      seen.submits.push(body);
      return response({ ...clone(wire.result), request_id: body.request_id,
        snapshot_id: observation.snapshot_id, action: clone(action), delivery: "delivered",
        execution: "unknown", effect: "unknown", cancel: "not_requested", stages: [], reason: null });
    }
    const prefix = "/api/player-environment/native-logical/";
    assert.ok(parsed.pathname.startsWith(prefix), `unexpected transport route ${parsed.pathname}`);
    const method = parsed.pathname.slice(prefix.length);
    assert.equal(init.method, method === "capabilities" ? "GET" : "POST");
    if (method === "capabilities") return response(capabilities);
    if (method === "attach") {
      assert.deepEqual(body, { client_session_id: client.client_session_id, eager_scope: [],
        required_seams: sdk.NATIVE_LOGICAL_PUBLICATION_PROFILE.required_seams, delivery_mode: "scoped" });
      return response(attached);
    }
    if (method === "current") {
      assert.deepEqual(body.eager_scope, sdk.NATIVE_LOGICAL_SCOPE);
      assert.equal(body.client_session_id, client.client_session_id);
      return response(freshCurrent());
    }
    if (method === "read") {
      const { capture, bytes, retained } = captures.get(body.capture_id);
      assert.equal(body.cursor, retained.read_cursor);
      assert.ok(bytes.length <= body.max_bytes);
      return response({ ...clone(wire.read), capture_id: capture.capture_id, sha256: capture.sha256,
        total_bytes: bytes.length, data_base64: bytes.toString("base64") });
    }
    if (method === "catalog") {
      assert.equal(body.prefix, null, "whole C has no consumer filter");
      assert.equal(body.cursor, null);
      assert.equal(body.stream_generation, capabilities.stream_generation);
      return response(pages.get(body.catalog_ref));
    }
    if (method === "release") {
      assert.equal(body.client_session_id, client.client_session_id);
      assert.ok(retentions.has(body.retention_handle_id));
      retentions.delete(body.retention_handle_id);
      seen.retentionReleases.push(body.retention_handle_id);
      return response({ ...clone(wire.release), retention_handle_id: body.retention_handle_id });
    }
    if (method === "events") {
      // With the old collector, the real SDK rejects undefined after_cursor BEFORE
      // this transport branch. Do not add that invented field to Attach to mask it.
      assert.deepEqual(Object.keys(body).sort(), Object.keys(wire.renew_request).concat("limit").sort());
      assert.equal(body.after_cursor, activeCursor);
      assert.equal(body.subscription_id, subscription.subscription_id);
      assert.equal(body.scope_id, subscription.scope_id);
      seen.eventCursors.push(body.after_cursor);
      eventOrdinal++;
      if (renewal && eventOrdinal === 1) await delay(1050);
      activeCursor = `opaque-event-batch-${eventOrdinal}`;
      return response({ ...clone(wire.event_batch), events: [], next_cursor: activeCursor,
        high_watermark: activeCursor, retained_start_cursor: subscription.starting_cursor });
    }
    if (method === "renew") {
      assert.equal(body.after_cursor, activeCursor);
      seen.renewCursors.push(body.after_cursor);
      renewedCursor = "opaque-event-renewed-next";
      activeCursor = renewedCursor;
      return response({ ...clone(wire.renew), subscription: { ...clone(subscription),
        expires_at: wire.renew.subscription.expires_at,
        stream_generation: generationDrift ? "changed-generation" : subscription.stream_generation },
      next_cursor: activeCursor, high_watermark: activeCursor,
      retained_start_cursor: subscription.starting_cursor });
    }
    if (method === "await") {
      assert.equal(body.after_cursor, activeCursor);
      assert.match(body.wait_id, /^[0-9a-f]{32}$/u);
      assert.equal(body.condition, "any_event");
      assert.equal(body.control_binding, null);
      assert.ok(body.timeout_ms <= capabilities.limits.max_wait_ms);
      seen.awaitCursors.push(body.after_cursor);
      return response({ ...clone(wire.await), status: "timeout", event: null });
    }
    if (method === "detach") {
      assert.deepEqual(body, { client_session_id: client.client_session_id,
        subscription_id: subscription.subscription_id });
      return response(wire.detach);
    }
    assert.fail(`unexpected native method ${method}`);
  };

  // The actual public REST class performs serialization, response byte bounds and
  // strict decoders. NativeLogicalSession and ControllerSession methods are untouched.
  function FixtureRestClient(base, timeout) {
    const actual = new sdk.PlayerEnvironmentRestClient(base, timeout, fetchFixture);
    assert.ok(actual instanceof sdk.PlayerEnvironmentRestClient);
    return actual;
  }
  const deps = { ...sdk, PlayerEnvironmentRestClient: FixtureRestClient, resolveInstallation: value => value,
    async startEpisode() {
      return { identity: { endpoint, host: { runtime_instance_id: runtime } },
        async releaseController() { return { controller: null }; },
        async close() { seen.hostCloses++; return exitReceipt; } };
    } };
  assert.equal(deps.NativeLogicalSession, sdk.NativeLogicalSession);
  assert.equal(deps.EnvironmentControllerSession, sdk.EnvironmentControllerSession);
  const options = { installation: "/synthetic/game", host_local_root: "/synthetic/host", output,
    endpoint, seed: "SYNTHETIC", template_id: "defect-a0-s0", target_choices: 2, max_submissions: 2,
    deadline_ms: 10_000, max_input_bytes: 64 * 1024 * 1024, max_diagnostic_bytes: 1024 * 1024,
    experimental_build_acknowledged: false, experimental_connector_acknowledged: false };
  const lifetime = createCollectionLifetime(), parentLifetime = createCollectionLifetime();
  const input = new PassThrough(), outputPipe = new PassThrough();
  const peer = createPipePeer(input, outputPipe, sdk.parseNativeLogicalJson, lifetime);
  const parent = createPipePeer(outputPipe, input, sdk.parseNativeLogicalJson, parentLifetime);
  const parentLoop = (async () => {
    for (;;) {
      const message = await parent.receive({ timeoutMs: 5000 });
      seen.pipe.push(message);
      if (message.type === "closed") return message;
      const common = { schema: PIPE_SCHEMA, operation_id: operation, message_id: message.message_id };
      if (message.type === "ready") await parent.send({ ...common, type: "source_ready", source_context: {
        runtime_instance_id: runtime, recording_session_id: "source-session", source_segment_id: "segment",
        source_epoch_id: "epoch", declaration: { source_kind: "agent_protocol", actor_id: "explicit-fixture",
          declaration_id: "declaration", machine_verifiable: false } } });
      else if (message.type === "current") {
        assert.deepEqual(message.catalog, catalog);
        assert.equal(message.basis.catalog_digest, sdk.digestNativeLogicalActions(catalog));
        await parent.send({ ...common, type: "choice", ordinal: message.ordinal, basis: message.basis,
          action_id: catalog.at(-1).action_id, stop_reason: null, teacher_state: { learned: false } });
      } else if (message.type === "result") {
        assert.equal(message.lookup_status, "terminal");
        assert.equal(message.result.delivery, "delivered");
        assert.equal(message.result.effect, "unknown");
        await parent.send({ ...common, type: "continue", continue: true, reason: "known_original_source" });
      } else if (message.type === "quiesced") {
        seen.sourceCloses++;
        await parent.send({ ...common, type: "source_closed", known_closed: true,
          source_status: { recording_lifecycle: "closed" }, close_outcome: "known_closed" });
      } else assert.fail(`unexpected collector pipe request ${message.type}`);
    }
  })();
  const final = await runNativeSource3(options, operation, peer, deps, lifetime);
  assert.deepEqual(await parentLoop, final);
  input.destroy(); outputPipe.destroy();
  assert.equal(createHash("sha256").update(await readFile(fixturePath)).digest("hex"), fixtureDigest);
  assert.equal(retentions.size, 0, "every real SDK full capture released its original retention");
  assert.equal(seen.hostCloses, 1);
  assert.equal(seen.sourceCloses, 1);
  assert.equal(seen.requests.filter(request => request.path.endsWith("controller/release")).length, 1);
  assert.equal(final.control_release.confirmed, true);
  assert.deepEqual(final.host_exit, exitReceipt);
  assert.equal(final.source_closed, true);
  assert.equal(final.live_byte_reservations, 0);
  return { final, seen, renewedCursor };
}

test("real SDK starting_cursor drives two complete choices through empty Events/Await and owned cleanup", async t => {
  const { final, seen } = await collect(t);
  assert.equal(final.reason, "target_choices_reached",
    JSON.stringify({ reason: final.reason, submissions: final.counts.submissions, eventRequests: seen.eventCursors.length }));
  assert.equal(final.counts.submissions, 2);
  assert.equal(seen.submits.length, 2);
  assert.equal(seen.eventCursors[0], wire.attach.subscription.starting_cursor);
  assert.deepEqual(seen.awaitCursors, ["opaque-event-batch-1", "opaque-event-batch-2"]);
  assert.equal(final.advisory.wake_timeouts, 2);
  assert.equal(seen.retentionReleases.length, 2);
  assert.equal(final.advisory.history_claimed, false);
  assert.deepEqual(final.cleanup_errors, []);
});

test("real SDK renewal preserves subscription while the returned next_cursor drives Await and next Events", async t => {
  const { final, seen, renewedCursor } = await collect(t, { renewal: true });
  assert.equal(final.reason, "target_choices_reached");
  assert.equal(seen.renewCursors.length, 1);
  assert.equal(seen.renewCursors[0], "opaque-event-batch-1");
  assert.equal(seen.awaitCursors[0], renewedCursor);
  assert.equal(seen.eventCursors[1], renewedCursor);
  assert.equal(final.counts.submissions, 2);
});

test("real SDK rejects renewal generation drift after one choice and still releases its original owners", async t => {
  const { final, seen } = await collect(t, { renewal: true, generationDrift: true });
  assert.equal(seen.renewCursors.length, 1);
  assert.equal(final.counts.submissions, 1);
  assert.equal(seen.submits.length, 1);
  assert.equal(seen.awaitCursors.length, 0);
  assert.notEqual(final.reason, "target_choices_reached");
  assert.equal(seen.retentionReleases.length, 1);
});
