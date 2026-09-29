import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";
import test from "node:test";

const fixture = fileURLToPath(new URL("../tools/test-fixtures/managed-driver-shutdown.mjs", import.meta.url));

function isAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { if (error.code === "ESRCH") return false; throw error; }
}

async function exercise(mode) {
  const root = mkdtempSync(path.join(tmpdir(), "managed-driver-shutdown-"));
  const child = spawn(process.execPath, [fixture, path.join(root, "native-entered"),
    ...(mode === "upgrade" ? ["--stubborn"] : [])],
    { stdio: ["pipe", "pipe", "pipe"] });
  const output = [];
  const lines = readline.createInterface({ input: child.stdout });
  lines.on("line", (line) => output.push(JSON.parse(line)));
  const exited = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
  let nativePid;
  try {
    const deadline = Date.now() + 3_000;
    while (output.length === 0 && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.equal(output[0]?.type, "ready");
    nativePid = output[0].native_pid;
    assert.equal(isAlive(nativePid), true);
    if (mode === "explicit" || mode === "upgrade") {
      child.stdin.write('{"command":"close","request_id":"close"}\n');
      if (mode === "upgrade") {
        await new Promise((resolve) => setTimeout(resolve, 50));
        assert.deepEqual(output.map((item) => item.type), ["ready"]);
        child.stdin.end();
      }
    } else {
      child.stdin.write('{"command":"reset","seed":"SEED","request_id":"pending"}\n');
      await new Promise((resolve) => setTimeout(resolve, 20));
      if (mode === "eof") child.stdin.end();
      else { child.kill("SIGTERM"); child.kill("SIGTERM"); }
    }
    let timeout;
    const exit = await Promise.race([exited,
      new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("driver shutdown timeout")), 4_000); })
    ]).finally(() => clearTimeout(timeout));
    assert.ok(exit.code === 0 || exit.signal === "SIGTERM");
    assert.equal(isAlive(nativePid), false, "the owner must reap its synthetic native child");
    assert.deepEqual(output.map((item) => item.type),
      mode === "explicit" ? ["ready", "close_result"] : ["ready"],
      "a late pending request must not publish success after shutdown");
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    if (nativePid != null && isAlive(nativePid)) {
      // Test cleanup is scoped to the child PID that this fixture created.
      try { process.kill(nativePid, "SIGKILL"); } catch { /* already exited */ }
    }
    rmSync(root, { recursive: true, force: true });
  }
}

test("EOF interrupts a pending native command and reaps the child", () => exercise("eof"));
test("explicit close returns once and reaps the native child", () => exercise("explicit"));
if (process.platform !== "win32") {
  test("repeated SIGTERM uses the same bounded shutdown and reaps the child", () => exercise("signal"));
  test("EOF upgrades a pending graceful close without publishing a late result", () => exercise("upgrade"));
}
