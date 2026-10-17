#!/usr/bin/env node
// Run a P1 Agent over a seed suite on a real Host (Managed service or Reference game) through the existing Policy Runtime.
// node tools/p1-suite.mjs [--worktree W (default: this repository)] [--host managed --candidate C [--character Defect --language en|zh] | --host reference --local-root DIR [--template T]]
//   --seeds S1,S2|--suite FILE [--limit N] --out DIR --agent "cmd args"
import { spawn, execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, existsSync, chmodSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import net from "node:net";

const opt = (n, d = null) => { const i = process.argv.indexOf(n); return i >= 0 ? process.argv[i + 1] : d; };
const worktree = resolve(opt("--worktree", join(dirname(fileURLToPath(import.meta.url)), ".."))), candidate = opt("--candidate") ? resolve(opt("--candidate")) : null, outDir = resolve(opt("--out"));
const agentCmd = opt("--agent").split(" ").filter(Boolean);
let seeds = opt("--seeds") ? opt("--seeds").split(",") : JSON.parse(readFileSync(opt("--suite"), "utf8")).seeds;
seeds = seeds.slice(0, Number(opt("--limit", String(seeds.length))));
const sha = (b) => createHash("sha256").update(b).digest("hex");
mkdirSync(outDir, { recursive: true });
const priv = join(outDir, "private"); mkdirSync(priv, { recursive: true, mode: 0o700 }); chmodSync(priv, 0o700);
const clientAtt = join(priv, "client.json"), managerAtt = join(priv, "manager.json");
const freePort = () => new Promise((ok) => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => ok(p)); }); });
const bridge = join(worktree, "components/policy-runtime/examples/p1/session-bridge.mjs");
const dist = join(worktree, "components/policy-runtime/dist");
const { ManagedServicePolicyClient } = await import(pathToFileURL(join(dist, "managed.js")));
const { createGlobalProgramManifest } = await import(pathToFileURL(join(dist, "global-program.js")));

const host = opt("--host", "managed");
const rows = [];
if (host === "reference") { await runReference(); process.exit(0); }
let svc = null, svcErr = "", svcGeneration = 0;
function startService() {
  svcGeneration += 1;
  for (const f of [clientAtt, managerAtt]) { try { if (existsSync(f)) execFileSync("rm", ["-f", f]); } catch { /* own files */ } }
  svc = spawn(process.execPath, [join(worktree, "components/host-runtime/tools/managed-host-service.mjs"),
    "--candidate", candidate, "--client-attachment", clientAtt, "--manager-attachment", managerAtt,
    "--character", opt("--character", "Defect"), "--language", opt("--language", "en")], { stdio: ["ignore", "pipe", "pipe"] });
  svc.stderr.on("data", (d) => { svcErr += d; if (svcErr.length > 2e6) svcErr = svcErr.slice(-1e6); });
}
startService();
const until = async (pred, ms, what) => { const end = Date.now() + ms; while (Date.now() < end) { if (pred()) return; await new Promise((r) => setTimeout(r, 200)); } throw new Error(`timeout ${what}`); };
async function runRuntime(dir, manifestPath, hostArgs, started) {
      const port = await freePort();
      const args = [join(worktree, "components/policy-runtime/bin/policy-runtime.mjs"), "--manifest", manifestPath, "--adapter-command", process.execPath,
        `--adapter-arg=${bridge}`, `--adapter-arg=${manifestPath}`, ...agentCmd.map((v) => `--adapter-arg=${v}`), "--mode", "auto", "--listen-port", String(port),
        "--evidence-root", join(dir, "evidence"), "--max-auto-submissions", "4000", "--max-policy-calls", "8000", "--auto-deadline-ms", "1200000",
        ...hostArgs];
      const rt = spawn(process.execPath, args, { cwd: worktree, stdio: ["ignore", "pipe", "pipe"] });
      let err = ""; rt.stderr.on("data", (d) => { err += d; });
      // Watch the run's events: the Runtime keeps serving after handoff/terminal; stop it once it has stopped driving.
      const runDir = () => { const e = join(dir, "evidence"); const r = existsSync(e) ? readdirSync(e).filter((n) => n.startsWith("run-")) : []; return r.length ? join(e, r[0]) : null; };
      let ended = null;
      const exited = new Promise((ok) => rt.on("exit", ok));
      while (ended === null) {
        await Promise.race([exited, new Promise((r) => setTimeout(r, 1000))]);
        const rdir = runDir(); const events = rdir && existsSync(join(rdir, "events.jsonl")) ? readFileSync(join(rdir, "events.jsonl"), "utf8") : "";
        if (rt.exitCode !== null) ended = "exited";
        else if (/"kind":"(stopped|handoff_to_human|fail_closed|runtime_tainted)"/.test(events)) ended = "stopped";
        else if (Date.now() - started > 1300000) ended = "timeout";
      }
      if (rt.exitCode === null) { rt.kill("SIGTERM"); await exited; }
      const rdir = runDir(); const row = {}; row.run_dir = rdir;
      const events = readFileSync(join(rdir, "events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
      const kinds = {}; for (const e of events) kinds[e.kind] = (kinds[e.kind] ?? 0) + 1;
      const obs = events.filter((e) => e.kind === "global_observation"); const last = obs.at(-1)?.payload ?? {};
      row.decisions = kinds.global_choice ?? 0; row.dispatches = kinds.global_dispatch ?? 0;
      row.terminal = last.status === "terminal"; row.end = ended;
      row.taint = events.find((e) => e.kind === "runtime_tainted")?.payload.reason ?? null;
      row.handoff = !!kinds.handoff_to_human; row.last_missing = last.missing ?? [];
      try {
        const srcs = events.filter((e) => e.kind === "global_source" && e.payload.artifact).slice(-1);
        for (const s of srcs) { const d = JSON.parse(readFileSync(join(rdir, s.payload.artifact.path), "utf8"));
          const b = JSON.parse(d.serialized_observation); if (b.capture_status !== "complete")
            row.host_missing = { kind: b.snapshot.interaction.kind, missing: b.snapshot.completeness?.missing ?? b.missing }; }
      } catch { /* diagnostic only */ }
      row.unknown = events.some((e) => e.kind === "global_result" && e.payload.result?.status === "unknown");
      const results = events.filter((e) => e.kind === "global_result").map((e) => e.payload.result?.status); row.result_status = {};
      for (const s of results) row.result_status[s] = (row.result_status[s] ?? 0) + 1;
      try { row.replay = JSON.parse(execFileSync(process.execPath, [join(worktree, "components/policy-runtime/bin/sts2-p1.mjs"), "replay", "--run-directory", rdir], { encoding: "utf8", maxBuffer: 64e6 })).verdict; }
      catch (e) { row.replay = "error:" + String(e.stdout ?? e.message).slice(0, 200); }
      if (!row.terminal) row.stderr_tail = err.slice(-400);
      return row;
}

try {
  let manager, client, ep;
  const attach = async () => { await until(() => existsSync(clientAtt) && existsSync(managerAtt), 120000, "attachments");
    manager = JSON.parse(readFileSync(managerAtt, "utf8")); client = JSON.parse(readFileSync(clientAtt, "utf8")); ep = client.endpoint.replace(/\/$/, ""); };
  await attach();
  const ready = async () => (await fetch(ep + "/v1/ready", { headers: { authorization: `Bearer ${client.token}` } })).json();
  // An unknown delivery fences the Managed process (never retried); a suite continues on a fresh process.
  const freshService = async () => { svc.kill("SIGTERM"); await new Promise((r) => setTimeout(r, 1500)); startService(); await attach(); };
  for (const [index, seed] of seeds.entries()) {
    const episode = `managed-${String(index + 1).padStart(4, "0")}-${seed}`, dir = join(outDir, episode); mkdirSync(dir, { recursive: true });
    const started = Date.now(); const row = { episode, seed };
    try {
      let r0 = await ready();
      if (r0.episode?.tainted || r0.episode?.closed || index > 0 && rows.at(-1)?.unknown) { await freshService(); r0 = await ready(); row.service_generation = svcGeneration; }
      const reset = await fetch(ep + "/v1/admin/reset", { method: "POST", headers: { authorization: `Bearer ${manager.token}`, "content-type": "application/json",
        "x-sts2-managed-service-id": manager.service_instance_id }, body: JSON.stringify({ request_id: "reset-" + randomUUID(), seed,
        expected_runtime_instance_id: r0.adapter_runtime_instance_id, expected_game_continuity_id: r0.episode?.game_continuity_id ?? null }) });
      if (reset.status !== 200) throw new Error("reset_" + reset.status + ":" + (await reset.text()).slice(0, 300));
      const rd = await ready(); const h = rd.host_identity;
      const binding = { schema: "sts2.policy-runtime/managed-environment-binding-1", profile_sha256: sha("p1-suite"), input_profile: "public-basis-1",
        host_package_identity: { package: h.package_name, version: h.version, source_revision: h.source_revision, component_tree_revision: h.component_tree_revision,
          release_asset_sha256: "0".repeat(64), package_content_sha256: "0".repeat(64) },
        candidate_build: Object.fromEntries(["upstream_revision", "source_patch_sha256", "artifact_sha256", "artifact_mvid", "original_sts2_sha256", "runtime_sts2_sha256"].map((k) => [k, rd.candidate_build[k]])) };
      const bindingPath = join(priv, `binding-${index}.json`); writeFileSync(bindingPath, JSON.stringify(binding), { mode: 0o600 });
      const target = { serviceInstanceId: client.service_instance_id, runtimeInstanceId: rd.adapter_runtime_instance_id, gameContinuityId: rd.episode.game_continuity_id };
      const artifactPath = join(dir, "program.json"); writeFileSync(artifactPath, JSON.stringify({ program: "p1-suite", agent: agentCmd }));
      const t = await ManagedServicePolicyClient.attachGlobal(bindingPath, clientAtt, target, "run-" + randomUUID());
      const identity = await t.negotiate(AbortSignal.timeout(10000));
      const manifest = createGlobalProgramManifest({ episodeId: episode, manifestId: episode, identity, artifactPath, artifactBytes: readFileSync(artifactPath),
        adapter: { id: "p1-session-bridge", version: "1", protocol: "sts2.policy-runtime/agent-session-ndjson-1", code_sha256: sha(readFileSync(bridge)) } });
      const manifestPath = join(dir, "agent.json"); writeFileSync(manifestPath, JSON.stringify(manifest));
      Object.assign(row, await runRuntime(dir, manifestPath, ["--managed-binding", bindingPath, "--managed-attachment", clientAtt, "--managed-expected-service-instance-id", target.serviceInstanceId, "--managed-expected-runtime-instance-id", target.runtimeInstanceId, "--managed-expected-game-continuity-id", target.gameContinuityId], started));
    } catch (error) { row.error = String(error.message).slice(0, 400); }
    row.seconds = Math.round((Date.now() - started) / 1000);
    rows.push(row); console.log(JSON.stringify(row));
    writeFileSync(join(outDir, "report.json"), JSON.stringify({ worktree, candidate, agent: agentCmd, seeds, rows }, null, 1));
  }
} finally { svc?.kill("SIGTERM"); writeFileSync(join(outDir, "service.stderr"), svcErr); }
function writeSummary() {
const summary = { planned: seeds.length, terminal: rows.filter((r) => r.terminal).length, handoff: rows.filter((r) => r.handoff).length,
  taint: rows.filter((r) => r.taint).length, unknown: rows.filter((r) => r.unknown).length, errors: rows.filter((r) => r.error).length,
  replay_verified: rows.filter((r) => r.replay === "verified").length, decisions: rows.reduce((a, r) => a + (r.decisions ?? 0), 0) };
writeFileSync(join(outDir, "report.json"), JSON.stringify({ worktree, host, agent: agentCmd, seeds, summary, rows }, null, 1));
console.log(JSON.stringify({ summary }));
}
writeSummary();


// ---------- Reference (real game + Mod): driver owns lifecycle/seed, Runtime drives the Agent ----------
async function runReference() {
  const { ReferenceGlobalTransport } = await import(pathToFileURL(join(dist, "global-transport.js")));
  const sdk = await import(pathToFileURL(join(worktree, "node_modules/@rsgcsg/sts2-connector-client/dist/index.js")));
  const endpoint = opt("--endpoint", "http://127.0.0.1:15526");
  const driverArgs = [join(worktree, "components/host-runtime/tools/reference-pe-driver.mjs"), "--local-root", resolve(opt("--local-root")),
    "--template", opt("--template", "defect-a0-s0"), "--endpoint", endpoint, "--character", "DEFECT", "--native-speed", "instant",
    "--display-mode", opt("--display-mode", "headless"), "--experimental-connector"];
  // One driver per episode: after release_bootstrap the Runtime owns the Host, and the
  // driver refuses a second reset (reference_bootstrap_transferred).
  const startDriver = () => {
    const driver = spawn(process.execPath, driverArgs, { stdio: ["pipe", "pipe", "pipe"] });
    let dErr = ""; driver.stderr.on("data", (d) => { dErr += d; if (dErr.length > 2e6) dErr = dErr.slice(-1e6); });
    const lines = []; let buf = ""; driver.stdout.on("data", (d) => { buf += d; let i; while ((i = buf.indexOf("\n")) >= 0) { lines.push(buf.slice(0, i)); buf = buf.slice(i + 1); } });
    const next = async (ms) => { const end = Date.now() + ms; while (Date.now() < end) { if (lines.length) return JSON.parse(lines.shift()); await new Promise((r) => setTimeout(r, 100)); } throw new Error("driver_timeout " + dErr.slice(-500)); };
    let counter = 0; const call = async (command, extra = {}, ms = 300000) => {
      const request_id = `suite-${++counter}`; driver.stdin.write(JSON.stringify({ command, request_id, ...extra }) + "\n");
      const r = await next(ms); if (r.type === "error") throw new Error(`${command}:${r.code}:${r.message}`); return r; };
    const close = async (dir) => {
      try { await call("close", {}, 120000); } catch { /* best effort */ }
      driver.kill("SIGTERM"); writeFileSync(join(dir, "driver.stderr"), dErr);
    };
    return { next, call, close };
  };
  for (const [index, seed] of seeds.entries()) {
    const episode = `reference-${String(index + 1).padStart(4, "0")}-${seed}`, dir = join(outDir, episode); mkdirSync(dir, { recursive: true });
    const started = Date.now(); const row = { episode, seed };
    const { next, call, close } = startDriver();
    try {
      const ready = await next(300000); if (ready.type !== "ready") throw new Error("driver_not_ready " + JSON.stringify(ready).slice(0, 300));
      await call("reset", { seed }, 600000);
      const identity = (await call("episode_identity")).identity;
      const prov = identity.episode_provenance ?? {};
      row.provenance = { verdict: prov.verdict ?? null, actual_seed: prov.actual_seed ?? null };
      await call("release_bootstrap", { expected_runtime_instance_id: identity.host.runtime_instance_id });
      const artifactPath = join(dir, "program.json"); writeFileSync(artifactPath, JSON.stringify({ program: "p1-suite", agent: agentCmd }));
      const t = new ReferenceGlobalTransport(new sdk.PlayerEnvironmentRestClient(endpoint, 5000), { productVersion: "p1-suite", clientInstanceId: "suite-" + randomUUID() });
      const gid = await t.negotiate(AbortSignal.timeout(15000)); await t.close().catch(() => {});
      const manifest = createGlobalProgramManifest({ episodeId: episode, manifestId: episode, identity: gid, artifactPath, artifactBytes: readFileSync(artifactPath),
        adapter: { id: "p1-session-bridge", version: "1", protocol: "sts2.policy-runtime/agent-session-ndjson-1", code_sha256: sha(readFileSync(bridge)) } });
      const manifestPath = join(dir, "agent.json"); writeFileSync(manifestPath, JSON.stringify(manifest));
      Object.assign(row, await runRuntime(dir, manifestPath, ["--connector-endpoint", endpoint], started));
    } catch (error) { row.error = String(error.message).slice(0, 400); }
    finally { await close(dir); }
    row.seconds = Math.round((Date.now() - started) / 1000); rows.push(row); console.log(JSON.stringify(row));
    writeFileSync(join(outDir, "report.json"), JSON.stringify({ worktree, host, agent: agentCmd, seeds, rows }, null, 1));
  }
  writeSummary();
}
