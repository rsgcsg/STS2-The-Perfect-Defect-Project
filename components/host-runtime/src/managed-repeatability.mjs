import { runHostScenario } from "./host-driver.mjs";
import { compareCrossHostTrajectories } from "./semantic-differential.mjs";

function exactCandidateIdentity(report) {
  const manifest = report?.candidate?.manifest;
  const build = report?.candidate?.build;
  const host = report?.loaded_identity?.host;
  const game = report?.loaded_identity?.game;
  return {
    candidate_id: manifest?.candidate_id ?? null,
    upstream_revision: manifest?.upstream?.revision ?? null,
    source_patch_sha256: build?.source_patch_sha256 ?? null,
    artifact_sha256: build?.artifact_sha256 ?? null,
    runtime_artifact_sha256: host?.implementation?.artifact_sha256 ?? null,
    runtime_module_version_id: host?.implementation?.module_version_id ?? null,
    game_version: game?.version ?? null,
    game_commit: game?.commit ?? null,
    game_main_assembly_hash: game?.main_assembly_hash ?? null,
    adapter_environment_fingerprint: report?.candidate?.adapter_environment_fingerprint ?? null
  };
}

function identityErrors(first, second) {
  const fields = Object.keys(first);
  const errors = [];
  if (fields.some((field) => first[field] == null || second[field] == null)) {
    errors.push("exact_candidate_identity_incomplete");
  }
  if (JSON.stringify(first) !== JSON.stringify(second)) errors.push("exact_candidate_identity_changed");
  if (first.artifact_sha256 !== first.runtime_artifact_sha256
      || second.artifact_sha256 !== second.runtime_artifact_sha256) {
    errors.push("loaded_runtime_artifact_does_not_match_candidate");
  }
  return errors;
}

function complete(run) {
  return run?.report?.verdict?.integrity?.verdict === "integrity_pass"
    && run?.report?.episode_provenance?.verdict === "provenance_pass"
    && run?.report?.episode_provenance?.actual_seed === run?.scenario?.seed
    && run?.events?.some((event) => event?.type === "action" || event?.type === "read");
}

export async function runManagedRepeatability({ driver, scenario }) {
  const runs = [];
  let runError = null;
  for (let index = 0; index < 2; index += 1) {
    try {
      const run = await runHostScenario(driver, scenario);
      runs.push(run);
      if (!complete(run)) break;
    } catch (error) {
      runError = error instanceof Error ? error.message : String(error);
      break;
    }
  }

  const firstIdentity = exactCandidateIdentity(runs[0]?.report);
  const secondIdentity = exactCandidateIdentity(runs[1]?.report);
  const identityFailure = identityErrors(firstIdentity, secondIdentity);
  const canCompare = runs.length === 2;
  const comparison = canCompare
    ? compareCrossHostTrajectories({ referenceRun: runs[0], candidateRun: runs[1] })
    : null;
  const errors = [
    ...identityFailure,
    ...(runs.length !== 2 ? ["two_complete_runs_not_available"] : []),
    ...(runs.some((run) => !complete(run)) ? ["run_evidence_incomplete"] : []),
    ...(runs.length === 2 && runs[0].report.loaded_identity?.host?.runtime_instance_id
      === runs[1].report.loaded_identity?.host?.runtime_instance_id
      ? ["runtime_instances_not_independent"] : []),
    ...(comparison?.errors ?? []),
    ...(runError == null ? [] : [`run_error:${runError}`])
  ];
  const uniqueErrors = [...new Set(errors)];
  const matched = canCompare && uniqueErrors.length === 0
    && comparison.verdict === "cross_host_semantic_match";
  return {
    schema: "sts2.headless/managed-repeatability-1",
    verdict: matched ? "managed_repeatability_pass"
      : comparison?.first_divergence != null ? "managed_repeatability_mismatch"
        : "managed_repeatability_incomplete",
    errors: uniqueErrors,
    exact_candidate_artifact: canCompare && identityFailure.length === 0,
    candidate_identity: firstIdentity,
    runtime_instance_ids: runs.map((run) => run.report.loaded_identity?.host?.runtime_instance_id ?? null),
    actual_seeds: runs.map((run) => run.report.episode_provenance?.actual_seed ?? null),
    runs,
    comparison: comparison == null ? null : {
      verdict: comparison.verdict,
      errors: comparison.errors,
      semantic_event_count: comparison.reference_semantic_event_count,
      first_divergence: comparison.first_divergence
    },
    non_claims: [
      "This compares two bounded runs on one exact Managed candidate; it does not qualify Connector text-menu coverage or full-game determinism.",
      "Synthetic driver tests validate comparison logic only and do not qualify a native Managed runtime."
    ]
  };
}
