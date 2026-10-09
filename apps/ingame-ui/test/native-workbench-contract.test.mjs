import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
const root = path.resolve(import.meta.dirname, "../../..");
const read = file => fs.readFileSync(path.join(root,file),"utf8");
const panel = read("apps/ingame-ui/PlatformNativeWorkbenchPanel.cs");
const client = read("apps/ingame-ui/PlatformNativeWorkbenchClient.cs");
const bridge = read("apps/game-mod/PlatformTaskBridge.cs");
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

test("all five native pages, typed configuration and exact cumulative resume are discoverable", () => {
  for(const page of ["play","data","training","models","settings"]) assert.ok(panel.includes(`"${page}"`));
  assert.match(panel,/GetProperty\("config_fields"\)/);
  assert.match(panel,/GetProperty\("limits"\)/);
  assert.match(panel,/\["expected_attempt_id"\]/);
  assert.match(panel,/body\["limits"\] = operation.GetProperty\("limits"\).Clone\(\)/);
  assert.match(panel,/Worker 仍未确认停止/);
  assert.match(panel,/source_aware_prepare.*remote_execution/s);
});

test("auth renewal refreshes captured controls while retaining drafts and unknown fences", () => {
  assert.match(panel,/formsKey.*connection.Binding.PairId/s);
  assert.match(panel,/ownerContexts/);
  assert.match(panel,/_drafts.GetValueOrDefault/);
  assert.match(client,/SubmittedModelIntent/);
  assert.match(client,/Unconfirmed/);
  assert.doesNotMatch(client,/_unknown.Clear\(/);
  assert.match(panel,/Refresh\(_visible\)/);
});

test("completed writes refresh unchanged form eligibility without clearing user drafts", () => {
  const frame = panel.slice(panel.indexOf("internal void OnFrame("), panel.indexOf("private string HubUrl()"));
  const completed = frame.slice(frame.indexOf("var write = _writes[index]"));
  assert.ok(completed.indexOf("_formsKey = null;") > completed.indexOf("write.Task.GetAwaiter().GetResult()"));
  assert.ok(completed.indexOf("_formsKey = null;") < completed.indexOf("Refresh(_visible)"));
  assert.doesNotMatch(completed, /_drafts.Clear\(|\.Disabled = false/);
  const render = panel.slice(panel.indexOf("private void Render("), panel.indexOf("private void SetDraft("));
  assert.match(render, /formsKey != _formsKey/);
  assert.match(render, /BuildActionForm\(view, connection, descriptor, action\)/);
  assert.match(render, /next.GrabFocus\(\)/);
  assert.match(panel, /submit.Disabled = !Boolean\(descriptor, "enabled"\) \|\| !_commands.CanSubmit/);
});

test("public bridge status remains secret-free and scoped pair routes are explicit", () => {
  const status = bridge.slice(bridge.indexOf("private static object WorkbenchStatus()"),bridge.indexOf("private static void RegisterWorkbench("));
  assert.doesNotMatch(status,/Token|Secret|signature|cookie|control_token/);
  assert.match(bridge,/\/v1\/workbench\/native-register/);
  assert.match(bridge,/\/v1\/workbench\/native-status/);
  assert.match(bridge,/PlatformNativeWorkbenchBootstrap.Read/);
  assert.match(client,/action is "models.human" or "models.stop"/);
  assert.match(client,/native_request_id/);
  assert.match(client,/ExpiresAt \+ 600/);
});


test("recording forms keep explicit declaration, fixed owner API and original session context", () => {
  for (const id of ["recording.start", "recording.pause", "recording.resume", "recording.change_source", "recording.close"])
    { assert.ok(client.includes(`"${id}"`)); assert.ok(api.includes(`"${id}"`)); }
  assert.match(panel, /body\["runtime_instance_id"\]/);
  assert.match(panel, /body\["recording_session_id"\]/);
  assert.match(panel, /body\["source_segment_id"\]/);
  assert.match(panel, /PlatformNativeWorkbenchCommands.RecordingContext/);
  assert.match(client, /PayloadContext\(action, payload\)/);
  assert.match(api, /return app.control_native_recording\(body\)/);
  assert.match(bridge, /\/v2\/tasks\/prepare-model/);
});
