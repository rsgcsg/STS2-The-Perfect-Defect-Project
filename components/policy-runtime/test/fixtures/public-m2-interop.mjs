// Actual SDK/HTTP/Runtime/child/evidence seam. HTTP world and Model backend are synthetic.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createServer } from "node:http";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { publicManifest, publicSnapshot, publicCapabilities } from "./public-observation-fixture.mjs";

const [runtimePath, sdkPath, python, childPath, directory, profilePath, scenario = "normal"] = process.argv.slice(2);
const { PolicyRuntime, NdjsonPolicyPort, AgentRunEvidence, ConnectorPolicyClient, validatePolicyManifest } =
  await import(pathToFileURL(runtimePath).href);
const { PlayerEnvironmentRestClient } = await import(pathToFileURL(sdkPath).href);
await mkdir(directory, { recursive: true });
const manifest = publicManifest();
manifest.adapter_config = JSON.parse(await readFile(profilePath, "utf8"));
validatePolicyManifest(manifest);
const manifestPath = join(directory, "manifest.json"), tracePath = join(directory, "trace.jsonl");
await writeFile(manifestPath, JSON.stringify(manifest));
let current = publicSnapshot();
let submitted = 0, released = 0, clientInstanceId = "", held = false;
const requests = [];
const server = createServer(async (request, response) => {
  try {
    const chunks = []; for await (const chunk of request) chunks.push(chunk);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : null;
    const path = new URL(request.url, "http://127.0.0.1").pathname;
    requests.push({ path, body });
    const client = { client_session_id: "client-session", client_instance_id: clientInstanceId };
    const lease = { controller_lease_id: "lease", controller_generation: 1,
      client_session_id: "client-session", expires_at: new Date(Date.now() + 60000).toISOString() };
    const control = { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
      runtime_instance_id: "runtime", client };
    let value;
    if (path.endsWith("/capabilities")) value = publicCapabilities();
    else if (path.endsWith("/snapshot")) value = current;
    else if (path.endsWith("/clients/register")) {
      clientInstanceId = body.client_instance_id;
      value = { ...control, client: { ...client, client_instance_id: clientInstanceId }, controller: null };
    } else if (path.endsWith("/controller/acquire") || path.endsWith("/controller/renew")) {
      held = true; value = { ...control, status: "controller_acquired", detail: "fixture", controller: lease };
    } else if (path.endsWith("/controller/release")) {
      held = false; released += 1; value = { ...control, status: "controller_released", detail: "fixture", controller: null };
    } else if (path.endsWith("/actions")) {
      submitted += 1;
      assert.equal(body.expected_snapshot_id, current.snapshot_id);
      assert.ok(current.bound_actions.actions.some(action => action.bound_action_id === body.bound_action_id));
      const delivery = scenario === "unknown" ? "unknown" : "delivered";
      if (delivery === "delivered") current = publicSnapshot(current.sequence + 1);
      value = { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1",
        request_id: body.request_id, delivery,
        action: { bound_action_id: body.bound_action_id, verb: "end_turn", subject_referent_id: null, arguments: [] },
        reason_code: null, detail: null, retry: { allowed: false, reason: "never" },
        successor: delivery === "delivered" ? current : null, attribution: null };
    } else { response.writeHead(404); response.end(); return; }
    response.writeHead(200, { "content-type": "application/json" }); response.end(JSON.stringify(value));
  } catch (error) { response.writeHead(500); response.end(JSON.stringify({ error: String(error) })); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const endpoint = `http://127.0.0.1:${server.address().port}`;
const port = NdjsonPolicyPort.spawn(python, [childPath, manifestPath, tracePath], {
  cwd: join(childPath, "../.."), env: { ...process.env, OMP_NUM_THREADS: "2", MKL_NUM_THREADS: "2" }
});
const child = port.child;
const childClosed = new Promise(resolve => child.once("close", (code, signal) => resolve({ code, signal })));
let runtime;
try {
  const evidence = await AgentRunEvidence.create({ root: directory, runId: "interop-run", policyManifest: manifest,
    runtimeVersion: "fixture", runtimeCodeSha256: "d".repeat(64), mode: "shadow" });
  await evidence.attestAdapter(await port.attest(manifest.adapter));
  runtime = new PolicyRuntime({ manifest,
    connector: new ConnectorPolicyClient(new PlayerEnvironmentRestClient(endpoint, 5000)),
    publicStatefulPolicy: (input, signal, offered) => port.decideV4(input, signal, offered),
    statefulOfferBoundary: "port_write", evidence, mode: "shadow", runId: "interop-run",
    runtimeIdentity: { version: "fixture", code_sha256: "d".repeat(64) },
    autoBudget: { maxSubmissions: 10, maxPolicyCalls: 20, deadlineMs: 30000 }, policyTimeoutMs: 15000 });
  const shadow = await runtime.tick(); assert.equal(shadow.type, "shadow", JSON.stringify(shadow));
  current.observed_at = "2026-10-11T00:00:01Z";
  assert.equal((await runtime.tick()).reason, "snapshot_already_scored");
  current = publicSnapshot(2);
  assert.equal((await runtime.tick()).type, "shadow");
  await runtime.setMode("human"); await runtime.setMode("auto");
  current = publicSnapshot(3, scenario === "oversize" ? 32 : 2);
  const delivered = await runtime.tick();
  if (scenario === "oversize") {
    assert.equal(delivered.type, "not_admitted", JSON.stringify(delivered));
    assert.equal(delivered.reason, "policy_failed");
    assert.equal(submitted, 0);
    assert.equal(runtime.status().mode, "human");
  } else if (scenario === "unknown") {
    assert.equal(delivered.type, "unknown", JSON.stringify(delivered));
    assert.equal(runtime.status().tainted, true);
    await assert.rejects(runtime.setMode("auto"), /tainted/);
    await runtime.tick(); assert.equal(submitted, 1);
  } else {
    assert.equal(delivered.type, "delivered", JSON.stringify(delivered));
    assert.equal((await runtime.tick()).type, "delivered");
    assert.equal(submitted, 2);
  }
  await runtime.stop();
  assert.equal(held, false); assert.ok(released >= (scenario === "oversize" ? 0 : 1));
  const trace = (await readFile(tracePath, "utf8")).trim().split("\n").map(JSON.parse);
  if (scenario === "oversize") {
    assert.deepEqual(trace.slice(0, 2).map(row => row.control.observation_ordinal), [1, 2]);
    assert.equal(trace[2].error, "complete_catalog_binding_mismatch");
    assert.equal(trace[2].advanced, false);
    assert.equal(trace[2].memory_sha256, trace[1].memory_sha256);
  } else {
    assert.deepEqual(trace.slice(0, 3).map(row => row.control.observation_ordinal), [1, 2, 1]);
    assert.deepEqual(trace.slice(0, 3).map(row => row.reset), [true, false, true]);
    assert.equal(trace[0].memory_sha256, trace[2].memory_sha256);
  }
  assert.notEqual(trace[0].memory_sha256, trace[1].memory_sha256);
  if (scenario === "normal") {
    assert.equal(trace[3].control.observation_ordinal, 2);
    assert.equal(trace[3].control.previous_action.receipt.delivery, "delivered");
    assert.equal(trace[3].completion.previous_action_request_id, trace[3].control.previous_action.request_id);
  }
  assert.ok(requests.every(entry => !entry.path.includes("text-menu") && !entry.path.includes("native-logical")));
  const eventBytes = await readFile(join(evidence.directory, "events.jsonl"), "utf8");
  const inputs = eventBytes.trim().split("\n").map(JSON.parse).filter(event => event.kind === "public_decision_input");
  assert.equal(inputs.length, trace.filter(row => !row.error).length);
  assert.ok(inputs.every(event => event.payload.snapshot.bound_actions.actions.length === 2));
  process.stdout.write(JSON.stringify({ evidence: evidence.directory, trace, submitted, released,
    stopped: runtime.status(), generic_snapshot_only: true }) + "\n");
} finally {
  await runtime?.stop(); port.close(); await childClosed;
  await new Promise(resolve => server.close(resolve));
}
