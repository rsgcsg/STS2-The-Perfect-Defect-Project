import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { createSession, reduce, type P1Observation } from "@rsgcsg/sts2-connector-client/p1-global";
import { compareDecisions, comparePair } from "../src/p1-global/compare.js";
const basis = () => JSON.parse(readFileSync(new URL("../../connector/sdk/typescript/test/fixtures/p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));
const observation = (): P1Observation => {
  const r = reduce(createSession("compare-test", "managed"), basis());
  expect(r.complete).toBe(true); return r.observation;
};
const compare = (left: P1Observation, right: P1Observation, a: string | null = null, b = a) =>
  compareDecisions([{ observation: left, chosen: a, origin: "agent_choice" }], [{ observation: right, chosen: b, origin: "agent_choice" }]);
const content = (part: { readonly content: unknown }): any => part.content;
function unambiguousObservation(): P1Observation {
  const a = observation();
  for (const part of [content(a.deck).cards, ...content(a.piles).zones.map((z: any) => z.cards)])
    part.forEach((card: any, i: number) => { card.definition_id += `_SYNTHETIC_${i}`; });
  return a;
}
// Fixture transformations are graph/conformance probes, not native game states.
function renamed(o: P1Observation): P1Observation {
  const names = new Map<string, string>();
  const collect = (value: any, key = "") => {
    if (typeof value === "string" && /^(?:entity_id|group_id|player_entity_id|combat_id|choice_id|subject|target|target_entity_ids|selected_card_entity_ids)$/u.test(key)) names.set(value, `renamed_${value}`);
    else if (Array.isArray(value)) value.forEach(child => collect(child, key));
    else if (value && typeof value === "object") Object.entries(value).forEach(([k, v]) => collect(v, k));
  };
  collect(o);
  const replace = (value: any): any => typeof value === "string" ? names.get(value) ?? value
    : Array.isArray(value) ? value.map(replace) : value && typeof value === "object"
      ? Object.fromEntries(Object.entries(value).map(([k, v]) => [k, replace(v)])) : value;
  return replace(o);
}

describe("P1 r1.1 full-public graph comparison", () => {
  it("accepts identical full observations with repeated instances", () => {
    const a = observation(); expect(compare(a, structuredClone(a)).verdict).toBe("equal");
  });
  it("permits a bijection on a fully disambiguated public graph without deleting facts", () => {
    const a = unambiguousObservation();
    const b = renamed(a); expect(compare(a, b).verdict).toBe("equal");
  });
  it("uses the reducer's owning paths for recursive card previews and leaves extensions literal", () => {
    const a = unambiguousObservation(); const card = content(a.combat).player.hand[0];
    content(a.player).relics[0].card_previews = [{ ...structuredClone(card), entity_id: "public-preview-parent",
      card_previews: [{ ...structuredClone(card), entity_id: "public-preview-child" }] }];
    const b = renamed(a);
    expect(compare(a, b).verdict).toBe("equal");
    content(a.player).relics[0].card_previews[0].extension = { entity_id: "literal-left" };
    content(b.player).relics[0].card_previews[0].extension = { entity_id: "literal-right" };
    expect(compare(a, b).verdict).toBe("different");
  });
  it.each(["enemy_intent", "enemy_status", "hand_cost", "draw_definition", "map_edges", "selector_prefs", "unknown_extension"])("detects changed public %s", variant => {
    const a = observation(); const b = structuredClone(a);
    if (variant === "enemy_intent") content(b.combat).enemies[0].intents = [{ type: "Defend", title: "Defend", description: "Gain 999 Block." }];
    if (variant === "enemy_status") content(b.combat).enemies[0].statuses = [{ definition_id: "STRENGTH", name: "Strength", amount: 99, type: "Buff", description: "99 Strength" }];
    if (variant === "hand_cost") content(b.combat).player.hand[0].cost = "999";
    if (variant === "draw_definition") content(b.piles).zones[0].cards[0].definition_id = "BURN";
    if (variant === "map_edges") content(b.map).nodes[0].children = [];
    if (variant === "selector_prefs") (b.scene.content as any).surface.min_select = 2;
    if (variant === "unknown_extension") (b.scene.content as any).surface.future = { entity_id: "not-an-alias", description: "public" };
    const report = compare(a, b); expect(report.verdict).not.toBe("equal"); expect(report.matching).toBe(0);
    expect(report.first_divergence).not.toBeNull();
  });
  it("treats only combat card/potion legal target domains as complete unordered sets under one bijection", () => {
    const a = unambiguousObservation(), surface = (a.scene.content as any).surface;
    const enemy = content(a.combat).enemies[0], twin = { ...structuredClone(enemy), entity_id: "target-twin", combat_id: 2, hp: 1 };
    content(a.combat).enemies.push(twin);
    const targets = [enemy.entity_id, twin.entity_id];
    surface.playable_cards[1].target_entity_ids = [...targets];
    surface.usable_potions = [{ entity_id: surface.playable_cards[0].entity_id, target_entity_ids: [...targets] }];
    const b = renamed(a), other = (b.scene.content as any).surface;
    other.playable_cards[1].target_entity_ids.reverse(); other.usable_potions[0].target_entity_ids.reverse();
    expect(compare(a, b).verdict).toBe("equal");
    for (const change of ["missing", "extra", "different", "duplicate"]) {
      const bad = structuredClone(b), domain = (bad.scene.content as any).surface.playable_cards[1].target_entity_ids;
      if (change === "missing") domain.pop();
      if (change === "extra") domain.push(content(b.player).entity_id);
      if (change === "different") domain[0] = content(b.player).entity_id;
      if (change === "duplicate") domain[0] = domain[1];
      expect(compare(a, bad).verdict, change).not.toBe("equal");
    }
    const duplicate = structuredClone(a);
    (duplicate.scene.content as any).surface.usable_potions[0].target_entity_ids = [targets[0], targets[0]];
    expect(compare(duplicate, duplicate).verdict).toBe("incomparable");
    const reorderedEnemies = structuredClone(b); content(reorderedEnemies.combat).enemies.reverse();
    expect(compare(a, reorderedEnemies).verdict).toBe("different");
    const reorderedHand = structuredClone(b); content(reorderedHand.combat).player.hand.reverse();
    expect(compare(a, reorderedHand).verdict).toBe("different");
    for (const field of ["target_entity_ids", "cards"]) {
      const left = structuredClone(a), right = structuredClone(a);
      (left.scene.content as any).surface.extension = { [field]: ["literal-a", "literal-b"] };
      (right.scene.content as any).surface.extension = { [field]: ["literal-b", "literal-a"] };
      expect(compare(left, right).verdict).toBe("different");
    }
    const otherKindLeft = structuredClone(a), otherKindRight = structuredClone(a);
    for (const o of [otherKindLeft, otherKindRight]) {
      (o.scene as any).native_kind = "event_option";
      (o.scene as any).content_schema = "sts2.player-environment/surface/event_option-1";
      (o.scene.content as any).surface.kind = "event_option";
    }
    (otherKindRight.scene.content as any).surface.playable_cards[1].target_entity_ids.reverse();
    expect(compare(otherKindLeft, otherKindRight).verdict).toBe("different");
    const oldRevision = structuredClone(a); (oldRevision as any).revision = "r1.2";
    expect(compare(a, oldRevision).verdict).toBe("incomparable");
  });
  it("keeps chosen target instance relations for two enemies of one definition", () => {
    const a = observation(); const enemy = content(a.combat).enemies[0];
    const twin = { ...structuredClone(enemy), entity_id: "m999", combat_id: 2, hp: 1 };
    content(a.combat).enemies.push(twin);
    const attack = a.catalog.find(c => c.kind === "combat.play_card" && c.target)!;
    (a.catalog as any[]).push({ ...attack, choice_id: "choice_twin", target: twin.entity_id });
    expect(compare(a, a, attack.choice_id, "choice_twin").verdict).toBe("different");
  });
  it("preserves distinct reward commitment edges instead of grouping by card kind", () => {
    const a = observation(); (a.scene.content as any).surface.rewards = [
      { entity_id: "group-a", group_id: "group-a", kind: "card", card_options: [{ entity_id: "card-a", definition_id: "A" }] },
      { entity_id: "group-b", group_id: "group-b", kind: "card", card_options: [{ entity_id: "card-b", definition_id: "B" }] }];
    (a.commitments as any).declined_reward_groups = ["group-a"];
    const b = structuredClone(a); (b.commitments as any).declined_reward_groups = ["group-b"];
    expect(compare(a, b).verdict).not.toBe("equal");
  });
  it("accepts symmetric duplicate instances only through a bijection of the complete public graph", () => {
    const a = observation(); const b = renamed(a);
    expect(compare(a, b).verdict).toBe("equal");
    const deck = content(b.deck).cards;
    deck[1].entity_id = deck[0].entity_id;
    expect(compare(a, b).verdict).not.toBe("equal");
  });
  it("preserves full detail inventory multiplicity and its same-instance relations across transport reordering", () => {
    const a = observation();
    (a as any).extra_reads = content(a.deck).cards.map((card: any) => ({ kind: "surface_card", availability: "available",
      content: { ...structuredClone(card), public_details_revision: "1", display_context: "deck",
        display_owner_entity_id: null, keywords: [], card_previews: [] } }));
    const b = renamed(a); (b.extra_reads as any[]).reverse();
    expect(compare(a, b).verdict).toBe("equal");
    const changed = structuredClone(b); (changed.extra_reads[0]!.content as any).description += " real difference";
    expect(compare(a, changed).verdict).not.toBe("equal");
    const lost = structuredClone(b); (lost.extra_reads as any[]).pop();
    expect(compare(a, lost).verdict).not.toBe("equal");
  });
  it("never normalizes future literal entity_id fields or localized display fields globally", () => {
    const a = unambiguousObservation(); const b = structuredClone(a);
    (a.scene.content as any).surface.extension = { entity_id: "opaque-a" };
    (b.scene.content as any).surface.extension = { entity_id: "opaque-b" };
    expect(compare(a, b).verdict).toBe("different");
    const c = structuredClone(a); (c.scene as any).prompt = "different public text";
    expect(comparePair([a], [c], "semantic").verdict).toBe("different");
  });
  it("does not assign schema-owned card relations to a combat extension named cards", () => {
    const a = unambiguousObservation(); const b = structuredClone(a);
    (a.scene.content as any).surface.cards = [{ entity_id: "extension-left" }];
    (b.scene.content as any).surface.cards = [{ entity_id: "extension-right" }];
    expect(compare(a, b).verdict).toBe("different");
  });
  it("refuses incomplete capture, unsupported revisions, invalid parts and unqualified forced input", () => {
    const a = observation();
    for (const mutate of [(b: any) => { b.envelope.required_information_complete = false; },
      (b: any) => { b.revision = "r1.0"; }, (b: any) => { b.map.content = { kind: "map" }; },
      (b: any) => { b.piles.availability = "unsupported"; }]) {
      const b = structuredClone(a); mutate(b); expect(compare(a, b).verdict).toBe("incomparable");
    }
    const report = compareDecisions([{ observation: a, chosen: a.catalog[0]!.choice_id, origin: "forced" }],
      [{ observation: a, chosen: a.catalog[0]!.choice_id, origin: "forced" }]);
    expect(report.verdict).toBe("incomparable"); expect(report.matching).toBe(0);
  });
  it("reports a longer trace as different and stops at the first divergent point", () => {
    const a = observation(); const b = structuredClone(a); content(b.player).gold++;
    expect(comparePair([a], [a, a], "exact").verdict).toBe("different");
    const p = comparePair([a, a], [b, a], "semantic"); expect(p.compared).toBe(1); expect(p.matching).toBe(0);
  });
  it("bounds deeply nested public extensions before recursive comparison", () => {
    const a = observation(); let v: any = {}; (a.scene.content as any).surface.future = v;
    for (let i = 0; i < 100; i++) { v.next = {}; v = v.next; }
    expect(compare(a, a).verdict).toBe("incomparable");
  });
  it("diagnostic chooser retains every duplicate instance and declares no cross-Host replay", () => {
    const a = observation(); const original = a.catalog[0]!;
    (a as any).catalog = [{ ...original, choice_id: "one" }, { ...original, choice_id: "two" }];
    const messages = [{ type: "hello", protocol: "sts2.p1/agent-port-1", episode_id: "test-seed" },
      ...Array.from({ length: 30 }, () => ({ type: "observation", observation: a, requires_choice: true })), { type: "episode_end" }];
    const result = spawnSync(process.execPath, [fileURLToPath(new URL("../examples/p1/signature_agent.mjs", import.meta.url))],
      { input: messages.map(m => JSON.stringify(m)).join("\n") + "\n", encoding: "utf8" });
    expect(result.status, result.stderr).toBe(0); const replies = result.stdout.trim().split("\n").map(line => JSON.parse(line));
    expect(replies[0].agent_id).toBe("external-instance-choice-diagnostic");
    expect(new Set(replies.filter(m => m.type === "choose").map(m => m.choice_id))).toEqual(new Set(["one", "two"]));
  });
});
