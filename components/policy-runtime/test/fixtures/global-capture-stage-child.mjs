import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
const { AgentRunEvidence } = await import(pathToFileURL(process.argv[2]));
const manifest = JSON.parse(await readFile(process.argv[3], "utf8"));
const evidence = await AgentRunEvidence.createSession({ root: process.argv[4], runId: "hard-killed-capture",
  agentManifest: manifest, runtimeVersion: "synthetic", runtimeCodeSha256: "a".repeat(64), mode: "auto" });
const bytes = await readFile(process.argv[5]);
await evidence.append("global_capture_requested", { request_id: "capture-request-11111111-1111-1111-1111-111111111111" });
await evidence.stageGlobalCaptureResponse("capture-request-11111111-1111-1111-1111-111111111111", 1, bytes);
process.stdout.write("staged\n");
setInterval(() => {}, 1000);
