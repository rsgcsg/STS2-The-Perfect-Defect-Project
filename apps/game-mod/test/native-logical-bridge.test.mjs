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
test('inspect departure is bound to one exact native callback and expires at its finalizer', () => {
  const hooks = source('apps/game-mod/ConnectorNativeLogicalPatches.cs');
  const family = source('apps/game-mod/ConnectorNativeLogicalFamily.cs');
  const departure = source('apps/game-mod/ConnectorNativeLogicalInspectionDeparture.cs');
  assert.match(hooks, /game\.MainAssemblySha256 != ConnectorNativeLogicalInspectionDeparture\.GameAssemblySha256/u);
  assert.match(hooks, /game\.MainAssemblyMvid != ConnectorNativeLogicalInspectionDeparture\.GameModuleVersionId/u);
  assert.match(hooks, /GetMethod\(ConnectorNativeLogicalInspectionDeparture\.NativeCallbackName,\s*BindingFlags\.Instance \| BindingFlags\.NonPublic \| BindingFlags\.DeclaredOnly, none\)/u);
  assert.match(hooks, /FamilyPatch\(harmony, departure, nameof\(ConnectorNativeLogicalFamily\.InspectDepartureReturned\),\s*nameof\(ConnectorNativeLogicalFamily\.InspectDepartureBefore\), finalizer: true\)/u);
  assert.doesNotMatch(hooks, /PropertySetter\(typeof\(CanvasItem\)|GetMethods\(|<Close>.*StartsWith/u);
  assert.match(departure, /NativeCallbackName = "<Close>b__23_0"/u);
  assert.doesNotMatch(departure, /Task|Timer|DateTime|Stopwatch|File\.|Dictionary|Queue|PublicationIndex/u);
  assert.match(family, /NGame\.Instance\?\.InspectCardScreen, ActiveScreenContext\.Instance\.GetCurrentScreen\(\), ActiveScreenContext\.Instance/u);
  assert.match(family, /finally \{ __state\.Dispose\(\); \}/u);
});
test('departure duplicate avoidance uses actual Update accounting including missing and no stale recapture', () => {
  const family = source('apps/game-mod/ConnectorNativeLogicalFamily.cs');
  const departure = source('apps/game-mod/ConnectorNativeLogicalInspectionDeparture.cs');
  assert.match(family, /bool accounted = eligible && PlayerEnvironmentService\.NativeLogical\.PublishTracked/u);
  assert.match(family, /ConnectorNativeLogicalInspectionDeparture\.ContextReturned\(__instance, accounted\)/u);
  assert.match(departure, /if \(publicationAccounted\) return InspectionDepartureDisposition\.ExistingContextPublication;/u);
  assert.match(departure, /if \(disposed \|\| nativeFailed \|\| !contextReturned\) return InspectionDepartureDisposition\.Missing;/u);
  const finalized = family.slice(family.indexOf('internal static Exception? InspectDepartureReturned'), family.indexOf('internal static void RewardBefore'));
  assert.doesNotMatch(finalized, /CurrentFamily/u);
  assert.match(finalized, /NativeLogical\.PublishMissing\("native_information_owner", "inspect_delayed_close_failed"/u);
  assert.match(finalized, /return __exception;/u);
});
