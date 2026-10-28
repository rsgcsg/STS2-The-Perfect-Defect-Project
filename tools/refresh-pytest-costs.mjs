#!/usr/bin/env node
// Rebuild python/tools/pytest-file-costs.json from one retained, completed Linux CI
// run. The profile is a scheduling observation, never a receipt; check:ci and the
// independent Python/JS selectors still validate it before any shard uses it.
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { validatedCostProfile } from "./verify-pytest-shards.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PROFILE = path.join(ROOT, "python", "tools", "pytest-file-costs.json");
const hash = bytes => crypto.createHash("sha256").update(bytes).digest("hex");
const median = values => { const sorted = [...values].sort((a, b) => a - b), middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : Math.round((sorted[middle - 1] + sorted[middle]) / 2); };
const elapsed = report => Date.parse(report.ended_at) - Date.parse(report.started_at);

// JUnit names a module (plus classes) rather than a file; the longest collected
// module prefix owns the testcase.
export function junitFileMilliseconds(xml, files) {
  const modules = [...files].map(file => [file.replace(/\.py$/u, "").replaceAll("/", "."), file])
    .sort((left, right) => right[0].length - left[0].length);
  const totals = new Map();
  for (const [, attributes] of xml.matchAll(/<testcase\b([^>]*)>/gu)) {
    const classname = /\bclassname="([^"]*)"/u.exec(attributes)?.[1] ?? "";
    const seconds = Number(/\btime="([^"]*)"/u.exec(attributes)?.[1] ?? 0);
    const owner = modules.find(([module]) => classname === module || classname.startsWith(`${module}.`));
    if (!owner) throw new Error(`junit testcase outside the collection: ${classname}`);
    totals.set(owner[1], (totals.get(owner[1]) ?? 0) + seconds * 1000);
  }
  return totals;
}

// leaves: [{ manifest, junit (bytes), workspace }] for every shard of one Linux run.
export function buildProfile(leaves, previous) {
  const ordered = [...leaves].sort((left, right) => left.manifest.shard.index - right.manifest.shard.index);
  const runs = new Set(ordered.map(leaf => leaf.manifest.run.run_id));
  if (runs.size !== 1 || ordered.some((leaf, offset) => leaf.manifest.shard.index !== offset + 1
    || leaf.manifest.shard.count !== ordered.length || leaf.manifest.run.os !== "Linux"))
    throw new Error("cost source must be every Linux shard of one run");
  const costs = new Map();
  for (const leaf of ordered) {
    const { manifest } = leaf;
    if (manifest.state !== "completed" || manifest.invocation.exit_code !== 0 || hash(leaf.junit) !== manifest.junit_sha256)
      throw new Error(`shard ${manifest.shard.index} is not a completed passing original`);
    const files = new Set(manifest.collection.map(item => item.file));
    for (const [file, milliseconds] of junitFileMilliseconds(leaf.junit.toString("utf8"), files))
      costs.set(file, [...(costs.get(file) ?? []), milliseconds]);
  }
  const file_cost_ms = Object.fromEntries([...costs].sort(([left], [right]) => Buffer.compare(Buffer.from(left), Buffer.from(right)))
    .map(([file, values]) => [file, Math.max(1, Math.round(median(values)))]));
  // Shard1's extra wall time over the mean pytest-only leaf is its non-pytest gate load.
  const nonPytest = leaf => elapsed(leaf.workspace) - elapsed(leaf.manifest.invocation);
  const others = ordered.slice(1).map(nonPytest);
  const first_shard_overhead_ms = Math.max(0, Math.round(nonPytest(ordered[0]) -
    (others.length ? others.reduce((sum, value) => sum + value, 0) / others.length : 0)));
  return validatedCostProfile({ schema: previous.schema, algorithm: previous.algorithm,
    unknown_file_ms: previous.unknown_file_ms, aggregation: previous.aggregation,
    sources: [ordered.map(({ manifest }) => ({ run_id: manifest.run.run_id, os: "Linux", shard: manifest.shard.index,
      execution_checkout: manifest.source_at_start.head, collection_count: manifest.collection.length,
      collection_sha256: manifest.collection_sha256, junit_sha256: manifest.junit_sha256,
      exit_code: manifest.invocation.exit_code,
      all_selected_terminal: manifest.executed.length === manifest.selected_nodeids.length
        && manifest.executed.every(item => item.finished === true) }))],
    file_cost_ms, first_shard_overhead_ms });
}

function readLeaves(directory) {
  return fs.readdirSync(directory).filter(name => /^pytest-Linux-shard-\d+-\d+$/u.test(name)).map(name => {
    const leaf = path.join(directory, name);
    return { manifest: JSON.parse(fs.readFileSync(path.join(leaf, "python", ".local", "pytest-shard.json"), "utf8")),
      junit: fs.readFileSync(path.join(leaf, "python", ".local", "pytest.xml")),
      workspace: JSON.parse(fs.readFileSync(path.join(leaf, ".local", "checks", "workspace-full-linux.json"), "utf8")) };
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const run = process.argv[2];
  if (!/^\d+$/u.test(run ?? "")) throw new Error("usage: npm run ci:pytest-costs -- <successful full-scope CI run id>");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "pytest-costs-"));
  try {
    execFileSync("gh", ["run", "download", run, "-p", "pytest-Linux-shard-*", "-D", directory], { stdio: "inherit" });
    const previous = JSON.parse(fs.readFileSync(PROFILE, "utf8"));
    const profile = buildProfile(readLeaves(directory), previous);
    fs.writeFileSync(PROFILE, `${JSON.stringify(profile, null, 2)}\n`);
    const total = Object.values(profile.file_cost_ms).reduce((sum, value) => sum + value, 0);
    process.stdout.write(`${JSON.stringify({ run, files: Object.keys(profile.file_cost_ms).length,
      pytest_minutes: +(total / 60000).toFixed(1), first_shard_overhead_minutes: +(profile.first_shard_overhead_ms / 60000).toFixed(1),
      next: "npm run check:ci, then commit the profile" })}\n`);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
}
