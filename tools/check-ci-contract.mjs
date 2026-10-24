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
  const repository = jobBlock(source, "repository");
  if (!docs?.includes("run: npm ci")) errors.push("docs must install locked repository dependencies");
  if (!repository || !repository.includes("if: needs.plan.outputs.scope == 'repository'") ||
      !repository.includes("needs: plan") || !repository.includes("runs-on: ubuntu-latest") ||
      !repository.includes("run: npm ci") ||
      !repository.includes("run: node tools/check-plan.mjs execute") ||
      !repository.includes("CHECK_SCOPE: ${{ needs.plan.outputs.scope }}") ||
      !repository.includes("fetch-depth: 0") || !repository.includes("persist-credentials: false"))
    errors.push("repository must execute locked selected checks on Linux");
  if (!plan || !/run: node tools\/check-plan\.mjs --check-patch\s*$/mu.test(plan)) errors.push("CI must use the shared check planner");
  const route = plan?.match(/      - id: route\n[\s\S]*?(?=\n      - |$)/u)?.[0] ?? "";
  if (!/run: node tools\/check-plan\.mjs --check-patch\s*$/mu.test(route) ||
      /^        if:/mu.test(route)) errors.push("CI plan must check the committed patch unconditionally before routing");
  if (!route.includes("CHECK_BASE: ${{ github.event.pull_request.base.sha || github.event.before }}"))
    errors.push("CI plan must retain the exact event comparison base");
  if (!docs || !docs.includes("run: node tools/check-plan.mjs execute")) errors.push("CI must check editorial changes");
  if (!/^  schedule:/mu.test(source)) errors.push("CI must retain periodic full checks");
  for (const [name, scope] of [["docs", "docs"], ["linux-portability", "full"]]) {
    const block = jobBlock(source, name) || "";
    if (!block.includes("needs: plan") || !block.includes(`needs.plan.outputs.scope == '${scope}'`)) errors.push(`CI ${name} must use planned scope`);
  }

  const linux = jobBlock(source, "linux-portability");
  const windows = jobBlock(source, "windows-portability");
  const portable = jobBlock(source, "portable");
  if (!linux) errors.push("CI is missing linux-portability");
  if (!windows) errors.push("CI is missing windows-portability");
  if (!portable) errors.push("CI is missing required portable aggregate");

  if (linux) {
    requireMatch(errors, "Linux portability must use ubuntu-latest", linux, /runs-on:\s*ubuntu-latest/u);
    requireMatch(errors, "Linux portability must run the selected root check", linux, /run:\s*node tools\/check-plan\.mjs execute --pytest-shard \$\{\{ matrix\.shard \}\}\/2\s*$/mu);
    requireMatch(errors, "Linux portability must run git diff --check", linux, /run:\s*git diff --check\s*$/mu);
    if (!linux.includes('mkdir -p "$GITHUB_WORKSPACE/.local/portable-tmp"') ||
        !["TMPDIR", "TMP", "TEMP"].every(name => linux.includes(
          `echo "${name}=$GITHUB_WORKSPACE/.local/portable-tmp" >> "$GITHUB_ENV"`)))
      errors.push("Linux portable scratch must use the observed workspace filesystem");
    requireMatch(errors, "Linux checkout must fetch full history", linux, /fetch-depth:\s*0/u);
    requireMatch(errors, "Linux checkout must not persist credentials", linux, /persist-credentials:\s*false/u);
    requireMatch(errors, "Dependency review must remain on pull requests", linux,
      /if:\s*github\.event_name == 'pull_request'/u);
  }
  for (const [label, lane, minutes] of [["Linux", linux, 55], ["Windows", windows, 75]]) {
    if (!lane) continue;
    requireMatch(errors, `${label} must install the locked Python component`, lane,
      /run:\s*uv sync --project python --locked --all-extras\s*$/mu);
    requireMatch(errors, `${label} must install Python consumer packages`, lane,
      /run:\s*npm ci --prefix python\s*$/mu);
    requireMatch(errors, `${label} must retain two isolated pytest shards`, lane,
      /strategy:\s*\n      fail-fast: false\s*\n      matrix:\s*\n        shard: \[1, 2\]/u);
    requireMatch(errors, `${label} must retain its ${minutes}-minute budget`, lane,
      new RegExp(`^    timeout-minutes: ${minutes}[ \\t]*$`, "mu"));
    if (!lane.includes("name: pytest-${{ runner.os }}-shard-${{ matrix.shard }}-${{ github.run_attempt }}") ||
        !lane.includes("python/.local/pytest-shard.json") || !lane.includes("python/.local/fixture-diagnostics/**") || !lane.includes(".local/checks/workspace-*.json")) {
      errors.push(`${label} must retain uniquely named original shard diagnostics`);
    }
  }

  if (!source.includes('cron: "17 21 * * *"') ||
      !windows?.includes("CHECK_SCOPE: full") ||
      !windows?.includes("if: github.event_name == 'schedule' || inputs.windows == true || github.base_ref == 'main' || github.ref == 'refs/heads/main'") ||
      !linux?.includes("github.event_name != 'schedule'")) errors.push("Windows must be daily and pre-release, outside PR gates");
  if (!jobBlock(source, "windows-failures")?.includes("run: node tools/windows-failures.mjs"))
    errors.push("Windows must retain its failure checklist");
  if (windows) {
    requireMatch(errors, "Windows portability must use windows-latest", windows, /runs-on:\s*windows-latest/u);
    requireMatch(errors, "Windows portability must run the selected root check", windows, /run:\s*node tools\/check-plan\.mjs execute --pytest-shard \$\{\{ matrix\.shard \}\}\/2\s*$/mu);
    requireMatch(errors, "Windows portability must run git diff --check", windows, /run:\s*git diff --check\s*$/mu);
    requireMatch(errors, "Windows checkout must fetch full history", windows, /fetch-depth:\s*0/u);
    requireMatch(errors, "Windows checkout must not persist credentials", windows, /persist-credentials:\s*false/u);
    if (/npm\s+--prefix/u.test(windows)) {
      errors.push("Windows portability must not maintain a hand-picked component subset");
    }
  }
  if (portable) {
    requireMatch(errors, "portable must aggregate plan and selected lanes", portable,
      /needs:\s*\[plan, docs, repository, linux-portability\]/u);
    requireMatch(errors, "portable must evaluate failed/cancelled dependencies", portable,
      /if:\s*\$\{\{\s*always\(\) && github\.event_name != 'schedule'\s*\}\}/u);
    requireMatch(errors, "portable must use the tested scope aggregate", portable,
      /run:\s*node tools\/check-plan\.mjs aggregate\s*$/mu);
    for (const name of ["CHECK_SCOPE", "CHECK_PROOF", "PLAN_RESULT", "DOCS_RESULT", "REPOSITORY_RESULT", "LINUX_RESULT"]) {
      if (!portable.includes(`${name}:`)) errors.push(`portable missing ${name}`);
    }
    if (!portable.includes("actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093") ||
        !portable.includes("pattern: pytest-Linux-shard-*-${{ github.run_attempt }}") ||
        !portable.includes("path: .local/ci/pytest-shards") || !portable.includes("merge-multiple: false") ||
        portable.includes("run-id:") || portable.includes("repository:")) {
      errors.push("portable must download separate original current-run shard artifacts");
    }
    if (portable.indexOf("actions/download-artifact@") > portable.indexOf("run: node tools/check-plan.mjs aggregate")) {
      errors.push("portable must download shards before sealing its receipt");
    }
  }
  if (source.includes("PYTEST_ADDOPTS")) errors.push("CI must not inherit pytest shard selection into child tests");

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
  for (const name of ["linux-portability"]) {
    const lane = jobBlock(source, name) || "";
    if (!lane.includes("outputs.scope == 'python'")) errors.push(`${name} must cover Python scope`);
    if (!lane.includes("python/.local/pytest.xml")) errors.push(`${name} must report Python durations`);
  }
  return errors;
}

// A narrow routing/management edit may exercise repository tooling without
// re-running unchanged product jobs. Any changed trigger/plan/product lane,
// new unknown job, or invalid shape retains full. This is not general YAML diffing.
export function repositoryOnlyWorkflowChange(beforeRaw, afterRaw) {
  const before = normalizeNewlines(beforeRaw), after = normalizeNewlines(afterRaw);
  const split = source => {
    const index = /^jobs:\n/mu.exec(source)?.index;
    if (index === undefined) return null;
    const body = source.slice(index + 6);
    // YAML permits global env/defaults after jobs; they still affect product
    // jobs. Our narrow format has no top-level keys in this suffix.
    if (/^[^\s#]/mu.test(body)) return null;
    const names = [...body.matchAll(/^  ([A-Za-z0-9_-]+):\s*$/gmu)].map(m => m[1]);
    if (new Set(names).size !== names.length) return null;
    return {header: source.slice(0, index + 6), names};
  };
  const left = split(before), right = split(after);
  const allowed = new Set(["plan", "docs", "repository", "linux-portability", "windows-portability", "windows-failures", "portable"]);
  if (!left || !right || left.header !== right.header ||
      [...left.names, ...right.names].some(name => !allowed.has(name)) || ciWorkflowErrors(after).length) return false;
  return ["plan", "linux-portability", "windows-portability", "windows-failures"].every(name =>
    jobBlock(before, name) !== null && jobBlock(before, name) === jobBlock(after, name));
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
