#!/usr/bin/env node
// P5 synthetic design probe. No game/model/network/data-store access.
// Results describe these reference fixtures, not production enforcement or policy quality.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
import { fileURLToPath } from "node:url";

const digest = (value) => createHash("sha256").update(JSON.stringify(value)).digest("hex");
const bytes = (value) => Buffer.byteLength(JSON.stringify(value));
const clone = (value) => structuredClone(value);
const checks = [];
let checkKind = "reference_transition";
function check(name, run) { run(); checks.push({ name, kind: checkKind, status: "pass" }); }

function referenceAgent() {
  const seen = new Map();
  let events = [], ordinal = 0, generation = "g1", uncertain = false;
  const fingerprint = () => digest({ events, ordinal, generation, uncertain });
  return {
    consume(event) {
      assert.deepEqual(Object.keys(event).sort(), ["generation", "id", "kind", "ordinal", "public"].sort());
      assert(!JSON.stringify(event.public).match(/future_label|private_rng|teacher_latent/));
      const hash = digest(event);
      if (seen.has(event.id)) {
        assert.equal(seen.get(event.id), hash, "same event ID changed content");
        return false;
      }
      assert.equal(event.generation, generation, "new generation needs explicit reset");
      assert.equal(event.ordinal, ordinal + 1, "history gap");
      seen.set(event.id, hash); ordinal = event.ordinal; events.push(clone(event));
      if (event.kind === "delivery_unknown") uncertain = true;
      return true;
    },
    choose(catalog) {
      assert(catalog.length && new Set(catalog).size === catalog.length, "complete distinct catalog required");
      if (uncertain) return "yield";
      // Scripted distinguishability witness, NOT learned D-M2.
      return events.some((e) => e.kind === "reward_seen") && catalog.includes("continue")
        ? "continue" : catalog[0];
    },
    exportState() { return { events: clone(events), ordinal, generation, uncertain }; },
    fingerprint,
  };
}
const event = (ordinal, kind = "observation", content = { page: "reward_list" }) =>
  ({ id: `e${ordinal}`, ordinal, generation: "g1", kind, public: content });

check("different public histories distinguish equal current pages", () => {
  const first = referenceAgent(), revisited = referenceAgent();
  first.consume(event(1)); revisited.consume(event(1, "reward_seen", { offers: ["a", "b"] }));
  revisited.consume(event(2));
  assert.equal(first.choose(["open", "continue"]), "open");
  assert.equal(revisited.choose(["open", "continue"]), "continue");
});
check("transport duplicate does not write state twice", () => {
  const a = referenceAgent(); a.consume(event(1)); const before = a.fingerprint();
  assert.equal(a.consume(event(1)), false); assert.equal(a.fingerprint(), before);
});
check("new occurrence with equal content is retained", () => {
  const a = referenceAgent(); a.consume(event(1)); const before = a.fingerprint();
  a.consume(event(2)); assert.notEqual(a.fingerprint(), before);
});
check("same ID changed content is rejected", () => {
  const a = referenceAgent(); a.consume(event(1)); assert.throws(() => a.consume(event(1, "reward_seen")));
});
check("gap is not invented history", () => assert.throws(() => referenceAgent().consume(event(2))));
check("new environment generation is not old continuation", () =>
  assert.throws(() => referenceAgent().consume({ ...event(1), generation: "g2" })));
for (const field of ["future_label", "private_rng", "teacher_latent"]) {
  check(`online reference boundary rejects ${field}`, () =>
    assert.throws(() => referenceAgent().consume(event(1, "observation", { [field]: 1 }))));
}
check("candidate what-if reads do not mutate persistent state", () => {
  const a = referenceAgent(); a.consume(event(1)); const before = a.fingerprint();
  a.choose(["a", "b"]); a.choose(["b", "a"]); assert.equal(a.fingerprint(), before);
});
check("empty catalog fails without a state write", () => {
  const a = referenceAgent(); const before = a.fingerprint();
  assert.throws(() => a.choose([])); assert.equal(a.fingerprint(), before);
});
check("state export is a copy, not an external mutable alias", () => {
  const a = referenceAgent(); a.consume(event(1)); const before = a.fingerprint();
  a.exportState().events.length = 0; assert.equal(a.fingerprint(), before);
});
check("separate Agent instances isolate history", () => {
  const a = referenceAgent(), b = referenceAgent(); const before = b.fingerprint();
  a.consume(event(1)); assert.equal(b.fingerprint(), before);
});
check("offline public-prefix replay reproduces reference state", () => {
  const sequence = [event(1), event(2, "reward_seen"), event(3)];
  const a = referenceAgent(), b = referenceAgent();
  sequence.forEach((e) => a.consume(e)); clone(sequence).forEach((e) => b.consume(e));
  assert.equal(a.fingerprint(), b.fingerprint());
});
check("unknown delivery yields without a second selection", () => {
  const a = referenceAgent(); a.consume(event(1, "delivery_unknown"));
  assert.equal(a.choose(["play", "end_turn"]), "yield");
});

checkKind = "binding_guard";
function validateRequest(request, context) {
  assert.equal(request.generation, context.generation);
  assert.equal(request.revision, context.revision);
  assert.equal(request.catalogDigest, digest(context.actions));
  assert(context.actions.includes(request.action));
}
const context = { generation: "g1", revision: 2, actions: ["play", "end_turn"] };
const request = { generation: "g1", revision: 2, action: "play", catalogDigest: digest(context.actions) };
check("current request binds complete catalog", () => validateRequest(request, context));
check("stale context after Read rejects old action", () =>
  assert.throws(() => validateRequest(request, { ...context, revision: 3 })));
check("different complete catalog rejects reused action binding", () =>
  assert.throws(() => validateRequest(request, { ...context, actions: ["play"] })));
check("old generation rejects request", () =>
  assert.throws(() => validateRequest(request, { ...context, generation: "g2" })));

checkKind = "design_counterexample";
check("native effective confirmation differs from raw preference arithmetic", () => {
  const nativeFacts = { rawMin: 2, selected: 1, canConfirm: true };
  assert.equal(nativeFacts.selected >= nativeFacts.rawMin, false);
  assert.equal(nativeFacts.canConfirm, true);
});
check("hand replace and grid max-cap are not one selection rule", () => {
  const selected = ["a"];
  const hand = ["b"], grid = clone(selected);
  assert.notDeepEqual(hand, grid);
});
check("preview back does not close parent choice", () => {
  const after = { stage: "selecting", selected: [], parentAwaiting: true };
  assert.equal(after.parentAwaiting, true); assert.equal(after.stage, "selecting");
});
check("empty result null target and Task cancellation stay distinct", () => {
  assert.equal(new Set(["selected_empty", "target_cancel_null", "task_canceled"]).size, 3);
});
check("card reward return and final skip differ", () => {
  const innerReturn = { pendingReward: true }, outerSkip = { pendingReward: false };
  assert.notDeepEqual(innerReturn, outerSkip);
});
check("reroll invalidates old generation of choices", () => {
  assert.throws(() => validateRequest(request, { ...context, revision: 3, actions: ["take_new"] }));
});
check("unknown win has no negative label", () => {
  const label = (terminal) => terminal === "win" ? 1 : terminal === "death" ? 0 : null;
  assert.equal(label("unknown"), null); assert.equal(label("win"), 1);
});
check("known current floor is not failure floor", () => {
  const observation = { currentFloor: 8, terminal: "unknown" };
  const failureFloor = observation.terminal === "death" ? observation.currentFloor : null;
  assert.equal(failureFloor, null);
});
check("unknown combat end masks HP target", () => {
  const sample = { hp: 20, combatEndKnown: false };
  assert.equal(sample.combatEndKnown ? sample.hp : null, null);
});
check("unexecuted branch has no observed outcome target", () => {
  const targets = new Map([["played", { win: 1 }]]); assert.equal(targets.has("not_played"), false);
});
check("active plays count current play but not child or automatic repeat", () => {
  const edges = ["manual_play", "child_select", "automatic_play", "end_turn", "manual_play"];
  assert.equal(edges.filter((x) => x === "manual_play").length, 2);
});
check("few plays cannot unconditionally outrank survival", () => {
  const death = { win: 0, plays: 1 }, win = { win: 1, plays: 9 };
  assert(death.plays < win.plays); assert(death.win < win.win);
});
check("copied terminal labels have one independent group", () => {
  const copied = Array.from({ length: 20 }, () => ({ group: "run1", win: 1 }));
  assert.equal(new Set(copied.map((x) => x.group)).size, 1);
});
check("provider completed is not accepted result", () => {
  const state = { provider: "completed", artifact: "candidate" };
  assert.notEqual(state.artifact, "accepted");
});
check("cancel requested is not provider stopped", () => assert.notEqual("cancel_requested", "stopped"));
check("submission unknown never creates a replacement attempt automatically", () => {
  const attempts = [{ id: "attempt1", state: "submission_unknown" }];
  const dispatchable = attempts.every((a) => a.state === "terminal_verified");
  assert.equal(dispatchable, false); assert.equal(attempts.length, 1);
});

const cards = (n, family) => Array.from({ length: n }, (_, i) => ({
  id: `${family}-${i}`, name: `synthetic-${family}-${i}`, cost: i % 4,
  description: `Synthetic public card ${i}; effect parameters ${i % 7}; no native content.`,
}));
const scenarios = [
  { name: "small_combat_pile", cardCount: 12, mapCount: 8, needs: ["piles"], auto: [] },
  { name: "large_combat_pile_map", cardCount: 80, mapCount: 45, needs: ["piles", "map"], auto: [] },
  { name: "selector_combat_context", cardCount: 30, mapCount: 20, needs: ["combat"], auto: ["combat"] },
  { name: "reward_deck_context", cardCount: 35, mapCount: 20, needs: ["deck"], auto: ["deck"] },
  { name: "shop_deck_map", cardCount: 50, mapCount: 40, needs: ["deck", "map"], auto: ["deck"] },
  { name: "no_optional_information", cardCount: 35, mapCount: 30, needs: [], auto: [] },
];

function projectScenario(spec, profile) {
  const views = {
    piles: { cards: cards(spec.cardCount, "pile"), ordering: "unordered_multiset" },
    deck: { cards: cards(spec.cardCount, "deck"), ordering: "unordered_multiset" },
    map: { nodes: Array.from({ length: spec.mapCount }, (_, i) => ({ id: `n${i}`, type: "public", next: [`n${i + 1}`] })) },
    combat: { hand: cards(5, "hand"), enemies: [{ hp: 31, intent: "public_attack" }], energy: 2 },
  };
  const catalog = Array.from({ length: 12 }, (_, i) => ({ id: `a${i}`, label: `synthetic-action-${i}` }));
  const basic = { hp: 40, maxHp: 60, phase: "decision", actions: catalog };
  const payloads = [], exposed = new Set();
  const emit = (names, kind = "observation") => {
    names.forEach((n) => exposed.add(n));
    payloads.push({ fixture: true, profile, kind, ...basic,
      views: Object.fromEntries(names.map((n) => [n, views[n]])) });
  };
  const automatic = profile === "C-S-bundle" ? Object.keys(views) : profile === "C-H" ? spec.auto : [];
  emit(automatic);
  let nativeNavigation = 0, readRequests = 0, modelCalls = 1;
  for (const name of spec.needs.filter((n) => !automatic.includes(n))) {
    if (profile === "C-L") {
      nativeNavigation += 2; modelCalls += 2;
      emit([name], "native_information_page"); emit(automatic, "native_return");
    } else {
      readRequests++; modelCalls++; emit([name], "read_result");
    }
  }
  assert(spec.needs.every((n) => exposed.has(n)));
  return { profile, scene: spec.name, gameplayRequests: 1, nativeNavigation,
    readRequests, policyCallsAssumed: modelCalls, frames: payloads.length,
    totalJsonBytes: payloads.reduce((n, p) => n + bytes(p), 0),
    peakFrameJsonBytes: Math.max(...payloads.map(bytes)),
    requestedViews: spec.needs, exposedViews: [...exposed].sort() };
}
const profiles = ["C-L", "C-S-query", "C-S-bundle", "C-H"];
const costs = scenarios.flatMap((s) => profiles.map((p) => projectScenario(s, p)));
checkKind = "cost_fixture";
check("cost fixtures preserve the same required view set and gameplay count", () => {
  assert.equal(costs.length, 24); assert(costs.every((c) => c.gameplayRequests === 1));
});
check("automatic context removes query only when that view is actually provided", () => {
  const rows = costs.filter((c) => c.scene === "selector_combat_context");
  assert.equal(rows.find((c) => c.profile === "C-H").readRequests, 0);
  assert.equal(rows.find((c) => c.profile === "C-S-query").readRequests, 1);
});
check("unneeded bundle facts increase fixture bytes", () => {
  const rows = costs.filter((c) => c.scene === "no_optional_information");
  assert(rows.find((c) => c.profile === "C-S-bundle").totalJsonBytes >
    rows.find((c) => c.profile === "C-S-query").totalJsonBytes);
});
const timings = [];
for (let repeat = 0; repeat < 5; repeat++) {
  const start = performance.now();
  for (let i = 0; i < 100; i++) scenarios.forEach((s) => profiles.forEach((p) => projectScenario(s, p)));
  timings.push(performance.now() - start);
}
timings.sort((a, b) => a - b);
const report = {
  schema: "p5-synthetic-design-probe-1", fixtureVersion: 1,
  sourceSha256: createHash("sha256").update(readFileSync(fileURLToPath(import.meta.url))).digest("hex"),
  runtime: { node: process.version, platform: process.platform, arch: process.arch },
  checks, checksPassed: checks.length,
  checkKinds: Object.fromEntries([...new Set(checks.map((c) => c.kind))]
    .map((kind) => [kind, checks.filter((c) => c.kind === kind).length])),
  costs,
  projectionTiming: { repeats: 5, projectionsPerRepeat: 2400,
    medianMs: timings[2], minMs: timings[0], maxMs: timings[4] },
  scope: "synthetic reference transitions and JSON projection only",
  nonClaims: ["native execution", "production enforcement", "Human evidence", "neural D-M2 inference",
    "BPE token count", "training efficacy", "network latency", "provider cost", "universal protocol superiority"],
};
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
