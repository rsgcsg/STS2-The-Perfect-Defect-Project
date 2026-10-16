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
function scenario(points: any[], messages: any[], throughBridge = false) {
  const directory = mkdtempSync(join(tmpdir(), "p1-terminal-"));
  try {
    const trajectory = join(directory, "trajectory.json"), report = join(directory, "report.jsonl");
    writeFileSync(trajectory, JSON.stringify({ schema: "sts2.p1/trajectory-1", run_directory: "fixture", points }));
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
    return { end: rows.at(-1), replies: child.stdout.trim().split("\n").map(line => JSON.parse(line)) };
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
