// Verify original current-run coverage before the existing aggregate seals a receipt.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import {execFileSync} from "node:child_process";
import {workspaceStages, pytestPartition} from "./check-workspace.mjs";

const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const hash = bytes => crypto.createHash("sha256").update(bytes).digest("hex");
const sort = values => [...values].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
const requireProof = (condition, reason) => { if (!condition) throw new Error(`pytest_shards:${reason}`); };

const profilePath = "python/tools/pytest-file-costs.json";
const algorithm = "lpt-whole-file-gates-1";
const defaultRoot = path.resolve(import.meta.dirname, "..");
const exactKeys = (value, keys) => value && typeof value === "object" && !Array.isArray(value) &&
  equal(sort(Object.keys(value)), sort(keys));

export function validatedCostProfile(profile) {
  requireProof(exactKeys(profile, ["schema", "algorithm", "unknown_file_ms", "aggregation", "sources", "file_cost_ms", "first_shard_overhead_ms"]) &&
    profile.schema === "spireagent/pytest-file-cost-2" && profile.algorithm === algorithm &&
    profile.unknown_file_ms === 30000 && profile.aggregation ===
      "median-retained-Linux-testcase-total-ms-min1", "cost_profile_definition");
  requireProof(Number.isSafeInteger(profile.first_shard_overhead_ms) && profile.first_shard_overhead_ms >= 0,
    "first_shard_overhead");
  const costs = profile.file_cost_ms;
  requireProof(costs && typeof costs === "object" && !Array.isArray(costs) && Object.keys(costs).length > 0,
    "cost_profile_empty");
  for (const [file, weight] of Object.entries(costs)) requireProof(
    /^(?:tests|deploy\/hub)\//u.test(file) && file.endsWith(".py") && !file.includes("\\") &&
    !file.includes(":") && !file.split("/").some(part => ["", ".", ".."].includes(part)) &&
    Number.isSafeInteger(weight) && weight > 0, "cost_profile_weight");
  requireProof(Number.isSafeInteger(Object.values(costs).reduce((sum, value) => sum + value, profile.first_shard_overhead_ms)),
    "cost_profile_sum");
  requireProof(Array.isArray(profile.sources) && profile.sources.length > 0, "cost_profile_sources");
  for (const group of profile.sources) {
    requireProof(Array.isArray(group) && group.length === 2, "cost_profile_source_pair");
    for (const [offset, source] of group.entries()) requireProof(
      exactKeys(source, ["run_id", "os", "shard", "execution_checkout", "collection_count", "collection_sha256",
        "junit_sha256", "exit_code", "all_selected_terminal"]) &&
      typeof source.run_id === "string" && /^[0-9]+$/u.test(source.run_id) && source.os === "Linux" &&
      source.shard === offset + 1 && Number.isSafeInteger(source.collection_count) && source.collection_count > 0 &&
      typeof source.execution_checkout === "string" && /^[0-9a-f]{40}$/u.test(source.execution_checkout) && typeof source.collection_sha256 === "string" && /^[0-9a-f]{64}$/u.test(source.collection_sha256) &&
      typeof source.junit_sha256 === "string" && /^[0-9a-f]{64}$/u.test(source.junit_sha256) && [0, 1].includes(source.exit_code) &&
      source.all_selected_terminal === true, "cost_profile_source_identity");
    requireProof(group[0].run_id === group[1].run_id, "cost_profile_source_run");
  }
  return profile;
}

export function readCostProfile(root = defaultRoot) {
  const file = path.join(root, profilePath), stat = fs.lstatSync(file);
  requireProof(stat.isFile() && !stat.isSymbolicLink() && stat.size <= 1_000_000, "cost_profile_file");
  const bytes = fs.readFileSync(file);
  return {profile: validatedCostProfile(JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes))),
    sha256: hash(bytes)};
}

export function selectedNodeids(collection, index, profile) {
  requireProof([1, 2].includes(index), "selection_index");
  validatedCostProfile(profile);
  const costs = new Map(Object.entries(profile.file_cost_ms));
  const files = [...new Set(collection.map(item => item.file))];
  const weight = file => costs.get(file) ?? profile.unknown_file_ms;
  requireProof(Number.isSafeInteger(files.reduce((sum, file) => sum + weight(file), profile.first_shard_overhead_ms)), "selection_sum");
  files.sort((left, right) => weight(right) - weight(left) || Buffer.compare(Buffer.from(left), Buffer.from(right)));
  const bins = [new Set(), new Set()], loads = [profile.first_shard_overhead_ms, 0];
  for (const file of files) {
    const target = loads[0] <= loads[1] ? 0 : 1;
    bins[target].add(file); loads[target] += weight(file);
  }
  return sort(collection.filter(item => bins[index - 1].has(item.file)).map(item => item.nodeid));
}

export function currentShardIdentity(root, environment) {
  const git = (...args) => execFileSync("git", args, {cwd: root, encoding: "utf8"}).trim();
  const source = {head: git("rev-parse", "HEAD"), tree: git("rev-parse", "HEAD^{tree}"),
    workflow: git("rev-parse", "HEAD:.github/workflows/ci.yml"),
    selector: git("rev-parse", "HEAD:python/tools/pytest_shard.py"),
    cost_profile: git("rev-parse", `HEAD:${profilePath}`),
    cost_profile_sha256: readCostProfile(root).sha256, routing_algorithm: algorithm,
    dirty: git("status", "--porcelain", "--untracked-files=normal").length > 0};
  requireProof(!source.dirty && source.head === environment.GITHUB_SHA, "aggregate_source");
  return {source, repository: environment.GITHUB_REPOSITORY, run_id: environment.GITHUB_RUN_ID,
    run_attempt: environment.GITHUB_RUN_ATTEMPT, scope: environment.CHECK_SCOPE};
}

function readBytes(file) {
  const stat = fs.lstatSync(file);
  requireProof(stat.isFile() && !stat.isSymbolicLink() && stat.size <= 5_000_000, "artifact_file");
  return fs.readFileSync(file);
}

function readJson(file) {
  const bytes = readBytes(file);
  return {data: JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes)), sha256: hash(bytes)};
}

function idsMatch(values, expected, reason) {
  requireProof(Array.isArray(values) && values.every(value => typeof value === "string") &&
    new Set(values).size === values.length && equal(sort(values), expected), reason);
}

function verifiedCollection(report) {
  const collection = report.collection;
  requireProof(Array.isArray(collection) && collection.length > 0, "empty_collection");
  for (const item of collection) {
    requireProof(typeof item.nodeid === "string" && typeof item.file === "string" &&
      item.nodeid.startsWith(item.file + "::") && item.nodeid.length > item.file.length + 2 &&
      !item.file.includes("\\") && !item.file.includes(":") && !item.file.startsWith("/") &&
      !item.file.split("/").some(part => ["", ".", ".."].includes(part)) &&
      (item.file.startsWith("tests/") || item.file.startsWith("deploy/hub/")), "node_id");
  }
  const ids = sort(collection.map(item => item.nodeid));
  requireProof(new Set(ids).size === ids.length && equal(collection.map(item => item.nodeid), ids), "duplicate_or_unsorted_collection");
  requireProof(hash(Buffer.from(JSON.stringify(collection))) === report.collection_sha256, "collection_digest");
  return collection;
}

function verifyExecuted(report, selected) {
  requireProof(Array.isArray(report.executed), "missing_execution");
  idsMatch(report.executed.map(item => item.nodeid), selected, "executed_coverage");
  let passed = 0, skipped = 0;
  for (const item of report.executed) {
    requireProof(item.finished === true && Array.isArray(item.phases), "unfinished_item");
    for (const phase of item.phases) requireProof(
      ["setup", "call", "teardown"].includes(phase.when) && ["passed", "skipped", "failed"].includes(phase.outcome) &&
      typeof phase.subtest === "boolean" && (phase.wasxfail === null || typeof phase.wasxfail === "string") &&
      (!phase.subtest || phase.when === "call"), "invalid_item_phase");
    const ordinary = item.phases.filter(phase => !phase.subtest);
    const expected = ordinary[0]?.outcome === "passed" ? ["setup", "call", "teardown"] : ["setup", "teardown"];
    requireProof(equal(ordinary.map(phase => phase.when), expected), "incomplete_or_duplicate_phases");
    requireProof(!item.phases.some(phase => phase.outcome === "failed"), "failed_item_or_subtest");
    const status = ordinary.some(phase => phase.outcome === "skipped") ? "skipped" : "passed";
    requireProof(item.status === status, "item_status");
    if (status === "passed") passed++; else skipped++;
  }
  requireProof(passed > 0, "wholly_skipped_shard");
  return {passed, skipped};
}

function verifyWorkspace(report, expected, os, index) {
  const platform = {Linux: "linux", Windows: "win32", Darwin: "darwin"}[os];
  const identity = {head: expected.source.head, tree: expected.source.tree, dirty: false};
  requireProof(report.schema === "spireagent/portable-stage-results-1" && report.scope === expected.scope &&
    report.platform === platform && report.verdict === "passed" && report.source_accepted === true &&
    report.interrupted_signal === null && equal(report.source_at_start, identity) && equal(report.source_at_end, identity) &&
    equal(report.pytest_partition, pytestPartition(`${index}/2`)), "workspace_identity_or_result");
  const stages = workspaceStages(expected.scope, {pytestShard: `${index}/2`});
  requireProof(Array.isArray(report.results) && report.results.length === stages.length, "workspace_inventory");
  for (const [position, stage] of stages.entries()) {
    const actual = report.results[position];
    requireProof(actual.id === stage.id && equal(actual.args, stage.args) && actual.status === "passed" &&
      actual.exit_code === 0 && actual.signal === null && !actual.error && !actual.blocked_by,
      `workspace_stage_${stage.id}`);
  }
}

export function verifyPytestShards(directory, expected, {oses = ["Linux", "Windows"], profileRoot = defaultRoot} = {}) {
  const cost = readCostProfile(profileRoot);
  requireProof(expected.source?.cost_profile_sha256 === cost.sha256 &&
    /^[0-9a-f]{40}$/u.test(expected.source?.cost_profile ?? "") &&
    expected.source?.routing_algorithm === algorithm, "cost_profile_identity");
  requireProof(["full", "python"].includes(expected.scope) && /^[\w.-]+\/[\w.-]+$/.test(expected.repository ?? "") &&
    /^[1-9][0-9]*$/.test(expected.run_id ?? "") && /^[1-9][0-9]*$/.test(expected.run_attempt ?? ""), "expected_run");
  const names = oses.flatMap(os => [1, 2].map(index => `pytest-${os}-shard-${index}-${expected.run_attempt}`));
  requireProof(equal(sort(fs.readdirSync(directory)), sort(names)), "missing_or_extra_artifact");
  const proofs = [], collections = new Map(), selections = new Map();
  for (const os of oses) for (const index of [1, 2]) {
    const name = `pytest-${os}-shard-${index}-${expected.run_attempt}`;
    const artifact = path.join(directory, name);
    requireProof(fs.lstatSync(artifact).isDirectory() && !fs.lstatSync(artifact).isSymbolicLink(), "artifact_directory");
    const manifest = readJson(path.join(artifact, "python", ".local", "pytest-shard.json"));
    const report = manifest.data;
    const run = {repository: expected.repository, run_id: expected.run_id, run_attempt: expected.run_attempt,
      job: os === "Linux" ? "linux-portability" : os === "Windows" ? "windows-portability" : "fixture",
      os, scope: expected.scope};
    requireProof(report.schema === "spireagent/pytest-shard-2" && equal(report.run, run) &&
      equal(report.shard, {index, count: 2}) && equal(report.source_at_start, expected.source) &&
      equal(report.source_at_end, expected.source) && report.state === "completed" &&
      Array.isArray(report.errors) && report.errors.length === 0 &&
      Array.isArray(report.collection_errors) && report.collection_errors.length === 0, "leaf_identity_or_result");
    const invocation = report.invocation;
    requireProof(invocation?.exit_code === 0 && Number.isInteger(invocation.pid) && invocation.pid > 0 &&
      Number.isFinite(Date.parse(invocation.started_at)) && Date.parse(invocation.ended_at) >= Date.parse(invocation.started_at) &&
      Array.isArray(invocation.argv) && invocation.argv[1] === "-m" && invocation.argv[2] === "pytest" &&
      invocation.argv.includes(`--portable-shard-index=${index}`) && invocation.argv.includes("--portable-shard-count=2") &&
      invocation.argv.includes("tools.pytest_shard"), "invocation");
    const collection = verifiedCollection(report);
    if (collections.has(os)) requireProof(equal(collection, collections.get(os)), "same_os_collection_changed");
    else collections.set(os, collection);
    const selected = selectedNodeids(collection, index, cost.profile);
    requireProof(selected.length > 0, "empty_selection");
    idsMatch(report.selected_nodeids, selected, "deterministic_selection");
    const counts = verifyExecuted(report, selected);
    const earlier = selections.get(os) ?? [];
    requireProof(!selected.some(id => earlier.includes(id)), "overlapping_selection");
    selections.set(os, [...earlier, ...selected]);
    const junit = readBytes(path.join(artifact, "python", ".local", "pytest.xml"));
    requireProof(hash(junit) === report.junit_sha256, "junit_digest");
    const workspace = readJson(path.join(artifact, ".local", "checks", `workspace-${expected.scope}-${{Linux: "linux", Windows: "win32", Darwin: "darwin"}[os]}.json`));
    verifyWorkspace(workspace.data, expected, os, index);
    proofs.push({os, index, manifest_sha256: manifest.sha256, workspace_sha256: workspace.sha256,
      collection_sha256: report.collection_sha256, selected: selected.length, ...counts});
  }
  for (const os of oses) idsMatch(selections.get(os), collections.get(os).map(item => item.nodeid), "exhaustive_union");
  return {schema: "spireagent/pytest-coverage-2", complete: true, shards_per_os: 2,
    routing: {algorithm, cost_profile: expected.source.cost_profile, sha256: cost.sha256}, leaves: proofs};
}
