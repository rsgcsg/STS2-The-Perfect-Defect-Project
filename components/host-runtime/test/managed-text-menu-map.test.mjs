import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import {
  ManagedPlayerEnvironmentSession,
  projectManagedCandidateDecision
} from "../src/managed-player-environment.mjs";
import {
  MANAGED_TEXT_MENU_PROFILE,
  ManagedTextMenuSessionAdapter,
  ManagedTextMenuMapSessionAdapter
} from "../src/managed-text-menu-map.mjs";

const identity = {
  runtimeInstanceId: "managed-text-menu-test",
  environmentFingerprint: "managed-text-menu-environment",
  sequence: 1
};

function mapDecision() {
  return {
    type: "decision",
    decision: "map_select",
    context: {
      act: 1, act_index: 0, act_definition_id: "OVERGROWTH", act_name: "Overgrowth",
      floor: 0, total_floor: 0, ascension: 0,
      bosses: [{ id: "VANTOM_BOSS", name: "Vantom", order: 0 }], modifiers: []
    },
    choices: [{ col: 3, row: 0, type: "Monster", native_ref: "native-map-point", children: [{ col: 2, row: 1, type: "Monster" }] }],
    visible_map: {
      type: "map",
      rows: [[{ col: 2, row: 1, type: "Monster", children: [{ col: 3, row: 16 }], visited: false, current: false }]],
      boss: { col: 3, row: 16, type: "Boss" },
      current_coord: null
    },
    player: {
      name: "The Ironclad", character_id: "IRONCLAD", hp: 80, max_hp: 80, gold: 99,
      native_ref: "player-native", max_potion_slots: 3, relics: [], potions: [], deck: []
    }
  };
}

function restDecision() {
  return {
    type: "decision", decision: "rest_site",
    context: { ...mapDecision().context, floor: 5, total_floor: 5, room_type: "RestSite" },
    options: [
      { index: 0, native_ref: "native-rest-a", option_id: "HEAL", name: "Rest", description: "Heal 24 HP.", is_enabled: true },
      { index: 1, native_ref: "native-rest-b", option_id: "SMITH", name: "Smith", description: "Upgrade a card.", is_enabled: false },
      { index: 2, native_ref: "native-rest-c", option_id: "DIG", name: "Dig", description: "Find a relic.", is_enabled: true }
    ],
    player: mapDecision().player
  };
}

function deckUpgradeDecision(stage = "selecting", cardRefs = ["native-card-a", "native-card-b"]) {
  const preview = stage === "preview";
  return {
    type: "decision", decision: "deck_upgrade_select",
    context: restDecision().context, player: mapDecision().player,
    selector_ref: "native-selector-a", stage, prompt: "Choose a card to upgrade.",
    min_select: 1, max_select: 1, cancelable: true, require_manual_confirmation: true,
    selected_refs: preview ? [cardRefs[0]] : [],
    can_cancel_selection: !preview, can_cancel_preview: preview, can_confirm: preview,
    cards: cardRefs.map((ref, index) => ({
      index, native_ref: ref, id: "CARD.STRIKE", name: "Strike",
      cost: 1, type: "Attack", rarity: "Basic", upgraded: false,
      is_selected: preview && index === 0, is_selectable: !preview,
      is_deselectable: false
    }))
  };
}

function combatDecision() {
  return {
    type: "decision", decision: "combat_play", context: { ...mapDecision().context, floor: 2, total_floor: 2, room_type: "Combat" },
    encounter_type: "normal", turn_owner: "player", is_play_phase: true, round: 1,
    energy: 3, max_energy: 3, exhaust_pile_count: 0, orb_slots: 0, orbs: [], companions: [], player_statuses: [],
    hand: [{ index: 0, native_ref: "native-card-strike", valid_target_refs: ["native-enemy-a"],
      id: "CARD.STRIKE_IRONCLAD", name: "Strike", can_play: true, target_type: "AnyEnemy",
      type: "Attack", rarity: "Basic", cost: 1 }],
    enemies: [{ index: 0, native_ref: "native-enemy-a", id: "MONSTER.CULTIST", combat_id: 1,
      name: "Cultist", hp: 10, max_hp: 10, block: 0, statuses: [], intents: [] }],
    player: { ...mapDecision().player, native_ref: "native-player", potions: [{
      slot: 0, native_ref: "native-potion-fire", id: "POTION.FIRE", name: "Fire Potion",
      target_type: "AnyEnemy", can_use: true, can_discard: true, binding_supported: true,
      valid_target_refs: ["native-enemy-a"], hover_facts_complete: true, keywords: [], card_previews: []
    }] }
  };
}

function rewardDecision(kind = "reward_set") {
  const common = { type: "decision", context: { ...mapDecision().context, floor: 2,
    total_floor: 2, room_type: "Monster" }, player: mapDecision().player };
  if (kind === "reward_set") return { ...common, decision: kind,
    rewards: [{ index: 0, native_ref: "reward-card", kind: "card_choice", name: "Add a card" }],
    potion_slots_full: false, can_skip: true, is_terminal: true, can_proceed: true,
    room_ref: "combat-room", is_boss: false };
  if (kind === "card_reward") return { ...common, decision: kind,
    cards: [{ index: 0, native_ref: "creation-result-a", id: "CARD.STRIKE", name: "Strike" }],
    alternatives: [
      { index: 0, native_ref: "alternative-skip", id: "Skip", name: "Skip" },
      { index: 1, native_ref: "alternative-reroll", id: "REROLL", name: "Reroll" },
      { index: 2, native_ref: "alternative-sacrifice", id: "SACRIFICE", name: "Sacrifice" }
    ] };
  return { ...common, decision: "combat_rewards_complete", room_ref: "combat-room", is_boss: false };
}

test("reward text menu preserves every current callback choice and native binding", async () => {
  const projected = projectManagedCandidateDecision({ state: rewardDecision("card_reward"), ...identity });
  assert.equal(projected.snapshot.bound_actions.status, "complete");
  const adapter = new ManagedTextMenuSessionAdapter({ observe: () => projected.snapshot,
    async submit() { throw new Error("observation does not dispatch"); } });
  const menu = adapter.observe();
  assert.equal(menu.menu_actions.status, "complete");
  assert.deepEqual(menu.menu_actions.actions.map((action) => action.label),
    ["Take Strike", "Skip", "Reroll", "Sacrifice"]);
  assert.deepEqual([...projected.bindings.values()].map((binding) => binding.raw_request), [
    { cmd: "action", action: "select_card_reward", args: { card_ref: "creation-result-a" } },
    { cmd: "action", action: "select_card_reward_alternative", args: { alternative_ref: "alternative-skip" } },
    { cmd: "action", action: "select_card_reward_alternative", args: { alternative_ref: "alternative-reroll" } },
    { cmd: "action", action: "select_card_reward_alternative", args: { alternative_ref: "alternative-sacrifice" } }
  ]);
  assert.equal(JSON.stringify(menu).includes("alternative-reroll"), false);

  for (const bad of [
    { ...rewardDecision("card_reward"), alternatives: undefined },
    { ...rewardDecision("card_reward"), alternatives: [] , cards: [] },
    { ...rewardDecision("card_reward"), alternatives: [{ index: 0, id: "SACRIFICE", name: "Sacrifice" }] },
    { ...rewardDecision("card_reward"), alternatives: [{ index: 0, native_ref: "creation-result-a", id: "SACRIFICE", name: "Sacrifice" }] }
  ]) {
    const unavailable = projectManagedCandidateDecision({ state: bad, ...identity }).snapshot;
    assert.equal(unavailable.bound_actions.status, "unavailable");
    assert.equal(new ManagedTextMenuSessionAdapter({ observe: () => unavailable,
      async submit() { throw new Error("incomplete callback dispatched"); } }).observe().menu_actions.status,
    "unavailable");
  }
  const noSkip = rewardDecision("card_reward");
  noSkip.alternatives = noSkip.alternatives.slice(1).map((option, index) => ({ ...option, index }));
  const noSkipSnapshot = projectManagedCandidateDecision({ state: noSkip, ...identity }).snapshot;
  assert.equal(noSkipSnapshot.interaction.content.surface.can_skip, false);
  assert.equal(noSkipSnapshot.bound_actions.actions.some((action) => action.verb === "skip"), false);
  for (const kind of ["reward_set", "combat_rewards_complete"]) {
    const snapshot = projectManagedCandidateDecision({ state: rewardDecision(kind), ...identity }).snapshot;
    assert.equal(new ManagedTextMenuSessionAdapter({ observe: () => snapshot,
      async submit() { throw new Error("observation does not dispatch"); } }).observe().menu_actions.status,
    "complete", kind);
  }
});

test("reward text leaves submit through MPE and rebind each native successor", async () => {
  let current = rewardDecision("reward_set");
  const raw = [];
  const process = { async request(request) {
    if (request.cmd === "start_run") return current;
    if (request.cmd === "run_identity") return { type: "run_identity", seed: "REWARDCHAIN" };
    raw.push(request);
    if (request.action === "select_reward") current = rewardDecision("card_reward");
    else if (request.action === "select_card_reward") current = rewardDecision("combat_rewards_complete");
    else if (request.action === "proceed") current = mapDecision();
    else throw new Error(`unexpected native action ${request.action}`);
    return current;
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "REWARDCHAIN" });
  const adapter = new ManagedTextMenuSessionAdapter(session);
  let page = adapter.observe();
  for (const [kind, label, successor] of [
    ["reward_claim", /Claim Add a card/u, "card_reward_selection"],
    ["card_reward_selection", /Take Strike/u, "reward_completion"],
    ["reward_completion", /Proceed to map/u, "map_navigation"]
  ]) {
    assert.equal(page.interaction.kind, kind);
    const action = page.menu_actions.actions.find((entry) => label.test(entry.label));
    assert.ok(action);
    const receipt = await adapter.submit({ request_id: `reward-${kind}`,
      expected_snapshot_id: page.snapshot_id, action_id: action.action_id,
      input_profile: MANAGED_TEXT_MENU_PROFILE });
    assert.equal(receipt.status, "applied");
    assert.equal(receipt.successor.interaction.kind, successor);
    page = receipt.successor;
  }
  assert.deepEqual(raw.filter((request) => request.cmd === "action").map((request) => request.action),
    ["select_reward", "select_card_reward", "proceed"]);
  assert.deepEqual(raw.filter((request) => request.cmd === "action")[1].args,
    { card_ref: "creation-result-a" });
});

test("deck upgrade exposes staged exact card, preview, confirm, and cancel inputs", async () => {
  let current = deckUpgradeDecision();
  const raw = [];
  const process = { async request(request) {
    if (request.cmd === "start_run") return current;
    raw.push(request);
    if (request.action === "select_upgrade_card") current = deckUpgradeDecision("preview");
    else if (request.action === "cancel_upgrade_preview") current = deckUpgradeDecision();
    else if (request.action === "confirm_upgrade_selection") current = restDecision();
    return current;
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "TUPGRADE" });
  const adapter = new ManagedTextMenuSessionAdapter(session);
  const initial = adapter.observe();
  assert.equal(initial.interaction.kind, "deck_upgrade_selection");
  assert.deepEqual(initial.menu_actions.actions.map((action) => action.verb),
    ["select", "select", "cancel"]);
  assert.notEqual(initial.menu_actions.actions[0].subject_referent_id,
    initial.menu_actions.actions[1].subject_referent_id);
  assert.equal(JSON.stringify(initial).includes("native-card-a"), false);
  assert.equal(JSON.stringify(initial).includes("native-selector-a"), false);
  const select = await adapter.submit({ request_id: "upgrade-select",
    expected_snapshot_id: initial.snapshot_id,
    action_id: initial.menu_actions.actions[0].action_id,
    input_profile: MANAGED_TEXT_MENU_PROFILE });
  const preview = select.successor;
  assert.equal(preview.interaction.stage, "preview");
  assert.deepEqual(preview.menu_actions.actions.map((action) => action.verb), ["cancel", "confirm"]);
  assert.deepEqual(raw[0], { cmd: "action", action: "select_upgrade_card",
    args: { selector_ref: "native-selector-a", card_ref: "native-card-a" } });
  const back = await adapter.submit({ request_id: "upgrade-back",
    expected_snapshot_id: preview.snapshot_id, action_id: preview.menu_actions.actions[0].action_id,
    input_profile: MANAGED_TEXT_MENU_PROFILE });
  assert.equal(back.successor.interaction.stage, "selecting");
  assert.equal(raw[1].action, "cancel_upgrade_preview");
  const again = await adapter.submit({ request_id: "upgrade-select-again",
    expected_snapshot_id: back.successor.snapshot_id,
    action_id: back.successor.menu_actions.actions[0].action_id,
    input_profile: MANAGED_TEXT_MENU_PROFILE });
  const confirmed = await adapter.submit({ request_id: "upgrade-confirm",
    expected_snapshot_id: again.successor.snapshot_id,
    action_id: again.successor.menu_actions.actions[1].action_id,
    input_profile: MANAGED_TEXT_MENU_PROFILE });
  assert.equal(confirmed.successor.interaction.kind, "rest_site");
  assert.deepEqual(raw[3], { cmd: "action", action: "confirm_upgrade_selection",
    args: { selector_ref: "native-selector-a", selected_refs: "native-card-a" } });
  assert.deepEqual(await adapter.submit({ request_id: "upgrade-confirm",
    expected_snapshot_id: again.successor.snapshot_id,
    action_id: again.successor.menu_actions.actions[1].action_id,
    input_profile: MANAGED_TEXT_MENU_PROFILE }), confirmed);
  assert.equal(raw.length, 4);
});

test("deck upgrade with missing exact facts or changed option identity has no stale dispatch", async () => {
  const missing = deckUpgradeDecision();
  delete missing.selector_ref;
  const incomplete = projectManagedCandidateDecision({ state: missing, ...identity }).snapshot;
  assert.equal(incomplete.status, "visible_unsupported");
  assert.equal(incomplete.bound_actions.actions.length, 0);
  const notCancelable = deckUpgradeDecision();
  notCancelable.cancelable = false;
  notCancelable.can_cancel_selection = false;
  const noCancel = projectManagedCandidateDecision({ state: notCancelable, ...identity }).snapshot;
  assert.deepEqual(noCancel.bound_actions.actions.map((action) => action.verb), ["select", "select"]);
  for (const [mode, refs] of [
    ["reordered", ["native-card-b", "native-card-a"]],
    ["replaced", ["native-card-a", "native-card-replacement"]]
  ]) {
    let next = null;
    let dispatched = 0;
    const process = { async request(request) {
      if (request.cmd === "start_run") return deckUpgradeDecision();
      if (request.cmd === "reset_run") return next;
      dispatched += 1;
      return next;
    } };
    const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
    await session.mount({ seed: "TUPGRADE" });
    const adapter = new ManagedTextMenuSessionAdapter(session);
    const old = adapter.observe();
    next = deckUpgradeDecision("selecting", refs);
    await session.mount({ seed: "TUPGRADE", reset: true });
    const stale = await adapter.submit({ request_id: `upgrade-stale-${mode}`,
      expected_snapshot_id: old.snapshot_id, action_id: old.menu_actions.actions[0].action_id,
      input_profile: MANAGED_TEXT_MENU_PROFILE });
    assert.equal(stale.reason_code, "stale_snapshot");
    assert.equal(dispatched, 0);
  }
});

test("projects complete rest options in native order and submits only bound enabled leaves", async () => {
  let rawAction = null;
  const process = { async request(request) {
    if (request.cmd === "start_run") return restDecision();
    rawAction = request;
    return { ...restDecision(), decision: "deck_upgrade_selection", options: [] };
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "TREST" });
  const adapter = new ManagedTextMenuSessionAdapter(session);
  assert.equal(ManagedTextMenuMapSessionAdapter, ManagedTextMenuSessionAdapter);
  const snapshot = adapter.observe();
  assert.equal(snapshot.menu_actions.status, "complete");
  assert.deepEqual(snapshot.interaction.content.surface.options.map((entry) =>
    [entry.name, entry.is_enabled]), [["Rest", true], ["Smith", false], ["Dig", true]]);
  assert.deepEqual(snapshot.menu_actions.actions.map((entry) => entry.label), ["Rest", "Dig"]);
  assert.equal(JSON.stringify(snapshot).includes("native-rest-a"), false);
  const input = { request_id: "rest-choose", expected_snapshot_id: snapshot.snapshot_id,
    action_id: snapshot.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE };
  const result = await adapter.submit(input);
  assert.equal(result.status, "applied");
  assert.deepEqual(rawAction, { cmd: "action", action: "choose_option",
    args: { option_index: 0, option_ref: "native-rest-a" } });
  assert.equal(result.successor.status, "visible_unsupported");
  assert.equal(result.successor.menu_actions.status, "unavailable");
  assert.deepEqual(await adapter.submit(input), result);
});

test("rest reordering or replacement invalidates old leaf before native dispatch", async () => {
  for (const mode of ["reordered", "replaced"]) {
    let actionCalls = 0;
    let resetState = null;
    const process = { async request(request) {
      if (request.cmd === "start_run") return restDecision();
      if (request.cmd === "reset_run") return resetState;
      actionCalls += 1;
      return restDecision();
    } };
    const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
    await session.mount({ seed: "TREST" });
    const adapter = new ManagedTextMenuSessionAdapter(session);
    const old = adapter.observe();
    const updated = restDecision();
    if (mode === "reordered") {
      updated.options = [updated.options[2], updated.options[1], updated.options[0]]
        .map((entry, index) => ({ ...entry, index }));
    } else updated.options[0] = { ...updated.options[0], native_ref: "native-rest-replacement" };
    resetState = updated;
    await session.mount({ seed: "TREST", reset: true });
    const result = await adapter.submit({ request_id: `stale-rest-${mode}`,
      expected_snapshot_id: old.snapshot_id, action_id: old.menu_actions.actions[0].action_id,
      input_profile: MANAGED_TEXT_MENU_PROFILE });
    assert.equal(result.status, "not_applied");
    assert.equal(result.reason_code, "stale_snapshot");
    assert.equal(actionCalls, 0);
  }
});

test("a successful rest option retains the page and exposes an independent bound proceed", async () => {
  const nativeRequests = [];
  const process = { async request(request) {
    if (request.cmd === "start_run") return restDecision();
    if (request.cmd === "get_map") return mapDecision().visible_map;
    nativeRequests.push(request);
    if (request.action === "choose_option") return {
      ...restDecision(),
      options: restDecision().options.slice(1).map((option, index) => ({ ...option, index })),
      can_proceed: true, room_ref: "native-rest-room", last_option_result: "succeeded"
    };
    return mapDecision();
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "TREST" });
  const adapter = new ManagedTextMenuSessionAdapter(session);
  const initial = adapter.observe();
  const choice = await adapter.submit({ request_id: "rest-first", expected_snapshot_id: initial.snapshot_id,
    action_id: initial.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE });
  assert.equal(choice.status, "applied");
  assert.equal(choice.successor.interaction.kind, "rest_site");
  assert.deepEqual(choice.successor.menu_actions.actions.map((action) => action.label), ["Dig", "Proceed"]);
  assert.deepEqual(choice.successor.interaction.content.surface.options.map((option) => option.name), ["Smith", "Dig"]);
  const proceed = choice.successor.menu_actions.actions[1];
  const result = await adapter.submit({ request_id: "rest-proceed", expected_snapshot_id: choice.successor.snapshot_id,
    action_id: proceed.action_id, input_profile: MANAGED_TEXT_MENU_PROFILE });
  assert.equal(result.status, "applied");
  assert.equal(result.successor.interaction.kind, "map_navigation");
  assert.deepEqual(nativeRequests, [
    { cmd: "action", action: "choose_option", args: { option_index: 0, option_ref: "native-rest-a" } },
    { cmd: "action", action: "proceed", args: { room_ref: "native-rest-room" } }
  ]);
});

test("an exhausted rest site advertises only proceed when native success enabled it", async () => {
  let state = { ...restDecision(), options: [], can_proceed: false, room_ref: "native-rest-room" };
  const session = new ManagedPlayerEnvironmentSession({
    process: { async request(request) {
      if (request.cmd === "start_run" || request.cmd === "reset_run") return state;
      throw new Error("No native input expected");
    } }, ...identity
  });
  await session.mount({ seed: "TREST" });
  const adapter = new ManagedTextMenuSessionAdapter(session);
  assert.equal(adapter.observe().status, "visible_unsupported");
  state = { ...state, can_proceed: true };
  await session.mount({ seed: "TREST", reset: true });
  const ready = adapter.observe();
  assert.equal(ready.menu_actions.status, "complete");
  assert.deepEqual(ready.menu_actions.actions.map((action) => action.label), ["Proceed"]);
});

test("replaced rest room invalidates the previously advertised proceed without dispatch", async () => {
  let state = { ...restDecision(), options: [], can_proceed: true, room_ref: "native-rest-room-a" };
  let actionCalls = 0;
  const session = new ManagedPlayerEnvironmentSession({
    process: { async request(request) {
      if (request.cmd === "start_run" || request.cmd === "reset_run") return state;
      actionCalls += 1;
      return mapDecision();
    } }, ...identity
  });
  await session.mount({ seed: "TREST" });
  const adapter = new ManagedTextMenuSessionAdapter(session);
  const old = adapter.observe();
  state = { ...state, room_ref: "native-rest-room-b" };
  await session.mount({ seed: "TREST", reset: true });
  const rejected = await adapter.submit({ request_id: "stale-rest-proceed",
    expected_snapshot_id: old.snapshot_id, action_id: old.menu_actions.actions[0].action_id,
    input_profile: MANAGED_TEXT_MENU_PROFILE });
  assert.equal(rejected.status, "not_applied");
  assert.equal(actionCalls, 0);
});

test("cancelled rest choice stays on rest without proceed, while native error taints delivery", async () => {
  for (const outcome of ["cancelled", "error"]) {
    let actionCalls = 0;
    const process = { async request(request) {
      if (request.cmd === "start_run") return restDecision();
      actionCalls += 1;
      return outcome === "cancelled"
        ? { ...restDecision(), can_proceed: false, last_option_result: "cancelled" }
        : { type: "error", message: "Rest site option delivery is unknown: native operation faulted" };
    } };
    const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
    await session.mount({ seed: "TREST" });
    const adapter = new ManagedTextMenuSessionAdapter(session);
    const initial = adapter.observe();
    const input = { request_id: `rest-${outcome}`, expected_snapshot_id: initial.snapshot_id,
      action_id: initial.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE };
    const result = await adapter.submit(input);
    if (outcome === "cancelled") {
      assert.equal(result.status, "applied");
      assert.equal(result.successor.interaction.kind, "rest_site");
      assert.deepEqual(result.successor.menu_actions.actions.map((action) => action.label), ["Rest", "Dig"]);
    } else {
      assert.equal(result.status, "unknown");
      assert.equal(result.retry, "never");
      assert.equal(adapter.observe().menu_actions.status, "unavailable");
    }
    assert.deepEqual(await adapter.submit(input), result);
    assert.equal(actionCalls, 1);
  }
});

test("unknown rest delivery is replayed only as a receipt and taints later leaves", async () => {
  let actionCalls = 0;
  const process = { async request(request) {
    if (request.cmd === "start_run") return restDecision();
    actionCalls += 1;
    throw new Error("transport lost after native write");
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "TREST" });
  const adapter = new ManagedTextMenuSessionAdapter(session);
  const snapshot = adapter.observe();
  const input = { request_id: "unknown-rest", expected_snapshot_id: snapshot.snapshot_id,
    action_id: snapshot.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE };
  const unknown = await adapter.submit(input);
  assert.equal(unknown.status, "unknown");
  assert.equal(unknown.retry, "never");
  assert.deepEqual(await adapter.submit(input), unknown);
  assert.equal((await adapter.submit({ ...input, action_id: snapshot.menu_actions.actions[1].action_id }))
    .reason_code, "request_id_conflict");
  const later = await adapter.submit({ ...input, request_id: "after-unknown",
    action_id: snapshot.menu_actions.actions[1].action_id });
  assert.equal(later.status, "not_applied");
  assert.equal(adapter.observe().menu_actions.status, "unavailable");
  assert.equal(actionCalls, 1);
});

test("projects complete direct combat leaves with exact target referents and native bindings", async () => {
  const rawRequests = [];
  const process = { async request(request) {
    if (request.cmd === "start_run") return combatDecision();
    rawRequests.push(request);
    return combatDecision();
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "TCOMBAT" });
  const adapter = new ManagedTextMenuSessionAdapter(session);
  const source = session.observe();
  const snapshot = adapter.observe();
  assert.equal(snapshot.interaction.kind, "combat_turn");
  assert.equal(snapshot.status, "interactive");
  assert.equal(snapshot.menu_actions.status, "complete");
  assert.deepEqual(snapshot.menu_actions.actions.map((action) => action.verb), ["play", "end_turn", "use", "activate"]);
  assert.deepEqual(snapshot.menu_actions.actions[0].arguments,
    source.bound_actions.actions[0].arguments);
  assert.equal(snapshot.menu_actions.actions[0].action_id, `managed_${createHash("sha256")
    .update(`${source.snapshot_id}\0${source.bound_actions.actions[0].bound_action_id}`).digest("hex").slice(0, 32)}`);
  assert.equal(snapshot.menu_actions.actions[0].arguments[0].role, "target");
  const enemyId = snapshot.menu_actions.actions[0].arguments[0].referent_id;
  assert.equal(snapshot.referents.find((referent) => referent.referent_id === enemyId).role, "enemy");
  assert.equal(snapshot.menu.native_snapshot_id, source.snapshot_id);
  assert.equal(Object.hasOwn(snapshot, "reads"), false);
  const play = await adapter.submit({ request_id: "combat-play", expected_snapshot_id: snapshot.snapshot_id,
    action_id: snapshot.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE });
  assert.equal(play.status, "applied");
  assert.deepEqual(rawRequests[0], { cmd: "action", action: "play_card",
    args: { card_ref: "native-card-strike", target_ref: "native-enemy-a" } });
  const use = await adapter.submit({ request_id: "combat-use", expected_snapshot_id: play.successor.snapshot_id,
    action_id: play.successor.menu_actions.actions.find((action) => action.verb === "use").action_id,
    input_profile: MANAGED_TEXT_MENU_PROFILE });
  assert.equal(use.status, "applied");
  assert.deepEqual(rawRequests[1], { cmd: "action", action: "use_potion",
    args: { potion_slot: 0, potion_ref: "native-potion-fire", target_ref: "native-enemy-a" } });
});

test("text menu defers combat legality to MPE and accepts only reviewed surfaces", async () => {
  const base = projectManagedCandidateDecision({ state: combatDecision(), ...identity }).snapshot;
  const unsupportedFor = (snapshot) => new ManagedTextMenuSessionAdapter({
    observe: () => snapshot,
    async submit() { throw new Error("unsupported combat action dispatched"); }
  }).observe();
  const closed = projectManagedCandidateDecision({
    state: { ...combatDecision(), is_play_phase: false }, ...identity
  }).snapshot;
  assert.equal(unsupportedFor(closed).menu_actions.status, "unavailable");

  for (const targetType of ["AnyAlly", "FutureUnknownTarget"]) {
    const unsupported = projectManagedCandidateDecision({
      state: { ...combatDecision(), hand: [{ ...combatDecision().hand[0], target_type: targetType }] }, ...identity
    }).snapshot;
    assert.equal(unsupported.bound_actions.status, "unavailable", targetType);
    assert.equal(unsupportedFor(unsupported).menu_actions.status, "unavailable", targetType);
  }

  const futureVerb = structuredClone(base);
  futureVerb.bound_actions.actions[0].verb = "future_exact_mpe_action";
  assert.deepEqual(unsupportedFor(futureVerb).menu_actions.actions.map((action) => action.verb),
    ["future_exact_mpe_action", "end_turn", "use", "activate"]);

  for (const surface of ["event_choice", "bundle_selection"]) {
    const unreviewed = structuredClone(base);
    unreviewed.interaction.kind = surface;
    assert.equal(unsupportedFor(unreviewed).menu_actions.status, "unavailable", surface);
  }
});

test("text menu checks generic complete counts and referent structure", async () => {
  const base = projectManagedCandidateDecision({ state: combatDecision(), ...identity }).snapshot;
  const observe = (snapshot) => new ManagedTextMenuSessionAdapter({
    observe: () => snapshot,
    async submit() { throw new Error("observe-only structural check must not dispatch"); }
  }).observe();

  const badCount = structuredClone(base);
  badCount.bound_actions.total_count += 1;
  assert.equal(observe(badCount).menu_actions.status, "unavailable");

  const duplicateId = structuredClone(base);
  duplicateId.bound_actions.actions[1].bound_action_id = duplicateId.bound_actions.actions[0].bound_action_id;
  assert.equal(observe(duplicateId).menu_actions.status, "unavailable");

  const missingSubject = structuredClone(base);
  missingSubject.bound_actions.actions[0].subject_referent_id = "missing-referent";
  assert.equal(observe(missingSubject).menu_actions.status, "unavailable");

  const missingArgument = structuredClone(base);
  missingArgument.bound_actions.actions[0].arguments[0].referent_id = "missing-target";
  assert.equal(observe(missingArgument).menu_actions.status, "unavailable");

  const incomplete = structuredClone(base);
  incomplete.completeness.status = "partial";
  assert.equal(observe(incomplete).menu_actions.status, "unavailable");

  const presentationChanged = structuredClone(base);
  presentationChanged.interaction.stage = "new-owner-defined-stage";
  presentationChanged.completeness.visible_information = "owner-defined complete evidence";
  presentationChanged.completeness.interaction_discovery = "owner-defined discovery evidence";
  assert.equal(observe(presentationChanged).menu_actions.status, "complete");
});

test("stale combat text-menu leaf dispatches nothing and unknown delivery is never retried", async () => {
  for (const outcome of ["stale", "unknown"]) {
    let current = combatDecision();
    let actionCalls = 0;
    const process = { async request(request) {
      if (request.cmd === "start_run") return current;
      if (request.cmd === "reset_run") return current;
      actionCalls += 1;
      if (outcome === "unknown") throw new Error("transport lost after native write");
      return current;
    } };
    const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
    await session.mount({ seed: "TCOMBAT" });
    const adapter = new ManagedTextMenuSessionAdapter(session);
    const old = adapter.observe();
    if (outcome === "stale") {
      current = { ...combatDecision(), round: 2 };
      await session.mount({ seed: "TCOMBAT", reset: true });
      const rejected = await adapter.submit({ request_id: "stale-combat", expected_snapshot_id: old.snapshot_id,
        action_id: old.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE });
      assert.equal(rejected.reason_code, "stale_snapshot");
      assert.equal(actionCalls, 0);
    } else {
      const input = { request_id: "unknown-combat", expected_snapshot_id: old.snapshot_id,
        action_id: old.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE };
      const receipt = await adapter.submit(input);
      assert.equal(receipt.status, "unknown");
      assert.equal(receipt.retry, "never");
      assert.deepEqual(await adapter.submit(input), receipt);
      assert.equal(actionCalls, 1);
    }
  }
});

test("projects only the complete current map catalog and submits its exact hidden binding", async () => {
  let rawAction = null;
  const process = {
    async request(request) {
      if (request.cmd === "start_run") return mapDecision();
      if (request.cmd === "reset_run") return mapDecision();
      if (request.cmd === "get_map") return null;
      rawAction = request;
      return { ...mapDecision(), context: { ...mapDecision().context, floor: 1 } };
    }
  };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  const source = await session.mount({ seed: "TMAP" });
  const adapter = new ManagedTextMenuMapSessionAdapter(session);
  const snapshot = adapter.observe();
  assert.equal(snapshot.input_profile, MANAGED_TEXT_MENU_PROFILE);
  assert.equal(snapshot.menu.cursor, "root");
  assert.equal(snapshot.menu.native_snapshot_id, source.snapshot_id);
  assert.equal(snapshot.menu_actions.status, "complete");
  assert.equal(snapshot.menu_actions.actions.length, 1);
  assert.equal(snapshot.menu_actions.actions[0].kind, "native_input");
  assert.equal(snapshot.menu_actions.actions[0].effect_domain, "native_input");
  assert.equal(JSON.stringify(snapshot).includes("native-map-point"), false);
  assert.equal(Object.hasOwn(snapshot, "bound_actions"), false);
  assert.equal(Object.hasOwn(snapshot, "reads"), false);

  const wrongProfile = await adapter.submit({
    request_id: "wrong-profile", expected_snapshot_id: snapshot.snapshot_id,
    action_id: snapshot.menu_actions.actions[0].action_id, input_profile: "player-environment-v1"
  });
  assert.equal(wrongProfile.status, "not_applied");
  assert.equal(wrongProfile.reason_code, "invalid_text_menu_request");
  assert.deepEqual(await adapter.submit({
    request_id: "wrong-profile", expected_snapshot_id: snapshot.snapshot_id,
    action_id: snapshot.menu_actions.actions[0].action_id, input_profile: "player-environment-v1"
  }), wrongProfile);
  const correctedProfile = await adapter.submit({
    request_id: "wrong-profile", expected_snapshot_id: snapshot.snapshot_id,
    action_id: snapshot.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE
  });
  assert.equal(correctedProfile.reason_code, "request_id_conflict");
  assert.equal(rawAction, null);
  const result = await adapter.submit({
    request_id: "map-choice", expected_snapshot_id: snapshot.snapshot_id,
    action_id: snapshot.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE
  });
  assert.equal(result.status, "applied");
  assert.equal(result.native_delivery, "delivered");
  assert.equal(result.action.action_id, snapshot.menu_actions.actions[0].action_id);
  assert.deepEqual(rawAction, {
    cmd: "action", action: "select_map_node",
    args: { col: 3, row: 0, map_point_ref: "native-map-point" }
  });
  const nextAction = result.successor.menu_actions.actions[0];
  const nextResult = await adapter.submit({
    request_id: "map-choice-next", expected_snapshot_id: result.successor.snapshot_id,
    action_id: nextAction.action_id, input_profile: MANAGED_TEXT_MENU_PROFILE
  });
  assert.equal(nextResult.status, "applied");
});

test("does not advertise non-map actions and stale map authority dispatches nothing", async () => {
  let calls = 0;
  const process = {
    async request(request) {
      if (request.cmd === "start_run") return mapDecision();
      if (request.cmd === "reset_run") return mapDecision();
      if (request.cmd === "get_map") return null;
      calls += 1;
      return mapDecision();
    }
  };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "TMAP" });
  const adapter = new ManagedTextMenuMapSessionAdapter(session);
  const old = adapter.observe();
  await session.mount({ seed: "TMAP", reset: true });
  const newer = adapter.observe();
  const rejected = await adapter.submit({
    request_id: "stale-map", expected_snapshot_id: old.snapshot_id,
    action_id: old.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE
  });
  assert.equal(rejected.status, "not_applied");
  assert.equal(rejected.native_delivery, null);
  assert.equal(rejected.effect_domain, null);
  assert.equal(rejected.successor.snapshot_id, newer.snapshot_id);
  assert.equal(calls, 0);

  const unsupportedSession = {
    observe: () => ({ ...projectManagedCandidateDecision({
      state: { ...mapDecision(), decision: "combat_turn" }, ...identity
    }).snapshot, interaction: { kind: "combat_turn" } }),
    async submit() { calls += 1; throw new Error("unsupported action dispatched"); }
  };
  const unsupported = new ManagedTextMenuMapSessionAdapter(unsupportedSession).observe();
  assert.equal(unsupported.menu_actions.status, "unavailable");
  assert.deepEqual(unsupported.menu_actions.actions, []);
  assert.equal(unsupported.status, "visible_unsupported");
  assert.equal(calls, 0);
});

test("unknown map delivery is terminal and never retried", async () => {
  let actionCalls = 0;
  const process = {
    async request(request) {
      if (request.cmd === "start_run") return mapDecision();
      if (request.cmd === "get_map") return null;
      actionCalls += 1;
      throw new Error("transport lost after native write");
    }
  };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "TMAP" });
  const adapter = new ManagedTextMenuMapSessionAdapter(session);
  const snapshot = adapter.observe();
  const input = {
    request_id: "unknown-map", expected_snapshot_id: snapshot.snapshot_id,
    action_id: snapshot.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE
  };
  const result = await adapter.submit(input);
  assert.equal(result.status, "unknown");
  assert.equal(result.native_delivery, "unknown");
  assert.equal(result.retry, "never");
  assert.equal(adapter.observe().menu_actions.status, "unavailable");
  assert.deepEqual(await adapter.submit(input), result);
  const conflict = await adapter.submit({ ...input, action_id: "different-action" });
  assert.equal(conflict.reason_code, "request_id_conflict");
  const refused = await adapter.submit({ ...input, request_id: "after-unknown" });
  assert.equal(refused.status, "not_applied");
  assert.equal(actionCalls, 1);
});

test("serializes concurrent duplicate request IDs to one native delivery", async () => {
  let actionCalls = 0;
  let release;
  const nativeResult = new Promise((resolve) => { release = resolve; });
  const process = {
    async request(request) {
      if (request.cmd === "start_run") return mapDecision();
      if (request.cmd === "get_map") return null;
      actionCalls += 1;
      return nativeResult;
    }
  };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "TMAP" });
  const adapter = new ManagedTextMenuMapSessionAdapter(session);
  const snapshot = adapter.observe();
  const input = {
    request_id: "concurrent-same-request", expected_snapshot_id: snapshot.snapshot_id,
    action_id: snapshot.menu_actions.actions[0].action_id, input_profile: MANAGED_TEXT_MENU_PROFILE
  };
  const first = adapter.submit(input);
  const duplicate = adapter.submit(input);
  await Promise.resolve();
  assert.equal(actionCalls, 1);
  release(mapDecision());
  const [result, replay] = await Promise.all([first, duplicate]);
  assert.deepEqual(replay, result);
  assert.equal(result.status, "applied");
  assert.equal(actionCalls, 1);
});

test("serializes distinct requests and rechecks page and taint before native dispatch", async () => {
  for (const mode of ["delivered", "unknown"]) {
    let actionCalls = 0;
    let resolveNative;
    let rejectNative;
    const nativeResult = new Promise((resolve, reject) => {
      resolveNative = resolve;
      rejectNative = reject;
    });
    const process = {
      async request(request) {
        if (request.cmd === "start_run") return mapDecision();
        if (request.cmd === "get_map") return null;
        actionCalls += 1;
        return nativeResult;
      }
    };
    const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
    await session.mount({ seed: "TMAP" });
    const adapter = new ManagedTextMenuMapSessionAdapter(session);
    const snapshot = adapter.observe();
    const actionId = snapshot.menu_actions.actions[0].action_id;
    const first = adapter.submit({
      request_id: `first-${mode}`, expected_snapshot_id: snapshot.snapshot_id,
      action_id: actionId, input_profile: MANAGED_TEXT_MENU_PROFILE
    });
    const queued = adapter.submit({
      request_id: `queued-${mode}`, expected_snapshot_id: snapshot.snapshot_id,
      action_id: actionId, input_profile: MANAGED_TEXT_MENU_PROFILE
    });
    await Promise.resolve();
    assert.equal(actionCalls, 1);
    if (mode === "unknown") rejectNative(new Error("native delivery outcome lost"));
    else resolveNative(mapDecision());
    const [firstResult, queuedResult] = await Promise.all([first, queued]);
    assert.equal(firstResult.status, mode === "unknown" ? "unknown" : "applied");
    assert.equal(queuedResult.status, "not_applied");
    assert.equal(queuedResult.reason_code, mode === "unknown" ? "runtime_tainted_after_unknown" : "stale_snapshot");
    assert.equal(actionCalls, 1);
  }
});
