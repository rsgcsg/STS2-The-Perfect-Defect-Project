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
  decodeRewardPageReceipt,
  REWARD_POTION_PAGE_PROFILE, REWARD_POTION_SNAPSHOT_SCHEMA,
  decodeRewardPotionCapabilities, decodeRewardPotionSnapshot, decodeRewardPotionReceipt
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

function popupV2() {
  const page: any = currentCardPage();
  page.schema = REWARD_POTION_SNAPSHOT_SCHEMA;
  page.input_profile = REWARD_POTION_PAGE_PROFILE;
  page.interaction.kind = "potion_popup";
  page.interaction.interaction_id = "popup-screen";
  page.interaction.content_schema = "sts2.player-environment/surface/potion_popup-3";
  page.interaction.content.surface = {
    kind: "potion_popup", potion_entity_id: "potion-1", name: "Test potion", slot: 0,
    can_use: true, can_discard: true,
    controls: [
      { entity_id: "use-control", kind: "use", enabled: true, label: "Use" },
      { entity_id: "discard-control", kind: "discard", enabled: true, label: "Discard" },
      { entity_id: "popup-screen:close", kind: "close", enabled: true, label: "Close" }
    ]
  };
  page.referents = [
    { referent_id: "potion-1", role: "potion", kind: "entity",
      label: "Test potion", properties_schema: "sts2.player-environment/referent/potion-1",
      state: { visible: true, enabled: true, observation_basis: "native_visible_fact" },
      properties: { potion_entity_id: "potion-1", name: "Test potion" } },
    ...page.interaction.content.surface.controls.map((control: any) => ({
      referent_id: control.entity_id, role: "control", kind: "entity", label: control.label,
      state: { visible: true, enabled: control.enabled,
        observation_basis: "native_visible_fact" },
      properties_schema: "sts2.player-environment/referent/control-1", properties: control
    }))
  ];
  page.bound_actions.actions = [
    { bound_action_id: "use", verb: "activate", interaction_id: "popup-screen",
      subject_referent_id: "use-control",
      arguments: [{ role: "potion", referent_id: "potion-1" }], label: "Use" },
    { bound_action_id: "discard", verb: "activate", interaction_id: "popup-screen",
      subject_referent_id: "discard-control",
      arguments: [{ role: "potion", referent_id: "potion-1" }], label: "Discard" },
    { bound_action_id: "close", verb: "cancel", interaction_id: "popup-screen",
      subject_referent_id: "popup-screen:close", arguments: [], label: "Close" }
  ];
  page.bound_actions.total_count = 3;
  page.bound_actions.materialized_count = 3;
  return page;
}

describe("reward/potion v2 current page", () => {
  it("accepts the complete Use, Discard and Close menu while rejecting old decoders", () => {
    const page = popupV2();
    expect(decodeRewardPotionSnapshot(page).data.bound_actions.actions).toHaveLength(3);
    expect(() => decodeRewardPageSnapshot(page)).toThrow();
    expect(() => decodePlayerSnapshot(page)).toThrow();
    const cap: any = capabilities();
    cap.input_profile = REWARD_POTION_PAGE_PROFILE;
    cap.snapshot_schema = REWARD_POTION_SNAPSHOT_SCHEMA;
    expect(decodeRewardPotionCapabilities(cap).data.snapshot_schema)
      .toBe(REWARD_POTION_SNAPSHOT_SCHEMA);
    const receipt: any = deliveredReceipt(page);
    receipt.input_profile = REWARD_POTION_PAGE_PROFILE;
    expect(decodeRewardPotionReceipt(receipt).data.successor?.schema)
      .toBe(REWARD_POTION_SNAPSHOT_SCHEMA);
    expect(() => decodeRewardPotionReceipt({ ...receipt, input_profile: ORDINARY_REWARD_PAGE_PROFILE }))
      .toThrow();
  });

  it("rejects missing booleans, false referents, wrong potion operands and omitted controls", () => {
    const missingUse = popupV2();
    delete missingUse.interaction.content.surface.can_use;
    expect(() => decodeRewardPotionSnapshot(missingUse)).toThrow();
    const stringDiscard = popupV2();
    stringDiscard.interaction.content.surface.can_discard = "true";
    expect(() => decodeRewardPotionSnapshot(stringDiscard)).toThrow();
    const wrongRole = popupV2();
    wrongRole.referents[0].role = "card";
    expect(() => decodeRewardPotionSnapshot(wrongRole)).toThrow(/potion argument/u);
    const missingPotion = popupV2();
    missingPotion.referents.shift();
    expect(() => decodeRewardPotionSnapshot(missingPotion)).toThrow(/potion argument/u);
    const wrongOperand = popupV2();
    wrongOperand.bound_actions.actions[0].arguments[0].referent_id = "other-potion";
    expect(() => decodeRewardPotionSnapshot(wrongOperand)).toThrow(/exact current potion/u);
    const futureTarget = popupV2();
    futureTarget.bound_actions.actions[0].arguments.push({ role: "target", referent_id: "enemy" });
    expect(() => decodeRewardPotionSnapshot(futureTarget)).toThrow(/exact current potion/u);
    const omitted = popupV2();
    omitted.bound_actions.actions.splice(1, 1);
    omitted.bound_actions.total_count = 2;
    omitted.bound_actions.materialized_count = 2;
    expect(() => decodeRewardPotionSnapshot(omitted)).toThrow(/omits/u);
    const disabledDuplicate = popupV2();
    disabledDuplicate.interaction.content.surface.can_use = false;
    disabledDuplicate.interaction.content.surface.controls[0].enabled = false;
    disabledDuplicate.interaction.content.surface.controls[0].entity_id = "discard-control";
    disabledDuplicate.bound_actions.actions.shift();
    disabledDuplicate.bound_actions.total_count = 2;
    disabledDuplicate.bound_actions.materialized_count = 2;
    expect(() => decodeRewardPotionSnapshot(disabledDuplicate)).toThrow(/duplicated/u);
    const disabledMissing = popupV2();
    disabledMissing.interaction.content.surface.can_use = false;
    disabledMissing.interaction.content.surface.controls[0].enabled = false;
    delete disabledMissing.interaction.content.surface.controls[0].entity_id;
    disabledMissing.bound_actions.actions.shift();
    disabledMissing.bound_actions.total_count = 2;
    disabledMissing.bound_actions.materialized_count = 2;
    expect(() => decodeRewardPotionSnapshot(disabledMissing)).toThrow(/missing/u);
    const outer: any = popupV2();
    outer.interaction.kind = "reward_claim";
    outer.interaction.content_schema = "sts2.player-environment/surface/reward_claim-3";
    outer.interaction.content.surface = {
      kind: "reward_claim", rewards: [], can_proceed: false,
      openable_potions: [{ potion_entity_id: "potion-1", slot: 0, name: "Test potion" },
        { potion_entity_id: "potion-2", slot: 0, name: "Second potion" }]
    };
    expect(() => decodeRewardPotionSnapshot(outer)).toThrow(/unique current slot/u);
    outer.interaction.content.surface.openable_potions.pop();
    expect(() => decodeRewardPotionSnapshot(outer)).toThrow(/incomplete or legacy proceed/u);
    outer.interaction.content.surface.proceed_skips_remaining_rewards = false;
    outer.interaction.content.surface.cards = [{ name: "Unopened card" }];
    expect(() => decodeRewardPotionSnapshot(outer)).toThrow(/incomplete or legacy proceed/u);
    const inner: any = currentCardPage();
    inner.schema = REWARD_POTION_SNAPSHOT_SCHEMA;
    inner.input_profile = REWARD_POTION_PAGE_PROFILE;
    inner.interaction.content_schema = "sts2.player-environment/surface/card_reward_selection-3";
    inner.interaction.content.surface.openable_potions = [];
    inner.interaction.content.surface.rewards = [{ name: "Another reward group" }];
    expect(() => decodeRewardPotionSnapshot(inner)).toThrow(/another page/u);
    const mixedPopup = popupV2();
    mixedPopup.interaction.content.surface.cards = [{ name: "Unopened card" }];
    expect(() => decodeRewardPotionSnapshot(mixedPopup)).toThrow(/another page/u);
  });

  it("routes explicit v2 Observe, Submit and poll through the same profile", async () => {
    const fetchImpl = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = String(url);
      if (path.includes("/snapshot?")) return new Response(JSON.stringify(popupV2()));
      if (init?.method === "POST") return new Response(JSON.stringify({
        ...deliveredReceipt(null), input_profile: REWARD_POTION_PAGE_PROFILE
      }));
      return new Response(JSON.stringify({
        ...deliveredReceipt(popupV2()), input_profile: REWARD_POTION_PAGE_PROFILE
      }));
    });
    const client = new PlayerEnvironmentRestClient("http://test", 1000, fetchImpl as typeof fetch);
    await client.observeRewardPotionPage();
    await client.submitRewardPotionPage({ requestId: "request-1", expectedSnapshotId: "reward-state-1",
      boundActionId: "use", clientSessionId: "client", controllerLeaseId: "lease",
      controllerGeneration: 1 });
    await client.pollRewardPotionPage("request-1");
    expect(String(fetchImpl.mock.calls[0]?.[0])).toContain(
      `input_profile=${REWARD_POTION_PAGE_PROFILE}`);
    expect(JSON.parse(String(fetchImpl.mock.calls[1]?.[1]?.body)).input_profile)
      .toBe(REWARD_POTION_PAGE_PROFILE);
    expect(String(fetchImpl.mock.calls[2]?.[0])).toContain(
      `input_profile=${REWARD_POTION_PAGE_PROFILE}`);
  });
});
