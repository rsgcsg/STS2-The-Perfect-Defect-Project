import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { encodeGlobalArtifact } from "../src/global-artifact-storage.js";
import { extractTrajectory } from "../src/p1-global/trajectory.js";

it("extracts original P1 frames through lossless storage and rejects changed original bytes", async () => {
  const directory = await mkdtemp(join(tmpdir(), "p1-trajectory-"));
  try {
    const observation = { status: "ready", description: "完整公开事实".repeat(15000) };
    const frames = [
      { message_type: "consume", input: { observation } },
      { message_type: "directive", output: { directive: { type: "act", selection: { action_id: "choice-1" } } } },
    ];
    const events = [];
    for (const [index, frame] of frames.entries()) {
      const bytes = Buffer.from(JSON.stringify(frame) + "\n");
      const sha256 = createHash("sha256").update(bytes).digest("hex");
      const encoded = await encodeGlobalArtifact(bytes);
      expect(encoded.suffix).toBe(index === 0 ? ".bin.gz" : ".bin");
      const artifact = { role: index === 0 ? "agent_in" : "agent_out", path: `global-${sha256}${encoded.suffix}`, bytes: bytes.length, sha256 };
      await writeFile(join(directory, artifact.path), encoded.stored);
      events.push({ kind: "global_agent_frame", payload: { artifact } });
    }
    await writeFile(join(directory, "events.jsonl"), events.map(event => JSON.stringify(event)).join("\n") + "\n");
    const trajectory = await extractTrajectory(directory);
    expect(trajectory.points).toEqual([{ index: 0, chosen: "choice-1", observation: {
      ...observation, envelope: { occurrence: 0, required_information_complete: true },
    } }]);
    const path = join(directory, events[1]!.payload.artifact.path);
    const changed = (await readFile(path)).toString().replace("choice-1", "choice-2");
    await writeFile(path, changed);
    await expect(extractTrajectory(directory)).rejects.toThrow("global_artifact_bytes");
  } finally { await rm(directory, { recursive: true, force: true }); }
});
