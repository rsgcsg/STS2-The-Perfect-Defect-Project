import assert from "node:assert/strict";
import test from "node:test";
import { completeSelector, createSession, reduce } from "@rsgcsg/sts2-connector-client/p1-global";
import { captureManagedPublicBasis } from "../src/managed-public-basis.mjs";
import { ManagedPlayerEnvironmentSession, projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { combatState } from "./fixtures/managed-fair-combat.mjs";

function bundle(preview = false) {
  const state = combatState(); delete state.hand; delete state.combat_piles;
  state.context.current_map = { status: "captured", kind: "map", act_index: 0,
    current_position: null, visited: [], nodes: [{ native_ref: "private-map-point", col: 0, row: 0,
      point_type: "Monster", state: "travelable", children: [{ col: 0, row: 1, point_type: "Boss" }] },
    { native_ref: "private-map-boss", col: 0, row: 1, point_type: "Boss", state: "untravelable", children: [] }] };
  const card = { ...state.player.deck[0], native_ref: "private-bundle-card" };
  return { ...state, decision: "bundle_select", bundle_public: true, bundle_missing: [],
    selector_ref: "private-bundle-task", stage: preview ? "preview" : "choosing", prompt: "Choose a Pack",
    selected_bundle_ref: preview ? "private-bundle" : null,
    selector_context: { kind: "event", event_id: "NEOW", name: "Neow", ancient: true, in_dialogue: false },
    bundles: [{ index: 0, native_ref: "private-bundle", cards: [card] }] };
}
const identity = { runtimeInstanceId: "bundle-runtime", environmentFingerprint: "bundle-environment", sequence: 1 };

test("bundle public cards and original owner bind preview, cancel and confirmation", async () => {
  const calls = [];
  const session = new ManagedPlayerEnvironmentSession({ ...identity, process: {
    async request(request) {
      if (request.cmd === "start_run") return bundle();
      calls.push(request);
      assert.deepEqual(request.args, { selector_ref: "private-bundle-task", bundle_ref: "private-bundle" });
      return bundle(request.action === "preview_bundle");
    }
  } });
  let snapshot = await session.mount({ seed: "bundle-projection-conformance" });
  for (const [verb, native, stage] of [["select", "preview_bundle", "preview"], ["cancel", "cancel_bundle_preview", "choosing"], ["select", "preview_bundle", "preview"], ["confirm", "confirm_bundle", "choosing"]]) {
    const action = snapshot.bound_actions.actions.find(action => action.verb === verb);
    const result = await session.submit({ requestId: `bundle-${calls.length}`, expectedSnapshotId: snapshot.snapshot_id, boundActionId: action.bound_action_id });
    assert.equal(result.delivery, "delivered"); assert.equal(calls.at(-1).action, native);
    snapshot = result.successor;
    assert.equal(snapshot.interaction.stage, stage);
    assert.equal(completeSelector(snapshot.interaction.content.surface), true);
  }
});

test("the same existing P1 reducer admits choose and finish without changing its rules", async () => {
  for (const preview of [false, true]) {
    const state = bundle(preview);
    const session = new ManagedPlayerEnvironmentSession({ ...identity, process: { async request() { return state; } } });
    await session.mount({ seed: "bundle-p1-conformance" });
    const full = captureManagedPublicBasis(session, { generationId: "bundle-generation", gameContinuityId: "bundle-game", ordinal: 1 });
    const result = reduce(createSession("bundle-conformance", "managed"), full.basis);
    assert.equal(result.complete, true, JSON.stringify(result.missing));
    assert.deepEqual(result.observation.catalog.map(choice => choice.kind), [preview ? "selector.finish" : "selector.choose"]);
  }
});

test("missing exact bundle or rendering evidence remains unsupported", () => {
  for (const damage of [state => delete state.bundle_public,
    state => delete state.bundles[0].native_ref,
    state => state.bundle_missing.push("native_card_description")]) {
    const state = bundle(); damage(state);
    assert.equal(projectManagedCandidateDecision({ state, ...identity }).snapshot.completeness.status, "partial");
  }
});
