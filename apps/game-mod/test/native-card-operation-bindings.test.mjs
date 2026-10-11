import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const root = resolve(import.meta.dirname, '../../..');
const source = path => readFileSync(resolve(root, path), 'utf8');
const native = name => source(`components/connector/host/NativeUi/${name}.cs`);

test('card source hooks register exact typed invocations and retain passive boundaries', () => {
  const hooks = source('apps/game-mod/ConnectorNativeCardOperationBindings.cs');
  assert.match(hooks, /BindingFlags\.DeclaredOnly/u);
  assert.match(hooks, /original\.DeclaringType != owner \|\| original\.IsStatic \|\| original\.ReturnType != returns/u);
  assert.match(hooks, /"MultiCreatureTargeting", new\[\] \{ typeof\(TargetMode\) \}, typeof\(Task\)/u);
  for (const seam of ['Start', 'CancelPlayCard', '_ExitTree', 'UpdateCardDisplay', 'SetCard', 'ToggleShowUpgrade', 'Open'])
    assert.ok(hooks.includes(seam), seam);
  assert.doesNotMatch(hooks, /PatchAll|transpiler|\.Invoke\(|Dispatch\(|_Input\(|await |ContinueWith|Task\.Run|Timer|TryPlayCard/u);
  assert.ok(hooks.indexOf('NativeCardInspectionBinding.DisplayReturned(__instance, __state);')
    < hooks.indexOf('NativeLogical.Publish("native_inspect_preview"'));
  assert.match(hooks, /return __exception;/u);
  const composition = source('apps/game-mod/ConnectorNativeLogicalPatches.cs');
  assert.equal(composition.match(/ConnectorNativeCardOperationBindings\.Register\(harmony\)/gu)?.length, 1);
  assert.doesNotMatch(composition, /"UpdateCardDisplay"|InspectPreviewReturned/u);
  assert.match(source('apps/game-mod/STS2Platform.GameMod.csproj'), /Compile Include="ConnectorNativeCardOperationBindings.cs"/u);
  const replacements = hooks.slice(hooks.indexOf('foreach ((string method'), hooks.indexOf('NativeMouseCardConfirmation.Registered();'));
  assert.doesNotMatch(replacements, /nameof\(NInspectCardScreen\.Close\)/u);
  assert.match(composition, /typeof\(NInspectCardScreen\), nameof\(NInspectCardScreen\.Close\)/u);
});

test('mouse adapter dispatches only actual two-field left input without state edits or task completion', () => {
  const mouse = native('NativeMouseCardConfirmation');
  assert.match(mouse, /UnsafeAccessorKind\.Method, Name = "IsCardInPlayZone"/u);
  assert.match(mouse, /ButtonIndex = MouseButton\.Left, Pressed = expected\.Pressed/u);
  assert.match(mouse, /input\.IsActionPressed\(shortcut, false, false\)/u);
  assert.match(mouse, /expected\.Play\._Input\(input\);/u);
  assert.match(mouse, /NativeInputResult\.Unknown\("native_mouse_confirm_input_unknown"/u);
  assert.doesNotMatch(mouse, /IsCardInCancelZone|TryPlayCard|SetValue|TrySetResult|SetResult|Position\s*=|GlobalPosition|WarpMouse|Input\.ParseInputEvent|\.Invoke\(/u);
  assert.match(mouse, /operation\.Active \?\? operation\.Completed/u);
  assert.match(mouse, /owner\.Active is not \{ \} ticket/u);
  assert.match(mouse, /ReferenceEquals\(current\.Ticket, expected\.Ticket\)/u);
});

test('inspector capture only uses completed source identity and never materializes or exposes a private roster', () => {
  const inspect = native('NativeCardInspectionBinding');
  assert.match(inspect, /source\.Completed is not \{ \} ticket/u);
  assert.match(inspect, /ActiveScreenContext\.Instance\.IsCurrent\(owner\)/u);
  assert.match(inspect, /!SameDisplay\(ticket\.Fact, current\)/u);
  assert.match(inspect, /entered\.Checked == returned\.Checked/u);
  assert.doesNotMatch(inspect, /MutableClone|UpgradeInternal|CloneCard|\.Open\(|UpdateCardDisplay\(|GetOrCreate|\.Select\(|\.ToArray\(/u);
  const information = source('components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuInformation.cs');
  const relation = information.slice(information.indexOf('internal static PlayerEnvironmentSnapshot ProjectCardInspectionRelation'),
    information.indexOf('private static NativeInputResult ClickCardInspectControl'));
  assert.doesNotMatch(relation, /\.List\b|\.Index\b|\.Dispatch|BoundAction|\["(?:cards|index|source_list)"\]/u);
  assert.match(relation, /new\(true, false, false, false, basis\)/u);
});
