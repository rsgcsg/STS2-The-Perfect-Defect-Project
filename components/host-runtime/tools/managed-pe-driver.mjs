#!/usr/bin/env node
import path from "node:path";
import { fileURLToPath } from "node:url";
import { discoverGameDirectory, readDiskIdentity, resolveInstallation } from "../src/game-installation.mjs";
import { ManagedPeDriverSession } from "../src/managed-pe-driver-session.mjs";
import { serveManagedPeDriver } from "../src/managed-pe-driver-loop.mjs";
import { startManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { readProjectIdentity } from "../src/project-identity.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function option(args, name, fallback = null) {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
}

function write(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

const args = process.argv.slice(2);
const candidateDirectory = option(args, "--candidate");
if (candidateDirectory == null) throw new Error("managed-pe-driver requires --candidate <prepared-directory>.");
const gameDirectory = option(args, "--game-dir", discoverGameDirectory());
if (gameDirectory == null) throw new Error("Could not locate STS2; set --game-dir or STS2_GAME_DIR.");
const requestTimeoutMs = Number(option(args, "--timeout-ms", "10000"));
const started = await startManagedPlayerEnvironmentSession({
  root: ROOT,
  candidateDirectory,
  diskIdentity: readDiskIdentity(resolveInstallation(gameDirectory)),
  character: option(args, "--character", "Ironclad"),
  requestTimeoutMs,
  quietDiagnostics: args.includes("--quiet-diagnostics")
});
const driver = new ManagedPeDriverSession(started, { requestTimeoutMs });

write({
  type: "ready",
  protocol: "sts2.headless/managed-player-environment-driver-1",
  headless: readProjectIdentity(ROOT),
  candidate_manifest: started.runtime.manifest,
  exact_game: started.runtime.exactGame,
  candidate_build: started.runtime.build,
  runtime_identity: started.runtime.runtimeIdentity,
  adapter_runtime_instance_id: started.runtime.adapterRuntimeInstanceId,
  environment_fingerprint: started.environmentFingerprint
});

serveManagedPeDriver(driver);
