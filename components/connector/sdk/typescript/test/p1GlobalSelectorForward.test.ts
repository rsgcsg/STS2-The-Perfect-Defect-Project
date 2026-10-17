import { describe, expect, it } from "vitest";
import { createSession, reduce, resolveFollowBasis, type P1Basis } from "../src/p1Global/index.js";
import { completeSelectorFacts } from "../src/p1Global/selectorForward.js";

import { selectorForwardPair as pair } from "./fixtures/selector-forward.mjs";

const project = (basis: P1Basis) => reduce(createSession("selector-test", "reference"), basis);
const plan = (basis: P1Basis) => {
  const reduced = project(basis);
  const choice = reduced.observation.catalog.find(c => c.kind === "upgrade.choose")!;
  return reduced.lowering[choice.choice_id]!;
};

describe("same-request forward selector intention", () => {
  it("offers the chosen original with full after information and follows the exact native confirmation", () => {
    const { outer, preview } = pair();
    expect(completeSelectorFacts(outer.snapshot.interaction.content.surface)).toBe(true);
    const r = project(outer);
    expect(r.missing).toEqual([]); expect(r.complete).toBe(true);
    expect(r.observation.catalog.map(c => c.kind)).toEqual(["upgrade.choose"]);
    expect(r.excluded.map(e => e.rule)).toEqual([
      "forward_profile:complete_selector_public_presentations", "forward_profile:complete_selector_public_presentations"]);
    const low = plan(outer);
    expect(low.first?.bound_action_id).toBe("select-original");
    expect(resolveFollowBasis(low.follow[0]!, low, preview)?.bound_action_id).toBe("exact-confirm");
    const json = JSON.stringify(r.observation.scene);
    expect(json).not.toContain("owner-one"); expect(json).not.toContain("task-one");
  });
  it.each(["owner", "task", "selected", "roster", "after", "world", "context", "new-information", "control", "duplicate"])(
    "returns to the Agent on %s drift", change => {
      const { outer, preview } = pair(); const low = plan(outer);
      const s = preview.snapshot.interaction.content.surface;
      if (change === "owner") s.screen_entity_id = s.selector_request.owner_entity_id = "other-owner";
      if (change === "task") s.request_entity_id = s.selector_request.entity_id = "other-task";
      if (change === "selected") { s.selected_card_entity_ids = s.selector_request.selected_entity_ids = []; s.selected_count = 0; s.cards[0].is_selected = s.preview_options[0].before.is_selected = false; }
      if (change === "roster") s.cards.push(structuredClone(s.cards[0]));
      if (change === "after") s.preview_options[0].after.description += " new";
      if (change === "world") preview.snapshot.persistent.content.player.gold++;
      if (change === "context") preview.snapshot.interaction.content.context.new_fact = true;
      if (change === "new-information") s.new_fact = true;
      if (change === "control") s.current_controls[0].entity_id = "different-button";
      if (change === "duplicate") { preview.snapshot.bound_actions.actions.push({ ...preview.snapshot.bound_actions.actions[0], bound_action_id: "duplicate" }); preview.snapshot.bound_actions.total_count = preview.snapshot.bound_actions.materialized_count = 3; }
      expect(resolveFollowBasis(low.follow[0]!, low, preview)).toBeNull();
    });
  it("keeps browsing and confirmation explicit on legacy or incomplete producer facts", () => {
    const { outer } = pair();
    delete outer.snapshot.interaction.content.surface.selector_request;
    // Legacy producer's original inspection verb is open.
    outer.snapshot.bound_actions.actions[1].verb = "open";
    outer.snapshot.bound_actions.actions[2].verb = "activate";
    delete outer.snapshot.bound_actions.actions[2].subject_referent_id;
    const r = project(outer);
    expect(r.observation.catalog.map(c => c.kind)).toContain("upgrade.preview");
    expect(plan(outer).follow).toEqual([]);
    const broken = pair().outer; broken.snapshot.interaction.content.surface.preview_options[0].after = null;
    expect(project(broken).missing).toContain("required:selector:public_facts_incomplete");
  });
  it("keeps set selection decisions explicit until the actual native preview transition", () => {
    const { outer } = pair(); const s = outer.snapshot.interaction.content.surface;
    s.max_select = s.selector_request.max_select = 2;
    const second = structuredClone(s.cards[0]); second.entity_id = "second-original";
    const option = structuredClone(s.preview_options[0]);
    option.subject_entity_id = option.before.entity_id = option.after.entity_id = second.entity_id;
    s.cards.push(second); s.preview_options.push(option); s.selectable_card_entity_ids.push(second.entity_id);
    expect(project(outer).complete).toBe(true);
    expect(plan(outer).follow).toEqual([]);
  });
  it("compares full same-snapshot detail instances but excludes their transport locators", () => {
    const { outer, preview } = pair();
    for (const [basis, locator] of [[outer, "before-parent-read"], [preview, "after-parent-read"]] as const) {
      const card = basis.snapshot.interaction.content.surface.cards[0];
      const listed = { ...basis.snapshot.reads[0], read_id: "selector-card-detail", kind: "surface_card",
        content_schema: "sts2.player-environment/read/surface_card-1", target_referent_id: card.entity_id };
      const response = { ...basis.reads[0].response, read_id: listed.read_id, kind: listed.kind,
        content_schema: listed.content_schema, target_referent_id: listed.target_referent_id, content: { ...structuredClone(card),
        public_details_revision: "1", display_context: "selector_original:deck_upgrade", parent_read_id: locator,
        display_owner_entity_id: "owner-one", keywords: [], card_previews: [] } };
      basis.snapshot.reads.push(listed); basis.reads.push({ read_id: listed.read_id, kind: listed.kind, response, error: null });
    }
    const low = plan(outer);
    expect(resolveFollowBasis(low.follow[0]!, low, preview)?.bound_action_id).toBe("exact-confirm");
    preview.reads.at(-1).response.content.keywords.push({ name: "New", description: "Newly available fact" });
    expect(resolveFollowBasis(low.follow[0]!, low, preview)).toBeNull();
  });
});
