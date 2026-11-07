import { describe, expect, it } from "vitest";
import { canonicalJson, createPublicIdentityPolicy, publicFieldPath } from "../src/p1Global/index.js";
import { publicPath } from "../src/p1Global/publicPath.js";
import { createPublicIdentityPolicy as referencePolicy } from "./p1GlobalReferenceIdentity.js";

// The pre-optimization canonical form, kept verbatim as the byte reference.
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

function random(seed: number) {
  let state = seed >>> 0;
  return () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 2 ** 32; };
}
const KEYS = ["b", "a", "10", "9", "0", "z_y", "é", "A", "", "toJSON", "__proto__", "entity_id", "card_previews"];
const LEAVES: unknown[] = ["x", "", "e1", 0, -0, 1.5, NaN, Infinity, true, false, null, undefined, () => 1];
function tree(next: () => number, depth: number): unknown {
  const roll = next();
  if (depth <= 0 || roll < 0.3) return LEAVES[Math.floor(next() * LEAVES.length)];
  if (roll < 0.55) {
    const array: unknown[] = []; const length = Math.floor(next() * 5);
    for (let index = 0; index < length; index++) if (next() < 0.9) array[index] = tree(next, depth - 1);
    array.length = length; // keeps any holes
    return array;
  }
  if (roll < 0.6) return new Date(0);
  if (roll < 0.63) return new Map([["k", 1]]);
  const object: Record<string, unknown> = roll < 0.66 ? Object.create(null) : {};
  const keys = [...KEYS].sort(() => next() - 0.5).slice(0, Math.floor(next() * 6));
  if (next() < 0.5) keys.sort();
  for (const key of keys) Object.defineProperty(object, key, { value: tree(next, depth - 1), enumerable: true, writable: true, configurable: true });
  return object;
}

describe("P1 hot-path optimizations keep exact bytes and answers", () => {
  it("canonicalJson matches the reference serialization on edge cases", () => {
    const cases: unknown[] = [
      null, undefined, "s", 0, -0, NaN, [], {}, [undefined, , 1], { b: 1, a: undefined }, { a: 1, b: { d: [1, { f: 2, e: 3 }], c: null } },
      { 10: "x", 9: "y", a: "z" }, { toJSON: () => "own" }, { nested: { toJSON: () => "own" } }, new Date(5), { d: new Date(5) },
      Object.assign(Object.create(null), { b: 1, a: 2 }), Object.assign(Object.create(null), { a: 1, b: 2 }), [new Map(), new Set([1])],
      { f: () => 1, s: Symbol("s"), a: 1 }, [() => 1, Symbol("s")], Object.assign([1, 2], { extra: 3 }),
      JSON.parse('{"__proto__":{"a":1},"b":2}'), JSON.parse('{"a":1,"__proto__":null}'), JSON.parse('{"x":{"__proto__":"s"}}')
    ];
    for (const value of cases) expect(canonicalJson(value)).toBe(referenceCanonicalJson(value));
  });

  it("canonicalJson copies arrays with toJSON or a non-standard prototype and reads getters once", () => {
    class Plain { a = 1; b = 2; }
    class Own { toJSON() { return "class"; } a = 1; }
    class List<T> extends Array<T> {}
    const getter = (target: object, key: string) => {
      let calls = 0; return Object.defineProperty(target, key, { enumerable: true, get: () => ++calls });
    };
    const makers: (() => unknown)[] = [
      () => Object.assign([1, 2], { toJSON: () => "own" }), () => ({ a: Object.assign([1, 2], { toJSON: () => "own" }) }),
      () => Object.setPrototypeOf([1], { toJSON: () => "proto", length: 1 }), () => List.from([1, { b: 2, a: 1 }]),
      () => { const list = new List<unknown>(); list.push(1, 2); return { x: list }; }, () => Object.setPrototypeOf([1, 2], null),
      () => new Plain(), () => new Own(), () => new String("ab"), () => getter({}, "a"), () => getter({ a: 0 }, "b"),
      () => ({ nested: getter({}, "a") }), () => getter([1, 2], "0"), () => ({ list: getter([0, 1], "1") }),
      () => { const array: unknown[] = []; array[5] = { a: 1 }; return array; },
      () => ({ "1": 1, "01": 2, "4294967295": 3, "4294967294": 4, "-1": 5, a: 6 })
    ];
    // Same bytes, or the same error type where develop threw (a null-prototype array has no map).
    const outcome = (run: () => string) => { try { return run(); } catch (error) { return `throws ${(error as Error).name}`; } };
    for (const make of makers) expect(outcome(() => canonicalJson(make()))).toBe(outcome(() => referenceCanonicalJson(make())));
  });

  it("canonicalJson matches the reference serialization on random trees", () => {
    const next = random(20261108);
    for (let index = 0; index < 5000; index++) {
      const value = tree(next, 5);
      expect(canonicalJson(value)).toBe(referenceCanonicalJson(value));
    }
  });

  it("interned paths spell exactly the strings the walks used to build", () => {
    const root = publicPath("scene.content.surface");
    expect(root.field("cards").item(3).field("entity_id").path).toBe("scene.content.surface.cards[3].entity_id");
    expect(root.field("odd key").item(0).path).toBe(`${publicFieldPath("scene.content.surface", "odd key")}[0]`);
    expect(publicPath("").field("player").path).toBe("player");
    expect(publicPath("").field("a-b").path).toBe('["a-b"]');
    expect(root.field("cards")).toBe(root.field("cards"));
  });

  // Random oracle against develop's relation() kept verbatim in p1GlobalReferenceIdentity.ts.
  const KINDS = ["card_selection", "combat_turn", "map_navigation", "reward_claim", "card_reward_selection",
    "card_bundle_selection", "event_option", "event_dialogue", "rest_site", "treasure_room", "shop_inventory", "shop_room",
    "game_over", "abandon_run_confirmation", "crystal_sphere", "deck_upgrade_selection", "unknown_kind"];
  const REVISIONS = ["r1.7", "r1.8", "r1.9"] as const;
  const card = { entity_id: "x", definition_id: "STRIKE", name: "Strike", cost: "1", type: "attack", rarity: "basic",
    is_upgraded: false, is_selected: false };
  const shop = { kind: "shop_catalog", content: { kind: "shop_catalog", cards: [], relics: [], potions: [] } };
  const SEGMENTS = ["scene", "content", "surface", "cards", "entity_id", "card_previews", "options", "tooltips", "card",
    "extra_reads", "relics", "relic", "slot_entity_id", "deck", "player", "catalog", "subject", "target", "commitments", "map",
    "nodes", "combat", "enemies", "playable_cards", "target_entity_ids", "selected_card_entity_ids", "display_owner_entity_id",
    "rewards", "card_options", "potions", "card_removal", "bundles", "abandon_confirmation", "event_entity_id", "cells", "piles",
    "zones", "hand", "odd key", "$", "observation"];
  const pick = <T>(next: () => number, items: readonly T[]) => items[Math.floor(next() * items.length)]!;
  function randomPath(next: () => number): string {
    let path = next() < 0.1 ? "$.observation." : next() < 0.05 ? "." : "";
    const length = 1 + Math.floor(next() * 8);
    for (let index = 0; index < length; index++) {
      path += (index === 0 && path !== "" && !path.endsWith(".") ? "." : index === 0 ? "" : ".") + pick(next, SEGMENTS);
      if (next() < 0.4) path += `[${Math.floor(next() * 4)}]`;
    }
    return path;
  }
  const randomReads = (next: () => number) => Array.from({ length: Math.floor(next() * 4) }, () => {
    const roll = next(); return roll < 0.33 ? { kind: "surface_card", content: card } : roll < 0.66 ? shop : { kind: "other", content: {} };
  });
  const randomScene = (next: () => number) => ({ surface: { options: Array.from({ length: Math.floor(next() * 3) }, () => ({
    tooltips: Array.from({ length: Math.floor(next() * 3) }, () => next() < 0.5 ? { kind: "card", card } : { kind: "text" }) })) } });

  it("relation() and relationAt() equal develop's relation() on random policies and paths", () => {
    const next = random(4242);
    for (let round = 0; round < 3000; round++) {
      const kind = pick(next, KINDS); const revision = pick(next, REVISIONS);
      const reads = randomReads(next); const scene = next() < 0.7 ? randomScene(next) : undefined;
      const reference = referencePolicy(kind, reads, scene, revision);
      const policy = createPublicIdentityPolicy(kind, reads, scene, revision);
      for (let index = 0; index < 60; index++) {
        const path = randomPath(next); const expected = reference.relation(path);
        expect(policy.relation(path)).toBe(expected);
        expect(policy.relationAt(publicPath(path))).toBe(expected);
      }
    }
  });

  it("relation() equals develop's on targeted event, detail and shop paths for every kind and revision", () => {
    const scene = { surface: { options: [{ tooltips: [{ kind: "card", card }] }, { tooltips: [{ kind: "text" }, { kind: "card", card }] }] } };
    const reads = [{ kind: "surface_card", content: card }, shop];
    const paths = ["scene.content.surface.options[0].tooltips[0].card.entity_id",
      "scene.content.surface.options[1].tooltips[1].card.card_previews[2].entity_id", "extra_reads[0].content.entity_id",
      "extra_reads[0].content.card_previews[1].entity_id", "extra_reads[1].content.cards[3].card.entity_id",
      "extra_reads[1].content.relics[0].relic.card_previews[0].entity_id", "extra_reads[1].content.card_removal.slot_entity_id",
      "extra_reads[2].content.display_owner_entity_id", "$.observation.deck.content.cards[3].card_previews[0].entity_id",
      "commitments.declined_reward_groups[0]", "scene.content.surface.playable_cards[2].target_entity_ids[0]"];
    for (const kind of KINDS) for (const revision of REVISIONS) {
      const reference = referencePolicy(kind, reads, scene, revision); const policy = createPublicIdentityPolicy(kind, reads, scene, revision);
      for (const path of paths) {
        expect(policy.relation(path)).toBe(reference.relation(path));
        expect(policy.relationAt(publicPath(path))).toBe(reference.relation(path));
      }
    }
  });

  it("answers survive memo eviction", () => {
    const reference = referencePolicy("combat_turn"); const policy = createPublicIdentityPolicy("combat_turn");
    for (let index = 0; index < 40000; index++) {
      const path = `scene.content.surface.playable_cards[${index}].target_entity_ids[0]`;
      expect(policy.relation(path)).toBe(reference.relation(path));
      expect(policy.relationAt(publicPath(path))).toBe(reference.relation(path));
    }
    for (let index = 0; index < 300; index++) {
      const path = `combat.content.enemies[${index}].entity_id`;
      expect(policy.relationAt(publicPath(path))).toBe(reference.relation(path));
    }
  });
});
