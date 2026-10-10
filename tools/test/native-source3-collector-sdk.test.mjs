/** Real public SDK + generic Runtime + actual program Agent stdio, synthetic wire only. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PassThrough } from "node:stream";
import test from "node:test";
import { existsSync } from "node:fs";
import * as sdk from "../../components/connector/sdk/typescript/dist/index.js";
import * as runtimeApi from "../../components/policy-runtime/dist/index.js";
import { createCollectionLifetime, createPipePeer, OWNED_EXECUTION_POLICY, OWNED_PIPE_SCHEMA,
  PIPE_SCHEMA, runNativeSource3 }
  from "../native-source3-collector.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const source = JSON.parse(await readFile(new URL("../../components/connector/contracts/fixtures/native-logical-v1.json", import.meta.url)));
const wire = source.wire_samples;
const operation = "a".repeat(32), endpoint = "http://127.0.0.1:15526";
const projectPython = path.join(ROOT, "python", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const python = process.env.SOURCE3_TEST_PYTHON ?? (existsSync(projectPython) ? projectPython
  : process.platform === "win32" ? "python" : "python3");
const clone = value => structuredClone(value);
const response = value => new Response(JSON.stringify(value), { status: 200,
  headers: { "Content-Type": "application/json" } });
const member = (id, verb, subject = null) => ({ action_id: id, kind: "native_input", verb,
  label: id, subject_referent_id: subject, arguments: [], effect_domain: "native" });
const ref = (id, role = "card", kind = "entity") => ({ referent_id: id, role, kind, label: id,
  state: { visible: true, enabled: true, selected: false, focused: false,
    observation_basis: "synthetic_public_fixture" }, properties_schema: null, properties: null });

function frames() {
  const values = [
    ["native_map", [member("open-deck", "open_run_deck")]],
    ["run_deck", [member("inspect", "inspect_deck_card", "card")], [ref("card")]],
    ["inspect_card", [member("preview", "toggle_card_upgrade_preview"), member("return", "return_card_inspect")]],
    ["inspect_card", [member("return", "return_card_inspect")]],
    ["inspect_card", [member("return", "return_card_inspect")]], // closing still exposes legal Backstop
    ["inspect_card", [member("return", "return_card_inspect")]], // exact duplicate readiness, no consume
    ["run_deck", [member("back", "return_native_information")]],
    ["native_map", [member("leaf", "activate", "map-point")], [ref("map-point", "map_point", "entity")]],
  ];
  return values.map(([kind, catalog, referents], index) => {
    const number = index === 5 ? 5 : index + 1;
    const observation = { ...clone(wire.observation), snapshot_id: `snapshot-${number}`,
      revision: number, status: "interactive", referents: referents ?? [] };
    observation.interaction.kind = kind;
    observation.interaction.content.surface = kind === "native_map"
      ? { kind: "map_navigation", next_options: [{ entity_id: "map-point", point_type: "monster" }] }
      : { kind };
    observation.catalog = { ...observation.catalog, snapshot_id: observation.snapshot_id,
      catalog_ref: `catalog-${index + 1}`, total_count: catalog.length, digest: sdk.digestNativeLogicalActions(catalog) };
    return { observation, catalog };
  });
}

async function collect(t, { unknownAt = null, pendingAt = null, sourceOff = false, stopAfterAwait = false, nativeRejectedAt = null, target = 6, hugeStartup = false, largeStatus = false, privateWriteFailure = false, currentFailureAt = null, currentDiagnosticWriteFailure = false,
  ownedPolicy = false, staleAt = [], maxSubmissions = null, summaryFailure = false } = {}) {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "source3-runtime-teacher-"));
  // The production request owns an ordinary canonical directory. The helper
  // correctly rejects /var symlink aliases; normalize only this opt-in fixture.
  const output = ownedPolicy ? await realpath(temporary) : temporary;
  t.after(() => rm(output, { recursive: true, force: true }));
  const factory = "import json,sys; from pathlib import Path; from stpd.policy.native_teacher_agent import descriptor,write_artifact; p=Path(sys.argv[1]); "
    + (ownedPolicy ? "policy=json.loads(sys.argv[2]); print(json.dumps({'teacher_descriptor':descriptor(execution_policy=policy),'teacher_artifact':write_artifact(p,execution_policy=policy),'python_executable':sys.executable}))"
      : "print(json.dumps({'teacher_descriptor':descriptor(),'teacher_artifact':write_artifact(p),'python_executable':sys.executable}))");
  const prepared = spawnSync(python, ["-c", factory, path.join(output, "teacher-code-artifact.json"),
    JSON.stringify(ownedPolicy ? OWNED_EXECUTION_POLICY : null)],
    { cwd: ROOT, env: { ...process.env, PYTHONPATH: path.join(ROOT, "python") }, encoding: "utf8" });
  assert.equal(prepared.status, 0, prepared.stderr);
  if (privateWriteFailure) await import("node:fs/promises").then(m => m.writeFile(path.join(output, "collector-final-full.json"), "preserved_existing_private_file"));
  if (currentDiagnosticWriteFailure) await import("node:fs/promises").then(m =>
    m.writeFile(path.join(output, "native-current-failure-reply.json"), "preserved_existing_private_file"));
  const actor = JSON.parse(prepared.stdout), seen = { pipe: [], requests: [], submits: [], waits: 0,
    sourceCloses: 0, hostCloses: 0, order: [], retentionsReleased: [], captures: [] };
  let values = frames();
  if (ownedPolicy) {
    // A refused native input cannot advance to its expected owner. Preserve
    // that public state for each refusal, then assign each fresh fixture unit.
    for (const attempt of [...staleAt].sort((a, b) => a - b))
      values.splice(attempt, 0, clone(values[attempt - 1]));
    values = values.map((value, index) => {
      const observation = value.observation, snapshot = `owned-snapshot-${index + 1}`;
      observation.snapshot_id = snapshot; observation.revision = index + 1;
      observation.interaction.interaction_id = snapshot;
      observation.owner_occurrence = { owner_id: "fixture-owner", occurrence_id: snapshot,
        binding_revision: snapshot, focus_referent_id: null, focus_occurrence: null };
      observation.catalog.snapshot_id = snapshot;
      observation.catalog.catalog_ref = `owned-catalog-${index + 1}`;
      return value;
    });
  }
  const captures = new Map(), retentions = new Map(), catalogs = new Map();
  let current = 0, client = null, lease = null, cursor = wire.attach.subscription.starting_cursor;
  const c = { ...clone(wire.capabilities), supported_methods: ["capabilities", "submit", "result", ...wire.capabilities.supported_methods],
    capture_coverage: clone(sdk.NATIVE_LOGICAL_PUBLICATION_PROFILE.required_seams) };
  if (ownedPolicy) {
    c.supported_methods.push("current_owned");
    c.implemented_mechanisms.push("native_current_reader_owned_v1");
  }
  c.host = { ...c.host, host_kind: "test", version: "fixture-version", implementation: {
    source_revision: "fixture-source", module_version_id: "fixture-module", artifact_sha256: "f".repeat(64) } };
  c.game = { ...c.game, version: "fixture-game-version", commit: "fixture-game-commit", modset: {
    ...c.game.modset, fingerprint: "b".repeat(64) } };
  const subscription = { ...clone(wire.attach.subscription), eager_scope: [], delivery_mode: "scoped",
    coverage: c.capture_coverage, expires_at: new Date(Date.now() + c.limits.retention_ms).toISOString() };
  const control = { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
    runtime_instance_id: c.session.runtime_instance_id };
  const fetchFixture = async (url, init) => {
    const route = new URL(url).pathname;
    assert.equal(new URL(url).origin, endpoint); // This transport never opens a socket.
    const body = init.body === undefined ? undefined : sdk.parseNativeLogicalJson(init.body);
    seen.requests.push({ route, method: init.method, body });
    if (route.endsWith("/clients/register")) {
      assert.equal(client, null);
      client = { client_session_id: "client-fixture", client_instance_id: body.client_instance_id };
      return response({ ...control, client, controller: null });
    }
    if (route.endsWith("/controller/acquire")) {
      assert.deepEqual(body, { client_session_id: client.client_session_id });
      lease = { controller_lease_id: "lease-fixture", controller_generation: 1,
        client_session_id: client.client_session_id, expires_at: new Date(Date.now() + 60_000).toISOString() };
      return response({ ...control, status: "controller_acquired", detail: "synthetic", controller: lease });
    }
    if (route.endsWith("/controller/release")) {
      assert.deepEqual(body, { client_session_id: client.client_session_id,
        controller_lease_id: lease.controller_lease_id, controller_generation: lease.controller_generation });
      lease = null;
      return response({ ...control, status: "controller_released", detail: "synthetic", controller: null });
    }
    if (route.endsWith("/controller")) return response({ ...control, clients: client ? [client,
      ...(largeStatus ? Array.from({ length: 20 }, (_, index) => ({ client_session_id: `other-${index}`,
        client_instance_id: "非ASCII客户端".repeat(200) })) : [])] : [], controller: lease });
    if (route.endsWith("/actions")) {
      assert.equal(body.client_session_id, client.client_session_id);
      assert.equal(body.controller_lease_id, lease.controller_lease_id);
      const value = values[Math.min(current - 1, values.length - 1)];
      assert.equal(body.expected_snapshot_id, value.observation.snapshot_id);
      const action = value.catalog.find(a => a.action_id === body.bound_action_id);
      assert.ok(action);
      assert.ok(!seen.submits.some(original => original.request_id === body.request_id));
      seen.submits.push(body);
      if (seen.submits.length === unknownAt) throw new Error("synthetic_original_delivery_unknown");
      if (seen.submits.length === pendingAt) return new Response(JSON.stringify({
        error: { code: "request_pending", detail: "original result still pending" } }), { status: 202 });
      const result = { ...clone(wire.result), request_id: body.request_id,
        snapshot_id: value.observation.snapshot_id, action, delivery: "delivered", execution: seen.submits.length === nativeRejectedAt ? "native_rejected" : "unknown",
        effect: "unknown", cancel: "not_requested", stages: [], reason: null };
      if (ownedPolicy) result.attribution = {
        runtime_instance_id: c.session.runtime_instance_id, client_session_id: client.client_session_id,
        client_instance_id: client.client_instance_id, product_id: "sts2.policy-runtime",
        product_name: "STS2 Policy Runtime", product_version: runtimeApi.POLICY_RUNTIME_VERSION,
        controller_lease_id: body.controller_lease_id, controller_generation: body.controller_generation,
      };
      if (staleAt.includes(seen.submits.length)) Object.assign(result, { action: null,
        delivery: "not_started", execution: "unknown", effect: "unknown", cancel: "unknown",
        stages: [], reason: "stale_snapshot_or_binding", retry: "never_automatic" });
      return response(result);
    }
    const method = route.split("/").at(-1);
    if (method === "capabilities") return response(c);
    if (method === "attach") {
      assert.deepEqual(body.eager_scope, []);
      assert.deepEqual(body.required_seams, sdk.NATIVE_LOGICAL_PUBLICATION_PROFILE.required_seams);
      return response({ ...clone(wire.attach), subscription });
    }
    if (method === "events") {
      assert.equal(body.after_cursor, cursor);
      cursor = "cursor-" + seen.requests.length;
      return response({ ...clone(wire.event_batch), events: [], next_cursor: cursor,
        high_watermark: cursor, retained_start_cursor: subscription.starting_cursor });
    }
    if (method === (ownedPolicy ? "current_owned" : "current")) {
      if (largeStatus) return response({ ...clone(wire.current), schema: "非ASCII原因".repeat(1000) });
      assert.deepEqual(body.eager_scope, sdk.NATIVE_LOGICAL_SCOPE);
      if (current + 1 === currentFailureAt) {
        seen.failedCurrent = { schema: "sts2.player-environment/native-logical-current-1",
          input_profile: "native-logical-v1", status: "source_capture_incomplete", reason: "public_combat_power_facts",
          context: null, capture: null, retention: null };
        const text = JSON.stringify(seen.failedCurrent, null, 2) + "\n";
        seen.failedCurrentWireBytes = Buffer.byteLength(text);
        return new Response(text, { status: 409, headers: { "Content-Type": "application/json" } });
      }
      const value = values[Math.min(current, values.length - 1)];
      current++;
      const raw = Buffer.from(JSON.stringify(value.observation));
      const capture = { ...clone(wire.capture), capture_id: `capture-${current}`,
        snapshot_id: value.observation.snapshot_id, capture_ordinal: String(current), byte_count: raw.length,
        sha256: createHash("sha256").update(raw).digest("hex"), read_cursor: `read-${current}` };
      if (ownedPolicy) Object.assign(capture, { captured_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + c.limits.retention_ms).toISOString() });
      const retained = { ...clone(wire.retain.retention), retention_handle_id: `retention-${current}`,
        capture, read_cursor: `retained-read-${current}` };
      if (ownedPolicy) retained.expires_at = capture.expires_at;
      captures.set(capture.capture_id, { raw, capture, retained });
      retentions.set(retained.retention_handle_id, capture.capture_id);
      catalogs.set(value.observation.catalog.catalog_ref, { ...clone(wire.catalog_page),
        catalog_ref: value.observation.catalog.catalog_ref, snapshot_id: capture.snapshot_id,
        total_count: value.catalog.length, filtered_count: value.catalog.length, digest: value.observation.catalog.digest,
        filtered_digest: value.observation.catalog.digest, actions: value.catalog });
      seen.captures.push(capture);
      return response({ ...clone(wire.current_retained), capture, retention: retained,
        context: { ...clone(wire.observation_context), capture_ref: capture.capture_id,
          observation_ref: capture.snapshot_id } });
    }
    if (method === "read") {
      const value = captures.get(body.capture_id);
      assert.equal(body.cursor, value.retained.read_cursor);
      return response({ ...clone(wire.read), capture_id: value.capture.capture_id,
        sha256: value.capture.sha256, total_bytes: value.raw.length, data_base64: value.raw.toString("base64") });
    }
    if (method === "catalog") {
      assert.equal(body.prefix, null);
      assert.equal(body.cursor, null);
      return response(catalogs.get(body.catalog_ref));
    }
    if (method === "release") {
      assert.ok(retentions.has(body.retention_handle_id));
      retentions.delete(body.retention_handle_id); seen.retentionsReleased.push(body.retention_handle_id);
      return response({ ...clone(wire.release), retention_handle_id: body.retention_handle_id });
    }
    if (method === "await") {
      seen.waits++;
      assert.equal(body.after_cursor, cursor);
      assert.match(body.wait_id, /^[0-9a-f]{32}$/u);
      return response({ ...clone(wire.await), status: "timeout", event: null });
    }
    if (method === "detach") return response(wire.detach);
    assert.fail(`unexpected public transport method ${method}`);
  };
  function Environment(base, timeout) { return new sdk.PlayerEnvironmentRestClient(base, timeout, fetchFixture); }
  const deps = { ...sdk, ...runtimeApi, PlayerEnvironmentRestClient: Environment,
    NdjsonAgentSessionPort: class extends runtimeApi.NdjsonAgentSessionPort {
      constructor(child, ...args) {
        seen.teacherLaunches = (seen.teacherLaunches ?? 0) + 1;
        seen.teacherPid = child.pid;
        // Observe the original child independently of the collector's receipt.
        // Runtime/OS combinations need not encode forced exits the same way.
        seen.rawTeacherClose = new Promise(resolve => child.once("close", (code, signal) =>
          resolve({ pid: child.pid, code, signal })));
        seen.teacherKillOffers = [];
        const kill = child.kill.bind(child);
        child.kill = signal => { seen.teacherKillOffers.push(signal); return kill(signal); };
        super(child, ...args);
      }
    },
    verifyTerminalSummary: async ({ directory, expected, execution_policy }) => {
      seen.order.push("TerminalProof");
      if (summaryFailure) throw new Error("synthetic_finalized_proof_unavailable");
      // Explicit source/test-only public Evidence dependency. Production keeps its
      // installed pin; this subprocess exercises the actual fixed helper/owner.
      const checked = spawnSync(python,
        ["-m", "spireagent.workbench.native_source3_collection", "--verify-terminal-summary"],
        { cwd: ROOT, env: { ...process.env, PYTHONPATH: [path.join(ROOT, "python"),
          path.join(ROOT, "components/evidence")].join(path.delimiter) }, encoding: "utf8",
          input: JSON.stringify({ directory, expected, execution_policy }), timeout: 10_000 });
      if (checked.status !== 0) {
        const receipt = path.join(os.tmpdir(), `e6-collector-helper-failure-${process.pid}-${expected.run_id}.json`);
        await writeFile(receipt, JSON.stringify({ directory, expected, execution_policy,
          status: checked.status, signal: checked.signal, stdout: checked.stdout, stderr: checked.stderr }) + "\n");
      }
      assert.equal(checked.status, 0, checked.stderr);
      return JSON.parse(checked.stdout);
    },
    runtimeIdentity: { version: runtimeApi.POLICY_RUNTIME_VERSION, code_sha256: "c".repeat(64) },
    resolveInstallation: value => value, async startEpisode() {
      if (hugeStartup) {
        const leaf = () => new Error("public_failure_" + "x".repeat(8192));
        throw new AggregateError([leaf(), leaf(), leaf()], "root_" + "x".repeat(8192),
          { cause: new AggregateError([leaf(), leaf(), leaf()], "cause_" + "x".repeat(8192)) });
      }
      return { identity: { endpoint, host: { runtime_instance_id: c.session.runtime_instance_id } },
        async releaseController() { return { controller: null }; },
        async close() { seen.hostCloses++; seen.order.push("HostClose"); return { code: 0, signal: null, forced: false }; } };
    } };
  const options = { installation: "/synthetic/game", host_local_root: "/synthetic/host", output, endpoint,
    seed: "SYNTHETIC", template_id: "defect-a0-s0", target_choices: target,
    max_submissions: maxSubmissions ?? Math.max(target, 6),
    deadline_ms: 30_000, max_input_bytes: 8 * 1024 * 1024, max_diagnostic_bytes: 1024 * 1024,
    experimental_build_acknowledged: false, experimental_connector_acknowledged: false,
    record_source3: !sourceOff, ...actor, ...(ownedPolicy ? { execution_policy: OWNED_EXECUTION_POLICY } : {}) };
  const input = new PassThrough(), outgoing = new PassThrough(), lifetime = createCollectionLifetime();
  const childPeer = createPipePeer(input, outgoing, sdk.parseNativeLogicalJson, lifetime);
  const parent = createPipePeer(outgoing, input, sdk.parseNativeLogicalJson, createCollectionLifetime());
  const parentTask = (async () => {
    for (;;) {
      const value = await parent.receive({ timeoutMs: 10_000 }); seen.pipe.push(value);
      if (value.type === "closed") return value;
      const common = { schema: ownedPolicy ? OWNED_PIPE_SCHEMA : PIPE_SCHEMA,
        operation_id: operation, message_id: value.message_id };
      if (value.type === "ready") await parent.send({ ...common, type: "source_ready",
        source_recording: sourceOff ? "not_requested" : "known_recording", source_context: sourceOff ? null : {
          runtime_instance_id: c.session.runtime_instance_id, recording_session_id: "source", source_segment_id: "segment",
          source_epoch_id: "epoch", declaration: { source_kind: "agent_protocol", actor_id: "explicit-fixture",
            declaration_id: "declaration", machine_verifiable: false } } });
      else if (value.type === "runtime_gate" || value.type === "runtime_tick") {
        if (stopAfterAwait && value.type === "runtime_tick" && value.tick.type === "awaited") {
          lifetime.stop("external_signal"); lifetime.stop("external_signal");
        }
        await parent.send({ ...common, type: "runtime_continue", continue: true, reason: "known_application_source" });
      } else if (value.type === "quiesced") {
        if (!sourceOff) { seen.sourceCloses++; seen.order.push("SourceClose"); }
        await parent.send({ ...common, type: "source_closed", known_closed: !sourceOff,
          source_status: null, close_outcome: sourceOff ? "not_requested" : "known_closed" });
      } else assert.fail(`unexpected bespoke collector message ${value.type}`);
    }
  })();
  const final = await runNativeSource3(options, operation, childPeer, deps, lifetime);
  if (final.failure_details) {
    const details = await readFile(path.join(output, final.failure_details.path), "utf8");
    if (process.env.SOURCE3_TEST_DIAGNOSTICS) {
      process.stderr.write(details);
      process.stderr.write(JSON.stringify(seen.requests.map(value => value.route)) + "\n");
    }
  }
  const wireFinal = await parentTask;
  assert.deepEqual(wireFinal, final);
  assert.ok(Buffer.byteLength(JSON.stringify(wireFinal) + "\n") <= 16 * 1024);
  const full = final.full_record_ref === null ? null : JSON.parse(await readFile(path.join(output, final.full_record_ref.path), "utf8"));
  if (full) { assert.equal(createHash("sha256").update(await readFile(path.join(output, final.full_record_ref.path))).digest("hex"), final.full_record_ref.sha256); }
  input.destroy(); outgoing.destroy();
  assert.equal(retentions.size, 0);
  if (hugeStartup) {
    assert.equal(final.runtime_summary, null);
    assert.ok(final.failure_details.bytes > 16 * 1024);
    assert.ok(Buffer.byteLength(JSON.stringify(final)) < 16 * 1024);
    assert.ok(seen.pipe.some(value => value.type === "closed"));
    return { final, seen, events: [] };
  }
  assert.equal(final.control_release.confirmed, true, JSON.stringify(final));
  assert.deepEqual(final.host_exit, { code: 0, signal: null, forced: false });
  assert.equal(seen.hostCloses, 1);
  assert.equal(seen.sourceCloses, sourceOff ? 0 : 1);
  if (!sourceOff) assert.deepEqual(seen.order, ["SourceClose", "HostClose", ...(ownedPolicy ? ["TerminalProof"] : [])]);
  assert.ok(final.teacher_exit.pid > 0);
  assert.equal(final.teacher_exit.actual_exit, true);
  assert.equal(final.teacher_exit.owner, "public_NdjsonAgentSessionPort");
  assert.equal(seen.teacherLaunches, 1);
  assert.deepEqual(seen.teacherKillOffers, ["SIGKILL"]);
  assert.deepEqual({ pid: final.teacher_exit.pid, code: final.teacher_exit.code,
    signal: final.teacher_exit.signal }, await seen.rawTeacherClose);
  if (full) assert.deepEqual(full.teacher_exit, final.teacher_exit);
  const events = (await readFile(path.join(final.direct_evidence.directory, "events.jsonl"), "utf8"))
    .trim().split("\n").filter(Boolean).map(JSON.parse);
  return { final: { ...final, runtime_status: full?.runtime_status ?? null }, wireFinal, full, seen, events, output };
}

test("real stdio program teacher commits exact ACK and Awaits closing Return without retry before owner arrival", async t => {
  const { final, seen, events } = await collect(t);
  assert.equal(final.reason, "target_choices_reached", JSON.stringify(final));
  assert.deepEqual(seen.submits.map(value => value.bound_action_id), ["open-deck", "inspect", "preview", "return", "back", "leaf"]);
  assert.equal(seen.waits, 2);
  assert.equal(final.counts.known_delivered_choices, 6);
  assert.equal(final.runtime_status.session.state_version, 7);
  const kinds = events.map(value => value.kind);
  assert.equal(kinds.filter(kind => kind === "agent_sample_consume_ack_offered").length, 7);
  assert.equal(kinds.filter(kind => kind === "agent_sample_query_discarded").length, 1);
  assert.equal(kinds.filter(kind => kind === "agent_sample_input_stored").length, 7);
  assert.ok(kinds.includes("native_await_result"));
  assert.ok(!seen.pipe.some(value => ["current", "choice", "result"].includes(value.type)));
});

for (const currentDiagnosticWriteFailure of [false, true]) {
  test(`original SDK failed Current reason survives unchanged Runtime handoff and cleanup; diagnostic write failure=${currentDiagnosticWriteFailure}`, async t => {
    const { final, seen, events, output } = await collect(t, {
      target: 100, currentFailureAt: 9, currentDiagnosticWriteFailure,
    });
    assert.equal(final.reason, "runtime_handoff");
    assert.deepEqual(final.runtime_status.errors, ["query_current_source_capture_incomplete"]);
    assert.equal(final.runtime_status.session.agent_state, "uncertain");
    assert.equal(final.runtime_status.tainted, false);
    assert.equal(final.runtime_status.pending_request, null);
    assert.equal(final.counts.known_delivered_choices, 6);
    assert.equal(seen.submits.length, 6);
    assert.equal(seen.requests.filter(entry => entry.route.endsWith("/current")).length, 9);
    assert.deepEqual(final.cleanup_errors, []);
    assert.equal(events.filter(event => event.kind === "native_result").length, 6);
    assert.equal(events.at(-1).kind, "stopped");
    const details = JSON.parse(await readFile(path.join(output, final.failure_details.path), "utf8"));
    assert.deepEqual(details.runtime_errors, ["query_current_source_capture_incomplete"]);
    const diagnosticFile = path.join(output, "native-current-failure-reply.json");
    if (currentDiagnosticWriteFailure) {
      assert.equal(details.native_current_failure_reply, null);
      assert.equal(await readFile(diagnosticFile, "utf8"), "preserved_existing_private_file");
      assert.ok(details.failures.some(error => /EEXIST/u.test(error.message)));
    } else {
      const reference = details.native_current_failure_reply;
      const bytes = await readFile(diagnosticFile);
      assert.equal(bytes.length, reference.bytes);
      assert.equal(createHash("sha256").update(bytes).digest("hex"), reference.sha256);
      const saved = JSON.parse(bytes);
      assert.deepEqual(saved.original_sdk_reply.raw, seen.failedCurrent);
      assert.equal(saved.original_sdk_reply.statusCode, 409);
      assert.equal(saved.original_sdk_reply.encodedByteCount, seen.failedCurrentWireBytes);
      assert.deepEqual(saved.request, { eager_scope: sdk.NATIVE_LOGICAL_SCOPE, expected_snapshot_id: null });
      assert.equal(saved.representation, "original_public_SDK_JSON_values_reserialized_as_private_JSON");
      assert.ok(saved.non_claims.includes("not_original_HTTP_bytes"));
      assert.ok(bytes.length <= 64 * 1024);
    }
  });
}

test("generic Runtime retains unknown original intent in immutable evidence, no next action or invented pending ID", async t => {
  const { final, seen, events } = await collect(t, { unknownAt: 2 });
  assert.equal(seen.submits.length, 2);
  assert.equal(final.runtime_status.tainted, true);
  assert.equal(final.runtime_status.autonomy_budget.submissions_used, 2);
  assert.equal(final.unknown_request_id_projection, "not_exposed_by_public_status_see_original_immutable_evidence");
  const original = seen.submits[1].request_id;
  assert.ok(events.some(value => value.kind === "native_submission_requested" && value.payload.request_id === original));
  assert.ok(!seen.requests.some(value => value.route.includes("/actions/")));
});

test("Source3 opt-out is explicit while direct Agent evidence stays real and actor remains program-only", async t => {
  const { final } = await collect(t, { sourceOff: true });
  assert.equal(final.record_source3, false);
  assert.equal(final.direct_evidence.training_admission, "not_run");
  assert.equal(final.runtime_status.agent.architecture, "explicit_native_public_program_teacher");
  assert.deepEqual(final.cleanup_errors, []);
});

test("repeated cancellation during public Runtime Await keeps original release, Source Close and actual child exit", async t => {
  const { final, seen } = await collect(t, { stopAfterAwait: true });
  assert.equal(final.reason, "external_signal");
  assert.equal(seen.submits.length, 4);
  assert.equal(seen.requests.filter(value => value.route.endsWith("controller/release")).length, 1);
});


test("known native rejection at target boundary remains a censored stop rather than target success", async t => {
  const { final, seen, events } = await collect(t, { nativeRejectedAt: 1, target: 1 });
  assert.equal(seen.submits.length, 1);
  assert.equal(final.counts.known_delivered_choices, 1); // Delivery and execution are separate.
  assert.equal(final.runtime_status.last_result.execution, "native_rejected");
  assert.equal(final.reason, "native_choice_rejected");
  assert.ok(events.some(value => value.kind === "native_result" && value.payload.result.execution === "native_rejected"));
});

test("large nested failure details stay private while real duplex retains the bounded cleanup receipt", async t => {
  const { final } = await collect(t, { hugeStartup: true });
  assert.equal(final.host_started, false);
  assert.equal(final.host_exit, null);
  assert.ok(final.failure_details.sha256.match(/^[a-f0-9]{64}$/u));
  assert.deepEqual(final.cleanup_errors, []);
});


for (const privateWriteFailure of [false, true]) {
  test(`large actual Runtime status/control crosses bounded cleanup envelopes; private final failure=${privateWriteFailure}`, async t => {
    const { wireFinal, full, seen, output } = await collect(t, { largeStatus: true, privateWriteFailure });
    const quiesced = seen.pipe.find(value => value.type === "quiesced");
    assert.ok(Buffer.byteLength(JSON.stringify(quiesced)) > 16 * 1024);
    assert.equal(quiesced.runtime_status.schema, "sts2.policy-runtime/agent-session-status-1");
    assert.ok(quiesced.runtime_status.errors.length > 0);
    assert.equal(wireFinal.source_closed, true);
    assert.equal(wireFinal.control_release.confirmed, true);
    assert.equal(wireFinal.runtime_summary.controller, "released");
    assert.deepEqual(wireFinal.host_exit, { code: 0, signal: null, forced: false });
    assert.ok(wireFinal.teacher_exit.actual_exit);
    assert.ok(Buffer.byteLength(JSON.stringify(wireFinal)) < 16 * 1024);
    if (privateWriteFailure) {
      assert.equal(wireFinal.full_record_ref, null);
      assert.ok(wireFinal.cleanup_errors.includes("private_final_record_write_failed"));
      assert.equal(await readFile(path.join(output, "collector-final-full.json"), "utf8"), "preserved_existing_private_file");
    } else {
      assert.ok(Buffer.byteLength(JSON.stringify(full)) > 16 * 1024);
      assert.ok(full.control_release.observation.clients.length > 1);
      assert.deepEqual(wireFinal.cleanup_errors, []);
    }
  });
}


test("known pending original Result is handed off without automatic read or resubmit", async t => {
  const { final, seen, events } = await collect(t, { pendingAt: 1 });
  assert.equal(seen.submits.length, 1);
  assert.equal(final.runtime_status.pending_request.request_id, seen.submits[0].request_id);
  assert.equal(final.runtime_status.pending_request.status, "pending");
  assert.equal(final.counts.known_delivered_choices, 0);
  assert.ok(events.some(value => value.kind === "native_request_pending"));
  assert.ok(!seen.requests.some(value => value.route.includes("/actions/")));
});

// Synthetic transport composition through the actual SDK/Runtime/stdio and
// public typed Evidence helper. These are not live-game or capacity qualifications.
test("declared stale refusal makes a fresh changed decision while the last-attempt budget stop stays original", async t => {
  const { final, seen, events } = await collect(t, { ownedPolicy: true, staleAt: [1], target: 2, maxSubmissions: 3 });
  assert.equal(final.reason, "submission_attempt_limit", JSON.stringify(final));
  assert.deepEqual(final.cleanup_errors, []);
  assert.equal(seen.submits.length, 3);
  assert.equal(new Set(seen.submits.map(value => value.request_id)).size, 3);
  assert.equal(seen.submits[0].bound_action_id, seen.submits[1].bound_action_id);
  assert.notEqual(seen.submits[0].expected_snapshot_id, seen.submits[1].expected_snapshot_id);
  assert.ok(!seen.requests.some(value => value.route.endsWith("/current")));
  const fresh = seen.pipe.filter(value => value.type === "runtime_tick" && value.tick.type === "fresh_decision_required");
  assert.equal(fresh.length, 1);
  assert.equal(fresh[0].tick.original_request_id, seen.submits[0].request_id);
  assert.equal(final.runtime_summary.submissions_used, 3);
  assert.equal(final.terminal_summary.original_submission_count, 3);
  assert.equal(final.terminal_summary.terminal_result_count, 3);
  assert.equal(final.terminal_summary.known_delivered, 2);
  assert.equal(final.terminal_summary.known_stale_rejections, 1);
  assert.equal(final.terminal_summary.consecutive_known_stale_rejections, 0);
  assert.equal(events.filter(value => value.kind === "native_stale_decision_deferred").length, 1);
});

test("inclusive stale threshold retains its last refusal and actual owner stop reason", async t => {
  const { final, seen, events } = await collect(t, { ownedPolicy: true, staleAt: [1, 2, 3], target: 6 });
  assert.equal(final.reason, "known_stale_streak_limit", JSON.stringify(final));
  assert.equal(seen.submits.length, 3);
  assert.equal(final.terminal_summary.terminal_result_count, 3);
  assert.equal(final.terminal_summary.known_stale_rejections, 3);
  assert.equal(final.terminal_summary.consecutive_known_stale_rejections, 3);
  assert.equal(final.terminal_summary.known_delivered, 0);
  assert.equal(events.filter(value => value.kind === "native_stale_decision_deferred").length, 2);
  assert.equal(events.filter(value => value.kind === "native_result").length, 3);
  assert.equal(seen.pipe.filter(value => value.type === "runtime_tick"
    && value.tick.type === "fresh_decision_required").length, 2);
});

test("one stale refusal within a two-attempt budget cannot claim a two-delivery target", async t => {
  const { final, seen } = await collect(t, { ownedPolicy: true, staleAt: [1], target: 2, maxSubmissions: 2 });
  assert.notEqual(final.reason, "target_choices_reached");
  assert.equal(seen.submits.length, 2);
  assert.equal(final.runtime_summary.submissions_used, 2);
  assert.equal(final.terminal_summary.terminal_result_count, 2);
  assert.equal(final.terminal_summary.known_delivered, 1);
  assert.equal(final.terminal_summary.known_stale_rejections, 1);
});

test("missing finalized counter proof does not delay owner cleanup or fabricate final totals", async t => {
  const { final, seen } = await collect(t, { ownedPolicy: true, target: 1, summaryFailure: true });
  assert.equal(final.source_closed, true);
  assert.equal(final.reason, "target_choices_reached");
  assert.equal(final.counts.known_delivered_choices, 1);
  assert.equal(final.control_release.confirmed, true);
  assert.equal(seen.hostCloses, 1);
  assert.deepEqual(seen.order, ["SourceClose", "HostClose", "TerminalProof"]);
  assert.equal(final.terminal_summary, null);
  assert.equal(final.counter_proof_error, "synthetic_finalized_proof_unavailable");
  assert.ok(final.cleanup_errors.includes("terminal_counter_proof_unavailable"));
});
