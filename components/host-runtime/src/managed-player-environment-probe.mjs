import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { ProcessResourceSampler } from "./process-resource-sampler.mjs";
import { readProjectIdentity } from "./project-identity.mjs";
import { readSystemIdentity } from "./system-identity.mjs";
import {
  canonicalizeReadResponse,
  canonicalizeSelectedAction,
  canonicalizeSnapshot
} from "./semantic-decision.mjs";
import { startManagedPlayerEnvironmentSession } from "./managed-player-environment.mjs";

function safeTimestamp() {
  return new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-");
}

function requirePositiveInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new TypeError(`${name} must be a positive integer.`);
  }
}

function latencySummary(values) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const percentile = (fraction) => sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1)];
  return {
    count: sorted.length,
    mean: sorted.reduce((sum, value) => sum + value, 0) / sorted.length,
    p50: percentile(0.50),
    p95: percentile(0.95),
    p99: percentile(0.99),
    max: sorted.at(-1)
  };
}

function actionSemanticKey(snapshot, action) {
  const referents = new Map((snapshot.referents ?? []).map((referent) => [referent.referent_id, referent]));
  const withoutOpaqueIdentity = (value) => {
    if (Array.isArray(value)) return value.map(withoutOpaqueIdentity);
    if (value == null || typeof value !== "object") return value;
    return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !/(?:^|_)(?:entity|referent)_ids?$/u.test(key)
        && !["combat_id", "snapshot_id", "interaction_id", "bound_action_id", "read_id"].includes(key))
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, nested]) => [key, withoutOpaqueIdentity(nested)]));
  };
  const publicReferent = (id) => {
    const referent = referents.get(id);
    return referent == null ? null : {
      role: referent.role,
      label: referent.label,
      properties: withoutOpaqueIdentity(referent.properties)
    };
  };
  return JSON.stringify({
    verb: action.verb,
    label: action.label,
    subject: publicReferent(action.subject_referent_id),
    arguments: (action.arguments ?? []).map((argument) => ({
      role: argument.role,
      referent: publicReferent(argument.referent_id)
    }))
  });
}

function inPublicCatalogOrder(actions, compare) {
  // Callers retain the complete public catalog's relative order. Fully equal
  // semantic values remain distinct occurrences, tied by that published order;
  // this is not an arbitrary-order or cross-Host correspondence guarantee.
  return actions.map((action, occurrence) => ({ action, occurrence }))
    .sort((left, right) => compare(left.action, right.action) || left.occurrence - right.occurrence)
    .map(({ action }) => action);
}

function ordered(snapshot, actions) {
  return inPublicCatalogOrder(actions, (left, right) =>
    actionSemanticKey(snapshot, left).localeCompare(actionSemanticKey(snapshot, right)));
}

export function managedTerminalOutcome(snapshot) {
  if (snapshot?.interaction?.kind !== "game_over") return null;
  const result = snapshot.interaction.content?.context?.result;
  const victory = result === "win" ? true : result === "loss" ? false : null;
  return {
    victory: typeof victory === "boolean" ? victory : null,
    act: snapshot.persistent?.content?.run?.act ?? null,
    floor: snapshot.persistent?.content?.run?.floor ?? null
  };
}

export function chooseManagedPlayerEnvironmentAction(snapshot) {
  if (snapshot?.status !== "interactive" || snapshot?.bound_actions?.status !== "complete") return null;
  const actions = snapshot.bound_actions.actions;
  const kind = snapshot.interaction.kind;
  if (kind === "game_over") return null; // Episode outcome precedes optional summary navigation.
  const referents = new Map((snapshot.referents ?? []).map((referent) => [referent.referent_id, referent]));
  const subject = (action) => referents.get(action.subject_referent_id)?.properties ?? {};
  const numeric = (value) => Number.isFinite(Number(value)) ? Number(value) : Number.MAX_SAFE_INTEGER;
  const byProperties = (items, selector) => inPublicCatalogOrder(items, (left, right) => {
    const leftValues = selector(left);
    const rightValues = selector(right);
    for (let index = 0; index < Math.max(leftValues.length, rightValues.length); index += 1) {
      const compared = String(leftValues[index] ?? "").localeCompare(String(rightValues[index] ?? ""), "en", {
        numeric: true
      });
      if (compared !== 0) return compared;
    }
    return actionSemanticKey(snapshot, left).localeCompare(actionSemanticKey(snapshot, right));
  });
  if (kind === "combat_turn") {
    return byProperties(actions.filter((action) => action.verb === "play"), (action) => {
      const card = subject(action);
      const targetId = action.arguments?.find((argument) => argument.role === "target")?.referent_id;
      const target = referents.get(targetId)?.properties ?? {};
      return [card.definition_id, numeric(card.hand_index), target.definition_id];
    })[0]
      ?? actions.find((action) => action.verb === "end_turn")
      ?? null;
  }
  if (kind === "rest_site") {
    return actions.find((action) => subject(action).option_id === "HEAL")
      ?? byProperties(actions, (action) => [subject(action).option_id])[0]
      ?? null;
  }
  if (kind === "event_option") {
    return byProperties(actions, (action) => [numeric(subject(action).index)])[0] ?? null;
  }
  if (kind === "map_navigation") {
    return byProperties(actions, (action) => [
      numeric(subject(action).row), numeric(subject(action).col)
    ])[0] ?? null;
  }
  if (kind === "reward_claim") {
    return byProperties(actions.filter((action) => /^Claim /u.test(action.label)), (action) => [
      subject(action).kind,
      subject(action).label
    ])[0]
      ?? byProperties(actions.filter((action) => /^Discard /u.test(action.label)), (action) => [
        numeric(subject(action).slot)
      ])[0]
      ?? actions.find((action) => /rewards|continue/u.test(action.label))
      ?? ordered(snapshot, actions)[0]
      ?? null;
  }
  if (kind === "card_reward_selection") {
    return byProperties(actions.filter((action) => action.verb === "select"), (action) => [
      numeric(subject(action).index)
    ])[0]
      ?? actions.find((action) => action.verb === "skip")
      ?? null;
  }
  if (kind === "treasure_room") {
    return byProperties(actions.filter((action) => action.verb === "activate"), (action) => [
      numeric(subject(action).index)
    ])[0]
      ?? actions.find((action) => action.verb === "skip" || action.verb === "open")
      ?? null;
  }
  return ordered(snapshot, actions)[0] ?? null;
}

function episodeSeed(baseSeed, episodeIndex, episodeCount) {
  return episodeCount === 1
    ? baseSeed
    : `${baseSeed}E${String(episodeIndex + 1).padStart(3, "0")}`;
}

export async function runManagedPlayerEnvironmentProbe({
  root,
  candidateDirectory,
  diskIdentity,
  seed,
  character = "Ironclad",
  maxActions = 200,
  episodeCount = 1,
  requestTimeoutMs = 10_000,
  evidenceRoot = null,
  profileName = "qualification",
  identityMode = "crypto",
  validateSdk = true,
  eagerReads = true,
  canonicalEvidence = true,
  resourceSamplingIntervalMs = 250,
  quietDiagnostics = false,
  repeatSeed = false,
  verifyResetAuthority = false,
  verifyIdempotency = false
}) {
  requirePositiveInteger(maxActions, "maxActions");
  requirePositiveInteger(episodeCount, "episodeCount");
  requirePositiveInteger(requestTimeoutMs, "requestTimeoutMs");
  if (resourceSamplingIntervalMs != null) {
    requirePositiveInteger(resourceSamplingIntervalMs, "resourceSamplingIntervalMs");
    if (resourceSamplingIntervalMs < 250) {
      throw new TypeError("resourceSamplingIntervalMs must be null or at least 250.");
    }
  }
  if (typeof seed !== "string" || seed.length === 0) throw new TypeError("seed must be a non-empty string.");
  if (verifyResetAuthority && episodeCount < 2) {
    throw new TypeError("verifyResetAuthority requires at least two episodes.");
  }

  const processStarted = performance.now();
  const started = await startManagedPlayerEnvironmentSession({
    root,
    candidateDirectory,
    diskIdentity,
    character,
    requestTimeoutMs,
    identityMode,
    validateSdk,
    quietDiagnostics
  });
  const sampler = resourceSamplingIntervalMs == null
    ? null
    : new ProcessResourceSampler(started.runtime.process.pid, { intervalMs: resourceSamplingIntervalMs });
  if (sampler != null) await sampler.start();
  const childMetricsBefore = await started.session.processMetrics(requestTimeoutMs);
  const nodeCpuBefore = process.cpuUsage();
  const events = [];
  let snapshot = null;
  let runIdentity = null;
  let stopReason = null;
  let failure = null;
  let decisionStarted = null;
  let decisionEnded = null;
  const episodes = [];
  let actionsAttempted = 0;
  let actionsDelivered = 0;
  let readsCompleted = 0;
  let unknownObserved = false;
  const actionLatenciesMs = [];
  const mountLatenciesMs = [];
  const resetAuthorityGates = [];
  const idempotencyGates = [];
  let previousEpisodeAuthority = null;
  try {
    for (let episodeIndex = 0; episodeIndex < episodeCount; episodeIndex += 1) {
      const requestedSeed = repeatSeed ? seed : episodeSeed(seed, episodeIndex, episodeCount);
      const mountStarted = performance.now();
      snapshot = await started.session.mount({
        seed: requestedSeed,
        reset: episodeIndex > 0,
        timeoutMs: requestTimeoutMs
      });
      runIdentity = await started.runtime.process.request({ cmd: "run_identity" }, requestTimeoutMs);
      if (runIdentity?.type !== "run_identity"
          || runIdentity.active !== true
          || runIdentity.seed !== requestedSeed
          || runIdentity.action_executor_running !== false
          || runIdentity.pending_host_operation !== false
          || runIdentity.pending_card_selection !== false
          || runIdentity.pending_card_reward !== false
          || runIdentity.pending_reward_set !== false
          || runIdentity.pending_bundle !== false) {
        throw new Error(`Managed Player Environment did not prove requested game seed ${requestedSeed}.`);
      }
      const mountedAt = performance.now();
      mountLatenciesMs.push(mountedAt - mountStarted);
      decisionStarted ??= mountedAt;
      if (canonicalEvidence) {
        events.push({
          type: "episode_provenance",
          episode_index: episodeIndex,
          requested_seed: requestedSeed,
          actual_seed: runIdentity.seed,
          verdict: "provenance_pass",
          mount_wall_ms: mountedAt - mountStarted
        });
      }

      if (verifyResetAuthority && previousEpisodeAuthority != null) {
        if (snapshot.snapshot_id === previousEpisodeAuthority.snapshotId
            || snapshot.interaction.interaction_id === previousEpisodeAuthority.interactionId) {
          throw new Error(`Episode ${episodeIndex} reused prior snapshot or interaction authority.`);
        }
        const staleReceipt = await started.session.submit({
          requestId: `managed-pe-reset-stale-${String(episodeIndex + 1).padStart(4, "0")}`,
          expectedSnapshotId: previousEpisodeAuthority.snapshotId,
          boundActionId: previousEpisodeAuthority.boundActionId,
          timeoutMs: requestTimeoutMs
        });
        if (staleReceipt.delivery !== "not_delivered"
            || staleReceipt.reason_code !== "stale_snapshot"
            || staleReceipt.successor?.snapshot_id !== snapshot.snapshot_id) {
          throw new Error(`Episode ${episodeIndex} did not reject prior episode authority as stale.`);
        }
        const gate = {
          type: "reset_authority",
          episode_index: episodeIndex,
          prior_snapshot_id: previousEpisodeAuthority.snapshotId,
          current_snapshot_id: snapshot.snapshot_id,
          delivery: staleReceipt.delivery,
          reason_code: staleReceipt.reason_code,
          verdict: "reset_authority_pass"
        };
        resetAuthorityGates.push(gate);
        events.push(gate);
      }

      let episodeStopReason = null;
      let episodeActionsAttempted = 0;
      let episodeActionsDelivered = 0;
      let episodeReadsCompleted = 0;
      let episodeAuthority = null;
      for (let actionIndex = 0; actionIndex < maxActions; actionIndex += 1) {
        for (const descriptor of eagerReads ? snapshot.reads : []) {
          try {
            const value = started.session.read({
              readId: descriptor.read_id,
              expectedSnapshotId: snapshot.snapshot_id
            });
            readsCompleted += 1;
            episodeReadsCompleted += 1;
            if (canonicalEvidence) {
              events.push({
                type: "read",
                episode_index: episodeIndex,
                action_index: actionIndex,
                canonical_read: canonicalizeReadResponse(value)
              });
            }
          } catch (error) {
            episodeStopReason = `read_failure:${descriptor.kind}:${error instanceof Error ? error.message : String(error)}`;
            break;
          }
        }
        if (episodeStopReason != null) break;
        const selected = chooseManagedPlayerEnvironmentAction(snapshot);
        if (selected == null) {
          episodeStopReason = snapshot.interaction.kind === "game_over"
            ? "game_over"
            : `visible_unsupported:${snapshot.interaction.kind}:${snapshot.completeness.missing.join(",")}`;
          break;
        }
        episodeAuthority ??= {
          snapshotId: snapshot.snapshot_id,
          interactionId: snapshot.interaction.interaction_id,
          boundActionId: selected.bound_action_id
        };
        const request = {
          requestId: `managed-pe-${String(episodeIndex + 1).padStart(4, "0")}-${String(actionIndex + 1).padStart(8, "0")}`,
          expectedSnapshotId: snapshot.snapshot_id,
          boundActionId: selected.bound_action_id,
          timeoutMs: requestTimeoutMs
        };
        const before = performance.now();
        const value = await started.session.submit(request);
        const after = performance.now();
        actionLatenciesMs.push(after - before);
        actionsAttempted += 1;
        episodeActionsAttempted += 1;
        if (value.delivery === "delivered") {
          actionsDelivered += 1;
          episodeActionsDelivered += 1;
        }
        if (canonicalEvidence) {
          events.push({
            type: "action",
            episode_index: episodeIndex,
            action_index: actionIndex,
            canonical_decision: canonicalizeSnapshot(snapshot),
            canonical_action: canonicalizeSelectedAction(snapshot, selected.bound_action_id),
            delivery: value.delivery,
            reason_code: value.reason_code ?? null,
            detail: value.detail ?? null,
            retry: value.retry ?? null,
            delivery_wall_ms: after - before,
            successor_kind: value.successor?.interaction?.kind ?? null,
            successor_status: value.successor?.status ?? null
          });
        }
        if (verifyIdempotency && actionIndex === 0) {
          const replay = await started.session.submit(request);
          if (JSON.stringify(replay) !== JSON.stringify(value)) {
            throw new Error(`Episode ${episodeIndex} did not replay the exact original receipt.`);
          }
          const gate = {
            type: "request_idempotency",
            episode_index: episodeIndex,
            request_id: request.requestId,
            delivery: replay.delivery,
            verdict: "exact_receipt_replay_pass"
          };
          idempotencyGates.push(gate);
          events.push(gate);
        }
        if (value.delivery === "unknown") {
          unknownObserved = true;
          episodeStopReason = `unknown:${value.reason_code ?? "unspecified"}`;
          break;
        }
        if (value.delivery !== "delivered" || value.successor == null) {
          episodeStopReason = `not_delivered:${value.reason_code ?? "unspecified"}`;
          break;
        }
        snapshot = value.successor;
      }
      episodeStopReason ??= "action_limit";
      episodes.push({
        episode_index: episodeIndex,
        requested_seed: requestedSeed,
        game_reported_seed: runIdentity.seed,
        seed_provenance: "game_reported_match",
        terminal: episodeStopReason,
        terminal_outcome: managedTerminalOutcome(snapshot),
        canonical_actions_attempted: episodeActionsAttempted,
        canonical_actions_delivered: episodeActionsDelivered,
        canonical_reads_completed: episodeReadsCompleted
      });
      previousEpisodeAuthority = episodeAuthority;
      if (episodeStopReason !== "game_over" && episodeStopReason !== "action_limit") {
        stopReason = episodeStopReason;
        break;
      }
    }
    decisionEnded = performance.now();
    if (stopReason == null) {
      stopReason = episodeCount === 1 ? episodes[0].terminal : "episodes_complete";
    }
  } catch (error) {
    failure = error instanceof Error ? error.message : String(error);
    stopReason ??= `exception:${failure}`;
    decisionEnded = performance.now();
  }
  const childMetricsAfter = await started.session.processMetrics(requestTimeoutMs);
  const nodeCpu = process.cpuUsage(nodeCpuBefore);
  const resources = sampler == null ? { samples: [], errors: [] } : await sampler.stop();
  const exit = await started.session.close();
  const delivered = actionsDelivered;
  const canonicalActionEvents = events.filter((event) => event.type === "action");
  const completeCanonicalDecisions = canonicalActionEvents.filter((event) =>
    event.canonical_decision?.completeness?.status === "complete").length;
  const partialCanonicalDecisions = canonicalActionEvents.length - completeCanonicalDecisions;
  const boundedCanonicalInformationComplete = canonicalEvidence
    ? canonicalActionEvents.length === actionsAttempted
      && partialCanonicalDecisions === 0
      && snapshot?.completeness?.status === "complete"
    : null;
  const windowSeconds = decisionStarted == null || decisionEnded == null
    ? null
    : (decisionEnded - decisionStarted) / 1000;
  const peakRssBytes = resources.samples.length === 0
    ? null
    : Math.max(...resources.samples.map((sample) => sample.rss_bytes));
  const report = {
    schema: "sts2.headless/managed-player-environment-probe-2",
    generated_at: new Date().toISOString(),
    status: failure != null
      ? "candidate_failure"
      : stopReason === "action_limit" || stopReason === "game_over" || stopReason === "episodes_complete"
        ? !canonicalEvidence
          ? "bounded_training_profile_measured"
          : boundedCanonicalInformationComplete
            ? "bounded_player_environment_measured"
            : "bounded_partial_player_environment_measured"
        : "fail_closed",
    headless: readProjectIdentity(root),
    system_identity: readSystemIdentity(),
    candidate: {
      manifest: started.runtime.manifest,
      build: started.runtime.build,
      runtime_identity: started.runtime.runtimeIdentity,
      adapter_runtime_instance_id: started.runtime.adapterRuntimeInstanceId,
      environment_fingerprint: started.environmentFingerprint
    },
    game_identity: {
      version: diskIdentity.release.version,
      commit: diskIdentity.release.commit,
      runtime_main_assembly_hash: diskIdentity.runtime_main_assembly_hash,
      original_sts2_sha256: diskIdentity.sts2_assembly.sha256
    },
    episode: {
      requested_seed: seed,
      game_reported_seed: episodeCount === 1 ? runIdentity?.seed ?? null : null,
      seed_provenance: episodes.length === episodeCount
        && episodes.every((episode) => episode.seed_provenance === "game_reported_match")
        ? "game_reported_match"
        : "incomplete",
      character,
      episodes_requested: episodeCount,
      episodes_completed: episodes.length,
      terminal: stopReason,
      failure,
      canonical_actions_attempted: actionsAttempted,
      canonical_actions_delivered: delivered,
      canonical_reads_completed: readsCompleted,
      canonical_information: {
        evidence_recorded: canonicalEvidence,
        action_decisions_observed: canonicalActionEvents.length,
        complete_action_decisions: completeCanonicalDecisions,
        partial_action_decisions: partialCanonicalDecisions,
        final_snapshot_complete: snapshot?.completeness?.status === "complete",
        bounded_complete: boundedCanonicalInformationComplete
      },
      reset_authority: {
        requested: verifyResetAuthority,
        repeat_seed: repeatSeed,
        reset_count: Math.max(0, episodes.length - 1),
        stale_rejections: resetAuthorityGates.length,
        all_passed: !verifyResetAuthority || resetAuthorityGates.length === Math.max(0, episodeCount - 1)
      },
      idempotency: {
        requested: verifyIdempotency,
        exact_receipt_replays: idempotencyGates.length,
        all_passed: !verifyIdempotency || idempotencyGates.length === episodes.length
      },
      episodes,
      final_snapshot: snapshot == null ? null : {
        status: snapshot.status,
        interaction_kind: snapshot.interaction.kind,
        terminal_outcome: managedTerminalOutcome(snapshot),
        completeness: snapshot.completeness
      }
    },
    performance: {
      unit: !canonicalEvidence
        ? "canonical_player_environment_decision_training_profile_unqualified"
        : boundedCanonicalInformationComplete
          ? "canonical_player_environment_decision_unqualified"
          : "canonical_player_environment_decision_partial_unqualified",
      profile: profileName,
      process_startup_seconds: decisionStarted == null ? null : (decisionStarted - processStarted) / 1000,
      decision_window_started_ms: decisionStarted,
      decision_window_ended_ms: decisionEnded,
      decision_window_started_epoch_ms: decisionStarted == null ? null : performance.timeOrigin + decisionStarted,
      decision_window_ended_epoch_ms: decisionEnded == null ? null : performance.timeOrigin + decisionEnded,
      decision_window_seconds: windowSeconds,
      reset_inclusive_decision_window_seconds: windowSeconds,
      delivered_decisions_per_second: windowSeconds > 0 ? delivered / windowSeconds : null,
      peak_rss_bytes: peakRssBytes,
      resource_samples: resources.samples,
      resource_sample_errors: resources.errors,
      stage_totals: started.session.performance(),
      action_latency_ms: latencySummary(actionLatenciesMs),
      mount_latency_ms: latencySummary(mountLatenciesMs),
      child_process: {
        cpu_ms: childMetricsAfter.cpu_total_ms - childMetricsBefore.cpu_total_ms,
        allocated_bytes: childMetricsAfter.allocated_bytes_total - childMetricsBefore.allocated_bytes_total,
        gc_collections: {
          gen0: childMetricsAfter.gen0_collections - childMetricsBefore.gen0_collections,
          gen1: childMetricsAfter.gen1_collections - childMetricsBefore.gen1_collections,
          gen2: childMetricsAfter.gen2_collections - childMetricsBefore.gen2_collections
        },
        final_working_set_bytes: childMetricsAfter.working_set_bytes,
        final_private_bytes: childMetricsAfter.private_bytes,
        final_managed_heap_bytes: childMetricsAfter.managed_heap_bytes
      },
      node_process: {
        cpu_ms: (nodeCpu.user + nodeCpu.system) / 1000,
        average_cpu_cores: windowSeconds > 0
          ? (nodeCpu.user + nodeCpu.system) / 1000 / (windowSeconds * 1000)
          : null
      },
      ablations: {
        identity_mode: identityMode,
        sdk_validation: validateSdk ? "every_step" : "off",
        eager_reads: eagerReads,
        canonical_evidence: canonicalEvidence,
        resource_sampling_interval_ms: resourceSamplingIntervalMs,
        quiet_diagnostics: quietDiagnostics,
        repeat_seed: repeatSeed,
        verify_reset_authority: verifyResetAuthority,
        verify_idempotency: verifyIdempotency
      }
    },
    process: {
      pid: started.runtime.process.pid,
      exit,
      diagnostics: started.runtime.process.diagnostics
    },
    events,
    verdict: {
      hard_shell: unknownObserved
        ? "unknown_delivery_fail_closed"
        : failure == null ? "bounded_integrity_pass" : "integrity_incomplete",
      semantic_conformance: "not_evaluated",
      h1_admission: "not_evaluated"
    },
    non_claims: [
      "A strict SDK-valid projection is not Player Environment semantic conformance.",
      "The managed decision source still contains manual projection and semantic shims.",
      "This bounded deterministic policy is not gameplay, training, or transfer evidence.",
      "Raw managed speed is not H1.0 speed until cross-Host semantic qualification succeeds."
    ]
  };
  let reportFile = null;
  if (evidenceRoot != null) {
    const directory = path.join(evidenceRoot, `managed-player-environment-${safeTimestamp()}`);
    mkdirSync(directory, { recursive: true });
    reportFile = path.join(directory, "report.json");
    writeFileSync(reportFile, `${JSON.stringify(report, null, 2)}\n`);
  }
  return { report, reportFile };
}

function capacityIdentity(report) {
  return {
    candidate_id: report.candidate.manifest.candidate_id,
    source_patch_sha256: report.candidate.build.source_patch_sha256,
    host_artifact_sha256: report.candidate.build.artifact_sha256,
    runtime_sts2_sha256: report.candidate.build.runtime_sts2_sha256,
    environment_fingerprint: report.candidate.environment_fingerprint,
    game_identity: report.game_identity
  };
}

export function summarizeManagedPlayerEnvironmentCapacityGroup(results, groupWallSeconds, parentPerformance = null) {
  if (!Array.isArray(results) || results.length === 0) {
    throw new TypeError("At least one managed Player Environment worker result is required.");
  }
  if (!Number.isFinite(groupWallSeconds) || groupWallSeconds <= 0) {
    throw new TypeError("groupWallSeconds must be positive.");
  }
  const reports = results.map((result) => result.report);
  const identities = reports.map(capacityIdentity);
  const expectedIdentity = JSON.stringify(identities[0]);
  if (identities.some((identity) => JSON.stringify(identity) !== expectedIdentity)) {
    throw new Error("Canonical capacity workers did not use one exact comparable artifact and game identity.");
  }
  const runtimeIds = reports.map((report) => report.candidate.adapter_runtime_instance_id);
  if (new Set(runtimeIds).size !== runtimeIds.length) {
    throw new Error("Canonical capacity workers did not report distinct runtime instance IDs.");
  }
  // Epoch timestamps stay comparable when each worker has an independent Node clock.
  const starts = reports.map((report) => report.performance.decision_window_started_epoch_ms
    ?? report.performance.decision_window_started_ms);
  const ends = reports.map((report) => report.performance.decision_window_ended_epoch_ms
    ?? report.performance.decision_window_ended_ms);
  const comparableWindow = [...starts, ...ends].every(Number.isFinite);
  const commonWindowSeconds = comparableWindow
    ? (Math.max(...ends) - Math.min(...starts)) / 1000
    : null;
  const decisions = reports.reduce(
    (sum, report) => sum + report.episode.canonical_actions_delivered,
    0
  );
  const reads = reports.reduce(
    (sum, report) => sum + report.episode.canonical_reads_completed,
    0
  );
  const sampledPeakRss = reports.map((report) => report.performance.peak_rss_bytes);
  const peakRssBytes = sampledPeakRss.every((value) => value == null)
    ? null
    : sampledPeakRss.reduce((sum, value) => sum + (value ?? 0), 0);
  const finalWorkingSetBytes = reports.reduce(
    (sum, report) => sum + (report.performance.child_process?.final_working_set_bytes ?? 0),
    0
  );
  const childCpuSeconds = reports.reduce(
    (sum, report) => sum + (report.performance.child_process?.cpu_ms ?? 0) / 1000,
    0
  );
  const nodeCpuSeconds = parentPerformance?.total_node_cpu_seconds
    ?? parentPerformance?.cpu_seconds
    ?? 0;
  const totalMeasuredCpuSeconds = childCpuSeconds + nodeCpuSeconds;
  const episodeSignatures = reports.map((report) => report.episode.episodes.map((episode) => ({
    requested_seed: episode.requested_seed,
    game_reported_seed: episode.game_reported_seed,
    terminal: episode.terminal,
    terminal_outcome: episode.terminal_outcome ?? null,
    canonical_actions_attempted: episode.canonical_actions_attempted,
    canonical_actions_delivered: episode.canonical_actions_delivered
  })));
  const expectedEpisodeSignatures = JSON.stringify(episodeSignatures[0]);
  const crossWorkerEpisodeConsistency = episodeSignatures.every((signatures) =>
    JSON.stringify(signatures) === expectedEpisodeSignatures);
  const measuredWorkerStatuses = new Set([
    "bounded_player_environment_measured",
    "bounded_partial_player_environment_measured",
    "bounded_training_profile_measured"
  ]);
  const workersMeasured = reports.every((report) =>
    measuredWorkerStatuses.has(report.status)
    && report.episode.failure == null
    && report.episode.seed_provenance === "game_reported_match"
    && report.episode.episodes_completed === report.episode.episodes_requested
    && report.episode.canonical_actions_attempted === report.episode.canonical_actions_delivered
    && report.episode.episodes.every((episode) =>
      episode.terminal === "game_over" || episode.terminal === "action_limit"))
    && crossWorkerEpisodeConsistency;
  const allWorkersInformationComplete = reports.every((report) =>
    report.status === "bounded_player_environment_measured");
  const allWorkersInformationUnrecorded = reports.every((report) =>
    report.status === "bounded_training_profile_measured");
  return {
    status: workersMeasured && commonWindowSeconds > 0
      ? allWorkersInformationUnrecorded
        ? "measured_training_profile_unqualified"
        : allWorkersInformationComplete
        ? "measured_canonical_unqualified"
        : "measured_canonical_partial_unqualified"
      : "measurement_incomplete",
    worker_count: reports.length,
    exact_identity: identities[0],
    runtime_instance_ids: runtimeIds,
    group_wall_seconds: groupWallSeconds,
    common_reset_inclusive_decision_window_seconds: commonWindowSeconds,
    delivered_canonical_decisions: decisions,
    completed_canonical_reads: reads,
    process_lifecycle_inclusive_canonical_decisions_per_second: decisions / groupWallSeconds,
    aggregate_reset_inclusive_canonical_decisions_per_second:
      commonWindowSeconds > 0 ? decisions / commonWindowSeconds : null,
    summed_worker_peak_rss_bytes: peakRssBytes,
    resource_sampling: peakRssBytes == null ? "not_recorded" : "recorded",
    summed_worker_final_working_set_bytes: finalWorkingSetBytes,
    summed_worker_observed_rss_growth_bytes: reports.reduce((sum, report) => {
      const samples = report.performance.resource_samples ?? [];
      return sum + (samples.length > 1
        ? samples.at(-1).rss_bytes - samples[0].rss_bytes
        : 0);
    }, 0),
    reliability: {
      episodes_completed: reports.reduce(
        (sum, report) => sum + report.episode.episodes_completed,
        0
      ),
      reset_count: reports.reduce(
        (sum, report) => sum + Math.max(0, report.episode.episodes_completed - 1),
        0
      ),
      unknown_deliveries: reports.reduce(
        (sum, report) => sum + (report.verdict?.hard_shell === "unknown_delivery_fail_closed" ? 1 : 0),
        0
      ),
      process_failures: reports.filter((report) => report.process.exit?.code !== 0).length,
      cross_worker_episode_consistency: crossWorkerEpisodeConsistency ? "pass" : "fail"
    },
    child_cpu_seconds: childCpuSeconds,
    node_cpu_seconds: nodeCpuSeconds,
    total_measured_cpu_seconds: totalMeasuredCpuSeconds,
    decisions_per_cpu_second: totalMeasuredCpuSeconds > 0 ? decisions / totalMeasuredCpuSeconds : null,
    average_measured_cpu_cores: commonWindowSeconds > 0
      ? totalMeasuredCpuSeconds / commonWindowSeconds
      : null,
    parent_process: parentPerformance,
    workers: reports.map((report) => ({
      status: report.status,
      runtime_instance_id: report.candidate.adapter_runtime_instance_id,
      episode: report.episode,
      performance: {
        process_startup_seconds: report.performance.process_startup_seconds,
        reset_inclusive_decision_window_seconds:
          report.performance.reset_inclusive_decision_window_seconds,
        delivered_decisions_per_second: report.performance.delivered_decisions_per_second,
        peak_rss_bytes: report.performance.peak_rss_bytes,
        resource_sample_errors: report.performance.resource_sample_errors,
        stage_totals: report.performance.stage_totals,
        child_process: report.performance.child_process,
        action_latency_ms: report.performance.action_latency_ms,
        mount_latency_ms: report.performance.mount_latency_ms
      },
      process_exit: report.process.exit,
      diagnostic_count: report.process.diagnostics.length,
      last_action: (() => {
        const action = report.events.findLast((event) => event.type === "action");
        return action == null ? null : {
          episode_index: action.episode_index,
          action_index: action.action_index,
          canonical_action: action.canonical_action,
          delivery: action.delivery,
          reason_code: action.reason_code,
          detail: action.detail
        };
      })()
    }))
  };
}

export function managedCapacityReportStatus(groups) {
  if (!Array.isArray(groups) || groups.length === 0) return "measurement_incomplete";
  const statuses = groups.map((group) => group.status);
  if (statuses.every((status) => status === "measured_training_profile_unqualified")) {
    return "measured_training_profile_unqualified";
  }
  const canonicalStatuses = new Set([
    "measured_canonical_unqualified",
    "measured_canonical_partial_unqualified"
  ]);
  if (statuses.every((status) => canonicalStatuses.has(status))) {
    return statuses.every((status) => status === "measured_canonical_unqualified")
      ? "measured_canonical_unqualified"
      : "measured_canonical_partial_unqualified";
  }
  return "measurement_incomplete";
}

export async function runManagedPlayerEnvironmentCapacity({
  root,
  candidateDirectory,
  diskIdentity,
  workerCounts = [1, 2, 4],
  maxActions = 300,
  episodesPerWorker = 3,
  seedPrefix = "H1PECAPACITY",
  character = "Ironclad",
  requestTimeoutMs = 10_000,
  evidenceRoot,
  profileName = "qualification",
  identityMode = "crypto",
  validateSdk = true,
  eagerReads = true,
  canonicalEvidence = true,
  resourceSamplingIntervalMs = 250,
  quietDiagnostics = false
}) {
  requirePositiveInteger(maxActions, "maxActions");
  requirePositiveInteger(episodesPerWorker, "episodesPerWorker");
  requirePositiveInteger(requestTimeoutMs, "requestTimeoutMs");
  if (!Array.isArray(workerCounts) || workerCounts.length === 0) {
    throw new TypeError("workerCounts must contain at least one positive integer.");
  }
  workerCounts.forEach((workerCount) => requirePositiveInteger(workerCount, "workerCount"));
  if (typeof seedPrefix !== "string" || seedPrefix.length === 0) {
    throw new TypeError("seedPrefix must be a non-empty string.");
  }
  if (typeof evidenceRoot !== "string" || evidenceRoot.length === 0) {
    throw new TypeError("evidenceRoot must be a non-empty path.");
  }

  const outputDirectory = path.join(evidenceRoot, `managed-player-environment-capacity-${safeTimestamp()}`);
  mkdirSync(outputDirectory, { recursive: true });
  const groups = [];
  for (const workerCount of workerCounts) {
    const groupStarted = performance.now();
    const parentCpuStarted = process.cpuUsage();
    const workers = await Promise.all(Array.from({ length: workerCount }, () =>
      runManagedPlayerEnvironmentProbe({
        root,
        candidateDirectory,
        diskIdentity,
        seed: seedPrefix,
        character,
        maxActions,
        episodeCount: episodesPerWorker,
        requestTimeoutMs,
        evidenceRoot: null,
        profileName,
        identityMode,
        validateSdk,
        eagerReads,
        canonicalEvidence,
        resourceSamplingIntervalMs,
        quietDiagnostics
      })));
    const groupWallSeconds = (performance.now() - groupStarted) / 1000;
    const parentCpu = process.cpuUsage(parentCpuStarted);
    groups.push(summarizeManagedPlayerEnvironmentCapacityGroup(
      workers,
      groupWallSeconds,
      {
        cpu_seconds: (parentCpu.user + parentCpu.system) / 1_000_000,
        total_node_cpu_seconds: (parentCpu.user + parentCpu.system) / 1_000_000,
        final_rss_bytes: process.memoryUsage().rss,
        average_cpu_cores: groupWallSeconds > 0
          ? (parentCpu.user + parentCpu.system) / 1_000_000 / groupWallSeconds
          : null
      }
    ));
  }
  const report = {
    schema: "sts2.headless/managed-player-environment-capacity-1",
    generated_at: new Date().toISOString(),
    status: managedCapacityReportStatus(groups),
    headless: readProjectIdentity(root),
    system_identity: readSystemIdentity(),
    worker_counts: workerCounts,
    episodes_per_worker: episodesPerWorker,
    max_actions_per_episode: maxActions,
    seed_prefix: seedPrefix,
    character,
    profile: {
      name: profileName,
      identity_mode: identityMode,
      sdk_validation: validateSdk ? "every_step" : "off",
      eager_reads: eagerReads,
      canonical_evidence: canonicalEvidence,
      resource_sampling_interval_ms: resourceSamplingIntervalMs,
      quiet_diagnostics: quietDiagnostics
    },
    groups,
    non_claims: [
      "Canonical adapter throughput is not cross-Host semantic conformance.",
      "A short reset-and-capacity ladder is not long-run reset or million-step reliability evidence.",
      "Information completeness is only evaluated when canonical evidence recording is enabled.",
      "The exact Host is an STPD operational baseline; this measurement does not grant formal H1.0 qualification.",
      "This deterministic policy is not gameplay quality, training, or transfer evidence."
    ]
  };
  const reportFile = path.join(outputDirectory, "report.json");
  writeFileSync(reportFile, `${JSON.stringify(report, null, 2)}\n`);
  return { report, reportFile, outputDirectory };
}
