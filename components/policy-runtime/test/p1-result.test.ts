import { expect, it } from "vitest";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { applyCommitment, commitmentView, createSession, reduce, type P1Observation, type P1Result } from "@rsgcsg/sts2-connector-client/p1-global";
// Shared native-shape conformance fixture, not a second reducer.
import { rewardForwardPair } from "../../connector/sdk/typescript/test/fixtures/reward-forward.mjs";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { comparePublicResult } from "../src/p1-global/compare.js";
import { extractTrajectory } from "../src/p1-global/trajectory.js";
import { buildP1Result } from "../src/p1-result.js";

it("compares the actual Runtime-produced serialized Result and retains every literal field", async () => {
  const f = await globalRuntimeFixture();
  try {
    await f.runtime.tick(); await f.runtime.tick();
    const original = (await f.events()).find(event => event.kind === "global_result").payload.result as P1Result;
    const trajectory = await extractTrajectory(f.evidence.directory), point = trajectory.points[0]!;
    expect(trajectory.result_source).toBe("runtime_global_result");
    expect(point.result).toEqual(original);
    expect(point.observation.envelope.occurrence).toBe(original.occurrence);
    const eventPath = join(f.evidence.directory, "events.jsonl"), eventBytes = await readFile(eventPath);
    for (const [field, expected] of [["acquisition_id", "acquisition"], ["occurrence", "occurrence"]]) {
      const changed = eventBytes.toString().trim().split("\n").map(line => JSON.parse(line));
      const event = changed.find(event => event.kind === "global_result");
      if (field === "acquisition_id") event.payload.acquisition_id = "foreign-acquisition";
      else event.payload.result.occurrence++;
      await writeFile(eventPath, changed.map(event => JSON.stringify(event)).join("\n") + "\n");
      await expect(extractTrajectory(f.evidence.directory)).rejects.toThrow(`trajectory_result_${expected}_binding`);
    }
    await writeFile(eventPath, eventBytes);
    expect(comparePublicResult(point.observation, point.observation, original, structuredClone(original)).verdict).toBe("equal");
    const changes: ((result: any) => void)[] = [
      result => result.occurrence++, result => { result.status = "not_started"; },
      result => { result.steps[0].origin = "fold"; }, result => { result.steps[0].native_verb += "changed"; },
      result => { result.steps[0].label += "changed"; }, result => { result.steps[0].delivery = "not_delivered"; },
      result => { result.steps[0].reason_code = "changed"; }, result => { result.steps[0].index++; },
      result => { result.commitment_delta = {}; }, result => { result.future = { entity_id: "literal" }; }
    ];
    for (const change of changes) {
      const changed = structuredClone(original); change(changed);
      expect(comparePublicResult(point.observation, point.observation, original, changed).verdict).not.toBe("equal");
    }
    const unknown = structuredClone(original) as any; unknown.status = "unknown"; unknown.steps[0].delivery = "unknown";
    expect(comparePublicResult(point.observation, point.observation, unknown, unknown)).toMatchObject({
      verdict: "incomparable", difference: { reason: "unknown_result_delivery" } });
    for (const delta of [undefined, [], false]) {
      const invalid = { ...original, commitment_delta: delta } as unknown as P1Result;
      expect(comparePublicResult(point.observation, point.observation, original, invalid).verdict).toBe("incomparable");
    }
  } finally { await f.close(); }
});

it("joins Result commitments to the original observation's single entity bijection", () => {
  const reduced = reduce(createSession("result-graph-probe", "managed"), rewardForwardPair().outer);
  expect(reduced.complete).toBe(true);
  const choice = reduced.observation.catalog.find(item => reduced.lowering[item.choice_id]!.commitment === "decline_group");
  expect(choice).toBeDefined();
  const lowering = reduced.lowering[choice!.choice_id]!;
  const state = applyCommitment(reduced.state, lowering, true);
  const original = buildP1Result({ occurrence: reduced.observation.envelope.occurrence,
    choice_id: choice!.choice_id, status: "completed", steps: [], commitment_delta: commitmentView(state) });
  const names = new Set<string>();
  const collect = (value: any, key = "") => {
    if (typeof value === "string" && /^(?:entity_id|group_id|reward_set_id|parent_group_id|choice_id|subject|target|group|declined_reward_groups)$/u.test(key)) names.add(value);
    else if (Array.isArray(value)) value.forEach(child => collect(child, key));
    else if (value && typeof value === "object") Object.entries(value).forEach(([k, child]) => collect(child, k));
  };
  collect(reduced.observation); collect(original);
  const rename = (value: any): any => typeof value === "string" ? names.has(value) ? "renamed_" + value : value
    : Array.isArray(value) ? value.map(rename) : value && typeof value === "object"
      ? Object.fromEntries(Object.entries(value).map(([key, child]) => [key, rename(child)])) : value;
  const observation = rename(reduced.observation) as P1Observation, result = rename(original) as P1Result;
  expect(comparePublicResult(reduced.observation, observation, original, result).verdict).toBe("equal");
  const wrong = structuredClone(result) as any;
  wrong.commitment_delta.declined_reward_groups[0] = "separately_isomorphic_but_foreign_group";
  wrong.commitment_delta.reward_dispositions[0].group = "separately_isomorphic_but_foreign_group";
  expect(comparePublicResult(reduced.observation, observation, original, wrong).verdict).toBe("different");
  const a = { ...original, future: { entity_id: "literal-left" } }, b = { ...result, future: { entity_id: "literal-right" } };
  expect(comparePublicResult(reduced.observation, observation, a, b).verdict).toBe("different");
});
