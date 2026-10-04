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

test("CI contract requires early committed-range hygiene rather than a late dirty diff", () => {
  const source = currentSource().replace("run: node tools/check-plan.mjs patch", "run: git diff --check");
  assert.ok(ciWorkflowErrors(source).includes(
    "CI must check the committed patch before selecting expensive jobs"));
});

test("CI contract rejects moving patch preflight after route selection", () => {
  const source = currentSource();
  const start = source.indexOf("      - name: Check committed patch before");
  const end = source.indexOf("      - id: route", start);
  const step = source.slice(start, end);
  const without = source.slice(0, start) + source.slice(end);
  const beforeDocs = without.indexOf("\n  docs:");
  const changed = without.slice(0, beforeDocs) + step + without.slice(beforeDocs);
  assert.ok(ciWorkflowErrors(changed).includes(
    "CI must check the committed patch before selecting expensive jobs"));
});

test("CI contract rejects replacing selected gates with a hand-picked subset", () => {
  const source = currentSource().replaceAll("run: node tools/check-plan.mjs execute", "run: npm --prefix components/annotator run test");
  assert.ok(ciWorkflowErrors(source).includes("Windows portability must run the selected root check"));
  assert.ok(ciWorkflowErrors(source).includes("Linux portability must run the selected root check"));
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
