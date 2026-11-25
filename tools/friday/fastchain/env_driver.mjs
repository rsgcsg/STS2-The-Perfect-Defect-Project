// Fast-chain env driver (port of the private fc-driver-c3b.mjs; launched by env.sh): one
// K-game fast-env process, real Managed natives, decision link on the node's aggregator ->
// inference service -> assembler -> learner. Learner admission per checked `opened` via the
// node's admission sidecar (a refused or failed admission closes that game with close_slot,
// never resent); a game whose evidence failed is excluded. Status at warm end, then every
// --status-s until warm+steady, then stop. Never retries a game; the slot that closes opens
// the next start. Every setting is a flag set from the run profile (env.sh).
// Environment-side recipe (NEW-METHODS-PLAN I4/I6): this driver's exploration arm (one
// `inference.sampling` per fast-env process) and K games per start in its own slots (one
// assembler), each game logged in driver.jsonl with arm, ascension, group and outcome.
// Save starts (RL5-METHODS B, tickets.mjs): with `save_starts.share` > 0 new runs save at
// map screens into the node pool and that share of starts opens from a pool save; the
// driver's save counters go to driver.jsonl on every status and at exit.
import { createConnection } from "node:net";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { SaveTickets } from "./tickets.mjs";

const arg = (name, fallback) => { const i = process.argv.indexOf(`--${name}`); return i >= 0 ? process.argv[i + 1] : fallback; };
const PKG = arg("pkg"), OUT = arg("out"), K = Number(arg("slots", "8"));
// #530 S-1: the level every game of this driver plays; required, never a default.
const ASCENSION_RAW = arg("ascension");
if (!/^(10|[0-9])$/.test(ASCENSION_RAW ?? "")) {
  process.stderr.write(`--ascension is required: an integer 0-10 (got ${ASCENSION_RAW})\n`); process.exit(2);
}
const ASCENSION = Number(ASCENSION_RAW);
// I4: this driver's arm of the exploration ladder; required, never a default. The fast env,
// the service and the assembler check every row's applied T/mix against this sampling,
// and behavior_q is the distribution actually sampled from.
const LADDER = JSON.parse(arg("exploration-ladder") ?? "null"), ARM = arg("arm");
if (!LADDER || typeof LADDER !== "object" || !Object.hasOwn(LADDER, ARM ?? "")) {
  process.stderr.write(`--exploration-ladder and --arm (one of its arms) are required (got ${ARM})\n`); process.exit(2);
}
const SAMPLING = { mode: "temperature_uniform", seed: `wp8-${arg("tag")}`, arm_id: ARM,
  classes: { combat: LADDER[ARM].combat, noncombat: LADDER[ARM].noncombat } };
// A3: the run's lowest arm temperature (recipe sampling_temperature_min). The fast env
// refuses an arm below it; 1 (the default) leaves the key out, so the config is as before.
const TEMPERATURE_MIN = Number(arg("temperature-min", "1"));
// I6: games per start (1-16, at most this driver's slots); required, never a default.
const REPLAY_K_RAW = arg("replay-k");
if (!/^([1-9]|1[0-6])$/.test(REPLAY_K_RAW ?? "") || Number(REPLAY_K_RAW) > K) {
  process.stderr.write(`--replay-k is required: an integer 1-16, at most --slots ${K} (got ${REPLAY_K_RAW})\n`); process.exit(2);
}
const REPLAY_K = Number(REPLAY_K_RAW);
// #529 R7 audit games: before each open the node sidecar's R7Selection decides `open.r7`.
const R7 = arg("r7", "off") === "on";
const WARM_S = Number(arg("warm-s", "90")), STEADY_S = Number(arg("steady-s", "240"));
// c3b (lead 11-12): named flags, printed at startup. Both only touch latency tolerance and
// the pause before a fresh seed after a not_started close; no game is retried or resent.
const INFERENCE_TIMEOUT_MS = Number(arg("inference-timeout-ms", "10000"));
const REOPEN_BACKOFF = { start_ms: Number(arg("reopen-backoff-start-ms", "1000")),
  cap_ms: Number(arg("reopen-backoff-cap-ms", "10000")), jitter: Number(arg("reopen-backoff-jitter", "0.5")) };
const notStartedRun = new Map();  // slot -> consecutive not_started closes
const reopenDelayMs = (n) => Math.round(Math.min(REOPEN_BACKOFF.cap_ms, REOPEN_BACKOFF.start_ms * 2 ** (n - 1))
  * (1 + REOPEN_BACKOFF.jitter * (2 * Math.random() - 1)));
const SINGLE = arg("single-choice", "score"), SOCK = arg("link"), STATUS_S = Number(arg("status-s", "300"));
const ADMIT = createConnection(arg("admit"));
const admitQueue = [];
createInterface({ input: ADMIT }).on("line", line => { const done = admitQueue.shift(); done(JSON.parse(line)); });
const admitCall = (request) => new Promise(resolve => { admitQueue.push(resolve); ADMIT.write(`${JSON.stringify(request)}\n`); });
const games = new Map(), admission = { sent: 0, admitted: 0, refused: {}, closed_after_refusal: 0, excluded: 0, exclusion_failed: {} };
const SEEDS = readFileSync(arg("seeds"), "utf8").trim().split("\n");
// #603: this driver's stream is offset, offset + stride, ... (env.sh: disjoint over the
// run's drivers until the file wraps); a seed taken past the file's end is counted.
let seedIndex = Number(arg("seed-offset", "0"));
const SEED_STRIDE = Number(arg("seed-stride", "1"));
const seedStream = { offset: seedIndex, stride: SEED_STRIDE, taken: 0, wrapped: 0, file_seeds: SEEDS.length };
// --eval-only on: no wrap, no game attested to the learner (never trained), stop when done.
const EVAL_ONLY = arg("eval-only", "off") === "on";
// eval_only: a seed whose game ended with no delivery in question (open refused at native
// start or reset; closed not_started with no native step sent, or at any step on an inference
// timeout: that step's successor was read and no action came) goes to the back of this
// driver's queue, at most --eval-retries times, as a new game from the start (greedy: the same
// game); any other not_started after a native step, or unknown, is never replayed.
const EVAL_RETRIES = Number(arg("eval-retries", "2"));
const retryQueue = [], attempts = new Map();  // seed -> starts so far
const requeue = (seed, reason, nativeSteps = 0) => {
  const n = attempts.get(seed) ?? 1;
  if (!EVAL_ONLY || n > EVAL_RETRIES) return;
  attempts.set(seed, n + 1); retryQueue.push(seed);
  log({ event: "eval_seed_requeued", seed, attempt: n + 1, reason, native_steps: nativeSteps });
};
const nextSeed = () => {
  const index = seedIndex;
  if (EVAL_ONLY && index >= SEEDS.length) return retryQueue.shift() ?? null;
  seedIndex += SEED_STRIDE;
  seedStream.taken += 1;
  if (index >= SEEDS.length) {
    if (seedStream.wrapped++ === 0) log({ event: "seed_stream_wrapped", index, file_seeds: SEEDS.length });
  }
  return SEEDS[index % SEEDS.length];
};
const gameFacts = (ref) => { const g = games.get(ref); return { game_ref: ref, ...(g ?? {}) }; };
const initialProgress = JSON.parse(readFileSync(arg("initial-progress"), "utf8"));
mkdirSync(OUT, { recursive: true, mode: 0o700 });
const log = (record) => appendFileSync(join(OUT, "driver.jsonl"), `${JSON.stringify({ t: Date.now(), ...record })}\n`);
// Save starts: `--save-starts` the recipe knob (JSON), `--save-pool` the node-local pool
// (env.sh). Off (share 0 or no flag): every start is the next seed of the list as a new run.
// An arm with `new_runs_only` (user 2026-11-24) never touches the pool: no save start and no
// save points, whatever the share (the arm's games are new runs only).
// Recipe A (tickets.mjs "Greedy cell"): with save_starts.act_target "greedy_cell" or greedy_saves, a new_runs_only
// arm's driver takes the role "greedy" (still new runs only): it writes its floor stats and, with greedy_saves,
// saves and exports them to --greedy-dir (the run directory); pool drivers read and import from there.
const SAVE_STARTS = JSON.parse(arg("save-starts") ?? "null");
const NEW_RUNS_ONLY = LADDER[ARM].new_runs_only === true;
const GREEDY_ROLE = NEW_RUNS_ONLY && SAVE_STARTS?.share > 0
  && (SAVE_STARTS.act_target === "greedy_cell" || SAVE_STARTS.greedy_saves === true);
const saves = SAVE_STARTS?.share > 0 && (!NEW_RUNS_ONLY || GREEDY_ROLE) ? new SaveTickets(SAVE_STARTS, { id: arg("tag"), ascension: ASCENSION, character: "Defect",
  pool: arg("save-pool"), progress_sha256: createHash("sha256").update(JSON.stringify(initialProgress)).digest("hex"),
  ...(GREEDY_ROLE ? { role: "greedy" } : {}), greedy_dir: arg("greedy-dir"),
  node_index: arg("node-index") === undefined ? null : Number(arg("node-index")),
  node_count: arg("node-count") === undefined ? null : Number(arg("node-count")) }, { log }) : null;
// I6 K replay: each start opens REPLAY_K times in this driver's slots as group `TAG-kN` (a
// save start: one claimed use per game); a member that is refused or ends early is never
// replaced (no game is retried).
let group = null, groupCount = 0;
const nextStart = () => {
  if (group === null || group.member >= group.size) {
    const id = `${arg("tag")}-k${++groupCount}`;
    const plan = saves ? saves.plan(id, REPLAY_K) : { seed: null, start: { kind: "new_run" }, save_points: null, tickets: null };
    const seed = plan.seed ?? nextSeed();
    if (seed === null) return null;
    group = { id, plan, member: 0, size: plan.tickets?.length ?? REPLAY_K, seed };
  }
  return { group, member: group.member++ };
};

const policy = await import(pathToFileURL(join(PKG, "components/policy-runtime/dist/global-program.js")).href);
const manifest = policy.createGlobalProgramManifest({ episodeId: "wp8-chain", p1Revision: "r1.9",
  identity: { kind: "managed", protocol_version: "measure",
    session: { runtime_instance_id: "measure", environment_fingerprint: "measure" }, game_continuity_id: null, producer: {}, game: {} },
  artifactPath: join(OUT, "program.json"), artifactBytes: Buffer.from("{}"),
  adapter: { id: "wp8-chain", version: "1", protocol: "sts2.policy-runtime/agent-session-ndjson-1", code_sha256: "d".repeat(64) } });
// The model agent binds the M-std input spec (as the actor's model manifest does), not the program spec.
manifest.input.input_spec = JSON.parse(readFileSync(arg("input-spec"), "utf8"));
writeFileSync(join(OUT, "agent-manifest.json"), JSON.stringify(manifest), { mode: 0o600 });

const rel = process.env.REL;
// --memo '{"projection":N,"reduce":N,"receipt":N,"deck":true,"hud":true}': the v3 memo layers (absent = all off).
const MEMO_RAW = arg("memo") ? JSON.parse(arg("memo")) : null;
const MEMO = MEMO_RAW && Object.keys(MEMO_RAW).length ? MEMO_RAW : null;  // {} = off
const COMPACT = arg("compact", "on") === "on";
const config = { schema: "sts2.policy-runtime/fast-env-config-1", env_process_id: `wp8-${process.env.SLURM_JOB_ID ?? "local"}-${arg("tag")}`, slots: K,
  host: { package: join(PKG, "components/host-runtime"), candidate_directory: arg("candidate"), game_directory: `${rel}/game`,
    character: "Defect", language: "zh", ascension: ASCENSION, request_timeout_ms: 30000 },
  agent_manifest: join(OUT, "agent-manifest.json"), compact: { enabled: COMPACT, reconcile_interval: 64,
    ...(MEMO?.projection ? { projection_memo: { check_interval: MEMO.projection } } : {}) }, checks: { sample_rate: 0.01,
    // C1 (V3-wu/V3-shen): --reduce-light-interval N > 0 sets reduce_light.strict_interval
    ...(Number(arg("reduce-light-interval", "0")) > 0 ? { reduce_light: { strict_interval: Number(arg("reduce-light-interval")) } } : {}) },
  decision_link: { kind: "unix", path: SOCK },
  inference: { timeout_ms: INFERENCE_TIMEOUT_MS, actor_id: `v3g-${arg("tag")}`, single_choice: SINGLE, sampling: SAMPLING,
    ...(TEMPERATURE_MIN !== 1 ? { temperature_min: TEMPERATURE_MIN } : {}) },
  budget: { max_decisions_per_game: 4000, max_native_steps_per_game: 8000, game_wall_ms: 3_600_000 },
  no_progress: null, settling_timeout_ms: 30000,
  ...(arg("cold-start") ? { cold_start: { max_concurrent: Number(arg("cold-start")), directory: null } } : {}),
  evidence: { directory: join(OUT, "evidence"), boundary_rows: 64 },
  ...(MEMO ? { memo: { native_card: MEMO.deck || MEMO.hud ? { deck: MEMO.deck === true, hud: MEMO.hud === true } : null,
    // s1g: the int fast-env refuses a `reduce` key (the E6 SDK reduce memo was removed)
    ...(MEMO.reduce ? { reduce: { check_interval: MEMO.reduce } } : {}),
    receipt_canonical: MEMO.receipt ? { check_interval: MEMO.receipt } : null } } : {}) };
writeFileSync(join(OUT, "config.json"), JSON.stringify(config, null, 1), { mode: 0o600 });
const GC1 = arg("single-threaded-gc", "off") === "on";  // V8 --single-threaded-gc (V3-shen)
const DRIVER_FLAGS = { inference_timeout_ms: INFERENCE_TIMEOUT_MS, reopen_backoff: REOPEN_BACKOFF,
  single_threaded_gc: GC1, reduce_light_interval: Number(arg("reduce-light-interval", "0")), memo: MEMO,
  ascension: ASCENSION, r7: R7, arm: ARM, sampling_classes: SAMPLING.classes, temperature_min: TEMPERATURE_MIN, replay_k: REPLAY_K,
  save_starts: SAVE_STARTS, new_runs_only: NEW_RUNS_ONLY, ...(GREEDY_ROLE ? { save_role: "greedy" } : {}), ...(EVAL_ONLY ? { eval_retries: EVAL_RETRIES } : {}) };
log({ event: "driver_flags", ...DRIVER_FLAGS });
process.stderr.write(`driver_flags ${JSON.stringify(DRIVER_FLAGS)}\n`);

const ELU = arg("elu-preload");
const env = spawn(process.execPath, [...(GC1 ? ["--single-threaded-gc"] : []), ...(ELU ? ["--import", ELU] : []), join(PKG, "components/policy-runtime/dist/fast-env/process.js"), "--config", join(OUT, "config.json")],
  { stdio: ["pipe", "pipe", "pipe"], env: ELU ? { ...process.env, ELU_OUT: join(OUT, "elu.jsonl") } : process.env });
env.stderr.on("data", chunk => appendFileSync(join(OUT, "env.stderr"), chunk));
let commandId = 0, opened = 0, stopping = false, stopSent = false, isReady = false;
const send = (command) => (stopSent && command.cmd !== "stop") ? false : env.stdin.write(`${JSON.stringify({ schema: "sts2.policy-runtime/fast-env-command-1", command_id: `c${++commandId}`, ...command })}\n`);
const r7Counts = { selected: 0, not_selected: 0, failed: {} };
const openWith = (slot, r7) => {
  if (stopping) return;
  let next;
  try { next = nextStart(); } catch (error) { return ticketFailed({ slot, reason: "save_start_failed", detail: String(error?.message ?? error) }); }
  if (next === null) return evalDone();
  const { group: { id, plan, seed }, member } = next, ref = `g${++opened}`;
  live.add(slot);
  games.set(ref, { seed, group: id, member, start: plan.start.kind,
    ...(EVAL_ONLY ? { attempt: attempts.get(seed) ?? 1 } : {}), ...(saves ? saves.opening(ref, plan, member) : {}) });
  send({ cmd: "open", slot, game_ref: ref, seed, initial_progress: initialProgress, start: plan.start, r7, retain_originals: false,
    ...(plan.save_points ? { save_points: plan.save_points } : {}) });
};
// A save-start draw that throws (a pool the driver cannot read or write) stops the driver
// (no backoff, no retry) and exits 3. A refused or unknown save start is sealed by
// tickets.mjs (its family paused) and the slot opens the next start, never the same ticket.
let ticketFailure = null;
const ticketFailed = (detail) => {
  if (ticketFailure) return;
  ticketFailure = detail; summary.ticket_failure = detail;
  log({ event: "ticket_failed", ...detail });
  process.stderr.write(`save start failed, driver stops: ${JSON.stringify(detail)}\n`);
  stopping = true; summary.final_status = true;
  if (!stopSent) { stopSent = true; send({ cmd: "stop" }); }
};
// The R7 choice comes before the game (never from its seed or content). A failed choice
// opens nothing: logged and counted, the slot tries again after 1 s (never a default).
const open = (slot) => {
  if (stopping) return;
  if (!R7) return openWith(slot, false);
  admitCall({ op: "r7", actor: `v3g-${arg("tag")}`, intent_id: "-", seed: "-", game_instance_id: "-" }).then(r => {
    if (r.ok && typeof r.selected === "boolean") {
      r7Counts[r.selected ? "selected" : "not_selected"] += 1;
      return openWith(slot, r.selected);
    }
    r7Counts.failed[r.code ?? "malformed"] = (r7Counts.failed[r.code ?? "malformed"] ?? 0) + 1;
    log({ event: "r7_select_failed", slot, code: r.code ?? null, detail: r.detail ?? null });
    setTimeout(() => open(slot), 1000);
  });
};
const live = new Set();  // eval-only: slots with a game open
const evalDone = () => {
  if (!EVAL_ONLY || live.size || stopping) return;
  log({ event: "eval_seeds_done", seed_stream: seedStream });
  stopping = true; summary.final_status = true;
  if (!stopSent) { stopSent = true; send({ cmd: "stop" }); }
};
const exclude = (gameInstanceId, reason) => admitCall({ op: "exclude", actor: `v3g-${arg("tag")}`, intent_id: "-", seed: "-", game_instance_id: gameInstanceId, reason })
  .then(r => { if (r.ok) admission.excluded += 1; else admission.exclusion_failed[r.code] = (admission.exclusion_failed[r.code] ?? 0) + 1; });
const excludedSent = new Set();
const summary = { tag: arg("tag"), slots: K, admission, ascension: ASCENSION, arm: ARM, replay_k: REPLAY_K, r7: r7Counts, single_choice: SINGLE, warm_s: WARM_S, steady_s: STEADY_S, closed: [], refused: [], failures: [], statuses: [] };
createInterface({ input: env.stdout }).on("line", line => {
  const event = JSON.parse(line);
  saves?.onEvent(event);
  // A game's lines carry its start; its `closed` line is the readout's per-arm/level/group record.
  log({ event: event.event, slot: event.slot ?? null, reason: event.reason ?? null, detail: event.detail ?? null,
    ...(event.game_ref ? gameFacts(event.game_ref) : {}),
    ...(event.event === "closed" ? { arm: ARM, ascension: ASCENSION, replay_k: REPLAY_K, decisions: event.decisions ?? null,
      floor: event.public_facts?.floor ?? null, terminal: event.terminal?.result ?? null, evidence_failed: event.evidence_failed ?? null } : {}) });
  if (event.event === "ready") {
    summary.ready_at = Date.now(); isReady = true;
    if (stopping) { stopSent = true; send({ cmd: "stop" }); return; }  // drained before ready
    for (let slot = 0; slot < K; slot += 1) open(slot);
    for (let at = WARM_S; at < WARM_S + STEADY_S; at += STATUS_S) setTimeout(() => send({ cmd: "status" }), at * 1000);
    setTimeout(() => { summary.final_status = true; send({ cmd: "status" }); }, (WARM_S + STEADY_S) * 1000);
  } else if (event.event === "opened") {
    const tag = arg("tag"), ref = event.game_ref, instance = event.game_instance_id;
    if (EVAL_ONLY) { log({ event: "eval_unattested", game_ref: ref, game_instance_id: instance }); return; }
    admission.sent += 1;
    admitCall({ op: "admit", actor: `v3g-${tag}`, intent_id: `v3g-${tag}-${ref}`, seed: games.get(ref).seed, game_instance_id: instance }).then(r => {
      log({ event: "admission", game_ref: ref, game_instance_id: instance, ok: r.ok, code: r.code ?? null });
      saves?.onAdmission(ref, r.ok);
      if (r.ok) { admission.admitted += 1; return; }
      admission.refused[r.code] = (admission.refused[r.code] ?? 0) + 1;
      admission.closed_after_refusal += 1;
      send({ cmd: "close_slot", slot: event.slot, game_instance_id: instance });
    });
  } else if (event.event === "evidence_failed") {
    summary.failures.push(event);
    if (!excludedSent.has(event.game_instance_id)) { excludedSent.add(event.game_instance_id); exclude(event.game_instance_id, "evidence_failed"); }
  } else if (event.event === "closed") {
    live.delete(event.slot);
    if (event.evidence_failed && !excludedSent.has(event.game_instance_id)) { excludedSent.add(event.game_instance_id); exclude(event.game_instance_id, "evidence_failed"); }
    summary.closed.push({ t: Date.now(), ...gameFacts(event.game_ref), slot: event.slot, reason: event.reason, detail: event.detail, decisions: event.decisions,
      game_instance_id: event.game_instance_id, evidence_failed: event.evidence_failed ?? null,
      native_steps: event.native_steps, floor: event.public_facts?.floor ?? null, terminal: event.terminal?.result ?? null });
    if (event.reason === "not_started") {
      if (event.native_steps === 0 || event.detail === "inference_timeout")
        requeue(games.get(event.game_ref)?.seed, `not_started:${event.detail}`, event.native_steps);
      const n = (notStartedRun.get(event.slot) ?? 0) + 1; notStartedRun.set(event.slot, n);
      const slot = event.slot; setTimeout(() => open(slot), reopenDelayMs(n));
    } else { notStartedRun.delete(event.slot); open(event.slot); }
  } else if (event.event === "open_refused") {
    summary.refused.push(event); live.delete(event.slot);
    if (/^(native_start|reset)_failed:/.test(event.reason)) requeue(games.get(event.game_ref)?.seed, event.reason);
    if (event.reason !== "build_output_stopped") open(event.slot); else evalDone();
  }
  else if (["slot_failed", "build_output_stopped"].includes(event.event)) summary.failures.push(event);
  else if (event.event === "status") {
    summary.statuses.push({ t: Date.now(), counters: event.counters, slots: event.slots });
    // #553 review C: the save-start counters so far on every status (cumulative), so the
    // run record shows the effective share while the run goes, not only at driver exit.
    if (saves) log({ event: "save_counters", ...saves.counters() });
    if (summary.final_status && !stopSent) { stopping = true; stopSent = true; send({ cmd: "stop" }); }
  } else if (event.event === "stopped" || event.event === "fatal") summary.end = event;
});
// s1f: cooperative stop (as the RL4 actor array's USR1): the GPU side is draining, so open
// no new game, take a final status and stop; the fast-env closes its games itself.
process.on("SIGUSR1", () => {
  if (summary.final_status) return;
  summary.final_status = true; summary.drained_at = Date.now(); stopping = true;
  if (isReady) send({ cmd: "status" });
});
const code = await new Promise(resolve => env.on("exit", resolve));
summary.exit = code;
summary.seed_stream = seedStream;
if (saves) { summary.save_starts = saves.counters(); log({ event: "save_counters", ...summary.save_starts }); }
ADMIT.end();
const a = summary.statuses[0], b = summary.statuses[summary.statuses.length - 1];
if (a && b) {
  const ca = a.counters, cb = b.counters, wall = b.t - a.t;
  summary.steady = { wall_ms: wall, t0: a.t, t1: b.t, decisions: cb.decisions - ca.decisions, forced_steps: cb.forced_steps - ca.forced_steps,
    native_steps: cb.native_steps - ca.native_steps, node_cpu_ms: cb.node_cpu_ms - ca.node_cpu_ms,
    link_wait_ms: cb.link_wait_ms - ca.link_wait_ms,
    sync_ms: Object.fromEntries(Object.keys(cb.sync_ms ?? {}).map(key => [key, cb.sync_ms[key] - ca.sync_ms[key]])),
    games_closed_in_window: summary.closed.filter(c => c.t > a.t && c.t <= b.t).length };
}
writeFileSync(join(OUT, "summary.json"), JSON.stringify(summary, null, 1));
process.exit(ticketFailure ? 3 : code === 0 ? 0 : 1);
