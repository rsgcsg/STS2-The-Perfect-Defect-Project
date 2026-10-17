import { it, expect } from "vitest";
import { mkdtemp, writeFile, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
// The actual Host service/actor and Managed projection, with only native process input synthetic.
// @ts-expect-error Host owner uses native ESM, consumed here without a TS reimplementation.
import { startManagedHostService } from "../../host-runtime/src/managed-host-service.mjs";
// @ts-expect-error same original Host module.
import { ManagedPlayerEnvironmentSession } from "../../host-runtime/src/managed-player-environment.mjs";
import { ManagedServicePolicyClient } from "../src/managed.js";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";
it("runs real Managed service HTTP → original same-actor capture → NDJSON → token/epoch action → release",async()=>{
 let terminal=false, actions=0;
 const decision=()=>terminal?{type:"decision",decision:"game_over",victory:false,context:{act:1,act_index:0,act_definition_id:"OVERGROWTH",act_name:"Overgrowth",floor:1,total_floor:1,ascension:0,bosses:[],modifiers:[]},player:{name:"The Ironclad",character_id:"IRONCLAD",hp:0,max_hp:80,gold:99,native_ref:"player",max_potion_slots:3,relics:[],potions:[],deck:[]}}:{
  type:"decision",decision:"map_select",context:{act:1,act_index:0,act_definition_id:"OVERGROWTH",act_name:"Overgrowth",floor:0,total_floor:0,ascension:0,bosses:[],modifiers:[]},
  choices:[{col:3,row:1,type:"Monster",native_ref:"point",children:[]}],visible_map:{type:"map",rows:[[{col:3,row:0,type:"Monster",visited:true,children:[{col:3,row:1}]}]],boss:{col:3,row:16,type:"Boss"},current_coord:{col:3,row:0}},
  player:{name:"The Ironclad",character_id:"IRONCLAD",hp:80,max_hp:80,gold:99,native_ref:"player",max_potion_slots:3,relics:[],potions:[],deck:[]}};
 const process={async request(value:any){if(value.cmd==="start_run")return decision();if(value.cmd==="get_map")return decision().visible_map;if(value.cmd==="run_identity")return{type:"run_identity",active:true,seed:"SEED"};if(value.cmd==="action"){actions++;terminal=true;return decision();}throw Error("unexpected:"+value.cmd);},async stop(){return{code:0};}};
 const build={upstream_revision:"upstream",source_patch_sha256:"f".repeat(64),artifact_sha256:"1".repeat(64),artifact_mvid:"mvid",original_sts2_sha256:"2".repeat(64),runtime_sts2_sha256:"3".repeat(64)};
 const host={package_name:"@rsgcsg/sts2-host-runtime",version:"1",distribution_kind:"git_checkout",source_revision:"b".repeat(40),component_tree_revision:"c".repeat(40),source_digest_sha256:"4".repeat(64)};
 const session=new ManagedPlayerEnvironmentSession({process,runtimeInstanceId:"managed-runtime",environmentFingerprint:"managed-environment"});
 const runtime={process,manifest:{candidate_id:"synthetic"},exactGame:{version:"fixture",commit:"fixture"},build,runtimeIdentity:{kind:"synthetic"},adapterRuntimeInstanceId:"managed-runtime"};
 const service=await startManagedHostService({session,runtime,environmentFingerprint:"managed-environment"},{hostIdentity:host});
 let f:Awaited<ReturnType<typeof globalRuntimeFixture>>|undefined;
 try{
  const reset=await fetch(service.endpoint+"/v1/admin/reset",{method:"POST",headers:{authorization:`Bearer ${service.managerToken}`,"content-type":"application/json","x-sts2-managed-service-id":service.serviceInstanceId},body:JSON.stringify({request_id:"reset",seed:"SEED",expected_runtime_instance_id:"managed-runtime",expected_game_continuity_id:null})});expect(reset.status).toBe(200);
  const ready=await(await fetch(service.endpoint+"/v1/ready",{headers:{authorization:`Bearer ${service.clientToken}`}})).json() as any;
  const directory=await realpath(await mkdtemp(join(tmpdir(),"global-managed-"))),bindingPath=join(directory,"binding.json"),attachmentPath=join(directory,"attachment.json");
  await writeFile(bindingPath,JSON.stringify({schema:"sts2.policy-runtime/managed-environment-binding-1",profile_sha256:"a".repeat(64),input_profile:"public-basis-1",host_package_identity:{package:host.package_name,version:host.version,source_revision:host.source_revision,component_tree_revision:host.component_tree_revision,release_asset_sha256:"d".repeat(64),package_content_sha256:"e".repeat(64)},candidate_build:build}));
  await writeFile(attachmentPath,JSON.stringify({schema:"sts2.host-runtime/managed-service-attachment-1",endpoint:service.endpoint,service_instance_id:service.serviceInstanceId,host_identity:ready.host_identity,role:"client",token:service.clientToken}),{mode:0o600});
  const transport=await ManagedServicePolicyClient.attachGlobal(bindingPath,attachmentPath,{serviceInstanceId:service.serviceInstanceId,runtimeInstanceId:"managed-runtime",gameContinuityId:ready.episode.game_continuity_id},"run-00000000-0000-4000-8000-000000000001");
  f=await globalRuntimeFixture({transport});
  const first=await f.runtime.tick();expect(first.type).toBe("delivered");expect(actions).toBe(1);
  expect((await f.runtime.tick()).type).toBe("closed");expect(service.driver.status().control_held).toBe(false);expect(service.driver.closed).toBe(false);
  expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
 }finally{await f?.close();await service.close();}
},15000);
