import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { discoverGameDirectory, readDiskIdentity, resolveInstallation } from "../src/game-installation.mjs";
import { startManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("exact candidate refuses raw treasure leave until native completion", async (context) => {
  const candidateDirectory = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidateDirectory) {
    context.skip("set STS2_MANAGED_TEST_CANDIDATE for the proprietary exact-build treasure gate");
    return;
  }
  const gameDirectory = discoverGameDirectory();
  assert.ok(gameDirectory, "the exact-build treasure gate requires a local game installation");
  const diskIdentity = readDiskIdentity(resolveInstallation(gameDirectory));
  const started = await startManagedPlayerEnvironmentSession({
    root, candidateDirectory, diskIdentity, requestTimeoutMs: 10_000
  });
  const request = (value) => started.runtime.process.request(value, 10_000);
  const leave = (args) => request({ cmd: "action", action: "leave_room", args });
  const reject = async (args) => assert.equal((await leave(args)).type, "error");
  try {
    await started.session.mount({ seed: "TREASURE-LEAVE-GATE" });
    let state = await request({ cmd: "enter_room", type: "treasure" });
    assert.equal(state.decision, "treasure_chest");
    await reject(undefined);
    await reject({ room_ref: "stale-room" });
    await reject({ room_ref: state.room_ref });

    state = await request({ cmd: "action", action: "open_treasure",
      args: { room_ref: state.room_ref } });
    assert.equal(state.decision, "treasure_relic");
    await reject(undefined);
    await reject({ room_ref: "stale-room" });
    await reject({ room_ref: state.room_ref });

    state = await request({ cmd: "action", action: "select_treasure_relic",
      args: { room_ref: state.room_ref, relic_ref: state.relics[0].native_ref } });
    assert.equal(state.decision, "treasure_complete");
    await reject(undefined);
    await reject({ room_ref: "stale-room" });
    assert.equal((await leave({ room_ref: state.room_ref })).decision, "map_select");

    state = await request({ cmd: "enter_room", type: "shop" });
    assert.equal(state.decision, "shop");
    assert.equal((await leave(undefined)).type, "error");
    assert.equal((await leave({ room_ref: "stale-room" })).type, "error");
    assert.equal((await leave({ room_ref: state.room_ref })).decision, "map_select");
  } finally {
    await started.session.close();
  }
});
