import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { P1Observation } from "@rsgcsg/sts2-connector-client/p1-global";
import { comparePublicPoint, type FirstDifference } from "./compare.js";

/**
 * One Agent decision point of a recorded GLOBAL run: the model view the Agent was
 * offered and the choice it returned (null on the terminal observation). Read from the
 * run's original framed Agent messages, not from summaries.
 */
export interface TrajectoryPoint {
  index: number;
  observation: P1Observation;
  chosen: string | null;
  continuity_checks?: {
    decision_id: string;
    phase: "cancelled_before_execution" | "cancelled_after_execution" | "presentation";
    boundary: "before" | "after";
    source_event_id: string;
    observation: P1Observation;
  }[];
}
export interface Trajectory { schema: "sts2.p1/trajectory-1"; run_directory: string; points: TrajectoryPoint[] }

const withEnvelope = (observation: Record<string, unknown>, index: number): P1Observation => ({
  ...(observation as unknown as P1Observation),
  // Offered model views carry no envelope; complete publication is a Runtime precondition.
  envelope: { ...(observation.envelope as object ?? {}), occurrence: index, required_information_complete: true } as P1Observation["envelope"]
});

export function extractTrajectory(runDirectory: string): Trajectory {
  const events = readFileSync(join(runDirectory, "events.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line));
  const points: TrajectoryPoint[] = [];
  let offered: Record<string, unknown> | null = null;
  for (const event of events) {
    if (event.kind !== "global_agent_frame" || !event.payload?.artifact?.path) continue;
    const frame = JSON.parse(readFileSync(join(runDirectory, event.payload.artifact.path), "utf8"));
    if (frame.message_type === "consume") offered = frame.input.observation;
    else if (frame.message_type === "directive" && offered) {
      const directive = frame.output.directive;
      points.push({ index: points.length, observation: withEnvelope(offered, points.length),
        chosen: directive.type === "act" ? String(directive.selection.action_id) : null });
      offered = null;
    }
  }
  return { schema: "sts2.p1/trajectory-1", run_directory: runDirectory, points };
}

/** The unique choice in `observation` that is publicly equivalent to `reference`'s choice. */
export function matchReferenceChoice(reference: TrajectoryPoint, observation: P1Observation):
  { choice_id: string | null; verdict: "equal" | "different" | "incomparable" | "ambiguous"; difference: FirstDifference | null } {
  const facts = comparePublicPoint(reference.observation, observation, null, null);
  if (facts.verdict !== "equal") return { choice_id: null, verdict: facts.verdict, difference: facts.difference };
  if (reference.chosen === null) return { choice_id: null, verdict: "equal", difference: null };
  const matches = observation.catalog.filter(choice =>
    comparePublicPoint(reference.observation, observation, reference.chosen, choice.choice_id).verdict === "equal");
  if (matches.length === 1) return { choice_id: matches[0]!.choice_id, verdict: "equal", difference: null };
  return { choice_id: null, verdict: matches.length ? "ambiguous" : "different",
    difference: { path: "$.chosen", left: reference.chosen, right: matches.map(m => m.choice_id), reason: matches.length ? "several_equivalent_choices" : "no_equivalent_choice" } };
}
