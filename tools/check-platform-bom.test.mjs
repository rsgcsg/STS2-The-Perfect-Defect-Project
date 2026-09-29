import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { readBomAuthorities, validatePlatformBom } from "./check-platform-bom.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// This file mutates copies; the checkout is immutable for this test process.
const authoritySnapshot = await readBomAuthorities(root);

test("current Platform BOM agrees with component and package authorities", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  const errors = validatePlatformBom(bom, structuredClone(authoritySnapshot));
  assert.deepEqual(errors, []);
});

test("BOM check rejects component and public Connector pin drift", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  bom.components.annotator.version = "9.9.9";
  bom.public_packages.connector_host.sha256 = "0".repeat(64);
  const errors = validatePlatformBom(bom, structuredClone(authoritySnapshot));
  assert.ok(errors.some((error) => error.startsWith("annotator.version:")));
  assert.ok(errors.some((error) => error.startsWith("public Connector archive SHA:")));
});

test("Connector dependency is a strict SemVer minimum, not a current-version lockstep pin", () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  const authorities = structuredClone(authoritySnapshot);
  const dependencies = authorities.annotatorManifest.dependencies;
  const connector = dependencies.find(({ id }) => id === "STS2_MCP");
  const dependencyErrors = () => validatePlatformBom(bom, authorities).filter((error) =>
    error.startsWith("Annotator Connector dependency:"));

  assert.deepEqual(dependencyErrors(), []); // rc.5 is a valid minimum for rc.6.
  connector.min_version = "1.3.0-rc.7";
  assert.equal(dependencyErrors().length, 1);
  connector.min_version = "1.3.0-rc.10";
  assert.equal(dependencyErrors().length, 1); // Numeric prerelease order.
  connector.min_version = "1.3.0";
  assert.equal(dependencyErrors().length, 1); // Stable is later than rc.6.
  connector.min_version = "1.3.0-rc.5";
  authorities.connectorManifest.version = "1.3.0";
  assert.deepEqual(dependencyErrors(), []); // Stable satisfies its prerelease minimum.
  authorities.connectorManifest.version = "1.3.0-rc.6";
  for (const invalid of ["", "1.3", "1.3.0-rc.06", "1.3.0-rc.x", "1.3.0+bad..meta"]) {
    connector.min_version = invalid;
    assert.equal(dependencyErrors().length, 1, invalid);
  }
  connector.min_version = "1.3.0-rc.5";
  dependencies.push({ id: "STS2_MCP", min_version: "1.0.0" });
  assert.equal(dependencyErrors().length, 1); // Ambiguous duplicate is invalid.
  dependencies.pop();
  connector.min_version = "1.3.0-rc.5";
  authorities.connectorManifest.version = "1.3.0-rc.06";
  assert.equal(dependencyErrors().length, 1); // Selected version must also be valid.
  authorities.connectorManifest.version = "1.3.0-rc.6";
  dependencies.pop();
  assert.equal(dependencyErrors().length, 1); // Missing dependency is invalid.
});

test("BOM keeps historical policy evidence separate from the standalone package source", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  const policy = bom.unified_platform_runtime_candidate.policy_runtime;
  policy.source_revision = bom.components.policy_runtime.source_revision;
  policy.current_component_source_revision = "0".repeat(40);
  const errors = validatePlatformBom(bom, structuredClone(authoritySnapshot));
  assert.ok(errors.some((error) => error.startsWith("historical Policy Runtime source:")));
  assert.ok(errors.some((error) => error.startsWith("candidate current Policy Runtime source:")));
});

test("BOM check rejects human gate drift and machine-proven origin claims", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  bom.exact_runtime_candidate.gates.annotator_human.runtime_instance_id = "wrong-runtime";
  bom.exact_runtime_candidate.gates.annotator_human.human_origin = "machine_proven";
  const errors = validatePlatformBom(bom, structuredClone(authoritySnapshot));
  assert.ok(errors.some((error) => error.startsWith("human gate runtime:")));
  assert.ok(errors.some((error) => error.startsWith("human origin boundary:")));
});

test("BOM check separates the loaded V2 artifact from later operations-only source", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  bom.current_v2_candidate.connector.current_component_source_revision = "0".repeat(40);
  bom.current_v2_candidate.annotator.current_component_source_revision = "0".repeat(40);
  bom.current_v2_candidate.annotator.loaded = "pending";
  bom.current_v2_candidate.native_human_gate.status = "pass";
  const errors = validatePlatformBom(bom, structuredClone(authoritySnapshot));
  assert.ok(errors.some((error) => error.startsWith("V2 current Connector source:")));
  assert.ok(errors.some((error) => error.startsWith("V2 current Annotator source:")));
  assert.ok(errors.some((error) => error.startsWith("V2 annotator loaded:")));
  assert.ok(errors.some((error) => error.startsWith("V2 human gate:")));
});

test("BOM check rejects V2 evidence and selector-claim drift", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  bom.current_v2_candidate.native_human_gate.reads.run_deck = 59;
  bom.current_v2_candidate.native_human_gate.transfer.retry_status = "promoted";
  bom.current_v2_candidate.native_human_gate.generated_card_choice.runtime_status = "pass";
  bom.non_claims = bom.non_claims.filter(
    (claim) => claim !== "read_rich_v2_candidate_generated_card_choice_not_exercised"
  );
  const errors = validatePlatformBom(bom, structuredClone(authoritySnapshot));
  assert.ok(errors.some((error) => error.startsWith("V2 run-deck Reads:")));
  assert.ok(errors.some((error) => error.startsWith("V2 transfer retry:")));
  assert.ok(errors.some((error) => error.startsWith("V2 selector runtime:")));
  assert.ok(errors.includes("Read-rich V2 predecessor generated-card-choice non-claim is missing"));
});

test("BOM check keeps closed Human audit provenance independent from current source", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  const candidate = bom.unified_platform_runtime_candidate.native_semantic_discriminator_source_candidate;

  assert.notEqual(
    candidate.owner_canary.audit_closeout_source_revision,
    bom.components.annotator.source_revision
  );
  assert.deepEqual(validatePlatformBom(bom, structuredClone(authoritySnapshot)), []);

  candidate.owner_canary.audit_closeout_source_revision = "0".repeat(40);
  const errors = validatePlatformBom(bom, structuredClone(authoritySnapshot));
  assert.ok(errors.some((error) => error.startsWith("native discriminator audit source:")));
});

test("BOM check rejects Windows Native Foundation identity and claim drift", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  const candidate =
    bom.unified_platform_runtime_candidate.native_foundation_windows_runtime_candidate;
  candidate.game.main_assembly_sha256 = "0".repeat(64);
  candidate.artifact_mvid = "00000000-0000-0000-0000-000000000000";
  candidate.loaded_mod_ids = ["STS2_PLATFORM", "STS2-RitsuLib"];
  candidate.automated_live_ui.reads = "filtered";
  candidate.automated_live_ui.action_delivered = true;
  candidate.final_takeover.loaded_mod_ids = ["STS2_PLATFORM", "CombatSolver"];
  candidate.final_takeover.evidence_level = "human_pass";
  candidate.final_takeover.disabled_workshop_update.loaded = true;
  candidate.recorder_lifecycle.owner_new_pause_resume_close = "pending_human_runtime";
  candidate.human_closeout.native_unknown = 1;
  candidate.human_closeout.loaded_mod_ids = ["STS2_PLATFORM", "CombatSolver"];
  candidate.visible_headless_semantic_invariance.scope = "full_run";
  candidate.human_runtime = "pass";
  candidate.evidence_transfer_from_predecessor = true;
  bom.non_claims = bom.non_claims.filter(
    (claim) => claim !== "native_foundation_windows_human_not_full_run"
  );
  const errors = validatePlatformBom(bom, structuredClone(authoritySnapshot));
  assert.ok(errors.some((error) =>
    error.startsWith("Windows Native Foundation main assembly:")));
  assert.ok(errors.some((error) => error.startsWith("Windows Native Foundation artifact MVID:")));
  assert.ok(errors.includes("Windows Native Foundation loaded Mods: expected only STS2_PLATFORM"));
  assert.ok(errors.some((error) => error.startsWith("Windows Native Foundation live reads:")));
  assert.ok(errors.some((error) => error.startsWith("Windows Native Foundation live mutation:")));
  assert.ok(errors.includes(
    "Windows Native Foundation final takeover loaded Mods: expected only STS2_PLATFORM"
  ));
  assert.ok(errors.some((error) =>
    error.startsWith("Windows Native Foundation final takeover evidence_level:")));
  assert.ok(errors.some((error) =>
    error.startsWith("Windows Native Foundation disabled Workshop update loaded:")));
  assert.ok(errors.some((error) =>
    error.startsWith("Windows Native Foundation Recorder owner lifecycle:")));
  assert.ok(errors.some((error) =>
    error.startsWith("Windows Native Foundation Human native_unknown:")));
  assert.ok(errors.includes(
    "Windows Native Foundation Human loaded Mods: expected only STS2_PLATFORM"
  ));
  assert.ok(errors.some((error) => error.startsWith("Windows Native Foundation parity scope:")));
  assert.ok(errors.some((error) => error.startsWith("Windows Native Foundation human_runtime:")));
  assert.ok(errors.some((error) =>
    error.startsWith("Windows Native Foundation predecessor evidence transfer:")));
  assert.ok(errors.includes(
    "Native Foundation non-claim is missing: "
      + "native_foundation_windows_human_not_full_run"
  ));
});

test("BOM check rejects unified artifact, identity and evidence promotion", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  const candidate = bom.unified_platform_runtime_candidate;
  candidate.live_ui.current_component_source_revision = "0".repeat(40);
  candidate.live_ui.artifact_sha256 = "0".repeat(64);
  candidate.connector.current_component_source_revision = "0".repeat(40);
  candidate.annotator.source_relation = "loaded_native_source_scope_matches_current_component";
  candidate.semantic_timeline_source_candidate.loaded = "non_claim";
  candidate.semantic_timeline_source_candidate.owner_canary.semantic_proved = 18;
  candidate.semantic_timeline_source_candidate.evidence_transfer_from_predecessor = true;
  candidate.full_run_semantic_source_candidate.annotator_source_revision = "0".repeat(40);
  candidate.full_run_semantic_source_candidate.workspace_revision_at_build = "0".repeat(40);
  candidate.full_run_semantic_source_candidate.loaded = "non_claim";
  candidate.full_run_semantic_source_candidate.owner_canary.semantic_proved = 332;
  candidate.full_run_semantic_source_candidate.owner_canary.performance_profile = "pass";
  candidate.full_run_semantic_source_candidate.evidence_transfer_from_schema2_predecessor = true;
  candidate.full_run_semantic_source_candidate.predecessor_missing_semantic_native_roots = 0;
  candidate.game_mod.installed = "pending";
  candidate.runtime.execution_available = false;
  candidate.owner_ui_visibility = "pass";
  candidate.rapid_input_ledger_v1_owner_validation.valid_records = 13;
  candidate.rapid_input_ledger_v1_owner_validation.evidence_transfer_to_ledger_v2 = true;
  candidate.rapid_input_ledger_v2_loaded_candidate.owner_rapid_input = "pass";
  candidate.rapid_input_ledger_v2_loaded_candidate.evidence_transfer_from_ledger_v1 = true;
  candidate.semantic_execution_order_loaded_candidate.artifact_sha256 = "1".repeat(64);
  candidate.semantic_execution_order_loaded_candidate.owner_semantic_execution_order = "pass";
  candidate.semantic_execution_order_loaded_candidate.owner_canary.semantic_proved = 25;
  candidate.semantic_execution_order_loaded_candidate.owner_canary.exact_reorder_rebind = "pass";
  candidate.semantic_execution_order_loaded_candidate.evidence_transfer_from_predecessor = true;
  candidate.serialized_canonical_loaded_candidate.human_runtime = "pass";
  candidate.serialized_canonical_loaded_candidate.host_automation.same_artifact_prefix_9 =
    "semantic_mismatch";
  candidate.serialized_canonical_loaded_candidate.evidence_transfer_from_predecessor = true;
  candidate.native_semantic_discriminator_source_candidate.human_runtime = "pending";
  candidate.native_semantic_discriminator_source_candidate.owner_canary.native_successful = 40;
  candidate.native_semantic_discriminator_source_candidate.owner_canary.route_verdict =
    "PRACTICALLY_IMPOSSIBLE_TRIANGLE";
  candidate.native_semantic_discriminator_source_candidate.evidence_transfer_from_predecessor = true;
  candidate.native_foundation_runtime_candidate.human_runtime = "pass";
  candidate.native_foundation_runtime_candidate.visible_headless_semantic_invariance.scope =
    "full_run";
  candidate.native_foundation_runtime_candidate.evidence_transfer_from_predecessor = true;
  candidate.recording_application_decision_gate.human_origin = "machine_proven";
  candidate.predecessor_human_session.evidence_transfer_to_unified_artifact = true;
  candidate.external_policy.checkpoint_status = "present";
  bom.non_claims = bom.non_claims.filter(
    (claim) => claim !== "s1_checkpoint_absent_shadow_one_step_auto_not_exercised"
  );
  bom.non_claims = bom.non_claims.filter(
    (claim) => claim !==
      "native_rejected_cancelled_attempt_absence_owner_attested_not_machine_attributable"
  );
  bom.non_claims = bom.non_claims.filter(
    (claim) => claim !== "serialized_canonical_candidate_human_runtime_not_exercised"
  );
  bom.non_claims = bom.non_claims.filter(
    (claim) => claim !== "native_semantic_discriminator_cancel_abort_not_exercised"
  );
  bom.non_claims = bom.non_claims.filter(
    (claim) => claim !== "native_foundation_candidate_human_runtime_not_exercised"
  );
  bom.non_claims = bom.non_claims.filter(
    (claim) => claim !== "native_foundation_visible_headless_parity_main_menu_only"
  );
  bom.non_claims.push("native_semantic_discriminator_human_runtime_pending");
  bom.non_claims.push("semantic_execution_order_exact_rebind_not_exercised");
  const errors = validatePlatformBom(bom, structuredClone(authoritySnapshot));
  assert.ok(errors.some((error) => error.startsWith("candidate current Live UI source:")));
  assert.ok(errors.some((error) => error.startsWith("candidate common artifact SHA (live_ui):")));
  assert.ok(errors.some((error) => error.startsWith("candidate current Connector source:")));
  assert.ok(errors.some((error) => error.startsWith("candidate Annotator source relation:")));
  assert.ok(errors.some((error) => error.startsWith("semantic timeline loaded:")));
  assert.ok(errors.some((error) => error.startsWith("semantic timeline semantic proved:")));
  assert.ok(errors.some((error) =>
    error.startsWith("semantic timeline predecessor evidence transfer:")));
  assert.ok(errors.some((error) => error.startsWith("Full-Run workspace at build:")));
  assert.ok(errors.some((error) => error.startsWith("Full-Run loaded:")));
  assert.ok(errors.some((error) => error.startsWith("schema-3 proved:")));
  assert.ok(errors.some((error) => error.startsWith("schema-3 performance profile:")));
  assert.ok(errors.some((error) => error.startsWith("Full-Run predecessor evidence transfer:")));
  assert.ok(errors.some((error) => error.startsWith("Full-Run predecessor missing semantic roots:")));
  assert.ok(errors.some((error) => error.startsWith("candidate Game Mod installed:")));
  assert.ok(errors.some((error) => error.startsWith("candidate execution:")));
  assert.ok(errors.some((error) => error.startsWith("candidate owner UI visibility:")));
  assert.ok(errors.some((error) => error.startsWith("rapid ledger valid records:")));
  assert.ok(errors.some((error) => error.startsWith("rapid ledger evidence transfer:")));
  assert.ok(errors.some((error) => error.startsWith("rapid ledger v2 owner canary:")));
  assert.ok(errors.some((error) => error.startsWith("rapid ledger v2 evidence transfer:")));
  assert.ok(errors.some((error) => error.startsWith("semantic execution candidate artifact:")));
  assert.ok(errors.some((error) => error.startsWith("semantic execution candidate owner canary:")));
  assert.ok(errors.some((error) => error.startsWith("semantic execution proved:")));
  assert.ok(errors.some((error) => error.startsWith("semantic execution exact reorder claim:")));
  assert.ok(errors.some((error) => error.startsWith("semantic execution candidate evidence transfer:")));
  assert.ok(errors.some((error) => error.startsWith("serialized candidate Human runtime:")));
  assert.ok(errors.some((error) => error.startsWith("serialized candidate predecessor transfer:")));
  assert.ok(errors.some((error) => error.startsWith("native discriminator Human runtime:")));
  assert.ok(errors.some((error) =>
    error.startsWith("native discriminator Human native_successful:")));
  assert.ok(errors.some((error) => error.startsWith("native discriminator route verdict:")));
  assert.ok(errors.some((error) => error.startsWith("native discriminator predecessor transfer:")));
  assert.ok(errors.some((error) => error.startsWith("Native Foundation Human runtime:")));
  assert.ok(errors.some((error) => error.startsWith("Native Foundation parity scope:")));
  assert.ok(errors.some((error) =>
    error.startsWith("Native Foundation predecessor evidence transfer:")));
  assert.ok(errors.some((error) => error.startsWith("accepted-only Human origin:")));
  assert.ok(errors.some((error) => error.startsWith("predecessor evidence transfer:")));
  assert.ok(errors.some((error) => error.startsWith("candidate policy checkpoint:")));
  assert.ok(errors.includes("S1 checkpoint/model-mode non-claim is missing"));
  assert.ok(errors.includes("Native-rejected attempt attribution non-claim is missing"));
  assert.ok(errors.includes("Serialized canonical Human-runtime non-claim is missing"));
  assert.ok(errors.includes(
    "Bounded Human-proved native discriminator retains a stale pending non-claim"));
  assert.ok(errors.includes(
    "Native semantic discriminator non-claim is missing: "
      + "native_semantic_discriminator_cancel_abort_not_exercised"));
  assert.ok(errors.includes(
    "Native Foundation non-claim is missing: native_foundation_candidate_human_runtime_not_exercised"
  ));
  assert.ok(errors.includes(
    "Native Foundation non-claim is missing: native_foundation_visible_headless_parity_main_menu_only"
  ));
  assert.ok(errors.includes(
    "Live-proved semantic execution-order rebind retains a stale non-claim"
  ));
});


test("final Full-Run candidate cannot borrow historical identity or Human qualification", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  assert.ok(bom.final_full_run_candidate);
  bom.final_full_run_candidate.components.annotator.source_revision = "0".repeat(40);
  bom.final_full_run_candidate.human_gate = "pass";
  // Explicitly model a fabricated PASS; the live BOM may legitimately contain
  // a completed Human gate after final qualification.
  delete bom.final_full_run_candidate.human_evidence;
  bom.final_full_run_candidate.evidence_transfer_from_predecessor = true;
  const errors = validatePlatformBom(bom, structuredClone(authoritySnapshot));
  assert.ok(errors.some(error => error.startsWith("Final Full-Run annotator.source_revision:")));
  assert.ok(errors.some(error => error.startsWith("Final Full-Run Human audit:")));
  assert.ok(errors.some(error => error.startsWith("Final Full-Run predecessor transfer:")));
});

test("portable verifier changes do not relabel closed native Human evidence", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  assert.deepEqual(validatePlatformBom(bom, structuredClone(authoritySnapshot)), []);
  bom.final_full_run_candidate.components.evidence.source_revision = "f".repeat(40);
  assert.ok(validatePlatformBom(bom, structuredClone(authoritySnapshot))
    .some(error => error.startsWith("Final Full-Run historical evidence source_revision:")));
});

test("new Live UI consumer source cannot relabel the recorded Human-tested native build", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  const authorities = structuredClone(authoritySnapshot);
  assert.deepEqual(validatePlatformBom(bom, authorities), []);
  bom.final_full_run_candidate.components.live_ui.source_revision = "f".repeat(40);
  assert.ok(validatePlatformBom(bom, authorities).some(error => error.startsWith("Final Full-Run live_ui.source_revision:")));
});

test("collection setup source and version updates cannot relabel the recorded Human artifact", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  const authorities = structuredClone(authoritySnapshot);
  assert.deepEqual(validatePlatformBom(bom, authorities), []);
  for (const key of ["annotator", "game_mod"]) {
    const mutated = structuredClone(bom);
    mutated.final_full_run_candidate.components[key].source_revision = "f".repeat(40);
    assert.ok(validatePlatformBom(mutated, authorities).some(error => error.startsWith(`Final Full-Run ${key}.source_revision:`)));
  }
});

test("published Host pin is separate from a newer local source version", async () => {
  const bom = JSON.parse(fs.readFileSync(path.join(root, "platform-bom.json"), "utf8"));
  const authorities = structuredClone(authoritySnapshot);
  assert.deepEqual(validatePlatformBom(bom, authorities), []);
  const published = structuredClone(bom.public_packages.host_runtime);
  // Model both a later source version and a legitimate future version sync.
  for (const version of ["9.9.9-fixture", published.version]) {
    const fixture = structuredClone(bom);
    const source = structuredClone(authorities);
    fixture.components.host_runtime.version = version;
    source.hostPackage.version = version;
    source.identities.components["host-runtime"].component_version = version;
    fixture.public_packages.host_runtime.source_relation = version === published.version
      ? "same_component_version" : "published_package_precedes_current_source";
    assert.deepEqual(validatePlatformBom(fixture, source), []);
    assert.equal(fixture.public_packages.host_runtime.sha256, published.sha256);
  }
  Object.assign(bom.public_packages.host_runtime, { version: "9.9.9-fixture",
    release: "host-runtime/v9.9.9-fixture", asset: "rsgcsg-sts2-host-runtime-9.9.9-fixture.tgz" });
  assert.ok(validatePlatformBom(bom, authorities).some(error => error.startsWith("published Host version:")));
  bom.public_packages.host_runtime.sha256 = "f".repeat(64);
  assert.ok(validatePlatformBom(bom, authorities).some(error => error.startsWith("published Host archive SHA:")));
});
