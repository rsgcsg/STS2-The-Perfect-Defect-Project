import { describe, expect, it } from "vitest";
import { canonicalJson, createPublicIdentityPolicy, publicFieldPath } from "../src/p1Global/index.js";
import { publicPath } from "../src/p1Global/publicPath.js";

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

  it("memoized relations equal fresh answers and never leak between different path sets", () => {
    const tooltip = { options: [{ tooltips: [{ kind: "card", card: { entity_id: "x", definition_id: "STRIKE", name: "Strike", cost: "1",
      type: "attack", rarity: "basic", is_upgraded: false, is_selected: false } }] }] };
    const paths = [
      "scene.content.surface.options[0].entity_id", "scene.content.surface.options[0].tooltips[0].card.entity_id",
      "scene.content.surface.options[1].tooltips[0].card.entity_id", "deck.content.cards[4].entity_id",
      "deck.content.cards[4].card_previews[0].entity_id", "catalog[2].subject", "player.content.relics[1].entity_id",
      "map.content.nodes[7].entity_id", "scene.content.surface.room_entity_id", "scene.content.surface.label"
    ];
    const plain = () => createPublicIdentityPolicy("event_option", [], { surface: { options: [{}] } });
    const withCard = () => createPublicIdentityPolicy("event_option", [], { surface: tooltip });
    const first = paths.map(path => plain().relation(path));
    const second = paths.map(path => withCard().relation(path));
    // Same nativeKind, different visible card tooltip: only the tooltip identity changes.
    expect(first[1]).toBeNull();
    expect(second[1]).toBe("declared");
    expect(second.filter((value, index) => value !== first[index])).toEqual(["declared"]);
    for (const make of [plain, withCard, plain]) {
      const policy = make();
      for (const path of paths) {
        expect(policy.relationAt(publicPath(path))).toBe(policy.relation(path));
        expect(policy.relation(path)).toBe((make === withCard ? second : first)[paths.indexOf(path)]);
      }
    }
    expect(createPublicIdentityPolicy("combat_turn").relation("scene.content.surface.playable_cards[2].target_entity_ids[0]")).toBe("reference");
    expect(createPublicIdentityPolicy("map_navigation").relation("scene.content.surface.playable_cards[2].target_entity_ids[0]")).toBeNull();
  });
});
