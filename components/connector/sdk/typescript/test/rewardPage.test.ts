import { describe, expect, it, vi } from "vitest";
import {
  ORDINARY_REWARD_PAGE_PROFILE,
  ORDINARY_REWARD_SNAPSHOT_SCHEMA,
  PlayerEnvironmentRestClient,
  decodePlayerCapabilities,
  decodePlayerSnapshot,
  decodePlayerReceipt,
  decodeRewardPageCapabilities,
  decodeRewardPageSnapshot,
  decodeRewardPageReceipt
} from "../src/index.js";

function currentCardPage() {
  return {
    protocol_version: "1.0.0", schema: ORDINARY_REWARD_SNAPSHOT_SCHEMA,
    input_profile: ORDINARY_REWARD_PAGE_PROFILE, snapshot_id: "reward-state-1",
    sequence: 1, observed_at: "2026-09-23T00:00:00Z", status: "interactive",
    persistent: null,
    interaction: {
      interaction_id: "screen-1", kind: "card_reward_selection", stage: "ready",
      prompt: null,
      content_schema: "sts2.player-environment/surface/card_reward_selection-2",
      content: {
        surface: {
          kind: "card_reward_selection",
          cards: [{ entity_id: "card-1", definition_id: "CARD", name: "Card",
            type: "skill", cost: "1", description: "Draw two cards.",
            rarity: "common", is_upgraded: false, is_selected: false }],
          alternatives: [{ entity_id: "alt-1", index: 0, label: "Skip", enabled: true }],
          selectable_card_entity_ids: ["card-1"],
          alternative_effects: [{ entity_id: "alt-1",
            effect: "return_to_rewards_without_claim" }]
        }, context: { kind: "reward_flow", reward_kind: "card_reward" }
      },
      capabilities: [{ verb: "select", subject_role: "card", arguments: [],
        availability_basis: "current_native_interaction" },
      { verb: "activate", subject_role: "card_reward_alternative", arguments: [],
        availability_basis: "current_native_interaction" }]
    },
    referents: ["card-1", "alt-1"].map((referent_id) => ({
      referent_id, role: "current_option", kind: "entity", label: referent_id,
      state: { visible: true, enabled: true, observation_basis: "native_visible_fact" },
      properties_schema: null, properties: null
    })),
    bound_actions: {
      schema: "sts2.player-environment/bound-actions-1", status: "complete",
      materialized_count: 2, total_count: 2, limit: 512,
      ordering_semantics: "deterministic",
      actions: [
        { bound_action_id: "select-1", verb: "select", interaction_id: "screen-1",
          subject_referent_id: "card-1", arguments: [], label: "Card" },
        { bound_action_id: "return-1", verb: "activate", interaction_id: "screen-1",
          subject_referent_id: "alt-1", arguments: [], label: "Skip" }
      ]
    },
    reads: [],
    completeness: { status: "complete", visible_information: "current page",
      interaction_discovery: "exact", missing: [], hidden_by_policy: [] },
    session: { runtime_instance_id: "runtime-1", environment_fingerprint: "environment-1" },
    information_policy: { id: "player_visible", scope: "player_environment",
      includes_hidden_information: false, unknown_field_behavior: "fail_closed" }
  };
}

function deliveredReceipt(successor: unknown) {
  return {
    protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1",
    input_profile: ORDINARY_REWARD_PAGE_PROFILE, request_id: "request-1",
    delivery: "delivered", action: { bound_action_id: "return-1", verb: "activate",
      subject_referent_id: "alt-1", arguments: [] },
    reason_code: null, detail: "input delivered; no causal settlement",
    retry: { allowed: false, reason: "fresh_snapshot_required" },
    successor, attribution: null
  };
}

function capabilities() {
  return {
    protocol_version: "1.0.0", snapshot_schema: ORDINARY_REWARD_SNAPSHOT_SCHEMA,
    input_profile: ORDINARY_REWARD_PAGE_PROFILE,
    action_schema: "sts2.player-environment/action-1",
    receipt_schema: "sts2.player-environment/receipt-1",
    control_schema: "sts2.player-environment/control-1", status: "implemented",
    host: { id: "host", name: "Host", version: "candidate", runtime_instance_id: "runtime-1",
      host_kind: "test", implementation: { source_revision: null, module_version_id: null,
        artifact_sha256: null } },
    game: { version: "v", commit: "commit", branch: null, main_assembly_hash: null,
      compatibility: { status: "test", observation_allowed: true, detail: "test" },
      modset: { status: "test", fingerprint: "modset", scope: "test",
        loaded_mod_ids: [], detail: "test" } },
    environment_fingerprint: "environment-1", verbs: ["select", "activate"],
    snapshot_bound: true, single_controller: true, execution_available: false,
    control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
  };
}

describe("ordinary reward page profile", () => {
  it("advertises a distinct opt-in schema and leaves legacy capabilities strict", () => {
    expect(() => decodePlayerCapabilities(capabilities())).toThrow();
    expect(decodeRewardPageCapabilities(capabilities()).data.snapshot_schema)
      .toBe(ORDINARY_REWARD_SNAPSHOT_SCHEMA);
    expect(() => decodeRewardPageCapabilities({ ...capabilities(),
      snapshot_schema: "sts2.player-environment/snapshot-1" })).toThrow();
  });

  it("keeps the legacy decoder closed and preserves full current card text and both actions", () => {
    const page = currentCardPage();
    expect(() => decodePlayerSnapshot(page)).toThrow();
    const decoded = decodeRewardPageSnapshot(page).data;
    expect(decoded.schema).toBe(ORDINARY_REWARD_SNAPSHOT_SCHEMA);
    expect(decoded.interaction.content.surface.cards).toEqual(page.interaction.content.surface.cards);
    expect(decoded.bound_actions.actions.map((action) => action.bound_action_id))
      .toEqual(["select-1", "return-1"]);
    expect(decoded.reads).toEqual([]);
  });

  it("rejects cross-profile, hidden read, effect mismatch and unsupported authority", () => {
    const page = currentCardPage();
    expect(() => decodeRewardPageSnapshot({ ...page, input_profile: undefined })).toThrow();
    expect(() => decodeRewardPageSnapshot({ ...page, schema: "sts2.player-environment/snapshot-1" })).toThrow();
    expect(() => decodeRewardPageSnapshot({ ...page, reads: [{ read_id: "read:other_group" }] })).toThrow();
    const wrongEffect = structuredClone(page);
    wrongEffect.interaction.content.surface.alternative_effects[0]!.entity_id = "other";
    expect(() => decodeRewardPageSnapshot(wrongEffect)).toThrow(/effects/u);
    const otherPage = structuredClone(page);
    otherPage.interaction.kind = "combat_turn";
    otherPage.interaction.content_schema = "sts2.player-environment/surface/combat_turn-2";
    expect(() => decodeRewardPageSnapshot(otherPage)).toThrow(/cannot authorize/u);
  });

  it("accepts a truthful unsupported response with no partial 512-action menu", () => {
    const page = currentCardPage();
    page.status = "visible_unsupported";
    page.completeness.status = "partial";
    page.interaction.capabilities = [];
    page.bound_actions.status = "unavailable";
    page.bound_actions.materialized_count = 0;
    page.bound_actions.total_count = 576;
    page.bound_actions.actions = [];
    const decoded = decodeRewardPageSnapshot(page).data;
    expect(decoded.status).toBe("visible_unsupported");
    expect(decoded.bound_actions.total_count).toBe(576);
    expect(decoded.bound_actions.actions).toEqual([]);
  });

  it("requires a profile-matched Receipt before exposing its successor", () => {
    const receipt = deliveredReceipt(currentCardPage());
    expect(() => decodePlayerReceipt(receipt)).toThrow();
    expect(decodeRewardPageReceipt(receipt).data.successor?.snapshot_id).toBe("reward-state-1");
    expect(() => decodeRewardPageReceipt({ ...receipt, input_profile: undefined })).toThrow();
    expect(() => decodeRewardPageReceipt(deliveredReceipt({
      ...currentCardPage(), input_profile: undefined
    }))).toThrow();
  });

  it("uses the explicit selector for Observe, Submit and poll", async () => {
    const fetchImpl = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = String(url);
      if (path.includes("/capabilities?")) return new Response(JSON.stringify(capabilities()));
      if (path.includes("/snapshot?")) return new Response(JSON.stringify(currentCardPage()));
      if (init?.method === "POST") return new Response(JSON.stringify(deliveredReceipt(null)));
      return new Response(JSON.stringify(deliveredReceipt(currentCardPage())));
    });
    const client = new PlayerEnvironmentRestClient("http://test", 1000, fetchImpl as typeof fetch);
    await client.rewardPageCapabilities();
    await client.observeRewardPage();
    await client.submitRewardPage({ requestId: "request-1", expectedSnapshotId: "reward-state-1",
      boundActionId: "return-1", clientSessionId: "client", controllerLeaseId: "lease",
      controllerGeneration: 1 });
    await client.pollRewardPage("request-1");
    expect(String(fetchImpl.mock.calls[0]?.[0])).toContain(
      `input_profile=${ORDINARY_REWARD_PAGE_PROFILE}`);
    expect(String(fetchImpl.mock.calls[1]?.[0])).toContain(
      `input_profile=${ORDINARY_REWARD_PAGE_PROFILE}`);
    expect(JSON.parse(String(fetchImpl.mock.calls[2]?.[1]?.body)).input_profile)
      .toBe(ORDINARY_REWARD_PAGE_PROFILE);
    expect(String(fetchImpl.mock.calls[3]?.[0])).toContain(
      `input_profile=${ORDINARY_REWARD_PAGE_PROFILE}`);
  });

  it("does not expose a polled successor after Host profile mismatch", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      error: "input_profile_mismatch"
    }), { status: 409 }));
    const client = new PlayerEnvironmentRestClient("http://test", 1000, fetchImpl as typeof fetch);
    await expect(client.pollRewardPage("request-legacy")).rejects.toThrow(/409/u);
  });
});
