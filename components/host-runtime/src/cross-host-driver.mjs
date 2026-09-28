import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createHostDriver } from "./host-driver.mjs";
import { readDiskIdentity, readInstalledConnectorIdentity } from "./game-installation.mjs";
import { chooseBoundAction, runBoundedJourney } from "./journey-probe.mjs";
import {
  inspectManagedCandidateBuild,
  loadManagedCandidateManifest,
  selectManagedCandidateManifest
} from "./managed-candidate.mjs";
import { startManagedPlayerEnvironmentSession } from "./managed-player-environment.mjs";
import { instantiateProfileTemplate } from "./profile-template.mjs";
import {
  canonicalDecisionDigest,
  canonicalizeReadResponse,
  canonicalizeSelectedAction,
  canonicalizeSnapshot
} from "./semantic-decision.mjs";

function readJsonl(file) {
  return readFileSync(file, "utf8").split(/\r?\n/u).filter(Boolean).map((line) => JSON.parse(line));
}

function managedLanguage(presentationLanguage) {
  if (presentationLanguage === "zhs") return "zh";
  if (presentationLanguage === "eng") return "en";
  throw new Error(`Managed cross-Host driver does not support presentation language ${presentationLanguage}.`);
}

function scenarioStartKind(scenario) {
  if (typeof scenario.start_interaction_kind !== "string"
      || scenario.start_interaction_kind.length === 0) {
    throw new Error("Cross-Host scenarios require start_interaction_kind.");
  }
  return scenario.start_interaction_kind;
}

function scenarioDiscoveryLimit(scenario) {
  const limit = scenario.discovery_max_actions ?? 0;
  if (!Number.isSafeInteger(limit) || limit < 0) {
    throw new Error("Cross-Host scenario discovery_max_actions must be a non-negative integer.");
  }
  return limit;
}

export function sliceScenarioEvents(events, scenario) {
  const startKind = scenarioStartKind(scenario);
  const start = events.findIndex((event) => event?.type === "action"
    && event?.canonical_decision?.interaction?.kind === startKind);
  if (start < 0) throw new Error(`Host did not reach scenario boundary ${startKind}.`);
  const selected = [];
  let actionCount = 0;
  for (let index = start; index < events.length; index += 1) {
    const event = events[index];
    if (event?.type === "read" && scenario.read_policy === "advertised_once") {
      selected.push(event);
      continue;
    }
    if (event?.type === "action") {
      if (actionCount >= scenario.max_actions) break;
      selected.push(event);
      actionCount += 1;
      continue;
    }
    if ((event?.type === "terminal" || event?.type === "stop") && actionCount > 0) {
      selected.push(event);
      break;
    }
  }
  return { events: selected, actionCount };
}

function managedLoadedIdentity(started, semanticTarget) {
  const identity = started.runtime.runtimeIdentity;
  return {
    protocol: semanticTarget.protocol_version,
    host: {
      id: "sts2_managed_exact_candidate",
      name: "STS2 Managed Exact Candidate",
      version: started.runtime.manifest.candidate_id,
      runtime_instance_id: started.runtime.adapterRuntimeInstanceId,
      host_kind: "managed_exact",
      implementation: {
        source_revision: started.runtime.manifest.upstream.revision,
        artifact_sha256: identity.host_assembly_sha256,
        module_version_id: identity.host_module_mvid
      }
    },
    game: {
      version: semanticTarget.game_build.version,
      commit: semanticTarget.game_build.commit,
      main_assembly_hash: semanticTarget.game_build.main_assembly_hash,
      modset: {
        status: "managed_exact_no_mod_loader",
        fingerprint: started.environmentFingerprint
      }
    }
  };
}

async function runManagedScenario({
  root,
  candidateDirectory,
  diskIdentity,
  semanticTarget,
  character,
  requestTimeoutMs,
  scenarioTimeoutMs,
  scenario
}) {
  const startKind = scenarioStartKind(scenario);
  const deadline = Date.now() + scenarioTimeoutMs;
  const operationTimeout = () => {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error(`Managed scenario exceeded ${scenarioTimeoutMs}ms deadline.`);
    return Math.min(requestTimeoutMs, remaining);
  };
  const started = await startManagedPlayerEnvironmentSession({
    root,
    candidateDirectory,
    diskIdentity,
    character,
    language: managedLanguage(semanticTarget.presentation_language),
    requestTimeoutMs: Math.min(requestTimeoutMs, scenarioTimeoutMs)
  });
  const events = [];
  let snapshot = null;
  let terminal = "not_started";
  let unknownCount = 0;
  let failure = null;
  let runIdentity = null;
  let exit = null;
  try {
    snapshot = await started.session.mount({ seed: scenario.seed, timeoutMs: operationTimeout() });
    runIdentity = await started.runtime.process.request({ cmd: "run_identity" }, operationTimeout());
    if (runIdentity?.type !== "run_identity"
        || runIdentity.active !== true
        || runIdentity.seed !== scenario.seed) {
      throw new Error(`Managed Host did not prove scenario seed ${scenario.seed}.`);
    }
    for (let index = 0;
      snapshot.interaction.kind !== startKind && index < scenarioDiscoveryLimit(scenario);
      index += 1) {
      if (snapshot.interaction.kind === "game_over") break;
      const action = chooseBoundAction(snapshot, { tutorialPreference: "disable" });
      if (action == null) break;
      const receipt = await started.session.submit({
        requestId: `cross-host-managed-discovery-${String(index + 1).padStart(6, "0")}-${randomUUID()}`,
        expectedSnapshotId: snapshot.snapshot_id,
        boundActionId: action.bound_action_id,
        timeoutMs: operationTimeout()
      });
      if (receipt.delivery === "unknown") {
        unknownCount += 1;
        throw new Error("Managed Host returned unknown delivery before the scenario boundary.");
      }
      if (receipt.delivery !== "delivered" || receipt.successor == null) {
        throw new Error(`Managed Host did not deliver discovery action: ${receipt.reason_code ?? "unspecified"}.`);
      }
      snapshot = receipt.successor;
    }
    if (snapshot.interaction.kind !== startKind) {
      throw new Error(`Managed Host did not reach scenario boundary ${startKind}.`);
    }
    const readKinds = new Set();
    for (let index = 0; index < scenario.max_actions; index += 1) {
      operationTimeout();
      if (scenario.read_policy === "advertised_once") {
        for (const descriptor of snapshot.reads) {
          if (readKinds.has(descriptor.kind)) continue;
          operationTimeout();
          const value = started.session.read({
            readId: descriptor.read_id,
            expectedSnapshotId: snapshot.snapshot_id
          });
          operationTimeout();
          readKinds.add(descriptor.kind);
          events.push({
            type: "read",
            kind: descriptor.kind,
            canonical_read: canonicalizeReadResponse(value)
          });
        }
      }
      if (snapshot.interaction.kind === "game_over") {
        events.push({
          type: "terminal",
          reason: "game_over",
          canonical_decision: canonicalizeSnapshot(snapshot)
        });
        terminal = "game_over";
        break;
      }
      const action = chooseBoundAction(snapshot, { tutorialPreference: "disable" });
      if (action == null) {
        events.push({
          type: "stop",
          reason: snapshot.status === "visible_unsupported"
            ? "visible_unsupported"
            : "no_shared_probe_action",
          canonical_decision: canonicalizeSnapshot(snapshot)
        });
        terminal = events.at(-1).reason;
        break;
      }
      const receipt = await started.session.submit({
        requestId: `cross-host-managed-${String(index + 1).padStart(6, "0")}-${randomUUID()}`,
        expectedSnapshotId: snapshot.snapshot_id,
        boundActionId: action.bound_action_id,
        timeoutMs: operationTimeout()
      });
      events.push({
        type: "action",
        canonical_decision_digest: canonicalDecisionDigest(snapshot),
        canonical_decision: canonicalizeSnapshot(snapshot),
        canonical_selected_action: canonicalizeSelectedAction(snapshot, action.bound_action_id),
        delivery: receipt.delivery,
        reason_code: receipt.reason_code ?? null
      });
      if (receipt.delivery === "unknown") {
        unknownCount += 1;
        terminal = "unknown_delivery";
        break;
      }
      if (receipt.delivery !== "delivered" || receipt.successor == null) {
        terminal = `not_delivered:${receipt.reason_code ?? "unspecified"}`;
        break;
      }
      snapshot = receipt.successor;
    }
    if (terminal === "not_started") terminal = "action_limit";
    if (Date.now() > deadline) throw new Error(`Managed scenario exceeded ${scenarioTimeoutMs}ms deadline.`);
  } catch (error) {
    failure = error instanceof Error ? error.message : String(error);
    terminal = "scenario_error";
  } finally {
    exit = await started.session.close();
  }
  const loadedIdentity = managedLoadedIdentity(started, semanticTarget);
  const integrityPass = failure == null
    && unknownCount === 0
    && new Set(["action_limit", "game_over"]).has(terminal);
  return {
    report: {
      schema: "sts2.headless/managed-host-scenario-1",
      status: integrityPass ? "bounded_scenario_measured" : "scenario_incomplete",
      loaded_identity: loadedIdentity,
      episode_provenance: {
        verdict: runIdentity?.seed === scenario.seed ? "provenance_pass" : "provenance_incomplete",
        requested_seed: scenario.seed,
        actual_seed: runIdentity?.seed ?? null,
        runtime_instance_id: loadedIdentity.host.runtime_instance_id
      },
      verdict: {
        integrity: {
          verdict: integrityPass ? "integrity_pass" : "integrity_incomplete",
          errors: [
            ...(failure == null ? [] : [`failure:${failure}`]),
            ...(unknownCount === 0 ? [] : ["unknown_delivery_observed"]),
            ...(new Set(["action_limit", "game_over"]).has(terminal)
              ? []
              : [`terminal:${terminal}`])
          ],
          unknown_deliveries: unknownCount,
          terminal
        }
      },
      candidate: {
        manifest: started.runtime.manifest,
        build: started.runtime.build,
        adapter_environment_fingerprint: started.environmentFingerprint
      },
      process: { exit, diagnostics: started.runtime.process.diagnostics },
      non_claims: [
        "This bounded scenario is not cross-Host qualification until the differential passes.",
        "The managed projection remains partial and experimental."
      ]
    },
    events
  };
}

export async function createManagedExactHostDriver({
  root,
  candidateDirectory,
  diskIdentity,
  semanticTarget,
  character = "Ironclad",
  requestTimeoutMs = 10_000,
  scenarioTimeoutMs = 120_000
}) {
  if (!Number.isSafeInteger(scenarioTimeoutMs) || scenarioTimeoutMs < 1) {
    throw new TypeError("scenarioTimeoutMs must be a positive integer.");
  }
  const { manifest: loadedManifest } = loadManagedCandidateManifest(root);
  const manifest = selectManagedCandidateManifest(loadedManifest, diskIdentity);
  const build = await inspectManagedCandidateBuild({ root, candidateDirectory, manifest });
  return createHostDriver({
    driverId: manifest.candidate_id,
    hostKind: "managed_exact",
    semanticTarget,
    implementation: {
      source_revision: manifest.upstream.revision,
      artifact_sha256: build.artifact_sha256,
      source_patch_sha256: build.source_patch_sha256
    },
    runScenario: (scenario) => runManagedScenario({
      root,
      candidateDirectory,
      diskIdentity,
      semanticTarget,
      character,
      requestTimeoutMs,
      scenarioTimeoutMs,
      scenario
    })
  });
}

export function createShippedReferenceHostDriver({
  installation,
  localRoot,
  evidenceRoot,
  semanticTarget,
  templateId = "vanilla-clean",
  endpoint = "http://127.0.0.1:15820",
  timeoutMs = 90_000,
  actionTimeoutMs = 20_000,
  experimentalBuildAcknowledged = false
}) {
  const installed = readInstalledConnectorIdentity(installation);
  return createHostDriver({
    driverId: "shipped-godot-reference",
    hostKind: "shipped_reference",
    semanticTarget,
    implementation: installed.identity ?? { status: installed.status },
    runScenario: async (scenario) => {
      scenarioStartKind(scenario);
      const profileId = `cross-host-reference-${randomUUID().slice(0, 12)}`;
      instantiateProfileTemplate({
        localRoot,
        templateId,
        profileId,
        expectedGameIdentity: readDiskIdentity(installation)
      });
      const result = await runBoundedJourney({
        installation,
        localRoot,
        evidenceRoot,
        endpoint,
        timeoutMs,
        actionTimeoutMs,
        maxActions: scenario.max_actions + (scenario.discovery_max_actions ?? 8),
        tutorialPreference: "disable",
        isolatedProfileId: profileId,
        experimentalBuildAcknowledged,
        experimentalConnectorAcknowledged: true,
        evidenceLabel: "cross-host-reference",
        runSeed: scenario.seed,
        stopOnCoverage: false
      });
      const sliced = sliceScenarioEvents(readJsonl(result.eventsFile), scenario);
      const terminal = result.report.verdict?.integrity?.terminal;
      const enough = sliced.actionCount === scenario.max_actions
        || terminal === "game_over";
      if (!enough) {
        result.report.verdict.integrity = {
          ...result.report.verdict.integrity,
          verdict: "integrity_incomplete",
          errors: [
            ...result.report.verdict.integrity.errors,
            "scenario_action_window_incomplete"
          ]
        };
      }
      result.report.scenario_window = {
        start_interaction_kind: scenario.start_interaction_kind,
        requested_actions: scenario.max_actions,
        observed_actions: sliced.actionCount
      };
      return {
        report: result.report,
        events: sliced.events,
        evidence: {
          directory: result.evidenceDirectory,
          report_file: result.reportFile,
          events_file: result.eventsFile
        }
      };
    }
  });
}
