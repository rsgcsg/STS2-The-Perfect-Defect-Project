import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import test from "node:test";
import {verifyPytestShards} from "./verify-pytest-shards.mjs";
import {workspaceStages, pytestPartition} from "./check-workspace.mjs";

const hash = bytes => crypto.createHash("sha256").update(bytes).digest("hex");
const write = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, JSON.stringify(value)); };

// Validator fixtures model completed owner stages; they are not full-suite receipts.
function fixture() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "pytest-proof-"));
  const expected = {source: {head: "a".repeat(40), tree: "b".repeat(40), workflow: "c".repeat(40),
    selector: "d".repeat(40), dirty: false}, repository: "owner/repo", run_id: "123", run_attempt: "1", scope: "full"};
  const collection = ["deploy/hub/test_a.py", "tests/test_b.py", "tests/test_c.py"].flatMap(file =>
    ["one", "two"].map(value => ({file, nodeid: `${file}::test_value[${value}]`})));
  const leaves = [];
  for (const os of ["Linux", "Windows"]) for (const index of [1, 2]) {
    const root = path.join(directory, `pytest-${os}-shard-${index}-1`);
    const selected = collection.filter(item => [...new Set(collection.map(value => value.file))].indexOf(item.file) % 2 === index - 1).map(item => item.nodeid);
    const report = {schema: "spireagent/pytest-shard-1", run: {repository: expected.repository,
      run_id: "123", run_attempt: "1", job: os === "Linux" ? "linux-portability" : "windows-portability", os, scope: "full"},
      shard: {index, count: 2}, source_at_start: expected.source, source_at_end: expected.source,
      invocation: {argv: ["python", "-m", "pytest", "-p", "tools.pytest_shard", `--portable-shard-index=${index}`, "--portable-shard-count=2"],
        pid: index, started_at: "2026-01-01T00:00:00Z", ended_at: "2026-01-01T00:00:01Z", exit_code: 0},
      collection, collection_sha256: hash(JSON.stringify(collection)), selected_nodeids: selected,
      executed: selected.map(nodeid => ({nodeid, finished: true, status: "passed", phases:
        ["setup", "call", "teardown"].map(when => ({when, outcome: "passed", subtest: false, wasxfail: null}))})),
      errors: [], collection_errors: [], state: "completed", junit_sha256: hash("<testsuite/>\n")};
    const identity = {head: expected.source.head, tree: expected.source.tree, dirty: false};
    const workspace = {schema: "spireagent/portable-stage-results-1", scope: "full",
      platform: os === "Linux" ? "linux" : "win32", verdict: "passed", source_accepted: true,
      interrupted_signal: null, source_at_start: identity, source_at_end: identity,
      pytest_partition: pytestPartition(`${index}/2`), results: workspaceStages("full", {pytestShard: `${index}/2`}).map(stage =>
        ({id: stage.id, args: stage.args, status: "passed", exit_code: 0, signal: null}))};
    const manifest = path.join(root, "python", ".local", "pytest-shard.json");
    const stageFile = path.join(root, ".local", "checks", `workspace-full-${workspace.platform}.json`);
    write(manifest, report); write(stageFile, workspace);
    fs.writeFileSync(path.join(root, "python", ".local", "pytest.xml"), "<testsuite/>\n");
    leaves.push({root, manifest, stageFile, report, workspace});
  }
  return {directory, expected, leaves};
}

test("complete deterministic same-OS selections seal a small digest/count proof", () => {
  const data = fixture();
  try {
    const result = verifyPytestShards(data.directory, data.expected);
    assert.equal(result.complete, true);
    assert.equal(result.leaves.length, 4);
    assert.equal(result.leaves.reduce((total, leaf) => total + leaf.selected, 0), 12);
    assert.ok(JSON.stringify(result).length < 2000);
    // Existing per-test skips are retained, not a whole-shard skip.
    const item = data.leaves[0].report.executed[0];
    item.phases = ["setup", "teardown"].map(when => ({when, outcome: when === "setup" ? "skipped" : "passed", subtest: false, wasxfail: null}));
    item.status = "skipped";
    write(data.leaves[0].manifest, data.leaves[0].report);
    assert.equal(verifyPytestShards(data.directory, data.expected).leaves[0].skipped, 1);
  } finally { fs.rmSync(data.directory, {recursive: true, force: true}); }
});

test("missing/extra/foreign/unfinished/failed evidence cannot turn green job groups into coverage", () => {
  const mutations = [
    leaf => leaf.report.selected_nodeids.pop(),
    leaf => leaf.report.executed.pop(),
    leaf => leaf.report.executed.push(leaf.report.executed[0]),
    leaf => leaf.report.executed[0].nodeid = "",
    leaf => leaf.report.executed[0].finished = false,
    leaf => leaf.report.executed[0].phases.pop(),
    leaf => leaf.report.executed[0].phases.push(leaf.report.executed[0].phases[0]),
    leaf => leaf.report.executed[0].phases[2].outcome = "failed",
    leaf => leaf.report.executed[0].phases.push({when: "call", outcome: "failed", subtest: true, wasxfail: null}),
    leaf => leaf.report.invocation.exit_code = 2,
    leaf => delete leaf.report.invocation.ended_at,
    leaf => leaf.report.state = "running",
    leaf => leaf.report.collection_errors.push("collection failed"),
    leaf => leaf.report.run.run_attempt = "2",
    leaf => leaf.report.run.os = "Darwin",
    leaf => leaf.report.source_at_end = {...leaf.report.source_at_end, dirty: true},
    leaf => leaf.report.source_at_start = {...leaf.report.source_at_start, workflow: "e".repeat(40)},
    leaf => leaf.workspace.results.pop(),
    leaf => leaf.workspace.results[2].status = "cancelled",
    leaf => leaf.workspace.pytest_partition.complete = true,
    leaf => leaf.report.collection = [],
    leaf => {
      leaf.report.collection = leaf.report.collection.slice(0, -1);
      leaf.report.collection_sha256 = hash(JSON.stringify(leaf.report.collection));
    },
    leaf => { for (const item of leaf.report.executed) {
      item.status = "skipped"; item.phases = ["setup", "teardown"].map(when =>
        ({when, outcome: when === "setup" ? "skipped" : "passed", subtest: false, wasxfail: null}));
    } },
  ];
  for (const [index, mutation] of mutations.entries()) {
    const data = fixture();
    try {
      mutation(data.leaves[0]); write(data.leaves[0].manifest, data.leaves[0].report); write(data.leaves[0].stageFile, data.leaves[0].workspace);
      assert.throws(() => verifyPytestShards(data.directory, data.expected), undefined, `mutation ${index}`);
    } finally { fs.rmSync(data.directory, {recursive: true, force: true}); }
  }
  for (const mutation of [
    data => fs.rmSync(data.leaves[0].manifest),
    data => fs.mkdirSync(path.join(data.directory, "extra-artifact")),
    data => fs.writeFileSync(data.leaves[0].manifest, Buffer.from([0xff])),
    data => fs.writeFileSync(path.join(data.leaves[0].root, "python", ".local", "pytest.xml"), "changed"),
    data => fs.writeFileSync(path.join(data.leaves[0].root, "python", ".local", "pytest.xml"), Buffer.alloc(5_000_001)),
  ]) {
    const data = fixture();
    try { mutation(data); assert.throws(() => verifyPytestShards(data.directory, data.expected)); }
    finally { fs.rmSync(data.directory, {recursive: true, force: true}); }
  }
});
