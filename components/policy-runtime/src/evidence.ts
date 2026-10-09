import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, writeFile } from "node:fs/promises";
import { createReadStream } from "node:fs";
import { join, relative, sep } from "node:path";
import { validateManagedEnvironmentBinding, type AgentRunManifest, type EvidenceFileEntry, type ImmutableEvidenceManifest, type ManagedEnvironmentBinding, type PolicyManifest, type RuntimeMode } from "./contracts.js";
import { AGENT_RUN_SCHEMA, EVIDENCE_MANIFEST_SCHEMA } from "./contracts.js";
import { validateAgentManifest, type AgentAdapterIdentity, type AgentManifest, type AgentInputSpec } from "./agent-session-contracts.js";
import { validateAgentStateMetadata, type AgentStateMetadata } from "./agent-session-state.js";
import { agentJsonByteLength, encodeBoundedAgentJson } from "./agent-session-json.js";

export interface EvidenceOptions {
  root: string;
  runId?: string;
  policyManifest: PolicyManifest;
  runtimeVersion: string;
  runtimeCodeSha256: string;
  mode: RuntimeMode;
  managedEnvironmentBinding?: ManagedEnvironmentBinding;
  now?: () => string;
}
export interface AgentSessionEvidenceOptions extends Omit<EvidenceOptions, "policyManifest" | "managedEnvironmentBinding"> {
  agentManifest: AgentManifest;
}
interface AgentSessionRunManifest extends Omit<AgentRunManifest, "schema" | "policy_manifest_sha256" | "policy_id" | "policy_version" | "policy_artifact_sha256"> {
  schema: "sts2.policy-runtime/agent-session-run-1";
  agent_manifest_sha256: string;
  agent_id: string;
  agent_version: string;
  agent_artifact_sha256: string;
}

export const AGENT_SAMPLE_STORAGE_LIMITS = Object.freeze({
  max_samples: 1024, max_files: 3072, max_total_bytes: 512 * 1024 * 1024,
  max_pending_bytes: 128 * 1024 * 1024, max_pending_samples: 8, max_metadata_bytes: 64 * 1024
});
export interface AgentSampleMetadata {
  schema: "sts2.policy-runtime/agent-sample-input-1";
  acquisition_id: string; input_spec: AgentInputSpec; continuity_token: string; publication_index: null;
  capture: Record<string, unknown>;
  observation: { path: string; bytes: number; sha256: string };
  catalog: { path: string; bytes: number; sha256: string; digest: string; count: number };
}
export interface AgentSampleReceipt { metadata_path: string; metadata: AgentSampleMetadata }

export class AgentRunEvidence {
  readonly directory: string;
  readonly runId: string;
  private readonly manifestPath: string;
  private readonly eventsPath: string;
  private readonly policyManifestPath: string;
  private readonly adapterAttestationPath: string;
  private readonly policyManifest: PolicyManifest | AgentManifest;
  private readonly policyManifestSha256: string;
  private adapterAttested = false;
  private sequence = 0;
  private sealed = false;
  private operation: Promise<unknown> = Promise.resolve();
  private manifest: AgentRunManifest | AgentSessionRunManifest;
  private readonly stateFiles = new Set<string>();
  private stateBytes = 0;
  private stateStorageFailed = false;
  private pendingStateBytes = 0;
  private pendingStateFiles = 0;
  private readonly sampleFiles = new Set<string>();
  private sampleBytes = 0;
  private pendingSampleBytes = 0;
  private pendingSamples = 0;
  private sampleStorageFailed = false;

  private constructor(
    directory: string,
    manifest: AgentRunManifest | AgentSessionRunManifest,
    policyManifest: PolicyManifest | AgentManifest,
    policyManifestSha256: string
  ) {
    this.directory = directory;
    this.runId = manifest.run_id;
    this.manifest = manifest;
    this.manifestPath = join(directory, "manifest.json");
    this.eventsPath = join(directory, "events.jsonl");
    this.policyManifestPath = join(directory, policyManifest.schema === "sts2.policy-runtime/agent-manifest-1" ? "agent-manifest.json" : "policy-manifest.json");
    this.adapterAttestationPath = join(directory, "adapter-attestation.json");
    this.policyManifest = policyManifest;
    this.policyManifestSha256 = policyManifestSha256;
  }

  static async create(options: EvidenceOptions): Promise<AgentRunEvidence> {
    const managed = "kind" in options.policyManifest.requirements.environment;
    if (managed !== (options.managedEnvironmentBinding !== undefined)) {
      throw new Error("Managed Agent Run requires one separately verified environment binding");
    }
    const binding = options.managedEnvironmentBinding === undefined ? undefined
      : validateManagedEnvironmentBinding(options.managedEnvironmentBinding);
    for (const [name, digest] of [
      ["policyArtifactSha256", options.policyManifest.artifact.sha256],
      ["runtimeCodeSha256", options.runtimeCodeSha256]
    ] as const) {
      if (!/^[a-f0-9]{64}$/u.test(digest)) throw new Error(`${name} must be a lowercase SHA-256`);
    }
    if (!options.runtimeVersion) throw new Error("runtimeVersion must be non-empty");
    const now = options.now ?? (() => new Date().toISOString());
    const runId = options.runId ?? `run-${randomUUID()}`;
    const directory = join(options.root, runId);
    await mkdir(directory, { recursive: false });
    const policyManifestSha256 = sha256Bytes(Buffer.from(canonicalJson(options.policyManifest), "utf8"));
    const manifest: AgentRunManifest = {
      schema: AGENT_RUN_SCHEMA,
      run_id: runId,
      manifest_id: options.policyManifest.manifest_id,
      policy_manifest_sha256: policyManifestSha256,
      policy_id: options.policyManifest.policy.id,
      policy_version: options.policyManifest.policy.version,
      policy_artifact_sha256: options.policyManifest.artifact.sha256,
      runtime_version: options.runtimeVersion,
      runtime_code_sha256: options.runtimeCodeSha256,
      started_at: now(),
      ended_at: null,
      status: "running",
      mode: options.mode,
      tainted: false,
      append_only: true,
      ...(binding === undefined ? {} : { environment_binding: binding })
    };
    const evidence = new AgentRunEvidence(directory, manifest, options.policyManifest, policyManifestSha256);
    await writeFile(evidence.manifestPath, `${canonicalJson(manifest)}\n`, { flag: "wx" });
    await writeFile(evidence.eventsPath, "", { flag: "wx" });
    await writeFile(evidence.policyManifestPath, `${canonicalJson(options.policyManifest)}\n`, { flag: "wx" });
    await writeFile(evidence.adapterAttestationPath, `${canonicalJson(evidence.adapterAttestation(null, null))}\n`, { flag: "wx" });
    return evidence;
  }

  static async createSession(options: AgentSessionEvidenceOptions): Promise<AgentRunEvidence> {
    const agent = validateAgentManifest(options.agentManifest);
    if (!options.runtimeVersion || !/^[a-f0-9]{64}$/u.test(options.runtimeCodeSha256)) throw new Error("exact Runtime identity required");
    const runId = options.runId ?? `run-${randomUUID()}`;
    const directory = join(options.root, runId);
    await mkdir(directory, { recursive: false });
    const manifestSha = sha256Bytes(Buffer.from(canonicalJson(agent), "utf8"));
    const manifest: AgentSessionRunManifest = {
      schema: "sts2.policy-runtime/agent-session-run-1", run_id: runId,
      manifest_id: agent.manifest_id, agent_manifest_sha256: manifestSha,
      agent_id: agent.agent.id, agent_version: agent.agent.version,
      agent_artifact_sha256: agent.artifact.sha256, runtime_version: options.runtimeVersion,
      runtime_code_sha256: options.runtimeCodeSha256,
      started_at: (options.now ?? (() => new Date().toISOString()))(), ended_at: null,
      status: "running", mode: options.mode, tainted: false, append_only: true
    };
    const evidence = new AgentRunEvidence(directory, manifest, agent, manifestSha);
    await writeFile(evidence.manifestPath, `${canonicalJson(manifest)}\n`, { flag: "wx" });
    await writeFile(evidence.eventsPath, "", { flag: "wx" });
    await writeFile(evidence.policyManifestPath, `${canonicalJson(agent)}\n`, { flag: "wx" });
    await writeFile(evidence.adapterAttestationPath, `${canonicalJson(evidence.adapterAttestation(null, null))}\n`, { flag: "wx" });
    return evidence;
  }

  async attestAdapter(adapter: PolicyManifest["adapter"] | AgentAdapterIdentity, now = new Date().toISOString()): Promise<void> {
    return this.serialize(async () => {
      if (this.sealed) throw new Error("agent run evidence is sealed");
      if (canonicalJson(adapter) !== canonicalJson(this.policyManifest.adapter)) {
        throw new Error("adapter attestation differs from Policy Manifest");
      }
      if (this.adapterAttested) return;
      await writeFile(this.adapterAttestationPath, `${canonicalJson(this.adapterAttestation(adapter, now))}\n`);
      this.adapterAttested = true;
    });
  }

  async append(kind: string, payload: Record<string, unknown> = {}, now = new Date().toISOString()): Promise<void> {
    return this.serialize(async () => {
      if (this.sealed) throw new Error("agent run evidence is sealed");
      if (!kind || kind.includes("\n")) throw new Error("evidence event kind must be a single non-empty line");
      this.sequence += 1;
      const event = {
        schema: this.policyManifest.schema === "sts2.policy-runtime/agent-manifest-1"
          ? "sts2.policy-runtime/agent-session-event-1" : "sts2.policy-runtime/agent-run-event-1",
        sequence: this.sequence,
        recorded_at: now,
        kind,
        payload
      };
      const handle = await open(this.eventsPath, "a");
      try {
        await handle.write(`${canonicalJson(event)}\n`);
        await handle.sync();
      } finally {
        await handle.close();
      }
    });
  }

  /** Immutable opaque inference state; metadata is validated, Model bytes are never decoded. */
  async storeAgentState(metadata: AgentStateMetadata, bytes: Uint8Array, expectedSha256: string): Promise<{ path: string; metadata_path: string; bytes: number; sha256: string }> {
    if (this.sealed || this.policyManifest.schema !== "sts2.policy-runtime/agent-manifest-1") throw new Error("state store requires an active Agent session");
    const manifest = this.policyManifest;
    validateAgentStateMetadata(metadata, manifest);
    if (manifest.input.state_recovery.mode !== "opaque" || bytes.byteLength < 1
      || bytes.byteLength > manifest.input.state_recovery.max_state_bytes
      || this.stateFiles.size + this.pendingStateFiles + 2 > 256
      || this.stateBytes + this.pendingStateBytes + bytes.byteLength > 256 * 1024 * 1024)
      throw new Error("opaque state storage capacity");
    // Snapshot before entering an async queue: callers cannot change saved bytes/meta.
    const snapshot = JSON.parse(encodeBoundedAgentJson(metadata, 64 * 1024).toString("utf8")) as AgentStateMetadata;
    const buffer = Buffer.from(bytes);
    const sha = sha256Bytes(buffer);
    if (!/^[a-f0-9]{64}$/u.test(expectedSha256) || sha !== expectedSha256) throw new Error("opaque state integrity");
    this.pendingStateBytes += buffer.length;
    this.pendingStateFiles += 2;
    return this.serialize(async () => {
      if (this.sealed) throw new Error("state store requires an active Agent session");
      const id = sha256Bytes(Buffer.from(canonicalJson({ metadata: snapshot, sha256: sha }), "utf8"));
      const path = `agent-state-${id}.bin`, metadataPath = `agent-state-${id}.json`;
      const record = { schema: "sts2.policy-runtime/agent-state-snapshot-1", metadata: snapshot,
        payload: { path, bytes: buffer.length, sha256: sha } };
      if (this.stateFiles.has(path)) {
        if (sha256Bytes(await readFile(join(this.directory, path))) !== sha
          || (await readFile(join(this.directory, metadataPath), "utf8")) !== `${canonicalJson(record)}\n`)
          throw new Error("immutable state collision");
        return { path, metadata_path: metadataPath, bytes: buffer.length, sha256: sha };
      }
      if (this.stateFiles.size + 2 > 256) throw new Error("opaque state storage capacity");
      try {
        const handle = await open(join(this.directory, path), "wx");
        try { await handle.writeFile(buffer); await handle.sync(); } finally { await handle.close(); }
        const meta = await open(join(this.directory, metadataPath), "wx");
        try { await meta.writeFile(`${canonicalJson(record)}\n`); await meta.sync(); } finally { await meta.close(); }
      } catch (error) { this.stateStorageFailed = true; throw error; }
      this.stateFiles.add(path); this.stateFiles.add(metadataPath); this.stateBytes += buffer.length;
      return { path, metadata_path: metadataPath, bytes: buffer.length, sha256: sha };
    }).finally(() => { this.pendingStateBytes -= buffer.length; this.pendingStateFiles -= 2; });
  }

  /** Exact original observation bytes plus SDK-assembled ordered catalog. No opaque W API. */
  async storeSampleAcquisition(input: {
    acquisition_id: string; input_spec: AgentInputSpec; continuity_token: string;
    capture: Record<string, unknown>; observation: Uint8Array;
    catalog: readonly Record<string, unknown>[]; catalog_digest: string;
  }): Promise<AgentSampleReceipt> {
    if (this.sealed || this.policyManifest.schema !== "sts2.policy-runtime/agent-manifest-1"
      || this.policyManifest.input.history_mode !== "sampled_current") throw new Error("sample store requires sampled session");
    if (!input.acquisition_id || !input.continuity_token
      || canonicalJson(input.input_spec) !== canonicalJson(this.policyManifest.input.input_spec)
      || input.observation.byteLength !== input.capture.byte_count
      || input.observation.byteLength < 1 || input.observation.byteLength > this.policyManifest.limits.max_capture_bytes
      || input.catalog.length > this.policyManifest.limits.max_catalog_actions)
      throw new Error("sample acquisition binding or size");
    const catalogBytes = agentJsonByteLength(input.catalog, this.policyManifest.limits.max_message_bytes);
    const charged = input.observation.byteLength + catalogBytes + AGENT_SAMPLE_STORAGE_LIMITS.max_metadata_bytes;
    if (this.sampleFiles.size / 3 + this.pendingSamples >= AGENT_SAMPLE_STORAGE_LIMITS.max_samples
      || this.sampleFiles.size + (this.pendingSamples + 1) * 3 > AGENT_SAMPLE_STORAGE_LIMITS.max_files
      || this.sampleBytes + this.pendingSampleBytes + charged > AGENT_SAMPLE_STORAGE_LIMITS.max_total_bytes
      || this.pendingSampleBytes + charged > AGENT_SAMPLE_STORAGE_LIMITS.max_pending_bytes
      || this.pendingSamples >= AGENT_SAMPLE_STORAGE_LIMITS.max_pending_samples)
      throw new Error("sample storage capacity");
    // Charge before copying either pending original payload; never borrow opaque limits.
    this.pendingSamples += 1; this.pendingSampleBytes += charged;
    try {
      const observation = Buffer.from(input.observation);
      const catalog = Buffer.from(canonicalJson(input.catalog), "utf8");
      const capture = JSON.parse(canonicalJson(input.capture)) as Record<string, unknown>;
      const observationSha = sha256Bytes(observation), catalogSha = sha256Bytes(catalog);
      if (observationSha !== capture.sha256) throw new Error("sample original observation integrity");
      const binding = { acquisition_id: input.acquisition_id, input_spec: input.input_spec,
        continuity_token: input.continuity_token, publication_index: null, capture };
      const id = sha256Bytes(Buffer.from(canonicalJson(binding), "utf8"));
      const stem = `agent-sample-${id}`;
      const metadata: AgentSampleMetadata = { schema: "sts2.policy-runtime/agent-sample-input-1", ...binding,
        publication_index: null, observation: { path: `${stem}.observation.json`, bytes: observation.length, sha256: observationSha },
        catalog: { path: `${stem}.catalog.json`, bytes: catalog.length, sha256: catalogSha,
          digest: input.catalog_digest, count: input.catalog.length } };
      const metadataPath = `${stem}.json`, meta = encodeBoundedAgentJson(metadata, AGENT_SAMPLE_STORAGE_LIMITS.max_metadata_bytes);
      const payloads = [[metadata.observation.path, observation], [metadata.catalog.path, catalog],
        [metadataPath, Buffer.concat([meta, Buffer.from("\n")])]] as const;
      return await this.serialize(async () => {
        if (this.sealed) throw new Error("sample store sealed");
        if (this.sampleFiles.has(metadataPath)) {
          for (const [path, bytes] of payloads) if (!(await readFile(join(this.directory, path))).equals(bytes))
            throw new Error("immutable sample collision");
          return { metadata_path: metadataPath, metadata };
        }
        try {
          for (const [path, bytes] of payloads) {
            const handle = await open(join(this.directory, path), "wx");
            try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
          }
        } catch (error) { this.sampleStorageFailed = true; throw error; }
        for (const [path, bytes] of payloads) { this.sampleFiles.add(path); this.sampleBytes += bytes.length; }
        return { metadata_path: metadataPath, metadata };
      });
    } finally { this.pendingSamples -= 1; this.pendingSampleBytes -= charged; }
  }

  async finalize(input: { status: "completed" | "stopped" | "tainted"; tainted: boolean; mode: RuntimeMode; now?: string }): Promise<ImmutableEvidenceManifest> {
    return this.serialize(async () => {
      if (this.sealed) throw new Error("agent run evidence is already sealed");
      if (this.sampleStorageFailed) throw new Error("sample storage incomplete; evidence cannot be sealed");
      if (this.stateStorageFailed) throw new Error("opaque state storage incomplete; evidence cannot be sealed");
      this.manifest = { ...this.manifest, ended_at: input.now ?? new Date().toISOString(), status: input.status, tainted: input.tainted, mode: input.mode };
      await writeFile(this.manifestPath, `${canonicalJson(this.manifest)}\n`);
      const executionManifestName = this.policyManifest.schema === "sts2.policy-runtime/agent-manifest-1" ? "agent-manifest.json" : "policy-manifest.json";
      const files = await fileEntries([
        "adapter-attestation.json",
        "events.jsonl",
        "manifest.json",
        executionManifestName, ...this.stateFiles, ...this.sampleFiles
      ], this.directory);
      const manifestSha256 = sha256Bytes(Buffer.from(canonicalJson({ run_id: this.runId, files }), "utf8"));
      const evidenceManifest: ImmutableEvidenceManifest = {
        schema: EVIDENCE_MANIFEST_SCHEMA,
        run_id: this.runId,
        complete: true,
        append_only: true,
        files,
        manifest_sha256: manifestSha256
      };
      await writeFile(join(this.directory, "evidence-manifest.json"), `${canonicalJson(evidenceManifest)}\n`, { flag: "wx" });
      const checksummed = await fileEntries([
        "adapter-attestation.json",
        "events.jsonl",
        "evidence-manifest.json",
        "manifest.json",
        executionManifestName, ...this.stateFiles, ...this.sampleFiles
      ], this.directory);
      const lines = checksummed.map((entry) => `${entry.sha256}  ${entry.path}`).join("\n");
      await writeFile(join(this.directory, "checksums.sha256"), `${lines}\n`, { flag: "wx" });
      this.sealed = true;
      return evidenceManifest;
    });
  }

  private serialize<T>(operation: () => Promise<T>): Promise<T> {
    const current = this.operation.then(operation, operation);
    this.operation = current.then(() => undefined, () => undefined);
    return current;
  }

  private adapterAttestation(actual: PolicyManifest["adapter"] | AgentAdapterIdentity | null, attestedAt: string | null): Record<string, unknown> {
    return {
      schema: this.policyManifest.schema === "sts2.policy-runtime/agent-manifest-1"
        ? "sts2.policy-runtime/agent-session-adapter-attestation-1" : "sts2.policy-runtime/adapter-attestation-1",
      run_id: this.runId,
      manifest_id: this.policyManifest.manifest_id,
      ...(this.policyManifest.schema === "sts2.policy-runtime/agent-manifest-1"
        ? { agent_manifest_sha256: this.policyManifestSha256 }
        : { policy_manifest_sha256: this.policyManifestSha256 }),
      status: actual === null ? "not_attested" : "attested",
      expected: this.policyManifest.adapter,
      actual,
      attested_at: attestedAt
    };
  }
}

async function fileEntries(names: string[], directory: string): Promise<EvidenceFileEntry[]> {
  const entries: EvidenceFileEntry[] = [];
  for (const name of names.sort()) {
    const path = join(directory, name);
    const data = await readFile(path);
    entries.push({ path: name, bytes: data.byteLength, sha256: sha256Bytes(data) });
  }
  return entries;
}

function sha256Bytes(data: Buffer): string {
  return createHash("sha256").update(data).digest("hex");
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortJson(value));
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => [key, sortJson(item)]));
  }
  return value;
}

export async function verifyEvidenceDirectory(directory: string): Promise<void> {
  const checksums = (await readFile(join(directory, "checksums.sha256"), "utf8")).trim().split("\n").filter(Boolean);
  const expected = new Map(checksums.map((line) => {
    const match = /^(\w{64})  (.+)$/.exec(line);
    if (!match) throw new Error("invalid evidence checksum line");
    return [match[2]!, match[1]!] as const;
  }));
  const actual = await fileEntries([...expected.keys()], directory);
  if (actual.length !== expected.size || actual.some((entry) => expected.get(entry.path) !== entry.sha256)) throw new Error("evidence checksum verification failed");
  const manifest = JSON.parse(await readFile(join(directory, "evidence-manifest.json"), "utf8")) as ImmutableEvidenceManifest;
  if (manifest.complete !== true || manifest.append_only !== true) throw new Error("evidence manifest is not immutable");
}
