import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { decodePlayerSnapshot, decodePlayerReceipt, type PlayerEnvironmentSnapshot,
  type PlayerEnvironmentCapabilities } from "@rsgcsg/sts2-connector-client";
import { PolicyRuntime, type SuccessorWaitDiagnostic } from "../src/runtime.js";
import { AgentRunEvidence, verifyEvidenceDirectory } from "../src/evidence.js";
import type { PolicyManifest, PolicyConnector, PolicyDecisionInput, RuntimeMode,
  PublicStatefulPolicyDecisionInput } from "../src/contracts.js";
// @ts-expect-error Production-protocol JSON fixtures shared with actual Python/NDJSON consumers.
import { publicManifest, publicSnapshot, publicCapabilities, publicCompletion } from "./fixtures/public-observation-fixture.mjs";

const manifest = (stateful = false): PolicyManifest => {
  const value = publicManifest() as PolicyManifest;
  if (!stateful) { value.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-1"; value.adapter_config = {}; }
  value.support.interaction_kinds.push("combat_hand_card_selection");
  value.support.action_verbs.push("select");
  return value;
};
const frame = (sequence: number, status: "interactive" | "settling" = "interactive"): PlayerEnvironmentSnapshot => {
  const value = publicSnapshot(sequence, status === "interactive" ? 1 : 0);
  value.status = status;
  if (status === "settling") {
    value.interaction.stage = "settling";
    value.interaction.content.surface.can_end_turn = false;
    value.interaction.content.context.is_play_phase = false;
  }
  return decodePlayerSnapshot(JSON.parse(JSON.stringify(value))).data;
};
function childSelector(sequence: number): PlayerEnvironmentSnapshot {
  const value = frame(sequence);
  value.interaction.kind = "combat_hand_card_selection";
  value.interaction.content_schema = "sts2.player-environment/surface/combat_hand_card_selection-1";
  value.interaction.content.surface = { kind: "combat_hand_card_selection", selection_mode: "simple_select",
    min_select: 1, max_select: 1, selected_count: 0, selected_card_entity_ids: [],
    selectable_card_entity_ids: ["card-choice"], can_confirm: false, is_peeking: false };
  value.interaction.content.context.is_play_phase = false;
  value.referents = [{ referent_id: "card-choice", role: "card", kind: "entity", label: "Strike",
    state: { visible: true, enabled: true, selected: false, observation_basis: "native_visible_fact" } }];
  value.bound_actions.actions = [{ bound_action_id: "select-current-card", interaction_id: "interaction", verb: "select",
    subject_referent_id: "card-choice", arguments: [], label: "Select Strike" }];
  return decodePlayerSnapshot(JSON.parse(JSON.stringify(value))).data;
}
const score = (input: PolicyDecisionInput) => ({ candidate_digest: input.candidate_digest,
  scores: Array(input.candidate_count).fill(1), selected_index: 0 });
function deferred<T = void>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
async function drain() { for (let index = 0; index < 40; index++) await Promise.resolve(); }
afterEach(() => vi.useRealTimers());

function fixture(frames: PlayerEnvironmentSnapshot[], options: { mode?: RuntimeMode; maxAttempts?: number;
  intervalMs?: number; deadlineMs?: number; stateful?: boolean; evidence?: AgentRunEvidence } = {}) {
  let clock = 0, submitted = false;
  let current = frame(1);
  const queue = [...frames];
  const events: { kind: string; payload: Record<string, unknown> }[] = [];
  const diagnostics: SuccessorWaitDiagnostic[] = [];
  const append = vi.fn(async (kind: string, payload: Record<string, unknown>, now?: string) => {
    await options.evidence?.append(kind, payload, now); events.push({ kind, payload });
  });
  const evidence = { append, finalize: async (value: Parameters<AgentRunEvidence["finalize"]>[0]) => {
    await options.evidence?.finalize(value);
  } } as unknown as AgentRunEvidence;
  const observe = vi.fn(async () => {
    if (submitted) current = queue.shift() ?? current;
    return { observation: decodePlayerSnapshot(JSON.parse(JSON.stringify(current))).data, reads: [] };
  });
  const submit = vi.fn(async (input: Parameters<PolicyConnector["submit"]>[0]) => {
    submitted = true;
    return decodePlayerReceipt(JSON.parse(JSON.stringify({ protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1",
      request_id: input.requestId, delivery: "delivered", action: { bound_action_id: input.boundActionId,
        verb: current.bound_actions.actions[0]!.verb, subject_referent_id: current.bound_actions.actions[0]!.subject_referent_id ?? null,
        arguments: [] }, retry: { allowed: false, reason: "never" }, successor: queue[0] ?? null }))).data;
  });
  const connector: PolicyConnector = { capabilities: vi.fn(async () => publicCapabilities() as PlayerEnvironmentCapabilities),
    observeBundle: observe, acquireController: vi.fn(async () => {}), releaseController: vi.fn(async () => {}), submit };
  const policy = vi.fn(async (input: PolicyDecisionInput, _signal?: AbortSignal) => score(input));
  const publicCalls: PublicStatefulPolicyDecisionInput[] = [];
  const publicPolicy = vi.fn((input: PublicStatefulPolicyDecisionInput, _signal: AbortSignal, onOffer: () => void) => {
    publicCalls.push(structuredClone(input)); onOffer();
    return { output: { ...score(input.decision), selected_index: publicCalls.length === 1 ? 0 : null }, completion: publicCompletion(input) };
  });
  const sleep = vi.fn(async (milliseconds: number) => { clock += milliseconds; });
  const runtime = new PolicyRuntime({ manifest: manifest(options.stateful), connector, mode: options.mode ?? "auto",
    policy: options.stateful ? undefined : policy, publicStatefulPolicy: options.stateful ? publicPolicy : undefined,
    evidence, runId: options.evidence?.runId, runtimeIdentity: { version: "fixture", code_sha256: "e".repeat(64) },
    monotonicNow: () => clock, sleep, successorPoll: { maxAttempts: options.maxAttempts ?? 41, baseBackoffMs: options.intervalMs ?? 250 },
    autoBudget: { maxPolicyCalls: 20, maxSubmissions: 10, deadlineMs: options.deadlineMs ?? 60_000 },
    successorWaitDiagnosticSink: record => diagnostics.push(record) });
  return { runtime, connector, observe, submit, policy, publicPolicy, publicCalls, events, append, sleep, diagnostics,
    clock: (value: number) => { clock = value; }, current: () => current, setCurrent: (value: PlayerEnvironmentSnapshot) => { current = value; } };
}

const longSettling = () => Array.from({ length: 54 }, (_, index) => frame(2 + Math.floor(index / 4), "settling"));

describe("generic Auto post-delivery readiness under the original authorization", () => {
  it("keeps one delivered request through >41 decoded repeated newer settling polls, then records an interactive child selector", async () => {
    vi.useFakeTimers();
    const evidence = await AgentRunEvidence.create({ root: await mkdtemp(join(tmpdir(), "auto-readiness-")), policyManifest: manifest(),
      runtimeVersion: "fixture", runtimeCodeSha256: "e".repeat(64), mode: "auto" });
    await evidence.attestAdapter(manifest().adapter);
    const child = childSelector(20);
    const f = fixture([...longSettling(), child], { evidence });
    const result = await f.runtime.tick();
    expect(result).toMatchObject({ type: "delivered", receipt: { delivery: "delivered" }, successor: {
      snapshot_id: child.snapshot_id, interaction: { kind: "combat_hand_card_selection" }, status: "interactive" } });
    expect(f.sleep.mock.calls.reduce((total, [ms]) => total + ms, 0)).toBe(13_500);
    expect(f.observe).toHaveBeenCalledTimes(56); expect(f.policy).toHaveBeenCalledTimes(1); expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.events.filter(event => event.kind === "receipt")).toHaveLength(1);
    expect(f.events.filter(event => event.kind === "successor")).toEqual([
      { kind: "successor", payload: { decision_id: f.runtime.status().last_decision!.decision_id, successor: child } }]);
    expect(f.diagnostics).toEqual([]); // Successful extended waits spend no failure-record budget.
    expect(f.runtime.status()).toMatchObject({ mode: "auto", tainted: false, controller: "held",
      autonomy_budget: { submissions_used: 1, policy_calls_used: 1, elapsed_ms: 13_500 } });
    await f.runtime.stop(); await verifyEvidenceDirectory(evidence.directory);
    const typed = execFileSync(process.execPath, [fileURLToPath(new URL("../../../tools/run-python-component.mjs", import.meta.url)),
      "components/evidence", "-c", "import sys;from pathlib import Path;from sts2_platform_evidence import verify_agent_run_evidence;r=verify_agent_run_evidence(Path(sys.argv[1]));print(r.status);print(r.findings);sys.exit(0 if r.passed else 1)", evidence.directory],
      { encoding: "utf8", timeout: 10000, env: { ...process.env } });
    expect(typed).toContain("pass");
    const durable = (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").map(line => JSON.parse(line));
    expect(durable.filter(event => event.kind === "successor")).toHaveLength(1);
  });

  it("keeps One-Step's original total poll ceiling with no retry of its delivered request", async () => {
    vi.useFakeTimers(); const f = fixture([...longSettling(), childSelector(20)], { mode: "one_step" });
    expect((await f.runtime.tick()).type).toBe("unknown");
    expect(f.runtime.status()).toMatchObject({ tainted: true, taint_reason: "successor_not_stable", controller: "released" });
    expect(f.observe).toHaveBeenCalledTimes(42); expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.sleep).toHaveBeenCalledTimes(40);
    expect(f.diagnostics[0]).toMatchObject({ wait_policy: "legacy_total_poll_limit", limit_scope: "total_polls", max_attempts: 41, poll_count: 41 });
  });

  it.each(["repeated", "changing"] as const)("bounds perpetual %s qualified settling by the original absolute deadline without NN or replay", async kind => {
    vi.useFakeTimers();
    const frames = Array.from({ length: 60 }, (_, index) => frame(kind === "repeated" ? 2 : index + 2, "settling"));
    const f = fixture(frames, { deadlineMs: 11_000 });
    expect(await f.runtime.tick()).toMatchObject({ type: "not_admitted", reason: "autonomy_budget_exhausted" });
    expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "released", tainted: false,
      last_receipt: { delivery: "delivered" }, autonomy_budget: { exhausted_reason: "deadline", elapsed_ms: 11_000, policy_calls_used: 1, submissions_used: 1 } });
    expect(f.observe).toHaveBeenCalledTimes(45); expect(f.policy).toHaveBeenCalledTimes(1); expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.events.filter(event => event.kind === "successor")).toEqual([]);
    expect(f.events.filter(event => event.kind === "autonomy_budget_exhausted")).toHaveLength(1);
    expect(f.diagnostics[0]).toMatchObject({ wait_policy: "auto_public_settling", limit_scope: "cumulative_unusable_polls",
      max_attempts: 41, poll_count: 44, qualified_settling_poll_count: 44, unusable_poll_count: 0, outcome: "cancelled" });
  });

  it.each(["same_basis", "incomplete", "truncated"] as const)("keeps a cumulative finite budget for %s frames", async kind => {
    vi.useFakeTimers();
    const unusable = kind === "same_basis" ? frame(1) : frame(2, "settling");
    if (kind === "incomplete") { unusable.completeness.status = "partial"; unusable.completeness.missing = ["visible_fact"]; }
    if (kind === "truncated") { unusable.bound_actions.status = "truncated"; unusable.bound_actions.total_count = 1; }
    const decoded = decodePlayerSnapshot(JSON.parse(JSON.stringify(unusable))).data;
    const f = fixture(Array.from({ length: 41 }, () => decoded));
    expect((await f.runtime.tick()).type).toBe("unknown");
    expect(f.runtime.status()).toMatchObject({ tainted: true, taint_reason: "successor_not_stable" });
    expect(f.observe).toHaveBeenCalledTimes(42); expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.diagnostics[0]).toMatchObject({ max_attempts: 41, poll_count: 41, unusable_poll_count: 41, qualified_settling_poll_count: 0 });
  });

  it("charges stale whole-bundle acquisition failures once each with no nested refresh or extra submit", async () => {
    vi.useFakeTimers(); const f = fixture([], { maxAttempts: 3 });
    const original = f.observe.getMockImplementation()!;
    let initial = true;
    f.observe.mockImplementation(async () => {
      if (initial) { initial = false; return original(); }
      throw Object.assign(new Error("private stale capture"), { code: "stale_state" });
    });
    expect((await f.runtime.tick()).type).toBe("unknown");
    expect(f.observe).toHaveBeenCalledTimes(4); expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.diagnostics[0]).toMatchObject({ poll_count: 3, unusable_poll_count: 3, qualified_settling_poll_count: 0 });
    expect(JSON.stringify(f.diagnostics)).not.toContain("private stale capture");
  });

  it("does not refill the unusable wallet by alternating trusted settling, changed IDs and bad frames", async () => {
    vi.useFakeTimers();
    const partial = frame(3, "settling"); partial.completeness.status = "partial";
    const f = fixture([frame(1), frame(2, "settling"), partial, frame(4, "settling"), frame(1), childSelector(5)], { maxAttempts: 3 });
    expect((await f.runtime.tick()).type).toBe("unknown");
    expect(f.observe).toHaveBeenCalledTimes(6); expect(f.policy).toHaveBeenCalledTimes(1); expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.diagnostics[0]).toMatchObject({ poll_count: 5, unusable_poll_count: 3, qualified_settling_poll_count: 2, outcome: "exhausted" });
    expect(f.events.filter(event => event.kind === "successor")).toEqual([]);
  });

  it.each(["environment", "sequence", "transport"] as const)("retains immediate strict %s failure and no delivered-request replay", async kind => {
    vi.useFakeTimers(); const invalid = frame(2, "settling");
    if (kind === "environment") invalid.session.environment_fingerprint = "different-environment";
    if (kind === "sequence") { invalid.sequence = 1; invalid.snapshot_id = "different-basis-with-old-sequence"; }
    const f = fixture([decodePlayerSnapshot(JSON.parse(JSON.stringify(invalid))).data]);
    if (kind === "transport") {
      const original = f.observe.getMockImplementation()!; let initial = true;
      f.observe.mockImplementation(async () => { if (initial) { initial = false; return original(); } throw new Error("transport failed"); });
    }
    expect((await f.runtime.tick()).type).toBe("unknown");
    expect(f.runtime.status()).toMatchObject({ mode: "human", tainted: true, last_receipt: { delivery: "delivered" } });
    expect(f.observe).toHaveBeenCalledTimes(2); expect(f.submit).toHaveBeenCalledTimes(1);
    await f.runtime.tick(); expect(f.submit).toHaveBeenCalledTimes(1);
  });

  it("rejects a malformed settling+Cnonempty DTO in the actual SDK instead of inventing a native readiness predicate", () => {
    const malformed = publicSnapshot(2, 1); malformed.status = "settling";
    expect(() => decodePlayerSnapshot(malformed)).toThrow();
  });

  it("uses a positive bounded yield when configured interval is zero, clamped to original remaining time", async () => {
    vi.useFakeTimers(); const f = fixture([frame(2, "settling")], { intervalMs: 0, deadlineMs: 3 });
    expect(await f.runtime.tick()).toMatchObject({ reason: "autonomy_budget_exhausted" });
    expect(f.sleep.mock.calls.map(([ms]) => ms)).toEqual([1, 1, 1]);
    expect(f.observe).toHaveBeenCalledTimes(4); expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.diagnostics[0]).toMatchObject({ interval_ms: 1, poll_count: 3, unusable_poll_count: 0 });
  });

  it.each(["sleep", "acquisition"] as const)("expires the original owner deadline during pending %s without a second tick or GET", async phase => {
    vi.useFakeTimers(); const f = fixture([frame(2, "settling")], { deadlineMs: 100 });
    const entered = deferred(); const finish = deferred();
    if (phase === "sleep") f.sleep.mockImplementationOnce(async () => { entered.resolve(); await finish.promise; });
    else {
      const original = f.observe.getMockImplementation()!; let initial = true;
      f.observe.mockImplementation(async () => { if (initial) { initial = false; return original(); }
        entered.resolve(); await finish.promise; return { observation: frame(2, "settling"), reads: [] }; });
    }
    const tick = f.runtime.tick(); await entered.promise;
    f.clock(100); await vi.advanceTimersByTimeAsync(100);
    expect(await tick).toMatchObject({ reason: "autonomy_budget_exhausted" }); await drain();
    expect(f.events.filter(event => event.kind === "autonomy_budget_exhausted")).toHaveLength(1);
    expect(f.connector.releaseController).toHaveBeenCalledTimes(1);
    expect(f.observe).toHaveBeenCalledTimes(2); expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.diagnostics[0]).toMatchObject({ outcome: "cancelled", poll_count: 1 });
    finish.resolve(); await drain(); expect(f.observe).toHaveBeenCalledTimes(2);
  });

  it.each(["human", "stop"] as const)("honors %s during the existing in-flight pacing wait without another acquisition", async command => {
    vi.useFakeTimers(); const f = fixture([frame(2, "settling")]);
    const entered = deferred(); const finish = deferred();
    f.sleep.mockImplementationOnce(async () => { entered.resolve(); await finish.promise; });
    const tick = f.runtime.tick(); await entered.promise;
    const recovery = command === "human" ? f.runtime.setMode("human") : f.runtime.stop();
    expect((await tick).type).toBe("not_admitted"); await recovery;
    expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "released", tainted: false });
    expect(f.observe).toHaveBeenCalledTimes(2); expect(f.submit).toHaveBeenCalledTimes(1);
    finish.resolve(); await drain(); expect(f.observe).toHaveBeenCalledTimes(2);
  });

  it("fences an old late observation and its cleanup from a new authorization's active policy cancellation", async () => {
    vi.useFakeTimers(); const f = fixture([frame(2, "settling")]);
    const entered = deferred(); const oldObservation = deferred<PlayerEnvironmentSnapshot>();
    const original = f.observe.getMockImplementation()!; let initial = true;
    f.observe.mockImplementation(async () => { if (initial) { initial = false; return original(); }
      entered.resolve(); return { observation: await oldObservation.promise, reads: [] }; });
    const oldTick = f.runtime.tick(); await entered.promise;
    await f.runtime.setMode("human"); await oldTick;
    f.observe.mockImplementation(async () => ({ observation: frame(10), reads: [] }));
    await f.runtime.setMode("auto");
    const newEntered = deferred(); const newScore = deferred<ReturnType<typeof score>>(); let signal!: AbortSignal;
    f.policy.mockImplementationOnce((input, currentSignal?: AbortSignal) => {
      signal = currentSignal!; newEntered.resolve(); return newScore.promise;
    });
    const newTick = f.runtime.tick(); await newEntered.promise;
    oldObservation.resolve(childSelector(20)); await drain();
    expect(f.events.filter(event => event.kind === "successor")).toEqual([]);
    expect(f.submit).toHaveBeenCalledTimes(1);
    await f.runtime.setMode("human"); expect(signal.aborted).toBe(true);
    await newTick; newScore.resolve(score({ candidate_digest: "a".repeat(64), candidate_count: 1 } as PolicyDecisionInput));
    await drain(); expect(f.submit).toHaveBeenCalledTimes(1);
  });

  it("keeps Port4 history and input ordinals untouched while waiting, remembers only a written successor, and offers once", async () => {
    vi.useFakeTimers(); const f = fixture([...longSettling(), childSelector(20)], { stateful: true });
    const first = await f.runtime.tick(); expect(first.type).toBe("delivered");
    expect(f.publicCalls).toHaveLength(1); expect(f.publicCalls[0]!.control).toMatchObject({ observation_ordinal: 1, previous_action: null });
    expect(f.events.filter(event => event.kind === "public_decision_input")).toHaveLength(1);
    const firstRequest = f.submit.mock.calls[0]![0].requestId;
    expect((await f.runtime.tick()).type).toBe("not_executed");
    expect(f.publicCalls[1]!.control).toMatchObject({ observation_ordinal: 2, previous_action: {
      request_id: firstRequest, successor: { snapshot_id: "snapshot-20", sequence: 20 } } });
    expect(f.events.filter(event => event.kind === "successor")).toHaveLength(1);
    expect(f.submit).toHaveBeenCalledTimes(1);
    await f.runtime.setMode("auto"); f.setCurrent(frame(21));
    expect((await f.runtime.tick()).type).toBe("not_executed");
    expect(f.publicCalls[2]!.control.previous_action).toBeNull();
  });

  it.each(["stop", "deadline", "evidence"] as const)("offers no Port4 history after waiting ends at %s", async boundary => {
    vi.useFakeTimers(); const f = fixture([frame(2, "settling"), childSelector(3)], { stateful: true, deadlineMs: 250 });
    if (boundary === "stop") f.sleep.mockImplementationOnce(async () => { void f.runtime.stop(); });
    else if (boundary === "evidence") f.append.mockImplementation(async (kind, payload) => {
      if (kind === "successor") throw new Error("successor evidence unavailable"); f.events.push({ kind, payload });
    });
    if (boundary === "evidence") { f.clock(0); f.sleep.mockImplementationOnce(async () => {}); }
    await f.runtime.tick(); expect(f.publicCalls).toHaveLength(1);
    expect(f.publicCalls[0]!.control.previous_action).toBeNull();
    expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.events.filter(event => event.kind === "successor")).toHaveLength(0);
    if (boundary === "evidence") expect(f.runtime.status().tainted).toBe(true);
  });

  it.each(["unknown", "throw"] as const)("never enters readiness or retries after %s delivery", async kind => {
    vi.useFakeTimers(); const f = fixture(longSettling());
    f.submit.mockImplementationOnce(async input => {
      if (kind === "throw") throw new Error("delivery transport lost");
      return decodePlayerReceipt({ protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1", request_id: input.requestId,
        delivery: "unknown", action: { bound_action_id: input.boundActionId, verb: "end_turn", arguments: [] },
        retry: { allowed: false, reason: "never" }, successor: null }).data;
    });
    expect((await f.runtime.tick()).type).toBe("unknown"); await f.runtime.tick();
    expect(f.observe).toHaveBeenCalledTimes(1); expect(f.submit).toHaveBeenCalledTimes(1); expect(f.diagnostics).toEqual([]);
  });

  it("retains unconfirmed release/taint after a won deadline and blocks new authorization", async () => {
    vi.useFakeTimers(); const f = fixture([frame(2, "settling")], { deadlineMs: 250 });
    vi.spyOn(f.connector, "releaseController").mockRejectedValue(new Error("release unavailable"));
    await f.runtime.tick(); expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "held", tainted: true });
    await expect(f.runtime.setMode("auto")).rejects.toThrow(); expect(f.submit).toHaveBeenCalledTimes(1);
  });

  it("reports >41 actual polls honestly while retaining only bounded scalar diagnostic rows", async () => {
    vi.useFakeTimers(); const f = fixture([...Array.from({ length: 70 }, () => frame(2, "settling")), ...Array.from({ length: 41 }, () => frame(1))]);
    expect((await f.runtime.tick()).type).toBe("unknown");
    expect(f.diagnostics).toHaveLength(1);
    const record = f.diagnostics[0]!;
    expect(record).toMatchObject({ wait_policy: "auto_public_settling", limit_scope: "cumulative_unusable_polls",
      max_attempts: 41, poll_count: 111, qualified_settling_poll_count: 70, unusable_poll_count: 41,
      rows_total: 111, rows_dropped: 47, truncated: true });
    if (record.diagnostic !== "successor_wait_failure") throw new Error("expected wait failure");
    expect(record.polls).toHaveLength(64); expect(record.polls.at(-1)!.attempt).toBe(111);
    expect(Buffer.byteLength(JSON.stringify(record)) + 1).toBeLessThanOrEqual(32 * 1024);
    expect(JSON.stringify(record)).not.toContain("can_end_turn");
    expect(f.observe).toHaveBeenCalledTimes(112); expect(f.submit).toHaveBeenCalledTimes(1);
  });

});
