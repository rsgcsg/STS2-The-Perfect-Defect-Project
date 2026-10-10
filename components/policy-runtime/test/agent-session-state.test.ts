import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough, Writable } from "node:stream";
import { describe, expect, it, vi } from "vitest";
import { fixtureProgress } from "./fixture-progress.js";
import { AgentByteBudget } from "../src/agent-session-budget.js";
import { AgentConsumptionLedger, type AgentAcquisition } from "../src/agent-session-consumption.js";
import { AGENT_SESSION_SCHEMA, type AgentManifest } from "../src/agent-session-contracts.js";
import { AgentRunEvidence, verifyEvidenceDirectory } from "../src/evidence.js";
import { NdjsonAgentSessionPort, type AgentStateAuthorization } from "../src/agent-session-port.js";
import {
  acquireAgentStateBytes, makeAgentStatePayload, requireAgentStateMetadata,
  validateAgentStatePayload, type AgentOpaqueState, type AgentStateMetadata
} from "../src/agent-session-state.js";

const shared = JSON.parse(readFileSync(new URL("../contracts/fixtures/agent-session-v1.json", import.meta.url), "utf8")) as {
  manifest: AgentManifest; acquisitions: Record<string, AgentAcquisition>;
};
const context = { session_id: "session", recovery_epoch: 7 };
function metadata(manifest = shared.manifest): AgentStateMetadata {
  const ledger = new AgentConsumptionLedger(manifest, "segment");
  const a = shared.acquisitions.empty!;
  ledger.register(a); ledger.noteReceived("cursor-11", 1);
  const ack = ledger.accept({ acquisition_id: a.acquisition_id, input_spec: manifest.input.input_spec,
    continuity_token: "segment", previous_consumption_id: null, consumption_id: "one", state_version: 1, advanced: true });
  return { agent_artifact_id: manifest.artifact.id, agent_artifact_sha256: manifest.artifact.sha256,
    adapter_code_sha256: manifest.adapter.code_sha256, model_bindings: manifest.input.state_recovery.model_bindings,
    input_spec: manifest.input.input_spec, profile: manifest.input.profile,
    state_format_version: manifest.input.state_format_version, stream_generation: String(a.capture.stream_generation),
    continuity_token: "segment", consumption_id: "one", state_version: 1, prefix: ack.prefix,
    last_acknowledged_basis: { acquisition_id: a.acquisition_id, capture_sha256: String(a.capture.sha256),
      snapshot_id: String(a.observation.snapshot_id), owner_occurrence: a.observation.owner_occurrence as Record<string, unknown>,
      revision: Number(a.observation.revision), included: ["persistent", "interaction", "referents", "catalog"],
      publication_index: a.publication_index } };
}
function fixture(manifest = shared.manifest) {
  const written: Record<string, unknown>[] = [];
  const stdin = new Writable({ write(chunk, _encoding, callback) { written.push(JSON.parse(Buffer.from(chunk).toString())); callback(); } });
  const stdout = new PassThrough(), stderr = new PassThrough();
  const child = Object.assign(new EventEmitter(), { stdin, stdout, stderr, exitCode: null, kill: vi.fn(() => true) });
  const port = new NdjsonAgentSessionPort(child as unknown as ConstructorParameters<typeof NdjsonAgentSessionPort>[0], manifest.adapter, manifest.limits, undefined, manifest.execution_policy);
  stdout.write(JSON.stringify({ schema: AGENT_SESSION_SCHEMA, message_type: "ready", adapter: manifest.adapter }) + "\n");
  const authorization: AgentStateAuthorization = { expected_metadata: metadata(manifest),
    pending_request: "reconciled", retained_prefix: "complete", assertCurrent: vi.fn() };
  const respond = (messageType: string, output: unknown) => stdout.write(JSON.stringify({ schema: AGENT_SESSION_SCHEMA,
    message_type: messageType, ...context, request_id: written.at(-1)!.request_id, output }) + "\n");
  return { port, child, written, authorization, respond };
}

describe("opaque Agent state wire, not numerical Model qualification", () => {
  it("copies/hash-checks opaque bytes without any Model codec interpretation", () => {
    const raw = Buffer.from("synthetic opaque state, not a tensor fixture");
    const payload = makeAgentStatePayload(raw, 100);
    const budget = new AgentByteBudget(100);
    const owned = acquireAgentStateBytes(payload, 100, budget);
    expect(owned.bytes).toEqual(raw); expect(budget.used).toBe(raw.length);
    owned.reservation.release(); expect(budget.used).toBe(0);
    expect(() => acquireAgentStateBytes({ ...payload, sha256: "f".repeat(64) }, 100, budget)).toThrow("state_payload_integrity");
    expect(budget.used).toBe(0);
  });
  it("rejects decoded oversize and noncanonical Base64 before allocating the payload", () => {
    expect(() => validateAgentStatePayload({ encoding: "base64", byte_count: 101, sha256: "a".repeat(64), data_base64: "AAAA" }, 100)).toThrow("invalid_integer");
    expect(() => validateAgentStatePayload({ encoding: "base64", byte_count: 1, sha256: "a".repeat(64), data_base64: "AB==" }, 100)).toThrow("state_base64_noncanonical");
  });
  it.each(["weight", "input", "format", "generation", "unacknowledged"])("rejects wrong %s binding", mutation => {
    const expected = metadata(), changed = structuredClone(expected);
    if (mutation === "weight") changed.model_bindings[0]!.weights_sha256 = "f".repeat(64);
    if (mutation === "input") changed.input_spec.sha256 = "f".repeat(64);
    if (mutation === "format") changed.state_format_version = "unsupported";
    if (mutation === "generation") changed.stream_generation = "other";
    if (mutation === "unacknowledged") changed.consumption_id = "not-acknowledged";
    expect(() => requireAgentStateMetadata(changed, expected, shared.manifest)).toThrow();
  });
  it("exports after durable acknowledged metadata and restores in a fresh child before Consume", async () => {
    const source = fixture(); await source.port.ready();
    const output: AgentOpaqueState = { metadata: source.authorization.expected_metadata,
      payload: makeAgentStatePayload(Buffer.from("opaque synthetic restart state"), 100) };
    const exported = source.port.exportState(context, shared.manifest, source.authorization, new AbortController().signal, () => {});
    source.respond("state_exported", output);
    const owned = await exported;
    expect(owned.bytes.toString()).toBe("opaque synthetic restart state");
    const restored = fixture(); await restored.port.ready();
    const result = restored.port.restoreState(context, shared.manifest, restored.authorization,
      owned.state, new AbortController().signal, () => {});
    restored.respond("state_restored", { metadata: output.metadata });
    expect(await result).toEqual(output.metadata);
    await expect(restored.port.restoreState(context, shared.manifest, restored.authorization,
      output, new AbortController().signal, () => {})).rejects.toThrow("restore_after_consume");
    owned.release(); source.port.close(); restored.port.close();
  });
  it("rejects restore metadata before offering to Model and preserves a pending-request fence", async () => {
    const f = fixture(); await f.port.ready(); const offer = vi.fn();
    const state = { metadata: { ...f.authorization.expected_metadata, stream_generation: "wrong" },
      payload: makeAgentStatePayload(Buffer.from("state"), 100) };
    expect(() => f.port.restoreState(context, shared.manifest, f.authorization, state, new AbortController().signal, offer)).toThrow("state_durable_prefix_mismatch");
    expect(offer).not.toHaveBeenCalled();
    await expect(f.port.exportState(context, shared.manifest,
      { ...f.authorization, pending_request: "unknown" as AgentStateAuthorization["pending_request"] }, new AbortController().signal, offer)).rejects.toThrow("state_recovery_requires_reconciled_prefix");
    expect(offer).not.toHaveBeenCalled(); f.port.close();
  });
  it("state-none explicitly refuses opaque recovery", async () => {
    const manifest = structuredClone(shared.manifest);
    manifest.input.state_recovery = { mode: "none", max_state_bytes: 0, model_bindings: [] };
    const f = fixture(manifest); await f.port.ready();
    await expect(f.port.exportState(context, manifest, f.authorization, new AbortController().signal, () => {})).rejects.toThrow("stateless_state_unsupported");
    f.port.close();
  });
  it("one aggregate byte budget blocks new assembly while retained captures own capacity", () => {
    const budget = new AgentByteBudget(256);
    const retained = budget.reserve(240);
    expect(() => budget.reserve(32)).toThrow("retained_acquisition_byte_capacity");
    expect(budget.used).toBe(240); retained.release(); expect(budget.reserve(32).bytes).toBe(32);
  });
  it("uses existing Evidence storage for immutable bytes/meta and includes them in checksums", async () => {
    const root = await mkdtemp(join(tmpdir(), "agent-state-evidence-"));
    try {
      const evidence = await AgentRunEvidence.createSession({ root, agentManifest: shared.manifest,
        runtimeVersion: "fixture", runtimeCodeSha256: "a".repeat(64), mode: "human" });
      await evidence.attestAdapter(shared.manifest.adapter);
      const raw = Buffer.from("opaque synthetic state"), payload = makeAgentStatePayload(raw, 100);
      const receipt = await evidence.storeAgentState(metadata(), raw, payload.sha256);
      expect(await evidence.storeAgentState(metadata(), raw, payload.sha256)).toEqual(receipt);
      expect(await readFile(join(evidence.directory, receipt.path))).toEqual(raw);
      const manifest = await evidence.finalize({ status: "stopped", tainted: false, mode: "human" });
      expect(manifest.files.map(file => file.path)).toContain(receipt.path);
      await verifyEvidenceDirectory(evidence.directory);
    } finally { await rm(root, { recursive: true, force: true }); }
  });
  it("reserves the file-count bound for concurrently queued distinct state snapshots", async () => {
    const progress = fixtureProgress("opaque-state-file-count");
    const root = await mkdtemp(join(tmpdir(), "agent-state-count-"));
    try {
      const evidence = await AgentRunEvidence.createSession({ root, agentManifest: shared.manifest,
        runtimeVersion: "fixture", runtimeCodeSha256: "a".repeat(64), mode: "human" });
      progress("session-created"); let completed = 0;
      const raw = Buffer.from("s"), payload = makeAgentStatePayload(raw, 100);
      const outcomes = await Promise.allSettled(Array.from({ length: 130 }, (_, index) => {
        const distinct = structuredClone(metadata());
        distinct.consumption_id = `synthetic-${index}`;
        distinct.state_version = index + 1;
        return evidence.storeAgentState(distinct, raw, payload.sha256).then(receipt => {
          completed++; if (completed % 32 === 0) progress("state-files-durable", { snapshots: completed, files: completed * 2 });
          return receipt;
        });
      }));
      expect(outcomes.filter(outcome => outcome.status === "fulfilled")).toHaveLength(128);
      expect(outcomes.filter(outcome => outcome.status === "rejected")).toHaveLength(2);
      const files = (await readdir(evidence.directory)).filter(name => name.startsWith("agent-state-"));
      expect(files).toHaveLength(256);
      const result = await evidence.finalize({ status: "stopped", tainted: false, mode: "human" });
      progress("finalized", { files: files.length });
      expect(result.files.filter(file => file.path.startsWith("agent-state-"))).toHaveLength(256);
      await verifyEvidenceDirectory(evidence.directory);
      progress("independently-verified");
    } finally { await rm(root, { recursive: true, force: true }); }
  // Harness completion includes all 256 fsyncs and independent file verification;
  // this is not a state-storage latency contract or a change to capacity.
  }, 15000);
});
