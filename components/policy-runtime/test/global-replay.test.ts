import { describe, it, expect } from "vitest";
import { cp, readFile, writeFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";
import { canonicalJson } from "../src/evidence.js";
const sha=(b:Buffer|string)=>createHash("sha256").update(b).digest("hex");
const runtime=fileURLToPath(new URL("../dist/",import.meta.url));
function pythonVerify(directory:string, trusted=true){
 const python=[process.env.STS2_EVIDENCE_PYTHON,process.env.PYTHON,"python","python3"].find(p=>p&&spawnSync(p,["-c","import sys;assert sys.version_info >= (3,11)"],{stdio:"ignore"}).status===0);
 if(!python)throw new Error("Python >=3.11 required; set PYTHON to the existing supported interpreter");
 const code=`import json,sys\nfrom sts2_platform_evidence import verify_agent_session_run_evidence\nr=verify_agent_session_run_evidence(sys.argv[1], {'global_runtime_directory':sys.argv[2]} if sys.argv[2] else None)\nprint(json.dumps({'passed':r.passed,'findings':[f.code for f in r.findings]}))`;
 return JSON.parse(execFileSync(python,["-c",code,directory,trusted?runtime:""],
  {env:{...process.env,PYTHONPATH:fileURLToPath(new URL("../../evidence/",import.meta.url))},encoding:"utf8",timeout:30000}));
}
async function reseal(directory:string){
 const names=(await readdir(directory)).filter(n=>n!=="checksums.sha256"&&n!=="evidence-manifest.json").sort();
 const files=await Promise.all(names.map(async path=>{const b=await readFile(join(directory,path));return{path,bytes:b.length,sha256:sha(b)};}));
 const run=JSON.parse(await readFile(join(directory,"manifest.json"),"utf8"));
 const manifest={schema:"sts2.policy-runtime/immutable-evidence-manifest-1",run_id:run.run_id,complete:true,append_only:true,files,manifest_sha256:sha(canonicalJson({run_id:run.run_id,files}))};
 await writeFile(join(directory,"evidence-manifest.json"),canonicalJson(manifest)+"\n");
 const checks=await Promise.all([...names,"evidence-manifest.json"].sort().map(async n=>`${sha(await readFile(join(directory,n)))}  ${n}`));
 await writeFile(join(directory,"checksums.sha256"),checks.join("\n")+"\n");
}
describe("GLOBAL original semantic replay and production Python Evidence verifier",()=>{
 it("verifies actual source→offered framed bytes→ACK→dispatch/result and rejects rehashed omissions",async()=>{
  const f=await globalRuntimeFixture();
  try{
   expect((await f.runtime.tick()).type).toBe("delivered");expect((await f.runtime.tick()).type).toBe("closed");
   const report=await replayGlobalAgentRun(f.evidence.directory);expect(report).toMatchObject({verdict:"verified",consumed:2,results:1,sources:2});
   expect(pythonVerify(f.evidence.directory)).toEqual({passed:true,findings:[]});
   expect(pythonVerify(f.evidence.directory,false).passed).toBe(false);
   for(const variant of ["truncate","drop_source","drop_input","fake_projection","fake_agent_offer","fake_ack"]){
    const directory=join(f.root,variant);await cp(f.evidence.directory,directory,{recursive:true});
    let rows=(await readFile(join(directory,"events.jsonl"),"utf8")).trim().split("\n").map(line=>JSON.parse(line));
    if(variant==="truncate")rows=rows.slice(0,-1);
    if(variant==="drop_source")rows=rows.filter(e=>e.kind!=="global_source");
    if(variant==="drop_input")rows=rows.filter(e=>e.kind!=="global_input");
    if(variant==="fake_ack")rows.find(e=>e.kind==="global_consumed").payload.acknowledgement.state_version+=4;
    if(variant==="fake_projection")rows.find(e=>e.kind==="global_observation").payload.observation_sha256="0".repeat(64);
    if(variant==="fake_agent_offer"){
     const e=rows.find(e=>e.kind==="global_agent_frame"&&e.payload.direction==="out"),old=e.payload.artifact;
     const message=JSON.parse(await readFile(join(directory,old.path),"utf8"));message.input.observation.player.content.hp=999;
     const bytes=Buffer.from(JSON.stringify(message)+"\n"),digest=sha(bytes);e.payload.artifact={...old,path:`global-${digest}.bin`,bytes:bytes.length,sha256:digest};await writeFile(join(directory,e.payload.artifact.path),bytes);
    }
    await writeFile(join(directory,"events.jsonl"),rows.map((e,i)=>canonicalJson({...e,sequence:i+1})).join("\n")+"\n");await reseal(directory);
    expect(pythonVerify(directory).passed,variant).toBe(false);
   }
  }finally{await f.close();}
 },30000);
});
