import { validateScenarioDescriptor } from "./host-driver.mjs";

const ENGINEERING_SCENARIO = Object.freeze({
  catalog_id: "managed-engineering-map-combat-six-v1",
  label: "工程重复性",
  purpose: "Bounded same-seed Managed engineering repeatability only.",
  scenario: Object.freeze({
    schema: "sts2.headless/scenario-1",
    scenario_id: "same-seed-map-combat-prefix-20260929",
    seed: "H1CROSSHOST01",
    policy_id: "deterministic-probe-1",
    max_actions: 6,
    discovery_max_actions: 8,
    start_interaction_kind: "map_navigation",
    read_policy: "none"
  }),
  character: "Ironclad",
  scenario_timeout_ms: 120_000,
  semantic_target: Object.freeze({
    schema: "sts2.headless/semantic-target-1",
    target_id: "sts2-v0.111.0-player-visible-zhs-v1",
    protocol_version: "1.0.0",
    game_build: Object.freeze({
      version: "v0.111.0",
      commit: "41cef1ea",
      main_assembly_hash: 1010476334
    }),
    content_policy_id: "vanilla_singleplayer_v1",
    information_policy_id: "player_visible_v1",
    presentation_language: "zhs"
  }),
  exact_game: Object.freeze({
    platform: "darwin",
    architecture: "arm64",
    version: "v0.111.0",
    commit: "41cef1ea",
    runtime_main_assembly_hash: 1010476334,
    sts2_dll_sha256: "9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4",
    godotsharp_dll_sha256: "0e4897ecdfb31456a97c7d8028dfb8d7dbdc632e2f73fc9b438d7b266a139289"
  }),
  candidate: Object.freeze({
    candidate_id: "wuhao21-sts2-cli-d11aa88-v01110",
    upstream_revision: "d11aa883b582dd68bd39b331f3370746b30d447e",
    source_patch_sha256: "61e0127e5860384c69be335c6e8d8e098f155a0b252da058e21fe44adcbcf990",
    artifact_sha256: "7876b7fe2534fe511305c9c00710b9cd412ab0e7f0c91bdb3091ece99b9c1a68",
    artifact_mvid: "4a9b280b-685d-4ff4-a4eb-396d18276548"
  })
});

const SCENARIOS = new Map([[ENGINEERING_SCENARIO.catalog_id, ENGINEERING_SCENARIO]]);

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function optionValues(args, name) {
  const values = [];
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] !== name) continue;
    const value = args[index + 1];
    if (typeof value !== "string" || value.startsWith("--")) {
      throw new Error(`${name} requires a value.`);
    }
    values.push(value);
  }
  return values;
}

function requireConsistentOption(args, name, expected) {
  const values = optionValues(args, name);
  if (values.some((value) => value !== String(expected))) {
    throw new Error(`${name} conflicts with built-in scenario ${ENGINEERING_SCENARIO.catalog_id}.`);
  }
}

function gameIdentity(identity) {
  return {
    platform: identity?.platform ?? null,
    architecture: identity?.architecture ?? null,
    version: identity?.release?.version ?? null,
    commit: identity?.release?.commit ?? null,
    runtime_main_assembly_hash: identity?.runtime_main_assembly_hash ?? null,
    sts2_dll_sha256: identity?.sts2_assembly?.sha256 ?? null,
    godotsharp_dll_sha256: identity?.godotsharp_assembly?.sha256 ?? null
  };
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value == null || typeof value !== "object") return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

function sameJson(left, right) {
  return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
}

export function listManagedScenarios() {
  return [...SCENARIOS.values()].map(({ catalog_id, label, purpose, scenario, character,
    scenario_timeout_ms, semantic_target, exact_game, candidate }) => clone({
    catalog_id,
    label,
    purpose,
    scenario,
    character,
    scenario_timeout_ms,
    semantic_target,
    exact_game,
    candidate
  }));
}

export function getManagedScenario(catalogId) {
  const scenario = SCENARIOS.get(catalogId);
  return scenario == null ? null : clone(scenario);
}

export function resolveManagedRepeatabilitySelection(args) {
  const ids = optionValues(args, "--scenario-id");
  const files = optionValues(args, "--scenario");
  if (ids.length > 0 && files.length > 0) {
    throw new Error("Choose either --scenario-id or --scenario, not both.");
  }
  if (ids.length > 1 && ids.some((id) => id !== ids[0])) {
    throw new Error("Conflicting --scenario-id values.");
  }
  if (ids.length === 0) {
    if (files.length === 0) throw new Error("repeatability requires --scenario <scenario.json> or --scenario-id <id>.");
    return { kind: "custom", file: files[0] };
  }

  const definition = SCENARIOS.get(ids[0]);
  if (definition == null) throw new Error(`Unknown managed scenario ID: ${ids[0]}`);
  const scenario = definition.scenario;
  for (const [name, expected] of [
    ["--seed", scenario.seed],
    ["--policy-id", scenario.policy_id],
    ["--max-actions", scenario.max_actions],
    ["--discovery-actions", scenario.discovery_max_actions],
    ["--start-kind", scenario.start_interaction_kind],
    ["--read-policy", scenario.read_policy],
    ["--character", definition.character],
    ["--language", definition.semantic_target.presentation_language]
  ]) requireConsistentOption(args, name, expected);

  const errors = validateScenarioDescriptor(scenario);
  if (errors.length > 0) throw new Error(`Built-in scenario is invalid: ${errors.join(", ")}`);
  return { kind: "builtin", definition: clone(definition) };
}

export function assertManagedScenarioTarget({ definition, actualGame, semanticTarget, candidateManifest }) {
  const errors = [];
  if (!sameJson(gameIdentity(actualGame), definition.exact_game)) errors.push("scenario_game_identity_mismatch");
  if (!sameJson(semanticTarget, definition.semantic_target)) errors.push("scenario_semantic_target_mismatch");
  const candidate = candidateManifest ?? {};
  const expectedCandidate = definition.candidate;
  const actualCandidate = {
    candidate_id: candidate.candidate_id ?? null,
    upstream_revision: candidate.upstream?.revision ?? null,
    source_patch_sha256: candidate.expected_build?.source_patch_sha256 ?? null,
    artifact_sha256: candidate.expected_build?.artifact_sha256 ?? null,
    artifact_mvid: candidate.expected_build?.artifact_mvid ?? null
  };
  if (!sameJson(actualCandidate, expectedCandidate)) errors.push("scenario_candidate_identity_mismatch");
  if (errors.length > 0) throw new Error(`Built-in scenario is incompatible: ${errors.join(", ")}`);
}

export function assertManagedScenarioDriver(definition, driver) {
  const expected = definition.candidate;
  const descriptor = driver?.descriptor;
  const implementation = descriptor?.implementation;
  if (descriptor?.driver_id !== expected.candidate_id
      || implementation?.source_revision !== expected.upstream_revision
      || implementation?.source_patch_sha256 !== expected.source_patch_sha256
      || implementation?.artifact_sha256 !== expected.artifact_sha256
      || !sameJson(descriptor?.semantic_target, definition.semantic_target)) {
    throw new Error("Built-in scenario driver identity does not match its reviewed target.");
  }
}

export async function createValidatedManagedScenarioDriver({
  definition,
  actualGame,
  semanticTarget,
  candidateManifest,
  createDriver
}) {
  assertManagedScenarioTarget({ definition, actualGame, semanticTarget, candidateManifest });
  if (typeof createDriver !== "function") throw new TypeError("createDriver must be a function.");
  const driver = await createDriver();
  assertManagedScenarioDriver(definition, driver);
  return driver;
}
