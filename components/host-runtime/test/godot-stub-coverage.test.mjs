import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { stageDotnetToolProject } from "../src/assembly-fingerprint-project.mjs";
import { discoverGameDirectory, readDiskIdentity, resolveInstallation } from "../src/game-installation.mjs";
import { checkGodotStubCoverage, godotStubCoverageContract } from "../src/godot-stub-coverage.mjs";
import { loadManagedCandidateManifest, resolveDotnet } from "../src/managed-candidate.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CATEGORIES = new Set(["display", "tooling", "platform", "engine_bridge", "unreferenced",
  "save_file_io", "telemetry", "text_rendering", "locale_engine", "input_events", "resource_loader"]);

function reviewedBaseline(manifest) {
  return JSON.parse(readFileSync(path.join(ROOT, "experiments", "managed-exact",
    godotStubCoverageContract(manifest).baseline), "utf8"));
}

test("reviewed Godot stub baseline lists only categorized Godot API gaps for the admitted game", () => {
  const { manifest } = loadManagedCandidateManifest(ROOT);
  const contract = godotStubCoverageContract(manifest);
  assert.equal(contract.gate, "local_only_prepare");
  assert.deepEqual(contract.scanned_game_files.filter(name => !manifest.required_game_files.includes(name)), []);
  const baseline = reviewedBaseline(manifest);
  assert.equal(baseline.schema, "spireagent.host-runtime/godot-stub-coverage-baseline-1");
  assert.deepEqual(baseline.exact_game, {
    sts2_dll_sha256: manifest.exact_game.sts2_dll_sha256,
    godotsharp_dll_sha256: manifest.exact_game.godotsharp_dll_sha256
  });
  const keys = baseline.accepted_gaps.map(gap => gap.key);
  assert.equal(new Set(keys).size, keys.length);
  const counts = {};
  for (const gap of baseline.accepted_gaps) {
    // Keys name Godot API types/members only: no game namespaces or source text.
    assert.match(gap.key, /^(type|member|not_implemented) Godot\./u);
    assert.doesNotMatch(gap.key, /MegaCrit/u);
    for (const part of gap.category.split("+")) assert.ok(CATEGORIES.has(part), gap.category);
    assert.equal(typeof baseline.category_reasons[gap.category], "string");
    counts[gap.category] = (counts[gap.category] ?? 0) + 1;
  }
  assert.deepEqual(counts, baseline.category_counts);
  // Logic-namespace gaps are listed by reason, never folded into display.
  const saveIo = baseline.accepted_gaps.filter(gap => gap.category === "save_file_io");
  assert.ok(saveIo.length > 0 && saveIo.every(gap => /Godot\.(DirAccess|FileAccess)::/u.test(gap.key)));
  for (const filled of ["member Godot.Node::MoveChild instance void (Godot.Node, int32)",
    "member Godot.StringExtensions::Capitalize string (string)",
    "member Godot.GodotObject::Connect instance Godot.Error (Godot.StringName, Godot.Callable, uint32)",
    "type Godot.SceneTreeTimer/SignalName"]) {
    assert.equal(keys.includes(filled), false, `${filled} is bound by the M2c stub`);
  }
});

test("coverage tool source ships with the package and stages outside it", () => {
  const packageJson = JSON.parse(readFileSync(path.join(ROOT, "package.json"), "utf8"));
  for (const name of ["GodotStubCoverage.csproj", "Program.cs"]) {
    assert.ok(packageJson.files.includes(`tools/dotnet/GodotStubCoverage/${name}`));
  }
  const staged = stageDotnetToolProject(ROOT, "GodotStubCoverage");
  try {
    assert.equal(staged.directory.startsWith(`${ROOT}${path.sep}`), false);
    assert.deepEqual(readFileSync(staged.project),
      readFileSync(path.join(ROOT, "tools", "dotnet", "GodotStubCoverage", "GodotStubCoverage.csproj")));
  } finally {
    staged.cleanup();
  }
  assert.equal(existsSync(staged.directory), false);
});

test("every Managed prepare runs the coverage gate after the exact build check", () => {
  const source = readFileSync(path.join(ROOT, "src", "managed-candidate.mjs"), "utf8");
  const prepare = source.slice(source.indexOf("export async function prepareManagedCandidate("),
    source.indexOf("export async function startManagedCandidateRuntime("));
  const inspect = prepare.indexOf("await inspectManagedCandidateBuild(");
  const gate = prepare.indexOf("await checkGodotStubCoverage(");
  assert.ok(inspect >= 0 && gate > inspect);
  assert.ok(prepare.indexOf("writeFileSync(reportFile") > gate, "no prepare report is written for a failed gate");
  assert.match(prepare, /referenceAssembly: diskIdentity\.godotsharp_assembly\.path/u);
});

test("coverage gate refuses a baseline reviewed for another game", async () => {
  const { manifest } = loadManagedCandidateManifest(ROOT);
  const foreign = { ...manifest, exact_game: { ...manifest.exact_game, sts2_dll_sha256: "0".repeat(64) } };
  await assert.rejects(checkGodotStubCoverage({ root: ROOT, candidateDirectory: "/nonexistent", manifest: foreign,
    referenceAssembly: "/nonexistent/GodotSharp.dll", outputFile: "/nonexistent/out.json", dotnet: { command: "false" } }),
  /reviewed for a different exact game/u);
});

test("coverage gate refuses a report whose game references do not all resolve in shipped Godot", async () => {
  const { manifest } = loadManagedCandidateManifest(ROOT);
  const directory = mkdtempSync(path.join(os.tmpdir(), "sts2-m2c-unresolved-"));
  try {
    // Stand-in for `dotnet run`: writes a zero-new-gap report with one unresolved reference.
    const fake = path.join(directory, "fake-dotnet.mjs");
    writeFileSync(fake, `#!/usr/bin/env node
import { writeFileSync } from "node:fs";
const out = process.argv[process.argv.indexOf("--output") + 1];
writeFileSync(out, JSON.stringify({ totals: { gaps: 0, new_gaps: 0, reference_unresolved_types: 0,
  reference_unresolved_members: 1 }, stale_accepted: [], inputs: { stub: { sha256: "0" } } }));
`, { mode: 0o755 });
    await assert.rejects(checkGodotStubCoverage({ root: ROOT, candidateDirectory: directory, manifest,
      referenceAssembly: path.join(directory, "GodotSharp.dll"), outputFile: path.join(directory, "out.json"),
      dotnet: { command: fake } }),
    /every game reference resolves in the shipped GodotSharp/u);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("exact native Godot stub bindings match shipped Godot and the gate names a new gap", async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for exact proprietary native component fixtures"); return; }
  const gameDirectory = discoverGameDirectory();
  assert.ok(gameDirectory, "exact game installation is required");
  const disk = readDiskIdentity(resolveInstallation(gameDirectory));
  const { manifest } = loadManagedCandidateManifest(ROOT);
  assert.equal(disk.godotsharp_assembly.sha256, manifest.exact_game.godotsharp_dll_sha256);
  const dotnet = await resolveDotnet();
  const artifacts = ["Sts2Headless.dll", "GodotSharp.dll"].map(name =>
    path.join(candidate, "src/Sts2Headless/bin/Release/net9.0", name));
  const hashes = () => artifacts.map(file => createHash("sha256").update(readFileSync(file)).digest("hex"));
  const before = hashes();
  const { stdout } = await promisify(execFile)(dotnet.command,
    ["run", "--project", path.join(candidate, "m2c-godot-stub-tests/M2cGodotStub.csproj"), "-c", "Release",
      "-p:Deterministic=true", `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`,
      "--", "--reference", disk.godotsharp_assembly.path],
    { cwd: candidate, timeout: 300000, maxBuffer: 16 * 1024 * 1024 });
  assert.deepEqual(hashes(), before, "native regressions retain the frozen candidate bytes");
  assert.equal(before[0], manifest.expected_build.artifact_sha256);
  assert.match(stdout, /^native-m2c-mathf-mismatches=\[\]$/mu);
  assert.match(stdout, /^native-m2c-color-mismatches=\[\]$/mu);
  assert.match(stdout, /^native-m2c-jit-logic-failures=\d+ unexplained=0$/mu);
  assert.match(stdout, /\d+ exact native M2c checks passed/u);

  const directory = mkdtempSync(path.join(os.tmpdir(), "sts2-m2c-coverage-"));
  try {
    const passed = await checkGodotStubCoverage({ root: ROOT, candidateDirectory: candidate, manifest,
      referenceAssembly: disk.godotsharp_assembly.path, outputFile: path.join(directory, "coverage.json"), dotnet });
    assert.equal(passed.new_gaps, 0);
    assert.deepEqual(passed.stale_accepted, []);
    assert.equal(passed.gaps, reviewedBaseline(manifest).accepted_gaps.length);
    // Drop one reviewed key: the same build must now fail and name that member.
    const baseline = reviewedBaseline(manifest);
    const removed = baseline.accepted_gaps.find(gap => gap.key.startsWith("member Godot.FileAccess::"));
    baseline.accepted_gaps = baseline.accepted_gaps.filter(gap => gap !== removed);
    const shadowRoot = path.join(directory, "root");
    for (const relative of ["tools/dotnet/GodotStubCoverage/GodotStubCoverage.csproj", "tools/dotnet/GodotStubCoverage/Program.cs"]) {
      const target = path.join(shadowRoot, relative);
      mkdirSync(path.dirname(target), { recursive: true });
      copyFileSync(path.join(ROOT, relative), target);
    }
    mkdirSync(path.join(shadowRoot, "experiments", "managed-exact"), { recursive: true });
    writeFileSync(path.join(shadowRoot, "experiments", "managed-exact", godotStubCoverageContract(manifest).baseline), JSON.stringify(baseline));
    await assert.rejects(checkGodotStubCoverage({ root: shadowRoot, candidateDirectory: candidate, manifest,
      referenceAssembly: disk.godotsharp_assembly.path, outputFile: path.join(directory, "drift.json"), dotnet }),
    error => error.message.includes(removed.key.slice("member ".length)));
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
