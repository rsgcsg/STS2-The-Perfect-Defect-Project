import assert from "node:assert/strict";
import test from "node:test";
import { ManagedPlayerEnvironmentSession, projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { chooseManagedCandidateAction } from "../src/managed-candidate.mjs";
import { shopRoom } from "./fixtures/managed-shop.mjs";

const identity = { runtimeInstanceId: "merchant-open", environmentFingerprint: "test", sequence: 1 };

test("a closed merchant presents the Reference room controls and keeps inventory inputs private until forward open", () => {
  const state = shopRoom();
  const p = projectManagedCandidateDecision({ state, ...identity });
  assert.equal(p.snapshot.completeness.status, "complete");
  assert.equal(p.snapshot.interaction.stage, "ready");
  const shopSession = p.snapshot.interaction.content.surface.shop_session_entity_id;
  assert.equal(typeof shopSession, "string");
  assert.deepEqual(p.snapshot.interaction.content, {
    surface: { kind: "shop_room", shop_session_entity_id: shopSession, can_open_inventory: true, can_proceed: true }, context: { kind: "shop" }
  });
  assert.deepEqual(p.snapshot.bound_actions.actions.map(action => [action.verb, action.subject_referent_id]), [["open", null], ["activate", null]]);
  assert.equal(JSON.stringify(p.snapshot).includes("private-original"), false);
  assert.ok(!p.snapshot.referents.some(ref => ref.role.startsWith("shop_")));
  const binding = [...p.bindings.values()].find(value => value.action.verb === "open");
  assert.equal(chooseManagedCandidateAction(state).action, "leave_shop");
  assert.deepEqual(binding.raw_request.args, { room_ref: state.room_ref, inventory_ref: state.inventory_ref });
  for (const mutate of [value => { value.presentation_owner.inventory_ref = "foreign"; },
    value => { value.presentation_owner.player_ref = "foreign"; }, value => { value.presentation_owner.stage = "inventory"; }]) {
    const changed = structuredClone(state); mutate(changed);
    const projected = projectManagedCandidateDecision({ state: changed, ...identity });
    assert.equal(projected.snapshot.completeness.status, "partial");
    assert.equal(projected.bindings.size, 0);
  }
});

test("forward open dispatches the original bound owner and advances to the actual returned inventory", async () => {
  const state = shopRoom(), requests = [];
  const inventory = { ...state, decision: "shop", cards: [], relics: [], potions: [], card_removal: null,
    presentation_owner: { ...state.presentation_owner, stage: "inventory" } };
  const session = new ManagedPlayerEnvironmentSession({ ...identity, process: {
    async request(request) {
      if (request.cmd === "start_run") return state;
      requests.push(request);
      assert.deepEqual(request.args, { room_ref: state.room_ref, inventory_ref: state.inventory_ref });
      assert.ok(["open_shop_inventory", "close_shop_inventory"].includes(request.action));
      return request.action === "open_shop_inventory" ? inventory : state;
    }
  } });
  const snapshot = await session.mount({ seed: "synthetic-forward-shop-open" });
  const open = snapshot.bound_actions.actions.find(action => action.verb === "open");
  const receipt = await session.submit({ requestId: "open", expectedSnapshotId: snapshot.snapshot_id, boundActionId: open.bound_action_id });
  assert.equal(receipt.delivery, "delivered");
  assert.equal(receipt.successor.interaction.kind, "shop_inventory");
  assert.equal(receipt.successor.bound_actions.actions.some(action => action.verb === "open"), false);
  const close = receipt.successor.bound_actions.actions.find(action => action.verb === "close");
  const closed = await session.submit({ requestId: "close", expectedSnapshotId: receipt.successor.snapshot_id,
    boundActionId: close.bound_action_id });
  assert.equal(closed.delivery, "delivered");
  assert.equal(closed.successor.interaction.kind, "shop_room");
  assert.equal(snapshot.interaction.content.surface.shop_session_entity_id, receipt.successor.interaction.content.surface.shop_session_entity_id);
  assert.equal(snapshot.interaction.content.surface.shop_session_entity_id, closed.successor.interaction.content.surface.shop_session_entity_id);
  const proceed = closed.successor.bound_actions.actions.find(action => action.verb === "activate");
  assert.ok(proceed);
  assert.deepEqual(requests.map(request => request.action), ["open_shop_inventory", "close_shop_inventory"]);
});
