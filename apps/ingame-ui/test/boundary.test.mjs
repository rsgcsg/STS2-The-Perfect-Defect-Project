import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const mod = read("PlatformLiveUiMod.cs");
const desk = read("PlatformGameDesk.cs");
const panel = read("PlatformNativeWorkbenchPanel.cs");
const client = read("PlatformLiveStatusClient.cs");
const contracts = read("PlatformLiveContracts.cs");
const feed = read("PlatformLiveActionFeed.cs");
const workbenchOpen = read("PlatformWorkbenchOpen.cs");
const taskBridge = read("../game-mod/PlatformTaskBridge.cs");

test("desktop furniture cannot submit business commands or consume keyboard shortcuts", () => {
  const furniture = desk.slice(desk.indexOf("internal sealed class PlatformDeskWindow"));
  assert.doesNotMatch(furniture, /PlatformNativeWorkbench|RecordingApplication|SetModeAsync|StopAsync|ExecuteForSession|\.Submit\(/u);
  assert.doesNotMatch(`${mod}\n${desk}`, /InputEventKey|Shortcut|Key\.F[89]|Key\.Escape/u);
  assert.doesNotMatch(`${mod}\n${desk}\n${panel}`, /player-environment\/actions|bound_action_id|RecorderRuntime|HumanActionScope|AppendDecision/u);
  assert.doesNotMatch(`${mod}\n${desk}`, /override void _(Ready|Process|Input)/u);
  assert.match(desk, /MouseFilter = MouseFilterEnum\.Ignore/u);
});

test("the native game desk reuses trusted installed backend discovery without opening a browser", () => {
  assert.match(desk, /PlatformWorkbenchOpenClient\.OpenAsync/u);
  assert.doesNotMatch(`${mod}\n${desk}\n${panel}`, /OS\.ShellOpen|Process\.Start/u);
  assert.match(workbenchOpen, /GetAsync\(\s*GameStatusUrl/u);
  assert.match(workbenchOpen, /new Uri\(new Uri\(url, UriKind\.Absolute\), "health"\)/u);
  assert.match(workbenchOpen, /observedInstanceId == instanceId/u);
  assert.match(workbenchOpen, /AllowAutoRedirect = false, UseProxy = false/u);
  assert.match(workbenchOpen, /initial\.State != PlatformWorkbenchOpenState\.NotRegistered/u);
  assert.match(workbenchOpen, /UseShellExecute = false/u);
});

test("Workbench registration remains exact-loopback and game-instance bound", () => {
  assert.match(taskBridge, /request\.UserHostName != Authority \|\| request\.Headers\["Origin"\] != null/u);
  assert.match(taskBridge, /!IPAddress\.IsLoopback\(request\.RemoteEndPoint\.Address\)/u);
  for (const route of ["status", "register", "unregister"])
    assert.ok(taskBridge.includes(`/v1/workbench/${route}`));
  assert.match(taskBridge, /_workbenchRegistration\.WorkbenchInstanceId != instanceId/u);
  assert.match(taskBridge, /request\.ContentLength64 is <= 0 or > MaximumWorkbenchRegistrationBytes/u);
  assert.doesNotMatch(taskBridge, /OS\.ShellOpen|Process\.Start|WebBrowser/u);
});

test("recording totals and failure definitions remain owned by canonical services", () => {
  assert.match(feed, /record:\{value\.RecordId\}/u);
  assert.match(feed, /RecordId action root unavailable/u);
  assert.match(feed, /RootPending => "… Observed"/u);
  assert.match(feed, /DecisionRecorded => "✓ Recorded"/u);
  assert.match(feed, /DecisionInvalidated => "✕ Invalidated"/u);
  assert.match(feed, /value\.Action\?\.Disposition is "unresolved" or "failed_closed"/u);
  assert.match(feed, /RetainedLimit = 512/u);
  assert.doesNotMatch(feed, /InputEventMouseButton|InputEventMouseMotion|AppendDecision|Execute\(/isu);
});

test("Connector status is accepted only after runtime and environment coherence", () => {
  assert.match(contracts, /EnsureConnectorCoherence\(/u);
  assert.match(contracts, /capabilities\.Host\.RuntimeInstanceId/u);
  assert.match(contracts, /snapshot\.Session\.RuntimeInstanceId/u);
  assert.match(contracts, /controller\.RuntimeInstanceId/u);
  assert.match(client, /PlatformLiveStatusProjection\.EnsureConnectorCoherence/u);
  assert.match(client, /capabilities = null;/u);
  assert.match(client, /snapshot = null;/u);
  assert.match(client, /controller = null;/u);
});

test("UI remains part of the one Game Mod package and does not own install authority", () => {
  for (const file of ["build.mjs", "deploy.mjs", "mod_manifest.json", "STS2PlatformLiveUi.csproj"])
    assert.equal(fs.existsSync(path.join(root, file)), false);
  assert.match(mod, /PlatformLiveUiMount/u);
  assert.match(mod, /Lazy<PlatformArtifactIdentity> ArtifactIdentity/u);
});

test("portable owner, transport, recording, mount and ranking counterexamples pass", () => {
  const result = spawnSync("dotnet", ["test", path.join(root, "test", "STS2PlatformLiveUi.Tests.csproj"),
    "--configuration", "Release", "--nologo"], { cwd: root, encoding: "utf8", shell: false });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});
