import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { publicCombatContextProblems } from "../src/publicCombat.js";
import { completeCombat, createSession, reduce } from "../src/p1Global/index.js";
const basis = (name = "synthetic-complete-combat"): any => JSON.parse(readFileSync(new URL(`./fixtures/p1-global/${name}.json`, import.meta.url), "utf8"));
const context = () => basis().snapshot.interaction.content.context;

describe("shared native public combat context boundary", () => {
  it("accepts captured Reference DTO and the declared synthetic current-native shape", () => {
    expect(publicCombatContextProblems(basis("reference-combat_turn").snapshot.interaction.content.context)).toEqual([]);
    expect(publicCombatContextProblems(context())).toEqual([]);
    expect(completeCombat(context())).toBe(true);
  });
  it("keeps the archived Managed HUD mismatch incomplete without defaults", () => {
    const b = basis("managed-combat_turn");
    expect(publicCombatContextProblems(b.snapshot.interaction.content.context).length).toBeGreaterThan(0);
    expect(completeCombat(b.snapshot.interaction.content.context)).toBe(false);
    expect(reduce(createSession("missing-native-shape", "managed"), b).missing).toContain("required:combat:invalid_content");
  });
  it.each(["intent_type", "status_amount", "companion_health", "potion_flags", "orb_values", "health_numbers", "health_visibility", "top_phase"])("refuses missing/incoherent nested %s through both shared schema and GLOBAL admission", variant => {
    const c = context();
    if (variant === "intent_type") delete c.enemies[0].intents[0].type;
    if (variant === "status_amount") c.player.statuses = [{ definition_id: "STRENGTH", type: "Buff" }];
    if (variant === "companion_health") c.player.companions = [{ entity_id: "companion", definition_id: "COMPANION", is_alive: true, block: 0, statuses: [] }];
    if (variant === "potion_flags") c.player.potion_states = [{ entity_id: "potion", target_type: "Self", can_use: false }];
    if (variant === "orb_values") c.player.orbs = [{ entity_id: "orb", definition_id: "LIGHTNING", passive_value: 3, queue_index: 0, is_next_to_evoke: true }];
    if (variant === "health_numbers") c.enemies[0].hp_numbers_available = false;
    if (variant === "health_visibility") c.enemies[0].health_bar_visible = false;
    if (variant === "top_phase") delete c.is_play_phase;
    expect(publicCombatContextProblems(c).length).toBeGreaterThan(0);
    expect(completeCombat(c)).toBe(false);
  });
  it("accepts complete nested DTOs and respects intentionally hidden health numbers", () => {
    const c = context(); c.player.statuses = [{ definition_id: "STRENGTH", type: "Buff", amount: 2 }];
    c.player.companions = [{ entity_id: "companion", definition_id: "COMPANION", is_alive: true,
      hp: null, max_hp: null, block: 0, health_bar_visible: false, hp_display_mode: "hidden", hp_numbers_available: false, statuses: [] }];
    c.player.potion_states = [{ entity_id: "potion", target_type: "Self", can_use: false, automatic: true }];
    c.player.orbs = [{ entity_id: "orb", definition_id: "LIGHTNING", passive_value: 3, evoke_value: 8, queue_index: 0, is_next_to_evoke: true }];
    expect(publicCombatContextProblems(c)).toEqual([]); expect(completeCombat(c)).toBe(true);
  });
  it("preserves the same finite-number fence across all required facts and Reads", () => {
    for (const mutate of [(b: any) => { b.snapshot.persistent.content.player.hp = Infinity; },
      (b: any) => { b.reads.find((r: any) => r.kind === "map").response.content.extension = { value: JSON.parse("1e999") }; }]) {
      const b = basis(); mutate(b);
      expect(reduce(createSession("finite-public-facts", "managed"), b).missing).toContain("required:finite_public_numbers");
    }
  });
  it("does not admit nonfinite public numbers", () => {
    const c = context(); c.enemies[0].hp = Infinity;
    expect(publicCombatContextProblems(c).length).toBeGreaterThan(0);
    expect(completeCombat(c)).toBe(false);
  });
});
