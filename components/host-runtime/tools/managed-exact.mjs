#!/usr/bin/env node
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseWorkerCounts } from "../src/capacity-benchmark.mjs";
import { discoverGameDirectory, readDiskIdentity, resolveInstallation } from "../src/game-installation.mjs";
import {
  inspectManagedCandidateBuild,
  loadManagedCandidateManifest,
  selectManagedCandidateManifest,
  prepareManagedCandidate,
  runManagedCandidateCapacity,
  runManagedCandidateProbe
} from "../src/managed-candidate.mjs";
import {
  runManagedPlayerEnvironmentCapacity,
  runManagedPlayerEnvironmentProbe
} from "../src/managed-player-environment-probe.mjs";
import {
  managedPerformanceProfile,
  runManagedEngineBenchmark
} from "../src/managed-performance-lab.mjs";
import { runManagedPlayerEnvironmentShardedCapacity } from "../src/managed-sharded-capacity.mjs";
import { runManagedNativeBindingGates } from "../src/managed-native-binding-gates.mjs";
import { runManagedRecoveryProbe } from "../src/managed-recovery-probe.mjs";
import { canonicalizeEpisodeSeed } from "../src/episode-provenance.mjs";
import {
  createManagedExactHostDriver,
  createShippedReferenceHostDriver
} from "../src/cross-host-driver.mjs";
import { runCrossHostDifferential } from "../src/semantic-differential.mjs";
import { runManagedRepeatability } from "../src/managed-repeatability.mjs";
import { validateScenarioDescriptor } from "../src/host-driver.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LOCAL = path.join(ROOT, ".local");

function option(args, name, fallback = null) {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
}

function commaList(value) {
  return value == null ? [] : value.split(",").map((item) => item.trim()).filter(Boolean);
}

function diskIdentity() {
  const gameDirectory = discoverGameDirectory();
  if (!gameDirectory) throw new Error("Could not locate STS2; set STS2_GAME_DIR.");
  return readDiskIdentity(resolveInstallation(gameDirectory));
}

function safeTimestamp() {
  return new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-");
}

async function main() {
  const [command = "help", ...args] = process.argv.slice(2);
  if (command === "prepare") {
    const result = await prepareManagedCandidate({
      root: ROOT,
      localRoot: LOCAL,
      diskIdentity: diskIdentity(),
      candidateDirectory: option(args, "--candidate")
    });
    console.log(JSON.stringify({
      status: result.report.status,
      candidate_directory: result.candidateDirectory,
      report_file: result.reportFile,
      build: result.report.build
    }, null, 2));
    return;
  }
  const candidateDirectory = option(args, "--candidate");
  if (["audit", "probe", "pe-probe", "pe-profile", "pe-capacity", "pe-sharded-capacity", "engine-lab", "native-gates", "recovery", "capacity", "cross-host", "repeatability"].includes(command)
      && !candidateDirectory) {
    throw new Error(`${command} requires --candidate <prepared-directory>.`);
  }
  if (command === "audit") {
    const { manifest: loadedManifest } = loadManagedCandidateManifest(ROOT);
    const manifest = selectManagedCandidateManifest(loadedManifest, diskIdentity());
    console.log(JSON.stringify(await inspectManagedCandidateBuild({
      root: ROOT,
      candidateDirectory,
      manifest
    }), null, 2));
    return;
  }
  if (command === "probe") {
    const result = await runManagedCandidateProbe({
      root: ROOT,
      candidateDirectory,
      diskIdentity: diskIdentity(),
      seed: option(args, "--seed", "H1MANAGEDPROBE01"),
      character: option(args, "--character", "Ironclad"),
      maxActions: Number(option(args, "--max-actions", "200")),
      episodeCount: Number(option(args, "--episodes", "1")),
      resetAtDecisions: commaList(option(args, "--reset-at")),
      requestTimeoutMs: Number(option(args, "--timeout-ms", "10000")),
      evidenceRoot: path.join(LOCAL, "evidence")
    });
    console.log(JSON.stringify({
      status: result.report.status,
      report_file: result.reportFile,
      episode: result.report.episode,
      performance: result.report.performance
    }, null, 2));
    process.exitCode = ["episodes_complete", "terminal_reached"].includes(result.report.status) ? 0 : 2;
    return;
  }
  if (command === "pe-probe") {
    const result = await runManagedPlayerEnvironmentProbe({
      root: ROOT,
      candidateDirectory,
      diskIdentity: diskIdentity(),
      seed: option(args, "--seed", "H1MANAGEDPE01"),
      character: option(args, "--character", "Ironclad"),
      maxActions: Number(option(args, "--max-actions", "200")),
      episodeCount: Number(option(args, "--episodes", "1")),
      requestTimeoutMs: Number(option(args, "--timeout-ms", "10000")),
      evidenceRoot: path.join(LOCAL, "evidence"),
      repeatSeed: args.includes("--repeat-seed"),
      verifyResetAuthority: args.includes("--verify-reset-authority"),
      verifyIdempotency: args.includes("--verify-idempotency")
    });
    console.log(JSON.stringify({
      status: result.report.status,
      report_file: result.reportFile,
      episode: result.report.episode,
      performance: result.report.performance
    }, null, 2));
    process.exitCode = result.report.status === "candidate_failure" ? 3 : 0;
    return;
  }
  if (command === "pe-profile") {
    const profile = managedPerformanceProfile(option(args, "--profile", "training"));
    const result = await runManagedPlayerEnvironmentProbe({
      root: ROOT,
      candidateDirectory,
      diskIdentity: diskIdentity(),
      seed: option(args, "--seed", "H1MANAGEDPROFILE01"),
      character: option(args, "--character", "Ironclad"),
      maxActions: Number(option(args, "--max-actions", "600")),
      episodeCount: Number(option(args, "--episodes", "5")),
      requestTimeoutMs: Number(option(args, "--timeout-ms", "10000")),
      evidenceRoot: path.join(LOCAL, "evidence"),
      ...profile
    });
    console.log(JSON.stringify({
      status: result.report.status,
      report_file: result.reportFile,
      profile: result.report.performance.profile,
      decisions_per_second: result.report.performance.delivered_decisions_per_second,
      decisions_per_cpu_second: result.report.performance.child_process.cpu_ms
        + result.report.performance.node_process.cpu_ms > 0
        ? result.report.episode.canonical_actions_delivered
          / ((result.report.performance.child_process.cpu_ms
            + result.report.performance.node_process.cpu_ms) / 1000)
        : null,
      stage_totals: result.report.performance.stage_totals
    }, null, 2));
    process.exitCode = result.report.status === "candidate_failure" ? 3 : 0;
    return;
  }
  if (command === "recovery") {
    const result = await runManagedRecoveryProbe({
      root: ROOT,
      candidateDirectory,
      diskIdentity: diskIdentity(),
      seed: option(args, "--seed", "H1MANAGEDRECOVERY01"),
      character: option(args, "--character", "Ironclad"),
      requestTimeoutMs: Number(option(args, "--timeout-ms", "10000")),
      evidenceRoot: path.join(LOCAL, "evidence")
    });
    console.log(JSON.stringify({
      status: result.report.status,
      report_file: result.reportFile,
      gates: result.report.gates,
      failure: result.report.failure
    }, null, 2));
    process.exitCode = result.report.status === "managed_recovery_pass" ? 0 : 3;
    return;
  }
  if (command === "engine-lab") {
    const result = await runManagedEngineBenchmark({
      root: ROOT,
      candidateDirectory,
      diskIdentity: diskIdentity(),
      episodes: Number(option(args, "--episodes", "5")),
      warmupEpisodes: Number(option(args, "--warmup-episodes", "1")),
      maxActions: Number(option(args, "--max-actions", "600")),
      seedPrefix: option(args, "--seed-prefix", "H1ENGINE"),
      character: option(args, "--character", "Ironclad"),
      serializeEachDecision: args.includes("--serialize-each-decision"),
      requestTimeoutMs: Number(option(args, "--timeout-ms", "120000")),
      evidenceRoot: path.join(LOCAL, "evidence")
    });
    console.log(JSON.stringify({
      status: result.report.status,
      report_file: result.reportFile,
      benchmark: result.report.benchmark
    }, null, 2));
    process.exitCode = result.report.status === "measured" ? 0 : 3;
    return;
  }
  if (command === "pe-capacity") {
    const profile = managedPerformanceProfile(option(args, "--profile", "qualification"));
    const result = await runManagedPlayerEnvironmentCapacity({
      root: ROOT,
      candidateDirectory,
      diskIdentity: diskIdentity(),
      workerCounts: parseWorkerCounts(option(args, "--workers", "1,2,4")),
      maxActions: Number(option(args, "--max-actions", "300")),
      episodesPerWorker: Number(option(args, "--episodes", "3")),
      seedPrefix: option(args, "--seed-prefix", "H1PECAPACITY"),
      character: option(args, "--character", "Ironclad"),
      requestTimeoutMs: Number(option(args, "--timeout-ms", "10000")),
      evidenceRoot: path.join(LOCAL, "evidence"),
      ...profile
    });
    console.log(JSON.stringify({
      status: result.report.status,
      report_file: result.reportFile,
      groups: result.report.groups.map((group) => ({
        worker_count: group.worker_count,
        status: group.status,
        canonical_decisions_per_second:
          group.aggregate_reset_inclusive_canonical_decisions_per_second,
        summed_worker_peak_rss_bytes: group.summed_worker_peak_rss_bytes
      }))
    }, null, 2));
    process.exitCode = [
      "measured_canonical_unqualified",
      "measured_canonical_partial_unqualified",
      "measured_training_profile_unqualified"
    ].includes(result.report.status) ? 0 : 3;
    return;
  }
  if (command === "pe-sharded-capacity") {
    const profile = managedPerformanceProfile(option(args, "--profile", "training"));
    const result = await runManagedPlayerEnvironmentShardedCapacity({
      root: ROOT,
      candidateDirectory,
      gameDirectory: discoverGameDirectory(),
      workerCounts: parseWorkerCounts(option(args, "--workers", "1,2,4")),
      maxActions: Number(option(args, "--max-actions", "600")),
      episodesPerWorker: Number(option(args, "--episodes", "5")),
      seedPrefix: option(args, "--seed-prefix", "H1PESHARDED"),
      character: option(args, "--character", "Ironclad"),
      requestTimeoutMs: Number(option(args, "--timeout-ms", "10000")),
      evidenceRoot: path.join(LOCAL, "evidence"),
      profile
    });
    console.log(JSON.stringify({
      status: result.report.status,
      report_file: result.reportFile,
      groups: result.report.groups.map((group) => ({
        worker_count: group.worker_count,
        decisions_per_second: group.aggregate_reset_inclusive_canonical_decisions_per_second,
        decisions_per_cpu_second: group.decisions_per_cpu_second,
        average_measured_cpu_cores: group.average_measured_cpu_cores
      }))
    }, null, 2));
    process.exitCode = [
      "measured_canonical_unqualified",
      "measured_canonical_partial_unqualified",
      "measured_training_profile_unqualified"
    ].includes(result.report.status) ? 0 : 3;
    return;
  }
  if (command === "native-gates") {
    const result = await runManagedNativeBindingGates({
      root: ROOT,
      candidateDirectory,
      diskIdentity: diskIdentity(),
      seed: option(args, "--seed", "H1NATIVEBINDING01"),
      requestTimeoutMs: Number(option(args, "--timeout-ms", "10000")),
      evidenceRoot: path.join(LOCAL, "evidence")
    });
    console.log(JSON.stringify({
      status: result.report.status,
      report_file: result.reportFile,
      gates: result.report.scenario.gates,
      failure: result.report.scenario.failure
    }, null, 2));
    process.exitCode = result.report.status === "pass" ? 0 : 3;
    return;
  }
  if (command === "cross-host") {
    const exactGame = diskIdentity();
    const semanticTarget = {
      schema: "sts2.headless/semantic-target-1",
      target_id: "sts2-v0.111.0-player-visible-zhs-v1",
      protocol_version: "1.0.0",
      game_build: {
        version: exactGame.release.version,
        commit: exactGame.release.commit,
        main_assembly_hash: exactGame.runtime_main_assembly_hash
      },
      content_policy_id: "vanilla_singleplayer_v1",
      information_policy_id: "player_visible_v1",
      presentation_language: option(args, "--language", "zhs")
    };
    const scenario = {
      schema: "sts2.headless/scenario-1",
      scenario_id: option(args, "--scenario-id", "first-map-prefix-v1"),
      seed: canonicalizeEpisodeSeed(option(args, "--seed", "H1CROSSHOST01")),
      policy_id: "deterministic-probe-1",
      max_actions: Number(option(args, "--max-actions", "12")),
      discovery_max_actions: Number(option(args, "--discovery-actions", "64")),
      start_interaction_kind: option(args, "--start-kind", "map_navigation"),
      read_policy: "none"
    };
    const referenceDriver = createShippedReferenceHostDriver({
      installation: resolveInstallation(discoverGameDirectory()),
      localRoot: LOCAL,
      evidenceRoot: path.join(LOCAL, "evidence"),
      semanticTarget,
      templateId: option(args, "--template", "vanilla-clean"),
      endpoint: option(args, "--endpoint", "http://127.0.0.1:15820"),
      timeoutMs: Number(option(args, "--reference-timeout-ms", "90000")),
      actionTimeoutMs: Number(option(args, "--action-timeout-ms", "20000")),
      experimentalBuildAcknowledged: args.includes("--experimental-build")
    });
    const managedDriver = await createManagedExactHostDriver({
      root: ROOT,
      candidateDirectory,
      diskIdentity: exactGame,
      semanticTarget,
      character: option(args, "--character", "Ironclad"),
      requestTimeoutMs: Number(option(args, "--timeout-ms", "10000"))
    });
    const result = await runCrossHostDifferential({
      referenceDriver,
      candidateDriver: managedDriver,
      scenario
    });
    const directory = path.join(LOCAL, "evidence", `managed-cross-host-${safeTimestamp()}`);
    mkdirSync(directory, { recursive: true });
    const reportFile = path.join(directory, "report.json");
    writeFileSync(reportFile, `${JSON.stringify({
      schema: "sts2.headless/managed-cross-host-run-1",
      generated_at: new Date().toISOString(),
      ...result
    }, null, 2)}\n`);
    console.log(JSON.stringify({
      status: result.comparison.verdict,
      report_file: reportFile,
      errors: result.comparison.errors,
      first_divergence: result.comparison.first_divergence
    }, null, 2));
    process.exitCode = result.comparison.verdict === "cross_host_semantic_match" ? 0 : 10;
    return;
  }
  if (command === "repeatability") {
    const scenarioFile = option(args, "--scenario");
    if (!scenarioFile) throw new Error("repeatability requires --scenario <scenario.json>.");
    const scenario = JSON.parse(readFileSync(path.resolve(scenarioFile), "utf8"));
    const scenarioErrors = validateScenarioDescriptor(scenario);
    if (scenarioErrors.length > 0) throw new Error(`Invalid scenario: ${scenarioErrors.join(", ")}`);
    if (typeof scenario.start_interaction_kind !== "string" || scenario.start_interaction_kind.length === 0) {
      throw new Error("Managed repeatability scenarios require start_interaction_kind.");
    }
    const exactGame = diskIdentity();
    const semanticTarget = {
      schema: "sts2.headless/semantic-target-1",
      target_id: "sts2-v0.111.0-player-visible-zhs-v1",
      protocol_version: "1.0.0",
      game_build: {
        version: exactGame.release.version,
        commit: exactGame.release.commit,
        main_assembly_hash: exactGame.runtime_main_assembly_hash
      },
      content_policy_id: "vanilla_singleplayer_v1",
      information_policy_id: "player_visible_v1",
      presentation_language: option(args, "--language", "zhs")
    };
    const driver = await createManagedExactHostDriver({
      root: ROOT,
      candidateDirectory,
      diskIdentity: exactGame,
      semanticTarget,
      character: option(args, "--character", "Ironclad"),
      requestTimeoutMs: Number(option(args, "--timeout-ms", "10000")),
      scenarioTimeoutMs: Number(option(args, "--scenario-timeout-ms", "120000"))
    });
    const result = await runManagedRepeatability({ driver, scenario });
    const directory = path.join(LOCAL, "evidence", `managed-repeatability-${safeTimestamp()}`);
    mkdirSync(directory, { recursive: true });
    const reportFile = path.join(directory, "report.json");
    writeFileSync(reportFile, `${JSON.stringify({ generated_at: new Date().toISOString(), ...result }, null, 2)}\n`);
    console.log(JSON.stringify({
      status: result.verdict,
      report_file: reportFile,
      runtime_instance_ids: result.runtime_instance_ids,
      actual_seeds: result.actual_seeds,
      exact_candidate_artifact: result.exact_candidate_artifact,
      errors: result.errors,
      first_divergence: result.comparison?.first_divergence ?? null
    }, null, 2));
    process.exitCode = result.verdict === "managed_repeatability_pass" ? 0 : 10;
    return;
  }
  if (command === "capacity") {
    const result = await runManagedCandidateCapacity({
      root: ROOT,
      candidateDirectory,
      diskIdentity: diskIdentity(),
      workerCounts: parseWorkerCounts(option(args, "--workers", "1,2,4")),
      maxActions: Number(option(args, "--max-actions", "200")),
      episodesPerWorker: Number(option(args, "--episodes", "5")),
      seedPrefix: option(args, "--seed-prefix", "H1MANAGED"),
      evidenceRoot: path.join(LOCAL, "evidence")
    });
    console.log(JSON.stringify({ status: result.report.status, report_file: result.reportFile }, null, 2));
    process.exitCode = result.report.status === "measured_raw_candidate" ? 0 : 3;
    return;
  }
  console.log(`Managed exact candidate (experimental, unqualified)

Commands:
  prepare [--candidate DIR]
  audit --candidate DIR
  probe --candidate DIR [--seed SEED] [--episodes N] [--max-actions N] [--reset-at card_select,card_reward]
  pe-probe --candidate DIR [--seed SEED] [--episodes N] [--max-actions N]
           [--repeat-seed] [--verify-reset-authority] [--verify-idempotency]
  pe-profile --candidate DIR [--profile training|training-validated|reliability|qualification]
  engine-lab --candidate DIR [--episodes N] [--serialize-each-decision]
  pe-capacity --candidate DIR [--profile NAME] [--workers 1,2,4] [--episodes N] [--max-actions N]
  pe-sharded-capacity --candidate DIR [--profile NAME] [--workers 1,2,4] [--episodes N]
  native-gates --candidate DIR [--seed SEED]
  recovery --candidate DIR [--seed SEED]
  cross-host --candidate DIR [--seed SEED] [--start-kind KIND] [--discovery-actions N] [--max-actions N] [--template ID]
  repeatability --candidate DIR --scenario scenario.json [--scenario-timeout-ms N]
  capacity --candidate DIR [--workers 1,2,4] [--episodes N] [--max-actions N]

The raw candidate protocol is not the canonical Player Environment. pe-probe
measures a strict but explicitly partial adapter; neither route is cross-host
qualified until the differential corpus passes.`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
