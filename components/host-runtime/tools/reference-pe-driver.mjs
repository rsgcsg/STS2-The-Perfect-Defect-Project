#!/usr/bin/env node
import readline from "node:readline";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { discoverGameDirectory, resolveInstallation } from "../src/game-installation.mjs";
import { readProjectIdentity } from "../src/project-identity.mjs";
import { ShippedPlayerEnvironmentSession } from "../src/shipped-player-environment.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LOCAL = path.join(ROOT, ".local");

function option(args, name, fallback = null) {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
}

function write(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

export async function handleReferenceDriverRequest(session, request) {
  const requestId = request?.request_id ?? null;
  switch (request?.command) {
    case "reset": {
      const snapshot = await session.reset(request.seed);
      return {
        type: "reset_result",
        request_id: requestId,
        snapshot,
        runtime_identity: session.lastIdentity
      };
    }
    case "observe":
      return { type: "observe_result", request_id: requestId, snapshot: await session.observe() };
    case "read":
      return {
        type: "read_result",
        request_id: requestId,
        read: await session.read({
          readId: request.read_id,
          expectedSnapshotId: request.expected_snapshot_id
        })
      };
    case "step":
      return {
        type: "step_result",
        request_id: requestId,
        receipt: await session.submit({
          requestId: request.mutation_request_id,
          expectedSnapshotId: request.expected_snapshot_id,
          boundActionId: request.bound_action_id
        })
      };
    case "episode_identity":
      return {
        type: "episode_identity_result",
        request_id: requestId,
        identity: await session.provenance()
      };
    case "close":
      return { type: "close_result", request_id: requestId, exit: await session.close() };
    default:
      throw new Error(`Unsupported driver command: ${String(request?.command)}`);
  }
}

async function main() {
  const args = process.argv.slice(2);
  const gameDirectory = option(args, "--game-dir", discoverGameDirectory());
  if (gameDirectory == null) throw new Error("Could not locate STS2; set --game-dir or STS2_GAME_DIR.");
  const session = new ShippedPlayerEnvironmentSession({
    installation: resolveInstallation(gameDirectory),
    localRoot: LOCAL,
    evidenceRoot: path.join(LOCAL, "evidence"),
    templateId: option(args, "--template", "vanilla-clean"),
    nativeSpeed: option(args, "--native-speed", null),
    displayMode: option(args, "--display-mode", "headless"),
    endpoint: option(args, "--endpoint"),
    timeoutMs: Number(option(args, "--timeout-ms", "90000")),
    requestTimeoutMs: Number(option(args, "--request-timeout-ms", "30000")),
    experimentalBuildAcknowledged: args.includes("--experimental-build"),
    experimentalConnectorAcknowledged: args.includes("--experimental-connector")
  });
  let closed = false;
  write({
    type: "ready",
    protocol: "sts2.headless/reference-player-environment-driver-1",
    headless: readProjectIdentity(ROOT),
    host_kind: "shipped_reference"
  });

  const input = readline.createInterface({ input: process.stdin });
  let queue = Promise.resolve();
  input.on("line", (line) => {
    queue = queue.then(async () => {
      let request;
      try {
        request = JSON.parse(line);
        const response = await handleReferenceDriverRequest(session, request);
        write(response);
        if (request.command === "close") {
          closed = true;
          input.close();
        }
      } catch (error) {
        write({
          type: "error",
          request_id: request?.request_id ?? null,
          code: "driver_request_failed",
          message: error instanceof Error ? error.message : String(error)
        });
      }
    });
  });
  input.on("close", async () => {
    await queue;
    if (!closed) await session.close().catch(() => null);
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
