import { readdir } from "node:fs/promises";
import { describe, expect, it, vi } from "vitest";
import { fixtureProgress } from "./fixture-progress.js";
import { nativeRuntimeFixture } from "./native-runtime-fixtures.js";

const RAW = "RAW_FIXTURE_PAYLOAD_MUST_NOT_APPEAR_IN_OBSERVER";

describe("native source-view fixture observation", () => {
  it("keeps the common default observer silent with zero retention I/O on a successful real fixture", async () => {
    const directory = new URL("../../../python/.local/fixture-diagnostics/", import.meta.url);
    const before = await readdir(directory).catch(() => []);
    const logging = vi.spyOn(console, "info").mockImplementation(() => undefined);
    try {
      const f = await nativeRuntimeFixture({ count: 0, status: "observed", mode: "shadow" });
      try {
        expect((await f.runtime.tick()).type).toBe("awaited");
        expect((await f.runtime.tick()).type).toBe("awaited");
        expect((await f.events()).filter(event => event.kind === "agent_consumed")).toHaveLength(1);
      } finally { await f.close(); }
      expect(logging).not.toHaveBeenCalled();
      expect((await readdir(directory).catch(() => [])).sort()).toEqual(before.sort());
    } finally { logging.mockRestore(); }
  });

  it("keeps real Consume/ACK/Await and cleanup when retention fails, without logging payloads or paths", async () => {
    const lines: string[] = [];
    const logging = vi.spyOn(console, "info").mockImplementation(value => { lines.push(String(value)); });
    // A source file cannot be a directory. Only the diagnostic writer sees it.
    const progress = fixtureProgress("native-source-view-retention-negative", true,
      new URL("./fixture-progress.ts/diagnostics/", import.meta.url));
    let f: Awaited<ReturnType<typeof nativeRuntimeFixture>> | undefined;
    try {
      f = await nativeRuntimeFixture({ count: 0, status: "observed", mode: "shadow", progress,
        beforeInitialize: ({ source }) => {
          source.observation.persistent!.content = { text: RAW }; source.rehash();
        } });
      await f.observe("tick-1", async () => {
        expect((await f!.runtime.tick()).type).toBe("awaited");
      });
      expect((await f.observe("tick-2", () => f!.runtime.tick())).type).toBe("awaited");
      expect((await f.events()).filter(event => event.kind === "agent_consumed")).toHaveLength(1);
      expect(f.source.requests.filter(request => /\/(current|actions)$/.test(request.path))).toHaveLength(0);
      expect(f.runtime.status().session.state_version).toBe(1);
    } finally {
      try { await f?.close(); } finally { await progress.flush(); logging.mockRestore(); }
    }
    const entries = lines.map(line => JSON.parse(line));
    for (const phase of ["setup-root", "setup-http-listen", "setup-artifact", "setup-manifest", "setup-evidence",
      "runtime-initialize", "agent-ready", "agent-consume", "agent-next", "sdk-transport", "durable-evidence-append",
      "tick-1", "tick-2", "runtime-stop", "port-close", "server-close", "root-remove"]) {
      expect(entries.some(entry => entry.phase === phase + "-begin")).toBe(true);
      expect(entries.some(entry => entry.phase === phase + "-end")).toBe(true);
    }
    expect(entries.at(-1)).toMatchObject({ phase: "observer-flush", pending_operation_ids: [], observer_pending_writes: 0 });
    expect(entries.at(-1).observer_write_failures).toBeGreaterThan(0);
    expect(entries.filter(entry => entry.phase === "fixture-state").at(-1)).toMatchObject({
      child_exit_observed: 1, runtime_stopped: 1, source_started: 0, operation_failed: 0 });
    expect(lines.join("\n")).not.toContain(RAW);
    expect(lines.join("\n")).not.toContain(f!.root);
    for (const entry of entries) {
      for (const [key, value] of Object.entries(entry)) {
        if (!["fixture_diagnostic", "phase", "pending_operation_ids"].includes(key)) expect(typeof value).toBe("number");
      }
    }
  });

  it("preserves the original startup exception and actual close even when every observer callback throws", async () => {
    const original = new Error(RAW), observerError = new Error("observer-only failure");
    let child: { exitCode: number | null; signalCode: string | null } | undefined;
    let source: { server?: { listening: boolean } } | undefined;
    const progress = Object.assign(() => { throw observerError; }, {
      begin: () => { throw observerError; }, flush: async () => undefined
    }) as ReturnType<typeof fixtureProgress>;
    await expect(nativeRuntimeFixture({ count: 0, status: "observed", mode: "shadow", progress,
      beforeInitialize: value => {
        child = (value.port as unknown as { child: typeof child }).child; source = value.source;
        throw original;
      } })).rejects.toMatchObject({ cause: original });
    expect(child !== undefined && (child.exitCode !== null || child.signalCode !== null)).toBe(true);
    expect(source?.server?.listening).toBe(false);
  });
});
