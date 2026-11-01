import assert from "node:assert/strict";
import test from "node:test";
import { createSession, reduce, modelView } from "@rsgcsg/sts2-connector-client/p1-global";
import { ManagedPlayerEnvironmentSession, projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { captureManagedPublicBasis } from "../src/managed-public-basis.mjs";
import { combatState } from "./fixtures/managed-fair-combat.mjs";

function sphere() {
  const state = { ...combatState(), decision: "crystal_sphere", run_ref: "run", room_ref: "room",
    event_ref: "event", sphere_ref: "sphere", board_width: 11, board_height: 11,
    remaining_divinations: 3, selected_tool: "Big", visible_items: [],
    cells: Array.from({ length: 121 }, (_, i) => ({ native_ref: `cell-${i}`, x: i % 11, y: Math.floor(i / 11),
      cleared: i === 0, ...(i === 0 ? { empty: true } : {}) })) };
  delete state.hand; delete state.combat_piles;
  state.context.current_map = { status: "captured", kind: "map", act_index: 0, current_position: null, visited: [],
    nodes: [{ native_ref: "map-node", col: 0, row: 0, point_type: "Event", state: "traveled", children: [{ col: 0, row: 1, point_type: "Boss" }] },
      { native_ref: "map-boss", col: 0, row: 1, point_type: "Boss", state: "untravelable", children: [] }] };
  return state;
}
function project(state) {
  return projectManagedCandidateDecision({ state, runtimeInstanceId: "sphere-test",
    environmentFingerprint: "sphere-component", sequence: 1 });
}
async function publicBasis(state) {
  const session = new ManagedPlayerEnvironmentSession({ process: { async request() { return state; } },
    runtimeInstanceId: "sphere-test", environmentFingerprint: "sphere-component" });
  await session.mount({ seed: "sphere-public-dto" });
  return captureManagedPublicBasis(session, { generationId: "generation", gameContinuityId: "continuity", ordinal: 1 }).basis;
}

test("sphere offers all cells with both tools, including a cleared empty cell", async () => {
  const result = project(sphere());
  assert.equal(result.snapshot.completeness.status, "complete");
  assert.equal(result.snapshot.bound_actions.actions.length, 242);
  assert.equal(result.bindings.size, 242);
  const requests = [...result.bindings.values()].map(binding => binding.raw_request);
  assert.deepEqual(requests.filter(request => request.args.cell_ref === "cell-0").map(request => request.args.tool), ["Small", "Big"]);
  const basis = await publicBasis(sphere());
  const current = reduce(createSession("sphere", "managed", "r1.9"), basis);
  assert.equal(current.complete, true, String(current.missing));
  assert.equal(current.observation.catalog.length, 242);
  assert.ok(current.observation.catalog.every(action => action.kind === "event.choose" && action.subject && action.target));
  assert.equal(reduce(createSession("old", "managed", "r1.8"), basis).complete, false);
  const dropped = structuredClone(basis);
  delete dropped.snapshot.interaction.content.surface.requires_profile_revision;
  assert.equal(reduce(createSession("old", "managed", "r1.8"), dropped).complete, false);
  assert.equal(modelView(current.observation).scene.content.surface.requires_profile_revision, undefined);
});

test("a wholly covered item is rejected before public referents or actions are allocated", () => {
  const state = sphere();
  state.visible_items.push({ native_ref: "hidden-secret", kind: "rare_potion", label: "Rare potion", x: 4, y: 4,
    width: 2, height: 2, cleared_cells: [], fully_revealed: false });
  const result = project(state);
  assert.equal(result.snapshot.completeness.status, "partial");
  assert.equal(result.bindings.size, 0);
  assert.ok(!JSON.stringify(result.snapshot).includes("hidden-secret"));
  assert.ok(!JSON.stringify(result.snapshot).includes("Rare potion"));
});

test("one exposed occupied cell publishes the approved full rectangle without future reward data", () => {
  const state = sphere();
  state.cells[12] = { ...state.cells[12], cleared: true, empty: false };
  state.visible_items.push({ native_ref: "seen-gold", kind: "small_gold", label: "Small gold", x: 1, y: 1,
    width: 2, height: 2, cleared_cells: [{ x: 1, y: 1 }], fully_revealed: false, future_reward: "never" });
  const result = project(state), surface = result.snapshot.interaction.content.surface;
  assert.equal(result.snapshot.completeness.status, "complete");
  assert.equal(surface.visible_items.length, 1);
  assert.deepEqual(surface.visible_items[0].cleared_cells, [{ x: 1, y: 1 }]);
  assert.equal(surface.visible_items[0].width, 2);
  assert.equal(surface.visible_items[0].fully_revealed, false);
  assert.equal(surface.visible_items[0].future_reward, undefined);
});

for (const fault of ["duplicate-cell", "missing-cell", "hidden-empty", "missing-visible-item", "false-revealed"]) {
  test(`malformed current sphere facts: ${fault}`, () => {
    const state = sphere();
    if (fault === "duplicate-cell") state.cells[1] = state.cells[0];
    if (fault === "missing-cell") state.cells.pop();
    if (fault === "hidden-empty") state.cells[1].empty = false;
    if (fault === "missing-visible-item") state.cells[12] = { ...state.cells[12], cleared: true, empty: false };
    if (fault === "false-revealed") {
      state.cells[12] = { ...state.cells[12], cleared: true, empty: false };
      state.visible_items.push({ native_ref: "seen", kind: "small_gold", label: "Small gold", x: 1, y: 1,
        width: 2, height: 2, cleared_cells: [{ x: 1, y: 1 }], fully_revealed: true });
    }
    assert.equal(project(state).bindings.size, 0);
  });
}
