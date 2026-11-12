import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { checkPublicBasisLight, passedPublicBasisLightCheck, PUBLIC_BASIS_SCHEMA, PUBLIC_BASIS_SCOPE } from "../src/index.js";
import { canonicalJson, configureReduceDecode, createSession, persistentContentValid, reduce, reduceDecodeStats, ReduceStrictSampleFailure } from "../src/p1Global/index.js";

const NAMES = ["synthetic-complete-combat", "managed-combat_turn", "managed-deck_enchant_selection", "managed-deck_upgrade_selection",
  "managed-game_over", "managed-map_navigation", "managed-reward_claim", "managed-shop_inventory",
  "reference-combat_turn", "reference-deck_enchant_selection", "reference-game_over", "reference-reward_claim"];

/** A revision-1 public basis around a recorded snapshot + Reads (the Host's light-checked shape). */
function basis(name: string): any {
  const original = JSON.parse(readFileSync(new URL(`./fixtures/p1-global/${name}.json`, import.meta.url), "utf8"));
  const reads = original.reads.map((read: any) => ({ read_id: read.read_id, kind: read.kind, response: read.response ?? read, error: read.error ?? null }));
  const missing = reads.flatMap((read: any) => read.response === null ? [`read:${read.read_id}:${read.error}`]
    : read.response.completeness.status !== "complete" || read.response.completeness.missing.length ? [`read:${read.read_id}:partial`] : []);
  if (original.snapshot.completeness.status !== "complete" || original.snapshot.completeness.missing.length) missing.push("snapshot:partial");
  return { schema: PUBLIC_BASIS_SCHEMA, revision: "1", capture_scope: PUBLIC_BASIS_SCOPE,
    capture_status: missing.length ? "partial" : "complete", snapshot: original.snapshot, reads, missing };
}
function lightBases(): { name: string; value: any }[] {
  return NAMES.flatMap(name => {
    const value = basis(name);
    try { checkPublicBasisLight(value); return [{ name, value }]; } catch { return []; }
  });
}

afterEach(() => configureReduceDecode(null));

describe("C1 reduce decode policy (fast chain, off by default)", () => {
  it("covers several recorded scenes with the light check", () => {
    expect(lightBases().length).toBeGreaterThanOrEqual(6);
  });

  it("vouches only for the exact basis, snapshot and Read response objects the light check saw", () => {
    const { value } = lightBases()[0]!;
    expect(passedPublicBasisLightCheck(value)).toBe(true);
    expect(passedPublicBasisLightCheck(structuredClone(value))).toBe(false);
    const swappedSnapshot = { ...value, snapshot: structuredClone(value.snapshot) };
    expect(passedPublicBasisLightCheck(swappedSnapshot)).toBe(false);
    const swappedRead = value.reads.length ? { ...value, reads: value.reads.map((read: any, index: number) =>
      index === 0 ? { ...read, response: structuredClone(read.response) } : read) } : null;
    if (swappedRead) {
      // A new basis object is never vouched for, even with the same parts.
      expect(passedPublicBasisLightCheck(swappedRead)).toBe(false);
      value.reads[0] = { ...value.reads[0], response: structuredClone(value.reads[0].response) };
      expect(passedPublicBasisLightCheck(value)).toBe(false);
    }
    expect(passedPublicBasisLightCheck(null)).toBe(false);
  });

  it("produces byte-identical reductions with the policy on and off", () => {
    for (const { name, value } of lightBases()) {
      configureReduceDecode(null);
      const strict = canonicalJson(reduce(createSession("e", "managed"), value));
      configureReduceDecode({ strictInterval: 1024, random: () => 0.5 });
      reduce(createSession("e", "managed"), value); // the first light-checked reduce is always a strict sample
      const light = canonicalJson(reduce(createSession("e", "managed"), value));
      expect(reduceDecodeStats(), name).toMatchObject({ light: 1, strict: 1, sampled: 1, persistent_rejects: 0, sample_failures: 0 });
      expect(light, name).toBe(strict);
    }
  });

  it("samples the first reduce and then each with probability 1/strict_interval; any basis not light-checked here is strict", () => {
    const { value } = lightBases()[0]!;
    const draws = [0.9, 0.2, 0.9, 0.24, 0.26, 0.9, 0.9];
    configureReduceDecode({ strictInterval: 4, random: () => draws.shift()! });
    for (let index = 0; index < 8; index++) reduce(createSession("e", "managed"), value);
    // First call sampled without a draw; then draws below 1/4 (0.2, 0.24) sample.
    expect(reduceDecodeStats()).toMatchObject({ light: 5, strict: 3, sampled: 3 });
    reduce(createSession("e", "managed"), structuredClone(value));
    expect(reduceDecodeStats()).toMatchObject({ light: 5, strict: 4, sampled: 3 });
    configureReduceDecode({ strictInterval: 1 });
    reduce(createSession("e", "managed"), value); reduce(createSession("e", "managed"), value);
    expect(reduceDecodeStats()).toMatchObject({ light: 0, strict: 2, sampled: 2 });
    expect(() => configureReduceDecode({ strictInterval: 0 })).toThrow(/positive integer/u);
  });

  it("non-claim pinned: a light-checked, zod-invalid basis is accepted on a non-sampled reduce", () => {
    const { value } = lightBases().find(item => item.value.snapshot.status === "interactive")!;
    const bad = structuredClone(value); bad.snapshot.observed_at = 42; // wrong type, not a light-check gate
    checkPublicBasisLight(bad);
    configureReduceDecode({ strictInterval: 64, random: () => 0.5 });
    reduce(createSession("e", "managed"), value); // spend the first (sampled) reduce on a valid basis
    expect(() => reduce(createSession("e", "managed"), bad)).not.toThrow();
    expect(reduceDecodeStats()).toMatchObject({ light: 1, sampled: 1, sample_failures: 0 });
  });

  it("a failing strict sample throws ReduceStrictSampleFailure and is counted apart", () => {
    const { value } = lightBases().find(item => item.value.snapshot.status === "interactive")!;
    const bad = structuredClone(value); bad.snapshot.observed_at = 42;
    checkPublicBasisLight(bad);
    configureReduceDecode({ strictInterval: 64, random: () => 0.5 });
    expect(() => reduce(createSession("e", "managed"), bad)).toThrow(ReduceStrictSampleFailure);
    expect(reduceDecodeStats()).toMatchObject({ sampled: 1, sample_failures: 1 });
    // A basis that is not vouched for fails the ordinary way (as with the policy off), not as a sample.
    const unvouched = structuredClone(bad);
    let error: unknown; try { reduce(createSession("e", "managed"), unvouched); } catch (caught) { error = caught; }
    expect(error).toBeInstanceOf(Error); expect(error).not.toBeInstanceOf(ReduceStrictSampleFailure);
    expect(reduceDecodeStats()).toMatchObject({ sample_failures: 1 });
  });

  it("the persistent part that feeds the observation is checked against the strict persistent schema on the light path", () => {
    const withPersistent = lightBases().filter(item => item.value.snapshot.persistent !== null);
    expect(withPersistent.length).toBeGreaterThan(0);
    const mutations: [string, (content: any) => void][] = [
      ["gold string", content => { content.player.gold = "12"; }], ["hp string", content => { content.player.hp = "x"; }],
      ["floor negative", content => { content.run.floor = -1; }], ["act zero", content => { content.run.act = 0; }],
      ["gold fraction", content => { content.player.gold = 1.5; }], ["max_hp NaN", content => { content.player.max_hp = Number.NaN; }],
      ["potion slot", content => { if (content.player.potions[0]) content.player.potions[0].slot = "0"; else content.player.potions = "none"; }],
      ["relic counter", content => { if (content.player.relics[0]) content.player.relics[0].counter = "3"; else content.player.relics = {}; }],
      ["relic definition_id", content => { if (content.player.relics[0]) content.player.relics[0].definition_id = 7; else content.player.relics = 0; }],
      ["relic name", content => { if (content.player.relics[0]) content.player.relics[0].name = 7; else content.player.relics = 0; }],
      ["relic keywords", content => { if (content.player.relics[0]) content.player.relics[0].keywords = [{ name: 3 }]; else content.player.relics = 0; }],
      ["relic card preview", content => { if (content.player.relics[0]) content.player.relics[0].card_previews = [{ entity_id: "c" }]; else content.player.relics = 0; }],
      ["preview enchantment amount", content => { content.player.relics = [{ entity_id: "r", definition_id: "R", keywords: [], card_previews: [{
        entity_id: "c", definition_id: "C", type: "Attack", cost: "1", rarity: "Basic", is_upgraded: false, is_selected: false,
        existing_enchantment: { definition_id: "E", amount: "2" } }] }]; }],
      ["player entity_id", content => { content.player.entity_id = ""; }], ["character_name", content => { content.player.character_name = 1; }],
      ["act_name", content => { content.run.act_name = 1; }], ["boss name", content => { content.run.bosses = [{ definition_id: "B", name: 1, order: 0 }]; }],
      ["modifier", content => { content.run.modifiers = [{ definition_id: "M", keywords: "none", card_previews: [] }]; }],
      ["scope", content => { content.scope = "other"; }], ["completeness", content => { content.completeness = null; }]];
    for (const { name, value } of withPersistent) {
      expect(persistentContentValid(value.snapshot), name).toBe(true);
      for (const [label, mutate] of mutations) {
        const bad = structuredClone(value); mutate(bad.snapshot.persistent.content);
        expect(persistentContentValid(bad.snapshot), `${name} ${label}`).toBe(false);
        try { checkPublicBasisLight(bad); } catch { continue; } // the light check already refuses it
        configureReduceDecode({ strictInterval: 1024, random: () => 0.5 });
        reduce(createSession("e", "managed"), value); // spend the sample
        // Not light: decoded strictly, so it fails exactly as with the policy off (never into the observation).
        expect(() => reduce(createSession("e", "managed"), bad), `${name} ${label}`).toThrow();
        expect(reduceDecodeStats(), `${name} ${label}`).toMatchObject({ light: 0, persistent_rejects: 1, sample_failures: 0 });
      }
    }
  });

  it("remembers a deep-frozen persistent part by identity (parsed once), never an unfrozen one", () => {
    const { value } = lightBases().find(item => item.value.snapshot.persistent !== null)!;
    configureReduceDecode({ strictInterval: 1024, random: () => 0.5 });
    persistentContentValid(value.snapshot); persistentContentValid(value.snapshot);
    expect(reduceDecodeStats()).toMatchObject({ persistent_parses: 2, persistent_cache_hits: 0 });
    const frozen = structuredClone(value.snapshot);
    const freeze = (item: any): any => { if (item && typeof item === "object") { Object.values(item).forEach(freeze); Object.freeze(item); } return item; };
    freeze(frozen.persistent.content);
    configureReduceDecode({ strictInterval: 1024, random: () => 0.5 });
    expect(persistentContentValid(frozen)).toBe(true); expect(persistentContentValid(frozen)).toBe(true);
    expect(reduceDecodeStats()).toMatchObject({ persistent_parses: 1, persistent_cache_hits: 1 });
    expect(persistentContentValid({ ...frozen, persistent: { ...frozen.persistent, extra: 1 } })).toBe(false);
  });

  it("never vouches for a navigation-profile snapshot (its range gate lives in the strict decode)", () => {
    const { value } = lightBases()[0]!;
    const profiled = structuredClone(value); profiled.snapshot.input_profile = "x";
    try { checkPublicBasisLight(profiled); } catch { return; }
    expect(passedPublicBasisLightCheck(profiled)).toBe(false);
  });

  it("keeps the strict decode loud: a shape the zod schema rejects still throws on a strict reduce", () => {
    const { value } = lightBases().find(item => item.value.snapshot.status === "interactive")!;
    const bad = structuredClone(value); bad.snapshot.observed_at = 42; // wrong type, not a light-check gate
    checkPublicBasisLight(bad);
    configureReduceDecode(null);
    expect(() => reduce(createSession("e", "managed"), bad)).toThrow();
    configureReduceDecode({ strictInterval: 64 });
    expect(() => reduce(createSession("e", "managed"), bad)).toThrow(); // the first reduce is a strict sample
  });
});
