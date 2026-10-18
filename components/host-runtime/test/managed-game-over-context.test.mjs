import assert from "node:assert/strict";
import test from "node:test";
import { gameOver } from "./fixtures/managed-game-over.mjs";
import { combatState } from "./fixtures/managed-fair-combat.mjs";
import { ManagedPlayerEnvironmentSession, projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { chooseManagedPlayerEnvironmentAction, managedTerminalOutcome } from "../src/managed-player-environment-probe.mjs";

const identity = { runtimeInstanceId: "terminal-owner-test", environmentFingerprint: "terminal-env", sequence: 1 };

test("terminal intro retains native outcome and disabled visible control with a real Continue binding", () => {
  const state = gameOver(combatState());
  const { snapshot, bindings } = projectManagedCandidateDecision({ state, ...identity });
  assert.equal(snapshot.completeness.status, "complete");
  assert.equal(snapshot.interaction.stage, "intro");
  assert.deepEqual(snapshot.interaction.content.context, { kind: "game_over", result: "loss", game_mode: "standard" });
  assert.equal(snapshot.interaction.content.surface.can_advance_summary, true);
  assert.equal(snapshot.interaction.content.surface.can_return, false);
  const control = snapshot.interaction.content.surface.other_controls[0];
  assert.equal(control.enabled, false);
  assert.equal(control.action_support, "visible_unsupported");
  assert.deepEqual(snapshot.referents.find((r) => r.role === "other_control").properties, control);
  const action = snapshot.bound_actions.actions[0];
  assert.equal(action.verb, "activate");
  assert.equal(action.subject_referent_id, null);
  assert.deepEqual(bindings.get(action.bound_action_id).raw_request, { cmd: "action", action: "advance_game_over_summary",
    args: { owner_ref: state.terminal_public.owner_ref, control_ref: state.terminal_public.continue_control_ref } });
  assert.equal(JSON.stringify(snapshot).includes("native-terminal-"), false);
  assert.equal(chooseManagedPlayerEnvironmentAction(snapshot), null, "episode probe stops at native outcome before navigation");
  assert.equal(managedTerminalOutcome(snapshot).victory, false);
});

test("Continue delivers the original owner/control and exposes the truthful partial summary successor", async () => {
  const state = gameOver(combatState());
  const requests = [];
  const process = { async request(raw) {
    if (raw.cmd === "start_run") return state;
    requests.push(raw);
    assert.deepEqual(raw, { cmd: "action", action: "advance_game_over_summary", args: {
      owner_ref: "native-terminal-owner", control_ref: "native-terminal-continue" } });
    return gameOver(combatState(), { stage: "summary" });
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  const before = await session.mount({ seed: "TERMINAL_OWNER" });
  const receipt = await session.submit({ requestId: "continue-once", expectedSnapshotId: before.snapshot_id,
    boundActionId: before.bound_actions.actions[0].bound_action_id });
  assert.equal(receipt.delivery, "delivered");
  const after = session.observe();
  assert.equal(after.interaction.stage, "summary");
  assert.equal(after.completeness.status, "partial");
  assert.ok(after.completeness.missing.includes("managed_game_over_return_navigation"));
  assert.deepEqual(after.bound_actions.actions, []);
  assert.equal(after.interaction.content.surface.can_return, false);
  assert.deepEqual(after.interaction.content.context, { kind: "game_over", result: "loss", game_mode: "standard",
    score: 10, floors_climbed: 1, ascension: 0 });
  const stale = await session.submit({ requestId: "stale-continue", expectedSnapshotId: before.snapshot_id,
    boundActionId: before.bound_actions.actions[0].bound_action_id });
  assert.equal(stale.delivery, "not_delivered");
  assert.equal(requests.length, 1);
});

test("old victory booleans or missing original end owner cannot advertise Continue", () => {
  for (const change of [
    (state) => { delete state.terminal_public; },
    (state) => { state.terminal_public.history_ref = null; },
    (state) => { state.terminal_public.player_ref = "foreign-native-player"; }
  ]) {
    const state = gameOver(combatState()); change(state);
    const { snapshot } = projectManagedCandidateDecision({ state, ...identity });
    assert.equal(snapshot.completeness.status, "partial");
    assert.deepEqual(snapshot.bound_actions.actions, []);
    assert.ok(snapshot.completeness.missing.includes("native_game_over_original_end_and_presentation_owner"));
  }
});
