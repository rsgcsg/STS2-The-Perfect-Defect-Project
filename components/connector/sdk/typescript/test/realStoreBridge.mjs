import { spawn, execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { nativeScenario } from "./nativeLogicalFixtures.js";
import { EnvironmentControllerSession, NativeLogicalSession, PlayerEnvironmentRestClient } from "../src/index.js";

let preparedDll;
function prepareBridge() {
  if (process.env.STS2_OWNED_CURRENT_BRIDGE_DLL) return process.env.STS2_OWNED_CURRENT_BRIDGE_DLL;
  if (preparedDll) return preparedDll;
  const project = fileURLToPath(new URL("../../../host/tests/STS2Connector.NativeLogical.OwnedCurrent.Bridge/STS2Connector.NativeLogical.OwnedCurrent.Bridge.csproj", import.meta.url));
  // One framework-only portable build per test module. Required regressions
  // fail clearly when the existing .NET9 source-test prerequisite is missing.
  execFileSync(process.env.STS2_OWNED_CURRENT_DOTNET ?? "dotnet", ["build", project,
    "--nologo", "--verbosity", "minimal", "--maxcpucount:1", "-p:BuildInParallel=false", "-p:UseSharedCompilation=false", "--ignore-failed-sources"], {
    encoding: "utf8", timeout: 60000,
    env: { ...process.env, DOTNET_PROCESSOR_COUNT: "2", DOTNET_GENERATE_ASPNET_CERTIFICATE: "false", DOTNET_SKIP_FIRST_TIME_EXPERIENCE: "1", DOTNET_CLI_TELEMETRY_OPTOUT: "1" }
  });
  preparedDll = join(dirname(project), "bin/Debug/net9.0/STS2Connector.NativeLogical.OwnedCurrent.Bridge.dll");
  return preparedDll;
}
/** Test-only HTTP envelope around a separately built game-free executable. Its
 * Current/Read/C/catalog/retain/release bytes and accounting are production C#
 * Store/Projector output. The control/capability/Await responses are synthetic. */
export async function realStoreBridge(options = {}) {
  const dll = prepareBridge();
  const backend = spawn(process.env.STS2_OWNED_CURRENT_DOTNET ?? "dotnet", [dll], {
    env: { ...process.env, DOTNET_PROCESSOR_COUNT: "2", DOTNET_GENERATE_ASPNET_CERTIFICATE: "false", DOTNET_SKIP_FIRST_TIME_EXPERIENCE: "1", DOTNET_CLI_TELEMETRY_OPTOUT: "1" }, stdio: ["pipe", "pipe", "pipe"] });
  const pending = [], stderr = [];
  backend.stderr.on("data", value => stderr.push(Buffer.from(value)));
  createInterface({ input: backend.stdout }).on("line", line => {
    const caller = pending.shift(); if (!caller) return;
    try { const reply = JSON.parse(line); if (reply.error) caller.reject(new Error(reply.error.code + ":" + reply.error.detail)); else caller.resolve(reply.value); }
    catch (error) { caller.reject(error); }
  });
  backend.on("exit", code => { for (const caller of pending.splice(0)) caller.reject(new Error(`bridge exited ${code}: ${Buffer.concat(stderr).toString()}`)); });
  const call = (operation, body = {}) => new Promise((resolve, reject) => {
    pending.push({ resolve, reject }); backend.stdin.write(JSON.stringify({ operation, body }) + "\n", error => { if (error) reject(error); });
  });
  const source = nativeScenario(1), requests = [];
  let afterReply;
  const server = createServer(async (request, response) => {
    try {
      const chunks = []; for await (const chunk of request) chunks.push(Buffer.from(chunk));
      const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString())  : {};
      const url = new URL(request.url, "http://127.0.0.1"), operation = url.pathname.split("/").at(-1); requests.push({ operation, body });
      let value;
      if (["current", "current_owned", "read", "catalog", "retain", "release"].includes(operation) && !url.pathname.includes("/controller/")) value = await call(operation, body);
      else {
        value = options.fallback ? options.fallback(url, body) : source.route(url, body);
        if (operation === "capabilities") {
          const cap = value;
          cap.supported_methods.push("current_owned"); cap.implemented_mechanisms.push("native_current_reader_owned_v1"); cap.limits.max_captures = 4;
        }
      }
      await afterReply?.(operation, value);
      const bytes = Buffer.from(JSON.stringify(value)); response.writeHead(200, { "content-type": "application/json", "content-length": bytes.length }); response.end(bytes);
    } catch (error) { response.writeHead(500, { "content-type": "application/json" }); response.end(JSON.stringify({ error: { code: "bridge_error", detail: String(error) } })); }
  });
  server.listen(0, "127.0.0.1"); await once(server, "listening"); const address = server.address();
  if (!address || typeof address === "string") throw new Error("bridge HTTP unavailable");
  const rest = new PlayerEnvironmentRestClient(`http://127.0.0.1:${address.port}`, 3000);
  const controller = new EnvironmentControllerSession(rest, { productId: "test", productName: "Test", productVersion: "1", clientInstanceId: "sdk-fixture" });
  const session = new NativeLogicalSession(rest, controller);
  const capabilities = (await session.capabilities()).data; await controller.register(capabilities.session, capabilities.control_policy);
  return { rest, session, controller, requests, call, capabilities,
    stats: () => call("stats"),
    afterReply: (fn) => { afterReply = fn; },
    close: async () => { afterReply = undefined; await controller.close(); server.closeAllConnections(); await new Promise(resolve => server.close(() => resolve())); backend.stdin.end(); if (backend.exitCode === null) await once(backend, "exit"); } };
}
