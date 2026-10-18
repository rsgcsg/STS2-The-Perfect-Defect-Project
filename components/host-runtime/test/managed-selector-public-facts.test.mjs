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
  assert.equal(projection.snapshot.bound_actions.actions.find(action => action.verb === "select").label, `Select ${surface.cards[0].name}`);
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
  for (const family of ["deck_upgrade", "deck_card", "deck_enchant", "deck_transform"])
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

test("transformation exposes exact original choices without evaluating a replacement preview", () => {
  const projection = projectManagedCandidateDecision({ state: selector({ family: "deck_transform" }), ...identity });
  const surface = projection.snapshot.interaction.content.surface;
  assert.equal(projection.snapshot.completeness.status, "complete");
  assert.equal(surface.kind, "deck_transform_selection");
  assert.equal(surface.selector_request.family, "deck_transform");
  assert.equal(surface.selector_request.selection_model, "native_selected_original_set");
  assert.equal(surface.selector_request.add_transition, "native_preview_at_max");
  assert.ok(surface.preview_options.every(option => option.after === null && option.after_availability === "not_applicable"));
  assert.deepEqual(surface.cards, surface.preview_options.map(option => option.before));
  const reads = [...projection.reads.values()].filter(read => read.content.display_owner_entity_id === surface.screen_entity_id);
  assert.deepEqual(reads.map(read => read.content.display_context), ["selector_original:deck_transform"]);
});

test("a custom native transformation keeps original displays but reports its unqualified replacement information", () => {
  const state = selector({ family: "deck_transform" });
  const missing = ["custom_transform_preview_unqualified"];
  Object.assign(state.selector_public.selector_request, { preview_information_complete: false,
    preview_projection_qualified: false, missing });
  state.selector_public.missing = missing;
  const projection = projectManagedCandidateDecision({ state, ...identity });
  const surface = projection.snapshot.interaction.content.surface;
  assert.equal(projection.snapshot.completeness.status, "partial");
  assert.deepEqual(surface.selector_request.missing, missing);
  assert.equal(surface.selector_request.mechanics_qualified, true);
  assert.equal(surface.selector_request.preview_information_complete, false);
  assert.equal(surface.selector_request.preview_projection_qualified, false);
  assert.deepEqual(surface.cards, surface.preview_options.map(option => option.before));
  assert.ok(surface.preview_options.every(option => option.after === null));
});

test("simple grid keeps the original eight-card roster after one of two selections and dispatches the remaining original", async () => {
  const state = selector({ family: "simple" });
  state.decision = "simple_card_select";
  state.origin = "simple";
  state.min_select = state.max_select = 2;
  const original = state.selector_public.preview_options[0];
  state.selector_public.preview_options = Array.from({ length: 8 }, (_, index) => {
    const before = { ...original.before, entity_id: `original-${index}`, definition_id: `CARD_${index}`,
      name: `Card ${index}`, is_selected: index === 2 };
    return { ...original, subject_entity_id: before.entity_id, before, after: null, after_availability: "not_applicable" };
  });
  state.cards = state.selector_public.preview_options.map(({ before }, index) => ({ ...before,
    index, native_ref: before.entity_id, is_selectable: index !== 2, is_deselectable: index === 2 }));
  state.selected_refs = ["original-2"];
  Object.assign(state.selector_public.selector_request, { min_select: 2, max_select: 2,
    selected_entity_ids: state.selected_refs, add_transition: "native_completion_at_max" });
  const dispatched = [];
  const session = new ManagedPlayerEnvironmentSession({ ...identity, process: { async request(request) {
    if (request.cmd === "start_run") return state;
    dispatched.push(request);
    return selector();
  } } });
  const snapshot = await session.mount({ seed: "synthetic-simple-grid-eight-choose-two" });
  const surface = snapshot.interaction.content.surface;
  assert.equal(snapshot.completeness.status, "complete");
  assert.equal(snapshot.interaction.kind, "native_simple_card_selection");
  assert.equal(surface.cards.length, 8);
  assert.deepEqual(surface.cards, surface.preview_options.map(option => option.before));
  assert.deepEqual(surface.selected_card_entity_ids, [surface.cards[2].entity_id]);
  assert.deepEqual(surface.selector_request.selected_entity_ids, surface.selected_card_entity_ids);
  assert.equal(surface.selector_request.add_transition, "native_completion_at_max");
  assert.equal(snapshot.bound_actions.actions.filter(action => action.verb === "select").length, 7);
  assert.equal(snapshot.bound_actions.actions.filter(action => action.verb === "deselect").length, 1);
  assert.equal(projectManagedTextMenuV1(snapshot).menu_actions.status, "complete");
  const choice = snapshot.bound_actions.actions.find(action => action.verb === "select"
    && action.subject_referent_id === surface.cards[5].entity_id);
  const receipt = await session.submit({ requestId: "simple-grid-select-second-original", expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: choice.bound_action_id });
  assert.equal(receipt.delivery, "delivered");
  assert.equal(dispatched[0].action, "select_native_card");
  assert.deepEqual(dispatched[0].args, { selector_ref: "private-selector-owner", card_ref: "original-5" });
});

test("bounded combat pile projects the current original task and native hand legality without inventing holder count", () => {
  const state = selector({ family: "combat_pile" });
  state.combat_context.hand[0].can_play = true;
  state.combat_context.hand[1].can_play = false;
  state.combat_context.hand[1].unplayable_reason = "NotEnoughEnergy";
  const projection = projectManagedCandidateDecision({ state, ...identity });
  const surface = projection.snapshot.interaction.content.surface;
  assert.equal(projection.snapshot.completeness.status, "complete");
  assert.equal(projection.snapshot.interaction.kind, "native_combat_pile_selection");
  assert.equal(surface.selector_request.family, "combat_pile");
  assert.equal(surface.selector_request.displayed_holder_count, null);
  assert.deepEqual(surface.cards, surface.preview_options.map(option => option.before));
  assert.deepEqual(projection.snapshot.interaction.content.context.combat.player.hand.map(card => card.can_play), [true, false]);
  assert.equal(projection.snapshot.interaction.content.context.combat.player.hand[1].unplayable_reason, "NotEnoughEnergy");
  assert.equal([...projection.bindings.values()][0].raw_request.action, "select_native_card");
  assert.deepEqual([...projection.bindings.values()][0].raw_request.args,
    { selector_ref: "private-selector-owner", card_ref: "private-card-original" });
});

test("native selector retains the current semantic parent and canonical combat HUD has one HP owner", () => {
  const event = selector({ family: "deck_enchant" });
  event.selector_context = { kind: "event", event_id: "SAPPHIRE_SEED", name: "Sapphire Seed",
    ancient: false, in_dialogue: false, body: "[gold]Enchant[/gold] a card.", private_owner: "private-do-not-publish" };
  const projection = projectManagedCandidateDecision({ state: event, ...identity });
  assert.deepEqual(projection.snapshot.interaction.content.context, { kind: "event", event_id: "SAPPHIRE_SEED",
    name: "Sapphire Seed", ancient: false, in_dialogue: false, body: "Enchant a card." });
  assert.equal(JSON.stringify(projection.snapshot).includes("private-do-not-publish"), false);
  const rest = selector();rest.selector_context = { kind: "rest" };
  assert.deepEqual(projectManagedCandidateDecision({ state: rest, ...identity }).snapshot.interaction.content.context, { kind: "rest" });
  const combat = selector({ family: "combat_pile" });
  const current = projectManagedCandidateDecision({ state: combat, ...identity }).snapshot;
  assert.equal(Object.hasOwn(current.interaction.content.context.combat.player, "hp"), false);
  assert.equal(Object.hasOwn(current.interaction.content.context.combat.player, "max_hp"), false);
  assert.equal(current.persistent.content.player.hp, combat.player.hp);
  assert.equal(current.persistent.content.player.max_hp, combat.player.max_hp);
});
