import { afterEach, describe, expect, it, vi } from "vitest";
import { RuntimeControlPreconditionError, RuntimeLifecycleOwner } from "../src/runtime-owner.js";

afterEach(() => { vi.useRealTimers(); });

describe("the shared Runtime lifecycle owner", () => {
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
