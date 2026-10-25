import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
const root=path.resolve(import.meta.dirname,"../../..");
const read=file=>fs.readFileSync(path.join(root,file),"utf8");
const mod=read("apps/ingame-ui/PlatformLiveUiMod.cs"),panel=read("apps/ingame-ui/PlatformNativeWorkbenchPanel.cs");
const client=read("apps/ingame-ui/PlatformNativeWorkbenchClient.cs"),api=read("python/spireagent/workbench/native_workbench_api.py");
test("ordinary runtime product controls navigate to the existing Workbench owner before direct dispatch",()=>{
  const flow=mod.slice(mod.indexOf("private async Task RunPolicyCommandAsync"),mod.indexOf("private async Task PollAsync"));
  assert.match(flow,/if \(!recovery\) \{ OpenNativeWorkbenchPage\("play"\); return; \}/);
  assert.ok(flow.indexOf("if (!recovery)")<flow.indexOf("_policyCommands.RunAsync"));
  assert.doesNotMatch(flow,/ObserveBindingAsync|PrepareForModel|TickAsync/);
  assert.match(flow,/SetModeAsync\(mode, expected\)/);assert.match(flow,/StopAsync\(expected\)/);
  for(const text of ["在工作台开始测试","在工作台只评分","在工作台单步执行","在工作台推进 Tick"])
    assert.ok(mod.includes(text));
});
test("active Source3/Source4 controls cannot bypass the paired application through a profile dropdown",()=>{
  const flow=mod.slice(mod.indexOf("private void ApplyRecordingCommand"),mod.indexOf("private STS2HumanAnnotator.Core.SourceDeclaration ReadSourceDeclaration"));
  assert.match(flow,/activeSource = before.Session\?\.CaptureProfileId is .*SourceSessionContractV3.ProfileId/);
  assert.match(flow,/if \(activeSource &&/);
  assert.ok(flow.indexOf('OpenNativeWorkbenchPage("data")')<flow.indexOf("PlatformRecordingCommands.Execute"));
  assert.match(flow,/owner.ExecuteForSession/); // legacy native Human capability retained
  assert.match(panel,/internal void OpenPage\(string page\)/);
});
test("advanced fixed actions carry original run game and epoch through the same application command",()=>{
  for(const mode of ["auto","shadow","one_step","tick"]){assert.ok(client.includes(`"models.${mode}"`));assert.ok(api.includes(`"models.${mode}"`));}
  for(const field of ["runtime_run_id","runtime_instance_id","recovery_epoch"])
    assert.ok(panel.includes(`body["${field}"]`));
  assert.match(api,/app\.models\.command\(\s*action_id\.split/);
  assert.match(api,/expected_context=body/);
  assert.doesNotMatch(panel,/Process.Start|SetModeAsync|TickAsync/);
});
test("direct Human Stop recovery retains exact remembered run while normal pairing is unavailable",()=>{
  assert.match(mod,/bool recoveryAvailable = _displayedPolicyRunId is not null && !_disposed/);
  assert.match(mod,/_compactHumanButton.Disabled = !recoveryAvailable/);
  assert.match(mod,/_endTestButton.Disabled = !recoveryAvailable/);
  assert.match(panel,/直接归还 Human/);assert.match(panel,/直接停止当前 Runtime/);
});

test("P1 recording buttons map to the existing application without requiring a Workbench connection",()=>{
  assert.match(mod,/BuildHeaderButton\("录制", \(\) => SelectSurface\(1\)/);
  assert.match(mod,/BuildHeaderButton\("开始本人录制", StartDailyRecording/);
  assert.match(mod,/"兼容录制诊断"/);
  assert.match(mod,/_recordingProfile.Selected = 1/);
  const flow=mod.slice(mod.indexOf("private void ApplyRecordingCommand"),mod.indexOf("private STS2HumanAnnotator.Core.SourceDeclaration ReadSourceDeclaration"));
  assert.match(flow,/PlatformRecordingCommands.ForP1Status/);
  assert.match(flow,/owner.ExecuteForSession/);
  assert.match(panel,/PlatformNativeRecordingPresentation.Rows\(recording\)/);
  assert.match(panel,/body.GetValueOrDefault\("capture_profile_id"\)[\s\S]*P1NativeRecordingContract.ProfileId\)[\s\S]*body\["actor_id"\] = null/);
});

test("recording status and eligible owner controls precede the artifact inventory",()=>{
  assert.ok(panel.indexOf('_body.AddChild(_recordingStatus)')<panel.indexOf('_body.AddChild(_items)'));
  assert.ok(panel.indexOf('_body.AddChild(_recordingForms)')<panel.indexOf('_body.AddChild(_items)'));
  assert.match(panel,/recordingAction && !Boolean\(descriptor, "enabled"\)/);
  assert.match(panel,/VBoxContainer destination = recordingAction \? _recordingForms : _forms/);
});
