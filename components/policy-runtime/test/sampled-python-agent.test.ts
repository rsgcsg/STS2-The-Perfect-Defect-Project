/** Separately enabled synthetic numerical package composition; no game/HTTP. */
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { JsonObject, NativeLogicalTransportOperation, NativeLogicalTransportOptions, PlayerEnvironmentRestClient } from "@rsgcsg/sts2-connector-client";
import { AgentRunEvidence } from "../src/evidence.js";
import { validateAgentManifest } from "../src/agent-session-contracts.js";
import { NdjsonAgentSessionPort } from "../src/agent-session-port.js";
import { PolicyRuntime } from "../src/runtime.js";
import type { NativeAgentRuntimeOwner } from "../src/agent-native-runtime.js";
import { SyntheticNativeHttp } from "./native-runtime-fixtures.js";

const python = process.env.E6_SAMPLED_AGENT_PYTHON;
const packageRoot = process.env.E6_SAMPLED_PACKAGE_ROOT;
const sourcePath = fileURLToPath(new URL("../../../python", import.meta.url));
const helper = fileURLToPath(new URL("./fixtures/sampled-python-package.py", import.meta.url));
const shared = JSON.parse(readFileSync(new URL("../contracts/fixtures/sampled-current-carry-v1.json", import.meta.url), "utf8"));
const profileBytes = readFileSync(new URL("../../connector/contracts/native-logical-publication-profile-v1.json", import.meta.url));
const digest = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");

// Opt-in follows the existing numerical interop lane; normal portable tests need
// no numerical dependencies. This test admits only a public-training fixture.
describe("actual sampled Python package / SDK / Runtime / Evidence composition", () => {
  it.skipIf(!python || !packageRoot)("ACKs original Map/Inspect/Map once, discards unchanged/empty Current, then ACKs summary and closes", async () => {
    const root = await mkdtemp(join(tmpdir(), "sampled-python-runtime-"));
    let runtime: NativeAgentRuntimeOwner | undefined;
    let port: NdjsonAgentSessionPort | undefined;
    const source = new SyntheticNativeHttp(1); // Only route(), never start().
    const calls: { operation: string; body: JsonObject }[] = [];
    try {
      const receipt = JSON.parse(await readFile(join(packageRoot!, "receipt.json"), "utf8"));
      expect(receipt).toMatchObject({ scope: "synthetic_package_conformance_only", optimizer_updates: 1,
        input_spec: shared.input_spec, intra_threads: 2, interop_threads: 1, tbptt_advances: 4,
        publication_profile_sha256: digest(profileBytes), required_seams: 13 });
      const manifest = validateAgentManifest(JSON.parse(await readFile(join(packageRoot!, "agent.json"), "utf8")));
      expect(manifest.adapter.code_sha256).toBe(receipt.adapter_code_sha256);
      expect(manifest.artifact.id).toBe(receipt.package_model_id);
      expect(manifest.artifact.sha256).toBe(receipt.package_manifest_sha256);
      expect(manifest.input.attachment.required_seams).toEqual(JSON.parse(profileBytes.toString()).required_seams);
      expect(manifest.input.history_mode).toBe("sampled_current");
      source.capabilities.capture_coverage = JSON.parse(profileBytes.toString()).required_seams;
      source.subscription.coverage = structuredClone(source.capabilities.capture_coverage);
      const environment = {
        registerClient: async (input: { clientInstanceId: string }) => ({ raw: {}, data: {
          runtime_instance_id: source.capture.session.runtime_instance_id,
          client: { client_session_id: "client-fixture", client_instance_id: input.clientInstanceId }, controller: null } }),
        acquireController: async () => ({ raw: {}, data: {
          runtime_instance_id: source.capture.session.runtime_instance_id, status: "controller_acquired",
          controller: { controller_lease_id: "lease-fixture", controller_generation: 1, client_session_id: "client-fixture", expires_at: new Date(Date.now() + 120000).toISOString() } } }),
        releaseController: async () => ({ raw: {}, data: { runtime_instance_id: source.capture.session.runtime_instance_id,
          status: "controller_released", controller: null } }),
        nativeLogicalRequest: async (operation: NativeLogicalTransportOperation, body: JsonObject = {}, options: NativeLogicalTransportOptions = {}) => {
          options.signal?.throwIfAborted(); calls.push({ operation, body: structuredClone(body) });
          const response = source.route(new URL("http://never-contacted.invalid/" + (operation === "submit" ? "actions" : operation)), body);
          return { raw: structuredClone(response.value) as JsonObject, encodedByteCount: Buffer.byteLength(JSON.stringify(response.value)), statusCode: response.status ?? 200 };
        }
      } as unknown as PlayerEnvironmentRestClient;
      const setFrame = (name: string) => {
        const frame = structuredClone(shared.frames[name]);
        source.observation = frame.observation; source.capture = frame.capture;
        source.actions.splice(0, source.actions.length, ...frame.catalog); source.rehash();
        return Buffer.from(source.bytes);
      };
      setFrame("map_a");
      const evidence = await AgentRunEvidence.createSession({ root, agentManifest: manifest, mode: "auto",
        runtimeVersion: "fixture", runtimeCodeSha256: "1".repeat(64) });
      const processChild = spawn(python!, [helper, "--package", join(packageRoot!, "package"), "--manifest", join(packageRoot!, "agent.json")],
        { env: { ...process.env, PYTHONPATH: sourcePath, OMP_NUM_THREADS: "2", MKL_NUM_THREADS: "2" }, stdio: "pipe" });
      const exited = new Promise<{ code: number | null; signal: NodeJS.Signals | null }>((resolve, reject) => {
        processChild.once("error", reject); processChild.once("exit", (code, signal) => resolve({ code, signal }));
      });
      port = new NdjsonAgentSessionPort(processChild, manifest.adapter, manifest.limits);
      runtime = await PolicyRuntime.forAgent({ manifest, environment, port, evidence,
        runtimeIdentity: { version: "fixture", code_sha256: "1".repeat(64) }, mode: "auto",
        autoBudget: { maxSubmissions: 8, maxPolicyCalls: 16, deadlineMs: 60000 } });
      const originalBytes: Buffer[] = [];
      for (const [name, type, version] of [
        ["map_a", "delivered", 1], ["map_a", "awaited", 1], ["inspect_b", "delivered", 2],
        ["map_c", "delivered", 3], ["map_c", "awaited", 3], ["empty_wait", "awaited", 3],
        ["ready_summary", "closed", 4]
      ] as const) {
        const bytes = setFrame(name);
        const result = await runtime.tick();
        expect(result.type, JSON.stringify(result)).toBe(type);
        expect(runtime.status().session.state_version).toBe(version);
        if (type === "delivered" || type === "closed") originalBytes.push(bytes);
      }
      await runtime.stop();
      const exit = await Promise.race([exited, new Promise<never>((_, reject) => {
        const timer = setTimeout(() => reject(new Error("Python child did not exit after Close/Stop")), 5000); timer.unref();
      })]);
      // The production port owns shutdown via SIGKILL; record actual exit.
      expect(exit.code === 0 || exit.signal === "SIGKILL").toBe(true);
      const recorded = (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").map(line => JSON.parse(line));
      const samples = recorded.filter(e => e.kind === "agent_sample_input_stored");
      const consumed = recorded.filter(e => e.kind === "agent_consumed");
      expect(samples).toHaveLength(4); expect(consumed).toHaveLength(4);
      expect(recorded.filter(e => e.kind === "agent_sample_consume_ack_offered")).toHaveLength(4);
      expect(recorded.filter(e => e.kind === "agent_sample_query_discarded")).toHaveLength(3);
      for (const [i, sample] of samples.entries()) {
        const metadata = sample.payload.metadata;
        expect(await readFile(join(evidence.directory, metadata.observation.path))).toEqual(originalBytes[i]);
        expect(metadata.publication_index).toBeNull();
        const catalog = JSON.parse(await readFile(join(evidence.directory, metadata.catalog.path), "utf8"));
        expect(catalog).toEqual(shared.frames[["map_a", "inspect_b", "map_c", "ready_summary"][i]!].catalog);
        expect(consumed[i].payload.acknowledgement.state_version).toBe(i + 1);
        expect(consumed[i].payload.acknowledgement.acquisition_id).toBe(metadata.acquisition_id);
      }
      const submissions = calls.filter(call => call.operation === "submit");
      expect(submissions).toHaveLength(3); expect(calls.filter(call => call.operation === "catalog")).toHaveLength(7);
      for (const [i, submission] of submissions.entries())
        expect(shared.frames[["map_a", "inspect_b", "map_c"][i]!].catalog.map((a: { action_id: string }) => a.action_id)).toContain(submission.body.bound_action_id);
      const verify = () => execFileSync(python!, ["-c", "import sys;from pathlib import Path;from sts2_platform_evidence import verify_agent_session_run_evidence;r=verify_agent_session_run_evidence(Path(sys.argv[1]));print(r.status);print(r.findings);sys.exit(0 if r.status=='pass' else 1)", evidence.directory],
        { encoding: "utf8", env: { ...process.env, PYTHONPATH: fileURLToPath(new URL("../../evidence", import.meta.url)) }, timeout: 10000 });
      expect(verify()).toContain("pass");
      if (process.env.E6_SAMPLED_EVIDENCE_OUTPUT) await cp(evidence.directory, process.env.E6_SAMPLED_EVIDENCE_OUTPUT, { recursive: true, errorOnExist: true, force: false });
    } catch (error) {
      if (process.env.E6_SAMPLED_EVIDENCE_OUTPUT)
        await cp(root, process.env.E6_SAMPLED_EVIDENCE_OUTPUT + "-failed-" + root.split("/").at(-1), { recursive: true, errorOnExist: true, force: false });
      const diagnostics = port ? Buffer.concat((port as unknown as { stderrChunks: Buffer[] }).stderrChunks).toString() : "";
      throw new Error(`actual sampled package composition failed: ${String(error)}\n${diagnostics}`, { cause: error });
    } finally {
      await runtime?.stop().catch(() => undefined); port?.close(); await rm(root, { recursive: true, force: true });
    }
  }, 60000);
});
