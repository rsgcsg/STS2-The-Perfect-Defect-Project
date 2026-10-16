import assert from "node:assert/strict";
import test from "node:test";
import { decodePlayerSnapshot } from "@rsgcsg/sts2-connector-client";
import { ManagedPlayerEnvironmentSession, projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { projectManagedTextMenuV1 } from "../src/managed-text-menu-map.mjs";
import { combatState } from "./fixtures/managed-fair-combat.mjs";

const identity = { runtimeInstanceId: "relic-fixture", environmentFingerprint: "relic-fixture", sequence: 1 };
function state() {
  return { ...combatState(), decision: "relic_choice", selector_ref: "native-relic-selector", stage: "choosing",
    native_relic_complete: true, can_skip: true, relics: ["PAELS_WING", "ANCIENT_TOOTH"].map((id,index) => ({
      index, native_ref: `native-relic-${index}`, id, name: id, description: `Visible ${id} effect.`,
      counter: null, keywords: [], card_previews: [], hover_facts_complete: true })) };
}
function project(value) { return projectManagedCandidateDecision({ state: value, ...identity }); }

test("native relic presentation publishes every original option and exact skip through strict PE/menu", () => {
  const value=state(), result=project(value), snapshot=decodePlayerSnapshot(result.snapshot).data;
  assert.equal(snapshot.completeness.status,"complete");
  assert.equal(snapshot.interaction.kind,"native_boss_relic_selection");
  assert.deepEqual(snapshot.bound_actions.actions.map(action=>action.verb),["select","select","skip"]);
  assert.equal(snapshot.interaction.content.surface.relics.length,2);
  assert.equal(projectManagedTextMenuV1(snapshot).menu_actions.status,"complete");
  for (const reference of [value.selector_ref,...value.relics.map(relic=>relic.native_ref)])
    assert.equal(JSON.stringify(snapshot).includes(reference),false);
  assert.deepEqual([...result.bindings.values()].map(binding=>binding.raw_request.args),[
    {selector_ref:value.selector_ref,relic_ref:value.relics[0].native_ref},
    {selector_ref:value.selector_ref,relic_ref:value.relics[1].native_ref}, {selector_ref:value.selector_ref}]);
});

test("incomplete, duplicate, foreign-display or missing native relic facts publish no input", () => {
  for (const mutate of [value=>{value.native_relic_complete=false;},value=>{value.selector_ref=null;},
    value=>{value.relics=[];},value=>{value.relics[1].native_ref=value.relics[0].native_ref;},
    value=>{delete value.can_skip;},value=>{value.relics[0].description=null;},
    value=>{value.relics[0].hover_facts_complete=false;}]) {
    const value=state();mutate(value);const result=project(value);
    assert.equal(result.snapshot.completeness.status,"partial");
    assert.deepEqual(result.snapshot.bound_actions.actions,[]);
    assert.equal(result.bindings.size,0);
  }
});

test("uncertain native relic delivery is never repeated or replaced with skip", async () => {
  const calls=[];const process={async request(request){calls.push(request);if(request.cmd==="start_run")return state();throw new Error("uncertain native continuation");}};
  const session=new ManagedPlayerEnvironmentSession({process,...identity});
  const snapshot=await session.mount({seed:"RELIC-SYNTHETIC"});const action=snapshot.bound_actions.actions[0];
  const request={requestId:"choose-once",expectedSnapshotId:snapshot.snapshot_id,boundActionId:action.bound_action_id,timeoutMs:1000};
  const receipt=await session.submit(request);
  assert.equal(receipt.delivery,"unknown");assert.equal(receipt.retry.allowed,false);
  await session.submit({...request,requestId:"choose-again"});
  assert.equal(calls.length,2);assert.equal(calls[1].action,"select_native_relic");
});
