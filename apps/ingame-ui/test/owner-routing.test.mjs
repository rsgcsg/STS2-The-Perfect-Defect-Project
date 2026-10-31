import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
const root = path.resolve(import.meta.dirname, "../../..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const desk = read("apps/ingame-ui/PlatformGameDesk.cs");
const panel = read("apps/ingame-ui/PlatformNativeWorkbenchPanel.cs");
const client = read("apps/ingame-ui/PlatformNativeWorkbenchClient.cs");

test("all ordinary controls map to one paired application transport", () => {
  for (const action of ["record_start", "record_stop", "model_auto", "model_step", "model_stop", "rank", "mark", "mistake", "choices_open"]) {
    assert.ok(desk.includes(`"desk.${action}"`));
    assert.ok(client.includes(`"desk.${action}"`));
  }
  assert.match(panel, /_commands\.RunAsync\(_client, connection, action, payload/u);
  assert.doesNotMatch(panel, /SetModeAsync|TickAsync|ExecuteForSession|Process\.Start/u);
  assert.match(client, /X-STS2-Game-Instance-ID/u);
  assert.match(client, /X-SpireAgent-Configuration-ID/u);
});

test("only the open expanded action list asks the backend for complete choices", () => {
  assert.match(desk, /_desk\.Refresh\(ChoicesVisible\)/u);
  assert.match(desk, /PlatformDeskSelection.ShouldReadChoices/u);
  assert.match(desk, /AddChoiceView\(_mainChoices, _mainChoices\)/u);
  assert.match(desk, /AddChoiceView\(_actions.Body, _actions.Footer\)/u);
  assert.match(panel, /if \(choicesVisible && ChoicesReadEnabled\) _choicesRead \?\?= Read\(connection, "choices"\)/u);
  assert.match(panel, /_deskRead \?\?= Read\(connection, "desk"\)/u);
  assert.doesNotMatch(desk, /\.TickAsync\(/u);
});

test("direct recovery retains an exact remembered Runtime and does not create normal execution", () => {
  const recovery = desk.slice(desk.indexOf("private void DirectRecovery"), desk.indexOf("public void Dispose"));
  assert.match(recovery, /_rememberedRun is not string run/u);
  assert.match(recovery, /_recoveryCommands\.RunAsync\(run, command/u);
  assert.match(recovery, /SetModeAsync\(mode, run\)/u);
  assert.match(recovery, /StopAsync\(run\)/u);
  assert.doesNotMatch(recovery, /ObserveBindingAsync|PrepareForModel|\.TickAsync\(/u);
});

test("retired product GUI entries are absent while service clients stay supported", () => {
  assert.doesNotMatch(`${desk}\n${panel}`, /F8|F9|兼容浏览器|显示模型选择|隐藏模型选择|进入商店时停|Boss 前停|在工作台只评分/u);
  assert.doesNotMatch(desk, /"资料管理"|"训练"|"账号"|"设置"/u);
  // Removing navigation does not remove the fixed backend/API capabilities.
  for (const action of ["training.start", "evaluation.start", "models.shadow", "identity.login", "recording.change_source"])
    assert.ok(client.includes(`"${action}"`));
});
