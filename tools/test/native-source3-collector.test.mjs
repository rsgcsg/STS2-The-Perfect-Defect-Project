import assert from "node:assert/strict";
import { PassThrough } from "node:stream";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createCollectionLifetime, createPipePeer, observeNativeCurrentFailure, OWNED_EXECUTION_POLICY, OWNED_PIPE_SCHEMA,
  PIPE_SCHEMA, runNativeSource3, runtimeTerminationReason, validateFreshDecisionTick, validateOptions, validateTerminalSummary,
  verifiedTerminalSummary }
  from "../native-source3-collector.mjs";

const operation = "a".repeat(32);
const options = { installation: "/synthetic/game", host_local_root: "/synthetic/host", output: "/synthetic/output",
  endpoint: "http://127.0.0.1:15526", seed: "SYNTHETIC", template_id: "defect-a0-s0",
  target_choices: 100, max_submissions: 100, deadline_ms: 900000, max_input_bytes: 8 * 1024 * 1024,
  max_diagnostic_bytes: 1024 * 1024, experimental_build_acknowledged: false,
  experimental_connector_acknowledged: false, record_source3: true,
  python_executable: "/synthetic/python", teacher_artifact: {}, teacher_descriptor: {} };

test("opted delivered handoff consumes the exact published owner budget cause without count inference", () => {
  const tick = { type: "delivered", status: { mode: "human", autonomy_budget: {
    state: "exhausted", submissions_used: 3, exhausted_reason: "submission_attempt_limit", ended_reason: null } } };
  assert.equal(runtimeTerminationReason(tick), "submission_attempt_limit");
  assert.equal(runtimeTerminationReason({ ...tick, type: "not_delivered", reason: "known_stale_streak_limit" }),
    "known_stale_streak_limit");
  for (const reason of ["policy_call_limit", "deadline"])
    assert.equal(runtimeTerminationReason({ ...tick, status: { ...tick.status,
      autonomy_budget: { ...tick.status.autonomy_budget, exhausted_reason: reason } } }), reason);
  const clone = value => structuredClone(value);
  for (const change of [value => { value.type = "not_admitted"; }, value => { value.type = "awaited"; },
    value => { value.status.mode = "auto"; }, value => { value.status.autonomy_budget.state = "inactive"; },
    value => { value.status.autonomy_budget.ended_reason = "stopped"; },
    value => { delete value.status.autonomy_budget.ended_reason; },
    value => { value.status.autonomy_budget.exhausted_reason = "arbitrary_reason"; },
    value => { value.status.autonomy_budget.exhausted_reason = null; }]) {
    const invalid = clone(tick); change(invalid);
    assert.throws(() => runtimeTerminationReason(invalid), /runtime_termination_reason_unavailable/);
  }
});

test("metadata helper timeout observes its original close or records an unconfirmed exit without retry",
  { timeout: 10000 }, async t => {
    const root = await mkdtemp(path.join(os.tmpdir(), "source3-helper-lifecycle-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    const program = path.join(root, "helper.mjs");
    for (const { mode, delay, expectedCode, expectedSignal } of [
      { mode: "observed-code", delay: 0, expectedCode: 7, expectedSignal: null },
      { mode: "observed-signal", delay: null,
        expectedCode: process.platform === "win32" ? 1 : null,
        expectedSignal: process.platform === "win32" ? null : "SIGTERM" },
      { mode: "unconfirmed", delay: 300, expectedCode: 7, expectedSignal: null },
    ]) {
      await t.test(mode, async () => {
        await writeFile(program, "process.stdin.resume(); setInterval(() => {}, 1000);\n"
          + (delay === null ? "" : `process.on('message', message => {
  if (message === 'fixture-termination-offer') setTimeout(() => process.exit(7), ${delay});
});\n`));
        let child, exited;
        const offers = [];
        let launches = 0;
        const spawnChild = (command, args, spawnOptions) => {
          launches++;
          assert.equal(command, process.execPath);
          assert.deepEqual(args,
            ["-m", "spireagent.workbench.native_source3_collection", "--verify-terminal-summary"]);
          assert.deepEqual(spawnOptions.stdio, ["pipe", "pipe", "pipe"]);
          // Only the executable fixture and its termination reaction are
          // substituted. The helper owner still observes this real child's close.
          child = spawn(process.execPath, [program], { ...spawnOptions,
            stdio: [...spawnOptions.stdio, "ipc"] });
          exited = new Promise(resolve => child.once("close", (code, signal) => resolve({ code, signal })));
          const kill = child.kill.bind(child);
          child.kill = signal => {
            offers.push(signal);
            if (delay === null) return kill(signal); // Real OS termination on both platforms.
            // Windows cannot handle SIGTERM in the child. IPC models an
            // immediate/delayed exit reaction without inventing a close receipt.
            child.send("fixture-termination-offer");
            return true;
          };
          return child;
        };
        const error = await verifiedTerminalSummary({ options: { python_executable: process.execPath },
          directory: root, expected: {}, execution_policy: OWNED_EXECUTION_POLICY },
        { spawnChild, timeoutMs: 1000, exitObservationMs: mode === "unconfirmed" ? 30 : 1000 })
          .then(() => assert.fail("a timed out helper cannot provide a counter proof"), value => value);
        try {
          assert.equal(error.message, mode === "unconfirmed" ? "helper_exit_unconfirmed" : "terminal_summary_helper_timeout");
          assert.deepEqual(error.helper_exit, { pid: child.pid,
            code: mode === "unconfirmed" ? null : expectedCode,
            signal: mode === "unconfirmed" ? null : expectedSignal,
            actual_exit: mode !== "unconfirmed" });
          assert.deepEqual(offers, ["SIGTERM"]);
          assert.equal(launches, 1);
        } finally {
          // The delayed fixture exits by its own timer; no test SIGKILL or second offer.
          assert.deepEqual(await exited, { code: expectedCode, signal: expectedSignal });
        }
      });
    }
  });

test("one fixed program collector request has finite pilot limits and explicit optional Source3", () => {
  assert.equal(validateOptions(options), options);
  for (const invalid of [{ ...options, deadline_ms: 900001 }, { ...options, target_choices: 101 },
    { ...options, record_source3: "false" }, { ...options, python_executable: "from-request" },
    { ...options, endpoint: "https://external.invalid" }, { ...options, arbitrary_module: "hidden" }])
    assert.throws(() => validateOptions(invalid));
});

test("only an explicit owned-current policy permits200 attempts; its omitted budget stays100", () => {
  assert.throws(() => validateOptions({ ...options, max_submissions: 200 }));
  const owned = { ...options, execution_policy: { ...OWNED_EXECUTION_POLICY } };
  assert.equal(validateOptions(owned).max_submissions, 100);
  assert.equal(validateOptions({ ...owned, max_submissions: 200 }).target_choices, 100);
  for (const invalid of [{ ...owned, max_submissions: 201 }, { ...owned, target_choices: 101 },
    { ...owned, execution_policy: null }, { ...owned, execution_policy: {} },
    { ...owned, execution_policy: { ...OWNED_EXECUTION_POLICY, max_known_stale_rejections: 16 } }])
    assert.throws(() => validateOptions(invalid));
});

test("fresh decisions trust only the declared Runtime owner tick joined to its original terminal projection", () => {
  const status = { mode: "auto", lifecycle: "running", tainted: false, pending_request: null,
    session: { agent_state: "known" }, last_result: { request_id: "original", status: "terminal",
      delivery: "not_started", action_id: null, execution: "unknown", effect: "unknown", cancel: "unknown",
      reason: "stale_snapshot_or_binding" } };
  const tick = { type: "fresh_decision_required", original_request_id: "original", status };
  const owned = { ...options, execution_policy: OWNED_EXECUTION_POLICY };
  validateFreshDecisionTick(tick, owned);
  assert.throws(() => validateFreshDecisionTick(tick, options));
  for (const change of [value => { value.original_request_id = "other"; },
    value => { value.status.last_result.status = "pending"; },
    value => { value.status.last_result.delivery = "unknown"; },
    value => { value.status.last_result.delivery = "partially_delivered"; },
    value => { value.status.last_result.action_id = "old-action"; },
    value => { value.status.last_result.reason = "other_reason"; },
    value => { value.status.mode = "human"; }, value => { value.status.tainted = true; },
    value => { value.status.lifecycle = "stopped"; },
    value => { value.status.pending_request = { request_id: "original" }; },
    value => { value.status.session.agent_state = "uncertain"; },
    value => { value.unadvertised = "guess"; }]) {
    const altered = structuredClone(tick); change(altered);
    assert.throws(() => validateFreshDecisionTick(altered, owned));
  }
});

test("public terminal summary carries exact joined counters and a bounded proof scope", () => {
  const summary = { schema: "sts2.evidence/agent-session-terminal-summary-1", run_id: "run",
    content_id: "f".repeat(64), original_submission_count: 4, terminal_result_count: 3,
    known_delivered: 1, known_stale_rejections: 2, consecutive_known_stale_rejections: 1,
    proof_scope: "recorded_dispatch_and_terminal_results", live_eligibility_proved: false };
  assert.equal(validateTerminalSummary(summary, "run"), summary);
  for (const invalid of [{ ...summary, run_id: "other" }, { ...summary, known_stale_rejections: true },
    { ...summary, known_delivered: 2 }, { ...summary, original_submission_count: 2 },
    { ...summary, live_eligibility_proved: true }, { ...summary, consecutive_known_stale_rejections: 3 },
    { ...summary, deferred_events: 2 }]) assert.throws(() => validateTerminalSummary(invalid, "run"));
});

test("owned Current diagnostics identify its actual operation without changing or repeating the request", async () => {
  const original = { raw: { status: "source_capture_incomplete" } }, seen = [], records = [];
  const environment = { async nativeLogicalRequest(...args) { seen.push(args); return original; } };
  observeNativeCurrentFailure(environment, { operation: "current_owned", decodeCurrent: raw => ({ data: raw }),
    record: (...args) => records.push(args), onRecordError: () => assert.fail("unexpected failure") });
  const request = { eager_scope: [], expected_snapshot_id: null };
  assert.equal(await environment.nativeLogicalRequest("current", request), original);
  assert.equal(records.length, 0);
  assert.equal(await environment.nativeLogicalRequest("current_owned", request), original);
  assert.equal(records[0][0], original);
  assert.deepEqual(records[0][1], request);
  assert.equal(records[0][2], "current_owned");
  assert.deepEqual(seen.map(args => args[0]), ["current", "current_owned"]);
});

test("failed Current observer retains the one original reply and never retries after a diagnostic failure", async () => {
  const original = { raw: { status: "source_capture_incomplete", reason: "public_combat_power_facts" },
    statusCode: 409, encodedByteCount: 177 };
  const request = { eager_scope: ["persistent", "interaction", "referents", "catalog"],
    expected_snapshot_id: null };
  const calls = [], errors = [];
  let records = 0;
  const environment = { async nativeLogicalRequest(...args) { calls.push(args); return original; } };
  observeNativeCurrentFailure(environment, { decodeCurrent: raw => ({ data: raw }),
    async record(reply, scope) { assert.equal(reply, original); assert.deepEqual(scope, request);
      records++; throw new Error("private_write_failed"); }, onRecordError: error => errors.push(error.message) });
  assert.equal(await environment.nativeLogicalRequest("current", request), original);
  assert.equal(await environment.nativeLogicalRequest("current", request), original);
  assert.equal(await environment.nativeLogicalRequest("events", { after_cursor: "known" }), original);
  assert.deepEqual(calls.map(args => args[0]), ["current", "current", "events"]);
  assert.equal(records, 1);
  assert.deepEqual(errors, ["private_write_failed"]);
});

test("optional diagnostic I/O cannot delay the original Current reply", { timeout: 1000 }, async () => {
  const original = { raw: { status: "source_capture_incomplete", reason: "public_combat_power_facts" } };
  let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  const environment = { async nativeLogicalRequest() { return original; } };
  observeNativeCurrentFailure(environment, { decodeCurrent: raw => ({ data: raw }),
    record: () => gate, onRecordError: () => assert.fail("unexpected write failure") });
  try {
    assert.equal(await environment.nativeLogicalRequest("current", { eager_scope: [], expected_snapshot_id: null }), original);
  } finally { finish(); }
});

test("early repeated cancellation is owned before Host or program launch", async () => {
  const lifetime = createCollectionLifetime();
  lifetime.stop("external_signal"); lifetime.stop("external_signal");
  let starts = 0, final;
  const deps = { async startEpisode() { starts++; throw new Error("must_not_launch"); } };
  const peer = { async send(value) { final = value; }, async receive() { throw new Error("must_not_query"); } };
  const original = { ...options, output: "/synthetic/not-created" };
  const result = await runNativeSource3(original, operation, peer, deps, lifetime);
  assert.equal(starts, 0);
  assert.equal(result.reason, "external_signal");
  assert.equal(final.teacher_exit, null);
  assert.equal(final.host_started, false);
});

test("retired application reply is discarded only for the exact canceled operation/message/type", async () => {
  const input = new PassThrough(), output = new PassThrough();
  const peer = createPipePeer(input, output, JSON.parse, createCollectionLifetime());
  peer.retireReply(operation, 4, "runtime_continue");
  input.write(JSON.stringify({ schema: PIPE_SCHEMA, type: "runtime_continue", operation_id: operation,
    message_id: 4, continue: true, reason: "late permission" }) + "\n");
  const sourceClosed = { schema: PIPE_SCHEMA, type: "source_closed", operation_id: operation, message_id: 5,
    known_closed: true, source_status: null, close_outcome: "known_closed" };
  input.write(JSON.stringify(sourceClosed) + "\n");
  assert.deepEqual(await peer.receive(), sourceClosed);
  peer.retireReply(operation, 6, "runtime_continue");
  const other = { schema: PIPE_SCHEMA, type: "source_closed", operation_id: operation, message_id: 6 };
  input.write(JSON.stringify(other) + "\n");
  assert.deepEqual(await peer.receive(), other); // A mismatch is never silently discarded.
  peer.retireReply(operation, 7, "runtime_continue", OWNED_PIPE_SCHEMA);
  const wrongSchema = { schema: PIPE_SCHEMA, type: "runtime_continue", operation_id: operation, message_id: 7 };
  input.write(JSON.stringify(wrongSchema) + "\n");
  assert.deepEqual(await peer.receive(), wrongSchema);
  const correctSchema = { ...wrongSchema, schema: OWNED_PIPE_SCHEMA };
  input.write(JSON.stringify(correctSchema) + "\n"); // Only the exact retired v3 reply is dropped.
  input.write(JSON.stringify(sourceClosed) + "\n");
  assert.deepEqual(await peer.receive(), sourceClosed);
  input.destroy(); output.destroy();
});

test("bounded one-message queue and EOF fail closed without another executor", async () => {
  const lifetime = createCollectionLifetime(), input = new PassThrough(), output = new PassThrough();
  const peer = createPipePeer(input, output, JSON.parse, lifetime);
  input.write('{"type":"first"}\n{"type":"unexpected"}\n');
  await assert.rejects(peer.receive(), /parent_eof/);
  assert.equal(lifetime.reason, "pipe_protocol_failed");
  input.destroy(); output.destroy();
});

test("named Runtime status envelopes allow bounded originals while compact final stays16KiB", async () => {
  const input = new PassThrough(), output = new PassThrough();
  const peer = createPipePeer(input, output, JSON.parse, createCollectionLifetime());
  const text = "汉字".repeat(8192);
  await assert.rejects(peer.send({ type: "closed", text }), /pipe_write_unavailable/);
  let bytes = 0; output.on("data", value => { bytes += value.length; });
  await peer.send({ type: "runtime_tick", text });
  assert.ok(bytes > 16 * 1024);
  input.destroy(); output.destroy();
});
