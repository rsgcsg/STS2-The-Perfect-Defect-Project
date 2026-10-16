import { combatState } from "./managed-fair-combat.mjs";

// Synthetic native DTO; does not establish native gameplay qualification.
export function selector({ choose = false, skip = false, manual = false, selected = false } = {}) {
  const state = combatState();
  const card = (ref, name = "Strike") => ({ native_ref: ref, id: name.toUpperCase(), name,
    cost: "1", type: "Attack", rarity: "Basic", description: "Deal 6 damage.", target_type: "AnyEnemy",
    is_upgraded: false, keywords: [], card_previews: [], render_complete: true,
    is_selected: false, is_selectable: !selected, is_deselectable: false });
  const cards = [card("native-card-a"), card("native-card-b")];
  if (selected) Object.assign(cards[0], { is_selected: true, is_deselectable: manual });
  return { ...state, decision: "native_card_select", selector_ref: "native-selector",
    origin: choose ? "choose_card" : "combat_pile", stage: "choosing", prompt: "Choose a card.",
    min_select: choose && skip ? 0 : 1, max_select: 1, selected_refs: selected ? [cards[0].native_ref] : [],
    cancelable: false, can_skip: skip, require_manual_confirmation: manual, can_confirm: manual && selected,
    display_pile: choose ? "none" : "discard", native_selector_complete: true, native_selector_missing: [], cards,
    combat_context: { round: 1, turn_owner: "player", player_ref: state.player.native_ref,
      hp: 80, max_hp: 80, block: 3, energy: 2, max_energy: 3, stars: 0,
      draw_pile_count: 2, discard_pile_count: 2, exhaust_pile_count: 0, orb_slots: 0,
      statuses: [], orbs: [], companions: [], enemies: [{ native_ref: "native-enemy-current", id: "MAWLER",
        name: "Mawler", hp: 7, max_hp: 20, block: 0, health_bar_visible: true, health_display: "Normal",
        statuses: [], intents: [] }] } };
}
