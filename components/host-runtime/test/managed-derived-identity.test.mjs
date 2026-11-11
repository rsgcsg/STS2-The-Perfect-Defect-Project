import assert from "node:assert/strict";
import test from "node:test";
import { projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { renamePrivateCounters } from "./fixtures/managed-fair-combat.mjs";
import { selector } from "./fixtures/managed-native-selector.mjs";
import { shopInventory } from "./fixtures/managed-shop.mjs";

// A1: ids inside a parent's public content are derived from the parent's stable
// identity plus a public position, so they survive from step to step. Snapshot,
// action and Read ids stay snapshot-scoped.
const base = { runtimeInstanceId: "derived-runtime", environmentFingerprint: "derived-environment" };
const session = () => {
  const aliases = new Map(); let sequence = 0;
  return state => projectManagedCandidateDecision({ ...base, state: structuredClone(state), sequence: ++sequence, publicEntityAliases: aliases });
};
const player = () => ({ name: "The Defect", hp: 70, max_hp: 75, gold: 99, relics: [], potions: [], deck: [] });
const preview = (id = "CLAW") => ({ id, name: id, type: "Attack", cost: "0", rarity: "Common", description: "Deal 3 damage.",
  target_type: "AnyEnemy", is_upgraded: false, keywords: [], card_previews: [] });

function eventState() {
  const tooltipCard = { id: "BYRDONIS_EGG", name: "Egg", type: "Quest", rarity: "Quest", cost: "-1", description: "Unplayable.",
    is_upgraded: false, keywords: [], card_previews: [] };
  return { type: "decision", decision: "event_choice", context: { act: 1, floor: 3, room_type: "Event" },
    room_ref: "private-room", event_ref: "private-event", event_name: "Nest", event_id: "BYRDONIS_NEST", ancient: false,
    in_dialogue: false, event_public_complete: true, description: "Choose.", player: player(),
    options: [0, 1].map(index => ({ index, native_ref: `private-option-${index}`, title: `Option ${index}`, description: "Take it.",
      is_locked: false, is_enabled: true, is_proceed: false, was_chosen: false, will_kill_player: false,
      tooltips: [{ kind: "card", card: { ...tooltipCard } }, { kind: "card", card: { ...tooltipCard } }] })) };
}

test("event tooltip cards keep their id across steps, per option and per tooltip position", () => {
  const project = session(); const state = eventState();
  const ids = snapshot => snapshot.interaction.content.surface.options.map(option => option.tooltips.map(tip => tip.card.entity_id));
  const first = project(state).snapshot, second = project(state).snapshot;
  assert.notEqual(first.snapshot_id, second.snapshot_id);
  assert.deepEqual(ids(first), ids(second));
  assert.equal(new Set(ids(first).flat()).size, 4);
  // A replaced option object is a new parent, so its tooltip cards are new too.
  const replaced = structuredClone(state); replaced.options[0].native_ref = "private-option-new";
  const third = ids(project(replaced).snapshot);
  assert.notDeepEqual(third[0], ids(first)[0]);
  assert.deepEqual(third[1], ids(first)[1]);
  assert.equal(JSON.stringify(first).includes("private-"), false);
  // Actions stay snapshot-scoped.
  assert.notDeepEqual(first.bound_actions.actions.map(a => a.bound_action_id), second.bound_actions.actions.map(a => a.bound_action_id));
});

test("unbound event options are stable within one event and distinct in another", () => {
  const project = session(); const state = eventState();
  for (const option of state.options) option.native_ref = "private-duplicate";
  const optionIds = snapshot => snapshot.interaction.content.surface.options.map(option => option.entity_id);
  const first = optionIds(project(state).snapshot), second = optionIds(project(state).snapshot);
  assert.deepEqual(first, second);
  assert.equal(new Set(first).size, 2);
  const other = structuredClone(state); other.event_ref = "private-event-2";
  assert.equal(optionIds(project(other).snapshot).some(id => first.includes(id)), false);
  // Without a parent key the id stays snapshot-scoped (no cross-screen identity).
  const orphan = structuredClone(state); delete orphan.event_ref;
  assert.notDeepEqual(optionIds(project(orphan).snapshot), optionIds(project(orphan).snapshot));
});

test("rest options and card reward alternatives keep their id across steps of one screen", () => {
  const project = session();
  const rest = { type: "decision", decision: "rest_site", context: { act: 1, floor: 5, room_type: "RestSite" }, room_ref: "private-rest-room",
    options: [{ index: 0, native_ref: "private-rest-a", option_id: "HEAL", name: "Rest", description: "Heal.", is_enabled: true },
      { index: 1, native_ref: "private-rest-b", option_id: "SMITH", name: "Smith", description: "Upgrade.", is_enabled: true }], player: player() };
  const restIds = snapshot => snapshot.interaction.content.surface.options.map(option => option.entity_id);
  const r1 = project(rest).snapshot, r2 = project(rest).snapshot;
  assert.deepEqual(restIds(r1), restIds(r2));
  assert.deepEqual(r1.referents.filter(r => r.role === "rest_option").map(r => r.referent_id), restIds(r1));
  const nextRoom = { ...structuredClone(rest), room_ref: "private-rest-room-2" };
  assert.equal(restIds(project(nextRoom).snapshot).some(id => restIds(r1).includes(id)), false);
  const roomless = structuredClone(rest); delete roomless.room_ref;
  assert.notDeepEqual(restIds(project(roomless).snapshot), restIds(project(roomless).snapshot));

  const card = { native_ref: "private-offer", card_entity_ref: "private-card", index: 0, id: "CARD.ZAP", name: "Zap", type: "Skill",
    cost: 1, rarity: "Basic", is_upgraded: false, description: "Channel 1 Lightning.", render_complete: true, keywords: [], card_previews: [] };
  const reward = { type: "decision", decision: "card_reward", context: { act: 1, floor: 6, room_type: "Monster" }, player: player(),
    parent_status: "bound", parent_reward_ref: "private-reward", reward_set_ref: "private-set", selection_completes_reward: true,
    native_relic_refresh_subscription: "absent", cards: [card],
    alternatives: [{ index: 0, id: "Skip", option_id: "Skip", native_ref: "private-skip", name: "Skip", title: "Skip",
      after_selected: "end_selection_and_do_not_complete_reward", is_native_skip: true }] };
  const alternativeIds = snapshot => snapshot.interaction.content.surface.alternatives.map(option => option.entity_id);
  const a1 = project(reward).snapshot, a2 = project(reward).snapshot;
  assert.equal(alternativeIds(a1).length, 1);
  assert.deepEqual(alternativeIds(a1), alternativeIds(a2));
  const otherReward = { ...structuredClone(reward), parent_reward_ref: "private-reward-2" };
  assert.notDeepEqual(alternativeIds(project(otherReward).snapshot), alternativeIds(a1));
  assert.equal(JSON.stringify([r1, a1]).includes("private-"), false);
});

test("selector hover cards derive from the parent card and preview position", () => {
  const project = session();
  const state = selector();
  state.cards[0].card_previews = [preview("CLAW"), preview("ZAP")];
  const hover = snapshot => snapshot.interaction.content.surface.cards[0].card_previews.map(card => card.entity_id);
  const first = project(state).snapshot, second = project(state).snapshot;
  assert.equal(first.completeness.status, "complete");
  assert.deepEqual(hover(first), hover(second));
  assert.equal(new Set(hover(first)).size, 2);
  // Content changes stay visible under the same position id.
  const changed = structuredClone(state); changed.cards[0].card_previews[0].description = "Deal 5 damage.";
  const third = project(changed).snapshot;
  assert.deepEqual(hover(third), hover(first));
  assert.equal(third.interaction.content.surface.cards[0].card_previews[0].description, "Deal 5 damage.");
  // A different parent card gets different hover ids.
  const moved = structuredClone(state); moved.cards[0].native_ref = "native-card-c";
  assert.equal(hover(project(moved).snapshot).some(id => hover(first).includes(id)), false);
});

test("revealed Draw candidates with hover cards stay independent of private order", () => {
  const state = selector(); state.display_pile = "draw";
  for (const card of state.cards) card.card_previews = [preview("CLAW")];
  const other = renamePrivateCounters(structuredClone(state));
  other.cards.reverse();
  const left = projectManagedCandidateDecision({ ...base, sequence: 1, state }).snapshot;
  const right = projectManagedCandidateDecision({ ...base, sequence: 1, state: other }).snapshot;
  right.observed_at = left.observed_at;
  assert.deepEqual(left, right);
});

test("combat_piles Draw zone of different cards: ids and listed order independent of private order (review of #463)", () => {
  // The Draw zone is sorted by public content before any alias is allocated; equal cards tie-break
  // by alias only. A draw-pile selector surface instead keeps the native display order (not covered here).
  const withDraw = () => {
    const state = selector();
    const template = state.combat_piles[0].cards[0];
    state.combat_piles[0].cards = [["native-201", "Strike", "CLAW"], ["native-202", "Defend", "ZAP"], ["native-203", "Zap", null],
      ["native-204", "Strike", "CLAW"], ["native-205", "Defend", null]]
      .map(([ref, name, tip]) => ({ ...structuredClone(template), native_ref: ref, id: name.toUpperCase(), name,
        description: `${name} text.`, card_previews: tip === null ? [] : [preview(tip)] }));
    state.combat_context.draw_pile_count = 5;
    return state;
  };
  const view = projection => {
    const { observed_at: _clock, ...snapshot } = projection.snapshot;
    return JSON.stringify([snapshot, [...projection.reads.values()]]);
  };
  const first = projectManagedCandidateDecision({ ...base, sequence: 1, state: withDraw() });
  const draw = [...first.reads.values()].find(read => read.descriptor.kind === "combat_piles").content.zones.find(zone => zone.zone === "draw");
  assert.deepEqual(draw.cards.map(card => card.name).sort(), ["Defend", "Defend", "Strike", "Strike", "Zap"]);
  assert.equal(new Set(draw.cards.map(card => card.entity_id)).size, 5);
  const reference = view(first);
  for (const order of [[4, 3, 2, 1, 0], [1, 3, 0, 4, 2], [2, 0, 4, 1, 3]]) {
    const state = withDraw();
    state.combat_piles[0].cards = order.map(index => state.combat_piles[0].cards[index]);
    for (const other of [state, renamePrivateCounters(structuredClone(state))])
      assert.equal(view(projectManagedCandidateDecision({ ...base, sequence: 1, state: other })), reference, `order ${order}`);
  }
});

test("unbound selector and shop positions are stable within their owner", () => {
  const project = session();
  const state = selector({ choose: true });
  state.cards[1].native_ref = state.cards[0].native_ref;
  const cardIds = snapshot => snapshot.interaction.content.surface.cards.map(card => card.entity_id);
  const s1 = cardIds(project(state).snapshot), s2 = cardIds(project(state).snapshot);
  assert.deepEqual(s1, s2);
  assert.equal(new Set(s1).size, 2);
  const otherSelector = { ...structuredClone(state), selector_ref: "native-selector-2" };
  assert.equal(cardIds(project(otherSelector).snapshot).some(id => s1.includes(id)), false);

  const shop = shopInventory();
  for (const entry of shop.potions ?? []) entry.native_ref = "private-duplicate-entry";
  if ((shop.potions ?? []).length < 2) shop.potions = [0, 1].map(index => ({ ...shop.potions[0], inventory_index: index,
    slot_ref: `private-potion-slot-${index}`, native_ref: "private-duplicate-entry" }));
  const potionIds = snapshot => snapshot.interaction.content.surface.potions.map(entry => entry.entity_id);
  const p1 = potionIds(project(shop).snapshot), p2 = potionIds(project(shop).snapshot);
  assert.deepEqual(p1, p2);
  assert.equal(new Set(p1).size, p1.length);
  const otherShop = { ...structuredClone(shop), shop_ref: "private-other-shop" };
  assert.equal(potionIds(project(otherShop).snapshot).some(id => p1.includes(id)), false);
});

test("derived ids never renumber a later entity id", () => {
  // One session sees an event with tooltip cards (derived ids) first; the other does
  // not. A later selector screen must get the same entity ids in both.
  for (const identityMode of ["crypto", "sequence"]) {
    const withDerived = new Map(), without = new Map();
    const project = (aliases, state, sequence) => projectManagedCandidateDecision({ ...base, identityMode, state: structuredClone(state),
      sequence, publicEntityAliases: aliases }).snapshot;
    const plain = eventState(); for (const option of plain.options) option.tooltips = [];
    const tooltipIds = project(withDerived, eventState(), 1).interaction.content.surface.options.flatMap(option => option.tooltips.map(tip => tip.card.entity_id));
    assert.equal(tooltipIds.length, 4);
    project(without, plain, 1);
    const ids = snapshot => snapshot.interaction.content.surface.cards.map(card => card.entity_id);
    const left = ids(project(withDerived, selector(), 2)), right = ids(project(without, selector(), 2));
    assert.deepEqual(left, right);
    assert.equal(left.some(id => tooltipIds.includes(id)), false);
  }
});
