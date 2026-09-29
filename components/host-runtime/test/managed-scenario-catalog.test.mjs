import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  createValidatedManagedScenarioDriver,
  getManagedScenario,
  listManagedScenarios,
  resolveManagedRepeatabilitySelection
} from "../src/managed-scenario-catalog.mjs";

const ID = "managed-engineering-map-combat-six-v1";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CLI = path.join(ROOT, "tools", "managed-exact.mjs");

function expectedGame(definition) {
  const exact = definition.exact_game;
  return {
    platform: exact.platform,
    architecture: exact.architecture,
    release: { version: exact.version, commit: exact.commit },
    runtime_main_assembly_hash: exact.runtime_main_assembly_hash,
    sts2_assembly: { sha256: exact.sts2_dll_sha256 },
    godotsharp_assembly: { sha256: exact.godotsharp_dll_sha256 }
  };
}

function expectedManifest(definition) {
  return {
    candidate_id: definition.candidate.candidate_id,
    upstream: { revision: definition.candidate.upstream_revision },
    expected_build: {
      source_patch_sha256: definition.candidate.source_patch_sha256,
      artifact_sha256: definition.candidate.artifact_sha256,
      artifact_mvid: definition.candidate.artifact_mvid
    }
  };
}

test("built-in catalog is metadata-only and exposes the reviewed engineering scenario", () => {
  const scenarios = listManagedScenarios();
  assert.equal(scenarios.length, 1);
  assert.equal(scenarios[0].catalog_id, ID);
  assert.equal(scenarios[0].label, "工程重复性");
  assert.equal(scenarios[0].scenario.max_actions, 6);
  assert.equal(scenarios[0].scenario.discovery_max_actions, 8);
  assert.equal(scenarios[0].scenario.read_policy, "none");
  assert.equal(scenarios[0].character, "Ironclad");
  assert.equal(getManagedScenario("unknown"), null);
  scenarios[0].scenario.seed = "MUTATED";
  assert.equal(getManagedScenario(ID).scenario.seed, "H1CROSSHOST01");
});

test("built-in engineering scenario retains its measured candidate after a new patch", () => {
  const definition = getManagedScenario(ID);
  const manifest = JSON.parse(readFileSync(
    path.join(ROOT, "experiments", "managed-exact", "manifest.json"), "utf8"
  ));
  assert.deepEqual(definition.exact_game, manifest.exact_game);
  assert.deepEqual(definition.candidate, {
    candidate_id: manifest.candidate_id,
    upstream_revision: manifest.upstream.revision,
    source_patch_sha256: "40d2e4cde715954c75125c12f30cc31cdf2b08dffd619bf95ca28f9913e7e030",
    artifact_sha256: "9a1d9445971d54f471701a84a6f95ed4984a2d60dc1319e1846f068eda1fd02b",
    artifact_mvid: "a75a426d-db7c-45a6-9d34-1179b2e35003"
  });
  assert.notEqual(definition.candidate.source_patch_sha256, manifest.expected_build.source_patch_sha256);
});

test("built-in selection accepts only matching explicit overrides", () => {
  const selected = resolveManagedRepeatabilitySelection([
    "--scenario-id", ID,
    "--character", "Ironclad",
    "--max-actions", "6"
  ]);
  assert.equal(selected.kind, "builtin");
  assert.equal(selected.definition.catalog_id, ID);
  assert.throws(() => resolveManagedRepeatabilitySelection(["--scenario-id", "missing"]), /Unknown managed scenario ID/);
  assert.throws(() => resolveManagedRepeatabilitySelection([
    "--scenario-id", ID, "--scenario", "custom.json"
  ]), /Choose either/);
  assert.throws(() => resolveManagedRepeatabilitySelection([
    "--scenario-id", ID, "--seed", "OTHER"
  ]), /conflicts with built-in scenario/);
  assert.throws(() => resolveManagedRepeatabilitySelection([
    "--scenario-id", ID, "--character", "Defect"
  ]), /conflicts with built-in scenario/);
  assert.deepEqual(resolveManagedRepeatabilitySelection([
    "--scenario", "user-scenario.json"
  ]), { kind: "custom", file: "user-scenario.json" });
});

test("target mismatch is rejected before the Managed driver factory can run", async () => {
  const definition = getManagedScenario(ID);
  let factoryCalls = 0;
  const createDriver = async () => {
    factoryCalls += 1;
    throw new Error("driver factory must not run");
  };
  await assert.rejects(createValidatedManagedScenarioDriver({
    definition,
    actualGame: { ...expectedGame(definition), platform: "win32" },
    semanticTarget: definition.semantic_target,
    candidateManifest: expectedManifest(definition),
    createDriver
  }), /scenario_game_identity_mismatch/);
  await assert.rejects(createValidatedManagedScenarioDriver({
    definition,
    actualGame: expectedGame(definition),
    semanticTarget: { ...definition.semantic_target, presentation_language: "en" },
    candidateManifest: expectedManifest(definition),
    createDriver
  }), /scenario_semantic_target_mismatch/);
  await assert.rejects(createValidatedManagedScenarioDriver({
    definition,
    actualGame: expectedGame(definition),
    semanticTarget: definition.semantic_target,
    candidateManifest: { ...expectedManifest(definition), candidate_id: "other" },
    createDriver
  }), /scenario_candidate_identity_mismatch/);
  assert.equal(factoryCalls, 0);
});

test("matching target permits exactly one driver factory call", async () => {
  const definition = getManagedScenario(ID);
  let factoryCalls = 0;
  const driver = {
    descriptor: {
      driver_id: definition.candidate.candidate_id,
      semantic_target: definition.semantic_target,
      implementation: {
        source_revision: definition.candidate.upstream_revision,
        source_patch_sha256: definition.candidate.source_patch_sha256,
        artifact_sha256: definition.candidate.artifact_sha256
      }
    }
  };
  const result = await createValidatedManagedScenarioDriver({
    definition,
    actualGame: expectedGame(definition),
    semanticTarget: definition.semantic_target,
    candidateManifest: expectedManifest(definition),
    createDriver: async () => {
      factoryCalls += 1;
      return driver;
    }
  });
  assert.equal(result, driver);
  assert.equal(factoryCalls, 1);
});

test("catalog queries and invalid CLI selections do not need a game or candidate", () => {
  assert.deepEqual(listManagedScenarios().map(({ catalog_id }) => catalog_id), [ID]);
  assert.equal(getManagedScenario(ID).scenario.scenario_id,
    "same-seed-map-combat-prefix-20260929");
  assert.throws(() => resolveManagedRepeatabilitySelection(["--scenario-id", "not-in-catalog"]),
    /Unknown managed scenario ID/);
  assert.throws(() => resolveManagedRepeatabilitySelection([
    "--scenario-id", ID, "--seed", "OTHER"
  ]), /conflicts with built-in scenario/);

  const env = { ...process.env, STS2_GAME_DIR: "/path-that-must-not-be-discovered" };
  const listed = spawnSync(process.execPath, [CLI, "scenarios", "list"], { encoding: "utf8", env });
  assert.equal(listed.status, 0, listed.stderr);
  assert.equal(JSON.parse(listed.stdout)[0].catalog_id, ID);
  const shown = spawnSync(process.execPath,
    [CLI, "scenarios", "show", "--scenario-id", ID], { encoding: "utf8", env });
  assert.equal(shown.status, 0, shown.stderr);
  assert.equal(JSON.parse(shown.stdout).label, "工程重复性");

  const unknown = spawnSync(process.execPath,
    [CLI, "repeatability", "--scenario-id", "not-in-catalog"], { encoding: "utf8", env });
  assert.notEqual(unknown.status, 0);
  assert.match(unknown.stderr, /Unknown managed scenario ID/);
  assert.doesNotMatch(unknown.stderr, /Could not locate STS2|requires --candidate/);
  const conflict = spawnSync(process.execPath,
    [CLI, "repeatability", "--scenario-id", ID, "--seed", "OTHER"], { encoding: "utf8", env });
  assert.notEqual(conflict.status, 0);
  assert.match(conflict.stderr, /conflicts with built-in scenario/);
  assert.doesNotMatch(conflict.stderr, /Could not locate STS2|requires --candidate/);
});
