// node --test tools/friday/fastchain/save_replay_probe.test.mjs (run by python/tests/test_friday_save_tickets.py)
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { keptRow, rowDivergence, sessionRow, summarize } from "./save_replay_probe.mjs";

test("a row is the same across sessions whose entity ids and catalog order differ; a real change is a divergence", () => {
  // Session A: hand [Strike e12, Zap e9], enemy e3. Session B (a load): other ids, the catalog order flipped.
  const obsA = { combat: { hand: [{ entity_id: "e12", name: "Strike" }, { entity_id: "e9", name: "Zap" }], enemies: [{ entity_id: "e3", hp: 10 }] }, catalog: [] };
  const catA = [{ choice_id: "c0", kind: "play", subject: "e12", target: "e3", label: "Play Strike" },
    { choice_id: "c1", kind: "play", subject: "e9", target: null, label: "Play Zap" }];
  const obsB = { combat: { hand: [{ entity_id: "e4", name: "Strike" }, { entity_id: "e5", name: "Zap" }], enemies: [{ entity_id: "e1", hp: 10 }] }, catalog: [] };
  const catB = [{ choice_id: "c0", kind: "play", subject: "e5", target: null, label: "Play Zap" },
    { choice_id: "c1", kind: "play", subject: "e4", target: "e1", label: "Play Strike" }];
  const a = sessionRow(obsA, catA), b = sessionRow(obsB, catB);
  const kept = keptRow(a, a.items[0]);
  assert.equal(a.full, b.full);
  assert.equal(rowDivergence(kept, b, 5, 0), null);
  assert.equal(b.items.indexOf(kept.chosen), 1); // the follower plays the parent's choice by content
  const obsC = structuredClone(obsB);
  obsC.combat.enemies[0].hp = 9;
  const divergence = rowDivergence(kept, sessionRow(obsC, catB), 5, 0);
  assert.equal(divergence.entity_ids_only, false);
  assert.deepEqual(divergence.sections, ["combat"]);
  assert.deepEqual(divergence.diff.paths, ["$.combat.enemies.0.hp"]);
});

test("summary: same game with the known save-path classes; a blind row divergence fails the pair", () => {
  const dir = mkdtempSync(join(tmpdir(), "replay-probe-"));
  const save = (name, value) => { const path = join(dir, name); writeFileSync(path, JSON.stringify(value)); return path; };
  const base = { num_reloads: 0, players: [{ rng: { a: 1 }, unlock_state: { x: 1 } }], acts: [{ saved_map: { points: [{ can_modify: null }] } }] };
  const obs = (hp) => ({ combat: { hand: [{ entity_id: "e1", name: "Strike" }], enemies: [{ entity_id: "e2", hp }] }, catalog: [] });
  const cat = [{ choice_id: "c0", kind: "play", subject: "e1", target: "e2", label: "Play" }];
  const rows = [10, 9, 8].map(hp => { const row = sessionRow(obs(hp), cat); return keptRow(row, row.items[0]); });
  const script = { seed: "S1", max_floor: 20, result: "loss" };
  const end = { reason: "terminal", terminal: { result: "loss" } };
  const job = (fields) => ({ divergence: null, kinds: new Map(), first_divergence: null, first_blind_divergence: null, close: end, script, ...fields });
  const parent = job({ kind: "parent", offset: 0, rows, instance: "i1", kinds: new Map([[18, "map"]]),
    attempts: [{ floor: 18, outcome: "saved", path: save("p18.json", base) }, { floor: 19, outcome: "saved", path: save("p19.json", base) }] });
  const loaded = structuredClone(base);
  loaded.num_reloads = 1; loaded.acts[0].saved_map.points[0].can_modify = true; loaded.players[0].discovered_enemies = ["M"];
  const child = job({ kind: "child", parent, offset: 1, floor: 18, reason: "pre_boss", instance: "i2",
    save: { act: 2, floor: 1, source_room_type: "RestSiteRoom" }, rows: rows.slice(1).map(row => ({ full: row.full, blind: row.blind })),
    attempts: [{ floor: 18, outcome: "saved", path: save("c18.json", loaded) }, { floor: 19, outcome: "saved", path: save("c19.json", loaded) }] });
  const control = job({ kind: "control", parent, offset: 0, rows: rows.map(row => ({ full: row.full, blind: row.blind })), attempts: [], instance: "i3" });
  const report = summarize([parent, control, child]);
  assert.equal(report.verdict.exit, 0);
  assert.equal(report.pairs[0].status, "same_game");
  assert.deepEqual(report.pairs[0].post_save_path_classes, { num_reloads: 2, discovered: 2, map_can_modify: 2 });
  child.first_blind_divergence = child.first_divergence = rowDivergence(rows[2], sessionRow(obs(7), cat), 2, 1);
  assert.equal(summarize([parent, control, child]).pairs[0].status, "diverged_rows");
  // An RNG path that differs after the save is a divergence, not a known class.
  child.first_blind_divergence = child.first_divergence = null;
  const drifted = structuredClone(loaded);
  drifted.players[0].rng.a = 2;
  child.attempts[1].path = save("c19b.json", drifted);
  assert.notEqual(summarize([parent, control, child]).pairs[0].status, "same_game");
});
