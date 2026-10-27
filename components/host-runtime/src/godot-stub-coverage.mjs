import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { stageDotnetToolProject } from "./assembly-fingerprint-project.mjs";

const execFileAsync = promisify(execFile);
const NEW_GAPS_EXIT = 4;

/** The reviewed coverage gate settings of the admitted candidate manifest. */
export function godotStubCoverageContract(manifest) {
  const contract = manifest.godot_stub_coverage;
  if (contract == null || typeof contract.baseline !== "string" || typeof contract.stub_artifact !== "string"
      || !Array.isArray(contract.scanned_game_files) || contract.scanned_game_files.length === 0) {
    throw new Error("Managed candidate manifest has no Godot stub coverage contract.");
  }
  return contract;
}

/**
 * Compare the exact game's Godot API references with the built headless stub
 * (M2c). Local-only: it reads the proprietary game assemblies, which hosted CI
 * never has. Any gap outside the reviewed baseline fails the build and names
 * the member; the full report is written to `outputFile`.
 */
export async function checkGodotStubCoverage({
  root,
  candidateDirectory,
  manifest,
  referenceAssembly,
  outputFile,
  dotnet
}) {
  const contract = godotStubCoverageContract(manifest);
  const baseline = path.resolve(root, "experiments", "managed-exact", contract.baseline);
  const reviewed = JSON.parse(readFileSync(baseline, "utf8")).exact_game ?? {};
  if (reviewed.sts2_dll_sha256 !== manifest.exact_game?.sts2_dll_sha256
      || reviewed.godotsharp_dll_sha256 !== manifest.exact_game?.godotsharp_dll_sha256) {
    throw new Error("Godot stub coverage baseline was reviewed for a different exact game.");
  }
  const args = ["run", "--project", null, "-c", "Release", "--"];
  // The tool runs from its staged directory, so every path it receives is absolute.
  for (const name of contract.scanned_game_files) args.push("--game", path.resolve(candidateDirectory, "lib", name));
  args.push("--stub", path.resolve(candidateDirectory, contract.stub_artifact),
    "--reference", path.resolve(referenceAssembly), "--baseline", baseline, "--output", path.resolve(outputFile));
  const staged = stageDotnetToolProject(root, "GodotStubCoverage");
  try {
    args[2] = staged.project;
    try {
      await execFileAsync(dotnet.command, args, {
        cwd: staged.directory, encoding: "utf8", maxBuffer: 32 * 1024 * 1024, timeout: 300_000
      });
    } catch (error) {
      if (error?.code === NEW_GAPS_EXIT) {
        throw new Error(`Godot stub coverage found gaps outside the reviewed baseline:\n${String(error.stderr).trim()}`);
      }
      throw error;
    }
  } finally {
    staged.cleanup();
  }
  const report = JSON.parse(readFileSync(outputFile, "utf8"));
  if (report.totals?.new_gaps !== 0) throw new Error("Godot stub coverage report did not confirm zero new gaps.");
  return {
    report_file: outputFile,
    baseline: contract.baseline,
    gaps: report.totals.gaps,
    new_gaps: report.totals.new_gaps,
    stale_accepted: report.stale_accepted,
    stub_sha256: report.inputs.stub.sha256
  };
}
