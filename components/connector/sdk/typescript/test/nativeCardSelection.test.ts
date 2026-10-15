import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { decodePlayerSnapshot } from "../src/protocol.js";
import { nativeCardSelectionProblems } from "../src/nativeCardSelection.js";

// Synthetic Host producer packet plus declared public HUD fixtures. This tests
// current SDK rejection independently of the Host's pinned published SDK.
const baseline = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/managed-selector-snapshot.json", import.meta.url), "utf8"));

describe("marked Managed selector contract", () => {
  it("accepts the whole public packet and preserves unmarked historical readers", () => {
    expect(() => decodePlayerSnapshot(baseline)).not.toThrow();
    expect(nativeCardSelectionProblems(baseline)).toEqual([]);
    const legacy = structuredClone(baseline);
    delete legacy.interaction.content.surface.selector_profile;
    legacy.interaction.content.context.combat = null;
    expect(() => decodePlayerSnapshot(legacy)).not.toThrow();
  });

  it("rejects incomplete cards, HUD, ownership, candidate or empty catalogs", () => {
    const mutations = [
      (s: any) => { s.interaction.content.surface.cards[0].description = null; },
      (s: any) => { s.interaction.content.surface.cards.pop(); },
      (s: any) => { s.interaction.content.surface.selector_kind = "unknown"; },
      (s: any) => { s.interaction.content.context.combat = null; },
      (s: any) => { s.bound_actions.actions.pop(); s.bound_actions.total_count--; s.bound_actions.materialized_count--; },
      (s: any) => { Object.assign(s.bound_actions, { actions: [], total_count: 0, materialized_count: 0 }); s.interaction.capabilities = []; },
      ...["hp", "max_hp", "block", "energy", "max_energy", "stars", "draw_pile_count", "discard_pile_count", "exhaust_pile_count", "orb_slots"]
        .map(key => (s: any) => { delete s.interaction.content.context.combat.player[key]; }),
      (s: any) => { s.interaction.content.context.combat.player.player_entity_id = "foreign"; },
      (s: any) => { s.interaction.content.context.combat.round = 0; },
      (s: any) => { s.interaction.content.context.combat.turn_owner = null; },
      (s: any) => { s.interaction.content.context.combat.player.statuses[0].amount = null; },
      (s: any) => { s.interaction.content.context.combat.player.statuses[0].description = null; },
      (s: any) => { s.interaction.content.context.combat.player.orbs[0].passive = null; },
      (s: any) => { s.interaction.content.context.combat.player.orbs[0].keywords = null; },
      (s: any) => { s.interaction.content.context.combat.player.companions[0].alive = null; },
      (s: any) => { s.interaction.content.context.combat.player.companions[0].hp = 5; },
      (s: any) => { s.interaction.content.context.combat.enemies[0].hp = null; },
      (s: any) => { s.interaction.content.context.combat.enemies[0].health_display = "unknown"; },
      (s: any) => { s.interaction.content.context.combat.enemies[0].statuses = null; },
      (s: any) => { s.interaction.content.context.combat.enemies[0].intents = [{ type: "Attack" }]; }
    ];
    for (const mutate of mutations) {
      const value = structuredClone(baseline); mutate(value);
      expect(nativeCardSelectionProblems(value).length).toBeGreaterThan(0);
      expect(() => decodePlayerSnapshot(value)).toThrow(/native card selection/u);
    }
  });

  it("keeps explicit hidden health and unavailable diagnostics distinct from missing facts", () => {
    for (const mode of ["Normal", "InfiniteWithNumbers", "InfiniteWithoutNumbers"]) {
      const value = structuredClone(baseline);
      Object.assign(value.interaction.content.context.combat.enemies[0], {
        health_display: mode, ...(mode === "InfiniteWithoutNumbers" ? { hp: null, max_hp: null } : {})
      });
      expect(() => decodePlayerSnapshot(value)).not.toThrow();
    }
    const unavailable = structuredClone(baseline);
    unavailable.status = "visible_unsupported";
    unavailable.completeness.status = "partial";
    Object.assign(unavailable.bound_actions, { status: "unavailable", actions: [], total_count: 0, materialized_count: 0 });
    unavailable.interaction.capabilities = [];
    unavailable.interaction.content.context.combat = null;
    expect(() => decodePlayerSnapshot(unavailable)).not.toThrow();
  });
});


it("empty intent label is a native display value, never an absent label or hover", () => {
  const value = structuredClone(baseline);
  value.interaction.content.context.combat.enemies[0].intents = [{ type: "Debuff", label: "", title: "Strategic",
    description: "This enemy intends to apply a Debuff to you." }];
  expect(() => decodePlayerSnapshot(value)).not.toThrow();
  for (const mutate of [(s: any) => { delete s.label; }, (s: any) => { s.label = null; },
    (s: any) => { s.label = 0; }, (s: any) => { s.label = "{Damage}"; },
    (s: any) => { s.title = ""; }, (s: any) => { s.description = null; }]) {
    const invalid = structuredClone(value); mutate(invalid.interaction.content.context.combat.enemies[0].intents[0]);
    expect(() => decodePlayerSnapshot(invalid)).toThrow(/native card selection/u);
  }
});
