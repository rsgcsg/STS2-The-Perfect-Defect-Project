import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { JsonLineProcess, RAW_LINE, isNativeCompactDivergence } from "../src/json-line-process.mjs";
import { ManagedPlayerEnvironmentSession, referentValue, startManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { ProjectionMemo, ProjectionMemoDivergence } from "../src/managed-projection-memo.mjs";
import { objectsRoot } from "../src/managed-native-compact.mjs";
import { captureManagedPublicBasis } from "../src/managed-public-basis.mjs";
import { createSession, reduce, modelView } from "@rsgcsg/sts2-connector-client/p1-global";
import { MANAGED_TEXT_MENU_PROFILE, ManagedTextMenuSessionAdapter } from "../src/managed-text-menu-map.mjs";
import { MANAGED_TEXT_MENU_V2_PROFILE, ManagedTextMenuV2SessionAdapter } from "../src/managed-text-menu-v2.mjs";

const sha = (text) => createHash("sha256").update(text, "utf8").digest("hex");
const REF = /^native-[1-9][0-9]*$/u;
const PILES = new Set(["draw_pile", "discard_pile", "exhaust_pile"]);
const stable = (value) => JSON.parse(JSON.stringify(value, (key, item) => key === "observed_at" ? null : item));

function player(hp = 80) {
  return {
    name: "The Ironclad", hp, max_hp: 80, gold: 99, relics: [], potions: [],
    deck: [{
      native_ref: "native-3", id: "CARD.STRIKE_IRONCLAD", name: "Strike", cost: 1, type: "Attack",
      rarity: "Basic", upgraded: false, description: "Deal 6 damage.",
      public_details: { keywords: [], card_previews: [] }, public_details_complete: true, public_details_missing: []
    }]
  };
}

function eventState(name = "Neow", hp = 80) {
  return {
    type: "decision", decision: "event_choice", context: { act: 1, floor: 1, room_type: "Event" },
    room_ref: "native-1", event_ref: "native-2", event_name: name, event_id: "NEOW", ancient: true,
    in_dialogue: false, event_public_complete: true, description: "Choose.",
    options: [
      { index: 0, native_ref: "native-4", title: "Open", description: "Available", is_locked: false, is_enabled: true,
        is_proceed: false, was_chosen: false, will_kill_player: false, tooltips: [] },
      { index: 1, native_ref: "native-5", title: "Locked", description: "Unavailable", is_locked: true, is_enabled: false,
        is_proceed: false, was_chosen: false, will_kill_player: false, tooltips: [] }
    ],
    player: player(hp)
  };
}

/** A test-only managed-native-compact-1 writer over the same full states the old
 * writer emits; digests are over the bytes it emits. */
class CompactWriter {
  #instance = 0;
  #seq = 0;
  #anchor = 0;
  #sent = new Map();
  constructor(interval = 64) { this.interval = interval; }

  #encode(state) {
    const table = new Map();
    const walk = (value, view, inPile) => {
      if (Array.isArray(value)) return value.map(item => walk(item, view, inPile));
      if (value == null || typeof value !== "object") return value;
      const pile = inPile || Object.hasOwn(value, "ordering_semantics");
      const out = {};
      for (const [key, item] of Object.entries(value))
        out[key] = walk(item, key.replace(/[^a-z0-9_]/gu, "").slice(0, 32) || "obj", pile || PILES.has(key));
      if (!pile && typeof value.native_ref === "string" && REF.test(value.native_ref)) {
        const key = `${value.native_ref}#${/^[a-z]/u.test(view) ? view : "obj"}`;
        table.set(key, out);
        return { $obj: key };
      }
      return out;
    };
    return { skeleton: walk(state, "root", false), table };
  }

  frame(state, kind) {
    const { skeleton, table } = this.#encode(state);
    const bodies = new Map([...table].map(([key, body]) => [key, JSON.stringify(body)]));
    const digests = new Map([...bodies].map(([key, text]) => [key, sha(text)]));
    if (kind === "full") { this.#instance += 1; this.#seq = 0; this.#anchor = 0; }
    else this.#seq += 1;
    if (kind !== "full" && this.#seq - this.#anchor >= this.interval) kind = "reconcile";
    const removed = [...this.#sent.keys()].filter(key => !digests.has(key));
    const upserted = [...digests].filter(([key, digest]) => this.#sent.get(key) !== digest).map(([key]) => key);
    const carry = kind === "delta" ? upserted : [...digests.keys()];
    const frame = {
      type: "compact_state", schema: "managed-native-compact-1", frame: kind,
      episode_instance: `test-instance-${this.#instance}`, seq: this.#seq,
      base_seq: kind === "full" ? null : this.#seq - 1,
      ...(kind === "full" ? { start: { kind: "new_run" } } : {}),
      ...(kind === "delta" ? { removed } : {}),
      ...(kind === "reconcile" ? { upserted, removed } : {}),
      skeleton,
      objects: Object.fromEntries(carry.map(key => [key, table.get(key)])),
      object_digests: Object.fromEntries(carry.map(key => [key, digests.get(key)])),
      objects_root_sha256: objectsRoot(digests)
    };
    if (kind !== "delta") this.#anchor = this.#seq;
    this.#sent = digests;
    return JSON.stringify(frame);
  }
}

function received(line) {
  const message = JSON.parse(line);
  if (message.type === "compact_state" || isNativeCompactDivergence(message))
    Object.defineProperty(message, RAW_LINE, { value: line, enumerable: false });
  return message;
}

/** The fast_chain native's own fatal report (#433 S3); the real native exits 70 after it. */
const NATIVE_DIVERGENCE_LINE = '{"type":"error","code":"compact_state_divergence","message":"card_detail_reconcile"}';

function successorState() {
  const state = eventState("Successor", 70);
  state.options[0].title = "Opened";  // a tabled body changes, so the delta upserts it
  return state;
}

function fakeNative({ successors = [successorState()], mutateLine = null, divergeOn = null } = {}) {
  const writer = new CompactWriter();
  const log = [];
  const requests = [];
  let compact = false;
  let step = 0;
  let current = eventState();
  return {
    log,
    requests,
    async request(request, timeoutMs) {
      log.push(request.cmd);
      requests.push(JSON.stringify([request, timeoutMs]));
      if (request.cmd === divergeOn) return received(NATIVE_DIVERGENCE_LINE);
      if (request.cmd === "start_run" || request.cmd === "reset_run") {
        current = eventState();
        return compact ? received(writer.frame(current, "full")) : structuredClone(current);
      }
      if (request.cmd === "configure_output") {
        assert.deepEqual(request, { cmd: "configure_output", format: "managed-native-compact-1", reconcile_interval: 64 });
        compact = true;
        return received(writer.frame(current, "full"));
      }
      if (request.cmd === "action") {
        current = successors[Math.min(step, successors.length - 1)];
        step += 1;
        if (!compact) return structuredClone(current);
        const line = writer.frame(current, "delta");
        return received(mutateLine ? mutateLine(line) : line);
      }
      throw new Error(`unexpected ${request.cmd}`);
    }
  };
}

function session(process, compactOutput) {
  return new ManagedPlayerEnvironmentSession({
    process, runtimeInstanceId: "managed-runtime-test", environmentFingerprint: "managed-environment-test",
    identityMode: "sequence", ...(compactOutput === undefined ? {} : { compactOutput })
  });
}

async function act(target, snapshot, requestId = "r1") {
  return target.submit({ requestId, expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: snapshot.bound_actions.actions[0].bound_action_id });
}

test("old chain never sends configure_output and keeps its exact requests", async () => {
  const native = fakeNative();
  const old = session(native);
  assert.equal(old.compactOutput, null);
  assert.equal(old.episodeInstance, null);
  const snapshot = await old.mount({ seed: "SEED" });
  const receipt = await act(old, snapshot);
  assert.equal(receipt.delivery, "delivered");
  assert.deepEqual(native.log, ["start_run", "action"]);
  // Byte-for-byte what the pre-slice session (0369eacf) sends for the same run.
  assert.deepEqual(native.requests, [
    '[{"cmd":"start_run","character":"Ironclad","seed":"SEED","lang":"en","ascension":0},10000]',
    '[{"action":"choose_option","args":{"event_ref":"native-2","option_index":0,"option_ref":"native-4","room_ref":"native-1"},"cmd":"action"},10000]'
  ]);
});

for (const shareUnchanged of [false, true]) test(`compact output rebuilds the same snapshots and receipts as the full writer (shareUnchanged ${shareUnchanged})`, async () => {
  const fullNative = fakeNative(), compactNative = fakeNative();
  const old = session(fullNative), fast = session(compactNative, { sampleRate: 1, shareUnchanged });
  const oldSnapshot = await old.mount({ seed: "SEED" });
  const fastSnapshot = await fast.mount({ seed: "SEED" });
  assert.deepEqual(compactNative.log, ["start_run", "configure_output"]);
  assert.deepEqual(stable(fastSnapshot), stable(oldSnapshot));
  assert.equal(fast.episodeInstance, "test-instance-1");
  assert.deepEqual(fast.compactOutput, { reconcileInterval: 64, sampleRate: 1, shareUnchanged, configured: true });
  const oldReceipt = await act(old, oldSnapshot), fastReceipt = await act(fast, fastSnapshot);
  assert.equal(fastReceipt.successor.interaction.content.surface.options[0].title, "Opened");
  assert.deepEqual(stable(fastReceipt), stable(oldReceipt));
  // In-process reset starts a new instance with a full frame; no second configure.
  const reset = await fast.mount({ seed: "SEED2", reset: true });
  assert.equal(fast.episodeInstance, "test-instance-2");
  assert.equal(reset.status, oldSnapshot.status);
  assert.deepEqual(compactNative.log, ["start_run", "configure_output", "action", "reset_run"]);
  assert.ok(fast.performance().compact_reconstruct_sampled.count >= 3);
});

test("a diverged successor is delivered, never acted on and closes mutation", async () => {
  const native = fakeNative({ mutateLine: (line) => {
    const frame = JSON.parse(line); frame.objects_root_sha256 = "0".repeat(64); return JSON.stringify(frame);
  } });
  const fast = session(native, { sampleRate: 0 });
  const snapshot = await fast.mount({ seed: "SEED" });
  const first = await act(fast, snapshot);
  assert.equal(first.delivery, "delivered");
  assert.equal(first.reason_code, "compact_state_divergence");
  assert.match(first.detail, /root_mismatch/u);
  assert.equal(first.successor, null);
  assert.equal(first.retry.allowed, false);
  assert.equal(fast.tainted, true);
  assert.equal(fast.taintReason, "compact_state_divergence");
  assert.throws(() => fast.observe(), /managed_session_tainted_after_compact_state_divergence/u);
  const later = await act(fast, snapshot, "r2");
  assert.equal(later.delivery, "not_delivered");
  assert.equal(later.reason_code, "runtime_tainted_after_compact_state_divergence");
  await assert.rejects(fast.mount({ seed: "SEED", reset: true }), /tainted/u);
  assert.deepEqual(native.log, ["start_run", "configure_output", "action"]);
});

test("text-menu consumers report a diverged session as such, never as unknown (review S3)", async () => {
  const native = fakeNative({ mutateLine: (line) => {
    const frame = JSON.parse(line); frame.objects_root_sha256 = "0".repeat(64); return JSON.stringify(frame);
  } });
  const fast = session(native, { sampleRate: 0 });
  const snapshot = await fast.mount({ seed: "SEED" });
  await act(fast, snapshot);
  assert.equal(fast.taintReason, "compact_state_divergence");
  for (const [Adapter, profile] of [[ManagedTextMenuSessionAdapter, MANAGED_TEXT_MENU_PROFILE],
    [ManagedTextMenuV2SessionAdapter, MANAGED_TEXT_MENU_V2_PROFILE]]) {
    // observe() refuses a diverged state, so the rejection must not reach it.
    const result = await new Adapter(fast).submit({ request_id: `t-${profile}`, expected_snapshot_id: "s",
      action_id: "a", input_profile: profile });
    assert.equal(result.status, "not_applied", profile);
    assert.equal(result.reason_code, "runtime_tainted_after_compact_state_divergence", profile);
    assert.equal(result.retry, "never", profile);
    assert.equal(result.successor, null, profile);
  }
  assert.deepEqual(native.log, ["start_run", "configure_output", "action"]);
});

test("the evidence tap sees every compact line before it is checked, the diverging one too", async () => {
  const native = fakeNative({ mutateLine: (line) => {
    const frame = JSON.parse(line); frame.objects_root_sha256 = "0".repeat(64); return JSON.stringify(frame);
  } });
  const fast = session(native, { sampleRate: 0 });
  const seen = [];
  fast.compactLineTap = (line, frame) => seen.push([line, frame.seq]);
  const snapshot = await fast.mount({ seed: "SEED" });
  await act(fast, snapshot);
  assert.deepEqual(seen.map(([, seq]) => seq), [0, 1]);
  assert.equal(JSON.parse(seen[1][0]).objects_root_sha256, "0".repeat(64));
  assert.throws(() => { fast.compactLineTap = 1; }, /function or null/u);
  const old = session(fakeNative());
  const legacy = [];
  old.compactLineTap = (line) => legacy.push(line);
  await act(old, await old.mount({ seed: "SEED" }));
  assert.deepEqual(legacy, []);
});

test("the sampled path checks emitted bytes; the hot path does not re-hash bodies", async () => {
  // Same values, different bytes: re-serialising consumers cannot see this.
  const escape = (line) => {
    assert.ok(line.includes('"title":"Opened"'));
    return line.replace('"title":"Opened"', '"title":"Opene\\u0064"');
  };
  for (const [sampleRate, ok] of [[0, true], [1, false]]) {
    const fast = session(fakeNative({ mutateLine: escape }), { sampleRate });
    const snapshot = await fast.mount({ seed: "SEED" });
    const receipt = await act(fast, snapshot);
    if (ok) assert.equal(receipt.reason_code, null);
    else {
      assert.equal(receipt.reason_code, "compact_state_divergence");
      assert.match(receipt.detail, /object_digest_mismatch|root_mismatch/u);
    }
  }
  // R7 games check every frame whatever the rate.
  const r7 = session(fakeNative({ mutateLine: escape }), { sampleRate: 0 });
  const snapshot = await r7.mount({ seed: "SEED", compactCheckAll: true });
  assert.equal((await act(r7, snapshot)).reason_code, "compact_state_divergence");
});

test("a full-format state after configure_output is a divergence, not a fallback", async () => {
  const native = fakeNative();
  const original = native.request;
  native.request = async (request) => request.cmd === "action" ? (native.log.push("action"), eventState("Successor"))
    : original(request);
  const fast = session(native, {});
  const snapshot = await fast.mount({ seed: "SEED" });
  const receipt = await act(fast, snapshot);
  assert.equal(receipt.reason_code, "compact_state_divergence");
  assert.match(receipt.detail, /legacy_state_after_configure/u);
});

test("configure refused or a mount without a full frame fails closed", async () => {
  const refused = session({ async request(request) {
    if (request.cmd === "start_run") return eventState();
    return { type: "error", message: "unknown command" };
  } }, {});
  await assert.rejects(refused.mount({ seed: "SEED" }), error => error.reason === "configure_output_refused");
  assert.equal(refused.taintReason, "compact_state_divergence");

  const native = fakeNative();
  const original = native.request;
  const fast = session(native, {});
  const snapshot = await fast.mount({ seed: "SEED" });
  await act(fast, snapshot);
  const writer = new CompactWriter();
  writer.frame(eventState(), "full");
  native.request = async (request) => request.cmd === "reset_run" ? received(writer.frame(eventState(), "delta"))
    : original(request);
  await assert.rejects(fast.mount({ seed: "SEED", reset: true }), error => error.reason === "instance_start_not_full");
  assert.equal(fast.taintReason, "compact_state_divergence");
});

test("a new instance must be fresh and start the way it was asked (review S2)", async () => {
  for (const [edit, reason] of [
    [frame => { frame.episode_instance = "test-instance-1"; }, "instance_reused"],
    [frame => { frame.start = { kind: "from_save", save_sha256: "e".repeat(64) }; }, "start_mismatch"]
  ]) {
    const native = fakeNative();
    const original = native.request;
    const fast = session(native, {});
    await act(fast, await fast.mount({ seed: "SEED" }));
    const writer = new CompactWriter();
    writer.frame(eventState(), "full");
    native.request = async (request) => {
      if (request.cmd !== "reset_run") return original(request);
      const frame = JSON.parse(writer.frame(eventState(), "full"));
      edit(frame);
      return received(JSON.stringify(frame));
    };
    await assert.rejects(fast.mount({ seed: "SEED", reset: true }), error => error.reason === reason, reason);
    assert.equal(fast.taintReason, "compact_state_divergence");
  }
});

test("an unexpected full frame on an action is a divergence", async () => {
  const native = fakeNative();
  const writer = new CompactWriter();
  const original = native.request;
  native.request = async (request) => request.cmd === "action" ? received(writer.frame(eventState("Successor"), "full"))
    : original(request);
  const fast = session(native, {});
  const receipt = await act(fast, await fast.mount({ seed: "SEED" }));
  assert.match(receipt.detail, /unexpected_full_frame/u);
});

test("compactOutput is validated", () => {
  assert.throws(() => session({ request() {} }, { reconcileInterval: 0 }), TypeError);
  assert.throws(() => session({ request() {} }, { sampleRate: 2 }), TypeError);
  assert.throws(() => session({ request() {} }, "on"), TypeError);
});

test("JsonLineProcess keeps the emitted line only on compact frames", async () => {
  const compactLine = '{"type":"compact_state","x":"\\u0041"}';
  const script = `process.stdin.once("data", () => { console.log(${JSON.stringify(compactLine)}); });`
    + `process.stdin.once("end", () => process.exit(0));`;
  const child = new JsonLineProcess({ command: process.execPath, args: ["-e", `console.log('{"type":"ready"}');${script}`] });
  try {
    const ready = await child.nextMessage(5_000);
    assert.equal(ready[RAW_LINE], undefined);
    const frame = await child.request({ cmd: "ping" }, 5_000);
    assert.equal(frame[RAW_LINE], compactLine);
    assert.equal(frame.x, "A");
    assert.deepEqual(Object.keys(frame), ["type", "x"]);
    assert.equal(JSON.stringify(frame), '{"type":"compact_state","x":"A"}');
  } finally {
    await child.stop({ force: true });
  }
});

test("a native-reported divergence on an action is delivered, never retried, no successor (#433 S3)", async () => {
  for (const compactOutput of [{ sampleRate: 0 }, undefined]) {
    const native = fakeNative({ divergeOn: "action" });
    const fast = session(native, compactOutput);
    const seen = [];
    fast.compactLineTap = (line, frame) => seen.push([line, frame.type]);
    const snapshot = await fast.mount({ seed: "SEED" });
    const first = await act(fast, snapshot);
    assert.equal(first.delivery, "delivered");
    assert.equal(first.reason_code, "compact_state_divergence");
    assert.match(first.detail, /native_reported:card_detail_reconcile/u);
    assert.deepEqual(first.retry, { allowed: false, reason: "compact_state_divergence_requires_process_replacement" });
    assert.equal(first.successor, null);
    assert.equal(fast.taintReason, "compact_state_divergence");
    assert.throws(() => fast.observe(), /managed_session_tainted_after_compact_state_divergence/u);
    // The exact line reached the evidence tap (an original); nothing is sent on the state.
    assert.deepEqual(seen.at(-1), [NATIVE_DIVERGENCE_LINE, "error"]);
    const later = await act(fast, snapshot, "r2");
    assert.equal(later.delivery, "not_delivered");
    assert.equal(later.reason_code, "runtime_tainted_after_compact_state_divergence");
    assert.equal(later.retry.reason, "compact_state_divergence_requires_process_replacement");
    assert.equal(native.log.filter(cmd => cmd === "action").length, 1);
  }
});

test("a native-reported divergence while mounting taints the session as divergence (#433 S3)", async () => {
  for (const divergeOn of ["start_run", "configure_output"]) {
    const native = fakeNative({ divergeOn });
    const fast = session(native, { sampleRate: 0 });
    await assert.rejects(fast.mount({ seed: "SEED" }), /compact_state_divergence:native_reported/u);
    assert.equal(fast.taintReason, "compact_state_divergence", divergeOn);
  }
});

test("the JSON-line process hands the native's divergence line over with its exact bytes, then sees exit 70", async () => {
  const script = `process.stdin.once("data", () => { process.stdout.write(${JSON.stringify(`${NATIVE_DIVERGENCE_LINE}\n`)}, () => process.exit(70)); });`;
  const child = new JsonLineProcess({ command: process.execPath, args: ["-e", script] });
  const reply = await child.request({ cmd: "action" }, 5_000);
  assert.equal(isNativeCompactDivergence(reply), true);
  assert.equal(reply[RAW_LINE], NATIVE_DIVERGENCE_LINE);
  assert.equal((await child.exit).code, 70);
  // An ordinary native error is not a divergence report and gets no raw line.
  assert.equal(isNativeCompactDivergence({ type: "error", message: "x" }), false);
});

test("design C differential: with shareUnchanged and the light frozen capture, P1 reduce outputs are byte-identical", async () => {
  const run = async (c) => {
    const target = session(fakeNative(), { sampleRate: 0, shareUnchanged: c });
    let state = createSession("differential", "managed", "r1.9");
    const out = [];
    const step = (ordinal) => {
      const { basis } = captureManagedPublicBasis(target, { generationId: "g", gameContinuityId: "c", ordinal, fullCheck: !c });
      if (c) assert.ok(Object.isFrozen(basis.snapshot));
      const reduction = reduce(state, basis);
      state = reduction.state;
      // observed_at is the capture clock, the only field that differs between two runs.
      out.push(JSON.stringify(stable({ observation: reduction.observation, model: modelView(reduction.observation),
        forced: reduction.forced_choice_id, excluded: reduction.excluded, complete: reduction.complete, state })));
    };
    const snapshot = await target.mount({ seed: "SEED" });
    step(1);
    await act(target, snapshot);
    step(2);
    return out;
  };
  const off = await run(false), on = await run(true);
  assert.equal(off.length, 2);
  assert.deepEqual(on, off);
});

/** Paths of objects that are the same object in both trees (outermost only). */
function sharedSubtrees(left, right, path = "$", out = []) {
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") return out;
  if (left === right) { out.push(path); return out; }
  for (const key of Object.keys(left)) if (Object.hasOwn(right, key)) sharedSubtrees(left[key], right[key], `${path}.${key}`, out);
  return out;
}

test("E5 projection memo: two consecutive captures keep an unchanged subtree as the same frozen object", async () => {
  const run = async (projectionMemo) => {
    const target = new ManagedPlayerEnvironmentSession({ process: fakeNative(), runtimeInstanceId: "managed-runtime-test",
      environmentFingerprint: "managed-environment-test", identityMode: "crypto",
      compactOutput: { sampleRate: 0, shareUnchanged: true }, projectionMemo });
    const snapshot = await target.mount({ seed: "SEED" });
    const first = captureManagedPublicBasis(target, { generationId: "g", gameContinuityId: "c", ordinal: 1, fullCheck: false });
    await act(target, snapshot);
    const second = captureManagedPublicBasis(target, { generationId: "g", gameContinuityId: "c", ordinal: 2, fullCheck: false });
    return { target, first, second };
  };
  const on = await run({ checkInterval: 1 });
  const off = await run(null);
  const deckCard = (basis) => basis.reads.find(read => read.kind === "run_deck").response.content.cards[0];
  // The deck card did not change between the two native frames: same deep-frozen object.
  assert.equal(deckCard(on.second.basis), deckCard(on.first.basis));
  assert.ok(Object.isFrozen(deckCard(on.second.basis)));
  assert.ok(sharedSubtrees(on.first.basis, on.second.basis).includes("$.reads.0.response.content.cards.0"));
  // Same public position, changed native content (option 0 retitled; its id stays the same under A1):
  // the memo key is the native input object, so the changed option is rebuilt, never served stale.
  const option = (basis) => basis.snapshot.interaction.content.surface.options[0];
  assert.equal(option(on.first.basis).title, "Open");
  assert.equal(option(on.second.basis).title, "Opened");
  const optionReferent = (basis) => basis.snapshot.referents.find(item => item.referent_id === option(basis).entity_id);
  assert.equal(optionReferent(on.second.basis).referent_id, optionReferent(on.first.basis).referent_id);
  assert.notEqual(optionReferent(on.second.basis), optionReferent(on.first.basis));
  // Without the memo the projection builds it anew each step.
  assert.notEqual(deckCard(off.second.basis), deckCard(off.first.basis));
  // Same public bytes either way (observed_at / capture clock aside), and the per-frame check ran.
  assert.equal(JSON.stringify(stable(on.second.basis)), JSON.stringify(stable(off.second.basis)));
  assert.deepEqual(on.target.projectionMemo.checks, 2);
  assert.ok(on.target.projectionMemo.hits > 0);
});

function memoSession(native, projectionMemo, compactOutput = { sampleRate: 0, shareUnchanged: true }) {
  return new ManagedPlayerEnvironmentSession({ process: native, runtimeInstanceId: "managed-runtime-test",
    environmentFingerprint: "managed-environment-test", identityMode: "crypto", compactOutput, projectionMemo });
}

test("E5 projection memo is validated: shareUnchanged compact output and fast_chain only", async () => {
  assert.throws(() => memoSession({ request() {} }, { checkInterval: 64 }, { sampleRate: 0 }), /shareUnchanged/u);
  assert.throws(() => memoSession({ request() {} }, { checkInterval: 0 }), /checkInterval/u);
  assert.throws(() => memoSession({ request() {} }, { every: 1 }), /projectionMemo.every/u);
  assert.throws(() => memoSession({ request() {} }, { checkSeed: 1.5 }), /checkSeed/u);
  assert.equal(memoSession({ request() {} }, null).projectionMemo, null);
  assert.deepEqual(memoSession({ request() {} }, {}).projectionMemo, { hits: 0, misses: 0, checks: 0, check_interval: 64 });
  await assert.rejects(startManagedPlayerEnvironmentSession({ compactOutput: { shareUnchanged: true }, projectionMemo: {} }),
    /fast_chain only/u);
});

test("E5 projection memo: a memo that disagrees with a fresh projection fails the step loudly, no fallback", async () => {
  const original = ProjectionMemo.prototype.value;
  let corrupt = false;
  ProjectionMemo.prototype.value = function (site, input, key, build) {
    const value = original.call(this, site, input, key, build);
    return corrupt && site === "run_deck_card" ? Object.freeze({ ...value, name: "stale" }) : value;
  };
  try {
    const target = memoSession(fakeNative(), { checkInterval: 1 });
    const snapshot = await target.mount({ seed: "SEED" });
    corrupt = true;
    const receipt = await act(target, snapshot);
    // Its own reason code, never mixed into managed_successor_projection_failed.
    assert.equal(receipt.delivery, "delivered");
    assert.equal(receipt.reason_code, "projection_memo_divergence");
    assert.match(receipt.detail, /^projection_memo_divergence: /u);
    assert.equal(receipt.successor, null);
    assert.deepEqual(receipt.retry, { allowed: false, reason: "projection_memo_divergence_requires_process_replacement" });
    assert.equal(target.taintReason, "projection_memo_divergence");
    assert.throws(() => target.observe(), /managed_session_tainted_after_projection_memo_divergence/u);
    const later = await act(target, snapshot, "r2");
    assert.equal(later.delivery, "not_delivered");
    assert.equal(later.reason_code, "runtime_tainted_after_projection_memo_divergence");
    assert.equal(later.retry.reason, "projection_memo_divergence_requires_process_replacement");
    // Frame 0 is always checked: a divergence while mounting taints the session the same way.
    const mounting = memoSession(fakeNative(), { checkInterval: 64 });
    await assert.rejects(mounting.mount({ seed: "SEED" }), ProjectionMemoDivergence);
    assert.equal(mounting.taintReason, "projection_memo_divergence");
  } finally {
    ProjectionMemo.prototype.value = original;
  }
});

test("E5 projection memo: check gaps are uniform in ceil(N/2)..N, frame 0 included, seeded and restarted by reset", () => {
  const dueFrames = (checkInterval, checkSeed, frames) => {
    const memo = new ProjectionMemo({ checkInterval, checkSeed });
    const due = [];
    for (let frame = 0; frame < frames; frame++) if (memo.checkDue()) due.push(frame);
    return { memo, due };
  };
  for (const n of [1, 2, 7, 64]) {
    const { memo, due } = dueFrames(n, 12345, 40_000);
    assert.equal(due[0], 0);
    const gaps = due.slice(1).map((frame, index) => frame - due[index]);
    const low = Math.ceil(n / 2);
    assert.ok(gaps.every(gap => gap >= low && gap <= n), `N=${n}`);
    // Every gap value in the range occurs (not a fixed phase).
    assert.equal(new Set(gaps).size, n - low + 1, `N=${n}`);
    assert.equal(memo.checks, due.length);
  }
  assert.deepEqual(dueFrames(64, 7, 2000).due, dueFrames(64, 7, 2000).due);
  assert.notDeepEqual(dueFrames(64, 7, 2000).due, dueFrames(64, 8, 2000).due);
  // reset() starts a new run: frame 0 is checked again.
  const { memo } = dueFrames(64, 7, 5);
  memo.reset();
  assert.equal(memo.checkDue(), true);
});

test("E5 projection memo: every referent field is in its memo key (one changed field rebuilds)", () => {
  const memo = new ProjectionMemo({ checkInterval: 64, checkSeed: 1 });
  const card = Object.freeze({ name: "Strike", cost: "1" });
  // [referentId, role, kind, label, enabled, selected, includeEntityId]
  const fields = ["card-1", "card", "entity", "Strike", true, false, true];
  const base = referentValue(memo, fields, card);
  assert.ok(Object.isFrozen(base));
  assert.equal(referentValue(memo, [...fields], card), base);
  // The same frozen card object with only `selected` switched must be rebuilt, and so on for each field.
  const variants = [["card-2", 0], ["hand", 1], ["other", 2], ["Bash", 3], [false, 4], [true, 5], [false, 6]];
  for (const [value, index] of variants) {
    const changed = fields.with(index, value);
    const memoized = referentValue(memo, changed, card);
    assert.notEqual(memoized, base, `field ${index}`);
    assert.deepEqual(memoized, referentValue(null, changed, card), `field ${index}`);
    assert.notDeepEqual(memoized, base, `field ${index}`);
  }
  assert.equal(referentValue(memo, fields.with(5, true), card).state.selected, true);
  assert.equal(referentValue(memo, fields.with(0, "card-2"), card).referent_id, "card-2");
  // An equal but different properties object is not the same native input.
  assert.notEqual(referentValue(memo, fields, Object.freeze({ ...card })), base);
});

test("E5 projection memo: the lazy raw-state digest has the eager value", async () => {
  const run = async (projectionMemo) => {
    const target = memoSession(fakeNative(), projectionMemo);
    await target.mount({ seed: "SEED" });
    return target.observe();
  };
  const on = await run({ checkInterval: 1024 }), off = await run(null);
  assert.equal(JSON.stringify(stable(on)), JSON.stringify(stable(off)));
});
