// node --test tools/friday/fastchain/tickets.test.mjs (run by python/tests/test_friday_save_tickets.py)
import { test } from "node:test";
import assert from "node:assert/strict";
import { appendFileSync, existsSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PRE_BOSS_FLOORS, SaveTickets, parseSaveStarts } from "./tickets.mjs";

const CANDIDATE = { artifact_sha256: "1".repeat(64), runtime_sts2_sha256: "2".repeat(64) };
const PROGRESS = "c".repeat(64);
const pool = () => mkdtempSync(join(tmpdir(), "save-tickets-"));
const KNOB = { share: 0, random_saves: 2, act_weights: [1, 1, 1], died_later_weight: 0, uses_per_save: 16, pool_gb: 10 };
const driver = (dir, extra = {}) => ({ id: "drv-a", ascension: 10, character: "Defect", pool: dir, progress_sha256: PROGRESS, ...extra });
const make = (dir, knob = {}, extra = {}, options = {}) => new SaveTickets({ ...KNOB, ...knob }, driver(dir, extra), options);
let shas = 0;
/** A `saved` event as the fast env emits it: `floor` global, the Host descriptor's own floor counts within the act. */
const saved = (gameRef, { floor = 20, act = 2, family = `fam-${gameRef}`, ascension = 10, candidate = CANDIDATE, dir = "/pool", bytes = 1000 } = {}) => {
  const sha256 = (++shas).toString(16).padStart(64, "0");
  const path = join(dir, "saves", `${sha256}.json`);
  if (dir !== "/pool") writeFileSync(path, "save");
  return { event: "saved", slot: 0, game_ref: gameRef, game_instance_id: `gi-${gameRef}`, floor, ascension, character: "Defect",
    mu_snapshot_sha256: "a".repeat(64), decisions: 120, public_facts: { act, floor, hp: 40, max_hp: 80 },
    save: { schema: "sts2.host-runtime/managed-map-continue-1", path, bytes, sha256, candidate,
      seed: `seed-${gameRef}`, act, floor: floor - 17, save_boundary: "map_room", source_room_type: "MapRoom",
      parent_runtime_instance_id: "rt", parent_game_continuity_id: family } };
};
const closed = (gameRef, reason = "terminal", extra = {}) => ({ event: "closed", slot: 0, game_ref: gameRef, game_instance_id: `gi-${gameRef}`,
  reason, detail: reason, terminal: null, public_facts: null, evidence_failed: false, ...extra });
const died = (act, floor) => ({ terminal: { result: "defeat" }, public_facts: { act, floor, hp: 0, max_hp: 80 } });
const ledger = (dir) => readFileSync(join(dir, "ledger.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line));
/** One game of a planned group. */
function open(tickets, gameRef, count = 1) {
  const plan = tickets.plan(`grp-${gameRef}`, count);
  return { plan, facts: tickets.opening(gameRef, plan, 0) };
}
/** A source game that saved, was admitted and closed: its saves are in the pool. */
function pooled(tickets, gameRef, saves = [{}], end = {}) {
  const { plan } = open(tickets, gameRef);
  assert.equal(plan.start.kind, "new_run");
  const events = saves.map(options => saved(gameRef, options));
  for (const event of events) tickets.onEvent(event);
  tickets.onAdmission(gameRef, true); tickets.onEvent(closed(gameRef, "terminal", end));
  return events.map(event => event.save);
}

test("config: every key required, unknown keys and bad values refused", () => {
  const parsed = parseSaveStarts(KNOB);
  assert.deepEqual([parsed.share, parsed.random_saves, parsed.uses_per_save, parsed.pool_bytes], [0, 2, 16, 1e10]);
  for (const bad of [{ extra: 1 }, { share: 2 }, { random_saves: 45 }, { act_weights: [1, 1] }, { act_weights: [0, 0, 0] },
    { died_later_weight: -1 }, { uses_per_save: 0 }, { pool_gb: 0 }])
    assert.throws(() => parseSaveStarts({ ...KNOB, ...bad }), /save_starts_config/u);
  assert.throws(() => parseSaveStarts({ ...KNOB, share: undefined }), /save_starts_config:share/u);
  assert.throws(() => make(pool(), {}, { progress_sha256: "x" }), /save_starts_config:driver/u);
});

test("every new run saves before each Boss and at random_saves random floors", () => {
  const dir = pool();
  const tickets = make(dir, { random_saves: 3 });
  for (let n = 0; n < 20; n += 1) {
    const plan = tickets.plan(`g${n}`, 2);
    assert.equal(plan.save_points.directory, join(dir, "saves"));
    const floors = plan.save_points.floors;
    assert.equal(floors.length, 6);
    assert.ok(PRE_BOSS_FLOORS.every(floor => floors.includes(floor)));
    assert.ok(floors.every(floor => floor >= 1 && floor <= 47) && new Set(floors).size === 6);
    assert.deepEqual(floors, [...floors].sort((a, b) => a - b));
  }
  assert.equal(tickets.counters().save_points_games, 40);
  assert.deepEqual(make(pool(), { random_saves: 0 }).plan("g").save_points.floors, PRE_BOSS_FLOORS);
});

test("saves enter the pool only from admitted games with intact evidence, with the global floor and the source's end", () => {
  const dir = pool(), logged = [];
  const tickets = make(dir, {}, {}, { log: record => logged.push(record) });
  const events = ["ok", "late", "refused", "broken"].map(ref => { open(tickets, ref); const event = saved(ref, { floor: 20 + ref.length, dir }); tickets.onEvent(event); return event; });
  tickets.onAdmission("ok", true); tickets.onEvent(closed("ok", "budget"));
  tickets.onEvent(closed("late", "terminal", died(2, 30))); assert.equal(tickets.counters().pool_saves, 1); tickets.onAdmission("late", true);
  tickets.onAdmission("refused", false); tickets.onEvent(closed("refused", "stop"));
  tickets.onAdmission("broken", true); tickets.onEvent(closed("broken", "terminal", { evidence_failed: true }));
  const counters = tickets.counters();
  assert.equal(counters.saves_pooled, 2);
  assert.deepEqual(counters.saves_dropped, { not_admitted: 1, evidence_failed: 1 });
  assert.deepEqual(events.map(event => existsSync(event.save.path)), [true, true, false, false]); // dropped saves' files deleted
  const lines = ledger(dir);
  assert.deepEqual(lines.map(line => [line.kind, line.source_game_ref, line.floor, line.save.floor, line.progress_sha256]),
    [["save", "ok", 22, 5, PROGRESS], ["save", "late", 24, 7, PROGRESS]]);
  assert.deepEqual(lines[1].source_end, { reason: "terminal", act: 2, floor: 30, terminal: "defeat" });
  assert.deepEqual(logged.map(record => [record.event, record.act, record.floor, record.source_floor, record.source_terminal, record.problem]),
    [["save_pooled", 2, 22, null, null, false], ["save_pooled", 2, 24, 30, "defeat", true]]);
});

test("the problem save is the last save before the source died, in the act it died in", () => {
  const dir = pool();
  const tickets = make(dir);
  pooled(tickets, "a", [{ act: 1, floor: 10 }, { act: 2, floor: 20 }, { act: 2, floor: 25 }, { act: 2, floor: 32 }], died(2, 30));
  pooled(tickets, "b", [{ act: 1, floor: 16 }, { act: 2, floor: 32 }], died(3, 40)); // no save in act 3 before death
  pooled(tickets, "c", [{ act: 2, floor: 26 }]); // no natural death on record
  assert.deepEqual(ledger(dir).filter(line => line.problem).map(line => [line.source_game_ref, line.floor]), [["a", 25]]);
});

test("save starts: none before this driver's own save shows its candidate; only its level, candidate and progress; each use once", () => {
  const dir = pool();
  // Saves of another candidate, level or initial progress already in the node pool: never offered.
  pooled(make(dir, {}, { id: "drv-old" }), "old", [{ candidate: { artifact_sha256: "3".repeat(64), runtime_sts2_sha256: "2".repeat(64) } }]);
  pooled(make(dir, {}, { id: "drv-a0", ascension: 0 }), "a0", [{ ascension: 0 }]);
  pooled(make(dir, {}, { id: "drv-p", progress_sha256: "d".repeat(64) }), "prog");
  const tickets = make(dir, { share: 1, uses_per_save: 3 });
  assert.equal(tickets.plan("g0").start.kind, "new_run");
  assert.deepEqual(tickets.counters().new_run_without_ticket, { no_own_save_yet: 1 });
  const [save] = pooled(tickets, "src", [{}], died(2, 30));
  const first = open(tickets, "t1");
  assert.deepEqual(first.plan.start, { kind: "from_save", save });
  assert.equal(first.plan.seed, "seed-src");
  assert.equal(first.plan.save_points, null);
  assert.deepEqual(first.facts, { save: save.sha256.slice(0, 16), save_act: 2, save_floor: 20, use: 0, source_floor: 30, source_terminal: "defeat" });
  const pair = tickets.plan("grp-k", 2);
  assert.deepEqual(pair.tickets.map(item => item.ticket), [`${save.sha256}.u1`, `${save.sha256}.u2`]);
  assert.equal(tickets.plan("grp-x").start.kind, "new_run"); // three uses claimed
  assert.deepEqual(readdirSync(join(dir, "claims")).sort(), [0, 1, 2].map(use => `${save.sha256}.u${use}`));
  tickets.onEvent({ event: "opened", slot: 0, game_ref: "t1", game_instance_id: "gi-t1", start: { kind: "from_save", save_sha256: save.sha256 } });
  tickets.onEvent(closed("t1", "terminal", died(3, 40)));
  const lines = ledger(dir).filter(line => line.ticket === `${save.sha256}.u0`);
  assert.deepEqual(lines.map(line => line.kind), ["claim", "open", "close"]);
  assert.equal(lines[2].floor, 40);
  // Floors played: the source 1-30 and the save start 21-40.
  assert.deepEqual(tickets.counters().covered_floors, [17, 13 + 13, 7]);
});

test("two drivers of one node share the pool: a ticket another driver took is never taken again", () => {
  const dir = pool();
  const a = make(dir, { share: 1, uses_per_save: 2 }), b = make(dir, { share: 1, uses_per_save: 2 }, { id: "drv-b" });
  const [save] = pooled(a, "src");
  pooled(b, "b-src", [{ floor: 30 }]);
  // b holds a stale view of a's ticket: the exclusive claim file still refuses it.
  b.refresh();
  writeFileSync(join(dir, "claims", `${save.sha256}.u0`), "{}\n", { flag: "wx" });
  b.random = () => 0; // the first eligible save is a's
  const plan = b.plan("b1");
  assert.equal(plan.tickets[0].ticket, `${save.sha256}.u1`);
  assert.equal(b.counters().claim_conflicts, 1);
  a.random = () => 0;
  assert.notEqual(a.plan("a1").entry.id, save.sha256); // a sees b's claim: only b-src's save has uses left
});

test("a refused load or an unknown close seals the attempt and pauses the family for every driver; env_crash does not", () => {
  const dir = pool();
  const a = make(dir, { share: 1, uses_per_save: 4 }), b = make(dir, { share: 1, uses_per_save: 4 }, { id: "drv-b" });
  pooled(a, "x", [{ family: "fam-x" }]);
  a.random = () => 0;
  open(a, "t1");
  a.onEvent({ event: "open_refused", slot: 0, game_ref: "t1", reason: "reset_failed:Error:managed_map_continue_file_changed" });
  assert.equal(a.counters().load_failed, 1);
  // b learns its candidate from its own save; the paused family is never offered to it either.
  pooled(b, "y", [{ family: "fam-y" }]);
  b.random = () => 0;
  const tb = open(b, "tb");
  assert.equal(tb.plan.entry.family, "fam-y");
  b.onEvent(closed("tb", "env_crash"));
  assert.equal(open(b, "tb2").plan.start.kind, "from_save");
  b.onEvent(closed("tb2", "unknown"));
  assert.equal(b.plan("tb3").start.kind, "new_run");
  assert.deepEqual(ledger(dir).filter(line => line.kind === "pause").map(line => [line.family, line.why]), [["fam-x", "load_failed"], ["fam-y", "unknown"]]);
  assert.equal(a.plan("t2").start.kind, "new_run");
  assert.deepEqual(b.counters().ticket_closes, { env_crash: 1, unknown: 1 });
});

test("a bad ledger line is counted and reported once; the lines after it still count and planning goes on", () => {
  const dir = pool();
  const a = make(dir);
  const [first] = pooled(a, "src1");
  appendFileSync(join(dir, "ledger.jsonl"), '{"kind":"save","id":"tor\n' + "null\n");
  const [second] = pooled(a, "src2", [{ floor: 30 }]);
  const reports = [], write = process.stderr.write;
  process.stderr.write = (text) => { reports.push(String(text)); return true; };
  let reader;
  try {
    reader = make(dir, { share: 1 }, { id: "drv-r" });
    reader.refresh(); reader.refresh();
    pooled(reader, "own", [{ floor: 40, act: 3 }]);
  } finally { process.stderr.write = write; }
  assert.equal(reports.filter(text => text.startsWith("save_tickets_ledger_bad_line:")).length, 2);
  assert.equal(reader.counters().ledger_bad_lines, 2);
  assert.equal(reader.counters().pool_saves, 3);
  assert.ok(reader.saves.has(second.sha256));
  reader.random = () => 0;
  assert.equal(reader.plan("t1").entry.id, first.sha256);
});

test("the act draw fills the act furthest below its share of floors played; a 0 weight is never drawn", () => {
  const dir = pool();
  const tickets = make(dir, { share: 1, act_weights: [1, 1, 0] });
  const saves = pooled(tickets, "src", [{ act: 1, floor: 10 }, { act: 2, floor: 20 }, { act: 3, floor: 40 }]);
  // The source closed without public facts: nothing played yet, so the draw follows the targets 1:1:0.
  const acts = new Set();
  for (let n = 0; n < 12; n += 1) acts.add(tickets.plan(`d${n}`).entry.act);
  assert.deepEqual([...acts].sort(), [1, 2]);
  // Act 1 played far over its half: every draw goes to act 2.
  tickets.covered = [30, 2, 0];
  for (let n = 0; n < 4; n += 1) assert.equal(tickets.plan(`e${n}`).entry.id, saves[1].sha256);
});

test("died_later_weight = 1 draws problem saves only", () => {
  const dir = pool();
  const tickets = make(dir, { share: 1, died_later_weight: 1 });
  pooled(tickets, "a", [{ act: 2, floor: 20 }, { act: 2, floor: 25 }], died(2, 28));
  pooled(make(dir, {}, { id: "drv-s" }), "b", [{ act: 2, floor: 22 }]);
  for (let n = 0; n < 5; n += 1) assert.equal(tickets.plan(`p${n}`).entry.floor, 25);
  assert.equal(tickets.counters().died_later_draws, 5);
});

test("the pool is capped by size (oldest without an open claim deleted) and used-up saves are deleted", () => {
  const dir = pool();
  const tickets = make(dir, { share: 1, uses_per_save: 1, pool_gb: 0.001 }); // 10^6 bytes: two saves of 400,000
  const [s1] = pooled(tickets, "s1", [{ dir, floor: 20, bytes: 4e5 }]);
  tickets.random = () => 0;
  const t = open(tickets, "t1"); // s1's only use is claimed: an open claim
  assert.equal(t.plan.entry.id, s1.sha256);
  const source = make(dir, { uses_per_save: 1, pool_gb: 0.001 }, { id: "drv-s" }); // another driver of the node
  const [s2] = pooled(source, "s2", [{ dir, floor: 21, bytes: 4e5 }]);
  const [s3] = pooled(source, "s3", [{ dir, floor: 22, bytes: 4e5 }]);
  // Over the cap: s1 is the oldest but claimed, so s2 goes.
  assert.ok(existsSync(s1.path) && !existsSync(s2.path) && existsSync(s3.path));
  tickets.onEvent(closed("t1", "terminal", died(2, 30)));
  assert.ok(!existsSync(s1.path)); // its last use closed
  assert.deepEqual(ledger(dir).filter(line => line.kind === "evict").map(line => [line.id, line.why]), [[s2.sha256, "pool_full"], [s1.sha256, "used"]]);
  assert.deepEqual(source.counters().evicted, { pool_full: 1 });
  assert.deepEqual(tickets.counters().evicted, { used: 1 });
  tickets.refresh();
  assert.equal(tickets.counters().pool_bytes, 4e5);
});

test("a driver with a stale view never deletes a save another driver has just claimed", () => {
  const dir = pool();
  const a = make(dir, { share: 1, uses_per_save: 2 }), b = make(dir, { share: 1, uses_per_save: 2 }, { id: "drv-b" });
  const [s1] = pooled(a, "s1", [{ dir, family: "fam-1" }]);
  a.random = () => 0; b.random = () => 0;
  open(a, "ta"); // a claims s1.u0; a's view is now read
  b.candidate = a.candidate;
  const tb = open(b, "tb"); // b claims s1.u1 after a's last read
  assert.equal(tb.plan.tickets[0].ticket, `${s1.sha256}.u1`);
  // a retires s1 on what it knows locally (an unknown close pauses the family) while b's cold read runs.
  a.onEvent(closed("ta", "unknown"));
  assert.ok(existsSync(s1.path), "s1 deleted under b's open claim");
  b.onEvent(closed("tb", "terminal"));
  assert.ok(!existsSync(s1.path)); // b's close sees the pause and no open claim: s1 goes, once
  assert.deepEqual(ledger(dir).filter(line => line.kind === "evict").map(line => [line.id, line.why, line.driver]), [[s1.sha256, "used", "drv-b"]]);
});

test("a claim whose ledger line is not written yet still keeps its save; trim gives back the claim files it took", () => {
  const dir = pool();
  const a = make(dir, { share: 1, uses_per_save: 3 });
  const [s1] = pooled(a, "s1", [{ dir, family: "fam-1" }]);
  a.random = () => 0;
  open(a, "ta"); // s1.u0
  // Another driver has created s1.u2's claim file (the exclusive create) and not yet appended its claim line.
  writeFileSync(join(dir, "claims", `${s1.sha256}.u2`), "{}\n", { flag: "wx" });
  a.onEvent(closed("ta", "unknown"));
  assert.ok(existsSync(s1.path), "s1 deleted under a claim file");
  assert.deepEqual(readdirSync(join(dir, "claims")).sort(), [`${s1.sha256}.u0`, `${s1.sha256}.u2`]); // u1 given back
  assert.equal(ledger(dir).filter(line => line.kind === "evict").length, 0);
  assert.equal(a.counters().evictions_kept, 1);
});
