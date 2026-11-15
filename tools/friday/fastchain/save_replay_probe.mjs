#!/usr/bin/env node
// Deep save probe (RL5-METHODS ticket B, phase 1). Question: does a fast-env map save taken in acts
// 2 and 3 load in a fresh process to the same game? save_probe.mjs answers it for act 1 with a random
// player, which never gets past act 1; this probe replays recorded argmax games instead (scripts made
// by export_replay_scripts.py from a fixed evaluation round: the P1 catalog and the chosen index of
// every step), through the production kernel (SlotKernel, Host map save and load, P1 reduce/lowering).
//   parent  new run of the script's seed, replaying the script, a save at every map floor the Host
//           takes (save_points); every row's P1 catalog must equal the script's or the game stops there.
//   control a second new run of the seed without saves, after the parent: replay determinism.
//   child   per chosen save (the map before each act boss, the map after each treasure room, a few
//           random maps), after the parent: fresh native process, Host map load, then saves at the
//           load floor (a load/save round trip) and every later map floor.
// Controls and children play the parent's choice at each row, found by content (P1 entity ids are per
// session and the P1 catalog is sorted by them, so a loaded game lists the same choices in another
// order). Compared per follower: every row's public observation and sorted catalog against the
// parent's same row (entities named in order of first appearance in the observation; a pair equal
// only with every entity id blanked is reported apart), the end of the game, and each later save
// against the parent's save at the same floor (every differing path; RNG and unlock paths counted apart).
// Needs the exact game, so it runs on a cluster CPU node with a built env package (the kernel is the
// package's own); writes only under --out (private; saves never leave it). Exit 0 when every child
// that loaded is the same game, 3 when any diverged, 1 when nothing was compared, 2 usage.
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync, appendFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { deflateRawSync, inflateRawSync } from "node:zlib";
import { diffPaths, unlockPaths } from "./save_probe.mjs";

export const REPLAY_PROBE_SCHEMA = "spireagent/fastchain-save-replay-probe-1";
const LINK_SCHEMA = "sts2.policy-runtime/fast-env-link-1";

export const canonical = (value) => Array.isArray(value) ? `[${value.map(canonical).join(",")}]`
  : value !== null && typeof value === "object"
    ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`
    : JSON.stringify(value);
const sha = (text) => createHash("sha256").update(text, "utf8").digest("hex");
const ENTITY = /^e\d+$/u;

/** P1 entity ids are per session: renamed in order of first appearance (keys sorted), or all blanked. */
export function renameEntities(value, names = new Map()) {
  if (Array.isArray(value)) return value.map(item => renameEntities(item, names));
  if (value !== null && typeof value === "object")
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, renameEntities(value[key], names)]));
  if (typeof value === "string" && ENTITY.test(value)) {
    if (names === null) return "E";
    if (!names.has(value)) names.set(value, `E${names.size}`);
    return names.get(value);
  }
  return value;
}
export const catalogKey = (catalog) => sha(canonical(renameEntities(catalog.map(({ choice_id: _id, ...rest }) => rest))));

/** One row as compared across processes. P1 entity ids are allocated per session (a loaded game allocates
 * them in another order than the game that saved), and the P1 catalog is sorted by them (the reducer's sort
 * key holds the subject id). So entities are named by first appearance in the observation without its
 * catalog (native hand, enemy, potion and pile order), and the catalog is compared as the sorted list of its
 * renamed items. `items` (renamed canonical item text, catalog order) selects a choice by content. */
export function sessionRow(observation, catalog) {
  const { catalog: _shown, ...body } = observation;
  const strip = (items) => items.map(({ choice_id: _id, ...rest }) => rest);
  const name = (names) => {
    const normal = renameEntities(body, names), items = strip(catalog).map(item => canonical(renameEntities(item, names)));
    return { normal, items, text: canonical({ ...normal, catalog: [...items].sort() }) };
  };
  const named = name(new Map()), blind = name(null);
  const sections = Object.fromEntries(Object.keys(named.normal).map(key => [key, sha(canonical(named.normal[key])).slice(0, 16)]));
  sections.catalog = sha(canonical([...named.items].sort())).slice(0, 16);
  return { full: sha(named.text).slice(0, 16), blind: sha(blind.text).slice(0, 16), sections, items: named.items, text: named.text };
}

const valueAt = (value, path) => path.split(".").slice(1).reduce((node, key) => (node ?? {})[key], value);
const short = (value) => { const text = JSON.stringify(value) ?? "undefined"; return text.length > 240 ? `${text.slice(0, 240)}…` : text; };
const viewOf = (row) => JSON.parse(row.text ?? inflateRawSync(row.view).toString("utf8"));
/** Where two rows differ: every differing JSON path (first 40) and the two values at the first 6. */
export function rowDiff(left, right) {
  if (!(left?.view || left?.text) || !(right?.view || right?.text)) return null;
  const a = viewOf(left), b = viewOf(right), paths = diffPaths(a, b);
  return { paths: paths.slice(0, 40), values: paths.slice(0, 6).map(path => ({ path, parent: short(valueAt(a, path)), child: short(valueAt(b, path)) })) };
}

/** A parent row as kept (the view deflated; read back only for the first differing row of a follower). */
export const keptRow = (row, chosen) => ({ full: row.full, blind: row.blind, sections: row.sections, chosen,
  view: deflateRawSync(Buffer.from(row.text, "utf8")) });

/** One follower row against the parent's same row: null when equal. */
export function rowDivergence(parentRow, row, index, followerIndex) {
  if (parentRow.full === row.full) return null;
  return { row: index, child_row: followerIndex, entity_ids_only: parentRow.blind === row.blind,
    sections: Object.keys({ ...parentRow.sections, ...row.sections }).filter(key => parentRow.sections[key] !== row.sections[key]),
    diff: rowDiff(parentRow, row) };
}

/** First differing row of two kept row lists from `offset` on (parent repeats); null when equal to the shorter end. */
export function firstRowDivergence(parentRows, offset, childRows) {
  for (let k = 0; k < childRows.length && offset + k < parentRows.length; k += 1) {
    const divergence = rowDivergence(parentRows[offset + k], childRows[k], offset + k, k);
    if (divergence) return divergence;
  }
  return null;
}

const FLAGS = { pkg: "path", candidate: "path", game: "path", "initial-progress": "path", scripts: "path", out: "path",
  games: [1, 1000], slots: [1, 16], "random-share": "share", "first-game": [0, 1000], "max-floor": [1, 100], "min-child-act": [1, 3] };
function parseArgs(argv) {
  const seen = {};
  for (let i = 0; i < argv.length; i += 2) {
    const name = argv[i]?.startsWith("--") ? argv[i].slice(2) : null, value = argv[i + 1];
    if (name === null || !Object.hasOwn(FLAGS, name) || Object.hasOwn(seen, name) || !value) throw new Error(`usage:${argv[i]}`);
    seen[name] = value;
  }
  for (const name of ["pkg", "candidate", "game", "initial-progress", "scripts", "out"]) if (!seen[name]) throw new Error(`usage:--${name}`);
  const int = (name, fallback) => {
    const value = Number(seen[name] ?? fallback), [min, max] = FLAGS[name];
    if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error(`usage:--${name}`);
    return value;
  };
  const share = Number(seen["random-share"] ?? "0.15");
  if (!(share >= 0 && share <= 1)) throw new Error("usage:--random-share");
  return { pkg: resolve(seen.pkg), candidate: resolve(seen.candidate), game: resolve(seen.game), initialProgress: resolve(seen["initial-progress"]),
    scripts: resolve(seen.scripts), out: resolve(seen.out), games: int("games", 1000), slots: int("slots", 4), randomShare: share,
    firstGame: int("first-game", 0), maxFloor: int("max-floor", 47), minChildAct: int("min-child-act", 1) };
}

/** Run floors (the `floor` of public facts; the save descriptor's `floor` counts within its act) of the
 * last map before each act boss: bosses at 17, 33 and 48. */
export const PRE_BOSS_FLOORS = [16, 32, 47];
/** Why a child is loaded from this save taken at run floor `floor` (null: not loaded). */
function childReason(seed, floor, save, randomShare) {
  if (PRE_BOSS_FLOORS.includes(floor)) return "pre_boss";
  if (/treasure/iu.test(String(save.source_room_type ?? ""))) return "after_treasure";
  if (save.act >= 2 && parseInt(sha(`${seed}/${floor}`).slice(0, 8), 16) / 2 ** 32 < randomShare) return "random";
  return null;
}

export async function main(argv = process.argv.slice(2)) {
  let args;
  try { args = parseArgs(argv); } catch (error) { process.stderr.write(`${error.message}\n`); return 2; }
  if (existsSync(args.out) && readdirSync(args.out).length > 0) { process.stderr.write(`--out not empty: ${args.out}\n`); return 2; }
  const saveDir = join(args.out, "saves");
  mkdirSync(saveDir, { recursive: true, mode: 0o700 });
  const dist = (file) => import(pathToFileURL(join(args.pkg, "components/policy-runtime/dist", file)).href);
  const [{ SlotKernel, emptyStats }, { hostSlotNativeFactory }, { parseFastEnvConfig, parseLinkReply }, { ResponseBinding },
    { requireFastEnvManifest }, { coldStartGate }, policy] = await Promise.all([dist("fast-env/kernel.js"), dist("fast-env/host.js"),
    dist("fast-env/contracts.js"), dist("fast-env/binding.js"), dist("fast-env/process.js"), dist("fast-env/cold-start.js"), dist("global-program.js")]);
  const scripts = readFileSync(args.scripts, "utf8").trim().split("\n").map(line => JSON.parse(line))
    .slice(args.firstGame, args.firstGame + args.games);
  const initialProgress = JSON.parse(readFileSync(args.initialProgress, "utf8"));
  const ascension = scripts[0].ascension;
  if (scripts.some(script => script.ascension !== ascension)) { process.stderr.write("scripts mix ascensions\n"); return 2; }

  const manifest = requireFastEnvManifest(policy.createGlobalProgramManifest({ episodeId: "save-replay-probe", p1Revision: "r1.9",
    identity: { kind: "managed", protocol_version: "measure", session: { runtime_instance_id: "measure", environment_fingerprint: "measure" },
      game_continuity_id: null, producer: {}, game: {} },
    artifactPath: join(args.out, "program.json"), artifactBytes: Buffer.from("{}"),
    adapter: { id: "save-replay-probe", version: "1", protocol: "sts2.policy-runtime/agent-session-ndjson-1", code_sha256: "d".repeat(64) } }));
  const sampling = { mode: "temperature_uniform", seed: "save-replay-probe", arm_id: "probe",
    classes: { combat: { temperature: 1, uniform_mix: 0 }, noncombat: { temperature: 1, uniform_mix: 0 } } };
  // The production env driver's config (env_driver.mjs), one slot per concurrent game; the link is in-process.
  const config = parseFastEnvConfig({ schema: "sts2.policy-runtime/fast-env-config-1", env_process_id: `probe-${process.env.SLURM_JOB_ID ?? "local"}`,
    slots: args.slots, host: { package: join(args.pkg, "components/host-runtime"), candidate_directory: args.candidate, game_directory: args.game,
      character: "Defect", language: "zh", ascension, request_timeout_ms: 30000 },
    agent_manifest: join(args.out, "agent-manifest.json"), compact: { enabled: true, reconcile_interval: 64 }, checks: { sample_rate: 0.01 },
    decision_link: { kind: "unix", path: join(args.out, "unused.sock") },
    inference: { timeout_ms: 10000, actor_id: "save-replay-probe", single_choice: "score", sampling },
    budget: { max_decisions_per_game: 4000, max_native_steps_per_game: 8000, game_wall_ms: 3_600_000 },
    no_progress: null, settling_timeout_ms: 30000, evidence: { directory: join(args.out, "evidence"), boundary_rows: 64 } });

  const log = (record) => appendFileSync(join(args.out, "events.jsonl"), `${JSON.stringify({ t: Date.now(), ...record })}\n`);
  const jobs = [], byRef = new Map(), byInstance = new Map(), queue = [];
  const nativeSaves = [];   // every native write_continue_save reply (diagnosis of failed saves)
  let refCount = 0;
  const floorsFrom = (from) => Array.from({ length: Math.max(0, args.maxFloor - from + 1) }, (_, i) => from + i);
  const addJob = (job) => {
    job.ref = `${job.kind[0]}${++refCount}`;
    Object.assign(job, { rows: [], kinds: new Map(), attempts: [], close: null, divergence: null, instance: null,
      first_divergence: null, first_blind_divergence: null });
    job.done = new Promise(done => { job.finish = done; });
    jobs.push(job); byRef.set(job.ref, job);
    // A follower (control or child) starts once its parent's game is over: it plays the parent's choices.
    if (job.parent) job.parent.done.then(() => { if (job.kind === "child") queue.unshift(job); else queue.push(job); });
    else {
      queue.push(job);
      // Every follower exists once the parent's game is over; when they have all ended, its row views go.
      job.done.then(() => Promise.all(jobs.filter(other => other.parent === job).map(other => other.done)))
        .then(() => { for (const row of job.rows) row.view = null; });
    }
    return job;
  };
  for (const script of scripts) {
    script.keys = new Map();
    const parent = addJob({ kind: "parent", script, offset: 0, floors: floorsFrom(1), parent: null, save: null, reason: null });
    // The control: a second new run of the seed without any save, playing the parent's choices (replay
    // determinism; saves change nothing).
    addJob({ kind: "control", script, offset: 0, floors: [], parent, save: null, reason: null });
  }

  const link = {
    lateReplies: 0, invalidReplies: 0, connected: true,
    send() {}, cancelPending() {}, setOpenInstances() {}, async close() {},
    decide(rowId, gameInstanceId, line) {
      const message = JSON.parse(line), job = byInstance.get(gameInstanceId);
      const { observation, catalog } = message.input;
      const index = job.offset + message.step_index, row = sessionRow(observation, catalog);
      const kind = observation.scene?.native_kind ?? null;
      if (!job.kinds.has(message.floor)) job.kinds.set(message.floor, kind);
      // Off the script or the parent's game: the game stops here (the row is never acted on).
      const stop = (why) => { job.divergence ??= { row: index, floor: message.floor, kind, why }; return Promise.resolve({ kind: "timeout" }); };
      let selected;
      if (job.kind === "parent") {
        // The parent replays the script by index; its row's P1 catalog must equal the script's.
        const step = job.script.steps[index];
        let key = step ? job.script.keys.get(index) : null;
        if (step && key === undefined) { key = catalogKey(step[0]); job.script.keys.set(index, key); }
        const on = step && key === catalogKey(catalog);
        job.rows.push(keptRow(row, on ? row.items[step[1]] : null));
        if (!on) return stop(step ? "catalog" : "script_ended");
        selected = step[1];
      } else {
        // A follower compares each row with the parent's same row and plays the parent's choice by content.
        const parentRow = job.parent.rows[index];
        job.rows.push({ full: row.full, blind: row.blind });
        if (!parentRow) return stop("parent_ended");
        const divergence = rowDivergence(parentRow, row, index, message.step_index);
        if (divergence) job.first_divergence ??= divergence;
        if (divergence && !divergence.entity_ids_only) job.first_blind_divergence ??= divergence;
        if (parentRow.chosen === null) return stop("parent_stopped");
        selected = row.items.indexOf(parentRow.chosen);
        if (selected < 0) return stop("choice_absent");
      }
      const q = Array(catalog.length).fill(0); q[selected] = 1;
      return Promise.resolve({ kind: "reply", reply: parseLinkReply({ schema: LINK_SCHEMA, type: "scored", row_id: rowId,
        game_instance_id: gameInstanceId, catalog_sha256: message.catalog_sha256, mu_snapshot_sha256: "0".repeat(64),
        segment_id: `probe-${rowId}`, row_in_segment: 0, selected_index: selected, behavior_q: q, selected_q: 1,
        applied: { arm_id: "probe", decision_class: message.in_combat ? "combat" : "noncombat", temperature: 1, uniform_mix: 0 } }) });
    }
  };

  const baseFactory = await hostSlotNativeFactory(config);
  const factory = { hostVersion: baseFactory.hostVersion, async start() {
    const native = await baseFactory.start();
    // Diagnosis only: keep the native's own reply to every save request (the Host keeps just a code).
    const process_ = native.started?.runtime?.process;
    if (process_?.request) {
      const request = process_.request.bind(process_);
      process_.request = async (message, timeoutMs) => {
        const reply = await request(message, timeoutMs);
        if (message?.cmd === "write_continue_save") nativeSaves.push({ path: message.path, reply });
        return reply;
      };
    }
    return native;
  } };

  const stats = emptyStats();
  let rowId = 0, stopping = false;
  const emit = (event, data) => {
    const job = data.game_ref ? byRef.get(data.game_ref) : null;
    log({ event, ref: data.game_ref ?? null, slot: data.slot ?? null, reason: data.reason ?? null, detail: data.detail ?? null,
      floor: data.floor ?? data.public_facts?.floor ?? null, decisions: data.decisions ?? null, terminal: data.terminal ?? null });
    if (!job) return;
    if (event === "opened") { job.instance = data.game_instance_id; byInstance.set(data.game_instance_id, job); job.start = data.start; }
    else if (event === "saved") {
      const save = data.save;
      job.attempts.push({ floor: data.floor, act: save.act, outcome: "saved", source_room_type: save.source_room_type ?? null,
        save_boundary: save.save_boundary ?? null, path: save.path, bytes: save.bytes, decisions: data.decisions, save_ms: data.save_ms });
      // A child saves again at the floor it loaded at (the map right after the load: a load/save round
      // trip, compared with the save it loaded) and at every later floor.
      const reason = job.kind === "parent" && save.act >= args.minChildAct ? childReason(job.script.seed, data.floor, save, args.randomShare) : null;
      if (reason) addJob({ kind: "child", script: job.script, offset: data.decisions, floors: floorsFrom(data.floor),
        parent: job, save, floor: data.floor, reason });
    } else if (event === "save_refused") job.attempts.push({ floor: data.floor, outcome: "refused", reason: data.reason });
    else if (event === "closed" || event === "open_refused") {
      job.close = event === "closed" ? { reason: data.reason, detail: data.detail, decisions: data.decisions, terminal: data.terminal,
        floor: data.public_facts?.floor ?? null, last_step_index: data.last_step_index }
        : { reason: "open_refused", detail: data.reason };
      const failed = /^save_(failed|unknown):(\S+) floor=(\d+) path=(\S+)/u.exec(data.detail ?? "");
      if (failed) {
        const path = failed[4], bytes = existsSync(path) ? statSync(path).size : null, floor = Number(failed[3]);
        job.attempts.push({ floor, outcome: failed[1], reason: failed[2], path, bytes,
          native_reply: nativeSaves.find(entry => entry.path === path)?.reply ?? null });
        // A failed save ends the game (production rule). The probe plays the same script again from a new
        // run in a fresh process to reach the later maps; it never saves at the failed floor again.
        if (job.kind === "parent" && floor < args.maxFloor)
          addJob({ kind: "parent", script: job.script, offset: 0, floors: floorsFrom(floor + 1), parent: null, save: null, reason: "after_failed_save" });
      }
      job.finish();
    } else if (event === "slot_failed") { job.close = { reason: "slot_failed", detail: data.reason }; job.finish(); }
  };
  const ctx = { config, manifest, factory, link, binding: new ResponseBinding(), evidence: nullEvidence(), stats,
    nextRowId: () => ++rowId, emit, stopBuildOutput: (reason, slot) => log({ event: "build_output_stopped", reason, slot }),
    evidenceFailed: (slot, id, reason) => log({ event: "evidence_failed", slot, reason }), stopping: () => stopping,
    sleep: (ms) => delay(ms), coldStart: coldStartGate({ maxConcurrent: args.slots, directory: join(args.out, "cold-start"), sleep: (ms) => delay(ms) }),
    receiptCanonical: null, memoFatal: (reason) => { log({ event: "memo_fatal", reason }); stopping = true; } };
  const slots = Array.from({ length: args.slots }, (_, index) => new SlotKernel(index, ctx));

  const started = Date.now();
  const running = new Set();
  while (queue.length > 0 || running.size > 0) {
    for (const slot of slots) {
      if (slot.state !== "idle" || [...running].some(job => job.slot === slot.index) || queue.length === 0) continue;
      const job = queue.shift();
      job.slot = slot.index; running.add(job);
      job.done.then(() => { running.delete(job); process.stderr.write(`${job.ref} ${job.kind} ${job.script.seed} ${job.close.reason} rows=${job.rows.length}\n`); });
      slot.open({ cmd: "open", command_id: `c-${job.ref}`, slot: slot.index, game_ref: job.ref, seed: job.script.seed,
        initial_progress: initialProgress, start: job.save ? { kind: "from_save", save: job.save } : { kind: "new_run" },
        r7: false, retain_originals: false, save_points: job.floors.length ? { directory: saveDir, floors: job.floors } : null }, false);
    }
    await delay(100);
  }
  stopping = true;
  await Promise.allSettled(slots.map(slot => slot.shutdown()));

  const report = summarize(jobs);
  report.schema = REPLAY_PROBE_SCHEMA; report.host_version = factory.hostVersion; report.wall_s = Math.round((Date.now() - started) / 1000);
  report.args = { scripts: args.scripts, games: scripts.length, first_game: args.firstGame, slots: args.slots, random_share: args.randomShare,
    max_floor: args.maxFloor, min_child_act: args.minChildAct, ascension };
  report.kernel = { decisions: stats.decisions, native_steps: stats.native_steps, saves: stats.saves, cold_starts: stats.cold_starts };
  const memory = process.memoryUsage();
  report.memory_mb = { rss: Math.round(memory.rss / 2 ** 20), heap_used: Math.round(memory.heapUsed / 2 ** 20) };
  writeFileSync(join(args.out, "report.json"), `${JSON.stringify(report, null, 1)}\n`, { mode: 0o600 });
  process.stdout.write(`${JSON.stringify(report.verdict)}\n`);
  return report.verdict.exit;
}

function nullEvidence() {
  return { openGame: () => ({ nativeLine() {}, step() {}, async segmentBoundary() {}, retainSampledCheckMismatch() {}, async close() { return null; } }),
    async refusedOpen() { return null; } };
}

const readJson = (path) => { try { return JSON.parse(readFileSync(path, "utf8")); } catch { return null; } };

/** Pairs, mismatch classes and save outcomes from the finished jobs. */
/** Known kinds of later-save differences between a loaded game and its parent (anything else is "other"):
 * the load bookkeeping, the map flag the native load sets, and the profile-relative discovery lists (a
 * from_save game runs on the default profile, not the parent's initial progress). */
const SAVE_PATH_CLASSES = [["num_reloads", /^\$\.num_reloads$/u], ["run_time", /^\$\.run_time$/u],
  ["map_can_modify", /^\$\.acts\.\d+\.saved_map\.points\.\d+\.can_modify$/u],
  ["discovered", /^\$\.players\.\d+\.discovered_[a-z_]+(\.\d+)?$/u]];

export function summarize(jobs) {
  const parents = jobs.filter(job => job.kind === "parent"), children = jobs.filter(job => job.kind === "child");
  // A game cut by a failed save (env_crash) has no end of its own to compare.
  const cut = (job) => job.attempts.some(attempt => attempt.outcome === "failed" || attempt.outcome === "unknown");
  // Every parent of a seed replays the same game from a new run: the longest one is the reference, and the
  // others must equal it where they overlap (determinism of the replay itself).
  const reference = new Map();
  for (const job of parents) {
    const best = reference.get(job.script.seed);
    if (!best || job.rows.length > best.rows.length) reference.set(job.script.seed, job);
  }
  const parentRepeats = parents.filter(job => reference.get(job.script.seed) !== job).map(job => ({ seed: job.script.seed,
    rows: job.rows.length, divergence: firstRowDivergence(reference.get(job.script.seed).rows, 0, job.rows) }));
  // A follower ends as its parent did: the same last row (both stop where the parent stopped) and terminal.
  const sameEnd = (parent, follower) => parent.rows.length === follower.offset + follower.rows.length
    && (parent.close?.terminal?.result ?? null) === (follower.close?.terminal?.result ?? null)
    && (parent.divergence?.row ?? null) === (follower.divergence?.row ?? null);
  const controls = jobs.filter(job => job.kind === "control").map(job => ({ seed: job.script.seed, rows: job.rows.length,
    parent_rows: job.parent.rows.length, close: job.close?.reason ?? null, stop: job.divergence, same_end: sameEnd(job.parent, job),
    divergence: job.first_blind_divergence, entity_id_divergence: job.first_divergence?.entity_ids_only ? job.first_divergence : null }));
  const pairs = children.map(child => {
    const parent = child.parent, rows = child.first_blind_divergence ?? child.first_divergence;
    const parentEnd = parent.close ?? {}, childEnd = child.close ?? {};
    const loaded = child.instance !== null, partial = cut(parent) || cut(child);
    const posts = [];
    for (const attempt of child.attempts) {
      const mine = parents.filter(job => job.script.seed === child.script.seed).flatMap(job => job.attempts)
        .find(entry => entry.floor === attempt.floor && entry.outcome === "saved")
        ?? parent.attempts.find(entry => entry.floor === attempt.floor);
      if (attempt.outcome !== "saved" || mine?.outcome !== "saved") {
        posts.push({ floor: attempt.floor, child: attempt.outcome, parent: mine?.outcome ?? null, compared: false });
        continue;
      }
      const a = readJson(mine.path), b = readJson(attempt.path);
      if (a === null || b === null) { posts.push({ floor: attempt.floor, compared: false, unreadable: true }); continue; }
      const differing = diffPaths(a, b, new Set(["save_time"]));
      posts.push({ floor: attempt.floor, compared: true, differing: differing.length, paths: differing.slice(0, 20), all_paths: differing,
        rng: differing.filter(path => /rng/iu.test(path)).length, unlock: differing.filter(path => /unlock/iu.test(path)).length,
        unlock_paths_present: unlockPaths(a).length > 0 });
    }
    // The child's save at the floor it loaded at, against the save it loaded: what a load/save round trip changes.
    const roundTrip = posts.find(post => post.floor === child.floor) ?? null;
    const compared = posts.filter(post => post.compared);
    // saved vs refused at the same map is a state difference; a failed save on one side only is counted apart.
    const saveOutcomeDiff = posts.filter(post => !post.compared && !post.unreadable && post.parent !== null && post.child !== post.parent
      && ["saved", "refused"].includes(post.child) && ["saved", "refused"].includes(post.parent)).length;
    // Rows that differ only in entity ids (ties between equal cards; new runs differ the same way, see the
    // controls) do not make another game; they are counted apart (entity_id_rows).
    const status = !loaded ? "load_refused"
      : rows !== null && !rows.entity_ids_only ? "diverged_rows"
        : compared.some(post => post.rng > 0 || post.unlock > 0) ? "diverged_save_rng_unlock"
          : saveOutcomeDiff > 0 ? "diverged_save_outcome"
            : partial ? "same_until_failed_save"
              : !sameEnd(parent, child) ? "diverged_end" : "same_game";
    const postClasses = {};
    for (const path of compared.flatMap(post => post.all_paths)) {
      const name = SAVE_PATH_CLASSES.find(([, pattern]) => pattern.test(path))?.[0] ?? "other";
      postClasses[name] = (postClasses[name] ?? 0) + 1;
    }
    return { seed: child.script.seed, act: child.save.act, floor: child.floor ?? null, act_floor: child.save.floor, reason: child.reason,
      source_room_type: child.save.source_room_type ?? null, round_trip: roundTrip, entity_id_rows: rows?.entity_ids_only === true,
      post_save_path_classes: postClasses,
      status, load: loaded ? null : childEnd.detail, rows_compared: Math.min(child.rows.length, Math.max(0, parent.rows.length - child.offset)),
      stop: child.divergence, first_row_divergence: rows, end: { parent: parentEnd, child: childEnd }, post_saves_compared: compared.length,
      post_saves_rng_unlock_equal: compared.filter(post => post.rng === 0 && post.unlock === 0).length,
      post_saves_unlock_present: compared.filter(post => post.unlock_paths_present).length,
      post_saves_other_paths: [...new Set(compared.flatMap(post => post.paths))].slice(0, 30), save_outcome_differences: saveOutcomeDiff,
      save_failure_one_side: posts.filter(post => !post.compared && (["failed", "unknown"].includes(post.child) || ["failed", "unknown"].includes(post.parent))).length };
  });
  const attempts = jobs.flatMap(job => job.attempts.map(attempt => ({ ...attempt, kind: job.kind, seed: job.script.seed,
    room: job.kinds.get(attempt.floor) ?? null })));
  const byRoom = {};
  for (const attempt of attempts) {
    const room = byRoom[attempt.room ?? "unknown"] ??= { saved: 0, refused: 0, failed: 0, unknown: 0 };
    room[attempt.outcome] += 1;
  }
  const perAct = {};
  for (const pair of pairs) {
    const act = perAct[pair.act] ??= { pairs: 0, same_game: 0, entity_id_rows: 0, statuses: {}, by_reason: {}, post_save_path_classes: {} };
    act.pairs += 1; if (pair.status === "same_game") act.same_game += 1;
    if (pair.entity_id_rows) act.entity_id_rows += 1;
    act.statuses[pair.status] = (act.statuses[pair.status] ?? 0) + 1;
    act.by_reason[pair.reason] = (act.by_reason[pair.reason] ?? 0) + 1;
    for (const [name, count] of Object.entries(pair.post_save_path_classes))
      act.post_save_path_classes[name] = (act.post_save_path_classes[name] ?? 0) + count;
  }
  const loadedPairs = pairs.filter(pair => pair.status !== "load_refused");
  const SAME = new Set(["same_game", "same_until_failed_save"]);
  // A control that differs from its parent: two new runs of one script are not the same game (saves or replay).
  const controlsDiverged = controls.filter(control => control.divergence !== null || !control.same_end).length;
  return {
    verdict: { exit: loadedPairs.length === 0 ? 1 : loadedPairs.every(pair => SAME.has(pair.status)) && controlsDiverged === 0 ? 0 : 3,
      controls: controls.length, controls_diverged: controlsDiverged,
      controls_entity_ids_only: controls.filter(control => control.divergence === null && control.entity_id_divergence !== null).length,
      pairs: pairs.length, same_game: pairs.filter(pair => pair.status === "same_game").length,
      same_until_failed_save: pairs.filter(pair => pair.status === "same_until_failed_save").length, per_act: perAct },
    parents: { games: reference.size, runs: parents.length,
      // A parent that reached the eval game's terminal on the script: the replay itself is faithful.
      terminal_on_script: [...reference.values()].filter(job => job.close?.reason === "terminal" && job.divergence === null).length,
      off_script: parents.filter(job => job.divergence !== null).map(job => ({ seed: job.script.seed, ...job.divergence })),
      repeats: parentRepeats, controls,
      ends: [...reference.values()].map(job => ({ seed: job.script.seed, close: job.close, rows: job.rows.length,
        eval_max_floor: job.script.max_floor, eval_result: job.script.result })) },
    saves: { attempts: attempts.length, by_room_kind: byRoom,
      failures: attempts.filter(attempt => attempt.outcome === "failed" || attempt.outcome === "unknown") },
    pairs
  };
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) process.exitCode = await main();
