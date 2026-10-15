import assert from "node:assert/strict";
import test from "node:test";
import { combatState } from "./fixtures/managed-fair-combat.mjs";
import {
  ManagedPlayerEnvironmentSession,
  projectManagedCandidateDecision
} from "../src/managed-player-environment.mjs";

const identity = { runtimeInstanceId: "anyplayer-runtime",
  environmentFingerprint: "anyplayer-environment", sequence: 1 };

function withPotion(overrides = {}) {
  const state = combatState();
  state.player.potions = [{ slot: 0, native_ref: "native-potion", id: "ENERGY_POTION",
    name: "Energy Potion", description: "Gain 2 Energy.", keywords: [], card_previews: [],
    hover_facts_complete: true, target_type: "AnyPlayer", usage: "CombatOnly",
    can_use: true, can_discard: true, binding_supported: true,
    valid_target_refs: [state.player.native_ref], ...overrides }];
  return state;
}

for (const identityMode of ["crypto", "sequence"]) {
  test(`AnyPlayer publishes a complete catalog and the existing public player target (${identityMode})`, () => {
    const state = withPotion();
    const projection = projectManagedCandidateDecision({ state, ...identity, identityMode });
    const snapshot = projection.snapshot;
    assert.equal(snapshot.completeness.status, "complete");
    assert.equal(snapshot.bound_actions.status, "complete");
    assert.equal(snapshot.bound_actions.actions.length, 7); // Four card targets, end, use, discard.
    const playerId = snapshot.persistent.content.player.entity_id;
    const player = snapshot.referents.filter((item) => item.role === "player");
    assert.equal(player.length, 1);
    assert.equal(player[0].referent_id, playerId);
    const use = snapshot.bound_actions.actions.find((item) => item.verb === "use");
    assert.equal(use.label, "Use Energy Potion on Ironclad");
    assert.deepEqual(use.arguments, [{ role: "target", referent_id: playerId }]);
    assert.deepEqual(snapshot.interaction.content.surface.usable_potions[0].target_entity_ids, [playerId]);
    assert.deepEqual(projection.bindings.get(use.bound_action_id).raw_request,
      { cmd: "action", action: "use_potion",
        args: { potion_slot: 0, potion_ref: "native-potion", target_ref: state.player.native_ref } });
    assert.equal(JSON.stringify(snapshot).includes("native-"), false);
  });
}

test("unusable AnyPlayer keeps its native target domain, held potion, discard and other actions", () => {
  for (const validTargetRefs of [["native-1"], []]) {
    const projection = projectManagedCandidateDecision({ state: withPotion({
      can_use: false, valid_target_refs: validTargetRefs }), ...identity });
    const snapshot = projection.snapshot;
    assert.equal(snapshot.completeness.status, "complete");
    assert.equal(snapshot.bound_actions.status, "complete");
    assert.equal(snapshot.bound_actions.actions.length, 6);
    assert.equal(snapshot.bound_actions.actions.some((item) => item.verb === "use"), false);
    assert.equal(snapshot.persistent.content.player.potions.length, 1);
    assert.equal(snapshot.interaction.content.context.player.potion_states[0].can_use, false);
    const potion = snapshot.referents.find((item) => item.role === "usable_potion");
    assert.deepEqual(potion.properties.target_entity_ids, validTargetRefs.length
      ? [snapshot.persistent.content.player.entity_id] : []);
    assert.equal([...projection.bindings.values()].filter((item) =>
      item.raw_request.action === "discard_potion").length, 1);
  }
});

test("AnyPlayer rejects incomplete, duplicate or foreign target domains without filtering the held item", () => {
  for (const overrides of [{ valid_target_refs: undefined }, { valid_target_refs: null },
    { can_use: undefined }, { can_use: null },
    { can_use: false, valid_target_refs: undefined }, { can_use: false, valid_target_refs: null },
    { valid_target_refs: "native-1" }, { valid_target_refs: [null] },
    { valid_target_refs: 42 }, { valid_target_refs: {} },
    { valid_target_refs: ["native-foreign-player"] },
    { valid_target_refs: ["native-1", "native-1"] }, { valid_target_refs: ["native-1", "native-foreign-player"] },
    { valid_target_refs: [] }, { binding_supported: false, can_use: false }]) {
    const projection = projectManagedCandidateDecision({ state: withPotion(overrides), ...identity });
    assert.equal(projection.snapshot.bound_actions.status, "unavailable", JSON.stringify(overrides));
    assert.equal(projection.snapshot.completeness.status, "partial");
    assert.deepEqual(projection.snapshot.bound_actions.actions, []);
    assert.equal(projection.bindings.size, 0);
    assert.equal(projection.snapshot.persistent.content.player.potions.length, 1);
    assert.ok(projection.snapshot.completeness.missing.includes("native_unsupported_potion_target_type_or_binding"));
  }
});

test("identical held AnyPlayer potions retain distinct complete use and discard bindings", () => {
  const state = withPotion();
  state.player.potions.push({ ...state.player.potions[0], slot: 1, native_ref: "native-potion-duplicate" });
  const projection = projectManagedCandidateDecision({ state, ...identity });
  assert.equal(projection.snapshot.bound_actions.status, "complete");
  assert.equal(projection.snapshot.bound_actions.actions.length, 9);
  const use = [...projection.bindings.values()].filter((item) => item.raw_request.action === "use_potion");
  assert.equal(use.length, 2);
  assert.equal(new Set(use.map((item) => item.action.subject_referent_id)).size, 2);
  assert.deepEqual(use.map((item) => item.raw_request.args), [
    { potion_slot: 0, potion_ref: "native-potion", target_ref: "native-1" },
    { potion_slot: 1, potion_ref: "native-potion-duplicate", target_ref: "native-1" }
  ]);
  assert.equal(projection.snapshot.persistent.content.player.potions.length, 2);
});

test("AnyPlayer dispatches the bound player once, retains stale refusal and leaves generated choices pending", async () => {
  const before = withPotion({ id: "COLORLESS_POTION", name: "Colorless Potion" });
  // Synthetic exact callback shape: Colorless uses GetSelectedCards(cards, 0, 1),
  // whose unscoped card_select child remains unsupported by this adapter.
  const child = { ...combatState(), decision: "card_select", min_select: 0, max_select: 1,
    cards: [combatState().hand[0]], player: { ...before.player, potions: [] } };
  const calls = [];
  const process = { async request(value) {
    calls.push(value);
    return value.cmd === "start_run" ? before : child;
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  const snapshot = await session.mount({ seed: "ANYPLAYER-SYNTHETIC" });
  const action = snapshot.bound_actions.actions.find((item) => item.verb === "use");
  assert.ok(action);
  const request = { requestId: "explicit-player-use", expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: action.bound_action_id };
  const result = await session.submit(request);
  assert.equal(result.delivery, "delivered");
  assert.equal(result.retry.allowed, false);
  assert.equal(result.successor.status, "visible_unsupported");
  assert.equal(result.successor.interaction.kind, "card_selection");
  assert.equal(result.successor.bound_actions.status, "unavailable");
  assert.ok(result.successor.completeness.missing.includes("unknown_native_card_selector_purpose_and_preferences"));
  assert.deepEqual(calls[1], { cmd: "action", action: "use_potion",
    args: { potion_slot: 0, potion_ref: "native-potion", target_ref: before.player.native_ref } });
  assert.deepEqual(await session.submit(request), result);
  const stale = await session.submit({ ...request, requestId: "stale-player-use" });
  assert.equal(stale.delivery, "not_delivered");
  assert.equal(stale.reason_code, "stale_snapshot");
  assert.equal(calls.length, 2); // No automatic child choice or replay.
});

test("AnyPlayer unknown delivery is fenced and never retried", async () => {
  const before = withPotion();
  let dispatches = 0;
  const process = { async request(value) {
    if (value.cmd === "start_run") return before;
    dispatches++;
    throw new Error("original native outcome is unknown");
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  const snapshot = await session.mount({ seed: "ANYPLAYER-UNKNOWN-SYNTHETIC" });
  const action = snapshot.bound_actions.actions.find((item) => item.verb === "use");
  assert.ok(action);
  const request = { requestId: "unknown-player-use", expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: action.bound_action_id };
  const result = await session.submit(request);
  assert.equal(result.delivery, "unknown");
  assert.equal(result.retry.allowed, false);
  assert.equal(session.tainted, true);
  assert.deepEqual(await session.submit(request), result);
  assert.equal((await session.submit({ ...request, requestId: "forbidden-retry" })).delivery, "not_delivered");
  assert.equal(dispatches, 1);
});
