import { it, expect } from "vitest";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";
import { validateAgentManifest } from "../src/agent-session-contracts.js";

it.each(["r1.5", "r1.8", "r1.9"] as const)("executes and independently replays the explicitly bound %s profile", async revision => {
  const f = await globalRuntimeFixture({ p1Revision: revision });
  try {
    expect(f.manifest.input.global?.revision).toBe(revision);
    expect(f.manifest.input.projection.version).toBe(revision);
    expect((await f.runtime.tick()).type).toBe("delivered");
    expect((await f.runtime.tick()).type).toBe("closed");
    const rows = await f.events();
    expect(rows.find(row => row.kind === "global_initialized").payload.core.revision).toBe(revision);
    expect((await replayGlobalAgentRun(f.evidence.directory)).core.revision).toBe(revision);
    const mismatched = structuredClone(f.manifest);
    mismatched.input.projection.version = revision === "r1.5" ? "r1.8" : "r1.5";
    expect(() => validateAgentManifest(mismatched)).toThrow("global_projection_revision_mismatch");
    const unsupported = structuredClone(f.manifest);
    (unsupported.input.global as any).revision = unsupported.input.projection.version = "r1.7";
    expect(() => validateAgentManifest(unsupported)).toThrow();
  } finally { await f.close(); }
}, 15000);
