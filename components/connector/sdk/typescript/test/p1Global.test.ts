import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { applyCommitment, canonicalJson, createSession, reduce, resolveFollow,
  type P1Basis } from "../src/p1Global/index.js";

// Real Player Environment bases captured by the P1 runner (Managed and Reference, v0.111.0).
const fixture = (name: string): P1Basis =>
  JSON.parse(readFileSync(new URL(`./fixtures/p1-global/${name}.json`, import.meta.url), "utf8")) as P1Basis;
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

describe("P1 global r1 shared reducer", () => {
  it("is deterministic: the same basis and state give byte-identical observations", () => {
    for (const name of ["managed-combat_turn", "reference-combat_turn", "managed-reward_claim", "reference-reward_claim"]) {
      const a = reduce(createSession("e", "managed"), fixture(name));
      const b = reduce(createSession("e", "managed"), fixture(name));
      expect(canonicalJson(a.observation)).toBe(canonicalJson(b.observation));
      expect(a.complete).toBe(false); // archived captures lack required map/qualification in r1.1
    }
  });

  it("does not leak hidden draw order: permuting the draw pile leaves the bytes unchanged", () => {
    for (const name of ["managed-combat_turn", "reference-combat_turn"]) {
      const base = fixture(name);
      const permuted = clone(base);
      const piles = permuted.reads.find((read) => read.kind === "combat_piles")!.response as any;
      const draw = piles.content.zones.find((zone: any) => zone.zone === "draw");
      expect(draw.cards.length).toBeGreaterThan(1);
      draw.cards.reverse();
      const left = reduce(createSession("e", "managed"), base).observation;
      const right = reduce(createSession("e", "managed"), permuted).observation;
      expect(canonicalJson(right)).toBe(canonicalJson(left));
    }
  });

  it("does not reveal which of two identical cards is drawn first", () => {
    for (const name of ["managed-combat_turn", "reference-combat_turn"]) {
      const base = fixture(name);
      const draw = (basis: P1Basis) => ((basis.reads.find((read) => read.kind === "combat_piles")!.response as any)
        .content.zones.find((zone: any) => zone.zone === "draw").cards as any[]);
      const cards = draw(base);
      const same = cards.findIndex((card, i) => cards.some((other, j) => j > i && other.definition_id === card.definition_id
        && other.is_upgraded === card.is_upgraded));
      expect(same).toBeGreaterThanOrEqual(0);
      const twin = cards.findIndex((other, j) => j > same && other.definition_id === cards[same].definition_id);
      const swapped = clone(base); const list = draw(swapped);
      [list[same], list[twin]] = [list[twin], list[same]];
      // Warm the alias table first so both instances already have public aliases.
      const warm = reduce(createSession("e", "managed"), base).state;
      expect(canonicalJson(reduce(warm, swapped).observation)).toBe(canonicalJson(reduce(warm, base).observation));
    }
  });

  it("keeps native reward opening until the producer qualifies all alternatives and relationships", () => {
    for (const name of ["managed-reward_claim", "reference-reward_claim"]) {
      const result = reduce(createSession("e", "managed"), fixture(name));
      const kinds = result.observation.catalog.map((choice) => choice.kind);
      expect(kinds).toContain("reward.open_card_group");
      expect(kinds).not.toContain("reward.take_card");
      expect(kinds).not.toContain("reward.decline_group");
      expect(result.complete).toBe(false);
      expect(result.missing).toContain("required:reward_card_group_qualification");
    }
  });

  it("an emptied reward page maps to proceed but stays with chooser without mandatory proof", () => {
    for (const name of ["managed-reward_claim", "reference-reward_claim"]) {
      const basis = clone(fixture(name));
      const snapshot = basis.snapshot as any;
      snapshot.interaction.content.surface.rewards = [];
      snapshot.bound_actions.actions = snapshot.bound_actions.actions.filter((action: any) => !action.subject_referent_id);
      snapshot.bound_actions.materialized_count = snapshot.bound_actions.total_count = snapshot.bound_actions.actions.length;
      const result = reduce(createSession("e", "managed"), basis);
      expect(result.observation.catalog.map((choice) => choice.kind)).toEqual(["reward.proceed"]);
      expect(result.forced_choice_id).toBeNull();
    }
  });

  it("preserves selector and shop actions while their navigation qualification is pending", () => {
    const enchant = reduce(createSession("e", "reference"), fixture("reference-deck_enchant_selection"));
    const native = (fixture("reference-deck_enchant_selection").snapshot as any).bound_actions.actions.length;
    expect(enchant.observation.catalog.length + enchant.excluded.length).toBe(native);
    for (const item of enchant.excluded) expect(item.rule).toMatch(/^forward_profile:/u);
    const shop = reduce(createSession("e", "managed"), fixture("managed-shop_inventory"));
    const shopNative = (fixture("managed-shop_inventory").snapshot as any).bound_actions.actions.length;
    expect(shop.observation.catalog.length + shop.excluded.length).toBe(shopNative);
  });

  it("retains deck enchant target choice without an uncertified preview confirmation fold", () => {
    for (const name of ["managed-deck_enchant_selection", "reference-deck_enchant_selection"]) {
      const basis = fixture(name);
      const result = reduce(createSession("e", "managed"), basis);
      expect(result.observation.scene.family).toBe("enchant");
      expect((result.observation.scene.content as any).surface.enchantment.definition_id).toBe("SOWN");
      const kinds = new Set(result.observation.catalog.map((choice) => choice.kind));
      expect([...kinds]).toEqual(["enchant.choose"]);
      for (const choice of result.observation.catalog)
        expect(result.lowering[choice.choice_id]!.follow).toEqual([]);
    }
  });

  it("retains native deselect inside an upgrade/enchant selection", () => {
    const basis = clone(fixture("managed-deck_enchant_selection"));
    const actions = (basis.snapshot as any).bound_actions.actions as any[];
    actions.push({ ...actions[0], bound_action_id: "action_deselect_test", verb: "deselect", label: "Deselect" });
    (basis.snapshot as any).bound_actions.materialized_count = actions.length;
    (basis.snapshot as any).bound_actions.total_count = actions.length;
    const result = reduce(createSession("e", "managed"), basis);
    expect(result.excluded).toEqual([]);
    expect(result.observation.catalog.some((choice) => choice.kind === "enchant.deselect")).toBe(true);
    expect(result.observation.catalog.length + result.excluded.length).toBe(actions.length);
  });

  it("keeps an unknown native verb offered as native.* instead of dropping a decision", () => {
    const basis = clone(fixture("managed-map_navigation"));
    const snapshot = basis.snapshot as any;
    snapshot.interaction.kind = "some_future_surface";
    const result = reduce(createSession("e", "managed"), basis);
    expect(result.observation.status).toBe("decision");
    expect(result.observation.catalog.length).toBe(snapshot.bound_actions.actions.length);
  });

  it("marks a failed Read as an incomplete observation (never published as complete)", () => {
    const basis = clone(fixture("reference-combat_turn"));
    basis.reads.forEach((read) => { (read as any).response = null; });
    const result = reduce(createSession("e", "reference"), basis);
    expect(result.complete).toBe(false);
    expect(result.observation.piles.availability).toBe("unknown");
    expect(result.missing.some((item) => item.startsWith("read:"))).toBe(true);
  });

  it("recognises the native result on both Hosts' terminal formats", () => {
    expect(reduce(createSession("e", "managed"), fixture("managed-game_over")).observation.terminal?.outcome).toBe("loss");
    expect(reduce(createSession("e", "reference"), fixture("reference-game_over")).observation.terminal?.outcome).toBe("loss");
    // After the native result no P1 choice is offered; summary navigation is excluded by declared rule.
    for (const name of ["managed-game_over", "reference-game_over"]) {
      const result = reduce(createSession("e", "reference"), fixture(name));
      expect(result.observation.status).toBe("terminal");
      expect(result.observation.catalog).toEqual([]);
      expect(result.excluded.length).toBe((fixture(name).snapshot as any).bound_actions.actions.length);
      for (const item of result.excluded) expect(item.rule).toBe("terminal:post_result_navigation");
    }
  });

  it("never puts native ids, seeds or hidden-order fields into the model view", () => {
    const text = canonicalJson(reduce(createSession("e", "managed"), fixture("managed-combat_turn")).observation);
    expect(text).not.toMatch(/native_ref|bound_action_|hidden_rng":[^\]]*[0-9a-f]{16}/u);
    expect(text).not.toMatch(/"card_[0-9a-f]{12,}"/u);
    // Reward group identities are Host ids too: only their public alias may appear.
    for (const name of ["managed-reward_claim", "reference-reward_claim"]) {
      const reward = canonicalJson(reduce(createSession("e", "managed"), fixture(name)).observation);
      expect(reward).not.toMatch(/"reward_group_[0-9a-f]+"|"reward_[0-9a-f]{12,}"/u);
    }
  });
});
