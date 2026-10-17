import { combatState } from "./managed-fair-combat.mjs";

// Synthetic same-capture DTO; native callback and widget qualification require
// the independent real Host canary, never this projection test.
export function publicSelector({ preview = false, family = "deck_upgrade" } = {}) {
  const state = combatState();
  delete state.hand;
  delete state.combat_piles;
  const card = (ref, upgraded) => ({ entity_id: ref, definition_id: "STRIKE", name: upgraded ? "Strike+" : "Strike",
    type: "Attack", cost: "1", star_cost: null, description: upgraded ? "Deal 9 damage." : "Deal 6 damage.",
    rarity: "Basic", is_upgraded: upgraded, is_selected: false, target_type: "AnyEnemy", can_play: null,
    keywords: [], card_previews: [] });
  const before = card("private-card-original", false);
  before.is_selected = preview;
  const after = family === "deck_upgrade" ? card("private-card-original", true) : null;
  const request = { entity_id: "private-original-task", owner_entity_id: "private-selector-owner", family,
    stage: preview ? "preview" : "selecting", selection_model: "native_selected_original_set",
    add_transition: "native_preview_at_max", min_select: 1, max_select: 1,
    require_manual_confirmation: false, displayed_holder_count: null,
    selected_entity_ids: preview ? [before.entity_id] : [], can_finish_now: preview,
    can_decline_now: false, mechanics_qualified: true, preview_information_complete: true,
    preview_projection_qualified: true, capability_revision: "sts2.native-selector-public-facts/1", missing: [] };
  return { ...state, decision: family === "deck_upgrade" ? "deck_upgrade_select" : "deck_card_select",
    selector_ref: request.owner_entity_id, origin: "deck_generic", stage: request.stage, prompt: "Choose a card.",
    cards: [{ ...before, index: 0, native_ref: before.entity_id, is_selectable: !preview, is_deselectable: false }],
    selected_refs: request.selected_entity_ids, min_select: 1, max_select: 1,
    require_manual_confirmation: false, cancelable: false,
    selector_public: { selector_request: request, preview_options: [{ subject_entity_id: before.entity_id,
      before, after, display_context: family === "deck_upgrade" ? "native_deck_upgrade_after" : "native_original_only",
      after_availability: after === null ? "not_applicable" : "available", before_missing: [], after_missing: [], missing: [] }],
    enchantment_summary: null, current_controls: preview ? [{ operation: family === "deck_upgrade" ? "confirm" : "confirm_selection" },
      { operation: "cancel_preview" }] : [] } };
}
