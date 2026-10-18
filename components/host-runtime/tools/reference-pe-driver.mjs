#!/usr/bin/env node
import readline from "node:readline";
import path from "node:path";
import { accessSync, constants, lstatSync, realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { discoverGameDirectory, resolveInstallation } from "../src/game-installation.mjs";
import { readProjectIdentity } from "../src/project-identity.mjs";
import { ShippedPlayerEnvironmentSession } from "../src/shipped-player-environment.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function ordinaryDirectory(directory) {
  const stat = lstatSync(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink() || realpathSync(directory) !== directory) {
    throw new Error("Reference state root must be an ordinary canonical directory.");
  }
}

export function resolveReferenceStateRoot(args, { packageRoot = ROOT } = {}) {
  const indices = args.flatMap((value, index) => value === "--local-root" ? [index] : []);
  if (indices.length > 1) throw new Error("Reference state root must be supplied once.");
  let localRoot;
  let mode;
  if (indices.length === 0) {
    if (readProjectIdentity(packageRoot).distribution_kind !== "git_checkout") {
      throw new Error("Installed reference drivers require an external --local-root.");
    }
    localRoot = path.join(packageRoot, ".local");
    mode = "legacy_source_checkout";
    if (lstatSync(localRoot, { throwIfNoEntry: false })) ordinaryDirectory(localRoot);
  } else {
    localRoot = args[indices[0] + 1];
    if (typeof localRoot !== "string" || !path.isAbsolute(localRoot)
        || path.resolve(localRoot) !== localRoot || localRoot === path.parse(localRoot).root) {
      throw new Error("Reference state root must be an explicit canonical absolute path.");
    }
    ordinaryDirectory(packageRoot);
    ordinaryDirectory(localRoot);
    const inside = (parent, child) => {
      const relative = path.relative(parent, child);
      return relative === "" || (!relative.startsWith(`..${path.sep}`)
        && relative !== ".." && !path.isAbsolute(relative));
    };
    if (inside(packageRoot, localRoot) || inside(localRoot, packageRoot)) {
      throw new Error("Reference state root must be disjoint from the immutable package.");
    }
    accessSync(localRoot, constants.W_OK);
    mode = "external";
  }
  for (const child of ["profile-templates", "profiles", "evidence"]) {
    const directory = path.join(localRoot, child);
    if (lstatSync(directory, { throwIfNoEntry: false })) ordinaryDirectory(directory);
  }
  const evidenceRoot = path.join(localRoot, "evidence");
  return { localRoot, evidenceRoot, stateRootReadback: {
    schema: "sts2.host-runtime/reference-state-root-1", mode,
    local_root: localRoot, evidence_root: evidenceRoot
  } };
}

export function referenceDriverOptions(args, { packageRoot = ROOT } = {}) {
  const state = resolveReferenceStateRoot(args, { packageRoot });
  const gameDirectory = option(args, "--game-dir") ?? discoverGameDirectory();
  const expectedTupleJson = option(args, "--expected-installed-tuple", null);
  if (expectedTupleJson != null && expectedTupleJson.length > 4096) {
    throw new Error("Expected native tuple exceeds its bound.");
  }
  if (gameDirectory == null) throw new Error("Could not locate STS2; set --game-dir or STS2_GAME_DIR.");
  return { stateRootReadback: state.stateRootReadback, sessionOptions: {
    installation: resolveInstallation(gameDirectory),
    localRoot: state.localRoot,
    evidenceRoot: state.evidenceRoot,
    templateId: option(args, "--template", "vanilla-clean"),
    characterId: option(args, "--character", null),
    ascension: Number(option(args, "--ascension", "0")),
    nativeSpeed: option(args, "--native-speed", "instant"),
    displayMode: option(args, "--display-mode", "headless"),
    expectedInstalledTuple: expectedTupleJson == null ? null : JSON.parse(expectedTupleJson),
    endpoint: option(args, "--endpoint"),
    timeoutMs: Number(option(args, "--timeout-ms", "90000")),
    requestTimeoutMs: Number(option(args, "--request-timeout-ms", "30000")),
    experimentalBuildAcknowledged: args.includes("--experimental-build"),
    experimentalConnectorAcknowledged: args.includes("--experimental-connector")
  } };
}

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
    case "release_bootstrap":
      if (Object.keys(request).some(key => !["command", "request_id", "expected_runtime_instance_id"].includes(key)))
        throw new Error("Invalid bootstrap handoff fields.");
      return { type: "release_bootstrap_result", request_id: requestId,
        handoff: await session.releaseBootstrap(request.expected_runtime_instance_id) };
    case "cancel":
      return { type: "cancel_result", request_id: requestId, exit: await session.close() };
    case "close":
      return { type: "close_result", request_id: requestId, exit: await session.close() };
    default:
      throw new Error(`Unsupported driver command: ${String(request?.command)}`);
  }
}

export function serveReferenceDriver(session, { inputStream = process.stdin,
  emit = write, abortController = new AbortController() } = {}) {
  // EOF/explicit close interrupts the factory before waiting serialized work.
  // It never acquires another controller or retries a preparation mutation.
  const input = readline.createInterface({ input: inputStream });
  let queue = Promise.resolve();
  let closed = false;
  const abort = () => abortController.abort(new Error("reference_driver_closed"));
  session.options.signal = abortController.signal;
  input.on("line", line => {
    let request;
    try { request = JSON.parse(line); }
    catch { queue = queue.then(() => emit({ type: "error", request_id: null, code: "driver_request_failed", message: "Invalid JSON request." })); return; }
    if (request?.command === "cancel" || request?.command === "close") abort();
    queue = queue.then(async () => {
      try {
        const response = await handleReferenceDriverRequest(session, request);
        emit(response);
        if (request.command === "close" || request.command === "cancel") { closed = true; input.close(); }
      } catch (error) {
        emit({ type: "error", request_id: request?.request_id ?? null,
          code: "driver_request_failed", message: error instanceof Error ? error.message : String(error),
          ...(error?.host_exit == null ? {} : { host_exit: error.host_exit }) });
      }
    });
  });
  return new Promise((resolve, reject) => input.on("close", () => {
    abort();
    void queue.then(async () => {
      if (!closed) await session.close();
      resolve();
    }).catch(reject);
  }));
}

async function main() {
  const args = process.argv.slice(2);
  const configuration = referenceDriverOptions(args);
  const session = new ShippedPlayerEnvironmentSession(configuration.sessionOptions);
  write({
    type: "ready",
    protocol: "sts2.headless/reference-player-environment-driver-1",
    headless: readProjectIdentity(ROOT),
    host_kind: "shipped_reference",
    bootstrap_handoff: "sts2.host-runtime/reference-bootstrap-handoff-1",
    state_root: configuration.stateRootReadback
  });
  await serveReferenceDriver(session);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
