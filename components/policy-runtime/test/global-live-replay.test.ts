import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { expect, it } from "vitest";
import { globalJournalEvents } from "../src/global-live-replay.js";
import { readLiveManifest, replayGlobalAgentRun, streamGlobalAgentRun, type VerifiedGlobalTransition } from "../src/global-replay.js";
import { GlobalHttpFixture, globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { projectPython } from "../../../tools/project-python.mjs";

it("the normal HTTP/Agent producer closes transitions before run end, including terminal without another choice", async () => {
  const source = new GlobalHttpFixture(); source.terminalAfterStep = false;
  const f = await globalRuntimeFixture({ source });
  let streaming: Promise<unknown> | undefined;
  const rows: VerifiedGlobalTransition[] = [];
  try {
    expect((await f.runtime.tick()).type).toBe("delivered");
    let first!: () => void;
    const emitted = new Promise<void>(resolve => { first = resolve; });
    streaming = streamGlobalAgentRun(f.evidence.directory, globalJournalEvents(f.evidence.directory), async row => {
      const manifest = JSON.parse(await readFile(join(f.evidence.directory, "manifest.json"), "utf8"));
      if (!rows.length) expect(manifest.ended_at).toBeNull();
      expect(row.input.catalog).toEqual(row.input.observation.catalog);
      // This conformance Agent has no model; research admission separately
      // requires a complete score vector bound to its real mu response.
      expect(row.directive.directive.selection.action_id).toBe(row.result.choice_id);
      expect(row.result.status).toBe("completed");
      expect(row.original_paths.length).toBeGreaterThan(0);
      expect(row.verified_through_sequence).toBeGreaterThan(row.result_sequence);
      const originalManifest = JSON.parse(Buffer.from(row.manifest_original_base64, "base64").toString("utf8"));
      expect(originalManifest.run_id).toBe(row.run_id);
      expect(originalManifest.ended_at).toBeNull();
      rows.push(row); first();
    });
    expect((await f.runtime.tick()).type).toBe("delivered");
    await Promise.race([emitted, streaming]);
    expect(rows[0]!.step_index).toBe(0);
    expect(rows[0]!.successor.observation.status).toBe("decision");
    source.terminalAfterStep = true;
    expect((await f.runtime.tick()).type).toBe("delivered");
    expect((await f.runtime.tick()).type).toBe("closed");
    await streaming;
    expect(rows).toHaveLength(3);
    expect(rows.at(-1)!.successor.observation.status).toBe("terminal");
    expect((await replayGlobalAgentRun(f.evidence.directory)).results).toBe(3);
  } finally { await f.close(); await streaming; }
});

it("a live finalization manifest rewrite is observed to completion", async () => {
  const directory = await mkdtemp(join(tmpdir(), "live-manifest-"));
  try {
    await writeFile(join(directory, "manifest.json"), "");
    const reading = readLiveManifest(directory);
    await writeFile(join(directory, "manifest.json"), '{"ended_at":');
    await new Promise(resolve => setTimeout(resolve, 40));
    await writeFile(join(directory, "manifest.json"), '{"ended_at":"final"}');
    expect(await reading).toEqual({ ended_at: "final" });
  } finally { await rm(directory, { recursive: true, force: true }); }
});

it("an unknown action produces no trainable transition and the original remains unknown", async () => {
  const source = new GlobalHttpFixture(); source.delivery = "unknown";
  const f = await globalRuntimeFixture({ source });
  const rows: VerifiedGlobalTransition[] = [];
  try {
    expect((await f.runtime.tick()).type).toBe("unknown");
    await f.runtime.stop();
    const report = await streamGlobalAgentRun(f.evidence.directory,
      globalJournalEvents(f.evidence.directory), async row => { rows.push(row); });
    expect(rows).toEqual([]);
    expect(report.unknown_delivery).toBe(true);
    expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(true);
  } finally { await f.close(); }
});

it("a known uncompleted action is an explicit boundary after the verified prefix", async () => {
  const source = new GlobalHttpFixture(); source.terminalAfterStep = false;
  const f = await globalRuntimeFixture({ source });
  const rows: VerifiedGlobalTransition[] = [], boundaries: Record<string, unknown>[] = [];
  try {
    expect((await f.runtime.tick()).type).toBe("delivered");
    source.delivery = "not_delivered";
    expect((await f.runtime.tick()).type).toBe("not_delivered");
    await f.runtime.stop();
    const report = await streamGlobalAgentRun(f.evidence.directory,
      globalJournalEvents(f.evidence.directory), async row => { rows.push(row); },
      async boundary => { boundaries.push(boundary); });
    expect(rows).toHaveLength(1);
    expect(rows[0]!.step_index).toBe(0);
    expect(boundaries).toHaveLength(1);
    expect(boundaries[0]).toMatchObject({ step_index: 1, result: { status: "not_started" } });
    expect(report.unknown_delivery).toBe(false);
  } finally { await f.close(); }
});

it("Evidence consumes the actual live proof through its typed entry, rejects resealed changes, and audits once", async () => {
  const source = new GlobalHttpFixture(); source.terminalAfterStep = false;
  const f = await globalRuntimeFixture({ source });
  const script = `import hashlib,json,sys
from pathlib import Path
from unittest.mock import patch
from sts2_platform_evidence import global_agent_session_run_evidence as owner
from sts2_platform_evidence.agent_run_evidence import _canonical_json
directory=Path(sys.argv[1]); runtime=Path(sys.argv[2]); expected={'global_runtime_directory':str(runtime)}
live=owner.LiveGlobalReplay(directory,runtime,stderr=sys.stderr)
print('STARTED',flush=True)
for value in live.events():
 if value.get('schema')=='sts2.policy-runtime/verified-global-transition-1': print('TRANSITION',flush=True)
 if value.get('schema')=='sts2.policy-runtime/verified-global-end-1':
  value['report']['verdict']='observer report is not Evidence'
  value['proof']['journal']={}
real=owner.subprocess.run
with patch.object(owner.subprocess,'run',wraps=real) as calls:
 typed=live.verify(expected); assert typed.passed,typed.findings
 # Count the semantic invocation even when the audit is prefixed by nice.
 semantic=lambda: sum(any('replayGlobalAgentRun' in arg for arg in call.args[0]) for call in calls.call_args_list)
 assert semantic()==0,semantic()
 wrong=live.verify({**expected,'autonomy_budget':{'mode':'bounded','deadlineMs':10000,'maxSubmissions':999,'maxPolicyCalls':40},'schema':'sts2.policy-runtime/agent-session-startup-2'})
 assert not wrong.passed,wrong
 audited=owner.audit_global_session(directory,expected); assert audited.passed,audited.findings
 assert semantic()==1,semantic()
 seal=directory/'evidence-manifest.json'; saved_seal=seal.read_bytes()
 seal.write_bytes(b'bad seal')
 assert not live.verify(expected).passed
 seal.write_bytes(saved_seal)
 def reseal():
  files=[owner._file_entry(directory/name,name) for name in sorted(p.name for p in directory.iterdir() if p.name not in owner.EXTRA)]
  run=json.loads((directory/'manifest.json').read_bytes())
  value={'schema':'sts2.policy-runtime/immutable-evidence-manifest-1','run_id':run['run_id'],'complete':True,'append_only':True,'files':files,'manifest_sha256':owner._sha(_canonical_json({'run_id':run['run_id'],'files':files}).encode())}
  seal.write_bytes((_canonical_json(value)+'\\n').encode())
  (directory/'checksums.sha256').write_text(''.join(f"{owner._sha(p.read_bytes())}  {p.name}\\n" for p in sorted(directory.iterdir()) if p.name!='checksums.sha256'))
 journal=directory/'events.jsonl'; original=journal.read_bytes()
 journal.write_bytes(original+b'\\n'); reseal()
 changed=live.verify(expected); assert not changed.passed,changed
 assert any(f.code=='global_live_original_changed' for f in changed.findings),changed.findings
 journal.write_bytes(original)
 events=[json.loads(line) for line in original.splitlines()]
 events[-1]['payload']['controller']='held'
 journal.write_bytes(b''.join((json.dumps(event)+'\\n').encode() for event in events)); reseal()
 assert not live.verify(expected).passed
 journal.write_bytes(original)
 next(directory.glob('global-*')).unlink(); reseal()
 missing=live.verify(expected); assert not missing.passed,missing
 assert any(f.code=='global_live_artifact_inventory' for f in missing.findings),missing.findings
 assert semantic()==1,semantic()
 print('FINAL:'+json.dumps({'typed':'pass','full_replays':semantic(),'budget':'rejected','resealed_bytes':'rejected','unreleased_control':'rejected','missing_original':'rejected'}),flush=True)
`;
  let output = "", errors = "";
  let first!: () => void;
  const transitioned = new Promise<void>(resolve => { first = resolve; });
  let child: ReturnType<typeof spawn> | undefined;
  try {
    expect((await f.runtime.tick()).type).toBe("delivered");
    child = spawn(projectPython(["STS2_EVIDENCE_PYTHON"]), ["-c", script, f.evidence.directory,
      fileURLToPath(new URL("../dist", import.meta.url))], {
      env: { ...process.env, PYTHONPATH: fileURLToPath(new URL("../../evidence", import.meta.url)) },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const exited = new Promise<number | null>((resolve, reject) => {
      child!.once("error", reject); child!.once("close", resolve);
    });
    child.stdout!.on("data", raw => { output += raw.toString(); if (output.includes("TRANSITION")) first(); });
    child.stderr!.on("data", raw => { errors += raw.toString(); });
    expect((await f.runtime.tick()).type).toBe("delivered");
    await Promise.race([transitioned, exited.then(() => { throw new Error(errors || output); })]);
    source.terminalAfterStep = true;
    expect((await f.runtime.tick()).type).toBe("delivered");
    expect((await f.runtime.tick()).type).toBe("closed");
    expect(await exited, errors).toBe(0);
    expect(JSON.parse(output.split("FINAL:")[1]!)).toEqual({ typed: "pass", full_replays: 1,
      budget: "rejected", resealed_bytes: "rejected", unreleased_control: "rejected", missing_original: "rejected" });
  } finally { child?.kill(); await f.close(); }
}, 30000);
