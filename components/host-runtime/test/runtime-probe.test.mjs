import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { createServer } from "node:http";
import { fork } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import {
  listGameProcesses,
  requestHostProvenance,
  requestHostShutdown,
  stopChild,
  resolveExperimentalConnectorCanary,
  withExplicitConnectorCanary
} from "../src/runtime-probe.mjs";
import {
  GAME_CANARY_ENVIRONMENT_VARIABLE,
  SOURCE_CANARY_ENVIRONMENT_VARIABLE
} from "../src/connector-endpoint.mjs";

test("Windows process discovery recognizes only the shipped executable", () => {
  const spawnProcess = () => ({
    status: 0,
    stdout: [
      '"SlayTheSpire2.exe","4242","Console","1","123,456 K"',
      '"SlayTheSpire2Helper.exe","4343","Console","1","12,345 K"',
      '"node.exe","4444","Console","1","12,345 K"'
    ].join("\r\n"),
    stderr: "",
    error: null
  });

  assert.deepEqual(listGameProcesses("win32", { spawnProcess, failClosed: true }), [
    '"SlayTheSpire2.exe","4242","Console","1","123,456 K"'
  ]);
});

test("strict process discovery fails closed when Windows enumeration fails", () => {
  const spawnProcess = () => ({
    status: 1,
    stdout: "",
    stderr: "access denied",
    error: null
  });

  assert.deepEqual(listGameProcesses("win32", { spawnProcess }), []);
  assert.throws(
    () => listGameProcesses("win32", { spawnProcess, failClosed: true }),
    /Could not enumerate Windows STS2 processes/u
  );
});

test("experimental authority binds a verified artifact and known game candidate", () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), "sts2-headless-canary-"));
  const modsDir = path.join(directory, "mods");
  mkdirSync(modsDir);
  writeFileSync(path.join(modsDir, "STS2_MCP.dll"), "candidate");
  const artifactSha = createHash("sha256").update("candidate").digest("hex");
  writeFileSync(path.join(modsDir, "STS2_MCP.identity"), JSON.stringify({
    source_revision: "a".repeat(40),
    artifact_sha256: artifactSha
  }));

  assert.equal(resolveExperimentalConnectorCanary({
    installation: { mods_dir: modsDir },
    compatibility: { status: "known_experimental", support_id: "game-candidate" },
    acknowledged: false
  }), null);
  assert.deepEqual(resolveExperimentalConnectorCanary({
    installation: { mods_dir: modsDir },
    compatibility: { status: "known_experimental", support_id: "game-candidate" },
    acknowledged: true
  }), {
    game_id: "game-candidate",
    source_revision: "a".repeat(40),
    artifact_sha256: artifactSha
  });
  rmSync(directory, { recursive: true, force: true });
});

test("launch authority ignores ambient canaries and admits only explicit exact values", () => {
  const ambient = {
    KEEP_ME: "yes",
    [GAME_CANARY_ENVIRONMENT_VARIABLE]: "ambient-game",
    [SOURCE_CANARY_ENVIRONMENT_VARIABLE]: "b".repeat(40)
  };
  assert.deepEqual(withExplicitConnectorCanary(ambient, null), { KEEP_ME: "yes" });
  assert.deepEqual(withExplicitConnectorCanary(ambient, {
    game_id: "darwin-arm64-v0.111.0-41cef1ea",
    source_revision: "a".repeat(40)
  }), {
    KEEP_ME: "yes",
    [GAME_CANARY_ENVIRONMENT_VARIABLE]: "darwin-arm64-v0.111.0-41cef1ea",
    [SOURCE_CANARY_ENVIRONMENT_VARIABLE]: "a".repeat(40)
  });
  assert.throws(() => withExplicitConnectorCanary({}, {
    game_id: null,
    source_revision: "not-a-revision"
  }), /exact Git revision/u);
});

test("Host control routes share authentication without sharing response semantics", async (context) => {
  const calls = [];
  const server = createServer(async (req, res) => {
    let text = "";
    for await (const chunk of req) text += chunk;
    calls.push({ url: req.url, body: JSON.parse(text) });
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ status: req.url.endsWith("/provenance") ? "seed_observed" : "shutdown_requested" }));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  context.after(() => { server.closeAllConnections(); server.close(); });
  const common = {
    endpoint: `http://127.0.0.1:${server.address().port}`,
    hostControlToken: "a".repeat(64),
    expectedRuntimeInstanceId: "runtime-1"
  };
  assert.equal((await requestHostProvenance(common)).status, "observed");
  assert.equal((await requestHostShutdown(common)).status, "requested");
  assert.deepEqual(calls.map((call) => call.url), [
    "/api/host-control/provenance",
    "/api/host-control/shutdown"
  ]);
  assert.deepEqual(calls[0].body, {
    expected_runtime_instance_id: "runtime-1",
    host_control_token: "a".repeat(64)
  });
  assert.deepEqual(calls[1].body, calls[0].body);
});

test("fresh shutdown preserves HTTP rejection and transport failure without retry", async context => {
  let requests = 0;
  const server = createServer((req, res) => {
    requests++;
    if (requests === 1) {
      res.writeHead(403, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: { code: "host_control_denied" } }));
    } else req.socket.destroy();
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  context.after(() => { server.closeAllConnections(); server.close(); });
  const common = { endpoint: `http://127.0.0.1:${server.address().port}`,
    hostControlToken: "a".repeat(64), expectedRuntimeInstanceId: "runtime-1" };
  const denied = await requestHostShutdown(common);
  assert.equal(denied.status, "rejected");
  assert.equal(denied.http_status, 403);
  assert.equal(denied.error, "host_control_denied");
  const failed = await requestHostShutdown(common);
  assert.equal(failed.status, "transport_error");
  assert.equal(failed.error_code, "ECONNRESET");
  assert.equal(requests, 2);
});

test("shutdown reaches the child once after synchronous work hides a closed pooled socket", { timeout: 5000 }, async context => {
  const child = fork(fileURLToPath(new URL("../tools/test-fixtures/host-control-stale-server.mjs", import.meta.url)),
    [], { stdio: ["ignore", "ignore", "pipe", "ipc"] });
  context.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL"); await once(child, "exit");
    }
  });
  const events = [];
  child.on("message", event => events.push(event));
  const [ready] = await once(child, "message");
  const params = { endpoint: ready.endpoint, hostControlToken: "f".repeat(64), expectedRuntimeInstanceId: "fixture-runtime" };
  assert.equal((await requestHostProvenance(params)).status, "observed");
  await new Promise(resolve => setImmediate(resolve));
  // The independent child closes socket 1 while this parent's I/O is blocked.
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 600);
  const result = await stopChild(child, params);
  assert.equal(result.host_shutdown.status, "requested");
  assert.equal(result.host_shutdown.http_status, 200);
  assert.equal(result.code, 0); assert.equal(result.signal, null); assert.equal(result.forced, false);
  assert.ok(events.some(event => event.closed_idle_socket === 1));
  assert.deepEqual(events.filter(event => event.request).map(event => [event.request, event.socket]),
    [["/api/host-control/provenance", 1], ["/api/host-control/shutdown", 2]]);
});

test("Host provenance remains unavailable without exact process-local credentials", async () => {
  const result = await requestHostProvenance({
    endpoint: "http://127.0.0.1:15526",
    hostControlToken: null,
    expectedRuntimeInstanceId: "runtime-1"
  });
  assert.equal(result.status, "unavailable");
  assert.equal(result.error, "host_control_not_configured");
});

test("native UI preparation requires an isolated profile and preserves profile arguments", async () => {
  const { shippedDisplayArguments } = await import("../src/runtime-probe.mjs");
  assert.deepEqual(shippedDisplayArguments(), ["--headless", "--verbose"]);
  assert.throws(() => shippedDisplayArguments("native-ui"), /requires_isolated/);
  assert.throws(() => shippedDisplayArguments("other"), /unsupported/);
  assert.throws(() => shippedDisplayArguments("native-ui", { steam: "disabled_before_platform_initialization", args: [] }), /requires_isolated/);
  const { isolatedProfileLaunch } = await import("../src/profile-isolation.mjs");
  const root = mkdtempSync(path.join(os.tmpdir(), "sts2-native-ui-profile-"));
  try {
    const profile = isolatedProfileLaunch(root, "prepare");
    assert.deepEqual(shippedDisplayArguments("native-ui", profile), ["--verbose", "--force-steam=off", "--clientId=1"]);
    assert.throws(() => shippedDisplayArguments("native-ui", { ...profile, environment: {} }), /requires_isolated/);
    assert.throws(() => shippedDisplayArguments("native-ui", { ...profile, expected_user_data_root: os.homedir() }), /requires_isolated/);
    const { shippedRuntimeLaunch } = await import("../src/runtime-probe.mjs");
    assert.throws(() => shippedRuntimeLaunch({}, { displayMode: "native-ui", launchProfile: profile, extraEnvironment: { HOME: os.homedir() } }), /environment_override/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
