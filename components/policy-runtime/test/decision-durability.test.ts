import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, readFile, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, basename } from "node:path";
import { AgentRunEvidence, readGlobalCaptureStaging, verifyEvidenceDirectory } from "../src/evidence.js";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";

const io = vi.hoisted(() => ({ sync: undefined as undefined | ((path: string) => Promise<void>) }));
vi.mock("node:fs/promises", async importOriginal => {
  const actual = await importOriginal<typeof import("node:fs/promises")>();
  return { ...actual, open: async (...args: Parameters<typeof actual.open>) => {
    const handle = await actual.open(...args), sync = handle.sync.bind(handle);
    handle.sync = async () => { await io.sync?.(String(args[0])); await sync(); };
    return handle;
  } };
});
afterEach(() => { io.sync = undefined; });
const request = "capture-request-11111111-1111-1111-1111-111111111111";
const fixed = "2026-10-28T00:00:00.000Z";
async function setup() {
  // The production manifest/serializer is reused; no native execution is claimed.
  const fixture = await globalRuntimeFixture(); await fixture.close();
  const manifest = structuredClone(fixture.manifest);
  if (!("kind" in manifest.requirements.environment)) throw Error("GLOBAL required");
  manifest.requirements.environment.host_kind = "managed";
  const create = async (durability: "event" | "decision") => AgentRunEvidence.createSession({
    root: await mkdtemp(join(tmpdir(), "decision-durability-")), runId: "fixed-run", agentManifest: manifest,
    runtimeVersion: "fixture", runtimeCodeSha256: "a".repeat(64), mode: "auto", now: () => fixed, durability
  });
  return { create, manifest };
}

describe("Managed decision durability with original evidence bytes", () => {
  it("seals byte-identical files with fewer journal syncs and keeps default event durability", async () => {
    const { create } = await setup(); const counts: number[] = [], directories: string[] = [];
    for (const durability of ["event", "decision"] as const) {
      const evidence = await create(durability); let count = 0;
      io.sync = async path => { if (basename(path) === "events.jsonl") count++; };
      const stage = await evidence.stageGlobalCaptureResponse(request, 1, Buffer.from("wire-original"));
      const artifact = await evidence.storeGlobalArtifact("response", Buffer.from("wire-original"));
      await evidence.append("global_transport_response", { artifact }, fixed);
      await evidence.append("global_receipt", { delivery: "unknown" }, fixed);
      await evidence.append("global_result", { status: "unknown" }, fixed);
      await evidence.releaseGlobalCaptureResponse(stage);
      await evidence.finalize({ status: "tainted", tainted: true, mode: "human", now: fixed });
      await verifyEvidenceDirectory(evidence.directory);
      counts.push(count); directories.push(evidence.directory);
    }
    expect(counts).toEqual([3, 1]);
    const names = await readdir(directories[0]!);
    expect(await readdir(directories[1]!)).toEqual(names);
    for (const name of names) expect(await readFile(join(directories[1]!, name))).toEqual(await readFile(join(directories[0]!, name)));
  });

  it("retains the durable raw response until replacement payload and journal sync both succeed", async () => {
    const { create } = await setup(); const evidence = await create("decision");
    const stage = await evidence.stageGlobalCaptureResponse(request, 1, Buffer.from("original"));
    const artifact = await evidence.storeGlobalArtifact("response", Buffer.from("replacement"));
    await evidence.append("global_transport_response", { artifact }, fixed);
    await evidence.releaseGlobalCaptureResponse(stage);
    let entered!: () => void, release!: () => void;
    const reached = new Promise<void>(resolve => { entered = resolve; });
    const gate = new Promise<void>(resolve => { release = resolve; });
    const order: string[] = [];
    io.sync = async path => { order.push(basename(path)); if (basename(path) === artifact.path) { entered(); await gate; } };
    const flushing = evidence.flush(); await reached;
    expect(await readGlobalCaptureStaging(evidence.directory)).toEqual([stage]);
    release(); await flushing;
    expect(order).toEqual([artifact.path, "events.jsonl"]);
    expect(await readGlobalCaptureStaging(evidence.directory)).toEqual([]);
  });

  it("never seals after a payload sync fault; diagnostic keeps the original raw prefix", async () => {
    const { create } = await setup(); const evidence = await create("decision");
    const stage = await evidence.stageGlobalCaptureResponse(request, 1, Buffer.from("original"));
    const artifact = await evidence.storeGlobalArtifact("response", Buffer.from("replacement"));
    await evidence.append("global_transport_response", { artifact }, fixed);
    await evidence.releaseGlobalCaptureResponse(stage);
    io.sync = async path => { if (basename(path) === artifact.path) throw Error("injected fsync fault"); };
    await expect(evidence.finalizeForStop({ status: "tainted", tainted: true, mode: "human", now: fixed })).rejects.toThrow("injected fsync fault");
    expect(await readGlobalCaptureStaging(evidence.directory)).toEqual([stage]);
    expect(await readFile(join(evidence.directory, stage.path), "utf8")).toBe("original");
    const diagnostic = JSON.parse(await readFile(join(evidence.directory, "diagnostic-manifest.json"), "utf8"));
    expect(diagnostic).toMatchObject({ complete: false, status: "unsealed", reason: "injected fsync fault" });
    expect(await readdir(evidence.directory)).not.toContain("evidence-manifest.json");
    await expect(evidence.flush()).rejects.toThrow("injected fsync fault");
  });

  it("rejects decision mode for a Reference manifest before creating a run", async () => {
    const { create, manifest } = await setup();
    if (!("kind" in manifest.requirements.environment)) throw Error("GLOBAL required");
    manifest.requirements.environment.host_kind = "reference";
    await expect(create("decision")).rejects.toThrow("stateless Managed GLOBAL");
  });
});
