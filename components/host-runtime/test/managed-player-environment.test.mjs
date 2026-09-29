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
      description: "Deal 6 damage."
    }]
  };
}

function eventState() {
  return {
    type: "decision",
    decision: "event_choice",
    context: { act: 1, floor: 1, room_type: "Event" },
    event_name: "Neow",
    description: "Choose.",
    options: [
      { index: 0, title: "Open", description: "Available", is_locked: false },
      { index: 1, title: "Locked", description: "Unavailable", is_locked: true }
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
  assert.deepEqual(projection.snapshot.referents.map((referent) => referent.role).sort(), [
    "node",
    "node",
    "option"
  ]);
  assert.equal(projection.snapshot.bound_actions.actions[0].label, "Choose monster at (3,0)");
  assert.equal(JSON.stringify(projection.snapshot).includes("map-point-a"), false);
  assert.equal([...projection.bindings.values()][0].raw_request.args.map_point_ref, "map-point-a");
});

test("projects stable player-visible hover facts without leaking native operands", () => {
  const baseState = {
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
  };
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
        { index: 0, native_ref: "rest-option-a", option_id: "REST", name: "Rest", description: "Heal 24 HP.", is_enabled: true },
        { index: 1, native_ref: "rest-option-b", option_id: "SMITH", name: "Smith", description: "Upgrade a card.", is_enabled: false }
      ],
      player: player()
    }
  });
  assert.equal(projection.snapshot.interaction.kind, "rest_site");
  assert.deepEqual(projection.snapshot.interaction.content.surface.options.map((option) => ({
    name: option.name,
    description: option.description,
    is_enabled: option.is_enabled
  })), [
    { name: "Rest", description: "Heal 24 HP.", is_enabled: true },
    { name: "Smith", description: "Upgrade a card.", is_enabled: false }
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
  assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.label), ["Dig", "Proceed"]);
  assert.deepEqual([...projection.bindings.values()].map((binding) => binding.raw_request), [
    { cmd: "action", action: "choose_option", args: { option_index: 1, option_ref: "rest-option-c" } },
    { cmd: "action", action: "proceed", args: { room_ref: "rest-room-a" } }
  ]);
  assert.equal(JSON.stringify(projection.snapshot).includes("rest-room-a"), false);
  const exhausted = projectManagedCandidateDecision({
    ...projectionIdentity, state: { ...state, options: [] }
  });
  assert.deepEqual(exhausted.snapshot.bound_actions.actions.map((action) => action.label), ["Proceed"]);
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
    rewards: projection.snapshot.interaction.content.surface.rewards.map(({ entity_id: _entityId, ...reward }) => reward)
  }, {
    kind: "reward_claim",
    rewards: [
      { kind: "gold", label: "17 gold", description: null, enabled: true },
      { kind: "card", label: "Add a card", description: null, enabled: true }
    ],
    potion_slots_full: false,
    discardable_potions: [],
    can_proceed: true,
    proceed_skips_remaining_rewards: true
  });
  assert.equal(JSON.stringify(projection.snapshot).includes("reward-gold-a"), false);
  assert.equal(JSON.stringify(projection.snapshot).includes("combat-room-a"), false);
  const requests = [...projection.bindings.values()].map((binding) => binding.raw_request);
  assert.equal(requests.some((request) => request.args?.reward_ref === "reward-gold-a"), true);
  assert.equal(requests.some((request) => request.args?.room_ref === "combat-room-a"), true);
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
  assert.equal(closed.snapshot.interaction.kind, "treasure_chest");
  assert.equal(closed.snapshot.bound_actions.actions[0].label, "Open treasure chest");
  assert.equal(JSON.stringify(closed.snapshot).includes("treasure-room-a"), false);

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
        description: "Draw two additional cards.",
        rarity: "Common"
      }],
      can_skip: true,
      player: player()
    }
  });
  assert.equal(decodePlayerSnapshot(choosing.snapshot).data.status, "interactive");
  assert.equal(choosing.snapshot.interaction.kind, "treasure_relic_selection");
  assert.deepEqual(choosing.snapshot.bound_actions.actions.map((action) => action.verb).sort(), ["select", "skip"]);
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
    is_play_phase: true,
    exhaust_pile_count: 0, orb_slots: 0, orbs: [], companions: [], player_statuses: [],
    hand: [{
      index: 0, native_ref: "card-a", id: "CARD.STRIKE", name: "Strike", can_play: true,
      target_type: "AnyEnemy", valid_target_refs: ["enemy-a"]
    }],
    enemies: [{
      index: 0, native_ref: "enemy-a", id: "MONSTER.CULTIST", combat_id: 1,
      name: "Enemy A", hp: 10, max_hp: 10, block: 0, statuses: [], intents: []
    }],
    player: { ...player(), native_ref: "player-a", character_id: "IRONCLAD", max_potion_slots: 3, potions: [] }
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
      index: 0, slot: 0, native_ref: "potion-unavailable", id: "POTION.TEST", name: "Test Potion",
      can_use: false, can_discard: true, binding_supported: true, target_type: "Self",
      hover_facts_complete: true, keywords: [], card_previews: []
    }] } }, ...projectionIdentity
  });
  assert.equal(supportedButUnavailable.snapshot.completeness.status, "complete");
  assert.equal(supportedButUnavailable.snapshot.bound_actions.status, "complete");
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
    state: {
      type: "decision",
      decision: "shop",
      context: { act: 1, floor: 3, room_type: "Shop" },
      room_ref: "room-a",
      cards: [{
        native_ref: "card-entry-a",
        name: "Pommel Strike",
        card_cost: 1,
        cost: 50,
        type: "Attack",
        rarity: "Common",
        is_stocked: true,
        can_purchase: true
      }],
      relics: [{ native_ref: "relic-entry-a", name: "Bag", cost: 150, is_stocked: true, can_purchase: false }],
      potions: [{ native_ref: "potion-entry-a", name: "Fire Potion", cost: 50, is_stocked: true, can_purchase: true }],
      card_removal: { native_ref: "removal-a", cost: 75, is_stocked: true, can_purchase: true },
      player: player()
    }
  });
  assert.equal(projection.snapshot.status, "interactive");
  assert.deepEqual(projection.snapshot.bound_actions.actions.map((action) => action.label).sort(), [
    "Buy Fire Potion",
    "Buy Pommel Strike",
    "Buy card removal",
    "Leave shop"
  ]);
  assert.equal(projection.snapshot.interaction.content.surface.cards[0].cost, "1");
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
