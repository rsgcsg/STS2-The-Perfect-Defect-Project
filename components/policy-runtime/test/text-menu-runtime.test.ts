import { describe, expect, it, vi } from "vitest";
import type { TextMenuAction, TextMenuActionResult, TextMenuCapabilities, TextMenuSnapshot } from "@rsgcsg/sts2-connector-client";
import { PolicyRuntime } from "../src/runtime.js";
import { type PolicyConnector, type PolicyManifest } from "../src/contracts.js";

const nav: TextMenuAction = { action_id: "nav-information", kind: "system_navigation", verb: "open_information", label: "Information", subject_referent_id: null, arguments: [], effect_domain: "text_menu" };
const native: TextMenuAction = { action_id: "native-end", kind: "native_input", verb: "end_turn", label: "End turn", subject_referent_id: null, arguments: [], effect_domain: "native_input" };

function frame(sequence: number, cursor: "root" | "information", actions: TextMenuAction[]): TextMenuSnapshot {
  return {
    protocol_version: "1.0.0", schema: "sts2.player-environment/text-menu-snapshot-1", input_profile: "text-menu-v1",
    snapshot_id: `text-${sequence}`, sequence, observed_at: "2026-09-26T00:00:00.000Z", status: "interactive", persistent: null,
    interaction: { interaction_id: "interaction", kind: "test", stage: "ready", content_schema: "test", content: { surface: { kind: "test" }, context: { kind: "test" } }, capabilities: [] },
    referents: [], menu: { cursor, revision: sequence, native_snapshot_id: "native-1" },
    menu_actions: { status: "complete", materialized_count: actions.length, total_count: actions.length, ordering_semantics: "connector_order", actions },
    completeness: { status: "complete", visible_information: "test", interaction_discovery: "test", missing: [], hidden_by_policy: [] },
    session: { runtime_instance_id: "runtime", environment_fingerprint: "environment" },
    information_policy: { id: "test", scope: "test", includes_hidden_information: false, unknown_field_behavior: "reject" }
  } as TextMenuSnapshot;
}

function manifest(): PolicyManifest {
  return {
    schema: "sts2.policy-runtime/policy-manifest-1", manifest_id: "text-test",
    policy: { id: "test", version: "1", provider: "test", architecture: "test" },
    adapter: { id: "test", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-1", code_sha256: "c".repeat(64) },
    artifact: { id: "test", path: "test", sha256: "a".repeat(64) },
    representation: { id: "text", version: "1", input_schema: "sts2.player-environment/text-menu-snapshot-1" },
    requirements: { connector_protocol_version: "1.0.0", environment: { host_kind: "test", connector_version: "1", connector_source_revision: "source", connector_artifact_sha256: "b".repeat(64), connector_module_version_id: "mvid", modset_status: "exact", modset_fingerprint: "modset", loaded_mod_ids: ["fixture-mod"] }, reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-menu-action-id-order", score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
    support: { game_versions: ["fixture-game"], game_commits: ["fixture-commit"], interaction_kinds: ["test"], action_verbs: ["open_information", "end_turn"] },
    adapter_config: {}, claims: { full_run: false, selector: false, catalog_filtered: false, creates_action_authority: false, creates_native_operands: false }
  };
}

function capabilities(): TextMenuCapabilities {
  return {
    protocol_version: "1.0.0", snapshot_schema: "sts2.player-environment/text-menu-snapshot-1", action_schema: "sts2.player-environment/action-1", receipt_schema: "sts2.player-environment/text-menu-action-result-1", control_schema: "sts2.player-environment/control-1", input_profile: "text-menu-v1", status: "ready",
    host: { id: "fixture", name: "fixture", version: "1", runtime_instance_id: "runtime", host_kind: "test", implementation: { source_revision: "source", module_version_id: "mvid", artifact_sha256: "b".repeat(64) } },
    game: { version: "fixture-game", commit: "fixture-commit", branch: null, main_assembly_hash: null, compatibility: { status: "exact", observation_allowed: true, detail: "fixture" }, modset: { status: "exact", fingerprint: "modset", scope: "fixture", loaded_mod_ids: ["fixture-mod"], detail: "fixture" } },
    environment_fingerprint: "environment", verbs: ["end_turn"], snapshot_bound: true, single_controller: true, execution_available: true, control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
  } as TextMenuCapabilities;
}

function result(requestId: string, action: TextMenuAction, successor: TextMenuSnapshot): TextMenuActionResult {
  return { protocol_version: "1.0.0", schema: "sts2.player-environment/text-menu-action-result-1", input_profile: "text-menu-v1", request_id: requestId, status: "applied", effect_domain: action.effect_domain, native_delivery: action.effect_domain === "text_menu" ? null : "delivered", action, reason_code: null, detail: null, retry: "never", successor, attribution: null };
}

describe("text menu Runtime opt-in", () => {
  it("scores each complete current menu once, navigates without a native receipt, and counts both dispatches in the finite wallet", async () => {
    const root = frame(1, "root", [nav]);
    const information = frame(2, "information", [native]);
    const after = frame(3, "information", [native]);
    let current = root;
    const submissions: string[] = [];
    const events: string[] = [];
    const connector: PolicyConnector = {
      capabilities: async () => capabilities(), observeBundle: async () => ({ observation: current, reads: [] }),
      acquireController: vi.fn(async () => {}), releaseController: vi.fn(async () => {}),
      submit: async input => { submissions.push(input.boundActionId); const action = input.boundActionId === nav.action_id ? nav : native; current = action === nav ? information : after; return result(input.requestId, action, current); }
    };
    const seen: string[][] = [];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "text-run", autoBudget: { maxSubmissions: 2, maxPolicyCalls: 3, deadlineMs: 10_000 }, successorPoll: { maxAttempts: 1, baseBackoffMs: 0 }, sleep: async () => {},
      evidence: { append: async (kind: string) => { events.push(kind); } } as never,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      policy: input => { seen.push(input.bundle.observation.schema === root.schema ? input.bundle.observation.menu_actions.actions.map(action => action.action_id) : []); return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }; }
    });
    expect((await runtime.tick()).type).toBe("navigated");
    expect(runtime.status().last_receipt).toBeNull();
    expect(events).toContain("menu_navigation");
    expect(events).not.toContain("receipt");
    expect((await runtime.tick()).type).toBe("text_native_delivered");
    expect(runtime.status().last_receipt?.delivery).toBe("delivered");
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(submissions).toEqual([nav.action_id, native.action_id]);
    expect(seen).toEqual([[nav.action_id], [native.action_id]]);
    expect(runtime.status().autonomy_budget.submissions_used).toBe(2);
  });

  it("taints an unknown native result and does not resubmit", async () => {
    const current = frame(1, "root", [native]);
    let submissions = 0;
    const connector: PolicyConnector = {
      capabilities: async () => capabilities(), observeBundle: async () => ({ observation: current, reads: [] }), acquireController: async () => {}, releaseController: async () => {},
      submit: async input => { submissions++; return { ...result(input.requestId, native, current), status: "unknown", native_delivery: "unknown", successor: null }; }
    };
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "unknown-run", policy: input => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    expect((await runtime.tick()).type).toBe("unknown");
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: true });
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(submissions).toBe(1);
  });

  it("rejects a profile capability mismatch before observing or scoring", async () => {
    const observe = vi.fn();
    const connector: PolicyConnector = { capabilities: async () => ({ ...capabilities(), input_profile: "wrong" } as unknown as TextMenuCapabilities), observeBundle: observe, acquireController: async () => {}, releaseController: async () => {}, submit: vi.fn() };
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "mismatch-run", policy: vi.fn() });
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(observe).not.toHaveBeenCalled();
  });
});
