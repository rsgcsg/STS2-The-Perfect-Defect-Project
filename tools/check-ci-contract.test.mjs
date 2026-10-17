import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { ciWorkflowErrors, repositoryOnlyWorkflowChange } from "./check-ci-contract.mjs";

const workflow = path.resolve(import.meta.dirname, "..", ".github", "workflows", "ci.yml");

function currentSource() {
  return fs.readFileSync(workflow, "utf8").replace(/\r\n?/gu, "\n");
}

test("current CI workflow satisfies the repository CI contract", () => {
  assert.deepEqual(ciWorkflowErrors(currentSource()), []);
});

test("current CI workflow also validates with CRLF newlines", () => {
  assert.deepEqual(ciWorkflowErrors(currentSource().replace(/\n/gu, "\r\n")), []);
});

test("CI contract rejects unscoped push duplication", () => {
  const source = currentSource().replace(
    "  push:\n    branches:\n      - develop\n      - main\n",
    "  push:\n"
  );
  assert.ok(ciWorkflowErrors(source).includes("CI push trigger must be branch-scoped"));
});

test("CI contract requires an unconditional committed-patch barrier before routing every scope", () => {
  const error = "CI plan must check the committed patch unconditionally before routing";
  assert.ok(ciWorkflowErrors(currentSource().replace(" --check-patch", "")).includes(error));
  assert.ok(ciWorkflowErrors(currentSource().replace("      - id: route\n",
    "      - id: route\n        if: github.event_name == 'pull_request'\n")).includes(error));
  assert.ok(ciWorkflowErrors(currentSource().replace(
    "CHECK_BASE: ${{ github.event.pull_request.base.sha || github.event.before }}",
    "CHECK_BASE: HEAD")).includes("CI plan must retain the exact event comparison base"));
});

test("CI contract rejects replacing selected gates with a hand-picked subset", () => {
  const source = currentSource().replaceAll("run: node tools/check-plan.mjs execute", "run: npm --prefix components/annotator run test");
  assert.ok(ciWorkflowErrors(source).includes("Windows portability must run the selected root check"));
  assert.ok(ciWorkflowErrors(source).includes("Linux portability must run the selected root check"));
});

test("partition scheduling cannot drop siblings, defaults, diagnostics or original artifact isolation", () => {
  for (const [before, after, error] of [
    ["fail-fast: false", "fail-fast: true", "Linux must retain two isolated pytest shards"],
    ["shard: [1, 2]", "shard: [1]", "Linux must retain two isolated pytest shards"],
    ["timeout-minutes: 55", "timeout-minutes: 60", "Linux must retain its 55-minute budget"],
    ["python/.local/fixture-diagnostics/**", "omitted.json", "Linux must retain uniquely named original shard diagnostics"],
    ["python/.local/pytest-shard.json", "omitted.json", "Linux must retain uniquely named original shard diagnostics"],
    ["merge-multiple: false", "merge-multiple: true", "portable must download separate original current-run shard artifacts"],
    ["pattern: pytest-Linux-shard-*-${{ github.run_attempt }}", "pattern: pytest-*", "portable must download separate original current-run shard artifacts"],
  ]) assert.ok(ciWorkflowErrors(currentSource().replaceAll(before, after)).includes(error), error);
  assert.ok(ciWorkflowErrors(currentSource() + "\n# PYTEST_ADDOPTS\n").includes("CI must not inherit pytest shard selection into child tests"));
});

test("CI keeps the bounded Windows capacity adjustment separate from Linux's existing budget", () => {
  for (const minutes of [55, 60, 90, 750]) {
    const source = currentSource().replace("timeout-minutes: 75", `timeout-minutes: ${minutes}`);
    assert.ok(ciWorkflowErrors(source).includes("Windows must retain its 75-minute budget"));
  }
  for (const minutes of [75, 550]) {
    const source = currentSource().replace("timeout-minutes: 55", `timeout-minutes: ${minutes}`);
    assert.ok(ciWorkflowErrors(source).includes("Linux must retain its 55-minute budget"));
  }
});

test("aggregate implementation verifies original coverage before minting a fresh receipt", () => {
  const source = fs.readFileSync(new URL("./check-plan.mjs", import.meta.url), "utf8");
  assert.ok(source.includes("currentShardIdentity(root, e)"));
  assert.ok(source.indexOf("const coverage =") < source.indexOf('"receipt.json"'));
  assert.ok(source.includes("pytest_coverage: coverage"));
});

test("CI contract rejects a required status that omits selected Linux gates", () => {
  const source = currentSource().replace(
    "needs: [plan, docs, repository, linux-portability]",
    "needs: [linux-portability]"
  );
  assert.ok(ciWorkflowErrors(source).includes("portable must aggregate plan and selected lanes"));
});

test("CI contract rejects hosted exact-game qualification", () => {
  const source = `${currentSource()}\n# npm run check:exact-game\n`;
  assert.ok(ciWorkflowErrors(source).some((error) => error.includes("check:exact-game")));
});

test("CI contract rejects unpinned GitHub Actions", () => {
  const source = currentSource().replace(
    "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
    "actions/setup-node@v7"
  );
  assert.ok(ciWorkflowErrors(source).some((error) => error.includes("actions/setup-node@v7")));
});

// Same root contract covers both language stacks after consolidation.

test("both language environments remain required on both OS lanes", () => {
  for (const command of ["uv sync --project python --locked --all-extras", "npm ci --prefix python"]) {
    const source = currentSource().replaceAll(`run: ${command}`, "run: echo omitted");
    const errors = ciWorkflowErrors(source);
    assert.ok(errors.some((error) => error.startsWith("Linux must install")));
    assert.ok(errors.some((error) => error.startsWith("Windows must install")));
  }
});


test("repository CI installs its real dependencies on Linux", () => {
  const source = currentSource();
  for (const changed of [source.replace("runs-on: ubuntu-latest", "runs-on: windows-latest").replaceAll("runs-on: ubuntu-latest", "runs-on: windows-latest"),
    source.replaceAll("run: npm ci\n", "run: echo no-install\n"),
    source.replace("REPOSITORY_RESULT:", "OMITTED_RESULT:")])
    assert.ok(ciWorkflowErrors(changed).length > 0);
});

test("repository-only workflow changes cannot alter product jobs, plan or triggers", () => {
  const source = currentSource();
  assert.ok(repositoryOnlyWorkflowChange(source, source.replace("  docs:\n", "  docs:\n    # setup correction\n")));
  assert.ok(repositoryOnlyWorkflowChange(source.replace(/  repository:\n[\s\S]*?(?=  linux-portability:\n)/u, "")
    .replace("plan, docs, repository,", "plan, docs,"), source));
  for (const changed of [source.replace("node-version: 20", "node-version: 22"),
    source.replace("dotnet-version: 9.0.x", "dotnet-version: 10.0.x"),
    source.replace("      - develop", "      - other"),
    source + "\n  extra-job:\n    runs-on: ubuntu-latest\n",
    source + "\nenv:\n  PRODUCT_INPUT: changed\n",
    source.replace("  repository:\n", "  unknown:\n"),
    source.replace("  docs:\n", "  docs:\n  docs:\n")])
    assert.equal(repositoryOnlyWorkflowChange(source, changed), false);
});

import {failureNames, updateChecklist, mergeChecklists} from './windows-failures.mjs';

test('original test failures and a crash remain actionable names', () => {
  assert.deepEqual(failureNames(`2026-10-17T00:00:00Z FAILED tests/test_one.py::test_case - AssertionError
not ok 7 - child exits
 FAIL tests/runtime.test.ts > releases controller
Windows fatal exception: access violation
{"stage":"workspace_gate","id":"python","status":"failed"}`), [
    'Python process: access violation', 'child exits', 'gate: python (failed)',
    'tests/runtime.test.ts > releases controller', 'tests/test_one.py::test_case',
  ]);
});
test('checklist keeps first date, deduplicates one run and marks recurrence across runs', () => {
  const first = updateChecklist([], ['test_a', 'test_a'], {run: '10', date: '2026-10-17'});
  assert.equal(first.length, 1);
  const repeat = updateChecklist(first, ['test_a'], {run: '11', date: '2026-10-18'});
  assert.deepEqual(repeat[0], {test: 'test_a', first_seen: '2026-10-17', runs: ['10', '11'], last_seen: '2026-10-18'});
  assert.deepEqual(updateChecklist(repeat, [], {run: '12', date: '2026-10-19'}), repeat);
});

test('overlapping Windows histories retain both runs without double counting', () => {
  const common = updateChecklist([], ['shared'], {run: '1', date: '2026-10-16'});
  const left = updateChecklist(common, ['only-left', 'shared'], {run: '2', date: '2026-10-17'});
  const right = updateChecklist(common, ['only-right', 'shared'], {run: '3', date: '2026-10-17'});
  const combined = mergeChecklists(left, right);
  assert.deepEqual(combined.map(entry => entry.test).sort(), ['only-left', 'only-right', 'shared']);
  assert.deepEqual(combined.find(entry => entry.test === 'shared').runs, ['1', '2', '3']);
});
