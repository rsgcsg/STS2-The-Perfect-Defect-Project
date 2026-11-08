import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  CompactStateDivergence,
  CompactStateReconstructor,
  compactBodySlices,
  compactFrameSchemaError,
  compactSampleSchedule,
  objectsRoot
} from "../src/managed-native-compact.mjs";

// The WP0 contract fixtures; the expected outcomes mirror
// python/tests/test_prewrite_wp0_contracts.py (the reference consumer).
const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "contracts", "managed-native", "fixtures");
const raw = (name) => readFileSync(join(FIXTURES, name), "utf8").split("\n").filter(line => line.length > 0);
const parsed = (name) => raw(name).map(line => JSON.parse(line));
const sha = (text) => createHash("sha256").update(text, "utf8").digest("hex");
const ascii = (value) => JSON.stringify(value).replace(/[\u0080-￿]/gu,
  char => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`);

/** Runs both consumers (design C shareUnchanged off and on): same states byte for byte, same divergence. */
function reconstruct(lines, { sampled = true, reconcileInterval = 64 } = {}) {
  const run = (shareUnchanged) => {
    const consumer = new CompactStateReconstructor({ reconcileInterval, shareUnchanged });
    try { return { states: lines.map(line => consumer.accept(JSON.parse(line), { line, sampled })) }; }
    catch (error) { return { error }; }
  };
  const off = run(false), on = run(true);
  assert.equal(on.error?.reason, off.error?.reason);
  if (off.error) throw off.error;
  assert.equal(JSON.stringify(on.states), JSON.stringify(off.states));
  return off.states;
}

function divergence(fn, reason) {
  assert.throws(fn, error => error instanceof CompactStateDivergence && error.reason === reason
    && error.code === "compact_state_divergence");
}

/** Bijective, step-persistent relabelling of native refs by first appearance. */
function rename(states) {
  const names = new Map();
  const walk = (value) => {
    if (Array.isArray(value)) return value.map(walk);
    if (value != null && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, walk(v)]));
    if (typeof value === "string" && /^native-[1-9][0-9]*$/u.test(value)) {
      if (!names.has(value)) names.set(value, `ref-${names.size + 1}`);
      return names.get(value);
    }
    return value;
  };
  return states.map(state => JSON.stringify(walk(state)));
}

function sealedFull(frame) {
  frame.object_digests = Object.fromEntries(Object.entries(frame.objects).map(([key, body]) => [key, sha(JSON.stringify(body))]));
  frame.objects_root_sha256 = objectsRoot(new Map(Object.entries(frame.object_digests)));
  return JSON.stringify(frame);
}

test("compact reconstruction equals the legacy full writer after renaming", () => {
  const lines = raw("compact-episode.jsonl");
  assert.deepEqual(lines.map(line => JSON.parse(line).frame), ["full", "delta", "delta", "reconcile"]);
  const legacy = parsed("legacy-episode.jsonl");
  for (const sampled of [true, false]) {
    const states = reconstruct(lines, { sampled });
    assert.deepEqual(rename(states), rename(legacy));
    assert.notDeepEqual(states, legacy);
    assert.ok(!JSON.stringify(states.map(state => state.draw_pile)).includes("native-"));
  }
});

test("body digests are over the emitted bytes, not a re-serialisation", () => {
  const plain = raw("compact-episode.jsonl"), escaped = raw("compact-episode-escaped.jsonl");
  assert.notDeepEqual(plain, escaped);
  assert.deepEqual(reconstruct(escaped), reconstruct(plain));
  const mixed = escaped.map((line, index) => {
    const frame = JSON.parse(line), source = JSON.parse(plain[index]);
    frame.object_digests = source.object_digests;
    frame.objects_root_sha256 = source.objects_root_sha256;
    return ascii(frame);
  });
  divergence(() => reconstruct(mixed), "object_digest_mismatch");
  assert.deepEqual(rename(reconstruct(mixed, { sampled: false })), rename(reconstruct(plain)));
});

test("raw slices are the exact body bytes", () => {
  const line = raw("compact-episode-escaped.jsonl")[0];
  const frame = JSON.parse(line);
  const slices = compactBodySlices(line);
  assert.deepEqual([...slices.keys()].sort(), Object.keys(frame.objects).sort());
  for (const [key, slice] of slices) {
    assert.deepEqual(JSON.parse(slice), frame.objects[key]);
    assert.equal(sha(slice), frame.object_digests[key]);
  }
});

test("a reappearing object must be resent", () => {
  const frames = parsed("compact-episode.jsonl");
  assert.ok(frames[1].removed.includes("native-12#hand") && Object.hasOwn(frames[2].objects, "native-12#hand"));
  divergence(() => reconstruct(raw("invalid-reappear-not-resent.jsonl")), "unresolved_placeholder");
});

for (const [name, reason] of [
  ["invalid-missed-upsert.jsonl", "root_mismatch"],
  ["invalid-stale-bookkeeping.jsonl", "reconcile_mismatch"],
  ["invalid-wrong-base.jsonl", "base_mismatch"],
  ["invalid-orphan.jsonl", "orphan_object"],
  ["invalid-removed-not-held.jsonl", "removed_not_held"],
  ["invalid-removed-and-upserted.jsonl", "removed_and_upserted"],
  ["invalid-noop-upsert.jsonl", "noop_upsert"],
  ["invalid-reconcile-removed-not-held.jsonl", "reconcile_mismatch"],
  ["invalid-reconcile-upserted-unchanged.jsonl", "reconcile_mismatch"],
  ["invalid-full-seq-nonzero.jsonl", "full_seq_nonzero"]
]) {
  test(`divergence fails closed: ${name}`, () => {
    divergence(() => reconstruct(raw(name)), reason);
    divergence(() => reconstruct(raw(name), { sampled: false }), reason);
  });
}

for (const [name, hot, sampled] of [
  ["invalid-pile-card-in-table.jsonl", "object_key_ref_mismatch", "schema_invalid"],
  ["invalid-pile-key-with-ref.jsonl", "pile_placeholder", "schema_invalid"],
  ["invalid-pile-card-other-view.jsonl", "pile_placeholder", "pile_placeholder"],
  ["invalid-discard-view.jsonl", "pile_placeholder", "schema_invalid"],
  ["invalid-pile-container-tabled.jsonl", "pile_tabled", "pile_tabled"]
]) {
  test(`pile cards are never tabled: ${name}`, () => {
    divergence(() => reconstruct(raw(name), { sampled: false }), hot);
    divergence(() => reconstruct(raw(name)), sampled);
  });
}

test("the start is checked on both paths: invalid-start-without-save-digest.jsonl", () => {
  divergence(() => reconstruct(raw("invalid-start-without-save-digest.jsonl"), { sampled: false }), "start_invalid");
  divergence(() => reconstruct(raw("invalid-start-without-save-digest.jsonl")), "schema_invalid");
});

test("reachability, cycles and reconcile due", () => {
  const first = JSON.parse(raw("compact-episode.jsonl")[0]);
  const island = structuredClone(first);
  for (const [ref, other] of [["native-90", "native-91"], ["native-91", "native-90"]])
    island.objects[`${ref}#enemy`] = { native_ref: ref, next: { $obj: `${other}#enemy` } };
  divergence(() => reconstruct([sealedFull(island)]), "orphan_object");
  const cycle = structuredClone(first);
  const relic = Object.keys(cycle.objects).find(key => key.endsWith("#relic"));
  const player = Object.keys(cycle.objects).find(key => key.endsWith("#player"));
  cycle.objects[relic].owner = { $obj: player };
  divergence(() => reconstruct([sealedFull(cycle)]), "placeholder_cycle");
  divergence(() => reconstruct(raw("compact-episode.jsonl"), { reconcileInterval: 2 }), "reconcile_overdue");
  assert.equal(reconstruct(raw("compact-episode.jsonl"), { reconcileInterval: 3 }).length, 4);
});

test("a collapsed rename is visible against the legacy writer", () => {
  assert.notDeepEqual(rename(reconstruct(raw("invalid-not-bijective.jsonl"))), rename(parsed("legacy-episode.jsonl")));
});

test("legacy full output is not a compact frame", () => {
  const legacy = parsed("legacy-episode.jsonl")[0];
  assert.notEqual(compactFrameSchemaError(legacy), null);
  divergence(() => new CompactStateReconstructor().accept(legacy), "not_compact_frame");
  for (const line of raw("compact-episode.jsonl")) assert.equal(compactFrameSchemaError(JSON.parse(line)), null);
});

test("sequence rules: gap, other instance, a delta before any full frame", () => {
  const lines = raw("compact-episode.jsonl");
  divergence(() => reconstruct([lines[1]]), "base_mismatch");
  const gap = JSON.parse(lines[1]); gap.seq += 1;
  divergence(() => reconstruct([lines[0], JSON.stringify(gap)], { sampled: false }), "seq_gap");
  const other = JSON.parse(lines[1]); other.episode_instance = "ep-9999:slot-3:run-9";
  divergence(() => reconstruct([lines[0], JSON.stringify(other)], { sampled: false }), "base_mismatch");
  const extra = JSON.parse(lines[1]); extra.object_digests["native-99#enemy"] = "0".repeat(64);
  divergence(() => reconstruct([lines[0], JSON.stringify(extra)], { sampled: false }), "digest_set_mismatch");
});

test("a failed reconstructor stays failed; a new full frame needs a new consumer", () => {
  const lines = raw("compact-episode.jsonl");
  const consumer = new CompactStateReconstructor();
  consumer.accept(JSON.parse(lines[0]), { line: lines[0] });
  const bad = JSON.parse(lines[1]); bad.objects_root_sha256 = "0".repeat(64);
  divergence(() => consumer.accept(bad), "root_mismatch");
  divergence(() => consumer.accept(JSON.parse(lines[0]), { line: lines[0] }), "root_mismatch");
  assert.equal(consumer.failed.reason, "root_mismatch");
});

test("sampled path needs the raw line", () => {
  const line = raw("compact-episode.jsonl")[0];
  divergence(() => new CompactStateReconstructor().accept(JSON.parse(line), { sampled: true }), "raw_line_unavailable");
});

test("reconstructed states never share objects with the carried table", () => {
  const lines = raw("compact-episode.jsonl");
  const consumer = new CompactStateReconstructor();
  const first = consumer.accept(JSON.parse(lines[0]));
  first.player.hp = -1;
  first.hand.length = 0;
  const later = lines.slice(1).map(line => consumer.accept(JSON.parse(line)));
  assert.deepEqual(later, reconstruct(lines).slice(1));
});

test("sample schedule is fixed before content and always checks the full frame", () => {
  const always = compactSampleSchedule({ all: true });
  assert.ok([0, 1, 2, 1000].every(always));
  const never = compactSampleSchedule({ rate: 0 });
  assert.ok(never(0) && !never(1) && !never(500));
  const fixed = compactSampleSchedule({ rate: 0.01, random: () => 0.25 });
  let hits = 0;
  for (let seq = 1; seq <= 100_000; seq += 1) hits += fixed(seq) ? 1 : 0;
  assert.ok(fixed(0));
  assert.ok(hits > 700 && hits < 1300, `about 1% expected, got ${hits}`);
  assert.throws(() => compactSampleSchedule({ rate: 2 }), TypeError);
});

test("shareUnchanged: frozen states; an unchanged object is the same object in the next state", () => {
  const lines = raw("compact-episode.jsonl");
  const consumer = new CompactStateReconstructor({ shareUnchanged: true });
  const states = lines.map(line => consumer.accept(JSON.parse(line), { line, sampled: true }));
  const frames = lines.map(line => JSON.parse(line));
  assert.ok(states.every(state => Object.isFrozen(state)));
  assert.throws(() => { states[0].player.hp = -1; }, TypeError);
  // A key neither upserted nor removed by frame 1, reached through an unchanged path, keeps its object.
  const relic = Object.keys(frames[0].objects).find(key => key.endsWith("#relic"));
  assert.ok(relic && !Object.hasOwn(frames[1].objects, relic) && !frames[1].removed.includes(relic));
  const find = (state, ref) => JSON.stringify(state).includes(ref) ? state.player.relics.find(item => item.native_ref === ref) : null;
  const ref = relic.split("#")[0];
  assert.ok(find(states[0], ref));
  assert.equal(find(states[1], ref), find(states[0], ref));
  // An upserted key gets a new object, and so does every object holding it.
  const upserted = Object.keys(frames[1].objects)[0];
  assert.ok(upserted);
  assert.notEqual(states[1], states[0]);
});

/** A minimal honest writer for hand-made episodes: digests over the emitted bytes, root over the held table. */
function writer(instance = "ep-x:slot-0:run-1") {
  const held = new Map();
  let seq = -1;
  const frame = (kind, skeleton, objects, extra = {}) => {
    const digests = Object.fromEntries(Object.entries(objects).map(([key, body]) => [key, sha(JSON.stringify(body))]));
    for (const key of extra.removed ?? []) held.delete(key);
    if (kind === "full" || kind === "reconcile") held.clear();
    for (const [key, digest] of Object.entries(digests)) held.set(key, digest);
    seq += 1;
    const value = { type: "compact_state", schema: "managed-native-compact-1", episode_instance: instance, seq: extra.seq ?? seq,
      skeleton: { type: "decision", decision: "combat_play", ...skeleton },
      frame: kind, base_seq: kind === "full" ? null : seq - 1, ...(kind === "full" ? { start: extra.start ?? { kind: "new_run" } } : { removed: extra.removed ?? [] }),
      ...(kind === "reconcile" ? { upserted: extra.upserted ?? [] } : {}), objects, object_digests: digests,
      objects_root_sha256: objectsRoot(held) };
    return JSON.stringify(value);
  };
  return { frame };
}
const card = (ref, title = "Strike") => ({ native_ref: ref, id: "STRIKE", title });

test("a pile container is never tabled, under any key (review M2)", () => {
  const w = writer();
  const pile = { native_ref: "native-7", pile: "draw", ordering_semantics: "unordered_multiset", cards: [{ id: "STRIKE", title: "Strike" }] };
  divergence(() => reconstruct([w.frame("full", { combat_piles: [{ $obj: "native-7#hand" }] }, { "native-7#hand": pile })]), "pile_tabled");
  // A pile carried inline in the skeleton is fine.
  const ok = writer();
  assert.deepEqual(reconstruct([ok.frame("full", { combat_piles: [{ pile: "draw", ordering_semantics: "unordered_multiset", cards: [] }] }, {})])[0],
    { type: "decision", decision: "combat_play", combat_piles: [{ pile: "draw", ordering_semantics: "unordered_multiset", cards: [] }] });
});

test("rule 5 is an iff: an unchanged body is never upserted again (review S1)", () => {
  const w = writer();
  const lines = [w.frame("full", { hand: [{ $obj: "native-1#hand" }] }, { "native-1#hand": card("native-1") }),
    w.frame("delta", { hand: [{ $obj: "native-1#hand" }] }, { "native-1#hand": card("native-1") })];
  for (const sampled of [true, false]) divergence(() => reconstruct(lines, { sampled }), "noop_upsert");
  const changed = writer();
  const states = reconstruct([changed.frame("full", { hand: [{ $obj: "native-1#hand" }] }, { "native-1#hand": card("native-1") }),
    changed.frame("delta", { hand: [{ $obj: "native-1#hand" }] }, { "native-1#hand": card("native-1", "Strike+") })]);
  assert.equal(states[1].hand[0].title, "Strike+");
});

test("an instance starts at seq 0 and states how it began (review S2)", () => {
  const w = writer();
  divergence(() => reconstruct([w.frame("full", {}, {}, { seq: 7 })]), "full_seq_nonzero");
  for (const start of [undefined, { kind: "resume" }, { kind: "new_run", seed: "x" }, { kind: "from_save", save_sha256: "nope" }]) {
    const frame = JSON.parse(writer().frame("full", {}, {}));
    if (start === undefined) delete frame.start; else frame.start = start;
    divergence(() => new CompactStateReconstructor().accept(frame, { sampled: false }), "start_invalid");
  }
  const consumer = new CompactStateReconstructor();
  consumer.accept(JSON.parse(writer().frame("full", {}, {}, { start: { kind: "from_save", save_sha256: "e".repeat(64) } })), { sampled: false });
  assert.deepEqual(consumer.start, { kind: "from_save", save_sha256: "e".repeat(64) });
});

test("writer bookkeeping drift is a divergence (review S7)", () => {
  const base = () => { const w = writer(); return [w, w.frame("full", { hand: [{ $obj: "native-1#hand" }] }, { "native-1#hand": card("native-1") })]; };
  {
    const [w, full] = base();
    divergence(() => reconstruct([full, w.frame("delta", { hand: [{ $obj: "native-1#hand" }] }, {}, { removed: ["native-9#hand"] })]), "removed_not_held");
  }
  {
    const [w, full] = base();
    divergence(() => reconstruct([full, w.frame("delta", { hand: [{ $obj: "native-1#hand" }] }, { "native-1#hand": card("native-1", "Zap") },
      { removed: ["native-1#hand"] })]), "removed_and_upserted");
  }
  {
    const [w, full] = base();
    divergence(() => reconstruct([full, w.frame("reconcile", { hand: [{ $obj: "native-1#hand" }] }, { "native-1#hand": card("native-1") },
      { upserted: ["native-1#hand"] })]), "reconcile_mismatch");
  }
  {
    const [w, full] = base();
    divergence(() => reconstruct([full, w.frame("reconcile", { hand: [{ $obj: "native-1#hand" }] }, { "native-1#hand": card("native-1") },
      { upserted: ["native-4#hand"] })]), "reconcile_mismatch");
  }
  {
    const [w, full] = base();
    divergence(() => reconstruct([full, w.frame("reconcile", { hand: [{ $obj: "native-1#hand" }] }, { "native-1#hand": card("native-1") },
      { removed: ["native-1#hand"] })]), "reconcile_mismatch");
  }
  const [w, full] = base();
  assert.equal(reconstruct([full, w.frame("reconcile", { hand: [{ $obj: "native-1#hand" }] }, { "native-1#hand": card("native-1") })]).length, 2);
});

test("shareUnchanged across the 64-frame reconcile: same bytes as off, unchanged objects kept, divergence unchanged", () => {
  const w = writer();
  const skeleton = { hand: [{ $obj: "native-1#hand" }, { $obj: "native-2#hand" }] };
  const lines = [w.frame("full", skeleton, { "native-1#hand": card("native-1"), "native-2#hand": card("native-2", "S0") })];
  for (let seq = 1; seq <= 140; seq += 1) {
    const moving = { "native-2#hand": card("native-2", `S${seq}`) };
    lines.push(seq % 64 === 0
      ? w.frame("reconcile", skeleton, { "native-1#hand": card("native-1"), ...moving }, { upserted: ["native-2#hand"] })
      : w.frame("delta", skeleton, moving));
  }
  const states = reconstruct(lines, { sampled: false }); // asserts on == off byte for byte
  assert.equal(states.at(-1).hand[1].title, "S140");
  const consumer = new CompactStateReconstructor({ shareUnchanged: true });
  const shared = lines.map(line => consumer.accept(JSON.parse(line)));
  // The unchanged card is one object within each reconcile window; a reconcile re-anchors it.
  for (const [from, to] of [[0, 63], [64, 127], [128, 140]]) {
    assert.ok(shared.slice(from, to + 1).every(state => state.hand[0] === shared[from].hand[0]));
    if (from > 0) assert.notEqual(shared[from].hand[0], shared[from - 1].hand[0]);
  }
  assert.ok(shared.slice(1).every((state, index) => state.hand[1] !== shared[index].hand[1]));
  // A late delta (no reconcile at 64) still diverges with sharing on.
  const late = writer();
  const overdue = [late.frame("full", skeleton, { "native-1#hand": card("native-1"), "native-2#hand": card("native-2", "S0") })];
  for (let seq = 1; seq <= 64; seq += 1) overdue.push(late.frame("delta", skeleton, { "native-2#hand": card("native-2", `S${seq}`) }));
  divergence(() => reconstruct(overdue, { sampled: false }), "reconcile_overdue");
});

test("a reconcile whose body changed under an unchanged digest: sharing on gives the same output as off (review S1 of #437)", () => {
  const w = writer();
  const skeleton = { hand: [{ $obj: "native-1#hand" }] };
  const lines = [w.frame("full", skeleton, { "native-1#hand": card("native-1") })];
  for (let seq = 1; seq < 64; seq += 1) lines.push(w.frame("delta", skeleton, {}));
  // The writer's own bookkeeping went wrong: a changed body under the old digest.
  const reconcile = JSON.parse(w.frame("reconcile", skeleton, { "native-1#hand": card("native-1") }));
  reconcile.objects["native-1#hand"].title = "Changed";
  lines.push(JSON.stringify(reconcile));
  const states = reconstruct(lines, { sampled: false }); // asserts on == off byte for byte
  assert.equal(states.at(-1).hand[0].title, "Changed");
  // A sampled reconcile re-hashes the emitted body and diverges, with sharing on or off.
  assert.throws(() => reconstruct(lines, { sampled: true }), error => error instanceof CompactStateDivergence);
});
