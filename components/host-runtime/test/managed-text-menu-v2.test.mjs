import assert from "node:assert/strict";
import test from "node:test";
import { ManagedPlayerEnvironmentSession, projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { ManagedTextMenuV2SessionAdapter, MANAGED_TEXT_MENU_V2_PROFILE,
  managedTextMenuV2Contract } from "../src/managed-text-menu-v2.mjs";
import { ManagedPeDriverSession } from "../src/managed-pe-driver-session.mjs";

const identity = { runtimeInstanceId: "v2-test-runtime",
  environmentFingerprint: "v2-test-environment", sequence: 1 };

function combatDecision({ enemies = 2, hand = null } = {}) {
  const monsters = Array.from({ length: enemies }, (_, index) => ({
    index, native_ref: `native-enemy-${index}`, id: "MONSTER.CULTIST",
    combat_id: index + 1, name: "Cultist", hp: 10, max_hp: 10,
    block: 0, statuses: [], intents: [] }));
  return {
    type: "decision", decision: "combat_play",
    context: { act: 1, act_index: 0, act_definition_id: "OVERGROWTH",
      act_name: "Overgrowth", floor: 2, total_floor: 2, ascension: 0,
      room_type: "Combat", bosses: [], modifiers: [] },
    encounter_type: "normal", turn_owner: "player", is_play_phase: true, round: 1,
    energy: 3, max_energy: 3, exhaust_pile_count: 0, orb_slots: 0,
    orbs: [], companions: [], player_statuses: [],
    hand: hand ?? [
      { index: 0, native_ref: "native-strike", id: "CARD.STRIKE",
        name: "Strike", can_play: true, target_type: "AnyEnemy",
        valid_target_refs: monsters.map((enemy) => enemy.native_ref),
        type: "Attack", rarity: "Basic", cost: 1 },
      { index: 1, native_ref: "native-defend", id: "CARD.DEFEND",
        name: "Defend", can_play: true, target_type: "Self",
        valid_target_refs: null, type: "Skill", rarity: "Basic", cost: 1 }
    ],
    enemies: monsters,
    player: { name: "The Defect", character_id: "DEFECT", hp: 70, max_hp: 70,
      gold: 99, native_ref: "native-player", max_potion_slots: 3,
      relics: [], potions: [], deck: [] }
  };
}

function request(page, action, id) {
  return { request_id: id, expected_snapshot_id: page.snapshot_id,
    action_id: action.action_id, input_profile: MANAGED_TEXT_MENU_V2_PROFILE };
}

function assertContractPage(page) {
  const contract = managedTextMenuV2Contract();
  assert.equal(page.schema, contract.snapshot_schema);
  assert.equal(page.input_profile, contract.input_profile);
  assert.ok(contract.interaction_kinds.includes(page.interaction.kind));
  for (const action of page.menu_actions.actions)
    assert.ok(contract.action_verbs.includes(action.verb), action.verb);
}

function action(page, verb, index = 0) {
  const found = page.menu_actions.actions.filter((item) => item.verb === verb);
  assert.ok(found[index], `Missing ${verb} at index ${index}`);
  return found[index];
}

function controlledDriver(source, { submit = undefined } = {}) {
  let tainted = false;
  const session = {
    get tainted() { return tainted; },
    observe: () => source,
    async mount() { return source; },
    async submit(request) {
      if (submit === undefined) throw new Error("text selection must not dispatch before play");
      const result = await submit(request);
      if (result?.delivery === "unknown") tainted = true;
      return result;
    },
    async close() { return { type: "close_result" }; }
  };
  return new ManagedPeDriverSession({ session, runtime: {
    adapterRuntimeInstanceId: identity.runtimeInstanceId
  }, environmentFingerprint: identity.environmentFingerprint });
}

function sharedHandle(driver, request, principal = "client") {
  return driver.handle(request, { principal, sharedService: true });
}

async function claim(driver, textStateOwner = undefined, principal = "client") {
  const episode = driver.status();
  return sharedHandle(driver, { command: "claim_control",
    expected_runtime_instance_id: identity.runtimeInstanceId,
    expected_game_continuity_id: episode.game_continuity_id,
    ...(textStateOwner === undefined ? {} : { text_state_owner: textStateOwner }) }, principal);
}

async function observeControlled(driver, control) {
  return (await sharedHandle(driver, { command: "text_observe", require_control: true,
    input_profile: MANAGED_TEXT_MENU_V2_PROFILE,
    control_token: control.control_token, control_epoch: control.control_epoch })).context;
}

async function submitControlled(driver, control, continuity, page, selectedAction, requestId) {
  return sharedHandle(driver, { command: "text_submit", request_id: `wire-${requestId}`,
    mutation_request_id: requestId, input_profile: MANAGED_TEXT_MENU_V2_PROFILE,
    expected_game_continuity_id: continuity,
    expected_snapshot_id: page.snapshot.snapshot_id,
    action_id: selectedAction.action_id,
    control_token: control.control_token, control_epoch: control.control_epoch });
}

async function release(driver, control, requestId) {
  return sharedHandle(driver, { command: "release_control", request_id: requestId,
    control_token: control.control_token, control_epoch: control.control_epoch });
}

function driverCombatSource() {
  return projectManagedCandidateDecision({ state: combatDecision(), ...identity }).snapshot;
}

test("a different logical owner starts at root and cannot reuse the prior combat snapshot", async () => {
  const driver = controlledDriver(driverCombatSource());
  await driver.handle({ command: "reset", seed: "OWNERTEST" });
  const continuity = driver.status().game_continuity_id;

  const ownerAId = `workbench:${"a".repeat(32)}`;
  const ownerBId = "policy-runtime:run-00000000-0000-4000-8000-000000000001";
  const ownerA = await claim(driver, ownerAId);
  const root = await observeControlled(driver, ownerA);
  const selectedCard = await submitControlled(driver, ownerA, continuity, root,
    action(root.snapshot, "select_card"), "owner-a-select-card");
  assert.equal(selectedCard.result.status, "applied");
  assert.equal(selectedCard.result.successor.menu.cursor, "card_targets");
  await release(driver, ownerA, "owner-a-release");

  const ownerB = await claim(driver, ownerBId);
  assert.notEqual(ownerB.control_epoch, ownerA.control_epoch);
  const bPage = await observeControlled(driver, ownerB);
  assert.equal(bPage.snapshot.menu.cursor, "root");
  assert.deepEqual(bPage.snapshot.menu.selection, []);
  assert.notEqual(bPage.snapshot.snapshot_id, selectedCard.result.successor.snapshot_id);
  driver.status();
  const statusReadPage = await observeControlled(driver, ownerB);
  assert.equal(statusReadPage.snapshot.snapshot_id, bPage.snapshot.snapshot_id,
    "status and same-owner observation do not reset active text state");
  const stale = await submitControlled(driver, ownerB, continuity,
    { snapshot: selectedCard.result.successor },
    action(selectedCard.result.successor, "select_target"),
    "owner-a-stale-snapshot");
  assert.equal(stale.result.status, "not_applied");
  assert.equal(stale.result.reason_code, "stale_or_unadvertised_action");
  await assert.rejects(release(driver, ownerA, "late-owner-a-release"),
    /managed_control_not_authorized/);
  assert.equal(driver.status().control_held, true,
    "a stale epoch cannot release the current controller");
  await release(driver, ownerB, "owner-b-release");

  const ownerAReturn = await claim(driver, ownerAId);
  const aAgain = await observeControlled(driver, ownerAReturn);
  assert.equal(aAgain.snapshot.menu.cursor, "root");
  assert.deepEqual(aAgain.snapshot.menu.selection, []);
  assert.notEqual(aAgain.snapshot.snapshot_id, selectedCard.result.successor.snapshot_id);
  const aAgainSelection = await submitControlled(driver, ownerAReturn, continuity,
    aAgain, action(aAgain.snapshot, "select_card"), "owner-a-return-select-card");
  assert.equal(aAgainSelection.result.successor.menu.cursor, "card_targets");
  await release(driver, ownerAReturn, "owner-a-return-release");

  const managerSameId = await claim(driver, ownerAId, "manager");
  const managerPage = await observeControlled(driver, managerSameId);
  assert.equal(managerPage.snapshot.menu.cursor, "root",
    "the existing manager and client bearer roles remain distinct principals");
  assert.deepEqual(managerPage.snapshot.menu.selection, []);
  await release(driver, managerSameId, "manager-same-id-release");
  await driver.shutdown();
});

test("the same Workbench-style client can continue a multi-step choice across short leases", async () => {
  const driver = controlledDriver(driverCombatSource());
  await driver.handle({ command: "reset", seed: "OWNERTEST" });
  const continuity = driver.status().game_continuity_id;

  const ownerId = `workbench:${"b".repeat(32)}`;
  const observeLease = await claim(driver, ownerId);
  const root = await observeControlled(driver, observeLease);
  await release(driver, observeLease, "observe-release");

  const selectionLease = await claim(driver, ownerId);
  const selectedCard = await submitControlled(driver, selectionLease, continuity, root,
    action(root.snapshot, "select_card"), "same-client-select-card");
  assert.equal(selectedCard.result.status, "applied");
  await release(driver, selectionLease, "selection-release");

  const continuationLease = await claim(driver, ownerId);
  const targetPage = await observeControlled(driver, continuationLease);
  assert.equal(targetPage.snapshot.menu.cursor, "card_targets");
  assert.deepEqual(targetPage.snapshot.menu.selection, selectedCard.result.successor.menu.selection);
  const target = action(targetPage.snapshot, "select_target", 1);
  const confirmed = await submitControlled(driver, continuationLease, continuity,
    targetPage, target, "same-client-select-target");
  assert.equal(confirmed.result.successor.menu.cursor, "card_confirmation");
  await release(driver, continuationLease, "continuation-release");
  await driver.shutdown();
});

test("a queued old-epoch observation cannot change the new owner's menu", async () => {
  const driver = controlledDriver(driverCombatSource());
  await driver.handle({ command: "reset", seed: "OWNERLATE" });
  const ownerA = await claim(driver, `workbench:${"d".repeat(32)}`);
  await observeControlled(driver, ownerA);
  const releasePromise = release(driver, ownerA, "owner-a-release-before-late-observe");
  const lateAObservation = observeControlled(driver, ownerA);
  await releasePromise;
  const ownerB = await claim(driver, `workbench:${"e".repeat(32)}`);
  await assert.rejects(lateAObservation, /managed_control_intent_stale/);
  const bPage = await observeControlled(driver, ownerB);
  assert.equal(bPage.snapshot.menu.cursor, "root");
  assert.deepEqual(bPage.snapshot.menu.selection, []);
  await release(driver, ownerB, "owner-b-release-after-late-observe");
  await driver.shutdown();
});

test("raw mutation clears the v2 owner association before native dispatch", async () => {
  const driver = controlledDriver(driverCombatSource());
  await driver.handle({ command: "reset", seed: "OWNERRAW" });
  const continuity = driver.status().game_continuity_id;
  const ownerId = `workbench:${"f".repeat(32)}`;
  const first = await claim(driver, ownerId);
  const root = await observeControlled(driver, first);
  const selected = await submitControlled(driver, first, continuity, root,
    action(root.snapshot, "select_card"), "raw-owner-select-card");
  assert.equal(selected.result.successor.menu.cursor, "card_targets");
  await assert.rejects(sharedHandle(driver, { command: "step",
    expected_snapshot_id: root.snapshot.native_snapshot_id,
    bound_action_id: "raw-bound-action", mutation_request_id: "raw-mutation",
    control_token: first.control_token, control_epoch: first.control_epoch
  }), /text selection must not dispatch before play/);
  await assert.rejects(observeControlled(driver, first), /managed_text_state_owner_required/);
  await release(driver, first, "raw-owner-release");
  const second = await claim(driver, ownerId);
  const afterRaw = await observeControlled(driver, second);
  assert.equal(afterRaw.snapshot.menu.cursor, "root");
  assert.deepEqual(afterRaw.snapshot.menu.selection, []);
  await release(driver, second, "raw-owner-release-2");
  await driver.shutdown();
});

test("shared v2 claims require a well-formed logical owner; reset drops the prior owner", async () => {
  const driver = controlledDriver(driverCombatSource());
  await driver.handle({ command: "reset", seed: "OWNERTEST" });
  const continuity = driver.status().game_continuity_id;

  const missing = await claim(driver);
  await assert.rejects(observeControlled(driver, missing), /managed_text_state_owner_required/);
  await release(driver, missing, "owner-missing-release");

  await assert.rejects(claim(driver, "workbench:not-a-session-id"),
    /managed_text_state_owner_invalid/);
  await assert.rejects(claim(driver,
    `policy-runtime:run-${"-".repeat(36)}`), /managed_text_state_owner_invalid/);

  const owner = `workbench:${"c".repeat(32)}`;
  const firstLease = await claim(driver, owner);
  const root = await observeControlled(driver, firstLease);
  const selected = await submitControlled(driver, firstLease, continuity, root,
    action(root.snapshot, "select_card"), "reset-owner-select-card");
  assert.equal(selected.result.successor.menu.cursor, "card_targets");
  await release(driver, firstLease, "reset-owner-release");

  await driver.handle({ command: "reset", seed: "OWNERTEST2" });
  const secondContinuity = driver.status().game_continuity_id;
  assert.notEqual(secondContinuity, continuity);
  const secondLease = await claim(driver, owner);
  const afterReset = await observeControlled(driver, secondLease);
  assert.equal(afterReset.snapshot.menu.cursor, "root");
  assert.deepEqual(afterReset.snapshot.menu.selection, []);
  assert.notEqual(afterReset.snapshot.snapshot_id, selected.result.successor.snapshot_id);
  await release(driver, secondLease, "reset-owner-release-2");
  await driver.shutdown();
});

test("an ownerless shared legacy claim clears the prior v2 owner without inventing a legacy owner", async () => {
  const driver = controlledDriver(driverCombatSource());
  await driver.handle({ command: "reset", seed: "OWNERLEGACY" });
  const continuity = driver.status().game_continuity_id;
  const ownerId = `workbench:${"9".repeat(32)}`;
  const ownerA = await claim(driver, ownerId);
  const root = await observeControlled(driver, ownerA);
  const selected = await submitControlled(driver, ownerA, continuity, root,
    action(root.snapshot, "select_card"), "legacy-interrupt-select-card");
  await release(driver, ownerA, "legacy-interrupt-owner-release");

  const ownerlessLegacy = await claim(driver);
  await assert.rejects(observeControlled(driver, ownerlessLegacy),
    /managed_text_state_owner_required/);
  await release(driver, ownerlessLegacy, "legacy-ownerless-release");

  const ownerAReturn = await claim(driver, ownerId);
  const returned = await observeControlled(driver, ownerAReturn);
  assert.equal(returned.snapshot.menu.cursor, "root");
  assert.deepEqual(returned.snapshot.menu.selection, []);
  assert.notEqual(returned.snapshot.snapshot_id, selected.result.successor.snapshot_id);
  await release(driver, ownerAReturn, "legacy-interrupt-owner-return-release");
  await driver.shutdown();
});

test("unknown native delivery clears owner association and cannot be replayed", async () => {
  let nativeSubmits = 0;
  const driver = controlledDriver(driverCombatSource(), { async submit() {
    nativeSubmits++;
    return { delivery: "unknown", reason_code: "managed_session_tainted_after_unknown",
      detail: "outcome uncertain", successor: null };
  } });
  await driver.handle({ command: "reset", seed: "OWNERUNKNOWN" });
  const continuity = driver.status().game_continuity_id;
  const lease = await claim(driver, `policy-runtime:run-00000000-0000-4000-8000-000000000002`);
  const root = await observeControlled(driver, lease);
  const card = await submitControlled(driver, lease, continuity, root,
    action(root.snapshot, "select_card"), "unknown-owner-select-card");
  const targetPage = { snapshot: card.result.successor };
  const selectedTarget = await submitControlled(driver, lease, continuity, targetPage,
    action(targetPage.snapshot, "select_target"), "unknown-owner-select-target");
  const confirmation = { snapshot: selectedTarget.result.successor };
  const play = action(confirmation.snapshot, "play");
  const unknown = await submitControlled(driver, lease, continuity, confirmation,
    play, "unknown-owner-native-play");
  assert.equal(unknown.result.status, "unknown");
  assert.equal(unknown.result.retry, "never");
  await assert.rejects(observeControlled(driver, lease), /managed_text_state_owner_required/);
  await assert.rejects(submitControlled(driver, lease, continuity, confirmation,
    play, "unknown-owner-native-play"), /managed_text_state_owner_required/);
  assert.equal(nativeSubmits, 1);
  await release(driver, lease, "unknown-owner-release");
  await driver.shutdown();
});

test("single-consumer stdio keeps its implicit v2 owner across short leases", async () => {
  const driver = controlledDriver(driverCombatSource());
  await driver.handle({ command: "reset", seed: "OWNERSTDIO" });
  const continuity = driver.status().game_continuity_id;
  const first = await driver.handle({ command: "claim_control",
    expected_runtime_instance_id: identity.runtimeInstanceId,
    expected_game_continuity_id: continuity });
  const root = await driver.handle({ command: "text_observe", require_control: true,
    input_profile: MANAGED_TEXT_MENU_V2_PROFILE, control_token: first.control_token,
    control_epoch: first.control_epoch });
  await driver.handle({ command: "text_submit", input_profile: MANAGED_TEXT_MENU_V2_PROFILE,
    expected_game_continuity_id: continuity,
    expected_snapshot_id: root.context.snapshot.snapshot_id,
    action_id: action(root.context.snapshot, "select_card").action_id,
    mutation_request_id: "stdio-select-card", control_token: first.control_token,
    control_epoch: first.control_epoch });
  await release(driver, first, "stdio-first-release");
  const second = await driver.handle({ command: "claim_control",
    expected_runtime_instance_id: identity.runtimeInstanceId,
    expected_game_continuity_id: continuity });
  const continued = await driver.handle({ command: "text_observe", require_control: true,
    input_profile: MANAGED_TEXT_MENU_V2_PROFILE, control_token: second.control_token,
    control_epoch: second.control_epoch });
  assert.equal(continued.context.snapshot.menu.cursor, "card_targets");
  await release(driver, second, "stdio-second-release");
  await driver.shutdown();
});

test("targeted text card choice keeps public order, exact pair and one native submit", async () => {
  const rawRequests = [];
  const process = { async request(raw) {
    if (raw.cmd === "start_run") return combatDecision();
    rawRequests.push(raw);
    return combatDecision();
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "V2TARGET" });
  const adapter = new ManagedTextMenuV2SessionAdapter(session);
  const source = session.observe();
  const root = adapter.observe();
  assertContractPage(root);
  assert.equal(root.input_profile, "text-menu-v2");
  assert.equal(root.menu.native_snapshot_id, source.snapshot_id);
  assert.deepEqual(root.menu.selection, []);
  assert.deepEqual(root.menu_actions.actions.map((item) => item.verb),
    ["end_turn", "select_card", "select_card"]);
  assert.equal(action(root, "select_card").label, "Choose card Strike");
  assert.equal(JSON.stringify(root).includes("native-strike"), false);

  const selected = await adapter.submit(request(root, action(root, "select_card"), "card"));
  assert.equal(selected.status, "applied");
  assert.equal(selected.effect_domain, "text_menu");
  assert.equal(selected.native_delivery, null);
  assert.equal(rawRequests.length, 0);
  const targets = selected.successor;
  assertContractPage(targets);
  assert.equal(targets.menu.cursor, "card_targets");
  assert.equal(targets.menu.native_snapshot_id, root.menu.native_snapshot_id);
  assert.deepEqual(targets.menu_actions.actions.map((item) => item.verb),
    ["select_target", "select_target", "cancel_selection", "back"]);
  assert.deepEqual(targets.menu_actions.actions.slice(0, 2).map((item) => item.label),
    ["Choose target Cultist (1 of 2 shown)", "Choose target Cultist (2 of 2 shown)"]);
  assert.deepEqual(adapter.observe(), targets);

  const chosen = await adapter.submit(request(targets, action(targets, "select_target", 1), "target"));
  const confirm = chosen.successor;
  assertContractPage(confirm);
  assert.equal(confirm.menu.cursor, "card_confirmation");
  assert.deepEqual(confirm.menu.selection, [
    { role: "card", referent_id: action(root, "select_card").subject_referent_id },
    { role: "target", referent_id: action(targets, "select_target", 1).subject_referent_id }
  ]);
  assert.equal(rawRequests.length, 0);
  const back = await adapter.submit(request(confirm, action(confirm, "back"), "back"));
  assert.equal(back.successor.menu.cursor, "card_targets");
  assert.deepEqual(back.successor.menu.selection, targets.menu.selection);
  assert.equal(rawRequests.length, 0);
  const again = await adapter.submit(request(back.successor,
    action(back.successor, "select_target", 1), "target-again"));
  const play = action(again.successor, "play");
  const delivered = await adapter.submit(request(again.successor, play, "confirm"));
  assert.equal(delivered.status, "applied");
  assert.equal(delivered.native_delivery, "delivered");
  assert.deepEqual(rawRequests, [{ cmd: "action", action: "play_card",
    args: { card_ref: "native-strike", target_ref: "native-enemy-1" } }]);
  assert.deepEqual(await adapter.submit(request(again.successor, play, "confirm")), delivered);
  assert.equal(rawRequests.length, 1);
});

test("card-only confirmation never invents a target and cancel changes no native state", async () => {
  const state = combatDecision();
  const source = projectManagedCandidateDecision({ state, ...identity }).snapshot;
  let submits = 0;
  const adapter = new ManagedTextMenuV2SessionAdapter({ observe: () => source,
    async submit() { submits++; throw new Error("cancel must not dispatch"); } });
  const root = adapter.observe();
  const defend = action(root, "select_card", 1);
  const selected = await adapter.submit(request(root, defend, "defend"));
  assert.equal(selected.successor.menu.cursor, "card_confirmation");
  assert.deepEqual(selected.successor.menu.selection,
    [{ role: "card", referent_id: defend.subject_referent_id }]);
  assert.deepEqual(action(selected.successor, "play").arguments, []);
  const cancelled = await adapter.submit(request(selected.successor,
    action(selected.successor, "cancel_selection"), "cancel"));
  assert.equal(cancelled.successor.menu.cursor, "root");
  assert.deepEqual(cancelled.successor.menu.selection, []);
  assert.equal(submits, 0);
});

test("source/catalog changes and incomplete or inconsistent pairs fail closed", async () => {
  const source = projectManagedCandidateDecision({ state: combatDecision(), ...identity }).snapshot;
  let current = source;
  const adapter = new ManagedTextMenuV2SessionAdapter({ observe: () => current,
    async submit() { throw new Error("stale action dispatched"); } });
  const root = adapter.observe();
  const staged = await adapter.submit(request(root, action(root, "select_card"), "choose"));
  current = structuredClone(source);
  current.bound_actions.actions[0].label = "Changed public binding";
  const changed = adapter.observe();
  assert.equal(changed.menu.cursor, "root");
  assert.deepEqual(changed.menu.selection, []);
  assert.notEqual(changed.snapshot_id, root.snapshot_id);
  const stale = await adapter.submit(request(staged.successor,
    action(staged.successor, "select_target"), "stale"));
  assert.equal(stale.reason_code, "stale_or_unadvertised_action");
  assert.equal(stale.native_delivery, null);

  current = structuredClone(source);
  current.interaction.content.surface.playable_cards[0].target_entity_ids.pop();
  assert.equal(adapter.observe().menu_actions.status, "unavailable");
  current = projectManagedCandidateDecision({ state: {
    ...combatDecision(), hand: [{ ...combatDecision().hand[0], target_type: "AnyAlly" }]
  }, ...identity }).snapshot;
  assert.equal(adapter.observe().menu_actions.status, "unavailable");
});

test("unknown confirmation is not retried and closes Managed mutation authority", async () => {
  const rawRequests = [];
  const process = { async request(raw) {
    if (raw.cmd === "start_run") return combatDecision();
    rawRequests.push(raw);
    throw new Error("response lost after dispatch");
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "V2UNKNOWN" });
  const adapter = new ManagedTextMenuV2SessionAdapter(session);
  const root = adapter.observe();
  const card = await adapter.submit(request(root, action(root, "select_card"), "choose"));
  const target = await adapter.submit(request(card.successor,
    action(card.successor, "select_target"), "target"));
  const confirm = request(target.successor, action(target.successor, "play"), "native");
  const unknown = await adapter.submit(confirm);
  assert.equal(unknown.status, "unknown");
  assert.equal(unknown.native_delivery, "unknown");
  assert.equal(unknown.retry, "never");
  assert.equal(unknown.successor, null);
  assert.deepEqual(await adapter.submit(confirm), unknown);
  assert.equal(rawRequests.length, 1);
  assert.equal(session.tainted, true);
});

test("terminal v2 page stays observed with no manufactured action", () => {
  const source = projectManagedCandidateDecision({
    state: { ...combatDecision(), decision: "game_over", victory: false }, ...identity
  }).snapshot;
  const page = new ManagedTextMenuV2SessionAdapter({ observe: () => source,
    async submit() { throw new Error("terminal must not submit"); } }).observe();
  assertContractPage(page);
  assert.equal(page.schema, "sts2.player-environment/text-menu-snapshot-2");
  assert.equal(page.status, "observed");
  assert.equal(page.interaction.kind, "game_over");
  assert.deepEqual(page.menu.selection, []);
  assert.equal(page.menu_actions.status, "complete");
  assert.deepEqual(page.menu_actions.actions, []);
});
