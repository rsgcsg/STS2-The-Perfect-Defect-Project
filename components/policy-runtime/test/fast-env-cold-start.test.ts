import { mkdtemp, readdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { coldStartGate, defaultColdStartDirectory } from "../src/fast-env/cold-start.js";
import { parseFastEnvConfig } from "../src/fast-env/contracts.js";

const tick = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));
const directory = () => mkdtemp(join(tmpdir(), "cold-start-"));

describe("fast-env cold-start gate", () => {
  it("never lets more than the bound hold at once, across processes sharing the directory", async () => {
    const dir = await directory();
    // Three "processes" (pids) on one host, eight starts each, bound 3.
    const gates = [101, 102, 103].map(pid => coldStartGate({ maxConcurrent: 3, directory: dir, sleep: tick, pid, host: "node-a",
      alive: () => true, pollMs: 2 }));
    let holding = 0, most = 0, done = 0;
    await Promise.all(gates.flatMap(gate => Array.from({ length: 8 }, async () => {
      const release = await gate.acquire(() => false);
      holding += 1; most = Math.max(most, holding);
      await tick(3);
      holding -= 1; release!(); release!();
      done += 1;
    })));
    expect(done).toBe(24);
    expect(most).toBeLessThanOrEqual(3);
    expect(most).toBeGreaterThan(1);
    expect(await readdir(dir)).toEqual([]);
  });

  it("removes a dead local owner's place, never another host's or a live process's", async () => {
    const dir = await directory();
    const id = "0".repeat(32);
    await writeFile(join(dir, `held-7-${id}-node-a`), "");
    await writeFile(join(dir, `held-8-${id}-node-b`), "");
    const gate = coldStartGate({ maxConcurrent: 2, directory: dir, sleep: tick, pid: 1, host: "node-a", alive: pid => pid !== 7 && pid !== 8, pollMs: 2 });
    const release = await gate.acquire(() => false);
    const names = await readdir(dir);
    expect(names).toContain(`held-8-${id}-node-b`);
    expect(names).not.toContain(`held-7-${id}-node-a`);
    // Two places taken (node-b's and ours): the next start waits until Stop.
    let stop = false;
    const waiting = gate.acquire(() => stop);
    await tick(20);
    stop = true;
    expect(await waiting).toBeNull();
    release!();
    expect(await readdir(dir)).toEqual([`held-8-${id}-node-b`]);
  });

  it("is a config parameter, default 10 places in the node-local directory", () => {
    const base = { schema: "sts2.policy-runtime/fast-env-config-1", env_process_id: "e", slots: 1,
      host: { package: "p", candidate_directory: "c", game_directory: null, character: "Defect", language: "en", ascension: 0, request_timeout_ms: 30000 },
      agent_manifest: "m", compact: { enabled: false, reconcile_interval: 64 }, checks: { sample_rate: 0 },
      decision_link: { kind: "unix", path: "/l" },
      inference: { timeout_ms: 1000, actor_id: "a", single_choice: "omit", sampling: { mode: "temperature_uniform", seed: "s", arm_id: "x",
        classes: { combat: { temperature: 1, uniform_mix: 0 }, noncombat: { temperature: 1, uniform_mix: 0 } } } },
      budget: { max_decisions_per_game: 1, max_native_steps_per_game: 1, game_wall_ms: 1 }, no_progress: null,
      evidence: { directory: "/e" } };
    expect(parseFastEnvConfig(base).cold_start).toEqual({ max_concurrent: 10, directory: null });
    expect(parseFastEnvConfig({ ...base, cold_start: { max_concurrent: 4, directory: "/tmp/x" } }).cold_start).toEqual({ max_concurrent: 4, directory: "/tmp/x" });
    expect(() => parseFastEnvConfig({ ...base, cold_start: { max_concurrent: 0 } })).toThrow(/cold_start.max_concurrent/u);
    expect(() => parseFastEnvConfig({ ...base, cold_start: { extra: 1 } })).toThrow(/cold_start.extra/u);
    if (process.platform !== "win32") expect(defaultColdStartDirectory()).toMatch(/^\/tmp\/sts2-fast-env-cold-start-/u);
  });
});
