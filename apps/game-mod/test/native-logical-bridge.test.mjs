import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const root = resolve(import.meta.dirname, '../../..');
const source = path => readFileSync(resolve(root, path), 'utf8');
test('native logical hooks remain typed passive composition independent of Recorder', () => {
  const hooks = source('apps/game-mod/ConnectorNativeLogicalPatches.cs');
  assert.doesNotMatch(hooks, /Recorder|Annotator|Task\.Run|await |JsonSerializer|File\.|Dispatch\(/u);
  for (const type of ['NTargetManager', 'NCard', 'NInspectCardScreen', 'NGameOverScreen'])
    assert.ok(hooks.includes(`typeof(${type})`));
  assert.match(hooks, /harmony\.Patch\(original, postfix: new HarmonyMethod\(postfix\)\)/u);
  assert.doesNotMatch(hooks, /PatchAll|transpiler|AccessTools\.TypeByName/u);
  const init = source('apps/game-mod/UnifiedPlatformMod.cs');
  assert.ok(init.indexOf('ConnectorMod.Initialize();') < init.indexOf('ConnectorNativeLogicalPatches.Initialize();'));
  assert.ok(init.indexOf('ConnectorNativeLogicalPatches.Initialize();') < init.indexOf('RecorderMod.Initialize();'));
});
test('native queries use strict frozen wire and keep read operations off the native queue', () => {
  const transport = source('components/connector/host/PlayerEnvironment/Transport/ConnectorMod.NativeLogical.cs');
  assert.match(transport, /NativeLogicalDecoder\.Decode<T>\(bytes\)/u);
  assert.match(transport, /NativeLogicalWire\.EncodeBounded\(value, maxBytes\)/u);
  for (const operation of ['read', 'catalog', 'resolve', 'events', 'await', 'cancel_wait', 'detach', 'renew', 'retain', 'release']) {
    const start = transport.indexOf(`case "${operation}":`);
    const end = transport.indexOf('case "', start + 8);
    const block = transport.slice(start, end < 0 ? transport.indexOf('default:', start) : end);
    assert.doesNotMatch(block, /RunOnMainThread|\.Dispatch\(/u);
  }
});
