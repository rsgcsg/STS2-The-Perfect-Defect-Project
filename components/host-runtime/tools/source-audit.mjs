#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { stageAssemblyFingerprintProject } from "../src/assembly-fingerprint-project.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function option(args, name, fallback = null) {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
}

function walk(directory, predicate, files = []) {
  for (const entry of readdirSync(directory)) {
    const file = path.join(directory, entry);
    if (statSync(file).isDirectory()) walk(file, predicate, files);
    else if (predicate(file)) files.push(file);
  }
  return files;
}

function sourceSummary(sourceRoot) {
  const files = walk(sourceRoot, (file) => file.endsWith(".cs"));
  const digest = createHash("sha256");
  const patterns = {
    force_steam: /force-steam/u,
    client_id: /clientId/u,
    user_data_path: /UserDataPathProvider/u,
    steam_initializer: /SteamInitializer/u,
    rng: /Random|Rng|RNG/u,
    async_or_task: /async|await|Task</u,
    godot_signal: /Connect\(|EmitSignal|Signal/u
  };
  const matches = Object.fromEntries(Object.keys(patterns).map((key) => [key, 0]));
  for (const file of files.sort()) {
    const relative = path.relative(sourceRoot, file).replaceAll("\\", "/");
    const content = readFileSync(file, "utf8");
    digest.update(relative).update("\0").update(content).update("\0");
    for (const [key, pattern] of Object.entries(patterns)) {
      if (pattern.test(content)) matches[key] += 1;
    }
  }
  return {
    source_file_count: files.length,
    source_tree_sha256: digest.digest("hex"),
    files_matching_semantic_edge: matches
  };
}

const args = process.argv.slice(2);
const assembly = path.resolve(option(args, "--assembly") ?? "");
if (!existsSync(assembly)) throw new Error("source-audit requires --assembly <existing sts2.dll>.");
const outputRoot = path.resolve(option(args, "--output", path.join(ROOT, ".local", "source-audit")));
mkdirSync(outputRoot, { recursive: true });

const staged = stageAssemblyFingerprintProject(ROOT);
let fingerprint;
try {
  fingerprint = spawnSync("dotnet", [
    "run", "--project", staged.project,
    "--configuration", "Release", "--", "--assembly", assembly
  ], { cwd: staged.directory, encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
} finally {
  staged.cleanup();
}
if (fingerprint.error) throw fingerprint.error;
if (fingerprint.status !== 0) throw new Error(fingerprint.stderr || "Assembly fingerprint failed.");
const inventory = JSON.parse(fingerprint.stdout);
const identity = inventory.assembly;
const evidenceDir = path.join(outputRoot, identity.sha256);
mkdirSync(evidenceDir, { recursive: true });
writeFileSync(path.join(evidenceDir, "assembly-inventory.json"), `${JSON.stringify(inventory, null, 2)}\n`);

const sourceRoot = path.join(evidenceDir, "decompiled");
if (!existsSync(sourceRoot)) {
  mkdirSync(sourceRoot, { recursive: true });
  const decompile = spawnSync("ilspycmd", ["--project", "--outputdir", sourceRoot, assembly], {
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024
  });
  if (decompile.status !== 0) throw new Error(decompile.stderr || "Exact assembly decompile failed.");
}

const report = {
  schema_version: 1,
  generated_at: new Date().toISOString(),
  evidence_scope: "local_exact_assembly_source_audit",
  assembly: identity,
  decompilation: {
    tool: "ilspycmd",
    ...sourceSummary(sourceRoot)
  },
  non_claims: [
    "Decompilation does not prove runtime behavior.",
    "Source-derived seams require exact-runtime tests before qualification.",
    "Generated source and inventories are local evidence and are not release artifacts."
  ]
};
const reportFile = path.join(evidenceDir, "report.json");
writeFileSync(reportFile, `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify({ report_file: reportFile, ...report }, null, 2));
