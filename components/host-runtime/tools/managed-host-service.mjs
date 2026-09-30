#!/usr/bin/env node
import { lstatSync, openSync, closeSync, fstatSync, unlinkSync,
  writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { discoverGameDirectory, readDiskIdentity, resolveInstallation } from "../src/game-installation.mjs";
import { startManagedHostService } from "../src/managed-host-service.mjs";
import { startManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { readProjectIdentity } from "../src/project-identity.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function option(args, name, fallback = null) {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
}

function privateParent(file) {
  const parent = lstatSync(path.dirname(path.resolve(file)));
  if (!parent.isDirectory() || (process.platform !== "win32" && (parent.mode & 0o077) !== 0)) {
    throw new Error("Managed service attachment parent must be a private directory (mode 0700).");
  }
}

function writeAttachment(file, value) {
  privateParent(file);
  const descriptor = openSync(file, "wx", 0o600);
  try {
    writeFileSync(descriptor, `${JSON.stringify(value)}\n`);
    const stat = fstatSync(descriptor);
    return { file, device: stat.dev, inode: stat.ino };
  } finally {
    closeSync(descriptor);
  }
}

function removeOwnAttachment(record) {
  if (!record) return;
  try {
    const stat = lstatSync(record.file);
    if (stat.dev === record.device && stat.ino === record.inode) unlinkSync(record.file);
  } catch (error) {
    if (error.code !== "ENOENT") process.stderr.write("Managed service attachment cleanup failed.\n");
  }
}

const args = process.argv.slice(2);
const candidateDirectory = option(args, "--candidate");
const clientAttachment = option(args, "--client-attachment");
const managerAttachment = option(args, "--manager-attachment");
if (!candidateDirectory || !clientAttachment || !managerAttachment
  || path.resolve(clientAttachment) === path.resolve(managerAttachment)) {
  throw new Error("Managed service requires --candidate and distinct --client-attachment/--manager-attachment paths.");
}
const gameDirectory = option(args, "--game-dir") ?? discoverGameDirectory();
if (!gameDirectory) throw new Error("Could not locate STS2; set --game-dir or STS2_GAME_DIR.");
const requestTimeoutMs = Number(option(args, "--timeout-ms", "10000"));
const port = Number(option(args, "--port", "0"));
const host = option(args, "--host", "127.0.0.1");
const hostIdentity = readProjectIdentity(ROOT);
const started = await startManagedPlayerEnvironmentSession({
  root: ROOT, candidateDirectory,
  diskIdentity: readDiskIdentity(resolveInstallation(gameDirectory)),
  character: option(args, "--character", "Ironclad"),
  requestTimeoutMs,
  quietDiagnostics: args.includes("--quiet-diagnostics")
});
const service = await startManagedHostService(started, {
  hostIdentity, host, port, requestTimeoutMs
});
let clientRecord;
let managerRecord;
try {
  const common = {
    schema: "sts2.host-runtime/managed-service-attachment-1",
    endpoint: service.endpoint,
    service_instance_id: service.serviceInstanceId,
    host_identity: service.ready().host_identity
  };
  clientRecord = writeAttachment(clientAttachment,
    { ...common, role: "client", token: service.clientToken });
  managerRecord = writeAttachment(managerAttachment,
    { ...common, role: "manager", token: service.managerToken });
} catch (error) {
  removeOwnAttachment(clientRecord);
  removeOwnAttachment(managerRecord);
  await service.close({ force: true });
  throw error;
}
service.server.once("close", () => {
  removeOwnAttachment(clientRecord);
  removeOwnAttachment(managerRecord);
  if (service.cleanupError != null) {
    process.stderr.write("Managed service native close unconfirmed.\n");
    process.exitCode = 1;
  }
});
process.stdout.write(`${JSON.stringify({
  ...service.ready(), endpoint: service.endpoint
})}\n`);
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    void service.close({ force: true }).then(
      () => { process.exitCode = 0; },
      () => { process.stderr.write("Managed service cleanup unconfirmed.\n"); process.exitCode = 1; }
    );
  });
}
