import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { discoverGameDirectory, readDiskIdentity, resolveInstallation } from "../src/game-installation.mjs";
import { startManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("raw merchant exit uses exact shop owner through pending selector and completion", async (context) => {
  const candidateDirectory = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidateDirectory) {
    context.skip("set STS2_MANAGED_TEST_CANDIDATE for proprietary exact merchant exit gate");
    return;
  }
  const gameDirectory = discoverGameDirectory();
  assert.ok(gameDirectory, "merchant exit gate requires an exact local game installation");
  const diskIdentity = readDiskIdentity(resolveInstallation(gameDirectory));
  const started = await startManagedPlayerEnvironmentSession({
    root, candidateDirectory, diskIdentity, requestTimeoutMs: 10_000
  });
  const request = value => started.runtime.process.request(value, 10_000);
  const action = (name, args) => request({ cmd: "action", action: name, args });
  try {
    await started.session.mount({ seed: "MERCHANT-EXIT-GATE" });
    let state = await request({ cmd: "enter_room", type: "shop" });
    assert.equal(state.decision, "shop");
    const roomRef = state.room_ref;
    const entryRef = state.card_removal.native_ref;
    const before = [state.player.gold, state.player.deck_size];
    state = await action("remove_card", { room_ref: roomRef, entry_ref: entryRef });
    assert.equal(state.decision, "deck_card_select");
    for (const args of [undefined, { room_ref: "stale-room" }, { room_ref: roomRef }]) {
      const refused = await action("leave_room", args);
      assert.equal(refused.type, "error", `pending selector exit ${JSON.stringify(args)}`);
    }
    state = await action("select_deck_card", {
      selector_ref: state.selector_ref, card_ref: state.cards[1].native_ref
    });
    assert.equal(state.stage, "preview");
    assert.deepEqual([state.player.gold, state.player.deck_size], before);
    for (const args of [undefined, { room_ref: "stale-room" }, { room_ref: roomRef }]) {
      const refused = await action("leave_room", args);
      assert.equal(refused.type, "error", `preview exit ${JSON.stringify(args)}`);
    }
    state = await action("confirm_deck_selection", {
      selector_ref: state.selector_ref, selected_refs: state.selected_refs.join(",")
    });
    assert.equal(state.decision, "shop");
    assert.deepEqual([state.player.gold, state.player.deck_size],
      [before[0] - state.card_removal.cost, before[1] - 1]);
    assert.equal((await action("leave_room")).type, "error");
    assert.equal((await action("leave_room", { room_ref: "stale-room" })).type, "error");
    assert.equal((await action("leave_room", { room_ref: roomRef })).decision, "map_select");
  } finally {
    await started.session.close();
  }
});
