import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { decodePlayerSnapshot, decodePlayerReceipt, type PlayerEnvironmentBoundAction, type PlayerEnvironmentSnapshot, type PlayerEnvironmentCapabilities } from "@rsgcsg/sts2-connector-client";
import { PolicyRuntime } from "../src/runtime.js";
import { type ConnectorPolicyRequirements, type PolicyConnector, type PolicyManifest, type PolicyDecisionInput } from "../src/contracts.js";
import { AgentRunEvidence, verifyEvidenceDirectory } from "../src/evidence.js";

const manifest = (): PolicyManifest & { requirements: ConnectorPolicyRequirements } => ({
  schema: "sts2.policy-runtime/policy-manifest-1",
  manifest_id: "manifest-test",
  policy: { id: "policy-test", version: "1", provider: "fixture", architecture: "fixture" },
  adapter: { id: "stpd-decision-only", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-1", code_sha256: "c".repeat(64) },
  artifact: { id: "artifact-test", path: "artifact.bin", sha256: "a".repeat(64) },
  representation: { id: "snapshot-representation", version: "1", input_schema: "sts2.player-environment/snapshot-1" },
  requirements: { connector_protocol_version: "1.0.0", environment: { host_kind: "test", connector_version: "1", connector_source_revision: "source", connector_artifact_sha256: "b".repeat(64), connector_module_version_id: "mvid", modset_status: "exact", modset_fingerprint: "modset", loaded_mod_ids: ["fixture-mod"] }, reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-bound-action-id-order", score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
  support: { game_versions: ["fixture-game"], game_commits: ["fixture-commit"], interaction_kinds: ["test"], action_verbs: ["end_turn"] },
  adapter_config: {},
  claims: { full_run: false, selector: false, catalog_filtered: false, creates_action_authority: false, creates_native_operands: false }
});

const action = (id: string): PlayerEnvironmentBoundAction => ({ bound_action_id: id, verb: "end_turn", interaction_id: "interaction", arguments: [], label: id });
const snapshot = (ids: string[], snapshotId = "snapshot-1", status: PlayerEnvironmentSnapshot["status"] = "interactive", sequence = 1): PlayerEnvironmentSnapshot => ({
  protocol_version: "1.0.0", schema: "sts2.player-environment/snapshot-1", snapshot_id: snapshotId, sequence, observed_at: "2026-08-25T00:00:00.000Z", status, persistent: null,
  interaction: { interaction_id: "interaction", kind: "test", stage: "ready", content_schema: "sts2.player-environment/surface/test-1", content: { surface: { kind: "test" }, context: { kind: "test" } }, capabilities: [] },
  referents: [], bound_actions: { schema: "sts2.player-environment/bound-actions-1", status: "complete", materialized_count: ids.length, total_count: ids.length, limit: ids.length || 1, ordering_semantics: "connector_order", actions: ids.map(action) }, reads: [],
  completeness: { status: "complete", visible_information: "test", interaction_discovery: "test", missing: [], hidden_by_policy: [] }, session: { runtime_instance_id: "runtime", environment_fingerprint: "environment" }, information_policy: { id: "test", scope: "test", includes_hidden_information: false, unknown_field_behavior: "reject" }
});

function frame(ordinal: number): PlayerEnvironmentSnapshot {
  const current = snapshot(ordinal % 2 ? ["EndTurn", `visible-${ordinal}`] : ["EndTurn"], `frame-${ordinal}`, "interactive", ordinal);
  current.interaction.content.surface = { kind: "test", energy: ordinal % 3,
    hand: [{ card_id: `card-${ordinal}`, cost: 1 }], draw_count: 20 - ordinal,
    discard_count: ordinal, exhaust_count: ordinal % 4, enemy_hp: 40 - ordinal };
  return decodePlayerSnapshot(JSON.parse(JSON.stringify(current))).data;
}
class MovingConnector implements PolicyConnector {
  current = frame(1);
  requests: { requestId: string; expectedSnapshotId: string; boundActionId: string }[] = [];
  observe = vi.fn(async () => ({ observation: decodePlayerSnapshot(JSON.parse(JSON.stringify(this.current))).data, reads: [] }));
  capabilities = vi.fn(async () => {
    return {
      protocol_version: "1.0.0", snapshot_schema: "sts2.player-environment/snapshot-1", action_schema: "sts2.player-environment/action-1", receipt_schema: "sts2.player-environment/receipt-1", control_schema: "sts2.player-environment/control-1", status: "ready",
      host: { id: "fixture", name: "fixture", version: "1", runtime_instance_id: "runtime", host_kind: "test", implementation: { source_revision: "source", module_version_id: "mvid", artifact_sha256: "b".repeat(64) } },
      game: { version: "fixture-game", commit: "fixture-commit", branch: null, main_assembly_hash: null, compatibility: { status: "exact", observation_allowed: true, detail: "fixture" }, modset: { status: "exact", fingerprint: "modset", scope: "fixture", loaded_mod_ids: ["fixture-mod"], detail: "fixture" } },
      environment_fingerprint: "environment", verbs: ["end_turn"], snapshot_bound: true, single_controller: true, execution_available: true, control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
    } as PlayerEnvironmentCapabilities;
  });
  observeBundle = this.observe;
  acquireController = vi.fn(async () => {});
  releaseController = vi.fn(async () => {});
  submit = vi.fn(async (input: { requestId: string; expectedSnapshotId: string; boundActionId: string }) => {
    this.requests.push(input);
    const stale = input.expectedSnapshotId !== this.current.snapshot_id;
    if (!stale) this.move();
    return decodePlayerReceipt(JSON.parse(JSON.stringify({ protocol_version: "1.0.0",
      schema: "sts2.player-environment/receipt-1", request_id: input.requestId,
      delivery: stale ? "not_delivered" : "delivered", action: { bound_action_id: input.boundActionId, verb: "end_turn", arguments: [] },
      reason_code: stale ? "stale_snapshot" : null, retry: { allowed: stale, reason: stale ? "fresh_snapshot_required" : "never" },
      successor: this.current }))).data;
  });
  move() { this.current = frame(this.current.sequence + 1); }
}
function deferred<T = void>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
const score = (input: PolicyDecisionInput) => ({ candidate_digest: input.candidate_digest,
  scores: Array.from({ length: input.candidate_count }, (_, index) => -index), selected_index: 0 });
async function drain() { for (let index = 0; index < 40; index++) await Promise.resolve(); }
afterEach(() => vi.useRealTimers());

function fixture(options: { deadlineMs?: number; maxPolicyCalls?: number; maxSubmissions?: number } = {}) {
  let clock = 0;
  const connector = new MovingConnector();
  const events: { kind: string; payload: Record<string, unknown> }[] = [];
  const append = vi.fn(async (kind: string, payload: Record<string, unknown>) => { events.push({ kind, payload }); });
  const evidence = { append, finalize: async () => {} } as unknown as AgentRunEvidence;
  const policy = vi.fn(async (input: PolicyDecisionInput, _signal?: AbortSignal) => { connector.move(); return score(input); });
  const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", policy,
    evidence, runtimeIdentity: { version: "test", code_sha256: "e".repeat(64) },
    monotonicNow: () => clock, autoBudget: { maxPolicyCalls: 100, maxSubmissions: 100, ...options } });
  return { runtime, connector, policy, append, events, clock: (value: number) => { clock = value; } };
}

describe("absolute stale recovery window", () => {
  it("rescores four moving complete public frames before a fresh delivery with durable original receipts", async () => {
    vi.useFakeTimers();
    let clock = 0;
    const connector = new MovingConnector();
    const evidence = await AgentRunEvidence.create({ root: await mkdtemp(join(tmpdir(), "stale-window-")), policyManifest: manifest(),
      runtimeVersion: "test", runtimeCodeSha256: "e".repeat(64), mode: "auto" });
    await evidence.attestAdapter(manifest().adapter);
    const consumed: PlayerEnvironmentSnapshot[] = [];
    let moves = 4;
    const policy = vi.fn(async (input: PolicyDecisionInput) => {
      consumed.push(input.bundle.observation as PlayerEnvironmentSnapshot);
      // Public facts move during admitted scoring, after observation and before submit.
      await Promise.resolve(); if (moves-- > 0) connector.move();
      return score(input);
    });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", policy,
      evidence, runId: evidence.runId, runtimeIdentity: { version: "test", code_sha256: "e".repeat(64) },
      monotonicNow: () => clock, autoBudget: { maxPolicyCalls: 100, maxSubmissions: 100 } });
    for (const time of [0, 250, 750, 1750]) {
      clock = time; expect((await runtime.tick()).type).toBe("not_delivered");
      expect(runtime.status()).toMatchObject({ mode: "auto", tainted: false, controller: "released" });
    }
    clock = 2750; expect((await runtime.tick()).type).toBe("delivered");
    expect(policy).toHaveBeenCalledTimes(5);
    expect(consumed.map(current => current.snapshot_id)).toEqual(["frame-1", "frame-2", "frame-3", "frame-4", "frame-5"]);
    expect(new Set(consumed.map(current => JSON.stringify(current.interaction.content))).size).toBe(5);
    expect(connector.requests.every(request => request.boundActionId === "EndTurn")).toBe(true);
    expect(new Set(connector.requests.map(request => request.requestId)).size).toBe(5);
    expect(connector.requests.map(request => request.expectedSnapshotId)).toEqual(consumed.map(current => current.snapshot_id));
    expect(runtime.status().autonomy_budget).toMatchObject({ policy_calls_used: 5, submissions_used: 5 });
    await runtime.stop(); await verifyEvidenceDirectory(evidence.directory);
    const events = (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").map(line => JSON.parse(line));
    expect(events.filter(event => event.kind === "decision")).toHaveLength(5);
    expect(events.filter(event => event.kind === "receipt")).toHaveLength(5);
    expect(events.filter(event => event.kind === "controller_released")).toHaveLength(5);
    // Source/test verifier, not a live-game or native causal successor qualification.
    const result = execFileSync(process.execPath, [fileURLToPath(new URL("../../../tools/run-python-component.mjs", import.meta.url)),
      "components/evidence", "-c", "import sys;from pathlib import Path;from sts2_platform_evidence import verify_agent_run_evidence;r=verify_agent_run_evidence(Path(sys.argv[1]));print(r.status);print(r.findings);sys.exit(0 if r.passed else 1)", evidence.directory],
      { encoding: "utf8", timeout: 10000, env: { ...process.env } });
    expect(result).toContain("pass");
  });

  it("never renews its absolute anchor from endlessly changing observations/catalogs or scores", async () => {
    vi.useFakeTimers(); const f = fixture();
    for (const time of [0, 250, 750, 1750, 2750, 3750, 4750, 5750, 6750, 7750, 8750, 9750]) {
      f.clock(time); expect((await f.runtime.tick()).type).toBe("not_delivered");
    }
    expect(f.connector.requests).toHaveLength(12);
    const calls = f.connector.capabilities.mock.calls.length;
    f.clock(9999); expect(await f.runtime.tick()).toMatchObject({ reason: "stale_submission_cooldown" });
    f.clock(10000); expect(await f.runtime.tick({ gameInstanceId: "runtime", recoveryEpoch: 0 })).toMatchObject({ reason: "stale_recovery_exhausted" });
    expect(f.connector.capabilities).toHaveBeenCalledTimes(calls);
    expect(f.policy).toHaveBeenCalledTimes(12);
    expect(f.events.filter(event => event.kind === "handoff_to_human")).toEqual([
      { kind: "handoff_to_human", payload: { reason: "stale_recovery_exhausted" } }]);
    expect(f.events.filter(event => event.kind === "autonomy_budget_exhausted")).toHaveLength(0);
  });

  it("expires idle cooldown through the one owner timer without a second tick or GET", async () => {
    vi.useFakeTimers(); const f = fixture(); await f.runtime.tick();
    expect(vi.getTimerCount()).toBe(1);
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000); await drain();
    expect(f.events.filter(event => event.kind === "handoff_to_human")).toEqual([
      { kind: "handoff_to_human", payload: { reason: "stale_recovery_exhausted" } }]);
    expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "released", tainted: false });
    expect(f.policy).toHaveBeenCalledTimes(1); expect(f.connector.capabilities).toHaveBeenCalledTimes(1);
  });

  it("cancels long scoring at local expiry and rejects its late result before acquiring or submitting", async () => {
    vi.useFakeTimers(); const f = fixture(); await f.runtime.tick();
    const entered = deferred(); const response = deferred<ReturnType<typeof score>>();
    let signal!: AbortSignal;
    f.policy.mockImplementationOnce(async (input, currentSignal?: AbortSignal) => {
      signal = currentSignal!; entered.resolve(); return response.promise;
    });
    f.clock(250); const tick = f.runtime.tick(); await entered.promise;
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000);
    expect(await tick).toMatchObject({ reason: "stale_recovery_exhausted" });
    expect(signal.aborted).toBe(true); expect(f.connector.submit).toHaveBeenCalledTimes(1);
    expect(f.connector.acquireController).toHaveBeenCalledTimes(1);
    response.resolve({ candidate_digest: "a".repeat(64), scores: [1], selected_index: 0 }); await drain();
    expect(f.connector.submit).toHaveBeenCalledTimes(1);
  });

  it.each(["capabilities", "observe", "decision"] as const)("checks local expiry after the %s await before further admission", async boundary => {
    vi.useFakeTimers(); const f = fixture(); await f.runtime.tick(); f.clock(250);
    if (boundary === "capabilities") {
      const capabilities = await f.connector.capabilities();
      f.connector.capabilities.mockImplementationOnce(async () => { f.clock(10000); return capabilities; });
    } else if (boundary === "observe") {
      f.connector.observe.mockImplementationOnce(async () => { f.clock(10000); return { observation: f.connector.current, reads: [] }; });
    } else {
      f.append.mockImplementation(async (kind, payload) => { f.events.push({ kind, payload }); if (kind === "decision") f.clock(10000); });
    }
    expect(await f.runtime.tick()).toMatchObject({ reason: "stale_recovery_exhausted" });
    expect(f.connector.submit).toHaveBeenCalledTimes(1); expect(f.connector.acquireController).toHaveBeenCalledTimes(1);
    expect(f.policy).toHaveBeenCalledTimes(boundary === "decision" ? 2 : 1);
  });

  it("anchors before stale receipt evidence/release latency and clamps cooldown to the remainder", async () => {
    vi.useFakeTimers(); const f = fixture();
    f.append.mockImplementation(async (kind, payload) => { f.events.push({ kind, payload }); if (kind === "receipt") f.clock(9800); });
    await f.runtime.tick(); expect(f.runtime.status().mode).toBe("auto");
    f.clock(9999); expect(await f.runtime.tick()).toMatchObject({ reason: "stale_submission_cooldown" });
    f.clock(10000); expect(await f.runtime.tick()).toMatchObject({ reason: "stale_recovery_exhausted" });
    expect(f.connector.requests).toHaveLength(1);
  });

  it.each([9000, 10000] as const)("lets the original global deadline %s win earlier or equal expiry, with clamped cooldown", async deadlineMs => {
    vi.useFakeTimers(); const f = fixture({ deadlineMs }); await f.runtime.tick();
    f.clock(deadlineMs); await vi.advanceTimersByTimeAsync(deadlineMs); await drain();
    expect(f.events.filter(event => event.kind === "autonomy_budget_exhausted")).toHaveLength(1);
    expect(f.events.filter(event => event.kind === "handoff_to_human")).toHaveLength(0);
    expect(f.runtime.status()).toMatchObject({ mode: "human", autonomy_budget: { exhausted_reason: "deadline" } });
  });

  it.each(["maxPolicyCalls", "maxSubmissions"] as const)("retains the original %s wallet during recovery", async key => {
    vi.useFakeTimers(); const f = fixture({ [key]: 1 }); await f.runtime.tick();
    expect(await f.runtime.tick()).toMatchObject({ reason: "autonomy_budget_exhausted" });
    expect(f.connector.capabilities).toHaveBeenCalledTimes(1); expect(f.policy).toHaveBeenCalledTimes(1);
  });

  it("does not refill on redundant Auto or mode cycling inside the original authorization", async () => {
    vi.useFakeTimers(); const f = fixture(); await f.runtime.tick();
    f.clock(9000); await f.runtime.setMode("auto"); await f.runtime.setMode("shadow"); await f.runtime.setMode("one_step"); await f.runtime.setMode("auto");
    f.clock(10000); expect(await f.runtime.tick()).toMatchObject({ reason: "stale_recovery_exhausted" });
    expect(f.connector.requests).toHaveLength(1);
    await f.runtime.setMode("auto"); expect((await f.runtime.tick()).type).toBe("not_delivered");
    expect(f.runtime.status()).toMatchObject({ mode: "auto", autonomy_budget: { policy_calls_used: 1, submissions_used: 1 } });
  });

  it.each(["receipt", "release"] as const)("does not rearm after local expiry while the stale %s awaits", async boundary => {
    vi.useFakeTimers(); const f = fixture(); const entered = deferred(); const finish = deferred();
    if (boundary === "receipt") f.append.mockImplementation(async (kind, payload) => {
      f.events.push({ kind, payload }); if (kind === "receipt") { entered.resolve(); await finish.promise; }
    });
    else f.connector.releaseController.mockImplementationOnce(async () => { entered.resolve(); await finish.promise; });
    const tick = f.runtime.tick(); await entered.promise; f.clock(10000); await vi.advanceTimersByTimeAsync(10000);
    finish.resolve(); await tick; await drain();
    expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "released", tainted: false });
    expect(f.events.filter(event => event.kind === "handoff_to_human")).toHaveLength(1);
    expect(f.connector.requests).toHaveLength(1);
  });

  it("records a late delivered receipt before local release and cannot revive Auto or refund expiry", async () => {
    vi.useFakeTimers(); const f = fixture(); await f.runtime.tick(); f.clock(250);
    f.policy.mockImplementationOnce(async input => score(input));
    const entered = deferred(); const finish = deferred();
    const submit = f.connector.submit.getMockImplementation()!;
    f.connector.submit.mockImplementationOnce(async input => { entered.resolve(); await finish.promise; return submit(input); });
    const tick = f.runtime.tick(); await entered.promise;
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000);
    expect(f.connector.releaseController).toHaveBeenCalledTimes(1); // The held submission is still owed a real receipt.
    f.clock(60_001); // Even a much later global deadline/read cannot replace the won local expiry.
    expect(f.runtime.status().autonomy_budget).toMatchObject({ state: "inactive", exhausted_reason: null });
    const auto = f.runtime.setMode("auto"); finish.resolve(); await tick; await auto; await drain();
    const receiptIndex = f.events.findIndex(event => event.kind === "receipt" && (event.payload.receipt as { delivery: string }).delivery === "delivered");
    const handoffIndex = f.events.findIndex(event => event.kind === "handoff_to_human");
    expect(receiptIndex).toBeGreaterThan(0); expect(handoffIndex).toBeGreaterThan(receiptIndex);
    expect(f.connector.observe).toHaveBeenCalledTimes(2); // No successor observation after expiry.
    expect(f.events.slice(receiptIndex + 1, handoffIndex).some(event => event.kind === "controller_released")).toBe(true);
    expect(f.runtime.status()).toMatchObject({ mode: "auto", autonomy_budget: { submissions_used: 0, policy_calls_used: 0 } });
    await f.runtime.setMode("human");
  });

  it("taints an unavailable local-expiry handoff append instead of silently confirming evidence", async () => {
    vi.useFakeTimers(); const f = fixture(); await f.runtime.tick();
    f.append.mockImplementation(async (kind, payload) => { if (kind === "handoff_to_human") throw new Error("disk failure"); f.events.push({ kind, payload }); });
    f.clock(10000); await vi.advanceTimersByTimeAsync(10000); await drain();
    expect(f.runtime.status()).toMatchObject({ mode: "human", tainted: true, taint_reason: "stale_recovery_exhaustion_evidence_write_failed" });
    await expect(f.runtime.setMode("auto")).rejects.toThrow("tainted");
  });


  it("does not perform a second whole-bundle observation when its stale refresh wait crosses local expiry", async () => {
    vi.useFakeTimers(); const f = fixture(); await f.runtime.tick(); f.clock(9990);
    f.connector.observe.mockRejectedValueOnce(Object.assign(new Error("stale bundle"), { code: "stale_state" }));
    const tick = f.runtime.tick();
    await drain(); f.clock(10000); await vi.advanceTimersByTimeAsync(25);
    expect(await tick).toMatchObject({ reason: "stale_recovery_exhausted" });
    expect(f.connector.observe).toHaveBeenCalledTimes(2); expect(f.policy).toHaveBeenCalledTimes(1);
  });


  it("seals local expiry with the existing exact handoff payload accepted by the typed Evidence verifier", async () => {
    vi.useFakeTimers(); let clock = 0;
    const connector = new MovingConnector();
    const evidence = await AgentRunEvidence.create({ root: await mkdtemp(join(tmpdir(), "stale-window-expiry-")), policyManifest: manifest(),
      runtimeVersion: "test", runtimeCodeSha256: "e".repeat(64), mode: "auto" });
    await evidence.attestAdapter(manifest().adapter);
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto",
      policy: input => { connector.move(); return score(input); }, evidence, runId: evidence.runId,
      runtimeIdentity: { version: "test", code_sha256: "e".repeat(64) }, monotonicNow: () => clock });
    await runtime.tick(); clock = 10000;
    await vi.advanceTimersByTimeAsync(10000); await drain(); await runtime.stop();
    await verifyEvidenceDirectory(evidence.directory);
    const result = execFileSync(process.execPath, [fileURLToPath(new URL("../../../tools/run-python-component.mjs", import.meta.url)),
      "components/evidence", "-c", "import sys;from pathlib import Path;from sts2_platform_evidence import verify_agent_run_evidence;r=verify_agent_run_evidence(Path(sys.argv[1]));print(r.status);print(r.findings);sys.exit(0 if r.passed else 1)", evidence.directory],
      { encoding: "utf8", timeout: 10000, env: { ...process.env } });
    expect(result).toContain("pass");
    const events = (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").map(line => JSON.parse(line));
    expect(events.filter(event => event.kind === "handoff_to_human").map(event => event.payload)).toEqual([{ reason: "stale_recovery_exhausted" }]);
  });

});
