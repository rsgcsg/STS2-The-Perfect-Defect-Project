import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import test from "node:test";
import { waitForExit } from "../src/runtime-probe.mjs";

test("already-exited child returns its actual code without a new listener", async () => {
  const child = spawn(process.execPath, ["-e", ""], { stdio: "ignore" });
  await once(child, "exit");
  const listeners = child.listenerCount("exit");
  assert.deepEqual(await waitForExit(child, 10000), { code: 0, signal: null });
  assert.equal(child.listenerCount("exit"), listeners);
});

test("actual early child exit preserves other exit observers", async () => {
  const child = spawn(process.execPath, ["-e", ""], { stdio: "ignore" });
  let observed = 0;
  const observer = () => { observed++; };
  child.on("exit", observer);
  assert.deepEqual(await waitForExit(child, 10000), { code: 0, signal: null });
  assert.equal(observed, 1);
  assert.deepEqual(child.listeners("exit"), [observer]);
});

test("timeout removes only the waiting listener while the actual child stays alive", async () => {
  const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
  const observer = () => {};
  child.on("exit", observer);
  try {
    assert.equal(await waitForExit(child, 25), null);
    assert.equal(child.exitCode, null);
    assert.equal(child.signalCode, null);
    assert.deepEqual(child.listeners("exit"), [observer]);
  } finally {
    const exited = once(child, "exit");
    child.kill();
    await exited;
  }
});
