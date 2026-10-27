import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { decodePublicBasis, SETTLED_RELIC_COUNTERS_SCHEMA, SETTLED_RELIC_GAME_MVID,
  reprojectSettledRelicCounters, PlayerEnvironmentRestClient } from "../src/index.js";
import { createSession, reduce, modelView, createPublicOrderingPolicy, publicOrderingIssue } from "../src/p1Global/index.js";

function fixture(): any {
  const b = JSON.parse(readFileSync(new URL("./fixtures/p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));
  b.schema = "sts2.player-environment/public-basis-1"; b.revision = "1";
  b.capture_scope = "current_public_snapshot_and_advertised_read_instances"; b.capture_status = "complete"; b.missing = [];
  const relic = (id: string, definition: string, counter: number) => ({ entity_id: id, definition_id: definition,
    name: definition, description: "Public relic", keywords: [], card_previews: [], counter });
  b.snapshot.persistent.content.player.relics.push(relic("relic-k", "KUSARIGAMA", 3), relic("relic-c", "LASTING_CANDY", 2));
  return b;
}
function witnessed(b = fixture()): any {
  b.revision = "3";
  b.settled_relic_counters = { schema: SETTLED_RELIC_COUNTERS_SCHEMA, source_snapshot_id: b.snapshot.snapshot_id,
    session: { ...b.snapshot.session }, game_mvid: SETTLED_RELIC_GAME_MVID, source_kind: "native_same_capture",
    facts: [{ entity_id: "relic-k", definition_id: "KUSARIGAMA", display_counter: 3, logical_counter: 1, rule_id: "kusarigama-attacks-mod3-1" },
      { entity_id: "relic-c", definition_id: "LASTING_CANDY", display_counter: 2, logical_counter: 0, rule_id: "lasting-candy-rewards-mod2-1" }], missing: [] };
  return b;
}
const relics = (b: any) => b.snapshot.persistent.content.player.relics;
const counters = (b: any) => relics(b).filter((r: any) => ["KUSARIGAMA", "LASTING_CANDY"].includes(r.definition_id)).map((r: any) => r.counter);

describe("PB3 same-capture settled counter witnesses", () => {
  it("accepts both original snapshot profiles, while PB1/2 refuse the additive witness", () => {
    const b = witnessed(); expect(decodePublicBasis(b)).toEqual(b);
    b.snapshot.input_profile = "public-basis-1"; expect(decodePublicBasis(b)).toEqual(b);
    for (const revision of ["1", "2"]) { b.revision = revision; expect(() => decodePublicBasis(b)).toThrow(); }
    b.revision = "3"; delete b.settled_relic_counters; expect(() => decodePublicBasis(b)).toThrow();
  });
  it.each(["snapshot", "runtime", "fingerprint", "build", "source", "duplicate", "foreign", "definition", "rule", "display", "range", "fraction", "nan", "missing_fact", "carrier_conflict"])(
    "refuses %s provenance/counter mismatch", variant => {
      const b = witnessed(), w = b.settled_relic_counters, f = w.facts[0];
      if (variant === "snapshot") w.source_snapshot_id = "foreign";
      if (variant === "runtime") w.session.runtime_instance_id = "foreign";
      if (variant === "fingerprint") w.session.environment_fingerprint = "foreign";
      if (variant === "build") w.game_mvid = "other";
      if (variant === "source") w.source_kind = "next_frame";
      if (variant === "duplicate") w.facts.push({ ...f });
      if (variant === "foreign") f.entity_id = "foreign";
      if (variant === "definition") f.definition_id = "LASTING_CANDY";
      if (variant === "rule") f.rule_id = "lasting-candy-rewards-mod2-1";
      if (variant === "display") f.display_counter = 2;
      if (variant === "range") f.logical_counter = 3;
      if (variant === "fraction") f.logical_counter = 0.5;
      if (variant === "nan") f.logical_counter = NaN;
      if (variant === "missing_fact") w.facts.pop();
      if (variant === "carrier_conflict") b.snapshot.referents.push({ role: "relic", properties: { ...relics(b).at(-2), counter: 2 } });
      expect(() => decodePublicBasis(b)).toThrow();
    });
  it("requires coherent nonactivation getters, and never accepts a fact for an unshown counter", () => {
    const b = witnessed(); relics(b).at(-2).counter = 1; b.settled_relic_counters.facts[0].display_counter = 1;
    b.settled_relic_counters.facts[0].logical_counter = 0; expect(() => decodePublicBasis(b)).toThrow();
    delete relics(b).at(-2).counter; expect(() => decodePublicBasis(b)).toThrow();
  });
  it("preserves explicit partial witness causes and refuses a false complete capsule", () => {
    const b = witnessed(); b.settled_relic_counters.facts.pop(); b.settled_relic_counters.missing = ["relic-c:native_unavailable"];
    expect(() => decodePublicBasis(b)).toThrow();
    b.capture_status = "partial"; b.missing = ["settled_relic_counters:relic-c:native_unavailable"];
    expect(decodePublicBasis(b)).toEqual(b);
    const reduced = reduce(createSession("partial", "reference", "r1.7"), b);
    expect(reduced.complete).toBe(false); expect(reduced.missing).toContain(b.missing[0]);
  });
  it("requests PB3 only explicitly on the actual HTTP capture entrance", async () => {
    const urls: string[] = [];
    const b = fixture(), client = new PlayerEnvironmentRestClient("http://unused", 1000, async url => {
      urls.push(String(url)); return new Response(JSON.stringify({ schema: "sts2.player-environment/public-basis-handle-1",
        read_profile: "public-basis-sealed-1", input_profile: "public-basis-1", capture_id: "capture", source_snapshot_id: b.snapshot.snapshot_id,
        session: b.snapshot.session, generation_id: "generation", game_continuity_id: "run", captured_at: "2026-10-27T00:00:00Z",
        expires_at: "2026-10-27T00:01:00Z", total_bytes: 100, sha256: "a".repeat(64), first_cursor: "cursor", capture_ordinal: 1 }));
    });
    await client.readCurrentPublicBasis(); await client.readCurrentPublicBasis({ settledRelicCounters: false });
    await client.readCurrentPublicBasis({ settledRelicCounters: true });
    expect(urls[0]).not.toContain("settled_relic_counters"); expect(urls[1]).toBe(urls[0]);
    expect(urls[2]).toContain("settled_relic_counters=1");
  });
});

describe("explicit P1 r1.7 projection and recovery grades", () => {
  it("normalizes every existing carrier on a copy and keeps native provenance out of model input", () => {
    const b = witnessed(), k = relics(b).at(-2), c = relics(b).at(-1);
    b.snapshot.interaction.content.surface.relics = [{ relic: { ...k } }, { ...c }, { ...k, entity_id: "absent", counter: null }];
    b.snapshot.referents.push({ role: "relic", properties: { ...k } });
    const shop = { response: { kind: "shop_catalog", content: { relics: [{ relic: { ...c } }] } } };
    b.reads.push(shop);
    const original = JSON.stringify(b), projected = reprojectSettledRelicCounters(b);
    expect(JSON.stringify(b)).toBe(original); expect(counters(projected.basis)).toEqual([1, 0]);
    expect(projected.basis.snapshot.interaction.content.surface.relics.map((r: any) => r.relic?.counter ?? r.counter)).toEqual([1, 0, null]);
    expect(projected.basis.snapshot.referents.at(-1).properties.counter).toBe(1);
    expect(projected.basis.reads.at(-1).response.content.relics[0].relic.counter).toBe(0);
    b.reads.pop(); delete b.snapshot.interaction.content.surface.relics; b.snapshot.referents.pop();
    const reduced = reduce(createSession("witnessed", "reference", "r1.7"), b);
    expect(reduced.complete).toBe(true); expect(reduced.settled_relic_counter_projection?.facts[0]?.recovery).toBe("native_same_capture");
    expect(JSON.stringify(modelView(reduced.observation))).not.toMatch(/game_mvid|source_kind|rule_id|settled_relic_counter/);
    expect((reduced.observation.player.content as any).relics.slice(-2).map((r: any) => r.counter)).toEqual([1, 0]);
  });
  it("retains old defaults and raw displays; r1.7 inherits r1.6 membership semantics", () => {
    const b = witnessed();
    for (const revision of [undefined, "r1.6"] as const) {
      const r = reduce(createSession("frozen", "managed", revision), b);
      expect((r.observation.player.content as any).relics.slice(-2).map((r: any) => r.counter)).toEqual([3, 2]);
      expect(r.settled_relic_counter_projection).toBeUndefined();
    }
    const o = reduce(createSession("new", "managed", "r1.7"), b).observation;
    (o.scene as any).native_kind = "native_generated_card_choice";
    expect(createPublicOrderingPolicy(o).unordered("$.observation.scene.content.surface.selectable_card_entity_ids", null)).toBe(true);
    (o.scene.content as any).surface.selectable_card_entity_ids = ["a", "a"];
    expect(publicOrderingIssue(o)).toBe("invalid_public_membership_set");
  });
  it("recovers only unambiguous same-snapshot old public counters without a witness", () => {
    for (const k of [0, 1, 2]) for (const c of [0, 1]) {
      const b = fixture(); relics(b).at(-2).counter = k; relics(b).at(-1).counter = c;
      const r = reduce(createSession("legacy", "managed", "r1.7"), b);
      expect(r.complete).toBe(true); expect(r.settled_relic_counter_projection?.facts.every(f => f.recovery === "legacy_same_snapshot_public_rule")).toBe(true);
    }
    const b = fixture(), r = reduce(createSession("ambiguous", "reference", "r1.7"), b);
    expect(r.complete).toBe(false); expect(r.missing.filter(m => m.includes("ambiguous_missing_witness"))).toHaveLength(2);
    expect((r.observation.player.content as any).relics.slice(-2).map((r: any) => r.counter)).toEqual([3, 2]);
    expect(counters(b)).toEqual([3, 2]);
  });
});

it("r1.7 reward continuations compare the same normalized world without relaxing old raw comparison", async () => {
  const { rewardForwardPair } = await import("./fixtures/reward-forward.mjs");
  const { resolveFollowBasis, compareOriginalPresentationBases } = await import("../src/p1Global/index.js");
  const { outer, child } = rewardForwardPair();
  for (const [basis, display] of [[outer, 2], [child, 0]] as const) {
    basis.snapshot.persistent.content.player.relics.push({ entity_id: "same-candy", definition_id: "LASTING_CANDY",
      name: "Candy", description: "Every two rewards", keywords: [], card_previews: [], counter: display });
    (basis as any).settled_relic_counters = { schema: SETTLED_RELIC_COUNTERS_SCHEMA,
      source_snapshot_id: basis.snapshot.snapshot_id, session: basis.snapshot.session,
      game_mvid: SETTLED_RELIC_GAME_MVID, source_kind: "native_same_capture",
      facts: [{ entity_id: "same-candy", definition_id: "LASTING_CANDY", display_counter: display,
        logical_counter: 0, rule_id: "lasting-candy-rewards-mod2-1" }], missing: [] };
  }
  const r = reduce(createSession("reward-counter", "reference", "r1.7"), outer);
  expect(r.complete).toBe(true);
  const take = r.observation.catalog.find(choice => choice.kind === "reward.take_card")!;
  const lowering = r.lowering[take.choice_id]!;
  expect(resolveFollowBasis(lowering.follow[0]!, lowering, child)?.bound_action_id).toBe("select-0");
  // This is still a strict comparison of original pages, with many real page
  // changes; the native witness's capture locator is not a game-world field.
  const comparison = compareOriginalPresentationBases(outer, child);
  expect(comparison.equal).toBe(false);
  expect(comparison.changes.some(change => change.path.includes("settled_relic_counters"))).toBe(false);
  expect(comparison.changes.some(change => change.path.endsWith("counter") && change.before === 2 && change.after === 0)).toBe(true);
});
