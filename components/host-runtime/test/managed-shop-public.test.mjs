import assert from "node:assert/strict";
import test from "node:test";
import {projectManagedCandidateDecision} from "../src/managed-player-environment.mjs";
import {shopRoom} from "./fixtures/managed-shop.mjs";

function inventory() {
  const state=shopRoom();state.decision="shop";state.shop_public=true;state.shop_missing=[];
  state.presentation_owner.stage="inventory";
  const offer=(index)=>({native_ref:`private-entry-${index}`,slot_ref:`private-slot-${index}`,inventory_index:index,cost:52+index,is_stocked:true,
    visible:true,affordable:true,can_purchase:true,blocked_reason:null});
  state.cards=[{...offer(0),on_sale:true,card:{native_ref:"private-card",id:"REBOOT",name:"Reboot",type:"Skill",cost:"0",
    rarity:"Rare",description:"Draw 4 cards. Exhaust.",is_upgraded:false,public_details:{keywords:[],card_previews:[]},public_details_missing:[]}}];
  state.relics=[{...offer(1),relic:{native_ref:"private-relic",id:"CENTENNIAL_PUZZLE",name:"Puzzle",description:"Draw 3 cards.",keywords:[],card_previews:[]}}];
  state.potions=[{...offer(2),id:"WEAK_POTION",name:"Weak",description:"Apply 3 Weak.",rarity:"Common"}];
  state.card_removal={...offer(3),next_price_increase:25};return state;
}
const identity={runtimeInstanceId:"shop-public",environmentFingerprint:"synthetic",sequence:1};

test("shop relic counter preserves native zero, nonzero and absent display separately", () => {
  for (const counter of [0, 2, null, undefined]) {
    const state = inventory();
    if (counter !== undefined) state.relics[0].relic.counter = counter;
    const projection = projectManagedCandidateDecision({ state, ...identity });
    assert.equal(projection.snapshot.completeness.status, "complete");
    const relic = projection.snapshot.interaction.content.surface.relics[0].relic;
    assert.equal(Object.hasOwn(relic, "counter"), counter != null);
    if (counter != null) assert.equal(relic.counter, counter);
  }
});
test("shop keeps original product instances and exact entry prices while publishing resolved nested facts",()=>{
  const state=inventory();const projected=projectManagedCandidateDecision({state,...identity});
  const surface=projected.snapshot.interaction.content.surface;
  assert.equal(projected.snapshot.completeness.status,"complete");
  assert.deepEqual([surface.cards[0],surface.relics[0],surface.potions[0],surface.card_removal].map(offer=>offer.inventory_index),[0,1,2,3]);
  assert.deepEqual([surface.cards[0],surface.relics[0],surface.potions[0],surface.card_removal].map(offer=>offer.price),[52,53,54,55]);
  assert.equal(surface.cards[0].card.definition_id,"REBOOT");assert.equal(surface.cards[0].card.cost,"0");
  assert.equal(surface.cards[0].on_sale,true);assert.equal(surface.relics[0].relic.definition_id,"CENTENNIAL_PUZZLE");
  assert.equal(surface.potions[0].definition_id,"WEAK_POTION");
  const binding=[...projected.bindings.values()].find(value=>value.raw_request.action==="buy_card");
  assert.deepEqual(binding.raw_request.args,{entry_ref:"private-entry-0"});
  assert.equal(JSON.stringify(projected.snapshot).includes("private-"),false);
  assert.equal([...projected.reads.values()].filter(read=>read.descriptor.kind==="surface_card"&&read.content.display_context==="shop_offer").length,2);
});
test("a changed merchant owner or incomplete native product render does not admit a complete shop basis",()=>{
  const owner=inventory();owner.presentation_owner.inventory_ref="foreign";
  const projected=projectManagedCandidateDecision({state:owner,...identity});
  assert.equal(projected.snapshot.completeness.status,"partial");assert.equal(projected.bindings.size,0);
  const absent=inventory();delete absent.shop_public;
  const unavailable=projectManagedCandidateDecision({state:absent,...identity});
  assert.equal(unavailable.snapshot.completeness.status,"partial");assert.equal(unavailable.bindings.size,0);
  const partial=inventory();partial.shop_missing=["native_card_render_failed"];
  assert.equal(projectManagedCandidateDecision({state:partial,...identity}).snapshot.completeness.status,"partial");
});

test("closed shop exposes snapshot-bound inspectable catalog without purchase authority",()=>{
  const state=inventory();state.decision="shop_room";state.presentation_owner.stage="room";
  for(const entry of [...state.cards,...state.relics,...state.potions,state.card_removal]) {
    entry.can_purchase=false;entry.blocked_reason="ui_control_disabled";
  }
  const projected=projectManagedCandidateDecision({state,...identity});
  const catalog=[...projected.reads.values()].find(read=>read.descriptor.kind==="shop_catalog");
  assert.equal(projected.snapshot.completeness.status,"complete");
  assert.equal(catalog.content.access_state,"inventory_closed_open_to_inspect");
  assert.equal(catalog.descriptor.ordering_semantics,"fixed_ui_slots");
  assert.equal([...projected.reads.values()].filter(read=>!["run_deck","map","combat_piles"].includes(read.descriptor.kind))[0].descriptor.kind,"shop_catalog");
  assert.deepEqual(catalog.content.cards.map(offer=>[offer.inventory_index,offer.price,offer.can_purchase,offer.blocked_reason]),[[0,52,false,"ui_control_disabled"]]);
  assert.deepEqual([...projected.bindings.values()].map(value=>value.raw_request.action),["open_shop_inventory","leave_shop"]);
  const details=[...projected.reads.values()].filter(read=>read.descriptor.kind==="surface_card"&&read.content.display_context==="shop_offer");
  assert.equal(details.length,1);assert.equal(details[0].content.parent_read_id,catalog.descriptor.read_id);
  assert.equal(details[0].content.display_owner_entity_id,catalog.content.cards[0].entity_id);
  assert.equal(JSON.stringify(projected.snapshot).includes("private-"),false);
  const bad=structuredClone(state);bad.cards[0].can_purchase=true;
  assert.equal(projectManagedCandidateDecision({state:bad,...identity}).snapshot.completeness.status,"partial");
});

test("catalog slot bindings are stable original presentation objects and duplicate slots are not admitted",()=>{
  const state=inventory(), aliases=new Map();
  const first=projectManagedCandidateDecision({state,...identity,publicEntityAliases:aliases});
  const next=projectManagedCandidateDecision({state,...identity,sequence:2,publicEntityAliases:aliases});
  const catalog=p=>[...p.reads.values()].find(read=>read.descriptor.kind==="shop_catalog").content;
  assert.equal(catalog(first).cards[0].slot_entity_id,catalog(next).cards[0].slot_entity_id);
  assert.notEqual(catalog(first).cards[0].slot_entity_id,catalog(first).relics[0].slot_entity_id);
  const bad=structuredClone(state);bad.relics[0].slot_ref=bad.cards[0].slot_ref;
  assert.equal(projectManagedCandidateDecision({state:bad,...identity}).snapshot.completeness.status,"partial");
});
