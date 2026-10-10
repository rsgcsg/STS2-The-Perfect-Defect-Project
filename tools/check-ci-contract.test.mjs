import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { ciWorkflowErrors } from "./check-ci-contract.mjs";

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
    ["python/.local/pytest-shard.json", "omitted.json", "Linux must retain uniquely named original shard diagnostics"],
    ["merge-multiple: false", "merge-multiple: true", "portable must download separate original current-run shard artifacts"],
    ["pattern: pytest-*-shard-*-${{ github.run_attempt }}", "pattern: pytest-*", "portable must download separate original current-run shard artifacts"],
  ]) assert.ok(ciWorkflowErrors(currentSource().replace(before, after)).includes(error), error);
  assert.ok(ciWorkflowErrors(currentSource() + "\n# PYTEST_ADDOPTS\n").includes("CI must not inherit pytest shard selection into child tests"));
});

test("aggregate implementation verifies original coverage before minting a fresh receipt", () => {
  const source = fs.readFileSync(new URL("./check-plan.mjs", import.meta.url), "utf8");
  assert.ok(source.includes("currentShardIdentity(root, e)"));
  assert.ok(source.indexOf("const coverage =") < source.indexOf('"receipt.json"'));
  assert.ok(source.includes("pytest_coverage: coverage"));
});

test("CI contract rejects a required status that does not aggregate Windows", () => {
  const source = currentSource().replace(
    "needs: [plan, docs, linux-portability, windows-portability]",
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
