import assert from "node:assert/strict";
import test from "node:test";
import { createHostDriver } from "../src/host-driver.mjs";
import { runManagedRepeatability } from "../src/managed-repeatability.mjs";

const target = {
  schema: "sts2.headless/semantic-target-1",
  target_id: "target-v1",
  protocol_version: "1.0.0",
  game_build: { version: "v0.111.0", commit: "41cef1ea", main_assembly_hash: 123 },
  content_policy_id: "vanilla_singleplayer_v1",
  information_policy_id: "player_visible_v1"
};
const scenario = {
  schema: "sts2.headless/scenario-1",
  scenario_id: "map-prefix",
  seed: "SEED01",
  policy_id: "deterministic-probe-1",
  max_actions: 1
};

function actionEvent({ call, delivery = "delivered", decisionId = "decision-1" }) {
  const referentId = `referent-${call}`;
  return {
    type: "action",
    canonical_decision_digest: decisionId,
    canonical_decision: {
      information_policy: { id: "player_visible_v1" },
      referents: [{
        canonical_referent_id: referentId,
        role: "card",
        kind: "entity",
        label: "Defend",
        state: { visible: true },
        properties_schema: "card-1",
        properties: { entity_id: `native-card-${call}`, definition_id: "DEFEND", cost: "1" }
      }]
    },
    canonical_selected_action: {
      verb: "play_card",
      subject_referent_id: referentId,
      arguments: [],
      label: "Play Defend"
    },
    delivery,
    reason_code: null
  };
}

const normalEvents = [{ type: "read", kind: "map", canonical_read: { information_policy: { id: "player_visible_v1" }, value: "map-a" } }, actionEvent({ call: 1 })];

function fakeDriver({ events = normalEvents, eventsForRun = (call) => events.map((event) => event.type === "action"
  ? actionEvent({ call, delivery: event.delivery, decisionId: event.canonical_decision_digest })
  : event), invalidFirst = false, artifactForRun = () => "artifact-a", seedForRun = () => scenario.seed, runtimeIdForRun = (call) => `runtime-${call}`, reportForRun = () => ({}) } = {}) {
  let calls = 0;
  return {
    get calls() { return calls; },
    driver: createHostDriver({
      driverId: "managed-candidate-a",
      hostKind: "managed_exact",
      semanticTarget: target,
      implementation: { source_revision: "rev-a", artifact_sha256: "artifact-a" },
      runScenario: async () => {
        calls += 1;
        const artifact = artifactForRun(calls);
        const overrides = reportForRun(calls);
        const baseReport = {
          loaded_identity: {
            protocol: target.protocol_version,
            host: {
              host_kind: "managed_exact",
              runtime_instance_id: runtimeIdForRun(calls),
              implementation: {
                source_revision: "rev-a",
                artifact_sha256: artifact,
                module_version_id: "mvid-a"
              }
            },
            game: { version: "v0.111.0", commit: "41cef1ea", main_assembly_hash: 123 }
          },
          episode_provenance: {
            verdict: invalidFirst && calls === 1 ? "provenance_incomplete" : "provenance_pass",
            actual_seed: seedForRun(calls)
          },
          verdict: { integrity: { verdict: invalidFirst && calls === 1 ? "integrity_incomplete" : "integrity_pass", errors: [], unknown_deliveries: 0 } },
          candidate: {
            manifest: { candidate_id: "candidate-a", upstream: { revision: "rev-a" } },
            build: { artifact_sha256: artifact, source_patch_sha256: "patch-a" },
            adapter_environment_fingerprint: "env-a"
          }
        };
        return {
          report: { ...baseReport, ...overrides },
          events: eventsForRun(calls, baseReport)
        };
      }
    })
  };
}

test("same exact Managed candidate compares two independent seeded processes", async () => {
  const fake = fakeDriver();
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(fake.calls, 2);
  assert.equal(result.verdict, "managed_repeatability_pass");
  assert.equal(result.exact_candidate_artifact, true);
  assert.deepEqual(result.runtime_instance_ids, ["runtime-1", "runtime-2"]);
  assert.deepEqual(result.actual_seeds, ["SEED01", "SEED01"]);
});

test("equal empty trajectories cannot pass", async () => {
  const fake = fakeDriver({ events: [] });
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(result.verdict, "managed_repeatability_incomplete");
  assert.ok(result.errors.includes("two_complete_runs_not_available"));
  assert.equal(fake.calls, 1);
});

test("unknown or incomplete first run does not trigger an automatic retry", async () => {
  const fake = fakeDriver({ invalidFirst: true });
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(fake.calls, 1);
  assert.equal(result.verdict, "managed_repeatability_incomplete");
  assert.ok(result.errors.includes("run_evidence_incomplete"));
});

test("candidate artifact drift is rejected", async () => {
  const fake = fakeDriver({
    events: [{ type: "read", kind: "map", canonical_read: { information_policy: { id: "player_visible_v1" }, value: "map-a" } }],
    artifactForRun: (call) => call === 1 ? "artifact-a" : "artifact-b"
  });
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(result.verdict, "managed_repeatability_incomplete");
  assert.ok(result.errors.includes("exact_candidate_identity_changed"));
});

test("semantic divergence reports its first differing event", async () => {
  const fake = fakeDriver({ eventsForRun: (call) => [actionEvent({ call, decisionId: `decision-${call}` })] });
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(result.verdict, "managed_repeatability_mismatch");
  assert.equal(result.comparison.first_divergence.semantic_event_index, 0);
});

test("a read-only trajectory is insufficient even when both integrity reports claim pass", async () => {
  const readOnly = [{ type: "read", kind: "map", canonical_read: { information_policy: { id: "player_visible_v1" }, value: "map-a" } }];
  const fake = fakeDriver({ events: readOnly });
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(result.verdict, "managed_repeatability_incomplete");
  assert.ok(result.errors.includes("run_evidence_incomplete"));
  assert.equal(fake.calls, 1);
});

test("unknown delivery rejects an otherwise integrity_pass action trajectory", async () => {
  const fake = fakeDriver({
    events: [actionEvent({ call: 1, delivery: "unknown" })],
    reportForRun: () => ({ verdict: { integrity: { verdict: "integrity_pass", errors: [], unknown_deliveries: 0 } } })
  });
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(result.verdict, "managed_repeatability_incomplete");
  assert.ok(result.errors.includes("run_evidence_incomplete"));
  assert.equal(fake.calls, 1);
});

test("a nonzero unknown_deliveries count rejects delivered actions despite integrity_pass", async () => {
  const fake = fakeDriver({
    events: [actionEvent({ call: 1, delivery: "delivered" })],
    reportForRun: () => ({ verdict: { integrity: { verdict: "integrity_pass", errors: [], unknown_deliveries: 1 } } })
  });
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(result.verdict, "managed_repeatability_incomplete");
  assert.ok(result.errors.includes("run_evidence_incomplete"));
  assert.equal(fake.calls, 1);
});

test("matching semantics with fresh native entity and canonical referent IDs remain comparable", async () => {
  const fake = fakeDriver({ events: [actionEvent({ call: 1 })] });
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(result.verdict, "managed_repeatability_pass");
  assert.notEqual(result.runs[0].events[0].canonical_decision.referents[0].properties.entity_id,
    result.runs[1].events[0].canonical_decision.referents[0].properties.entity_id);
  assert.notEqual(result.runs[0].events[0].canonical_decision.referents[0].canonical_referent_id,
    result.runs[1].events[0].canonical_decision.referents[0].canonical_referent_id);
});

test("reused runtime instance IDs cannot qualify two runs", async () => {
  const fake = fakeDriver({ runtimeIdForRun: () => "same-runtime" });
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(result.verdict, "managed_repeatability_incomplete");
  assert.ok(result.errors.includes("runtime_instances_not_independent"));
});

test("seed drift and missing candidate identity are independently rejected", async () => {
  const changedSeed = fakeDriver({ seedForRun: (call) => call === 1 ? "SEED01" : "SEED02" });
  const seedResult = await runManagedRepeatability({ driver: changedSeed.driver, scenario });
  assert.equal(seedResult.verdict, "managed_repeatability_incomplete");
  assert.ok(seedResult.errors.includes("episode_seed_not_comparable"));

  const missingIdentity = fakeDriver({ reportForRun: () => ({ candidate: null }) });
  const identityResult = await runManagedRepeatability({ driver: missingIdentity.driver, scenario });
  assert.equal(identityResult.verdict, "managed_repeatability_incomplete");
  assert.ok(identityResult.errors.includes("exact_candidate_identity_incomplete"));
  assert.equal(identityResult.exact_candidate_artifact, false);
});
