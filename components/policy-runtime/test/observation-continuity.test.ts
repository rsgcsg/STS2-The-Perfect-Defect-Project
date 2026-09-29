import { describe, expect, it, vi } from "vitest";
import { createServer } from "node:http";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import type { TextMenuAction, TextMenuActionResult, TextMenuSnapshot, TextMenuCapabilities } from "@rsgcsg/sts2-connector-client";
import { PlayerEnvironmentRestClient } from "@rsgcsg/sts2-connector-client";
import { ConnectorPolicyClient } from "../src/connector.js";
import { candidateOrderDigest } from "../src/digest.js";
import { type PolicyConnector, type PolicyManifest, type StatefulPolicyDecisionInput, POLICY_PORT_V2_SCHEMA, validatePolicyManifest } from "../src/contracts.js";
import { NdjsonPolicyPort, serveStatefulPolicyPort } from "../src/policy-port.js";
import { PolicyRuntime } from "../src/runtime.js";
import { startPolicyRuntimeHttpServer } from "../src/server.js";

const action: TextMenuAction = { action_id: "nav-info", kind: "system_navigation", verb: "open_information", label: "Information", subject_referent_id: null, arguments: [], effect_domain: "text_menu" };
function page(sequence: number): TextMenuSnapshot {
  return { protocol_version: "1.0.0", schema: "sts2.player-environment/text-menu-snapshot-1", input_profile: "text-menu-v1", snapshot_id: `text-${sequence}`, sequence,
    observed_at: "2026-09-29T00:00:00.000Z", status: "interactive", persistent: null,
    interaction: { interaction_id: "interaction", kind: "combat_turn", stage: "ready", content_schema: "sts2.player-environment/surface/combat_turn-1", content: { surface: { kind: "combat_turn", room_entity_id: "room", can_end_turn: false, playable_cards: [], usable_potions: [] }, context: { kind: "combat", encounter_type: "normal", round: 1, turn_owner: "player", is_play_phase: true, player: {}, enemies: [] } }, capabilities: [] },
    referents: [], menu: { cursor: "root", revision: sequence, native_snapshot_id: "native" },
    menu_actions: { status: "complete", materialized_count: 1, total_count: 1, ordering_semantics: "connector_order", actions: [action] },
    completeness: { status: "complete", visible_information: "test", interaction_discovery: "test", missing: [], hidden_by_policy: [] },
    session: { runtime_instance_id: "runtime", environment_fingerprint: "environment" },
    information_policy: { id: "test", scope: "test", includes_hidden_information: false, unknown_field_behavior: "reject" } } as TextMenuSnapshot;
}
function manifest(): PolicyManifest {
  return { schema: "sts2.policy-runtime/policy-manifest-1", manifest_id: "v2-test", policy: { id: "test", version: "1", provider: "test", architecture: "test" },
    adapter: { id: "test", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-2", code_sha256: "c".repeat(64) },
    artifact: { id: "test", path: "test", sha256: "a".repeat(64) }, representation: { id: "text", version: "1", input_schema: "sts2.player-environment/text-menu-snapshot-1" },
    requirements: { connector_protocol_version: "1.0.0", environment: { host_kind: "test", connector_version: "1", connector_source_revision: "source", connector_artifact_sha256: "b".repeat(64), connector_module_version_id: "mvid", modset_status: "exact", modset_fingerprint: "modset", loaded_mod_ids: ["fixture-mod"] }, reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-menu-action-id-order", score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
    support: { game_versions: ["fixture-game"], game_commits: ["fixture-commit"], interaction_kinds: ["combat_turn"], action_verbs: ["open_information"] }, adapter_config: {},
    claims: { full_run: false, selector: false, catalog_filtered: false, creates_action_authority: false, creates_native_operands: false } };
}
function capabilities(): TextMenuCapabilities {
  return { protocol_version: "1.0.0", snapshot_schema: "sts2.player-environment/text-menu-snapshot-1", action_schema: "sts2.player-environment/action-1", receipt_schema: "sts2.player-environment/text-menu-action-result-1", control_schema: "sts2.player-environment/control-1", input_profile: "text-menu-v1", status: "ready",
    host: { id: "fixture", name: "fixture", version: "1", runtime_instance_id: "runtime", host_kind: "test", implementation: { source_revision: "source", module_version_id: "mvid", artifact_sha256: "b".repeat(64) } },
    game: { version: "fixture-game", commit: "fixture-commit", branch: null, main_assembly_hash: null, compatibility: { status: "exact", observation_allowed: true, detail: "fixture" }, modset: { status: "exact", fingerprint: "modset", scope: "fixture", loaded_mod_ids: ["fixture-mod"], detail: "fixture" } },
    environment_fingerprint: "environment", verbs: [], snapshot_bound: true, single_controller: true, execution_available: true, control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: [] } as TextMenuCapabilities;
}
function output(input: StatefulPolicyDecisionInput, selected_index: number | null = null) {
  return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index },
    completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id, sequence: input.bundle.observation.sequence } };
}
function fixture() {
  let current = page(1), gameId: string | null = "native-run-1";
  const observe = vi.fn(async () => ({ schema: "sts2.player-environment/text-menu-observation-context-1" as const, game_continuity_id: gameId, snapshot: current }));
  const submit = vi.fn(async (input: { requestId: string }): Promise<TextMenuActionResult> => ({ protocol_version: "1.0.0", schema: "sts2.player-environment/text-menu-action-result-1", input_profile: "text-menu-v1",
    request_id: input.requestId, status: "not_applied", effect_domain: "text_menu", native_delivery: null, action, reason_code: "fixture_not_applied", detail: null, retry: "never", successor: null, attribution: null }));
  const connector: PolicyConnector = { capabilities: async () => capabilities(), observeBundle: vi.fn(async () => { throw new Error("legacy GET must not run"); }), observeTextMenuContext: observe,
    acquireController: vi.fn(async () => {}), releaseController: vi.fn(async () => {}), submit };
  return { connector, observe, submit, next(sequence: number, id = gameId) { current = page(sequence); gameId = id; } };
}

describe("model-neutral observation continuity v2", () => {
  it("requires a text-menu manifest with no Reads and leaves the v1 protocol valid", () => {
    expect(validatePolicyManifest(manifest())).toEqual(manifest());
    expect(() => validatePolicyManifest({ ...manifest(), requirements: { ...manifest().requirements, reads: ["run_deck"] } })).toThrow();
    expect(() => validatePolicyManifest({ ...manifest(), representation: { ...manifest().representation, input_schema: "sts2.player-environment/snapshot-1" } })).toThrow();
    const legacy = manifest(); legacy.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-1";
    expect(validatePolicyManifest(legacy)).toEqual(legacy);
  });

  it("keeps a known completed observation through repeated One-Step Human handoffs, then rotates on new game", async () => {
    const f = fixture(), tokens: string[] = [];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "human", runId: "run", statefulPolicy: async (input) => { tokens.push(input.continuity_token); return output(input); } });
    await runtime.setMode("one_step"); expect((await runtime.tick()).type).toBe("not_executed");
    expect(runtime.status().mode).toBe("human");
    f.next(2); await runtime.setMode("one_step"); expect((await runtime.tick()).type).toBe("not_executed");
    expect(tokens[1]).toBe(tokens[0]);
    f.next(3, "native-run-2"); await runtime.setMode("one_step"); expect((await runtime.tick()).type).toBe("not_executed");
    expect(tokens[2]).not.toBe(tokens[1]);
    expect(f.observe).toHaveBeenCalledTimes(3);
    expect(f.connector.observeBundle).not.toHaveBeenCalled();
  });

  it("keeps continuity after a known not-applied action in the same game", async () => {
    const f = fixture(), tokens: string[] = [];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "one_step", runId: "run",
      statefulPolicy: async input => { tokens.push(input.continuity_token); return output(input, 0); } });
    expect((await runtime.tick()).type).toBe("text_not_applied");
    f.next(2); await runtime.setMode("one_step");
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(tokens[1]).toBe(tokens[0]);
    expect(f.submit).toHaveBeenCalledTimes(2);
  });

  it("rotates after an offered cancellation and refuses missing game identity", async () => {
    const f = fixture(), tokens: string[] = [];
    let entered!: () => void;
    const offered = new Promise<void>(resolve => { entered = resolve; });
    let calls = 0;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "one_step", runId: "run", statefulPolicy: async (input) => {
      tokens.push(input.continuity_token);
      if (++calls === 1) { entered(); return await new Promise<ReturnType<typeof output>>(() => {}); }
      return output(input);
    } });
    const pending = runtime.tick(); await offered;
    await runtime.setMode("human");
    expect((await pending).type).toBe("not_admitted");
    f.next(2); await runtime.setMode("one_step"); expect((await runtime.tick()).type).toBe("not_executed");
    expect(tokens[1]).not.toBe(tokens[0]);
    f.next(3, null); await runtime.setMode("one_step");
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "game_continuity_unavailable" });
    expect(tokens).toHaveLength(2);
  });

  it("returns Human to a second HTTP client without waiting for the first client's scoring", async () => {
    const f = fixture(), tokens: string[] = [];
    let entered!: () => void;
    const offered = new Promise<void>(resolve => { entered = resolve; });
    let calls = 0;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "human", runId: "cross-client-v2",
      statefulPolicy: async input => {
        tokens.push(input.continuity_token);
        if (++calls === 1) { entered(); return await new Promise<ReturnType<typeof output>>(() => {}); }
        return output(input);
      } });
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0, autoDrive: false });
    const headers = { "content-type": "application/json", "x-sts2-policy-run-id": "cross-client-v2" };
    const post = (route: string, body: object, epoch?: number) => fetch(`${service.address}${route}`, { method: "POST",
      headers: epoch === undefined ? headers : { ...headers, "x-sts2-recovery-epoch": String(epoch) }, body: JSON.stringify(body) });
    try {
      expect((await post("/v2/mode", { mode: "one_step" })).status).toBe(200);
      const pending = post("/v2/tick", { max_ticks: 1 }); await offered;
      const human = await post("/v2/mode", { mode: "human" });
      expect(human.status).toBe(200);
      expect((await pending).status).toBe(200);
      f.next(2);
      expect((await post("/v2/mode", { mode: "one_step" }, 1)).status).toBe(200);
      expect((await post("/v2/tick", { max_ticks: 1 }, 1)).status).toBe(200);
      expect(tokens[1]).not.toBe(tokens[0]);
    } finally { await service.close(); }
  });

  it("rejects a wrong completion watermark before accepting a decision", async () => {
    const f = fixture(), tokens: string[] = [];
    let calls = 0;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "one_step", runId: "run", statefulPolicy: async input => {
      tokens.push(input.continuity_token);
      const result = output(input);
      if (++calls === 1) result.completion.snapshot_id = "wrong";
      return result;
    } });
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "policy_failed" });
    // failClosed is terminal for this Runtime instance; recoverable Human
    // cancellation and its next token are tested above.
    expect(runtime.status().mode).toBe("human");
    expect(tokens).toHaveLength(1);
  });

  it("uses the SDK's atomic context HTTP route and does not fall back to legacy snapshot GET on 404", async () => {
    const paths: string[] = [];
    const server = createServer((request, response) => {
      paths.push(request.url ?? ""); response.statusCode = 404; response.end("missing");
    });
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    try {
      const address = server.address(); if (address === null || typeof address === "string") throw new Error("missing address");
      const client = new PlayerEnvironmentRestClient(`http://127.0.0.1:${address.port}`, 1_000);
      const connector = new ConnectorPolicyClient(client);
      await expect(connector.observeTextMenuContext()).rejects.toThrow();
      expect(paths).toEqual(["/api/player-environment/text-menu/observation-context"]);
    } finally { await new Promise<void>(resolve => server.close(() => resolve())); }
  });

  it("binds a real v2 child reply to request id, digest, count, token and page, ignoring a cancelled late reply", async () => {
    const adapter = manifest().adapter;
    const script = [
      `process.stdout.write(JSON.stringify({schema:'${POLICY_PORT_V2_SCHEMA}',message_type:'ready',adapter:${JSON.stringify(adapter)}})+'\\n');`,
      "const readline=require('node:readline').createInterface({input:process.stdin});",
      "let old;const emit=(r)=>process.stdout.write(JSON.stringify({schema:r.schema,message_type:'decision',request_id:r.request_id,output:{candidate_digest:r.input.candidate_digest,scores:[1],selected_index:null},completion:{continuity_token:r.input.continuity_token,snapshot_id:r.input.bundle.observation.snapshot_id,sequence:r.input.bundle.observation.sequence}})+'\\n');",
      "readline.on('line',(line)=>{const r=JSON.parse(line);if(!old){old=r;return;}emit(old);setImmediate(()=>emit(r));});"
    ].join("");
    const port = NdjsonPolicyPort.spawn(process.execPath, ["-e", script]);
    try {
      await expect(port.attest(adapter)).resolves.toEqual(adapter);
      const input = (sequence: number, token: string): StatefulPolicyDecisionInput => ({ run_id: "run", manifest: manifest(), bundle: { observation: page(sequence), reads: [] }, candidate_count: 1, candidate_digest: candidateOrderDigest([action.action_id]), continuity_token: token });
      const abort = new AbortController(); const offered = vi.fn();
      const old = port.decideV2(input(1, "old"), abort.signal, offered); abort.abort();
      await expect(old).rejects.toThrow("cancelled"); expect(offered).toHaveBeenCalledOnce();
      const result = await port.decideV2(input(2, "new"), new AbortController().signal, vi.fn());
      expect(result.completion).toEqual({ continuity_token: "new", snapshot_id: "text-2", sequence: 2 });
    } finally { port.close(); }
  });

  it("aborts an offered port request on timeout and fails closed before any next offer", async () => {
    const f = fixture();
    let aborted = false;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "one_step", runId: "run", policyTimeoutMs: 5,
      statefulPolicy: async (_input, signal, onOffer) => {
        onOffer(); signal.addEventListener("abort", () => { aborted = true; }, { once: true });
        return await new Promise<ReturnType<typeof output>>(() => {});
      }, statefulOfferBoundary: "port_write" });
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "policy_failed" });
    expect(aborted).toBe(true);
    expect(runtime.status().mode).toBe("human");
  });

  it("closes a nonresponding child when cancelled-request tombstones exceed the bound", async () => {
    const adapter = manifest().adapter;
    const script = [
      `process.stdout.write(JSON.stringify({schema:'${POLICY_PORT_V2_SCHEMA}',message_type:'ready',adapter:${JSON.stringify(adapter)}})+'\\n');`,
      "const readline=require('node:readline').createInterface({input:process.stdin});readline.on('line',()=>{});"
    ].join("");
    const port = NdjsonPolicyPort.spawn(process.execPath, ["-e", script]);
    const input: StatefulPolicyDecisionInput = { run_id: "run", manifest: manifest(), bundle: { observation: page(1), reads: [] },
      candidate_count: 1, candidate_digest: candidateOrderDigest([action.action_id]), continuity_token: "token" };
    try {
      await port.attest(adapter);
      for (let index = 0; index < 257; index += 1) {
        const controller = new AbortController();
        const pending = port.decideV2(input, controller.signal, () => {});
        controller.abort();
        await expect(pending).rejects.toThrow("cancelled");
      }
      await expect(port.decideV2(input, new AbortController().signal, () => {})).rejects.toThrow("closed");
    } finally { port.close(); }
  });

  it("distinguishes pre-offer serialization and abort from a synchronous partial write", async () => {
    const stdin = new PassThrough(), stdout = new PassThrough(), stderr = new PassThrough();
    const child = Object.assign(new EventEmitter(), { stdin, stdout, stderr, exitCode: null, killed: false,
      kill: vi.fn(() => true) });
    const port = new NdjsonPolicyPort(child as unknown as ConstructorParameters<typeof NdjsonPolicyPort>[0]);
    stdout.write(JSON.stringify({ schema: POLICY_PORT_V2_SCHEMA, message_type: "ready", adapter: manifest().adapter }) + "\n");
    await port.ready();
    const input: StatefulPolicyDecisionInput = { run_id: "run", manifest: manifest(), bundle: { observation: page(1), reads: [] },
      candidate_count: 1, candidate_digest: candidateOrderDigest([action.action_id]), continuity_token: "token" };
    const offered = vi.fn();
    try {
      const cyclic: Record<string, unknown> = {}; cyclic.self = cyclic;
      await expect(port.decideV2({ ...input, manifest: { ...input.manifest, adapter_config: cyclic } }, new AbortController().signal, offered)).rejects.toThrow();
      expect(offered).not.toHaveBeenCalled();
      const preAborted = new AbortController(); preAborted.abort();
      await expect(port.decideV2(input, preAborted.signal, offered)).rejects.toThrow("cancelled");
      expect(offered).not.toHaveBeenCalled();
      stdin.write = (() => { throw new Error("partial write"); }) as typeof stdin.write;
      await expect(port.decideV2(input, new AbortController().signal, offered)).rejects.toThrow("partial write");
      expect(offered).toHaveBeenCalledOnce();

      const f = fixture(); let token = "";
      const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "one_step", runId: "run",
        statefulOfferBoundary: "port_write", statefulPolicy: (decision, signal, onOffer) => {
          token = decision.continuity_token; return port.decideV2(decision, signal, onOffer);
        } });
      expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "policy_failed" });
      const continuity = (runtime as unknown as { continuity: { token: string } | null }).continuity;
      expect(continuity?.token).toBeDefined();
      expect(continuity?.token).not.toBe(token);
    } finally { port.close(); }
  });

  it("rejects incomplete or drifted v2 child input before calling the stateful policy", async () => {
    const input = new PassThrough(), outputStream = new PassThrough();
    const replies: string[] = [];
    outputStream.setEncoding("utf8"); outputStream.on("data", chunk => replies.push(String(chunk)));
    const policy = vi.fn(async (value: StatefulPolicyDecisionInput) => output(value));
    const serving = serveStatefulPolicyPort(policy, input, outputStream);
    const requestInput: StatefulPolicyDecisionInput = { run_id: "run", manifest: manifest(), bundle: { observation: page(1), reads: [] },
      candidate_count: 1, candidate_digest: candidateOrderDigest([action.action_id]), continuity_token: "token" };
    const wire = (request_id: string, candidate_digest: string) => JSON.stringify({ schema: POLICY_PORT_V2_SCHEMA, message_type: "decide", request_id,
      input: { ...requestInput, candidate_digest } }) + "\n";
    input.write(wire("bad", "0".repeat(64))); input.write(wire("good", requestInput.candidate_digest)); input.end();
    await serving;
    const parsed = replies.join("").trim().split("\n").map(line => JSON.parse(line) as { message_type: string; request_id: string });
    expect(parsed).toMatchObject([{ message_type: "error", request_id: "bad" }, { message_type: "decision", request_id: "good" }]);
    expect(policy).toHaveBeenCalledTimes(1);
  });

  it("does not enter a queued v2 observation until the first child call completes", async () => {
    const input = new PassThrough(), outputStream = new PassThrough();
    const replies: string[] = [], entered: number[] = [];
    outputStream.setEncoding("utf8"); outputStream.on("data", chunk => replies.push(String(chunk)));
    let firstEntered!: () => void, releaseFirst!: () => void;
    const atFirst = new Promise<void>(resolve => { firstEntered = resolve; });
    const barrier = new Promise<void>(resolve => { releaseFirst = resolve; });
    const serving = serveStatefulPolicyPort(async value => {
      const sequence = value.bundle.observation.sequence;
      entered.push(sequence);
      if (sequence === 1) { firstEntered(); await barrier; }
      return output(value);
    }, input, outputStream);
    const wire = (sequence: number, request_id: string) => JSON.stringify({ schema: POLICY_PORT_V2_SCHEMA,
      message_type: "decide", request_id, input: { run_id: "run", manifest: manifest(), bundle: { observation: page(sequence), reads: [] },
        candidate_count: 1, candidate_digest: candidateOrderDigest([action.action_id]), continuity_token: "same-game" } }) + "\n";
    try {
      input.write(wire(1, "first")); input.write(wire(2, "second")); input.end();
      await atFirst;
      await new Promise<void>(resolve => setImmediate(resolve));
      expect(entered).toEqual([1]);
    } finally { releaseFirst(); }
    await serving;
    expect(entered).toEqual([1, 2]);
    expect(replies.join("").trim().split("\n").map(line => {
      const response = JSON.parse(line) as { request_id: string; completion: { sequence: number } };
      return [response.request_id, response.completion.sequence];
    })).toEqual([["first", 1], ["second", 2]]);
  });
});
