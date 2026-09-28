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

function fakeDriver({ events = [{ type: "read", kind: "map", canonical_read: { information_policy: { id: "player_visible_v1" }, value: "map-a" } }], eventsForRun = () => events, invalidFirst = false, artifactForRun = () => "artifact-a" } = {}) {
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
        return {
          report: {
            loaded_identity: {
              protocol: target.protocol_version,
              host: {
                host_kind: "managed_exact",
                runtime_instance_id: `runtime-${calls}`,
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
              actual_seed: scenario.seed
            },
            verdict: { integrity: { verdict: invalidFirst && calls === 1 ? "integrity_incomplete" : "integrity_pass" } },
            candidate: {
              manifest: { candidate_id: "candidate-a", upstream: { revision: "rev-a" } },
              build: { artifact_sha256: artifact, source_patch_sha256: "patch-a" },
              adapter_environment_fingerprint: "env-a"
            }
          },
          events: eventsForRun(calls)
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
  const fake = fakeDriver({ eventsForRun: (call) => [{
    type: "read",
    kind: "map",
    canonical_read: { information_policy: { id: "player_visible_v1" }, value: call === 1 ? "map-a" : "map-b" }
  }] });
  const result = await runManagedRepeatability({ driver: fake.driver, scenario });
  assert.equal(result.verdict, "managed_repeatability_mismatch");
  assert.equal(result.comparison.first_divergence.semantic_event_index, 0);
});
