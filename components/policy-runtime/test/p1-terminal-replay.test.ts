import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { describe, expect, it } from "vitest";
import { createSession, reduce, modelView } from "@rsgcsg/sts2-connector-client/p1-global";
import { comparePublicPoint } from "../src/p1-global/compare.js";

const replay = fileURLToPath(new URL("../examples/p1/replay_agent.mjs", import.meta.url));
const bridge = fileURLToPath(new URL("../examples/p1/session-bridge.mjs", import.meta.url));
const terminal = () => {
  const capture = JSON.parse(readFileSync(new URL("../../connector/sdk/typescript/test/fixtures/p1-global/managed-game_over.json", import.meta.url), "utf8"));
  const projected = reduce(createSession("terminal-test", "managed"), capture);
  expect(projected.complete).toBe(true);
  expect(projected.observation.status).toBe("terminal");
  return projected.observation;
};
const point = (observation: any) => ({ index: 0, observation, chosen: null });
const hello = { type: "hello", protocol: "sts2.p1/agent-port-1" };
const inform = (observation: any) => ({ type: "observation", observation, requires_choice: false });
function cancellationMetadata(points: any[]) {
  const cancellations = new Map<string, any>();
  points.forEach((point, index) => (point.continuity_checks ?? []).forEach((check: any) => {
    if (!cancellations.has(check.decision_id)) cancellations.set(check.decision_id, {
      decision_id: check.decision_id, action_witness_id: `witness-${check.decision_id}`, phase: check.phase,
      source_event_id: `terminal-${check.decision_id}`, reason: "native_cancel", continuity_checkpoints: [],
    });
    cancellations.get(check.decision_id).continuity_checkpoints.push({ boundary: check.boundary,
      source_event_id: check.source_event_id, comparison_point_index: index });
  }));
  return cancellations.size ? { cancellation_count: cancellations.size, native_cancellations: [...cancellations.values()] } : {};
}
function scenario(points: any[], messages: any[], throughBridge = false, metadata = {}) {
  const directory = mkdtempSync(join(tmpdir(), "p1-terminal-"));
  try {
    const trajectory = join(directory, "trajectory.json"), report = join(directory, "report.jsonl");
    writeFileSync(trajectory, JSON.stringify({ schema: "sts2.p1/trajectory-1", run_directory: "fixture", points,
      ...cancellationMetadata(points), ...metadata }));
    let command = [replay, trajectory, report];
    if (throughBridge) {
      const manifest = join(directory, "agent.json");
      writeFileSync(manifest, JSON.stringify({ manifest_id: "terminal-bridge", adapter: "p1-global-r1", input: { input_spec: "test" } }));
      command = [bridge, manifest, process.execPath, replay, trajectory, report];
    }
    const child = spawnSync(process.execPath, command, {
      input: messages.map(message => JSON.stringify(message)).join("\n") + "\n", encoding: "utf8", timeout: 5000,
    });
    expect(child.status, child.stderr || String(child.error)).toBe(0);
    const rows = readFileSync(report, "utf8").trim().split("\n").map(line => JSON.parse(line));
    return { end: rows.at(-1), rows, replies: child.stdout.trim().split("\n").map(line => JSON.parse(line)) };
  } finally { rmSync(directory, { recursive: true, force: true }); }
}

describe("P1 replay compares the actual final public observation", () => {
  it("accepts an equal final terminal, not merely a null action", () => {
    const observation = terminal();
    const result = scenario([point(observation)], [hello, inform(observation), { type: "episode_end", outcome: "loss", censor_reason: null }]);
    expect(result.end.replay_reached_reference_terminal).toBe(true);
    expect(result.end.comparison.verdict).toBe("equal");
  });
  it.each(["other_outcome", "end_disagrees", "missing_observation", "missing_terminal", "nonterminal_null_choice", "not_last", "censored", "changed_public_fact"])("rejects %s", variant => {
    const reference: any = terminal(), actual: any = structuredClone(reference);
    const points = [point(reference)];
    const end: any = { type: "episode_end", outcome: "loss", censor_reason: null };
    if (variant === "other_outcome") { actual.terminal.outcome = "win"; end.outcome = "win"; }
    if (variant === "end_disagrees") end.outcome = "win";
    if (variant === "missing_terminal") actual.terminal = null;
    if (variant === "nonterminal_null_choice") reference.status = "decision";
    if (variant === "not_last") points.push(point(reference));
    if (variant === "censored") end.censor_reason = "budget";
    if (variant === "changed_public_fact") actual.player.content.gold += 1;
    const messages = [hello, ...(variant === "missing_observation" ? [] : [inform(actual)]), end];
    expect(scenario(points, messages).end.replay_reached_reference_terminal).toBe(false);
  });
  it("never turns a previous divergence into terminal success", () => {
    const observation = terminal();
    const result = scenario([point(observation)], [hello,
      { type: "observation", observation, requires_choice: true }, inform(observation),
      { type: "episode_end", outcome: "loss", censor_reason: null }]);
    expect(result.end.reason).toBe("prior_divergence");
    expect(result.end.replay_reached_reference_terminal).toBe(false);
  });
  it("does not compare missing terminal outcomes as equal", () => {
    const observation: any = terminal(); observation.terminal = null;
    expect(comparePublicPoint(observation, observation, null, null).verdict).toBe("incomparable");
  });
  it.each([false, true])("only accepts an explicitly bounded final public successor (changed=%s)", changed => {
    const capture = JSON.parse(readFileSync(new URL("../../connector/sdk/typescript/test/fixtures/p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));
    const reference = reduce(createSession("short-test", "reference"), capture).observation;
    const actual = structuredClone(reference);
    if (changed) (actual.player.content as any).gold += 1;
    const result = scenario([point(reference)], [hello,
      { type: "observation", observation: actual, requires_choice: true },
      { type: "episode_end", outcome: null, censor_reason: "agent_abstained" }], false,
    { scope: { kind: "bounded_native_semantic_prefix" }, origin: "native_semantic_recording" });
    expect(result.end.replay_reached_reference_prefix).toBe(!changed);
    expect(result.end.replay_reached_reference_terminal).toBe(false);
    if (!changed) expect(result.replies.at(-1)).toEqual({ type: "abstain", reason: "recorded_prefix_complete" });
  });
  it("delivers the producer model view through the real session bridge before close", () => {
    const observation = terminal();
    const frame = (message_type: string, input = {}) => ({ schema: "sts2.policy-runtime/agent-session-1", message_type,
      session_id: "session", recovery_epoch: 0, request_id: message_type, input });
    const result = scenario([point(observation)], [
      frame("consume", { consumption_unit: { id: "terminal-unit" }, observation: modelView(observation),
        acquisition_id: "terminal-acquisition", continuity_token: "terminal-token" }),
      frame("consume_ack"), frame("next", { continuity_token: "terminal-token" }),
    ], true);
    expect(result.end.replay_reached_reference_terminal).toBe(true);
    expect(result.replies.find(reply => reply.message_type === "directive").output.directive.type).toBe("close");
  });
});

describe("P1 cancellation continuity uses the actual replay observation", () => {
  const decision = () => {
    const capture = JSON.parse(readFileSync(new URL("../../connector/sdk/typescript/test/fixtures/p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));
    const result = reduce(createSession("continuity-test", "reference"), capture);
    expect(result.complete).toBe(true);
    return result.observation;
  };
  const checks = (observation: any, phase = "cancelled_before_execution") => ["before", "after"].map((boundary, index) => ({
    decision_id: "cancel", phase, boundary, source_event_id: `capture-event-${index}`, observation: structuredClone(observation),
  }));
  const bounded = { scope: { kind: "bounded_native_semantic_prefix" }, origin: "native_semantic_recording" };
  const observe = (observation: any) => ({ type: "observation", observation, requires_choice: true });

  it.each(["cancelled_before_execution", "cancelled_after_execution"])("checks both boundaries before choosing the next successful action (%s)", phase => {
    const observation = decision();
    const reference = { ...point(observation), chosen: observation.catalog[0]!.choice_id, continuity_checks: checks(observation, phase) };
    const result = scenario([reference, point(observation)], [hello, observe(observation), observe(observation),
      { type: "episode_end", outcome: null, censor_reason: "agent_abstained" }], false, bounded);
    const checked = result.rows.filter(row => row.type === "cancellation_continuity");
    expect(checked.map(row => [row.decision_id, row.phase, row.boundary, row.verdict])).toEqual([
      ["cancel", phase, "before", "equal"], ["cancel", phase, "after", "equal"],
    ]);
    expect(result.replies.some(reply => reply.type === "choose")).toBe(true);
    expect(result.end.replay_reached_reference_prefix).toBe(true);
  });

  it.each([0, 1])("rejects a changed cancellation fact even when the successful point matches (check %s)", changed => {
    const observation = decision(), continuity = checks(observation);
    continuity[changed]!.observation.player.content.gold = 1098;
    const reference = { ...point(observation), chosen: observation.catalog[0]!.choice_id, continuity_checks: continuity };
    const result = scenario([reference], [hello, observe(observation)]);
    expect(result.replies.at(-1).type).toBe("abstain");
    expect(result.replies.some(reply => reply.type === "choose")).toBe(false);
    const failed = result.rows.find(row => row.type === "cancellation_continuity" && row.verdict !== "equal");
    expect(failed.decision_id).toBe("cancel");
    expect(failed.boundary).toBe(changed === 0 ? "before" : "after");
    expect(failed.difference).not.toBeNull();
  });

  it.each(["unsupported", "incomplete"])("does not promote the original before observation (%s)", defect => {
    const observation = decision(), continuity = checks(observation);
    if (defect === "unsupported") continuity[0]!.observation.status = "unsupported";
    else continuity[0]!.observation.envelope.required_information_complete = false;
    const result = scenario([{ ...point(observation), continuity_checks: continuity }], [hello, observe(observation)], false, bounded);
    expect(result.replies.at(-1).type).toBe("abstain");
    expect(result.rows.find(row => row.type === "cancellation_continuity").verdict).not.toBe("equal");
  });

  it.each(["prefix", "terminal"])("compares before and after at their separate actual points ending at %s", boundary => {
    const first = decision(), last = boundary === "terminal" ? terminal() : structuredClone(first);
    const [before, after] = checks(first, "cancelled_after_execution");
    after!.observation = last;
    const result = scenario([
      { ...point(first), chosen: first.catalog[0]!.choice_id, continuity_checks: [before] },
      { ...point(last), index: 1, continuity_checks: [after] },
    ], [hello, observe(first), ...(boundary === "terminal" ? [inform(last)] : [observe(last)]),
      { type: "episode_end", outcome: boundary === "terminal" ? "loss" : null,
        censor_reason: boundary === "terminal" ? null : "agent_abstained" }], false, bounded);
    expect(result.rows.filter(row => row.type === "cancellation_continuity").map(row => [row.boundary, row.index, row.verdict]))
      .toEqual([["before", 0, "equal"], ["after", 1, "equal"]]);
    expect(boundary === "terminal" ? result.end.replay_reached_reference_terminal : result.end.replay_reached_reference_prefix).toBe(true);
  });

  it.each(["before", "after"])("rejects an omitted %s checkpoint even with the other boundary intact", boundary => {
    const observation = terminal(), continuity = checks(observation);
    const reference = { ...point(observation), continuity_checks: continuity };
    const metadata = cancellationMetadata([reference]);
    reference.continuity_checks = continuity.filter(check => check.boundary !== boundary);
    const result = scenario([reference], [hello, inform(observation), { type: "episode_end", outcome: "loss", censor_reason: null }], false, metadata);
    expect(result.end.replay_reached_reference_terminal).toBe(false);
    expect(result.rows.find(row => row.type === "divergence").reason).toBe("cancellation_manifest_mismatch");
  });

  it.each([false, true])("checks cancellations at the final bounded decision before prefix_end (changed=%s)", changed => {
    const observation = decision(), continuity = checks(observation);
    if (changed) continuity[1]!.observation.player.content.gold += 1;
    const result = scenario([{ ...point(observation), continuity_checks: continuity }], [hello, observe(observation),
      { type: "episode_end", outcome: null, censor_reason: "agent_abstained" }], false, bounded);
    expect(result.end.replay_reached_reference_prefix).toBe(!changed);
    expect(result.rows.some(row => row.type === "prefix_end")).toBe(!changed);
    expect(result.rows.filter(row => row.type === "cancellation_continuity")).toHaveLength(2);
  });

  it.each([false, true])("checks cancellations against the actual final terminal before success (changed=%s)", changed => {
    const observation: any = terminal(), continuity = checks(observation);
    if (changed) continuity[1]!.observation.player.content.gold += 1;
    const result = scenario([{ ...point(observation), continuity_checks: continuity }], [hello, inform(observation),
      { type: "episode_end", outcome: "loss", censor_reason: null }]);
    expect(result.end.replay_reached_reference_terminal).toBe(!changed);
    expect(result.rows.filter(row => row.type === "cancellation_continuity")).toHaveLength(2);
    if (changed) expect(result.end.reason).toBe("cancellation_continuity_difference");
  });

  it.each(["prefix", "terminal"])("rejects a missing cancellation observation at the final %s", boundary => {
    const observation = boundary === "terminal" ? terminal() : decision();
    const continuity = checks(observation); delete (continuity[0] as any).observation;
    const result = scenario([{ ...point(observation), continuity_checks: continuity }], boundary === "terminal"
      ? [hello, inform(observation), { type: "episode_end", outcome: "loss", censor_reason: null }]
      : [hello, observe(observation), { type: "episode_end", outcome: null, censor_reason: "agent_abstained" }], false, bounded);
    expect(result.end.replay_reached_reference_prefix).toBe(false);
    expect(result.end.replay_reached_reference_terminal).toBe(false);
    expect(result.rows.find(row => row.type === "cancellation_continuity").verdict).toBe("incomparable");
  });

  it.each(["missing", "extra", "duplicate", "wrong_index", "wrong_event", "count"])("rejects %s cancellation attachment metadata", defect => {
    const observation = terminal(), continuity = checks(observation);
    const reference = { ...point(observation), continuity_checks: continuity };
    const declared = cancellationMetadata([reference]).native_cancellations!;
    let count = declared.length;
    if (defect === "missing") reference.continuity_checks = [];
    if (defect === "extra") reference.continuity_checks.push({ ...continuity[0]!, decision_id: "extra" });
    if (defect === "duplicate") reference.continuity_checks[1] = reference.continuity_checks[0]!;
    if (defect === "wrong_index") declared[0]!.continuity_checkpoints[0].comparison_point_index = 1;
    if (defect === "wrong_event") declared[0]!.continuity_checkpoints[0].source_event_id = "foreign-event";
    if (defect === "count") count += 1;
    const result = scenario([reference], [hello, inform(observation), { type: "episode_end", outcome: "loss", censor_reason: null }],
      false, { cancellation_count: count, native_cancellations: declared });
    expect(result.end.replay_reached_reference_terminal).toBe(false);
    expect(result.rows.find(row => row.type === "divergence").reason).toBe("cancellation_manifest_mismatch");
  });

  it("never accepts a final prefix when declared cancellations were omitted from its point", () => {
    const observation = decision();
    const result = scenario([point(observation)], [hello, observe(observation),
      { type: "episode_end", outcome: null, censor_reason: "agent_abstained" }], false,
    { ...bounded, cancellation_count: 1, native_cancellations: [{ decision_id: "cancel", source_event_id: "event",
      phase: "cancelled_before_execution", continuity_checkpoints: [{ boundary: "before", source_event_id: "capture-before", comparison_point_index: 0 },
        { boundary: "after", source_event_id: "capture-after", comparison_point_index: 0 }] }] });
    expect(result.end.replay_reached_reference_prefix).toBe(false);
    expect(result.rows.some(row => row.type === "prefix_end")).toBe(false);
    expect(result.replies.at(-1).type).toBe("abstain");
  });
});
