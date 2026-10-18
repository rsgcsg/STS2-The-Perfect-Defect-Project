import { combatState } from "./managed-fair-combat.mjs";

export function shopRoom() {
  const state = combatState();
  delete state.hand; delete state.combat_piles;
  return { ...state, decision: "shop_room", room_ref: "private-original-room", shop_ref: "private-original-room", inventory_ref: "private-original-inventory",
    can_open_inventory: true, can_proceed: true,
    presentation_owner: { kind: "managed_merchant_presentation", stage: "room", room_ref: "private-original-room",
      inventory_ref: "private-original-inventory", player_ref: state.player.native_ref } };
}


// Current pinned native DTO shape; no legacy flattened shop fallback.
export function shopInventory() {
  const state=shopRoom();
  const offer=(native_ref,inventory_index,cost,can_purchase=true)=>({ native_ref,inventory_index,cost,
    is_stocked:true,visible:true,affordable:can_purchase,can_purchase,blocked_reason:can_purchase?null:"insufficient_gold" });
  return { ...state,decision:"shop",shop_public:true,shop_missing:[],
    presentation_owner:{...state.presentation_owner,stage:"inventory"},
    cards:[{...offer("card-entry-a",0,50),on_sale:false,card:{native_ref:"native-shop-card",id:"POMMEL_STRIKE",name:"Pommel Strike",
      type:"Attack",rarity:"Common",cost:"1",description:"Deal 9 damage. Draw 1 card.",is_upgraded:false,
      public_details:{keywords:[],card_previews:[]},public_details_missing:[]}}],
    relics:[{...offer("relic-entry-a",1,150,false),relic:{native_ref:"native-shop-relic",id:"BAG_OF_PREPARATION",name:"Bag",
      description:"Draw 2 cards.",keywords:[],card_previews:[]}}],
    potions:[{...offer("potion-entry-a",2,50),id:"FIRE_POTION",name:"Fire Potion",description:"Deal 20 damage.",rarity:"Common"}],
    card_removal:{...offer("removal-a",3,75),next_price_increase:25} };
}
