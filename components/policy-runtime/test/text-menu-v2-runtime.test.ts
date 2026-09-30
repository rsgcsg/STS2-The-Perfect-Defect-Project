import { readFileSync } from "node:fs";
import { PassThrough } from "node:stream";
import { describe, expect, it, vi } from "vitest";
import {
  decodeTextMenuV2ActionResult, decodeTextMenuV2Snapshot,
  type TextMenuV2Action, type TextMenuV2ActionResult,
  type TextMenuV2Capabilities, type TextMenuV2Snapshot
} from "@rsgcsg/sts2-connector-client";
import { PolicyRuntime } from "../src/runtime.js";
import { candidateOrderDigest } from "../src/digest.js";
import { serveStatefulPolicyPort } from "../src/policy-port.js";
import { type PolicyConnector, type PolicyManifest, validatePolicyManifest } from "../src/contracts.js";

const sdkFixture = (name: string): unknown => JSON.parse(readFileSync(
  new URL(`../../connector/sdk/typescript/test/fixtures/${name}.json`, import.meta.url), "utf8"));
const root = () => decodeTextMenuV2Snapshot(sdkFixture("text-menu-v2-targeted-root")).data;
const cardSelected = () => decodeTextMenuV2ActionResult(sdkFixture("text-menu-v2-targeted-select"), root()).data;

function manifest(): PolicyManifest {
  return {
    schema: "sts2.policy-runtime/policy-manifest-1", manifest_id: "text-v2-test",
    policy: { id: "test", version: "1", provider: "test", architecture: "test" },
    adapter: { id: "test", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-2", code_sha256: "c".repeat(64) },
    artifact: { id: "test", path: "test", sha256: "a".repeat(64) },
    representation: { id: "text-v2", version: "2", input_schema: "sts2.player-environment/text-menu-snapshot-2" },
    requirements: { connector_protocol_version: "1.0.0", environment: { host_kind: "test", connector_version: "1",
      connector_source_revision: "source", connector_artifact_sha256: "b".repeat(64), connector_module_version_id: "mvid",
      modset_status: "exact", modset_fingerprint: "modset", loaded_mod_ids: ["fixture-mod"] },
      reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-menu-action-id-order",
      score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
    support: { game_versions: ["fixture-game"], game_commits: ["fixture-commit"], interaction_kinds: ["combat_turn"],
      action_verbs: ["select_card", "select_target", "cancel_selection", "play"] },
    adapter_config: {}, claims: { full_run: false, selector: false, catalog_filtered: false,
      creates_action_authority: false, creates_native_operands: false }
  };
}

function capabilities(): TextMenuV2Capabilities {
  return {
    protocol_version: "1.0.0", snapshot_schema: "sts2.player-environment/text-menu-snapshot-2",
    action_schema: "sts2.player-environment/action-1", receipt_schema: "sts2.player-environment/text-menu-action-result-2",
    control_schema: "sts2.player-environment/control-1", input_profile: "text-menu-v2", status: "ready",
    host: { id: "fixture", name: "fixture", version: "1", runtime_instance_id: "runtime-1", host_kind: "test",
      implementation: { source_revision: "source", module_version_id: "mvid", artifact_sha256: "b".repeat(64) } },
    game: { version: "fixture-game", commit: "fixture-commit", branch: null, main_assembly_hash: null,
      compatibility: { status: "exact", observation_allowed: true, detail: "fixture" },
      modset: { status: "exact", fingerprint: "modset", scope: "fixture", loaded_mod_ids: ["fixture-mod"], detail: "fixture" } },
    environment_fingerprint: "environment-1", verbs: ["select_card", "select_target", "cancel_selection", "play"],
    snapshot_bound: true, single_controller: true, execution_available: true,
    control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
  } as TextMenuV2Capabilities;
}

function targetSelected(previous: TextMenuV2Snapshot): TextMenuV2ActionResult {
  const target = previous.menu_actions.actions.find(action => action.verb === "select_target")!;
  const play: TextMenuV2Action = { action_id: "v2-play-card-C-target-E", kind: "native_input", verb: "play",
    label: "Play Strike on Jaw Worm", subject_referent_id: "card-C",
    arguments: [{ role: "target", referent_id: "enemy-E" }], effect_domain: "native_input" };
  const cancel = previous.menu_actions.actions.find(action => action.verb === "cancel_selection")!;
  const successor = decodeTextMenuV2Snapshot({ ...previous, snapshot_id: "text-confirm", sequence: previous.sequence + 1,
    menu: { ...previous.menu, cursor: "card_confirmation", revision: previous.menu.revision + 1,
      selection: [...previous.menu.selection, { role: "target", referent_id: "enemy-E" }] },
    menu_actions: { ...previous.menu_actions, actions: [play, cancel] } }).data;
  return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
    schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
    request_id: "target-request", status: "applied", effect_domain: "text_menu", native_delivery: null,
    action: target, reason_code: null, detail: null, retry: "never", successor, attribution: null }, previous).data;
}

function fixture() {
  let current = root();
  let successor = current;
  const events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
  const submit = vi.fn(async (input: Parameters<PolicyConnector["submit"]>[0]) => {
    expect(input.inputProfile).toBe("text-menu-v2");
    expect(input.previousSnapshot?.snapshot_id).toBe(current.snapshot_id);
    expect(input.expectedSnapshotId).toBe(current.snapshot_id);
    const action = current.menu_actions.actions.find(item => item.action_id === input.boundActionId)!;
    let result: TextMenuV2ActionResult;
    if (action.verb === "select_card") {
      const selected = cardSelected();
      result = { ...selected, request_id: input.requestId };
    } else if (action.verb === "select_target") {
      result = { ...targetSelected(current), request_id: input.requestId };
    } else {
      result = { protocol_version: "1.0.0", schema: "sts2.player-environment/text-menu-action-result-2",
        input_profile: "text-menu-v2", request_id: input.requestId, status: "applied", effect_domain: "native_input",
        native_delivery: "delivered", action, reason_code: null, detail: null, retry: "never", successor: null, attribution: null };
      successor = decodeTextMenuV2Snapshot({ ...root(), snapshot_id: "text-native-next", sequence: current.sequence + 1,
        menu: { ...root().menu, native_snapshot_id: "managed-source-21" } }).data;
    }
    result = decodeTextMenuV2ActionResult(result, current).data;
    if (result.successor) current = result.successor;
    return result;
  });
  const context = vi.fn(async (profile?: string) => {
    expect(profile).toBe("text-menu-v2");
    return { schema: "sts2.player-environment/text-menu-observation-context-2" as const,
      snapshot: current, game_continuity_id: "game-1" };
  });
  const connector: PolicyConnector = {
    capabilities: vi.fn(async () => capabilities()),
    observeBundle: vi.fn(async (_reads: readonly string[], profile?: string) => { expect(profile).toBe("text-menu-v2"); return { observation: successor, reads: [] as [] }; }),
    observeTextMenuContext: context, acquireController: vi.fn(async () => {}),
    releaseController: vi.fn(async () => {}), submit
  };
  return { connector, context, submit, events, current: () => current,
    setCurrent: (page: TextMenuV2Snapshot) => { current = page; },
    evidence: { append: async (kind: string, payload: Record<string, unknown>) => { events.push({ kind, payload }); } } as never };
}

describe("explicit text-menu-v2 Runtime consumer", () => {
  it("keeps old manifests and port-2 v1 valid but rejects v2 on port-1", () => {
    expect(validatePolicyManifest(manifest()).representation.input_schema).toBe("sts2.player-environment/text-menu-snapshot-2");
    const legacy = manifest(); legacy.representation.input_schema = "sts2.player-environment/text-menu-snapshot-1";
    expect(validatePolicyManifest(legacy).adapter.protocol).toBe("sts2.policy-runtime/decision-only-ndjson-2");
    const wrongPort = manifest(); wrongPort.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-1";
    expect(() => validatePolicyManifest(wrongPort)).toThrow(/requires stateful policy port/u);
  });

  it("scores complete current catalogs through select card, select target, then one native leaf", async () => {
    const f = fixture();
    const offered: Array<{ ids: string[]; count: number; snapshot: string; reads: number; token: string }> = [];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence: f.evidence,
      runId: "v2-flow", runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      successorPoll: { maxAttempts: 1, baseBackoffMs: 0 }, autoBudget: { maxSubmissions: 3, maxPolicyCalls: 3, deadlineMs: 60_000 },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer();
        const ids = input.bundle.observation.schema === "sts2.player-environment/snapshot-1" ? []
          : input.bundle.observation.menu_actions.actions.map(action => action.action_id);
        offered.push({ ids, count: input.candidate_count, snapshot: input.bundle.observation.snapshot_id,
          reads: input.bundle.reads.length, token: input.continuity_token });
        return { output: { candidate_digest: input.candidate_digest, scores: ids.map((_id, i) => -i), selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    expect((await runtime.tick()).type).toBe("navigated");
    expect((await runtime.tick()).type).toBe("navigated");
    const third = await runtime.tick();
    expect(third.type).toBe("text_native_delivered");
    expect(offered.map(item => item.ids)).toEqual([
      ["v2-select-card-C"], ["v2-select-target-E", "v2-cancel-card-C"],
      ["v2-play-card-C-target-E", "v2-cancel-card-C"]
    ]);
    expect(offered.every(item => item.ids.length === item.count && item.reads === 0)).toBe(true);
    expect(f.events.filter(event => event.kind === "decision").map(event =>
      (event.payload.decision as { candidate_digest: string }).candidate_digest)).toEqual(
      offered.map(item => candidateOrderDigest(item.ids)));
    expect(new Set(offered.map(item => item.token)).size).toBe(1);
    expect(f.submit).toHaveBeenCalledTimes(3);
    expect(f.events.filter(event => event.kind === "menu_navigation")).toHaveLength(2);
    expect(f.events.filter(event => event.kind === "text_native_delivery")).toHaveLength(1);
    expect(f.events.filter(event => event.kind === "text_decision_input")
      .every(event => Object.keys(event.payload).sort().join(",") === "decision_id,snapshot")).toBe(true);
    expect(f.events.filter(event => event.kind === "text_observed_successor")).toHaveLength(1);
    expect(f.events.filter(event => event.kind === "text_menu_dispatch_attempt").map(event => [
      event.payload.native_submissions_used, event.payload.menu_navigations_used
    ])).toEqual([[0, 1], [0, 2], [1, 2]]);
    expect(runtime.status()).toMatchObject({ mode: "auto", tainted: false, autonomy_budget: { submissions_used: 3, policy_calls_used: 3 } });
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(runtime.status().mode).toBe("human");
  });

  it("keeps Human passive and Shadow score-only", async () => {
    const f = fixture();
    const score = vi.fn(async (input: Parameters<NonNullable<ConstructorParameters<typeof PolicyRuntime>[0]["statefulPolicy"]>>[0], _signal: AbortSignal, onOffer: () => void) => {
      onOffer();
      return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 },
        completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
          sequence: input.bundle.observation.sequence } };
    });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, statefulPolicy: score });
    expect((await runtime.tick()).type).toBe("human");
    expect(f.context).not.toHaveBeenCalled();
    await runtime.setMode("shadow");
    expect((await runtime.tick()).type).toBe("shadow");
    expect(score).toHaveBeenCalledTimes(1);
    expect(f.submit).not.toHaveBeenCalled();
  });

  it("retains the exact unknown native action, taints, and never revives it", async () => {
    const f = fixture();
    const selected = cardSelected().successor!;
    const confirmation = targetSelected(selected).successor!;
    f.setCurrent(confirmation);
    f.submit.mockImplementationOnce(async input => {
      const action = confirmation.menu_actions.actions[0]!;
      return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
        schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
        request_id: input.requestId, status: "unknown", effect_domain: "native_input", native_delivery: "unknown",
        action, reason_code: "native_delivery_unknown", detail: null, retry: "never", successor: null, attribution: null }, confirmation).data;
    });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) }, statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); return { output: { candidate_digest: input.candidate_digest, scores: [1, 0], selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    const result = await runtime.tick();
    expect(result.type).toBe("unknown");
    if (result.type === "unknown") expect(result.receipt).toMatchObject({ action: confirmation.menu_actions.actions[0], retry: "never" });
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: true });
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.events.some(event => event.kind === "text_native_unknown")).toBe(true);
  });

  it("reobserves a stale v2 intent and makes a fresh decision with new IDs", async () => {
    const f = fixture();
    const confirmation = targetSelected(cardSelected().successor!).successor!;
    f.setCurrent(confirmation);
    const fresh = decodeTextMenuV2Snapshot({ ...root(), snapshot_id: "text-fresh-root",
      sequence: confirmation.sequence + 1, menu: { ...root().menu, native_snapshot_id: "native-fresh" } }).data;
    f.submit.mockImplementationOnce(async input => {
      f.setCurrent(fresh);
      return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
        schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
        request_id: input.requestId, status: "not_applied", effect_domain: null, native_delivery: null,
        action: null, reason_code: "stale_snapshot", detail: null,
        retry: "reobserve", successor: fresh, attribution: null }, confirmation).data;
    });
    f.submit.mockImplementationOnce(async input => {
      const selected = cardSelected();
      const next = decodeTextMenuV2Snapshot({ ...selected.successor!, snapshot_id: "text-fresh-card",
        sequence: fresh.sequence + 1, menu: { ...selected.successor!.menu,
          native_snapshot_id: fresh.menu.native_snapshot_id } }).data;
      f.setCurrent(next);
      return decodeTextMenuV2ActionResult({ ...selected, request_id: input.requestId,
        successor: next }, fresh).data;
    });
    const offered: string[] = [];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); offered.push(input.bundle.observation.snapshot_id);
        return { output: { candidate_digest: input.candidate_digest,
          scores: input.bundle.observation.schema === "sts2.player-environment/snapshot-1" ? []
            : input.bundle.observation.menu_actions.actions.map((_action, i) => -i), selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status()).toMatchObject({ mode: "auto", tainted: false });
    expect(f.connector.releaseController).toHaveBeenCalledTimes(1);
    expect((await runtime.tick()).type).toBe("navigated");
    expect(offered).toEqual([confirmation.snapshot_id, fresh.snapshot_id]);
    expect(f.submit).toHaveBeenCalledTimes(2);
    expect(f.submit.mock.calls[0]![0].requestId).not.toBe(f.submit.mock.calls[1]![0].requestId);
    expect(f.submit.mock.calls[1]![0].expectedSnapshotId).toBe(fresh.snapshot_id);
    expect(f.events.filter(event => event.kind === "text_menu_not_applied")).toHaveLength(1);
    expect(f.events.filter(event => event.kind === "menu_navigation")).toHaveLength(1);
  });

  it("hands off after three consecutive v2 stale submissions", async () => {
    const f = fixture();
    let ordinal = 0;
    f.submit.mockImplementation(async input => {
      ordinal += 1;
      const next = decodeTextMenuV2Snapshot({ ...root(), snapshot_id: `text-stale-${ordinal}`,
        sequence: root().sequence + ordinal, menu: { ...root().menu,
          native_snapshot_id: `native-stale-${ordinal}` },
        menu_actions: { ...root().menu_actions, actions: root().menu_actions.actions.map(action =>
          ({ ...action, action_id: `v2-select-card-${ordinal}` })) } }).data;
      f.setCurrent(next);
      return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
        schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
        request_id: input.requestId, status: "not_applied", effect_domain: null,
        native_delivery: null, action: null, reason_code: "stale_snapshot", detail: null,
        retry: "reobserve", successor: next, attribution: null }).data;
    });
    const policy = vi.fn(async (input: Parameters<NonNullable<ConstructorParameters<typeof PolicyRuntime>[0]["statefulPolicy"]>>[0],
      _signal: AbortSignal, onOffer: () => void) => {
      onOffer(); return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 },
        completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
          sequence: input.bundle.observation.sequence } };
    });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) }, statefulPolicy: policy });
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status().mode).toBe("auto");
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status().mode).toBe("auto");
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: false });
    expect(f.submit).toHaveBeenCalledTimes(3);
    expect(policy).toHaveBeenCalledTimes(3);
    expect(f.connector.releaseController).toHaveBeenCalledTimes(3);
    expect((await runtime.tick()).type).toBe("human");
    expect(f.submit).toHaveBeenCalledTimes(3);
  });

  it("resets the consecutive stale count after an applied text selection", async () => {
    const f = fixture();
    let submitted = 0;
    f.submit.mockImplementation(async input => {
      submitted += 1;
      const previous = f.current();
      if (submitted === 2) {
        const selected = cardSelected();
        const next = decodeTextMenuV2Snapshot({ ...selected.successor!, snapshot_id: "text-reset-selected",
          sequence: previous.sequence + 1, menu: { ...selected.successor!.menu,
            native_snapshot_id: previous.menu.native_snapshot_id } }).data;
        f.setCurrent(next);
        return decodeTextMenuV2ActionResult({ ...selected, request_id: input.requestId,
          successor: next }, previous).data;
      }
      const next = decodeTextMenuV2Snapshot({ ...root(), snapshot_id: `text-reset-${submitted}`,
        sequence: previous.sequence + 1, menu: { ...root().menu,
          native_snapshot_id: `native-reset-${submitted}` } }).data;
      f.setCurrent(next);
      return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
        schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
        request_id: input.requestId, status: "not_applied", effect_domain: null,
        native_delivery: null, action: null, reason_code: "stale_snapshot", detail: null,
        retry: "reobserve", successor: next, attribution: null }, previous).data;
    });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); const count = input.bundle.observation.schema === "sts2.player-environment/snapshot-1" ? 0
          : input.bundle.observation.menu_actions.actions.length;
        return { output: { candidate_digest: input.candidate_digest,
          scores: Array.from({ length: count }, (_item, i) => -i), selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect((await runtime.tick()).type).toBe("navigated");
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status()).toMatchObject({ mode: "auto", tainted: false });
    expect(f.submit).toHaveBeenCalledTimes(4);
  });

  it.each([
    ["nonstale", "menu_action_not_current", "reobserve", null],
    ["no-reobserve", "stale_snapshot", "never", null],
    ["native-not-delivered", "stale_snapshot", "reobserve", "not_delivered"],
    ["no-evidence-writer", "stale_snapshot", "reobserve", null]
  ] as const)("hands %s back to Human", async (name, reason, retry, nativeDelivery) => {
    const f = fixture();
    const confirmation = targetSelected(cardSelected().successor!).successor!;
    f.setCurrent(confirmation);
    f.submit.mockImplementationOnce(async input => decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
      schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
      request_id: input.requestId, status: "not_applied", effect_domain: "native_input",
      native_delivery: nativeDelivery, action: confirmation.menu_actions.actions[0],
      reason_code: reason, detail: null, retry, successor: null, attribution: null }, confirmation).data);
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto",
      ...(name === "no-evidence-writer" ? {} : { evidence: f.evidence,
        runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) } }),
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); return { output: { candidate_digest: input.candidate_digest, scores: [1, 0], selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: false });
    expect(f.submit).toHaveBeenCalledTimes(1);
  });

  it.each(["evidence", "release"] as const)("does not continue after %s failure", async failure => {
    const f = fixture();
    f.submit.mockImplementationOnce(async input => decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
      schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
      request_id: input.requestId, status: "not_applied", effect_domain: null, native_delivery: null,
      action: null, reason_code: "stale_snapshot", detail: null, retry: "reobserve",
      successor: root(), attribution: null }).data);
    if (failure === "release") vi.mocked(f.connector.releaseController).mockRejectedValueOnce(new Error("release failed"));
    const evidence = failure === "evidence" ? {
      append: async (kind: string) => { if (kind === "text_menu_not_applied") throw new Error("write failed"); }
    } as never : f.evidence;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    if (failure === "release") await expect(runtime.tick()).rejects.toThrow(/release failed/u);
    else expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: true });
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(f.submit).toHaveBeenCalledTimes(1);
  });

  it("does not continue when Human cancels during the stale evidence append", async () => {
    const f = fixture();
    f.submit.mockImplementationOnce(async input => decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
      schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
      request_id: input.requestId, status: "not_applied", effect_domain: null, native_delivery: null,
      action: null, reason_code: "stale_snapshot", detail: null, retry: "reobserve",
      successor: root(), attribution: null }).data);
    let appendStarted!: () => void;
    let finishAppend!: () => void;
    const started = new Promise<void>(resolve => { appendStarted = resolve; });
    const appendGate = new Promise<void>(resolve => { finishAppend = resolve; });
    const evidence = { append: async (kind: string) => {
      if (kind === "text_menu_not_applied") { appendStarted(); await appendGate; }
    } } as never;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    const pending = runtime.tick();
    await started;
    const human = runtime.setMode("human");
    finishAppend();
    expect((await pending).type).toBe("text_not_applied");
    await human;
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: false });
    expect((await runtime.tick()).type).toBe("human");
    expect(f.submit).toHaveBeenCalledTimes(1);
  });

  it("offers a confirmed interaction once across a stale v2 decision", async () => {
    const f = fixture();
    const third = decodeTextMenuV2Snapshot({ ...root(), snapshot_id: "text-third",
      sequence: root().sequence + 3, menu: { ...root().menu, native_snapshot_id: "native-third" } }).data;
    let submitted = 0;
    f.submit.mockImplementation(async input => {
      submitted += 1;
      if (submitted === 1) {
        const selected = cardSelected(); f.setCurrent(selected.successor!);
        return decodeTextMenuV2ActionResult({ ...selected, request_id: input.requestId }, root()).data;
      }
      if (submitted === 2) {
        f.setCurrent(third);
        return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
          schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
          request_id: input.requestId, status: "not_applied", effect_domain: null,
          native_delivery: null, action: null, reason_code: "stale_snapshot", detail: null,
          retry: "reobserve", successor: third, attribution: null }, cardSelected().successor!).data;
      }
      const selected = cardSelected();
      const next = decodeTextMenuV2Snapshot({ ...selected.successor!, snapshot_id: "text-third-selected",
        sequence: third.sequence + 1, menu: { ...selected.successor!.menu,
          native_snapshot_id: third.menu.native_snapshot_id } }).data;
      f.setCurrent(next);
      return decodeTextMenuV2ActionResult({ ...selected, request_id: input.requestId, successor: next }, third).data;
    });
    const m = manifest(); m.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-3";
    const feedback: Array<string | null> = [];
    const runtime = new PolicyRuntime({ manifest: m, connector: f.connector, mode: "auto", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); const prior = input.previous_interaction?.request_id ?? null;
        feedback.push(prior);
        const count = input.bundle.observation.schema === "sts2.player-environment/snapshot-1" ? 0
          : input.bundle.observation.menu_actions.actions.length;
        return { output: { candidate_digest: input.candidate_digest,
          scores: Array.from({ length: count }, (_item, i) => -i), selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence, previous_interaction_request_id: prior } };
      } });
    expect((await runtime.tick()).type).toBe("navigated");
    const firstRequest = f.submit.mock.calls[0]![0].requestId;
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect((await runtime.tick()).type).toBe("navigated");
    expect(feedback).toEqual([null, firstRequest, null]);
    expect(f.events.filter(event => event.kind === "menu_navigation")).toHaveLength(2);
    expect(f.events.filter(event => event.kind === "text_menu_not_applied")).toHaveLength(1);
  });

  it("port-2 accepts a strict v2 bundle with exact count and digest", async () => {
    const input = new PassThrough();
    const output = new PassThrough();
    const policy = vi.fn(async (request: Parameters<NonNullable<ConstructorParameters<typeof PolicyRuntime>[0]["statefulPolicy"]>>[0]) => ({
      output: { candidate_digest: request.candidate_digest, scores: [1], selected_index: 0 },
      completion: { continuity_token: request.continuity_token, snapshot_id: request.bundle.observation.snapshot_id,
        sequence: request.bundle.observation.sequence }
    }));
    const serving = serveStatefulPolicyPort(policy, input, output);
    const page = root();
    const decisionInput = { run_id: "run", manifest: manifest(), bundle: { observation: page, reads: [] },
      candidate_digest: candidateOrderDigest(page.menu_actions.actions), candidate_count: 1, continuity_token: "token-1" };
    const reply = () => new Promise<Record<string, unknown>>(resolve => output.once("data", chunk => resolve(JSON.parse(String(chunk)))));
    const accepted = reply();
    input.write(JSON.stringify({ schema: "sts2.policy-runtime/policy-port-2", message_type: "decide",
      request_id: "exact", input: decisionInput }) + "\n");
    expect(await accepted).toMatchObject({ message_type: "decision", request_id: "exact",
      completion: { continuity_token: "token-1", snapshot_id: page.snapshot_id } });
    const rejected = reply();
    input.write(JSON.stringify({ schema: "sts2.policy-runtime/policy-port-2", message_type: "decide",
      request_id: "wrong-digest", input: { ...decisionInput, candidate_digest: "0".repeat(64) } }) + "\n");
    expect(await rejected).toMatchObject({ message_type: "error", request_id: "wrong-digest" });
    expect(policy).toHaveBeenCalledTimes(1);
    input.end();
    await serving;
  });
});
