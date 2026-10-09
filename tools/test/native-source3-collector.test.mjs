import assert from "node:assert/strict";
import { PassThrough } from "node:stream";
import test from "node:test";
import { createCollectionLifetime, createPipePeer, PIPE_SCHEMA, runNativeSource3, validateOptions }
  from "../native-source3-collector.mjs";

const operation = "a".repeat(32);
const options = { installation: "/synthetic/game", host_local_root: "/synthetic/host", output: "/synthetic/output",
  endpoint: "http://127.0.0.1:15526", seed: "SYNTHETIC", template_id: "defect-a0-s0",
  target_choices: 100, max_submissions: 100, deadline_ms: 900000, max_input_bytes: 8 * 1024 * 1024,
  max_diagnostic_bytes: 1024 * 1024, experimental_build_acknowledged: false,
  experimental_connector_acknowledged: false, record_source3: true,
  python_executable: "/synthetic/python", teacher_artifact: {}, teacher_descriptor: {} };

test("one fixed program collector request has finite pilot limits and explicit optional Source3", () => {
  assert.equal(validateOptions(options), options);
  for (const invalid of [{ ...options, deadline_ms: 900001 }, { ...options, target_choices: 101 },
    { ...options, record_source3: "false" }, { ...options, python_executable: "from-request" },
    { ...options, endpoint: "https://external.invalid" }, { ...options, arbitrary_module: "hidden" }])
    assert.throws(() => validateOptions(invalid));
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
