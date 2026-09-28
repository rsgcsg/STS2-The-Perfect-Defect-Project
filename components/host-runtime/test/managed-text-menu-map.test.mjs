import assert from "node:assert/strict";
import test from "node:test";
import {
  ManagedPlayerEnvironmentSession,
  projectManagedCandidateDecision
} from "../src/managed-player-environment.mjs";
import {
  MANAGED_TEXT_MENU_PROFILE,
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

  await assert.rejects(() => adapter.submit({
    request_id: "wrong-profile", expected_snapshot_id: snapshot.snapshot_id,
    action_id: snapshot.menu_actions.actions[0].action_id, input_profile: "player-environment-v1"
  }), /text-menu-v1/u);
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
  assert.equal(rejected.native_delivery, "not_delivered");
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
  const refused = await adapter.submit({ ...input, request_id: "after-unknown" });
  assert.equal(refused.status, "not_applied");
  assert.equal(actionCalls, 1);
});
