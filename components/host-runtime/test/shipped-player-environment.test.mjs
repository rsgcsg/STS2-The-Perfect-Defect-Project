import assert from "node:assert/strict";
import test from "node:test";
import {
  allocateReferenceEndpoint,
  settleReferenceReceipt,
  ShippedPlayerEnvironmentSession
} from "../src/shipped-player-environment.mjs";
import { handleReferenceDriverRequest } from "../tools/reference-pe-driver.mjs";

function fakeEpisode(seed, events) {
  const snapshot = { snapshot_id: `snapshot-${seed}`, interaction: { kind: "main_menu" } };
  return {
    snapshot,
    identity: { seed },
    observe: async () => snapshot,
    read: async (input) => ({ ...input, status: "observed" }),
    submit: async (input) => ({ ...input, delivery: "delivered", successor: snapshot }),
    close: async () => {
      events.push(`close:${seed}`);
      return { code: 0, signal: null };
    }
  };
}

test("Reference Player Environment resets by replacing the exact runtime", async () => {
  const events = [];
  const session = new ShippedPlayerEnvironmentSession(
    { marker: "options" },
    { startEpisode: async ({ seed, marker }) => {
      assert.equal(marker, "options");
      events.push(`start:${seed}`);
      return fakeEpisode(seed, events);
    } }
  );
  assert.equal((await session.reset("FIRST")).snapshot_id, "snapshot-FIRST");
  assert.equal((await session.reset("SECOND")).snapshot_id, "snapshot-SECOND");
  await session.close();
  assert.deepEqual(events, ["start:FIRST", "close:FIRST", "start:SECOND", "close:SECOND"]);
});

test("Reference JSONL requests preserve exact action and snapshot bindings", async () => {
  const calls = [];
  const session = {
    lastIdentity: { runtime_instance_id: "runtime-1" },
    reset: async (seed) => ({ snapshot_id: `snapshot-${seed}` }),
    observe: async () => ({ snapshot_id: "snapshot-current" }),
    read: async (input) => ({ status: "observed", ...input }),
    submit: async (input) => {
      calls.push(input);
      return { delivery: "delivered" };
    },
    provenance: async () => ({
      runtime_instance_id: "runtime-1",
      episode_provenance: { verdict: "provenance_pass", actual_seed: "EXACTSEED" }
    }),
    close: async () => ({ code: 0, signal: null })
  };
  const reset = await handleReferenceDriverRequest(session, {
    command: "reset", request_id: "transport-1", seed: "EXACTSEED"
  });
  assert.equal(reset.snapshot.snapshot_id, "snapshot-EXACTSEED");
  const step = await handleReferenceDriverRequest(session, {
    command: "step",
    request_id: "transport-2",
    mutation_request_id: "mutation-1",
    expected_snapshot_id: "snapshot-current",
    bound_action_id: "bound-action-1"
  });
  assert.equal(step.receipt.delivery, "delivered");
  assert.deepEqual(calls, [{
    requestId: "mutation-1",
    expectedSnapshotId: "snapshot-current",
    boundActionId: "bound-action-1"
  }]);
  const identity = await handleReferenceDriverRequest(session, {
    command: "episode_identity", request_id: "transport-3"
  });
  assert.equal(identity.identity.episode_provenance.verdict, "provenance_pass");
});

test("Reference delivery observes settling without retrying the mutation", async () => {
  const observed = [
    { snapshot_id: "before", status: "settling" },
    { snapshot_id: "after", status: "interactive" }
  ];
  let polls = 0;
  const receipt = await settleReferenceReceipt({
    receipt: {
      request_id: "mutation-1",
      delivery: "delivered",
      successor: { snapshot_id: "before", status: "settling" }
    },
    expectedSnapshotId: "before",
    observe: async () => observed[polls++],
    child: { exitCode: null, signalCode: null },
    timeoutMs: 50,
    pollIntervalMs: 0
  });
  assert.equal(receipt.delivery, "delivered");
  assert.equal(receipt.request_id, "mutation-1");
  assert.equal(receipt.successor.snapshot_id, "after");
  assert.equal(receipt.successor_observation, "driver_observed_after_delivery");
  assert.equal(polls, 2);
});

test("Reference runtimes receive an isolated loopback endpoint", async () => {
  const first = new URL(await allocateReferenceEndpoint());
  const second = new URL(await allocateReferenceEndpoint());
  assert.equal(first.hostname, "127.0.0.1");
  assert.equal(second.hostname, "127.0.0.1");
  assert.notEqual(first.port, "0");
  assert.notEqual(second.port, "0");
});

test("reference handoff requires fresh exact-runtime unheld evidence after SDK close", async () => {
  const { releaseReferenceController } = await import("../src/shipped-player-environment.mjs");
  const calls = [];
  const controller = { close: async () => { calls.push("close"); } };
  const state = { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
    runtime_instance_id: "runtime-a", clients: [] };
  const fetchImpl = async (url) => {
    calls.push(url);
    return { ok: true, json: async () => state };
  };
  const result = await releaseReferenceController({ controller, endpoint: "http://127.0.0.1:1234",
    expectedRuntimeInstanceId: "runtime-a", fetchImpl });
  assert.deepEqual(calls, ["close", "http://127.0.0.1:1234/api/player-environment/controller"]);
  assert.equal(result.controller, null);
  assert.equal(result.basis, "fresh_control_observation_after_close");
  for (const invalid of [{ ...state, runtime_instance_id: "replacement" },
    { ...state, controller: { client_session_id: "still-held" } },
    { ...state, clients: null }, { ...state, clients: [{}] },
    { ...state, extra: true }, {}]) {
    await assert.rejects(releaseReferenceController({ controller,
      endpoint: "http://127.0.0.1:1234", expectedRuntimeInstanceId: "runtime-a",
      fetchImpl: async () => ({ ok: true, json: async () => invalid })
    }), /release_unconfirmed|strict decoding/);
  }
  await assert.rejects(releaseReferenceController({ controller,
    endpoint: "http://127.0.0.1:1234", expectedRuntimeInstanceId: "runtime-a",
    fetchImpl: async () => { throw new Error("network unavailable"); }
  }), /network unavailable/);
});

test("explicit character setup chooses desired visible character before Embark and verifies actual run", async () => {
  const { chooseReferenceBootstrapAction, verifyReferenceCharacter } = await import("../src/shipped-player-environment.mjs");
  const desired = { character_id: "DEFECT", entity_id: "c2", is_locked: false, is_enabled: true, is_selected: false };
  const select = { verb: "select", subject_referent_id: "c2", label: "Select localized name" };
  const embark = { verb: "activate", label: "Embark" };
  const decrease = { verb: "activate", label: "Decrease Ascension" };
  const snapshot = { status: "interactive", interaction: { kind: "character_select", content: { surface: { characters: [desired], ascension: 1 } } }, bound_actions: { status: "complete", actions: [embark, select, decrease] } };
  const options = { characterId: "DEFECT", ascension: 0 };
  assert.equal(chooseReferenceBootstrapAction(snapshot, options), select);
  desired.is_selected = true;
  assert.equal(chooseReferenceBootstrapAction(snapshot, options), decrease);
  snapshot.interaction.content.surface.ascension = null;
  assert.equal(chooseReferenceBootstrapAction(snapshot, options), embark);
  desired.is_locked = true;
  assert.throws(() => chooseReferenceBootstrapAction(snapshot, options), /unavailable/);
  assert.throws(() => verifyReferenceCharacter({ persistent: { content: { player: { character_definition_id: "IRONCLAD" }, run: { ascension: 0 } } } }, options), /mismatch/);
  verifyReferenceCharacter({ persistent: { content: { player: { character_definition_id: "DEFECT" }, run: { ascension: 0 } } } }, options);
});


test("handoff queries the production controller route over HTTP with the released SDK", async () => {
  const { createServer } = await import("node:http");
  const { readFileSync } = await import("node:fs");
  const { once } = await import("node:events");
  const { releaseReferenceController } = await import("../src/shipped-player-environment.mjs");
  const route = "/api/player-environment/controller";
  const router = readFileSync(new URL("../../connector/host/ConnectorMod.cs", import.meta.url), "utf8");
  assert.ok(router.includes(`path == "${route}"`));
  let closed = false, queries = 0;
  const server = createServer((request, response) => {
    if (request.method !== "GET" || request.url !== route) { response.writeHead(404).end(); return; }
    queries++;
    assert.equal(closed, true);
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify({ protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
      runtime_instance_id: "native-1", clients: [{ client_session_id: "s1", client_instance_id: "i1" }] }));
  });
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  try {
    const endpoint = `http://127.0.0.1:${server.address().port}`;
    const receipt = await releaseReferenceController({ controller: { close: async () => { closed = true; } },
      endpoint, expectedRuntimeInstanceId: "native-1" });
    assert.equal(receipt.controller, null); assert.equal(queries, 1);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});
