import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { canonicalJson, completeCombatSurface, createSession, reduce, resolveFollow, type Lowering, type P1Basis } from "../src/p1Global/index.js";
const fixture = (name: string): any => JSON.parse(readFileSync(new URL(`./fixtures/p1-global/${name}.json`, import.meta.url), "utf8"));
const project = (basis: P1Basis) => reduce(createSession("admission", "managed"), basis);
const count = (basis: any) => { basis.snapshot.bound_actions.total_count = basis.snapshot.bound_actions.materialized_count = basis.snapshot.bound_actions.actions.length; };
const read = (basis: any, kind: string): any => basis.reads.find((item: any) => item.kind === kind).response;

describe("P1 r1.1 explicit complete-capture admission", () => {
  it("accepts a fully shaped same-snapshot synthetic capture through production codecs", () => {
    // Captured combat + explicit synthetic map topology/current position; no native qualification claim.
    const result = project(fixture("synthetic-complete-combat"));
    expect(result.complete).toBe(true);
    expect(result.missing).toEqual([]);
    expect(result.observation.revision).toBe("r1.1");
    expect(result.observation.envelope.required_information_complete).toBe(true);
    expect(result.observation.envelope.read_instances).toHaveLength(3);
  });
  it.each(["run_deck", "combat_piles", "map"])("rejects required %s even when its descriptor was never advertised", kind => {
    const b = fixture("synthetic-complete-combat");
    b.snapshot.reads = b.snapshot.reads.filter((item: any) => item.kind !== kind);
    b.reads = b.reads.filter((item: any) => item.kind !== kind);
    const r = project(b);
    expect(r.complete).toBe(false);
    expect(r.observation.envelope.required_information_complete).toBe(false);
  });
  it.each(["nodes", "visited", "current_position", "act_index"])("rejects missing map field %s despite complete read envelope", key => {
    const b = fixture("synthetic-complete-combat"); delete read(b, "map").content[key];
    const r = project(b); expect(r.complete).toBe(false); expect(r.missing).toContain("required:map:invalid_content");
  });
  it.each(["missing_edge_target", "wrong_act", "bad_current", "minimal_blob"])("rejects invalid complete-declared map %s", variant => {
    const b = fixture("synthetic-complete-combat"); const m = read(b, "map");
    if (variant === "missing_edge_target") m.content.nodes[0].children.push({ col: 999, row: 999 });
    if (variant === "wrong_act") m.content.act_index++;
    if (variant === "bad_current") m.content.current_position = { col: 999, row: 999 };
    if (variant === "minimal_blob") m.content = { kind: "map" };
    expect(project(b).missing).toContain("required:map:invalid_content");
  });
  it.each(["run_deck", "combat_piles"])("rejects malformed %s content instead of treating empty as complete", kind => {
    const b = fixture("synthetic-complete-combat"); read(b, kind).content = { kind };
    expect(project(b).complete).toBe(false);
  });
  it("rejects malformed nested capture shapes without throwing or publishing", () => {
    for (const mutate of [(b: any) => { read(b, "combat_piles").content.zones = [null]; },
      (b: any) => { b.snapshot.interaction.content.context = { kind: "combat" }; },
      (b: any) => { b.snapshot.interaction.content.surface = { kind: "combat_turn" }; }]) {
      const b = fixture("synthetic-complete-combat"); mutate(b);
      expect(project(b).complete).toBe(false);
    }
  });
  it("retains strict codec rejection of null owning content", () => {
    const b = fixture("synthetic-complete-combat"); b.snapshot.interaction.content.context = null;
    expect(() => project(b)).toThrow(/strict decoding/u);
  });
  it("accepts the actual native71cf serialized discard DTO without invented targets", () => {
    // Existing71cf STS2_MCP.dll, actual CombatPotionDiscardTests.Surface(0) constructor,
    // serialized with System.Text.Json SnakeCaseLower. Assembly SHA d822df953b2bf37cdf0a95aa7bf89075c41a55f8314c4bc55132c996643c4207.
    const surface = fixture("reference-71cf-serialized-discard-surface");
    expect(surface.discardable_potions[0]).not.toHaveProperty("target_entity_ids");
    expect(completeCombatSurface(surface)).toBe(true);
    for (const [key, invalid] of [["slot", 0.5], ["target_type", null], ["can_use", "false"], ["automatic", 1]]) {
      const changed = structuredClone(surface); changed.discardable_potions[0][key as string] = invalid;
      expect(completeCombatSurface(changed)).toBe(false);
    }
    const b = fixture("synthetic-complete-combat"); b.snapshot.interaction.content.surface = surface;
    const potion = surface.discardable_potions[0];
    b.snapshot.persistent.content.player.potions.push({ ...potion, keywords: [], card_previews: [] });
    b.snapshot.interaction.content.context.player.potion_states.push({ entity_id: potion.entity_id, target_type: potion.target_type,
      can_use: potion.can_use, automatic: potion.automatic });
    b.snapshot.referents.push({ referent_id: potion.entity_id, role: "discardable_potion", kind: "entity", label: potion.name,
      state: { visible: true, observation_basis: "native_visible_fact" },
      properties_schema: "sts2.player-environment/referent/discardable_potion-1", properties: potion });
    b.snapshot.bound_actions.actions = [{ ...b.snapshot.bound_actions.actions[0], bound_action_id: "action_native_discard", verb: "activate",
      subject_referent_id: potion.entity_id, arguments: [] }]; count(b);
    const r = project(b); expect(r.complete).toBe(true);
    expect(r.observation.catalog[0]!.kind).toBe("potion.discard");
    expect(r.observation.catalog[0]!.subject).toBe((r.observation.player.content as any).potions[0].entity_id);
    expect(r.lowering[r.observation.catalog[0]!.choice_id]!.first!.bound_action_id).toBe("action_native_discard");
  });
  it("preserves native map state text including None without inventing legality", () => {
    const b = fixture("synthetic-complete-combat"); read(b, "map").content.nodes[0].state = "none";
    const r = project(b); expect(r.complete).toBe(true);
    expect((r.observation.map.content as any).nodes[0].state).toBe("none");
  });
  it("preserves all Read instances and rejects ambiguous aggregate multiplicity", () => {
    const b = fixture("synthetic-complete-combat");
    const descriptor = structuredClone(b.snapshot.reads[0]); descriptor.read_id = "read_second_deck";
    const captured = structuredClone(b.reads[0]); captured.read_id = descriptor.read_id; captured.response.read_id = descriptor.read_id;
    b.snapshot.reads.push(descriptor); b.reads.push(captured);
    const r = project(b); expect(r.complete).toBe(false); expect(r.missing).toContain("read:run_deck:unsupported_multiplicity");
    expect(r.observation.envelope.read_instances.filter(item => item.kind === "run_deck")).toHaveLength(2);
  });
  it("retains nonaggregate same-kind Read instances rather than taking the last", () => {
    const b = fixture("synthetic-complete-combat");
    for (const i of [1, 2]) {
      const d = { ...b.snapshot.reads[0], read_id: `read_surface_${i}`, kind: "surface_card", content_schema: "sts2.player-environment/read/surface_card-1" };
      const c = structuredClone(b.reads[0]); c.read_id = d.read_id; c.kind = d.kind;
      Object.assign(c.response, { read_id: d.read_id, kind: d.kind, content_schema: d.content_schema, content: { detail: `detail-${i}` } });
      b.snapshot.reads.push(d); b.reads.push(c);
    }
    const r = project(b); expect(r.complete).toBe(true);
    expect(r.observation.extra_reads.map(item => item.content)).toEqual([{ detail: "detail-1" }, { detail: "detail-2" }]);
  });
  it("rejects a foreign Read session and a future aggregate schema", () => {
    for (const mutate of [(b: any) => { read(b, "map").session.runtime_instance_id = "foreign"; },
      (b: any) => { read(b, "map").content_schema = b.snapshot.reads[2].content_schema = "sts2.player-environment/read/future_map-1"; }]) {
      const b = fixture("synthetic-complete-combat"); mutate(b); expect(project(b).complete).toBe(false);
    }
  });
  it("does not auto-execute any unqualified singleton map or event choice", () => {
    const b = fixture("managed-map_navigation"); expect(project(b).forced_choice_id).toBeNull();
    b.snapshot.interaction.kind = "event_option"; b.snapshot.referents[0].role = "option";
    expect(project(b).forced_choice_id).toBeNull();
  });
  it("retains meaningful multiselect deselect and cancel with no automatic confirmation", () => {
    const b = fixture("managed-deck_enchant_selection"); const s = b.snapshot.interaction;
    s.kind = "card_selection"; s.content_schema = "sts2.player-environment/surface/card_selection-1";
    Object.assign(s.content.surface, { kind: "card_selection", min_select: 1, max_select: 3, selected_count: 1, cancelable: true, can_cancel_selection: true });
    const a = b.snapshot.bound_actions.actions[0];
    b.snapshot.bound_actions.actions.push({ ...a, bound_action_id: "action_deselect", verb: "deselect" },
      { ...a, bound_action_id: "action_cancel", verb: "cancel", subject_referent_id: null }); count(b);
    const r = project(b); const kinds = r.observation.catalog.map(c => c.kind);
    expect(kinds).toContain("selector.deselect"); expect(kinds).toContain("selector.cancel"); expect(r.excluded).toEqual([]);
    for (const low of Object.values(r.lowering)) expect(low.follow).toEqual([]);
  });
  it("refuses unqualified reward commitments before a mandatory set can dead-end", () => {
    const b = fixture("managed-reward_claim");
    b.snapshot.bound_actions.actions = b.snapshot.bound_actions.actions.filter((a: any) => a.subject_referent_id); count(b);
    b.snapshot.interaction.content.surface.can_proceed = false;
    const r = project(b); expect(r.complete).toBe(false);
    expect(r.observation.catalog.some(c => c.kind === "reward.decline_group")).toBe(false);
    expect(r.observation.catalog.some(c => c.kind === "reward.open_card_group")).toBe(true);
  });
  it("requires current hand and complete piles on a combat child selector", () => {
    const b = fixture("managed-deck_enchant_selection"); const combat = fixture("synthetic-complete-combat");
    b.snapshot.interaction.content.context.combat = combat.snapshot.interaction.content.context;
    delete b.snapshot.interaction.content.context.combat.player.hand;
    const r = project(b); expect(r.missing).toContain("required:combat:invalid_content"); expect(r.missing).toContain("required:piles");
    expect(r.observation.piles.availability).toBe("unsupported");
  });
  it("preserves known discard/exhaust display order and rejects unordered legacy claims", () => {
    const b = fixture("synthetic-complete-combat"); const piles = read(b, "combat_piles");
    const draw = piles.content.zones.find((z: any) => z.zone === "draw");
    const discard = piles.content.zones.find((z: any) => z.zone === "discard");
    discard.cards = [draw.cards.shift(), draw.cards.pop()]; discard.card_count = 2; draw.card_count = draw.cards.length;
    expect(discard.cards[0].definition_id).not.toBe(discard.cards[1].definition_id);
    b.snapshot.interaction.content.context.player.draw_pile_count = draw.card_count;
    b.snapshot.interaction.content.context.player.discard_pile_count = discard.card_count;
    const first = project(b); expect(first.complete).toBe(true);
    const reversed = structuredClone(b); read(reversed, "combat_piles").content.zones.find((z: any) => z.zone === "discard").cards.reverse();
    expect(canonicalJson(project(reversed).observation)).not.toBe(canonicalJson(first.observation));
    discard.ordering_semantics = "unordered_multiset";
    expect(project(b).missing).toContain("required:piles:invalid_content");
  });
  it("does not call unqualified scene information complete merely because common parts exist", () => {
    const b = fixture("synthetic-complete-combat");
    b.snapshot.interaction.kind = "event_option"; b.snapshot.interaction.content_schema = "sts2.player-environment/surface/event_option-1";
    b.snapshot.interaction.content.surface.kind = "event_option";
    expect(project(b).missing).toContain("required:scene_information_qualification:event");
  });
  it("checks owning surface schema, public player identity and pile counts", () => {
    for (const mutate of [(b: any) => { b.snapshot.interaction.content_schema = "sts2.player-environment/surface/future_combat-1"; },
      (b: any) => { b.snapshot.interaction.content.context.player.player_entity_id = "player_foreign"; },
      (b: any) => { b.snapshot.interaction.content.context.player.draw_pile_count++; }]) {
      const b = fixture("synthetic-complete-combat"); mutate(b); expect(project(b).complete).toBe(false);
    }
  });
  it("uses encoded bytes for the observation limit", () => {
    const b = fixture("synthetic-complete-combat"); b.snapshot.interaction.content.surface.public_text = "界".repeat(3 * 1024 * 1024);
    expect(project(b).missing).toContain("observation_over_limit");
  });
  it("never derives a generic continuation from a sole confirm or continue", () => {
    const page = fixture("managed-deck_enchant_selection").snapshot;
    page.interaction.stage = "preview";
    const action = page.bound_actions.actions[0]; action.verb = "confirm"; action.subject_referent_id = null;
    page.bound_actions.actions = [action]; page.bound_actions.total_count = page.bound_actions.materialized_count = 1;
    const low: Lowering = { first: null, follow: [], commitment: null, group_key: null, option_index: null,
      option_definition: null, option_entity: null, option_public: null };
    expect(resolveFollow({ rule_id: "enchant_confirm", expect_native_kind: "deck_enchant_selection", expect_verb: "confirm", optional: true }, low, page)).toBeNull();
  });
  it.each(["usable_potion", "discardable_potion"])("binds potion discard from exact native %s role without changing instance", role => {
    const b = fixture("synthetic-complete-combat"); const potion = { entity_id: "potion_test", definition_id: "FIRE_POTION", name: "Fire Potion",
      description: "Deal 20 damage.", slot: 0, keywords: [], card_previews: [] };
    b.snapshot.persistent.content.player.potions.push(potion);
    b.snapshot.referents.push({ referent_id: potion.entity_id, role, kind: "entity", label: potion.name,
      state: { visible: true, observation_basis: "native_visible_fact" }, properties_schema: `sts2.player-environment/referent/${role}-1`,
      properties: { entity_id: potion.entity_id, name: potion.name, target_entity_ids: [] } });
    const a = b.snapshot.bound_actions.actions[0];
    b.snapshot.bound_actions.actions.push({ ...a, bound_action_id: "action_discard_potion", verb: "activate", subject_referent_id: potion.entity_id, arguments: [] }); count(b);
    const r = project(b); expect(r.complete).toBe(true);
    const discard = r.observation.catalog.find(c => c.kind === "potion.discard")!;
    expect(discard.subject).toBe((r.observation.player.content as any).potions[0].entity_id);
    expect(r.lowering[discard.choice_id]!.first!.bound_action_id).toBe("action_discard_potion");
  });
  it.each([["treasure_chest", "activate", "treasure_room", "treasure.open"],
    ["treasure_relic_selection", "select", "relic", "treasure.take"],
    ["treasure_relic_selection", "skip", "treasure_room", "treasure.skip"],
    ["treasure_completion", "close", "treasure_room", "treasure.proceed"]])("preserves native %s/%s command binding in treasure family", (kind, verb, role, expected) => {
    // Command-contract conformance only; full scene information remains explicitly unqualified.
    const b = fixture("synthetic-complete-combat"); const action = b.snapshot.bound_actions.actions[0];
    b.snapshot.referents.find((ref: any) => ref.referent_id === action.subject_referent_id).role = role;
    action.verb = verb; action.arguments = []; b.snapshot.bound_actions.actions = [action]; count(b);
    Object.assign(b.snapshot.interaction, { kind, content_schema: `sts2.player-environment/surface/${kind}-1` });
    b.snapshot.interaction.content.surface = { kind };
    const r = project(b); expect(r.observation.scene.family).toBe("treasure"); expect(r.complete).toBe(false);
    expect(r.observation.catalog[0]!.kind).toBe(expected);
    expect(r.lowering[r.observation.catalog[0]!.choice_id]!.first!.bound_action_id).toBe(action.bound_action_id);
  });
  it("checks full public preview state and stable instance before binding a reward continuation", () => {
    const page = fixture("managed-deck_enchant_selection").snapshot;
    page.interaction.kind = "card_reward_selection"; page.interaction.content_schema = "sts2.player-environment/surface/card_reward_selection-1";
    page.interaction.content.surface.kind = "card_reward_selection"; const card = page.interaction.content.surface.cards[0];
    const publicCard = (c: any) => { const { index: _index, is_selected: _selected, ...rest } = c; return canonicalJson(rest); };
    const low: Lowering = { first: null, follow: [], commitment: null, group_key: "group", option_index: 0,
      option_definition: card.definition_id, option_entity: card.entity_id, option_public: publicCard(card) };
    const rule = { rule_id: "card_reward_select", expect_native_kind: "card_reward_selection", expect_verb: "select", optional: false } as const;
    expect(resolveFollow(rule, low, page)).not.toBeNull();
    for (const key of ["is_upgraded", "cost", "description"]) {
      const changed = structuredClone(page); const c = changed.interaction.content.surface.cards[0];
      c[key] = key === "is_upgraded" ? !c[key] : "changed"; expect(resolveFollow(rule, low, changed)).toBeNull();
    }
    expect(resolveFollow(rule, { ...low, option_entity: "different_instance" }, page)).toBeNull();
  });
});
