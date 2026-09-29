import { writeFileSync, existsSync } from "node:fs";
import readline from "node:readline";
import { fileURLToPath } from "node:url";
import { JsonLineProcess } from "../../src/json-line-process.mjs";
import { ManagedPeDriverSession } from "../../src/managed-pe-driver-session.mjs";
import { serveManagedPeDriver } from "../../src/managed-pe-driver-loop.mjs";

const marker = process.argv[2];
if (process.argv.includes("--native")) {
  if (process.argv.includes("--stubborn") && process.platform !== "win32") {
    process.on("SIGTERM", () => writeFileSync(`${marker}.graceful-signal-entered`, "entered"));
  }
  process.stdout.write('{"type":"native_ready"}\n');
  const input = readline.createInterface({ input: process.stdin });
  input.on("line", (line) => {
    const request = JSON.parse(line);
    if (request.cmd === "hold") writeFileSync(marker, "entered");
    if (request.cmd === "quit") process.exit(0);
  });
} else {
  const native = new JsonLineProcess({ command: process.execPath,
    args: [fileURLToPath(import.meta.url), marker, "--native",
      ...(process.argv.includes("--stubborn") ? ["--stubborn"] : [])] });
  await native.nextMessage();
  const pending = native.request({ cmd: "hold" }, 30_000);
  pending.catch(() => undefined);
  while (!existsSync(marker)) await new Promise((resolve) => setTimeout(resolve, 5));
  if (process.argv.includes("--ignore-eof")) {
    process.stdin.resume();
    setInterval(() => undefined, 1_000);
    process.stdout.write(`${JSON.stringify({ type: "ready", native_pid: native.pid })}\n`);
  } else {
  const session = {
    async mount() { writeFileSync(`${marker}.reset-entered`, "entered"); return pending; },
    async close({ force = false, timeoutMs = 5_000 } = {}) {
      if (process.argv.includes("--reject-close")) throw new Error("synthetic cleanup failed");
      return native.stop({ request: force ? null : { cmd: "quit" }, timeoutMs, force });
    },
    observe() { throw new Error("No mounted page"); },
    async submit() { throw new Error("No mounted page"); }
  };
  const driver = new ManagedPeDriverSession({ session, runtime: { process: native,
    build: "synthetic", runtimeIdentity: "synthetic",
    adapterRuntimeInstanceId: "synthetic" }, environmentFingerprint: "synthetic" });
  serveManagedPeDriver(driver);
  process.stdout.write(`${JSON.stringify({ type: "ready", native_pid: native.pid })}\n`);
  }
}
