import { spawn } from "node:child_process";
import { once } from "node:events";
import { describe, expect, it } from "vitest";
import { openRealStoreTransport, type RealStoreStartupFailureDiagnostics } from "./realStoreTransport.mjs";

// These real OS child fixtures test transport startup/cleanup only. Store
// bytes/accounting remain covered by the actual linked C# producer tests.
describe("actual bridge backend lifecycle before SDK deadlines", () => {
  it("waits for a backend command acknowledgement before publishing an HTTP endpoint", async () => {
    const child = spawn(process.execPath, ["--input-type=module", "-e", `
      import { createInterface } from 'node:readline';
      let release; const gate = new Promise(resolve => { release = resolve; });
      process.on('message', () => release());
      const lines = createInterface({ input: process.stdin });
      for await (const line of lines) {
        if (JSON.parse(line).operation === 'stats') { process.send('stats-received'); await gate;
          process.stdout.write(JSON.stringify({value:{charged_bytes:0,charged_buffers:0,handles:0,live_captures:0,now:0}})+'\\n'); }
      }
      process.disconnect();
    `], { stdio: ["pipe", "pipe", "pipe", "ipc"] });
    let published = false;
    const creating = openRealStoreTransport({ backendFactory: () => child });
    void creating.then(() => { published = true; });
    await once(child, "message"); expect(published).toBe(false);
    child.send("release-startup");
    const transport = await creating;
    try { expect(transport.endpoint).toMatch(/^http:\/\/127\.0\.0\.1:/); }
    finally { await transport.close(); }
    expect(child.exitCode).toBe(0);
  });
  it("fails and reaps its own backend when readiness never arrives", async () => {
    const child = spawn(process.execPath, ["-e", "process.stdin.resume();"], { stdio: ["pipe", "pipe", "pipe"] });
    const failure = await openRealStoreTransport({ backendFactory: () => child, readinessTimeoutMs: 200 }).then(
      () => { throw new Error("silent backend unexpectedly became ready"); },
      error => error as Error & { realStoreStartupDiagnostics: RealStoreStartupFailureDiagnostics });
    expect(failure.message).toBe("bridge readiness deadline");
    const diagnostic = failure.realStoreStartupDiagnostics;
    expect(diagnostic.before_cleanup.backend_pid).toBe(child.pid);
    expect(diagnostic.before_cleanup.pending_operations).toEqual(["stats"]);
    expect(diagnostic.before_cleanup.events.at(-1)?.phase).toBe("backend_requested");
    expect(diagnostic.before_cleanup.events.at(-1)?.operation).toBe("stats");
    expect(diagnostic.cleanup).toEqual({ code: child.exitCode, signal: child.signalCode });
    expect(diagnostic.stderr_original).toEqual(Buffer.alloc(0));
    expect(diagnostic.stderr_bytes).toBe(0);
    expect(diagnostic.stderr_retained_bytes).toBe(0);
    expect(diagnostic.stderr_truncated).toBe(false);
    expect(child.exitCode !== null || child.signalCode !== null).toBe(true);
  });
  it("keeps the original startup error and bounded backend stderr through actual cleanup", async () => {
    const original = new Error("test pipe failed before typed acknowledgement");
    const child = spawn(process.execPath, ["-e", `
      process.stdin.once('data', () => process.stderr.write(Buffer.alloc(65537, 255)));
      process.stdin.resume();
    `], { stdio: ["pipe", "pipe", "pipe"] });
    let observedBytes = 0;
    const observed = new Promise<void>(resolve => child.stderr!.on("data", bytes => {
      observedBytes += bytes.length;
      if (observedBytes === 65537) resolve();
    }));
    // Handle startup rejection immediately, even if stderr never arrives.
    const opening = openRealStoreTransport({ backendFactory: () => child, readinessTimeoutMs: 2000 }).then(
      transport => ({ transport }), error => ({ error }));
    try {
      // Consume real stderr before injecting this exact pipe error. This is a
      // lifecycle fixture, not a fake Store or a successful readiness response.
      await Promise.race([observed, opening.then(() => {
        throw new Error("backend startup settled before original stderr was observed");
      })]);
      child.stdin!.emit("error", original);
      const outcome = await opening;
      expect("error" in outcome ? outcome.error : undefined).toBe(original);
      const diagnostic = (original as Error & {
        realStoreStartupDiagnostics: RealStoreStartupFailureDiagnostics }).realStoreStartupDiagnostics;
      expect(diagnostic.before_cleanup.pending_operations).toEqual([]);
      expect(diagnostic.before_cleanup.events.some(event => event.phase === "backend_requested" && event.operation === "stats")).toBe(true);
      expect(diagnostic.stderr_original).toEqual(Buffer.alloc(65536, 255));
      expect(diagnostic.stderr_bytes).toBe(65537);
      expect(diagnostic.stderr_retained_bytes).toBe(65536);
      expect(diagnostic.stderr_truncated).toBe(true);
      expect(diagnostic.before_cleanup.events.length).toBeLessThanOrEqual(12);
      expect(diagnostic.cleanup).toEqual({ code: child.exitCode, signal: child.signalCode });
      expect(child.exitCode !== null || child.signalCode !== null).toBe(true);
    } finally {
      const outcome = await opening;
      if ("transport" in outcome) await outcome.transport.close();
    }
  });
  it("fails closed for an exited or malformed initial backend", async () => {
    for (const source of ["process.exit(7)", "process.stdin.once('data',()=>process.stdout.write(JSON.stringify({value:{now:0}})+'\\n')); process.stdin.resume();"]) {
      const child = spawn(process.execPath, ["-e", source], { stdio: ["pipe", "pipe", "pipe"] });
      await expect(openRealStoreTransport({ backendFactory: () => child })).rejects.toThrow(/bridge exited|fresh empty actual store/);
      expect(child.exitCode !== null || child.signalCode !== null).toBe(true);
    }
  });
});
