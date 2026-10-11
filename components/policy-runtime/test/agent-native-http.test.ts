import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { once } from "node:events";
import { spawn, type ChildProcess } from "node:child_process";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { mkdtemp, symlink, writeFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { nativeRuntimeFixture, CHILD } from "./native-runtime-fixtures.js";
import { startPolicyRuntimeHttpServer } from "../src/server.js";
import { fixtureProgress } from "./fixture-progress.js";

async function freePort(): Promise<number> {
  const listener = createServer(); listener.listen(0, "127.0.0.1"); await once(listener, "listening");
  const address = listener.address(); if (!address || typeof address === "string") throw new Error("no ephemeral test port");
  const port = address.port; await new Promise<void>(resolve => listener.close(() => resolve())); return port;
}

describe("same native Runtime HTTP/CLI application surface", () => {
  it("enforces run/game/epoch guards for explicit original reconciliation and never dispatches a mismatched request", async () => {
    const f = await nativeRuntimeFixture(); const service = await startPolicyRuntimeHttpServer(f.runtime);
    try {
      const binding = await (await fetch(`${service.address}/v2/environment`)).json();
      const headers = { "content-type": "application/json", "x-sts2-policy-run-id": f.evidence.runId,
        "x-sts2-game-instance-id": binding.runtime_instance_id, "x-sts2-recovery-epoch": String(binding.recovery_epoch) };
      const mode = await fetch(`${service.address}/v2/mode`, { method: "POST", headers, body: JSON.stringify({ mode: "auto" }) });
      expect(mode.status).toBe(200);
      f.source.behavior = "pending";
      const tick = await fetch(`${service.address}/v2/tick`, { method: "POST", headers, body: JSON.stringify({ max_ticks: 1 }) });
      const pending = await tick.json(); expect(pending.status.pending_request.status).toBe("pending");
      const id = pending.status.pending_request.request_id;
      for (const [field, invalid, expected] of [
        ["x-sts2-policy-run-id", "wrong-run", "runtime_run_mismatch"],
        ["x-sts2-game-instance-id", "wrong-game", "runtime_game_mismatch"],
        ["x-sts2-recovery-epoch", "900", "runtime_recovery_epoch_mismatch"]
      ] as const) {
        const reply = await fetch(`${service.address}/v2/reconcile`, { method: "POST", headers: { ...headers, [field]: invalid }, body: JSON.stringify({ request_id: id }) });
        expect(reply.status).toBe(409); expect((await reply.json()).error).toBe(expected);
      }
      const mismatch = await fetch(`${service.address}/v2/reconcile`, { method: "POST", headers, body: JSON.stringify({ request_id: "other" }) });
      expect(mismatch.status).toBe(409); expect((await mismatch.json()).error).toBe("runtime_pending_request_mismatch");
      expect(f.source.requests.filter(r => r.path.includes("/actions/"))).toHaveLength(0);
      const reconciled = await fetch(`${service.address}/v2/reconcile`, { method: "POST", headers, body: JSON.stringify({ request_id: id }) });
      expect(await reconciled.json()).toMatchObject({ schema: "sts2.policy-runtime/http-2", request_id: id, resolution: "resolved",
        status: { schema: "sts2.policy-runtime/agent-session-status-1", mode: "human", pending_request: null } });
      expect(f.source.requests.filter(r => r.path.includes("/actions/"))).toHaveLength(1);
    } finally { await f.close(); await service.close(); }
  });
  describe("fresh native CLI source and process", () => {
    const progress = fixtureProgress("native-cli-source-process");
    let compiled: string | undefined;
    let child: ChildProcess | undefined, closed: Promise<unknown[]> | undefined;
    beforeAll(async () => {
      // Source preparation has the existing setup allowance. Application and
      // startup assertions keep their existing five-second clocks.
      compiled = await mkdtemp(join(tmpdir(), "native-runtime-cli-source-"));
      const project = fileURLToPath(new URL("../../../", import.meta.url));
      progress("compile-start");
      execFileSync(process.execPath, [createRequire(import.meta.url).resolve("typescript/bin/tsc"), "-p", fileURLToPath(new URL("../tsconfig.json", import.meta.url)), "--outDir", compiled]);
      progress("source-compiled");
      await symlink(join(project, "node_modules"), join(compiled, "node_modules"), process.platform === "win32" ? "junction" : "dir");
      await writeFile(join(compiled, "package.json"), '{"type":"module"}');
      progress("source-linked");
    });
    afterAll(async () => {
      if (child && child.exitCode === null && child.signalCode === null && !child.killed) child.kill("SIGKILL");
      if (closed) await closed;
      if (compiled) await rm(compiled, { recursive: true, force: true });
      progress("compiled-source-cleaned");
    });
    it("native CLI strictly selects the new manifest and produces public startup/status without legacy fields or executable details", async () => {
      const f = await nativeRuntimeFixture(); await f.runtime.stop();
      // Its first temporary Runtime is closed; the CLI owns a separate explicit run.
      const listen = await freePort();
      progress("application-fixture-ready");
      try {
        const cli = join(compiled!, "cli.js");
        child = spawn(process.execPath, [cli, "--manifest", f.manifestPath, "--adapter-command", process.execPath,
          "--adapter-arg", CHILD, "--adapter-arg", f.manifestPath, "--connector-endpoint", f.source.address,
          "--listen-port", String(listen), "--evidence-root", f.root, "--mode", "human"], { stdio: ["ignore", "pipe", "pipe"] });
        closed = once(child, "close");
        progress("cli-spawned");
        let diagnostics = ""; child.stderr!.on("data", data => { diagnostics += String(data); });
        const startup = await new Promise<Record<string, unknown>>((resolve, reject) => {
          let text = ""; const timer = setTimeout(() => reject(new Error(`CLI startup timeout: ${diagnostics}`)), 5000);
          child!.once("exit", code => { clearTimeout(timer); reject(new Error(`CLI early exit ${code}: ${diagnostics}`)); });
          child!.stdout!.on("data", chunk => {
            text += String(chunk); const newline = text.indexOf("\n");
            if (newline >= 0) { clearTimeout(timer); resolve(JSON.parse(text.slice(0, newline))); }
          });
        });
        progress("startup-observed");
        expect(startup).toMatchObject({ schema: "sts2.policy-runtime/agent-session-startup-1", managed_environment: null,
          agent_manifest_id: f.manifest.manifest_id, mode: "human", adapter: f.manifest.adapter });
        expect(startup).not.toHaveProperty("policy_artifact_sha256"); expect(startup).not.toHaveProperty("manifest_id");
        expect(JSON.stringify(startup)).not.toContain(CHILD); expect(JSON.stringify(startup)).not.toContain(f.manifestPath);
        const status = await (await fetch(`${startup.address}/status`)).json();
        expect(status.status.schema).toBe("sts2.policy-runtime/agent-session-status-1");
        expect(status.status.agent_manifest_sha256).toBe(startup.agent_manifest_sha256);
        progress("status-verified");
        const stopped = await fetch(`${startup.address}/v2/stop`, { method: "POST",
          headers: { "content-type": "application/json", "x-sts2-policy-run-id": String(startup.run_id) }, body: "{}" });
        expect(stopped.status).toBe(200); expect((await stopped.json()).status.lifecycle).toBe("stopped");
        progress("stop-confirmed");
        const [code] = await closed; expect(code, diagnostics).toBe(0);
        progress("cli-close-observed");
      } finally {
        if (child && child.exitCode === null && child.signalCode === null && !child.killed) child.kill("SIGKILL");
        if (closed) await closed;
        await f.close();
        progress("application-fixture-closed");
      }
    });
  });
});
