import { describe, expect, it, vi } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { decodePlayerSnapshot, type PlayerEnvironmentSnapshot, type PlayerEnvironmentCapabilities, type PlayerEnvironmentReceipt } from "@rsgcsg/sts2-connector-client";
import { validatePolicyManifest, POLICY_PORT_V4_SCHEMA, type PolicyManifest, type PublicStatefulPolicyDecisionInput, type PolicyDecisionInput, type PolicyConnector } from "../src/contracts.js";
import { PublicObservationTracker } from "../src/public-observation.js";
import { NdjsonPolicyPort } from "../src/policy-port.js";
import { PolicyRuntime } from "../src/runtime.js";
import { AgentRunEvidence } from "../src/evidence.js";
import { candidateOrderDigest } from "../src/digest.js";
// @ts-expect-error Shared raw JSON fixture used by the Python->Node protocol seam.
import { publicManifest, publicSnapshot, publicCapabilities, publicCompletion } from "./fixtures/public-observation-fixture.mjs";

const manifest = (): PolicyManifest => publicManifest();
const snapshot = (sequence = 1): PlayerEnvironmentSnapshot => decodePlayerSnapshot(publicSnapshot(sequence)).data;
function input(current = snapshot()): PolicyDecisionInput {
  return { run_id: "public-run", manifest: manifest(), bundle: { observation: current, reads: [] },
    candidate_count: current.bound_actions.actions.length, candidate_digest: candidateOrderDigest(current.bound_actions.actions) };
}
const choice = (current: PublicStatefulPolicyDecisionInput, selected = 1) => ({
  output: { candidate_digest: current.decision.candidate_digest,
    scores: current.decision.bundle.observation.bound_actions.actions.map((_, index) => index), selected_index: selected },
  completion: publicCompletion(current)
});

describe("bounded public observation Port4", () => {
  it("requires the explicit generic Snapshot profile and keeps all old ports closed", () => {
    expect(validatePolicyManifest(manifest())).toEqual(manifest());
    for (const alter of [
      (m: PolicyManifest) => { m.representation.input_schema = "sts2.player-environment/text-menu-snapshot-1"; },
      (m: PolicyManifest) => { m.requirements.reads = ["run_deck"]; },
      (m: PolicyManifest) => { delete m.adapter_config.public_stateful_profile; },
      (m: PolicyManifest) => { m.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-3"; }
    ]) { const value = manifest(); alter(value); expect(() => validatePolicyManifest(value)).toThrow(); }
  });

  it("counts qualified watermarks once, ignores poll time, and resets only on an explicit segment boundary", () => {
    const tracker = new PublicObservationTracker();
    const first = tracker.prepare(input());
    const observed = snapshot(); observed.observed_at = "2026-10-11T00:00:01Z";
    expect(tracker.prepare(input(observed)).control).toEqual(first.control);
    expect(tracker.prepare(input(snapshot(2))).control).toMatchObject({
      continuity_token: first.control.continuity_token, observation_ordinal: 2,
      episode_scope: "bounded_policy_segment", previous_action: null });
    tracker.reset();
    const reset = tracker.prepare(input(snapshot(2)));
    expect(reset.control.observation_ordinal).toBe(1);
    expect(reset.control.continuity_token).not.toBe(first.control.continuity_token);
    expect(reset.control.segment_id).not.toBe(first.control.segment_id);
  });

  it("rejects changed bodies/catalogs at the same watermark and regressing sequences", () => {
    for (const alter of [
      (s: PlayerEnvironmentSnapshot) => { s.bound_actions.actions.reverse(); },
      (s: PlayerEnvironmentSnapshot) => { s.interaction.content.surface.new_visible_value = 1; },
      (s: PlayerEnvironmentSnapshot) => { s.snapshot_id = "other"; }
    ]) {
      const tracker = new PublicObservationTracker(); tracker.prepare(input());
      const observed = snapshot(); alter(observed);
      expect(() => tracker.prepare(input(observed))).toThrow();
    }
  });

  it("uses the shared NDJSON queue, exact completion and cancelled-request fence", async () => {
    const stdin = new PassThrough(), stdout = new PassThrough(), stderr = new PassThrough();
    const child = Object.assign(new EventEmitter(), { stdin, stdout, stderr, exitCode: null, kill: vi.fn(() => true) });
    const port = new NdjsonPolicyPort(child as unknown as ConstructorParameters<typeof NdjsonPolicyPort>[0]);
    const requests: Record<string, unknown>[] = [];
    stdin.on("data", data => requests.push(JSON.parse(data.toString())));
    stdout.write(JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "ready", adapter: manifest().adapter }) + "\n");
    await port.attest(manifest().adapter);
    const tracker = new PublicObservationTracker(), current = tracker.prepare(input());
    const cancellation = new AbortController(), offered = vi.fn();
    const pending = port.decideV4(current, cancellation.signal, offered);
    expect(Object.keys(requests[0]!).sort()).toEqual(["control", "decision", "message_type", "request_id", "schema"]);
    expect(offered).toHaveBeenCalledOnce();
    cancellation.abort(); await expect(pending).rejects.toThrow(/cancelled/);
    const next = port.decideV4(current, new AbortController().signal, offered);
    stdout.write(JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "decision", request_id: requests[0]!.request_id, ...choice(current) }) + "\n");
    stdout.write(JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "decision", request_id: requests[1]!.request_id, ...choice(current) }) + "\n");
    expect(await next).toEqual(choice(current));
    const malformed = port.decideV4(current, new AbortController().signal, offered);
    const response = choice(current); response.completion.observation_ordinal += 1;
    stdout.write(JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "decision", request_id: requests[2]!.request_id, ...response }) + "\n");
    await expect(malformed).rejects.toThrow(/watermark mismatch/); port.close();
  });
});

async function runtimeFixture(selected = 1) {
  const root = await mkdtemp(join(tmpdir(), "public-observation-runtime-"));
  const policyManifest = manifest();
  const evidence = await AgentRunEvidence.create({ root, runId: "public-run", policyManifest,
    runtimeVersion: "fixture", runtimeCodeSha256: "d".repeat(64), mode: "auto" });
  await evidence.attestAdapter(policyManifest.adapter);
  let current = snapshot();
  let delivery: PlayerEnvironmentReceipt["delivery"] = "delivered";
  const calls: PublicStatefulPolicyDecisionInput[] = [];
  const connector: PolicyConnector = {
    capabilities: async () => publicCapabilities() as PlayerEnvironmentCapabilities,
    observeBundle: vi.fn(async () => ({ observation: structuredClone(current), reads: [] })),
    observeTextMenuContext: vi.fn(async () => { throw new Error("Port4 must never call text context"); }),
    acquireController: vi.fn(async () => {}), releaseController: vi.fn(async () => {}),
    submit: vi.fn(async (args: Parameters<PolicyConnector["submit"]>[0]): Promise<PlayerEnvironmentReceipt> => {
      if (delivery === "delivered") current = snapshot(current.sequence + 1);
      return { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1", request_id: args.requestId,
        delivery, action: { bound_action_id: args.boundActionId, verb: "end_turn", subject_referent_id: null, arguments: [] },
        reason_code: null, detail: null, retry: { allowed: false, reason: "never" },
        successor: delivery === "delivered" ? current : null, attribution: null };
    })
  };
  const policy = vi.fn(async (value: PublicStatefulPolicyDecisionInput) => { calls.push(structuredClone(value)); return choice(value, selected); });
  const runtime = new PolicyRuntime({ manifest: policyManifest, connector, evidence, publicStatefulPolicy: policy,
    mode: "auto", runId: "public-run", runtimeIdentity: { version: "fixture", code_sha256: "d".repeat(64) },
    autoBudget: { maxSubmissions: 10, maxPolicyCalls: 20, deadlineMs: 30000 }, sleep: async () => {} });
  return { runtime, calls, policy, connector, setCurrent(s: PlayerEnvironmentSnapshot) { current = s; },
    setDelivery(value: PlayerEnvironmentReceipt["delivery"]) { delivery = value; },
    async close() { await runtime.stop(); await rm(root, { recursive: true, force: true }); } };
}

describe("Port4 uses the one Runtime control/delivery owner", () => {
  it("records one exact delivery/successor only as control on the next qualified observation", async () => {
    const f = await runtimeFixture();
    try {
      const first = await f.runtime.tick(); expect(first.type).toBe("delivered");
      const second = await f.runtime.tick(); expect(second.type).toBe("delivered");
      expect(f.calls[0]!.control).toMatchObject({ observation_ordinal: 1, previous_action: null });
      expect(f.calls[1]!.control).toMatchObject({ observation_ordinal: 2, previous_action: {
        source_snapshot_id: "snapshot-1", bound_action_id: "candidate-1",
        receipt: { delivery: "delivered", reason_code: null }, successor: { snapshot_id: "snapshot-2", sequence: 2 } } });
      expect(f.calls[1]!.decision.bundle.observation).not.toHaveProperty("previous_action");
      expect(f.connector.observeTextMenuContext).not.toHaveBeenCalled();
      await f.runtime.setMode("human"); await f.runtime.setMode("shadow"); f.setCurrent(snapshot(3));
      expect((await f.runtime.tick()).type).toBe("shadow");
      expect(f.calls[2]!.control).toMatchObject({ observation_ordinal: 1, previous_action: null });
      expect(f.calls[2]!.control.continuity_token).not.toBe(f.calls[1]!.control.continuity_token);
    } finally { await f.close(); }
  });

  it("fails whole before any offered policy write for incomplete C and never retries unknown delivery", async () => {
    const f = await runtimeFixture();
    try {
      const partial = snapshot(); partial.bound_actions.total_count = 3; f.setCurrent(partial);
      expect((await f.runtime.tick()).type).toBe("not_admitted"); expect(f.policy).not.toHaveBeenCalled();
      f.setCurrent(snapshot()); await f.runtime.setMode("auto"); f.setDelivery("unknown");
      expect((await f.runtime.tick()).type).toBe("unknown");
      expect(f.runtime.status()).toMatchObject({ mode: "human", tainted: true, controller: "released" });
      await expect(f.runtime.setMode("auto")).rejects.toThrow(/tainted/);
      await f.runtime.tick(); expect(f.connector.submit).toHaveBeenCalledOnce();
    } finally { await f.close(); }
  });
});
