// Synthetic DTOs with the current RunSimulator decision shape. Native
// CardModel rendering/execution is not represented by this shared fixture.
function card(nativeRef, overrides = {}) {
  return {
    native_ref: nativeRef, index: 0, id: "CARD.STRIKE", name: "Strike", type: "Attack",
    cost: "1", rarity: "Basic", is_upgraded: false, description: "Deal 6 damage.",
    ...overrides
  };
}

export function combatState() {
  const deck = [card("native-101"), card("native-102"),
    card("native-103", { cost: "0", is_upgraded: true,
      enchantment: "Sharp", enchantment_id: "SHARP", enchantment_description: "Deal 2 more damage.",
      enchantment_amount: 2 }),
    card("native-104", { id: "CARD.DEFEND", name: "Defend", type: "Skill", description: "Gain 5 Block." })];
  return {
    type: "decision", decision: "combat_play",
    context: { act: 1, act_index: 0, act_definition_id: "OVERGROWTH", act_name: "Overgrowth",
      floor: 2, total_floor: 2, ascension: 0, room_type: "Combat",
      bosses: [{ id: "VANTOM_BOSS", name: "Vantom", order: 0 }], modifiers: [] },
    combat_hud_complete: true, potion_states: [], stars: null, encounter_type: "normal", round: 1, turn_owner: "player", is_play_phase: true,
    energy: 3, max_energy: 3, draw_pile_count: 4, discard_pile_count: 2,
    exhaust_pile_count: 1, orb_slots: 0, orbs: [], companions: [], player_statuses: [],
    player: { native_ref: "native-1", name: "Ironclad", character_id: "IRONCLAD",
      hp: 80, max_hp: 80, block: 0, gold: 99, max_potion_slots: 3, relics: [{ native_ref: "native-401", id: "HAPPY_FLOWER",
        name: "Happy Flower", description: "Every three turns, gain 1 Energy.", counter: 2,
        keywords: [], card_previews: [], hover_facts_complete: true }], potions: [], deck },
    hand: [card("native-201", { can_play: true, target_type: "AnyEnemy",
      valid_target_refs: ["native-301", "native-302"] }),
    card("native-202", { can_play: true, target_type: "AnyEnemy",
      valid_target_refs: ["native-301", "native-302"] })],
    enemies: [301, 302].map((ref, index) => ({
      index, native_ref: `native-${ref}`, combat_id: ref, id: "MONSTER.CULTIST", name: "Cultist",
      hp: 10, max_hp: 10, block: 0, health_bar_visible: true, hp_display_mode: "normal", hp_numbers_available: true, statuses: [], intents: []
    })),
    combat_piles: [
      { pile: "draw", ordering_semantics: "unordered_multiset",
        cards: deck.map((value, index) => ({ ...value, index, hidden_card_state: `secret-${index}` })) },
      { pile: "discard", ordering_semantics: "unordered_multiset", cards: [deck[3], deck[0]] },
      { pile: "exhaust", ordering_semantics: "unordered_multiset", cards: [deck[2]] }
    ],
    hidden_rng: "unexposed-a", internal_counter: 50
  };
}

export function renamePrivateCounters(value) {
  if (Array.isArray(value)) return value.map(renamePrivateCounters);
  if (value != null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
      renamePrivateCounters(item)]));
  }
  return typeof value === "string" && /^native-\d+$/u.test(value)
    ? `native-${Number(value.slice(7)) + 9000}` : value;
}
