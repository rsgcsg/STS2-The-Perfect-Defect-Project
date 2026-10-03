import { describe, expect, it, vi } from "vitest";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import type { PlayerEnvironmentBoundAction, PlayerEnvironmentCapabilities, PlayerEnvironmentReceipt, PlayerEnvironmentSnapshot } from "@rsgcsg/sts2-connector-client";
import { candidateOrderDigest } from "../src/digest.js";
import { POLICY_PORT_V4_SCHEMA, validatePolicyManifest, type PolicyManifest, type PolicyConnector, type PublicStatefulPolicyDecisionInput } from "../src/contracts.js";
import type { AgentRunEvidence } from "../src/evidence.js";
import { NdjsonPolicyPort, servePublicStatefulPolicyPort } from "../src/policy-port.js";
import { PolicyRuntime } from "../src/runtime.js";

const action = (id: string, label = id): PlayerEnvironmentBoundAction => ({ bound_action_id: id, verb: "end_turn", interaction_id: "interaction", arguments: [], label });
function snapshot(snapshotId = "snapshot-a", sequence = 7): PlayerEnvironmentSnapshot {
  return {
    protocol_version: "1.0.0", schema: "sts2.player-environment/snapshot-1", snapshot_id: snapshotId, sequence,
    observed_at: "2026-10-03T00:00:00.000Z", status: "interactive", persistent: null,
    interaction: { interaction_id: "interaction", kind: "test", stage: "ready", content_schema: "sts2.player-environment/surface/test-1", content: { surface: { kind: "test" }, context: { kind: "test" } }, capabilities: [] },
    referents: [], bound_actions: { schema: "sts2.player-environment/bound-actions-1", status: "complete", materialized_count: 1, total_count: 1, limit: 1, ordering_semantics: "connector_order", actions: [action("bound-a")] }, reads: [],
    completeness: { status: "complete", visible_information: "fixture", interaction_discovery: "fixture", missing: [], hidden_by_policy: [] },
    session: { runtime_instance_id: "runtime", environment_fingerprint: "environment" },
    information_policy: { id: "test", scope: "test", includes_hidden_information: false, unknown_field_behavior: "reject" }
  };
}

function manifest(): PolicyManifest {
  return {
    schema: "sts2.policy-runtime/policy-manifest-1", manifest_id: "public-m2-test",
    policy: { id: "public-m2", version: "1", provider: "fixture", architecture: "stateful-fixture" },
    adapter: { id: "public-m2-port4", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-4", code_sha256: "c".repeat(64) },
    artifact: { id: "artifact", path: "artifact.bin", sha256: "a".repeat(64) },
    representation: { id: "public-snapshot", version: "1", input_schema: "sts2.player-environment/snapshot-1" },
    requirements: { connector_protocol_version: "1.0.0", environment: { host_kind: "test", connector_version: "1", connector_source_revision: "source", connector_artifact_sha256: "b".repeat(64), connector_module_version_id: "mvid", modset_status: "exact", modset_fingerprint: "modset", loaded_mod_ids: ["fixture-mod"] }, reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-bound-action-id-order", score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
    support: { game_versions: ["fixture-game"], game_commits: ["fixture-commit"], interaction_kinds: ["test"], action_verbs: ["end_turn"] },
    adapter_config: { public_stateful_profile: "stpd/public-m2-observation-only-v1" },
    claims: { full_run: false, selector: false, catalog_filtered: false, creates_action_authority: false, creates_native_operands: false }
  };
}

class FixtureConnector implements PolicyConnector {
  current = snapshot();
  submitCount = 0;
  observeCount = 0;
  delivery: PlayerEnvironmentReceipt["delivery"] = "not_delivered";
  async capabilities(): Promise<PlayerEnvironmentCapabilities> {
    return {
      protocol_version: "1.0.0", snapshot_schema: "sts2.player-environment/snapshot-1", action_schema: "sts2.player-environment/action-1", receipt_schema: "sts2.player-environment/receipt-1", control_schema: "sts2.player-environment/control-1", status: "ready",
      host: { id: "fixture", name: "fixture", version: "1", runtime_instance_id: this.current.session.runtime_instance_id, host_kind: "test", implementation: { source_revision: "source", module_version_id: "mvid", artifact_sha256: "b".repeat(64) } },
      game: { version: "fixture-game", commit: "fixture-commit", branch: null, main_assembly_hash: null, compatibility: { status: "exact", observation_allowed: true, detail: "fixture" }, modset: { status: "exact", fingerprint: "modset", scope: "fixture", loaded_mod_ids: ["fixture-mod"], detail: "fixture" } },
      environment_fingerprint: this.current.session.environment_fingerprint, verbs: ["end_turn"], snapshot_bound: true, single_controller: true, execution_available: true, control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
    };
  }
  async observeBundle() { this.observeCount += 1; return { observation: this.current, reads: [] }; }
  async acquireController() {}
  async releaseController() {}
  async submit(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }): Promise<PlayerEnvironmentReceipt> {
    this.submitCount += 1;
    const successor = this.delivery === "delivered" ? snapshot(`successor-${this.current.sequence + 1}`, this.current.sequence + 1) : null;
    if (successor) this.current = successor;
    return { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1", request_id: input.requestId, delivery: this.delivery,
      action: { bound_action_id: input.boundActionId, verb: "end_turn", arguments: [] },
      reason_code: this.delivery === "not_delivered" ? "stale_snapshot" : null,
      retry: { allowed: this.delivery === "not_delivered", reason: "fresh_snapshot_required" }, successor };
  }
}

function evidence(events: Array<{ kind: string; payload: Record<string, unknown> }> = []): AgentRunEvidence {
  return { append: vi.fn(async (kind: string, payload: Record<string, unknown>) => { events.push({ kind, payload }); }), finalize: vi.fn(async () => {}) } as unknown as AgentRunEvidence;
}
function completion(input: PublicStatefulPolicyDecisionInput) {
  return { continuity_token: input.continuity_token, episode_id: input.episode_id, segment_id: input.segment_id,
    observation_ordinal: input.observation_ordinal, snapshot_id: input.bundle.observation.snapshot_id,
    sequence: input.bundle.observation.sequence, previous_action_request_id: input.previous_action?.request_id ?? null };
}
function runtime(connector: FixtureConnector, policy: (input: PublicStatefulPolicyDecisionInput) => ReturnType<typeof completion> | Promise<ReturnType<typeof completion>>, opts: { timeout?: number; events?: Array<{ kind: string; payload: Record<string, unknown> }> } = {}) {
  return new PolicyRuntime({ manifest: manifest(), connector, runId: "public-run", evidence: evidence(opts.events),
    runtimeIdentity: { version: "test", code_sha256: "d".repeat(64) }, policyTimeoutMs: opts.timeout,
    publicStatefulPolicy: async (input) => ({ output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }, completion: await policy(input) }) });
}

describe("public Snapshot stateful protocol 4", () => {
  it("requires explicit Runtime-owned segment creation and keeps scope separate from its token", async () => {
    const connector = new FixtureConnector(), seen: PublicStatefulPolicyDecisionInput[] = [];
    const rt = runtime(connector, async input => { seen.push(input); return completion(input); });
    expect(validatePolicyManifest(manifest()).adapter.protocol).toBe("sts2.policy-runtime/decision-only-ndjson-4");
    const bad = manifest(); bad.adapter_config = {};
    expect(() => validatePolicyManifest(bad)).toThrow(/observation-only public M2 profile/);
    await expect(rt.setMode("auto")).rejects.toThrow(/explicitly begun/);
    const begun = await rt.beginPublicStatefulSegment("bounded_policy_segment");
    expect(begun.scope).toBe("bounded_policy_segment");
    await expect(rt.beginPublicStatefulSegment("bounded_policy_segment")).rejects.toThrow(/already active/);
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("shadow");
    expect(seen[0]).toMatchObject({ episode_scope: "bounded_policy_segment", episode_id: begun.episode_id, segment_id: begun.segment_id, observation_ordinal: 1 });
    expect(seen[0]!.continuity_token).not.toBe(begun.episode_id);
    const ended = await rt.endPublicStatefulSegment();
    expect(ended).toEqual(begun);
    await expect(rt.setMode("auto")).rejects.toThrow(/explicitly begun/);
  });

  it("reuses an ordinal for a polled identity, ignores only observed_at, and accepts sequence gaps with a new identity", async () => {
    const connector = new FixtureConnector(), ordinals: number[] = [];
    const rt = runtime(connector, async input => { ordinals.push(input.observation_ordinal); return completion(input); });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    expect((await rt.tick()).type).toBe("not_delivered");
    connector.current = { ...connector.current, observed_at: "2026-10-03T00:00:01.000Z" };
    expect((await rt.tick()).type).toBe("not_delivered");
    connector.current = { ...connector.current, snapshot_id: "return-to-same-page", sequence: 19 };
    expect((await rt.tick()).type).toBe("not_delivered");
    expect(ordinals).toEqual([1, 1, 2]);
    await rt.setMode("human");
  });

  it("fails closed when one snapshot identity is reused with a conflicting canonical body", async () => {
    const connector = new FixtureConnector(), ordinals: number[] = [];
    const rt = runtime(connector, async input => { ordinals.push(input.observation_ordinal); return completion(input); });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("shadow");
    connector.current = { ...connector.current, bound_actions: { ...connector.current.bound_actions, actions: [action("bound-a", "changed visible label")] } };
    const rejected = await rt.tick();
    expect(rejected).toMatchObject({ type: "not_admitted", reason: "public_snapshot_identity_conflict" });
    expect(ordinals).toEqual([1]);
    expect(connector.submitCount).toBe(0);
    expect(rt.status().mode).toBe("human");
  });

  it("keeps previous-action feedback stable for a same-observation submission retry", async () => {
    const connector = new FixtureConnector(), seen: PublicStatefulPolicyDecisionInput[] = [];
    connector.delivery = "delivered";
    const rt = runtime(connector, async input => { seen.push(input); return completion(input); });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    const first = await rt.tick();
    expect(first.type).toBe("delivered");
    if (first.type !== "delivered") throw new Error("fixture action was not delivered");
    connector.delivery = "not_delivered";
    expect((await rt.tick()).type).toBe("not_delivered");
    expect((await rt.tick()).type).toBe("not_delivered");
    expect(seen.map(input => input.observation_ordinal)).toEqual([1, 2, 2]);
    expect(seen[2]!.previous_action).toEqual(seen[1]!.previous_action);
    expect(seen[1]!.previous_action?.request_id).toBe(first.receipt.request_id);
    await rt.setMode("human");
  });

  it("fails closed when a new Snapshot moves to a different Connector session", async () => {
    const connector = new FixtureConnector(), rt = runtime(connector, async input => completion(input));
    await rt.beginPublicStatefulSegment("single_game_episode");
    await rt.setMode("auto");
    expect((await rt.tick()).type).toBe("not_delivered");
    connector.current = { ...connector.current, snapshot_id: "new-session-page", sequence: 8,
      session: { runtime_instance_id: "runtime-restarted", environment_fingerprint: "new-environment" } };
    await expect(rt.tick()).resolves.toMatchObject({ type: "not_admitted", reason: "public_stateful_session_identity_drift" });
    expect(connector.submitCount).toBe(1);
    expect(rt.status().mode).toBe("human");
  });

  it("ends the segment when mode evidence fails so recovery needs an explicit begin", async () => {
    const connector = new FixtureConnector(), events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    let failNextModeEvent = true;
    const ev = { append: vi.fn(async (kind: string, payload: Record<string, unknown>) => {
      if (kind === "mode_changed" && failNextModeEvent) { failNextModeEvent = false; throw new Error("fixture evidence failure"); }
      events.push({ kind, payload });
    }), finalize: vi.fn(async () => {}) } as unknown as AgentRunEvidence;
    const rt = new PolicyRuntime({ manifest: manifest(), connector, runId: "evidence-failure", evidence: ev,
      runtimeIdentity: { version: "test", code_sha256: "d".repeat(64) },
      publicStatefulPolicy: async input => ({ output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }, completion: completion(input) }) });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await expect(rt.setMode("shadow")).rejects.toThrow(/mode change failed closed/);
    expect(events.some(event => event.kind === "public_stateful_episode_ended" && event.payload.reason === "mode_change_evidence_write_failed")).toBe(true);
    await expect(rt.setMode("auto")).rejects.toThrow(/explicitly begun/);
  });

  it("passes only a delivered action receipt and verified successor as next-call feedback", async () => {
    const connector = new FixtureConnector(), seen: PublicStatefulPolicyDecisionInput[] = [];
    connector.delivery = "delivered";
    const rt = runtime(connector, async input => { seen.push(input); return completion(input); });
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    expect((await rt.tick()).type).toBe("delivered");
    expect((await rt.tick()).type).toBe("delivered");
    expect(seen[1]!.previous_action).toMatchObject({
      source_snapshot_id: "snapshot-a", bound_action_id: "bound-a",
      receipt: { delivery: "delivered" }, successor: { snapshot_id: "successor-8", sequence: 8 }
    });
    await rt.setMode("human");
  });

  it("does not retry an unknown delivery under the public stateful port", async () => {
    const connector = new FixtureConnector(), rt = runtime(connector, async input => completion(input));
    connector.delivery = "unknown";
    await rt.beginPublicStatefulSegment("bounded_policy_segment");
    await rt.setMode("auto");
    expect((await rt.tick()).type).toBe("unknown");
    expect((await rt.tick()).type).toBe("not_admitted");
    expect(connector.submitCount).toBe(1);
    expect(rt.status().tainted).toBe(true);
  });

  it("rotates token and segment after timeout; a late old completion cannot submit", async () => {
    const connector = new FixtureConnector(), events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    let firstInput: PublicStatefulPolicyDecisionInput | null = null;
    let resolveFirst!: (value: ReturnType<typeof completion>) => void;
    const rt = new PolicyRuntime({ manifest: manifest(), connector, runId: "late-run", evidence: evidence(events),
      runtimeIdentity: { version: "test", code_sha256: "d".repeat(64) }, policyTimeoutMs: 10,
      publicStatefulPolicy: async input => {
        if (!firstInput) {
          firstInput = input;
          return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }, completion: await new Promise(resolve => { resolveFirst = resolve; }) };
        }
        return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: null }, completion: completion(input) };
      } });
    await rt.beginPublicStatefulSegment("single_game_episode");
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("not_admitted");
    const old = firstInput!;
    const nextSegment = await rt.beginPublicStatefulSegment("single_game_episode");
    await rt.setMode("shadow");
    expect((await rt.tick()).type).toBe("shadow");
    expect(events.some(event => event.kind === "public_stateful_observation_segment_reset" && event.payload.memory_continuity === false)).toBe(true);
    expect(events.some(event => event.kind === "public_stateful_episode_ended" && event.payload.requires_explicit_begin === true)).toBe(true);
    resolveFirst(completion(old));
    await new Promise<void>(resolve => setImmediate(resolve));
    expect(connector.submitCount).toBe(0);
    expect(nextSegment.episode_id).not.toBe(old.episode_id);
  });

  it("accepts only v4 completion watermarks and drops a late response after request cancellation", async () => {
    const writes: string[] = [];
    const stdin = new Writable({ write(chunk, _encoding, callback) { writes.push(chunk.toString()); callback(); } });
    const stdout = new PassThrough(), stderr = new PassThrough();
    const child = Object.assign(new EventEmitter(), { stdin, stdout, stderr, exitCode: null, kill: vi.fn(() => true) });
    const port = new NdjsonPolicyPort(child as unknown as ConstructorParameters<typeof NdjsonPolicyPort>[0]);
    stdout.write(`${JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "ready", adapter: manifest().adapter })}\n`);
    await port.ready();
    const observation = snapshot();
    const input: PublicStatefulPolicyDecisionInput = { run_id: "run", manifest: manifest(), bundle: { observation, reads: [] },
      candidate_digest: candidateOrderDigest(["bound-a"]), candidate_count: 1, continuity_token: "token-a",
      episode_scope: "bounded_policy_segment", episode_id: "episode-a", segment_id: "segment-a", observation_ordinal: 1, previous_action: null };
    try {
      const first = port.decideV4(input, new AbortController().signal, () => {});
      const firstId = (JSON.parse(writes[0]!) as { request_id: string }).request_id;
      const done = { continuity_token: input.continuity_token, episode_id: input.episode_id, segment_id: input.segment_id,
        observation_ordinal: input.observation_ordinal, snapshot_id: observation.snapshot_id, sequence: observation.sequence, previous_action_request_id: null };
      stdout.write(`${JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "decision", request_id: firstId,
        output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }, completion: done })}\n`);
      await expect(first).resolves.toMatchObject({ completion: done });

      const controller = new AbortController();
      const late = port.decideV4(input, controller.signal, () => {});
      const lateId = (JSON.parse(writes[1]!) as { request_id: string }).request_id;
      const rejected = expect(late).rejects.toThrow("cancelled");
      controller.abort();
      await rejected;
      stdout.write(`${JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "decision", request_id: lateId,
        output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }, completion: done })}\n`);
      await new Promise<void>(resolve => setImmediate(resolve));
      expect(child.kill).not.toHaveBeenCalled();
    } finally { port.close(); stdin.destroy(); stdout.destroy(); stderr.destroy(); }
  });

  it("validates a v4 child request against generic Snapshot and the complete ordered catalog", async () => {
    const inputStream = new PassThrough(), outputStream = new PassThrough();
    const seen: PublicStatefulPolicyDecisionInput[] = [];
    const policyServer = servePublicStatefulPolicyPort(async input => {
      seen.push(input);
      return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }, completion: completion(input) };
    }, inputStream, outputStream);
    const observation = snapshot();
    const input: PublicStatefulPolicyDecisionInput = { run_id: "run", manifest: manifest(), bundle: { observation, reads: [] },
      candidate_digest: candidateOrderDigest(["bound-a"]), candidate_count: 1, continuity_token: "token-a",
      episode_scope: "single_game_episode", episode_id: "episode-a", segment_id: "segment-a", observation_ordinal: 3, previous_action: null };
    const responseLine = new Promise<string>(resolve => outputStream.once("data", chunk => resolve(chunk.toString().trim())));
    inputStream.write(`${JSON.stringify({ schema: POLICY_PORT_V4_SCHEMA, message_type: "decide", request_id: "request-a", input })}\n`);
    const response = JSON.parse(await responseLine) as { message_type: string; completion: ReturnType<typeof completion> };
    expect(response.message_type).toBe("decision");
    expect(response.completion).toEqual(completion(input));
    expect(seen).toHaveLength(1);
    inputStream.end();
    await policyServer;
    outputStream.destroy();
  });
});
