#!/usr/bin/env node

import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, isAbsolute, resolve } from "node:path";
import process from "node:process";
import { PlayerEnvironmentRestClient } from "@rsgcsg/sts2-connector-client";
import { DEFAULT_AUTONOMY_BUDGET, POLICY_RUNTIME_VERSION, validatePolicyManifest, type AutonomyBudgetConfig, type RuntimeMode } from "./contracts.js";
import { ConnectorPolicyClient } from "./connector.js";
import { AgentRunEvidence, canonicalJson } from "./evidence.js";
import { ManagedServicePolicyClient, type ManagedTarget } from "./managed.js";
import { NdjsonPolicyPort } from "./policy-port.js";
import { PolicyRuntime } from "./runtime.js";
import { startPolicyRuntimeHttpServer } from "./server.js";
import { AGENT_MANIFEST_SCHEMA, validateAgentManifest, type AgentManifest } from "./agent-session-contracts.js";
import { NdjsonAgentSessionPort } from "./agent-session-port.js";
import type { NativeAgentRuntimeOwner } from "./agent-native-runtime.js";
import type { AgentRuntimeStartup } from "./agent-runtime-contracts.js";

interface CliOptions {
  manifestPath: string;
  adapterCommand: string;
  adapterArgs: string[];
  adapterCwd?: string;
  connectorEndpoint: string;
  connectorEndpointExplicit: boolean;
  observationAcquisition: "direct" | "sealed-text-menu-v2";
  managed?: { bindingPath: string; attachmentPath: string; target: ManagedTarget };
  listenPort: number;
  evidenceRoot: string;
  mode: RuntimeMode;
  autoBudget: AutonomyBudgetConfig;
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  const manifestPath = resolve(options.manifestPath);
  const rawManifest: unknown = JSON.parse(await readFile(manifestPath, "utf8"));
  if (rawManifest !== null && typeof rawManifest === "object" && !Array.isArray(rawManifest)
    && (rawManifest as Record<string, unknown>).schema === AGENT_MANIFEST_SCHEMA) {
    await runAgent(options, manifestPath, validateAgentManifest(rawManifest)); return;
  }
  const manifest = validatePolicyManifest(rawManifest);
  const isManaged = "kind" in manifest.requirements.environment;
  if (options.observationAcquisition === "sealed-text-menu-v2"
    && (isManaged || manifest.representation.input_schema !== "sts2.player-environment/text-menu-snapshot-2"))
    throw new Error("sealed acquisition requires a native text-menu-v2 manifest");
  if (isManaged !== Boolean(options.managed)) throw new Error("Managed manifest requires exact Managed attachment and binding arguments");
  if (isManaged && options.connectorEndpointExplicit) throw new Error("Managed Runtime cannot use a Connector endpoint");
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
  const runId = `run-${randomUUID()}`;
  const managedClient = options.managed
    ? await ManagedServicePolicyClient.attach(options.managed.bindingPath,
      options.managed.attachmentPath, options.managed.target, runId)
    : undefined;

  await mkdir(resolve(options.evidenceRoot), { recursive: true });
  const evidence = await AgentRunEvidence.create({
    root: resolve(options.evidenceRoot),
    runId,
    policyManifest: manifest,
    runtimeVersion: POLICY_RUNTIME_VERSION,
    runtimeCodeSha256,
    mode: options.mode,
    ...(managedClient ? { managedEnvironmentBinding: managedClient.binding } : {})
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
    const connector = managedClient ?? new ConnectorPolicyClient(
      new PlayerEnvironmentRestClient(options.connectorEndpoint, 5_000),
      { productVersion: POLICY_RUNTIME_VERSION, observationAcquisition: options.observationAcquisition }
    );
    runtime = new PolicyRuntime({
      manifest,
      connector,
      ...(manifest.adapter.protocol !== "sts2.policy-runtime/decision-only-ndjson-1"
        ? { statefulPolicy: (input: Parameters<NdjsonPolicyPort["decideV2"]>[0],
            signal: Parameters<NdjsonPolicyPort["decideV2"]>[1],
            onOffer: Parameters<NdjsonPolicyPort["decideV2"]>[2]) => manifest.adapter.protocol === "sts2.policy-runtime/decision-only-ndjson-3"
              ? port!.decideV3(input, signal, onOffer) : port!.decideV2(input, signal, onOffer),
          statefulOfferBoundary: "port_write" as const }
        : { policy: (input: Parameters<NdjsonPolicyPort["decide"]>[0],
            signal: Parameters<NdjsonPolicyPort["decide"]>[1]) => port!.decide(input, signal) }),
      mode: options.mode,
      autoBudget: options.autoBudget,
      runId: evidence.runId,
      evidence,
      ...(managedClient ? { managedBindingSha256: managedClient.bindingSha256 } : {}),
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
    mode: options.mode,
    autonomy_budget: options.autoBudget,
    ...(managedClient ? { managed_environment: {
      binding_sha256: managedClient.bindingSha256,
      service_instance_id: managedClient.initialEnvironment.service_instance_id,
      runtime_instance_id: managedClient.initialEnvironment.runtime_instance_id,
      game_continuity_id: managedClient.initialEnvironment.game_continuity_id
    } } : {})
  })}\n`);
  service.startDriving();

  process.once("SIGINT", () => { void shutdown(); });
  process.once("SIGTERM", () => { void shutdown(); });
  await exit;
}

async function runAgent(options: CliOptions, manifestPath: string, manifest: AgentManifest): Promise<void> {
  if (options.managed || options.observationAcquisition !== "direct")
    throw new Error("native Agent session does not support Managed or legacy sealed text acquisition");
  const artifactPath = isAbsolute(manifest.artifact.path) ? manifest.artifact.path : resolve(dirname(manifestPath), manifest.artifact.path);
  const artifactSha256 = createHash("sha256").update(await readFile(artifactPath)).digest("hex");
  if (artifactSha256 !== manifest.artifact.sha256) throw new Error("Agent artifact SHA-256 differs from Agent Manifest");
  const agentManifestSha256 = createHash("sha256").update(canonicalJson(manifest)).digest("hex");
  const runtimeCodeSha256 = await codeDigest(dirname(fileURLToPath(import.meta.url)));
  await mkdir(resolve(options.evidenceRoot), { recursive: true });
  const evidence = await AgentRunEvidence.createSession({ root: resolve(options.evidenceRoot), agentManifest: manifest,
    runtimeVersion: POLICY_RUNTIME_VERSION, runtimeCodeSha256, mode: options.mode });
  let runtime: NativeAgentRuntimeOwner | undefined;
  let port: NdjsonAgentSessionPort | undefined;
  let service: Awaited<ReturnType<typeof startPolicyRuntimeHttpServer>> | undefined;
  let shuttingDown = false;
  let resolveExit!: () => void;
  const exit = new Promise<void>(resolvePromise => { resolveExit = resolvePromise; });
  const shutdown = async (): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    try { await runtime?.stop(); }
    finally { try { await service?.close(); } finally { port?.close(); resolveExit(); } }
  };
  try {
    port = NdjsonAgentSessionPort.spawn(options.adapterCommand, options.adapterArgs,
      manifest.adapter, manifest.limits, { cwd: options.adapterCwd, executionPolicy: manifest.execution_policy });
    runtime = await PolicyRuntime.forAgent({ manifest, environment: new PlayerEnvironmentRestClient(options.connectorEndpoint, manifest.limits.agent_timeout_ms),
      port, evidence, mode: options.mode, autoBudget: options.autoBudget,
      runtimeIdentity: { version: POLICY_RUNTIME_VERSION, code_sha256: runtimeCodeSha256 } });
    service = await startPolicyRuntimeHttpServer(runtime, { port: options.listenPort, autoDrive: true,
      deferAutoDrive: true, onStopped: shutdown });
  } catch (error) {
    try {
      if (runtime) await runtime.stop();
      else await evidence.finalize({ status: "stopped", tainted: false, mode: "human" });
    } finally { port?.close(); }
    throw error;
  }
  const startup: AgentRuntimeStartup = { schema: "sts2.policy-runtime/agent-session-startup-1", address: service.address,
    run_id: evidence.runId, agent_manifest_id: manifest.manifest_id, agent_artifact_sha256: artifactSha256,
    agent_manifest_sha256: agentManifestSha256, runtime_version: POLICY_RUNTIME_VERSION, runtime_code_sha256: runtimeCodeSha256,
    mode: runtime.status().mode, autonomy_budget: options.autoBudget, managed_environment: null, adapter: manifest.adapter };
  process.stdout.write(`${JSON.stringify(startup)}\n`);
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
    else if (["--observation-acquisition", "--manifest", "--adapter-command", "--adapter-cwd", "--connector-endpoint", "--listen-port", "--evidence-root", "--mode", "--max-auto-submissions", "--max-policy-calls", "--auto-deadline-ms", "--managed-binding", "--managed-attachment", "--managed-expected-service-instance-id", "--managed-expected-runtime-instance-id", "--managed-expected-game-continuity-id"].includes(key)) values.set(key, value);
    else throw new Error(`unknown argument: ${key}`);
  }
  const manifestPath = required(values, "--manifest");
  const adapterCommand = required(values, "--adapter-command");
  const connectorEndpoint = values.get("--connector-endpoint") ?? "http://127.0.0.1:15526";
  const parsedEndpoint = new URL(connectorEndpoint);
  if (parsedEndpoint.protocol !== "http:" || !["127.0.0.1", "localhost", "::1", "[::1]"].includes(parsedEndpoint.hostname)) throw new Error("Connector endpoint must be loopback HTTP");
  const listenPort = Number(values.get("--listen-port") ?? "15527");
  if (!Number.isSafeInteger(listenPort) || listenPort < 1 || listenPort > 65535) throw new Error("--listen-port must be a valid TCP port");
  const observationAcquisition = values.get("--observation-acquisition") ?? "direct";
  if (observationAcquisition !== "direct" && observationAcquisition !== "sealed-text-menu-v2")
    throw new Error("--observation-acquisition must be direct or sealed-text-menu-v2");
  const mode = values.get("--mode") ?? "human";
  if (mode !== "human" && mode !== "shadow" && mode !== "one_step" && mode !== "auto") throw new Error("--mode is invalid");
  const autoBudget = {
    maxSubmissions: positiveOption(values, "--max-auto-submissions", DEFAULT_AUTONOMY_BUDGET.maxSubmissions),
    maxPolicyCalls: positiveOption(values, "--max-policy-calls", DEFAULT_AUTONOMY_BUDGET.maxPolicyCalls),
    deadlineMs: positiveOption(values, "--auto-deadline-ms", DEFAULT_AUTONOMY_BUDGET.deadlineMs)
  };
  const managedKeys = ["--managed-binding", "--managed-attachment", "--managed-expected-service-instance-id", "--managed-expected-runtime-instance-id", "--managed-expected-game-continuity-id"];
  const managed = managedKeys.some((key) => values.has(key)) ? {
    bindingPath: required(values, "--managed-binding"),
    attachmentPath: required(values, "--managed-attachment"),
    target: {
      serviceInstanceId: required(values, "--managed-expected-service-instance-id"),
      runtimeInstanceId: required(values, "--managed-expected-runtime-instance-id"),
      gameContinuityId: required(values, "--managed-expected-game-continuity-id")
    }
  } : undefined;
  return {
    observationAcquisition,
    manifestPath,
    adapterCommand,
    adapterArgs,
    adapterCwd: values.get("--adapter-cwd"),
    connectorEndpoint,
    connectorEndpointExplicit: values.has("--connector-endpoint"),
    managed,
    listenPort,
    evidenceRoot: values.get("--evidence-root") ?? ".local/evidence/agent-runs",
    mode,
    autoBudget
  };
}

function positiveOption(values: Map<string, string>, key: string, fallback: number): number {
  const value = Number(values.get(key) ?? fallback);
  if (!Number.isSafeInteger(value) || value < 1) throw new Error(`${key} must be a positive integer`);
  return value;
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
