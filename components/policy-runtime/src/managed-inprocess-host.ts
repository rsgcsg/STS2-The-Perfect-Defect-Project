#!/usr/bin/env node
/**
 * One process for one Managed Host service and the Policy Runtime that drives it.
 *
 *   node dist/managed-inprocess-host.js --host-package <dir> --runtime-stdout <file>
 *     -- <dir>/tools/managed-host-service.mjs <managed-host-service arguments...>
 *
 * The Host part is the unchanged `managed-host-service` tool body: it starts the
 * native child, listens on loopback for its manager (reset/close/recover/status)
 * and writes the same private attachment descriptors. The Runtime part is the
 * unchanged CLI, whose client exchanges enter the Host service's own dispatch
 * in-process instead of through a socket.
 *
 * stdin, one JSON object per line:
 *   {"schema":"sts2.policy-runtime/inprocess-runtime-request-1","argv":[...]}  starts the Runtime CLI once;
 *   {"schema":"sts2.policy-runtime/inprocess-runtime-stop-1"} or end of stdin  requests the Runtime Stop
 *   (the same owner shutdown a separate Runtime runs on SIGTERM).
 * `--runtime-stdout` receives the Runtime startup line, then
 *   {"schema":"sts2.policy-runtime/inprocess-runtime-exit-1","code":0|1}.
 * The process exits after the Host's own close and after the Runtime ended.
 */
import { closeSync, lstatSync, openSync, realpathSync, writeSync } from "node:fs";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";
import process from "node:process";
import { main } from "./cli-main.js";
import type { ManagedInProcessService } from "./managed.js";

export const INPROCESS_REQUEST_SCHEMA = "sts2.policy-runtime/inprocess-runtime-request-1";
export const INPROCESS_STOP_SCHEMA = "sts2.policy-runtime/inprocess-runtime-stop-1";
export const INPROCESS_EXIT_SCHEMA = "sts2.policy-runtime/inprocess-runtime-exit-1";

type HostTool = { runManagedHostServiceTool(args: string[], options: { root: string; installSignalHandlers: boolean }):
  Promise<{ service: { inProcess: ManagedInProcessService; close(options: { force: boolean }): Promise<void> }; closed: Promise<void> }> };

function value(args: string[], name: string): string {
  const index = args.indexOf(name);
  const found = index >= 0 ? args[index + 1] : undefined;
  if (!found) throw new Error(`${name} is required`);
  return found;
}

async function run(): Promise<void> {
  const all = process.argv.slice(2);
  const split = all.indexOf("--");
  if (split < 0) throw new Error("in-process launcher requires -- <managed-host-service tool> <arguments>");
  const own = all.slice(0, split), [tool, ...hostArgs] = all.slice(split + 1);
  const hostPackage = realpathSync(resolve(value(own, "--host-package")));
  if (!tool || realpathSync(resolve(tool)) !== join(hostPackage, "tools", "managed-host-service.mjs"))
    throw new Error("in-process launcher tool must be the pinned Host package's managed-host-service.mjs");
  const stdoutPath = resolve(value(own, "--runtime-stdout"));
  const stat = lstatSync(stdoutPath);
  if (!stat.isFile() || stat.size !== 0 || (process.platform !== "win32" && (stat.mode & 0o077) !== 0))
    throw new Error("--runtime-stdout must be an existing empty private regular file");
  const out = openSync(stdoutPath, "a");
  const writeLine = (line: string): void => { writeSync(out, line.endsWith("\n") ? line : `${line}\n`); };

  const hostTool = await import(pathToFileURL(join(hostPackage, "src", "managed-host-service-tool.mjs")).href) as HostTool;
  const { service, closed } = await hostTool.runManagedHostServiceTool(hostArgs, { root: hostPackage, installSignalHandlers: false });

  let runtime: Promise<void> | null = null;
  let stopRuntime: (() => Promise<void>) | null = null;
  let stopRequested = false;
  const requestStop = (): void => {
    stopRequested = true;
    if (stopRuntime) void stopRuntime();
  };
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  input.on("line", (line) => {
    let message: Record<string, unknown>;
    try { message = JSON.parse(line) as Record<string, unknown>; }
    catch { process.stderr.write("in-process launcher: stdin line is not JSON; requesting Runtime Stop\n"); requestStop(); return; }
    if (message.schema === INPROCESS_STOP_SCHEMA) { requestStop(); return; }
    if (message.schema !== INPROCESS_REQUEST_SCHEMA || runtime || stopRequested
      || !Array.isArray(message.argv) || message.argv.some((item) => typeof item !== "string")) {
      process.stderr.write("in-process launcher: invalid or repeated Runtime request; requesting Runtime Stop\n");
      requestStop(); return;
    }
    runtime = main(message.argv as string[], {
      inProcess: service.inProcess, writeLine,
      onShutdownRequest: (shutdown) => { stopRuntime = shutdown; if (stopRequested) void shutdown(); }
    }).then(() => { writeLine(JSON.stringify({ schema: INPROCESS_EXIT_SCHEMA, code: 0 })); },
      (error: unknown) => {
        process.stderr.write(`${error instanceof Error ? error.stack ?? error.message : String(error)}\n`);
        writeLine(JSON.stringify({ schema: INPROCESS_EXIT_SCHEMA, code: 1 }));
      });
  });
  input.on("close", requestStop);
  for (const signal of ["SIGINT", "SIGTERM"] as const) {
    process.on(signal, () => {
      requestStop();
      void (runtime ?? Promise.resolve()).finally(() => service.close({ force: true }).catch(() => {
        process.stderr.write("Managed service cleanup unconfirmed.\n"); process.exitCode = 1;
      }));
    });
  }
  // Host close (manager close or signal) also ends a Runtime that is still running.
  await closed;
  requestStop();
  await (runtime ?? Promise.resolve());
  input.close();
  closeSync(out);
  process.exit();
}

run().catch((error: unknown) => {
  process.stderr.write(`${error instanceof Error ? error.stack ?? error.message : String(error)}\n`);
  process.exit(1);
});
