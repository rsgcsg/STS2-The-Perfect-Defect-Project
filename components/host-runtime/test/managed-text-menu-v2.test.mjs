import assert from "node:assert/strict";
import test from "node:test";
import { ManagedPlayerEnvironmentSession, projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { ManagedTextMenuV2SessionAdapter, MANAGED_TEXT_MENU_V2_PROFILE } from "../src/managed-text-menu-v2.mjs";

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

function action(page, verb, index = 0) {
  const found = page.menu_actions.actions.filter((item) => item.verb === verb);
  assert.ok(found[index], `Missing ${verb} at index ${index}`);
  return found[index];
}

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
  assert.equal(targets.menu.cursor, "card_targets");
  assert.equal(targets.menu.native_snapshot_id, root.menu.native_snapshot_id);
  assert.deepEqual(targets.menu_actions.actions.map((item) => item.verb),
    ["select_target", "select_target", "cancel_selection", "back"]);
  assert.deepEqual(targets.menu_actions.actions.slice(0, 2).map((item) => item.label),
    ["Choose target Cultist (1 of 2 shown)", "Choose target Cultist (2 of 2 shown)"]);
  assert.deepEqual(adapter.observe(), targets);

  const chosen = await adapter.submit(request(targets, action(targets, "select_target", 1), "target"));
  const confirm = chosen.successor;
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
  assert.equal(page.schema, "sts2.player-environment/text-menu-snapshot-2");
  assert.equal(page.status, "observed");
  assert.equal(page.interaction.kind, "game_over");
  assert.deepEqual(page.menu.selection, []);
  assert.equal(page.menu_actions.status, "complete");
  assert.deepEqual(page.menu_actions.actions, []);
});
