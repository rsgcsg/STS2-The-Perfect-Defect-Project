import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";
import { readCostProfile } from "./verify-pytest-shards.mjs";
import { buildProfile, junitFileMilliseconds } from "./refresh-pytest-costs.mjs";

const hash = bytes => crypto.createHash("sha256").update(bytes).digest("hex");

test("JUnit modules and nested classes map to the longest collected file", () => {
  const xml = `<testsuite><testcase classname="tests.test_a" name="x" time="1.5" />
    <testcase classname="tests.test_a.Nested" name="y" time="0.5"/>
    <testcase classname="tests.test_a_b" name="z" time="2" /></testsuite>`;
  assert.deepEqual([...junitFileMilliseconds(xml, new Set(["tests/test_a.py", "tests/test_a_b.py"]))],
    [["tests/test_a.py", 2000], ["tests/test_a_b.py", 2000]]);
  assert.throws(() => junitFileMilliseconds(xml, new Set(["tests/test_a.py"])), /outside the collection/u);
});

function leaf(index, count, files, { startedAt, pytestMinutes, workspaceMinutes, exit = 0 }) {
  const collection = files.map(([file]) => ({ file, nodeid: `${file}::test` }));
  const junit = Buffer.from(`<testsuite>${files.map(([file, seconds]) =>
    `<testcase classname="${file.replace(/\.py$/u, "").replaceAll("/", ".")}" name="test" time="${seconds}"/>`).join("")}</testsuite>`);
  const at = minutes => new Date(Date.parse(startedAt) + minutes * 60000).toISOString();
  return { junit, workspace: { started_at: startedAt, ended_at: at(workspaceMinutes) },
    manifest: { run: { run_id: "77", os: "Linux" }, shard: { index, count }, state: "completed",
      invocation: { exit_code: exit, started_at: at(0.5), ended_at: at(0.5 + pytestMinutes) },
      source_at_start: { head: "f".repeat(40) }, collection, collection_sha256: "a".repeat(64),
      junit_sha256: hash(junit), selected_nodeids: collection.map(item => item.nodeid),
      executed: collection.map(item => ({ nodeid: item.nodeid, finished: true })) } };
}

test("one whole passing run yields file medians and shard1's extra non-pytest time", () => {
  const { profile: previous } = readCostProfile();
  const started = "2026-10-27T13:56:00.000Z";
  const leaves = [
    leaf(2, 2, [["tests/test_b.py", 4]], { startedAt: started, pytestMinutes: 10, workspaceMinutes: 11 }),
    leaf(1, 2, [["tests/test_a.py", 0.0001]], { startedAt: started, pytestMinutes: 8, workspaceMinutes: 16 }),
  ];
  const profile = buildProfile(leaves, previous);
  assert.deepEqual(profile.file_cost_ms, { "tests/test_a.py": 1, "tests/test_b.py": 4000 });
  assert.equal(profile.first_shard_overhead_ms, 7 * 60000, "(16-8) minus the pytest-only leaf's (11-10)");
  assert.deepEqual(profile.sources[0].map(source => [source.shard, source.run_id, source.all_selected_terminal]),
    [[1, "77", true], [2, "77", true]]);
  // A missing shard, a failed shard or altered JUnit is never a cost source.
  assert.throws(() => buildProfile([leaves[1]], previous), /every Linux shard/u);
  const failed = leaf(2, 2, [["tests/test_b.py", 4]], { startedAt: started, pytestMinutes: 10, workspaceMinutes: 11, exit: 1 });
  assert.throws(() => buildProfile([leaves[1], failed], previous), /completed passing/u);
  assert.throws(() => buildProfile([leaves[1], { ...leaves[0], junit: Buffer.from("<x/>") }], previous), /completed passing/u);
});
