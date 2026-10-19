/** Opt-in real CPU artifact over the existing Runtime and synthetic HTTP Host.
 * The ordinary portable model/mask/resume checks live in Python. This local
 * artifact test starts no game and never qualifies a Reference session.
 */
import { it, expect } from "vitest";
import { execFileSync } from "node:child_process";
import { readFile, writeFile, mkdtemp, cp } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";

const modelExport = process.env.STPD_T0_MODEL_EXPORT;
it.skipIf(!modelExport)("runs exact trained M-std via ACK, score-bound choice, Receipt and terminal", async () => {
  const python = process.env.STPD_TEST_PYTHON!;
  if (!python) throw new Error("STPD_TEST_PYTHON is required for the local CPU artifact test");
  const root = await mkdtemp(join(tmpdir(), "p1-mstd-runtime-"));
  const registration = join(root, "models");
  const env = { ...process.env, PYTHONPATH: resolve("python"), PYTHONDONTWRITEBYTECODE: "1" };
  const identity = JSON.parse(execFileSync(python, ["-c", "import json; from stpd.p1.export import adapter_identity; print(json.dumps(adapter_identity()))"], { env, encoding: "utf8" }));
  const packageBytes = await readFile(join(modelExport!, "model.tensor-tree"));
  const f = await globalRuntimeFixture({
    budgetMs: 60000,
    program: { command: python, args: ["-m", "stpd.p1.agent", "--config", join(registration, "config.json"),
      "--manifest", join(registration, "manifest.json"), "--binding-root", registration], adapter: identity, artifact: packageBytes, env },
    editManifest: manifest => {
      const result = execFileSync(python, ["-c", "import json,sys; from pathlib import Path; from stpd.p1.export import register; register(Path(sys.argv[1]),Path(sys.argv[2]),json.loads(sys.stdin.read())); print((Path(sys.argv[2])/'manifest.json').read_text())", modelExport!, registration], { env, input: JSON.stringify(manifest), encoding: "utf8" });
      Object.assign(manifest, JSON.parse(result));
      manifest.limits.agent_timeout_ms = 30000;
    },
  });
  try {
    expect((await f.runtime.tick()).type).toBe("delivered");
    expect((await f.runtime.tick()).type).toBe("closed");
    expect(f.source.steps).toBe(1);
    expect(f.source.controlHeld).toBe(false);
    const events = await f.events();
    const frames = events.filter(e => e.kind === "global_agent_frame" && e.payload.direction === "in");
    const messages = await Promise.all(frames.map(async e => JSON.parse(await readFile(join(f.evidence.directory, e.payload.artifact.path), "utf8"))));
    const chosen = messages.find(m => m.message_type === "directive" && m.output.directive.type === "act").output.directive;
    expect(chosen.scores.values.length).toBeGreaterThan(1);
    expect(chosen.selection.kind).toBe("handle");
    const inputEvent = events.find(e => e.kind === "global_input");
    const input = JSON.parse(await readFile(join(f.evidence.directory, inputEvent.payload.artifact.path), "utf8"));
    const best = chosen.scores.values.reduce((winner: number, value: number, index: number, values: number[]) => value > values[winner]! ? index : winner, 0);
    expect(chosen.selection.action_id).toBe(input.catalog[best].choice_id);
    expect(events.filter(e => e.kind === "global_consumed")).toHaveLength(2);
    const replay = await replayGlobalAgentRun(f.evidence.directory);
    expect(replay.verdict).toBe("verified");
    const destination = process.env.STPD_T0_PROOF_DIR;
    if (destination) {
      await cp(f.evidence.directory, join(destination, "runtime-evidence"), { recursive: true, errorOnExist: true });
      await writeFile(join(destination, "runtime-proof.json"), JSON.stringify({
        scope: "real trained CPU model / production Runtime / synthetic HTTP Host; no Reference game",
        manifest: f.manifest, directive: chosen, submissions: f.source.steps, controller_released: !f.source.controlHeld,
        consumed: 2, terminal: "fixture terminal", replay,
      }, null, 2) + "\n");
    }
  } finally { await f.close(); }
}, 120000);
