import assert from "node:assert/strict";
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
