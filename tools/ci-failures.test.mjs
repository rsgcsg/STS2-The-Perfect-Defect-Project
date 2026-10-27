import assert from "node:assert/strict";
import test from "node:test";
import { cleanLog, logFailures, target } from "./ci-failures.mjs";

const gate = (id, status, extra = {}) => `2026-10-27T12:06:59.1Z ${JSON.stringify({ stage: "workspace_gate", id, status, ...extra })}`;

test("log lines lose runner timestamps and terminal colours", () => {
  assert.deepEqual(cleanLog("2026-10-27T12:00:00.1234567Z \u001b[31mFAILED\u001b[0m x\r\nplain"), ["FAILED x", "plain"]);
});

test("pytest, node:test and vitest failures surface with their first error line", () => {
  const failures = logFailures([
    "FAILED tests/test_local_models.py::test_unknown_runtime_version_never_selects_a_guessed_startup_contract[0.1.0-rc.53] - Failed: DID NOT RAISE BoundaryError",
    "not ok 3 - preflight lists every failure",
    "  ---",
    "  error: 'Expected values to be strictly equal'",
    " FAIL  test/global-replay.test.ts > replays one run",
    "AssertionError: expected 1 to be 2",
    gate("python", "failed", { exit_code: 1 }),
  ].join("\n"));
  assert.deepEqual(failures.map(item => item.name), [
    "tests/test_local_models.py::test_unknown_runtime_version_never_selects_a_guessed_startup_contract[0.1.0-rc.53]",
    "preflight lists every failure", "test/global-replay.test.ts > replays one run", "stage python: failed"]);
  assert.equal(failures[0].detail, "Failed: DID NOT RAISE BoundaryError");
  assert.equal(failures[1].detail, "error: 'Expected values to be strictly equal'");
  assert.equal(failures[2].detail, "AssertionError: expected 1 to be 2");
  assert.equal(failures[3].detail, "exit 1", "a stage with named test failures does not repeat them");
});

test("a lint or docs stage without test names shows its own cause, not only its exit code", () => {
  const failures = logFailures([
    gate("repository", "passed", { exit_code: 0 }),
    "E501 Line too long (105 > 100)",
    "   --> spireagent/workbench/managed_evaluation.py:225:101",
    "Found 2 errors.",
    gate("python", "failed", { exit_code: 1 }),
    "Active Markdown link check failed (1):",
    "- docs/player-environment/PROTOCOL.md: path escapes repository: ../../../../docs/protocols/P1_GLOBAL_R1.zh-CN.md",
    gate("connector", "failed", { exit_code: 1 }),
    gate("annotator", "blocked", { blocked_by: ["connector"] }),
  ].join("\n"));
  assert.deepEqual(failures, [
    { name: "stage python: failed", detail: "E501 Line too long (105 > 100)" },
    { name: "stage connector: failed", detail: "Active Markdown link check failed (1): | " +
      "- docs/player-environment/PROTOCOL.md: path escapes repository: ../../../../docs/protocols/P1_GLOBAL_R1.zh-CN.md" },
    { name: "stage annotator: blocked", detail: "blocked by connector" },
  ]);
});

test("without a recognised summary the last lines before the runner error are shown", () => {
  const [failure] = logFailures("setup\nnpm ERR! code ENOENT\n##[error]Process completed with exit code 1.\nafter");
  assert.equal(failure.name, "no test summary found; last log lines");
  assert.match(failure.detail, /npm ERR! code ENOENT \| ##\[error\]Process completed/u);
});

test("targets accept PR numbers, run ids and URLs", () => {
  assert.deepEqual(target("266"), { pr: 266 });
  assert.deepEqual(target("#266"), { pr: 266 });
  assert.deepEqual(target("40500470336"), { run: 40500470336 });
  assert.deepEqual(target("https://github.com/o/r/pull/266"), { pr: 266 });
  assert.deepEqual(target("https://github.com/o/r/actions/runs/40500470336/job/1"), { run: 40500470336 });
  assert.throws(() => target("main"), /usage: npm run ci:failures/u);
});
