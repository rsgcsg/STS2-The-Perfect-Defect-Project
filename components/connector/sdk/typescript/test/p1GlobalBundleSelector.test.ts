import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { completeSelector, createSession, reduce } from "../src/p1Global/index.js";

// Synthetic fair-player data with the actual CardBundleSelectionSurface wire shape.
// Slot 35's private sealed basis is exercised separately; raw recordings stay out of Git.
function basis(stage: "choosing" | "preview" = "choosing"): any {
  const b = JSON.parse(readFileSync(new URL("./fixtures/p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));
  const template = b.snapshot.interaction.content.context.player.hand[0];
  const bundles = [0, 1].map(pack => ({ entity_id: `bundle_${pack}`, cards: [0, 1, 2].map(index => ({
    ...structuredClone(template), entity_id: `bundle_card_${pack}_${index}`, definition_id: `PUBLIC_CARD_${pack}_${index}`,
    name: `Pack ${pack} card ${index}`, description: `Public effect ${pack}/${index}`, is_selected: false
  })) }));
  const surface = { kind: "card_bundle_selection", stage, prompt: "Choose a bundle",
    ...(stage === "preview" ? { selected_bundle_entity_id: bundles[0]!.entity_id } : {}),
    selectable_bundle_entity_ids: stage === "choosing" ? bundles.map(pack => pack.entity_id) : [],
    can_confirm: stage === "preview", can_cancel_preview: stage === "preview",
    bundles: stage === "choosing" ? bundles : [bundles[0]] };
  Object.assign(b.snapshot.interaction, { kind: surface.kind, stage,
    content_schema: "sts2.player-environment/surface/card_bundle_selection-1" });
  b.snapshot.interaction.content = { surface, context: { kind: "event", event_id: "NEOW", name: "Neow",
    ancient: true, in_dialogue: false } };
  b.snapshot.reads = b.snapshot.reads.filter((r: any) => r.kind !== "combat_piles");
  b.reads = b.reads.filter((r: any) => r.kind !== "combat_piles");
  b.snapshot.referents.push(...surface.bundles.map(pack => ({ referent_id: pack!.entity_id, kind: "entity", role: "bundle",
    label: "Visible bundle", state: { visible: true, observation_basis: "native_visible_fact" },
    properties_schema: "sts2.player-environment/referent/bundle-1", properties: pack })));
  const action = b.snapshot.bound_actions.actions[0];
  b.snapshot.bound_actions.actions = stage === "choosing"
    ? bundles.map(pack => ({ ...action, bound_action_id: `preview_${pack.entity_id}`, verb: "select",
      subject_referent_id: pack.entity_id, arguments: [], label: "Preview bundle" }))
    : ["confirm", "cancel"].map(verb => ({ ...action, bound_action_id: `${verb}_bundle_0`, verb,
      subject_referent_id: bundles[0]!.entity_id, arguments: [], label: verb }));
  b.snapshot.bound_actions.total_count = b.snapshot.bound_actions.materialized_count = b.snapshot.bound_actions.actions.length;
  return b;
}

const project = (b: any) => reduce(createSession("bundle-regression", "reference"), b);

describe("native two-stage whole-bundle selector", () => {
  it("admits choosing without inventing single-card grid fields and preserves all pack contents", () => {
    const b = basis(); const surface = b.snapshot.interaction.content.surface;
    expect(completeSelector(surface)).toBe(true);
    expect(surface).not.toHaveProperty("cards"); expect(surface).not.toHaveProperty("min_select");
    const r = project(b);
    expect(r.missing).toEqual([]); expect(r.complete).toBe(true);
    expect(r.observation.catalog.map(c => c.kind)).toEqual(["selector.choose", "selector.choose"]);
    const shown = (r.observation.scene.content as any).surface;
    expect(shown.bundles.map((pack: any) => pack.cards.map((card: any) => [card.name, card.description])))
      .toEqual(surface.bundles.map((pack: any) => pack.cards.map((card: any) => [card.name, card.description])));
    expect(shown).not.toHaveProperty("selection_progress");
    for (const choice of r.observation.catalog) {
      expect(shown.bundles.some((pack: any) => pack.entity_id === choice.subject)).toBe(true);
      expect(r.lowering[choice.choice_id]!.first!.verb).toBe("select");
      expect(r.lowering[choice.choice_id]!.follow).toEqual([]);
    }
  });

  it("keeps the exact current confirmation and excludes returning to bundle choices", () => {
    const r = project(basis("preview"));
    expect(r.missing).toEqual([]); expect(r.complete).toBe(true);
    expect(r.observation.catalog).toHaveLength(1);
    expect(r.observation.catalog[0]!.kind).toBe("selector.finish");
    expect(r.lowering[r.observation.catalog[0]!.choice_id]!.first!.bound_action_id).toBe("confirm_bundle_0");
    expect(r.excluded).toEqual([{ bound_action_id: "cancel_bundle_0", verb: "cancel", label: "cancel",
      rule: "forward_profile:selection_append_only" }]);
    expect(r.forced_choice_id).toBeNull(); // A singleton alone never authorizes automatic input.
    expect((r.observation.scene.content as any).surface.bundles[0].cards).toHaveLength(3);
  });

  const invalid: [string, (surface: any) => void][] = [
    ["missing bundle contents", s => { delete s.bundles[0].cards; }],
    ["empty bundle contents", s => { s.bundles[0].cards = []; }],
    ["malformed card", s => { delete s.bundles[0].cards[0].cost; }],
    ["duplicate bundle identity", s => { s.bundles[1].entity_id = s.bundles[0].entity_id; }],
    ["duplicate card identity", s => { s.bundles[1].cards[0].entity_id = s.bundles[0].cards[0].entity_id; }],
    ["foreign selectable bundle", s => { s.selectable_bundle_entity_ids = ["foreign"]; }],
    ["duplicate selectable bundle", s => { s.selectable_bundle_entity_ids.push(s.selectable_bundle_entity_ids[0]); }],
    ["missing control availability", s => { delete s.can_confirm; }],
    ["unknown stage", s => { s.stage = "unknown"; }],
    ["selection before preview", s => { s.selected_bundle_entity_id = s.bundles[0].entity_id; }],
    ["confirmation before preview", s => { s.can_confirm = true; }],
    ["null bundle", s => { s.bundles = [null]; }],
    ["null surface", () => {}]
  ];
  it.each(invalid)("rejects %s instead of laundering it as a complete selector", (name, mutate) => {
    const b = basis(); mutate(b.snapshot.interaction.content.surface);
    if (name === "null surface") b.snapshot.interaction.content.surface = { kind: "card_bundle_selection", bundles: null };
    expect(completeSelector(b.snapshot.interaction.content.surface)).toBe(false);
    const r = project(b); expect(r.complete).toBe(false);
    expect(r.missing).toContain("required:selector:invalid_content");
  });
  it.each(["foreign", null, undefined])("rejects a preview without its exact selected bundle (%s)", selected => {
    const b = basis("preview"); b.snapshot.interaction.content.surface.selected_bundle_entity_id = selected;
    expect(project(b).missing).toContain("required:selector:invalid_content");
  });
});
