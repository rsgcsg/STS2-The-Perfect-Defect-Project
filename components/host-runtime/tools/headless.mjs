#!/usr/bin/env node
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  discoverGameDirectory,
  readDiskIdentity,
  resolveInstallation
} from "../src/game-installation.mjs";
import {
  installConnectorRelease,
  rollbackConnectorRelease,
  writeInstallationRecord
} from "../src/connector-release.mjs";
import {
  queryHeadlessStatus,
  runHeadlessHost,
  stopHeadlessHost
} from "../src/headless-host.mjs";
import { listGameProcesses, runShippedProbe } from "../src/runtime-probe.mjs";
import { runBoundedJourney } from "../src/journey-probe.mjs";
import { evaluateRuntimeCompatibility } from "../src/compatibility.mjs";
import { enableConnectorModLoading, resetIsolatedProfile } from "../src/profile-isolation.mjs";
import { bootstrapIsolatedProfile } from "../src/profile-bootstrap.mjs";
import {
  captureProfileTemplate,
  instantiateProfileTemplate
} from "../src/profile-template.mjs";
import { parseWorkerCounts, runCapacityBenchmark } from "../src/capacity-benchmark.mjs";
import { runRecoveryDrill } from "../src/recovery-drill.mjs";
import { runReferenceRepeatability } from "../src/semantic-differential.mjs";
import { runReferenceSoak } from "../src/soak-supervisor.mjs";
import { runRequalificationDrill } from "../src/requalification.mjs";
import { runAutoSlayerUpperBound } from "../src/autoslayer-experiment.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function option(args, name, fallback) {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
}

function uniqueOption(args, name, fallback) {
  const indices = args.flatMap((argument, index) => argument === name ? [index] : []);
  if (indices.length > 1) throw new Error(`${name} may be supplied only once.`);
  if (indices.length === 0) return fallback;
  const value = args[indices[0] + 1];
  if (!value || value.startsWith("--")) throw new Error(`${name} requires a value.`);
  return value;
}

function resolveCurrentInstallation() {
  const gameDir = discoverGameDirectory();
  if (!gameDir) throw new Error("Could not locate the Steam installation; set STS2_GAME_DIR.");
  return resolveInstallation(gameDir);
}

async function main() {
  const [command = "help", ...args] = process.argv.slice(2);
  if (command === "setup") {
    const installation = resolveCurrentInstallation();
    const localRoot = path.join(ROOT, ".local", "releases");
    const result = await installConnectorRelease({ installation, localRoot });
    writeInstallationRecord(path.join(ROOT, ".local", "last-connector-install.json"), result);
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  if (command === "rollback") {
    const backup = option(args, "--backup", null);
    if (!backup) throw new Error("rollback requires --backup <directory>.");
    const result = rollbackConnectorRelease({
      backup,
      localRoot: path.join(ROOT, ".local", "releases")
    });
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  if (command === "doctor") {
    const installation = resolveCurrentInstallation();
    const diskIdentity = readDiskIdentity(installation);
    const compatibility = evaluateRuntimeCompatibility(diskIdentity);
    const runningGameProcesses = listGameProcesses();
    const report = {
      schema_version: 1,
      generated_at: new Date().toISOString(),
      node: process.version,
      disk_identity: diskIdentity,
      compatibility,
      running_game_processes: runningGameProcesses,
      ready_for_supported_start:
        runningGameProcesses.length === 0 && compatibility.status === "supported_exact",
      ready_for_experimental_probe: runningGameProcesses.length === 0
    };
    console.log(JSON.stringify(report, null, 2));
    process.exitCode = report.ready_for_supported_start ? 0 : 2;
    return;
  }
  if (command === "start") {
    const displayMode = option(args, "--display-mode", "headless");
    const expectedExperimentalConnectorSource = uniqueOption(args, "--experimental-connector-source", null);
    if (expectedExperimentalConnectorSource != null && displayMode !== "native_window") {
      throw new Error("--experimental-connector-source requires --display-mode native_window.");
    }
    if (expectedExperimentalConnectorSource != null
        && !/^[a-f0-9]{40}$/u.test(expectedExperimentalConnectorSource)) {
      throw new Error("--experimental-connector-source requires one exact lowercase 40-character Git revision.");
    }
    const installation = resolveCurrentInstallation();
    await runHeadlessHost({
      installation,
      localRoot: path.join(ROOT, ".local"),
      endpoint: option(args, "--endpoint", "http://127.0.0.1:15526"),
      timeoutMs: Number(option(args, "--timeout-ms", "90000")),
      mirrorLogs: args.includes("--verbose"),
      sharedProfileAcknowledged: args.includes("--shared-profile"),
      isolatedProfileId: option(args, "--isolated-profile", null),
      displayMode,
      expectedExperimentalConnectorSource
    });
    return;
  }
  if (command === "status") {
    console.log(JSON.stringify(await queryHeadlessStatus({
      localRoot: path.join(ROOT, ".local"),
      endpoint: option(args, "--endpoint", "http://127.0.0.1:15526")
    }), null, 2));
    return;
  }
  if (command === "stop") {
    console.log(JSON.stringify(await stopHeadlessHost({
      localRoot: path.join(ROOT, ".local"),
      endpoint: option(args, "--endpoint", "http://127.0.0.1:15526")
    }), null, 2));
    return;
  }
  if (command === "drill-update") {
    const result = runRequalificationDrill({
      installation: resolveCurrentInstallation(),
      evidenceRoot: path.join(ROOT, ".local", "evidence")
    });
    console.log(JSON.stringify({
      report_file: result.reportFile,
      status: result.report.status,
      authority: result.report.plan.authority,
      required_gates: result.report.plan.required_gates
    }, null, 2));
    process.exitCode = result.report.plan.authority === "supported_exact" ? 0 : 8;
    return;
  }
  if (command === "reset-profile") {
    const profileId = option(args, "--isolated-profile", null);
    if (!profileId) throw new Error("reset-profile requires --isolated-profile <id>.");
    const running = listGameProcesses();
    if (running.length > 0) {
      throw new Error(`Refusing to reset a profile while STS2 is running:\n${running.join("\n")}`);
    }
    console.log(JSON.stringify(resetIsolatedProfile(path.join(ROOT, ".local"), profileId), null, 2));
    return;
  }
  if (command === "bootstrap-profile") {
    const profileId = option(args, "--isolated-profile", null);
    if (!profileId) throw new Error("bootstrap-profile requires --isolated-profile <id>.");
    console.log(JSON.stringify(await bootstrapIsolatedProfile({
      installation: resolveCurrentInstallation(),
      localRoot: path.join(ROOT, ".local"),
      profileId,
      timeoutMs: Number(option(args, "--timeout-ms", "60000")),
      evidenceRoot: path.join(ROOT, ".local", "evidence")
    }), null, 2));
    return;
  }
  if (command === "enable-profile-mods") {
    const profileId = option(args, "--isolated-profile", null);
    const expectedSettingsSchema = Number(option(args, "--settings-schema", ""));
    if (!profileId) throw new Error("enable-profile-mods requires --isolated-profile <id>.");
    const running = listGameProcesses();
    if (running.length > 0) {
      throw new Error(`Refusing to rewrite profile settings while STS2 is running:\n${running.join("\n")}`);
    }
    console.log(JSON.stringify(enableConnectorModLoading(path.join(ROOT, ".local"), profileId, {
      expectedSettingsSchema,
      acknowledgeEarlyAccessDisclaimer: args.includes("--accept-ea-disclaimer")
    }), null, 2));
    return;
  }
  if (command === "capture-profile-template") {
    const profileId = option(args, "--isolated-profile", null);
    const templateId = option(args, "--template", null);
    if (!profileId || !templateId) {
      throw new Error("capture-profile-template requires --isolated-profile ID --template ID.");
    }
    const running = listGameProcesses();
    if (running.length > 0) {
      throw new Error(`Refusing to capture a profile while STS2 is running:\n${running.join("\n")}`);
    }
    console.log(JSON.stringify(captureProfileTemplate({
      localRoot: path.join(ROOT, ".local"),
      profileId,
      templateId,
      gameIdentity: readDiskIdentity(resolveCurrentInstallation())
    }), null, 2));
    return;
  }
  if (command === "instantiate-profile-template") {
    const profileId = option(args, "--isolated-profile", null);
    const templateId = option(args, "--template", null);
    if (!profileId || !templateId) {
      throw new Error("instantiate-profile-template requires --template ID --isolated-profile ID.");
    }
    const running = listGameProcesses();
    if (running.length > 0) {
      throw new Error(`Refusing to replace a profile while STS2 is running:\n${running.join("\n")}`);
    }
    console.log(JSON.stringify(instantiateProfileTemplate({
      localRoot: path.join(ROOT, ".local"),
      templateId,
      profileId,
      expectedGameIdentity: readDiskIdentity(resolveCurrentInstallation())
    }), null, 2));
    return;
  }
  if (command === "probe-shipped" || command === "probe-menu-control") {
    const installation = resolveCurrentInstallation();
    const timeoutMs = Number(option(args, "--timeout-ms", "90000"));
    const endpoint = option(args, "--endpoint", "http://127.0.0.1:15526");
    const result = await runShippedProbe({
      installation,
      localRoot: path.join(ROOT, ".local"),
      endpoint,
      timeoutMs,
      evidenceRoot: path.join(ROOT, ".local", "evidence"),
      exerciseMenu: command === "probe-menu-control",
      sharedProfileAcknowledged: args.includes("--shared-profile"),
      isolatedProfileId: option(args, "--isolated-profile", null),
      experimentalBuildAcknowledged: args.includes("--experimental-build")
    });
    console.log(JSON.stringify({
      report_file: result.reportFile,
      evidence_directory: result.evidenceDir,
      boot_verdict: result.report.verdict,
      control_gate_verdict: result.report.control_gate?.verdict ?? null
    }, null, 2));
    const passed = command === "probe-menu-control"
      ? result.report.control_gate?.verdict?.verdict === "h1_pass"
      : result.report.verdict.verdict === "h0_pass";
    process.exitCode = passed ? 0 : 3;
    return;
  }
  if (command === "probe-journey") {
    const result = await runBoundedJourney({
      installation: resolveCurrentInstallation(),
      localRoot: path.join(ROOT, ".local"),
      endpoint: option(args, "--endpoint", "http://127.0.0.1:15526"),
      timeoutMs: Number(option(args, "--timeout-ms", "90000")),
      actionTimeoutMs: Number(option(args, "--action-timeout-ms", "20000")),
      maxActions: Number(option(args, "--max-actions", "40")),
      tutorialPreference: option(args, "--tutorials", "disable"),
      runSeed: option(args, "--seed", null),
      hostExecutionProfile: option(args, "--host-execution-profile", null),
      evidenceRoot: path.join(ROOT, ".local", "evidence"),
      sharedProfileAcknowledged: args.includes("--shared-profile"),
      isolatedProfileId: option(args, "--isolated-profile", null),
      experimentalBuildAcknowledged: args.includes("--experimental-build")
    });
    console.log(JSON.stringify({
      report_file: result.reportFile,
      events_file: result.eventsFile,
      verdict: result.report.verdict
    }, null, 2));
    process.exitCode = result.report.verdict.verdict === "h2_pass" ? 0 : 4;
    return;
  }
  if (command === "bench-capacity") {
    const result = await runCapacityBenchmark({
      installation: resolveCurrentInstallation(),
      localRoot: path.join(ROOT, ".local"),
      evidenceRoot: path.join(ROOT, ".local", "evidence"),
      templateId: option(args, "--template", "vanilla-clean"),
      workerCounts: parseWorkerCounts(option(args, "--workers", "1,2,4")),
      basePort: Number(option(args, "--base-port", "15600")),
      maxActions: Number(option(args, "--max-actions", "12")),
      runSeed: option(args, "--seed", "H1CAPACITY01"),
      timeoutMs: Number(option(args, "--timeout-ms", "90000")),
      actionTimeoutMs: Number(option(args, "--action-timeout-ms", "20000"))
    });
    console.log(JSON.stringify({ report_file: result.reportFile, status: result.report.status }, null, 2));
    process.exitCode = result.report.status === "measured" ? 0 : 5;
    return;
  }
  if (command === "probe-reference-differential") {
    const result = await runReferenceRepeatability({
      installation: resolveCurrentInstallation(),
      localRoot: path.join(ROOT, ".local"),
      evidenceRoot: path.join(ROOT, ".local", "evidence"),
      templateId: option(args, "--template", "vanilla-clean"),
      profilePrefix: option(args, "--profile-prefix", "reference-differential"),
      endpoint: option(args, "--endpoint", "http://127.0.0.1:15710"),
      runSeed: option(args, "--seed", null),
      maxActions: Number(option(args, "--max-actions", "12")),
      timeoutMs: Number(option(args, "--timeout-ms", "90000")),
      actionTimeoutMs: Number(option(args, "--action-timeout-ms", "20000")),
      experimentalBuildAcknowledged: args.includes("--experimental-build")
    });
    console.log(JSON.stringify({ report_file: result.reportFile, status: result.report.status }, null, 2));
    process.exitCode = result.report.status === "semantic_match" ? 0 : 7;
    return;
  }
  if (command === "probe-recovery") {
    const result = await runRecoveryDrill({
      installation: resolveCurrentInstallation(),
      localRoot: path.join(ROOT, ".local"),
      evidenceRoot: path.join(ROOT, ".local", "evidence"),
      templateId: option(args, "--template", "vanilla-clean"),
      profileId: option(args, "--isolated-profile", "recovery-worker"),
      endpoint: option(args, "--endpoint", "http://127.0.0.1:15700"),
      cycles: Number(option(args, "--cycles", "3")),
      faultAfterDeliveredActions: Number(option(args, "--fault-after", "5")),
      recoveryActions: Number(option(args, "--recovery-actions", "5")),
      timeoutMs: Number(option(args, "--timeout-ms", "90000")),
      actionTimeoutMs: Number(option(args, "--action-timeout-ms", "20000")),
      experimentalBuildAcknowledged: args.includes("--experimental-build"),
      runSeed: option(args, "--seed", "H1RECOVERY01"),
      faultMode: option(args, "--fault-mode", "process_crash")
    });
    console.log(JSON.stringify({ report_file: result.reportFile, status: result.report.status }, null, 2));
    process.exitCode = result.report.status.startsWith("recovery_pass_")
        || result.report.status.startsWith("recovery_operational_pass_")
      ? 0
      : 6;
    return;
  }
  if (command === "soak-reference") {
    const result = await runReferenceSoak({
      installation: resolveCurrentInstallation(),
      localRoot: path.join(ROOT, ".local"),
      evidenceRoot: path.join(ROOT, ".local", "evidence"),
      templateId: option(args, "--template", "vanilla-clean"),
      workerCount: Number(option(args, "--workers", "2")),
      episodes: Number(option(args, "--episodes", "2")),
      actionsPerWorker: Number(option(args, "--actions", "8")),
      basePort: Number(option(args, "--base-port", "15800")),
      baseSeed: option(args, "--base-seed", "H1SOAK"),
      timeoutMs: Number(option(args, "--timeout-ms", "90000")),
      actionTimeoutMs: Number(option(args, "--action-timeout-ms", "20000")),
      maxWorkerFailures: Number(option(args, "--max-worker-failures", "0")),
      experimentalBuildAcknowledged: args.includes("--experimental-build")
    });
    console.log(JSON.stringify({ report_file: result.reportFile, status: result.report.status }, null, 2));
    process.exitCode = result.report.status === "soak_smoke_pass" ? 0 : 8;
    return;
  }
  if (command === "experiment-autoslayer") {
    const result = await runAutoSlayerUpperBound({
      installation: resolveCurrentInstallation(),
      projectRoot: ROOT,
      localRoot: path.join(ROOT, ".local"),
      evidenceRoot: path.join(ROOT, ".local", "evidence"),
      templateId: option(args, "--template", "vanilla-clean"),
      profileId: option(args, "--isolated-profile", "autoslayer-upper-bound"),
      seed: option(args, "--seed", "H1AUTOSLAYER01"),
      timeoutMs: Number(option(args, "--timeout-ms", String(30 * 60 * 1000))),
      experimentalBuildAcknowledged: args.includes("--experimental-build")
    });
    console.log(JSON.stringify({ report_file: result.reportFile, status: result.report.status }, null, 2));
    process.exitCode = result.report.status === "autoslayer_upper_bound_pass" ? 0 : 9;
    return;
  }
  console.log(`Usage:
  node tools/headless.mjs setup
  node tools/headless.mjs rollback --backup DIRECTORY
  node tools/headless.mjs doctor
  node tools/headless.mjs start (--isolated-profile ID | --shared-profile) [--display-mode headless|native_window] [--experimental-connector-source REVISION] [--timeout-ms 90000] [--endpoint URL] [--verbose]
  node tools/headless.mjs status [--endpoint URL]
  node tools/headless.mjs stop [--endpoint URL]
  node tools/headless.mjs reset-profile --isolated-profile ID
  node tools/headless.mjs bootstrap-profile --isolated-profile ID [--timeout-ms 60000]
  node tools/headless.mjs enable-profile-mods --isolated-profile ID --settings-schema VERSION [--accept-ea-disclaimer]
  node tools/headless.mjs capture-profile-template --isolated-profile ID --template ID
  node tools/headless.mjs instantiate-profile-template --template ID --isolated-profile ID
  node tools/headless.mjs probe-shipped (--isolated-profile ID | --shared-profile) [--experimental-build] [--timeout-ms 90000] [--endpoint URL]
  node tools/headless.mjs probe-menu-control (--isolated-profile ID | --shared-profile) [--experimental-build] [--timeout-ms 90000] [--endpoint URL]
  node tools/headless.mjs probe-journey (--isolated-profile ID | --shared-profile) [--experimental-build] [--max-actions 40] [--seed SEED] [--host-execution-profile PROFILE] [--tutorials disable|enable] [--timeout-ms 90000]
  node tools/headless.mjs bench-capacity [--template vanilla-clean] [--workers 1,2,4] [--base-port 15600] [--max-actions 12] [--seed SEED]
  node tools/headless.mjs probe-reference-differential --seed SEED [--template vanilla-clean] [--max-actions 12] [--endpoint URL] [--experimental-build]
  node tools/headless.mjs probe-recovery [--template vanilla-clean] [--isolated-profile recovery-worker] [--cycles 3] [--fault-after 5] [--recovery-actions 5] [--seed SEED] [--fault-mode process_crash|process_hang] [--experimental-build]
  node tools/headless.mjs soak-reference [--template vanilla-clean] [--workers 2] [--episodes 2] [--actions 8] [--base-seed H1SOAK] [--max-worker-failures 0] [--experimental-build]
  node tools/headless.mjs experiment-autoslayer [--template vanilla-clean] [--isolated-profile autoslayer-upper-bound] [--seed H1AUTOSLAYER01] [--timeout-ms 1800000] [--experimental-build]`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
