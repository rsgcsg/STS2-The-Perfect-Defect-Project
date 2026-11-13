// Fast-chain env driver (port of the private fc-driver-c3b.mjs; launched by env.sh): one
// K-game fast-env process, real Managed natives, decision link on the node's aggregator ->
// inference service -> assembler -> learner. Learner admission per checked `opened` via the
// node's admission sidecar (a refused or failed admission closes that game with close_slot,
// never resent); a game whose evidence failed is excluded. Status at warm end, then every
// --status-s until warm+steady, then stop. Never retries a game; the slot that closes opens
// the next seed. Every setting is a flag set from the run profile (env.sh).
import { createConnection } from "node:net";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdirSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const arg = (name, fallback) => { const i = process.argv.indexOf(`--${name}`); return i >= 0 ? process.argv[i + 1] : fallback; };
const PKG = arg("pkg"), OUT = arg("out"), K = Number(arg("slots", "8"));
// #530 S-1: the level every game of this driver plays; required, never a default.
const ASCENSION_RAW = arg("ascension");
if (!/^(10|[0-9])$/.test(ASCENSION_RAW ?? "")) {
  process.stderr.write(`--ascension is required: an integer 0-10 (got ${ASCENSION_RAW})\n`); process.exit(2);
}
const ASCENSION = Number(ASCENSION_RAW);
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
const seedOf = new Map(), admission = { sent: 0, admitted: 0, refused: {}, closed_after_refusal: 0, excluded: 0, exclusion_failed: {} };
const SEEDS = readFileSync(arg("seeds"), "utf8").trim().split("\n");
let seedIndex = Number(arg("seed-offset", "0"));
const initialProgress = JSON.parse(readFileSync(arg("initial-progress"), "utf8"));
mkdirSync(OUT, { recursive: true, mode: 0o700 });
const log = (record) => appendFileSync(join(OUT, "driver.jsonl"), `${JSON.stringify({ t: Date.now(), ...record })}\n`);

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
  inference: { timeout_ms: INFERENCE_TIMEOUT_MS, actor_id: `v3g-${arg("tag")}`, single_choice: SINGLE,
    sampling: { mode: "temperature_uniform", seed: `wp8-${arg("tag")}`, arm_id: "control",
      classes: { combat: { temperature: 1, uniform_mix: 0 }, noncombat: { temperature: 1, uniform_mix: 0 } } } },
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
  ascension: ASCENSION, r7: R7 };
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
  const seed = SEEDS[seedIndex++ % SEEDS.length];
  opened += 1;
  seedOf.set(`g${opened}`, seed);
  send({ cmd: "open", slot, game_ref: `g${opened}`, seed, initial_progress: initialProgress, start: { kind: "new_run" }, r7, retain_originals: false });
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
const exclude = (gameInstanceId, reason) => admitCall({ op: "exclude", actor: `v3g-${arg("tag")}`, intent_id: "-", seed: "-", game_instance_id: gameInstanceId, reason })
  .then(r => { if (r.ok) admission.excluded += 1; else admission.exclusion_failed[r.code] = (admission.exclusion_failed[r.code] ?? 0) + 1; });
const excludedSent = new Set();
const summary = { tag: arg("tag"), slots: K, admission, ascension: ASCENSION, r7: r7Counts, single_choice: SINGLE, warm_s: WARM_S, steady_s: STEADY_S, closed: [], refused: [], failures: [], statuses: [] };
createInterface({ input: env.stdout }).on("line", line => {
  const event = JSON.parse(line);
  log({ event: event.event, slot: event.slot ?? null, reason: event.reason ?? null, detail: event.detail ?? null });
  if (event.event === "ready") {
    summary.ready_at = Date.now(); isReady = true;
    if (stopping) { stopSent = true; send({ cmd: "stop" }); return; }  // drained before ready
    for (let slot = 0; slot < K; slot += 1) open(slot);
    for (let at = WARM_S; at < WARM_S + STEADY_S; at += STATUS_S) setTimeout(() => send({ cmd: "status" }), at * 1000);
    setTimeout(() => { summary.final_status = true; send({ cmd: "status" }); }, (WARM_S + STEADY_S) * 1000);
  } else if (event.event === "opened") {
    const tag = arg("tag"), ref = event.game_ref, instance = event.game_instance_id;
    admission.sent += 1;
    admitCall({ op: "admit", actor: `v3g-${tag}`, intent_id: `v3g-${tag}-${ref}`, seed: seedOf.get(ref), game_instance_id: instance }).then(r => {
      log({ event: "admission", game_ref: ref, game_instance_id: instance, ok: r.ok, code: r.code ?? null });
      if (r.ok) { admission.admitted += 1; return; }
      admission.refused[r.code] = (admission.refused[r.code] ?? 0) + 1;
      admission.closed_after_refusal += 1;
      send({ cmd: "close_slot", slot: event.slot, game_instance_id: instance });
    });
  } else if (event.event === "evidence_failed") {
    summary.failures.push(event);
    if (!excludedSent.has(event.game_instance_id)) { excludedSent.add(event.game_instance_id); exclude(event.game_instance_id, "evidence_failed"); }
  } else if (event.event === "closed") {
    if (event.evidence_failed && !excludedSent.has(event.game_instance_id)) { excludedSent.add(event.game_instance_id); exclude(event.game_instance_id, "evidence_failed"); }
    summary.closed.push({ t: Date.now(), slot: event.slot, reason: event.reason, detail: event.detail, decisions: event.decisions,
      game_instance_id: event.game_instance_id, evidence_failed: event.evidence_failed ?? null,
      native_steps: event.native_steps, floor: event.public_facts?.floor ?? null, terminal: event.terminal?.result ?? null });
    if (event.reason === "not_started") {
      const n = (notStartedRun.get(event.slot) ?? 0) + 1; notStartedRun.set(event.slot, n);
      const slot = event.slot; setTimeout(() => open(slot), reopenDelayMs(n));
    } else { notStartedRun.delete(event.slot); open(event.slot); }
  } else if (event.event === "open_refused") { summary.refused.push(event); if (event.reason !== "build_output_stopped") open(event.slot); }
  else if (["slot_failed", "build_output_stopped"].includes(event.event)) summary.failures.push(event);
  else if (event.event === "status") {
    summary.statuses.push({ t: Date.now(), counters: event.counters, slots: event.slots });
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
process.exit(code === 0 ? 0 : 1);
