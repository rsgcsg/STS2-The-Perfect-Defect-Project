import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { discoverGameDirectory, readDiskIdentity, resolveInstallation } from "../src/game-installation.mjs";
import { startManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("exact candidate merchant removal uses staged native deck choice and commits once", async (context) => {
  const candidateDirectory = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidateDirectory) {
    context.skip("set STS2_MANAGED_TEST_CANDIDATE for the proprietary exact-build selector gate");
    return;
  }
  const gameDirectory = discoverGameDirectory();
  assert.ok(gameDirectory, "selector gate requires a local exact game installation");
  const diskIdentity = readDiskIdentity(resolveInstallation(gameDirectory));
  const started = await startManagedPlayerEnvironmentSession({
    root, candidateDirectory, diskIdentity, requestTimeoutMs: 10_000
  });
  const request = (value) => started.runtime.process.request(value, 10_000);
  const action = (name, args) => request({ cmd: "action", action: name, args });
  try {
    await started.session.mount({ seed: "SELECTOR-SHOP-GATE" });
    let state = await request({ cmd: "enter_room", type: "shop" });
    assert.equal(state.decision, "shop");
    assert.equal(state.card_removal.can_purchase, true);
    const before = { gold: state.player.gold, deck: state.player.deck_size,
      cost: state.card_removal.cost, room: state.room_ref, entry: state.card_removal.native_ref };
    assert.equal((await action("remove_card", { entry_ref: before.entry })).type, "error");
    state = await action("remove_card", { room_ref: before.room, entry_ref: before.entry });
    assert.equal(state.decision, "deck_card_select");
    assert.equal(state.origin, "deck_generic");
    assert.deepEqual([state.min_select, state.max_select,
      state.cancelable, state.require_manual_confirmation], [1, 1, true, true]);
    const card = state.cards[1];
    assert.equal((await action("select_cards", { indices: "1" })).type, "error");
    assert.equal((await action("skip_select")).type, "error");
    assert.equal((await action("select_deck_card", { card_ref: card.native_ref })).type, "error");
    state = await action("select_deck_card",
      { selector_ref: state.selector_ref, card_ref: card.native_ref });
    assert.equal(state.stage, "preview");
    state = await action("cancel_deck_preview", { selector_ref: state.selector_ref });
    assert.equal(state.stage, "selecting");
    assert.deepEqual(state.selected_refs, []);
    state = await action("cancel_deck_selection", { selector_ref: state.selector_ref });
    assert.equal(state.decision, "shop");
    assert.deepEqual([state.player.gold, state.player.deck_size,
      state.card_removal.is_stocked], [before.gold, before.deck, true]);

    state = await action("remove_card", { room_ref: state.room_ref,
      entry_ref: state.card_removal.native_ref });
    assert.equal(state.decision, "deck_card_select");
    state = await action("select_deck_card",
      { selector_ref: state.selector_ref, card_ref: card.native_ref });
    assert.equal(state.stage, "preview");
    assert.equal((await action("confirm_deck_selection",
      { selector_ref: state.selector_ref, selected_refs: "wrong-card" })).type, "error");
    state = await action("confirm_deck_selection",
      { selector_ref: state.selector_ref, selected_refs: card.native_ref });
    assert.equal(state.decision, "shop");
    assert.deepEqual([state.player.gold, state.player.deck_size,
      state.card_removal.is_stocked], [before.gold - before.cost, before.deck - 1, false]);
    assert.equal((await action("remove_card", { room_ref: state.room_ref,
      entry_ref: state.card_removal.native_ref })).type, "error");
    assert.equal((await action("leave_shop", { room_ref: state.room_ref })).decision, "map_select");
  } finally {
    await started.session.close();
  }
});
