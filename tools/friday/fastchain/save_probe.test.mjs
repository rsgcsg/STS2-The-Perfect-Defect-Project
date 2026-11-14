// node --test tools/friday/fastchain/save_probe.test.mjs (run by python/tests/test_friday_save_tickets.py)
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { diffPaths, normalizeBasis, probeSeed, unlockPaths, verdict } from "./save_probe.mjs";

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
    // refuseFloor1: a refusal that depends on the game state (the same in every process).
    if (this.kind !== "map_navigation" || (this.options.refuseFloor1 && this.floor === 1))
      return { kind: "refused", reason: "managed_map_continue_quiescent_map_required" };
    const text = JSON.stringify({ t: this.state.t, h: this.state.h, rng: { seed: this.state.h }, save_time: Math.random(),
      players: [{ unlock_state: { epochs: ["E1"] } }] });
    writeFileSync(path, text, { flag: "wx", mode: 0o600 });
    return { kind: "saved", save: { path, floor: this.floor, act: 1, bytes: text.length, sha256: hash(text) } };
  }
  async capture() {
    const settling = this.settle; this.settle = false;
    const actions = this.kind === "game_over" ? [] : [0, 1, 2].map(k => ({ bound_action_id: `action_${this.proc}${k}`, verb: "activate", label: `${this.kind}-${k}` }));
    return { basis: { reads: [], snapshot: { snapshot_id: `snap_${randomBytes(8).toString("hex")}`, sequence: ++this.sequence, status: settling ? "settling" : "interactive",
      interaction: { interaction_id: `interaction_${this.proc}_s1`, kind: this.kind, content: { h: this.state.h, surface: { victory: false } } },
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
  // Real Host captures: ids end in the process's snapshot sequence, every Read response has its wall time.
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
  const report = await probe({}, { saveFloor: 2 });
  assert.equal(report.status, "same_game", JSON.stringify(report.failure));
  assert.equal(report.save.floor, 2);
  assert.equal(report.resumed.length, 2);
  for (const resumed of report.resumed) {
    assert.equal(resumed.first_divergence, null);
    assert.equal(resumed.trace_length, report.parent.trace_length);
    assert.deepEqual(resumed.post_save.differing_paths, []);
    assert.deepEqual(resumed.post_save.unlock_paths, ["$.players.0.unlock_state"]);
    assert.equal(resumed.progress, null);
    assert.equal(resumed.origin, "map_continue_save");
  }
  assert.equal(report.parent.end.end, "terminal");
  assert.equal(report.parent.progress.run_unlock_state_matches, true);
  assert.deepEqual(verdict([report]), { same_game: true, unlock_state: "carried_in_save_equal", parent_progress_proven: true, resumed_initial_progress_null: true });
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
});

test("a refused save is counted and the next map is tried; an incomplete run has no verdict", async () => {
  const report = await probe({ refuseFloor1: true });
  assert.deepEqual(report.save_refusals, [{ floor: 1, reason: "managed_map_continue_quiescent_map_required" }]);
  assert.equal(report.save.floor, 2);
  assert.equal(report.status, "same_game");
  const ended = await probe({}, { saveFloor: 9 });
  assert.equal(ended.status, "incomplete");
  assert.equal(ended.failure.reason, "ProbeStop:no_save_point:terminal");
  assert.equal(verdict([ended]).same_game, null);
});
