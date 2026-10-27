#!/usr/bin/env node
// Execute the existing portable gates; collect independent failures, never qualification.
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawn, execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolveProjectPython } from "./project-python.mjs";

const root = path.resolve(import.meta.dirname, "..");
const owners = [
  ["connector", "components/connector", "check"],
  ["host-runtime", "components/host-runtime", "check"],
  ["annotator", "components/annotator", "test"],
  ["evidence", "components/evidence", "check"],
  ["policy-runtime", "components/policy-runtime", "check"],
  ["workbench", "apps/workbench", "test"],
  ["ingame-ui", "apps/ingame-ui", "check"],
  ["game-mod", "apps/game-mod", "check"],
];

// Must equal python/tools/pytest_shard.py SHARD_COUNT and the CI shard matrices.
export const PYTEST_SHARD_COUNT = 3;
export const PYTEST_SHARD_INDEXES = Object.freeze(Array.from({length: PYTEST_SHARD_COUNT}, (_, index) => index + 1));

export function pytestPartition(value) {
  if (value === undefined) return null;
  const match = /^([1-9])\/([1-9])$/.exec(value ?? "");
  if (!match || Number(match[2]) !== PYTEST_SHARD_COUNT || !PYTEST_SHARD_INDEXES.includes(Number(match[1])))
    throw new Error("invalid_pytest_shard");
  return {index: Number(match[1]), count: PYTEST_SHARD_COUNT, complete: false,
    manifest: "python/.local/pytest-shard.json"};
}

export function workspaceStages(scope, {pytestShard} = {}) {
  if (!["full", "python", "components"].includes(scope)) throw new Error("unknown_workspace_scope");
  const partition = pytestPartition(pytestShard);
  if (partition && scope === "components") throw new Error("pytest_shard_requires_python_scope");
  // Shard 1 owns non-pytest gates. Each isolated runner still builds its own clients.
  const pythonOnly = partition !== null && partition.index !== 1;
  // A seconds-long environment preflight precedes every gate it can invalidate.
  const stages = [{ id: "preflight", args: ["run", "check:preflight", "--", scope], requires: [] }];
  const guards = scope === "components" || pythonOnly ? ["preflight"] : ["preflight", "repository"];
  if (scope !== "components" && !pythonOnly)
    stages.push({ id: "repository", args: ["run", "check:repository"], requires: ["preflight"] });
  stages.push({ id: "shared-client-build", args: ["run", "precheck:s0"], requires: guards });
  if (scope !== "components" && !pythonOnly) stages.push({
    id: "s0-consumers", args: ["run", "check:s0-tests"], requires: ["shared-client-build"],
  });
  // Complete readers of this generation before owner check scripts rebuild the
  // shared dist directories. No consumer can borrow an earlier successful build
  // after a later check has removed or partly replaced its output.
  if (scope !== "components") stages.push({
    id: "python", args: ["run", "check:python", ...(partition ? ["--", "--pytest-shard", pytestShard] : [])],
    requires: ["shared-client-build"],
  });
  if (scope !== "python" && !pythonOnly) {
    for (const [id, owner, script] of owners) stages.push({
      id, args: ["--prefix", owner, "run", script],
      // These consumers import the shared generated SDK/Runtime output. A failed
      // build cannot be replaced by output left by an earlier checkout.
      requires: id === "policy-runtime"
        ? ["shared-client-build"] : guards,
    });
  }
  return stages;
}

// Existing repository validators follow the guarded runner rather than requiring
// one textual shell chain. Only this known entrypoint expands to its fixed plan.
export function scriptIncludesGuard(scripts, name, guard, visited = new Set()) {
  if (visited.has(name) || typeof scripts[name] !== "string") return false;
  if (name === guard) return true;
  visited.add(name);
  const command = scripts[name];
  const references = [...command.matchAll(/\bnpm\s+run\s+([A-Za-z0-9:_-]+)/gu)].map(match => match[1]);
  if (command === "node tools/check-workspace.mjs full") references.push(...workspaceStages("full")
    .filter(stage => stage.args[0] === "run").map(stage => stage.args[1]));
  return references.some(reference => scriptIncludesGuard(scripts, reference, guard, visited));
}

// Same workstation reserve as the owning storage SOP. This observer does not
// reserve blocks or give permission to overlap heavy tests with a live run.
export function workspaceCapacity(directory, { environment = process.env, observe = fs.statfsSync } = {}) {
  const value = environment.SPIREAGENT_MIN_FREE_BYTES;
  if (value !== undefined && (!/^[0-9]{1,19}$/u.test(value) || BigInt(value) > (1n << 63n) - 1n))
    throw new Error("invalid_workspace_reserve");
  const reserve = value === undefined ? 2n * 1024n ** 3n : BigInt(value);
  // Six GiB covers the existing 2.7 GiB checkpoint/input planning envelope plus
  // concurrent build/test scratch. Larger measured work needs an explicit margin.
  const work = 6n * 1024n ** 3n;
  const stat = observe(directory, { bigint: true });
  const available = stat.bavail * stat.bsize;
  return { available_bytes: String(available), reserve_bytes: String(reserve),
    work_bytes: String(work), required_bytes: String(reserve + work),
    sufficient: available >= reserve + work, observation: "not_a_reservation" };
}

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function cancellationFailure(child, phase, signal, exception) {
  const diagnostic = { phase, signal, pid: child.pid, pgid: child.pid,
    uid: process.getuid(), error_code: exception.code ?? null,
    errno: exception.errno ?? null, syscall: exception.syscall ?? null,
    group_state: { status: "unavailable", observation: "post_failure_sample" } };
  // Diagnostics run only after the original failure. Never select another
  // signal target from this snapshot, and never include command arguments.
  const result = spawnSync("/bin/ps", ["-axo", "pid=,ppid=,pgid=,uid=,stat="],
    { encoding: "utf8", timeout: 1000, maxBuffer: 64 * 1024,
      stdio: ["ignore", "pipe", "pipe"] });
  if (result.error || result.status !== 0) {
    diagnostic.group_state.reason = result.error?.code ?? "ps_failed";
    return diagnostic;
  }
  const members = result.stdout.split("\n").flatMap(line => {
    const match = /^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s*$/u.exec(line);
    return match && Number(match[3]) === child.pid ? [{ pid: Number(match[1]),
      ppid: Number(match[2]), pgid: Number(match[3]), uid: Number(match[4]), state: match[5] }] : [];
  });
  diagnostic.group_state = { status: "observed", observation: "post_failure_sample",
    members: members.slice(0, 16), truncated: members.length > 16 };
  return diagnostic;
}

export function ownedCommand(command, args, options = {}) {
  const child = spawn(command, args, { ...options, detached: process.platform !== "win32" });
  let error, cleanup, stop, cleanupDiagnostic;
  const completion = new Promise(resolve => {
    child.on("error", exception => { error = String(exception); });
    child.on("close", async (code, signal) => {
      if (cleanup) await cleanup;
      resolve({ code, signal: stop ?? signal, ...(error ? { error } : {}),
        ...(cleanupDiagnostic ? { cleanup_diagnostic: cleanupDiagnostic } : {}) });
    });
  });
  const cancel = signal => {
    if (cleanup || !child.pid) return cleanup;
    stop = signal;
    cleanup = (async () => {
      if (process.platform === "win32") {
        const result = spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { encoding: "utf8" });
        if (result.status !== 0) error = `owned tree termination failed: ${result.stderr ?? result.error ?? result.status}`;
        return;
      }
      let phase, attemptedSignal;
      const alive = () => {
        phase = "probe"; attemptedSignal = 0;
        try { process.kill(-child.pid, 0); return true; }
        catch (exception) { if (exception.code === "ESRCH") return false; throw exception; }
      };
      const send = value => {
        phase = "send"; attemptedSignal = value;
        try { process.kill(-child.pid, value); }
        catch (exception) { if (exception.code !== "ESRCH") throw exception; }
      };
      try {
        send(signal);
        for (let count = 0; count < 50 && alive(); count++) await delay(20);
        if (alive()) send("SIGKILL");
        for (let count = 0; count < 100 && alive(); count++) await delay(20);
        if (alive()) error = "owned process group termination unresolved";
      } catch (exception) {
        error = String(exception);
        try { cleanupDiagnostic = cancellationFailure(child, phase, attemptedSignal, exception); }
        catch { cleanupDiagnostic = { phase, signal: attemptedSignal, pid: child.pid,
          pgid: child.pid, uid: process.getuid(), error_code: exception.code ?? null,
          group_state: { status: "unavailable", reason: "diagnostic_capture_failed" } }; }
      }
    })();
    return cleanup;
  };
  return { child, completion, cancel };
}

export async function runStages(stages, invoke, { cancelled = () => false, record = () => {} } = {}) {
  const results = [];
  for (const stage of stages) {
    const blockedBy = stage.requires.filter(id => !results.some(result => result.id === id && result.status === "passed"));
    if (cancelled() || results.some(result => result.status === "cancelled")) blockedBy.push("interrupted");
    if (blockedBy.length) {
      const result = { id: stage.id, args: stage.args, status: "blocked", blocked_by: blockedBy };
      results.push(result); record(result); continue;
    }
    const started = performance.now();
    record({ id: stage.id, args: stage.args, status: "running" });
    let outcome;
    try { outcome = await invoke(stage); }
    catch (error) { outcome = { code: null, error: String(error) }; }
    const result = { id: stage.id, args: stage.args,
      status: cancelled() || outcome.signal ? "cancelled" : outcome.code === 0 && !outcome.error ? "passed" : "failed",
      exit_code: outcome.code ?? null, signal: outcome.signal ?? null,
      ...(outcome.error ? { error: outcome.error } : {}),
      ...(outcome.cleanup_diagnostic ? { cleanup_diagnostic: outcome.cleanup_diagnostic } : {}),
      seconds: Math.round((performance.now() - started) / 10) / 100 };
    results.push(result); record(result);
  }
  return { results, passed: results.length > 0 && results.every(result => result.status === "passed") };
}

function sourceIdentity() {
  const git = (...args) => execFileSync("git", args, { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  return { head: git("rev-parse", "HEAD"), tree: git("rev-parse", "HEAD^{tree}"),
    dirty: git("status", "--porcelain", "--untracked-files=normal").length > 0 };
}

export function sourceAccepted(before, after, scope) {
  return before.head === after.head && before.tree === after.tree &&
    (scope === "components" || (!before.dirty && !after.dirty));
}

export async function runWorkspaceChecks(scope, pytestShard) {
  const stages = workspaceStages(scope, {pytestShard});
  const capacity = [workspaceCapacity(root), workspaceCapacity(os.tmpdir())];
  if (capacity.some(item => !item.sufficient)) {
    process.stdout.write(JSON.stringify({ stage: "workspace_capacity", status: "blocked", filesystems: capacity }) + "\n");
    process.exitCode = 1;
    return;
  }
  const before = sourceIdentity();
  // Every child, including nested test processes, sees the same project Python.
  // An unresolved interpreter leaves PYTHON untouched; the preflight stage fails.
  let python = null;
  try { python = resolveProjectPython(); } catch { /* reported by the preflight stage */ }
  const environment = python ? { ...process.env, PYTHON: python.executable } : process.env;
  const directory = path.join(root, ".local", "checks");
  fs.mkdirSync(directory, { recursive: true });
  const output = path.join(directory, `workspace-${scope}-${process.platform}.json`);
  let active, interrupted;
  const interrupt = signal => { interrupted = signal; active?.cancel(signal); };
  const onInt = () => interrupt("SIGINT"), onTerm = () => interrupt("SIGTERM");
  process.on("SIGINT", onInt); process.on("SIGTERM", onTerm);
  const report = { schema: "spireagent/portable-stage-results-1", scope,
    pytest_partition: pytestPartition(pytestShard),
    started_at: new Date().toISOString(), platform: process.platform, architecture: process.arch,
    node: process.version, python: python && { executable: python.executable, version: python.version,
      source: python.source }, source_at_start: before, capacity_checks: [...capacity], results: [], verdict: "running",
    non_claims: ["installed", "loaded", "native", "Human", "learning", "qualification"] };
  const save = () => {
    fs.writeFileSync(output + ".tmp", JSON.stringify(report, null, 2) + "\n");
    fs.renameSync(output + ".tmp", output);
  };
  save();
  try {
    const outcome = await runStages(stages, async stage => {
      const available = [workspaceCapacity(root), workspaceCapacity(os.tmpdir())];
      report.capacity_checks.push(...available.map(item => ({ stage: stage.id, ...item }))); save();
      if (available.some(item => !item.sufficient)) return { code: 1, error: "workspace_capacity_insufficient" };
      active = ownedCommand(process.platform === "win32" ? "npm.cmd" : "npm", stage.args,
        { cwd: root, env: environment, stdio: "inherit", shell: process.platform === "win32" });
      const result = await active.completion; active = null; return result;
    }, { cancelled: () => Boolean(interrupted), record: result => {
      const existing = report.results.findIndex(item => item.id === result.id);
      if (existing < 0) report.results.push(result); else report.results[existing] = result;
      process.stdout.write(JSON.stringify({ stage: "workspace_gate", ...result }) + "\n"); save();
    } });
    const after = sourceIdentity();
    report.source_at_end = after;
    report.source_accepted = sourceAccepted(before, after, scope);
    report.verdict = outcome.passed && report.source_accepted ? "passed" : "failed";
    report.interrupted_signal = interrupted ?? null;
    report.ended_at = new Date().toISOString(); save();
    process.stdout.write(JSON.stringify({ stage: "workspace_summary", verdict: report.verdict,
      pytest_partition: report.pytest_partition, output }) + "\n");
    if (process.env.GITHUB_STEP_SUMMARY) {
      fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY,
        `\nPortable ${scope}${report.pytest_partition ? ` (partial pytest shard ${pytestShard})` : ""}, checkout ${before.head}: **${report.verdict}**\n\n` +
        "| Gate | Result | Seconds |\n|---|---|---|\n" +
        report.results.map(item => `| ${item.id} | ${item.status}${item.blocked_by ? ": " + item.blocked_by.join(", ") : ""} | ${item.seconds ?? ""} |`).join("\n") + "\n");
    }
    if (report.verdict !== "passed") process.exitCode = 1;
  } finally {
    process.removeListener("SIGINT", onInt); process.removeListener("SIGTERM", onTerm);
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [scope = "full", ...args] = process.argv.slice(2);
  if (args.length && (args.length !== 2 || args[0] !== "--pytest-shard")) {
    throw new Error("unknown_workspace_arguments");
  }
  await runWorkspaceChecks(scope, args[1]);
}
