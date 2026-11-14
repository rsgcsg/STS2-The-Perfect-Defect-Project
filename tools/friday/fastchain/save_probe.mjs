#!/usr/bin/env node
// P1-e save probe (W3 save tickets). Question: is a game resumed from a fast-env map save the
// same game as the one that went on without saving, and does the resumed run keep the parent's
// unlock state? Runs the production fast-env Host path (HostSlotNative reset / saveMap /
// loadSave / capture / step, from a built package) against the exact game with a seeded random
// player, no model:
//   A  fresh process, new run (seed, initial progress) -> plays to the first map at floor >=
//      --save-floor -> saveMap -> plays on (--steps) recording every capture; at the first map of
//      a later floor it saves again (the "post" save, for a raw compare).
//   Bn fresh process (--loads of them), loadSave(the A save) -> the same seeded player from the
//      same point, the same post save.
// Compared: every capture after the save (the public snapshot and Reads, per-capture
// bookkeeping dropped, hex ids of 16+ characters renamed in order of first appearance: they are
// per-process random), each chosen action, where the game ended; the two post saves as JSON.
// Exit 0 = every load played the same game; 3 = a load diverged; 1 = incomplete; 2 = usage.
// Needs the exact game build (a Mac with another game build is refused at process start), so it
// runs on a cluster CPU node: save_probe.sh. Writes only under --out (private; the saves stay
// there, never to R2 or git). A diverging capture pair is written there for diffing.
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

export const PROBE_SCHEMA = "spireagent/fastchain-save-probe-1";
const MANAGER = { principal: "manager", sharedService: true };
/** Per-capture bookkeeping of a snapshot: differs between processes by construction. */
const VOLATILE = new Set(["snapshot_id", "sequence", "observed_at", "session"]);

const canonical = (value) => Array.isArray(value) ? `[${value.map(canonical).join(",")}]`
  : value !== null && typeof value === "object"
    ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`
    : JSON.stringify(value);
const sha256 = (text) => createHash("sha256").update(text, "utf8").digest("hex");
class ProbeStop extends Error { name = "ProbeStop"; }

/** `observed_at` at any depth: each Read response carries the capture's wall time. */
const withoutTimes = (value) => Array.isArray(value) ? value.map(withoutTimes)
  : value !== null && typeof value === "object"
    ? Object.fromEntries(Object.entries(value).filter(([key]) => key !== "observed_at").map(([key, child]) => [key, withoutTimes(child)]))
    : value;

/** One capture as compared across processes. */
export function normalizeBasis(basis) {
  const snapshot = Object.fromEntries(Object.entries(basis.snapshot).filter(([key]) => !VOLATILE.has(key)));
  const names = new Map(), sequences = new Map();
  const text = canonical(withoutTimes({ snapshot, reads: basis.reads ?? null })).replace(/[0-9a-f]{16,}/gu, (hex) => {
    if (!names.has(hex)) names.set(hex, `#${names.size}`);
    return names.get(hex);
  // Snapshot and interaction ids end in the process's snapshot sequence (`managed_<hex>_s3`): renamed
  // in order of first appearance like the hex ids.
  }).replace(/(#\d+)_s(\d+)/gu, (_all, id, sequence) => {
    if (!sequences.has(sequence)) sequences.set(sequence, `@${sequences.size}`);
    return `${id}_${sequences.get(sequence)}`;
  });
  return { text, digest: sha256(text) };
}

/** Seeded uniform [0, 1): mulberry32 over the seed text's sha256. */
export function seededRandom(seedText) {
  let state = parseInt(sha256(seedText).slice(0, 8), 16) >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = Math.imul(state ^ (state >>> 15), state | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** JSON paths where two parsed saves differ (`ignore`: key names never compared). */
export function diffPaths(left, right, ignore = new Set(), path = "$", out = []) {
  if (out.length >= 200) return out;
  const object = (value) => value !== null && typeof value === "object";
  if (object(left) && object(right) && Array.isArray(left) === Array.isArray(right)) {
    const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
    for (const key of keys) if (!ignore.has(key)) diffPaths(left[key], right[key], ignore, `${path}.${key}`, out);
  } else if (canonical(left) !== canonical(right)) out.push(path);
  return out;
}
/** Every JSON path of a value whose key mentions unlocks. */
export function unlockPaths(value, path = "$", out = []) {
  if (value !== null && typeof value === "object")
    for (const [key, child] of Object.entries(value)) {
      if (/unlock/iu.test(key)) out.push(`${path}.${key}`);
      unlockPaths(child, `${path}.${key}`, out);
    }
  return out;
}

const kindOf = (snapshot) => snapshot.interaction?.kind ?? null;
const floorOf = (snapshot) => snapshot.persistent?.content?.run?.floor ?? null;
const terminal = (snapshot) => kindOf(snapshot) === "game_over";
const actionsOf = (snapshot) => snapshot.bound_actions?.actions ?? [];

/** Captures until the game offers actions or has ended. */
async function settled(native, { sleep, timeoutMs }) {
  const since = Date.now();
  for (;;) {
    const full = await native.capture();
    const snapshot = full.basis.snapshot;
    if (snapshot.status !== "settling" && (terminal(snapshot) || actionsOf(snapshot).length > 0)) return full;
    if (Date.now() - since > timeoutMs) throw new ProbeStop("settling_stalled");
    await sleep(50);
  }
}

/** The seeded random player from where the game is. `beforeAct` may save and ask for a fresh capture. */
async function play(native, random, { maxSteps, stopAt = () => false, beforeAct = async () => false, record, timing, options }) {
  const trace = [], texts = [];
  for (let steps = 0; ;) {
    const full = await settled(native, options);
    const snapshot = full.basis.snapshot;
    const entry = { kind: kindOf(snapshot), floor: floorOf(snapshot) };
    if (record) { const normal = normalizeBasis(full.basis); entry.digest = normal.digest; texts.push(normal.text); }
    trace.push(entry);
    if (terminal(snapshot)) { entry.end = "terminal"; entry.victory = snapshot.interaction?.content?.surface?.victory ?? null; return { trace, texts, snapshot }; }
    if (stopAt(snapshot)) { entry.end = "stop_at"; return { trace, texts, snapshot }; }
    if (steps >= maxSteps) { entry.end = "max_steps"; return { trace, texts, snapshot }; }
    if (await beforeAct(snapshot)) { entry.end = "saved"; continue; }
    const actions = actionsOf(snapshot);
    entry.index = Math.floor(random() * actions.length);
    entry.label = actions[entry.index].label ?? null;
    const started = performance.now();
    const receipt = await native.step({ requestId: `probe-${randomUUID()}`, expectedSnapshotId: String(snapshot.snapshot_id),
      boundActionId: String(actions[entry.index].bound_action_id) });
    timing.step_ms += performance.now() - started; timing.steps += 1; steps += 1;
    // Never retried: a step that was not delivered ends the probe.
    if (receipt?.delivery !== "delivered") throw new ProbeStop(`step_${receipt?.delivery ?? "no_receipt"}:${receipt?.reason_code ?? "none"}`);
  }
}

async function identityOf(native) {
  // The Host's own run identity (initial-progress proof, provenance); not part of SlotNative.
  const result = await native.driver.handle({ command: "episode_identity", request_id: randomUUID().replaceAll("-", "") }, MANAGER);
  return result?.identity ?? null;
}
const progressSummary = (identity) => {
  const proof = identity?.initial_progress ?? null;
  return proof === null ? null : { recipe: proof.recipe ?? null, progress_sha256: proof.progress_sha256 ?? null,
    run_unlock_state_matches: proof.run_unlock_state_matches ?? null,
    unlock_state_sha256: proof.unlock_state ? sha256(canonical(proof.unlock_state)) : null };
};
const round = (value) => value === null ? null : Math.round(value);

/** One seed: A, then `loads` fresh-process loads; the report of what matched. */
export async function probeSeed({ factory, seed, initialProgress, saveFloor = 1, steps = 400, loads = 2, out,
  sleep = (ms) => new Promise(done => setTimeout(done, ms)), timeoutMs = 30_000 }) {
  const options = { sleep, timeoutMs };
  const report = { seed, save_floor: saveFloor, steps, loads, status: "incomplete", save: null, save_refusals: [],
    parent: null, resumed: [], failure: null };
  const timing = { step_ms: 0, steps: 0 };
  let stage = "parent_start", native = null;
  const posts = {};
  const postHook = (label, savedFloor) => {
    let done = false;
    return async (snapshot) => {
      if (done || kindOf(snapshot) !== "map_navigation" || !(floorOf(snapshot) > savedFloor)) return false;
      done = true;
      const started = performance.now();
      const result = await native.saveMap(join(out, `post-${seed}-${label}.json`));
      posts[label] = { result, ms: performance.now() - started };
      return result.kind === "saved";
    };
  };
  try {
    let started = performance.now();
    native = await factory.start();
    const parent = { start_ms: round(performance.now() - started) };
    stage = "parent_reset"; started = performance.now();
    await native.reset({ seed, initialProgress, r7: false });
    parent.reset_ms = round(performance.now() - started);
    parent.native_cpu_ms_after_reset = await native.nativeCpuMs();
    parent.progress = progressSummary(await identityOf(native));
    stage = "parent_prefix";
    const tried = new Set();
    let save = null;
    while (save === null) {
      const prefix = await play(native, seededRandom(`${seed}/prefix/${tried.size}`), { maxSteps: 3000, timing, options,
        stopAt: (snapshot) => kindOf(snapshot) === "map_navigation" && floorOf(snapshot) >= saveFloor && !tried.has(floorOf(snapshot)) });
      if (prefix.trace.at(-1).end !== "stop_at") throw new ProbeStop(`no_save_point:${prefix.trace.at(-1).end}`);
      const floor = floorOf(prefix.snapshot);
      tried.add(floor);
      started = performance.now();
      const result = await native.saveMap(join(out, `save-${seed}-f${floor}.json`));
      if (result.kind === "saved") save = { descriptor: result.save, ms: performance.now() - started };
      else report.save_refusals.push({ floor, reason: result.reason });
    }
    report.save = { floor: save.descriptor.floor, act: save.descriptor.act, bytes: save.descriptor.bytes,
      sha256: save.descriptor.sha256, save_ms: round(save.ms) };
    stage = "parent_continue";
    const continued = await play(native, seededRandom(`${seed}/after-save`), { maxSteps: steps, record: true, timing, options,
      beforeAct: postHook("parent", save.descriptor.floor) });
    parent.progress_after = progressSummary(await identityOf(native));
    report.parent = { ...parent, trace_length: continued.trace.length, end: continued.trace.at(-1) };
    await native.shutdown(false); native = null;

    const parentPost = posts.parent?.result.kind === "saved" ? JSON.parse(readFileSync(posts.parent.result.save.path, "utf8")) : null;
    for (let load = 1; load <= loads; load += 1) {
      const label = `load${load}`;
      stage = `${label}_start`; started = performance.now();
      native = await factory.start();
      const resumed = { start_ms: round(performance.now() - started), native_cpu_ms_after_start: await native.nativeCpuMs() };
      stage = `${label}_load`; started = performance.now();
      await native.loadSave({ save: save.descriptor, r7: false });
      resumed.load_ms = round(performance.now() - started);
      resumed.native_cpu_ms_after_load = await native.nativeCpuMs();
      const identity = await identityOf(native);
      resumed.progress = progressSummary(identity);
      resumed.origin = identity?.episode_provenance?.origin?.kind ?? null;
      resumed.provenance = identity?.episode_provenance?.verdict ?? null;
      stage = `${label}_continue`;
      const run = await play(native, seededRandom(`${seed}/after-save`), { maxSteps: steps, record: true, timing, options,
        beforeAct: postHook(label, save.descriptor.floor) });
      await native.shutdown(false); native = null;
      // The same game: every capture and choice equal, in order, to the end.
      let first = null;
      for (let i = 0; i < Math.max(run.trace.length, continued.trace.length) && first === null; i += 1)
        if (canonical(run.trace[i] ?? null) !== canonical(continued.trace[i] ?? null)) first = i;
      if (first !== null && run.texts[first] !== undefined && continued.texts[first] !== undefined) {
        writeFileSync(join(out, `diverge-${seed}-${label}-parent.json`), continued.texts[first], { mode: 0o600 });
        writeFileSync(join(out, `diverge-${seed}-${label}-resumed.json`), run.texts[first], { mode: 0o600 });
      }
      resumed.trace_length = run.trace.length;
      resumed.first_divergence = first === null ? null : { index: first, parent: continued.trace[first] ?? null, resumed: run.trace[first] ?? null };
      resumed.kinds = Object.entries(run.trace.reduce((counts, entry) => ({ ...counts, [entry.kind]: (counts[entry.kind] ?? 0) + 1 }), {}));
      const post = posts[label]?.result.kind === "saved" ? JSON.parse(readFileSync(posts[label].result.save.path, "utf8")) : null;
      if (parentPost && post) {
        const differing = diffPaths(parentPost, post, new Set(["save_time"]));
        const unlocks = unlockPaths(parentPost);
        resumed.post_save = { differing_paths: differing, rng_paths_differing: differing.filter(path => /rng/iu.test(path)),
          unlock_paths: unlocks, unlock_paths_differing: differing.filter(path => /unlock/iu.test(path)) };
      } else resumed.post_save = null;
      report.resumed.push(resumed);
    }
    report.status = report.resumed.every(resumed => resumed.first_divergence === null) ? "same_game" : "diverged";
  } catch (error) {
    report.failure = { stage, reason: error instanceof Error ? `${error.name}:${error.message}`.slice(0, 300) : String(error) };
  } finally {
    if (native !== null) await native.shutdown(true).catch(() => {});
  }
  report.timing = { steps: timing.steps, step_ms_mean: timing.steps ? Math.round(timing.step_ms / timing.steps) : null };
  return report;
}

/** What the run says about P1-e, from the per-seed reports. */
export function verdict(reports) {
  const complete = reports.every(report => report.status !== "incomplete");
  const resumed = reports.flatMap(report => report.resumed);
  const posts = resumed.map(entry => entry.post_save).filter(Boolean);
  return {
    same_game: !complete ? null : reports.every(report => report.status === "same_game"),
    // The run's UnlockState is visible only if the native save carries it.
    unlock_state: posts.length === 0 ? "post_saves_missing"
      : posts.every(post => post.unlock_paths.length === 0) ? "not_in_save_unproven"
        : posts.every(post => post.unlock_paths_differing.length === 0) ? "carried_in_save_equal" : "differs",
    parent_progress_proven: reports.some(report => !report.parent) ? null
      : reports.every(report => report.parent.progress?.run_unlock_state_matches === true),
    resumed_initial_progress_null: resumed.length === 0 ? null : resumed.every(entry => entry.progress === null)
  };
}

const arg = (name, fallback) => { const i = process.argv.indexOf(`--${name}`); return i >= 0 ? process.argv[i + 1] : fallback; };
async function main() {
  const pkg = arg("pkg"), out = arg("out") && resolve(arg("out")), candidate = arg("candidate"), progressFile = arg("initial-progress");
  if (!pkg || !out || !candidate || !progressFile) {
    process.stderr.write("usage: save_probe.mjs --pkg PKG --candidate DIR --initial-progress FILE --out DIR [--game DIR] [--seeds A,B] "
      + "[--save-floor 1] [--steps 400] [--loads 2] [--ascension 10] [--language zh]\n");
    return 2;
  }
  mkdirSync(out, { recursive: true, mode: 0o700 });
  const { hostSlotNativeFactory } = await import(pathToFileURL(join(pkg, "components/policy-runtime/dist/fast-env/host.js")).href);
  const factory = await hostSlotNativeFactory({
    host: { package: join(pkg, "components/host-runtime"), candidate_directory: candidate, game_directory: arg("game", null),
      character: "Defect", language: arg("language", "zh"), ascension: Number(arg("ascension", "10")), request_timeout_ms: 30_000 },
    compact: { enabled: true, reconcile_interval: 64, projection_memo: null },
    // Every capture gets the Host full check here (production samples 1%).
    checks: { sample_rate: 1, reduce_light: null }, memo: { native_card: null, receipt_canonical: null } });
  const initialProgress = JSON.parse(readFileSync(progressFile, "utf8"));
  const reports = [];
  for (const seed of arg("seeds", "W3P1E0001,W3P1E0002").split(",")) {
    const report = await probeSeed({ factory, seed, initialProgress, out, saveFloor: Number(arg("save-floor", "1")),
      steps: Number(arg("steps", "400")), loads: Number(arg("loads", "2")) });
    process.stderr.write(`${seed}: ${report.status}${report.failure ? ` ${report.failure.stage} ${report.failure.reason}` : ""}\n`);
    reports.push(report);
  }
  const summary = { schema: PROBE_SCHEMA, generated_at: new Date().toISOString(), host_version: factory.hostVersion,
    verdict: verdict(reports), seeds: reports };
  writeFileSync(join(out, "report.json"), `${JSON.stringify(summary, null, 1)}\n`, { mode: 0o600 });
  process.stdout.write(`${JSON.stringify(summary.verdict)}\n`);
  return summary.verdict.same_game === null ? 1 : summary.verdict.same_game ? 0 : 3;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) process.exitCode = await main();
