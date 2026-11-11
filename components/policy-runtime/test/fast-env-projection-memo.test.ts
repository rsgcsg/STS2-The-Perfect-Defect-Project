import { describe, expect, it } from "vitest";
import { parseFastEnvConfig } from "../src/fast-env/contracts.js";

const base = (compact: Record<string, unknown>) => ({ schema: "sts2.policy-runtime/fast-env-config-1", env_process_id: "e", slots: 1,
  host: { package: "p", candidate_directory: "c", game_directory: null, character: "Defect", language: "en", ascension: 0, request_timeout_ms: 30000 },
  agent_manifest: "m", compact, checks: { sample_rate: 0 },
  decision_link: { kind: "unix", path: "/l" },
  inference: { timeout_ms: 1000, actor_id: "a", single_choice: "omit", sampling: { mode: "temperature_uniform", seed: "s", arm_id: "x",
    classes: { combat: { temperature: 1, uniform_mix: 0 }, noncombat: { temperature: 1, uniform_mix: 0 } } } },
  budget: { max_decisions_per_game: 1, max_native_steps_per_game: 1, game_wall_ms: 1 }, no_progress: null,
  evidence: { directory: "/e" } });

describe("fast-env compact.projection_memo (E5)", () => {
  it("is off unless configured, and only with compact output", () => {
    expect(parseFastEnvConfig(base({ enabled: true, reconcile_interval: 64 })).compact.projection_memo).toBeNull();
    expect(parseFastEnvConfig(base({ enabled: true, reconcile_interval: 64, projection_memo: null })).compact.projection_memo).toBeNull();
    expect(parseFastEnvConfig(base({ enabled: true, reconcile_interval: 64, projection_memo: { check_interval: 64 } })).compact.projection_memo)
      .toEqual({ check_interval: 64 });
    expect(() => parseFastEnvConfig(base({ enabled: false, reconcile_interval: 64, projection_memo: { check_interval: 64 } })))
      .toThrow(/compact.projection_memo/u);
    expect(() => parseFastEnvConfig(base({ enabled: true, reconcile_interval: 64, projection_memo: { check_interval: 0 } })))
      .toThrow(/compact.projection_memo.check_interval/u);
    expect(() => parseFastEnvConfig(base({ enabled: true, reconcile_interval: 64, projection_memo: { extra: 1 } })))
      .toThrow(/compact.projection_memo/u);
  });
});
