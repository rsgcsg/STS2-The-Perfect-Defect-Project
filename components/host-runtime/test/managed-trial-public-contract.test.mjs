import assert from "node:assert/strict";
import test from "node:test";
import { combatState } from "./fixtures/managed-fair-combat.mjs";
import { projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";

function modal() {
  return { ...combatState(), decision: "abandon_confirm", stage: "awaiting_confirmation", complete: true,
    modal_kind: "abandon_run_confirmation", run_ref: "run", room_ref: "room", event_ref: "event",
    parent_option_ref: "parent", modal_ref: "modal", input_owner_ref: "modal", header: "Abandon?", body: "End this run?",
    controls: [{ control_ref: "yes", semantic_id: "yes", operation: "confirm_abandon", enabled: true, label: "Yes" },
      { control_ref: "no", semantic_id: "no", operation: "cancel_abandon", enabled: true, label: "No" }] };
}

for (const value of ["", "   ", null]) {
  test(`malformed abandon control reference ${JSON.stringify(value)} publishes no input`, () => {
    const state = modal();
    if (value === null) state.controls[0] = null;
    else state.controls[0].control_ref = value;
    const result = projectManagedCandidateDecision({ state, runtimeInstanceId: "trial-validation",
      environmentFingerprint: "trial-component", sequence: 1 });
    assert.equal(result.snapshot.completeness.status, "partial");
    assert.equal(result.snapshot.bound_actions.actions.length, 0);
    assert.equal(result.bindings.size, 0);
  });
}
