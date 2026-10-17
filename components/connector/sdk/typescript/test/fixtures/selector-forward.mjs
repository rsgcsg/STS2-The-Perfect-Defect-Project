import { readFileSync } from "node:fs";
// Codec-complete synthetic producer seam; exact native production is tested in Host.Tests.
export function selectorForwardPair() {
  const outer = JSON.parse(readFileSync(new URL("./p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));
  const s = outer.snapshot;
  const card = structuredClone(outer.reads.find((r) => r.kind === "run_deck").response.content.cards[0]);
  card.is_selected = false;
  const after = { ...structuredClone(card), is_upgraded: true, description: "Native upgraded description" };
  s.interaction.kind = "deck_upgrade_selection";
  s.interaction.stage = "selecting";
  s.interaction.content_schema = "sts2.player-environment/surface/deck_upgrade_selection-1";
  s.interaction.content.context = { kind: "rest_site", room_entity_id: "rest-room" };
  s.interaction.content.surface = {
    kind: "deck_upgrade_selection", stage: "selecting", screen_entity_id: "owner-one", request_entity_id: "task-one",
    cards: [card], min_select: 1, max_select: 1, require_manual_confirmation: false,
    selected_count: 0, selected_card_entity_ids: [], selectable_card_entity_ids: [card.entity_id], deselectable_card_entity_ids: [],
    can_confirm: false, cancelable: true, current_controls: [{ entity_id: "toggle", operation: "toggle_upgrade_view" }],
    selector_request: { entity_id: "task-one", owner_entity_id: "owner-one", family: "deck_upgrade", stage: "selecting",
      selection_model: "native_selected_original_set", add_transition: "native_preview_at_max", min_select: 1, max_select: 1,
      require_manual_confirmation: false, displayed_holder_count: null, selected_entity_ids: [], can_finish_now: false,
      can_decline_now: true, mechanics_qualified: true, preview_information_complete: true, preview_projection_qualified: true,
      capability_revision: "sts2.native-selector-public-facts/1", missing: [] },
    preview_options: [{ subject_entity_id: card.entity_id, before: structuredClone(card), after,
      display_context: "native_deck_upgrade_after", after_availability: "available", missing: [] }], enchantment_summary: null
  };
  const ref = s.referents[0];
  s.referents = [
    { ...ref, referent_id: card.entity_id, role: "card" },
    ...["toggle", "confirm", "cancel"].map(id => ({ ...ref, referent_id: id, role: "selector_control" }))
  ];
  const action = s.bound_actions.actions[0];
  s.bound_actions.actions = [
    { ...action, bound_action_id: "select-original", verb: "select", subject_referent_id: card.entity_id, arguments: [], label: "Select" },
    { ...action, bound_action_id: "inspect", verb: "open", subject_referent_id: card.entity_id, arguments: [], label: "Inspect" },
    { ...action, bound_action_id: "toggle-binding", verb: "open", subject_referent_id: "toggle", arguments: [], label: "Toggle" }
  ];
  s.bound_actions.total_count = s.bound_actions.materialized_count = s.bound_actions.actions.length;
  // Piles are irrelevant outside combat and are omitted by this synthetic provider.
  s.reads = s.reads.filter((r) => r.kind !== "combat_piles");
  outer.reads = outer.reads.filter((r) => r.kind !== "combat_piles");
  const preview = structuredClone(outer);
  const p = preview.snapshot.interaction.content.surface;
  p.stage = preview.snapshot.interaction.stage = p.selector_request.stage = "preview";
  p.cards[0].is_selected = p.preview_options[0].before.is_selected = true;
  p.selected_card_entity_ids = p.selector_request.selected_entity_ids = [card.entity_id];
  p.selected_count = 1; p.selectable_card_entity_ids = []; p.can_confirm = true;
  p.selector_request.can_finish_now = true; p.selector_request.can_decline_now = false;
  p.current_controls = [{ entity_id: "confirm", operation: "confirm" }, { entity_id: "cancel", operation: "cancel_preview" }];
  preview.snapshot.bound_actions.actions = [
    { ...action, bound_action_id: "exact-confirm", verb: "confirm", subject_referent_id: "confirm", arguments: [], label: "Confirm" },
    { ...action, bound_action_id: "exact-cancel", verb: "cancel", subject_referent_id: "cancel", arguments: [], label: "Back" }
  ];
  preview.snapshot.bound_actions.total_count = preview.snapshot.bound_actions.materialized_count = 2;
  return { outer, preview };
}
