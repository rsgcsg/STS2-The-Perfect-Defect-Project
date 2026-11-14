// node --test tools/friday/fastchain/tickets.test.mjs (run by python/tests/test_friday_save_tickets.py)
import { test } from "node:test";
import assert from "node:assert/strict";
import { appendFileSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SaveTickets, TICKETS_SCHEMA, parseTicketsConfig } from "./tickets.mjs";

const CANDIDATE = { artifact_sha256: "1".repeat(64), runtime_sts2_sha256: "2".repeat(64) };
const DRIVER = { id: "drv-a", ascension: 10, character: "Defect" };
const pool = () => mkdtempSync(join(tmpdir(), "save-tickets-"));
const config = (dir, extra = {}) => ({ schema: TICKETS_SCHEMA, pool: dir, ...extra });
let shas = 0;
/** A `saved` event as the fast env emits it (the save is the Host descriptor). */
const saved = (gameRef, { floor = 20, act = 2, family = `fam-${gameRef}`, ascension = 10, candidate = CANDIDATE } = {}) => {
  const sha256 = (++shas).toString(16).padStart(64, "0");
  return { event: "saved", slot: 0, game_ref: gameRef, game_instance_id: `gi-${gameRef}`, floor, ascension, character: "Defect",
    mu_snapshot_sha256: "a".repeat(64), decisions: 120, public_facts: { act, floor, hp: 40, max_hp: 80 },
    save: { schema: "sts2.host-runtime/managed-map-continue-1", path: `/pool/saves/${sha256}.json`, bytes: 10, sha256, candidate,
      seed: `seed-${gameRef}`, act, floor, save_boundary: "map_room", source_room_type: "MapRoom",
      parent_runtime_instance_id: "rt", parent_game_continuity_id: family } };
};
const closed = (gameRef, reason = "terminal", extra = {}) => ({ event: "closed", slot: 0, game_ref: gameRef, game_instance_id: `gi-${gameRef}`,
  reason, detail: reason, terminal: null, public_facts: null, evidence_failed: false, ...extra });
const ledger = (dir) => readFileSync(join(dir, "ledger.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line));
/** A source game that saved, was admitted and closed: its saves are in the pool. */
function pooled(tickets, gameRef, options) {
  const plan = tickets.plan(gameRef);
  assert.equal(plan.start.kind, "new_run");
  const event = saved(gameRef, options);
  tickets.onEvent(event); tickets.onAdmission(gameRef, true); tickets.onEvent(closed(gameRef));
  return event.save;
}

test("config: defaults, unknown keys and bad values are refused", () => {
  const parsed = parseTicketsConfig(config("/pool"));
  assert.deepEqual([parsed.endgame_share, parsed.save_every_games, parsed.saves_per_game, parsed.uses_per_save, parsed.max_live_saves],
    [0.3, 2, 3, 8, 2000]);
  for (const bad of [{ pool: "rel" }, { extra: 1 }, { endgame_share: 2 }, { save_floors: { min: 5, max: 2 } }, { save_floors: { min: 1, max: 2, every: 1 } },
    { floor_weights: [{ min: 0, max: 1 }] }, { uses_per_save: 0 }, { schema: "x" }])
    assert.throws(() => parseTicketsConfig({ ...config("/pool"), ...bad }), /save_tickets_config/u);
});

test("save points: one new run in N, floors drawn from the band, none while the pool is full", () => {
  const dir = pool();
  const tickets = new SaveTickets(config(dir, { save_every_games: 3, saves_per_game: 2, save_floors: { min: 10, max: 14 }, max_live_saves: 1 }), DRIVER);
  const plans = ["g1", "g2", "g3", "g4"].map(ref => tickets.plan(ref));
  assert.deepEqual(plans.map(plan => plan.save_points === null), [false, true, true, false]);
  for (const plan of [plans[0], plans[3]]) {
    assert.equal(plan.save_points.directory, join(dir, "saves"));
    assert.equal(plan.save_points.floors.length, 2);
    assert.ok(plan.save_points.floors.every(floor => floor >= 10 && floor <= 14));
    assert.equal(new Set(plan.save_points.floors).size, 2);
  }
  assert.ok(plans.every(plan => plan.seed === null && plan.start.kind === "new_run"));
  // One live save fills this pool: no further save points.
  tickets.onEvent(saved("g1")); tickets.onAdmission("g1", true); tickets.onEvent(closed("g1"));
  for (const ref of ["g5", "g6", "g7"]) assert.equal(tickets.plan(ref).save_points, null);
});

test("saves enter the pool only from admitted games with intact evidence, also when admission answers after the close", () => {
  const dir = pool();
  const tickets = new SaveTickets(config(dir, { endgame_share: 0 }), DRIVER);
  for (const ref of ["ok", "late", "refused", "broken"]) { tickets.plan(ref); tickets.onEvent(saved(ref)); }
  tickets.onAdmission("ok", true); tickets.onEvent(closed("ok", "budget"));
  tickets.onEvent(closed("late")); assert.equal(tickets.counters().pool_saves, 1); tickets.onAdmission("late", true);
  tickets.onAdmission("refused", false); tickets.onEvent(closed("refused", "stop"));
  tickets.onAdmission("broken", true); tickets.onEvent(closed("broken", "terminal", { evidence_failed: true }));
  const counters = tickets.counters();
  assert.equal(counters.saves_pooled, 2);
  assert.deepEqual(counters.saves_dropped, { not_admitted: 1, evidence_failed: 1 });
  assert.deepEqual(ledger(dir).map(line => [line.kind, line.source_game_ref, line.source_close]), [["save", "ok", "budget"], ["save", "late", "terminal"]]);
});

test("endgame tickets: none before this driver's own save shows its candidate; each ticket consumed once", () => {
  const dir = pool();
  // A save of another candidate already in the node pool (an older build): never offered.
  const other = new SaveTickets(config(dir, { endgame_share: 0 }), { ...DRIVER, id: "drv-old" });
  pooled(other, "old", { candidate: { artifact_sha256: "3".repeat(64), runtime_sts2_sha256: "2".repeat(64) } });
  const tickets = new SaveTickets(config(dir, { endgame_share: 1, uses_per_save: 2 }), DRIVER);
  assert.equal(tickets.plan("g0").start.kind, "new_run");
  assert.deepEqual(tickets.counters().new_run_without_ticket, { no_own_save_yet: 1 });
  tickets.onEvent(closed("g0"));
  const save = pooled(tickets, "src");
  const first = tickets.plan("t1"), second = tickets.plan("t2"), third = tickets.plan("t3");
  assert.deepEqual(first, { seed: "seed-src", start: { kind: "from_save", save }, save_points: null, ticket: `${save.sha256}.u0` });
  assert.equal(second.ticket, `${save.sha256}.u1`);
  assert.equal(third.start.kind, "new_run");
  assert.deepEqual(readdirSync(join(dir, "claims")).sort(), [`${save.sha256}.u0`, `${save.sha256}.u1`]);
  tickets.onEvent({ event: "opened", slot: 0, game_ref: "t1", game_instance_id: "gi-t1", start: { kind: "from_save", save_sha256: save.sha256 } });
  tickets.onEvent(closed("t1", "terminal", { public_facts: { act: 3, floor: 40, hp: 0, max_hp: 80 } }));
  const lines = ledger(dir).filter(line => line.ticket === first.ticket);
  assert.deepEqual(lines.map(line => line.kind), ["claim", "open", "close"]);
  assert.equal(lines[2].floor, 40);
});

test("two drivers of one node share the pool: a ticket another driver took is never taken again", () => {
  const dir = pool();
  const a = new SaveTickets(config(dir, { endgame_share: 1, uses_per_save: 1 }), DRIVER);
  const b = new SaveTickets(config(dir, { endgame_share: 1, uses_per_save: 1 }), { ...DRIVER, id: "drv-b" });
  const save = pooled(a, "src");
  pooled(b, "b-src", { floor: 30 });
  // b holds a stale view of a's ticket: the exclusive claim file still refuses it.
  b.refresh();
  writeFileSync(join(dir, "claims", `${save.sha256}.u0`), "{}\n", { flag: "wx" });
  b.random = () => 0; // the first eligible save is a's
  const plan = b.plan("b1");
  assert.equal(plan.start.kind, "from_save");
  assert.notEqual(plan.ticket, `${save.sha256}.u0`);
  assert.equal(b.counters().claim_conflicts, 1);
  assert.equal(a.plan("a1").start.kind, "new_run");
});

test("a refused load or an unknown close seals the attempt and pauses the family for every driver; env_crash does not", () => {
  const dir = pool();
  const opts = { endgame_share: 1, uses_per_save: 4 };
  const a = new SaveTickets(config(dir, opts), DRIVER), b = new SaveTickets(config(dir, opts), { ...DRIVER, id: "drv-b" });
  pooled(a, "x", { family: "fam-x" });
  a.random = () => 0; // always the first eligible save
  const t1 = a.plan("t1");
  a.onEvent({ event: "open_refused", slot: 0, game_ref: "t1", reason: "reset_failed:Error:managed_map_continue_file_changed" });
  assert.equal(a.counters().load_failed, 1);
  // b has no own save yet, so it learns the candidate first; the paused family is never offered to it either.
  pooled(b, "y", { family: "fam-y" });
  b.random = () => 0;
  const tb = b.plan("tb");
  assert.ok(tb.ticket.startsWith(ledger(dir).find(line => line.kind === "save" && line.family === "fam-y").id));
  b.onEvent(closed("tb", "env_crash"));
  assert.equal(b.plan("tb2").start.kind, "from_save");
  b.onEvent(closed("tb2", "unknown"));
  assert.equal(b.plan("tb3").start.kind, "new_run");
  assert.deepEqual(ledger(dir).filter(line => line.kind === "pause").map(line => [line.family, line.why]), [["fam-x", "load_failed"], ["fam-y", "unknown"]]);
  assert.ok(t1.ticket);
  assert.equal(a.plan("t2").start.kind, "new_run");
  assert.deepEqual(b.counters().ticket_closes, { env_crash: 1, unknown: 1 });
});

test("a bad ledger line is counted and reported once; the lines after it still count and planning goes on", () => {
  const dir = pool();
  const a = new SaveTickets(config(dir, { endgame_share: 0 }), DRIVER);
  const first = pooled(a, "src1");
  // A driver killed mid-append leaves a torn line; a non-object line is no record either.
  appendFileSync(join(dir, "ledger.jsonl"), '{"kind":"save","id":"tor\n' + "null\n");
  const second = pooled(a, "src2", { floor: 30 });
  const reports = [];
  const write = process.stderr.write;
  process.stderr.write = (text) => { reports.push(String(text)); return true; };
  let reader;
  try {
    reader = new SaveTickets(config(dir, { endgame_share: 1 }), { ...DRIVER, id: "drv-r" });
    reader.refresh(); reader.refresh();
    pooled(reader, "own", { floor: 40 });
  } finally { process.stderr.write = write; }
  assert.equal(reports.filter(text => text.startsWith("save_tickets_ledger_bad_line:")).length, 2);
  const counters = reader.counters();
  assert.equal(counters.ledger_bad_lines, 2);
  assert.equal(counters.pool_saves, 3);
  assert.ok(reader.saves.has(second.sha256)); // the line after the bad ones
  reader.random = () => 0;
  assert.equal(reader.plan("t1").start.save.sha256, first.sha256);
});

test("a driver restarted onto a full pool still saves until it learns its candidate, then claims", () => {
  const dir = pool();
  const opts = { endgame_share: 1, save_every_games: 1, max_live_saves: 1 };
  const before = new SaveTickets(config(dir, { ...opts, endgame_share: 0 }), DRIVER);
  const save = pooled(before, "src");
  assert.equal(before.plan("full").save_points, null); // the pool is full for a driver that knows its candidate
  const after = new SaveTickets(config(dir, opts), DRIVER); // the restart: nothing learned yet
  const plan = after.plan("r1");
  assert.equal(plan.start.kind, "new_run");
  assert.notEqual(plan.save_points, null);
  assert.deepEqual(after.counters().new_run_without_ticket, { no_own_save_yet: 1 });
  after.onEvent(saved("r1")); after.onAdmission("r1", true); after.onEvent(closed("r1"));
  after.random = () => 0;
  assert.equal(after.plan("r2").ticket, `${save.sha256}.u0`);
});

test("only the session's level, candidate and weighted floor bands are offered", () => {
  const dir = pool();
  const tickets = new SaveTickets(config(dir, { endgame_share: 1, floor_weights: [{ min: 17, max: 33, weight: 1 }] }), DRIVER);
  pooled(tickets, "low", { floor: 5 });
  pooled(tickets, "a0", { floor: 20, ascension: 0 });
  assert.equal(tickets.plan("t0").start.kind, "new_run");
  const save = pooled(tickets, "mid", { floor: 20 });
  assert.equal(tickets.plan("t1").start.save.sha256, save.sha256);
});
