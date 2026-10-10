#!/usr/bin/env node
// Execute the existing portable gates; collect independent failures, never qualification.
import fs from "node:fs";
import path from "node:path";
import { spawn, execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

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

export function workspaceStages(scope) {
  if (!["full", "python", "components"].includes(scope)) throw new Error("unknown_workspace_scope");
  const guards = scope === "components" ? [] : ["repository"];
  const stages = scope === "components" ? [] : [
    { id: "repository", args: ["run", "check:repository"], requires: [] },
  ];
  stages.push({ id: "shared-client-build", args: ["run", "precheck:s0"], requires: guards });
  if (scope !== "components") stages.push({
    id: "s0-consumers", args: ["run", "check:s0-tests"], requires: ["shared-client-build"],
  });
  // Complete readers of this generation before owner check scripts rebuild the
  // shared dist directories. No consumer can borrow an earlier successful build
  // after a later check has removed or partly replaced its output.
  if (scope !== "components") stages.push({
    id: "python", args: ["run", "check:python"], requires: ["shared-client-build"],
  });
  if (scope !== "python") {
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

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

export function ownedCommand(command, args, options = {}) {
  const child = spawn(command, args, { ...options, detached: process.platform !== "win32" });
  let error, cleanup, stop;
  const completion = new Promise(resolve => {
    child.on("error", exception => { error = String(exception); });
    child.on("close", async (code, signal) => {
      if (cleanup) await cleanup;
      resolve({ code, signal: stop ?? signal, ...(error ? { error } : {}) });
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
      const alive = () => {
        try { process.kill(-child.pid, 0); return true; }
        catch (exception) { if (exception.code === "ESRCH") return false; throw exception; }
      };
      const send = value => {
        try { process.kill(-child.pid, value); }
        catch (exception) { if (exception.code !== "ESRCH") throw exception; }
      };
      try {
        send(signal);
        for (let count = 0; count < 50 && alive(); count++) await delay(20);
        if (alive()) send("SIGKILL");
        for (let count = 0; count < 100 && alive(); count++) await delay(20);
        if (alive()) error = "owned process group termination unresolved";
      } catch (exception) { error = String(exception); }
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

async function main(scope) {
  const stages = workspaceStages(scope);
  const before = sourceIdentity();
  const directory = path.join(root, ".local", "checks");
  fs.mkdirSync(directory, { recursive: true });
  const output = path.join(directory, `workspace-${scope}-${process.platform}.json`);
  let active, interrupted;
  const interrupt = signal => { interrupted = signal; active?.cancel(signal); };
  const onInt = () => interrupt("SIGINT"), onTerm = () => interrupt("SIGTERM");
  process.on("SIGINT", onInt); process.on("SIGTERM", onTerm);
  const report = { schema: "spireagent/portable-stage-results-1", scope,
    started_at: new Date().toISOString(), platform: process.platform, architecture: process.arch,
    node: process.version, source_at_start: before, results: [], verdict: "running",
    non_claims: ["installed", "loaded", "native", "Human", "learning", "qualification"] };
  const save = () => {
    fs.writeFileSync(output + ".tmp", JSON.stringify(report, null, 2) + "\n");
    fs.renameSync(output + ".tmp", output);
  };
  save();
  try {
    const outcome = await runStages(stages, async stage => {
      active = ownedCommand(process.platform === "win32" ? "npm.cmd" : "npm", stage.args,
        { cwd: root, stdio: "inherit", shell: process.platform === "win32" });
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
    process.stdout.write(JSON.stringify({ stage: "workspace_summary", verdict: report.verdict, output }) + "\n");
    if (process.env.GITHUB_STEP_SUMMARY) {
      fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY,
        `\nPortable ${scope}, checkout ${before.head}: **${report.verdict}**\n\n` +
        "| Gate | Result | Seconds |\n|---|---|---|\n" +
        report.results.map(item => `| ${item.id} | ${item.status}${item.blocked_by ? ": " + item.blocked_by.join(", ") : ""} | ${item.seconds ?? ""} |`).join("\n") + "\n");
    }
    if (report.verdict !== "passed") process.exitCode = 1;
  } finally {
    process.removeListener("SIGINT", onInt); process.removeListener("SIGTERM", onTerm);
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main(process.argv[2] ?? "full");
