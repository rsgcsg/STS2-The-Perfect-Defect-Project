import { describe, it, expect } from "vitest";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { replayGlobalAgentRun } from "../src/global-replay.js";
import { globalRuntimeFixture, GlobalHttpFixture } from "./global-runtime-fixtures.js";
function gate() { let release!: () => void; return { promise: new Promise<void>(r => { release = r; }), get release() { return release; } }; }
const within = <T>(value: Promise<T>, ms=5000) => Promise.race([value, new Promise<never>((_, reject) => { const t=setTimeout(() => reject(new Error("bounded fixture deadline")),ms);t.unref(); })]);
describe("GLOBAL through the existing Runtime, real HTTP and real supervised Agent NDJSON", () => {
  it("consumes only the semantic modelView, executes, closes and preserves exact CRLF Agent frames", async () => {
    const f = await globalRuntimeFixture();
    try {
      expect((await f.runtime.tick()).type).toBe("delivered");
      expect((await f.runtime.tick()).type).toBe("closed");
      const events = await f.events(); expect(events.at(-1).kind).toBe("stopped"); expect(f.source.steps).toBe(1); expect(f.source.controlHeld).toBe(false);
      expect(f.source.requests.find(r => r.path === "/api/player-environment/actions")!.body.input_profile).toBe("public-basis-1");
      expect(events.find(e => e.kind === "global_receipt").payload.receipt.input_profile).toBe("public-basis-1");
      const input = events.find(e=>e.kind==="global_input"); const parsed=JSON.parse(await readFile(join(f.evidence.directory,input.payload.artifact.path),"utf8"));
      expect(parsed.observation.envelope).toBeUndefined(); expect(parsed.observation.session).toBeUndefined();
      expect(JSON.stringify(parsed)).not.toMatch(/runtime_instance_id|source_snapshot_id|bound_action_id|control_token/);
      const frame = events.find(e=>e.kind==="global_agent_frame"&&e.payload.direction==="in");
      const bytes=await readFile(join(f.evidence.directory,frame.payload.artifact.path)); expect(bytes.subarray(-2).toString()).toBe("\r\n");
      expect(events.filter(e=>e.kind==="global_consumed")).toHaveLength(2);
    } finally { await f.close(); }
  });
  it("expiry releases a held controller during a five-minute human Next wait without a second tick or GET", async () => {
    const source=new GlobalHttpFixture();source.terminalAfterStep=false;
    const f=await globalRuntimeFixture({source,behavior:"hang_second_next",budgetMs:1200, editManifest: manifest => { manifest.limits.agent_timeout_ms = 300_000; }});
    try {
      expect((await f.runtime.tick()).type).toBe("delivered");expect(source.controlHeld).toBe(true);
      const released=gate();source.releaseObserved=released.release;const pending=f.runtime.tick();
      await within(released.promise);expect(source.releases).toBe(1);await within(pending);expect(source.steps).toBe(1);expect(source.controlHeld).toBe(false);
      await within((async()=>{while(!(await f.events()).some(e=>e.kind==="autonomy_budget_exhausted"))await new Promise(r=>setTimeout(r,10));})());
      expect((await f.events()).some(e=>e.kind==="autonomy_budget_exhausted")).toBe(true);
    } finally { await f.close(); }
  });
  it("cancels a late chooser before a post-deadline native write",async()=>{
    const f=await globalRuntimeFixture({behavior:"late_choice",budgetMs:1500});
    try{await within(f.runtime.tick());expect(f.source.steps).toBe(0);
      const events=await f.events();const frames=events.filter(e=>e.kind==="global_agent_frame"&&e.payload.direction==="out");
      const messages=await Promise.all(frames.map(async e=>JSON.parse(await readFile(join(f.evidence.directory,e.payload.artifact.path),"utf8"))));
      expect(messages.some(m=>m.message_type==="next")).toBe(true);
    }finally{await f.close();}
  });
  it.each(["banana","wrong_request","wrong_action","unknown"])("fences %s after one original submission with no retry",async variant=>{
    const source=new GlobalHttpFixture();if(variant==="wrong_request")source.wrongRequest=true;else if(variant==="wrong_action")source.wrongAction=true;else source.delivery=variant;
    const f=await globalRuntimeFixture({source});try{expect((await f.runtime.tick()).type).toBe("unknown");await f.runtime.tick();expect(source.steps).toBe(1);expect(f.runtime.status().tainted).toBe(true);}finally{await f.close();}
  });
  it("Stop cancels an in-flight source capture without acquiring control or sending actions",async()=>{
    const f=await globalRuntimeFixture();const blocked=gate();f.source.captureGate=blocked.promise;
    try{const tick=f.runtime.tick();await new Promise(r=>setTimeout(r,50));const stopped=f.runtime.stop();await within(tick);await within(stopped);expect(f.source.steps).toBe(0);expect(f.source.controlHeld).toBe(false);}finally{blocked.release();await f.close();}
  });
  it("does not publish a complete original inventory as complete P1 when a required Read was never advertised",async()=>{
    const source=new GlobalHttpFixture();source.basis.snapshot.reads=source.basis.snapshot.reads.filter((r:any)=>r.kind!=="map");source.basis.reads=source.basis.reads.filter((r:any)=>r.kind!=="map");
    const f=await globalRuntimeFixture({source});try{expect((await f.runtime.tick()).type).toBe("not_admitted");expect(f.source.steps).toBe(0);expect((await f.events()).filter(e=>e.kind==="global_input")).toHaveLength(0);}finally{await f.close();}
  });
  it.each(["another-episode", null])("retains the known delivered prefix when the same session swaps continuity to %s",async continuity=>{
    const source=new GlobalHttpFixture();source.afterStepContinuity=continuity;
    const f=await globalRuntimeFixture({source});
    try{
      expect((await f.runtime.tick()).type).toBe("not_delivered"); expect(source.steps).toBe(1);expect(source.controlHeld).toBe(false);
      expect(f.runtime.status().tainted).toBe(true);
      const result=(await f.events()).find(e=>e.kind==="global_result").payload.result;
      expect(result).toMatchObject({status:"interrupted_known",steps:[{delivery:"delivered"}]});
      await f.runtime.stop();expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(false);
    }finally{await f.close();}
  });
  it("keeps a classified delivered Receipt when its successor session drifts",async()=>{
    const source=new GlobalHttpFixture();source.wrongSuccessorSession=true;const f=await globalRuntimeFixture({source});
    try{expect((await f.runtime.tick()).type).toBe("not_delivered");
      const result=(await f.events()).find(e=>e.kind==="global_result").payload.result;
      expect(result).toMatchObject({status:"interrupted_known",steps:[{delivery:"delivered"}]});expect(source.steps).toBe(1);expect(source.controlHeld).toBe(false);
      await f.runtime.stop();expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(false);
    }finally{await f.close();}
  });
  it("Stop during the mandatory single-step post-delivery capture preserves known delivery",async()=>{
    const source=new GlobalHttpFixture(), entered=gate(), release=gate();source.postCaptureGate=release.promise;source.postCaptureEntered=entered.release;
    const f=await globalRuntimeFixture({source});try{
      const tick=f.runtime.tick();await within(entered.promise);const stopping=f.runtime.stop();await within(tick);await within(stopping);
      const result=(await f.events()).find(e=>e.kind==="global_result").payload.result;
      expect(result).toMatchObject({status:"interrupted_known",steps:[{delivery:"delivered"}]});expect(source.steps).toBe(1);expect(source.controlHeld).toBe(false);
      expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(false);
    }finally{release.release();await f.close();}
  });

  it.each(["unknown", "not_delivered"])("preserves %s when the same Receipt also carries a wrong successor session",async delivery=>{
    const source=new GlobalHttpFixture();source.delivery=delivery;source.wrongSuccessorSession=true;const f=await globalRuntimeFixture({source});
    try{expect((await f.runtime.tick()).type).toBe(delivery==="unknown"?"unknown":"not_delivered");
      const result=(await f.events()).find(e=>e.kind==="global_result").payload.result;
      expect(result).toMatchObject({status:delivery==="unknown"?"unknown":"not_started",steps:[{delivery}]});expect(source.steps).toBe(1);
      await f.runtime.stop();expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(delivery==="unknown");
    }finally{await f.close();}
  });
  it("does not offer a complete decision before an actual nonnull episode binding",async()=>{
    const source=new GlobalHttpFixture();source.continuity=null;const f=await globalRuntimeFixture({source});
    try{expect((await f.runtime.tick()).type).toBe("not_admitted");expect(source.steps).toBe(0);expect((await f.events()).some(e=>e.kind==="global_input")).toBe(false);}finally{await f.close();}
  });

  it("refuses an otherwise complete decision outside the Agent's declared interaction support", async () => {
    const f = await globalRuntimeFixture({ editManifest: m => { m.support.interaction_kinds = ["not_this_scene"]; } });
    try {
      expect((await f.runtime.tick()).type).toBe("not_admitted"); expect(f.source.steps).toBe(0);
      expect((await f.events()).some(e => e.kind === "global_input")).toBe(false);
      await f.runtime.stop(); expect((await replayGlobalAgentRun(f.evidence.directory)).results).toBe(0);
    } finally { await f.close(); }
  });

});
