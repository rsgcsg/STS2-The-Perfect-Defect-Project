#!/usr/bin/env node
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const workspace = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => readFileSync(path.join(workspace, relative), "utf8");
const contract = JSON.parse(read("contracts/player-environment-contract.json"));
const textContext = JSON.parse(read("contracts/text-menu-observation-context.json"));
const csharp = read("host/PlayerEnvironment/Protocol/PlayerEnvironmentContracts.cs");
const service = read("host/PlayerEnvironment/Core/PlayerEnvironmentService.cs");
const projection = read("host/PlayerEnvironment/Projection/BoundActionProjection.cs");
const submission = read("host/PlayerEnvironment/Execution/ActionSubmission.cs");
const reads = read("host/PlayerEnvironment/Reads/ReadService.cs")
  + read("host/LiveHost/PlayerVisibleReadBuilder.cs");
const typescript = read("sdk/typescript/src/protocol.ts");
const rewardPage = read("sdk/typescript/src/rewardPage.ts");
const textMenu = read("sdk/typescript/src/textMenu.ts");
const textContextHost = read("host/PlayerEnvironment/Protocol/TextMenuContracts.cs")
  + read("host/PlayerEnvironment/TextMenu/TextMenuService.cs")
  + read("host/PlayerEnvironment/TextMenu/TextMenuExecutor.cs");
const client = read("sdk/typescript/src/client.ts");
const transport = read("host/ConnectorMod.cs")
  + read("host/PlayerEnvironment/Transport/ConnectorMod.PlayerEnvironment.cs");
const python = read("transports/mcp/server.py");
const failures = [];

function requireIn(source, value, owner) {
  if (!source.includes(value)) failures.push(`${owner}: missing ${value}`);
}

requireIn(csharp, `ProtocolVersion = "${contract.protocol_version}"`, "C# protocol");
requireIn(typescript, `SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL = "${contract.protocol_version}"`, "TypeScript client protocol");
requireIn(python, `_CONTROL_PROTOCOL = "${contract.protocol_version}"`, "Python transport");

for (const schema of Object.values(contract.schemas)) {
  requireIn(csharp + projection, schema, "C# schemas");
  if (schema !== contract.schemas.native_page_evidence) requireIn(typescript, schema, "TypeScript client schemas");
}

const verbBody = typescript.match(/const verbSchema = z\.enum\(\[([\s\S]*?)\]\);/u)?.[1] ?? "";
const actualVerbs = [...verbBody.matchAll(/"([a-z_]+)"/gu)].map((match) => match[1]).sort();
const expectedVerbs = [...contract.action_verbs].sort();
if (JSON.stringify(actualVerbs) !== JSON.stringify(expectedVerbs)) {
  failures.push(`TypeScript client verbs differ: expected ${expectedVerbs.join(", ")}; actual ${actualVerbs.join(", ")}`);
}
for (const verb of expectedVerbs) requireIn(service, `"${verb}"`, "C capability verbs");

for (const [name, route] of Object.entries(contract.routes)) {
  const prefix = route.replace("/{read_id}", "/").replace("/{request_id}", "/");
  const transportPrefix = name.startsWith("controller_") && name !== "controller_snapshot"
    ? "/api/player-environment/controller/"
    : prefix;
  requireIn(transport, transportPrefix, `C route ${name}`);
  if (["capabilities", "snapshot", "read", "submit_action", "action_receipt", "register_client", "controller_acquire", "controller_renew", "controller_release"].includes(name)) {
    const clientPrefix = prefix.endsWith("/") ? prefix : route;
    requireIn(client, clientPrefix, `TypeScript client route ${name}`);
  }
}

for (const field of contract.snapshot_contract.required_fields) {
  requireIn(typescript, `${field}:`, `TypeScript client snapshot field ${field}`);
}
requireIn(csharp, "PlayerEnvironmentInformationPolicy", "C information policy");
requireIn(typescript, "information_policy:", "TypeScript client information policy");
requireIn(typescript, "surface: z.object({ kind:", "tagged surface content");
requireIn(typescript, "context: z.object({ kind:", "tagged context content");

for (const kind of Object.keys(contract.read_kinds)) requireIn(reads, `"${kind}"`, `read kind ${kind}`);
for (const state of contract.delivery_contract.states) requireIn(submission, `"${state}"`, `delivery state ${state}`);
requireIn(typescript, 'value.delivery === "unknown" && value.retry.allowed', "unknown-no-retry schema");

for (const profile of contract.evidence_profiles) {
  requireIn(csharp, `NativePageEvidenceProfile = "${profile.id}"`, "native-page profile");
  requireIn(typescript, "creates_mutation_authority: z.literal(false)", "non-authorizing evidence profile");
}

for (const profile of contract.input_profiles ?? []) {
  requireIn(csharp, `OrdinaryRewardPageProfile = "${profile.id}"`, "C# input profile");
  requireIn(csharp, profile.snapshot_schema, "C# profiled Snapshot schema");
  requireIn(rewardPage, profile.id, "TypeScript input profile");
  requireIn(rewardPage, profile.snapshot_schema, "TypeScript profiled Snapshot schema");
  requireIn(client, "observeRewardPage", "opt-in SDK observation");
  requireIn(client, "submitRewardPage", "opt-in SDK submission");
  requireIn(client, "pollRewardPage", "opt-in SDK receipt poll");
}

requireIn(python, '_environment_get("snapshot")', "Python snapshot route");

requireIn(transport, textContext.route, "text menu context route");
requireIn(transport, "RunOnMainThread(PlayerEnvironmentService.ObserveTextMenuContext)",
  "text menu context game-thread capture");
requireIn(transport, textContext.run_change_error, "text menu context atomic rejection");
requireIn(client, textContext.route, "text menu context SDK route");
requireIn(textMenu, textContext.response_schema, "text menu context SDK schema");
requireIn(textMenu, "decodeTextMenuSnapshot(raw.snapshot)", "text menu context inner snapshot");
requireIn(textContextHost, textContext.response_schema, "text menu context Host schema");
requireIn(textContextHost, "ReferenceEquals(before, currentRun())", "whole-capture run reference check");
requireIn(textContextHost, "Entities.GetId(run, \"run\")", "process-local run identity");

if (failures.length > 0) {
  console.error(["Player Environment contract checks failed:", ...failures.map((item) => `- ${item}`)].join("\n"));
  process.exitCode = 1;
} else {
  console.log("player-environment contract checks passed");
}
