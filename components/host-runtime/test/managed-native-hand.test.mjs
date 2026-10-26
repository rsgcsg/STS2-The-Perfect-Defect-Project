import assert from "node:assert/strict";
import test from "node:test";
import { projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { selector } from "./fixtures/managed-native-selector.mjs";

function hand(selectedCount = 0) {
  const state = selector();
  const original = [...state.cards, ...state.cards.map((card, index) => ({ ...card, native_ref: `more-hand-${index}` }))];
  for (let index = 0; index < original.length; index++) Object.assign(original[index], {
    is_selected: index < selectedCount, is_selectable: index >= selectedCount, is_deselectable: index < selectedCount });
  return { ...state, decision: "native_hand_select", stage: "ready", selection_mode: "simple_select",
    min_select: 0, max_select: 3, selected_refs: original.slice(0, selectedCount).map(card => card.native_ref),
    cards: [...original.slice(selectedCount), ...original.slice(0, selectedCount)],
    require_manual_confirmation: true, can_confirm: true, is_peeking: false, can_close_peek: false,
    native_hand_complete: true, native_hand_missing: [],
    combat_context: { ...state.combat_context, hand: original.map(card => ({ ...card, is_selected: false })) } };
}
const project = state => projectManagedCandidateDecision({ state, sequence: 1, runtimeInstanceId: "hand", environmentFingerprint: "hand" });

test("original hand staging shares card identity with HUD and commits only the exact native selected order", () => {
  for (const count of [0, 1, 3]) {
    const state = hand(count); const p = project(state); const s = p.snapshot;
    assert.equal(s.completeness.status, "complete");
    const surface = s.interaction.content.surface;
    assert.equal(surface.kind, "combat_hand_card_selection");
    assert.equal(surface.selected_count, count);
    assert.equal(surface.cards.filter(card => card.is_selected).length, count);
    assert.ok(surface.cards.every(card => !Object.hasOwn(card, "target_type") && !Object.hasOwn(card, "can_play")));
    const hud = s.interaction.content.context.player;
    assert.equal(hud.hand.length, 4);
    assert.deepEqual(new Set(hud.hand.map(card => card.entity_id)), new Set(surface.cards.map(card => card.entity_id)));
    const confirm = [...p.bindings.values()].find(value => value.raw_request.action === "confirm_native_selection");
    assert.equal(confirm.raw_request.args.selected_refs, state.selected_refs.join(","));
    const select = s.bound_actions.actions.filter(action => action.verb === "select");
    assert.equal(select.length, 4 - count);
    assert.ok(select.every(action => action.label.startsWith(count === 3 ? "Replace current selection with " : "Select ")));
    const selectedRead = [...p.reads.values()].filter(read => read.content?.is_selected === true);
    if (count) assert.ok(selectedRead.length >= count);
  }
});

test("missing hand scope, object membership, render or current HUD removes the complete action catalog", () => {
  for (const mutate of [s => { s.native_hand_complete = false; }, s => { s.selected_refs = ["foreign"]; },
    s => { s.cards[0].is_selected = true; }, s => { s.cards[1].native_ref = s.cards[0].native_ref; },
    s => { s.combat_context = null; }, s => { s.cards[0].public_details_missing = ["upgrade_preview"]; },
    s => { s.cards[0].description = "{Damage}"; }, s => { s.can_confirm = false; }, s => { s.max_select = -1; }]) {
    const state = hand(); mutate(state); const p = project(state);
    assert.equal(p.snapshot.completeness.status, "partial");
    assert.equal(p.snapshot.bound_actions.actions.length, 0);
    assert.equal(p.bindings.size, 0);
  }
});
