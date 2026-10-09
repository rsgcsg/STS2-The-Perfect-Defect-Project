import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";
import { createCollectionLifetime, createPipePeer, PIPE_SCHEMA,
  runNativeSource3 } from "../native-source3-collector.mjs";

const operation = "a".repeat(32);
const receipt = { code: 0, signal: null, forced: false };
const action = index => ({ action_id: `original-${index}`, kind: "input", verb: "activate",
  label: `Public ${index}`, subject_referent_id: `ref-${index}`, arguments: [], effect_domain: "native" });

async function fixture(t, behavior = {}) {
  const output = await mkdtemp(path.join(os.tmpdir(), "source3-pure-"));
  t.after(() => rm(output, { recursive: true, force: true }));
  const options = { installation: "/synthetic/game", host_local_root: "/synthetic/host",
    output, endpoint: "http://127.0.0.1:15526", seed: "SYNTHETIC", template_id: "defect-a0-s0",
    target_choices: 2, max_submissions: 2, deadline_ms: 10_000, max_input_bytes: 1024 * 1024,
    max_diagnostic_bytes: 1024 * 1024, experimental_build_acknowledged: false,
    experimental_connector_acknowledged: false, ...behavior.options };
  const lifetime = createCollectionLifetime();
  const seen = { messages: [], submissions: [], queries: [], captures: [], disposals: 0,
    releases: 0, closes: 0, hostCloses: 0, attaches: [], events: 0, order: [] };
  const catalog = Array.from({ length: 40 }, (_, index) => action(index));
  let nextReply, original;
  const peer = {
    async send(value) {
      seen.messages.push(value);
      const common = { schema: PIPE_SCHEMA, operation_id: operation, message_id: value.message_id };
      if (value.type === "ready") {
        nextReply = { ...common, type: "source_ready", source_context: {
          runtime_instance_id: "runtime", recording_session_id: "session", source_segment_id: "segment",
          source_epoch_id: "epoch", declaration: { source_kind: "agent_protocol", actor_id: "teacher",
            declaration_id: "declaration", machine_verifiable: false } } };
      } else if (value.type === "current") {
        assert.deepEqual(value.catalog, catalog); // Low-ranked members survive unchanged.
        nextReply = { ...common, type: "choice", ordinal: value.ordinal, basis: { ...value.basis },
          action_id: catalog.at(-1).action_id, stop_reason: null, teacher_state: { learned: false } };
        if (behavior.wrongBasis) nextReply.basis.snapshot_id = "different";
        if (behavior.wrongAction) nextReply.action_id = "not-original";
      } else if (value.type === "result") {
        nextReply = { ...common, type: "continue", continue: true, reason: "known_original_source" };
      } else if (value.type === "quiesced") {
        seen.order.push("application_close");
        nextReply = { ...common, type: "source_closed", known_closed: true,
          source_status: { recording_lifecycle: "closed" }, close_outcome: "known_closed" };
      }
    },
    async receive() {
      if (behavior.unknownStart && nextReply?.type === "source_ready") throw new Error("source_start_unknown");
      if (behavior.resultEOF && nextReply?.type === "continue") throw new Error("parent_eof");
      return nextReply;
    }
  };
  class Client { async controlSnapshot() { return { data: { runtime_instance_id: "runtime", controller: null } }; } }
  class Controller {
    constructor() { if (behavior.stopDuringConstruction) lifetime.stop("construction_stop"); }
    async register() {}
    async releaseControl() { seen.releases++; }
    async close() { seen.closes++; }
  }
  class Native {
    async capabilities() { return { data: { session: { runtime_instance_id: "runtime" },
      host: {}, control_policy: {}, limits: { retention_ms: 10_000 } } }; }
    async attach(input) {
      seen.attaches.push(input);
      return { data: { subscription: this.subscription = { delivery_mode: "scoped", eager_scope: [],
        stream_generation: "generation" }, next_cursor: "first" } };
    }
    async getFullCurrent({ budget }) {
      const number = seen.captures.length + 1;
      const observation = { status: "interactive", catalog: { stream_generation: behavior.badGeneration
        ? "wrong" : "generation", digest: "synthetic-digest", total_count: catalog.length } };
      const raw = JSON.stringify(observation);
      const reservation = budget.reserve({ bytes: Buffer.byteLength(raw) });
      const capture = { capture_id: `capture-${number}`, snapshot_id: `snapshot-${number}`,
        session: { runtime_instance_id: "runtime" }, byte_count: Buffer.byteLength(raw),
        sha256: createHash("sha256").update(raw).digest("hex") };
      seen.captures.push(capture);
      return { observation, actions: catalog, capture, serializedObservation: raw,
        async dispose() { seen.disposals++; reservation.release(); } };
    }
    async submit(input) {
      input.onSubmitStart(); seen.submissions.push(input);
      original = { request_id: input.requestId, snapshot_id: input.expectedSnapshotId,
        action: catalog.at(-1), delivery: behavior.unknownDelivery ? "unknown" : "delivered",
        execution: behavior.nativeRejected ? "native_rejected" : "unknown", effect: "unknown", cancel: "unknown" };
      if (behavior.doubleSignal) {
        lifetime.stop("external_signal"); lifetime.stop("external_signal");
        throw new Error("original_transport_unresolved");
      }
      if (behavior.unknownSubmit) throw new Error("original_transport_unresolved");
      return behavior.pending ? { status: "pending" } : { status: "terminal", result: { data: original } };
    }
    async result(requestId) {
      seen.queries.push(requestId);
      return seen.queries.length < 2 ? { status: "pending" }
        : { status: "terminal", result: { data: original } };
    }
    async events() { seen.events++; return { data: { events: [{ kind: "advisory" }],
      gap: { reason: "retention" }, next_cursor: "next" } }; }
    async detach() { this.subscription = null; if (behavior.detachFailure) throw new Error("detach_failed"); }
  }
  const deps = { PlayerEnvironmentRestClient: Client, EnvironmentControllerSession: Controller,
    NativeLogicalSession: Native, resolveInstallation: value => value,
    NATIVE_LOGICAL_PUBLICATION_PROFILE: { profile_id: "native-logical-publication-profile-v1",
      input_profile: "native-logical-v1", required_seams: ["fixed-native-seam"] },
    async startEpisode(input) {
      if (behavior.startupCancel) {
        await new Promise(resolve => {
          input.signal.addEventListener("abort", resolve, { once: true });
          lifetime.stop("external_signal"); lifetime.stop("external_signal");
        });
        throw Object.assign(new Error("startup_cancelled"), { host_started: true, host_exit: receipt });
      }
      return { identity: { endpoint: options.endpoint, host: { runtime_instance_id: "runtime" } },
        async releaseController() { return { controller: null }; },
        async close() { seen.hostCloses++; seen.order.push("host_close"); return receipt; } };
    }
  };
  return { options, lifetime, peer, deps, seen };
}

async function run(t, behavior) {
  const f = await fixture(t, behavior);
  return { ...f, final: await runNativeSource3(f.options, operation, f.peer, f.deps, f.lifetime) };
}

test("complete fresh Current/C, scoped empty attachment and advisory gap preserve original choices", async t => {
  const { final, seen } = await run(t);
  assert.equal(final.reason, "target_choices_reached");
  assert.equal(final.counts.submissions, 2);
  assert.equal(seen.captures.length, 2);
  assert.equal(seen.disposals, 2);
  assert.equal(final.live_byte_reservations, 0);
  assert.ok(seen.submissions.every(s => s.actionId === "original-39"));
  assert.deepEqual(seen.attaches[0].eagerScope, []);
  assert.deepEqual(seen.attaches[0].requiredSeams, ["fixed-native-seam"]);
  assert.equal(final.advisory.gaps, 2);
  assert.equal(final.advisory.history_claimed, false);
  assert.equal(final.advisory.event_payloads_archived, false);
  assert.deepEqual(final.host_exit, receipt);
  assert.equal(final.control_release.confirmed, true);
  assert.equal(seen.releases, 1);
  assert.equal(seen.hostCloses, 1);
});

test("pending reads only the original Result, never resubmits or invents effect proof", async t => {
  const { final, seen } = await run(t, { pending: true, options: { target_choices: 1, max_submissions: 1 } });
  assert.equal(seen.submissions.length, 1);
  assert.equal(seen.queries.length, 2);
  assert.ok(seen.queries.every(id => id === seen.submissions[0].requestId));
  assert.equal(final.counts.result_queries, 2);
  const result = seen.messages.find(m => m.type === "result");
  assert.equal(result.result.effect, "unknown");
});

for (const field of ["wrongBasis", "wrongAction", "unknownStart", "badGeneration"]) {
  test(`${field} admits zero native submissions and closes the owned Host`, async t => {
    const { final, seen } = await run(t, { [field]: true });
    assert.equal(seen.submissions.length, 0);
    assert.equal(seen.hostCloses, 1);
    assert.equal(final.counts.submissions, 0);
    assert.equal(final.live_byte_reservations, 0);
  });
}

for (const field of ["unknownSubmit", "unknownDelivery", "doubleSignal"]) {
  test(`${field} stops after the original attempt without automatic retry`, async t => {
    const { final, seen, lifetime } = await run(t, { [field]: true });
    assert.equal(seen.submissions.length, 1);
    assert.equal(seen.queries.length, 0);
    assert.equal(seen.hostCloses, 1);
    assert.equal(seen.releases, 1);
    assert.equal(final.live_byte_reservations, 0);
    assert.notEqual(final.reason, "target_choices_reached");
    if (field === "doubleSignal") assert.equal(lifetime.stops, 2);
  });
}

test("early double cancellation keeps the actual startup Host exit receipt", async t => {
  const { final, seen } = await run(t, { startupCancel: true });
  assert.equal(final.host_started, true);
  assert.deepEqual(final.host_exit, receipt);
  assert.equal(seen.submissions.length, 0);
});

test("cancellation during controller construction cannot cache a phantom empty release", async t => {
  const { seen, final } = await run(t, { stopDuringConstruction: true });
  assert.equal(seen.releases, 1);
  assert.equal(seen.hostCloses, 1);
  assert.equal(final.control_release.confirmed, true);
});

test("known delivery survives parent reply EOF; cleanup errors remain explicit", async t => {
  const { seen, final } = await run(t, { resultEOF: true, detachFailure: true });
  assert.equal(seen.submissions.length, 1);
  assert.equal(final.counts.known_delivered_choices, 1);
  assert.ok(final.cleanup_errors.includes("native_detach_unconfirmed"));
  assert.equal(seen.hostCloses, 1);
});

test("pipe EOF and duplicate queued replies stop before init or Host launch", async () => {
  const lifetime = createCollectionLifetime(), input = new PassThrough(), output = new PassThrough();
  const peer = createPipePeer(input, output, JSON.parse, lifetime);
  input.end();
  await assert.rejects(peer.receive(), /parent_eof/);
  assert.equal(lifetime.reason, "parent_eof");
  const life2 = createCollectionLifetime(), in2 = new PassThrough();
  const peer2 = createPipePeer(in2, new PassThrough(), JSON.parse, life2);
  in2.write('{"type":"init"}\n{"type":"init"}\n');
  await assert.rejects(peer2.receive(), /parent_eof/);
  assert.equal(life2.reason, "pipe_protocol_failed");
});


test("native rejection at the target remains a failed/censored reason, never target success", async t => {
  const { final, seen } = await run(t, { nativeRejected: true,
    options: { target_choices: 1, max_submissions: 1 } });
  assert.equal(seen.submissions.length, 1);
  assert.equal(final.counts.known_delivered_choices, 1);
  assert.equal(final.reason, "native_choice_not_delivered_or_rejected");
});


test("failed SourceReady ACK quiesces App admission before Host close, with zero native choices", async t => {
  const { final, seen } = await run(t, { unknownStart: true });
  assert.deepEqual(seen.order, ["application_close", "host_close"]);
  assert.equal(seen.submissions.length, 0);
  assert.equal(final.source_closed, true);
  assert.equal(final.control_release.confirmed, true);
  assert.equal(final.control_release.native_controller_constructed, false);
  assert.equal(seen.releases, 0);
  assert.equal(seen.messages.find(message => message.type === "quiesced").pending_request_id, null);
});
