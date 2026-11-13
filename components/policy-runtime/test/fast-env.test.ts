import { readFileSync } from "node:fs";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import { createInterface } from "node:readline";
import { randomUUID } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlayerEnvironmentRestClient, checkPublicBasisLight, decodePublicSourceReceipt } from "@rsgcsg/sts2-connector-client";
import { ReduceStrictSampleFailure } from "@rsgcsg/sts2-connector-client/p1-global";
import { ReferenceGlobalTransport } from "../src/global-transport.js";
import { createGlobalProgramManifest } from "../src/global-program.js";
import { CanonicalTextMemo, CanonicalTextMemoDivergence, pyJsonSha256 } from "../src/fast-env/json-bytes.js";
import { globalDigest } from "../src/global-transport.js";
import { canonicalJson } from "../src/evidence.js";
import { GlobalHttpFixture } from "./global-runtime-fixtures.js";
import { FastEnvProcess, defaultFastEvidenceSink, requireFastEnvManifest } from "../src/fast-env/process.js";
import { FastActionEvidenceSink, readFastGameEvidence } from "../src/global-fast-storage.js";
import { StreamDecisionLink } from "../src/fast-env/link.js";
import { ResponseBinding } from "../src/fast-env/binding.js";
import { MinimalJsonlEvidenceSink, type FastEvidenceSink, type FastGameMeta } from "../src/fast-env/evidence-sink.js";
import { FAST_ENV_COMMAND_SCHEMA, FAST_ENV_LINK_SCHEMA, parseFastEnvCommand, parseFastEnvConfig, parseSampling,
  type FastEnvConfig } from "../src/fast-env/contracts.js";
import { HostSlotNative, SampledCheckFailure, captureSampleOffset, fastGameInstanceId, captureSampleSchedule, type NativeEpisode, type SlotNative, type SlotNativeFactory } from "../src/fast-env/host.js";

/** Equal subtrees become one deep-frozen object, kept across steps (what the E5 Host hands over). */
function internFrozen(value: unknown, table: Map<string, object>): unknown {
  if (value === null || typeof value !== "object") return value;
  const key = `${Array.isArray(value) ? "a" : "o"}${canonicalJson(value)}`;
  const kept = table.get(key);
  if (kept) return kept;
  const copy: any = Array.isArray(value) ? value.map(item => internFrozen(item, table))
    : Object.fromEntries(Object.entries(value).map(([name, item]) => [name, internFrozen(item, table)]));
  table.set(key, Object.freeze(copy));
  return copy;
}

const p1Fixture = (name: string): any => JSON.parse(readFileSync(new URL(`../../connector/sdk/typescript/test/fixtures/p1-global/${name}.json`, import.meta.url), "utf8"));
const SAMPLING = { mode: "temperature_uniform", seed: "actor-7", arm_id: "control",
  classes: { combat: { temperature: 1, uniform_mix: 0 }, noncombat: { temperature: 1, uniform_mix: 0 } } } as const;

interface NativeHooks {
  /** Native steps after which the game is over (default 1). */
  terminalAfter?: number;
  delivery?: (step: number) => string;
  stepThrows?: (step: number) => boolean;
  captureThrows?: (capture: number) => boolean;
  /** This capture's sampled full check disagrees with the light check. */
  sampledCheckFails?: (capture: number) => boolean;
  /** This capture's basis passes the SDK light check (and is vouched for) but its shape is zod-invalid. */
  lightCheckedBadShape?: (capture: number) => boolean;
  stepGate?: () => Promise<void>;
  resetThrows?: boolean;
  /** Emit a fake compact line on every mount and step (evidence tap). */
  compactLines?: boolean;
  /** The native's taint reason (e.g. compact_state_divergence), read after a failure. */
  taintReason?: () => string | null;
  /** reason_code on the receipt of a native step. */
  receiptReasonCode?: (step: number) => string | null;
  /** On this native step the native writes its own divergence report (#433 S3, compact on or off);
   * the Host's receipt is then delivered + compact_state_divergence. */
  divergenceReportOn?: number;
  /** From this native step on, every capture shows a settling snapshot. */
  settlingAfterStep?: number;
  /** Called when the process asks the native for its CPU time; may hold the read open. */
  onCpuRead?: () => void | Promise<void>;
  /** The state after native step n (0 = after the mount): a six-option combat ("multi"), the
   * same combat with one option ("single") or the game over ("terminal"). Overrides terminalAfter. */
  script?: (step: number) => "multi" | "single" | "terminal";
  /** The successor frame of these native steps carries one native combat_end fact. */
  combatEndOn?: number[];
  /** Receipts come back deep-frozen, equal subtrees as the same objects across steps (E5 identity). */
  internReceipts?: boolean;
  /** E5 counters this native's Host session reports. */
  projectionMemo?: () => { hits: number; misses: number; checks: number };
}

/** A state of the scripted fixture game; every one gets its own snapshot id and the fixture's session. */
function scriptedBasis(source: GlobalHttpFixture, kind: "multi" | "single", step: number): any {
  const basis = p1Fixture("synthetic-complete-combat");
  if (kind === "single") {
    // The same combat with only End turn left: one option.
    const actions = basis.snapshot.bound_actions;
    actions.actions = actions.actions.filter((action: any) => action.verb === "end_turn");
    actions.materialized_count = actions.total_count = 1;
    basis.snapshot.interaction.content.surface.playable_cards = [];
  }
  const session = { runtime_instance_id: source.capabilities.host.runtime_instance_id, environment_fingerprint: source.capabilities.environment_fingerprint };
  basis.snapshot.session = session;
  basis.snapshot.snapshot_id = `${basis.snapshot.snapshot_id}-s${step}`;
  basis.snapshot.sequence = step + 1;
  for (const read of basis.reads) {
    read.response.session = session;
    read.response.expected_snapshot_id = read.response.observed_snapshot_id = basis.snapshot.snapshot_id;
  }
  return basis;
}
const COMBAT_END = (step: number) => ({ index: step, act: 1, floor: 1, room_kind: "monster", encounter_id: `encounter-${step}`, result: "victory",
  hp_start: 70, hp_end: 60, max_hp_start: 75, max_hp_end: 75, potions_used: 0, turns: 3 });

/** One slot's "native": a Reference fixture Host behind the real public-basis client. */
class FakeNative implements SlotNative {
  readonly source = new GlobalHttpFixture();
  transport!: ReferenceGlobalTransport;
  shutdowns: boolean[] = [];
  captures = 0;
  checked = 0;
  fullChecks() { return this.checked; }
  constructor(readonly id: number, private readonly hooks: NativeHooks) {}
  async init(): Promise<this> {
    await this.source.start();
    this.transport = new ReferenceGlobalTransport(new PlayerEnvironmentRestClient(this.source.address, 2000), { productVersion: "fixture", clientInstanceId: `client-${this.id}` });
    await this.transport.negotiate(new AbortController().signal);
    return this;
  }
  get runtimeInstanceId() { return this.source.capabilities.host.runtime_instance_id as string; }
  async reset(): Promise<NativeEpisode> {
    if (this.cpuReadInFlight) throw new Error("fixture_one_request_at_a_time");
    this.#lines = []; this.#listener = null; this.checked = 0;
    this.#line('{"frame":"full","seq":0}');
    if (this.hooks.resetThrows) throw new Error("fixture_reset_failed");
    this.source.basis = p1Fixture("synthetic-complete-combat");
    this.source.steps = 0;
    this.source.terminalAfterStep = (this.hooks.terminalAfter ?? 1) <= 1;
    this.#combatEnd = [];
    const first = this.hooks.script?.(0);
    if (first === "single") this.source.basis = scriptedBasis(this.source, "single", 0);
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
    const full = await this.transport.capture({ timeoutMs: 2000 });
    this.checked += 1;
    if (this.hooks.sampledCheckFails?.(this.captures)) throw new SampledCheckFailure(new Error("basis_differs_from_bytes"), full, this.captures - 1);
    if (this.hooks.lightCheckedBadShape?.(this.captures)) {
      // A revision-1 basis (the fast chain's) whose snapshot zod rejects; the light check does not look at the field.
      const { settled_relic_counters: _witness, ...rest } = full.basis as unknown as Record<string, any>;
      const { input_profile: _profile, ...snapshot } = rest.snapshot; // the Managed fast chain carries no navigation profile
      const basis = { ...rest, revision: "1", snapshot: { ...snapshot, observed_at: 42 } };
      checkPublicBasisLight(basis);
      (full as { basis: unknown }).basis = basis;
    }
    if (this.hooks.settlingAfterStep !== undefined && this.source.steps >= this.hooks.settlingAfterStep)
      (full.basis.snapshot as { status: string }).status = "settling";
    return full;
  }
  async step(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }): Promise<unknown> {
    const step = this.source.steps + 1;
    if (this.hooks.stepGate) await this.hooks.stepGate();
    if (this.hooks.stepThrows?.(step)) throw new Error("fixture_native_lost");
    this.source.delivery = this.hooks.delivery?.(step) ?? "delivered";
    this.source.terminalAfterStep = step >= (this.hooks.terminalAfter ?? 1);
    const next = this.hooks.script?.(step);
    if (next !== undefined) {
      this.source.terminalAfterStep = next === "terminal";
      this.source.nextBases = next === "terminal" ? [] : [scriptedBasis(this.source, next, step)];
    }
    const receipt = await this.transport.step(input, { timeoutMs: 2000 });
    if (this.hooks.combatEndOn?.includes(step)) this.#combatEnd.push(COMBAT_END(step));
    if (this.hooks.divergenceReportOn === step) {
      // What the Host tap hands over for the native's report, with or without compact output.
      this.#emit(NATIVE_DIVERGENCE_LINE);
      return { ...receipt, reason_code: "compact_state_divergence" };
    }
    this.#line(`{"frame":"delta","seq":${step}}`);
    const reasonCode = this.hooks.receiptReasonCode?.(step);
    const value = reasonCode === undefined ? receipt : { ...receipt, reason_code: reasonCode };
    return this.hooks.internReceipts ? internFrozen(value, this.#interned) : value;
  }
  #interned = new Map<string, object>();
  projectionMemo() { return this.hooks.projectionMemo?.() ?? null; }
  async release() { await this.transport.release(); }
  taint() { const reason = this.hooks.taintReason?.() ?? null; return { tainted: reason !== null, reason }; }
  #lines: string[] = [];
  #listener: ((line: string) => void) | null = null;
  #line(text: string) { if (this.hooks.compactLines) this.#emit(text); }
  #emit(text: string) { if (this.#listener) this.#listener(text); else this.#lines.push(text); }
  attachLines(listener: ((line: string) => void) | null) {
    this.#listener = listener;
    const buffered = this.#lines; this.#lines = [];
    if (listener) for (const line of buffered) listener(line);
  }
  #combatEnd: Record<string, unknown>[] = [];
  takeCombatEnd() { const facts = this.#combatEnd; this.#combatEnd = []; return facts; }
  frame() { return this.hooks.compactLines ? { episode_instance: "fake", seq: this.source.steps, objects_root_sha256: "b".repeat(64), line_sha256: "c".repeat(64) } : null; }
  cpuReadInFlight = false;
  async nativeCpuMs() { this.cpuReadInFlight = true; try { await this.hooks.onCpuRead?.(); return 1; } finally { this.cpuReadInFlight = false; } }
  async shutdown(force: boolean) { this.shutdowns.push(force); await this.source.close(); }
}

const NATIVE_DIVERGENCE_LINE = '{"type":"error","code":"compact_state_divergence","message":"card_detail_reconcile"}';

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
  edit?: (config: any) => void; sink?: (directory: string) => FastEvidenceSink; onStart?: () => void } = {}) {
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
    settling_timeout_ms: 1000, cold_start: { max_concurrent: 10, directory: join(root, "cold-start") },
    evidence: { directory: join(root, "evidence") } };
  options.edit?.(rawConfig);
  const config: FastEnvConfig = parseFastEnvConfig(rawConfig);
  const factory: SlotNativeFactory = { hostVersion: "fixture-host",
    async start() { options.onStart?.(); const index = created++; const native = await new FakeNative(index, options.hooks?.(index) ?? {}).init(); natives.push(native); return native; } };
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

  it("cold starts hold the node-wide gate through their first reset: with one place, slots mount one at a time", async () => {
    let active = 0, most = 0;
    const h = await harness({ slots: 3, edit: config => { config.cold_start.max_concurrent = 1; },
      onStart: () => { active += 1; most = Math.max(most, active); } });
    await h.next(event => event.event === "ready");
    let opened = 0;
    const counted = h.next(event => {
      if (event.event === "opened") { opened += 1; active -= 1; }
      return opened === 3;
    });
    h.open(0); h.open(1); h.open(2);
    await counted;
    expect(most).toBe(1);
    h.send({ cmd: "status" });
    const status = await h.next(event => event.event === "status");
    expect(status.counters).toMatchObject({ cold_starts: 3 });
    expect(status.counters.cold_start_wait_ms).toBeGreaterThanOrEqual(0);
  });

  it("reuses the native for the next game and replaces it after an unknown delivery", async () => {
    const h = await harness({ hooks: index => index === 0 ? { delivery: () => "unknown" } : {} });
    h.open(0);
    const first = await h.next(event => event.event === "closed");
    expect(first).toMatchObject({ reason: "unknown", native: "replaced", decisions: 1 });
    expect(first.detail).toMatch(/^delivery_unknown:receipt_delivery_unknown:/u);
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

  it("Stop waits until the in-flight row resolves (timeout or reply), then records stop_before_send without acting", async () => {
    // Resolved by timeout: Stop is held for the inference timeout, never longer.
    let pending: any = null;
    const h = await harness({ timeoutMs: 300, policy: decision => { pending = decision; return "silent"; } });
    h.open(0);
    await h.next(event => event.event === "opened");
    while (!pending) await new Promise(resolve => setTimeout(resolve, 5));
    const started = Date.now();
    h.send({ cmd: "stop" });
    expect(await h.exit).toBe(0);
    const waited = Date.now() - started;
    expect(waited).toBeGreaterThanOrEqual(200);
    expect(waited).toBeLessThan(2000);
    expect(h.events.find(event => event.event === "closed")).toMatchObject({ reason: "stop", detail: "stop_before_send", native: "kept" });
    expect(linkOf(h.linkMessages, "step_outcome")).toEqual([expect.objectContaining({ status: "not_started", reason: "stop_before_send" })]);
    expect(h.natives[0]!.source.steps).toBe(0);
    expect(h.events.at(-1)).toMatchObject({ event: "stopped", games_closed: 1 });
    // Resolved by a valid reply that arrives during Stop: still never acted on.
    let held: any = null;
    const r = await harness({ timeoutMs: 5000, policy: decision => { held = decision; return "silent"; } });
    r.open(0);
    while (!held) await new Promise(resolve => setTimeout(resolve, 5));
    r.send({ cmd: "stop" });
    await new Promise(resolve => setTimeout(resolve, 30));
    expect(r.events.some(event => event.event === "closed")).toBe(false);
    r.inject(scored(held));
    expect(await r.exit).toBe(0);
    expect(r.events.find(event => event.event === "closed")).toMatchObject({ reason: "stop", detail: "stop_before_send" });
    expect(linkOf(r.linkMessages, "step_outcome")).toEqual([expect.objectContaining({ status: "not_started", reason: "stop_before_send" })]);
    expect(r.natives[0]!.source.steps).toBe(0);
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
        retainSampledCheckMismatch(mismatch) { calls.push(["mismatch", mismatch]); },
        async close(closed) { calls.push(["close", closed]); return "locator"; } };
    }, async refusedOpen(refused) { calls.push(["refused", refused]); return null; } });
    const h = await harness({ sink, hooks: () => ({ terminalAfter: 3, compactLines: true }), edit: config => { config.evidence.boundary_rows = 2; config.compact.enabled = true; } });
    h.open(0, { r7: true, retain_originals: true });
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "terminal", decisions: 3, evidence_dir: "locator" });
    const kinds = calls.map(([kind]) => kind);
    // The mount's full frame (buffered until open), then each step's successor line before that step's record.
    expect(kinds).toEqual(["open", "line", "line", "step", "line", "step", "boundary", "line", "step", "boundary", "close"]);
    expect(calls.filter(([kind]) => kind === "line").map(([, line]) => JSON.parse(line).seq)).toEqual([0, 1, 2, 3]);
    expect(calls[0][1]).toMatchObject({ slot: 0, game_instance_id: closed.game_instance_id, seed: "seed-0",
      start: { kind: "new_run" }, r7: true, retain_originals: true, native_lines: true });
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
    const unknown = await harness({ sink, hooks: () => ({ delivery: () => "unknown", compactLines: true }), edit: config => { config.compact.enabled = true; } });
    unknown.open(0);
    await unknown.next(event => event.event === "closed");
    // Not R7, not retained: the sink still gets every native line and the input line (retention is its call).
    expect(calls.filter(([kind]) => kind === "step").map(([, record]) => record)).toEqual([expect.objectContaining({ status: "unknown",
      native_steps: [expect.objectContaining({ delivery: "unknown" })], input_line: expect.stringContaining('"type":"decision"') })]);
    expect(calls.filter(([kind]) => kind === "line").map(([, line]) => JSON.parse(line).seq)).toEqual([0, 1]);
    expect(calls.at(-1)[1]).toMatchObject({ reason: "unknown", native_replaced: true });
    expect(calls[0][1]).toMatchObject({ r7: false, retain_originals: false, native_lines: true });
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

describe("fast-env L1 single_choice omit", () => {
  const omit = (extra?: (config: any) => void) => (config: any) => {
    config.inference.single_choice = "omit"; config.budget.max_native_steps_per_game = 400; extra?.(config);
  };
  const outcomes = (h: Awaited<ReturnType<typeof harness>>, gid: string) => linkOf(h.linkMessages, "step_outcome", gid);

  it("forced steps before the terminal: the row is completed with forced_steps and the terminal (B1)", async () => {
    const script = (step: number) => step === 0 ? "multi" : step < 3 ? "single" : "terminal";
    const h = await harness({ hooks: () => ({ script }), edit: omit() });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "terminal", terminal: { result: "defeat" }, decisions: 1, native_steps: 3 });
    expect(linkOf(h.linkMessages, "decision", closed.game_instance_id)).toHaveLength(1);
    expect(outcomes(h, closed.game_instance_id)).toEqual([expect.objectContaining({ row_id: 1, status: "completed", forced_steps: 2,
      terminal: { result: "defeat" }, public_facts: { act: 1, floor: 4, hp: 0, max_hp: 75 } })]);
    h.send({ cmd: "status" });
    expect((await h.next(event => event.event === "status")).counters).toMatchObject({ forced_steps: 2, decisions: 1 });
  });

  it("several forced steps in a row: their count and a combat_end read inside the chain go to the row before it (B1, S2)", async () => {
    const script = (step: number) => step === 0 ? "multi" : step <= 5 ? "single" : step === 6 ? "multi" : "terminal";
    const h = await harness({ hooks: () => ({ script, combatEndOn: [3] }), edit: omit() });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "terminal", decisions: 2, native_steps: 7 });
    const [first, second] = outcomes(h, closed.game_instance_id);
    // The row's successor facts are those of the next row's state, not of the first forced state.
    expect(first).toMatchObject({ status: "completed", forced_steps: 5, terminal: null, combat_end: [COMBAT_END(3)],
      public_facts: publicFactsOf(linkOf(h.linkMessages, "decision", closed.game_instance_id)[1]) });
    expect(second).toMatchObject({ status: "completed", forced_steps: 0, terminal: { result: "defeat" } });
    expect(second).not.toHaveProperty("combat_end");
  });

  it("Stop inside a forced chain commits the row as completed with the last accepted facts", async () => {
    let release!: () => void, entered!: () => void, steps = 0;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const reached = new Promise<void>(resolve => { entered = resolve; });
    const script = (step: number) => step === 0 ? "multi" : "single";
    // The second native step (the first forced one) waits until close_slot is in.
    const stepGate = async () => { if (++steps === 2) { entered(); await gate; } };
    const h = await harness({ hooks: () => ({ script, stepGate }), edit: omit() });
    h.open(0);
    await reached;
    h.send({ cmd: "close_slot", slot: 0 });
    await new Promise(resolve => setTimeout(resolve, 20));
    release();
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "stop", detail: "close_slot", decisions: 1, native_steps: 2 });
    expect(outcomes(h, closed.game_instance_id)).toEqual([expect.objectContaining({ status: "completed", forced_steps: 1, terminal: null })]);
  });

  it("a combat_end read inside a forced chain, then Stop: the fact rides on the completed row (S2)", async () => {
    let release!: () => void, entered!: () => void, steps = 0;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const reached = new Promise<void>(resolve => { entered = resolve; });
    const script = (step: number) => step === 0 ? "multi" : "single";
    const stepGate = async () => { if (++steps === 2) { entered(); await gate; } };
    // The fact comes with the gated forced step; Stop is seen right after its successor is accepted.
    const h = await harness({ hooks: () => ({ script, stepGate, combatEndOn: [2] }), edit: omit() });
    h.open(0);
    await reached;
    h.send({ cmd: "close_slot", slot: 0 });
    await new Promise(resolve => setTimeout(resolve, 20));
    release();
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "stop", detail: "close_slot", decisions: 1, native_steps: 2 });
    expect(outcomes(h, closed.game_instance_id)).toEqual([expect.objectContaining({ status: "completed", forced_steps: 1,
      combat_end: [COMBAT_END(2)] })]);
    h.send({ cmd: "status" });
    expect((await h.next(event => event.event === "status")).counters).toMatchObject({ combat_end_unattributed: 0 });
  });

  it("a forced step whose delivery is unknown closes the game unknown; the row before it stays completed", async () => {
    const script = (step: number) => step === 0 ? "multi" : "single";
    const h = await harness({ hooks: () => ({ script, delivery: step => step === 3 ? "unknown" : "delivered" }), edit: omit() });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "unknown", native: "replaced", decisions: 1, native_steps: 3 });
    expect(closed.detail).toMatch(/^delivery_unknown:receipt_delivery_unknown:/u);
    expect(outcomes(h, closed.game_instance_id)).toEqual([expect.objectContaining({ status: "completed", forced_steps: 1 })]);
    expect(h.natives[0]!.shutdowns).toEqual([true]);
  });

  it("a combat_end read by the forced step that ended the game is counted, never sent", async () => {
    const script = (step: number) => step === 0 ? "multi" : "single";
    const h = await harness({ hooks: () => ({ script, combatEndOn: [3], delivery: step => step === 3 ? "unknown" : "delivered" }), edit: omit() });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "unknown", decisions: 1, native_steps: 3 });
    const [outcome] = outcomes(h, closed.game_instance_id);
    expect(outcome).toMatchObject({ status: "completed", forced_steps: 1 });
    expect(outcome).not.toHaveProperty("combat_end");
    h.send({ cmd: "status" });
    expect((await h.next(event => event.event === "status")).counters).toMatchObject({ combat_end_unattributed: 1 });
  });

  it("a forced step not delivered closes not_started; a forced first state's combat_end has no row and is only counted", async () => {
    const script = (step: number) => step === 0 ? "single" : step === 1 ? "multi" : "single";
    const h = await harness({ hooks: () => ({ script, combatEndOn: [1], delivery: step => step === 3 ? "not_delivered" : "delivered" }), edit: omit() });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "not_started", detail: "forced_step_not_delivered", decisions: 1, native_steps: 3 });
    const [outcome] = outcomes(h, closed.game_instance_id);
    expect(outcome).toMatchObject({ status: "completed", forced_steps: 0 });
    expect(outcome).not.toHaveProperty("combat_end");
    h.send({ cmd: "status" });
    expect((await h.next(event => event.event === "status")).counters).toMatchObject({ combat_end_unattributed: 1, forced_steps: 2 });
  });

  it("a combat that ends on the row's own step, then a forced step: the fact rides on that row (S2)", async () => {
    const script = (step: number) => step === 0 ? "multi" : step === 1 ? "single" : "terminal";
    const h = await harness({ hooks: () => ({ script, combatEndOn: [1, 2] }), edit: omit() });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(outcomes(h, closed.game_instance_id)).toEqual([expect.objectContaining({ status: "completed", forced_steps: 1,
      combat_end: [COMBAT_END(1), COMBAT_END(2)], terminal: { result: "defeat" } })]);
  });

  it("more than 64 forced steps in a row close the game as budget/forced_steps_in_a_row (S1)", async () => {
    const script = (step: number) => step === 0 ? "multi" : "single";
    const h = await harness({ hooks: () => ({ script }), edit: omit() });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "budget", detail: "forced_steps_in_a_row", decisions: 1, native_steps: 65 });
    expect(outcomes(h, closed.game_instance_id)).toEqual([expect.objectContaining({ status: "completed", forced_steps: 64 })]);
    h.send({ cmd: "status" });
    expect((await h.next(event => event.event === "status")).counters).toMatchObject({ forced_chain_limit: 1 });
  });

  it("forced steps that are not in a row never add up to the S1 bound", async () => {
    // Chains of 40 forced steps between rows: 120 forced steps in all, none closes the game.
    const script = (step: number) => step >= 123 ? "terminal" : step % 41 === 0 ? "multi" : "single";
    const h = await harness({ hooks: () => ({ script }), edit: omit() });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "terminal", decisions: 3, native_steps: 123 });
    expect(outcomes(h, closed.game_instance_id).map(o => o.forced_steps)).toEqual([40, 40, 40]);
    h.send({ cmd: "status" });
    expect((await h.next(event => event.event === "status")).counters).toMatchObject({ forced_chain_limit: 0, forced_steps: 120 });
  });

  it("forced steps after the last row are durable: the default sink reads them back after the close (S4)", async () => {
    const script = (step: number) => step === 0 ? "multi" : step < 4 ? "single" : "terminal";
    const h = await harness({ sink: dir => defaultFastEvidenceSink(dir), hooks: () => ({ script }), edit: omit() });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    const read = await readFastGameEvidence(closed.evidence_dir);
    expect(read).toMatchObject({ state: "closed", close: { reason: "terminal", decisions: 1, native_steps: 4 } });
    expect(read.steps.map(step => [step.row_id, step.catalog_len, step.status])).toEqual([
      [1, 6, "completed"], [null, 1, "completed"], [null, 1, "completed"], [null, 1, "completed"]]);
    expect(read.satisfied_steps).toBe(4);
  });

  it("score (the default) keeps today's rows: a one-option state is a row and no forced_steps is sent", async () => {
    const script = (step: number) => step === 0 ? "multi" : step === 1 ? "single" : "terminal";
    const h = await harness({ hooks: () => ({ script }) });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "terminal", decisions: 2, native_steps: 2 });
    expect(linkOf(h.linkMessages, "decision", closed.game_instance_id).map(decision => decision.catalog_len)).toEqual([6, 1]);
    for (const outcome of outcomes(h, closed.game_instance_id)) expect(outcome).not.toHaveProperty("forced_steps");
  });
});

const publicFactsOf = (decision: any) => {
  const run = decision.input.observation.run.content, player = decision.input.observation.player.content;
  return { act: run.act, floor: run.floor, hp: player.hp, max_hp: player.max_hp };
};

describe("fast-env failure paths", () => {
  const recordingSink = (calls: any[]) => (): FastEvidenceSink => ({
    openGame(meta: FastGameMeta) {
      calls.push(["open", meta]);
      return { nativeLine: line => calls.push(["line", line]), step: record => calls.push(["step", record]),
        async segmentBoundary() {}, retainSampledCheckMismatch(mismatch) { calls.push(["mismatch", mismatch]); },
        async close(closed) { calls.push(["close", closed]); return null; } };
    },
    async refusedOpen(refused) { calls.push(["refused", refused]); return "refused-locator"; }
  });

  const expectBuildStopped = async (h: Awaited<ReturnType<typeof harness>>) => {
    expect(await h.next(event => event.event === "build_output_stopped")).toMatchObject({ reason: "compact_state_divergence", slot: 0 });
    h.open(0);
    expect(await h.next(event => event.event === "open_refused" && event.reason === "build_output_stopped")).toMatchObject({ slot: 0 });
  };

  it("a delivered action whose successor frame diverges: row and game unknown/compact_state_divergence, native replaced, build output stopped", async () => {
    const h = await harness({ hooks: () => ({ captureThrows: capture => capture === 2, taintReason: () => "compact_state_divergence" }) });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "unknown", detail: "compact_state_divergence", native: "replaced", native_steps: 1 });
    expect(linkOf(h.linkMessages, "episode_close", closed.game_instance_id)).toEqual([expect.objectContaining({ reason: "unknown" })]);
    expect(linkOf(h.linkMessages, "step_outcome", closed.game_instance_id)).toEqual([expect.objectContaining({ status: "unknown", reason: "compact_state_divergence" })]);
    expect(h.natives[0]!.shutdowns).toEqual([true]);
    await expectBuildStopped(h);
  });

  it("a delivered receipt carrying reason_code compact_state_divergence ends the game the same way (unknown)", async () => {
    const h = await harness({ hooks: () => ({ terminalAfter: 5, receiptReasonCode: () => "compact_state_divergence" }) });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "unknown", detail: "compact_state_divergence", native: "replaced", native_steps: 1 });
    expect(linkOf(h.linkMessages, "step_outcome", closed.game_instance_id)).toEqual([expect.objectContaining({ status: "unknown", reason: "compact_state_divergence" })]);
    // Counted apart from ordinary unknowns (the native-reported #433 S3 path reaches the kernel as this receipt).
    h.send({ cmd: "status" });
    expect((await h.next(event => event.event === "status")).counters).toMatchObject({ compact_divergences: 1, build_output_stopped: "compact_state_divergence" });
    await expectBuildStopped(h);
  });

  it("the Host's projection_memo_divergence (E5) ends the game unknown with its own detail, counter and build stop", async () => {
    for (const via of ["receipt", "capture"] as const) {
      const h = await harness({ hooks: () => via === "receipt"
        ? { terminalAfter: 5, receiptReasonCode: () => "projection_memo_divergence" }
        : { captureThrows: capture => capture === 2, taintReason: () => "projection_memo_divergence" } });
      h.open(0);
      const closed = await h.next(event => event.event === "closed");
      // Not mixed into managed_successor_projection_failed / env_crash, never retried: the game is unknown.
      expect(closed).toMatchObject({ reason: "unknown", detail: "projection_memo_divergence", native: "replaced", native_steps: 1 });
      expect(linkOf(h.linkMessages, "step_outcome", closed.game_instance_id)).toEqual([expect.objectContaining({ status: "unknown" })]);
      h.send({ cmd: "status" });
      expect((await h.next(event => event.event === "status")).counters).toMatchObject({ projection_memo_divergences: 1,
        compact_divergences: 0, build_output_stopped: "projection_memo_divergence" });
      expect(await h.next(event => event.event === "build_output_stopped")).toMatchObject({ reason: "projection_memo_divergence", slot: 0 });
      h.open(0);
      expect(await h.next(event => event.event === "open_refused" && event.reason === "build_output_stopped")).toMatchObject({ slot: 0 });
    }
  });

  it("memo layers all off (default): no memo counters, the status is exactly today's", async () => {
    const h = await harness({ hooks: () => ({ terminalAfter: 2 }) });
    expect(h.config.memo).toEqual({ native_card: null, receipt_canonical: null });
    h.open(0);
    await h.next(event => event.event === "closed");
    h.send({ cmd: "status" });
    expect((await h.next(event => event.event === "status")).counters).not.toHaveProperty("memo");
  });

  it("E4 on, compared on every step (interval 1): digests are globalDigest of the decoded receipts, counters reported", async () => {
    const h = await harness({ hooks: () => ({ terminalAfter: 4, internReceipts: true, projectionMemo: () => ({ hits: 5, misses: 2, checks: 1 }) }),
      edit: config => {
        config.compact = { enabled: true, reconcile_interval: 64, projection_memo: { check_interval: 1 } };
        config.memo = { native_card: { deck: true, hud: true }, receipt_canonical: { check_interval: 1 } };
      } });
    const receipts: unknown[] = [];
    const real = FakeNative.prototype.step;
    const spy = vi.spyOn(FakeNative.prototype, "step").mockImplementation(async function (this: FakeNative, input) {
      const receipt = await real.call(this, input); receipts.push(receipt); return receipt;
    });
    try {
      h.open(0);
      const closed = await h.next(event => event.event === "closed");
      expect(closed).toMatchObject({ reason: "terminal", native_steps: 4 });
      const digests = new Set(receipts.map(receipt => globalDigest(decodePublicSourceReceipt(receipt).data)));
      const outcomes = linkOf(h.linkMessages, "step_outcome", closed.game_instance_id);
      expect(outcomes.length).toBeGreaterThan(0);
      for (const outcome of outcomes) expect(digests.has(outcome.receipt_sha256)).toBe(true);
      h.send({ cmd: "status" });
      const { memo } = (await h.next(event => event.event === "status")).counters;
      expect(memo).toMatchObject({ memo_divergences: 0, native_card: { deck: true, hud: true },
        projection: { hits: 5, misses: 2, checks: 1, check_interval: 1 },
        receipt_canonical: { check_interval: 1, digests: 4, verified: 4 } });
      expect(memo).not.toHaveProperty("reduce");
      // Later receipts reuse the kept text of the subtrees an earlier receipt already had.
      expect(memo.receipt_canonical.hits).toBeGreaterThan(0);
    } finally { spy.mockRestore(); }
  });

  it("an E4 receipt-text divergence: game unknown, build output stopped, the whole process exits fatal", async () => {
    const real = CanonicalTextMemo.prototype.digest;
    let calls = 0;
    const spy = vi.spyOn(CanonicalTextMemo.prototype, "digest").mockImplementation(function (this: CanonicalTextMemo, raw, decoded) {
      if (++calls === 2) throw new CanonicalTextMemoDivergence(1, 40);
      return real.call(this, raw, decoded);
    });
    try {
      const h = await harness({ hooks: () => ({ terminalAfter: 5 }), edit: config => { config.memo = { receipt_canonical: { check_interval: 64 } }; } });
      h.open(0);
      const closed = await h.next(event => event.event === "closed");
      expect(closed).toMatchObject({ reason: "unknown", detail: "receipt_canonical_memo_divergence", native: "replaced", native_steps: 2 });
      expect(linkOf(h.linkMessages, "step_outcome", closed.game_instance_id).map(message => message.status)).toEqual(["completed", "unknown"]);
      expect(await h.next(event => event.event === "build_output_stopped")).toMatchObject({ reason: "receipt_canonical_memo_divergence", slot: 0 });
      const fatal = await h.next(event => event.event === "fatal");
      expect(fatal.reason).toMatch(/^receipt_canonical_memo_divergence:CanonicalTextMemoDivergence:/u);
      expect(fatal.counters.memo).toMatchObject({ memo_divergences: 1 });
      expect(await h.exit).toBe(1);
    } finally { spy.mockRestore(); }
  });

  it("memo config: off unless named, interval 1..1024, unknown keys (and the removed E6 reduce) refused", () => {
    const raw = (memo: unknown) => ({ schema: "sts2.policy-runtime/fast-env-config-1", env_process_id: "e", slots: 1,
      host: { package: "p", candidate_directory: "c", game_directory: null, character: "Defect", language: "en", ascension: 0, request_timeout_ms: 30000 },
      agent_manifest: "m", compact: { enabled: false, reconcile_interval: 64 }, checks: { sample_rate: 0 }, decision_link: { kind: "unix", path: "/l" },
      inference: { timeout_ms: 1000, actor_id: "a", single_choice: "omit", sampling: SAMPLING },
      budget: { max_decisions_per_game: 1, max_native_steps_per_game: 1, game_wall_ms: 1 }, no_progress: null, evidence: { directory: "/e" }, memo });
    expect(parseFastEnvConfig(raw(undefined)).memo).toEqual({ native_card: null, receipt_canonical: null });
    expect(parseFastEnvConfig(raw(null)).memo).toEqual({ native_card: null, receipt_canonical: null });
    expect(parseFastEnvConfig(raw({ native_card: { deck: true, hud: false }, receipt_canonical: { check_interval: 1024 } })).memo)
      .toEqual({ native_card: { deck: true, hud: false }, receipt_canonical: { check_interval: 1024 } });
    expect(() => parseFastEnvConfig(raw({ reduce: { check_interval: 64 } }))).toThrow(/memo.reduce/u);
    expect(() => parseFastEnvConfig(raw({ receipt_canonical: { check_interval: 1025 } }))).toThrow(/memo.receipt_canonical.check_interval/u);
    expect(() => parseFastEnvConfig(raw({ native_card: { deck: true } }))).toThrow(/memo.native_card.hud/u);
    expect(() => parseFastEnvConfig(raw({ projection: {} }))).toThrow(/memo.projection/u);
  });

  it("C1 reduce_light config: off unless named, strict_interval 1..1024, unknown keys refused", () => {
    const raw = (checks: unknown) => ({ schema: "sts2.policy-runtime/fast-env-config-1", env_process_id: "e", slots: 1,
      host: { package: "p", candidate_directory: "c", game_directory: null, character: "Defect", language: "en", ascension: 0, request_timeout_ms: 30000 },
      agent_manifest: "m", compact: { enabled: false, reconcile_interval: 64 }, checks, decision_link: { kind: "unix", path: "/l" },
      inference: { timeout_ms: 1000, actor_id: "a", single_choice: "omit", sampling: SAMPLING },
      budget: { max_decisions_per_game: 1, max_native_steps_per_game: 1, game_wall_ms: 1 }, no_progress: null, evidence: { directory: "/e" } });
    expect(parseFastEnvConfig(raw({ sample_rate: 0 })).checks).toEqual({ sample_rate: 0, reduce_light: null });
    expect(parseFastEnvConfig(raw({ sample_rate: 0, reduce_light: null })).checks.reduce_light).toBeNull();
    expect(parseFastEnvConfig(raw({ sample_rate: 0.01, reduce_light: { strict_interval: 64 } })).checks)
      .toEqual({ sample_rate: 0.01, reduce_light: { strict_interval: 64 } });
    expect(() => parseFastEnvConfig(raw({ sample_rate: 0, reduce_light: { strict_interval: 0 } }))).toThrow(/checks.reduce_light.strict_interval/u);
    expect(() => parseFastEnvConfig(raw({ sample_rate: 0, reduce_light: { strict_interval: 1025 } }))).toThrow(/checks.reduce_light.strict_interval/u);
    expect(() => parseFastEnvConfig(raw({ sample_rate: 0, reduce_light: {} }))).toThrow(/checks.reduce_light.strict_interval/u);
    expect(() => parseFastEnvConfig(raw({ sample_rate: 0, reduce_light: { strict_interval: 8, every: 1 } }))).toThrow(/checks.reduce_light/u);
    expect(() => parseFastEnvConfig(raw({ sample_rate: 0, light: true }))).toThrow(/checks.light/u);
  });

  it("C1 reduce_light: off = no counter (status exactly today's); on = light/strict counts reported", async () => {
    const off = await harness({ hooks: () => ({ terminalAfter: 2 }) });
    expect(off.config.checks.reduce_light).toBeNull();
    off.open(0);
    await off.next(event => event.event === "closed");
    off.send({ cmd: "status" });
    expect((await off.next(event => event.event === "status")).counters).not.toHaveProperty("reduce_light");
    const on = await harness({ hooks: () => ({ terminalAfter: 3 }), edit: config => { config.checks = { ...config.checks, reduce_light: { strict_interval: 8 } }; } });
    on.open(0);
    expect(await on.next(event => event.event === "closed")).toMatchObject({ reason: "terminal" });
    on.send({ cmd: "status" });
    const { reduce_light: light } = (await on.next(event => event.event === "status")).counters;
    // The fake Host never runs the SDK light check, so every reduce here decodes strictly.
    expect(light).toMatchObject({ light: 0, strict_interval: 8 });
    expect(light.strict).toBeGreaterThan(0);
  });

  it("C1 reduce_light: a failing strict sample stops this build's output with its own reason and counter", async () => {
    const h = await harness({ hooks: () => ({ terminalAfter: 3, lightCheckedBadShape: () => true }),
      edit: config => { config.checks = { ...config.checks, reduce_light: { strict_interval: 1024 } }; } });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    // The first light-checked reduce of the process is always a strict sample.
    expect(closed).toMatchObject({ reason: "env_crash", native: "replaced" });
    expect(String(closed.detail)).toMatch(/^reduce:reduce_strict_sample_failed:/u);
    expect(await h.next(event => event.event === "build_output_stopped")).toMatchObject({ reason: "reduce_strict_sample_failed", slot: 0 });
    h.send({ cmd: "status" });
    const status = await h.next(event => event.event === "status");
    expect(status.counters.build_output_stopped).toBe("reduce_strict_sample_failed");
    expect(status.counters.reduce_light).toMatchObject({ sampled: 1, sample_failures: 1, light: 0 });
    h.open(0);
    expect(await h.next(event => event.event === "open_refused" && event.reason === "build_output_stopped")).toMatchObject({ slot: 0 });
  });

  it("C1 reduce_light: a strict-sample failure raised after delivery (e.g. a follow-rule reduce) is the same build stop", async () => {
    // Any reduce outside the step's own capture (resolveFollowBasis) reaches the game's outer catch;
    // inject the failure at a post-delivery site that rethrows (the E4 receipt digest).
    const real = CanonicalTextMemo.prototype.digest;
    let calls = 0;
    const spy = vi.spyOn(CanonicalTextMemo.prototype, "digest").mockImplementation(function (this: CanonicalTextMemo, raw, decoded) {
      if (++calls === 2) throw new ReduceStrictSampleFailure(new Error("follow_basis_shape"));
      return real.call(this, raw, decoded);
    });
    try {
      const h = await harness({ hooks: () => ({ terminalAfter: 5 }), edit: config => {
        config.memo = { receipt_canonical: { check_interval: 64 } }; config.checks = { ...config.checks, reduce_light: { strict_interval: 1024 } }; } });
      h.open(0);
      const closed = await h.next(event => event.event === "closed");
      expect(closed).toMatchObject({ reason: "env_crash", native: "replaced" });
      expect(closed.native_steps).toBeGreaterThan(0);
      expect(String(closed.detail)).toBe("reduce:reduce_strict_sample_failed:follow_basis_shape");
      expect(await h.next(event => event.event === "build_output_stopped")).toMatchObject({ reason: "reduce_strict_sample_failed", slot: 0 });
    } finally { spy.mockRestore(); }
  });

  it("the default sink is WP6's: a real game reads back whole, its close carrying full_checks", async () => {
    const h = await harness({ sink: dir => defaultFastEvidenceSink(dir) });
    expect(defaultFastEvidenceSink("/unused")).toBeInstanceOf(FastActionEvidenceSink);
    h.open(0, { r7: true });
    const closed = await h.next(event => event.event === "closed");
    const read = await readFastGameEvidence(closed.evidence_dir);
    expect(read).toMatchObject({ state: "closed", close: { reason: "terminal", retained: ["r7"], sampled_check_mismatch: null } });
    // An R7 game fully checks every capture, and the close says how many there were.
    expect(read.close!.full_checks).toBe(h.natives[0]!.fullChecks());
    expect(read.close!.full_checks).toBeGreaterThan(1);
    expect(read.steps.length).toBe(closed.decisions);
    expect(closed.evidence_failed).toBe(false);
  });

  it("compact off: the native's own divergence report (#433 S3) is still kept as an original", async () => {
    const h = await harness({ sink: dir => defaultFastEvidenceSink(dir), hooks: () => ({ terminalAfter: 5, divergenceReportOn: 2 }) });
    expect(h.config.compact.enabled).toBe(false);
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "unknown", detail: "compact_state_divergence", native: "replaced", evidence_failed: false });
    const read = await readFastGameEvidence(closed.evidence_dir);
    expect(read.state).toBe("closed");
    expect(read.native_lines).toEqual([NATIVE_DIVERGENCE_LINE]);
    expect(read.close!.retained).toEqual(expect.arrayContaining(["close:unknown", "native_replaced", "step:unknown"]));
  });

  for (const [name, broken] of [
    ["close", (): FastEvidenceSink => ({ openGame: () => ({ nativeLine() {}, step() {}, async segmentBoundary() {}, retainSampledCheckMismatch() {},
      async close() { throw new Error("disk_full"); } }), async refusedOpen() { return null; } })],
    ["boundary", (): FastEvidenceSink => ({ openGame: () => ({ nativeLine() {}, step() {}, async segmentBoundary() { throw new Error("disk_full"); },
      retainSampledCheckMismatch() {}, async close() { throw new Error("fast_evidence_failed:disk_full"); } }), async refusedOpen() { return null; } })]
  ] as const) {
    it(`evidence that cannot be made durable (${name}) is fatal: loud, no further game, exit 1`, async () => {
      const h = await harness({ sink: broken, hooks: () => ({ terminalAfter: 3 }) });
      h.open(0);
      expect(await h.next(event => event.event === "evidence_failed")).toMatchObject({ slot: 0, reason: expect.stringMatching(/disk_full/u) });
      expect(await h.exit).toBe(1);
      expect(h.events.at(-1)).toMatchObject({ event: "fatal", reason: expect.stringMatching(/^evidence_failed:.*disk_full/u) });
      // The game's close is marked, so the manager neither counts nor trains on it.
      const closed = h.events.find(event => event.event === "closed");
      expect(closed).toMatchObject({ reason: "terminal", evidence_dir: null, evidence_failed: true });
      // The decision link hears env_crash, so the assembler never labels the game for SGD.
      expect(linkOf(h.linkMessages, "episode_close", closed.game_instance_id)).toEqual([expect.objectContaining({ reason: "env_crash" })]);
      expect(h.events.filter(event => event.event === "opened")).toHaveLength(1);
    });
  }

  it("a refused open whose originals cannot be written is fatal too", async () => {
    const h = await harness({ sink: () => ({ openGame() { throw new Error("unused"); }, async refusedOpen() { throw new Error("disk_full"); } }),
      hooks: () => ({ resetThrows: true }) });
    h.open(0);
    expect(await h.next(event => event.event === "evidence_failed")).toMatchObject({ slot: 0, game_instance_id: null });
    expect(await h.exit).toBe(1);
    expect(h.events.at(-1)).toMatchObject({ event: "fatal", reason: "evidence_failed:Error:disk_full" });
  });

  it("a divergence before any action of the game is not_started (no outcome in question), native replaced, build output stopped", async () => {
    const h = await harness({ hooks: () => ({ captureThrows: capture => capture === 1, taintReason: () => "compact_state_divergence" }) });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "not_started", detail: "compact_state_divergence", native: "replaced", native_steps: 0, decisions: 0 });
    expect(linkOf(h.linkMessages, "step_outcome", closed.game_instance_id)).toEqual([]);
    expect(h.natives[0]!.source.steps).toBe(0);
    await expectBuildStopped(h);
  });

  it("a mount whose first frame diverges hands its lines to refusedOpen, refuses the open and stops build output", async () => {
    const calls: any[] = [];
    const h = await harness({ sink: recordingSink(calls), hooks: () => ({ resetThrows: true, compactLines: true, taintReason: () => "compact_state_divergence" }),
      edit: config => { config.compact.enabled = true; } });
    h.open(0, { game_ref: "mount-1" });
    expect(await h.next(event => event.event === "open_refused")).toMatchObject({ game_ref: "mount-1", reason: "reset_failed:compact_state_divergence" });
    expect(calls).toEqual([["refused", { slot: 0, game_ref: "mount-1", start: { kind: "new_run" }, reason: "reset_failed:compact_state_divergence",
      native_lines: ['{"frame":"full","seq":0}'] }]]);
    expect(h.natives[0]!.shutdowns).toEqual([true]);
    expect(h.events.some(event => event.event === "opened")).toBe(false);
    await expectBuildStopped(h);
  });

  it("a sampled full check that disagrees ends the game like a capture failure and stops build output", async () => {
    const calls: any[] = [];
    const h = await harness({ sink: recordingSink(calls), hooks: () => ({ sampledCheckFails: capture => capture === 2 }) });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    // The mismatching originals reach this game's evidence before its close, with the count of full checks.
    const kinds = calls.map(([kind]) => kind);
    expect(kinds.indexOf("mismatch")).toBeGreaterThan(-1);
    expect(kinds.indexOf("mismatch")).toBeLessThan(kinds.indexOf("close"));
    const mismatch = calls.find(([kind]) => kind === "mismatch")[1];
    expect(mismatch).toMatchObject({ capture_ordinal: 1, cause: "basis_differs_from_bytes",
      capture: expect.objectContaining({ source_snapshot_id: mismatch.basis.snapshot.snapshot_id }) });
    expect(typeof mismatch.serialized_observation).toBe("string");
    expect(calls.at(-1)[1]).toMatchObject({ reason: "env_crash", native_replaced: true, full_checks: 2 });
    // Same close a capture failure gets today (env_crash; the delivered row's outcome stays unknown).
    expect(closed).toMatchObject({ reason: "env_crash", native: "replaced", native_steps: 1 });
    expect(String(closed.detail)).toMatch(/sampled_check_failed:basis_differs_from_bytes/u);
    expect(linkOf(h.linkMessages, "step_outcome", closed.game_instance_id)).toEqual([expect.objectContaining({ status: "unknown" })]);
    expect(await h.next(event => event.event === "build_output_stopped")).toMatchObject({ reason: "sampled_check_failed", slot: 0 });
    h.open(0);
    expect(await h.next(event => event.event === "open_refused" && event.reason === "build_output_stopped")).toMatchObject({ slot: 0 });
  });

  it("the interim sink writes a sampled mismatch and then keeps every native line of that game", async () => {
    const directory = await mkdtemp(join(tmpdir(), "fast-sink-"));
    const game = new MinimalJsonlEvidenceSink(directory).openGame({ slot: 0, game_ref: "g", game_instance_id: "gi-1", seed: "s",
      start: { kind: "new_run" } as any, r7: false, retain_originals: false, native_lines: true });
    game.nativeLine('{"seq":0}'); game.nativeLine('{"seq":1}');
    game.retainSampledCheckMismatch({ capture_ordinal: 1, cause: "basis_differs_from_bytes", capture: { h: 1 } as any,
      serialized_observation: "{}", basis: { b: 1 } as any });
    game.nativeLine('{"seq":2}');
    const path = await game.close({ reason: "env_crash", detail: "d", decisions: 1, native_steps: 1, last_step_index: 0, terminal: null,
      public_facts: null, native_replaced: true, full_checks: 2 });
    const lines = (await readFile(path!, "utf8")).trimEnd().split("\n");
    expect(JSON.parse(lines[1]!)).toMatchObject({ kind: "sampled_check_mismatch", capture_ordinal: 1, serialized_observation: "{}", basis: { b: 1 } });
    expect(JSON.parse(lines[2]!)).toMatchObject({ kind: "game_close", full_checks: 2, native_lines: 3 });
    expect(lines.slice(3)).toEqual(['{"seq":0}', '{"seq":1}', '{"seq":2}']);
  });

  it("an ordinary reset failure is refused with its lines kept and does not stop build output", async () => {
    const calls: any[] = [];
    const h = await harness({ sink: recordingSink(calls), hooks: () => ({ resetThrows: true, compactLines: true }), edit: config => { config.compact.enabled = true; } });
    h.open(0);
    const refused = await h.next(event => event.event === "open_refused");
    expect(refused.reason).toMatch(/^reset_failed:.*fixture_reset_failed/u);
    expect(calls).toEqual([["refused", expect.objectContaining({ reason: refused.reason, native_lines: ['{"frame":"full","seq":0}'] })]]);
    h.send({ cmd: "status" });
    expect((await h.next(event => event.event === "status")).counters).toMatchObject({ build_output_stopped: null });
  });

  it("delivered, then the successor never settles: the game is unknown and the native is replaced", async () => {
    const h = await harness({ hooks: () => ({ terminalAfter: 5, settlingAfterStep: 1 }), edit: config => { config.settling_timeout_ms = 100; } });
    h.open(0);
    const closed = await h.next(event => event.event === "closed");
    expect(closed).toMatchObject({ reason: "unknown", detail: "delivered_successor_unreadable:managed_settling_stalled", native: "replaced", native_steps: 1 });
    expect(linkOf(h.linkMessages, "step_outcome", closed.game_instance_id)).toEqual([expect.objectContaining({ status: "unknown", reason: "native_crash" })]);
    expect(linkOf(h.linkMessages, "episode_close", closed.game_instance_id)).toEqual([expect.objectContaining({ reason: "unknown" })]);
    expect(h.natives[0]!.shutdowns).toEqual([true]);
  });

  it("status never reads native metrics during a game (one request at a time on the Host channel)", async () => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    let reads = 0;
    const h = await harness({ hooks: () => ({ stepGate: () => gate, onCpuRead: () => { reads += 1; } }) });
    h.open(0);
    await h.next(event => event.event === "opened");
    h.send({ cmd: "status" });
    expect((await h.next(event => event.event === "status")).slots).toEqual([expect.objectContaining({ state: "running", native_cpu_ms: null })]);
    expect(reads).toBe(0);
    release();
    await h.next(event => event.event === "closed");
    h.send({ cmd: "status" });
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(h.events.filter(event => event.event === "status").at(-1).slots).toEqual([expect.objectContaining({ state: "idle", native_cpu_ms: 1 })]);
    expect(reads).toBe(1);
  });

  it("an open that arrives during a between-games metrics read waits for it instead of colliding", async () => {
    let releaseRead!: () => void;
    let holdRead = false;
    const h = await harness({ hooks: () => ({ onCpuRead: () => holdRead ? new Promise<void>(resolve => { releaseRead = resolve; }) : undefined }) });
    h.open(0);
    const first = await h.next(event => event.event === "closed");
    holdRead = true;
    h.send({ cmd: "status" });
    while (!releaseRead) await new Promise(resolve => setTimeout(resolve, 2));
    h.open(0);
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(h.events.some(event => event.event === "open_refused" || event.event === "opened" && event.game_instance_id !== first.game_instance_id)).toBe(false);
    releaseRead();
    const second = await h.next(event => event.event === "closed" && event.game_instance_id !== first.game_instance_id);
    expect(second).toMatchObject({ reason: "terminal", native: "kept" });
    expect(h.events.some(event => event.event === "open_refused")).toBe(false);
  });

  it("close_slot for another game or an idle slot is ignored and counted; the matching one stops the game; open clears it", async () => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const h = await harness({ hooks: () => ({ terminalAfter: 100, stepGate: () => gate }), edit: config => { config.budget.max_decisions_per_game = 3; } });
    const counters = async () => {
      const before = h.events.filter(event => event.event === "status").length;
      h.send({ cmd: "status" });
      for (;;) { const all = h.events.filter(event => event.event === "status"); if (all.length > before) return all.at(-1).counters; await new Promise(resolve => setTimeout(resolve, 5)); } };
    h.open(0);
    const opened = await h.next(event => event.event === "opened");
    h.send({ cmd: "close_slot", slot: 0, game_instance_id: "gi-someone-else" });
    expect((await counters()).stale_close_slot_ignored).toBe(1);
    h.send({ cmd: "close_slot", slot: 0, game_instance_id: opened.game_instance_id });
    release();
    expect(await h.next(event => event.event === "closed")).toMatchObject({ game_instance_id: opened.game_instance_id, reason: "stop", detail: "close_slot" });
    h.send({ cmd: "close_slot", slot: 0, game_instance_id: opened.game_instance_id });
    h.send({ cmd: "close_slot", slot: 0 });
    expect((await counters()).stale_close_slot_ignored).toBe(3);
    h.open(0);
    const second = await h.next(event => event.event === "closed" && event.game_instance_id !== opened.game_instance_id);
    expect(second).toMatchObject({ reason: "budget", detail: "max_decisions_per_game", decisions: 3 });
  });
});

describe("HostSlotNative", () => {
  it("passes the Host's control epoch through unchanged (an opaque id, not a number)", async () => {
    const requests: any[] = [];
    let epoch: unknown = "5f0c2a8e-7d3b-4c1a-9e2f-0b6d8a4c3e71";
    const driver: any = {
      async handle(request: any) {
        requests.push(request);
        if (request.command === "claim_control") return { type: "claim_control_result", control_token: "token-1", control_epoch: epoch };
        if (request.command === "step") return { type: "step_result", receipt: { ok: true } };
        return { type: `${request.command}_result` };
      },
      status: () => ({ game_continuity_id: "continuity-1" }),
      async shutdown() {}
    };
    const started: any = { session: { episodeInstance: "episode-1", episodeStart: { kind: "new_run" } }, runtime: { adapterRuntimeInstanceId: "runtime-1" } };
    const native = new HostSlotNative(started, driver);
    expect(await native.reset({ seed: "s", initialProgress: null, r7: false })).toEqual({ gameInstanceId: "runtime-1:episode-1", start: { kind: "new_run" } });
    expect(await native.step({ requestId: "r", expectedSnapshotId: "snap", boundActionId: "b" })).toEqual({ ok: true });
    await native.release();
    const step = requests.find(request => request.command === "step"), released = requests.find(request => request.command === "release_control");
    expect(step).toMatchObject({ control_token: "token-1", control_epoch: epoch, mutation_request_id: "r", expected_game_continuity_id: "continuity-1" });
    expect(released).toMatchObject({ control_token: "token-1", control_epoch: epoch });
    epoch = 7;
    await native.reset({ seed: "s", initialProgress: null, r7: false });
    await native.step({ requestId: "r2", expectedSnapshotId: "snap", boundActionId: "b" });
    expect(requests.filter(request => request.command === "step").at(-1).control_epoch).toBe(7);
    epoch = null;
    await expect(native.reset({ seed: "s", initialProgress: null, r7: false })).rejects.toThrow(/fast_env_claim_result_drift/u);
  });

  it("scopes the native episode id by the Host launch, so two env nodes with the same native id stay distinct", async () => {
    // h100d env-n6/n7 both ran PID 565295: the native ids were equal, the fenced learner said closed_twice.
    const nativeId = "native-compact:565295:1";
    const driver = (): any => ({
      async handle(request: any) {
        if (request.command === "claim_control") return { type: "claim_control_result", control_token: "t", control_epoch: 1 };
        return { type: `${request.command}_result` };
      },
      status: () => ({ game_continuity_id: "c" }), async shutdown() {}
    });
    const open = async (runtime: string) => {
      const started: any = { session: { episodeInstance: nativeId, episodeStart: { kind: "new_run" } }, runtime: { adapterRuntimeInstanceId: runtime } };
      return (await new HostSlotNative(started, driver()).reset({ seed: "s", initialProgress: null, r7: false })).gameInstanceId;
    };
    const runtimeA = randomUUID().replaceAll("-", ""), runtimeB = randomUUID().replaceAll("-", "");
    const a = await open(runtimeA), b = await open(runtimeB);
    expect(a).toBe(fastGameInstanceId(runtimeA, nativeId));
    expect(a).not.toBe(b);
    // The native fact is kept whole inside the id; the id is a valid wire id (contracts ID pattern).
    for (const id of [a, b]) { expect(id!.endsWith(`:${nativeId}`)).toBe(true); expect(id).toMatch(/^[A-Za-z0-9._:-]{1,128}$/u); }
  });

  it("light-checks every capture, fully verifies the first and the sampled ones, and every capture of an R7 game", async () => {
    const fixture = await new FakeNative(0, {}).init();
    await fixture.reset();
    const good = await fixture.transport.capture({ timeoutMs: 2000 });
    await fixture.shutdown(true);
    const continuity = good.capture.game_continuity_id;
    let serve: () => any = () => good;
    const requests: any[] = [];
    const driver: any = {
      async handle(request: any) {
        requests.push(request);
        if (request.command === "claim_control") return { type: "claim_control_result", control_token: "t", control_epoch: 1 };
        if (request.command === "capture") return { type: "capture_result", full: serve() };
        return { type: `${request.command}_result` };
      },
      status: () => ({ game_continuity_id: continuity }), async shutdown() {}
    };
    const started: any = { session: { episodeInstance: "e", episodeStart: null }, runtime: { adapterRuntimeInstanceId: good.capture.session.runtime_instance_id } };
    // Bytes no longer match the basis object: only a full check can see it.
    const drifted = () => ({ ...good, basis: { ...good.basis, missing: [...good.basis.missing] , capture_status: good.basis.capture_status, reads: good.basis.reads.slice().reverse() } });
    const native = new HostSlotNative(started, driver, 0);
    await native.reset({ seed: "s", initialProgress: null, r7: false });
    expect(await native.capture()).not.toBe(good); // first capture of the game: verified copy
    expect(await native.capture()).toBe(good);     // unsampled: the Host's object as built
    serve = drifted;
    await expect(native.capture()).resolves.toBeDefined();
    expect(native.fullChecks()).toBe(1);
    expect(requests.filter(request => request.command === "capture").every(request => request.full_check === false)).toBe(true);
    await native.reset({ seed: "s", initialProgress: null, r7: false });
    expect(native.fullChecks()).toBe(0);
    const failure = await native.capture().catch(error => error);
    expect(failure).toBeInstanceOf(SampledCheckFailure);
    // The originals the light path accepted travel with the failure, unchanged.
    const sent = drifted();
    expect(failure.mismatch).toMatchObject({ capture_ordinal: 0, cause: "basis_differs_from_bytes" });
    expect(failure.mismatch.serialized_observation).toBe(sent.serializedObservation);
    expect(canonicalJson(failure.mismatch.basis)).toBe(canonicalJson(sent.basis));
    expect(failure.mismatch.capture).toEqual(sent.capture);
    serve = () => good;
    // Compact output off (no episode id): the draw comes from the open's seed, still sampled.
    started.session.episodeInstance = null;
    await native.reset({ seed: "s", initialProgress: null, r7: false });
    await native.capture(); await native.capture();
    expect(native.fullChecks()).toBe(1);
    started.session.episodeInstance = "e";
    await native.reset({ seed: "s", initialProgress: null, r7: true });
    await native.capture(); serve = drifted;
    await expect(native.capture()).rejects.toThrow(/sampled_check_failed/u);
    serve = () => ({ ...good, capture: { ...good.capture, source_snapshot_id: "other" } });
    await expect(native.capture()).rejects.toThrow(/fast_env_capture_world_drift/u);
  });

  it("the capture schedule is derived from the episode id, draws about the configured share and always includes the first capture", () => {
    const picks = (id: string) => { const schedule = captureSampleSchedule(0.01, false, captureSampleOffset(id));
      return Array.from({ length: 100_000 }, (_, i) => i).filter(i => schedule(i)); };
    const hits = picks("episode-a");
    expect(hits[0]).toBe(0);
    expect(hits.length).toBeGreaterThan(800); expect(hits.length).toBeLessThan(1200);
    // Reproducible from the id alone; another episode draws other captures.
    expect(picks("episode-a")).toEqual(hits);
    expect(picks("episode-b")).not.toEqual(hits);
    expect(captureSampleOffset("episode-a")).toBe(captureSampleOffset("episode-a"));
    expect(Array.from({ length: 50 }, (_, i) => captureSampleSchedule(0.01, true, 1)(i)).every(Boolean)).toBe(true);
    expect(Array.from({ length: 50 }, (_, i) => captureSampleSchedule(0.01, false, null)(i)).every(Boolean)).toBe(true);
    expect(captureSampleSchedule(0, false, 1)(1)).toBe(false);
  });
});
