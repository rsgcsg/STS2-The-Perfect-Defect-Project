// Save starts (RL5 card §6, RL5 plan §3.4, RL5-METHODS ticket B): a node-local pool of Host map saves made by
// ordinary fast-env games, and the draw that turns them into starts (from a save to the natural end of the
// run). The env driver builds this only when the recipe's `save_starts.share` is above 0; at 0 (the default)
// the driver opens exactly what it opened before.
//
// Pool directory (node-local disk: env.sh puts it in the node's /tmp, one per job, removed at job end; R2 gets
// nothing; never in git):
//   saves/        the Host writes each save here (open.save_points.directory)
//   ledger.jsonl  append-only, shared by every driver of the node: save / claim / open / close / pause / evict
//   claims/       one file per consumed ticket, created exclusively: a ticket is consumed once, node-wide; a
//                 driver deletes a save only after taking its remaining tickets the same way (#retire)
// A ticket is one use of one save (`<save sha256>.u<n>`, n < uses_per_save). A refused load (`reset_failed`)
// or an unknown close seals that attempt, is counted, and pauses the save's family (the source game) for every
// driver; nothing is retried or reissued under a new id. A source game's saves enter the pool only after it
// closed admitted (the learner's held-out seed guard) and with its evidence intact; a save start carries the
// source seed, so its game passes the same guard. Evaluation never runs through the env driver. Games started
// from a save make no saves (the fast env refuses save points on them). A save loads only into a driver of
// the same level, character, Host candidate and initial progress (sha256), and the load itself checks the
// file sha256. Saves are management-plane only: nothing from a save reaches model input.
import { createHash } from "node:crypto";
import { appendFileSync, closeSync, copyFileSync, existsSync, mkdirSync, openSync, readdirSync, readFileSync, readSync, renameSync,
  statSync, unlinkSync, writeFileSync, writeSync } from "node:fs";
import { hostname } from "node:os";
import { basename, join } from "node:path";

const SAVE_SCHEMA = "sts2.host-runtime/managed-map-continue-1";
const SHA = /^[0-9a-f]{64}$/u;
// Global floors (the fast env's public_facts.floor). The Bosses are on 17 / 33 / 48 (stpd.rl.heads, the
// readout's BOSS_FLOORS); a save at the map screen of the floor before is the save before that Boss. Floors
// of a game count to act 1 up to 17, act 2 up to 33, act 3 after.
export const PRE_BOSS_FLOORS = [16, 32, 47];
const RANDOM_FLOORS = Array.from({ length: 46 }, (_, index) => index + 1).filter(floor => !PRE_BOSS_FLOORS.includes(floor));
const actOf = (floor) => (floor <= 17 ? 1 : floor <= 33 ? 2 : 3);

const fail = (field) => { throw new Error(`save_starts_config:${field}`); };
const int = (value, field, min, max) => (Number.isSafeInteger(value) && value >= min && value <= max ? value : fail(field));
const num = (value, field, min, max) => (typeof value === "number" && Number.isFinite(value) && value >= min && value <= max ? value : fail(field));
export const SAVE_STARTS_KEYS = ["share", "random_saves", "act_weights", "died_later_weight", "uses_per_save", "pool_gb"];
// Optional keys (absent = the behaviour before them), recipe A: `act_floor` (default 0), `act_target` ("fixed"),
// `act_target_min_games` (30), `act_target_read_seconds` (60), `greedy_saves` (false), `greedy_save_share` (0),
// `greedy_import_seconds` (60). See "Greedy cell" below.
export const SAVE_STARTS_OPTIONAL = ["act_floor", "act_target", "act_target_min_games", "act_target_read_seconds", "greedy_saves",
  "greedy_save_share", "greedy_import_seconds"];
const choice = (value, field, options) => (options.includes(value) ? value : fail(field));
const bool = (value, field) => (typeof value === "boolean" ? value : fail(field));

/** The recipe knob `save_starts` (run_profile.py checks the same table; defaults live in the profile only). */
export function parseSaveStarts(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail("config");
  for (const key of Object.keys(value)) if (!SAVE_STARTS_KEYS.includes(key) && !SAVE_STARTS_OPTIONAL.includes(key)) fail(key);
  const weights = value.act_weights;
  if (!Array.isArray(weights) || weights.length !== 3) fail("act_weights");
  weights.forEach((weight, index) => num(weight, `act_weights.${index}`, 0, 1e6));
  if (!(weights[0] + weights[1] + weights[2] > 0)) fail("act_weights");
  return {
    // Share of opens (not of decisions: a save start is shorter than a new run) drawn from the pool.
    share: num(value.share, "share", 0, 1),
    // Every new run saves at the map screen before each Boss plus this many random floors (1-46).
    random_saves: int(value.random_saves, "random_saves", 0, RANDOM_FLOORS.length),
    // Target share of training data per act (1-3); see #pickAct.
    act_weights: [...weights],
    // Share of save starts drawn among "problem saves" only (the last save before the source game died, in the
    // act it died in). The owner has not accepted that rule: 0 = off.
    died_later_weight: num(value.died_later_weight, "died_later_weight", 0, 1),
    // Uses per save, against learning one deck order by heart.
    uses_per_save: int(value.uses_per_save, "uses_per_save", 1, 1024),
    // The node pool's size cap; past it the oldest saves without an open claim are deleted.
    pool_bytes: Math.round(num(value.pool_gb, "pool_gb", 0.001, 1000) * 1e9),
    // Recipe A: each act with a save to start from gets at least this share of the act draw
    // (0 = off, the draw exactly as before; at most 1/3).
    act_floor: num(value.act_floor ?? 0, "act_floor", 0, 1 / 3),
    // Recipe A B1: "fixed" = act_weights is the target; "greedy_cell" = the per-act floors the greedy cell's own
    // closed new runs walked, summed over its drivers' stats files, once they hold >= act_target_min_games games
    // (act_weights until then), read at most every act_target_read_seconds.
    act_target: choice(value.act_target ?? "fixed", "act_target", ["fixed", "greedy_cell"]),
    act_target_min_games: int(value.act_target_min_games ?? 30, "act_target_min_games", 1, 1e6),
    act_target_read_seconds: num(value.act_target_read_seconds ?? 60, "act_target_read_seconds", 1, 3600),
    // Recipe A B3: the greedy cell's new runs save (same floors as any new run) and export each save of an
    // admitted, evidence-intact game to the run directory; B4: a save start draws, inside the act #pickAct
    // chose, among greedy saves with this probability (0 = off: greedy saves are never imported or drawn).
    greedy_saves: bool(value.greedy_saves ?? false, "greedy_saves"),
    greedy_save_share: num(value.greedy_save_share ?? 0, "greedy_save_share", 0, 1),
    greedy_import_seconds: num(value.greedy_import_seconds ?? 60, "greedy_import_seconds", 1, 3600)
  };
}

/** The ledger's save line from the env `saved` event (floor = global floor of the event; the descriptor's own
 * floor counts within the act); null when the event is not a usable save. */
const unlinkQuiet = (path) => { try { unlinkSync(path); } catch (error) { if (error?.code !== "ENOENT") throw error; } };

function saveLine(event, progressSha) {
  const save = event.save;
  if (save === null || typeof save !== "object" || save.schema !== SAVE_SCHEMA || !SHA.test(save.sha256 ?? "")
      || typeof save.path !== "string" || typeof save.parent_game_continuity_id !== "string" || !save.parent_game_continuity_id
      || typeof save.seed !== "string" || !Number.isSafeInteger(save.act) || !Number.isSafeInteger(event.floor)
      || !Number.isSafeInteger(save.bytes) || save.candidate === null || typeof save.candidate !== "object") return null;
  return { kind: "save", id: save.sha256, family: save.parent_game_continuity_id, act: save.act, floor: event.floor,
    bytes: save.bytes, path: save.path, ascension: event.ascension, character: event.character, seed: save.seed,
    candidate: save.candidate, progress_sha256: progressSha, source_game_instance_id: event.game_instance_id,
    mu_snapshot_sha256: event.mu_snapshot_sha256 ?? null, decisions: event.decisions ?? null, save };
}

/** Draws one index with probability proportional to `weights` (all >= 0, some > 0). */
function draw(weights, random) {
  let pick = random() * weights.reduce((sum, weight) => sum + weight, 0), index = 0;
  while (index < weights.length - 1 && pick >= weights[index]) { pick -= weights[index]; index += 1; }
  return index;
}

// Greedy cell (recipe A B1/B3/B4). The greedy arm's drivers (role "greedy": new runs only, never a save start)
// write, per driver, `<greedy_dir>/stats/<driver>.json` = { floors: [act 1, 2, 3], games } over their closed
// terminal new runs (temp file + rename), and, with greedy_saves, copy each pooled-worthy save to
// `<greedy_dir>/saves/<sha256>.save` with its ledger line as `<sha256>.json` (each written to a temp name and
// renamed; the .json last, so a .json means a complete export). The run directory is shared by every node; the
// node pools are not. A pool driver imports, at most every greedy_import_seconds, the exports this node owns
// (sha256 prefix mod the run's env node count = this node's index: one node per save, so a save's uses stay
// uses_per_save) into its own pool, claimed node-wide by an exclusive `imports/<sha256>` file (one import, ever;
// a failed import stays sealed, counted and logged). Every read or import failure is counted and logged.
const SAVE_ROLES = ["pool", "greedy"];
const sumOf = (values) => values[0] + values[1] + values[2];

export class SaveTickets {
  /**
   * `driver`: who plans here (ledger attribution), the env session's `ascension` and `character`, the node
   * `pool` directory and `progress_sha256` (the driver's initial progress). `log` receives the driver.jsonl
   * records (pooled saves); `random` and `now` are injectable for tests.
   */
  constructor(config, driver, { random = Math.random, now = Date.now, log = () => {} } = {}) {
    this.config = parseSaveStarts(config);
    if (typeof driver?.id !== "string" || !driver.id || !Number.isSafeInteger(driver.ascension) || typeof driver.character !== "string"
        || typeof driver.pool !== "string" || !driver.pool.startsWith("/") || !SHA.test(driver.progress_sha256 ?? "")) fail("driver");
    this.role = choice(driver.role ?? "pool", "driver.role", SAVE_ROLES);
    const c = this.config;
    this.targetOn = this.role === "pool" && c.act_target === "greedy_cell";
    this.importOn = this.role === "pool" && c.greedy_save_share > 0;
    if (this.role === "greedy" || this.targetOn || this.importOn) {
      if (typeof driver.greedy_dir !== "string" || !driver.greedy_dir.startsWith("/")) fail("greedy_dir");
      this.statsDir = join(driver.greedy_dir, "stats");
      this.exportDir = join(driver.greedy_dir, "saves");
      mkdirSync(this.statsDir, { recursive: true, mode: 0o700 });
      mkdirSync(this.exportDir, { recursive: true, mode: 0o700 });
    }
    if (this.importOn) {
      // The node's share of the exports: never guessed (env.sh passes NODEIDX and FC_ENV_NODES).
      if (!Number.isSafeInteger(driver.node_count) || driver.node_count < 1 || !Number.isSafeInteger(driver.node_index)
          || driver.node_index < 0 || driver.node_index >= driver.node_count) fail("node_index/node_count");
      this.nodeShare = { index: driver.node_index, count: driver.node_count };
      this.importDir = join(driver.pool, "imports");
      mkdirSync(this.importDir, { recursive: true, mode: 0o700 });
      this.seenExports = new Set();
      this.importedAt = null;
    }
    // B1: the act target now; "start" = act_weights (too few greedy games, or none read yet).
    this.target = { mode: c.act_target, state: "start", weights: [...c.act_weights], greedy_games: 0, read_at: null,
      switched_at: null, logged: false };
    this.greedyStats = { floors: [0, 0, 0], games: 0 };
    this.driver = { id: driver.id, ascension: driver.ascension, character: driver.character, node: hostname() };
    this.progressSha = driver.progress_sha256;
    this.random = random; this.now = now; this.log = log;
    this.saveDir = join(driver.pool, "saves");
    this.claimDir = join(driver.pool, "claims");
    this.ledgerPath = join(driver.pool, "ledger.jsonl");
    mkdirSync(this.saveDir, { recursive: true, mode: 0o700 });
    mkdirSync(this.claimDir, { recursive: true, mode: 0o700 });
    /** The node pool as read from the ledger, oldest first: save id -> entry (+ used = claimed uses, open =
     * claims not closed yet); consumed ticket ids; paused families; the pooled bytes. */
    this.saves = new Map(); this.used = new Set(); this.paused = new Set(); this.ticketSave = new Map();
    /** Tickets whose claim file another driver made before its claim line reached this driver's view. */
    this.taken = new Set();
    this.bytes = 0; this.offset = 0; this.partial = "";
    /** This driver's games: game_ref -> { ticket, entry } (from a save) or { saves, admitted, closed } (a new run). */
    this.games = new Map();
    /** The Host candidate of this driver's own saves: a save loads only into the same candidate (learned, never assumed). */
    this.candidate = null;
    /** Floors this driver's closed games played, per act (the training-data proxy of #pickAct). */
    this.covered = [0, 0, 0];
    /** claim_conflicts also counts tickets of a save another driver already retired (#retire's claim files);
     * evictions_kept counts trims that kept a save because another driver's claim file was there. */
    this.counts = { save_points_games: 0, saves_reported: 0, saves_pooled: 0, saves_dropped: {}, saves_duplicate: 0,
      tickets_claimed: 0, claim_conflicts: 0, from_save_opened: 0, load_failed: 0, open_refused_other: 0,
      ticket_closes: {}, families_paused: 0, new_run_without_ticket: {}, died_later_draws: 0, ledger_bad_lines: 0,
      evicted: {}, evictions_kept: 0 };
    // Recipe A counters, only when their switch is on (the driver.jsonl lines are as before otherwise).
    if (this.targetOn) Object.assign(this.counts, { act_target_reads: 0, act_target_read_failed: {} });
    if (this.importOn) Object.assign(this.counts, { greedy_imported: 0, greedy_import_failed: {}, greedy_draws: 0,
      greedy_fallback: 0, ordinary_fallback: 0 });
    if (this.role === "greedy") Object.assign(this.counts, { greedy_games: 0, greedy_stats_write_failed: 0,
      greedy_exported: 0, greedy_export_bytes: 0, greedy_export_failed: {} });
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
    if (record.kind === "save") {
      if (this.saves.has(record.id) || typeof record.save !== "object") return;
      // Only what the draw needs is kept: a full pool holds about 10^5 saves per node.
      this.saves.set(record.id, { id: record.id, family: record.family, act: record.act, floor: record.floor, bytes: record.bytes,
        path: record.path, scenario: `${record.ascension}|${record.character}|${JSON.stringify(record.candidate)}|${record.progress_sha256}`,
        seed: record.seed, problem: record.problem === true, source_floor: record.source_end?.floor ?? null,
        source_terminal: record.source_end?.terminal ?? null, used: 0, open: 0, save: record.save,
        ...(record.source === "greedy" ? { source: "greedy" } : {}) });
      this.bytes += record.bytes;
    } else if (record.kind === "claim" && !this.used.has(record.ticket)) {
      this.used.add(record.ticket);
      this.ticketSave.set(record.ticket, record.save);
      const entry = this.saves.get(record.save);
      if (entry) { entry.used += 1; entry.open += 1; }
    } else if (record.kind === "close" && this.ticketSave.has(record.ticket)) {
      const entry = this.saves.get(this.ticketSave.get(record.ticket));
      this.ticketSave.delete(record.ticket);
      if (entry) entry.open -= 1;
    } else if (record.kind === "pause") this.paused.add(record.family);
    else if (record.kind === "evict") {
      const entry = this.saves.get(record.id);
      if (entry) { this.saves.delete(record.id); this.bytes -= entry.bytes; }
    }
  }

  #scenario() {
    return `${this.driver.ascension}|${this.driver.character}|${this.candidate}|${this.progressSha}`;
  }

  /** Saves this driver may claim `count` uses of: same scenario, family not paused, enough uses left. */
  #eligible(count) {
    if (this.candidate === null) return [];
    const scenario = this.#scenario(), out = [];
    const greedy = this.config.greedy_save_share > 0;
    for (const entry of this.saves.values())
      if (entry.scenario === scenario && !this.paused.has(entry.family) && this.config.uses_per_save - entry.used >= count
          && (greedy || entry.source !== "greedy")) out.push(entry);
    return out;
  }

  /**
   * The act a save start begins in. Target: training data per act in proportion to `act_weights`. Proxy for
   * training data: floors played (equal rows per floor), counted from this driver's closed games, new runs
   * from floor 1 and save starts from the floor after the save, up to the floor the game closed on. The act
   * is drawn in proportion to how far each act (with a save to start from) is below its target share; when
   * none is below, in proportion to the targets. Rows per floor differ (a fight floor has more decisions than
   * a shop), so the shares are rough: the readout reports the result per act.
   * `act_floor` f > 0 (recipe A): over the n acts with a save, p_a -> f + (1 - n f) p_a, so every such act
   * is drawn with probability at least f; the fill-the-gap draw itself is unchanged.
   */
  #pickAct(acts) {
    const weights = this.target.weights, total = weights[0] + weights[1] + weights[2];
    const played = this.covered[0] + this.covered[1] + this.covered[2];
    const target = acts.map(act => (weights[act - 1] ?? 0) / total);
    const below = acts.map((act, index) => Math.max(0, target[index] - (played ? this.covered[act - 1] / played : 0)));
    const use = below.some(value => value > 0) ? below : target;
    if (!use.some(value => value > 0)) return null;
    const floor = this.config.act_floor;
    if (floor === 0) return acts[draw(use, this.random)];
    const sum = use.reduce((total, value) => total + value, 0);
    return acts[draw(use.map(value => floor + (1 - acts.length * floor) * value / sum), this.random)];
  }

  /** The save of a draw: act (#pickAct), then a floor uniformly among that act's floors, then a save of that floor. */
  #pick(entries) {
    const byAct = new Map();
    for (const entry of entries) {
      if (!byAct.has(entry.act)) byAct.set(entry.act, new Map());
      const floors = byAct.get(entry.act);
      if (!floors.has(entry.floor)) floors.set(entry.floor, []);
      floors.get(entry.floor).push(entry);
    }
    const act = this.#pickAct([...byAct.keys()].sort((a, b) => a - b));
    if (act === null) return null;
    let floors = [...byAct.get(act).values()];
    if (this.config.greedy_save_share > 0) {
      // B4: which kind of save inside the chosen act; none of that kind there = the other kind, counted.
      const greedy = this.random() < this.config.greedy_save_share;
      const kept = floors.map(saves => saves.filter(entry => (entry.source === "greedy") === greedy)).filter(saves => saves.length > 0);
      if (kept.length > 0) { floors = kept; if (greedy) this.counts.greedy_draws += 1; }
      else {
        this.counts[greedy ? "greedy_fallback" : "ordinary_fallback"] += 1;
        this.log({ event: "greedy_save_fallback", act, wanted: greedy ? "greedy" : "ordinary" });
      }
    }
    const saves = floors[Math.floor(this.random() * floors.length)];
    return saves[Math.floor(this.random() * saves.length)];
  }

  /** Claims `count` uses of one drawn save (exclusive claim files; another driver's claim moves on to the next use). */
  #claim(group, count) {
    let entries = this.#eligible(count);
    if (this.config.died_later_weight > 0 && this.random() < this.config.died_later_weight) {
      const problems = entries.filter(entry => entry.problem);
      if (problems.length > 0) { entries = problems; this.counts.died_later_draws += 1; }
      else this.#count(this.counts.new_run_without_ticket, "no_problem_save_drawn_any");
    }
    while (entries.length > 0) {
      const entry = this.#pick(entries);
      if (entry === null) return null;
      const tickets = [];
      for (let use = 0; use < this.config.uses_per_save && tickets.length < count; use += 1) {
        const ticket = `${entry.id}.u${use}`;
        if (this.used.has(ticket) || this.taken.has(ticket)) continue;
        let fd;
        try { fd = openSync(join(this.claimDir, ticket), "wx", 0o600); }
        catch (error) {
          if (error?.code !== "EEXIST") throw error;
          this.taken.add(ticket); this.counts.claim_conflicts += 1; continue;
        }
        try { writeSync(fd, `${JSON.stringify({ ticket, group, driver: this.driver.id })}\n`); } finally { closeSync(fd); }
        const record = { kind: "claim", ticket, save: entry.id, family: entry.family, group };
        this.#append(record); this.#apply(record);
        this.counts.tickets_claimed += 1;
        tickets.push({ ticket, use });
      }
      if (tickets.length > 0) return { entry, tickets };
      entries = entries.filter(item => item !== entry);
    }
    return null;
  }

  #saveFloors() {
    const floors = [...RANDOM_FLOORS];
    for (let index = floors.length - 1; index > 0; index -= 1) {
      const other = Math.floor(this.random() * (index + 1));
      [floors[index], floors[other]] = [floors[other], floors[index]];
    }
    return [...floors.slice(0, this.config.random_saves), ...PRE_BOSS_FLOORS].sort((a, b) => a - b);
  }

  /**
   * The start of the next group of `count` games (K replay: one start for K games). A save start claims one
   * use per game, all of one save (fewer than `count` only when other drivers took the rest meanwhile:
   * `tickets.length` games then). A new run (`seed: null` = the driver's own next seed) saves at the floors
   * of `save_points`.
   */
  plan(group, count = 1) {
    if (this.role === "greedy") {
      this.counts.save_points_games += count;
      return { seed: null, start: { kind: "new_run" }, tickets: null, entry: null,
        save_points: this.config.greedy_saves ? { directory: this.saveDir, floors: this.#saveFloors() } : null };
    }
    this.refresh();
    if (this.importOn) this.#import();
    if (this.targetOn) this.#readTarget();
    if (this.random() < this.config.share) {
      const claimed = this.#claim(group, count);
      if (claimed !== null) return { seed: claimed.entry.seed, start: { kind: "from_save", save: claimed.entry.save }, save_points: null,
        tickets: claimed.tickets, entry: claimed.entry };
      this.#count(this.counts.new_run_without_ticket, this.candidate === null ? "no_own_save_yet" : "no_eligible_ticket");
    }
    this.counts.save_points_games += count;
    return { seed: null, start: { kind: "new_run" }, save_points: { directory: this.saveDir, floors: this.#saveFloors() }, tickets: null, entry: null };
  }

  /** Member `member` of a planned group opens as `gameRef`; returns its driver.jsonl facts (save starts only). */
  opening(gameRef, plan, member) {
    if (plan.start.kind !== "from_save") {
      this.games.set(gameRef, { saves: [], admitted: null, closed: null, from: 0 });
      return {};
    }
    const { entry } = plan, { ticket, use } = plan.tickets[member];
    this.games.set(gameRef, { ticket, entry, from: entry.floor });
    return { save: entry.id.slice(0, 16), save_act: entry.act, save_floor: entry.floor, use,
      source_floor: entry.source_floor, source_terminal: entry.source_terminal, ...(entry.source ? { save_source: entry.source } : {}) };
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
    if (why !== null) {
      // Their files go too: a dropped save is outside the pool's size cap.
      for (const line of game.saves) unlinkQuiet(line.path);
      if (game.saves.length > 0) this.#count(this.counts.saves_dropped, why, game.saves.length);
      return;
    }
    const end = game.closed.end;
    // The problem save: the last save before the game died, in the act it died in.
    const died = end.terminal === "defeat" && Number.isSafeInteger(end.floor) ? game.saves.filter(line => line.act === end.act && line.floor <= end.floor) : [];
    const problem = died.length > 0 ? died[died.length - 1] : null;
    if (this.role === "greedy") {
      for (const line of game.saves) this.#export({ ...line, problem: line === problem, source_game_ref: gameRef, source_end: end, source: "greedy" });
      return;
    }
    this.refresh();
    for (const line of game.saves) {
      if (this.saves.has(line.id)) { this.counts.saves_duplicate += 1; continue; }
      const record = { ...line, problem: line === problem, source_game_ref: gameRef, source_end: end };
      this.#append(record); this.#apply(record);
      this.counts.saves_pooled += 1;
      this.log({ event: "save_pooled", save: line.id.slice(0, 16), act: line.act, floor: line.floor, bytes: line.bytes,
        problem: line === problem, source_floor: end.floor, source_terminal: end.terminal });
    }
    this.#trim();
  }

  /** Deletes saves no use is left of, then the oldest past the size cap; never one with an open claim. */
  #trim() {
    this.refresh();
    for (const entry of [...this.saves.values()]) {
      const used = entry.used >= this.config.uses_per_save || this.paused.has(entry.family);
      if (entry.open > 0 || (!used && this.bytes <= this.config.pool_bytes) || !this.#retire(entry)) continue;
      const record = { kind: "evict", id: entry.id, why: used ? "used" : "pool_full" };
      this.#append(record); this.#apply(record);
      this.#count(this.counts.evicted, record.why);
      unlinkQuiet(entry.path);
    }
  }

  /**
   * Takes every use of `entry` this driver has not seen claimed, with the claim's own exclusive file create,
   * so a delete and a claim never both win: another driver's claim whose ledger line is not in this view yet
   * (its file exists) keeps the save, and the claim files taken here are given back. False = keep it.
   */
  #retire(entry) {
    const taken = [];
    for (let use = 0; use < this.config.uses_per_save; use += 1) {
      const ticket = `${entry.id}.u${use}`;
      if (this.used.has(ticket)) continue;
      const path = join(this.claimDir, ticket);
      try { closeSync(openSync(path, "wx", 0o600)); taken.push(path); }
      catch (error) {
        if (error?.code !== "EEXIST") throw error;
        for (const own of taken) unlinkQuiet(own);
        this.counts.evictions_kept += 1;
        return false;
      }
    }
    return true;
  }

  #pause(family, ticket, why) {
    if (this.paused.has(family)) return;
    this.paused.add(family);
    this.counts.families_paused += 1;
    this.#append({ kind: "pause", family, ticket, why });
  }

  #close(record) {
    this.#append(record); this.#apply(record);
    this.#trim();
  }

  /** Every fast-env event the driver reads goes through here; events of other kinds are ignored. */
  onEvent(event) {
    const game = this.games.get(event.game_ref);
    if (!game) return;
    if (event.event === "saved") {
      this.counts.saves_reported += 1;
      const line = saveLine(event, this.progressSha);
      if (line === null) { this.#count(this.counts.saves_dropped, "not_a_save"); return; }
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
      if (loadFailed) this.#pause(game.entry.family, game.ticket, "load_failed");
      this.#close({ kind: "close", ticket: game.ticket, game_ref: event.game_ref, outcome: loadFailed ? "load_failed" : "open_refused", reason: event.reason });
    } else if (event.event === "closed") {
      const facts = event.public_facts;
      if (Number.isSafeInteger(facts?.floor)) for (let floor = game.from + 1; floor <= facts.floor; floor += 1) this.covered[actOf(floor) - 1] += 1;
      if (this.role === "greedy" && event.reason === "terminal" && Number.isSafeInteger(facts?.floor)) this.#writeStats(facts.floor);
      if (game.ticket) {
        this.games.delete(event.game_ref);
        this.#count(this.counts.ticket_closes, event.reason);
        if (event.reason === "unknown") this.#pause(game.entry.family, game.ticket, "unknown");
        this.#close({ kind: "close", ticket: game.ticket, game_ref: event.game_ref, game_instance_id: event.game_instance_id,
          outcome: event.reason, detail: event.detail ?? null, floor: facts?.floor ?? null, terminal: event.terminal?.result ?? null });
        return;
      }
      game.closed = { evidence_failed: event.evidence_failed === true,
        end: { reason: event.reason, act: facts?.act ?? null, floor: facts?.floor ?? null, terminal: event.terminal?.result ?? null } };
      // An admission verdict still in flight: the saves wait for it (onAdmission commits them).
      if (game.admitted !== null || event.evidence_failed === true) this.#commit(event.game_ref, game);
    }
  }

  #count(table, key, n = 1) { table[key] = (table[key] ?? 0) + n; }

  /** Greedy role, B1: this driver's closed terminal new runs, floors per act, as one file (temp + rename). */
  #writeStats(endFloor) {
    for (let floor = 1; floor <= endFloor; floor += 1) this.greedyStats.floors[actOf(floor) - 1] += 1;
    this.greedyStats.games += 1;
    this.counts.greedy_games += 1;
    const path = join(this.statsDir, `${this.driver.id}.json`), temp = `${path}.tmp`;
    try {
      writeFileSync(temp, JSON.stringify({ driver: this.driver.id, floors: this.greedyStats.floors, games: this.greedyStats.games, t: this.now() }), { mode: 0o600 });
      renameSync(temp, path);
    } catch (error) {
      this.counts.greedy_stats_write_failed += 1;
      this.log({ event: "greedy_stats_write_failed", error: String(error?.code ?? error?.message ?? error) });
    }
  }

  /** Greedy role, B3: one save of an admitted, evidence-intact game to the run's export directory; the local
   * file goes either way (the greedy driver keeps no pool). */
  #export(record) {
    const base = join(this.exportDir, record.id), temp = `.${this.driver.id}.tmp`;
    try {
      copyFileSync(record.path, `${base}.save${temp}`);
      renameSync(`${base}.save${temp}`, `${base}.save`);
      writeFileSync(`${base}.json${temp}`, JSON.stringify({ kind: "save", ...record, path: null }), { mode: 0o600 });
      renameSync(`${base}.json${temp}`, `${base}.json`);
      this.counts.greedy_exported += 1; this.counts.greedy_export_bytes += record.bytes;
      this.log({ event: "save_exported", save: record.id.slice(0, 16), act: record.act, floor: record.floor, bytes: record.bytes,
        problem: record.problem, source_floor: record.source_end.floor, source_terminal: record.source_end.terminal });
    } catch (error) {
      const why = String(error?.code ?? "error");
      this.#count(this.counts.greedy_export_failed, why);
      this.log({ event: "greedy_export_failed", save: record.id.slice(0, 16), error: why });
    } finally { unlinkQuiet(record.path); }
  }

  /** B4: imports this node's new exports into the pool (exclusive marker first: once per save, node-wide). */
  #import() {
    const now = this.now();
    if (this.importedAt !== null && now - this.importedAt < this.config.greedy_import_seconds * 1000) return;
    this.importedAt = now;
    let names;
    try { names = readdirSync(this.exportDir); }
    catch (error) { return this.#importFailed("export_dir", null, error); }
    for (const name of names) {
      if (!name.endsWith(".json") || this.seenExports.has(name)) continue;
      this.seenExports.add(name);
      const id = name.slice(0, -5);
      if (!SHA.test(id) || Number.parseInt(id.slice(0, 8), 16) % this.nodeShare.count !== this.nodeShare.index || this.saves.has(id)) continue;
      try { closeSync(openSync(join(this.importDir, id), "wx", 0o600)); }
      catch (error) { if (error?.code === "EEXIST") continue; this.#importFailed("marker", id, error); continue; }
      try {
        const line = JSON.parse(readFileSync(join(this.exportDir, name), "utf8"));
        if (line?.kind !== "save" || line.id !== id || line.source !== "greedy" || typeof line.save?.path !== "string") throw new Error("bad_export_line");
        const bytes = readFileSync(join(this.exportDir, `${id}.save`));
        if (bytes.length !== line.bytes || createHash("sha256").update(bytes).digest("hex") !== id) throw new Error("sha256_mismatch");
        const path = join(this.saveDir, basename(line.save.path)), temp = `${path}.import-tmp`;
        writeFileSync(temp, bytes, { mode: 0o600 });
        renameSync(temp, path);
        const record = { ...line, path, save: { ...line.save, path }, imported_from: "greedy_export" };
        this.#append(record); this.#apply(record);
        this.counts.greedy_imported += 1;
        this.log({ event: "save_imported", save: id.slice(0, 16), act: line.act, floor: line.floor, bytes: line.bytes, source: "greedy" });
      } catch (error) { this.#importFailed("import", id, error); }
    }
  }

  #importFailed(stage, id, error) {
    const why = `${stage}:${error?.code ?? error?.message ?? "error"}`;
    this.#count(this.counts.greedy_import_failed, why);
    this.log({ event: "greedy_import_failed", save: id?.slice(0, 16) ?? null, why });
  }

  /** B1: the greedy cell's measured floors per act, read at most every act_target_read_seconds. A failed read
   * keeps the target as it was (act_weights before the switch, the last measured after), counted and logged. */
  #readTarget() {
    const now = this.now(), t = this.target;
    if (t.read_at !== null && now - t.read_at < this.config.act_target_read_seconds * 1000) return;
    t.read_at = now;
    this.counts.act_target_reads += 1;
    const floors = [0, 0, 0];
    let games = 0, names;
    try { names = readdirSync(this.statsDir).filter(name => name.endsWith(".json")); }
    catch (error) { return this.#targetFailed("stats_dir", null, error); }
    for (const name of names) {
      let stats;
      try { stats = JSON.parse(readFileSync(join(this.statsDir, name), "utf8")); }
      catch (error) { return this.#targetFailed(error?.code === "ENOENT" ? "missing" : "parse", name, error); }
      if (!Array.isArray(stats?.floors) || stats.floors.length !== 3 || !stats.floors.every(n => Number.isSafeInteger(n) && n >= 0)
          || !Number.isSafeInteger(stats.games) || stats.games < 0) return this.#targetFailed("parse", name, null);
      stats.floors.forEach((n, index) => { floors[index] += n; });
      games += stats.games;
    }
    t.greedy_games = games;
    if (games < this.config.act_target_min_games || !(sumOf(floors) > 0)) {
      if (!t.logged) { t.logged = true; this.log({ event: "act_target", state: "start", greedy_games: games, target: t.weights }); }
      return;
    }
    if (t.state === "start") {
      t.switched_at = now;
      this.log({ event: "act_target_switched", greedy_games: games, floors, start: [...this.config.act_weights] });
    }
    t.state = "measured"; t.weights = floors;
  }

  #targetFailed(why, name, error) {
    this.#count(this.counts.act_target_read_failed, why);
    this.log({ event: "act_target_read_failed", why, file: name, error: error ? String(error.code ?? error.message ?? error) : null,
      state: this.target.state, kept: this.target.weights });
  }

  /** For the driver summary: what this driver did and the node pool as it last read it. */
  counters() {
    return { ...this.counts, pool_saves: this.saves.size, pool_bytes: this.bytes, pool_paused_families: this.paused.size,
      covered_floors: [...this.covered], candidate_known: this.candidate !== null,
      ...(this.targetOn ? { act_target: { state: this.target.state, target: [...this.target.weights], greedy_games: this.target.greedy_games,
        read_at: this.target.read_at, switched_at: this.target.switched_at } } : {}) };
  }
}
