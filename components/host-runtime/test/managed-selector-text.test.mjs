import assert from "node:assert/strict";
import test from "node:test";
import { ManagedPlayerEnvironmentSession, projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { MANAGED_TEXT_MENU_PROFILE, ManagedTextMenuSessionAdapter,
  managedTextMenuV1Contract } from "../src/managed-text-menu-map.mjs";
import { managedTextMenuV2Contract, ManagedTextMenuV2SessionAdapter
} from "../src/managed-text-menu-v2.mjs";

const identity = { runtimeInstanceId: "selector-test", environmentFingerprint: "exact-managed", sequence: 1 };
const player = { name: "Ironclad", character_id: "IRONCLAD", hp: 80, max_hp: 80,
  gold: 99, native_ref: "player-ref", max_potion_slots: 3, relics: [], potions: [], deck: [] };
const context = { act: 1, act_index: 0, act_definition_id: "OVERGROWTH", act_name: "Overgrowth",
  floor: 2, total_floor: 2, ascension: 0, room_type: "Merchant", bosses: [], modifiers: [] };
const cards = [
  { index: 0, native_ref: "card-a", id: "STRIKE", name: "Strike", is_selected: false, is_selectable: true, is_deselectable: false },
  { index: 1, native_ref: "card-b", id: "STRIKE", name: "Strike", is_selected: false, is_selectable: true, is_deselectable: false }
];
function selector(stage = "selecting", chosen = null) {
  const selected = chosen == null ? [] : [chosen];
  return { type: "decision", decision: "deck_card_select", context, player,
    selector_ref: "selector-a", stage, origin: "deck_generic",
    prompt: "Choose a card to remove.", min_select: 1, max_select: 1,
    cancelable: true, require_manual_confirmation: true,
    selected_refs: selected, can_cancel_selection: stage === "selecting",
    can_preview: false,
    can_cancel_preview: stage === "preview", can_confirm: stage === "preview",
    cards: cards.map(card => ({ ...card, is_selected: selected.includes(card.native_ref),
      is_selectable: stage === "selecting" && selected.length === 0,
      is_deselectable: false })) };
}
function shop() {
  return { type: "decision", decision: "shop", context, player,
    room_ref: "shop-room", cards: [], relics: [], potions: [],
    card_removal: { native_ref: "removal-entry", cost: 75, is_stocked: true, can_purchase: true } };
}
function menu(state) {
  const snapshot = projectManagedCandidateDecision({ state, ...identity }).snapshot;
  return new ManagedTextMenuSessionAdapter({ observe: () => snapshot,
    async submit() { throw new Error("observation must not dispatch"); } }).observe();
}

test("published contract covers merchant and deck selector fixture pages", () => {
  for (const state of [shop(), selector(), selector("preview", "card-a")]) {
    const source = projectManagedCandidateDecision({ state, ...identity }).snapshot;
    for (const [contract, Adapter] of [
      [managedTextMenuV1Contract(), ManagedTextMenuSessionAdapter],
      [managedTextMenuV2Contract(), ManagedTextMenuV2SessionAdapter]
    ]) {
      const page = new Adapter({ observe: () => source,
        async submit() { throw new Error("contract test is observation only"); } }).observe();
      assert.equal(page.schema, contract.snapshot_schema);
      assert.equal(page.menu_actions.status, "complete");
      assert.ok(contract.interaction_kinds.includes(page.interaction.kind));
      for (const action of page.menu_actions.actions)
        assert.ok(contract.action_verbs.includes(action.verb), action.verb);
    }
  }
});

test("exact deck selector is one sequential card choice, preview return, and confirmation", async () => {
  let current = shop();
  const raw = [];
  const process = { async request(request) {
    if (request.cmd === "start_run") return current;
    raw.push(request);
    if (request.action === "remove_card") current = selector();
    else if (request.action === "select_deck_card") current = selector("preview", request.args.card_ref);
    else if (request.action === "cancel_deck_preview") current = selector();
    else if (request.action === "confirm_deck_selection") current = shop();
    return current;
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "REMOVE" });
  const adapter = new ManagedTextMenuSessionAdapter(session);
  const submit = async (page, index, requestId) => adapter.submit({
    request_id: requestId, expected_snapshot_id: page.snapshot_id,
    action_id: page.menu_actions.actions[index].action_id,
    input_profile: MANAGED_TEXT_MENU_PROFILE
  });
  let page = adapter.observe();
  assert.equal(page.interaction.kind, "shop_inventory");
  assert.deepEqual(page.menu_actions.actions.map(x => x.label), ["Buy card removal", "Leave shop"]);
  page = (await submit(page, 0, "buy-removal")).successor;
  assert.equal(page.interaction.kind, "deck_card_selection");
  assert.deepEqual(page.menu_actions.actions.map(x => x.verb), ["select", "select", "cancel"]);
  assert.notEqual(page.menu_actions.actions[0].subject_referent_id,
    page.menu_actions.actions[1].subject_referent_id);
  assert.equal(JSON.stringify(page).includes("card-a"), false);
  page = (await submit(page, 1, "select-second")).successor;
  assert.equal(page.interaction.stage, "preview");
  assert.deepEqual(page.menu_actions.actions.map(x => x.verb), ["cancel", "confirm"]);
  page = (await submit(page, 0, "return-selection")).successor;
  assert.equal(page.interaction.stage, "selecting");
  page = (await submit(page, 0, "select-first")).successor;
  page = (await submit(page, 1, "confirm-removal")).successor;
  assert.equal(page.interaction.kind, "shop_inventory");
  assert.deepEqual(raw.map(x => x.action), ["remove_card", "select_deck_card",
    "cancel_deck_preview", "select_deck_card", "confirm_deck_selection"]);
  assert.deepEqual(raw[0].args, { room_ref: "shop-room", entry_ref: "removal-entry" });
  assert.deepEqual(raw[4].args, { selector_ref: "selector-a", selected_refs: "card-a" });
});

test("unknown selector and incomplete shop facts expose no executable text catalog", () => {
  const legacy = { ...selector(), decision: "card_select", selector_ref: undefined };
  assert.equal(menu(legacy).menu_actions.status, "unavailable");
  for (const bad of [
    { ...selector(), selector_ref: null },
    { ...selector(), cards: [cards[0], { ...cards[1], native_ref: "card-a" }] },
    { ...selector(), cancelable: null },
    { ...selector(), cards: [null, cards[1]] }
  ]) assert.equal(menu(bad).menu_actions.status, "unavailable");
  for (const bad of [
    { ...shop(), cards: null },
    { ...shop(), cards: [{ index: 0, native_ref: "removal-entry", name: "Strike",
      cost: 50, is_stocked: true, can_purchase: true }] },
    { ...shop(), card_removal: { ...shop().card_removal, can_purchase: null } },
    { ...shop(), card_removal: { ...shop().card_removal, native_ref: null } }
  ]) assert.equal(menu(bad).menu_actions.status, "unavailable");
});

test("deferred merchant callback error remains unknown without a second purchase", async () => {
  let purchases = 0;
  const process = { async request(request) {
    if (request.cmd === "start_run") return shop();
    purchases += 1;
    return { type: "error", message: "The bound merchant removal changed before native purchase." };
  } };
  const session = new ManagedPlayerEnvironmentSession({ process, ...identity });
  await session.mount({ seed: "SHOP-UNKNOWN" });
  const adapter = new ManagedTextMenuSessionAdapter(session);
  const page = adapter.observe();
  const request = { request_id: "shop-unknown", expected_snapshot_id: page.snapshot_id,
    action_id: page.menu_actions.actions[0].action_id,
    input_profile: MANAGED_TEXT_MENU_PROFILE };
  const receipt = await adapter.submit(request);
  assert.equal(receipt.status, "unknown");
  assert.equal(receipt.native_delivery, "unknown");
  assert.equal(receipt.retry, "never");
  assert.equal(receipt.successor, null);
  assert.deepEqual(await adapter.submit(request), receipt);
  assert.equal((await adapter.submit({ ...request, request_id: "later" })).status,
    "not_applied");
  assert.equal(purchases, 1);
});
