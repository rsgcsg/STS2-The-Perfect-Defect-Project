// node --test tools/friday/fastchain/save_probe.test.mjs (run by python/tests/test_friday_save_tickets.py)
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { existsSync, mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { diffPaths, exitCode, main, normalizeBasis, parseArgs, probeSeed, unlockPaths, verdict } from "./save_probe.mjs";

const hash = (text) => createHash("sha256").update(text).digest("hex").slice(0, 8);
/** A deterministic toy game behind the SlotNative calls the probe makes; ids are per-process random hex. */
class FakeNative {
  constructor(options) {
    this.options = options; this.proc = randomBytes(8).toString("hex"); this.sequence = 0; this.state = null; this.loaded = false;
    this.settle = true;
    this.driver = { handle: async () => ({ type: "episode_identity_result", identity: {
      initial_progress: this.loaded ? null : { recipe: "normal-sp-all-unlocked-v1", progress_sha256: "p", run_unlock_state_matches: true, unlock_state: { epochs: ["E1"] } },
      episode_provenance: { origin: { kind: this.loaded ? "map_continue_save" : "seed" }, verdict: "provenance_pass" } } }) };
  }
  get kind() { return this.state.t >= 30 ? "game_over" : this.state.t % 6 === 0 ? "map_navigation" : "combat_turn"; }
  get floor() { return Math.floor(this.state.t / 6) + 1; }
  async reset({ seed }) { this.state = { t: 0, h: hash(seed) }; }
  async loadSave({ save }) {
    const saved = JSON.parse(readFileSync(save.path, "utf8"));
    this.state = { t: saved.t, h: this.options.tamper ? hash("other") : saved.h }; this.loaded = true;
  }
  async saveMap(path) {
    // refuseFloors: refusals that depend on the game state (the same in every process); refuseLoaded: only
    // a loaded process refuses (another game state).
    if (this.kind !== "map_navigation" || (this.options.refuseFloors ?? []).includes(this.floor) || (this.options.refuseLoaded && this.loaded))
      return { kind: "refused", reason: "managed_map_continue_quiescent_map_required" };
    // rngDrift / unlockDrift: a load whose visible play is the same but whose saved state is not.
    const drift = this.loaded ? 1 : 0;
    const text = JSON.stringify({ t: this.state.t, h: this.state.h, rng: { seed: this.state.h, counter: this.options.rngDrift ? drift : 0 },
      save_time: Math.random(), num_reloads: drift,
      ...(this.options.noUnlock ? {} : { players: [{ unlock_state: { epochs: this.options.unlockDrift && this.loaded ? ["E1", "E2"] : ["E1"] } }] }) });
    writeFileSync(path, text, { flag: "wx", mode: 0o600 });
    return { kind: "saved", save: { path, floor: this.floor, act: 1, bytes: text.length, sha256: hash(text) } };
  }
  async capture() {
    const settling = this.settle; this.settle = false;
    const actions = this.kind === "game_over" ? [] : [0, 1, 2].map(k => ({ bound_action_id: `action_${this.proc}${k}`, verb: "activate", label: `${this.kind}-${k}` }));
    // Like the real Host: ids end in this process's snapshot ordinal; every Read response has its wall time.
    const sequence = ++this.sequence;
    return { basis: { reads: [{ kind: "run", response: { observed_at: new Date().toISOString(), observed_snapshot_id: `managed_${this.proc}_s${sequence}`, floor: this.floor } }],
      snapshot: { snapshot_id: `snap_${randomBytes(8).toString("hex")}`, sequence, status: settling ? "settling" : "interactive",
      interaction: { interaction_id: `interaction_${this.proc}_s${sequence}`, kind: this.kind, content: { h: this.state.h, surface: { victory: false } } },
      persistent: { content: { run: { act: 1, floor: this.floor } } }, bound_actions: { actions } } } };
  }
  async step({ boundActionId }) {
    this.state = { t: this.state.t + 1, h: hash(this.state.h + boundActionId.slice(-1)) }; this.settle = true;
    return { delivery: "delivered" };
  }
  async nativeCpuMs() { return 1; }
  async shutdown() {}
}
const factory = (options = {}) => ({ hostVersion: "fake", async start() { return new FakeNative(options); } });
const probe = (options, extra = {}) => probeSeed({ factory: factory(options), seed: "S1", initialProgress: { recipe: "x" },
  out: mkdtempSync(join(tmpdir(), "save-probe-")), steps: 40, loads: 2, sleep: async () => {}, ...extra });

test("normalized captures: per-capture bookkeeping dropped, hex ids renamed consistently", () => {
  const basis = (id, other, seq) => ({ reads: [], snapshot: { snapshot_id: `s_${other}`, sequence: seq, observed_at: seq,
    interaction: { interaction_id: `i_${id}`, target: id, kind: "map_navigation" } } });
  const a = normalizeBasis(basis("0123456789abcdef01", "aaaaaaaaaaaaaaaa", 1));
  assert.equal(a.digest, normalizeBasis(basis("fedcba9876543210fe", "bbbbbbbbbbbbbbbb", 7)).digest);
  assert.match(a.text, /"target":"#0"/u);
  const changed = basis("0123456789abcdef01", "aaaaaaaaaaaaaaaa", 1); changed.snapshot.interaction.kind = "combat_turn";
  assert.notEqual(normalizeBasis(changed).digest, a.digest);
  // Real Host captures: ids end in the process's snapshot ordinal, every Read response has its wall time.
  const host = (proc, seq, at) => ({ reads: [{ kind: "map", response: { observed_at: at, observed_snapshot_id: `managed_${proc}_s${seq}`, content: { floor: 1 } } }],
    snapshot: { interaction: { interaction_id: `interaction_${proc}_s${seq}`, kind: "map_navigation" } } });
  const parent = normalizeBasis(host("aaaaaaaaaaaaaaaa", 3, "2026-11-14T10:26:44.395Z"));
  assert.equal(parent.digest, normalizeBasis(host("bbbbbbbbbbbbbbbb", 1, "2026-11-14T10:26:48.015Z")).digest);
  const moved = host("aaaaaaaaaaaaaaaa", 3, "t"); moved.reads[0].response.content.floor = 2;
  assert.notEqual(normalizeBasis(moved).digest, parent.digest);
  assert.deepEqual(diffPaths({ a: 1, b: { c: [1, 2] }, save_time: 1 }, { a: 1, b: { c: [1, 3] }, save_time: 2 }, new Set(["save_time"])), ["$.b.c.1"]);
  assert.deepEqual(unlockPaths({ players: [{ unlock_state: {} }] }), ["$.players.0.unlock_state"]);
});

test("a faithful load plays the same game: every capture and choice, the end, the post save", async () => {
  const out = mkdtempSync(join(tmpdir(), "save-probe-"));
  const report = await probe({}, { saveFloor: 2, out });
  assert.equal(report.status, "same_game", JSON.stringify(report.failure));
  assert.equal(report.save.floor, 2);
  assert.equal(report.resumed.length, 2);
  for (const resumed of report.resumed) {
    assert.equal(resumed.first_divergence, null);
    assert.equal(resumed.trace_length, report.parent.trace_length);
    // A reload counter is not game state (S1-PROBE: num_reloads, discovered_enemies, can_modify differ).
    assert.deepEqual(resumed.post_save.differing_paths, ["$.num_reloads"]);
    assert.deepEqual(resumed.post_save.unlock_paths, ["$.players.0.unlock_state"]);
    assert.equal(resumed.progress, null);
    assert.equal(resumed.origin, "map_continue_save");
  }
  assert.equal(report.parent.end.end, "terminal");
  assert.equal(report.parent.progress.run_unlock_state_matches, true);
  // Every file carries the seed.
  assert.deepEqual(readdirSync(out).sort(), ["S1-post-load1-f3.json", "S1-post-load2-f3.json", "S1-post-parent-f3.json", "S1-save-f2.json"]);
  const summary = verdict([report]);
  assert.deepEqual(summary, { same_game: true, rng_state: "equal", unlock_state: "carried_in_save_equal", parent_progress_proven: true, resumed_initial_progress_null: true });
  assert.equal(exitCode(summary), 0);
});

test("two seeds in one --out each save at the asked floor and keep their own post saves", async () => {
  const out = mkdtempSync(join(tmpdir(), "save-probe-"));
  const reports = [];
  for (const seed of ["S1", "S/2"]) reports.push(await probeSeed({ factory: factory(), seed, initialProgress: {}, out, saveFloor: 2, steps: 40, loads: 1, sleep: async () => {} }));
  assert.deepEqual(reports.map(report => [report.status, report.save.floor, report.save_refusals.length]), [["same_game", 2, 0], ["same_game", 2, 0]]);
  assert.ok(existsSync(join(out, "S_2-save-f2.json")) && existsSync(join(out, "S_2-post-load1-f3.json")));
  assert.equal(exitCode(verdict(reports)), 0);
});

test("a load that is another game diverges at its first capture; the pair is kept for diffing", async () => {
  const out = mkdtempSync(join(tmpdir(), "save-probe-"));
  const report = await probe({ tamper: true }, { out });
  assert.equal(report.status, "diverged");
  assert.equal(report.resumed[0].first_divergence.index, 0);
  assert.ok(report.resumed[0].post_save.differing_paths.includes("$.h"));
  assert.ok(existsSync(join(out, "diverge-S1-load1-resumed.json")));
  assert.equal(statSync(join(out, "diverge-S1-load1-resumed.json")).mode & 0o777, 0o600);
  assert.equal(verdict([report]).same_game, false);
  assert.equal(exitCode(verdict([report])), 3);
});

test("the same visible play with differing RNG or unlock fields in the post save is not the same game", async () => {
  const rng = await probe({ rngDrift: true });
  assert.equal(rng.status, "diverged");
  assert.ok(rng.resumed.every(resumed => resumed.first_divergence === null));
  assert.deepEqual(rng.resumed[0].post_save.rng_paths_differing, ["$.rng.counter"]);
  assert.equal(verdict([rng]).rng_state, "differs");
  assert.equal(exitCode(verdict([rng])), 3);
  const unlock = await probe({ unlockDrift: true });
  assert.equal(unlock.status, "diverged");
  assert.equal(verdict([unlock]).unlock_state, "differs");
  assert.equal(exitCode(verdict([unlock])), 3);
});

test("a missing post save is incomplete, never the same game; a divergence anywhere still exits 3", async () => {
  // Too few steps to reach a map past the save floor: neither side has a post save.
  const short = await probe({}, { steps: 3 });
  assert.equal(short.status, "incomplete");
  assert.ok(short.resumed.every(resumed => resumed.first_divergence === null && resumed.post_save === null));
  assert.deepEqual(short.resumed[0].post_save_missing, { parent: "not_saved:max_steps", resumed: "not_saved:max_steps" });
  assert.equal(verdict([short]).same_game, null);
  assert.equal(verdict([short]).rng_state, "post_saves_missing");
  assert.equal(exitCode(verdict([short])), 1);
  const diverged = await probe({ tamper: true });
  assert.equal(exitCode(verdict([short, diverged])), 3);
  assert.equal(exitCode(verdict([diverged, short])), 3);
});

test("the save carries no unlock state: the same game, but the unlock half is unproven (exit 4)", async () => {
  const report = await probe({ noUnlock: true });
  assert.equal(report.status, "same_game");
  assert.equal(verdict([report]).unlock_state, "not_in_save_unproven");
  assert.equal(exitCode(verdict([report])), 4);
});

test("a refused save is counted and the next map is tried; an incomplete run has no verdict", async () => {
  const report = await probe({ refuseFloors: [1, 3] });
  assert.deepEqual(report.save_refusals, [{ floor: 1, reason: "managed_map_continue_quiescent_map_required" }]);
  assert.equal(report.save.floor, 2);
  // The post save is refused at floor 3 in every process and taken at floor 4.
  assert.deepEqual(report.parent.post_save_refusals, [{ floor: 3, reason: "managed_map_continue_quiescent_map_required" }]);
  assert.equal(report.parent.post_save_floor, 4);
  assert.ok(report.resumed.every(resumed => resumed.post_save.floor === 4));
  assert.equal(report.status, "same_game");
  // A refusal in the loaded process only is another game state: diverged, at that capture.
  const loadedOnly = await probe({ refuseLoaded: true }, { saveFloor: 2 });
  assert.equal(loadedOnly.status, "diverged");
  assert.equal(loadedOnly.resumed[0].first_divergence.resumed.save_refused, true);
  const ended = await probe({}, { saveFloor: 9 });
  assert.equal(ended.status, "incomplete");
  assert.equal(ended.failure.reason, "ProbeStop:no_save_point:terminal");
  assert.equal(verdict([ended]).same_game, null);
  assert.equal(exitCode(verdict([ended])), 1);
});

test("bad sizes and arguments are refused, never a pass", async () => {
  for (const extra of [{ loads: 0 }, { steps: 0 }, { loads: Number("x") }, { saveFloor: 1.5 }])
    await assert.rejects(probe({}, extra), /save_probe_usage/u);
  const base = ["--pkg", "/p", "--candidate", "/c", "--initial-progress", "/i.json", "--out", "/o"];
  assert.deepEqual(parseArgs(base), { pkg: "/p", candidate: "/c", initialProgressFile: "/i.json", out: "/o", game: null,
    seeds: ["W3P1E0001", "W3P1E0002"], saveFloor: 1, steps: 400, loads: 2, ascension: 10, language: "zh" });
  for (const bad of [["--loads", "0"], ["--steps", "0"], ["--steps", "abc"], ["--loads", "1.5"], ["--loads", "-1"], ["--ascension", "11"],
    ["--seeds", "A,,B"], ["--seeds", "A/1,A_1"], ["--loops", "2"], ["--out", "/again"], ["--steps"]])
    assert.throws(() => parseArgs([...base, ...bad]), /save_probe_usage/u, bad.join(" "));
  assert.throws(() => parseArgs(base.slice(0, 6)), /--out:required/u);
  // main: usage errors and a non-empty --out exit 2 before any process starts.
  const out = mkdtempSync(join(tmpdir(), "save-probe-"));
  writeFileSync(join(out, "S1-save-f1.json"), "{}");
  const write = process.stderr.write;
  process.stderr.write = () => true;
  try {
    assert.equal(await main([...base.slice(0, 6), "--out", out]), 2);
    assert.equal(await main([...base, "--loads", "0"]), 2);
  } finally { process.stderr.write = write; }
});
