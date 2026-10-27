#!/usr/bin/env node
// One screen of CI failure causes: failed jobs, failed test names and the first
// error line, read once from the original job logs. Read-only; never reruns.
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const FAILED = new Set(["failure", "cancelled", "timed_out", "startup_failure"]);
const PER_JOB = 8, WIDTH = 220;

// `gh api` refuses raw logs containing terminal escapes unless allowed.
function gh(args) {
  return execFileSync("gh", args, { encoding: "utf8", maxBuffer: 256 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] });
}
const api = path => JSON.parse(gh(["api", path]));
const clip = text => (text.length > WIDTH ? `${text.slice(0, WIDTH - 1)}…` : text);

export function cleanLog(raw) {
  return raw.split(/\r?\n/u).map(line => line
    .replace(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ?/u, "")
    // eslint-disable-next-line no-control-regex
    .replace(/\u001b\[[0-9;?]*[A-Za-z]/gu, ""));
}

// Returns the failing test identities and the first explanatory line for each.
export function logFailures(raw) {
  const lines = cleanLog(raw), failures = [], seen = new Set();
  const add = (name, detail = "") => {
    const key = name.trim();
    if (!key || seen.has(key)) return;
    seen.add(key); failures.push({ name: key, detail: detail.trim() });
  };
  const nextError = start => {
    for (let index = start + 1; index < Math.min(lines.length, start + 40); index++) {
      const line = lines[index].trim();
      if (/^(?:[A-Za-z_.]*(?:Error|Exception)\b|AssertionError|error:\s*(?!\|)|Expected|expected .* to |✗|×)/u.test(line)
        || /^E\s{2,}\S/u.test(line)) return line.replace(/^E\s+/u, "");
    }
    return "";
  };
  // A node:test file that fails to load reports only "test failed"; the child's
  // own error and location were echoed as "# " comments just before it.
  const loadError = start => {
    let error = "", location = "";
    for (let index = start - 1; index >= Math.max(0, start - 30); index--) {
      const line = lines[index].trim();
      if (/^(?:not )?ok \d+ /u.test(line)) break;
      error ||= /^# (\w*Error: .*)$/u.exec(line)?.[1] ?? "";
      if (error) location ||= /^# (?:file:\/\/)?\S*?([^/\s]+:\d+)$/u.exec(line)?.[1] ?? "";
    }
    return error && location ? `${error} at ${location}` : error;
  };
  // A failed stage without its own test summary (lint, type, docs, contract)
  // shows the first cause-shaped lines it printed since the previous stage.
  const cause = (from, to) => lines.slice(from, to).filter(line =>
    /^[A-Z]{1,3}\d{3,4} |: error: |check failed|failed \(\d+\)|^- .*(?:escapes|missing|invalid|mismatch)|^Error: |^\w*Error: /u
      .test(line.trim())).slice(0, 3).map(line => line.trim()).join(" | ");
  let stageStart = 0, failuresAtStage = 0;
  lines.forEach((line, index) => {
    let match;
    if ((match = /^(?:FAILED|ERROR) (\S+::\S+)(?: - (.*))?$/u.exec(line))) add(match[1], match[2] ?? "");
    else if ((match = /^\s*(?:FAIL|×)\s+(\S+\.(?:test|spec)\.[cm]?[jt]sx?(?: > .+)?)$/u.exec(line))) add(match[1], nextError(index));
    else if ((match = /^not ok \d+ - (.+)$/u.exec(line))) {
      const detail = nextError(index);
      add(match[1], /^error: 'test failed'$/u.test(detail) ? loadError(index) || detail : detail);
    }
    else if ((match = /^\{"stage":"workspace_gate".*"status":"(failed|blocked|cancelled)"/u.exec(line))) {
      const stage = JSON.parse(line);
      const own = failures.length === failuresAtStage ? cause(stageStart, index) : "";
      add(`stage ${stage.id}: ${stage.status}`, stage.blocked_by ? `blocked by ${stage.blocked_by.join(", ")}`
        : own || `exit ${stage.exit_code}`);
    }
    if (/^\{"stage":"workspace_gate"/u.test(line)) { stageStart = index + 1; failuresAtStage = failures.length; }
  });
  if (!failures.length) {
    const end = lines.findIndex(line => line.startsWith("##[error]"));
    const tail = lines.slice(Math.max(0, (end < 0 ? lines.length : end) - 6), end < 0 ? lines.length : end + 1)
      .filter(line => line.trim());
    add("no test summary found; last log lines", tail.join(" | "));
  }
  return failures;
}

export function target(value) {
  const url = /\/pull\/(\d+)|\/actions\/runs\/(\d+)/u.exec(value ?? "");
  if (url) return url[1] ? { pr: Number(url[1]) } : { run: Number(url[2]) };
  if (!/^#?\d+$/u.test(value ?? "")) throw new Error("usage: npm run ci:failures -- <PR number | run id | PR/run URL>");
  const number = Number(value.replace("#", ""));
  return number < 10_000_000 ? { pr: number } : { run: number };
}

function main(argument) {
  const repository = gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]).trim();
  const selected = target(argument);
  let run;
  if (selected.pr) {
    const head = gh(["pr", "view", String(selected.pr), "--json", "headRefOid", "-q", ".headRefOid"]).trim();
    const runs = api(`repos/${repository}/actions/runs?head_sha=${head}&per_page=20`).workflow_runs
      .filter(item => item.path === ".github/workflows/ci.yml");
    if (!runs.length) { process.stdout.write(`PR ${selected.pr} head ${head.slice(0, 10)}: no CI run yet\n`); return 0; }
    run = runs[0];
    process.stdout.write(`PR ${selected.pr} head ${head.slice(0, 10)}: ${runs.length} run(s); latest shown\n`);
  } else run = api(`repos/${repository}/actions/runs/${selected.run}`);
  process.stdout.write(`run ${run.id} attempt ${run.run_attempt} ${run.status}/${run.conclusion ?? "-"} ` +
    `started ${run.run_started_at ?? run.created_at} ${run.html_url}\n`);
  const jobs = api(`repos/${repository}/actions/runs/${run.id}/jobs?per_page=100`).jobs;
  const failed = jobs.filter(job => FAILED.has(job.conclusion));
  const running = jobs.filter(job => job.status !== "completed").map(job => job.name);
  if (running.length) process.stdout.write(`still running: ${running.join(", ")} (rerun this command after they finish)\n`);
  if (!failed.length) { process.stdout.write("no failed job\n"); return 0; }
  for (const job of failed) {
    const minutes = job.completed_at ? ((Date.parse(job.completed_at) - Date.parse(job.started_at)) / 60000).toFixed(1) : "?";
    process.stdout.write(`\n✗ ${job.name}: ${job.conclusion} after ${minutes} min, ended ${job.completed_at ?? "-"}\n`);
    let failures;
    try { failures = logFailures(gh(["api", "--allow-escape-sequences", `repos/${repository}/actions/jobs/${job.id}/logs`])); }
    catch (error) { failures = [{ name: "log unavailable", detail: String(error.stderr ?? error.message).trim() }]; }
    // Aggregate jobs only restate leaf results; name them without repeating.
    if (job.name === "portable") failures = failures.filter(item => !item.name.startsWith("no test summary"));
    for (const failure of failures.slice(0, PER_JOB))
      process.stdout.write(`  - ${clip(failure.name)}${failure.detail ? `\n      ${clip(failure.detail)}` : ""}\n`);
    if (failures.length > PER_JOB) process.stdout.write(`  … ${failures.length - PER_JOB} more (gh run view ${run.id} --log-failed)\n`);
  }
  return 1;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exitCode = main(process.argv[2]);
