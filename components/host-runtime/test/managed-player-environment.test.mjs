import { gameOver } from "./fixtures/managed-game-over.mjs";
import { shopInventory } from "./fixtures/managed-shop.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import {
  decodePlayerRead,
  decodePlayerReceipt,
  decodePlayerSnapshot
} from "@rsgcsg/sts2-connector-client";
import {
  ManagedPlayerEnvironmentSession,
  projectManagedCandidateDecision
} from "../src/managed-player-environment.mjs";
import { chooseManagedPlayerEnvironmentAction } from "../src/managed-player-environment-probe.mjs";
import { chooseManagedCandidateAction } from "../src/managed-candidate.mjs";
import { captureManagedPublicBasis } from "../src/managed-public-basis.mjs";

const projectionIdentity = {
  runtimeInstanceId: "managed-runtime-test",
  environmentFingerprint: "managed-environment-test",
  sequence: 1
};

function player() {
  return {
    name: "The Ironclad",
    hp: 80,
    max_hp: 80,
    gold: 99,
    relics: [],
    potions: [],
    deck: [{
      native_ref: "deck-card-a",
      id: "CARD.STRIKE_IRONCLAD",
      name: "Strike",
      cost: 1,
      type: "Attack",
      rarity: "Basic",
      upgraded: false,
      description: "Deal 6 damage.",
      public_details: { keywords: [], card_previews: [] },
      public_details_complete: true,
      public_details_missing: []
    }]
  };
}

function eventState() {
  return {
    type: "decision",
    decision: "event_choice",
    context: { act: 1, floor: 1, room_type: "Event" },
    room_ref: "event-room-a",
    event_ref: "event-a",
    event_name: "Neow",
    event_id: "NEOW", ancient: true, in_dialogue: false, event_public_complete: true,
    description: "Choose.",
    options: [
      { index: 0, native_ref: "event-option-open", title: "Open", description: "Available", is_locked: false, is_enabled: true, is_proceed: false, was_chosen: false, will_kill_player: false, tooltips: [] },
      { index: 1, native_ref: "event-option-locked", title: "Locked", description: "Unavailable", is_locked: true, is_enabled: false, is_proceed: false, was_chosen: false, will_kill_player: false, tooltips: [] }
    ],
    player: player()
  };
}

test("projects a managed decision through the strict canonical SDK", () => {
  const projection = projectManagedCandidateDecision({ state: eventState(), ...projectionIdentity });
  assert.equal(decodePlayerSnapshot(projection.snapshot).data.status, "interactive");
  assert.equal(projection.snapshot.interaction.kind, "event_option");
  assert.equal(projection.snapshot.bound_actions.actions.length, 1);
  assert.equal(projection.bindings.size, 1);
  assert.equal(projection.snapshot.completeness.status, "partial");
  assert.match(projection.snapshot.completeness.missing.join(","), /canonical_persistent_run_identity/u);
});

test("projects complete map topology and persistent visible state without exposing native operands", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "map_select",
      context: {
        act: 1,
        act_index: 0,
        act_definition_id: "OVERGROWTH",
        act_name: "Overgrowth",
        floor: 0,
        total_floor: 0,
        ascension: 0,
        bosses: [{ id: "VANTOM_BOSS", name: "Vantom", order: 0 }],
        modifiers: []
      },
      choices: [{
        col: 3,
        row: 0,
        type: "Monster",
        native_ref: "map-point-a",
        children: [{ col: 2, row: 1, type: "Monster" }]
      }],
      visible_map: {
        type: "map",
        rows: [[{
          col: 2,
          row: 1,
          type: "Monster",
          children: [{ col: 3, row: 16 }],
          visited: false,
          current: false
        }]],
        boss: { col: 3, row: 16, type: "Boss" },
        current_coord: null
      },
      player: {
        ...player(),
        native_ref: "player-a",
        character_id: "IRONCLAD",
        max_potion_slots: 3
      }
    }
  });
  assert.equal(decodePlayerSnapshot(projection.snapshot).data.status, "interactive");
  assert.equal(projection.snapshot.completeness.status, "complete");
  assert.equal(projection.snapshot.persistent.content.run.act_definition_id, "OVERGROWTH");
  assert.equal(projection.snapshot.interaction.content.context.nodes.length, 3);
  assert.equal(projection.snapshot.interaction.content.context.current_position, null);
  assert.deepEqual(projection.snapshot.referents.map((referent) => referent.role).sort(), [
    "card",
    "node",
    "node",
    "option"
  ]);
  assert.equal(projection.snapshot.bound_actions.actions[0].label, "Choose monster at (3,0)");
  assert.equal(JSON.stringify(projection.snapshot).includes("map-point-a"), false);
  assert.equal([...projection.bindings.values()][0].raw_request.args.map_point_ref, "map-point-a");
});

test("projects stable player-visible hover facts without leaking native operands", () => {
  const baseState = gameOver({
    type: "decision",
    decision: "game_over",
    victory: false,
    context: {
      act: 1,
      act_index: 0,
      act_definition_id: "OVERGROWTH",
      act_name: "Overgrowth",
      floor: 2,
      total_floor: 2,
      ascension: 0,
      bosses: [{ id: "VANTOM_BOSS", name: "Vantom", order: 0 }],
      modifiers: [{
        id: "CUSTOM_MODIFIER",
        name: "Custom Modifier",
        description: "A visible modifier.",
        keywords: [{ name: "Strength", description: "Increases attack damage." }],
        card_previews: [],
        hover_facts_complete: true
      }]
    },
    player: {
      ...player(),
      native_ref: "player-a",
      character_id: "IRONCLAD",
      max_potion_slots: 3,
      relics: [{
        native_ref: "relic-a",
        id: "BAG_OF_PREPARATION",
        name: "Bag of Preparation",
        description: "Draw 2 additional cards.",
        keywords: [],
        card_previews: [{
          id: "CARD.STRIKE_IRONCLAD",
          name: "Strike",
          type: "Attack",
          cost: "1",
          description: "Deal 6 damage.",
          rarity: "Basic",
          is_upgraded: false
        }],
        hover_facts_complete: true
      }],
      potions: [{
        native_ref: "potion-a",
        id: "STRENGTH_POTION",
        name: "Strength Potion",
        description: "Gain 2 Strength.",
        slot: 0,
        keywords: [{ name: "Strength", description: "Increases attack damage." }],
        card_previews: [],
        hover_facts_complete: true
      }]
    }
  });
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: baseState
  });
  assert.equal(decodePlayerSnapshot(projection.snapshot).data.completeness.status, "complete");
  assert.equal(projection.snapshot.persistent.content.run.modifiers[0].keywords[0].name, "Strength");
  assert.equal(projection.snapshot.persistent.content.player.relics[0].card_previews[0].name, "Strike");
  assert.equal(projection.snapshot.persistent.content.player.potions[0].keywords[0].description,
    "Increases attack damage.");
  assert.equal(JSON.stringify(projection.snapshot).includes("relic-a"), false);
  assert.equal(JSON.stringify(projection.snapshot).includes("potion-a"), false);

  const incomplete = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      ...baseState,
      player: {
        ...baseState.player,
        potions: [{ ...baseState.player.potions[0], hover_facts_complete: false }]
      }
    }
  });
  assert.equal(incomplete.snapshot.persistent, null);
  assert.deepEqual(incomplete.snapshot.completeness.missing, ["persistent_hover_or_modifier_facts"]);
});

test("projects combat reward completion as a state-bound player proceed", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "combat_rewards_complete",
      context: { act: 3, floor: 17, room_type: "Boss" },
      room_ref: "boss-room-a",
      is_boss: true,
      player: player()
    }
  });
  assert.equal(decodePlayerSnapshot(projection.snapshot).data.status, "interactive");
  assert.equal(projection.snapshot.interaction.kind, "reward_completion");
  assert.equal(projection.snapshot.bound_actions.actions.length, 1);
  assert.equal(projection.snapshot.bound_actions.actions[0].verb, "activate");
  assert.equal(JSON.stringify(projection.snapshot).includes("boss-room-a"), false);
  assert.deepEqual([...projection.bindings.values()][0].raw_request, {
    action: "proceed",
    args: { room_ref: "boss-room-a" },
    cmd: "action"
  });
});

test("projects game-owned rest option text and native actionability", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "rest_site",
      context: { act: 1, floor: 5, room_type: "RestSite" },
      options: [
        { index: 0, native_ref: "rest-option-a", option_id: "REST", name: "Rest", description: "Heal 24 HP.\n[gold]Regal Pillow[/gold] restores more.", is_enabled: true },
        { index: 1, native_ref: "rest-option-b", option_id: "SMITH", name: "Smith", description: "Upgrade a card.", is_enabled: false }
      ],
      player: player()
    }
  });
  assert.equal(projection.snapshot.interaction.kind, "rest_site");
  assert.deepEqual(projection.snapshot.interaction.content.surface.options.map((option) => ({
    name: option.name,
    description: option.description,
    enabled: option.enabled
  })), [
    { name: "Rest", description: "Heal 24 HP.\nRegal Pillow restores more.", enabled: true },
    { name: "Smith", description: "Upgrade a card.", enabled: false }
  ]);
  assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.label), ["Rest"]);
  assert.equal(JSON.stringify(projection.snapshot).includes("rest-option-a"), false);
  assert.deepEqual([...projection.bindings.values()][0].raw_request, {
    cmd: "action", action: "choose_option", args: { option_index: 0, option_ref: "rest-option-a" }
  });
});

test("rest options without exact native identity remain visible but expose no action", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision", decision: "rest_site", context: { act: 1, floor: 5, room_type: "RestSite" },
      options: [{ index: 0, option_id: "REST", name: "Rest", description: "Heal.", is_enabled: true }],
      player: player()
    }
  });
  assert.equal(projection.snapshot.status, "visible_unsupported");
  assert.equal(projection.snapshot.interaction.content.surface.options[0].name, "Rest");
  assert.deepEqual(projection.snapshot.bound_actions.actions, []);
});

test("rest raw probe requires the exact option reference and a matching native index", () => {
  const state = {
    type: "decision", decision: "rest_site", context: { act: 1, floor: 5, room_type: "RestSite" },
    options: [{ index: 0, native_ref: "rest-option-a", option_id: "HEAL", name: "Rest", is_enabled: true }],
    player: player()
  };
  assert.deepEqual(chooseManagedCandidateAction(state), {
    cmd: "action", action: "choose_option", args: { option_index: 0, option_ref: "rest-option-a" }
  });
  assert.equal(chooseManagedCandidateAction({ ...state, options: [{ ...state.options[0], native_ref: null }] }), null);
  const invalid = projectManagedCandidateDecision({
    ...projectionIdentity, state: { ...state, options: [{ ...state.options[0], index: 2 }] }
  });
  assert.equal(invalid.snapshot.status, "visible_unsupported");
  assert.deepEqual(invalid.snapshot.bound_actions.actions, []);
});

test("rest proceed is a separate room-bound input while remaining options stay visible", () => {
  const state = {
    type: "decision", decision: "rest_site", context: { act: 1, floor: 5, room_type: "RestSite" },
    options: [
      { index: 0, native_ref: "rest-option-b", option_id: "SMITH", name: "Smith", is_enabled: false },
      { index: 1, native_ref: "rest-option-c", option_id: "DIG", name: "Dig", is_enabled: true }
    ],
    room_ref: "rest-room-a", can_proceed: true, player: player()
  };
  const projection = projectManagedCandidateDecision({ ...projectionIdentity, state });
  assert.equal(decodePlayerSnapshot(projection.snapshot).data.interaction.content.surface.can_proceed, true);
  assert.deepEqual(projection.snapshot.interaction.content.surface.options.map((option) => option.name), ["Smith", "Dig"]);
  assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.label), ["Dig", "Proceed to map"]);
  assert.deepEqual([...projection.bindings.values()].map((binding) => binding.raw_request), [
    { cmd: "action", action: "choose_option", args: { option_index: 1, option_ref: "rest-option-c" } },
    { cmd: "action", action: "proceed", args: { room_ref: "rest-room-a" } }
  ]);
  assert.equal(JSON.stringify(projection.snapshot).includes("rest-room-a"), false);
  const exhausted = projectManagedCandidateDecision({
    ...projectionIdentity, state: { ...state, options: [] }
  });
  assert.deepEqual(exhausted.snapshot.bound_actions.actions.map((action) => action.label), ["Proceed to map"]);
  assert.deepEqual(chooseManagedCandidateAction(state), {
    cmd: "action", action: "proceed", args: { room_ref: "rest-room-a" }
  });
  assert.equal(chooseManagedCandidateAction({ ...state, room_ref: null }), null);
  const missing = projectManagedCandidateDecision({ ...projectionIdentity, state: { ...state, room_ref: null } });
  assert.equal(missing.snapshot.status, "visible_unsupported");
  assert.deepEqual(missing.snapshot.bound_actions.actions, []);
});

test("projects native reward sets without exposing exact reward or room operands", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "reward_set",
      context: { act: 1, floor: 2, room_type: "Monster" },
      rewards: [
        { index: 0, native_ref: "reward-gold-a", kind: "gold", name: "17 gold", value: 17 },
        { index: 1, native_ref: "reward-card-a", kind: "card_choice", name: "Add a card" }
      ],
      can_skip: true,
      is_terminal: true,
      can_proceed: true,
      room_ref: "combat-room-a",
      is_boss: false,
      player: player()
    }
  });
  assert.equal(decodePlayerSnapshot(projection.snapshot).data.status, "interactive");
  assert.equal(projection.snapshot.interaction.kind, "reward_claim");
  assert.equal(projection.snapshot.bound_actions.actions.length, 3);
  assert.deepEqual({
    ...projection.snapshot.interaction.content.surface,
    rewards: projection.snapshot.interaction.content.surface.rewards.map(({ entity_id: _entityId, group_id: _groupId, ...reward }) => reward)
  }, {
    kind: "reward_claim",
    non_combat_discardable_potions: [],
    rewards: [
      { kind: "gold", label: "17 gold", description: null, enabled: true, group_relation: "unavailable",
        card_alternatives_complete: false, native_relic_refresh_subscription: "unavailable" },
      { kind: "card", label: "Add a card", description: null, enabled: true, group_relation: "unavailable", card_options: null,
        card_alternatives: null, card_alternatives_complete: false, can_skip_selection: null, can_reroll: null, selection_completes_reward: null,
        native_relic_refresh_subscription: "unavailable" }
    ],
    potion_slots_full: false,
    discardable_potions: [],
    can_proceed: true,
    can_leave_with_unclaimed_rewards: null,
    reward_set_id: null,
    proceed_skips_remaining_rewards: true
  });
  assert.equal(JSON.stringify(projection.snapshot).includes("reward-gold-a"), false);
  assert.equal(JSON.stringify(projection.snapshot).includes("combat-room-a"), false);
  const requests = [...projection.bindings.values()].map((binding) => binding.raw_request);
  assert.equal(requests.some((request) => request.args?.reward_ref === "reward-gold-a"), true);
  assert.equal(requests.some((request) => request.args?.room_ref === "combat-room-a"), true);
});

test("reward descriptions expose visible text while retaining native reward bindings and inline icons", () => {
  const state = {
    ...eventState(), decision: "reward_set", reward_set_ref: "native-reward-set",
    rewards: [
      { native_ref: "native-gold", kind: "gold", name: "18金币", description: "18金币", group_relation: "independent" },
      { native_ref: "native-potion", kind: "potion", name: "再生药水",
        description: "获得[green]5[/green]层[gold]再生[/gold]。", group_relation: "independent" }
    ]
  };
  const projection = projectManagedCandidateDecision({ state, ...projectionIdentity });
  const rewards = projection.snapshot.interaction.content.surface.rewards;
  assert.equal(rewards[0].description, "18金币");
  assert.equal(rewards[1].description, "获得5层再生。");
  assert.equal(projection.snapshot.referents.find(item => item.referent_id === rewards[1].entity_id)
    .properties.description, "获得5层再生。");
  assert.ok([...projection.bindings.values()].some(binding =>
    binding.raw_request.args?.reward_ref === "native-potion"));

  state.rewards[1].description = "获得[img]res://images/packed/sprite_fonts/defect_energy_icon.png[/img]。";
  const icon = projectManagedCandidateDecision({ state, ...projectionIdentity });
  assert.equal(icon.snapshot.interaction.content.surface.rewards[1].description,
    "获得[defect_energy_icon.png]。");
});

test("a populated card reward publishes its lawfully viewable candidates under a public group id", () => {
  const state = (options) => ({
    type: "decision", decision: "reward_set",
    context: { act: 1, floor: 2, room_type: "Monster" },
    rewards: [
      { index: 0, native_ref: "reward-card-a", kind: "card_choice", name: "Add a card", card_options: options },
      { index: 1, native_ref: "reward-card-b", kind: "card_choice", name: "Add a card" }
    ],
    can_skip: true, is_terminal: true, can_proceed: true, room_ref: "combat-room-a", is_boss: false, player: player()
  });
  const options = [
    { index: 0, native_ref: "offer-card-1", id: "ZAP", name: "Zap", cost: 1, type: "Skill", rarity: "Basic" },
    { index: 1, native_ref: "offer-card-2", id: "DUALCAST", name: "Dualcast", cost: 1, type: "Skill", rarity: "Basic" }
  ];
  const projection = projectManagedCandidateDecision({ ...projectionIdentity, state: state(options) });
  const [first, second] = projection.snapshot.interaction.content.surface.rewards;
  assert.equal(first.card_options.length, 2);
  assert.deepEqual(first.card_options.map(card => card.definition_id), ["ZAP", "DUALCAST"]);
  assert.equal(first.card_options.some(card => Object.hasOwn(card, "index")), false);
  assert.equal(second.card_options, null);
  assert.notEqual(first.group_id, second.group_id);
  const text = JSON.stringify(projection.snapshot);
  for (const ref of ["reward-card-a", "reward-card-b", "offer-card-1", "offer-card-2"])
    assert.equal(text.includes(ref), false, ref);
  // The public group id is stable for the same native reward across snapshots.
  const again = projectManagedCandidateDecision({ ...projectionIdentity, state: state(options) });
  assert.equal(again.snapshot.interaction.content.surface.rewards[0].group_id, first.group_id);
});

test("the exact deck-enchant callback is presented as the native enchant selection", () => {
  const cards = [
    { index: 0, native_ref: "card-a", id: "STRIKE_DEFECT", name: "Strike", is_selected: false, is_selectable: true, is_deselectable: false },
    { index: 1, native_ref: "card-b", id: "DEFEND_DEFECT", name: "Defend", is_selected: false, is_selectable: true, is_deselectable: false }
  ];
  const state = (enchantment) => ({
    type: "decision", decision: "deck_card_select", context: { act: 1, floor: 3, room_type: "Event" }, player: player(),
    selector_ref: "selector-a", stage: "selecting", origin: "deck_enchant", enchantment,
    prompt: "Choose a card to enchant.", min_select: 1, max_select: 1,
    cancelable: false, require_manual_confirmation: true, selected_refs: [],
    can_cancel_selection: false, can_preview: false, can_cancel_preview: false, can_confirm: false, cards
  });
  const projection = projectManagedCandidateDecision({ ...projectionIdentity,
    state: state({ id: "SOWN", name: "Sown", description: "Gain energy.", amount: 1 }) });
  assert.equal(projection.snapshot.interaction.kind, "deck_enchant_selection");
  assert.deepEqual(projection.snapshot.interaction.content.surface.enchantment,
    { definition_id: "SOWN", name: "Sown", description: "Gain energy.", amount: 1 });
  assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.verb), ["select", "select"]);
  assert.equal(JSON.stringify(projection.snapshot).includes("selector-a"), false);
  // Without the named enchantment the page is not exact and publishes no action.
  const unnamed = projectManagedCandidateDecision({ ...projectionIdentity, state: state(null) });
  assert.equal(unnamed.snapshot.bound_actions.actions.length, 0);
});

test("multi-select opens an exact preview before confirmation and preserves native cancellation", async () => {
  const state = (selected = [], preview = false) => ({
    type: "decision", decision: "deck_card_select", context: { act: 1, floor: 3, room_type: "Event" }, player: player(),
    selector_ref: "selector-multi", origin: "deck_generic", stage: preview ? "preview" : "selecting",
    prompt: "Choose one or two cards.", min_select: 1, max_select: 2, cancelable: true,
    require_manual_confirmation: true, selected_refs: selected,
    can_cancel_selection: !preview, can_preview: !preview && selected.length >= 1,
    can_cancel_preview: preview, can_confirm: preview,
    cards: ["card-a", "card-b"].map((ref, index) => ({ index, native_ref: ref,
      id: "STRIKE", name: "Strike", is_selected: selected.includes(ref),
      is_selectable: !preview && !selected.includes(ref) && selected.length < 2,
      is_deselectable: !preview && selected.includes(ref) }))
  });
  const calls = [];
  const process = { async request(request) {
    calls.push(request);
    if (request.cmd === "start_run") return state();
    if (request.action === "select_deck_card") return state(["card-a"]);
    if (request.action === "preview_deck_selection") return state(["card-a"], true);
    if (request.action === "cancel_deck_preview") return state();
    if (request.action === "confirm_deck_selection") return eventState();
    throw new Error(`unexpected native request ${request.action}`);
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...projectionIdentity });
  let snapshot = await session.mount({ seed: "MULTI-STAGED-PREVIEW" });
  const step = async (verb, id) => {
    const action = snapshot.bound_actions.actions.find((action) => action.verb === verb);
    assert.ok(action, verb);
    const receipt = await session.submit({ requestId: id, expectedSnapshotId: snapshot.snapshot_id,
      boundActionId: action.bound_action_id });
    assert.equal(receipt.delivery, "delivered"); snapshot = receipt.successor;
    decodePlayerSnapshot(snapshot);
  };
  assert.equal(snapshot.bound_actions.actions.some((action) => action.verb === "open"), false);
  await step("select", "select-first");
  const tentative = snapshot;
  assert.deepEqual(snapshot.bound_actions.actions.map((action) => action.verb).sort(), ["cancel", "deselect", "open", "select"]);
  await step("open", "open-preview");
  assert.equal(snapshot.interaction.stage, "preview");
  assert.deepEqual(snapshot.bound_actions.actions.map((action) => action.verb), ["cancel", "confirm"]);
  assert.deepEqual(calls.at(-1), { cmd: "action", action: "preview_deck_selection", args: { selector_ref: "selector-multi" } });
  const stale = await session.submit({ requestId: "stale-preview", expectedSnapshotId: tentative.snapshot_id,
    boundActionId: tentative.bound_actions.actions.find((action) => action.verb === "open").bound_action_id });
  assert.equal(stale.reason_code, "stale_snapshot");
  assert.equal(calls.length, 3);
  await step("cancel", "return-preview");
  assert.equal(snapshot.interaction.stage, "selecting");
  await step("select", "select-again");
  await step("open", "preview-again");
  await step("confirm", "confirm-selected");
  assert.deepEqual(calls.at(-1), { cmd: "action", action: "confirm_deck_selection",
    args: { selector_ref: "selector-multi", selected_refs: "card-a" } });
  assert.equal(snapshot.interaction.kind, "event_option");

  // A producer claiming preview before the native minimum does not gain input.
  const invalid = state(); invalid.can_preview = true;
  assert.equal(projectManagedCandidateDecision({ ...projectionIdentity, state: invalid }).snapshot.bound_actions.status, "unavailable");
});

test("a non-skippable terminal reward set offers claims without proceed", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision", decision: "reward_set",
      context: { act: 1, floor: 2, room_type: "Monster" },
      rewards: [{ index: 0, native_ref: "required-reward", kind: "gold", name: "Gold" }],
      potion_slots_full: false, can_skip: false, is_terminal: true, can_proceed: false,
      room_ref: "combat-room", is_boss: false, player: player()
    }
  });
  assert.deepEqual([...projection.bindings.values()].map((binding) => binding.raw_request.action),
    ["select_reward"]);
});

test("blocks a full-belt potion reward and publishes exact native potion discards", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "reward_set",
      context: { act: 1, floor: 14, room_type: "Monster" },
      rewards: [{
        index: 0,
        native_ref: "reward-potion-a",
        kind: "potion",
        name: "Energy Potion",
        description: "Gain 2 Energy."
      }],
      potion_slots_full: true,
      can_skip: true,
      is_terminal: true,
      can_proceed: true,
      room_ref: "combat-room-a",
      is_boss: false,
      player: {
        ...player(),
        potions: [{
          slot: 0,
          native_ref: "owned-potion-a",
          id: "FIRE_POTION",
          name: "Fire Potion",
          description: "Deal 20 damage.",
          target_type: "AnyEnemy",
          usage: "CombatOnly"
        }]
      }
    }
  });
  assert.equal(decodePlayerSnapshot(projection.snapshot).data.status, "interactive");
  const surface = projection.snapshot.interaction.content.surface;
  assert.equal(surface.rewards[0].enabled, false);
  assert.equal(surface.potion_slots_full, true);
  assert.equal(surface.discardable_potions.length, 1);
  assert.equal(JSON.stringify(projection.snapshot).includes("reward-potion-a"), false);
  assert.equal(JSON.stringify(projection.snapshot).includes("owned-potion-a"), false);
  assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.label), [
    "Discard Fire Potion from slot 1 to make room",
    "Skip remaining rewards and continue"
  ]);
  const selected = chooseManagedPlayerEnvironmentAction(projection.snapshot);
  assert.equal(selected.label, "Discard Fire Potion from slot 1 to make room");
  const requests = [...projection.bindings.values()].map((binding) => binding.raw_request);
  assert.equal(requests.some((request) => request.action === "select_reward"), false);
  assert.equal(requests.some((request) => request.action === "discard_potion"
    && request.args.potion_ref === "owned-potion-a"), true);
});

test("projects treasure stages with explicit native choice and Host-local operands", () => {
  const closed = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "treasure_chest",
      context: { act: 1, floor: 6, room_type: "Treasure" },
      room_ref: "treasure-room-a",
      player: player()
    }
  });
  assert.equal(closed.snapshot.interaction.kind, "treasure_room");
  assert.equal(closed.snapshot.bound_actions.actions[0].label, "Open the treasure chest");
  assert.equal(JSON.stringify(closed.snapshot).includes("treasure-room-a"), false);
  assert.deepEqual(closed.snapshot.interaction.content.surface, { kind: "treasure_room",non_combat_discardable_potions: [],stage:"closed",chest_opened:false,
    relics:[],can_choose:false,can_skip:false,can_proceed:false });
  assert.equal(closed.snapshot.bound_actions.actions[0].subject_referent_id,null);

  const choosing = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "treasure_relic",
      context: { act: 1, floor: 6, room_type: "Treasure" },
      room_ref: "treasure-room-a",
      relics: [{
        index: 0,
        native_ref: "relic-a",
        id: "RELIC.BAG_OF_PREPARATION",
        name: "Bag of Preparation",
        description: "Draw two additional cards.", keywords: [], card_previews: [], hover_facts_complete: true,
        rarity: "Common"
      }],
      can_skip: true,
      player: player()
    }
  });
  assert.equal(decodePlayerSnapshot(choosing.snapshot).data.status, "interactive");
  assert.equal(choosing.snapshot.interaction.kind, "treasure_room");
  assert.deepEqual(choosing.snapshot.bound_actions.actions.map((action) => action.verb).sort(), ["activate", "skip"]);
  assert.equal(JSON.stringify(choosing.snapshot).includes("relic-a"), false);
  assert.equal(JSON.stringify(choosing.snapshot).includes("treasure-room-a"), false);
  const requests = [...choosing.bindings.values()].map((binding) => binding.raw_request);
  assert.equal(requests.some((request) => request.args?.relic_ref === "relic-a"), true);
  assert.equal(requests.some((request) => request.args?.room_ref === "treasure-room-a"), true);
});

test("materializes every combat card-target pair and keeps raw operands Host-local", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "combat_play",
      context: { act: 1, floor: 2, room_type: "Combat" },
      is_play_phase: true,
      round: 1,
      energy: 3,
      max_energy: 3,
      hand: [
        { index: 0, native_ref: "card-strike", valid_target_refs: ["enemy-a", "enemy-b"], id: "CARD.STRIKE_IRONCLAD", name: "Strike", can_play: true, target_type: "AnyEnemy", type: "Attack", rarity: "Basic", cost: 1 },
        { index: 1, native_ref: "card-defend", id: "CARD.DEFEND_IRONCLAD", name: "Defend", can_play: true, target_type: "Self", type: "Skill", rarity: "Basic", cost: 1 }
      ],
      enemies: [
        { index: 0, native_ref: "enemy-a", name: "A", hp: 10, max_hp: 10 },
        { index: 1, native_ref: "enemy-b", name: "B", hp: 10, max_hp: 10 }
      ],
      player: { ...player(), native_ref: "player-a" }
    }
  });
  assert.equal(projection.snapshot.bound_actions.actions.length, 4);
  assert.deepEqual(
    projection.snapshot.bound_actions.actions.map((action) => action.verb).sort(),
    ["end_turn", "play", "play", "play"]
  );
  assert.equal(JSON.stringify(projection.snapshot).includes("card_index"), false);
  assert.equal(JSON.stringify(projection.snapshot).includes("native_ref"), false);
  const playableCards = projection.snapshot.interaction.content.surface.playable_cards;
  assert.deepEqual(Object.keys(playableCards[0]).sort(), ["entity_id", "name", "target_entity_ids"]);
  assert.equal(playableCards.some((card) => "hand_index" in card || "definition_id" in card), false);
  const visibleHand = projection.snapshot.interaction.content.context.player.hand;
  assert.deepEqual(visibleHand.map((card) => card.definition_id), ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"]);
  assert.equal([...projection.bindings.values()].some((binding) => binding.raw_request.args?.card_ref === "card-strike"), true);
});

test("does not publish combat mutations outside the native player play window", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "combat_play",
      context: { act: 1, floor: 2, room_type: "Combat" },
      round: 1,
      energy: 3,
      max_energy: 3,
      encounter_type: "normal",
      turn_owner: "enemy",
      is_play_phase: false,
      exhaust_pile_count: 0,
      orb_slots: 0,
      orbs: [],
      companions: [],
      player_statuses: [],
      hand: [{
        index: 0,
        native_ref: "card-strike",
        valid_target_refs: ["enemy-a"],
        id: "CARD.STRIKE_IRONCLAD",
        name: "Strike",
        can_play: true,
        target_type: "AnyEnemy",
        type: "Attack",
        rarity: "Basic",
        cost: 1
      }],
      enemies: [{
        index: 0,
        native_ref: "enemy-a",
        id: "MONSTER.CULTIST",
        combat_id: 1,
        name: "A",
        hp: 10,
        max_hp: 10,
        block: 0,
        statuses: [],
        intents: []
      }],
      player: { ...player(), native_ref: "player-a" }
    }
  });
  assert.equal(projection.snapshot.status, "visible_unsupported");
  assert.equal(projection.snapshot.interaction.content.surface.can_end_turn, false);
  assert.deepEqual(projection.snapshot.interaction.content.surface.playable_cards, []);
  assert.deepEqual(projection.snapshot.bound_actions.actions, []);
  assert.equal(projection.bindings.size, 0);
});

test("keeps visible unplayable cards as hand facts without creating action authority", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "combat_play",
      context: { act: 1, floor: 2, room_type: "Combat" },
      is_play_phase: true,
      round: 1,
      energy: 0,
      max_energy: 3,
      hand: [{
        index: 0,
        native_ref: "card-strike",
        valid_target_refs: ["enemy-a"],
        id: "CARD.STRIKE_IRONCLAD",
        name: "Strike",
        can_play: false,
        is_selected: false,
        unplayable_reason: "EnergyCostTooHigh",
        target_type: "AnyEnemy",
        type: "Attack",
        rarity: "Basic",
        cost: 1
      }],
      enemies: [{ index: 0, native_ref: "enemy-a", name: "A", hp: 10, max_hp: 10 }],
      player: { ...player(), native_ref: "player-a" }
    }
  });
  assert.deepEqual(projection.snapshot.interaction.content.surface.playable_cards, []);
  const cardReferent = projection.snapshot.referents.find((referent) =>
    referent.referent_id === projection.snapshot.interaction.content.context.player.hand[0].entity_id);
  assert.equal(cardReferent.role, "hand");
  assert.equal(cardReferent.state.selected, false);
  assert.equal(cardReferent.properties.definition_id, "STRIKE_IRONCLAD");
  assert.equal(cardReferent.properties.unplayable_reason, "EnergyCostTooHigh");
  assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.verb), ["end_turn"]);
  assert.equal([...projection.bindings.values()].some((binding) =>
    binding.raw_request.args?.card_ref === "card-strike"), false);
});

test("projects native potion bindings without exposing Host-local identity", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: {
      type: "decision",
      decision: "combat_play",
      context: {},
      is_play_phase: true,
      hand: [],
      enemies: [{ index: 0, native_ref: "enemy-a", name: "A", hp: 10, max_hp: 10 }],
      player: {
        ...player(),
        native_ref: "player-a",
        potions: [{
          slot: 1,
          native_ref: "potion-a",
          name: "Fire Potion",
          target_type: "AnyEnemy",
          can_use: true,
          can_discard: true,
          binding_supported: true,
          valid_target_refs: ["enemy-a"]
        }]
      }
    }
  });
  assert.equal(projection.snapshot.status, "interactive");
  assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.verb).sort(), [
    "activate",
    "end_turn",
    "use"
  ]);
  assert.equal(JSON.stringify(projection.snapshot).includes("native_ref"), false);
  const requests = [...projection.bindings.values()].map((binding) => binding.raw_request);
  assert.equal(requests.some((request) => request.args?.potion_ref === "potion-a"), true);
  const use = projection.snapshot.bound_actions.actions.find(action => action.verb === "use");
  assert.equal(use.label, "Use Fire Potion -> A");
  assert.equal(use.arguments.length, 1);
});

test("fails closed when visible potion actionability is not represented", () => {
  const combat = {
    type: "decision",
    decision: "combat_play",
    context: {},
    hand: [],
    enemies: [],
    player: { ...player(), potions: [{ index: 0, name: "Potion", target_type: "Self" }] }
  };
  const projection = projectManagedCandidateDecision({ state: combat, ...projectionIdentity });
  assert.equal(projection.snapshot.status, "visible_unsupported");
  assert.equal(projection.snapshot.bound_actions.status, "unavailable");
  assert.equal(projection.bindings.size, 0);
});

test("combat projection fails closed for target types without an exact current binding", () => {
  const base = {
    type: "decision",
    decision: "combat_play",
    context: {
      act: 1, act_index: 0, act_definition_id: "OVERGROWTH", act_name: "Overgrowth",
      floor: 2, total_floor: 2, ascension: 0, room_type: "Combat",
      bosses: [{ id: "VANTOM_BOSS", name: "Vantom", order: 0 }], modifiers: []
    },
    encounter_type: "normal", turn_owner: "player", round: 1,
    is_play_phase: true, combat_hud_complete: true, potion_states: [],
    stars: null, energy: 3, max_energy: 3, draw_pile_count: 0, discard_pile_count: 0, exhaust_pile_count: 0, orb_slots: 0, orbs: [], companions: [], player_statuses: [],
    hand: [{
      index: 0, native_ref: "card-a", id: "CARD.STRIKE", name: "Strike", can_play: true,
      target_type: "AnyEnemy", valid_target_refs: ["enemy-a"]
    }],
    enemies: [{
      index: 0, native_ref: "enemy-a", id: "MONSTER.CULTIST", combat_id: 1,
      health_bar_visible: true, hp_display_mode: "normal", hp_numbers_available: true, name: "Enemy A", hp: 10, max_hp: 10, block: 0, statuses: [], intents: []
    }],
    player: { ...player(), block: 0, native_ref: "player-a", character_id: "IRONCLAD", max_potion_slots: 3, potions: [] }
  };

  for (const targetType of ["AnyAlly", "FutureUnknownTarget"]) {
    const projection = projectManagedCandidateDecision({
      state: { ...base, hand: [{ ...base.hand[0], target_type: targetType }] }, ...projectionIdentity
    });
    assert.equal(projection.snapshot.bound_actions.status, "unavailable", targetType);
    assert.deepEqual(projection.snapshot.bound_actions.actions, [], targetType);
    assert.equal(projection.bindings.size, 0, targetType);
    assert.equal(projection.snapshot.completeness.status, "partial", targetType);
    assert.ok(projection.snapshot.completeness.missing.includes("native_unsupported_card_target_type"), targetType);
  }

  for (const targetType of ["None", "Self", "AllEnemies", "RandomEnemy", "AnyPlayer", "AllAllies", "TargetedNoCreature", "Osty"]) {
    const projection = projectManagedCandidateDecision({
      state: { ...base, hand: [{ ...base.hand[0], target_type: targetType, valid_target_refs: [] }] }, ...projectionIdentity
    });
    assert.equal(projection.snapshot.bound_actions.status, "complete", targetType);
    assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.verb), ["play", "end_turn"]);
  }

  for (const targetType of ["None", "Self", "AllEnemies", "RandomEnemy", "AllAllies"]) {
    const projection = projectManagedCandidateDecision({
      state: { ...base, hand: [], player: { ...base.player, potions: [{
        index: 0, slot: 0, native_ref: "potion-a", name: "Test Potion", can_use: true,
        can_discard: false, binding_supported: true, target_type: targetType
      }] } }, ...projectionIdentity
    });
    assert.equal(projection.snapshot.bound_actions.status, "complete", targetType);
    assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.verb), ["end_turn", "use"]);
  }

  const supportedButUnavailable = projectManagedCandidateDecision({
    state: { ...base, hand: [], player: { ...base.player, potions: [{
      index: 0, slot: 2, native_ref: "potion-unavailable", id: "POTION.TEST", name: "Test Potion",
      can_use: false, can_discard: true, binding_supported: true, target_type: "Self",
      hover_facts_complete: true, keywords: [], card_previews: []
    }] } }, ...projectionIdentity
  });
  assert.equal(supportedButUnavailable.snapshot.completeness.status, "complete");
  assert.equal(supportedButUnavailable.snapshot.bound_actions.status, "complete");
  assert.equal(supportedButUnavailable.snapshot.interaction.content.surface.discardable_potions.length, 1);
  assert.equal(supportedButUnavailable.snapshot.interaction.content.surface.discardable_potions[0].can_use, false);
  assert.equal(supportedButUnavailable.snapshot.interaction.content.surface.discardable_potions[0].slot, 2);
  assert.equal(supportedButUnavailable.snapshot.interaction.content.surface.discardable_potions[0].definition_id, "TEST");
  assert.deepEqual(supportedButUnavailable.snapshot.bound_actions.actions.map((action) => action.verb), ["end_turn", "activate"]);

  const unavailableUnknownTarget = projectManagedCandidateDecision({
    state: { ...base, hand: [], player: { ...base.player, potions: [{
      index: 0, slot: 0, native_ref: "potion-unbound", id: "POTION.TEST", name: "Test Potion",
      can_use: false, can_discard: true, binding_supported: false, target_type: "FutureUnknownTarget",
      hover_facts_complete: true, keywords: [], card_previews: []
    }] } }, ...projectionIdentity
  });
  assert.equal(unavailableUnknownTarget.snapshot.completeness.status, "partial");
  assert.equal(unavailableUnknownTarget.snapshot.bound_actions.status, "unavailable");
  assert.deepEqual(unavailableUnknownTarget.snapshot.bound_actions.actions, []);
  assert.equal(unavailableUnknownTarget.bindings.size, 0);
  assert.ok(unavailableUnknownTarget.snapshot.completeness.missing.includes(
    "native_unsupported_potion_target_type_or_binding"));

  const potion = {
    ...base,
    hand: [],
    player: { ...base.player, potions: [{
      index: 0, slot: 0, native_ref: "potion-a", id: "POTION.TEST", name: "Test Potion",
      can_use: true, can_discard: false, binding_supported: true, target_type: "FutureUnknownTarget"
    }] }
  };
  const potionProjection = projectManagedCandidateDecision({ state: potion, ...projectionIdentity });
  assert.equal(potionProjection.snapshot.bound_actions.status, "unavailable");
  assert.deepEqual(potionProjection.snapshot.bound_actions.actions, []);
  assert.equal(potionProjection.bindings.size, 0);
  assert.ok(potionProjection.snapshot.completeness.missing.includes(
    "native_unsupported_potion_target_type_or_binding"));

  for (const targetType of ["AnyAlly", "AnyPlayer", "TargetedNoCreature", "Osty", "FutureUnknownTarget"]) {
    const projection = projectManagedCandidateDecision({
      state: { ...base, hand: [], player: { ...base.player, potions: [{
        index: 0, slot: 0, native_ref: "potion-a", id: "POTION.TEST", name: "Test Potion",
        can_use: true, can_discard: false, binding_supported: true, target_type: targetType
      }] } }, ...projectionIdentity
    });
    assert.equal(projection.snapshot.bound_actions.status, "unavailable", targetType);
    assert.deepEqual(projection.snapshot.bound_actions.actions, [], targetType);
    assert.equal(projection.bindings.size, 0, targetType);
  }
});

test("projects shop entries from native actionability and keeps purchase identities Host-local", () => {
  const projection = projectManagedCandidateDecision({
    ...projectionIdentity,
    state: { ...shopInventory(), room_ref: "room-a", shop_ref: "room-a",
      presentation_owner: { ...shopInventory().presentation_owner, room_ref: "room-a" } }
  });
  assert.equal(projection.snapshot.status, "interactive");
  assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.label).sort(), [
    "Buy Fire Potion for 50 gold",
    "Buy Pommel Strike for 50 gold",
    "Buy card removal for 75 gold",
    "Close shop inventory"
  ]);
  assert.equal(projection.snapshot.interaction.content.surface.cards[0].card.cost, "1");
  assert.equal(projection.snapshot.interaction.content.surface.cards[0].price, 50);
  assert.equal(JSON.stringify(projection.snapshot).includes("native_ref"), false);
  const rawRequests = [...projection.bindings.values()].map((binding) => binding.raw_request);
  assert.equal(rawRequests.some((request) => request.args?.entry_ref === "card-entry-a"), true);
  assert.equal(rawRequests.some((request) => request.args?.room_ref === "room-a"), true);
});

test("state-bound run-deck reads pass the strict SDK and reject stale tokens", async () => {
  const process = {
    async request(request) {
      assert.equal(request.cmd, "start_run");
      assert.equal(request.lang, "zh");
      return eventState();
    }
  };
  const session = new ManagedPlayerEnvironmentSession({
    process,
    runtimeInstanceId: "managed-runtime-test",
    environmentFingerprint: "managed-environment-test",
    language: "zh"
  });
  const snapshot = await session.mount({ seed: "TEST" });
  const read = session.read({ readId: snapshot.reads[0].read_id, expectedSnapshotId: snapshot.snapshot_id });
  const decoded = decodePlayerRead(read).data;
  assert.equal(decoded.content.card_count, 1);
  assert.equal(decoded.completeness.status, "complete");
  assert.equal(decoded.content.cards[0].rarity, "Basic");
  assert.equal(JSON.stringify(decoded).includes("deck-card-a"), false);
  assert.throws(() => session.read({ readId: snapshot.reads[0].read_id, expectedSnapshotId: "old" }), /stale_snapshot/u);
});

test("run-deck read remains explicitly partial when game-owned card rendering is incomplete", () => {
  const state = eventState();
  delete state.player.deck[0].native_ref;
  state.player.deck[0].description = "Deal {Damage:diff()} damage.";
  const projection = projectManagedCandidateDecision({ state, ...projectionIdentity });
  const session = projection.reads.get(projection.snapshot.reads[0].read_id);
  assert.equal(session.completeness.status, "partial");
  assert.deepEqual(session.completeness.missing, [
    "native_card_entity_identity",
    "fully_rendered_localized_dynamic_text"
  ]);
});

test("enforces stale rejection and request idempotency before raw execution", async () => {
  let actionCalls = 0;
  const process = {
    async request(request) {
      if (request.cmd === "start_run") return eventState();
      actionCalls += 1;
      return { ...eventState(), event_name: "Successor" };
    }
  };
  const session = new ManagedPlayerEnvironmentSession({
    process,
    runtimeInstanceId: "managed-runtime-test",
    environmentFingerprint: "managed-environment-test"
  });
  const snapshot = await session.mount({ seed: "TEST" });
  const action = snapshot.bound_actions.actions[0];
  const stale = await session.submit({
    requestId: "stale",
    expectedSnapshotId: "old",
    boundActionId: action.bound_action_id
  });
  assert.equal(decodePlayerReceipt(stale).data.reason_code, "stale_snapshot");
  assert.equal(actionCalls, 0);

  const input = {
    requestId: "same",
    expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: action.bound_action_id
  };
  const first = await session.submit(input);
  const duplicate = await session.submit(input);
  assert.equal(first.delivery, "delivered");
  assert.deepEqual(duplicate, first);
  assert.equal(actionCalls, 1);
});

test("same-state reset rotates snapshot authority and rejects the prior episode", async () => {
  let actionCalls = 0;
  const process = {
    async request(request) {
      if (request.cmd === "start_run" || request.cmd === "reset_run") return eventState();
      actionCalls += 1;
      return { ...eventState(), event_name: "Successor" };
    }
  };
  const session = new ManagedPlayerEnvironmentSession({
    process,
    runtimeInstanceId: "managed-runtime-test",
    environmentFingerprint: "managed-environment-test"
  });
  const first = await session.mount({ seed: "SAME" });
  const firstAction = first.bound_actions.actions[0];
  const second = await session.mount({ seed: "SAME", reset: true });
  const secondAction = second.bound_actions.actions[0];
  assert.notEqual(second.snapshot_id, first.snapshot_id);
  assert.notEqual(second.interaction.interaction_id, first.interaction.interaction_id);
  assert.notEqual(secondAction.bound_action_id, firstAction.bound_action_id);

  const stale = await session.submit({
    requestId: "old-episode",
    expectedSnapshotId: first.snapshot_id,
    boundActionId: firstAction.bound_action_id
  });
  assert.equal(stale.delivery, "not_delivered");
  assert.equal(stale.reason_code, "stale_snapshot");
  assert.equal(actionCalls, 0);

  const delivered = await session.submit({
    requestId: "current-episode",
    expectedSnapshotId: second.snapshot_id,
    boundActionId: secondAction.bound_action_id
  });
  assert.equal(delivered.delivery, "delivered");
  assert.equal(actionCalls, 1);
});

test("taints the process after unknown delivery and never retries", async () => {
  let actionCalls = 0;
  const process = {
    async request(request) {
      if (request.cmd === "start_run") return eventState();
      actionCalls += 1;
      throw new Error("transport lost after write");
    }
  };
  const session = new ManagedPlayerEnvironmentSession({
    process,
    runtimeInstanceId: "managed-runtime-test",
    environmentFingerprint: "managed-environment-test"
  });
  const snapshot = await session.mount({ seed: "TEST" });
  const action = snapshot.bound_actions.actions[0];
  const unknown = await session.submit({
    requestId: "unknown",
    expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: action.bound_action_id
  });
  assert.equal(unknown.delivery, "unknown");
  assert.equal(unknown.retry.allowed, false);
  const refused = await session.submit({
    requestId: "after",
    expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: action.bound_action_id
  });
  assert.equal(refused.reason_code, "runtime_tainted_after_unknown");
  assert.equal(actionCalls, 1);
});

test("native delivery with rejected successor projection preserves delivered Receipt and closes mutation", async () => {
  let actionCalls = 0;
  const invalidSuccessor = {
    type: "decision", decision: "map_select",
    context: { act: 1, act_index: 0, act_definition_id: "OVERGROWTH",
      act_name: "Overgrowth", floor: 2, total_floor: 2, ascension: 0,
      bosses: [{ id: "VANTOM_BOSS", name: "Vantom", order: 0 }], modifiers: [] },
    choices: [{ col: 3, row: 0, type: "Monster", native_ref: "map-point-a",
      children: [{ col: 2, row: 1, type: "Monster" }] }],
    visible_map: { type: "map", rows: [[{ col: 2, row: 1, type: "Monster",
      children: [{ col: 3, row: 16 }], visited: false, current: false }]],
      boss: { col: 3, row: 16, type: "Boss" }, current_coord: null },
    player: { ...player(), native_ref: "player-a", character_id: "IRONCLAD",
      max_potion_slots: 3, gold: "bad" }
  };
  const process = {
    async request(request) {
      if (request.cmd === "start_run") return eventState();
      if (request.cmd === "action") {
        actionCalls += 1;
        return invalidSuccessor;
      }
      if (request.cmd === "get_map") return invalidSuccessor.visible_map;
      throw new Error(`unexpected ${request.cmd}`);
    }
  };
  const session = new ManagedPlayerEnvironmentSession({
    process, runtimeInstanceId: "managed-runtime-test",
    environmentFingerprint: "managed-environment-test"
  });
  const snapshot = await session.mount({ seed: "TEST" });
  const action = snapshot.bound_actions.actions[0];
  const exact = { requestId: "projection-failed", expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: action.bound_action_id };
  const first = await session.submit(exact);
  assert.equal(first.delivery, "delivered");
  assert.equal(first.reason_code, "managed_successor_projection_failed");
  assert.equal(first.successor, null);
  assert.equal(first.retry.allowed, false);
  assert.equal(session.tainted, true);
  assert.throws(() => session.observe(), /managed_session_tainted_after_successor_projection_failure/);
  assert.throws(() => session.read({ readId: "old", expectedSnapshotId: snapshot.snapshot_id }),
    /managed_session_tainted_after_successor_projection_failure/);
  assert.deepEqual(await session.submit(exact), first);
  const later = await session.submit({ ...exact, requestId: "later" });
  assert.equal(later.delivery, "not_delivered");
  assert.equal(later.reason_code, "runtime_tainted_after_successor_projection_failure");
  assert.equal(actionCalls, 1);
});


test("complete unopened card reward alternatives preserve actual outcomes and outer abandonment permission", () => {
  const state = { type: "decision", decision: "reward_set", context: {}, player: player(),
    can_skip: true, is_terminal: true, can_proceed: true, can_leave_with_unclaimed_rewards: false,
    room_ref: "room-original", rewards: [{ native_ref: "group-original", kind: "card_choice", name: "Reward",
      card_options: [], card_alternatives: [{ option_id: "Skip", title: "Skip", after_selected: "end_selection_and_do_not_complete_reward", is_native_skip: false },
        { option_id: "SACRIFICE", title: "Sacrifice", after_selected: "end_selection_and_complete_reward", is_native_skip: false }],
      card_alternatives_complete: true, can_skip_selection: true, can_reroll: false, selection_completes_reward: true,
      native_relic_refresh_subscription: "attached" }] };
  const result = projectManagedCandidateDecision({ state, ...projectionIdentity }).snapshot.interaction.content.surface;
  assert.deepEqual(result.rewards[0].card_alternatives, state.rewards[0].card_alternatives);
  assert.equal(result.rewards[0].card_alternatives_complete, true);
  assert.equal(result.rewards[0].native_relic_refresh_subscription, "attached");
  assert.equal(result.can_leave_with_unclaimed_rewards, false);
  assert.equal(JSON.stringify(result).includes("group-original"), false);
  for (const mutate of [reward => { reward.card_alternatives = null; },
    reward => { reward.card_alternatives[0].after_selected = "unknown"; },
    reward => { delete reward.selection_completes_reward; },
    reward => { reward.card_alternatives[1].option_id = "Skip"; }]) {
    const changed=structuredClone(state); mutate(changed.rewards[0]);
    const output=projectManagedCandidateDecision({ state: changed, ...projectionIdentity }).snapshot.interaction.content.surface;
    assert.equal(output.rewards[0].card_alternatives_complete, false);
  }
});


test("opened reward retains exact parent, set and real card identity while executing original creation result", () => {
  const aliases = new Map();
  const card = { native_ref: "real-card", id: "CARD.STRIKE", name: "Strike", type: "Attack", cost: 1,
    rarity: "Basic", is_upgraded: false, description: "Deal 6 damage.", render_complete: true, keywords: [], card_previews: [] };
  const common = eventState();
  const outer = projectManagedCandidateDecision({ ...projectionIdentity, publicEntityAliases: aliases,
    state: { ...common, decision: "reward_set", reward_set_ref: "native-set", is_terminal: false,
      rewards: [{ native_ref: "native-reward", kind: "card_choice", name: "Add card", card_options: [card] }] } });
  const opened = { ...common, decision: "card_reward", parent_status: "bound",
    parent_reward_ref: "native-reward", reward_set_ref: "native-set", selection_completes_reward: true,
    native_relic_refresh_subscription: "absent",
    cards: [{ ...card, native_ref: "creation-result", card_entity_ref: "real-card", index: 0 }], alternatives: [] };
  const project = state => projectManagedCandidateDecision({ ...projectionIdentity, sequence: 2, state,
    publicEntityAliases: aliases });
  const inner = project(opened);
  const outerSurface = outer.snapshot.interaction.content.surface;
  const innerSurface = inner.snapshot.interaction.content.surface;
  // Reference VisibleInteractionProjection/CardRewardSurfaceReader publish
  // ready and reward_flow, with no second stage/can_skip surface inventory.
  assert.equal(inner.snapshot.interaction.stage, "ready");
  assert.deepEqual(inner.snapshot.interaction.content.context, { kind: "reward_flow", reward_kind: "card_reward" });
  assert.equal(Object.hasOwn(innerSurface, "stage"), false);
  assert.equal(Object.hasOwn(innerSurface, "can_skip"), false);
  assert.equal(innerSurface.parent_group_id, outerSurface.rewards[0].group_id);
  assert.equal(innerSurface.reward_set_id, outerSurface.reward_set_id);
  assert.equal(innerSurface.cards[0].entity_id, outerSurface.rewards[0].card_options[0].entity_id);
  assert.equal(innerSurface.selection_completes_reward, true);
  assert.equal(innerSurface.native_relic_refresh_subscription, "absent");
  const skipProjection = project({ ...opened, alternatives: [{ index: 0, id: "Skip", option_id: "Skip", native_ref: "skip-real",
    name: "Skip", title: "Skip", after_selected: "end_selection_and_do_not_complete_reward", is_native_skip: true }] });
  assert.equal(skipProjection.snapshot.bound_actions.actions.find(action => action.label === "Skip").verb, "activate");
  assert.deepEqual([...inner.bindings.values()][0].raw_request.args, { card_ref: "creation-result" });
  assert.equal(JSON.stringify(inner.snapshot).includes("creation-result"), false);
  assert.equal(JSON.stringify(inner.snapshot).includes("real-card"), false);
  for (const key of ["parent_reward_ref", "parent_status", "selection_completes_reward"]) {
    const missing = structuredClone(opened); delete missing[key];
    assert.ok(project(missing).snapshot.completeness.missing.includes("exact_card_reward_parent_and_card_identity"), key);
  }
  const missingCard = structuredClone(opened); delete missingCard.cards[0].card_entity_ref;
  assert.ok(project(missingCard).snapshot.completeness.missing.includes("exact_card_reward_parent_and_card_identity"));
});

test("deck hover details are separate snapshot-bound Reads with the same public card identity", () => {
  const state = eventState();
  const preview = { entity_id: null, definition_id: "FUEL", name: "Fuel", type: "Skill", cost: "0",
    description: "Gain 1 Energy.", rarity: "Token", is_upgraded: false, is_selected: false,
    target_type: "Self", can_play: null, keywords: [], card_previews: [] };
  state.player.deck[0].public_details = {
    keywords: [{ name: "Energy", description: "Used to play cards." }], card_previews: [preview]
  };
  state.player.deck.push({ ...structuredClone(state.player.deck[0]), native_ref: "second-identical-card" });
  const projection = projectManagedCandidateDecision({ state, ...projectionIdentity });
  const reads = [...projection.reads.values()];
  const deck = reads.find(read => read.descriptor.kind === "run_deck");
  const details = reads.filter(read => read.descriptor.kind === "surface_card");
  assert.equal(details.length, 2);
  assert.notEqual(details[0].descriptor.target_referent_id, details[1].descriptor.target_referent_id);
  for (const [index, detail] of details.entries()) {
    assert.equal(detail.descriptor.snapshot_bound, true);
    assert.equal(detail.descriptor.target_referent_id, deck.content.cards[index].entity_id);
    assert.equal(detail.content.entity_id, deck.content.cards[index].entity_id);
    assert.equal(detail.content.parent_read_id, deck.descriptor.read_id);
    assert.equal(detail.content.public_details_revision, "1");
    assert.equal(detail.content.display_context, "deck");
    assert.equal(detail.content.display_owner_entity_id, null);
    assert.deepEqual(detail.content.keywords, state.player.deck[index].public_details.keywords);
    assert.deepEqual(detail.content.card_previews, [preview]);
    assert.equal(detail.completeness.status, "complete");
    for (const key of ["target_type", "can_play", "keywords", "card_previews"])
      assert.equal(Object.hasOwn(deck.content.cards[index], key), false, key);
  }
  assert.equal(JSON.stringify(reads).includes("second-identical-card"), false);
});

test("missing or failed native deck details keep the sealed basis partial without hiding the deck", async () => {
  for (const mutate of [card => { delete card.public_details; }, card => {
    card.public_details_complete = false;
    card.public_details_missing = ["native_selector_hover_card_incomplete"];
  }]) {
    const state = eventState();
    mutate(state.player.deck[0]);
    const session = new ManagedPlayerEnvironmentSession({
      process: { async request() { return state; } },
      runtimeInstanceId: projectionIdentity.runtimeInstanceId,
      environmentFingerprint: projectionIdentity.environmentFingerprint
    });
    const snapshot = await session.mount({ seed: "DECK-DETAILS" });
    const detail = snapshot.reads.find(read => read.kind === "surface_card");
    const read = session.read({ readId: detail.read_id, expectedSnapshotId: snapshot.snapshot_id });
    assert.equal(decodePlayerRead(read).data.completeness.status, "partial");
    assert.ok(read.completeness.missing.length > 0);
    assert.throws(() => session.read({ readId: detail.read_id, expectedSnapshotId: "old" }), /stale_snapshot/u);
    const deck = snapshot.reads.find(read => read.kind === "run_deck");
    assert.equal(session.read({ readId: deck.read_id, expectedSnapshotId: snapshot.snapshot_id }).completeness.status, "complete");
    const captured = captureManagedPublicBasis(session, { generationId: "generation-1", gameContinuityId: "continuity-1", ordinal: 1 });
    assert.equal(captured.basis.capture_status, "partial");
    assert.ok(captured.basis.missing.includes(`read:${detail.read_id}:partial`));
  }
});

test("reward diagnostics distinguish incomplete native rendering from the exact parent binding", () => {
  const state = { ...eventState(), decision: "card_reward", parent_status: "bound",
    parent_reward_ref: "native-reward", reward_set_ref: "native-set", selection_completes_reward: true,
    alternatives: [], cards: [{ ...player().deck[0], native_ref: "creation-result", card_entity_ref: "real-card", index: 0,
      render_complete: false, render_missing: ["native_selector_hover_card_incomplete"], keywords: [], card_previews: [] }] };
  const projection = projectManagedCandidateDecision({ state, ...projectionIdentity });
  const missing = projection.snapshot.completeness.missing;
  assert.ok(missing.includes("exact_card_reward_parent_and_card_identity"));
  assert.ok(missing.includes("card_reward_render:0:native_selector_hover_card_incomplete"));
  assert.equal(missing.includes("native_card_reward_parent_identity"), false);
  assert.equal(missing.includes("native_card_reward_card_identity"), false);
  assert.deepEqual(projection.snapshot.bound_actions.actions, []);
  state.cards[0].render_complete = true;
  state.cards[0].render_missing = [];
  state.cards[0].is_upgraded = false;
  delete state.parent_reward_ref;
  const parentMissing = projectManagedCandidateDecision({ state, ...projectionIdentity }).snapshot.completeness.missing;
  assert.ok(parentMissing.includes("native_card_reward_parent_identity"));
  assert.equal(parentMissing.some(reason => reason.startsWith("card_reward_render:")), false);
});

test("same raw capture supplies the full map Read off page without creating travel actions", () => {
  const state = eventState();
  state.context.current_map = { kind: "map", status: "captured", gap: null, act_index: 0,
    current_position: null, visited: [], nodes: [
      { native_ref: "start", col: 0, row: -1, point_type: "ancient", state: "travelable", children: [{ col: 0, row: 0, point_type: "monster" }] },
      { native_ref: "room", col: 0, row: 0, point_type: "monster", state: "untravelable", children: [{ col: 0, row: 1, point_type: "boss" }] },
      { native_ref: "boss", col: 0, row: 1, point_type: "boss", state: "untravelable", children: [] }
    ] };
  const projection = projectManagedCandidateDecision({ state, ...projectionIdentity });
  const read = [...projection.reads.values()].find(value => value.descriptor.kind === "map");
  assert.equal(read.completeness.status, "complete");
  assert.equal(read.content.current_position, null);
  assert.equal(read.content.nodes.length, 3);
  assert.equal(read.content.nodes[1].state, "untravelable");
  assert.equal(projection.snapshot.interaction.kind, "event_option");
  assert.equal([...projection.bindings.values()].some(value => value.raw_request.action === "select_map_node"), false);
  assert.equal(JSON.stringify(projection.snapshot).includes("current_map"), false);
  assert.equal(JSON.stringify(projection.snapshot).includes('"native_ref"'), false);
  for (const mutate of [raw => { raw.nodes[1].col = 0; raw.nodes[1].row = -1; },
    raw => { raw.nodes[1].native_ref = raw.nodes[0].native_ref; },
    raw => { raw.nodes[0].children[0].row = 99; }, raw => { raw.status = "unsupported"; }]) {
    const changed = structuredClone(state); mutate(changed.context.current_map);
    const partial = projectManagedCandidateDecision({ state: changed, ...projectionIdentity });
    assert.equal([...partial.reads.values()].find(value => value.descriptor.kind === "map").completeness.status, "partial");
  }
  const mapState = { ...state, decision: "map_select", choices: [{ native_ref: "start", col: 0, row: -1, type: "Ancient" }] };
  const current = projectManagedCandidateDecision({ state: mapState, ...projectionIdentity });
  assert.equal(current.snapshot.interaction.content.context.current_position, null);
  const mapRead = [...current.reads.values()].find(value => value.descriptor.kind === "map");
  assert.deepEqual(mapRead.content, current.snapshot.interaction.content.context);
  assert.equal(current.bindings.size, 1);
  assert.equal([...current.bindings.values()][0].raw_request.args.map_point_ref, "start");
});


test("populated reward candidate Reads retain each actual native card, group and hover facts across open", () => {
  const card = { native_ref: "card-real", id: "COLD_SNAP", name: "寒流", cost: 1,
    type: "Attack", rarity: "Common", target_type: "AnyEnemy", is_upgraded: false,
    description: "造成6点伤害。生成1个冰霜充能球。", render_complete: true, render_missing: [],
    keywords: [{ name: "冰霜", description: "获得格挡。" }], card_previews: [] };
  card.public_details = { keywords: card.keywords, card_previews: [] };
  const aliases = new Map(), common = eventState();
  const project = state => projectManagedCandidateDecision({ ...projectionIdentity, state,
    publicEntityAliases: aliases });
  const state = { ...common, decision: "reward_set", reward_set_ref: "set-real", is_terminal: false,
    rewards: [{ native_ref: "reward-real", kind: "card_choice", name: "Add card", card_options: [card] },
      { native_ref: "reward-unpopulated", kind: "card_choice", name: "Unpopulated" }] };
  const outer = project(state), group = outer.snapshot.interaction.content.surface.rewards[0];
  const reads = [...outer.reads.values()].filter(read => read.content.display_context === "reward_candidate");
  assert.equal(reads.length, 1); // Unpopulated groups create no made-up details.
  const read = reads[0];
  assert.equal(read.completeness.status, "complete");
  assert.equal(read.descriptor.target_referent_id, group.card_options[0].entity_id);
  assert.equal(read.content.display_owner_entity_id, group.group_id);
  assert.equal(read.content.parent_read_id, null);
  assert.deepEqual(read.content.keywords, card.keywords);
  for (const key of ["index", "can_play", "keywords", "card_previews"])
    assert.equal(Object.hasOwn(group.card_options[0], key), false, key);
  assert.equal(group.card_options[0].target_type, "AnyEnemy");
  const inner = project({ ...common, decision: "card_reward", parent_status: "bound",
    parent_reward_ref: "reward-real", reward_set_ref: "set-real", selection_completes_reward: true,
    cards: [{ ...card, index: 0, native_ref: "creation-real", card_entity_ref: "card-real" }], alternatives: [] });
  assert.deepEqual([...inner.reads.values()].find(read => read.content.display_context === "reward_candidate").content,
    read.content);
  const incomplete = structuredClone(state); incomplete.rewards[0].card_options[0].render_complete = false;
  incomplete.rewards[0].card_options[0].render_missing = ["native_tooltip_unavailable"];
  const changed = [...project(incomplete).reads.values()].find(read => read.content.display_context === "reward_candidate");
  assert.equal(changed.completeness.status, "partial");
  assert.deepEqual(changed.completeness.missing, ["native_tooltip_unavailable"]);
});


test("reward group identity is the visible reward entity for every reward kind", () => {
  const state = { ...eventState(), decision: "reward_set", reward_set_ref: "set-native", is_terminal: false,
    rewards: ["gold", "potion", "relic", "card_choice"].map((kind, index) => ({
      native_ref: `reward-${index}`, kind, name: kind, group_relation: "independent" })) };
  const projection = projectManagedCandidateDecision({ ...projectionIdentity, state });
  const rewards = projection.snapshot.interaction.content.surface.rewards;
  for (const reward of rewards) {
    assert.equal(reward.entity_id, reward.group_id);
    assert.equal(projection.snapshot.referents.find(item => item.referent_id === reward.entity_id).role, "reward");
  }
  for (const reward of rewards.filter(reward => reward.kind !== "card")) {
    assert.equal(reward.card_alternatives_complete, false);
    assert.equal(reward.native_relic_refresh_subscription, "unavailable");
  }
  assert.equal(new Set(rewards.map(reward => reward.group_id)).size, rewards.length);
});


test("projects native resolved event options, flags and card tooltips without template substitution", () => {
  const state = eventState();
  Object.assign(state, { event_id: "BYRDONIS_NEST", event_name: "多尼斯异鸟巢", ancient: false,
    description: "第一段。\n\n第二段。" });
  state.options[0].title = "吃掉这颗蛋";
  state.options[0].description = "获得7点最大生命值。";
  state.options[1] = { ...state.options[0], index: 1, native_ref: "native-take-egg", title: "带走这颗蛋",
    description: "将一张多尼斯异鸟蛋加入你的牌组。", tooltips: [
      { kind: "card", card: { id: "BYRDONIS_EGG", name: "多尼斯异鸟蛋", type: "Quest", rarity: "Quest",
        cost: "-1", description: "不能被打出。 能在休息处被孵化。", is_upgraded: false, keywords: [], card_previews: [] } },
      { kind: "text", name: "不能被打出", description: "不能被打出的牌无法被打出。" }
    ] };
  const projected = projectManagedCandidateDecision({ state, ...projectionIdentity });
  const interaction = projected.snapshot.interaction;
  assert.equal(interaction.stage, "ready"); assert.equal(interaction.prompt, null);
  assert.equal(interaction.content.context.event_id, "BYRDONIS_NEST");
  assert.equal(interaction.content.context.body, "第一段。  第二段。");
  const options = interaction.content.surface.options;
  assert.equal(options[0].description, "获得7点最大生命值。");
  assert.equal(options[0].is_enabled, true); assert.equal(options[0].will_kill_player, false);
  assert.equal(options[1].tooltips[0].card.definition_id, "BYRDONIS_EGG");
  assert.equal(options[1].tooltips[0].card.target_type, undefined);
  assert.equal(options[1].tooltips[0].card.keywords, undefined);
  assert.equal(options[1].tooltips[1].description, "不能被打出的牌无法被打出。");
  assert.equal(Object.hasOwn(options[0], "variables"), false);
  assert.equal(Object.hasOwn(options[0], "text_key"), false);
  const binding = [...projected.bindings.values()].find(b => b.raw_request.args.option_ref === "native-take-egg");
  assert.equal(binding.raw_request.action, "choose_option");
  assert.equal(binding.raw_request.args.event_ref, state.event_ref);
  assert.equal(JSON.stringify(projected.snapshot).includes("native-take-egg"), false);
});

test("event body is published only when the native current body was actually rendered",()=>{
  const present=eventState();present.description="Current visible body with 7 maximum HP.";
  const withBody=projectManagedCandidateDecision({...projectionIdentity,state:present}).snapshot;
  assert.equal(withBody.interaction.content.context.body,"Current visible body with 7 maximum HP.");
  const absent={...present,event_id:"PAEL",ancient:true,description:null};
  const noBody=projectManagedCandidateDecision({...projectionIdentity,state:absent}).snapshot;
  assert.equal(noBody.bound_actions.status,"complete");
  assert.equal(Object.hasOwn(noBody.interaction.content.context,"body"),false);
  assert.equal(noBody.interaction.content.context.ancient,true);
});


test("reward candidate previews retain native null star cost without changing the outer card", () => {
  const slimed = { id: "CARD.SLIMED", name: "Slimed", type: "Status", cost: 1,
    star_cost: null, description: "Exhaust.", rarity: "Status", keywords: [], card_previews: [] };
  const card = { native_ref: "gunk-up", id: "CARD.GUNK_UP", name: "Gunk Up", type: "Skill", cost: 1,
    star_cost: null, description: "Add a Slimed.", rarity: "Common", render_complete: true,
    keywords: [], card_previews: [slimed, { ...slimed, id: "CARD.STAR_PREVIEW", star_cost: 2 }] };
  card.public_details = { keywords: [], card_previews: card.card_previews.map(({ id, ...preview }) => ({
    ...preview, definition_id: id.slice(5), entity_id: null, cost: String(preview.cost),
    star_cost: preview.star_cost == null ? null : String(preview.star_cost),
    is_upgraded: false, is_selected: false, target_type: null, can_play: null,
  })) };
  const state = { ...eventState(), decision: "reward_set", reward_set_ref: "native-set",
    rewards: [{ native_ref: "native-reward", kind: "card_choice", name: "Add card", card_options: [card] }] };
  const projection = projectManagedCandidateDecision({ state, ...projectionIdentity });
  const detail = [...projection.reads.values()].find(read => read.descriptor.kind === "surface_card"
    && read.content.display_context === "reward_candidate");
  assert.equal(detail.completeness.status, "complete");
  assert.equal(Object.hasOwn(detail.content, "star_cost"), false);
  assert.equal(Object.hasOwn(detail.content.card_previews[0], "star_cost"), true);
  assert.equal(detail.content.card_previews[0].star_cost, null);
  assert.equal(detail.content.card_previews[1].star_cost, "2");
});

test("deck public detail revision 2 preserves the native upgrade without replacing the original card", () => {
  const state = eventState();
  const preview = { status: "available", card: { entity_id: null, definition_id: "STRIKE_IRONCLAD",
    name: "Strike+", type: "Attack", cost: "1", description: "Deal 9 damage.", rarity: "Basic",
    is_upgraded: true, target_type: "AnyEnemy", keywords: [], card_previews: [] } };
  state.player.deck[0].public_details.upgrade_preview = preview;
  const projection = projectManagedCandidateDecision({ state, ...projectionIdentity });
  const detail = [...projection.reads.values()].find(r => r.descriptor.kind === "surface_card");
  assert.equal(detail.content.public_details_revision, "2");
  assert.deepEqual(detail.content.upgrade_preview, preview);
  assert.equal(detail.content.description, "Deal 6 damage.");
  assert.equal(detail.completeness.status, "complete");
  assert.equal(state.player.deck[0].upgraded, false);
  for (const bad of [null, undefined, {}, { status: "unavailable", card: null }]) {
    state.player.deck[0].public_details.upgrade_preview = bad;
    const invalid = projectManagedCandidateDecision({ state, ...projectionIdentity });
    const failed = [...invalid.reads.values()].find(r => r.descriptor.kind === "surface_card");
    assert.equal(failed.content.public_details_revision, "2");
    assert.equal(failed.completeness.status, "partial");
    assert.ok(failed.completeness.missing.includes("current_native_public_card_upgrade_preview_not_captured"));
  }
});


test("noncombat belt discard preserves event options, exact native slot and post-discard absence", () => {
  const state = eventState();
  state.player.potions = [{ native_ref: "potion-original", slot: 1, id: "ENERGY_POTION", name: "Energy Potion",
    description: "Gain 2 Energy", can_discard: true, can_use: false, usage: "CombatOnly", keywords: [], card_previews: [], hover_facts_complete: true }];
  const before = projectManagedCandidateDecision({ state, ...projectionIdentity });
  assert.equal(before.snapshot.bound_actions.actions.length, 2);
  assert.equal(before.snapshot.interaction.content.surface.options.length, 2);
  const discard = before.snapshot.bound_actions.actions.find(action => action.label === "Discard Energy Potion");
  assert.ok(discard);
  assert.deepEqual(before.bindings.get(discard.bound_action_id).raw_request, {
    cmd: "action", action: "discard_potion", args: { potion_slot: 1, potion_ref: "potion-original" } });
  state.player.potions = [];
  const after = projectManagedCandidateDecision({ state, ...projectionIdentity, sequence: 2 });
  assert.equal(after.snapshot.bound_actions.actions.length, 1);
  assert.deepEqual(after.snapshot.interaction.content.surface.non_combat_discardable_potions, []);
});
