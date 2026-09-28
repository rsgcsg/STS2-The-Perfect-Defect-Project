import { describe, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import type { TextMenuAction, TextMenuActionResult, TextMenuCapabilities, TextMenuSnapshot } from "@rsgcsg/sts2-connector-client";
import { decodeTextMenuSnapshot } from "@rsgcsg/sts2-connector-client";
import { PolicyRuntime } from "../src/runtime.js";
import { startPolicyRuntimeHttpServer } from "../src/server.js";
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
  it("records an unsupported interactive terminal page before Auto hands control to Human", async () => {
    const page = decodeTextMenuSnapshot({ ...frame(1, "root", [{ ...native, action_id: "game-over-advance", verb: "activate", label: "Continue" }]),
      interaction: { interaction_id: "game-over", kind: "game_over", stage: "intro", content_schema: "sts2.player-environment/surface/game_over-1",
        content: { surface: { kind: "game_over", stage: "intro", screen_entity_id: "game-over-screen", return_destination: null, can_advance_summary: true, can_return: false, other_controls: [] },
          context: { kind: "game_over", result: "loss", game_mode: "standard", score: null, floor_reached: null, ascension: null } },
        capabilities: [{ verb: "activate", subject_role: null, arguments: [], availability_basis: "exact_current_text_menu" }] }
    }).data;
    const events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    const acquire = vi.fn(async () => {}), submit = vi.fn(async () => { throw new Error("unexpected submit"); }), score = vi.fn();
    const supported = manifest();
    supported.support.action_verbs.push("activate");
    const runtime = new PolicyRuntime({ manifest: supported, mode: "auto", runId: "unadmitted-terminal",
      connector: { capabilities: async () => ({ ...capabilities(), verbs: ["activate"] }), observeBundle: async () => ({ observation: page, reads: [] }), acquireController: acquire, releaseController: vi.fn(async () => {}), submit },
      evidence: { append: async (kind: string, payload: Record<string, unknown>) => { events.push({ kind, payload }); } } as never,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) }, policy: score });
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "unsupported_interaction_kind" });
    expect(runtime.status()).toMatchObject({ mode: "human", controller: "released", tainted: false });
    expect(score).not.toHaveBeenCalled();
    expect(acquire).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
    expect(events.map(event => event.kind)).toEqual(["environment_admitted", "text_observation_not_admitted", "handoff_to_human"]);
    expect(events[1]?.payload).toEqual({ reason: "unsupported_interaction_kind", snapshot: page });
  });

  it("does not record repeated settling observations or identity-mismatched pages", async () => {
    const base = frame(1, "root", []);
    const settling: TextMenuSnapshot = { ...base, status: "settling", menu_actions: { ...base.menu_actions, status: "unavailable" } };
    const events: string[] = [];
    let current = settling;
    const score = vi.fn(), acquire = vi.fn(), submit = vi.fn();
    const runtime = new PolicyRuntime({ manifest: manifest(), mode: "auto", runId: "settling-reject",
      connector: { capabilities: async () => capabilities(), observeBundle: async () => ({ observation: current, reads: [] }), acquireController: acquire, releaseController: vi.fn(), submit },
      evidence: { append: async (kind: string) => { events.push(kind); } } as never,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) }, policy: score });
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "snapshot_settling" });
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "snapshot_settling" });
    expect(events).toEqual(["environment_admitted"]);
    expect(runtime.status().mode).toBe("auto");
    current = { ...frame(2, "root", [native]), session: { ...base.session, environment_fingerprint: "foreign" } };
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "environment_identity_drift" });
    expect(events).not.toContain("text_observation_not_admitted");
    expect(score).not.toHaveBeenCalled();
    expect(acquire).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
  });

  it("fails closed if a rejected text observation cannot be recorded", async () => {
    const current = frame(1, "root", [{ ...native, verb: "unsupported_verb" }]);
    const events: string[] = [];
    const score = vi.fn(), acquire = vi.fn(), submit = vi.fn();
    const runtime = new PolicyRuntime({ manifest: manifest(), mode: "auto", runId: "rejected-write-failure",
      connector: { capabilities: async () => capabilities(), observeBundle: async () => ({ observation: current, reads: [] }), acquireController: acquire, releaseController: vi.fn(), submit },
      evidence: { append: async (kind: string) => { events.push(kind); if (kind === "text_observation_not_admitted") throw new Error("disk full"); } } as never,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) }, policy: score });
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "agent_evidence_write_failed" });
    expect(runtime.status()).toMatchObject({ mode: "human", controller: "released" });
    expect(events).toEqual(["environment_admitted", "text_observation_not_admitted", "fail_closed"]);
    expect(score).not.toHaveBeenCalled();
    expect(acquire).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
  });

  it("drives complete combat, reward and map menus through Auto and hands observed game over to Human", async () => {
    const action = (actionId: string, verb: string, subject: string | null = null): TextMenuAction => ({ ...native, action_id: actionId, verb, label: actionId, subject_referent_id: subject });
    const scene = (sequence: number, kind: string, actions: TextMenuAction[], status: TextMenuSnapshot["status"] = "interactive"): TextMenuSnapshot => {
      const base = frame(sequence, "root", actions);
      const referents = actions.filter(item => item.subject_referent_id !== null).map(item => ({ referent_id: item.subject_referent_id!, role: kind === "combat_turn" ? "card" : kind === "reward_claim" ? "reward" : "map_point", kind: "entity" as const, label: item.label, state: { visible: true, enabled: true, observation_basis: "native_visible_fact" as const } }));
      const surface = kind === "game_over" ? { kind, stage: "summary", screen_entity_id: "game-over-screen", return_destination: "main_menu", can_advance_summary: false, can_return: true, other_controls: [] }
        : kind === "combat_turn" ? { kind, room_entity_id: `combat-room-${sequence}`, can_end_turn: status === "interactive", playable_cards: actions.filter(item => item.verb === "begin_card_play").map(item => ({ entity_id: item.subject_referent_id, name: item.label, target_entity_ids: [] })), usable_potions: [] }
        : kind === "reward_claim" ? { kind, screen_entity_id: "reward-screen", rewards: [{ entity_id: "reward-a", kind: "gold", label: "Gold", description: "Gold", enabled: true }], potion_slots_full: false, discardable_potions: [], can_proceed: true, proceed_skips_remaining_rewards: false }
        : { kind, screen_entity_id: "map-screen", travel_enabled: status === "interactive", traveling: false, drawing_mode: "none", next_options: actions.map((item, index) => ({ entity_id: item.subject_referent_id, col: index, row: 1, point_type: "combat" })), annotation_input_entity_id: null, can_exit_annotation: false };
      const context = kind === "game_over" ? { kind, result: "loss", game_mode: "standard", score: 0, floor_reached: 9, ascension: 0 }
        : kind === "combat_turn" ? { kind: "combat", encounter_type: "normal", round: 1, turn_owner: "player", is_play_phase: true, player: {}, enemies: [] }
        : kind === "reward_claim" ? { kind: "reward_flow", reward_kind: "room_rewards" }
        : { kind: "map", act_index: 1, current_position: null, visited: [], nodes: [] };
      return decodeTextMenuSnapshot({ ...base, status, referents,
        interaction: { ...base.interaction, interaction_id: `interaction-${sequence}`, kind, stage: status === "interactive" ? "ready" : status, content_schema: `sts2.player-environment/surface/${kind}-1`,
          content: { surface, context },
          capabilities: status === "interactive" ? actions.map(item => ({ verb: item.verb, subject_role: item.subject_referent_id === null ? null : "subject", arguments: [], availability_basis: "exact_current_text_menu" })) : [] },
        menu: { ...base.menu, native_snapshot_id: `native-${sequence}` },
        menu_actions: { ...base.menu_actions, status: status === "interactive" ? "complete" : "unavailable", ordering_semantics: "native_order_with_fixed_information_groups" }
      }).data;
    };
    const combat = scene(1, "combat_turn", [action("end-combat-turn", "end_turn"), action("begin-card", "begin_card_play", "card-a")]);
    const settling = scene(2, "combat_turn", [], "settling");
    const reward = scene(3, "reward_claim", [action("claim-reward", "claim_reward", "reward-a"), action("proceed-rewards", "proceed_rewards")]);
    const map = scene(4, "map_navigation", [action("travel-left", "navigate", "map-left"), action("travel-right", "navigate", "map-right")]);
    const mapSettling = scene(5, "map_navigation", [], "settling");
    const nextCombat = scene(6, "combat_turn", [action("end-next-turn", "end_turn"), action("begin-next-card", "begin_card_play", "card-b")]);
    const gameOver = scene(7, "game_over", [], "observed");
    const pages = [combat, reward, map, nextCombat];
    const selected = ["end-combat-turn", "proceed-rewards", "travel-right", "end-next-turn"];
    const expectedNext = [reward, map, nextCombat, gameOver];
    let current = combat;
    let pending: TextMenuSnapshot[] = [];
    const observations: string[] = [];
    const submissions: Array<{ snapshotId: string; actionId: string; requestId: string }> = [];
    const events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
    const scoreInputs: Array<{ snapshotId: string; actionIds: string[]; count: number; digest: string }> = [];
    const connector: PolicyConnector = {
      capabilities: async () => ({ ...capabilities(), verbs: ["end_turn", "begin_card_play", "claim_reward", "proceed_rewards", "navigate"] }),
      observeBundle: async () => {
        current = pending.shift() ?? current;
        observations.push(current.snapshot_id);
        return { observation: current, reads: [] };
      },
      acquireController: vi.fn(async () => {}),
      releaseController: vi.fn(async () => {}),
      submit: vi.fn(async input => {
        const index = submissions.length;
        expect(input).toMatchObject({ expectedSnapshotId: pages[index]?.snapshot_id, boundActionId: selected[index], inputProfile: "text-menu-v1" });
        submissions.push({ snapshotId: input.expectedSnapshotId, actionId: input.boundActionId, requestId: input.requestId });
        const chosen = pages[index]!.menu_actions.actions.find(candidate => candidate.action_id === input.boundActionId)!;
        pending = index === 0 ? [settling, expectedNext[index]!] : index === 2 ? [mapSettling, expectedNext[index]!] : [expectedNext[index]!];
        return result(input.requestId, chosen, pending[0]!);
      })
    };
    const supported = manifest();
    supported.support.interaction_kinds.push("combat_turn", "reward_claim", "map_navigation");
    supported.support.action_verbs.push("begin_card_play", "claim_reward", "proceed_rewards", "navigate");
    const runtime = new PolicyRuntime({ manifest: supported, connector, mode: "auto", runId: "whole-flow-run",
      autoBudget: { maxSubmissions: 8, maxPolicyCalls: 8, deadlineMs: 10_000 }, successorPoll: { maxAttempts: 2, baseBackoffMs: 0 }, sleep: async () => {},
      evidence: { append: async (kind: string, payload: Record<string, unknown>) => { events.push({ kind, payload }); } } as never,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      policy: input => {
        const snapshot = input.bundle.observation as TextMenuSnapshot;
        const actionIds = snapshot.menu_actions.actions.map(candidate => candidate.action_id);
        scoreInputs.push({ snapshotId: snapshot.snapshot_id, actionIds, count: input.candidate_count, digest: input.candidate_digest });
        const index = pages.findIndex(page => page.snapshot_id === snapshot.snapshot_id);
        expect(index).toBeGreaterThanOrEqual(0);
        return { candidate_digest: input.candidate_digest, scores: actionIds.map(id => id === selected[index] ? 1 : 0), selected_index: actionIds.indexOf(selected[index]!) };
      }
    });
    const service = await startPolicyRuntimeHttpServer(runtime, { port: 0, autoDrive: true, deferAutoDrive: true, autoIdleMs: 0 });
    try {
      service.startDriving();
      await vi.waitFor(() => expect(runtime.status().mode).toBe("human"), { timeout: 2_000 });
      const response = await fetch(`${service.address}/status`);
      expect(response.status).toBe(200);
      expect((await response.json()).status).toMatchObject({ mode: "human", controller: "released", tainted: false, last_snapshot: { snapshot_id: gameOver.snapshot_id, status: "observed" } });
      expect(observations).toEqual([combat.snapshot_id, settling.snapshot_id, reward.snapshot_id, reward.snapshot_id, map.snapshot_id, map.snapshot_id, mapSettling.snapshot_id, nextCombat.snapshot_id, nextCombat.snapshot_id, gameOver.snapshot_id, gameOver.snapshot_id]);
      expect(scoreInputs).toEqual(pages.map(page => {
        const actionIds = page.menu_actions.actions.map(candidate => candidate.action_id);
        return { snapshotId: page.snapshot_id, actionIds, count: actionIds.length, digest: createHash("sha256").update(JSON.stringify(actionIds), "utf8").digest("hex") };
      }));
      expect(submissions.map(({ snapshotId, actionId }) => [snapshotId, actionId])).toEqual(pages.map((page, index) => [page.snapshot_id, selected[index]]));
      expect(new Set(submissions.map(item => item.requestId)).size).toBe(4);
      expect(connector.releaseController).toHaveBeenCalledTimes(1);
      expect(events.filter(event => event.kind === "text_decision_input").map(event => (event.payload.snapshot as TextMenuSnapshot).snapshot_id)).toEqual(pages.map(page => page.snapshot_id));
      const deliveries = events.filter(event => event.kind === "text_native_delivery");
      expect(deliveries).toHaveLength(4);
      expect((deliveries[0]?.payload.result as TextMenuActionResult).successor).toEqual(settling);
      expect(events.filter(event => event.kind === "text_observed_successor").map(event => event.payload.successor)).toEqual(expectedNext);
      expect(events.filter(event => event.kind === "text_decision_input" || event.kind === "decision")).toHaveLength(8);
      expect(events.find(event => event.kind === "handoff_to_human")?.payload).toEqual({ reason: "auto_surface_not_admitted" });
      expect(events.some(event => event.kind === "text_decision_input" && (event.payload.snapshot as TextMenuSnapshot).snapshot_id === gameOver.snapshot_id)).toBe(false);
    } finally { await service.close(); }
  });

  it("scores each complete current menu once, navigates without a native receipt, and counts both dispatches in the finite wallet", async () => {
    const root = frame(1, "root", [nav]);
    const information = frame(2, "information", [native]);
    const after = frame(3, "information", [native]);
    let current = root;
    const submissions: string[] = [];
    const events: string[] = [];
    const inputs: unknown[] = [];
    const connector: PolicyConnector = {
      capabilities: async () => capabilities(), observeBundle: async () => ({ observation: current, reads: [] }),
      acquireController: vi.fn(async () => {}), releaseController: vi.fn(async () => {}),
      submit: async input => { submissions.push(input.boundActionId); const action = input.boundActionId === nav.action_id ? nav : native; current = action === nav ? information : after; return result(input.requestId, action, current); }
    };
    const seen: string[][] = [];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "text-run", autoBudget: { maxSubmissions: 2, maxPolicyCalls: 3, deadlineMs: 10_000 }, successorPoll: { maxAttempts: 1, baseBackoffMs: 0 }, sleep: async () => {},
      evidence: { append: async (kind: string, payload: Record<string, unknown>) => { events.push(kind); if (kind === "text_decision_input") inputs.push(payload); } } as never,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      policy: input => { seen.push(input.bundle.observation.schema === root.schema ? input.bundle.observation.menu_actions.actions.map(action => action.action_id) : []); return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }; }
    });
    expect((await runtime.tick()).type).toBe("navigated");
    expect(runtime.status().last_receipt).toBeNull();
    expect(events).toContain("menu_navigation");
    expect(events.indexOf("text_decision_input")).toBeLessThan(events.indexOf("decision"));
    expect(events.indexOf("decision")).toBeLessThan(events.indexOf("text_menu_dispatch_attempt"));
    expect(inputs[0]).toMatchObject({ snapshot: root });
    expect(events).not.toContain("receipt");
    expect((await runtime.tick()).type).toBe("text_native_delivered");
    expect(runtime.status().last_receipt?.delivery).toBe("delivered");
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(submissions).toEqual([nav.action_id, native.action_id]);
    expect(seen).toEqual([[nav.action_id], [native.action_id]]);
    expect(runtime.status().autonomy_budget.submissions_used).toBe(2);
  });

  it("does not acquire or submit if the exact text input cannot be recorded", async () => {
    const current = frame(1, "root", [native]);
    const acquire = vi.fn(async () => {}), submit = vi.fn();
    const connector: PolicyConnector = {
      capabilities: async () => capabilities(), observeBundle: async () => ({ observation: current, reads: [] }),
      acquireController: acquire, releaseController: async () => {}, submit
    };
    const events: string[] = [];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector, mode: "auto", runId: "record-failure",
      evidence: { append: async (kind: string) => { events.push(kind); if (kind === "text_decision_input") throw new Error("disk fixture"); } } as never,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      policy: input => ({ candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 }) });
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(events).toContain("text_decision_input");
    expect(events).not.toContain("decision");
    expect(acquire).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
    expect(runtime.status().mode).toBe("human");
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
