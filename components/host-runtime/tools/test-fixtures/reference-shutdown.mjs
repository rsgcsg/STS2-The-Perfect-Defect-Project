// Synthetic native leaf only. Real Session/CLI, SDK release, signed HTTP stop,
// child wait, output streams and Python-consumer lifetime remain production.
import { spawn } from "node:child_process";
import { appendFileSync, createWriteStream } from "node:fs";
import { createServer } from "node:http";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const directory = process.argv[2];
const runtime = "fixture-runtime";
const token = "fixture-owned-host-token";
const log = value => appendFileSync(path.join(directory, "events.jsonl"), `${JSON.stringify(value)}\n`);

if (process.argv.includes("--native")) {
  const server = createServer(async (request, response) => {
    response.setHeader("content-type", "application/json");
    if (request.method === "GET" && request.url === "/api/player-environment/controller") {
      response.end(JSON.stringify({ protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
        runtime_instance_id: runtime, clients: [], controller: null }));
      return;
    }
    let raw = "";
    for await (const part of request) raw += part;
    const body = JSON.parse(raw);
    if (request.method !== "POST" || request.url !== "/api/host-control/shutdown"
      || body.expected_runtime_instance_id !== runtime || body.host_control_token !== token) {
      response.statusCode = 409; response.end('{"error":"fixture_shutdown_identity"}'); return;
    }
    log({ event: "host_shutdown", runtime_instance_id: runtime, http_status: 200 });
    response.setHeader("connection", "close");
    response.end(JSON.stringify({ status: "shutdown_requested", runtime_instance_id: runtime }));
    // The actual child exits after the acknowledgement, while production's
    // 10s wait is pending. This is a synthetic process seam, not a game delay.
    setTimeout(() => server.close(() => log({ event: "native_server_closed" })), 100);
  });
  server.listen(0, "127.0.0.1", () => {
    const endpoint = `http://127.0.0.1:${server.address().port}`;
    log({ event: "native_started", pid: process.pid, endpoint });
    process.stdout.write(`${JSON.stringify({ endpoint })}\n`);
  });
} else {
  const { EnvironmentControllerSession, PlayerEnvironmentRestClient } = await import("@rsgcsg/sts2-connector-client");
  const { ShippedPlayerEnvironmentSession, ownReferenceEpisodeLifecycle,
    releaseReferenceController } = await import("../../src/shipped-player-environment.mjs");
  const { serveReferenceDriver } = await import("../reference-pe-driver.mjs");
  const session = new ShippedPlayerEnvironmentSession({}, { startEpisode: async ({ signal }) => {
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), directory, "--native"],
      { stdio: ["ignore", "pipe", "pipe"] });
    child.on("exit", (code, signal) => log({ event: "native_child_exit", code, signal }));
    const lines = readline.createInterface({ input: child.stdout });
    const endpoint = await new Promise((resolve, reject) => {
      child.once("error", reject);
      lines.once("line", line => { lines.close(); resolve(JSON.parse(line).endpoint); });
    });
    const client = new PlayerEnvironmentRestClient(endpoint, 2000);
    const controller = new EnvironmentControllerSession(client, { productId: "fixture-reference",
      productName: "Reference process fixture", productVersion: "fixture" });
    const streams = [createWriteStream(path.join(directory, "native.stdout.log")),
      createWriteStream(path.join(directory, "native.stderr.log"))];
    child.stdout.pipe(streams[0]); child.stderr.pipe(streams[1]);
    const lifecycle = ownReferenceEpisodeLifecycle({ child, endpoint, hostControlToken: token,
      controller: () => controller, runtimeInstance: () => runtime, streams, signal });
    return {
      snapshot: { snapshot_id: "fixture-initial" },
      identity: { host: { runtime_instance_id: runtime } },
      releaseController: () => releaseReferenceController({ controller, endpoint,
        expectedRuntimeInstanceId: runtime }),
      close: lifecycle.close
    };
  } });
  process.stdout.write(`${JSON.stringify({ type: "ready", protocol: "sts2.headless/reference-player-environment-driver-1",
    host_kind: "shipped_reference", bootstrap_handoff: "sts2.host-runtime/reference-bootstrap-handoff-1" })}\n`);
  await serveReferenceDriver(session);
  log({ event: "driver_finished" });
}
