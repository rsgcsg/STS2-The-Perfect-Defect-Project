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
let index = 0, diverged = false, terminalObservation = null;
const completeView = observation => ({ ...observation,
  envelope: { ...observation.envelope, required_information_complete: true } });
log({ type: "start", reference: trajectory.run_directory, points: trajectory.points.length });
for await (const line of readline.createInterface({ input: process.stdin })) {
  const msg = JSON.parse(line);
  if (msg.type === "hello") {
    console.log(JSON.stringify({ type: "ready", protocol: msg.protocol, agent_id: "p1-reference-replay", agent_version: "1" }));
  } else if (msg.type === "observation" && !msg.requires_choice) {
    terminalObservation = msg.observation?.status === "terminal" ? completeView(msg.observation) : null;
  } else if (msg.type === "observation" && msg.requires_choice) {
    terminalObservation = null;
    const reference = trajectory.points[index];
    const observation = completeView(msg.observation);
    if (!reference) { diverged = true; log({ type: "divergence", index, reason: "reference_ended_before_replay" }); console.log(JSON.stringify({ type: "abstain", reason: "reference_ended" })); continue; }
    const match = matchReferenceChoice(reference, observation);
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
    else {
      comparison = matchReferenceChoice(reference, terminalObservation);
      if (comparison.verdict !== "equal") reason = "public_terminal_difference";
    }
    log({ type: "end", index, reference_points: trajectory.points.length,
      replay_reached_reference_terminal: reason === null && comparison?.verdict === "equal",
      outcome: msg.outcome ?? null, reason, comparison });
    break;
  }
}
