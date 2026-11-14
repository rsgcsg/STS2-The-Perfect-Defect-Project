// Save tickets (RL5 card §6.1-6.6; NEW-METHODS-PLAN I1/I3/I6/I7): a node-local pool of Host map saves made
// by ordinary fast-env games, and the scheduler that turns them into endgame starts (from a save to the
// natural end of the run). Off unless the env driver opens it; with it off the driver is exactly today's.
//
// Pool directory (node-local disk only; R2 gets nothing, I7; never in git):
//   saves/        the Host writes each save here (open.save_points.directory)
//   ledger.jsonl  append-only, shared by every driver of the node: save / claim / open / close / pause lines
//   claims/       one file per consumed ticket, created exclusively: a ticket is consumed once, node-wide
// A ticket is one use of one save (`<save sha256>.u<n>`, n < uses_per_save). A refused load (`reset_failed`)
// or an unknown close seals that attempt, is counted, and pauses the save's family (the source game) for
// every driver; nothing is retried or reissued under a new id. Saves of a source game enter the pool only
// after that game closed admitted and with its evidence intact, so a held-out seed never seeds the pool.
// The pool is not durable (§6.6: no verified persistent copy yet), so it is never marked recoverable.
import { appendFileSync, closeSync, existsSync, mkdirSync, openSync, readSync, statSync, writeSync } from "node:fs";
import { hostname } from "node:os";
import { join } from "node:path";

export const TICKETS_SCHEMA = "spireagent/fastchain-save-tickets-1";
const SAVE_SCHEMA = "sts2.host-runtime/managed-map-continue-1";
const SHA = /^[0-9a-f]{64}$/u;
const MAX_SAVE_FLOORS = 64; // fast-env open.save_points.floors

const fail = (field) => { throw new Error(`save_tickets_config:${field}`); };
const int = (value, field, min, max) => (Number.isSafeInteger(value) && value >= min && value <= max ? value : fail(field));
const num = (value, field, min, max) => (typeof value === "number" && Number.isFinite(value) && value >= min && value <= max ? value : fail(field));

/** The ticket knobs (one JSON object; W1 builds it from the run profile). Unknown keys are refused. */
export function parseTicketsConfig(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail("config");
  const keys = ["schema", "pool", "endgame_share", "save_every_games", "saves_per_game", "save_floors", "uses_per_save",
    "max_live_saves", "floor_weights"];
  for (const key of Object.keys(value)) if (!keys.includes(key)) fail(key);
  if (value.schema !== TICKETS_SCHEMA) fail("schema");
  if (typeof value.pool !== "string" || !value.pool.startsWith("/")) fail("pool");
  const band = value.save_floors ?? { min: 1, max: 60 };
  const min = int(band.min, "save_floors.min", 0, 1000), max = int(band.max, "save_floors.max", min, 1000);
  if (max - min + 1 > 1000 || Object.keys(band).some(key => key !== "min" && key !== "max")) fail("save_floors");
  const weights = (value.floor_weights ?? [{ min: 0, max: 1000, weight: 1 }]).map((item, index) => {
    if (item === null || typeof item !== "object" || Object.keys(item).some(key => !["min", "max", "weight"].includes(key))) fail(`floor_weights.${index}`);
    const low = int(item.min, `floor_weights.${index}.min`, 0, 1000);
    return { min: low, max: int(item.max, `floor_weights.${index}.max`, low, 1000), weight: num(item.weight, `floor_weights.${index}.weight`, 0, 1e6) };
  });
  return {
    schema: TICKETS_SCHEMA, pool: value.pool,
    // Share of opens that start from a save while the pool has an eligible ticket (card §6.4: 30% of decisions to start).
    endgame_share: num(value.endgame_share ?? 0.3, "endgame_share", 0, 1),
    // One new-run game in N carries save points; each saves at most saves_per_game floors drawn from the band.
    save_every_games: int(value.save_every_games ?? 2, "save_every_games", 1, 1_000_000),
    saves_per_game: int(value.saves_per_game ?? 3, "saves_per_game", 1, MAX_SAVE_FLOORS),
    save_floors: { min, max },
    // Tickets per save (card §6.2: K0 = 8; a capped number of uses per save against memorising the deck order).
    uses_per_save: int(value.uses_per_save ?? 8, "uses_per_save", 1, 1024),
    // No new save points while the node pool holds this many live saves (disk grows with use, not with play).
    max_live_saves: int(value.max_live_saves ?? 2000, "max_live_saves", 1, 10_000_000),
    floor_weights: weights
  };
}

/** Exactly one save line of the ledger, from the env `saved` event; null when the event is not a usable save. */
function saveLine(event) {
  const save = event.save;
  if (save === null || typeof save !== "object" || save.schema !== SAVE_SCHEMA || !SHA.test(save.sha256 ?? "")
      || typeof save.path !== "string" || typeof save.parent_game_continuity_id !== "string" || !save.parent_game_continuity_id
      || typeof save.seed !== "string" || !Number.isSafeInteger(save.act) || !Number.isSafeInteger(save.floor)
      || save.candidate === null || typeof save.candidate !== "object") return null;
  return { kind: "save", id: save.sha256, family: save.parent_game_continuity_id, act: save.act, floor: save.floor,
    ascension: event.ascension, character: event.character, seed: save.seed, candidate: save.candidate,
    source_game_instance_id: event.game_instance_id, mu_snapshot_sha256: event.mu_snapshot_sha256 ?? null,
    decisions: event.decisions ?? null, hp: event.public_facts?.hp ?? null, max_hp: event.public_facts?.max_hp ?? null,
    save };
}

export class SaveTickets {
  /**
   * `driver`: who plans here (ledger attribution), the env session's `ascension` and `character` (a save loads
   * only into a session of the same scenario). `random` is injectable for tests.
   */
  constructor(config, driver, { random = Math.random, now = Date.now } = {}) {
    this.config = parseTicketsConfig(config);
    if (typeof driver?.id !== "string" || !driver.id || !Number.isSafeInteger(driver.ascension) || typeof driver.character !== "string") fail("driver");
    this.driver = { id: driver.id, ascension: driver.ascension, character: driver.character, node: hostname() };
    this.random = random; this.now = now;
    this.saveDir = join(this.config.pool, "saves");
    this.claimDir = join(this.config.pool, "claims");
    this.ledgerPath = join(this.config.pool, "ledger.jsonl");
    mkdirSync(this.saveDir, { recursive: true, mode: 0o700 });
    mkdirSync(this.claimDir, { recursive: true, mode: 0o700 });
    /** The node pool as read from the ledger: save id -> entry; used ticket ids; paused families. */
    this.saves = new Map(); this.used = new Set(); this.paused = new Set();
    this.offset = 0; this.partial = "";
    /** This driver's games: game_ref -> { ticket } (from a save) or { saves: [], admitted } (a new run). */
    this.games = new Map();
    /** The Host candidate of this driver's own saves: a save loads only into the same candidate (learned, never assumed). */
    this.candidate = null;
    this.newRuns = 0;
    this.counts = { save_points_games: 0, saves_reported: 0, saves_pooled: 0, saves_dropped: {}, saves_duplicate: 0,
      tickets_claimed: 0, claim_conflicts: 0, from_save_opened: 0, load_failed: 0, open_refused_other: 0,
      ticket_closes: {}, families_paused: 0, new_run_without_ticket: {}, ledger_bad_lines: 0 };
  }

  #append(record) {
    appendFileSync(this.ledgerPath, `${JSON.stringify({ t: this.now(), driver: this.driver.id, node: this.driver.node, ...record })}\n`, { mode: 0o600 });
  }

  /** Reads the ledger lines appended since the last read (every driver of the node appends to it). */
  refresh() {
    if (!existsSync(this.ledgerPath)) return;
    const size = statSync(this.ledgerPath).size;
    if (size <= this.offset) return;
    const fd = openSync(this.ledgerPath, "r");
    try {
      const buffer = Buffer.alloc(size - this.offset);
      readSync(fd, buffer, 0, buffer.length, this.offset);
      this.offset = size;
      const text = this.partial + buffer.toString("utf8");
      const end = text.lastIndexOf("\n");
      this.partial = text.slice(end + 1);
      for (const line of text.slice(0, end + 1).split("\n")) if (line) this.#line(line);
    } finally { closeSync(fd); }
  }

  /** One ledger line. A line that is not a JSON object (a driver killed mid-append, a full disk) is counted
   * and reported once, here, as it is read; the lines after it still count. */
  #line(line) {
    let record;
    try { record = JSON.parse(line); } catch { record = null; }
    if (record === null || typeof record !== "object" || Array.isArray(record)) {
      this.counts.ledger_bad_lines += 1;
      process.stderr.write(`save_tickets_ledger_bad_line: ${this.ledgerPath} (${line.length} bytes) ${JSON.stringify(line.slice(0, 200))}\n`);
      return;
    }
    this.#apply(record);
  }

  #apply(record) {
    if (record.kind === "save" && !this.saves.has(record.id)) this.saves.set(record.id, record);
    else if (record.kind === "claim") this.used.add(record.ticket);
    else if (record.kind === "pause") this.paused.add(record.family);
  }

  #live(entry) {
    if (this.paused.has(entry.family)) return false;
    for (let use = 0; use < this.config.uses_per_save; use += 1) if (!this.used.has(`${entry.id}.u${use}`)) return true;
    return false;
  }

  #weight(entry) {
    const band = this.config.floor_weights.find(item => entry.floor >= item.min && entry.floor <= item.max);
    return (band?.weight ?? 0) * this.priority(entry);
  }

  /** PLR hook (card §6.3): how much training on this start teaches now. Uniform until a score is built. */
  priority(_entry) { return 1; }

  #eligible() {
    const out = [];
    if (this.candidate === null) return out;
    for (const entry of this.saves.values()) {
      if (entry.ascension !== this.driver.ascension || entry.character !== this.driver.character
          || JSON.stringify(entry.candidate) !== this.candidate || !this.#live(entry)) continue;
      const weight = this.#weight(entry);
      if (weight > 0) out.push({ entry, weight });
    }
    return out;
  }

  /** Claims one ticket of a weighted draw; another driver's claim of the same ticket moves on to the next use. */
  #claim(gameRef) {
    let candidates = this.#eligible();
    while (candidates.length > 0) {
      let pick = this.random() * candidates.reduce((sum, item) => sum + item.weight, 0), index = 0;
      while (index < candidates.length - 1 && pick >= candidates[index].weight) { pick -= candidates[index].weight; index += 1; }
      const { entry } = candidates[index];
      for (let use = 0; use < this.config.uses_per_save; use += 1) {
        const ticket = `${entry.id}.u${use}`;
        if (this.used.has(ticket)) continue;
        let fd;
        try { fd = openSync(join(this.claimDir, ticket), "wx", 0o600); }
        catch (error) {
          if (error?.code !== "EEXIST") throw error;
          this.used.add(ticket); this.counts.claim_conflicts += 1; continue;
        }
        try { writeSync(fd, `${JSON.stringify({ ticket, game_ref: gameRef, driver: this.driver.id })}\n`); } finally { closeSync(fd); }
        this.used.add(ticket);
        this.#append({ kind: "claim", ticket, save: entry.id, family: entry.family, game_ref: gameRef });
        this.counts.tickets_claimed += 1;
        return { ticket, entry };
      }
      candidates = candidates.filter(item => item.entry !== entry);
    }
    return null;
  }

  #liveCount() {
    let live = 0;
    for (const entry of this.saves.values()) if (this.#live(entry)) live += 1;
    return live;
  }

  #saveFloors() {
    const { min, max } = this.config.save_floors;
    const floors = Array.from({ length: max - min + 1 }, (_, index) => min + index);
    for (let index = floors.length - 1; index > 0; index -= 1) {
      const other = Math.floor(this.random() * (index + 1));
      [floors[index], floors[other]] = [floors[other], floors[index]];
    }
    return floors.slice(0, this.config.saves_per_game).sort((a, b) => a - b);
  }

  /**
   * The start of the next game the driver opens as `gameRef`. `seed: null` = the driver's own next seed (a new
   * run); a save start carries the save's seed (its descendants keep it, so admission's seed checks apply).
   */
  plan(gameRef) {
    this.refresh();
    if (this.random() < this.config.endgame_share) {
      const claimed = this.#claim(gameRef);
      if (claimed !== null) {
        this.games.set(gameRef, { ticket: claimed.ticket, family: claimed.entry.family });
        return { seed: claimed.entry.seed, start: { kind: "from_save", save: claimed.entry.save }, save_points: null, ticket: claimed.ticket };
      }
      const why = this.candidate === null ? "no_own_save_yet" : "no_eligible_ticket";
      this.counts.new_run_without_ticket[why] = (this.counts.new_run_without_ticket[why] ?? 0) + 1;
    }
    // A driver that has not learned its Host candidate yet (a restart onto a full pool) still saves, or it
    // could never claim and the pool would never drain.
    const saving = this.newRuns++ % this.config.save_every_games === 0
      && (this.candidate === null || this.#liveCount() < this.config.max_live_saves);
    this.games.set(gameRef, { saves: [], admitted: null, closed: null });
    if (!saving) return { seed: null, start: { kind: "new_run" }, save_points: null, ticket: null };
    this.counts.save_points_games += 1;
    return { seed: null, start: { kind: "new_run" }, save_points: { directory: this.saveDir, floors: this.#saveFloors() }, ticket: null };
  }

  /** The admission verdict of a game (the driver's admission sidecar). Saves of a refused game never enter the pool. */
  onAdmission(gameRef, ok) {
    const game = this.games.get(gameRef);
    if (!game || !("admitted" in game)) return;
    game.admitted = ok === true;
    if (game.closed) this.#commit(gameRef, game);
  }

  /** A source game's saves enter the pool once it closed admitted and with its evidence intact. */
  #commit(gameRef, game) {
    this.games.delete(gameRef);
    const why = game.closed.evidence_failed ? "evidence_failed" : game.admitted !== true ? "not_admitted" : null;
    for (const line of game.saves) {
      if (why !== null) { this.#drop(why); continue; }
      this.refresh();
      if (this.saves.has(line.id)) { this.counts.saves_duplicate += 1; continue; }
      this.saves.set(line.id, line);
      this.#append({ ...line, source_game_ref: gameRef, source_close: game.closed.reason });
      this.counts.saves_pooled += 1;
    }
  }

  #pause(family, ticket, why) {
    if (this.paused.has(family)) return;
    this.paused.add(family);
    this.counts.families_paused += 1;
    this.#append({ kind: "pause", family, ticket, why });
  }

  /** Every fast-env event the driver reads goes through here; events of other kinds are ignored. */
  onEvent(event) {
    const game = this.games.get(event.game_ref);
    if (!game) return;
    if (event.event === "saved") {
      this.counts.saves_reported += 1;
      const line = saveLine(event);
      if (line === null) { this.#drop("not_a_save"); return; }
      this.candidate ??= JSON.stringify(line.candidate);
      game.saves?.push(line);
    } else if (event.event === "opened") {
      if (game.ticket) { this.counts.from_save_opened += 1; this.#append({ kind: "open", ticket: game.ticket, game_ref: event.game_ref, game_instance_id: event.game_instance_id }); }
    } else if (event.event === "open_refused") {
      this.games.delete(event.game_ref);
      if (!game.ticket) return;
      // A load the env could not start is sealed: counted, the family paused. Other refusals only seal it.
      const loadFailed = typeof event.reason === "string" && event.reason.startsWith("reset_failed:");
      if (loadFailed) this.counts.load_failed += 1; else this.counts.open_refused_other += 1;
      this.#append({ kind: "close", ticket: game.ticket, game_ref: event.game_ref, outcome: loadFailed ? "load_failed" : "open_refused", reason: event.reason });
      if (loadFailed) this.#pause(game.family, game.ticket, "load_failed");
    } else if (event.event === "closed") {
      if (game.ticket) {
        this.games.delete(event.game_ref);
        this.counts.ticket_closes[event.reason] = (this.counts.ticket_closes[event.reason] ?? 0) + 1;
        this.#append({ kind: "close", ticket: game.ticket, game_ref: event.game_ref, game_instance_id: event.game_instance_id,
          outcome: event.reason, detail: event.detail ?? null, floor: event.public_facts?.floor ?? null, terminal: event.terminal?.result ?? null });
        if (event.reason === "unknown") this.#pause(game.family, game.ticket, "unknown");
        return;
      }
      game.closed = { reason: event.reason, evidence_failed: event.evidence_failed === true };
      // An admission verdict still in flight: the saves wait for it (onAdmission commits them).
      if (game.admitted !== null || event.evidence_failed === true) this.#commit(event.game_ref, game);
    }
  }

  #drop(why) { this.counts.saves_dropped[why] = (this.counts.saves_dropped[why] ?? 0) + 1; }

  /** For the driver summary and status: what this driver did and the node pool as it last read it. */
  counters() {
    return { ...this.counts, pool_saves: this.saves.size, pool_live: this.#liveCount(), pool_paused_families: this.paused.size,
      candidate_known: this.candidate !== null };
  }
}
