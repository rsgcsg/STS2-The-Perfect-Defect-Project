import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { createSession, reduce, type P1Basis } from "../src/p1Global/index.js";

function example(preview: unknown = { status: "available", card: { entity_id: null,
  definition_id: "DEFEND_DEFECT", name: "Defend+", type: "Skill", cost: "1", description: "Gain 8 Block.",
  rarity: "Basic", is_upgraded: true, is_selected: false, target_type: "Self", keywords: [], card_previews: [] } }) {
  const basis = JSON.parse(readFileSync(new URL("./fixtures/p1-global/synthetic-complete-combat.json", import.meta.url), "utf8")) as P1Basis;
  const snapshot = basis.snapshot as any, card = snapshot.interaction.content.context.player.hand[0];
  const readId = "read:surface_card:upgrade-fixture";
  const listed = { ...snapshot.reads[0], read_id: readId, kind: "surface_card", target_referent_id: card.entity_id,
    content_schema: "sts2.player-environment/read/surface_card-1", ordering_semantics: "single_entity" };
  const response = { ...(basis.reads[0]!.response as any), read_id: readId, kind: "surface_card",
    target_referent_id: card.entity_id, content_schema: listed.content_schema, ordering_semantics: listed.ordering_semantics,
    content: { ...card, public_details_revision: "2", display_context: "deck", parent_read_id: snapshot.reads[0].read_id,
      display_owner_entity_id: null, keywords: [], card_previews: [], upgrade_preview: preview } };
  snapshot.reads.push(listed);
  (basis as any).reads.push({ read_id: readId, kind: "surface_card", response, error: null });
  return basis;
}

describe("public card detail revision 2 upgrade preview", () => {
  it("retains the native display-only upgrade and leaves the source and native action catalog intact", () => {
    const basis = example(), original = JSON.stringify(basis);
    const result = reduce(createSession("preview", "reference"), basis);
    expect(result.complete).toBe(true);
    const detail = result.observation.extra_reads.find(r => r.kind === "surface_card")!.content as any;
    expect(detail.public_details_revision).toBe("2");
    expect(detail.upgrade_preview.card.description).toBe("Gain 8 Block.");
    expect(detail.upgrade_preview.card.entity_id).toBeNull();
    expect(Object.keys(result.lowering)).toHaveLength((basis.snapshot as any).bound_actions.actions.length);
    expect(JSON.stringify(basis)).toBe(original);
  });
  it("accepts an explicit native not-upgradable result", () => {
    expect(reduce(createSession("preview", "reference"), example({ status: "not_applicable", card: null })).complete).toBe(true);
  });
  it.each([null, undefined, {}, { status: "unavailable", card: null }, { status: "available", card: null }])(
    "does not call an incomplete revision 2 preview complete: %s", preview => {
      const basis = example(null);
      ((basis.reads.at(-1)!.response as any).content).upgrade_preview = preview;
      const result = reduce(createSession("preview", "reference"), basis);
      expect(result.complete).toBe(false);
      expect(result.missing.join("\n")).toContain("upgrade_preview_incomplete");
    });
});
