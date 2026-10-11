import { spawn } from "node:child_process";
import { once } from "node:events";
import { describe, expect, it } from "vitest";
import { openRealStoreTransport } from "./realStoreTransport.mjs";

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
    await expect(openRealStoreTransport({ backendFactory: () => child, readinessTimeoutMs: 200 })).rejects.toThrow("bridge readiness deadline");
    expect(child.exitCode !== null || child.signalCode !== null).toBe(true);
  });
  it("fails closed for an exited or malformed initial backend", async () => {
    for (const source of ["process.exit(7)", "process.stdin.once('data',()=>process.stdout.write(JSON.stringify({value:{now:0}})+'\\n')); process.stdin.resume();"]) {
      const child = spawn(process.execPath, ["-e", source], { stdio: ["pipe", "pipe", "pipe"] });
      await expect(openRealStoreTransport({ backendFactory: () => child })).rejects.toThrow(/bridge exited|fresh empty actual store/);
      expect(child.exitCode !== null || child.signalCode !== null).toBe(true);
    }
  });
});
