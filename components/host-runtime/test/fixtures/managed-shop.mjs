import { combatState } from "./managed-fair-combat.mjs";

export function shopRoom() {
  const state = combatState();
  delete state.hand; delete state.combat_piles;
  return { ...state, decision: "shop_room", room_ref: "private-original-room", shop_ref: "private-original-room", inventory_ref: "private-original-inventory",
    can_open_inventory: true, can_proceed: true,
    presentation_owner: { kind: "managed_merchant_presentation", stage: "room", room_ref: "private-original-room",
      inventory_ref: "private-original-inventory", player_ref: state.player.native_ref } };
}
