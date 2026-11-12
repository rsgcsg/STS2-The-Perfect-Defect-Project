import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { canonicalJson, createPublicIdentityPolicy, createSession, reduce, rewardGroupIntentions, rewardIntention, rewardWorldFacts,
  type P1Basis } from "../src/p1Global/index.js";
import { publicPath, type PublicPath } from "../src/p1Global/publicPath.js";
import { hasFinitePublicNumbers } from "../src/publicCombat.js";
import { createPublicIdentityPolicy as referencePolicy } from "./p1GlobalReferenceIdentity.js";

// Environment cost (R2): each faster path below must give exactly what the code it replaced gave.

function random(seed: number) {
  let state = seed >>> 0;
  return () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 2 ** 32; };
}
const pick = <T>(next: () => number, items: readonly T[]) => items[Math.floor(next() * items.length)]!;
const fixture = (name: string): any =>
  JSON.parse(readFileSync(new URL(`./fixtures/p1-global/${name}.json`, import.meta.url), "utf8"));

// The pre-change canonical form, kept verbatim as the byte reference.
function referenceSortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(referenceSortKeys);
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      const item = (value as Record<string, unknown>)[key];
      if (item !== undefined) out[key] = referenceSortKeys(item);
    }
    return out;
  }
  return value;
}
const referenceCanonicalJson = (value: unknown) => JSON.stringify(referenceSortKeys(value));

// The pre-change finite-number walk, kept verbatim.
function referenceFinite(value: unknown): boolean {
  const pending: unknown[] = [value];
  const seen = new Set<object>();
  while (pending.length > 0) {
    const item = pending.pop();
    if (typeof item === "number" && !Number.isFinite(item)) return false;
    if (typeof item === "object" && item !== null && !seen.has(item)) {
      seen.add(item); pending.push(...Object.values(item));
    }
  }
  return true;
}

describe("P1 reduce cost paths keep exact bytes and answers", () => {
  // The written fast path covers ordinary JSON data; these trees stay on it (no exotic types or keys).
  const KEYS = ["b", "a", "entity_id", "z_y", "é", "A", "", "a b", "quote\"", "back\\slash", "tab\t", " ", "😀",
    "\ud800", "card_previews", "Z", "_x", "x0"];
  const STRINGS = ["x", "", "e1", "line\nbreak", "\"q\"", "\\", "\u0000\u001f", "\ud800", "\udc00x", "😀", "é", " "];
  const NUMBERS = [0, -0, 1.5, -2, 1e21, 1e-7, 2 ** 53, NaN, Infinity, -Infinity, 123456789.125];
  function plainTree(next: () => number, depth: number): unknown {
    const roll = next();
    if (depth <= 0 || roll < 0.35) {
      const leaf = next();
      return leaf < 0.4 ? pick(next, STRINGS) : leaf < 0.75 ? pick(next, NUMBERS) : leaf < 0.85 ? next() < 0.5
        : leaf < 0.93 ? null : undefined;
    }
    if (roll < 0.6) {
      const array: unknown[] = []; const length = Math.floor(next() * 6);
      for (let index = 0; index < length; index++) if (next() < 0.9) array[index] = plainTree(next, depth - 1);
      array.length = length;
      return array;
    }
    const object: Record<string, unknown> = next() < 0.1 ? Object.create(null) : {};
    const keys = [...KEYS].sort(() => next() - 0.5).slice(0, Math.floor(next() * 8));
    if (next() < 0.4) keys.sort();
    for (const key of keys) object[key] = next() < 0.05 ? (() => 1) : plainTree(next, depth - 1);
    return object;
  }

  it("canonicalJson's written path matches the reference on random JSON data", () => {
    const next = random(20261112);
    for (let index = 0; index < 8000; index++) {
      const value = plainTree(next, 6);
      expect(canonicalJson(value)).toBe(referenceCanonicalJson(value));
    }
  });

  it("canonicalJson follows a patched Object.prototype.toJSON exactly as the copy does", () => {
    const value = { b: { d: 1 }, a: [1, { c: 2 }] };
    const before = canonicalJson(value);
    (Object.prototype as any).toJSON = function () { return "patched"; };
    try { expect(canonicalJson(value)).toBe(referenceCanonicalJson(value)); }
    finally { delete (Object.prototype as any).toJSON; }
    expect(canonicalJson(value)).toBe(before);
  });

  // subtreeAt may say true too often, never false above a path that has a relation.
  const KINDS = ["card_selection", "combat_turn", "map_navigation", "reward_claim", "card_reward_selection",
    "card_bundle_selection", "event_option", "shop_inventory", "deck_upgrade_selection", "crystal_sphere", "unknown_kind"];
  const card = { entity_id: "x", definition_id: "STRIKE", name: "Strike", cost: "1", type: "attack", rarity: "basic",
    is_upgraded: false, is_selected: false };
  const shop = { kind: "shop_catalog", content: { kind: "shop_catalog", cards: [], relics: [], potions: [] } };
  const SEGMENTS = ["scene", "content", "surface", "cards", "entity_id", "card_previews", "options", "tooltips", "card",
    "extra_reads", "relics", "relic", "slot_entity_id", "deck", "player", "catalog", "subject", "target", "commitments", "map",
    "nodes", "combat", "enemies", "playable_cards", "target_entity_ids", "display_owner_entity_id", "rewards", "card_options",
    "potions", "card_removal", "piles", "zones", "hand", "odd key", "a[1]", "orbs", "companions", "group", "reward_dispositions"];

  it("subtreeAt is false only where no path at or below the node has a relation", () => {
    const next = random(777);
    let pruned = 0;
    for (let round = 0; round < 1500; round++) {
      const kind = pick(next, KINDS); const revision = pick(next, ["r1.8", "r1.9"] as const);
      const reads = Array.from({ length: Math.floor(next() * 3) }, () => next() < 0.5 ? { kind: "surface_card", content: card } : shop);
      const scene = { surface: { options: [{ tooltips: [{ kind: "card", card }] }, { tooltips: [{ kind: "text" }, { kind: "card", card }] }] } };
      const reference = referencePolicy(kind, reads, scene, revision);
      const policy = createPublicIdentityPolicy(kind, reads, scene, revision);
      for (let walk = 0; walk < 40; walk++) {
        const nodes: PublicPath[] = [publicPath("")];
        const length = 1 + Math.floor(next() * 9);
        for (let index = 0; index < length; index++) {
          const last = nodes[nodes.length - 1]!;
          nodes.push(next() < 0.35 ? last.item(Math.floor(next() * 4)) : last.field(pick(next, SEGMENTS)));
        }
        const leaf = nodes[nodes.length - 1]!;
        if (reference.relation(leaf.path) === null) continue;
        for (const node of nodes) expect(policy.subtreeAt(node)).toBe(true);
      }
      for (const path of ["player.content.name", "scene.content.surface.options[0].title", "combat.content.enemies[0].intents"])
        if (!policy.subtreeAt(publicPath(path))) pruned++;
    }
    expect(pruned).toBeGreaterThan(0); // the pruning does happen
  }, 30000);

  const chain = (path: string): PublicPath[] => {
    const nodes = [publicPath("")];
    for (const [, key, index] of path.matchAll(/\.?([A-Za-z0-9_]+)|\[(\d+)\]/gu))
      nodes.push(index === undefined ? nodes[nodes.length - 1]!.field(key!) : nodes[nodes.length - 1]!.item(Number(index)));
    expect(nodes[nodes.length - 1]!.path).toBe(path);
    return nodes;
  };
  it("subtreeAt keeps every node above relations deep inside card, event and detail roots", () => {
    const scene = { surface: { options: [{ tooltips: [{ kind: "card", card }] }, { tooltips: [{ kind: "text" }, { kind: "card", card }] }] } };
    const reads = [{ kind: "surface_card", content: card }, shop];
    const paths = ["deck.content.cards[1].card_previews[0].entity_id", "piles.content.zones[0].cards[3].card_previews[2].entity_id",
      "player.content.relics[0].card_previews[0].card_previews[1].entity_id", "combat.content.player.hand[0].card_previews[0].entity_id",
      "scene.content.surface.options[1].tooltips[1].card.card_previews[0].entity_id", "extra_reads[0].content.card_previews[1].entity_id",
      "extra_reads[1].content.relics[0].relic.card_previews[0].entity_id", "extra_reads[1].content.cards[2].card.entity_id",
      "scene.content.surface.cards[0].card_previews[0].entity_id", "scene.content.surface.preview_options[0].after.card_previews[0].entity_id"];
    let checked = 0;
    for (const kind of KINDS) for (const revision of ["r1.8", "r1.9"] as const) {
      const reference = referencePolicy(kind, reads, scene, revision); const policy = createPublicIdentityPolicy(kind, reads, scene, revision);
      for (const path of paths) {
        if (reference.relation(path) === null) continue;
        checked++;
        for (const node of chain(path)) expect(policy.subtreeAt(node)).toBe(true);
      }
    }
    expect(checked).toBeGreaterThan(60);
  });

  it("subtreeAt answers stay right when one node alternates between policies", () => {
    const node = publicPath("scene.content.surface.playable_cards");
    const combat = createPublicIdentityPolicy("combat_turn"); const event = createPublicIdentityPolicy("event_option");
    for (let index = 0; index < 3; index++) {
      expect(combat.subtreeAt(node)).toBe(true);
      expect(event.subtreeAt(node)).toBe(false);
      expect(combat.relationAt(node.item(0).field("entity_id"))).toBe("declared");
      expect(event.relationAt(node.item(0).field("entity_id"))).toBe(null);
    }
  });

  it("hasFinitePublicNumbers answers as before on random graphs with shared objects and cycles", () => {
    const next = random(99);
    for (let round = 0; round < 3000; round++) {
      const pool: object[] = [];
      const build = (depth: number): unknown => {
        const roll = next();
        if (pool.length > 0 && roll < 0.1) return pick(next, pool);
        if (depth <= 0 || roll < 0.4) return next() < 0.03 ? pick(next, [NaN, Infinity, -Infinity]) : pick(next, [1, "s", null, true, undefined]);
        const value: any = next() < 0.5 ? [] : {};
        pool.push(value);
        const count = Math.floor(next() * 4);
        for (let index = 0; index < count; index++) value[Array.isArray(value) ? index : `k${index}`] = build(depth - 1);
        return value;
      };
      const graph = build(5);
      if (pool.length > 1 && next() < 0.3) (pool[0] as any).cycle = pick(next, pool);
      expect(hasFinitePublicNumbers(graph)).toBe(referenceFinite(graph));
    }
    for (const value of [NaN, 1, "x", null, undefined, [Infinity], { a: [{ b: -Infinity }] }])
      expect(hasFinitePublicNumbers(value)).toBe(referenceFinite(value));
  });

  it("reward intentions computed once per group equal rewardIntention for every option", () => {
    const basis = fixture("managed-reward_claim") as P1Basis;
    const surface = (basis.snapshot as any).interaction.content.surface;
    const group = { ...surface.rewards.find((item: any) => item.kind === "card"),
      card_alternatives: [{ option_id: "reroll", entity_id: "alt", index: 0, label: "Reroll", enabled: true, title: "Reroll" }] };
    // One current detail Read of an offered card, and one of another card (not part of the option).
    const detail = (target: string, value: number) => ({ kind: "surface_card", read_id: `d${value}`, response: { target_referent_id: target,
      content_schema: "s", visibility_basis: "v", ordering_semantics: "o", completeness: { hidden_by_policy: [] }, content: { value } } });
    (basis as any).reads = [...basis.reads, detail(group.card_options[1].entity_id, 2), detail("elsewhere", 3), detail(group.card_options[0].entity_id, 1)];
    // The pre-change rewardIntention, with its helpers inlined verbatim.
    const reference = (kind: string, option: string) => {
      const cardFacts = (c: any) => { const { index: _i, is_selected: _s, ...facts } = c; return facts; };
      const alternativeFacts = (o: any) => { const { entity_id: _e, index: _i, label: _l, enabled: _n, ...facts } = o; return facts; };
      const ids = new Set(group.card_options.map((c: any) => c.entity_id));
      const values = basis.reads.filter(read => read.kind === "surface_card").map(read => read.response as Record<string, any>)
        .filter(read => read && ids.has(read.target_referent_id)).map(read => ({ target: read.target_referent_id,
          schema: read.content_schema, visibility: read.visibility_basis, ordering: read.ordering_semantics,
          hidden: read.completeness.hidden_by_policy, content: read.content }));
      const details = canonicalJson(values.sort((a, b) => { const x = canonicalJson(a), y = canonicalJson(b); return x < y ? -1 : x > y ? 1 : 0; }));
      return { owner: "owner", group: String(group.group_id), kind, option, cards: canonicalJson(group.card_options.map(cardFacts)),
        alternatives: canonicalJson(group.card_alternatives.map(alternativeFacts)), completes: group.selection_completes_reward,
        world: rewardWorldFacts(basis), details, refresh_subscription: group.native_relic_refresh_subscription };
    };
    const intention = rewardGroupIntentions("owner", group, basis, rewardWorldFacts(basis));
    for (const [kind, option] of [["card", "c1"], ["decline", "skip"], ["alternative", "reroll"]] as const) {
      expect(JSON.stringify(intention(kind, option))).toBe(JSON.stringify(reference(kind, option)));
      expect(JSON.stringify(rewardIntention("owner", group, basis, kind, option))).toBe(JSON.stringify(reference(kind, option)));
    }
    expect(reference("card", "c1").world.length).toBeGreaterThan(100);
    expect(JSON.parse(reference("card", "c1").details)).toHaveLength(2);
  });

  // The alias table is copied on write: a reused previous table and a table this reducer did not build
  // (an own-key copy, as every reduction made before) must give the same reductions.
  const deck = (b: any) => b.reads.find((r: any) => r.kind === "run_deck").response.content;
  const piles = (b: any) => b.reads.find((r: any) => r.kind === "combat_piles").response.content;
  const hud = (b: any) => b.snapshot.interaction.content.context;
  function frames() {
    const witnessed = fixture("synthetic-complete-combat");
    const shown = deck(witnessed).cards.find((c: any) => c.definition_id === "DEFEND_DEFECT");
    const discard = piles(witnessed).zones.find((z: any) => z.zone === "discard");
    discard.cards = [structuredClone(shown)]; discard.card_count = hud(witnessed).player.discard_pile_count = 1;
    // The same card later has no ordered witness: equal deck copies make it anonymous again.
    return { shown, list: [witnessed, fixture("synthetic-complete-combat"), fixture("synthetic-complete-combat"), witnessed] };
  }

  it("the alias table: anonymous ids leave it, an unchanged table is reused frozen, and foreign tables reduce the same", () => {
    const { shown, list } = frames();
    let own = createSession("alias-table", "managed");
    let foreign = createSession("alias-table", "managed");
    const tables: object[] = [];
    for (const basis of list) {
      const left = reduce(own, basis);
      const right = reduce(structuredClone(foreign), basis);
      expect(JSON.stringify(left)).toBe(JSON.stringify(right));
      expect(Object.isFrozen(left.state.aliases)).toBe(true);
      tables.push(left.state.aliases);
      own = left.state; foreign = right.state;
    }
    expect(Object.hasOwn(tables[0]!, shown.entity_id)).toBe(true);
    expect(Object.hasOwn(tables[1]!, shown.entity_id)).toBe(false);
    expect(tables[2]).toBe(tables[1]); // nothing new and nothing left: the previous table itself
    expect(Object.hasOwn(tables[3]!, shown.entity_id)).toBe(true);
  });
});
