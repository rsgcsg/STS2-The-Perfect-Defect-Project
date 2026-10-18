import assert from "node:assert/strict";
import test from "node:test";
import {projectManagedCandidateDecision} from "../src/managed-player-environment.mjs";
import {shopRoom} from "./fixtures/managed-shop.mjs";

function inventory() {
  const state=shopRoom();state.decision="shop";state.shop_public=true;state.shop_missing=[];
  state.presentation_owner.stage="inventory";
  const offer=(index)=>({native_ref:`private-entry-${index}`,inventory_index:index,cost:52+index,is_stocked:true,
    visible:true,affordable:true,can_purchase:true,blocked_reason:null});
  state.cards=[{...offer(0),on_sale:true,card:{native_ref:"private-card",id:"REBOOT",name:"Reboot",type:"Skill",cost:"0",
    rarity:"Rare",description:"Draw 4 cards. Exhaust.",is_upgraded:false,public_details:{keywords:[],card_previews:[]},public_details_missing:[]}}];
  state.relics=[{...offer(1),relic:{native_ref:"private-relic",id:"CENTENNIAL_PUZZLE",name:"Puzzle",description:"Draw 3 cards.",keywords:[],card_previews:[]}}];
  state.potions=[{...offer(2),id:"WEAK_POTION",name:"Weak",description:"Apply 3 Weak.",rarity:"Common"}];
  state.card_removal={...offer(3),next_price_increase:25};return state;
}
const identity={runtimeInstanceId:"shop-public",environmentFingerprint:"synthetic",sequence:1};
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
  assert.equal([...projected.reads.values()].filter(read=>read.descriptor.kind==="surface_card"&&read.content.display_context==="shop").length,1);
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
