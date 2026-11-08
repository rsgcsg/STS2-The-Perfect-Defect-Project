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

function reconstruct(lines, { sampled = true, reconcileInterval = 64 } = {}) {
  const consumer = new CompactStateReconstructor({ reconcileInterval });
  return lines.map(line => consumer.accept(JSON.parse(line), { line, sampled }));
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
  ["invalid-orphan.jsonl", "orphan_object"]
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
  ["invalid-discard-view.jsonl", "pile_placeholder", "schema_invalid"]
]) {
  test(`pile cards are never tabled: ${name}`, () => {
    divergence(() => reconstruct(raw(name), { sampled: false }), hot);
    divergence(() => reconstruct(raw(name)), sampled);
  });
}

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
