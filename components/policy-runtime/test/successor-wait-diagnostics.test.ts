import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { once } from "node:events";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { mkdtemp, symlink, writeFile, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { Writable } from "node:stream";
import { SuccessorWaitDiagnostics, successorWaitStderrSink, type SuccessorWaitDiagnostic } from "../src/successor-wait-diagnostics.js";
import type { AnyDecisionBundle, PolicyManifest } from "../src/contracts.js";
// @ts-expect-error Shared synthetic public-protocol fixtures are JavaScript.
import { publicManifest, publicSnapshot, publicCapabilities } from "./fixtures/public-observation-fixture.mjs";

describe("bounded successor failure sink", () => {
  const previous = () => publicSnapshot(1, 1) as AnyDecisionBundle["observation"];
  it("caps only failure records, sends one explicit marker and then does no trace work", () => {
    const records: SuccessorWaitDiagnostic[] = [];
    const diagnostics = new SuccessorWaitDiagnostics(record => records.push(record), () => 0);
    for (let index = 0; index < 20; index += 1) {
      const trace = diagnostics.begin({ runId: "run", requestId: "request", decisionId: "decision", previous: previous(), maxAttempts: 1, intervalMs: 250 });
      trace?.pollStarted(); trace?.row(1, "same_basis", previous(), true); trace?.finish("exhausted");
      diagnostics.flush();
    }
    expect(records).toHaveLength(17);
    expect(records.filter(record => record.diagnostic === "successor_wait_failure")).toHaveLength(16);
    expect(records.at(-1)).toEqual({ diagnostic: "successor_wait_diagnostics_limit", failure_records_emitted: 16 });
    expect(diagnostics.begin({ runId: "run", requestId: "request", decisionId: "decision", previous: previous(), maxAttempts: 1, intervalMs: 0 })).toBeUndefined();
  });
  it("disables the failing sink and leaves later calls contained", () => {
    let writes = 0;
    const diagnostics = new SuccessorWaitDiagnostics(() => { writes += 1; throw new Error("sink-unavailable"); }, () => 0);
    const context = { runId: "run", requestId: "request", decisionId: "decision", previous: previous(), maxAttempts: 1, intervalMs: 0 };
    const trace = diagnostics.begin(context)!; trace.row(1, "no_whole_bundle"); trace.finish("exhausted");
    expect(() => diagnostics.flush()).not.toThrow();
    expect(diagnostics.begin(context)).toBeUndefined(); diagnostics.flush(); expect(writes).toBe(1);
  });
  it("caps serialized records including escaped strings, with honest dropped-row counts", () => {
    const records: SuccessorWaitDiagnostic[] = [];
    const diagnostics = new SuccessorWaitDiagnostics(record => records.push(record), () => 1e100);
    const basis = previous(); basis.snapshot_id = "x".repeat(254);
    const trace = diagnostics.begin({ runId: "r".repeat(254), requestId: "q".repeat(254), decisionId: "d".repeat(254), previous: basis, maxAttempts: 64, intervalMs: 250 })!;
    const observed = previous(); observed.snapshot_id = "s".repeat(254);
    observed.interaction.kind = "k".repeat(62);
    for (let attempt = 1; attempt <= 64; attempt += 1) { trace.pollStarted(); trace.row(attempt, "settling", observed, false); }
    trace.finish("exhausted"); diagnostics.flush();
    const record = records[0]!; if (record.diagnostic !== "successor_wait_failure") throw new Error("no failure record");
    expect(Buffer.byteLength(JSON.stringify(record)) + 1).toBeLessThanOrEqual(32 * 1024);
    expect(record.poll_count).toBe(64); expect(record.rows_total).toBe(64);
    expect(record.rows_dropped).toBe(64 - record.polls.length);
    expect(record.rows_dropped).toBeGreaterThan(0); expect(record.truncated).toBe(true);
    expect(record.polls.at(-1)!.attempt).toBe(64);
  });
  it.each([undefined, "0", "true", "yes"])("keeps stderr opt-in disabled for %s", flag => {
    const stream = new Writable({ write() { throw new Error("unexpected write"); } });
    expect(successorWaitStderrSink({ STS2_POLICY_RUNTIME_SUCCESSOR_DIAGNOSTICS: flag }, stream)).toBeUndefined();
    expect(stream.listenerCount("error")).toBe(0);
  });
  it("contains a later Writable error and disables further diagnostic writes with one listener", async () => {
    let writes = 0;
    const stream = new Writable({ write(_chunk, _encoding, done) {
      writes += 1; queueMicrotask(() => done(new Error("synthetic EPIPE")));
    } });
    const sink = successorWaitStderrSink({ STS2_POLICY_RUNTIME_SUCCESSOR_DIAGNOSTICS: "1" }, stream)!;
    expect(stream.listenerCount("error")).toBe(1);
    const failed = once(stream, "error");
    expect(() => sink({ diagnostic: "successor_wait_diagnostics_limit", failure_records_emitted: 16 })).not.toThrow();
    await failed;
    expect(stream.listenerCount("error")).toBe(1);
    sink({ diagnostic: "successor_wait_diagnostics_limit", failure_records_emitted: 16 });
    expect(writes).toBe(1);
    expect(() => stream.emit("error", new Error("later pending error"))).not.toThrow();
  });
  it("contains a synchronous write throw without removing the pending-error listener", () => {
    let writes = 0;
    const stream = new Writable();
    stream.write = () => { writes += 1; throw new Error("synchronous write failure"); };
    const sink = successorWaitStderrSink({ STS2_POLICY_RUNTIME_SUCCESSOR_DIAGNOSTICS: "1" }, stream)!;
    const record = { diagnostic: "successor_wait_diagnostics_limit" as const, failure_records_emitted: 16 as const };
    expect(() => { sink(record); sink(record); }).not.toThrow();
    expect(writes).toBe(1); expect(stream.listenerCount("error")).toBe(1);
    expect(() => stream.emit("error", new Error("later pending error"))).not.toThrow();
  });
});

describe("successor failure diagnostics through the actual generic CLI", () => {
  let compiled: string;
  beforeAll(async () => {
    compiled = await mkdtemp(join(tmpdir(), "successor-diagnostics-cli-"));
    execFileSync(process.execPath, [createRequire(import.meta.url).resolve("typescript/bin/tsc"), "-p",
      fileURLToPath(new URL("../tsconfig.json", import.meta.url)), "--outDir", compiled]);
    await symlink(fileURLToPath(new URL("../../../node_modules", import.meta.url)), join(compiled, "node_modules"), process.platform === "win32" ? "junction" : "dir");
    await writeFile(join(compiled, "package.json"), '{"type":"module"}');
  });
  afterAll(async () => { if (compiled) await rm(compiled, { recursive: true, force: true }); });
  it.each([
    ["default-off", "0", false], ["opt-in", "1", false], ["closed opt-in pipe", "1", true]
  ] as const)("preserves actual CLI recovery and exit with %s stderr", async (_label, flag, closedPipe) => {
    const root = await mkdtemp(join(tmpdir(), "successor-cli-run-"));
    const m = publicManifest() as PolicyManifest;
    m.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-1"; m.adapter_config = {};
    const artifact = Buffer.from("synthetic-no-model");
    m.artifact.path = "artifact.bin"; m.artifact.sha256 = createHash("sha256").update(artifact).digest("hex");
    await writeFile(join(root, "artifact.bin"), artifact); await writeFile(join(root, "manifest.json"), JSON.stringify(m));
    let observations = 0, submits = 0, releases = 0;
    let clientInstanceId = "";
    const connector = createServer(async (request, response) => {
      try {
        let text = ""; for await (const chunk of request) text += String(chunk);
        const body = text ? JSON.parse(text) : {};
        const path = new URL(request.url!, "http://fixture").pathname;
        let value: unknown;
        if (path.endsWith("/capabilities")) value = publicCapabilities();
        else if (path.endsWith("/snapshot")) {
          observations += 1; const observed = publicSnapshot(observations, 1);
          if (observations > 1) observed.session.runtime_instance_id = "different-runtime";
          value = observed;
        } else if (path.endsWith("/clients/register")) {
          clientInstanceId = body.client_instance_id;
          value = { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
            runtime_instance_id: "runtime", client: { client_session_id: "client", client_instance_id: clientInstanceId }, controller: null };
        }
        else if (path.includes("/controller/")) {
          const release = path.endsWith("/release"); if (release) releases += 1;
          value = { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1", runtime_instance_id: "runtime",
            status: release ? "controller_released" : "controller_acquired", detail: "synthetic controller",
            client: release ? { client_session_id: "client", client_instance_id: clientInstanceId } : null,
            controller: release ? null : { controller_lease_id: "lease", controller_generation: 1, client_session_id: "client", expires_at: new Date(Date.now() + 60_000).toISOString() } };
        } else if (path.endsWith("/actions")) {
          submits += 1; value = { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1", request_id: body.request_id,
            delivery: "delivered", action: { bound_action_id: body.bound_action_id, verb: "end_turn", arguments: [] },
            retry: { allowed: false, reason: "synthetic" }, successor: null };
        } else throw new Error(`unexpected fixture route:${path}`);
        response.writeHead(200, { "content-type": "application/json" }); response.end(JSON.stringify(value));
      } catch { response.writeHead(500); response.end("{}"); }
    });
    connector.listen(0, "127.0.0.1"); await once(connector, "listening");
    const address = connector.address(); if (!address || typeof address === "string") throw new Error("no fixture address");
    const listener = createServer(); listener.listen(0, "127.0.0.1"); await once(listener, "listening");
    const listen = listener.address(); if (!listen || typeof listen === "string") throw new Error("no CLI port");
    await new Promise<void>(resolve => listener.close(() => resolve()));
    const script = `process.stdout.write(JSON.stringify({schema:'sts2.policy-runtime/policy-port-1',message_type:'ready',adapter:${JSON.stringify(m.adapter)}})+'\\n');require('node:readline').createInterface({input:process.stdin}).on('line',line=>{const r=JSON.parse(line);process.stdout.write(JSON.stringify({schema:r.schema,message_type:'decision',request_id:r.request_id,output:{candidate_digest:r.input.candidate_digest,scores:Array(r.input.candidate_count).fill(1),selected_index:0}})+'\\n');});`;
    let child: ChildProcess | undefined, closed: Promise<unknown[]> | undefined;
    let stdout = "", stderr = "";
    try {
      child = spawn(process.execPath, [join(compiled, "cli.js"), "--manifest", join(root, "manifest.json"),
        "--adapter-command", process.execPath, "--adapter-arg", "-e", "--adapter-arg", script,
        "--connector-endpoint", `http://127.0.0.1:${address.port}`, "--listen-port", String(listen.port),
        "--evidence-root", join(root, "evidence"), "--mode", "human"], {
        stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, STS2_POLICY_RUNTIME_SUCCESSOR_DIAGNOSTICS: flag }
      });
      closed = once(child, "close"); child.stderr!.on("data", data => { stderr += String(data); });
      const startup = await new Promise<{ address: string; run_id: string }>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`CLI startup timeout:${stderr}`)), 5_000);
        child!.once("error", error => { clearTimeout(timer); reject(error); });
        child!.once("exit", code => { clearTimeout(timer); reject(new Error(`CLI early exit:${code}:${stderr}`)); });
        child!.stdout!.on("data", data => {
          stdout += String(data); const newline = stdout.indexOf("\n");
          if (newline >= 0) { clearTimeout(timer); resolve(JSON.parse(stdout.slice(0, newline))); }
        });
      });
      const headers = { "content-type": "application/json", "x-sts2-policy-run-id": startup.run_id,
        "x-sts2-game-instance-id": "runtime", "x-sts2-recovery-epoch": "0" };
      if (closedPipe) {
        const stderrClosed = once(child.stderr!, "close"); child.stderr!.destroy(); await stderrClosed;
      }
      expect((await fetch(`${startup.address}/v2/mode`, { method: "POST", headers, body: '{"mode":"one_step"}' })).status).toBe(200);
      const response = await fetch(`${startup.address}/v2/tick`, { method: "POST", headers, body: '{"max_ticks":1}' });
      expect(response.status).toBe(200); const result = await response.json();
      expect(result.status, JSON.stringify(result)).toMatchObject({ tainted: true, controller: "released", mode: "human" });
      const status = await (await fetch(`${startup.address}/status`)).json();
      expect(status.status).toMatchObject({ tainted: true, controller: "released", mode: "human",
        taint_reason: "unknown_successor_after_submit:successor_environment_identity_drift" });
      expect((await fetch(`${startup.address}/v2/stop`, { method: "POST", headers, body: "{}" })).status).toBe(200);
      const [code] = await closed; expect(code, stderr).toBe(0);
      expect(observations).toBe(2); expect(submits).toBe(1); expect(releases).toBe(1);
      expect(stdout).not.toContain("successor_wait_failure");
      if (flag === "0" || closedPipe) expect(stderr).toBe("");
      else {
        const lines = stderr.trim().split("\n"); expect(lines).toHaveLength(1);
        expect(JSON.parse(lines[0]!)).toMatchObject({ diagnostic: "successor_wait_failure", outcome: "error", poll_count: 1,
          polls: [{ category: "environment_identity_drift", snapshot_id: "snapshot-2", sequence: 2 }] });
      }
    } finally {
      if (child && child.exitCode === null && child.signalCode === null && !child.killed) child.kill("SIGKILL");
      if (closed) await closed;
      await new Promise<void>(resolve => connector.close(() => resolve()));
      await rm(root, { recursive: true, force: true });
    }
  });
});
