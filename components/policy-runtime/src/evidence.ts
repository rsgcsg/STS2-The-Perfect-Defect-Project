import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, writeFile } from "node:fs/promises";
import { createReadStream } from "node:fs";
import { join, relative, sep } from "node:path";
import { validateManagedEnvironmentBinding, type AgentRunManifest, type EvidenceFileEntry, type ImmutableEvidenceManifest, type ManagedEnvironmentBinding, type PolicyManifest, type RuntimeMode } from "./contracts.js";
import { AGENT_RUN_SCHEMA, EVIDENCE_MANIFEST_SCHEMA } from "./contracts.js";

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

export class AgentRunEvidence {
  readonly directory: string;
  readonly runId: string;
  private readonly manifestPath: string;
  private readonly eventsPath: string;
  private readonly policyManifestPath: string;
  private readonly adapterAttestationPath: string;
  private readonly policyManifest: PolicyManifest;
  private readonly policyManifestSha256: string;
  private adapterAttested = false;
  private sequence = 0;
  private sealed = false;
  private operation: Promise<unknown> = Promise.resolve();
  private manifest: AgentRunManifest;

  private constructor(
    directory: string,
    manifest: AgentRunManifest,
    policyManifest: PolicyManifest,
    policyManifestSha256: string
  ) {
    this.directory = directory;
    this.runId = manifest.run_id;
    this.manifest = manifest;
    this.manifestPath = join(directory, "manifest.json");
    this.eventsPath = join(directory, "events.jsonl");
    this.policyManifestPath = join(directory, "policy-manifest.json");
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

  async attestAdapter(adapter: PolicyManifest["adapter"], now = new Date().toISOString()): Promise<void> {
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
        schema: "sts2.policy-runtime/agent-run-event-1",
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

  async finalize(input: { status: "completed" | "stopped" | "tainted"; tainted: boolean; mode: RuntimeMode; now?: string }): Promise<ImmutableEvidenceManifest> {
    return this.serialize(async () => {
      if (this.sealed) throw new Error("agent run evidence is already sealed");
      this.manifest = { ...this.manifest, ended_at: input.now ?? new Date().toISOString(), status: input.status, tainted: input.tainted, mode: input.mode };
      await writeFile(this.manifestPath, `${canonicalJson(this.manifest)}\n`);
      const files = await fileEntries([
        "adapter-attestation.json",
        "events.jsonl",
        "manifest.json",
        "policy-manifest.json"
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
        "policy-manifest.json"
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

  private adapterAttestation(actual: PolicyManifest["adapter"] | null, attestedAt: string | null): Record<string, unknown> {
    return {
      schema: "sts2.policy-runtime/adapter-attestation-1",
      run_id: this.runId,
      manifest_id: this.policyManifest.manifest_id,
      policy_manifest_sha256: this.policyManifestSha256,
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
