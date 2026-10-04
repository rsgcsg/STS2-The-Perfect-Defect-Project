#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)));

function normalizeNewlines(source) {
  return source.replace(/\r\n?/gu, "\n");
}

function jobBlock(source, jobName) {
  const marker = `  ${jobName}:\n`;
  const start = source.indexOf(marker);
  if (start < 0) return null;
  const bodyStart = start + marker.length;
  const next = source.slice(bodyStart).search(/^  [A-Za-z0-9_-]+:\s*$/mu);
  return next < 0 ? source.slice(bodyStart) : source.slice(bodyStart, bodyStart + next);
}

function requireMatch(errors, label, source, pattern) {
  if (!pattern.test(source)) errors.push(label);
}

export function ciWorkflowErrors(rawSource) {
  const source = normalizeNewlines(rawSource);
  const errors = [];
  requireMatch(errors, "CI must trigger for pull requests", source, /^  pull_request:\s*$/mu);
  requireMatch(errors, "CI push trigger must be branch-scoped", source,
    /^  push:\s*\n    branches:\s*$/mu);
  for (const branch of ["develop", "main"]) {
    if (!source.includes(`      - ${branch}`)) errors.push(`CI push trigger is missing ${branch}`);
  }
  requireMatch(errors, "CI must retain manual workflow_dispatch", source, /^  workflow_dispatch:\s*$/mu);
  requireMatch(errors, "CI must cancel stale runs", source, /^  cancel-in-progress:\s*true\s*$/mu);
  requireMatch(errors, "CI concurrency must distinguish PR/ref", source,
    /github\.event\.pull_request\.number\s*\|\|\s*github\.ref/u);

  if (/"(?:release|hotfix)\/\*\*"/u.test(source)) errors.push("CI must avoid duplicate release/hotfix push runs");
  if (/^\s+paths(?:-ignore)?:/mu.test(source)) errors.push("CI must not skip required workflows by path");
  if (/continue-on-error:\s*true/u.test(source)) errors.push("CI must not ignore check failures");
  const plan = jobBlock(source, "plan");
  const docs = jobBlock(source, "docs");
  if (!plan || !/run: node tools\/check-plan\.mjs\s*$/mu.test(plan)) errors.push("CI must use the shared check planner");
  if (!docs || !docs.includes("run: node tools/check-plan.mjs execute")) errors.push("CI must check editorial changes");
  if (!/^  schedule:/mu.test(source)) errors.push("CI must retain periodic full checks");
  for (const [name, scope] of [["docs", "docs"], ["linux-portability", "full"], ["windows-portability", "full"]]) {
    const block = jobBlock(source, name) || "";
    if (!block.includes("needs: plan") || !block.includes(`if: needs.plan.outputs.scope == '${scope}'`)) errors.push(`CI ${name} must use planned scope`);
  }

  const linux = jobBlock(source, "linux-portability");
  const windows = jobBlock(source, "windows-portability");
  const portable = jobBlock(source, "portable");
  if (!linux) errors.push("CI is missing linux-portability");
  if (!windows) errors.push("CI is missing windows-portability");
  if (!portable) errors.push("CI is missing required portable aggregate");

  if (linux) {
    requireMatch(errors, "Linux portability must use ubuntu-latest", linux, /runs-on:\s*ubuntu-latest/u);
    requireMatch(errors, "Linux portability must run the selected root check", linux, /run:\s*node tools\/check-plan\.mjs execute\s*$/mu);
    requireMatch(errors, "Linux portability must run git diff --check", linux, /run:\s*git diff --check\s*$/mu);
    requireMatch(errors, "Linux checkout must fetch full history", linux, /fetch-depth:\s*0/u);
    requireMatch(errors, "Linux checkout must not persist credentials", linux, /persist-credentials:\s*false/u);
    requireMatch(errors, "Dependency review must remain on pull requests", linux,
      /if:\s*github\.event_name == 'pull_request'/u);
  }
  for (const [label, lane] of [["Linux", linux], ["Windows", windows]]) {
    if (!lane) continue;
    requireMatch(errors, `${label} must install the locked Python component`, lane,
      /run:\s*uv sync --project python --locked --all-extras\s*$/mu);
    requireMatch(errors, `${label} must install Python consumer packages`, lane,
      /run:\s*npm ci --prefix python\s*$/mu);
  }

  if (windows) {
    requireMatch(errors, "Windows portability must use windows-latest", windows, /runs-on:\s*windows-latest/u);
    const timeoutMinutes = Number(windows.match(/^    timeout-minutes:[ \t]*(\d+)[ \t]*$/mu)?.[1]);
    if (!Number.isInteger(timeoutMinutes) || timeoutMinutes < 90) {
      errors.push("Windows portability timeout must be at least 90 minutes for its full portable gate");
    }
    requireMatch(errors, "Windows portability must run the selected root check", windows, /run:\s*node tools\/check-plan\.mjs execute\s*$/mu);
    requireMatch(errors, "Windows portability must run git diff --check", windows, /run:\s*git diff --check\s*$/mu);
    requireMatch(errors, "Windows checkout must fetch full history", windows, /fetch-depth:\s*0/u);
    requireMatch(errors, "Windows checkout must not persist credentials", windows, /persist-credentials:\s*false/u);
    if (/npm\s+--prefix/u.test(windows)) {
      errors.push("Windows portability must not maintain a hand-picked component subset");
    }
  }
  if (portable) {
    requireMatch(errors, "portable must aggregate plan and selected lanes", portable,
      /needs:\s*\[plan, docs, linux-portability, windows-portability\]/u);
    requireMatch(errors, "portable must evaluate failed/cancelled dependencies", portable,
      /if:\s*\$\{\{\s*always\(\)\s*\}\}/u);
    requireMatch(errors, "portable must use the tested scope aggregate", portable,
      /run:\s*node tools\/check-plan\.mjs aggregate\s*$/mu);
    for (const name of ["CHECK_SCOPE", "CHECK_PROOF", "PLAN_RESULT", "DOCS_RESULT", "LINUX_RESULT", "WINDOWS_RESULT"]) {
      if (!portable.includes(`${name}:`)) errors.push(`portable missing ${name}`);
    }
  }

  for (const line of source.split("\n")) {
    const match = line.match(/^\s*- uses:\s*([^\s#]+)@([^\s#]+)/u);
    if (match && !/^[0-9a-f]{40}$/u.test(match[2])) {
      errors.push(`GitHub Action is not pinned by full commit SHA: ${match[1]}@${match[2]}`);
    }
  }

  const forbiddenHostedCommands = [
    "npm run check:exact-game",
    "npm run deploy",
    "npm run launch",
    "npm run verify:loaded",
    "npm run game-mod:deploy",
    "npm run game-mod:launch",
    "npm run game-mod:verify-loaded",
    "npm run annotator:build",
    "npm run annotator:launch-live-connector",
    "npm run connector:build"
  ];
  for (const command of forbiddenHostedCommands) {
    if (source.includes(command)) {
      errors.push(`Hosted CI must not claim exact-game/runtime qualification via: ${command}`);
    }
  }
  if (!source.includes("name: portable-execution-receipt") || !source.includes("if-no-files-found: error")) errors.push("CI must retain executed receipts");
  if (!source.includes("actions: read")) errors.push("CI must be able to verify prior execution");
  for (const name of ["linux-portability", "windows-portability"]) {
    const lane = jobBlock(source, name) || "";
    if (!lane.includes("outputs.scope == 'python'")) errors.push(`${name} must cover Python scope`);
    if (!lane.includes("python/.local/pytest.xml")) errors.push(`${name} must report Python durations`);
  }
  return errors;
}

export function validateCiWorkflow(workflowPath) {
  const source = fs.readFileSync(workflowPath, "utf8");
  return ciWorkflowErrors(source);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const workflowPath = path.resolve(ROOT, "..", ".github", "workflows", "ci.yml");
  const errors = validateCiWorkflow(workflowPath);
  if (errors.length > 0) {
    process.stderr.write(`${errors.join("\n")}\n`);
    process.exitCode = 1;
  } else {
    process.stdout.write("CI contract checks passed\n");
  }
}
