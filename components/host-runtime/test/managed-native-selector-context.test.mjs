import assert from "node:assert/strict";
import test from "node:test";
import { decodePlayerSnapshot, nativeCardSelectionProblems } from "@rsgcsg/sts2-connector-client";
import { combatState, renamePrivateCounters } from "./fixtures/managed-fair-combat.mjs";
import { projectManagedCandidateDecision, ManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { projectManagedTextMenuV1 } from "../src/managed-text-menu-map.mjs";

const identity = { runtimeInstanceId: "selector-runtime", environmentFingerprint: "selector-environment", sequence: 1 };
// Synthetic exact producer DTO shape. Native callback capture, rendering, original
// object revalidation and effects are separately gated by exact native fixtures.
function selector({ choose = false, skip = false, manual = false, selected = false } = {}) {
  const state = combatState();
  const card = (ref, name = "Strike") => ({ native_ref: ref, id: name.toUpperCase(), name,
    cost: "1", type: "Attack", rarity: "Basic", description: "Deal 6 damage.", target_type: "AnyEnemy",
    is_upgraded: false, keywords: [], card_previews: [], render_complete: true,
    is_selected: false, is_selectable: !selected, is_deselectable: false });
  const cards = [card("native-card-a"), card("native-card-b")];
  if (selected) Object.assign(cards[0], { is_selected: true, is_deselectable: manual });
  return { ...state, decision: "native_card_select", selector_ref: "native-selector",
    origin: choose ? "choose_card" : "combat_pile", stage: "choosing", prompt: "Choose a card.",
    min_select: choose && skip ? 0 : 1, max_select: 1, selected_refs: selected ? [cards[0].native_ref] : [],
    cancelable: false, can_skip: skip, require_manual_confirmation: manual, can_confirm: manual && selected,
    display_pile: choose ? "none" : "discard", native_selector_complete: true, native_selector_missing: [], cards,
    combat_context: { round: 1, turn_owner: "player", player_ref: state.player.native_ref,
      hp: 80, max_hp: 80, block: 3, energy: 2, max_energy: 3, stars: 0,
      draw_pile_count: 2, discard_pile_count: 2, exhaust_pile_count: 0, orb_slots: 0,
      statuses: [], orbs: [], companions: [], enemies: [{ native_ref: "native-enemy-current", id: "MAWLER",
        name: "Mawler", hp: 7, max_hp: 20, block: 0, health_bar_visible: true, health_display: "Normal",
        statuses: [], intents: [] }] } };
}
function project(state) { return projectManagedCandidateDecision({ state, ...identity }); }

test("choose and combat publish every original occurrence, current HUD and strict SDK/text-menu catalog", () => {
  for (const state of [selector(), selector({ choose: true, skip: true }), selector({ choose: true })]) {
    const p = project(state); const s = decodePlayerSnapshot(p.snapshot).data;
    assert.equal(s.completeness.status, "complete");
    assert.equal(s.bound_actions.status, "complete");
    assert.deepEqual(nativeCardSelectionProblems(s), []);
    assert.equal(s.referents.filter((r) => r.role === "card").length, 2);
    assert.equal(s.interaction.content.context.combat.player.block, 3);
    assert.equal(s.interaction.content.context.combat.enemies[0].hp, 7);
    assert.equal(s.bound_actions.actions.length, state.can_skip ? 3 : 2);
    assert.equal(new Set(s.bound_actions.actions.filter((a) => a.verb === "select").map((a) => a.subject_referent_id)).size, 2);
    assert.equal(projectManagedTextMenuV1(s).menu_actions.status, "complete");
    assert.equal(JSON.stringify(s).includes("native-"), false);
    assert.ok([...p.bindings.values()].every((b) => b.raw_request.args.selector_ref === state.selector_ref));
  }
});

test("native manual one-option input remains select then exact confirm and deselect", () => {
  const state = selector({ manual: true }); state.cards = state.cards.slice(0, 1);
  const first = project(state).snapshot;
  assert.deepEqual(first.bound_actions.actions.map((a) => a.verb), ["select"]);
  const next = selector({ manual: true, selected: true }); next.cards = next.cards.slice(0, 1);
  const p = project(next);
  assert.deepEqual(p.snapshot.bound_actions.actions.map((a) => a.verb).sort(), ["confirm", "deselect"]);
  assert.deepEqual([...p.bindings.values()].find((b) => b.raw_request.action === "confirm_native_selection").raw_request.args,
    { selector_ref: "native-selector", selected_refs: "native-card-a" });
});

test("missing exact scope, renderer, bounds, flags or HUD fences the entire candidate catalog", () => {
  const mutations = [s => { s.origin = "deck_enchant"; }, s => { s.native_selector_complete = false; },
    s => { s.native_selector_missing = ["native_card_render_failed"]; }, s => { s.selector_ref = null; },
    s => { s.prompt = null; }, s => { s.cards[0].description = "Deal {Damage:diff()} damage."; },
    s => { s.cards[0].render_complete = false; }, s => { s.cards[0].card_previews = null; },
    s => { s.cards[1].native_ref = s.cards[0].native_ref; }, s => { s.can_skip = true; },
    s => { s.cancelable = true; }, s => { s.max_select = 2; }, s => { s.combat_context = null; },
    s => { s.combat_context.player_ref = "foreign-player"; }, s => { s.cards[0].is_selected = true; },
    s => { s.cards[0].is_selectable = false; }, s => { s.combat_context.enemies[0].intents = [{ type: "Attack" }]; }];
  for (const mutate of mutations) {
    const state = selector(); mutate(state); const p = project(state);
    assert.equal(p.snapshot.completeness.status, "partial");
    assert.equal(p.snapshot.bound_actions.status, "unavailable");
    assert.deepEqual(p.snapshot.bound_actions.actions, []);
    assert.equal(p.bindings.size, 0);
    assert.equal(projectManagedTextMenuV1(p.snapshot).menu_actions.status, "unavailable");
  }
});

test("SDK rejects removed candidate details/catalog controls and unknown-purpose complete leaves", () => {
  for (const mutate of [s => { s.interaction.content.surface.cards[0].description = null; },
    s => { s.interaction.content.context.combat = null; },
    s => { s.interaction.content.surface.selector_kind = "unknown"; },
    s => { s.interaction.content.surface.cards.pop(); },
    s => { s.bound_actions.actions.pop(); s.bound_actions.materialized_count--; s.bound_actions.total_count--; }]) {
    const snapshot = structuredClone(project(selector({ choose: true, skip: true })).snapshot);
    mutate(snapshot);
    assert.throws(() => decodePlayerSnapshot(snapshot), /native card selection/u);
    assert.equal(projectManagedTextMenuV1(snapshot).menu_actions.status, "unavailable");
  }
});

test("fixed revealed Draw candidates retain public projection across private counter/order changes", () => {
  const state = selector(); state.display_pile = "draw";
  const other = renamePrivateCounters(structuredClone(state));
  other.hidden_rng = "other"; other.internal_counter = 999;
  // The producer sorts already revealed cards by public fields before assigning
  // aliases; identical occurrences remain indistinguishable in presentation.
  other.cards.reverse();
  const left = project(state).snapshot; const right = project(other).snapshot;
  // Wall-clock observation metadata is independent of private game projection.
  right.observed_at = left.observed_at;
  assert.deepEqual(left, right);
});

test("native child actions retain unknown-no-retry and never re-dispatch a parent", async () => {
  const state = selector({ choose: true, skip: true }); const calls = [];
  const process = { async request(request) { calls.push(request);
    if (request.cmd === "start_run") return state;
    throw new Error("uncertain native child transport"); } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  const snapshot = await session.mount({ seed: "SELECTOR-TRANSPORT-FIXTURE" });
  const action = snapshot.bound_actions.actions.find((a) => a.verb === "select");
  const receipt = await session.submit({ requestId: "child-once", expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: action.bound_action_id, timeoutMs: 1000 });
  assert.equal(receipt.delivery, "unknown"); assert.equal(receipt.retry.allowed, false);
  assert.equal(calls.length, 2); assert.equal(calls[1].action, "select_native_card");
  await session.submit({ requestId: "child-again", expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: action.bound_action_id, timeoutMs: 1000 });
  assert.equal(calls.length, 2);
});

test("Managed selector profile does not redefine Reference/legacy card-selection decoding", () => {
  const snapshot = structuredClone(project(selector()).snapshot);
  delete snapshot.interaction.content.surface.selector_profile;
  delete snapshot.interaction.content.surface.selector_kind;
  assert.doesNotThrow(() => decodePlayerSnapshot(snapshot));
  assert.equal(projectManagedTextMenuV1(snapshot).menu_actions.status, "unavailable");
});
