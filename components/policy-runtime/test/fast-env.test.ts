import { readFileSync } from "node:fs";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import { createInterface } from "node:readline";
import { randomUUID } from "node:crypto";
import { afterEach, describe, expect, it } from "vitest";
import { PlayerEnvironmentRestClient } from "@rsgcsg/sts2-connector-client";
import { ReferenceGlobalTransport } from "../src/global-transport.js";
import { createGlobalProgramManifest } from "../src/global-program.js";
import { pyJsonSha256 } from "../src/fast-env/json-bytes.js";
import { GlobalHttpFixture } from "./global-runtime-fixtures.js";
import { FastEnvProcess, requireFastEnvManifest } from "../src/fast-env/process.js";
import { StreamDecisionLink } from "../src/fast-env/link.js";
import { ResponseBinding } from "../src/fast-env/binding.js";
import { MinimalJsonlEvidenceSink, type FastEvidenceSink, type FastGameMeta } from "../src/fast-env/evidence-sink.js";
import { FAST_ENV_COMMAND_SCHEMA, FAST_ENV_LINK_SCHEMA, parseFastEnvCommand, parseFastEnvConfig, parseSampling,
  type FastEnvConfig } from "../src/fast-env/contracts.js";
import type { NativeEpisode, SlotNative, SlotNativeFactory } from "../src/fast-env/host.js";

const p1Fixture = (name: string): any => JSON.parse(readFileSync(new URL(`../../connector/sdk/typescript/test/fixtures/p1-global/${name}.json`, import.meta.url), "utf8"));
const SAMPLING = { mode: "temperature_uniform", seed: "actor-7", arm_id: "control",
  classes: { combat: { temperature: 1, uniform_mix: 0 }, noncombat: { temperature: 1, uniform_mix: 0 } } } as const;

interface NativeHooks {
  /** Native steps after which the game is over (default 1). */
  terminalAfter?: number;
  delivery?: (step: number) => string;
  stepThrows?: (step: number) => boolean;
  captureThrows?: (capture: number) => boolean;
  stepGate?: () => Promise<void>;
  resetThrows?: boolean;
  /** Emit a fake compact line on every mount and step (evidence tap). */
  compactLines?: boolean;
}

/** One slot's "native": a Reference fixture Host behind the real public-basis client. */
class FakeNative implements SlotNative {
  readonly source = new GlobalHttpFixture();
  transport!: ReferenceGlobalTransport;
  shutdowns: boolean[] = [];
  captures = 0;
  constructor(readonly id: number, private readonly hooks: NativeHooks) {}
  async init(): Promise<this> {
    await this.source.start();
    this.transport = new ReferenceGlobalTransport(new PlayerEnvironmentRestClient(this.source.address, 2000), { productVersion: "fixture", clientInstanceId: `client-${this.id}` });
    await this.transport.negotiate(new AbortController().signal);
    return this;
  }
  get runtimeInstanceId() { return this.source.capabilities.host.runtime_instance_id as string; }
  async reset(): Promise<NativeEpisode> {
    if (this.hooks.resetThrows) throw new Error("fixture_reset_failed");
    this.#lines = []; this.#listener = null;
    this.#line('{"frame":"full","seq":0}');
    this.source.basis = p1Fixture("synthetic-complete-combat");
    this.source.steps = 0;
    this.source.terminalAfterStep = (this.hooks.terminalAfter ?? 1) <= 1;
    await this.transport.acquire(new AbortController().signal);
    return { gameInstanceId: `gi-${randomUUID().replaceAll("-", "")}`, start: { kind: "new_run" } };
  }
  async loadSave(input: { save: Record<string, unknown> }): Promise<NativeEpisode> {
    const episode = await this.reset();
    return { ...episode, start: { kind: "from_save", save_sha256: String(input.save.sha256) } };
  }
  async capture() {
    this.captures += 1;
    if (this.hooks.captureThrows?.(this.captures)) throw new Error("fixture_native_dead");
    return this.transport.capture({ timeoutMs: 2000 });
  }
  async step(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }): Promise<unknown> {
    const step = this.source.steps + 1;
    if (this.hooks.stepGate) await this.hooks.stepGate();
    if (this.hooks.stepThrows?.(step)) throw new Error("fixture_native_lost");
    this.source.delivery = this.hooks.delivery?.(step) ?? "delivered";
    this.source.terminalAfterStep = step >= (this.hooks.terminalAfter ?? 1);
    const receipt = await this.transport.step(input, { timeoutMs: 2000 });
    this.#line(`{"frame":"delta","seq":${step}}`);
    return receipt;
  }
  async release() { await this.transport.release(); }
  taint() { return { tainted: false, reason: null }; }
  #lines: string[] = [];
  #listener: ((line: string) => void) | null = null;
  #line(text: string) { if (!this.hooks.compactLines) return; if (this.#listener) this.#listener(text); else this.#lines.push(text); }
  attachLines(listener: ((line: string) => void) | null) {
    this.#listener = listener;
    const buffered = this.#lines; this.#lines = [];
    if (listener) for (const line of buffered) listener(line);
  }
  frame() { return this.hooks.compactLines ? { episode_instance: "fake", seq: this.source.steps, objects_root_sha256: "b".repeat(64), line_sha256: "c".repeat(64) } : null; }
  async nativeCpuMs() { return null; }
  async shutdown(force: boolean) { this.shutdowns.push(force); await this.source.close(); }
}

type Policy = (decision: any) => Record<string, unknown> | "silent" | "disconnect";

const scored = (decision: any, index = 0, override: Record<string, unknown> = {}) => {
  const q = Array.from({ length: decision.catalog_len }, () => 1 / decision.catalog_len);
  return { type: "scored", row_id: decision.row_id, game_instance_id: decision.game_instance_id, catalog_sha256: decision.catalog_sha256,
    mu_snapshot_sha256: "a".repeat(64), segment_id: "seg-1", row_in_segment: 0, selected_index: index, behavior_q: q, selected_q: q[index],
    applied: { arm_id: "control", decision_class: decision.in_combat ? "combat" : "noncombat", temperature: 1, uniform_mix: 0 }, ...override };
};

const cleanups: (() => Promise<void>)[] = [];
afterEach(async () => { for (const cleanup of cleanups.splice(0)) await cleanup().catch(() => undefined); });

async function harness(options: { slots?: number; hooks?: (index: number) => NativeHooks; policy?: Policy; timeoutMs?: number;
  edit?: (config: any) => void; sink?: (directory: string) => FastEvidenceSink } = {}) {
  const natives: FakeNative[] = [];
  let created = 0;
  const probe = await new FakeNative(-1, {}).init();
  const identity = await probe.transport.negotiate(new AbortController().signal);
  await probe.shutdown(true);
  const root = await mkdtemp(join(tmpdir(), "fast-env-"));
  const manifest = requireFastEnvManifest(createGlobalProgramManifest({ episodeId: "fast-env", identity, artifactPath: join(root, "program.json"),
    artifactBytes: Buffer.from("{}"), adapter: { id: "fast-env-test", version: "1", protocol: "sts2.policy-runtime/agent-session-ndjson-1", code_sha256: "d".repeat(64) } }));
  const rawConfig: any = { schema: "sts2.policy-runtime/fast-env-config-1", env_process_id: "env-test", slots: options.slots ?? 1,
    host: { package: "/unused", candidate_directory: "/unused", game_directory: null, character: "Defect", language: "en", ascension: 0, request_timeout_ms: 2000 },
    agent_manifest: "/unused", compact: { enabled: false, reconcile_interval: 64 }, checks: { sample_rate: 0.01 },
    decision_link: { kind: "unix", path: "/unused" },
    inference: { timeout_ms: options.timeoutMs ?? 2000, actor_id: "actor-7", sampling: SAMPLING, single_choice: "score" },
    budget: { max_decisions_per_game: 50, max_native_steps_per_game: 100, game_wall_ms: 60_000 }, no_progress: null,
    settling_timeout_ms: 1000, evidence: { directory: join(root, "evidence") } };
  options.edit?.(rawConfig);
  const config: FastEnvConfig = parseFastEnvConfig(rawConfig);
  const factory: SlotNativeFactory = { hostVersion: "fixture-host",
    async start() { const index = created++; const native = await new FakeNative(index, options.hooks?.(index) ?? {}).init(); natives.push(native); return native; } };
  const fromEnv = new PassThrough(), toEnv = new PassThrough();
  const linkMessages: any[] = [];
  const policy = options.policy ?? (decision => scored(decision));
  createInterface({ input: fromEnv }).on("line", line => {
    const message = JSON.parse(line);
    linkMessages.push(message);
    if (message.type !== "decision") return;
    const reply = policy(message);
    if (reply === "disconnect") { toEnv.end(); return; }
    if (reply !== "silent") toEnv.write(`${JSON.stringify({ schema: FAST_ENV_LINK_SCHEMA, ...reply })}\n`);
  });
  let proc: FastEnvProcess | null = null;
  const link = new StreamDecisionLink(fromEnv, toEnv, reason => proc?.linkDisconnected(reason));
  const commands = new PassThrough();
  const events: any[] = [];
  const waiters: { match: (event: any) => boolean; resolve: (event: any) => void }[] = [];
  proc = new FastEnvProcess({ config, manifest, factory, link, evidence: options.sink?.(config.evidence.directory) ?? new MinimalJsonlEvidenceSink(config.evidence.directory), commands,
    write: line => { const event = JSON.parse(line); events.push(event);
      for (const waiter of [...waiters]) if (waiter.match(event)) { waiters.splice(waiters.indexOf(waiter), 1); waiter.resolve(event); } },
    sleep: ms => new Promise(resolve => setTimeout(resolve, Math.min(ms, 5))) });
  const exit = proc.run();
  cleanups.push(async () => { commands.end(); await exit; for (const native of natives) await native.source.close().catch(() => undefined); });
  let commandId = 0;
  const send = (command: Record<string, unknown>) => commands.write(`${JSON.stringify({ schema: FAST_ENV_COMMAND_SCHEMA, command_id: `c${++commandId}`, ...command })}\n`);
  const next = (match: (event: any) => boolean) => {
    const found = events.find(match);
    return found ? Promise.resolve(found) : new Promise<any>(resolve => waiters.push({ match, resolve }));
  };
  const open = (slot: number, extra: Record<string, unknown> = {}) =>
    send({ cmd: "open", slot, game_ref: `ref-${slot}-${commandId}`, seed: `seed-${slot}`, initial_progress: null, start: { kind: "new_run" }, r7: false, ...extra });
  const inject = (reply: Record<string, unknown>) => toEnv.write(`${JSON.stringify({ schema: FAST_ENV_LINK_SCHEMA, ...reply })}\n`);
  return { proc, exit, events, linkMessages, natives, send, next, open, config, link, inject };
}

const linkOf = (messages: any[], type: string, gameInstanceId?: string) =>
  messages.filter(message => message.type === type && (gameInstanceId === undefined || message.game_instance_id === gameInstanceId));

describe("fast-env contracts", () => {
  it("parses commands only with their schema and refuses unknown fields", () => {
    const open = { schema: FAST_ENV_COMMAND_SCHEMA, cmd: "open", command_id: "c1", slot: 0, game_ref: "g", seed: "s", initial_progress: null, start: { kind: "new_run" }, r7: false };
    expect(parseFastEnvCommand(open)).toMatchObject({ cmd: "open", start: { kind: "new_run" } });
    expect(() => parseFastEnvCommand({ ...open, schema: undefined })).toThrow(/command.schema/u);
    expect(() => parseFastEnvCommand({ ...open, extra: 1 })).toThrow(/open.extra/u);
    const save = { schema: "x", sha256: "e".repeat(64), act: 2, floor: 18 };
    expect(parseFastEnvCommand({ ...open, start: { kind: "from_save", save } })).toMatchObject({ start: { kind: "from_save", save } });
    expect(() => parseFastEnvCommand({ ...open, start: { kind: "from_save", save: { ...save, sha256: "nope" } } })).toThrow(/sha256/u);
    expect(() => parseSampling({ ...SAMPLING, classes: { ...SAMPLING.classes, combat: { temperature: 0.5, uniform_mix: 0 } } })).toThrow();
  });

  it("binding matches every WP0 rl-inference-3 scenario (request_id = row_id)", async () => {
    const scenarios = JSON.parse(readFileSync(new URL("../../../python/tests/fixtures/rl_inference_3/binding-scenarios.json", import.meta.url), "utf8"));
    expect(new Set(scenarios.map((item: any) => item.verdict))).toEqual(new Set(["accept", "discard_late", "not_started"]));
    for (const item of scenarios) {
      const fromEnv = new PassThrough(), toEnv = new PassThrough();
      fromEnv.resume();
      const link = new StreamDecisionLink(fromEnv, toEnv);
      const open = new Set<string>(item.open_instances);
      link.setOpenInstances(id => open.has(id));
      const binding = new ResponseBinding();
      const row = { row_id: item.row.request_id, game_instance_id: item.row.game_instance_id, catalog_sha256: item.row.catalog_sha256,
        catalog_len: item.row.catalog_len, in_combat: item.row.in_combat };
      const sampling = parseSampling(item.sampling);
      if (item.segment_mu !== null) {
        // Prime the segment's mu through an accepted reply on another row.
        const primer = { ...row, row_id: 999 };
        expect(binding.check(primer, { type: "scored", row_id: 999, game_instance_id: row.game_instance_id, catalog_sha256: row.catalog_sha256,
          mu_snapshot_sha256: item.segment_mu, segment_id: item.response.segment_id, row_in_segment: 0, selected_index: 0,
          behavior_q: [0.25, 0.5, 0.25], selected_q: 0.25, applied: item.response.applied }, sampling).verdict).toBe("accept");
      }
      const { request_id: requestId, status, ...rest } = item.response;
      const reply = Object.fromEntries(Object.entries({ schema: FAST_ENV_LINK_SCHEMA, type: status, row_id: requestId, ...rest }).filter(([, value]) => value !== null));
      const pending = link.decide(row.row_id, row.game_instance_id, "{}", 50);
      toEnv.write(`${JSON.stringify(reply)}\n`);
      const result = await pending;
      let verdict: string;
      if (result.kind === "timeout") verdict = link.lateReplies === 1 && requestId === row.row_id ? "discard_late" : "not_started";
      else if (result.kind === "invalid") verdict = "not_started";
      else if (result.kind === "reply") verdict = binding.check(row, result.reply, sampling).verdict;
      else verdict = "disconnected";
      expect(verdict, item.name).toBe(item.verdict);
      await link.close();
    }
  });
});

describe("fast-env K-game process", () => {
  it("plays a game to its terminal and reports every link message with honest facts", async () => {
    const h = await harness();
    await h.next(event => event.event === "ready");
    h.open(0);
    const opened = await h.next(event => event.event === "opened");
    expect(opened).toMatchObject({ schema: "sts2.policy-runtime/fast-env-event-1", slot: 0, start: { kind: "new_run" }, native: "started" });
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "terminal", terminal: { result: "defeat" }, decisions: 1, native_steps: 1, native: "kept",
      public_facts: { act: 1, floor: 4, hp: 0, max_hp: 75 } });
    const gid = opened.game_instance_id;
    expect(h.linkMessages[0]).toMatchObject({ type: "hello", env_process_id: "env-test", slots: 1 });
    expect(linkOf(h.linkMessages, "episode_open", gid)[0]).toMatchObject({ actor_id: "actor-7", start: { kind: "new_run" }, sampling: SAMPLING });
    const decision = linkOf(h.linkMessages, "decision", gid)[0];
    expect(decision).toMatchObject({ row_id: 1, step_index: 0, catalog_len: 6, in_combat: true, floor: 1 });
    expect(decision.input.observation).toBeTypeOf("object");
    expect(JSON.stringify(decision)).not.toMatch(/bound_action_id/u);
    expect(linkOf(h.linkMessages, "step_outcome", gid)).toEqual([expect.objectContaining({ row_id: 1, status: "completed",
      receipt_sha256: expect.stringMatching(/^[0-9a-f]{64}$/u), public_facts: { act: 1, floor: 4, hp: 0, max_hp: 75 }, terminal: { result: "defeat" } })]);
    expect(linkOf(h.linkMessages, "episode_close", gid)).toEqual([expect.objectContaining({ reason: "terminal", last_step_index: 0 })]);
    const evidence = (await readFile(closed.evidence_dir, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    expect(evidence.map(record => record.kind)).toEqual(["game_open", "step", "boundary", "game_close"]);
    h.send({ cmd: "stop" });
    expect(await h.exit).toBe(0);
    expect(h.events.at(-1)).toMatchObject({ event: "stopped", games_closed: 1 });
  });

  it("reuses the native for the next game and replaces it after an unknown delivery", async () => {
    const h = await harness({ hooks: index => index === 0 ? { delivery: () => "unknown" } : {} });
    h.open(0);
    const first = await h.next(event => event.event === "closed");
    expect(first).toMatchObject({ reason: "unknown", detail: "delivery_unknown", native: "replaced", decisions: 1 });
    expect(linkOf(h.linkMessages, "step_outcome", first.game_instance_id)).toEqual([expect.objectContaining({ status: "unknown", reason: "delivery_unknown" })]);
    expect(h.natives[0]!.shutdowns).toEqual([true]);
    h.open(0);
    const second = await h.next(event => event.event === "closed" && event.game_instance_id !== first.game_instance_id);
    expect(second).toMatchObject({ reason: "terminal", native: "kept" });
    expect((await h.next(event => event.event === "opened" && event.game_instance_id === second.game_instance_id)).native).toBe("started");
    h.open(0);
    await h.next(event => event.event === "opened" && event.native === "reused");
    expect(h.natives).toHaveLength(2);
  });

  it("an inference timeout, refusal or binding mismatch closes the game not_started without a native action", async () => {
    for (const [policy, reason] of [
      [() => "silent", "inference_timeout"],
      [(d: any) => ({ type: "refused", row_id: d.row_id, game_instance_id: d.game_instance_id, catalog_sha256: d.catalog_sha256, error_code: "MU_NOT_RESIDENT" }), "refused"],
      [(d: any) => scored(d, 0, { catalog_sha256: "f".repeat(64) }), "response_binding_mismatch"],
      [(d: any) => scored(d, 0, { applied: { arm_id: "control", decision_class: "noncombat", temperature: 1, uniform_mix: 0 } }), "response_binding_mismatch"],
      [(d: any) => scored(d, 0, { selected_q: 0 }), "response_binding_mismatch"]
    ] as [Policy, string][]) {
      const h = await harness({ policy, timeoutMs: 100 });
      h.open(0);
      const closed = await h.next(event => event.event === "closed");
      expect(closed, reason).toMatchObject({ reason: "not_started", native_steps: 0, native: "kept" });
      expect(closed.detail.startsWith(reason)).toBe(true);
      expect(linkOf(h.linkMessages, "step_outcome")).toEqual([expect.objectContaining({ status: "not_started", reason })]);
      expect(h.natives[0]!.source.steps).toBe(0);
      h.send({ cmd: "stop" });
      expect(await h.exit).toBe(0);
    }
  });

  it("discards a late reply after its row timed out and never applies it to the next game", async () => {
    let held: any = null;
    const h = await harness({ timeoutMs: 80, policy: decision => { if (!held) { held = decision; return "silent"; } return scored(decision); } });
    h.open(0);
    const first = await h.next(event => event.event === "closed");
    expect(first.detail).toBe("inference_timeout");
    h.open(0);
    await h.next(event => event.event === "opened" && event.game_instance_id !== first.game_instance_id);
    // The old row's reply arrives while the new game is running: counted, never applied.
    h.inject(scored(held));
    const second = await h.next(event => event.event === "closed" && event.game_instance_id !== first.game_instance_id);
    expect(second).toMatchObject({ reason: "terminal", decisions: 1 });
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(h.link.lateReplies).toBe(1);
    expect(h.link.invalidReplies).toBe(0);
  });

  it("isolates a crashed native to its slot while the other slot finishes", async () => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const h = await harness({ slots: 2, hooks: index => index === 0 ? { captureThrows: capture => capture === 2 } : { stepGate: () => gate } });
    h.open(0); h.open(1);
    const crashed = await h.next(event => event.event === "closed" && event.slot === 0);
    expect(crashed).toMatchObject({ reason: "env_crash", native: "replaced" });
    expect(linkOf(h.linkMessages, "step_outcome", crashed.game_instance_id)).toEqual([expect.objectContaining({ status: "unknown", reason: "native_crash" })]);
    expect(h.events.some(event => event.event === "closed" && event.slot === 1)).toBe(false);
    release();
    expect(await h.next(event => event.event === "closed" && event.slot === 1)).toMatchObject({ reason: "terminal", native: "kept" });
  });

  it("a native step that throws after hand-off is unknown and replaces only that native", async () => {
    const h = await harness({ hooks: () => ({ stepThrows: () => true }) });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "unknown", native: "replaced" });
    expect(linkOf(h.linkMessages, "step_outcome")).toEqual([expect.objectContaining({ status: "unknown", reason: "delivery_unknown" })]);
  });

  it("a typed not_delivered receipt on the first step is not_started/native_not_delivered", async () => {
    const h = await harness({ hooks: () => ({ delivery: () => "not_delivered" }) });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "not_started", detail: "native_not_delivered", native: "kept" });
    expect(linkOf(h.linkMessages, "step_outcome")).toEqual([expect.objectContaining({ status: "not_started", reason: "native_not_delivered" })]);
  });

  it("Stop waits for the in-flight inference and closes it stop_before_send; stdin EOF also stops", async () => {
    let pending: any = null;
    const h = await harness({ timeoutMs: 1000, policy: decision => { pending = decision; return "silent"; } });
    h.open(0);
    await h.next(event => event.event === "opened");
    while (!pending) await new Promise(resolve => setTimeout(resolve, 5));
    const started = Date.now();
    h.send({ cmd: "stop" });
    expect(await h.exit).toBe(0);
    expect(Date.now() - started).toBeLessThan(500);
    expect(h.events.find(event => event.event === "closed")).toMatchObject({ reason: "stop", detail: "stop_before_send", native: "kept" });
    expect(linkOf(h.linkMessages, "step_outcome")).toEqual([expect.objectContaining({ status: "not_started", reason: "stop_before_send" })]);
    expect(h.natives[0]!.source.steps).toBe(0);
    expect(h.events.at(-1)).toMatchObject({ event: "stopped", games_closed: 1 });
  });

  it("refuses opens on a busy or out-of-range slot and a malformed command is fatal", async () => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const h = await harness({ hooks: () => ({ stepGate: () => gate }) });
    h.open(0);
    await h.next(event => event.event === "opened");
    h.open(0);
    expect(await h.next(event => event.event === "open_refused")).toMatchObject({ reason: "slot_busy" });
    h.open(3);
    expect(await h.next(event => event.event === "open_refused" && event.slot === 3)).toMatchObject({ reason: "slot_out_of_range" });
    release();
    await h.next(event => event.event === "closed");
    h.send({ cmd: "teleport" });
    expect(await h.exit).toBe(1);
    expect(h.events.at(-1)).toMatchObject({ event: "fatal", reason: expect.stringMatching(/^command_invalid/u) });
  });

  it("a dropped decision link closes every game and is fatal", async () => {
    const h = await harness({ policy: () => "disconnect" });
    h.open(0);
    expect(await h.exit).toBe(1);
    expect(h.events.find(event => event.event === "closed")).toMatchObject({ reason: "not_started", detail: "link_disconnected" });
    expect(h.events.at(-1)).toMatchObject({ event: "fatal", reason: expect.stringMatching(/^link_disconnected/u) });
  });

  it("from_save starts on a fresh native and echoes the compact start shape", async () => {
    const h = await harness();
    h.open(0);
    await h.next(event => event.event === "closed");
    const save = { schema: "managed-map-save", sha256: "e".repeat(64), act: 2, floor: 18 };
    h.open(0, { start: { kind: "from_save", save } });
    const opened = await h.next(event => event.event === "opened" && event.start.kind === "from_save");
    expect(opened).toMatchObject({ start: { kind: "from_save", save_sha256: save.sha256 }, native: "started" });
    expect(h.natives[0]!.shutdowns).toEqual([true]);
    expect(linkOf(h.linkMessages, "episode_open", opened.game_instance_id)[0].start).toEqual({ kind: "from_save", save_sha256: save.sha256, act: 2, floor: 18 });
  });

  it("hands WP6 one typed record per acted choice, boundaries, the compact lines and one close", async () => {
    const calls: any[] = [];
    const sink = (): FastEvidenceSink => ({ openGame(meta: FastGameMeta) {
      calls.push(["open", meta]);
      return { nativeLine: line => calls.push(["line", line]), step: record => calls.push(["step", record]),
        async segmentBoundary(range) { calls.push(["boundary", range]); },
        async close(closed) { calls.push(["close", closed]); return "locator"; } };
    } });
    const h = await harness({ sink, hooks: () => ({ terminalAfter: 3, compactLines: true }), edit: config => { config.evidence.boundary_rows = 2; config.compact.enabled = true; } });
    h.open(0, { r7: true, retain_originals: true });
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "terminal", decisions: 3, evidence_dir: "locator" });
    const kinds = calls.map(([kind]) => kind);
    // The mount's full frame (buffered until open), then each step's successor line before that step's record.
    expect(kinds).toEqual(["open", "line", "line", "step", "line", "step", "boundary", "line", "step", "boundary", "close"]);
    expect(calls.filter(([kind]) => kind === "line").map(([, line]) => JSON.parse(line).seq)).toEqual([0, 1, 2, 3]);
    expect(calls[0][1]).toMatchObject({ slot: 0, game_instance_id: closed.game_instance_id, start: { kind: "new_run" },
      r7: true, retain_originals: true, native_lines: true });
    const decisions = linkOf(h.linkMessages, "decision", closed.game_instance_id);
    const steps = calls.filter(([kind]) => kind === "step").map(([, record]) => record);
    for (const [index, record] of steps.entries()) {
      expect(record).toMatchObject({ row_id: decisions[index].row_id, step_index: index, consumption_id: decisions[index].consumption_id,
        observation_sha256: decisions[index].observation_sha256, catalog_sha256: decisions[index].catalog_sha256,
        catalog_len: decisions[index].catalog_len, selected_index: 0, same_content_ordinal: 0, status: "completed",
        frame: { episode_instance: "fake", seq: index }, native_steps: [expect.objectContaining({ fold_index: 0, delivery: "delivered",
          receipt_sha256: expect.stringMatching(/^[0-9a-f]{64}$/u) })] });
      expect(record.input_line).toBe(JSON.stringify(decisions[index]));
      // Recomputed from the line the encoder parses: digest(json_bytes(observation)) / digest(json_bytes(catalog)).
      expect(decisions[index].observation_sha256).toBe(pyJsonSha256(decisions[index].input.observation));
      expect(decisions[index].catalog_sha256).toBe(pyJsonSha256(decisions[index].input.observation.catalog));
      expect(JSON.parse(record.choice_json)).not.toHaveProperty("choice_id");
      expect(record.choice_json).not.toMatch(/bound_action_id/u);
      expect(Number.isSafeInteger(record.occurrence)).toBe(true);
    }
    expect(calls.filter(([kind]) => kind === "boundary").map(([, range]) => range)).toEqual([
      { first_row_id: decisions[0].row_id, last_row_id: decisions[1].row_id }, { first_row_id: decisions[2].row_id, last_row_id: decisions[2].row_id }]);
    expect(calls.at(-1)[1]).toMatchObject({ reason: "terminal", decisions: 3, native_steps: 3, last_step_index: 2,
      terminal: { result: "defeat" }, native_replaced: false, public_facts: expect.objectContaining({ act: 1 }) });
    // An unknown delivery is still recorded, with the native step that was handed over.
    calls.length = 0;
    const unknown = await harness({ sink, hooks: () => ({ delivery: () => "unknown" }) });
    unknown.open(0);
    await unknown.next(event => event.event === "closed");
    expect(calls.filter(([kind]) => kind === "step").map(([, record]) => record)).toEqual([expect.objectContaining({ status: "unknown",
      native_steps: [expect.objectContaining({ delivery: "unknown" })] })]);
    expect(calls.at(-1)[1]).toMatchObject({ reason: "unknown", native_replaced: true });
    expect(calls[0][1]).toMatchObject({ r7: false, retain_originals: false, native_lines: false });
  });

  it("budget closes the game before a further row", async () => {
    const h = await harness({ hooks: () => ({ terminalAfter: 100 }), edit: config => { config.budget.max_decisions_per_game = 3; } });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "budget", detail: "max_decisions_per_game", decisions: 3 });
    const outcomes = linkOf(h.linkMessages, "step_outcome");
    expect(outcomes.map(outcome => outcome.status)).toEqual(["completed", "completed", "completed"]);
  });
});
