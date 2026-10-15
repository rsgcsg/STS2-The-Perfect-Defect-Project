import { mkdir, writeFile } from "node:fs/promises";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureProgress } from "./fixture-progress.js";

vi.mock("node:fs/promises", () => ({ mkdir: vi.fn(), writeFile: vi.fn() }));

beforeEach(() => {
  vi.mocked(mkdir).mockResolvedValue(undefined);
  vi.mocked(writeFile).mockResolvedValue();
  vi.spyOn(console, "info").mockImplementation(() => undefined);
});
afterEach(() => { vi.restoreAllMocks(); vi.clearAllMocks(); vi.useRealTimers(); });

describe("best-effort fixture progress", () => {
  it("retains distinct immutable overlapping begin/end entries without gating the work", async () => {
    let release!: () => void;
    const held = new Promise<void>(resolve => { release = resolve; });
    const originals: { path: string; text: string }[] = [];
    vi.mocked(writeFile).mockImplementation(async (path, bytes, options) => {
      expect(options).toEqual({ flag: "wx" });
      originals.push({ path: String(path), text: String(bytes) });
      if (originals.length === 1) await held;
    });
    const progress = fixtureProgress("overlap", true), counts = { iteration: 1 };
    const outer = progress.begin("runtime_tick", counts);
    counts.iteration = 99;
    const inner = progress.begin("durable_evidence_append");
    // The product promise can settle while the first diagnostic write is held.
    await expect(Promise.resolve("original result").finally(inner)).resolves.toBe("original result");
    inner(); outer();
    await vi.waitFor(() => expect(originals).toHaveLength(1));
    const first = originals[0]!.text;
    release(); await progress.flush();
    expect(originals).toHaveLength(4);
    expect(new Set(originals.map(entry => entry.path)).size).toBe(4);
    expect(originals[0]!.text).toBe(first);
    const entries = originals.map(entry => JSON.parse(entry.text));
    expect(entries.map(entry => entry.entry_sequence)).toEqual([1, 2, 3, 4]);
    expect(entries.map(entry => entry.operation_id)).toEqual([1, 2, 2, 1]);
    expect(entries.map(entry => entry.pending_operation_ids)).toEqual([[1], [1, 2], [1], []]);
    expect(entries[0].iteration).toBe(1); expect(entries[3].iteration).toBe(1);
    expect(entries.map(entry => entry.phase)).toEqual([
      "runtime_tick-begin", "durable_evidence_append-begin", "durable_evidence_append-end", "runtime_tick-end"
    ]);
  });

  it("keeps original success and failure when diagnostic filesystem and logging fail", async () => {
    vi.mocked(mkdir).mockRejectedValue(new Error("observer directory unavailable"));
    vi.mocked(console.info).mockImplementation(() => { throw new Error("observer stdout unavailable"); });
    const progress = fixtureProgress("observer-failure", true);
    const success = progress.begin("runtime_tick");
    await expect(Promise.resolve("delivered").finally(success)).resolves.toBe("delivered");
    const original = new Error("original scenario failure"), failure = progress.begin("runtime_tick");
    await expect(Promise.reject(original).finally(failure)).rejects.toBe(original);
    await expect(progress.flush()).resolves.toBeUndefined();
    expect(mkdir).toHaveBeenCalledTimes(4);
    expect(writeFile).not.toHaveBeenCalled();
  });

  it("does not extend an original 5000ms deadline when observation is blocked and bounds tail flush", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "performance"] });
    vi.mocked(writeFile).mockImplementation(() => new Promise(() => undefined));
    const progress = fixtureProgress("blocked-observer", true), end = progress.begin("runtime_tick");
    const original = new Error("original 5000ms deadline");
    const work = new Promise<void>((_, reject) => { setTimeout(() => reject(original), 5000); }).finally(end);
    const rejected = expect(work).rejects.toBe(original);
    await vi.advanceTimersByTimeAsync(4999);
    expect(vi.mocked(console.info).mock.calls.map(([line]) => JSON.parse(line).phase)).not.toContain("runtime_tick-end");
    await vi.advanceTimersByTimeAsync(1); await rejected;
    let flushed = false;
    const flush = progress.flush().then(() => { flushed = true; });
    await vi.advanceTimersByTimeAsync(249); expect(flushed).toBe(false);
    await vi.advanceTimersByTimeAsync(1); await flush;
    expect(flushed).toBe(true);
    const summary = JSON.parse(vi.mocked(console.info).mock.calls.at(-1)![0]);
    expect(summary).toMatchObject({ phase: "observer-flush", flush_ms: 250, observer_pending_writes: 2 });
  });
});
