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

test("built native act-selection source and retained measured builds remain explicit", () => {
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
  assert.equal(manifest.pre_hand_selection_build.artifact_sha256, "b8386bf8bd54086b1921ddade3cd4fb4a5156072ad45456bc3310e2b62275c8f");
  assert.equal(manifest.expected_build.artifact_sha256, "0d51a9cc9f9af64ead27ec978a7eb35573353e4b61c3e7d3bd1b5bebc7c7d5a2");
  assert.equal(manifest.historical_candidate_builds.some(build => build.artifact_sha256 === "c3eed8fa9930d7428af0d9028a4ca0a0e5234853d8040bc99dd478f99fdd4df4"), true);
  assert.equal(manifest.expected_build.artifact_mvid, "30cbfc58-f916-44ff-a60e-788194dbf4d4");
  assert.equal(manifest.historical_candidate_builds.some(build =>
    build.source_patch_sha256 === "a46736eee8c0c4c65b574a53e769330831d2e39de7897563f4a4c16b48fc5e20"
    && build.artifact_sha256 === "39a6455c2f67c4313b3cf2b6b46a6783d41cc5ebe86cb62829a6a780c7d562b2"
    && build.artifact_mvid === "a52dd778-7725-4794-80cb-4d532c4e528a"), true);
  assert.equal(manifest.historical_candidate_builds.some(build =>
    build.source_patch_sha256 === "41b15fbf27ef26ef4c04650adbdb8a437969743931bc8dc7263b88910b3e312d"
    && build.artifact_sha256 === "ef907317a47900204caad7400c341213f5dc533549d7c33a913066e83e522766"
    && build.artifact_mvid === "8b84be95-3391-49d9-b11e-69aa29afa38a"
    && build.localization_sha256 === "7ba5ef6ac07c0be696499ba2bbf853f05830883475fefef3bbc7ffd25d8a77d0"), true);
  assert.equal(manifest.historical_candidate_builds.some(build => build.artifact_sha256 === "a70d8de18578cc2666ec854d8d8eb6bb85868317335fb67fcfbb7151133d7f66"), true);
  assert.equal(manifest.previous_candidate_build.artifact_sha256, "0b376bf504b75522e3e8e9bb766decd30ea4905f9ce484c4cfcece23701cea24");
  assert.equal(manifest.historical_candidate_builds.some(build => build.artifact_sha256 === "0426384c8c38e1a147dcfb2ba39c599ebdebe191f979acaaab8a4e302060943b" && build.artifact_mvid === "1115859e-f311-4e2e-b103-0b3c91168250"), true);
  assert.equal(manifest.previous_candidate_build.artifact_mvid, "f9fcb312-3c64-4e22-96d0-7436aef82e1c");
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
  assert.match(additions, /ManagedGameOverPresentation\.Install\(harmony\)/u);
  assert.match(additions, /\["victory"\] = presentation\.Victory/u);
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
    "initial-progress-tests/InitialProgressTests.csproj",
    "initial-progress-tests/Program.cs",
    "native-hand-tests/NativeHand.csproj",
    "native-hand-tests/Program.cs",
    "native-run-choice-tests/NativeRunChoice.csproj",
    "native-run-choice-tests/Program.cs",
    "native-tests/NativeMechanisms.csproj",
    "native-tests/Program.cs",
    "src/Sts2Headless/DeckEnchantCallScope.cs",
    "src/Sts2Headless/DeckGenericCallScope.cs",
    "src/Sts2Headless/DeckGenericSelection.cs",
    "src/Sts2Headless/DeckTransformCallScope.cs",
    "src/Sts2Headless/DeckUpgradeCallScope.cs",
    "src/Sts2Headless/DeckUpgradeSelection.cs",
    "src/Sts2Headless/FairPileProjection.cs",
    "src/Sts2Headless/ManagedBundlePresentation.cs",
    "src/Sts2Headless/ManagedEventPresentation.cs",
    "src/Sts2Headless/ManagedGameOverPresentation.cs",
    "src/Sts2Headless/ManagedInitialProgress.cs",
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
    "src/Sts2Headless/NativeHandSelection.cs",
    "src/Sts2Headless/NativeMapReadProjection.cs",
    "src/Sts2Headless/NativeMerchantPotionCost.cs",
    "src/Sts2Headless/NativeMerchantRemovalAdmission.cs",
    "src/Sts2Headless/NativePlayWindowAdmission.cs",
    "src/Sts2Headless/NativePotionTargetAdmission.cs",
    "src/Sts2Headless/NativePublicCardDetails.cs",
    "src/Sts2Headless/NativeRelicPresentation.cs",
    "src/Sts2Headless/NativeRewardSetObservation.cs",
    "src/Sts2Headless/NativeRunHookReadQualification.cs",
    "src/Sts2Headless/NativeScreenShakePresentation.cs",
    "src/Sts2Headless/NativeSelectorCallScope.cs",
    "src/Sts2Headless/NativeSelectorCardDisplay.cs",
    "src/Sts2Headless/NativeSelectorHud.cs",
    "src/Sts2Headless/NativeSelectorSelection.cs",
    "src/Sts2Headless/NativeTreasureChestAdmission.cs",
    "src/Sts2Headless/NativeUpgradeMethodInventory.cs",
    "src/Sts2Headless/NativeVisibleText.cs",
    "src/Sts2Headless/PerformanceLab.cs",
    "src/Sts2Headless/SimpleGridSelection.cs",
    "terminal-tests/Program.cs",
    "terminal-tests/TerminalMechanisms.csproj",
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
