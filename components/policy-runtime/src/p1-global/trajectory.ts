import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { P1Observation, P1Result } from "@rsgcsg/sts2-connector-client/p1-global";
import { comparePublicPoint, type FirstDifference } from "./compare.js";
import { readGlobalArtifact } from "../global-artifact-storage.js";

/**
 * One Agent decision point of a recorded GLOBAL run: the model view the Agent was
 * offered and the choice it returned (null on the terminal observation). Read from the
 * run's original framed Agent messages, not from summaries.
 */
export interface TrajectoryPoint {
  index: number;
  observation: P1Observation;
  chosen: string | null;
  /** Original recorded Runtime result, never derived from native Human input. */
  result?: P1Result;
  continuity_checks?: {
    decision_id: string;
    phase: "cancelled_before_execution" | "cancelled_after_execution" | "presentation";
    boundary: "before" | "after";
    source_event_id: string;
    observation: P1Observation;
  }[];
}
export interface Trajectory { schema: "sts2.p1/trajectory-1"; run_directory: string; points: TrajectoryPoint[];
  result_source?: "runtime_global_result" }

const withEnvelope = (observation: Record<string, unknown>, index: number): P1Observation => ({
  ...(observation as unknown as P1Observation),
  // Offered model views carry no envelope; complete publication is a Runtime precondition.
  envelope: { ...(observation.envelope as object ?? {}), occurrence: index, required_information_complete: true } as P1Observation["envelope"]
});

export async function extractTrajectory(runDirectory: string): Promise<Trajectory> {
  const events = readFileSync(join(runDirectory, "events.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line));
  const points: TrajectoryPoint[] = [];
  let offered: Record<string, unknown> | null = null;
  let occurrence: number | null = null;
  let acquisition: string | null = null;
  let offeredAcquisition: string | null = null;
  let awaitingResult: TrajectoryPoint | null = null;
  let awaitingAcquisition: string | null = null;
  for (const event of events) {
    if (event.kind === "global_input") {
      occurrence = event.payload.occurrence;
      acquisition = event.payload.acquisition_id;
    }
    if (event.kind === "global_result") {
      const result = event.payload.result as P1Result;
      if (!awaitingResult || result.choice_id !== awaitingResult.chosen)
        throw new Error("trajectory_result_choice_binding");
      if (event.payload.acquisition_id !== awaitingAcquisition)
        throw new Error("trajectory_result_acquisition_binding");
      if (result.occurrence !== awaitingResult.observation.envelope.occurrence)
        throw new Error("trajectory_result_occurrence_binding");
      if (awaitingResult.result) throw new Error("trajectory_duplicate_result");
      awaitingResult.result = result;
      awaitingResult = null;
      awaitingAcquisition = null;
      continue;
    }
    if (event.kind !== "global_agent_frame" || !event.payload?.artifact?.path) continue;
    const frame = JSON.parse((await readGlobalArtifact(runDirectory, event.payload.artifact)).toString("utf8"));
    if (frame.message_type === "consume") {
      if (frame.input.acquisition_id !== acquisition || !Number.isSafeInteger(occurrence))
        throw new Error("trajectory_consume_input_binding");
      offered = frame.input.observation;
      offeredAcquisition = frame.input.acquisition_id;
    }
    else if (frame.message_type === "directive" && offered) {
      const directive = frame.output.directive;
      if (directive.type === "act" && directive.basis_acquisition_id !== offeredAcquisition)
        throw new Error("trajectory_directive_acquisition_binding");
      const point = { index: points.length, observation: withEnvelope(offered, occurrence ?? points.length),
        chosen: directive.type === "act" ? String(directive.selection.action_id) : null };
      points.push(point);
      awaitingResult = point.chosen === null ? null : point;
      awaitingAcquisition = point.chosen === null ? null : offeredAcquisition;
      offered = null;
    }
  }
  return { schema: "sts2.p1/trajectory-1", run_directory: runDirectory, points, result_source: "runtime_global_result" };
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
