#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, isAbsolute, resolve } from "node:path";
import process from "node:process";
import { PlayerEnvironmentRestClient } from "@rsgcsg/sts2-connector-client";
import { POLICY_RUNTIME_VERSION, validatePolicyManifest, type RuntimeMode } from "./contracts.js";
import { ConnectorPolicyClient } from "./connector.js";
import { AgentRunEvidence, canonicalJson } from "./evidence.js";
import { NdjsonPolicyPort } from "./policy-port.js";
import { PolicyRuntime } from "./runtime.js";
import { startPolicyRuntimeHttpServer } from "./server.js";

interface CliOptions {
  manifestPath: string;
  adapterCommand: string;
  adapterArgs: string[];
  adapterCwd?: string;
  connectorEndpoint: string;
  listenPort: number;
  evidenceRoot: string;
  mode: RuntimeMode;
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  const manifestPath = resolve(options.manifestPath);
  const manifest = validatePolicyManifest(JSON.parse(await readFile(manifestPath, "utf8")));
  const artifactPath = isAbsolute(manifest.artifact.path)
    ? manifest.artifact.path
    : resolve(dirname(manifestPath), manifest.artifact.path);
  const artifact = await readFile(artifactPath).catch(() => {
    throw new Error(`policy artifact is unavailable: ${artifactPath}`);
  });
  const artifactSha256 = createHash("sha256").update(artifact).digest("hex");
  if (artifactSha256 !== manifest.artifact.sha256) throw new Error("policy artifact SHA-256 differs from Policy Manifest");
  const policyManifestSha256 = createHash("sha256").update(canonicalJson(manifest)).digest("hex");
  const runtimeCodeSha256 = await codeDigest(dirname(fileURLToPath(import.meta.url)));

  await mkdir(resolve(options.evidenceRoot), { recursive: true });
  const evidence = await AgentRunEvidence.create({
    root: resolve(options.evidenceRoot),
    policyManifest: manifest,
    runtimeVersion: POLICY_RUNTIME_VERSION,
    runtimeCodeSha256,
    mode: options.mode
  });
  let port: NdjsonPolicyPort | undefined;
  let runtime: PolicyRuntime | undefined;
  let service: Awaited<ReturnType<typeof startPolicyRuntimeHttpServer>> | undefined;
  let shuttingDown = false;
  let resolveExit: (() => void) | undefined;
  const exit = new Promise<void>((resolvePromise) => { resolveExit = resolvePromise; });
  const shutdown = async (): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    try { await runtime?.stop(); } finally {
      try { await service?.close(); } finally {
        port?.close();
        resolveExit?.();
      }
    }
  };
  try {
    port = NdjsonPolicyPort.spawn(options.adapterCommand, options.adapterArgs, {
      cwd: options.adapterCwd ? resolve(options.adapterCwd) : undefined,
      env: process.env
    });
    const adapter = await port.attest(manifest.adapter);
    await evidence.attestAdapter(adapter);
    const connector = new ConnectorPolicyClient(
      new PlayerEnvironmentRestClient(options.connectorEndpoint, 5_000),
      { productVersion: POLICY_RUNTIME_VERSION }
    );
    runtime = new PolicyRuntime({
      manifest,
      connector,
      policy: (input, signal) => port!.decide(input, signal),
      mode: options.mode,
      runId: evidence.runId,
      evidence,
      runtimeIdentity: { version: POLICY_RUNTIME_VERSION, code_sha256: runtimeCodeSha256 }
    });
    service = await startPolicyRuntimeHttpServer(runtime, {
      port: options.listenPort,
      autoDrive: true,
      deferAutoDrive: true,
      onStopped: shutdown
    });
  } catch (error) {
    try {
      if (runtime) await runtime.stop();
      else await evidence.finalize({ status: "stopped", tainted: false, mode: "human" });
    } finally {
      port?.close();
    }
    throw error;
  }

  process.stdout.write(`${JSON.stringify({
    schema: "sts2.policy-runtime/startup-1",
    address: service.address,
    run_id: evidence.runId,
    manifest_id: manifest.manifest_id,
    policy_artifact_sha256: artifactSha256,
    policy_manifest_sha256: policyManifestSha256,
    runtime_version: POLICY_RUNTIME_VERSION,
    runtime_code_sha256: runtimeCodeSha256,
    mode: options.mode
  })}\n`);
  service.startDriving();

  process.once("SIGINT", () => { void shutdown(); });
  process.once("SIGTERM", () => { void shutdown(); });
  await exit;
}

function parseArgs(args: string[]): CliOptions {
  const values = new Map<string, string>();
  const adapterArgs: string[] = [];
  for (let index = 0; index < args.length; index += 1) {
    const key = args[index];
    if (!key?.startsWith("--")) throw new Error(`unexpected argument: ${String(key)}`);
    if (key.startsWith("--adapter-arg=")) {
      const embedded = key.slice("--adapter-arg=".length);
      if (!embedded) throw new Error("--adapter-arg requires a value");
      adapterArgs.push(embedded);
      continue;
    }
    const value = args[index + 1];
    if (!value) throw new Error(`${key} requires a value`);
    index += 1;
    if (key === "--adapter-arg") adapterArgs.push(value);
    else if (["--manifest", "--adapter-command", "--adapter-cwd", "--connector-endpoint", "--listen-port", "--evidence-root", "--mode"].includes(key)) values.set(key, value);
    else throw new Error(`unknown argument: ${key}`);
  }
  const manifestPath = required(values, "--manifest");
  const adapterCommand = required(values, "--adapter-command");
  const connectorEndpoint = values.get("--connector-endpoint") ?? "http://127.0.0.1:15526";
  const parsedEndpoint = new URL(connectorEndpoint);
  if (parsedEndpoint.protocol !== "http:" || !["127.0.0.1", "localhost", "::1", "[::1]"].includes(parsedEndpoint.hostname)) throw new Error("Connector endpoint must be loopback HTTP");
  const listenPort = Number(values.get("--listen-port") ?? "15527");
  if (!Number.isSafeInteger(listenPort) || listenPort < 1 || listenPort > 65535) throw new Error("--listen-port must be a valid TCP port");
  const mode = values.get("--mode") ?? "human";
  if (mode !== "human" && mode !== "shadow" && mode !== "one_step" && mode !== "auto") throw new Error("--mode is invalid");
  return {
    manifestPath,
    adapterCommand,
    adapterArgs,
    adapterCwd: values.get("--adapter-cwd"),
    connectorEndpoint,
    listenPort,
    evidenceRoot: values.get("--evidence-root") ?? ".local/evidence/agent-runs",
    mode
  };
}

function required(values: Map<string, string>, key: string): string {
  const value = values.get(key);
  if (!value) throw new Error(`${key} is required`);
  return value;
}

async function codeDigest(directory: string): Promise<string> {
  const names = (await readdir(directory)).filter((name) => name.endsWith(".js")).sort();
  if (names.length === 0) throw new Error("Policy Runtime compiled code is absent");
  const digest = createHash("sha256");
  for (const name of names) {
    digest.update(name).update("\0").update(await readFile(resolve(directory, name))).update("\0");
  }
  return digest.digest("hex");
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack ?? error.message : String(error)}\n`);
  process.exitCode = 1;
});
