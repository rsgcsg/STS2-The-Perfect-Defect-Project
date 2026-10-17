import assert from "node:assert/strict";
import test from "node:test";
import { ManagedPlayerEnvironmentSession, projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { projectManagedTextMenuV1 } from "../src/managed-text-menu-map.mjs";
import { publicSelector as selector } from "./fixtures/managed-selector-public.mjs";

const identity = { runtimeInstanceId: "selector-public", environmentFingerprint: "test", sequence: 1 };


test("selector facts keep full original/after displays and same-snapshot details, with original operands private", () => {
  const state = selector();
  const projection = projectManagedCandidateDecision({ state, ...identity });
  const surface = projection.snapshot.interaction.content.surface;
  assert.equal(projection.snapshot.completeness.status, "complete");
  assert.deepEqual(surface.cards, surface.preview_options.map(option => option.before));
  assert.equal(surface.cards[0].entity_id, surface.preview_options[0].after.entity_id);
  assert.equal(surface.cards[0].is_upgraded, false);
  assert.equal(surface.preview_options[0].after.is_upgraded, true);
  const details = [...projection.reads.values()].filter(read => read.content.display_owner_entity_id === surface.screen_entity_id);
  assert.deepEqual(details.map(read => read.content.display_context), ["selector_original:deck_upgrade", "native_deck_upgrade_after"]);
  assert.equal(new Set(details.map(read => read.descriptor.read_id)).size, 2);
  assert.ok(details.every(read => read.descriptor.target_referent_id === surface.cards[0].entity_id));
  assert.equal(JSON.stringify({ snapshot: projection.snapshot, reads: [...projection.reads.values()] }).includes("private-"), false);
  assert.equal([...projection.bindings.values()][0].raw_request.args.card_ref, "private-card-original");
});

test("selector controls are current subject-bound operations and confirm binds the original selected set", () => {
  const projection = projectManagedCandidateDecision({ state: selector({ preview: true }), ...identity });
  const surface = projection.snapshot.interaction.content.surface;
  for (const action of projection.snapshot.bound_actions.actions) {
    const control = surface.current_controls.find(control => control.entity_id === action.subject_referent_id);
    assert.ok(control);
    assert.equal(projection.snapshot.referents.find(ref => ref.referent_id === control.entity_id).role, "selector_control");
  }
  assert.deepEqual([...projection.bindings.values()].find(value => value.action.verb === "confirm").raw_request.args,
    { selector_ref: "private-selector-owner", selected_refs: "private-card-original" });
  const generic = projectManagedCandidateDecision({ state: selector({ family: "deck_card" }), ...identity });
  assert.equal(generic.snapshot.interaction.kind, "native_deck_card_selection");
  assert.equal(projectManagedTextMenuV1(generic.snapshot).menu_actions.status, "complete");
});

test("same owner with a new original Task rotates request identity without relabeling the cards", () => {
  const aliases = new Map();
  const state = selector();
  const first = projectManagedCandidateDecision({ state, ...identity, publicEntityAliases: aliases });
  const next = structuredClone(state);
  next.selector_public.selector_request.entity_id = "private-new-task";
  const second = projectManagedCandidateDecision({ state: next, ...identity, sequence: 2, publicEntityAliases: aliases });
  assert.equal(first.snapshot.interaction.content.surface.cards[0].entity_id, second.snapshot.interaction.content.surface.cards[0].entity_id);
  assert.notEqual(first.snapshot.interaction.content.surface.request_entity_id, second.snapshot.interaction.content.surface.request_entity_id);
});

test("every deck family dispatches either public confirm spelling to its owning raw handler", async () => {
  for (const family of ["deck_upgrade", "deck_card", "deck_enchant"])
    for (const operation of ["confirm", "confirm_selection"]) {
      const state = selector({ preview: true, family });
      state.selector_public.current_controls = [{ operation }];
      const expected = family === "deck_upgrade" ? "confirm_upgrade_selection" : "confirm_deck_selection";
      const dispatched = [];
      const handlers = {
        [expected](request) {
          assert.deepEqual(request.args, { selector_ref: "private-selector-owner", selected_refs: "private-card-original" });
          dispatched.push(request.action);
          return selector({ family });
        }
      };
      const session = new ManagedPlayerEnvironmentSession({ ...identity, process: {
        async request(request) {
          if (request.cmd === "start_run") return state;
          assert.equal(request.cmd, "action");
          assert.ok(handlers[request.action], `${family}/${operation}: wrong owning raw handler ${request.action}`);
          return handlers[request.action](request);
        }
      } });
      const snapshot = await session.mount({ seed: "synthetic-selector-dispatch" });
      const confirm = snapshot.bound_actions.actions.find(action => action.verb === "confirm");
      const receipt = await session.submit({ requestId: `${family}/${operation}`, expectedSnapshotId: snapshot.snapshot_id,
        boundActionId: confirm.bound_action_id });
      assert.equal(receipt.delivery, "delivered", `${family}/${operation}`);
      assert.deepEqual(dispatched, [expected]);
    }
});

test("missing original or after facts keep public displays while fencing complete-basis admission", () => {
  for (const field of ["before", "after"]) {
    const state = selector();
    const option = state.selector_public.preview_options[0];
    option[field].description = null;
    option[`${field}_missing`] = ["native_card_render_failed"];
    option.missing = ["native_card_render_failed"];
    state.selector_public.missing = ["native_selector_candidate_presentation_incomplete", "native_card_render_failed"];
    Object.assign(state.selector_public.selector_request, { preview_information_complete: false, preview_projection_qualified: false,
      missing: state.selector_public.missing });
    const projection = projectManagedCandidateDecision({ state, ...identity });
    assert.equal(projection.snapshot.completeness.status, "partial");
    assert.equal(projection.snapshot.interaction.content.surface.preview_options.length, 1);
    const reads = [...projection.reads.values()].filter(read => read.content.display_owner_entity_id != null);
    assert.equal(reads.find(read => read.content.display_context === (field === "before" ? "selector_original:deck_upgrade" : "native_deck_upgrade_after")).completeness.status, "partial");
    assert.equal(reads.filter(read => read.completeness.status === "partial").length, 1);
  }
  const stale = selector();
  stale.selector_public.selector_request.owner_entity_id = "foreign-owner";
  const projection = projectManagedCandidateDecision({ state: stale, ...identity });
  assert.equal(projection.snapshot.bound_actions.status, "unavailable");
  assert.equal(projection.bindings.size, 0);
});
