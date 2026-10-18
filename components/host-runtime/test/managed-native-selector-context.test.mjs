import assert from "node:assert/strict";
import test from "node:test";
import { decodePlayerSnapshot as decodeBaseSnapshot } from "@rsgcsg/sts2-connector-client";
import { nativeCardSelectionProblems } from "../src/native-card-selection.generated.mjs";
import { renamePrivateCounters } from "./fixtures/managed-fair-combat.mjs";
import { projectManagedCandidateDecision, ManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { selector } from "./fixtures/managed-native-selector.mjs";
import { projectManagedTextMenuV1 } from "../src/managed-text-menu-map.mjs";

// The installed public SDK validates the base wire. This Host validates its own
// additive selector profile with the package-contained canonical artifact.
function decodePlayerSnapshot(value) {
  const result = decodeBaseSnapshot(value);
  const problems = nativeCardSelectionProblems(result.data);
  if (problems.length) throw new Error(problems.join("; "));
  return result;
}

const identity = { runtimeInstanceId: "selector-runtime", environmentFingerprint: "selector-environment", sequence: 1 };
// Synthetic exact producer DTO shape. Native callback capture, rendering, original
// object revalidation and effects are separately gated by exact native fixtures.

function project(state) { return projectManagedCandidateDecision({ state, ...identity }); }

test("choose and combat publish every original occurrence, current HUD and strict Host/text-menu catalog", () => {
  for (const state of [selector(), selector({ choose: true, skip: true }), selector({ choose: true })]) {
    const p = project(state); const s = decodePlayerSnapshot(p.snapshot).data;
    assert.equal(s.completeness.status, "complete");
    assert.equal(s.bound_actions.status, "complete");
    assert.deepEqual(nativeCardSelectionProblems(s), []);
    const currentCardIds = s.interaction.content.surface.cards.map(card => card.entity_id);
    assert.equal(s.referents.filter(r => r.role === "card" && currentCardIds.includes(r.referent_id)).length, 2);
    assert.equal(s.interaction.content.context.combat.player.block, 3);
    assert.equal(s.interaction.content.context.combat.enemies[0].hp, 7);
    assert.deepEqual(s.interaction.content.context.combat.player.hand.map((card) => card.definition_id), ["STRIKE", "DEFEND"]);
    const piles = [...p.reads.values()].find((read) => read.descriptor.kind === "combat_piles");
    assert.deepEqual(piles.content.zones.map((zone) => [zone.zone, zone.ordering_semantics, zone.cards.length]), [
      ["draw", "unordered_multiset", 2], ["discard", "native_visible_order", 2], ["exhaust", "native_visible_order", 0]
    ]);
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
    s => { s.cards[0].is_selectable = false; }, s => { s.combat_context.enemies[0].intents = [{ type: "Attack" }]; },
    s => { delete s.combat_context.hand; }, s => { s.combat_context.hand[0].render_complete = false; },
    s => { s.combat_context.hand[1].native_ref = s.combat_context.hand[0].native_ref; },
    s => { delete s.combat_piles; }, s => { s.combat_piles.pop(); },
    s => { s.combat_piles[1].ordering_semantics = "unordered_multiset"; },
    s => { s.combat_piles[0].cards[0].description = "Deal {Damage} damage."; },
    s => { s.combat_context.draw_pile_count++; },
    s => { delete s.combat_context.encounter_type; }, s => { delete s.combat_context.is_play_phase; },
    s => { delete s.combat_context.potion_states; },
    s => { delete s.combat_context.enemies[0].hp_numbers_available; },
    s => { s.combat_context.enemies[0].combat_id = "opaque-id"; },
    s => { s.combat_context.enemies[0].hp_display_mode = "unknown"; }];
  for (const mutate of mutations) {
    const state = selector(); mutate(state); const p = project(state);
    assert.equal(p.snapshot.completeness.status, "partial");
    assert.equal(p.snapshot.bound_actions.status, "unavailable");
    assert.deepEqual(p.snapshot.bound_actions.actions, []);
    assert.equal(p.bindings.size, 0);
    assert.equal(projectManagedTextMenuV1(p.snapshot).menu_actions.status, "unavailable");
  }
});

test("Host rejects removed candidate details/catalog controls and unknown-purpose complete leaves", () => {
  for (const mutate of [s => { s.interaction.content.surface.cards[0].description = null; },
    s => { s.interaction.content.context.combat = null; },
    s => { s.interaction.content.surface.selector_kind = "unknown"; },
    s => { s.interaction.content.surface.cards.pop(); },
    s => { s.bound_actions.actions.pop(); s.bound_actions.materialized_count--; s.bound_actions.total_count--; }]) {
    const snapshot = structuredClone(project(selector()).snapshot);
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
  snapshot.interaction.content.surface.selector_kind = "reference-native-selector";
  assert.doesNotThrow(() => decodePlayerSnapshot(snapshot));
  assert.equal(projectManagedTextMenuV1(snapshot).menu_actions.status, "unavailable");
});


test("marked selector Host and menu require the whole current HUD and same persistent player", () => {
  const baseline = project(selector()).snapshot;
  const hud = baseline.interaction.content.context.combat;
  hud.player.statuses = [{ definition_id: "STRENGTH", name: "Strength", amount: 1, type: "Buff", description: "Deal more damage." }];
  hud.player.orbs = [{ entity_id: "orb-public", definition_id: "LIGHTNING", name: "Lightning", passive: 3, evoke: 8, keywords: [] }];
  hud.player.companions = [{ entity_id: "pet-public", definition_id: "OSTY", alive: true,
    hp: null, max_hp: null, block: 0, health_bar_visible: false, health_display: "Normal", statuses: [] }];
  assert.doesNotThrow(() => decodePlayerSnapshot(baseline));
  const mutations = [
    s => { delete s.player.hp; }, s => { delete s.player.block; delete s.player.energy; },
    s => { s.player.player_entity_id = "foreign-unbound-player"; },
    ...["max_hp", "max_energy", "stars", "draw_pile_count", "discard_pile_count", "exhaust_pile_count", "orb_slots"].map(key => s => { delete s.player[key]; }),
    s => { s.turn_owner = null; }, s => { s.round = 0; },
    s => { s.player.statuses[0].amount = null; }, s => { s.player.statuses[0].description = null; },
    s => { s.player.orbs[0].passive = null; }, s => { s.player.orbs[0].keywords = null; },
    s => { s.player.companions[0].alive = null; }, s => { s.player.companions[0].hp = 5; },
    s => { s.enemies[0].hp = null; }, s => { s.enemies[0].health_display = "unknown"; },
    s => { s.enemies[0].intents = [{ type: "Attack" }]; }, s => { s.enemies[0].statuses = null; }
  ];
  for (const mutate of mutations) {
    const snapshot = structuredClone(baseline); mutate(snapshot.interaction.content.context.combat);
    assert.throws(() => decodePlayerSnapshot(snapshot), /native card selection/u);
    assert.ok(nativeCardSelectionProblems(snapshot).length > 0);
    assert.equal(projectManagedTextMenuV1(snapshot).menu_actions.status, "unavailable");
  }
  // Native hidden-health projection is explicit null, not a missing number.
  const hidden = structuredClone(baseline);
  Object.assign(hidden.interaction.content.context.combat.enemies[0], { health_bar_visible: false, hp: null, max_hp: null });
  assert.doesNotThrow(() => decodePlayerSnapshot(hidden));
  for (const mode of ["InfiniteWithNumbers", "InfiniteWithoutNumbers"]) {
    const infinite = structuredClone(baseline);
    Object.assign(infinite.interaction.content.context.combat.enemies[0], { health_display: mode,
      ...(mode === "InfiniteWithoutNumbers" ? { hp: null, max_hp: null } : {}) });
    assert.doesNotThrow(() => decodePlayerSnapshot(infinite));
  }
});

test("complete marked selector cannot erase the entire expected catalog", () => {
  const snapshot = structuredClone(project(selector()).snapshot);
  Object.assign(snapshot.bound_actions, { actions: [], materialized_count: 0, total_count: 0 });
  snapshot.status = "observed"; snapshot.interaction.capabilities = [];
  assert.throws(() => decodePlayerSnapshot(snapshot), /native card selection/u);
  assert.ok(nativeCardSelectionProblems(snapshot).length > 0);
  // Unavailable marked diagnostics, including noncombat selectors, remain readable.
  snapshot.bound_actions.status = "unavailable"; snapshot.status = "visible_unsupported";
  snapshot.completeness.status = "partial";
  snapshot.interaction.content.context.combat = null;
  assert.doesNotThrow(() => decodePlayerSnapshot(snapshot));
  assert.deepEqual(nativeCardSelectionProblems(snapshot), []);
  delete snapshot.interaction.content.surface.selector_profile;
  snapshot.bound_actions.status = "complete"; snapshot.status = "observed";
  assert.doesNotThrow(() => decodePlayerSnapshot(snapshot));
});


test("empty intent label is a native display value with required hover meaning", () => {
  const state = selector();
  state.combat_context.enemies[0].intents = [{ type: "Debuff", label: "", title: "Strategic",
    description: "This enemy intends to apply a Debuff to you." }];
  const valid = project(state).snapshot;
  assert.equal(valid.bound_actions.status, "complete");
  assert.equal(valid.interaction.content.context.combat.enemies[0].intents[0].label, "");
  assert.equal(projectManagedTextMenuV1(valid).menu_actions.status, "complete");
  for (const mutate of [s => { delete s.label; }, s => { s.label = null; },
    s => { s.label = 0; }, s => { s.label = "{Damage}"; },
    s => { s.title = ""; }, s => { s.description = null; }]) {
    const invalid = structuredClone(state); mutate(invalid.combat_context.enemies[0].intents[0]);
    const value = project(invalid).snapshot;
    assert.equal(value.bound_actions.status, "unavailable");
    assert.deepEqual(value.bound_actions.actions, []);
  }
});


test("canonical current selector HUD preserves numeric ids, native potion flags and orb queue facts", () => {
  const state = selector();
  state.combat_context.orbs = [{ native_ref: "native-orb-a", id: "LIGHTNING", name: "Lightning",
    description: "Deal 3 damage.", passive_value: 3, evoke_value: 8, queue_index: 0, is_next_to_evoke: true,
    passive: 3, evoke: 8, keywords: [] }];
  state.combat_context.potion_states = [{ native_ref: "native-potion-a", target_type: "Self", can_use: false, automatic: true }];
  state.combat_context.companions = [{ native_ref: "native-pet-a", id: "OSTY", name: "Osty", is_alive: false, alive: false,
    hp: null, max_hp: null, block: 0, health_bar_visible: false, health_display: "InfiniteWithoutNumbers",
    hp_display_mode: "infinite_without_numbers", hp_numbers_available: false, statuses: [] }];
  const value = project(state).snapshot.interaction.content.context.combat;
  assert.equal(value.enemies[0].combat_id, 17);
  assert.equal(value.is_play_phase, false);
  assert.equal(value.player.orbs[0].passive_value, 3);
  assert.equal(value.player.orbs[0].queue_index, 0);
  assert.equal(value.player.potion_states[0].automatic, true);
  assert.equal(value.player.companions[0].is_alive, false);
  for (const mutate of [hud => { delete hud.orbs[0].passive_value; },
    hud => { hud.orbs[0].queue_index = 1; }, hud => { delete hud.potion_states[0].automatic; },
    hud => { delete hud.companions[0].is_alive; }]) {
    const other = structuredClone(state); mutate(other.combat_context);
    assert.equal(project(other).snapshot.completeness.status, "partial");
    assert.equal(project(other).snapshot.bound_actions.actions.length, 0);
  }
});


test("generated choice uses the existing Reference DTO and keeps all rich facts in same-capture detail Reads", () => {
  const state=selector({choose:true,skip:true});
  state.cards[0].public_details={keywords:[{name:"Block",description:"Prevent damage."}],card_previews:[]};
  const p=project(state),surface=p.snapshot.interaction.content.surface;
  assert.equal(p.snapshot.interaction.kind,"native_generated_card_choice");
  assert.deepEqual(p.snapshot.bound_actions.actions.map(action=>action.label),["Choose Strike","Choose Strike","Skip"]);
  assert.deepEqual(surface.selectable_card_entity_ids,surface.cards.map(card=>card.entity_id));
  assert.equal(Object.hasOwn(surface.cards[0],"keywords"),false);
  assert.equal(Object.hasOwn(surface.cards[0],"can_play"),false);
  const reads=[...p.reads.values()].filter(read=>read.content.display_context==="surface:native_generated_card_choice");
  assert.equal(reads.length,2);
  assert.deepEqual(reads[0].content.keywords,[{name:"Block",description:"Prevent damage."}]);
  assert.deepEqual([...p.bindings.values()][0].raw_request.args,{selector_ref:"native-selector",card_ref:"native-card-a"});
  const missing=structuredClone(state);delete missing.cards[0].public_details;
  assert.equal(project(missing).snapshot.completeness.status,"partial");
  assert.equal(project(missing).bindings.size,0);
});
