import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import test from "node:test";
import {verifyPytestShards, readCostProfile, selectedNodeids, validatedCostProfile} from "./verify-pytest-shards.mjs";
import {workspaceStages, pytestPartition, PYTEST_SHARD_COUNT, PYTEST_SHARD_INDEXES} from "./check-workspace.mjs";

const hash = bytes => crypto.createHash("sha256").update(bytes).digest("hex");
const write = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, JSON.stringify(value)); };

// Validator fixtures model completed owner stages; they are not full-suite receipts.
function fixture() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "pytest-proof-"));
  const directory = path.join(temporary, "artifacts");
  fs.mkdirSync(directory);
  const profileRoot = temporary;
  const profile = structuredClone(readCostProfile().profile);
  profile.first_shard_overhead_ms = 0;
  write(path.join(profileRoot, "python/tools/pytest-file-costs.json"), profile);
  const expected = {source: {head: "a".repeat(40), tree: "b".repeat(40), workflow: "c".repeat(40),
    selector: "d".repeat(40), cost_profile: "e".repeat(40),
    cost_profile_sha256: readCostProfile(profileRoot).sha256, routing_algorithm: "lpt-whole-file-gates-1", dirty: false}, repository: "owner/repo", run_id: "123", run_attempt: "1", scope: "full"};
  const collection = ["deploy/hub/test_a.py", "tests/test_b.py", "tests/test_c.py"].flatMap(file =>
    ["one", "two"].map(value => ({file, nodeid: `${file}::test_value[${value}]`})));
  const leaves = [];
  for (const os of ["Linux", "Windows"]) for (const index of PYTEST_SHARD_INDEXES) {
    const root = path.join(directory, `pytest-${os}-shard-${index}-1`);
    const selected = selectedNodeids(collection, index, profile);
    const report = {schema: "spireagent/pytest-shard-2", run: {repository: expected.repository,
      run_id: "123", run_attempt: "1", job: os === "Linux" ? "linux-portability" : "windows-portability", os, scope: "full"},
      shard: {index, count: PYTEST_SHARD_COUNT}, source_at_start: expected.source, source_at_end: expected.source,
      invocation: {argv: ["python", "-m", "pytest", "-p", "tools.pytest_shard", `--portable-shard-index=${index}`, `--portable-shard-count=${PYTEST_SHARD_COUNT}`],
        pid: index, started_at: "2026-01-01T00:00:00Z", ended_at: "2026-01-01T00:00:01Z", exit_code: 0},
      collection, collection_sha256: hash(JSON.stringify(collection)), selected_nodeids: selected,
      executed: selected.map(nodeid => ({nodeid, finished: true, status: "passed", phases:
        ["setup", "call", "teardown"].map(when => ({when, outcome: "passed", subtest: false, wasxfail: null}))})),
      errors: [], collection_errors: [], state: "completed", junit_sha256: hash("<testsuite/>\n")};
    const identity = {head: expected.source.head, tree: expected.source.tree, dirty: false};
    const workspace = {schema: "spireagent/portable-stage-results-1", scope: "full",
      platform: os === "Linux" ? "linux" : "win32", verdict: "passed", source_accepted: true,
      interrupted_signal: null, source_at_start: identity, source_at_end: identity,
      pytest_partition: pytestPartition(`${index}/${PYTEST_SHARD_COUNT}`),
      results: workspaceStages("full", {pytestShard: `${index}/${PYTEST_SHARD_COUNT}`}).map(stage =>
        ({id: stage.id, args: stage.args, status: "passed", exit_code: 0, signal: null}))};
    const manifest = path.join(root, "python", ".local", "pytest-shard.json");
    const stageFile = path.join(root, ".local", "checks", `workspace-full-${workspace.platform}.json`);
    write(manifest, report); write(stageFile, workspace);
    fs.writeFileSync(path.join(root, "python", ".local", "pytest.xml"), "<testsuite/>\n");
    leaves.push({root, manifest, stageFile, report, workspace});
  }
  return {directory, temporary, profileRoot, expected, leaves};
}

test("complete deterministic same-OS selections seal a small digest/count proof", () => {
  const data = fixture();
  try {
    const result = verifyPytestShards(data.directory, data.expected, {profileRoot: data.profileRoot});
    assert.equal(result.complete, true);
    assert.equal(result.leaves.length, 2 * PYTEST_SHARD_COUNT);
    assert.equal(result.leaves.reduce((total, leaf) => total + leaf.selected, 0), 12);
    // The same 500-byte-per-leaf budget as the original 4-leaf/2000-byte bound.
    assert.ok(JSON.stringify(result).length < 500 * result.leaves.length);
    // Existing per-test skips are retained, not a whole-shard skip.
    const item = data.leaves[0].report.executed[0];
    item.phases = ["setup", "teardown"].map(when => ({when, outcome: when === "setup" ? "skipped" : "passed", subtest: false, wasxfail: null}));
    item.status = "skipped";
    write(data.leaves[0].manifest, data.leaves[0].report);
    assert.equal(verifyPytestShards(data.directory, data.expected, {profileRoot: data.profileRoot}).leaves[0].skipped, 1);
  } finally { fs.rmSync(data.temporary, {recursive: true, force: true}); }
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
    leaf => leaf.report.schema = "spireagent/pytest-shard-1",
    leaf => leaf.report.source_at_start = {...leaf.report.source_at_start, cost_profile: "f".repeat(40)},
    leaf => leaf.report.source_at_start = {...leaf.report.source_at_start, cost_profile_sha256: "0".repeat(64)},
    leaf => leaf.report.source_at_start = {...leaf.report.source_at_start, routing_algorithm: "rank-2"},
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
      assert.throws(() => verifyPytestShards(data.directory, data.expected, {profileRoot: data.profileRoot}), undefined, `mutation ${index}`);
    } finally { fs.rmSync(data.temporary, {recursive: true, force: true}); }
  }
  for (const mutation of [
    data => fs.rmSync(data.leaves[0].manifest),
    data => fs.mkdirSync(path.join(data.directory, "extra-artifact")),
    data => fs.writeFileSync(data.leaves[0].manifest, Buffer.from([0xff])),
    data => fs.writeFileSync(path.join(data.leaves[0].root, "python", ".local", "pytest.xml"), "changed"),
    data => fs.writeFileSync(path.join(data.leaves[0].root, "python", ".local", "pytest.xml"), Buffer.alloc(5_000_001)),
  ]) {
    const data = fixture();
    try { mutation(data); assert.throws(() => verifyPytestShards(data.directory, data.expected, {profileRoot: data.profileRoot})); }
    finally { fs.rmSync(data.temporary, {recursive: true, force: true}); }
  }
});

test("integer LPT keeps whole files, unknown files and UTF-8 ties deterministic", () => {
  const profile = structuredClone(readCostProfile().profile);
  profile.first_shard_overhead_ms = 0;
  profile.file_cost_ms = {"tests/test_a.py": 100000, "tests/test_b.py": 80000,
    "tests/test_c.py": 20000, "tests/test_d.py": 10000, "tests/retired.py": 999999000};
  const collection = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "tests/test_d.py", "tests/new.py"]
    .flatMap(file => ["test_one", "OtherClass::test_two[param]"].map(name => ({file, nodeid: `${file}::${name}`})));
  const [first, second, third] = PYTEST_SHARD_INDEXES.map(index => selectedNodeids(collection, index, profile));
  const files = ids => [...new Set(ids.map(id => id.split("::")[0]))];
  assert.deepEqual([files(first), files(second), files(third)],
    [["tests/test_a.py"], ["tests/test_b.py"], ["tests/new.py", "tests/test_c.py", "tests/test_d.py"]]);
  assert.deepEqual([...first, ...second, ...third].sort(), collection.map(item => item.nodeid).sort());
  assert.deepEqual(selectedNodeids([...collection].reverse(), 1, profile), first);
  profile.file_cost_ms = {"tests/é.py": 50, "tests/z.py": 50};
  assert.deepEqual(selectedNodeids(Object.keys(profile.file_cost_ms).map(file => ({file, nodeid: `${file}::test_case`})), 1, profile),
    ["tests/z.py::test_case"]);
  profile.first_shard_overhead_ms = 100000;
  profile.file_cost_ms = {"tests/a.py": 200000, "tests/b.py": 180000, "tests/c.py": 100000};
  const weighted = Object.keys(profile.file_cost_ms).map(file => ({file, nodeid: `${file}::test_case`}));
  // Shard1 starts at its measured non-pytest overhead, so it takes the smallest file here.
  assert.deepEqual(PYTEST_SHARD_INDEXES.map(index => selectedNodeids(weighted, index, profile)),
    [["tests/c.py::test_case"], ["tests/a.py::test_case"], ["tests/b.py::test_case"]]);
  assert.throws(() => selectedNodeids(weighted, PYTEST_SHARD_COUNT + 1, profile), /selection_index/);
  assert.throws(() => pytestPartition("2/2"), /invalid_pytest_shard/);
  assert.throws(() => pytestPartition(`${PYTEST_SHARD_COUNT + 1}/${PYTEST_SHARD_COUNT}`), /invalid_pytest_shard/);
});

test("cost provenance may come from a run with another shard count, but only one whole run", () => {
  const profile = structuredClone(readCostProfile().profile);
  const [group] = profile.sources;
  assert.ok(group.length >= 1);
  assert.doesNotThrow(() => validatedCostProfile({...profile, sources: [group.slice(0, 1)]}));
  for (const sources of [[[]], [[{...group[0], shard: 2}]], [[group[0], {...group[0], shard: 2, run_id: "1"}]]])
    assert.throws(() => validatedCostProfile({...profile, sources}));
});

test("invalid cost definitions never fall back to an unverified assignment", () => {
  for (const mutation of [
    p => p.schema = "unknown", p => p.algorithm = "rank-2", p => p.unknown_file_ms = 0,
    p => p.extra = "ignored", p => p.first_shard_overhead_ms = -1, p => p.file_cost_ms = {}, p => p.file_cost_ms = {"../foreign.py": 1},
    ...[true, 1.5, -1, Infinity, Number.MAX_SAFE_INTEGER + 1].map(value => p => p.file_cost_ms = {"tests/x.py": value}),
    p => p.file_cost_ms = {"tests/x.py": Number.MAX_SAFE_INTEGER, "tests/y.py": 1},
    p => p.sources[0][0].all_selected_terminal = false, p => p.sources[0][0].junit_sha256 = "foreign",
  ]) {
    const profile = structuredClone(readCostProfile().profile); mutation(profile);
    assert.throws(() => validatedCostProfile(profile));
  }
});

test("an exhaustive whole-file swap and a foreign cost definition cannot seal coverage", () => {
  const data = fixture();
  try {
    for (let offset = 0; offset < data.leaves.length; offset += PYTEST_SHARD_COUNT) {
      const first = structuredClone(data.leaves[offset].report);
      const second = structuredClone(data.leaves[offset + 1].report);
      for (const [leaf, other] of [[data.leaves[offset], second], [data.leaves[offset + 1], first]]) {
        leaf.report.selected_nodeids = other.selected_nodeids;
        leaf.report.executed = other.executed;
        write(leaf.manifest, leaf.report);
      }
    }
    assert.throws(() => verifyPytestShards(data.directory, data.expected, {profileRoot: data.profileRoot}), /deterministic_selection/);
  } finally { fs.rmSync(data.temporary, {recursive: true, force: true}); }
  const other = fixture();
  try {
    const foreign = structuredClone(other.expected);
    foreign.source.cost_profile_sha256 = "0".repeat(64);
    assert.throws(() => verifyPytestShards(other.directory, foreign, {profileRoot: other.profileRoot}), /cost_profile_identity/);
    foreign.source.cost_profile_sha256 = other.expected.source.cost_profile_sha256;
    foreign.source.routing_algorithm = "rank-2";
    assert.throws(() => verifyPytestShards(other.directory, foreign, {profileRoot: other.profileRoot}), /cost_profile_identity/);
  } finally { fs.rmSync(other.temporary, {recursive: true, force: true}); }
});
