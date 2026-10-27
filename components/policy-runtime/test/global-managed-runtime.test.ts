import { afterAll, beforeAll, describe, it, expect } from "vitest";
import { mkdtemp, writeFile, readFile, realpath, rm, symlink } from "node:fs/promises";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { createServer } from "node:net";
import { createRequire } from "node:module";
import { execFileSync, spawn, spawnSync, type ChildProcess } from "node:child_process";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { join } from "node:path";
// The actual Host service/actor and Managed projection, with only native process input synthetic.
// @ts-expect-error Host owner uses native ESM, consumed here without a TS reimplementation.
import { startManagedHostService } from "../../host-runtime/src/managed-host-service.mjs";
// @ts-expect-error same original Host module.
import { ManagedPlayerEnvironmentSession } from "../../host-runtime/src/managed-player-environment.mjs";
// @ts-expect-error Shared synthetic DTO follows the original Host terminal owner.
import { gameOver } from "../../host-runtime/test/fixtures/managed-game-over.mjs";
import { ManagedServicePolicyClient } from "../src/managed.js";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";
import { canonicalJson } from "../src/evidence.js";
import { POLICY_RUNTIME_VERSION } from "../src/contracts.js";
// @ts-expect-error Original Host synthetic native DTO, no game mutation.
import { combatState } from "../../host-runtime/test/fixtures/managed-fair-combat.mjs";
async function managedSource(stall = false) {
 let terminal=false, actions=0;
 const enemy = combatState(); enemy.turn_owner = "enemy"; enemy.is_play_phase = false;
 const decision=()=>terminal && stall ? enemy : terminal?gameOver({type:"decision",context:{act:1,act_index:0,act_definition_id:"OVERGROWTH",act_name:"Overgrowth",floor:1,total_floor:1,ascension:0,bosses:[],modifiers:[]},player:{name:"The Ironclad",character_id:"IRONCLAD",hp:0,max_hp:80,gold:99,native_ref:"player",max_potion_slots:3,relics:[],potions:[],deck:[]}}):{
  type:"decision",decision:"map_select",context:{act:1,act_index:0,act_definition_id:"OVERGROWTH",act_name:"Overgrowth",floor:0,total_floor:0,ascension:0,bosses:[],modifiers:[]},
  choices:[{col:3,row:1,type:"Monster",native_ref:"point",children:[]}],visible_map:{type:"map",rows:[[{col:3,row:0,type:"Monster",visited:true,children:[{col:3,row:1}]}]],boss:{col:3,row:16,type:"Boss"},current_coord:{col:3,row:0}},
  player:{name:"The Ironclad",character_id:"IRONCLAD",hp:80,max_hp:80,gold:99,native_ref:"player",max_potion_slots:3,relics:[],potions:[],deck:[]}};
 const process={async request(value:any){if(value.cmd==="start_run")return decision();if(value.cmd==="get_map")return decision().visible_map;if(value.cmd==="run_identity")return{type:"run_identity",active:true,seed:"SEED"};if(value.cmd==="action"){actions++;terminal=true;return decision();}throw Error("unexpected:"+value.cmd);},async stop(){return{code:0};}};
 const build={upstream_revision:"upstream",source_patch_sha256:"f".repeat(64),artifact_sha256:"1".repeat(64),artifact_mvid:"mvid",original_sts2_sha256:"2".repeat(64),runtime_sts2_sha256:"3".repeat(64)};
 const host={package_name:"@rsgcsg/sts2-host-runtime",version:"1",distribution_kind:"git_checkout",source_revision:"b".repeat(40),component_tree_revision:"c".repeat(40),source_digest_sha256:"4".repeat(64)};
 const session=new ManagedPlayerEnvironmentSession({process,runtimeInstanceId:"managed-runtime",environmentFingerprint:"managed-environment"});
 const runtime={process,manifest:{candidate_id:"synthetic"},exactGame:{version:"fixture",commit:"fixture"},build,runtimeIdentity:{kind:"synthetic"},adapterRuntimeInstanceId:"managed-runtime"};
 const service=await startManagedHostService({session,runtime,environmentFingerprint:"managed-environment"},{hostIdentity:host});
 try{
  const reset=await fetch(service.endpoint+"/v1/admin/reset",{method:"POST",headers:{authorization:`Bearer ${service.managerToken}`,"content-type":"application/json","x-sts2-managed-service-id":service.serviceInstanceId},body:JSON.stringify({request_id:"reset",seed:"SEED",expected_runtime_instance_id:"managed-runtime",expected_game_continuity_id:null})});expect(reset.status).toBe(200);
  const ready=await(await fetch(service.endpoint+"/v1/ready",{headers:{authorization:`Bearer ${service.clientToken}`}})).json() as any;
  const directory=await realpath(await mkdtemp(join(tmpdir(),"global-managed-"))),bindingPath=join(directory,"binding.json"),attachmentPath=join(directory,"attachment.json");
  await writeFile(bindingPath,JSON.stringify({schema:"sts2.policy-runtime/managed-environment-binding-1",profile_sha256:"a".repeat(64),input_profile:"public-basis-1",host_package_identity:{package:host.package_name,version:host.version,source_revision:host.source_revision,component_tree_revision:host.component_tree_revision,release_asset_sha256:"d".repeat(64),package_content_sha256:"e".repeat(64)},candidate_build:build}));
  await writeFile(attachmentPath,JSON.stringify({schema:"sts2.host-runtime/managed-service-attachment-1",endpoint:service.endpoint,service_instance_id:service.serviceInstanceId,host_identity:ready.host_identity,role:"client",token:service.clientToken}),{mode:0o600});
  const target={serviceInstanceId:service.serviceInstanceId,runtimeInstanceId:"managed-runtime",gameContinuityId:ready.episode.game_continuity_id};
  return {service,bindingPath,attachmentPath,target,get actions(){return actions;}};
 }catch(error){await service.close();throw error;}
}
it("runs real Managed service HTTP → original same-actor capture → NDJSON → token/epoch action → release",async()=>{
 const source=await managedSource();let f:Awaited<ReturnType<typeof globalRuntimeFixture>>|undefined;
 try{
  const transport=await ManagedServicePolicyClient.attachGlobal(source.bindingPath,source.attachmentPath,source.target,"run-00000000-0000-4000-8000-000000000001");
  expect(()=>transport.startupEnvironment).toThrow("global_source_not_admitted");
  f=await globalRuntimeFixture({transport});
  const first=await f.runtime.tick();expect(first.type).toBe("delivered");expect(source.actions).toBe(1);
  expect((await f.runtime.tick()).type).toBe("closed");expect(source.actions).toBe(1);expect(source.service.driver.status().control_held).toBe(false);expect(source.service.driver.closed).toBe(false);
  expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
 }finally{await f?.close();await source.service.close();}
},15000);


it("bounds a stuck enemy turn through the real Managed service and releases its actor on Stop", async () => {
 const source = await managedSource(true);
 let f: Awaited<ReturnType<typeof globalRuntimeFixture>> | undefined;
 try {
  const transport = await ManagedServicePolicyClient.attachGlobal(source.bindingPath, source.attachmentPath, source.target,
    "run-00000000-0000-4000-8000-000000000002");
  f = await globalRuntimeFixture({ transport, managedSettlingTimeoutMs: 100 });
  await f.runtime.tick();
  expect(f.runtime.status()).toMatchObject({ controller: "released", mode: "human", tainted: true,
    taint_reason: expect.stringContaining("managed_settling_stalled") });
  expect(source.actions).toBe(1);
  await f.runtime.stop();
  expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
  expect(source.service.driver.status().control_held).toBe(false);
  await source.service.close();
  expect(source.service.driver.closed).toBe(true);
 } finally { await f?.close(); await source.service.close(); }
}, 15000);

describe("original GLOBAL CLI startup and control",()=>{
 let compiled:string;
 beforeAll(async()=>{
  compiled=await mkdtemp(join(tmpdir(),"global-runtime-cli-source-"));
  execFileSync(process.execPath,[createRequire(import.meta.url).resolve("typescript/bin/tsc"),"-p",fileURLToPath(new URL("../tsconfig.json",import.meta.url)),"--outDir",compiled]);
  await symlink(fileURLToPath(new URL("../../../node_modules/",import.meta.url)),join(compiled,"node_modules"),process.platform==="win32"?"junction":"dir");
  await writeFile(join(compiled,"package.json"),'{"type":"module"}');
 });
 afterAll(async()=>{if(compiled)await rm(compiled,{recursive:true,force:true});});
 it.each(["invalid=key","STPD_TEST_UNAVAILABLE_EXPLICIT_KEY"])("rejects unavailable/invalid explicit key %s before opening a manifest",key=>{
  const environment={...process.env};delete environment.STPD_TEST_UNAVAILABLE_EXPLICIT_KEY;
  const result=spawnSync(process.execPath,[join(compiled,"cli.js"),"--manifest","/not-opened.json",
   "--adapter-command","/not-launched","--adapter-env-key",key],{env:environment,encoding:"utf8",timeout:2000});
  expect(result.status).toBe(1);expect(result.stderr).toContain("Agent environment key");
  expect(result.stderr).not.toContain("ENOENT");expect(result.stdout).toBe("");
 });
 it.each(["managed","reference","managed-stalled"] as const)("attests %s startup and performs one original step/Stop",async(kind)=>{
  const stalled=kind==="managed-stalled";
  const source=kind!=="reference"?await managedSource(stalled):undefined;
  const transport=source?await ManagedServicePolicyClient.attachGlobal(source.bindingPath,source.attachmentPath,source.target,"run-00000000-0000-4000-8000-000000000001"):undefined;
  const f=await globalRuntimeFixture({transport});await f.runtime.stop();
  let child:ChildProcess|undefined,closed:Promise<unknown[]>|undefined;
  try{
   const listener=createServer();listener.listen(0,"127.0.0.1");await once(listener,"listening");
   const address=listener.address();if(!address||typeof address==="string")throw Error("test listener unavailable");
   const port=address.port;await new Promise<void>(done=>listener.close(()=>done()));
   const manifestPath=join(f.root,"agent.json");
   const agent=join(f.root,"explicit-environment-agent.mjs");
   await writeFile(agent,`if(process.env.STPD_TEST_SELECTED!=="local-test-capability"||process.env.STPD_TEST_UNSELECTED!==undefined)throw Error("Agent environment selection differs");\nawait import(${JSON.stringify(new URL("./fixtures/global-agent-child.mjs",import.meta.url).href)});\n`);
   const args=[join(compiled,"cli.js"),"--manifest",manifestPath,"--adapter-command",process.execPath,
    "--adapter-arg",agent,"--adapter-arg",manifestPath,"--listen-port",String(kind!=="reference"?0:port),"--evidence-root",f.root,
    "--mode","human","--max-auto-submissions",stalled?"4":"1","--max-policy-calls","8","--auto-deadline-ms","10000",
    "--adapter-env-key","STPD_TEST_SELECTED"];
   if(source)args.push("--managed-binding",source.bindingPath,"--managed-attachment",source.attachmentPath,
    "--managed-expected-service-instance-id",source.target.serviceInstanceId,"--managed-expected-runtime-instance-id",source.target.runtimeInstanceId,
    "--managed-expected-game-continuity-id",source.target.gameContinuityId,"--managed-settling-timeout-ms",stalled?"100":"120000");
   else {
    args.push("--connector-endpoint",f.source.address);
    const raw=Buffer.from([255,0,13,10,123,125,10]);
    const original={schema:"spireagent/native-initial-progress-original-v1",source:"native_progress_file",status:"captured",
     runtime_instance_id:f.source.capabilities.host.runtime_instance_id,profile_generation_id:"fixture-generation",native_profile_id:1,
     seed:"fixture-seed",captured_at:new Date().toISOString(),bytes:raw.length,sha256:createHash("sha256").update(raw).digest("hex"),reason:null};
    const descriptor=JSON.stringify(original),path=join(f.root,"initial-progress-original.json");
    await writeFile(path,descriptor);await writeFile(join(f.root,"initial-progress.bin"),raw);
    args.push("--initial-progress-original",path,"--initial-progress-original-sha256",createHash("sha256").update(descriptor).digest("hex"));
   }
   child=spawn(process.execPath,args,{stdio:["ignore","pipe","pipe"],env:{...process.env,
    STPD_TEST_SELECTED:"local-test-capability",STPD_TEST_UNSELECTED:"must-not-inherit"}});closed=once(child,"close");
   let diagnostics="";child.stderr!.on("data",chunk=>{diagnostics+=String(chunk);});
   const startup=await new Promise<any>((resolve,reject)=>{
    let text="";const timer=setTimeout(()=>reject(Error("GLOBAL CLI startup timeout:"+diagnostics)),5000);
    child!.once("exit",code=>{clearTimeout(timer);reject(Error(`GLOBAL CLI early exit ${code}: ${diagnostics}`));});
    child!.stdout!.on("data",chunk=>{text+=String(chunk);const end=text.indexOf("\n");if(end>=0){clearTimeout(timer);resolve(JSON.parse(text.slice(0,end)));}});
   });
   const expected=source?{binding_sha256:createHash("sha256").update(canonicalJson(JSON.parse(await readFile(source.bindingPath,"utf8")))).digest("hex"),
    service_instance_id:source.target.serviceInstanceId,runtime_instance_id:source.target.runtimeInstanceId,game_continuity_id:source.target.gameContinuityId}:null;
   expect(startup).toMatchObject({schema:"sts2.policy-runtime/agent-session-startup-2",runtime_version:POLICY_RUNTIME_VERSION,managed_environment:expected,mode:"human"});
   expect(JSON.stringify(startup)).not.toContain("local-test-capability");
   expect(Number(new URL(startup.address).port)).toBeGreaterThan(0);
   if(source){const attachment=JSON.parse(await readFile(source.attachmentPath,"utf8"));expect(JSON.stringify(startup)).not.toContain(attachment.token);expect(JSON.stringify(startup)).not.toContain(source.attachmentPath);}
   const environment=await(await fetch(startup.address+"/v2/environment")).json();
   const headers={"content-type":"application/json","x-sts2-policy-run-id":startup.run_id,"x-sts2-game-instance-id":environment.runtime_instance_id,"x-sts2-recovery-epoch":String(environment.recovery_epoch)};
   const mode=await fetch(startup.address+"/v2/mode",{method:"POST",headers,body:JSON.stringify({mode:"one_step"})});expect(mode.status).toBe(200);
   const tick=await fetch(startup.address+"/v2/tick",{method:"POST",headers,body:JSON.stringify({max_ticks:1})});expect(tick.status).toBe(200);
   const tickResult=await tick.json();
   if(stalled)expect(tickResult).toMatchObject({results:[{type:"not_delivered"}],status:{mode:"human",tainted:true,
    taint_reason:"global_post_delivery_capture:managed_settling_stalled",controller:"released"}});
   else expect(tickResult).toMatchObject({results:[{type:"delivered"}],status:{mode:"human",autonomy_budget:{submissions_used:1}}});
   const stop=await fetch(startup.address+"/v2/stop",{method:"POST",headers:{"content-type":"application/json","x-sts2-policy-run-id":startup.run_id},body:"{}"});expect(stop.status).toBe(200);
   expect((await stop.json()).status).toMatchObject({lifecycle:"stopped",controller:"released",pending_request:null});
   const [code]=await closed;expect(code,diagnostics).toBe(0);
   const replay=await replayGlobalAgentRun(join(f.root,startup.run_id));
   expect(replay.verdict).toBe("verified");
   expect(replay.initial_progress_original?.status??null).toBe(kind==="reference"?"captured":null);
   expect(await readFile(join(f.root,startup.run_id,"events.jsonl"),"utf8")).not.toContain("local-test-capability");
   expect(source?source.actions:f.source.steps).toBe(1);
   if(stalled&&source){await source.service.close();expect(source.service.driver.closed).toBe(true);
    await expect(fetch(startup.address+"/status")).rejects.toThrow();}
  }finally{
   if(child&&child.exitCode===null&&child.signalCode===null&&!child.killed)child.kill("SIGKILL");
   if(closed)await closed;await f.close();await source?.service.close();
  }
 },15000);
});
