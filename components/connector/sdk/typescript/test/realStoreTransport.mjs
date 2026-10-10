import { spawn, execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

let preparedDll;
function prepareBridge() {
  if (process.env.STS2_OWNED_CURRENT_BRIDGE_DLL) return process.env.STS2_OWNED_CURRENT_BRIDGE_DLL;
  if (preparedDll) return preparedDll;
  const project = fileURLToPath(new URL("../../../host/tests/STS2Connector.NativeLogical.OwnedCurrent.Bridge/STS2Connector.NativeLogical.OwnedCurrent.Bridge.csproj", import.meta.url));
  execFileSync(process.env.STS2_OWNED_CURRENT_DOTNET ?? "dotnet", ["build", project,
    "--nologo", "--verbosity", "minimal", "--maxcpucount:1", "-p:BuildInParallel=false", "-p:UseSharedCompilation=false", "--ignore-failed-sources"], {
    encoding: "utf8", timeout: 60000,
    env: { ...process.env, DOTNET_PROCESSOR_COUNT: "2", DOTNET_GENERATE_ASPNET_CERTIFICATE: "false", DOTNET_SKIP_FIRST_TIME_EXPERIENCE: "1", DOTNET_CLI_TELEMETRY_OPTOUT: "1" }
  });
  preparedDll = join(dirname(project), "bin/Debug/net9.0/STS2Connector.NativeLogical.OwnedCurrent.Bridge.dll");
  return preparedDll;
}

function spawnBackend() {
  return spawn(process.env.STS2_OWNED_CURRENT_DOTNET ?? "dotnet", [prepareBridge()], {
    env: { ...process.env, DOTNET_PROCESSOR_COUNT: "2", DOTNET_GENERATE_ASPNET_CERTIFICATE: "false", DOTNET_SKIP_FIRST_TIME_EXPERIENCE: "1", DOTNET_CLI_TELEMETRY_OPTOUT: "1" }, stdio: ["pipe", "pipe", "pipe"] });
}

/** Shared test transport, not a gameplay service. Production C# owns only
 * Current/Read/catalog/retain/release bytes and accounting here. Callers declare
 * their synthetic public-frame/control/action fixtures explicitly. */
export async function openRealStoreTransport(options = {}) {
  const started = performance.now(), events = [];
  const trace = (phase, operation = null) => {
    if (events.length < 4096) events.push({ phase, operation, elapsed_ms: performance.now() - started });
  };
  trace("backend_preparation_started");
  const backend = (options.backendFactory ?? spawnBackend)();
  trace("backend_spawned");
  const pending = [], stderr = [], requests = [];
  let failure, exited = false, afterReply, beforeRequest, stderrBytes = 0;
  const fail = error => {
    trace("backend_failure");
    failure ??= error;
    for (const caller of pending.splice(0)) caller.reject(failure);
  };
  const closed = new Promise(resolve => backend.once("close", (code, signal) => {
    trace("backend_closed");
    exited = true;
    fail(new Error(`bridge exited ${code}/${signal}: ${Buffer.concat(stderr).toString()}`));
    resolve({ code, signal });
  }));
  backend.stderr.on("data", value => {
    const bytes = Buffer.from(value).subarray(0, Math.max(0, 65536 - stderrBytes));
    if (bytes.length) { stderr.push(bytes); stderrBytes += bytes.length; }
  });
  backend.on("error", fail); backend.stdin.on("error", fail);
  createInterface({ input: backend.stdout }).on("line", line => {
    const caller = pending.shift();
    if (!caller) { fail(new Error("unsolicited bridge reply")); return; }
    try {
      const reply = JSON.parse(line);
      if (reply.error) caller.reject(Object.assign(new Error(reply.error.code + ":" + reply.error.detail), reply.error));
      else { trace("backend_replied", caller.operation); caller.resolve(reply.value); }
    } catch (error) { caller.reject(error); fail(error); }
  });
  const call = (operation, body = {}) => new Promise((resolve, reject) => {
    if (failure || exited) { reject(failure ?? new Error("bridge closed")); return; }
    trace("backend_requested", operation);
    pending.push({ resolve, reject, operation });
    backend.stdin.write(JSON.stringify({ operation, body }) + "\n", error => { if (error) fail(error); });
  });
  const stopBackend = async force => {
    if (exited) return closed;
    if (force) backend.kill(); else backend.stdin.end();
    const timer = setTimeout(() => backend.kill("SIGKILL"), 2000);
    try { return await closed; } finally { clearTimeout(timer); }
  };
  let server;
  try {
    // A real typed command acknowledges the initialized producer before HTTP
    // requests start spending SDK deadlines. Merely spawning/listening is not
    // readiness. This startup budget never changes the SDK's request timeout.
    const readyMs = options.readinessTimeoutMs ?? 10000;
    if (!Number.isInteger(readyMs) || readyMs < 1 || readyMs > 10000) throw new Error("invalid bridge readiness deadline");
    let timer;
    const ready = await Promise.race([call("stats"), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("bridge readiness deadline")), readyMs);
    })]).finally(() => clearTimeout(timer));
    if (!ready || ["charged_bytes", "charged_buffers", "handles", "live_captures", "now"].some(key => ready[key] !== 0))
      throw new Error("bridge readiness must acknowledge a fresh empty actual store");
    trace("backend_ready");
    server = createServer(async (request, response) => {
      try {
        const chunks = []; for await (const chunk of request) chunks.push(Buffer.from(chunk));
        const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {};
        const url = new URL(request.url, `http://${request.headers.host}`), operation = url.pathname.split("/").at(-1);
        requests.push({ operation, body });
        trace("http_requested", operation);
        await beforeRequest?.(operation, body, url);
        const value = ["current", "current_owned", "read", "catalog", "retain", "release"].includes(operation) && !url.pathname.includes("/controller/")
          ? await call(operation, body) : await options.fallback?.(url, body);
        if (value === undefined) throw new Error(`unhandled bridge fixture route ${operation}`);
        await afterReply?.(operation, value);
        const bytes = Buffer.from(JSON.stringify(value));
        response.writeHead(200, { "content-type": "application/json", "content-length": bytes.length }); response.end(bytes);
      } catch (error) {
        if (!response.destroyed) {
          response.writeHead(500, { "content-type": "application/json" });
          response.end(JSON.stringify({ error: { code: "bridge_error", detail: String(error) } }));
        }
      }
    });
    server.listen(0, "127.0.0.1"); await once(server, "listening");
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("bridge HTTP unavailable");
    return { endpoint: `http://127.0.0.1:${address.port}`, requests, call,
      diagnostics: () => ({ backend_pid: backend.pid ?? null, events: [...events], pending_operations: pending.map(value => value.operation) }),
      stats: () => call("stats"),
      afterReply: fn => { afterReply = fn; }, beforeRequest: fn => { beforeRequest = fn; },
      close: async () => {
        afterReply = beforeRequest = undefined; server.closeAllConnections();
        await new Promise(resolve => server.close(() => resolve()));
        return stopBackend(false);
      } };
  } catch (error) {
    server?.closeAllConnections(); server?.close();
    await stopBackend(true); throw error;
  }
}
