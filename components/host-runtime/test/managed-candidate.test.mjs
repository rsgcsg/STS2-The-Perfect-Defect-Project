import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync
} from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  discoverGameDirectory,
  readDiskIdentity,
  resolveInstallation
} from "../src/game-installation.mjs";
import {
  addedPatchPaths,
  canonicalizeManagedCandidateDirectory,
  inspectManagedCandidateBuild,
  assertManagedCandidateGame,
  chooseManagedCandidateAction,
  loadManagedCandidateManifest,
  runManagedCandidateCapacity,
  runManagedCandidateProbe,
  selectManagedCandidateManifest,
  toBashPath
} from "../src/managed-candidate.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("managed candidate source and measured build identity match the manifest", () => {
  const { manifest } = loadManagedCandidateManifest(ROOT);
  assert.equal(manifest.status, "candidate_built_unqualified");
  assert.deepEqual(Object.keys(manifest.expected_build), [
    "source_patch_sha256", "artifact_sha256", "artifact_mvid", "localization_sha256"
  ]);
  const sourcePatch = readFileSync(path.join(ROOT, "experiments", "managed-exact", manifest.source_patch));
  assert.equal(
    createHash("sha256").update(sourcePatch).digest("hex"),
    manifest.expected_build.source_patch_sha256
  );
  assert.equal(manifest.expected_build.artifact_sha256, "e6f68ebb3e6e2c6a1d3e0c86332014ac6fbb7f0bace2a365ac473fd912db65e5");
  assert.equal(manifest.expected_build.artifact_mvid, "12a48a52-aab8-4f37-b42e-994150b48c02");
  assert.equal(manifest.previous_candidate_build.artifact_sha256, "003dd9f0fe8e5081ccce577d9963e143511d6a42461624df17b7e56ea35b4d93");
  assert.equal(manifest.previous_candidate_build.artifact_mvid, "5da84f07-fa96-4538-91c2-974180da3e62");
  assert.equal(manifest.historical_candidate_builds.some(build => build.artifact_sha256 === "75be6efa9ab35ef4eca4c2ce4153918bb8c14c75bee61d4f837b31c1727a7aba"
    && build.artifact_mvid === "c91e08aa-797e-4837-a320-bdbb92e5b9b2"), true);
  assert.equal(manifest.last_measured_build.artifact_sha256, "dd4b10f22606203f8825569c2e0478626d96ac1166d2cd9430591be607d808a6");
  assert.equal(manifest.last_measured_build.artifact_mvid, "61b5b737-724e-4887-a0a4-6664a3c9daea");
  assert.equal(manifest.admission.forbidden_claims.includes("formal H1.0 qualification"), true);
  assert.ok(manifest.semantic_shims.some((entry) => entry.risk === "critical"));
  assert.equal(manifest.platform_baselines.length, 1);
  assert.throws(() => assertManagedCandidateGame(manifest, {
    platform: "darwin",
    architecture: "arm64",
    release: { version: "v0.112.0", commit: "changed" },
    runtime_main_assembly_hash: 1,
    sts2_assembly: { sha256: "changed" },
    godotsharp_assembly: { sha256: "changed" }
  }), /refuses this game identity/u);
});

test("managed candidate patch keeps normal actions on native identity and commit paths", () => {
  const { manifest } = loadManagedCandidateManifest(ROOT);
  const patch = readFileSync(path.join(ROOT, "experiments", "managed-exact", manifest.source_patch), "utf8");
  const additions = patch.split(/\r?\n/u)
    .filter((line) => line.startsWith("+") && !line.startsWith("+++"))
    .map((line) => line.slice(1))
    .join("\n");
  for (const forbidden of [
    "EnqueueWithoutSynchronizing",
    "RunManager.Instance.EnterMapCoord(",
    "EnterRoom(new MapRoom",
    "ForceToMap",
    "HealBetweenActs",
    "RunManager.Instance.EnterNextAct(",
    "NeutralizePrefix",
    "PotionCmd.Discard"
  ]) {
    assert.equal(additions.includes(forbidden), false, `normal action patch must not add ${forbidden}`);
  }
  assert.match(additions, /TryManualPlay\(target\)/u);
  assert.match(additions, /NativePlayWindowAdmission\.IsOpen/u);
  assert.match(additions, /ActionSynchronizerCombatState\.PlayPhase/u);
  assert.match(additions, /Cannot play a card while a native selection is pending/u);
  assert.match(additions, /EnqueueManualUse\(target\)/u);
  assert.match(additions, /ActionQueueSynchronizer\.RequestEnqueue/u);
  assert.match(additions, /NativeObjectIdentity\.Get/u);
  assert.match(additions, /GameOverState\(_runState\.CurrentRoom\?\.IsVictoryRoom == true\)/u);
  assert.doesNotMatch(additions, /if \(RunManager\.Instance\.IsGameOver\)\s+return GameOverState\(true\)/u);
});

test("fresh candidate preparation admits added source files into the audited diff", () => {
  const source = readFileSync(path.join(ROOT, "src", "managed-candidate.mjs"), "utf8");
  const { manifest } = loadManagedCandidateManifest(ROOT);
  const patch = readFileSync(
    path.join(ROOT, "experiments", "managed-exact", manifest.source_patch),
    "utf8"
  );
  assert.deepEqual(addedPatchPaths(patch), [
    "native-tests/NativeMechanisms.csproj",
    "native-tests/Program.cs",
    "src/Sts2Headless/DeckEnchantCallScope.cs",
    "src/Sts2Headless/DeckGenericCallScope.cs",
    "src/Sts2Headless/DeckGenericSelection.cs",
    "src/Sts2Headless/DeckTransformCallScope.cs",
    "src/Sts2Headless/DeckUpgradeCallScope.cs",
    "src/Sts2Headless/DeckUpgradeSelection.cs",
    "src/Sts2Headless/FairPileProjection.cs",
    "src/Sts2Headless/ManagedMerchantPresentation.cs",
    "src/Sts2Headless/ManagedSelectorPublicCapture.cs",
    "src/Sts2Headless/NativeCalculatedVariableInventory.cs",
    "src/Sts2Headless/NativeCardPreviewReadQualification.Rendering.cs",
    "src/Sts2Headless/NativeCardPreviewReadQualification.cs",
    "src/Sts2Headless/NativeCardRenderTransaction.cs",
    "src/Sts2Headless/NativeCardRewardAlternativePreview.cs",
    "src/Sts2Headless/NativeCardRewardParentPresentation.cs",
    "src/Sts2Headless/NativeCardRewardSelectionObservation.cs",
    "src/Sts2Headless/NativeDetachedCardCopy.cs",
    "src/Sts2Headless/NativeDetachedCardPreview.cs",
    "src/Sts2Headless/NativeEnchantmentMethodInventory.cs",
    "src/Sts2Headless/NativeEventOptionAdmission.cs",
    "src/Sts2Headless/NativeMapReadProjection.cs",
    "src/Sts2Headless/NativeMerchantRemovalAdmission.cs",
    "src/Sts2Headless/NativePlayWindowAdmission.cs",
    "src/Sts2Headless/NativePotionTargetAdmission.cs",
    "src/Sts2Headless/NativePublicCardDetails.cs",
    "src/Sts2Headless/NativeRelicPresentation.cs",
    "src/Sts2Headless/NativeRewardSetObservation.cs",
    "src/Sts2Headless/NativeRunHookReadQualification.cs",
    "src/Sts2Headless/NativeSelectorCallScope.cs",
    "src/Sts2Headless/NativeSelectorCardDisplay.cs",
    "src/Sts2Headless/NativeSelectorHud.cs",
    "src/Sts2Headless/NativeSelectorSelection.cs",
    "src/Sts2Headless/NativeTreasureChestAdmission.cs",
    "src/Sts2Headless/NativeUpgradeMethodInventory.cs",
    "src/Sts2Headless/NativeVisibleText.cs",
    "src/Sts2Headless/PerformanceLab.cs",
    "tests/NativePlayWindowAdmissionTests.csproj",
    "tests/Program.cs"
  ]);  assert.equal(source.includes('["add", "--intent-to-add"'), true);
  assert.equal(source.includes("stpd-managed-candidate.patch"), true);
  assert.equal(source.includes("normalizeText(readFileSync(patchFile"), true);
  assert.equal(source.includes('"core.autocrlf=false"'), true);
  assert.equal(source.includes('"--no-checkout"'), false);
});

test("managed candidate selects Windows as separate provenance without changing macOS", () => {
  const { manifest } = loadManagedCandidateManifest(ROOT);
  const selected = selectManagedCandidateManifest(manifest, {
    platform: "win32",
    architecture: "x64",
    release: { version: "v0.111.0", commit: "41cef1ea" },
    runtime_main_assembly_hash: 222455745,
    sts2_assembly: {
      sha256: "0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9"
    },
    godotsharp_assembly: {
      sha256: "0e4897ecdfb31456a97c7d8028dfb8d7dbdc632e2f73fc9b438d7b266a139289"
    }
  });
  assert.equal(selected.selected_baseline.status, "historical_windows_candidate_previous_patch");
  assert.equal(
    selected.expected_build.artifact_sha256,
    "0d8c916365f0a64a0ed5cfc706186811e33708c841fef82e1f73c6a33dcfcc4d"
  );
  assert.equal(manifest.exact_game.platform, "darwin");
  assert.equal(manifest.last_measured_build.artifact_mvid, "61b5b737-724e-4887-a0a4-6664a3c9daea");
});

test("managed setup converts drive-qualified Windows paths for Git Bash", () => {
  assert.equal(
    toBashPath("E:\\SteamLibrary\\steamapps\\common\\Slay the Spire 2\\data", "win32"),
    "/e/SteamLibrary/steamapps/common/Slay the Spire 2/data"
  );
  assert.equal(toBashPath("/Applications/Game/data", "darwin"), "/Applications/Game/data");
  assert.throws(() => toBashPath("\\\\server\\share\\game", "win32"), /drive-qualified/u);
});

test("managed candidate path aliases resolve before creating a missing build root", (context) => {
  const temporaryRoot = mkdtempSync(path.join(os.tmpdir(), "sts2-managed-candidate-path-"));
  try {
    const physicalParent = path.join(temporaryRoot, "physical");
    const aliasedParent = path.join(temporaryRoot, "alias");
    mkdirSync(physicalParent);
    try {
      symlinkSync(physicalParent, aliasedParent, process.platform === "win32" ? "junction" : "dir");
    } catch (error) {
      if (["EPERM", "EACCES", "ENOTSUP"].includes(error?.code)) {
        context.skip(`directory symlinks are unavailable: ${error.code}`);
        return;
      }
      throw error;
    }

    const requested = path.join(aliasedParent, "not-yet-created", "candidate");
    const resolved = canonicalizeManagedCandidateDirectory(requested);
    assert.equal(resolved.requested, path.resolve(requested));
    assert.equal(resolved.canonical, path.join(realpathSync.native(physicalParent), "not-yet-created", "candidate"));
    assert.equal(existsSync(path.dirname(requested)), true);
    assert.equal(existsSync(resolved.canonical), false);
    const requestedThroughPhysicalParent = canonicalizeManagedCandidateDirectory(
      path.join(physicalParent, "not-yet-created", "candidate")
    );
    assert.equal(requestedThroughPhysicalParent.canonical, resolved.canonical);
    assert.notEqual(requestedThroughPhysicalParent.requested, resolved.requested);
  } finally {
    rmSync(temporaryRoot, { recursive: true, force: true });
  }
});

test("managed preparation and runtime launch use the canonical candidate root", () => {
  const source = readFileSync(path.join(ROOT, "src", "managed-candidate.mjs"), "utf8");
  const prepareStart = source.indexOf("export async function prepareManagedCandidate(");
  const runtimeStart = source.indexOf("export async function startManagedCandidateRuntime(");
  const prepare = source.slice(prepareStart, runtimeStart);
  const canonicalize = prepare.indexOf("canonicalizeManagedCandidateDirectory(requestedDestination)");
  assert.ok(canonicalize >= 0);
  const canonicalPreparation = prepare.slice(canonicalize);
  assert.match(
    canonicalPreparation,
    /await run\("git", \[\s*"clone",\s*"--config",\s*"core\.autocrlf=false",\s*manifest\.upstream\.url,\s*destination\s*\]\);/u
  );
  assert.match(
    canonicalPreparation,
    /await run\("bash", \["setup\.sh", toBashPath\(gameDataDirectory\)\], \{\s*cwd: destination,\s*timeout: 600_000,\s*env: \{ \.\.\.process\.env, DOTNET: dotnet\.command \}\s*\}\);/u
  );
  assert.match(
    canonicalPreparation,
    /"build", project,[\s\S]*cwd: destination,[\s\S]*STS2_LIB: path\.join\(destination, "lib"\)/u
  );
  assert.match(
    canonicalPreparation,
    /inspectManagedCandidateBuild\(\{ root, candidateDirectory: destination, manifest \}\)/u
  );

  const runtimeEnd = source.indexOf("\nfunction stateDigest", runtimeStart);
  const runtime = source.slice(runtimeStart, runtimeEnd);
  assert.match(runtime, /const resolvedCandidateDirectory = build\.candidate_directory;/u);
  assert.match(runtime, /cwd: resolvedCandidateDirectory/u);
  assert.match(runtime, /STS2_LIB: path\.join\(resolvedCandidateDirectory, "lib"\)/u);
});

test("managed probe policy uses advertised semantic operands and fails closed on unknown decisions", () => {
  assert.deepEqual(chooseManagedCandidateAction({
    decision: "map_select",
    choices: [
      { row: 2, col: 1, native_ref: "map-b" },
      { row: 1, col: 2, native_ref: "map-a" }
    ]
  }), {
    cmd: "action",
    action: "select_map_node",
    args: { col: 2, row: 1, map_point_ref: "map-a" }
  });
  assert.deepEqual(chooseManagedCandidateAction({
    decision: "combat_rewards_complete",
    room_ref: "boss-room-a"
  }), {
    cmd: "action",
    action: "proceed",
    args: { room_ref: "boss-room-a" }
  });
  assert.deepEqual(chooseManagedCandidateAction({
    decision: "reward_set",
    rewards: [{ native_ref: "reward-a", kind: "gold" }],
    is_terminal: true,
    can_proceed: true,
    room_ref: "room-a"
  }), {
    cmd: "action",
    action: "select_reward",
    args: { reward_ref: "reward-a" }
  });
  assert.deepEqual(chooseManagedCandidateAction({
    decision: "treasure_relic",
    room_ref: "treasure-room-a",
    relics: [{ native_ref: "relic-a", name: "Bag" }],
    can_skip: true
  }), {
    cmd: "action",
    action: "select_treasure_relic",
    args: { room_ref: "treasure-room-a", relic_ref: "relic-a" }
  });
  assert.deepEqual(chooseManagedCandidateAction({
    decision: "combat_play",
    hand: [{
      id: "STRIKE",
      native_ref: "card-a",
      can_play: true,
      target_type: "AnyEnemy",
      valid_target_refs: ["enemy-a", "enemy-b"]
    }],
    enemies: [
      { id: "B", native_ref: "enemy-b", index: 1, hp: 5 },
      { id: "A", native_ref: "enemy-a", index: 0, hp: 5 }
    ]
  }), {
    cmd: "action",
    action: "play_card",
    args: { card_ref: "card-a", target_ref: "enemy-a" }
  });
  assert.equal(chooseManagedCandidateAction({ decision: "unrecognized" }), null);
});

test("managed build inspection reports the physical path for a candidate alias", async (context) => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) {
    context.skip("set STS2_MANAGED_TEST_CANDIDATE for the proprietary exact-build integration gate");
    return;
  }
  const { manifest: loadedManifest } = loadManagedCandidateManifest(ROOT);
  const gameDirectory = discoverGameDirectory();
  assert.ok(gameDirectory, "integration candidate requires the installed exact game");
  const manifest = selectManagedCandidateManifest(
    loadedManifest,
    readDiskIdentity(resolveInstallation(gameDirectory))
  );
  const result = await inspectManagedCandidateBuild({ root: ROOT, candidateDirectory: candidate, manifest });
  assert.equal(path.isAbsolute(result.candidate_directory), true);
  assert.equal(result.candidate_directory, realpathSync.native(path.resolve(candidate)));
  assert.equal(path.isAbsolute(result.artifact), true);
  assert.equal(result.artifact_mvid, manifest.expected_build.artifact_mvid);
});

test("managed probes reject invalid workload dimensions before touching a runtime", async () => {
  await assert.rejects(runManagedCandidateProbe({
    root: ROOT,
    candidateDirectory: "unused",
    diskIdentity: {},
    seed: "TEST",
    maxActions: 0
  }), /maxActions must be a positive integer/u);
  await assert.rejects(runManagedCandidateProbe({
    root: ROOT,
    candidateDirectory: "unused",
    diskIdentity: {},
    seed: "TEST",
    episodeCount: 1.5
  }), /episodeCount must be a positive integer/u);
  await assert.rejects(runManagedCandidateProbe({
    root: ROOT,
    candidateDirectory: "unused",
    diskIdentity: {},
    seed: "TEST",
    resetAtDecisions: [""]
  }), /resetAtDecisions must contain non-empty decision names/u);
  await assert.rejects(runManagedCandidateCapacity({
    root: ROOT,
    candidateDirectory: "unused",
    diskIdentity: {},
    workerCounts: [0],
    evidenceRoot: "unused"
  }), /workerCount must be a positive integer/u);
});
