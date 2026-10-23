import { afterEach, describe, expect, it, vi } from "vitest";
import { RuntimeControlPreconditionError, RuntimeLifecycleOwner } from "../src/runtime-owner.js";
import { readFileSync } from "node:fs";
import { normalizeAutonomyBudget, parseAutonomyBudgetStatus, type AutonomyBudgetInput } from "../src/contracts.js";

afterEach(() => { vi.useRealTimers(); });

describe("the shared Runtime lifecycle owner", () => {
  it("keeps omitted and partial numeric inputs bounded, including optional undefined values", () => {
    const defaults = { mode: "bounded", maxSubmissions: 16, maxPolicyCalls: 32, deadlineMs: 60000 };
    expect(normalizeAutonomyBudget()).toEqual(defaults);
    expect(normalizeAutonomyBudget({ maxSubmissions: undefined, maxPolicyCalls: undefined })).toEqual(defaults);
    expect(normalizeAutonomyBudget({ mode: "bounded", deadlineMs: 10 })).toEqual({ ...defaults, deadlineMs: 10 });
    for (const value of [{ mode: "continuous", maxSubmissions: undefined }, { mode: "continuous", deadlineMs: null },
      { mode: "continuous", maxPolicyCalls: 1 }, { mode: "other" }, { deadlineMs: null }, { deadlineMs: Infinity }, { maxPolicyCalls: 0 }])
      expect(() => normalizeAutonomyBudget(value as AutonomyBudgetInput)).toThrow();
  });

  it("has no global timer or limits in explicit continuous mode and freezes the actual segment on Human", async () => {
    vi.useFakeTimers(); let now = 0;
    const owner = new RuntimeLifecycleOwner({ mode: "continuous" }, () => now, "human");
    const expired = vi.fn(); owner.begin(expired);
    expect(vi.getTimerCount()).toBe(0);
    for (let i = 0; i < 40; i++) { expect(owner.consumeCall()).toBe(true); expect(owner.consumeSubmission()).toBe(true); }
    now = 70001; await vi.advanceTimersByTimeAsync(70001);
    expect(expired).not.toHaveBeenCalled(); expect(owner.available()).toBe(true);
    expect(owner.status(expired)).toEqual({ mode: "continuous", state: "active", max_submissions: null, submissions_used: 40,
      max_policy_calls: null, policy_calls_used: 40, deadline_ms: null, elapsed_ms: 70001, remaining_ms: null, exhausted_reason: null, ended_reason: null });
    owner.end("human_recovery"); now += 1000;
    expect(owner.status(expired)).toMatchObject({ state: "inactive", elapsed_ms: 70001, submissions_used: 40, ended_reason: "human_recovery" });
    expect(owner.consumeCall()).toBe(false); owner.begin(expired);
    expect(owner.status(expired)).toMatchObject({ mode: "continuous", state: "active", elapsed_ms: 0, submissions_used: 0 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("still schedules finite local recovery and fences its stale timer in continuous mode", async () => {
    vi.useFakeTimers(); let now = 0;
    const owner = new RuntimeLifecycleOwner({ mode: "continuous" }, () => now, "auto");
    const global = vi.fn(), local = vi.fn(); owner.scheduleDeadline(global, { at: 25, onDeadline: local });
    now = 25; await vi.advanceTimersByTimeAsync(25);
    expect(local).toHaveBeenCalledTimes(1); expect(global).not.toHaveBeenCalled();
    owner.scheduleDeadline(global, { at: 50, onDeadline: local }); owner.end("human_recovery"); owner.begin(global);
    now = 100; await vi.advanceTimersByTimeAsync(75);
    expect(local).toHaveBeenCalledTimes(1); expect(global).not.toHaveBeenCalled();
  });

  it("decodes strict historical bounded and v2 union vectors without weakening the old wire shape", () => {
    const v = JSON.parse(readFileSync(new URL("./fixtures/autonomy-v2.json", import.meta.url), "utf8"));
    expect(parseAutonomyBudgetStatus(v.bounded_status_v1, 1).mode).toBe("bounded");
    for (const key of ["bounded_status_v2", "continuous_status_v2", "continuous_paused_v2"])
      expect(parseAutonomyBudgetStatus(v[key], 2)).toEqual(v[key]);
    expect(() => parseAutonomyBudgetStatus(v.continuous_status_v2, 1)).toThrow();
    for (const bad of [{ ...v.bounded_status_v2, deadline_ms: null }, { ...v.continuous_status_v2, remaining_ms: 1 },
      { ...v.continuous_status_v2, state: "exhausted" }, { ...v.continuous_status_v2, policy_calls_used: -1 },
      { ...v.bounded_status_v2, remaining_ms: 1 }]) expect(() => parseAutonomyBudgetStatus(bad, 2)).toThrow();
  });
  it("keeps one mutation order after a preceding operation fails", async () => {
    const owner = new RuntimeLifecycleOwner(undefined, () => 0, "human");
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const order: string[] = [];
    const first = owner.serialize(async () => { order.push("first"); await gate; throw new Error("original_failure"); });
    const caught = first.catch(error => error.message);
    const second = owner.serialize(async () => { order.push("second"); return "second_result"; });
    await Promise.resolve();
    expect(order).toEqual(["first"]);
    release();
    expect(await caught).toBe("original_failure");
    expect(await second).toBe("second_result");
    expect(order).toEqual(["first", "second"]);
  });

  it("fences and cancels deferred work before serialized Human cleanup", async () => {
    const owner = new RuntimeLifecycleOwner(undefined, () => 0, "human");
    const active = new AbortController();
    owner.active = { controller: active };
    const epoch = owner.epoch;
    const order: string[] = [];
    const first = owner.serialize(async () => {
      order.push("work");
      await new Promise<void>(resolve => active.signal.addEventListener("abort", () => resolve(), { once: true }));
      expect(() => owner.checkEpoch(epoch)).toThrow(RuntimeControlPreconditionError);
      order.push("cancelled");
    });
    await Promise.resolve();
    owner.advanceEpoch(); owner.cancelActive("human_recovery");
    expect(active.signal.aborted).toBe(true);
    expect(() => owner.checkEpoch(epoch)).toThrow("runtime_recovery_epoch_mismatch");
    const human = owner.serialize(async () => { order.push("released"); });
    await Promise.all([first, human]);
    expect(order).toEqual(["work", "cancelled", "released"]);
  });

  it("expires idle-after-submission authorization without another tick or status read", async () => {
    vi.useFakeTimers();
    let now = 0;
    const owner = new RuntimeLifecycleOwner({ deadlineMs: 40 }, () => now, "auto");
    const active = new AbortController(); owner.active = { controller: active };
    const release = vi.fn();
    owner.scheduleDeadline(() => {
      owner.advanceEpoch(); owner.cancelActive("deadline");
      void owner.serialize(async () => { release(); });
    });
    expect(owner.consumeSubmission()).toBe(true);
    now = 40;
    await vi.advanceTimersByTimeAsync(40);
    expect(release).toHaveBeenCalledTimes(1);
    expect(owner.epoch).toBe(1);
    expect(active.signal.aborted).toBe(true);
    expect(owner.state).toMatchObject({ state: "exhausted", exhaustedReason: "deadline", submissionsUsed: 1, elapsedMs: 40 });
    expect(owner.consumeCall()).toBe(false);
    expect(owner.consumeSubmission()).toBe(false);
  });

  it("does not let a replaced authorization's deadline revoke its successor", async () => {
    vi.useFakeTimers();
    let now = 0;
    const owner = new RuntimeLifecycleOwner({ deadlineMs: 40 }, () => now, "human");
    const first = vi.fn(), second = vi.fn();
    owner.begin(first);
    now = 20; await vi.advanceTimersByTimeAsync(20);
    owner.begin(second);
    now = 40; await vi.advanceTimersByTimeAsync(20);
    expect(first).not.toHaveBeenCalled(); expect(second).not.toHaveBeenCalled();
    now = 60; await vi.advanceTimersByTimeAsync(20);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("revokes the timer on explicit Stop while preserving the spent wallet", async () => {
    vi.useFakeTimers();
    let now = 0;
    const owner = new RuntimeLifecycleOwner({ deadlineMs: 40 }, () => now, "auto");
    const expired = vi.fn(); owner.scheduleDeadline(expired);
    owner.consumeCall(); owner.consumeSubmission();
    now = 10; owner.end("stopped");
    now = 100; await vi.advanceTimersByTimeAsync(100);
    expect(expired).not.toHaveBeenCalled();
    expect(owner.status(expired)).toMatchObject({ state: "inactive", submissions_used: 1,
      policy_calls_used: 1, elapsed_ms: 10, ended_reason: "stopped" });
  });

  it("retains overflow fencing even when Human and Stop advance an exhausted epoch", () => {
    const owner = new RuntimeLifecycleOwner(undefined, () => 0, "human");
    owner.epoch = Number.MAX_SAFE_INTEGER;
    owner.advanceEpoch(); owner.advanceEpoch();
    expect(() => owner.checkEpoch(Number.MAX_SAFE_INTEGER)).toThrow("runtime_recovery_epoch_mismatch");
    expect(() => owner.checkEpoch(undefined)).not.toThrow();
    expect(() => owner.checkEpoch(-1)).toThrow("runtime_recovery_precondition_required");
  });
});
