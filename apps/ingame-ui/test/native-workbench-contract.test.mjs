import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
const root = path.resolve(import.meta.dirname, "../../..");
const read = file => fs.readFileSync(path.join(root,file),"utf8");
const panel = read("apps/ingame-ui/PlatformNativeWorkbenchPanel.cs");
const client = read("apps/ingame-ui/PlatformNativeWorkbenchClient.cs");
const bridge = read("apps/game-mod/PlatformTaskBridge.cs");
const pairing = read("apps/ingame-ui/PlatformNativeWorkbenchPair.cs");
const api = read("python/spireagent/workbench/native_workbench_api.py");

test("native and application fixed command catalogs agree without an arbitrary proxy", () => {
  const expected = JSON.parse(read("docs/design/fixtures/native_workbench_pair_v1.json"));
  assert.equal(expected.fixture_only,true);
  const actionIds = ["workspace.create","curation.prepare","recordings.refresh","recordings.import", "datasets.preview","datasets.human-preview","datasets.publish","training.start","training.pause","training.cancel","training.reconcile","training.resume","evaluation.start","models.export","models.register","models.download","models.load","models.takeover","models.human","models.stop","identity.login","identity.poll","identity.logout","collection.consent","collection.prepare","collection.upload","downloads.start"];
  for (const id of actionIds) {assert.ok(client.includes(`"${id}"`));assert.ok(api.includes(`"${id}"`));}
  assert.ok(client.includes('"datasets.source3-preview"'));
  assert.ok(api.includes('"datasets.source3-preview"'));
  assert.match(client,/AllowAutoRedirect = false, UseProxy = false, UseCookies = false/);
  assert.doesNotMatch(api,/eval\(|exec\(|shell=True|import_module\(/);
});

test("retired native forms leave their supported application pages and CLI capabilities intact", () => {
  for (const page of ["play", "data", "training", "models", "settings"])
    { assert.ok(client.includes(`"${page}"`)); assert.ok(api.includes(`"${page}"`)); }
  assert.doesNotMatch(panel, /config_fields|expected_attempt_id|BuildActionForm|native_action_not_supported/);
});

test("pair renewal and status reads cannot erase an uncertain command fence", () => {
  assert.match(client, /SubmittedModelIntent/);
  assert.match(client, /Unconfirmed/);
  assert.doesNotMatch(client, /_unknown.Clear\(/);
  assert.match(client, /_commands|class PlatformNativeWorkbenchCommands/);
  assert.match(panel, /PlatformNativeWorkbenchConnection.IsCurrent/);
  assert.match(panel, /PlatformNativeWorkbenchPair.Read/);
  assert.match(panel, /_commands.CanSubmit/);
  assert.doesNotMatch(panel, /\.Disabled = false/);
});

test("public bridge status remains secret-free and scoped pair routes are explicit", () => {
  const status = bridge.slice(bridge.indexOf("private static object WorkbenchStatus()"),bridge.indexOf("private static void RegisterWorkbench("));
  assert.doesNotMatch(status,/Token|Secret|signature|cookie|control_token/);
  assert.match(bridge,/\/v1\/workbench\/native-register/);
  assert.match(bridge,/\/v1\/workbench\/native-status/);
  assert.match(pairing,/PlatformNativeWorkbenchBootstrap.Read/);
  assert.match(pairing,/private static Authority\? _production/);
  assert.match(pairing,/Interlocked.CompareExchange\(ref _production/);
  const startup = bridge.slice(bridge.indexOf("internal static void Start()"), bridge.indexOf("private static object Status("));
  assert.ok(startup.indexOf("PlatformNativeWorkbenchConnection.Initialize") < startup.indexOf("listener.Start()"));
  assert.match(startup,/GetPlayerEnvironmentControlSnapshot\(\).RuntimeInstanceId/);
  const nativeRoutes = bridge.slice(bridge.indexOf("private static bool NativeTransport("), bridge.indexOf("private static void Reply("));
  assert.doesNotMatch(nativeRoutes,/GetPlayerEnvironmentControlSnapshot|PlatformNativeWorkbenchBootstrap.Read/);
  assert.match(nativeRoutes,/authority.Register\(document.RootElement\)/);
  assert.match(nativeRoutes,/authority.CurrentProof\(context.Request.Headers\)/);
  assert.match(pairing,/lock \(_gate\)[\s\S]*bootstrapReader\(\)[\s\S]*ReadSigned[\s\S]*Scope\(pair, now\)[\s\S]*Select\(bootstrap, now\)/);
  assert.match(client,/action is "models.human" or "models.stop"/);
  assert.match(client,/native_request_id/);
  assert.match(client,/ExpiresAt \+ 600/);
});


test("retired recording forms preserve the exact source and session application API", () => {
  for (const id of ["recording.start", "recording.pause", "recording.resume", "recording.change_source", "recording.close"])
    { assert.ok(client.includes(`"${id}"`)); assert.ok(api.includes(`"${id}"`)); }
  assert.match(client, /PayloadContext\(action, payload\)/);
  assert.match(client, /RecordingContext\(body.GetProperty\("runtime_instance_id"\)/);
  assert.match(api, /return app.control_native_recording\(body\)/);
  assert.match(bridge, /\/v2\/tasks\/prepare-model/);
});
