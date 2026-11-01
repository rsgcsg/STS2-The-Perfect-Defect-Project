import assert from "node:assert/strict";
import test from "node:test";
import { combatState } from "./fixtures/managed-fair-combat.mjs";
import { ManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { ManagedPeDriverSession } from "../src/managed-pe-driver-session.mjs";

function fixture() {
  const pending = { ...combatState(), is_play_phase: false, turn_owner: "enemy" };
  let current = pending;
  const calls = [];
  const process = { async request(request) {
    calls.push(request);
    if (["start_run", "observe_decision"].includes(request.cmd)) return structuredClone(current);
    throw new Error(`unexpected native input ${request.cmd}`);
  } };
  const session = new ManagedPlayerEnvironmentSession({ process,
    runtimeInstanceId: "settling-test", environmentFingerprint: "settling-environment" });
  const driver = new ManagedPeDriverSession({ session,
    runtime: { adapterRuntimeInstanceId: "settling-test" } });
  return { session, driver, calls, ready() { current = combatState(); },
    failRead() { current = { type: "error", message: "native pending task failed" }; } };
}

test("normal capture refreshes settling without rotating unchanged snapshots or repeating an input", async () => {
  const { driver, calls, ready } = fixture();
  const first = await driver.handle({ command: "reset", seed: "U1SETTLINGTEST" });
  assert.equal(first.snapshot.status, "settling");
  const capture = () => driver.handle({ command: "capture" });
  const a = (await capture()).full.basis.snapshot;
  const b = (await capture()).full.basis.snapshot;
  assert.equal(a.snapshot_id, first.snapshot.snapshot_id);
  assert.equal(b.snapshot_id, a.snapshot_id, "unchanged native facts retain the watchdog identity");
  ready();
  const next = (await capture()).full.basis.snapshot;
  assert.equal(next.status, "interactive");
  assert.notEqual(next.snapshot_id, a.snapshot_id);
  const count = calls.length;
  await capture();
  assert.equal(calls.length, count, "interactive snapshots stay bound until the next mutation");
  assert.deepEqual(calls.map(x => x.cmd), ["start_run", "observe_decision", "observe_decision", "observe_decision"]);
});

for (const command of ["observe", "text_observe"]) {
  test(`${command} reaches the same settling refresh`, async () => {
    const { driver, calls, ready } = fixture();
    await driver.handle({ command: "reset", seed: "U1SETTLINGREADTEST" });
    ready();
    await driver.handle({ command });
    assert.deepEqual(calls.map(x => x.cmd), ["start_run", "observe_decision"]);
  });
}

test("a failed native observation is surfaced and leaves the old snapshot bound", async () => {
  const { driver, session, failRead } = fixture();
  const first = await driver.handle({ command: "reset", seed: "U1SETTLINGFAILURETEST" });
  failRead();
  await assert.rejects(driver.handle({ command: "capture" }), /managed_observe_decision_failed/);
  assert.equal(session.observe().snapshot_id, first.snapshot.snapshot_id);
});
