#!/usr/bin/env node
/** Test-only subprocess: actual C# Store/Projector -> HTTP SDK -> Runtime ->
 * production program Teacher/collector. Host/control/native results are SYN.
 * No game, native Service/Executor, Source recording or research admission. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as sdk from "../../components/connector/sdk/typescript/dist/index.js";
import * as runtimeApi from "../../components/policy-runtime/dist/index.js";
import { openRealStoreTransport } from "../../components/connector/sdk/typescript/test/realStoreTransport.mjs";
import { createCollectionLifetime, createPipePeer, OWNED_PIPE_SCHEMA, runNativeSource3,
  verifiedTerminalSummary } from "../native-source3-collector.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const wire = JSON.parse(await readFile(path.join(ROOT, "components/connector/contracts/fixtures/native-logical-v1.json"), "utf8")).wire_samples;
const clone = value => structuredClone(value);
const runtimeId = "runtime-real-chain-SYN", environmentId = "environment-real-chain-SYN", stream = "stream-real-chain-SYN";
const lifetime = createCollectionLifetime();
process.on("SIGTERM", () => lifetime.stop("external_signal"));
process.on("SIGINT", () => lifetime.stop("external_signal"));
const peer = createPipePeer(process.stdin, process.stdout, sdk.parseNativeLogicalJson, lifetime);

const referent = (id, role, properties = null) => ({ referent_id: id, role, kind: "entity", label: id,
  state: { visible: true, enabled: true, selected: false, focused: false, observation_basis: "SYN_public_fixture" },
  properties_schema: null, properties });
const leaf = (verb, subject = null) => ({ verb, label: verb, subject_referent_id: subject,
  arguments: [], effect_domain: "native" });
function publicFrame(index, incomplete = false) {
  const map = index === 1;
  return { stream_generation: stream, session: { runtime_instance_id: runtimeId, environment_fingerprint: environmentId },
    owner_occurrence: { owner_id: "SYN_owner", occurrence_id: String(index), binding_revision: String(index),
      focus_referent_id: null, focus_occurrence: null }, status: "interactive",
    persistent: { content_schema: "SYN_persistent", content: { value: 1 } },
    interaction: { interaction_id: "SYN_interaction:" + index, kind: map ? "native_map" : "run_deck", stage: "ready",
      prompt: "SYN public page", content_schema: "SYN_surface", capabilities: [],
      content: { surface: map ? { kind: "map_navigation", next_options: [{ entity_id: "map-point", point_type: "monster" }] }
        : { kind: "run_deck" }, context: { kind: "SYN_public_context" } } },
    referents: map ? [referent("map-point", "map_point")]
      : [referent("public-card", "card", { definition_id: "SYN_DEFEND", cost: index === 2 ? "1" : "2", description: "SYN displayed card" })],
    information_policy: clone(wire.observation.information_policy),
    leaves: map ? [leaf("open_run_deck"), leaf("activate", "map-point")]
      : [leaf("inspect_deck_card", "public-card"), leaf("return_native_information")],
    source_completeness: { status: incomplete ? "partial" : "complete", missing: incomplete ? ["SYN_required_public_fact"] : [] } };
}

async function compiledRuntimeIdentity() {
  const directory = path.join(ROOT, "components/policy-runtime/dist"), digest = createHash("sha256");
  for (const name of (await readdir(directory)).filter(name => name.endsWith(".js")).sort())
    digest.update(name).update("\0").update(await readFile(path.join(directory, name))).update("\0");
  return { version: runtimeApi.POLICY_RUNTIME_VERSION, code_sha256: digest.digest("hex") };
}

let transport;
let phase = "awaiting_parent_init", scenario;
try {
  const init = await peer.receive();
  assert.deepEqual(Object.keys(init).sort(), ["operation_id", "options", "schema", "type"]);
  assert.equal(init.schema, OWNED_PIPE_SCHEMA); assert.equal(init.type, "init");
  const options = init.options;
  scenario = options.seed;
  assert.ok(["SYN_REAL_CHAIN_COMPLETE", "SYN_REAL_CHAIN_CURRENT_GAP", "SYN_REAL_CHAIN_NATIVE_UNKNOWN"].includes(scenario));
  assert.equal(options.record_source3, false);
  const endpoint = new URL(options.endpoint);
  assert.equal(endpoint.hostname, "127.0.0.1");
  const c = clone(wire.capabilities);
  c.session = { runtime_instance_id: runtimeId, environment_fingerprint: environmentId }; c.stream_generation = stream;
  c.supported_methods = [...new Set([...c.supported_methods, "capabilities", "submit", "result", "current_owned"])];
  c.implemented_mechanisms = [...new Set([...c.implemented_mechanisms, "native_current_reader_owned_v1"])];
  c.capture_coverage = clone(sdk.NATIVE_LOGICAL_PUBLICATION_PROFILE.required_seams); c.limits.max_captures = 4;
  c.host = { ...c.host, runtime_instance_id: runtimeId, host_kind: "test", version: "SYN_fixture", implementation: {
    source_revision: "SYN_source", module_version_id: "SYN_module", artifact_sha256: "f".repeat(64) } };
  c.game = { ...c.game, version: "SYN_game", commit: "SYN_commit", modset: { ...c.game.modset, fingerprint: "b".repeat(64) } };
  const subscription = { ...clone(wire.attach.subscription), stream_generation: stream, eager_scope: [], delivery_mode: "scoped",
    coverage: c.capture_coverage, expires_at: new Date(Date.now() + c.limits.retention_ms).toISOString() };
  const control = { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1", runtime_instance_id: runtimeId };
  let client, registration, lease, current = 0, cursor = subscription.starting_cursor;
  let currentCapture, currentCatalog;
  const submits = [], catalogs = [], currents = [];
  const fallback = async (url, body) => {
    const route = url.pathname, method = route.split("/").at(-1);
    if (route.endsWith("/clients/register")) {
      assert.equal(client, undefined); registration = body;
      client = { client_session_id: "client-real-chain-SYN", client_instance_id: body.client_instance_id };
      return { ...control, client, controller: null };
    }
    if (route.endsWith("/controller/acquire")) {
      assert.equal(body.client_session_id, client.client_session_id);
      lease = { controller_lease_id: "lease-real-chain-SYN", controller_generation: 1,
        client_session_id: client.client_session_id, expires_at: new Date(Date.now() + 120000).toISOString() };
      return { ...control, status: "controller_acquired", detail: "SYN_control", controller: lease };
    }
    if (route.endsWith("/controller/renew")) return { ...control, status: "controller_renewed", detail: "SYN_control", controller: lease };
    if (route.endsWith("/controller/release")) {
      assert.equal(body.controller_lease_id, lease.controller_lease_id); lease = null;
      return { ...control, status: "controller_released", detail: "SYN_control", controller: null };
    }
    if (route.endsWith("/controller")) return { ...control, clients: client ? [client] : [], controller: lease ?? null };
    if (route.endsWith("/actions")) {
      assert.equal(body.client_session_id, client.client_session_id); assert.equal(body.controller_lease_id, lease.controller_lease_id);
      assert.equal(body.expected_snapshot_id, currentCapture.snapshot_id);
      const action = currentCatalog.actions.find(value => value.action_id === body.bound_action_id);
      assert.ok(action); assert.ok(!submits.some(original => original.request_id === body.request_id));
      submits.push({ ...clone(body), original_catalog: clone(currentCatalog.actions) });
      const result = { ...clone(wire.result), request_id: body.request_id, snapshot_id: currentCapture.snapshot_id,
        action: clone(action), delivery: "delivered", execution: "unknown", effect: "unknown", cancel: "unknown", stages: [], reason: null,
        observed_frame: null, attribution: { runtime_instance_id: runtimeId, client_session_id: client.client_session_id,
          client_instance_id: client.client_instance_id, product_id: registration.product_id,
          product_name: registration.product_name, product_version: registration.product_version,
          controller_lease_id: body.controller_lease_id, controller_generation: body.controller_generation } };
      if (submits.length === 2) Object.assign(result, scenario === "SYN_REAL_CHAIN_NATIVE_UNKNOWN"
        ? { delivery: "unknown", reason: "SYN_native_delivery_unknown" }
        : { action: null, delivery: "not_started", reason: "stale_snapshot_or_binding", retry: "never_automatic" });
      return result;
    }
    if (method === "capabilities") return c;
    if (method === "attach") return { ...clone(wire.attach), subscription };
    if (method === "events") {
      assert.equal(body.after_cursor, cursor); cursor = "SYN_cursor:" + transport.requests.length;
      return { ...clone(wire.event_batch), events: [], next_cursor: cursor, high_watermark: cursor,
        retained_start_cursor: subscription.starting_cursor };
    }
    if (method === "await") return { ...clone(wire.await), status: "timeout", event: null };
    if (method === "detach") return clone(wire.detach);
    assert.fail("unexpected SYN control route " + route);
  };
  phase = "opening_real_store_transport";
  transport = await openRealStoreTransport({ port: Number(endpoint.port), fallback });
  assert.equal(transport.endpoint, options.endpoint); // No endpoint alias or SDK redirect.
  transport.beforeRequest(async operation => {
    if (operation === "current_owned") {
      current++;
      await transport.call("set_frame", { frame: publicFrame(current,
        scenario === "SYN_REAL_CHAIN_CURRENT_GAP" && current === 3) });
    }
  });
  transport.afterReply((operation, value) => {
    if (operation === "current_owned") { currents.push(clone(value)); currentCapture = value.capture; }
    if (operation === "catalog") {
      assert.equal(value.total_count, 2); assert.equal(value.digest, sdk.digestNativeLogicalActions(value.actions));
      currentCatalog = clone(value); catalogs.push(currentCatalog);
    }
  });
  const handoff = () => ({ schema: "sts2.host-runtime/reference-controller-handoff-1", runtime_instance_id: runtimeId,
    controller: null, basis: "fresh_control_observation_after_close" });
  phase = "reading_compiled_runtime_identity";
  const deps = { ...sdk, ...runtimeApi, runtimeIdentity: await compiledRuntimeIdentity(),
    verifyTerminalSummary: verifiedTerminalSummary, resolveInstallation: value => value,
    async startEpisode() { return { identity: { endpoint: options.endpoint, host: { runtime_instance_id: runtimeId } },
      async releaseController() { assert.equal(lease ?? null, null); return handoff(); },
      async close() { return { code: 0, signal: null, forced: false, scope: "SYN_Host_no_game" }; } }; } };
  phase = "running_real_collection";
  await runNativeSource3(options, init.operation_id, peer, deps, lifetime);
  phase = "checking_terminal_store_stats";
  const stats = await transport.stats(), diagnostics = transport.diagnostics(), producerMethods = clone(transport.requests);
  phase = "closing_real_store_transport";
  const producerExit = await transport.close(); transport = undefined;
  phase = "writing_real_chain_proof";
  await writeFile(path.join(options.output, "real-chain-producer-proof.json"), JSON.stringify({
    schema: "spireagent/test-real-chain-proof-1", scenario, scope: "SYN_public_frames_Host_control_native_results_no_game",
    actual: ["C#_Store_Projector", "loopback_HTTP", "public_SDK", "Runtime", "OS_program_Teacher", "Node_collector", "Python_parent", "public_I27_helper"],
    synthetic: ["public_source_frames", "Host", "controller", "native_action_delivery", "Await_events"],
    endpoint: options.endpoint, producer_methods: producerMethods, stats, producer_exit: producerExit,
    diagnostics, currents, catalogs, submits, admission: "not_run", learned: false }) + "\n", { flag: "wx" });
  process.stdin.destroy();
} catch (error) {
  // Snapshot the failing phase and actual pending producer operations before
  // cleanup changes them. The Python test retains these original stderr bytes.
  const observed = transport?.diagnostics() ?? null;
  const diagnostics = observed && { ...observed, events: observed.events.slice(-12) };
  process.stderr.write(JSON.stringify({ schema: "spireagent/test-real-chain-failure-1",
    phase, scenario: scenario ?? null, original_error: String(error.stack ?? error),
    diagnostics }) + "\n");
  let producerExit = null, cleanupError = null;
  try { producerExit = await transport?.close() ?? null; }
  catch (cleanup) { cleanupError = String(cleanup.stack ?? cleanup); }
  if (transport) process.stderr.write(JSON.stringify({
    schema: "spireagent/test-real-chain-cleanup-1", producer_exit: producerExit,
    cleanup_error: cleanupError }) + "\n");
  process.exitCode = 1; process.stdin.destroy();
}
