#!/usr/bin/env node
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const workspace = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const failures = [];
const absolute = (relative) => path.join(workspace, relative);
const read = (relative) => readFileSync(absolute(relative), "utf8");
const requireText = (relative, value, label) => {
  if (!read(relative).includes(value)) failures.push(`${relative}: missing ${label}`);
};
const forbidText = (relative, value, label) => {
  if (read(relative).includes(value)) failures.push(`${relative}: contains ${label}`);
};
const sourceFiles = (relative) => {
  const root = absolute(relative);
  if (!existsSync(root)) return [];
  return readdirSync(root, { recursive: true })
    .map((entry) => path.join(root, String(entry)))
    .filter((entry) => statSync(entry).isFile() && /\.(cs|ts)$/u.test(entry));
};

const client = "sdk/typescript/src/client.ts";
for (const route of [
  "/api/player-environment/capabilities",
  "/api/player-environment/snapshot",
  "/api/player-environment/reads/",
  "/api/player-environment/actions",
  "/api/player-environment/clients/register",
  "/api/player-environment/controller/"
]) requireText(client, route, `current route ${route}`);
for (const legacy of ["/api/he", "/api/v2", "/api/v3", "legal_actions"])
  forbidText(client, legacy, `legacy client seam ${legacy}`);

const protocol = "sdk/typescript/src/protocol.ts";
requireText(protocol, 'SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL = "1.0.0"', "current protocol");
requireText(protocol, "information_policy:", "fair-player information boundary");
requireText(protocol, "bound_actions:", "finite Host projection");
for (const legacy of [
  "observation_policy", "optional_annotations", "state_token", "legal_actions",
  "expected_frame_id", "expected_owner_id", "parameter_domains", "SourceContract",
  "native_ui_actionability"
]) forbidText(protocol, legacy, `retired public field ${legacy}`);

for (const retired of [
  "host/HumanEnvironment", "host/ConnectorV3", "host/BridgeV2",
  "host/Authority/GatewayAuthorityRuntime.cs",
  "host/Authority/EnvironmentPermissionManager.cs",
  "host/Authority/EnvironmentQualificationStore.cs",
  "host/NativeUi/NativeOperationManifest.cs",
  "host/LegacyV1RoutePolicy.cs", "host/McpMod.SettingsUI.cs",
  "host/docs/bridge-v2", "host/docs/connector-v3", "Re-SpireAgent"
]) {
  if (existsSync(absolute(retired))) failures.push(`${retired}: retired or foreign ownership remains`);
}

const trackedFiles = execFileSync("git", ["ls-files", "-z"], {
  cwd: workspace,
  encoding: "utf8"
}).split("\0").filter(Boolean);
for (const forbiddenArtifact of ["sts2.dll", "GodotSharp.dll", "STS2_MCP.dll"]) {
  if (trackedFiles.some((file) => path.basename(file).toLowerCase()
      === forbiddenArtifact.toLowerCase())) {
    failures.push(`repository tracks forbidden runtime artifact ${forbiddenArtifact}`);
  }
}

const cProtocol = "host/PlayerEnvironment/Protocol/PlayerEnvironmentContracts.cs";
for (const required of [
  "PlayerEnvironmentSnapshot", "PlayerEnvironmentInteraction", "PlayerEnvironmentReferent",
  "PlayerEnvironmentReadOpportunity", "PlayerEnvironmentBoundAction",
  "PlayerEnvironmentActionReceipt", "PlayerEnvironmentInformationPolicy"
]) requireText(cProtocol, required, `public contract ${required}`);
for (const legacy of ["BridgeV2", "ConnectorV3", "SourceContract", "BusinessOutcome", "ObservationPolicy"])
  forbidText(cProtocol, legacy, `legacy public ontology ${legacy}`);
requireText(cProtocol, "namespace STS2Connector.PlayerEnvironment.Protocol", "current Connector namespace");

const hostSources = [
  ...sourceFiles("host/PlayerEnvironment"),
  ...sourceFiles("host/NativeUi"),
  ...sourceFiles("host/LiveHost"),
  absolute("host/Authority/EnvironmentIdentityRuntime.cs"),
  absolute("host/Authority/MutationControlRuntime.cs")
];
const currentHost = hostSources.map((file) => readFileSync(file, "utf8")).join("\n");
for (const legacy of [
  "GatewayAuthorityRuntime", "EnvironmentPermission", "QualificationStore",
  "NativeOperationManifest", "provider_native_binding_adapter", "SourceContract",
  "CompletionProbe", "NativeInputResult.Started", "business_contract", "gateway_owned",
  "InspectionAllowed", "AvailableInspections", "normal_inspection"
]) {
  if (currentHost.includes(legacy)) failures.push(`current Host path contains retired authority ${legacy}`);
}

const snapshotBuilder = read("host/PlayerEnvironment/Observation/SnapshotBuilder.cs");
if (!snapshotBuilder.includes("information.ReadCatalog")) {
  failures.push("SnapshotBuilder: Read advertisement does not consume the one information catalog");
}
if (snapshotBuilder.includes("BuildInspectionCatalog")) {
  failures.push("SnapshotBuilder: duplicate legacy Inspection catalog remains");
}

const submission = "host/PlayerEnvironment/Execution/ActionSubmission.cs";
for (const required of ["stale_snapshot", "bound_action_not_current", "preparation.TryBegin()", "input_delivery_unknown"])
  requireText(submission, required, `delivery hard shell ${required}`);
for (const required of ["MutationControlRuntime.ValidateActiveControl", "MutationControlRuntime.TryAdmitRequest", "MutationControlRuntime.TryBegin"])
  requireText("host/PlayerEnvironment/Core/PlayerEnvironmentService.Requests.cs", required, `one shared request Authority ${required}`);
for (const required of ["owner.begin(Authorization(entry.Request))", "entry.State = State.Started", "ReleasePreparation", "nativeTerminal(entry.Request, native)"])
  requireText("host/PlayerEnvironment/Requests/RequestNamespace.cs", required, `original request start and seal ${required}`);
forbidText(submission, "CompletionProbe", "business completion wait");

const transport = read("host/ConnectorMod.cs")
  + read("host/PlayerEnvironment/Transport/ConnectorMod.PlayerEnvironment.cs");
for (const retiredRoute of ["/api/v1", "/api/v2", "/api/v3", "/api/he"]) {
  if (transport.includes(retiredRoute)) failures.push(`transport retains retired route ${retiredRoute}`);
}
if (read("host/STS2Connector.Host.csproj").includes("0Harmony")) {
  failures.push("host/STS2Connector.Host.csproj: Host retains an unnecessary Harmony dependency");
}
for (const relative of ["host/ConnectorMod.cs", "tools/connector.mjs", "docs/INSTALLATION.md"]) {
  requireText(relative, "STS2_MCP.conf", "stable major-1 runtime config identity");
}
forbidText("host/ConnectorMod.cs", "STS2Connector.conf", "renamed runtime config seam");

const processWitness = "host/PlayerEnvironment/Witness/ProcessLocalNativeWitness.cs";
for (const required of [
  "ProcessLocalNativeWitnessFrame", "reference_equality_to_frozen_host_binding",
  "CaptureExactReferences", "SourceDigest"
]) requireText(processWitness, required, `process-local witness boundary ${required}`);
for (const forbidden of [
  "SubmitAction", "RunOnMainThread", "NativeUiActionRuntime.Execute", "HttpListener",
  "JsonSerializer"
]) forbidText(processWitness, forbidden, `authorizing or serialized witness seam ${forbidden}`);
const playerEnvironmentTransport = read("host/PlayerEnvironment/Transport/ConnectorMod.PlayerEnvironment.cs");
for (const forbidden of ["ProcessLocalNativeWitness", "native-witness", "native_witness"])
  if (playerEnvironmentTransport.includes(forbidden)) failures.push(`transport exposes process-local witness: ${forbidden}`);

const python = "transports/mcp/server.py";
requireText(python, "observe_sts2_player_environment", "current MCP observe tool");
requireText(python, '_environment_get("snapshot")', "current MCP snapshot route");
for (const legacy of ["_he_", "get_sts2_human", "read_sts2_human", "Human-Equivalent"])
  forbidText(python, legacy, `legacy Python transport ${legacy}`);

const sdk = sourceFiles("sdk/typescript/src")
  .map((file) => readFileSync(file, "utf8"))
  .join("\n");
for (const strategy of ["DeepSeek", "systemPrompt", "rewardSignal", "buildAllowedActions", "scoreAction"])
  if (sdk.includes(strategy)) failures.push(`client SDK contains consumer strategy ${strategy}`);
for (const foreignOwner of ["Re-SpireAgent", "re-spireagent"])
  if (sdk.includes(foreignOwner)) failures.push(`client SDK contains consumer identity ${foreignOwner}`);

for (const source of hostSources) {
  const body = readFileSync(source, "utf8");
  if (body.includes("namespace STS2_MCP") || body.includes("using STS2_MCP")) {
    failures.push(`${path.relative(workspace, source)}: legacy MCP namespace remains`);
  }
}

if (failures.length > 0) {
  console.error(["Player Environment boundary checks failed:", ...failures.map((item) => `- ${item}`)].join("\n"));
  process.exitCode = 1;
} else {
  console.log("player-environment boundary checks passed");
}
