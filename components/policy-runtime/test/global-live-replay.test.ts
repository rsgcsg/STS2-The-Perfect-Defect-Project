import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { globalJournalEvents } from "../src/global-live-replay.js";
import { readLiveManifest, replayGlobalAgentRun, streamGlobalAgentRun, type VerifiedGlobalTransition } from "../src/global-replay.js";
import { GlobalHttpFixture, globalRuntimeFixture } from "./global-runtime-fixtures.js";

it("the normal HTTP/Agent producer closes transitions before run end, including terminal without another choice", async () => {
  const source = new GlobalHttpFixture(); source.terminalAfterStep = false;
  const f = await globalRuntimeFixture({ source });
  let streaming: Promise<unknown> | undefined;
  const rows: VerifiedGlobalTransition[] = [];
  try {
    expect((await f.runtime.tick()).type).toBe("delivered");
    let first!: () => void;
    const emitted = new Promise<void>(resolve => { first = resolve; });
    streaming = streamGlobalAgentRun(f.evidence.directory, globalJournalEvents(f.evidence.directory), async row => {
      const manifest = JSON.parse(await readFile(join(f.evidence.directory, "manifest.json"), "utf8"));
      if (!rows.length) expect(manifest.ended_at).toBeNull();
      expect(row.input.catalog).toEqual(row.input.observation.catalog);
      // This conformance Agent has no model; research admission separately
      // requires a complete score vector bound to its real mu response.
      expect(row.directive.directive.selection.action_id).toBe(row.result.choice_id);
      expect(row.result.status).toBe("completed");
      expect(row.original_paths.length).toBeGreaterThan(0);
      expect(row.verified_through_sequence).toBeGreaterThan(row.result_sequence);
      rows.push(row); first();
    });
    expect((await f.runtime.tick()).type).toBe("delivered");
    await Promise.race([emitted, streaming]);
    expect(rows[0]!.step_index).toBe(0);
    expect(rows[0]!.successor.observation.status).toBe("decision");
    source.terminalAfterStep = true;
    expect((await f.runtime.tick()).type).toBe("delivered");
    expect((await f.runtime.tick()).type).toBe("closed");
    await streaming;
    expect(rows).toHaveLength(3);
    expect(rows.at(-1)!.successor.observation.status).toBe("terminal");
    expect((await replayGlobalAgentRun(f.evidence.directory)).results).toBe(3);
  } finally { await f.close(); await streaming; }
});

it("a live finalization manifest rewrite is observed to completion", async () => {
  const directory = await mkdtemp(join(tmpdir(), "live-manifest-"));
  try {
    await writeFile(join(directory, "manifest.json"), "");
    const reading = readLiveManifest(directory);
    await writeFile(join(directory, "manifest.json"), '{"ended_at":');
    await new Promise(resolve => setTimeout(resolve, 40));
    await writeFile(join(directory, "manifest.json"), '{"ended_at":"final"}');
    expect(await reading).toEqual({ ended_at: "final" });
  } finally { await rm(directory, { recursive: true, force: true }); }
});

it("an unknown action produces no trainable transition and the original remains unknown", async () => {
  const source = new GlobalHttpFixture(); source.delivery = "unknown";
  const f = await globalRuntimeFixture({ source });
  const rows: VerifiedGlobalTransition[] = [];
  try {
    expect((await f.runtime.tick()).type).toBe("unknown");
    await f.runtime.stop();
    const report = await streamGlobalAgentRun(f.evidence.directory,
      globalJournalEvents(f.evidence.directory), async row => { rows.push(row); });
    expect(rows).toEqual([]);
    expect(report.unknown_delivery).toBe(true);
    expect((await replayGlobalAgentRun(f.evidence.directory)).unknown_delivery).toBe(true);
  } finally { await f.close(); }
});

it("a known uncompleted action is an explicit boundary after the verified prefix", async () => {
  const source = new GlobalHttpFixture(); source.terminalAfterStep = false;
  const f = await globalRuntimeFixture({ source });
  const rows: VerifiedGlobalTransition[] = [], boundaries: Record<string, unknown>[] = [];
  try {
    expect((await f.runtime.tick()).type).toBe("delivered");
    source.delivery = "not_delivered";
    expect((await f.runtime.tick()).type).toBe("not_delivered");
    await f.runtime.stop();
    const report = await streamGlobalAgentRun(f.evidence.directory,
      globalJournalEvents(f.evidence.directory), async row => { rows.push(row); },
      async boundary => { boundaries.push(boundary); });
    expect(rows).toHaveLength(1);
    expect(rows[0]!.step_index).toBe(0);
    expect(boundaries).toHaveLength(1);
    expect(boundaries[0]).toMatchObject({ step_index: 1, result: { status: "not_started" } });
    expect(report.unknown_delivery).toBe(false);
  } finally { await f.close(); }
});
