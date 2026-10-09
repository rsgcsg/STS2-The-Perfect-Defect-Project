import { readFileSync } from "node:fs";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { describe, expect, it, vi } from "vitest";
import { validateAgentExecutionPolicy, validateAgentManifest, validateAgentNextInput, validateAgentOperationalOutcome,
  sameAgentExecutionPolicy, type AgentExecutionPolicy } from "../src/agent-session-contracts.js";
import { NdjsonAgentSessionPort } from "../src/agent-session-port.js";
import { PolicyRuntime } from "../src/runtime.js";

const shared = JSON.parse(readFileSync(new URL("../contracts/fixtures/owned-current-known-stale-v1.json", import.meta.url), "utf8"));
function port(policy?: Readonly<AgentExecutionPolicy>) {
  const child = Object.assign(new EventEmitter(), { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), kill: vi.fn() });
  return new NdjsonAgentSessionPort(child as unknown as ConstructorParameters<typeof NdjsonAgentSessionPort>[0],
    shared.manifest.adapter, shared.manifest.limits, undefined, policy);
}

describe("owned Current and stale decision closed admission contract", () => {
  it("preserves exact old absence and requires the explicit sixth Next field only for opt-in", () => {
    expect(validateAgentManifest(shared.legacy_manifest).execution_policy).toBeUndefined();
    expect(validateAgentNextInput(shared.legacy_next)).toEqual(shared.legacy_next);
    expect(() => validateAgentNextInput(shared.opted_next_initial)).toThrow("unknown_or_missing_fields");
    const policy = validateAgentExecutionPolicy(shared.execution_policy);
    expect(validateAgentManifest(shared.manifest).execution_policy).toEqual(policy);
    expect(validateAgentNextInput(shared.opted_next_after_stale, policy)).toEqual(shared.opted_next_after_stale);
    expect(() => validateAgentNextInput(shared.legacy_next, policy)).toThrow("unknown_or_missing_fields");
  });
  it.each([0, true, 17, 1.5])("rejects invalid total ceiling %s", value => {
    expect(() => validateAgentExecutionPolicy({ ...shared.execution_policy, max_known_stale_rejections: value })).toThrow();
  });
  it.each([0, true, 5, 1.5])("rejects invalid consecutive ceiling %s", value => {
    expect(() => validateAgentExecutionPolicy({ ...shared.execution_policy, max_consecutive_known_stale_rejections: value })).toThrow();
  });
  it("rejects surplus fields, unsupported modes and missing owned capability requirement", () => {
    expect(() => validateAgentExecutionPolicy({ ...shared.execution_policy, retry: true })).toThrow();
    expect(() => validateAgentExecutionPolicy({ ...shared.execution_policy, known_stale: "retry_same_action" })).toThrow();
    const missing = structuredClone(shared.manifest); missing.requirements.required_methods = missing.requirements.required_methods.filter((m: string) => m !== "current_owned");
    expect(() => validateAgentManifest(missing)).toThrow("required_methods_mismatch");
  });
  it("retains full result grammar and requires matching old basis and watermark", () => {
    const outcome = structuredClone(shared.opted_next_after_stale.operational_outcome);
    outcome.result = shared.result_with_observed_frame;
    expect(validateAgentOperationalOutcome(outcome).result.observed_frame).toEqual(shared.result_with_observed_frame.observed_frame);
    expect(() => validateAgentOperationalOutcome({ ...outcome, result: { ...outcome.result, delivery: "unknown" } })).toThrow();
    expect(() => validateAgentOperationalOutcome({ ...outcome, result: { ...outcome.result, unknown_field: true } })).toThrow();
    expect(() => validateAgentNextInput({ ...shared.opted_next_after_stale, state_version: 2 }, shared.execution_policy)).toThrow("watermark");
    const long = { ...outcome, action_id: "x".repeat(65536) }; expect(validateAgentOperationalOutcome(long).action_id).toHaveLength(65536);
    expect(() => validateAgentOperationalOutcome({ ...long, action_id: long.action_id + "x" })).toThrow();
  });
  it("copies/fixes the explicit fifth constructor policy independently of caller mutation", () => {
    const mutable = structuredClone(shared.execution_policy), p = port(mutable);
    try {
      mutable.max_known_stale_rejections = 1;
      expect(p.executionPolicy?.max_known_stale_rejections).toBe(8); expect(Object.isFrozen(p.executionPolicy)).toBe(true);
      expect(sameAgentExecutionPolicy(p.executionPolicy, shared.execution_policy)).toBe(true);
      expect(sameAgentExecutionPolicy(undefined, p.executionPolicy)).toBe(false);
      expect(typeof Object.getOwnPropertyDescriptor(NdjsonAgentSessionPort.prototype, "executionPolicy")?.get).toBe("function");
    } finally { p.close(); }
  });
  it.each(["manifest_only", "port_only", "different"])("rejects %s policy mismatch before any ready/native request", async mismatch => {
    const manifest = structuredClone(mismatch === "port_only" ? shared.legacy_manifest : shared.manifest);
    const policy = mismatch === "manifest_only" ? undefined : { ...shared.execution_policy,
      ...(mismatch === "different" ? { max_known_stale_rejections: 7 } : {}) };
    const p = port(policy), ready = vi.spyOn(p, "ready"), native = vi.fn();
    try {
      await expect(PolicyRuntime.forAgent({ manifest, port: p,
        environment: { nativeLogicalRequest: native } as never, evidence: {} as never,
        runtimeIdentity: { version: "fixture", code_sha256: "1".repeat(64) } })).rejects.toThrow("agent_execution_policy_mismatch");
      expect(ready).not.toHaveBeenCalled(); expect(native).not.toHaveBeenCalled();
    } finally { p.close(); }
  });
});
