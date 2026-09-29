import assert from "node:assert/strict";
import test from "node:test";
import { ManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { ManagedPeDriverSession } from "../src/managed-pe-driver-session.mjs";

function decision(floor = 0) {
  return {
    type: "decision", decision: "map_select",
    context: { act: 1, act_index: 0, act_definition_id: "OVERGROWTH",
      act_name: "Overgrowth", floor, total_floor: floor, ascension: 0,
      bosses: [{ id: "VANTOM_BOSS", name: "Vantom", order: 0 }], modifiers: [] },
    choices: [{ col: 3, row: 0, type: "Monster", native_ref: "native-map-point",
      children: [{ col: 2, row: 1, type: "Monster" }] }],
    visible_map: { type: "map", rows: [[{ col: 2, row: 1, type: "Monster",
      children: [{ col: 3, row: 16 }], visited: false, current: false }]],
      boss: { col: 3, row: 16, type: "Boss" }, current_coord: null },
    player: { name: "The Ironclad", character_id: "IRONCLAD", hp: 80,
      max_hp: 80, gold: 99, native_ref: "player-native", max_potion_slots: 3,
      relics: [], potions: [], deck: [] }
  };
}

function fixture() {
  const calls = [];
  let floor = 0;
  let failReset = false;
  let unknownAction = false;
  let projectionFailure = false;
  let pendingAction = null;
  let onActionEntered = null;
  const process = {
    async request(request) {
      calls.push(request);
      if (request.cmd === "start_run" || request.cmd === "reset_run") {
        floor = 0;
        if (failReset) { failReset = false; throw new Error("reset failed after simulator replacement"); }
        return decision(floor);
      }
      if (request.cmd === "get_map") return decision(floor).visible_map;
      if (request.cmd === "run_identity") return { type: "run_identity", active: true, seed: "SEED" };
      if (request.cmd === "action") {
        onActionEntered?.();
        if (pendingAction) await pendingAction;
        if (unknownAction) throw new Error("transport lost after native action");
        floor += 1;
        if (projectionFailure) return { ...decision(floor),
          player: { ...decision(floor).player, gold: "bad" } };
        return decision(floor);
      }
      throw new Error(`unexpected ${request.cmd}`);
    },
    async stop() { return { code: 0 }; }
  };
  const session = new ManagedPlayerEnvironmentSession({ process,
    runtimeInstanceId: "driver-test-instance", environmentFingerprint: "driver-test-environment" });
  const driver = new ManagedPeDriverSession({ session, runtime: {
    process, build: "test-build", runtimeIdentity: "test-runtime",
    adapterRuntimeInstanceId: "driver-test-instance"
  }, environmentFingerprint: "driver-test-environment" });
  return { driver, calls,
    failNextReset() { failReset = true; },
    makeActionUnknown() { unknownAction = true; },
    makeProjectionFail() { projectionFailure = true; },
    holdAction(promise, entered) { pendingAction = promise; onActionEntered = entered; }
  };
}

async function context(driver) {
  return (await driver.handle({ command: "text_observe" })).context;
}

function submit(page, requestId = "mutation-1") {
  return { command: "text_submit", mutation_request_id: requestId,
    expected_game_continuity_id: page.game_continuity_id,
    expected_snapshot_id: page.snapshot.snapshot_id,
    action_id: page.snapshot.menu_actions.actions[0].action_id };
}

test("public text context binds one real Managed source and replays exact request only", async () => {
  const { driver, calls } = fixture();
  const reset = await driver.handle({ command: "reset", seed: "SEED" });
  const page = await context(driver);
  assert.deepEqual(Object.keys(page).sort(), ["game_continuity_id", "schema", "snapshot"]);
  assert.equal(page.schema, "sts2.player-environment/text-menu-observation-context-1");
  assert.equal(page.snapshot.input_profile, "text-menu-v1");
  assert.equal(page.snapshot.menu.native_snapshot_id, reset.snapshot.snapshot_id);
  assert.equal(JSON.stringify(page).includes("native-map-point"), false);
  const first = await driver.handle(submit(page));
  assert.equal(first.result.status, "applied");
  assert.deepEqual(await driver.handle(submit(page)), first);
  assert.equal(calls.filter((call) => call.cmd === "action").length, 1);
  const conflict = await driver.handle({ ...submit(page), action_id: "other-action" });
  assert.equal(conflict.result.reason_code, "request_id_conflict");
  await assert.rejects(driver.handle({ command: "step", mutation_request_id: "mutation-1",
    expected_snapshot_id: reset.snapshot.snapshot_id,
    bound_action_id: reset.snapshot.bound_actions.actions[0].bound_action_id }),
  /mutation_request_id_conflict_between_routes/);
  const staleRaw = await driver.handle({ command: "step", mutation_request_id: "raw-stale",
    expected_snapshot_id: reset.snapshot.snapshot_id,
    bound_action_id: reset.snapshot.bound_actions.actions[0].bound_action_id });
  assert.equal(staleRaw.receipt.reason_code, "stale_snapshot");
  assert.equal(calls.filter((call) => call.cmd === "action").length, 1);
});

test("raw mutation invalidates text binding; same-seed resets rotate continuity and fence old request IDs", async () => {
  const { driver, calls } = fixture();
  const reset = await driver.handle({ command: "reset", seed: "SEED" });
  const old = await context(driver);
  const raw = await driver.handle({ command: "step", mutation_request_id: "raw-1",
    expected_snapshot_id: reset.snapshot.snapshot_id,
    bound_action_id: reset.snapshot.bound_actions.actions[0].bound_action_id });
  assert.equal(raw.receipt.delivery, "delivered");
  const currentAfterRaw = await context(driver);
  await assert.rejects(driver.handle(submit(currentAfterRaw, "raw-1")),
    /mutation_request_id_conflict_between_routes/);
  assert.equal((await driver.handle(submit(old))).result.reason_code, "stale_or_unadvertised_action");
  const second = await driver.handle({ command: "reset", seed: "SEED" });
  const next = await context(driver);
  assert.notEqual(next.game_continuity_id, old.game_continuity_id);
  assert.notEqual(second.snapshot.snapshot_id, reset.snapshot.snapshot_id);
  await assert.rejects(driver.handle(submit(old, "old-token")), /stale_game_continuity/);
  await assert.rejects(driver.handle(submit(next)), /text_request_id_conflict_across_episodes/);
  assert.equal((await driver.handle(submit(next, "new-mutation"))).result.status, "applied");
  assert.equal(calls.filter((call) => call.cmd === "action").length, 2);
  await driver.handle({ command: "reset", seed: "SEED" });
  const third = await context(driver);
  assert.notEqual(third.game_continuity_id, next.game_continuity_id);
});

test("failed reset quarantines raw and text routes until a successful reset", async () => {
  const { driver, failNextReset } = fixture();
  await driver.handle({ command: "reset", seed: "SEED" });
  const old = await context(driver);
  failNextReset();
  await assert.rejects(driver.handle({ command: "reset", seed: "SEED" }), /reset failed/);
  for (const command of ["observe", "read", "step", "text_observe", "text_submit", "episode_identity"]) {
    await assert.rejects(driver.handle({ command }), /managed_episode_unavailable_reset_required/);
  }
  await driver.handle({ command: "reset", seed: "SEED" });
  const next = await context(driver);
  assert.notEqual(next.game_continuity_id, old.game_continuity_id);
  await assert.rejects(driver.handle(submit(old)), /stale_game_continuity/);
});

test("unknown text mutation is never retried or bypassed by raw step, including queued step", async () => {
  const { driver, calls, makeActionUnknown, holdAction } = fixture();
  await driver.handle({ command: "reset", seed: "SEED" });
  const page = await context(driver);
  let release;
  let entered;
  const actionEntered = new Promise((resolve) => { entered = resolve; });
  holdAction(new Promise((resolve) => { release = resolve; }), entered);
  makeActionUnknown();
  const pendingText = driver.handle(submit(page));
  await actionEntered;
  let rawSettled = false;
  const pendingRaw = driver.handle({ command: "step", mutation_request_id: "raw-after-unknown",
    expected_snapshot_id: page.snapshot.menu.native_snapshot_id,
    bound_action_id: "untrusted" });
  pendingRaw.finally(() => { rawSettled = true; }).catch(() => undefined);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(rawSettled, false);
  assert.equal(calls.filter((call) => call.cmd === "action").length, 1);
  release();
  const first = await pendingText;
  assert.equal(first.result.status, "unknown");
  assert.equal(first.result.retry, "never");
  await assert.rejects(pendingRaw, /managed_session_tainted_after_unknown/);
  assert.deepEqual(await driver.handle(submit(page)), first);
  assert.equal(calls.filter((call) => call.cmd === "action").length, 1);
  await assert.rejects(driver.handle({ command: "reset", seed: "SEED" }), /tainted/);
  await assert.rejects(driver.handle({ command: "text_observe" }), /managed_episode_unavailable_reset_required/);
  await driver.handle({ command: "close" });
  await assert.rejects(driver.handle({ command: "reset", seed: "SEED" }), /driver_closed/);
});

test("delivered text action with rejected successor projection keeps Receipt and blocks both routes", async () => {
  const { driver, calls, makeProjectionFail } = fixture();
  await driver.handle({ command: "reset", seed: "SEED" });
  const page = await context(driver);
  makeProjectionFail();
  const first = await driver.handle(submit(page, "projection-1"));
  assert.equal(first.result.status, "applied");
  assert.equal(first.result.native_delivery, "delivered");
  assert.equal(first.result.reason_code, "managed_successor_projection_failed");
  assert.equal(first.result.successor, null);
  assert.deepEqual(await driver.handle(submit(page, "projection-1")), first);
  await assert.rejects(driver.handle({ command: "step", mutation_request_id: "raw-after-projection",
    expected_snapshot_id: page.snapshot.menu.native_snapshot_id,
    bound_action_id: "untrusted" }), /managed_session_tainted_after_successor_projection_failure/);
  const later = await driver.handle(submit(page, "text-after-projection"));
  assert.equal(later.result.reason_code, "runtime_tainted_after_successor_projection_failure");
  assert.equal(later.result.retry, "never");
  assert.equal(later.result.successor, null);
  assert.equal(calls.filter((call) => call.cmd === "action").length, 1);
});

test("v2 opt-in shares continuity and request fence without changing the v1 reader", async () => {
  const { driver, calls } = fixture();
  await driver.handle({ command: "reset", seed: "SEED" });
  const v2 = (await driver.handle({ command: "text_observe", input_profile: "text-menu-v2" })).context;
  assert.equal(v2.schema, "sts2.player-environment/text-menu-observation-context-2");
  assert.equal(v2.snapshot.input_profile, "text-menu-v2");
  assert.deepEqual(v2.snapshot.menu.selection, []);
  const v1 = await context(driver);
  assert.equal(v1.snapshot.input_profile, "text-menu-v1");
  assert.equal((await driver.handle({ command: "text_observe", input_profile: "text-menu-v2" }))
    .context.snapshot.snapshot_id, v2.snapshot.snapshot_id);
  await assert.rejects(driver.handle({ command: "text_observe", input_profile: "text-menu-v3" }),
    /Unsupported text-menu input_profile/);

  const request = { command: "text_submit", input_profile: "text-menu-v2",
    mutation_request_id: "v2-id", expected_game_continuity_id: v2.game_continuity_id,
    expected_snapshot_id: v2.snapshot.snapshot_id,
    action_id: v2.snapshot.menu_actions.actions[0].action_id };
  const first = await driver.handle(request);
  assert.equal(first.result.schema, "sts2.player-environment/text-menu-action-result-2");
  assert.equal(first.result.status, "applied");
  assert.deepEqual(await driver.handle(request), first);
  await assert.rejects(driver.handle(submit(v1, "v2-id")),
    /mutation_request_id_conflict_between_routes/);
  assert.equal(calls.filter((call) => call.cmd === "action").length, 1);

  const next = (await driver.handle({ command: "text_observe", input_profile: "text-menu-v2" })).context;
  await assert.rejects(driver.handle({ command: "step", mutation_request_id: "v2-id",
    expected_snapshot_id: next.snapshot.menu.native_snapshot_id,
    bound_action_id: "untrusted" }), /mutation_request_id_conflict_between_routes/);
  await driver.handle({ command: "reset", seed: "SEED" });
  const episode = (await driver.handle({ command: "text_observe", input_profile: "text-menu-v2" })).context;
  assert.notEqual(episode.game_continuity_id, v2.game_continuity_id);
  await assert.rejects(driver.handle({ ...request, mutation_request_id: "fresh-id" }),
    /stale_game_continuity/);
  await assert.rejects(driver.handle({ ...request,
    expected_game_continuity_id: episode.game_continuity_id,
    expected_snapshot_id: episode.snapshot.snapshot_id,
    action_id: episode.snapshot.menu_actions.actions[0].action_id }),
  /text_request_id_conflict_across_episodes/);
});
