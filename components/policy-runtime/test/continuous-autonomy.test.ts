import { describe, expect, it } from "vitest";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { decodePlayerSnapshot, decodePlayerReceipt } from "@rsgcsg/sts2-connector-client";
import type { PolicyConnector, PolicyManifest } from "../src/contracts.js";
import { PolicyRuntime } from "../src/runtime.js";
import { AgentRunEvidence, verifyEvidenceDirectory } from "../src/evidence.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";
import { globalRuntimeFixture, GlobalHttpFixture } from "./global-runtime-fixtures.js";
import { nativeRuntimeFixture } from "./native-runtime-fixtures.js";
// @ts-expect-error Shared production-protocol fixture, not a second legality implementation.
import { publicManifest, publicSnapshot, publicCapabilities } from "./fixtures/public-observation-fixture.mjs";
import { projectPython } from "../../../tools/project-python.mjs";

function typedVerify(directory: string, native = false) {
  const python = projectPython(["STS2_EVIDENCE_PYTHON"]);
  const code = `import json,sys
from sts2_platform_evidence import verify_agent_run_evidence,verify_agent_session_run_evidence
r=verify_agent_session_run_evidence(sys.argv[1], {'schema':'sts2.policy-runtime/agent-session-startup-2','autonomy_budget':{'mode':'continuous'}}) if sys.argv[2]=='native' else verify_agent_run_evidence(sys.argv[1])
print(json.dumps({'passed':r.passed,'findings':[f.code for f in r.findings]}))`;
  return JSON.parse(execFileSync(python, ["-c", code, directory, native ? "native" : "policy"], {
    env: { ...process.env, PYTHONPATH: fileURLToPath(new URL("../../evidence/", import.meta.url)) }, encoding: "utf8", timeout: 10000 }));
}

describe("explicit continuous authorization through the production Runtime profiles", () => {
  it("keeps the legacy Policy producer active past all old defaults, then pauses and explicitly continues the same run", async () => {
    const root = await mkdtemp(join(tmpdir(), "continuous-policy-"));
    let clock = 0, sequence = 1, submissions = 0, releases = 0;
    let current = decodePlayerSnapshot(publicSnapshot(sequence, 1)).data;
    const manifest = publicManifest() as PolicyManifest;
    manifest.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-1"; manifest.adapter_config = {};
    const evidence = await AgentRunEvidence.create({ root, policyManifest: manifest, mode: "auto", runtimeVersion: "fixture", runtimeCodeSha256: "e".repeat(64) });
    await evidence.attestAdapter(manifest.adapter);
    const connector: PolicyConnector = { capabilities: async () => publicCapabilities(), observeBundle: async () => ({ observation: current, reads: [] }),
      acquireController: async () => {}, releaseController: async () => { releases++; },
      submit: async input => {
        submissions++; const action = current.bound_actions.actions[0]!;
        current = decodePlayerSnapshot(publicSnapshot(++sequence, 1)).data;
        return decodePlayerReceipt({ protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1", request_id: input.requestId,
          delivery: "delivered", action: { bound_action_id: input.boundActionId, verb: action.verb, subject_referent_id: action.subject_referent_id ?? null, arguments: action.arguments },
          retry: { allowed: false, reason: "never" }, successor: current }).data;
      } };
    const runtime = new PolicyRuntime({ manifest, connector, evidence, runId: evidence.runId, mode: "auto", runtimeIdentity: { version: "fixture", code_sha256: "e".repeat(64) }, autoBudget: { mode: "continuous" }, monotonicNow: () => clock,
      policy: input => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    try {
      for (let i = 0; i < 34; i++) { clock = i * 2000; expect((await runtime.tick()).type).toBe("delivered"); }
      expect(runtime.status()).toMatchObject({ schema: "sts2.policy-runtime/status-2", mode: "auto", controller: "held", autonomy_budget: {
        mode: "continuous", state: "active", max_submissions: null, max_policy_calls: null, deadline_ms: null, remaining_ms: null,
        submissions_used: 34, policy_calls_used: 34, elapsed_ms: 66000 } });
      await runtime.setMode("human"); expect(releases).toBe(1); expect(submissions).toBe(34);
      clock += 1000; expect(runtime.status().autonomy_budget).toMatchObject({ state: "inactive", elapsed_ms: 66000, ended_reason: "human_recovery", submissions_used: 34 });
      await runtime.setMode("auto"); expect((await runtime.tick()).type).toBe("delivered");
      expect(runtime.status().autonomy_budget).toMatchObject({ mode: "continuous", state: "active", submissions_used: 1 });
      await runtime.stop(); expect(releases).toBe(2); await verifyEvidenceDirectory(evidence.directory);
      expect(typedVerify(evidence.directory)).toEqual({ passed: true, findings: [] });
      const events = (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").map(line => JSON.parse(line));
      expect(events.every(event => event.schema === "sts2.policy-runtime/agent-run-event-2")).toBe(true);
      expect(events.some(event => event.kind === "autonomy_budget_exhausted")).toBe(false);
    } finally { await runtime.stop(); await rm(root, { recursive: true, force: true }); }
  });

  it("passes continuous authorization through native SDK/HTTP/real Agent calls without losing actual accounting", async () => {
    let clock = 0;
    const f = await nativeRuntimeFixture({ mode: "auto", child: "query", autoBudget: { mode: "continuous" }, monotonicNow: () => clock });
    try {
      for (let i = 0; i < 34; i++) {
        clock = i * 2000;
        f.source.observation.revision = i + 1; f.source.observation.snapshot_id = `continuous-${i}`;
        f.source.observation.owner_occurrence.occurrence_id = `continuous-occurrence-${i}`;
        f.source.capture.snapshot_id = f.source.observation.snapshot_id; f.source.observation.catalog.snapshot_id = f.source.observation.snapshot_id; f.source.rehash();
        const result = await f.runtime.tick(); expect(result.type, JSON.stringify(result)).toBe("delivered");
      }
      const budget = f.runtime.status().autonomy_budget;
      expect(budget).toMatchObject({ mode: "continuous", state: "active", submissions_used: 34, elapsed_ms: 66000, remaining_ms: null });
      expect(budget.policy_calls_used).toBeGreaterThan(32);
      expect(f.source.requests.filter(request => request.path.endsWith("/actions"))).toHaveLength(34);
      await f.runtime.setMode("human"); expect(f.runtime.status().controller).toBe("released");
      await f.runtime.stop(); expect((await f.events()).every(event => event.schema === "sts2.policy-runtime/agent-session-event-2")).toBe(true);
      expect(typedVerify(f.evidence.directory, true)).toEqual({ passed: true, findings: [] });
    } finally { await f.close(); }
  });

  it("passes continuous GLOBAL dispatches to exact-code semantic replay and preserves manual release", async () => {
    let clock = 0; const source = new GlobalHttpFixture(); source.terminalAfterStep = false;
    const f = await globalRuntimeFixture({ source, autoBudget: { mode: "continuous" }, monotonicNow: () => clock });
    try {
      for (let i = 0; i < 18; i++) { clock = i * 4000; source.basis.snapshot.interaction.content.context.round = i + 1;
        source.basis.snapshot.snapshot_id = `continuous-source-${i}`;
        for (const read of source.basis.reads) read.response.expected_snapshot_id = read.response.observed_snapshot_id = source.basis.snapshot.snapshot_id;
        const result = await f.runtime.tick(); expect(result.type, JSON.stringify(result)).toBe("delivered"); }
      expect(f.runtime.status().autonomy_budget).toMatchObject({ mode: "continuous", state: "active", submissions_used: 18, elapsed_ms: 68000, remaining_ms: null });
      expect(f.runtime.status().autonomy_budget.policy_calls_used).toBeGreaterThan(32);
      expect(source.steps).toBe(18); expect(source.controlHeld).toBe(true);
      await f.runtime.setMode("human"); expect(source.releases).toBe(1); expect(source.controlHeld).toBe(false);
      await f.runtime.stop(); expect((await replayGlobalAgentRun(f.evidence.directory)).verdict).toBe("verified");
    } finally { await f.close(); }
  });
  it("retains continuous GLOBAL release uncertainty as taint and never seals it as a successful stop", async () => {
    const source = new GlobalHttpFixture(); source.terminalAfterStep = false;
    const f = await globalRuntimeFixture({ source, autoBudget: { mode: "continuous" } });
    try {
      expect((await f.runtime.tick()).type).toBe("delivered"); source.releaseFailure = true;
      await expect(f.runtime.setMode("human")).rejects.toThrow();
      expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "unknown", tainted: true });
      await expect(f.runtime.setMode("auto")).rejects.toThrow();
      await expect(f.runtime.stop()).rejects.toThrow("native Stop attempted all owned cleanup"); expect(source.releases).toBe(1); expect(source.steps).toBe(1);
      const run = JSON.parse(await readFile(join(f.evidence.directory, "manifest.json"), "utf8"));
      expect(run).toMatchObject({ status: "tainted", tainted: true });
    } finally { await f.close(); }
  });

});
