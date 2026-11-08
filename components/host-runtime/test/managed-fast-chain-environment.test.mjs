import assert from "node:assert/strict";
import test from "node:test";
import { startManagedCandidateRuntime, withFastChainSwitch } from "../src/managed-candidate.mjs";

const env = { PATH: "/bin", STS2_LIB: "/candidate/lib", STS2_GAME_DIR: "/game/data" };

test("fast_chain off: the native environment is the old one and drops an inherited switch", () => {
  assert.deepEqual(withFastChainSwitch(env, false), env);
  assert.deepEqual(withFastChainSwitch({ ...env, STS2_HEADLESS_FAST_CHAIN: "1" }, false), env);
});

test("fast_chain on: the Host sets STS2_HEADLESS_FAST_CHAIN=1 for the native", () => {
  assert.deepEqual(withFastChainSwitch({ ...env, STS2_HEADLESS_FAST_CHAIN: "0" }, true), { ...env, STS2_HEADLESS_FAST_CHAIN: "1" });
});

test("fastChain must be a boolean, checked before anything starts", async () => {
  await assert.rejects(startManagedCandidateRuntime({ fastChain: "1" }), /fastChain must be a boolean/u);
});
