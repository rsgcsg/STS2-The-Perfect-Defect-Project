import { copyFileSync, mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

/** Stage the shipped, reviewed fingerprint source outside an installed package. */
export function stageAssemblyFingerprintProject(root) {
  const source = path.join(root, "tools", "dotnet", "AssemblyFingerprint");
  const directory = mkdtempSync(path.join(os.tmpdir(), "sts2-host-assembly-fingerprint-"));
  try {
    for (const name of ["AssemblyFingerprint.csproj", "Program.cs"]) {
      copyFileSync(path.join(source, name), path.join(directory, name));
    }
    return {
      directory,
      project: path.join(directory, "AssemblyFingerprint.csproj"),
      cleanup() { rmSync(directory, { recursive: true, force: true }); }
    };
  } catch (error) {
    rmSync(directory, { recursive: true, force: true });
    throw error;
  }
}
