import assert from "node:assert/strict";
import test from "node:test";
import { ownReferenceEpisodeLifecycle, startShippedPlayerEnvironmentEpisode }
  from "../src/shipped-player-environment.mjs";

const exit = { code: 0, signal: null, forced: false };

function fixture({ signal, failController = false, failOutput = false } = {}) {
  const seen = { controller: 0, stop: 0, output: 0 };
  const child = { pid: 1234 };
  const owner = ownReferenceEpisodeLifecycle({ child, endpoint: "http://127.0.0.1:1234",
    hostControlToken: "synthetic-secret-never-in-receipts", runtimeInstance: () => "runtime",
    streams: ["synthetic-output"], signal,
    controller: () => ({ async close() {
      seen.controller++;
      if (failController) throw new Error("controller_close_failed");
    } }),
    async stop(actualChild, options) {
      seen.stop++;
      assert.equal(actualChild, child);
      assert.equal(options.expectedRuntimeInstanceId, "runtime");
      return exit;
    },
    async finishOutput(streams) {
      seen.output++;
      assert.deepEqual(streams, ["synthetic-output"]);
      if (failOutput) throw new Error("output_failed");
    }
  });
  return { owner, seen };
}

test("pre-launch AbortSignal rejects before reading or spawning any actual game", async () => {
  const abort = new AbortController(); abort.abort(new Error("early_stop"));
  await assert.rejects(startShippedPlayerEnvironmentEpisode({ signal: abort.signal }), /early_stop/);
});

test("concurrent Close callers share one actual child exit and output completion", async () => {
  const { owner, seen } = fixture();
  const first = owner.close(), second = owner.close();
  assert.equal(first, second);
  assert.deepEqual(await first, exit);
  assert.deepEqual(await second, exit);
  assert.deepEqual(seen, { controller: 1, stop: 1, output: 1 });
  assert.throws(() => owner.assertActive(), /reference_episode_closing/);
});

test("early and repeated cancellation owns closure and forbids the next bootstrap dispatch", async () => {
  const abort = new AbortController(), { owner, seen } = fixture({ signal: abort.signal });
  let dispatches = 0;
  await Promise.resolve(); // Model an in-flight startup acquisition.
  abort.abort(new Error("early_stop")); abort.abort(new Error("second_stop"));
  assert.throws(() => { owner.assertActive(); dispatches++; }, /early_stop/);
  assert.equal(dispatches, 0);
  assert.deepEqual(await owner.close(), exit);
  assert.deepEqual(seen, { controller: 1, stop: 1, output: 1 });
});

test("synchronous/async controller closure failure cannot strand the exact owned child", async () => {
  const { owner, seen } = fixture({ failController: true });
  assert.deepEqual(await owner.close(), exit);
  assert.equal(seen.stop, 1);
  assert.equal(seen.output, 1);
});

test("output cleanup error retains the real Host exit receipt", async () => {
  const { owner, seen } = fixture({ failOutput: true });
  await assert.rejects(owner.close(), error => {
    assert.deepEqual(error.host_exit, exit);
    assert.ok(!JSON.stringify(error.host_exit).includes("synthetic-secret"));
    return /output_failed/.test(error.message);
  });
  assert.equal(seen.stop, 1);
});
