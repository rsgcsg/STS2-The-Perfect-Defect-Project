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
test('family publication routes are typed native observations with no independent clocks or dispatch', () => {
  const hooks = source('apps/game-mod/ConnectorNativeLogicalPatches.cs');
  const family = source('apps/game-mod/ConnectorNativeLogicalFamily.cs');
  assert.doesNotMatch(family, /Recorder|Annotator|Task\.Run|await |JsonSerializer|File\.|Dispatch\(|Timer|DateTime|Stopwatch|ContinueWith|GetInstanceField|SetValue|Dictionary/u);
  assert.doesNotMatch(hooks, /PatchAll|transpiler|AccessTools\.TypeByName/u);
  assert.match(family, /ReferenceEquals\(ActiveScreenContext\.Instance\.GetCurrentScreen\(\), node\)/u);
  assert.match(family, /ConnectorMod\.IsLiveNode\(node\)/u);
  assert.match(family, /Live\(node\) && node\.IsNodeReady\(\)/u);
  assert.match(family, /ConnectorMod\.IsNodeVisible\(canvas\)/u);
  assert.match(family, /ReferenceEquals\(node\.GetParent\(\), __instance\)/u);
  assert.match(family, /__state \|\| \(Reward\(__instance\) && Current\(__instance\)\)/u);
  assert.match(family, /__state \|\| \(Information\(__instance\) && Current\(__instance\)\)/u);
  assert.match(family, /node is NRewardButton or NProceedButton or NCardRewardAlternativeButton/u);
  assert.match(family, /__instance is NGridCardHolder && OwnedControl\(__instance, true\)/u);
});
test('reward publication waits for native owner registration and presentation finalization', () => {
  const hooks = source('apps/game-mod/ConnectorNativeLogicalPatches.cs');
  const family = source('apps/game-mod/ConnectorNativeLogicalFamily.cs');
  assert.match(hooks, /RewardShown\), after: new\[\] \{ foundation \}/u);
  assert.match(hooks, /CardRewardShown\), after: new\[\] \{ foundation \}/u);
  assert.match(hooks, /CardRewardRefreshReturned\), nameof\(ConnectorNativeLogicalFamily\.RewardBefore\),\s*finalizer: true, after: new\[\] \{ foundation, presentation \}/u);
  assert.match(family, /if \(__exception is null\) Publish\(true, "native_reward_catalog", "card_reward_refresh_finalized"\);\s*else PlayerEnvironmentService\.NativeLogical\.PublishMissing/u);
  assert.match(family, /return __exception;/u);
});
test('composition advertises the whole fixed profile only after all hook registrations', () => {
  const hooks = source('apps/game-mod/ConnectorNativeLogicalPatches.cs');
  const setup = hooks.slice(hooks.indexOf('internal static void Initialize()'), hooks.indexOf('private static void Patch('));
  assert.ok(setup.indexOf('RegisterFamilies(harmony);') < setup.indexOf('InstallNativeLogicalPublicationProfile('));
  assert.ok(setup.indexOf('InstallNativeLogicalPublicationProfile(') < setup.indexOf('initialized = true;'));
  const profile = JSON.parse(source('components/connector/contracts/native-logical-publication-profile-v1.json'));
  for (const seam of profile.required_seams) assert.ok(hooks.includes(`"${seam.source_seam}"`));
  assert.doesNotMatch(setup, /catch|unsupported|sampled/u);
});
