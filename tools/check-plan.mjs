#!/usr/bin/env node
// One conservative check router for local use and CI. This is not qualification.
import fs from "node:fs";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { eligibleEvent, findReceipt } from "./check-receipt.mjs";

const root = path.resolve(import.meta.dirname, "..");
// Explicit prose surfaces only. Governance, contracts, component docs and
// unknown paths retain the complete suite. Python owners have a separate full consumer gate.
const editorial = new Set([
  "README.md", "CONTRIBUTING.md", "docs/NEW_MEMBER_HANDOFF.zh-CN.md",
  "docs/NEW_ENGINEER_GUIDE.md", "docs/DOCUMENT_MAP.md", "docs/STATUS.md",
]);

function prose({status, file, oldMode, newMode}) {
  // Mode comes from the committed raw Git diff, not extension or worktree bytes.
  // Executable files, symlinks and type changes cannot masquerade as prose.
  const regular = newMode === "100644" &&
    ((status === "A" && oldMode === "000000") || (status === "M" && oldMode === "100644"));
  const surface = (status === "M" && editorial.has(file)) ||
    file === "docs/memory/CURRENT.md" || /^docs\/(?:design|plans|evidence)\/[^/]+\.md$/.test(file);
  return regular && surface;
}

export function classifyChanges(entries) {
  if (!entries.length) return { scope: "full", reason: "empty_or_unknown_diff" };
  // Protected instructions cannot enter the Python report-companion route either.
  // Case variants stay protected on case-insensitive supported workstations.
  if (entries.some(({file}) => /(?:^|\/)(?:AGENTS|SKILL)\.md$/i.test(file))) {
    return {scope: "full", reason: "protected_instruction_change"};
  }
  if (entries.some(({oldMode, newMode}) =>
    (oldMode !== undefined && !["000000", "100644"].includes(oldMode)) ||
    (newMode !== undefined && !["000000", "100644"].includes(newMode)))) {
    return {scope: "full", reason: "non_regular_or_executable_change"};
  }
  if (entries.every(prose)) {
    return { scope: "docs", reason: "regular_prose_surfaces_only" };
  }
  const pythonOwner = ({file, status}) => ["M", "A", "D"].includes(status) &&
    /^python\/(spireagent|stpd|tests)\//.test(file) && !/(^|\/)AGENTS\.md$/.test(file);
  // Reports accompany a Python fix; they do not introduce another executable owner.
  // Eligible standalone prose uses docs; governance and evidence deletions stay full.
  const companion = ({file, status}) =>
    (status === "M" && (editorial.has(file) || ["docs/memory/CURRENT.md", "python/docs/PROJECT_CONSOLE.md"].includes(file))) ||
    (["A", "M"].includes(status) && /^docs\/evidence\/[^/]+\.md$/.test(file));
  if (entries.some(pythonOwner) && entries.every(entry => pythonOwner(entry) || companion(entry))) {
    return { scope: "python", reason: "python_owners_and_installed_platform_consumers" };
  }
  return { scope: "full", reason: "source_contract_governance_or_unknown_change" };
}

export function parseDiff(raw) {
  const parts = raw.split("\0");
  if (parts.pop() !== "") throw new Error("unterminated_diff");
  const entries = [];
  while (parts.length) {
    const status = parts.shift();
    const file = parts.shift();
    if (!/^[AMDTUXB]$/.test(status) || !file) throw new Error("unsupported_diff_record");
    entries.push({ status, file });
  }
  return entries;
}

export function parseRawDiff(raw) {
  const parts = raw.split("\0");
  if (parts.pop() !== "") throw new Error("unterminated_diff");
  const entries = [];
  while (parts.length) {
    const match = parts.shift().match(/^:(\d{6}) (\d{6}) [0-9a-f]{40} [0-9a-f]{40} ([AMDTUXB])$/);
    const file = parts.shift();
    if (!match || !file) throw new Error("unsupported_raw_diff_record");
    entries.push({status: match[3], file, oldMode: match[1], newMode: match[2]});
  }
  return entries;
}

export function makePlan({ base, head = "HEAD", forceFull = false, cwd = root } = {}) {
  const git = (...args) => execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  try {
    // Resolve options as revisions, never interpolate them into a shell.
    const exactHead = git("rev-parse", "--verify", "--end-of-options", `${head}^{commit}`).trim();
    const exactBase = git("rev-parse", "--verify", "--end-of-options", `${base}^{commit}`).trim();
    const entries = parseRawDiff(git("diff", "--no-renames", "--raw", "--no-abbrev", "-z", exactBase, exactHead, "--"));
    const dirty = git("status", "--porcelain", "--untracked-files=normal").trim().length > 0;
    const route = forceFull || dirty ? { scope: "full", reason: forceFull ? "explicit_full" : "dirty_worktree" } : classifyChanges(entries);
    return { ...route, base: exactBase, head: exactHead, files: entries };
  } catch {
    return { scope: "full", reason: "diff_unavailable", base: null, head: null, files: [] };
  }
}

export function aggregatePassed(scope, results) {
  if (results.plan !== "success") return false;
  if (scope === "docs") return results.docs === "success" && results.linux === "skipped" && results.windows === "skipped";
  if (scope === "reuse") return results.docs === "success" && results.linux === "skipped" && results.windows === "skipped";
  if (["full", "python"].includes(scope)) return results.docs === "skipped" && results.linux === "success" && results.windows === "success";
  return false;
}

export function scopeCommands(scope) {
  if (scope === "docs") return ["check:docs"];
  if (scope === "reuse") return ["check:repository"];
  if (scope === "python") return ["check:python-scope"];
  if (scope === "full") return ["check"];
  throw new Error("unknown_check_scope");
}
function execute(scope) {
  for (const command of scopeCommands(scope)) {
    const result = spawnSync(process.platform === "win32" ? "npm.cmd" : "npm", ["run", command],
      {cwd: root, stdio: "inherit", shell: process.platform === "win32"});
    if (result.status !== 0) { process.exitCode = result.status ?? 1; return; }
  }
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args[0] === "aggregate") {
    const e = process.env;
    const results = {plan: e.PLAN_RESULT, docs: e.DOCS_RESULT, linux: e.LINUX_RESULT, windows: e.WINDOWS_RESULT};
    if (!aggregatePassed(e.CHECK_SCOPE, results)) process.exitCode = 1;
    else {
      const git = (...args) => execFileSync("git", args, {cwd: root, encoding: "utf8"}).trim();
      if (e.GITHUB_STEP_SUMMARY) fs.appendFileSync(e.GITHUB_STEP_SUMMARY,
        `Passed scope: **${e.CHECK_SCOPE}**. Current checkout ${git("rev-parse", "HEAD")}. ${e.CHECK_PROOF || "Fresh execution"}\n`);
      if (["full", "python"].includes(e.CHECK_SCOPE)) {
        fs.mkdirSync(path.join(root, ".local", "ci"), {recursive: true});
        fs.writeFileSync(path.join(root, ".local", "ci", "receipt.json"), JSON.stringify({
          schema: "spireagent/ci-execution-1", executed: true, repository: e.GITHUB_REPOSITORY,
          run_id: e.GITHUB_RUN_ID, run_attempt: e.GITHUB_RUN_ATTEMPT, scope: e.CHECK_SCOPE,
          checkout: git("rev-parse", "HEAD"), tree: git("rev-parse", "HEAD^{tree}"),
          workflow: git("rev-parse", "HEAD:.github/workflows/ci.yml"), results,
        }, null, 2) + "\n");
      }
    }
  } else if (args[0] === "execute") {
    execute(process.env.CHECK_SCOPE);
  } else {
    const baseIndex = args.indexOf("--base");
    const headIndex = args.indexOf("--head");
    const plan = makePlan({ base: baseIndex < 0 ? process.env.CHECK_BASE : args[baseIndex + 1], head: headIndex < 0 ? "HEAD" : args[headIndex + 1], forceFull: args.includes("--full") || process.env.CHECK_FULL === "true" });
    const e = process.env;
    if (plan.scope === "python" && ((e.GITHUB_EVENT_NAME === "pull_request" && e.GITHUB_BASE_REF === "main") ||
        (e.GITHUB_EVENT_NAME === "push" && e.GITHUB_REF === "refs/heads/main"))) {
      Object.assign(plan, {scope: "full", reason: "executable_main_promotion"});
    }
    // Ordinary topic PRs always execute. Only promotion/integration can reuse.
    if (e.GITHUB_ACTIONS === "true" && e.CHECK_FULL !== "true" && !args.includes("--full") &&
        ["full", "python"].includes(plan.scope) && !["dirty_worktree", "diff_unavailable"].includes(plan.reason) &&
        eligibleEvent(e.GITHUB_EVENT_NAME, e.GITHUB_REF, e.GITHUB_BASE_REF, e.GITHUB_HEAD_REF)) {
      const git = (...values) => execFileSync("git", values, {cwd: root, encoding: "utf8", stdio: "pipe"});
      const current = {repository: e.GITHUB_REPOSITORY, scope: plan.scope,
        tree: git("rev-parse", "HEAD^{tree}").trim(), workflow: git("rev-parse", "HEAD:.github/workflows/ci.yml").trim()};
      const proof = await findReceipt(current, {token: e.GH_TOKEN, git, runId: e.GITHUB_RUN_ID});
      if (proof) Object.assign(plan, {scope: "reuse", reason: "verified_execution_same_tree", proof});
    }
    process.stdout.write(`${JSON.stringify(plan, null, 2)}\n`);
    if (process.env.GITHUB_OUTPUT) fs.appendFileSync(process.env.GITHUB_OUTPUT, `scope=${plan.scope}\nproof=${plan.proof?.url || ""}\n`);
    if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `Check scope: **${plan.scope}** (${plan.reason}). Base ${plan.base}; tested checkout ${plan.head}. Source/test only.\n`);
    if (args.includes("--run")) {
      if (plan.base && plan.head) execFileSync("git", ["diff", "--check", plan.base, plan.head, "--"], { cwd: root, stdio: "inherit" });
      execute(plan.scope);
    }
  }
}
