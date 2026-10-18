import { selector as nativeSelector } from "./managed-native-selector.mjs";
import { combatState } from "./managed-fair-combat.mjs";

// Synthetic same-capture DTO; native callback and widget qualification require
// the independent real Host canary, never this projection test.
export function publicSelector({ preview = false, family = "deck_upgrade" } = {}) {
  const state = family === "combat_pile" ? nativeSelector() : combatState();
  delete state.hand;
  if (family !== "combat_pile") delete state.combat_piles;
  const card = (ref, upgraded) => ({ entity_id: ref, definition_id: "STRIKE", name: upgraded ? "Strike+" : "Strike",
    type: "Attack", cost: "1", star_cost: null, description: upgraded ? "Deal 9 damage." : "Deal 6 damage.",
    rarity: "Basic", is_upgraded: upgraded, is_selected: false, target_type: "AnyEnemy", can_play: null,
    keywords: [], card_previews: [] });
  const before = card("private-card-original", false);
  before.is_selected = preview;
  const after = family === "deck_upgrade" ? card("private-card-original", true)
    : family === "deck_enchant" ? card("private-card-original", false) : null;
  const request = { entity_id: "private-original-task", owner_entity_id: "private-selector-owner", family,
    stage: preview ? "preview" : "selecting", selection_model: "native_selected_original_set",
    add_transition: family === "combat_pile" ? "native_completion_with_display_count_clamp" : family === "simple" ? "native_completion_at_max" : "native_preview_at_max", min_select: 1, max_select: 1,
    require_manual_confirmation: false, displayed_holder_count: null,
    selected_entity_ids: preview ? [before.entity_id] : [], can_finish_now: preview,
    can_decline_now: false, mechanics_qualified: true, preview_information_complete: true,
    preview_projection_qualified: true, capability_revision: "sts2.native-selector-public-facts/1", missing: [] };
  return { ...state, decision: family === "combat_pile" ? "native_card_select" : family === "simple" ? "simple_card_select" : family === "deck_upgrade" ? "deck_upgrade_select" : "deck_card_select",
    selector_ref: request.owner_entity_id, origin: family === "combat_pile" ? "combat_pile" : family === "deck_enchant" || family === "deck_transform" ? family : "deck_generic",
    stage: request.stage, prompt: "Choose a card.",
    cards: [{ ...before, index: 0, native_ref: before.entity_id, is_selectable: !preview, is_deselectable: false }],
    selected_refs: request.selected_entity_ids, min_select: 1, max_select: 1,
    require_manual_confirmation: false, cancelable: false,
    selector_public: { selector_request: request, preview_options: [{ subject_entity_id: before.entity_id,
      before, after, display_context: family === "deck_upgrade" ? "native_deck_upgrade_after"
        : family === "deck_enchant" ? "native_single_enchantment_after" : "native_original_only",
      after_availability: after === null ? "not_applicable" : "available", before_missing: [], after_missing: [], missing: [] }],
    enchantment_summary: family === "deck_enchant" ? { title: "Sharp", description: "Deal 2 more damage.", amount: 2 } : null,
    current_controls: preview ? [{ operation: family === "deck_upgrade" ? "confirm" : "confirm_selection" },
      { operation: "cancel_preview" }] : [] } };
}
