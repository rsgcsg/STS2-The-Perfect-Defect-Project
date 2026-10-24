import { it, expect } from "vitest";
import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { execFile, execFileSync, spawnSync } from "node:child_process";
import { promisify } from "node:util";
import { GlobalHttpFixture, globalRuntimeFixture } from "./global-runtime-fixtures.js";
const exec=promisify(execFile);
const python=()=>[process.env.STS2_EVIDENCE_PYTHON,process.env.PYTHON,"python","python3"].find(p=>p&&spawnSync(p,["-c","import sys;assert sys.version_info>=(3,11)"],{stdio:"ignore"}).status===0)!;
it("Workbench registers only the code-owned GLOBAL program and exposes zero learned models",async()=>{
 const source=new GlobalHttpFixture(),f=await globalRuntimeFixture({source});
 const root=f.root,modules=join(root,"node_modules"),runtime=join(modules,"@rsgcsg/sts2-policy-runtime"),sdk=join(runtime,"node_modules/@rsgcsg/sts2-connector-client");
 try{
  // Isolated source-stage package fixture. No claim that release/install admission ran here.
  await mkdir(sdk,{recursive:true});await cp(fileURLToPath(new URL("../dist/",import.meta.url)),join(runtime,"dist"),{recursive:true});
  await cp(fileURLToPath(new URL("../../connector/sdk/typescript/dist/",import.meta.url)),join(sdk,"dist"),{recursive:true});
  await cp(fileURLToPath(new URL("../../connector/sdk/typescript/package.json",import.meta.url)),join(sdk,"package.json"));
  await cp(fileURLToPath(new URL("../../../node_modules/zod/",import.meta.url)),join(runtime,"node_modules/zod"),{recursive:true});
  await writeFile(join(runtime,"package.json"),JSON.stringify({name:"@rsgcsg/sts2-policy-runtime",type:"module"}));
  const script=`import json,sys,subprocess\nfrom pathlib import Path\nfrom types import SimpleNamespace\nfrom spireagent.workbench.local_model_registration import LocalModelRegistration\nfrom spireagent.workbench.local_models import LocalModelService\nfrom spireagent.workbench.global_program_support import validate, metadata\nfrom spireagent.workbench.agent_composition import describe, launch_arguments\nroot=Path(sys.argv[1]).resolve(); private=root/'private'; private.mkdir(); (root/'configs/developer').mkdir(parents=True)\n(root/'configs/developer/local-policies-v1.json').write_text(json.dumps({'schema':'stpd/local-policy-registry-v1','runtime_package':{},'policies':[]}))\nmodels=SimpleNamespace(root=root,private_root=private,config=SimpleNamespace(platform_url=sys.argv[2]))\nmodels.registry=lambda:LocalModelService.registry(models)\nmodels._public_manifest_contract=lambda p:subprocess.run(['node','--input-type=module','-e',"import{readFileSync}from'node:fs';import{pathToFileURL}from'node:url';const r=await import(pathToFileURL(process.argv[1]));r.validateAgentManifest(JSON.parse(readFileSync(process.argv[2])));",str(root/'node_modules/@rsgcsg/sts2-policy-runtime/dist/index.js'),str(p)],check=True,capture_output=True)\nr=LocalModelRegistration(models.config,None,models)\nr._native_runtime=lambda **kw:(root/'node_modules',Path('.'))\nregistered=r.register_global_program(); registry=models.registry(); entry=registry['policies'][0]\nconfig,manifest=validate(root,private/entry['config'],private/entry['manifest'],binding_root=private)\ncomposition=describe(entry,manifest,config,metadata(private,config,manifest))\nassert composition['semantic_definition']['models']==[]\nassert launch_arguments({**entry,'manifest':str(private/entry['manifest'])})[1]=='spireagent.workbench.global_program_agent'\nassert manifest['input']['profile']=='p1-global-r1'\nprint(json.dumps({'status':registered['status'],'models':composition['semantic_definition']['models'],'trusted_adapter':entry['adapter']}))`;
  const result=await exec(python(),["-c",script,root,source.address],{env:{...process.env,PYTHONPATH:fileURLToPath(new URL("../../../python/",import.meta.url))},timeout:15000});
  expect(JSON.parse(result.stdout)).toEqual({status:"registered",models:[],trusted_adapter:"spireagent-global-public-program"});
 }finally{await f.close();}
},20000);

it("the exact registered Python program consumes, ACK-commits, selects and closes through original NDJSON",async()=>{
 const env={...process.env,PYTHONPATH:fileURLToPath(new URL("../../../python/",import.meta.url))};
 const declared=JSON.parse(execFileSync(python(),["-c","import json;from spireagent.workbench.global_program_agent import descriptor;print(json.dumps(descriptor()))"],{env,encoding:"utf8"}));
 const source=new GlobalHttpFixture();
 // Use the current Reference producer shape. Preserve the old Managed fixture as historical.
 const terminal=JSON.parse(await readFile(new URL("../../connector/sdk/typescript/test/fixtures/p1-global/reference-game_over.json",import.meta.url),"utf8"));
 terminal.snapshot.session=structuredClone(source.basis.snapshot.session);
 for(const read of terminal.reads)read.response.session=structuredClone(source.basis.snapshot.session);
 source.nextBases=[terminal];
 const f=await globalRuntimeFixture({source,program:{command:python(),args:["-m","spireagent.workbench.global_program_agent","--manifest","{manifest}"],adapter:declared.adapter,artifact:Buffer.from(JSON.stringify(declared)),env}});
 try{expect((await f.runtime.tick()).type).toBe("delivered");expect((await f.runtime.tick()).type).toBe("closed");
  expect((await f.events()).filter(e=>e.kind==="global_consumed")).toHaveLength(2);expect(f.source.steps).toBe(1);
  const code="import json,sys;from pathlib import Path;from sts2_platform_evidence import verify_agent_session_run_evidence;from spireagent.workbench.agent_task_outcome import project;r=verify_agent_session_run_evidence(sys.argv[1],{'global_runtime_directory':sys.argv[2]});assert r.passed;r=project(r,native=True,native_package=Path(sys.argv[3]).read_bytes());print(json.dumps({'models':r['agent_composition']['models'],'task':r['task_completion']['status'],'terminal':r['terminal_observation']['status']}))";
  const result=await exec(python(),["-c",code,f.evidence.directory,fileURLToPath(new URL("../dist/",import.meta.url)),f.manifest.artifact.path],{env:{...env,PYTHONPATH:env.PYTHONPATH+ (process.platform==="win32"?";":":")+fileURLToPath(new URL("../../evidence/",import.meta.url))},timeout:15000});
  expect(JSON.parse(result.stdout)).toEqual({models:[],task:"unproved",terminal:"observed"});
 }finally{await f.close();}
},15000);
