import assert from "node:assert/strict";
import test from "node:test";
import {projectManagedCandidateDecision} from "../src/managed-player-environment.mjs";
import {publicSelector} from "./fixtures/managed-selector-public.mjs";

test("actual Byrdpip hover cards retain an owner-bound identity across captures while replacement and content changes stay visible",()=>{
  const state=publicSelector();
  state.player.relics.push({native_ref:"private-byrdpip",id:"BYRDPIP",name:"异鸟宝宝",description:"拾起时，获得一张异鸟扑击。",
    keywords:[],hover_facts_complete:true,card_previews:[{id:"BYRD_SWOOP",name:"异鸟扑击",type:"Attack",cost:"0",star_cost:null,
      rarity:"Event",description:"造成14点伤害。",is_upgraded:false}]});
  state.context.modifiers=[{native_ref:"private-modifier",id:"SYNTHETIC_MODIFIER",name:"Modifier",description:"Public modifier.",
    keywords:[],card_previews:structuredClone(state.player.relics.at(-1).card_previews),hover_facts_complete:true}];
  const aliases=new Map();
  const capture=(value,sequence)=>projectManagedCandidateDecision({state:value,sequence,runtimeInstanceId:"hover-id",environmentFingerprint:"test",publicEntityAliases:aliases});
  const hover=value=>value.snapshot.persistent.content.player.relics.at(-1).card_previews[0];
  const first=capture(state,1),second=capture(state,2);
  for (const key of ["can_play", "target_type", "unplayable_reason"])
    assert.equal(Object.hasOwn(hover(first), key), false);
  assert.deepEqual(first.snapshot.persistent,second.snapshot.persistent);
  assert.notEqual(first.snapshot.persistent.content.run.modifiers[0].card_previews[0].entity_id,hover(first).entity_id);
  assert.equal(JSON.stringify(first.snapshot).includes("private-"),false);
  assert.equal(hover(first).entity_id,hover(second).entity_id);
  const replaced=structuredClone(state);replaced.player.relics.at(-1).native_ref="private-replacement-byrdpip";
  assert.notEqual(hover(first).entity_id,hover(capture(replaced,3)).entity_id);
  const changed=structuredClone(state);changed.player.relics.at(-1).card_previews[0].description="造成15点伤害。";
  const changedHover=hover(capture(changed,4));
  assert.equal(changedHover.entity_id,hover(first).entity_id);
  assert.notDeepEqual(changedHover,hover(first));
  assert.equal(changedHover.description,"造成15点伤害。");
});
