import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { checkPublicBasisLight, passedPublicBasisLightCheck, PUBLIC_BASIS_SCHEMA, PUBLIC_BASIS_SCOPE } from "../src/index.js";
import { canonicalJson, configureReduceDecode, createSession, reduce, reduceDecodeStats } from "../src/p1Global/index.js";

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
      configureReduceDecode({ strictInterval: 1024 });
      reduce(createSession("e", "managed"), value); // ordinal 0 always decodes strictly
      const light = canonicalJson(reduce(createSession("e", "managed"), value));
      expect(reduceDecodeStats(), name).toEqual({ light: 1, strict: 1 });
      expect(light, name).toBe(strict);
    }
  });

  it("decodes strictly on every strict_interval-th reduce and for any basis not light-checked here", () => {
    const { value } = lightBases()[0]!;
    configureReduceDecode({ strictInterval: 4 });
    for (let index = 0; index < 8; index++) reduce(createSession("e", "managed"), value);
    expect(reduceDecodeStats()).toEqual({ light: 6, strict: 2 });
    reduce(createSession("e", "managed"), structuredClone(value));
    expect(reduceDecodeStats()).toEqual({ light: 6, strict: 3 });
    configureReduceDecode({ strictInterval: 1 });
    reduce(createSession("e", "managed"), value);
    expect(reduceDecodeStats()).toEqual({ light: 0, strict: 1 });
    expect(() => configureReduceDecode({ strictInterval: 0 })).toThrow(/positive integer/u);
  });

  it("keeps the strict decode loud: a shape the zod schema rejects still throws on a strict reduce", () => {
    const { value } = lightBases().find(item => item.value.snapshot.status === "interactive")!;
    const bad = structuredClone(value); bad.snapshot.observed_at = 42; // wrong type, not a light-check gate
    checkPublicBasisLight(bad);
    configureReduceDecode(null);
    expect(() => reduce(createSession("e", "managed"), bad)).toThrow();
    configureReduceDecode({ strictInterval: 64 });
    expect(() => reduce(createSession("e", "managed"), bad)).toThrow(); // ordinal 0 is strict
  });
});
