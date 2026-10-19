#!/usr/bin/env node
// Replay a recorded GLOBAL trajectory on another Host (normally Reference -> Managed) and
// report the first divergence. P1 simple Agent protocol (sts2.p1/agent-port-1).
//
//   replay_agent.mjs <trajectory.json> <report.jsonl>
//
// At each decision it requires the current observation to be publicly equivalent to the
// recorded one (full public graph, injective alias renaming) and picks the unique choice
// equivalent to the recorded choice. Otherwise it writes the divergence and abstains, so
// the Runtime stops. A divergence needs owning-source investigation on either Host.
import { appendFileSync, readFileSync } from "node:fs";
import readline from "node:readline";
import { pathToFileURL } from "node:url";

const [trajectoryPath, reportPath] = process.argv.slice(2);
const { matchReferenceChoice } = await import(new URL("../../dist/p1-global/trajectory.js", import.meta.url).href);
const trajectory = JSON.parse(readFileSync(trajectoryPath, "utf8"));
const log = (value) => appendFileSync(reportPath, JSON.stringify(value) + "\n");
let index = 0, diverged = false, terminalObservation = null, prefixComplete = false;
const completeView = observation => ({ ...observation,
  envelope: { ...observation.envelope, required_information_complete: true } });
function cancellationContinuity(reference, observation) {
  for (const check of reference.continuity_checks ?? []) {
    const match = check.observation == null
      ? { verdict: "incomparable", difference: { path: "$.continuity_checks.observation",
        left: null, right: null, reason: "cancellation_observation_missing" } }
      : matchReferenceChoice({ index, observation: check.observation, chosen: null }, observation);
    log({ type: "cancellation_continuity", decision_id: check.decision_id, index, phase: check.phase,
      source_event_id: check.source_event_id, verdict: match.verdict, difference: match.difference });
    if (match.verdict !== "equal") return false;
  }
  return true;
}
function cancellationManifestComplete() {
  const checks = trajectory.points.flatMap((point, index) =>
    (point.continuity_checks ?? []).map(check => ({ ...check, index })));
  if (trajectory.cancellation_count === undefined && trajectory.native_cancellations === undefined)
    return checks.length === 0;
  const declared = trajectory.native_cancellations;
  if (!Number.isInteger(trajectory.cancellation_count) || trajectory.cancellation_count < 0
      || !Array.isArray(declared) || declared.length !== trajectory.cancellation_count
      || checks.length !== declared.length || new Set(declared.map(item => item.decision_id)).size !== declared.length)
    return false;
  return declared.every(item => typeof item.decision_id === "string" && item.decision_id.length > 0
    && typeof item.source_event_id === "string" && item.source_event_id.length > 0
    && ["cancelled_before_execution", "cancelled_after_execution"].includes(item.phase)
    && checks.filter(check => check.decision_id === item.decision_id && check.source_event_id === item.source_event_id
      && check.phase === item.phase && check.index === item.comparison_point_index).length === 1);
}
log({ type: "start", reference: trajectory.run_directory, points: trajectory.points.length });
if (!cancellationManifestComplete()) {
  diverged = true;
  log({ type: "divergence", index, reason: "cancellation_manifest_mismatch" });
}
for await (const line of readline.createInterface({ input: process.stdin })) {
  const msg = JSON.parse(line);
  if (msg.type === "hello") {
    console.log(JSON.stringify({ type: "ready", protocol: msg.protocol, agent_id: "p1-reference-replay", agent_version: "1" }));
  } else if (msg.type === "observation" && !msg.requires_choice) {
    terminalObservation = msg.observation?.status === "terminal" ? completeView(msg.observation) : null;
  } else if (msg.type === "observation" && msg.requires_choice) {
    terminalObservation = null;
    if (diverged) { console.log(JSON.stringify({ type: "abstain", reason: "prior_divergence" })); continue; }
    const reference = trajectory.points[index];
    const observation = completeView(msg.observation);
    if (!reference) { diverged = true; log({ type: "divergence", index, reason: "reference_ended_before_replay" }); console.log(JSON.stringify({ type: "abstain", reason: "reference_ended" })); continue; }
    if (!cancellationContinuity(reference, observation)) {
      diverged = true;
      log({ type: "divergence", index, reason: "cancellation_continuity_difference" });
      console.log(JSON.stringify({ type: "abstain", reason: `divergence_at_${index}` }));
      continue;
    }
    const match = matchReferenceChoice(reference, observation);
    if (!diverged && match.verdict === "equal" && reference.chosen === null && index === trajectory.points.length - 1
      && trajectory.scope?.kind === "bounded_native_semantic_prefix" && reference.observation.status === "decision") {
      // Compare the actual final successor, then stop through the existing
      // abstain path. This proves a bounded replay prefix, never a whole game.
      prefixComplete = true;
      log({ type: "prefix_end", index, reference_points: trajectory.points.length,
        replay_reached_reference_prefix: true, public_successor_equal: true });
      console.log(JSON.stringify({ type: "abstain", reason: "recorded_prefix_complete" }));
      continue;
    }
    if (match.verdict !== "equal" || match.choice_id === null) {
      diverged = true;
      log({ type: "divergence", index, verdict: match.verdict, difference: match.difference,
        reference_scene: reference.observation.scene?.native_kind, replay_scene: observation.scene?.native_kind });
      console.log(JSON.stringify({ type: "abstain", reason: `divergence_at_${index}` }));
      continue;
    }
    log({ type: "match", index, scene: observation.scene?.native_kind, reference_choice: reference.chosen, replay_choice: match.choice_id });
    index += 1;
    console.log(JSON.stringify({ type: "choose", occurrence: msg.observation.envelope.occurrence, choice_id: match.choice_id }));
  } else if (msg.type === "episode_end") {
    const reference = trajectory.points[index];
    let reason = null, comparison = null;
    if (diverged) reason = "prior_divergence";
    else if (msg.censor_reason != null) reason = "replay_censored";
    else if (!reference || index !== trajectory.points.length - 1 || reference.chosen !== null ||
      reference.observation.status !== "terminal" || !["win", "loss"].includes(reference.observation.terminal?.outcome))
      reason = "reference_not_final_known_terminal";
    else if (!terminalObservation || !["win", "loss"].includes(terminalObservation.terminal?.outcome))
      reason = "replay_terminal_observation_missing";
    else if (reference.observation.terminal.outcome !== terminalObservation.terminal.outcome ||
      msg.outcome !== terminalObservation.terminal.outcome) reason = "terminal_outcome_mismatch";
    else if (!cancellationContinuity(reference, terminalObservation)) reason = "cancellation_continuity_difference";
    else {
      comparison = matchReferenceChoice(reference, terminalObservation);
      if (comparison.verdict !== "equal") reason = "public_terminal_difference";
    }
    log({ type: "end", index, reference_points: trajectory.points.length,
      replay_reached_reference_prefix: prefixComplete && !diverged,
      replay_reached_reference_terminal: reason === null && comparison?.verdict === "equal",
      outcome: msg.outcome ?? null, reason, comparison });
    break;
  }
}
